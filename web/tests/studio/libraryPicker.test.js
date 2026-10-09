import { resetDom, mockFetch, tick, window } from './_dom.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  openLibraryPicker,
  libraryQuery,
  normalizeKeywords,
  keywordProblem,
  validateLibraryFile,
  usedInText,
  itemTitle,
  itemFacts,
  acceptedExtensions,
  uploadToLibrary,
  libraryCard,
  LIBRARY_LIMITS,
} from '../../js/studio/components/libraryPicker.js';
import { closeAllModals } from '../../js/studio/components/modal.js';
import { ApiError, CSRF_HEADER } from '../../js/shared/api.js';
import { fakeApp, byText, clickModal, waitFor, jsonBody, typeInto, deferred } from './_views.js';

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

const listing = (items, total = items.length) => ({ items, total });
const libraryCalls = (calls) => calls.filter((c) => c.method === 'GET' && c.path.startsWith('/api/library'));
const query = (call) => new URLSearchParams(call.path.split('?')[1] || '');
const dialog = () => document.querySelector('.modal');

// --- helpers --------------------------------------------------------------------------------

test('libraryQuery leaves out empty filters and always pages', () => {
  assert.equal(libraryQuery({}), 'limit=48&offset=0');
  assert.equal(libraryQuery({ q: '  ohm law ', kind: 'image', source: 'upload', offset: 48 }), 'q=ohm+law&kind=image&source=upload&limit=48&offset=48');
  assert.equal(libraryQuery({ q: '   ', kind: '', source: '' }), 'limit=48&offset=0');
});

test('normalizeKeywords tidies, drops repeats ignoring case and keeps the limits', () => {
  assert.deepEqual(normalizeKeywords(['  Ohm  law ', 'ohm LAW', '', null, 'Resistor', 'resistor']), ['Ohm law', 'Resistor']);
  const many = Array.from({ length: 30 }, (_, i) => `word${i}`);
  assert.equal(normalizeKeywords(many).length, LIBRARY_LIMITS.keywords);
  assert.equal(normalizeKeywords(['x'.repeat(60)])[0].length, LIBRARY_LIMITS.keyword);
  assert.deepEqual(normalizeKeywords('not a list'), []);
});

test('keywordProblem explains repeats, length and the maximum count', () => {
  assert.equal(keywordProblem('volt', ['ohm']), null);
  assert.match(keywordProblem('OHM', ['ohm']), /already a keyword/);
  assert.match(keywordProblem('x'.repeat(41), []), /at most 40 characters/);
  assert.match(keywordProblem('new', Array.from({ length: 20 }, (_, i) => `k${i}`)), /at most 20 keywords/);
});

test('validateLibraryFile accepts pictures and videos within the size limit, per kind', () => {
  const file = (name, size = 10, type = '') => ({ name, size, type });
  assert.equal(validateLibraryFile(file('a.png', 10, 'image/png'), 50), null);
  assert.equal(validateLibraryFile(file('a.webm', 10, 'video/webm'), 50), null);
  assert.match(validateLibraryFile(file('notes.txt', 10, 'text/plain'), 50), /Upload an image or video/);
  assert.match(validateLibraryFile(file('a.png', 2 * 1024 * 1024), 1), /larger than the 1 MB upload limit/);
  assert.match(validateLibraryFile(file('a.png', 0), 50), /empty/);
  assert.match(validateLibraryFile(file('clip.mp4', 10, 'video/mp4'), 50, 'image'), /Upload an image/);
  assert.match(validateLibraryFile(file('a.png', 10, 'image/png'), 50, 'video'), /Upload a video/);
  assert.equal(validateLibraryFile(file('clip.mp4', 10, 'video/mp4'), 50, 'video'), null);
  assert.deepEqual(acceptedExtensions('video'), ['.mp4', '.webm']);
  assert.ok(acceptedExtensions().includes('.gif') && acceptedExtensions().includes('.mp4'));
});

test('plain words for titles, use counts and sizes', () => {
  // what used_in counts: lectures the item was ever added to or made for (an intended wording change)
  assert.equal(usedInText(0), 'Not added to a lecture yet');
  assert.equal(usedInText(1), 'Added to 1 lecture');
  assert.equal(usedInText(3), 'Added to 3 lectures');
  assert.equal(usedInText(null), 'Not added to a lecture yet');
  assert.equal(itemTitle({ title: '  ', kind: 'video' }), 'Untitled video');
  assert.equal(itemTitle({ title: '', kind: 'image' }), 'Untitled picture');
  assert.equal(itemFacts({ kind: 'video', width: 1280, height: 720, duration_s: 12.4 }), '1280 × 720 · 0:12');
  assert.equal(itemFacts({ kind: 'image', width: null, height: null, duration_s: null }), '');
});

test('a card shows server strings as text only', () => {
  fresh();
  const evil = '<img src=x onerror="window.pwned=1">';
  const card = libraryCard(libItem({ title: evil, description: evil, keywords: [evil] }));
  document.body.appendChild(card);
  assert.equal(card.querySelector('.library-title').textContent, evil);
  assert.ok(card.querySelector('.library-desc').textContent.includes(evil));
  assert.equal(card.querySelectorAll('img').length, 1, 'only the thumbnail is an image');
  assert.equal(card.querySelector('img').getAttribute('alt'), '', 'the thumbnail is decorative');
  assert.equal(window.pwned, undefined);
});

test('a video card shows its poster, or a placeholder without one', () => {
  fresh();
  const withPoster = libraryCard(libItem({ kind: 'video', poster_url: '/media/p.jpg?sig=1', duration_s: 75 }));
  assert.match(withPoster.querySelector('img').getAttribute('src'), /\/media\/p\.jpg\?sig=1$/);
  assert.match(withPoster.querySelector('.library-thumb-badge').textContent, /1:15/);
  const without = libraryCard(libItem({ kind: 'video', poster_url: null }));
  assert.equal(without.querySelector('img'), null);
  assert.ok(without.querySelector('.library-thumb-empty'));
  assert.equal(without.querySelector('video'), null, 'cards never load the video itself');
});

// --- picker ---------------------------------------------------------------------------------

test('picker: lists only the asked kind and resolves with the chosen item (no attach without a lecture)', async () => {
  fresh();
  const calls = mockFetch({
    'GET /api/library': listing([libItem(), libItem({ id: 2, title: 'Circuit photo', used_in: 0 })]),
  });
  const result = openLibraryPicker({ app: fakeApp(), kind: 'image' });
  await waitFor(() => document.querySelectorAll('.library-card').length === 2);
  assert.equal(document.querySelector('.modal-title').textContent, 'Choose a picture from your library');
  const q = query(libraryCalls(calls)[0]);
  assert.equal(q.get('kind'), 'image');
  assert.equal(q.get('limit'), '48');
  assert.equal(document.querySelector('.library-kind-select'), null, 'the kind is fixed');
  assert.match(dialog().textContent, /Added to 2 lectures/);
  assert.match(dialog().textContent, /Not added to a lecture yet/);
  const use = [...document.querySelectorAll('[data-action="use"]')][1];
  assert.equal(use.getAttribute('aria-label'), 'Use this: Circuit photo');
  use.click();
  const chosen = await result;
  assert.equal(chosen.id, 2);
  assert.equal(dialog(), null);
  assert.equal(calls.filter((c) => c.method === 'POST').length, 0);
});

test('picker: with a lecture, the item is attached first and the attach key is returned', async () => {
  fresh();
  const calls = mockFetch({
    'GET /api/library': listing([libItem()]),
    'POST /api/library/1/attach': { asset_key: 'upload/3f2a' },
  });
  const result = openLibraryPicker({ app: fakeApp(), projectId: 5 });
  await waitFor(() => document.querySelector('[data-action="use"]'));
  assert.equal(query(libraryCalls(calls)[0]).get('kind'), null, 'both kinds by default');
  document.querySelector('[data-action="use"]').click();
  const chosen = await result;
  const attach = calls.find((c) => c.path === '/api/library/1/attach');
  assert.deepEqual(jsonBody(attach), { project_id: 5 });
  assert.equal(attach.init.headers[CSRF_HEADER], '1');
  assert.equal(chosen.asset_key, 'upload/3f2a');
  assert.equal(chosen.title, 'Resistor diagram');
});

test('picker: a failed attach keeps the dialog open with the reason; Cancel resolves null', async () => {
  fresh();
  mockFetch({
    'GET /api/library': listing([libItem()]),
    'POST /api/library/1/attach': { status: 404, body: { detail: 'Not found', code: 'not_found' } },
  });
  const result = openLibraryPicker({ app: fakeApp(), projectId: 5 });
  await waitFor(() => document.querySelector('[data-action="use"]'));
  document.querySelector('[data-action="use"]').click();
  await waitFor(() => !document.querySelector('.modal-error').hidden);
  // a lecture that is not the user's own (an admin's view): said in words, never "it may have been deleted"
  assert.match(document.querySelector('.modal-error').textContent, /only in your own lectures/);
  assert.ok(dialog(), 'still open');
  assert.equal(document.querySelector('[data-action="use"]').disabled, false, 'buttons work again');
  clickModal('Cancel');
  assert.equal(await result, null);
});

test('picker: Escape closes it with null and a late answer is ignored', async () => {
  fresh();
  const slow = deferred();
  mockFetch({ 'GET /api/library': () => slow.promise });
  const result = openLibraryPicker({ app: fakeApp() });
  await tick();
  document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  assert.equal(await result, null);
  slow.resolve(listing([libItem()]));
  await tick(10);
  assert.equal(document.querySelector('.library-card'), null);
});

test('picker: search is debounced and sent as q; the kind filter can change when not fixed', async () => {
  fresh();
  const calls = mockFetch({ 'GET /api/library': listing([libItem()]) });
  void openLibraryPicker({ app: fakeApp() });
  await waitFor(() => document.querySelector('.library-card'));
  const search = document.querySelector('.library-picker input[type="search"]');
  typeInto(search, 'res');
  typeInto(search, 'resistor');
  await tick(50);
  assert.equal(libraryCalls(calls).length, 1, 'no request while typing');
  await waitFor(() => libraryCalls(calls).length === 2, 2000);
  assert.equal(query(libraryCalls(calls)[1]).get('q'), 'resistor');
  const kind = document.querySelector('.library-kind-select');
  kind.value = 'video';
  kind.dispatchEvent(new window.Event('change', { bubbles: true }));
  await waitFor(() => libraryCalls(calls).length === 3);
  assert.equal(query(libraryCalls(calls)[2]).get('kind'), 'video');
  assert.equal(query(libraryCalls(calls)[2]).get('q'), 'resistor');
  closeAllModals();
});

test('picker: a kind-limited picker never offers another kind, and its empty state says so', async () => {
  fresh();
  mockFetch({ 'GET /api/library': listing([libItem({ id: 4, kind: 'image' })], 1) });
  void openLibraryPicker({ app: fakeApp(), kind: 'video' });
  await waitFor(() => /No videos in your library yet/.test(dialog().textContent));
  assert.equal(document.querySelector('.library-card'), null);
  closeAllModals();
});

test('picker: `sources` keeps out items a field cannot use (the sources are sent to the server)', async () => {
  fresh();
  const calls = mockFetch({
    'GET /api/library': listing([libItem({ id: 1, source: 'upload' }), libItem({ id: 2, source: 'generated', title: 'AI picture' }), libItem({ id: 3, source: 'figure', title: 'Fig' })]),
  });
  void openLibraryPicker({ app: fakeApp(), kind: 'image', sources: ['upload', 'figure'] });
  await waitFor(() => document.querySelectorAll('.library-card').length === 2);
  assert.equal(query(libraryCalls(calls)[0]).get('source'), 'upload,figure', 'two sources: filtered by the server too');
  assert.doesNotMatch(dialog().textContent, /AI picture/);
  assert.equal(byText(dialog(), 'Upload a picture').hidden, false, 'uploads are allowed');
  closeAllModals();

  const calls2 = mockFetch({ 'GET /api/library': listing([libItem({ id: 2, source: 'generated' })]) });
  void openLibraryPicker({ app: fakeApp(), sources: ['generated'] });
  await waitFor(() => document.querySelector('.library-card'));
  assert.equal(query(libraryCalls(calls2)[0]).get('source'), 'generated');
  assert.equal(byText(dialog(), 'Upload a file').hidden, true, 'an upload would be refused by the field');
  closeAllModals();
});

test('picker: Load more asks for the next page and moves focus to it', async () => {
  fresh();
  const first = Array.from({ length: 48 }, (_, i) => libItem({ id: i + 1, title: `Item ${i + 1}` }));
  const calls = mockFetch({
    'GET /api/library': ({ path }) =>
      query({ path }).get('offset') === '0' ? listing(first, 50) : listing([libItem({ id: 49, title: 'Item 49' }), libItem({ id: 50, title: 'Item 50' })], 50),
  });
  void openLibraryPicker({ app: fakeApp() });
  await waitFor(() => document.querySelectorAll('.library-card').length === 48);
  assert.match(dialog().textContent, /Showing 48 of 50/);
  byText(dialog(), 'Load more').click();
  await waitFor(() => document.querySelectorAll('.library-card').length === 50);
  assert.equal(query(libraryCalls(calls)[1]).get('offset'), '48');
  assert.equal(document.activeElement.getAttribute('aria-label'), 'Use this: Item 49');
  assert.equal(byText(dialog(), 'Load more'), null);
  closeAllModals();
});

test('picker: a load error offers a retry', async () => {
  fresh();
  let fail = true;
  mockFetch({ 'GET /api/library': () => (fail ? { status: 500, body: { detail: 'boom' } } : listing([libItem()])) });
  void openLibraryPicker({ app: fakeApp() });
  await waitFor(() => /server had a problem/.test(dialog().textContent));
  fail = false;
  byText(dialog(), 'Try again').click();
  await waitFor(() => document.querySelector('.library-card'));
  closeAllModals();
});

/** Choose `file` in the picker's hidden file input. */
function pickFile(file) {
  const fileInput = document.querySelector('.library-picker input[type="file"]');
  Object.defineProperty(fileInput, 'files', { value: [file], configurable: true });
  fileInput.dispatchEvent(new window.Event('change', { bubbles: true }));
}

test('picker: uploading a new file adds it to the library and chooses it', async () => {
  fresh();
  const uploaded = libItem({ id: 9, title: 'photo', used_in: 0 });
  const calls = mockFetch({
    'GET /api/library': listing([]),
    'POST /api/library': { status: 201, body: uploaded },
    'POST /api/library/9/attach': { asset_key: 'upload/9' },
  });
  const result = openLibraryPicker({ app: fakeApp(), kind: 'image', projectId: 3 });
  await waitFor(() => /No pictures in your library yet/.test(dialog().textContent));
  assert.equal(document.querySelector('.library-picker input[type="file"]').getAttribute('accept'), '.png,.jpg,.jpeg,.webp,.gif');
  pickFile(new window.File(['png-bytes'], 'photo.png', { type: 'image/png' }));
  const chosen = await result;
  const upload = calls.find((c) => c.method === 'POST' && c.path === '/api/library');
  assert.ok(upload.init.body instanceof window.FormData);
  assert.equal(upload.init.body.get('file').name, 'photo.png');
  assert.equal(chosen.id, 9);
  assert.equal(chosen.asset_key, 'upload/9', 'attached to the lecture too');
});

test('picker: a file of the wrong kind is refused before any upload', async () => {
  fresh();
  const calls = mockFetch({ 'GET /api/library': listing([libItem()]) });
  void openLibraryPicker({ app: fakeApp(), kind: 'image' });
  await waitFor(() => document.querySelector('.library-card'));
  pickFile(new window.File(['x'], 'clip.mp4', { type: 'video/mp4' }));
  await tick();
  assert.match(document.querySelector('.library-picker-upload').textContent, /clip\.mp4: Upload an image/);
  assert.equal(calls.filter((c) => c.method === 'POST').length, 0);
  assert.ok(dialog(), 'still open');
  closeAllModals();
});

test('picker: a refused upload shows the reason and the dialog stays usable', async () => {
  fresh();
  mockFetch({
    'GET /api/library': listing([libItem()]),
    'POST /api/library': { status: 415, body: { detail: 'Allowed: png, jpg, webp, gif, mp4, webm.', code: 'unsupported_type' } },
  });
  void openLibraryPicker({ app: fakeApp() });
  await waitFor(() => document.querySelector('.library-card'));
  pickFile(new window.File(['x'], 'photo.png', { type: 'image/png' }));
  await waitFor(() => /not supported/.test(document.querySelector('.library-picker-upload').textContent));
  assert.equal(document.querySelector('[data-action="use"]').disabled, false);
  closeAllModals();
});

// --- upload with progress (XMLHttpRequest) --------------------------------------------------

class FakeXHR {
  static last = null;
  static respond = { status: 201, body: libItem({ id: 7 }), headers: {} };
  constructor() {
    this.headers = {};
    this.listeners = {};
    this.upload = { listeners: {}, addEventListener: (n, fn) => (this.upload.listeners[n] = fn) };
    FakeXHR.last = this;
  }
  open(method, url) {
    this.method = method;
    this.url = url;
  }
  setRequestHeader(k, v) {
    this.headers[k] = v;
  }
  addEventListener(n, fn) {
    this.listeners[n] = fn;
  }
  getResponseHeader(n) {
    return (FakeXHR.respond.headers || {})[n] ?? null;
  }
  abort() {
    this.listeners.abort && this.listeners.abort();
  }
  send(body) {
    this.body = body;
  }
  /** Finish the request with FakeXHR.respond. */
  finish() {
    this.upload.listeners.progress && this.upload.listeners.progress({ lengthComputable: true, loaded: 50, total: 100 });
    this.upload.listeners.progress && this.upload.listeners.progress({ lengthComputable: true, loaded: 100, total: 100 });
    this.status = FakeXHR.respond.status;
    this.statusText = String(this.status);
    this.responseText = JSON.stringify(FakeXHR.respond.body);
    this.listeners.load();
  }
}

async function withXhr(fn) {
  globalThis.XMLHttpRequest = FakeXHR;
  try {
    await fn();
  } finally {
    delete globalThis.XMLHttpRequest;
  }
}

test('upload: progress is reported and the CSRF header is sent', async () => {
  await withXhr(async () => {
    FakeXHR.respond = { status: 201, body: libItem({ id: 7 }), headers: {} };
    const seen = [];
    const file = new window.File(['abc'], 'a.png', { type: 'image/png' });
    const p = uploadToLibrary(file, { title: 'A', keywords: ['one', 'One', 'two'] }, { onProgress: (f) => seen.push(f) });
    const xhr = FakeXHR.last;
    assert.equal(xhr.method, 'POST');
    assert.equal(xhr.url, '/api/library');
    assert.equal(xhr.headers[CSRF_HEADER], '1');
    assert.equal(xhr.body.get('file').name, 'a.png');
    assert.equal(xhr.body.get('title'), 'A');
    assert.equal(xhr.body.get('keywords'), 'one,two');
    xhr.finish();
    const item = await p;
    assert.equal(item.id, 7);
    assert.deepEqual(seen, [0.5, 1]);
  });
});

test('upload: an error answer becomes an ApiError; a 401 lets the shared client sign the user out', async () => {
  const calls = mockFetch({ 'GET /api/auth/me': { status: 401, body: { detail: 'Not signed in', code: 'unauthenticated' } } });
  await withXhr(async () => {
    FakeXHR.respond = { status: 413, body: { detail: 'Too big', code: 'too_large' }, headers: {} };
    const p = uploadToLibrary(new window.File(['abc'], 'a.png', { type: 'image/png' }));
    FakeXHR.last.finish();
    await assert.rejects(p, (err) => err instanceof ApiError && err.status === 413 && err.code === 'too_large');
    assert.equal(calls.length, 0);
    FakeXHR.respond = { status: 401, body: { detail: 'Not signed in', code: 'unauthenticated' }, headers: {} };
    const p2 = uploadToLibrary(new window.File(['abc'], 'a.png', { type: 'image/png' }));
    FakeXHR.last.finish();
    await assert.rejects(p2, (err) => err instanceof ApiError && err.status === 401);
    await waitFor(() => calls.some((c) => c.path === '/api/auth/me'));
  });
});

test('upload: aborting the signal cancels the request', async () => {
  await withXhr(async () => {
    const ctrl = new AbortController();
    const p = uploadToLibrary(new window.File(['abc'], 'a.png', { type: 'image/png' }), {}, { signal: ctrl.signal });
    ctrl.abort();
    await assert.rejects(p, (err) => err.name === 'AbortError');
  });
});

test('upload without XMLHttpRequest goes through the shared client', async () => {
  const calls = mockFetch({ 'POST /api/library': { status: 201, body: libItem({ id: 8 }) } });
  const item = await uploadToLibrary(new window.File(['abc'], 'a.png', { type: 'image/png' }), { description: 'D' });
  assert.equal(item.id, 8);
  const call = calls[0];
  assert.equal(call.init.headers[CSRF_HEADER], '1');
  assert.equal(call.init.body.get('description'), 'D');
  assert.equal(call.init.body.get('keywords'), null);
});

// --- batch 3 review fixes ---------------------------------------------------------------------

test('picker: the best matches come first, are not repeated below, and Use this chooses one', async () => {
  fresh();
  mockFetch({ 'GET /api/library': listing([libItem({ id: 1, title: 'Newest' }), libItem({ id: 5, title: 'Pump diagram' })], 2) });
  const result = openLibraryPicker({ app: fakeApp(), suggested: [libItem({ id: 5, title: 'Pump diagram' }), libItem({ id: 9, kind: 'video', title: 'Clip' })], kind: 'image' });
  await waitFor(() => dialog().querySelector('.library-grid .library-card') && dialog().querySelector('.library-suggested'));
  const best = dialog().querySelector('.library-suggested');
  assert.match(best.textContent, /Best match/);
  assert.deepEqual([...best.querySelectorAll('.library-card')].map((c) => c.dataset.itemId), ['5'], 'a video is not offered for a picture');
  const below = [...dialog().querySelectorAll('.library-picker > .library-grid .library-card')].map((c) => c.dataset.itemId);
  assert.deepEqual(below, ['1'], 'not repeated in the list');
  assert.ok(best.compareDocumentPosition(dialog().querySelector('.library-picker > .library-grid')) & window.Node.DOCUMENT_POSITION_FOLLOWING);
  best.querySelector('[data-action="use"]').click();
  assert.equal((await result).id, 5);
});

test('picker: a search hides the best matches', async () => {
  fresh();
  mockFetch({ 'GET /api/library': listing([libItem({ id: 1 })]) });
  void openLibraryPicker({ app: fakeApp(), suggested: [libItem({ id: 5, title: 'Pump diagram' })] });
  await waitFor(() => dialog().querySelector('.library-suggested'));
  typeInto(dialog().querySelector('input[type="search"]'), 'resistor');
  await waitFor(() => !dialog().querySelector('.library-suggested'), 2000);
  closeAllModals();
});

test('picker: screen readers hear a short count, never the whole grid', async () => {
  fresh();
  mockFetch({ 'GET /api/library': listing([libItem({ id: 1 }), libItem({ id: 2, title: 'Circuit photo' })]) });
  void openLibraryPicker({ app: fakeApp() });
  await waitFor(() => document.querySelectorAll('.library-card').length === 2);
  assert.equal(dialog().querySelector('.library-grid').hasAttribute('aria-live'), false);
  const status = dialog().querySelector('.sr-only[role="status"]');
  assert.equal(status.textContent, '2 items found.');
  closeAllModals();
  fresh();
  mockFetch({ 'GET /api/library': listing([]) });
  void openLibraryPicker({ app: fakeApp(), kind: 'video' });
  await waitFor(() => /No videos in your library yet/.test(dialog().textContent));
  assert.equal(dialog().querySelector('.sr-only[role="status"]').textContent, 'No videos in your library yet.');
  closeAllModals();
});

test('picker: limited to sources, an empty page still offers the rest and never says the library is empty', async () => {
  fresh();
  const calls = mockFetch({
    'GET /api/library': ({ path }) =>
      query({ path }).get('offset') === '0'
        ? listing(Array.from({ length: 48 }, (_, i) => libItem({ id: i + 1, source: 'generated' })), 60)
        : listing([libItem({ id: 70, source: 'upload', title: 'My upload' })], 60),
  });
  void openLibraryPicker({ app: fakeApp(), sources: ['upload', 'figure'] });
  const more = await waitFor(() => byText(dialog(), 'Load more'));
  assert.equal(query(libraryCalls(calls)[0]).get('source'), 'upload,figure');
  assert.doesNotMatch(dialog().textContent, /Your library is empty/);
  more.click();
  await waitFor(() => /My upload/.test(dialog().textContent));
  closeAllModals();
});
