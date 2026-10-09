import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import { createRequire } from 'node:module';

import {
  DISABLED_FUNCTIONS,
  EXPR_FUNCS,
  ExprError,
  autoYRange,
  checkExpr,
  createSafeMath,
  decimalsOf,
  digitValue,
  formatTick,
  niceTicks,
  normalizeExpr,
  sampleFunction,
  splitSegments,
  toNumber,
} from '../../js/player/panels/graphMath.js';

const require = createRequire(import.meta.url);
const mathjs = require('mathjs');

const MALICIOUS = [
  'import({}, {})',
  'evaluate("1")',
  'parse("1")',
  'constructor',
  'x.constructor',
  'sin.constructor("return process")()',
  'createUnit("foo")',
  'simplify("x")',
  'derivative("x^2", "x")',
  'resolve(x)',
  'a = 1',
  'f(x) = x',
  '[1, 2]',
  'x; 1',
  '"str"',
  "'str'",
  'x > 1',
  '__proto__',
  'pi.toString()',
  'x ? 1 : 2',
  '{a: 1}',
  'cos(x) # comment',
  'x & 1',
  'x | 1',
  'x!',
  'x % 2',
];

test('the JS allow-list mirrors screenplay.py _EXPR_FUNCS', () => {
  const py = readFileSync(new URL('../../../aadhi/schemas/screenplay.py', import.meta.url), 'utf8');
  const block = /_EXPR_FUNCS = \{([^}]*)\}/.exec(py);
  assert.ok(block, '_EXPR_FUNCS found in screenplay.py');
  const names = [...block[1].matchAll(/"([^"]+)"/g)].map((m) => m[1]).sort();
  assert.deepEqual([...EXPR_FUNCS].sort(), names);
  const token = /_EXPR_TOKEN = re\.compile\(r"(.*)"(?:, re\.ASCII)?\)/.exec(py);
  assert.ok(token, '_EXPR_TOKEN found');
  assert.ok(token[1].includes('[-+*/^(),.]'), 'same operator set as the JS tokenizer');
  assert.ok(/re\.ASCII\)/.test(token[0]), 'ASCII-only digits/whitespace, like the JS tokenizer');
});

test('checkExpr accepts allow-listed expressions and normalises **', () => {
  assert.equal(checkExpr('x^2 + 2*x'), 'x^2 + 2*x');
  assert.equal(checkExpr('2**x'), '2^x');
  assert.equal(checkExpr('exp(-x/2) * sin(3.5e-1*x) '), 'exp(-x/2) * sin(3.5e-1*x)', 'trimmed like pydantic');
  assert.equal(checkExpr('min(x, .5) + max(abs(x), pi)'), 'min(x, .5) + max(abs(x), pi)');
});

test('checkExpr rejects everything outside the allow-list', () => {
  for (const expr of MALICIOUS) assert.throws(() => checkExpr(expr), ExprError, expr);
  assert.throws(() => checkExpr(''), ExprError);
  assert.throws(() => checkExpr('  \t\n'), ExprError, 'only whitespace');
  assert.throws(() => checkExpr('x+'.repeat(150)), ExprError);
  assert.throws(() => checkExpr(/** @type {any} */ (42)), ExprError);
});

test('Unicode digits and whitespace the backend accepts are normalised to what math.js parses', () => {
  assert.equal(digitValue('२'), 2, 'Devanagari two');
  assert.equal(digitValue('١'), 1, 'Arabic-Indic one');
  assert.equal(digitValue('௯'), 9, 'Tamil nine');
  assert.equal(digitValue('\u{1D7CF}'), 1, 'mathematical bold one (first of five adjacent runs)');
  assert.equal(digitValue('\u{1D7FF}'), 9, 'mathematical monospace nine (last run)');
  assert.equal(digitValue('7'), 7);
  for (let cp = 0x0966; cp <= 0x096f; cp++) assert.equal(digitValue(String.fromCodePoint(cp)), cp - 0x0966);
  assert.equal(normalizeExpr('x*२'), 'x*2');
  assert.equal(normalizeExpr('١٠+x'), '10+x');
  assert.equal(normalizeExpr(' x + 1　'), 'x + 1', 'NBSP / em space / ideographic space');
  assert.equal(normalizeExpr('x\n+\t1\u001c\u0085'), 'x + 1', 'newline would end a math.js statement');
  const safe = createSafeMath(mathjs);
  assert.equal(safe.compileExpr('x*२')(3), 6);
  assert.equal(safe.compileExpr('x + 1')(2), 3);
  assert.equal(safe.compileExpr('\u{1D7D0}**x')(3), 8);
  // zero-width characters are not whitespace for either validator
  assert.throws(() => checkExpr('x​+1'), ExprError);
  // the length limit counts code points (Python len), not UTF-16 units
  assert.doesNotThrow(() => checkExpr('\u{1D7CF}'.repeat(150)));
});

/** Expressions run through both validators (screenplay.py GraphFunction and checkExpr). */
const PARITY_TABLE = [
  'x', 'x^2 + 2*x', '2**x', 'sin(x)/x', '.5*x', '1.e3', '3.5e-1*x', 'exp(-x/2)', 'log10(x)', 'mod(x, 2)',
  '  x  ', '\tx\t', 'x\n+1', 'x\r\n+1', 'x + 1', 'x +1', 'x　+1', 'x\u001c+1', 'x\u0085+1',
  'x﻿+1', 'x​+1', 'x*२', '١+x', '௧௦*x', '\u{1D7CF}+x', '１+x', 'x²',
  '½*x', 'x− 1', 'X', 'xx', 'sinx', 'Sin(x)', 'x;1', 'x % 2', '"x"', 'x.y', '1..2', 'x__', '',
  ' ', ' ', 'x'.repeat(200), 'x'.repeat(201), `${'\u{1D7CF}'.repeat(100)}+x`, 'constructor', 'e^x', 'pi*x',
];

/** Rows made of allowed tokens that math.js cannot parse (no validator checks syntax). */
const TOKEN_VALID_BUT_MALFORMED = new Set(['1..2']);

/** ASCII-only rows must be judged identically by both validators. */
const isAscii = (s) => /^[\x20-\x7e]*$/.test(s);

/**
 * Run PARITY_TABLE through screenplay.py with the project's venv (null when Python is unavailable).
 * @returns {boolean[] | null}
 */
function pythonVerdicts() {
  const root = fileURLToPath(new URL('../../../', import.meta.url));
  const candidates = [
    process.env.AADHI_PYTHON,
    path.join(root, '.venv', 'Scripts', 'python.exe'),
    path.join(root, '.venv', 'bin', 'python'),
  ].filter(Boolean);
  const python = candidates.find((p) => existsSync(p));
  if (!python) return null;
  const script = [
    'import json, sys',
    'from aadhi.schemas.screenplay import GraphFunction',
    'out = []',
    'for e in json.loads(sys.stdin.read()):',
    '    try:',
    '        GraphFunction(expr=e)',
    '        out.append(True)',
    '    except Exception:',
    '        out.append(False)',
    'print(json.dumps(out))',
  ].join('\n');
  const res = spawnSync(python, ['-c', script], {
    cwd: root,
    input: JSON.stringify(PARITY_TABLE),
    encoding: 'utf8',
    env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
    timeout: 60_000,
  });
  if (res.status !== 0) return null;
  return JSON.parse(res.stdout.trim().split('\n').pop());
}

test('frontend and backend expression validators agree (screenplay.py via the venv)', (t) => {
  const py = pythonVerdicts();
  if (!py) {
    t.skip('Python venv with the aadhi package not available');
    return;
  }
  assert.equal(py.length, PARITY_TABLE.length);
  const safe = createSafeMath(mathjs);
  PARITY_TABLE.forEach((expr, i) => {
    let js = true;
    try {
      checkExpr(expr);
    } catch {
      js = false;
    }
    const label = JSON.stringify(expr);
    if (py[i]) assert.equal(js, true, `backend accepts ${label}, so the panel must accept it too`);
    if (isAscii(expr)) assert.equal(js, py[i], `ASCII ${label}: both validators must agree`);
    // Both validators are token allow-lists, not parsers: a token-valid but malformed expression
    // passes both and is reported by the panel as "(cannot be plotted)".
    if (py[i] && !TOKEN_VALID_BUT_MALFORMED.has(expr)) assert.doesNotThrow(() => safe.compileExpr(expr), label);
  });
  // sanity: the table exercises both outcomes on both sides
  assert.ok(py.some(Boolean) && py.some((v) => !v));
});

test('hardened math.js instance: disabled functions throw, compile still works', () => {
  const safe = createSafeMath(mathjs);
  assert.equal(createSafeMath(mathjs), safe, 'cached per library');
  for (const name of ['import', 'createUnit', 'evaluate', 'parse', 'simplify', 'derivative', 'resolve', 'compile']) {
    assert.ok(DISABLED_FUNCTIONS.includes(name));
    assert.throws(() => safe.math[name]('1'), /disabled/, name);
  }
  const f = safe.compileExpr('x^2 + sin(x)');
  assert.ok(Math.abs(f(2) - (4 + Math.sin(2))) < 1e-12);
  assert.ok(Number.isNaN(safe.compileExpr('sqrt(x)')(-1)), 'predictable: no complex results');
  assert.ok(Number.isNaN(safe.compileExpr('log(x)')(-1)));
  assert.equal(safe.compileExpr('x**3')(2), 8);
});

test('malicious expressions never reach math.js', () => {
  const safe = createSafeMath(mathjs);
  for (const expr of MALICIOUS) assert.throws(() => safe.compileExpr(expr), ExprError, expr);
});

test('createSafeMath requires a math.js namespace', () => {
  assert.throws(() => createSafeMath(null), /not available/);
  assert.throws(() => createSafeMath({}), /not available/);
});

test('the vendored browser bundle (default instance, no factories) is hardened in place', () => {
  const src = readFileSync(new URL('../../vendor/mathjs/math.js', import.meta.url), 'utf8');
  // Same realm as the module under test (as in a browser page); the UMD wrapper sets `this.math`.
  const previous = globalThis.math;
  vm.runInThisContext(src, { filename: 'math.js' });
  const math = globalThis.math;
  if (previous === undefined) delete globalThis.math;
  else globalThis.math = previous;
  assert.equal(typeof math.compile, 'function');
  assert.equal(math.all, undefined, 'UMD build exposes no factories');
  const safe = createSafeMath(math);
  assert.equal(safe.math, math);
  for (const name of ['import', 'createUnit', 'evaluate', 'parse', 'simplify', 'derivative', 'resolve', 'compile']) {
    assert.throws(() => math[name]('1'), /disabled/, name);
  }
  assert.equal(safe.compileExpr('x^2 + 1')(3), 10);
  assert.ok(Number.isNaN(safe.compileExpr('sqrt(x)')(-4)), 'predictable config applied');
  for (const expr of MALICIOUS) assert.throws(() => safe.compileExpr(expr), ExprError, expr);
  assert.equal(createSafeMath(math), safe, 'idempotent');
});

test('toNumber maps math.js results to finite numbers or NaN', () => {
  assert.equal(toNumber(3), 3);
  assert.ok(Number.isNaN(toNumber(Infinity)));
  assert.equal(toNumber({ re: 2, im: 0 }), 2);
  assert.ok(Number.isNaN(toNumber({ re: 0, im: 1 })));
  assert.ok(Number.isNaN(toNumber('5')));
  assert.ok(Number.isNaN(toNumber(true)));
});

test('sampleFunction includes the endpoints and refines curved regions', () => {
  const line = sampleFunction((x) => 2 * x + 1, -2, 2, { initial: 40 });
  assert.equal(line.points[0].x, -2);
  assert.equal(line.points.at(-1).x, 2);
  assert.equal(line.points.length, 2 * 40 + 1, 'a line needs one midpoint check per interval');
  for (let i = 1; i < line.points.length; i++) assert.ok(line.points[i].x > line.points[i - 1].x);
  const wave = sampleFunction((x) => Math.sin(8 * x), -2, 2, { initial: 40 });
  assert.ok(wave.points.length > line.points.length);
  assert.equal(wave.uniform.length, 41);
});

test('sampleFunction respects the point budget and survives throwing functions', () => {
  const s = sampleFunction((x) => Math.sin(1 / x), -1, 1, { initial: 50, maxPoints: 300 });
  assert.ok(s.points.length <= 300 + 60);
  const t = sampleFunction(() => {
    throw new Error('boom');
  }, 0, 1, { initial: 10 });
  assert.ok(t.points.every((p) => Number.isNaN(p.y)));
});

test('splitSegments breaks at undefined values and asymptotes', () => {
  const safe = createSafeMath(mathjs);
  const sq = sampleFunction(safe.compileExpr('sqrt(x)'), -2, 2);
  const sqSegs = splitSegments(sq.points, [-1, 2], sq.minDx);
  assert.equal(sqSegs.length, 1);
  assert.ok(sqSegs[0][0].x >= -1e-6 && sqSegs[0][0].x < 0.05);

  const inv = sampleFunction(safe.compileExpr('1/x'), -3, 3, { initial: 60 });
  const range = autoYRange(inv.uniform);
  const invSegs = splitSegments(inv.points, range, inv.minDx);
  assert.ok(invSegs.length >= 2);
  for (const seg of invSegs) assert.ok(seg.every((p) => p.x < 0) || seg.every((p) => p.x > 0), 'no segment crosses x = 0');

  const tan = sampleFunction(safe.compileExpr('tan(x)'), -5, 5);
  const tr = autoYRange(tan.uniform);
  assert.ok(tr[1] - tr[0] < 40, `tan range is robust to spikes: ${tr}`);
  assert.ok(splitSegments(tan.points, tr, tan.minDx).length >= 4, 'tan has asymptotes at ±pi/2, ±3pi/2');

  const step = sampleFunction(safe.compileExpr('floor(x)'), 0, 3, { initial: 30 });
  assert.ok(splitSegments(step.points, [0, 3], step.minDx).length >= 3, 'unresolved jumps split floor()');
});

test('autoYRange pads, includes points and snaps to zero', () => {
  const [lo, hi] = autoYRange([1, 2, 3, 4, 5, 6]);
  assert.ok(lo < 0 && lo > -1, 'snapped to 0 then padded');
  assert.ok(hi > 6);
  const [lo2] = autoYRange([10, 11, 12]);
  assert.ok(lo2 > 9, 'far from zero: no snapping');
  const [clo, chi] = autoYRange([3, 3, 3]);
  assert.ok(chi - clo >= 2);
  const [plo, phi] = autoYRange([0, 1], [-10, 20]);
  assert.ok(plo < -10 && phi > 20);
  assert.deepEqual(autoYRange([NaN]), [-1, 1]);
  assert.deepEqual(autoYRange([]), [-1, 1]);
});

test('niceTicks uses 1/2/2.5/5 steps', () => {
  assert.deepEqual(niceTicks(0, 10, 6), { ticks: [0, 2, 4, 6, 8, 10], step: 2 });
  const t = niceTicks(-1.1, 1.1, 6);
  assert.equal(t.step, 0.5);
  assert.deepEqual(t.ticks, [-1, -0.5, 0, 0.5, 1]);
  assert.equal(niceTicks(0, 1, 5).step, 0.25);
  assert.deepEqual(niceTicks(1, 1), { ticks: [], step: 0 });
  assert.ok(niceTicks(0, 1e9, 6).ticks.length <= 7);
});

test('formatTick prints just enough decimals with a typographic minus', () => {
  assert.equal(decimalsOf(0.25), 2);
  assert.equal(decimalsOf(2.5), 1);
  assert.equal(decimalsOf(5), 0);
  assert.equal(decimalsOf(0.0001), 4);
  assert.equal(formatTick(0.25, 0.25), '0.25');
  assert.equal(formatTick(0.5, 0.25), '0.5');
  assert.equal(formatTick(-1, 0.5), '−1');
  assert.equal(formatTick(-0, 1), '0');
  assert.equal(formatTick(0.1 + 0.2, 0.1), '0.3');
  assert.equal(formatTick(200000, 100000), '2.0e+5');
});
