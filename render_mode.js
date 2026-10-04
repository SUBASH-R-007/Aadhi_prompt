/*
 * Aadhi render mode (Phase 22): the live stage played frame by frame for the server's rendered export.
 *
 * The render worker (render_worker.mjs) opens the lesson page with window.__AADHI_RENDER__ set before any page script,
 * installs Playwright's page.clock (Date, performance.now, timers and requestAnimationFrame become virtual) and steps
 * it one frame at a time. What that clock does not reach is done here:
 *   - media: every <video>/<audio> is muted for real and never plays; its currentTime, paused, ended, play()/pause()
 *     and its play / playing / timeupdate / pause / ended events follow the virtual clock (timeupdate on the frame
 *     grid, ended at the exact virtual end). What would have been heard is logged for the server's audio mix.
 *   - animations: CSS animations and transitions, Web Animations and View Transitions are held and set to their time
 *     on the virtual clock at every frame (a page's own pause() / play() / playState keep working).
 *   - frames: a clip with a frame set (manifest.json + numbered images) shows the image for its virtual time in its
 *     own box (same size, fit, clip-path, opacity); others are seeked to the frame.
 *   - the readiness gate: a frame waits for pending fetches, images, fonts, MathJax, view transitions and media loads.
 *   - determinism: a seeded Math.random, the GIF search pinned, the quiz sounds recorded (never played).
 * The page's facade (window.renderMode in index.html) drives it. Without window.__AADHI_RENDER__ nothing is
 * installed: the page plays exactly as before.
 *
 * Loaded as a classic <script> (window.AadhiRenderMode) and as a CommonJS module by the Node unit tests in
 * tests/render_mode.test.js (the pure helpers only).
 */
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    } else {
        root.AadhiRenderMode = api;
        api.activate(root);
    }
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    const DEFAULT_FPS = 30;
    const FRAME_BASE = '/__render_frames/';
    // Native media events the page must not see in render mode (the element never really plays): their virtual
    // counterparts are dispatched instead
    const SUPPRESSED = ['play', 'playing', 'pause', 'timeupdate', 'ended', 'seeking', 'seeked', 'ratechange', 'waiting', 'volumechange'];
    // Native loading events: before the render starts the page hears them as they come; from start() on, a clip's loading is
    // told to the page at frame boundaries (see "Deliveries"), so these are held back
    const LOADING = ['loadstart', 'durationchange', 'loadedmetadata', 'loadeddata', 'canplay', 'canplaythrough', 'progress', 'suspend',
        'stalled', 'abort', 'emptied', 'error'];
    const MEDIA_EVENTS = SUPPRESSED.concat(LOADING);
    // The virtual clock never moves while the page waits on real work. A piece of work still unfinished after this long (real
    // ms) is a stall: the render stops with a plain message instead of going on differently from run to run.
    const STALL_MS = { network: 180000, images: 120000, decode: 30000, media: 120000, fonts: 60000, mathjax: 60000, transition: 30000 };

    // ---- Configuration ----------------------------------------------------------------------------------------------

    const SAFE_SETTING = /^[\w .:@-]{1,80}$/;

    // window.__AADHI_RENDER__ as the worker set it, checked; null when render mode is off (not an object)
    function readConfig(raw) {
        if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
        const whole = (v, min) => (Number.isInteger(v) && v >= min ? v : null);
        const fromScene = whole(raw.fromScene, 0) || 0;
        // Whole-lesson pre-roll: the page plays from fromScene (0: the intro too) exactly as the page before it did, and
        // captures from the first frame at or after scene captureFromScene begins (0 with fromScene 0: from frame 0). Without
        // it, a later range pre-rolls only the scene before fromScene (preroll), or captures from frame 0 (preroll false).
        let captureFromScene = whole(raw.captureFromScene, 0);
        let playFrom = fromScene;
        // (the same, as the worker may also say it: { fromScene: k, preroll: 'lesson', prerollFrom: 0 })
        if (captureFromScene === null && raw.preroll === 'lesson') {
            captureFromScene = fromScene;
            playFrom = whole(raw.prerollFrom, 0) || 0;
        }
        const preroll = fromScene > 0 && (raw.preroll === undefined ? true : !!raw.preroll);
        let projectId = whole(raw.projectId, 1);
        if (projectId === null && typeof raw.projectId === 'string' && /^\d{1,12}$/.test(raw.projectId) && Number(raw.projectId) > 0) projectId = Number(raw.projectId);
        let tts = null;
        if (raw.tts && typeof raw.tts === 'object') {
            const text = v => (typeof v === 'string' && SAFE_SETTING.test(v) ? v : null);
            const rate = Number(raw.tts.rate);
            tts = { engine: text(raw.tts.engine), voice: text(raw.tts.voice), geminiVoice: text(raw.tts.geminiVoice),
                rate: Number.isFinite(rate) && rate >= 0.5 && rate <= 1.5 ? Math.round(rate * 100) / 100 : null };
        }
        return {
            fps: Number.isFinite(raw.fps) && raw.fps >= 1 && raw.fps <= 120 ? raw.fps : DEFAULT_FPS,
            seed: Number.isInteger(raw.seed) ? raw.seed >>> 0 : 1,
            fromScene: playFrom,
            captureFromScene,
            captureScene: captureFromScene !== null ? captureFromScene : fromScene,
            captureAtStart: captureFromScene !== null ? captureFromScene === 0 && playFrom === 0 : !preroll,
            toScene: whole(raw.toScene, 0),
            preroll: captureFromScene === null && preroll,
            missingVisuals: raw.missingVisuals === 'omit' ? 'omit' : 'refuse',
            frameBase: typeof raw.frameBase === 'string' && /^\/[\w\-./]*\/$/.test(raw.frameBase) && !raw.frameBase.includes('..') ? raw.frameBase : FRAME_BASE,
            projectId,
            tts
        };
    }

    // ---- Frame grid and frame sets ----------------------------------------------------------------------------------

    // Frame n of the render is at round(n · 1000 / fps) virtual ms from the start (the worker steps the same grid)
    function frameTimeMs(n, fps) {
        return Math.round(n * 1000 / (fps || DEFAULT_FPS));
    }

    // The first frame index whose time is at or after `ms`
    function frameAtOrAfter(ms, fps) {
        const f = fps || DEFAULT_FPS;
        let n = Math.max(0, Math.floor(ms * f / 1000) - 1);
        while (frameTimeMs(n, f) < ms) n++;
        return n;
    }

    // The next frame time strictly after `now` (virtual ms), on the grid that starts at `epoch`
    function nextFrameTime(now, epoch, fps) {
        const rel = now - (epoch || 0);
        const f = fps || DEFAULT_FPS;
        let n = Math.max(0, Math.floor(rel * f / 1000));
        while (frameTimeMs(n, f) <= rel) n++;
        return (epoch || 0) + frameTimeMs(n, f);
    }

    const PATTERN = /^([\w-]*)%0([1-9])d\.(jpg|jpeg|png|webp)$/i;

    // A frame set's manifest.json, checked: { fps, frames, pattern, loop, alpha, ... } or null
    function normalizeManifest(m) {
        if (!m || typeof m !== 'object') return null;
        const fps = Number(m.fps);
        const frames = Number(m.frames);
        const pattern = typeof m.pattern === 'string' ? m.pattern : '';
        if (!(fps > 0 && fps <= 240) || !Number.isInteger(frames) || frames < 1 || !PATTERN.test(pattern)) return null;
        return { fps, frames, pattern, loop: m.loop !== false, alpha: !!m.alpha, width: Number(m.width) || null, height: Number(m.height) || null,
            format: typeof m.format === 'string' ? m.format : null };
    }

    // The frame index shown at media time t (seconds): the nearest frame by time, i = round(t × fps); a looping set
    // wraps, any other set stays on its last frame (no interpolation: a 24 fps clip at 30 fps repeats one frame in five)
    function frameIndex(t, manifest) {
        const n = manifest.frames;
        let i = Math.round((Number.isFinite(t) ? t : 0) * manifest.fps);
        if (manifest.loop) return ((i % n) + n) % n;
        return Math.min(n - 1, Math.max(0, i));
    }

    // The file of frame index i (0-based): the pattern's number is 1-based and zero-padded
    function frameFileName(i, manifest) {
        const [, prefix, width, ext] = PATTERN.exec(manifest.pattern);
        return prefix + String(i + 1).padStart(Number(width), '0') + '.' + ext;
    }

    function frameUrl(set, t) {
        return set.base + frameFileName(frameIndex(t, set.manifest), set.manifest);
    }

    // A media address as the frame map keys it: absolute, without query or fragment
    function normalizeSrc(url, base) {
        if (!url || typeof url !== 'string') return '';
        try {
            const u = new URL(url, base || undefined);
            return u.origin + u.pathname;
        } catch (e) {
            return url.split('#')[0].split('?')[0];
        }
    }

    // ---- Determinism ------------------------------------------------------------------------------------------------

    // mulberry32: a small seeded generator for Math.random (particles, side animations: the same every render)
    function seededRandom(seed) {
        let a = (seed >>> 0) || 1;
        return function random() {
            a = (a + 0x6D2B79F5) >>> 0;
            let t = a;
            t = Math.imul(t ^ (t >>> 15), t | 1);
            t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
            return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
        };
    }

    // The GIF search answers its first match instead of a random one, so every render shows the same GIF
    function pinGifRequest(url) {
        return typeof url === 'string' && /\/get-gif\?/.test(url) ? url.replace(/([?&])randomize=true\b/, '$1randomize=false') : url;
    }

    // ---- Virtual media ----------------------------------------------------------------------------------------------

    // A virtual element's media time (seconds) at virtual time `now` (ms): base + elapsed · rate since it was anchored,
    // wrapped when it loops, held at the end otherwise
    function mediaTime(state, now, duration, loop) {
        let t = state.base;
        if (state.playing && state.anchorAt !== null) t += Math.max(0, now - state.anchorAt) / 1000 * state.rate;
        if (duration > 0 && Number.isFinite(duration)) {
            if (loop) t = t >= duration ? t % duration : t;
            else t = Math.min(t, duration);
        }
        return t;
    }

    // When (virtual ms) a playing, non-looping element reaches its end; null when that is unknown
    function mediaEndAt(state, duration) {
        if (!state.playing || state.anchorAt === null || !(duration > 0) || !Number.isFinite(duration) || !(state.rate > 0)) return null;
        return state.anchorAt + Math.max(0, duration - state.base) / state.rate * 1000;
    }

    // What a sound is, for the mix: the intro logo's soundtrack, the background music, an AI clip's own voice, narration
    function soundKind(info) {
        if (info.id === 'intro-logo-video') return 'logo';
        if (/bgm\.mp3(\?|$)/i.test(info.src || '')) return 'music';
        if (info.tag === 'VIDEO') return 'video-voice';
        return 'narration';
    }

    // The sounds heard: one entry per stretch an unmuted element played without a change of position, rate or volume
    class SoundLog {
        constructor() {
            this.entries = [];
        }
        open(info) {
            const entry = { at: info.at, endAt: null, kind: info.kind, src: info.src, offset: info.offset, rate: info.rate,
                volume: info.volume, loop: !!info.loop, clipDuration: info.clipDuration > 0 && Number.isFinite(info.clipDuration) ? info.clipDuration : null };
            this.entries.push(entry);
            return entry;
        }
        close(entry, at) {
            if (entry && entry.endAt === null) entry.endAt = Math.max(entry.at, at);
        }
    }

    // A quiz sound as the server synthesizes it, from the oscillator / gain automation the page scheduled:
    // { type, freq, freqEnd?, sweep?, gain, attack, floor, duration } (seconds; gain linear)
    function synthFromNodes(osc, gains) {
        const start = osc.startAt || 0;
        const duration = Math.max(0.01, (osc.stopAt !== null && osc.stopAt !== undefined ? osc.stopAt : start + 1) - start);
        const round = v => Math.round(v * 10000) / 10000;
        const freqEvents = osc.frequency.events;
        const firstSet = freqEvents.find(e => e[0] === 'set');
        const ramps = freqEvents.filter(e => e[0] === 'exp' || e[0] === 'linear');
        const synth = { type: osc.type || 'sine', freq: round(firstSet ? firstSet[1] : osc.frequency.value), gain: 1, attack: 0, floor: 1, duration: round(duration) };
        if (ramps.length) {
            const last = ramps[ramps.length - 1];
            synth.freqEnd = round(last[1]);
            synth.sweep = round(Math.max(0, last[2] - start));
        }
        // the loudest point of the envelope, how long it took to get there, and where its decay ends (relative)
        let peak = null;
        let peakAt = start;
        let tail = null;
        (gains || []).forEach(g => {
            const events = g.gain.events;
            if (!events.length) {
                if (peak === null || g.gain.value > peak) { peak = g.gain.value; peakAt = start; }
                return;
            }
            events.forEach(e => {
                if (peak === null || e[1] > peak) { peak = e[1]; peakAt = e[0] === 'set' ? start : e[2]; }
            });
            const lastEvent = events[events.length - 1];
            if (lastEvent[0] === 'exp' || lastEvent[0] === 'linear') tail = lastEvent[1];
        });
        if (peak !== null && peak > 0) {
            synth.gain = round(peak);
            synth.attack = round(Math.max(0, Math.min(duration, peakAt - start)));
            synth.floor = round(tail === null ? 1 : Math.max(0, tail) / peak);
        }
        return synth;
    }

    // A stand-in for the page's AudioContext: nothing is played; each oscillator started is reported (onSound) with its
    // synthesis parameters and its virtual start time. `now` is the virtual clock (ms).
    function createFakeAudioContext(now, onSound) {
        class Param {
            constructor(value) { this.value = value; this.defaultValue = value; this.events = []; }
            setValueAtTime(v, t) { this.events.push(['set', v, t]); if (this.events.length === 1) this.value = v; return this; }
            linearRampToValueAtTime(v, t) { this.events.push(['linear', v, t]); return this; }
            exponentialRampToValueAtTime(v, t) { this.events.push(['exp', v, t]); return this; }
            setTargetAtTime(v, t) { this.events.push(['exp', v, t]); return this; }
            setValueCurveAtTime(values, t, d) { if (values && values.length) this.events.push(['linear', values[values.length - 1], t + d]); return this; }
            cancelScheduledValues() { this.events = []; return this; }
            cancelAndHoldAtTime() { return this; }
        }
        class Node {
            constructor(ctx, kind) { this.context = ctx; this.kind = kind; this.outputs = []; this.numberOfInputs = 1; this.numberOfOutputs = 1; }
            connect(node) { this.outputs.push(node); return node; }
            disconnect() { this.outputs = []; }
            addEventListener() {}
            removeEventListener() {}
        }
        class Oscillator extends Node {
            constructor(ctx) {
                super(ctx, 'oscillator');
                this.type = 'sine';
                this.frequency = new Param(440);
                this.detune = new Param(0);
                this.startAt = null;
                this.stopAt = null;
                this.onended = null;
            }
            start(when) {
                if (this.startAt !== null) return;
                this.startAt = Math.max(Number(when) || 0, this.context.currentTime);
                // reported once the calling code has scheduled the stop and the envelope (the same task)
                Promise.resolve().then(() => this.context._report(this));
            }
            stop(when) { this.stopAt = Math.max(Number(when) || 0, this.startAt || 0); }
            setPeriodicWave() {}
        }
        class Gain extends Node {
            constructor(ctx) { super(ctx, 'gain'); this.gain = new Param(1); }
        }
        class Filter extends Node {
            constructor(ctx) { super(ctx, 'filter'); this.type = 'lowpass'; this.frequency = new Param(350); this.Q = new Param(1); this.gain = new Param(0); this.detune = new Param(0); }
        }
        class Source extends Node {
            constructor(ctx) { super(ctx, 'source'); this.buffer = null; this.loop = false; this.playbackRate = new Param(1); this.onended = null; }
            start() {}
            stop() {}
        }
        class FakeAudioContext {
            constructor() {
                this._t0 = now();
                this.state = 'running';
                this.sampleRate = 48000;
                this.baseLatency = 0;
                this.destination = new Node(this, 'destination');
                this.listener = {};
                this.onstatechange = null;
            }
            get currentTime() { return Math.max(0, now() - this._t0) / 1000; }
            resume() { this.state = 'running'; return Promise.resolve(); }
            suspend() { return Promise.resolve(); }
            close() { this.state = 'closed'; return Promise.resolve(); }
            createOscillator() { return new Oscillator(this); }
            createGain() { return new Gain(this); }
            createBiquadFilter() { return new Filter(this); }
            createBufferSource() { return new Source(this); }
            createDynamicsCompressor() { return new Gain(this); }
            createStereoPanner() { const n = new Gain(this); n.pan = new Param(0); return n; }
            createAnalyser() { const n = new Node(this, 'analyser'); n.getByteFrequencyData = () => {}; n.getFloatTimeDomainData = () => {}; return n; }
            createBuffer() { return { getChannelData: () => new Float32Array(0) }; }
            decodeAudioData() { return Promise.resolve(this.createBuffer()); }
            addEventListener() {}
            removeEventListener() {}
            _report(osc) {
                const gains = [];
                const filters = [];
                const seen = new Set();
                const walk = node => {
                    if (!node || seen.has(node)) return;
                    seen.add(node);
                    if (node.kind === 'gain') gains.push(node);
                    if (node.kind === 'filter') filters.push(node);
                    (node.outputs || []).forEach(walk);
                };
                osc.outputs.forEach(walk);
                const synth = synthFromNodes(osc, gains);
                if (filters.length) {
                    const f = filters[0];
                    const fset = f.frequency.events.find(e => e[0] === 'set');
                    const framp = f.frequency.events.filter(e => e[0] !== 'set').pop();
                    synth.filter = { type: f.type, freq: fset ? fset[1] : f.frequency.value };
                    if (framp) synth.filter.freqEnd = framp[1];
                }
                onSound({ at: this._t0 + osc.startAt * 1000, synth });
            }
        }
        return FakeAudioContext;
    }

    // ---- Preparation progress ---------------------------------------------------------------------------------------

    // window.renderMode.progress: plain data the worker reads (page.evaluate) while prepare() runs, so a long lesson's
    // preparation is reported ("Narration 12 of 140") instead of looking stuck. phase: idle → preparing → ready (or failed);
    // done / total: whole numbers (done never above a known total); label: the step, in plain words; changes: counts every
    // update (the worker can tell a slow step from a stuck one).
    const PROGRESS_PHASES = ['idle', 'preparing', 'ready', 'failed'];
    function setProgress(progress, phase, done, total, label) {
        const count = v => (Number.isFinite(v) && v >= 0 ? Math.floor(v) : 0);
        if (PROGRESS_PHASES.includes(phase)) progress.phase = phase;
        progress.total = count(total);
        progress.done = progress.total ? Math.min(count(done), progress.total) : count(done);
        progress.label = typeof label === 'string' ? label.replace(/\s+/g, ' ').trim().slice(0, 160) : '';
        progress.changes = (progress.changes || 0) + 1;
        return progress;
    }
    function createProgress() {
        return setProgress({ phase: 'idle', done: 0, total: 0, label: '', changes: -1 }, 'idle', 0, 0, '');
    }

    // ---- The readiness gate's bookkeeping --------------------------------------------------------------------------

    // Real work a frame waits for: requests (started / ended), and what is seen on the page as it loads (images, clips, fonts:
    // observed as [key, tag, what], a new tag being a new piece of work). The virtual clock stays where it is while any of it
    // is unfinished; a piece older than its kind's limit is a stall (stalled() names it).
    class PendingWork {
        constructor(now, limits) {
            this.now = now;
            this.limits = limits;
            this.items = new Map();
            this.seq = 0;
        }
        start(kind, what) {
            const id = ++this.seq;
            this.items.set(id, { kind, what: what || '', since: this.now(), tag: null });
            return id;
        }
        end(id) {
            this.items.delete(id);
        }
        observe(kind, seen) {
            const keys = new Set();
            seen.forEach(([key, tag, what]) => {
                keys.add(key);
                const item = this.items.get(key);
                if (!item || item.tag !== tag) this.items.set(key, { kind, what: what || '', since: this.now(), tag });
            });
            Array.from(this.items.keys()).forEach(key => {
                const item = this.items.get(key);
                if (item.kind === kind && typeof key !== 'number' && !keys.has(key)) this.items.delete(key);
            });
        }
        // the kinds of work still unfinished
        busy() {
            const kinds = new Set();
            this.items.forEach(item => kinds.add(item.kind));
            return Array.from(kinds);
        }
        // the oldest piece past its kind's limit, or null
        stalled() {
            const t = this.now();
            let worst = null;
            this.items.forEach(item => {
                if (t - item.since > (this.limits[item.kind] || Infinity) && (!worst || item.since < worst.since)) worst = item;
            });
            return worst;
        }
    }

    // What the page hears of finished real work once the render has started (a response, a clip's loading, an image, a view
    // transition's turn to update the page): held until the frame boundary, then told in a fixed order, so that how long the
    // work took on this machine never changes what the video shows. rank orders the kinds; key (a function, read when the
    // queue is drained) orders within a kind.
    class DeliveryQueue {
        constructor() {
            this.items = [];
        }
        add(rank, key, run) {
            this.items.push({ rank, key, run });
        }
        get size() {
            return this.items.length;
        }
        drain() {
            const items = this.items.map(item => ({ rank: item.rank, key: item.key(), run: item.run }));
            this.items = [];
            items.sort((x, y) => x.rank - y.rank || x.key - y.key);
            items.forEach(item => { try { item.run(); } catch (e) { /* one listener never stops the frame */ } });
            return items.length;
        }
    }

    // ---- The range a page renders -----------------------------------------------------------------------------------

    // Which frames of this page belong to its range. Times are virtual ms from start(). Without a pre-roll the capture
    // starts at frame 0; with one, at the first frame at or after the moment scene fromScene began. The range is done at
    // the first frame at or after the scene after toScene began, or after the lesson ended (that frame is the next
    // range's first: not captured; for the last range the worker captures it and adds its tail).
    class RangeTracker {
        constructor(config) {
            this.fromScene = config.captureScene !== undefined ? config.captureScene : (config.fromScene || 0); // the range's first scene
            this.toScene = config.toScene === undefined ? null : config.toScene;
            const atStart = config.captureAtStart !== undefined ? config.captureAtStart : !(config.preroll && this.fromScene > 0);
            this.preroll = !atStart;
            this.rangeStartAt = atStart ? 0 : null;
            this.captureFrom = atStart ? 0 : null;
            this.endAt = null;
            this.endReason = null;
            this.lastScene = null;
            this.scenes = [];
        }
        sceneBegan(index, at, title, type) {
            this.lastScene = index;
            this.scenes.push({ at, index, title: title || `Scene ${index + 1}`, type: type || null });
            if (this.rangeStartAt === null && index >= this.fromScene) this.rangeStartAt = at;
            if (this.toScene !== null && index > this.toScene && this.endAt === null) { this.endAt = at; this.endReason = 'next scene'; }
        }
        lessonEnded(at) {
            if (this.endAt === null) { this.endAt = at; this.endReason = 'lesson ended'; }
        }
        frame(tMs) {
            if (this.captureFrom === null && this.rangeStartAt !== null && tMs >= this.rangeStartAt) this.captureFrom = tMs;
            return { scene: this.lastScene, captureFrom: this.captureFrom, done: this.endAt !== null && tMs >= this.endAt };
        }
    }

    // The range's timeline for the server: subtitles, chapters, the audio mix and what was left out, in seconds from the
    // range's first captured frame. Inputs are virtual ms from start() (cues: seconds from start()).
    function buildTimeline(input) {
        const fps = input.fps || DEFAULT_FPS;
        const from = input.captureFrom === null || input.captureFrom === undefined ? 0 : input.captureFrom;
        const rangeStart = input.rangeStartAt === null || input.rangeStartAt === undefined ? from : Math.min(input.rangeStartAt, from);
        const sec = ms => Math.round(ms) / 1000;
        const rel = at => sec(Math.max(0, at - from));
        const firstFrame = frameAtOrAfter(from, fps);
        const out = { fps, scenes: [], cues: [], audio: [], sync: [], notes: [] };
        (input.scenes || []).forEach(s => {
            if (s.at >= rangeStart) out.scenes.push({ t: rel(s.at), title: s.title, type: s.type, index: s.index });
        });
        const fromSec = from / 1000;
        (input.cues || []).forEach(c => {
            const end = c.end === null || c.end === undefined ? null : c.end - fromSec;
            if (end !== null && end <= 0) return;
            out.cues.push({ start: Math.round(Math.max(0, c.start - fromSec) * 1000) / 1000, end: end === null ? null : Math.round(end * 1000) / 1000, text: c.text });
        });
        (input.sounds || []).forEach(s => {
            if (s.endAt !== null && s.endAt !== undefined && s.endAt <= from) return;
            let offset = s.offset;
            if (s.at < from) offset += (from - s.at) / 1000 * s.rate;
            if (s.loop && s.clipDuration) offset %= s.clipDuration;
            const t = rel(s.at);
            const end = s.endAt === null || s.endAt === undefined ? null : rel(s.endAt);
            const entry = { t, kind: s.kind, src: s.src, offset: Math.round(offset * 1000) / 1000, rate: s.rate, volume: s.volume, loop: s.loop,
                end, duration: end === null ? null : Math.round((end - t) * 1000) / 1000 };
            if (s.clipDuration) entry.clipDuration = s.clipDuration;
            out.audio.push(entry);
        });
        (input.sfx || []).forEach(s => {
            if (s.at < from) return;
            out.audio.push({ t: rel(s.at), kind: 'sfx', offset: 0, rate: 1, volume: 1, loop: false, duration: s.synth.duration,
                end: Math.round((rel(s.at) + s.synth.duration) * 1000) / 1000, synth: s.synth });
        });
        out.audio.sort((a, b) => a.t - b.t);
        (input.sync || []).forEach(s => {
            if (s.at < rangeStart) return;
            out.sync.push(Object.assign({ t: rel(s.at), frame: Math.max(0, frameAtOrAfter(s.at, fps) - firstFrame), scene: s.scene, kind: s.kind,
                target: s.target === undefined ? null : s.target }, s.extra || {}));
        });
        (input.notes || []).forEach(n => {
            if (n.at >= rangeStart) out.notes.push({ t: rel(n.at), scene: n.scene, text: n.text });
        });
        return out;
    }

    // ---- The browser runtime ----------------------------------------------------------------------------------------

    function activate(win) {
        if (!win || !win.document || api.runtime) return api.runtime || null;
        const config = readConfig(win.__AADHI_RENDER__);
        if (!config) return null;
        api.runtime = install(win, config);
        return api.runtime;
    }

    function install(win, config) {
        const doc = win.document;
        const builtins = (win.__pwClock && win.__pwClock.builtins) || {};
        const realPerformance = builtins.performance || win.performance;
        const realSetTimeout = builtins.setTimeout || win.setTimeout.bind(win);
        const now = () => win.performance.now(); // the virtual clock (page.clock) — looked up at each call
        const realNow = () => realPerformance.now();
        const channel = new win.MessageChannel();
        const yields = [];
        channel.port1.onmessage = () => { const r = yields.shift(); if (r) r(); };
        const realYield = () => new Promise(resolve => { yields.push(resolve); channel.port2.postMessage(0); });
        const realDelay = ms => (ms > 0 ? new Promise(resolve => realSetTimeout(resolve, ms)) : realYield());

        const state = {
            epoch: 0, started: false, fps: config.fps,
            sounds: new SoundLog(), sfx: [], notes: [], sync: [],
            frameSets: new Map(), range: new RangeTracker(config),
            gateNotes: new Set(),
            presentFromScene: null, // pre-roll frames draw the clips' images once this scene has begun (null: always)
            lastFrame: null
        };
        const rel = at => at - state.epoch;
        const work = new PendingWork(realNow, STALL_MS);
        const deliveries = new DeliveryQueue();
        // finished real work: told to the page at once before start(), at the next frame boundary after it
        const later = (rank, key, run) => (state.started ? deliveries.add(rank, typeof key === 'function' ? key : () => key, run) : run());

        // Determinism: the same particles and side animations every render; no voice from the operating system. The generator
        // is seeded again when the page's first listeners run (the particles are made then: an async script loading before or
        // after them must not shift their numbers) and again at start() (everything the render plays).
        Math.random = seededRandom(config.seed);
        doc.addEventListener('DOMContentLoaded', () => { Math.random = seededRandom(config.seed); }, { once: true, capture: true });
        if (win.speechSynthesis) {
            try { win.speechSynthesis.speak = () => {}; win.speechSynthesis.cancel = () => {}; } catch (e) { /* read-only: the worker stubs it */ }
        }

        // The look of a recording (every data-recording rule applies, except the glass blur: render keeps it)
        const markBody = () => {
            if (!doc.body) return false;
            doc.body.setAttribute('data-render', '');
            doc.body.setAttribute('data-recording', '');
            return true;
        };
        if (!markBody()) doc.addEventListener('DOMContentLoaded', markBody, { once: true });

        // ---- Network, MathJax and view transitions: work a frame waits for, told to the page at the frame boundary ----
        const requestName = resource => String((resource && resource.url) || resource || '').split('?')[0].replace(/^https?:\/\/[^/]+/, '');
        const deferred = (kind, what, make) => {
            const id = work.start(kind, what);
            let p;
            try {
                p = make();
            } catch (e) {
                work.end(id);
                throw e;
            }
            return new Promise((resolve, reject) => {
                Promise.resolve(p).then(v => { work.end(id); later(1, id, () => resolve(v)); },
                    e => { work.end(id); later(1, id, () => reject(e)); });
            });
        };
        if (typeof win.fetch === 'function') {
            const realFetch = win.fetch;
            win.fetch = function (resource, init) {
                return deferred('network', requestName(resource), () => realFetch.call(win, pinGifRequest(resource), init));
            };
        }
        if (win.Response && win.Response.prototype) {
            ['json', 'text', 'blob', 'arrayBuffer', 'formData'].forEach(name => {
                const real = win.Response.prototype[name];
                if (typeof real !== 'function') return;
                win.Response.prototype[name] = function () {
                    const response = this;
                    const args = arguments;
                    return deferred('network', requestName(response.url), () => real.apply(response, args));
                };
            });
        }
        if (win.XMLHttpRequest && win.XMLHttpRequest.prototype) {
            // (the stage never uses XMLHttpRequest while it plays: only the tab capture's upload does; it is waited for)
            const realSend = win.XMLHttpRequest.prototype.send;
            win.XMLHttpRequest.prototype.send = function () {
                const id = work.start('network', 'an upload');
                this.addEventListener('loadend', () => work.end(id));
                try {
                    return realSend.apply(this, arguments);
                } catch (e) {
                    work.end(id);
                    throw e;
                }
            };
        }
        const wrapMathJax = () => {
            const MJ = win.MathJax;
            if (!MJ || typeof MJ.typesetPromise !== 'function' || MJ.typesetPromise.aadhiRender) return;
            const real = MJ.typesetPromise;
            const wrapped = function () {
                const self = this;
                const args = arguments;
                return deferred('mathjax', 'a formula', () => real.apply(self, args));
            };
            wrapped.aadhiRender = true;
            MJ.typesetPromise = wrapped;
        };
        // A view transition first captures the page as it is (real work: waited for), then updates it: the update runs at the
        // frame boundary, and the transition's start (its animations) is waited for too
        if (typeof doc.startViewTransition === 'function') {
            const realStart = doc.startViewTransition;
            doc.startViewTransition = function (arg) {
                if (!state.started) {
                    const transition = realStart.apply(doc, arguments);
                    const id = work.start('transition', 'a scene transition');
                    const end = () => work.end(id);
                    Promise.resolve(transition && transition.ready).then(end, end);
                    return transition;
                }
                const update = typeof arg === 'function' ? arg : (arg && typeof arg.update === 'function' ? arg.update : null);
                const capture = work.start('transition', 'a scene transition');
                let transition = null;
                const wrapped = () => new Promise((resolve, reject) => {
                    work.end(capture);
                    later(1, capture, () => {
                        const starting = work.start('transition', 'a scene transition');
                        const end = () => work.end(starting);
                        Promise.resolve(transition && transition.ready).then(end, end);
                        let result;
                        try {
                            result = update ? update() : undefined;
                        } catch (e) {
                            reject(e);
                            return;
                        }
                        Promise.resolve(result).then(resolve, reject);
                    });
                });
                try {
                    transition = realStart.call(doc, typeof arg === 'function' || !arg ? wrapped : Object.assign({}, arg, { update: wrapped }));
                } catch (e) {
                    work.end(capture);
                    throw e;
                }
                return transition;
            };
        }

        // ---- Quiz sounds: recorded with their synthesis, never played ----
        const FakeAudioContext = createFakeAudioContext(now, sound => state.sfx.push({ at: rel(sound.at), synth: sound.synth }));
        win.AudioContext = FakeAudioContext;
        win.webkitAudioContext = FakeAudioContext;

        // ---- Virtual media ----
        const MP = win.HTMLMediaElement.prototype;
        const desc = name => Object.getOwnPropertyDescriptor(MP, name);
        const O = { currentTime: desc('currentTime'), playbackRate: desc('playbackRate'), muted: desc('muted'), volume: desc('volume'),
            paused: desc('paused'), ended: desc('ended'), readyState: desc('readyState'), duration: desc('duration'), src: desc('src'),
            play: MP.play, pause: MP.pause, load: MP.load };
        const realReady = el => O.readyState.get.call(el);
        const media = new WeakMap();
        const known = new Set(); // every element with a virtual state (pruned when it is gone and settled)
        let mediaSeq = 0;
        const playing = new Set();
        const seekWaiters = new WeakMap();
        const handled = new WeakSet();
        let tickTimer = null;

        const hasSource = el => !!(el.getAttribute('src') || el.currentSrc || el.querySelector('source[src]'));
        const fileOf = el => String(el.currentSrc || el.getAttribute('src') || '').split('?')[0].split('/').pop();
        // A clip whose own duration is unknown (e.g. a WebM recorded in a browser, without a duration in its header) lasts as
        // long as its frame set (frames / fps); otherwise a scene waiting for its end would never move on (review L2)
        const durationOf = el => {
            const d = el.duration;
            if (d > 0 && Number.isFinite(d)) return d;
            const set = state.frameSets.size ? state.frameSets.get(normalizeSrc(el.currentSrc || el.getAttribute('src') || '', doc.baseURI)) : null;
            return set && set.manifest.frames > 0 && set.manifest.fps > 0 ? set.manifest.frames / set.manifest.fps : NaN;
        };
        const absoluteSrc = el => { try { return new URL(el.currentSrc || el.getAttribute('src') || '', doc.baseURI).href; } catch (e) { return el.currentSrc || ''; } };
        const fire = (el, type, sync) => {
            if (sync) el.dispatchEvent(new win.Event(type));
            else Promise.resolve().then(() => el.dispatchEvent(new win.Event(type)));
        };

        function mstate(el) {
            let s = media.get(el);
            if (s) return s;
            // vReady: the readiness the page sees (null before the render starts: the browser's own); it only rises, at frame
            // boundaries, until a new source
            s = { playing: false, anchorAt: null, base: 0, rate: 1, ended: false, muted: false, volume: 1, autoplay: false,
                waiting: false, endTimer: null, sound: null, seq: ++mediaSeq, vReady: state.started ? 0 : null, vErr: false };
            try {
                s.base = O.currentTime.get.call(el) || 0;
                s.rate = O.playbackRate.get.call(el) || 1;
                s.muted = !!O.muted.get.call(el);
                s.volume = O.volume.get.call(el);
            } catch (e) { /* defaults */ }
            media.set(el, s);
            known.add(el);
            try { O.muted.set.call(el, true); } catch (e) { /* the browser is muted anyway */ }
            MEDIA_EVENTS.forEach(type => el.addEventListener(type, onElementEvent, true));
            return s;
        }
        const timeOf = el => mediaTime(mstate(el), now(), durationOf(el), el.loop);

        function updateSound(el, restart) {
            const s = mstate(el);
            const audible = s.playing && s.anchorAt !== null && !s.muted && s.volume > 0 && hasSource(el);
            if (s.sound && (!audible || restart)) { state.sounds.close(s.sound, rel(now())); s.sound = null; }
            if (audible && !s.sound) {
                const src = absoluteSrc(el);
                s.sound = state.sounds.open({ at: rel(now()), kind: soundKind({ id: el.id, src, tag: el.tagName }), src, offset: timeOf(el),
                    rate: s.rate, volume: s.volume, loop: el.loop, clipDuration: durationOf(el) });
            }
        }
        function scheduleEnd(el) {
            const s = mstate(el);
            if (s.endTimer !== null) { win.clearTimeout(s.endTimer); s.endTimer = null; }
            if (el.loop) return;
            const at = mediaEndAt(s, durationOf(el));
            if (at === null) return;
            s.endTimer = win.setTimeout(() => { s.endTimer = null; reachEnd(el); }, Math.max(0, at - now()));
        }
        function reachEnd(el) {
            const s = mstate(el);
            if (!s.playing || el.loop) return;
            const d = durationOf(el);
            if (Number.isFinite(d) && timeOf(el) < d - 0.0005) { scheduleEnd(el); return; }
            s.base = Number.isFinite(d) ? d : timeOf(el);
            s.playing = false;
            s.anchorAt = null;
            s.ended = true;
            playing.delete(el);
            updateSound(el);
            fire(el, 'timeupdate', true);
            fire(el, 'pause', true);
            fire(el, 'ended', true);
        }
        // timeupdate on the frame grid while anything plays (the page's [SYNC] reveals and captions land on their frame)
        function ensureTicker() {
            if (tickTimer !== null || !playing.size) return;
            const t = now();
            tickTimer = win.setTimeout(tick, Math.max(0, nextFrameTime(t, state.epoch, state.fps) - t));
        }
        function tick() {
            tickTimer = null;
            Array.from(playing).forEach(el => {
                const s = media.get(el);
                if (s && s.playing && s.anchorAt !== null) el.dispatchEvent(new win.Event('timeupdate'));
            });
            ensureTicker();
        }
        function anchor(el) {
            const s = mstate(el);
            s.waiting = false;
            s.anchorAt = now();
            scheduleEnd(el);
            updateSound(el);
            fire(el, 'playing');
            ensureTicker();
        }
        function play(el) {
            const s = mstate(el);
            if (!hasSource(el)) return Promise.reject(new win.DOMException('The element has no supported sources.', 'NotSupportedError'));
            if (s.playing) return Promise.resolve();
            const d = durationOf(el);
            if (s.ended || (!el.loop && Number.isFinite(d) && s.base >= d)) s.base = 0;
            s.ended = false;
            s.playing = true;
            s.anchorAt = null;
            playing.add(el);
            fire(el, 'play');
            if (el.readyState >= 1) anchor(el);
            else s.waiting = true; // anchored when its metadata arrives (the frame gate waits for it)
            return Promise.resolve();
        }
        function pause(el, internal) {
            const s = mstate(el);
            if (!s.playing) return;
            s.base = timeOf(el);
            s.playing = false;
            s.anchorAt = null;
            s.waiting = false;
            scheduleEnd(el);
            playing.delete(el);
            updateSound(el);
            fire(el, 'timeupdate', internal);
            fire(el, 'pause', internal);
        }
        function seek(el, value) {
            const s = mstate(el);
            const d = durationOf(el);
            let t = Math.max(0, Number(value) || 0);
            if (Number.isFinite(d)) t = Math.min(t, d);
            s.base = t;
            s.ended = false;
            if (s.anchorAt !== null) s.anchorAt = now();
            scheduleEnd(el);
            updateSound(el, true);
            fire(el, 'seeking');
            fire(el, 'timeupdate');
            fire(el, 'seeked');
        }
        function resetLogical(el) {
            const s = mstate(el);
            const was = s.playing;
            s.playing = false; s.anchorAt = null; s.base = 0; s.ended = false; s.waiting = false;
            scheduleEnd(el);
            playing.delete(el);
            updateSound(el);
            return was;
        }
        // A new source (src set, or load()): paused at 0 with nothing loaded, at once, as the browser's load algorithm does
        function newSource(el) {
            const s = mstate(el);
            known.add(el);
            resetLogical(el);
            if (s.vReady !== null) { s.vReady = 0; s.vErr = false; }
        }
        // The clip's loading as the page is told it at a frame boundary: its readiness rises to the browser's, with the events the
        // browser would have sent on the way, in order; the virtual playback starts once the metadata is in
        const READY_STEPS = [[1, ['durationchange', 'loadedmetadata']], [2, ['loadeddata']], [3, ['canplay']], [4, ['canplaythrough']]];
        function deliverLoading(el) {
            const s = mstate(el);
            if (el.error && !s.vErr) {
                s.vErr = true;
                if (s.sound) { state.sounds.close(s.sound, rel(now())); s.sound = null; }
                el.dispatchEvent(new win.Event('error'));
            }
            const real = realReady(el);
            READY_STEPS.forEach(([level, events]) => {
                if (s.vReady >= level || real < level) return;
                s.vReady = level;
                if (level === 1) {
                    if (s.playing && s.anchorAt === null) anchor(el);
                    else scheduleEnd(el);
                    if (s.autoplay && !s.playing && el.isConnected) play(el);
                }
                events.forEach(type => el.dispatchEvent(new win.Event(type)));
            });
        }
        // Clips whose loading has news for the page, queued for the frame boundary (by the order they were made)
        function queueLoading() {
            known.forEach(el => {
                const s = media.get(el);
                if (!s || s.vReady === null) return;
                if ((el.error && !s.vErr) || (realReady(el) > s.vReady && s.vReady < 4)) deliveries.add(0, () => s.seq, () => deliverLoading(el));
                else if (!el.isConnected && !s.playing && (realReady(el) <= s.vReady || el.error)) known.delete(el); // gone and settled
            });
        }

        Object.defineProperty(MP, 'currentTime', { configurable: true, enumerable: true,
            get() { return timeOf(this); }, set(v) { seek(this, v); } });
        Object.defineProperty(MP, 'readyState', { configurable: true, enumerable: true,
            get() { const s = mstate(this); return s.vReady === null ? realReady(this) : s.vReady; } });
        Object.defineProperty(MP, 'duration', { configurable: true, enumerable: true,
            get() { const s = mstate(this); return s.vReady !== null && s.vReady < 1 ? NaN : O.duration.get.call(this); } });
        if (O.src && O.src.set) {
            Object.defineProperty(MP, 'src', { configurable: true, enumerable: true,
                get() { return O.src.get.call(this); }, set(v) { O.src.set.call(this, v); newSource(this); } });
        }
        if (win.Element && win.Element.prototype.setAttribute) {
            const EP = win.Element.prototype;
            const realSet = EP.setAttribute;
            const realRemove = EP.removeAttribute;
            const isSrc = (el, name) => el instanceof win.HTMLMediaElement && String(name).toLowerCase() === 'src';
            EP.setAttribute = function (name, value) {
                const r = realSet.call(this, name, value);
                if (isSrc(this, name)) newSource(this);
                return r;
            };
            EP.removeAttribute = function (name) {
                const r = realRemove.call(this, name);
                if (isSrc(this, name)) newSource(this);
                return r;
            };
        }
        Object.defineProperty(MP, 'paused', { configurable: true, enumerable: true, get() { return !mstate(this).playing; } });
        Object.defineProperty(MP, 'ended', { configurable: true, enumerable: true, get() { return mstate(this).ended; } });
        Object.defineProperty(MP, 'playbackRate', { configurable: true, enumerable: true,
            get() { return mstate(this).rate; },
            set(v) {
                const s = mstate(this);
                const r = Number(v);
                if (!(r > 0) || r === s.rate) return;
                s.base = timeOf(this);
                if (s.anchorAt !== null) s.anchorAt = now();
                s.rate = r;
                try { O.playbackRate.set.call(this, r); } catch (e) { /* out of the browser's range: virtual only */ }
                scheduleEnd(this);
                updateSound(this, true);
                fire(this, 'ratechange');
            } });
        Object.defineProperty(MP, 'muted', { configurable: true, enumerable: true,
            get() { return mstate(this).muted; },
            set(v) { const s = mstate(this); if (s.muted === !!v) return; s.muted = !!v; updateSound(this); fire(this, 'volumechange'); } });
        Object.defineProperty(MP, 'volume', { configurable: true, enumerable: true,
            get() { return mstate(this).volume; },
            set(v) {
                const s = mstate(this);
                const n = Math.min(1, Math.max(0, Number(v)));
                if (!Number.isFinite(n) || n === s.volume) return;
                s.volume = n;
                updateSound(this, true);
                fire(this, 'volumechange');
            } });
        MP.play = function () { return play(this); };
        MP.pause = function () { pause(this, false); };
        MP.load = function () {
            const r = O.load.call(this);
            newSource(this);
            return r;
        };

        // Native events: the ones the virtual element replaces are stopped before any page listener. Loading events: as they come
        // before the render starts; held for the frame boundary after it (deliverLoading tells the page)
        function onMediaEvent(el, e) {
            if (handled.has(e)) return;
            handled.add(e);
            if (!e.isTrusted) return; // the virtual element's own events
            const s = mstate(el);
            if (SUPPRESSED.includes(e.type)) {
                e.stopImmediatePropagation();
                if (e.type === 'play' || e.type === 'playing') {
                    try { O.pause.call(el); } catch (err) { /* already paused */ }
                    if (!state.started && s.autoplay && !s.playing && el.isConnected) play(el);
                } else if (e.type === 'seeked') {
                    const done = seekWaiters.get(el);
                    if (done) { seekWaiters.delete(el); done(); }
                }
                return;
            }
            if (state.started) {
                e.stopImmediatePropagation();
                return;
            }
            if (e.type === 'loadedmetadata' || e.type === 'durationchange' || e.type === 'loadeddata' || e.type === 'canplay') {
                if (s.playing && s.anchorAt === null && el.readyState >= 1) anchor(el);
                else scheduleEnd(el);
                if (s.autoplay && !s.playing && el.isConnected && el.readyState >= 1) play(el);
            } else if (e.type === 'error') {
                if (s.sound) { state.sounds.close(s.sound, rel(now())); s.sound = null; }
            }
        }
        function onElementEvent(e) { onMediaEvent(e.currentTarget, e); }
        MEDIA_EVENTS.forEach(type => win.addEventListener(type, e => {
            const el = e.target;
            if (el instanceof win.HTMLMediaElement) onMediaEvent(el, e);
        }, true));

        // A page image's load / error, after start(): told at the frame boundary, in document order (the frame images are ours)
        ['load', 'error'].forEach(type => win.addEventListener(type, e => {
            const img = e.target;
            if (!state.started || !e.isTrusted || !(img instanceof win.HTMLImageElement) || img.hasAttribute('data-render-overlay')) return;
            e.stopImmediatePropagation();
            deliveries.add(2, () => { const i = Array.prototype.indexOf.call(doc.images, img); return i < 0 ? 1e9 : i; },
                () => img.dispatchEvent(new win.Event(type)));
        }, true));

        // New media elements: virtual from the start; autoplay becomes a virtual start once the metadata is in
        const adoptMedia = el => {
            const s = mstate(el);
            if (el.hasAttribute('autoplay')) {
                s.autoplay = true;
                HTMLElementRemove(el, 'autoplay');
                if (el.readyState >= 1 && el.isConnected) play(el);
            }
        };
        const HTMLElementRemove = (el, name) => win.Element.prototype.removeAttribute.call(el, name);
        const mediaIn = node => {
            if (!node || node.nodeType !== 1) return [];
            const list = node instanceof win.HTMLMediaElement ? [node] : [];
            return list.concat(Array.from(node.querySelectorAll ? node.querySelectorAll('video, audio') : []));
        };
        const observer = new win.MutationObserver(records => {
            records.forEach(r => {
                if (r.type === 'attributes') {
                    if (r.target instanceof win.HTMLMediaElement && r.target.hasAttribute('autoplay')) adoptMedia(r.target);
                    return;
                }
                r.addedNodes.forEach(n => mediaIn(n).forEach(adoptMedia));
                // removed from the page while playing: paused, as the browser does
                r.removedNodes.forEach(n => mediaIn(n).forEach(el => { if (!el.isConnected && media.has(el)) pause(el, true); }));
            });
        });
        observer.observe(doc, { childList: true, subtree: true, attributes: true, attributeFilter: ['autoplay'] });
        doc.querySelectorAll && doc.querySelectorAll('video, audio').forEach(adoptMedia);

        // ---- Animations: held, and set to their virtual time at every frame ----
        const AP = win.Animation && win.Animation.prototype;
        const OA = AP ? { play: AP.play, pause: AP.pause, finish: AP.finish, cancel: AP.cancel,
            currentTime: Object.getOwnPropertyDescriptor(AP, 'currentTime'), playState: Object.getOwnPropertyDescriptor(AP, 'playState') } : null;
        const anims = new WeakMap();
        const logical = (st, t) => (st.hold !== null ? st.hold : (t - st.start) * st.rate);
        const isCss = a => !!(win.CSSAnimation && a instanceof win.CSSAnimation);
        const endTimeOf = a => {
            try { return a.effect ? a.effect.getComputedTiming().endTime : Infinity; } catch (e) { return Infinity; }
        };
        function adopt(a, t) {
            let st = anims.get(a);
            if (st) return st;
            const real = OA.playState.get.call(a);
            st = { start: t, hold: null, rate: a.playbackRate > 0 ? a.playbackRate : 1, finished: false, idle: false, cssHeld: false, css: isCss(a) };
            if (real === 'paused') {
                st.hold = Number(OA.currentTime.get.call(a)) || 0;
                st.cssHeld = st.css; // held by its animation-play-state: it runs again when the style says so
            }
            if (real === 'finished') st.finished = true;
            if (real === 'idle') st.idle = true;
            anims.set(a, st);
            if (real === 'running') { try { OA.pause.call(a); } catch (e) { /* not pausable */ } }
            return st;
        }
        if (AP) {
            AP.play = function () {
                const st = anims.get(this);
                if (!st) return OA.play.call(this);
                const t = now();
                if (st.idle || st.finished) {
                    st.idle = false; st.finished = false; st.hold = null; st.start = t;
                    try { OA.play.call(this); OA.pause.call(this); OA.currentTime.set.call(this, 0); } catch (e) { /* restarted logically */ }
                } else if (st.hold !== null) {
                    st.start = t - st.hold / st.rate;
                    st.hold = null;
                }
                st.cssHeld = false;
            };
            AP.pause = function () {
                const st = anims.get(this);
                if (!st) return OA.pause.call(this);
                if (st.hold === null) st.hold = st.finished ? Math.min(logical(st, now()), endTimeOf(this)) : logical(st, now());
                st.finished = false;
                st.cssHeld = false;
                try { OA.pause.call(this); } catch (e) { /* held logically */ }
            };
            AP.finish = function () {
                const result = OA.finish.call(this); // (throws for an endless animation, as the browser does)
                const st = anims.get(this);
                if (st) { st.finished = true; st.hold = null; }
                return result;
            };
            AP.cancel = function () {
                const st = anims.get(this);
                if (st) st.idle = true;
                return OA.cancel.call(this);
            };
            Object.defineProperty(AP, 'currentTime', { configurable: true, enumerable: true,
                get() { const st = anims.get(this); return st ? (st.idle ? null : logical(st, now())) : OA.currentTime.get.call(this); },
                set(v) {
                    const st = anims.get(this);
                    if (st && v !== null && Number.isFinite(Number(v))) {
                        if (st.hold !== null) st.hold = Number(v);
                        else st.start = now() - Number(v) / st.rate;
                        st.finished = false;
                    }
                    OA.currentTime.set.call(this, v);
                } });
            Object.defineProperty(AP, 'playState', { configurable: true, enumerable: true,
                get() {
                    const st = anims.get(this);
                    if (!st) return OA.playState.get.call(this);
                    return st.idle ? 'idle' : st.finished ? 'finished' : st.hold !== null ? 'paused' : 'running';
                } });
        }
        if (win.Element && win.Element.prototype.animate && OA) {
            const realAnimate = win.Element.prototype.animate;
            win.Element.prototype.animate = function () {
                const a = realAnimate.apply(this, arguments);
                try { adopt(a, now()); } catch (e) { /* adopted at the next frame */ }
                return a;
            };
        }
        // A CSS animation's animation-play-state (e.g. .mascot-paused) holds it on the virtual clock too
        function cssPlayState(a) {
            const effect = a.effect;
            if (!effect || !effect.target || !a.animationName) return 'running';
            try {
                const cs = win.getComputedStyle(effect.target, effect.pseudoElement || null);
                const names = cs.animationName.split(',').map(s => s.trim());
                const states = cs.animationPlayState.split(',').map(s => s.trim());
                const i = names.indexOf(a.animationName);
                return i < 0 || !states.length ? 'running' : states[i % states.length];
            } catch (e) {
                return 'running';
            }
        }
        const allAnimations = () => (OA && typeof doc.getAnimations === 'function' ? doc.getAnimations() : []);
        function adoptAnimations() {
            const t = now();
            allAnimations().forEach(a => { try { adopt(a, t); } catch (e) { /* one animation never stops a frame */ } });
        }
        function stepAnimations() {
            const t = now();
            allAnimations().forEach(a => {
                try {
                    const st = adopt(a, t);
                    if (st.idle) return;
                    if (st.css) {
                        const wanted = cssPlayState(a);
                        if (wanted === 'paused' && st.hold === null && !st.finished) { st.hold = logical(st, t); st.cssHeld = true; }
                        else if (wanted === 'running' && st.cssHeld) { st.start = t - st.hold / st.rate; st.hold = null; st.cssHeld = false; }
                    }
                    if (st.finished) return;
                    const lt = logical(st, t);
                    const end = endTimeOf(a);
                    if (Number.isFinite(end) && lt >= end && st.hold === null) {
                        st.finished = true;
                        OA.finish.call(a); // its finished promise, its end events, a view transition completing
                        return;
                    }
                    if (OA.playState.get.call(a) !== 'paused') OA.pause.call(a);
                    OA.currentTime.set.call(a, Math.max(0, lt));
                } catch (e) { /* one animation never stops a frame */ }
            });
        }

        // ---- Frames: a frame set's image in the clip's box, or the clip seeked to its virtual time ----
        const overlays = new WeakMap();
        const prefetch = new WeakMap();
        const decoding = new Set();
        // a decode the gate waits for (and wakes on)
        const trackDecode = p => {
            const id = work.start('decode');
            decoding.add(p);
            p.then(() => { decoding.delete(p); work.end(id); });
        };
        const pageDecoded = new WeakMap();
        const COPIED = ['object-fit', 'object-position', 'opacity', 'visibility', 'transform', 'transform-origin', 'clip-path',
            'filter', 'border-top-left-radius', 'border-top-right-radius', 'border-bottom-right-radius', 'border-bottom-left-radius',
            'z-index', 'mix-blend-mode', 'padding-top', 'padding-right', 'padding-bottom', 'padding-left'];
        const frameSetFor = el => {
            if (!state.frameSets.size) return null;
            return state.frameSets.get(normalizeSrc(el.currentSrc || el.getAttribute('src') || '', doc.baseURI)) || null;
        };
        function overlayFor(v) {
            let img = overlays.get(v);
            if (img && img.isConnected && img.previousElementSibling === v) return img;
            if (img) img.remove();
            img = doc.createElement('img');
            img.alt = '';
            img.decoding = 'sync';
            img.setAttribute('aria-hidden', 'true');
            img.setAttribute('data-render-overlay', '');
            const parent = v.parentElement;
            // the frame's box is the clip's own: its parent holds both (and clips them alike)
            if (parent && win.getComputedStyle(parent).position === 'static') {
                parent.style.position = 'relative';
                parent.setAttribute('data-render-positioned', '');
            }
            v.after(img);
            overlays.set(v, img);
            return img;
        }
        function dropOverlay(v) {
            const img = overlays.get(v);
            if (img) { img.remove(); overlays.delete(v); }
            if (v.hasAttribute('data-render-frame')) {
                v.removeAttribute('data-render-frame');
                v.style.removeProperty('-webkit-mask-image');
                v.style.removeProperty('mask-image');
            }
        }
        function placeOverlay(v, img) {
            const cs = win.getComputedStyle(v);
            const shown = v.isConnected && cs.display !== 'none' && v.offsetParent !== null;
            const set = (p, value) => img.style.setProperty(p, value, 'important');
            if (!shown) { set('display', 'none'); return false; }
            set('display', 'block');
            set('position', 'absolute');
            set('left', v.offsetLeft + 'px');
            set('top', v.offsetTop + 'px');
            set('width', v.offsetWidth + 'px');
            set('height', v.offsetHeight + 'px');
            ['max-width', 'max-height'].forEach(p => set(p, 'none'));
            ['min-width', 'min-height', 'margin'].forEach(p => set(p, '0'));
            set('box-sizing', 'border-box');
            set('border-style', 'solid');
            set('border-color', 'transparent');
            ['top', 'right', 'bottom', 'left'].forEach(side => set(`border-${side}-width`, cs.getPropertyValue(`border-${side}-width`)));
            COPIED.forEach(p => set(p, cs.getPropertyValue(p)));
            set('background', 'transparent');
            set('box-shadow', 'none');
            set('animation', 'none');
            set('transition', 'none');
            set('pointer-events', 'none');
            return cs.visibility !== 'hidden' && Number(cs.opacity) > 0;
        }
        function seekReal(v, t) {
            if (realReady(v) < 1) return null;
            const target = Math.max(0, t + 0.0001);
            if (Math.abs((O.currentTime.get.call(v) || 0) - target) < 0.002) return null;
            return new Promise(resolve => {
                let open = true;
                const done = () => { if (open) { open = false; resolve(); } };
                seekWaiters.set(v, () => {
                    if (typeof v.requestVideoFrameCallback === 'function') { v.requestVideoFrameCallback(() => done()); realSetTimeout(done, 120); } else done();
                });
                realSetTimeout(done, 5000);
                O.currentTime.set.call(v, target);
            });
        }
        // Every clip shows the frame for its virtual time; returns how many images / seeks it had to start
        function presentMedia() {
            let started = 0;
            doc.querySelectorAll('video').forEach(v => {
                const set = frameSetFor(v);
                if (!set) {
                    if (overlays.has(v) || v.hasAttribute('data-render-frame')) dropOverlay(v);
                    const cs = v.isConnected ? win.getComputedStyle(v) : null;
                    if (!cs || cs.display === 'none' || cs.visibility === 'hidden' || !v.getClientRects().length) return;
                    const p = seekReal(v, timeOf(v));
                    if (p) { started++; trackDecode(p); }
                    return;
                }
                if (!v.hasAttribute('data-render-frame')) {
                    v.setAttribute('data-render-frame', '');
                    v.style.setProperty('-webkit-mask-image', 'linear-gradient(transparent, transparent)', 'important');
                    v.style.setProperty('mask-image', 'linear-gradient(transparent, transparent)', 'important');
                }
                const img = overlayFor(v);
                if (!placeOverlay(v, img)) return; // hidden: keeps its last frame, nothing to load
                const t = timeOf(v);
                const url = frameUrl(set, t);
                // the next frame's image loads and decodes while this one is captured
                const s = mstate(v);
                if (s.playing && s.anchorAt !== null) {
                    const next = frameUrl(set, t + s.rate / state.fps);
                    if (next !== url) {
                        let ahead = prefetch.get(v);
                        if (!ahead) { ahead = new win.Image(); prefetch.set(v, ahead); }
                        if (ahead.getAttribute('src') !== next) { ahead.setAttribute('src', next); ahead.decode().catch(() => {}); }
                    }
                }
                if (img.getAttribute('src') !== url) {
                    img.setAttribute('src', url);
                    started++;
                    trackDecode(img.decode().catch(() => {
                        if (!state.gateNotes.has('frame:' + url)) { state.gateNotes.add('frame:' + url); note(state.range.lastScene, 'A video frame could not be shown.'); }
                    }));
                }
            });
            return started;
        }

        // ---- The readiness gate ----
        // What the page is seen waiting for: images loading, clips loading what the page needs of them (the metadata; enough to
        // play for a clip that plays or is preloaded), web fonts; and a decode for every page image newly loaded
        function observePage() {
            const images = [];
            Array.from(doc.images || []).forEach(img => {
                const src = img.currentSrc || img.getAttribute('src');
                if (!img.isConnected || img.loading === 'lazy' || !src || img.hasAttribute('data-render-overlay')) return;
                if (!img.complete) { images.push([img, src, src.split('?')[0].split('/').pop()]); return; }
                if (img.naturalWidth > 0 && pageDecoded.get(img) !== src) {
                    pageDecoded.set(img, src);
                    trackDecode(img.decode().catch(() => {}));
                }
            });
            work.observe('images', images);
            const clips = [];
            const seen = new Set();
            const consider = el => {
                if (seen.has(el)) return;
                seen.add(el);
                if (el.error || !hasSource(el) || el.networkState !== 2) return; // (2: still loading)
                const s = mstate(el);
                const need = s.playing || s.autoplay || el.preload === 'auto' ? 3 : (el.preload === 'none' ? 0 : 1);
                if (realReady(el) < need) clips.push([el, el.currentSrc || el.getAttribute('src'), fileOf(el)]);
            };
            doc.querySelectorAll('video, audio').forEach(consider);
            known.forEach(consider);
            work.observe('media', clips);
            work.observe('fonts', doc.fonts && doc.fonts.status === 'loading' ? [[doc, 'fonts', 'the fonts']] : []);
        }
        const KIND_WORDS = { network: 'a request to the server', images: 'an image', decode: 'an image', media: 'a clip', fonts: 'the fonts',
            mathjax: 'a formula', transition: 'a scene transition' };
        // Waits until no real work is unfinished (the virtual clock does not move meanwhile); a stall stops the render
        async function settle() {
            const started = realNow();
            let quiet = 0;
            for (;;) {
                wrapMathJax();
                adoptAnimations();
                observePage();
                const stalled = work.stalled();
                if (stalled) {
                    const err = new Error(`The render stopped: ${KIND_WORDS[stalled.kind] || stalled.kind} did not finish within ${Math.round((STALL_MS[stalled.kind] || 0) / 1000)} seconds${stalled.what ? ` (${String(stalled.what).slice(0, 80)})` : ''}.`);
                    err.stall = { kind: stalled.kind, what: stalled.what };
                    throw err;
                }
                const busy = work.busy();
                if (!busy.length) {
                    if (++quiet >= 2) return realNow() - started;
                    await realYield();
                } else {
                    quiet = 0;
                    // a frame image decoding wakes the gate as soon as it is done; anything else is polled
                    await (busy.length === 1 && busy[0] === 'decode' ? Promise.race([Promise.all(Array.from(decoding)), realDelay(8)]) : realDelay(8));
                }
            }
        }
        // Tells the page what finished, in order, until nothing is left (each round can start more work: waited for first)
        async function deliverAll() {
            let rounds = 0;
            let waited = 0;
            for (;;) {
                waited += await settle();
                queueLoading();
                if (!deliveries.size) return waited;
                deliveries.drain();
                await realYield(); // the page's own reactions (promise callbacks) run before the next round
                if (++rounds > 500) throw new Error('The render stopped: the page kept reacting to itself without the clock moving.');
            }
        }

        // scene: the scene index (default: the scene that began last)
        function note(scene, text) {
            state.notes.push({ at: rel(now()), scene: Number.isInteger(scene) ? scene : state.range.lastScene, text: String(text).slice(0, 300) });
        }

        const runtime = {
            config,
            now,
            realNow,
            realDelay,
            get epoch() { return state.epoch; },
            get started() { return state.started; },
            get range() { return state.range; },
            // virtual t = 0: the render's start (the frame grid and every timeline time count from here)
            markStart() {
                state.epoch = now();
                state.started = true;
                // only what happens from here on is in the render's timeline
                state.sounds = new SoundLog();
                state.sfx = [];
                state.notes = [];
                state.sync = [];
                Math.random = seededRandom((config.seed ^ 0x9E3779B9) >>> 0);
                wrapMathJax();
                // Every clip starts the render from its beginning (Aadhi's has played since the page loaded, for as long as this
                // machine took to prepare: never part of the video); from here on the page hears of its loading at frame boundaries
                const all = new Set(known);
                doc.querySelectorAll('video, audio').forEach(el => all.add(el));
                all.forEach(el => {
                    const s = mstate(el);
                    s.vReady = realReady(el);
                    s.vErr = !!el.error;
                    s.base = 0;
                    s.ended = false;
                    s.sound = null;
                    if (s.playing) {
                        s.anchorAt = s.vReady >= 1 ? state.epoch : null;
                        s.waiting = s.vReady < 1;
                    }
                    scheduleEnd(el);
                    updateSound(el);
                });
                if (tickTimer !== null) { win.clearTimeout(tickTimer); tickTimer = null; }
                ensureTicker();
            },
            // A pre-roll draws the clips' images only from scene `index` on (the scene before the range's first one)
            presentFrom(index) {
                state.presentFromScene = Number.isInteger(index) && index >= 0 ? index : null;
            },
            useFrames(map) {
                const accepted = [];
                Object.keys(map || {}).forEach(src => {
                    const entry = map[src];
                    const manifest = normalizeManifest(entry && entry.manifest);
                    const base = entry && typeof entry.base === 'string' ? entry.base : '';
                    if (!manifest || !base.startsWith(config.frameBase) || !base.endsWith('/') || base.includes('..')) return;
                    const key = normalizeSrc(src, doc.baseURI);
                    if (!key) return;
                    state.frameSets.set(key, { base, manifest });
                    accepted.push(key);
                });
                return accepted;
            },
            // Applies the frame for virtual time tMs (ms from start(); the clock was already moved there by the worker)
            // The real work the page waits on is finished first (the clock does not move meanwhile) and told to the page in a
            // fixed order; then the animations and the clips are set to this time. The clips' frame images are drawn on captured
            // frames and, in a pre-roll, from the scene before the range's first one on: the scene transition into the range
            // snapshots the page as it was drawn last, so it must look exactly like the page that captured those frames.
            // Rejects with a plain message when a piece of work stalls.
            async frameReady(tMs) {
                const t = rel(now());
                const at = Number.isFinite(tMs) ? tMs : t;
                const present = () => state.presentFromScene === null || (state.range.rangeStartAt !== null && at >= state.range.rangeStartAt)
                    || (state.range.lastScene !== null && state.range.lastScene >= state.presentFromScene);
                let waited = 0;
                for (let pass = 0; pass < 8; pass++) {
                    waited += await deliverAll();
                    stepAnimations();
                    if (!present() || !presentMedia()) break;
                }
                waited += await deliverAll();
                stepAnimations();
                const r = state.range.frame(at);
                state.lastFrame = { tMs, virtual: t };
                return Object.assign(r, { waitedMs: Math.round(waited), clockSkewMs: Number.isFinite(tMs) ? Math.round((t - tMs) * 1000) / 1000 : 0 });
            },
            // Waits for `promise` (a call the worker makes between frames, e.g. the lesson link), telling the page what finished
            // meanwhile (after start() the page hears of finished work only at frame boundaries; the clock does not move here)
            async pump(promise) {
                let settled = false;
                let value;
                let failure = null;
                Promise.resolve(promise).then(v => { settled = true; value = v; }, e => { settled = true; failure = e || new Error('failed'); });
                while (!settled) {
                    await deliverAll();
                    if (!settled) await realDelay(5);
                }
                if (failure) throw failure;
                return value;
            },
            sceneBegan(index, title, type) { state.range.sceneBegan(index, rel(now()), title, type); },
            lessonEnded() { state.range.lessonEnded(rel(now())); },
            note,
            noteSync(scene, kind, target, extra) {
                state.sync.push({ at: rel(now()), scene: Number.isInteger(scene) ? scene : state.range.lastScene, kind: String(kind || 'sync'),
                    target: target === undefined ? null : target, extra: extra || null });
            },
            // cues: [{start, end, text}] in seconds from start()
            timeline(cues) {
                const at = rel(now());
                const sounds = state.sounds.entries.map(e => Object.assign({}, e));
                return buildTimeline({ fps: state.fps, captureFrom: state.range.captureFrom, rangeStartAt: state.range.rangeStartAt,
                    scenes: state.range.scenes, cues, sounds, sfx: state.sfx, notes: state.notes, sync: state.sync, now: at });
            },
            stats() {
                return { playing: playing.size, frameSets: state.frameSets.size, waitingFor: work.busy(), sounds: state.sounds.entries.length,
                    sfx: state.sfx.length, notes: state.notes.length, sync: state.sync.length };
            }
        };
        return runtime;
    }

    const api = {
        DEFAULT_FPS,
        readConfig,
        frameTimeMs,
        frameAtOrAfter,
        nextFrameTime,
        normalizeManifest,
        frameIndex,
        frameFileName,
        frameUrl,
        normalizeSrc,
        seededRandom,
        pinGifRequest,
        mediaTime,
        mediaEndAt,
        soundKind,
        SoundLog,
        synthFromNodes,
        createFakeAudioContext,
        setProgress,
        createProgress,
        PendingWork,
        DeliveryQueue,
        RangeTracker,
        buildTimeline,
        activate,
        runtime: null
    };
    return api;
});
