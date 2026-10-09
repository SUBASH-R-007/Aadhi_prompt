import { resetDom, mockFetch, tick, window } from './_dom.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import { mount } from '../../js/studio/views/editor/editor.js';
import * as E from '../../js/studio/lib/screenplayEdit.js';
import { localProblems } from '../../js/studio/lib/validationIssues.js';
import { draftKey } from '../../js/studio/lib/draftStore.js';
import { closeAllModals } from '../../js/studio/components/modal.js';
import { sampleScreenplay, sampleVersion, sampleMeta, sampleMetaWithTemplateSchemas } from './fixtures.js';
import { fakeApp, deferred, byText, clickModal, typeInto, waitFor, jsonBody } from './_views.js';

const VID = 9;
const PID = 5;

/** Two simulation scenes (s2 "A", s3 "B") with free-form code, plus the sample's others. */
function simScreenplay() {
  let sp = sampleScreenplay();
  sp = E.changeSceneType(sp, 's2', 'simulation');
  sp = E.changeSceneType(sp, 's3', 'simulation');
  sp = E.updateScene(sp, 's2', (s) => {
    s.manim = { template: null, params: {}, code: 'class A(AadhiScene):\n  pass' };
  });
  sp = E.updateScene(sp, 's3', (s) => {
    s.manim = { template: null, params: {}, code: 'class B(AadhiScene):\n  pass' };
  });
  return sp;
}

/**
 * Mock the editor's endpoints. Returns the call log and a `puts` list of PUT bodies.
 * @param {any} version
 * @param {Record<string, any>} [extra]
 */
function editorRoutes(version, extra = {}) {
  const puts = [];
  let revision = version.revision;
  const calls = mockFetch({
    [`GET /api/versions/${VID}`]: () => ({ ...version, revision }),
    [`POST /api/versions/${VID}/lint`]: { issues: [] },
    [`POST /api/versions/${VID}/timeline/preview`]: { status: 404, body: { detail: 'not built', code: 'not_found' } },
    [`GET /api/jobs?project_id=${PID}&limit=20`]: { items: [], total: 0 },
    [`PUT /api/versions/${VID}/screenplay`]: ({ init }) => {
      const body = JSON.parse(init.body);
      puts.push(body);
      revision += 1;
      return { version: { ...version, screenplay: undefined, revision }, issues: [], stale_scenes: [] };
    },
    ...extra,
  });
  return { calls, puts };
}

/**
 * Mount the editor in a connected container.
 * @param {any} app
 * @param {Record<string, string>} [query]
 * @param {AbortSignal} [signal]
 */
async function mountEditor(app, query = {}, signal) {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const handle = await mount(container, { app, params: { id: PID, vid: VID }, query, signal });
  if (handle) live.push(handle);
  return { container, handle };
}

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

test('fixture sanity: the simulation screenplay is locally valid', () => {
  assert.deepEqual(localProblems(simScreenplay()), []);
});

test('a pending (debounced) Manim edit stays in its own scene when the user switches scenes', async () => {
  freshTest();
  const version = sampleVersion({ screenplay: simScreenplay() });
  const { puts } = editorRoutes(version);
  const app = fakeApp();
  const { container, handle } = await mountEditor(app, { scene: 's2' });
  const codeA = container.querySelector('[data-fk="scene:manim:code"]');
  assert.match(codeA.value, /class A/);
  typeInto(codeA, 'class A(AadhiScene):\n  EDITED_IN_A = 1');
  // Within the 250 ms debounce, switch to scene B.
  container.querySelector('.scene-select[data-scene-id="s3"]').click();
  const codeB = container.querySelector('[data-fk="scene:manim:code"]');
  assert.match(codeB.value, /class B/, 'the inspector shows scene B');
  assert.equal(container.querySelector('.inspector').dataset.sceneId, 's3');
  await tick(300); // past the debounce: nothing else may arrive
  byText(container, 'Save').click();
  await waitFor(() => puts.length === 1);
  const saved = puts[0].screenplay;
  assert.equal(E.findScene(saved, 's2').manim.code, 'class A(AadhiScene):\n  EDITED_IN_A = 1', "A keeps A's edit");
  assert.equal(E.findScene(saved, 's3').manim.code, 'class B(AadhiScene):\n  pass', 'B is untouched');
  assert.equal(puts[0].revision, version.revision);
  handle.destroy();
});

test('pending edits are flushed into the right scene before add, duplicate, delete, undo and save', async () => {
  freshTest();
  const version = sampleVersion({ screenplay: simScreenplay() });
  const { puts } = editorRoutes(version);
  const app = fakeApp();
  const { container, handle } = await mountEditor(app, { scene: 's2' });
  const undoBtn = container.querySelector('button[title^="Undo"]');
  const redoBtn = container.querySelector('button[title^="Redo"]');

  // A committed edit first (so Undo is enabled), then a pending (debounced) code edit.
  typeInto(container.querySelector('[data-fk="scene:title"]'), 'Animated law');
  typeInto(container.querySelector('[data-fk="scene:manim:code"]'), 'class A(AadhiScene):\n  X = 1');
  // Undo right after typing undoes the typing (not the title, and it is not re-applied).
  undoBtn.click();
  assert.match(container.querySelector('[data-fk="scene:manim:code"]').value, /pass/, 'the code edit is undone');
  assert.equal(container.querySelector('[data-fk="scene:title"]').value, 'Animated law', 'the earlier edit stays');
  assert.equal(redoBtn.disabled, false, 'redo is available (not cleared by a late flush)');
  await tick(300);
  assert.match(container.querySelector('[data-fk="scene:manim:code"]').value, /pass/, 'nothing re-applied later');
  redoBtn.click();
  assert.match(container.querySelector('[data-fk="scene:manim:code"]').value, /X = 1/, 'redo restores it');

  // Duplicate while an edit is pending: the copy includes the edit, the original keeps it.
  typeInto(container.querySelector('[data-fk="scene:manim:code"]'), 'class A(AadhiScene):\n  Y = 2');
  byText(container.querySelector('.inspector'), 'Duplicate').click();
  await tick();
  assert.equal(container.querySelector('.inspector').dataset.sceneId, 's2-copy');
  await tick(300);

  // Ctrl+S with a pending edit saves it.
  typeInto(container.querySelector('[data-fk="scene:manim:code"]'), 'class A2(AadhiScene):\n  Z = 3');
  container.querySelector('.editor').dispatchEvent(new window.KeyboardEvent('keydown', { key: 's', ctrlKey: true, bubbles: true, cancelable: true }));
  await waitFor(() => puts.length === 1);
  const saved = puts[0].screenplay;
  assert.match(E.findScene(saved, 's2').manim.code, /Y = 2/);
  assert.match(E.findScene(saved, 's2-copy').manim.code, /Z = 3/);
  assert.equal(E.findScene(saved, 's3').manim.code, 'class B(AadhiScene):\n  pass');
  handle.destroy();
});

test('destroying the editor flushes a pending edit into its scene and autosaves the draft per user', async () => {
  freshTest();
  const version = sampleVersion({ screenplay: simScreenplay() });
  editorRoutes(version);
  const app = fakeApp();
  const { container, handle } = await mountEditor(app, { scene: 's3' });
  typeInto(container.querySelector('[data-fk="scene:manim:code"]'), 'class B(AadhiScene):\n  LAST = 1');
  handle.destroy();
  const raw = localStorage.getItem(draftKey(7, VID));
  assert.ok(raw, 'draft stored under the user-scoped key');
  assert.equal(localStorage.getItem(`aadhi.studio.draft.v${VID}`), null, 'no unscoped key');
  const draft = JSON.parse(raw);
  assert.match(E.findScene(draft.screenplay, 's3').manim.code, /LAST = 1/);
  assert.match(E.findScene(draft.screenplay, 's2').manim.code, /class A/);
});

test('restore offer names the lecture, restores into the editor, and is never shown to another account', async () => {
  freshTest();
  const version = sampleVersion();
  editorRoutes(version);
  // First session: edit the title and leave.
  const first = await mountEditor(fakeApp(), { scene: 's2' });
  typeInto(first.container.querySelector('[data-fk="scene:title"]'), 'Edited title');
  first.handle.destroy();
  assert.ok(localStorage.getItem(draftKey(7, VID)));

  // Another account on the same browser: no dialog, nothing restored.
  const other = fakeApp({ user: () => ({ id: 8, username: 'admin', role: 'admin', must_change_password: false }) });
  const second = await mountEditor(other, { scene: 's2' });
  await tick(20);
  assert.equal(document.querySelector('.modal'), null);
  assert.equal(second.container.querySelector('[data-fk="scene:title"]').value, 'The law');
  second.handle.destroy();

  // The same teacher again: the dialog names the lecture; Restore brings the edit back.
  const third = await mountEditor(fakeApp(), { scene: 's2' });
  await waitFor(() => document.querySelector('.modal'));
  const dialog = document.querySelector('.modal');
  assert.match(dialog.textContent, /Restore unsaved changes/);
  assert.match(dialog.textContent, /Ohm's Law/);
  clickModal('Restore');
  await tick();
  assert.equal(third.container.querySelector('[data-fk="scene:title"]').value, 'Edited title');
  assert.equal(byText(third.container, 'Save').disabled, false);
  assert.match(third.container.querySelector('.save-status').textContent, /restored/i);
  third.handle.destroy();

  // Discard removes it.
  const fourth = await mountEditor(fakeApp(), { scene: 's2' });
  await waitFor(() => document.querySelector('.modal'));
  clickModal('Discard them');
  await tick();
  assert.equal(localStorage.getItem(draftKey(7, VID)), null);
  fourth.handle.destroy();
});

test('leaving the route while the editor loads: no leave guard, no restore dialog, draft kept', async () => {
  freshTest();
  localStorage.setItem(draftKey(7, VID), JSON.stringify({ revision: 4, savedAt: new Date().toISOString(), screenplay: { ...sampleScreenplay(), session_title: 'Other' }, base: null }));
  const slow = deferred();
  mockFetch({ [`GET /api/versions/${VID}`]: () => slow.promise });
  const app = fakeApp();
  const ac = new AbortController();
  const pending = mountEditor(app, {}, ac.signal);
  await tick();
  ac.abort(); // the user navigated elsewhere
  slow.resolve(sampleVersion());
  const { handle } = await pending;
  await tick(20);
  assert.equal(handle, undefined, 'no view handle');
  assert.equal(app.rec.guardCalls, 0, 'no leave guard installed');
  assert.equal(document.querySelector('.modal'), null, 'no dialog over the next page');
  assert.ok(localStorage.getItem(draftKey(7, VID)), 'the draft is not discarded');
});

test('save conflict: "Keep mine" merges with the newer version and saves again', async () => {
  freshTest();
  const version = sampleVersion();
  const theirs = sampleScreenplay();
  theirs.scenes[2].title = 'Their example title';
  let conflictOnce = true;
  const puts = [];
  mockFetch({
    [`GET /api/versions/${VID}`]: () => (conflictOnce ? version : sampleVersion({ revision: 6, screenplay: theirs })),
    [`POST /api/versions/${VID}/lint`]: { issues: [] },
    [`POST /api/versions/${VID}/timeline/preview`]: { status: 404, body: { detail: 'x', code: 'not_found' } },
    [`GET /api/jobs?project_id=${PID}&limit=20`]: { items: [], total: 0 },
    [`PUT /api/versions/${VID}/screenplay`]: ({ init }) => {
      const body = JSON.parse(init.body);
      puts.push(body);
      if (conflictOnce) {
        conflictOnce = false;
        return { status: 409, body: { detail: 'Revision conflict', code: 'revision_conflict', current_revision: 6 } };
      }
      return { version: { ...version, screenplay: undefined, revision: 7 }, issues: [], stale_scenes: [] };
    },
  });
  const app = fakeApp();
  const { container, handle } = await mountEditor(app, { scene: 's2' });
  typeInto(container.querySelector('[data-fk="scene:title"]'), 'My law title');
  byText(container, 'Save').click();
  await waitFor(() => document.querySelector('.modal'));
  assert.match(document.querySelector('.modal').textContent, /changed elsewhere/);
  clickModal('Keep mine');
  await waitFor(() => puts.length === 2);
  assert.equal(puts[0].revision, 4);
  assert.equal(puts[1].revision, 6, 'saved on top of the newer revision');
  const merged = puts[1].screenplay;
  assert.equal(E.findScene(merged, 's2').title, 'My law title', 'mine kept');
  assert.equal(E.findScene(merged, 's3').title, 'Their example title', 'theirs kept');
  await waitFor(() => /All changes saved/.test(container.querySelector('.save-status').textContent));
  handle.destroy();
});

test('scene list keeps keyboard focus on the focused scene when the lint response arrives', async () => {
  freshTest();
  const lint = deferred();
  mockFetch({
    [`GET /api/versions/${VID}`]: sampleVersion(),
    [`POST /api/versions/${VID}/lint`]: () => lint.promise,
    [`POST /api/versions/${VID}/timeline/preview`]: { status: 404, body: { detail: 'x', code: 'not_found' } },
    [`GET /api/jobs?project_id=${PID}&limit=20`]: { items: [], total: 0 },
  });
  const { container, handle } = await mountEditor(fakeApp());
  const s3 = container.querySelector('.scene-select[data-scene-id="s3"]');
  s3.focus();
  assert.equal(document.activeElement, s3);
  lint.resolve({ issues: [{ code: 'x', severity: 'warning', message: 'w', scene_id: 's2', beat_id: null }] });
  await tick(10);
  assert.equal(document.activeElement.classList.contains('scene-select'), true, 'focus stays in the list');
  assert.equal(document.activeElement.dataset.sceneId, 's3');
  handle.destroy();
});

test('new simulation scenes start valid with the documented meta, and template-only servers flag missing params', async () => {
  freshTest();
  editorRoutes(sampleVersion());
  // Documented /api/meta (no params_schema/example_params): the code skeleton is used.
  let app = fakeApp();
  let m = await mountEditor(app, { scene: 's2' });
  byText(m.container, 'Add scene').click();
  byText(document.body, 'Simulation (Manim)', '[role="menuitem"], button').click();
  await tick();
  const sceneId = m.container.querySelector('.inspector').dataset.sceneId;
  assert.equal(m.container.querySelector('.inspector').dataset.sceneType, 'simulation');
  assert.ok(m.container.querySelector('[data-fk="scene:manim:code"]'), 'free-form skeleton');
  assert.ok(!m.container.querySelector('.issues-panel, .issues')?.textContent.includes('needs parameters'));
  m.handle.destroy();

  // A server that allows templates only: the template starts with empty params, reported locally.
  freshTest();
  editorRoutes(sampleVersion());
  const templateOnly = sampleMeta();
  templateOnly.features.manim_freeform = false;
  app = fakeApp({ meta: async () => templateOnly });
  m = await mountEditor(app, { scene: 's2' });
  byText(m.container, 'Add scene').click();
  byText(document.body, 'Simulation (Manim)', '[role="menuitem"], button').click();
  await tick();
  assert.equal(m.container.querySelector('[data-fk="scene:manim:template"]').value, 'equation_steps');
  assert.equal(m.container.querySelector('[data-fk="scene:manim:params"]').value.trim(), '{}');
  assert.match(m.container.textContent, /does not publish the parameter fields/);
  assert.match(m.container.textContent, /needs parameters for the “Equation steps” template/);
  m.handle.destroy();

  // With the requested schema extras, the template's example params are used (valid at once).
  freshTest();
  editorRoutes(sampleVersion());
  app = fakeApp({ meta: async () => sampleMetaWithTemplateSchemas() });
  m = await mountEditor(app, { scene: 's2' });
  byText(m.container, 'Add scene').click();
  byText(document.body, 'Simulation (Manim)', '[role="menuitem"], button').click();
  await tick();
  assert.equal(m.container.querySelector('[data-fk="scene:manim:template"]').value, 'equation_steps');
  assert.ok(!/needs parameters/.test(m.container.textContent));
  assert.ok(sceneId);
  m.handle.destroy();
});

test('board: "Add item" opens the new item, and figure items work without source figures', async () => {
  freshTest();
  const sp = sampleScreenplay();
  sp.figures = [];
  editorRoutes(sampleVersion({ screenplay: sp }));
  const { container, handle } = await mountEditor(fakeApp(), { scene: 's2' });
  const kind = container.querySelector('select[aria-label="Kind of board item to add"]');
  kind.value = 'formula';
  byText(container, 'Add item').click();
  await tick();
  const items = [...container.querySelectorAll('details.board-item')];
  assert.equal(items.length, 4);
  assert.equal(items[3].open, true, 'the new item editor is open');
  assert.ok(items[3].querySelector('[data-fk="item:s2-i4:latex"]'));

  const kind2 = container.querySelector('select[aria-label="Kind of board item to add"]');
  const figOption = [...kind2.options].find((o) => o.value === 'figure');
  assert.equal(figOption.disabled, false, 'figure is offered without source figures');
  kind2.value = 'figure';
  byText(container, 'Add item').click();
  await tick();
  const fig = [...container.querySelectorAll('details.board-item')].at(-1);
  assert.equal(fig.open, true);
  assert.match(fig.textContent, /No figures yet: upload one below/);
  assert.ok(byText(fig, 'Upload file'), 'upload control available');
  // Reported (after the debounced check) until a figure is uploaded or chosen.
  await waitFor(() => /needs a figure/.test(container.querySelector('.pane-side').textContent));
  handle.destroy();
});

// --- Render MP4: preflight (silent scenes) ------------------------------------------------------

const SILENT = { scene_index: 1, scene_id: 's2', title: 'Ohm', reason: 'no_audio', blocking: true, message: 'No narration audio.' };
const BOARD = { scene_index: 2, scene_id: 's3', title: 'Sim', reason: 'simulation_without_media', blocking: false, message: 'Text board instead.' };
const BUSY = { status: 429, body: { detail: 'You already have 2 video render(s) in progress.', code: 'render_busy' }, headers: { 'retry-after': '60' } };

/** Built version + render routes; `renders` collects the POST /render bodies. */
function renderRoutes(preflight, answers) {
  const renders = [];
  const version = sampleVersion({ has_timeline: true, built_revision: 4, revision: 4, timeline_stale: false });
  const { calls } = editorRoutes(version, {
    [`GET /api/versions/${VID}/render/preflight`]: preflight,
    [`POST /api/versions/${VID}/render`]: ({ init }) => {
      renders.push(JSON.parse(init.body));
      return answers[Math.min(renders.length, answers.length) - 1];
    },
  });
  return { calls, renders };
}

test('render: silent scenes are listed first; "Fix first" sends nothing, "Render anyway" allows a degraded video', async () => {
  freshTest();
  const { renders } = renderRoutes({ blocking: true, items: [SILENT, BOARD] }, [BUSY]);
  const app = fakeApp();
  const { container, handle } = await mountEditor(app);
  byText(container, 'Render MP4').click();
  await waitFor(() => document.querySelector('.modal .render-preflight'));
  const notice = document.querySelector('.modal .render-preflight');
  assert.equal(notice.dataset.preflight, 'blocking');
  assert.match(notice.textContent, /1 scene would be silent/);
  assert.match(notice.textContent, /Scene 2 \(Ohm\): No narration audio\./);
  assert.match(notice.textContent, /Scene 3 \(Sim\): Text board instead\./);
  const labels = [...document.querySelectorAll('.modal .modal-footer button')].map((b) => b.textContent.trim());
  assert.deepEqual(labels, ['Render anyway', 'Fix first'], 'Fix first is the primary (last) action');
  clickModal('Fix first');
  await tick(20);
  assert.equal(renders.length, 0, 'nothing is rendered');

  byText(container, 'Render MP4').click();
  await waitFor(() => document.querySelector('.modal .render-preflight'));
  clickModal('Render anyway');
  await waitFor(() => renders.length === 1);
  assert.deepEqual(renders[0], { burn_captions: false, include_intro: true, allow_degraded: true });
  await waitFor(() => app.rec.errors.length === 1);
  assert.equal(app.rec.errors[0], 'Could not start the render.');
  handle.destroy();
});

test('render: a clean preflight refuses silent scenes; a 409 render_preflight asks again and can render anyway', async () => {
  freshTest();
  const refused = { status: 409, body: { detail: '1 scene(s) would be silent in the video.', code: 'render_preflight', items: [SILENT] } };
  const { renders } = renderRoutes({ blocking: false, items: [] }, [refused, BUSY]);
  const app = fakeApp();
  const { container, handle } = await mountEditor(app);
  byText(container, 'Render MP4').click();
  await waitFor(() => document.querySelector('.modal .modal-footer'));
  assert.equal(document.querySelector('.modal .render-preflight'), null, 'nothing to report');
  const boxes = [...document.querySelectorAll('.modal input[type="checkbox"]')];
  const soft = boxes.find((i) => /selectable caption track/.test(i.parentElement.parentElement.textContent) && !/Burn captions/.test(i.parentElement.parentElement.textContent));
  assert.ok(soft, 'the caption-track option is offered');
  soft.checked = true;
  clickModal('Render');
  await waitFor(() => renders.length === 1);
  assert.deepEqual(renders[0], { burn_captions: false, include_intro: true, soft_subtitles: true, allow_degraded: false });
  await waitFor(() => document.querySelector('.modal .render-preflight'));
  assert.match(document.querySelector('.modal .render-preflight').textContent, /Scene 2 \(Ohm\)/);
  clickModal('Render anyway');
  await waitFor(() => renders.length === 2);
  assert.equal(renders[1].allow_degraded, true);
  assert.equal(renders[1].soft_subtitles, true);
  handle.destroy();
});

test('render: when the preflight cannot be loaded the request is unchanged (no allow_degraded)', async () => {
  freshTest();
  const { renders } = renderRoutes({ status: 500, body: { detail: 'boom', code: 'error' } }, [BUSY]);
  const { container, handle } = await mountEditor(fakeApp());
  byText(container, 'Render MP4').click();
  await waitFor(() => document.querySelector('.modal .modal-footer'));
  clickModal('Render');
  await waitFor(() => renders.length === 1);
  assert.deepEqual(renders[0], { burn_captions: false, include_intro: true });
  handle.destroy();
});

test('Choose from library is offered only in the user\'s own lecture (an admin may open someone else\'s)', async () => {
  const admin = { id: 8, username: 'boss', role: 'admin', must_change_password: false };
  const cases = [
    [fakeApp(), null, true], // a teacher only ever opens their own lectures: no lookup needed
    [fakeApp({ user: () => admin }), { project: { id: PID, owner: { id: 99, username: 'teacher1' } } }, false],
    [fakeApp({ user: () => admin }), { project: { id: PID, owner: { id: 8, username: 'boss' } } }, true],
    [fakeApp({ user: () => admin }), { status: 500, body: { detail: 'down', code: 'internal' } }, false],
  ];
  for (const [app, project, offered] of cases) {
    freshTest();
    const { calls } = editorRoutes(sampleVersion({ screenplay: simScreenplay() }), project ? { [`GET /api/projects/${PID}`]: project } : {});
    const { container } = await mountEditor(app, { scene: 's2' });
    await waitFor(() => container.querySelector('[data-fk="scene:title"]'));
    assert.equal(!!container.querySelector('[data-action="library"]'), offered, JSON.stringify(project));
    assert.equal(calls.some((c) => c.path === `/api/projects/${PID}`), project !== null);
  }
});

test('a job found while Manim code is still being typed keeps the typing: it reaches the draft before editing locks', async () => {
  freshTest();
  let running = false;
  let done = false;
  const job = () => ({ id: 81, kind: 'build_assets', status: done ? 'succeeded' : 'running', stage: 'tts', progress: 0.4, project_id: PID, version_id: VID, created_at: '2026-10-01T10:00:00Z' });
  const version = sampleVersion({ screenplay: simScreenplay() });
  const { puts } = editorRoutes(version, {
    [`GET /api/jobs?project_id=${PID}&limit=20`]: () => ({ items: running && !done ? [job()] : [], total: 0 }),
    'GET /api/jobs/81': () => job(),
  });
  const app = fakeApp();
  const { container } = await mountEditor(app, { scene: 's2' });
  typeInto(container.querySelector('[data-fk="scene:manim:code"]'), 'class A(AadhiScene):\n  TYPED = 1');
  // within the 250 ms debounce, a build started elsewhere is found
  running = true;
  document.dispatchEvent(new window.Event('visibilitychange'));
  await waitFor(() => container.querySelector('.inspector-host').hasAttribute('inert'));
  assert.equal(container.querySelector('.editor').dataset.dirty, 'true', 'the typing is in the draft, kept while locked');
  done = true;
  await waitFor(() => !container.querySelector('.inspector-host').hasAttribute('inert'), 8000);
  await tick(50);
  assert.match(container.querySelector('[data-fk="scene:manim:code"]').value, /TYPED = 1/, 'still there after the job');
  byText(container, 'Save').click();
  await waitFor(() => puts.length === 1);
  assert.equal(E.findScene(puts[0].screenplay, 's2').manim.code, 'class A(AadhiScene):\n  TYPED = 1');
});
