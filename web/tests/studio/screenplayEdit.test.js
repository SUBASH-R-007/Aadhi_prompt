import './_dom.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import * as E from '../../js/studio/lib/screenplayEdit.js';
import { localProblems } from '../../js/studio/lib/validationIssues.js';
import { sampleScreenplay } from './fixtures.js';

/** Re-implementation of the server's BoardScene/SceneBase invariants (screenplay.py). */
function assertSceneValid(scene) {
  const beats = E.sceneBeats(scene).map((b) => b.beat);
  const ids = beats.map((b) => b.id);
  assert.equal(new Set(ids).size, ids.length, 'beat ids unique');
  for (const id of ids) assert.match(id, /^[a-z0-9][a-z0-9_-]{0,63}$/);
  if (!E.isBoardScene(scene)) {
    for (const b of beats) {
      assert.equal(b.board_item_id ?? null, null);
      assert.equal(b.fill_item_id ?? null, null);
      assert.deepEqual(b.highlight_item_ids || [], []);
    }
  } else {
    const items = new Map(scene.board.map((i) => [i.id, i]));
    assert.equal(items.size, scene.board.length, 'item ids unique');
    const revealedAt = new Map();
    scene.beats.forEach((b, idx) => {
      if (b.board_item_id) {
        assert.ok(items.has(b.board_item_id), `reveal ${b.board_item_id} exists`);
        assert.ok(!revealedAt.has(b.board_item_id), 'revealed once');
        revealedAt.set(b.board_item_id, idx);
      }
    });
    const filled = new Set();
    scene.beats.forEach((b, idx) => {
      assert.ok((b.highlight_item_ids || []).length <= 2);
      for (const h of b.highlight_item_ids || []) assert.ok(items.has(h), `highlight ${h} exists`);
      if (b.fill_item_id) {
        const it = items.get(b.fill_item_id);
        assert.ok(it && it.blank && it.kind === 'example_step', 'fill targets a blank step');
        assert.ok(!filled.has(b.fill_item_id), 'filled once');
        assert.ok((revealedAt.get(b.fill_item_id) ?? -1) <= idx, 'fill after reveal');
        filled.add(b.fill_item_id);
      }
    });
    for (const it of scene.board) if (it.blank) assert.equal(it.kind, 'example_step');
  }
  if (scene.side_panel && scene.side_panel.show_from_beat_id) assert.ok(ids.includes(scene.side_panel.show_from_beat_id));
  if (scene.type === 'quiz_checkpoint') {
    assert.equal(scene.feedback_wrong.length, scene.options.length);
    assert.equal(scene.option_misconception_ids.length, scene.options.length);
    assert.ok(scene.correct_index >= 0 && scene.correct_index < scene.options.length);
  }
}

function assertScreenplayValid(sp) {
  const ids = sp.scenes.map((s) => s.id);
  assert.equal(new Set(ids).size, ids.length, 'scene ids unique');
  for (const s of sp.scenes) assertSceneValid(s);
  for (const ch of sp.chapters) for (const id of ch.scene_ids) assert.ok(ids.includes(id), `chapter ref ${id}`);
}

const scene = (sp, id) => sp.scenes.find((s) => s.id === id);

test('fixture satisfies the invariants', () => {
  assertScreenplayValid(sampleScreenplay());
});

test('moveScene reorders scenes and chapter scene lists, without mutating input', () => {
  const sp = sampleScreenplay();
  const frozen = JSON.stringify(sp);
  const out = E.moveScene(sp, 3, 2);
  assert.equal(JSON.stringify(sp), frozen, 'input untouched');
  assert.deepEqual(out.scenes.map((s) => s.id), ['s1', 's2', 's4', 's3']);
  assert.deepEqual(out.chapters[1].scene_ids, ['s4', 's3']);
  const clamped = E.moveScene(sp, 0, 99);
  assert.deepEqual(clamped.scenes.map((s) => s.id), ['s2', 's3', 's4', 's1']);
  assertScreenplayValid(out);
});

test('addScene creates valid scenes of every type in the predecessor chapter', () => {
  let sp = sampleScreenplay();
  for (const type of E.SCENE_TYPES) {
    const r = E.addScene(sp, type, 1, { manimTemplate: { name: 'equation_steps', example_params: { steps: ['a'] } } });
    assert.equal(r.scene.type, type);
    assert.equal(r.scene.chapter_id, 'ch1');
    assert.equal(r.screenplay.scenes[2].id, r.scene.id);
    assert.ok(r.screenplay.chapters[0].scene_ids.includes(r.scene.id));
    if (type === 'chapter_card') assert.equal(r.scene.beats.length, 0);
    else assert.equal(r.scene.beats.length, 1);
    if (type === 'quiz_checkpoint') assert.equal(r.scene.reveal_beats.length, 1);
    if (type === 'simulation') assert.deepEqual(r.scene.manim, { template: 'equation_steps', params: { steps: ['a'] }, code: null });
    assertScreenplayValid(r.screenplay);
    sp = r.screenplay;
  }
  assert.equal(new Set(sp.scenes.map((s) => s.id)).size, sp.scenes.length);
  assert.throws(() => E.addScene(sp, 'bogus', 0));
});

test('simulation without templates gets free-form code', () => {
  const r = E.addScene(sampleScreenplay(), 'simulation', -1);
  assert.equal(r.screenplay.scenes[0].id, r.scene.id);
  assert.equal(r.scene.manim.template, null);
  assert.match(r.scene.manim.code, /AadhiScene/);
});

test('duplicateScene re-issues ids and rewrites internal references', () => {
  const sp = sampleScreenplay();
  const { screenplay, scene: copy } = E.duplicateScene(sp, 2);
  assert.equal(copy.id, 's3-copy');
  assert.equal(screenplay.scenes[3].id, 's3-copy');
  assert.deepEqual(copy.board.map((i) => i.id), ['s3-copy-i1', 's3-copy-i2']);
  assert.deepEqual(copy.beats.map((b) => b.id), ['s3-copy-b1', 's3-copy-b2', 's3-copy-b3']);
  assert.equal(copy.beats[2].fill_item_id, 's3-copy-i2');
  assert.deepEqual(copy.beats[2].highlight_item_ids, ['s3-copy-i1']);
  assert.ok(screenplay.chapters[1].scene_ids.includes('s3-copy'));
  assertScreenplayValid(screenplay);
  // duplicating again gives another unique id
  const again = E.duplicateScene(screenplay, 2);
  assert.equal(again.scene.id, 's3-copy-2');
  // side panel beat reference follows the copy
  const dup2 = E.duplicateScene(sp, 1).scene;
  assert.equal(dup2.side_panel.show_from_beat_id, 's2-copy-b2');
});

test('deleteScene removes chapter references', () => {
  const out = E.deleteScene(sampleScreenplay(), 1);
  assert.deepEqual(out.scenes.map((s) => s.id), ['s1', 's3', 's4']);
  assert.deepEqual(out.chapters[0].scene_ids, ['s1']);
  assertScreenplayValid(out);
});

test('addBeat inserts a uniquely named beat', () => {
  const sp = sampleScreenplay();
  const { scene: s, beat } = E.addBeat(scene(sp, 's2'), 0);
  assert.equal(beat.id, 's2-b4');
  assert.deepEqual(s.beats.map((b) => b.id), ['s2-b1', 's2-b4', 's2-b2', 's2-b3']);
  assertSceneValid(s);
  const q = E.addBeat(scene(sp, 's4'), 0, 'reveal');
  assert.equal(q.beat.id, 's4-b3');
  assert.equal(q.scene.reveal_beats.length, 2);
});

test('addBeat respects per-type maximums', () => {
  const sp = sampleScreenplay();
  let card = E.addScene(sp, 'chapter_card', 0).scene;
  for (let i = 0; i < 4; i++) card = E.addBeat(card, i - 1).scene;
  assert.equal(E.canAddBeat(card), false);
  assert.throws(() => E.addBeat(card, 0));
});

test('removeBeat keeps reveal/fill/highlight references valid', () => {
  const sp = sampleScreenplay();
  // Remove the beat that reveals the blank step s3-i2: the item becomes visible from the
  // start, so the later fill stays valid.
  let s = E.removeBeat(scene(sp, 's3'), 1);
  assert.deepEqual(s.beats.map((b) => b.id), ['s3-b1', 's3-b3']);
  assert.equal(s.beats[1].fill_item_id, 's3-i2');
  assertSceneValid(s);
  // Removing the reveal of s2-i1 makes s2-i1 visible from start: highlights stay valid.
  s = E.removeBeat(scene(sp, 's2'), 0);
  assert.deepEqual(s.beats[0].highlight_item_ids, ['s2-i1']);
  assertSceneValid(s);
  // side panel shown from the removed beat moves to the beat taking its place
  assert.equal(s.side_panel.show_from_beat_id, 's2-b2');
  const s2 = E.removeBeat(scene(sp, 's2'), 1);
  assert.equal(s2.side_panel.show_from_beat_id, 's2-b3');
  // cannot remove the last beat
  const title = scene(sp, 's1');
  assert.equal(E.canRemoveBeat(title), false);
  assert.throws(() => E.removeBeat(title, 0));
});

test('formula legend rows keep naming a beat of their scene (removal, duplication)', () => {
  const sp = sampleScreenplay();
  const s2 = scene(sp, 's2');
  const formula = {
    id: 's2-f9',
    kind: 'formula',
    latex: 'V = I R',
    text: '',
    variables: [
      { symbol_latex: 'V', meaning: 'voltage', unit: 'V', beat_id: s2.beats[1].id },
      { symbol_latex: 'I', meaning: 'current', unit: 'A' },
    ],
  };
  const withFormula = { ...s2, board: [...s2.board, formula] };
  // the anchored beat is removed: the row is automatic again (no dangling beat_id); the input is untouched
  const removed = E.removeBeat(withFormula, 1);
  assert.equal('beat_id' in removed.board.find((i) => i.id === 's2-f9').variables[0], false);
  assert.equal(formula.variables[0].beat_id, s2.beats[1].id);
  // another beat is removed: the anchor stays
  assert.equal(E.removeBeat(withFormula, 0).board.find((i) => i.id === 's2-f9').variables[0].beat_id, s2.beats[1].id);
  // a duplicated scene's rows name the copy's beats
  const sp2 = { ...sp, scenes: sp.scenes.map((x) => (x.id === 's2' ? withFormula : x)) };
  const { scene: copy } = E.duplicateScene(sp2, sp2.scenes.findIndex((x) => x.id === 's2'));
  const copied = copy.board.find((i) => i.kind === 'formula' && i.latex === 'V = I R');
  assert.equal(copied.variables[0].beat_id, copy.beats[1].id);
  assert.equal('beat_id' in copied.variables[1], false);
});

test('moveBeat clears fills that would precede their reveal and stale highlights', () => {
  const sp = sampleScreenplay();
  // Move the filling beat (index 2) before the revealing beat (index 1).
  const s = E.moveBeat(scene(sp, 's3'), 2, 0);
  assert.deepEqual(s.beats.map((b) => b.id), ['s3-b3', 's3-b1', 's3-b2']);
  assert.equal(s.beats[0].fill_item_id, null, 'fill before reveal is cleared');
  assert.deepEqual(s.beats[0].highlight_item_ids, [], 'highlight of a not-yet-visible item is cleared');
  assertSceneValid(s);
});

test('reveal/fill/highlight options are constrained to valid items', () => {
  const sp = sampleScreenplay();
  const s2 = scene(sp, 's2');
  assert.deepEqual(E.revealOptions(s2, 0).map((i) => i.id), ['s2-i1', 's2-i3']);
  assert.deepEqual(E.revealOptions(s2, 2).map((i) => i.id), ['s2-i3']);
  assert.deepEqual(E.highlightOptions(s2, 0).map((i) => i.id), ['s2-i3']);
  assert.deepEqual(E.highlightOptions(s2, 2).map((i) => i.id), ['s2-i1', 's2-i2', 's2-i3']);
  const s3 = scene(sp, 's3');
  assert.deepEqual(E.fillOptions(s3, 0).map((i) => i.id), [], 'blank revealed at beat 1 cannot be filled at beat 0');
  assert.deepEqual(E.fillOptions(s3, 1).map((i) => i.id), [], 'already filled by beat 2');
  assert.deepEqual(E.fillOptions(s3, 2).map((i) => i.id), ['s3-i2']);
  assert.deepEqual(E.revealOptions(scene(sp, 's4'), 0), []);
});

test('setBeatReveal moves a reveal between beats and keeps fills valid', () => {
  const sp = sampleScreenplay();
  let s = E.setBeatReveal(scene(sp, 's3'), 2, 's3-i2');
  assert.equal(s.beats[1].board_item_id, null, 'previous revealer released the item');
  assert.equal(s.beats[2].board_item_id, 's3-i2');
  assert.equal(s.beats[2].fill_item_id, 's3-i2', 'fill at the reveal beat is allowed');
  assertSceneValid(s);
  s = E.setBeatReveal(scene(sp, 's2'), 0, null);
  assert.equal(s.beats[0].board_item_id, null);
  assert.throws(() => E.setBeatReveal(scene(sp, 's2'), 0, 'nope'));
});

test('setBeatFill only accepts valid blank steps', () => {
  const sp = sampleScreenplay();
  assert.throws(() => E.setBeatFill(scene(sp, 's3'), 0, 's3-i2'));
  assert.throws(() => E.setBeatFill(scene(sp, 's3'), 2, 's3-i1'), /cannot be filled/);
  const cleared = E.setBeatFill(scene(sp, 's3'), 2, null);
  assert.equal(cleared.beats[2].fill_item_id, null);
});

test('setBeatHighlights dedupes, filters and caps at two', () => {
  const sp = sampleScreenplay();
  const s = E.setBeatHighlights(scene(sp, 's2'), 2, ['s2-i1', 's2-i1', 'zzz', 's2-i2', 's2-i3']);
  assert.deepEqual(s.beats[2].highlight_item_ids, ['s2-i1', 's2-i2']);
  const s0 = E.setBeatHighlights(scene(sp, 's2'), 0, ['s2-i1']);
  assert.deepEqual(s0.beats[0].highlight_item_ids, [], 'not visible yet');
});

test('deleteBoardItem clears every reference to the item', () => {
  const sp = sampleScreenplay();
  let s = E.deleteBoardItem(scene(sp, 's2'), 's2-i1');
  assert.deepEqual(s.board.map((i) => i.id), ['s2-i2', 's2-i3']);
  assert.equal(s.beats[0].board_item_id, null);
  assert.deepEqual(s.beats[1].highlight_item_ids, []);
  assert.deepEqual(s.beats[2].highlight_item_ids, ['s2-i2']);
  assertSceneValid(s);
  s = E.deleteBoardItem(scene(sp, 's3'), 's3-i2');
  assert.equal(s.beats[1].board_item_id, null);
  assert.equal(s.beats[2].fill_item_id, null);
  assertSceneValid(s);
});

test('addBoardItem produces schema-valid placeholders for every kind', () => {
  const sp = sampleScreenplay();
  let s = { ...scene(sp, 's1'), board: [] };
  for (const kind of E.BOARD_ITEM_KINDS) {
    if (s.board.length >= E.MAX_BOARD_ITEMS) break;
    const r = E.addBoardItem(s, kind, s.board.length - 1, sp);
    s = r.scene;
    const it = r.item;
    assert.equal(it.kind, kind);
    if (kind === 'formula') assert.ok(it.latex);
    if (kind === 'code') assert.ok(it.code && it.language);
    if (kind === 'table') assert.ok(it.headers.length && it.rows.every((row) => row.length === it.headers.length));
    if (kind === 'figure') assert.equal(it.figure_id, 'fig-1');
  }
  assert.equal(s.board.length, 12);
  assert.throws(() => E.addBoardItem(s, 'bullet', 0, sp), /at most 12/);
  assert.throws(() => E.addBoardItem(scene(sp, 's4'), 'bullet', 0, sp));
  // Without source figures a figure item can still be added (the teacher uploads one); it
  // starts without a figure, which localProblems reports until one is chosen.
  const noFigures = { ...sp, figures: [] };
  const fig = E.addBoardItem(scene(sp, 's2'), 'figure', 0, noFigures);
  assert.equal(fig.item.kind, 'figure');
  assert.equal(fig.item.figure_id, null);
  const withFig = E.updateScene(noFigures, 's2', () => fig.scene);
  assert.ok(localProblems(withFig).some((p) => p.code === 'local.item_figure' && p.scene_id === 's2'));
  const uploaded = E.addFigure(withFig, { asset_key: 'k-up', caption: 'Mine' });
  const fixed = E.updateScene(uploaded.screenplay, 's2', (s) => E.updateBoardItem(s, fig.item.id, { figure_id: uploaded.figureId }));
  assert.equal(localProblems(fixed).filter((p) => p.code === 'local.item_figure').length, 0);
  // Switching an item to "figure" works without figures too.
  assert.equal(E.setBoardItemKind(scene(sp, 's2'), 's2-i3', 'figure', noFigures).board[2].figure_id, null);
});

test('setBoardItemKind and setItemBlank drop fills that become invalid', () => {
  const sp = sampleScreenplay();
  let s = E.setBoardItemKind(scene(sp, 's3'), 's3-i2', 'bullet', sp);
  assert.equal(s.board[1].blank, false);
  assert.equal(s.beats[2].fill_item_id, null);
  assertSceneValid(s);
  s = E.setItemBlank(scene(sp, 's3'), 's3-i2', false);
  assert.equal(s.beats[2].fill_item_id, null);
  assertSceneValid(s);
  assert.throws(() => E.setItemBlank(scene(sp, 's2'), 's2-i1', true));
  s = E.setBoardItemKind(scene(sp, 's2'), 's2-i1', 'table', sp);
  assert.deepEqual(s.board[0].headers, ['Column 1', 'Column 2']);
});

test('moveBoardItem only changes layout order', () => {
  const sp = sampleScreenplay();
  const s = E.moveBoardItem(scene(sp, 's2'), 2, 0);
  assert.deepEqual(s.board.map((i) => i.id), ['s2-i3', 's2-i1', 's2-i2']);
  assert.deepEqual(s.beats, scene(sp, 's2').beats);
});

test('changeSceneType converts shapes and strips board references', () => {
  const sp = sampleScreenplay();
  let out = E.changeSceneType(sp, 's2', 'summary');
  assert.equal(scene(out, 's2').board.length, 3, 'board kept between board types');
  out = E.changeSceneType(sp, 's2', 'quiz_checkpoint');
  const q = scene(out, 's2');
  assert.equal(q.type, 'quiz_checkpoint');
  assert.equal(q.board, undefined);
  assert.equal(q.options.length, 2);
  assert.equal(q.reveal_beats.length, 1);
  assertSceneValid(q);
  out = E.changeSceneType(sp, 's4', 'content');
  const c = scene(out, 's4');
  assert.deepEqual(c.board, []);
  assert.equal(c.reveal_beats, undefined);
  assert.equal(c.question, undefined);
  assertSceneValid(c);
  out = E.changeSceneType(sp, 's3', 'chapter_card');
  assert.equal(scene(out, 's3').chapter_label, 'Part 1');
  out = E.changeSceneType(sp, 's3', 'interactive');
  assert.match(scene(out, 's3').p5_code, /createCanvas/);
  out = E.changeSceneType(sp, 's3', 'ai_video');
  assert.equal(scene(out, 's3').video_prompt, 'Worked example');
  assertScreenplayValid(out);
});

test('quiz option helpers keep arrays aligned and the correct answer stable', () => {
  const sp = sampleScreenplay();
  const q = scene(sp, 's4');
  let s = E.addQuizOption(q);
  assert.equal(s.options.length, 4);
  assert.equal(s.feedback_wrong.length, 4);
  assert.equal(s.option_misconception_ids.length, 4);
  s = E.removeQuizOption(q, 1);
  assert.deepEqual(s.options, ['2 A', '0.5 A']);
  assert.deepEqual(s.option_misconception_ids, [null, 'm1']);
  assert.equal(s.correct_index, 0);
  s = E.moveQuizOption(q, 0, 2);
  assert.deepEqual(s.options, ['18 A', '0.5 A', '2 A']);
  assert.equal(s.correct_index, 2);
  assert.deepEqual(s.feedback_wrong, ['You multiplied', 'You inverted', '']);
  s = E.removeQuizOption({ ...q, correct_index: 2 }, 0);
  assert.equal(s.correct_index, 1);
  s = E.removeQuizOption(q, 0);
  assert.equal(s.correct_index, 0, 'removing the correct option falls back to 0');
  assert.throws(() => E.removeQuizOption(E.removeQuizOption(q, 0), 0));
});

test('sanitizeScreenplayRefs drops dangling cross references', () => {
  const sp = sampleScreenplay();
  sp.chapters[0].scene_ids.push('ghost');
  sp.scenes[1].concept_id = 'gone';
  sp.scenes[1].objective_ids = ['obj-1', 'obj-x'];
  sp.scenes[3].option_misconception_ids = [null, 'nope', 'm1'];
  sp.concept_map[1].depends_on = ['voltage', 'missing'];
  const out = E.sanitizeScreenplayRefs(sp);
  assert.deepEqual(out.chapters[0].scene_ids, ['s1', 's2']);
  assert.equal(out.scenes[1].concept_id, null);
  assert.deepEqual(out.scenes[1].objective_ids, ['obj-1']);
  assert.deepEqual(out.scenes[3].option_misconception_ids, [null, null, 'm1']);
  assert.deepEqual(out.concept_map[1].depends_on, ['voltage']);
});

test('ids: toSlug and uniqueId follow the schema slug rule', () => {
  assert.equal(E.toSlug('Hello World!'), 'hello-world');
  assert.equal(E.toSlug('__x'), 'x');
  assert.equal(E.toSlug(''), 'item');
  assert.equal(E.toSlug('-'), 'item');
  assert.equal(E.uniqueId('s1', ['s1', 's1-2']), 's1-3');
  assert.equal(E.uniqueId('x'.repeat(80), []).length, 64);
  assert.equal(E.nextBeatId({ id: 'a'.repeat(64), beats: [] }).length, 64);
});

test('updateBeat and updateBoardItem never change ids', () => {
  const sp = sampleScreenplay();
  const s = E.updateBeat(scene(sp, 's2'), 0, { id: 'hacked', narration: 'New' });
  assert.equal(s.beats[0].id, 's2-b1');
  assert.equal(s.beats[0].narration, 'New');
  const s2 = E.updateBoardItem(scene(sp, 's2'), 's2-i1', { id: 'x', kind: 'formula', text: 'T' });
  assert.equal(s2.board[0].id, 's2-i1');
  assert.equal(s2.board[0].kind, 'bullet');
  assert.equal(s2.board[0].text, 'T');
  const same = E.updateScene(sp, 'nope', () => ({}));
  assert.equal(same, sp);
});

test('truncateText never leaves half of an emoji (a lone surrogate the server refuses)', () => {
  assert.equal(E.truncateText('abc', 5), 'abc');
  assert.equal(E.truncateText('ab\u{1F600}cd', 3), 'ab', 'the pair is dropped, not split');
  assert.equal(E.truncateText('ab\u{1F600}cd', 4), 'ab\u{1F600}');
  const sp = sampleScreenplay();
  sp.scenes[1].title = `${'x'.repeat(239)}\u{1F600}`; // "<title> (copy)" cut at 240 units falls inside the emoji
  const copy = E.duplicateScene(sp, 1).scene;
  assert.equal(copy.title, 'x'.repeat(239));
});
