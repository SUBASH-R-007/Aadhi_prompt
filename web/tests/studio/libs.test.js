import './_dom.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import { localProblems, issuesFromValidationError, sortIssues, countIssues, issuesByScene, forbiddenTexMacro } from '../../js/studio/lib/validationIssues.js';
import * as P from '../../js/studio/lib/planEdit.js';
import { passwordHints, passwordScore, generateTempPassword, blockingHints, serverMinLength } from '../../js/studio/lib/password.js';
import * as T from '../../js/studio/lib/manimTemplates.js';
import { saveDraft, loadDraft, clearDraft, clearAllDrafts, noteSignedInUser, draftKey, loadPref, savePref, MAX_DRAFT_CHARS } from '../../js/studio/lib/draftStore.js';
import { stageStates, isActive, isTerminal, statusLabel } from '../../js/studio/lib/jobStages.js';
import { checkExpr } from '../../js/studio/lib/expr.js';
import { tokenizeRichLite, stripRichLite } from '../../js/studio/lib/richLite.js';
import * as U from '../../js/studio/util.js';
import * as E from '../../js/studio/lib/screenplayEdit.js';
import { sampleScreenplay, sampleMeta, sampleMetaWithTemplateSchemas } from './fixtures.js';

// --- validationIssues -------------------------------------------------------------------------

test('localProblems is empty for a valid screenplay', () => {
  assert.deepEqual(localProblems(sampleScreenplay()), []);
  assert.deepEqual(localProblems(null), []);
});

test('localProblems reports empty narration, missing payloads and bad quizzes with scene/beat ids', () => {
  let sp = sampleScreenplay();
  sp = E.updateScene(sp, 's2', (s) => {
    s.beats[1].narration = '  ';
    s.board[1].latex = '';
    s.board.push({ id: 's2-i9', kind: 'table', headers: ['a', 'b'], rows: [['1']], source_refs: [] });
    s.board.push({ id: 's2-i10', kind: 'definition', term: '', text: '', source_refs: [] });
  });
  sp = E.updateScene(sp, 's4', (s) => {
    s.options = ['2 A', '2 a', ''];
    s.question = '';
  });
  const probs = localProblems(sp);
  const codes = probs.map((p) => p.code).sort();
  assert.deepEqual(codes, ['local.beat_empty', 'local.item_definition', 'local.item_latex', 'local.item_table', 'local.quiz_option_empty', 'local.quiz_options_distinct', 'local.quiz_question']);
  const beat = probs.find((p) => p.code === 'local.beat_empty');
  assert.equal(beat.scene_id, 's2');
  assert.equal(beat.beat_id, 's2-b2');
  assert.ok(probs.every((p) => p.severity === 'error'));
});

test('localProblems validates side panels (chart lengths, graph expressions, manim spec)', () => {
  let sp = sampleScreenplay();
  sp = E.updateScene(sp, 's2', (s) => {
    s.side_panel = E.sidePanelOfKind('chart', s.side_panel, sp);
    s.side_panel.chart.datasets[0].data = [1];
  });
  sp = E.updateScene(sp, 's3', (s) => {
    s.side_panel = E.sidePanelOfKind('graph', null, sp);
    s.side_panel.graph.functions = [{ expr: 'alert(1)', label: null }];
    s.side_panel.graph.x_range = [5, 1];
  });
  sp = E.updateScene(sp, 's1', (s) => {
    s.side_panel = E.sidePanelOfKind('manim', null, sp, null);
    s.side_panel.manim = { template: null, params: {}, code: '' };
  });
  const codes = localProblems(sp).map((p) => p.code).sort();
  assert.deepEqual(codes, ['local.chart_lengths', 'local.graph_expr', 'local.graph_range', 'local.manim_spec']);
});

test('issuesFromValidationError maps pydantic locations to scenes and beats', () => {
  const sp = sampleScreenplay();
  const detail = [
    { loc: ['body', 'screenplay', 'scenes', 1, 'content', 'beats', 2, 'narration'], msg: 'String should have at least 1 character', type: 'string_too_short' },
    { loc: ['body', 'screenplay', 'scenes', 3, 'quiz_checkpoint', 'reveal_beats', 0, 'narration'], msg: 'bad', type: 'x' },
    { loc: ['body', 'screenplay', 'scenes', 2, 'example'], msg: 'Value error, item filled before it is revealed', type: 'value_error' },
    { loc: ['body', 'screenplay', 'session_title'], msg: 'too long', type: 'x' },
  ];
  const issues = issuesFromValidationError(detail, sp);
  assert.equal(issues[0].scene_id, 's2');
  assert.equal(issues[0].beat_id, 's2-b3');
  assert.equal(issues[1].scene_id, 's4');
  assert.equal(issues[1].beat_id, 's4-b2');
  assert.equal(issues[2].scene_id, 's3');
  assert.equal(issues[2].beat_id, null);
  assert.match(issues[2].message, /^scenes\.2\.example: item filled/);
  assert.equal(issues[3].scene_id, null);
  assert.equal(issuesFromValidationError('boom', sp)[0].message, 'boom');
});

test('issue sorting, counting and grouping', () => {
  const sp = sampleScreenplay();
  const issues = [
    { code: 'a.b', severity: 'info', message: 'i', scene_id: 's1' },
    { code: 'a.b', severity: 'error', message: 'e2', scene_id: 's3' },
    { code: 'a.b', severity: 'warning', message: 'w', scene_id: 's2' },
    { code: 'a.b', severity: 'error', message: 'e1', scene_id: 's2' },
  ];
  assert.deepEqual(sortIssues(issues, sp).map((i) => i.message), ['e1', 'e2', 'w', 'i']);
  assert.deepEqual(countIssues(issues), { error: 2, warning: 1, info: 1 });
  assert.deepEqual(issuesByScene(issues).get('s2'), { error: 1, warning: 1, info: 0 });
});

// --- planEdit --------------------------------------------------------------------------------------

function samplePlan() {
  return {
    subject_name: 'S',
    unit_name: 'U',
    session_number: 'Session 1',
    session_title: 'T',
    learning_objectives: [{ id: 'obj-1', text: 'State', bloom: 'remember', concept_ids: ['a'] }],
    concept_map: [
      { id: 'a', title: 'A', summary: '', depends_on: [], kind: 'core' },
      { id: 'b', title: 'B', summary: '', depends_on: ['a'], kind: 'core' },
      { id: 'c', title: 'C', summary: '', depends_on: ['b'], kind: 'core' },
    ],
    misconceptions: [{ id: 'm1', concept_id: 'b', statement: 'x', correction: 'y' }],
    glossary_terms: [],
    chapters: [
      { id: 'ch1', title: 'One', concept_ids: ['a'], scenes: [{ id: 's1', type: 'content', concept_id: 'a', objective_ids: ['obj-1'], goal: 'g', key_points: [], source_refs: [], side_panel_kind: null, visual_rationale: '', manim_template: null, misconception_ids: ['m1'], est_seconds: 45 }] },
      { id: 'ch2', title: 'Two', concept_ids: [], scenes: [{ id: 's2', type: 'simulation', concept_id: 'b', objective_ids: [], goal: 'g2', key_points: [], source_refs: [], side_panel_kind: null, visual_rationale: '', manim_template: 'equation_steps', misconception_ids: [], est_seconds: 60 }] },
    ],
    notes: '',
  };
}

test('planEdit: cycle detection and dependency options', () => {
  const plan = samplePlan();
  assert.deepEqual(P.findCycle(plan.concept_map), []);
  assert.equal(P.wouldCreateCycle(plan.concept_map, 'a', 'c'), true);
  assert.equal(P.wouldCreateCycle(plan.concept_map, 'c', 'a'), false);
  assert.equal(P.wouldCreateCycle(plan.concept_map, 'a', 'a'), true);
  assert.deepEqual(P.dependencyOptions(plan.concept_map, 'a').map((c) => c.id), []);
  assert.deepEqual(P.dependencyOptions(plan.concept_map, 'c').map((c) => c.id), ['a', 'b']);
  const cyclic = [{ id: 'x', depends_on: ['y'] }, { id: 'y', depends_on: ['x'] }, { id: 'z', depends_on: [] }];
  assert.deepEqual(P.findCycle(cyclic), ['x', 'y']);
});

test('planEdit: removing a concept clears every reference', () => {
  const out = P.removeConcept(samplePlan(), 'b');
  assert.deepEqual(out.concept_map.map((c) => c.id), ['a', 'c']);
  assert.deepEqual(out.concept_map[1].depends_on, []);
  assert.equal(out.misconceptions[0].concept_id, null);
  assert.equal(out.chapters[1].scenes[0].concept_id, null);
  assert.deepEqual(P.validatePlan(out, ['equation_steps']), []);
});

test('planEdit: objectives, misconceptions, chapters and scenes', () => {
  let plan = samplePlan();
  let r = P.addObjective(plan, 'Apply');
  assert.equal(r.id, 'obj-2');
  plan = P.removeObjective(r.plan, 'obj-1');
  assert.deepEqual(plan.chapters[0].scenes[0].objective_ids, []);
  r = P.addMisconception(plan);
  assert.equal(r.id, 'm2');
  plan = P.removeMisconception(r.plan, 'm1');
  assert.deepEqual(plan.chapters[0].scenes[0].misconception_ids, []);
  r = P.addConcept(plan, 'Ohm law');
  assert.equal(r.id, 'ohm-law');
  assert.equal(P.addConcept(r.plan, 'Ohm law').id, 'ohm-law-2');
  plan = P.addChapter(r.plan, 'Three');
  assert.equal(plan.chapters[2].id, 'three');
  const s = P.addPlannedScene(plan, 2, 'quiz_checkpoint');
  assert.equal(s.id, 's3');
  assert.equal(s.plan.chapters[2].scenes[0].est_seconds, 30);
  plan = P.moveSceneToChapter(s.plan, 2, 0, 0, 'start');
  assert.deepEqual(plan.chapters[0].scenes.map((x) => x.id), ['s3', 's1']);
  plan = P.movePlannedScene(plan, 0, 0, 1);
  assert.deepEqual(plan.chapters[0].scenes.map((x) => x.id), ['s1', 's3']);
  plan = P.removeChapter(plan, 1);
  assert.deepEqual(plan.chapters[0].scenes.map((x) => x.id), ['s1', 's3', 's2'], 'scenes move to the previous chapter');
  plan = P.removePlannedScene(plan, 0, 1);
  assert.deepEqual(P.allPlannedScenes(plan).map((x) => x.id), ['s1', 's2']);
  assert.equal(P.planTotalSeconds(plan), 105);
});

test('planEdit: updatePlannedScene clears templates that are no longer allowed; validatePlan flags problems', () => {
  let plan = P.updatePlannedScene(samplePlan(), 1, 0, { type: 'content' });
  assert.equal(plan.chapters[1].scenes[0].manim_template, null);
  plan = samplePlan();
  plan.chapters[1].scenes[0].manim_template = 'nope';
  plan.chapters[0].scenes[0].est_seconds = 2;
  plan.concept_map[0].depends_on = ['c'];
  plan.learning_objectives[0].text = '';
  const problems = P.validatePlan(plan, ['equation_steps']);
  assert.ok(problems.some((p) => /cycle/.test(p)));
  assert.ok(problems.some((p) => /unknown Manim template nope/.test(p)));
  assert.ok(problems.some((p) => /5–600/.test(p)));
  assert.ok(problems.some((p) => /Objective obj-1 needs text/.test(p)));
});

// --- password ------------------------------------------------------------------------------------

test('password hints and score', () => {
  const byKey = (/** @type {any[]} */ hints) => Object.fromEntries(hints.map((x) => [x.key, x.ok]));
  // too short and contains the username (server: min length 10, username >= 3 chars)
  assert.deepEqual(byKey(passwordHints('alice123', 'alice')), { length: false, distinct: true, username: false, common: true });
  const good = passwordHints('Blackbuck-Runs-42', 'alice');
  assert.ok(good.every((x) => x.ok));
  assert.equal(passwordScore(''), 0);
  assert.equal(passwordScore('Blackbuck-Runs-42', 'alice'), 4);
  assert.equal(passwordHints('password123', '').find((x) => x.key === 'common').ok, false);
  const temp = generateTempPassword();
  assert.equal(temp.length, 16);
  assert.ok(passwordHints(temp, 'bob').every((x) => x.ok));
  assert.notEqual(generateTempPassword(), generateTempPassword());
});

test('password hints mirror the server rules (no composition rules)', () => {
  const ok = (/** @type {string} */ pw, user = '') => passwordHints(pw, user).every((x) => x.ok);
  // NIST-style: an all-lowercase passphrase is fine (the old 3-of-4 rule wrongly blocked it).
  assert.ok(ok('correct horse battery staple'));
  assert.equal(passwordScore('correct horse battery staple'), 4);
  // base words decorated with digits/symbols are rejected like the server's _BASE_WORDS check
  assert.equal(ok('Rajalakshmi2024!'), false);
  assert.equal(ok('Password!!!!'), false);
  assert.equal(ok('1q2w3e4r5t'), false);
  // fewer than 5 distinct characters
  assert.equal(byKeyOk(passwordHints('abababababab'), 'distinct'), false);
  // usernames shorter than 3 characters are not checked (server parity)
  assert.ok(ok('my bo pass-like phrase', 'bo'));
  assert.equal(ok('teacher-alice-notes', 'alice'), false);
  // bcrypt byte limit: 30 Tamil letters are 90 UTF-8 bytes
  const tamil = 'அ'.repeat(10) + 'ஆஇஈஉஊ'.repeat(4);
  const bytes = passwordHints(tamil).find((x) => x.key === 'bytes');
  assert.ok(bytes && !bytes.ok);
  // control characters
  assert.ok(passwordHints('good phrase\u0007here').some((x) => x.key === 'control' && !x.ok));
  // minimum length honours a stricter server setting but never goes below 8
  assert.equal(byKeyOk(passwordHints('twelve-chars', '', 14), 'length'), false);
  assert.equal(byKeyOk(passwordHints('nine-char', '', 4), 'length'), true);
  // meter: unmet requirements cap the score at 2
  assert.ok(passwordScore('short') <= 2);
});

/**
 * @param {{ key: string, ok: boolean }[]} hints
 * @param {string} key
 */
function byKeyOk(hints, key) {
  const h = hints.find((x) => x.key === key);
  return h ? h.ok : undefined;
}

// --- draftStore ------------------------------------------------------------------------------------

class MemStorage {
  constructor(limit = Infinity) {
    this.map = new Map();
    this.limit = limit;
  }
  get length() {
    return this.map.size;
  }
  key(i) {
    return [...this.map.keys()][i] ?? null;
  }
  getItem(k) {
    return this.map.has(k) ? this.map.get(k) : null;
  }
  setItem(k, v) {
    if (v.length > this.limit) throw new Error('QuotaExceededError');
    this.map.set(k, v);
  }
  removeItem(k) {
    this.map.delete(k);
  }
}

test('draftStore round-trips per-user drafts and tolerates storage failures', () => {
  const st = new MemStorage();
  const sp = sampleScreenplay();
  assert.equal(draftKey(3, 7), 'aadhi.studio.draft.u3.v7');
  assert.equal(saveDraft(3, 7, { revision: 3, screenplay: sp, base: sp }, st), true);
  const d = loadDraft(3, 7, st);
  assert.equal(d.revision, 3);
  assert.deepEqual(d.screenplay, sp);
  assert.deepEqual(d.base, sp);
  assert.ok(d.savedAt);
  // Another account never sees it.
  assert.equal(loadDraft(4, 7, st), null);
  // No user, no draft.
  assert.equal(saveDraft(null, 7, { revision: 3, screenplay: sp, base: null }, st), false);
  assert.equal(loadDraft(undefined, 7, st), null);
  clearDraft(3, 7, st);
  assert.equal(loadDraft(3, 7, st), null);
  // quota: falls back to the draft without its base
  const small = new MemStorage(JSON.stringify({ revision: 3, screenplay: sp, base: null, savedAt: new Date().toISOString() }).length + 10);
  assert.equal(saveDraft(3, 8, { revision: 3, screenplay: sp, base: sp }, small), true);
  assert.equal(loadDraft(3, 8, small).base, null);
  // unusable storage
  const broken = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); }, removeItem() { throw new Error('denied'); }, key() { throw new Error('denied'); }, get length() { throw new Error('denied'); } };
  assert.equal(saveDraft(3, 9, { revision: 1, screenplay: sp, base: null }, broken), false);
  assert.equal(loadDraft(3, 9, broken), null);
  clearDraft(3, 9, broken);
  assert.equal(clearAllDrafts(broken), 0);
  assert.equal(noteSignedInUser(3, broken), 0);
  assert.equal(saveDraft(3, 9, { revision: 1, screenplay: sp, base: null }, null), false);
  // corrupt data
  st.setItem(draftKey(3, 10), '{nope');
  assert.equal(loadDraft(3, 10, st), null);
  st.setItem(draftKey(3, 11), JSON.stringify({ revision: 'x', screenplay: {} }));
  assert.equal(loadDraft(3, 11, st), null);
  // oversize drafts are skipped
  const huge = { ...sp, session_title: 'x'.repeat(MAX_DRAFT_CHARS) };
  assert.equal(saveDraft(3, 12, { revision: 1, screenplay: huge, base: null }, st), false);
  // prefs
  savePref('k', { a: 1 }, st);
  assert.deepEqual(loadPref('k', null, st), { a: 1 });
  assert.equal(loadPref('missing', 5, st), 5);
  assert.equal(loadPref('k', 5, broken), 5);
});

test('draftStore removes drafts on sign-out and when another user signs in (shared lab PCs)', () => {
  const st = new MemStorage();
  const sp = sampleScreenplay();
  const draft = { revision: 1, screenplay: sp, base: null };
  // The first user on this browser keeps nothing foreign; legacy unscoped drafts are dropped.
  st.setItem('aadhi.studio.draft.v5', JSON.stringify({ ...draft, savedAt: 'x' }));
  assert.equal(noteSignedInUser(1, st), 1);
  assert.equal(st.getItem('aadhi.studio.draft.v5'), null);
  saveDraft(1, 5, draft, st);
  saveDraft(1, 6, draft, st);
  savePref('pane', 300, st);
  // Same user again: nothing removed.
  assert.equal(noteSignedInUser(1, st), 0);
  assert.ok(loadDraft(1, 5, st));
  // A different teacher signs in on the same PC: the previous user's drafts are removed.
  saveDraft(2, 5, draft, st);
  assert.equal(noteSignedInUser(3, st), 3);
  assert.equal(loadDraft(1, 5, st), null);
  assert.equal(loadDraft(2, 5, st), null);
  assert.equal(loadPref('pane', 0, st), 300, 'preferences are not lecture content');
  // The current user's own drafts survive a switch back to them...
  saveDraft(3, 7, draft, st);
  saveDraft(4, 7, draft, st);
  assert.equal(clearAllDrafts(st, { keepUserId: 3 }), 1);
  assert.ok(loadDraft(3, 7, st));
  // ...and explicit sign-out removes everything.
  assert.equal(clearAllDrafts(st), 1);
  assert.equal(loadDraft(3, 7, st), null);
  assert.equal(noteSignedInUser(0, st), 0, 'invalid ids are ignored');
});

// --- jobStages ----------------------------------------------------------------------------------------

test('stageStates marks done/active/failed stages', () => {
  const s = stageStates({ kind: 'generate_lecture', status: 'running', stage: 'script' });
  assert.deepEqual(s.map((x) => x.state), ['done', 'done', 'active', 'pending', 'pending', 'pending', 'pending']);
  assert.equal(s[2].label, 'Write scenes');
  assert.deepEqual(stageStates({ kind: 'build_assets', status: 'succeeded', stage: 'assets' }).map((x) => x.state), ['done', 'done']);
  assert.deepEqual(stageStates({ kind: 'build_assets', status: 'failed', stage: 'timeline' }).map((x) => x.state), ['done', 'failed']);
  assert.deepEqual(stageStates({ kind: 'generate_lecture', status: 'awaiting_review', stage: 'plan' }).map((x) => x.state).slice(0, 3), ['done', 'done', 'pending']);
  // render_video reports screenshots -> segments -> mux -> store (aadhi/compose/video.py)
  const render = stageStates({ kind: 'render_video', status: 'running', stage: 'segments' });
  assert.deepEqual(render.map((x) => [x.name, x.state]), [['screenshots', 'done'], ['segments', 'active'], ['mux', 'pending'], ['store', 'pending']]);
  assert.equal(render[0].label, 'Capture frames');
  assert.deepEqual(stageStates({ kind: 'translate', status: 'running', stage: 'assets' }).map((x) => x.state), ['done', 'active', 'pending']);
  assert.deepEqual(stageStates({ kind: 'import_legacy', status: 'running', stage: 'import' }).map((x) => x.label), ['Import']);
  const custom = stageStates({ kind: 'mystery', status: 'running', stage: 'warp_drive' }, ['boot']);
  assert.deepEqual(custom.map((x) => [x.name, x.state]), [['boot', 'done'], ['warp_drive', 'active']]);
  assert.equal(custom[1].label, 'Warp drive');
  assert.equal(isActive({ status: 'queued' }), true);
  assert.equal(isActive({ status: 'failed' }), false);
  assert.equal(isTerminal({ status: 'cancelled' }), true);
  assert.equal(statusLabel('awaiting_review'), 'Awaiting review');
  assert.equal(statusLabel('weird'), 'weird');
});

// --- expr / richLite ----------------------------------------------------------------------------------

test('checkExpr mirrors the server allow-list', () => {
  for (const ok of ['x^2 + 2*x', 'sin(x)', 'exp(-x/2)', '3.5e-2*x', 'max(x, 0)', 'x**2', 'pi*e']) assert.equal(checkExpr(ok), null, ok);
  assert.match(checkExpr('alert(1)'), /Unknown name/);
  assert.match(checkExpr('x; y'), /Unsupported character/);
  assert.match(checkExpr('sin(x'), /parentheses/);
  assert.match(checkExpr(''), /Enter/);
  assert.match(checkExpr('x'.repeat(201)), /200/);
});

test('tokenizeRichLite handles markup and escapes', () => {
  assert.deepEqual(tokenizeRichLite('a **b** *c* `d` $e^2$ [[f]] \\$5 \\*x'), [
    { type: 'text', text: 'a ' },
    { type: 'bold', text: 'b' },
    { type: 'text', text: ' ' },
    { type: 'italic', text: 'c' },
    { type: 'text', text: ' ' },
    { type: 'code', text: 'd' },
    { type: 'text', text: ' ' },
    { type: 'math', text: 'e^2' },
    { type: 'text', text: ' ' },
    { type: 'keyword', text: 'f' },
    { type: 'text', text: ' $5 *x' },
  ]);
  assert.deepEqual(tokenizeRichLite('unclosed **bold and $math'), [{ type: 'text', text: 'unclosed **bold and $math' }]);
  assert.equal(stripRichLite('Voltage is **pressure** [[V]]'), 'Voltage is pressure V');
  assert.deepEqual(tokenizeRichLite('<script>alert(1)</script>'), [{ type: 'text', text: '<script>alert(1)</script>' }]);
});

// --- util ------------------------------------------------------------------------------------------------

test('util helpers', async () => {
  assert.equal(U.formatDuration(65), '1:05');
  assert.equal(U.formatDuration(3725), '1:02:05');
  assert.equal(U.formatDuration(null), '–');
  assert.equal(U.formatUsd(0.4234), '$0.42');
  assert.equal(U.formatUsd(0.0012), '$0.0012');
  assert.equal(U.formatBytes(1536), '1.5 KB');
  assert.equal(U.formatBytes(50 * 1024 * 1024), '50 MB');
  assert.equal(U.formatRelative(new Date(Date.now() - 120000).toISOString()), '2 min ago');
  assert.equal(U.formatRelative('bad'), '–');
  assert.equal(U.plural(1, 'scene'), '1 scene');
  assert.equal(U.plural(2, 'scene'), '2 scenes');
  assert.equal(Math.round(U.estimateSpeechSeconds('one two three four five')), 2);
  assert.deepEqual(U.moveIndex([1, 2, 3], 0, 2), [2, 3, 1]);
  assert.deepEqual(U.moveIndex([1, 2, 3], 5, 0), [1, 2, 3]);
  assert.equal(U.deepEqual({ a: 1, b: [1, { c: 2 }] }, { b: [1, { c: 2 }], a: 1 }), true);
  assert.equal(U.deepEqual({ a: 1, x: undefined }, { a: 1 }), true);
  assert.equal(U.deepEqual([1], { 0: 1 }), false);
  assert.equal(U.deepEqual(NaN, NaN), true);
  assert.notEqual(U.uid('a'), U.uid('a'));
  let calls = 0;
  const d = U.debounce(() => calls++, 10);
  d();
  d();
  assert.equal(d.pending(), true);
  d.flush();
  assert.equal(calls, 1);
  d();
  d.cancel();
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(calls, 1);
  const inp = document.createElement('input');
  assert.equal(U.isEditableTarget(inp), true);
  inp.type = 'checkbox';
  assert.equal(U.isEditableTarget(inp), false);
  assert.equal(U.isEditableTarget(document.createElement('textarea')), true);
  assert.equal(U.isEditableTarget(null), false);
  assert.equal(U.absoluteUrl('/watch/abc'), 'http://localhost/watch/abc');
});

test('screenplay helpers for chapters, figures and side panels', () => {
  let sp = sampleScreenplay();
  sp = E.setSceneChapter(sp, 's2', 'ch2');
  assert.deepEqual(sp.chapters[0].scene_ids, ['s1']);
  assert.deepEqual(sp.chapters[1].scene_ids, ['s2', 's3', 's4']);
  assert.equal(sp.scenes[1].chapter_id, 'ch2');
  sp = E.setSceneChapter(sp, 's2', null);
  assert.equal(sp.scenes[1].chapter_id, null);
  assert.ok(!sp.chapters.some((c) => c.scene_ids.includes('s2')));
  assert.throws(() => E.setSceneChapter(sp, 's2', 'nope'));
  const r = E.addFigure(sp, { asset_key: 'k1', caption: 'c', width: 10, height: 5 });
  assert.equal(r.figureId, 'fig-up1');
  assert.equal(r.screenplay.figures.at(-1).asset_key, 'k1');
  assert.equal(E.addFigure(r.screenplay, { asset_key: 'k2' }).figureId, 'fig-up2');
  for (const kind of E.SIDE_PANEL_KINDS) {
    const p = E.sidePanelOfKind(kind, { kind: 'skill_tree', title: 'T', rationale: 'R', show_from_beat_id: 's2-b1' }, sp, { name: 'equation_steps', example_params: { steps: ['a'] } });
    assert.equal(p.kind, kind);
    assert.equal(p.title, 'T');
    assert.equal(p.rationale, 'R');
  }
  assert.equal(E.sidePanelOfKind(null, null, sp), null);
  const same = { kind: 'chart', chart: { chart_type: 'line', labels: ['x'], datasets: [{ label: 'a', data: [1] }] } };
  assert.deepEqual(E.sidePanelOfKind('chart', same, sp).chart, same.chart, 'same kind keeps payload');
});

test('forbidden TeX macros are reported locally (mirror of the schema check)', () => {
  assert.equal(forbiddenTexMacro('V = I R'), null);
  assert.equal(forbiddenTexMacro(String.raw`\href{javascript:alert(1)}{x}`), String.raw`\href`);
  assert.equal(forbiddenTexMacro(String.raw`\newcommand{\x}{1}`), String.raw`\newcommand`);
  assert.equal(forbiddenTexMacro(String.raw`\definecolor`), null, 'only whole command names');
  assert.equal(forbiddenTexMacro(String.raw`\frac{a}{b} \cdot \text{ok}`), null);
  let sp = sampleScreenplay();
  sp = E.updateScene(sp, 's2', (s) => {
    s.board[1].latex = String.raw`\style{color:red}{x}`;
  });
  const issues = localProblems(sp);
  assert.ok(issues.some((i) => i.code === 'local.latex_forbidden' && i.scene_id === 's2' && i.message.includes(String.raw`\style`)));
});

// --- password: configurable server minimum -------------------------------------------------------

test('password length between the floor (8) and the default (10) is advisory unless the server minimum is known', () => {
  const nine = passwordHints('Kx7-mPq2z', 'alice');
  const len = nine.find((x) => x.key === 'length');
  assert.equal(len.ok, false);
  assert.equal(len.advisory, true, 'shown, but the server decides');
  assert.deepEqual(blockingHints(nine), []);
  // Below the floor it always blocks.
  assert.deepEqual(blockingHints(passwordHints('Kx7-mPq', 'alice')).map((x) => x.key), ['length']);
  // Exact (published) minimum: enforced.
  assert.deepEqual(blockingHints(passwordHints('Kx7-mPq2zab', 'alice', 12, { exactMin: true })).map((x) => x.key), ['length']);
  assert.deepEqual(blockingHints(passwordHints('Kx7-mPq2zab', 'alice', 8, { exactMin: true })), []);
  // Other rules are never advisory.
  assert.deepEqual(blockingHints(passwordHints('Password123', '')).map((x) => x.key), ['common']);
  assert.equal(serverMinLength(sampleMeta()), null, '/api/meta does not publish it today');
  assert.equal(serverMinLength({ limits: { password_min_length: 12 } }), 12);
  assert.equal(serverMinLength({ limits: { password_min_length: 4 } }), null, 'below the server floor: ignored');
});

// --- Manim templates ------------------------------------------------------------------------------

test('manim template helpers work with the documented meta (no schema) and with schema extras', () => {
  const documented = sampleMeta();
  const rich = sampleMetaWithTemplateSchemas();
  assert.deepEqual(T.templatesOf(documented).map((t) => t.name), ['equation_steps', 'function_plot']);
  assert.deepEqual(T.templatesOf({ manim_templates: [null, { name: '' }, { name: 'x' }] }).map((t) => t.name), ['x']);
  assert.deepEqual(T.templatesOf(null), []);
  const [eq] = T.templatesOf(documented);
  assert.equal(T.hasParamsSchema(eq), false);
  assert.equal(T.hasExampleParams(eq), false);
  assert.deepEqual(T.initialParams(eq), {});
  const [richEq] = T.templatesOf(rich);
  const p1 = T.initialParams(richEq);
  assert.deepEqual(p1, { steps: ['V = IR', 'I = V/R'] });
  p1.steps.push('mutated');
  assert.deepEqual(richEq.example_params.steps.length, 2, 'examples are cloned');
  // Schema without example: defaults from the schema.
  assert.deepEqual(T.initialParams({ name: 'x', params_schema: { type: 'object', properties: { n: { type: 'integer', minimum: 2 } }, required: ['n'] } }), { n: 2 });

  // New specs: example when published; else the free-form skeleton (null) when allowed; else the first template.
  assert.deepEqual(T.defaultManimTemplate(rich), { name: 'equation_steps', example_params: { steps: ['V = IR', 'I = V/R'] } });
  assert.equal(T.defaultManimTemplate(documented), null);
  const templateOnly = { ...documented, features: { ...documented.features, manim_freeform: false } };
  assert.deepEqual(T.defaultManimTemplate(templateOnly), { name: 'equation_steps', example_params: {} });
  assert.equal(T.defaultManimTemplate({ ...templateOnly, manim_templates: [] }), null);
});

test('manimSpecProblem and localProblems flag template specs without (valid) parameters', () => {
  assert.equal(T.manimSpecProblem({ template: null, params: {}, code: 'class A(AadhiScene): pass' }), null);
  assert.match(T.manimSpecProblem({ template: null, params: {}, code: '  ' }), /either a template or free-form code/);
  assert.match(T.manimSpecProblem({ template: 'a', params: {}, code: 'x' }), /either/);
  assert.match(T.manimSpecProblem({ template: 'equation_steps', params: {}, code: null }, T.templatesOf(sampleMeta())), /needs parameters for the “Equation steps” template/);
  assert.equal(T.manimSpecProblem({ template: 'equation_steps', params: { steps: [{ latex: 'x' }] }, code: null }, T.templatesOf(sampleMeta())), null);
  assert.match(T.manimSpecProblem({ template: 'equation_steps', params: [], code: null }), /not a JSON object/);
  // With a published schema the parameters are validated.
  const rich = T.templatesOf(sampleMetaWithTemplateSchemas());
  assert.match(T.manimSpecProblem({ template: 'equation_steps', params: { steps: [] }, code: null }, rich), /invalid template parameters/);
  assert.equal(T.manimSpecProblem({ template: 'equation_steps', params: { steps: ['a'] }, code: null }, rich), null);
  // An uploaded video replaces the animation: parameters are not required.
  assert.equal(T.manimSpecProblem({ template: 'equation_steps', params: {}, code: null }, rich, { requireParams: false }), null);

  let sp = sampleScreenplay();
  sp = E.changeSceneType(sp, 's3', 'simulation', { manimTemplate: { name: 'equation_steps', example_params: {} } });
  sp = E.updateScene(sp, 's2', (s) => {
    s.side_panel = E.sidePanelOfKind('manim', s.side_panel, sp, { name: 'function_plot', example_params: {} });
  });
  const probs = localProblems(sp, { manimTemplates: T.templatesOf(sampleMeta()) }).filter((p) => p.code === 'local.manim_spec');
  assert.deepEqual(probs.map((p) => p.scene_id).sort(), ['s2', 's3']);
  assert.ok(probs.every((p) => p.severity === 'error'));
  // Override media skips the parameter requirement.
  const overridden = E.updateScene(sp, 's3', (s) => {
    s.override_asset_key = 'uploads/v.mp4';
  });
  assert.deepEqual(localProblems(overridden).filter((p) => p.code === 'local.manim_spec').map((p) => p.scene_id), ['s2']);
});
