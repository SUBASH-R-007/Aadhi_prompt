import { resetDom, mockFetch, tick, window } from './_dom.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import { mount, aiDescribeEnabled, uploadSummaryText, MAX_UPLOAD_BATCH } from '../../js/studio/views/library.js';
import { closeAllModals } from '../../js/studio/components/modal.js';
import { resolveHash, href, guardRoute, ROUTES } from '../../js/studio/router.js';
import { startStudio } from '../../js/studio/app.js';
import { sampleMeta, sampleUser } from './fixtures.js';
import { fakeApp, deferred, byText, clickModal, waitFor, jsonBody, typeInto } from './_views.js';

function fresh() {
  closeAllModals();
  resetDom();
}

/** A library item as GET /api/library lists it. */
function libItem(over = {}) {
  return {
    id: 1,
    asset_key: 'upload/3f2a',
    kind: 'image',
    title: 'Resistor diagram',
    description: 'A resistor in a simple circuit.',
    keywords: ['resistor', 'circuit'],
    source: 'upload',
    width: 800,
    height: 600,
    duration_s: null,
    url: '/media/upload/3f2a.png?sig=abc',
    poster_url: null,
    created_at: '2026-10-01T10:00:00Z',
    updated_at: '2026-10-01T10:00:00Z',
    last_used_at: null,
    used_in: 2,
    prompt: null,
    provider: null,
    model: null,
    ...over,
  };
}

const GENERATED = libItem({
  id: 2,
  asset_key: 'video/9e1',
  kind: 'video',
  title: 'Electrons drifting',
  description: '',
  keywords: [],
  source: 'generated',
  width: 1280,
  height: 720,
  duration_s: 8,
  url: '/media/video/9e1.mp4?sig=v',
  poster_url: '/media/poster/9e1.jpg?sig=p',
  used_in: 0,
  prompt: 'Electrons drift slowly through a copper wire, glowing blue.',
  provider: 'veo',
  model: 'veo-2.0-generate-001',
});

const listing = (items, total = items.length) => ({ items, total });
const libraryGets = (calls) => calls.filter((c) => c.method === 'GET' && c.path.startsWith('/api/library'));
const query = (call) => new URLSearchParams(call.path.split('?')[1] || '');
const topModal = () => {
  const all = document.querySelectorAll('.modal');
  return all[all.length - 1] || null;
};

/** Mount the page; returns the container, handle and app. */
async function mountPage(opts = {}) {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const app = opts.app || fakeApp();
  const handle = await mount(container, { app, params: {}, query: opts.query || {} });
  return { container, handle, app };
}

/** Drop files on the page. */
function dropFiles(container, files) {
  const ev = new window.Event('drop', { bubbles: true, cancelable: true });
  ev.dataTransfer = { files, types: ['Files'] };
  container.querySelector('.library-grid').dispatchEvent(ev);
}

// --- route and navigation -------------------------------------------------------------------

test('route: #/library is a signed-in page with its query kept', () => {
  const r = resolveHash('#/library?q=ohm&kind=image');
  assert.equal(r.route.name, 'library');
  assert.deepEqual(r.query, { q: 'ohm', kind: 'image' });
  assert.equal(href('library', {}, { q: 'ohm law', kind: '', source: 'upload' }), '#/library?q=ohm+law&source=upload');
  const route = ROUTES.find((x) => x.name === 'library');
  assert.deepEqual(guardRoute(route, null), { redirect: '#/login' });
  assert.deepEqual(guardRoute(route, { role: 'editor', must_change_password: false }), { ok: true });
});

test('the top bar links to the Library and the page opens there', async () => {
  fresh();
  window.history.replaceState(null, '', '#/library');
  mockFetch({
    'GET /api/auth/me': { user: sampleUser() },
    'GET /api/meta': sampleMeta(),
    'GET /api/library': listing([libItem()]),
  });
  const root = document.createElement('div');
  document.body.appendChild(root);
  const { dispose } = await startStudio(root);
  try {
    await waitFor(() => root.querySelector('.library-card'));
    const link = [...root.querySelectorAll('.nav-link')].find((a) => a.textContent.trim() === 'Library');
    assert.ok(link, 'nav link');
    assert.match(link.getAttribute('href'), /#\/library$/);
    assert.equal(link.getAttribute('aria-current'), 'page');
    assert.equal(root.querySelector('h1').textContent, 'Library');
    assert.match(document.title, /^Library/);
  } finally {
    dispose();
  }
});

// --- list -----------------------------------------------------------------------------------

test('cards show the picture or poster, title, badges, keywords and use count', async () => {
  fresh();
  const calls = mockFetch({ 'GET /api/library': listing([libItem(), GENERATED]) });
  const { container, handle } = await mountPage();
  await waitFor(() => container.querySelectorAll('.library-card').length === 2);
  const [first, second] = container.querySelectorAll('.library-card');
  assert.equal(first.querySelector('.library-title').textContent, 'Resistor diagram');
  assert.match(first.textContent, /Picture/);
  assert.match(first.textContent, /Uploaded/);
  assert.match(first.textContent, /800 × 600/);
  assert.deepEqual([...first.querySelectorAll('.library-keywords .chip')].map((c) => c.textContent), ['resistor', 'circuit']);
  assert.match(first.textContent, /Added to 2 lectures/);
  assert.match(first.querySelector('img').getAttribute('src'), /\/media\/upload\/3f2a\.png\?sig=abc$/);
  assert.match(second.textContent, /Made by AI/);
  assert.match(second.textContent, /Not added to a lecture yet/);
  assert.match(second.querySelector('img').getAttribute('src'), /\/media\/poster\/9e1\.jpg/);
  assert.equal(container.querySelector('video'), null, 'no video is loaded in the grid');
  assert.match(container.querySelector('.list-footer').textContent, /Showing 2 of 2/);
  assert.equal(query(libraryGets(calls)[0]).toString(), 'limit=48&offset=0');
  // keyboard: every action is a real button with a name that says which item it is for
  assert.equal(first.querySelector('.library-thumb-button').getAttribute('aria-label'), 'Preview: Resistor diagram');
  assert.equal(first.querySelector('[data-action="edit"]').getAttribute('aria-label'), 'Edit details: Resistor diagram');
  assert.equal(first.querySelector('[data-action="remove"]').getAttribute('aria-label'), 'Remove: Resistor diagram');
  handle.destroy();
});

test('server strings are shown as text, never as HTML', async () => {
  fresh();
  const evil = '<img src=x onerror="window.pwned=1"><b>bold</b>';
  mockFetch({ 'GET /api/library': listing([libItem({ title: evil, description: evil, keywords: [evil] })]) });
  const { container, handle } = await mountPage();
  await waitFor(() => container.querySelector('.library-card'));
  const card = container.querySelector('.library-card');
  assert.equal(card.querySelector('.library-title').textContent, evil);
  assert.equal(card.querySelector('b'), null);
  assert.equal(card.querySelectorAll('img').length, 1);
  assert.equal(window.pwned, undefined);
  handle.destroy();
});

test('filters from the URL are used, and an empty filtered list offers to clear them', async () => {
  fresh();
  const hashes = [];
  const calls = mockFetch({
    'GET /api/library': ({ path }) => (query({ path }).get('q') ? listing([]) : listing([libItem()])),
  });
  const app = fakeApp({ replaceHash: (h) => hashes.push(h) });
  const { container, handle } = await mountPage({ app, query: { q: 'zebra', kind: 'video', source: 'bogus' } });
  await waitFor(() => /Nothing matches/.test(container.textContent));
  const first = query(libraryGets(calls)[0]);
  assert.equal(first.get('q'), 'zebra');
  assert.equal(first.get('kind'), 'video');
  assert.equal(first.get('source'), null, 'an unknown source is ignored');
  assert.equal(container.querySelector('input[type="search"]').value, 'zebra');
  byText(container, 'Clear search and filters').click();
  await waitFor(() => container.querySelector('.library-card'));
  assert.equal(query(libraryGets(calls)[1]).toString(), 'limit=48&offset=0');
  assert.deepEqual(hashes, ['#/library']);
  handle.destroy();
});

test('an empty library explains where items come from', async () => {
  fresh();
  mockFetch({ 'GET /api/library': listing([]) });
  const { container, handle } = await mountPage();
  await waitFor(() => /Your library is empty/.test(container.textContent));
  assert.match(container.textContent, /pictures and clips Aadhi makes for your lectures/);
  handle.destroy();
});

test('search is debounced and kept in the URL; the filters reload the list', async () => {
  fresh();
  const hashes = [];
  const calls = mockFetch({ 'GET /api/library': listing([libItem()]) });
  const { container, handle } = await mountPage({ app: fakeApp({ replaceHash: (h) => hashes.push(h) }) });
  await waitFor(() => container.querySelector('.library-card'));
  const search = container.querySelector('input[type="search"]');
  typeInto(search, 'o');
  typeInto(search, 'ohm');
  await tick(50);
  assert.equal(libraryGets(calls).length, 1, 'nothing sent while typing');
  await waitFor(() => libraryGets(calls).length === 2, 2000);
  assert.equal(query(libraryGets(calls)[1]).get('q'), 'ohm');
  assert.equal(hashes.at(-1), '#/library?q=ohm');
  const [kindSelect, sourceSelect] = container.querySelectorAll('.library-toolbar select');
  kindSelect.value = 'image';
  kindSelect.dispatchEvent(new window.Event('change', { bubbles: true }));
  sourceSelect.value = 'generated';
  sourceSelect.dispatchEvent(new window.Event('change', { bubbles: true }));
  await waitFor(() => libraryGets(calls).length === 4);
  const last = query(libraryGets(calls)[3]);
  assert.equal(last.get('kind'), 'image');
  assert.equal(last.get('source'), 'generated');
  assert.equal(last.get('q'), 'ohm');
  assert.equal(hashes.at(-1), '#/library?q=ohm&kind=image&source=generated');
  handle.destroy();
});

test('Load more appends the next page and focuses its first item', async () => {
  fresh();
  const first = Array.from({ length: 48 }, (_, i) => libItem({ id: i + 1, title: `Item ${i + 1}` }));
  const calls = mockFetch({
    'GET /api/library': ({ path }) => (query({ path }).get('offset') === '0' ? listing(first, 49) : listing([libItem({ id: 49, title: 'Item 49' })], 49)),
  });
  const { container, handle } = await mountPage();
  await waitFor(() => container.querySelectorAll('.library-card').length === 48);
  byText(container, 'Load more').click();
  await waitFor(() => container.querySelectorAll('.library-card').length === 49);
  assert.equal(query(libraryGets(calls)[1]).get('offset'), '48');
  assert.equal(document.activeElement.getAttribute('aria-label'), 'Edit details: Item 49');
  assert.equal(byText(container, 'Load more'), null);
  handle.destroy();
});

test('a failed load shows the reason with a retry', async () => {
  fresh();
  let fail = true;
  mockFetch({ 'GET /api/library': () => (fail ? { status: 500, body: { detail: 'boom' } } : listing([libItem()])) });
  const { container, handle } = await mountPage();
  await waitFor(() => container.querySelector('.error-state'));
  assert.match(container.textContent, /server had a problem/);
  fail = false;
  byText(container, 'Try again').click();
  await waitFor(() => container.querySelector('.library-card'));
  handle.destroy();
});

// --- preview ----------------------------------------------------------------------------------

test('preview: a generated video plays with its poster and says how it was made; Edit details opens the editor', async () => {
  fresh();
  mockFetch({ 'GET /api/library': listing([GENERATED]) });
  const { container, handle } = await mountPage();
  await waitFor(() => container.querySelector('.library-card'));
  container.querySelector('.library-thumb-button').click();
  await waitFor(() => topModal());
  const modal = topModal();
  assert.equal(modal.querySelector('.modal-title').textContent, 'Electrons drifting');
  const video = modal.querySelector('video');
  await tick();
  assert.equal(document.activeElement, modal.querySelector('.library-preview-frame'), 'focus starts on the media, so a tall dialog opens at its top');
  assert.ok(video.hasAttribute('controls'));
  assert.match(video.getAttribute('src'), /9e1\.mp4\?sig=v$/);
  assert.match(video.getAttribute('poster'), /9e1\.jpg\?sig=p$/);
  assert.match(modal.textContent, /Electrons drift slowly through a copper wire/);
  assert.match(modal.textContent, /veo · veo-2\.0-generate-001/);
  assert.match(modal.textContent, /Not added to a lecture yet/);
  clickModal('Edit details');
  await waitFor(() => topModal() && topModal().querySelector('.modal-title').textContent === 'Edit details');
  assert.match(topModal().textContent, /Made by AI from this description/);
  closeAllModals();
  handle.destroy();
});

// --- edit details -----------------------------------------------------------------------------

/** Open the edit dialog of the first card. */
async function openEdit(container) {
  container.querySelector('[data-action="edit"]').click();
  await waitFor(() => topModal() && topModal().querySelector('.library-edit'));
  const modal = topModal();
  return {
    modal,
    title: modal.querySelector('.library-edit-fields input.input:not(.chips-input)'),
    description: modal.querySelector('textarea'),
    keyword: modal.querySelector('.chips-input'),
    chips: () => [...modal.querySelectorAll('.chips .chip .chip-text')].map((c) => c.textContent),
  };
}

/** Type a keyword and press Enter. */
function addKeyword(inputEl, word) {
  inputEl.value = word;
  inputEl.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
}

test('edit: only the changed fields are saved, and the card shows them', async () => {
  fresh();
  const calls = mockFetch({
    'GET /api/library': listing([libItem()]),
    'PATCH /api/library/1': ({ init }) => libItem({ ...JSON.parse(init.body) }),
  });
  const { container, handle, app } = await mountPage();
  await waitFor(() => container.querySelector('.library-card'));
  const form = await openEdit(container);
  assert.equal(form.title.value, 'Resistor diagram');
  assert.deepEqual(form.chips(), ['resistor', 'circuit']);
  assert.match(form.modal.textContent, /31 of 1000 characters/);
  assert.equal(form.modal.querySelector('.library-ai'), null, 'no AI suggestions unless the server allows them');
  typeInto(form.title, '  Resistor   symbol ');
  addKeyword(form.keyword, 'Ohm, RESISTOR');
  addKeyword(form.keyword, 'ohm');
  assert.deepEqual(form.chips(), ['resistor', 'circuit', 'Ohm']);
  assert.match(form.modal.textContent, /“ohm” is already a keyword/);
  clickModal('Save');
  await waitFor(() => !topModal());
  const sent = calls.find((c) => c.method === 'PATCH');
  assert.deepEqual(jsonBody(sent), { title: 'Resistor symbol', keywords: ['resistor', 'circuit', 'Ohm'] });
  const card = container.querySelector('.library-card');
  assert.equal(card.querySelector('.library-title').textContent, 'Resistor symbol');
  assert.deepEqual([...card.querySelectorAll('.library-keywords .chip')].map((c) => c.textContent), ['resistor', 'circuit', 'Ohm']);
  assert.ok(app.rec.toasts.some((t) => t.message === 'Details saved.'));
  assert.equal(document.activeElement, card.querySelector('[data-action="edit"]'), 'focus returns to the card');
  handle.destroy();
});

test('edit: a title is required, nothing changed saves nothing, and a server refusal is shown', async () => {
  fresh();
  let refuse = true;
  const calls = mockFetch({
    'GET /api/library': listing([libItem()]),
    'PATCH /api/library/1': () =>
      refuse ? { status: 422, body: { detail: 'The description is too long.', code: 'validation_error' } } : libItem({ description: 'New' }),
  });
  const { container, handle } = await mountPage();
  await waitFor(() => container.querySelector('.library-card'));
  let form = await openEdit(container);
  typeInto(form.title, '   ');
  clickModal('Save');
  await tick();
  assert.match(form.modal.textContent, /Give it a title/);
  assert.ok(topModal(), 'still open');
  typeInto(form.title, 'Resistor diagram');
  clickModal('Save');
  await waitFor(() => !topModal());
  assert.equal(calls.filter((c) => c.method === 'PATCH').length, 0, 'unchanged: no request');

  form = await openEdit(container);
  typeInto(form.description, 'New');
  clickModal('Save');
  await waitFor(() => !form.modal.querySelector('.modal-error').hidden);
  assert.match(form.modal.querySelector('.modal-error').textContent, /too long/);
  refuse = false;
  clickModal('Save');
  await waitFor(() => !topModal());
  assert.deepEqual(jsonBody(calls.filter((c) => c.method === 'PATCH').at(-1)), { description: 'New' });
  handle.destroy();
});

test('edit: "Suggest with AI" fills the fields when the server allows it, and Undo brings the teacher’s words back', async () => {
  fresh();
  assert.equal(aiDescribeEnabled(sampleMeta()), false);
  const meta = sampleMeta();
  meta.library = { auto_save_generated: true, ai_describe_enabled: true };
  assert.equal(aiDescribeEnabled(meta), true);
  let answer = 'ok';
  const calls = mockFetch({
    'GET /api/library': listing([libItem({ keywords: ['resistor'] })]),
    'POST /api/library/1/describe': () =>
      answer === 'off'
        ? { status: 403, body: { detail: 'Turned off', code: 'feature_disabled' } }
        : answer === 'failed'
          ? { status: 502, body: { detail: 'The AI engine could not describe this item. Try again later.', code: 'describe_failed' } }
          : { title: 'Resistor in series', description: 'A resistor drawn in a series circuit.', keywords: ['Resistor', 'series circuit'] },
  });
  const { container, handle } = await mountPage({ app: fakeApp({ meta: async () => meta }) });
  await waitFor(() => container.querySelector('.library-card'));
  const form = await openEdit(container);
  const suggest = byText(form.modal, 'Suggest with AI');
  assert.ok(suggest);
  assert.match(form.modal.textContent, /counts toward your daily AI budget/);
  suggest.click();
  await waitFor(() => form.title.value === 'Resistor in series');
  assert.equal(form.description.value, 'A resistor drawn in a series circuit.');
  assert.deepEqual(form.chips(), ['resistor', 'series circuit'], 'merged without repeats');
  assert.equal(calls.find((c) => c.path === '/api/library/1/describe').method, 'POST');
  assert.equal(calls.filter((c) => c.method === 'PATCH').length, 0, 'nothing is saved by a suggestion');
  byText(form.modal, 'Undo the suggestion').click();
  assert.equal(form.title.value, 'Resistor diagram');
  assert.equal(form.description.value, 'A resistor in a simple circuit.');
  assert.deepEqual(form.chips(), ['resistor']);
  answer = 'off';
  suggest.click();
  await waitFor(() => /turned off on this server/.test(form.modal.querySelector('.library-ai-status').textContent));
  assert.equal(form.title.value, 'Resistor diagram', 'a refusal changes nothing');
  answer = 'failed';
  suggest.click();
  await waitFor(() => /could not describe this item/.test(form.modal.querySelector('.library-ai-status').textContent));
  closeAllModals();
  handle.destroy();
});

// --- remove -----------------------------------------------------------------------------------

test('remove: the confirmation says lectures keep working; Cancel keeps the item', async () => {
  fresh();
  const calls = mockFetch({
    'GET /api/library': listing([libItem(), GENERATED]),
    'DELETE /api/library/1': { status: 204, body: null },
  });
  const { container, handle, app } = await mountPage();
  await waitFor(() => container.querySelectorAll('.library-card').length === 2);
  container.querySelector('[data-action="remove"]').click();
  await waitFor(() => topModal());
  assert.match(topModal().textContent, /Remove from your library\?/);
  assert.match(topModal().textContent, /Added to 2 lectures\./);
  assert.doesNotMatch(topModal().textContent, /keeps? showing it/, 'used_in counts lectures the item was added to, not what they show now');
  assert.match(topModal().textContent, /Lectures that use it keep working/);
  clickModal('Cancel');
  await tick();
  assert.equal(calls.filter((c) => c.method === 'DELETE').length, 0);

  container.querySelector('[data-action="remove"]').click();
  await waitFor(() => topModal());
  clickModal('Remove');
  await waitFor(() => container.querySelectorAll('.library-card').length === 1);
  assert.equal(calls.find((c) => c.method === 'DELETE').path, '/api/library/1');
  assert.match(container.querySelector('.list-footer').textContent, /Showing 1 of 1/);
  assert.ok(app.rec.toasts.some((t) => /Removed “Resistor diagram” from your library/.test(t.message)));
  assert.equal(document.activeElement.getAttribute('aria-label'), 'Edit details: Electrons drifting');
  handle.destroy();
});

test('remove: a failure is reported and the card stays; removing the last item shows the empty state', async () => {
  fresh();
  let fail = true;
  mockFetch({
    'GET /api/library': ({ path }) => listing(fail ? [libItem()] : []),
    'DELETE /api/library/1': () => (fail ? { status: 500, body: { detail: 'x' } } : { status: 204, body: null }),
  });
  const { container, handle, app } = await mountPage();
  await waitFor(() => container.querySelector('.library-card'));
  container.querySelector('[data-action="remove"]').click();
  await waitFor(() => topModal());
  clickModal('Remove');
  await waitFor(() => app.rec.errors.length === 1);
  assert.equal(app.rec.errors[0], 'Could not remove it from your library.');
  assert.ok(container.querySelector('.library-card'));
  fail = false;
  container.querySelector('[data-action="remove"]').click();
  await waitFor(() => topModal());
  clickModal('Remove');
  await waitFor(() => /Your library is empty/.test(container.textContent));
  handle.destroy();
});

// --- upload -----------------------------------------------------------------------------------

test('upload: dropped files are checked, uploaded one by one, and the list reloads', async () => {
  fresh();
  const gate = deferred();
  let listed = [libItem()];
  const calls = mockFetch({
    'GET /api/library': () => listing(listed),
    'POST /api/library': async () => {
      await gate.promise;
      listed = [libItem({ id: 5, title: 'photo', keywords: [] }), libItem()];
      return { status: 201, body: libItem({ id: 5, title: 'photo', keywords: [] }) };
    },
  });
  const { container, handle, app } = await mountPage();
  await waitFor(() => container.querySelector('.library-card'));
  dropFiles(container, [new window.File(['text'], 'notes.txt', { type: 'text/plain' }), new window.File(['png'], 'photo.png', { type: 'image/png' })]);
  await waitFor(() => calls.some((c) => c.method === 'POST'));
  const rows = container.querySelectorAll('.library-upload');
  assert.equal(rows.length, 2);
  assert.match(rows[0].textContent, /notes\.txt.*Upload an image or video/);
  assert.match(rows[1].textContent, /Uploading/);
  assert.equal(typeof app.rec.guard, 'function', 'leaving asks first while uploading');
  assert.equal(container.dataset.dirty, 'true');
  assert.equal(container.querySelector('.library-drop button').disabled, true);
  const post = calls.find((c) => c.method === 'POST');
  assert.equal(post.init.body.get('file').name, 'photo.png');
  assert.equal(post.init.body.get('project_id'), null, 'no lecture needed');
  gate.resolve();
  await waitFor(() => container.querySelectorAll('.library-card').length === 2);
  assert.match(rows[1].textContent, /Added to your library/);
  assert.match(container.querySelector('.library-upload-summary').textContent, /1 file added to your library\. 1 file could not be added/);
  assert.equal(app.rec.guard, null, 'the leave guard is gone');
  assert.equal(container.dataset.dirty, undefined);
  assert.equal(libraryGets(calls).length, 2, 'reloaded once');
  // "Add details" opens the editor for the new item
  byText(rows[1], 'Add details').click();
  await waitFor(() => topModal() && topModal().querySelector('.library-edit'));
  assert.equal(topModal().querySelector('.library-edit-fields input.input').value, 'photo');
  closeAllModals();
  handle.destroy();
});

test('upload: the leave guard asks before abandoning running uploads', async () => {
  fresh();
  const gate = deferred();
  mockFetch({ 'GET /api/library': listing([]), 'POST /api/library': () => gate.promise });
  const { container, handle, app } = await mountPage();
  await waitFor(() => /Your library is empty/.test(container.textContent));
  dropFiles(container, [new window.File(['png'], 'a.png', { type: 'image/png' })]);
  await waitFor(() => typeof app.rec.guard === 'function');
  const answer = app.rec.guard(null);
  await waitFor(() => topModal());
  assert.match(topModal().textContent, /Stop uploading\?/);
  clickModal('Stay');
  assert.equal(await answer, false);
  handle.destroy();
  gate.resolve({ status: 201, body: libItem() });
  await tick(10);
});

test('upload: a server refusal is shown on the file; too many files at once are refused', async () => {
  fresh();
  const calls = mockFetch({
    'GET /api/library': listing([]),
    'POST /api/library': { status: 415, body: { detail: 'Allowed: png, jpg, webp, gif, mp4, webm.', code: 'unsupported_type' } },
  });
  const { container, handle } = await mountPage();
  await waitFor(() => /Your library is empty/.test(container.textContent));
  dropFiles(container, [new window.File(['x'], 'fake.png', { type: 'image/png' })]);
  await waitFor(() => /could not be added/.test(container.querySelector('.library-upload-summary').textContent));
  assert.match(container.querySelector('.library-upload').textContent, /That file type is not supported/);
  assert.equal(libraryGets(calls).length, 1, 'nothing added: no reload');

  const many = Array.from({ length: MAX_UPLOAD_BATCH + 1 }, (_, i) => new window.File(['x'], `f${i}.png`, { type: 'image/png' }));
  dropFiles(container, many);
  await tick();
  assert.match(container.querySelector('.library-upload-summary').textContent, /at most 20 files at a time/);
  assert.equal(calls.filter((c) => c.method === 'POST').length, 1);
  byText(container, 'Clear this list').click();
  assert.equal(container.querySelector('.library-upload-box').hidden, true);
  handle.destroy();
});

test('upload: the Choose files button takes several files and shows the size limit from the server', async () => {
  fresh();
  const meta = sampleMeta();
  meta.limits = { ...meta.limits, upload_max_mb: 1 };
  const calls = mockFetch({ 'GET /api/library': listing([]), 'POST /api/library': { status: 201, body: libItem() } });
  const { container, handle } = await mountPage({ app: fakeApp({ meta: async () => meta }) });
  await waitFor(() => /Your library is empty/.test(container.textContent));
  assert.match(container.querySelector('.library-drop').textContent, /up to 1 MB each/);
  const fileInput = container.querySelector('input[type="file"]');
  assert.ok(fileInput.hasAttribute('multiple'));
  assert.equal(fileInput.getAttribute('accept'), '.png,.jpg,.jpeg,.webp,.gif,.mp4,.webm');
  const big = new window.File([new Uint8Array(2 * 1024 * 1024)], 'big.png', { type: 'image/png' });
  Object.defineProperty(fileInput, 'files', { value: [big, new window.File(['p'], 'ok.png', { type: 'image/png' })], configurable: true });
  fileInput.dispatchEvent(new window.Event('change', { bubbles: true }));
  await waitFor(() => /1 file added/.test(container.querySelector('.library-upload-summary').textContent));
  assert.match(container.querySelector('.library-upload').textContent, /larger than the 1 MB upload limit/);
  assert.equal(calls.filter((c) => c.method === 'POST').length, 1);
  handle.destroy();
});

test('uploadSummaryText words', () => {
  assert.equal(uploadSummaryText(2, 0), '2 files added to your library.');
  assert.equal(uploadSummaryText(0, 1), 'The file could not be added: see the list for why.');
  assert.equal(uploadSummaryText(0, 3), 'The files could not be added: see the list for why.');
  assert.equal(uploadSummaryText(0, 0), '');
});
