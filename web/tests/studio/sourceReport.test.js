import { resetDom, mockFetch, tick, FakeEventSource } from './_dom.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import { mount, readinessOf, effectiveStatus, draftFrom, draftBody, STATUS_LABELS } from '../../js/studio/views/sourceReport.js';
import { resolveHash } from '../../js/studio/router.js';
import { closeAllModals } from '../../js/studio/components/modal.js';
import { fakeApp, byText, clickModal, waitFor, jsonBody, typeInto } from './_views.js';

const URL = '/api/projects/5/versions/9/source-report';

function chunk(id, heading, aadhi, extra = {}) {
  return { id, heading, page: null, chars: 120, excerpt: `${heading} text`, status: aadhi, aadhi_status: aadhi, reason: '', concepts: [], visual_notes: 0, formulas: 0, code_blocks: 0, figures: 0, tables: 0, questions: 0, ...extra };
}

function report(overrides = null) {
  return {
    review_version: '1',
    file: 'ohm.pdf',
    source_kind: 'pdf',
    source_format: 'notes',
    pages: 3,
    language: 'en-IN',
    characters: 900,
    truncated: false,
    attach_original: false,
    outline: {
      title: "Ohm's Law",
      title_source: 'document',
      sections: [
        { title: 'Learning objectives', level: 2, role: 'objectives', chunk_ids: ['c0001'], chars: 100, empty: false, teaching_chunks: 1, set_aside_chunks: 0, visual_notes: 0, subtopics: [] },
        { title: 'Voltage', level: 2, role: 'teaching', chunk_ids: ['c0002'], chars: 300, empty: false, teaching_chunks: 1, set_aside_chunks: 0, visual_notes: 1, subtopics: [] },
        { title: 'Coming up next', level: 2, role: 'teaching', chunk_ids: ['c0003'], chars: 50, empty: false, teaching_chunks: 0, set_aside_chunks: 1, visual_notes: 0, subtopics: [] },
      ],
    },
    chunks: [
      chunk('c0001', 'Learning objectives', 'context'),
      chunk('c0002', 'Voltage', 'teaching', { concepts: ['voltage'], formulas: 1 }),
      chunk('c0003', 'Coming up next', 'set_aside', { reason: 'scaffolding' }),
    ],
    inventory: { formulas: [{ chunk_id: 'c0002', expression: 'V = I × R', symbols: ['V', 'I', 'R'], undefined_symbols: ['R'] }], formula_count: 1, formulas_with_undefined_symbols: 1, code_blocks: 0, code_unexplained: 0, figures: 1, figure_markers: 1, tables: 0, questions: 2, source_questions: 0 },
    findings: [
      { id: 'f1', code: 'source.formula_undefined_symbols', severity: 'warning', category: 'missing_context', message: 'Formula symbols are not explained: R', why: 'The lecture needs every symbol explained.', suggestion: 'Say what R stands for.', chunk_ids: ['c0002'], heading: 'Voltage' },
      { id: 'f2', code: 'source.near_duplicate', severity: 'info', category: 'partially_structured', message: 'Repeated content', why: 'It repeats.', suggestion: 'Keep one.', chunk_ids: ['c0002'], heading: 'Voltage' },
    ],
    findings_total: 2,
    strengths: ['Clear title.'],
    readiness: { verdict: 'missing_context', ready: false, warnings: 1, infos: 1 },
    scope: [{ category: 'person', count: 2, label: 'Names of the authors', source: 'rules' }, { category: 'duration', count: 3, label: 'Durations', source: 'rules' }],
    visual_notes: 1,
    brief_available: true,
    concepts: [{ key: 'voltage', name: 'Voltage', original_name: '', chunk_ids: ['c0002'], headings: ['Voltage'], key_facts: 1, dropped: false }],
    accounting: { chunks: 3, cited: 1, context: 1, set_aside: 1, set_aside_by_you: 0, restored: 0, skipped_by_reason: { scaffolding: 1 } },
    warnings: ['2 page(s) look scanned; their text could not be extracted.'],
    scenes: [{ scene_id: 's1', title: 'Voltage', type: 'content', chunk_ids: ['c0002'], headings: ['Voltage'], unknown_refs: 0 }, { scene_id: 's2', title: 'Wrap up', type: 'recap', chunk_ids: [], headings: [], unknown_refs: 0 }],
    overrides,
  };
}

const payload = (active, rep = report()) => ({ version: { id: 9, status: active ? 'awaiting_review' : 'ready' }, available: true, reason: '', review: { active, editable: active }, report: rep });

function fresh() {
  resetDom();
  closeAllModals();
  FakeEventSource.instances = [];
  try {
    window.localStorage.clear();
  } catch {
    /* ignore */
  }
}

async function mountPage(app) {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const handle = await mount(container, { app, params: { id: 5, vid: 9 }, query: {} });
  return { container, handle };
}

/** @param {HTMLElement} container @param {string} id */
const row = (container, id) => container.querySelector(`tr[data-chunk="${id}"]`);

test('the route resolves', () => {
  const r = resolveHash('#/p/5/v/9/source');
  assert.equal(r.route.name, 'sourceReport');
  assert.deepEqual(r.params, { id: 5, vid: 9 });
});

test('pure helpers: readiness follows done marks, statuses follow the draft, the body is in document order', () => {
  const findings = report().findings;
  assert.equal(readinessOf(findings, new Set()).verdict, 'missing_context');
  const after = readinessOf(findings, new Set(['f1']));
  assert.equal(after.ready, true);
  assert.equal(after.verdict, 'partially_structured');
  assert.equal(readinessOf(findings, new Set(['f1', 'f2'])).verdict, 'well_structured');
  const draft = draftFrom({ excluded_chunk_ids: ['c0002'], restored_chunk_ids: ['c0003'], concept_names: { voltage: 'Potential difference' } });
  assert.equal(effectiveStatus({ id: 'c0002', aadhi_status: 'teaching' }, draft), 'set_aside_by_you');
  assert.equal(effectiveStatus({ id: 'c0003', aadhi_status: 'set_aside' }, draft), 'restored');
  assert.equal(effectiveStatus({ id: 'c0001', aadhi_status: 'context' }, draft), 'context');
  draft.excluded.add('c0001');
  draft.names.blank = '   ';
  assert.deepEqual(draftBody(draft, ['c0001', 'c0002', 'c0003']), { excluded_chunk_ids: ['c0001', 'c0002'], restored_chunk_ids: ['c0003'], concept_names: { voltage: 'Potential difference' } });
  assert.equal(STATUS_LABELS.set_aside_by_you, 'Set aside by you');
});

test('read-only report: overview, findings, outline, counts only, concepts, formulas, parts and scenes', async () => {
  fresh();
  mockFetch({ [`GET ${URL}`]: payload(false) });
  const app = fakeApp();
  const { container, handle } = await mountPage(app);
  await waitFor(() => container.querySelector('[data-section="overview"]'));
  const text = container.textContent;
  assert.match(text, /How Aadhi read your document/);
  assert.match(text, /ohm\.pdf · PDF · 3 pages · English \(India\)/);
  assert.match(text, /Some things are not explained/);
  assert.match(text, /Formula symbols are not explained: R/);
  assert.match(text, /Where: “Voltage” · part c0002/);
  assert.match(text, /Suggestion: Say what R stands for\./);
  assert.match(text, /2 · Names of the authors/);
  assert.match(text, /Of 3 parts: 1 taught, 1 kept as background, 1 set aside by Aadhi \(1 video packaging\)/);
  assert.match(text, /scanned/);
  assert.match(container.querySelector('[data-section="outline"]').textContent, /Objectives/);
  assert.match(row(container, 'c0003').textContent, /Set aside by Aadhi \(video packaging\)/);
  assert.equal(row(container, 'c0003').dataset.status, 'set_aside');
  assert.match(container.querySelector('[data-section="content"]').textContent, /V = I × R.*Not explained: R/);
  assert.match(container.querySelector('[data-section="scenes"]').textContent, /1 of 2 scenes cite parts of your document/);
  assert.equal(byText(container, 'Save changes', 'button'), null, 'nothing to edit once the lecture is planned');
  assert.equal(app.rec.guard, null);
  handle.destroy();
});

test('"mark as done" is remembered in this browser and updates the verdict', async () => {
  fresh();
  mockFetch({ [`GET ${URL}`]: payload(false) });
  const app = fakeApp();
  const { container, handle } = await mountPage(app);
  await waitFor(() => container.querySelector('[data-verdict]'));
  assert.equal(container.querySelector('[data-verdict]').dataset.verdict, 'missing_context');
  byText(container.querySelector('[data-finding="source.formula_undefined_symbols"]'), 'Mark as done', 'button').click();
  await tick();
  assert.equal(container.querySelector('[data-verdict]').dataset.verdict, 'partially_structured');
  assert.deepEqual(JSON.parse(window.localStorage.getItem('aadhi.sourceReport.done.v9')), ['f1']);
  handle.destroy();
  const again = await mountPage(app);
  await waitFor(() => again.container.querySelector('[data-verdict]'));
  assert.equal(again.container.querySelector('[data-verdict]').dataset.verdict, 'partially_structured');
  again.handle.destroy();
});

test('imported lectures get an empty state', async () => {
  fresh();
  mockFetch({ [`GET ${URL}`]: { version: { id: 9 }, available: false, reason: 'no_source', review: { active: false, editable: false }, report: null } });
  const { container } = await mountPage(fakeApp());
  await waitFor(() => container.querySelector('.empty-state'));
  assert.match(container.textContent, /No source report/);
  assert.match(container.textContent, /imported lecture/);
});

test('source review: set aside, restore and rename, save, then continue', async () => {
  fresh();
  let saved = null;
  const calls = mockFetch({
    [`GET ${URL}`]: () => payload(true, report(saved)),
    'PUT /api/versions/9/source-review': ({ init }) => {
      saved = JSON.parse(init.body);
      return { version: { id: 9 }, source_overrides: saved };
    },
    'POST /api/versions/9/approve-source': { status: 202, body: { job: { id: 31, kind: 'generate_lecture', status: 'queued', stage: '', progress: 0 } } },
  });
  const app = fakeApp();
  const { container, handle } = await mountPage(app);
  await waitFor(() => byText(container, 'Save changes', 'button'));
  assert.match(container.textContent, /is waiting for you/);
  assert.equal(typeof app.rec.guard, 'function');
  assert.equal(await app.rec.guard(null), true, 'nothing changed yet');
  byText(row(container, 'c0002'), 'Set aside', 'button').click();
  await tick();
  assert.equal(row(container, 'c0002').dataset.status, 'set_aside_by_you');
  assert.match(container.querySelector('[data-concept="voltage"]').textContent, /Not planned/);
  byText(row(container, 'c0003'), 'Restore', 'button').click();
  await tick();
  assert.equal(row(container, 'c0003').dataset.status, 'restored');
  typeInto(container.querySelector('[data-concept="voltage"] input'), 'Potential difference');
  assert.equal(container.querySelector('.source-report > div').dataset.dirty, 'true');
  byText(container, 'Save changes', 'button').click();
  await waitFor(() => saved);
  assert.deepEqual(saved, { excluded_chunk_ids: ['c0002'], restored_chunk_ids: ['c0003'], concept_names: { voltage: 'Potential difference' } });
  await waitFor(() => app.rec.toasts.some((t) => t.message === 'Changes saved.'));
  assert.equal(row(container, 'c0002').dataset.status, 'set_aside_by_you', 'the saved corrections are shown');
  byText(container, 'Continue: plan the lecture', 'button').click();
  await waitFor(() => document.querySelector('.modal'));
  clickModal('Continue');
  await waitFor(() => calls.some((c) => c.path === '/api/versions/9/approve-source'));
  await waitFor(() => FakeEventSource.instances.length === 1);
  assert.equal(FakeEventSource.instances[0].url, '/api/jobs/31/stream');
  FakeEventSource.instances[0].emit('end', { id: 31, kind: 'generate_lecture', status: 'succeeded', progress: 1 });
  await waitFor(() => app.rec.navs.includes('#/p/5/v/9/edit'));
  handle.destroy();
  closeAllModals();
});

test('source review: unsaved changes are guarded and at least one part stays', async () => {
  fresh();
  const calls = mockFetch({ [`GET ${URL}`]: payload(true) });
  const app = fakeApp();
  const { container, handle } = await mountPage(app);
  await waitFor(() => byText(container, 'Save changes', 'button'));
  byText(row(container, 'c0001'), 'Set aside', 'button').click();
  await tick();
  assert.equal(app.rec.toasts.length, 0);
  // c0003 is already set aside by Aadhi: setting c0002 aside too would leave nothing to teach
  byText(row(container, 'c0002'), 'Set aside', 'button').click();
  await tick();
  assert.match(app.rec.toasts[0].message, /Keep at least one part/);
  assert.equal(row(container, 'c0002').dataset.status, 'teaching');
  assert.equal(row(container, 'c0001').dataset.status, 'set_aside_by_you', 'earlier changes are kept');
  const answer = app.rec.guard(null);
  await waitFor(() => document.querySelector('.modal'));
  assert.match(document.querySelector('.modal-title').textContent, /Discard your changes\?/);
  clickModal('Cancel');
  assert.equal(await answer, false);
  assert.equal(calls.filter((c) => c.method === 'PUT').length, 0);
  handle.destroy();
  assert.equal(app.rec.guard, null);
});

test('the PUT body keeps document order and rejects nothing silently', () => {
  const body = draftBody(draftFrom({ excluded_chunk_ids: ['c0003', 'c0001'] }), ['c0001', 'c0002', 'c0003']);
  assert.deepEqual(body.excluded_chunk_ids, ['c0001', 'c0003']);
  assert.deepEqual(jsonBody({ init: { body: JSON.stringify(body) } }), body);
});
