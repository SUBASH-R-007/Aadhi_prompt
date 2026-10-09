// @ts-check
/**
 * Live previews for board items in the inspector. Uses the player's rich-lite renderer
 * (web/js/player/richtext.js renderRich, loaded lazily) so the preview matches playback; falls
 * back to the Studio tokenizer when the player module is unavailable. Formulas render through
 * shared/libs.js renderTex (locked-down MathJax). Never uses innerHTML.
 */

import { h, clear, append } from '../../../shared/dom.js';
import { renderTex } from '../../../shared/libs.js';
import { tokenizeRichLite } from '../../lib/richLite.js';

/** @type {Promise<((text: string, opts?: any) => DocumentFragment) | null> | null} */
let richLoader = null;

/** Lazily import the player's renderRich (non-literal specifier: written by another module). */
function loadRenderRich() {
  if (!richLoader) {
    const url = new URL('../../../player/richtext.js', import.meta.url).href;
    richLoader = import(/* @vite-ignore */ url)
      .then((m) => (m && typeof m.renderRich === 'function' ? m.renderRich : null))
      .catch(() => null);
  }
  return richLoader;
}

/**
 * Fallback renderer for rich-lite text.
 * @param {string} text
 * @returns {DocumentFragment}
 */
export function renderRichFallback(text) {
  const frag = document.createDocumentFragment();
  for (const t of tokenizeRichLite(text)) {
    switch (t.type) {
      case 'bold':
        frag.appendChild(h('strong', {}, t.text));
        break;
      case 'italic':
        frag.appendChild(h('em', {}, t.text));
        break;
      case 'code':
        frag.appendChild(h('code', {}, t.text));
        break;
      case 'keyword':
        frag.appendChild(h('span', { class: 'rich-keyword' }, t.text));
        break;
      case 'math': {
        const span = h('span', { class: 'rich-math', title: t.text }, t.text);
        renderTex(t.text, false)
          .then((node) => span.replaceChildren(node))
          .catch(() => span.classList.add('tex-error'));
        frag.appendChild(span);
        break;
      }
      default:
        frag.appendChild(document.createTextNode(t.text));
    }
  }
  return frag;
}

/**
 * Fill `host` with the rendered rich-lite `text` (async upgrade to the player renderer).
 * @param {HTMLElement} host
 * @param {string} text
 */
export function renderRichInto(host, text) {
  const value = String(text || '');
  host.replaceChildren(renderRichFallback(value));
  void loadRenderRich().then((render) => {
    if (!render || host.dataset.richSource !== value) return;
    try {
      host.replaceChildren(render(value));
    } catch {
      /* keep the fallback */
    }
  });
  host.dataset.richSource = value;
}

/**
 * @param {string} latex
 * @param {boolean} display
 */
export function texPreview(latex, display = true) {
  const host = h('span', { class: ['tex-preview', display ? 'display' : ''] }, latex);
  if (latex && latex.trim()) {
    renderTex(latex, display)
      .then((node) => host.replaceChildren(node))
      .catch(() => {
        host.classList.add('tex-error');
        host.title = 'This LaTeX could not be rendered.';
      });
  }
  return host;
}

/**
 * Preview of a board item as it will look on the board (approximation of the player style).
 * @param {Record<string, any>} item
 * @param {Record<string, any> | null} sp
 * @returns {HTMLElement}
 */
export function boardItemPreview(item, sp) {
  const host = h('div', { class: ['item-preview', `kind-${item.kind}`], 'aria-label': 'Preview' });
  updateBoardItemPreview(host, item, sp);
  return host;
}

/**
 * @param {HTMLElement} host
 * @param {Record<string, any>} item
 * @param {Record<string, any> | null} sp
 */
export function updateBoardItemPreview(host, item, sp) {
  clear(host);
  /** @param {string} text @param {string} [cls] */
  const rich = (text, cls = 'rich') => {
    const el = h('div', { class: cls });
    renderRichInto(el, text);
    return el;
  };
  switch (item.kind) {
    case 'heading':
      host.appendChild(rich(item.text, 'rich preview-heading'));
      break;
    case 'definition':
      append(host, [h('div', { class: 'preview-term' }, item.term || ''), item.text ? rich(item.text) : null]);
      break;
    case 'formula':
      append(host, [texPreview(item.latex || '', true), item.text ? rich(item.text, 'rich muted') : null]);
      if (Array.isArray(item.variables) && item.variables.length) {
        host.appendChild(
          h(
            'ul',
            { class: 'preview-vars' },
            item.variables.map((/** @type {any} */ v) => h('li', {}, texPreview(v.symbol_latex || '', false), ` — ${v.meaning || ''}${v.unit ? ` (${v.unit})` : ''}`)),
          ),
        );
      }
      break;
    case 'code':
      host.appendChild(h('pre', { class: 'mono preview-code' }, h('code', {}, item.code || '')));
      break;
    case 'table':
      host.appendChild(
        h(
          'table',
          { class: 'table compact' },
          h('thead', {}, h('tr', {}, (item.headers || []).map((/** @type {string} */ c) => h('th', {}, rich(c, 'rich'))))),
          h('tbody', {}, (item.rows || []).map((/** @type {string[]} */ r) => h('tr', {}, r.map((c) => h('td', {}, rich(c, 'rich')))))),
        ),
      );
      break;
    case 'figure': {
      const fig = sp && Array.isArray(sp.figures) ? sp.figures.find((/** @type {any} */ f) => f.id === item.figure_id) : null;
      append(host, [h('div', { class: 'preview-figure' }, `Figure: ${fig ? fig.caption || fig.id : item.figure_id || 'none'}`), item.caption ? rich(item.caption, 'rich muted') : null]);
      break;
    }
    case 'example_step':
      append(host, [item.blank ? h('div', { class: 'preview-blank', title: 'Blank until a beat fills it' }, '______ ', h('span', { class: 'muted' }, '(blank)')) : null, rich(item.text), item.justification ? rich(item.justification, 'rich muted') : null]);
      break;
    case 'misconception':
      append(host, [h('div', { class: 'preview-label bad' }, 'Misconception'), rich(item.text), item.justification ? h('div', { class: 'preview-label ok' }, 'Correction') : null, item.justification ? rich(item.justification) : null]);
      break;
    default:
      host.appendChild(rich(item.text || ''));
  }
}
