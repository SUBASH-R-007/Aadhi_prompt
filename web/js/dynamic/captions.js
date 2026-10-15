// @ts-check
/**
 * In-stage phrase captions (DYNAMIC_VIDEO_DESIGN §4.11, §2.6): drawn by the stage itself, so the live player and the
 * MP4 match and the MP4 never relies on libass. One fixed position for the whole video (R-34): the caption band of
 * the format (R-59). Sentence case, no per-chunk scale pop, no active-word emphasis; key terms in `mark` (R-35).
 *
 * Styles (R1): `clean_pill` (white, weight 600, on a translucent dark pill; 16:9 and 9:16 Soft) and `bold_outline`
 * (weight 700, white with a 6 px dark outline; 9:16 Crisp). The caption-only layer mode (`setLayers`) draws this
 * layer alone on a transparent page for the burned second version (§7.5).
 */

import { h } from '../shared/dom.js';
import { splitWords } from '../motion/text.js';

/** @typedef {import('./core.js').Painter} Painter */
/** @typedef {import('./core.js').Zones} Zones */
/** @typedef {import('./state.js').CaptionView} CaptionView */

export const CAPTION_STYLES = Object.freeze(['clean_pill', 'bold_outline']);
/** Caption text colour and the pill behind it (contrast >= 7:1 target, R-53). */
export const CAPTION_INK = '#FFFFFF';
export const CAPTION_PILL = '#0B0D14';
export const CAPTION_MARK = '#F2D935';

/**
 * The caption style of a video: the presentation's when this stage draws it, else by format (§4.8 personalities:
 * Crisp reels get `bold_outline`, everything else `clean_pill`).
 * @param {{caption_style?: string, personality?: string} | null | undefined} presentation
 * @param {'landscape' | 'portrait'} format
 * @returns {'clean_pill' | 'bold_outline'}
 */
export function captionStyle(presentation, format) {
  const asked = presentation && presentation.caption_style;
  if (asked === 'clean_pill' || asked === 'bold_outline') return asked;
  return format === 'portrait' && presentation && presentation.personality === 'crisp' ? 'bold_outline' : 'clean_pill';
}

export class CaptionLayer {
  /**
   * @param {HTMLElement} parent
   * @param {{zones: Zones, style: 'clean_pill' | 'bold_outline', locale?: string}} opts
   */
  constructor(parent, opts) {
    this.zones = opts.zones;
    this.style = opts.style;
    this.locale = opts.locale;
    const band = opts.zones.caption;
    /** @type {HTMLDivElement} */
    this.text = h('div', { class: 'dyn-caption-text', dir: 'auto' });
    /** @type {HTMLDivElement} */
    this.box = h('div', { class: 'dyn-caption' }, this.text);
    /** @type {HTMLDivElement} */
    this.el = h(
      'div',
      {
        class: ['dyn-captions', `dyn-captions--${opts.zones.format}`, `dyn-captions--${opts.style}`],
        'aria-hidden': 'true',
        style: { left: band.x0, top: band.y0, width: band.x1 - band.x0, height: band.y1 - band.y0 },
      },
      this.box,
    );
    parent.appendChild(this.el);
    /** @type {string | null} */
    this.shown = null;
    this.enabled = true;
  }

  /**
   * Show a caption (null: none).
   * @param {Painter} paint
   * @param {CaptionView | null} caption
   */
  update(paint, caption) {
    const on = this.enabled && !!caption;
    paint.style(this.el, 'visibility', on ? 'visible' : 'hidden');
    const id = on && caption ? `${caption.id}|${caption.lines.join('\n')}|${[...caption.keys].sort().join(',')}` : '';
    paint.note(`cap:${id}`);
    if (id === this.shown) return;
    this.shown = id;
    this.text.replaceChildren();
    if (!on || !caption) return;
    caption.lines.forEach((line, i) => {
      if (i) this.text.appendChild(h('br'));
      if (!caption.keys.size) {
        this.text.appendChild(document.createTextNode(line));
        return;
      }
      for (const part of splitWords(line, this.locale)) {
        const word = part.text.toLowerCase().replace(/[^\p{L}\p{M}\p{N}]+/gu, '');
        const node = caption.keys.has(word) || caption.keys.has(part.text.toLowerCase())
          ? h('span', { class: 'dyn-caption-key', text: part.text })
          : document.createTextNode(part.text);
        this.text.append(node, part.ws);
      }
    });
  }

  /** @param {boolean} on */
  setEnabled(on) {
    this.enabled = !!on;
  }

  /** Text box of the caption on screen (stage px) for textBoxes(); null when none. */
  textEntry() {
    if (!this.enabled || !this.shown) return null;
    // clean_pill: on its pill; bold_outline: the 6 px outline in the same dark colour surrounds every glyph
    const band = this.zones.caption;
    const size = this.zones.format === 'portrait' ? 56 : 48;
    return {
      el: this.box, role: 'caption', slot: 'caption', critical: true, ink: CAPTION_INK, bg: CAPTION_PILL,
      overflow: Math.max(0, this.box.offsetHeight - (band.y1 - band.y0)), size, min: size,
      weight: this.zones.format === 'portrait' || this.style === 'bold_outline' ? 700 : 600, text: this.text.textContent || '',
    };
  }

  destroy() {
    this.el.remove();
  }
}
