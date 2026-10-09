import { resetDom, mockFetch, tick, window, FakeEventSource } from './_dom.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import { mount } from '../../js/studio/views/projectDetail.js';
import { closeAllModals } from '../../js/studio/components/modal.js';
import { draftKey } from '../../js/studio/lib/draftStore.js';
import { sampleScreenplay } from './fixtures.js';
import { fakeApp, byText, clickModal, waitFor, jsonBody } from './_views.js';

const V9 = { id: 9, number: 1, label: '', status: 'ready', language: 'en-IN', revision: 4, built_revision: 4, timeline_stale: false, has_timeline: true, source_version_id: null, issue_counts: {}, created_at: '2026-09-01T10:00:00Z', updated_at: '2026-09-01T10:00:00Z' };

function detail(project = {}, extra = {}) {
  return {
    project: { id: 5, title: "Ohm's Law", subject_name: 'BEE', unit_name: 'Circuits', session_number: 'S2', session_title: "Ohm's Law", language: 'en-IN', owner: { id: 7, username: 'teacher1' }, current_version: V9, active_job: null, created_at: V9.created_at, updated_at: V9.updated_at, ...project },
    versions: [project.current_version || V9],
    sources: [],
    jobs: [],
    ...extra,
  };
}

const render = (id, more = {}) => ({ id, version_id: 9, status: 'succeeded', language: 'en-IN', duration_s: 60, built_revision: 4, created_at: V9.created_at, downloads: { video: `/api/renders/${id}/download?file=video` }, chapters_text: '', options: {}, job: null, ...more });

const hash = (/** @type {Element} */ a) => String(a.getAttribute('href')).replace(/^[^#]*/, '');

function fresh(url = '/') {
  resetDom();
  closeAllModals();
  localStorage.clear();
  window.history.replaceState(null, '', url);
  FakeEventSource.instances = [];
}

async function mountPage(routes, app = fakeApp()) {
  const calls = mockFetch({ 'GET /api/projects/5/shares': { items: [] }, 'GET /api/versions/9/renders': { items: [] }, 'GET /api/versions/9/render/preflight': { version_id: 9, blocking: false, items: [] }, ...routes });
  const container = document.createElement('div');
  document.body.appendChild(container);
  const handle = await mount(container, { app, params: { id: 5 }, query: {} });
  await waitFor(() => container.querySelector('.page-header'));
  return { container, handle, calls, app };
}

const card = (container) => container.querySelector('.next-step');

test("next step: the server's stage in words, its link and the workflow strip", async () => {
  fresh();
  const v = { ...V9, status: 'awaiting_review', review_stage: 'plan', has_timeline: false };
  const { container, handle } = await mountPage({
    'GET /api/projects/5': detail({ current_version: v, stage: 'plan_review', next_step: { action: 'review_plan', label: 'Review the plan', href: '#/p/5/v/9/plan' } }),
  });
  const c = card(container);
  assert.equal(c.dataset.stage, 'plan_review');
  assert.match(c.querySelector('.stage-chip').textContent, /Plan ready for review/);
  assert.match(c.textContent, /Check it before Aadhi writes the scenes/);
  assert.equal(hash(byText(c, 'Review the plan', 'a')), '#/p/5/v/9/plan');
  const steps = [...c.querySelectorAll('.workflow-step')];
  assert.deepEqual(steps.map((s) => s.className.replace('workflow-step ', '')), ['is-done', 'is-current', 'is-todo', 'is-todo', 'is-todo']);
  assert.equal(steps[1].getAttribute('aria-current'), 'step');
  assert.match(steps[0].textContent, /Source \(done\)/);
  // the card comes right after the header, before the version table
  assert.ok(container.querySelector('.page-header').nextElementSibling === c);
  handle.destroy();
});

test('next step: "Build the lecture" starts a build on this page and follows it', async () => {
  fresh();
  const v = { ...V9, revision: 5, timeline_stale: true };
  const { container, handle, calls } = await mountPage({
    'GET /api/projects/5': detail({ current_version: v, stage: 'ready_to_build', next_step: { action: 'build', label: 'Build the lecture', href: null } }),
    'POST /api/versions/9/build': { status: 202, body: { job: { id: 40, kind: 'build_assets', status: 'queued', progress: 0, version_id: 9 } } },
  });
  const loads = () => calls.filter((c) => c.method === 'GET' && c.path === '/api/projects/5').length;
  const before = loads();
  byText(card(container), 'Build the lecture', 'button').click();
  await waitFor(() => calls.some((c) => c.method === 'POST' && c.path === '/api/versions/9/build'));
  assert.deepEqual(jsonBody(calls.find((c) => c.path === '/api/versions/9/build')), { scene_ids: null });
  await waitFor(() => /Building voice and visuals/.test(document.querySelector('.modal')?.textContent || ''));
  assert.ok(loads() > before, 'the page reloaded to show the running job');
  assert.equal(FakeEventSource.instances.at(-1).url, '/api/jobs/40/stream');
  closeAllModals();
  handle.destroy();
});

test('next step: a build refused because a job runs follows that job instead', async () => {
  fresh();
  const { container, handle, calls } = await mountPage({
    'GET /api/projects/5': detail({ stage: 'ready_to_build', next_step: { action: 'build', label: 'Build the lecture', href: null } }),
    'POST /api/versions/9/build': { status: 409, body: { detail: 'busy', code: 'job_in_progress', job_id: 41 } },
    'GET /api/jobs/41': { id: 41, kind: 'render_video', status: 'running', progress: 0.4, version_id: 9 },
  });
  const app = fakeApp();
  byText(card(container), 'Build the lecture', 'button').click();
  await waitFor(() => /Render MP4 in progress/.test(document.querySelector('.modal')?.textContent || ''));
  assert.ok(calls.some((c) => c.path === '/api/jobs/41'));
  assert.deepEqual(app.rec.errors, []);
  closeAllModals();
  handle.destroy();
});

test('next step: "Make the video" asks for the options, then renders', async () => {
  fresh();
  const { container, handle, calls } = await mountPage({
    'GET /api/projects/5': detail({ stage: 'ready_to_render', next_step: { action: 'render', label: 'Make the video', href: null } }),
    'POST /api/versions/9/render': { status: 202, body: { render: { id: 3 }, job: { id: 42, kind: 'render_video', status: 'queued', progress: 0, version_id: 9 } } },
  });
  byText(card(container), 'Make the video', 'button').click();
  await waitFor(() => document.querySelector('.modal'));
  const dialog = document.querySelector('.modal');
  assert.match(dialog.querySelector('.modal-title').textContent, /Make the video/);
  const [captions, intro] = dialog.querySelectorAll('input[type="checkbox"]');
  assert.equal(captions.checked, false);
  assert.equal(intro.checked, true);
  captions.click();
  clickModal('Make the video');
  await waitFor(() => calls.some((c) => c.method === 'POST' && c.path === '/api/versions/9/render'));
  assert.deepEqual(jsonBody(calls.find((c) => c.path === '/api/versions/9/render')), { burn_captions: true, include_intro: true, allow_degraded: false });
  await waitFor(() => /Making the video/.test(document.querySelector('.modal')?.textContent || ''));
  closeAllModals();
  handle.destroy();
});

test('next step: silent scenes offer "Fix first" (the editor) or "Make it anyway"', async () => {
  fresh();
  const pre = { version_id: 9, blocking: true, items: [{ scene_index: 1, scene_id: 's2', title: 'Ohm', reason: 'no_audio', blocking: true, message: 'No narration audio.' }] };
  const { container, handle, calls, app } = await mountPage({
    'GET /api/projects/5': detail({ stage: 'video_outdated', next_step: { action: 'render', label: 'Make the video again', href: null } }),
    'GET /api/versions/9/render/preflight': pre,
    'POST /api/versions/9/render': { status: 202, body: { job: { id: 43, kind: 'render_video', status: 'queued', progress: 0, version_id: 9 } } },
  });
  byText(card(container), 'Make the video again', 'button').click();
  await waitFor(() => document.querySelector('.modal [data-preflight="blocking"]'));
  clickModal('Fix first');
  await waitFor(() => app.rec.navs.length);
  assert.deepEqual(app.rec.navs, ['#/p/5/v/9/edit']);
  assert.ok(!calls.some((c) => c.method === 'POST'), 'nothing rendered');
  byText(card(container), 'Make the video again', 'button').click();
  await waitFor(() => document.querySelector('.modal [data-preflight="blocking"]'));
  clickModal('Make it anyway');
  await waitFor(() => calls.some((c) => c.method === 'POST' && c.path === '/api/versions/9/render'));
  assert.equal(jsonBody(calls.find((c) => c.path === '/api/versions/9/render')).allow_degraded, true);
  closeAllModals();
  handle.destroy();
});

test('next step: "Try again" retries the version\'s latest failed job', async () => {
  fresh();
  const failed = { id: 44, kind: 'build_assets', status: 'failed', progress: 0.5, version_id: 9, error: 'TTS quota', created_at: V9.created_at };
  const older = { id: 30, kind: 'render_video', status: 'failed', progress: 0.1, version_id: 9, created_at: V9.created_at };
  const { container, handle, calls } = await mountPage({
    'GET /api/projects/5': detail({ current_version: { ...V9, status: 'failed' }, stage: 'failed', next_step: { action: 'retry', label: 'Try again', href: null } }, { jobs: [failed, older] }),
    'POST /api/jobs/44/retry': { status: 202, body: { job: { ...failed, id: 45, status: 'queued', error: null } } },
  });
  const c = card(container);
  assert.match(c.textContent, /Your document and your edits are safe/);
  byText(c, 'Try again', 'button').click();
  await waitFor(() => calls.some((x) => x.method === 'POST' && x.path === '/api/jobs/44/retry'));
  assert.ok(!calls.some((x) => x.path === '/api/jobs/30/retry'));
  await waitFor(() => /trying again/.test(document.querySelector('.modal')?.textContent || ''));
  closeAllModals();
  handle.destroy();
});

test('next step: unsaved editor changes on this computer come first', async () => {
  fresh();
  localStorage.setItem('aadhi.studio.lastUser', '7');
  localStorage.setItem(draftKey(7, 9), JSON.stringify({ revision: 4, savedAt: new Date().toISOString(), screenplay: sampleScreenplay(), base: null }));
  const { container, handle } = await mountPage({
    'GET /api/projects/5': detail({ stage: 'ready_to_render', next_step: { action: 'render', label: 'Make the video', href: null } }),
  });
  const c = card(container);
  assert.ok(c.querySelector('[data-unsaved-draft]'));
  assert.equal(byText(c, 'Make the video', 'button'), null);
  assert.equal(hash(byText(c, 'Open the editor to save your changes', 'a')), '#/p/5/v/9/edit');
  handle.destroy();
});

test('next step: an older server (no stage) gets the stage derived from the version', async () => {
  fresh();
  const { container, handle } = await mountPage({ 'GET /api/projects/5': detail({ current_version: { ...V9, timeline_stale: true, built_revision: 3 } }) });
  const c = card(container);
  assert.equal(c.dataset.stage, 'ready_to_build');
  assert.ok(byText(c, 'Build the lecture', 'button'));
  handle.destroy();
});

test('next step: "Your video is ready" goes to the videos on this page; unknown actions show no button', async () => {
  fresh();
  const { container, handle } = await mountPage({
    'GET /api/projects/5': detail({ stage: 'video_ready', next_step: { action: 'download', label: 'Your video is ready', href: null } }),
    'GET /api/versions/9/renders': { items: [render(1, { matches_current: true })] },
  });
  byText(card(container), 'Your video is ready', 'button').click();
  await waitFor(() => document.activeElement && document.activeElement.textContent === 'Rendered videos');
  handle.destroy();

  fresh();
  const other = await mountPage({ 'GET /api/projects/5': detail({ stage: 'ready_to_render', next_step: { action: 'teleport', label: 'Beam it', href: null } }) });
  const c = card(other.container);
  assert.equal(byText(c, 'Beam it'), null, 'an action this page does not know is not offered');
  assert.match(c.textContent, /Ready to make the video/);
  other.handle.destroy();
});

test('renders: "Up to date" / "Out of date", a failed render says why and can be tried again', async () => {
  fresh();
  const failedJob = { id: 50, kind: 'render_video', status: 'failed', progress: 0.3, version_id: 9, error: 'ffmpeg exited with an error' };
  const { container, handle, calls } = await mountPage({
    'GET /api/projects/5': detail({ stage: 'video_outdated', next_step: null }),
    'GET /api/versions/9/renders': { items: [render(3, { matches_current: false }), render(2, { matches_current: true }), render(1, { status: 'failed', downloads: {}, job: failedJob }), render(0, {})] },
    'POST /api/jobs/50/retry': { status: 202, body: { job: { ...failedJob, id: 51, status: 'queued' } } },
  });
  const section = container.querySelector('[data-section="renders"]');
  await waitFor(() => section.querySelector('[data-render-id="0"]'));
  const row = (id) => section.querySelector(`[data-render-id="${id}"]`);
  assert.equal(row(3).querySelector('[data-matches-current]').textContent, 'Out of date');
  assert.equal(row(3).querySelector('[data-matches-current]').title, 'Made before your latest changes to the script');
  assert.equal(row(2).querySelector('[data-matches-current]').textContent, 'Up to date');
  assert.equal(row(0).querySelector('[data-matches-current]'), null, 'older servers: nothing');
  assert.equal(row(1).querySelector('[data-matches-current]'), null, 'only finished videos');
  // Download comes first
  assert.equal(row(3).querySelector('.btn').textContent, 'Download MP4');
  const failure = row(1).querySelector('.render-failure');
  assert.match(failure.textContent, /The video could not be made: ffmpeg exited with an error/);
  byText(failure, 'Try again', 'button').click();
  await waitFor(() => calls.some((c) => c.method === 'POST' && c.path === '/api/jobs/50/retry'));
  handle.destroy();
});

test('renders: "Watch" plays a video in the browser when the server offers a preview URL', async () => {
  fresh();
  const { container, handle } = await mountPage({
    'GET /api/projects/5': detail(),
    'GET /api/versions/9/renders': { items: [render(4, { preview_url: '/api/renders/4/download?file=video&inline=1&sig=abc' }), render(5)] },
  });
  const section = container.querySelector('[data-section="renders"]');
  await waitFor(() => section.querySelector('[data-render-id="5"]'));
  assert.equal(byText(section.querySelector('[data-render-id="5"]'), 'Watch', 'button'), null, 'no preview URL: no Watch');
  byText(section.querySelector('[data-render-id="4"]'), 'Watch', 'button').click();
  const video = await waitFor(() => document.querySelector('.modal video'));
  assert.match(video.getAttribute('src'), /\/api\/renders\/4\/download\?file=video&inline=1&sig=abc$/);
  assert.equal(video.hasAttribute('controls'), true);
  assert.equal(video.getAttribute('preload'), 'metadata');
  assert.ok(byText(document.querySelector('.modal'), 'Download MP4', 'a'));
  clickModal('Close');
  await tick();
  assert.equal(video.hasAttribute('src'), false, 'closing releases the video');
  handle.destroy();
});

test('technical details (render and job numbers, revisions, raw stages) only with ?debug', async () => {
  const job = { id: 21, kind: 'build_assets', status: 'succeeded', stage: 'assets', progress: 1, version_id: 9, created_at: V9.created_at, cost_usd: 0 };
  for (const [url, technical] of [['/', false], ['/?debug=1', true]]) {
    fresh(url);
    const { container, handle } = await mountPage({
      'GET /api/projects/5': detail({}, { jobs: [job] }),
      'GET /api/versions/9/renders': { items: [render(7)] },
    });
    const section = container.querySelector('[data-section="renders"]');
    await waitFor(() => section.querySelector('[data-render-id="7"]'));
    const text = container.textContent;
    assert.equal(/Render #7/.test(text), technical, `render number (${url})`);
    assert.equal(/revision 4/.test(text), technical, `render revision (${url})`);
    assert.equal([...container.querySelectorAll('tbody td')].some((td) => /^r4\b/.test(td.textContent.trim())), technical, `version revision (${url})`);
    assert.equal(/#21/.test(text), technical, `job number (${url})`);
    const jobs = byText(container, 'Jobs (1)', 'h2').closest('section');
    assert.match(jobs.textContent, technical ? /assets/ : /Voice & visuals/);
    assert.match(container.querySelector('tbody').textContent, /Up to date/);
    handle.destroy();
  }
  window.history.replaceState(null, '', '/');
});

test('the header shows each name part once, and not the title again', async () => {
  fresh();
  const { container, handle } = await mountPage({ 'GET /api/projects/5': detail({ subject_name: 'Physics', unit_name: 'physics', session_title: "ohm's law" }) });
  assert.equal(container.querySelector('.page-subtitle').textContent, 'Physics · S2');
  handle.destroy();
});

test('next step: link actions without a link get theirs here; share goes to the share links', async () => {
  fresh();
  const videos = await mountPage({ 'GET /api/projects/5': detail({ stage: 'video_ready', next_step: { action: 'videos', label: 'See your videos', href: null } }) });
  assert.equal(hash(byText(card(videos.container), 'See your videos', 'a')), '#/videos');
  videos.handle.destroy();

  fresh();
  const generating = { ...V9, status: 'generating', has_timeline: false };
  const editor = await mountPage({ 'GET /api/projects/5': detail({ current_version: generating, stage: 'writing', next_step: { action: 'open_editor', label: 'Open the editor', href: null } }) });
  assert.equal(byText(card(editor.container), 'Open the editor'), null, 'no editor while the lecture is being written');
  editor.handle.destroy();

  fresh();
  const share = await mountPage({ 'GET /api/projects/5': detail({ stage: 'video_ready', next_step: { action: 'share', label: 'Share it with your students', href: null } }) });
  byText(card(share.container), 'Share it with your students', 'button').click();
  await waitFor(() => document.activeElement && document.activeElement.textContent === 'Share with students');
  share.handle.destroy();
});

test('next step: a failed video of a built lecture shows the earlier steps done; other failures show no strip', async () => {
  fresh();
  const { container, handle } = await mountPage({
    'GET /api/projects/5': detail({ stage: 'failed', next_step: { action: 'render', label: 'The video could not be made. Try again', href: null } }),
  });
  const steps = [...card(container).querySelectorAll('.workflow-step')];
  assert.deepEqual(steps.map((s) => s.className.replace('workflow-step ', '')), ['is-done', 'is-done', 'is-done', 'is-done', 'is-current']);
  assert.match(steps[3].textContent, /Voice and visuals \(done\)/);
  handle.destroy();

  fresh();
  const other = await mountPage({
    'GET /api/projects/5': detail({ current_version: { ...V9, status: 'failed' }, stage: 'failed', next_step: { action: 'retry', label: 'Try again', href: null } }),
  });
  assert.equal(card(other.container).querySelector('.workflow-strip'), null, 'never "to do" for steps that may be done');
  other.handle.destroy();
});

test('renders: scenes in the preflight lists and render warnings are numbered as in the editor (skipped scenes counted)', async () => {
  fresh();
  const { container, handle } = await mountPage({
    'GET /api/projects/5': detail(),
    'GET /api/versions/9/renders': { items: [render(6, { options: { warnings: [{ scene_index: 1, scene_number: 3, scene_id: 's3', title: 'Example', reason: 'no_audio', message: 'Silent.' }, { scene_index: 2, scene_id: 's4', title: 'Quiz', reason: 'no_audio', message: 'Older render.' }] } })] },
    'GET /api/versions/9/render/preflight': {
      version_id: 9,
      blocking: true,
      items: [{ scene_index: 1, scene_number: 3, scene_id: 's3', title: 'Example', reason: 'no_audio', blocking: true, message: 'No narration audio.' }],
      quality: [{ scene_index: 1, scene_number: 3, scene_id: 's3', title: 'Example', reason: 'quality', blocking: false, message: 'Too long.', severity: 'warning', code: 'scene.too_long' }],
    },
  });
  const section = container.querySelector('[data-section="renders"]');
  await waitFor(() => section.querySelector('[data-render-id="6"]'));
  assert.deepEqual([...section.querySelectorAll('[data-preflight] li')].map((li) => li.textContent), ['Scene 3 (Example): No narration audio.']);
  assert.deepEqual([...section.querySelectorAll('.render-quality li')].map((li) => li.textContent), ['Please check: Scene 3 (Example): Too long.']);
  const warnings = [...section.querySelectorAll('[data-render-id="6"] .render-warnings li')].map((li) => li.textContent);
  assert.deepEqual(warnings, ['Scene 3 (Example): Silent.', 'Scene 3 (Quiz): Older render.'], 'an older stored warning falls back to its timeline place');
  handle.destroy();
});
