import { resetDom, mockFetch, tick, window, FakeEventSource } from './_dom.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  mountVisualReview,
  sourceLabel,
  provenanceText,
  reviewState,
  isApproved,
  approvedBeforeChange,
  needsAttention,
  isListed,
  summarize,
  summaryText,
  pickerKind,
  uploadAccept,
  checkUpload,
  FILTERS,
} from '../../js/studio/views/visualReview.js';
import { resolveHash, href } from '../../js/studio/router.js';
import { closeAllModals } from '../../js/studio/components/modal.js';
import { saveDraft } from '../../js/studio/lib/draftStore.js';
import { fakeApp, byText, clickModal, modalButtons, waitFor, jsonBody, deferred } from './_views.js';
import { sampleVersion, sampleScreenplay } from './fixtures.js';

const VID = 9;
const REVIEW = `/api/versions/${VID}/visual-review`;
const ACTION = (id) => `/api/versions/${VID}/scenes/${id}/visual`;

/** A SceneVisual as GET /visual-review sends it. */
function sv(over = {}) {
  return {
    scene_id: 's2',
    index: 1,
    title: 'The law',
    kind: 'image',
    source: 'generated',
    provider: 'pollinations',
    model: 'flux',
    prompt: 'A water pipe with a pump, labelled',
    url: '/media/img-s2.png?sig=a',
    poster_url: null,
    status: 'ready',
    status_reason: null,
    review: { state: 'pending', stale: false, note: null, updated_at: null },
    variant: 0,
    actions: ['approve', 'new_version', 'choose_library', 'upload', 'remove'],
    findings: [],
    library_suggestions: 0,
    ...over,
  };
}

function scenes() {
  return [
    sv({ scene_id: 's1', index: 0, title: "Ohm's Law", kind: 'none', source: 'none', provider: null, model: null, prompt: null, url: null, actions: [] }),
    sv(),
    sv({ scene_id: 's3', index: 2, title: 'Worked example', kind: 'video', source: 'fallback', provider: null, model: null, url: null, poster_url: '/media/still-s3.jpg', status: 'ambiguous', actions: ['approve', 'choose_library', 'upload', 'retry', 'confirm_paid_retry'], findings: [{ code: 'video.ambiguous_submission', severity: 'warning', message: 'The video may already have been paid for.' }] }),
    sv({ scene_id: 's4', index: 3, title: 'Check', kind: 'chart', source: 'builtin', provider: null, model: null, prompt: null, url: null, review: { state: 'approved', stale: false, note: 'Fine for now', updated_at: '2026-10-01T10:00:00Z' }, actions: ['approve'] }),
  ];
}

const live = [];

function fresh() {
  for (const hd of live.splice(0)) {
    try {
      hd.destroy();
    } catch {
      /* already gone */
    }
  }
  resetDom();
  closeAllModals();
  FakeEventSource.instances = [];
  localStorage.clear();
}

/**
 * @param {Record<string, any>} [extra]
 * @param {{ version?: any, scenes?: any[] }} [opts]
 */
function routes(extra = {}, opts = {}) {
  return mockFetch({
    [`GET /api/versions/${VID}`]: opts.version || sampleVersion({ has_timeline: true }),
    [`GET ${REVIEW}`]: () => ({ summary: { total: 3, approved: 1, pending: 2, changed: 0, removed: 0, needs_attention: 1 }, scenes: opts.scenes || scenes() }),
    'GET /api/jobs?project_id=5&limit=20': { items: [], total: 0 },
    ...extra,
  });
}

async function mountPage(app, query = {}, deps = {}) {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const handle = await mountVisualReview(container, { app, params: { vid: VID }, query }, deps);
  if (handle) live.push(handle);
  return { container, handle };
}

/** @param {HTMLElement} root @param {string} id */
const cardOf = (root, id) => root.querySelector(`article[data-scene="${id}"]`);

// --- pure helpers ----------------------------------------------------------------------------

test('visual review helpers: sources, provenance and states in plain words', () => {
  assert.equal(sourceLabel(sv()), 'AI picture');
  assert.equal(sourceLabel(sv({ kind: 'video' })), 'AI video');
  assert.equal(sourceLabel(sv({ source: 'library' })), 'From your library');
  assert.equal(sourceLabel(sv({ source: 'upload' })), 'Your upload');
  assert.equal(sourceLabel(sv({ source: 'figure', kind: 'figure' })), 'Figure from your document');
  assert.equal(sourceLabel(sv({ source: 'builtin', kind: 'model_3d' })), 'Built-in 3D model');
  assert.equal(sourceLabel(sv({ source: 'builtin', kind: 'something_new' })), 'Built-in visual');
  assert.equal(sourceLabel(sv({ source: 'fallback' })), 'Backup still');
  assert.equal(sourceLabel(sv({ source: 'none' })), 'No visual');
  assert.equal(provenanceText(sv()), 'Made by pollinations (flux)');
  assert.equal(provenanceText(sv({ model: null })), 'Made by pollinations');
  assert.equal(provenanceText(sv({ provider: null })), null);
  assert.equal(reviewState(sv({ review: { state: 'weird' } })), 'pending');
  assert.equal(reviewState(sv({ review: null })), 'pending');
  assert.ok(isApproved(sv({ review: { state: 'approved', stale: false } })));
  assert.ok(!isApproved(sv({ review: { state: 'approved', stale: true } })), 'an approval made before a change no longer counts');
  assert.ok(needsAttention(sv({ status: 'failed' })) && !needsAttention(sv()));
  assert.ok(needsAttention(sv({ review: { state: 'approved', stale: true } })), 'a decision made before a change needs a look');
});

test('visual review helpers: listing, counts, filters and upload rules', () => {
  const all = scenes();
  assert.deepEqual(all.filter(isListed).map((s) => s.scene_id), ['s2', 's3', 's4'], 'a scene that never had a visual is not listed');
  assert.ok(isListed(sv({ kind: 'none', source: 'none', review: { state: 'removed', stale: false } })), 'a removed visual stays listed');
  assert.ok(!isListed(sv({ kind: 'none', source: 'none', status: 'stale' })), 'an edited scene without a visual is not listed');
  const s = summarize(all);
  assert.deepEqual(s, { total: 3, approved: 1, not_approved: 2, needs_attention: 1 });
  assert.equal(summaryText(s), '3 visuals: 1 approved, 2 not approved, 1 needs attention.');
  assert.equal(summaryText({ total: 1, approved: 0, not_approved: 1, needs_attention: 2 }), '1 visual: 0 approved, 1 not approved, 2 need attention.');
  assert.equal(summaryText({ total: 0, approved: 0, not_approved: 0, needs_attention: 0 }), 'No scene of this lecture has a visual to review.');
  assert.deepEqual(all.filter(isListed).filter(FILTERS.attention.test).map((x) => x.scene_id), ['s3']);
  assert.deepEqual(all.filter(isListed).filter(FILTERS.not_approved.test).map((x) => x.scene_id), ['s2', 's3']);
  assert.equal(pickerKind(sv()), undefined, 'a picture panel may also take a video');
  assert.equal(pickerKind(sv({ kind: 'interactive' })), 'image');
  assert.equal(pickerKind(sv({ kind: 'manim' })), 'video');
  assert.equal(pickerKind(sv({ kind: 'chart' })), undefined);
  assert.ok(uploadAccept(sv({ kind: 'manim' })).every((e) => ['.mp4', '.webm'].includes(e)));
  assert.ok(uploadAccept(sv()).includes('.png') && uploadAccept(sv()).includes('.mp4'));
  assert.equal(checkUpload({ name: 'pump.png', size: 1000, type: 'image/png' }, sv(), 50), null);
  assert.equal(checkUpload({ name: 'clip.mp4', size: 1000, type: 'video/mp4' }, sv(), 50), null);
  assert.match(String(checkUpload({ name: 'clip.mp4', size: 1000, type: 'video/mp4' }, sv({ kind: 'interactive' }), 50)), /image|picture/i, 'a sketch still refuses a video');
  assert.match(String(checkUpload({ name: 'pump.png', size: 1000, type: 'image/png' }, sv({ kind: 'manim' }), 50)), /video/i);
  assert.match(String(checkUpload({ name: 'big.png', size: 60 * 1024 * 1024, type: 'image/png' }, sv(), 50)), /50 MB/);
});

test('router: #/v/:vid/review is the visual review', () => {
  const r = resolveHash('#/v/9/review?scene=s3');
  assert.equal(r && r.route.name, 'visualReview');
  assert.deepEqual(r && r.params, { vid: 9 });
  assert.equal(r && r.query.scene, 's3');
  assert.equal(href('visualReview', { vid: 9 }, { filter: 'attention' }), '#/v/9/review?filter=attention');
  assert.equal(resolveHash('#/v/abc/review'), null);
});

// --- the page --------------------------------------------------------------------------------

test('visual review: cards, counts, sources and filters', async () => {
  fresh();
  routes();
  const hashes = [];
  const app = fakeApp({ replaceHash: (h) => hashes.push(h) });
  const { container } = await mountPage(app);
  assert.equal(container.querySelector('h1').textContent, 'Visual review');
  assert.deepEqual([...container.querySelectorAll('article[data-scene]')].map((a) => a.dataset.scene), ['s2', 's3', 's4']);
  assert.equal(container.querySelector('.vr-summary').textContent, '3 visuals: 1 approved, 2 not approved, 1 needs attention.');
  assert.match(container.querySelector('.vr-hidden-note').textContent, /1 scene without a visual is not listed/);
  const s2 = cardOf(container, 's2');
  assert.match(s2.textContent, /AI picture/);
  assert.match(s2.textContent, /Made by pollinations \(flux\)/);
  assert.equal(s2.querySelector('img').getAttribute('alt'), 'Visual of Scene 2: The law');
  assert.equal(s2.querySelector('details.vr-prompt summary').textContent, 'Prompt');
  assert.match(s2.querySelector('details.vr-prompt p').textContent, /water pipe/);
  const s3 = cardOf(container, 's3');
  assert.match(s3.textContent, /Backup still/);
  assert.match(s3.textContent, /Waiting for your decision/);
  assert.match(s3.textContent, /may already have been paid for/, 'the reason is said in plain words');
  assert.ok(s3.classList.contains('needs-attention'));
  const s4 = cardOf(container, 's4');
  assert.match(s4.textContent, /Built-in chart/);
  assert.match(s4.textContent, /A chart the player draws/);
  assert.match(s4.textContent, /Note: Fine for now/);
  assert.ok(byText(s4, 'Undo approval'), 'an approved visual offers to undo the approval');
  assert.deepEqual([...container.querySelectorAll('.vr-filters button')].map((b) => b.textContent), ['All (3)', 'Needs attention (1)', 'Not approved (2)']);

  byText(container, 'Needs attention').click();
  assert.deepEqual([...container.querySelectorAll('article[data-scene]')].map((a) => a.dataset.scene), ['s3']);
  assert.equal(byText(container, 'Needs attention').getAttribute('aria-pressed'), 'true');
  assert.equal(hashes.at(-1), '#/v/9/review?filter=attention');
  byText(container, 'Not approved').click();
  assert.deepEqual([...container.querySelectorAll('article[data-scene]')].map((a) => a.dataset.scene), ['s2', 's3']);
});

test('visual review: a scene skipped in the video is marked and never needs attention', async () => {
  fresh();
  assert.equal(needsAttention(sv({ status: 'failed', hidden: true })), false);
  assert.equal(needsAttention(sv({ review: { state: 'approved', stale: true }, hidden: true })), false);
  const list = scenes();
  list[2] = { ...list[2], hidden: true, actions: ['approve', 'choose_library', 'upload'] };
  routes({}, { scenes: list });
  const { container } = await mountPage(fakeApp());
  const s3 = cardOf(container, 's3');
  assert.ok(s3.querySelector('.badge[data-hidden="true"]'));
  assert.match(s3.querySelector('.badge[data-hidden="true"]').textContent, /Skipped in the video/);
  assert.match(s3.querySelector('.vr-skipped-note').textContent, /Show it again in the editor/);
  assert.equal(s3.classList.contains('needs-attention'), false);
  assert.equal(cardOf(container, 's2').querySelector('.badge[data-hidden]'), null);
  assert.equal(container.querySelector('.vr-summary').textContent, '3 visuals: 1 approved, 2 not approved, 0 need attention.');
});

test('visual review: approve and undo keep focus and update the counts', async () => {
  fresh();
  const calls = routes({
    'PUT /api/versions/9/visual-review/s2': ({ init }) => {
      const body = JSON.parse(init.body);
      return sv({ review: { state: body.state, stale: false, note: null, updated_at: '2026-10-08T10:00:00Z' } });
    },
  });
  const { container } = await mountPage(fakeApp());
  const approve = byText(cardOf(container, 's2'), 'Approve');
  approve.focus();
  approve.click();
  await waitFor(() => byText(cardOf(container, 's2'), 'Undo approval'));
  // an approval carries the revision the list was read at (intended change: the server refuses an older one)
  assert.deepEqual(jsonBody(calls.find((c) => c.method === 'PUT')), { state: 'approved', revision: 4 });
  assert.equal(document.activeElement.textContent, 'Undo approval', 'focus stays on the same control');
  assert.equal(container.querySelector('.vr-summary').textContent, '3 visuals: 2 approved, 1 not approved, 1 needs attention.');
  assert.match(container.querySelector('[aria-live="polite"].sr-only').textContent, /approved/);
  byText(cardOf(container, 's2'), 'Undo approval').click();
  await waitFor(() => byText(cardOf(container, 's2'), 'Approve'));
  assert.deepEqual(jsonBody(calls.filter((c) => c.method === 'PUT')[1]), { state: 'pending' });
  assert.equal(calls.filter((c) => c.method === 'POST').length, 0, 'approving never builds anything');
});

test('visual review: a new AI version is confirmed, sends the revision and follows the build', async () => {
  fresh();
  let reviews = 0;
  const calls = routes({
    [`GET ${REVIEW}`]: () => {
      reviews += 1;
      return { summary: {}, scenes: reviews > 1 ? scenes().map((s) => (s.scene_id === 's2' ? { ...s, variant: 1, review: { state: 'changed', stale: false, note: null, updated_at: null } } : s)) : scenes() };
    },
    [`POST ${ACTION('s2')}`]: { revision: 5, scene: sv({ variant: 1, status: 'missing', url: null }), job_id: 77 },
    'GET /api/jobs/77': { id: 77, kind: 'build_assets', status: 'succeeded', progress: 1, version_id: VID, project_id: 5 },
  });
  const { container } = await mountPage(fakeApp());
  byText(cardOf(container, 's2'), 'New AI version').click();
  await tick();
  assert.match(document.querySelector('.modal').textContent, /costs money and counts toward your budget/);
  clickModal('Cancel');
  await tick();
  assert.equal(calls.filter((c) => c.method === 'POST').length, 0, 'cancel makes nothing');
  byText(cardOf(container, 's2'), 'New AI version').click();
  await tick();
  clickModal('Make a new version');
  await waitFor(() => reviews > 1);
  assert.deepEqual(jsonBody(calls.find((c) => c.method === 'POST')), { action: 'new_version', revision: 4 });
  await waitFor(() => cardOf(container, 's2') && cardOf(container, 's2').dataset.state === 'changed');
  assert.ok(calls.some((c) => c.path === '/api/jobs/77'), 'the build is followed');
});

test('visual review: choose from library sends the item and the newer revision', async () => {
  fresh();
  let revisionSeen = [];
  const calls = routes({
    [`POST ${ACTION('s2')}`]: ({ init }) => {
      revisionSeen.push(JSON.parse(init.body).revision);
      return { revision: 6, scene: sv({ source: 'library', provider: null, model: null, review: { state: 'changed', stale: false, note: null, updated_at: null } }), job_id: null };
    },
  });
  const picks = [];
  const { container } = await mountPage(fakeApp(), {}, {
    pickLibraryItem: async (opts) => {
      picks.push(opts);
      return { id: 12, title: 'Pump diagram', kind: 'image' };
    },
  });
  byText(cardOf(container, 's2'), 'Choose from library').click();
  await waitFor(() => cardOf(container, 's2').dataset.state === 'changed');
  assert.equal(picks[0].kind, undefined, 'a picture panel may take a picture or a video');
  assert.equal(picks[0].projectId, undefined, 'the server attaches the item itself');
  assert.deepEqual(jsonBody(calls.find((c) => c.method === 'POST')), { action: 'choose_library', library_item_id: 12, revision: 4 });
  assert.match(cardOf(container, 's2').textContent, /From your library/);
  assert.match(cardOf(container, 's2').textContent, /Changed by you/);
  byText(cardOf(container, 's2'), 'Choose from library').click();
  await waitFor(() => revisionSeen.length === 2);
  assert.equal(revisionSeen[1], 6, 'the next change carries the revision the server returned');
});

test('visual review: library matches open the picker; closing it changes nothing', async () => {
  fresh();
  const calls = routes({}, { scenes: scenes().map((s) => (s.scene_id === 's2' ? { ...s, library_suggestions: 2 } : s)) });
  let opened = 0;
  const { container } = await mountPage(fakeApp(), {}, {
    pickLibraryItem: async () => {
      opened += 1;
      return null;
    },
  });
  const link = byText(cardOf(container, 's2'), '2 matches in your library');
  assert.ok(link);
  assert.ok(!byText(cardOf(container, 's3'), 'in your library'), 'no link without matches');
  link.click();
  await tick();
  await tick();
  assert.equal(opened, 1);
  assert.equal(calls.filter((c) => c.method === 'POST').length, 0);
});

test('visual review: a revision conflict reloads the list with a toast', async () => {
  fresh();
  let versions = 0;
  routes({
    [`GET /api/versions/${VID}`]: () => {
      versions += 1;
      return sampleVersion({ has_timeline: true, revision: versions > 1 ? 7 : 4 });
    },
    [`POST ${ACTION('s2')}`]: { status: 409, body: { detail: 'Screenplay changed', code: 'revision_conflict', current_revision: 7 } },
  });
  const app = fakeApp();
  const { container } = await mountPage(app, {}, { pickLibraryItem: async () => ({ id: 3 }) });
  byText(cardOf(container, 's2'), 'Choose from library').click();
  await waitFor(() => versions === 2);
  await waitFor(() => app.rec.toasts.some((t) => t.kind === 'warning' && /reloaded/.test(t.message)));
  assert.equal(app.rec.errors.length, 0);
  assert.ok(cardOf(container, 's2'), 'the list is shown again');
});

test('visual review: a possibly paid AI video is generated again only after its own confirmation', async () => {
  fresh();
  const calls = routes({
    [`POST ${ACTION('s3')}`]: { revision: 4, scene: sv({ scene_id: 's3', index: 2, kind: 'video', source: 'fallback', status: 'missing', url: null, actions: ['approve'] }), job_id: null },
  });
  const { container } = await mountPage(fakeApp());
  assert.equal(byText(cardOf(container, 's3'), 'Try again'), null, 'no plain retry next to the paid one');
  byText(cardOf(container, 's3'), 'Generate again…').click();
  await tick();
  assert.match(document.querySelector('.modal').textContent, /paying for this video twice/);
  assert.equal(modalButtons()[0].textContent.trim(), 'Cancel');
  clickModal('Cancel');
  await tick();
  assert.equal(calls.filter((c) => c.method === 'POST').length, 0);
  byText(cardOf(container, 's3'), 'Generate again…').click();
  await tick();
  clickModal('Generate again (may be paid twice)');
  await waitFor(() => calls.some((c) => c.method === 'POST'));
  assert.deepEqual(jsonBody(calls.find((c) => c.method === 'POST')), { action: 'retry', confirm_paid: true, revision: 4 });
});

test('visual review: a plain retry that needs a paid confirmation asks, then sends confirm_paid', async () => {
  fresh();
  const bodies = [];
  routes(
    {
      [`POST ${ACTION('s2')}`]: ({ init }) => {
        const body = JSON.parse(init.body);
        bodies.push(body);
        if (!body.confirm_paid) return { status: 409, body: { detail: 'May be billed twice', code: 'confirm_paid_required' } };
        return { revision: 4, scene: sv({ status: 'missing' }), job_id: null };
      },
    },
    { scenes: [sv({ kind: 'video', status: 'failed', actions: ['retry'] })] },
  );
  const app = fakeApp();
  const { container } = await mountPage(app);
  byText(cardOf(container, 's2'), 'Try again').click();
  await waitFor(() => document.querySelector('.modal'));
  clickModal('Generate again (may be paid twice)');
  await waitFor(() => bodies.length === 2);
  assert.deepEqual(bodies, [{ action: 'retry', revision: 4 }, { action: 'retry', revision: 4, confirm_paid: true }]);
  assert.equal(app.rec.errors.length, 0);
});

test('visual review: remove asks first', async () => {
  fresh();
  const calls = routes({ [`POST ${ACTION('s2')}`]: { revision: 5, scene: sv({ kind: 'none', source: 'none', url: null, review: { state: 'removed', stale: false, note: null, updated_at: null }, actions: ['choose_library', 'upload'] }), job_id: null } });
  const { container } = await mountPage(fakeApp());
  byText(cardOf(container, 's2'), 'Remove visual').click();
  await tick();
  clickModal('Cancel');
  await tick();
  assert.equal(calls.filter((c) => c.method === 'POST').length, 0);
  byText(cardOf(container, 's2'), 'Remove visual').click();
  await tick();
  clickModal('Remove visual');
  await waitFor(() => cardOf(container, 's2').dataset.state === 'removed');
  assert.deepEqual(jsonBody(calls.find((c) => c.method === 'POST')), { action: 'remove', revision: 4 });
  assert.match(cardOf(container, 's2').textContent, /Removed by you/);
});

test('visual review: an upload joins the library and replaces the visual; a wrong file is refused', async () => {
  fresh();
  const calls = routes({
    'POST /api/library': { status: 201, body: { id: 31, title: 'pump', kind: 'image', asset_key: 'upload:abc', url: '/media/pump.png' } },
    [`POST ${ACTION('s2')}`]: { revision: 5, scene: sv({ source: 'upload', provider: null, model: null, review: { state: 'changed', stale: false, note: null, updated_at: null } }), job_id: null },
  });
  const app = fakeApp();
  const { container } = await mountPage(app);
  const input = cardOf(container, 's2').querySelector('input[type="file"]');
  assert.ok(input.getAttribute('accept').includes('.png'));
  const send = (file) => {
    Object.defineProperty(input, 'files', { value: [file], configurable: true });
    input.dispatchEvent(new window.Event('change'));
  };
  send(new window.File(['x'], 'notes.txt', { type: 'text/plain' }));
  await tick();
  assert.ok(app.rec.toasts.some((t) => t.kind === 'error'), 'a file that is not a picture or video is refused');
  assert.equal(calls.filter((c) => c.method === 'POST').length, 0);
  send(new window.File(['png'], 'pump.png', { type: 'image/png' }));
  await waitFor(() => cardOf(container, 's2').dataset.state === 'changed');
  const upload = calls.find((c) => c.path === '/api/library');
  assert.equal(upload.init.body.get('file').name, 'pump.png');
  assert.deepEqual(jsonBody(calls.find((c) => c.path === ACTION('s2'))), { action: 'choose_library', library_item_id: 31, revision: 4 });
  assert.match(cardOf(container, 's2').textContent, /Your upload/);
});

test('visual review: server text is shown as text, never as HTML', async () => {
  fresh();
  const evil = '<img src=x onerror="window.__pwned=1">';
  routes({}, { scenes: [sv({ title: evil, prompt: evil, status: 'failed', status_reason: evil, findings: [{ code: 'x', severity: 'error', message: evil }] })] });
  const { container } = await mountPage(fakeApp());
  const card = cardOf(container, 's2');
  assert.ok(card.textContent.includes(evil));
  assert.ok(![...card.querySelectorAll('img')].some((i) => i.getAttribute('src') === 'x'));
  assert.equal(window.__pwned, undefined);
});

test('visual review: a video plays only when asked', async () => {
  fresh();
  routes({}, {
    scenes: [
      sv({ scene_id: 's5', index: 4, title: 'Footage', kind: 'video', url: '/media/clip.mp4?sig=1', poster_url: '/media/clip.jpg?sig=1' }),
      sv({ scene_id: 's6', index: 5, title: 'Still', kind: 'video', source: 'fallback', status: 'fallback', url: '/media/still.jpg?sig=1' }),
    ],
  });
  const { container } = await mountPage(fakeApp());
  const still = cardOf(container, 's6');
  assert.equal(still.querySelector('button.vr-play'), null, 'a backup still is a picture');
  assert.ok(still.querySelector('img').getAttribute('src').endsWith('/media/still.jpg?sig=1'));
  const card = cardOf(container, 's5');
  assert.equal(card.querySelector('video'), null);
  const play = card.querySelector('button.vr-play');
  assert.equal(play.getAttribute('aria-label'), 'Play the video of Scene 5: Footage');
  play.click();
  const video = card.querySelector('video');
  assert.ok(video && video.getAttribute('src').endsWith('/media/clip.mp4?sig=1'));
  assert.ok(video.hasAttribute('controls'));
});

test('visual review: unsaved editor changes are named, never touched', async () => {
  fresh();
  const changed = sampleScreenplay();
  changed.scenes[1].title = 'My unsaved title';
  saveDraft(7, VID, { screenplay: changed, revision: 4, base: sampleScreenplay() });
  routes();
  const { container } = await mountPage(fakeApp());
  const notice = container.querySelector('[data-draft="local"]');
  assert.ok(notice);
  assert.match(notice.textContent, /offers to merge/);
  assert.ok(notice.querySelector('a').getAttribute('href').endsWith('#/p/5/v/9/edit'));
  assert.ok(localStorage.length > 0, 'the draft is kept');
});

test('visual review: changed scenes offer a build, which is followed', async () => {
  fresh();
  const calls = routes(
    {
      [`POST /api/versions/${VID}/build`]: { status: 202, body: { job: { id: 81, kind: 'build_assets', status: 'queued', progress: 0, version_id: VID, project_id: 5 } } },
      'GET /api/jobs/81': { id: 81, kind: 'build_assets', status: 'running', progress: 0.5, version_id: VID, project_id: 5 },
    },
    { version: sampleVersion({ has_timeline: true, timeline_stale: true }), scenes: [sv({ status: 'stale', actions: ['approve', 'retry'] })] },
  );
  const { container } = await mountPage(fakeApp());
  assert.ok(byText(cardOf(container, 's2'), 'Build this scene'), 'a stale visual offers to build its scene');
  assert.match(container.querySelector('.vr-stale').textContent, /1 scene changed after its visual was made/);
  byText(container, 'Build changed scenes').click();
  await waitFor(() => !container.querySelector('.vr-job').hidden);
  assert.deepEqual(jsonBody(calls.find((c) => c.path === `/api/versions/${VID}/build`)), { scene_ids: null });
  assert.match(container.querySelector('.vr-job').textContent, /Building the changed scenes/);
});

test('visual review: a busy version is followed instead of failing', async () => {
  fresh();
  routes({
    [`POST ${ACTION('s2')}`]: { status: 409, body: { detail: 'busy', code: 'version_busy', job_id: 90 } },
    'GET /api/jobs/90': { id: 90, kind: 'build_assets', status: 'running', progress: 0.2, version_id: VID, project_id: 5 },
  });
  const app = fakeApp();
  const { container } = await mountPage(app, {}, { pickLibraryItem: async () => ({ id: 1 }) });
  byText(cardOf(container, 's2'), 'Choose from library').click();
  await waitFor(() => !container.querySelector('.vr-job').hidden);
  assert.equal(app.rec.errors.length, 0);
  assert.ok(app.rec.toasts.some((t) => t.kind === 'warning'));
});

test('visual review: ?scene= shows and focuses that scene', async () => {
  fresh();
  routes();
  const { container } = await mountPage(fakeApp(), { scene: 's4', filter: 'attention' });
  assert.equal(byText(container, 'All (3)').getAttribute('aria-pressed'), 'true', 'a filter hiding the scene is reset');
  await waitFor(() => document.activeElement && document.activeElement.classList.contains('vr-card-title'));
  assert.equal(document.activeElement.closest('article').dataset.scene, 's4');
});

test('visual review: a version that is not ready is shown without actions', async () => {
  fresh();
  routes({}, { version: sampleVersion({ status: 'generating' }) });
  const { container } = await mountPage(fakeApp());
  assert.match(container.textContent, /You can look at its visuals/);
  assert.equal(container.querySelectorAll('.vr-actions').length, 0);
});

test('visual review: a version without a screenplay has nothing to review', async () => {
  fresh();
  routes({ [`GET ${REVIEW}`]: { status: 409, body: { detail: 'not ready', code: 'not_ready' } } }, { version: sampleVersion({ status: 'generating', screenplay: null }) });
  const app = fakeApp();
  const { container } = await mountPage(app);
  assert.match(container.textContent, /There is nothing to review yet/);
  assert.equal(app.rec.errors.length, 0);
});

test('visual review: the library picker of the Library page is used by default', async () => {
  fresh();
  const item = { id: 44, asset_key: 'upload:pump', kind: 'image', title: 'Pump', description: '', keywords: [], source: 'upload', width: 640, height: 360, duration_s: null, url: '/media/pump.png', poster_url: null, created_at: '2026-10-01T10:00:00Z', updated_at: '2026-10-01T10:00:00Z', last_used_at: null, used_in: 0, prompt: null, provider: null, model: null };
  const calls = routes({
    're:^GET /api/library\\?': { items: [item], total: 1 },
    [`POST ${ACTION('s2')}`]: { revision: 5, scene: sv({ source: 'library', review: { state: 'changed', stale: false, note: null, updated_at: null } }), job_id: null },
  });
  const { container } = await mountPage(fakeApp());
  byText(cardOf(container, 's2'), 'Choose from library').click();
  const use = await waitFor(() => document.querySelector('.modal [data-action="use"]'));
  use.click();
  await waitFor(() => calls.some((c) => c.path === ACTION('s2')));
  assert.deepEqual(jsonBody(calls.find((c) => c.path === ACTION('s2'))), { action: 'choose_library', library_item_id: 44, revision: 4 });
  assert.ok(!calls.some((c) => /\/attach$/.test(c.path)), 'the review page lets the server attach the item');
});

test('visual review: actions carry the revision the review was read at (when the server sends it)', async () => {
  fresh();
  const calls = routes({
    [`GET /api/versions/${VID}`]: sampleVersion({ has_timeline: true, revision: 4 }),
    [`GET ${REVIEW}`]: () => ({ summary: {}, scenes: scenes(), revision: 3 }),
    [`POST ${ACTION('s2')}`]: { revision: 4, scene: sv({ kind: 'none', source: 'none', url: null, review: { state: 'removed', stale: false, note: null, updated_at: null }, actions: ['choose_library', 'upload'] }), job_id: null },
  });
  const { container } = await mountPage(fakeApp());
  byText(cardOf(container, 's2'), 'Remove').click();
  await tick();
  const confirm = modalButtons().find((b) => /Remove/.test(b.textContent));
  if (confirm) confirm.click();
  await waitFor(() => calls.some((c) => c.method === 'POST'));
  assert.equal(jsonBody(calls.find((c) => c.method === 'POST')).revision, 3, 'the scenes shown are those of revision 3');
});

// --- batch 3 review fixes ---------------------------------------------------------------------

test('visual review helpers: an approval made before a change, and the kinds a scene accepts', () => {
  const before = sv({ review: { state: 'pending', stale: true, note: null, updated_at: null } }); // as the server sends it
  assert.ok(approvedBeforeChange(before) && needsAttention(before) && !isApproved(before));
  assert.ok(approvedBeforeChange(sv({ review: { state: 'approved', stale: true } })), 'the older shape is understood too');
  assert.ok(!approvedBeforeChange(sv()) && !approvedBeforeChange(sv({ review: { state: 'changed', stale: true } })));
  assert.equal(pickerKind(sv({ kind: 'video', accepts: ['video'] })), 'video', 'an animation replaced by a clip still takes videos only');
  assert.equal(pickerKind(sv({ kind: 'manim', accepts: ['image', 'video'] })), undefined, 'the server decides');
  assert.equal(pickerKind(sv({ kind: 'image', accepts: ['image'] })), 'image');
  assert.ok(uploadAccept(sv({ kind: 'video', accepts: ['video'] })).every((e) => ['.mp4', '.webm'].includes(e)));
});

test('visual review: an approval made before a change says so and why it needs a look', async () => {
  fresh();
  routes({}, {
    scenes: [
      sv({ review: { state: 'pending', stale: true, note: 'Looks right', updated_at: null } }),
      sv({ scene_id: 's5', index: 4, title: 'Fresh', review: { state: 'pending', stale: false, note: null, updated_at: null } }),
    ],
  });
  const { container } = await mountPage(fakeApp());
  const card = cardOf(container, 's2');
  assert.ok(card.querySelector('.vr-review-stale'), 'the scene changed after the decision: said in words');
  assert.match(card.querySelector('.badges').textContent, /Approved before a change/);
  assert.equal(card.querySelector('[data-review]').dataset.review, 'pending');
  const plain = cardOf(container, 's5');
  assert.equal(plain.querySelector('.vr-review-stale'), null);
  assert.doesNotMatch(plain.textContent, /Approved before a change/);
});

test('visual review: an approval of a changed lecture reloads the list instead of approving', async () => {
  fresh();
  let reviews = 0;
  const calls = routes({
    [`GET ${REVIEW}`]: () => {
      reviews += 1;
      return { summary: {}, scenes: scenes(), revision: reviews > 1 ? 5 : 4 };
    },
    'PUT /api/versions/9/visual-review/s2': { status: 409, body: { detail: 'changed', code: 'revision_conflict', current_revision: 5 } },
  });
  const app = fakeApp();
  const { container } = await mountPage(app);
  byText(cardOf(container, 's2'), 'Approve').click();
  await waitFor(() => reviews === 2);
  await waitFor(() => app.rec.toasts.some((t) => t.kind === 'warning' && /reloaded/.test(t.message)));
  assert.equal(app.rec.errors.length, 0);
  assert.deepEqual(jsonBody(calls.find((c) => c.method === 'PUT')), { state: 'approved', revision: 4 });
  assert.ok(byText(cardOf(container, 's2'), 'Approve'), 'still not approved');
});

test('visual review: library matches open the picker with the scene\'s matches first', async () => {
  fresh();
  const item = (id) => ({ id, asset_key: `upload:${id}`, kind: 'image', title: `Match ${id}`, source: 'upload' });
  const calls = routes(
    { [`GET /api/library/suggestions?version_id=${VID}`]: { scenes: [{ scene_id: 's1', matches: [] }, { scene_id: 's2', matches: [{ item: item(21), score: 0.8 }, { item: item(22), score: 0.5 }] }] } },
    { scenes: scenes().map((s) => (s.scene_id === 's2' ? { ...s, library_suggestions: 2 } : s)) },
  );
  const picks = [];
  const { container } = await mountPage(fakeApp(), {}, {
    pickLibraryItem: async (opts) => {
      picks.push(opts);
      return null;
    },
  });
  byText(cardOf(container, 's2'), '2 matches in your library').click();
  await waitFor(() => picks.length === 1);
  assert.deepEqual(picks[0].suggested.map((it) => it.id), [21, 22]);
  assert.ok(calls.some((c) => c.path === `/api/library/suggestions?version_id=${VID}`));
  byText(cardOf(container, 's2'), 'Choose from library').click();
  await waitFor(() => picks.length === 2);
  assert.equal(picks[1].suggested, undefined, 'the plain button opens the whole library');
});

test('visual review: when the matches cannot be read, the picker still opens', async () => {
  fresh();
  routes(
    { [`GET /api/library/suggestions?version_id=${VID}`]: { status: 429, body: { detail: 'slow down', code: 'rate_limited' } } },
    { scenes: scenes().map((s) => (s.scene_id === 's2' ? { ...s, library_suggestions: 1 } : s)) },
  );
  const picks = [];
  const app = fakeApp();
  const { container } = await mountPage(app, {}, { pickLibraryItem: async (opts) => (picks.push(opts), null) });
  byText(cardOf(container, 's2'), '1 match in your library').click();
  await waitFor(() => picks.length === 1);
  assert.deepEqual(picks[0].suggested, []);
  assert.equal(app.rec.errors.length, 0);
});

test('visual review: a replacement upload shows its progress, guards leaving and stops with the page', async () => {
  fresh();
  const hold = deferred();
  const calls = routes({ 'POST /api/library': () => hold.promise });
  const app = fakeApp();
  const { container, handle } = await mountPage(app);
  const input = cardOf(container, 's2').querySelector('input[type="file"]');
  Object.defineProperty(input, 'files', { value: [new window.File(['png'], 'pump.png', { type: 'image/png' })], configurable: true });
  input.dispatchEvent(new window.Event('change'));
  await waitFor(() => calls.some((c) => c.path === '/api/library'));
  const status = cardOf(container, 's2').querySelector('.vr-upload-status');
  assert.ok(status && !status.hidden && /Uploading pump\.png/.test(status.textContent), 'visible, not only for screen readers');
  assert.equal(typeof app.rec.guard, 'function', 'leaving asks first');
  assert.equal(container.dataset.dirty, 'true');
  handle.destroy();
  const upload = calls.find((c) => c.path === '/api/library');
  assert.equal(upload.init.signal.aborted, true, 'leaving the page stops the upload');
  hold.resolve({ status: 201, body: { id: 31, title: 'pump', kind: 'image' } });
  await tick();
  await tick();
  assert.ok(!calls.some((c) => c.path === ACTION('s2')), 'the scene is not changed after the page is left');
});

test('visual review: an upload the scene cannot use says the file stayed in the library, in plain words', async () => {
  fresh();
  routes({
    'POST /api/library': { status: 201, body: { id: 31, title: 'pump', kind: 'image' } },
    [`POST ${ACTION('s2')}`]: { status: 422, body: { detail: [{ loc: ['body', 'library_item_id'], msg: 'Choose a video for an animation scene.', type: 'library_item.kind' }], code: 'validation' } },
  });
  const app = fakeApp();
  const { container } = await mountPage(app);
  const input = cardOf(container, 's2').querySelector('input[type="file"]');
  Object.defineProperty(input, 'files', { value: [new window.File(['png'], 'pump.png', { type: 'image/png' })], configurable: true });
  input.dispatchEvent(new window.Event('change'));
  await waitFor(() => app.rec.toasts.some((t) => t.kind === 'error'));
  const toast = app.rec.toasts.find((t) => t.kind === 'error');
  assert.equal(toast.message, 'Your file was added to your library, but it could not be used here. Choose a video for an animation scene.');
  assert.doesNotMatch(toast.message, /library_item_id/);
  assert.equal(app.rec.errors.length, 0);
  assert.equal(cardOf(container, 's2').querySelector('.vr-upload-status').hidden, true, 'the upload status is cleared');
});

test('visual review: a job retried from its own box stops polling when the page is left', async () => {
  fresh();
  const calls = routes(
    {
      [`POST /api/versions/${VID}/build`]: { status: 202, body: { job: { id: 81, kind: 'build_assets', status: 'queued', progress: 0, version_id: VID, project_id: 5 } } },
      'GET /api/jobs/81': { id: 81, kind: 'build_assets', status: 'failed', progress: 0.4, error: 'provider down', version_id: VID, project_id: 5 },
      'POST /api/jobs/81/retry': { job: { id: 82, kind: 'build_assets', status: 'queued', progress: 0, version_id: VID, project_id: 5 } },
      'GET /api/jobs/82': { id: 82, kind: 'build_assets', status: 'running', progress: 0.1, version_id: VID, project_id: 5 },
    },
    { version: sampleVersion({ has_timeline: true, timeline_stale: true }), scenes: [sv({ status: 'stale', actions: ['approve', 'retry'] })] },
  );
  const { container, handle } = await mountPage(fakeApp());
  byText(container, 'Build changed scenes').click();
  const retryBtn = await waitFor(() => byText(container.querySelector('.vr-job'), 'Retry'));
  retryBtn.click();
  await waitFor(() => calls.some((c) => c.path === '/api/jobs/82'));
  handle.destroy();
  const seen = calls.filter((c) => c.path === '/api/jobs/82').length;
  await new Promise((r) => setTimeout(r, 3400));
  assert.equal(calls.filter((c) => c.path === '/api/jobs/82').length, seen, 'no polling after the page is left');
});
