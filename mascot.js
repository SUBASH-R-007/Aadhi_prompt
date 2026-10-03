/*
 * Aadhi mascot controller.
 *
 * One object owns every mascot <video> layer inside #mascot-bg: which clip is
 * on screen, keeping it playing, preloading, crossfades, recovery from load
 * errors / stalls / unexpected ends, and the animated poster fallback that
 * keeps Aadhi moving whenever a clip cannot play. The page only tells it
 * WHERE Aadhi stands (placement, from the scene's aadhi_position) and WHAT he
 * is doing (behaviour state, driven by narration and quiz events).
 *
 * Loaded as a classic <script> (window.AadhiMascot) and as a CommonJS module
 * by the Node unit tests in tests/mascot.test.js.
 */
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    } else {
        root.AadhiMascot = api;
    }
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    const STATES = ['idle', 'talking', 'explaining', 'thinking', 'question', 'success'];

    // aadhi_position values the layout engine understands
    const PLACEMENTS = ['left', 'right', 'center', 'popup_bottom_left', 'popup_bottom_right', 'hidden'];
    // Values generated screenplays use that are not in the list above
    const PLACEMENT_ALIASES = { popup: 'popup_bottom_left', none: 'hidden' };

    // Which clip (the layer's data-asset) renders each placement
    const PLACEMENT_ASSET = {
        left: 'left',
        right: 'right',
        center: 'center',
        popup_bottom_left: 'popup',
        popup_bottom_right: 'popup',
        hidden: 'none'
    };

    // Where Aadhi stands in each 16:9 clip, as fractions of the frame.
    // cue: anchor for the thought/speech bubble beside his head (null: no bubble;
    //      the popup layout's board covers his face, so a bubble would float unattached).
    // pivot: his feet, the origin of the fallback breathing motion.
    const FRAMING = {
        left: { cue: [0.11, 0.10], pivot: [0.21, 0.92] },
        right: { cue: [0.93, 0.09], pivot: [0.83, 0.92] },
        center: { cue: [0.43, 0.09], pivot: [0.53, 0.92] },
        popup: { cue: null, pivot: [0.50, 1.00] },
        none: { cue: null, pivot: [0.50, 0.50] }
    };
    const FRAME_ASPECT = 16 / 9;

    const TIMING = {
        fadeMs: 600,             // the CSS crossfade is 0.5s; outgoing clips pause after it
        swapTimeoutMs: 1500,     // longest wait for an incoming clip's first frame
        preloadTimeoutMs: 8000,  // a lesson never waits longer than this for mascot clips
        retryDelaysMs: [1000, 3000],
        watchdogMs: 1000,
        stallMs: 3500,           // no playback progress for this long means the clip is frozen
        playAttempts: 3          // failed play() calls before switching to the fallback
    };

    const CUE_STATES = ['thinking', 'question', 'success'];
    const SPEAKING_STATES = ['talking', 'explaining'];

    function normalizeState(value) {
        const v = typeof value === 'string' ? value.trim().toLowerCase() : '';
        return STATES.includes(v) ? v : null;
    }

    function normalizePlacement(value) {
        if (typeof value !== 'string') return null;
        const v = value.trim().toLowerCase();
        if (!v) return null;
        return PLACEMENT_ALIASES[v] || v;
    }

    // The placement rules renderSlide and applyAadhiLayout have always used, kept
    // in one place so lesson preloading and scene rendering agree.
    function resolvePlacement(slide) {
        slide = slide || {};
        const position = normalizePlacement(slide.aadhi_position);
        if (slide.type === 'ai_video' && (!position || position === 'hidden')) {
            return 'left'; // Aadhi stays in the background around ai_video windows
        }
        if (!position) {
            // Scenes generated before aadhi_position existed
            let needsMascot = slide.type === 'title' || slide.type === 'content';
            if (needsMascot && slide.html && slide.html.length > 300) needsMascot = false;
            if (slide.force_background && slide.force_background !== 'auto') {
                needsMascot = slide.force_background === 'mascot';
            }
            return needsMascot ? 'left' : 'hidden';
        }
        return PLACEMENTS.includes(position) ? position : 'hidden';
    }

    function requiredAssetKeys(slides) {
        const keys = new Set();
        (slides || []).forEach(slide => keys.add(PLACEMENT_ASSET[resolvePlacement(slide)]));
        return Array.from(keys);
    }

    // Maps a point of the 16:9 clip to container pixels under object-fit: cover
    function coverPoint(fx, fy, width, height) {
        const frameH = Math.max(width / FRAME_ASPECT, height);
        const frameW = frameH * FRAME_ASPECT;
        return {
            x: (width - frameW) / 2 + fx * frameW,
            y: (height - frameH) / 2 + fy * frameH
        };
    }

    function isAutoplayBlock(err) {
        return !!err && err.name === 'NotAllowedError';
    }

    function describeMediaError(el) {
        const err = el && el.error;
        if (!err) return 'media error';
        const names = { 1: 'aborted', 2: 'network error', 3: 'decode error', 4: 'source not supported' };
        return (names[err.code] || 'media error') + (err.message ? ' (' + err.message + ')' : '');
    }

    function fileName(src) {
        return src ? String(src).split('?')[0].split('/').pop() : '—';
    }

    // One "Enable playback" button for browsers that refuse to start media
    // without a user gesture. Everything that was blocked is retried from inside
    // that click, which is what the browser requires.
    class PlaybackGate {
        constructor(doc) {
            this.doc = doc;
            this.pending = [];
            this.button = null;
        }

        isBlocked() {
            return this.pending.length > 0;
        }

        request(retry) {
            if (typeof retry === 'function') this.pending.push(retry);
            if (!this.button) {
                const button = this.doc.createElement('button');
                button.type = 'button';
                button.className = 'btn-gold playback-gate';
                button.setAttribute('data-mascot-runtime', '');
                button.textContent = '▶ Enable playback';
                button.addEventListener('click', () => this.unlock());
                this.doc.body.appendChild(button);
                this.button = button;
            }
            this.button.classList.add('visible');
        }

        unlock() {
            const retries = this.pending;
            this.pending = [];
            if (this.button) this.button.classList.remove('visible');
            retries.forEach(retry => {
                try {
                    retry();
                } catch (e) {
                    console.error('Playback retry failed:', e);
                }
            });
        }
    }

    class MascotController {
        constructor(container, options = {}) {
            this.container = container;
            this.doc = container.ownerDocument;
            this.view = this.doc.defaultView || null;
            this.gate = options.gate || null;
            this.timing = Object.assign({}, TIMING, options.timing);
            this.now = options.now || (() => Date.now());
            // Same once-per-page-load cache busting the page has always used for these clips
            this.cacheBust = 't=' + this.now();
            this.fallbackStill = container.getAttribute('data-fallback-src') || '';
            this.cuesEnabled = container.getAttribute('data-mascot-cues') !== 'off';

            this.state = 'idle';
            this.placement = null;
            this.activeAsset = null;
            this.activeLayer = null;   // the clip Aadhi should be showing
            this.visibleLayer = null;  // the clip currently faded in (lags activeLayer during a swap)
            this.fadingOut = null;
            this.wantPlaying = true;
            this.recording = false;
            this.fallback = null; // { reason } while the poster fallback is on screen
            this.lastTransition = null;
            this.lastError = null;
            this.sceneSpeakState = 'talking';
            this.narration = { audio: 'SILENT', speakState: 'talking', explaining: false };
            this.debug = false;
            this.hud = null;
            this._transitionToken = 0;
            this._retrySeq = 0;
            this._preload = null;
            this._gateRequested = false;
            this._warned = new Set();
            this._warmedPosters = new Set();
            this.resetMetrics();

            // An exported HTML file is a clone of the live DOM: drop runtime nodes it carried over
            this.doc.querySelectorAll('[data-mascot-runtime]').forEach(node => node.remove());
            container.classList.remove('mascot-fallback-active', 'mascot-paused');

            this.layers = Array.from(container.querySelectorAll('.mascot-layer')).map(el => this._adoptLayer(el));
            this._buildOverlay();
            this.setDebug(!!options.debug);

            this._watchdog = setInterval(() => this._tick(), this.timing.watchdogMs);
            this._onVisibility = () => {
                if (!this.doc.hidden) this._tick();
            };
            this.doc.addEventListener('visibilitychange', this._onVisibility);
            if (this.view) {
                this._onResize = () => this._applyFraming();
                this.view.addEventListener('resize', this._onResize);
            }

            this.setPlacement(options.placement || 'left');
        }

        // ---- Public API ------------------------------------------------------

        getCurrentState() {
            return this.state;
        }

        // Behaviour state. Repeating the current state is a no-op: nothing reloads or restarts.
        setState(state) {
            const next = normalizeState(state);
            if (!next) {
                this._log('ignored unknown state:', state);
                return false;
            }
            if (next === this.state) return false;
            this.lastTransition = { from: this.state, to: next, at: this.now() };
            this.state = next;
            // Only switches clips when this placement has a dedicated clip for the state
            this._activate(this._layerFor(this.activeAsset, next));
            this._render();
            return true;
        }

        // Where Aadhi stands: an aadhi_position value.
        setPlacement(placement) {
            const normalized = normalizePlacement(placement);
            this.placement = PLACEMENTS.includes(normalized) ? normalized : 'hidden';
            this.activeAsset = PLACEMENT_ASSET[this.placement];
            this._applyFraming();
            this._activate(this._layerFor(this.activeAsset, this.state));
            this._render();
        }

        // Called once per scene: where Aadhi stands and how he narrates it (the
        // optional scene field mascot_state; defaults to talking).
        beginScene(placement, sceneState) {
            this.sceneSpeakState = normalizeState(sceneState) || 'talking';
            this.narration = { audio: 'SILENT', speakState: this.sceneSpeakState, explaining: false };
            if (this.state !== 'idle') {
                this.lastTransition = { from: this.state, to: 'idle', at: this.now() };
                this.state = 'idle';
            }
            this.setPlacement(placement);
        }

        // Narration → behaviour state. speakingState overrides the scene's state
        // for one narration (the quiz uses 'question' and 'success').
        //   segment: a narration segment starts speaking    resume: speaking again after a hold
        //   sync:    a [SYNC] reveal put content on the board
        //   pause:   scripted [PAUSE] beat of silence        hold: narration paused by viewer/browser
        //   end:     narration finished or cancelled
        narrationEvent(type, speakingState) {
            const n = this.narration;
            switch (type) {
                case 'segment':
                    n.speakState = normalizeState(speakingState) || this.sceneSpeakState;
                    n.explaining = false;
                    n.audio = 'SPEAKING';
                    return this.setState(n.speakState);
                case 'resume':
                    n.audio = 'SPEAKING';
                    return this.setState(n.explaining ? 'explaining' : n.speakState);
                case 'sync':
                    // Presenting board content; never overrides a question or success moment
                    if (n.audio !== 'SPEAKING' || n.speakState !== 'talking') return false;
                    n.explaining = true;
                    return this.setState('explaining');
                case 'pause':
                    n.audio = 'PAUSE';
                    n.explaining = false;
                    return this.setState('thinking');
                case 'hold':
                    n.audio = 'HOLD';
                    return this.setState('idle');
                case 'end':
                    n.audio = 'SILENT';
                    n.explaining = false;
                    return this.setState('idle');
                default:
                    return false;
            }
        }

        // Preloads the clips a lesson needs (data-asset keys). Resolves with every
        // clip's status once they are all loaded or failed, or when the timeout
        // passes; it never rejects, so one bad clip cannot block a lesson.
        preload(assetKeys, timeoutMs) {
            const keys = assetKeys || this.layers.map(layer => layer.asset);
            const loads = this.layers.filter(layer => keys.includes(layer.asset)).map(layer => {
                this._warmPoster(layer);
                if (layer.status === 'loaded' || layer.el.readyState >= 3) {
                    layer.status = 'loaded';
                    return Promise.resolve();
                }
                return new Promise(resolve => {
                    layer.waiters.push(resolve);
                    if (layer.status === 'failed') {
                        layer.retries = 0;
                        this._reload(layer);
                    } else if (layer.status !== 'error' && layer.el.preload !== 'auto') {
                        layer.status = 'loading';
                        layer.el.preload = 'auto';
                        // Restart the fetch with the stronger hint (the on-screen clip is loading already)
                        if (layer !== this.activeLayer) layer.el.load();
                    } else if (layer.status === 'idle') {
                        layer.status = 'loading';
                    }
                });
            });
            let timer;
            const timeout = new Promise(resolve => {
                timer = setTimeout(resolve, timeoutMs === undefined ? this.timing.preloadTimeoutMs : timeoutMs);
            });
            this._preload = Promise.race([Promise.all(loads), timeout]).then(() => {
                clearTimeout(timer);
                this._render();
                return this.getStatus().assets;
            });
            this._render();
            return this._preload;
        }

        whenReady() {
            return this._preload || Promise.resolve(this.getStatus().assets);
        }

        // Fresh start for a new lesson: idle, playing, and failed clips get another chance.
        reset() {
            this.sceneSpeakState = 'talking';
            this.narration = { audio: 'SILENT', speakState: 'talking', explaining: false };
            this.setState('idle');
            this.lastError = null;
            this.layers.forEach(layer => {
                if (layer.status === 'failed') {
                    layer.retries = 0;
                    this._reload(layer);
                }
            });
            this.play();
        }

        play() {
            if (this.suspended) { this.resumeWanted = true; return; } // covered by another background: stays paused
            this.wantPlaying = true;
            if (this.activeLayer && this.activeLayer.el.paused) this._play(this.activeLayer);
            this._render();
        }

        // A cinematic scene (Phase 13) whose background covers the studio: the clips stop decoding until it is
        // uncovered (then they play again if they were playing). Recording cannot restart a covered clip.
        suspend(on) {
            if (!!on === !!this.suspended) return;
            if (on) {
                this.resumeWanted = this.wantPlaying;
                this.pause();
                this.suspended = true;
            } else {
                this.suspended = false;
                if (this.resumeWanted) this.play();
                this.resumeWanted = false;
            }
        }

        pause() {
            this.wantPlaying = false;
            this.layers.forEach(layer => {
                if (!layer.el.paused) layer.el.pause();
            });
            this._render();
        }

        // Tab/screen recording captures whatever is painted: keep Aadhi animating
        // and keep the debug HUD out of the video.
        setRecording(on) {
            this.recording = !!on;
            if (this.recording) this.play();
            this._render();
        }

        setDebug(on) {
            this.debug = !!on;
            if (this.debug && !this.hud) {
                this.hud = this.doc.createElement('div');
                this.hud.className = 'mascot-debug-hud';
                this.hud.setAttribute('data-mascot-runtime', '');
                this.doc.body.appendChild(this.hud);
            } else if (!this.debug && this.hud) {
                this.hud.remove();
                this.hud = null;
            }
            this._render();
        }

        // Playback measurements (Phase 7): counts and a short event timeline with each clip's media
        // state, to tell buffering (little data ahead) from a starved decoder (data ahead, no progress)
        resetMetrics() {
            this.metrics = { since: this.now(), switches: 0, fallbacks: 0, fallbackReasons: {}, fallbackMs: 0, stalls: 0,
                waiting: 0, stalledEvents: 0, reloads: 0, playFailures: 0, events: [] };
            this._fallbackSince = this.fallback ? this.now() : null;
        }

        getMetrics() {
            const m = this.metrics;
            const open = this._fallbackSince !== null ? this.now() - this._fallbackSince : 0;
            return Object.assign({}, m, { fallbackReasons: Object.assign({}, m.fallbackReasons), fallbackMs: m.fallbackMs + open,
                inFallback: !!this.fallback, events: m.events.slice() });
        }

        _measure(type, layer, extra) {
            const m = this.metrics;
            if (m.events.length >= 300) return;
            const el = layer && layer.el;
            let ahead = null;
            try {
                if (el && el.buffered && el.buffered.length) ahead = Math.round((el.buffered.end(el.buffered.length - 1) - el.currentTime) * 100) / 100;
            } catch (e) { /* no buffered ranges yet */ }
            m.events.push(Object.assign({ t: this.now() - m.since, type, asset: layer ? layer.asset : null,
                readyState: el ? el.readyState : null, networkState: el ? el.networkState : null,
                currentTime: el ? Math.round(el.currentTime * 100) / 100 : null, ahead }, extra || {}));
        }

        getStatus() {
            const layer = this.activeLayer;
            const assets = {};
            this.layers.forEach(l => {
                assets[l.state ? l.asset + '/' + l.state : l.asset] = l.status;
            });
            return {
                state: this.state,
                placement: this.placement,
                asset: layer ? fileName(layer.baseSrc) : null,
                playback: this._playbackLabel(),
                fallback: this.fallback ? this.fallback.reason : null,
                audio: this.narration.audio,
                recording: this.recording,
                gestureRequired: !!(this.gate && this.gate.isBlocked()),
                assets,
                lastTransition: this.lastTransition,
                lastError: this.lastError ? this.lastError.message : null
            };
        }

        destroy() {
            clearInterval(this._watchdog);
            clearTimeout(this._fadeTimer);
            clearTimeout(this._swapTimer);
            this.layers.forEach(layer => clearTimeout(layer.retryTimer));
            this.doc.removeEventListener('visibilitychange', this._onVisibility);
            if (this.view) this.view.removeEventListener('resize', this._onResize);
        }

        // ---- Layers ------------------------------------------------------------

        _adoptLayer(el) {
            const layer = {
                el,
                asset: el.getAttribute('data-asset'),
                state: el.getAttribute('data-state') || null, // optional clip for one behaviour state
                baseSrc: el.getAttribute('data-src'),
                status: 'idle', // idle | loading | loaded | error (retrying) | failed
                retries: 0,
                retryTimer: null,
                waiters: [],
                lastTime: -1,
                lastProgressAt: 0,
                buffering: false,
                stallStrikes: 0,
                playFailures: 0
            };
            el.classList.remove('active-layer');
            el.muted = true; // muted clips may autoplay without a user gesture
            el.src = this._srcFor(layer, false);

            el.addEventListener('canplay', () => this._markLoaded(layer));
            el.addEventListener('canplaythrough', () => this._markLoaded(layer));
            el.addEventListener('playing', () => this._onPlaying(layer));
            el.addEventListener('timeupdate', () => this._noteProgress(layer));
            el.addEventListener('waiting', () => this._onBuffering(layer, 'waiting'));
            el.addEventListener('stalled', () => this._onBuffering(layer, 'stalled'));
            el.addEventListener('ended', () => this._onEnded(layer));
            el.addEventListener('error', () => this._onError(layer));
            // External pauses (power saving, media keys, tab switches) are resumed by the watchdog
            el.addEventListener('pause', () => this._render());
            el.addEventListener('loadedmetadata', () => this._render());
            return layer;
        }

        _srcFor(layer, fresh) {
            const sep = layer.baseSrc.includes('?') ? '&' : '?';
            return layer.baseSrc + sep + this.cacheBust + (fresh ? '&retry=' + (++this._retrySeq) : '');
        }

        _layerFor(asset, state) {
            return this.layers.find(l => l.asset === asset && l.state === state)
                || this.layers.find(l => l.asset === asset && !l.state)
                || null;
        }

        _activate(layer) {
            if (!layer) return;
            if (layer === this.activeLayer) {
                if (this.wantPlaying && layer.el.paused) this._play(layer);
                return;
            }
            const token = ++this._transitionToken;
            this.activeLayer = layer;
            this.metrics.switches++;
            this._measure('switch', layer);
            // The clip on screen keeps playing until the new one has faded in over it
            this.fadingOut = this.visibleLayer === layer ? null : this.visibleLayer;
            layer.lastProgressAt = this.now();
            layer.stallStrikes = 0;
            layer.playFailures = 0;
            if (layer.status === 'idle') layer.status = 'loading';

            if (layer.status === 'failed') {
                // One more attempt at a clip that failed earlier; the fallback covers the wait
                layer.retries = this.timing.retryDelaysMs.length;
                this._enterFallback('clip failed to load');
                this._reload(layer);
            } else if (layer.status === 'error') {
                // A retry is already scheduled and will start it; animate meanwhile
                this._enterFallback('clip error, retrying');
            } else if (this.wantPlaying) {
                this._play(layer);
            }

            let swapped = false;
            const swap = () => {
                if (swapped || token !== this._transitionToken) return;
                swapped = true;
                clearTimeout(this._swapTimer);
                const previous = this.visibleLayer;
                this.visibleLayer = layer;
                this.layers.forEach(l => l.el.classList.toggle('active-layer', l === layer));
                // Both clips play through the CSS crossfade, then only the new one keeps playing
                this.fadingOut = previous === layer ? null : previous;
                clearTimeout(this._fadeTimer);
                this._fadeTimer = setTimeout(() => {
                    if (token !== this._transitionToken) return;
                    this.fadingOut = null;
                    this._pauseInactive();
                }, this.timing.fadeMs);
                if (this.fallback) {
                    this._showFallbackFrame(layer.el.getAttribute('poster'));
                    this._exitFallbackIfHealthy();
                }
                this._render();
            };

            if (!this.visibleLayer || layer.el.readyState >= 2) {
                swap();
            } else {
                // Keep the current clip on screen until the incoming one has a frame to show
                layer.el.addEventListener('loadeddata', swap, { once: true });
                clearTimeout(this._swapTimer);
                this._swapTimer = setTimeout(swap, this.timing.swapTimeoutMs);
            }
        }

        _play(layer) {
            let result;
            try {
                result = layer.el.play();
            } catch (e) {
                result = Promise.reject(e);
            }
            return Promise.resolve(result).then(() => {
                layer.playFailures = 0;
            }, err => {
                // AbortError: a later pause()/load() superseded this call, not a failure.
                // A clip with a load error is handled by its error/retry path instead.
                if ((err && err.name === 'AbortError') || layer !== this.activeLayer || !this.wantPlaying) return;
                if (layer.status === 'error' || layer.status === 'failed') return;
                if (isAutoplayBlock(err)) {
                    this._recordError('autoplay blocked by the browser');
                    this._enterFallback('autoplay blocked, waiting for a click');
                    if (this.gate && !this._gateRequested) {
                        this._gateRequested = true;
                        this.gate.request(() => {
                            this._gateRequested = false;
                            this.play();
                        });
                    }
                    return;
                }
                layer.playFailures++;
                this.metrics.playFailures++;
                this._measure('play-failed', layer, { error: (err && err.name) || 'error' });
                this._recordError(layer.asset + ': play() failed (' + ((err && err.name) || 'error') + ')');
                if (layer.playFailures >= this.timing.playAttempts) this._enterFallback('playback keeps failing');
                this._render();
            });
        }

        _pauseInactive() {
            this.layers.forEach(layer => {
                if (layer !== this.activeLayer && layer !== this.fadingOut && !layer.el.paused) layer.el.pause();
            });
        }

        _reload(layer) {
            this.metrics.reloads++;
            this._measure('reload', layer);
            layer.status = 'loading';
            layer.buffering = false;
            layer.lastProgressAt = this.now();
            layer.el.preload = 'auto';
            // A fresh URL also gets past a bad cached response
            layer.el.src = this._srcFor(layer, true);
            if (layer === this.activeLayer && this.wantPlaying) this._play(layer);
            this._render();
        }

        _warmPoster(layer) {
            const poster = layer.el.getAttribute('poster');
            if (!poster || this._warmedPosters.has(poster)) return;
            this._warmedPosters.add(poster);
            this.doc.createElement('img').src = poster;
        }

        // ---- Media events ------------------------------------------------------

        _markLoaded(layer) {
            layer.status = 'loaded';
            layer.retries = 0;
            const waiters = layer.waiters;
            layer.waiters = [];
            waiters.forEach(resolve => resolve());
            this._render();
        }

        _onPlaying(layer) {
            if (layer !== this.activeLayer && layer !== this.fadingOut) {
                layer.el.pause(); // only the on-screen clip may play
                return;
            }
            layer.playFailures = 0;
            layer.stallStrikes = 0;
            layer.buffering = false;
            layer.lastProgressAt = this.now();
            if (layer === this.activeLayer) this._exitFallbackIfHealthy();
            this._render();
        }

        _noteProgress(layer) {
            const t = layer.el.currentTime;
            if (t === layer.lastTime) return;
            layer.lastTime = t;
            if (layer.el.paused) return; // seeking moves currentTime without playing
            layer.lastProgressAt = this.now();
            layer.buffering = false;
            layer.stallStrikes = 0;
            if (layer === this.activeLayer) this._exitFallbackIfHealthy();
        }

        _onBuffering(layer, kind) {
            layer.buffering = true;
            if (layer === this.activeLayer) {
                if (kind === 'waiting') this.metrics.waiting++;
                else this.metrics.stalledEvents++;
                this._measure(kind, layer);
            }
            this._render();
        }

        _onEnded(layer) {
            if (layer !== this.activeLayer || !this.wantPlaying) return;
            // `loop` normally prevents this; restart rather than freeze on the last frame
            this._log('clip ended, restarting:', layer.asset);
            layer.el.currentTime = 0;
            this._play(layer);
        }

        _onError(layer) {
            this._recordError(layer.asset + ': ' + describeMediaError(layer.el));
            clearTimeout(layer.retryTimer);
            if (layer.retries < this.timing.retryDelaysMs.length) {
                layer.status = 'error';
                const delay = this.timing.retryDelaysMs[layer.retries++];
                layer.retryTimer = setTimeout(() => this._reload(layer), delay);
            } else {
                layer.status = 'failed';
                const waiters = layer.waiters;
                layer.waiters = [];
                waiters.forEach(resolve => resolve());
                this._warnOnce('failed:' + layer.asset,
                    'Aadhi clip ' + fileName(layer.baseSrc) + ' could not be loaded; showing the animated fallback.');
            }
            if (layer === this.activeLayer) {
                this._enterFallback(layer.status === 'failed' ? 'clip failed to load' : 'clip error, retrying');
            }
            this._render();
        }

        // ---- Watchdog ----------------------------------------------------------

        _tick() {
            if (this.doc.hidden) return; // resumes on visibilitychange
            const active = this.activeLayer;
            this._pauseInactive();
            if (active && this.wantPlaying) {
                const el = active.el;
                if (active.status === 'failed') {
                    this._enterFallback('clip failed to load');
                } else if (el.paused) {
                    // Paused by the browser, or a start that failed: try again unless waiting for a click
                    if (!(this.gate && this.gate.isBlocked())) this._play(active);
                } else {
                    this._noteProgress(active);
                    const frozenFor = this.now() - active.lastProgressAt;
                    if (frozenFor >= this.timing.stallMs) {
                        // Frozen while "playing": fallback at once, reload if it stays stuck
                        this.metrics.stalls++;
                        this._measure('stall', active, { frozenMs: Math.round(frozenFor), hidden: !!this.doc.hidden });
                        active.stallStrikes++;
                        active.lastProgressAt = this.now();
                        this._recordError(active.asset + ': no playback progress for ' + Math.round(frozenFor / 1000) + 's');
                        this._enterFallback('clip stalled');
                        if (active.stallStrikes % 2 === 0) this._reload(active);
                    }
                }
            }
            this._render();
        }

        // ---- Fallback ----------------------------------------------------------

        _buildOverlay() {
            const make = (tag, className) => {
                const el = this.doc.createElement(tag);
                el.className = className;
                el.setAttribute('data-mascot-runtime', '');
                el.setAttribute('aria-hidden', 'true');
                return el;
            };
            // Two frames so fallback posters crossfade when Aadhi changes placement
            this.fallbackEl = make('div', 'mascot-fallback');
            this.frames = [make('img', 'mascot-fallback-frame'), make('img', 'mascot-fallback-frame')];
            this.frames.forEach(img => {
                img.alt = '';
                img.addEventListener('error', () => this._onFrameError(img));
                this.fallbackEl.appendChild(img);
            });
            this.cueEl = make('div', 'mascot-cue');
            this.cueEl.innerHTML = '<span class="mascot-cue-bubble"><i></i><i></i><i></i></span>';
            this.container.appendChild(this.fallbackEl);
            this.container.appendChild(this.cueEl);
        }

        _enterFallback(reason) {
            if (!this.fallback) {
                this._log('fallback on:', reason);
                this.metrics.fallbacks++;
                this.metrics.fallbackReasons[reason] = (this.metrics.fallbackReasons[reason] || 0) + 1;
                this._fallbackSince = this.now();
                this._measure('fallback', this.activeLayer, { reason });
            }
            this.fallback = { reason };
            const layer = this.activeLayer;
            this._showFallbackFrame(layer ? layer.el.getAttribute('poster') : null);
            this._render();
        }

        _exitFallbackIfHealthy() {
            const layer = this.activeLayer;
            if (!this.fallback || !layer) return;
            if (layer.el.paused || layer.status === 'failed' || layer.el.readyState < 2) return;
            this._log('fallback off');
            this.fallback = null;
            if (this._fallbackSince !== null) {
                this.metrics.fallbackMs += this.now() - this._fallbackSince;
                this._measure('recovered', layer, { afterMs: this.now() - this._fallbackSince });
                this._fallbackSince = null;
            }
            this._render();
        }

        _showFallbackFrame(src) {
            const target = src || this.fallbackStill;
            const current = this.frames.find(img => img.classList.contains('is-current'));
            if (current && current.getAttribute('data-for') === target) return;
            const next = current === this.frames[0] ? this.frames[1] : this.frames[0];
            next.setAttribute('data-for', target);
            next.setAttribute('src', target);
            next.classList.add('is-current');
            if (current) current.classList.remove('is-current');
        }

        _onFrameError(img) {
            // Last resort: the empty studio still, so the mascot area never goes black
            if (this.fallbackStill && img.getAttribute('src') !== this.fallbackStill) {
                this._recordError('poster missing: ' + fileName(img.getAttribute('src')));
                img.setAttribute('src', this.fallbackStill);
            }
        }

        // ---- Rendering ---------------------------------------------------------

        _applyFraming() {
            const framing = FRAMING[this.activeAsset] || FRAMING.none;
            const style = this.container.style;
            style.setProperty('--mascot-pivot-x', framing.pivot[0] * 100 + '%');
            style.setProperty('--mascot-pivot-y', framing.pivot[1] * 100 + '%');
            if (!framing.cue) return;
            const width = this.container.clientWidth || (this.view ? this.view.innerWidth : 0);
            const height = this.container.clientHeight || (this.view ? this.view.innerHeight : 0);
            if (!width || !height) return;
            const point = coverPoint(framing.cue[0], framing.cue[1], width, height);
            const margin = 40;
            style.setProperty('--mascot-cue-x', Math.round(Math.min(Math.max(point.x, margin), width - margin)) + 'px');
            style.setProperty('--mascot-cue-y', Math.round(Math.min(Math.max(point.y, margin), height - margin)) + 'px');
        }

        _render() {
            const c = this.container;
            c.setAttribute('data-state', this.state);
            c.setAttribute('data-asset', this.activeAsset || '');
            c.classList.toggle('mascot-fallback-active', !!this.fallback);
            c.classList.toggle('mascot-paused', !this.wantPlaying);

            let cue = '';
            const framing = FRAMING[this.activeAsset];
            if (this.cuesEnabled && framing && framing.cue) {
                if (CUE_STATES.includes(this.state)) cue = this.state;
                else if (this.fallback && SPEAKING_STATES.includes(this.state)) cue = 'talking';
            }
            if (this.cueEl && this.cueEl.getAttribute('data-cue') !== cue) this.cueEl.setAttribute('data-cue', cue);

            if (this.hud) this._renderHud();
        }

        _renderHud() {
            const s = this.getStatus();
            const upper = v => String(v).toUpperCase();
            const t = s.lastTransition;
            const ago = at => ((this.now() - at) / 1000).toFixed(1) + 's ago';
            this.hud.textContent = [
                'Mascot',
                'State: ' + upper(s.state),
                'Placement: ' + s.placement,
                'Asset: ' + s.asset,
                'Playback: ' + s.playback,
                'Loaded: ' + Object.keys(s.assets).map(k => k + ' ' + s.assets[k]).join(', '),
                'Fallback: ' + (s.fallback ? 'YES (' + s.fallback + ')' : 'NO'),
                'Audio: ' + s.audio + (s.gestureRequired ? ' (needs a click)' : ''),
                'Last transition: ' + (t ? upper(t.from) + ' → ' + upper(t.to) + ' (' + ago(t.at) + ')' : '—'),
                'Last error: ' + (this.lastError ? this.lastError.message + ' (' + ago(this.lastError.at) + ')' : '—')
            ].join('\n');
            this.hud.classList.toggle('is-recording', this.recording);
        }

        _playbackLabel() {
            const layer = this.activeLayer;
            if (!layer) return 'NONE';
            if (!this.wantPlaying) return 'PAUSED';
            if (layer.status === 'failed') return 'FAILED';
            if (layer.el.paused) return 'STARTING';
            if (layer.buffering) return 'BUFFERING';
            return 'PLAYING';
        }

        _recordError(message) {
            this.lastError = { message, at: this.now() };
            this._log(message);
        }

        _warnOnce(key, message) {
            if (this._warned.has(key)) return;
            this._warned.add(key);
            console.warn('[Aadhi mascot] ' + message);
        }

        _log(...args) {
            if (this.debug) console.log('[Aadhi mascot]', ...args);
        }
    }

    return {
        STATES,
        PLACEMENTS,
        TIMING,
        MascotController,
        PlaybackGate,
        resolvePlacement,
        requiredAssetKeys,
        normalizePlacement,
        coverPoint,
        isAutoplayBlock
    };
});
