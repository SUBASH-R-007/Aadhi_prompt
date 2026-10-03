'use strict';
// Unit tests for cinematic scenes in the page (cinematic.js, Phase 13) and the composition item of Visual Review (review.js).
// Run from the repo root:  node --test "tests/*.test.js"
const test = require('node:test');
const assert = require('node:assert/strict');
const C = require('../cinematic.js');
const R = require('../review.js');

// ---- a small DOM stand-in (tests/helpers/cinematic-dom.js) ----------------------------------------------------------
const { fakeDoc } = require('./helpers/cinematic-dom.js');

// The page's own elements the stage places
function page() {
    const doc = fakeDoc();
    const add = (parent, tag, attrs = {}) => { const n = doc.createElement(tag); Object.entries(attrs).forEach(([k, v]) => (k === 'class' ? (n.className = v) : n.setAttribute(k, v))); parent.appendChild(n); return n; };
    const zone = add(doc.body, 'div', { class: 'lecture-overlay-zone' });
    const board = add(zone, 'div', { id: 'presentation-board', class: 'cinematic-scene-container' });
    const content = add(board, 'div', { id: 'slide-content-container' });
    const side = add(doc.body, 'div', { class: 'dynamic-side-zone' });
    const panel = add(side, 'div', { class: 'side-panel-view active' });
    const presenter = add(doc.body, 'div', { id: 'presenter-layer' });
    add(presenter, 'svg', { class: 'teacher-svg' });
    return { doc, zone, board, content, side, panel, presenter };
}
function clock() {
    let t = 0; let seq = 0; const timers = new Map();
    return {
        now: () => t,
        setTimeout: (fn, ms) => { const id = ++seq; timers.set(id, { at: t + ms, fn }); return id; },
        clearTimeout: id => timers.delete(id),
        advance(ms) { const end = t + ms; for (;;) { const due = [...timers.entries()].filter(([, v]) => v.at <= end).sort((a, b) => a[1].at - b[1].at)[0]; if (!due) break; timers.delete(due[0]); t = due[1].at; due[1].fn(); } t = end; },
        pending: () => timers.size
    };
}
const box = (x, y, w, h) => ({ x, y, w, h });
function planB(extra = {}) { // Scene B of the quality gate: presenter right, visual left, labels under it
    return {
        version: 1, template: 'presenter_plus_visual', template_label: 'Presenter + visual', shot: 'medium', duration: 12,
        style: { typography: 'academic', motion: 'subtle', transitions: 'fade', background: 'auto', emphasis: 'clear' },
        background: { type: 'gradient', colors: ['#111111', '#222222', '#333333'] },
        presenter: { type: 'illustrated', presenter_id: 'aadhi-teacher', shown: true, side: 'right' },
        transition: { in: 'fade', out: 'fade', duration: 0.5 },
        camera: { shot: 'medium', movement: 'slow_zoom_in', from: { x: 0, y: 0, w: 1 }, to: { x: 0.0283, y: 0.0094, w: 0.9434 }, start: 0.6, duration: 11, easing: 'ease-in-out', target: 'visual' },
        layers: [
            { id: 'background', type: 'background', box: box(0, 0, 1, 1), z: 0, start: 0, enter: 'appear', important: false, camera: true },
            { id: 'visual', type: 'visual', role: 'image', box: box(0.05, 0.19, 0.2814, 0.52), z: 20, start: 0.3, enter: 'scale_in', important: true, camera: true },
            { id: 'board', type: 'board', role: 'body', style: 'body', box: box(0.3514, 0.19, 0.3186, 0.62), z: 30, start: 0.1, enter: 'fade_in', important: true, camera: true },
            { id: 'title', type: 'title', role: 'title', variant: 'band', subtitle: true, box: box(0.05, 0.05, 0.62, 0.1), z: 60, start: 0, enter: 'fade_in', important: true, camera: false },
            { id: 'labels', type: 'label', role: 'label', box: box(0.05, 0.725, 0.2814, 0.085), z: 50, start: 1.2, enter: 'slide_in', important: true, camera: true,
                items: [{ source: { kind: 'composition', field: 'labels', index: 0 }, at: 1.2, anchor: null }, { source: { kind: 'composition', field: 'labels', index: 1 }, at: 3, anchor: { sync: 2 } }] },
            { id: 'presenter', type: 'presenter', role: 'illustrated', side: 'right', placement: 'side', box: box(0.71, 0.14, 0.25, 0.71), face: box(0.71, 0.14, 0.25, 0.2982), z: 40, start: 0.2, enter: 'slide_in', important: true, camera: true },
            { id: 'subtitles', type: 'subtitles', role: 'caption', box: box(0, 0.85, 1, 0.15), z: 90, start: 0, enter: null, important: false, camera: false }
        ],
        timeline: [{ layer: 'labels', item: 0, at: 1.2, anchor: null, event: 'slide_in' }, { layer: 'labels', item: 1, at: 3, anchor: { sync: 2 }, event: 'slide_in' },
            { layer: 'visual', at: 2, anchor: { sync: 1 }, event: 'highlight', duration: 1.6 }],
        warnings: [], notes: [], review_status: 'pending', ...extra
    };
}
const sceneB = () => ({ type: 'content', title: 'Inside the <b>leaf</b>', subtitle: 'Where the sugar is made', narration: 'Look. [SYNC] One. [SYNC] Two.',
    composition: { labels: ['Sunlight → Chlorophyll → Glucose', { text: 'Glucose' }] }, presenter_plan: { presenter_id: 'aadhi-teacher', type: 'illustrated', enabled: true } });
function stage(extra = {}) {
    const p = page();
    const c = clock();
    const settings = { ...C.DEFAULTS, mode: 'cinematic', ...(extra.settings || {}) };
    const st = new C.CinematicStage({ doc: p.doc, settings: () => settings, mediaUrl: u => `https://host${u}`, now: c.now, setTimeout: c.setTimeout,
        clearTimeout: c.clearTimeout, exporting: () => !!extra.exporting, reducedMotion: () => !!extra.reduced });
    return { ...p, clock: c, st, settings };
}

// ---- settings and plans --------------------------------------------------------------------------------------------

test('settings: Classic by default, kept per browser; plans applied to the scenes', () => {
    const store = {}; const storage = { getItem: k => store[k] || null, setItem: (k, v) => { store[k] = v; } };
    const s = C.loadSettings(storage);
    assert.equal(s.mode, 'classic');
    assert.equal(C.isClassic(s), true);
    C.saveSettings(storage, { ...s, mode: 'cinematic', transitions: 'slide' });
    assert.equal(C.loadSettings(storage).transitions, 'slide');
    assert.equal(C.isClassic(C.loadSettings(storage)), false);
    assert.equal(C.loadSettings({ getItem: () => '{broken' }).mode, 'classic');
    const slides = [{ title: 'a', cinematic_plan: { old: true } }, { title: 'b' }];
    C.applyPlans(slides, [null, planB()]);
    assert.equal(slides[0].cinematic_plan, undefined); // Classic for this scene: the existing renderer
    assert.equal(slides[1].cinematic_plan.template, 'presenter_plus_visual');
});

test('camera maths: the same safe framing as the server, and one transform per layer that moves them as one picture', () => {
    const important = [box(0.05, 0.19, 0.53, 0.62), box(0.6, 0.19, 0.35, 0.31), box(0.8, 0.52, 0.15, 0.1176)];
    const forbidden = [C.SUBTITLES, box(0.05, 0.05, 0.9, 0.1)];
    assert.deepEqual(C.bestFraming(important, forbidden, important[0], 1.10), { x: 0.0305, y: 0.0305, w: 0.939 }); // cinematic.py's answer
    assert.deepEqual(C.bestFraming([box(0.012, 0.2, 0.976, 0.6)], [C.SUBTITLES], null, 1.1), { x: 0, y: 0, w: 1 }); // no room: no move
    assert.equal(C.framingOk({ x: 0, y: 0, w: 1 }, important, forbidden), true);
    assert.equal(C.framingOk({ x: 0.3, y: 0.3, w: 0.7 }, important, forbidden), false); // it would crop the visual
    // a point at (0.5, 0.5) of the frame, seen by a camera at {0.1, 0.1, 0.8}, lands at (0.5, 0.5) for a layer at that corner
    const f = { x: 0.1, y: 0.1, w: 0.8 };
    const b = box(0.5, 0.5, 0.2, 0.2);
    const seen = C.through(f, b);
    assert.ok(Math.abs(seen.x - 0.5) < 1e-9 && Math.abs(seen.w - 0.25) < 1e-9);
    assert.equal(C.cameraTransform(f, b), 'translate(0.000vw, 0.000vh) scale(1.2500)');
    assert.equal(C.cameraTransform({ x: 0, y: 0, w: 1 }, box(0.3, 0.2, 0.1, 0.1)), 'translate(0.000vw, 0.000vh) scale(1.0000)');
    // two layers moved by the same framing keep their relative places (scaled around the frame, not each its own centre)
    const t1 = C.through(f, box(0.2, 0.2, 0.1, 0.1)); const t2 = C.through(f, box(0.4, 0.2, 0.1, 0.1));
    assert.ok(Math.abs((t2.x - t1.x) - 0.25) < 1e-9);
    const cons = C.cameraConstraints(planB());
    assert.equal(cons.important.length, 4); // visual, board, labels, the presenter's face
    assert.equal(cons.forbidden.length, 2); // the subtitles and the title overlay (the camera never moves them)
});

test('the stage follows the setting: Classic (or no plan) renders as before', () => {
    const { st, settings } = stage();
    assert.equal(st.planFor({ cinematic_plan: planB() }).template, 'presenter_plus_visual');
    assert.equal(st.planFor({ cinematic_plan: { ...planB(), version: 99 } }), null); // a plan from another version is not guessed at
    assert.equal(st.planFor({ title: 'no plan' }), null);
    settings.mode = 'classic';
    assert.equal(st.planFor({ cinematic_plan: planB() }), null);
});

test('presenter and Aadhi: the composition places them, the presenter keeps its behaviour', () => {
    const { st } = stage();
    const pp = { presenter_id: 'aadhi-teacher', type: 'illustrated', enabled: true, position: 'right', layout: 'right', box: box(0.72, 0.14, 0.26, 0.72), expression: 'engaged', gesture: 'point' };
    const merged = st.presenterPlanFor(pp, planB());
    assert.deepEqual(merged.box, box(0.71, 0.14, 0.25, 0.71));
    assert.equal(merged.gesture, 'point');
    assert.equal(merged.expression, 'engaged');
    const hidden = planB({ layers: planB().layers.filter(l => l.id !== 'presenter') });
    assert.equal(st.presenterPlanFor(pp, hidden).enabled, false);
    assert.equal(st.presenterPlanFor(null, planB()), null);
    const mascot = planB({ presenter: { type: 'mascot', presenter_id: 'aadhi', shown: true, side: 'left' } });
    assert.equal(st.mascotPlacement(mascot, 'right'), 'left');
    assert.equal(st.mascotPlacement({ ...mascot, presenter: { ...mascot.presenter, shown: false } }, 'right'), 'hidden');
    assert.equal(st.mascotPlacement(planB(), 'right'), 'right'); // not the mascot: unchanged
});

test('layout: boxes, background, title and labels from the plan; the lesson text is never interpreted or changed', () => {
    const { st, doc } = stage();
    st.layout(sceneB(), planB(), 1);
    assert.equal(doc.body.getAttribute('data-cinematic'), 'presenter_plus_visual');
    assert.equal(doc.body.getAttribute('data-cine-visual'), 'side');
    assert.equal(doc.body.getAttribute('data-cine-board'), 'body');
    const root = doc.documentElement.style;
    assert.equal(root.getPropertyValue('--cine-board-x'), '0.3514');
    assert.equal(root.getPropertyValue('--cine-visual-w'), '0.2814');
    assert.equal(root.getPropertyValue('--cine-bg-2'), '#222222');
    const title = doc.getElementById('cine-title');
    assert.equal(title.querySelector('.cine-title-text').textContent, 'Inside the <b>leaf</b>'); // text, never HTML
    assert.equal(title.querySelector('.cine-title-subtitle').textContent, 'Where the sugar is made');
    assert.equal(title.style.left, '5%');
    const chips = doc.querySelectorAll('.cine-label');
    assert.deepEqual(chips.map(c => c.textContent), ['Sunlight → Chlorophyll → Glucose', 'Glucose']); // exactly the screenplay's labels
    assert.ok(chips.every(c => c.getAttribute('data-shown') === 'false'));
    // a background picture from the library: shown under a scrim so the text stays readable
    st.layout(sceneB(), planB({ background: { type: 'image', url: '/api/assets/b/content?token=t', scrim: 0.5 } }), 1);
    const bg = doc.getElementById('cine-background');
    assert.equal(bg.getAttribute('data-type'), 'image');
    assert.equal(bg.querySelector('img').attrs.src || bg.querySelector('img').src, 'https://host/api/assets/b/content?token=t');
    assert.equal(bg.querySelector('.cine-scrim').style.opacity, '0.5');
    st.clear();
    assert.equal(doc.body.getAttribute('data-cinematic'), null);
    assert.equal(doc.getElementById('cine-title').getAttribute('data-state'), 'hidden');
});

test('transitions: the lesson\'s kind; a cut has none; reduced motion slides become fades in the preview only', () => {
    const a = stage();
    assert.equal(a.st.prepareTransition(planB()), 'fade');
    assert.equal(a.doc.documentElement.getAttribute('data-cine-transition'), 'fade');
    assert.equal(a.doc.documentElement.style.getPropertyValue('--cine-transition-seconds'), '0.5s');
    assert.equal(a.st.prepareTransition(planB({ transition: { in: 'cut', out: 'cut', duration: 0 } })), 'cut');
    assert.equal(a.st.prepareTransition(null), null);
    assert.equal(a.doc.documentElement.getAttribute('data-cine-transition'), null);
    const calm = stage({ reduced: true });
    assert.equal(calm.st.prepareTransition(planB({ transition: { in: 'slide', out: 'slide', duration: 0.55 } })), 'fade');
    const recording = stage({ reduced: true, exporting: true });
    assert.equal(recording.st.prepareTransition(planB({ transition: { in: 'slide', out: 'slide', duration: 0.55 } })), 'slide'); // the video follows the plan
});

test('timeline: entrances at their times, the camera from framing to framing, labels on time or at their [SYNC] reveal', () => {
    const { st, doc, clock: c, panel, presenter, zone, side } = stage();
    const scene = sceneB();
    st.layout(scene, planB(), 1);
    st.start(scene, planB({ camera: planB().camera })); // another object: not this scene's plan, ignored
    assert.equal(panel.animations.length, 0);
    const plan = st.plan;
    st.start(scene, plan);
    assert.equal(panel.animations[0].opts.delay, 300); // the visual scales in at 0.3 s
    assert.deepEqual(panel.animations[0].frames[0], { opacity: 0, scale: '0.96' });
    assert.equal(presenter.firstElementChild.animations[0].frames[0].translate, '3vw 0'); // slides in from its own side
    const camZone = zone.animations.find(a => a.opts.fill === 'both');
    assert.ok(camZone, 'the board moves with the camera');
    assert.equal(camZone.opts.delay, 600);
    assert.equal(camZone.frames[0].transform, C.cameraTransform(plan.camera.from, plan.layers[2].box));
    assert.equal(camZone.frames[1].transform, C.cameraTransform(plan.camera.to, plan.layers[2].box));
    assert.ok(side.animations.find(a => a.opts.fill === 'both'), 'the visual moves with the same camera');
    assert.ok(zone.classList.contains('cine-camera'));
    assert.equal(st.stats.cameraMoves, 1);
    const chips = doc.querySelectorAll('.cine-label');
    c.advance(1300);
    assert.equal(chips[0].getAttribute('data-shown'), 'true'); // at 1.2 s
    assert.equal(chips[1].getAttribute('data-shown'), 'false');
    st.narrationEvent('sync'); // the first reveal: the visual is highlighted there
    assert.ok(panel.animations.some(a => a.frames.some(f => f.boxShadow)), 'emphasis on the visual at its [SYNC]');
    st.narrationEvent('sync'); // the second reveal: the second label, before its estimated time
    assert.equal(chips[1].getAttribute('data-shown'), 'true');
    assert.equal(st.stats.anchored, 2);
    st.endScene();
    assert.ok(zone.animations.every(a => a.cancelled || a.opts.fill !== 'both'));
    assert.equal(zone.classList.contains('cine-camera'), false);
});

test('hold and resume: the camera and the timeline wait for the narration', () => {
    const { st, doc, clock: c, zone } = stage();
    const scene = sceneB();
    st.layout(scene, planB(), 1);
    st.start(scene, st.plan);
    const cam = zone.animations.find(a => a.opts.fill === 'both');
    c.advance(500);
    st.narrationEvent('hold');
    assert.equal(cam.playState, 'paused');
    c.advance(5000); // held for 5 s: the label due at 1.2 s is still waiting
    assert.equal(doc.querySelectorAll('.cine-label')[0].getAttribute('data-shown'), 'false');
    st.narrationEvent('resume');
    assert.equal(cam.playState, 'running');
    c.advance(800);
    assert.equal(doc.querySelectorAll('.cine-label')[0].getAttribute('data-shown'), 'true');
});

test('still camera: a static plan, reduced motion in the preview, or motion switched off move nothing', () => {
    const still = stage();
    still.st.layout(sceneB(), planB({ camera: { ...planB().camera, movement: 'static' } }), 1);
    still.st.start(sceneB(), still.st.plan);
    assert.equal(still.zone.animations.filter(a => a.opts.fill === 'both').length, 0);
    const calm = stage({ reduced: true });
    calm.st.layout(sceneB(), planB(), 1);
    calm.st.start(sceneB(), calm.st.plan);
    assert.equal(calm.zone.animations.filter(a => a.opts.fill === 'both').length, 0);
    assert.deepEqual(calm.panel.animations[0].frames, [{ opacity: 0 }, { opacity: 1 }]); // entrances become plain fades
    const off = stage();
    off.st.layout(sceneB(), planB({ style: { ...planB().style, motion: 'none' } }), 1);
    off.st.start(sceneB(), off.st.plan);
    assert.deepEqual(off.panel.animations[0].frames, [{ opacity: 0 }, { opacity: 1 }]);
});

test('formula focus: the camera leans towards the typeset formula as far as the safety rules allow', () => {
    const { st, content, doc } = stage();
    const formula = doc.createElement('div');
    formula.className = 'formula-block';
    formula.rect = { left: 0.1 * 1280, top: 0.25 * 720, width: 0.3 * 1280, height: 0.12 * 720 };
    content.appendChild(formula);
    const plan = planB({ template: 'formula_focus', camera: { ...planB().camera, movement: 'focus', target: 'formula', to: { x: 0.037, y: 0.0247, w: 0.9259 } } });
    st.layout(sceneB(), plan, 3);
    const aimed = st.aimAtFormula(plan, plan.camera.to);
    const { important, forbidden } = C.cameraConstraints(plan);
    assert.equal(C.framingOk(aimed, important, forbidden), true);
    assert.ok(aimed.w >= 1 / 1.08 - 1e-3, 'never further than the plan allowed');
});

test('board text that does not fit is made smaller, never below 80 %', () => {
    const { st, board, doc } = stage();
    st.layout(sceneB(), planB(), 1);
    const scale = () => Number(doc.documentElement.style.getPropertyValue('--cine-text-scale') || 1);
    Object.defineProperty(board, 'clientHeight', { get: () => 400, configurable: true });
    Object.defineProperty(board, 'scrollHeight', { get: () => 460 * scale(), configurable: true });
    assert.equal(st.fit(), 0.84); // 460 × 0.88 ≈ 405 is still too tall; 460 × 0.84 ≈ 386 fits
    assert.equal(st.stats.overflow, false);
    Object.defineProperty(board, 'scrollHeight', { get: () => 900 * scale(), configurable: true });
    assert.equal(st.fit(), 0.8); // far too long: 80 % is the floor, and the overflow is reported
    assert.equal(st.stats.overflow, true);
});

test('inspector words and element states (ready, generating, needs attention)', () => {
    const facts = Object.fromEntries(C.inspectorFacts(planB()));
    assert.equal(facts.Template, 'Presenter + visual');
    assert.equal(facts.Presenter, 'aadhi-teacher · right');
    assert.equal(facts.Camera, 'Medium · slow zoom in → visual');
    assert.equal(facts.Transition, 'Fade');
    assert.equal(facts.Duration, '12 s');
    const scene = { ...sceneB(), visual_plan: { side: { source: 'AI_IMAGE', requires_generation: true } } };
    const rows = C.elementStatuses(scene, planB({ background: { type: 'gradient', fallback: true } }), { activeRuns: [] });
    const state = Object.fromEntries(rows.map(([n, s]) => [n, s]));
    assert.deepEqual(state, { Background: 'warning', Presenter: 'ready', 'Educational visual': 'warning', Text: 'ready' });
    const generating = C.elementStatuses(sceneB(), planB(), { activeRuns: [{ slot: 'background', status: 'running' }] });
    assert.equal(generating[0][1], 'generating');
    const ai = C.elementStatuses({ ...sceneB(), presenter_plan: { type: 'ai_avatar' } }, planB({ presenter: { type: 'ai_avatar', presenter_id: 'ai-teacher', shown: true, side: 'right' } }));
    assert.deepEqual(ai[1], ['Presenter', 'warning', 'No presenter clip yet']);
    assert.deepEqual(C.inspectorFacts(null), []);
});

test('settings panel: scene style, background from the library, an AI background only when asked', async () => {
    const doc = fakeDoc();
    const container = doc.createElement('div');
    const store = {}; const storage = { getItem: k => store[k] || null, setItem: (k, v) => { store[k] = v; } };
    const calls = []; const changes = [];
    let picked = null;
    const api = { background: async body => { calls.push(body); return calls.length === 1 ? { asset_id: 'a'.repeat(32), provider: 'fake', cache_hit: false } : { asset_id: 'a'.repeat(32), cache_hit: true }; } };
    const panel = new C.CinematicSettingsPanel({ doc, container, api, storage, onChange: s => changes.push({ ...s }), pickBackground: p => { picked = p; },
        presenterIsMascot: () => true });
    panel.render();
    assert.ok(container.querySelector('#cinematic-mode'));
    assert.equal(container.querySelector('#cinematic-background'), null); // Classic: nothing else to choose
    // Phase 21: Classic in plain words, and the disabled transitions say why next to the control
    assert.ok(container.querySelectorAll('.cinematic-note').some(n => n.textContent === "Classic: Aadhi's original layout. Choose Cinematic to use a video style and scene transitions."));
    assert.equal(container.querySelector('#cinematic-transition').getAttribute('disabled'), '');
    assert.equal(container.querySelector('#cinematic-transition').getAttribute('aria-describedby'), 'cinematic-transition-why');
    assert.equal(container.querySelector('#cinematic-transition-why').textContent, 'Needs the Cinematic layout.');
    panel.set('mode', 'cinematic');
    assert.ok(container.querySelector('#cinematic-background'));
    assert.equal(container.querySelector('#cinematic-transition').getAttribute('disabled'), null);
    assert.equal(container.querySelector('#cinematic-transition').getAttribute('aria-describedby'), null);
    assert.equal(container.querySelector('#cinematic-transition-why'), null);
    assert.doesNotMatch(container.textContent, /Asset Library|original layout/);
    assert.ok(container.textContent.includes('camera stays still while Aadhi is on screen'));
    assert.deepEqual(['image', 'video'].map(v => container.querySelector('#cinematic-background').children.find(o => o.value === v).textContent),
        ['Picture from your Library', 'Clip from your Library']);
    panel.set('background', 'image');
    assert.equal(container.querySelector('[data-action="pick-background"]').textContent, 'Choose from your Library');
    container.querySelector('[data-action="pick-background"]').fire('click');
    assert.deepEqual(picked.kinds, ['image']);
    picked.onPick({ id: 'b'.repeat(32), file_name: 'chalkboard.png' });
    assert.equal(panel.settings.background_asset_id, 'b'.repeat(32));
    assert.ok(container.textContent.includes("Aadhi is filmed in his studio"));
    panel.set('background', 'ai');
    assert.equal(panel.settings.background_asset_id, null);
    assert.equal(calls.length, 0); // choosing "AI background" generates nothing by itself
    await panel.generateBackground(false);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].force_regenerate, false);
    assert.equal(panel.settings.background_asset_id, 'a'.repeat(32));
    await panel.generateBackground(false);
    assert.ok(panel.status.includes('Reused the background'));
    const failing = new C.CinematicSettingsPanel({ doc, container: doc.createElement('div'), storage, api: { background: async () => { throw new Error('AI generation is switched off'); } } });
    await failing.generateBackground();
    assert.ok(failing.status.includes('No AI background') && failing.status.includes('clean gradient'));
    assert.equal(JSON.parse(store['aadhi.cinematic']).mode, 'cinematic');
    assert.ok(changes.length >= 4);
});

test('Phase 21: the settings panel speaks plain words; the provider, server reasons and raw errors only with debug', async () => {
    const doc = fakeDoc();
    const store = {}; const storage = { getItem: k => store[k] || null, setItem: (k, v) => { store[k] = v; } };
    const status = { ai_available: true, providers: { gemini: { available: false, reason: 'no Gemini API key is configured on this server' } } };
    const make = (debug, api = {}) => new C.CinematicSettingsPanel({ doc, container: doc.createElement('div'), storage, api, status, directorStatus: status, debug });
    const normal = make(false);
    normal.set('mode', 'cinematic');
    const labels = normal.container.querySelectorAll('label').map(l => l.textContent);
    ['Layout', 'Scene transitions', 'Background', 'Camera movement', 'Learners'].forEach(l => assert.ok(labels.includes(l), l));
    assert.deepEqual(normal.container.querySelector('#cinematic-mode').children.map(o => o.textContent), ['Classic', 'Cinematic']);
    assert.deepEqual(normal.container.querySelector('#cinematic-motion').children.map(o => [o.value, o.textContent]), [['subtle', 'Gentle movement'], ['none', 'Still camera']]);
    normal.update({ composer: 'ai', director: 'ai' });
    const text = normal.container.textContent;
    assert.ok(text.includes('AI-assisted composition is not available on this server: the automatic rules decide every scene.'));
    assert.ok(text.includes('AI-assisted visual direction is not available on this server: the automatic rules decide how every scene teaches.'));
    assert.doesNotMatch(text, /Gemini|API key|text model/);
    const debug = make(() => true);
    debug.update({ mode: 'cinematic', composer: 'ai', director: 'ai' });
    assert.match(debug.container.textContent, /composition is not available on this server \(no Gemini API key is configured on this server\): the automatic rules/);
    assert.match(debug.container.textContent, /visual direction is not available on this server \(no Gemini API key is configured on this server\)/);
    // available: the AI is described without the "text model" jargon
    const ready = new C.CinematicSettingsPanel({ doc, container: doc.createElement('div'), storage, status: { providers: { gemini: { available: true } } } });
    ready.update({ mode: 'cinematic', composer: 'ai' });
    assert.ok(ready.container.textContent.includes('The AI is asked only about scenes where several elements compete'));
    // an AI background: made by whom only in debug; a failure says the lesson is safe and what to do, the server's words in debug
    const made = { background: async () => ({ asset_id: 'a'.repeat(32), provider: 'fake-images', cache_hit: false }) };
    const plain = make(false, made);
    await plain.generateBackground();
    assert.equal(plain.status, 'Background generated and added to the lesson.');
    const told = make(true, made);
    await told.generateBackground();
    assert.equal(told.status, 'Background generated and added to the lesson (by fake-images).');
    const broken = { background: async () => { throw new Error('ProviderError: 402 Payment Required'); } };
    const failing = make(false, broken);
    await failing.generateBackground();
    assert.equal(failing.status, 'No AI background was made. Your lesson is safe: the clean gradient is used instead. Try again later, or choose a picture from your Library.');
    const failingDebug = make(true, broken);
    await failingDebug.generateBackground();
    assert.ok(failingDebug.status.endsWith(' (ProviderError: 402 Payment Required)'));
    // a library picture without a file name is never named by its asset id
    let pick = null;
    const lib = new C.CinematicSettingsPanel({ doc, container: doc.createElement('div'), storage, pickBackground: p => { pick = p; } });
    lib.update({ mode: 'cinematic', background: 'image' });
    lib.chooseBackground();
    pick.onPick({ id: 'c'.repeat(32) });
    assert.equal(lib.settings.background_label, 'chosen picture');
    assert.ok(lib.container.querySelector('[data-action="pick-background"]').textContent.startsWith('Change picture'));
    assert.doesNotMatch(lib.container.textContent, /cccc/);
});

test('Phase 21: the style picker is one radio group (one Tab stop, arrow keys) and a choice keeps the keyboard focus', () => {
    const doc = fakeDoc();
    const store = {}; const storage = { getItem: k => store[k] || null, setItem: (k, v) => { store[k] = v; } };
    const focused = [];
    const create = doc.createElement;
    doc.createElement = tag => { const n = create(tag); n.focus = () => { doc.activeElement = n; focused.push(n); }; return n; };
    const container = doc.createElement('div');
    container.contains = node => container.all().includes(node);
    const panel = new C.CinematicSettingsPanel({ doc, container, storage });
    const draw = () => panel.render();
    panel.update({ mode: 'cinematic' });
    const options = () => container.querySelectorAll('.style-option');
    const tabs = () => options().map(o => o.getAttribute('tabindex'));
    assert.deepEqual(tabs(), ['0', '-1', '-1', '-1'], 'no style yet: the first one is the Tab stop');
    assert.ok(options().every(o => o.classList.contains('ui-focusable')));
    assert.ok(container.querySelectorAll('select').every(s => s.classList.contains('ui-focusable')));
    // ArrowRight from the first: the second is chosen and focused (the panel was drawn again)
    let stopped = 0;
    const key = (node, k) => (node.listeners.keydown || []).forEach(fn => fn({ key: k, target: node, preventDefault() {}, stopPropagation() { stopped += 1; } }));
    key(options()[0], 'ArrowRight');
    assert.equal(panel.settings.style, 'cinematic_education');
    assert.deepEqual(tabs(), ['-1', '0', '-1', '-1']);
    assert.equal(doc.activeElement, container.querySelector('.style-option[data-style="cinematic_education"]'));
    assert.equal(stopped, 1, 'the lesson\'s own arrow keys do not see it');
    key(options()[1], 'ArrowLeft');
    assert.equal(panel.settings.style, 'academic');
    key(options()[0], 'ArrowUp'); // wraps around
    assert.equal(panel.settings.style, 'corporate_training');
    key(options()[3], 'Enter'); // not an arrow: nothing
    assert.equal(panel.settings.style, 'corporate_training');
    // a select changed with the keyboard keeps the focus after the panel is drawn again
    const motion = container.querySelector('#cinematic-motion');
    doc.activeElement = motion;
    motion.value = 'none';
    motion.fire('change');
    assert.equal(panel.settings.motion, 'none');
    assert.equal(doc.activeElement, container.querySelector('#cinematic-motion'));
    assert.notEqual(doc.activeElement, motion, 'the new select, not the removed one');
    // the focus elsewhere on the page: drawing the panel takes nothing
    doc.activeElement = doc.body;
    const before = focused.length;
    draw();
    assert.equal(focused.length, before);
});

// ---- Phase 21: captions that fit their band --------------------------------------------------------------------------

test('captionFit: a caption taller than its band is lowered first, then made smaller (never below 85 %); what fits stays', () => {
    const same = { drop: 0, scale: 1 };
    // 720p (band: 15 % = 108 px). One line (1.8rem ≈ 28.8 px, line ≈ 36 px) 60 px above the bottom, lifted 5 px: 101 px, fits
    assert.deepEqual(C.captionFit({ viewportH: 720, bottom: 60, height: 36.3, lift: 5 }), same);
    // one line in the box style (60 px − 0.3em, padding 0.3em): 110 px, within the 4 px slack
    assert.deepEqual(C.captionFit({ viewportH: 720, bottom: 51.36, height: 53.6 }), same);
    // two lines in the box style (146 px): lowered 38 px, the font untouched; its bottom stays above the 10.8 px gutter
    const two = C.captionFit({ viewportH: 720, bottom: 51.36, height: 89.9 });
    assert.equal(two.scale, 1);
    assert.ok(Math.abs(two.drop - 38) < 0.5, `${two.drop}`);
    assert.ok(51.36 - two.drop >= 720 * 0.015);
    // two lines with the shadow style (60 + 72.6 + 5 = 137.6 px): lowered to the band's top
    assert.deepEqual(C.captionFit({ viewportH: 720, bottom: 60, height: 72.6 }), { drop: 29.6, scale: 1 });
    // 1080p (band: 162 px): every two-line caption, large ones too, stays exactly where it is
    assert.deepEqual(C.captionFit({ viewportH: 1080, bottom: 60, height: 72.6 }), same);
    assert.deepEqual(C.captionFit({ viewportH: 1080, bottom: 51.36, height: 89.9 }), same);
    assert.deepEqual(C.captionFit({ viewportH: 1080, bottom: 49.6, height: 107.8 }), same);
    // three lines (shadow style) at 720p: lowered to the gutter, then made smaller, never below 85 %
    const three = C.captionFit({ viewportH: 720, bottom: 60, height: 109 });
    assert.deepEqual(three, { drop: 49.2, scale: 0.85 });
    // a large two-line caption in the box style: lowered to the gutter, then a little smaller (just what is needed)
    const large = C.captionFit({ viewportH: 720, bottom: 49.6, height: 107.8 });
    assert.equal(large.drop, 38.8);
    assert.ok(large.scale >= 0.85 && large.scale < 1, `${large.scale}`);
    assert.ok(Math.abs((49.6 - large.drop) + 107.8 * large.scale + 5 - 108) < 0.2, 'its top lands on the band');
    // the gutter is 8 px on a small frame
    assert.equal(C.captionFit({ viewportH: 400, bottom: 60, height: 80 }).drop, 52);
    // input it cannot use: unchanged
    [undefined, {}, { viewportH: 0, bottom: 60, height: 80 }, { viewportH: 720, bottom: 60, height: 0 }, { viewportH: 720, bottom: NaN, height: 80 },
        { viewportH: '720', bottom: 60, height: 200 }, { viewportH: 720, bottom: -5, height: 200 }, { viewportH: 720, bottom: 60, height: 200, lift: -1 }]
        .forEach(input => assert.deepEqual(C.captionFit(input), same, JSON.stringify(input)));
});

// A caption track whose layout follows stage-fit.css: its bottom = 60 px − drop, its height = its natural height × the font's fit
function captionPage({ height = 72.6, viewportH = 720, cinematic = true } = {}) {
    const doc = fakeDoc();
    const track = doc.createElement('div');
    track.id = 'subtitle-track';
    doc.body.appendChild(track);
    if (cinematic) doc.body.setAttribute('data-cinematic', 'presenter_explanation');
    const page = { doc, track, natural: height, writes: [] };
    const props = {};
    const style = track.style = { _props: props, getPropertyValue: k => props[k] || '', removeProperty: k => { delete props[k]; },
        setProperty: (k, v) => { page.writes.push([k, v]); props[k] = String(v); } };
    Object.defineProperty(track, 'offsetHeight', { configurable: true, get: () => page.natural * Number(style.getPropertyValue('--cine-caption-fit') || 1) });
    doc.documentElement.clientHeight = viewportH;
    const listeners = {};
    doc.defaultView = { innerHeight: viewportH, getComputedStyle: node => ({ bottom: `${60 - parseFloat(node.style.getPropertyValue('--cine-caption-drop') || 0)}px` }),
        addEventListener: (t, fn) => { listeners[t] = fn; }, removeEventListener: t => { delete listeners[t]; }, requestAnimationFrame: fn => { page.frame = fn; } };
    page.resize = h => { doc.documentElement.clientHeight = h; listeners.resize(); };
    page.observers = [];
    page.RO = class { constructor(fn) { this.fn = fn; this.watching = []; page.observers.push(this); }
        observe(el, opts) { this.watching.push(el); this.opts = opts; } unobserve(el) { this.watching = this.watching.filter(x => x !== el); }
        disconnect() { this.watching = []; } report() { if (this.watching.length) this.fn([{ target: track }]); } };
    page.MO = class { constructor(fn) { page.mutate = fn; } observe(el, opts) { page.moOpts = opts; } disconnect() { page.mutate = null; } };
    page.vars = () => [style.getPropertyValue('--cine-caption-drop'), style.getPropertyValue('--cine-caption-fit')];
    return page;
}

test('watchCaptions: the track gets the fit as inline variables when its size changes; Classic, 1080p and one line keep it unset', () => {
    const p = captionPage({ height: 72.6 }); // two lines, 720p
    const watch = C.watchCaptions(p.doc, { ResizeObserver: p.RO, MutationObserver: p.MO });
    assert.ok(watch);
    assert.deepEqual(p.vars(), ['29.6px', ''], 'lowered at once; the font untouched');
    assert.deepEqual(p.observers[0].opts, { box: 'border-box' });
    assert.deepEqual(p.moOpts.attributeFilter, ['data-cinematic', 'data-cine-caption']);
    assert.equal(C.watchCaptions(p.doc, { ResizeObserver: p.RO, MutationObserver: p.MO }), watch, 'once per track');
    // a one-line caption: back to its own place (the variables removed, not set to 0)
    p.natural = 36.3;
    p.observers[0].report();
    assert.deepEqual(p.vars(), ['', '']);
    assert.ok(!('--cine-caption-drop' in p.track.style._props));
    // three lines: lowered and smaller; the observer steps back for a frame (no resize loop), then watches again
    p.natural = 109;
    p.observers[0].report();
    assert.deepEqual(p.vars(), ['49.2px', '0.85']);
    assert.equal(p.observers[0].watching.length, 0);
    p.frame();
    assert.deepEqual(p.observers[0].watching, [p.track]);
    const writes = p.writes.length;
    p.observers[0].report(); // the first report after watching again: measured at its own size, the same fit, no change
    assert.deepEqual(p.vars(), ['49.2px', '0.85']);
    assert.equal(p.observers[0].watching.length, 1, 'still watched');
    assert.ok(p.writes.slice(writes).every(([k, v]) => (k === '--cine-caption-drop' ? v === '49.2px' : v === '0.85')), 'written again unchanged');
    // the page made taller (1080p): nothing has to move any more
    p.natural = 72.6;
    p.resize(1080);
    assert.deepEqual(p.vars(), ['', '']);
    // Classic (the scene style changed): nothing is set
    p.resize(720);
    assert.deepEqual(p.vars(), ['29.6px', '']);
    p.doc.body.removeAttribute('data-cinematic');
    p.mutate();
    assert.deepEqual(p.vars(), ['', '']);
    p.natural = 109;
    p.observers[0].report();
    assert.deepEqual(p.vars(), ['', ''], 'Classic: the caption is never measured or moved');
    watch.stop();
    assert.equal(p.mutate, null);
    // 1080p from the start: two lines stay exactly where they are, nothing written
    const big = captionPage({ height: 72.6, viewportH: 1080 });
    C.watchCaptions(big.doc, { ResizeObserver: big.RO, MutationObserver: big.MO });
    assert.deepEqual(big.vars(), ['', '']);
    assert.deepEqual(big.writes, []);
});

test('watchCaptions: nothing happens without a track, a ResizeObserver or layout to measure (the tests\' stub track stays untouched)', () => {
    const p = captionPage();
    assert.equal(C.watchCaptions(p.doc, { MutationObserver: p.MO }), null, 'no ResizeObserver');
    assert.deepEqual(p.writes, []);
    assert.equal(C.watchCaptions(null), null);
    assert.equal(C.watchCaptions(fakeDoc(), { ResizeObserver: p.RO }), null, 'no #subtitle-track');
    // a stand-in track with a plain style object (tests/playback_edit.test.js): left alone
    const stub = { dataset: {}, style: { visibility: '' }, textContent: 'An earlier line.' };
    const doc = { getElementById: id => (id === 'subtitle-track' ? stub : null), defaultView: { getComputedStyle: () => ({ bottom: '60px' }) }, body: null };
    assert.equal(C.watchCaptions(doc, { ResizeObserver: p.RO }), null);
    assert.deepEqual(stub.style, { visibility: '' });
    // layout it cannot read (no offsetHeight): the caption keeps what it had
    const blind = captionPage();
    Object.defineProperty(blind.track, 'offsetHeight', { get: () => undefined });
    const watch = C.watchCaptions(blind.doc, { ResizeObserver: blind.RO, MutationObserver: blind.MO });
    assert.ok(watch);
    assert.deepEqual(blind.vars(), ['', '']);
    assert.deepEqual(blind.writes, []);
});

test('API client: the composition endpoints and honest errors', async () => {
    const sent = [];
    const api = new C.CinematicApi({ fetch: async (url, init) => { sent.push([url, init && init.body ? JSON.parse(init.body) : null]);
        return url.endsWith('/background') ? { ok: false, status: 403, json: async () => ({ detail: 'AI generation is switched off on this server.' }) }
            : { ok: true, status: 200, json: async () => ({ plans: [] }) }; } });
    await api.plan([{ title: 'a' }], { mode: 'cinematic' }, 7);
    assert.deepEqual(sent[0], ['/api/cinematic/plan', { scenes: [{ title: 'a' }], settings: { mode: 'cinematic' }, project_id: 7 }]);
    await api.review({ scene_index: 1, action: 'keep' });
    assert.equal(sent[1][0], '/api/cinematic/review');
    await assert.rejects(() => api.background({}), /switched off/);
});

// ---- Visual Review: the composition item ----------------------------------------------------------------------------

test('Visual Review: a composition item per scene, decided through its own endpoint; approvals of older compositions go stale', async () => {
    const slides = [{ ...sceneB(), cinematic_plan: planB() }, { title: 'Old scene' }, { ...sceneB(), cinematic_plan: planB({ review_status: 'pending', review_stale: true }) }];
    assert.equal(R.reviewItems(slides, false, false).length, 0);
    const items = R.reviewItems(slides, false, true);
    assert.deepEqual(items.map(i => [i.sceneIndex, i.slot]), [[0, 'composition'], [2, 'composition']]);
    assert.equal(items[1].stale, true);
    const bodies = [];
    const session = new R.ReviewSession({ slides, api: {}, media: {}, composition: {
        enabled: () => true,
        review: async body => { bodies.push(body); return { review: body.action === 'reset' ? null : { status: body.action === 'keep' ? 'approved' : 'changed', overrides: body.overrides },
            plan: planB({ review_status: body.action === 'reset' ? 'pending' : body.action === 'keep' ? 'approved' : 'changed', template: body.overrides ? body.overrides.template : 'presenter_plus_visual' }) }; },
        facts: C.inspectorFacts, statuses: C.elementStatuses } });
    assert.equal(session.items.length, 2);
    assert.equal(session.canGenerate(), false);
    assert.equal(await session.generate({ force: true }), null); // a composition is arranged, never generated
    await session.keep();
    assert.equal(slides[0].visual_review.composition.status, 'approved');
    assert.equal(session.current.status, 'approved');
    await session.decide('change', { overrides: { template: 'diagram_focus' } });
    assert.equal(slides[0].cinematic_plan.template, 'diagram_focus');
    assert.equal(session.message, 'Changed: the scene is composed your way.');
    await session.reset();
    assert.equal(slides[0].visual_review, undefined);
    assert.deepEqual(bodies.map(b => b.action), ['keep', 'change', 'reset']);
    const off = new R.ReviewSession({ slides, api: {}, media: {}, composition: { enabled: () => false } });
    assert.equal(off.items.length, 0); // Classic: nothing to review
});

test('Visual Review: the scene inspector draws the composition and lists its elements', () => {
    const doc = fakeDoc();
    const slides = [{ ...sceneB(), cinematic_plan: planB({ notes: ['the visual needs the stage'] }) }];
    const session = new R.ReviewSession({ slides, api: {}, media: {}, composition: { enabled: () => true, review: async () => ({}), facts: C.inspectorFacts,
        statuses: C.elementStatuses, options: { template: C.TEMPLATES, camera: C.CAMERA } } });
    const shown = [];
    const panel = new R.VisualReviewPanel({ doc, session, pickAsset: () => {}, showInLesson: i => shown.push(i) });
    panel.build();
    panel.render();
    const frame = panel.detail.querySelector('.review-composition-frame');
    assert.ok(frame);
    const layers = frame.querySelectorAll('.review-composition-box').map(b => b.getAttribute('data-layer'));
    assert.deepEqual(layers, ['visual', 'board', 'title', 'labels', 'presenter', 'subtitles']);
    assert.equal(frame.querySelector('[data-layer="presenter"]').style.left, '71%');
    assert.ok(frame.querySelector('.review-composition-camera'), 'where the camera ends is drawn');
    const elements = panel.detail.querySelectorAll('.review-composition-elements li').map(li => li.getAttribute('data-element'));
    assert.deepEqual(elements, ['Background', 'Presenter', 'Educational visual', 'Text']);
    assert.ok(panel.detail.textContent.includes('the visual needs the stage'));
    assert.ok(panel.detail.querySelector('#composition-template'));
    panel.detail.querySelector('[data-action="show"]').fire('click');
    assert.deepEqual(shown, [0]);
});

test('Phase 20: a display formula or table wider than its place on the board is made smaller to fit it (never below 45 %), not cut off', () => {
    // browser check finding: a long formula (704 px) was cut off in a 349 px board box beside the presenter; a first fit that
    // scaled once in proportion still overflowed (MathJax's sizes and a table's padding do not shrink in proportion), so the fit
    // now goes in steps until the box no longer overflows. Stand-ins: widths follow the font size, plus a fixed part.
    const pct = node => (node.style.fontSize ? parseFloat(node.style.fontSize) / 100 : 1);
    const pad = { paddingLeft: '10px', paddingRight: '10px' };
    // a formula block (it scrolls sideways): its content is 60 px of fixed margins plus 644 px of formula at full size
    const block = { style: { fontSize: '90%' }, clientWidth: 349, get scrollWidth() { return Math.max(this.clientWidth, 60 + 644 * pct(this)); } };
    const formula = { style: { fontSize: '119%' }, closest: sel => (sel === '.formula-block' ? block : null), parentElement: block };
    // a table in a box with 351 px of room: 80 px of cell padding plus 309 px of text at full size
    const tbox = { clientWidth: 371 };
    const table = { style: {}, closest: () => null, parentElement: tbox, get scrollWidth() { return 80 + 309 * pct(this); } };
    const fits = { style: { fontSize: '80%' }, closest: () => null, parentElement: { clientWidth: 400 }, get scrollWidth() { return 300; } };
    const hugeBlock = { style: {}, clientWidth: 349, get scrollWidth() { return 40 + 4000 * pct(this); } };
    const huge = { style: {}, closest: () => hugeBlock, parentElement: hugeBlock };
    const doc = { defaultView: { getComputedStyle: () => pad } };
    const st = new C.CinematicStage({ doc, settings: () => ({ ...C.DEFAULTS, mode: 'cinematic' }) });
    const content = { querySelectorAll: sel => { assert.equal(sel, 'mjx-container[display="true"], table'); return [formula, table, fits, huge]; } };
    assert.equal(st.fitWidth(content), 3);
    assert.equal(block.style.fontSize, '45%', 'the formula is made smaller through its block (the first step that fits: 60 + 644 × 0.45 = 350)');
    assert.ok(block.scrollWidth <= block.clientWidth + 1, 'nothing overflows');
    assert.equal(formula.style.fontSize, '119%', "MathJax's own scale on the formula is kept");
    assert.equal(table.style.fontSize, '85%', 'the table, in steps, until it fits its 351 px (80 + 309 × 0.85 = 343)');
    assert.equal(fits.style.fontSize, '', 'what fits is drawn at its own size (an earlier fit is undone)');
    assert.equal(hugeBlock.style.fontSize, '45%', 'never below 45 %');
    assert.equal(st.stats.widthFitted, 3);
    assert.equal(st.fitWidth({}), 0, 'no content to measure: nothing changes');
});

test('stage-fit.css holds only the caption fit, for cinematic scenes, and with the variables unset gives today\'s place and size', () => {
    const css = require('fs').readFileSync(require('path').join(__dirname, '..', 'stage-fit.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
    const rules = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map(m => ({ selector: m[1].trim(), body: m[2] }));
    assert.deepEqual(rules.map(r => r.selector), ['body[data-cinematic] #subtitle-track', 'body[data-cinematic][data-cine-caption="box"] #subtitle-track']);
    const decl = r => Object.fromEntries(r.body.split(';').map(d => d.trim()).filter(Boolean).map(d => [d.slice(0, d.indexOf(':')).trim(), d.slice(d.indexOf(':') + 1).trim()]));
    // exactly these two: no line-breaking rule, so a caption that fits is drawn (and recorded) exactly as before Phase 21
    // (review finding: text-wrap / overflow-wrap here re-wrapped every recorded two-line caption)
    assert.deepEqual(decl(rules[0]), { bottom: 'calc(60px - var(--cine-caption-drop, 0px))', 'font-size': 'calc(var(--st-caption-size, 1.8rem) * var(--cine-caption-fit, 1))' });
    assert.deepEqual(decl(rules[1]), { bottom: 'calc(60px - 0.3em - var(--cine-caption-drop, 0px))' });
    // the only variables it reads: the fit's two and the style's caption size (whose value styles.py keeps)
    assert.deepEqual([...new Set([...css.matchAll(/var\((--[a-z-]+)/g)].map(m => m[1]))].sort(), ['--cine-caption-drop', '--cine-caption-fit', '--st-caption-size']);
});
