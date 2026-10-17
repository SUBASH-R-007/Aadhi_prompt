// @ts-check
/**
 * `aadhi_intro` (DYNAMIC_VIDEO_DESIGN §4.3 A, R1, class `structure`; §3.3 the compositions and the timing; B2 plan §1.4
 * the template contract, §1.12 the presenter crop, §2 "Aadhi" and R-54; owner decision 2 of 2026-10-16: every Dynamic
 * video opens with Aadhi): the session introduction - Aadhi on one side, the session chip, the title and (when the
 * scene has one) the hook question on the side his gesture opens to, then the brand wipe into the lesson.
 *
 * **Aadhi is not drawn here.** The presenter is a medium (§7.7) the stage's Slot draws below this template's content
 * from `look.presenter` (`../presenter.js`: the clip's crop as the worker's pre-extracted frames, 1:1, never scaled or
 * pushed; his settle and exit are the Slot's too). This template lays its text out around the placement the look
 * names and draws the parts that are not the clip: the session chip, the title stack, the `split` layout's colour
 * panel and the 16:9 brand wipe.
 *
 * Data (§4.3, templates.py `_fit_aadhi_intro`): a scene with at most one `heading` (the title; a board with none takes
 * the scene's own `title`) and at most one `question` (the hook), at least one of the two, nothing else with text and
 * no shown side panel (`drawsScene`); the
 * session chip is the scene's `subtitle` (subject · unit · session, written by the resolver from the screenplay, so the
 * template never reads the timeline). Anything else is `board_generic`'s whole, every hook handed on (list_build's
 * convention, review CG-2), and the fit refuses it too. The timeline builder synthesises this scene for every
 * Dynamic lecture (`aadhi/compose/dynamic/presenter.py intro_scene`: the chip and the title, silent) unless the
 * screenplay brings its own presenter scene.
 *
 * Variants = §3.3's layouts. 16:9 (`look.variant`, else `look.presenter.layout`, else `card_left`):
 * - `fullframe`: the left clip fills the frame; the stack is set on the studio wall at x 860-1824, y 54-855 (the region
 *   measured over all 192 frames, presenter.py WALL), in the wall inks the look stores (`presenter.wall_ink` /
 *   `wall_ink2`: the light content inks, >= 4.5:1 on the wall's darkest colour);
 * - `card_left`: a 720x720 card at x 96-816, y 180-900; the stack at x 880-1824 on the theme background;
 * - `pip`: a 480x480 card at x 96-576, y 432-912; the hook question large at x 640-1824;
 * - `split`: the clip fills x 0-864; a colour-block panel (`block.base`) at x 864-1920 carries the stack (x 960-1824)
 *   in the block inks.
 * 9:16 (`card`): the session chip in the progress row (y 288-340) and the headline (the hook question, else the
 * title) **fully set on frame 0** in the hook band (x 192-888, y 340-760; the frame0_hook rule, §3.3 / §7.12: a reel's
 * first frame is its cover), with the title as an eyebrow above a hook question; Aadhi's 744x744 card below.
 *
 * Choreography (§3.3): at 0.0 s the chip is up; at 0.15 s the title's words rise 24 px with a 70 ms stagger (`ease.enter`,
 * 450 ms; compressed into 0.8 s for a long title, board_generic's rule); the hook question lands word by word from its
 * own reveal (its beat, R-17), else after the title; at `t_exit` (`look.presenter.t_exit`) the **brand wipe**: a block
 * in the next scene's accent (`look.refs.accent_next`, R-54; else this scene's `block.base`) sweeps across from his
 * side (`ease.hero`, 600 ms) until it covers the frame at the scene's end, so the next scene enters by a crossfade from
 * the block (>= 300 ms, the resolver's). The personality's scale applies to the template's own durations
 * (`_shared.js seconds`); the wipe's 600 ms is §3.3's and is not scaled (it is the scene's exit, timed by the
 * resolver). In 9:16 the text stays until the scene ends; the card slides down (the Slot).
 *
 * Emphasis is cues.js's: `itemEl` hands it the title and the question (`.rt-k` spans kept by `_shared.js richWords`),
 * so the hook's keyword takes `cue.term` (§3.3); nothing here writes outline, text-decoration, filter, backdrop-filter
 * or will-change, and no structure role colours the text. Determinism: `update` is a pure function of t; every property
 * it paints is painted on every frame with a value; translates are whole pixels and 2D.
 */

import { h } from '../../shared/dom.js';
import { mathSpans, plainText } from '../../player/richtext.js';
import { TYPE_FLOORS, fixesOf, fx } from '../core.js';
import { itemTimings } from '../state.js';
import {
  INTRO_LAYOUTS, REEL_LAYOUT, WALL_TITLE_BOX, WIPE_SECONDS, presenterSpec, wallInks, wipeColour, wipeProgress,
} from '../presenter.js';
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

export const ID = 'aadhi_intro';

/** §3.3's layouts per format (templates.py `variants`). */
export const VARIANTS = Object.freeze({ landscape: INTRO_LAYOUTS, portrait: Object.freeze([REEL_LAYOUT]) });
/** The layout of a look that names none: card_left in 16:9 (the plainest), the card in 9:16. */
export const DEFAULT_VARIANT = Object.freeze({ landscape: 'card_left', portrait: REEL_LAYOUT });

// ---------------------------------------------------------------------------------------------
// Motion (§3.3 choreography, §4.8)
// ---------------------------------------------------------------------------------------------

/** §3.3: "0.15 s: title words rise 24 px with a 70 ms stagger (ease.enter, 450 ms)". */
export const TITLE_AT = 0.15;
export const RISE = 24;
export const WORD_SECONDS = 0.45;
export const WORD_STAGGER = 0.07;
/** A long title's stagger is compressed into this window (board_generic's / hook_question's rule). */
export const WORD_STAGGER_TOTAL = 0.8;
/** A hook question no beat reveals lands when Aadhi would start speaking (§3.3 audio offset), after the title. */
export const HOOK_AT = 0.8;

// ---------------------------------------------------------------------------------------------
// Layout (stage px; §3.3; presenter.py mirrors the boxes, templates.py the slots)
// ---------------------------------------------------------------------------------------------

/** The display face's tracking (hook_question's, §4.6 type scale). */
export const TRACKING_EM = -0.015;
/** Body-face line height of the labels (the type scale's body Latin value). */
export const LABEL_LINE_HEIGHT = 1.35;
/** Display-face line height (Latin; Indic text takes hook_question.js lineHeight's 1.25). */
export const DISPLAY_LINE_HEIGHT = 1.05;
/** Size steps when fitting (board_generic's and textfit.py's 4 px). */
export const SIZE_STEP = 4;

/**
 * The 16:9 session chip: a pill at R-29's 16:9 label floor (§3.3 asks 36 px; R-29's 40 px floor wins), and the 9:16
 * chip: a one-line label at the 9:16 label floor in the progress row above the hook band.
 */
export const CHIP = Object.freeze({
  landscape: Object.freeze({ size: 40, weight: 600, pad: Object.freeze([8, 24]) }),
  portrait: Object.freeze({ size: 44, weight: 600, box: Object.freeze({ x0: 192, y0: 288, x1: 888, y1: 340 }) }),
});

/**
 * Per 16:9 layout: `box` (the title stack's box, the stack centred in it vertically and set from its left edge),
 * `title` / `question` (size range, line limit, preferred lines) and `gap` between the rows. §3.3: title 96-120 px in
 * <= 2 lines and the hook 60-64 px; the title here keeps 120 px at the top, may go down to 72 px and take up to 4
 * lines (a session title is the teacher's metadata, of any length, set whole: one that does not fit at 72 px is
 * reported as overflow and preflight asks for a shorter one, never cut; preferring 2 lines keeps §3.3's look for the
 * usual one). `pip` sets the hook large (88-112 px, §3.3 "the hook question large")
 * and the title smaller above it. Every size is a B2 proposal pending the owner's review of the reference frames.
 */
export const LAYOUT = Object.freeze({
  fullframe: Object.freeze({
    box: WALL_TITLE_BOX,
    title: Object.freeze({ min: 72, max: 120, lines: 4, prefer: 2 }),
    question: Object.freeze({ min: 60, max: 64, lines: 3 }),
    gap: SPACE[3], // 32
  }),
  card_left: Object.freeze({
    box: Object.freeze({ x0: 880, y0: 180, x1: 1824, y1: 900 }),
    title: Object.freeze({ min: 72, max: 120, lines: 4, prefer: 2 }),
    question: Object.freeze({ min: 60, max: 64, lines: 3 }),
    gap: SPACE[3],
  }),
  pip: Object.freeze({
    box: Object.freeze({ x0: 640, y0: 54, x1: 1824, y1: 912 }),
    title: Object.freeze({ min: 64, max: 80, lines: 2, prefer: 1 }),
    question: Object.freeze({ min: 88, max: 112, lines: 3 }),
    gap: SPACE[3],
  }),
  split: Object.freeze({
    box: Object.freeze({ x0: 960, y0: 54, x1: 1824, y1: 912 }),
    panel: Object.freeze({ x0: 864, y0: 0, x1: 1920, y1: 1080 }),
    title: Object.freeze({ min: 72, max: 120, lines: 4, prefer: 2 }),
    question: Object.freeze({ min: 60, max: 64, lines: 3 }),
    gap: SPACE[3],
  }),
});

/**
 * 9:16: the hook band (the catalogue's `hook` region, x 192-888, y 340-760) holds the headline at R-29's `hook` range
 * (88-104 px, <= 4 lines, measured against the band's height, hook_question's `fitInBox`) and, above a hook question,
 * the title as an eyebrow (a label at the 9:16 floor, <= 2 lines).
 */
export const PORTRAIT = Object.freeze({
  headline: Object.freeze({ min: 88, max: 104, lines: 4 }),
  eyebrow: Object.freeze({ size: 44, weight: 600, lines: 2 }),
  gap: SPACE[2], // 24
});

/**
 * The text slots (templates.py `SlotSpec`, what textfit.py fits; plan §1.6, §1.11): slot -> format -> variant -> spec.
 * 16:9 `chip` / `title` / `question` per layout; 9:16 `chip` / `headline` / `eyebrow` (`default`). The chip is
 * decoration (not critical: board_generic's kicker rule, a one-line label cut with an ellipsis). Pure.
 */
export function slotRows() {
  /** @type {Record<string, Record<string, Record<string, any>>>} */
  const out = { chip: { landscape: {}, portrait: {} }, title: { landscape: {} }, question: { landscape: {} }, headline: { portrait: {} }, eyebrow: { portrait: {} } };
  /** @param {{min: number, max: number, lines: number, prefer?: number}} s @param {number} w */
  const display = (s, w) => ({
    w, max_lines: s.lines, size: [s.min, s.max], role: 'title', face: 'display', weight: 700,
    line_height: DISPLAY_LINE_HEIGHT, tracking_em: TRACKING_EM, prefer_lines: s.prefer || null, critical: true,
  });
  /** @param {number} size @param {number} weight @param {number} lines @param {number} w @param {boolean} critical */
  const label = (size, weight, lines, w, critical) => ({
    w, max_lines: lines, size: [size, size], role: 'label', face: 'body', weight, line_height: LABEL_LINE_HEIGHT,
    tracking_em: 0, prefer_lines: null, critical,
  });
  for (const v of INTRO_LAYOUTS) {
    const L = /** @type {any} */ (LAYOUT)[v];
    const w = L.box.x1 - L.box.x0;
    out.chip.landscape[v] = label(CHIP.landscape.size, CHIP.landscape.weight, 1, w - 2 * CHIP.landscape.pad[1], false);
    out.title.landscape[v] = display(L.title, w);
    out.question.landscape[v] = display(L.question, w);
  }
  const band = region({ format: 'portrait' }, 'hook');
  const bw = band.x1 - band.x0;
  const cb = CHIP.portrait.box;
  out.chip.portrait.default = label(CHIP.portrait.size, CHIP.portrait.weight, 1, cb.x1 - cb.x0, false);
  out.headline.portrait.default = display(PORTRAIT.headline, bw);
  out.eyebrow.portrait.default = label(PORTRAIT.eyebrow.size, PORTRAIT.eyebrow.weight, PORTRAIT.eyebrow.lines, bw, true);
  return out;
}

// ---------------------------------------------------------------------------------------------
// What the scene shows (pure)
// ---------------------------------------------------------------------------------------------

/**
 * The id of the title row drawn from the scene's own `title` (a board with no heading; `introItems`). It is no board
 * item's: no event targets it.
 */
export const SCENE_TITLE_ITEM = 'scene-title';

/**
 * The scene's title (its first `heading` with text, else - a board with no heading - the scene's own `title` as a
 * heading row, id SCENE_TITLE_ITEM), its hook (its first `question` with text) and every other item (`others`: none is
 * drawn here, so a scene with any is board_generic's) - any item of another kind, with text or not (a figure has none),
 * and a second heading or question with text; a heading or question without text draws nothing. The scene's title
 * stands in because a screenplay's own presenter scene often carries the session title there with an empty board, and
 * refusing it opened the video with board_generic and no Aadhi (owner decision 2; step-5 review R5-2; templates.py
 * `_fit_aadhi_intro` takes the same scenes).
 * @param {TimedScene} scene
 * @returns {{title: BoardItem | null, question: BoardItem | null, others: BoardItem[]}}
 */
export function introItems(scene) {
  /** @type {BoardItem | null} */
  let title = null;
  /** @type {BoardItem | null} */
  let question = null;
  /** @type {BoardItem[]} */
  const others = [];
  for (const item of (scene && scene.board) || []) {
    const text = typeof item.text === 'string' ? item.text.trim() : '';
    if (item.kind !== 'heading' && item.kind !== 'question') others.push(item);
    else if (!text) continue;
    else if (item.kind === 'heading' && !title) title = item;
    else if (item.kind === 'question' && !question) question = item;
    else others.push(item);
  }
  const own = scene && typeof scene.title === 'string' ? scene.title.trim() : '';
  if (!title && own) title = /** @type {BoardItem} */ (/** @type {unknown} */ ({ id: SCENE_TITLE_ITEM, kind: 'heading', text: own }));
  return { title, question, others };
}

/**
 * The scene's side panel when the layout shows it (null without one): this template draws none.
 * @param {TimedScene} scene
 */
function shownPanel(scene) {
  const sp = scene && scene.side_panel;
  return sp && scene.layout?.show_side_panel !== false ? sp : null;
}

/**
 * Whether this template draws the scene whole (else board_generic does; `_fit_aadhi_intro` refuses the same): at most
 * one heading and one question with text, at least one of them, nothing else with text, no shown side panel.
 * @param {TimedScene} scene
 */
export function drawsScene(scene) {
  const { title, question, others } = introItems(scene);
  return !!(title || question) && others.length === 0 && !shownPanel(scene);
}

/**
 * The layout drawn: `look.variant` when §3.3 names it for the format, else the presenter's stored layout, else the
 * format's default.
 * @param {any} look @param {'landscape' | 'portrait'} format
 */
export function variantOf(look, format) {
  const allowed = /** @type {readonly string[]} */ (VARIANTS[format]);
  const v = look && look.variant;
  if (allowed.includes(String(v))) return String(v);
  const stored = look && look.presenter && look.presenter.layout;
  if (allowed.includes(String(stored))) return String(stored);
  return DEFAULT_VARIANT[format];
}

/**
 * Whether the 9:16 headline is set whole on frame 0 (frame0_hook, §3.3): the video's first scene in 9:16.
 * @param {'landscape' | 'portrait'} format @param {number} index
 */
export function setsAtFrame0(format, index) {
  return format === 'portrait' && index === 0;
}

/**
 * The session chip's text (the scene's subtitle), '' without one.
 * @param {TimedScene} scene
 */
export function chipText(scene) {
  return typeof scene.subtitle === 'string' ? scene.subtitle.trim() : '';
}

/**
 * Every TeX string the template renders (the static `tex` hook): the `$...$` spans of the chip, the title and the
 * question, inline (`richWords` mounts each with display off).
 * @param {TimedScene} scene
 * @returns {{tex: string, display: boolean}[]}
 */
export function texOf(scene) {
  const { title, question } = introItems(scene);
  /** @type {{tex: string, display: boolean}[]} */
  const out = [];
  for (const text of [chipText(scene), title ? String(title.text || '') : '', question ? String(question.text || '') : '']) {
    for (const tex of mathSpans(text)) if (tex.trim()) out.push({ tex, display: false });
  }
  return out;
}

/**
 * When the exit starts: the presenter's `t_exit`, else the scene's last WIPE_SECONDS.
 * @param {TimedScene} scene
 */
export function exitAt(scene) {
  const spec = presenterSpec(scene);
  const end = Number(scene.duration) || 0;
  return spec ? Math.min(spec.t_exit, end) : Math.max(0, end - WIPE_SECONDS);
}

// ---------------------------------------------------------------------------------------------
// The template
// ---------------------------------------------------------------------------------------------

/** @param {TemplateContext} ctx */
const isFallback = (ctx) => !!(ctx.view && ctx.view.aiFallback);

/**
 * A text row of the stack: an element of `cls` holding `text` as word spans, in a dim group (its styles are the
 * caller's, written as literal objects the source scan reads).
 * @param {TemplateContext} ctx @param {string} cls @param {string} text @param {string} [itemId]
 */
function textRow(ctx, cls, text, itemId) {
  const el = h('div', { class: cls, dir: 'auto' });
  if (itemId) el.dataset.item = itemId;
  const { words, tex } = richWords(ctx, el, text);
  const g = group(`${cls}-group`, el);
  return { el, g, words, tex };
}

/** @type {TemplateModule} */
export default {
  id: ID,

  /**
   * The DOM: the chip, the title and the question (each in a dim group), the split layout's panel and the 16:9 wipe.
   * No measuring here.
   * @param {TemplateContext} ctx
   */
  build(ctx) {
    const { scene, format } = ctx;
    if (!drawsScene(scene)) {
      const inner = boardGeneric.build(ctx);
      inner.aiFallback = true;
      return inner;
    }
    const variant = variantOf(ctx.look, format);
    const { title, question } = introItems(scene);
    const fixes = fixesOf(ctx.look);
    const roles = ctx.roles;
    const portrait = format === 'portrait';
    // the inks of the stack: the studio wall's (fullframe), the block's (split's panel), else the background's
    const wall = !portrait && variant === 'fullframe' ? wallInks(ctx.look) : null;
    const onPanel = !portrait && variant === 'split';
    const ink = wall ? wall.ink : onPanel ? roles.color('block.ink') : ctx.onBackground.ink;
    const ink2 = wall ? wall.ink2 : onPanel ? roles.color('block.ink2') : ctx.onBackground.ink2;
    const bg = wall ? wall.bg : onPanel ? roles.color('block.base') : ctx.onBackground.bg;
    const themed = !wall && !onPanel;
    const scrim = themed && ['title', 'question', 'headline', 'eyebrow'].some((s) => fixes.slots[s] && fixes.slots[s].scrim)
      ? roles.color('scrim') : null;
    const root = h('div', {
      class: ['dyn-ai', `dyn-ai--${format}`, `dyn-ai--${variant}`],
      style: { position: 'absolute', left: '0px', top: '0px', right: '0px', bottom: '0px' },
    });

    /** @type {HTMLElement | null} */
    let panel = null;
    if (onPanel) {
      panel = h('div', { class: 'dyn-ai-panel', style: { background: roles.color('block.base') } });
      place(panel, LAYOUT.split.panel);
      root.appendChild(panel);
    }

    // the chip: a pill (16:9) or a label in the progress row (9:16); decoration, never critical
    const chipValue = chipText(scene);
    /** @type {any} */
    let chip = null;
    if (chipValue) {
      const C = CHIP[format];
      chip = textRow(ctx, 'dyn-ai-chip', chipValue);
      if (portrait) {
        Object.assign(chip.el.style, {
          color: ctx.onBackground.ink2, fontFamily: 'var(--dyn-font-body)', fontWeight: String(C.weight),
          fontSize: `${C.size}px`, lineHeight: '1.15', whiteSpace: 'nowrap', overflow: 'hidden',
          textOverflow: 'ellipsis', maxWidth: '100%',
        });
      } else {
        Object.assign(chip.el.style, {
          display: 'inline-block', color: roles.color('ink'), background: roles.color('surface'),
          fontFamily: 'var(--dyn-font-body)', fontWeight: String(C.weight), fontSize: `${C.size}px`,
          lineHeight: String(LABEL_LINE_HEIGHT), padding: `${CHIP.landscape.pad[0]}px ${CHIP.landscape.pad[1]}px`,
          borderRadius: `${RADIUS.chip}px`, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
          maxWidth: '100%', boxSizing: 'border-box',
        });
      }
      // the group may not grow past the stack (a long chip is cut with its ellipsis, never pushed out of the box)
      Object.assign(chip.g.style, { maxWidth: '100%', minWidth: '0px' });
      chip.ink = portrait ? ctx.onBackground.ink2 : roles.color('ink');
      chip.bg = portrait ? ctx.onBackground.bg : roles.color('surface');
    }

    // the title and the question: 16:9 a stack; 9:16 the headline (the hook, else the title) with the title above
    const headlineItem = portrait ? (question || title) : null;
    const eyebrowItem = portrait && question ? title : null;
    /** @param {BoardItem} item @param {'display' | 'body'} face @param {number} weight @param {string} colour */
    const row = (item, face, weight, colour) => {
      const r = textRow(ctx, face === 'display' ? 'dyn-ai-text' : 'dyn-ai-eyebrow', String(item.text || ''), item.id);
      Object.assign(r.el.style, {
        margin: '0px', color: colour, fontFamily: `var(--dyn-font-${face})`, fontWeight: String(weight),
        lineHeight: String(lineHeight(face, item.text || '')),
        letterSpacing: face === 'display' ? `${TRACKING_EM}em` : '0em', overflowWrap: 'anywhere', maxWidth: '100%',
      });
      if (face === 'body') r.el.style.fontSize = `${PORTRAIT.eyebrow.size}px`;
      return r;
    };
    /** @type {any} */
    let titleRow = null;
    /** @type {any} */
    let questionRow = null;
    if (portrait) {
      if (eyebrowItem) titleRow = { ...row(eyebrowItem, 'body', PORTRAIT.eyebrow.weight, ctx.onBackground.ink2), item: eyebrowItem, slot: 'eyebrow', ink: ctx.onBackground.ink2 };
      const head = /** @type {BoardItem} */ (headlineItem);
      const r = { ...row(head, 'display', 700, ink), item: head, slot: 'headline', ink };
      if (head === question) questionRow = r;
      else titleRow = r;
    } else {
      if (title) titleRow = { ...row(title, 'display', 700, ink), item: title, slot: 'title', ink };
      if (question) questionRow = { ...row(question, 'display', 700, ink), item: question, slot: 'question', ink };
    }

    const stack = h('div', {
      class: 'dyn-ai-stack',
      style: {
        display: 'flex', flexDirection: 'column', alignItems: 'flex-start', justifyContent: 'center',
        height: '100%', gap: `${portrait ? PORTRAIT.gap : /** @type {any} */ (LAYOUT)[variant].gap}px`,
        boxSizing: 'border-box',
      },
    });
    if (!portrait && chip) stack.appendChild(chip.g);
    const ordered = portrait ? [eyebrowItem ? titleRow : null, questionRow || (eyebrowItem ? null : titleRow)]
      : [titleRow, questionRow];
    for (const r of ordered) if (r) stack.appendChild(r.g);
    const holder = h('div', { class: 'dyn-ai-holder' }, stack);
    if (scrim) {
      Object.assign(holder.style, {
        padding: `${SPACE[1]}px ${SPACE[2]}px`, marginLeft: `-${SPACE[2]}px`, borderRadius: `${RADIUS.card}px`,
        background: scrim, boxSizing: 'border-box',
      });
    }
    root.appendChild(holder);
    if (portrait && chip) {
      const chipHolder = h('div', { class: 'dyn-ai-chiprow' }, chip.g);
      place(chipHolder, CHIP.portrait.box);
      root.appendChild(chipHolder);
    }

    // the brand wipe (16:9): a full-frame block that slides in from his side at t_exit, above everything of the scene
    /** @type {HTMLElement | null} */
    let wipe = null;
    if (!portrait) {
      wipe = h('div', {
        class: 'dyn-ai-wipe',
        style: {
          position: 'absolute', left: '0px', top: '0px', width: `${ctx.zones.width}px`, height: `${ctx.zones.height}px`,
          background: wipeColour(ctx.look, roles.color('block.base')), visibility: 'hidden',
        },
      });
      root.appendChild(wipe);
    }
    ctx.root.appendChild(root);

    // when each text lands: the title at TITLE_AT; the question at its own reveal (its beat, R-17), else after the title
    const timings = new Map(itemTimings(scene).map((x) => [x.id, x]));
    const qt = question ? timings.get(question.id) : undefined;
    const questionAt = qt && !qt.initial ? Math.max(0, qt.enter) : HOOK_AT;
    return {
      root, holder, stack, panel, wipe, variant, chip, titleRow, questionRow, scrim, ink, ink2, bg, wall: !!wall,
      onPanel, cover: setsAtFrame0(format, ctx.index), questionAt, tExit: exitAt(scene), wipeP: 0,
    };
  },

  /**
   * Measure and fit (idempotent): place the stack on its box, then fit the title and the question in it (16:9), or
   * the eyebrow and the headline in the hook band (9:16).
   * @param {TemplateContext} ctx
   */
  layout(ctx) {
    if (isFallback(ctx)) {
      if (typeof boardGeneric.layout === 'function') boardGeneric.layout(ctx);
      return;
    }
    const v = ctx.view;
    if (ctx.format === 'portrait') {
      const band = region(ctx, 'hook');
      place(v.holder, band);
      let taken = 0;
      if (v.titleRow && v.titleRow.slot === 'eyebrow') {
        const e = v.titleRow;
        const lh = lineHeight('body', e.item.text || '');
        e.size = PORTRAIT.eyebrow.size;
        e.min = PORTRAIT.eyebrow.size;
        const lines = Math.round(e.el.offsetHeight / (e.size * lh));
        e.overflow = lines > PORTRAIT.eyebrow.lines ? Math.max(1, Math.round(e.el.offsetHeight - PORTRAIT.eyebrow.lines * e.size * lh)) : 0;
        taken = e.el.offsetHeight + PORTRAIT.gap;
      }
      const head = v.questionRow || (v.titleRow && v.titleRow.slot === 'headline' ? v.titleRow : null);
      if (head) {
        const pad = v.scrim ? 2 * SPACE[1] : 0;
        const height = Math.max(0, band.y1 - band.y0 - pad - taken);
        const range = slotRange(ctx, 'headline', { min: PORTRAIT.headline.min, max: PORTRAIT.headline.max, role: 'title' });
        const fit = fitInBox(head.el, { max: range.max, min: range.min, lines: PORTRAIT.headline.lines, lh: lineHeight('display', head.item.text || ''), step: SIZE_STEP }, head.words, head.tex, height || Infinity);
        head.size = fit.size;
        head.min = range.min;
        head.overflow = fit.overflow;
      }
      if (v.chip) v.chip.overflow = 0; // decoration: cut with an ellipsis, never reported (board_generic's kicker)
      return;
    }
    const L = /** @type {any} */ (LAYOUT)[v.variant];
    place(v.holder, L.box);
    for (const [r, slot] of /** @type {const} */ ([[v.titleRow, 'title'], [v.questionRow, 'question']])) {
      if (!r) continue;
      const S = L[slot];
      const range = slotRange(ctx, slot, { min: S.min, max: S.max, role: 'title' });
      const fit = fitLines(r.el, { max: range.max, min: range.min, lines: S.lines, prefer: S.prefer, lh: lineHeight('display', r.item.text || ''), step: SIZE_STEP }, r.words, r.tex);
      r.size = fit.size;
      r.min = range.min;
      r.overflow = fit.overflow;
    }
    // the whole stack must fit its box: what runs past it is reported on the last row (never clipped silently)
    const boxH = L.box.y1 - L.box.y0 - (v.scrim ? 2 * SPACE[1] : 0);
    const used = v.stack.scrollHeight;
    const last = v.questionRow || v.titleRow;
    if (last && used > boxH + 1) last.overflow = Math.max(last.overflow || 0, Math.round(used - boxH));
    if (v.chip) v.chip.overflow = 0;
  },

  /**
   * Paint scene time t: the chip, the title's and the question's words, and the 16:9 wipe. A pure function of t.
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
    const p = ctx.paint;
    if (v.chip) {
      p.style(v.chip.el, 'opacity', '1');
      v.chip.alpha = 1;
    }
    /** @param {any} r @param {number} start */
    const words = (r, start) => {
      if (!r) return;
      const n = r.words.length;
      const each = n > 1 ? Math.min(WORD_STAGGER, WORD_STAGGER_TOTAL / (n - 1)) : 0;
      let alpha = n ? 0 : 1;
      r.words.forEach((/** @type {HTMLElement} */ w, i) => {
        const e = v.cover ? 1 : enterAt(ctx, t, start + seconds(ctx, each * i), WORD_SECONDS);
        if (i === n - 1) alpha = e;
        paintRise(ctx, w, e, RISE);
      });
      r.alpha = alpha;
    };
    words(v.titleRow, TITLE_AT);
    words(v.questionRow, v.questionAt);
    if (v.wipe) {
      const k = wipeProgress(t, v.tExit);
      v.wipeP = k;
      const width = ctx.zones.width;
      p.style(v.wipe, 'visibility', k > 0 ? 'visible' : 'hidden');
      p.style(v.wipe, 'transform', k > 0 && k < 1 ? `translate(${Math.round((k - 1) * width)}px, 0px)` : 'none');
    }
  },

  /**
   * Where a look_here points: the title's or the question's text box, on whole stage px; null otherwise.
   * @param {TemplateContext} ctx
   * @param {string} target
   * @returns {FocusRect | null}
   */
  focusRect(ctx, target) {
    if (isFallback(ctx)) return typeof boardGeneric.focusRect === 'function' ? boardGeneric.focusRect(ctx, target) : null;
    const v = ctx.view;
    const id = String(target || '').split(':')[0];
    const hit = [v.titleRow, v.questionRow].find((r) => r && r.item.id === id);
    const r = hit ? ctx.rectOf(hit.el) : null;
    if (!r || !hit) return null;
    const x0 = Math.round(r.x);
    const y0 = Math.round(r.y);
    return { rect: { x: x0, y: y0, w: Math.round(r.x + r.w) - x0, h: Math.round(r.y + r.h) - y0 }, shape: 'rect', radius: 0, el: hit.g, kind: 'text' };
  },

  /**
   * The element holding an item's text (cue.term finds its `.rt-k` spans there): the title or the question.
   * @param {TemplateContext} ctx
   * @param {string} itemId
   * @returns {Element | Element[] | null}
   */
  itemEl(ctx, itemId) {
    if (isFallback(ctx)) return typeof boardGeneric.itemEl === 'function' ? boardGeneric.itemEl(ctx, itemId) : null;
    const v = ctx.view;
    const hit = [v.titleRow, v.questionRow].find((r) => r && r.item.id === itemId);
    return hit ? hit.el : null;
  },

  /**
   * The content groups a look_here dims: the chip, the title and the question - the same elements at every t; this
   * template animates only their children.
   * @param {TemplateContext} ctx
   * @returns {Element[]}
   */
  dimGroups(ctx) {
    if (isFallback(ctx)) return typeof boardGeneric.dimGroups === 'function' ? boardGeneric.dimGroups(ctx) : [];
    const v = ctx.view;
    return [v.chip ? v.chip.g : null, v.titleRow ? v.titleRow.g : null, v.questionRow ? v.questionRow.g : null].filter(Boolean);
  },

  /** The morph anchor: none (B2f). @param {TemplateContext} _ctx @returns {null} */
  anchor(_ctx) {
    return null;
  },

  /**
   * Static: no medium of the template's own (Aadhi is the stage's presenter, sized from `look.presenter`); a scene this
   * template does not draw keeps board_generic's box.
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
   * The text on screen (preflight, §7.12): the chip (decoration), the title and the question (critical), in the inks
   * and on the colour they are drawn on (the wall, the panel, the background or its scrim). Under the wipe the text is
   * reported fading out with the block's progress (it is being covered).
   * @param {TemplateContext} ctx
   * @returns {TextEntry[]}
   */
  texts(ctx) {
    if (isFallback(ctx)) return typeof boardGeneric.texts === 'function' ? boardGeneric.texts(ctx) : [];
    const v = ctx.view;
    const floors = TYPE_FLOORS[ctx.format];
    const left = 1 - (v.wipeP || 0);
    /** @param {string} ink */
    const bgOf = (ink) => (v.scrim ? scrimmedBg(v.scrim, ctx.onBackground, ink) : v.bg);
    /** @type {TextEntry[]} */
    const out = [];
    if (v.chip) {
      const C = CHIP[ctx.format];
      out.push({
        el: v.chip.el, role: 'chip', slot: 'chip', critical: false, ink: v.chip.ink, bg: v.chip.bg,
        alpha: Number(fx(left)), overflow: 0, size: C.size, min: Math.max(floors.label_min, C.size), weight: C.weight,
        text: plainText(chipText(ctx.scene)),
      });
    }
    for (const r of [v.titleRow, v.questionRow]) {
      if (!r) continue;
      const eyebrow = r.slot === 'eyebrow';
      out.push({
        el: r.el, role: eyebrow ? 'label' : 'title', slot: r.slot, critical: true, ink: r.ink, bg: bgOf(r.ink),
        alpha: Number(fx((r.alpha === undefined ? 0 : r.alpha) * left)), overflow: r.overflow || 0, size: r.size,
        min: eyebrow ? Math.max(floors.label_min, r.min || 0) : r.min, weight: eyebrow ? PORTRAIT.eyebrow.weight : 700,
        text: plainText(String(r.item.text || '')),
      });
    }
    return out;
  },
};
