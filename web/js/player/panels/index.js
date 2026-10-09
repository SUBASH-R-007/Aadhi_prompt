// @ts-check
/**
 * Side panels: `createPanel(container, resolvedSidePanel, ctx)` builds the card for one scene's
 * side panel and dispatches on `panel.kind` (and the resolved media kind for media panels).
 *
 * Contract (docs/INTERFACES.md):
 *   createPanel(container, rsp, ctx) -> { el, update(t, sceneState), mediaRect?(), ready(), destroy() }
 *   ctx = { mode: 'live'|'preview'|'render', timeline, sceneIndex, conceptState: {doneIds, activeId} }
 *   - `t` is scene-relative seconds; visibility follows `sceneState.panelVisible` (fallback t >= show_at).
 *   - `sceneState.panelFocus` (the narration points at the visual: a 'focus' sync cue) adds the
 *     `is-focus` class to the card: a soft pulse in live mode, a static outline in render mode and with
 *     reduced motion (panels.css).
 *   - update() also accepts an optional third argument, the player's frame info
 *     `{ playing, rate, seeked }` (video panels follow `rate`; without it they estimate the speed).
 *   - ready() resolves once the panel's current visual state is fully drawn (images decoded,
 *     chart created, 3D frame rendered); in render mode an infrastructure failure rejects.
 *   - mediaRect() (media panels only) is the stage-pixel rect of a transparent media hole in
 *     render mode, or null while the panel is hidden.
 *   - destroy() releases listeners, timers, WebGL contexts, charts and media elements.
 */

import { createChrome, setChromeVisible } from './chrome.js';
import { createChartPanel } from './chart.js';
import { createFigurePanel } from './figure.js';
import { createGifPanel } from './gif.js';
import { createGraphPanel } from './graph.js';
import { createImagePanel } from './image.js';
import { createManimPanel } from './manimVideo.js';
import { createModel3dPanel } from './model3d.js';
import { createQuizTeaserPanel } from './quizTeaser.js';
import { createSkillTreePanel } from './skillTree.js';
import { createTerminalPanel } from './terminal.js';
import { finiteOr, normalizeMode, showNotice } from './util.js';

export { panelStateTimes, quizTeaserRevealTime, terminalLineTimes, terminalOutputLines } from './timing.js';
export { layoutSkillTree } from './skillTreeLayout.js';

/** @typedef {import('./types.js').Panel} Panel */
/** @typedef {import('./types.js').PanelCtx} PanelCtx */
/** @typedef {import('./types.js').PanelFactory} PanelFactory */
/** @typedef {import('./types.js').PanelImpl} PanelImpl */
/** @typedef {import('./types.js').ResolvedSidePanel} ResolvedSidePanel */

/** @type {Readonly<Record<string, PanelFactory>>} */
export const PANEL_FACTORIES = Object.freeze({
  skill_tree: createSkillTreePanel,
  figure: createFigurePanel,
  image: createImagePanel,
  chart: createChartPanel,
  graph: createGraphPanel,
  model_3d: createModel3dPanel,
  manim: createManimPanel,
  terminal: createTerminalPanel,
  quiz: createQuizTeaserPanel,
  gif: createGifPanel,
});

export const PANEL_KINDS = Object.freeze(Object.keys(PANEL_FACTORIES));

/** Media panels follow the resolved media kind (e.g. a video uploaded for an `image` panel). */
const MEDIA_KINDS = new Set(['figure', 'image', 'manim']);

/**
 * @param {string} kind
 * @param {ResolvedSidePanel} rsp
 * @returns {PanelFactory | null}
 */
export function factoryFor(kind, rsp) {
  if (MEDIA_KINDS.has(kind) && rsp.media) {
    if (rsp.media.kind === 'video') return createManimPanel;
    if (rsp.media.kind === 'image') return kind === 'manim' ? createImagePanel : PANEL_FACTORIES[kind];
    if (rsp.media.kind === 'gif') return createGifPanel;
  }
  return Object.prototype.hasOwnProperty.call(PANEL_FACTORIES, kind) ? PANEL_FACTORIES[kind] : null;
}

/**
 * Create the side panel for one scene.
 * @param {HTMLElement} container         the side-panel rect (the card fills it)
 * @param {ResolvedSidePanel} resolvedSidePanel
 * @param {PanelCtx} ctx
 * @returns {Panel}
 */
export function createPanel(container, resolvedSidePanel, ctx) {
  if (!container || typeof container.appendChild !== 'function') throw new TypeError('createPanel: container element required');
  const panelSpec = resolvedSidePanel && resolvedSidePanel.panel;
  if (!panelSpec || typeof panelSpec.kind !== 'string') throw new TypeError('createPanel: resolvedSidePanel.panel.kind required');
  const mode = normalizeMode(ctx && ctx.mode);
  const fullCtx = { ...(ctx || {}), mode };
  const rsp = { ...resolvedSidePanel, show_at: Math.max(0, finiteOr(resolvedSidePanel.show_at, 0)) };
  const kind = panelSpec.kind;
  let visible = rsp.show_at <= 0;
  const chrome = createChrome({ kind, title: panelSpec.title, mode, visible });
  container.appendChild(chrome.el);

  /** @type {PanelImpl} */
  let impl;
  const factory = factoryFor(kind, rsp);
  try {
    if (!factory) throw new Error(`unsupported side panel kind "${kind}"`);
    impl = factory(chrome.body, rsp, fullCtx, chrome);
  } catch (e) {
    console.error('side panel:', e);
    showNotice(chrome.body, 'This panel could not be displayed.', 'warn');
    impl = { destroy() {} };
  }

  let destroyed = false;
  let focused = false;
  /** @type {Panel} */
  const panel = {
    el: chrome.el,
    update(t, sceneState, frame) {
      if (destroyed) return;
      const s = sceneState || {};
      const time = finiteOr(t, 0);
      const nextVisible = typeof s.panelVisible === 'boolean' ? s.panelVisible : time + 1e-6 >= rsp.show_at;
      if (nextVisible !== visible) {
        visible = nextVisible;
        setChromeVisible(chrome, visible);
      }
      const nextFocused = visible && s.panelFocus === true;
      if (nextFocused !== focused) {
        focused = nextFocused;
        chrome.el.classList.toggle('is-focus', focused);
      }
      if (impl.update) impl.update(time, s, visible, frame && typeof frame === 'object' ? frame : null);
    },
    ready() {
      if (destroyed) return Promise.resolve();
      return impl.ready ? impl.ready() : Promise.resolve();
    },
    destroy() {
      if (destroyed) return;
      destroyed = true;
      try {
        impl.destroy();
      } finally {
        chrome.el.remove();
      }
    },
  };
  if (impl.mediaRect) {
    const rect = impl.mediaRect;
    panel.mediaRect = () => (destroyed || !visible ? null : rect());
  }
  return panel;
}
