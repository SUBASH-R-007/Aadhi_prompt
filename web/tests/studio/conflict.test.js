import './_dom.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import { diffScreenplay, mergeScreenplays, hasChanges } from '../../js/studio/lib/conflict.js';
import * as E from '../../js/studio/lib/screenplayEdit.js';
import { sampleScreenplay } from './fixtures.js';

const ids = (sp) => sp.scenes.map((s) => s.id);
const scene = (sp, id) => sp.scenes.find((s) => s.id === id);
const editTitle = (sp, id, title) => E.updateScene(sp, id, (s) => ({ ...s, title }));

test('diffScreenplay classifies field and scene changes', () => {
  const base = sampleScreenplay();
  let next = editTitle(base, 's2', 'Changed');
  next = E.deleteScene(next, 0);
  next = E.addScene(next, 'content', 0).screenplay;
  next = { ...next, session_title: 'New title' };
  const d = diffScreenplay(base, next);
  assert.deepEqual(d.fields, ['chapters', 'session_title']);
  assert.deepEqual(d.removed, ['s1']);
  assert.deepEqual(d.modified, ['s2']);
  assert.equal(d.added.length, 1);
  assert.equal(d.reordered, false);
  assert.equal(diffScreenplay(base, E.moveScene(base, 0, 2)).reordered, true);
  assert.equal(hasChanges(base, sampleScreenplay()), false);
  assert.equal(hasChanges(base, next), true);
});

test('non-overlapping edits merge cleanly', () => {
  const base = sampleScreenplay();
  const mine = editTitle(base, 's2', 'Mine');
  const theirs = editTitle(base, 's3', 'Theirs');
  const { screenplay, conflicts } = mergeScreenplays(base, mine, theirs);
  assert.deepEqual(conflicts, []);
  assert.equal(scene(screenplay, 's2').title, 'Mine');
  assert.equal(scene(screenplay, 's3').title, 'Theirs');
  assert.deepEqual(ids(screenplay), ['s1', 's2', 's3', 's4']);
});

test('the same scene edited on both sides keeps mine and reports a conflict', () => {
  const base = sampleScreenplay();
  const mine = editTitle(base, 's2', 'Mine');
  const theirs = editTitle(base, 's2', 'Theirs');
  const { screenplay, conflicts } = mergeScreenplays(base, mine, theirs);
  assert.equal(scene(screenplay, 's2').title, 'Mine');
  assert.equal(conflicts.length, 1);
  assert.equal(conflicts[0].kind, 'scene');
  assert.equal(conflicts[0].key, 's2');
});

test('identical edits on both sides are not conflicts', () => {
  const base = sampleScreenplay();
  const mine = editTitle(base, 's2', 'Same');
  const theirs = editTitle(base, 's2', 'Same');
  assert.deepEqual(mergeScreenplays(base, mine, theirs).conflicts, []);
});

test('top-level fields: mine wins only where I changed them', () => {
  const base = sampleScreenplay();
  const mine = { ...base, session_title: 'Mine' };
  const theirs = { ...base, session_title: 'Theirs', unit_name: 'Their unit' };
  const { screenplay, conflicts } = mergeScreenplays(base, mine, theirs);
  assert.equal(screenplay.session_title, 'Mine');
  assert.equal(screenplay.unit_name, 'Their unit');
  assert.equal(conflicts.length, 1);
  assert.equal(conflicts[0].key, 'session_title');
});

test('scenes added on both sides are kept, each after its predecessor', () => {
  const base = sampleScreenplay();
  const mineAdd = E.addScene(base, 'content', 1); // after s2
  const theirAdd = E.addScene(base, 'summary', 2, { title: 'T' }); // after s3 -> same generated id!
  // Rename theirs to avoid an id clash (the server would assign different ids).
  const theirs = E.updateScene(theirAdd.screenplay, theirAdd.scene.id, (s) => ({ ...s, id: 'srv1' }));
  theirs.chapters = theirs.chapters.map((c) => ({ ...c, scene_ids: c.scene_ids.map((x) => (x === theirAdd.scene.id ? 'srv1' : x)) }));
  const { screenplay, conflicts } = mergeScreenplays(base, mineAdd.screenplay, theirs);
  assert.deepEqual(conflicts, []);
  assert.deepEqual(ids(screenplay), ['s1', 's2', mineAdd.scene.id, 's3', 'srv1', 's4']);
  // My new scene stays in my chapter even though chapters came from theirs.
  assert.ok(screenplay.chapters[0].scene_ids.includes(mineAdd.scene.id));
  assert.ok(screenplay.chapters[1].scene_ids.includes('srv1'));
});

test('deletions: mine removes, theirs removes, and edit-vs-delete conflicts', () => {
  const base = sampleScreenplay();
  // I delete s1, they delete s3.
  let r = mergeScreenplays(base, E.deleteScene(base, 0), E.deleteScene(base, 2));
  assert.deepEqual(ids(r.screenplay), ['s2', 's4']);
  assert.deepEqual(r.conflicts, []);
  assert.deepEqual(r.screenplay.chapters[1].scene_ids, ['s4']);
  // I edit s3 but they deleted it -> restored with a conflict.
  r = mergeScreenplays(base, editTitle(base, 's3', 'Mine'), E.deleteScene(base, 2));
  assert.deepEqual(ids(r.screenplay), ['s1', 's2', 's3', 's4']);
  assert.equal(r.conflicts.length, 1);
  assert.match(r.conflicts[0].message, /restored/);
  // I delete s3 but they edited it -> stays deleted with a conflict.
  r = mergeScreenplays(base, E.deleteScene(base, 2), editTitle(base, 's3', 'Theirs'));
  assert.deepEqual(ids(r.screenplay), ['s1', 's2', 's4']);
  assert.equal(r.conflicts.length, 1);
  assert.match(r.conflicts[0].message, /stays deleted/);
});

test('reorders: mine wins when I reordered, theirs otherwise', () => {
  const base = sampleScreenplay();
  const mineMoved = E.moveScene(base, 3, 0); // s4 first
  const theirsEdited = editTitle(base, 's2', 'Theirs');
  let r = mergeScreenplays(base, mineMoved, theirsEdited);
  assert.deepEqual(ids(r.screenplay), ['s4', 's1', 's2', 's3']);
  assert.equal(scene(r.screenplay, 's2').title, 'Theirs');
  const theirsMoved = E.moveScene(base, 0, 3); // s1 last
  r = mergeScreenplays(base, editTitle(base, 's3', 'Mine'), theirsMoved);
  assert.deepEqual(ids(r.screenplay), ['s2', 's3', 's4', 's1']);
  assert.equal(scene(r.screenplay, 's3').title, 'Mine');
});

test('merged result has valid cross references', () => {
  const base = sampleScreenplay();
  // They removed objective obj-2 and misconception m1; my edited scenes still reference them.
  const theirs = { ...base, learning_objectives: base.learning_objectives.slice(0, 1), misconceptions: [{ id: 'm2', concept_id: null, statement: 'x', correction: 'y' }] };
  const mine = editTitle(editTitle(base, 's3', 'Mine'), 's4', 'Mine too');
  const { screenplay } = mergeScreenplays(base, mine, theirs);
  assert.deepEqual(scene(screenplay, 's3').objective_ids, []);
  assert.deepEqual(scene(screenplay, 's4').option_misconception_ids, [null, null, null]);
  for (const ch of screenplay.chapters) for (const id of ch.scene_ids) assert.ok(ids(screenplay).includes(id));
});

test('merge never mutates its inputs', () => {
  const base = sampleScreenplay();
  const mine = editTitle(base, 's2', 'Mine');
  const theirs = E.deleteScene(base, 0);
  const snap = JSON.stringify([base, mine, theirs]);
  mergeScreenplays(base, mine, theirs);
  assert.equal(JSON.stringify([base, mine, theirs]), snap);
});
