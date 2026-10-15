import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  FREEZE_EASE_SECONDS,
  INITIAL_DELAY,
  INITIAL_STAGGER,
  REVEAL_LEAD,
  backgroundTime,
  captionAt,
  chapterTitleAt,
  dynamicStateAt,
  freezeProgress,
  frozenIntervals,
  itemTimings,
} from '../../js/dynamic/state.js';
import { dynamicTimeline } from './_fixture.js';

test('items enter 120 ms before the beat that reveals them; others at the start, staggered', () => {
  const scene = dynamicTimeline().scenes[0];
  const t = itemTimings(scene);
  assert.deepEqual(t.map((x) => x.id), ['b1', 'b2', 'b3', 'b4', 'b5']);
  assert.equal(t[0].enter, INITIAL_DELAY);
  assert.ok(Math.abs(t[1].enter - (0.5 - REVEAL_LEAD)) < 1e-9);
  assert.ok(Math.abs(t[2].enter - (2.2 - REVEAL_LEAD)) < 1e-9);
  assert.equal(t[3].fill, 6.2);
  assert.equal(t[4].fill, null);
  assert.equal(itemTimings(scene), t, 'cached per scene object');
});

test('items without reveals stagger in board order', () => {
  const scene = { scene_id: 'x', board: [{ id: 'a', kind: 'bullet' }, { id: 'b', kind: 'bullet' }], beats: [] };
  const t = itemTimings(/** @type {any} */ (scene));
  assert.deepEqual(t.map((x) => x.enter), [INITIAL_DELAY, INITIAL_DELAY + INITIAL_STAGGER]);
});

test('reveal and fill events (events.py) win over the beats', () => {
  const scene = structuredClone(dynamicTimeline().scenes[0]);
  scene.events = [
    { t: 1.5, kind: 'reveal', target: 'b3' },
    { t: 5.0, kind: 'fill', target: 'b4' },
    { t: 9.0, kind: 'cue_term', target: 'b2' },
  ];
  const t = itemTimings(scene);
  assert.equal(t.find((x) => x.id === 'b3').enter, 1.5);
  assert.equal(t.find((x) => x.id === 'b4').fill, 5.0);
  const st = dynamicStateAt(scene, 1.6);
  assert.ok(st.visibleItemIds.has('b3'), 'visible from the event, before its beat');
  assert.ok(!dynamicStateAt(scene, 1.4).visibleItemIds.has('b3'));
  assert.ok(dynamicStateAt(scene, 5.1).filledItemIds.has('b4'));
});

test('without events the state is the classic schedule', () => {
  const scene = dynamicTimeline().scenes[0];
  const st = dynamicStateAt(scene, 2.3);
  assert.ok(st.visibleItemIds.has('b3'));
  assert.ok(!st.visibleItemIds.has('b4'));
  assert.equal(st.caption, 'Current is the rate of flow.');
});

test('the background clock stops during holds and never jumps', () => {
  const tl = dynamicTimeline();
  assert.deepEqual(frozenIntervals(tl), [{ start: 14, end: 26 }]);
  assert.equal(backgroundTime(tl, 10), 10);
  assert.equal(backgroundTime(tl, 14), 14);
  assert.equal(backgroundTime(tl, 20), 14);
  assert.equal(backgroundTime(tl, 26), 14);
  assert.equal(backgroundTime(tl, 30), 18);
  // continuous: small steps give small changes
  for (let t = 0; t < 40; t += 0.1) assert.ok(backgroundTime(tl, t + 0.1) - backgroundTime(tl, t) <= 0.1 + 1e-9);
});

test('the hold eases in over 600 ms from frozen_at', () => {
  const tl = dynamicTimeline();
  const quiz = tl.scenes[2];
  assert.equal(freezeProgress(quiz, 0), 0);
  assert.ok(Math.abs(freezeProgress(quiz, FREEZE_EASE_SECONDS / 2) - 0.5) < 1e-9);
  assert.equal(freezeProgress(quiz, 5), 1);
  assert.equal(freezeProgress(tl.scenes[0], 5), 0);
});

test('captions: kinetic chunks (blank ones show nothing, key words), else the beat phrase captions', () => {
  const tl = dynamicTimeline();
  const sim = tl.scenes[3];
  const c = captionAt(sim, 0.6);
  assert.deepEqual(c.lines, ['Watch the current']);
  assert.ok(c.keys.has('current'));
  assert.equal(captionAt(sim, 2.0), null, 'a blank chunk: the on-screen text is the caption (R-31)');
  assert.equal(captionAt(sim, 4.0), null);
  const board = tl.scenes[0];
  assert.deepEqual(captionAt(board, 1.0).lines, ['Charge flows like water.']);
  assert.equal(captionAt(board, 9.0), null);
});

test('chapterTitleAt: the last chapter (in the timeline order) starting at or before the scene, 1 ms tolerance', () => {
  // board_generic's kicker rule since B1, now the stage's (B2 plan §1.4); aadhi/compose/motion/chunks.py
  // _kicker_chapters hashes the same title into the chunk key (tests/compose/motion/test_chunks.py)
  const tl = dynamicTimeline();
  assert.deepEqual(tl.scenes.map((/** @type {any} */ s) => chapterTitleAt(tl, s.start)), ['Basics', 'The law', 'The law', 'The law', 'The law']);
  assert.equal(chapterTitleAt(tl, 9.9995), 'The law', 'a chapter starting within 1 ms after the scene');
  assert.equal(chapterTitleAt(tl, 9.99), 'Basics');
  assert.equal(chapterTitleAt(tl, -1), '', 'before every chapter');
  assert.equal(chapterTitleAt(/** @type {any} */ ({ ...tl, chapters: undefined }), 5), '');
  const unordered = /** @type {any} */ ({ chapters: [{ start: 10, title: 'B' }, { start: 0, title: 'A' }, { start: 3 }] });
  assert.equal(chapterTitleAt(unordered, 12), '', 'the last match in order wins (an untitled one: no title)');
  assert.equal(chapterTitleAt(unordered, 2), 'A');
});

// --- B2c: item states (plan §1.5) -------------------------------------------------------------------------------------

test('item states: the closed list, each look from the roles (current: 700 + ink2 border; done: ok; struck: warn line)', async () => {
  const { ITEM_STATES, itemStateStyle } = await import('../../js/dynamic/state.js');
  const { makeRoles } = await import('../../js/dynamic/core.js');
  assert.deepEqual([...ITEM_STATES], ['current', 'past', 'done', 'struck']);
  assert.ok(Object.isFrozen(ITEM_STATES));
  const roles = makeRoles(/** @type {any} */ (dynamicTimeline().presentation), null);
  const c = (/** @type {string} */ r) => roles.color(r);
  assert.deepEqual(itemStateStyle('current', roles), { weight: 700, ink: c('ink'), border: c('ink2'), check: null, strike: null });
  assert.deepEqual(itemStateStyle('past', roles), { weight: null, ink: c('ink2'), border: null, check: null, strike: null });
  assert.deepEqual(itemStateStyle('done', roles), { weight: null, ink: c('ink'), border: c('ok'), check: c('ok'), strike: null });
  assert.deepEqual(itemStateStyle('struck', roles), { weight: null, ink: c('ink2'), border: null, check: null, strike: c('warn') });
  for (const state of ITEM_STATES) {
    const s = itemStateStyle(state, roles);
    for (const v of [s.ink, s.border, s.check, s.strike]) {
      assert.ok(v === null || ![c('mark'), c('focus'), c('struct.accent')].includes(v) || v === c('ink'), `${state}: no cue or structure colour`);
    }
  }
  assert.throws(() => itemStateStyle('glow', roles), RangeError);
});

test('itemStatesAt: the item entered last is current from its entrance; earlier ones past from the next entrance', async () => {
  const { itemStatesAt } = await import('../../js/dynamic/state.js');
  const scene = dynamicTimeline().scenes[0]; // b1 at 0.35 (no beat), b2 0.38, b3 2.08, b4 4.08, b5 6.08
  const at = (/** @type {number} */ t, /** @type {string[] | null} */ ids = null) => {
    const r = itemStatesAt(scene, t, ids);
    return { current: r.current, states: Object.fromEntries([...r.states].map(([k, v]) => [k, [v.state, Math.round(v.since * 100) / 100]])) };
  };
  assert.deepEqual(at(0.1), { current: null, states: {} });
  assert.deepEqual(at(3), { current: 'b3', states: { b1: ['past', 0.38], b2: ['past', 2.08], b3: ['current', 2.08] } });
  assert.deepEqual(at(3, ['b2', 'b3', 'b4']), { current: 'b3', states: { b2: ['past', 2.08], b3: ['current', 2.08] } });
  assert.deepEqual(at(9).current, 'b5');
  // a reveal event moves an entrance, and with it the states
  const moved = structuredClone(scene);
  moved.events = [{ t: 2.5, kind: 'reveal', target: 'b2' }];
  const r = itemStatesAt(moved, 3);
  assert.equal(r.current, 'b2');
  assert.equal(/** @type {any} */ (r.states.get('b3')).state, 'past');
});
