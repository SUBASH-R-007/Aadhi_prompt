import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import * as THREE from 'three';

import { container, fakeContext2d, flush, installDom, stubCanvas, timelineWith, trackListeners } from './_env.js';
import { createPanel } from '../../js/player/panels/index.js';
import { createChrome } from '../../js/player/panels/chrome.js';
import { buildChartConfig } from '../../js/player/panels/chart.js';
import { buildGraphModel, drawGraph } from '../../js/player/panels/graph.js';
import { createSafeMath } from '../../js/player/panels/graphMath.js';
import {
  RENDER_ANGLE,
  buildPrimitives,
  fitDistance,
  model3dFactory,
  orbitPosition,
} from '../../js/player/panels/model3d.js';

const w = installDom();
const ctx2d = stubCanvas(w);
const require = createRequire(import.meta.url);
const mathjs = require('mathjs');
w.math = mathjs;
const doc = w.document;

// ---------------------------------------------------------------- chart config

test('buildChartConfig: bar chart with axis titles and theme colours', () => {
  const cfg = buildChartConfig(
    { chart_type: 'bar', labels: ['**R1**', 'R2'], datasets: [{ label: 'Ohms', data: [10, 'x'] }], x_label: 'Resistor', y_label: 'Value' },
    { animate: true, render: false },
  );
  assert.equal(cfg.type, 'bar');
  assert.deepEqual(cfg.data.labels, ['R1', 'R2']);
  assert.deepEqual(cfg.data.datasets[0].data, [10, null]);
  assert.equal(cfg.options.scales.x.title.text, 'Resistor');
  assert.equal(cfg.options.scales.y.title.text, 'Value');
  assert.equal(cfg.options.scales.y.beginAtZero, true);
  assert.equal(cfg.options.plugins.legend.display, false, 'single dataset: no legend');
  assert.equal(typeof cfg.options.animation, 'object');
  assert.equal(cfg.options.responsive, true);
  assert.equal(cfg.options.devicePixelRatio, undefined);
});

test('buildChartConfig: render mode disables animation, events and tooltips', () => {
  const cfg = buildChartConfig({ chart_type: 'line', labels: ['a'], datasets: [{ data: [1] }, { data: [2] }] }, { animate: false, render: true });
  assert.equal(cfg.options.animation, false);
  assert.equal(cfg.options.devicePixelRatio, 1);
  assert.deepEqual(cfg.options.events, []);
  assert.equal(cfg.options.plugins.tooltip.enabled, false);
  assert.equal(cfg.options.plugins.legend.display, true);
  assert.equal(cfg.data.datasets[0].label, 'Series 1');
  assert.equal(cfg.data.datasets[0].fill, false, 'two lines are not filled');
});

test('buildChartConfig: pie/doughnut colour slices, radar uses the r scale', () => {
  const pie = buildChartConfig({ chart_type: 'pie', labels: ['a', 'b', 'c'], datasets: [{ data: [1, 2, 3] }] }, { animate: false, render: true });
  assert.equal(pie.options.scales, undefined);
  assert.equal(pie.data.datasets[0].backgroundColor.length, 3);
  assert.equal(new Set(pie.data.datasets[0].backgroundColor).size, 3);
  assert.equal(pie.options.plugins.legend.display, true);
  const radar = buildChartConfig({ chart_type: 'radar', labels: ['a', 'b', 'c'], datasets: [{ data: [1, 2, 3] }] }, { animate: false, render: false });
  assert.ok(radar.options.scales.r);
  assert.equal(buildChartConfig({ chart_type: 'evil', labels: [], datasets: [] }, { animate: false, render: false }).type, 'bar');
});

// ---------------------------------------------------------------- graph

const SPEC = {
  functions: [{ expr: 'x^2', label: 'y = x squared' }, { expr: 'import({}, {})' }, { expr: 'sin(x)' }],
  points: [{ x: 1, y: 1, label: 'P' }, { x: 99, y: 0 }],
  x_range: [-2, 2],
  x_label: 'x',
  y_label: 'y',
};

test('buildGraphModel compiles allowed expressions and flags the rest', () => {
  const m = buildGraphModel(SPEC, createSafeMath(mathjs));
  assert.equal(m.fns.length, 3);
  assert.equal(m.fns[0].error, null);
  assert.match(m.fns[1].error, /unknown identifier "import"/);
  assert.equal(m.fns[1].label, 'y = import({}, {})');
  assert.ok(m.y0 <= 0 && m.y1 >= 4, `y range covers x^2 on [-2, 2]: ${m.y0}..${m.y1}`);
  assert.ok(m.fns[0].segments.length === 1 && m.fns[0].segments[0].length > 100);
  assert.equal(m.points.length, 2);
  const fixed = buildGraphModel({ ...SPEC, y_range: [-1, 1] }, createSafeMath(mathjs));
  assert.deepEqual([fixed.y0, fixed.y1], [-1, 1]);
  const bad = buildGraphModel({ functions: [{ expr: 'x' }], x_range: [3, 1] }, createSafeMath(mathjs));
  assert.deepEqual([bad.x0, bad.x1], [-5, 5]);
});

test('drawGraph draws grid, axes, labels, curves and points; progress clips the stroke', () => {
  const m = buildGraphModel(SPEC, createSafeMath(mathjs));
  const full = fakeContext2d();
  drawGraph(full, m, { width: 440, height: 320, progress: 1 });
  const count = (c, name) => c.calls.filter((x) => x[0] === name).length;
  assert.ok(count(full, 'stroke') >= 4);
  assert.equal(count(full, 'arc'), 1, 'only the in-range point');
  const texts = full.calls.filter((x) => x[0] === 'fillText').map((x) => x[1]);
  assert.ok(texts.includes('P') && texts.includes('x') && texts.includes('y'));
  assert.ok(texts.includes('−2'), 'typographic minus on ticks');
  assert.equal(count(full, 'clip'), 1);

  const half = fakeContext2d();
  drawGraph(half, m, { width: 440, height: 320, progress: 0.5 });
  assert.ok(count(half, 'lineTo') < count(full, 'lineTo'));
  assert.equal(count(half, 'arc'), 0, 'point at x=1 not reached at 50%');
  const again = fakeContext2d();
  drawGraph(again, m, { width: 440, height: 320, progress: 0.5 });
  assert.deepEqual(again.calls, half.calls, 'deterministic');

  const tiny = fakeContext2d();
  drawGraph(tiny, m, { width: 30, height: 30, progress: 1 });
  assert.equal(count(tiny, 'stroke'), 0, 'too small to plot');
});

test('graph panel: render mode draws the full graph in ready(), legend marks invalid functions', async () => {
  ctx2d.calls.length = 0;
  const rsp = { show_at: 0, panel: { kind: 'graph', title: 'Parabola', graph: SPEC } };
  const p = createPanel(container(doc), rsp, { mode: 'render', timeline: timelineWith(rsp), sceneIndex: 0 });
  p.update(0, {});
  await p.ready();
  assert.ok(ctx2d.calls.some((c) => c[0] === 'arc'), 'points drawn (progress 1)');
  const keys = [...p.el.querySelectorAll('.ap-graph-key')];
  assert.equal(keys.length, 3);
  assert.ok(keys[1].classList.contains('is-error'));
  assert.match(keys[1].textContent, /cannot be plotted/);
  const canvas = p.el.querySelector('canvas');
  assert.equal(canvas.width, 400, 'DPR 1 render canvas (jsdom has no layout: 400 px fallback)');
  p.destroy();
});

test('graph panel: live mode strokes progressively from show_at', async () => {
  const rsp = { show_at: 2, panel: { kind: 'graph', graph: { functions: [{ expr: 'x' }], points: [{ x: 1.5, y: 1.5 }], x_range: [-2, 2] } } };
  const p = createPanel(container(doc), rsp, { mode: 'live', timeline: timelineWith(rsp), sceneIndex: 0 });
  p.update(2.2, {});
  await flush();
  ctx2d.calls.length = 0;
  p.update(2.3, {});
  assert.ok(!ctx2d.calls.some((c) => c[0] === 'arc'), 'early: point not drawn yet');
  ctx2d.calls.length = 0;
  p.update(10, {});
  assert.ok(ctx2d.calls.some((c) => c[0] === 'arc'), 'later: complete');
  ctx2d.calls.length = 0;
  p.update(10, {});
  assert.equal(ctx2d.calls.length, 0, 'no redraw when nothing changed');
  p.destroy();
});

test('graph panel (live): missing graph data shows a notice through update() alone', async () => {
  const rsp = { show_at: 0, panel: { kind: 'graph', title: 'Empty', graph: null } };
  const logged = await quietly(async () => {
    const p = createPanel(container(doc), rsp, { mode: 'live', timeline: timelineWith(rsp), sceneIndex: 0 });
    p.update(0.2, {});
    await flush();
    assert.match(p.el.textContent, /could not be plotted/);
    p.update(0.3, {});
    await flush();
    assert.equal(p.el.querySelectorAll('.ap-notice').length, 1, 'reported once, no retries');
    await p.ready(); // a content problem never fails a render
    p.destroy();
  });
  assert.equal(logged.length, 1);
});

test('graph panel: a broken math.js global shows a notice in live mode and fails render mode', async () => {
  const real = w.math;
  try {
    w.math = { notMathJs: true }; // loads "successfully" but is not math.js
    const rsp = { show_at: 0, panel: { kind: 'graph', graph: { functions: [{ expr: 'x' }] } } };
    await quietly(async () => {
      const live = createPanel(container(doc), rsp, { mode: 'live', timeline: timelineWith(rsp), sceneIndex: 0 });
      live.update(0.2, {});
      await flush();
      assert.match(live.el.textContent, /Graphs are unavailable right now/);
      live.destroy();
      const render = createPanel(container(doc), rsp, { mode: 'render', timeline: timelineWith(rsp), sceneIndex: 0 });
      render.update(0, {});
      await assert.rejects(render.ready(), /math\.js is not available/);
      assert.match(render.el.textContent, /Graphs are unavailable right now/);
      render.destroy();
    });
  } finally {
    w.math = real;
  }
});

test('graph panel with only invalid expressions shows a notice', async () => {
  const rsp = { show_at: 0, panel: { kind: 'graph', graph: { functions: [{ expr: 'constructor' }] } } };
  const p = createPanel(container(doc), rsp, { mode: 'render' });
  await p.ready();
  assert.match(p.el.textContent, /could not be plotted/);
  p.destroy();
});

// ---------------------------------------------------------------- 3D

const PRIMS = [
  { shape: 'sphere', position: [0, 0, 0], size: [1], color: '#ffd700', label: 'Nucleus' },
  { shape: 'box', size: [2, 1, 1] },
  { shape: 'cylinder', size: [0.5, 2] },
  { shape: 'cone', size: [0.5] },
  { shape: 'torus', size: [1.5, 0.2] },
  { shape: 'arrow', position: [1, 0, 0], size: [0, 2, 0], label: 'F' },
  { shape: 'teapot' },
  { shape: 'sphere', size: [1e9], color: 'red; background:url(x)' },
];

test('buildPrimitives builds meshes, arrows and label anchors', () => {
  const { root, labels } = buildPrimitives(THREE, PRIMS);
  assert.equal(root.children.length, 7, 'unknown shapes are skipped');
  assert.deepEqual(labels.map((l) => l.text), ['Nucleus', 'F']);
  const nucleus = labels[0].anchor;
  assert.ok(Math.abs(nucleus.y - 1.3) < 1e-9);
  const arrowTip = labels[1].anchor;
  assert.ok(Math.abs(arrowTip.x - 1) < 1e-9 && Math.abs(arrowTip.y - 2.25) < 1e-9);
  const arrow = root.children[5];
  assert.equal(arrow.children.length, 2, 'shaft + head');
  const huge = root.children[6];
  assert.ok(huge.geometry.parameters.radius <= 100, 'sizes are clamped');
  assert.equal(huge.material.color.getHexString(), new THREE.Color('#b026ff').getHexString(), 'invalid colours fall back');
});

test('fitDistance and orbitPosition', () => {
  assert.ok(fitDistance(1, 40, 0.5) > fitDistance(1, 40, 1), 'tall panels need more distance');
  assert.equal(fitDistance(2, 40, 1), 2 * fitDistance(1, 40, 1));
  const p = orbitPosition({ x: 0, y: 0, z: 0 }, 10, 0, 0);
  assert.ok(Math.abs(p.z - 10) < 1e-9 && Math.abs(p.x) < 1e-9);
  const q = orbitPosition({ x: 1, y: 1, z: 1 }, 5, RENDER_ANGLE.yaw, RENDER_ANGLE.pitch);
  assert.ok(q.y > 1 && q.x < 1, 'nice angle: from above, rotated');
});

function fakeThree({ failRenderer = false } = {}) {
  const renderers = [];
  class FakeRenderer {
    constructor(opts) {
      if (failRenderer) throw new Error('no WebGL');
      this.opts = opts;
      this.renders = 0;
      this.disposed = false;
      this.lost = false;
      renderers.push(this);
    }
    setClearColor() {}
    setPixelRatio(r) {
      this.ratio = r;
    }
    setSize(wd, ht) {
      this.size = [wd, ht];
    }
    render(scene, camera) {
      this.renders++;
      this.lastCamera = camera.position.clone();
    }
    dispose() {
      this.disposed = true;
    }
    forceContextLoss() {
      this.lost = true;
    }
  }
  return { renderers, lib: { ...THREE, WebGLRenderer: FakeRenderer } };
}

function make3d(mode, deps, spec = { primitives: PRIMS.slice(0, 6), auto_rotate: true }) {
  const box = container(doc);
  const rsp = { show_at: 0, panel: { kind: 'model_3d', title: 'Atom', model_3d: spec } };
  const chrome = createChrome({ kind: 'model_3d', title: 'Atom', mode, visible: true });
  box.appendChild(chrome.el);
  const impl = model3dFactory(deps)(chrome.body, rsp, { mode, timeline: timelineWith(rsp), sceneIndex: 0 }, chrome);
  return { impl, chrome };
}

test('3D render mode: one frame at the fixed angle with a preserved drawing buffer, full disposal', async () => {
  const { renderers, lib } = fakeThree();
  let geometryDisposals = 0;
  const original = THREE.BufferGeometry.prototype.dispose;
  THREE.BufferGeometry.prototype.dispose = function dispose() {
    geometryDisposals++;
    return original.call(this);
  };
  try {
    const { impl, chrome } = make3d('render', { loadThree: async () => lib });
    impl.update(0, {}, true);
    await impl.ready();
    assert.equal(renderers.length, 1);
    const r = renderers[0];
    assert.equal(r.opts.preserveDrawingBuffer, true);
    assert.equal(r.opts.alpha, true);
    assert.equal(r.ratio, 1);
    assert.equal(r.renders, 1);
    assert.ok(r.lastCamera.y > 0, 'camera above the model');
    assert.deepEqual(
      [...chrome.el.querySelectorAll('.ap-3d-label')].map((l) => l.textContent),
      ['Nucleus', 'F'],
    );
    assert.equal(chrome.el.querySelector('.ap-3d-hint'), null, 'no drag hint in render mode');
    await flush();
    assert.equal(r.renders, 1, 'no animation loop in render mode');
    impl.destroy();
    assert.ok(r.disposed && r.lost);
    assert.ok(geometryDisposals >= 7, `geometries disposed (${geometryDisposals})`);
    assert.equal(chrome.el.querySelector('canvas'), null);
  } finally {
    THREE.BufferGeometry.prototype.dispose = original;
  }
});

test('3D render mode: the standing hidden context-lost notice never reduces the MP4 panel to its title', async () => {
  const { titleOnlyPanels } = await import('../../js/render/render.js');
  const { lib } = fakeThree();
  const { impl, chrome } = make3d('render', { loadThree: async () => lib });
  impl.update(0, {}, true);
  await impl.ready();
  assert.ok(chrome.el.querySelector('.ap-3d-lost'), 'the notice exists, hidden');
  assert.deepEqual(titleOnlyPanels(/** @type {HTMLElement} */ (chrome.el.parentElement)), []);
  assert.equal(chrome.el.classList.contains('ap-panel--title-only'), false);
  impl.destroy();
});

test('3D live mode: auto-rotate loop, drag rotation, context loss, listener release', async () => {
  const tracker = trackListeners(w);
  try {
    const { renderers, lib } = fakeThree();
    const { impl, chrome } = make3d('live', { loadThree: async () => lib });
    impl.update(0, {}, true);
    await impl.ready();
    await new Promise((r) => setTimeout(r, 120));
    const r = renderers[0];
    assert.equal(r.opts.preserveDrawingBuffer, false);
    assert.ok(r.renders >= 2, `auto-rotate renders continuously (${r.renders})`);
    const canvas = chrome.el.querySelector('canvas');
    assert.equal(canvas.getAttribute('tabindex'), '0');
    const before = r.lastCamera.clone();
    canvas.dispatchEvent(new w.MouseEvent('pointerdown', { button: 0, clientX: 10, clientY: 10 }));
    canvas.dispatchEvent(new w.MouseEvent('pointermove', { clientX: 90, clientY: 10 }));
    canvas.dispatchEvent(new w.MouseEvent('pointerup', { clientX: 90, clientY: 10 }));
    await new Promise((r2) => setTimeout(r2, 40));
    assert.ok(r.lastCamera.distanceTo(before) > 0.01, 'drag rotates the view');

    const lost = new w.Event('webglcontextlost', { cancelable: true });
    canvas.dispatchEvent(lost);
    assert.ok(lost.defaultPrevented, 'preventDefault allows restoring the context');
    assert.equal(chrome.el.querySelector('.ap-3d-lost').hidden, false);
    const frozen = r.renders;
    await new Promise((r2) => setTimeout(r2, 60));
    assert.equal(r.renders, frozen, 'no rendering while the context is lost');
    canvas.dispatchEvent(new w.Event('webglcontextrestored'));
    assert.equal(chrome.el.querySelector('.ap-3d-lost').hidden, true);
    await new Promise((r2) => setTimeout(r2, 60));
    assert.ok(r.renders > frozen, 'rendering resumes after restore');

    impl.update(1, {}, false);
    const hiddenAt = r.renders;
    await new Promise((r2) => setTimeout(r2, 60));
    assert.equal(r.renders, hiddenAt, 'loop stops while hidden');
    impl.destroy();
    assert.ok(r.disposed && r.lost);
    assert.equal(tracker.active(), 0, 'every listener removed');
  } finally {
    tracker.restore();
  }
});

test('3D preview mode: drag only, no auto-rotate', async () => {
  const { renderers, lib } = fakeThree();
  const { impl } = make3d('preview', { loadThree: async () => lib });
  impl.update(0, {}, true);
  await impl.ready();
  await new Promise((r) => setTimeout(r, 80));
  assert.ok(renderers[0].renders <= 2, `renders on demand only (${renderers[0].renders})`);
  impl.destroy();
});

/** Silence console.error while `fn` runs (expected failure logs). */
async function quietly(fn) {
  const original = console.error;
  const logged = [];
  console.error = (...args) => logged.push(args);
  try {
    await fn();
  } finally {
    console.error = original;
  }
  return logged;
}

test('3D live mode: a three.js load failure shows a notice through update() alone (no ready())', async () => {
  let loads = 0;
  const logged = await quietly(async () => {
    const { impl, chrome } = make3d('live', {
      loadThree: () => {
        loads++;
        return Promise.reject(new Error('404 three.module.js'));
      },
    });
    impl.update(0.1, {}, true);
    await flush();
    assert.match(chrome.el.textContent, /3D models are unavailable right now/);
    assert.equal(chrome.el.querySelector('.ap-3d-hint').hidden, true, 'no "Drag to rotate" over a dead panel');
    assert.equal(chrome.el.querySelector('canvas').hidden, true);
    impl.update(1, {}, false);
    impl.update(2, {}, true);
    await flush();
    assert.equal(loads, 1, 'no retry storm when the panel is shown again');
    assert.equal(chrome.el.querySelectorAll('.ap-notice--warn').length, 1, 'one notice');
    impl.destroy();
  });
  assert.equal(logged.length, 1);
});

test('3D live mode: WebGL and scene-building failures also show a notice through update() alone', async () => {
  await quietly(async () => {
    const { lib } = fakeThree({ failRenderer: true });
    const a = make3d('live', { loadThree: async () => lib });
    a.impl.update(0, {}, true);
    await flush();
    assert.match(a.chrome.el.textContent, /not supported/);
    a.impl.destroy();

    const { lib: ok } = fakeThree();
    const broken = {
      ...ok,
      Scene: class {
        constructor() {
          throw new Error('scene boom');
        }
      },
    };
    const b = make3d('live', { loadThree: async () => broken });
    b.impl.update(0, {}, true);
    await flush();
    assert.match(b.chrome.el.textContent, /could not be displayed/);
    await b.impl.ready(); // resolves: a content failure is not fatal
    b.impl.destroy();
  });
});

test('3D failures degrade gracefully (live) and loudly when three.js is missing (render)', async () => {
  const { lib } = fakeThree({ failRenderer: true });
  const a = make3d('live', { loadThree: async () => lib });
  await quietly(() => a.impl.ready());
  assert.match(a.chrome.el.textContent, /not supported/);
  a.impl.destroy();

  const b = make3d('render', { loadThree: async () => lib }, { primitives: [] });
  await b.impl.ready();
  assert.match(b.chrome.el.textContent, /missing/);
  b.impl.destroy();

  const c = make3d('render', { loadThree: () => Promise.reject(new Error('404')) });
  await quietly(() => assert.rejects(c.impl.ready(), /404/));
  assert.match(c.chrome.el.textContent, /unavailable/);
  c.impl.destroy();
});
