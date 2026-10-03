'use strict';
// Unit tests for the synchronization in Visual Review (Phase 16): a scene's sync plan (plan.sync) put into plain words
// (cinematic.js syncSummary: the lesson's words read from the scene, never from the plan), the read-only "Synchronization"
// block of the scene inspector (review.js), and the script editor's narration edit that retimes a cinematic lesson
// (review.js retimeAfterNarrationEdit, called by index.html).
// Run from the repo root:  node --test "tests/*.test.js"
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const C = require('../cinematic.js');
const R = require('../review.js');
const { fakeDoc } = require('./helpers/cinematic-dom.js');

const box = (x, y, w, h) => ({ x, y, w, h });

// ---- a scene with every kind of concept a plan can point at ----------------------------------------------------------

const LONG_STEP = 'Water is split into hydrogen and oxygen inside the chloroplast, a long step that keeps going';
function scene(extra = {}) {
    return {
        title: 'Plants and animals', narration: 'Plants make food. [SYNC] Animals eat it. [PAUSE] Look at the diagram.',
        html: '<div class="definition"><p>Photosynthesis is how a plant makes food with <strong>chlorophyll</strong>.</p></div>'
            + '<table><tr><th>Feature</th><th>Plants</th><th>Animals</th></tr><tr><td>Food</td><td>make it</td><td>eat it</td></tr></table>'
            + `<ol><li>Light is <span class="keyword">absorbed</span> by the leaves &amp; stems</li><li>${LONG_STEP}<ul><li>inside</li></ul></li><li>Sugar is made</li></ol>`
            + '<pre>print(2 + 2)</pre><p>Output: 4</p><div class="formula-block">F = ma</div>',
        composition: { labels: [{ text: 'Mass' }, 'Force'] },
        visual: { keywords: ['stomata'] },
        visual_direction: { annotations: [{ text: 'a = acceleration' }] },
        ...extra
    };
}

const at = (ratio, segment = 0) => ({ segment, ratio });
function event(id, type, target, estimate, anchor, concept = null, extra = {}) {
    return { id, type, target, at: at(Math.min(1, estimate / 10)), estimate, duration: 1.4, tolerance: 0.25, priority: 2, anchor, concept,
        depends_on: [], params: {}, ...extra };
}
const ref = (kind, index = 0) => ({ kind, index });

function sync(events, extra = {}) {
    return { version: 1, fingerprint: 'abcdef0123456789', timing_source: 'narration',
        narration: { segments: [{ index: 0, chars: 120, pause_after: 1, start_estimate: 0, estimate: 10 }], estimate_total: 11 },
        events, attention: [], end_hold: 0, fallback: null,
        metrics: { anchors: events.length, resolved: events.length, fallbacks: 0, conflicts: 0, orphans_removed: 0, unsupported: 0 }, ...extra };
}

function plan(syncPlan, extra = {}) {
    return {
        version: 1, template: 'comparison', template_label: 'Comparison', duration: 11, shot: 'medium',
        style: { typography: 'academic', motion: 'subtle', transitions: 'fade', background: 'auto', emphasis: 'clear' },
        background: { type: 'gradient' }, presenter: { type: 'illustrated', presenter_id: 'aadhi-teacher', shown: true, side: 'right' },
        transition: { in: 'fade', out: 'fade', duration: 0.5 },
        camera: { shot: 'medium', movement: 'focus', from: { x: 0, y: 0, w: 1 }, to: { x: 0.03, y: 0.03, w: 0.94 }, start: 1, duration: 3, target: 'board' },
        layers: [
            { id: 'visual', type: 'visual', role: 'image', box: box(0.05, 0.19, 0.35, 0.52), z: 20, start: 0.15, enter: 'scale_in', important: true, camera: true },
            { id: 'board', type: 'board', role: 'comparison', style: 'body', box: box(0.42, 0.19, 0.53, 0.5), z: 30, start: 0.1, enter: 'fade_in', important: true, camera: true },
            { id: 'labels', type: 'labels', box: box(0.05, 0.72, 0.5, 0.08), z: 50, start: 0, important: false, camera: true,
                items: [{ source: { kind: 'screenplay', index: 1 } }, { source: { kind: 'direction', index: 0 } }, { source: { kind: 'screenplay', index: 0 } }] },
            { id: 'subtitles', type: 'subtitles', box: box(0, 0.85, 1, 0.15), z: 90, start: 0, important: false, camera: false }
        ],
        timeline: [], warnings: [], notes: [], review_status: 'pending',
        composition: { version: 1, mode: 'rules', source: 'rules', decision: { template: 'comparison' }, locked: [], reasons: ['both sides are taught'],
            repairs: [], intent: { purpose: 'comparison', density: 'medium' } },
        sync: syncPlan, ...extra
    };
}

// Every concept kind, every anchor kind, every event type the page plays
function everyKind() {
    return [
        event('e1', 'label_enter', { layer: 'labels', item: 2 }, 0.4, { kind: 'sync', value: 1 }, ref('label', 2)),
        event('e2', 'text_emphasis', { layer: 'board', kind: 'column', index: 1 }, 1.0, { kind: 'concept', ref: ref('side', 0) }, ref('side', 0)),
        event('e3', 'text_emphasis', { layer: 'board', kind: 'column', index: 2 }, 1.5, { kind: 'concept', ref: ref('side', 1) }, ref('side', 1)),
        event('e4', 'diagram_focus', { layer: 'visual' }, 2.0, { kind: 'sentence', value: 'look' }, ref('visual')),
        event('e5', 'diagram_focus', { layer: 'visual' }, 2.5, { kind: 'concept', ref: ref('term', 3) }, ref('term', 3)),
        event('e6', 'text_emphasis', { layer: 'board', kind: 'term', index: 0 }, 3.0, { kind: 'concept', ref: ref('term', 0) }, ref('term', 0)),
        event('e7', 'text_emphasis', { layer: 'board', kind: 'step', index: 1 }, 3.5, { kind: 'concept', ref: ref('step', 1) }, ref('step', 1)),
        event('e8', 'text_emphasis', { layer: 'board', kind: 'event', index: 0 }, 4.0, { kind: 'concept', ref: ref('event', 0) }, ref('event', 0)),
        event('e9', 'formula_emphasis', { layer: 'board', kind: 'formula' }, 4.5, { kind: 'sync', value: 2 }, ref('formula')),
        event('e10', 'text_reveal', { layer: 'board', kind: 'output' }, 5.0, { kind: 'sentence', value: 'output' }, ref('output')),
        event('e11', 'visual_highlight', { layer: 'visual' }, 5.5, { kind: 'segment', value: 1 }, ref('visual')),
        event('e12', 'camera_focus', { layer: 'camera' }, 6.0, { kind: 'fraction', value: 'planned start' }, null,
            { params: { to: { x: 0.03, y: 0.03, w: 0.94 }, duration: 1.2 } }),
        event('e13', 'presenter_point', { layer: 'presenter' }, 6.5, { kind: 'after', value: 'e10' }, null, { params: { gesture: 'point', expression: 'engaged' } }),
        event('e14', 'label_exit', { layer: 'labels', item: 0 }, 7.0, { kind: 'concept', ref: ref('label', 0) }, ref('label', 0)),
        event('e15', 'label_enter', { layer: 'labels', item: 1 }, 7.5, { kind: 'fraction', value: 0.44 }, ref('label', 1)),
        event('e16', 'text_emphasis', { layer: 'board', kind: 'point', index: 2 }, 8.0, { kind: 'concept', ref: ref('step', 2) }, ref('point', 2)),
        event('e17', 'presenter_summarize', { layer: 'presenter' }, 8.5, { kind: 'sentence', value: 'summary' }, null, { params: { gesture: 'open_hand' } }),
        event('e18', 'camera_return', { layer: 'camera' }, 9.0, { kind: 'fraction', value: 0.82 }, null, { params: { to: { x: 0, y: 0, w: 1 }, duration: 2 } })
    ];
}

// ---- syncSummary: the words ---------------------------------------------------------------------------------------------

test('every concept a plan points at is read from the scene: sides, terms, steps, events, points, labels, fixed words', () => {
    const s = C.syncSummary(scene(), plan(sync(everyKind())));
    const what = s.moments.map(m => m.what);
    assert.deepEqual(what, [
        'Mass appears as a label',                       // label 2 -> the screenplay's label 0
        'Plants is focused',                             // side 0: the table's first side (a "Feature" column is skipped)
        'Animals is focused',                            // side 1
        'the diagram is highlighted',                    // the visual
        'the diagram focuses on stomata',                // term 3: the defined term, the board's two key terms, then the screenplay's keyword
        'Photosynthesis is focused',                     // term 0: the term the definition defines
        `${LONG_STEP.slice(0, 59).trimEnd()}… is focused`, // step 1: the second top-level list item, shortened
        'Light is absorbed by the leaves & stems is focused', // event 0: the first top-level list item (entities decoded)
        'the formula is highlighted',
        "the code's output appears",
        'the picture is highlighted',
        'the camera leans in',
        'the presenter points',
        'the Force label leaves',                        // label 0 -> the screenplay's label 1 (a plain string)
        'a = acceleration appears as a label',           // label 1 -> the visual direction's annotation
        'Sugar is made is focused',                      // point 2: the third top-level list item (nested items are not counted)
        'the presenter sums up',
        'the camera steps back to the whole scene'
    ]);
    // when: about how many seconds in, from each event's estimate
    assert.deepEqual(s.moments.slice(0, 3).map(m => m.when), ['0.4 s', '1.0 s', '1.5 s']);
    assert.equal(s.moments[0].at, 0.4);
    assert.equal(s.moments[0].type, 'label_enter');
    assert.equal(s.moments[0].layer, 'labels');
    assert.equal(s.source, 'narration');
    assert.equal(s.timing, 'follows the narration audio');
    // a comparison without a table: the sides come from the title, or are the two lists
    const titled = C.syncSummary(scene({ title: 'Mitosis vs Meiosis', html: '<p>Two kinds of cell division.</p>' }),
        plan(sync([event('e1', 'text_emphasis', { layer: 'board', kind: 'side', index: 1 }, 1, { kind: 'concept' }, ref('side', 1))])));
    assert.equal(titled.moments[0].what, 'Meiosis is focused');
    const lists = C.syncSummary(scene({ title: 'Two groups', html: '<ul><li>a</li></ul><ul><li>b</li></ul>' }),
        plan(sync([event('e1', 'text_emphasis', { layer: 'board', kind: 'side', index: 0 }, 1, { kind: 'concept' }, ref('side', 0))])));
    assert.equal(lists.moments[0].what, 'the first list is focused');
});

test('what anchors each moment, in words', () => {
    const s = C.syncSummary(scene(), plan(sync(everyKind())));
    const anchor = id => s.moments[Number(id.slice(1)) - 1].anchor;
    assert.equal(anchor('e1'), 'at the [SYNC] beat');
    assert.equal(anchor('e9'), 'at [SYNC] beat 2');
    assert.equal(anchor('e2'), 'when the narration names it');
    assert.equal(anchor('e4'), 'when the narration points at it');
    assert.equal(anchor('e10'), 'when the narration mentions the result');
    assert.equal(anchor('e17'), 'when the narration sums up');
    assert.equal(anchor('e12'), 'at its planned moment');
    assert.equal(anchor('e15'), 'a share of the scene'); // a fallback: placed by a share of the scene
    assert.equal(anchor('e18'), 'a share of the scene');
    assert.equal(anchor('e13'), 'with the moment it follows');
    assert.equal(anchor('e11'), 'when narration part 2 starts');
});

test('never a word that is only in the plan, never a score or an internal metric', () => {
    const sneaky = [
        event('zzid1', 'label_enter', { layer: 'labels', item: 0, text: 'zzchip' }, 1, { kind: 'sentence', value: 'zzanchor' }, ref('label', 0)),
        event('zzid2', 'text_emphasis', { layer: 'board', kind: 'step', index: 7 }, 2, { kind: 'zzmystery', value: 'zzvalue' }, ref('step', 7)),
        event('zzid3', 'presenter_point', { layer: 'presenter' }, 3, { kind: 'after', value: 'zzid1' }, null, { params: { gesture: 'zzgesture', state: 'zzstate' } }),
        event('zzid4', 'diagram_focus', { layer: 'visual' }, 4, { kind: 'concept', ref: ref('term', 6) }, ref('term', 6)),
        event('zzid5', 'zz_unknown_type', { layer: 'zz' }, 5, { kind: 'sync', value: 1 }, ref('topic')),
        event('zzid6', 'label_enter', { layer: 'labels', item: 9 }, 6, { kind: 'concept' }, { kind: 'label', index: 9, text: 'zzlabel' })
    ];
    const p = plan(sync(sneaky, { fingerprint: 'zzfinger', attention: [{ state: 'ZZSTATE', at: at(0), estimate: 0 }],
        metrics: { anchors: 77, resolved: 66, fallbacks: 0, conflicts: 55, orphans_removed: 44, unsupported: 0 },
        ai: { status: 'ok', provider: 'zzprovider', model: 'zzmodel', key: 'zzkey', alignments: [{ target: 'zztarget', sentence: 1 }] } }));
    // the labels layer's chip names a screenplay label the scene does not have; the plan's own copy is never used
    p.layers.find(l => l.id === 'labels').items = [{ source: { kind: 'screenplay', index: 5 }, text: 'zzplanlabel' }];
    const s = C.syncSummary(scene({ composition: { labels: [] } }), p);
    const all = JSON.stringify(s);
    assert.doesNotMatch(all, /zz/i, 'nothing from the plan is printed');
    assert.deepEqual(s.moments.map(m => m.what), ['a label appears', 'a part of the board is focused', 'the presenter points',
        'the diagram is highlighted', 'a label appears']); // the unknown type is ignored, as the page ignores it
    assert.equal(s.moments[0].anchor, 'at a matching sentence of the narration');
    assert.equal(s.moments[1].anchor, 'at its planned moment');
    assert.equal(s.attention, '');
    assert.equal(s.ai, 'AI suggestion used');
    // no score or metric: none of the numbers, none of the metrics' names, no priority or tolerance
    assert.doesNotMatch(all, /\b(77|66|55|44)\b|anchors|resolved|conflicts|orphans|priority|tolerance|metrics|fingerprint|confidence/);
    for (const m of s.moments) assert.deepEqual(Object.keys(m).sort(), ['anchor', 'at', 'layer', 'type', 'what', 'when']);
});

test('the attention flow, the timing source, notes, the AI outcome and the end hold, in words', () => {
    const base = everyKind().slice(0, 3);
    const s = C.syncSummary(scene(), plan(sync(base, {
        attention: [{ state: 'PRESENTER', at: at(0), estimate: 0 }, { state: 'VISUAL', at: at(0.2), estimate: 2 }, { state: 'VISUAL', at: at(0.3), estimate: 3 },
            { state: 'FORMULA', at: at(0.4), estimate: 4 }, { state: 'PRESENTER', at: at(0.6), estimate: 6 }],
        metrics: { anchors: 3, resolved: 3, fallbacks: 2, conflicts: 0, orphans_removed: 0, unsupported: 1 }, end_hold: 0.4 })));
    assert.equal(s.attention, 'presenter → visual → formula → presenter');
    assert.deepEqual(s.notes, ['the presenter cannot act at a moment here; the visual carries the emphasis', '2 moments placed by a share of the scene']);
    assert.equal(s.endHold, 'the result stays on screen a moment longer');
    assert.equal(s.ai, null);
    const one = C.syncSummary(scene(), plan(sync(base, { metrics: { fallbacks: 1 }, fallback: 'simplified' })));
    assert.deepEqual(one.notes, ['1 moment placed by a share of the scene', 'the scene is short, so only its most important moments are kept']);
    assert.equal(one.endHold, null);
    const estimated = C.syncSummary(scene({ narration: '' }), plan(sync(base, { timing_source: 'estimate', fallback: 'estimate' })));
    assert.equal(estimated.source, 'estimate');
    assert.equal(estimated.timing, 'estimated (no narration audio)');
    // the AI outcome in the composition's own words
    const ai = (status, error, options) => C.syncSummary(scene(), plan(sync(base, { ai: { status, provider: 'fake', model: 'fake-sync-1', ...(error ? { error } : {}) } })), options).ai;
    assert.equal(ai('ok'), 'AI suggestion used');
    assert.equal(ai('repaired'), 'AI suggestion used (after one repair)');
    assert.equal(ai('invalid', 'the answer was not valid after one repair (x)'), 'AI suggestion rejected (not valid); the rules decided');
    assert.equal(ai('failed', 'boom'), 'AI model failed; the rules decided');
    assert.equal(ai('timeout'), 'AI model too slow; the rules decided');
    assert.equal(ai('skipped'), 'AI not asked for this scene (a lesson asks about 6 scenes at most); the rules decided');
    // Phase 21: the server's reason (it names the provider) only with ?visualDebug; plain words otherwise
    assert.equal(ai('unavailable', 'no Gemini API key is configured', { debug: true }), 'AI-assisted timing is not available here (no Gemini API key is configured); the rules decided');
    assert.equal(ai('unavailable', 'no Gemini API key is configured'), 'AI-assisted timing is not available here; the rules decided');
    assert.doesNotMatch(ai('unavailable', 'no Gemini API key is configured'), /Gemini|API key|\(/);
    assert.ok(['ok', 'failed', 'unavailable'].every(st => !/fake/.test(ai(st, 'x', { debug: true }) || '')), 'never the provider or model');
    assert.equal(ai('something-else'), null);
});

test('no synchronization, no summary: classic scenes, older plans, a plan the page does not understand', () => {
    assert.equal(C.syncSummary(scene(), null), null);
    assert.equal(C.syncSummary(scene(), plan(undefined)), null);
    assert.equal(C.syncSummary(scene(), plan(null)), null);
    assert.equal(C.syncSummary(scene(), plan({ ...sync([]), version: 2 })), null);
    assert.equal(C.syncSummary(scene(), plan({ version: 1, events: 'nope' })), null);
    assert.equal(C.syncSummary(null, null), null);
    // a plan with nothing timed still says how it is timed
    const empty = C.syncSummary(scene(), plan(sync([])));
    assert.deepEqual(empty.moments, []);
    assert.equal(empty.timing, 'follows the narration audio');
    // a scene without board HTML: generic words, no crash
    const bare = C.syncSummary({ title: 'Bare' }, plan(sync(everyKind())));
    assert.equal(bare.moments[1].what, 'a part of the board is focused');
});

test('the key moments: at most 8, the most important ones, still in time order', () => {
    const events = [];
    for (let i = 0; i < 12; i++) {
        events.push(event(`e${i + 1}`, i % 2 ? 'camera_focus' : 'formula_emphasis', i % 2 ? { layer: 'camera' } : { layer: 'board', kind: 'formula' }, i,
            { kind: 'sync', value: 1 }, i % 2 ? null : ref('formula'), { priority: i % 2 ? 4 : 1, params: { to: { x: 0, y: 0, w: 0.9 } } }));
    }
    const s = C.syncSummary(scene(), plan(sync(events)));
    assert.equal(s.moments.length, 12);
    assert.equal(s.keyMoments.length, 8);
    assert.deepEqual(s.keyMoments.map(m => m.at), [0, 1, 2, 3, 4, 6, 8, 10]); // the 6 formula moments first, then the 2 earliest camera moves
    const few = C.syncSummary(scene(), plan(sync(everyKind().slice(0, 5))));
    assert.deepEqual(few.keyMoments, few.moments);
});

// ---- the scene inspector -------------------------------------------------------------------------------------------------

function inspector(slides, adapterExtra = {}) {
    const doc = fakeDoc();
    const calls = [];
    const next = [];
    const session = new R.ReviewSession({ slides, api: {}, media: {}, composition: {
        enabled: () => true, facts: C.inspectorFacts, summary: C.compositionSummary, statuses: C.elementStatuses,
        review: async body => { calls.push(['review', body]); return { review: null, plan: next.shift() || slides[body.scene_index].cinematic_plan }; },
        regenerate: async i => { calls.push(['regenerate', i]); return { plan: next.shift(), review: null }; },
        options: { template: C.TEMPLATES, camera: C.CAMERA },
        direction: () => ({ label: 'Side-by-side comparison ✓', source: 'rules', facts: [['Decided', 'Automatic', 'source']], reasons: [], notes: [], ai: null,
            locked: [], current: {} }),
        syncSummary: (sc, p) => C.syncSummary(sc, p),
        ...adapterExtra } });
    const panel = new R.VisualReviewPanel({ doc, session, pickAsset: () => {}, showInLesson: () => {} });
    panel.build();
    panel.render();
    return { doc, session, panel, calls, next };
}

test('the inspector shows a read-only Synchronization block under the visual direction, with at most 8 moments', () => {
    const many = everyKind().slice(0, 12);
    const slides = [scene({ cinematic_plan: plan(sync(many, { attention: [{ state: 'PRESENTER', at: at(0), estimate: 0 }, { state: 'VISUAL', at: at(0.2), estimate: 2 }],
        metrics: { fallbacks: 1, unsupported: 1 }, end_hold: 0.4, ai: { status: 'timeout', provider: 'fake' } })) })];
    const { panel } = inspector(slides);
    const block = panel.detail.querySelector('.review-sync');
    assert.ok(block);
    const order = panel.detail.children.map(c => c.className);
    assert.ok(order.indexOf('review-direction') >= 0 && order.indexOf('review-direction') < order.indexOf('review-sync'), 'under the visual direction');
    assert.ok(order.indexOf('review-sync') < order.indexOf('review-composition-decided'), 'above the composition block');
    assert.equal(block.getAttribute('data-source'), 'narration');
    assert.equal(block.querySelector('.review-sync-auto').textContent, 'Synchronization: follows the narration audio');
    const items = block.querySelectorAll('.review-sync-moments li');
    assert.equal(items.length, 8);
    assert.equal(items[0].textContent, '0.4 s — Mass appears as a label · at the [SYNC] beat');
    assert.equal(items[1].querySelector('.review-sync-what').textContent, '1.0 s — Plants is focused');
    assert.equal(items[1].querySelector('.review-sync-anchor').textContent, ' · when the narration names it');
    assert.equal(block.querySelector('.review-sync-more').textContent, '…and 4 smaller moments.');
    assert.equal(block.querySelector('.review-sync-attention').textContent, 'Attention: presenter → visual');
    assert.deepEqual(block.querySelectorAll('.review-sync-note').map(n => n.textContent),
        ['the presenter cannot act at a moment here; the visual carries the emphasis', '1 moment placed by a share of the scene']);
    assert.equal(block.querySelector('.review-sync-hold').textContent, 'the result stays on screen a moment longer');
    assert.equal(block.querySelector('.review-sync-ai').textContent, 'AI model too slow; the rules decided');
    // read-only: no button, select or input inside the block (timeline editing comes later)
    assert.equal(block.querySelectorAll('button').length + block.querySelectorAll('select').length + block.querySelectorAll('input').length, 0);
    assert.equal(block.querySelector('[data-action]'), null);
    // the composition's own controls are still there
    for (const action of ['keep', 'change', 'regenerate', 'apply']) assert.ok(panel.detail.querySelector(`[data-action="${action}"]`), action);
});

test('the block follows the plan: a new composition or direction brings its own timing; no sync plan, no block', async () => {
    const slides = [scene({ cinematic_plan: plan(sync(everyKind().slice(0, 3))) })];
    const { panel, session, next } = inspector(slides);
    assert.equal(panel.detail.querySelectorAll('.review-sync-moments li').length, 3);
    next.push(plan(sync([event('e1', 'camera_focus', { layer: 'camera' }, 0.6, { kind: 'sentence', value: 'key moment' }, null,
        { params: { to: { x: 0, y: 0, w: 0.9 } } })], { timing_source: 'estimate' })));
    await session.regenerateComposition();
    panel.render();
    const items = panel.detail.querySelectorAll('.review-sync-moments li');
    assert.equal(items.length, 1);
    assert.equal(items[0].querySelector('.review-sync-what').textContent, '0.6 s — the camera leans in');
    assert.equal(panel.detail.querySelector('.review-sync-auto').textContent, 'Synchronization: estimated (no narration audio)');
    assert.equal(panel.detail.querySelector('.review-sync-more'), null);
    // a composition decided again without a synchronization (e.g. a quiz scene): the block goes
    next.push(plan(undefined));
    await session.decide('keep');
    panel.render();
    assert.equal(panel.detail.querySelector('.review-sync'), null);
    // nothing timed: the block says so
    const quiet = inspector([scene({ cinematic_plan: plan(sync([])) })]);
    assert.equal(quiet.panel.detail.querySelector('.review-sync-empty').textContent, 'Nothing in this scene is timed to the narration.');
    // an older page without the adapter key: no block
    const older = inspector([scene({ cinematic_plan: plan(sync(everyKind())) })], { syncSummary: undefined });
    assert.equal(older.panel.detail.querySelector('.review-sync'), null);
});

// ---- the script editor: a narration edit retimes a cinematic lesson ------------------------------------------------------

test('which scenes\' narration changed (an added or removed scene counts)', () => {
    const before = ['First light.', 'Then water.'];
    assert.deepEqual(R.narrationChanges(before, [{ narration: 'First light.' }, { narration: 'Then water.' }]), []);
    assert.deepEqual(R.narrationChanges(before, [{ narration: 'First light.', html: '<p>new board</p>' }, { narration: 'Then water.' }]), []);
    assert.deepEqual(R.narrationChanges(before, [{ narration: 'First light.' }, { narration: 'Then [SYNC] water.' }]), [1]);
    assert.deepEqual(R.narrationChanges(before, [{ narration: 'First light.' }, { narration: 'Then water.' }, { narration: 'New.' }]), [2]);
    assert.deepEqual(R.narrationChanges(before, [{ narration: 'First light.' }]), [1]);
    assert.deepEqual(R.narrationChanges(['', 'x'], [{}, { narration: 'x' }]), []); // no narration = an empty one
    assert.deepEqual(R.narrationChanges([{ narration: 'a' }], [{ narration: 'b' }]), [0]); // scenes work as well as texts
    // the page's snapshot carries the board: a board edit counts (what the moments point at moved)
    assert.deepEqual(R.narrationChanges([{ narration: 'a', html: '<p>x</p>' }], [{ narration: 'a', html: '<p>y</p>' }]), [0]);
    assert.deepEqual(R.narrationChanges([{ narration: 'a', html: '<p>x</p>' }], [{ narration: 'a', html: '<p>x</p>' }]), []);
});

test('a narration edit plans a cinematic lesson again, then draws the scene; classic lessons and other edits: nothing', async () => {
    const calls = [];
    let release;
    const plan = () => { calls.push('plan'); return new Promise(r => { release = r; }); };
    const render = () => calls.push('render');
    const before = ['First light.', 'Then water.'];
    const edited = [{ narration: 'First light.' }, { narration: 'Then water is split. [SYNC]' }];
    // classic: nothing is asked even though the narration changed
    let r = await R.retimeAfterNarrationEdit({ cinematic: false, before, after: edited, plan, render });
    assert.deepEqual(r, { replanned: false, changed: [1] });
    assert.deepEqual(calls, []);
    // cinematic, but the narration is the same (a board edit): nothing is asked
    r = await R.retimeAfterNarrationEdit({ cinematic: true, before, after: [{ narration: 'First light.', html: '<p>x</p>' }, { narration: 'Then water.' }], plan, render });
    assert.equal(r.replanned, false);
    assert.deepEqual(calls, []);
    // cinematic and the narration changed: planned once, and the scene is drawn again only once the plans are back
    const pending = R.retimeAfterNarrationEdit({ cinematic: true, before, after: edited, plan, render });
    await Promise.resolve();
    assert.deepEqual(calls, ['plan']);
    release();
    r = await pending;
    assert.deepEqual(r, { replanned: true, changed: [1] });
    assert.deepEqual(calls, ['plan', 'render']);
    // a planner failure reaches the caller (index.html logs it); nothing is drawn twice
    calls.length = 0;
    await assert.rejects(R.retimeAfterNarrationEdit({ cinematic: true, before, after: edited, plan: async () => { calls.push('plan'); throw new Error('offline'); }, render }));
    assert.deepEqual(calls, ['plan']);
});

test('the page wires the synchronization block and the narration retiming (index.html)', () => {
    const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
    // (Phase 21: the technical moments only in the debug view)
    assert.match(html, /syncSummary: \(scene, plan\) => AadhiCinematic\.syncSummary\(scene, plan, \{ debug: visualDebug \}\)/);
    const save = html.slice(html.indexOf("scriptEditorSave.addEventListener('click'"), html.indexOf("const scriptEditorRegenManim"));
    assert.ok(save.length > 0);
    assert.match(save, /const narrationsBefore = slides\.map\(/);
    assert.match(save, /AadhiReview\.retimeAfterNarrationEdit\(\{/);
    assert.match(save, /const cinematicLesson = !AadhiCinematic\.isClassic\(cinematicSettings\.settings\)/);
    assert.match(save, /plan: planLessonCinematic/);
    assert.match(save, /render: showAndSave/);
    // drawn and saved once, after the new timing (the server copy gets the new plans); otherwise at once
    assert.match(save, /const showAndSave = \(\) => \{ renderSlide\(currentSlide\); saveProjectBackup\(\); saveToServer\(\); \}/);
    assert.equal((save.match(/renderSlide\(currentSlide\)/g) || []).length, 1, 'the scene is drawn in one place only');
    // the narration is read before the edit is applied, the retiming runs after it
    assert.ok(save.indexOf('narrationsBefore = ') < save.indexOf("editorMode === 'narration'"));
    assert.ok(save.indexOf("slides[currentSlide].narration = rawText") < save.indexOf('retimeAfterNarrationEdit'));
});
