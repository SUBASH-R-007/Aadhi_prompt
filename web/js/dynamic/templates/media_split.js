// @ts-check
/**
 * `media_split` (DYNAMIC_VIDEO_DESIGN §4.3 B, R1, class `media`): the explainer frame. Data (§4.3, templates.py
 * `_fit_media_split`): at most 3 text items (TEXT_KINDS) and a picture or clip (a `figure` / `image` side panel's
 * medium, or the scene's own medium), or a `terminal` panel. B2 plan §A step 3 pilot: clip, mediaBox, look_here.
 *
 * Layout (§2.5 regions from the catalogue, core.js REGIONS; whole stage px):
 * - 16:9: the medium takes 55% of the critical box, the text 45% (the named regions `left-55` + `right-45`, or their
 *   mirror image), so the media side is the scene's variant (§4.3: it changes only at chapter boundaries, which
 *   direct.py decides, B2e): `media_left` (medium in `left-55`, text in `right-45`) or `media_right` (the default:
 *   text in the mirror of `right-45`, x 96-862, medium in the mirror of `left-55`, x 886-1824). The text keeps a
 *   further GUTTER from the medium (a 48 px gap with the regions' own 24 px). The medium is contained in its region
 *   at its own aspect (24 px radius, the soft shadow: layout tokens radius.media and shadow level 3) and centred; the
 *   text column holds the scene's title above the text items, its top level with the medium's top when it fits. Every
 *   item but a heading starts with a dot in the marker column (B2c review V10: a definition or callout without one read
 *   as the bullet above's second paragraph); a heading is the column's own sub-head, bold, its column empty.
 * - Line heights are the type scale's per script (§4.6, plan §2 R-29; the catalogue's `type.scale`): the title the
 *   display role's (Latin 1.05, Indic >= 1.25), the items the body role's (Latin 1.35, Indic >= 1.45), per text: Tamil
 *   and Devanagari marks need the room (B2c review V5: a Tamil phrase at 1.2 touched the TeX line under it).
 * - 9:16: a headline (the title, one line when it fits at >= 56 px, else two: R-25 asks one line, B2c reading pending
 *   the owner, as board_generic's title) in the `headline` region, the medium in the stage (x 60-1020, from y 460, or
 *   340 without a headline: `tall`) and a key phrase above the caption band (from the headline's left edge, x 192, to
 *   the TikTok rail's x 780, ending 8 px above the caption band: `phraseBottom`). The headline and the phrase share one
 *   left edge (B2c review V7: the phrase started at x 120, 72 px left of the headline). Reading of §4.3: the key phrase
 *   is the scene's current text item, one at a time, each replacing the one before on its beat (≤ 2 lines; a 9:16 reel
 *   shows one phrase, never a list: that is list_build's compact stack). The medium is shown in what the tallest phrase
 *   leaves.
 * - Without a picture or clip the render can show (a GIF, a chart or graph panel, which this template does not draw,
 *   or an unsafe URL) the medium's box shows the panel's title on the card surface (board_generic's convention: never
 *   an empty black box); a terminal panel is a code card with its output lines (§4.3 data).
 *
 * Motion (§4.8; every duration times the personality's scale, `ctx.motion.scale`): the title and each text item enter
 * with the personality's text entrance (`ctx.motion.entrance`: Soft fades in with a 16 px rise, Crisp is revealed
 * from its baseline) over TEXT_ENTER; the text items one per beat (state.js itemTimings: a reveal event, else the beat
 * that reveals the item, 120 ms ahead of its anchor); the medium fades in and scales 0.96 -> 1 over MEDIA_ENTER (§4.3;
 * §P: a picture only ever downscales, and mediaBox leaves room for the push too); a clip only fades in (`clipShown`:
 * a contain_blur clip scaled by its entrance was measured order-dependent on its first visible frame, B2c,
 * tests/determinism/test_template_clips.py; a clip is never scaled, nor pushed, cues.js showsClip). In 9:16 a phrase leaves 30% faster
 * than it entered (§4.8) just before the next one enters; a phrase with no beat of its own waits until the one before
 * was shown MIN_DWELL. Reduced motion (live only): fades in place, no scale. Everything is a pure function of t.
 *
 * Cues (cues.js draws them; this template never draws emphasis, §4.2): `look_here` on `panel` / `panel:row:<n>` /
 * `<item>` (focusRect) is a camera push of <= 3% (push_ok, §4.3 "push <= 3%"), the outline form under reduced motion
 * in the live player and in a scene that shows a clip (cues.js showsClip: a clip under the push was measured
 * order-dependent, B2c; such a scene keeps the full regions); `cue_term` on `<item>` finds the item's [[term]] spans (itemEl). A push scales the whole layer
 * about the target's centre, which would carry text near the zone edges out of its critical box (R-59) or into the
 * caption band; so a scene whose look_here cues push lays its text out inside `pushArea`: the box from which text,
 * scaled by 1 + PUSH_SCALE about any centre a push can take, stays inside its critical box. Scenes without a push keep
 * the full regions. Every text slot reports to preflight (§7.12) with its slot, size, minimum and weight; preflight's
 * fixes reach the sizes through `_shared.js` slotRange (a size cap, the safe layout = every slot at its minimum, the
 * R-29 floor) and the scrims through `look.fixes.slots[slot].scrim` (`title`, `body`).
 *
 * The constants below are B2c proposals, pending the owner's review (the design gives the split, the radius, the
 * shadow and the timings; the sizes, gaps and the 9:16 phrase reading are this template's): aadhi/compose/dynamic/
 * templates.py mirrors LAYOUT as the template's slots, and the catalogue test pins the two together.
 */

import { h } from '../../shared/dom.js';
import { ease } from '../../motion/easing.js';
import { progress } from '../../motion/track.js';
import { splitWords } from '../../motion/text.js';
import { mathSpans } from '../../player/richtext.js';
import { planScene } from '../../player/schedule.js';
import { LAYOUT_TOKENS, ZONES, fixesOf, fx, parseTarget } from '../core.js';
import { PUSH_SCALE, cueNeeds } from '../cues.js';
import { shownIn } from '../media.js';
import { itemTimings } from '../state.js';
import {
  RADIUS, SPACE, containRect, enterAt, fitLines, group, mediaAspect, paintMedia, paintTextEntrance, pushHeadroom,
  region, revealMask, richWords, scrimmedBg, seconds, slotRange,
} from './_shared.js';

/** @typedef {import('./types.js').TemplateContext} TemplateContext */
/** @typedef {import('./types.js').TemplateModule} TemplateModule */
/** @typedef {import('./types.js').FocusRect} FocusRect */
/** @typedef {import('./types.js').TextEntry} TextEntry */
/** @typedef {import('../../shared/types.js').TimedScene} TimedScene */
/** @typedef {import('../../shared/types.js').BoardItem} BoardItem */
/** @typedef {import('../../shared/types.js').MediaRef} MediaRef */
/** @typedef {import('../../shared/types.js').SceneState} SceneState */
/** @typedef {import('../core.js').Zones} Zones */
/** @typedef {import('../core.js').Box} Box */

export const ID = 'media_split';

/** UI labels (kept together for localisation). */
export const LABELS = Object.freeze({
  callout_info: 'Info',
  callout_tip: 'Tip',
  callout_warning: 'Watch out',
  /** the definition's term and meaning */
  definitionJoin: ' — ',
  mediaMissing: 'Picture unavailable',
});

/** The board item kinds drawn as text (templates.py TEXT_KINDS: what `_fit_media_split` counts as text items). */
export const TEXT_KINDS = Object.freeze(['heading', 'bullet', 'paragraph', 'definition', 'takeaway', 'callout_info',
  'callout_tip', 'callout_warning']);
/** Text kinds drawn bold (a heading, a takeaway): the weight the slot reports for them. */
const BOLD_KINDS = new Set(['heading', 'takeaway']);

/** 16:9 variants (§4.3: the media side; A2 names, pending the owner's review) and the one used without a valid one. */
export const VARIANTS = Object.freeze(['media_left', 'media_right']);
export const DEFAULT_VARIANT = 'media_right';

/**
 * Per-format layout (stage px; B2c proposals, pending the owner's review: several sit below R-29's type scale, above
 * its floors). 16:9: body 44-52 (R-29's body is 48-56: the fit may go to 44, as board_generic's), titles 64-88 in the
 * 45% column, up to 3 lines (R-29's headings are 84-120); 9:16: the headline 56-88, one line preferred, two allowed
 * (R-25 asks one line), and a phrase of at most 2 lines 48-64 at weight 700 (R-29's body is 56-64; >= 600 below 64 px).
 * Each is fitted on the page in 4 px steps (`_shared.js` fitLines, the rule textfit.py mirrors), never below the R-29
 * floor (slotRange). Line heights: LINE_HEIGHT, per script.
 * - `gutter`: what the text keeps from the medium on top of the regions' 24 px gutter (16:9);
 * - `marker`: the bullet column every 16:9 item's text starts after (a dot for bullets, empty for the other kinds, so
 *   every item's text starts at one x and the body slot has one width); `dot` its dot;
 * - `titleGap` / `itemGap`: title to first item, item to item (16:9);
 * - `stageGap`: headline to medium and medium to phrase (9:16); `phraseBottom`: where the phrase ends, 8 px (the
 *   spacing scale's first step) above the caption band (the catalogue's zones.caption.y0, y 1040; B2c review CG-3:
 *   catalogue geometry, never a literal);
 * - `terminal`: the code card's text (mono; 9:16 at weight 700, R-29: JetBrains Mono has 400 and 700) and padding.
 */
export const LAYOUT = Object.freeze({
  landscape: Object.freeze({
    title: Object.freeze({ max: 88, min: 64, lines: 3 }),
    body: Object.freeze({ max: 52, min: 44 }),
    bodyWeight: 400,
    gutter: 24,
    marker: 36,
    dot: 14,
    titleGap: 40,
    itemGap: 24,
    terminal: Object.freeze({ max: 44, min: 36, lh: 1.35, weight: 400, pad: 32 }),
  }),
  portrait: Object.freeze({
    title: Object.freeze({ max: 88, min: 56, lines: 2, prefer: 1 }),
    body: Object.freeze({ max: 64, min: 48, lines: 2 }),
    bodyWeight: 700,
    stageGap: 24,
    phraseBottom: ZONES.portrait.caption.y0 - SPACE[0],
    terminal: Object.freeze({ max: 44, min: 40, lh: 1.35, weight: 700, pad: 28 }),
  }),
});
/**
 * Line heights (§4.6 type scale, the catalogue's `type.scale`; media_split.test.js pins them): the title the display
 * role's, the items the body role's; Indic text (Devanagari to Sinhala) the Indic minimums (plan §2 R-29).
 */
export const LINE_HEIGHT = Object.freeze({
  display: Object.freeze({ latin: 1.05, indic: 1.25 }),
  body: Object.freeze({ latin: 1.35, indic: 1.45 }),
});
/** Indic scripts (Devanagari to Sinhala), whose text takes the taller line heights (list_build's rule). */
const INDIC = new RegExp(`[${['Devanagari', 'Bengali', 'Gurmukhi', 'Gujarati', 'Oriya', 'Tamil', 'Telugu', 'Kannada',
  'Malayalam', 'Sinhala'].map((x) => `\\p{Script=${x}}`).join('')}]`, 'u');

/**
 * The line height of `text` in role `role` (LINE_HEIGHT: Indic text takes the Indic value). Pure.
 * @param {'display' | 'body'} role @param {string} text
 */
export function lineHeight(role, text) {
  return INDIC.test(String(text || '')) ? LINE_HEIGHT[role].indic : LINE_HEIGHT[role].latin;
}

/** The text of a text item as one string (its term and text; the line height reads its script). @param {BoardItem} item */
function itemString(item) {
  return `${item.kind === 'definition' && item.term ? item.term : ''} ${item.text || ''}`;
}

/** Title weight and tracking (dynamic.css .dyn-title's, so the title slot measures like board_generic's). */
export const TITLE_WEIGHT = 700;
export const TITLE_TRACKING_EM = -0.015;
/** The label over a missing medium (board_generic's .dyn-media-missing size, R-29 labels: 40 / 44 px). */
export const MISSING_LABEL_PX = Object.freeze({ landscape: 52, portrait: 48 });

// Motion (§4.8), seconds before the personality's scale
export const TITLE_DELAY = 0.08;
/** §4.8 entrance 400-600 ms; §4.3 list_build's per-item 450 ms, the same for these items. */
export const TEXT_ENTER = 0.45;
/** §4.3: the medium scales 0.96 -> 1 over 500 ms. */
export const MEDIA_ENTER = 0.5;
export const MEDIA_FROM = 0.96;
/** The medium enters this long after the scene starts (a side panel shown later: at its show_at). */
export const MEDIA_DELAY = 0.15;
/** §4.8: exits are 30% shorter than entrances. */
export const EXIT_FACTOR = 0.7;
/** A 9:16 phrase is fully shown at least this long before a phrase with no beat of its own replaces it
 * (board_generic's MIN_DWELL). */
export const MIN_DWELL = 1.5;

// ---------------------------------------------------------------------------------------------
// What the scene shows (pure)
// ---------------------------------------------------------------------------------------------

/**
 * The scene's medium (board_generic's order): its own picture or clip, an interactive scene's poster, else a side
 * panel's picture or clip; `shown`: whether `mode` can show it (media.js shownIn: the render page leaves out live-only
 * GIFs and media kept out of the MP4). The medium is laid out (at its aspect) whether or not the mode shows it, so the
 * live player and the MP4 share one layout. Null without one.
 * @param {TimedScene} scene
 * @param {'live' | 'render'} [mode]
 * @returns {{slot: 'main' | 'panel', ref: MediaRef, start: number, shown: boolean} | null}
 */
export function mediaOf(scene, mode = 'live') {
  const m = mode === 'render' ? 'render' : 'live';
  if (scene.media) return { slot: 'main', ref: scene.media, start: 0, shown: shownIn(scene.media, m) };
  if (scene.type === 'interactive' && scene.poster) return { slot: 'main', ref: scene.poster, start: 0, shown: shownIn(scene.poster, m) };
  const sp = scene.side_panel;
  if (sp && sp.media && scene.layout?.show_side_panel !== false) {
    return { slot: 'panel', ref: sp.media, start: Math.max(0, Number(sp.show_at) || 0), shown: shownIn(sp.media, m) };
  }
  return null;
}

/**
 * Whether the medium `mediaOf` gives is a clip (a video, in either mode): it enters by a fade only, never scaled (see
 * the module comment).
 * @param {{ref: MediaRef} | null} media
 */
export function clipShown(media) {
  return !!media && media.ref.kind === 'video';
}

/**
 * The scene's terminal panel (§4.3 data: a `terminal` panel), as the classic panel splits it (schedule.js planScene:
 * its command, its output lines and when each is shown, state.terminalLines), or null.
 * @param {TimedScene} scene
 * @returns {{command: string, lines: string[], start: number} | null}
 */
export function terminalOf(scene) {
  const sp = scene.side_panel;
  if (!sp || !sp.panel || sp.panel.kind !== 'terminal' || !sp.panel.terminal || scene.layout?.show_side_panel === false) return null;
  const plan = planScene(scene).terminal;
  return { command: String(sp.panel.terminal.command || ''), lines: plan ? plan.lines.slice() : [], start: Math.max(0, Number(sp.show_at) || 0) };
}

/**
 * The text items drawn, in board order (TEXT_KINDS; any other kind is not this template's data, §4.3).
 * @param {TimedScene} scene
 * @returns {BoardItem[]}
 */
export function textItemsOf(scene) {
  return (scene.board || []).filter((i) => TEXT_KINDS.includes(i.kind));
}

/**
 * The 16:9 variant of a look (`media_left` | `media_right`; DEFAULT_VARIANT for none, B1's `safe` or an unknown name).
 * @param {import('../../shared/types.js').ResolvedLook | null | undefined} look
 */
export function variantOf(look) {
  const v = look && /** @type {any} */ (look).variant;
  return VARIANTS.includes(String(v)) ? String(v) : DEFAULT_VARIANT;
}

/**
 * The 16:9 boxes of a variant: the medium's region (55%) and the text column (45%, less GUTTER on the medium's side).
 * @param {string} variant
 * @returns {{media: Box, text: Box}}
 */
export function splitRegions(variant) {
  const at = { format: /** @type {'landscape'} */ ('landscape') };
  const full = region(at, 'full');
  const wide = region(at, 'left-55');
  const narrow = region(at, 'right-45');
  const g = LAYOUT.landscape.gutter;
  if (variant === 'media_left') return { media: wide, text: { ...narrow, x0: narrow.x0 + g } };
  /** @param {Box} b mirrored about the critical box's centre */
  const mirror = (b) => ({ x0: full.x0 + full.x1 - b.x1, y0: b.y0, x1: full.x0 + full.x1 - b.x0, y1: b.y1 });
  const text = mirror(narrow);
  return { media: mirror(wide), text: { ...text, x1: text.x1 - g } };
}

/**
 * The box text must stay inside so that, scaled by `scale` about any point of `origins` (the centres a look_here push
 * can take, cues.js), it stays inside `zone`: a point p pushed about o lands on o + scale (p - o), which is >= zone.x0
 * for every o <= origins.x1 exactly when p >= origins.x1 (1 - 1 / scale) + zone.x0 / scale; likewise for the other
 * three sides. Whole px, rounded inwards. For an unpushed scene (scale 1) it is `zone` itself.
 * @param {Box} zone
 * @param {Box} origins   bounds of the push centres (a point: x0 = x1, y0 = y1)
 * @param {number} [scale]
 * @returns {Box}
 */
export function pushArea(zone, origins, scale = 1 + PUSH_SCALE) {
  const k = 1 - 1 / scale;
  return {
    x0: Math.ceil(origins.x1 * k + zone.x0 / scale - 1e-9),
    y0: Math.ceil(origins.y1 * k + zone.y0 / scale - 1e-9),
    x1: Math.floor(origins.x0 * k + zone.x1 / scale + 1e-9),
    y1: Math.floor(origins.y0 * k + zone.y1 / scale + 1e-9),
  };
}

/** @param {Box} a @param {Box} b */
function intersect(a, b) {
  return { x0: Math.max(a.x0, b.x0), y0: Math.max(a.y0, b.y0), x1: Math.min(a.x1, b.x1), y1: Math.min(a.y1, b.y1) };
}

/** @param {Box | null} a @param {Box} b */
function union(a, b) {
  return a ? { x0: Math.min(a.x0, b.x0), y0: Math.min(a.y0, b.y0), x1: Math.max(a.x1, b.x1), y1: Math.max(a.y1, b.y1) } : { ...b };
}

/**
 * Which kinds of target the scene's look_here cues push toward (cues.js cueNeeds: only events this template consumes,
 * and only while it may push), so the layout leaves room for exactly those pushes.
 * @param {TimedScene} scene
 * @returns {{item: boolean, panel: boolean}}
 */
export function pushTargets(scene) {
  const needs = cueNeeds(scene, ID);
  if (!needs.camera) return { item: false, panel: false };
  const looks = needs.events.filter((e) => e.kind === 'look_here');
  return {
    item: looks.some((e) => e.parsed.form === 'item'),
    panel: looks.some((e) => e.parsed.form === 'panel' || e.parsed.form === 'panel:row'),
  };
}

/**
 * The 9:16 geometry around the medium: the headline's box, the phrase's box (both inside their push areas when the
 * scene pushes) and the zone the medium is laid out in, given the headline's height (0: none) and the tallest
 * phrase's (0: no text item). Shared by `layout` (measured heights) and the static `mediaBox` (the smallest heights
 * any fit can give: one line at each slot's minimum), so the box a clip is extracted at is never smaller than shown.
 * @param {TimedScene} scene
 * @param {number} titleH
 * @param {number} phraseH
 * @returns {{title: Box, phrase: Box, media: Box}}
 */
export function portraitBoxes(scene, titleH, phraseH) {
  const at = { format: /** @type {'portrait'} */ ('portrait') };
  const L = LAYOUT.portrait;
  const headline = region(at, 'headline');
  const stage = region(at, 'stage');
  const tall = region(at, 'tall');
  const lower = region(at, 'text-below');
  const upper = region(at, 'text-above');
  // the phrase: below the TikTok rail's top (y >= 840) from the headline's left edge (x 192: one left edge for the
  // text, V7) to the rail (x 780), ending above the caption band
  /** @type {Box} */
  let phrase = { x0: headline.x0, y0: lower.y0, x1: lower.x1, y1: L.phraseBottom };
  /** @type {Box} */
  let title = { x0: headline.x0, y0: headline.y0, x1: headline.x1, y1: headline.y0 + titleH };
  const push = pushTargets(scene);
  if (push.item || push.panel) {
    // the push centres: the medium's centre (centred in the stage; anywhere from the tall region's top to the stage's
    // bottom) and the phrase's (its column's centre, anywhere in its box)
    /** @type {Box | null} */
    let o = null;
    if (push.panel) o = union(o, { x0: (stage.x0 + stage.x1) / 2, x1: (stage.x0 + stage.x1) / 2, y0: tall.y0, y1: stage.y1 });
    if (push.item) o = union(o, { x0: (phrase.x0 + phrase.x1) / 2, x1: (phrase.x0 + phrase.x1) / 2, y0: phrase.y0, y1: phrase.y1 });
    const origins = /** @type {Box} */ (o);
    // the phrase's critical box (R-59, below y 840) ends where the caption band starts (the catalogue's zones)
    phrase = intersect(phrase, pushArea({ ...lower, y1: ZONES.portrait.caption.y0 }, origins));
    const room = pushArea(upper, origins);
    title = { ...title, x0: Math.max(title.x0, room.x0), x1: Math.min(title.x1, room.x1) };
    // one left edge for the headline and the phrase (V7), the further in of the two push areas'
    const x0 = Math.max(title.x0, phrase.x0);
    title = { ...title, x0 };
    phrase = { ...phrase, x0 };
  }
  const top = titleH > 0 ? Math.max(stage.y0, Math.ceil(title.y1 + L.stageGap)) : tall.y0;
  const phraseTop = phraseH > 0 ? Math.floor(phrase.y1 - phraseH) : stage.y1 + L.stageGap;
  const bottom = Math.min(stage.y1, phraseTop - L.stageGap);
  return { title, phrase: { ...phrase, y0: phraseH > 0 ? phraseTop : phrase.y0 }, media: { x0: stage.x0, y0: top, x1: stage.x1, y1: Math.max(top, bottom) } };
}

/**
 * When each 9:16 phrase is on screen: `enter` (its entrance starts) and `exit` (its exit starts; Infinity for the
 * last). A phrase on a beat enters on time and the phrase before it leaves just before (EXIT_FACTOR of the entrance);
 * a phrase with no beat (`initial`) waits until the one before was fully shown for MIN_DWELL, but no longer than until
 * a second before the scene ends (its entrance done by then; never before the one before has left). Pure; `scale` is the
 * personality's duration scale.
 * @param {{id: string, enter: number, initial: boolean}[]} timings   in entrance order
 * @param {number} duration   the scene's
 * @param {number} [scale]
 * @returns {{id: string, enter: number, exit: number}[]}
 */
export function phraseSchedule(timings, duration, scale = 1) {
  const enterDur = TEXT_ENTER * scale;
  const exitDur = enterDur * EXIT_FACTOR;
  const latest = Math.max(0, Number(duration) - enterDur - 1);
  /** @type {{id: string, enter: number, exit: number}[]} */
  const out = [];
  for (const it of timings) {
    const prev = out[out.length - 1];
    let enter = it.enter;
    if (prev) {
      if (it.initial) enter = Math.max(enter, Math.min(prev.enter + enterDur + MIN_DWELL + exitDur, Math.max(latest, enter)));
      // the one before leaves just before this one enters (never before it began to enter)
      prev.exit = Math.max(prev.enter, enter - exitDur);
      enter = Math.max(enter, prev.exit + exitDur);
    }
    out.push({ id: it.id, enter, exit: Infinity });
  }
  return out;
}

/**
 * Every TeX string this template converts for a scene (the static `tex` hook; render.js converts them before `ready`):
 * the `$...$` spans of the drawn text items' term and text (inline), in that order. The title, the callout labels and
 * a terminal's lines are plain text.
 * @param {TimedScene} scene
 * @returns {{tex: string, display: boolean}[]}
 */
export function texOf(scene) {
  /** @type {{tex: string, display: boolean}[]} */
  const out = [];
  for (const item of textItemsOf(scene)) {
    for (const text of [item.kind === 'definition' ? item.term : null, item.text]) {
      if (typeof text === 'string' && text.includes('$')) for (const tex of mathSpans(text)) if (tex.trim()) out.push({ tex, display: false });
    }
  }
  return out;
}

/** The scene's title, plain text ('' without one). @param {TimedScene} scene */
function titleOf(scene) {
  return String(scene.title || '').trim();
}

// ---------------------------------------------------------------------------------------------
// Building
// ---------------------------------------------------------------------------------------------

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

/**
 * One text item: its rich-lite fields as word spans that keep their [[term]] spans (richWords; cue.term's targets,
 * `fields` in the item's field order term, text), with a callout's label before them. `weight`: the format's body
 * weight, 700 for a heading or a takeaway; `lh`: its line height (0: the body role's for its script, `lineHeight`).
 * @param {TemplateContext} ctx
 * @param {BoardItem} item
 * @param {number} bodyWeight
 * @param {number} lh
 */
function itemText(ctx, item, bodyWeight, lh) {
  const weight = BOLD_KINDS.has(item.kind) ? 700 : bodyWeight;
  lh = lh || lineHeight('body', itemString(item));
  const content = h('div', {
    class: 'dyn-ms-text', dir: 'auto',
    style: { overflowWrap: 'anywhere', minWidth: '0', fontWeight: String(weight), lineHeight: String(lh) },
  });
  /** @type {HTMLElement[]} */
  const fields = [];
  /** @type {HTMLElement[]} */
  const words = [];
  /** @type {boolean[]} */
  const tex = [];
  /** @param {string} text @param {boolean} strong */
  const field = (text, strong) => {
    const host = h('span', { class: 'dyn-ms-field', style: { fontWeight: strong ? '700' : null } });
    const r = richWords(ctx, host, text);
    words.push(...r.words);
    tex.push(...r.tex);
    fields.push(host);
    content.appendChild(host);
  };
  const label = /** @type {Record<string, string>} */ (LABELS)[item.kind];
  if (typeof label === 'string' && item.kind.startsWith('callout_')) {
    content.append(h('span', { class: 'dyn-ms-label', style: { fontWeight: '700' }, text: label }), ' ');
  }
  if (item.kind === 'definition' && item.term) {
    field(String(item.term), true);
    if (item.text) content.append(LABELS.definitionJoin);
  }
  if (item.text || !fields.length) field(String(item.text || ''), false);
  return { content, fields, words, tex, weight, lh };
}

/** @type {TemplateModule} */
export default {
  id: ID,

  /**
   * @param {TemplateContext} ctx
   */
  build(ctx) {
    const { scene, format, roles } = ctx;
    const portrait = format === 'portrait';
    const L = portrait ? LAYOUT.portrait : LAYOUT.landscape;
    const variant = portrait ? 'default' : variantOf(ctx.look);
    const ink = ctx.onBackground.ink;
    const surface = roles.color('surface');
    // the Crisp entrance slides text up inside a mask; every other entrance fades in place with a rise (no mask: it
    // would clip the rise)
    const masked = !!ctx.motion && ctx.motion.entrance === 'baseline_reveal';
    /** @param {HTMLElement} el */
    const maskOf = (el) => (masked ? revealMask(el) : null);
    const root = h('div', { class: ['dyn-ms', `dyn-ms--${format}`], style: { position: 'absolute', left: '0', top: '0', width: `${ctx.zones.width}px`, height: `${ctx.zones.height}px` } });

    // title
    const text = titleOf(scene);
    /** @type {HTMLElement | null} */
    let title = null;
    /** @type {HTMLElement[]} */
    let titleWords = [];
    if (text) {
      title = h('h2', {
        class: 'dyn-ms-title', dir: 'auto',
        style: {
          margin: '0', color: ink, fontFamily: 'var(--dyn-font-display)', fontWeight: String(TITLE_WEIGHT),
          lineHeight: String(lineHeight('display', text)), letterSpacing: `${TITLE_TRACKING_EM}em`, overflowWrap: 'anywhere',
        },
      });
      titleWords = wordSpans(title, text, ctx.locale);
    }
    const titleMask = title ? maskOf(title) : null;
    const titleGroup = title ? group('dyn-ms-titlegroup', titleMask || title) : null;

    // text items: group (the stage's dim) > exit wrapper (9:16 phrases leave) > [mask] > inner (the entrance) > row
    const timings = new Map(itemTimings(scene).map((t) => [t.id, t]));
    const drawn = textItemsOf(scene);
    const phrases = portrait ? phraseSchedule(drawn.map((i) => /** @type {any} */ (timings.get(i.id))).filter(Boolean)
      .sort((a, b) => a.enter - b.enter || a.order - b.order), Number(scene.duration) || 0, seconds(ctx, 1)) : [];
    const items = drawn.map((item, order) => {
      const t = timings.get(item.id);
      const parts = itemText(ctx, item, L.bodyWeight, 0);
      /** @type {HTMLElement[]} */
      const row = [];
      if (!portrait) {
        const LL = LAYOUT.landscape;
        // every item a dot (each reads as its own item, V10), but a heading (the column's sub-head)
        const dot = item.kind !== 'heading'
          ? h('span', { class: 'dyn-ms-dot', 'aria-hidden': 'true', style: { display: 'block', width: `${LL.dot}px`, height: `${LL.dot}px`, borderRadius: '50%', background: ink } })
          : null;
        // the marker column is one line high, so the dot sits on the first line's middle at any size
        row.push(h('span', { class: 'dyn-ms-marker', style: { display: 'flex', alignItems: 'center', flex: 'none', width: `${LL.marker}px`, height: `${parts.lh}em` } }, dot));
      }
      parts.content.style.flex = '1 1 auto';
      row.push(parts.content);
      const inner = h('div', { class: 'dyn-ms-item', dataset: { item: item.id }, style: { display: 'flex', alignItems: 'flex-start', color: ink } }, row);
      const mask = maskOf(inner);
      const exitEl = h('div', { class: 'dyn-ms-exit' }, mask || inner);
      const g = group('dyn-ms-itemgroup', exitEl);
      const phrase = phrases.find((p) => p.id === item.id);
      return {
        item, id: item.id, order, group: g, exitEl, mask, inner, ...parts,
        enter: phrase ? phrase.enter : t ? t.enter : 0, exit: phrase ? phrase.exit : Infinity,
        alpha: 0, overflow: 0,
      };
    });

    // the medium: a picture or clip, a terminal card, or the panel's title on the card surface
    const media = mediaOf(scene, ctx.mode);
    const terminal = media ? null : terminalOf(scene);
    const frame = h('div', {
      class: 'dyn-ms-frame',
      style: {
        position: 'absolute', overflow: 'hidden', borderRadius: `${RADIUS.media}px`, boxShadow: LAYOUT_TOKENS.shadow['3'],
        transformOrigin: '50% 50%',
      },
    });
    /** @type {import('../media.js').MediaView | null} */
    let mediaView = null;
    /** @type {HTMLElement | null} */
    let missing = null;
    /** @type {any} */
    let term = null;
    if (media && media.shown) {
      // sized by this template's mediaBox for the medium's slot: the box mediaPlan extracts a clip at
      mediaView = ctx.media(media.ref, { slot: media.slot, start: media.start, alt: text });
      frame.appendChild(mediaView.el);
    }
    if (terminal) {
      const T = L.terminal;
      const lines = terminal.lines.map((line) => h('div', { class: 'dyn-ms-term-line', text: line || ' ' }));
      const cmd = h('div', { class: 'dyn-ms-term-cmd', style: { fontWeight: '700' }, text: `$ ${terminal.command}` });
      const pre = h('pre', {
        class: 'dyn-ms-term', style: {
          margin: '0', fontFamily: 'var(--dyn-font-mono)', fontWeight: String(T.weight), lineHeight: String(T.lh),
          whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', color: roles.color('ink'),
        },
      }, cmd, lines);
      frame.style.background = surface;
      frame.style.padding = `${T.pad}px`;
      frame.appendChild(pre);
      term = { pre, cmd, lines, start: terminal.start, size: T.max, overflow: 0 };
    } else if (!mediaView || mediaView.missing) {
      // nothing this mode can show (or an unsafe URL): the panel's title (else a plain label) on the card surface
      const sp = scene.side_panel;
      const label = (sp && sp.panel && sp.panel.title) || LABELS.mediaMissing;
      frame.style.background = surface;
      missing = h('div', {
        class: 'dyn-ms-missing', dir: 'auto', text: String(label),
        style: {
          position: 'absolute', left: '0', top: '0', right: '0', bottom: '0', display: 'flex', alignItems: 'center',
          justifyContent: 'center', padding: '0 1em', textAlign: 'center', fontSize: `${MISSING_LABEL_PX[format]}px`,
          fontWeight: '600', lineHeight: '1.2', color: roles.color('ink2'), overflowWrap: 'anywhere',
        },
      });
      frame.appendChild(missing);
    }
    const mediaGroup = group('dyn-ms-mediagroup', frame);

    // R-53 remedy: a scrim of the scene's `scrim` role behind the text drawn on the background when preflight asked
    // for one (look.fixes.slots.title / .body); 16:9: one behind the whole column, 9:16: one per slot
    // (core.js fixesOf: the fixes channel, else B1's legacy text_fit)
    const fixed = fixesOf(ctx.look).slots;
    /** @param {string} slot */
    const wants = (slot) => Object.prototype.hasOwnProperty.call(fixed, slot) && fixed[slot].scrim;
    const scrimColour = wants('title') || wants('body') ? roles.color('scrim') : null;
    /** @param {boolean} on */
    const scrimEl = (on) => (on && scrimColour
      ? h('div', { class: 'dyn-ms-scrim', style: { position: 'absolute', borderRadius: `${RADIUS.card}px`, background: scrimColour } })
      : null);
    const titleScrim = portrait ? scrimEl(wants('title')) : scrimEl(wants('title') || wants('body'));
    const phraseScrim = portrait ? scrimEl(wants('body')) : null;

    // 16:9: the column (title and items in flow); 9:16: each part placed on its own
    /** @type {HTMLElement | null} */
    let column = null;
    if (!portrait) {
      column = h('div', { class: 'dyn-ms-column', style: { position: 'absolute' } }, titleGroup, items.map((it) => it.group));
      root.append(...[titleScrim, mediaGroup, column].filter((x) => !!x));
    } else {
      root.append(...[titleScrim, phraseScrim, mediaGroup, titleGroup, ...items.map((it) => it.group)].filter((x) => !!x));
    }
    ctx.root.appendChild(root);
    return {
      root, variant, portrait, title, titleText: text, titleWords, titleMask, titleGroup, items,
      byId: new Map(items.map((it) => [it.id, it])), media, mediaView, frame, mediaGroup, missing, term, column,
      titleScrim, phraseScrim, scrim: scrimColour, ink,
      mediaStart: media && media.start > 0 ? media.start : terminal && terminal.start > 0 ? terminal.start : MEDIA_DELAY,
      clip: clipShown(media),
      titleSize: L.title.max, titleRange: { min: L.title.min, max: L.title.max }, titleOverflow: 0,
      titleLh: lineHeight('display', text),
      bodySize: L.body.max, bodyRange: { min: L.body.min, max: L.body.max },
      titleAlpha: 0, mediaAlpha: 0,
      /** laid out: the text column / phrase box and the medium's box (stage px) */
      textBox: /** @type {Box | null} */ (null), mediaBox: /** @type {Box | null} */ (null),
    };
  },

  /**
   * Measure and fit (idempotent: every pass starts from the slot ranges): the title, the text items, the medium's box
   * and the push room (see the module comment).
   * @param {TemplateContext} ctx
   */
  layout(ctx) {
    const v = ctx.view;
    const portrait = v.portrait;
    const L = portrait ? LAYOUT.portrait : LAYOUT.landscape;
    const titleRange = slotRange(ctx, 'title', { min: L.title.min, max: L.title.max, role: 'title' });
    const bodyRange = slotRange(ctx, 'body', { min: L.body.min, max: L.body.max, role: 'body' });
    v.titleRange = titleRange;
    v.bodyRange = bodyRange;
    if (portrait) layoutPortrait(ctx, v, titleRange, bodyRange);
    else layoutLandscape(ctx, v, titleRange, bodyRange);
  },

  /**
   * Paint scene time t.
   * @param {TemplateContext} ctx
   * @param {number} t
   * @param {SceneState} state
   */
  update(ctx, t, state) {
    const v = ctx.view;
    const p = ctx.paint;
    // title
    const te = v.title ? enterAt(ctx, t, TITLE_DELAY, TEXT_ENTER) : 0;
    v.titleAlpha = te;
    if (v.title) paintTextEntrance(ctx, v.title, v.titleMask, te);
    // the medium: fades in and scales 0.96 -> 1 about its centre (a picture only ever downscales, §P); a clip only fades
    const me = enterAt(ctx, t, v.mediaStart, MEDIA_ENTER);
    v.mediaAlpha = me;
    p.style(v.frame, 'opacity', fx(me));
    p.style(v.frame, 'visibility', me > 0.001 ? 'visible' : 'hidden');
    const scale = ctx.reducedMotion || v.clip || me >= 1 ? 1 : MEDIA_FROM + (1 - MEDIA_FROM) * me;
    p.style(v.frame, 'transform', scale === 1 ? 'none' : `scale(${fx(scale, 4)})`);
    // painted every frame, also while hidden, so the element never keeps a frame from an earlier seek
    if (v.mediaView) paintMedia(ctx, v.mediaView, t);
    if (v.term) {
      const shown = state.terminalLines.length;
      v.term.lines.forEach((/** @type {HTMLElement} */ line, /** @type {number} */ i) => p.style(line, 'visibility', i < shown ? 'visible' : 'hidden'));
    }
    // text items: the entrance on the inner element, a 9:16 phrase's exit on its wrapper (the group is the stage's)
    for (const it of v.items) {
      const e = enterAt(ctx, t, it.enter, TEXT_ENTER);
      const x = Number.isFinite(it.exit) ? ease('ease.exit', progress(t, it.exit, seconds(ctx, TEXT_ENTER * EXIT_FACTOR))) : 0;
      it.alpha = e * (1 - x);
      paintTextEntrance(ctx, it.inner, it.mask, e);
      // always written (never removed), so the wrapper's style lists its properties in one order whatever was painted
      // before (the DOM of a frame never depends on the order of seeks)
      p.style(it.exitEl, 'opacity', fx(1 - x));
      p.style(it.exitEl, 'visibility', it.alpha > 0.001 ? 'visible' : 'hidden');
    }
  },

  /**
   * Where cue.look_here points (B2 plan §1.5, §1.7): `panel` the medium's frame (also a terminal card), `panel:row:<n>`
   * a terminal's n-th output line (a bar), `<item>` (or `<item>:term:<n>`: its item) the text item while it is on
   * screen. Its box as painted now (read
   * after `update`), edges rounded to whole stage px; null for anything else, a row this scene does not have, or an
   * item not shown.
   * @param {TemplateContext} ctx
   * @param {string} target
   * @returns {FocusRect | null}
   */
  focusRect(ctx, target) {
    const v = ctx.view;
    const parsed = parseTarget(target);
    if (!parsed) return null;
    /** @type {Element | null} */
    let el = null;
    let shape = /** @type {'rect' | 'bar'} */ ('rect');
    let radius = RADIUS.media;
    if (parsed.form === 'panel') el = v.mediaAlpha > 0.01 ? v.frame : null;
    else if (parsed.form === 'panel:row') {
      const line = v.term && v.mediaAlpha > 0.01 ? v.term.lines[/** @type {number} */ (parsed.index)] : null;
      el = line || null;
      shape = 'bar';
      radius = 0;
    } else if (parsed.form === 'item' || parsed.form === 'item:term') {
      // a term of an item: the item it is in (B2c review CG-5: the catalogue lets look_here name a term)
      const it = v.byId.get(String(parsed.item));
      el = it && it.alpha > 0.01 ? it.inner : null;
      radius = SPACE[0];
    }
    const r = el ? ctx.rectOf(el) : null;
    if (!r || !(r.w > 0 && r.h > 0)) return null;
    const x0 = Math.round(r.x);
    const y0 = Math.round(r.y);
    const x1 = Math.round(r.x + r.w);
    const y1 = Math.round(r.y + r.h);
    // the medium and a terminal's line are shapes (the 9:16 stage spans x 60-1020, past the text column): their
    // outline goes round them, never across (cues.js shapeArea, B2c review CG-1); an item is text
    const kind = parsed.form === 'panel' || parsed.form === 'panel:row' ? 'shape' : 'text';
    return { rect: { x: x0, y: y0, w: x1 - x0, h: y1 - y0 }, shape, radius, el: /** @type {Element} */ (el), kind };
  },

  /**
   * The elements holding an item's text, where cue.term finds its [[term]] spans: the item's rich fields in their
   * order (a definition: its term, then its text), one element when it has one. Null for anything else.
   * @param {TemplateContext} ctx
   * @param {string} itemId
   * @returns {Element | Element[] | null}
   */
  itemEl(ctx, itemId) {
    const it = ctx.view.byId.get(String(itemId));
    if (!it || !it.fields.length) return null;
    return it.fields.length === 1 ? it.fields[0] : it.fields.slice();
  },

  /**
   * The content groups a look_here dims (all but the one holding its target): the title, each text item, the medium.
   * The same elements at every t; this template never writes their opacity (it animates the parts inside).
   * @param {TemplateContext} ctx
   * @returns {Element[]}
   */
  dimGroups(ctx) {
    const v = ctx.view;
    return [v.titleGroup, ...v.items.map((/** @type {any} */ it) => it.group), v.mediaGroup].filter(Boolean);
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
   * Static: the box the scene's medium is extracted at (B2 plan §1.4): the largest box the layout ever shows it in,
   * grown by the most a look_here push scales it (_shared.js pushHeadroom), so a picture only ever downscales (§P),
   * through the 0.96 -> 1 entrance and the push alike. 16:9: its 55% region (the same size in both variants). 9:16:
   * the stage under the smallest headline and above the smallest phrase any fit can give (one line at each slot's
   * minimum; `portraitBoxes`). Every remedy in `fixes` only lowers a slot's maximum size or holds it at its minimum,
   * and a slot's minimum never goes below its spec's (slotRange), so no remedy can lay the medium out larger than this
   * box (B2a open item: board_generic's mediaBox could be exceeded by about 3% when a title was capped below its
   * minimum; here no cap reaches below it). One box for every slot (`main`, `panel`).
   * @param {TimedScene} scene
   * @param {Zones} zones
   * @param {'live' | 'render'} _mode
   * @param {string | undefined} _slot
   * @param {import('../core.js').Fixes} _fixes
   * @returns {{w: number, h: number}}
   */
  mediaBox(scene, zones, _mode, _slot, _fixes) {
    if (zones.format === 'landscape') {
      const m = splitRegions(variantOf(scene.look)).media;
      return pushHeadroom({ w: m.x1 - m.x0, h: m.y1 - m.y0 });
    }
    const L = LAYOUT.portrait;
    // one line at each slot's minimum, at the scene's own line heights (the smallest: per script), a pixel under (the
    // page rounds a measured height to whole px)
    const items = textItemsOf(scene);
    const titleH = titleOf(scene) ? Math.floor(L.title.min * lineHeight('display', titleOf(scene))) - 1 : 0;
    const bodyLh = Math.min(...items.map((i) => lineHeight('body', itemString(i))));
    const phraseH = items.length ? Math.floor(L.body.min * bodyLh) - 1 : 0;
    const box = portraitBoxes(scene, titleH, phraseH).media;
    return pushHeadroom({ w: box.x1 - box.x0, h: box.y1 - box.y0 });
  },

  /**
   * Static: every TeX string this template renders for the scene (`texOf`; the same in both formats).
   * @param {TimedScene} scene
   * @param {import('../../shared/types.js').ResolvedLook | null} _look
   * @param {'landscape' | 'portrait'} _fmt
   */
  tex(scene, _look, _fmt) {
    return texOf(scene);
  },

  /**
   * The text on screen (preflight: safe zones, overflow, contrast, type floor), each with its slot (`title`, `body`,
   * `terminal`), its size now, the slot's minimum and its weight. Text on the background takes the background's inks,
   * over its scrim when it has one (`scrimmedBg`: the scrim over the background's worst case).
   * @param {TemplateContext} ctx
   * @returns {TextEntry[]}
   */
  texts(ctx) {
    const v = ctx.view;
    const portrait = v.portrait;
    const L = portrait ? LAYOUT.portrait : LAYOUT.landscape;
    const ink = ctx.onBackground.ink;
    const plain = ctx.onBackground.bg;
    const onScrim = v.scrim ? scrimmedBg(v.scrim, ctx.onBackground, ink) : plain;
    const titleBg = v.titleScrim ? onScrim : plain;
    const bodyBg = (portrait ? v.phraseScrim : v.titleScrim) ? onScrim : plain;
    /** @type {TextEntry[]} */
    const out = [];
    if (v.title) {
      out.push({ el: v.title, role: 'title', slot: 'title', critical: true, ink, bg: titleBg, alpha: v.titleAlpha,
        overflow: v.titleOverflow, size: v.titleSize, min: v.titleRange.min, weight: TITLE_WEIGHT, text: v.titleText });
    }
    for (const it of v.items) {
      out.push({ el: it.content, role: 'body', slot: 'body', critical: true, ink, bg: bodyBg, alpha: it.alpha,
        overflow: it.overflow, size: v.bodySize, min: v.bodyRange.min, weight: it.weight,
        text: (it.content.textContent || '').replace(/\s+/g, ' ').trim() });
    }
    if (v.term) {
      const surface = ctx.roles.color('surface');
      out.push({ el: v.term.pre, role: 'body', slot: 'terminal', critical: true, ink: ctx.roles.color('ink'), bg: surface,
        alpha: v.mediaAlpha, overflow: v.term.overflow, size: v.term.size, min: L.terminal.min, weight: L.terminal.weight,
        text: (v.term.pre.textContent || '').replace(/\s+/g, ' ').trim() });
    }
    if (v.missing) {
      out.push({ el: v.missing, role: 'label', critical: false, ink: ctx.roles.color('ink2'), bg: ctx.roles.color('surface'),
        alpha: v.mediaAlpha, size: MISSING_LABEL_PX[ctx.format], weight: 600, text: v.missing.textContent || '' });
    }
    return out;
  },
};

// ---------------------------------------------------------------------------------------------
// Layout
// ---------------------------------------------------------------------------------------------

/**
 * Words broken inside at size `s` (taller than a line: overflow-wrap broke them; a TeX word: wider than its line).
 * `lhs`: each word's line height.
 * @param {HTMLElement[]} words @param {boolean[]} tex @param {number} s @param {number[]} lhs @param {number} width
 */
function brokenWords(words, tex, s, lhs, width) {
  return words.map((w, i) => (tex[i] ? w.offsetWidth > width + 1 : w.offsetHeight > 1.5 * s * lhs[i]));
}

/**
 * The frame's box for the medium in `box`: a picture or clip contained at its aspect (centred), a terminal card the
 * box's width and its text's height (centred; fitted first), a missing medium at the medium's aspect (16:9 without
 * one). Writes the frame's place.
 * @param {TemplateContext} ctx @param {any} v @param {Box} box
 * @returns {Box}
 */
function placeFrame(ctx, v, box) {
  /** @type {{x: number, y: number, w: number, h: number}} */
  let r;
  if (v.term) {
    const T = (v.portrait ? LAYOUT.portrait : LAYOUT.landscape).terminal;
    const range = slotRange(ctx, 'terminal', { min: T.min, max: T.max, role: 'body' });
    const w = box.x1 - box.x0;
    const avail = box.y1 - box.y0 - 2 * T.pad;
    v.frame.style.width = `${w}px`;
    v.frame.style.height = 'auto';
    let s = range.max;
    for (;;) {
      v.term.pre.style.fontSize = `${s}px`;
      if (v.term.pre.offsetHeight <= avail || s <= range.min) break;
      s = Math.max(range.min, s - 2);
    }
    v.term.size = s;
    const contentH = v.term.pre.offsetHeight;
    const wide = (v.term.pre.scrollWidth || 0) - (v.term.pre.clientWidth || 0);
    v.term.overflow = Math.max(0, contentH - avail, wide > 2 ? wide : 0);
    const hh = Math.min(box.y1 - box.y0, contentH + 2 * T.pad);
    r = { x: box.x0, y: Math.round(box.y0 + (box.y1 - box.y0 - hh) / 2), w, h: Math.round(hh) };
  } else {
    r = containRect(mediaAspect(v.media ? v.media.ref : null), box);
  }
  Object.assign(v.frame.style, { left: `${r.x}px`, top: `${r.y}px`, width: `${r.w}px`, height: `${r.h}px` });
  return { x0: r.x, y0: r.y, x1: r.x + r.w, y1: r.y + r.h };
}

/**
 * 16:9: the medium in its region; the column (the title, then the items at one body size) in the text region, inside
 * its push area when the scene pushes (`landscapeColumn`); the title and body sizes fitted together (`fitColumn`); the
 * column's top level with the medium's top when it fits below it.
 * @param {TemplateContext} ctx @param {any} v
 * @param {{min: number, max: number}} titleRange @param {{min: number, max: number}} bodyRange
 */
function layoutLandscape(ctx, v, titleRange, bodyRange) {
  const regions = splitRegions(v.variant);
  const frame = placeFrame(ctx, v, regions.media);
  v.mediaBox = frame;
  const zone = ctx.zones.critical[0];
  const push = pushTargets(ctx.scene);
  const panelOrigin = v.term ? 'rows' : 'centre';
  // the push room, first from bounds that hold wherever the items end up (their centres anywhere down the column)
  let col = landscapeColumn(regions.text, zone, frame, push, panelOrigin, null);
  let fit = fitColumn(v, col, frame, titleRange, bodyRange);
  if (push.item && v.items.length) {
    // ... then from the items' centres as laid out (the column usually gets back most of that room), kept only when
    // the column laid out in it holds under its own items' centres
    const wider = landscapeColumn(regions.text, zone, frame, push, panelOrigin, fit.centres);
    if (wider.y1 - wider.y0 > col.y1 - col.y0 || wider.x1 - wider.x0 > col.x1 - col.x0) {
      const again = fitColumn(v, wider, frame, titleRange, bodyRange);
      const room = landscapeColumn(regions.text, zone, frame, push, panelOrigin, again.centres);
      const box = { x0: wider.x0, y0: again.top, x1: wider.x1, y1: again.top + again.blockH };
      if (box.x0 >= room.x0 && box.x1 <= room.x1 && box.y0 >= room.y0 && box.y1 <= room.y1) {
        col = wider;
        fit = again;
      } else {
        fit = fitColumn(v, col, frame, titleRange, bodyRange);
      }
    }
  }
  v.textBox = col;
  if (v.titleScrim) placeScrim(v.titleScrim, { x0: col.x0, y0: fit.top, x1: col.x1, y1: fit.top + fit.blockH });
}

/**
 * The 16:9 text column: the text region, inside the push area of the scene's push centres when it pushes: the
 * medium's centre (a terminal's rows: anywhere down its card) for `panel` targets, and for `<item>` targets the
 * column's centre across and, down, the items' centres (`centres`, as laid out) or, without them, anywhere in the
 * region. Three passes settle the column's own centre (each pass moves it by 3% of the pass before's move).
 * @param {Box} text @param {Box} zone @param {Box} frame @param {{item: boolean, panel: boolean}} push
 * @param {'rows' | 'centre'} panelOrigin @param {number[] | null} centres
 * @returns {Box}
 */
export function landscapeColumn(text, zone, frame, push, panelOrigin, centres) {
  if (!push.item && !push.panel) return { ...text };
  let col = { ...text };
  for (let pass = 0; pass < 3; pass++) {
    /** @type {Box | null} */
    let o = null;
    if (push.panel) {
      const cx = (frame.x0 + frame.x1) / 2;
      const cy = (frame.y0 + frame.y1) / 2;
      o = union(o, panelOrigin === 'rows' ? { x0: cx, x1: cx, y0: frame.y0, y1: frame.y1 } : { x0: cx, x1: cx, y0: cy, y1: cy });
    }
    if (push.item) {
      const cx = (col.x0 + col.x1) / 2;
      const ys = centres && centres.length ? centres : [text.y0, text.y1];
      o = union(o, { x0: cx, x1: cx, y0: Math.min(...ys), y1: Math.max(...ys) });
    }
    col = intersect(text, pushArea(zone, /** @type {Box} */ (o)));
  }
  return col;
}

/**
 * Fit the 16:9 column in `col` and place it: the title at the largest size (4 px steps) at which it takes at most its
 * lines and the items still fit below it at a size in their range, the items at the largest body size (4 px steps)
 * at which they all fit with whole words (the title gives up size before the body overflows: the column holds both);
 * at the minimums what does not fit is reported as overflow (preflight shrinks, then blocks: "Shorten text"). The
 * column's top is level with the medium's top when the whole column fits below it, else as high as it must be.
 * Returns the column's top and height and the items' centres down the stage (look_here push origins).
 * @param {any} v @param {Box} col @param {Box} frame
 * @param {{min: number, max: number}} titleRange @param {{min: number, max: number}} bodyRange
 * @returns {{top: number, blockH: number, centres: number[]}}
 */
function fitColumn(v, col, frame, titleRange, bodyRange) {
  const L = LAYOUT.landscape;
  const width = col.x1 - col.x0;
  const avail = col.y1 - col.y0;
  const column = /** @type {HTMLElement} */ (v.column);
  column.style.left = `${col.x0}px`;
  column.style.width = `${width}px`;
  const gap = v.title && v.items.length ? L.titleGap : 0;
  if (v.titleGroup) v.titleGroup.style.marginBottom = `${gap}px`;
  v.items.forEach((/** @type {any} */ it, /** @type {number} */ k) => {
    it.group.style.marginTop = k ? `${L.itemGap}px` : '0px';
  });
  const textW = width - L.marker;
  const words = v.items.flatMap((/** @type {any} */ it) => it.words);
  const tex = v.items.flatMap((/** @type {any} */ it) => it.tex);
  // each word's line height (its item's, per script)
  const lhs = v.items.flatMap((/** @type {any} */ it) => it.words.map(() => it.lh));
  /** @param {number} s the items' height at body size s */
  const total = (s) => {
    column.style.fontSize = `${s}px`;
    return v.items.reduce((/** @type {number} */ a, /** @type {any} */ it, /** @type {number} */ k) => a + it.group.offsetHeight + (k ? L.itemGap : 0), 0);
  };
  /** @type {boolean[]} */
  let unavoidable = [];
  if (v.items.length) {
    total(bodyRange.min);
    unavoidable = brokenWords(words, tex, bodyRange.min, lhs, textW);
  }
  /** @param {number} room @returns {{size: number, fits: boolean}} the largest body size whose items fit `room` */
  const body = (room) => {
    if (!v.items.length) return { size: bodyRange.min, fits: true };
    for (let s = bodyRange.max; ; s = Math.max(bodyRange.min, s - 4)) {
      const fits = total(s) <= room && brokenWords(words, tex, s, lhs, textW).every((b, i) => !b || unavoidable[i]);
      if (fits || s <= bodyRange.min) return { size: s, fits };
    }
  };
  let titleH = 0;
  let b = body(avail);
  if (v.title) {
    let max = titleRange.max;
    for (;;) {
      const t = fitLines(v.title, { max, min: titleRange.min, lines: L.title.lines, lh: v.titleLh }, v.titleWords);
      v.titleSize = t.size;
      v.titleOverflow = t.overflow;
      titleH = v.title.offsetHeight;
      b = body(avail - titleH - gap);
      if (b.fits || t.size <= titleRange.min) break;
      max = Math.max(titleRange.min, t.size - 4); // the next pass: the largest title size below this one
    }
  }
  column.style.fontSize = `${b.size}px`;
  v.bodySize = b.size;
  const itemsH = v.items.length ? total(b.size) : 0;
  const blockH = (v.title ? titleH + gap : 0) + itemsH;
  // whole px, never pushed below the column's end by the rounding
  const top = Math.max(Math.ceil(col.y0), Math.min(Math.round(frame.y0), Math.floor(col.y1 - blockH)));
  column.style.top = `${top}px`;
  // overflow: what of each item ends below the column, and TeX wider than its line; the items' centres
  let y = top + (v.title ? titleH + gap : 0);
  /** @type {number[]} */
  const centres = [];
  v.items.forEach((/** @type {any} */ it, /** @type {number} */ k) => {
    if (k) y += L.itemGap;
    const ih = it.group.offsetHeight;
    centres.push(y + ih / 2);
    y += ih;
    const wide = Math.max(0, ...it.words.map((/** @type {HTMLElement} */ w, /** @type {number} */ i) => (it.tex[i] ? w.offsetWidth - textW : 0)));
    it.overflow = Math.max(0, y - col.y1, wide > 2 ? wide : 0);
  });
  return { top, blockH, centres };
}

/**
 * 9:16: the headline fitted in its region (one line preferred, R-25), every phrase fitted at one size (the largest at
 * which each takes at most its lines with whole words), the phrase box ending above the caption band at the tallest
 * phrase's height, and the medium in the stage between them (`portraitBoxes`, which the static mediaBox shares).
 * @param {TemplateContext} ctx @param {any} v
 * @param {{min: number, max: number}} titleRange @param {{min: number, max: number}} bodyRange
 */
function layoutPortrait(ctx, v, titleRange, bodyRange) {
  const L = LAYOUT.portrait;
  // the boxes before measuring (the push room narrows the headline and the phrase column)
  const pre = portraitBoxes(ctx.scene, 1, 1);
  let titleH = 0;
  if (v.title && v.titleGroup) {
    Object.assign(v.titleGroup.style, { position: 'absolute', left: `${pre.title.x0}px`, top: `${pre.title.y0}px`, width: `${pre.title.x1 - pre.title.x0}px` });
    const fit = fitLines(v.title, { max: titleRange.max, min: titleRange.min, lines: L.title.lines, lh: v.titleLh, prefer: L.title.prefer }, v.titleWords);
    v.titleSize = fit.size;
    v.titleOverflow = fit.overflow;
    titleH = v.title.offsetHeight;
  }
  // phrases: each fitted alone; all drawn at the smallest of their sizes, so the phrases of a scene match
  const width = pre.phrase.x1 - pre.phrase.x0;
  let size = bodyRange.max;
  for (const it of v.items) {
    Object.assign(it.group.style, { position: 'absolute', left: `${pre.phrase.x0}px`, width: `${width}px` });
    const fit = fitLines(it.content, { max: bodyRange.max, min: bodyRange.min, lines: L.body.lines, lh: it.lh }, it.words, it.tex);
    size = Math.min(size, fit.size);
  }
  let phraseH = 0;
  for (const it of v.items) {
    it.content.style.fontSize = `${size}px`;
    phraseH = Math.max(phraseH, it.group.offsetHeight);
  }
  v.bodySize = size;
  const boxes = portraitBoxes(ctx.scene, v.title ? Math.max(1, titleH) : 0, v.items.length ? Math.max(1, phraseH) : 0);
  for (const it of v.items) {
    it.group.style.top = `${boxes.phrase.y0}px`;
    const h = it.content.offsetHeight;
    const lines = Math.round(h / (size * it.lh));
    const wide = Math.max(0, ...it.words.map((/** @type {HTMLElement} */ w, /** @type {number} */ i) => (it.tex[i] ? w.offsetWidth - width : 0)));
    const over = lines > L.body.lines ? Math.max(1, h - L.body.lines * size * it.lh) : 0;
    it.overflow = Math.max(over, wide > 2 ? wide : 0);
  }
  v.textBox = boxes.phrase;
  v.mediaBox = placeFrame(ctx, v, boxes.media);
  if (v.titleScrim && v.title) placeScrim(v.titleScrim, { x0: boxes.title.x0, y0: boxes.title.y0, x1: boxes.title.x1, y1: boxes.title.y0 + titleH });
  if (v.phraseScrim && v.items.length) placeScrim(v.phraseScrim, { x0: boxes.phrase.x0, y0: boxes.phrase.y0, x1: boxes.phrase.x1, y1: boxes.phrase.y0 + phraseH });
}

/**
 * A scrim box: the text's box grown by 24 px sideways and 16 px up and down (board_generic's title-block scrim padding),
 * so its text stays on the zone edge and the scrim reaches past it.
 * @param {HTMLElement} el @param {Box} b
 */
function placeScrim(el, b) {
  const px = SPACE[2];
  const py = SPACE[1];
  Object.assign(el.style, { left: `${Math.round(b.x0 - px)}px`, top: `${Math.round(b.y0 - py)}px`, width: `${Math.round(b.x1 - b.x0 + 2 * px)}px`, height: `${Math.round(b.y1 - b.y0 + 2 * py)}px` });
}
