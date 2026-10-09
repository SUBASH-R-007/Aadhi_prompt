// Entry points of the Visual review: the editor (toolbar link, the inspector's "Visual" line, media
// issue actions, choosing library media), and the project page (link and attention badge).
import { resetDom, mockFetch, tick, FakeEventSource } from './_dom.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import { mount as mountEditorView } from '../../js/studio/views/editor/editor.js';
import { mount as mountProject } from '../../js/studio/views/projectDetail.js';
import { createIssuesPanel, mediaActionsOf } from '../../js/studio/views/editor/issuesPanel.js';
import { mediaUpload, libraryKindOf } from '../../js/studio/views/editor/mediaUpload.js';
import { visualInfo } from '../../js/studio/views/editor/visualInfo.js';
import { closeAllModals } from '../../js/studio/components/modal.js';
import { sampleScreenplay, sampleVersion } from './fixtures.js';
import { fakeApp, byText, clickModal, waitFor, jsonBody } from './_views.js';

const VID = 9;
const PID = 5;

function sv(over = {}) {
  return {
    scene_id: 's2',
    index: 1,
    title: 'The law',
    kind: 'image',
    source: 'generated',
    provider: 'pollinations',
    model: 'flux',
    prompt: 'A water pipe',
    url: '/media/s2.png',
    poster_url: null,
    status: 'ready',
    status_reason: null,
    review: { state: 'approved', stale: false, note: null, updated_at: null },
    variant: 0,
    actions: ['approve'],
    findings: [],
    library_suggestions: 0,
    ...over,
  };
}

/** @param {Partial<any>} over */
function issue(over) {
  return { code: 'assets.media_degraded', severity: 'warning', message: 'The picture could not be made; a placeholder is shown.', scene_id: 's2', beat_id: null, source: 'assets', fixable: false, ...over };
}

const AMBIGUOUS = issue({ code: 'video.ambiguous_submission', scene_id: 's3', message: 'A previous attempt may already have been billed.' });

const live = [];

function fresh() {
  for (const hd of live.splice(0)) {
    try {
      hd.destroy();
    } catch {
      /* gone */
    }
  }
  resetDom();
  closeAllModals();
  FakeEventSource.instances = [];
  localStorage.clear();
}

// --- issues panel -------------------------------------------------------------------------------

test('media issues: which actions each one offers', () => {
  assert.deepEqual(mediaActionsOf(issue({})), { rebuild: true, generateAgain: false, review: true });
  assert.deepEqual(mediaActionsOf(AMBIGUOUS), { rebuild: false, generateAgain: true, review: true });
  assert.deepEqual(mediaActionsOf(issue({ code: 'video.operation_lost' })), { rebuild: false, generateAgain: true, review: true });
  assert.deepEqual(mediaActionsOf(issue({ code: 'assets.cached_media_used', severity: 'info' })), { rebuild: false, generateAgain: false, review: true }, 'a note needs no rebuild');
  assert.deepEqual(mediaActionsOf(issue({ code: 'assets.tts_fallback' })), { rebuild: true, generateAgain: false, review: false }, 'a voice problem has no visual to review');
  assert.deepEqual(mediaActionsOf(issue({ source: 'lint' })), { rebuild: false, generateAgain: false, review: false });
  assert.deepEqual(mediaActionsOf(issue({ scene_id: null })), { rebuild: false, generateAgain: false, review: false });
});

test('issues panel: rebuild, generate again and review links for media issues', () => {
  fresh();
  const seen = [];
  const panel = createIssuesPanel({
    onSelect: () => {},
    onRebuildScene: (i) => seen.push(['rebuild', i.scene_id]),
    onGenerateAgain: (i) => seen.push(['again', i.scene_id]),
    reviewHref: (id) => `#/v/9/review?scene=${id}`,
  });
  document.body.appendChild(panel.el);
  panel.update([issue({}), AMBIGUOUS, issue({ code: 'board.too_many_items', source: 'lint', severity: 'warning' })], sampleScreenplay(), {});
  const rows = [...panel.el.querySelectorAll('.issue-row')];
  const rowOf = (code) => rows.find((r) => r.querySelector(`[data-code="${code}"]`));
  const degraded = rowOf('assets.media_degraded');
  const ambiguous = rowOf('video.ambiguous_submission');
  assert.ok(degraded.querySelector('[data-action="rebuild"]'));
  assert.equal(degraded.querySelector('[data-action="generate-again"]'), null);
  assert.ok(degraded.querySelector('[data-action="review-visual"]').getAttribute('href').endsWith('#/v/9/review?scene=s2'));
  assert.equal(ambiguous.querySelector('[data-action="rebuild"]'), null, 'a possibly paid video is never rebuilt with one click');
  assert.ok(ambiguous.querySelector('[data-action="generate-again"]'));
  assert.equal(rowOf('board.too_many_items').querySelector('[data-action="rebuild"]'), null);
  degraded.querySelector('[data-action="rebuild"]').click();
  ambiguous.querySelector('[data-action="generate-again"]').click();
  assert.deepEqual(seen, [['rebuild', 's2'], ['again', 's3']]);
  panel.update([issue({})], sampleScreenplay(), { busy: true });
  assert.ok(panel.el.querySelector('[data-action="rebuild"]').disabled, 'disabled while saving or starting a job');
});

test('issues panel: without the new callbacks nothing changes', () => {
  fresh();
  const panel = createIssuesPanel({ onSelect: () => {} });
  panel.update([issue({}), AMBIGUOUS], sampleScreenplay(), {});
  assert.equal(panel.el.querySelectorAll('[data-action="rebuild"], [data-action="generate-again"], [data-action="review-visual"]').length, 0);
});

// --- media control: choose from library ------------------------------------------------------------

test('media control: a library item becomes the override (picture-only controls ask for pictures)', async () => {
  fresh();
  assert.equal(libraryKindOf(['.png', '.jpg']), 'image');
  assert.equal(libraryKindOf(['.mp4', '.webm']), 'video');
  assert.equal(libraryKindOf(['.png', '.mp4']), undefined);
  const changes = [];
  const kinds = [];
  let next = { id: 4, asset_key: 'upload:pump', kind: 'image', title: 'Pump', url: '/media/pump.png', width: 640, height: 360 };
  const el = mediaUpload({
    purpose: 'poster',
    projectId: PID,
    assetKey: null,
    label: 'Poster',
    maxMb: 50,
    pickLibrary: async (kind) => {
      kinds.push(kind);
      return next;
    },
    onChange: (key, info) => changes.push([key, info]),
  });
  document.body.appendChild(el);
  byText(el, 'Choose from library').click();
  await waitFor(() => changes.length === 1);
  assert.deepEqual(kinds, ['image']);
  assert.equal(changes[0][0], 'upload:pump');
  assert.deepEqual(changes[0][1], { asset_key: 'upload:pump', url: '/media/pump.png', kind: 'image', width: 640, height: 360 });
  assert.match(el.textContent, /Using “Pump” from your library/);
  assert.ok(el.querySelector('img.upload-preview'));
  next = { id: 5, asset_key: 'upload:clip', kind: 'video', title: 'Clip', url: '/media/clip.mp4' };
  byText(el, 'Choose from library').click();
  await tick();
  await tick();
  assert.equal(changes.length, 1, 'a video is refused where only pictures fit');
  assert.match(el.querySelector('.field-error').textContent, /picture/);
  next = null;
  byText(el, 'Choose from library').click();
  await tick();
  assert.equal(changes.length, 1, 'closing the picker changes nothing');
});

test('media control: without a picker there is no library button', () => {
  fresh();
  const el = mediaUpload({ purpose: 'scene_media', projectId: PID, assetKey: null, label: 'Media', maxMb: 50, onChange: () => {} });
  assert.equal(byText(el, 'Choose from library'), null);
});

// --- the editor ------------------------------------------------------------------------------------

test('visual line: says where the visual came from and links to the review', () => {
  fresh();
  const el = visualInfo(sv(), '#/v/9/review?scene=s2');
  assert.match(el.textContent, /AI picture/);
  assert.match(el.textContent, /Made by pollinations \(flux\)/);
  assert.match(el.textContent, /Ready/);
  assert.match(el.textContent, /Approved/);
  assert.ok(el.querySelector('a').getAttribute('href').endsWith('#/v/9/review?scene=s2'));
  assert.match(visualInfo(sv({ review: { state: 'approved', stale: true } })).textContent, /Approved before a change/);
  // the server sends an approval made before a change as pending + stale
  assert.match(visualInfo(sv({ review: { state: 'pending', stale: true, note: null, updated_at: null } })).textContent, /Approved before a change/);
  assert.doesNotMatch(visualInfo(sv({ review: { state: 'pending', stale: false, note: null, updated_at: null } })).textContent, /Approved before a change/);
  assert.equal(visualInfo(sv({ kind: 'none', source: 'none', review: { state: 'pending', stale: false } })), null);
  assert.equal(visualInfo(null), null);
});

/**
 * @param {any} version
 * @param {Record<string, any>} [extra]
 */
function editorRoutes(version, extra = {}) {
  return mockFetch({
    [`GET /api/versions/${VID}`]: version,
    [`POST /api/versions/${VID}/lint`]: { issues: [], quality: { version: 1, repairs: [], registry: {} } },
    [`POST /api/versions/${VID}/timeline/preview`]: { status: 404, body: { detail: 'not built', code: 'not_found' } },
    [`GET /api/jobs?project_id=${PID}&limit=20`]: { items: [], total: 0 },
    ...extra,
  });
}

async function mountEditor(app, query = {}) {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const handle = await mountEditorView(container, { app, params: { id: PID, vid: VID }, query, signal: undefined });
  if (handle) live.push(handle);
  return { container, handle };
}

test('editor: "Review visuals" link and the inspector\'s visual line from the last build', async () => {
  fresh();
  editorRoutes(sampleVersion(), { [`GET /api/versions/${VID}/visual-review`]: { summary: {}, scenes: [sv()] } });
  const { container } = await mountEditor(fakeApp(), { scene: 's2' });
  const link = byText(container.querySelector('.editor-toolbar'), 'Review visuals', 'a');
  assert.ok(link && link.getAttribute('href').endsWith('#/v/9/review'));
  const line = await waitFor(() => container.querySelector('.inspector .visual-info'));
  assert.match(line.textContent, /Made by pollinations \(flux\)/);
  assert.ok(line.querySelector('a').getAttribute('href').endsWith('#/v/9/review?scene=s2'));
});

test('editor: without a visual review the inspector shows no visual line and no error', async () => {
  fresh();
  editorRoutes(sampleVersion());
  const app = fakeApp();
  const { container } = await mountEditor(app, { scene: 's2' });
  await tick(10);
  assert.ok(container.querySelector('.inspector [data-visual-info]'));
  assert.equal(container.querySelector('.inspector .visual-info'), null);
  assert.equal(app.rec.errors.length, 0);
});

test('editor: "Rebuild this scene" builds only that scene', async () => {
  fresh();
  const calls = editorRoutes(sampleVersion({ issues: [issue({})] }), {
    [`POST /api/versions/${VID}/build`]: { status: 202, body: { job: { id: 61, kind: 'build_assets', status: 'queued', progress: 0, version_id: VID, project_id: PID } } },
    'GET /api/jobs/61': { id: 61, kind: 'build_assets', status: 'running', progress: 0.3, version_id: VID, project_id: PID },
  });
  const { container } = await mountEditor(fakeApp(), { scene: 's2' });
  const rebuild = await waitFor(() => container.querySelector('.issues-panel [data-action="rebuild"]'));
  rebuild.click();
  await waitFor(() => calls.some((c) => c.method === 'POST' && c.path === `/api/versions/${VID}/build`));
  assert.deepEqual(jsonBody(calls.find((c) => c.path === `/api/versions/${VID}/build`)), { scene_ids: ['s2'] });
  closeAllModals();
});

test('editor: "Generate again…" asks before a possibly paid video is made again', async () => {
  fresh();
  const calls = editorRoutes(sampleVersion({ issues: [AMBIGUOUS] }), {
    [`POST /api/versions/${VID}/scenes/s3/visual`]: { revision: 4, scene: sv({ scene_id: 's3', index: 2 }), job_id: 62 },
    'GET /api/jobs/62': { id: 62, kind: 'build_assets', status: 'running', progress: 0.1, version_id: VID, project_id: PID },
  });
  const { container } = await mountEditor(fakeApp(), { scene: 's3' });
  const again = await waitFor(() => container.querySelector('.issues-panel [data-action="generate-again"]'));
  again.click();
  await waitFor(() => document.querySelector('.modal'));
  assert.match(document.querySelector('.modal').textContent, /paying for this video twice/);
  clickModal('Cancel');
  await tick();
  assert.ok(!calls.some((c) => c.path.endsWith('/visual')), 'nothing is sent without the confirmation');
  container.querySelector('.issues-panel [data-action="generate-again"]').click();
  await waitFor(() => document.querySelector('.modal'));
  clickModal('Generate again (may be paid twice)');
  await waitFor(() => calls.some((c) => c.path === '/api/jobs/62'));
  assert.deepEqual(jsonBody(calls.find((c) => c.path.endsWith('/scenes/s3/visual'))), { action: 'retry', confirm_paid: true, revision: 4 });
  closeAllModals();
});

// --- the project page ---------------------------------------------------------------------------------

const V9 = { id: 9, number: 1, label: '', status: 'ready', language: 'en-IN', revision: 4, built_revision: 4, timeline_stale: false, has_timeline: true, source_version_id: null, issue_counts: {}, created_at: '2026-09-01T10:00:00Z', updated_at: '2026-09-01T10:00:00Z' };
const V10 = { ...V9, id: 10, number: 2, status: 'generating', has_timeline: false };

function projectRoutes(review) {
  return mockFetch({
    'GET /api/projects/5': {
      project: { id: 5, title: "Ohm's Law", subject_name: 'BEE', unit_name: 'Circuits', session_number: 'S2', session_title: "Ohm's Law", language: 'en-IN', owner: { id: 7, username: 'teacher1' }, current_version: V9, active_job: null, created_at: V9.created_at, updated_at: V9.updated_at },
      versions: [V9, V10],
      sources: [],
      jobs: [],
    },
    'GET /api/projects/5/shares': { items: [] },
    'GET /api/versions/9/renders': { items: [] },
    'GET /api/versions/9/visual-review': review,
  });
}

async function mountProjectPage(app) {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const handle = await mountProject(container, { app, params: { id: 5 }, query: {} });
  if (handle) live.push(handle);
  await waitFor(() => container.querySelector('.page-header'));
  return container;
}

test('project page: "Review visuals" per version and how many visuals need attention', async () => {
  fresh();
  projectRoutes({ summary: {}, scenes: [sv({ status: 'failed' }), sv({ scene_id: 's3', status: 'ready' })] });
  const container = await mountProjectPage(fakeApp());
  const rows = [...container.querySelectorAll('tbody tr')];
  const v9 = rows.find((r) => r.textContent.includes('v1'));
  const v10 = rows.find((r) => r.textContent.includes('v2'));
  assert.ok(byText(v9, 'Review visuals', 'a').getAttribute('href').endsWith('#/v/9/review'));
  assert.equal(byText(v10, 'Review visuals', 'a'), null, 'a version still being written has nothing to review');
  const badge = await waitFor(() => v9.querySelector('[data-visual-attention] a.badge'));
  assert.equal(badge.textContent, '1 visual needs attention');
  assert.ok(badge.getAttribute('href').endsWith('#/v/9/review?filter=attention'));
});

test('project page: no badge when nothing needs attention or the review cannot be read', async () => {
  fresh();
  projectRoutes({ summary: {}, scenes: [sv()] });
  let container = await mountProjectPage(fakeApp());
  await tick(10);
  assert.equal(container.querySelector('[data-visual-attention] a'), null);
  fresh();
  const app = fakeApp();
  projectRoutes({ status: 404, body: { detail: 'not found', code: 'not_found' } });
  container = await mountProjectPage(app);
  await tick(10);
  assert.equal(container.querySelector('[data-visual-attention] a'), null);
  assert.equal(app.rec.errors.length, 0);
});
