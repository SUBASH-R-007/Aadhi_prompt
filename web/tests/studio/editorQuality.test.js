import { resetDom, mockFetch, tick, window } from './_dom.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import { mount } from '../../js/studio/views/editor/editor.js';
import { createIssuesPanel } from '../../js/studio/views/editor/issuesPanel.js';
import * as Q from '../../js/studio/views/editor/quality.js';
import * as E from '../../js/studio/lib/screenplayEdit.js';
import { closeAllModals } from '../../js/studio/components/modal.js';
import { sampleScreenplay, sampleVersion } from './fixtures.js';
import { fakeApp, byText, clickModal, typeInto, waitFor } from './_views.js';

const VID = 9;
const PID = 5;

/** @param {Partial<import('../../js/studio/lib/validationIssues.js').Issue>} over */
function issue(over) {
  return { code: 'board.too_many_items', severity: 'warning', message: 'm', scene_id: 's2', beat_id: null, source: 'lint', fixable: true, ...over };
}

const CASING = issue({ code: 'terminology.casing', severity: 'info', fixable: false, message: 'The term “pressure” is capitalised differently.' });
const CASING_REPAIR = {
  code: 'terminology.casing',
  scene_id: 's2',
  message: CASING.message,
  label: 'Write it as “Pressure”',
  edits: [{ scene_id: 's2', path: ['board', 0, 'text'], before: 'Voltage is **pressure**', after: 'Voltage is **Pressure**' }],
};

// --- pure helpers -------------------------------------------------------------------------------

test('areas come from the code prefix, with plain labels', () => {
  assert.equal(Q.areaOf('terminology.casing'), 'terms');
  assert.equal(Q.areaOf('concept.naming_variant'), 'terms');
  assert.equal(Q.areaOf('concept.unused'), 'coverage');
  assert.equal(Q.areaOf('formula.symbol_conflict'), 'formulas');
  assert.equal(Q.areaOf('code.mixed_indentation'), 'code');
  assert.equal(Q.areaOf('pacing.dense_run'), 'board');
  assert.equal(Q.areaOf('flow.bridge'), 'teaching');
  assert.equal(Q.areaOf('local.beat_empty'), 'editor');
  assert.equal(Q.areaOf('something.new'), 'other');
  // skipped scenes (batch 4) count against coverage and length
  assert.equal(Q.areaOf('scene.hidden'), 'coverage');
  assert.equal(Q.areaOf('chapter.all_scenes_hidden'), 'coverage');
  assert.equal(Q.areaOf('lecture.all_scenes_hidden'), 'coverage');
  assert.equal(Q.areaLabel('terms'), 'Terms and abbreviations');
});

test('the headline says the status in words', () => {
  assert.deepEqual(Q.statusOf({ error: 0, warning: 0, info: 0 }), { level: 'good', text: 'Looks good: nothing to fix' });
  assert.deepEqual(Q.statusOf({ error: 0, warning: 0, info: 2 }), { level: 'good', text: 'Looks good · 2 notes' });
  assert.deepEqual(Q.statusOf({ error: 0, warning: 1, info: 0 }), { level: 'review', text: '1 thing to review' });
  assert.deepEqual(Q.statusOf({ error: 1, warning: 3, info: 1 }), { level: 'fix', text: '1 needs fixing, 3 to review · 1 note' });
  assert.deepEqual(Q.statusOf({ error: 2, warning: 0, info: 0 }), { level: 'fix', text: '2 need fixing' });
});

test('groups follow the area order and sort by severity, then scene', () => {
  const sp = sampleScreenplay();
  const groups = Q.groupByArea(
    [issue({ code: 'terminology.casing', severity: 'info', scene_id: 's3' }), issue({ code: 'board.reading_time', severity: 'info' }), issue({ code: 'terminology.abbreviation_conflict', scene_id: 's2' }), issue({ code: 'local.beat_empty', severity: 'error' })],
    sp,
  );
  assert.deepEqual(groups.map((g) => g.id), ['editor', 'terms', 'board']);
  assert.deepEqual(groups[1].issues.map((i) => i.code), ['terminology.abbreviation_conflict', 'terminology.casing']);
  assert.deepEqual(groups[1].counts, { error: 0, warning: 1, info: 1 });
});

test('repairs attach by code, scene and message, and apply only to an unchanged draft', () => {
  const [withRepair, other] = Q.attachRepairs([CASING, issue({})], [CASING_REPAIR, { code: 'x', scene_id: null, message: 'y', label: 'z', edits: [] }]);
  assert.equal(withRepair.repair, CASING_REPAIR);
  assert.equal(other.repair, undefined);
  const sp = sampleScreenplay();
  const next = Q.applyRepair(sp, CASING_REPAIR);
  assert.ok(next && next !== sp);
  assert.equal(E.findScene(next, 's2').board[0].text, 'Voltage is **Pressure**');
  assert.equal(E.findScene(sp, 's2').board[0].text, 'Voltage is **pressure**', 'the input is not changed');
  assert.equal(next.scenes[0], sp.scenes[0], 'untouched scenes are shared');
  assert.equal(Q.applyRepair(next, CASING_REPAIR), null, 'a changed draft is never edited');
  assert.equal(Q.applyRepair(sp, { ...CASING_REPAIR, edits: [{ ...CASING_REPAIR.edits[0], scene_id: 'nope' }] }), null);
});

test('a repair applies when the draft field only differs by outer whitespace the server strips', () => {
  const sp = sampleScreenplay();
  /** @param {string} text */
  const withText = (text) => ({
    ...sp,
    scenes: sp.scenes.map((/** @type {any} */ s) => (s.id === 's2' ? { ...s, board: s.board.map((/** @type {any} */ b, /** @type {number} */ i) => (i === 0 ? { ...b, text } : b)) } : s)),
  });
  const next = Q.applyRepair(withText(' Voltage is **pressure**\n'), CASING_REPAIR);
  assert.ok(next, 'a trailing newline from the textarea does not block the repair');
  assert.equal(E.findScene(next, 's2').board[0].text, 'Voltage is **Pressure**');
  assert.equal(Q.applyRepair(withText('Voltage is **pressure** now\n'), CASING_REPAIR), null, 'a real change still blocks it');
  assert.equal(Q.applyRepair(withText('Voltage  is **pressure**'), CASING_REPAIR), null, 'inner whitespace still counts');
});

test('the pre-export list holds errors then warnings, at most twelve', () => {
  const sp = sampleScreenplay();
  const many = Array.from({ length: 15 }, (_, n) => issue({ message: `w${n}`, scene_id: 's3' }));
  const { items, more } = Q.preExportList([issue({ severity: 'info' }), ...many, issue({ severity: 'error', message: 'broken', scene_id: null })], sp);
  assert.equal(items.length, 12);
  assert.equal(more, 4);
  assert.deepEqual(items[0], { severity: 'error', text: 'Whole lecture: broken', sceneId: null });
  assert.equal(items[1].text, 'Scene 3 (Worked example): w0');
});

test('board fit: overflow is a warning, shrunk text a note, a good fit nothing', () => {
  const over = Q.fitIssue('s2', 1, { fit: 0.62, overflow: true });
  assert.equal(over.code, 'board.overflow');
  assert.equal(over.severity, 'warning');
  assert.equal(over.source, 'local');
  assert.match(over.message, /scene 2 does not fit/);
  const small = Q.fitIssue('s2', 1, { fit: 0.66, overflow: false });
  assert.deepEqual([small.code, small.severity], ['board.small_text', 'info']);
  assert.match(small.message, /66%/);
  assert.equal(Q.fitIssue('s2', 1, { fit: 0.9, overflow: false }), null);
  assert.equal(Q.fitIssue('s2', 1, null), null);
});

test('measureBoard reads the laid-out board and ignores a hidden one', () => {
  resetDom();
  const root = document.createElement('div');
  const board = document.createElement('div');
  board.className = 'ap-board';
  board.style.setProperty('--fit', '0.7000');
  const body = document.createElement('div');
  body.className = 'ap-board-body';
  body.appendChild(document.createElement('div'));
  board.appendChild(body);
  root.appendChild(board);
  assert.equal(Q.measureBoard(root), null, 'no layout (clientHeight 0): nothing measured');
  Object.defineProperty(body, 'clientHeight', { value: 500, configurable: true });
  Object.defineProperty(body, 'scrollHeight', { value: 720, configurable: true });
  assert.deepEqual(Q.measureBoard(root), { fit: 0.7, overflow: true });
  Object.defineProperty(body, 'scrollHeight', { value: 500, configurable: true });
  assert.deepEqual(Q.measureBoard(root), { fit: 0.7, overflow: false });
  assert.equal(Q.measureBoard(null), null);
});

// --- issues panel -------------------------------------------------------------------------------

test('issues panel: headline, areas, words for severity, technical details, actions', () => {
  resetDom();
  localStorage.clear();
  const calls = { select: [], repair: [], regen: [], build: 0 };
  const panel = createIssuesPanel({
    onSelect: (i) => calls.select.push(i.code),
    onApplyRepair: (i) => calls.repair.push(i.code),
    onRegenerate: (i) => calls.regen.push(i.code),
    onBuild: () => {
      calls.build += 1;
    },
  });
  document.body.appendChild(panel.el);
  const sp = sampleScreenplay();
  const list = [
    { ...CASING, repair: CASING_REPAIR },
    issue({ code: 'example.blank_never_filled', severity: 'error', scene_id: 's3', message: 'Blank step never filled.' }),
    issue({ code: 'formula.legend_missing', severity: 'info', fixable: false, message: 'No legend.' }),
  ];
  panel.update(list, sp, { selectedSceneId: 's2', timelineStale: true });
  const summary = panel.el.querySelector('.issues-summary');
  assert.equal(summary.textContent, '1 needs fixing · 2 notes');
  assert.equal(summary.getAttribute('aria-live'), 'polite');
  const groups = [...panel.el.querySelectorAll('details.issue-group')];
  assert.deepEqual(groups.map((g) => g.dataset.area), ['terms', 'formulas', 'board']);
  assert.deepEqual(groups.map((g) => g.open), [false, false, true], 'areas with only notes start folded');
  assert.match(groups[2].textContent, /Needs fixing/);
  assert.match(groups[0].textContent, /Note/);
  assert.ok(!/terminology\.casing/.test(panel.el.textContent), 'codes stay hidden by default');

  const technical = [...panel.el.querySelectorAll('input[type="checkbox"]')].find((i) => /technical/.test(i.closest('label, .field')?.textContent || ''));
  technical.checked = true;
  technical.dispatchEvent(new window.Event('change', { bubbles: true }));
  assert.match(panel.el.textContent, /terminology\.casing/);

  byText(panel.el, 'Write it as “Pressure”').click();
  byText(panel.el, 'Regenerate scene').click();
  assert.deepEqual(calls.repair, ['terminology.casing']);
  assert.deepEqual(calls.regen, ['example.blank_never_filled']);
  assert.equal([...panel.el.querySelectorAll('[data-action="regenerate"]')].length, 1, 'only fixable errors offer a rewrite');
  const build = byText(panel.el, 'Build');
  assert.ok(build && !build.hidden);
  build.click();
  assert.equal(calls.build, 1);
  panel.el.querySelector('button.issue[data-code="example.blank_never_filled"]').click();
  assert.deepEqual(calls.select, ['example.blank_never_filled']);

  panel.update([], sp, { timelineStale: false });
  assert.equal(summary.textContent, 'Looks good: nothing to fix');
  assert.ok(byText(panel.el, 'Build').hidden);
  assert.match(panel.el.textContent, /Nothing to fix here/);
});

// --- editor integration -------------------------------------------------------------------------

/** Editors mounted by the current test. */
const live = [];

function freshTest() {
  for (const h of live.splice(0)) {
    try {
      h.destroy();
    } catch {
      /* already destroyed */
    }
  }
  resetDom();
  closeAllModals();
  localStorage.clear();
}

/**
 * @param {any} version
 * @param {Record<string, any>} [extra]
 */
function routes(version, extra = {}) {
  return mockFetch({
    [`GET /api/versions/${VID}`]: version,
    [`POST /api/versions/${VID}/lint`]: { issues: [CASING], quality: { version: 1, repairs: [CASING_REPAIR], registry: {} } },
    [`POST /api/versions/${VID}/timeline/preview`]: { status: 404, body: { detail: 'not built', code: 'not_found' } },
    [`GET /api/jobs?project_id=${PID}&limit=20`]: { items: [], total: 0 },
    ...extra,
  });
}

async function mountEditor(app, query = {}) {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const handle = await mount(container, { app, params: { id: PID, vid: VID }, query, signal: undefined });
  if (handle) live.push(handle);
  return { container, handle };
}

test('editor: a safe repair from the lecture check is one undoable edit, saved normally', async () => {
  freshTest();
  routes(sampleVersion());
  const app = fakeApp();
  const { container } = await mountEditor(app, { scene: 's2' });
  const fix = await waitFor(() => byText(container, 'Write it as “Pressure”'));
  assert.equal(container.querySelector('.editor').dataset.dirty, 'false');
  fix.click();
  await tick();
  assert.equal(container.querySelector('.editor').dataset.dirty, 'true', 'the draft changed; nothing was saved');
  assert.ok(app.rec.toasts.some((t) => /Fixed\. Undo with Ctrl\+Z/.test(t.message)));
  assert.ok(!byText(container, 'Write it as “Pressure”'), 'the fixed issue leaves the list at once');
  container.querySelector('button[aria-label="Undo"]').click();
  await tick();
  assert.equal(container.querySelector('.editor').dataset.dirty, 'false', 'undo restores the original words');
});

test('editor: safe repairs survive a save (the PUT carries none)', async () => {
  freshTest();
  const version = sampleVersion();
  let lints = 0;
  const puts = [];
  routes(version, {
    // only the first check returns repairs: a later check cannot hide a repair dropped by the save
    [`POST /api/versions/${VID}/lint`]: () => {
      lints += 1;
      return lints === 1 ? { issues: [CASING], quality: { version: 1, repairs: [CASING_REPAIR], registry: {} } } : { issues: [CASING] };
    },
    [`PUT /api/versions/${VID}/screenplay`]: ({ init }) => {
      puts.push(JSON.parse(init.body));
      return { version: { ...version, screenplay: undefined, revision: version.revision + 1 }, issues: [CASING], stale_scenes: [] };
    },
  });
  const app = fakeApp();
  const { container } = await mountEditor(app, { scene: 's2' });
  await waitFor(() => byText(container, 'Write it as “Pressure”'));
  typeInto(container.querySelector('[data-fk="scene:title"]'), 'Pressure and voltage');
  await tick(300); // past the field debounce, well before the 900 ms lint debounce
  byText(container, 'Save').click();
  await waitFor(() => puts.length === 1 && container.querySelector('.editor').dataset.dirty === 'false');
  assert.equal(lints, 1, 'no check ran in between');
  assert.ok(byText(container, 'Write it as “Pressure”'), 'the repair of the last check is still offered after the save');
});

test('editor: adopting the server copy ("Reload theirs") checks it again, so its repairs come back', async () => {
  freshTest();
  const version = sampleVersion();
  let lints = 0;
  routes(version, {
    [`GET /api/versions/${VID}`]: () => ({ ...version, revision: version.revision + 1, issues: [{ ...CASING }] }),
    [`POST /api/versions/${VID}/lint`]: () => {
      lints += 1;
      return { issues: [CASING], quality: { version: 1, repairs: [CASING_REPAIR], registry: {} } };
    },
    [`PUT /api/versions/${VID}/screenplay`]: { status: 409, body: { detail: 'Revision conflict', code: 'revision_conflict', current_revision: version.revision + 1 } },
  });
  const app = fakeApp();
  const { container } = await mountEditor(app, { scene: 's2' });
  await waitFor(() => byText(container, 'Write it as “Pressure”'));
  typeInto(container.querySelector('[data-fk="scene:title"]'), 'Pressure and voltage');
  const edited = lints;
  await waitFor(() => lints > edited, 3000); // the edit's own check has run: nothing else is pending
  byText(container, 'Save').click();
  await waitFor(() => document.querySelector('.modal'));
  const before = lints;
  clickModal('Reload theirs');
  await waitFor(() => !byText(container, 'Write it as “Pressure”'));  // stored issues carry no repairs
  // the check that follows the adoption brings them back
  await waitFor(() => lints > before && byText(container, 'Write it as “Pressure”'), 4000);
  assert.equal(container.querySelector('.editor').dataset.dirty, 'false');
});

test('editor: a repair whose words changed meanwhile is not applied', async () => {
  freshTest();
  const sp = sampleScreenplay();
  sp.scenes[1].board[0].text = 'Voltage is **push**';
  routes(sampleVersion({ screenplay: sp }));
  const app = fakeApp();
  const { container } = await mountEditor(app, { scene: 's2' });
  (await waitFor(() => byText(container, 'Write it as “Pressure”'))).click();
  await tick();
  assert.equal(container.querySelector('.editor').dataset.dirty, 'false');
  assert.ok(app.rec.toasts.some((t) => t.kind === 'warning' && /changed since the check/.test(t.message)));
});

test('render dialog: the quality check lists errors and warnings under its own heading and never blocks', async () => {
  freshTest();
  const version = sampleVersion({
    has_timeline: true,
    issues: [issue({ code: 'formula.symbol_conflict', message: 'The symbol “V” stands for different things.' }), issue({ code: 'beat.too_short', severity: 'info', message: 'short' })],
  });
  const renders = [];
  const serverQuality = { scene_index: 1, scene_id: 's2', title: 'The law', reason: 'quality', blocking: false, message: 'server copy', severity: 'warning', code: 'x.y' };
  routes(version, {
    [`POST /api/versions/${VID}/lint`]: { issues: version.issues, quality: { version: 1, repairs: [], registry: {} } },
    [`GET /api/versions/${VID}/render/preflight`]: { blocking: false, items: [serverQuality] },
    [`POST /api/versions/${VID}/render`]: ({ init }) => {
      renders.push(JSON.parse(init.body));
      return { status: 429, body: { detail: 'busy', code: 'render_busy' }, headers: { 'retry-after': '60' } };
    },
  });
  const { container } = await mountEditor(fakeApp());
  await tick(20);
  byText(container, 'Render MP4').click();
  const notice = await waitFor(() => document.querySelector('.modal .render-quality'));
  assert.match(notice.textContent, /quality check found things to review/);
  assert.match(notice.textContent, /Please check: Scene 2 \(The law\): The symbol “V” stands for different things\./);
  assert.ok(!/short/.test(notice.textContent), 'notes are not listed before a render');
  assert.equal(document.querySelector('.modal .render-preflight'), null, 'server quality items are not shown as video differences');
  clickModal('Render');
  await waitFor(() => renders.length === 1);
  assert.equal(renders[0].allow_degraded, false);
});

test('editor: "Regenerate scene" on a fixable error opens the usual dialog with the problems filled in', async () => {
  freshTest();
  const broken = issue({ code: 'example.blank_never_filled', severity: 'error', scene_id: 's3', message: 'Blank step s3-i2 is never filled.' });
  const calls = routes(sampleVersion(), { [`POST /api/versions/${VID}/lint`]: { issues: [broken], quality: { version: 1, repairs: [], registry: {} } } });
  const { container } = await mountEditor(fakeApp());
  (await waitFor(() => byText(container, 'Regenerate scene', '.issues-panel button'))).click();
  const box = await waitFor(() => document.querySelector('.modal textarea'));
  assert.equal(box.value, 'Fix these problems:\n- Blank step s3-i2 is never filled.');
  assert.match(document.querySelector('.modal').textContent, /counts toward your budget/);
  clickModal('Cancel');
  await tick(10);
  assert.equal(calls.filter((c) => /regenerate/.test(c.path)).length, 0, 'nothing is rewritten without a click on Regenerate');
});

// --- a scene skipped in the video: its findings are notes, nothing builds or rewrites it ----------

test('issues panel: a skipped scene offers no Regenerate, Rebuild or Generate again (Review visual stays)', () => {
  resetDom();
  localStorage.clear();
  const calls = { regen: 0, rebuild: 0, again: 0 };
  const panel = createIssuesPanel({
    onSelect: () => {},
    onRegenerate: () => {
      calls.regen += 1;
    },
    onRebuildScene: () => {
      calls.rebuild += 1;
    },
    onGenerateAgain: () => {
      calls.again += 1;
    },
    reviewHref: (id) => `#/v/9/review?scene=${id}`,
  });
  document.body.appendChild(panel.el);
  const list = (sceneId) => [
    issue({ code: 'assets.tts_failed', severity: 'error', source: 'assets', fixable: false, scene_id: sceneId, message: 'The narration failed.' }),
    issue({ code: 'assets.media_degraded', severity: 'warning', source: 'assets', fixable: false, scene_id: sceneId, message: 'The picture provider failed.' }),
    issue({ code: 'video.ambiguous_submission', severity: 'warning', source: 'assets', fixable: false, scene_id: sceneId, message: 'May already be paid.' }),
    issue({ code: 'critic.factual', severity: 'error', source: 'critic', fixable: true, scene_id: sceneId, message: 'A wrong fact.' }),
  ];
  const actions = () => [...panel.el.querySelectorAll('[data-action]')].map((b) => b.dataset.action);
  const shown = sampleScreenplay();
  panel.update(list('s3'), shown, {});
  assert.deepEqual(new Set(actions()), new Set(['rebuild', 'generate-again', 'regenerate', 'review-visual']), 'a scene that plays: as before');
  const skipped = E.updateScene(shown, 's3', (s) => {
    s.hidden = true;
  });
  panel.update(list('s3'), skipped, {});
  assert.deepEqual(new Set(actions()), new Set(['review-visual']), 'skipped in the video: nothing that builds or rewrites it');
  assert.deepEqual(calls, { regen: 0, rebuild: 0, again: 0 });
});

test('editor: the findings of a scene skipped in the video (an unsaved skip too) are notes: no rebuild, no paid rewrite', async () => {
  freshTest();
  const broken = issue({ code: 'example.blank_never_filled', severity: 'error', scene_id: 's3', message: 'Blank step s3-i2 is never filled.' });
  const media = issue({ code: 'assets.tts_failed', severity: 'error', source: 'assets', fixable: false, scene_id: 's2', message: 'The narration failed.' });
  const hiddenS2 = E.updateScene(sampleScreenplay(), 's2', (s) => {
    s.hidden = true;
  });
  // an older server serves the stored severity of a skipped scene's issue
  routes(sampleVersion({ screenplay: hiddenS2, issues: [media] }), { [`POST /api/versions/${VID}/lint`]: { issues: [broken], quality: { version: 1, repairs: [], registry: {} } } });
  const { container } = await mountEditor(fakeApp(), { scene: 's3' });
  const panel = container.querySelector('.issues-panel');
  await waitFor(() => panel.querySelector('button.issue[data-code="example.blank_never_filled"]'));
  const row = (code) => panel.querySelector(`button.issue[data-code="${code}"]`);
  assert.ok(row('assets.tts_failed').classList.contains('issue-info'), "the skipped scene's stored error is a note");
  assert.equal(panel.querySelector('[data-action="rebuild"]'), null);
  assert.ok(row('example.blank_never_filled').classList.contains('issue-error'), 'a scene that plays keeps its error');
  assert.ok(panel.querySelector('[data-action="regenerate"]'));
  // skip scene 3 (not saved yet): with the next check its error is a note, and no paid rewrite is offered
  container.querySelector('[data-fk="scene:hidden"]').click();
  await waitFor(() => row('example.blank_never_filled').classList.contains('issue-info'));
  assert.equal(panel.querySelector('[data-action="regenerate"]'), null);
  assert.doesNotMatch(panel.querySelector('.issues-summary').textContent, /needs fixing/);
});
