// @ts-check
/**
 * Function graph panel: math.js (hardened instance, allow-listed expressions, compiled once),
 * adaptive sampling, auto y-range, grid/axes/ticks/labels/points on a DPR-aware canvas.
 * Live/preview: curves are stroked progressively after the panel appears (deterministic in t).
 * Render: static, fully drawn.
 */

import { Disposer, h } from '../../shared/dom.js';
import { loadLib } from '../../shared/libs.js';
import { autoYRange, createSafeMath, formatTick, niceTicks, sampleFunction, splitSegments } from './graphMath.js';
import { PALETTE, THEME, clamp, finiteOr, prefersReducedMotion, showNotice, stripRich } from './util.js';

/** @typedef {import('./types.js').PanelFactory} PanelFactory */
/** @typedef {import('./graphMath.js').Pt} Pt */
/** @typedef {import('./graphMath.js').SafeMath} SafeMath */

/**
 * @typedef {object} GraphFn
 * @property {string} label
 * @property {string} color
 * @property {string | null} error
 * @property {Pt[][]} segments
 */

/**
 * @typedef {object} GraphModel
 * @property {number} x0
 * @property {number} x1
 * @property {number} y0
 * @property {number} y1
 * @property {GraphFn[]} fns
 * @property {{ x: number, y: number, label: string }[]} points
 * @property {string} xLabel
 * @property {string} yLabel
 */

/** Seconds the progressive stroke takes in live mode. */
export const DRAW_SECONDS = 1.6;
const DRAW_DELAY = 0.15;

/**
 * Compile, sample and range a GraphSpec (pure given a SafeMath).
 * @param {any} spec  GraphSpec {functions[{expr,label}], points[{x,y,label}], x_range, y_range, x_label, y_label}
 * @param {SafeMath} safe
 * @returns {GraphModel}
 */
export function buildGraphModel(spec, safe) {
  const xr = spec && Array.isArray(spec.x_range) ? spec.x_range : [-5, 5];
  let x0 = finiteOr(Number(xr[0]), -5);
  let x1 = finiteOr(Number(xr[1]), 5);
  if (!(x1 > x0)) {
    x0 = -5;
    x1 = 5;
  }
  /** @type {number[]} */
  const uniform = [];
  /** @type {Array<GraphFn & { samples: ReturnType<typeof sampleFunction> | null }>} */
  const fns = [];
  const list = spec && Array.isArray(spec.functions) ? spec.functions.slice(0, 4) : [];
  list.forEach((/** @type {any} */ fn, /** @type {number} */ i) => {
    const color = PALETTE[i % PALETTE.length];
    const label = stripRich(fn && fn.label) || `y = ${String((fn && fn.expr) || '').trim()}`;
    try {
      const f = safe.compileExpr(String(fn && fn.expr));
      const samples = sampleFunction(f, x0, x1);
      if (!samples.uniform.some(Number.isFinite)) throw new Error('the expression is undefined on this range');
      uniform.push(...samples.uniform);
      fns.push({ label, color, error: null, segments: [], samples });
    } catch (e) {
      fns.push({ label, color, error: e instanceof Error ? e.message : String(e), segments: [], samples: null });
    }
  });
  const points = (spec && Array.isArray(spec.points) ? spec.points.slice(0, 20) : [])
    .map((/** @type {any} */ p) => ({ x: Number(p && p.x), y: Number(p && p.y), label: stripRich(p && p.label) }))
    .filter((/** @type {{x: number, y: number}} */ p) => Number.isFinite(p.x) && Number.isFinite(p.y));
  const yr = spec && Array.isArray(spec.y_range) ? spec.y_range.map(Number) : null;
  /** @type {[number, number]} */
  const yRange =
    yr && Number.isFinite(yr[0]) && Number.isFinite(yr[1]) && yr[1] > yr[0]
      ? [yr[0], yr[1]]
      : autoYRange(
          uniform,
          points.filter((/** @type {{x: number}} */ p) => p.x >= x0 && p.x <= x1).map((/** @type {{y: number}} */ p) => p.y),
        );
  for (const fn of fns) {
    if (fn.samples) fn.segments = splitSegments(fn.samples.points, yRange, fn.samples.minDx);
  }
  return {
    x0,
    x1,
    y0: yRange[0],
    y1: yRange[1],
    fns: fns.map(({ label, color, error, segments }) => ({ label, color, error, segments })),
    points,
    xLabel: stripRich(spec && spec.x_label),
    yLabel: stripRich(spec && spec.y_label),
  };
}

/**
 * Draw the model on a 2D context whose transform already maps CSS px.
 * @param {CanvasRenderingContext2D} g
 * @param {GraphModel} m
 * @param {{ width: number, height: number, progress: number }} opts
 */
export function drawGraph(g, m, { width, height, progress }) {
  g.clearRect(0, 0, width, height);
  const fs = clamp(Math.min(width, height) / 22, 11, 16);
  const font = `500 ${fs}px ${THEME.fontSans}`;
  g.font = font;
  const xt = niceTicks(m.x0, m.x1, clamp(Math.round(width / 90), 3, 8));
  const yt = niceTicks(m.y0, m.y1, clamp(Math.round(height / 70), 3, 8));
  const yLabels = yt.ticks.map((v) => formatTick(v, yt.step));
  const yLabelW = Math.max(0, ...yLabels.map((s) => g.measureText(s).width));
  const left = yLabelW + fs * 0.8 + (m.yLabel ? fs * 1.6 : 0) + 2;
  const top = fs * 0.8;
  const right = fs * 0.9;
  const bottom = fs * 2 + (m.xLabel ? fs * 1.5 : 0);
  const pw = width - left - right;
  const ph = height - top - bottom;
  if (pw < 20 || ph < 20) return;
  const X = (/** @type {number} */ x) => left + ((x - m.x0) / (m.x1 - m.x0)) * pw;
  const Y = (/** @type {number} */ y) => clamp(top + (1 - (y - m.y0) / (m.y1 - m.y0)) * ph, top - ph * 4, top + ph * 5);

  // grid
  g.lineWidth = 1;
  g.strokeStyle = THEME.grid;
  g.beginPath();
  for (const v of xt.ticks) {
    const x = Math.round(X(v)) + 0.5;
    g.moveTo(x, top);
    g.lineTo(x, top + ph);
  }
  for (const v of yt.ticks) {
    const y = Math.round(Y(v)) + 0.5;
    g.moveTo(left, y);
    g.lineTo(left + pw, y);
  }
  g.stroke();

  // axes (through the origin when visible, else along the frame)
  const axisY = m.y0 <= 0 && m.y1 >= 0 ? Y(0) : top + ph;
  const axisX = m.x0 <= 0 && m.x1 >= 0 ? X(0) : left;
  g.strokeStyle = THEME.axis;
  g.lineWidth = 1.5;
  g.beginPath();
  g.moveTo(left, Math.round(axisY) + 0.5);
  g.lineTo(left + pw, Math.round(axisY) + 0.5);
  g.moveTo(Math.round(axisX) + 0.5, top);
  g.lineTo(Math.round(axisX) + 0.5, top + ph);
  g.stroke();

  // tick labels
  g.fillStyle = THEME.textDim;
  g.textAlign = 'center';
  g.textBaseline = 'top';
  for (const v of xt.ticks) g.fillText(formatTick(v, xt.step), X(v), top + ph + fs * 0.45);
  g.textAlign = 'right';
  g.textBaseline = 'middle';
  yt.ticks.forEach((v, i) => g.fillText(yLabels[i], left - fs * 0.5, Y(v)));

  // axis titles
  g.fillStyle = THEME.text;
  g.font = `600 ${fs}px ${THEME.fontSans}`;
  if (m.xLabel) {
    g.textAlign = 'center';
    g.textBaseline = 'bottom';
    g.fillText(m.xLabel, left + pw / 2, height - 2);
  }
  if (m.yLabel) {
    g.save();
    g.translate(fs * 0.2, top + ph / 2);
    g.rotate(-Math.PI / 2);
    g.textAlign = 'center';
    g.textBaseline = 'top';
    g.fillText(m.yLabel, 0, 0);
    g.restore();
  }

  // curves, clipped to the plot area, stroked up to the progress cut
  const xCut = m.x0 + clamp(progress, 0, 1) * (m.x1 - m.x0);
  g.save();
  g.beginPath();
  g.rect(left, top, pw, ph);
  g.clip();
  g.lineWidth = 3;
  g.lineJoin = 'round';
  g.lineCap = 'round';
  for (const fn of m.fns) {
    if (fn.error) continue;
    g.strokeStyle = fn.color;
    for (const seg of fn.segments) {
      if (!seg.length || seg[0].x > xCut) continue;
      g.beginPath();
      g.moveTo(X(seg[0].x), Y(seg[0].y));
      for (let i = 1; i < seg.length; i++) {
        const p = seg[i];
        if (p.x > xCut) {
          const a = seg[i - 1];
          const f = (xCut - a.x) / (p.x - a.x || 1);
          g.lineTo(X(xCut), Y(a.y + (p.y - a.y) * f));
          break;
        }
        g.lineTo(X(p.x), Y(p.y));
      }
      g.stroke();
    }
  }
  g.restore();

  // points
  g.font = `600 ${fs}px ${THEME.fontSans}`;
  for (const p of m.points) {
    if (p.x < m.x0 || p.x > m.x1 || p.y < m.y0 || p.y > m.y1) continue;
    if ((p.x - m.x0) / (m.x1 - m.x0) > progress + 1e-9) continue;
    const px = X(p.x);
    const py = Y(p.y);
    g.beginPath();
    g.arc(px, py, fs * 0.38, 0, Math.PI * 2);
    g.fillStyle = THEME.gold;
    g.fill();
    g.lineWidth = 2;
    g.strokeStyle = THEME.surfaceSolid;
    g.stroke();
    if (p.label) {
      const rightSide = px < left + pw * 0.7;
      g.textAlign = rightSide ? 'left' : 'right';
      g.textBaseline = py > top + fs * 1.5 ? 'bottom' : 'top';
      g.fillStyle = THEME.text;
      g.fillText(p.label, px + (rightSide ? fs * 0.6 : -fs * 0.6), py + (g.textBaseline === 'bottom' ? -fs * 0.3 : fs * 0.3));
    }
  }
}

/** @type {PanelFactory} */
export function createGraphPanel(body, rsp, ctx) {
  const disposer = new Disposer();
  const spec = rsp.panel && rsp.panel.graph;
  const render = ctx.mode === 'render';
  const animate = !render && !prefersReducedMotion();
  const showAt = Math.max(0, finiteOr(rsp.show_at, 0));
  const wrap = h('div', { class: 'ap-graph-wrap' });
  const canvas = h('canvas', { class: 'ap-graph-canvas', role: 'img', 'aria-label': stripRich(rsp.panel && rsp.panel.title) || 'Graph' });
  const legend = h('div', { class: 'ap-graph-legend' });
  wrap.appendChild(canvas);
  body.append(wrap, legend);

  /** @type {GraphModel | null} */
  let model = null;
  let failed = false;
  let destroyed = false;
  let lastT = 0;
  let visible = false;
  let drawnKey = '';
  /** @type {Promise<void> | null} */
  let loading = null;

  const progressAt = (/** @type {number} */ t) => (animate ? clamp((t - showAt - DRAW_DELAY) / DRAW_SECONDS, 0, 1) : 1);

  const draw = (/** @type {number} */ progress) => {
    if (!model || destroyed) return;
    const cssW = Math.max(1, wrap.clientWidth || 400);
    const cssH = Math.max(1, wrap.clientHeight || 300);
    const rect = wrap.getBoundingClientRect();
    const stageScale = rect.width > 0 ? rect.width / cssW : 1;
    const ratio = clamp((typeof devicePixelRatio === 'number' ? devicePixelRatio : 1) * stageScale, 1, 3);
    const key = `${cssW}x${cssH}@${ratio.toFixed(3)}:${progress.toFixed(4)}`;
    if (key === drawnKey) return;
    const pw = Math.round(cssW * ratio);
    const phh = Math.round(cssH * ratio);
    if (canvas.width !== pw || canvas.height !== phh) {
      canvas.width = pw;
      canvas.height = phh;
    }
    canvas.style.width = `${cssW}px`;
    canvas.style.height = `${cssH}px`;
    const g = canvas.getContext('2d');
    if (!g) return;
    drawnKey = key;
    g.setTransform(ratio, 0, 0, ratio, 0, 0);
    drawGraph(g, model, { width: cssW, height: cssH, progress });
  };

  const buildLegend = (/** @type {GraphModel} */ m) => {
    for (const fn of m.fns) {
      legend.appendChild(
        h(
          'span',
          { class: ['ap-graph-key', { 'is-error': !!fn.error }], title: fn.error || '' },
          h('i', { 'aria-hidden': 'true', style: { background: fn.color } }),
          fn.error ? `${fn.label} (cannot be plotted)` : fn.label,
        ),
      );
    }
  };

  /**
   * Mark the panel failed and show one notice (the legend stays empty).
   * @param {string} message
   */
  const fail = (message) => {
    if (failed) return;
    failed = true;
    showNotice(body, message, 'warn');
  };

  /**
   * Load math.js and build the model once. Every failure is reported in the panel here, so live,
   * preview and render all show it. The promise rejects only for infrastructure failures (math.js
   * missing), which ready() turns into a failed MP4 render; bad graph data resolves with a notice.
   * @returns {Promise<void>}
   */
  const load = () => {
    if (!loading) {
      loading = (async () => {
        /** @type {SafeMath} */
        let safe;
        try {
          safe = createSafeMath(await loadLib('mathjs'));
        } catch (e) {
          console.error('graph panel: math.js failed to load', e);
          fail('Graphs are unavailable right now.');
          throw e;
        }
        if (destroyed) return;
        try {
          if (!spec) throw new Error('graph data is missing');
          model = buildGraphModel(spec, safe);
        } catch (e) {
          console.error('graph panel: cannot build the graph', e);
          model = null;
          fail('This graph could not be plotted.');
          return;
        }
        buildLegend(model);
        if (!model.points.length && model.fns.every((f) => f.error)) fail('This graph could not be plotted.');
      })();
    }
    return loading;
  };

  if (!render && typeof ResizeObserver === 'function') {
    const ro = new ResizeObserver(() => {
      if (visible) draw(progressAt(lastT));
    });
    ro.observe(wrap);
    disposer.add(() => ro.disconnect());
  }
  disposer.add(() => {
    destroyed = true;
  });

  let waiting = false;
  return {
    update(t, _state, isVisible) {
      lastT = t;
      visible = isVisible;
      if (!model) {
        if (!failed && !waiting) {
          waiting = true;
          load()
            .then(
              () => {
                if (visible || render) draw(progressAt(lastT));
              },
              () => undefined, // already reported by load()
            )
            .finally(() => {
              waiting = false;
            });
        }
        return;
      }
      if (isVisible || render) draw(progressAt(t));
    },
    async ready() {
      try {
        await load();
      } catch (e) {
        if (render) throw e;
        return;
      }
      if (render || visible) draw(render ? 1 : progressAt(lastT));
    },
    destroy: () => disposer.dispose(),
  };
}
