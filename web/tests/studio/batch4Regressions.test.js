// Batch-4 regression list (design §10.3, B0 item 11): one test for each Studio fix listed under "Batch 4: review
// fixes" in docs/INTEGRATION_ANDRYAN.md, each taking an angle the batch's own tests (editorWorkflow, editor,
// editorTimeline, videos, videoPreview, nextStep) do not. The server-side fixes are in
// tests/api/test_batch4_regressions.py.
import { resetDom, mockFetch, tick, window } from './_dom.js';
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { mount as mountEditorView, AUTOSAVE_MS } from '../../js/studio/views/editor/editor.js';
import { mount as mountProjectView } from '../../js/studio/views/projectDetail.js';
import { mount as mountVideosView, POLL_MS, PAGE_SIZE } from '../../js/studio/views/videos.js';
import * as E from '../../js/studio/lib/screenplayEdit.js';
import { draftKey } from '../../js/studio/lib/draftStore.js';
import { closeAllModals } from '../../js/studio/components/modal.js';
import { sampleScreenplay, sampleVersion } from './fixtures.js';
import { fakeApp, byText, clickModal, typeInto, waitFor, deferred } from './_views.js';

const VID = 9;
const PID = 5;
const JOBS = `GET /api/jobs?project_id=${PID}&limit=20`;
const AUTOSAVE_PREF = 'aadhi.studio.pref.editorAutosave.u7';

/** Views mounted by the current test (destroyed before the next one, even after a failure). */
const live = [];

function freshTest(url = '/') {
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
  window.history.replaceState(null, '', url);
}
after(() => freshTest());

/** Let pending promise chains settle without using (possibly mocked) timers. */
async function settle(rounds = 12) {
  for (let i = 0; i < rounds; i++) await new Promise((r) => setImmediate(r));
}

/**
 * The editor's endpoints; `puts` collects PUT /screenplay bodies and the stored screenplay follows the saves.
 * @param {any} version
 * @param {Record<string, any>} [extra]
 */
function editorRoutes(version, extra = {}) {
  const puts = [];
  const state = { revision: version.revision, screenplay: version.screenplay };
  const calls = mockFetch({
    [`GET /api/versions/${VID}`]: () => ({ ...version, revision: state.revision, screenplay: state.screenplay }),
    [`GET /api/versions/${VID}/changes`]: { available: false, scenes: [] },
    [`POST /api/versions/${VID}/lint`]: { issues: [] },
    [`POST /api/versions/${VID}/timeline/preview`]: { status: 404, body: { detail: 'not built', code: 'not_found' } },
    [JOBS]: { items: [], total: 0 },
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

/** @param {any} app @param {Record<string, string>} [query] */
async function mountEditor(app, query = {}) {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const handle = await mountEditorView(container, { app, params: { id: PID, vid: VID }, query, signal: undefined });
  if (handle) live.push(handle);
  return { container, handle };
}

const status = (container) => container.querySelector('.save-status');
const title = (container) => container.querySelector('[data-fk="scene:title"]');
const toolbarButton = (container, label) => byText(container.querySelector('.editor-toolbar'), label, 'button');
const jobCalls = (calls) => calls.filter((c) => c.path === `/api/jobs?project_id=${PID}&limit=20`).length;
const jobRow = (id, kind, state) => ({ id, kind, status: state, stage: 'script', progress: 0.2, project_id: PID, version_id: VID, created_at: '2026-10-01T10:00:00Z' });

/**
 * A scene regeneration found running by the editor's job check once `job.started`, finished once `job.done`; its
 * result (`change` applied to the screenplay) is stored one revision later.
 * @param {any} version
 * @param {number} id
 * @param {(sp: any) => any} change
 */
function jobScript(version, id, change) {
  const job = { started: false, done: false };
  const result = change(JSON.parse(JSON.stringify(version.screenplay)));
  const row = () => jobRow(id, 'regenerate_scene', job.done ? 'succeeded' : 'running');
  const routes = {
    [`GET /api/versions/${VID}`]: () => (job.done ? { ...version, revision: version.revision + 1, screenplay: result } : version),
    [JOBS]: () => ({ items: job.started && !job.done ? [row()] : [], total: 0 }),
    [`GET /api/jobs/${id}`]: () => row(),
    [`GET /api/jobs/${id}/events`]: { items: [] },
  };
  return { job, routes, result };
}

// --- "a job result that clashes with unsaved edits holds automatic saving (merged) until a save by hand" ----------

test('autosave hold: after a clashing job result no edit is saved automatically until a save by hand; then it is', async () => {
  freshTest();
  localStorage.setItem(AUTOSAVE_PREF, 'true');
  const v = sampleVersion();
  const { job, routes } = jobScript(v, 78, (sp) => {
    sp.scenes.find((s) => s.id === 's2').title = 'Rewritten by the job';
    return sp;
  });
  const { puts } = editorRoutes(v, routes);
  const app = fakeApp();
  const { container } = await mountEditor(app, { scene: 's2' });
  typeInto(title(container), 'Teacher title');
  job.started = true; // found before the automatic save is due
  document.dispatchEvent(new window.Event('visibilitychange'));
  await waitFor(() => container.querySelector('.inspector-host').hasAttribute('inert'));
  job.done = true;
  const dialog = await waitFor(() => [...document.querySelectorAll('.modal')].find((m) => /Changes merged with conflicts/.test(m.textContent)), 9000);
  assert.ok(dialog, 'the clash is said in a dialog');
  clickModal('OK');
  await waitFor(() => status(container).dataset.state === 'merged');
  // later edits do not lift the hold: the job's paid result is never overwritten without the teacher saving
  typeInto(title(container), 'Teacher title, edited again');
  await tick(AUTOSAVE_MS + 600);
  assert.equal(puts.length, 0, 'nothing saved automatically after the merge');
  assert.equal(status(container).dataset.state, 'merged');
  toolbarButton(container, 'Save').click();
  await waitFor(() => puts.length === 1);
  assert.equal(puts[0].revision, v.revision + 1, 'saved on the job\'s revision');
  assert.equal(E.findScene(puts[0].screenplay, 's2').title, 'Teacher title, edited again');
  await waitFor(() => status(container).dataset.state === 'saved');
  // a save by hand lifts the hold: the next change is saved automatically again
  typeInto(title(container), 'Saved by itself again');
  await waitFor(() => puts.length === 2, AUTOSAVE_MS + 2000);
  assert.equal(E.findScene(puts[1].screenplay, 's2').title, 'Saved by itself again');
});

// --- "adopting a job's result is its own labelled undo step" ------------------------------------------------------

test('undo steps: a job result merged without clashes is two labelled steps and saving goes on automatically', async () => {
  freshTest();
  localStorage.setItem(AUTOSAVE_PREF, 'true');
  const v = sampleVersion();
  const { job, routes } = jobScript(v, 79, (sp) => {
    sp.scenes.find((s) => s.id === 's3').title = 'Rewritten by the job';
    return sp;
  });
  const { puts } = editorRoutes(v, routes);
  const app = fakeApp();
  const { container } = await mountEditor(app, { scene: 's2' });
  typeInto(title(container), 'Teacher title');
  job.started = true;
  document.dispatchEvent(new window.Event('visibilitychange'));
  await waitFor(() => container.querySelector('.inspector-host').hasAttribute('inert'));
  job.done = true;
  await waitFor(() => !container.querySelector('.inspector-host').hasAttribute('inert'), 9000);
  const undoBtn = container.querySelector('button[aria-label="Undo"]');
  await waitFor(() => undoBtn.title === "Undo: Merge with the job's changes (Ctrl+Z)");
  assert.equal(document.querySelector('.modal'), null, 'no clash, no dialog');
  assert.notEqual(status(container).dataset.state, 'merged');
  // the merged draft (the teacher's title and the job's scene) is saved automatically on the job's revision
  await waitFor(() => puts.length === 1, AUTOSAVE_MS + 2000);
  assert.equal(puts[0].revision, v.revision + 1);
  assert.equal(E.findScene(puts[0].screenplay, 's2').title, 'Teacher title');
  assert.equal(E.findScene(puts[0].screenplay, 's3').title, 'Rewritten by the job');
  undoBtn.click();
  assert.ok(app.rec.toasts.some((t) => t.message === "Undone: Merge with the job's changes."));
  assert.equal(title(container).value, 'The law', "first the job's version: the teacher's edit is not in it");
  assert.equal(undoBtn.title, 'Undo: Changes from the job (Ctrl+Z)');
  undoBtn.click();
  assert.equal(title(container).value, 'Teacher title', 'then the draft as it was before the job');
  assert.match(container.querySelector('.scene-select[data-scene-id="s3"]').textContent, /Worked example/);
});

// --- "jobs the editor starts lock it at once" -----------------------------------------------------------------

test('job lock: a build started from the editor locks editing at once, without waiting for the job poll', async () => {
  freshTest();
  let started = false;
  const row = () => jobRow(90, 'build_assets', 'queued');
  const { calls } = editorRoutes(sampleVersion(), {
    [JOBS]: () => ({ items: started ? [row()] : [], total: 0 }),
    [`POST /api/versions/${VID}/build`]: () => {
      started = true;
      return { status: 202, body: { job: row() } };
    },
    'GET /api/jobs/90': () => row(),
    'GET /api/jobs/90/events': { items: [] },
  });
  const app = fakeApp();
  const { container } = await mountEditor(app, { scene: 's2' });
  await waitFor(() => jobCalls(calls) === 1);
  toolbarButton(container, 'Build').click();
  await waitFor(() => calls.some((c) => c.method === 'POST' && c.path === `/api/versions/${VID}/build`));
  closeAllModals(); // the progress dialog may be closed: the lock does not depend on it
  await waitFor(() => container.querySelector('.inspector-host').hasAttribute('inert'), 1500);
  assert.equal(jobCalls(calls), 2, 'the editor looked for the job right after starting it (the poll is 15 s)');
  assert.ok(container.querySelector('.scene-pane').hasAttribute('inert'));
  assert.equal(status(container).dataset.state, 'busy');
  typeInto(title(container), 'Typed during the build');
  assert.equal(container.querySelector('.editor').dataset.dirty, 'false', 'the edit waits for the job');
});

// --- "Space leaves form controls and media to themselves" -------------------------------------------------------

test('Space on media, buttons, links, selects, the timeline slider and editable text is theirs, not the preview\'s', async () => {
  freshTest();
  editorRoutes(sampleVersion());
  const { container } = await mountEditor(fakeApp(), { scene: 's2' });
  const space = (el) => {
    const ev = new window.KeyboardEvent('keydown', { key: ' ', bubbles: true, cancelable: true });
    el.dispatchEvent(ev);
    return ev.defaultPrevented;
  };
  const inspector = container.querySelector('.inspector');
  const video = document.createElement('video');
  const audio = document.createElement('audio');
  const editable = document.createElement('div');
  editable.setAttribute('contenteditable', 'true');
  inspector.append(video, audio, editable);
  const targets = {
    video,
    audio,
    'contenteditable text': editable,
    'a toolbar button': toolbarButton(container, 'Save'),
    'a link': container.querySelector('.editor-head a[href]'),
    'a select': container.querySelector('[data-fk="scene:type"]'),
    'the timeline slider': container.querySelector('.tl-strip [role="slider"]'),
    'a folding section toggle': container.querySelector('.inspector-section .section-toggle'),
  };
  for (const [name, el] of Object.entries(targets)) {
    assert.ok(el, `${name} is on the page`);
    assert.equal(space(el), false, `Space is left to ${name}`);
  }
  assert.equal(space(document.body), true, 'on the page itself Space still plays or pauses the preview');
});

// --- "edits typed while a revert is out are merged on top of it" --------------------------------------------------

test('revert: an edit of the reverted field typed meanwhile is kept, said, and never saved automatically', async () => {
  freshTest();
  localStorage.setItem(AUTOSAVE_PREF, 'true');
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
  typeInto(title(container), 'Typed while the revert was out'); // the very field being reverted
  await tick(AUTOSAVE_MS + 300);
  assert.equal(puts.length, 0, 'nothing is saved while the revert is out');
  held.resolve();
  await waitFor(() => app.rec.toasts.some((t) => /edits made during the revert were kept \(1 conflict/.test(t.message)));
  assert.equal(title(container).value, 'Typed while the revert was out', 'the typed edit wins over the revert');
  await waitFor(() => status(container).dataset.state === 'merged');
  await tick(AUTOSAVE_MS + 600);
  assert.equal(puts.length, 0, 'the clash is the teacher\'s to review: no automatic save');
  const undoBtn = container.querySelector('button[aria-label="Undo"]');
  assert.equal(undoBtn.title, 'Undo: Edits made during the revert (Ctrl+Z)');
  undoBtn.click();
  assert.equal(title(container).value, 'As Aadhi wrote it', 'one Undo shows the reverted scene');
});

// --- "Save as a copy never saves the original meanwhile" --------------------------------------------------------

test('save as a copy: when the copy cannot take the edits they wait in the copy\'s draft; the original is never saved', async () => {
  freshTest();
  localStorage.setItem(AUTOSAVE_PREF, 'true');
  const { calls, puts } = editorRoutes(sampleVersion(), {
    [`POST /api/versions/${VID}/duplicate`]: { status: 201, body: { version: { ...sampleVersion(), id: 12, number: 2, revision: 1, screenplay: undefined } } },
    'PUT /api/versions/12/screenplay': { status: 500, body: { detail: 'Server error' } },
  });
  const app = fakeApp();
  const { container } = await mountEditor(app, { scene: 's2' });
  typeInto(title(container), 'Only in the copy');
  byText(container, 'Save as a copy').click();
  await waitFor(() => document.querySelector('.modal'));
  clickModal('Save copy');
  await waitFor(() => app.rec.navs.length === 1);
  assert.equal(app.rec.navs[0], `#/p/${PID}/v/12/edit`, 'the copy opens all the same');
  assert.ok(calls.some((c) => c.method === 'PUT' && c.path === '/api/versions/12/screenplay'), 'the copy was asked to take the edits');
  assert.ok(app.rec.toasts.some((t) => /could not be saved into it yet/.test(t.message)));
  const kept = JSON.parse(localStorage.getItem(draftKey(7, 12)) || 'null');
  assert.ok(kept, "the edits wait in the copy's local draft");
  assert.equal(kept.revision, 1);
  assert.equal(E.findScene(kept.screenplay, 's2').title, 'Only in the copy');
  assert.equal(localStorage.getItem(draftKey(7, VID)), null, 'they left this version');
  await tick(AUTOSAVE_MS + 500);
  assert.equal(puts.length, 0, 'this version stays as it was last saved');
  assert.equal(container.querySelector('.editor').dataset.dirty, 'false');
});

test('save as a copy: edits typed while the copy is made are offered in the copy, never saved into this version', async () => {
  freshTest();
  localStorage.setItem(AUTOSAVE_PREF, 'true');
  const made = deferred();
  const copyPuts = [];
  const { puts } = editorRoutes(sampleVersion(), {
    [`POST /api/versions/${VID}/duplicate`]: async () => {
      await made.promise;
      return { status: 201, body: { version: { ...sampleVersion(), id: 12, number: 2, revision: 1, screenplay: undefined } } };
    },
    'PUT /api/versions/12/screenplay': ({ init }) => {
      copyPuts.push(JSON.parse(init.body));
      return { version: { id: 12, revision: 2 }, issues: [], stale_scenes: [] };
    },
  });
  const app = fakeApp();
  const { container } = await mountEditor(app, { scene: 's2' });
  typeInto(title(container), 'In the copy');
  byText(container, 'Save as a copy').click();
  await waitFor(() => document.querySelector('.modal'));
  clickModal('Save copy');
  await tick(20);
  // the dialog is closed and the copy is still being made: the editor is live
  typeInto(title(container), 'Typed while the copy was made');
  await tick(AUTOSAVE_MS + 300);
  assert.equal(puts.length, 0, 'this version is not saved meanwhile');
  made.resolve();
  await waitFor(() => app.rec.navs.length === 1);
  assert.equal(E.findScene(copyPuts[0].screenplay, 's2').title, 'In the copy', 'the copy holds the edits made before');
  const offered = JSON.parse(localStorage.getItem(draftKey(7, 12)) || 'null');
  assert.ok(offered, 'the later edit waits in the copy\'s local draft, offered when the copy opens');
  assert.equal(offered.revision, 2, 'on the copy as saved');
  assert.equal(E.findScene(offered.screenplay, 's2').title, 'Typed while the copy was made');
  assert.equal(E.findScene(offered.base, 's2').title, 'In the copy');
  assert.equal(localStorage.getItem(draftKey(7, VID)), null);
  await tick(AUTOSAVE_MS + 300);
  assert.equal(puts.length, 0, 'this version stays as it was last saved');
});

// --- "a failed automatic save waits for the next change or Retry" -------------------------------------------------

test('autosave offline: one try, no retry loop (switching it off and on does not retry); Ctrl+S and Retry still save', async () => {
  freshTest();
  localStorage.setItem(AUTOSAVE_PREF, 'true');
  let online = false;
  const tries = [];
  const { puts } = editorRoutes(sampleVersion());
  const served = globalThis.fetch;
  globalThis.fetch = async (url, init = {}) => {
    if ((init.method || 'GET') === 'PUT') {
      tries.push(1);
      if (!online) throw new TypeError('Failed to fetch');
    }
    return served(url, init);
  };
  const app = fakeApp();
  const { container } = await mountEditor(app, { scene: 's2' });
  typeInto(title(container), 'Typed offline');
  await waitFor(() => tries.length === 1, AUTOSAVE_MS + 2000);
  await waitFor(() => status(container).dataset.state === 'error');
  assert.equal(byText(container.querySelector('.save-actions'), 'Retry').hidden, false);
  const toggle = container.querySelector('[data-fk="editor:autosave"]');
  toggle.click();
  toggle.click(); // off and on again
  await tick(AUTOSAVE_MS + 600);
  assert.equal(tries.length, 1, 'the draft that failed is not sent again by itself');
  container.querySelector('.editor').dispatchEvent(new window.KeyboardEvent('keydown', { key: 's', ctrlKey: true, bubbles: true, cancelable: true }));
  await waitFor(() => tries.length === 2);
  await waitFor(() => status(container).dataset.state === 'error'); // still offline
  await tick(AUTOSAVE_MS + 300);
  assert.equal(tries.length, 2, 'a save by hand that fails does not start a loop either');
  online = true;
  byText(container.querySelector('.save-actions'), 'Retry').click();
  await waitFor(() => puts.length === 1);
  await waitFor(() => status(container).dataset.state === 'saved');
  assert.equal(E.findScene(puts[0].screenplay, 's2').title, 'Typed offline');
});

// --- "pending inspector edits reach the draft before the job lock" -----------------------------------------------

test('job lock: a side panel animation still being typed reaches the draft before editing locks', async () => {
  freshTest();
  const sp = E.updateScene(sampleScreenplay(), 's2', (s) => {
    s.side_panel = { kind: 'manim', title: 'Current flow', rationale: 'Shows the current.', show_from_beat_id: null, manim: { template: null, params: {}, code: 'class P(AadhiScene):\n  pass' } };
  });
  const v = sampleVersion({ screenplay: sp });
  const { job, routes } = jobScript(v, 82, (doc) => doc);
  const { puts } = editorRoutes(v, routes);
  const { container } = await mountEditor(fakeApp(), { scene: 's2' });
  const code = await waitFor(() => container.querySelector('[data-fk="panel:manim:code"]'));
  typeInto(code, 'class P(AadhiScene):\n  TYPED = 2');
  // within the editor's debounce, a job started elsewhere is found
  job.started = true;
  document.dispatchEvent(new window.Event('visibilitychange'));
  await waitFor(() => container.querySelector('.inspector-host').hasAttribute('inert'));
  assert.equal(container.querySelector('.editor').dataset.dirty, 'true', 'the typing is in the draft, kept while locked');
  job.done = true;
  await waitFor(() => !container.querySelector('.inspector-host').hasAttribute('inert'), 9000);
  await tick(50);
  toolbarButton(container, 'Save').click();
  await waitFor(() => puts.length === 1);
  assert.equal(E.findScene(puts[0].screenplay, 's2').side_panel.manim.code, 'class P(AadhiScene):\n  TYPED = 2');
});

// --- "the timeline's keyboard slider moves through the estimates without a preview" -----------------------------

test('timeline slider in the editor without a preview: Home, End and the arrows choose the scene at that time', async () => {
  freshTest();
  editorRoutes(sampleVersion());
  const { container } = await mountEditor(fakeApp(), { scene: 's2' });
  const strip = await waitFor(() => (container.querySelector('.tl-strip.is-fallback') ? container.querySelector('.tl-strip') : null));
  const ruler = strip.querySelector('[role="slider"]');
  const inspectorScene = () => container.querySelector('.inspector').dataset.sceneId;
  const key = (k) => {
    const ev = new window.KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true });
    ruler.dispatchEvent(ev);
    return ev.defaultPrevented;
  };
  ruler.focus();
  assert.equal(key('End'), true);
  assert.equal(inspectorScene(), 's4', 'End: the last scene');
  assert.equal(ruler.getAttribute('aria-valuenow'), ruler.getAttribute('aria-valuemax'));
  assert.equal(key('Home'), true);
  assert.equal(inspectorScene(), 's1', 'Home: the first scene');
  assert.equal(ruler.getAttribute('aria-valuenow'), '0');
  const seen = new Set([inspectorScene()]);
  for (let i = 0; i < 600 && inspectorScene() !== 's4'; i++) {
    key('ArrowRight'); // one second at a time through the estimates
    seen.add(inspectorScene());
  }
  assert.deepEqual([...seen], ['s1', 's2', 's3', 's4'], 'every scene in order, by keyboard alone');
  assert.equal(document.activeElement, ruler, 'the slider keeps the focus while the inspector follows');
  assert.equal(strip.querySelector('.tl-playhead').hidden, true, 'no playhead without a preview');
});

// --- the Videos page: "leaves the list alone when only presigned links changed" and "keeps videos loaded past its
// 100-item re-read" ------------------------------------------------------------------------------------------------

const videoRow = (id, more = {}) => ({
  render_id: id,
  project_id: 5,
  project_title: "Ohm's law",
  version_id: 9,
  version_number: 2,
  created_at: '2026-10-08T10:00:00Z',
  status: 'succeeded',
  duration_s: 204,
  size_bytes: 25_165_824,
  width: 1920,
  height: 1080,
  matches_current: true,
  download_url: `/api/renders/${id}/download?file=video`,
  preview_url: `/s3/render-${id}.mp4?sig=1`,
  qa_ok: true,
  ...more,
});

test('videos: renewed links redraw nothing, and a new video at the top keeps every older one loaded, once', async (t) => {
  freshTest();
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const server = { newest: 150, reads: 0 };
  const all = () => Array.from({ length: server.newest }, (_, i) => {
    const id = server.newest - i;
    return videoRow(id, { preview_url: `/s3/render-${id}.mp4?sig=${server.reads}`, ...(id === server.newest ? { status: 'running', preview_url: null, download_url: null } : {}) });
  });
  const calls = mockFetch({
    're:^GET /api/videos\\?limit=(\\d+)&offset=(\\d+)$': ({ path }) => {
      server.reads += 1;
      const q = new URLSearchParams(path.split('?')[1]);
      const offset = Number(q.get('offset'));
      return { items: all().slice(offset, offset + Number(q.get('limit'))), total: server.newest };
    },
  });
  const container = document.createElement('div');
  document.body.appendChild(container);
  const app = fakeApp();
  const handle = mountVideosView(container, { app, params: {}, query: {} });
  live.push(handle);
  await settle();
  for (let i = 0; i < 4; i++) {
    byText(container, 'Load more', 'button').click();
    await settle();
  }
  const cards = () => [...container.querySelectorAll('.video-card')];
  assert.equal(cards().length, 5 * PAGE_SIZE);
  const oldest = container.querySelector('[data-render-id="31"]');
  const first = container.querySelector('[data-render-id="149"]');
  // a re-read while the newest video is made: only the presigned links are new
  t.mock.timers.tick(POLL_MS);
  await settle();
  assert.ok(calls.some((c) => c.path === '/api/videos?limit=100&offset=0'));
  assert.equal(container.querySelector('[data-render-id="149"]'), first, 'the same cards: nothing drawn again');
  assert.equal(container.querySelector('[data-render-id="31"]'), oldest);
  // a second video is started: the 100-item window now ends one video earlier
  server.newest = 151;
  t.mock.timers.tick(POLL_MS);
  await settle();
  const ids = cards().map((c) => Number(c.dataset.renderId));
  assert.equal(ids.length, 5 * PAGE_SIZE + 1, 'the new video and every video loaded before');
  assert.equal(new Set(ids).size, ids.length, 'no video twice');
  assert.deepEqual([ids[0], ids.at(-1)], [151, 31]);
  assert.match(container.querySelector('.list-footer').textContent, /Showing 121 of 151/);
});

// --- "the preview asks once for a fresh link before it says the video could not be loaded" -----------------------

test('project page: "Watch" on an expired link asks the version\'s renders once for a fresh one', async () => {
  freshTest();
  let reads = 0;
  const version = { id: 9, number: 1, label: '', status: 'ready', language: 'en-IN', revision: 4, built_revision: 4, timeline_stale: false, has_timeline: true, source_version_id: null, issue_counts: {}, created_at: '2026-09-01T10:00:00Z', updated_at: '2026-09-01T10:00:00Z' };
  const renderRow = () => ({ id: 4, version_id: 9, status: 'succeeded', language: 'en-IN', duration_s: 60, built_revision: 4, created_at: version.created_at, downloads: { video: '/api/renders/4/download?file=video' }, chapters_text: '', options: {}, job: null, preview_url: `/s3/render-4.mp4?sig=${reads}` });
  mockFetch({
    'GET /api/projects/5': projectDetail(version, { stage: 'video_ready', next_step: { action: 'watch', label: 'Watch', href: null } }),
    'GET /api/projects/5/shares': { items: [] },
    'GET /api/versions/9/render/preflight': { version_id: 9, blocking: false, items: [] },
    'GET /api/versions/9/renders': () => {
      reads += 1;
      return { items: [renderRow()] };
    },
  });
  const container = await mountProject();
  const section = container.querySelector('[data-section="renders"]');
  const row = await waitFor(() => section.querySelector('[data-render-id="4"]'));
  byText(row, 'Watch', 'button').click();
  const video = await waitFor(() => document.querySelector('.modal video'));
  const first = video.getAttribute('src');
  const before = reads;
  video.dispatchEvent(new window.Event('error'));
  await waitFor(() => video.getAttribute('src') !== first);
  assert.equal(reads, before + 1, 'one fresh read of the render list');
  assert.match(video.getAttribute('src'), new RegExp(`sig=${before + 1}$`));
  const warning = () => document.querySelector('.modal [role="alert"].notice');
  assert.equal(warning().hidden, true, 'no warning while the fresh link is tried');
  video.dispatchEvent(new window.Event('error'));
  await settle();
  assert.equal(reads, before + 1, 'asked only once');
  assert.equal(warning().hidden, false);
  clickModal('Close');
});

// --- "a failed lesson shows no workflow strip unless the video of a built lecture failed" -------------------------

/** GET /api/projects/{id} for project 5 with `version` current. */
function projectDetail(version, project = {}) {
  return {
    project: { id: 5, title: "Ohm's Law", subject_name: 'BEE', unit_name: 'Circuits', session_number: 'S2', session_title: "Ohm's Law", language: 'en-IN', owner: { id: 7, username: 'teacher1' }, current_version: version, active_job: null, created_at: version.created_at, updated_at: version.updated_at, ...project },
    versions: [project.current_version || version],
    sources: [],
    jobs: [],
  };
}

async function mountProject() {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const handle = await mountProjectView(container, { app: fakeApp(), params: { id: 5 }, query: {} });
  if (handle) live.push(handle);
  await waitFor(() => container.querySelector('.page-header'));
  return container;
}

test('next step: a failed video of a built lecture keeps its strip when unsaved editor changes change the action', async () => {
  freshTest();
  const version = { id: 9, number: 1, label: '', status: 'ready', language: 'en-IN', revision: 4, built_revision: 4, timeline_stale: false, has_timeline: true, source_version_id: null, issue_counts: {}, created_at: '2026-09-01T10:00:00Z', updated_at: '2026-09-01T10:00:00Z' };
  localStorage.setItem(draftKey(7, 9), JSON.stringify({ revision: 4, savedAt: new Date().toISOString(), screenplay: sampleScreenplay(), base: null }));
  mockFetch({
    'GET /api/projects/5': projectDetail(version, { stage: 'failed', next_step: { action: 'render', label: 'The video could not be made. Try again', href: null } }),
    'GET /api/projects/5/shares': { items: [] },
    'GET /api/versions/9/renders': { items: [] },
    'GET /api/versions/9/render/preflight': { version_id: 9, blocking: false, items: [] },
  });
  const container = await mountProject();
  const card = container.querySelector('.next-step');
  assert.ok(card.querySelector('[data-unsaved-draft]'), 'the unsaved changes come first');
  assert.ok(byText(card, 'Open the editor to save your changes', 'a'));
  const steps = [...card.querySelectorAll('.workflow-step')];
  assert.deepEqual(steps.map((s) => s.className.replace('workflow-step ', '')), ['is-done', 'is-done', 'is-done', 'is-done', 'is-current'], 'the lecture is built: only the video is left');

  // a failed generation says nothing about where the lesson is, with or without a local draft
  freshTest();
  localStorage.setItem(draftKey(7, 9), JSON.stringify({ revision: 4, savedAt: new Date().toISOString(), screenplay: sampleScreenplay(), base: null }));
  mockFetch({
    'GET /api/projects/5': projectDetail({ ...version, status: 'failed' }, { stage: 'failed', next_step: { action: 'retry', label: 'Try again', href: null } }),
    'GET /api/projects/5/shares': { items: [] },
    'GET /api/versions/9/renders': { items: [] },
    'GET /api/versions/9/render/preflight': { version_id: 9, blocking: false, items: [] },
  });
  const other = await mountProject();
  assert.equal(other.querySelector('.next-step .workflow-strip'), null);
});
