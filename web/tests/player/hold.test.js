// @ts-check
/**
 * The teacher's minimum scene duration (TimedScene.hold_seconds, aadhi/compose/timeline.py): the hold is added
 * after the content, so nothing inside the scene moves. Terminal lines and the quiz teaser (the only timings
 * spread over the scene's length) keep their times, in the live schedule, in the panels and in the render-mode
 * state times; during the hold Aadhi idles and no caption shows.
 */
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import { contentEnd, sceneStateAt, stateTimes } from '../../js/player/schedule.js';
import { panelStateTimes } from '../../js/player/panels/timing.js';
import { sceneDuration } from '../../js/player/panels/util.js';
import { loadTimeline, sceneById } from './_fixture.js';

const timeline = loadTimeline();
const ohm = sceneById(timeline, 's-ohm'); // board scene with a terminal side panel

/**
 * @param {any} scene
 * @param {number} hold
 */
function held(scene, hold) {
  return { ...scene, duration: scene.duration + hold, hold_seconds: hold };
}

/** @param {any} side */
function teaserScene(side) {
  return {
    scene_id: 'teaser', index: 0, type: 'content', start: 0, duration: 20, beats: [], board: [],
    side_panel: { show_at: 2, panel: { kind: 'quiz', quiz: { question: 'Which?', options: ['a', 'b'], correct_index: 0 } } },
    ...side,
  };
}

describe('minimum duration hold', () => {
  test('contentEnd is the duration without the hold', () => {
    assert.equal(contentEnd({ duration: 12 }), 12);
    assert.equal(contentEnd({ duration: 12, hold_seconds: 0 }), 12);
    assert.equal(contentEnd({ duration: 17, hold_seconds: 5 }), 12);
    assert.equal(contentEnd({ duration: 3, hold_seconds: 9 }), 0);
  });

  test('terminal lines keep their times; the hold only extends the last state', () => {
    const long = held(ohm, 30);
    for (const t of stateTimes(ohm)) {
      const a = sceneStateAt(ohm, t);
      const b = sceneStateAt(long, t);
      assert.deepEqual([...b.terminalLines], [...a.terminalLines], `t=${t}`);
      assert.equal(b.caption?.text ?? null, a.caption?.text ?? null);
    }
    const allLines = sceneStateAt(ohm, ohm.duration - 0.001).terminalLines;
    assert.deepEqual([...sceneStateAt(long, ohm.duration - 0.001).terminalLines], [...allLines]);
    assert.deepEqual([...sceneStateAt(long, long.duration - 0.001).terminalLines], [...allLines]);
  });

  test('during the hold Aadhi idles and no caption shows', () => {
    const long = held(ohm, 30);
    for (const t of [ohm.duration + 0.5, ohm.duration + 15, long.duration - 0.01]) {
      const s = sceneStateAt(long, t);
      assert.equal(s.phase, 'tail');
      assert.equal(s.caption, null);
      assert.equal(s.mascotState, 'idle');
    }
  });

  test('panel state times (render mode) ignore the hold', () => {
    assert.deepEqual(panelStateTimes(held(ohm, 30)), panelStateTimes(ohm));
    assert.deepEqual(panelStateTimes(teaserScene({ duration: 50, hold_seconds: 30 })), panelStateTimes(teaserScene({})));
  });

  test("the panels' scene duration ignores the hold", () => {
    const scenes = [{ duration: 40, hold_seconds: 25 }, { duration: 40 }];
    assert.equal(sceneDuration(/** @type {any} */ ({ timeline: { scenes }, sceneIndex: 0 }), 2), 15);
    assert.equal(sceneDuration(/** @type {any} */ ({ timeline: { scenes }, sceneIndex: 1 }), 2), 40);
    assert.equal(sceneDuration(/** @type {any} */ ({ timeline: { scenes: [{ duration: 10, hold_seconds: 9 }] }, sceneIndex: 0 }), 3), 3);
  });
});
