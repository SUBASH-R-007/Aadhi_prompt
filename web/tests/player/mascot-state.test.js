// @ts-check
/**
 * Aadhi's behaviour states and cue bubble (mascot-state.js) as part of the pure schedule, plus the
 * presenter vocabulary with its capability fallback (presenter-vocab.js).
 */
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import { renderStateList, sceneStateAt, stateKey, stateTimes } from '../../js/player/schedule.js';
import {
  CUE_ANCHORS, CUE_SIZE, THINK_DELAY, THINK_MIN_SILENCE, cueAnchor, cueRect, sceneRole,
} from '../../js/player/mascot-state.js';
import {
  BEHAVIOUR_FALLBACK, BEHAVIOURS, CLIP_CAPABILITIES, EXPRESSION_FALLBACK, EXPRESSIONS, GESTURE_FALLBACK, GESTURES,
  actingFor, mapVocab,
} from '../../js/player/presenter-vocab.js';
import { computeZones, intersects, STAGE_HEIGHT, STAGE_WIDTH } from '../../js/player/layout.js';
import { loadTimeline, sceneById } from './_fixture.js';

const timeline = loadTimeline();
const ohm = sceneById(timeline, 's-ohm'); // left, every beat reveals an item, short pauses
const example = sceneById(timeline, 's-example'); // right, b2/b4 have 2 s pauses
const title = sceneById(timeline, 's-title'); // center, no board item
const quiz = sceneById(timeline, 's-quiz'); // popup_bottom_right
const chapter = sceneById(timeline, 's-chapter'); // silent

/**
 * A copy of a scene with layout changes (fresh object: schedule plans are cached per scene object).
 * @param {any} scene
 * @param {Record<string, unknown>} layout
 */
const withLayout = (scene, layout) => ({ ...structuredClone(scene), layout: { ...(scene.layout || {}), ...layout } });

/** @param {any} scene @param {number} t */
const at = (scene, t) => {
  const s = sceneStateAt(scene, t);
  return [s.mascotState, s.mascotCue];
};

describe('presenter vocabulary', () => {
  test('every fallback stays inside its vocabulary', () => {
    for (const [table, vocab] of /** @type {const} */ ([[BEHAVIOUR_FALLBACK, BEHAVIOURS], [EXPRESSION_FALLBACK, EXPRESSIONS], [GESTURE_FALLBACK, GESTURES]])) {
      for (const [from, to] of Object.entries(table)) {
        assert.ok(vocab.includes(from) && vocab.includes(to), `${from} -> ${to}`);
      }
    }
    for (const b of CLIP_CAPABILITIES.behaviours) assert.ok(BEHAVIOURS.includes(b));
  });

  test('the nearest supported value is chosen and the substitution is noted', () => {
    assert.deepEqual(mapVocab('surprised', ['neutral', 'friendly'], EXPRESSION_FALLBACK, 'neutral'),
      { value: 'friendly', note: 'surprised shown as friendly' });
    assert.deepEqual(mapVocab('counting', ['none', 'open_hand'], GESTURE_FALLBACK, 'none'),
      { value: 'open_hand', note: 'counting shown as open_hand' });
    assert.deepEqual(mapVocab('happy', ['happy'], EXPRESSION_FALLBACK, 'neutral'), { value: 'happy', note: null });
    assert.deepEqual(mapVocab(null, ['neutral', 'happy'], EXPRESSION_FALLBACK, 'neutral'), { value: 'neutral', note: null });
    // an unknown value and a chain that ends outside the supported set use the default, then the first value
    assert.deepEqual(mapVocab('dancing', ['idle', 'talking'], BEHAVIOUR_FALLBACK, 'idle'), { value: 'idle', note: 'dancing shown as idle' });
    assert.equal(mapVocab('welcome', ['point'], GESTURE_FALLBACK, 'none').value, 'point');
  });

  test('cycles in a fallback table terminate', () => {
    const cyclic = { a: 'b', b: 'c', c: 'a' };
    assert.deepEqual(mapVocab('a', ['z'], cyclic, 'z'), { value: 'z', note: 'a shown as z' });
    assert.deepEqual(mapVocab('a', ['y', 'x'], cyclic, 'z'), { value: 'y', note: 'a shown as y' });
  });

  test('nothing is faked: a vocabulary the presenter has nothing of maps to null', () => {
    assert.deepEqual(mapVocab('point', [], GESTURE_FALLBACK, 'none'), { value: null, note: 'point not shown' });
    assert.deepEqual(mapVocab(null, null, GESTURE_FALLBACK, 'none'), { value: null, note: null });
  });

  test('the branding clips show behaviour states only', () => {
    const acting = actingFor({ behaviour: 'welcoming', expression: 'happy', gesture: 'welcome' });
    assert.equal(acting.behaviour, 'talking');
    assert.equal(acting.expression, null);
    assert.equal(acting.gesture, null);
    assert.deepEqual(acting.notes, ['welcoming shown as talking', 'happy not shown', 'welcome not shown']);
    assert.equal(actingFor({ behaviour: 'concluding' }).behaviour, 'explaining');
    assert.deepEqual(actingFor({ behaviour: 'thinking' }), { behaviour: 'thinking', expression: null, gesture: null, notes: [] });
    // a richer presenter (e.g. a future rig) keeps what it can show
    const rig = { behaviours: BEHAVIOURS, expressions: ['neutral', 'friendly'], gestures: ['none', 'open_hand', 'point'] };
    assert.deepEqual(actingFor({ behaviour: 'welcoming', expression: 'encouraging', gesture: 'counting' }, rig),
      { behaviour: 'welcoming', expression: 'friendly', gesture: 'open_hand', notes: ['encouraging shown as friendly', 'counting shown as open_hand'] });
  });
});

describe('director rules: behaviour states from the schedule', () => {
  test('scene roles come from the typed scene type', () => {
    assert.equal(sceneRole(title), 'intro');
    assert.equal(sceneRole(quiz), 'quiz');
    assert.equal(sceneRole(ohm), 'explanation');
    for (const type of ['summary', 'key_takeaway', 'recap']) assert.equal(sceneRole({ ...ohm, type }), 'summary');
  });

  test('board scene: idle lead, explaining while a beat reveals its item, idle tail', () => {
    assert.deepEqual(at(ohm, 0), ['idle', null]);
    assert.deepEqual(at(ohm, 0.5), ['explaining', null]);
    assert.deepEqual(at(ohm, 3.05), ['explaining', null], 'the inter-beat gap keeps the speaking state');
    assert.deepEqual(at(ohm, 6.5), ['explaining', null], 'a 1.15 s pause is shorter than THINK_MIN_SILENCE');
    assert.deepEqual(at(ohm, 18), ['talking', null], 'b8 reveals nothing');
    assert.deepEqual(at(ohm, 19.5), ['idle', null]);
    assert.deepEqual(at(chapter, 1), ['idle', null], 'a silent scene stays idle');
  });

  test('a long pause turns into thinking THINK_DELAY after the speech, with the dots bubble', () => {
    const b2 = /** @type {any} */ (example.beats).find((/** @type {any} */ b) => b.beat_id === 's-example-b2');
    assert.ok(6.5 - b2.speech_end >= THINK_MIN_SILENCE);
    const from = b2.speech_end + THINK_DELAY;
    assert.deepEqual(at(example, b2.speech_end + 0.01), ['explaining', null]);
    assert.deepEqual(at(example, from - 1e-6), ['explaining', null]);
    assert.deepEqual(at(example, from), ['thinking', 'dots']);
    assert.deepEqual(at(example, 6.49), ['thinking', 'dots']);
    assert.deepEqual(at(example, 6.5), ['explaining', null], 'the next beat speaks again');
    assert.ok(stateTimes(example).includes(from), 'the render screenshots the bubble appearing');
    assert.ok(renderStateList(example).some((s) => s.t === from));
  });

  test('a title scene welcomes, shown by the clips as talking', () => {
    assert.deepEqual(at(title, 1), ['talking', null]);
  });

  test('quiz: question while asking, listening in the countdown, success at the reveal', () => {
    const q = /** @type {any} */ (quiz.quiz);
    const left = withLayout(quiz, { mascot_position: 'left' });
    assert.deepEqual(at(left, 0.2), ['idle', null]);
    assert.deepEqual(at(left, 1), ['question', 'question']);
    assert.deepEqual(at(left, q.countdown_start), ['listening', 'dots']);
    assert.deepEqual(at(left, q.reveal_start - 0.01), ['listening', 'dots']);
    assert.deepEqual(at(left, q.reveal_start), ['success', 'success']);
    assert.deepEqual(at(left, 11.7), ['idle', null], 'tail after the reveal beats');
    // the fixture's quiz uses the popup close-up: the same states, no bubble
    assert.deepEqual(at(quiz, 1), ['question', null]);
    assert.deepEqual(at(quiz, q.reveal_start), ['success', null]);
    // the countdown keeps one state per second (the bubble adds none)
    const list = renderStateList(left);
    assert.equal(list.filter((s) => s.t >= q.countdown_start && s.t < q.reveal_start).length, q.countdown_seconds);
    // no blink in the INTER_BEAT_GAP between the last question beat and the countdown
    const lastAsk = left.beats[0].end;
    assert.ok(lastAsk < q.countdown_start, 'the fixture has the gap');
    assert.deepEqual(at(left, lastAsk), ['question', 'question']);
    assert.deepEqual(at(left, q.countdown_start - 0.05), ['question', 'question'], 'no blink before the countdown');
    for (let t = left.beats[0].start; t < q.reveal_start; t += 0.05) {
      assert.notEqual(at(left, t)[1], null, `bubble shown at ${t.toFixed(2)}`);
    }
    assert.ok(!list.some((s) => Math.abs(s.t - lastAsk) < 1e-9), 'the gap merges with the "?" state (no extra screenshot)');
    // a quiz without reveal beats reaches 'tail' in that gap: still asking
    const noReveal = { ...structuredClone(left), beats: structuredClone(left.beats).filter((/** @type {any} */ b) => b.phase !== 'reveal') };
    assert.equal(sceneStateAt(noReveal, lastAsk + 0.05).phase, 'tail');
    assert.deepEqual(at(noReveal, lastAsk + 0.05), ['question', 'question']);
    assert.deepEqual(at(noReveal, 0.2), ['idle', null], 'lead stays idle');
  });

  test('Layout.mascot_cues === false hides the bubble but keeps the state', () => {
    const off = withLayout(example, { mascot_cues: false });
    assert.deepEqual(at(off, 5.0), ['thinking', null]);
    const thinkStarts = [4.35 + THINK_DELAY, 10.35 + THINK_DELAY]; // b2 and b4 pause for 2 s
    const on = renderStateList(example).map((s) => s.t);
    const offTimes = renderStateList(off).map((s) => s.t);
    for (const t of thinkStarts) {
      assert.ok(on.some((x) => Math.abs(x - t) < 1e-9), `bubble state at ${t}`);
      assert.ok(!offTimes.some((x) => Math.abs(x - t) < 1e-9), `no extra screenshot at ${t} without the bubble`);
    }
    assert.ok(offTimes.every((t) => on.includes(t)));
  });

  test('keys of states without a bubble are unchanged; the bubble changes the key', () => {
    const s = sceneStateAt(ohm, 0.5);
    const legacy = { ...s };
    delete legacy.mascotState;
    delete legacy.mascotCue;
    assert.equal(stateKey(ohm, s), stateKey(ohm, legacy));
    const thinking = sceneStateAt(example, 5.0);
    assert.notEqual(stateKey(example, thinking), stateKey(example, { ...thinking, mascotCue: null }));
  });

  test('seeking gives the same state as continuous play (pure in t)', () => {
    for (const scene of [ohm, example, title, withLayout(quiz, { mascot_position: 'right' })]) {
      const forward = [];
      for (let k = 0; k * 0.05 < scene.duration; k++) forward.push(at(scene, k * 0.05));
      const fresh = structuredClone(scene); // new plan, visited backwards
      for (let k = forward.length - 1; k >= 0; k--) assert.deepEqual(at(fresh, k * 0.05), forward[k], `${scene.scene_id}@${k * 0.05}`);
    }
  });
});

describe('cue bubble geometry', () => {
  test('anchors exist beside the head for left/right/center only', () => {
    assert.deepEqual(cueAnchor('left'), CUE_ANCHORS.left);
    assert.deepEqual(cueAnchor('right'), CUE_ANCHORS.right);
    assert.deepEqual(cueAnchor('center'), CUE_ANCHORS.center);
    assert.deepEqual(cueAnchor(undefined), CUE_ANCHORS.left, 'the default position is left');
    for (const p of ['popup_bottom_left', 'popup_bottom_right', 'hidden']) assert.equal(cueAnchor(p), null, p);
  });

  test('the bubble never covers the board, side panel, title band or captions, and stays on stage', () => {
    for (const position of ['left', 'right', 'center']) {
      const r = /** @type {{x: number, y: number, w: number, h: number}} */ (cueRect(position));
      assert.deepEqual([r.w, r.h], [CUE_SIZE.w, CUE_SIZE.h]);
      assert.ok(r.x >= 0 && r.y >= 0 && r.x + r.w <= STAGE_WIDTH && r.y + r.h <= STAGE_HEIGHT, position);
      for (const sidePanel of [false, true]) {
        const z = computeZones(position, { sidePanel });
        for (const zone of [z.board, z.title, z.media, z.side, z.captions]) {
          if (zone) assert.equal(intersects(r, zone), false, `${position} (side panel ${sidePanel}) overlaps ${JSON.stringify(zone)}`);
        }
      }
      assert.ok(r.y + r.h <= 900, 'above the captions band');
    }
  });
});
