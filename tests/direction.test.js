'use strict';
// Unit tests for the AI Visual Director in the page (Phase 15): how a scene's visual direction is put into words
// (cinematic.js), the "Visual direction" block of the scene inspector with its changes, "Back to automatic direction" and
// "Regenerate direction" (review.js), the Visual direction and Learners settings, and the API client's bodies.
// Run from the repo root:  node --test "tests/*.test.js"
const test = require('node:test');
const assert = require('node:assert/strict');
const C = require('../cinematic.js');
const R = require('../review.js');
const { fakeDoc } = require('./helpers/cinematic-dom.js');

const box = (x, y, w, h) => ({ x, y, w, h });
const tick = () => new Promise(r => setTimeout(r, 0));

// A step-by-step scene's direction, as visual_director.build_plan stores it on the scene
function direction(extra = {}) {
    return {
        version: 1, scene_index: 0, concept: { title: 'Photosynthesis', id: 'c1', terms: [] }, learning_goal: 'follow the 4 steps in order',
        purpose: 'process', strategy: 'step_by_step', family: 'process', strategy_label: 'Step-by-step process',
        primary_visual: { kind: 'step_flow', source: 'board', label: 'The steps as a flow' }, secondary_visuals: [],
        presenter: { role: 'guide', interaction: 'points_to_board' }, emphasis: [{ target: 'step' }], annotations: [],
        camera_intent: 'follow_process', motion_intent: 'progressive_build', timing: { first: 'board', reveal: 'progressive' },
        complexity: 'moderate', density: 'medium', learner_focus: 'each step, one after another', visual_need: 'met', route: null,
        confidence: 'high', reason_codes: ['ordered_steps', 'little_text'], source: 'rules', locked: [], notes: [], choices: 'c0',
        basis: 'b0', fingerprint: 'fp-steps', ...extra
    };
}
// The plan's direction summary (composer.direction_summary): codes and the vocabulary's words only
function summaryOf(d, extra = {}) {
    return { strategy: d.strategy, label: d.strategy_label, family: d.family, primary: d.primary_visual.label, primary_kind: d.primary_visual.kind,
        secondary: [], presenter: d.presenter.role, interaction: d.presenter.interaction, motion: d.motion_intent, camera: d.camera_intent,
        reasons: ['the scene lists steps in order', 'the board has little text'], source: d.source, locked: d.locked, notes: d.notes,
        confidence: d.confidence, fingerprint: d.fingerprint, ai: null, ...extra };
}
function plan(d = direction(), extra = {}, summary = {}) {
    return {
        version: 1, template: 'presenter_explanation', template_label: 'Presenter + explanation', duration: 9, shot: 'medium',
        style: { typography: 'academic', motion: 'subtle', transitions: 'fade', background: 'auto', emphasis: 'clear' },
        background: { type: 'gradient' }, presenter: { type: 'illustrated', presenter_id: 'aadhi-teacher', shown: true, side: 'right' },
        transition: { in: 'fade', out: 'fade', duration: 0.5 }, camera: { shot: 'medium', movement: 'static' },
        layers: [
            { id: 'board', type: 'board', role: 'steps', style: 'body', box: box(0.05, 0.19, 0.55, 0.6), z: 30, start: 0.1, enter: 'fade_in', important: true, camera: true },
            { id: 'presenter', type: 'presenter', side: 'right', placement: 'side', box: box(0.66, 0.2, 0.3, 0.6), z: 40, start: 0.5, enter: 'slide_in', important: true, camera: true },
            { id: 'subtitles', type: 'subtitles', box: box(0, 0.85, 1, 0.15), z: 90, start: 0, important: false, camera: false }
        ],
        timeline: [], warnings: [], notes: [], review_status: 'pending',
        composition: { version: 1, mode: 'rules', source: 'rules', decision: { template: 'presenter_explanation' }, locked: [],
            reasons: ['the steps are what is taught'], repairs: [], intent: { purpose: 'process', density: 'medium' } },
        direction: summaryOf(d, summary), ...extra
    };
}
function scene(d = direction(), extra = {}) {
    return { title: 'How plants make food', narration: 'First light is absorbed.', visual_direction: d, cinematic_plan: plan(d), ...extra };
}

// The scene inspector with the page's composition adapter (index.html), the direction parts recorded
function inspector(slides, adapterExtra = {}, media = {}) {
    const doc = fakeDoc();
    const calls = [];
    const changed = (overrides, prev) => {
        const locked = Object.keys(overrides).filter(k => overrides[k] !== 'auto');
        const d = direction({ ...(overrides.strategy === 'side_by_side' ? { strategy: 'side_by_side', strategy_label: 'Side-by-side comparison' } : {}),
            ...(overrides.presenter_role ? { presenter: { role: overrides.presenter_role, interaction: 'none' } } : {}),
            source: locked.length ? 'user' : 'rules', locked, fingerprint: 'fp-' + locked.join('-') });
        return { review: locked.length ? { status: 'changed', overrides: { ...((prev && prev.overrides) || {}), ...overrides } } : null, direction: d, plan: plan(d) };
    };
    const session = new R.ReviewSession({ slides, api: {}, media, composition: {
        enabled: () => true, facts: C.inspectorFacts, summary: C.compositionSummary, statuses: C.elementStatuses,
        review: async body => { calls.push(['review', body]); return { review: null, plan: plan() }; },
        regenerate: async i => { calls.push(['regenerate', i]); return { plan: plan(), review: null }; },
        options: { template: C.TEMPLATES, camera: C.CAMERA },
        direction: (sc, p) => C.directionSummary(sc, p),
        directionOptions: () => C.directionOptions(null),
        reviewDirection: async body => {
            calls.push(['reviewDirection', body]);
            if (body.action === 'reset') { const d = direction(); return { review: null, direction: d, plan: plan(d) }; }
            return changed(body.overrides, slides[body.scene_index].visual_review && slides[body.scene_index].visual_review.direction);
        },
        regenerateDirection: async i => { calls.push(['regenerateDirection', i]); const d = direction({ fingerprint: 'fp-again' }); return { direction: d, plan: plan(d) }; },
        ...adapterExtra } });
    const panel = new R.VisualReviewPanel({ doc, session, pickAsset: () => {}, showInLesson: () => {} });
    panel.build();
    panel.render();
    return { doc, session, panel, calls };
}

const optionValues = select => select.children.map(o => o.attrs.value);

// ---- words ---------------------------------------------------------------------------------------------------------

test('how a visual direction was decided, in words: automatic, AI-assisted, your choice, the safe direction', () => {
    const rules = C.directionSummary(scene(), plan());
    assert.equal(rules.label, 'Step-by-step process ✓');
    assert.equal(rules.sourceText, 'Automatic');
    assert.equal(rules.automatic, true);
    assert.equal(rules.goal, 'follow the 4 steps in order');
    assert.equal(rules.primary, 'The steps as a flow');
    assert.equal(rules.presenter, 'Presenter small, pointing');
    assert.equal(rules.motion, 'Builds step by step');
    assert.equal(rules.camera, 'Follows the process');
    assert.equal(rules.focus, 'each step, one after another');
    assert.deepEqual(rules.current, { strategy: 'step_by_step', primary_visual: 'step_flow', presenter_role: 'guide', camera_intent: 'follow_process',
        motion_intent: 'progressive_build', prefer: '' });

    const aiDir = direction({ source: 'ai', ai: { status: 'ok', provider: 'fake', model: 'fake-director-1' } });
    const ai = C.directionSummary(scene(aiDir), plan(aiDir, {}, { ai: { status: 'ok', provider: 'fake', model: 'fake-director-1' } }));
    assert.equal(ai.label, 'Step-by-step process ✓');
    assert.equal(ai.sourceText, 'AI-assisted');
    assert.equal(ai.ai, 'AI suggestion used');

    const userDir = direction({ source: 'user', locked: ['strategy'], strategy: 'side_by_side', strategy_label: 'Side-by-side comparison' });
    const user = C.directionSummary(scene(userDir), plan(userDir));
    assert.equal(user.label, 'Side-by-side comparison');
    assert.equal(user.sourceText, 'Your choice');
    assert.equal(user.automatic, false);
    assert.deepEqual(user.locked, ['strategy']);

    const safeDir = direction({ source: 'fallback' });
    const safe = C.directionSummary(scene(safeDir), plan(safeDir));
    assert.equal(safe.label, 'Step-by-step process');
    assert.equal(safe.sourceText, 'Safe direction');

    // the model's outcome in the composition's wording; a failure's internal error is never shown
    assert.match(C.directionAiNote({ status: 'repaired' }, 'ai'), /after one repair/);
    assert.match(C.directionAiNote({ status: 'ok' }, 'rules'), /checked; the rules fitted this scene better/);
    assert.match(C.directionAiNote({ status: 'ok' }, 'user'), /your choices come first/);
    // Phase 21: the server's reason (it names the provider) only with ?visualDebug; plain words otherwise
    const unavailable = { status: 'unavailable', error: 'no Gemini API key is configured' };
    assert.match(C.directionAiNote(unavailable, 'rules', { debug: true }),
        /AI-assisted direction is not available here \(no Gemini API key is configured\); the rules decided/);
    assert.equal(C.directionAiNote(unavailable, 'rules'), 'AI-assisted direction is not available here; the rules decided');
    assert.doesNotMatch(C.directionAiNote(unavailable, 'rules'), /Gemini|API key|\(/);
    const offDir = direction();
    const offPlan = plan(offDir, {}, { ai: unavailable });
    assert.equal(C.directionSummary(scene(offDir), offPlan).ai, 'AI-assisted direction is not available here; the rules decided');
    assert.match(C.directionSummary(scene(offDir), offPlan, { debug: true }).ai, /\(no Gemini API key is configured\)/);
    assert.equal(C.directionAiNote({ status: 'failed', error: 'HTTP 500 from the provider' }, 'rules'), 'AI model failed; the rules decided');
    assert.match(C.directionAiNote({ status: 'skipped' }, 'rules'), /not asked for this scene/);
    assert.equal(C.directionAiNote(null, 'rules'), null);

    // every presenter role and motion intent in plain words
    const roles = { dominant: 'Presenter leads', secondary: 'Presenter beside the content', guide: 'Presenter small, pointing',
        demonstrator: 'Presenter demonstrates beside the visual', hidden: 'No presenter' };
    Object.entries(roles).forEach(([role, words]) => {
        const d = direction({ presenter: { role, interaction: 'explains' } });
        assert.equal(C.directionSummary(scene(d), plan(d)).presenter, words, role);
    });
    const motions = { progressive_build: 'Builds step by step', sequence: 'One part after another', compare: 'Both sides together',
        highlight: 'Highlights what matters', reveal: 'Appears with the narration', fade: 'Gentle fade', none: 'Still' };
    Object.entries(motions).forEach(([motion, words]) => {
        const d = direction({ motion_intent: motion });
        assert.equal(C.directionSummary(scene(d), plan(d)).motion, words, motion);
    });
    ['move_along_path', 'transform', 'zoom_to_detail'].forEach(m => assert.ok(C.MOTION_INTENTS[m], m));

    // a stored direction that is not the one the composition followed lends it no words (no outdated goal)
    const stale = C.directionSummary(scene(direction({ fingerprint: 'older', learning_goal: 'an old goal' })), plan());
    assert.equal(stale.goal, '');
    assert.equal(stale.label, 'Step-by-step process ✓');
    // a scene with only its stored direction (no direction summary in the plan yet) still reads
    const only = C.directionSummary(scene(), { template: 'quiz' });
    assert.equal(only.label, 'Step-by-step process ✓');
    assert.equal(only.goal, 'follow the 4 steps in order');
    assert.equal(C.directionSummary({ title: 'x' }, { template: 'quiz' }), null);
});

test('the change choices: the server vocabulary when loaded, the same words otherwise; preference without "auto"', () => {
    const fallback = C.directionOptions(null);
    assert.equal(fallback.strategy.step_by_step, 'Step-by-step process');
    assert.equal(fallback.primary_visual.step_flow, 'The steps as a flow');
    assert.deepEqual(Object.keys(fallback.prefer), ['existing', 'static']);
    assert.equal(fallback.presenter_role.guide, 'Presenter small, pointing');
    const served = C.directionOptions({ strategies: { timeline: 'Timeline' }, visuals: { timeline: 'The events on a timeline' },
        presenter_roles: ['dominant', 'hidden'], camera: ['static'], motion: ['none', 'sequence'], prefer: ['existing', 'static', 'auto'] });
    assert.deepEqual(served.strategy, { timeline: 'Timeline' });
    assert.deepEqual(served.presenter_role, { dominant: 'Presenter leads', hidden: 'No presenter' });
    assert.deepEqual(served.camera_intent, { static: 'Still camera' });
    assert.deepEqual(served.motion_intent, { none: 'Still', sequence: 'One part after another' });
    assert.deepEqual(Object.keys(served.prefer), ['existing', 'static']);
});

// ---- the scene inspector ---------------------------------------------------------------------------------------------

test('the inspector shows the direction above the composition: goal, what teaches, presenter, motion, focus, reasons', () => {
    const d = direction({ source: 'ai', confidence: 'high', score: 0.87,
        ai: { status: 'ok', provider: 'fake', model: 'fake-director-1', key: 'k1', decision: { strategy: 'step_by_step', reasons: ['I believe the steps matter most'] } } });
    const slides = [{ ...scene(d), cinematic_plan: plan(d, {}, { source: 'ai', notes: ['a diagram would help this scene; none is planned (you can add one in Visual Review)'],
        reasons: ['the scene lists steps in order', 'the board has little text', 'the narration explains how something works', 'the learners are beginners'],
        ai: { status: 'ok', provider: 'fake', model: 'fake-director-1' } }) }];
    const { panel } = inspector(slides);
    const block = panel.detail.querySelector('.review-direction');
    assert.ok(block);
    const order = panel.detail.children.map(c => c.className);
    assert.ok(order.indexOf('review-direction') < order.indexOf('review-composition-decided'), 'above the composition block');
    assert.equal(block.getAttribute('data-source'), 'ai');
    assert.equal(block.querySelector('.review-direction-auto').textContent, 'Visual direction: Step-by-step process ✓');
    const fact = key => block.querySelector(`[data-fact="${key}"]`).textContent;
    assert.equal(fact('source'), 'AI-assisted');
    assert.equal(fact('goal'), 'follow the 4 steps in order');
    assert.equal(fact('what'), 'The steps as a flow');
    assert.equal(fact('presenter'), 'Presenter small, pointing');
    assert.equal(fact('motion'), 'Builds step by step');
    assert.equal(fact('focus'), 'each step, one after another');
    const reasons = block.querySelectorAll('.review-direction-reasons li').map(li => li.textContent);
    assert.deepEqual(reasons, ['the scene lists steps in order', 'the board has little text', 'the narration explains how something works']); // at most 3
    assert.match(block.querySelector('.review-direction-note').textContent, /a diagram would help/);
    assert.equal(block.querySelector('.review-direction-ai').textContent, 'AI suggestion used');
    // never a score, the confidence, the model's answer or its reasoning
    const text = block.textContent;
    assert.doesNotMatch(text, /0\.87|confidence|I believe|fake-director|k1|reason_codes|step_by_step|progressive_build/i);
    // the composition's own block and controls are unchanged
    assert.equal(panel.detail.querySelector('.review-composition-auto').textContent, 'Layout: Automatic ✓ · Presenter + explanation'); // (Phase 21: the Layout row's word)
    for (const action of ['keep', 'change', 'regenerate', 'apply']) assert.ok(panel.detail.querySelector(`[data-action="${action}"]`), action);
    // without the direction adapter (an older page) there is no direction block
    const older = inspector([scene()], { direction: undefined });
    assert.equal(older.panel.detail.querySelector('.review-direction'), null);
});

test('change direction: "Automatic" only for the keys the user chose; Apply sends only the changed keys', async () => {
    const slides = [scene()];
    const { panel, calls, session } = inspector(slides);
    const toggle = panel.detail.querySelector('[data-action="direction-change"]');
    assert.equal(toggle.textContent, 'Change direction ▾');
    assert.ok('hidden' in panel.detail.querySelector('.review-direction-change').attrs, 'closed until asked');
    assert.equal(panel.detail.querySelector('[data-action="direction-reset"]'), null); // nothing chosen yet
    toggle.fire('click');
    assert.equal('hidden' in panel.detail.querySelector('.review-direction-change').attrs, false);
    assert.equal(panel.detail.querySelector('[data-action="direction-change"]').textContent, 'Change direction ▴');
    for (const key of ['strategy', 'primary_visual', 'presenter_role', 'camera_intent', 'motion_intent', 'prefer']) {
        const select = panel.detail.querySelector(`#direction-${key}`);
        assert.ok(select, key);
        assert.equal(optionValues(select).includes('auto'), false, `${key}: nothing chosen, no "Automatic" to go back to`);
        assert.match(select.children[0].textContent, /as it is \(automatic\)/);
    }
    assert.deepEqual(optionValues(panel.detail.querySelector('#direction-prefer')), ['', 'existing', 'static']);
    assert.ok(optionValues(panel.detail.querySelector('#direction-strategy')).includes('timeline'));
    // nothing picked: nothing is sent
    panel.detail.querySelector('[data-action="direction-apply"]').fire('click');
    assert.equal(calls.length, 0);
    assert.match(session.message, /Nothing to change/);
    // a new strategy and presenter role; the motion picked is the current one, so it is not sent
    panel.detail.querySelector('#direction-strategy').value = 'side_by_side';
    panel.detail.querySelector('#direction-presenter_role').value = 'hidden';
    panel.detail.querySelector('#direction-motion_intent').value = 'progressive_build';
    panel.detail.querySelector('[data-action="direction-apply"]').fire('click');
    await tick();
    assert.deepEqual(calls[0], ['reviewDirection', { scene_index: 0, action: 'change', overrides: { strategy: 'side_by_side', presenter_role: 'hidden' } }]);
    assert.deepEqual(slides[0].visual_review.direction, { status: 'changed', overrides: { strategy: 'side_by_side', presenter_role: 'hidden' } });
    assert.equal(slides[0].visual_direction.source, 'user');
    assert.equal(slides[0].cinematic_plan.direction.label, 'Side-by-side comparison');
    assert.match(session.message, /follows your visual direction/);
    assert.equal(calls.some(([name]) => name === 'review' || name === 'regenerate'), false); // the composition's own review is not touched
    // now the chosen keys can be given back ("Automatic"); the others still cannot
    assert.equal(panel.detail.querySelector('.review-direction-auto').textContent, 'Visual direction: Side-by-side comparison');
    assert.equal(panel.detail.querySelector('[data-fact="source"]').textContent, 'Your choice');
    panel.detail.querySelector('[data-action="direction-change"]').fire('click');
    const strategy = panel.detail.querySelector('#direction-strategy');
    assert.ok(optionValues(strategy).includes('auto'));
    assert.match(strategy.children[0].textContent, /\(your choice\)/);
    assert.equal(strategy.children[1].textContent, 'Way of teaching: Automatic');
    assert.equal(optionValues(panel.detail.querySelector('#direction-camera_intent')).includes('auto'), false);
    assert.ok(panel.detail.querySelector('[data-action="direction-reset"]'), '"Back to automatic direction" once something is chosen');
    // "Automatic" for one key is sent as such
    strategy.value = 'auto';
    panel.detail.querySelector('[data-action="direction-apply"]').fire('click');
    await tick();
    assert.deepEqual(calls[1], ['reviewDirection', { scene_index: 0, action: 'change', overrides: { strategy: 'auto' } }]);
});

test('back to automatic direction: reset removes the choices and keeps the other reviews', async () => {
    const d = direction({ source: 'user', locked: ['camera_intent'], camera_intent: 'static' });
    const slides = [scene(d, { visual_review: { direction: { status: 'changed', overrides: { camera_intent: 'static' } }, composition: { status: 'approved' } } })];
    const { panel, calls, session } = inspector(slides);
    const reset = panel.detail.querySelector('[data-action="direction-reset"]');
    assert.equal(reset.textContent, 'Back to automatic direction');
    reset.fire('click');
    await tick();
    assert.deepEqual(calls[0], ['reviewDirection', { scene_index: 0, action: 'reset' }]);
    assert.equal(slides[0].visual_review.direction, undefined);
    assert.deepEqual(slides[0].visual_review.composition, { status: 'approved' });
    assert.equal(slides[0].visual_direction.source, 'rules');
    assert.equal(session.message, 'Back to the automatic visual direction.');
    assert.equal(panel.detail.querySelector('[data-action="direction-reset"]'), null);
    // the only review removed: no empty review left behind
    const lone = inspector([scene(d, { visual_review: { direction: { status: 'changed', overrides: { camera_intent: 'static' } } } })]);
    await lone.session.decideDirection('reset');
    assert.equal(lone.session.slides[0].visual_review, undefined);
    // a failure says so and changes nothing
    const failing = inspector([scene(d)], { reviewDirection: async () => { throw new Error('Lesson not found.'); } });
    assert.equal(await failing.session.decideDirection('reset'), null);
    assert.equal(failing.session.error, true);
    // Phase 21 (review.js): a plain message; the server's own words are kept apart for the debug view
    assert.equal(failing.session.message, 'The change could not be saved. Your lesson is safe: nothing was changed. Please try again.');
    assert.equal(failing.session.detailText(), 'Lesson not found.');
    assert.equal(failing.session.slides[0].visual_direction.source, 'user');
});

test('regenerate direction: only the direction endpoint is asked (no media), and the result is stored', async () => {
    const sent = [];
    const api = new C.CinematicApi({ fetch: async (url, init) => {
        sent.push([url, init && init.body ? JSON.parse(init.body) : null]);
        const d = direction({ fingerprint: 'fp-new', learning_goal: 'follow the steps of photosynthesis in order' });
        return { ok: true, status: 200, json: async () => ({ direction: d, plan: plan(d) }) };
    } });
    const generated = [];
    const media = { generateImage: async () => { generated.push('image'); return {}; }, generateVideo: async () => { generated.push('video'); return {}; } };
    const settings = { ...C.DEFAULTS, mode: 'cinematic', director: 'ai' };
    const conceptMap = [{ id: 'c1', title: 'Photosynthesis', depends_on: [] }];
    const slides = [scene()];
    const { panel, session } = inspector(slides, { regenerateDirection: i => api.regenerateDirection(slides, i, settings, { project_id: 7, concept_map: conceptMap }) }, media);
    const button = panel.detail.querySelector('[data-action="direction-regenerate"]');
    assert.equal(button.textContent, 'Regenerate direction');
    assert.equal(button.getAttribute('title'), 'Decides the visual direction again (no picture, clip or presenter is generated)');
    button.fire('click');
    await tick();
    await tick();
    assert.deepEqual(sent.map(([url]) => url), ['/api/cinematic/direction/regenerate']);
    assert.equal(sent.some(([url]) => /generate-ai|presenters\/generate|background|\/render|\/api\/cinematic\/regenerate$/.test(url)), false);
    assert.deepEqual(generated, []);
    assert.equal(sent[0][1].scene_index, 0);
    assert.equal(sent[0][1].project_id, 7);
    assert.deepEqual(sent[0][1].concept_map, conceptMap);
    assert.equal(sent[0][1].settings.director, 'ai');
    assert.equal(slides[0].visual_direction.fingerprint, 'fp-new');
    assert.equal(slides[0].cinematic_plan.direction.fingerprint, 'fp-new');
    assert.equal(session.message, 'Visual direction decided again (no picture, clip or presenter was generated).');
    assert.equal(panel.detail.querySelector('[data-fact="goal"]').textContent, 'follow the steps of photosynthesis in order');
    // a failure says so, and the earlier direction stays
    const failing = inspector([scene()], { regenerateDirection: async () => { throw new Error('the server could not be reached'); } });
    assert.equal(await failing.session.regenerateDirection(), null);
    assert.equal(failing.session.error, true);
    // Phase 21 (review.js): a plain message; the server's own words are kept apart for the debug view
    assert.equal(failing.session.message, 'The visual direction could not be decided again. Your lesson is safe: the scene keeps its direction. Please try again.');
    assert.equal(failing.session.detailText(), 'the server could not be reached');
    assert.equal(failing.session.slides[0].visual_direction.fingerprint, 'fp-steps');
    // without the adapter's regeneration there is no button (and the session does nothing)
    const none = inspector([scene()], { regenerateDirection: undefined });
    assert.equal(none.panel.detail.querySelector('[data-action="direction-regenerate"]'), null);
    assert.equal(await none.session.regenerateDirection(), null);
});

// ---- the start screen ------------------------------------------------------------------------------------------------

test('the Visual direction and Learners settings: Automatic and General by default; AI-assisted explained honestly', async () => {
    assert.equal(C.DEFAULTS.director, 'rules');
    assert.equal(C.DEFAULTS.learner_level, null);
    const doc = fakeDoc();
    const container = doc.createElement('div');
    const store = {}; const storage = { getItem: k => store[k] || null, setItem: (k, v) => { store[k] = v; } };
    const director = { modes: ['rules', 'ai'], ai_available: true, providers: { gemini: { available: false, reason: 'no Gemini API key is configured on this server' },
        openai: { available: false, reason: 'no OpenAI API key is configured' }, fake: { available: true, reason: null } } };
    // (debug, the page's ?visualDebug: the server's reason is shown; normal mode is covered in cinematic.test.js, Phase 21)
    const panel = new C.CinematicSettingsPanel({ doc, container, storage, debug: true, api: { vocabulary: async () => ({ composer: null, director }) } });
    assert.equal(container.querySelector('#cinematic-director'), null); // Classic: nothing to direct
    panel.set('mode', 'cinematic');
    const select = container.querySelector('#cinematic-director');
    assert.ok(select);
    assert.equal(panel.settings.director, 'rules');
    assert.deepEqual(select.children.map(o => o.value), ['rules', 'ai']);
    assert.deepEqual(select.children.map(o => o.textContent), ['Automatic', 'AI-assisted']);
    const learners = container.querySelector('#cinematic-learners');
    assert.deepEqual(learners.children.map(o => o.value), ['', 'beginner', 'intermediate', 'advanced']);
    assert.deepEqual(learners.children.map(o => o.textContent), ['General', 'Beginner', 'Intermediate', 'Advanced']);
    assert.equal(learners.children[0].selected, true);
    assert.equal(panel.settings.learner_level, null);
    assert.doesNotMatch(container.textContent, /visual direction is not available|best way to teach/); // Automatic: no note
    panel.set('learner_level', 'beginner');
    assert.equal(JSON.parse(store['aadhi.cinematic']).learner_level, 'beginner');
    panel.set('learner_level', '');
    assert.equal(panel.settings.learner_level, null); // General
    await panel.loadStatus();
    assert.deepEqual(panel.directorStatus, director);
    panel.set('director', 'ai');
    // a stand-in model exists on this (test) server, but the page would use Gemini, which has no key: said honestly
    assert.match(container.textContent,
        /AI-assisted visual direction is not available on this server \(no Gemini API key is configured on this server\).*automatic rules decide/);
    const ready = new C.CinematicSettingsPanel({ doc, container: doc.createElement('div'), storage, composerProvider: () => 'fake', directorStatus: director });
    ready.set('director', 'ai');
    assert.match(ready.container.textContent, /asked only about scenes where the best way to teach is not clear/);
    const failing = new C.CinematicSettingsPanel({ doc, container: doc.createElement('div'), storage, api: { vocabulary: async () => { throw new Error('401'); } } });
    await failing.loadStatus();
    assert.equal(failing.directorStatus, null); // no login yet: the panel still works
});

// ---- the API client --------------------------------------------------------------------------------------------------

test('the API client sends the documented direction bodies (and the concept map with the plan)', async () => {
    const sent = [];
    const api = new C.CinematicApi({ fetch: async (url, init) => {
        sent.push([url, init.body ? JSON.parse(init.body) : null, init.headers]);
        return url.endsWith('/review') ? { ok: false, status: 422, json: async () => ({ detail: 'Say what to change.' }) }
            : { ok: true, status: 200, json: async () => ({ directions: [null] }) };
    } });
    const scenes = [{ title: 'a' }, { title: 'b' }];
    const settings = { mode: 'cinematic', director: 'rules', learner_level: null };
    const conceptMap = [{ id: 'c1', title: 'Photosynthesis' }];
    const data = await api.direction(scenes, settings, { project_id: 7, concept_map: conceptMap });
    assert.deepEqual(data.directions, [null]);
    assert.deepEqual(sent[0].slice(0, 2), ['/api/cinematic/direction', { scenes, settings, project_id: 7, concept_map: conceptMap }]);
    assert.equal(sent[0][2]['Content-Type'], 'application/json');
    await api.direction(scenes, settings, { project_id: undefined, concept_map: null }); // an unsaved lesson without a concept map
    assert.deepEqual(sent[1][1], { scenes, settings });
    await api.regenerateDirection(scenes, 1, settings, { project_id: 7 });
    assert.deepEqual(sent[2].slice(0, 2), ['/api/cinematic/direction/regenerate', { scenes, scene_index: 1, settings, project_id: 7 }]);
    const body = { project_id: 7, scene_index: 0, action: 'change', overrides: { strategy: 'timeline' }, scene: scenes[0], settings, concept_map: conceptMap };
    await assert.rejects(() => api.reviewDirection(body), /Say what to change/);
    assert.deepEqual(sent[3].slice(0, 2), ['/api/cinematic/direction/review', body]);
    await api.plan(scenes, settings, 7, { concept_map: conceptMap });
    assert.deepEqual(sent[4].slice(0, 2), ['/api/cinematic/plan', { scenes, settings, project_id: 7, concept_map: conceptMap }]);
    await api.plan(scenes, settings, null);
    assert.deepEqual(sent[5][1], { scenes, settings }); // as before Phase 15
    assert.equal(sent.some(([url]) => /generate|background|\/render/.test(url) && !/direction\/regenerate$/.test(url)), false);
});
