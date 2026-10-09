// @ts-check
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import {
  sceneStateAt,
  stateTimes,
  stateKey,
  renderStateList,
  selectCue,
  sceneIndexAt,
  introCardAt,
  chapterIndexAt,
  conceptStateAt,
  terminalOutputLines,
  terminalLineTimes,
  TERMINAL_MAX_LINES,
  planScene,
} from '../../js/player/schedule.js';
import { loadTimeline, sceneById, beat } from './_fixture.js';

const timeline = loadTimeline();
const ohm = sceneById(timeline, 's-ohm');
const example = sceneById(timeline, 's-example');
const quiz = sceneById(timeline, 's-quiz');
const chapter = sceneById(timeline, 's-chapter');

/** @param {Set<string>} s */
const sorted = (s) => [...s].sort();

describe('sceneStateAt: phases', () => {
  test('lead before the first beat: only unrevealed-by-beat items are visible', () => {
    const s = sceneStateAt(ohm, 0);
    assert.equal(s.phase, 'lead');
    assert.equal(s.beatIndex, -1);
    assert.deepEqual(sorted(s.visibleItemIds), ['i-bullet', 'i-head', 'i-para']);
    assert.equal(s.activeItemId, null);
    assert.equal(s.caption, null);
    assert.equal(s.countdownRemaining, null);
    assert.equal(s.quizRevealed, false);
    assert.equal(s.panelVisible, false);
    assert.equal(s.terminalLines.length, 0);
  });

  test('beat start reveals its item exactly at start (inclusive)', () => {
    assert.equal(sceneStateAt(ohm, 0.4999).visibleItemIds.has('i-def'), false);
    const s = sceneStateAt(ohm, 0.5);
    assert.equal(s.phase, 'beat');
    assert.equal(s.beatIndex, 0);
    assert.ok(s.visibleItemIds.has('i-def'));
    assert.equal(s.activeItemId, 'i-def');
    assert.equal(s.caption, 'Resistance opposes the flow of current.');
  });

  test('inter-beat gap is a pause that keeps the previous beat current', () => {
    const s = sceneStateAt(ohm, 3.05); // b1 ends 3.0, b2 starts 3.15
    assert.equal(s.phase, 'pause');
    assert.equal(s.beatIndex, 0);
    assert.equal(s.activeItemId, 'i-def');
    assert.equal(s.caption, null);
  });

  test('pause_after keeps the beat current and its item active', () => {
    const s = sceneStateAt(ohm, 6.5); // b2 speech_end 6.0, end 7.0
    assert.equal(s.phase, 'pause');
    assert.equal(s.beatIndex, 1);
    assert.equal(s.activeItemId, 'i-formula');
  });

  test('tail after the last beat: nothing active, everything visible', () => {
    const s = sceneStateAt(ohm, 19.5);
    assert.equal(s.phase, 'tail');
    assert.equal(s.beatIndex, -1);
    assert.equal(s.activeItemId, null);
    assert.equal(s.visibleItemIds.size, ohm.board?.length);
  });

  test('silent scene (no beats) is tail from the start', () => {
    const s = sceneStateAt(chapter, 0);
    assert.equal(s.phase, 'tail');
    assert.equal(s.beatIndex, -1);
    assert.equal(s.visibleItemIds.size, 0);
  });

  test('silent scene with an audio offset has a lead first', () => {
    const scene = { ...chapter, audio_offset: 1 };
    assert.equal(sceneStateAt(scene, 0.5).phase, 'lead');
    assert.equal(sceneStateAt(scene, 1).phase, 'tail');
  });
});

describe('sceneStateAt: highlights, fills, active item', () => {
  test('highlights apply during [start, end) of their beat only', () => {
    assert.deepEqual(sorted(sceneStateAt(ohm, 7.15).highlightItemIds), ['i-formula']);
    assert.deepEqual(sorted(sceneStateAt(ohm, 8.99).highlightItemIds), ['i-formula']);
    assert.equal(sceneStateAt(ohm, 9.0).highlightItemIds.size, 0);
    assert.deepEqual(sorted(sceneStateAt(ohm, 14).highlightItemIds), ['i-def', 'i-formula']);
  });

  test('an item highlighted in consecutive beats glows across both when only the first is word-anchored', () => {
    // aadhi/compose/sync.py: b1 names the item early enough to re-time it (cue to b1's end); b2 was lit by
    // b1, so it emits no cue and keeps its beat-long highlight (tests/compose/test_timeline_sync.py)
    const beats = [
      beat({ beat_id: 'b0', start: 0, speech_end: 1, end: 1, board_item_id: 'i-def' }),
      beat({ beat_id: 'b1', start: 1, speech_end: 2.5, end: 3, highlight_item_ids: ['i-def'] }),
      beat({ beat_id: 'b2', start: 3, speech_end: 4.5, end: 5, highlight_item_ids: ['i-def'] }),
    ];
    const cue = { kind: 'emphasis', start: 1.8, end: 3, item_id: 'i-def', beat_id: 'b1', words: 'voltage' };
    const scene = { ...structuredClone(ohm), beats, sync_cues: [cue] };
    assert.equal(sceneStateAt(scene, 1.5).highlightItemIds.has('i-def'), false, 'b1 waits for its word');
    for (const t of [1.8, 2.5, 2.99, 3, 3.5, 4.6, 4.99]) {
      assert.ok(sceneStateAt(scene, t).highlightItemIds.has('i-def'), `lit at ${t}`);
    }
    // a b2 cue at its late word would switch the item off from b2's start until then (the old blink)
    const late = { ...cue, start: 4.6, end: 5, beat_id: 'b2' };
    const blinking = { ...structuredClone(ohm), beats, sync_cues: [cue, late] };
    assert.equal(sceneStateAt(blinking, 3.5).highlightItemIds.has('i-def'), false);
  });

  test('highlights of items that are not visible yet are ignored', () => {
    const scene = {
      ...ohm,
      beats: [
        beat({ beat_id: 'h', start: 0, speech_end: 1, end: 1, highlight_item_ids: ['i-take', 'i-head'] }),
        beat({ beat_id: 'r', start: 2, speech_end: 3, end: 3, board_item_id: 'i-take' }),
      ],
    };
    assert.deepEqual(sorted(sceneStateAt(scene, 0.5).highlightItemIds), ['i-head']);
  });

  test('blank example steps are revealed first, filled by a later beat', () => {
    const at3 = sceneStateAt(example, 3);
    assert.ok(at3.visibleItemIds.has('e-step1'));
    assert.equal(at3.filledItemIds.has('e-step1'), false);
    assert.equal(at3.activeItemId, 'e-step1');
    const at65 = sceneStateAt(example, 6.5);
    assert.ok(at65.filledItemIds.has('e-step1'));
    assert.equal(at65.activeItemId, 'e-step1', 'fill beat narrates the filled step');
    const at125 = sceneStateAt(example, 12.5);
    assert.deepEqual(sorted(at125.filledItemIds), ['e-step1', 'e-step2']);
    assert.deepEqual(sorted(at125.highlightItemIds), ['e-given']);
  });

  test('narration-only beat has no active item', () => {
    const s = sceneStateAt(ohm, 18);
    assert.equal(s.phase, 'beat');
    assert.equal(s.beatIndex, 7);
    assert.equal(s.activeItemId, null);
  });

  test('beat order is by start even if the array is not', () => {
    const b1 = beat({ beat_id: 'a', start: 0, speech_end: 1, end: 1, board_item_id: 'x' });
    const b2 = beat({ beat_id: 'b', start: 2, speech_end: 3, end: 3, board_item_id: 'y' });
    const scene = {
      scene_id: 'u', index: 0, type: 'content', start: 0, duration: 4, board: [{ id: 'x', kind: 'bullet', text: 'x' }, { id: 'y', kind: 'bullet', text: 'y' }],
      beats: [b2, b1],
    };
    const s = sceneStateAt(/** @type {any} */ (scene), 0.5);
    assert.equal(s.beatIndex, 1, 'beatIndex refers to the array position');
    assert.equal(s.activeItemId, 'x');
    assert.equal(sceneStateAt(/** @type {any} */ (scene), 2.5).activeItemId, 'y');
  });
});

describe('sceneStateAt: quiz timing', () => {
  const q = /** @type {import('../../js/shared/types.js').QuizTiming} */ (quiz.quiz);

  test('question beat, then a pause before the countdown', () => {
    assert.equal(sceneStateAt(quiz, 1).phase, 'beat');
    const gap = sceneStateAt(quiz, q.countdown_start - 0.05);
    assert.equal(gap.phase, 'pause');
    assert.equal(gap.beatIndex, -1);
  });

  test('countdown counts whole seconds down to 1', () => {
    const start = q.countdown_start;
    assert.equal(sceneStateAt(quiz, start).phase, 'countdown');
    assert.equal(sceneStateAt(quiz, start).countdownRemaining, 5);
    assert.equal(sceneStateAt(quiz, start + 0.99).countdownRemaining, 5);
    assert.equal(sceneStateAt(quiz, start + 1).countdownRemaining, 4);
    assert.equal(sceneStateAt(quiz, start + 4.5).countdownRemaining, 1);
    assert.equal(sceneStateAt(quiz, q.reveal_start - 1e-4).countdownRemaining, 1);
    assert.equal(sceneStateAt(quiz, start + 1).beatIndex, -1);
    assert.equal(sceneStateAt(quiz, start).quizRevealed, false);
  });

  test('every countdown second boundary is exact despite float sums', () => {
    for (let k = 0; k < q.countdown_seconds; k++) {
      assert.equal(sceneStateAt(quiz, q.countdown_start + k).countdownRemaining, q.countdown_seconds - k);
    }
  });

  test('reveal phase plays reveal beats, then tail; reveal persists', () => {
    const r = sceneStateAt(quiz, q.reveal_start);
    assert.equal(r.phase, 'reveal');
    assert.equal(r.quizRevealed, true);
    assert.equal(r.countdownRemaining, null);
    assert.equal(r.beatIndex, 1);
    assert.equal(quiz.beats?.[r.beatIndex].phase, 'reveal');
    const tail = sceneStateAt(quiz, quiz.duration - 0.01);
    assert.equal(tail.phase, 'tail');
    assert.equal(tail.quizRevealed, true);
    assert.equal(tail.beatIndex, -1);
  });

  test('reveal without reveal beats stays in reveal', () => {
    const scene = { ...quiz, beats: (quiz.beats || []).filter((b) => b.phase !== 'reveal') };
    assert.equal(sceneStateAt(scene, scene.duration - 0.01).phase, 'reveal');
  });

  test('non-quiz scenes never report countdown or reveal', () => {
    for (const t of [0, 5, 10, 19]) {
      const s = sceneStateAt(ohm, t);
      assert.equal(s.countdownRemaining, null);
      assert.equal(s.quizRevealed, false);
    }
  });
});

describe('sceneStateAt: side panel and terminal', () => {
  const showAt = ohm.side_panel?.show_at ?? 0;

  test('panel is visible from show_at', () => {
    assert.equal(sceneStateAt(ohm, showAt - 0.001).panelVisible, false);
    assert.equal(sceneStateAt(ohm, showAt).panelVisible, true);
  });

  test('panel hidden when the layout disables it', () => {
    const scene = { ...ohm, layout: { ...ohm.layout, show_side_panel: false } };
    assert.equal(sceneStateAt(scene, 10).panelVisible, false);
    assert.equal(sceneStateAt(scene, 10).terminalLines.length, 0);
  });

  test('terminal lines are revealed evenly across the rest of the scene', () => {
    const times = terminalLineTimes(3, showAt, ohm.duration);
    const step = (ohm.duration - showAt) / 4;
    times.forEach((tt, k) => assert.ok(Math.abs(tt - (showAt + (k + 1) * step)) < 1e-9));
    assert.equal(sceneStateAt(ohm, times[0] - 0.001).terminalLines.length, 0);
    assert.deepEqual([...sceneStateAt(ohm, times[0]).terminalLines], ['V = 12 V']);
    assert.equal(sceneStateAt(ohm, times[1]).terminalLines.length, 2);
    assert.deepEqual([...sceneStateAt(ohm, ohm.duration - 0.001).terminalLines], ['V = 12 V', 'R = 4 ohm', 'I = 3.0 A']);
  });

  test('terminalLines is a plain, fresh array (callers may keep or mutate it)', () => {
    const a = sceneStateAt(ohm, ohm.duration - 0.001).terminalLines;
    const b = sceneStateAt(ohm, ohm.duration - 0.001).terminalLines;
    assert.ok(Array.isArray(a));
    assert.equal(Object.getPrototypeOf(a), Array.prototype);
    assert.notEqual(a, b);
    a.push('mutated');
    assert.equal(sceneStateAt(ohm, ohm.duration - 0.001).terminalLines.length, 3);
  });

  test('terminalOutputLines normalises newlines, drops trailing blanks and caps the line count', () => {
    assert.deepEqual(terminalOutputLines('a\r\nb\rc\n\n  \n'), ['a', 'b', 'c']);
    assert.deepEqual(terminalOutputLines(''), []);
    assert.deepEqual(terminalOutputLines(null), []);
    const many = Array.from({ length: TERMINAL_MAX_LINES + 50 }, (_, i) => String(i)).join('\n');
    assert.equal(terminalOutputLines(many).length, TERMINAL_MAX_LINES);
  });

  test('terminal line split and reveal times match the terminal panel exactly', async () => {
    const timing = await import('../../js/player/panels/timing.js').catch(() => null);
    if (!timing) return; // panels module not present in this checkout
    const outputs = ['a\nb\nc', 'x\r\n\r\ny\n\n', '', Array.from({ length: 260 }, (_, i) => `l${i}`).join('\n')];
    for (const out of outputs) {
      assert.deepEqual(terminalOutputLines(out), timing.terminalOutputLines(out));
      const n = terminalOutputLines(out).length;
      assert.deepEqual(terminalLineTimes(n, 1.5, 20), timing.terminalLineTimes(1.5, 20, n));
    }
  });
});

describe('stateTimes', () => {
  /** Full output key (visual key + phase, beat and caption) to verify completeness. */
  const fullKey = (/** @type {any} */ scene, /** @type {number} */ t) => {
    const s = sceneStateAt(scene, t);
    return `${stateKey(scene, s)}|${s.phase}|${s.beatIndex}|${s.caption}|${s.countdownRemaining}`;
  };

  test('sorted, unique, within [0, duration), starting at 0', () => {
    for (const scene of timeline.scenes) {
      const times = stateTimes(scene);
      assert.equal(times[0], 0);
      for (let i = 1; i < times.length; i++) assert.ok(times[i] > times[i - 1], `${scene.scene_id} sorted`);
      assert.ok(times.every((t) => t >= 0 && t < scene.duration));
    }
  });

  test('complete: the state between consecutive state times never changes', () => {
    for (const scene of timeline.scenes) {
      const times = stateTimes(scene);
      for (let t = 0; t < scene.duration; t += 0.01) {
        let k = 0;
        while (k + 1 < times.length && times[k + 1] <= t) k++;
        assert.equal(fullKey(scene, t), fullKey(scene, times[k]), `${scene.scene_id} t=${t.toFixed(2)}`);
      }
    }
  });

  test('contains every reveal, fill, highlight edge, countdown second and panel time', () => {
    const times = stateTimes(ohm);
    for (const b of ohm.beats || []) {
      assert.ok(times.includes(b.start));
      assert.ok(times.includes(b.end));
    }
    assert.ok(times.includes(ohm.side_panel?.show_at ?? -1));
    const q = /** @type {any} */ (quiz.quiz);
    const qt = stateTimes(quiz);
    for (let k = 0; k < q.countdown_seconds; k++) assert.ok(qt.includes(q.countdown_start + k));
    assert.ok(qt.includes(q.reveal_start));
  });

  test('scene with no beats has a single state time', () => {
    assert.deepEqual(stateTimes(chapter), [0]);
  });
});

describe('stateKey and renderStateList', () => {
  test('keys are stable, scene-prefixed and ignore phase/caption', () => {
    const a = stateKey(ohm, sceneStateAt(ohm, 0.5));
    assert.equal(a, stateKey(ohm, sceneStateAt(ohm, 0.6)));
    assert.match(a, /^s1-[0-9a-f]{8}$/);
    // 2.9 (beat) and 3.05 (pause after speech) look identical
    assert.equal(stateKey(ohm, sceneStateAt(ohm, 2.9)), stateKey(ohm, sceneStateAt(ohm, 3.05)));
    assert.notEqual(stateKey(ohm, sceneStateAt(ohm, 0)), a);
  });

  test('render list merges identical consecutive visual states', () => {
    for (const scene of timeline.scenes) {
      const list = renderStateList(scene);
      assert.equal(list[0].t, 0);
      for (let i = 1; i < list.length; i++) {
        assert.notEqual(list[i].key, list[i - 1].key);
        assert.ok(list[i].t > list[i - 1].t);
      }
    }
    const list = renderStateList(ohm);
    for (const b of ohm.beats || []) {
      if (b.board_item_id) assert.ok(list.some((s) => s.t === b.start), `reveal state at ${b.start}`);
    }
    const qlist = renderStateList(quiz);
    const q = /** @type {any} */ (quiz.quiz);
    assert.equal(qlist.filter((s) => s.t >= q.countdown_start && s.t < q.reveal_start).length, q.countdown_seconds);
  });

  test('plans are cached per scene object', () => {
    assert.equal(planScene(ohm), planScene(ohm));
  });
});

describe('selectCue', () => {
  const cues = [
    { start: 0, end: 1, text: 'a' },
    { start: 1, end: 2, text: 'b' },
    { start: 3, end: 4, text: 'c' },
  ];
  test('half-open intervals and gaps', () => {
    assert.equal(selectCue(cues, -1), null);
    assert.equal(selectCue(cues, 0)?.text, 'a');
    assert.equal(selectCue(cues, 0.999)?.text, 'a');
    assert.equal(selectCue(cues, 1)?.text, 'b');
    assert.equal(selectCue(cues, 2.5), null);
    assert.equal(selectCue(cues, 3.5)?.text, 'c');
    assert.equal(selectCue(cues, 4), null);
    assert.equal(selectCue([], 1), null);
    assert.equal(selectCue(null, 1), null);
  });
  test('tolerates a slightly overlapping previous cue', () => {
    const overlap = [{ start: 0, end: 2.5, text: 'long' }, { start: 2, end: 2.2, text: 'short' }];
    assert.equal(selectCue(overlap, 2.3)?.text, 'long');
  });
});

describe('timeline helpers', () => {
  test('sceneIndexAt maps absolute time to scenes (intro = -1)', () => {
    assert.equal(sceneIndexAt(timeline, 0), -1);
    assert.equal(sceneIndexAt(timeline, 11.499), -1);
    assert.equal(sceneIndexAt(timeline, 11.5), 0);
    const s3 = timeline.scenes[3];
    assert.equal(sceneIndexAt(timeline, s3.start), 3);
    assert.equal(sceneIndexAt(timeline, s3.start - 1e-6), 2);
    assert.equal(sceneIndexAt(timeline, timeline.total_duration), timeline.scenes.length - 1);
    assert.equal(sceneIndexAt(timeline, 1e9), timeline.scenes.length - 1);
    assert.equal(sceneIndexAt({ scenes: [], total_duration: 0 }, 0), -1);
  });

  test('introCardAt', () => {
    assert.equal(introCardAt(timeline.intro, 0), -1);
    assert.equal(introCardAt(timeline.intro, 5.5), 0);
    assert.equal(introCardAt(timeline.intro, 8.49), 0);
    assert.equal(introCardAt(timeline.intro, 8.5), 1);
    assert.equal(introCardAt(timeline.intro, 11.5), -1);
    assert.equal(introCardAt(null, 1), -1);
  });

  test('chapterIndexAt', () => {
    const ch = timeline.chapters || [];
    assert.equal(chapterIndexAt(ch, 0), -1);
    assert.equal(chapterIndexAt(ch, ch[0].start), 0);
    assert.equal(chapterIndexAt(ch, ch[1].start + 1), 1);
  });

  test('conceptStateAt marks earlier concepts done', () => {
    const tl = { scenes: [{ concept_id: 'a' }, { concept_id: 'b' }, { concept_id: 'a' }, { concept_id: 'c' }], total_duration: 0 };
    const cs = conceptStateAt(/** @type {any} */ (tl), 2);
    assert.equal(cs.activeId, 'a');
    assert.deepEqual([...cs.doneIds], ['b']);
    assert.deepEqual([...conceptStateAt(/** @type {any} */ (tl), 3).doneIds].sort(), ['a', 'b']);
  });
});
