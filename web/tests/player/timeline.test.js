// @ts-check
/**
 * The fixture timeline obeys the backend time-base contract (aadhi/schemas/timeline.py
 * Timeline._time_base) and covers every board item kind and scene view, so the other suites
 * exercise the whole player.
 */
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import { BOARD_ITEM_KINDS, MASCOT_POSITIONS } from '../../js/shared/types.js';
import { viewKind } from '../../js/player/scenes/index.js';
import { loadTimeline } from './_fixture.js';

const EPS = 1e-3;
const timeline = loadTimeline();

describe('fixture timeline contract', () => {
  test('scenes are contiguous after the intro and total_duration matches', () => {
    let t = timeline.intro ? Number(timeline.intro.duration) : 0;
    timeline.scenes.forEach((s, i) => {
      assert.equal(s.index, i, `scene ${s.scene_id} index`);
      assert.ok(Math.abs(s.start - t) <= EPS, `scene ${s.scene_id} starts at ${s.start}, expected ${t}`);
      assert.ok(s.duration > 0);
      for (const b of s.beats || []) {
        assert.ok(b.start >= -EPS && b.end <= s.duration + EPS && b.speech_end >= b.start - EPS, `beat ${b.beat_id}`);
        assert.ok(b.speech_end <= b.end + EPS, `beat ${b.beat_id}: speech_end <= end`);
      }
      t = s.start + s.duration;
    });
    assert.ok(Math.abs(timeline.total_duration - t) <= EPS);
  });

  test('intro cards are inside the intro; chapters and captions are absolute and ordered', () => {
    const intro = timeline.intro;
    if (!intro) throw new Error('fixture has no intro');
    for (const c of intro.cards || []) assert.ok(c.start >= 0 && c.start + c.duration <= Number(intro.duration) + EPS);
    const chapters = timeline.chapters || [];
    chapters.forEach((c, i) => {
      assert.ok(c.start >= 0 && c.start <= timeline.total_duration);
      if (i) assert.ok(c.start >= chapters[i - 1].start);
    });
    const caps = timeline.captions || [];
    caps.forEach((c, i) => {
      assert.ok(c.end > c.start);
      if (i) assert.ok(c.start >= caps[i - 1].start);
    });
  });

  test('beat references point at board items; quiz timing is ordered', () => {
    for (const s of timeline.scenes) {
      const ids = new Set((s.board || []).map((i) => i.id));
      for (const b of s.beats || []) {
        if (b.board_item_id) assert.ok(ids.has(b.board_item_id), `${s.scene_id}: ${b.board_item_id}`);
        if (b.fill_item_id) assert.ok(ids.has(b.fill_item_id), `${s.scene_id}: ${b.fill_item_id}`);
        for (const hId of b.highlight_item_ids || []) assert.ok(ids.has(hId), `${s.scene_id}: ${hId}`);
      }
      if (s.quiz) {
        assert.ok(s.quiz.countdown_start <= s.quiz.reveal_start && s.quiz.reveal_start <= s.duration);
        assert.ok(s.quiz.correct_index >= 0 && s.quiz.correct_index < s.quiz.options.length);
        assert.equal(s.quiz.feedback_wrong.length, s.quiz.options.length);
      }
      if (s.layout?.mascot_position) assert.ok(MASCOT_POSITIONS.includes(s.layout.mascot_position));
    }
  });

  test('covers every board item kind and every scene view', () => {
    /** @type {Set<string>} */
    const kinds = new Set(timeline.scenes.flatMap((s) => (s.board || []).map((i) => String(i.kind))));
    for (const k of BOARD_ITEM_KINDS) assert.ok(kinds.has(k), `fixture lacks a ${k} item`);
    const views = new Set(timeline.scenes.map((s) => viewKind(s.type)));
    for (const v of ['board', 'card', 'media', 'quiz', 'interactive']) assert.ok(views.has(/** @type {any} */ (v)), `fixture lacks a ${v} scene`);
    assert.ok(timeline.scenes.some((s) => s.side_panel), 'a side panel');
    assert.ok(timeline.scenes.some((s) => (s.board || []).some((i) => i.blank)), 'a blank worked-example step');
  });
});
