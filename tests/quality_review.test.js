'use strict';
// Unit tests for the lesson's quality in Visual Review (Phase 18): the lesson-wide "Quality" section of the panel (review.js
// renderQuality, fed by the composition adapter's quality() / qualityRun() / qualityStale() / qualityRepair(issue) /
// qualityApply(issue) / selectScene(index)), the per-scene chips and "Quality" filter, the scene's own "Quality" block in the
// composition inspector, and CinematicApi.quality (POST /api/quality/lesson). Quality is information: it never changes a
// review, a status or an approval.
// Run from the repo root:  node --test "tests/*.test.js"
const test = require('node:test');
const assert = require('node:assert/strict');
const R = require('../review.js');
const C = require('../cinematic.js');
const { fakeDoc } = require('./helpers/cinematic-dom.js');

const box = (x, y, w, h) => ({ x, y, w, h });
const tick = () => new Promise(r => setTimeout(r, 0));
const settle = async () => { for (let i = 0; i < 4; i++) await tick(); };
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };

function plan(extra = {}) {
    return {
        version: 1, template: 'comparison', template_label: 'Comparison', duration: 11, shot: 'medium',
        background: { type: 'gradient' }, presenter: { type: 'illustrated', presenter_id: 'aadhi-teacher', shown: true, side: 'right' },
        transition: { in: 'fade', out: 'fade', duration: 0.5 }, camera: { shot: 'medium', movement: 'static' },
        layers: [
            { id: 'visual', type: 'visual', role: 'image', box: box(0.05, 0.19, 0.35, 0.52), z: 20, start: 0.15 },
            { id: 'board', type: 'board', role: 'comparison', box: box(0.42, 0.19, 0.53, 0.5), z: 30, start: 0.1 }
        ],
        timeline: [], warnings: [], notes: [], review_status: 'pending',
        ...extra
    };
}

// Five scenes: 0 composition approved, 1 an approved visual and a composition, 2 and 3 compositions, 4 nothing to review
function lesson() {
    return [
        { title: 'Seeds', cinematic_plan: plan({ review_status: 'approved' }), visual_review: { composition: { status: 'approved' } } },
        { title: 'Inside the leaf', cinematic_plan: plan(),
            visual_plan: { main: { source: 'ASSET', selection: 'approved', review_status: 'approved', asset_id: 7, url: '/media/a.png', media: 'STATIC_IMAGE' } },
            visual_review: { main: { status: 'approved', asset_id: 7 } } },
        { title: 'Roots', cinematic_plan: plan() },
        { title: 'Sunlight', cinematic_plan: plan() },
        { title: 'Quiz', type: 'quiz_checkpoint' }
    ];
}

// ---- a report shaped like quality.py's (the same severities, dimensions, ordering, counts and statuses) ------------------
const SEVERITIES = ['info', 'notice', 'warning', 'error', 'blocking'];
const RANK = Object.fromEntries(SEVERITIES.map((s, i) => [s, i]));
const DIMENSIONS = { presenter: 'Presenter', style: 'Style', typography: 'Text', colour: 'Colours', terminology: 'Terminology', education: 'Concepts',
    formulas: 'Formulas', code: 'Code', diagrams: 'Diagrams', camera: 'Camera', motion: 'Motion', background: 'Backgrounds', density: 'Busy scenes',
    timing: 'Timing', media: 'Pictures and clips', preview_export: 'Preview and export' };
let nextId = 1;
const NONE = { kind: 'none', class: null, action: { type: 'none' }, label: null };

function finding(rule, dimension, severity, scene, message, repair = null, extra = {}) {
    const rep = repair || NONE;
    return { id: (nextId++).toString(16).padStart(12, '0'), rule, dimension, severity, scene, element: 'scene', message,
        evidence: { key: rule.split('.')[1], found: [1, 2] }, repair: rep,
        repair_status: rep.kind === 'none' ? 'not_repairable' : 'available', fingerprint: scene === null ? null : `fp${scene}`, ...extra };
}
const replan = (scenes, label = 'Re-plan the scene', klass = 'presentation') => ({ kind: 'auto', class: klass, action: { type: 'replan', scenes }, label });
const suggest = (type, scene, overrides, label = '') => ({ kind: 'suggest', class: 'composition', action: { type, scene, overrides }, label });

function report(issues, { scenes = 5, limitations = [] } = {}) {
    const sorted = [...issues].sort((a, b) => (RANK[b.severity] - RANK[a.severity]) || ((a.scene ?? -1) - (b.scene ?? -1)) || a.rule.localeCompare(b.rule));
    const counts = Object.fromEntries(SEVERITIES.map(s => [s, sorted.filter(f => f.severity === s).length]));
    const status = c => (c.blocking ? 'blocked' : c.error ? 'attention' : c.warning || c.notice ? 'review' : 'good');
    const dimensions = {};
    Object.entries(DIMENSIONS).forEach(([key, label]) => {
        const mine = sorted.filter(f => f.dimension === key && f.severity !== 'info');
        const worst = Math.max(-1, ...mine.map(f => RANK[f.severity]));
        dimensions[key] = { label, status: worst < RANK.notice ? 'pass' : SEVERITIES[worst], issues: mine.length };
    });
    return {
        rules: 'quality_rules@1', version: 1, fingerprint: '0123456789abcdef', status: status(counts),
        summary: { scenes, counts, attention: sorted.filter(f => f.severity !== 'info').length, auto_repairable: sorted.filter(f => f.repair.kind === 'auto').length },
        dimensions, scenes: Array.from({ length: scenes }, (_, index) => ({ index, fingerprint: `fp${index}`, status: 'good', counts: {} })),
        issues: sorted, registry: {}, limitations, families: ['quality_style']
    };
}

// The lesson's usual findings: a lesson-wide one, two on scene 2 (index 1: a re-plan, a style suggestion), one on scene 4
// (a composition suggestion without a label), a note on scene 3 (info: intentional) and a notice on scene 1 (no repair)
function findings() {
    return [
        finding('style.mixed_versions', 'style', 'warning', null, 'Two versions of the lesson style are mixed.'),
        finding('core.stale_plan', 'preview_export', 'error', 1, 'Scene 2 (“Inside the leaf”) was planned with older settings.', replan([1])),
        finding('style.scene_accent', 'colour', 'notice', 1, 'Scene 2 (“Inside the leaf”) uses another accent than the lesson.',
            suggest('scene_style', 1, { style_accent: 'auto' }, 'Use the lesson accent')),
        finding('timing.too_fast', 'timing', 'warning', 3, 'Scene 4 (“Sunlight”) moves too fast to read.', suggest('composition', 3, { motion: 'calm' })),
        finding('style.chosen_accent', 'colour', 'info', 2, 'Scene 3 (“Roots”) has its own accent, chosen in Visual Review.'),
        finding('education.term_casing', 'terminology', 'notice', 0, 'The term “Chlorophyll” is written two ways.')
    ];
}

// The panel with a fake composition adapter (the real one is wired in index.html)
function setup({ slides = lesson(), rep = report(findings()), adapter = {}, debug = false, api = {} } = {}) {
    const doc = fakeDoc();
    const calls = [];
    const changes = [];
    const state = { report: rep, stale: false };
    const composition = {
        enabled: () => true,
        facts: () => [['Layout', 'Comparison']],
        summary: () => ({ source: 'rules', label: 'Automatic ✓', template: 'Comparison', reasons: [], repairs: [], ai: null, locked: [], chosen: [] }),
        statuses: () => [],
        options: { camera: { static: 'Still', focus: 'Focus' } },
        review: async body => { calls.push(['review', body]); return { review: null, plan: slides[body.scene_index].cinematic_plan }; },
        quality: () => state.report,
        qualityStale: () => state.stale,
        qualityRun: async () => { calls.push(['run']); return state.report; },
        qualityRepair: async issue => { calls.push(['repair', issue]); },
        qualityApply: async issue => { calls.push(['apply', issue]); },
        ...adapter
    };
    const session = new R.ReviewSession({ slides, api, media: {}, composition });
    const panel = new R.VisualReviewPanel({ doc, session, pickAsset: () => {}, showInLesson: () => {}, debug, onChange: i => changes.push(i) });
    panel.build();
    panel.render();
    return { doc, session, panel, calls, changes, state, slides, quality: () => panel.qualityBox };
}

const texts = nodes => nodes.map(n => n.textContent);
const hidden = node => node.getAttribute('hidden') !== null;
// no such element (asserted without printing a fake DOM node: its document graph is far too big to show)
const absent = (node, what = 'no such element') => assert.ok(node === null, what);

// ---- the headline ----------------------------------------------------------------------------------------------------

test('the headline follows the report status, with the number of things and of scenes checked', () => {
    const headline = rep => {
        const { quality } = setup({ rep });
        return [quality().querySelector('.quality-headline').textContent, quality().querySelector('.quality-scenes').textContent,
            quality().getAttribute('data-status'), quality().querySelector('.quality-headline').getAttribute('data-status')];
    };
    assert.deepEqual(headline(report([])), ['✓ Quality looks good', '5 scenes checked', 'good', 'good']);
    // an intentional variation (info) is not a thing to review
    assert.deepEqual(headline(report([finding('style.chosen_accent', 'colour', 'info', 2, 'chosen')]))[0], '✓ Quality looks good');
    assert.deepEqual(headline(report([finding('education.term_casing', 'terminology', 'notice', 0, 'two ways')], { scenes: 1 })),
        ['⚠ 1 thing to review', '1 scene checked', 'review', 'review']);
    assert.equal(headline(report([finding('a.b', 'style', 'notice', 0, 'x'), finding('a.c', 'style', 'warning', 1, 'y')]))[0], '⚠ 2 things to review');
    // Phase 21: one plain headline for anything to review; how serious it is stays in data-status and in each finding's words
    const serious = headline(report([finding('a.b', 'style', 'error', 0, 'x')]));
    assert.equal(serious[0], '⚠ 1 thing to review');
    assert.deepEqual(serious.slice(2), ['attention', 'attention']);
    assert.deepEqual(headline(report(findings())).slice(0, 3), ['⚠ 5 things to review', '5 scenes checked', 'attention']);
    assert.deepEqual(headline(report([...findings(), finding('media.missing_file', 'media', 'blocking', 2, 'The picture of Scene 3 is gone.')])).slice(0, 3),
        ['✕ The lesson cannot be exported as reviewed', '5 scenes checked', 'blocked']);
    // the same words from the helper
    assert.equal(R.qualityHeadline(null), 'Quality not checked yet');
    assert.equal(R.qualityHeadline(R.qualityReport(report([]))), '✓ Quality looks good');
});

test('the section sits at the top of the panel, above the review summary', () => {
    const { panel } = setup();
    const order = panel.panel.children.map(c => c.className);
    assert.ok(order.indexOf('quality-panel') >= 0);
    assert.ok(order.indexOf('quality-panel') < order.indexOf('review-summary'));
    assert.ok(order.indexOf('asset-intro') < order.indexOf('quality-panel'));
    assert.equal(panel.qualityBox.getAttribute('aria-label'), 'Quality');
    assert.equal(hidden(panel.qualityBox), false);
});

// ---- the areas checked ------------------------------------------------------------------------------------------------

test('the areas: those with something to look at shown with ⚠ / ✕ and how many, the fine ones folded into one line', () => {
    const rep = report([
        finding('style.text_size', 'typography', 'warning', 0, 'The text of Scene 1 is small.'),
        finding('style.text_small', 'typography', 'notice', 1, 'The text of Scene 2 is small.'),
        finding('media.missing_file', 'media', 'blocking', 2, 'The picture of Scene 3 is gone.'),
        finding('style.chosen_accent', 'colour', 'info', 2, 'chosen')
    ]);
    const { quality } = setup({ rep });
    const items = quality().querySelectorAll('.quality-dimension');
    assert.equal(items.length, 16);
    const visible = items.filter(li => !hidden(li));
    assert.deepEqual(texts(visible), ['⚠ Text · 2 things to look at', '✕ Pictures and clips · 1 thing to look at']);
    assert.deepEqual(visible.map(li => [li.getAttribute('data-dimension'), li.getAttribute('data-status')]), [['typography', 'warning'], ['media', 'blocking']]);
    // the colour note is intentional: the area stays fine
    assert.equal(items.find(li => li.getAttribute('data-dimension') === 'colour').getAttribute('data-status'), 'pass');
    const fine = quality().querySelector('.quality-dimensions-fine');
    assert.match(fine.textContent, /^✓ 14 areas look fine/);
    // "Show them" unfolds the fine areas, in plain words
    const toggle = quality().querySelector('[data-action="quality-areas"]');
    assert.equal(toggle.getAttribute('aria-expanded'), 'false');
    toggle.fire('click');
    const again = quality().querySelectorAll('.quality-dimension');
    assert.equal(again.filter(li => hidden(li)).length, 0);
    assert.equal(again[0].textContent, '✓ Presenter · looks fine');
    assert.equal(quality().querySelector('[data-action="quality-areas"]').textContent, 'Hide them');
    // never the engineering words
    assert.doesNotMatch(quality().textContent, /\b(pass|error|blocking|warning|notice|severity)\b/i);
});

test('a good lesson: every area fine, no list of findings', () => {
    const { quality } = setup({ rep: report([]) });
    assert.match(quality().querySelector('.quality-dimensions-fine').textContent, /^✓ All 16 areas look fine/);
    assert.equal(quality().querySelectorAll('.quality-dimension').filter(li => !hidden(li)).length, 0);
    absent(quality().querySelector('.quality-issue'));
    absent(quality().querySelector('[data-action="quality-toggle"]'));
});

// ---- the findings ---------------------------------------------------------------------------------------------------

test('findings are grouped by scene (the whole lesson first), each with its severity as an icon and words', () => {
    const { quality } = setup();
    const groups = quality().querySelectorAll('.quality-group');
    assert.deepEqual(texts(groups.map(g => g.querySelector('.quality-group-title'))),
        ['Whole lesson', 'Scene 1 · Seeds', 'Scene 2 · Inside the leaf', 'Scene 4 · Sunlight']);
    assert.deepEqual(groups.map(g => g.getAttribute('data-scene')), ['lesson', '0', '1', '3']);
    // the scene's findings in the report's order (most serious first)
    const leaf = groups[2].querySelectorAll('.quality-issue');
    assert.deepEqual(leaf.map(li => li.getAttribute('data-severity')), ['error', 'notice']);
    assert.equal(leaf[0].querySelector('.quality-message').textContent.trim(), 'Scene 2 (“Inside the leaf”) was planned with older settings.');
    // severity: never colour alone
    const severity = li => li.querySelector('.quality-severity').textContent;
    assert.equal(severity(leaf[0]), '✕ Needs fixing');
    assert.equal(severity(leaf[1]), '⚠ Worth a look');
    assert.equal(severity(groups[0].querySelector('.quality-issue')), '⚠ Please check');
    assert.equal(groups[0].querySelector('.quality-mark').getAttribute('aria-hidden'), 'true');
    assert.deepEqual(Object.keys(R.QUALITY_SEVERITY), SEVERITIES);
    const blocked = setup({ rep: report([finding('media.missing_file', 'media', 'blocking', 2, 'The picture of Scene 3 is gone.')]) });
    assert.equal(blocked.quality().querySelector('.quality-severity').textContent, '✕ Blocks the export');
    // an intentional variation (info) is left out of the list for normal users
    assert.equal(quality().querySelectorAll('.quality-issue').length, 5);
    absent(quality().querySelector('[data-severity="info"]'));
});

test('buttons only for an automatic re-plan or a scene_style / composition suggestion the page can apply', () => {
    const { quality } = setup();
    const leaf = quality().querySelector('.quality-group[data-scene="1"]');
    const buttons = li => li.querySelectorAll('.quality-fix').map(b => [b.getAttribute('data-action'), b.textContent]);
    const [stale, accent] = leaf.querySelectorAll('.quality-issue');
    assert.deepEqual(buttons(stale), [['quality-repair', 'Fix automatically']]);
    assert.deepEqual(buttons(accent), [['quality-apply', 'Use the lesson accent']]);
    // a suggestion without a label
    assert.deepEqual(buttons(quality().querySelector('.quality-group[data-scene="3"] .quality-issue')), [['quality-apply', 'Apply suggestion']]);
    // no repair: no button
    assert.deepEqual(buttons(quality().querySelector('.quality-group[data-scene="0"] .quality-issue')), []);
    assert.deepEqual(buttons(quality().querySelector('.quality-group[data-scene="lesson"] .quality-issue')), []);
    // repairs that break the contract or are not available any more: no button
    const odd = setup({ rep: report([
        // a suggested re-plan is a button now (Phase 18 review), but only with integer scenes
        finding('a.suggest_replan', 'timing', 'warning', 0, 'one', { kind: 'suggest', class: 'timing', action: { type: 'replan', scenes: ['0'] }, label: 'x' }),
        finding('a.auto_composition', 'camera', 'warning', 0, 'two', { kind: 'auto', class: 'composition', action: { type: 'composition', scene: 0, overrides: { camera: 'static' } }, label: 'x' }),
        finding('a.auto_no_scenes', 'timing', 'warning', 0, 'three', { kind: 'auto', class: 'timing', action: { type: 'replan' }, label: 'x' }),
        finding('a.auto_style', 'style', 'warning', 0, 'three b', { kind: 'auto', class: 'presentation',
            action: { type: 'scene_style', scenes: [0], scene: 0, overrides: { style_accent: 'auto' } }, label: 'x' }),
        finding('a.auto_bad_scenes', 'timing', 'warning', 0, 'three c', replan([0, -1])),
        finding('a.suggest_none', 'style', 'warning', 0, 'four', { kind: 'suggest', class: 'content', action: { type: 'none' }, label: 'x' }),
        finding('a.suggest_empty', 'style', 'warning', 0, 'five', suggest('composition', 0, {}, 'x')),
        finding('a.suggest_noscene', 'style', 'warning', 0, 'six', suggest('scene_style', 'one', { style_accent: 'teal' }, 'x')),
        finding('a.bogus_kind', 'style', 'warning', 0, 'seven', { kind: 'magic', class: 'presentation', action: { type: 'replan', scenes: [0] }, label: 'x' }),
        finding('a.unknown_type', 'style', 'warning', 0, 'eight', { kind: 'suggest', class: 'composition', action: { type: 'delete_scene', scene: 0, overrides: { a: 1 } }, label: 'x' }),
        finding('a.done', 'timing', 'warning', 0, 'nine', replan([0]), { repair_status: 'repaired' })
    ]) });
    assert.equal(odd.quality().querySelectorAll('.quality-issue').length, 11, 'the findings are still shown');
    absent(odd.quality().querySelector('.quality-fix'));
    // the page offers no repair or no apply: those buttons are hidden
    const none = setup({ adapter: { qualityRepair: undefined, qualityApply: undefined } });
    absent(none.quality().querySelector('.quality-fix'));
    const repairOnly = setup({ adapter: { qualityApply: undefined } });
    assert.deepEqual(texts(repairOnly.quality().querySelectorAll('.quality-fix')), ['Fix automatically']);
});

test('"Fix automatically" asks the page to re-plan (the report\'s own finding), then everything is drawn again', async () => {
    const wait = deferred();
    const { quality, calls, changes, session, state } = setup({ adapter: { qualityRepair: issue => { calls.push(['repair', issue]); return wait.promise; } } });
    const issue = state.report.issues.find(f => f.rule === 'core.stale_plan');
    quality().querySelector('[data-action="quality-repair"]').fire('click');
    assert.equal(calls.length, 1);
    assert.equal(calls[0][0], 'repair');
    assert.equal(calls[0][1], issue, 'the finding exactly as the report has it');
    // while it runs: said so, and every quality action waits
    assert.equal(quality().querySelector('.quality-state').textContent, 'Fixing…');
    assert.ok(quality().querySelectorAll('.quality-fix').every(b => b.getAttribute('disabled') !== null));
    assert.notEqual(quality().querySelector('[data-action="quality-run"]').getAttribute('disabled'), null);
    assert.equal(session.busy, true);
    quality().querySelector('[data-action="quality-apply"]').fire('click');
    assert.equal(calls.length, 1, 'one action at a time');
    // the session refuses a second action itself (a disabled button is not the only guard); checked before the first one ends
    const again = [session.repairQuality(issue), session.applyQuality(state.report.issues.find(f => f.rule === 'style.scene_accent')), session.checkQuality()];
    assert.equal(calls.length, 1);
    wait.resolve();
    await settle();
    assert.deepEqual(await Promise.all(again), [null, null, null]);
    assert.equal(session.busy, false);
    assert.equal(quality().querySelector('.quality-state').textContent, 'Fixed: the scene was planned again (nothing was generated; your approvals are kept).');
    assert.deepEqual(changes, [1], 'saved like any decision');
    assert.equal(calls.filter(([k]) => k === 'review').length, 0, 'no review was made');
});

test('"Apply suggestion" goes through the page; a composition decision it answers with is recorded in the scene', async () => {
    const { quality, calls, changes, session, slides, state } = setup({ adapter: {
        qualityApply: async issue => {
            calls.push(['apply', issue]);
            return { review: { status: 'changed', overrides: { ...issue.repair.action.overrides } }, plan: { ...slides[1].cinematic_plan, review_status: 'changed' } };
        } } });
    const issue = state.report.issues.find(f => f.rule === 'style.scene_accent');
    quality().querySelector('.quality-group[data-scene="1"] [data-action="quality-apply"]').fire('click');
    assert.equal(quality().querySelector('.quality-state').textContent, 'Applying the suggestion…');
    await settle();
    assert.deepEqual(calls, [['apply', issue]]);
    assert.deepEqual(slides[1].visual_review.composition, { status: 'changed', overrides: { style_accent: 'auto' } });
    assert.equal(slides[1].cinematic_plan.review_status, 'changed');
    assert.deepEqual(slides[1].visual_review.main, { status: 'approved', asset_id: 7 }, 'the visual keeps its review');
    assert.equal(session.all.find(i => i.sceneIndex === 1 && i.slot === 'composition').status, 'changed');
    assert.equal(session.all.find(i => i.sceneIndex === 1 && i.slot === 'main').status, 'approved');
    assert.equal(quality().querySelector('.quality-state').textContent, 'Applied: the scene now uses this suggestion as your choice (nothing was generated).');
    assert.deepEqual(changes, [1]);
    // an answer that is not a composition decision changes nothing in the scene
    const other = setup({ adapter: { qualityApply: async () => ({ ok: true }) } });
    const before = JSON.stringify(other.slides);
    other.quality().querySelector('[data-action="quality-apply"]').fire('click');
    await settle();
    assert.equal(JSON.stringify(other.slides), before);
});

test('"Check again" runs the check: "Checking…" while it runs, the new report after', async () => {
    const wait = deferred();
    const { quality, calls, state, session } = setup({ adapter: { qualityRun: () => { calls.push(['run']); return wait.promise; } } });
    const run = quality().querySelector('[data-action="quality-run"]');
    assert.equal(run.textContent, 'Check again');
    run.fire('click');
    assert.deepEqual(calls, [['run']]);
    assert.equal(quality().querySelector('.quality-state').textContent, 'Checking…');
    assert.equal(quality().getAttribute('aria-busy'), 'true');
    assert.notEqual(quality().querySelector('[data-action="quality-run"]').getAttribute('disabled'), null);
    quality().querySelector('[data-action="quality-run"]').fire('click');
    assert.equal(calls.length, 1);
    state.report = report([]);
    wait.resolve(state.report);
    await settle();
    assert.equal(quality().querySelector('.quality-headline').textContent, '✓ Quality looks good');
    assert.equal(quality().querySelector('.quality-state').textContent, '');
    assert.equal(quality().getAttribute('aria-busy'), null);
    assert.equal(session.all.some(i => i.quality), false, 'the chips follow the new report');
});

test('a page with only qualityRun: "Check quality" first, then the report it gave; with only quality(): no button', async () => {
    let answer = report(findings());
    const runOnly = setup({ adapter: { quality: undefined, qualityRun: async () => answer } });
    assert.equal(runOnly.quality().querySelector('.quality-headline').textContent, 'Quality not checked yet');
    assert.equal(runOnly.quality().getAttribute('data-status'), 'none');
    runOnly.quality().querySelector('[data-action="quality-run"]').fire('click');
    await settle();
    assert.equal(runOnly.quality().querySelector('.quality-headline').textContent, '⚠ 5 things to review');
    assert.equal(runOnly.quality().querySelector('[data-action="quality-run"]').textContent, 'Check again');
    answer = 'not a report';
    runOnly.quality().querySelector('[data-action="quality-run"]').fire('click');
    await settle();
    assert.equal(runOnly.quality().querySelector('.quality-headline').textContent, '⚠ 5 things to review', 'a bad answer keeps the last report');
    const readOnly = setup({ adapter: { qualityRun: undefined } });
    absent(readOnly.quality().querySelector('[data-action="quality-run"]'));
    assert.equal(readOnly.quality().querySelector('.quality-headline').textContent, '⚠ 5 things to review');
});

test('stale: "The lesson changed since this check" with "Check again"; fixes wait for a new check', () => {
    const { quality, state, panel, session } = setup();
    absent(quality().querySelector('.quality-stale'));
    state.stale = true;
    panel.render();
    // Phase 21: an icon and words, and "Check again" right there
    const note = quality().querySelector('.quality-stale');
    assert.equal(note.querySelector('.quality-stale-text').textContent, 'The lesson changed since this check.');
    assert.equal(note.querySelector('.quality-mark').textContent, '⚠');
    assert.equal(note.querySelector('.quality-mark').getAttribute('aria-hidden'), 'true');
    assert.equal(note.textContent, '⚠ The lesson changed since this check. Check again');
    const run = quality().querySelector('[data-action="quality-run"]');
    assert.equal(run.textContent, 'Check again');
    assert.equal(run.getAttribute('disabled'), null);
    const again = note.querySelector('[data-action="quality-run-stale"]');
    assert.equal(again.textContent, 'Check again');
    assert.equal(again.getAttribute('aria-label'), 'Check the lesson again');
    assert.equal(again.getAttribute('disabled'), null);
    assert.ok(again.classList.contains('ui-btn') && again.classList.contains('ui-btn-link'));
    assert.equal(quality().querySelectorAll('.quality-run').length, 1, 'the head keeps the one .quality-run');
    const fixes = quality().querySelectorAll('.quality-fix');
    assert.ok(fixes.length >= 3);
    assert.ok(fixes.every(b => b.getAttribute('disabled') !== null));
    assert.ok(fixes.every(b => /^Check again first/.test(b.getAttribute('title'))));
    // the scene's own block says it is from an earlier check
    session.select(session.items.findIndex(i => i.sceneIndex === 1 && i.slot === 'composition'));
    panel.render();
    assert.match(panel.detail.querySelector('.review-quality-auto').textContent, /from an earlier check/);
    // a failing qualityStale reads as "not stale"
    const failing = setup({ adapter: { qualityStale: () => { throw new Error('x'); } } });
    absent(failing.quality().querySelector('.quality-stale'));
});

test('stale: the note\'s "Check again" runs the check (and waits while one runs); a page without qualityRun has no such button', async () => {
    const wait = deferred();
    const { quality, state, panel, calls } = setup({ adapter: { qualityRun: () => { calls.push(['run']); return wait.promise; } } });
    state.stale = true;
    panel.render();
    quality().querySelector('[data-action="quality-run-stale"]').fire('click');
    assert.deepEqual(calls, [['run']]);
    assert.equal(quality().querySelector('.quality-state').textContent, 'Checking…');
    assert.notEqual(quality().querySelector('[data-action="quality-run-stale"]').getAttribute('disabled'), null);
    state.stale = false;
    wait.resolve(state.report);
    await settle();
    absent(quality().querySelector('.quality-stale'));
    const readOnly = setup({ adapter: { qualityRun: undefined } });
    readOnly.state.stale = true;
    readOnly.panel.render();
    assert.equal(readOnly.quality().querySelector('.quality-stale-text').textContent, 'The lesson changed since this check.');
    absent(readOnly.quality().querySelector('[data-action="quality-run-stale"]'));
});

test('Phase 21: a one-line help, labelled "Show scene" links and the product buttons; no engineering words for normal users', () => {
    const { quality } = setup();
    assert.equal(quality().querySelector('.quality-help').textContent, 'Checks your lesson for readability and consistency.');
    const head = quality().children.map(c => c.className);
    assert.ok(head.indexOf('quality-head') < head.indexOf('quality-help'), 'under the headline');
    const show = quality().querySelector('.quality-group[data-scene="3"] [data-action="quality-show"]');
    assert.equal(show.textContent, 'Show scene');
    assert.equal(show.getAttribute('aria-label'), 'Show scene 4');
    for (const action of ['quality-run', 'quality-toggle', 'quality-repair', 'quality-apply']) {
        const button = quality().querySelector(`[data-action="${action}"]`);
        assert.ok(button.classList.contains('ui-btn') && button.classList.contains('export-action'), action);
    }
    const toggle = quality().querySelector('[data-action="quality-toggle"]');
    assert.equal(toggle.getAttribute('aria-controls'), quality().querySelector('.quality-details').getAttribute('id'));
    // what the browser check calls engineering words never shows outside the debug view
    assert.doesNotMatch(quality().textContent, /\b(?:plan_hash|fingerprint|token|evidence|provider|gemini|openai|rule|dimension)\b|--st-/i);
    // the help line is part of the section only when the page offers quality
    const none = setup({ adapter: { quality: undefined, qualityRun: undefined } });
    absent(none.quality().querySelector('.quality-help'));
});

test('errors from the page: a short plain message, and the panel keeps working', async () => {
    const run = setup({ adapter: { qualityRun: async () => { throw new Error('HTTP 500 Traceback (most recent call last)'); } } });
    run.quality().querySelector('[data-action="quality-run"]').fire('click');
    await settle();
    const state = run.quality().querySelector('.quality-state');
    assert.equal(state.textContent, 'The quality check could not run. Please try again.');
    assert.equal(state.getAttribute('data-kind'), 'error');
    assert.equal(run.quality().querySelector('[data-action="quality-run"]').getAttribute('disabled'), null);
    assert.equal(run.quality().querySelector('.quality-headline').textContent, '⚠ 5 things to review', 'the last report stays');
    assert.ok(run.panel.detail.querySelector('[data-action="keep"]'), 'the review still works');
    const repair = setup({ adapter: { qualityRepair: async () => { throw new Error('boom'); } } });
    repair.quality().querySelector('[data-action="quality-repair"]').fire('click');
    await settle();
    assert.equal(repair.quality().querySelector('.quality-state').textContent, 'The automatic fix could not be applied. Please try again.');
    assert.equal(repair.session.busy, false);
    assert.deepEqual(repair.changes, []);
    assert.equal(repair.quality().querySelector('[data-action="quality-repair"]').getAttribute('disabled'), null);
    const apply = setup({ adapter: { qualityApply: async () => { throw new Error('boom'); } } });
    apply.quality().querySelector('[data-action="quality-apply"]').fire('click');
    await settle();
    assert.equal(apply.quality().querySelector('.quality-state').textContent, 'The suggestion could not be applied. Please try again.');
    // the page's own words only in the debug view
    const debug = setup({ debug: true, adapter: { qualityRun: async () => { throw new Error('the server could not be reached'); } } });
    debug.quality().querySelector('[data-action="quality-run"]').fire('click');
    await settle();
    assert.equal(debug.quality().querySelector('.quality-state').textContent, 'The quality check could not run. Please try again. (the server could not be reached)');
    // a quality() that throws: no report, nothing breaks
    const broken = setup({ adapter: { quality: () => { throw new Error('x'); } } });
    assert.equal(broken.quality().querySelector('.quality-headline').textContent, 'Quality not checked yet');
    assert.ok(broken.panel.list.querySelector('.review-item'));
});

test('"Show scene" selects the scene in the review list (its composition, presenter or visual), across a filter', () => {
    const rep = report([...findings(), finding('media.blurry', 'media', 'warning', 1, 'The picture of Scene 2 is blurry.'),
        finding('core.no_plan', 'preview_export', 'error', 4, 'Scene 5 (“Quiz”) has no composition yet.')]);
    const { quality, session, panel, calls } = setup({ rep, adapter: { selectScene: i => calls.push(['select', i]) } });
    const show = scene => quality().querySelector(`.quality-group[data-scene="${scene}"] [data-action="quality-show"]`);
    assert.equal(show(3).textContent, 'Show scene');
    show(3).fire('click');
    assert.equal(session.current.sceneIndex, 3);
    assert.equal(session.current.slot, 'composition');
    assert.equal(panel.list.querySelector('.review-item.selected').getAttribute('data-scene'), '3');
    // a finding about a picture opens the scene's visual
    quality().querySelectorAll('.quality-group[data-scene="1"] .quality-issue')
        .find(li => /blurry/.test(li.textContent)).querySelector('[data-action="quality-show"]').fire('click');
    assert.equal(session.current.sceneIndex, 1);
    assert.equal(session.current.slot, 'main');
    // a filter that hides the scene gives way to "All"
    assert.ok(session.setFilter('approved'));
    panel.render();
    show(3).fire('click');
    assert.equal(session.filter, 'all');
    assert.equal(session.current.sceneIndex, 3);
    // a scene with nothing to review is left to the page
    show(4).fire('click');
    assert.deepEqual(calls.filter(([k]) => k === 'select'), [['select', 4]]);
    // no selectScene: no link for that scene; the lesson-wide finding never has one
    const plain = setup({ rep });
    absent(plain.quality().querySelector('.quality-group[data-scene="4"] [data-action="quality-show"]'));
    assert.ok(plain.quality().querySelector('.quality-group[data-scene="3"] [data-action="quality-show"]'));
    absent(plain.quality().querySelector('.quality-group[data-scene="lesson"] [data-action="quality-show"]'));
});

test('"Hide details" folds the areas and the findings away; the headline stays', () => {
    const { quality } = setup();
    const toggle = quality().querySelector('[data-action="quality-toggle"]');
    assert.equal(toggle.textContent, 'Hide details ▴');
    assert.equal(hidden(quality().querySelector('.quality-details')), false);
    toggle.fire('click');
    assert.equal(hidden(quality().querySelector('.quality-details')), true);
    assert.equal(quality().querySelector('[data-action="quality-toggle"]').textContent, 'Show details ▾');
    assert.equal(quality().querySelector('.quality-headline').textContent, '⚠ 5 things to review');
});

test('limitations are small notes', () => {
    const { quality } = setup({ rep: report(findings(), { limitations: ['Pictures and clips were not looked up (save the lesson to check its files).',
        'Pictures and clips were not looked up (save the lesson to check its files).', 42, ''] }) });
    assert.deepEqual(texts(quality().querySelectorAll('.quality-limitation')), ['Pictures and clips were not looked up (save the lesson to check its files).']);
    assert.ok(quality().querySelector('.quality-limitation').classList.contains('review-note'));
});

// ---- the debug view ---------------------------------------------------------------------------------------------------

test('debug only: each finding\'s rule, area, severity, repair class and evidence, and the report\'s rules and fingerprint', () => {
    const plain = setup();
    absent(plain.quality().querySelector('.quality-debug'));
    absent(plain.quality().querySelector('.quality-debug-report'));
    assert.doesNotMatch(plain.quality().textContent, /core\.stale_plan|preview_export|quality_rules|0123456789abcdef/);
    const { quality } = setup({ debug: true });
    const stale = quality().querySelectorAll('.quality-issue').find(li => /older settings/.test(li.textContent));
    assert.equal(stale.querySelector('.quality-debug-facts').textContent,
        'rule: core.stale_plan · dimension: preview_export · severity: error · repair: auto (presentation) · replan');
    assert.deepEqual(JSON.parse(stale.querySelector('.quality-evidence').textContent), { key: 'stale_plan', found: [1, 2] });
    const reportDebug = JSON.parse(quality().querySelector('.quality-debug-report').textContent);
    assert.equal(reportDebug.rules, 'quality_rules@1');
    assert.equal(reportDebug.fingerprint, '0123456789abcdef');
    // the intentional notes are listed in the debug view
    const info = quality().querySelector('.quality-issue[data-severity="info"]');
    assert.ok(info);
    assert.equal(info.querySelector('.quality-severity').textContent, 'ℹ Noted');
    assert.equal(quality().querySelector('.quality-group[data-scene="2"] .quality-group-title').textContent, 'Scene 3 · Roots');
});

// ---- data stays data ------------------------------------------------------------------------------------------------

test('markup in messages, labels, titles, evidence and limitations stays text; odd values never become attributes', () => {
    const slides = lesson();
    slides[1].title = '<b onclick="x()">Leaf</b>';
    const rep = report([
        finding('style.scene_accent', 'colour', 'warning', 1, '<img src=x onerror=alert(1)> is <script>bad</script>',
            suggest('scene_style', 1, { style_accent: 'auto' }, '<i>Fix</i> it'), { evidence: { found: '<script>x</script>' }, id: '"><svg onload=x>' })
    ], { limitations: ['<u>note</u>'] });
    rep.dimensions.colour.label = '<em>Colours</em>';
    const { quality, panel } = setup({ slides, rep, debug: true });
    const li = quality().querySelector('.quality-issue');
    assert.equal(li.querySelector('.quality-message').textContent.trim(), '<img src=x onerror=alert(1)> is <script>bad</script>');
    assert.equal(li.querySelector('[data-action="quality-apply"]').textContent, '<i>Fix</i> it');
    assert.equal(li.getAttribute('data-issue'), null, 'an id that is not a hash never reaches an attribute');
    assert.equal(quality().querySelector('.quality-group-title').textContent, 'Scene 2 · <b onclick="x()">Leaf</b>');
    assert.match(li.querySelector('.quality-evidence').textContent, /<script>x<\/script>/);
    assert.equal(quality().querySelector('.quality-limitation').textContent, '<u>note</u>');
    panel.qualityAllAreas = true;
    panel.render();
    assert.match(quality().querySelector('.quality-dimension[data-dimension="colour"]').textContent, /<em>Colours<\/em>/);
    // only the elements the section builds: no markup was parsed, nothing went through innerHTML
    const tags = new Set(quality().all().filter(n => n.tag !== '#text').map(n => n.tag));
    for (const tag of ['img', 'script', 'b', 'i', 'u', 'em', 'svg']) assert.equal(tags.has(tag), false, tag);
    assert.ok(quality().all().every(n => n._html === undefined));
    assert.ok(panel.list.all().every(n => n._html === undefined));
});

test('malformed findings are ignored; enum fields are checked before they reach an attribute', () => {
    const good = finding('education.term_casing', 'terminology', 'notice', 0, 'The term “Chlorophyll” is written two ways.');
    const raw = report([good]);
    raw.issues.push(null, 'text', 42, [good], { ...good, severity: 'critical' }, { ...good, severity: 'Warning' }, { ...good, message: '' },
        { ...good, message: '   ' }, { ...good, message: { toString: () => 'x' } }, { ...good, scene: -1 }, { ...good, scene: 1.5 }, { ...good, scene: '2' },
        { ...good, scene: true }, { ...good, scene: 5000 });
    raw.issues.push({ ...good, id: 'abc123', dimension: '"><x', rule: 'Not A Rule', repair: 'auto', message: 'Kept with an odd dimension and repair.' });
    raw.dimensions.style = { label: 'Style', status: 'great', issues: 1 };
    raw.dimensions['bad key'] = { label: 'Bad', status: 'warning', issues: 1 };
    raw.dimensions.camera = 'warning';
    raw.status = 'excellent'; // unknown: derived from the findings
    const normal = R.qualityReport(raw);
    assert.equal(normal.issues.length, 2);
    assert.equal(normal.status, 'review');
    assert.equal(normal.issues[1].dimension, null);
    assert.equal(normal.issues[1].rule, null);
    assert.equal(normal.issues[1].fix, null);
    assert.equal(normal.dimensions.some(d => ['style', 'bad key', 'camera'].includes(d.key)), false);
    const { quality, panel } = setup({ rep: raw });
    assert.equal(quality().querySelectorAll('.quality-issue').length, 2);
    assert.equal(quality().querySelector('.quality-headline').textContent, '⚠ 2 things to review');
    const severities = quality().all().map(n => n.getAttribute && n.getAttribute('data-severity')).filter(v => v !== null && v !== undefined);
    assert.ok(severities.every(v => SEVERITIES.includes(v)));
    const statuses = quality().querySelectorAll('.quality-dimension').map(li => li.getAttribute('data-status'));
    assert.ok(statuses.every(v => ['pass', 'notice', 'warning', 'error', 'blocking'].includes(v)));
    assert.ok(panel.list.querySelectorAll('.quality-chip').every(c => SEVERITIES.includes(c.getAttribute('data-severity'))));
    // not a report at all
    for (const bad of [null, 'report', 42, [], [report([])]]) {
        assert.equal(R.qualityReport(bad), null);
        assert.equal(setup({ rep: bad }).quality().querySelector('.quality-headline').textContent, 'Quality not checked yet');
    }
    // no issues list, no dimensions: a headline only
    const bare = setup({ rep: { status: 'good' } });
    assert.equal(bare.quality().querySelector('.quality-headline').textContent, '✓ Quality looks good');
    absent(bare.quality().querySelector('.quality-scenes'));
    absent(bare.quality().querySelector('.quality-dimension'));
});

// ---- the review list: chips and the "Quality" filter ------------------------------------------------------------------

test('scenes with findings get a chip next to their status, once per scene, with the most serious mark', () => {
    const { panel } = setup();
    const chips = panel.list.querySelectorAll('.quality-chip');
    const at = chip => { let n = chip.parent; while (n && !n.getAttribute('data-scene')) n = n.parent; return [n.getAttribute('data-scene'), n.getAttribute('data-slot')]; };
    assert.deepEqual(chips.map(at), [['0', 'composition'], ['1', 'main'], ['3', 'composition']]);
    assert.deepEqual(texts(chips), ['⚠ 1', '✕ 2', '⚠ 1']);
    assert.deepEqual(chips.map(c => c.getAttribute('data-severity')), ['notice', 'error', 'warning']);
    assert.equal(chips[1].getAttribute('title'), 'Quality: 2 things to look at in this scene');
    // next to the review status, which is unchanged
    const row = panel.list.querySelector('.review-item[data-scene="1"]');
    const parts = row.children.map(c => c.className);
    assert.deepEqual(parts, ['review-item-main', 'review-chip', 'quality-chip']);
    assert.equal(row.querySelector('.review-chip').textContent, '✓ Approved');
    // scene 3 has only an intentional note: no chip
    absent(panel.list.querySelector('.review-item[data-scene="2"] .quality-chip'));
    // Phase 21: a scene's composition is its "Layout" in the list (capitalised, never the internal slot name)
    const layout = panel.list.querySelector('.review-item[data-scene="0"][data-slot="composition"]');
    assert.equal(layout.querySelector('.review-item-title').textContent, 'Scene 1 · Layout: Seeds');
    assert.equal(layout.querySelector('.review-item-meta').textContent, 'Layout · Comparison');
    assert.equal(panel.list.querySelector('.review-item[data-scene="1"][data-slot="main"] .review-item-title').textContent, 'Scene 2: Inside the leaf');
    assert.ok(!panel.list.querySelectorAll('.review-item-title').some(t => /· (composition|presenter|main|side panel):/.test(t.textContent)));
    assert.equal(panel.filters.querySelector('[data-filter="composition"]').textContent, 'Layout');
});

test('the "Quality" filter shows the scenes with something to look at (notice or more serious)', () => {
    const { panel, session } = setup();
    const button = panel.filters.querySelector('[data-filter="quality"]');
    assert.equal(button.textContent, 'Quality');
    assert.equal(hidden(button), false);
    assert.equal(R.FILTERS.quality.label, 'Quality');
    button.fire('click');
    assert.equal(session.filter, 'quality');
    assert.deepEqual(session.items.map(i => [i.sceneIndex, i.slot]), [[0, 'composition'], [1, 'main'], [1, 'composition'], [3, 'composition']]);
    assert.equal(button.getAttribute('aria-pressed'), 'true');
    assert.equal(R.filterItems(session.all, 'quality').some(i => i.sceneIndex === 2), false, 'an intentional note is not a reason');
    // a page without quality: the filter is hidden, and no scene matches it
    const none = setup({ adapter: { quality: undefined, qualityRun: undefined } });
    assert.equal(hidden(none.panel.filters.querySelector('[data-filter="quality"]')), true);
    assert.equal(R.filterItems(none.session.all, 'quality').length, 0);
});

test('without quality functions the section, the chips and the scene block are absent', () => {
    const { panel, session } = setup({ adapter: { quality: undefined, qualityRun: undefined, qualityStale: undefined, qualityRepair: undefined, qualityApply: undefined } });
    assert.equal(hidden(panel.qualityBox), true);
    assert.equal(panel.qualityBox.children.length, 0);
    absent(panel.list.querySelector('.quality-chip'));
    session.select(session.items.findIndex(i => i.slot === 'composition' && i.sceneIndex === 1));
    panel.render();
    absent(panel.detail.querySelector('.review-quality'));
    assert.ok(panel.detail.querySelector('[data-action="keep"]'));
});

// ---- the scene's own block in the composition inspector -------------------------------------------------------------

test('the composition inspector lists the scene\'s findings in a "Quality" block, under the drawing', async () => {
    const { panel, session, calls } = setup();
    // a visual's detail has no quality block
    assert.equal(session.current.sceneIndex, 0);
    session.select(session.items.findIndex(i => i.sceneIndex === 1 && i.slot === 'main'));
    panel.render();
    absent(panel.detail.querySelector('.review-quality'));
    session.select(session.items.findIndex(i => i.sceneIndex === 1 && i.slot === 'composition'));
    panel.render();
    const block = panel.detail.querySelector('.review-quality');
    assert.ok(block);
    assert.equal(block.getAttribute('data-severity'), 'error');
    assert.equal(block.querySelector('.review-quality-auto').textContent, 'Quality: 2 things to look at in this scene');
    const order = panel.detail.children.map(c => c.className);
    assert.ok(order.indexOf('review-preview') < order.indexOf('review-quality'), 'under the drawing');
    assert.ok(order.indexOf('review-quality') < order.indexOf('review-composition-decided'), 'above the composition block');
    const items = block.querySelectorAll('.quality-issue');
    assert.deepEqual(items.map(li => li.querySelector('.quality-severity').textContent), ['✕ Needs fixing', '⚠ Worth a look']);
    absent(block.querySelector('[data-action="quality-show"]'), 'it is the scene shown');
    assert.deepEqual(texts(block.querySelectorAll('.quality-fix')), ['Fix automatically', 'Use the lesson accent']);
    block.querySelector('[data-action="quality-repair"]').fire('click');
    await settle();
    assert.equal(calls.filter(([k]) => k === 'repair').length, 1);
    // a scene with only an intentional note: no block (in the debug view, a block with the note)
    session.select(session.items.findIndex(i => i.sceneIndex === 2));
    panel.render();
    absent(panel.detail.querySelector('.review-quality'));
    const debug = setup({ debug: true });
    debug.session.select(debug.session.items.findIndex(i => i.sceneIndex === 2));
    debug.panel.render();
    assert.equal(debug.panel.detail.querySelector('.review-quality-auto').textContent, 'Quality: only notes in this scene');
    assert.equal(debug.panel.detail.querySelector('.review-quality').getAttribute('data-severity'), 'info');
});

// ---- approvals are never touched ------------------------------------------------------------------------------------

test('quality never changes a review, a status or an approval: drawing, filtering, checking again', async () => {
    const slides = lesson();
    const rep = report([...findings(), finding('media.missing_file', 'media', 'blocking', 0, 'The picture of Scene 1 is gone.'),
        finding('style.contrast', 'colour', 'error', 1, 'The text of Scene 2 is hard to read.')]);
    const plainSession = new R.ReviewSession({ slides: lesson(), api: {}, media: {}, composition: { enabled: () => true } });
    const before = JSON.stringify(slides);
    const mediaCalls = [];
    const { panel, session, calls, quality } = setup({ slides, rep, api: { review: async body => { mediaCalls.push(body); return {}; } } });
    assert.equal(quality().getAttribute('data-status'), 'blocked');
    // the statuses and the summary are what they would be without quality
    assert.deepEqual(session.all.map(i => [i.sceneIndex, i.slot, i.status, i.stale]), plainSession.all.map(i => [i.sceneIndex, i.slot, i.status, i.stale]));
    assert.deepEqual(session.summary, plainSession.summary);
    assert.equal(session.all.find(i => i.sceneIndex === 0).status, 'approved');
    assert.equal(session.all.find(i => i.sceneIndex === 1 && i.slot === 'main').status, 'approved');
    // filter, show a scene, check again, unfold, debug: still nothing changed
    panel.filters.querySelector('[data-filter="quality"]').fire('click');
    quality().querySelector('.quality-group[data-scene="1"] [data-action="quality-show"]').fire('click');
    quality().querySelector('[data-action="quality-run"]').fire('click');
    await settle();
    quality().querySelector('[data-action="quality-areas"]').fire('click');
    panel.debug = true;
    panel.render();
    assert.equal(JSON.stringify(slides), before, 'the lesson (its reviews and plans) is untouched');
    assert.deepEqual(calls.filter(([k]) => k !== 'run'), [], 'no decision was asked');
    assert.deepEqual(mediaCalls, []);
    assert.deepEqual(session.all.map(i => i.status), plainSession.all.map(i => i.status));
    // "Next needing review" and the approved filter ignore quality
    assert.ok(R.filterItems(session.all, 'approved').some(i => i.sceneIndex === 0));
    assert.equal(R.summarize(session.all).approved, plainSession.summary.approved);
});

// ---- the API client ----------------------------------------------------------------------------------------------------

test('CinematicApi.quality posts the lesson to /api/quality/lesson and reports errors honestly', async () => {
    const sent = [];
    let answer = { ok: true, status: 200, json: async () => report(findings()) };
    const api = new C.CinematicApi({ fetch: async (url, init) => { sent.push([url, init]); return answer; } });
    const scenes = [{ title: 'a' }];
    const settings = { mode: 'cinematic' };
    const map = [{ id: 'c1', title: 'Photosynthesis', depends_on: [] }];
    const got = await api.quality(scenes, settings, 7, map);
    assert.equal(sent[0][0], '/api/quality/lesson');
    assert.equal(sent[0][1].method, 'POST');
    assert.equal(sent[0][1].headers['Content-Type'], 'application/json');
    assert.deepEqual(JSON.parse(sent[0][1].body), { scenes, settings, project_id: 7, concept_map: map });
    assert.equal(got.status, 'attention');
    assert.equal(got.issues.length, 6);
    // the page's { concept_map } works too; nothing known: neither sent
    await api.quality(scenes, settings, 7, { concept_map: map });
    assert.deepEqual(JSON.parse(sent[1][1].body).concept_map, map);
    await api.quality(scenes, settings, null);
    assert.deepEqual(JSON.parse(sent[2][1].body), { scenes, settings });
    await api.quality(scenes, settings, undefined, 'nope');
    assert.deepEqual(Object.keys(JSON.parse(sent[3][1].body)), ['scenes', 'settings']);
    // the server's words when it refuses
    answer = { ok: false, status: 404, json: async () => ({ detail: 'Lesson not found.' }) };
    await assert.rejects(() => api.quality(scenes, settings, 9), err => err.message === 'Lesson not found.' && err.status === 404);
    answer = { ok: false, status: 500, json: async () => { throw new Error('not json'); } };
    await assert.rejects(() => api.quality(scenes, settings, 9), /error 500/);
});

// ---- a suggested re-plan (Phase 18 review: one that would ask an approved scene again) -------------------------------------

test('a suggested re-plan has its own button with the engine label and is applied on click', () => {
    const R = require('../review.js');
    const f = R.qualityIssue({ id: 'a1b2c3d4e5f6', rule: 'core.stale_plan', dimension: 'preview_export', severity: 'error', scene: 2,
        message: 'Scene 3 was planned with older settings.', evidence: {},
        repair: { kind: 'suggest', class: 'composition', action: { type: 'replan', scenes: [2] }, label: "Re-plan (the scene's approval will be asked again)" },
        repair_status: 'available' });
    assert.equal(f.fix, 'replan');
    assert.deepEqual(f.scenes, [2]);
    const auto = R.qualityIssue({ id: 'a1b2c3d4e5f7', rule: 'core.stale_plan', dimension: 'preview_export', severity: 'error', scene: 2,
        message: 'm', evidence: {}, repair: { kind: 'auto', class: 'presentation', action: { type: 'replan', scenes: [2] }, label: 'Re-plan' } });
    assert.equal(auto.fix, 'auto');
    const bad = R.qualityIssue({ id: 'a1b2c3d4e5f8', rule: 'core.stale_plan', dimension: 'preview_export', severity: 'error', scene: 2,
        message: 'm', evidence: {}, repair: { kind: 'suggest', class: 'composition', action: { type: 'replan', scenes: ['2'] }, label: 'x' } });
    assert.equal(bad.fix, null, 'scene numbers must be integers');
});

