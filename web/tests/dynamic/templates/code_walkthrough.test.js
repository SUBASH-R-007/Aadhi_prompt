// @ts-check
// templates/code_walkthrough.js (B2 step 5; DYNAMIC_VIDEO_DESIGN §4.3 code_walkthrough, deviation 13, B2 plan §1.4
// template contract, §1.5 cues, §1.7 target grammar, §1.11 slots, §P): the five fixture timelines
// (tests/compose/dynamic/make_code_walkthrough_fixtures.py) through the harness's contract and order gates, the pure
// helpers (lines as Python splits them, the data rule, the lexer, entity runs, the editor's colours, geometry, slots),
// the painted behaviour (the editor's rise, the lines one by one, the terminal on its output event), the cue geometry
// (the editor, a line's bar, cueMode), preflight's entries, the board_generic fallback and the fit on a fake text
// layout (jsdom has none). The data rule's parity with templates.py is tests/compose/dynamic/
// test_code_walkthrough_fixtures.py (it runs these functions under node).
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { installDom, uninstallDom } from '../../player/_dom.js';
import { CATALOGUE } from '../../../js/dynamic/catalogue.gen.js';
import { ZONES, makeRoles, templateSlots } from '../../../js/dynamic/core.js';
import { FOCUS_OUTSET, FOCUS_PX } from '../../../js/dynamic/cues.js';
import { itemTimings } from '../../../js/dynamic/state.js';
import { PANEL_KINDS, PANEL_TERM, PANEL_TITLED_KINDS } from '../../../js/dynamic/templates/_shared.js';
import codeWalkthrough, {
  CODE_LH, CODE_SURFACE_TOKEN, FRAME_ENTER, FRAME_RISE, GUTTER_EM, HEADING_LH, LAYOUT, LEXERS, LINE_FADE, LINE_STEP,
  MONO, TAB, barBand, boxes, charCount, codeBox, codeFits, codeItems, codeLines, codeSegments, drawsScene, editorLook,
  entityRuns, gutterAt, hasIndic, headingReserve, lexerFor, lineStart, outputAt, panelFor, pitchAt, slotRows, texOf,
  textWidth,
} from '../../../js/dynamic/templates/code_walkthrough.js';
import boardGeneric from '../../../js/dynamic/templates/board_generic.js';
import { contractProblems, orderProblems, recordingTex, renderStage } from './_harness.js';

before(() => installDom());
after(() => uninstallDom());

const FIXTURES = ['code_walkthrough.landscape.json', 'code_walkthrough.landscape.light.json',
  'code_walkthrough.landscape.long.json', 'code_walkthrough.portrait.json', 'code_walkthrough.portrait.hindi.json'];
const DIR = new URL('../../../../tests/compose/dynamic/fixtures/templates/', import.meta.url);
/** @param {string} name @returns {any} */
const load = (name) => JSON.parse(readFileSync(new URL(name, DIR), 'utf-8'));
const SEAM = { templates: { code_walkthrough: codeWalkthrough } };
const BACKSLASH = String.fromCharCode(92);

/**
 * A render stage of `tl` with code_walkthrough through the stage's test seam, and the context each scene was built
 * with.
 * @param {any} tl @param {{reducedMotion?: boolean, mode?: 'live' | 'render'}} [opts]
 */
function spyStage(tl, opts = {}) {
  /** @type {Map<number, any>} */
  const contexts = new Map();
  const wrapped = { ...codeWalkthrough, build(/** @type {any} */ ctx) { contexts.set(ctx.index, ctx); return codeWalkthrough.build(ctx); } };
  const run = renderStage(tl, { templates: { code_walkthrough: /** @type {any} */ (wrapped) }, renderTex: recordingTex().renderTex, ...opts });
  /** Seek scene `i` to the frame nearest scene time `t`; returns that frame's exact scene time. @param {number} i @param {number} t */
  const seekTo = async (i, t) => {
    const scene = tl.scenes[i];
    const f = Math.round((Number(scene.start) + t) * tl.fps);
    await run.stage.seek(f);
    return f / tl.fps - Number(scene.start);
  };
  return { ...run, contexts, seekTo, ctx: (/** @type {number} */ i) => contexts.get(i) };
}

/** The scene-relative entrance of item `id` (state.js itemTimings). */
const enterOf = (/** @type {any} */ scene, /** @type {string} */ id) => /** @type {number} */ (itemTimings(scene).find((x) => x.id === id)?.enter);
/** An opacity as a number ('' is 1: none painted). @param {string} v */
const op = (v) => (v === '' ? 1 : Number(v));
/** @param {string} hex */
const rgb = (hex) => `rgb(${[1, 3, 5].map((i) => Number.parseInt(hex.slice(i, i + 2), 16)).join(', ')})`;

// --- the fixtures through the harness -------------------------------------------------------------------------------

test('every fixture keeps the template contract (harness: dim groups, no banned writes, whole-px focus, TeX, entries)', async () => {
  for (const name of FIXTURES) assert.deepEqual(await contractProblems(codeWalkthrough, load(name)), [], name);
});

test('every fixture paints the same frame forward, in reverse and on a fresh page (a frame is a pure function of t)', async () => {
  for (const name of FIXTURES) {
    const tl = load(name);
    const total = Math.round(tl.total_duration * tl.fps);
    const frames = new Set();
    for (let f = 0; f < total; f += 12) frames.add(f);
    for (const s of tl.scenes) {
      for (const e of s.events || []) for (const d of [0, 0.2, 0.5]) frames.add(Math.min(total - 1, Math.round((s.start + e.t + d) * tl.fps)));
      frames.add(Math.min(total - 1, Math.round(s.start * tl.fps) + 3));
    }
    assert.deepEqual(await orderProblems(tl, [...frames].sort((a, b) => a - b), SEAM), [], name);
  }
});

test('the fixtures cover both formats, dark and light, both entrances, the events it draws and every edge', () => {
  const seen = { formats: new Set(), modes: new Set(), entrances: new Set(), kinds: new Set(), cases: new Set() };
  for (const name of FIXTURES) {
    const tl = load(name);
    const fmt = tl.presentation.format;
    seen.formats.add(fmt);
    seen.modes.add(tl.presentation.mode);
    seen.entrances.add(tl.presentation.text_entrance);
    for (const s of tl.scenes) {
      assert.equal(s.look.template, 'code_walkthrough', name);
      assert.equal(s.look.background.frozen_at, 0, 'dense: the background is frozen from the scene start (R-55)');
      assert.ok(/^#[0-9A-F]{6}$/.test(s.look.tokens[CODE_SURFACE_TOKEN]), `${name}: the editor's surface is stored`);
      assert.equal(drawsScene(s, fmt), true, `${name} ${s.scene_id}: drawn by the template, never handed on`);
      for (const e of s.events || []) seen.kinds.add(e.kind);
      const { code, heading } = codeItems(s);
      const lines = codeLines(String(code?.code || ''));
      const box = codeBox(fmt, ZONES[fmt]);
      if (heading) seen.cases.add('heading');
      else seen.cases.add('no-heading');
      if (s.side_panel) seen.cases.add('terminal');
      if (hasIndic(String(code?.code || ''))) seen.cases.add('indic-code');
      if (lines.some((l) => charCount(l) === box.chars)) seen.cases.add('widest-line');
      if (!heading && lines.length * box.pitch > box.linesPx - box.pitch) seen.cases.add('most-lines');
      if (String(code?.code || '').includes('\t')) seen.cases.add('tab');
      if (tl.presentation.entity_colors) seen.cases.add('entities');
    }
  }
  assert.deepEqual([...seen.formats].sort(), ['landscape', 'portrait']);
  assert.deepEqual([...seen.modes].sort(), ['dark', 'light']);
  assert.deepEqual([...seen.entrances].sort(), ['baseline_reveal', 'fade_rise']);
  assert.deepEqual([...seen.kinds].sort(), ['look_here', 'output', 'reveal']);
  assert.deepEqual([...seen.cases].sort(), ['entities', 'heading', 'indic-code', 'most-lines', 'no-heading', 'tab', 'terminal', 'widest-line']);
});

// --- lines, the data rule and the geometry (pure) ----------------------------------------------------------------

test('codeLines: Python\'s splitlines and expandtabs (the full parity run is the Python test\'s, under node)', () => {
  assert.deepEqual(codeLines(''), []);
  assert.deepEqual(codeLines('a'), ['a']);
  assert.deepEqual(codeLines('a\n'), ['a'], 'a final break closes the line');
  assert.deepEqual(codeLines('a\n\n'), ['a', '']);
  assert.deepEqual(codeLines('\n'), ['']);
  assert.deepEqual(codeLines('a\r\nb\rc'), ['a', 'b', 'c']);
  assert.deepEqual(codeLines('x\r\n\r\ny'), ['x', '', 'y']);
  assert.deepEqual(codeLines('\tx'), ['    x'], `TAB ${TAB}`);
  assert.deepEqual(codeLines('ab\tc'), ['ab  c'], 'to the next tab stop');
  assert.deepEqual(codeLines('abcd\te'), ['abcd    e']);
  assert.deepEqual(codeLines('a\t\tb'), ['a       b']);
  assert.equal(charCount('தமிழ்'), 5, 'code points, as Python\'s len');
  assert.equal(hasIndic('x = 1'), false);
  assert.equal(hasIndic('# धारा'), true);
});

test('the data rule: deviation 13\'s limits, from the editor\'s geometry', () => {
  const l = codeBox('landscape', ZONES.landscape);
  const p = codeBox('portrait', ZONES.portrait);
  assert.deepEqual(l, { linesPx: 740, headedPx: 596, pitch: 43, pitchIndic: 52, chars: 60, charsPanel: 38 });
  assert.deepEqual(p, { linesPx: 484, headedPx: 388, pitch: 48, pitchIndic: 58, chars: 28, charsPanel: null });
  /** @param {number} n @param {string} [line] */
  const prog = (n, line = 'x = 1') => Array.from({ length: n }, () => line).join('\n');
  const indicLine = '# கூட்டு';
  /** the most lines that fit, per format, headed, indic */
  const most = (/** @type {'landscape' | 'portrait'} */ fmt, /** @type {boolean} */ headed, /** @type {boolean} */ indic) => {
    let n = 0;
    while (codeFits(prog(n + 1, indic ? indicLine : 'x = 1'), fmt, ZONES[fmt], { headed, panel: false })) n += 1;
    return n;
  };
  assert.equal(most('landscape', false, false), 17, 'deviation 13: <= 17 lines at 36 px');
  assert.equal(most('landscape', true, false), 13, 'below a heading\'s reserved line');
  assert.equal(most('landscape', false, true), 14, 'the Indic line box (plan §2: 1.45)');
  assert.equal(most('landscape', true, true), 11);
  assert.equal(most('portrait', false, false), 10, 'deviation 13: <= 10 lines in 9:16');
  assert.equal(most('portrait', true, false), 8);
  assert.equal(most('portrait', false, true), 8);
  assert.equal(most('portrait', true, true), 6);
  const opts = { headed: false, panel: false };
  assert.equal(codeFits('x'.repeat(60), 'landscape', ZONES.landscape, opts), true);
  assert.equal(codeFits('x'.repeat(61), 'landscape', ZONES.landscape, opts), false);
  assert.equal(codeFits('x'.repeat(38), 'landscape', ZONES.landscape, { headed: false, panel: true }), true, 'beside a terminal');
  assert.equal(codeFits('x'.repeat(39), 'landscape', ZONES.landscape, { headed: false, panel: true }), false);
  assert.equal(codeFits('x'.repeat(28), 'portrait', ZONES.portrait, opts), true);
  assert.equal(codeFits('x'.repeat(29), 'portrait', ZONES.portrait, opts), false);
  assert.equal(codeFits('x', 'portrait', ZONES.portrait, { headed: false, panel: true }), false, 'no panel layout in 9:16');
  assert.equal(codeFits(`${'x'.repeat(21)}\t`, 'portrait', ZONES.portrait, opts), true, 'a tab from column 21 to 24');
  assert.equal(codeFits(`${'x'.repeat(24)}\t`, 'portrait', ZONES.portrait, opts), true, 'a tab from column 24 to 28');
  assert.equal(codeFits(`${'x'.repeat(24)}\ty`, 'portrait', ZONES.portrait, opts), false, 'then one more: 29');
  assert.equal(codeFits('', 'landscape', ZONES.landscape, opts), false, 'an empty program');
});

test('the geometry: the zones the line boxes live in, and the widest Latin line fits its column at the floor', () => {
  const l = boxes('landscape', ZONES.landscape, false, false);
  assert.deepEqual([l.codeTop, l.linesTop, l.linesBottom], [112, 136, 876], 'the title zone\'s top to the body\'s bottom');
  assert.deepEqual(l.area, { x0: 96, y0: 112, x1: 1824, y1: 900 });
  assert.equal(l.visual, null);
  assert.equal(l.minWidth, 1036, 'the centre 60%');
  const lh = boxes('landscape', ZONES.landscape, true, true);
  assert.equal(headingReserve('landscape'), Math.ceil(96 * HEADING_LH.indic));
  assert.equal(lh.codeTop, 112 + 120 + LAYOUT.landscape.headingGap);
  assert.deepEqual(lh.area, { x0: 96, y0: 256, x1: 1034, y1: 900 }, 'the left 55% beside a panel');
  assert.deepEqual(lh.visual, { x0: 1058, y0: 256, x1: 1824, y1: 900 }, 'the right 45%');
  const p = boxes('portrait', ZONES.portrait, false, false);
  assert.deepEqual([p.area.x0, p.area.x1, p.linesBottom], [192, 888, 840], 'the upper critical box (R-59)');
  // a mono glyph is 0.6 em: the widest line each format allows fits its code column at the minimum size
  assert.ok(60 * 0.6 * 36 <= textWidth('landscape', l, 36));
  assert.ok(38 * 0.6 * 36 <= textWidth('landscape', lh, 36), `${38 * 0.6 * 36} <= ${textWidth('landscape', lh, 36)}`);
  assert.ok(28 * 0.6 * 40 <= textWidth('portrait', p, 40));
  assert.equal(gutterAt(36), Math.ceil(GUTTER_EM * 36));
  assert.equal(pitchAt(36, false), 43);
  assert.equal(pitchAt(40, true), 58);
  // the gap leaves a look_here bar's stroke clear of the line number
  assert.ok(LAYOUT.landscape.gap - (FOCUS_OUTSET + FOCUS_PX) >= 12);
  assert.ok(LAYOUT.portrait.gap - (FOCUS_OUTSET + FOCUS_PX) >= 12);
});

test('the line heights are the type scale\'s (heading, Indic body) and the bar band sits inside the line box', () => {
  const scale = /** @type {any} */ (CATALOGUE.type).scale;
  assert.equal(HEADING_LH.latin, Number(scale.heading.line_height.latin[0]));
  assert.equal(HEADING_LH.indic, Number(scale.heading.line_height.indic[0]));
  assert.equal(CODE_LH.indic, Number(scale.body.line_height.indic[0]));
  assert.ok(CODE_LH.latin < Number(scale.body.line_height.latin[0]), 'the editor\'s own (deviation 13 needs 17 lines)');
  for (const [pitch, size] of [[43, 36], [52, 36], [48, 40], [58, 40], [58, 48]]) {
    const b = barBand(pitch, size);
    assert.ok(b.top > 0 && b.top + b.height < pitch, `${size}/${pitch}`);
    assert.ok(Math.abs(b.height - (MONO.cap + MONO.below) * size) < 1e-9);
  }
});

test('codeItems / drawsScene: one code item, at most one heading, a terminal in 16:9 only, a program that fits', () => {
  const scene = (/** @type {any} */ board, /** @type {any} */ extra = {}) => /** @type {any} */ ({ board, ...extra });
  const code = { id: 'c1', kind: 'code', language: 'python', code: 'x = 1' };
  const heading = { id: 'h1', kind: 'heading', text: 'T' };
  assert.equal(drawsScene(scene([code])), true);
  assert.equal(drawsScene(scene([heading, code])), true);
  assert.equal(drawsScene(scene([code]), 'portrait'), true);
  assert.equal(drawsScene(scene([])), false);
  assert.equal(drawsScene(scene([heading])), false, 'no code');
  assert.equal(drawsScene(scene([code, { ...code, id: 'c2' }])), false, 'two programs');
  assert.equal(drawsScene(scene([heading, { ...heading, id: 'h2' }, code])), false, 'two headings');
  assert.equal(drawsScene(scene([code, { id: 'b', kind: 'bullet', text: 'x' }])), false, 'another item');
  assert.equal(drawsScene(scene([{ ...code, code: '' }])), false, 'an empty program');
  assert.equal(drawsScene(scene([code], { quiz: {} })), false);
  assert.equal(drawsScene(scene([code], { media: {} })), false);
  const long = { ...code, code: Array.from({ length: 18 }, () => 'x').join('\n') };
  assert.equal(drawsScene(scene([long])), false, '18 lines');
  assert.equal(drawsScene(scene([{ ...code, code: 'x'.repeat(29) }]), 'portrait'), false, '29 characters in 9:16');
  // a side panel: a terminal in 16:9 only (the one question `_fit_code_walkthrough` must ask too: the CG-2 gate)
  const sp = (/** @type {any} */ panel, /** @type {any} */ media = null) => ({ side_panel: { panel, media, show_at: 0 } });
  const term = { kind: 'terminal', title: 'Run', terminal: { command: 'python a.py', output: '1' } };
  assert.equal(drawsScene(scene([code], sp(term)), 'landscape'), true);
  assert.equal(drawsScene(scene([code], sp(term)), 'portrait'), false, 'no panel in 9:16');
  assert.equal(drawsScene(scene([{ ...code, code: 'x'.repeat(39) }], sp(term)), 'landscape'), false, '39 characters beside it');
  assert.equal(drawsScene(scene([code], sp({ kind: 'terminal', title: 'Run' })), 'landscape'), false, 'no terminal spec');
  for (const kind of PANEL_KINDS.filter((k) => k !== 'terminal')) {
    const panel = PANEL_TITLED_KINDS.includes(kind) ? { kind, title: 'A plot' } : { kind, title: 'P' };
    assert.equal(drawsScene(scene([code], sp(panel, { kind: 'image', asset_key: 'a', url: '/x.png' })), 'landscape'), false, kind);
  }
  for (const kind of ['skill_tree', 'quiz']) assert.equal(drawsScene(scene([code], sp({ kind })), 'landscape'), false, kind);
  assert.equal(drawsScene(scene([code], { ...sp({ kind: 'figure' }), layout: { show_side_panel: false } }), 'portrait'), true,
    'a hidden panel is no panel');
});

test('panelFor / outputAt: the terminal enters on the first output event, never before its show_at', () => {
  const term = { kind: 'terminal', title: 'Run', terminal: { command: 'python a.py', output: '1\n2' } };
  const base = /** @type {any} */ ({ board: [{ id: 'c1', kind: 'code', code: 'x' }], side_panel: { panel: term, media: null, show_at: 1 }, beats: [], duration: 8 });
  assert.equal(outputAt(base), null);
  assert.equal(panelFor(base, 'landscape')?.start, 1);
  const out = { ...base, events: [{ t: 4, kind: 'output', target: 'panel' }, { t: 3, kind: 'output' }, { t: 2, kind: 'output', target: 'c1' }] };
  assert.equal(outputAt(out), 3, 'on the panel or on no target; the earliest');
  assert.equal(panelFor(out, 'landscape')?.start, 3);
  assert.equal(panelFor({ ...out, events: [{ t: 0.5, kind: 'output' }] }, 'landscape')?.start, 1, 'never before show_at');
  assert.equal(panelFor(out, 'portrait'), null);
  assert.equal(panelFor({ ...base, side_panel: { panel: { kind: 'image', title: 'x' }, media: null, show_at: 0 } }, 'landscape'), null);
});

test('texOf: the heading\'s $TeX$ spans, inline; code is never TeX', () => {
  const scene = /** @type {any} */ ({ board: [{ id: 'h', kind: 'heading', text: 'Solve $x^2 = 4$' }, { id: 'c', kind: 'code', code: 'x = $y$' }] });
  assert.deepEqual(texOf(scene), [{ tex: 'x^2 = 4', display: false }]);
  assert.deepEqual(texOf(/** @type {any} */ ({ board: [{ id: 'c', kind: 'code', code: '$a$' }] })), []);
});

// --- the lexer and the entity runs (colours only) -----------------------------------------------------------------

test('codeSegments: comments and strings per language family, carried over lines where the language does', () => {
  const py = codeSegments(['x = 1  # one', 'print("# no")', 's = """a', '# inside', 'b"""  # after'], lexerFor('python'));
  assert.deepEqual(py[0], [{ text: 'x = 1  ', kind: 'code' }, { text: '# one', kind: 'comment' }]);
  assert.deepEqual(py[1], [{ text: 'print(', kind: 'code' }, { text: '"# no"', kind: 'string' }, { text: ')', kind: 'code' }]);
  assert.deepEqual(py[3], [{ text: '# inside', kind: 'string' }], 'a triple-quoted string runs over lines');
  assert.deepEqual(py[4], [{ text: 'b"""', kind: 'string' }, { text: '  ', kind: 'code' }, { text: '# after', kind: 'comment' }]);
  const esc = codeSegments([`s = "a${BACKSLASH}"b" # c`], lexerFor('py'));
  assert.deepEqual(esc[0].map((r) => r.kind), ['code', 'string', 'code', 'comment'], 'a backslash escapes the quote');
  const c = codeSegments(['int a; /* one', 'two */ int b; // three', '#include <stdio.h>', "char q = '/';"], lexerFor('c'));
  assert.deepEqual(c[0], [{ text: 'int a; ', kind: 'code' }, { text: '/* one', kind: 'comment' }]);
  assert.deepEqual(c[1], [{ text: 'two */', kind: 'comment' }, { text: ' int b; ', kind: 'code' }, { text: '// three', kind: 'comment' }]);
  assert.deepEqual(c[2], [{ text: '#include <stdio.h>', kind: 'code' }], 'a preprocessor line is code in C');
  assert.deepEqual(c[3].map((r) => r.kind), ['code', 'string', 'code']);
  const unterminated = codeSegments(['x = "abc', 'y = 1 # c'], lexerFor('python'));
  assert.deepEqual(unterminated[1].map((r) => r.kind), ['code', 'comment'], 'a one-line string ends with its line');
  const js = codeSegments(['const s = `a', '// in', 'b`; // c'], lexerFor('javascript'));
  assert.deepEqual(js[1], [{ text: '// in', kind: 'string' }], 'a template literal runs over lines');
  assert.deepEqual(js[2].map((r) => r.kind), ['string', 'code', 'comment']);
  const sh = codeSegments(['echo $# ${#a} # yes', "echo 'a # b'"], lexerFor('bash'));
  assert.deepEqual(sh[0], [{ text: 'echo $# ${#a} ', kind: 'code' }, { text: '# yes', kind: 'comment' }], 'a shell # starts a word');
  assert.deepEqual(sh[1].map((r) => r.kind), ['code', 'string']);
  const ml = codeSegments(["b = a'; disp('50% done') % c"], lexerFor('matlab'));
  assert.deepEqual(ml[0].map((r) => r.kind), ['code', 'string', 'code', 'comment'], "A' is the transpose, '50% done' a string");
  const sql = codeSegments(["SELECT 'It''s' -- c"], lexerFor('sql'));
  assert.deepEqual(sql[0].map((r) => r.kind), ['code', 'string', 'code', 'comment']);
  const rust = codeSegments(["fn f<'a>(x: &'a str) // c"], lexerFor('rust'));
  assert.deepEqual(rust[0].map((r) => r.kind), ['code', 'comment'], "a Rust lifetime is no string");
  assert.equal(lexerFor('brainfuck'), null);
  assert.equal(lexerFor(null), null);
  assert.deepEqual(codeSegments(['# not lexed', ''], null), [[{ text: '# not lexed', kind: 'code' }], []]);
  assert.ok(LEXERS.python && LEXERS.c && LEXERS.java && LEXERS.javascript && LEXERS.bash && LEXERS.sql && LEXERS.matlab && LEXERS.verilog,
    'every language classic board.js highlights');
});

test('entityRuns: a whole identifier that names a lesson entity, exact and case-sensitive', () => {
  const roleOf = (/** @type {string} */ id) => ({ V: 'entity:0', I: 'entity:1', R: 'entity:2' })[/** @type {'V'} */ (id)] || null;
  assert.deepEqual(entityRuns('I = V / R', roleOf), [
    { text: 'I', role: 'entity:1' }, { text: ' = ', role: null }, { text: 'V', role: 'entity:0' }, { text: ' / ', role: null },
    { text: 'R', role: 'entity:2' },
  ]);
  assert.deepEqual(entityRuns('Vx = i + 0xR + $V + obj.I', roleOf).filter((r) => r.role).map((r) => r.text), ['I'],
    'never part of a longer word, a number or a $ variable; another case is another name');
  assert.deepEqual(entityRuns('x', roleOf), [{ text: 'x', role: null }]);
  assert.deepEqual(entityRuns('', roleOf), []);
});

// --- the editor's colours -----------------------------------------------------------------------------------------

test('editorLook: the stored code surface in the dark palette; without it the scene\'s own surface and roles', () => {
  for (const name of ['code_walkthrough.landscape.json', 'code_walkthrough.landscape.light.json']) {
    const tl = load(name);
    const scene = tl.scenes[0];
    const roles = makeRoles(tl.presentation, scene.look);
    const ed = editorLook(/** @type {any} */ ({ roles }));
    assert.equal(ed.surface, scene.look.tokens[CODE_SURFACE_TOKEN]);
    assert.equal(ed.mode, 'dark', '§4.3: a dark surface in every family');
    assert.equal(ed.roles.color('ink'), tl.presentation.mode === 'dark' ? tl.presentation.content.ink : tl.presentation.content_alt.ink);
    const bare = { ...scene.look, tokens: { ...scene.look.tokens } };
    delete bare.tokens[CODE_SURFACE_TOKEN];
    const without = editorLook(/** @type {any} */ ({ roles: makeRoles(tl.presentation, bare) }));
    assert.equal(without.mode, tl.presentation.mode);
    assert.equal(without.surface, makeRoles(tl.presentation, bare).color('surface'));
    // no dark palette in the timeline: the scene's own (never a guessed colour)
    const noAlt = { ...tl.presentation, content_alt: {} };
    const fallback = editorLook(/** @type {any} */ ({ roles: makeRoles(noAlt, scene.look) }));
    assert.equal(fallback.mode, tl.presentation.mode);
  }
});

// --- slots (templates.py mirrors them) ------------------------------------------------------------------------------

test('slotRows: the heading line, the code column per layout, the terminal card', () => {
  const rows = /** @type {any} */ (slotRows(ZONES.landscape, ZONES.portrait));
  assert.deepEqual(rows.heading.landscape.default, {
    w: 1728, max_lines: 1, size: [84, 96], role: 'title', face: 'display', weight: 700, line_height: 1.15, prefer_lines: null, max_chars: null,
  });
  assert.deepEqual(rows.heading.portrait.default.size, [48, 64]);
  assert.equal(rows.heading.portrait.default.w, 696);
  const c = rows.code;
  assert.deepEqual(Object.keys(c.landscape), ['default', 'headed', 'panel', 'headed_panel']);
  assert.deepEqual(Object.keys(c.portrait), ['default', 'headed']);
  assert.deepEqual([c.landscape.default.w, c.landscape.panel.w, c.portrait.default.w], [1612, 822, 696]);
  assert.deepEqual([c.landscape.default.max_lines, c.landscape.headed.max_lines, c.portrait.default.max_lines, c.portrait.headed.max_lines], [17, 13, 10, 8]);
  assert.deepEqual([c.landscape.default.max_chars, c.landscape.panel.max_chars, c.portrait.default.max_chars], [60, 38, 28]);
  assert.deepEqual([c.landscape.default.face, c.landscape.default.weight, c.portrait.default.weight], ['mono', 400, 700]);
  assert.equal(c.landscape.default.line_height, CODE_LH.latin);
  assert.deepEqual(rows.terminal.landscape.default.size, [PANEL_TERM.landscape.min, PANEL_TERM.landscape.max]);
  assert.equal(rows.terminal.landscape.default.w, 766 - 2 * PANEL_TERM.landscape.pad);
  assert.equal(rows.terminal.portrait, undefined, 'no panel in 9:16');
});

test('the catalogue\'s code_walkthrough slots and behaviour (templates.py) are slotRows exactly, once it is registered', () => {
  const spec = /** @type {any} */ (CATALOGUE.templates).code_walkthrough;
  assert.equal(spec.dense, true, 'R-55 / R-49: the background freezes and SFX are off');
  const cat = /** @type {any} */ (templateSlots('code_walkthrough'));
  if (!spec.implemented) {
    // before the integrator registers it (templates.py `implemented=True`, `slots=_code_walkthrough_slots()`), the
    // catalogue has no slot table for it; this test turns strict the moment it does
    assert.deepEqual(cat || {}, {}, 'an unregistered template has no slots in the catalogue');
    return;
  }
  const want = /** @type {any} */ (slotRows(ZONES.landscape, ZONES.portrait));
  assert.deepEqual(Object.keys(cat).sort(), Object.keys(want).sort());
  for (const slot of Object.keys(want)) {
    assert.deepEqual(Object.keys(cat[slot]).sort(), Object.keys(want[slot]).sort(), slot);
    for (const fmt of Object.keys(want[slot])) {
      assert.deepEqual(Object.keys(cat[slot][fmt]).sort(), Object.keys(want[slot][fmt]).sort(), `${slot} ${fmt}`);
      for (const variant of Object.keys(want[slot][fmt])) {
        const got = cat[slot][fmt][variant];
        assert.deepEqual({
          w: got.w, max_lines: got.max_lines, size: got.size, role: got.role, face: got.face, weight: got.weight,
          line_height: got.line_height, prefer_lines: got.prefer_lines, max_chars: got.max_chars,
        }, want[slot][fmt][variant], `${slot} ${fmt} ${variant}`);
      }
    }
  }
  const forms = /** @type {any} */ (spec.consumes.forms);
  assert.deepEqual(forms, {
    reveal: ['item'], look_here: ['item', 'item:line', 'panel', 'panel:row'], output: ['panel'],
  });
  assert.equal(spec.push_ok, false, 'a bar, no zoom (§4.3)');
});

// --- the painted behaviour ------------------------------------------------------------------------------------------

test('the editor rises in on the code\'s reveal, then the lines appear one by one, LINE_STEP apart', async () => {
  const tl = load('code_walkthrough.landscape.json');
  const scene = tl.scenes[0];
  const run = spyStage(tl);
  try {
    const enter = enterOf(scene, 'c1');
    const reveal = scene.events.find((/** @type {any} */ e) => e.kind === 'reveal');
    assert.equal(enter, reveal.t, 'a reveal event wins over the beat');
    let t = await run.seekTo(0, enter - 0.1);
    const v = run.ctx(0).view;
    assert.equal(v.frame.style.visibility, 'hidden', `before the entrance (t ${t})`);
    const scale = tl.presentation.motion_scale;
    t = await run.seekTo(0, enter + 0.1);
    assert.equal(v.frame.style.visibility, 'visible');
    assert.ok(op(v.frame.style.opacity) > 0 && op(v.frame.style.opacity) < 1);
    assert.match(v.frame.style.transform, /translate\(0, \d+px\)/, `a ${FRAME_RISE} px rise`);
    assert.equal(v.lines[0].row.style.visibility, 'hidden', 'no line before the editor has settled');
    const ctx = run.ctx(0);
    assert.ok(Math.abs(lineStart(ctx, enter, 0) - (enter + FRAME_ENTER * scale)) < 1e-9);
    assert.ok(Math.abs(lineStart(ctx, enter, 3) - lineStart(ctx, enter, 2) - LINE_STEP * scale) < 1e-9);
    // midway through the cascade: the lines before are in, the ones after are not
    t = await run.seekTo(0, lineStart(ctx, enter, 2) + LINE_FADE * scale + 0.02);
    assert.equal(op(v.lines[0].row.style.opacity), 1);
    assert.equal(op(v.lines[2].row.style.opacity), 1);
    assert.equal(v.lines[4].row.style.visibility, 'hidden', `t ${t}`);
    assert.equal(v.frame.style.transform, 'none');
    await run.seekTo(0, scene.duration - 0.05);
    assert.ok(v.lines.every((/** @type {any} */ l) => op(l.row.style.opacity) === 1 && l.row.style.visibility === 'visible'));
  } finally {
    run.done();
  }
});

test('the code\'s colours: ink, comments in ink2, entity identifiers in their colours (not in strings), Indic in the body stack', async () => {
  for (const name of ['code_walkthrough.landscape.json', 'code_walkthrough.portrait.hindi.json']) {
    const tl = load(name);
    const run = spyStage(tl);
    try {
      await run.seekTo(0, 0.1);
      const ctx = run.ctx(0);
      const v = ctx.view;
      const ed = editorLook(ctx);
      assert.equal(v.frame.style.background, rgb(ed.surface));
      assert.equal(v.codeLayer.style.color, rgb(ed.roles.color('ink')));
      assert.equal(v.codeLayer.style.fontVariantLigatures, 'none', '!= stays two characters');
      const comments = [...v.root.querySelectorAll('.dyn-cw-comment')];
      assert.ok(comments.length > 0 && comments.every((c) => /** @type {HTMLElement} */ (c).style.color === rgb(ed.roles.color('ink2'))));
      const ents = [...v.root.querySelectorAll('.dyn-cw-entity')].map((e) => [e.textContent, /** @type {HTMLElement} */ (e).style.color]);
      assert.ok(ents.length >= 4, name);
      for (const [text, colour] of ents) {
        assert.equal(colour, rgb(ed.roles.color(`entity:${tl.presentation.entity_colors[/** @type {string} */ (text)]}`)), `${name} ${text}`);
      }
      // nothing inside a string or a comment is coloured as an entity
      for (const s of v.root.querySelectorAll('.dyn-cw-string, .dyn-cw-comment')) assert.equal(s.querySelector('.dyn-cw-entity'), null);
      assert.equal(v.lines[0].num.textContent, '1');
      assert.equal(v.lines[0].num.style.color, rgb(ed.roles.color('ink2')));
      if (name.includes('hindi')) {
        const indic = [...v.root.querySelectorAll('.dyn-cw-indic')];
        assert.ok(indic.length >= 2 && indic.every((s) => /** @type {HTMLElement} */ (s).style.fontFamily === 'var(--dyn-font-body)'));
        assert.equal(ed.mode, 'dark');
        assert.equal(tl.presentation.mode, 'light');
      }
    } finally {
      run.done();
    }
  }
});

test('the terminal panel slides up on the output event, its card in the right 45% level with the editor', async () => {
  const tl = load('code_walkthrough.landscape.json');
  const scene = tl.scenes[1];
  const run = spyStage(tl);
  try {
    const at = outputAt(scene);
    assert.ok(at !== null && at > 0);
    await run.seekTo(1, /** @type {number} */ (at) - 0.1);
    const v = run.ctx(1).view;
    assert.ok(v.panel, 'the terminal is drawn');
    assert.equal(v.panel.start, at);
    assert.equal(v.panel.frame.style.visibility, 'hidden', 'not before the output cue');
    await run.seekTo(1, /** @type {number} */ (at) + 0.15);
    assert.equal(v.panel.frame.style.visibility, 'visible');
    assert.match(v.panel.frame.style.transform, /translate\(0, \d+px\)/, 'it slides up');
    assert.ok(v.panel.term && v.panel.term.cmd.textContent === '$ python ohm.py');
  } finally {
    run.done();
  }
});

test('reduced motion (live only): the editor fades in place', async () => {
  const tl = load('code_walkthrough.portrait.json');
  const run = spyStage(tl, { mode: 'live', reducedMotion: true });
  try {
    const t = enterOf(tl.scenes[0], 'c1') + 0.1;
    await run.seekTo(0, t);
    const v = run.ctx(0).view;
    assert.ok(op(v.frame.style.opacity) < 1);
    assert.equal(v.frame.style.transform, 'none');
  } finally {
    run.done();
  }
});

// --- cue geometry, entries, the fallback ----------------------------------------------------------------------------

/** A fake rectOf for jsdom: an element's laid-out style box (stage px), or its line's for an inline span. */
function rectsFrom(/** @type {any} */ ctx) {
  const v = ctx.view;
  ctx.rectOf = (/** @type {any} */ el) => {
    const line = v.lines.find((/** @type {any} */ l) => l.el === el || l.body === el);
    if (line && el === line.el) return { x: Number.parseFloat(el.style.left), y: line.top, w: Number.parseFloat(el.style.width), h: line.pitch };
    if (line) return { x: Number.parseFloat(line.el.style.left) + 100, y: line.top + 2, w: line.text.trim().length * 20, h: line.pitch - 4 };
    if (el === v.frame) return { x: Number.parseFloat(el.style.left), y: Number.parseFloat(el.style.top), w: Number.parseFloat(el.style.width), h: Number.parseFloat(el.style.height) };
    if (v.head && el === v.head.text) return { x: 96, y: 112, w: 600, h: 100 };
    return null;
  };
}

test('focusRect: the editor (its lines kept bright), a line as a bar over its ink band (the frame kept bright), nothing before it shows', async () => {
  const tl = load('code_walkthrough.landscape.json');
  const run = spyStage(tl);
  try {
    const scene = tl.scenes[0];
    await run.seekTo(0, enterOf(scene, 'c1') - 0.2);
    const ctx = run.ctx(0);
    rectsFrom(ctx);
    const tmpl = codeWalkthrough;
    assert.equal(tmpl.focusRect?.(ctx, 'c1'), null, 'the editor is not on screen yet');
    assert.equal(tmpl.focusRect?.(ctx, 'c1:line:3'), null);
    await run.seekTo(0, scene.duration - 0.1);
    const v = ctx.view;
    const whole = tmpl.focusRect?.(ctx, 'c1');
    assert.ok(whole);
    assert.equal(whole.shape, 'rect');
    assert.equal(whole.kind, 'shape');
    assert.equal(whole.el, v.frameGroup);
    assert.deepEqual(whole.keep, v.lines.map((/** @type {any} */ l) => l.el));
    const bar = tmpl.focusRect?.(ctx, 'c1:line:3');
    assert.ok(bar);
    assert.equal(bar.shape, 'bar');
    assert.equal(bar.el, v.lines[3].el);
    assert.deepEqual(bar.keep, [v.frameGroup]);
    const band = barBand(v.lines[3].pitch, v.size);
    assert.equal(bar.rect.y, Math.round(v.lines[3].top + band.top));
    assert.ok(['x', 'y', 'w', 'h'].every((k) => Number.isInteger(/** @type {any} */ (bar.rect)[k])));
    assert.equal(tmpl.focusRect?.(ctx, 'c1:line:99'), null, 'no such line');
    assert.equal(tmpl.focusRect?.(ctx, 'c1:line:01'), null, 'not in the grammar');
    assert.equal(tmpl.focusRect?.(ctx, 'c1:step:1'), null);
    assert.ok(tmpl.focusRect?.(ctx, 'h1'), 'the heading');
    assert.equal(tmpl.focusRect?.(ctx, 'nope'), null);
    assert.equal(tmpl.focusRect?.(ctx, 'panel'), null, 'no panel in this scene');
    // the dim groups: heading, frame, every line (siblings: a dim never compounds)
    assert.deepEqual(tmpl.dimGroups?.(ctx), [v.head.el, v.frameGroup, ...v.lines.map((/** @type {any} */ l) => l.el)]);
    assert.equal(tmpl.itemEl?.(ctx, 'h1'), v.head.text);
    assert.equal(tmpl.itemEl?.(ctx, 'c1'), null, 'code is literal: no [[term]]');
    assert.equal(tmpl.anchor?.(ctx), null);
  } finally {
    run.done();
  }
});

test('an empty line takes no bar; cueMode is the dark editor\'s in a light family and null in a dark one', async () => {
  const tl = load('code_walkthrough.portrait.json');
  const run = spyStage(tl);
  try {
    await run.seekTo(0, tl.scenes[0].duration - 0.1);
    const ctx = run.ctx(0);
    rectsFrom(ctx);
    assert.equal(ctx.view.lines[4].text, '');
    assert.equal(codeWalkthrough.focusRect?.(ctx, 'c1:line:4'), null);
    assert.equal(codeWalkthrough.cueMode(ctx, 'c1:line:3'), null, 'a dark family: the scene\'s own focus');
  } finally {
    run.done();
  }
  const light = load('code_walkthrough.portrait.hindi.json');
  const run2 = spyStage(light);
  try {
    await run2.seekTo(0, 1);
    const ctx = run2.ctx(0);
    assert.equal(codeWalkthrough.cueMode(ctx, 'c1:line:3'), 'dark', 'the dark editor of a light family');
    assert.equal(codeWalkthrough.cueMode(ctx, 'c1'), 'dark');
    assert.equal(codeWalkthrough.cueMode(ctx, 'h1'), null, 'the heading is on the scene\'s own background');
  } finally {
    run2.done();
  }
});

test('the look_here outline on the dark editor of a light family takes the dark palette\'s focus, once registered', async () => {
  const spec = /** @type {any} */ (CATALOGUE.templates).code_walkthrough;
  if (!spec.implemented) return; // the cues.js `cueMode` change lands with the registration (the integrator's request)
  const tl = load('code_walkthrough.landscape.light.json');
  const run = spyStage(tl);
  try {
    const scene = tl.scenes[0];
    const cue = scene.events.find((/** @type {any} */ e) => e.kind === 'look_here');
    await run.seekTo(0, cue.t + 0.5);
    const path = /** @type {SVGElement} */ (run.root.querySelector('.dyn-cue-outline'));
    assert.ok(path);
    assert.equal(path.getAttribute('stroke'), tl.presentation.content_alt.focus);
  } finally {
    run.done();
  }
});

test('texts(): the heading, each code line (critical, on the editor), each number (not critical), the terminal card', async () => {
  const tl = load('code_walkthrough.landscape.json');
  const run = spyStage(tl);
  try {
    await run.seekTo(0, tl.scenes[0].duration - 0.1);
    const ctx = run.ctx(0);
    const entries = /** @type {any[]} */ (codeWalkthrough.texts?.(ctx));
    const ed = editorLook(ctx);
    const heading = entries.find((e) => e.slot === 'heading');
    assert.equal(heading.role, 'title');
    assert.equal(heading.critical, true);
    const code = entries.filter((e) => e.slot === 'code');
    assert.equal(code.length, 5);
    assert.ok(code.every((e) => e.critical && e.bg === ed.surface && e.ink === ed.roles.color('ink') && e.alpha === 1 && e.weight === 400));
    assert.equal(code[3].text, 'I = V / R');
    const nums = entries.filter((e) => !e.slot && e.role === 'body');
    assert.equal(nums.length, 5);
    assert.ok(nums.every((e) => e.critical === false && e.ink === ed.roles.color('ink2')));
    await run.seekTo(1, tl.scenes[1].duration - 0.1);
    const term = /** @type {any[]} */ (codeWalkthrough.texts?.(run.ctx(1))).filter((e) => e.slot === 'terminal');
    assert.equal(term.length, 1);
    // the blank line of the program has a number and no code entry
    const second = /** @type {any[]} */ (codeWalkthrough.texts?.(run.ctx(1)));
    assert.equal(second.filter((e) => e.slot === 'code').length, 3);
    assert.equal(second.filter((e) => !e.slot && e.role === 'body').length, 4);
  } finally {
    run.done();
  }
});

test('a scene it does not draw whole is handed to board_generic, every hook with it', async () => {
  const tl = load('code_walkthrough.portrait.json');
  const scene = tl.scenes[1];
  scene.board[0].code = Array.from({ length: 11 }, () => 'x = 1').join('\n'); // 11 lines: past the reel's 10
  assert.equal(drawsScene(scene, 'portrait'), false);
  const run = spyStage(tl);
  try {
    await run.seekTo(1, 1);
    const ctx = run.ctx(1);
    assert.equal(ctx.view.cwFallback, true);
    assert.equal(run.root.querySelector(`.dyn-scene[data-scene="1"] .dyn-cw`), null);
    assert.deepEqual(codeWalkthrough.texts?.(ctx), boardGeneric.texts?.(ctx));
    assert.deepEqual(codeWalkthrough.dimGroups?.(ctx), boardGeneric.dimGroups?.(ctx));
    assert.equal(codeWalkthrough.cueMode(ctx, 'c1'), null);
    assert.deepEqual(codeWalkthrough.tex?.(scene, scene.look, 'portrait'), boardGeneric.tex?.(scene, scene.look, 'portrait'));
    assert.deepEqual(codeWalkthrough.mediaBox?.(scene, ZONES.portrait, 'render', 'panel', { safe: false, slots: {} }),
      boardGeneric.mediaBox?.(scene, ZONES.portrait, 'render', 'panel', { safe: false, slots: {} }));
  } finally {
    run.done();
  }
  // the fallback paints a frame as a pure function of t too (board_generic, which takes no cue, owns its cards)
  const frames = [0, 15, 30, 45, 60, 120, 200, 260, 300].filter((f) => f < Math.round(tl.total_duration * tl.fps));
  assert.deepEqual(await orderProblems(tl, frames, SEAM), []);
});

// --- the fit, on a fake text layout ---------------------------------------------------------------------------------

/**
 * Install a fake text layout on HTMLElement (jsdom has none): a code line's width is its characters x 0.6 em of the
 * code layer's size (a mono glyph). Returns the restore function.
 */
function fakeLayout() {
  const proto = /** @type {any} */ (HTMLElement.prototype);
  const saved = Object.getOwnPropertyDescriptor(proto, 'offsetWidth');
  Object.defineProperty(proto, 'offsetWidth', {
    configurable: true,
    get() {
      const el = /** @type {HTMLElement} */ (this);
      if (!el.classList.contains('dyn-cw-text')) return 0;
      const layer = /** @type {HTMLElement | null} */ (el.closest('.dyn-cw-code'));
      const size = layer ? Number.parseFloat(layer.style.fontSize) : 16;
      return Math.round(charCount(el.textContent || '') * 0.6 * size);
    },
  });
  return () => {
    if (saved) Object.defineProperty(proto, 'offsetWidth', saved);
    else delete proto.offsetWidth;
  };
}

/** A one-scene timeline of `fmt` with `lines` of code (and a heading unless `heading` is null). */
function codeTimeline(/** @type {'landscape' | 'portrait'} */ fmt, /** @type {string[]} */ lines, heading = /** @type {string | null} */ ('Code')) {
  const tl = load(fmt === 'portrait' ? 'code_walkthrough.portrait.json' : 'code_walkthrough.landscape.long.json');
  tl.scenes = [tl.scenes[0]];
  const scene = tl.scenes[0];
  scene.board = [...(heading ? [{ id: 'h1', kind: 'heading', text: heading }] : []), { id: 'c1', kind: 'code', language: 'python', code: lines.join('\n') }];
  scene.events = [];
  tl.total_duration = scene.duration;
  return tl;
}

test('fit: the largest 4 px size at which every line box and the widest line fit; the editor sized and centred', async () => {
  const restore = fakeLayout();
  try {
    const short = spyStage(codeTimeline('landscape', ['x = 1', 'print(x)'], null));
    await short.seekTo(0, 1);
    const v = short.ctx(0).view;
    assert.equal(v.size, LAYOUT.landscape.code.max, 'a short program at the largest size');
    assert.equal(v.lines[1].top - v.lines[0].top, pitchAt(48, false), 'whole-pixel line boxes');
    assert.equal(Number.parseFloat(v.frame.style.width), 1036, 'never narrower than the centre 60%');
    assert.equal(Number.parseFloat(v.frame.style.left), 96 + (1728 - 1036) / 2, 'centred');
    const frameTop = Number.parseFloat(v.frame.style.top);
    const frameH = Number.parseFloat(v.frame.style.height);
    assert.equal(frameTop, Math.round(112 + (900 - 112 - frameH) / 2), 'alone, centred in its area');
    short.done();

    const wide = spyStage(codeTimeline('landscape', ['x'.repeat(60)], 'Wide'));
    await wide.seekTo(0, 1);
    const w = wide.ctx(0).view;
    assert.equal(w.size, 44, `60 characters at 48 px (${60 * 0.6 * 48}) are wider than the column`);
    assert.equal(Number.parseFloat(w.frame.style.top), boxes('landscape', ZONES.landscape, true, false).codeTop, 'under a heading, at its top');
    assert.ok(w.lines.every((/** @type {any} */ l) => l.overflow === 0));
    wide.done();

    const tall = spyStage(codeTimeline('landscape', Array.from({ length: 17 }, (_, i) => `line_${i} = ${i}`), null));
    await tall.seekTo(0, 1);
    const t = tall.ctx(0).view;
    assert.equal(t.size, 36, '17 lines only at the floor');
    const last = t.lines[16];
    assert.ok(last.top + last.pitch <= 876, 'inside the band');
    assert.ok(t.lines.every((/** @type {any} */ l) => l.overflow === 0));
    tall.done();

    // a line wider than the column even at the floor (not Latin: the char count let it through) is reported
    const over = spyStage(codeTimeline('portrait', ['ஆ'.repeat(28)], null));
    const restoreWide = (() => {
      const proto = /** @type {any} */ (HTMLElement.prototype);
      const d = Object.getOwnPropertyDescriptor(proto, 'offsetWidth');
      Object.defineProperty(proto, 'offsetWidth', { configurable: true, get() { return /** @type {HTMLElement} */ (this).classList.contains('dyn-cw-text') ? 800 : 0; } });
      return () => { if (d) Object.defineProperty(proto, 'offsetWidth', d); };
    })();
    try {
      await over.seekTo(0, 1);
      const o = over.ctx(0).view;
      assert.equal(o.size, 40, 'held at the floor');
      assert.equal(o.lines[0].overflow, 800 - 696, 'the px past the 696 px column');
      const entry = /** @type {any[]} */ (codeWalkthrough.texts?.(over.ctx(0))).find((e) => e.slot === 'code');
      assert.equal(entry.overflow, 104, 'reported, so preflight blocks ("Shorten text")');
    } finally {
      restoreWide();
      over.done();
    }
  } finally {
    restore();
  }
});

test('9:16: the code column is the upper critical box, the gutter left of it, the lines above y 840', async () => {
  const restore = fakeLayout();
  const run = spyStage(codeTimeline('portrait', Array.from({ length: 8 }, () => 'x'.repeat(28)), 'Eight'));
  try {
    await run.seekTo(0, 1);
    const v = run.ctx(0).view;
    assert.equal(v.size, 40);
    const gutter = gutterAt(40);
    const left = Number.parseFloat(v.lines[0].el.style.left);
    assert.equal(left + gutter + LAYOUT.portrait.gap, 192, 'the code starts at the box\'s left edge');
    assert.equal(Number.parseFloat(v.frame.style.left) + Number.parseFloat(v.frame.style.width), 888 + LAYOUT.portrait.padX);
    const last = v.lines[7];
    assert.ok(last.top + last.pitch <= 840, `${last.top + last.pitch}`);
    assert.equal(v.lines[0].num.style.width, `${gutter}px`);
  } finally {
    restore();
    run.done();
  }
});
