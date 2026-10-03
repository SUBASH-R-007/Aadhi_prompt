'use strict';
// Unit tests for the Intelligent Scene Composer in the page (Phase 14): how a composition was decided (cinematic.js),
// the scene inspector's automatic display, reasons, override controls with "Automatic" and regeneration (review.js),
// and the Composition setting on the start screen.
// Run from the repo root:  node --test "tests/*.test.js"
const test = require('node:test');
const assert = require('node:assert/strict');
const C = require('../cinematic.js');
const R = require('../review.js');
const { fakeDoc } = require('./helpers/cinematic-dom.js');

const box = (x, y, w, h) => ({ x, y, w, h });
function plan(extra = {}) {
    return {
        version: 1, template: 'diagram_focus', template_label: 'Diagram focus', duration: 9, shot: 'visual',
        style: { typography: 'academic', motion: 'subtle', transitions: 'fade', background: 'auto', emphasis: 'clear' },
        background: { type: 'gradient' }, presenter: { type: 'illustrated', presenter_id: 'aadhi-teacher', shown: true, side: 'right' },
        transition: { in: 'fade', out: 'fade', duration: 0.5 },
        camera: { shot: 'visual', movement: 'focus', from: { x: 0, y: 0, w: 1 }, to: { x: 0.03, y: 0.03, w: 0.94 }, start: 1, duration: 3.6, target: 'visual' },
        layers: [
            { id: 'visual', type: 'visual', role: 'image', box: box(0.05, 0.19, 0.35, 0.52), z: 20, start: 0.15, enter: 'scale_in', important: true, camera: true },
            { id: 'board', type: 'board', role: 'body', style: 'body', box: box(0.42, 0.19, 0.53, 0.31), z: 30, start: 0.1, enter: 'fade_in', important: true, camera: true },
            { id: 'presenter', type: 'presenter', side: 'right', placement: 'pip', box: box(0.8, 0.52, 0.15, 0.28), z: 40, start: 0.5, enter: 'slide_in', important: true, camera: true },
            { id: 'subtitles', type: 'subtitles', box: box(0, 0.85, 1, 0.15), z: 90, start: 0, important: false, camera: false }
        ],
        timeline: [], warnings: [], notes: [], review_status: 'pending',
        composition: { version: 1, mode: 'rules', source: 'rules', decision: { template: 'diagram_focus', presenter_role: 'small', visual_role: 'dominant' },
            locked: [], reasons: ['the diagram is what is taught: it gets the largest area', 'the presenter stays small in the corner'], repairs: [],
            intent: { purpose: 'diagram', density: 'low', priority: ['visual', 'text', 'presenter'] } },
        ...extra
    };
}

test('how a composition was decided: automatic, AI-assisted, your choice, the safe layout', () => {
    const auto = C.compositionSummary(plan());
    assert.equal(auto.label, 'Automatic ✓');
    assert.equal(auto.automatic, true);
    assert.equal(auto.template, 'Diagram focus');
    assert.deepEqual(auto.reasons, plan().composition.reasons);
    const ai = C.compositionSummary(plan({ composition: { ...plan().composition, source: 'ai', ai: { status: 'repaired', provider: 'fake' } } }));
    assert.equal(ai.label, 'AI-assisted ✓');
    assert.match(ai.ai, /after one repair/);
    const rejected = C.compositionSummary(plan({ composition: { ...plan().composition, ai: { status: 'invalid' } } }));
    assert.match(rejected.ai, /rejected.*rules decided/);
    const offPlan = plan({ composition: { ...plan().composition, ai: { status: 'unavailable', error: 'no Gemini API key is configured' } } });
    // Phase 21: the server's reason (it names the provider) only with ?visualDebug; plain words otherwise
    assert.match(C.compositionSummary(offPlan, { debug: true }).ai, /not available here \(no Gemini API key is configured\)/);
    assert.match(C.compositionSummary(offPlan, { debug: () => true }).ai, /\(no Gemini API key is configured\)/);
    const off = C.compositionSummary(offPlan);
    assert.equal(off.ai, 'AI-assisted composition is not available here; the rules decided');
    assert.doesNotMatch(off.ai, /Gemini|API key|\(/);
    const user = C.compositionSummary(plan({ composition: { ...plan().composition, source: 'user', locked: ['template'] } }));
    assert.equal(user.automatic, false);
    assert.equal(user.label, 'Your choice');
    assert.equal(C.compositionSummary(plan({ composition: { ...plan().composition, source: 'fallback', repairs: ['the camera stays still'] } })).label, 'Safe layout');
    assert.equal(C.compositionSummary({ template: 'quiz' }), null); // a Phase 13 plan without a composition record
    const facts = Object.fromEntries(C.inspectorFacts(plan()));
    assert.equal(facts.Composition, 'Automatic ✓ · Diagram focus');
    assert.equal(facts.Scene, 'diagram · low density');
});

function inspector(slides, adapterExtra = {}) {
    const doc = fakeDoc();
    const calls = [];
    const session = new R.ReviewSession({ slides, api: {}, media: {}, composition: {
        enabled: () => true, facts: C.inspectorFacts, summary: C.compositionSummary, statuses: C.elementStatuses,
        review: async body => { calls.push(['review', body]); return { review: { status: 'changed', overrides: body.overrides }, plan: plan({ review_status: 'changed',
            composition: { ...plan().composition, source: 'user', locked: Object.keys(body.overrides || {}) } }) }; },
        regenerate: async i => { calls.push(['regenerate', i]); return { plan: plan({ template: 'presenter_plus_visual', template_label: 'Presenter + visual' }), review: null }; },
        options: { template: C.TEMPLATES, presenter_size: C.PRESENTER_SIZES, visual_size: C.VISUAL_SIZES, camera: C.CAMERA, motion: C.MOTION_LEVELS },
        ...adapterExtra } });
    const panel = new R.VisualReviewPanel({ doc, session, pickAsset: () => {}, showInLesson: () => {} });
    panel.build();
    panel.render();
    return { doc, session, panel, calls };
}

test('the scene inspector shows the automatic composition with its short reasons (never model reasoning)', () => {
    const slides = [{ title: 'Inside a plant', cinematic_plan: plan() }];
    const { panel } = inspector(slides);
    const decided = panel.detail.querySelector('.review-composition-decided');
    assert.ok(decided);
    assert.equal(decided.getAttribute('data-source'), 'rules');
    assert.equal(panel.detail.querySelector('.review-composition-auto').textContent, 'Layout: Automatic ✓ · Diagram focus'); // (Phase 21: the Layout row's word)
    const reasons = panel.detail.querySelectorAll('.review-composition-reasons li').map(li => li.textContent);
    assert.deepEqual(reasons, plan().composition.reasons);
    assert.equal(panel.detail.querySelector('.review-composition-repairs'), null);
    const repaired = inspector([{ title: 'x', cinematic_plan: plan({ composition: { ...plan().composition, repairs: ['the presenter made small to make room'] } }) }]);
    assert.match(repaired.panel.detail.querySelector('.review-composition-repairs').textContent, /Adjusted: the presenter made small/);
});

test('override controls: presenter size, visual size, motion; "Automatic" only where the user chose something', async () => {
    const slides = [{ title: 'Inside a plant', cinematic_plan: plan() }];
    const { panel, calls, session } = inspector(slides);
    for (const key of ['template', 'presenter_size', 'visual_size', 'camera', 'motion']) assert.ok(panel.detail.querySelector(`#composition-${key}`), key);
    const size = panel.detail.querySelector('#composition-presenter_size');
    const values = size.children.map(o => o.attrs.value);
    assert.equal(values.includes('auto'), false); // nothing chosen yet: the composer decides, no "Automatic" to go back to
    assert.match(size.children[0].textContent, /\(automatic\)/);
    size.value = 'hidden';
    panel.detail.querySelector('#composition-motion').value = 'none';
    panel.detail.querySelector('[data-action="apply"]').fire('click');
    await new Promise(r => setTimeout(r, 0));
    assert.deepEqual(calls[0], ['review', { scene_index: 0, action: 'change', overrides: { presenter_size: 'hidden', motion: 'none' } }]);
    assert.equal(slides[0].visual_review.composition.status, 'changed');
    panel.render();
    const again = panel.detail.querySelector('#composition-presenter_size').children.map(o => o.attrs.value);
    assert.ok(again.includes('auto'), 'a chosen value can be given back to the composer');
    assert.match(panel.detail.querySelector('#composition-presenter_size').children[0].textContent, /\(your choice\)/);
    assert.equal(session.current.status, 'changed');
});

test('a screenplay choice is shown as such, without an "Automatic" that could not undo it', async () => {
    const slides = [{ title: 'Inside a plant', cinematic_plan: plan({ composition: { ...plan().composition, source: 'user',
        locked: ['motion', 'template'], chosen: ['motion'] } }) }];
    const { panel } = inspector(slides);
    const layout = panel.detail.querySelector('#composition-template');
    assert.match(layout.children[0].textContent, /\(as the screenplay asks\)/);
    assert.equal(layout.children.map(o => o.attrs.value).includes('auto'), false);
    const motion = panel.detail.querySelector('#composition-motion');
    assert.match(motion.children[0].textContent, /\(your choice\)/);
    assert.ok(motion.children.map(o => o.attrs.value).includes('auto'));
    const skipped = C.compositionSummary(plan({ composition: { ...plan().composition, ai: { status: 'skipped' } } }));
    assert.match(skipped.ai, /not asked for this scene/);
    const changed = inspector([{ title: 'x', cinematic_plan: plan(), visual_review: { composition: { status: 'changed', overrides: { motion: 'none' } } } }],
        { regenerate: async () => ({ plan: plan(), review: { status: 'changed', overrides: { motion: 'none' } } }) });
    await changed.session.regenerateComposition();
    assert.match(changed.session.message, /around your choices/); // the user's choices stay: no "needs your review"
    assert.equal(changed.session.slides[0].visual_review.composition.status, 'changed');
});

test('regenerate composition: only the composition is decided again, and it needs a new look', async () => {
    const slides = [{ title: 'Inside a plant', cinematic_plan: plan({ review_status: 'approved' }), visual_review: { composition: { status: 'approved' } } }];
    const { panel, calls, session } = inspector(slides);
    assert.ok(panel.detail.querySelector('[data-action="regenerate"]'));
    const result = await session.regenerateComposition();
    assert.deepEqual(calls[0], ['regenerate', 0]);
    assert.equal(slides[0].cinematic_plan.template, 'presenter_plus_visual');
    assert.equal(slides[0].visual_review, undefined); // the approval is withdrawn
    assert.match(session.message, /no picture, clip or presenter was generated/);
    assert.ok(result);
    const failing = inspector([{ title: 'x', cinematic_plan: plan() }], { regenerate: async () => { throw new Error('the server could not be reached'); } });
    assert.equal(await failing.session.regenerateComposition(), null);
    assert.equal(failing.session.error, true);
    // Phase 21 (review.js): a plain message; the server's own words are kept apart for the debug view
    assert.equal(failing.session.message, 'The layout could not be decided again. Your lesson is safe: the scene keeps its layout. Please try again.');
    assert.equal(failing.session.detailText(), 'the server could not be reached');
    const none = inspector([{ title: 'x', cinematic_plan: plan() }], { regenerate: undefined });
    assert.equal(none.panel.detail.querySelector('[data-action="regenerate"]'), null);
});

test('the Composition setting: Automatic by default, AI-assisted explained honestly', async () => {
    const doc = fakeDoc();
    const container = doc.createElement('div');
    const store = {}; const storage = { getItem: k => store[k] || null, setItem: (k, v) => { store[k] = v; } };
    // (debug, the page's ?visualDebug: the server's reason is shown; normal mode is covered in cinematic.test.js, Phase 21)
    const panel = new C.CinematicSettingsPanel({ doc, container, storage, debug: true, api: { vocabulary: async () => ({ composer: { ai_available: true, modes: ['rules', 'ai'],
        providers: { gemini: { available: false, reason: 'no Gemini API key is configured on this server' }, fake: { available: true } } } }) } });
    panel.set('mode', 'cinematic');
    const select = container.querySelector('#cinematic-composer');
    assert.ok(select);
    assert.equal(panel.settings.composer, 'rules');
    assert.deepEqual(select.children.map(o => o.value || o.attrs.value), ['rules', 'ai']);
    await panel.loadStatus();
    panel.set('composer', 'ai');
    // a stand-in model exists on this (test) server, but the page would use Gemini, which has no key: said honestly
    assert.match(container.textContent, /not available on this server \(no Gemini API key is configured on this server\).*automatic rules decide/);
    const ready = new C.CinematicSettingsPanel({ doc, container: doc.createElement('div'), storage, status: { ai_available: true, providers: { gemini: { available: true } } } });
    ready.set('composer', 'ai');
    assert.match(ready.container.textContent, /asked only about scenes where several elements compete/);
    const failing = new C.CinematicSettingsPanel({ doc, container: doc.createElement('div'), storage, api: { vocabulary: async () => { throw new Error('401'); } } });
    assert.equal(await failing.loadStatus(), null); // no login yet: the panel still works
});

test('the API client sends regeneration with the scenes and never media requests', async () => {
    const sent = [];
    const api = new C.CinematicApi({ fetch: async (url, init) => { sent.push([url, JSON.parse(init.body)]); return { ok: true, status: 200, json: async () => ({ plan: {} }) }; } });
    await api.regenerate({ scenes: [{ title: 'a' }], scene_index: 0, settings: { composer: 'rules' } });
    assert.equal(sent[0][0], '/api/cinematic/regenerate');
    assert.deepEqual(sent[0][1].scenes, [{ title: 'a' }]);
    assert.equal(sent.some(([url]) => /generate-ai|background|presenters\/generate/.test(url)), false);
});
