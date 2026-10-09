// @ts-check
/**
 * Fixed 1920x1080 logical stage scaled into its container with a CSS transform (letterboxed),
 * identical in live, preview and render mode so text wraps the same everywhere.
 */

import { h } from '../shared/dom.js';
import { STAGE_HEIGHT, STAGE_WIDTH } from './layout.js';

/**
 * Scale and offset that fit a w x h stage inside a cw x ch container (contain, centred).
 * Degenerate containers (0 or NaN) fall back to scale 1 at the origin.
 * @param {number} cw
 * @param {number} ch
 * @param {number} [w]
 * @param {number} [h]
 * @returns {{scale: number, x: number, y: number}}
 */
export function computeFit(cw, ch, w = STAGE_WIDTH, h = STAGE_HEIGHT) {
  if (!(cw > 0 && ch > 0 && w > 0 && h > 0)) return { scale: 1, x: 0, y: 0 };
  const scale = Math.min(cw / w, ch / h);
  return { scale, x: (cw - w * scale) / 2, y: (ch - h * scale) / 2 };
}

/**
 * @typedef {import('../shared/types.js').StageRect} StageRect
 */

/**
 * Make a StageRect (with w/h aliases), rounded to whole pixels.
 * @param {number} x
 * @param {number} y
 * @param {number} width
 * @param {number} height
 * @returns {StageRect}
 */
export function stageRect(x, y, width, height) {
  const r = { x: Math.round(x), y: Math.round(y), width: Math.round(width), height: Math.round(height) };
  return { ...r, w: r.width, h: r.height };
}

export class Stage {
  /**
   * @param {HTMLElement} host   element the viewport is appended to
   * @param {{ width?: number, height?: number }} [opts]
   */
  constructor(host, opts = {}) {
    this.width = opts.width || STAGE_WIDTH;
    this.height = opts.height || STAGE_HEIGHT;
    this.scale = 1;
    this.offsetX = 0;
    this.offsetY = 0;
    /** @type {HTMLDivElement} */
    this.el = h('div', { class: 'ap-stage', style: { width: `${this.width}px`, height: `${this.height}px` } });
    /** @type {HTMLDivElement} */
    this.viewport = h('div', { class: 'ap-viewport' }, this.el);
    host.appendChild(this.viewport);
    /** @type {ResizeObserver | null} */
    this.observer = null;
    this.onWindowResize = () => this.fit();
    if (typeof ResizeObserver === 'function') {
      this.observer = new ResizeObserver(() => this.fit());
      this.observer.observe(this.viewport);
    } else if (typeof window !== 'undefined') {
      window.addEventListener('resize', this.onWindowResize);
    }
    this.fit();
  }

  /** Recompute the transform from the viewport size. */
  fit() {
    const { scale, x, y } = computeFit(this.viewport.clientWidth, this.viewport.clientHeight, this.width, this.height);
    this.scale = scale;
    this.offsetX = x;
    this.offsetY = y;
    this.el.style.transform = `translate(${x}px, ${y}px) scale(${scale})`;
  }

  /**
   * Convert a client-space rect into stage pixels.
   * @param {{left: number, top: number, width: number, height: number}} r
   * @returns {StageRect}
   */
  toStageRect(r) {
    const s = this.el.getBoundingClientRect();
    const k = this.scale || 1;
    return stageRect((r.left - s.left) / k, (r.top - s.top) / k, r.width / k, r.height / k);
  }

  /**
   * Stage-pixel rect of an element (null when it has no box).
   * @param {Element | null | undefined} el
   * @returns {StageRect | null}
   */
  rectOf(el) {
    if (!el || !el.isConnected) return null;
    return this.toStageRect(el.getBoundingClientRect());
  }

  /** Detach observers and remove the viewport. */
  destroy() {
    if (this.observer) this.observer.disconnect();
    if (typeof window !== 'undefined') window.removeEventListener('resize', this.onWindowResize);
    this.viewport.remove();
  }
}
