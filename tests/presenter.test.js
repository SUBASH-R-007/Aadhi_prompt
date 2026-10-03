'use strict';
// Unit tests for the presenter in the page (presenter.js, Phase 12) and its Visual Review item (review.js).
// Run from the repo root:  node --test "tests/*.test.js"
const test = require('node:test');
const assert = require('node:assert/strict');
const P = require('../presenter.js');
const R = require('../review.js');

// ---- a small DOM stand-in ------------------------------------------------------------------------------------------
class Node {
    constructor(doc, tag) {
        this.doc = doc; this.tag = tag; this.children = []; this.attrs = {}; this.listeners = {}; this.style = {}; this._text = ''; this._html = '';
        const names = new Set();
        this.classList = { add: n => names.add(n), remove: n => names.delete(n), contains: n => names.has(n) };
        Object.defineProperty(this, 'className', { get: () => [...names].join(' '), set: v => { names.clear(); String(v).split(/\s+/).filter(Boolean).forEach(n => names.add(n)); } });
    }
    setAttribute(k, v) { this.attrs[k] = String(v); }
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
    removeAttribute(k) { delete this.attrs[k]; }
    addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); }
    removeEventListener() {}
    appendChild(c) { this.children.push(c); c.parent = this; return c; }
    append(...cs) { cs.forEach(c => this.appendChild(typeof c === 'string' ? this.doc.createTextNode(c) : c)); }
    get textContent() { return this.tag === '#text' ? this._text : this._html ? this._html.replace(/<[^>]+>/g, '') : this.children.map(c => c.textContent).join(''); }
    set textContent(v) { this.children = []; this._html = ''; if (this.tag === '#text') this._text = String(v); else if (v) this.children.push(Object.assign(new Node(this.doc, '#text'), { _text: String(v) })); }
    set innerHTML(v) { this.children = []; this._html = String(v); this._parts = null; }
    get innerHTML() { return this._html; }
    // the SVG markup is parsed only as far as the stage needs: one stand-in element per teacher part (class) in the
    // markup, the same element until the markup is replaced (so a rebuild would show as new elements)
    querySelector(sel) {
        if (/^\.teacher-[\w-]+$/.test(sel)) {
            if (!this._html.includes(`class="${sel.slice(1)}"`)) return null;
            this._parts = this._parts || {};
            return (this._parts[sel] = this._parts[sel] || new Node(this.doc, sel === '.teacher-svg' ? 'svg' : 'g'));
        }
        return this.all().find(n => sel.split(',').some(s => n.tag === s.trim().replace(/^#.*/, '').split('.')[0])) || null;
    }
    querySelectorAll() { return []; }
    all() { return this.children.flatMap(c => [c, ...c.all()]); }
    fire(type) { (this.listeners[type] || []).forEach(fn => fn({ type, target: this })); }
    play() { this.paused = false; return Promise.resolve(); }
    pause() { this.paused = true; }
}
function fakeDoc() {
    const doc = { createElement: tag => new Node(doc, tag), createTextNode: t => Object.assign(new Node(doc, '#text'), { _text: t }), addEventListener() {}, removeEventListener() {} };
    doc.body = new Node(doc, 'body');
    return doc;
}
const TEACHER = { id: 'aadhi-teacher', name: 'Aadhi Teacher', type: 'illustrated', appearance: { palette: ['#5B2A86', '#FFD700', '#C98B62', '#2B1B14'] },
    available: true, capabilities: { speech: 'audio_envelope', lip_sync: false, expressions: P.EXPRESSIONS, gestures: P.GESTURES } };
const AI = { id: 'ai-teacher', name: 'AI Teacher', type: 'ai_avatar', available: true, capabilities: { speech: 'lip_sync', lip_sync: true, expressions: ['neutral'], gestures: ['none'], provider: 'fake-presenter' } };
const plan = (extra = {}) => ({ presenter_id: 'aadhi-teacher', type: 'illustrated', enabled: true, position: 'right', placement: 'side', layout: 'right',
    box: { x: 0.72, y: 0.14, w: 0.26, h: 0.72 }, behavior: 'explaining', expression: 'engaged', gesture: 'point', reason: 'an explanation', ...extra });
const timeline = { fps: 25, duration: 2.2, segments: [{ start: 0, duration: 1, envelope_start: 0 }, { start: 1.2, duration: 1, envelope_start: 30 }],
    envelope: [...Array(25).fill(0.9), 0, 0, 0, 0, 0, ...Array(25).fill(0.1)] };

// ---- settings and plans --------------------------------------------------------------------------------------------

test('settings: defaults, persistence and the legacy path', () => {
    const store = {}; const storage = { getItem: k => store[k] || null, setItem: (k, v) => { store[k] = v; } };
    const s = P.loadSettings(storage);
    assert.deepEqual([s.presenter_id, s.mode, s.position], ['aadhi', 'lesson', 'right']);
    assert.ok(P.isLegacy(s)); // default: Aadhi placed by the screenplay, exactly as before
    P.saveSettings(storage, { ...s, presenter_id: 'aadhi-teacher', mode: 'auto' });
    assert.equal(P.loadSettings(storage).presenter_id, 'aadhi-teacher');
    assert.ok(!P.isLegacy(P.loadSettings(storage)));
    assert.equal(P.loadSettings({ getItem: () => '{broken' }).mode, 'lesson');
    assert.equal(P.effective({ presenter_plan: plan() }, s), null); // legacy settings: no presenter plan used
    assert.equal(P.effective({ presenter_plan: plan() }, { ...s, presenter_id: 'aadhi-teacher', mode: 'auto' }).layout, 'right');
    assert.equal(P.effective({ presenter_plan: plan() }, { ...s, presenter_id: 'ai-teacher', mode: 'auto' }), null); // a plan for another presenter
});

test('plans keep a clip made earlier for the same presenter; boxes for every placement', () => {
    const slides = [{ presenter_plan: { presenter_id: 'ai-teacher', media: { asset_id: 'x', url: '/x' } } }, {}];
    P.applyPlans(slides, [{ presenter_id: 'ai-teacher', enabled: true }, { presenter_id: 'ai-teacher', enabled: false }]);
    assert.deepEqual(slides[0].presenter_plan.media, { asset_id: 'x', url: '/x' });
    P.applyPlans(slides, [{ presenter_id: 'aadhi-teacher', enabled: true }]);
    assert.equal(slides[0].presenter_plan.media, undefined); // another presenter: not its clip
    assert.deepEqual(P.boxFor({ enabled: true, layout: 'right', placement: 'pip' }), P.BOXES.pip_right);
    assert.equal(P.boxFor({ enabled: false, layout: 'right' }), null);
    for (const box of Object.values(P.BOXES)) assert.ok(box.y + box.h <= 0.87, 'above the subtitles');
});

test('the illustrated teacher: every expression and gesture, mirrored on the left', () => {
    for (const expression of P.EXPRESSIONS) {
        for (const gesture of P.GESTURES) {
            const svg = P.teacherSvg(TEACHER, { expression, gesture });
            assert.ok(svg.includes(`data-expression="${expression}"`) && svg.includes(`data-gesture="${gesture}"`));
            assert.ok(svg.includes('teacher-mouth-open') && svg.includes('#5B2A86'));
        }
    }
    assert.ok(P.teacherSvg(TEACHER, { mirror: true }).includes('scale(-1 1)'));
    assert.ok(P.teacherSvg(TEACHER, { expression: 'surprised' }).includes('opacity="1"')); // the "O" mouth
});

test('Phase 21 (security): a custom presenter\'s name, colours or a plan\'s expression never become markup', () => {
    // the SVG string is inserted as HTML by the stage, the settings preview and Visual Review: every value is attribute-escaped
    const evil = '<img src=x onerror=alert(1)>"';
    const custom = { ...TEACHER, name: evil, appearance: { palette: ['red" onload="alert(2)', '#FFD700', "x' onclick='alert(3)", '<b>'] } };
    const svg = P.teacherSvg(custom, { expression: '"><script>alert(4)</script>', gesture: 'point"onmouseover="alert(5)' });
    assert.ok(!svg.includes('<img') && !svg.includes('<script') && !svg.includes('<b>'), 'no element smuggled in');
    // with every (double-quoted) attribute value emptied, no event handler attribute is left: the values stay values
    const bare = svg.replace(/="[^"]*"/g, '=""');
    assert.ok(!/\son\w+=/.test(bare) && !bare.includes("'"), 'no attribute smuggled in');
    assert.ok(svg.includes('aria-label="&lt;img src=x onerror=alert(1)&gt;&quot;, &quot;&gt;&lt;script&gt;alert(4)&lt;/script&gt;, point&quot;onmouseover=&quot;alert(5)"'));
    assert.ok(svg.includes('fill="red&quot; onload=&quot;alert(2)"') && svg.includes('fill="x&#39; onclick=&#39;alert(3)"'));
    assert.ok(svg.includes('data-expression="&quot;&gt;&lt;script&gt;alert(4)&lt;/script&gt;"'));
    // every quote left in the markup opens or closes an attribute value: the escaped values cannot end one early
    assert.equal((svg.match(/="/g) || []).length * 2, (svg.match(/"/g) || []).length);
    // ordinary values are unchanged, and a name like "constructor" for a gesture or an expression draws the defaults
    assert.ok(P.teacherSvg(TEACHER, { expression: 'happy', gesture: 'welcome' }).includes('aria-label="Aadhi Teacher, happy, welcome"'));
    assert.doesNotThrow(() => P.teacherSvg(TEACHER, { expression: 'constructor', gesture: 'constructor' }));
    assert.equal(P.teacherSvg(TEACHER, { gesture: 'toString' }).match(/rotate\(([-\d]+)\)/)[1], '8'); // POSE.none's arm
    // the settings preview draws it (as HTML) escaped too
    const doc = fakeDoc();
    const container = new Node(doc, 'div');
    const panel = new P.PresenterSettingsPanel({ doc, container, storage: { getItem: () => null, setItem() {} }, api: {} });
    panel.data = { profiles: [custom], providers: {} };
    panel.settings.presenter_id = custom.id;
    panel.render();
    const figure = container.all().find(n => n.attrs.class === 'presenter-preview-figure');
    assert.ok(figure.innerHTML.includes('&lt;img src=x onerror=alert(1)&gt;&quot;') && !figure.innerHTML.includes('<img'));
});

test('mouth level follows the speech envelope of the segment playing', () => {
    assert.equal(P.mouthLevel(timeline, 0, 0.1), 0.9);
    assert.equal(P.mouthLevel(timeline, 1, 0.1), 0.1);
    assert.equal(P.mouthLevel(timeline, 0, 1.5), 0); // after the segment's audio
    assert.equal(P.mouthLevel(null, 0, 0), null);
});

// ---- the stage -------------------------------------------------------------------------------------------------------

function stage(settings = {}, speech = async () => timeline, profiles = {}) {
    const doc = fakeDoc();
    const frames = [];
    const s = new P.PresenterStage({ doc, api: { speech }, settings: () => ({ ...P.DEFAULTS, presenter_id: 'aadhi-teacher', mode: 'auto', ...settings }),
        profile: id => ({ 'aadhi-teacher': TEACHER, 'ai-teacher': AI, ...profiles })[id], voice: () => ({ voice: 'v', engine: 'default' }),
        raf: fn => frames.push(fn), now: () => 1000 });
    return { s, doc, frames, run: (n = 3) => { for (let i = 0; i < n && frames.length; i++) frames.shift()(); } };
}
const tick = () => new Promise(r => setImmediate(r));

test('the illustrated teacher is drawn in its box and its mouth follows the narration audio', async () => {
    const { s, run } = stage();
    assert.ok(s.beginScene({ narration: 'Hello' }, plan(), 2));
    await tick();
    assert.equal(s.layer.style.left, '72%');
    assert.equal(s.layer.getAttribute('data-scene'), '2');
    assert.equal(s.layer.getAttribute('data-state'), 'idle');
    assert.ok(s.wantsServerAudio()); // follows the lesson's own audio, so preview and export use the server voice
    const audio = new Node(null, 'audio');
    audio.currentTime = 0.1; audio.playbackRate = 1;
    s.attachAudio(0, audio);
    audio.fire('playing');
    run(2);
    assert.equal(s.layer.getAttribute('data-state'), 'speaking');
    assert.equal(s.mouthOpen.getAttribute('opacity'), '1');
    assert.equal(Number(s.mouthOpen.getAttribute('ry')), 1 + 0.9 * 9);
    audio.currentTime = 2; // past the segment: the mouth closes
    run(1);
    assert.equal(s.mouthOpen.getAttribute('opacity'), '0');
    assert.ok(s.stats.envelopeFrames >= 2 && s.stats.genericFrames === 0);
    audio.fire('pause');
    assert.equal(s.layer.getAttribute('data-state'), 'listening');
    s.endScene();
    assert.equal(s.layer.getAttribute('data-state'), 'hidden');
    assert.equal(s.layer.children.length, 0);
});

test("the browser's own speech (no audio file) gets a plain talking animation", async () => {
    const { s, run } = stage({}, async () => { throw new Error('offline'); });
    s.beginScene({ narration: 'Hi' }, plan());
    await tick();
    s.narrationEvent('segment');
    run(2);
    assert.ok(s.stats.genericFrames >= 1);
    s.narrationEvent('end');
    assert.equal(s.layer.getAttribute('data-state'), 'idle');
});

test('an AI presenter plays its clip in time with the narration; without a clip nothing is faked', async () => {
    const { s, run } = stage({ presenter_id: 'ai-teacher' });
    s.beginScene({ narration: 'Hi' }, plan({ presenter_id: 'ai-teacher', type: 'ai_avatar', media: { url: '/static/c.mp4', kind: 'video', lip_sync: true } }));
    await tick();
    assert.equal(s.kind, 'video');
    assert.equal(s.video.muted, true); // the narration stays the lesson's own audio
    const audio = new Node(null, 'audio');
    audio.currentTime = 0.4; audio.playbackRate = 1.1;
    s.attachAudio(1, audio);
    audio.fire('playing');
    assert.equal(s.video.currentTime, 1.2 + 0.4); // the second segment starts at 1.2 s in the clip
    assert.equal(s.video.playbackRate, 1.1);
    run(1);
    s.narrationEvent('end');
    assert.equal(s.video.paused, true);
    const empty = stage({ presenter_id: 'ai-teacher' }).s;
    assert.equal(empty.beginScene({ narration: 'Hi' }, plan({ presenter_id: 'ai-teacher', type: 'ai_avatar' })), null);
    assert.equal(empty.layer.getAttribute('data-state'), 'missing');
    assert.ok(!empty.wantsServerAudio());
    const fallback = stage({ presenter_id: 'ai-teacher', fallback: 'aadhi-teacher' }).s;
    fallback.beginScene({ narration: 'Hi' }, plan({ presenter_id: 'ai-teacher', type: 'ai_avatar' }));
    assert.equal(fallback.layer.getAttribute('data-fallback'), 'aadhi-teacher'); // only because the user allowed it
});

test('hidden and mascot plans draw nothing (Aadhi stays with MascotController)', () => {
    const { s } = stage();
    assert.equal(s.beginScene({}, plan({ enabled: false })), null);
    assert.equal(s.beginScene({}, plan({ type: 'mascot', presenter_id: 'aadhi' })), null);
    assert.equal(s.layer.getAttribute('data-state'), 'hidden');
});

// ---- acting at a moment (Phase 16 synchronization) -------------------------------------------------------------------

// The parts of a teacher freshly drawn with this expression and gesture (what acting must redraw in place)
function drawnParts(expression, gesture, profile = TEACHER) {
    const svg = P.teacherSvg(profile, { expression, gesture });
    const after = (from, to, start = 0) => { const i = svg.indexOf(from, start) + from.length; return svg.slice(i, svg.indexOf(to, i)); };
    return { arms: after('<g class="teacher-arms">', '</g><g class="teacher-head"'), head: after('<g class="teacher-head" transform="', '"'),
        eyes: after('<g class="teacher-eyes">', '</g>'), brows: after('>', '</g>', svg.indexOf('<g class="teacher-brows"')),
        mouth: after('class="teacher-mouth-closed" d="', '"') };
}
const PARTS = ['.teacher-svg', '.teacher-arms', '.teacher-head', '.teacher-eyes', '.teacher-brows'];

test('acting: the drawn teacher changes its gesture and expression in place, exactly as a scene drawn that way', async () => {
    const { s } = stage();
    s.beginScene({ narration: 'Hello' }, plan({ expression: 'engaged', gesture: 'point' }), 0);
    await tick();
    const markup = s.layer.innerHTML;
    const [svg, arms, head, eyes, brows] = PARTS.map(c => s.layer.querySelector(c));
    assert.ok(markup.includes('<g class="teacher-arms">') && markup.includes('<g class="teacher-brows"'));
    assert.equal(s.act({ gesture: 'welcome', expression: 'happy' }), true);
    const want = drawnParts('happy', 'welcome');
    assert.equal(arms.innerHTML, want.arms);
    assert.equal(head.getAttribute('transform'), want.head);
    assert.equal(eyes.innerHTML, want.eyes);
    assert.equal(brows.innerHTML, want.brows);
    assert.equal(s.mouthClosed.getAttribute('d'), want.mouth);
    assert.deepEqual(['data-gesture', 'data-expression', 'aria-label'].map(a => svg.getAttribute(a)), ['welcome', 'happy', 'Aadhi Teacher, happy, welcome']);
    // the same SVG: nothing rebuilt, the layer where it was, in the state it was
    assert.equal(s.layer.innerHTML, markup);
    [svg, arms, head, eyes, brows].forEach((part, i) => assert.equal(s.layer.querySelector(PARTS[i]), part)); // the same elements
    assert.deepEqual([s.layer.style.left, s.layer.style.width, s.layer.getAttribute('data-state')], ['72%', '26%', 'idle']);
    // only a gesture: the face stays
    const face = [eyes.innerHTML, brows.innerHTML, head.getAttribute('transform'), s.mouthClosed.getAttribute('d')];
    assert.equal(s.act({ gesture: 'counting' }), true);
    assert.equal(arms.innerHTML, drawnParts('happy', 'counting').arms);
    assert.deepEqual([eyes.innerHTML, brows.innerHTML, head.getAttribute('transform'), s.mouthClosed.getAttribute('d')], face);
    // only an expression: the arms stay
    const pose = arms.innerHTML;
    assert.equal(s.act({ gesture: null, expression: 'thinking' }), true);
    assert.equal(arms.innerHTML, pose);
    assert.deepEqual([eyes.innerHTML, head.getAttribute('transform')], [drawnParts('thinking', 'counting').eyes, drawnParts('thinking', 'counting').head]);
    assert.equal(svg.getAttribute('data-gesture'), 'counting');
    // what it already shows: nothing to do
    assert.equal(s.act({ gesture: 'counting', expression: 'thinking' }), false);
    assert.equal(s.stats.acts, 3);
    // the next scene is drawn from its own plan; acting starts again from there
    s.beginScene({ narration: 'Next' }, plan({ expression: 'serious', gesture: 'none' }), 1);
    assert.equal(s.act({ gesture: 'none', expression: 'serious' }), false);
    assert.equal(s.act({ gesture: 'point' }), true);
    assert.equal(s.layer.querySelector('.teacher-arms').innerHTML, drawnParts('serious', 'point').arms);
});

test('acting: the mouth that follows the narration keeps working before, during and after acting', async () => {
    const { s, run } = stage();
    s.beginScene({ narration: 'Hello' }, plan(), 1);
    await tick();
    const mouthOpen = s.mouthOpen;
    assert.equal(s.act({ expression: 'surprised' }), true); // at rest: the face's own mouth (the "O")
    assert.deepEqual([mouthOpen.getAttribute('ry'), mouthOpen.getAttribute('opacity')], ['6', '1']);
    assert.equal(s.act({ expression: 'friendly' }), true);
    assert.equal(mouthOpen.getAttribute('opacity'), '0');
    const audio = new Node(null, 'audio');
    audio.currentTime = 0.1; audio.playbackRate = 1;
    s.attachAudio(0, audio);
    audio.fire('playing');
    run(1);
    assert.equal(Number(mouthOpen.getAttribute('ry')), 1 + 0.9 * 9);
    assert.equal(s.act({ gesture: 'emphasis', expression: 'surprised' }), true); // mid-speech: the narration keeps the mouth
    assert.equal(Number(mouthOpen.getAttribute('ry')), 1 + 0.9 * 9);
    assert.equal(s.mouthOpen, mouthOpen);
    assert.equal(s.layer.querySelector('.teacher-mouth-open'), mouthOpen); // the very same element
    audio.currentTime = 2; // past the segment: closed
    run(1);
    assert.equal(mouthOpen.getAttribute('opacity'), '0');
    audio.currentTime = 0.5;
    run(1);
    assert.deepEqual([mouthOpen.getAttribute('opacity'), Number(mouthOpen.getAttribute('ry'))], ['1', 1 + 0.9 * 9]);
    assert.ok(s.stats.envelopeFrames >= 3 && s.stats.genericFrames === 0);
    assert.equal(s.layer.getAttribute('data-state'), 'speaking');
    audio.fire('ended');
    assert.equal(s.layer.getAttribute('data-state'), 'listening');
});

test('acting: unknown values, values the profile lacks and anything but the drawn teacher are ignored', async () => {
    const { s } = stage();
    s.beginScene({ narration: 'Hi' }, plan({ expression: 'engaged', gesture: 'point' }));
    const svg = s.layer.querySelector('.teacher-svg');
    for (const bad of [null, undefined, 'point', 7, [], {}, { gesture: 'dance' }, { expression: 'angry' }, { gesture: 'POINT' },
        { gesture: 'toString', expression: '__proto__' }, { gesture: ['welcome'] }, { expression: { name: 'happy' } }, { gesture: 'point', expression: 'engaged' }]) {
        assert.equal(s.act(bad), false, String(JSON.stringify(bad)));
    }
    assert.equal(svg.getAttribute('data-gesture'), null); // never touched
    assert.equal(s.layer.querySelector('.teacher-arms').innerHTML, '');
    assert.equal(s.act({ gesture: 'dance', expression: 'happy' }), true); // the known part only
    assert.deepEqual([svg.getAttribute('data-gesture'), svg.getAttribute('data-expression')], ['point', 'happy']);
    assert.equal(s.layer.querySelector('.teacher-arms').innerHTML, '');
    // a drawn presenter whose profile can show less: only what it has
    const few = { ...TEACHER, capabilities: { ...TEACHER.capabilities, expressions: ['neutral', 'friendly'], gestures: ['none', 'point'] } };
    const limited = stage({}, async () => timeline, { 'aadhi-teacher': few }).s;
    limited.beginScene({ narration: 'Hi' }, plan({ expression: 'neutral', gesture: 'none' }));
    assert.equal(limited.act({ gesture: 'welcome', expression: 'happy' }), false);
    assert.equal(limited.act({ gesture: 'point', expression: 'happy' }), true);
    assert.deepEqual(['data-gesture', 'data-expression'].map(a => limited.layer.querySelector('.teacher-svg').getAttribute(a)), ['point', 'neutral']);
    // an AI clip is pre-rendered, a picture is still, Aadhi is MascotController's: none of them acts here
    const ai = stage({ presenter_id: 'ai-teacher' }).s;
    ai.beginScene({ narration: 'Hi' }, plan({ presenter_id: 'ai-teacher', type: 'ai_avatar', media: { url: '/static/c.mp4', kind: 'video' } }));
    assert.equal(ai.kind, 'video');
    assert.equal(ai.act({ gesture: 'point', expression: 'happy' }), false);
    assert.equal(ai.layer.children.length, 1); // the clip only
    ai.beginScene({ narration: 'Hi' }, plan({ presenter_id: 'ai-teacher', type: 'ai_avatar', media: { url: '/static/p.png', kind: 'image' } }));
    assert.equal(ai.act({ gesture: 'point' }), false);
    ai.beginScene({ narration: 'Hi' }, plan({ presenter_id: 'ai-teacher', type: 'ai_avatar' }));
    assert.equal(ai.layer.getAttribute('data-state'), 'missing');
    assert.equal(ai.act({ gesture: 'point' }), false);
    assert.equal(ai.stats.acts, 0);
    assert.equal(s.beginScene({}, plan({ type: 'mascot', presenter_id: 'aadhi' })), null);
    assert.equal(s.act({ gesture: 'point', expression: 'happy' }), false);
    s.beginScene({}, plan());
    s.endScene();
    assert.equal(s.act({ gesture: 'welcome' }), false);
    assert.equal(new P.PresenterStage({ doc: fakeDoc() }).act({ gesture: 'point' }), false); // before any scene
});

// ---- settings panel, review ------------------------------------------------------------------------------------------

test('the settings panel lists presenters, disables unavailable ones and saves choices', async () => {
    const doc = fakeDoc();
    const container = new Node(doc, 'div');
    const store = {};
    const changes = [];
    const panel = new P.PresenterSettingsPanel({ doc, container, storage: { getItem: k => store[k] || null, setItem: (k, v) => { store[k] = v; } },
        onChange: s => changes.push(s.presenter_id),
        api: { profiles: async () => ({ profiles: [{ id: 'aadhi', name: 'Aadhi', type: 'mascot', available: true, capabilities: { speech: 'talking_animation', expressions: [] } }, TEACHER,
            { ...AI, available: false, capabilities: null, unavailable_reason: "AI presenter generation isn't configured yet." }],
            providers: { providers: [], available: false, message: "AI presenter generation isn't configured yet.", not_presenter_capable: [{ name: 'veo', label: 'Google Veo' }] } }) } });
    await panel.load();
    const options = container.all().filter(n => n.tag === 'option' && n.parent && n.parent.attrs.id === 'presenter-select');
    assert.deepEqual(options.map(o => o.value), ['aadhi', 'aadhi-teacher', 'ai-teacher']);
    assert.equal(options[2].attrs.disabled, '');
    assert.match(container.textContent, /Google Veo/);
    panel.set('presenter_id', 'aadhi-teacher');
    assert.deepEqual(changes, ['aadhi-teacher']);
    assert.equal(JSON.parse(store['aadhi.presenter']).presenter_id, 'aadhi-teacher');
    assert.match(container.textContent, /mouth follows the narration's loudness/);
    // Phase 21: the advanced choices in words; an AI presenter's provider shown in the preview only with debug
    const expressionOptions = container.all().filter(n => n.tag === 'option' && n.parent && n.parent.attrs.id === 'presenter-expression');
    assert.deepEqual(expressionOptions.slice(0, 2).map(o => o.textContent), ['Automatic', 'Calm face']);
    const gestureOptions = container.all().filter(n => n.tag === 'option' && n.parent && n.parent.attrs.id === 'presenter-gesture');
    assert.ok(gestureOptions.map(o => o.textContent).includes('Pointing'));
    const withAi = debug => {
        const box = new Node(doc, 'div');
        const p = new P.PresenterSettingsPanel({ doc, container: box, storage: { getItem: () => null, setItem() {} }, api: {}, debug });
        p.data = { profiles: [AI], providers: {} };
        p.settings.presenter_id = 'ai-teacher';
        p.render();
        return box.all().find(n => n.attrs.class === 'presenter-caps').textContent;
    };
    assert.doesNotMatch(withAi(false), /fake-presenter/);
    assert.match(withAi(true), /made by fake-presenter/);
});

test('review facts say honestly how the presenter speaks', () => {
    const facts = (p, profile = TEACHER, options) => Object.fromEntries(P.reviewFacts(p, profile, options));
    assert.match(facts(plan()).Speech, /loudness \(not phoneme lip sync\)/);
    // Phase 21: the provider is a technical detail, shown with ?visualDebug only
    const lipSynced = plan({ type: 'ai_avatar', media: { lip_sync: true, provider: 'fake-presenter' } });
    assert.equal(facts(lipSynced).Speech, 'Lip-synced to the narration');
    assert.match(facts(lipSynced, TEACHER, { debug: true }).Speech, /Lip-synced to the narration by fake-presenter/);
    assert.match(facts(lipSynced, TEACHER, { debug: () => true }).Speech, /by fake-presenter/);
    assert.match(facts(plan({ type: 'ai_avatar', media: { lip_sync: false } })).Speech, /not lip-synced/);
    assert.equal(facts(plan({ enabled: false })).Where, 'Hidden in this scene');
    assert.match(facts(plan({ fallbacks: ['surprised shown as engaged'] }))['Shown differently'], /surprised/);
});

test('Phase 21: review facts in plain words; the provider and the raw codes only in debug', () => {
    const facts = (p, profile = TEACHER, options) => Object.fromEntries(P.reviewFacts(p, profile, options));
    const normal = facts(plan());
    assert.equal(normal.Where, 'On the right');
    assert.equal(normal.Doing, 'Explaining · engaged · pointing');
    assert.equal(facts(plan({ position: 'left', layout: 'left' })).Where, 'On the left');
    assert.equal(facts(plan({ placement: 'pip', layout: 'left' })).Where, 'Small, in the bottom-left corner');
    assert.equal(facts(plan({ placement: 'pip', layout: 'right' })).Where, 'Small, in the bottom-right corner');
    assert.equal(facts(plan({ behavior: 'concluding', expression_shown: 'encouraging', gesture_shown: 'open_hand' })).Doing, 'Wrapping up · encouraging · open hand');
    assert.equal(facts(plan({ behavior: 'dancing', gesture: 'wave_hand' })).Doing, 'dancing · engaged · wave hand', 'an unknown code as it is');
    const ai = plan({ presenter_id: 'ai-teacher', type: 'ai_avatar', media: { lip_sync: true, provider: 'fake-presenter' } });
    assert.equal(facts(ai, AI).Provider, undefined, 'no Provider row');
    assert.ok(!JSON.stringify(P.reviewFacts(ai, AI)).includes('fake-presenter'));
    const debug = facts(ai, AI, { debug: true });
    assert.equal(debug.Provider, 'fake-presenter');
    assert.equal(debug.Where, 'On the right (right)');
    assert.equal(debug.Doing, 'Explaining · engaged · pointing (explaining, engaged, point)');
    // what a presenter can do: no provider in normal mode
    assert.equal(P.providerText(AI), 'lip-synced to the narration · 1 expressions · 1 gestures');
    assert.equal(P.providerText(AI, { debug: true }), 'lip-synced to the narration · 1 expressions · 1 gestures · made by fake-presenter');
});

test('Visual Review: presenter items, decisions and New Version through the presenter endpoints', async () => {
    const slides = [{ title: 'Intro', presenter_plan: plan({ presenter_id: 'ai-teacher', type: 'ai_avatar' }) },
        { title: 'Legacy', presenter_plan: { presenter_id: 'aadhi', type: 'mascot', source: 'lesson', enabled: true } }];
    const calls = [];
    const session = new R.ReviewSession({ slides, api: { review: async () => { throw new Error('visual endpoint used'); } }, media: {},
        presenter: {
            enabled: () => true, canGenerate: p => p.type === 'ai_avatar',
            review: async body => { calls.push(['review', body.action, body.asset_id || body.position || null]);
                return { review: body.action === 'reset' ? null : { status: body.action === 'keep' ? 'approved' : body.action === 'remove' ? 'removed' : 'changed' },
                    plan: plan({ presenter_id: 'ai-teacher', type: 'ai_avatar', enabled: body.action !== 'remove', media: body.asset_id ? { asset_id: body.asset_id, url: '/a', kind: 'video' } : undefined }) }; },
            generate: async (i, { force }) => { calls.push(['generate', i, force]); return { asset_id: force ? 'v2' : 'v1', provider: 'fake-presenter', warnings: [] }; }
        } });
    assert.deepEqual(session.all.map(i => i.slot), ['presenter']); // the legacy mascot scene is not a presenter item
    assert.equal(session.current.status, 'pending');
    await session.generate();
    assert.deepEqual(calls.slice(0, 2), [['generate', 0, false], ['review', 'keep', 'v1']]);
    assert.equal(slides[0].presenter_plan.media.asset_id, 'v1');
    await session.generate({ force: true }); // New Version: a new clip, chosen
    assert.deepEqual(calls.slice(2, 4), [['generate', 0, true], ['review', 'choose', 'v2']]);
    await session.decide('move', { position: 'left' });
    await session.remove();
    assert.equal(slides[0].visual_review.presenter.status, 'removed');
    await session.reset();
    assert.equal(slides[0].visual_review, undefined);
    const off = new R.ReviewSession({ slides, api: {}, media: {}, presenter: { enabled: () => false } });
    assert.equal(off.all.length, 0); // presenter off: nothing to review
});

test('the API client', async () => {
    const calls = [];
    const api = new P.PresenterApi({ fetch: async (url, init) => { calls.push([url, init.method || 'GET', init.body && JSON.parse(init.body)]);
        return { ok: true, status: url.endsWith('generate') ? 202 : 200, json: async () => ({ run_id: 'r' }) }; } });
    // The scenes go to the Presenter Director as they are, with their Phase 15 visual direction (no field whitelist)
    const direction = { version: 1, strategy: 'concept_overview', presenter: { role: 'guide', interaction: 'points_to_visual' } };
    await api.plan([{ title: 'a', visual_direction: direction }], { presenter_id: 'aadhi-teacher' });
    assert.deepEqual(calls[0][2].scenes[0].visual_direction, direction);
    await api.speech('Hi', 'v', 'default');
    await api.speech('Hi', 'v', 'default'); // cached
    const started = await api.generate({ scene: {} });
    assert.equal(started.httpStatus, 202);
    assert.deepEqual(calls.map(c => c[0]), ['/api/presenters/plan', '/api/presenters/speech', '/api/presenters/generate']);
});
