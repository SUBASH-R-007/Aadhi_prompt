import { resetDom, mockFetch, tick, FakeEventSource } from './_dom.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import { mount } from '../../js/studio/views/projectDetail.js';
import { closeAllModals } from '../../js/studio/components/modal.js';
import { fakeApp, byText, waitFor, clickModal, jsonBody } from './_views.js';

const V9 = { id: 9, number: 1, label: '', status: 'awaiting_review', review_stage: 'source', language: 'en-IN', revision: 1, built_revision: null, timeline_stale: true, has_timeline: false, source_version_id: null, issue_counts: {}, created_at: '2026-09-01T10:00:00Z', updated_at: '2026-09-01T10:00:00Z' };
const JOB = { id: 21, kind: 'generate_lecture', status: 'awaiting_review', stage: 'plan', progress: 0.08, version_id: 9, project_id: 5, message: 'Aadhi has read the source.' };

function detail(version = V9, job = JOB) {
  const { review_stage: _ignored, ...summary } = version; // the page reads the stage from its versions rows
  return {
    project: { id: 5, title: "Ohm's Law", subject_name: '', unit_name: '', session_number: '', session_title: '', language: 'en-IN', owner: { id: 7, username: 'teacher1' }, current_version: summary, active_job: job, created_at: V9.created_at, updated_at: V9.updated_at },
    versions: [version],
    sources: [{ id: 1, filename: 'ohm.pdf', mime: 'application/pdf', size_bytes: 2048, page_count: 3, created_at: V9.created_at }],
    jobs: job ? [job] : [],
  };
}

/** The route part of a link's href (links are resolved against the page URL). */
const hash = (/** @type {Element} */ a) => String(a.getAttribute('href')).replace(/^[^#]*/, '');

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

const common = { 'GET /api/projects/5/shares': { items: [] }, 'GET /api/versions/9/renders': { items: [] }, 'GET /api/versions/9/render/preflight': { items: [] } };

test('a version waiting for its source review links to the source report, not the plan review', async () => {
  fresh();
  mockFetch({ ...common, 'GET /api/projects/5': detail() });
  const app = fakeApp();
  const { container, handle } = await mountPage(app);
  const header = container.querySelector('.page-header');
  assert.equal(hash(byText(header, 'Check the source')), '#/p/5/v/9/source');
  const progress = byText(container, 'In progress', 'h2').closest('section');
  assert.equal(hash(byText(progress, 'Check how Aadhi read your document')), '#/p/5/v/9/source');
  assert.equal(byText(progress, 'Review plan'), null);
  const versions = byText(container, 'Versions', 'h2').closest('section');
  assert.equal(hash(byText(versions, 'Check the source')), '#/p/5/v/9/source');
  handle.destroy();
});

test('a plan review keeps its plan link; every version offers its source report', async () => {
  fresh();
  const v = { ...V9, review_stage: 'plan' };
  mockFetch({ ...common, 'GET /api/projects/5': detail(v) });
  const app = fakeApp();
  const { container, handle } = await mountPage(app);
  assert.equal(hash(byText(container.querySelector('.page-header'), 'Review plan')), '#/p/5/v/9/plan');
  const versions = byText(container, 'Versions', 'h2').closest('section');
  byText(versions, 'Actions', 'button').click();
  await tick();
  byText(document.body, 'How Aadhi read the source', '[role="menuitem"]').click();
  await tick();
  assert.deepEqual(app.rec.navs, ['#/p/5/v/9/source']);
  handle.destroy();
});

test('regenerate can ask for a source review', async () => {
  fresh();
  const ready = { ...V9, status: 'ready', review_stage: undefined, has_timeline: true, built_revision: 1, timeline_stale: false };
  const calls = mockFetch({
    ...common,
    'GET /api/projects/5': detail(ready, null),
    'POST /api/projects/5/regenerate': { status: 201, body: { version: { ...ready, id: 10, number: 2, status: 'generating' }, job: { ...JOB, id: 22, status: 'queued', version_id: 10 } } },
  });
  const app = fakeApp();
  const { container, handle } = await mountPage(app);
  byText(container.querySelector('.page-header'), 'More', 'button').click();
  await tick();
  byText(document.body, 'Regenerate lecture', '[role="menuitem"]').click();
  await waitFor(() => document.querySelector('.modal'));
  byText(document.querySelector('.modal'), 'Let me check how Aadhi read my document', 'label').querySelector('input').click();
  clickModal('Regenerate');
  await waitFor(() => calls.some((c) => c.path === '/api/projects/5/regenerate'));
  const body = jsonBody(calls.find((c) => c.path === '/api/projects/5/regenerate'));
  assert.equal(body.review_source, true);
  assert.ok(body.options && typeof body.options === 'object');
  await waitFor(() => app.rec.toasts.some((t) => /reading the document/.test(t.message)));
  handle.destroy();
});
