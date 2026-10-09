import { resetDom, mockFetch, tick, window, FakeEventSource } from './_dom.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import { mount } from '../../js/studio/views/projectDetail.js';
import { closeAllModals } from '../../js/studio/components/modal.js';
import { fakeApp, deferred, byText, clickModal, waitFor, jsonBody } from './_views.js';
import { sampleMeta } from './fixtures.js';

const V9 = { id: 9, number: 1, label: '', status: 'ready', language: 'en-IN', revision: 4, built_revision: 4, timeline_stale: false, has_timeline: true, source_version_id: null, issue_counts: {}, created_at: '2026-09-01T10:00:00Z', updated_at: '2026-09-01T10:00:00Z' };
const V10 = { ...V9, id: 10, number: 2, language: 'ta-IN', source_version_id: 9 };

function detail() {
  return {
    project: { id: 5, title: "Ohm's Law", subject_name: 'BEE', unit_name: 'Circuits', session_number: 'S2', session_title: "Ohm's Law", language: 'en-IN', owner: { id: 7, username: 'teacher1' }, current_version: V9, active_job: null, created_at: V9.created_at, updated_at: V9.updated_at },
    versions: [V9, V10],
    sources: [{ id: 1, filename: 'ohm.pdf', mime: 'application/pdf', size_bytes: 2048, page_count: 3, created_at: V9.created_at }],
    jobs: [],
  };
}

const render = (id, vid) => ({ id, version_id: vid, status: 'succeeded', language: 'en-IN', duration_s: 60, built_revision: 4, created_at: V9.created_at, downloads: { video: `/api/renders/${id}/download?file=video` }, chapters_text: null, job: null });

function fresh() {
  resetDom();
  closeAllModals();
  FakeEventSource.instances = [];
}

async function mountPage(app) {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const handle = await mount(container, { app, params: { id: 5 }, query: {} });
  await waitFor(() => container.querySelector('.page-header'));
  return { container, handle };
}

/** Open a menuButton by its label and click an item. */
async function menuPick(root, menuLabel, item) {
  byText(root, menuLabel, 'button').click();
  await tick();
  byText(document.body, item, '[role="menuitem"]').click();
  await tick();
}

test('share links: create (with expiry and pinned version) and revoke', async () => {
  fresh();
  let shares = [];
  const calls = mockFetch({
    'GET /api/projects/5': detail(),
    'GET /api/projects/5/shares': () => ({ items: shares }),
    'GET /api/versions/9/renders': { items: [] },
    'POST /api/projects/5/shares': ({ init }) => {
      const body = JSON.parse(init.body);
      shares = [{ token: 'tok1', url: '/s/tok1', version_id: body.version_id ?? null, created_at: V9.created_at, expires_at: '2026-10-31T00:00:00Z', revoked_at: null, view_count: 0 }];
      return { status: 201, body: { token: 'tok1', url: '/s/tok1', version_id: body.version_id ?? null, expires_at: '2026-10-31T00:00:00Z' } };
    },
    'DELETE /api/shares/tok1': () => {
      shares = [{ ...shares[0], revoked_at: '2026-10-02T00:00:00Z' }];
      return { status: 204, body: null };
    },
  });
  const app = fakeApp();
  const { container, handle } = await mountPage(app);
  const section = byText(container, 'Share with students', 'h2').closest('section');
  const [versionSel, expirySel] = section.querySelectorAll('select');
  versionSel.value = '9';
  expirySel.value = '7';
  byText(section, 'Create share link').click();
  await waitFor(() => container.querySelector('a[href$="/s/tok1"]'));
  const post = calls.find((c) => c.method === 'POST' && c.path === '/api/projects/5/shares');
  assert.deepEqual(jsonBody(post), { version_id: 9, expires_in_days: 7 });
  assert.ok(app.rec.toasts.some((t) => /Share link created/.test(t.message)));

  const row = container.querySelector('a[href$="/s/tok1"]').closest('tr');
  assert.match(row.textContent, /v1/);
  byText(row, 'Revoke').click();
  await waitFor(() => document.querySelector('.modal'));
  clickModal('Revoke');
  await waitFor(() => /Revoked/.test(container.querySelector('a[href$="/s/tok1"]').closest('tr').textContent));
  assert.ok(calls.some((c) => c.method === 'DELETE' && c.path === '/api/shares/tok1'));
  const revokedRow = container.querySelector('a[href$="/s/tok1"]').closest('tr');
  assert.equal(byText(revokedRow, 'Revoke').disabled, true);
  assert.equal(byText(revokedRow, 'Copy link').disabled, true);
  handle.destroy();
});

test('renders: a late response for an older picker value never replaces the current version', async () => {
  fresh();
  const slow10 = deferred();
  mockFetch({
    'GET /api/projects/5': detail(),
    'GET /api/projects/5/shares': { items: [] },
    'GET /api/versions/9/renders': { items: [render(1, 9)] },
    'GET /api/versions/10/renders': () => slow10.promise,
  });
  const { container, handle } = await mountPage(fakeApp());
  const section = container.querySelector('[data-section="renders"]');
  await waitFor(() => section.querySelector('[data-render-id="1"]'));
  const picker = section.querySelector('select');
  picker.value = '10';
  picker.dispatchEvent(new window.Event('change'));
  picker.value = '9';
  picker.dispatchEvent(new window.Event('change'));
  await waitFor(() => section.querySelector('[data-render-id="1"]'));
  slow10.resolve({ items: [render(2, 10)] });
  await tick(20);
  assert.ok(section.querySelector('[data-render-id="1"]'));
  assert.equal(section.querySelector('[data-render-id="2"]'), null, "v2's late answer is discarded");
  handle.destroy();
});

test('edit details limits the session title to the API maximum (255)', async () => {
  fresh();
  mockFetch({ 'GET /api/projects/5': detail(), 'GET /api/projects/5/shares': { items: [] }, 'GET /api/versions/9/renders': { items: [] } });
  const { container, handle } = await mountPage(fakeApp());
  await menuPick(container, 'More', 'Edit details');
  const inputs = [...document.querySelectorAll('.modal input')];
  const maxes = inputs.map((i) => Number(i.getAttribute('maxlength')));
  assert.ok(maxes.every((m) => m > 0 && m <= 255), `all within ProjectPatch limits: ${maxes}`);
  const sessionTitle = inputs[4];
  assert.equal(sessionTitle.value, "Ohm's Law");
  assert.equal(sessionTitle.getAttribute('maxlength'), '255');
  clickModal('Cancel');
  handle.destroy();
});

test('translate: dismissing the dialog while the request runs does not lose the created job', async () => {
  fresh();
  const translate = deferred();
  const calls = mockFetch({
    'GET /api/projects/5': detail(),
    'GET /api/projects/5/shares': { items: [] },
    'GET /api/versions/9/renders': { items: [] },
    'POST /api/versions/9/translate': () => translate.promise,
  });
  const { container, handle } = await mountPage(fakeApp());
  const row = container.querySelector('tbody tr');
  await menuPick(row, 'Actions', 'Translate');
  const dialog = document.querySelector('.modal');
  assert.match(dialog.textContent, /Translate v1/);
  clickModal('Translate');
  await tick();
  // Escape, the close button and a backdrop click are ignored while busy.
  document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
  dialog.querySelector('.modal-close').click();
  dialog.parentElement.dispatchEvent(new window.MouseEvent('mousedown', { bubbles: true }));
  assert.ok(dialog.isConnected, 'still open while the request runs');
  assert.equal(dialog.querySelector('.modal-close').disabled, true);
  const loadsBefore = calls.filter((c) => c.path === '/api/projects/5').length;
  translate.resolve({ status: 202, body: { version: { ...V10, id: 11, number: 3 }, job: { id: 30, kind: 'translate', status: 'queued', progress: 0 } } });
  await waitFor(() => !dialog.isConnected);
  // The page reloads and follows the new job in its own dialog.
  await waitFor(() => calls.filter((c) => c.path === '/api/projects/5').length > loadsBefore);
  await waitFor(() => /Translating to/.test(document.querySelector('.modal')?.textContent || ''));
  assert.equal(FakeEventSource.instances.at(-1).url, '/api/jobs/30/stream');
  closeAllModals();
  handle.destroy();
});

/** The regenerate modal's control for a GenerationOptions field, found by one of its option values. */
function modalSelectWith(value) {
  return [...document.querySelectorAll('.modal select')].find((s) => [...s.options].some((o) => o.value === value)) || null;
}

test('regenerate starts from the lecture\'s stored options and posts its engine back', async () => {
  fresh();
  const stored = {
    ...sampleMeta().generation_defaults,
    llm_provider: 'anthropic',
    language: 'ta-IN',
    target_minutes: 12,
    depth: 'deep',
    extra_instructions: 'Use examples from Indian power grids.',
    subject_name: 'BEE',
    tts_voice: 'ta-IN-PallaviNeural',
  };
  let posted = null;
  mockFetch({
    'GET /api/projects/5': { ...detail(), options: stored },
    'GET /api/projects/5/shares': { items: [] },
    'GET /api/versions/9/renders': { items: [] },
    'POST /api/projects/5/regenerate': ({ init }) => {
      posted = JSON.parse(init.body);
      return { status: 201, body: { version: { ...V10, id: 11, number: 3, status: 'generating' }, job: { id: 31, kind: 'generate_lecture', status: 'queued', progress: 0 } } };
    },
  });
  const { container, handle } = await mountPage(fakeApp());
  await menuPick(container, 'More', 'Regenerate lecture');
  await waitFor(() => document.querySelector('.modal select'));
  assert.equal(modalSelectWith('anthropic').value, 'anthropic', 'the stored engine is preselected, not the server default');
  assert.equal(modalSelectWith('hi-IN').value, 'ta-IN');
  assert.equal(modalSelectWith('deep').value, 'deep');
  assert.equal(modalSelectWith('ta-IN-PallaviNeural').value, 'ta-IN-PallaviNeural');
  clickModal('Regenerate');
  await waitFor(() => posted);
  assert.equal(posted.options.llm_provider, 'anthropic');
  assert.equal(posted.options.language, 'ta-IN');
  assert.equal(posted.options.target_minutes, 12);
  assert.equal(posted.options.depth, 'deep');
  assert.equal(posted.options.extra_instructions, 'Use examples from Indian power grids.');
  assert.equal(posted.options.subject_name, 'BEE');
  assert.equal(posted.options.tts_voice, 'ta-IN-PallaviNeural');
  closeAllModals();
  handle.destroy();
});

test('regenerate never switches silently away from a stored engine that lost its key', async () => {
  fresh();
  const calls = mockFetch({
    'GET /api/projects/5': { ...detail(), options: { ...sampleMeta().generation_defaults, llm_provider: 'openai' } }, // not configured in sampleMeta
    'GET /api/projects/5/shares': { items: [] },
    'GET /api/versions/9/renders': { items: [] },
  });
  const { container, handle } = await mountPage(fakeApp());
  await menuPick(container, 'More', 'Regenerate lecture');
  await waitFor(() => document.querySelector('.modal select'));
  assert.equal(modalSelectWith('openai').value, 'openai');
  clickModal('Regenerate');
  await waitFor(() => /not configured on the server/.test(document.querySelector('.modal').textContent));
  assert.ok(!calls.some((c) => c.method === 'POST'), 'nothing is posted until the teacher picks another engine');
  closeAllModals();
  handle.destroy();
});

test('renders: the preflight list shows what the video would show differently, before rendering', async () => {
  fresh();
  mockFetch({
    'GET /api/projects/5': detail(),
    'GET /api/projects/5/shares': { items: [] },
    'GET /api/versions/9/renders': { items: [] },
    'GET /api/versions/9/render/preflight': {
      version_id: 9,
      has_timeline: true,
      timeline_stale: false,
      blocking: true,
      items: [
        { scene_index: 1, scene_id: 's2', title: 'Ohm', reason: 'no_audio', blocking: true, message: 'No narration audio: the scene would be silent.' },
        { scene_index: 3, scene_id: 's4', title: '', reason: 'panel_media_missing', blocking: false, message: 'Only the panel title is shown.' },
      ],
    },
  });
  const { container, handle } = await mountPage(fakeApp());
  const section = container.querySelector('[data-section="renders"]');
  await waitFor(() => section.querySelector('[data-preflight]'));
  const notice = section.querySelector('[data-preflight]');
  assert.equal(notice.getAttribute('data-preflight'), 'blocking');
  assert.equal(notice.hasAttribute('open'), true, 'silent scenes are shown expanded');
  assert.match(notice.querySelector('summary').textContent, /some scenes would be silent in the video \(2\)/);
  const items = [...notice.querySelectorAll('li')].map((li) => li.textContent);
  assert.deepEqual(items, ['Scene 2 (Ohm): No narration audio: the scene would be silent.', 'Scene 4: Only the panel title is shown.']);
  assert.match(section.textContent, /No renders yet/);
  handle.destroy();
});

test('renders: the quality check is listed under its own heading, apart from how the video differs', async () => {
  fresh();
  mockFetch({
    'GET /api/projects/5': detail(),
    'GET /api/projects/5/shares': { items: [] },
    'GET /api/versions/9/renders': { items: [] },
    'GET /api/versions/9/render/preflight': {
      version_id: 9,
      has_timeline: true,
      timeline_stale: false,
      blocking: false,
      items: [],
      quality: [
        { scene_index: 0, scene_id: 's1', title: 'Intro', reason: 'quality', blocking: false, message: 'Too many items.', severity: 'error', code: 'board.too_many_items' },
        { scene_index: null, scene_id: null, title: '', reason: 'quality', blocking: false, message: '“AC” stands for two things.', severity: 'warning', code: 'abbreviation_conflict' },
        { scene_index: null, scene_id: null, title: '', reason: 'quality', blocking: false, message: '3 more error(s) or warning(s): see Issues in the editor.', severity: 'warning', code: '' },
      ],
    },
  });
  const { container, handle } = await mountPage(fakeApp());
  const section = container.querySelector('[data-section="renders"]');
  await waitFor(() => section.querySelector('.render-quality'));
  assert.equal(section.querySelector('[data-preflight]'), null, 'no "video will differ" notice for quality items');
  const notice = section.querySelector('.render-quality');
  assert.equal(notice.getAttribute('data-quality'), '2');
  assert.match(notice.querySelector('summary').textContent, /quality check found things to review \(2\)/);
  const items = [...notice.querySelectorAll('li')].map((li) => li.textContent);
  assert.deepEqual(items, ['Needs fixing: Scene 1 (Intro): Too many items.', 'Please check: Whole lecture: “AC” stands for two things.', '3 more error(s) or warning(s): see Issues in the editor.']);
  handle.destroy();
});

test('renders: quality items an older server lists among the preflight items get the same heading', async () => {
  fresh();
  mockFetch({
    'GET /api/projects/5': detail(),
    'GET /api/projects/5/shares': { items: [] },
    'GET /api/versions/9/renders': { items: [] },
    'GET /api/versions/9/render/preflight': {
      version_id: 9,
      has_timeline: true,
      timeline_stale: false,
      blocking: false,
      items: [
        { scene_index: 3, scene_id: 's4', title: '', reason: 'panel_media_missing', blocking: false, message: 'Only the panel title is shown.' },
        { scene_index: 1, scene_id: 's2', title: 'Ohm', reason: 'quality', blocking: false, message: 'Check the symbol.', severity: 'warning', code: 'formula.symbol_conflict' },
      ],
    },
  });
  const { container, handle } = await mountPage(fakeApp());
  const section = container.querySelector('[data-section="renders"]');
  await waitFor(() => section.querySelector('.render-quality'));
  assert.deepEqual([...section.querySelectorAll('[data-preflight] li')].map((li) => li.textContent), ['Scene 4: Only the panel title is shown.']);
  assert.deepEqual([...section.querySelectorAll('.render-quality li')].map((li) => li.textContent), ['Please check: Scene 2 (Ohm): Check the symbol.']);
  handle.destroy();
});

test('renders: no preflight notice when the lecture renders as edited (or the check is unavailable)', async () => {
  fresh();
  mockFetch({
    'GET /api/projects/5': detail(),
    'GET /api/projects/5/shares': { items: [] },
    'GET /api/versions/9/renders': { items: [render(1, 9)] },
    'GET /api/versions/9/render/preflight': { version_id: 9, has_timeline: true, timeline_stale: false, blocking: false, items: [] },
    'GET /api/versions/10/renders': { items: [] },
    // v2: no preflight route mocked -> 404, ignored
  });
  const { container, handle } = await mountPage(fakeApp());
  const section = container.querySelector('[data-section="renders"]');
  await waitFor(() => section.querySelector('[data-render-id="1"]'));
  assert.equal(section.querySelector('[data-preflight]'), null);
  const picker = section.querySelector('select');
  picker.value = '10';
  picker.dispatchEvent(new window.Event('change'));
  await waitFor(() => /No renders yet/.test(section.textContent));
  assert.equal(section.querySelector('[data-preflight]'), null);
  handle.destroy();
});

test('renders: each render shows the server-side video check and its fallback scenes', async () => {
  fresh();
  const good = {
    ...render(1, 9),
    options: {
      burn_captions: false,
      qa: { ok: true, frames: 1800, fps: 30, audible: true, has_audio: true, problems: [], warnings: [] },
      warnings: [{ scene_index: 0, scene_id: 's1', title: 'Intro', reason: 'simulation_without_media', blocking: false, message: 'The animation is not available.' }],
    },
  };
  const noted = { ...render(2, 9), options: { qa: { ok: true, frames: 90, fps: 30, audible: true, problems: [], warnings: ['black bars at the top edge(s)'] } } };
  const legacy = { ...render(3, 9), options: { burn_captions: true } };
  mockFetch({
    'GET /api/projects/5': detail(),
    'GET /api/projects/5/shares': { items: [] },
    'GET /api/versions/9/renders': { items: [good, noted, legacy] },
  });
  const { container, handle } = await mountPage(fakeApp());
  const section = container.querySelector('[data-section="renders"]');
  await waitFor(() => section.querySelector('[data-render-id="3"]'));
  const rows = [...section.querySelectorAll('.render-row')];
  const qa0 = rows[0].querySelector('[data-qa]');
  assert.equal(qa0.getAttribute('data-qa'), 'ok');
  assert.equal(qa0.textContent, 'Video checked: 1800 frames at 30 fps, sound OK.');
  const details = rows[0].querySelector('.render-warnings');
  assert.match(details.querySelector('summary').textContent, /1 scene rendered differently/);
  assert.equal(details.querySelector('li').textContent, 'Scene 1 (Intro): The animation is not available.');
  assert.match(rows[1].querySelector('[data-qa]').textContent, /Video check: black bars at the top edge/);
  assert.ok(rows[1].querySelector('[data-qa]').classList.contains('warning'));
  assert.equal(rows[2].querySelector('[data-qa]'), null, 'renders made before the check show nothing extra');
  assert.equal(rows[2].querySelector('.render-warnings'), null);
  handle.destroy();
});
