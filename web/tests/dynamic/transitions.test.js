import { test } from 'node:test';
import assert from 'node:assert/strict';

import { CROSSFADE_DEFAULT, CROSSFADE_MAX, CROSSFADE_MIN, paintTransition, transitionAt, transitionOf } from '../../js/dynamic/transitions.js';
import { Painter } from '../../js/dynamic/core.js';
import { dynamicTimeline } from './_fixture.js';

/** @param {any} transition @param {number} [duration] */
const sceneWith = (transition, duration = 5) => ({ scene_id: 'x', duration, look: { transition_in: transition } });

test('transitionOf normalises the look: cut by default, crossfades clamped to 300-800 ms and the scene', () => {
  assert.deepEqual(transitionOf(/** @type {any} */ ({ scene_id: 'x', duration: 5 })), { kind: 'cut', seconds: 0 });
  assert.deepEqual(transitionOf(/** @type {any} */ (sceneWith({ kind: 'whip', seconds: 0.35 }))), { kind: 'cut', seconds: 0 });
  assert.deepEqual(transitionOf(/** @type {any} */ (sceneWith({ kind: 'crossfade' }))), { kind: 'crossfade', seconds: CROSSFADE_DEFAULT });
  assert.equal(transitionOf(/** @type {any} */ (sceneWith({ kind: 'crossfade', seconds: 0.1 }))).seconds, CROSSFADE_MIN);
  assert.equal(transitionOf(/** @type {any} */ (sceneWith({ kind: 'crossfade', seconds: 3 }))).seconds, CROSSFADE_MAX);
  assert.equal(transitionOf(/** @type {any} */ (sceneWith({ kind: 'crossfade', seconds: 0.5 }, 0.2))).seconds, 0.2);
});

test('transitionAt: a crossfade runs inside the incoming scene, over the previous one', () => {
  const tl = dynamicTimeline();
  assert.equal(transitionAt(tl, 0, 0), null, 'the first scene has nothing to fade from');
  assert.equal(transitionAt(tl, 1, 0.45), null);
  const mid = transitionAt(tl, 1, 0.225);
  assert.deepEqual(mid, { kind: 'crossfade', p: 0.5, outgoing: 0 });
  assert.equal(transitionAt(tl, 1, 0).p, 0);
  assert.equal(transitionAt(tl, 3, 0.1), null, 'scene 3 is entered by a cut');
});

test('paintTransition puts the incoming scene above and fades it in', () => {
  /** @param {string} name */
  const el = (name) => {
    const props = new Map();
    return { name, props, style: { setProperty: (k, v) => props.set(k, v), removeProperty: (k) => props.delete(k) } };
  };
  const inc = el('in');
  const out = el('out');
  const p = new Painter();
  p.begin();
  paintTransition(p, /** @type {any} */ (inc), /** @type {any} */ (out), { kind: 'crossfade', p: 0 });
  assert.equal(inc.props.get('opacity'), '0.0000');
  assert.equal(inc.props.get('z-index'), '2');
  assert.equal(out.props.get('z-index'), '1');
  paintTransition(p, /** @type {any} */ (inc), /** @type {any} */ (out), { kind: 'crossfade', p: 0.5 });
  assert.equal(inc.props.get('opacity'), '0.5000');
  paintTransition(p, /** @type {any} */ (inc), null, null);
  assert.equal(inc.props.has('opacity'), false, 'no transition: fully opaque');
});
