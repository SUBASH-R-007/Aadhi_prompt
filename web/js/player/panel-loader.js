// @ts-check
/**
 * Loads the side-panel factory (web/js/player/panels/index.js, owned by the panels module) with a
 * dynamic import, falling back to an empty panel if it is missing or fails to load, and normalises
 * the instances it returns so the player can rely on the full interface.
 */

import { h } from '../shared/dom.js';

/** @typedef {import('../shared/types.js').ResolvedSidePanel} ResolvedSidePanel */
/** @typedef {import('../shared/types.js').PanelContext} PanelContext */
/** @typedef {import('../shared/types.js').PanelInstance} PanelInstance */
/** @typedef {(container: HTMLElement, panel: ResolvedSidePanel, ctx: PanelContext) => PanelInstance} PanelFactory */

/** @typedef {(scene: import('../shared/types.js').TimedScene) => number[]} PanelTimesFn */

/**
 * @typedef {object} PanelModule
 * @property {PanelFactory} createPanel
 * @property {PanelTimesFn | null} panelStateTimes   panel-internal state change times (render mode)
 */

/** @type {Promise<PanelModule> | null} */
let modulePromise = null;

/**
 * Empty panel used when the panels module is unavailable or a panel fails to build.
 * @type {PanelFactory}
 */
export function emptyPanel(container) {
  const el = h('div', { class: 'ap-panel ap-panel--empty' });
  container.appendChild(el);
  container.classList.add('is-empty');
  return {
    el,
    update() {},
    destroy() {
      el.remove();
    },
  };
}

/**
 * Resolve the panels module once (dynamic import so the player works without it).
 * @param {{ createPanel?: PanelFactory, panelStateTimes?: PanelTimesFn | null }} [override]
 *   injected implementation (tests, embedding); skips the import
 * @returns {Promise<PanelModule>}
 */
export function loadPanelModule(override) {
  if (override && override.createPanel) {
    return Promise.resolve({ createPanel: override.createPanel, panelStateTimes: override.panelStateTimes || null });
  }
  if (!modulePromise) {
    const spec = './panels/index.js'; // non-literal: optional module, resolved at runtime
    modulePromise = import(/* @vite-ignore */ spec).then(
      (mod) => ({
        createPanel: mod && typeof mod.createPanel === 'function' ? /** @type {PanelFactory} */ (mod.createPanel) : emptyPanel,
        panelStateTimes: mod && typeof mod.panelStateTimes === 'function' ? /** @type {PanelTimesFn} */ (mod.panelStateTimes) : null,
      }),
      (err) => {
        console.warn('side panels unavailable, using empty panels', err);
        return { createPanel: emptyPanel, panelStateTimes: null };
      },
    );
  }
  return modulePromise;
}

/**
 * Resolve just the panel factory.
 * @param {PanelFactory} [override]
 * @returns {Promise<PanelFactory>}
 */
export async function loadPanelFactory(override) {
  return (await loadPanelModule(override ? { createPanel: override } : undefined)).createPanel;
}

/**
 * Build a panel safely: never throws, always returns a complete instance attached to `container`.
 * @param {PanelFactory} factory
 * @param {HTMLElement} container
 * @param {ResolvedSidePanel} panel
 * @param {PanelContext} ctx
 * @param {(err: unknown) => void} [onError]
 * @returns {Required<Pick<PanelInstance, 'el' | 'update' | 'destroy'>> & PanelInstance}
 */
export function buildPanel(factory, container, panel, ctx, onError) {
  /** @type {PanelInstance} */
  let inst;
  try {
    inst = factory(container, panel, ctx);
    if (!inst || !(inst.el instanceof HTMLElement)) throw new Error('createPanel returned no element');
  } catch (err) {
    if (onError) onError(err);
    container.replaceChildren();
    inst = emptyPanel(container, panel, ctx);
  }
  if (!container.contains(inst.el)) container.appendChild(inst.el);
  const raw = inst;
  return {
    el: raw.el,
    update: (t, state) => {
      try {
        raw.update(t, state);
      } catch (err) {
        if (onError) onError(err);
      }
    },
    mediaRect: typeof raw.mediaRect === 'function' ? () => /** @type {any} */ (raw.mediaRect)() : undefined,
    ready: typeof raw.ready === 'function' ? () => Promise.resolve(/** @type {any} */ (raw.ready)()) : () => Promise.resolve(),
    destroy: () => {
      try {
        raw.destroy();
      } catch (err) {
        if (onError) onError(err);
      }
      if (raw.el.isConnected) raw.el.remove();
    },
  };
}
