// @ts-check
/**
 * `full_bleed_media` (DYNAMIC_VIDEO_DESIGN §4.3 B, R1, class `media`; forced for an `ai_video` scene): the clip or
 * picture fills the frame and the scene's words sit over it as a lower third.
 *
 * Data (§4.3, templates.py `_fit_full_bleed_media`): an `ai_video` scene (its clip, or the still the asset stage made
 * from `fallback_image_prompt`), or a board scene with an application image under a lower-third label. What the lower
 * third shows, in this order (`rowsOf`, nothing dropped): the scene's **title** as the keyword label (§4.3 "a
 * lower-third keyword label (<= 4 words)"), then the scene's `subtitle` and the one text item of the board as
 * sub-lines; without a title the text item is the label itself. A scene this template cannot draw whole - no medium of
 * its own at all (`compose/timeline.py` writes an `ai_video` without footage or still as a text board, reason
 * `ai_video_without_media`), more than one text item, a board item that is not text, or a quiz - is handed to
 * `board_generic` whole, every hook with it (`drawsScene`, list_build's convention, B2c review CG-2): a 1,200-character
 * visual description is a board, not a label.
 *
 * Layout (§2.5 named regions and zones from the catalogue, core.js REGIONS / ZONES; whole stage px; `mediaRegion`,
 * `labelBox`):
 * - **The medium is the frame**: its box is the whole stage (0, 0, width, height), outside the critical box on every
 *   side, which is what "full bleed" means. It is the only template whose medium leaves the safe area; the text over
 *   it stays inside the critical boxes (R-59).
 * - **The lower third** is the bottom third of the frame intersected with what text may use: 16:9 y 720-912 (the
 *   critical box ends at 912, the caption band starts at 924), 9:16 y 840-1032 (the `text-below` region, ending 8 px
 *   above the caption band, the catalogue's `zones.caption.y0`: B2c review CG-3, never a literal). Its width is the
 *   critical box in 16:9 (x 96-1824) and, in 9:16, x 192-780: the part both critical bands hold, so a block that grows
 *   past y 840 is still inside the safe area (preflight's rule: every band the box reaches into must hold its width).
 *   The rows are bottom-anchored (a lower third grows upward, never downward into the caption band) and never clipped:
 *   the third is the room the fit aims at (the sub-lines give up size first, the label last), the critical area the
 *   room a block that still does not fit may use, and only what is past *that* is reported through `texts()` as
 *   overflow, so preflight shrinks and then blocks with "Shorten text" (plan §2) - never for a block that is only a
 *   little taller than a third while staying safe and legible.
 * - **The scrim** (§4.3 "over a 55% scrim", §7.12 "text sits on a card surface, on a scrim with >= 55% opacity, ...")
 *   is the scene's `scrim` role (the catalogue's scrim alpha is 0.78, above the 55% floor), a band across the full
 *   frame width from the block's top padding to the bottom of the frame. It is always on - the text sits on a picture
 *   whose pixels no analytic check can see - so preflight's scrim remedy for these slots is already satisfied, and
 *   `texts()` reports the colour the text really sits on: the scrim composited over the **darkest and the lightest
 *   medium** (`mediaBg`, `_shared.js scrimmedBg` over `#000000` / `#FFFFFF`), the worse of the two against the ink
 *   (R-53's worst case), never the page background, which the medium covers.
 * - Line heights are the type scale's, per script (§4.6, plan §2 R-29): the label the display role's (Latin 1.05,
 *   Indic >= 1.25), the sub-lines the body role's (1.35 / 1.45) - Tamil and Devanagari marks need the room (B2c
 *   review V5). In 9:16 a sub-line is set at weight 700 (R-29: at most 64 px means at least weight 600).
 *
 * Motion (§4.3, §4.8; every duration times the personality's scale, `_shared.js seconds` / `enterAt`):
 * - The medium **fades** in over MEDIA_ENTER and is never scaled and never pushed: a clip scaled by its entrance was
 *   measured order-dependent (B2c, `tests/determinism/test_template_clips.py`; cues.js `showsClip`), and a still's own
 *   move is Ken Burns, which `MediaRef.ken_burns` carries and `media.js` paints (1.00 -> 1.06, §4.3; the resolver
 *   stores it, this template never animates the picture itself). So the spec is **not** `push_ok`.
 * - The scrim and the label enter together with the personality's text entrance (Crisp reveals from the baseline
 *   inside a mask, Soft fades in with a 16 px rise), the label on its own beat when a text item is the label
 *   (state.js `itemTimings`: a `reveal` event wins, else the beat, R-17's 120 ms lead), else LABEL_DELAY after the
 *   scene starts; each sub-line follows ROW_DELAY later. Nothing leaves: the lower third holds to the end of the
 *   scene. Reduced motion (live only): fades in place.
 *
 * The medium's `fit` (§2.5, §4.3 "in 9:16 it is cropped to its focus point or shown `contain_blur`"): a medium whose
 * aspect matches the frame covers it exactly, and in 16:9 the medium covers the frame (`cover`); in 9:16 a medium of
 * another aspect is shown `contain_blur` - contained over a blurred, darkened copy of its own frame (media.js
 * BACKDROP_WIDTH), the form B2c measured order-independent for both a still and a clip
 * (`tests/determinism/test_kenburns_determinism.py`). `shownFit` decides it and `build` hands `ctx.media` the ref with
 * that fit: the frame key of a clip is (kind, asset key, size) only, and a `contain_blur` medium draws its backdrop
 * from the frame it already shows, so no other frame or size is asked for than the ones `mediaPlan` lists.
 * `MediaRef.focus` (the cover focus point) is not drawn by media.js and stays an open item (`b2crit_engineering`).
 *
 * Emphasis is cues.js's (§4.2, plan §1.4/§1.5): this spec consumes `reveal` only, so no cue is drawn here today;
 * `itemEl` (the text item's fields, their `[[term]]` `.rt-k` spans kept by `_shared.js richWords`), `focusRect` (the
 * frame as a shape, a row as text) and `dimGroups` (the medium, the lower third) are implemented so a `cue_term` or
 * `look_here` works the moment the spec declares it. This template draws no emphasis, writes no `outline`,
 * `text-decoration`, `filter`, `backdrop-filter` or `will-change`, and gives no content element a structure role
 * (§4.2).
 *
 * Determinism (§7.4, plan §P): `update` is a pure function of t, every property it paints gets a value on every frame
 * (never ''), translates are whole pixels and 2D, and every size decision happens in `layout`, from the laid-out page.
 *
 * The sizes, the gaps, the always-on scrim and the lower-third box are B2 proposals pending the owner's review (the
 * design gives the full bleed, the 55% scrim, the <= 4-word label, Ken Burns and the 3-12 s timing):
 * `aadhi/compose/dynamic/templates.py` mirrors LAYOUT as this template's slots (`slotRows`), and a test pins the two
 * together.
 */

import { h } from '../../shared/dom.js';
import { mathSpans } from '../../player/richtext.js';
import { splitWords } from '../../motion/text.js';
import { ZONES, fx, parseTarget } from '../core.js';
import { shownIn } from '../media.js';
import { itemTimings } from '../state.js';
import boardGeneric from './board_generic.js';
import {
  SPACE, enterAt, fitLines, group, mediaAspect, paintMedia, paintTextEntrance, region, revealMask, richWords,
  scrimmedBg, shownPanel, slotRange,
} from './_shared.js';

/** @typedef {import('./types.js').TemplateContext} TemplateContext */
/** @typedef {import('./types.js').TemplateModule} TemplateModule */
/** @typedef {import('./types.js').FocusRect} FocusRect */
/** @typedef {import('./types.js').TextEntry} TextEntry */
/** @typedef {import('../../shared/types.js').TimedScene} TimedScene */
/** @typedef {import('../../shared/types.js').BoardItem} BoardItem */
/** @typedef {import('../../shared/types.js').MediaRef} MediaRef */
/** @typedef {import('../../shared/types.js').SceneState} SceneState */
/** @typedef {import('../core.js').Box} Box */
/** @typedef {import('../core.js').Zones} Zones */

export const ID = 'full_bleed_media';

/** UI labels (kept together for localisation). */
export const LABELS = Object.freeze({ mediaMissing: 'Picture unavailable' });

/** The board item kinds drawn as text (templates.py TEXT_KINDS, media_split's list: a test pins the two equal). */
export const TEXT_KINDS = Object.freeze(['heading', 'bullet', 'paragraph', 'definition', 'takeaway', 'callout_info',
  'callout_tip', 'callout_warning']);

/**
 * Per-format layout (stage px; B2 proposals, pending the owner's review).
 * - `label`: the keyword label, display face at weight 700 (16:9 64-96, 9:16 56-88; R-29's headings are 84-120 in
 *   16:9 and its 9:16 hook band is 88-104, both above the floors), one line preferred and two allowed (§4.3 asks for
 *   <= 4 words; R-25 asks for one line, B2c's reading pending the owner).
 * - `sub`: a sub-line, body face (16:9 40-52 at weight 600, 9:16 44-56 at weight 700: R-29's 9:16 rule is weight
 *   >= 600 at or below 64 px), at most 2 lines each.
 * - `gap`: label to first sub-line and sub-line to sub-line; `padX` / `padY`: how far the scrim reaches past the text
 *   (the spacing scale's steps).
 */
export const LAYOUT = Object.freeze({
  landscape: Object.freeze({
    label: Object.freeze({ max: 96, min: 64, lines: 2, prefer: 1 }),
    sub: Object.freeze({ max: 52, min: 40, lines: 2 }),
    subWeight: 600,
    gap: SPACE[1],
    padX: SPACE[2],
    padY: SPACE[3],
  }),
  portrait: Object.freeze({
    label: Object.freeze({ max: 88, min: 56, lines: 2, prefer: 1 }),
    sub: Object.freeze({ max: 56, min: 44, lines: 2 }),
    subWeight: 700,
    gap: SPACE[0],
    padX: SPACE[2],
    padY: SPACE[2],
  }),
});

/**
 * Line heights (§4.6 type scale, the catalogue's `type.scale`; a test pins them): the label the display role's, the
 * sub-lines the body role's; Indic text (Devanagari to Sinhala) the Indic minimums (plan §2 R-29).
 */
export const LINE_HEIGHT = Object.freeze({
  display: Object.freeze({ latin: 1.05, indic: 1.25 }),
  body: Object.freeze({ latin: 1.35, indic: 1.45 }),
});
/** Indic scripts (Devanagari to Sinhala), whose text takes the taller line heights (the pilots' rule). */
const INDIC = new RegExp(`[${['Devanagari', 'Bengali', 'Gurmukhi', 'Gujarati', 'Oriya', 'Tamil', 'Telugu', 'Kannada',
  'Malayalam', 'Sinhala'].map((x) => `\\p{Script=${x}}`).join('')}]`, 'u');

/**
 * The line height of `text` in role `role` (LINE_HEIGHT: Indic text takes the Indic value). Pure.
 * @param {'display' | 'body'} role @param {string} text
 */
export function lineHeight(role, text) {
  return INDIC.test(String(text || '')) ? LINE_HEIGHT[role].indic : LINE_HEIGHT[role].latin;
}

/** The label's weight and tracking (dynamic.css `.dyn-title`'s tracking, so the slot measures like board_generic's). */
export const LABEL_WEIGHT = 700;
export const LABEL_TRACKING_EM = -0.015;
/** Size steps when fitting (board_generic's and textfit.py's 4 px). */
export const SIZE_STEP = 4;
/** The label over a medium this mode cannot show (board_generic's `.dyn-media-missing` size, R-29 labels). */
export const MISSING_LABEL_PX = Object.freeze({ landscape: 52, portrait: 48 });
/** A medium whose aspect differs from the frame's by less than this covers it exactly (rounding only). */
export const ASPECT_EPS = 0.01;

// Motion (§4.3, §4.8), seconds before the personality's scale
/** The medium fades in over this (media_split's MEDIA_ENTER; never a scale: see the module comment). */
export const MEDIA_ENTER = 0.5;
/** The lower third enters this long after the scene starts when no beat reveals it (media_split's TITLE_DELAY). */
export const LABEL_DELAY = 0.08;
/** §4.8 text entrance 400-600 ms (list_build's and media_split's 450 ms). */
export const TEXT_ENTER = 0.45;
/** A sub-line follows the label by this (§4.8: 60-120 ms per item). */
export const ROW_DELAY = 0.12;

// ---------------------------------------------------------------------------------------------
// What the scene shows (pure)
// ---------------------------------------------------------------------------------------------

/**
 * The scene's medium (board_generic's order): its own clip or picture (an `ai_video`'s footage or fallback still),
 * else a side panel's. `shown`: whether `mode` can show it (media.js shownIn: the render page leaves out live-only
 * GIFs, media kept out of the MP4 and unsafe URLs). The medium is laid out whether or not the mode shows it, so the
 * live player and the MP4 share one layout. Null without one.
 * @param {TimedScene} scene
 * @param {'live' | 'render'} [mode]
 * @returns {{slot: 'main' | 'panel', ref: MediaRef, start: number, shown: boolean} | null}
 */
export function mediaOf(scene, mode = 'live') {
  const m = mode === 'render' ? 'render' : 'live';
  if (scene.media) return { slot: 'main', ref: scene.media, start: 0, shown: shownIn(scene.media, m) };
  const sp = scene.side_panel;
  if (sp && sp.media && scene.layout?.show_side_panel !== false) {
    return { slot: 'panel', ref: sp.media, start: Math.max(0, Number(sp.show_at) || 0), shown: shownIn(sp.media, m) };
  }
  return null;
}

/**
 * The text items of the board, in board order (TEXT_KINDS).
 * @param {TimedScene} scene
 * @returns {BoardItem[]}
 */
export function textItemsOf(scene) {
  return (scene.board || []).filter((i) => TEXT_KINDS.includes(String(i.kind)));
}

/**
 * Whether this template draws the scene whole (templates.py `_fit_full_bleed_media` scores such a scene 0 for a
 * board, and an `ai_video` whose medium the asset stage could not make arrives as a text board, which it hands on):
 * there must be a medium (in either mode: `tex()` has no mode, so this predicate has none either - a medium the mode
 * cannot show is drawn as a missing medium, board_generic's convention, never as an empty box), the board must be at
 * most one text item and nothing else, and the scene must not be a quiz (`check`'s data). Anything else is drawn by
 * board_generic, every hook handed on.
 *
 * A **side panel** the scene shows is only ever drawn here as the medium itself (a board scene's application picture,
 * `mediaOf` slot `panel`): the lower third holds the scene's own words and nothing else, and this template lays out no
 * second visual. So a scene whose shown panel is *not* the medium - an `ai_video` with its own footage beside a
 * terminal, chart or picture card - is board_generic's whole: board_generic draws that card, and dropping it would
 * break "never drop content" (§7.12, B2c review CG-2). `_fit_full_bleed_media` refuses the same scene, so the forced
 * `ai_video` row falls through instead of planning a template the page would hand back.
 * @param {TimedScene} scene
 */
export function drawsScene(scene) {
  const media = mediaOf(scene);
  if (!media) return false;
  if (scene.quiz) return false;
  if (shownPanel(scene) && media.slot !== 'panel') return false;
  const board = scene.board || [];
  return board.length === textItemsOf(scene).length && board.length <= 1;
}

/**
 * One row of the lower third: its slot (`label`, `sub`), its text, whether the text is rich-lite (a board item's text
 * is; a title or subtitle is plain), and the board item it belongs to (null: none).
 * @typedef {object} Row
 * @property {'label' | 'sub'} slot
 * @property {string} text
 * @property {boolean} rich
 * @property {string | null} item
 */

/**
 * The lower third's rows, top to bottom (pure): the scene's title as the keyword label, then its subtitle and the
 * board's one text item as sub-lines; without a title the text item is the label (and the subtitle its sub-line).
 * Nothing the scene carries is left out.
 * @param {TimedScene} scene
 * @returns {Row[]}
 */
export function rowsOf(scene) {
  const title = String(scene.title || '').trim();
  const subtitle = String(scene.subtitle || '').trim();
  const item = textItemsOf(scene)[0] || null;
  const itemText = item ? `${item.kind === 'definition' && item.term ? `${item.term} ` : ''}${item.text || ''}`.trim() : '';
  /** @type {Row[]} */
  const out = [];
  if (title) out.push({ slot: 'label', text: title, rich: false, item: null });
  if (itemText) out.push({ slot: out.length ? 'sub' : 'label', text: itemText, rich: true, item: item ? item.id : null });
  if (subtitle) out.push({ slot: out.length ? 'sub' : 'label', text: subtitle, rich: false, item: null });
  return out;
}

/**
 * The box the medium fills: the whole frame (full bleed, §4.3). Pure.
 * @param {Zones} zones
 * @returns {Box}
 */
export function mediaRegion(zones) {
  return { x0: 0, y0: 0, x1: zones.width, y1: zones.height };
}

/**
 * The lower third's box (pure; whole stage px): the bottom third of the frame, bounded by what text may use - 16:9 the
 * critical box (x 96-1824, ending y 912); 9:16 the `text-below` region from the headline's left edge (x 192-780: the
 * width both critical bands hold), ending 8 px above the caption band. The rows are bottom-anchored inside it and may
 * grow above its top (still inside the critical area) rather than be clipped; `texts()` reports the excess.
 * @param {'landscape' | 'portrait'} format
 * @param {Zones} zones
 * @returns {Box}
 */
export function labelBox(format, zones) {
  const at = { format };
  if (format === 'portrait') {
    const below = region(at, 'text-below');
    const headline = region(at, 'headline');
    return { x0: headline.x0, y0: below.y0, x1: below.x1, y1: ZONES.portrait.caption.y0 - SPACE[0] };
  }
  const full = region(at, 'full');
  const third = zones.height - Math.round(zones.height / 3);
  return { x0: full.x0, y0: Math.max(full.y0, third), x1: full.x1, y1: full.y1 };
}

/**
 * The top the rows may reach when they do not fit the lower third (the critical area's top: 16:9 the critical box's,
 * 9:16 the upper band's, whose width holds the 9:16 label box). Pure.
 * @param {'landscape' | 'portrait'} format
 */
export function textCeiling(format) {
  const bands = ZONES[format].critical;
  return Math.min(...bands.map((b) => b.y0));
}

/**
 * The `fit` the medium is shown with (see the module comment): `cover` when its aspect matches the frame's (it fills
 * the frame exactly) or in 16:9 (§4.3 "the media covers the frame"), `contain_blur` in 9:16 for any other aspect
 * (§4.3, §2.5). Pure.
 * @param {MediaRef | null | undefined} ref
 * @param {Zones} zones
 * @returns {'cover' | 'contain_blur'}
 */
export function shownFit(ref, zones) {
  const frame = zones.height > 0 ? zones.width / zones.height : 16 / 9;
  const aspect = mediaAspect(ref);
  if (Math.abs(aspect - frame) <= ASPECT_EPS) return 'cover';
  return zones.format === 'portrait' ? 'contain_blur' : 'cover';
}

/**
 * How far above the frame a picture's own Ken Burns zooms it (`MediaRef.ken_burns`, player/kenburns.js: the larger of
 * its start and end scale, and 1.15 for an end without one, as `kenBurnsAt` reads it), so `mediaBox` extracts the
 * picture large enough that it only ever downscales (§P condition 1). 1 for a clip and for a picture without
 * Ken Burns. Pure.
 * @param {MediaRef | null | undefined} ref
 */
export function kenBurnsScale(ref) {
  const kb = ref && ref.kind !== 'video' ? ref.ken_burns : null;
  if (!kb) return 1;
  /** @param {unknown} v @param {number} dflt */
  const num = (v, dflt) => (typeof v === 'number' && Number.isFinite(v) ? v : dflt);
  return Math.max(1, num(kb.start && kb.start.scale, 1), num(kb.end && kb.end.scale, 1.15));
}

/**
 * The colour the lower third's text really sits on: the scene's scrim composited over the darkest and the lightest
 * medium, the worse of the two against `ink` (R-53's worst case; `_shared.js scrimmedBg`). The page background is
 * never reported here - the medium covers it. Pure.
 * @param {string} scrim
 * @param {string} ink
 */
export function mediaBg(scrim, ink) {
  return scrimmedBg(scrim, { bg: '#000000', bgs: ['#000000', '#FFFFFF'] }, ink);
}

/**
 * Every TeX string this template converts for a scene (the static `tex` hook; render.js converts them before
 * `ready`): the `$...$` spans of the rich rows (the board item's text), in row order. A title or subtitle is plain
 * text.
 * @param {TimedScene} scene
 * @returns {{tex: string, display: boolean}[]}
 */
export function texOf(scene) {
  /** @type {{tex: string, display: boolean}[]} */
  const out = [];
  for (const row of rowsOf(scene)) {
    if (!row.rich || !row.text.includes('$')) continue;
    for (const tex of mathSpans(row.text)) if (tex.trim()) out.push({ tex, display: false });
  }
  return out;
}

/**
 * This template's text slots as `aadhi/compose/dynamic/templates.py` holds them (plan §1.6 SlotSpec: one `default`
 * variant per format, since §4.3 gives this template no variant). The jsdom test pins the catalogue's rows to these,
 * so the page and the catalogue can never drift. Pure.
 * @param {Zones} landscape
 * @param {Zones} portrait
 */
export function slotRows(landscape, portrait) {
  /** @param {'landscape' | 'portrait'} fmt @param {Zones} zones */
  const rows = (fmt, zones) => {
    const L = LAYOUT[fmt];
    const box = labelBox(fmt, zones);
    const w = box.x1 - box.x0;
    return {
      label: {
        w, max_lines: L.label.lines, size: [L.label.min, L.label.max], role: 'title', face: 'display',
        weight: LABEL_WEIGHT, line_height: LINE_HEIGHT.display.latin, prefer_lines: L.label.prefer,
        tracking_em: LABEL_TRACKING_EM,
      },
      sub: {
        w, max_lines: L.sub.lines, size: [L.sub.min, L.sub.max], role: 'label', face: 'body', weight: L.subWeight,
        line_height: LINE_HEIGHT.body.latin, prefer_lines: null, tracking_em: 0,
      },
    };
  };
  const land = rows('landscape', landscape);
  const port = rows('portrait', portrait);
  return {
    label: { landscape: { default: land.label }, portrait: { default: port.label } },
    sub: { landscape: { default: land.sub }, portrait: { default: port.sub } },
  };
}

// ---------------------------------------------------------------------------------------------
// Building
// ---------------------------------------------------------------------------------------------

/** True once `build` handed the scene to board_generic (every hook then hands on too). @param {any} ctx */
const isFallback = (ctx) => !!(ctx.view && ctx.view.fbmFallback);

/**
 * Plain text as word spans (Intl.Segmenter units, so Indic clusters stay whole: board_generic's title words).
 * @param {HTMLElement} host @param {string} text @param {string} locale
 * @returns {HTMLElement[]}
 */
function wordSpans(host, text, locale) {
  /** @type {HTMLElement[]} */
  const out = [];
  for (const part of splitWords(text, locale)) {
    const span = h('span', { class: 'dyn-word', text: part.text });
    out.push(span);
    host.append(span, part.ws ? document.createTextNode(part.ws) : '');
  }
  return out;
}

/** @type {TemplateModule} */
export default {
  id: ID,

  /**
   * @param {TemplateContext} ctx
   */
  build(ctx) {
    const { scene, format, roles } = ctx;
    if (!drawsScene(scene)) {
      // a scene this template does not draw whole: board_generic draws it, every hook handed on
      const inner = boardGeneric.build(ctx);
      inner.fbmFallback = true;
      return inner;
    }
    const L = LAYOUT[format];
    const ink = roles.color('ink');
    const ink2 = roles.color('ink2');
    const scrimColour = roles.color('scrim');
    const masked = !!ctx.motion && ctx.motion.entrance === 'baseline_reveal';
    const root = h('div', {
      class: ['dyn-fbm', `dyn-fbm--${format}`],
      style: { position: 'absolute', left: '0px', top: '0px', width: `${ctx.zones.width}px`, height: `${ctx.zones.height}px` },
    });

    // the medium: the whole frame, its fit decided here (shownFit), sized by this template's own mediaBox
    const media = mediaOf(scene, ctx.mode);
    const box = mediaRegion(ctx.zones);
    const frame = h('div', {
      class: 'dyn-fbm-frame',
      style: {
        position: 'absolute', left: `${box.x0}px`, top: `${box.y0}px`, width: `${box.x1 - box.x0}px`,
        height: `${box.y1 - box.y0}px`, overflow: 'hidden',
      },
    });
    /** @type {import('../media.js').MediaView | null} */
    let mediaView = null;
    /** @type {HTMLElement | null} */
    let missing = null;
    if (media && media.shown) {
      const shown = /** @type {MediaRef} */ ({ ...media.ref, fit: shownFit(media.ref, ctx.zones) });
      mediaView = ctx.media(shown, { slot: media.slot, start: media.start, alt: String(scene.title || '') });
      frame.appendChild(mediaView.el);
    }
    if (!mediaView || mediaView.missing) {
      // nothing this mode can show (a live-only GIF, an unsafe URL): the panel's title, else a plain label, on the
      // card surface (board_generic's convention: never an empty black box)
      const sp = scene.side_panel;
      const label = (sp && sp.panel && sp.panel.title) || LABELS.mediaMissing;
      frame.style.background = roles.color('surface');
      missing = h('div', {
        class: 'dyn-fbm-missing', dir: 'auto', text: String(label),
        style: {
          position: 'absolute', left: '0px', top: '0px', right: '0px', bottom: '0px', display: 'flex',
          alignItems: 'center', justifyContent: 'center', padding: '0 1em', textAlign: 'center',
          fontSize: `${MISSING_LABEL_PX[format]}px`, fontWeight: '600', lineHeight: '1.2', color: ink2,
          overflowWrap: 'anywhere',
        },
      });
      frame.appendChild(missing);
    }
    const mediaGroup = group('dyn-fbm-mediagroup', frame);

    // the lower third: the scrim band, then the rows (each: [mask >] inner > text)
    const scrim = h('div', {
      class: 'dyn-fbm-scrim', 'aria-hidden': 'true',
      style: { position: 'absolute', left: '0px', width: `${ctx.zones.width}px`, background: scrimColour },
    });
    /** @type {Map<string, number>} */
    const enters = new Map(itemTimings(scene).map((x) => [x.id, x.enter]));
    const rows = rowsOf(scene).map((row, order) => {
      const label = row.slot === 'label';
      const lh = lineHeight(label ? 'display' : 'body', row.text);
      const text = h(label ? 'h2' : 'div', {
        class: ['dyn-fbm-text', label ? 'dyn-fbm-label' : 'dyn-fbm-sub'], dir: 'auto',
        style: {
          margin: '0px', color: label ? ink : ink2,
          fontFamily: label ? 'var(--dyn-font-display)' : 'var(--dyn-font-body)',
          fontWeight: String(label ? LABEL_WEIGHT : L.subWeight), lineHeight: String(lh),
          letterSpacing: label ? `${LABEL_TRACKING_EM}em` : '0em', overflowWrap: 'anywhere',
        },
      });
      /** @type {HTMLElement[]} */
      let fields = [];
      /** @type {HTMLElement[]} */
      let words = [];
      /** @type {boolean[]} */
      let tex = [];
      if (row.rich) {
        const field = h('span', { class: 'dyn-fbm-field' });
        const r = richWords(ctx, field, row.text);
        words = r.words;
        tex = r.tex;
        fields = [field];
        text.appendChild(field);
      } else {
        words = wordSpans(text, row.text, ctx.locale);
        tex = words.map(() => false);
      }
      const inner = h('div', {
        class: 'dyn-fbm-row', dataset: row.item ? { item: row.item } : {},
        style: { position: 'absolute' },
      }, masked ? revealMask(text) : text);
      const own = row.item && enters.has(row.item) ? /** @type {number} */ (enters.get(row.item)) : null;
      return {
        ...row, order, text, inner, fields, words, tex, lh,
        mask: masked ? /** @type {HTMLElement} */ (inner.firstElementChild) : null,
        enter: (own === null ? LABEL_DELAY : own) + ROW_DELAY * order,
        size: label ? L.label.max : L.sub.max,
        range: label ? { min: L.label.min, max: L.label.max } : { min: L.sub.min, max: L.sub.max },
        weight: label ? LABEL_WEIGHT : L.subWeight,
        alpha: 0, overflow: 0,
      };
    });
    const lowerGroup = group('dyn-fbm-lowergroup', scrim, ...rows.map((r) => r.inner));
    root.append(mediaGroup, lowerGroup);
    ctx.root.appendChild(root);
    return {
      root, format, media, mediaView, frame, mediaGroup, missing, scrim, scrimColour, lowerGroup, rows,
      byId: new Map(rows.filter((r) => r.item).map((r) => [String(r.item), r])),
      ink, ink2,
      labelRange: { min: L.label.min, max: L.label.max },
      subRange: { min: L.sub.min, max: L.sub.max },
      mediaAlpha: 0,
      /** laid out (stage px) */
      box: /** @type {Box | null} */ (null),
      blockTop: 0,
      blockH: 0,
    };
  },

  /**
   * Measure and fit (idempotent: every pass starts from the slot ranges): the rows at the largest sizes whose lines
   * fit, then - while the block is taller than the lower third - the sub-lines give up size first and the label last
   * (definition_card's rule: the headline keeps its size longest). The block is bottom-anchored on the third's bottom
   * edge and grows upward; only what reaches past the critical area is reported as overflow.
   * @param {TemplateContext} ctx
   */
  layout(ctx) {
    if (isFallback(ctx)) {
      if (typeof boardGeneric.layout === 'function') boardGeneric.layout(ctx);
      return;
    }
    const v = ctx.view;
    const L = LAYOUT[ctx.format];
    const box = labelBox(ctx.format, ctx.zones);
    v.box = box;
    const width = box.x1 - box.x0;
    const band = box.y1 - box.y0;
    const labelRange = slotRange(ctx, 'label', { min: L.label.min, max: L.label.max, role: 'title' });
    const subRange = slotRange(ctx, 'sub', { min: L.sub.min, max: L.sub.max, role: 'label' });
    v.labelRange = labelRange;
    v.subRange = subRange;
    for (const row of v.rows) {
      Object.assign(row.inner.style, { left: `${box.x0}px`, width: `${width}px` });
      const range = row.slot === 'label' ? labelRange : subRange;
      const spec = row.slot === 'label'
        ? { max: range.max, min: range.min, lines: L.label.lines, lh: row.lh, prefer: L.label.prefer, step: SIZE_STEP }
        : { max: range.max, min: range.min, lines: L.sub.lines, lh: row.lh, step: SIZE_STEP };
      const fit = fitLines(row.text, spec, row.words, row.tex);
      row.size = fit.size;
      row.overflow = fit.overflow;
      row.range = range;
    }
    /** @param {any} row @param {number} size */
    const setSize = (row, size) => {
      row.size = size;
      row.text.style.fontSize = `${size}px`;
    };
    const heights = () => v.rows.map((/** @type {any} */ r) => r.text.offsetHeight);
    const total = () => heights().reduce((/** @type {number} */ a, /** @type {number} */ hh, /** @type {number} */ k) => a + hh + (k ? L.gap : 0), 0);
    // the sub-lines shrink first, then the label (each in 4 px steps, never below its minimum)
    for (const slot of /** @type {const} */ (['sub', 'label'])) {
      for (;;) {
        if (total() <= band) break;
        const rows = v.rows.filter((/** @type {any} */ r) => r.slot === slot && r.size > r.range.min);
        if (!rows.length) break;
        for (const row of rows) setSize(row, Math.max(row.range.min, row.size - SIZE_STEP));
      }
    }
    // the block is bottom-anchored (a lower third grows upward, never down into the caption band) and never clipped:
    // the lower third is the room the fit aims at, the critical area the room it may still use, and only what is past
    // *that* is overflow (preflight: shrink, then block with "Shorten text")
    const blockH = total();
    const room = box.y1 - textCeiling(ctx.format);
    const top = Math.round(box.y1 - blockH);
    v.blockTop = top;
    v.blockH = blockH;
    let y = top;
    const rowHeights = heights();
    v.rows.forEach((/** @type {any} */ row, /** @type {number} */ k) => {
      if (k) y += L.gap;
      row.inner.style.top = `${Math.round(y)}px`;
      const wide = Math.max(0, ...row.words.map((/** @type {HTMLElement} */ w, /** @type {number} */ i) => (row.tex[i] ? w.offsetWidth - width : 0)));
      const lines = Math.round(rowHeights[k] / (row.size * row.lh));
      const cap = row.slot === 'label' ? L.label.lines : L.sub.lines;
      // past its line cap at its minimum size, past the critical area (the top row carries the block's excess), or a
      // TeX word wider than the box
      row.overflow = Math.max(lines > cap ? Math.max(1, rowHeights[k] - cap * row.size * row.lh) : 0,
        k === 0 ? Math.max(0, blockH - room) : 0, wide > 2 ? wide : 0);
      y += rowHeights[k];
    });
    // the scrim: from the block's top padding to the bottom of the frame (a lower third over the picture); a scene
    // with no words at all (a clip without a title) shows none - `update` keeps it hidden at every t
    const scrimTop = v.rows.length ? Math.max(0, Math.round(top - L.padY)) : ctx.zones.height;
    Object.assign(v.scrim.style, { top: `${scrimTop}px`, height: `${Math.max(0, ctx.zones.height - scrimTop)}px` });
  },

  /**
   * Paint scene time t.
   * @param {TemplateContext} ctx
   * @param {number} t
   * @param {SceneState} state
   */
  update(ctx, t, state) {
    if (isFallback(ctx)) {
      boardGeneric.update(ctx, t, state);
      return;
    }
    const v = ctx.view;
    const p = ctx.paint;
    // the medium: a fade, never a scale and never a push (a clip was measured order-dependent when scaled, B2c; a
    // still's own move is its stored Ken Burns, which media.js paints)
    const me = enterAt(ctx, t, v.media && v.media.start > 0 ? v.media.start : 0, MEDIA_ENTER);
    v.mediaAlpha = me;
    p.style(v.frame, 'opacity', fx(me));
    p.style(v.frame, 'visibility', me > 0.001 ? 'visible' : 'hidden');
    // painted every frame, also while hidden, so the element never keeps a frame from an earlier seek
    if (v.mediaView) paintMedia(ctx, v.mediaView, t);
    // the lower third: the scrim with the first row, then each row's text entrance
    let first = 0;
    for (const row of v.rows) {
      const e = enterAt(ctx, t, row.enter, TEXT_ENTER);
      row.alpha = e;
      paintTextEntrance(ctx, row.text, row.mask, e);
      p.style(row.inner, 'visibility', e > 0.001 ? 'visible' : 'hidden');
      if (row.order === 0) first = e;
    }
    p.style(v.scrim, 'opacity', fx(first));
    p.style(v.scrim, 'visibility', first > 0.001 ? 'visible' : 'hidden');
  },

  /**
   * Where a look_here would point (plan §1.4, §1.5, §1.7; this spec consumes no cue today): `panel` the medium's
   * frame - a shape, so its outline is drawn round it (cues.js shapeArea, B2c review CG-1) - and `<item>` (or
   * `<item>:term:<n>`) the row of the board item, which is text. Its box as painted now (read after `update`), on
   * whole stage px; null for anything else or for something not on screen.
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
    let kind = /** @type {'text' | 'shape'} */ ('shape');
    let radius = 0;
    if (parsed.form === 'panel') {
      el = v.mediaAlpha > 0.01 ? v.frame : null;
      holder = v.mediaGroup;
    } else if (parsed.form === 'item' || parsed.form === 'item:term') {
      const row = v.byId.get(String(parsed.item));
      el = row && row.alpha > 0.01 ? row.inner : null;
      holder = v.lowerGroup;
      kind = 'text';
      radius = SPACE[0];
    }
    const r = el ? ctx.rectOf(el) : null;
    if (!r || !(r.w > 0 && r.h > 0)) return null;
    const x0 = Math.round(r.x);
    const y0 = Math.round(r.y);
    const x1 = Math.round(r.x + r.w);
    const y1 = Math.round(r.y + r.h);
    return { rect: { x: x0, y: y0, w: x1 - x0, h: y1 - y0 }, shape: 'rect', radius, el: /** @type {Element} */ (holder || el), kind };
  },

  /**
   * The elements holding an item's text, where cue.term finds its `[[term]]` spans (`.rt-k`, `_shared.js richWords`):
   * the row's one field. Null for anything else.
   * @param {TemplateContext} ctx
   * @param {string} itemId
   * @returns {Element | Element[] | null}
   */
  itemEl(ctx, itemId) {
    if (isFallback(ctx)) return typeof boardGeneric.itemEl === 'function' ? boardGeneric.itemEl(ctx, itemId) : null;
    const row = ctx.view.byId.get(String(itemId));
    if (!row || !row.fields.length) return null;
    return row.fields.length === 1 ? row.fields[0] : row.fields.slice();
  },

  /**
   * The content groups a look_here dims (all but the target's): the medium and the lower third. The same elements at
   * every t; this template never writes their opacity (it animates the parts inside them).
   * @param {TemplateContext} ctx
   * @returns {Element[]}
   */
  dimGroups(ctx) {
    if (isFallback(ctx)) return typeof boardGeneric.dimGroups === 'function' ? boardGeneric.dimGroups(ctx) : [];
    const v = ctx.view;
    return [v.mediaGroup, v.lowerGroup].filter(Boolean);
  },

  /**
   * The shared-element morph's anchor (§4.4, B2f): none yet.
   * @param {TemplateContext} _ctx
   * @returns {null}
   */
  anchor(_ctx) {
    return null;
  },

  /**
   * Static: the box the medium is extracted at (plan §1.4): the whole frame (the medium is full bleed), grown by the
   * most the picture's own Ken Burns zooms it (`kenBurnsScale`), so a picture only ever downscales (§P condition 1).
   * It never depends on preflight's fixes - a remedy only shrinks text - and this template never pushes, so it needs
   * no push headroom. board_generic's box for a scene it draws instead.
   *
   * Only the **one slot** the layout shows (`mediaOf`'s): a clip in any other slot is not laid out here, so
   * board_generic's box for that slot is what the worker should extract - never this template's full frame, which
   * would have `mediaPlan` (media.js) ask the worker for a panel clip's frames at 1920x1080 that the page never
   * shows. (After `drawsScene` a drawn scene carries one medium only, so this is a guard, not a path.)
   * @param {TimedScene} scene
   * @param {Zones} zones
   * @param {'live' | 'render'} mode
   * @param {string | undefined} slot
   * @param {import('../core.js').Fixes} fixes
   * @returns {{w: number, h: number} | null}
   */
  mediaBox(scene, zones, mode, slot, fixes) {
    if (!drawsScene(scene)) {
      return typeof boardGeneric.mediaBox === 'function' ? boardGeneric.mediaBox(scene, zones, mode, slot, fixes) : null;
    }
    const media = mediaOf(scene, mode);
    if (typeof slot === 'string' && media && slot !== media.slot) {
      return typeof boardGeneric.mediaBox === 'function' ? boardGeneric.mediaBox(scene, zones, mode, slot, fixes) : null;
    }
    const k = kenBurnsScale(media ? media.ref : null);
    const box = mediaRegion(zones);
    return { w: Math.ceil((box.x1 - box.x0) * k), h: Math.ceil((box.y1 - box.y0) * k) };
  },

  /**
   * Static: every TeX string this template renders for the scene (`texOf`; the same in both formats).
   * @param {TimedScene} scene
   * @param {import('../../shared/types.js').ResolvedLook | null} look
   * @param {'landscape' | 'portrait'} fmt
   */
  tex(scene, look, fmt) {
    if (!drawsScene(scene)) return typeof boardGeneric.tex === 'function' ? boardGeneric.tex(scene, look, fmt) : [];
    return texOf(scene);
  },

  /**
   * The text on screen (preflight, §7.12): each row with its slot (`label`, `sub`), its size, its slot's minimum and
   * its weight, critical, on the colour it really sits on - the scrim composited over the darkest and the lightest
   * medium, the worse against its ink (`mediaBg`) - plus a missing medium's label (not critical).
   * @param {TemplateContext} ctx
   * @returns {TextEntry[]}
   */
  texts(ctx) {
    if (isFallback(ctx)) return typeof boardGeneric.texts === 'function' ? boardGeneric.texts(ctx) : [];
    const v = ctx.view;
    /** @type {TextEntry[]} */
    const out = [];
    for (const row of v.rows) {
      const ink = row.slot === 'label' ? v.ink : v.ink2;
      out.push({
        el: row.text, role: row.slot === 'label' ? 'title' : 'label', slot: row.slot, critical: true, ink,
        bg: mediaBg(v.scrimColour, ink), alpha: row.alpha, overflow: row.overflow, size: row.size,
        min: row.range.min, weight: row.weight, text: row.text.textContent || '',
      });
    }
    if (v.missing) {
      out.push({
        el: v.missing, role: 'label', critical: false, ink: v.ink2, bg: ctx.roles.color('surface'),
        alpha: v.mediaAlpha, size: MISSING_LABEL_PX[ctx.format], weight: 600, text: v.missing.textContent || '',
      });
    }
    return out;
  },
};
