// @ts-check
/**
 * `definition_card` (DYNAMIC_VIDEO_DESIGN §4.3 B, R1, class `definition`; B2 plan §1.4 template contract, §1.5 cues,
 * §1.11 slots, §P): "X means Y" on one card.
 *
 * Data (§4.3, templates.py `_fit_definition_card`): a `definition` item (its `term` and its `text`, the meaning) plus
 * an optional `bullet` example. Every further item with text is drawn as another example row, so nothing a timeline
 * names is dropped; a scene this template cannot draw whole (16:9: a side panel that is not a picture or clip; 9:16:
 * any shown panel or scene medium, because the stack has no room beside the card; or a scene with no `definition`
 * item at all) is handed to `board_generic` whole, every hook with it (`drawsScene`, list_build's convention, B2c
 * review CG-2).
 *
 * Layout (§2.5 named regions from the catalogue, core.js REGIONS; whole stage px; `boxes`):
 * - 16:9, per variant (§4.3 `card_icon_right`, `card_image_left`, `centred`; direct.py stores the variant, B2e):
 *   `centred` sets the card in `centre-60`; `card_icon_right` in `full`, with the item's stored icon (the catalogue's
 *   `feature_px`, §4.12) in a column of its own on the card's right; `card_image_left` contains the scene's picture or
 *   clip in `left-55` and sets the card in `right-45`, a GUTTER further right (media_split's 48 px gap in all).
 * - 9:16: one column. The card is the text column (x 192-780, the part both critical boxes hold, R-59) grown by its
 *   padding on each side, so its text sits exactly in the column while the card itself, a shape, may reach past it
 *   (cues.js shapeArea). `card_icon_right` puts the icon (`card_px`) above the term; `card_image_left` has no picture
 *   to show (such a scene is board_generic's), so it draws the `centred` layout.
 * - The card is `surface` with the layout tokens' card radius, shadow level 2 and a card-width structure border down
 *   its left edge (board_generic's card). Its height is its content's, centred in its region; the term, the meaning
 *   and the example rows are stacked with the format's gaps.
 * - Line heights are the type scale's, per script (§4.6, plan §2 R-29): the term the display role's (Latin 1.05,
 *   Indic >= 1.25), the meaning and the examples the body role's (1.35 / 1.45) - Tamil and Devanagari marks need the
 *   room (B2c review V5).
 * - Every text sits on the card's `surface`, so preflight's scrim remedy never applies here (board_generic's cards do
 *   the same); its size cap and the safe layout do, through `_shared.js slotRange`, and so does R-29's floor.
 *
 * The fit (`layout`): each slot takes the largest size in 4 px steps whose text fits its line cap
 * (`_shared.js fitLines`, the page's own rule, which textfit.py mirrors); then, while the stack is taller than the
 * card's region, the example rows shrink first, then the meaning, then the term - the headline keeps its size longest.
 * Nothing is ever dropped or faded out to make room: text still past the bottom is reported through `texts()` as
 * overflow, so preflight blocks with "Shorten text" (plan §2, formula_focus's and list_build's rule).
 *
 * Motion (§4.3, §4.8; every duration times the personality's scale, `_shared.js seconds` / `enterAt`): the card fades
 * in and rises CARD_RISE px over CARD_ENTER; the term enters on the item's own beat (state.js `itemTimings`: a reveal
 * event wins, else the beat, R-17's 120 ms lead) with the personality's text entrance (Crisp is revealed from its
 * baseline inside a mask, §4.3 "the term reveals from its baseline"); the meaning follows MEANING_DELAY later, its
 * words landing one after another (§4.3 "the meaning's words follow the narration", WORD_STAGGER apart) unless the
 * personality reveals text from its baseline, which reveals the meaning as one block (a per-word mask cannot keep the
 * paragraph's inline flow); each example row enters on its own beat; the icon enters with the term; a picture fades in
 * and scales 0.96 -> 1 (§4.3 media_split; §P: it only ever downscales, and `mediaBox` extracts it at its largest box),
 * while a clip only fades (a clip is never scaled, B2c deviation 28). Reduced motion (live only): everything fades in
 * place.
 *
 * Emphasis is cues.js's (§4.2, plan §1.4/§1.5): `itemEl` hands it the definition's fields in the item's field order
 * (its term, then its meaning), or an example row's text, with their [[term]] `.rt-k` spans kept by
 * `_shared.js richWords`, so `<item>` and `<item>:term:<n>` both resolve; `focusRect` gives the card (a shape, so its
 * outline is drawn round it, cues.js shapeArea), an example row (text) or the picture; `dimGroups` the card's text, the
 * icon and the picture. This template draws no emphasis, writes no `outline`, `text-decoration`, `filter`,
 * `backdrop-filter` or `will-change`, and colours no content element with a structure role (§4.2): only the card's own
 * left border takes one.
 *
 * Determinism (§7.4, plan §P): `update` is a pure function of t, every property it paints gets a value on every frame
 * (never ''), translates are whole pixels and 2D, and the only size decisions happen in `layout`, from the laid-out
 * page.
 *
 * The sizes, gaps and the 9:16 reading are B2 proposals pending the owner's review (the design gives the variants, the
 * choreography and the timings): aadhi/compose/dynamic/templates.py mirrors LAYOUT as this template's slots
 * (`slotRows`), and a test pins the two together.
 */

import { h } from '../../shared/dom.js';
import { mathSpans, plainText } from '../../player/richtext.js';
import { LAYOUT_TOKENS, fx, parseTarget } from '../core.js';
import { shownIn } from '../media.js';
import { itemTimings } from '../state.js';
import boardGeneric from './board_generic.js';
import {
  RADIUS, SPACE, TEXT_RISE, containRect, enterAt, fitLines, group, mediaAspect, paintMedia, paintRise,
  paintTextEntrance, region, revealMask, richWords, seconds, slotRange, storedIcon,
} from './_shared.js';

/** @typedef {import('./types.js').TemplateContext} TemplateContext */
/** @typedef {import('./types.js').TemplateModule} TemplateModule */
/** @typedef {import('./types.js').FocusRect} FocusRect */
/** @typedef {import('./types.js').TextEntry} TextEntry */
/** @typedef {import('../../shared/types.js').TimedScene} TimedScene */
/** @typedef {import('../../shared/types.js').BoardItem} BoardItem */
/** @typedef {import('../../shared/types.js').MediaRef} MediaRef */
/** @typedef {import('../core.js').Box} Box */
/** @typedef {import('../core.js').Zones} Zones */

export const ID = 'definition_card';

/** UI labels (kept together for localisation). */
export const LABELS = Object.freeze({ mediaMissing: 'Picture unavailable' });

/** The §4.3 variants (direct.py stores one; an unknown or missing name falls back to DEFAULT_VARIANT). */
export const VARIANTS = Object.freeze(['card_icon_right', 'card_image_left', 'centred']);
export const DEFAULT_VARIANT = 'centred';

/** The side-panel kinds whose medium this template shows beside the card in 16:9 (media_split's and list_build's
 * picture kinds: a GIF shows in the live player and its title card in the MP4, media.js shownIn). */
export const PICTURE_PANELS = Object.freeze(['figure', 'image', 'manim', 'gif']);

/** Line heights (§4.6 type scale, the catalogue's `type.scale`; a test pins them): the term the display role's, the
 * meaning and the examples the body role's; Indic text takes the Indic minimums (plan §2 R-29). */
export const LINE_HEIGHT = Object.freeze({
  display: Object.freeze({ latin: 1.05, indic: 1.25 }),
  body: Object.freeze({ latin: 1.35, indic: 1.45 }),
});
/** Indic scripts (Devanagari to Sinhala), whose text takes the taller line heights (the pilots' rule). */
const INDIC = new RegExp(`[${['Devanagari', 'Bengali', 'Gurmukhi', 'Gujarati', 'Oriya', 'Tamil', 'Telugu', 'Kannada',
  'Malayalam', 'Sinhala'].map((x) => `\\p{Script=${x}}`).join('')}]`, 'u');

/** The term's tracking (dynamic.css `.dyn-title`'s, so the slot measures like board_generic's title). */
export const TERM_TRACKING_EM = -0.015;
/** Size steps when fitting (board_generic's and textfit.py's 4 px). */
export const SIZE_STEP = 4;
/** The card's left border (the catalogue's `border.card`, 6 px) in the scene's structure border colour. */
export const BORDER_PX = Number(LAYOUT_TOKENS.border.card);
/** The label over a medium this mode cannot show (board_generic's `.dyn-media-missing` size, R-29 labels). */
export const MISSING_LABEL_PX = Object.freeze({ landscape: 52, portrait: 48 });

// Motion (§4.3, §4.8), seconds before the personality's scale
/** §4.3: "the card rises 24 px (400 ms)". */
export const CARD_RISE = 24;
export const CARD_ENTER = 0.4;
/** §4.8 entrance 400-600 ms (the pilots' 450 ms). */
export const TEXT_ENTER = 0.45;
/** The meaning follows the term (§4.3: the term first, then the meaning). */
export const MEANING_DELAY = 0.12;
/** §4.3 hook_question: words land 70 ms apart. */
export const WORD_STAGGER = 0.07;
/** The icon enters with the term. */
export const ICON_ENTER = 0.45;
/** §4.3 media_split: the medium scales 0.96 -> 1 over 500 ms, and enters just after the scene starts. */
export const MEDIA_ENTER = 0.5;
export const MEDIA_FROM = 0.96;
export const MEDIA_DELAY = 0.15;

/**
 * Per-format layout (stage px; B2 proposals, pending the owner's review). `term`: the term's size range (16:9 R-29's
 * heading range 84-120; 9:16 board_generic's title range 56-88, at least 600 by R-29's weight clause below 64 px), at
 * most `lines` lines with 1 preferred - a term is a few words, so a third line is a lint's business, not a layout's.
 * `meaning` / `example`: R-29's body ranges (16:9 48-56, 9:16 56-64) with **no line cap** (the pilots' rule: a row's
 * height, not a line count, is what has to fit, so an over-wide word that the page breaks never blocks preflight by
 * itself); an example row is the subordinate line, told apart by its `ink2` ink and its marker dot rather than by a
 * smaller size. `pad`: the card's padding on every side; `gutter`: what the 16:9 card keeps from the picture beside it
 * (with the regions' own 24 px); `termGap` / `rowGap`: term to meaning, meaning to the first example row and row to
 * row; `icon` / `iconGap`: the stored icon's size (16:9 the catalogue's feature icon beside the text, 9:16 its card
 * icon above the term) and its gap; `marker` / `dot`: the example rows' marker column and its dot (media_split's).
 */
export const LAYOUT = Object.freeze({
  landscape: Object.freeze({
    term: Object.freeze({ max: 120, min: 84, lines: 2, prefer: 1 }),
    meaning: Object.freeze({ max: 56, min: 48, lines: null }),
    example: Object.freeze({ max: 52, min: 48, lines: null }),
    meaningWeight: 400,
    exampleWeight: 400,
    pad: SPACE[5],
    gutter: SPACE[2],
    termGap: SPACE[3],
    rowGap: SPACE[3],
    icon: Number(LAYOUT_TOKENS.icon.feature_px),
    iconGap: SPACE[5],
    marker: 36,
    dot: 14,
  }),
  portrait: Object.freeze({
    term: Object.freeze({ max: 88, min: 56, lines: 2, prefer: 1 }),
    meaning: Object.freeze({ max: 64, min: 56, lines: null }),
    example: Object.freeze({ max: 60, min: 56, lines: null }),
    meaningWeight: 600,
    exampleWeight: 600,
    pad: SPACE[3],
    gutter: SPACE[2],
    termGap: SPACE[2],
    rowGap: SPACE[2],
    icon: Number(LAYOUT_TOKENS.icon.card_px),
    iconGap: SPACE[2],
    marker: 36,
    dot: 14,
  }),
});

// ---------------------------------------------------------------------------------------------
// What the scene shows (pure)
// ---------------------------------------------------------------------------------------------

/**
 * The line height of `text` in type role `role` (LINE_HEIGHT: Indic text takes the Indic value). Pure.
 * @param {'display' | 'body'} role @param {string} text
 */
export function lineHeight(role, text) {
  return INDIC.test(plainText(String(text || ''))) ? LINE_HEIGHT[role].indic : LINE_HEIGHT[role].latin;
}

/**
 * The scene's definition (its first `definition` item with a term or a text) and its example rows (every other item
 * with text, in board order; §4.3 gives one `bullet`, and a further one is drawn rather than dropped).
 * @param {TimedScene} scene
 * @returns {{term: BoardItem | null, rows: BoardItem[]}}
 */
export function cardItems(scene) {
  /** @type {BoardItem | null} */
  let term = null;
  /** @type {BoardItem[]} */
  const rows = [];
  for (const item of scene.board || []) {
    const text = `${typeof item.term === 'string' ? item.term : ''}${typeof item.text === 'string' ? item.text : ''}`.trim();
    if (!text) continue;
    if (item.kind === 'definition' && !term) term = item;
    else rows.push(item);
  }
  return { term, rows };
}

/** The scene's side panel when the layout shows it (null when there is none, or it is hidden). @param {TimedScene} scene */
export function shownPanel(scene) {
  const sp = scene.side_panel;
  return sp && scene.layout?.show_side_panel !== false ? sp : null;
}

/**
 * The picture or clip beside the card in 16:9 (board_generic's order: the scene's own medium, else a picture panel's),
 * laid out whether or not `mode` can show it, so the live player and the MP4 share one layout (`shown` false: the
 * panel's title is shown on the card surface instead). Null without one.
 * @param {TimedScene} scene @param {'live' | 'render'} mode
 * @returns {{slot: 'main' | 'panel', ref: MediaRef | null, start: number, shown: boolean, title: string} | null}
 */
export function pictureOf(scene, mode) {
  const m = mode === 'render' ? 'render' : 'live';
  if (scene.media) {
    return { slot: 'main', ref: scene.media, start: 0, shown: shownIn(scene.media, m), title: String(scene.title || '') };
  }
  const sp = shownPanel(scene);
  if (!sp || !PICTURE_PANELS.includes(String(sp.panel && sp.panel.kind))) return null;
  const title = String((sp.panel && sp.panel.title) || scene.title || '');
  return {
    slot: 'panel', ref: sp.media || null, start: Math.max(0, Number(sp.show_at) || 0),
    shown: !!sp.media && shownIn(sp.media, m), title,
  };
}

/**
 * Whether this template draws the scene whole in `format` (templates.py `_fit_definition_card` scores such a scene 0,
 * so direct.py never sends it here, B2c review CG-2): it needs a `definition` item, and in 9:16 there must be nothing
 * to show beside the card (the column holds the card alone), while in 16:9 a side panel must be a picture or a clip.
 * Anything else is drawn by board_generic, every hook handed on.
 * @param {TimedScene} scene @param {'landscape' | 'portrait'} format
 */
export function drawsScene(scene, format) {
  if (!cardItems(scene).term) return false;
  const sp = shownPanel(scene);
  if (format === 'portrait') return !sp && !scene.media;
  return !sp || PICTURE_PANELS.includes(String(sp.panel && sp.panel.kind));
}

/**
 * The layout variant: the look's own when its data is there, else derived (a picture gives `card_image_left`, a stored
 * icon for the definition item `card_icon_right`, else `centred`) - never a variant whose visual is missing (an icon
 * the schema check refuses counts as missing, as in list_build's B2c review V12).
 * @param {TimedScene} scene @param {'landscape' | 'portrait'} format @param {'live' | 'render'} mode @param {any} look
 * @returns {string}
 */
export function variantOf(scene, format, mode, look) {
  const stored = look && VARIANTS.includes(String(look.variant)) ? String(look.variant) : null;
  const picture = format === 'landscape' ? pictureOf(scene, mode) : null;
  const item = cardItems(scene).term;
  const icons = look && look.icons && typeof look.icons === 'object' ? look.icons : {};
  const icon = !!item && Object.prototype.hasOwnProperty.call(icons, item.id);
  if (stored === 'card_image_left' && picture) return stored;
  if (stored === 'card_icon_right' && icon) return stored;
  if (stored === 'centred') return stored;
  if (picture) return 'card_image_left';
  if (icon) return 'card_icon_right';
  return DEFAULT_VARIANT;
}

/**
 * The boxes of a format and variant (pure; whole stage px): `card` the region the card is laid out in (its width, and
 * the band its height is centred in) and `media` the region a picture is contained in (16:9 `card_image_left` only).
 * 9:16: the text column grown by the card's padding, from the headline's top to the text column's bottom.
 * @param {'landscape' | 'portrait'} format @param {string} variant @param {Zones} zones
 * @returns {{card: Box, media: Box | null}}
 */
export function boxes(format, variant, zones) {
  const at = { format };
  const L = LAYOUT[format];
  if (format === 'portrait') {
    const col = zones.textColumn;
    return { card: { x0: col.x0 - L.pad, y0: zones.title.y0, x1: col.x1 + L.pad, y1: col.y1 }, media: null };
  }
  if (variant === 'card_image_left') {
    const wide = region(at, 'left-55');
    const narrow = region(at, 'right-45');
    return { card: { ...narrow, x0: narrow.x0 + L.gutter }, media: wide };
  }
  if (variant === 'card_icon_right') return { card: region(at, 'full'), media: null };
  return { card: region(at, 'centre-60'), media: null };
}

/**
 * The width of the card's text column in `variant` (the card less its padding, less the 16:9 icon column). Pure.
 * @param {'landscape' | 'portrait'} format @param {string} variant @param {Box} card
 */
export function textWidth(format, variant, card) {
  const L = LAYOUT[format];
  const w = card.x1 - card.x0 - 2 * L.pad;
  return format === 'landscape' && variant === 'card_icon_right' ? w - L.icon - L.iconGap : w;
}

/**
 * This template's text slots (templates.py SlotSpec, what textfit.py fits in Python; plan §1.6, §1.11):
 * slot -> format -> variant -> {w, max_lines, size, role, face, weight, line_height, tracking_em, prefer_lines}. The
 * 16:9 variants are §4.3's three, and `default` is the narrowest of them (`card_image_left`), so a size stored without
 * knowing the variant is an upper bound for all of them; 9:16 has one column, so one `default`. Line heights are the
 * Latin ones (Indic text takes LINE_HEIGHT's Indic values, which textfit.py's Indic measure uses). Pure.
 * @param {Zones} landscape @param {Zones} portrait
 */
export function slotRows(landscape, portrait) {
  const L = LAYOUT.landscape;
  const P = LAYOUT.portrait;
  /** @param {'landscape' | 'portrait'} fmt @param {string} variant @param {Zones} zones */
  const w = (fmt, variant, zones) => textWidth(fmt, variant, boxes(fmt, variant, zones).card);
  /** @param {{max: number, min: number, lines: number, prefer: number}} s @param {number} width */
  const term = (s, width) => ({
    w: width, max_lines: s.lines, size: [s.min, s.max], role: 'title', face: 'display', weight: 700,
    line_height: LINE_HEIGHT.display.latin, tracking_em: TERM_TRACKING_EM, prefer_lines: s.prefer,
  });
  /** @param {{max: number, min: number, lines: number | null}} s @param {number} width @param {number} weight */
  const body = (s, width, weight) => ({
    w: width, max_lines: s.lines, size: [s.min, s.max], role: 'body', face: 'body', weight,
    line_height: LINE_HEIGHT.body.latin, tracking_em: 0, prefer_lines: null,
  });
  const land = {
    default: w('landscape', 'card_image_left', landscape),   // the narrowest: an upper bound for every variant
    card_icon_right: w('landscape', 'card_icon_right', landscape),
    card_image_left: w('landscape', 'card_image_left', landscape),
    centred: w('landscape', 'centred', landscape),
  };
  const port = w('portrait', 'centred', portrait);
  /** @param {(width: number) => any} make */
  const landMap = (make) => ({
    default: make(land.default), card_icon_right: make(land.card_icon_right),
    card_image_left: make(land.card_image_left), centred: make(land.centred),
  });
  return {
    term: { landscape: landMap((width) => term(L.term, width)), portrait: { default: term(P.term, port) } },
    meaning: {
      landscape: landMap((width) => body(L.meaning, width, L.meaningWeight)),
      portrait: { default: body(P.meaning, port, P.meaningWeight) },
    },
    // an example row's text starts after its marker column
    example: {
      landscape: landMap((width) => body(L.example, width - L.marker, L.exampleWeight)),
      portrait: { default: body(P.example, port - P.marker, P.exampleWeight) },
    },
  };
}

/**
 * Every TeX string this template converts for a scene (the static `tex` hook; render.js converts them before `ready`):
 * the `$...$` spans of the definition's term and meaning and of every example row, inline, in that order.
 * @param {TimedScene} scene
 * @returns {{tex: string, display: boolean}[]}
 */
export function texOf(scene) {
  const { term, rows } = cardItems(scene);
  /** @type {{tex: string, display: boolean}[]} */
  const out = [];
  /** @param {string | null | undefined} text */
  const add = (text) => {
    if (typeof text !== 'string' || !text.includes('$')) return;
    for (const tex of mathSpans(text)) if (tex.trim()) out.push({ tex, display: false });
  };
  if (term) {
    add(term.term);
    add(term.text);
  }
  for (const row of rows) add(row.text);
  return out;
}

// ---------------------------------------------------------------------------------------------
// Building
// ---------------------------------------------------------------------------------------------

/** @param {TemplateContext} ctx */
const isFallback = (ctx) => !!(ctx.view && ctx.view.dcFallback);

/**
 * One stacked text part of the card (the term, the meaning, an example row): its element, its word spans and the fit
 * its slot got.
 * @typedef {object} Part
 * @property {string} slot
 * @property {HTMLElement} el           the text element (what preflight measures)
 * @property {HTMLElement} box          the element the stack places (a row's marker and text)
 * @property {HTMLElement | null} mask
 * @property {HTMLElement[]} words
 * @property {boolean[]} tex
 * @property {number} lh
 * @property {number | null} lines      its own line cap (null: capped by the room there is, see `layout`)
 * @property {number | undefined} prefer
 * @property {{min: number, max: number}} range
 * @property {number} size
 * @property {number} lineOverflow
 * @property {number} overflow
 * @property {number} top
 * @property {number} alpha
 * @property {number} weight
 * @property {string} ink
 * @property {string} text
 * @property {string} role
 * @property {string | null} item       the board item the part belongs to
 * @property {number} enter
 */

/** @type {TemplateModule} */
export default {
  id: ID,

  /**
   * @param {TemplateContext} ctx
   */
  build(ctx) {
    const { scene, format, roles } = ctx;
    if (!drawsScene(scene, format)) {
      // a scene this template does not draw whole: board_generic draws it, every hook handed on
      const inner = boardGeneric.build(ctx);
      inner.dcFallback = true;
      return inner;
    }
    const L = LAYOUT[format];
    const portrait = format === 'portrait';
    const { term, rows } = cardItems(scene);
    const item = /** @type {BoardItem} */ (term);
    const variant = variantOf(scene, format, ctx.mode, ctx.look);
    const picture = variant === 'card_image_left' ? pictureOf(scene, ctx.mode) : null;
    const ink = roles.color('ink');
    const ink2 = roles.color('ink2');
    const surface = roles.color('surface');
    const masked = !!ctx.motion && ctx.motion.entrance === 'baseline_reveal';
    /** @type {Map<string, number>} */
    const enter = new Map(itemTimings(scene).map((x) => [x.id, x.enter]));
    const itemEnter = enter.get(item.id) || 0;

    const root = h('div', {
      class: ['dyn-dc', `dyn-dc--${format}`, `dyn-dc--${variant}`],
      style: { position: 'absolute', left: '0px', top: '0px', width: `${ctx.zones.width}px`, height: `${ctx.zones.height}px` },
    });

    // the card: the surface, its radius and shadow; its structure border goes on after it is made, so no content word
    // shares the statement with the structure role (§4.2, the source scan's struct-role rule)
    const card = h('div', {
      class: 'dyn-dc-card',
      style: {
        position: 'absolute', boxSizing: 'border-box', borderRadius: `${RADIUS.card}px`, background: surface,
        color: ink, padding: `${L.pad}px`, boxShadow: LAYOUT_TOKENS.shadow['2'], fontFamily: 'var(--dyn-font-body)',
      },
    });
    const structBorder = roles.color('struct.border');
    card.style.borderLeft = `${BORDER_PX}px solid ${structBorder}`;

    // the term (display face) and the meaning (body face), then the example rows
    /** @type {Part[]} */
    const parts = [];
    /** @param {HTMLElement} el @param {string} text */
    const fill = (el, text) => richWords(ctx, el, text);
    const termText = String(item.term || item.text || '');
    const termEl = h('h2', {
      class: 'dyn-dc-term', dir: 'auto',
      style: {
        margin: '0px', fontFamily: 'var(--dyn-font-display)', fontWeight: '700', color: ink,
        lineHeight: String(lineHeight('display', termText)), letterSpacing: `${TERM_TRACKING_EM}em`,
        overflowWrap: 'anywhere',
      },
    });
    const termWords = fill(termEl, termText);
    const termMask = masked ? revealMask(termEl) : null;
    parts.push({
      slot: 'term', el: termEl, box: termMask || termEl, mask: termMask, words: termWords.words, tex: termWords.tex,
      lh: lineHeight('display', termText), lines: L.term.lines, prefer: L.term.prefer,
      range: { min: L.term.min, max: L.term.max }, size: L.term.max, lineOverflow: 0, overflow: 0, top: 0, alpha: 0,
      weight: 700, ink, text: plainText(termText), role: 'title', item: item.id, enter: itemEnter,
    });

    const meaningText = item.term ? String(item.text || '') : '';
    /** @type {Part | null} */
    let meaning = null;
    if (meaningText) {
      const el = h('div', {
        class: 'dyn-dc-meaning', dir: 'auto',
        style: {
          fontWeight: String(L.meaningWeight), color: ink, lineHeight: String(lineHeight('body', meaningText)),
          overflowWrap: 'anywhere',
        },
      });
      const words = fill(el, meaningText);
      const mask = masked ? revealMask(el) : null;
      meaning = {
        slot: 'meaning', el, box: mask || el, mask, words: words.words, tex: words.tex,
        lh: lineHeight('body', meaningText), lines: L.meaning.lines, prefer: undefined,
        range: { min: L.meaning.min, max: L.meaning.max }, size: L.meaning.max, lineOverflow: 0, overflow: 0, top: 0,
        alpha: 0, weight: L.meaningWeight, ink, text: plainText(meaningText), role: 'body', item: item.id,
        enter: itemEnter + MEANING_DELAY,
      };
      parts.push(meaning);
    }

    /** @type {Part[]} */
    const exampleParts = rows.map((row) => {
      const text = String(row.text || '');
      const el = h('div', {
        class: 'dyn-dc-example-text', dir: 'auto',
        style: { flex: '1 1 auto', minWidth: '0px', fontWeight: String(L.exampleWeight), lineHeight: String(lineHeight('body', text)), overflowWrap: 'anywhere' },
      });
      const words = fill(el, text);
      const mask = masked ? revealMask(el) : null;
      const dot = h('span', {
        class: 'dyn-dc-dot', 'aria-hidden': 'true',
        style: { display: 'block', width: `${L.dot}px`, height: `${L.dot}px`, borderRadius: '50%', background: ink2 },
      });
      const marker = h('span', {
        class: 'dyn-dc-marker',
        style: { display: 'flex', alignItems: 'center', flex: 'none', width: `${L.marker}px`, height: `${lineHeight('body', text)}em` },
      }, dot);
      const box = h('div', {
        class: 'dyn-dc-example', dataset: { item: row.id },
        style: { display: 'flex', alignItems: 'flex-start', color: ink2 },
      }, marker, mask || el);
      return {
        slot: 'example', el, box, mask, words: words.words, tex: words.tex, lh: lineHeight('body', text),
        lines: L.example.lines, prefer: undefined, range: { min: L.example.min, max: L.example.max },
        size: L.example.max, lineOverflow: 0, overflow: 0, top: 0, alpha: 0, weight: L.exampleWeight, ink: ink2,
        text: plainText(text), role: 'body', item: row.id, enter: enter.get(row.id) || 0,
      };
    });
    parts.push(...exampleParts);

    // the card's text column, and (card_icon_right) the item's stored icon in its own dim group
    const textCol = h('div', { class: 'dyn-dc-text', style: { display: 'flex', flexDirection: 'column', minWidth: '0px' } },
      ...parts.map((p) => p.box));
    const textGroup = group('dyn-dc-textgroup', textCol);
    // the groups are the card's flex items (the stage owns their opacity, so the template puts its own parts inside
    // them): the text takes the room that is left, the icon column exactly its own
    Object.assign(textGroup.style, { flex: '1 1 auto', minWidth: '0px' });
    const drawnIcon = variant === 'card_icon_right' ? storedIcon(ctx, item.id, { size: L.icon, block: true }) : null;
    /** @type {HTMLElement | null} */
    let iconEl = null;
    /** @type {HTMLElement | null} */
    let iconGroup = null;
    if (drawnIcon) {
      iconEl = h('div', {
        class: 'dyn-dc-icon',
        style: { width: `${L.icon}px`, height: `${L.icon}px`, color: ink },
      }, drawnIcon);
      iconGroup = group('dyn-dc-icongroup', iconEl);
      Object.assign(iconGroup.style, {
        flex: 'none', width: `${L.icon}px`, height: `${L.icon}px`,
        marginBottom: portrait ? `${L.iconGap}px` : '0px', marginLeft: portrait ? '0px' : `${L.iconGap}px`,
      });
    }
    // 16:9 card_icon_right sets the icon beside the text, 9:16 above it
    const inner = h('div', {
      class: 'dyn-dc-inner',
      style: { display: 'flex', flexDirection: portrait ? 'column' : 'row', alignItems: portrait ? 'stretch' : 'center', height: '100%' },
    }, portrait ? [iconGroup, textGroup] : [textGroup, iconGroup]);
    card.appendChild(inner);
    root.appendChild(card);

    // the picture or clip (16:9 card_image_left), in its own dim group; its title on the card surface when this mode
    // cannot show it (board_generic's convention: never an empty box)
    /** @type {any} */
    let visual = null;
    if (picture) {
      const view = picture.shown && picture.ref ? ctx.media(picture.ref, { slot: picture.slot, start: picture.start, alt: picture.title }) : null;
      const frame = h('div', {
        class: 'dyn-dc-media',
        style: {
          position: 'absolute', overflow: 'hidden', borderRadius: `${RADIUS.media}px`,
          boxShadow: LAYOUT_TOKENS.shadow['3'], transformOrigin: '50% 50%',
        },
      }, view ? view.el : null);
      /** @type {HTMLElement | null} */
      let missing = null;
      if (!view || view.missing) {
        frame.style.background = surface;
        missing = h('div', {
          class: 'dyn-dc-missing', dir: 'auto', text: picture.title || LABELS.mediaMissing,
          style: {
            position: 'absolute', left: '0px', top: '0px', right: '0px', bottom: '0px', display: 'flex',
            alignItems: 'center', justifyContent: 'center', padding: '0 1em', textAlign: 'center',
            fontSize: `${MISSING_LABEL_PX[format]}px`, fontWeight: '600', lineHeight: '1.2', color: ink2,
            overflowWrap: 'anywhere',
          },
        });
        frame.appendChild(missing);
      }
      const el = group('dyn-dc-mediagroup', frame);
      Object.assign(el.style, { position: 'absolute', left: '0px', top: '0px', right: '0px', bottom: '0px' });
      root.appendChild(el);
      visual = {
        el, frame, view, missing, ref: picture.ref, clip: !!picture.ref && picture.ref.kind === 'video',
        start: picture.start > 0 ? picture.start : MEDIA_DELAY, alpha: 0,
      };
    }

    ctx.root.appendChild(root);
    // cue.term's targets: the definition's fields (its term, then its meaning) and each example row's text
    /** @type {Map<string, Part[]>} */
    const byId = new Map();
    byId.set(item.id, parts.filter((x) => x.item === item.id));
    for (const row of exampleParts) byId.set(String(row.item), [row]);
    return {
      root, variant, portrait, card, inner, textCol, textGroup, iconEl, iconGroup, visual,
      item, parts, term: parts[0], meaning, examples: exampleParts, byId,
      cardAlpha: 0, ink, ink2, surface,
    };
  },

  /**
   * Measure and fit (idempotent: every pass starts from the slot ranges): each part at the largest size whose text
   * keeps its line cap, then the stack shrunk (examples, meaning, term) while it is taller than the card's region;
   * then the card's own box, centred in that region, and the picture contained beside it. `update` only paints.
   * @param {TemplateContext} ctx
   */
  layout(ctx) {
    if (isFallback(ctx)) {
      if (typeof boardGeneric.layout === 'function') boardGeneric.layout(ctx);
      return;
    }
    const v = ctx.view;
    const { format, zones } = ctx;
    const L = LAYOUT[format];
    const b = boxes(format, v.variant, zones);
    const width = textWidth(format, v.variant, b.card);
    const regionH = b.card.y1 - b.card.y0;
    const iconRoom = v.iconEl && v.portrait ? L.icon + L.iconGap : 0;
    const avail = regionH - 2 * L.pad - iconRoom;
    v.textCol.style.width = `${width}px`;
    /** @type {Part[]} */
    const parts = v.parts;

    // each part on its own: the largest 4 px size whose text keeps its line cap (_shared.js fitLines). A part with no
    // cap of its own (the meaning, the example rows) is capped by the room there is: the most lines the card could
    // hold at that part's minimum, so `fitLines` reports an overflow only for text that cannot fit the card at all
    for (const p of parts) {
      const range = slotRange(ctx, p.slot, { min: p.range.min, max: p.range.max, role: p.role });
      p.range = range;
      const lines = p.lines === null ? Math.max(1, Math.floor(avail / (range.min * p.lh))) : p.lines;
      const spec = { max: range.max, min: range.min, lines, prefer: p.prefer, lh: p.lh, step: SIZE_STEP };
      const fit = fitLines(p.el, spec, p.words, p.tex);
      p.size = fit.size;
      p.lineOverflow = fit.overflow;
    }
    // the stack: the gap before each part, and the height of everything laid out now
    const gapBefore = (/** @type {number} */ i) => (i === 0 ? 0 : parts[i].slot === 'term' || parts[i - 1].slot === 'term' ? L.termGap : L.rowGap);
    const stackHeight = () => parts.reduce((a, p, i) => a + gapBefore(i) + p.box.offsetHeight, 0);
    // too tall: the example rows give way first, then the meaning, then the term (the headline keeps its size longest)
    const order = [...parts.filter((p) => p.slot === 'example'), ...parts.filter((p) => p.slot === 'meaning'),
      ...parts.filter((p) => p.slot === 'term')];
    for (let guard = 0; guard < 200 && stackHeight() > avail; guard++) {
      const next = order.find((p) => p.size > p.range.min);
      if (!next) break;
      next.size = Math.max(next.range.min, next.size - SIZE_STEP);
      next.el.style.fontSize = `${next.size}px`;
    }
    // the parts' places in the stack, and what is still past the bottom (reported through texts(), never clipped)
    let y = 0;
    parts.forEach((p, i) => {
      y += gapBefore(i);
      p.top = y;
      const height = p.box.offsetHeight;
      p.overflow = Math.max(0, p.top + height - avail, p.lineOverflow);
      p.box.style.marginTop = `${gapBefore(i)}px`;
      y += height;
    });
    // the card: its content's height, centred in its region, never above it. In 16:9 the icon stands beside the text,
    // so the card is at least as tall as the icon (a short definition must not let it spill out of the card)
    const beside = v.iconEl && !v.portrait ? L.icon : 0;
    const contentH = Math.max(0, y, beside) + iconRoom;
    const cardH = Math.round(contentH + 2 * L.pad);
    const cardY = Math.round(Math.max(b.card.y0, b.card.y0 + (regionH - cardH) / 2));
    Object.assign(v.card.style, {
      left: `${b.card.x0}px`, top: `${cardY}px`, width: `${Math.round(b.card.x1 - b.card.x0)}px`,
      height: `${cardH}px`,
    });
    // the picture: contained in its region at its own aspect, centred
    if (v.visual && b.media) {
      const r = containRect(mediaAspect(v.visual.ref), b.media);
      Object.assign(v.visual.frame.style, { left: `${r.x}px`, top: `${r.y}px`, width: `${r.w}px`, height: `${r.h}px` });
    }
  },

  /**
   * Paint scene time t: the card's entrance, the term, the meaning (its words one after another), the example rows,
   * the icon and the picture. A pure function of t: every property painted on an element is painted on every frame,
   * with a value (never '').
   * @param {TemplateContext} ctx
   * @param {number} t
   * @param {import('../../shared/types.js').SceneState} state
   */
  update(ctx, t, state) {
    if (isFallback(ctx)) {
      boardGeneric.update(ctx, t, state);
      return;
    }
    const v = ctx.view;
    const p = ctx.paint;
    // the card rises and fades in (§4.3)
    const ce = enterAt(ctx, t, 0, CARD_ENTER);
    v.cardAlpha = ce;
    paintRise(ctx, v.card, ce, CARD_RISE);
    p.style(v.card, 'visibility', ce > 0.001 ? 'visible' : 'hidden');
    // the term and the meaning
    const stagger = seconds(ctx, WORD_STAGGER);
    for (const part of /** @type {Part[]} */ (v.parts)) {
      if (part.slot === 'meaning' && !part.mask && part.words.length) {
        // the meaning's words land one after another (§4.3); its alpha is the last word's, so it reads as settled
        // only once every word is in, and its visibility the first word's
        let first = 0;
        let last = 0;
        part.words.forEach((word, i) => {
          const e = enterAt(ctx, t, part.enter + i * stagger, TEXT_ENTER);
          paintRise(ctx, word, e, TEXT_RISE);
          if (i === 0) first = e;
          last = e;
        });
        part.alpha = last;
        p.style(part.el, 'visibility', first > 0.001 ? 'visible' : 'hidden');
        continue;
      }
      const e = enterAt(ctx, t, part.enter, TEXT_ENTER);
      part.alpha = e;
      paintTextEntrance(ctx, part.el, part.mask, e);
      p.style(part.box, 'visibility', e > 0.001 ? 'visible' : 'hidden');
    }
    // the icon enters with the term
    if (v.iconEl) {
      const e = enterAt(ctx, t, v.term.enter, ICON_ENTER);
      paintRise(ctx, v.iconEl, e, CARD_RISE);
      p.style(v.iconEl, 'visibility', e > 0.001 ? 'visible' : 'hidden');
    }
    // the picture: a fade and a 0.96 -> 1 scale about its centre; a clip only fades (B2c deviation 28)
    if (v.visual) {
      const vis = v.visual;
      const e = enterAt(ctx, t, vis.start, MEDIA_ENTER);
      vis.alpha = e;
      p.style(vis.frame, 'opacity', fx(e));
      p.style(vis.frame, 'visibility', e > 0.001 ? 'visible' : 'hidden');
      const scale = ctx.reducedMotion || vis.clip || e >= 1 ? 1 : MEDIA_FROM + (1 - MEDIA_FROM) * e;
      p.style(vis.frame, 'transform', scale === 1 ? 'none' : `scale(${fx(scale, 4)})`);
      // painted every frame, also while hidden, so the element never keeps a frame from an earlier seek
      if (vis.view) paintMedia(ctx, vis.view, t);
    }
  },

  /**
   * Where a look_here points (plan §1.4, §1.5, §1.7): `<item>` (or `<item>:term:<n>`) the definition's card, an
   * example row's text, and `panel` the picture; its box as painted now (read after `update`) on whole stage px. The
   * card and the picture are shapes, so their outline is drawn round them (cues.js shapeArea, B2c review CG-1); a row
   * is text. Null for anything else, or for something not on screen.
   * @param {TemplateContext} ctx
   * @param {string} target
   * @returns {FocusRect | null}
   */
  focusRect(ctx, target) {
    if (isFallback(ctx)) return typeof boardGeneric.focusRect === 'function' ? boardGeneric.focusRect(ctx, target) : null;
    const v = ctx.view;
    const parsed = parseTarget(target);
    if (!parsed) return null;
    /** @type {Element | null} */
    let el = null;
    /** @type {Element | null} */
    let holder = null;
    let radius = RADIUS.card;
    let kind = /** @type {'text' | 'shape'} */ ('shape');
    if (parsed.form === 'panel') {
      el = v.visual && v.visual.alpha > 0.01 ? v.visual.frame : null;
      holder = v.visual ? v.visual.el : null;
      radius = RADIUS.media;
    } else if (parsed.form === 'item' || parsed.form === 'item:term') {
      const id = String(parsed.item);
      if (v.item.id === id) {
        el = v.cardAlpha > 0.01 ? v.card : null;
        holder = v.textGroup;
      } else {
        const row = v.examples.find((/** @type {Part} */ x) => x.item === id);
        el = row && row.alpha > 0.01 ? row.box : null;
        holder = v.textGroup;
        radius = SPACE[0];
        kind = 'text';
      }
    }
    const r = el ? ctx.rectOf(el) : null;
    if (!r || !(r.w > 0 && r.h > 0)) return null;
    const x0 = Math.round(r.x);
    const y0 = Math.round(r.y);
    const x1 = Math.round(r.x + r.w);
    const y1 = Math.round(r.y + r.h);
    return {
      rect: { x: x0, y: y0, w: x1 - x0, h: y1 - y0 }, shape: 'rect', radius,
      el: /** @type {Element} */ (holder || el), kind,
    };
  },

  /**
   * The elements holding an item's text, where cue.term finds its [[term]] spans (`.rt-k`, `_shared.js richWords`):
   * the definition's fields in the item's field order (its term, then its meaning), or an example row's text. Null for
   * anything else.
   * @param {TemplateContext} ctx
   * @param {string} itemId
   * @returns {Element | Element[] | null}
   */
  itemEl(ctx, itemId) {
    if (isFallback(ctx)) return typeof boardGeneric.itemEl === 'function' ? boardGeneric.itemEl(ctx, itemId) : null;
    const found = ctx.view.byId.get(String(itemId));
    if (!found || !found.length) return null;
    return found.length === 1 ? found[0].el : found.map((/** @type {Part} */ x) => x.el);
  },

  /**
   * The content groups a look_here dims (all but the target's): the card's text, the icon and the picture. The same
   * elements at every t; this template never writes their opacity (it animates the parts inside them).
   * @param {TemplateContext} ctx
   * @returns {Element[]}
   */
  dimGroups(ctx) {
    if (isFallback(ctx)) return typeof boardGeneric.dimGroups === 'function' ? boardGeneric.dimGroups(ctx) : [];
    const v = ctx.view;
    return [v.textGroup, v.iconGroup, v.visual ? v.visual.el : null].filter(Boolean);
  },

  /**
   * The morph anchor (plan §1.4): none yet (B2f pairs the templates that have one).
   * @param {TemplateContext} _ctx
   * @returns {null}
   */
  anchor(_ctx) {
    return null;
  },

  /**
   * Static: the box the scene's picture or clip is extracted at (plan §1.4): the 16:9 `left-55` region it is contained
   * in, the largest box this layout ever shows it in (its entrance only scales it down, §P, and this template never
   * pushes, so it needs no push headroom). Null in 9:16 (no picture is drawn) and for a scene drawn by board_generic.
   * @param {TimedScene} scene
   * @param {Zones} zones
   * @param {'live' | 'render'} mode
   * @param {string | undefined} slot
   * @param {import('../core.js').Fixes} fixes
   * @returns {{w: number, h: number} | null}
   */
  mediaBox(scene, zones, mode, slot, fixes) {
    if (!drawsScene(scene, zones.format)) {
      return typeof boardGeneric.mediaBox === 'function' ? boardGeneric.mediaBox(scene, zones, mode, slot, fixes) : null;
    }
    if (zones.format !== 'landscape') return null;
    const variant = variantOf(scene, 'landscape', mode, scene.look);
    const box = boxes('landscape', variant, zones).media;
    if (!box) return null;
    return { w: box.x1 - box.x0, h: box.y1 - box.y0 };
  },

  /**
   * Static: every TeX string this template renders for the scene (`texOf`; the same in both formats).
   * @param {TimedScene} scene
   * @param {import('../../shared/types.js').ResolvedLook | null} look
   * @param {'landscape' | 'portrait'} fmt
   */
  tex(scene, look, fmt) {
    if (!drawsScene(scene, fmt)) return typeof boardGeneric.tex === 'function' ? boardGeneric.tex(scene, look, fmt) : [];
    return texOf(scene);
  },

  /**
   * The text on screen (preflight, §7.12): the term (slot `term`), the meaning (`meaning`) and each example row
   * (`example`), critical, in the ink, size and weight painted now, on the card's `surface` (every text of this
   * template sits on the card, so no scrim is involved), plus a missing picture's label (not critical).
   * @param {TemplateContext} ctx
   * @returns {TextEntry[]}
   */
  texts(ctx) {
    if (isFallback(ctx)) return typeof boardGeneric.texts === 'function' ? boardGeneric.texts(ctx) : [];
    const v = ctx.view;
    /** @type {TextEntry[]} */
    const out = [];
    for (const part of /** @type {Part[]} */ (v.parts)) {
      out.push({
        el: part.el, role: part.role, slot: part.slot, critical: true, ink: part.ink, bg: v.surface,
        alpha: Math.min(part.alpha, v.cardAlpha), overflow: part.overflow, size: part.size, min: part.range.min,
        weight: part.weight, text: part.text,
      });
    }
    if (v.visual && v.visual.missing) {
      out.push({
        el: v.visual.missing, role: 'label', critical: false, ink: v.ink2, bg: v.surface, alpha: v.visual.alpha,
        size: MISSING_LABEL_PX[ctx.format], weight: 600, text: v.visual.missing.textContent || '',
      });
    }
    return out;
  },
};
