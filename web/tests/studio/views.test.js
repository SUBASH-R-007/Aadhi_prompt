import { resetDom, mockFetch, tick, window } from './_dom.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import { mount as mountAnalytics } from '../../js/studio/views/analytics.js';
import { mount as mountAdmin } from '../../js/studio/views/admin.js';
import { mount as mountPlanReview } from '../../js/studio/views/planReview.js';
import { closeAllModals } from '../../js/studio/components/modal.js';
import { sampleMeta, sampleVersion } from './fixtures.js';
import { fakeApp, deferred, byText, clickModal, waitFor, jsonBody } from './_views.js';

function fresh() {
  closeAllModals();
  resetDom();
}

/** Chart.js stand-in installed when the (never-loading) vendor script is "loaded". */
class FakeChart {
  static instances = [];
  constructor(canvas, cfg) {
    this.canvas = canvas;
    this.cfg = cfg;
    this.destroyed = false;
    FakeChart.instances.push(this);
  }
  destroy() {
    this.destroyed = true;
  }
}

const analytics = (viewers) => ({
  summary: { viewers, sessions: viewers + 1, completion_rate: 0.5, avg_watch_seconds: 120 },
  scenes: [
    { scene_id: 's1', title: 'Intro', index: 0, enters: viewers, completes: viewers, dropoff_rate: 0 },
    { scene_id: 's2', title: 'Law', index: 1, enters: viewers, completes: 1, dropoff_rate: 0.4 },
  ],
  quizzes: [],
  flags: [],
});

test('analytics: switching versions while the chart library loads leaves exactly one live chart', async () => {
  fresh();
  FakeChart.instances = [];
  const v = sampleVersion();
  mockFetch({
    'GET /api/projects/5': { project: { id: 5, title: "Ohm's Law" }, versions: [v, { ...v, id: 10, number: 2 }], sources: [], jobs: [] },
    'GET /api/projects/5/analytics': analytics(3),
    'GET /api/projects/5/analytics?version_id=10': analytics(5),
  });
  const container = document.createElement('div');
  document.body.appendChild(container);
  const handle = await mountAnalytics(container, { app: fakeApp(), params: { id: 5 }, query: {} });
  const script = await waitFor(() => document.head.querySelector('script[src*="chartjs"]'));
  const picker = container.querySelector('select');
  picker.value = '10';
  picker.dispatchEvent(new window.Event('change'));
  await waitFor(() => /5/.test(container.querySelector('.stat-value')?.textContent || ''));
  // The library arrives after both loads asked for a chart.
  window.Chart = FakeChart;
  script.onload();
  await tick(10);
  assert.equal(FakeChart.instances.length, 2);
  const alive = FakeChart.instances.filter((c) => !c.destroyed);
  assert.equal(alive.length, 1, 'the stale chart was destroyed');
  assert.equal(alive[0].canvas.isConnected, true, 'the live chart is the one on screen');
  handle.destroy();
  assert.equal(FakeChart.instances.filter((c) => !c.destroyed).length, 0, 'destroy() releases it');
  delete window.Chart;
});

test('admin: no password reset for your own account; length between 8 and the default is left to the server', async () => {
  fresh();
  const me = { id: 7, username: 'boss', role: 'admin', must_change_password: false, is_active: true, daily_budget_usd: null, last_login_at: null };
  const other = { id: 8, username: 'teacher2', role: 'editor', must_change_password: false, is_active: true, daily_budget_usd: null, last_login_at: null };
  const calls = mockFetch({
    'GET /api/admin/users': { items: [me, other] },
    'GET /api/admin/usage?days=30': { status: 404, body: { detail: 'x', code: 'not_found' } },
    'PATCH /api/admin/users/8': { user: other },
    'POST /api/admin/users': { status: 201, body: { user: { id: 9 } } },
  });
  let meta = sampleMeta();
  const app = fakeApp({ user: () => me, meta: async () => meta });
  const container = document.createElement('div');
  document.body.appendChild(container);
  const handle = await mountAdmin(container, { app, params: {}, query: {} });
  await waitFor(() => container.querySelectorAll('tbody tr').length === 2);
  const [rowMe, rowOther] = container.querySelectorAll('tbody tr');
  const resetMe = byText(rowMe, 'Reset password', 'button');
  assert.equal(resetMe.disabled, true, 'resetting your own password would sign you out at once');
  assert.match(resetMe.getAttribute('title'), /Change password/);
  assert.equal(byText(rowOther, 'Reset password', 'button').disabled, false);

  // Reset another user's password with a 9-character password (server minimum is configurable).
  byText(rowOther, 'Reset password', 'button').click();
  await waitFor(() => document.querySelector('.modal input'));
  const pw = document.querySelector('.modal input');
  pw.value = 'Kx7-mPq2z';
  clickModal('Reset password');
  await waitFor(() => calls.some((c) => c.method === 'PATCH'));
  assert.deepEqual(jsonBody(calls.find((c) => c.method === 'PATCH')), { password: 'Kx7-mPq2z', must_change_password: true });
  await waitFor(() => document.querySelector('.modal') && /Password reset/.test(document.querySelector('.modal').textContent));
  clickModal('OK');
  await tick(10);

  // When /api/meta publishes the configured minimum, it is enforced exactly.
  meta = { ...sampleMeta(), limits: { ...sampleMeta().limits, password_min_length: 12 } };
  byText(container, 'Create user', 'button').click();
  await waitFor(() => document.querySelectorAll('.modal input').length >= 2);
  const [username, temp] = document.querySelectorAll('.modal input');
  username.value = 'teacher3';
  temp.value = 'Kx7-mPq2zab'; // 11 characters
  clickModal('Create');
  await waitFor(() => /at least 12 characters/i.test(document.querySelector('.modal-error')?.textContent || ''));
  assert.equal(calls.filter((c) => c.method === 'POST').length, 0);
  temp.value = 'Kx7-mPq2zab4';
  clickModal('Create');
  await waitFor(() => calls.some((c) => c.method === 'POST' && c.path === '/api/admin/users'));
  closeAllModals();
  handle.destroy();
});

test('plan review: a version waiting for its source review opens the source report instead', async () => {
  for (const version of [
    sampleVersion({ status: 'awaiting_review', review_stage: 'source', screenplay: null, plan: null }),
    sampleVersion({ status: 'awaiting_review', screenplay: null, plan: null, generation_meta: { review_stage: 'source' } }),
  ]) {
    fresh();
    mockFetch({ 'GET /api/versions/9': version, 'GET /api/meta': sampleMeta() });
    const app = fakeApp();
    const container = document.createElement('div');
    document.body.appendChild(container);
    const handle = await mountPlanReview(container, { app, params: { id: 5, vid: 9 }, query: {} });
    assert.equal(handle, undefined);
    assert.deepEqual(app.rec.navs, ['#/p/5/v/9/source']);
    assert.equal(app.rec.guardCalls, 0);
    assert.doesNotMatch(container.textContent, /No plan to review/);
  }
});

test('plan review: leaving while it loads installs no leave guard', async () => {
  fresh();
  const slow = deferred();
  mockFetch({ 'GET /api/versions/9': () => slow.promise, 'GET /api/meta': sampleMeta() });
  const app = fakeApp();
  const ac = new AbortController();
  const container = document.createElement('div');
  document.body.appendChild(container);
  const pending = mountPlanReview(container, { app, params: { id: 5, vid: 9 }, query: {}, signal: ac.signal });
  await tick();
  ac.abort();
  slow.resolve(sampleVersion({ status: 'awaiting_review', screenplay: null, plan: { chapters: [] } }));
  const handle = await pending;
  assert.equal(handle, undefined);
  assert.equal(app.rec.guardCalls, 0);
});
