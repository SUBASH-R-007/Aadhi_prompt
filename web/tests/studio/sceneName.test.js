// @ts-check
// How the Studio names the scene of a render or preflight item (web/js/studio/lib/sceneName.js): the editor's number,
// and Aadhi's synthesised intro / recap by name, never by the timeline position (step-5 review R5-3).
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { APPEARANCE_NAMES, APPEARANCE_RE, appearanceName, sceneNumberOf } from '../../js/studio/lib/sceneName.js';

test('a screenplay scene: the server\'s scene_number, else (older items) the timeline position', () => {
  assert.equal(sceneNumberOf({ scene_id: 's2', scene_index: 2, scene_number: 3 }), 3);
  assert.equal(sceneNumberOf({ scene_id: 's2', scene_index: 1 }), 2);
  assert.equal(sceneNumberOf({ scene_id: null, scene_index: null }), null);
  assert.equal(appearanceName({ scene_id: 's2', scene_index: 1 }), null);
});

test('Aadhi\'s synthesised intro and recap are named, never "Scene 1" (the teacher\'s first scene)', () => {
  // renders.numbered gives a synthesised appearance scene_number null: it is not in the screenplay
  const intro = { scene_id: 'aadhi-intro', scene_index: 0, scene_number: null };
  assert.equal(sceneNumberOf(intro), null, 'not the timeline position: scene_index 0 + 1 is the teacher\'s scene 1');
  assert.equal(appearanceName(intro), "Aadhi's intro");
  assert.equal(appearanceName({ scene_id: 'aadhi-recap', scene_index: 4 }), "Aadhi's recap");
  assert.equal(appearanceName({ scene_id: 'aadhi-intro-2', scene_index: 0 }), "Aadhi's intro", 'the id made unique');
  // a screenplay scene that happens to have such an id is numbered by the server: it is the teacher's
  const own = { scene_id: 'aadhi-intro', scene_index: 0, scene_number: 1 };
  assert.equal(appearanceName(own), null);
  assert.equal(sceneNumberOf(own), 1);
  assert.equal(appearanceName({ scene_id: 'aadhi-intros', scene_index: 0 }), null);
});

test('the names and the id form are presenter.py\'s', () => {
  assert.deepEqual({ ...APPEARANCE_NAMES }, { intro: "Aadhi's intro", recap: "Aadhi's recap" });
  for (const id of ['aadhi-intro', 'aadhi-recap', 'aadhi-intro-3']) assert.ok(APPEARANCE_RE.test(id), id);
  for (const id of ['intro', 'aadhi-intro-x', 'xaadhi-intro']) assert.ok(!APPEARANCE_RE.test(id), id);
});
