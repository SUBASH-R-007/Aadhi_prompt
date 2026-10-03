/*
 * AI Presenter / AI Teacher (Phase 12) in the page: presenter settings, the presenter layer that plays with
 * the lesson, the illustrated Aadhi Teacher, and the presenter's place in Visual Review.
 *
 * The server's Presenter Director (presenters.py) plans each scene's presenter (scene.presenter_plan): whether
 * it appears, where (a layout the board already makes room for, never over the content), what it does. Here:
 *   Aadhi (mascot)      MascotController plays it, as before (mascot.js) - nothing changes for existing lessons
 *   Aadhi Teacher       drawn as SVG: expressions, gestures, blinking, breathing; its mouth follows the
 *                       narration's loudness (the speech timeline's envelope), in time with the playing audio.
 *                       Not phoneme lip sync, and never called that; with the browser's own speech (no audio
 *                       file) it falls back to a plain talking animation. It can also act at a moment of the
 *                       narration (Phase 16 sync plan: PresenterStage.act), its gesture / expression redrawn in place.
 *   AI presenter clip   a generated clip (muted) kept in time with the narration audio it was made from
 * The narration stays the lesson's own audio: the presenter never adds a second voice.
 *
 * Loaded as a classic <script> (window.AadhiPresenter) and as a CommonJS module by tests/presenter.test.js.
 */
(function (root, factory) {
    if (typeof module === 'object' && module.exports) module.exports = factory();
    else root.AadhiPresenter = factory();
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    const STORAGE_KEY = 'aadhi.presenter';
    const DEFAULTS = { presenter_id: 'aadhi', mode: 'lesson', position: 'right', style: 'friendly', expression: null, gesture: null,
        provider: null, background: 'scene', fallback: 'none' };
    const MODES = { lesson: 'As the lesson says', auto: 'Automatic (where it helps)', always: 'Always', off: 'Off' };
    const STYLES = { friendly: 'Friendly', energetic: 'Energetic', calm: 'Calm', formal: 'Formal' };
    const EXPRESSIONS = ['neutral', 'friendly', 'engaged', 'thinking', 'surprised', 'happy', 'encouraging', 'serious'];
    const GESTURES = ['none', 'open_hand', 'point', 'counting', 'explaining', 'emphasis', 'thinking', 'welcome'];
    // The same boxes as presenters.py (normalized to the 16:9 stage); the board layouts make room for them
    const BOXES = {
        left: { x: 0.02, y: 0.14, w: 0.26, h: 0.72 }, right: { x: 0.72, y: 0.14, w: 0.26, h: 0.72 },
        center: { x: 0.28, y: 0.14, w: 0.25, h: 0.70 },
        pip_left: { x: 0.05, y: 0.52, w: 0.18, h: 0.34 }, pip_right: { x: 0.77, y: 0.52, w: 0.18, h: 0.34 }
    };

    function loadSettings(storage) {
        try {
            const saved = JSON.parse((storage && storage.getItem(STORAGE_KEY)) || '{}');
            return { ...DEFAULTS, ...(saved && typeof saved === 'object' ? saved : {}) };
        } catch (e) {
            return { ...DEFAULTS };
        }
    }

    function saveSettings(storage, settings) {
        try { storage.setItem(STORAGE_KEY, JSON.stringify(settings)); } catch (e) { /* private mode: the defaults stay */ }
    }

    // The legacy path: Aadhi placed by the screenplay (aadhi_position) - existing lessons look exactly as before
    function isLegacy(settings) {
        return (settings.presenter_id || 'aadhi') === 'aadhi' && (settings.mode || 'lesson') === 'lesson';
    }

    function boxFor(plan) {
        if (!plan || !plan.enabled) return null;
        return plan.box || BOXES[plan.placement === 'pip' && (plan.layout === 'left' || plan.layout === 'right') ? `pip_${plan.layout}` : plan.layout] || null;
    }

    // Which clip and layout the page uses for a scene: null = the legacy mascot path
    function effective(slide, settings) {
        const plan = slide && slide.presenter_plan;
        if (isLegacy(settings) || !plan || plan.presenter_id !== settings.presenter_id) return null;
        return plan;
    }

    // Plans from the server applied to the scenes; a clip made earlier for the same presenter is kept
    function applyPlans(slides, plans) {
        (plans || []).forEach((plan, i) => {
            const scene = slides[i];
            if (!scene || !plan) return;
            const before = scene.presenter_plan;
            if (!plan.media && before && before.media && before.presenter_id === plan.presenter_id && plan.review_status !== 'removed') {
                plan.media = before.media;
            }
            scene.presenter_plan = plan;
        });
        return slides.map(s => s && s.presenter_plan);
    }

    // ---- the illustrated Aadhi Teacher -------------------------------------------------------------------------

    const FACE = {
        neutral: { smile: 0, brow: 0, browTilt: 0, tilt: 0 },
        friendly: { smile: 5, brow: 0, browTilt: 0, tilt: 0 },
        engaged: { smile: 6, brow: -2, browTilt: 0, tilt: 0 },
        thinking: { smile: -1, brow: -3, browTilt: 8, tilt: -3, look: -2 },
        surprised: { smile: 0, brow: -6, browTilt: 0, tilt: 0, open: 0.55 },
        happy: { smile: 9, brow: -1, browTilt: 0, tilt: 0, squint: true },
        encouraging: { smile: 7, brow: -1, browTilt: 0, tilt: 4 },
        serious: { smile: -2, brow: 2, browTilt: -6, tilt: 0 }
    };
    // Arm angles (degrees, around the shoulder; 0 = hanging down) for [gesturing arm, other arm]
    const POSE = {
        none: [8, -8], open_hand: [62, -8], point: [84, -10], counting: [150, -8], explaining: [40, -40],
        emphasis: [165, -8], thinking: [8, 150], welcome: [70, -70]
    };

    // The drawing is markup (inserted as HTML by the stage, the settings preview and Visual Review): every value that comes
    // from a profile, the settings or a plan (a custom presenter's name, its colours, an expression) is attribute-escaped here
    const own = (table, key) => typeof key === 'string' && Object.prototype.hasOwnProperty.call(table, key);
    const attr = value => String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');

    // The drawing's parts. The ones an expression or gesture changes are built separately, so the stage can redraw just
    // them in place mid-scene (PresenterStage.act) inside the same SVG.
    function teacherColors(profile) {
        const pal = (profile && profile.appearance && profile.appearance.palette) || ['#5B2A86', '#FFD700', '#C98B62', '#2B1B14'];
        return { blazer: attr(pal[0]), accent: attr(pal[1]), skin: attr(pal[2] || '#C98B62'), hair: attr(pal[3] || '#2B1B14') };
    }

    function teacherArms(gesture, { blazer, skin }) {
        const [armA, armB] = own(POSE, gesture) ? POSE[gesture] : POSE.none;
        const hand = g => g === 'counting'
            ? '<rect x="-6" y="48" width="3" height="10" rx="1.5"/><rect x="-1.5" y="46" width="3" height="12" rx="1.5"/><rect x="3" y="48" width="3" height="10" rx="1.5"/>'
            : g === 'point' ? '<rect x="-1.5" y="50" width="3" height="14" rx="1.5"/>' : '';
        const arm = (angle, x, g) => `<g class="arm" transform="translate(${x} 200) rotate(${angle})">`
            + `<rect x="-9" y="0" width="18" height="58" rx="9" fill="${blazer}"/><circle cx="0" cy="${g === 'emphasis' ? 56 : 60}" r="${g === 'open_hand' || g === 'welcome' ? 10 : 8}" fill="${skin}"/>`
            + `<g fill="${skin}">${hand(g)}</g></g>`;
        return arm(armA, 56, gesture) + arm(armB, 144, gesture === 'explaining' || gesture === 'welcome' ? gesture : (gesture === 'thinking' ? 'thinking' : 'none'));
    }

    const teacherHead = face => `translate(0 14) rotate(${face.tilt} 100 90)`;
    const teacherEyes = face => `<ellipse cx="86" cy="${86 + (face.look || 0)}" rx="4" ry="${face.squint ? 2.2 : 4.2}" fill="#221"/>`
        + `<ellipse cx="114" cy="${86 + (face.look || 0)}" rx="4" ry="${face.squint ? 2.2 : 4.2}" fill="#221"/>`;
    const teacherBrows = face => `<line x1="79" y1="${70 + face.brow + face.browTilt * 0.3}" x2="93" y2="${70 + face.brow - face.browTilt * 0.3}"/>`
        + `<line x1="107" y1="${70 + face.brow}" x2="121" y2="${70 + face.brow + face.browTilt * 0.2}"/>`;
    const teacherMouth = face => `M86 ${112 - face.smile * 0.2} Q100 ${112 + face.smile} 114 ${112 - face.smile * 0.2}`;
    const teacherLabel = (profile, expression, gesture) => `${(profile && profile.name) || 'Teacher'}, ${expression}, ${String(gesture).replace('_', ' ')}`;

    function teacherSvg(profile, { expression = 'engaged', gesture = 'none', mirror = false, id = 'teacher' } = {}) {
        const colors = teacherColors(profile);
        const { blazer, accent, skin, hair } = colors;
        const face = own(FACE, expression) ? FACE[expression] : FACE.engaged;
        return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="-24 0 248 360" class="teacher-svg" data-expression="${attr(expression)}" data-gesture="${attr(gesture)}" role="img" aria-label="${attr(teacherLabel(profile, expression, gesture))}">`
            + `<g transform="${mirror ? 'translate(200 0) scale(-1 1)' : ''}"><g class="teacher-body">`
            + `<path d="M40 360 L52 196 Q100 168 148 196 L160 360 Z" fill="${blazer}"/>`
            + `<path d="M84 180 L100 214 L116 180 Z" fill="${accent}"/><rect x="89" y="118" width="22" height="66" rx="9" fill="${skin}"/>`
            + `<g class="teacher-arms">${teacherArms(gesture, colors)}</g>`
            + `<g class="teacher-head" transform="${teacherHead(face)}">`
            + `<ellipse cx="62" cy="86" rx="6" ry="9" fill="${skin}"/><ellipse cx="138" cy="86" rx="6" ry="9" fill="${skin}"/>`
            + `<ellipse cx="100" cy="86" rx="38" ry="46" fill="${skin}"/>`
            + `<path d="M62 78 Q60 38 100 36 Q142 36 139 80 Q128 56 100 56 Q76 56 62 78 Z" fill="${hair}"/>`
            + `<g class="teacher-eyes">${teacherEyes(face)}</g>`
            + `<g class="teacher-brows" stroke="${hair}" stroke-width="3" stroke-linecap="round">${teacherBrows(face)}</g>`
            + `<path class="teacher-mouth-closed" d="${teacherMouth(face)}" stroke="#6b2323" stroke-width="3" fill="none" stroke-linecap="round"/>`
            + `<ellipse class="teacher-mouth-open" cx="100" cy="114" rx="9" ry="${face.open ? 6 : 0.5}" fill="#4a1414" opacity="${face.open ? 1 : 0}"/>`
            + '</g></g></g></svg>';
    }

    // Mouth openness 0..1 for a moment of the narration (the envelope of the scene's speech timeline)
    function mouthLevel(timeline, segIndex, mediaTime) {
        if (!timeline || !timeline.segments || !timeline.segments[segIndex]) return null;
        const seg = timeline.segments[segIndex];
        const i = seg.envelope_start + Math.floor(Math.max(0, mediaTime) * (timeline.fps || 25));
        const end = seg.envelope_start + Math.round(seg.duration * (timeline.fps || 25));
        if (i >= end) return 0;
        return timeline.envelope[i] || 0;
    }

    // ---- API --------------------------------------------------------------------------------------------------

    class PresenterApi {
        constructor({ fetch }) { this.fetch = fetch; this.speechCache = {}; }

        async call(url, init = {}) {
            const res = await this.fetch(url, { ...init, headers: { ...(init.body ? { 'Content-Type': 'application/json' } : {}), ...(init.headers || {}) } });
            const data = await res.json().catch(() => ({}));
            if (!res.ok && res.status !== 202) {
                throw Object.assign(new Error(typeof data.detail === 'string' ? data.detail : `error ${res.status}`), { status: res.status });
            }
            return Object.assign(data || {}, { httpStatus: res.status });
        }

        profiles() { return this.call('/api/presenters'); }
        plan(scenes, settings) { return this.call('/api/presenters/plan', { method: 'POST', body: JSON.stringify({ scenes, settings }) }); }
        speech(text, voice, engine) {
            const key = `${voice}|${engine}|${text}`;
            if (!this.speechCache[key]) {
                this.speechCache[key] = this.call('/api/presenters/speech', { method: 'POST', body: JSON.stringify({ text, voice, tts_engine: engine }) })
                    .catch(e => { delete this.speechCache[key]; throw e; });
            }
            return this.speechCache[key];
        }
        generate(body) { return this.call('/api/presenters/generate', { method: 'POST', body: JSON.stringify(body) }); }
        review(body) { return this.call('/api/presenters/review', { method: 'POST', body: JSON.stringify(body) }); }
    }

    // ---- the presenter layer during the lesson ----------------------------------------------------------------

    class PresenterStage {
        // deps: doc, api (PresenterApi), settings() -> current settings, profile(id) -> profile, voice() -> {voice, engine},
        // mediaUrl(url), raf(fn), now()
        constructor({ doc, api, settings, profile, voice, mediaUrl = u => u, raf = null, now = () => Date.now() }) {
            this.doc = doc;
            this.api = api;
            this.settings = settings;
            this.profile = profile;
            this.voice = voice;
            this.mediaUrl = mediaUrl;
            this.raf = raf || (fn => setTimeout(fn, 40));
            this.now = now;
            this.layer = null;
            this.plan = null;
            this.timeline = null;
            this.audio = null;
            this.segIndex = 0;
            this.speaking = false;
            this.generic = false;
            this.token = 0;
            this.figure = null; // the drawn teacher on stage: what it shows now, what it may show (acting, Phase 16)
            this.stats = { frames: 0, envelopeFrames: 0, genericFrames: 0, scenes: 0, acts: 0 };
        }

        ensureLayer() {
            if (this.layer) return this.layer;
            this.layer = this.doc.createElement('div');
            this.layer.id = 'presenter-layer';
            this.layer.className = 'presenter-layer';
            this.layer.setAttribute('aria-hidden', 'true');
            this.doc.body.appendChild(this.layer);
            return this.layer;
        }

        // The plan the page uses for a scene (null: the legacy mascot path)
        planFor(slide) { return effective(slide, this.settings()); }

        // Uses the illustrated teacher or an AI clip (both follow the lesson's narration audio)
        wantsServerAudio() {
            return !!(this.plan && this.plan.enabled && (this.kind === 'illustrated' || this.kind === 'video'));
        }

        beginScene(slide, plan, index = null) {
            this.endScene();
            const token = ++this.token;
            this.ensureLayer().setAttribute('data-scene', index === null ? '' : String(index));
            this.plan = plan && plan.enabled && plan.type !== 'mascot' ? plan : null;
            const layer = this.ensureLayer();
            layer.textContent = '';
            layer.className = 'presenter-layer';
            this.kind = null;
            if (!this.plan) {
                layer.setAttribute('data-state', 'hidden');
                return null;
            }
            const box = boxFor(this.plan);
            Object.assign(layer.style, { left: `${box.x * 100}%`, top: `${box.y * 100}%`, width: `${box.w * 100}%`, height: `${box.h * 100}%` });
            layer.setAttribute('data-layout', this.plan.placement === 'pip' ? `pip_${this.plan.layout}` : this.plan.layout);
            layer.setAttribute('data-presenter', this.plan.presenter_id);
            const profile = this.profile(this.plan.presenter_id) || { name: 'Presenter' };
            const media = this.plan.media;
            if (this.plan.type === 'illustrated') {
                this.kind = 'illustrated';
                const look = { expression: this.plan.expression_shown || this.plan.expression, gesture: this.plan.gesture_shown || this.plan.gesture };
                layer.innerHTML = teacherSvg(profile, { ...look, mirror: this.plan.layout === 'left' });
                this.mouthOpen = layer.querySelector('.teacher-mouth-open');
                this.mouthClosed = layer.querySelector('.teacher-mouth-closed');
                this.drawn(profile, look);
            } else if (media && media.url && media.kind === 'video') {
                this.kind = 'video';
                const video = this.doc.createElement('video');
                video.className = 'presenter-video';
                video.muted = true;
                video.setAttribute('muted', '');
                video.setAttribute('playsinline', '');
                video.preload = 'auto';
                video.src = this.mediaUrl(media.url);
                layer.appendChild(video);
                this.video = video;
            } else if (media && media.url && media.kind === 'image') {
                this.kind = 'image';
                const img = this.doc.createElement('img');
                img.className = 'presenter-image';
                img.alt = profile.name;
                img.src = this.mediaUrl(media.url);
                layer.appendChild(img);
            } else if (this.settings().fallback === 'aadhi-teacher') {
                this.kind = 'illustrated'; // no clip yet: the user allowed the illustrated teacher to stand in
                const drawnProfile = this.profile('aadhi-teacher') || profile;
                layer.innerHTML = teacherSvg(drawnProfile, { expression: this.plan.expression, gesture: this.plan.gesture,
                    mirror: this.plan.layout === 'left' });
                this.mouthOpen = layer.querySelector('.teacher-mouth-open');
                this.mouthClosed = layer.querySelector('.teacher-mouth-closed');
                this.drawn(drawnProfile, { expression: this.plan.expression, gesture: this.plan.gesture });
                layer.setAttribute('data-fallback', 'aadhi-teacher');
            } else {
                layer.setAttribute('data-state', 'missing'); // an AI presenter without a clip: nothing is faked
                this.plan = null;
                return null;
            }
            layer.classList.add('presenter-' + this.kind);
            layer.setAttribute('data-state', 'idle');
            this.stats.scenes += 1;
            if ((this.kind === 'illustrated' || this.kind === 'video') && slide && slide.narration) {
                const { voice, engine } = this.voice();
                this.api.speech(String(slide.narration), voice, engine).then(t => { if (token === this.token) this.timeline = t; }).catch(() => {});
            }
            return this.plan;
        }

        setMouth(level) {
            if (!this.mouthOpen) return;
            const open = level > 0.08;
            this.mouthOpen.setAttribute('ry', String(open ? 1 + level * 9 : 0.5));
            this.mouthOpen.setAttribute('opacity', open ? '1' : '0');
            if (this.mouthClosed) this.mouthClosed.setAttribute('opacity', open ? '0' : '1');
        }

        // The drawn teacher just put on stage: what it shows, and what its profile lets it show (its parts are looked up
        // only when it first acts, so a scene without acting costs nothing more than before)
        drawn(profile, { expression, gesture }) {
            const caps = (profile && profile.capabilities) || {};
            this.figure = { profile, colors: teacherColors(profile), parts: null,
                expression: own(FACE, expression) ? expression : 'engaged', gesture: own(POSE, gesture) ? gesture : 'none',
                expressions: Array.isArray(caps.expressions) ? caps.expressions : null, gestures: Array.isArray(caps.gestures) ? caps.gestures : null };
        }

        // Acting at a moment (Phase 16 synchronization): params = { gesture, expression } from the scene's sync plan (the
        // server already checked them against the presenter's capabilities). The drawn teacher changes its gesture and/or
        // expression IN PLACE: only the arms and the face's features (eyes, brows, the resting mouth line, the head's
        // tilt) are redrawn inside the same SVG, so the mouth that follows the narration, the entrance / highlight
        // animations on the figure, breathing, blinking and the layer's box keep going. A value outside the drawing's
        // vocabulary or the profile's capabilities is ignored. Anything other than the drawn teacher (Aadhi, whose
        // MascotController plays him; an AI clip, pre-rendered) does not act here. True when something changed.
        act(params) {
            const figure = this.kind === 'illustrated' ? this.figure : null;
            if (!figure || !params || typeof params !== 'object') return false;
            const pick = (value, table, allowed, current) => (typeof value === 'string' && Object.prototype.hasOwnProperty.call(table, value)
                && (!allowed || allowed.includes(value)) ? value : current);
            const gesture = pick(params.gesture, POSE, figure.gestures, figure.gesture);
            const expression = pick(params.expression, FACE, figure.expressions, figure.expression);
            if (gesture === figure.gesture && expression === figure.expression) return false;
            const layer = this.layer;
            const parts = figure.parts || (figure.parts = {
                svg: layer.querySelector('.teacher-svg'), arms: layer.querySelector('.teacher-arms'), head: layer.querySelector('.teacher-head'),
                eyes: layer.querySelector('.teacher-eyes'), brows: layer.querySelector('.teacher-brows') });
            if (!parts.svg || !parts.arms || !parts.head || !parts.eyes || !parts.brows) return false;
            if (gesture !== figure.gesture) parts.arms.innerHTML = teacherArms(gesture, figure.colors);
            if (expression !== figure.expression) {
                const face = FACE[expression];
                parts.head.setAttribute('transform', teacherHead(face));
                parts.eyes.innerHTML = teacherEyes(face); // the blinking group itself stays
                parts.brows.innerHTML = teacherBrows(face);
                if (this.mouthClosed) this.mouthClosed.setAttribute('d', teacherMouth(face));
                if (!this.speaking && this.mouthOpen) { // at rest the face's own mouth; while speaking the narration drives it
                    this.mouthOpen.setAttribute('ry', String(face.open ? 6 : 0.5));
                    this.mouthOpen.setAttribute('opacity', face.open ? '1' : '0');
                    if (this.mouthClosed) this.mouthClosed.setAttribute('opacity', '1');
                }
            }
            figure.gesture = gesture;
            figure.expression = expression;
            parts.svg.setAttribute('data-gesture', gesture);
            parts.svg.setAttribute('data-expression', expression);
            parts.svg.setAttribute('aria-label', teacherLabel(figure.profile, expression, gesture));
            this.stats.acts += 1;
            return true;
        }

        loop() {
            const token = this.token;
            const tick = () => {
                if (token !== this.token || !this.speaking) return;
                this.stats.frames += 1;
                let level = null;
                if (this.audio && this.timeline) level = mouthLevel(this.timeline, this.segIndex, this.audio.currentTime);
                if (level === null && this.generic) {
                    const t = this.now() / 1000;
                    level = Math.max(0, Math.sin(t * 13) * 0.5 + Math.sin(t * 7.3) * 0.3);
                    this.stats.genericFrames += 1;
                } else if (level !== null) {
                    this.stats.envelopeFrames += 1;
                }
                if (level !== null) this.setMouth(level);
                if (this.video && this.audio && this.timeline && this.timeline.segments[this.segIndex]) {
                    const target = this.timeline.segments[this.segIndex].start + this.audio.currentTime;
                    if (Math.abs(this.video.currentTime - target) > 0.25) this.video.currentTime = target;
                }
                this.raf(tick);
            };
            this.raf(tick);
        }

        // A segment's narration audio (server TTS): the mouth / clip follow it
        attachAudio(segIndex, audio) {
            if (!this.plan) return;
            const start = () => {
                this.audio = audio;
                this.segIndex = segIndex;
                this.generic = !this.timeline;
                if (this.layer) this.layer.setAttribute('data-state', 'speaking');
                if (this.video) {
                    this.video.playbackRate = audio.playbackRate || 1;
                    if (this.timeline && this.timeline.segments[segIndex]) this.video.currentTime = this.timeline.segments[segIndex].start + audio.currentTime;
                    const p = this.video.play();
                    if (p && p.catch) p.catch(() => {});
                }
                if (!this.speaking) { this.speaking = true; this.loop(); }
            };
            audio.addEventListener('playing', start);
            audio.addEventListener('pause', () => { if (!audio.ended) this.hold(); });
            audio.addEventListener('ended', () => { this.speaking = false; this.setMouth(0); if (this.layer) this.layer.setAttribute('data-state', 'listening'); });
        }

        // The browser's own speech (no audio file): a plain talking animation
        narrationEvent(kind) {
            if (!this.plan) return;
            if (kind === 'segment' || kind === 'resume') {
                if (this.audio) return;
                this.generic = true;
                if (this.layer) this.layer.setAttribute('data-state', 'speaking');
                if (!this.speaking) { this.speaking = true; this.loop(); }
            } else if (kind === 'hold') {
                this.hold();
            } else if (kind === 'end') {
                this.speaking = false;
                this.audio = null;
                this.setMouth(0);
                if (this.video) this.video.pause();
                if (this.layer) this.layer.setAttribute('data-state', 'idle');
            }
        }

        hold() {
            this.speaking = false;
            this.setMouth(0);
            if (this.video) this.video.pause();
            if (this.layer) this.layer.setAttribute('data-state', 'listening');
        }

        endScene() {
            this.token++;
            this.speaking = false;
            this.audio = null;
            this.timeline = null;
            if (this.video) { this.video.pause(); this.video.removeAttribute('src'); this.video = null; }
            this.mouthOpen = this.mouthClosed = null;
            this.figure = null;
            if (this.layer) { this.layer.textContent = ''; this.layer.setAttribute('data-state', 'hidden'); }
            this.plan = null;
            this.kind = null;
        }
    }

    // ---- settings panel (start screen) --------------------------------------------------------------------------

    function el(doc, tag, attrs = {}, ...children) {
        const node = doc.createElement(tag);
        for (const [k, v] of Object.entries(attrs || {})) {
            if (v === undefined || v === null || v === false) continue;
            if (k === 'text') node.textContent = v;
            else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
            else if (k === 'value') node.value = v;
            else if (k === 'selected') node.selected = !!v;
            else node.setAttribute(k, v === true ? '' : v);
        }
        children.flat().forEach(c => { if (c !== null && c !== undefined && c !== false) node.appendChild(typeof c === 'string' ? doc.createTextNode(c) : c); });
        return node;
    }

    // Phase 21: the presenter's acting and place in plain words (the codes are presenters.py's; an unknown one is shown as it is)
    const BEHAVIOR_WORDS = { idle: 'Waiting', talking: 'Talking', explaining: 'Explaining', thinking: 'Thinking', listening: 'Listening',
        welcoming: 'Welcoming the learners', concluding: 'Wrapping up' };
    const EXPRESSION_WORDS = { neutral: 'calm face', friendly: 'friendly', engaged: 'engaged', thinking: 'thoughtful', surprised: 'surprised',
        happy: 'happy', encouraging: 'encouraging', serious: 'serious' };
    const GESTURE_WORDS = { none: 'no gesture', open_hand: 'open hand', point: 'pointing', counting: 'counting on fingers',
        explaining: 'explaining with both hands', emphasis: 'stressing a point', thinking: 'hand on chin', welcome: 'welcoming gesture' };
    const POSITION_WORDS = { left: 'On the left', right: 'On the right', center: 'In the centre' };
    const words = (table, code) => (own(table, code) ? table[code] : String(code || '').replace(/_/g, ' '));
    const capital = text => (text ? text.charAt(0).toUpperCase() + text.slice(1) : text);
    // ?visualDebug (a boolean, or a function asked each time): technical details such as the provider are shown
    const debugOn = debug => !!(typeof debug === 'function' ? debug() : debug);

    // What a presenter can do, in words. The provider that makes its clips is a technical detail: shown in debug only.
    function providerText(view, { debug = false } = {}) {
        if (!view) return '';
        const caps = view.capabilities || {};
        const speech = caps.speech === 'audio_envelope' ? "mouth follows the narration's loudness"
            : caps.speech === 'lip_sync' ? 'lip-synced to the narration' : caps.speech === 'talking_animation' ? 'talking animation' : '';
        const parts = [speech];
        if (caps.expressions) parts.push(`${caps.expressions.length} expressions`);
        if (caps.gestures && caps.gestures.length) parts.push(`${caps.gestures.length} gestures`);
        if (caps.provider && debugOn(debug)) parts.push(`made by ${caps.provider}`);
        return parts.filter(Boolean).join(' · ');
    }

    class PresenterSettingsPanel {
        // deps: doc, container, api, storage, onChange(settings), mascotPoster (url of Aadhi's still)
        // Phase 21: debug (boolean or () => boolean; the page's ?visualDebug): technical details such as the provider are shown
        constructor({ doc, container, api, storage, onChange = () => {}, mascotPoster = null, debug = false }) {
            this.doc = doc;
            this.container = container;
            this.api = api;
            this.storage = storage;
            this.onChange = onChange;
            this.mascotPoster = mascotPoster;
            this.debug = debug;
            this.settings = loadSettings(storage);
            this.data = null;
            this.previewStatus = '';
        }

        profile(id) { return this.data && this.data.profiles.find(p => p.id === id); }

        async load() {
            try {
                this.data = await this.api.profiles();
            } catch (e) {
                this.data = { profiles: [], providers: { providers: [], available: false, message: 'Presenters could not be loaded.' } };
            }
            if (!this.profile(this.settings.presenter_id) || !this.profile(this.settings.presenter_id).available) this.settings.presenter_id = 'aadhi';
            this.render();
            return this.data;
        }

        set(key, value) {
            this.settings[key] = value === '' ? null : value;
            saveSettings(this.storage, this.settings);
            this.render();
            this.onChange(this.settings);
        }

        render() {
            const h = (...a) => el(this.doc, ...a);
            const s = this.settings;
            const d = this.data || { profiles: [], providers: {} };
            this.container.textContent = '';
            const select = (id, label, key, options, extra = {}) => h('div', { class: 'presenter-field' },
                h('label', { class: 'config-label', for: id, text: label }),
                h('select', { id, class: 'neon-input ui-focusable', onchange: e => this.set(key, e.target.value), ...extra },
                    options.map(([value, text, disabled]) => h('option', { value, text, selected: String(s[key] || '') === String(value), disabled }))));
            const current = this.profile(s.presenter_id);
            this.container.appendChild(h('div', { class: 'presenter-settings' },
                h('div', { class: 'presenter-row' },
                    select('presenter-select', 'Presenter', 'presenter_id', d.profiles.map(p => [p.id, p.available ? p.name : `${p.name} (not available)`, !p.available])),
                    select('presenter-mode', 'Show presenter', 'mode', Object.entries(MODES))),
                h('div', { class: 'presenter-row' },
                    select('presenter-position', 'Position', 'position', [['right', 'Right'], ['left', 'Left']]),
                    select('presenter-style', 'Teaching style', 'style', Object.entries(STYLES))),
                this.renderPreview(current),
                h('details', { class: 'presenter-advanced' }, h('summary', { text: 'Advanced presenter settings' }),
                    select('presenter-provider', 'Presenter provider', 'provider', [['', 'Automatic']].concat(((d.providers || {}).providers || [])
                        .map(p => [p.name, `${p.label}${p.state === 'available' ? '' : ' (unavailable)'}`, p.state !== 'available']))),
                    h('p', { class: 'presenter-note', text: (d.providers || {}).message || 'Only what the provider really supports is offered.' }),
                    ((d.providers || {}).not_presenter_capable || []).length ? h('p', { class: 'presenter-note',
                        text: `Not presenter-capable: ${d.providers.not_presenter_capable.map(p => p.label).join(', ')} (they make video from text or pictures, not from the lesson's narration).` }) : null,
                    select('presenter-expression', 'Expression', 'expression', [['', 'Automatic']].concat(EXPRESSIONS.map(e => [e, capital(words(EXPRESSION_WORDS, e))]))),
                    select('presenter-gesture', 'Gesture', 'gesture', [['', 'Automatic']].concat(GESTURES.map(g => [g, capital(words(GESTURE_WORDS, g))]))),
                    select('presenter-background', 'Background', 'background', [['scene', 'The scene decides'], ['dark', 'Dark'], ['light', 'Light']]),
                    select('presenter-fallback', 'If an AI presenter clip is missing', 'fallback', [['none', 'Show no presenter'], ['aadhi-teacher', 'Use Aadhi Teacher instead']]))));
        }

        renderPreview(profile) {
            const h = (...a) => el(this.doc, ...a);
            const s = this.settings;
            const box = h('div', { class: 'presenter-preview', 'data-presenter': profile ? profile.id : '' });
            const meta = h('div', { class: 'presenter-preview-meta' });
            if (!profile) return box;
            if (profile.type === 'illustrated') {
                const holder = h('div', { class: 'presenter-preview-figure' });
                holder.innerHTML = teacherSvg(profile, { expression: s.expression || 'engaged', gesture: s.gesture || 'explaining' });
                box.appendChild(holder);
            } else if (profile.type === 'mascot') {
                box.appendChild(this.mascotPoster ? h('img', { class: 'presenter-preview-figure', src: this.mascotPoster, alt: 'Aadhi' })
                    : h('div', { class: 'presenter-preview-figure presenter-placeholder', text: 'Aadhi' }));
            } else {
                box.appendChild(h('div', { class: 'presenter-preview-figure presenter-placeholder', text: profile.available ? 'Preview needs a generated clip' : 'Not configured' }));
            }
            meta.appendChild(h('strong', { text: profile.name }));
            meta.appendChild(h('span', { text: `${STYLES[s.style] || 'Friendly'} · ${MODES[s.mode] || MODES.lesson}` }));
            meta.appendChild(h('span', { class: 'presenter-caps', text: profile.available ? providerText(profile, { debug: debugOn(this.debug) }) : profile.unavailable_reason }));
            if (profile.type === 'ai_avatar' || profile.type === 'custom') {
                meta.appendChild(h('span', { class: 'presenter-note', text: 'Presenter clips are made only when you ask (in Visual Review), one per scene, and may be billed by the provider.' }));
            }
            box.appendChild(meta);
            return box;
        }
    }

    // ---- Visual Review: the presenter item ---------------------------------------------------------------------

    const TYPE_TEXT = { mascot: 'Aadhi (mascot)', illustrated: 'Illustrated teacher', ai_avatar: 'AI presenter', custom: 'Custom presenter' };

    // The presenter's facts in plain words. options.debug (the page's ?visualDebug): the provider and the plan's raw codes too.
    function reviewFacts(plan, profile, { debug = false } = {}) {
        const caps = (profile && profile.capabilities) || {};
        const technical = debugOn(debug);
        const by = technical && plan.media && plan.media.provider ? ` by ${plan.media.provider}` : '';
        const speech = plan.type === 'illustrated' ? "Mouth follows the narration's loudness (not phoneme lip sync)"
            : plan.media && plan.media.lip_sync ? `Lip-synced to the narration${by}`
                : plan.type === 'mascot' ? 'Talking animation' : plan.media ? 'Talking animation (not lip-synced)' : 'No clip yet';
        const corner = plan.layout === 'left' ? 'Small, in the bottom-left corner' : plan.layout === 'right' ? 'Small, in the bottom-right corner' : 'Small, in a bottom corner';
        const where = !plan.enabled ? 'Hidden in this scene'
            : `${plan.placement === 'pip' ? corner : words(POSITION_WORDS, plan.position) || 'Beside the content'}${technical ? ` (${plan.layout})` : ''}`;
        const gesture = plan.gesture_shown || plan.gesture || 'none';
        const expression = plan.expression_shown || plan.expression;
        const doing = [plan.behavior ? words(BEHAVIOR_WORDS, plan.behavior) : '', expression ? words(EXPRESSION_WORDS, expression) : '', words(GESTURE_WORDS, gesture)]
            .filter(Boolean).join(' · ');
        return [
            ['Presenter', `${(profile && profile.name) || plan.presenter_id} · ${TYPE_TEXT[plan.type] || plan.type}`],
            ['Where', where],
            ['Doing', plan.enabled ? doing + (technical ? ` (${[plan.behavior, expression, gesture].filter(Boolean).join(', ')})` : '') : '—'],
            ['Speech', plan.enabled ? speech : '—'],
            ['Why', [plan.reason, plan.composition_note].filter(Boolean).join('; ')],
            ...(plan.fallbacks && plan.fallbacks.length ? [['Shown differently', plan.fallbacks.join('; ')]] : []),
            ...(technical && caps.provider && plan.type !== 'illustrated' ? [['Provider', caps.provider]] : [])
        ];
    }

    return { BOXES, DEFAULTS, EXPRESSIONS, GESTURES, MODES, STYLES, PresenterApi, PresenterSettingsPanel, PresenterStage, TYPE_TEXT,
        applyPlans, boxFor, effective, isLegacy, loadSettings, mouthLevel, providerText, reviewFacts, saveSettings, teacherSvg };
});
