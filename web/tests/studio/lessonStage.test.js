import './_dom.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import { STAGES, STAGE_INFO, stageInfo, knownStage, workflowSteps, deriveStage, nextStepFor, safeStepHref, projectStage } from '../../js/studio/lib/lessonStage.js';
import { uniqueJoin, distinctTitles, withSuffix, nameKey } from '../../js/studio/lib/names.js';
import { showTechnical } from '../../js/studio/lib/debug.js';
import { pageHidden, onceVisible } from '../../js/studio/lib/visibility.js';

const V = { id: 9, number: 1, label: '', status: 'ready', language: 'en-IN', revision: 4, built_revision: 4, timeline_stale: false, has_timeline: true, source_version_id: null, issue_counts: {}, created_at: '', updated_at: '' };
const job = (kind, status = 'running', stage = '', version_id = 9) => ({ id: 1, kind, status, stage, version_id, progress: 0.2 });

test('every contract stage has plain words, a tone, an icon and a summary', () => {
  assert.deepEqual(STAGES, ['reading', 'source_review', 'planning', 'plan_review', 'writing', 'ready_to_build', 'building', 'ready_to_render', 'rendering', 'video_ready', 'video_outdated', 'failed']);
  for (const s of STAGES) {
    const info = STAGE_INFO[s];
    assert.ok(info.label && info.summary && info.icon && info.tone, s);
    assert.doesNotMatch(info.label + info.summary, /_|timeline|revision|render_video|job/i, `${s} reads in plain words`);
  }
  assert.equal(knownStage('nope'), null);
  assert.deepEqual(stageInfo('brand_new_stage'), { label: 'Brand new stage', summary: '', tone: 'muted', icon: 'info', step: -1 });
});

test('workflow strip: done, current and to-do steps per stage', () => {
  const states = (stage) => workflowSteps(stage).map((s) => s.state[0]).join('');
  assert.equal(states('reading'), 'ctttt');
  assert.equal(states('plan_review'), 'dcttt');
  assert.equal(states('building'), 'dddct');
  assert.equal(states('video_outdated'), 'ddddc');
  assert.equal(states('video_ready'), 'ddddd');
  // a failure says nothing about where the lesson is: no strip (it used to mark every step 'to do')
  assert.equal(states('failed'), '');
  assert.equal(states('brand_new_stage'), '');
  // the latest video of a built lecture failed: everything before the video is done
  assert.equal(workflowSteps('failed', 'render').map((s) => s.state[0]).join(''), 'ddddc');
  assert.equal(workflowSteps('failed', 'retry').length, 0);
  assert.deepEqual(workflowSteps('writing').map((s) => s.label), ['Source', 'Plan', 'Script', 'Voice and visuals', 'Video']);
});

test('deriveStage (older servers): from the version summary and the active job', () => {
  assert.equal(deriveStage(null, null), null);
  assert.equal(deriveStage(null, job('generate_lecture', 'queued', '', null)), 'reading');
  assert.equal(deriveStage({ ...V, status: 'generating' }, job('generate_lecture', 'running', 'ingest')), 'reading');
  assert.equal(deriveStage({ ...V, status: 'generating' }, job('generate_lecture', 'running', 'plan')), 'planning');
  assert.equal(deriveStage({ ...V, status: 'generating' }, job('generate_lecture', 'running', 'script')), 'writing');
  assert.equal(deriveStage({ ...V, status: 'generating' }, job('generate_lecture', 'running', 'assets')), 'building');
  assert.equal(deriveStage({ ...V, status: 'awaiting_review', review_stage: 'source' }, job('generate_lecture', 'awaiting_review', 'plan')), 'source_review');
  assert.equal(deriveStage({ ...V, status: 'awaiting_review', review_stage: 'plan' }, null), 'plan_review');
  assert.equal(deriveStage({ ...V, status: 'awaiting_review' }, null), 'plan_review');
  assert.equal(deriveStage(V, job('build_assets')), 'building');
  assert.equal(deriveStage(V, job('render_video', 'queued')), 'rendering');
  assert.equal(deriveStage(V, job('render_video', 'running', '', 10)), 'ready_to_render', "another version's job does not count");
  assert.equal(deriveStage({ ...V, status: 'failed' }, null), 'failed');
  assert.equal(deriveStage({ ...V, timeline_stale: true, built_revision: 3 }, null), 'ready_to_build');
  assert.equal(deriveStage({ ...V, has_timeline: false }, null), 'ready_to_build');
  assert.equal(deriveStage(V, job('render_video', 'failed')), 'ready_to_render', 'a finished job is history');
  assert.equal(deriveStage(V, null), 'ready_to_render');
});

test('nextStepFor: one plain action per stage, links for review pages', () => {
  const ids = { projectId: 5, versionId: 9 };
  assert.deepEqual(nextStepFor('source_review', ids), { action: 'review_source', label: 'Check how Aadhi read your document', href: '#/p/5/v/9/source' });
  assert.deepEqual(nextStepFor('plan_review', ids), { action: 'review_plan', label: 'Review the plan', href: '#/p/5/v/9/plan' });
  assert.deepEqual(nextStepFor('ready_to_build', ids), { action: 'build', label: 'Build the lecture', href: null });
  assert.deepEqual(nextStepFor('ready_to_render', ids), { action: 'render', label: 'Make the video', href: null });
  assert.deepEqual(nextStepFor('video_outdated', ids), { action: 'render', label: 'Make the video again', href: null });
  assert.deepEqual(nextStepFor('video_ready', ids), { action: 'download', label: 'Your video is ready', href: null });
  assert.deepEqual(nextStepFor('failed', ids), { action: 'retry', label: 'Try again', href: null });
  for (const busy of ['reading', 'planning', 'writing', 'building', 'rendering']) assert.equal(nextStepFor(busy, ids).action, 'wait');
  assert.equal(nextStepFor('plan_review', { projectId: 5 }).href, null);
});

test('safeStepHref: Studio routes and same-site paths only', () => {
  assert.equal(safeStepHref('#/p/5/v/9/edit'), '#/p/5/v/9/edit');
  assert.equal(safeStepHref('/preview/9'), '/preview/9');
  for (const bad of ['javascript:alert(1)', 'https://evil.example/', '//evil.example/x', '/\\evil', '#/p/1\n', 'data:text/html,x', '', null, 5]) assert.equal(safeStepHref(bad), null, String(bad));
});

test("projectStage: the server's stage and next step win; unsafe links are dropped; older servers derive", () => {
  const server = projectStage({ id: 5, stage: 'plan_review', next_step: { action: 'review_plan', label: ' Review the plan ', href: '#/p/5/v/9/plan' }, current_version: { ...V, status: 'awaiting_review' } });
  assert.deepEqual(server, { stage: 'plan_review', next: { action: 'review_plan', label: 'Review the plan', href: '#/p/5/v/9/plan' }, derived: false });
  const unsafe = projectStage({ id: 5, stage: 'ready_to_render', next_step: { action: 'render', label: 'Make the video', href: 'https://elsewhere.example/' }, current_version: V });
  assert.equal(unsafe.next.href, null);
  const noStep = projectStage({ id: 5, stage: 'ready_to_build', next_step: null, current_version: V });
  assert.deepEqual(noStep.next, { action: 'build', label: 'Build the lecture', href: null });
  const derived = projectStage({ id: 5, current_version: { ...V, timeline_stale: true }, active_job: null });
  assert.deepEqual(derived, { stage: 'ready_to_build', next: { action: 'build', label: 'Build the lecture', href: null }, derived: true });
  assert.equal(projectStage({ id: 5, current_version: null, active_job: null }), null);
});

test('uniqueJoin drops empty and repeated name parts (case and spaces ignored)', () => {
  assert.equal(uniqueJoin(['Physics', ' physics ', '', null, 'Unit 2', 'PHYSICS']), 'Physics · Unit 2');
  assert.equal(uniqueJoin(['A  B', 'a b', 'C'], ' / '), 'A B / C');
  assert.equal(uniqueJoin([]), '');
  assert.equal(nameKey('  Ohm’s   LAW '), 'ohm’s law');
});

test('distinctTitles: a short suffix only for lectures sharing a title', () => {
  const p = (id, extra) => ({ id, title: "Ohm's law", created_at: '2026-10-0' + id + 'T10:00:00Z', ...extra });
  // a field that tells them apart
  let s = distinctTitles([p(1, { session_number: 'S1' }), p(2, { session_number: 'S2' }), { id: 3, title: 'Other' }]);
  assert.deepEqual([...s.entries()], [[1, 'S1'], [2, 'S2']]);
  // same session: the unit differs
  s = distinctTitles([p(1, { session_number: 'S1', unit_name: 'DC' }), p(2, { session_number: 'S1', unit_name: 'AC' })]);
  assert.deepEqual([...s.values()], ['DC', 'AC']);
  // nothing differs: the creation date
  s = distinctTitles([p(1), p(2)]);
  assert.deepEqual([...s.values()], ['1 Oct 2026', '2 Oct 2026']);
  // same day too: a running number by id
  s = distinctTitles([{ ...p(7), created_at: '2026-10-01T09:00:00Z' }, { ...p(3), created_at: '2026-10-01T11:00:00Z' }]);
  assert.deepEqual(Object.fromEntries(s), { 3: '(1)', 7: '(2)' });
  // the title is matched without case or spaces; one item per id
  s = distinctTitles([{ id: 1, title: 'Ohm' }, { id: 1, title: 'Ohm' }, { id: 2, title: ' OHM ' }]);
  assert.equal(s.size, 2);
  assert.equal(withSuffix('Ohm', '(2)'), 'Ohm (2)');
  assert.equal(withSuffix('Ohm', 'S1'), 'Ohm · S1');
  assert.equal(withSuffix('Ohm', undefined), 'Ohm');
});

test('showTechnical: ?debug in the page or the hash query', () => {
  assert.equal(showTechnical({ search: '', hash: '#/projects' }), false);
  assert.equal(showTechnical({ search: '?debug', hash: '#/projects' }), true);
  assert.equal(showTechnical({ search: '?debug=1', hash: '' }), true);
  assert.equal(showTechnical({ search: '', hash: '#/p/5?debug=true' }), true);
  assert.equal(showTechnical({ search: '?debug=0', hash: '#/p/5?debug=1' }), false, 'the page query decides first');
  assert.equal(showTechnical({ search: '?debug=off', hash: '' }), false);
  assert.equal(showTechnical(null), false);
});

test('visibility: pageHidden and a one-shot wait for the tab to come back', () => {
  let hidden = true;
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden });
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => (hidden ? 'hidden' : 'visible') });
  try {
    assert.equal(pageHidden(), true);
    let calls = 0;
    const cancel = onceVisible(() => calls++);
    document.dispatchEvent(new window.Event('visibilitychange'));
    assert.equal(calls, 0, 'still hidden');
    hidden = false;
    document.dispatchEvent(new window.Event('visibilitychange'));
    document.dispatchEvent(new window.Event('visibilitychange'));
    assert.equal(calls, 1, 'once');
    cancel();
    const cancelled = onceVisible(() => calls++);
    cancelled();
    document.dispatchEvent(new window.Event('visibilitychange'));
    assert.equal(calls, 1, 'a cancelled wait never fires');
    assert.equal(pageHidden(null), false);
  } finally {
    delete document.hidden;
    delete document.visibilityState;
  }
});
