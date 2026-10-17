// @ts-check
/**
 * `aadhi_recap` (DYNAMIC_VIDEO_DESIGN §4.3 A, R1, class `structure`; §3.3 "16:9 / 9:16 aadhi_recap", §3.4; B2 plan
 * §1.4 the template contract, §2 "Aadhi"; owner decision 2 of 2026-10-16): the social cue that opens the retrieval
 * recap - Aadhi's card, and on his gesture side the prompt ("Quick check: what are the 3 big ideas?") with three empty
 * slots and **no countdown** (§3.1: he is gone before anyone has to think; `recap_retrieval`, the next scene, runs the
 * think time).
 *
 * **Aadhi is not drawn here**: the stage's Slot draws the presenter below this template's content from
 * `look.presenter` (`../presenter.js`; the card fades out over 300 ms at `t_exit`). This template draws the prompt.
 *
 * Data (§4.3, templates.py `_fit_aadhi_recap`): a scene with exactly one `question` item with text (the prompt),
 * nothing else with text and no shown side panel (`drawsScene`); anything else is `board_generic`'s whole, every hook
 * handed on, and the fit refuses it too. The timeline builder synthesises this scene before a 16:9 lecture's
 * retrieval recap (`aadhi/compose/dynamic/presenter.py recap_scene`, §3.4: on by default in 16:9, off in 9:16) unless
 * the screenplay brings its own presenter recap scene.
 *
 * 16:9 (§3.3): Aadhi's 720x720 card at x 96-816, y 180-900 (the Slot); the prompt card on the right, x 880-1824 and
 * the same height, on the `surface` role: the prompt (display face, 64-80 px, <= 3 lines) above a row of three empty
 * slots (4 px dashed `ink2` outlines on the card, the card radius; decoration, no text: §4.2 keeps structure roles off
 * content). 9:16 (§3.3): the prompt in the
 * hook band (88-104 px, <= 4 lines, <= 10 words), Aadhi's 744x744 card below.
 *
 * Choreography (§4.8): the prompt card rises 24 px into place from 0.15 s (450 ms, `ease.enter`), the prompt's words
 * land with a 70 ms stagger 120 ms later, and the slots follow one by one (100 ms apart); everything stays until the
 * scene ends. The personality's scale applies to these durations (`_shared.js seconds` / `enterAt`).
 *
 * Emphasis is cues.js's (this template consumes none: templates.py `consumes` is the prompt's reveal only), and the
 * prompt is a plain `.rt-k`-keeping `richWords` text all the same. Determinism: `update` is a pure function of t; every
 * property it paints is painted on every frame with a value; translates are whole pixels and 2D.
 */

import { h } from '../../shared/dom.js';
import { mathSpans, plainText } from '../../player/richtext.js';
import { fixesOf, fx } from '../core.js';
import boardGeneric from './board_generic.js';
import { fitInBox, lineHeight } from './hook_question.js';
import {
  RADIUS, SPACE, enterAt, fitLines, group, paintRise, place, region, richWords, scrimmedBg, seconds, slotRange,
} from './_shared.js';

/** @typedef {import('./types.js').TemplateContext} TemplateContext */
/** @typedef {import('./types.js').TemplateModule} TemplateModule */
/** @typedef {import('./types.js').FocusRect} FocusRect */
/** @typedef {import('./types.js').TextEntry} TextEntry */
/** @typedef {import('../../shared/types.js').TimedScene} TimedScene */
/** @typedef {import('../../shared/types.js').BoardItem} BoardItem */
/** @typedef {import('../core.js').Box} Box */
/** @typedef {import('../core.js').Zones} Zones */

export const ID = 'aadhi_recap';

// ---------------------------------------------------------------------------------------------
// Motion (§4.8)
// ---------------------------------------------------------------------------------------------

/** The prompt card rises into place from here (with the clip already up), over CARD_SECONDS. */
export const CARD_AT = 0.15;
export const CARD_SECONDS = 0.45;
export const RISE = 24;
/** The prompt's words follow the card by this much, 70 ms apart (compressed into 0.8 s for a long prompt). */
export const TEXT_DELAY = 0.12;
export const WORD_SECONDS = 0.45;
export const WORD_STAGGER = 0.07;
export const WORD_STAGGER_TOTAL = 0.8;
/** The empty slots enter after the prompt has landed, SLOT_STAGGER apart, each over SLOT_SECONDS. */
export const SLOT_STAGGER = 0.1;
export const SLOT_SECONDS = 0.3;

// ---------------------------------------------------------------------------------------------
// Layout (stage px; §3.3; templates.py mirrors the slots)
// ---------------------------------------------------------------------------------------------

/** How many empty slots the prompt card shows (§3.3: "three empty slots"; presenter.py RECAP_SLOTS). */
export const SLOTS = 3;
export const TRACKING_EM = -0.015;
export const SIZE_STEP = 4;

/**
 * 16:9: `card` (the prompt card on his gesture side: x 880-1824 at his card's height, y 180-900), its padding, the
 * prompt's size range and lines, and the slot row (`slotHeight`, `gap` between the slots and above the row). 9:16: the
 * prompt in the hook band. Every size is a B2 proposal pending the owner's review of the reference frames.
 */
export const LAYOUT = Object.freeze({
  landscape: Object.freeze({
    card: Object.freeze({ x0: 880, y0: 180, x1: 1824, y1: 900 }),
    pad: SPACE[5], // 48
    prompt: Object.freeze({ min: 64, max: 80, lines: 3 }),
    slotHeight: 120,
    gap: SPACE[3], // 32
  }),
  portrait: Object.freeze({
    prompt: Object.freeze({ min: 88, max: 104, lines: 4 }),
  }),
});
/** The slots' outline: 4 px (the catalogue's structure border width), dashed, in the content role `ink2`. */
export const SLOT_BORDER = 4;

/**
 * The text slots (templates.py `SlotSpec`): `prompt` per format (`default`). Pure.
 */
export function slotRows() {
  const L = LAYOUT.landscape;
  const band = region({ format: 'portrait' }, 'hook');
  /** @param {{min: number, max: number, lines: number}} s @param {number} w */
  const display = (s, w) => ({
    w, max_lines: s.lines, size: [s.min, s.max], role: 'title', face: 'display', weight: 700, line_height: 1.05,
    tracking_em: TRACKING_EM, prefer_lines: null, critical: true,
  });
  return {
    prompt: {
      landscape: { default: display(L.prompt, L.card.x1 - L.card.x0 - 2 * L.pad) },
      portrait: { default: display(LAYOUT.portrait.prompt, band.x1 - band.x0) },
    },
  };
}

// ---------------------------------------------------------------------------------------------
// What the scene shows (pure)
// ---------------------------------------------------------------------------------------------

/**
 * The scene's prompt (its first `question` with text) and every other item: any item of another kind (with text or
 * not) and a second question with text; a question without text draws nothing.
 * @param {TimedScene} scene
 * @returns {{prompt: BoardItem | null, others: BoardItem[]}}
 */
export function recapItems(scene) {
  /** @type {BoardItem | null} */
  let prompt = null;
  /** @type {BoardItem[]} */
  const others = [];
  for (const item of (scene && scene.board) || []) {
    const text = typeof item.text === 'string' ? item.text.trim() : '';
    if (item.kind !== 'question') others.push(item);
    else if (!text) continue;
    else if (!prompt) prompt = item;
    else others.push(item);
  }
  return { prompt, others };
}

/**
 * Whether this template draws the scene whole (`_fit_aadhi_recap` refuses the same): one prompt, nothing else with
 * text, no shown side panel.
 * @param {TimedScene} scene
 */
export function drawsScene(scene) {
  const { prompt, others } = recapItems(scene);
  const sp = scene && scene.side_panel;
  return !!prompt && others.length === 0 && !(sp && scene.layout?.show_side_panel !== false);
}

/**
 * Every TeX string the template renders (the prompt's `$...$` spans, inline).
 * @param {TimedScene} scene
 * @returns {{tex: string, display: boolean}[]}
 */
export function texOf(scene) {
  const { prompt } = recapItems(scene);
  return prompt ? mathSpans(String(prompt.text || '')).filter((t) => t.trim()).map((tex) => ({ tex, display: false })) : [];
}

// ---------------------------------------------------------------------------------------------
// The template
// ---------------------------------------------------------------------------------------------

/** @param {TemplateContext} ctx */
const isFallback = (ctx) => !!(ctx.view && ctx.view.arFallback);

/** @type {TemplateModule} */
export default {
  id: ID,

  /**
   * The DOM: 16:9 the prompt card (prompt and slots); 9:16 the prompt in the hook band. No measuring here.
   * @param {TemplateContext} ctx
   */
  build(ctx) {
    const { scene, format } = ctx;
    if (!drawsScene(scene)) {
      const inner = boardGeneric.build(ctx);
      inner.arFallback = true;
      return inner;
    }
    const { prompt } = recapItems(scene);
    const item = /** @type {BoardItem} */ (prompt);
    const portrait = format === 'portrait';
    const fixes = fixesOf(ctx.look);
    const surface = ctx.roles.color('surface');
    const ink = portrait ? ctx.onBackground.ink : ctx.roles.color('ink');
    const scrim = portrait && fixes.slots.prompt && fixes.slots.prompt.scrim ? ctx.roles.color('scrim') : null;
    const root = h('div', {
      class: ['dyn-ar', `dyn-ar--${format}`],
      style: { position: 'absolute', left: '0px', top: '0px', right: '0px', bottom: '0px' },
    });
    const text = h('div', {
      class: 'dyn-ar-prompt', dir: 'auto', dataset: { item: item.id },
      style: {
        margin: '0px', color: ink, fontFamily: 'var(--dyn-font-display)', fontWeight: '700',
        lineHeight: String(lineHeight('display', item.text || '')), letterSpacing: `${TRACKING_EM}em`,
        overflowWrap: 'anywhere', maxWidth: '100%',
      },
    });
    const { words, tex } = richWords(ctx, text, String(item.text || ''));
    const textGroup = group('dyn-ar-prompt-group', text);

    /** @type {HTMLElement | null} */
    let card = null;
    /** @type {HTMLElement[]} */
    const slots = [];
    /** @type {HTMLElement} */
    let holder;
    if (portrait) {
      holder = h('div', { class: 'dyn-ar-holder', style: { display: 'flex', flexDirection: 'column', justifyContent: 'center', boxSizing: 'border-box' } }, textGroup);
      if (scrim) {
        Object.assign(holder.style, {
          padding: `${SPACE[1]}px ${SPACE[2]}px`, marginLeft: `-${SPACE[2]}px`, borderRadius: `${RADIUS.card}px`, background: scrim,
        });
      }
      root.appendChild(holder);
    } else {
      const L = LAYOUT.landscape;
      const row = h('div', { class: 'dyn-ar-slots', style: { display: 'flex', gap: `${SPACE[2]}px`, width: '100%' } });
      for (let i = 0; i < SLOTS; i++) {
        const slot = h('div', {
          class: 'dyn-ar-slot',
          style: {
            flex: '1 1 0px', height: `${L.slotHeight}px`, boxSizing: 'border-box', borderRadius: `${RADIUS.card}px`,
            border: `${SLOT_BORDER}px dashed ${ctx.roles.color('ink2')}`,
          },
        });
        slots.push(slot);
        row.appendChild(slot);
      }
      const slotGroup = group('dyn-ar-slots-group', row);
      Object.assign(slotGroup.style, { width: '100%' });
      const inner = h('div', {
        class: 'dyn-ar-inner',
        style: {
          display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: `${L.gap}px`, height: '100%',
          padding: `${L.pad}px`, boxSizing: 'border-box',
        },
      }, textGroup, slotGroup);
      card = h('div', { class: 'dyn-ar-card', style: { background: surface, borderRadius: `${RADIUS.card}px`, boxSizing: 'border-box' } }, inner);
      holder = h('div', { class: 'dyn-ar-holder' }, card);
      root.appendChild(holder);
      return finish({ root, holder, card, slots, slotGroup, text, textGroup, words, tex, item, ink, bg: surface, scrim: null });
    }
    return finish({ root, holder, card, slots, slotGroup: null, text, textGroup, words, tex, item, ink, bg: ctx.onBackground.bg, scrim });

    /** @param {any} view */
    function finish(view) {
      ctx.root.appendChild(view.root);
      return { ...view, size: 0, min: 0, overflow: 0, alpha: 0 };
    }
  },

  /**
   * Measure and fit (idempotent): place the card (16:9) or the band (9:16), then fit the prompt.
   * @param {TemplateContext} ctx
   */
  layout(ctx) {
    if (isFallback(ctx)) {
      if (typeof boardGeneric.layout === 'function') boardGeneric.layout(ctx);
      return;
    }
    const v = ctx.view;
    const lh = lineHeight('display', v.item.text || '');
    if (ctx.format === 'portrait') {
      const band = region(ctx, 'hook');
      place(v.holder, band);
      const P = LAYOUT.portrait.prompt;
      const range = slotRange(ctx, 'prompt', { min: P.min, max: P.max, role: 'title' });
      const pad = v.scrim ? 2 * SPACE[1] : 0;
      const fit = fitInBox(v.text, { max: range.max, min: range.min, lines: P.lines, lh, step: SIZE_STEP }, v.words, v.tex, band.y1 - band.y0 - pad);
      v.size = fit.size;
      v.min = range.min;
      v.overflow = fit.overflow;
      return;
    }
    const L = LAYOUT.landscape;
    place(v.holder, L.card);
    v.card.style.width = '100%';
    v.card.style.height = '100%';
    const range = slotRange(ctx, 'prompt', { min: L.prompt.min, max: L.prompt.max, role: 'title' });
    const fit = fitLines(v.text, { max: range.max, min: range.min, lines: L.prompt.lines, lh, step: SIZE_STEP }, v.words, v.tex);
    v.size = fit.size;
    v.min = range.min;
    v.overflow = fit.overflow;
    // the prompt and the slot row must fit the card: what runs past it is reported on the prompt
    const room = L.card.y1 - L.card.y0 - 2 * L.pad;
    const used = v.text.offsetHeight + L.gap + L.slotHeight;
    if (used > room + 1) v.overflow = Math.max(v.overflow, Math.round(used - room));
  },

  /**
   * Paint scene time t: the card's rise, the prompt's words and the slots. A pure function of t.
   * @param {TemplateContext} ctx
   * @param {number} t
   * @param {import('../../shared/types.js').SceneState} _state
   */
  update(ctx, t, _state) {
    if (isFallback(ctx)) {
      boardGeneric.update(ctx, t, _state);
      return;
    }
    const v = ctx.view;
    if (v.card) paintRise(ctx, v.card, enterAt(ctx, t, CARD_AT, CARD_SECONDS), RISE);
    const start = CARD_AT + seconds(ctx, TEXT_DELAY);
    const n = v.words.length;
    const each = n > 1 ? Math.min(WORD_STAGGER, WORD_STAGGER_TOTAL / (n - 1)) : 0;
    let alpha = n ? 0 : 1;
    v.words.forEach((/** @type {HTMLElement} */ w, i) => {
      const e = enterAt(ctx, t, start + seconds(ctx, each * i), WORD_SECONDS);
      if (i === n - 1) alpha = e;
      paintRise(ctx, w, e, RISE);
    });
    const cardIn = v.card ? enterAt(ctx, t, CARD_AT, CARD_SECONDS) : 1;
    v.alpha = Math.min(alpha, cardIn);
    const landed = start + seconds(ctx, each * Math.max(0, n - 1) + WORD_SECONDS);
    v.slots.forEach((/** @type {HTMLElement} */ s, i) => {
      ctx.paint.style(s, 'opacity', fx(enterAt(ctx, t, landed + seconds(ctx, SLOT_STAGGER * i), SLOT_SECONDS)));
    });
  },

  /**
   * Where a look_here points: the prompt's text box (whole stage px); null otherwise.
   * @param {TemplateContext} ctx
   * @param {string} target
   * @returns {FocusRect | null}
   */
  focusRect(ctx, target) {
    if (isFallback(ctx)) return typeof boardGeneric.focusRect === 'function' ? boardGeneric.focusRect(ctx, target) : null;
    const v = ctx.view;
    if (String(target || '').split(':')[0] !== v.item.id) return null;
    const r = ctx.rectOf(v.text);
    if (!r) return null;
    const x0 = Math.round(r.x);
    const y0 = Math.round(r.y);
    return { rect: { x: x0, y: y0, w: Math.round(r.x + r.w) - x0, h: Math.round(r.y + r.h) - y0 }, shape: 'rect', radius: 0, el: v.textGroup, kind: 'text' };
  },

  /**
   * The prompt's text element (cue.term's `.rt-k` spans), else null.
   * @param {TemplateContext} ctx
   * @param {string} itemId
   * @returns {Element | Element[] | null}
   */
  itemEl(ctx, itemId) {
    if (isFallback(ctx)) return typeof boardGeneric.itemEl === 'function' ? boardGeneric.itemEl(ctx, itemId) : null;
    return itemId === ctx.view.item.id ? ctx.view.text : null;
  },

  /**
   * The content groups a look_here dims: the prompt and (16:9) the slot row - the same elements at every t.
   * @param {TemplateContext} ctx
   * @returns {Element[]}
   */
  dimGroups(ctx) {
    if (isFallback(ctx)) return typeof boardGeneric.dimGroups === 'function' ? boardGeneric.dimGroups(ctx) : [];
    return [ctx.view.textGroup, ctx.view.slotGroup].filter(Boolean);
  },

  /** The morph anchor: none (B2f). @param {TemplateContext} _ctx @returns {null} */
  anchor(_ctx) {
    return null;
  },

  /**
   * Static: no medium of the template's own (Aadhi is the stage's presenter); board_generic's box when it draws.
   * @param {TimedScene} scene @param {Zones} zones @param {'live' | 'render'} mode @param {string | undefined} slot
   * @param {import('../core.js').Fixes} fixes
   * @returns {{w: number, h: number} | null}
   */
  mediaBox(scene, zones, mode, slot, fixes) {
    if (!drawsScene(scene)) {
      return typeof boardGeneric.mediaBox === 'function' ? boardGeneric.mediaBox(scene, zones, mode, slot, fixes) : null;
    }
    return null;
  },

  /**
   * Static: every TeX string the template renders for the scene (`texOf`).
   * @param {TimedScene} scene @param {import('../../shared/types.js').ResolvedLook | null} look
   * @param {'landscape' | 'portrait'} fmt
   */
  tex(scene, look, fmt) {
    if (!drawsScene(scene)) return typeof boardGeneric.tex === 'function' ? boardGeneric.tex(scene, look, fmt) : [];
    return texOf(scene);
  },

  /**
   * The text on screen (preflight, §7.12): the prompt, critical, on the card's surface (16:9) or on the background or
   * its scrim (9:16).
   * @param {TemplateContext} ctx
   * @returns {TextEntry[]}
   */
  texts(ctx) {
    if (isFallback(ctx)) return typeof boardGeneric.texts === 'function' ? boardGeneric.texts(ctx) : [];
    const v = ctx.view;
    return [{
      el: v.text, role: 'title', slot: 'prompt', critical: true, ink: v.ink,
      bg: v.scrim ? scrimmedBg(v.scrim, ctx.onBackground, v.ink) : v.bg, alpha: Number(fx(v.alpha)),
      overflow: v.overflow, size: v.size, min: v.min, weight: 700, text: plainText(String(v.item.text || '')),
    }];
  },
};
