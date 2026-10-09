import { resetDom, mockFetch, window, FakeEventSource } from './_dom.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import { mount as mountProjects, JOB_POLL_MS } from '../../js/studio/views/projects.js';
import { jobProgress, stageText } from '../../js/studio/components/jobProgress.js';
import { closeAllModals } from '../../js/studio/components/modal.js';
import { fakeApp } from './_views.js';

/** Let pending promise chains settle without using (possibly mocked) timers. */
async function settle(rounds = 12) {
  for (let i = 0; i < rounds; i++) await new Promise((r) => setImmediate(r));
}

function fresh(url = '/') {
  resetDom();
  closeAllModals();
  window.history.replaceState(null, '', url);
  FakeEventSource.instances = [];
}

/** Make the page look hidden / visible (restored by the returned function). */
function visibility() {
  let hidden = false;
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden });
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => (hidden ? 'hidden' : 'visible') });
  return {
    set(value) {
      hidden = value;
      if (!value) document.dispatchEvent(new window.Event('visibilitychange'));
    },
    restore() {
      delete document.hidden;
      delete document.visibilityState;
    },
  };
}

const version = (id, more = {}) => ({ id: id * 10, number: 1, label: '', status: 'ready', language: 'en-IN', revision: 2, built_revision: 2, timeline_stale: false, has_timeline: true, source_version_id: null, issue_counts: {}, created_at: '2026-09-01T10:00:00Z', updated_at: '2026-09-01T10:00:00Z', ...more });
const project = (id, more = {}) => ({
  id,
  title: `Lecture ${id}`,
  subject_name: 'BEE',
  unit_name: 'Circuits',
  session_number: `S${id}`,
  session_title: '',
  language: 'en-IN',
  owner: { id: 7, username: 'teacher1' },
  current_version: version(id),
  active_job: null,
  created_at: `2026-09-0${id}T10:00:00Z`,
  updated_at: '2026-09-01T10:00:00Z',
  ...more,
});

async function mountList(items) {
  const calls = mockFetch({ 'GET /api/projects?limit=24&offset=0': { items, total: items.length } });
  const container = document.createElement('div');
  document.body.appendChild(container);
  const handle = mountProjects(container, { app: fakeApp(), params: {}, query: {} });
  await settle();
  return { container, handle, calls };
}

test('project cards say where each lesson is and its next step', async () => {
  fresh();
  const { container, handle } = await mountList([
    project(1, { current_version: version(1, { status: 'awaiting_review', review_stage: 'plan' }), stage: 'plan_review', next_step: { action: 'review_plan', label: 'Review the plan', href: '#/p/1/v/10/plan' } }),
    project(2, { stage: 'building', next_step: { action: 'wait', label: 'Follow the progress', href: null } }),
    project(3, { stage: 'video_ready', next_step: { action: 'download', label: 'Your video is ready', href: null } }),
    project(4, { current_version: version(4, { timeline_stale: true, built_revision: 1 }) }), // older server: derived
  ]);
  const cards = [...container.querySelectorAll('.project-card')];
  const stage = (c) => c.querySelector('.card-stage');
  assert.equal(stage(cards[0]).querySelector('.stage-chip').dataset.stage, 'plan_review');
  assert.match(stage(cards[0]).textContent, /Plan ready for review\s*Next: Review the plan/);
  assert.match(stage(cards[1]).textContent, /Building voice and visuals/);
  assert.equal(stage(cards[1]).querySelector('.card-next'), null, 'nothing to do while Aadhi works');
  assert.match(stage(cards[2]).textContent, /Video ready/);
  assert.equal(stage(cards[2]).querySelector('.card-next'), null, 'the chip already says it');
  assert.match(stage(cards[3]).textContent, /Ready to build\s*Next: Build the lecture/);
  handle.destroy();
});

test('lectures sharing a title get a short suffix; name parts are shown once', async () => {
  fresh();
  const { container, handle } = await mountList([
    project(1, { title: "Ohm's law", session_number: 'S1' }),
    project(2, { title: "ohm's LAW", session_number: 'S2' }),
    project(3, { title: 'Physics', subject_name: 'Physics', unit_name: 'physics', session_number: '', session_title: 'physics' }),
  ]);
  const cards = [...container.querySelectorAll('.project-card')];
  assert.equal(cards[0].querySelector('.card-title').textContent, "Ohm's law · S1");
  assert.equal(cards[1].querySelector('.card-title').textContent, "ohm's LAW · S2");
  assert.equal(cards[0].querySelector('.card-title a').title, "Ohm's law · S1");
  assert.equal(cards[2].querySelector('.card-title').textContent, 'Physics', 'a unique title has no suffix');
  assert.equal(cards[2].querySelector('.card-meta'), null, 'subject and unit only repeat the title');
  assert.equal(cards[2].querySelector('.card-sub'), null, 'nor does the session title');
  handle.destroy();
});

test('projects page: the shared job poll pauses while the tab is hidden and polls at once on return', async (t) => {
  fresh();
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const vis = visibility();
  try {
    const items = [1, 2, 3].map((i) => project(i, { active_job: { id: 100 + i, kind: 'generate_lecture', status: 'running', stage: 'script', progress: 0.3, project_id: i, version_id: i * 10 } }));
    const calls = mockFetch({
      'GET /api/projects?limit=24&offset=0': { items, total: 3 },
      'GET /api/jobs?status=queued&limit=200': { items: [], total: 0 },
      'GET /api/jobs?status=running&limit=200': { items: items.map((p) => p.active_job), total: 3 },
    });
    const container = document.createElement('div');
    document.body.appendChild(container);
    const handle = mountProjects(container, { app: fakeApp(), params: {}, query: {} });
    await settle();
    const polls = () => calls.filter((c) => c.path.startsWith('/api/jobs?')).length;
    vis.set(true);
    t.mock.timers.tick(JOB_POLL_MS * 5);
    await settle();
    assert.equal(polls(), 0, 'no requests while hidden');
    vis.set(false);
    await settle();
    assert.equal(polls(), 2, 'one listing per status, at once');
    t.mock.timers.tick(JOB_POLL_MS);
    await settle();
    assert.equal(polls(), 4, 'then the usual interval');
    vis.set(true);
    t.mock.timers.tick(JOB_POLL_MS);
    await settle();
    handle.destroy();
    vis.set(false);
    await settle();
    assert.equal(polls(), 4, 'destroy() drops the wait for the tab');
  } finally {
    vis.restore();
  }
});

test('job progress: polling pauses while the tab is hidden; stages read in plain words', async (t) => {
  fresh();
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const vis = visibility();
  try {
    vis.set(true);
    const job = { id: 9, kind: 'build_assets', status: 'running', stage: 'assets', progress: 0.2 };
    const calls = mockFetch({
      'GET /api/jobs/9': { ...job, progress: 0.5 },
      'GET /api/jobs/9/events?after=0&limit=200': { items: [{ id: 1, created_at: '2026-10-08T10:00:00Z', level: 'info', stage: 'assets', message: 'Voicing scene 2' }] },
    });
    const w = jobProgress(job, { stream: false, pollMs: 1000 });
    document.body.appendChild(w.el);
    await settle();
    assert.equal(calls.length, 0, 'hidden: no poll');
    vis.set(false);
    await settle();
    assert.ok(calls.some((c) => c.path === '/api/jobs/9'), 'visible: polled at once');
    assert.equal(w.el.querySelector('.log-stage').textContent, 'Voice & visuals');
    assert.match(w.el.querySelector('[aria-live]').textContent, /Running, Voice & visuals/);
    vis.set(true);
    t.mock.timers.tick(5000);
    await settle();
    const n = calls.length;
    w.destroy();
    vis.set(false);
    await settle();
    assert.equal(calls.length, n, 'destroyed: the wait for the tab is dropped');
  } finally {
    vis.restore();
  }
  assert.equal(stageText('assets'), 'Voice & visuals');
  assert.equal(stageText('assets', true), 'assets');
  assert.equal(stageText('custom_step'), 'custom step');
});
