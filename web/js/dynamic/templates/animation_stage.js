// @ts-check
/**
 * `animation_stage` (DYNAMIC_VIDEO_DESIGN §4.3 B, R1, class `media`; FORCED for a `simulation` scene): the generated
 * animation on the stage with its caption / label furniture. Data (§4.3, templates.py `_fit_animation_stage`): a
 * `simulation` scene (its rendered `ManimSpec`, one animation step per beat) or a board of at most three text items
 * with a `manim` side panel.
 *
 * **The animation is a MEDIUM like any other.** It reaches the page through the stage's media path (`ctx.media`, the
 * box this template's static `mediaBox` gives it, `media.js` `mediaPlan` / the worker's extracted frames), so this
 * module draws the frame, the title and the step labels and nothing else: never a player of its own, never a clock
 * (`<video>`, `Date`, `requestAnimationFrame` are all refused by the source scan). The same clip rules as
 * `full_bleed_media` and `media_split`: a clip is **never scaled** (no 0.96 -> 1 entrance, no camera push - this
 * template is not `push_ok`, so `cue.look_here` keeps its outline form, deviation 28) and its entrance is a plain
 * fade. A still standing in for the animation (an image asset, a poster) is treated exactly the same, so the live
 * player and the MP4 share one layout and one choreography whatever the asset stage produced.
 *
 * Layout (§2.5 regions from the catalogue, `core.js` REGIONS; whole stage px, `boxes()`), one structure in both
 * formats - the title above, the animation in the middle, the step label in the lower third:
 * - 16:9: the title across the critical box (`full`, x 96-1824) at its top, the animation contained at its own aspect
 *   in what is left between the title and the step label (centred, 24 px media radius and the soft shadow: the layout
 *   tokens' `radius.media` and shadow level 3), the step label across the same column at the bottom of `full`, just
 *   above the caption band.
 * - 9:16: the title in the `headline` region, the animation across the `stage` (x 60-1020; the `tall` region's top
 *   without a title), the step label in the lower critical box from the headline's left edge (x 192: one left edge
 *   for the text, as media_split's B2c review V7) to the TikTok rail (x 780), ending 8 px above the caption band.
 * - A scene with no step label gives the animation every pixel the title leaves; a scene with no title too (the
 *   `tall` region in 9:16). The medium only ever *downscales* (§P condition 1), twice over: the box `mediaBox`
 *   extracts it at is computed from the *smallest* title and label any fit can give, so no layout and no preflight
 *   remedy can show it in a larger box; and `frameRect` never draws it larger than the asset itself, because
 *   `media.js mediaSize` caps the extraction at the source (a 1280x720 Manim render would otherwise be stretched
 *   across the 16:9 stage). Rendering the animation at the stage's own size is B4's Manim config work.
 * - **Reading of §4.3's "a step label appears as a lower third on each beat"** (deviation, recorded with the spec):
 *   the label is the lower third *of the frame, under the animation*, not a band over it. Text over a clip's own
 *   pixels cannot be checked for contrast analytically (`colour.py` returns None for a translucent layer; the design's
 *   post-render pixel-contrast warning is not built - `b2crit_quality.md` names these very lower thirds), while text
 *   on the background is checked like any other. The animation keeps the room the label does not use.
 *
 * What the label says: the step of the animation this beat drives (`TimedBeat.visual_cue`, §4.3 "one step per beat"),
 * one at a time, each replacing the one before on its beat; or, on a board with a `manim` panel, the scene's text
 * items in board order (they carry ids, so `cue_term` and `look_here` reach them). A scene whose board holds anything
 * else, more than MAX_ITEMS text items, or no medium at all (a simulation whose Manim render failed: `compose/
 * timeline.py` gives it a board of bullets built from the same visual cues) is handed to `board_generic` **whole**,
 * every hook with it (`drawsScene`, list_build's and formula_focus's convention): this template never drops content.
 *
 * Motion (§4.8; every duration times the personality's scale `ctx.motion.scale`, cue timings excepted): the title
 * enters with the personality's text entrance (Soft fades in with a 16 px rise, Crisp is revealed from its baseline)
 * over TEXT_ENTER; the animation fades in over MEDIA_ENTER from MEDIA_DELAY (or its panel's `show_at`), never scaled;
 * each step label enters on its beat with the text entrance and the one before it leaves 30% faster just before
 * (EXIT_FACTOR), a label with no beat of its own waiting until the one before has been shown MIN_DWELL. Reduced
 * motion (live only): fades in place. Every frame is a pure function of t.
 *
 * Cues (cues.js draws them, never this module, §4.2): `cue_term` on `<item>` / `<item>:term:<n>` finds the label's
 * `[[term]]` spans (`itemEl`, built by `_shared.js richWords` so the `.rt-k` spans survive the word split);
 * `look_here` on `<item>` outlines the label on screen and on `panel` the animation's frame (`focusRect`, `kind:
 * 'shape'`: its outline runs round the frame, never across it, B2c review CG-1) - always the outline form, because the
 * scene shows a clip. Every text slot reports to preflight (§7.12) with its slot, size, minimum and weight;
 * preflight's fixes reach the sizes through `_shared.js slotRange` (a size cap, the safe layout = every slot at its
 * minimum, the R-29 floor).
 *
 * Deferred (deviation 17, design §4.3 "Colours" [RV L-17]): the Manim colour mapping (`BLUE` -> entity 0, ...), the
 * asset-family background and the `colour_is_content` key are B4's, and the dark card surface that frames a Manim
 * clip in a light family needs `themes.code_surface` in the timeline (B2f). Until then the clip's own dark background
 * is the card: the frame is the medium itself, with the media radius and the shadow.
 *
 * The constants below are B2 proposals, pending the owner's review (the design gives the stage, the radius, the
 * shadow and the timings; the sizes and gaps are this template's): aadhi/compose/dynamic/templates.py mirrors LAYOUT
 * as the template's slots, and a jsdom test pins the two together.
 */

import { h } from '../../shared/dom.js';
import { ease } from '../../motion/easing.js';
import { progress } from '../../motion/track.js';
import { splitWords } from '../../motion/text.js';
import { mathSpans, plainText } from '../../player/richtext.js';
import { LAYOUT_TOKENS, ZONES, fx, parseTarget } from '../core.js';
import { shownIn } from '../media.js';
import { itemTimings } from '../state.js';
import boardGeneric from './board_generic.js';
import {
  RADIUS, SPACE, containRect, enterAt, fitLines, group, mediaAspect, paintMedia, paintTextEntrance, region,
  revealMask, richWords, seconds, shownPanel, slotRange,
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

export const ID = 'animation_stage';

/** UI labels (kept together for localisation). */
export const LABELS = Object.freeze({
  callout_info: 'Info',
  callout_tip: 'Tip',
  callout_warning: 'Watch out',
  /** the definition's term and meaning */
  definitionJoin: ' — ',
  /** the card's label when this mode shows no animation (board_generic's convention: never an empty black box) */
  mediaMissing: 'Animation unavailable',
});

/** The board item kinds drawn as a step label (templates.py TEXT_KINDS: what `_media_board` counts as text items). */
export const TEXT_KINDS = Object.freeze(['heading', 'bullet', 'paragraph', 'definition', 'takeaway', 'callout_info',
  'callout_tip', 'callout_warning']);
/** Text kinds drawn bold (a heading, a takeaway): the weight the slot reports for them. */
const BOLD_KINDS = new Set(['heading', 'takeaway']);
/** §4.4's media row (templates.py `_media_board`): at most three text items beside the medium. */
export const MAX_ITEMS = 3;

/** The side panels whose medium is this template's animation (§4.3 data: a `manim` panel). */
export const PANEL_KINDS = Object.freeze(['manim']);

/**
 * Per-format layout (stage px; B2 proposals, pending the owner's review: the title sits below R-29's nominal heading
 * range and above its floors, deviation 31's precedent). 16:9: the title 64-88 over at most 2 lines (one preferred),
 * the step label 44-56 over at most 2 lines at weight 700 (R-29's label floor is 40); 9:16: the title 56-88 (one line
 * preferred, two allowed: deviation 32), the label 48-64 (R-29's 9:16 label floor is 44, its body 56-64; >= 600 below
 * 64 px). Each is fitted on the page in 4 px steps (`_shared.js fitLines`, the rule textfit.py mirrors), never below
 * the R-29 floor (`slotRange`). Line heights: LINE_HEIGHT, per script.
 * - `gap`: title to animation and animation to label;
 * - `labelBottom` (9:16): where the label ends, 8 px (the spacing scale's first step) above the caption band (the
 *   catalogue's `zones.caption.y0`, y 1040; B2c review CG-3: catalogue geometry, never a literal).
 */
export const LAYOUT = Object.freeze({
  landscape: Object.freeze({
    title: Object.freeze({ max: 88, min: 64, lines: 2, prefer: 1 }),
    label: Object.freeze({ max: 56, min: 44, lines: 2 }),
    labelWeight: 700,
    gap: SPACE[3],
  }),
  portrait: Object.freeze({
    title: Object.freeze({ max: 88, min: 56, lines: 2, prefer: 1 }),
    label: Object.freeze({ max: 64, min: 48, lines: 2 }),
    labelWeight: 700,
    gap: SPACE[2],
    labelBottom: ZONES.portrait.caption.y0 - SPACE[0],
  }),
});

/**
 * Line heights (§4.6 type scale, the catalogue's `type.scale`; a jsdom test pins them): the title the heading role's,
 * the label the body role's; Indic text (Devanagari to Sinhala) the Indic minimums (plan §2 R-29), because Tamil and
 * Devanagari marks need the room (B2c review V5).
 */
export const LINE_HEIGHT = Object.freeze({
  heading: Object.freeze({ latin: 1.15, indic: 1.25 }),
  body: Object.freeze({ latin: 1.35, indic: 1.45 }),
});

/** Indic scripts (Devanagari to Sinhala), whose text takes the taller line heights (key_terms' rule). */
const INDIC = new RegExp(`[${['Devanagari', 'Bengali', 'Gurmukhi', 'Gujarati', 'Oriya', 'Tamil', 'Telugu', 'Kannada',
  'Malayalam', 'Sinhala'].map((x) => `\\p{Script=${x}}`).join('')}]`, 'u');

/**
 * The line height of `text` in type role `role` (LINE_HEIGHT: Indic text takes the Indic value). Pure.
 * @param {'heading' | 'body'} role @param {string} text
 */
export function lineHeight(role, text) {
  return INDIC.test(plainText(String(text || ''))) ? LINE_HEIGHT[role].indic : LINE_HEIGHT[role].latin;
}

/** Title weight (the display face; `.dyn-title`'s weight, so the slot measures like board_generic's). */
export const TITLE_WEIGHT = 700;
/** The label over a card this mode shows no animation in (board_generic's `.dyn-media-missing`, R-29 labels). */
export const MISSING_LABEL_PX = Object.freeze({ landscape: 52, portrait: 48 });

// Motion (§4.8), seconds before the personality's scale
export const TITLE_DELAY = 0.08;
/** §4.8 entrance 400-600 ms (list_build's per-item 450 ms, the same for a step label). */
export const TEXT_ENTER = 0.45;
/** §4.3: a medium enters over 500 ms - here a fade only (a clip is never scaled, deviation 28). */
export const MEDIA_ENTER = 0.5;
/** The animation enters this long after the scene starts (a side panel shown later: at its `show_at`). */
export const MEDIA_DELAY = 0.15;
/** §4.8: exits are 30% shorter than entrances. */
export const EXIT_FACTOR = 0.7;
/** A label is fully shown at least this long before a label with no beat of its own replaces it (board_generic's). */
export const MIN_DWELL = 1.5;
/** A label tied to a beat enters this long before the beat's speech (R-17, state.js REVEAL_LEAD). */
export const LABEL_LEAD = 0.12;

// ---------------------------------------------------------------------------------------------
// What the scene shows (pure)
// ---------------------------------------------------------------------------------------------

/**
 * The scene's side panel when the layout shows it **and** it is one this template draws - a `manim` panel, whose
 * medium is the animation itself (PANEL_KINDS). Every other shown panel is a second visual this template lays out
 * nothing for; `drawsScene` hands such a scene to board_generic whole (`_shared.js shownPanel` is the general
 * question: any panel the layout shows).
 * @param {TimedScene} scene
 */
function manimPanel(scene) {
  const sp = scene.side_panel;
  if (!sp || scene.layout?.show_side_panel === false) return null;
  return PANEL_KINDS.includes(String(sp.panel && sp.panel.kind)) ? sp : null;
}

/**
 * The scene's animation (board_generic's order): its own clip or picture, an interactive scene's poster, else a
 * `manim` side panel's medium; `shown`: whether `mode` can show it (`media.js shownIn`: the render page leaves out
 * live-only GIFs and media kept out of the MP4). The medium is laid out (at its aspect) whether or not the mode shows
 * it, so the live player and the MP4 share one layout. Null without one.
 * @param {TimedScene} scene
 * @param {'live' | 'render'} [mode]
 * @returns {{slot: 'main' | 'panel', ref: MediaRef, start: number, shown: boolean} | null}
 */
export function mediaOf(scene, mode = 'live') {
  const m = mode === 'render' ? 'render' : 'live';
  if (scene.media) return { slot: 'main', ref: scene.media, start: 0, shown: shownIn(scene.media, m) };
  if (scene.poster) return { slot: 'main', ref: scene.poster, start: 0, shown: shownIn(scene.poster, m) };
  const sp = manimPanel(scene);
  if (sp && sp.media) {
    return { slot: 'panel', ref: sp.media, start: Math.max(0, Number(sp.show_at) || 0), shown: shownIn(sp.media, m) };
  }
  return null;
}

/**
 * Whether the medium is a clip (a video, in either mode). A clip is never scaled and never pushed (deviation 28,
 * cues.js `showsClip`); this template holds a still to the same rule, so one choreography serves both.
 * @param {{ref: MediaRef} | null} media
 */
export function clipShown(media) {
  return !!media && media.ref.kind === 'video';
}

/**
 * The text items drawn as step labels, in board order (TEXT_KINDS; any other kind is not this template's data, §4.3).
 * @param {TimedScene} scene
 * @returns {BoardItem[]}
 */
export function textItemsOf(scene) {
  return (scene.board || []).filter((i) => TEXT_KINDS.includes(i.kind));
}

/**
 * Whether this template draws the scene whole (B2c review CG-2: templates.py `_fit_animation_stage` must give 0
 * exactly when this is false, so a planned scene is never redrawn by board_generic): there is a medium to lay out,
 * and the board holds nothing but at most MAX_ITEMS text items. A simulation whose Manim render failed carries no
 * medium and a board of bullets built from its visual cues instead (compose/timeline.py
 * `_simulation_fallback_board`); `board_generic` draws that scene whole, as it does today.
 *
 * A **side panel** is only ever drawn here as the animation itself (a `manim` panel, `manimPanel`): the layout holds
 * the title, the animation and the step labels, and nothing else. So a scene whose shown panel is *not* the medium -
 * a `simulation` whose own Manim render succeeded beside a terminal, chart or picture card - is board_generic's
 * whole: board_generic draws that card, and dropping it would break "never drop content" (§7.12, B2c review CG-2).
 * `_fit_animation_stage` refuses the same scene, so the forced `simulation` row falls through instead of planning a
 * template the page would hand back.
 * @param {TimedScene} scene
 */
export function drawsScene(scene) {
  const media = mediaOf(scene); // `mediaOf`'s nullness does not depend on the mode
  if (media === null) return false;
  if (shownPanel(scene) && media.slot !== 'panel') return false;
  const board = scene.board || [];
  return board.every((i) => TEXT_KINDS.includes(i.kind)) && board.length <= MAX_ITEMS;
}

/** Whether `ctx.view` came from board_generic (a scene this template does not draw). @param {TemplateContext} ctx */
function isFallback(ctx) {
  return !!ctx.view && ctx.view.asFallback === true;
}

/**
 * The step labels of a scene, in entrance order (§4.3 "one step per beat"): the board's text items when it has any
 * (they carry ids, so cues reach them, and nothing a timeline names is dropped), otherwise one label per beat that
 * says what the animation does (`TimedBeat.visual_cue`, entering LABEL_LEAD before the beat's speech, R-17). `item`
 * is the board item a label draws (null for a beat's cue); `initial` marks a label no narration moment anchors, which
 * waits for MIN_DWELL instead. Pure.
 * @param {TimedScene} scene
 * @returns {{id: string, text: string, item: BoardItem | null, enter: number, initial: boolean}[]}
 */
export function stepsOf(scene) {
  const items = textItemsOf(scene);
  if (items.length) {
    const timings = new Map(itemTimings(scene).map((t) => [t.id, t]));
    return items
      .map((item) => {
        const t = timings.get(item.id);
        return { id: item.id, text: itemString(item), item, enter: t ? t.enter : 0, initial: !t || t.initial };
      })
      .sort((a, b) => a.enter - b.enter);
  }
  /** @type {{id: string, text: string, item: null, enter: number, initial: boolean}[]} */
  const out = [];
  for (const beat of scene.beats || []) {
    const text = String(beat.visual_cue || '').trim();
    if (!text) continue;
    out.push({
      id: `beat:${String(beat.beat_id || out.length)}`, text, item: null,
      enter: Math.max(0, (Number(beat.start) || 0) - LABEL_LEAD), initial: false,
    });
  }
  return out;
}

/** The text of a text item as one string (its term and text; the line height reads its script). @param {BoardItem} item */
function itemString(item) {
  return item.kind === 'definition' && item.term
    ? `${item.term}${item.text ? LABELS.definitionJoin : ''}${item.text || ''}`
    : String(item.text || '');
}

/**
 * When each step label is on screen: `enter` (its entrance starts) and `exit` (its exit starts; Infinity for the
 * last). A label on a beat enters on time and the label before it leaves just before (EXIT_FACTOR of the entrance); a
 * label no moment anchors (`initial`) waits until the one before has been fully shown for MIN_DWELL, but no longer
 * than until a second before the scene ends (its entrance done by then; never before the one before has left). Pure;
 * `scale` is the personality's duration scale (media_split's phrase rule, the same reading of §4.8).
 * @param {{id: string, enter: number, initial: boolean}[]} steps   in entrance order
 * @param {number} duration   the scene's
 * @param {number} [scale]
 * @returns {{id: string, enter: number, exit: number}[]}
 */
export function labelSchedule(steps, duration, scale = 1) {
  const enterDur = TEXT_ENTER * scale;
  const exitDur = enterDur * EXIT_FACTOR;
  const latest = Math.max(0, Number(duration) - enterDur - 1);
  /** @type {{id: string, enter: number, exit: number}[]} */
  const out = [];
  for (const s of steps) {
    const prev = out[out.length - 1];
    let enter = s.enter;
    if (prev) {
      if (s.initial) enter = Math.max(enter, Math.min(prev.enter + enterDur + MIN_DWELL + exitDur, Math.max(latest, enter)));
      // the one before leaves just before this one enters (never before it began to enter)
      prev.exit = Math.max(prev.enter, enter - exitDur);
      enter = Math.max(enter, prev.exit + exitDur);
    }
    out.push({ id: s.id, enter, exit: Infinity });
  }
  return out;
}

/**
 * The three boxes of a scene, given the title's height (0: none) and the tallest step label's (0: none). Shared by
 * `layout` (measured heights) and the static `mediaBox` (the smallest heights any fit can give), so the box a clip is
 * extracted at is never smaller than the box it is shown in. Pure, whole px.
 * @param {'landscape' | 'portrait'} format
 * @param {number} titleH
 * @param {number} labelH
 * @returns {{title: Box, label: Box, media: Box}}
 */
export function boxes(format, titleH, labelH) {
  const at = { format };
  if (format === 'landscape') {
    const L = LAYOUT.landscape;
    const area = region(at, 'full');
    const title = { x0: area.x0, y0: area.y0, x1: area.x1, y1: area.y0 + titleH };
    const label = { x0: area.x0, y0: area.y1 - labelH, x1: area.x1, y1: area.y1 };
    const top = titleH > 0 ? Math.ceil(title.y1 + L.gap) : area.y0;
    const bottom = labelH > 0 ? Math.floor(label.y0 - L.gap) : area.y1;
    return { title, label, media: { x0: area.x0, y0: top, x1: area.x1, y1: Math.max(top, bottom) } };
  }
  const L = LAYOUT.portrait;
  const headline = region(at, 'headline');
  const stage = region(at, 'stage');
  const tall = region(at, 'tall');
  const below = region(at, 'text-below');
  // the label: the lower critical box from the headline's left edge (one left edge for the text, B2c review V7) to
  // the TikTok rail's x 780, ending above the caption band
  const title = { x0: headline.x0, y0: headline.y0, x1: headline.x1, y1: headline.y0 + titleH };
  const label = { x0: headline.x0, y0: L.labelBottom - labelH, x1: below.x1, y1: L.labelBottom };
  // the medium starts right under the headline (the `tall` region's top without one), never above it
  const top = titleH > 0 ? Math.max(tall.y0, Math.ceil(title.y1 + L.gap)) : tall.y0;
  const bottom = labelH > 0 ? Math.min(stage.y1, Math.floor(label.y0 - L.gap)) : stage.y1;
  return { title, label, media: { x0: stage.x0, y0: top, x1: stage.x1, y1: Math.max(top, bottom) } };
}

/**
 * The frame's box for the animation in `box`: the medium contained at its own aspect and centred, and **never larger
 * than the medium itself** (§P condition 1: a picture only ever downscales from its source, and `media.js mediaSize`
 * caps the extraction at the source too, so a frame past it would upscale). A medium with no stored size (and the
 * card shown when this mode has no animation) fills the box. Whole px, pure.
 * @param {MediaRef | null} ref
 * @param {Box} box
 * @returns {{x: number, y: number, w: number, h: number}}
 */
export function frameRect(ref, box) {
  const r = containRect(mediaAspect(ref), box);
  const sw = ref && Number(ref.width) > 0 ? Number(ref.width) : Infinity;
  const sh = ref && Number(ref.height) > 0 ? Number(ref.height) : Infinity;
  const k = Math.min(1, sw / Math.max(1, r.w), sh / Math.max(1, r.h));
  if (!(k < 1)) return r;
  const w = Math.round(r.w * k);
  const hh = Math.round(r.h * k);
  return {
    x: Math.round(box.x0 + (box.x1 - box.x0 - w) / 2), y: Math.round(box.y0 + (box.y1 - box.y0 - hh) / 2), w, h: hh,
  };
}

/**
 * Every TeX string this template converts for a scene (the static `tex` hook; render.js converts them before
 * `ready`): the `$...$` spans of the step labels, in their order. The title and the card's missing label are plain
 * text (board_generic's title rule).
 * @param {TimedScene} scene
 * @returns {{tex: string, display: boolean}[]}
 */
export function texOf(scene) {
  /** @type {{tex: string, display: boolean}[]} */
  const out = [];
  if (!drawsScene(scene)) return out;
  for (const step of stepsOf(scene)) {
    if (!step.text.includes('$')) continue;
    for (const tex of mathSpans(step.text)) if (tex.trim()) out.push({ tex, display: false });
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
 * One step label: its rich-lite text as word spans that keep their `[[term]]` spans (`richWords`; cue.term's
 * targets), with a callout's label before them and a definition's term in bold. `fields` are the rich hosts in the
 * item's field order (term, text), which is what `itemEl` hands cue.term.
 * @param {TemplateContext} ctx
 * @param {{text: string, item: BoardItem | null}} step
 * @param {number} weight
 * @param {number} lh
 */
function labelText(ctx, step, weight, lh) {
  const item = step.item;
  const bold = !!item && BOLD_KINDS.has(item.kind);
  const content = h('div', {
    class: 'dyn-as-text', dir: 'auto',
    style: { overflowWrap: 'anywhere', minWidth: '0', fontWeight: String(bold ? 700 : weight), lineHeight: String(lh) },
  });
  /** @type {HTMLElement[]} */
  const fields = [];
  /** @type {HTMLElement[]} */
  const words = [];
  /** @type {boolean[]} */
  const tex = [];
  /** @param {string} text @param {boolean} strong */
  const field = (text, strong) => {
    const host = h('span', { class: 'dyn-as-field', style: { fontWeight: strong ? '700' : null } });
    const r = richWords(ctx, host, text);
    words.push(...r.words);
    tex.push(...r.tex);
    fields.push(host);
    content.appendChild(host);
  };
  const label = item ? /** @type {Record<string, string>} */ (LABELS)[item.kind] : undefined;
  if (typeof label === 'string' && item && item.kind.startsWith('callout_')) {
    content.append(h('span', { class: 'dyn-as-kind', style: { fontWeight: '700' }, text: label }), ' ');
  }
  if (item && item.kind === 'definition' && item.term) {
    field(String(item.term), true);
    if (item.text) content.append(LABELS.definitionJoin);
    if (item.text) field(String(item.text), false);
  } else {
    field(step.text, false);
  }
  return { content, fields, words, tex, weight: bold ? 700 : weight, lh };
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
      // no medium to lay out, or a board this template does not draw: board_generic draws the scene whole
      const inner = boardGeneric.build(ctx);
      inner.asFallback = true;
      return inner;
    }
    const L = LAYOUT[format];
    const ink = ctx.onBackground.ink;
    // the Crisp entrance slides text up inside a mask; every other entrance fades in place with a rise (no mask: it
    // would clip the rise)
    const masked = !!ctx.motion && ctx.motion.entrance === 'baseline_reveal';
    /** @param {HTMLElement} el */
    const maskOf = (el) => (masked ? revealMask(el) : null);
    const root = h('div', {
      class: ['dyn-as', `dyn-as--${format}`],
      style: { position: 'absolute', left: '0px', top: '0px', width: `${ctx.zones.width}px`, height: `${ctx.zones.height}px` },
    });

    // the animation: the medium in a frame with the media radius and the soft shadow (its own dark background is the
    // card until themes.code_surface reaches the timeline, B2f); nothing this mode can show gets the panel's title
    const media = mediaOf(scene, ctx.mode);
    const frame = h('div', {
      class: 'dyn-as-frame',
      style: {
        position: 'absolute', overflow: 'hidden', borderRadius: `${RADIUS.media}px`,
        boxShadow: LAYOUT_TOKENS.shadow['3'], transformOrigin: '50% 50%',
      },
    });
    const text = titleOf(scene);
    /** @type {import('../media.js').MediaView | null} */
    let mediaView = null;
    /** @type {HTMLElement | null} */
    let missing = null;
    if (media && media.shown) {
      // sized by this template's mediaBox for the medium's slot: the box mediaPlan extracts a clip at
      mediaView = ctx.media(media.ref, { slot: media.slot, start: media.start, alt: text });
      frame.appendChild(mediaView.el);
    }
    if (!mediaView || mediaView.missing) {
      const sp = scene.side_panel;
      const caption = (sp && sp.panel && sp.panel.title) || text || LABELS.mediaMissing;
      frame.style.background = roles.color('surface');
      missing = h('div', {
        class: 'dyn-as-missing', dir: 'auto', text: String(caption),
        style: {
          position: 'absolute', left: '0px', top: '0px', right: '0px', bottom: '0px', display: 'flex',
          alignItems: 'center', justifyContent: 'center', padding: '0 1em', textAlign: 'center',
          fontSize: `${MISSING_LABEL_PX[format]}px`, fontWeight: '600', lineHeight: '1.2',
          color: roles.color('ink2'), overflowWrap: 'anywhere',
        },
      });
      frame.appendChild(missing);
    }
    const mediaGroup = group('dyn-as-mediagroup', frame);

    // the title
    /** @type {HTMLElement | null} */
    let title = null;
    /** @type {HTMLElement[]} */
    let titleWords = [];
    if (text) {
      title = h('h2', {
        class: 'dyn-as-title', dir: 'auto',
        style: {
          margin: '0', color: ink, fontFamily: 'var(--dyn-font-display)', fontWeight: String(TITLE_WEIGHT),
          lineHeight: String(lineHeight('heading', text)), overflowWrap: 'anywhere',
        },
      });
      titleWords = wordSpans(title, text, ctx.locale);
    }
    const titleMask = title ? maskOf(title) : null;
    const titleGroup = title ? group('dyn-as-titlegroup', titleMask || title) : null;

    // the step labels: group (the stage's dim) > exit wrapper (a label leaves) > [mask] > inner (the entrance)
    const steps = stepsOf(scene);
    const timed = new Map(labelSchedule(steps, Number(scene.duration) || 0, seconds(ctx, 1)).map((s) => [s.id, s]));
    const labels = steps.map((step, order) => {
      const parts = labelText(ctx, step, L.labelWeight, lineHeight('body', step.text));
      const inner = h('div', {
        class: 'dyn-as-label', dataset: step.item ? { item: step.item.id } : {},
        style: { color: ink },
      }, parts.content);
      const mask = maskOf(inner);
      const exitEl = h('div', { class: 'dyn-as-exit' }, mask || inner);
      const g = group('dyn-as-labelgroup', exitEl);
      const when = timed.get(step.id);
      return {
        step, id: step.id, item: step.item, order, group: g, exitEl, mask, inner, ...parts,
        enter: when ? when.enter : step.enter, exit: when ? when.exit : Infinity, alpha: 0, overflow: 0,
      };
    });

    root.append(...[mediaGroup, titleGroup, ...labels.map((it) => it.group)].filter((x) => !!x));
    ctx.root.appendChild(root);
    return {
      asFallback: false, root, format, title, titleText: text, titleWords, titleMask, titleGroup,
      labels, byId: new Map(labels.filter((it) => it.item).map((it) => [/** @type {BoardItem} */ (it.item).id, it])),
      media, mediaView, frame, mediaGroup, missing, ink,
      mediaStart: media && media.start > 0 ? media.start : MEDIA_DELAY,
      clip: clipShown(media),
      titleSize: L.title.max, titleRange: { min: L.title.min, max: L.title.max }, titleOverflow: 0,
      titleLh: lineHeight('heading', text),
      labelSize: L.label.max, labelRange: { min: L.label.min, max: L.label.max },
      titleAlpha: 0, mediaAlpha: 0,
      /** laid out (stage px) */
      titleBox: /** @type {Box | null} */ (null), labelBox: /** @type {Box | null} */ (null),
      mediaBox: /** @type {Box | null} */ (null),
    };
  },

  /**
   * Measure and fit (idempotent: every pass starts from the slot ranges): the title, the step labels at one size, and
   * the animation's frame in what they leave (`boxes`, which the static `mediaBox` shares).
   * @param {TemplateContext} ctx
   */
  layout(ctx) {
    if (isFallback(ctx)) {
      if (typeof boardGeneric.layout === 'function') boardGeneric.layout(ctx);
      return;
    }
    const v = ctx.view;
    const format = /** @type {'landscape' | 'portrait'} */ (ctx.format);
    const L = LAYOUT[format];
    const titleRange = slotRange(ctx, 'title', { min: L.title.min, max: L.title.max, role: 'title' });
    const labelRange = slotRange(ctx, 'label', { min: L.label.min, max: L.label.max, role: 'label' });
    v.titleRange = titleRange;
    v.labelRange = labelRange;
    // the title's and the label's columns do not depend on any height: fit them in the boxes of a one-pixel layout
    const pre = boxes(format, 1, v.labels.length ? 1 : 0);
    let titleH = 0;
    if (v.title && v.titleGroup) {
      Object.assign(v.titleGroup.style, {
        position: 'absolute', left: `${pre.title.x0}px`, top: `${pre.title.y0}px`,
        width: `${pre.title.x1 - pre.title.x0}px`,
      });
      const fit = fitLines(v.title, {
        max: titleRange.max, min: titleRange.min, lines: L.title.lines, lh: v.titleLh, prefer: L.title.prefer,
      }, v.titleWords);
      v.titleSize = fit.size;
      v.titleOverflow = fit.overflow;
      titleH = v.title.offsetHeight;
    }
    // every label at one size (the smallest of their own fits), so the labels of a scene match
    const width = pre.label.x1 - pre.label.x0;
    let size = labelRange.max;
    for (const it of v.labels) {
      Object.assign(it.group.style, { position: 'absolute', left: `${pre.label.x0}px`, width: `${width}px` });
      const fit = fitLines(it.content, {
        max: labelRange.max, min: labelRange.min, lines: L.label.lines, lh: it.lh,
      }, it.words, it.tex);
      size = Math.min(size, fit.size);
    }
    let labelH = 0;
    for (const it of v.labels) {
      it.content.style.fontSize = `${size}px`;
      labelH = Math.max(labelH, it.group.offsetHeight);
    }
    v.labelSize = size;
    const laid = boxes(format, v.title ? Math.max(1, titleH) : 0, v.labels.length ? Math.max(1, labelH) : 0);
    for (const it of v.labels) {
      it.group.style.top = `${laid.label.y0}px`;
      // overflow: what does not fit the slot's lines at its minimum, and TeX wider than its line (preflight shrinks,
      // then blocks with "Shorten text"; §7.12)
      const height = it.content.offsetHeight;
      const lines = Math.round(height / (size * it.lh));
      const wide = Math.max(0, ...it.words.map((/** @type {HTMLElement} */ w, /** @type {number} */ i) => (it.tex[i] ? w.offsetWidth - width : 0)));
      const over = lines > L.label.lines ? Math.max(1, height - L.label.lines * size * it.lh) : 0;
      it.overflow = Math.max(over, wide > 2 ? wide : 0);
    }
    v.titleBox = laid.title;
    v.labelBox = laid.label;
    const r = frameRect(v.media ? v.media.ref : null, laid.media);
    Object.assign(v.frame.style, { left: `${r.x}px`, top: `${r.y}px`, width: `${r.w}px`, height: `${r.h}px` });
    v.mediaBox = { x0: r.x, y0: r.y, x1: r.x + r.w, y1: r.y + r.h };
  },

  /**
   * Paint scene time t (pure in t: everything is read from `(ctx, t, state)`).
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
    // the title
    const te = v.title ? enterAt(ctx, t, TITLE_DELAY, TEXT_ENTER) : 0;
    v.titleAlpha = te;
    if (v.title) paintTextEntrance(ctx, v.title, v.titleMask, te);
    // the animation: a fade, never a scale (a clip under a scale was measured order-dependent, deviation 28)
    const me = enterAt(ctx, t, v.mediaStart, MEDIA_ENTER);
    v.mediaAlpha = me;
    p.style(v.frame, 'opacity', fx(me));
    p.style(v.frame, 'visibility', me > 0.001 ? 'visible' : 'hidden');
    p.style(v.frame, 'transform', 'none');
    // painted every frame, also while hidden, so the element never keeps a frame from an earlier seek
    if (v.mediaView) paintMedia(ctx, v.mediaView, t);
    // the step labels: the entrance on the inner element, the exit on its wrapper (the group is the stage's)
    for (const it of v.labels) {
      const e = enterAt(ctx, t, it.enter, TEXT_ENTER);
      const x = Number.isFinite(it.exit)
        ? ease('ease.exit', progress(t, it.exit, seconds(ctx, TEXT_ENTER * EXIT_FACTOR)))
        : 0;
      it.alpha = e * (1 - x);
      paintTextEntrance(ctx, it.inner, it.mask, e);
      // always written (never removed), so the wrapper's style lists its properties in one order whatever was painted
      // before (the DOM of a frame never depends on the order of seeks)
      p.style(it.exitEl, 'opacity', fx(1 - x));
      p.style(it.exitEl, 'visibility', it.alpha > 0.001 ? 'visible' : 'hidden');
    }
  },

  /**
   * Where cue.look_here points (plan §1.4, §1.5, §1.7): `panel` the animation's frame (a shape, so its outline runs
   * round the frame and never across it, B2c review CG-1), `<item>` (or `<item>:term:<n>`: its item) the step label
   * while it is on screen. Its box as painted now (read after `update`), on whole stage px; null for anything else, or
   * a label not shown. Always the outline form: the scene shows a clip, so cues.js never pushes (deviation 28).
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
    let radius = RADIUS.media;
    let kind = /** @type {'text' | 'shape'} */ ('shape');
    if (parsed.form === 'panel') {
      el = v.mediaAlpha > 0.01 ? v.frame : null;
      holder = v.mediaGroup;
    } else if (parsed.form === 'item' || parsed.form === 'item:term') {
      const it = v.byId.get(String(parsed.item));
      el = it && it.alpha > 0.01 ? it.inner : null;
      holder = it ? it.group : null;
      radius = SPACE[0];
      kind = 'text';
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
   * The elements holding an item's text, where cue.term finds its `[[term]]` spans: the label's rich fields in their
   * order (a definition: its term, then its text). Null for anything else.
   * @param {TemplateContext} ctx
   * @param {string} itemId
   * @returns {Element | Element[] | null}
   */
  itemEl(ctx, itemId) {
    if (isFallback(ctx)) return typeof boardGeneric.itemEl === 'function' ? boardGeneric.itemEl(ctx, itemId) : null;
    const it = ctx.view.byId.get(String(itemId));
    if (!it || !it.fields.length) return null;
    return it.fields.length === 1 ? it.fields[0] : it.fields.slice();
  },

  /**
   * The content groups a look_here dims (all but the one holding its target): the animation, the title, every step
   * label. The same elements at every t; this template never writes their opacity (it animates the parts inside).
   * @param {TemplateContext} ctx
   * @returns {Element[]}
   */
  dimGroups(ctx) {
    if (isFallback(ctx)) return typeof boardGeneric.dimGroups === 'function' ? boardGeneric.dimGroups(ctx) : [];
    const v = ctx.view;
    return [v.mediaGroup, v.titleGroup, ...v.labels.map((/** @type {any} */ it) => it.group)].filter(Boolean);
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
   * Static: the box the animation is extracted at (B2 plan §1.4): the largest box the layout ever shows it in - the
   * stage under the *smallest* title and above the *smallest* step label any fit can give (one line at each slot's
   * minimum), so the medium only ever downscales (§P condition 1). No push headroom: this template is not `push_ok`
   * (a clip is never scaled, deviation 28). Every remedy in `fixes` only lowers a slot's maximum size or holds it at
   * its minimum, and a slot's minimum never goes below its spec's (`slotRange`; this template's minimums are already
   * above R-29's floors), so no remedy can lay the animation out larger than this box.
   *
   * Only the **one slot** the layout shows (`mediaOf`'s): a clip in any other slot is not laid out here, so
   * board_generic's box for that slot is what the worker should extract, never the animation box - otherwise
   * `mediaPlan` (media.js) asks for frames of a clip the page never shows, at this template's size. (After
   * `drawsScene` a drawn scene carries one medium only, so this is a guard, not a path.)
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
    const format = /** @type {'landscape' | 'portrait'} */ (zones.format);
    const L = LAYOUT[format];
    const text = titleOf(scene);
    const steps = stepsOf(scene);
    // one line at each slot's minimum, at the scene's own line heights (the smallest: per script), a pixel under (the
    // page rounds a measured height to whole px)
    const titleH = text ? Math.floor(L.title.min * lineHeight('heading', text)) - 1 : 0;
    const labelLh = steps.length ? Math.min(...steps.map((s) => lineHeight('body', s.text))) : 0;
    const labelH = steps.length ? Math.floor(L.label.min * labelLh) - 1 : 0;
    const box = boxes(format, titleH, labelH).media;
    return { w: box.x1 - box.x0, h: box.y1 - box.y0 };
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
   * The text on screen (preflight: safe zones, overflow, contrast, type floor), each with its slot (`title`,
   * `label`), its size now, the slot's minimum and its weight. Both are drawn on the background, never over the
   * animation, so the colour they are reported on is the background's own worst case (see the module comment).
   * @param {TemplateContext} ctx
   * @returns {TextEntry[]}
   */
  texts(ctx) {
    if (isFallback(ctx)) return typeof boardGeneric.texts === 'function' ? boardGeneric.texts(ctx) : [];
    const v = ctx.view;
    const ink = ctx.onBackground.ink;
    const bg = ctx.onBackground.bg;
    /** @type {TextEntry[]} */
    const out = [];
    if (v.title) {
      out.push({
        el: v.title, role: 'title', slot: 'title', critical: true, ink, bg, alpha: v.titleAlpha,
        overflow: v.titleOverflow, size: v.titleSize, min: v.titleRange.min, weight: TITLE_WEIGHT, text: v.titleText,
      });
    }
    for (const it of v.labels) {
      out.push({
        el: it.content, role: 'label', slot: 'label', critical: true, ink, bg, alpha: it.alpha,
        overflow: it.overflow, size: v.labelSize, min: v.labelRange.min, weight: it.weight,
        text: (it.content.textContent || '').replace(/\s+/g, ' ').trim(),
      });
    }
    if (v.missing) {
      out.push({
        el: v.missing, role: 'label', critical: false, ink: ctx.roles.color('ink2'), bg: ctx.roles.color('surface'),
        alpha: v.mediaAlpha, size: MISSING_LABEL_PX[ctx.format], weight: 600, text: v.missing.textContent || '',
      });
    }
    return out;
  },
};
