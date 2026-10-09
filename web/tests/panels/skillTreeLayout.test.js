import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  MAX_UPSCALE,
  assignLevels,
  countPairCrossings,
  curvePath,
  focusConcepts,
  isotonic,
  layoutSkillTree,
  normalizeConcepts,
  wrapLabel,
} from '../../js/player/panels/skillTreeLayout.js';

const c = (id, deps = [], extra = {}) => ({ id, title: `Concept ${id}`, depends_on: deps, ...extra });

/**
 * Real canvas of the left-mascot side zone (432x740 zone minus card chrome and legend) and the
 * panel's default size: labels must render at >= MIN_FONT px after the SVG viewBox scaling.
 */
const SIDE_CANVAS = { width: 388, height: 580 };
const DEFAULT_CANVAS = { width: 440, height: 640 };
const MIN_FONT = 11;

/** Label size as displayed, recomputed from the layout's own size (independent of `scale`). */
function displayedFont(layout, box) {
  return layout.fontSize * Math.min(MAX_UPSCALE, box.width / layout.width, box.height / layout.height);
}

/** No two node boxes intersect and every node lies inside the layout's own box. */
function assertNoOverlap(layout) {
  const eps = 0.11;
  for (const n of layout.nodes) {
    assert.ok(n.x - n.w / 2 >= -eps && n.x + n.w / 2 <= layout.width + eps, `${n.id} inside horizontally`);
    assert.ok(n.y - n.h / 2 >= -eps && n.y + n.h / 2 <= layout.height + eps, `${n.id} inside vertically`);
  }
  for (let i = 0; i < layout.nodes.length; i++) {
    for (let j = i + 1; j < layout.nodes.length; j++) {
      const a = layout.nodes[i];
      const b = layout.nodes[j];
      const apart = Math.abs(a.x - b.x) >= (a.w + b.w) / 2 - eps || Math.abs(a.y - b.y) >= (a.h + b.h) / 2 - eps;
      assert.ok(apart, `${a.id} and ${b.id} overlap`);
    }
  }
}

/** Complete binary tree of 15 concepts; `inward`: the 8 leaves are prerequisites of their parents. */
function binaryTree(inward) {
  const out = [];
  for (let i = 0; i < 15; i++) {
    const kids = [2 * i + 1, 2 * i + 2].filter((k) => k < 15).map((k) => `b${k}`);
    out.push(c(`b${i}`, inward ? kids : i ? [`b${(i - 1) >> 1}`] : []));
  }
  return out;
}

/** Eight prerequisites feeding one core concept. */
function eightIntoOne() {
  const pre = Array.from({ length: 8 }, (_, i) => c(`p${i}`, [], { kind: 'prerequisite' }));
  return [...pre, c('core', pre.map((p) => p.id))];
}

/** Edges of a layout must form a DAG pointing strictly downward. */
function assertDownward(layout) {
  const level = new Map(layout.nodes.map((n) => [n.id, n.level]));
  for (const e of layout.edges) assert.ok(level.get(e.to) > level.get(e.from), `${e.from}->${e.to} must go down`);
}

test('normalizeConcepts drops duplicates, unknown deps, self loops', () => {
  const { ids, byId } = normalizeConcepts([
    c('a', ['a', 'zzz']),
    c('b', ['a', 'a']),
    c('a', []),
    { id: '', title: 'bad' },
    null,
    c('p', [], { kind: 'prerequisite' }),
  ]);
  assert.deepEqual(ids, ['a', 'b', 'p']);
  assert.deepEqual(byId.get('a').deps, []);
  assert.deepEqual(byId.get('b').deps, ['a']);
  assert.equal(byId.get('p').kind, 'prerequisite');
  assert.equal(byId.get('a').kind, 'core');
});

test('levels are the longest path from the roots', () => {
  const deps = new Map([
    ['a', []],
    ['b', ['a']],
    ['c', ['a', 'b']],
    ['d', ['c']],
    ['e', []],
  ]);
  const { level, edges, broken } = assignLevels(['a', 'b', 'c', 'd', 'e'], deps);
  assert.deepEqual(Object.fromEntries(level), { a: 0, b: 1, c: 2, d: 3, e: 0 });
  assert.equal(edges.length, 4);
  assert.deepEqual(broken, []);
});

test('cycles are broken deterministically and the layout terminates', () => {
  const concepts = [c('a', ['c']), c('b', ['a']), c('c', ['b']), c('d', ['c']), c('e', ['e'])];
  const layout = layoutSkillTree(concepts);
  assert.equal(layout.nodes.length, 5);
  assert.ok(layout.brokenEdges.length >= 1, 'at least one edge dropped');
  assertDownward(layout);
  // same input -> same output
  assert.deepEqual(layoutSkillTree(concepts), layout);
});

test('two-node cycle and fully cyclic graphs still produce levels', () => {
  const { level, broken } = assignLevels(['x', 'y'], new Map([['x', ['y']], ['y', ['x']]]));
  assert.equal(broken.length, 1);
  assert.notEqual(level.get('x'), level.get('y'));
});

test('crossing reduction untangles a swapped layer', () => {
  // naive input order puts c (child of a) after d (child of b) -> crossing-free;
  // swap the input order so the naive layout crosses, the barycenter sweep must fix it.
  const concepts = [c('a'), c('b'), c('d', ['b']), c('c', ['a']), c('e', ['a', 'b'])];
  const layout = layoutSkillTree(concepts);
  assert.equal(layout.crossings, 0);
  const byId = new Map(layout.nodes.map((n) => [n.id, n]));
  assert.ok(byId.get('c').x < byId.get('d').x, 'child of the left root is on the left');
  assertDownward(layout);
});

test('countPairCrossings counts inversions', () => {
  assert.equal(countPairCrossings([[0, 0], [1, 1]]), 0);
  assert.equal(countPairCrossings([[0, 1], [1, 0]]), 1);
  assert.equal(countPairCrossings([[0, 2], [1, 1], [2, 0]]), 3);
  assert.equal(countPairCrossings([[0, 0], [0, 1], [1, 0]]), 1);
});

test('isotonic regression is nondecreasing and mean-preserving', () => {
  const out = isotonic([3, 1, 2, 5, 4], [1, 1, 1, 1, 1]);
  for (let i = 1; i < out.length; i++) assert.ok(out[i] >= out[i - 1]);
  const sum = (a) => a.reduce((s, x) => s + x, 0);
  assert.ok(Math.abs(sum(out) - 15) < 1e-9);
  assert.deepEqual(isotonic([1, 2, 3], [1, 1, 1]), [1, 2, 3]);
});

test('nodes never overlap and stay inside the layout box (both orientations)', () => {
  const concepts = [c('r1'), c('r2'), c('r3')];
  for (let i = 0; i < 9; i++) concepts.push(c(`n${i}`, [`r${(i % 3) + 1}`, ...(i > 2 ? [`n${i - 3}`] : [])]));
  for (const orientation of [undefined, 'tb', 'lr']) {
    const layout = layoutSkillTree(concepts, { width: 480, height: 800, orientation });
    assertNoOverlap(layout);
    assertDownward(layout);
    assert.equal(layout.levels, 4);
    if (orientation) assert.equal(layout.orientation, orientation);
  }
});

test('tb edges run from the bottom of the prerequisite to the top of the dependent', () => {
  const layout = layoutSkillTree([c('a'), c('b', ['a']), c('c', ['b']), c('d', ['c', 'a'])], { orientation: 'tb' });
  const byId = new Map(layout.nodes.map((n) => [n.id, n]));
  for (const e of layout.edges) {
    const from = byId.get(e.from);
    const to = byId.get(e.to);
    assert.ok(Math.abs(e.points[0].y - (from.y + from.h / 2)) < 0.11);
    assert.ok(Math.abs(e.points.at(-1).y - (to.y - to.h / 2)) < 0.11);
    assert.equal(e.points[0].x, from.x);
    assert.match(e.d, /^M[\d.-]+ [\d.-]+( C[\d. -]+)+$/);
  }
  const long = layout.edges.find((e) => e.from === 'a' && e.to === 'd');
  assert.equal(long.points.length, 4, 'two dummy points for an edge spanning three levels');
  assert.ok(long.points[1].y > long.points[0].y && long.points[2].y > long.points[1].y, 'dummies run downward');
});

test('lr edges run from the right side of the prerequisite to the left side of the dependent', () => {
  const layout = layoutSkillTree([c('a'), c('b', ['a']), c('c', ['b']), c('d', ['c', 'a'])], { orientation: 'lr' });
  assert.equal(layout.orientation, 'lr');
  const byId = new Map(layout.nodes.map((n) => [n.id, n]));
  for (const n of layout.nodes) assert.equal(n.x > 0, true);
  assert.ok(byId.get('a').x < byId.get('b').x && byId.get('b').x < byId.get('c').x, 'levels run left to right');
  for (const e of layout.edges) {
    const from = byId.get(e.from);
    const to = byId.get(e.to);
    assert.ok(Math.abs(e.points[0].x - (from.x + from.w / 2)) < 0.11);
    assert.ok(Math.abs(e.points.at(-1).x - (to.x - to.w / 2)) < 0.11);
    assert.equal(e.points[0].y, from.y);
    assert.equal(e.points.at(-1).y, to.y);
  }
  const long = layout.edges.find((e) => e.from === 'a' && e.to === 'd');
  assert.equal(long.points.length, 4);
  assert.ok(long.points[1].x > long.points[0].x && long.points[2].x > long.points[1].x, 'dummies run rightward');
  assertNoOverlap(layout);
});

test('side-zone fit: labels render at >= 11 px for wide maps (reviewer cases)', () => {
  const cases = { binaryOut: binaryTree(false), binaryIn: binaryTree(true), eightIntoOne: eightIntoOne() };
  for (const box of [SIDE_CANVAS, DEFAULT_CANVAS]) {
    for (const [name, concepts] of Object.entries(cases)) {
      const l = layoutSkillTree(concepts, box);
      const shown = displayedFont(l, box);
      assert.ok(shown >= MIN_FONT, `${name} at ${box.width}x${box.height}: ${shown.toFixed(1)} px`);
      // (fontSize, width and height are rounded to 0.1 in the result)
      assert.ok(Math.abs(shown - l.renderedFont) < 0.2, `renderedFont reports the displayed size (${shown} vs ${l.renderedFont})`);
      assert.equal(l.fits, true, `${name} fits without scaling down`);
      assert.ok(l.width <= box.width + 0.5 && l.height <= box.height + 0.5);
      assertNoOverlap(l);
      assert.equal(l.nodes.length, concepts.length);
    }
  }
  // the old single orientation could not do it: forcing tb scales the 8-wide layer below 11 px
  const tb = layoutSkillTree(eightIntoOne(), { ...SIDE_CANVAS, orientation: 'tb' });
  assert.ok(displayedFont(tb, SIDE_CANVAS) < MIN_FONT);
});

test('orientation: deep narrow maps stay top-to-bottom, wide shallow ones go left-to-right', () => {
  const chain = Array.from({ length: 8 }, (_, i) => c(`k${i}`, i ? [`k${i - 1}`] : []));
  assert.equal(layoutSkillTree(chain, SIDE_CANVAS).orientation, 'tb');
  assert.equal(layoutSkillTree(eightIntoOne(), SIDE_CANVAS).orientation, 'lr');
  assert.equal(layoutSkillTree([c('a'), c('b', ['a'])], SIDE_CANVAS).orientation, 'tb', 'ties keep tb');
  // a landscape box keeps tb for the 8-wide layer (it fits)
  assert.equal(layoutSkillTree(eightIntoOne(), { width: 900, height: 300 }).orientation, 'tb');
});

test('small maps are enlarged at most MAX_UPSCALE times', () => {
  const one = layoutSkillTree([c('only')], SIDE_CANVAS);
  assert.ok(one.scale <= MAX_UPSCALE);
  assert.ok(one.renderedFont <= one.fontSize * MAX_UPSCALE + 0.05);
  assert.ok(one.renderedFont >= MIN_FONT);
});

test('boxes hug their labels: short titles get narrower boxes than long ones', () => {
  const l = layoutSkillTree(
    [c('a', [], { title: 'AC' }), c('b', [], { title: 'Kirchhoff current law at a junction' })],
    { ...SIDE_CANVAS, orientation: 'tb' },
  );
  const byId = new Map(l.nodes.map((n) => [n.id, n]));
  assert.ok(byId.get('a').w < byId.get('b').w);
  assert.ok(byId.get('a').w >= l.fontSize * 3 - 0.1, 'minimum box width');
  assertNoOverlap(l);
});

test('the font shrinks to fit short panels but never below the minimum', () => {
  const concepts = [];
  for (let i = 0; i < 8; i++) concepts.push(c(`k${i}`, i ? [`k${i - 1}`] : []));
  const tall = layoutSkillTree(concepts, { width: 480, height: 1600 });
  const short = layoutSkillTree(concepts, { width: 480, height: 400 });
  assert.ok(short.fontSize < tall.fontSize);
  assert.ok(short.fontSize >= 11);
  assert.ok(short.height < tall.height);
  for (const box of [{ width: 200, height: 200 }, { width: 1600, height: 300 }, { width: 120, height: 2000 }]) {
    const l = layoutSkillTree(concepts, box);
    assert.ok(Number.isFinite(l.width) && Number.isFinite(l.height) && l.width > 0 && l.height > 0);
  }
});

test('labels wrap into at most three lines with an ellipsis', () => {
  assert.deepEqual(wrapLabel('Ohm law basics', 20), ['Ohm law basics']);
  assert.deepEqual(wrapLabel('Kirchhoff current law at a node', 12), ['Kirchhoff', 'current law', 'at a node']);
  const many = wrapLabel('one two three four five six seven eight nine ten', 8, 3);
  assert.equal(many.length, 3);
  assert.ok(many[2].endsWith('…'));
  const longWord = wrapLabel('Electroencephalography', 8);
  assert.ok(longWord.every((l) => Array.from(l).length <= 8));
  assert.deepEqual(wrapLabel('', 10), ['']);
});

test('focusConcepts keeps ancestors (2 hops) and dependents (1 hop) of the active concept', () => {
  const concepts = [c('a'), c('b', ['a']), c('c', ['b']), c('d', ['c']), c('e', ['d']), c('f', ['e']), c('x')];
  const focused = focusConcepts(concepts, 'd');
  assert.deepEqual(focused.map((n) => n.id), ['b', 'c', 'd', 'e']);
  assert.deepEqual(focused.find((n) => n.id === 'b').depends_on, []);
  assert.equal(focusConcepts(concepts, 'nope'), concepts);
});

test('large maps lay out quickly', () => {
  const concepts = [];
  for (let i = 0; i < 150; i++) {
    const deps = [];
    if (i > 3) deps.push(`n${(i * 7) % i}`, `n${(i * 13) % i}`);
    concepts.push(c(`n${i}`, deps));
  }
  const t0 = performance.now();
  const layout = layoutSkillTree(concepts, { width: 480, height: 860 });
  assert.ok(performance.now() - t0 < 2000);
  assert.equal(layout.nodes.length, 150);
  assertDownward(layout);
});

test('curvePath uses tangents along the flow (vertical for tb, horizontal for lr)', () => {
  assert.equal(curvePath([{ x: 0, y: 0 }, { x: 10, y: 20 }]), 'M0 0 C0 10 10 10 10 20');
  assert.equal(curvePath([{ x: 0, y: 0 }, { x: 10, y: 20 }], 'tb'), 'M0 0 C0 10 10 10 10 20');
  assert.equal(curvePath([{ x: 0, y: 0 }, { x: 20, y: 10 }], 'lr'), 'M0 0 C10 0 10 10 20 10');
  assert.equal(curvePath([]), '');
});

test('empty input', () => {
  const layout = layoutSkillTree([]);
  assert.equal(layout.nodes.length, 0);
  assert.equal(layout.edges.length, 0);
  assert.equal(layout.levels, 0);
  assert.ok(Number.isFinite(layout.renderedFont) && layout.width > 0 && layout.height > 0);
});
