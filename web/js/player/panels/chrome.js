// @ts-check
/**
 * Shared panel chrome: a glass card with a kicker, a title and a flexible body that fills the
 * side-panel rect. Text is always inserted with textContent (shared/dom.js).
 */

import { h } from '../../shared/dom.js';
import { stripRich } from './util.js';

/** @typedef {import('./types.js').Chrome} Chrome */
/** @typedef {import('./types.js').PanelMode} PanelMode */

/** Default titles and kicker labels per panel kind. */
export const KIND_LABELS = Object.freeze({
  skill_tree: { kicker: 'Learning path', title: 'Skill Tree' },
  figure: { kicker: 'Figure', title: 'Figure' },
  image: { kicker: 'Illustration', title: 'Illustration' },
  chart: { kicker: 'Data', title: 'Chart' },
  graph: { kicker: 'Graph', title: 'Graph' },
  model_3d: { kicker: '3D model', title: '3D Model' },
  manim: { kicker: 'Animation', title: 'Animation' },
  terminal: { kicker: 'Terminal', title: 'Terminal' },
  quiz: { kicker: 'Quick check', title: 'Think about it' },
  gif: { kicker: 'GIF', title: 'GIF' },
});

/**
 * @param {string} kind
 * @returns {{ kicker: string, title: string }}
 */
export function kindLabels(kind) {
  return /** @type {Record<string, { kicker: string, title: string }>} */ (KIND_LABELS)[kind] || {
    kicker: 'Panel',
    title: 'Panel',
  };
}

/**
 * Build the card.
 * @param {{ kind: string, title?: string | null, mode: PanelMode, visible: boolean }} opts
 * @returns {Chrome}
 */
export function createChrome({ kind, title, mode, visible }) {
  const labels = kindLabels(kind);
  const text = stripRich(title) || labels.title;
  const titleEl = h('h3', { class: 'ap-title', text });
  const head = h(
    'header',
    { class: 'ap-head' },
    h('span', { class: 'ap-kicker', text: labels.kicker }),
    titleEl,
  );
  const body = h('div', { class: 'ap-body' });
  const el = h(
    'section',
    {
      class: ['ap-panel', `ap-panel--${kind.replace(/[^a-z0-9_]/gi, '')}`],
      dataset: { mode, kind, visible: visible ? 'true' : 'false' },
      'aria-label': text,
      'aria-hidden': visible ? 'false' : 'true',
    },
    h('div', { class: 'ap-backdrop', 'aria-hidden': 'true' }),
    head,
    body,
  );
  return { el, head, titleEl, body };
}

/**
 * Toggle visibility (CSS handles the fade in live mode; render mode switches instantly).
 * @param {Chrome} chrome
 * @param {boolean} visible
 */
export function setChromeVisible(chrome, visible) {
  chrome.el.dataset.visible = visible ? 'true' : 'false';
  chrome.el.setAttribute('aria-hidden', visible ? 'false' : 'true');
}
