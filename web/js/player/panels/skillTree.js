// @ts-check
/**
 * Skill tree panel: the lecture's concept map as a layered DAG (see skillTreeLayout.js) with
 * done / active / todo states and distinct prerequisite styling. Static per scene; re-laid out
 * when the panel is resized (live/preview). When the whole map cannot be shown with legible labels
 * (rendered font < MIN_LEGIBLE_FONT) the panel shows the neighbourhood of the current concept
 * instead ("Showing N of M concepts").
 */

import { Disposer, clear, h, svg } from '../../shared/dom.js';
import { MAX_UPSCALE, focusConcepts, layoutSkillTree } from './skillTreeLayout.js';
import { showNotice, stripRich } from './util.js';

export { layoutSkillTree } from './skillTreeLayout.js';

/** @typedef {import('./types.js').PanelFactory} PanelFactory */
/** @typedef {import('./types.js').PanelCtx} PanelCtx */
/** @typedef {import('./skillTreeLayout.js').SkillTreeLayout} SkillTreeLayout */

/** Labels rendered smaller than this (CSS px, after the SVG scales the layout) are not legible. */
export const MIN_LEGIBLE_FONT = 11;
const DEFAULT_SIZE = { width: 440, height: 640 };

/**
 * Concept state from ctx, or derived from the timeline (scenes before this one are done).
 * @param {PanelCtx} ctx
 * @returns {{ doneIds: Set<string>, activeId: string | null }}
 */
export function resolveConceptState(ctx) {
  const cs = ctx && ctx.conceptState;
  if (cs && (cs.doneIds || cs.activeId)) {
    return { doneIds: new Set(cs.doneIds ? Array.from(cs.doneIds) : []), activeId: cs.activeId || null };
  }
  return deriveConceptState(ctx && ctx.timeline, ctx && ctx.sceneIndex);
}

/**
 * @param {any} timeline
 * @param {number | undefined} sceneIndex
 * @returns {{ doneIds: Set<string>, activeId: string | null }}
 */
export function deriveConceptState(timeline, sceneIndex) {
  const scenes = timeline && Array.isArray(timeline.scenes) ? timeline.scenes : [];
  const idx = typeof sceneIndex === 'number' ? sceneIndex : -1;
  const current = idx >= 0 && scenes[idx] ? scenes[idx].concept_id || null : null;
  /** @type {Set<string>} */
  const doneIds = new Set();
  for (let i = 0; i < Math.min(idx, scenes.length); i++) {
    const c = scenes[i] && scenes[i].concept_id;
    if (c && c !== current) doneIds.add(c);
  }
  return { doneIds, activeId: current };
}

/**
 * @param {string} id
 * @param {{ doneIds: Set<string>, activeId: string | null }} state
 * @returns {'done' | 'active' | 'todo'}
 */
export function nodeState(id, state) {
  if (state.activeId === id) return 'active';
  return state.doneIds.has(id) ? 'done' : 'todo';
}

/**
 * Concept a focused view is centred on: the active one, else the first concept still to learn
 * whose prerequisites are all done, else the last done one, else the first.
 * @param {{ id: string, depends_on?: string[] }[]} concepts
 * @param {{ doneIds: Set<string>, activeId: string | null }} state
 * @returns {string | null}
 */
export function focusAnchor(concepts, state) {
  const ids = new Set(concepts.map((c) => c.id));
  if (state.activeId && ids.has(state.activeId)) return state.activeId;
  const next = concepts.find(
    (c) => !state.doneIds.has(c.id) && (c.depends_on || []).every((d) => state.doneIds.has(d) || !ids.has(d)),
  );
  if (next) return next.id;
  const done = concepts.filter((c) => state.doneIds.has(c.id));
  if (done.length) return done[done.length - 1].id;
  return concepts.length ? concepts[0].id : null;
}

/**
 * The concepts to draw and their layout for a box: the whole map when its labels are legible,
 * otherwise the neighbourhood of focusAnchor() (when that is more legible).
 * @param {any[]} all
 * @param {{ doneIds: Set<string>, activeId: string | null }} state
 * @param {{ width: number, height: number }} size
 * @returns {{ concepts: any[], layout: SkillTreeLayout }}
 */
export function chooseView(all, state, size) {
  const layout = layoutSkillTree(all, size);
  if (layout.renderedFont >= MIN_LEGIBLE_FONT) return { concepts: all, layout };
  const anchor = focusAnchor(all, state);
  const focused = anchor ? focusConcepts(all, anchor) : all;
  if (focused.length >= all.length) return { concepts: all, layout };
  const focusedLayout = layoutSkillTree(focused, size);
  return focusedLayout.renderedFont > layout.renderedFont ? { concepts: focused, layout: focusedLayout } : { concepts: all, layout };
}

/** @type {PanelFactory} */
export function createSkillTreePanel(body, _rsp, ctx) {
  const disposer = new Disposer();
  const state = resolveConceptState(ctx);
  const all = (ctx.timeline && Array.isArray(ctx.timeline.concept_map) ? ctx.timeline.concept_map : []).map(
    (/** @type {any} */ c) => ({ ...c, title: stripRich(c && c.title) }),
  );
  const canvas = h('div', { class: 'ap-tree-canvas' });
  const legend = h('div', { class: 'ap-tree-legend' });
  body.append(canvas, legend);

  if (!all.length) {
    showNotice(canvas, 'The concept map for this lecture is not available.');
    return { destroy: () => disposer.dispose() };
  }

  /** @type {{ width: number, height: number } | null} */
  let lastSize = null;
  const render = () => {
    const size = measure(canvas);
    if (lastSize && Math.abs(lastSize.width - size.width) < 2 && Math.abs(lastSize.height - size.height) < 2) return;
    lastSize = size;
    const view = chooseView(all, state, size);
    clear(canvas);
    canvas.appendChild(drawTree(view.layout, state));
    clear(legend);
    buildLegend(legend, view.concepts.length < all.length ? { shown: view.concepts.length, total: all.length } : null);
  };
  render();

  if (ctx.mode !== 'render' && typeof ResizeObserver === 'function') {
    let pending = 0;
    const ro = new ResizeObserver(() => {
      if (pending) return;
      pending = requestAnimationFrame(() => {
        pending = 0;
        render();
      });
    });
    ro.observe(canvas);
    disposer.add(() => {
      ro.disconnect();
      if (pending) cancelAnimationFrame(pending);
    });
  }

  return {
    ready: async () => {
      render(); // final layout once fonts/CSS are applied
    },
    destroy: () => disposer.dispose(),
  };
}

/**
 * @param {HTMLElement} el
 * @returns {{ width: number, height: number }}
 */
function measure(el) {
  const width = el.clientWidth;
  const height = el.clientHeight;
  return width > 40 && height > 40 ? { width, height } : { ...DEFAULT_SIZE };
}

/**
 * @param {HTMLElement} legend
 * @param {{ shown: number, total: number } | null} focus
 */
function buildLegend(legend, focus) {
  const item = (/** @type {string} */ cls, /** @type {string} */ label) =>
    h('span', { class: ['ap-tree-key', cls] }, h('i', { 'aria-hidden': 'true' }), label);
  legend.append(
    item('is-done', 'Done'),
    item('is-active', 'Now'),
    item('is-todo', 'Next'),
    item('is-prereq', 'Prerequisite'),
  );
  if (focus) legend.append(h('span', { class: 'ap-tree-focus', text: `Showing ${focus.shown} of ${focus.total} concepts` }));
}

/**
 * Render a computed layout as SVG.
 * @param {SkillTreeLayout} layout
 * @param {{ doneIds: Set<string>, activeId: string | null }} state
 * @returns {SVGElement}
 */
export function drawTree(layout, state) {
  const stateOf = new Map(layout.nodes.map((n) => [n.id, nodeState(n.id, state)]));
  const kindOf = new Map(layout.nodes.map((n) => [n.id, n.kind]));
  const arrow = Math.max(5, layout.fontSize * 0.45);
  const lr = layout.orientation === 'lr';
  const edges = svg('g', { class: 'ap-tree-edges' });
  for (const e of layout.edges) {
    const to = stateOf.get(e.to);
    const from = stateOf.get(e.from);
    const edgeState = to === 'active' ? 'active' : from !== 'todo' && to !== 'todo' ? 'done' : 'todo';
    const end = e.points[e.points.length - 1];
    edges.append(
      svg('path', {
        class: 'ap-tree-edge',
        d: e.d,
        dataset: { state: edgeState, prereq: kindOf.get(e.from) === 'prerequisite' ? 'true' : 'false' },
      }),
      svg('path', {
        class: 'ap-tree-arrow',
        d: lr
          ? `M${end.x - arrow} ${end.y - arrow * 0.75} L${end.x - arrow} ${end.y + arrow * 0.75} L${end.x} ${end.y} Z`
          : `M${end.x - arrow * 0.75} ${end.y - arrow} L${end.x + arrow * 0.75} ${end.y - arrow} L${end.x} ${end.y} Z`,
        dataset: { state: edgeState },
      }),
    );
  }
  const nodes = svg('g', { class: 'ap-tree-nodes' });
  for (const n of layout.nodes) {
    const st = /** @type {string} */ (stateOf.get(n.id));
    const left = n.x - n.w / 2;
    const top = n.y - n.h / 2;
    const text = svg('text', {
      class: 'ap-tree-label',
      x: n.x,
      'font-size': layout.fontSize,
      'text-anchor': 'middle',
    });
    const firstBaseline = n.y - ((n.lines.length - 1) * layout.lineHeight) / 2 + layout.fontSize * 0.35;
    n.lines.forEach((line, i) => {
      text.appendChild(svg('tspan', { x: n.x, y: Math.round((firstBaseline + i * layout.lineHeight) * 10) / 10, text: line }));
    });
    const group = svg(
      'g',
      { class: 'ap-tree-node', dataset: { state: st, kind: n.kind, concept: n.id } },
      svg('title', { text: n.title }),
      svg('rect', { class: 'ap-tree-box', x: left, y: top, width: n.w, height: n.h, rx: Math.min(12, n.h / 3) }),
      text,
    );
    if (st === 'done') group.appendChild(doneBadge(left + n.w, top, layout.fontSize));
    if (n.kind === 'prerequisite') group.appendChild(prereqTag(left, top, layout.fontSize));
    nodes.appendChild(group);
  }
  const root = svg(
    'svg',
    {
      class: 'ap-tree-svg',
      role: 'img',
      'aria-label': `Concept map with ${layout.nodes.length} concepts`,
      dataset: { orientation: layout.orientation || 'tb' },
      // A small map is enlarged at most MAX_UPSCALE times (the layout's `scale` assumes this cap).
      style: { maxWidth: `${Math.ceil(layout.width * MAX_UPSCALE)}px`, maxHeight: `${Math.ceil(layout.height * MAX_UPSCALE)}px` },
    },
    edges,
    nodes,
  );
  // shared/dom.js lower-cases attribute names; SVG attributes are case-sensitive.
  root.setAttribute('viewBox', `0 0 ${layout.width} ${layout.height}`);
  root.setAttribute('preserveAspectRatio', 'xMidYMid meet');
  return root;
}

/**
 * Small gold check badge on the top-right corner of a done node (drawn, no font glyphs).
 * @param {number} x
 * @param {number} y
 * @param {number} fs
 */
function doneBadge(x, y, fs) {
  const r = Math.max(6, fs * 0.55);
  return svg(
    'g',
    { class: 'ap-tree-badge', transform: `translate(${x - r * 0.4} ${y + r * 0.4})` },
    svg('circle', { r }),
    svg('path', { d: `M${-r * 0.45} 0 L${-r * 0.1} ${r * 0.35} L${r * 0.5} ${-r * 0.35}` }),
  );
}

/**
 * Corner tag marking a prerequisite concept.
 * @param {number} x
 * @param {number} y
 * @param {number} fs
 */
function prereqTag(x, y, fs) {
  const s = Math.max(6, fs * 0.6);
  return svg('path', { class: 'ap-tree-prereq-tag', d: `M${x} ${y + s * 1.6} L${x} ${y + 6} Q${x} ${y} ${x + 6} ${y} L${x + s * 1.6} ${y} Z` });
}
