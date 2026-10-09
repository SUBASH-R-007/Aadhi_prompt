// @ts-check
/**
 * Board renderer: turns TimedScene.board (typed BoardItems) into DOM built with textContent /
 * createElement only, and applies the per-time state from schedule.sceneStateAt():
 *   is-visible (revealed), is-filled (blank example step filled), is-highlight (signalling),
 *   is-active (item narrated by the current beat; gold "narration-active" glow).
 * Word-anchored parts (TimedScene.sync_cues): a formula legend row is is-unsaid (hidden, its space
 * kept) until the narration names it; a named table column or definition term is is-emph.
 * Items keep their list-order slot while hidden, so the layout never jumps when items appear, and the
 * board is fitted once for all items (deterministic in render mode).
 * Model ids are only ever written to data-item attributes, never to DOM ids.
 */

import { h } from '../shared/dom.js';
import { loadPrism as libLoadPrism } from '../shared/libs.js';
import { icon } from './icons.js';
import { mountTex, plainText, renderRich } from './richtext.js';

/** @typedef {import('../shared/types.js').TimedScene} TimedScene */
/** @typedef {import('../shared/types.js').BoardItem} BoardItem */
/** @typedef {import('../shared/types.js').SceneState} SceneState */
/** @typedef {import('./richtext.js').TexRenderer} TexRenderer */

/** UI labels (kept together for future localisation). */
export const LABELS = Object.freeze({
  info: 'Info',
  tip: 'Tip',
  warning: 'Warning',
  misconception: 'Misconception',
  correction: 'Actually',
  takeaway: 'Key takeaway',
  step: 'Step',
  blank: '______',
  figureMissing: 'Figure unavailable',
  example: 'Worked example',
  summary: 'Summary',
  key_takeaway: 'Key takeaways',
  recap: 'Recap',
});

/** Scene-type badges shown above the board title. */
const BADGES = /** @type {Record<string, string>} */ ({
  example: LABELS.example,
  summary: LABELS.summary,
  key_takeaway: LABELS.key_takeaway,
  recap: LABELS.recap,
});

/** Prism grammar names for code languages (others render as plain text). */
const PRISM_LANGS = /** @type {Record<string, string>} */ ({
  python: 'python', py: 'python', c: 'c', cpp: 'cpp', 'c++': 'cpp', java: 'java', javascript: 'javascript',
  js: 'javascript', bash: 'bash', sh: 'bash', shell: 'bash', sql: 'sql', matlab: 'matlab', verilog: 'verilog',
});

/** Smallest font scale used when fitting a crowded board (below this the body scrolls). */
export const MIN_FIT = 0.62;

/** @type {ReadonlySet<string>} */
const NO_PARTS = new Set();

/**
 * Case/space/punctuation-insensitive form of a label (badge vs title comparison).
 * @param {string | null | undefined} text
 */
function normalizeLabel(text) {
  return String(text || '')
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, '');
}

/**
 * Prism grammar for a BoardItem.language, or null for plain text.
 * @param {string | null | undefined} language
 * @returns {string | null}
 */
export function prismLanguage(language) {
  const key = String(language || '').trim().toLowerCase();
  return Object.prototype.hasOwnProperty.call(PRISM_LANGS, key) ? PRISM_LANGS[key] : null;
}

/**
 * @typedef {object} BoardDeps
 * @property {import('../shared/types.js').PlayerMode} mode
 * @property {TexRenderer} [renderTex]
 * @property {(language: string) => Promise<any>} [loadPrism]
 * @property {boolean} [header]   render the title header (default true)
 */

export class BoardView {
  /**
   * @param {TimedScene} scene
   * @param {BoardDeps} deps
   */
  constructor(scene, deps) {
    this.scene = scene;
    this.deps = deps;
    /** @type {Promise<unknown>[]} */
    this.pending = [];
    /** @type {HTMLImageElement[]} */
    this.images = [];
    /** @type {Map<string, HTMLElement>} */
    this.items = new Map();
    this.destroyed = false;
    this.fitScale = 1;
    this._signature = '';
    /** @type {SceneState | null} */
    this._state = null;
    this._stepNo = 0;
    /** @type {Map<string, HTMLElement[]>} `${itemId}|var:<n>` / `|column:<n>` / `|term` -> its elements */
    this.parts = new Map();
    /** @type {string} */
    this._building = '';

    const type = String(scene.type || 'content');
    /** @type {HTMLDivElement} */
    this.body = h('div', { class: 'ap-board-body', role: 'list' });
    for (const item of scene.board || []) {
      this._building = item.id;
      const el = this.renderItem(item);
      el.dataset.item = item.id;
      el.dataset.kind = item.kind;
      el.setAttribute('role', 'listitem');
      el.setAttribute('aria-hidden', 'true');
      this.items.set(item.id, el);
      this.body.appendChild(el);
    }
    // The type badge ("Worked example") is dropped when the title already says the same thing.
    const typeBadge = BADGES[type];
    const badge = typeBadge && normalizeLabel(typeBadge) !== normalizeLabel(scene.title) ? typeBadge : undefined;
    const header =
      deps.header === false || !(scene.title || scene.subtitle || badge)
        ? null
        : h(
            'header',
            { class: 'ap-board-head' },
            badge ? h('div', { class: 'ap-badge', text: badge }) : null,
            scene.title ? h('h2', { class: 'ap-board-title', text: scene.title }) : null,
            scene.subtitle ? h('p', { class: 'ap-board-subtitle', text: scene.subtitle }) : null,
          );
    /** @type {HTMLDivElement} */
    this.el = h(
      'div',
      {
        class: ['ap-board', `ap-board--${type.replace(/[^a-z0-9_-]/gi, '')}`, { 'is-empty': this.items.size === 0 }],
        role: 'region',
        'aria-label': scene.title || 'Board',
      },
      header,
      this.items.size ? this.body : null,
    );
  }

  /**
   * Rich-lite text -> fragment, collecting TeX promises.
   * @param {string | null | undefined} text
   */
  rich(text) {
    return renderRich(text, { renderTex: this.deps.renderTex, pending: this.pending });
  }

  /**
   * Register the element(s) of a part of the item being built (word-anchored sync).
   * @param {string} part   'var:<n>' | 'column:<n>' | 'term'
   * @param {HTMLElement} el
   */
  part(part, el) {
    const key = `${this._building}|${part}`;
    const list = this.parts.get(key);
    if (list) list.push(el);
    else this.parts.set(key, [el]);
    return el;
  }

  /**
   * Build the element of one board item.
   * @param {BoardItem} item
   * @returns {HTMLElement}
   */
  renderItem(item) {
    const kind = item.kind;
    switch (kind) {
      case 'heading':
        return h('h3', { class: 'bi bi-heading' }, this.rich(item.text));
      case 'bullet':
        return h('div', { class: 'bi bi-bullet' }, h('span', { class: 'bi-dot', 'aria-hidden': 'true' }), h('span', { class: 'bi-text' }, this.rich(item.text)));
      case 'paragraph':
        return h('p', { class: 'bi bi-paragraph' }, this.rich(item.text));
      case 'definition':
        return h(
          'div',
          { class: 'bi bi-definition' },
          item.term ? this.part('term', h('span', { class: 'bi-term' }, this.rich(item.term))) : null,
          item.text ? h('span', { class: 'bi-def-text' }, this.rich(item.text)) : null,
        );
      case 'formula':
        return this.renderFormula(item);
      case 'callout_info':
      case 'callout_tip':
      case 'callout_warning': {
        const tone = kind.slice('callout_'.length);
        const label = tone === 'info' ? LABELS.info : tone === 'tip' ? LABELS.tip : LABELS.warning;
        return h(
          'div',
          { class: ['bi', 'bi-callout', `bi-callout--${tone}`] },
          h('div', { class: 'bi-callout-label' }, icon(tone, { class: 'bi-callout-icon' }), label),
          h('div', { class: 'bi-callout-text' }, this.rich(item.text)),
        );
      }
      case 'misconception':
        return h(
          'div',
          { class: 'bi bi-misconception' },
          h(
            'div',
            { class: 'bi-mis-row bi-mis-wrong' },
            icon('cross', { class: 'bi-mis-icon' }),
            h('span', { class: 'bi-mis-label', text: LABELS.misconception }),
            h('span', { class: 'bi-mis-text' }, this.rich(item.text)),
          ),
          item.justification
            ? h(
                'div',
                { class: 'bi-mis-row bi-mis-right' },
                icon('check', { class: 'bi-mis-icon' }),
                h('span', { class: 'bi-mis-label', text: LABELS.correction }),
                h('span', { class: 'bi-mis-text' }, this.rich(item.justification)),
              )
            : null,
        );
      case 'code':
        return this.renderCode(item);
      case 'table':
        return this.renderTable(item);
      case 'figure':
        return this.renderFigure(item);
      case 'example_step':
        return this.renderStep(item);
      case 'takeaway':
        return h(
          'div',
          { class: 'bi bi-takeaway' },
          icon('star', { class: 'bi-takeaway-icon' }),
          h('span', { class: 'bi-text' }, this.rich(item.text)),
        );
      default:
        return h('p', { class: 'bi bi-paragraph' }, this.rich(item.text));
    }
  }

  /** @param {BoardItem} item */
  renderFormula(item) {
    const tex = h('div', { class: 'bi-formula-tex' });
    mountTex(tex, item.latex || '', true, { renderTex: this.deps.renderTex, pending: this.pending });
    const vars = (item.variables || []).map((v, n) => {
      const sym = h('span', { class: 'bi-var-sym' });
      mountTex(sym, v.symbol_latex, false, { renderTex: this.deps.renderTex, pending: this.pending });
      const row = h(
        'div',
        { class: 'bi-var', dataset: { part: `var:${n}` } },
        sym,
        h('span', { class: 'bi-var-meaning', text: v.meaning }),
        v.unit ? h('span', { class: 'bi-var-unit', text: v.unit }) : null,
      );
      return this.part(`var:${n}`, row);
    });
    return h(
      'div',
      { class: 'bi bi-formula' },
      tex,
      item.text ? h('div', { class: 'bi-formula-text' }, this.rich(item.text)) : null,
      vars.length ? h('div', { class: 'bi-vars' }, vars) : null,
    );
  }

  /** @param {BoardItem} item */
  renderCode(item) {
    const lang = prismLanguage(item.language);
    const cls = lang ? `language-${lang}` : 'language-none';
    /** @type {HTMLElement} */
    const code = h('code', { class: cls, text: item.code || '' });
    const root = h(
      'div',
      { class: 'bi bi-code' },
      h('div', { class: 'bi-code-lang', text: String(item.language || 'code') }),
      h('pre', { class: ['bi-code-pre', cls] }, code),
    );
    if (lang) {
      const load = this.deps.loadPrism || libLoadPrism;
      this.pending.push(
        Promise.resolve()
          .then(() => load(lang))
          .then((Prism) => {
            // Prism re-tokenises the element's textContent and writes escaped markup.
            if (!this.destroyed && Prism && typeof Prism.highlightElement === 'function') Prism.highlightElement(code);
          })
          .catch(() => {
            /* plain text fallback */
          }),
      );
    }
    return root;
  }

  /** @param {BoardItem} item */
  renderTable(item) {
    const headers = item.headers || [];
    const rows = item.rows || [];
    return h(
      'div',
      { class: 'bi bi-table' },
      h(
        'table',
        null,
        h('thead', null, h('tr', null, headers.map((c, n) => this.part(`column:${n}`, h('th', { scope: 'col' }, this.rich(c)))))),
        h('tbody', null, rows.map((r) => h('tr', null, r.map((c, n) => this.part(`column:${n}`, h('td', null, this.rich(c))))))),
      ),
    );
  }

  /** @param {BoardItem} item */
  renderFigure(item) {
    const media = this.scene.figures ? this.scene.figures[item.id] : undefined;
    const caption = item.caption ? h('figcaption', { class: 'bi-figcaption' }, this.rich(item.caption)) : null;
    if (!media || !media.url) {
      return h('figure', { class: 'bi bi-figure is-missing' }, h('div', { class: 'bi-figure-missing', text: LABELS.figureMissing }), caption);
    }
    /** @type {HTMLImageElement} */
    const img = h('img', {
      class: 'bi-figure-img',
      src: media.url,
      alt: plainText(item.caption) || 'Figure',
      decoding: 'async',
      loading: 'eager',
      draggable: 'false',
      width: media.width || undefined,
      height: media.height || undefined,
    });
    this.images.push(img);
    return h('figure', { class: 'bi bi-figure' }, h('div', { class: 'bi-figure-frame' }, img), caption);
  }

  /** @param {BoardItem} item */
  renderStep(item) {
    this._stepNo += 1;
    return h(
      'div',
      { class: ['bi', 'bi-step', { 'is-blank': !!item.blank }] },
      h('span', { class: 'bi-step-num', text: `${LABELS.step} ${this._stepNo}` }),
      h(
        'div',
        { class: 'bi-step-main' },
        item.blank ? h('span', { class: 'bi-step-blank', text: LABELS.blank }) : null,
        h('span', { class: 'bi-step-text' }, this.rich(item.text)),
        item.justification ? h('span', { class: 'bi-step-why' }, this.rich(item.justification)) : null,
      ),
    );
  }

  /**
   * Apply a schedule state (cheap when nothing changed).
   * @param {SceneState} state
   */
  update(state) {
    this._state = state;
    const pending = state.pendingParts || NO_PARTS;
    const emphasis = state.emphasisParts || NO_PARTS;
    const sig = [
      [...state.visibleItemIds].join(','),
      [...state.filledItemIds].join(','),
      [...state.highlightItemIds].join(','),
      state.activeItemId || '',
      [...pending].join(','),
      [...emphasis].join(','),
    ].join('|');
    if (sig === this._signature) return;
    this._signature = sig;
    for (const [id, el] of this.items) {
      const visible = state.visibleItemIds.has(id);
      el.classList.toggle('is-visible', visible);
      el.setAttribute('aria-hidden', visible ? 'false' : 'true');
      el.classList.toggle('is-filled', state.filledItemIds.has(id));
      el.classList.toggle('is-highlight', state.highlightItemIds.has(id));
      el.classList.toggle('is-active', state.activeItemId === id);
    }
    for (const [key, els] of this.parts) {
      const isPending = pending.has(key);
      const isEmph = emphasis.has(key);
      for (const el of els) {
        el.classList.toggle('is-unsaid', isPending);
        el.classList.toggle('is-emph', isEmph);
        if (isPending) el.setAttribute('aria-hidden', 'true');
        else el.removeAttribute('aria-hidden');
      }
    }
    this.keepInView();
  }

  /** Resolves once TeX, code highlighting and figures are done (never rejects). */
  async ready() {
    let count = -1;
    // TeX/Prism promises may enqueue more work; settle until stable.
    while (count !== this.pending.length) {
      count = this.pending.length;
      await Promise.allSettled(this.pending.slice());
    }
    await Promise.allSettled(
      this.images.map((img) =>
        typeof img.decode === 'function'
          ? img.decode().catch(() => {
              img.classList.add('is-broken');
            })
          : Promise.resolve(),
      ),
    );
  }

  /** Whether the body content is taller than its box. */
  overflows() {
    return this.body.scrollHeight > this.body.clientHeight + 1;
  }

  /**
   * Fit the board into its zone by scaling the font (binary search, deterministic), then keep the
   * narrated item in view if even the smallest scale overflows.
   * @returns {number} the chosen scale
   */
  fit() {
    if (this.destroyed || !this.el.isConnected) return this.fitScale;
    const set = (/** @type {number} */ s) => this.el.style.setProperty('--fit', s.toFixed(4));
    set(1);
    let scale = 1;
    if (this.overflows()) {
      let lo = MIN_FIT;
      let hi = 1;
      for (let i = 0; i < 7; i++) {
        const mid = (lo + hi) / 2;
        set(mid);
        if (this.overflows()) hi = mid;
        else lo = mid;
      }
      scale = lo;
      set(scale);
    }
    this.fitScale = scale;
    this.keepInView();
    return scale;
  }

  /** Scroll so the active (or last revealed) item is visible; a pure function of the state. */
  keepInView() {
    const body = this.body;
    if (!this._state || !this.overflows()) {
      if (body.scrollTop) body.scrollTop = 0;
      return;
    }
    const state = this._state;
    let target = state.activeItemId ? this.items.get(state.activeItemId) || null : null;
    if (!target) {
      for (const [id, el] of this.items) if (state.visibleItemIds.has(id)) target = el;
    }
    if (!target) return;
    const desired = Math.max(0, target.offsetTop + target.offsetHeight - body.clientHeight + 12);
    body.scrollTop = Math.min(desired, Math.max(0, body.scrollHeight - body.clientHeight));
  }

  /** Release the DOM. */
  destroy() {
    this.destroyed = true;
    this.el.remove();
    this.items.clear();
    this.parts.clear();
    this.images = [];
    this.pending = [];
  }
}
