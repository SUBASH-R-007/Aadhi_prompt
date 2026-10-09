import { resetDom, mockFetch, tick, FakeEventSource } from './_dom.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import { jobProgress, pollBackoff, MAX_POLL_MS, PERSONAL_KEY_REJECTED } from '../../js/studio/components/jobProgress.js';
import { mount as mountProjects, matchActiveJobs, JOB_POLL_MS } from '../../js/studio/views/projects.js';
import { closeAllModals } from '../../js/studio/components/modal.js';
import { ApiError } from '../../js/shared/api.js';
import { fakeApp, byText } from './_views.js';

/** Let pending promise chains settle without using (possibly mocked) timers. */
async function settle(rounds = 12) {
  for (let i = 0; i < rounds; i++) await new Promise((r) => setImmediate(r));
}

function fresh() {
  resetDom();
  closeAllModals();
  FakeEventSource.instances = [];
}

test('job progress keeps keyboard focus on Cancel across progress updates', async () => {
  fresh();
  const job = { id: 5, kind: 'build_assets', status: 'running', stage: 'assets', progress: 0.3, message: 'Voicing' };
  const w = jobProgress(job, {});
  document.body.appendChild(w.el);
  const es = FakeEventSource.instances[0];
  const cancel = byText(w.el, 'Cancel', 'button');
  cancel.focus();
  es.emit('job', { ...job, progress: 0.31 });
  es.emit('job', { ...job, progress: 0.32, message: 'Voicing scene 3' });
  assert.equal(document.activeElement, cancel, 'still on Cancel after progress ticks');
  assert.ok(cancel.isConnected);
  assert.equal(w.el.querySelectorAll('.job-actions button').length, 2, 'buttons are reused, not re-created');
  // The job fails: Cancel disappears, focus moves to Retry (not to <body>).
  es.emit('end', { ...job, status: 'failed', error: 'TTS quota', progress: 0.32 });
  await tick();
  assert.equal(cancel.hidden, true);
  const retry = byText(w.el, 'Retry', 'button');
  assert.equal(retry.hidden, false);
  assert.equal(document.activeElement, retry);
  w.destroy();
});

test('a provider notice (slow or retried AI service) shows under the status until the job moves on', () => {
  fresh();
  const job = { id: 11, kind: 'generate_lecture', status: 'running', stage: 'plan', progress: 0.05, message: 'Extracting the concepts to teach' };
  const w = jobProgress(job, {});
  document.body.appendChild(w.el);
  const es = FakeEventSource.instances[0];
  const line = w.el.querySelector('.job-notice');
  assert.equal(line.hidden, true);
  const retry = "The AI service (OpenAI) didn't respond within 1 min; trying again (attempt 2 of 4).";
  es.emit('job_event', { id: 1, created_at: '2026-10-08T10:00:00Z', level: 'warning', stage: 'plan', message: retry, data: { notice: 'retry', provider: 'openai', attempt: 2 } });
  assert.equal(line.hidden, false);
  assert.equal(line.textContent, retry);
  assert.ok(line.classList.contains('job-notice-warning'));
  assert.equal(w.el.querySelector('.job-message').textContent, 'Extracting the concepts to teach', 'the status line stays');
  assert.equal(w.el.querySelectorAll('.job-log .log-warning').length, 1, 'the notice is in the live log too');
  const waiting = 'Still waiting for the AI service (OpenAI) to answer (attempt 2 of 4, 1 min so far).';
  es.emit('job_event', { id: 2, created_at: '2026-10-08T10:01:00Z', level: 'info', stage: 'plan', message: waiting, data: { notice: 'waiting' } });
  assert.equal(line.textContent, waiting);
  assert.equal(line.classList.contains('job-notice-warning'), false);
  // the answer arrived: the next progress event clears the line (an ordinary log line would too)
  es.emit('job_event', { id: 3, created_at: '2026-10-08T10:02:00Z', level: 'progress', stage: 'plan', message: 'Planning 6 scenes', progress: 0.08, data: {} });
  assert.equal(line.hidden, true);
  es.emit('job_event', { id: 4, created_at: '2026-10-08T10:03:00Z', level: 'warning', stage: 'script', message: retry, data: { notice: 'retry' } });
  assert.equal(line.hidden, false);
  es.emit('end', { ...job, status: 'succeeded', progress: 1 });
  assert.equal(line.hidden, true, 'never shown once the job has ended');
  w.destroy();
});

test('job progress hides the actions row when nothing applies and shows Review while paused', () => {
  fresh();
  const w = jobProgress({ id: 6, kind: 'render_video', status: 'succeeded', progress: 1 }, { reviewHref: '#/p/1/v/2/plan' });
  document.body.appendChild(w.el);
  assert.equal(w.el.querySelector('.job-actions').hidden, true);
  w.update({ id: 6, kind: 'render_video', status: 'awaiting_review' });
  assert.equal(w.el.querySelector('.job-actions').hidden, false);
  assert.equal(byText(w.el, 'Review plan', 'a').hidden, false);
  w.destroy();
});

test('a job failed on a rejected personal key links to the API keys page; other failures do not', () => {
  fresh();
  const failed = { id: 7, kind: 'generate_lecture', status: 'failed', progress: 0.1 };
  const w = jobProgress({ ...failed, error: 'Your personal Anthropic Claude API key was rejected (HTTP 401).', error_code: PERSONAL_KEY_REJECTED }, {});
  document.body.appendChild(w.el);
  const box = w.el.querySelector('.job-error');
  assert.equal(box.hidden, false);
  assert.match(box.textContent, /^Your personal Anthropic Claude API key was rejected/);
  assert.equal(byText(box, 'Open API keys', 'a').hash, '#/keys');
  w.update({ ...failed, error: 'TTS quota', error_code: 'provider' });
  assert.equal(box.textContent, 'TTS quota');
  assert.equal(box.querySelector('a'), null);
  w.update({ ...failed, error: 'over budget', error_code: 'budget' });
  assert.equal(box.textContent, 'Budget limit reached: over budget');
  w.destroy();
});

test('pollBackoff honours Retry-After, doubles on 5xx/network errors and stops on 404', () => {
  assert.equal(pollBackoff(new ApiError(429, { detail: 'slow down' }, 30), 3000, 3000), 30_000);
  assert.equal(pollBackoff(new ApiError(429, { detail: 'slow down' }, null), 3000, 3000), 6000);
  assert.equal(pollBackoff(new ApiError(503, { detail: 'x' }, null), 6000, 3000), 12_000);
  assert.equal(pollBackoff(new TypeError('Failed to fetch'), 40_000, 3000), MAX_POLL_MS);
  assert.equal(pollBackoff(new ApiError(404, { detail: 'gone' }, null), 3000, 3000), null);
  assert.equal(pollBackoff(new ApiError(403, { detail: 'no' }, null), 3000, 3000), null);
  assert.equal(pollBackoff(new ApiError(400, { detail: 'odd' }, null), 3000, 3000), 3000);
});

test('job polling backs off on 429 (Retry-After) instead of hammering the API', async () => {
  fresh();
  let n = 0;
  const calls = mockFetch({
    'GET /api/jobs/9': () => {
      n += 1;
      return n === 1 ? { status: 429, body: { detail: 'Too many requests', code: 'rate_limited' }, headers: { 'Retry-After': '1' } } : { id: 9, kind: 'build_assets', status: 'succeeded', progress: 1 };
    },
  });
  const finished = [];
  const w = jobProgress({ id: 9, kind: 'build_assets', status: 'running', progress: 0.2 }, { compact: true, stream: false, pollMs: 20, onFinished: (j) => finished.push(j.status) });
  await tick(200);
  assert.equal(calls.filter((c) => c.path === '/api/jobs/9').length, 1, 'waits for Retry-After (1 s), not pollMs (20 ms)');
  await tick(1100);
  assert.equal(calls.filter((c) => c.path === '/api/jobs/9').length, 2);
  assert.deepEqual(finished, ['succeeded']);
  w.destroy();
});

test('external job widgets neither stream nor poll; the owner pushes updates', async () => {
  fresh();
  const calls = mockFetch({});
  const done = [];
  const w = jobProgress({ id: 3, kind: 'translate', status: 'running', progress: 0.1 }, { compact: true, external: true, onFinished: (j) => done.push(j.status) });
  await tick(30);
  assert.equal(FakeEventSource.instances.length, 0);
  assert.equal(calls.length, 0);
  w.update({ id: 3, kind: 'translate', status: 'running', progress: 0.5, message: 'Half way' });
  assert.match(w.el.textContent, /Half way/);
  w.update({ id: 99, kind: 'translate', status: 'failed' }); // another job: ignored
  w.update({ id: 3, kind: 'translate', status: 'succeeded', progress: 1 });
  assert.deepEqual(done, ['succeeded']);
  w.destroy();
});

test('matchActiveJobs maps listings to tracked jobs and only reports missing ones when complete', () => {
  const r = matchActiveJobs([1, 2, 3], [{ items: [{ id: 1, status: 'queued' }], total: 1 }, { items: [{ id: 2, status: 'running' }], total: 1 }]);
  assert.deepEqual([...r.updates.keys()], [1, 2]);
  assert.deepEqual(r.missing, [3]);
  const partial = matchActiveJobs([1, 3], [{ items: [{ id: 1 }], total: 250 }, { items: [], total: 0 }]);
  assert.deepEqual(partial.missing, [], 'truncated listing: absence proves nothing');
});

const project = (id, jobId) => ({
  id,
  title: `Lecture ${id}`,
  subject_name: 'BEE',
  unit_name: '',
  session_number: '',
  session_title: '',
  language: 'en-IN',
  owner: { id: 7, username: 'teacher1' },
  current_version: { id: id * 10, number: 1, status: 'generating', language: 'en-IN', has_timeline: false, timeline_stale: false, issue_counts: {} },
  active_job: { id: jobId, kind: 'generate_lecture', status: 'running', stage: 'script', progress: 0.2, project_id: id, version_id: id * 10 },
  created_at: '2026-09-01T10:00:00Z',
  updated_at: '2026-09-01T10:00:00Z',
});

test('projects page: 2 cards stream, all others share ONE page-level poll (rate-limit safe)', async (t) => {
  fresh();
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const projects = [1, 2, 3, 4, 5, 6].map((i) => project(i, 100 + i));
  let running = projects.map((p) => ({ ...p.active_job }));
  let listing429 = false;
  let projectLoads = 0;
  const calls = mockFetch({
    'GET /api/projects?limit=24&offset=0': () => {
      projectLoads += 1;
      return { items: projects, total: projects.length };
    },
    'GET /api/jobs?status=queued&limit=200': { items: [], total: 0 },
    'GET /api/jobs?status=running&limit=200': () => (listing429 ? { status: 429, body: { detail: 'slow down', code: 'rate_limited' }, headers: { 'Retry-After': '40' } } : { items: running, total: running.length }),
    'GET /api/jobs/106': { id: 106, kind: 'generate_lecture', status: 'succeeded', progress: 1, project_id: 6, version_id: 60 },
  });
  const container = document.createElement('div');
  document.body.appendChild(container);
  const handle = mountProjects(container, { app: fakeApp(), params: {}, query: {} });
  await settle();
  assert.equal(FakeEventSource.instances.length, 2, 'per-user SSE cap respected');
  assert.equal(calls.filter((c) => c.path.startsWith('/api/jobs')).length, 0, 'no per-card polling');

  // One poll interval: exactly one listing per status, whatever the number of cards.
  running = running.map((j) => (j.id === 103 ? { ...j, progress: 0.6, message: 'Writing scene 4/6' } : j));
  t.mock.timers.tick(JOB_POLL_MS);
  await settle();
  const listCalls = calls.filter((c) => c.path.startsWith('/api/jobs?'));
  assert.deepEqual(listCalls.map((c) => c.path).sort(), ['/api/jobs?status=queued&limit=200', '/api/jobs?status=running&limit=200']);
  assert.match(container.textContent, /Writing scene 4\/6/, 'the polled card was updated');

  // Job 106 left the active lists: its final state is fetched once and the list refreshes.
  running = running.filter((j) => j.id !== 106);
  t.mock.timers.tick(JOB_POLL_MS);
  await settle();
  assert.equal(calls.filter((c) => c.path === '/api/jobs/106').length, 1);
  t.mock.timers.tick(700); // refreshSoon debounce
  await settle();
  assert.equal(projectLoads, 2, 'the page reloaded after a job finished');

  // 429 on the listing: the next poll waits for Retry-After (40 s), not 8 s.
  listing429 = true;
  t.mock.timers.tick(JOB_POLL_MS);
  await settle();
  const before = calls.length;
  t.mock.timers.tick(JOB_POLL_MS * 2);
  await settle();
  assert.equal(calls.length, before, 'backing off');
  t.mock.timers.tick(40_000);
  await settle();
  assert.ok(calls.length > before, 'polls again after Retry-After');
  handle.destroy();
  const after = calls.length;
  t.mock.timers.tick(120_000);
  await settle();
  assert.equal(calls.length, after, 'no polling after destroy');
});

test('job progress: the review link can carry another label (a source pause links to the source report)', () => {
  fresh();
  const w = jobProgress({ id: 8, kind: 'generate_lecture', status: 'awaiting_review', progress: 0.08 }, { reviewHref: '#/p/1/v/2/source', reviewLabel: 'Check the source' });
  document.body.appendChild(w.el);
  assert.equal(byText(w.el, 'Review plan', 'a'), null);
  const link = byText(w.el, 'Check the source', 'a');
  assert.equal(link.hidden, false);
  assert.match(link.getAttribute('href'), /#\/p\/1\/v\/2\/source$/);
  w.destroy();
});

test('projects page: a lecture waiting for its source review links to the source report, not the plan review', async () => {
  fresh();
  const paused = project(1, 101);
  paused.current_version = { ...paused.current_version, status: 'awaiting_review', review_stage: 'source' };
  paused.active_job = { ...paused.active_job, status: 'awaiting_review', stage: 'plan', progress: 0.08 };
  const planned = project(2, 102);
  planned.current_version = { ...planned.current_version, status: 'awaiting_review', review_stage: 'plan' };
  planned.active_job = { ...planned.active_job, status: 'awaiting_review', stage: 'plan', progress: 0.18 };
  mockFetch({ 'GET /api/projects?limit=24&offset=0': { items: [paused, planned], total: 2 } });
  const container = document.createElement('div');
  document.body.appendChild(container);
  const handle = mountProjects(container, { app: fakeApp(), params: {}, query: {} });
  await settle();
  const [first, second] = [...container.querySelectorAll('.project-card')];
  const hrefs = (/** @type {Element} */ card, /** @type {string} */ label) => [...card.querySelectorAll('a')].filter((a) => a.textContent.trim() === label).map((a) => String(a.getAttribute('href')).replace(/^[^#]*/, ''));
  assert.deepEqual(hrefs(first, 'Check the source'), ['#/p/1/v/10/source', '#/p/1/v/10/source'], 'job widget and primary action');
  assert.deepEqual(hrefs(first, 'Review plan'), []);
  assert.deepEqual(hrefs(second, 'Review plan'), ['#/p/2/v/20/plan', '#/p/2/v/20/plan']);
  handle.destroy();
});
