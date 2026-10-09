// @ts-check
/**
 * Caption overlay (live/preview). Text comes from schedule.sceneStateAt().caption (beat caption
 * cues, already line-broken by the backend) and is always set with textContent. Render mode never
 * shows captions (MP4 captions are burned in by ffmpeg or shipped as SRT/VTT).
 */

import { h } from '../shared/dom.js';

export { selectCue } from './schedule.js';

/** @typedef {'s' | 'm' | 'l'} CaptionSize */

export const CAPTION_SIZES = /** @type {const} */ (['s', 'm', 'l']);

export class CaptionsView {
  /**
   * @param {HTMLElement} parent   stage layer
   * @param {{ enabled?: boolean, size?: CaptionSize }} [opts]
   */
  constructor(parent, opts = {}) {
    /** @type {HTMLSpanElement} */
    this.text = h('span', { class: 'ap-caption-text' });
    /** @type {HTMLDivElement} */
    this.el = h('div', { class: 'ap-captions', 'aria-live': 'off', dir: 'auto' }, this.text);
    parent.appendChild(this.el);
    this.enabled = opts.enabled !== false;
    /** @type {CaptionSize} */
    this.size = 'm';
    /** @type {string | null} */
    this.current = null;
    this.setSize(opts.size || 'm');
    this.setEnabled(this.enabled);
  }

  /**
   * Show a caption (null/empty hides the box).
   * @param {string | null | undefined} text
   */
  show(text) {
    const value = text ? String(text) : null;
    if (value === this.current) return;
    this.current = value;
    this.text.textContent = value || '';
    this.el.classList.toggle('has-text', !!value);
  }

  /** @param {boolean} on */
  setEnabled(on) {
    this.enabled = !!on;
    this.el.classList.toggle('is-off', !this.enabled);
  }

  /** @param {CaptionSize} size */
  setSize(size) {
    this.size = CAPTION_SIZES.includes(size) ? size : 'm';
    this.el.dataset.size = this.size;
  }

  destroy() {
    this.el.remove();
  }
}
