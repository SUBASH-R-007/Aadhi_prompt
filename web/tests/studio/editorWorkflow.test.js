// Editor workflow (batch 4): labelled undo, the save state with Retry / Review, automatic saving (off by
// default), "Save as a copy", shortcuts, skipping a scene and holding it longer, splitting at a beat, the
// comparison with what Aadhi generated (markers, revert, restore), the job lock with its poll, the timeline
// strip inside the editor, a picture scene from the library and the follow-playback rule.
import { resetDom, mockFetch, tick, window } from './_dom.js';
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { mount, shouldFollow, AUTOSAVE_MS, FOLLOW_QUIET_MS, JOB_POLL_MS } from '../../js/studio/views/editor/editor.js';
import * as E from '../../js/studio/lib/screenplayEdit.js';
import { draftKey } from '../../js/studio/lib/draftStore.js';
import { closeAllModals } from '../../js/studio/components/modal.js';
import { sampleScreenplay, sampleVersion } from './fixtures.js';
import { fakeApp, byText, clickModal, typeInto, waitFor, jsonBody, deferred } from './_views.js';

const VID = 9;
const PID = 5;

/** Editors mounted by the current test (destroyed before the next one, even after a failure). */
const live = [];

function freshTest() {
  for (const h of live.splice(0)) {
    try {
      h.destroy();
    } catch {
      /* already destroyed */
    }
  }
  resetDom();
  closeAllModals();
  localStorage.clear();
}
after(() => freshTest());

/**
 * Mock the editor's endpoints; `puts` collects PUT /screenplay bodies, the revision follows the saves.
 * @param {any} version
 * @param {Record<string, any>} [extra]
 */
function editorRoutes(version, extra = {}) {
  const puts = [];
  const state = { revision: version.revision, screenplay: version.screenplay };
  const calls = mockFetch({
    [`GET /api/versions/${VID}`]: () => ({ ...version, revision: state.revision, screenplay: state.screenplay }),
    [`POST /api/versions/${VID}/lint`]: { issues: [] },
    [`POST /api/versions/${VID}/timeline/preview`]: { status: 404, body: { detail: 'not built', code: 'not_found' } },
    [`GET /api/jobs?project_id=${PID}&limit=20`]: { items: [], total: 0 },
    [`PUT /api/versions/${VID}/screenplay`]: ({ init }) => {
      const body = JSON.parse(init.body);
      puts.push(body);
      state.revision += 1;
      state.screenplay = body.screenplay;
      return { version: { ...version, screenplay: undefined, revision: state.revision }, issues: [], stale_scenes: [] };
    },
    ...extra,
  });
  return { calls, puts, state };
}

async function mountEditor(app, query = {}) {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const handle = await mount(container, { app, params: { id: PID, vid: VID }, query, signal: undefined });
  if (handle) live.push(handle);
  return { container, handle };
}

const inspectorScene = (container) => container.querySelector('.inspector').dataset.sceneId;
const status = (container) => container.querySelector('.save-status');
/** @param {Element} el @param {string} key */
const keydown = (el, key, opts = {}) => el.dispatchEvent(new window.KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...opts }));

test('follow rule: the selection follows playback only while playing, never while typing or just after an edit', () => {
  const base = { playing: true, sceneId: 's3', selectedId: 's2', known: true, typing: false, sinceEdit: FOLLOW_QUIET_MS };
  assert.equal(shouldFollow(base), true);
  assert.equal(shouldFollow({ ...base, playing: false }), false, 'paused: never');
  assert.equal(shouldFollow({ ...base, typing: true }), false);
  assert.equal(shouldFollow({ ...base, sinceEdit: FOLLOW_QUIET_MS - 1 }), false);
  assert.equal(shouldFollow({ ...base, sceneId: 's2' }), false, 'already selected');
  assert.equal(shouldFollow({ ...base, known: false }), false, 'not a scene of the draft');
  assert.equal(shouldFollow({ ...base, sceneId: null }), false, 'the intro');
  assert.equal(AUTOSAVE_MS, 3000);
  assert.equal(JOB_POLL_MS, 15000);
});

test('undo and redo say what they undo; structural edits are named too', async () => {
  freshTest();
  editorRoutes(sampleVersion());
  const app = fakeApp();
  const { container } = await mountEditor(app, { scene: 's2' });
  const undoBtn = container.querySelector('button[aria-label="Undo"]');
  const redoBtn = container.querySelector('button[aria-label="Redo"]');
  assert.equal(undoBtn.title, 'Undo (Ctrl+Z)');
  typeInto(container.querySelector('[data-fk="scene:title"]'), 'Ohm, again');
  assert.equal(undoBtn.title, 'Undo: Edit title in scene 2 (Ctrl+Z)');
  const desc = document.getElementById(undoBtn.getAttribute('aria-describedby'));
  assert.equal(desc.textContent, 'Edit title in scene 2', 'the button keeps its name; what it undoes is its description');
  typeInto(container.querySelector('[data-fk="beat:main:s2-b1:narration"]'), 'Voltage pushes charges.');
  assert.equal(undoBtn.title, 'Undo: Edit narration in scene 2 (Ctrl+Z)');
  undoBtn.click();
  assert.ok(app.rec.toasts.some((t) => t.message === 'Undone: Edit narration in scene 2.'));
  assert.equal(redoBtn.title, 'Redo: Edit narration in scene 2 (Ctrl+Shift+Z)');
  assert.equal(undoBtn.title, 'Undo: Edit title in scene 2 (Ctrl+Z)');
  byText(container.querySelector('.inspector'), 'Duplicate').click();
  await tick();
  assert.equal(undoBtn.title, 'Undo: Duplicate scene 2 (Ctrl+Z)');
  assert.equal(redoBtn.title, 'Redo (Ctrl+Shift+Z)', 'a new edit clears the redo history');
  byText(container.querySelector('.inspector'), 'Add beat').click();
  assert.match(undoBtn.title, /^Undo: Add beat in scene 3 \(Ctrl\+Z\)$/);
});

test('save state: a failed save stays visible with Retry until a save works', async () => {
  freshTest();
  let fail = true;
  const { puts } = editorRoutes(sampleVersion(), {});
  const okPut = globalThis.fetch;
  globalThis.fetch = async (url, init = {}) => {
    if ((init.method || 'GET') === 'PUT' && fail) {
      fail = false;
      return okPut('/__fail__', init);
    }
    return okPut(url, init);
  };
  const app = fakeApp();
  const { container } = await mountEditor(app, { scene: 's2' });
  assert.match(status(container).textContent, /All changes saved/);
  assert.equal(status(container).dataset.state, 'saved');
  typeInto(container.querySelector('[data-fk="scene:title"]'), 'Retry me');
  assert.equal(status(container).dataset.state, 'dirty');
  byText(container, 'Save').click();
  await waitFor(() => status(container).dataset.state === 'error');
  assert.match(status(container).textContent, /Couldn’t save\. Your changes are kept in this browser\./);
  const retry = byText(container.querySelector('.save-actions'), 'Retry');
  assert.equal(retry.hidden, false);
  assert.equal(container.querySelector('.save-actions').hidden, false);
  assert.equal(app.rec.errors[0], 'Saving failed. Your changes are kept locally.');
  retry.click();
  await waitFor(() => puts.length === 1);
  await waitFor(() => status(container).dataset.state === 'saved');
  assert.equal(container.querySelector('.save-actions').hidden, true);
  assert.equal(E.findScene(puts[0].screenplay, 's2').title, 'Retry me');
});

test('automatic saving (when switched on): quiet saves after the last change, never while the draft is invalid', async () => {
  freshTest();
  localStorage.setItem('aadhi.studio.pref.editorAutosave.u7', 'true');
  const { puts } = editorRoutes(sampleVersion());
  const app = fakeApp();
  const { container } = await mountEditor(app, { scene: 's2' });
  assert.equal(container.querySelector('[data-fk="editor:autosave"]').checked, true);
  typeInto(container.querySelector('[data-fk="scene:title"]'), 'Saved by itself');
  assert.match(status(container).textContent, /Unsaved changes: saving automatically/);
  await tick(AUTOSAVE_MS - 600);
  assert.equal(puts.length, 0, 'it waits for a quiet moment');
  await waitFor(() => puts.length === 1, 3000);
  assert.equal(E.findScene(puts[0].screenplay, 's2').title, 'Saved by itself');
  await waitFor(() => status(container).dataset.state === 'saved');
  assert.equal(app.rec.toasts.filter((t) => t.message === 'Saved.').length, 0, 'no toast for an automatic save');
  // an invalid draft (empty narration) is never sent
  typeInto(container.querySelector('[data-fk="beat:main:s2-b1:narration"]'), '');
  assert.match(status(container).textContent, /fix the errors to save/);
  await tick(AUTOSAVE_MS + 400);
  assert.equal(puts.length, 1);
});

test('automatic saving is off by default; switched on, a conflict only changes the save state (Review, no dialog)', async () => {
  freshTest();
  const puts = [];
  editorRoutes(sampleVersion(), {
    [`PUT /api/versions/${VID}/screenplay`]: ({ init }) => {
      puts.push(JSON.parse(init.body));
      return { status: 409, body: { detail: 'Revision conflict', code: 'revision_conflict', current_revision: 6 } };
    },
  });
  const app = fakeApp();
  const { container } = await mountEditor(app, { scene: 's2' });
  const toggle = container.querySelector('[data-fk="editor:autosave"]');
  assert.equal(toggle.checked, false, 'off by default');
  typeInto(container.querySelector('[data-fk="scene:title"]'), 'Not saved by itself');
  await tick(AUTOSAVE_MS + 400);
  assert.equal(puts.length, 0);
  toggle.click();
  assert.equal(localStorage.getItem('aadhi.studio.pref.editorAutosave.u7'), 'true', 'remembered per user in this browser');
  await waitFor(() => puts.length === 1, AUTOSAVE_MS + 1500);
  await waitFor(() => status(container).dataset.state === 'conflict');
  assert.match(status(container).textContent, /Changed elsewhere/);
  assert.equal(document.querySelector('.modal'), null, 'no dialog while the teacher types');
  const review = byText(container.querySelector('.save-actions'), 'Review');
  assert.equal(review.hidden, false);
  typeInto(container.querySelector('[data-fk="scene:title"]'), 'Still mine');
  await tick(AUTOSAVE_MS + 400);
  assert.equal(puts.length, 1, 'no more automatic saves while it conflicts');
  review.click();
  await waitFor(() => document.querySelector('.modal'));
  assert.match(document.querySelector('.modal').textContent, /changed elsewhere/);
  clickModal('Cancel');
  await tick();
  assert.equal(status(container).dataset.state, 'conflict');
});

test('save as a copy: the copy gets the unsaved changes and opens; this version stays as last saved', async () => {
  freshTest();
  const { calls, puts } = editorRoutes(sampleVersion(), {
    [`POST /api/versions/${VID}/duplicate`]: { status: 201, body: { version: { ...sampleVersion(), id: 12, number: 2, revision: 1, screenplay: undefined } } },
    'PUT /api/versions/12/screenplay': ({ init }) => ({ version: { id: 12, revision: 2 }, issues: [], stale_scenes: [], _body: JSON.parse(init.body) }),
  });
  const app = fakeApp();
  const { container } = await mountEditor(app, { scene: 's2' });
  typeInto(container.querySelector('[data-fk="scene:title"]'), 'Only in the copy');
  await tick(900); // the local draft is stored
  assert.ok(localStorage.getItem(draftKey(7, VID)));
  byText(container, 'Save as a copy').click();
  await waitFor(() => document.querySelector('.modal'));
  const modal = document.querySelector('.modal');
  assert.match(modal.textContent, /The copy gets your unsaved changes/);
  assert.equal(modal.querySelector('input').value, 'Copy of v1');
  clickModal('Save copy');
  await waitFor(() => app.rec.navs.length === 1);
  assert.equal(app.rec.navs[0], `#/p/${PID}/v/12/edit`);
  const dup = calls.find((c) => c.path === `/api/versions/${VID}/duplicate`);
  assert.deepEqual(jsonBody(dup), { label: 'Copy of v1' });
  const intoCopy = calls.find((c) => c.method === 'PUT' && c.path === '/api/versions/12/screenplay');
  assert.equal(jsonBody(intoCopy).revision, 1);
  assert.equal(E.findScene(jsonBody(intoCopy).screenplay, 's2').title, 'Only in the copy');
  assert.equal(puts.length, 0, 'this version is not saved');
  assert.equal(container.querySelector('.editor').dataset.dirty, 'false', 'no leave question');
  assert.equal(localStorage.getItem(draftKey(7, VID)), null, 'the changes moved to the copy');
  assert.equal(await app.rec.guard(null), true);
});

test('shortcuts: "?" lists them, [ and ] change scene, Delete asks first; never while typing', async () => {
  freshTest();
  editorRoutes(sampleVersion());
  const app = fakeApp();
  const { container } = await mountEditor(app, { scene: 's2' });
  keydown(document.body, '?');
  await waitFor(() => document.querySelector('.modal'));
  assert.match(document.querySelector('.modal').textContent, /Keyboard shortcuts/);
  assert.match(document.querySelector('.modal').textContent, /Delete the selected scene \(asks first\)/);
  closeAllModals();
  // typing "?" in a field is text
  const title = container.querySelector('[data-fk="scene:title"]');
  title.focus();
  keydown(title, '?');
  keydown(title, ']');
  await tick();
  assert.equal(document.querySelector('.modal'), null);
  assert.equal(inspectorScene(container), 's2');
  keydown(document.body, ']');
  assert.equal(inspectorScene(container), 's3');
  keydown(container.querySelector('.scene-select[data-scene-id="s3"]'), '[');
  assert.equal(inspectorScene(container), 's2');
  // the toolbar button opens the list too
  container.querySelector('button[aria-label="Keyboard shortcuts"]').click();
  await waitFor(() => document.querySelector('.modal'));
  closeAllModals();
  keydown(document.body, 'Delete');
  await waitFor(() => document.querySelector('.modal'));
  assert.match(document.querySelector('.modal').textContent, /Delete scene\?/);
  clickModal('Delete scene');
  await waitFor(() => container.querySelectorAll('.scene-select').length === 3);
  assert.equal(container.querySelector('.scene-select[data-scene-id="s2"]'), null);
  assert.match(container.querySelector('button[aria-label="Undo"]').title, /^Undo: Delete scene 2 /);
});

test('skip a scene in the video and hold one longer: fields left out at their defaults', async () => {
  freshTest();
  const { puts } = editorRoutes(sampleVersion());
  const app = fakeApp();
  const { container } = await mountEditor(app, { scene: 's3' });
  const skip = () => container.querySelector('[data-fk="scene:hidden"]');
  assert.equal(skip().checked, false);
  assert.equal(skip().disabled, false);
  skip().click();
  const row = container.querySelector('.scene-select[data-scene-id="s3"]');
  assert.ok(row.classList.contains('is-hidden'));
  assert.match(row.textContent, /Skipped/);
  assert.match(container.querySelector('.scene-pane .pane-head').textContent, /4 scenes \(1 skipped\)/);
  assert.match(container.querySelector('button[aria-label="Undo"]').title, /Skip scene in the video in scene 3/);
  // hold for at least 12 s
  const hold = () => container.querySelector('[data-fk="scene:min_seconds"]');
  typeInto(hold(), '12');
  typeInto(hold(), '0');
  const err = hold().closest('.field-wrap').querySelector('.field-error');
  assert.equal(err.hidden, false);
  assert.match(err.textContent, /from 1 to 600/);
  byText(container, 'Save').click();
  await waitFor(() => puts.length === 1);
  await waitFor(() => status(container).dataset.state === 'saved');
  let s3 = E.findScene(puts[0].screenplay, 's3');
  assert.equal(s3.hidden, true);
  assert.equal(s3.min_seconds, 12, 'an invalid entry is not kept');
  assert.equal('hidden' in E.findScene(puts[0].screenplay, 's2'), false, 'other scenes are untouched');
  // back to the defaults: both keys are removed (not false / null)
  skip().click();
  byText(container.querySelector('.timing-section'), 'Clear').click();
  assert.equal(hold().value, '');
  byText(container, 'Save').click();
  await waitFor(() => puts.length === 2);
  s3 = E.findScene(puts[1].screenplay, 's3');
  assert.equal('hidden' in s3, false);
  assert.equal('min_seconds' in s3, false);
  assert.deepEqual(s3, E.findScene(sampleScreenplay(), 's3'), 'byte-identical to the original scene');
});

test('the last scene that plays cannot be skipped; a held scene shows its longer time in the list', async () => {
  freshTest();
  let sp = sampleScreenplay();
  for (const id of ['s1', 's2', 's3']) {
    sp = E.updateScene(sp, id, (s) => {
      s.hidden = true;
    });
  }
  sp = E.updateScene(sp, 's4', (s) => {
    s.min_seconds = 75;
  });
  editorRoutes(sampleVersion({ screenplay: sp }));
  const { container } = await mountEditor(fakeApp(), { scene: 's4' });
  const skip = container.querySelector('[data-fk="scene:hidden"]');
  assert.equal(skip.disabled, true);
  assert.match(skip.closest('.field-wrap').textContent, /only scene that plays/);
  assert.equal(container.querySelector('[data-fk="scene:min_seconds"]').value, '75');
  assert.match(container.querySelector('.scene-select[data-scene-id="s4"] .scene-type').textContent, /1:15/);
  assert.match(container.querySelector('.scene-pane .pane-head').textContent, /4 scenes \(3 skipped\) · ≈ 1:15/);
});

test('split a scene at a beat: one undoable edit; the new part is selected; never inside a quiz', async () => {
  freshTest();
  const { puts } = editorRoutes(sampleVersion());
  const app = fakeApp();
  const { container } = await mountEditor(app, { scene: 's2' });
  const beats = [...container.querySelectorAll('.beat[data-beat-phase="main"]')];
  assert.equal(beats[0].querySelector('.beat-split'), null, 'not before the first beat');
  beats[1].querySelector('.beat-split').click();
  await tick();
  assert.equal(inspectorScene(container), 's2-2');
  assert.equal(container.querySelectorAll('.scene-select').length, 5);
  assert.match(container.querySelector('button[aria-label="Undo"]').title, /^Undo: Split scene 2 /);
  assert.ok(app.rec.toasts.some((t) => /Scene 2 was split/.test(t.message)));
  byText(container, 'Save').click();
  await waitFor(() => puts.length === 1);
  const saved = puts[0].screenplay;
  assert.deepEqual(saved.scenes.map((s) => s.id), ['s1', 's2', 's2-2', 's3', 's4']);
  assert.deepEqual(E.findScene(saved, 's2-2').beats.map((b) => b.narration), ['Here is the formula.', 'Remember it.']);
  container.querySelector('.scene-select[data-scene-id="s4"]').click();
  assert.equal(container.querySelectorAll('.beat-split').length, 0, 'a quiz is never split');
});

test('changes since Aadhi wrote it: markers, the list, revert (asked first) and restore of a removed scene', async () => {
  freshTest();
  const changes = {
    available: true,
    scenes: [
      { scene_id: 's1', status: 'unchanged', generated_index: 0, current_index: 0, fields_changed: [], history: 0 },
      { scene_id: 's2', status: 'edited', generated_index: 1, current_index: 1, fields_changed: ['beats', 'title'], history: 1 },
      { scene_id: 'gone', status: 'removed', generated_index: 2, current_index: null, fields_changed: [], history: 0 },
      { scene_id: 's3', status: 'moved', generated_index: 4, current_index: 2, fields_changed: [], history: 0 },
      { scene_id: 's4', status: 'added', generated_index: null, current_index: 3, fields_changed: [], history: 0 },
    ],
  };
  const reverts = [];
  const { calls, state } = editorRoutes(sampleVersion(), {
    [`GET /api/versions/${VID}/changes`]: changes,
    're:^POST /api/versions/9/scenes/[^/]+/revert$': ({ path, init }) => {
      reverts.push({ path, body: JSON.parse(init.body) });
      state.revision += 1;
      return { revision: state.revision, scene_id: path.split('/')[5] };
    },
  });
  const app = fakeApp();
  const { container } = await mountEditor(app, { scene: 's2' });
  await waitFor(() => container.querySelector('.badge-change'));
  const badge = (id) => container.querySelector(`.scene-select[data-scene-id="${id}"] .badge-change`);
  assert.equal(badge('s1'), null);
  assert.equal(badge('s2').textContent, 'Edited');
  assert.equal(badge('s2').title, 'Changed since Aadhi wrote it: narration, title');
  assert.equal(badge('s3').textContent, 'Moved');
  assert.equal(badge('s4').textContent, 'New');
  const panel = container.querySelector('.changes-panel');
  assert.equal(panel.hidden, false);
  assert.equal(panel.querySelector('summary').textContent, 'Changes since Aadhi wrote it (1 edited, 1 new, 1 moved, 1 removed)');
  assert.match(container.querySelector('.change-line').textContent, /Edited since Aadhi wrote it: narration, title\./);
  // revert: asked first, with the version to go back to
  byText(container.querySelector('.change-line'), 'Compare and revert').click();
  await waitFor(() => document.querySelector('.modal'));
  const modal = document.querySelector('.modal');
  assert.match(modal.textContent, /Revert scene 2\?/);
  assert.match(modal.textContent, /These parts go back: narration, title\./);
  const options = [...modal.querySelectorAll('input[type="radio"]')];
  assert.deepEqual(options.map((o) => o.dataset.revertTo), ['generated', 'history:0']);
  assert.equal(options[0].checked, true);
  clickModal('Revert scene');
  await waitFor(() => reverts.length === 1);
  assert.equal(reverts[0].path, `/api/versions/${VID}/scenes/s2/revert`);
  assert.deepEqual(reverts[0].body, { revision: 4, to: 'generated' });
  await waitFor(() => app.rec.toasts.some((t) => t.message === 'Scene 2 was reverted. Undo with Ctrl+Z.'));
  assert.equal(container.querySelector('button[aria-label="Undo"]').title, 'Undo: Revert scene (Ctrl+Z)');
  // the earlier version (before the last regeneration)
  byText(container.querySelector('.change-line'), 'Compare and revert').click();
  await waitFor(() => document.querySelector('.modal'));
  const second = [...document.querySelectorAll('.modal input[type="radio"]')][1];
  second.checked = true;
  second.dispatchEvent(new window.Event('change', { bubbles: true }));
  clickModal('Revert scene');
  await waitFor(() => reverts.length === 2);
  assert.deepEqual(reverts[1].body, { revision: 5, to: 'history', history_index: 0 });
  // restore the removed scene after the nearest scene that came before it
  panel.querySelector('details').open = true;
  await waitFor(() => !document.querySelector('.modal'));
  byText(panel, 'Restore').click();
  await waitFor(() => document.querySelector('.modal'));
  assert.match(document.querySelector('.modal').textContent, /comes back as scene 3/);
  clickModal('Restore scene');
  await waitFor(() => reverts.length === 3);
  assert.equal(reverts[2].path, `/api/versions/${VID}/scenes/gone/revert`);
  assert.deepEqual(reverts[2].body, { revision: 6, to: 'generated', position: 2 });
  await waitFor(() => calls.filter((c) => c.path === `/api/versions/${VID}/changes`).length >= 2); // the comparison is loaded again
});

test('revert to an earlier version: history_index 0 is the scene before the last regeneration (newest first)', async () => {
  freshTest();
  const changes = {
    available: true,
    scenes: [{ scene_id: 's2', status: 'edited', generated_index: 1, current_index: 1, fields_changed: ['title'], history: 2 }],
  };
  const reverts = [];
  const { state } = editorRoutes(sampleVersion(), {
    [`GET /api/versions/${VID}/changes`]: changes,
    're:^POST /api/versions/9/scenes/[^/]+/revert$': ({ path, init }) => {
      reverts.push(JSON.parse(init.body));
      state.revision += 1;
      return { revision: state.revision, scene_id: path.split('/')[5] };
    },
  });
  const app = fakeApp();
  const { container } = await mountEditor(app, { scene: 's2' });
  await waitFor(() => container.querySelector('.change-line'));
  byText(container.querySelector('.change-line'), 'Compare and revert').click();
  await waitFor(() => document.querySelector('.modal'));
  const labels = [...document.querySelectorAll('.modal .revert-option')].map((l) => l.textContent);
  const options = [...document.querySelectorAll('.modal input[type="radio"]')];
  assert.deepEqual(options.map((o) => o.dataset.revertTo), ['generated', 'history:0', 'history:1']);
  assert.deepEqual(labels, ['As Aadhi wrote it', 'As it was before the last regeneration', 'As it was before regeneration 1']);
  options[2].checked = true;
  options[2].dispatchEvent(new window.Event('change', { bubbles: true }));
  clickModal('Revert scene');
  await waitFor(() => reverts.length === 1);
  assert.deepEqual(reverts[0], { revision: 4, to: 'history', history_index: 1 }, 'the first regeneration is the oldest entry');
});

test('changes: unavailable or unknown to the server: no markers, no errors', async () => {
  freshTest();
  editorRoutes(sampleVersion(), { [`GET /api/versions/${VID}/changes`]: { available: false, scenes: [] } });
  const app = fakeApp();
  const { container } = await mountEditor(app, { scene: 's2' });
  await waitFor(() => !container.querySelector('.changes-panel').hidden);
  assert.match(container.querySelector('.changes-panel').textContent, /Not available for this version/);
  assert.equal(container.querySelector('.badge-change'), null);
  assert.equal(container.querySelector('.change-line'), null);
  freshTest();
  editorRoutes(sampleVersion()); // 404
  const other = await mountEditor(fakeApp(), { scene: 's2' });
  await tick(20);
  assert.equal(other.container.querySelector('.changes-panel').hidden, true);
  assert.equal(other.container.querySelector('.badge-change'), null);
});

test('a running job that writes the version locks editing; when it finishes, editing comes back', async () => {
  freshTest();
  let jobStatus = 'running';
  const job = () => ({ id: 77, kind: 'build_assets', status: jobStatus, stage: 'tts', progress: 0.4, project_id: PID, version_id: VID, created_at: '2026-10-01T10:00:00Z' });
  const { calls } = editorRoutes(sampleVersion(), {
    [`GET /api/jobs?project_id=${PID}&limit=20`]: () => ({ items: jobStatus === 'running' ? [job()] : [], total: 1 }),
    'GET /api/jobs/77': () => job(),
    'GET /api/jobs/77/events': { items: [] },
  });
  const app = fakeApp();
  const { container } = await mountEditor(app, { scene: 's2' });
  await waitFor(() => !container.querySelector('.editor-banner').hidden);
  assert.match(container.querySelector('.editor-banner').textContent, /Editing waits until it finishes/);
  assert.ok(container.querySelector('.scene-pane').hasAttribute('inert'));
  assert.ok(container.querySelector('.inspector-host').hasAttribute('inert'));
  assert.ok(container.querySelector('.editor').classList.contains('is-locked'));
  assert.equal(status(container).dataset.state, 'busy');
  assert.equal(byText(container, 'Save').disabled, true);
  typeInto(container.querySelector('[data-fk="scene:title"]'), 'Typed during the build');
  assert.equal(container.querySelector('.editor').dataset.dirty, 'false', 'the edit waits');
  assert.ok(app.rec.toasts.some((t) => /Editing waits until the running job finishes/.test(t.message)));
  keydown(document.body, ']');
  jobStatus = 'succeeded';
  await waitFor(() => !container.querySelector('.scene-pane').hasAttribute('inert'), 6000);
  assert.equal(container.querySelector('.editor').classList.contains('is-locked'), false);
  await waitFor(() => calls.filter((c) => c.path === `/api/versions/${VID}`).length >= 2, 3000);
  typeInto(container.querySelector('[data-fk="scene:title"]'), 'Typed after the build');
  assert.equal(container.querySelector('.editor').dataset.dirty, 'true');
});

test('a save refused because a job started meanwhile finds that job and waits for it', async () => {
  freshTest();
  let started = false;
  const job = { id: 78, kind: 'regenerate_scene', status: 'running', stage: 'script', progress: 0.2, project_id: PID, version_id: VID, created_at: '2026-10-01T10:00:00Z' };
  const { calls } = editorRoutes(sampleVersion(), {
    [`GET /api/jobs?project_id=${PID}&limit=20`]: () => ({ items: started ? [job] : [], total: started ? 1 : 0 }),
    'GET /api/jobs/78': job,
    [`PUT /api/versions/${VID}/screenplay`]: () => {
      started = true;
      return { status: 409, body: { detail: 'busy', code: 'version_busy', job_id: 78 } };
    },
  });
  const app = fakeApp();
  const { container } = await mountEditor(app, { scene: 's2' });
  typeInto(container.querySelector('[data-fk="scene:title"]'), 'x');
  byText(container, 'Save').click();
  await waitFor(() => calls.filter((c) => c.path === `/api/jobs?project_id=${PID}&limit=20`).length === 2);
  await waitFor(() => !container.querySelector('.editor-banner').hidden);
  assert.equal(status(container).dataset.state, 'busy');
  assert.match(status(container).textContent, /editing waits until it finishes\. Your changes are kept\./);
  assert.equal(container.querySelector('.editor').dataset.dirty, 'true', 'the unsaved change is kept');
  assert.ok(app.rec.toasts.some((t) => /A job is running on this version/.test(t.message)));
});

test('timeline strip in the editor: the scene list estimates until a preview exists; a block selects its scene', async () => {
  freshTest();
  editorRoutes(sampleVersion());
  const { container } = await mountEditor(fakeApp(), { scene: 's2' });
  const strip = container.querySelector('.tl-strip');
  assert.ok(strip, 'under the panes');
  await waitFor(() => strip.classList.contains('is-fallback'));
  const blocks = [...strip.querySelectorAll('.tl-block')];
  assert.deepEqual(blocks.map((b) => b.dataset.sceneId), ['s1', 's2', 's3', 's4']);
  assert.equal(strip.querySelector('.tl-block[data-scene-id="s2"]').getAttribute('aria-current'), 'true');
  blocks[2].click();
  assert.equal(inspectorScene(container), 's3');
  assert.equal(strip.querySelector('.tl-block[data-scene-id="s3"]').getAttribute('aria-current'), 'true');
  assert.ok(container.querySelector('.scene-select[data-scene-id="s3"]').classList.contains('selected'));
});

test('a picture scene from the library: a content scene showing the picture as a lecture figure', async () => {
  freshTest();
  const item = { id: 1, asset_key: 'upload/3f2a', kind: 'image', title: 'Resistor diagram', description: '', keywords: [], source: 'upload', width: 800, height: 600, duration_s: null, url: '/media/x.png', poster_url: null, created_at: '2026-10-01T10:00:00Z', updated_at: '2026-10-01T10:00:00Z', last_used_at: null, used_in: 0, prompt: null, provider: null, model: null };
  const { calls, puts } = editorRoutes(sampleVersion(), {
    'GET /api/library': { items: [item], total: 1 },
    'POST /api/library/1/attach': { asset_key: 'upload/3f2a' },
  });
  const app = fakeApp();
  const { container } = await mountEditor(app, { scene: 's2' });
  byText(container, 'Picture from my library').click();
  await waitFor(() => document.querySelector('[data-action="use"]'));
  const listCall = calls.find((c) => c.path.startsWith('/api/library?'));
  assert.equal(new URLSearchParams(listCall.path.split('?')[1]).get('source'), 'upload,figure', 'figures take uploads and document figures');
  document.querySelector('[data-action="use"]').click();
  await waitFor(() => container.querySelectorAll('.scene-select').length === 5);
  const newId = inspectorScene(container);
  assert.equal(container.querySelectorAll('.scene-select')[2].dataset.sceneId, newId, 'right after the selected scene');
  const beat = container.querySelector('.beat[data-beat-phase="main"] textarea');
  typeInto(beat, 'Here is the resistor in its circuit.');
  byText(container, 'Save').click();
  await waitFor(() => puts.length === 1);
  const saved = puts[0].screenplay;
  const fig = saved.figures.find((f) => f.asset_key === 'upload/3f2a');
  assert.ok(fig);
  assert.equal(fig.caption, 'Resistor diagram');
  const scene = E.findScene(saved, newId);
  assert.equal(scene.type, 'content');
  assert.equal(scene.title, 'Resistor diagram');
  assert.equal(scene.board.length, 1);
  assert.equal(scene.board[0].kind, 'figure');
  assert.equal(scene.board[0].figure_id, fig.id);
  assert.equal(scene.chapter_id, 'ch1');
});

test('inspector sections fold and stay folded across scenes; an issue jump unfolds the beats', async () => {
  freshTest();
  editorRoutes(sampleVersion(), {
    [`POST /api/versions/${VID}/lint`]: { issues: [{ code: 'beat.too_short', severity: 'warning', message: 'Beat 2 is short.', scene_id: 's3', beat_id: 's3-b2' }] },
  });
  const { container } = await mountEditor(fakeApp(), { scene: 's2' });
  const toggle = (name) => container.querySelector(`.inspector-section[aria-label="${name}"] .section-toggle`);
  const body = (name) => container.querySelector(`.inspector-section[aria-label="${name}"] .section-body`);
  assert.equal(container.querySelector('.inspector-section[aria-label="Scene"] .section-toggle'), null, 'the first section always stays open');
  for (const name of ['Timing and visibility', 'Board', 'Narration beats', 'Side panel']) {
    assert.equal(toggle(name).getAttribute('aria-expanded'), 'true', `${name} open by default`);
    assert.equal(toggle(name).getAttribute('aria-controls'), body(name).getAttribute('id'));
  }
  toggle('Board').click();
  toggle('Narration beats').click();
  assert.equal(body('Board').hidden, true);
  assert.equal(toggle('Board').getAttribute('aria-expanded'), 'false');
  assert.ok(container.querySelector('.inspector-section[aria-label="Board"]').classList.contains('is-folded'));
  container.querySelector('.scene-select[data-scene-id="s3"]').click();
  assert.equal(body('Board').hidden, true, 'still folded in another scene');
  assert.equal(body('Narration beats').hidden, true);
  // jumping to a beat issue unfolds its section first (jsdom has no scrollIntoView)
  const proto = window.HTMLElement.prototype;
  const had = Object.prototype.hasOwnProperty.call(proto, 'scrollIntoView');
  proto.scrollIntoView = function scrollIntoView() {};
  const issue = await waitFor(() => container.querySelector('button.issue[data-code="beat.too_short"]'));
  issue.click();
  await tick();
  if (!had) delete proto.scrollIntoView;
  assert.equal(body('Narration beats').hidden, false);
  assert.equal(toggle('Narration beats').getAttribute('aria-expanded'), 'true');
  assert.equal(body('Board').hidden, true);
});

test('the editor looks for a job again when the tab becomes visible; Delete is not taken from the inspector', async () => {
  freshTest();
  const { calls } = editorRoutes(sampleVersion());
  const { container } = await mountEditor(fakeApp(), { scene: 's2' });
  const jobCalls = () => calls.filter((c) => c.path === `/api/jobs?project_id=${PID}&limit=20`).length;
  await waitFor(() => jobCalls() === 1);
  document.dispatchEvent(new window.Event('visibilitychange'));
  await waitFor(() => jobCalls() === 2);
  // Delete on a control of the inspector is about that control, never "delete the scene"
  keydown(byText(container.querySelector('.inspector'), 'Duplicate'), 'Delete');
  await tick();
  assert.equal(document.querySelector('.modal'), null);
  keydown(container.querySelector('.scene-select[data-scene-id="s2"]'), 'Delete');
  await waitFor(() => document.querySelector('.modal'));
  clickModal('Cancel');
});

test('scene list: the scene the preview plays is marked "now playing"', async () => {
  const { createSceneList } = await import('../../js/studio/views/editor/sceneList.js');
  resetDom();
  const list = createSceneList({ onSelect: () => {}, onMove: () => {}, onAdd: () => {}, onDuplicate: () => {}, onDelete: () => {} });
  document.body.appendChild(list.el);
  list.update({ sp: sampleScreenplay(), selectedId: 's1', issueCounts: new Map(), staleScenes: new Set(), playingId: 's3' });
  const row = list.el.querySelector('.scene-select[data-scene-id="s3"]');
  assert.ok(row.classList.contains('is-playing'));
  assert.match(row.textContent, /now playing/);
  list.update({ sp: sampleScreenplay(), selectedId: 's1', issueCounts: new Map(), staleScenes: new Set() });
  assert.equal(list.el.querySelector('.is-playing'), null);
  assert.equal(byText(list.el, 'Picture from my library'), null, 'only offered when the editor allows the library');
  list.destroy();
});

// --- batch 4 review fixes -------------------------------------------------------------------------

test('Space on a focused checkbox or radio toggles it; it does not play the preview', async () => {
  freshTest();
  editorRoutes(sampleVersion(), { [`GET /api/versions/${VID}/changes`]: { available: false, scenes: [] } });
  const app = fakeApp();
  const { container } = await mountEditor(app, { scene: 's3' });
  const space = (el) => {
    const ev = new window.KeyboardEvent('keydown', { key: ' ', bubbles: true, cancelable: true });
    el.dispatchEvent(ev);
    return ev.defaultPrevented;
  };
  const boxes = [
    container.querySelector('[data-fk="scene:hidden"]'),
    container.querySelector('[data-fk="editor:autosave"]'),
    byText(container.querySelector('.issues-panel'), 'Only the selected scene', 'label').querySelector('input'),
  ];
  for (const box of boxes) {
    assert.ok(box, 'a checkbox of the editor');
    box.focus();
    assert.equal(space(box), false, `Space is the checkbox's own (${box.dataset.fk || box.closest('label').textContent})`);
  }
  const radio = document.createElement('input');
  radio.type = 'radio';
  container.querySelector('.inspector').appendChild(radio);
  radio.focus();
  assert.equal(space(radio), false, 'a radio button too');
  // on the page body Space is still the play shortcut
  assert.equal(space(document.body), true);
});

test('a job result that clashes with unsaved edits is merged and never saved automatically; Undo shows the job’s version', async () => {
  freshTest();
  localStorage.setItem('aadhi.studio.pref.editorAutosave.u7', 'true');
  const v = sampleVersion();
  let started = false;
  let jobDone = false;
  const regenerated = JSON.parse(JSON.stringify(v.screenplay));
  const s2 = regenerated.scenes.find((s) => s.id === 's2');
  s2.title = 'Regenerated by AI';
  s2.beats = [{ ...s2.beats[0], id: 's2-n1', narration: 'Brand new paid narration.' }, { ...s2.beats[1], id: 's2-n2', narration: 'Second new beat.' }];
  s2.side_panel = { ...s2.side_panel, show_from_beat_id: 's2-n2' };
  const puts = [];
  const job = () => ({ id: 78, kind: 'regenerate_scene', status: jobDone ? 'succeeded' : 'running', stage: 'script', progress: 0.2, project_id: PID, version_id: VID, created_at: '2026-10-01T10:00:00Z' });
  editorRoutes(v, {
    [`GET /api/versions/${VID}`]: () => (jobDone ? { ...v, revision: 5, screenplay: regenerated } : v),
    [`GET /api/versions/${VID}/changes`]: { available: false, scenes: [] },
    [`GET /api/jobs?project_id=${PID}&limit=20`]: () => ({ items: started && !jobDone ? [job()] : [], total: 0 }),
    'GET /api/jobs/78': () => job(),
    [`PUT /api/versions/${VID}/screenplay`]: ({ init }) => {
      const body = JSON.parse(init.body);
      puts.push(body);
      if (!started) {
        started = true;
        setTimeout(() => {
          jobDone = true;
        }, 500);
        return { status: 409, body: { detail: 'busy', code: 'version_busy', job_id: 78 } };
      }
      return { version: { ...v, screenplay: undefined, revision: body.revision + 1 }, issues: [], stale_scenes: [] };
    },
  });
  const app = fakeApp();
  const { container } = await mountEditor(app, { scene: 's2' });
  typeInto(container.querySelector('[data-fk="scene:title"]'), 'Teacher title');
  await waitFor(() => puts.length === 1, AUTOSAVE_MS + 2000); // refused: a job writes the version
  const dialog = await waitFor(() => [...document.querySelectorAll('.modal')].find((m) => /Changes merged with conflicts/.test(m.textContent)), 12000);
  assert.match(dialog.textContent, /Undo shows the job's version/);
  clickModal('OK');
  await waitFor(() => status(container).dataset.state === 'merged');
  assert.match(status(container).textContent, /clash with newer changes \(your version kept\)\. Review them and save\./);
  await tick(AUTOSAVE_MS + 1500);
  assert.equal(puts.length, 1, 'the paid result is never overwritten by an automatic save');
  const undoBtn = container.querySelector('button[aria-label="Undo"]');
  assert.equal(undoBtn.title, "Undo: Merge with the job's changes (Ctrl+Z)");
  undoBtn.click();
  assert.equal(container.querySelector('[data-fk="scene:title"]').value, 'Regenerated by AI', "Undo shows the job's version");
  assert.ok(container.querySelector('[data-fk="beat:main:s2-n1:narration"]'), "with the job's beats");
  assert.equal(undoBtn.title, 'Undo: Changes from the job (Ctrl+Z)', 'one more Undo takes back the job itself');
  container.querySelector('button[aria-label="Redo"]').click();
  assert.equal(container.querySelector('[data-fk="scene:title"]').value, 'Teacher title');
  byText(container, 'Save').click();
  await waitFor(() => puts.length === 2);
  assert.equal(puts[1].revision, 5);
  assert.equal(E.findScene(puts[1].screenplay, 's2').title, 'Teacher title', 'a save by hand sends the merged draft');
  await waitFor(() => status(container).dataset.state === 'saved');
});

test('Undo after a job changed the lecture takes back the job’s changes and says so', async () => {
  freshTest();
  let jobStatus = 'none';
  const job = () => ({ id: 79, kind: 'regenerate_scene', status: jobStatus, stage: 'script', progress: 0.2, project_id: PID, version_id: VID, created_at: '2026-10-01T10:00:00Z' });
  const { puts, state } = editorRoutes(sampleVersion(), {
    [`GET /api/versions/${VID}/changes`]: { available: false, scenes: [] },
    [`GET /api/jobs?project_id=${PID}&limit=20`]: () => ({ items: jobStatus === 'running' ? [job()] : [], total: 0 }),
    'GET /api/jobs/79': () => job(),
  });
  const app = fakeApp();
  const { container } = await mountEditor(app, { scene: 's2' });
  typeInto(container.querySelector('[data-fk="scene:title"]'), 'Teacher title');
  byText(container, 'Save').click();
  await waitFor(() => puts.length === 1);
  await tick(20);
  // a regeneration of scene 3 runs elsewhere and finishes
  jobStatus = 'running';
  document.dispatchEvent(new window.Event('visibilitychange'));
  await waitFor(() => container.querySelector('.inspector-host').hasAttribute('inert'));
  const regen = JSON.parse(JSON.stringify(state.screenplay));
  regen.scenes.find((s) => s.id === 's3').title = 'AI rewrote this';
  state.screenplay = regen;
  state.revision += 1;
  jobStatus = 'succeeded';
  await waitFor(() => !container.querySelector('.inspector-host').hasAttribute('inert'), 8000);
  const s3Row = () => container.querySelector('.scene-select[data-scene-id="s3"]').textContent;
  await waitFor(() => s3Row().includes('AI rewrote this'), 3000);
  const undoBtn = container.querySelector('button[aria-label="Undo"]');
  assert.equal(undoBtn.title, 'Undo: Changes from the job (Ctrl+Z)');
  undoBtn.click();
  assert.ok(app.rec.toasts.some((t) => t.message === 'Undone: Changes from the job.'));
  assert.ok(!s3Row().includes('AI rewrote this'), 'scene 3 is back to its text before the job');
  assert.equal(container.querySelector('[data-fk="scene:title"]').value, 'Teacher title', 'the earlier edit stays');
  assert.equal(container.querySelector('button[aria-label="Redo"]').title, 'Redo: Changes from the job (Ctrl+Shift+Z)');
  assert.equal(undoBtn.title, 'Undo: Edit title in scene 2 (Ctrl+Z)', "the teacher's own history is kept below it");
});

test('edits typed while a revert is on its way are kept on top of the reverted scene', async () => {
  freshTest();
  const changes = { available: true, scenes: [{ scene_id: 's2', status: 'edited', generated_index: 1, current_index: 1, fields_changed: ['title'], history: 0 }] };
  const held = deferred();
  const { calls, puts, state } = editorRoutes(sampleVersion(), {
    [`GET /api/versions/${VID}/changes`]: changes,
    're:^POST /api/versions/9/scenes/[^/]+/revert$': async () => {
      await held.promise;
      const reverted = JSON.parse(JSON.stringify(state.screenplay));
      reverted.scenes.find((s) => s.id === 's2').title = 'As Aadhi wrote it';
      state.screenplay = reverted;
      state.revision += 1;
      return { revision: state.revision, scene_id: 's2' };
    },
  });
  const app = fakeApp();
  const { container } = await mountEditor(app, { scene: 's2' });
  await waitFor(() => container.querySelector('.change-line'));
  byText(container.querySelector('.change-line'), 'Compare and revert').click();
  await waitFor(() => document.querySelector('.modal'));
  clickModal('Revert scene');
  await waitFor(() => calls.some((c) => c.method === 'POST' && c.path.endsWith('/revert')));
  // the editor stays live while the request is out: the teacher edits another scene
  container.querySelector('.scene-select[data-scene-id="s3"]').click();
  typeInto(container.querySelector('[data-fk="beat:main:s3-b1:narration"]'), 'Typed during the revert.');
  held.resolve();
  await waitFor(() => app.rec.toasts.some((t) => t.message === 'Scene 2 was reverted. Undo with Ctrl+Z.'));
  assert.equal(container.querySelector('.editor').dataset.dirty, 'true', 'the typed edit is still there');
  const undoBtn = container.querySelector('button[aria-label="Undo"]');
  assert.equal(undoBtn.title, 'Undo: Edits made during the revert (Ctrl+Z)');
  undoBtn.click();
  assert.equal(container.querySelector('.editor').dataset.dirty, 'false', 'one Undo gives the reverted lecture as saved');
  assert.equal(undoBtn.title, 'Undo: Revert scene (Ctrl+Z)');
  container.querySelector('button[aria-label="Redo"]').click();
  byText(container, 'Save').click();
  await waitFor(() => puts.length === 1);
  assert.equal(E.findScene(puts[0].screenplay, 's2').title, 'As Aadhi wrote it');
  assert.equal(E.findScene(puts[0].screenplay, 's3').beats[0].narration, 'Typed during the revert.');
});

test('"Save as a copy" never saves this version automatically while the copy is made; cancelled, saving goes on', async () => {
  freshTest();
  localStorage.setItem('aadhi.studio.pref.editorAutosave.u7', 'true');
  const slow = (body) => async () => {
    await new Promise((r) => setTimeout(r, 1500));
    return body;
  };
  const { puts } = editorRoutes(sampleVersion(), {
    [`POST /api/versions/${VID}/duplicate`]: slow({ status: 201, body: { version: { ...sampleVersion(), id: 12, number: 2, revision: 1, screenplay: undefined } } }),
    'PUT /api/versions/12/screenplay': slow({ version: { id: 12, revision: 2 }, issues: [], stale_scenes: [] }),
  });
  const app = fakeApp();
  const { container } = await mountEditor(app, { scene: 's2' });
  typeInto(container.querySelector('[data-fk="scene:title"]'), 'Only in the copy');
  byText(container, 'Save as a copy').click();
  await waitFor(() => document.querySelector('.modal'));
  await tick(AUTOSAVE_MS + 500); // the dialog stays open a while
  clickModal('Save copy');
  await waitFor(() => app.rec.navs.length === 1, 6000);
  assert.equal(puts.length, 0, 'this version stays as it was last saved');

  freshTest();
  localStorage.setItem('aadhi.studio.pref.editorAutosave.u7', 'true');
  const other = editorRoutes(sampleVersion());
  const second = await mountEditor(fakeApp(), { scene: 's2' });
  typeInto(second.container.querySelector('[data-fk="scene:title"]'), 'Saved after all');
  byText(second.container, 'Save as a copy').click();
  await waitFor(() => document.querySelector('.modal'));
  clickModal('Cancel');
  await waitFor(() => other.puts.length === 1, AUTOSAVE_MS + 1500);
  assert.equal(E.findScene(other.puts[0].screenplay, 's2').title, 'Saved after all');
});

test('automatic saving tries a failed save once, then waits for the next change or Retry (no retry loop)', async () => {
  freshTest();
  localStorage.setItem('aadhi.studio.pref.editorAutosave.u7', 'true');
  const puts = [];
  editorRoutes(sampleVersion(), {
    [`PUT /api/versions/${VID}/screenplay`]: ({ init }) => {
      puts.push(JSON.parse(init.body));
      return { status: 500, body: { detail: 'Server error' } };
    },
  });
  const app = fakeApp();
  const { container } = await mountEditor(app, { scene: 's2' });
  typeInto(container.querySelector('[data-fk="scene:title"]'), 'Not saved');
  await waitFor(() => puts.length === 1, AUTOSAVE_MS + 1500);
  await waitFor(() => status(container).dataset.state === 'error');
  await tick(AUTOSAVE_MS * 2 + 500);
  assert.equal(puts.length, 1, 'no automatic retry every few seconds');
  byText(container.querySelector('.save-actions'), 'Retry').click();
  await waitFor(() => puts.length === 2);
  typeInto(container.querySelector('[data-fk="scene:title"]'), 'Not saved either');
  await waitFor(() => puts.length === 3, AUTOSAVE_MS + 1500);
  await tick(AUTOSAVE_MS + 500);
  assert.equal(puts.length, 3);
});

test('render dialog: scenes are numbered as in the scene list, skipped scenes counted', async () => {
  freshTest();
  const sp = E.updateScene(sampleScreenplay(), 's2', (s) => {
    s.hidden = true;
  });
  const version = sampleVersion({ screenplay: sp, has_timeline: true, built_revision: 4, revision: 4, timeline_stale: false });
  editorRoutes(version, {
    [`GET /api/versions/${VID}/render/preflight`]: {
      blocking: true,
      items: [
        // the timeline leaves the skipped s2 out: s3 is its second scene, the editor's third
        { scene_index: 1, scene_number: 3, scene_id: 's3', title: 'Example', reason: 'no_audio', blocking: true, message: 'No narration audio.' },
        { scene_index: 4, scene_number: 9, scene_id: 'gone', title: 'Old', reason: 'no_audio', blocking: true, message: 'Gone.' },
        { scene_index: 5, scene_id: 'older', title: 'Older server', reason: 'no_audio', blocking: true, message: 'Old item.' },
      ],
    },
  });
  const { container } = await mountEditor(fakeApp(), { scene: 's1' });
  byText(container, 'Render MP4').click();
  const notice = await waitFor(() => document.querySelector('.modal .render-preflight'));
  const lines = [...notice.querySelectorAll('li')].map((li) => li.textContent);
  assert.deepEqual(lines, ['Scene 3 (Example): No narration audio.', 'Scene 9 (Old): Gone.', 'Scene 6 (Older server): Old item.']);
  clickModal('Fix first');
});
