import { resetDom, mockFetch, tick, window } from './_dom.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import { mount, POLL_MS, PAGE_SIZE } from '../../js/studio/views/videos.js';
import { closeAllModals } from '../../js/studio/components/modal.js';
import { fakeApp, byText, clickModal, waitFor } from './_views.js';

/** Let pending promise chains settle without using (possibly mocked) timers. */
async function settle(rounds = 12) {
  for (let i = 0; i < rounds; i++) await new Promise((r) => setImmediate(r));
}

const video = (id, more = {}) => ({
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
  preview_url: `/api/renders/${id}/download?file=video&inline=1`,
  qa_ok: true,
  ...more,
});

function fresh(url = '/') {
  resetDom();
  closeAllModals();
  window.history.replaceState(null, '', url);
}

async function mountPage(routes, { query = {}, app = fakeApp() } = {}) {
  const calls = mockFetch(routes);
  const container = document.createElement('div');
  document.body.appendChild(container);
  const replaced = [];
  app.replaceHash = (h) => replaced.push(h);
  const handle = mount(container, { app, params: {}, query });
  await settle();
  return { container, handle, calls, app, replaced };
}

const LIST = `GET /api/videos?limit=${PAGE_SIZE}&offset=0`;

test('videos: every video with its facts, status words, download, watch and its lecture', async () => {
  fresh();
  const { container, handle } = await mountPage({
    [LIST]: { items: [video(3, { matches_current: false }), video(2, { qa_ok: false }), video(1, { status: 'running', preview_url: null, download_url: null, duration_s: null, size_bytes: null })], total: 3 },
  });
  const cards = [...container.querySelectorAll('.video-card')];
  assert.deepEqual(cards.map((c) => c.dataset.renderId), ['3', '2', '1']);
  const [outdated, checked, running] = cards;
  assert.equal(outdated.querySelector('.card-meta').textContent, 'Version 2 · 3:24 · 1920 × 1080 · 24 MB');
  assert.match(outdated.textContent, /Out of date/);
  assert.match(checked.textContent, /Up to date/);
  assert.match(checked.textContent, /Check this video/);
  assert.match(running.textContent, /Being made/);
  assert.equal(byText(running, 'Watch', 'button'), null);
  assert.equal(byText(running, 'Download MP4', 'a'), null);
  const dl = byText(outdated, 'Download MP4', 'a');
  assert.match(dl.getAttribute('href'), /\/api\/renders\/3\/download\?file=video$/);
  assert.equal(dl.hasAttribute('download'), true);
  assert.match(byText(outdated, 'Open lecture', 'a').getAttribute('href'), /#\/p\/5\?version=9$/);
  assert.doesNotMatch(container.textContent, /Render #/, 'no render numbers without ?debug');
  assert.match(container.querySelector('.list-footer').textContent, /Showing 3 of 3/);
  handle.destroy();
});

test('videos: "Watch" plays the signed preview in a dialog; closing it releases the video', async () => {
  fresh();
  let reads = 0;
  const { container, handle } = await mountPage({
    [LIST]: () => {
      reads += 1;
      return { items: [video(3, { preview_url: `/api/renders/3/download?file=video&inline=1&sig=${reads}` })], total: 1 };
    },
  });
  byText(container, 'Watch', 'button').click();
  const player = await waitFor(() => document.querySelector('.modal video'));
  assert.match(player.getAttribute('src'), /\/api\/renders\/3\/download\?file=video&inline=1&sig=1$/);
  assert.equal(player.hasAttribute('controls'), true);
  assert.match(document.querySelector('.modal-title').textContent, /Ohm's law/);
  assert.ok(byText(document.querySelector('.modal'), 'Download MP4', 'a'));
  // (batch 4 review) a link that no longer loads (an expired signed URL) is read again once before giving up
  player.dispatchEvent(new window.Event('error'));
  await waitFor(() => /sig=2$/.test(player.getAttribute('src') || ''));
  assert.equal(document.querySelector('.modal [role="alert"].notice').hidden, true, 'the fresh link is tried first');
  player.dispatchEvent(new window.Event('error'));
  await settle();
  assert.equal(document.querySelector('.modal [role="alert"].notice').hidden, false, 'a video that still cannot be loaded says so');
  assert.match(document.querySelector('.modal [role="alert"].notice').textContent, /could not be loaded here/);
  clickModal('Close');
  await tick();
  assert.equal(player.hasAttribute('src'), false);
  handle.destroy();
});

test('videos: filters for up-to-date and out-of-date videos, kept in the URL', async () => {
  fresh();
  const { container, handle, replaced } = await mountPage({
    [LIST]: { items: [video(3, { matches_current: false }), video(2), video(1, { status: 'failed', preview_url: null, download_url: null, matches_current: true })], total: 3 },
  });
  const buttons = () => [...container.querySelectorAll('.videos-filters button')];
  assert.deepEqual(buttons().map((b) => b.textContent), ['All videos (3)', 'Up to date (1)', 'Out of date (1)']);
  byText(container, 'Out of date', 'button').click();
  assert.deepEqual([...container.querySelectorAll('.video-card')].map((c) => c.dataset.renderId), ['3']);
  assert.equal(replaced.at(-1), '#/videos?filter=outdated');
  assert.equal(byText(container, 'Out of date', 'button').getAttribute('aria-pressed'), 'true');
  assert.equal(document.activeElement, byText(container, 'Out of date', 'button'));
  byText(container, 'Up to date', 'button').click();
  assert.deepEqual([...container.querySelectorAll('.video-card')].map((c) => c.dataset.renderId), ['2'], 'a failed video is never "up to date"');
  byText(container, 'All videos', 'button').click();
  assert.equal(replaced.at(-1), '#/videos');
  assert.equal(container.querySelectorAll('.video-card').length, 3);
  handle.destroy();

  fresh();
  const filtered = await mountPage({ [LIST]: { items: [video(2)], total: 1 } }, { query: { filter: 'outdated' } });
  assert.match(filtered.container.textContent, /No videos here/);
  byText(filtered.container, 'Show all videos', 'button').click();
  assert.equal(filtered.container.querySelectorAll('.video-card').length, 1);
  filtered.handle.destroy();
});

test('videos: empty, error and "load more"', async () => {
  fresh();
  const empty = await mountPage({ [LIST]: { items: [], total: 0 } });
  assert.match(empty.container.textContent, /No videos yet/);
  assert.match(byText(empty.container, 'Go to your lectures', 'a').getAttribute('href'), /#\/projects$/);
  assert.equal(empty.container.querySelector('.videos-filters').hidden, true);
  empty.handle.destroy();

  fresh();
  let fail = true;
  const broken = await mountPage({ [LIST]: () => (fail ? { status: 500, body: { detail: 'x' } } : { items: [video(1)], total: 1 }) });
  assert.match(broken.container.textContent, /Could not load your videos/);
  assert.equal(broken.app.rec.errors.length, 1);
  fail = false;
  byText(broken.container, 'Try again', 'button').click();
  await settle();
  assert.equal(broken.container.querySelectorAll('.video-card').length, 1);
  broken.handle.destroy();

  fresh();
  const first = Array.from({ length: PAGE_SIZE }, (_, i) => video(100 - i));
  const paged = await mountPage({
    [LIST]: { items: first, total: PAGE_SIZE + 1 },
    [`GET /api/videos?limit=${PAGE_SIZE}&offset=${PAGE_SIZE}`]: { items: [video(1)], total: PAGE_SIZE + 1 },
  });
  byText(paged.container, 'Load more', 'button').click();
  await settle();
  assert.equal(paged.container.querySelectorAll('.video-card').length, PAGE_SIZE + 1);
  assert.equal(byText(paged.container, 'Load more', 'button'), null);
  paged.handle.destroy();
});

test('videos: lectures sharing a title get a short suffix; render numbers only with ?debug', async () => {
  fresh('/?debug=1');
  const { container, handle } = await mountPage({
    [LIST]: { items: [video(3, { project_id: 8 }), video(2, { project_id: 5 }), video(1, { project_id: 5 }), video(4, { project_id: 6, project_title: 'Logic gates' })], total: 4 },
  });
  const titles = [...container.querySelectorAll('.video-card .card-title')].map((t) => t.textContent);
  assert.deepEqual(titles, ["Ohm's law (2)", "Ohm's law (1)", "Ohm's law (1)", 'Logic gates']);
  assert.match(container.querySelector('[data-render-id="3"] .card-meta').textContent, /^Render #3 · Version 2/);
  handle.destroy();
  window.history.replaceState(null, '', '/');
});

test('videos: a video being made is checked again every POLL_MS, never while the tab is hidden', async (t) => {
  fresh();
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let hidden = false;
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden });
  let state = 'running';
  const routes = {
    'GET /api/videos?limit=24&offset=0': () => ({ items: [video(2, { status: state, preview_url: state === 'succeeded' ? '/p/2' : null }), video(1)], total: 2 }),
  };
  try {
    const { container, handle, calls } = await mountPage(routes);
    const lists = () => calls.filter((c) => c.path.startsWith('/api/videos')).length;
    assert.equal(lists(), 1);
    // keyboard focus on a finished video's Watch survives a redraw
    byText(container.querySelector('[data-render-id="1"]'), 'Watch', 'button').focus();
    t.mock.timers.tick(POLL_MS);
    await settle();
    assert.equal(lists(), 2, 'polled once');
    assert.equal(document.activeElement, byText(container.querySelector('[data-render-id="1"]'), 'Watch', 'button'));

    hidden = true;
    t.mock.timers.tick(POLL_MS * 3);
    await settle();
    assert.equal(lists(), 2, 'no requests while hidden');
    state = 'succeeded';
    hidden = false;
    document.dispatchEvent(new window.Event('visibilitychange'));
    await settle();
    assert.equal(lists(), 3, 'polled at once on return');
    assert.ok(byText(container.querySelector('[data-render-id="2"]'), 'Watch', 'button'), 'the finished video can be watched');
    assert.equal(document.activeElement, byText(container.querySelector('[data-render-id="1"]'), 'Watch', 'button'), 'focus kept');
    t.mock.timers.tick(POLL_MS * 3);
    await settle();
    assert.equal(lists(), 3, 'nothing being made: no more polling');
    handle.destroy();
  } finally {
    delete document.hidden;
  }
});

test('videos: a read that only renews the signed preview links leaves the list on screen as it is', async (t) => {
  fresh();
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let reads = 0;
  const routes = {
    [LIST]: () => {
      reads += 1;
      // a presigned URL is new on every read (S3 without a CDN)
      return { items: [video(2, { status: 'running', preview_url: null, download_url: null }), video(1, { preview_url: `/s3/render-1.mp4?X-Amz-Signature=${reads}` })], total: 2 };
    },
  };
  const { container, handle } = await mountPage(routes);
  const before = container.querySelector('[data-render-id="1"]');
  t.mock.timers.tick(POLL_MS);
  await settle();
  assert.equal(reads, 2, 'read again while a video is being made');
  assert.equal(container.querySelector('[data-render-id="1"]'), before, 'the same card: nothing redrawn or announced again');
  byText(before, 'Watch', 'button').click();
  await settle(); // timers are mocked here: no waitFor
  const player = document.querySelector('.modal video');
  assert.match(player.getAttribute('src'), /X-Amz-Signature=2$/, 'Watch opens the newest signed link');
  clickModal('Close');
  handle.destroy();
});

test('videos: closing the preview after the list was drawn again gives focus back to the video’s Watch', async (t) => {
  fresh();
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let state = 'running';
  const routes = {
    [LIST]: () => ({ items: [video(2, { status: state, preview_url: state === 'succeeded' ? '/p/2' : null }), video(1)], total: 2 }),
  };
  const { container, handle } = await mountPage(routes);
  const watch = byText(container.querySelector('[data-render-id="1"]'), 'Watch', 'button');
  watch.focus();
  watch.click();
  await settle();
  assert.ok(document.querySelector('.modal video'));
  state = 'succeeded'; // the other video finishes while this one plays: the list is drawn again
  t.mock.timers.tick(POLL_MS);
  await settle();
  assert.equal(watch.isConnected, false, 'the button that opened the preview was replaced');
  clickModal('Close');
  await settle();
  assert.equal(document.activeElement, byText(container.querySelector('[data-render-id="1"]'), 'Watch', 'button'));
  handle.destroy();
});

test('videos: the refresh keeps the videos loaded past its window (more than MAX_REFRESH)', async (t) => {
  fresh();
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let state = 'running';
  const all = () => Array.from({ length: 150 }, (_, i) => video(150 - i, i === 0 ? { status: state, preview_url: null, download_url: null } : {}));
  const page = (limit, offset) => ({ items: all().slice(offset, offset + limit), total: 150 });
  const routes = {
    're:^GET /api/videos\\?limit=(\\d+)&offset=(\\d+)$': ({ path }) => {
      const q = new URLSearchParams(path.split('?')[1]);
      return page(Number(q.get('limit')), Number(q.get('offset')));
    },
  };
  const { container, handle, calls } = await mountPage(routes);
  for (let i = 0; i < 4; i++) {
    byText(container, 'Load more', 'button').click();
    await settle();
  }
  assert.equal(container.querySelectorAll('.video-card').length, 120);
  state = 'succeeded';
  t.mock.timers.tick(POLL_MS);
  await settle();
  assert.ok(calls.some((c) => c.path === '/api/videos?limit=100&offset=0'), 'the re-read window is at most 100');
  assert.equal(container.querySelectorAll('.video-card').length, 120, 'the 20 older videos stay');
  assert.match(container.querySelector('.list-footer').textContent, /Showing 120 of 150/);
  assert.match(container.querySelector('[data-render-id="150"]').textContent, /Up to date/);
  handle.destroy();
});

test('videos: lectures sharing a title get the same suffix as on the project list', async () => {
  const { distinctTitles } = await import('../../js/studio/lib/names.js');
  fresh();
  const lecture = (id, session, created) => ({ project_id: id, session_number: session, unit_name: 'Circuits', subject_name: 'BEE', session_title: "Ohm's law", project_language: 'en-IN', project_created_at: created });
  const { container, handle } = await mountPage({
    [LIST]: { items: [video(3, lecture(9, '1', '2026-10-02T10:00:00Z')), video(2, lecture(5, '2', '2026-10-01T10:00:00Z'))], total: 2 },
  });
  const titles = [...container.querySelectorAll('.video-card .card-title')].map((el) => el.textContent);
  assert.deepEqual(titles, ["Ohm's law · 1", "Ohm's law · 2"], 'project 9 is session 1 although it is the newer project');
  // what the project list gives the same lectures
  const projects = [
    { id: 5, title: "Ohm's law", session_number: '2', unit_name: 'Circuits', subject_name: 'BEE', language: 'en-IN', created_at: '2026-10-01T10:00:00Z' },
    { id: 9, title: "Ohm's law", session_number: '1', unit_name: 'Circuits', subject_name: 'BEE', language: 'en-IN', created_at: '2026-10-02T10:00:00Z' },
  ];
  const suffixes = distinctTitles(projects);
  assert.deepEqual([suffixes.get(9), suffixes.get(5)], ['1', '2']);
  handle.destroy();
});
