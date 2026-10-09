// Editor: split a scene at a beat (splitScene.js), field-level merges (sceneMerge.js) and the history /
// changes helpers (changes.js). Pure functions: no DOM.
import test from 'node:test';
import assert from 'node:assert/strict';
import { splitScene, splitProblem, canSplitType } from '../../js/studio/views/editor/splitScene.js';
import { mergeDrafts, mergeScene } from '../../js/studio/views/editor/sceneMerge.js';
import { editLabel, fieldWords, changeBadge, changesBySceneId, restorePosition, HISTORY_LIMIT } from '../../js/studio/views/editor/changes.js';
import * as E from '../../js/studio/lib/screenplayEdit.js';
import { localProblems } from '../../js/studio/lib/validationIssues.js';
import { sampleScreenplay } from './fixtures.js';

/** Every board reference of a scene points at an item of its own board. */
function refsValid(scene) {
  const items = new Set((scene.board || []).map((i) => i.id));
  for (const b of scene.beats) {
    if (b.board_item_id) assert.ok(items.has(b.board_item_id), `${scene.id}: reveal ${b.board_item_id}`);
    if (b.fill_item_id) assert.ok(items.has(b.fill_item_id), `${scene.id}: fill ${b.fill_item_id}`);
    for (const id of b.highlight_item_ids || []) assert.ok(items.has(id), `${scene.id}: highlight ${id}`);
  }
  assert.deepEqual(E.repairSceneRefs(scene), scene, `${scene.id}: references already repaired`);
}

test('split: a board scene keeps beats before the cut; the new scene takes the rest and the items they reveal', () => {
  const sp = sampleScreenplay();
  const { screenplay: out, scene: b } = splitScene(sp, 's2', 1);
  assert.deepEqual(out.scenes.map((s) => s.id), ['s1', 's2', 's2-2', 's3', 's4']);
  const a = E.findScene(out, 's2');
  assert.deepEqual(a.beats.map((x) => x.id), ['s2-b1']);
  assert.deepEqual(a.board.map((i) => i.id), ['s2-i1', 's2-i3'], 'A keeps what it reveals and what is visible from the start');
  assert.equal(b.id, 's2-2');
  assert.equal(b.type, 'content');
  assert.equal(b.title, 'The law (continued)');
  assert.equal(b.chapter_id, 'ch1');
  assert.equal(b.concept_id, 'ohms-law');
  assert.deepEqual(b.objective_ids, ['obj-1']);
  assert.deepEqual(b.beats.map((x) => x.id), ['s2-2-b1', 's2-2-b2']);
  assert.deepEqual(b.beats.map((x) => x.narration), ['Here is the formula.', 'Remember it.']);
  assert.deepEqual(b.board.map((i) => [i.id, i.kind, i.latex]), [['s2-2-i1', 'formula', 'V = IR']]);
  assert.equal(b.beats[0].board_item_id, 's2-2-i1');
  assert.deepEqual(b.beats[0].highlight_item_ids, [], 'a highlight of an item that stayed in A is dropped');
  assert.deepEqual(b.beats[1].highlight_item_ids, ['s2-2-i1']);
  // the visual (side panel) stays with the first part; its beat moved, so it shows from the start
  assert.equal(a.side_panel.kind, 'skill_tree');
  assert.equal(a.side_panel.show_from_beat_id, null);
  assert.equal(b.side_panel, null);
  // chapter membership follows the scene order
  assert.deepEqual(out.chapters.find((c) => c.id === 'ch1').scene_ids, ['s1', 's2', 's2-2']);
  refsValid(a);
  refsValid(b);
  assert.deepEqual(localProblems(out), [], 'both parts are valid');
  // the input is untouched
  assert.equal(E.findScene(sp, 's2').beats.length, 3);
  assert.equal(E.findScene(sp, 's2-2'), null);
});

test('split: a blank step filled after the cut goes with its fill; nothing points across the cut', () => {
  const { screenplay: out, scene: b } = splitScene(sampleScreenplay(), 's3', 2);
  const a = E.findScene(out, 's3');
  assert.deepEqual(a.board.map((i) => i.id), ['s3-i1']);
  assert.equal(a.beats[1].board_item_id, null, 'A no longer reveals the moved step');
  assert.equal(b.type, 'example');
  assert.deepEqual(b.board.map((i) => [i.id, i.blank]), [['s3-2-i1', true]]);
  assert.equal(b.beats[0].fill_item_id, 's3-2-i1');
  assert.deepEqual(b.beats[0].highlight_item_ids, []);
  refsValid(a);
  refsValid(b);
  assert.deepEqual(localProblems(out), []);
});

test('split: only between two main beats, never for quizzes, chapter cards or animations', () => {
  const sp = sampleScreenplay();
  assert.match(String(splitProblem(sp, 's2', 0)), /between two beats/);
  assert.match(String(splitProblem(sp, 's2', 3)), /between two beats/);
  assert.match(String(splitProblem(sp, 's2', 1.5)), /between two beats/);
  assert.match(String(splitProblem(sp, 's1', 1)), /at least two beats/);
  assert.match(String(splitProblem(sp, 's4', 1)), /quiz/);
  assert.match(String(splitProblem(sp, 'nope', 1)), /no longer exists/);
  let sim = E.changeSceneType(sp, 's2', 'simulation');
  assert.match(String(splitProblem(sim, 's2', 1)), /animation/);
  let card = E.changeSceneType(sp, 's2', 'chapter_card');
  card = E.updateScene(card, 's2', (s) => {
    s.beats = [E.newBeat(s, { narration: 'One' }), { ...E.newBeat(s), id: 's2-b9', narration: 'Two' }];
  });
  assert.match(String(splitProblem(card, 's2', 1)), /chapter card/);
  assert.throws(() => splitScene(sp, 's4', 1), /quiz/);
  assert.equal(canSplitType(E.findScene(sp, 's2')), true);
  assert.equal(canSplitType(E.findScene(sp, 's1')), false, 'one beat');
  assert.equal(canSplitType(E.findScene(sp, 's4')), false);
  assert.equal(canSplitType(E.findScene(sim, 's2')), false);
});

test('split: footage and sketches stay with the first part; the second is a content scene; hold and skip', () => {
  let sp = E.changeSceneType(sampleScreenplay(), 's2', 'ai_video');
  sp = E.updateScene(sp, 's2', (s) => {
    s.video_prompt = 'A river flowing';
    s.min_seconds = 20;
    s.hidden = true;
  });
  const { screenplay: out, scene: b } = splitScene(sp, 's2', 2);
  const a = E.findScene(out, 's2');
  assert.equal(a.type, 'ai_video');
  assert.equal(a.video_prompt, 'A river flowing');
  assert.equal(a.min_seconds, 20, 'the hold stays with the first part');
  assert.equal(b.type, 'content');
  assert.deepEqual(b.board, []);
  assert.equal('video_prompt' in b, false);
  assert.equal('min_seconds' in b, false);
  assert.equal(b.hidden, true, 'a skipped scene stays skipped in both parts');
  assert.deepEqual(localProblems(out), []);
  // a visible scene's parts carry no `hidden` key at all (default left out)
  const plain = splitScene(sampleScreenplay(), 's2', 1).scene;
  assert.equal('hidden' in plain, false);
});

test('split: legend rows and companion entries follow moved items; ids stay unique and short', () => {
  let sp = sampleScreenplay();
  sp = E.updateScene(sp, 's2', (s) => {
    s.board[1].variables = [{ symbol_latex: 'V', meaning: 'voltage', unit: 'V', beat_id: 's2-b3' }, { symbol_latex: 'I', meaning: 'current', unit: 'A', beat_id: 's2-b1' }];
  });
  sp.companion_sheet.key_formulas = [{ name: 'Ohm', latex: 'V = IR', description: '', variables: [], source_item_id: 's2/s2-i2' }, { name: 'Other', latex: null, description: '', variables: [], source_item_id: 's2/s2-i1' }];
  sp.scenes.push({ ...E.findScene(sp, 's1'), id: 's2-2', chapter_id: null });
  const { screenplay: out, scene: b } = splitScene(sp, 's2', 1);
  assert.equal(b.id, 's2-2-2', 'a free id');
  const formula = b.board.find((i) => i.kind === 'formula');
  assert.equal(formula.variables[0].beat_id, 's2-2-2-b2', 'the row follows its beat');
  assert.equal('beat_id' in formula.variables[1], false, 'a row whose beat stayed behind is automatic again');
  assert.equal(out.companion_sheet.key_formulas[0].source_item_id, 's2-2-2/s2-2-2-i1');
  assert.equal(out.companion_sheet.key_formulas[1].source_item_id, 's2/s2-i1', 'items that stayed keep their entries');
  // a 64-character scene id: the parts' ids stay within the slug limit
  let long = sampleScreenplay();
  const longId = `s${'x'.repeat(63)}`;
  long = E.updateScene(long, 's2', (s) => {
    s.id = longId;
  });
  long.chapters[0].scene_ids = ['s1', longId];
  const r = splitScene(long, longId, 1);
  for (const id of [r.scene.id, ...r.scene.beats.map((x) => x.id), ...r.scene.board.map((i) => i.id)]) assert.ok(id.length <= 64 && /^[a-z0-9][a-z0-9_-]*$/.test(id), id);
});

test('merge: two edits of different fields (or beats) of one scene merge without a conflict', () => {
  const base = sampleScreenplay();
  const mine = E.updateScene(base, 's2', (s) => {
    s.title = 'My title';
    s.beats[0].narration = 'Mine first.';
  });
  const theirs = E.updateScene(base, 's2', (s) => {
    s.subtitle = 'Their subtitle';
    s.beats[2].narration = 'Theirs last.';
    s.board[2].text = 'Their takeaway';
  });
  const merged = mergeDrafts(base, mine, theirs);
  assert.deepEqual(merged.conflicts, []);
  const s2 = E.findScene(merged.screenplay, 's2');
  assert.equal(s2.title, 'My title');
  assert.equal(s2.subtitle, 'Their subtitle');
  assert.deepEqual(s2.beats.map((b) => b.narration), ['Mine first.', 'Here is the formula.', 'Theirs last.']);
  assert.equal(s2.board[2].text, 'Their takeaway');
});

test('merge: the same field changed on both sides stays a conflict with my version kept', () => {
  const base = sampleScreenplay();
  const mine = E.updateScene(base, 's2', (s) => {
    s.title = 'Mine';
  });
  const theirs = E.updateScene(base, 's2', (s) => {
    s.title = 'Theirs';
    s.subtitle = 'x';
  });
  const merged = mergeDrafts(base, mine, theirs);
  assert.equal(merged.conflicts.length, 1);
  assert.equal(merged.conflicts[0].key, 's2');
  assert.equal(E.findScene(merged.screenplay, 's2').title, 'Mine');
  assert.equal(E.findScene(merged.screenplay, 's2').subtitle, null, 'the whole scene is mine, as before');
});

test('merge: beat lists merge by id unless both sides reshaped them or a removed beat was edited', () => {
  const base = E.findScene(sampleScreenplay(), 's2');
  // I add a beat at the end; they edit beat 1: both kept
  const mine = E.addBeat(base, 2, 'main', { narration: 'Extra.' }).scene;
  const theirs = { ...base, beats: base.beats.map((b, i) => (i === 0 ? { ...b, narration: 'Edited by them.' } : b)) };
  const s = mergeScene(base, mine, theirs);
  assert.ok(s);
  assert.deepEqual(s.beats.map((b) => b.narration), ['Edited by them.', 'Here is the formula.', 'Remember it.', 'Extra.']);
  // I remove beat 3; they edited it: a real conflict
  const removed = E.removeBeat(base, 2);
  const edited3 = { ...base, beats: base.beats.map((b, i) => (i === 2 ? { ...b, narration: 'Changed.' } : b)) };
  assert.equal(mergeScene(base, removed, edited3), null);
  // both reorder differently: conflict
  assert.equal(mergeScene(base, E.moveBeat(base, 0, 2), E.moveBeat(base, 1, 0)), null);
  // a changed chapter is never merged field by field
  assert.equal(mergeScene(base, { ...base, chapter_id: 'ch2' }, { ...base, title: 'x' }), null);
});

test('history and changes helpers', () => {
  assert.equal(HISTORY_LIMIT, 200);
  assert.equal(editLabel('scene:title'), 'Edit title');
  assert.equal(editLabel('beat:main:s2-b1:narration'), 'Edit narration');
  assert.equal(editLabel('beat:reveal:s4-b2:pause'), 'Change pause');
  assert.equal(editLabel('item:s2-i1:text'), 'Edit board');
  assert.equal(editLabel('chart:type'), 'Edit side panel');
  assert.equal(editLabel('quiz:question'), 'Edit quiz');
  assert.equal(editLabel(undefined), 'Edit scene');
  assert.deepEqual(fieldWords(['beats', 'narration', 'board', 'side_panel', 'some_new_field']), ['narration', 'board', 'side panel', 'some new field']);
  assert.deepEqual(changeBadge({ scene_id: 'a', status: 'edited', generated_index: 1, current_index: 1, fields_changed: ['title'], history: 0 }), { status: 'edited', label: 'Edited', title: 'Changed since Aadhi wrote it: title' });
  assert.equal(changeBadge({ scene_id: 'a', status: 'added', generated_index: null, current_index: 2, fields_changed: [], history: 0 }).label, 'New');
  assert.match(changeBadge({ scene_id: 'a', status: 'moved', generated_index: 3, current_index: 0, fields_changed: [], history: 0 }).title, /was scene 4/);
  assert.equal(changeBadge({ scene_id: 'a', status: 'unchanged', generated_index: 0, current_index: 0, fields_changed: [], history: 2 }), null);
  assert.equal(changeBadge(null), null);
  assert.equal(changesBySceneId({ available: false, scenes: [{ scene_id: 'a', status: 'edited' }] }).size, 0);
  assert.equal(changesBySceneId(null).size, 0);
  const all = [
    { scene_id: 'a', status: 'unchanged', generated_index: 0, current_index: 0, fields_changed: [], history: 0 },
    { scene_id: 'b', status: 'removed', generated_index: 1, current_index: null, fields_changed: [], history: 0 },
    { scene_id: 'c', status: 'moved', generated_index: 2, current_index: 3, fields_changed: [], history: 0 },
    { scene_id: 'n', status: 'added', generated_index: null, current_index: 1, fields_changed: [], history: 0 },
    { scene_id: 'z', status: 'removed', generated_index: 0, current_index: null, fields_changed: [], history: 0 },
  ];
  assert.equal(restorePosition(all, all[1]), 1, 'after scene a (generated before it, still there)');
  assert.equal(restorePosition(all, all[4]), 0, 'nothing before it: at the start');
});
