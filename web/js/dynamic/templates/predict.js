// @ts-check
/**
 * `predict` (DYNAMIC_VIDEO_DESIGN §4.3 C, R1, class `prompt`, R-62, R-07; B2 plan §1.4 the template contract, §1.5 the
 * cues and item states, §1.6/§1.11 the slots, §P): the prediction that comes **before** the explanation — one
 * question, "Pause and predict", and a think ring. **No answer in this scene** (R-62: the answering beat lives in the
 * next explaining scene of the same chapter, and `predict.order` / `predict.unanswered` lint that).
 *
 * Data (§4.3, templates.py `_fit_predict`): a BoardScene with **one** `question` item (`blank=True`; the page draws a
 * non-blank one the same way, it is the director that only ever sends a blank one) plus at most the scene's `heading`,
 * which is set small above the card as a label (`_shape`'s one allowed extra, so no item a timeline names is dropped).
 * Nothing else is drawn here: a scene with another item, with a shown side panel, with a scene medium or a poster is
 * handed to `board_generic` **whole**, every hook handed on (`drawsScene`; list_build's and hook_question's
 * convention, B2c review CG-2), and `_fit_predict` carries the same guards so direct.py never sends such a scene.
 *
 * Layout (§4.3 "the question card, 'Pause and predict', and a think ring"), in that reading order inside one card on
 * `surface`, so the instruction and the ring sit where the learner has just finished reading:
 * - **16:9:** the card centred in `centre-60` (442-1478), from the title zone's top to the body box's bottom; the
 *   question at 84-96 px (R-29's 16:9 heading band, capped at board_generic's 96 so a prediction's question matches a
 *   board title), the instruction at the 40 px label floor, the ring 120 px across.
 * - **9:16:** the card fills the text column (x 192-780, y 340-1020: the part of the stage both critical boxes hold,
 *   R-59); the question at 56-64 px, the instruction at the 44 px label floor (R-29: 9:16 text at most 64 px carries
 *   at least weight 600, which every body slot here does), the ring 96 px across.
 *
 * The think time (design §1.4, R-07): a `countdown` event's own start and length when the scene carries one, else
 * `max(min_fmt, words / 130 x 60 + 3 s)` capped at 12 s from the question's entrance - the design's formula over the
 * words to read - clamped to what is left of the scene. The ring is a **visual ring only**: it runs linearly, with no
 * riser and no sound (R-07, R-49), its number counting whole seconds down, and the card then holds to the end of the
 * scene (nothing here ever fades text out to make room).
 *
 * The "Your prediction?" chip (§4.3 "In the explaining scene: a small 'Your prediction?' chip with the question stays
 * at the top until the answering beat, where it resolves") is **this module's** too, as a small API a host calls
 * (`buildChip` / `layoutChip` / `paintChip` / `chipTexts`), because the scene that carries it is drawn by whichever
 * template explains the outcome, not by this one. It reads `look.refs.prediction` ({text, resolve_t}, plan §1.2,
 * denormalised by the resolver) and needs no template to know about predictions. Two readings are recorded as open
 * items: `RefPrediction` carries no answer text, so at `resolve_t` the chip takes the `done` item state (the `ok`
 * check pops, the `ok` border draws, plan §1.5) and leaves - the scene itself is the answer on screen; and its one
 * line is **plain** text (`plainText`, so no TeX is converted for it and the host's static `tex()` stays right),
 * cut with an ellipsis when it is longer than the chip, which `chipTexts` reports as no overflow - board_generic's
 * kicker rule for a reference to another scene's words.
 *
 * Choreography (§4.3, §4.8): the card rises 24 px as it fades in (500 ms), the question's words follow 80 ms later
 * with the personality's text entrance, the instruction fades in after them, and the ring fades in and empties over
 * the think time. Template durations take the personality's scale (`_shared.js seconds` / `enterAt`); the think time
 * does not (it is the learner's own pause, not a template duration) and neither do the cues (cues.js).
 *
 * Emphasis is cues.js's (§4.2, plan §1.4/§1.5): `itemEl` hands it the question's text (its `[[term]]` `.rt-k` spans,
 * kept by `_shared.js richWords`, so `<item>` and `<item>:term:<n>` both resolve) or the label's; `focusRect` gives
 * the card as a **shape** (its outline is drawn just outside it, never across the question) or the label's text box;
 * `dimGroups` the label and the card. Nothing here writes outline, text-decoration, filter, backdrop-filter or
 * will-change, and no structure role colours content (§4.2): only the ring's arc, a structure mark, takes
 * `struct.accent`.
 *
 * Determinism (§7.4, plan §P): `update` is a pure function of t; every property it paints is painted on every frame
 * with a value (never ''), so a frame never depends on paint order; translates are whole pixels and 2D (`paintRise`);
 * every size and position is measured once in `layout` from the laid-out page and never changes with t.
 */

import { h, svg } from '../../shared/dom.js';
import { ease } from '../../motion/easing.js';
import { progress } from '../../motion/track.js';
import { strokeDash } from '../../motion/draw.js';
import { mathSpans, plainText } from '../../player/richtext.js';
import { TYPE_FLOORS, fixesOf, fx } from '../core.js';
import { itemTimings } from '../state.js';
import boardGeneric from './board_generic.js';
import {
  RADIUS, SPACE, enterAt, fitLines, group, itemStateParts, paintItemState, paintRise, paintTextEntrance, place, region,
  revealMask, richWords, scrimmedBg, seconds, slotRange,
} from './_shared.js';

/** @typedef {import('./types.js').TemplateContext} TemplateContext */
/** @typedef {import('./types.js').TemplateModule} TemplateModule */
/** @typedef {import('./types.js').FocusRect} FocusRect */
/** @typedef {import('./types.js').TextEntry} TextEntry */
/** @typedef {import('../../shared/types.js').TimedScene} TimedScene */
/** @typedef {import('../../shared/types.js').BoardItem} BoardItem */
/** @typedef {import('../../shared/types.js').ResolvedLook} ResolvedLook */
/** @typedef {import('../core.js').Box} Box */
/** @typedef {import('../core.js').Zones} Zones */

export const ID = 'predict';

// ---------------------------------------------------------------------------------------------
// Text this template writes itself (English until B4's i18n, as hook_question's pill)
// ---------------------------------------------------------------------------------------------

/** §4.3: the instruction under the question. */
export const PROMPT_TEXT = 'Pause and predict';
/** §4.3: the chip the explaining scene carries. */
export const CHIP_TEXT = 'Your prediction?';

// ---------------------------------------------------------------------------------------------
// Motion (§4.3 predict, §4.8)
// ---------------------------------------------------------------------------------------------

/** The card fades in and rises this far (§4.3's cards; §4.8 medium move) ... */
export const CARD_RISE = 24;
/** ... over 500 ms (§4.8 "medium move 400-500 ms"). */
export const CARD_ENTER = 0.5;
/** The question's words follow its card by 80 ms (list_build's number-then-text rule, §4.3) ... */
export const TEXT_DELAY = 0.08;
/** ... and enter over 450 ms with the personality's text entrance. */
export const TEXT_ENTER = 0.45;
/** The label above the card enters with the frame (the cards' length). */
export const LABEL_ENTER = 0.45;
/** The instruction follows the question (§4.8: one thing at a time) ... */
export const PROMPT_DELAY = 0.3;
/** ... over 400 ms, rising 16 px. */
export const PROMPT_ENTER = 0.4;
export const PROMPT_RISE = 16;
/** The ring fades in 250 ms after its card and leaves 30% faster (§4.8); it never moves (R-07). */
export const RING_DELAY = 0.25;
export const RING_IN = 0.3;
export const RING_OUT = RING_IN * 0.7;
/** A ring shorter than this is not drawn at all (nothing to think about). */
export const RING_MIN = 0.5;

/** Design §1.4's think time: the words to read at this reading rate, plus this much, capped here ... */
export const THINK_WPM = 130;
export const THINK_EXTRA = 3;
export const THINK_MAX = 12;
/** ... and never under the format's floor (R-07: >= 5 s in 16:9, >= 4 s in 9:16). */
export const THINK_MIN = Object.freeze({ landscape: 5, portrait: 4 });

/** The chip: it is already there when the explaining scene opens, so it only fades in (§4.8 small move) ... */
export const CHIP_ENTER = 0.35;
export const CHIP_RISE = 16;
/** ... takes the `done` state over 300 ms at the answering beat (plan §1.5, `pop`) ... */
export const CHIP_POP = 0.3;
/** ... holds that long enough to be read, then leaves 30% faster than it came (§4.8 exits). */
export const CHIP_HOLD = 1.2;
export const CHIP_EXIT = CHIP_ENTER * 0.7;

// ---------------------------------------------------------------------------------------------
// Layout (stage px; R-29 floors; the catalogue's regions and layout tokens)
// ---------------------------------------------------------------------------------------------

/** Line heights of the type scale (zones.py TYPE_SCALE, plan §2: Indic body >= 1.45, Indic heading >= 1.25;
 * predict.test.js pins them to the catalogue's `type.scale`). */
export const LINE_HEIGHT = Object.freeze({
  heading: Object.freeze({ latin: 1.15, indic: 1.25 }),
  body: Object.freeze({ latin: 1.35, indic: 1.45 }),
  label: Object.freeze({ latin: 1.3, indic: 1.45 }),
});

/** The think ring's stroke width (board_generic's quiz ring, dynamic.css `.dyn-ring-track`). */
export const RING_STROKE = 8;
/** Size steps when fitting (board_generic's and textfit.py's 4 px). */
export const SIZE_STEP = 4;

/**
 * Per-format layout. `label` / `question` / `prompt`: each slot's size range, line limit, preferred line count and
 * weight (R-29: labels >= 40 px in 16:9 and >= 44 px in 9:16; 16:9 headings 84-120, capped at board_generic's 96; 9:16
 * text at most 64 px carries at least weight 600). `ring`: the think ring's box, `ringNum` its number's size (a
 * label). `pad`: the card's padding; `gap`: between the card's parts; `labelGap`: between the label and the card.
 * `chip` / `chipNum`: the "Your prediction?" chip's text size and its padding (y, x); `chipMax`: the widest it may be,
 * so it never reaches the kicker a host template draws at the other end of the header row. Every size is a B2
 * proposal pending the owner's review of the reference frames.
 */
export const LAYOUT = Object.freeze({
  landscape: Object.freeze({
    label: Object.freeze({ max: 40, min: 40, lines: 1, prefer: 1, weight: 600 }),
    // the floor is 64, not 84: the question, the instruction and the 120 px think ring share a 712 px card, so a
    // fourth line does not fit it - a long Indic question (`predict.landscape.tamil`, which the real-Chromium
    // preflight blocked at an 84 px floor by exactly one line) has to come down in size to stay in three.
    // Above R-29's 36 px floor for a title and in `check`'s own range; `prefer: 2` still sets a short question big.
    question: Object.freeze({ max: 96, min: 64, lines: 3, prefer: 2, weight: 700 }),
    prompt: Object.freeze({ max: 40, min: 40, lines: 1, prefer: 1, weight: 600 }),
    ring: 120,
    ringNum: 40,
    pad: 32,
    gap: 24,
    labelGap: 24,
    chip: 40,
    chipPad: Object.freeze([10, 24]),
    chipMax: 720,
  }),
  portrait: Object.freeze({
    label: Object.freeze({ max: 44, min: 44, lines: 1, prefer: 1, weight: 600 }),
    question: Object.freeze({ max: 64, min: 56, lines: 3, prefer: 2, weight: 700 }),
    prompt: Object.freeze({ max: 44, min: 44, lines: 1, prefer: 1, weight: 600 }),
    ring: 96,
    ringNum: 44,
    pad: 24,
    gap: 24,
    labelGap: 20,
    chip: 44,
    chipPad: Object.freeze([8, 20]),
    chipMax: 420,
  }),
});

/**
 * The boxes of a format (pure; whole stage px): `label` (the line above the card) and `card` (where the card is
 * centred) — 16:9 the centre 60% from the title zone's top to the body box's bottom (y 112-900, 24 px above the
 * caption band), 9:16 the text column to y 1020, the part of the stage both critical boxes hold (R-59).
 * @param {'landscape' | 'portrait'} format
 * @param {Zones} zones
 * @returns {{label: Box, card: Box}}
 */
export function boxes(format, zones) {
  const L = LAYOUT[format];
  const top = zones.title.y0;
  const labelH = Math.round(L.label.max * LINE_HEIGHT.label.latin);
  if (format === 'portrait') {
    const col = zones.textColumn;
    const box = { x0: col.x0, y0: top, x1: col.x1, y1: col.y1 };
    return { label: { ...box, y1: top + labelH }, card: box };
  }
  const mid = region({ format }, 'centre-60');
  return {
    label: { x0: mid.x0, y0: top, x1: mid.x1, y1: top + labelH },
    card: { x0: mid.x0, y0: top, x1: mid.x1, y1: zones.body.y1 },
  };
}

/**
 * The default box the "Your prediction?" chip is drawn in: one line at the top of the scene, right-aligned in the
 * header row, at most `chipMax` wide, so it never reaches a kicker drawn from the other end of that row. A host that
 * reserves room of its own passes its own box to `buildChip`.
 * @param {'landscape' | 'portrait'} format
 * @param {Zones} zones
 * @returns {Box}
 */
export function chipBox(format, zones) {
  const L = LAYOUT[format];
  const row = zones.header;
  const h0 = Math.round(L.chip * LINE_HEIGHT.label.latin) + 2 * L.chipPad[0];
  const width = Math.min(L.chipMax, row.x1 - row.x0);
  return { x0: row.x1 - width, y0: row.y0, x1: row.x1, y1: row.y0 + h0 };
}

/**
 * The text slots of this template (aadhi/compose/dynamic/templates.py SlotSpec, what textfit.py fits in Python; plan
 * §1.6, §1.11): slot -> format -> variant -> the spec. One variant, `default` (§4.3 gives `predict` no look variant).
 * `label` sits on the background above the card; `question` and `prompt` share the card's text box; `ring` is the
 * countdown's number in the ring's box, never critical (R-07: the think time is the ring itself); `chip` is the
 * "Your prediction?" line a host draws in the explaining scene, one line and never critical (it is a reminder of
 * another scene's words, cut with an ellipsis as board_generic's kicker is). Line heights are the Latin ones (Indic
 * text takes LINE_HEIGHT's Indic values, which textfit.py's Indic measure must use). Pure.
 * @param {Zones} landscape @param {Zones} portrait
 */
export function slotRows(landscape, portrait) {
  /** @param {'landscape' | 'portrait'} fmt @param {Zones} zones */
  const table = (fmt, zones) => {
    const L = LAYOUT[fmt];
    const b = boxes(fmt, zones);
    const cardText = b.card.x1 - b.card.x0 - 2 * L.pad;
    const chip = chipBox(fmt, zones);
    /**
     * @param {{max: number, min: number, lines: number, prefer: number, weight: number}} s
     * @param {number} w @param {'title' | 'body' | 'label'} role @param {'display' | 'body'} face
     * @param {number} lh @param {{critical?: boolean, tabular?: boolean}} [opts]
     */
    const spec = (s, w, role, face, lh, opts = {}) => ({
      w, max_lines: s.lines, size: [s.min, s.max], role, face, weight: s.weight, line_height: lh,
      prefer_lines: s.prefer > 0 ? s.prefer : null, critical: opts.critical !== false, tabular_nums: !!opts.tabular,
    });
    const one = (/** @type {number} */ size, /** @type {number} */ weight) => ({
      max: size, min: size, lines: 1, prefer: 1, weight,
    });
    return {
      label: spec(L.label, b.label.x1 - b.label.x0, 'label', 'body', LINE_HEIGHT.label.latin),
      question: spec(L.question, cardText, 'title', 'display', LINE_HEIGHT.heading.latin),
      prompt: spec(L.prompt, cardText, 'label', 'body', LINE_HEIGHT.label.latin),
      ring: spec(one(L.ringNum, 700), L.ring, 'label', 'body', LINE_HEIGHT.label.latin,
        { critical: false, tabular: true }),
      chip: spec(one(L.chip, 600), chip.x1 - chip.x0 - 2 * L.chipPad[1], 'label', 'body', LINE_HEIGHT.label.latin,
        { critical: false }),
    };
  };
  const l = table('landscape', landscape);
  const p = table('portrait', portrait);
  /** @type {Record<string, any>} */
  const out = {};
  for (const slot of Object.keys(l)) {
    out[slot] = {
      landscape: { default: l[/** @type {keyof typeof l} */ (slot)] },
      portrait: { default: p[/** @type {keyof typeof p} */ (slot)] },
    };
  }
  return out;
}

// ---------------------------------------------------------------------------------------------
// What the scene shows (pure)
// ---------------------------------------------------------------------------------------------

/** Indic scripts (Devanagari to Sinhala), whose text takes the taller line heights (plan §2). */
const INDIC = new RegExp(`[${['Devanagari', 'Bengali', 'Gurmukhi', 'Gujarati', 'Oriya', 'Tamil', 'Telugu', 'Kannada',
  'Malayalam', 'Sinhala'].map((x) => `\\p{Script=${x}}`).join('')}]`, 'u');

/**
 * The line height of `text` in type role `role` (LINE_HEIGHT: Indic text takes the Indic value).
 * @param {'heading' | 'body' | 'label'} role @param {string} text
 */
export function lineHeight(role, text) {
  return INDIC.test(plainText(String(text || ''))) ? LINE_HEIGHT[role].indic : LINE_HEIGHT[role].latin;
}

/** @param {BoardItem} item */
const textOf = (item) => (typeof item.text === 'string' ? item.text.trim() : '');

/**
 * What the scene gives this template: its `question` (the first `question` item with text), its `label` (the first
 * `heading` with text) and every other item with text (`others`: this template draws none of them, so a scene with any
 * is board_generic's).
 * @param {TimedScene} scene
 * @returns {{question: BoardItem | null, label: BoardItem | null, others: BoardItem[]}}
 */
export function predictItems(scene) {
  /** @type {BoardItem | null} */
  let question = null;
  /** @type {BoardItem | null} */
  let label = null;
  /** @type {BoardItem[]} */
  const others = [];
  for (const item of scene.board || []) {
    if (!textOf(item)) continue;
    if (item.kind === 'question' && !question) question = item;
    else if (item.kind === 'heading' && !label) label = item;
    else others.push(item);
  }
  return { question, label, others };
}

/**
 * Whether this template draws the scene whole (else board_generic does, every hook handed on; `_fit_predict` must
 * score such a scene 0, B2c review CG-2): one `question` item with text, at most one `heading` with text, nothing else
 * with text, and nothing to show beside them — §4.3 gives a prediction no picture and this template plans no media
 * box, so a scene with a shown side panel, a scene medium or a poster would lose it (B2-demo review D5).
 * @param {TimedScene} scene
 */
export function drawsScene(scene) {
  if (scene.media || scene.poster) return false;
  if (scene.side_panel && scene.layout?.show_side_panel !== false) return false;
  const { question, others } = predictItems(scene);
  return !!question && others.length === 0;
}

/**
 * The number of words to read in `text` (design §1.4's `words_to_read`, over the plain text: the question itself; a
 * `predict` scene has no options).
 * @param {string} text
 */
export function wordsToRead(text) {
  const plain = plainText(String(text || '')).trim();
  return plain ? plain.split(/\s+/u).length : 0;
}

/**
 * Design §1.4's think time for `words` words in `format`: `max(min_fmt, words / 130 x 60 + 3 s)`, capped at 12 s
 * (R-07: >= 5 s in 16:9, >= 4 s in 9:16). Pure.
 * @param {number} words @param {'landscape' | 'portrait'} format
 */
export function thinkSeconds(words, format) {
  const read = (Math.max(0, words) / THINK_WPM) * 60 + THINK_EXTRA;
  return Math.min(THINK_MAX, Math.max(THINK_MIN[format], read));
}

/**
 * The first event of kind `kind` on item `id` (its target's item part), or null. The stage applies only the kinds
 * `templates.py consumes` declares; `countdown` is read here because it says WHEN the think time runs, not how
 * (§4.2 "Python decides when").
 * @param {TimedScene} scene @param {string} kind @param {string} id
 * @returns {{t: number, dur: number | null} | null}
 */
export function eventFor(scene, kind, id) {
  /** @type {{t: number, dur: number | null} | null} */
  let out = null;
  for (const e of scene.events || []) {
    if (!e || e.kind !== kind || typeof e.t !== 'number' || !Number.isFinite(e.t)) continue;
    if (typeof e.target !== 'string' || e.target.split(':')[0] !== id) continue;
    const dur = typeof e.dur === 'number' && Number.isFinite(e.dur) && e.dur > 0 ? e.dur : null;
    if (!out || e.t < out.t) out = { t: e.t, dur };
  }
  return out;
}

/**
 * The scene's timetable for this template (pure; scene-relative seconds): when the label and the question enter, and
 * the think ring's start and length — a `countdown` event's own, else design §1.4's think time from the question's
 * entrance plus RING_DELAY, clamped to what is left of the scene; null when what is left is under RING_MIN (there is
 * nothing to think about).
 * @param {TimedScene} scene
 * @param {BoardItem | null} question
 * @param {BoardItem | null} label
 * @param {'landscape' | 'portrait'} format
 * @returns {{label: number, question: number, ring: {start: number, seconds: number} | null}}
 */
export function timetable(scene, question, label, format) {
  const timings = itemTimings(scene);
  const at = (/** @type {string} */ id) => {
    const found = timings.find((x) => x.id === id);
    return found ? found.enter : 0;
  };
  const duration = Math.max(0, Number(scene.duration) || 0);
  const enter = question ? at(question.id) : 0;
  const countdown = question ? eventFor(scene, 'countdown', question.id) : null;
  const start = countdown ? countdown.t : enter + RING_DELAY;
  const want = countdown && countdown.dur !== null
    ? countdown.dur
    : thinkSeconds(wordsToRead(question ? String(question.text || '') : ''), format);
  const length = Math.max(0, Math.min(want, duration - start));
  return {
    label: label ? at(label.id) : 0,
    question: enter,
    ring: length >= RING_MIN ? { start, seconds: length } : null,
  };
}

/**
 * Every TeX string the template renders for the scene (the static `tex` hook; render.js converts them before `ready`):
 * the `$...$` spans of the label and of the question, inline (`richWords` mounts each with display off), in the order
 * they are built. The chip never converts TeX (it is plain text), so a host's own `tex()` stays right.
 * @param {TimedScene} scene
 * @returns {{tex: string, display: boolean}[]}
 */
export function texOf(scene) {
  const { question, label } = predictItems(scene);
  /** @type {{tex: string, display: boolean}[]} */
  const out = [];
  for (const item of [label, question]) {
    if (!item) continue;
    for (const tex of mathSpans(String(item.text || ''))) if (tex.trim()) out.push({ tex, display: false });
  }
  return out;
}

// ---------------------------------------------------------------------------------------------
// The "Your prediction?" chip (§4.3: carried by the EXPLAINING scene, so a host template calls this)
// ---------------------------------------------------------------------------------------------

/**
 * The open prediction of a look (`refs.prediction`, plan §1.2: {text, resolve_t}), or null when the scene carries
 * none or it is malformed. `resolveT`: the scene-relative second the chip resolves at (null: not in this scene, so
 * the chip stays to the end). Pure.
 * @param {ResolvedLook | null | undefined} look
 * @returns {{text: string, resolveT: number | null} | null}
 */
export function predictionOf(look) {
  const refs = look && typeof look === 'object' ? /** @type {any} */ (look).refs : null;
  const open = refs && typeof refs === 'object' ? refs.prediction : null;
  if (!open || typeof open !== 'object') return null;
  const text = plainText(String(open.text || '')).trim();
  if (!text) return null;
  const at = Number(open.resolve_t);
  return { text, resolveT: Number.isFinite(at) && at >= 0 ? at : null };
}

/**
 * Build the chip into `host` (the element the caller owns; it is placed absolutely inside it), or null when the scene
 * carries no open prediction. Call it once, at build time, from a template's `build` or from the stage's Slot; then
 * `layoutChip`, `paintChip` and `chipTexts`. `box`: where it is drawn (default `chipBox`, the top of the scene).
 * @param {TemplateContext} ctx
 * @param {HTMLElement} host
 * @param {Box} [box]
 * @returns {any | null}
 */
export function buildChip(ctx, host, box) {
  const open = predictionOf(ctx.look);
  if (!open) return null;
  const L = LAYOUT[ctx.format];
  const where = box || chipBox(ctx.format, ctx.zones);
  const ink = ctx.roles.color('ink');
  const label = h('span', {
    class: 'dyn-pd-chip-label', text: CHIP_TEXT,
    style: { fontWeight: '700', whiteSpace: 'nowrap', flex: 'none' },
  });
  // the question, cut with an ellipsis when it is longer than the chip (board_generic's kicker rule: a reference to
  // another scene's words, reported as no overflow)
  const text = h('span', {
    class: 'dyn-pd-chip-text', dir: 'auto', text: open.text,
    style: { fontWeight: '400', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', minWidth: '0px' },
  });
  const body = h('div', {
    class: 'dyn-pd-chip',
    style: {
      position: 'absolute', boxSizing: 'border-box', display: 'flex', alignItems: 'center', gap: `${SPACE[1]}px`,
      background: ctx.roles.color('surface'), color: ink, borderRadius: `${RADIUS.chip}px`,
      padding: `${L.chipPad[0]}px ${L.chipPad[1]}px`, fontFamily: 'var(--dyn-font-body)', fontSize: `${L.chip}px`,
      lineHeight: String(LINE_HEIGHT.label.latin), maxWidth: '100%',
    },
  }, label, text);
  const parts = itemStateParts(body);
  const el = group('dyn-pd-chip-group', body);
  el.style.position = 'absolute';
  host.appendChild(el);
  return { open, el, body, label, text, parts, box: where, ink, surface: ctx.roles.color('surface'), alpha: 0, state: 0 };
}

/**
 * Place the chip on its box (layout time; idempotent).
 * @param {TemplateContext} ctx @param {any | null} chip
 */
export function layoutChip(ctx, chip) {
  if (!chip) return;
  const b = chip.box;
  Object.assign(chip.el.style, {
    left: `${b.x0}px`, top: `${b.y0}px`, width: `${b.x1 - b.x0}px`, height: `${b.y1 - b.y0}px`,
    display: 'flex', justifyContent: 'flex-end', alignItems: 'flex-start',
  });
  Object.assign(chip.body.style, { position: 'relative', maxWidth: '100%' });
}

/**
 * Paint the chip at scene time `t` (every frame, with a value for every property): it fades in with the scene, takes
 * the `done` item state at `resolve_t` and leaves after CHIP_HOLD; without a `resolve_t` it stays to the end.
 * @param {TemplateContext} ctx @param {any | null} chip @param {number} t
 */
export function paintChip(ctx, chip, t) {
  if (!chip) return;
  const p = ctx.paint;
  const at = /** @type {number | null} */ (chip.open.resolveT);
  const inP = enterAt(ctx, t, 0, CHIP_ENTER);
  const leaveAt = at === null ? null : at + seconds(ctx, CHIP_POP + CHIP_HOLD);
  const out = leaveAt === null ? 0 : ease('ease.exit', progress(t, leaveAt, seconds(ctx, CHIP_EXIT)));
  const alpha = inP * (1 - out);
  chip.alpha = alpha;
  const dy = ctx.reducedMotion || inP >= 1 ? 0 : Math.round((1 - inP) * CHIP_RISE);
  p.style(chip.el, 'visibility', alpha > 0.001 ? 'visible' : 'hidden');
  p.style(chip.body, 'opacity', fx(alpha));
  p.style(chip.body, 'transform', dy ? `translate(0, ${dy}px)` : 'none'); // 2D: see _shared.js paintRise
  // the `done` state: `RefPrediction` carries no answer text, so resolving is the ok check and the ok border on the
  // chip itself, over the scene that is explaining the outcome (recorded as an open item)
  const k = at === null ? 0 : ease('pop', progress(t, at, seconds(ctx, CHIP_POP)));
  chip.state = k;
  paintItemState(ctx, chip.body, chip.parts, k > 0 ? 'done' : null, k, { weight: 400, ink: chip.ink });
}

/**
 * The chip's text for preflight (§7.12), to add to a host's own `texts()`: one label on the card surface, never
 * critical (it is a reminder of another scene's words, and its own question is cut with an ellipsis rather than
 * shrunk, so it reports no overflow).
 * @param {TemplateContext} ctx @param {any | null} chip
 * @returns {TextEntry[]}
 */
export function chipTexts(ctx, chip) {
  if (!chip) return [];
  const L = LAYOUT[ctx.format];
  return [{
    el: chip.text, role: 'label', slot: 'chip', critical: false, ink: chip.ink, bg: chip.surface,
    alpha: chip.alpha, overflow: 0, size: L.chip, min: L.chip, weight: 600,
    text: `${CHIP_TEXT} ${chip.open.text}`,
  }];
}

// ---------------------------------------------------------------------------------------------
// The template
// ---------------------------------------------------------------------------------------------

/** @param {TemplateContext} ctx */
const isFallback = (ctx) => !!(ctx.view && ctx.view.pdFallback);

/** @type {TemplateModule} */
export default {
  id: ID,

  /**
   * The DOM: the label, and one card holding the question, the instruction and the think ring, each part absolutely
   * placed at layout so no part moves when another appears. No measurement here (plan §1.4).
   * @param {TemplateContext} ctx
   */
  build(ctx) {
    const { scene, format } = ctx;
    if (!drawsScene(scene)) {
      // another item, a side panel or a medium: board_generic draws the scene whole (review CG-2)
      const inner = boardGeneric.build(ctx);
      inner.pdFallback = true;
      return inner;
    }
    const L = LAYOUT[format];
    const { question: asked, label } = predictItems(scene);
    const item = /** @type {BoardItem} */ (asked);
    const plan = timetable(scene, item, label, format);
    const fixes = fixesOf(ctx.look);
    const baseline = !!ctx.motion && ctx.motion.entrance === 'baseline_reveal';
    const surface = ctx.roles.color('surface');
    const ink = ctx.roles.color('ink');
    const ink2 = ctx.roles.color('ink2');
    const root = h('div', {
      class: ['dyn-pd', `dyn-pd--${format}`],
      style: { position: 'absolute', left: '0px', top: '0px', right: '0px', bottom: '0px' },
    });

    // the label above the card: on the background (R-53's scrim remedy behind it when preflight asked for one)
    /** @type {any} */
    let head = null;
    if (label) {
      const lh = lineHeight('label', label.text || '');
      const text = h('div', {
        class: 'dyn-pd-label', dir: 'auto',
        style: {
          margin: '0px', fontFamily: 'var(--dyn-font-body)', fontWeight: String(L.label.weight),
          lineHeight: String(lh), textAlign: 'center', overflowWrap: 'anywhere', color: ctx.onBackground.ink2,
        },
      });
      const { words, tex } = richWords(ctx, text, String(label.text || ''));
      const mask = baseline ? revealMask(text) : null;
      const scrim = fixes.slots.label && fixes.slots.label.scrim ? ctx.roles.color('scrim') : null;
      const el = group('dyn-pd-label-group', mask || text);
      el.style.position = 'absolute';
      if (scrim) {
        Object.assign(el.style, { padding: '8px 24px 10px', borderRadius: `${RADIUS.chip}px`, background: scrim });
      }
      root.appendChild(el);
      head = {
        item: label, el, text, mask, words, tex, lh, scrim, enter: plan.label,
        size: L.label.max, min: L.label.min, overflow: 0, alpha: 0,
      };
    }

    // the question
    const qlh = lineHeight('heading', item.text || '');
    const question = h('div', {
      class: 'dyn-pd-q', dir: 'auto', dataset: { item: item.id },
      style: {
        position: 'absolute', fontFamily: 'var(--dyn-font-display)', fontWeight: String(L.question.weight),
        lineHeight: String(qlh), textAlign: 'center', overflowWrap: 'anywhere', color: ink,
      },
    });
    const q = richWords(ctx, question, String(item.text || ''));
    const qmask = baseline ? revealMask(question) : null;
    if (qmask) qmask.style.position = 'absolute';

    // §4.3's instruction, under the question
    const prompt = h('div', {
      class: 'dyn-pd-prompt', text: PROMPT_TEXT,
      style: {
        position: 'absolute', fontFamily: 'var(--dyn-font-body)', fontWeight: String(L.prompt.weight),
        fontSize: `${L.prompt.max}px`, lineHeight: String(LINE_HEIGHT.label.latin), textAlign: 'center',
        color: ink2, whiteSpace: 'nowrap',
      },
    });

    // the think ring (R-07: the think time is a visual ring only; its arc is a structure mark)
    /** @type {any} */
    let ring = null;
    if (plan.ring) {
      const mid = L.ring / 2;
      const r = mid - RING_STROKE; // the stroke's centre line, so the ring stays inside its box
      const track = svg('circle', {
        cx: String(mid), cy: String(mid), r: String(r), class: 'dyn-pd-ring-track',
        fill: 'none', stroke: ink2, 'stroke-width': String(RING_STROKE), 'stroke-opacity': '0.35',
      });
      const arc = svg('circle', {
        cx: String(mid), cy: String(mid), r: String(r), class: 'dyn-pd-ring-arc', fill: 'none',
        stroke: ctx.roles.color('struct.accent'), 'stroke-width': String(RING_STROKE), 'stroke-linecap': 'round',
      });
      const face = svg('svg', {
        class: 'dyn-pd-ring-svg', width: String(L.ring), height: String(L.ring), 'aria-hidden': 'true',
      }, track, arc);
      face.setAttribute('viewBox', `0 0 ${L.ring} ${L.ring}`);
      // the arc starts at 12 o'clock: a quarter turn, set once (never painted)
      Object.assign(face.style, { display: 'block', transform: 'rotate(-90deg)' });
      const num = h('div', {
        class: 'dyn-pd-ring-num',
        style: {
          position: 'absolute', left: '0px', top: '0px', width: `${L.ring}px`, height: `${L.ring}px`,
          display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'var(--dyn-font-body)',
          fontSize: `${L.ringNum}px`, fontWeight: '700', fontVariantNumeric: 'tabular-nums', lineHeight: '1',
          color: ink,
        },
      });
      const box = h('div', {
        class: 'dyn-pd-ring',
        style: { position: 'absolute', width: `${L.ring}px`, height: `${L.ring}px` },
      }, face, num);
      ring = { box, arc, num, length: 2 * Math.PI * r, alpha: 0, text: '' };
    }

    const card = h('div', {
      class: 'dyn-pd-card', dataset: { item: item.id },
      style: {
        position: 'absolute', boxSizing: 'border-box', borderRadius: `${RADIUS.card}px`, background: surface,
        padding: `${L.pad}px`,
      },
    }, qmask || question, prompt, ring ? ring.box : null);
    const cardGroup = group('dyn-pd-card-group', card);
    cardGroup.style.position = 'absolute';
    root.appendChild(cardGroup);
    ctx.root.appendChild(root);

    return {
      root, head, item, card, cardGroup, question, qmask, words: q.words, wordTex: q.tex, qlh, prompt, ring, plan,
      surface, ink, ink2, size: L.question.max, min: L.question.min, overflow: 0, alpha: 0, qalpha: 0,
      promptAlpha: 0, promptSize: L.prompt.max, promptOverflow: 0,
    };
  },

  /**
   * Measure and fit (runs again when a late face arrives; idempotent): the label in its line, then the question at the
   * largest size its card holds, and the parts of the card top to bottom from what the page laid out. Positions are
   * set here, once; `update` only paints.
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
    const b = boxes(format, zones);

    // --- the label ---------------------------------------------------------------------------------
    let cardTop = b.card.y0;
    if (v.head) {
      const hd = v.head;
      const range = slotRange(ctx, 'label', { min: L.label.min, max: L.label.max, role: 'label' });
      Object.assign(hd.el.style, {
        left: `${b.label.x0}px`, top: `${b.label.y0}px`, width: `${b.label.x1 - b.label.x0}px`,
      });
      const fit = fitLines(hd.text, {
        max: range.max, min: range.min, lines: L.label.lines, prefer: L.label.prefer, lh: hd.lh, step: SIZE_STEP,
      }, hd.words, hd.tex);
      hd.size = fit.size;
      hd.min = range.min;
      hd.overflow = fit.overflow;
      const bottom = hd.el.offsetTop + hd.el.offsetHeight;
      cardTop = Math.round(Math.max(bottom, b.label.y0 + range.min * hd.lh) + L.labelGap);
    }

    // --- the card ----------------------------------------------------------------------------------
    const cardW = b.card.x1 - b.card.x0;
    const innerW = cardW - 2 * L.pad;
    const room = Math.max(0, b.card.y1 - cardTop);
    Object.assign(v.cardGroup.style, { left: `${b.card.x0}px`, top: `${cardTop}px`, width: `${cardW}px` });
    Object.assign(v.card.style, { left: '0px', width: `${cardW}px` });
    const host = v.qmask || v.question;
    Object.assign(host.style, { left: `${L.pad}px`, width: `${innerW}px` });
    if (v.qmask) Object.assign(v.question.style, { position: 'relative', left: '0px', width: `${innerW}px` });
    Object.assign(v.prompt.style, { left: `${L.pad}px`, width: `${innerW}px` });

    const qRange = slotRange(ctx, 'question', { min: L.question.min, max: L.question.max, role: 'title' });
    const pRange = slotRange(ctx, 'prompt', { min: L.prompt.min, max: L.prompt.max, role: 'label' });
    const qFit = fitLines(v.question, {
      max: qRange.max, min: qRange.min, lines: L.question.lines, prefer: L.question.prefer, lh: v.qlh,
      step: SIZE_STEP,
    }, v.words, v.wordTex);
    v.question.style.fontSize = `${qFit.size}px`;
    v.size = qFit.size;
    v.min = qRange.min;
    v.prompt.style.fontSize = `${pRange.max}px`;
    v.promptSize = pRange.max;
    // one line of a fixed-size instruction: px it runs past its column (never clipped silently, §7.12)
    v.promptOverflow = Math.max(0, v.prompt.scrollWidth - v.prompt.clientWidth);

    // the parts, top to bottom, from what the page laid out at those sizes
    const qH = Math.max(Math.round(qFit.size * v.qlh), host.offsetHeight);
    const promptH = Math.max(Math.round(pRange.max * LINE_HEIGHT.label.latin), v.prompt.offsetHeight);
    let y = L.pad;
    host.style.top = `${y}px`;
    y += qH + L.gap;
    v.prompt.style.top = `${y}px`;
    y += promptH;
    if (v.ring) {
      y += L.gap;
      Object.assign(v.ring.box.style, { left: `${Math.round((cardW - L.ring) / 2)}px`, top: `${y}px` });
      y += L.ring;
    }
    const cardH = y + L.pad;
    v.card.style.height = `${cardH}px`;
    v.card.style.top = `${Math.max(0, Math.round((room - cardH) / 2))}px`;
    v.overflow = Math.max(qFit.overflow, Math.max(0, cardH - room));
  },

  /**
   * Paint scene time t: the label's entrance, the card's, the question's words, the instruction and the ring. A pure
   * function of t: every property painted on an element is painted on every frame, with a value (never '').
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

    if (v.head) {
      const e = enterAt(ctx, t, v.head.enter, LABEL_ENTER);
      v.head.alpha = e;
      paintTextEntrance(ctx, v.head.text, v.head.mask, e);
    }

    const enter = v.plan.question;
    // the card never leaves: §4.3 gives a prediction no answer, so it holds to the end of the scene
    const inP = enterAt(ctx, t, enter, CARD_ENTER);
    v.alpha = inP;
    const dy = ctx.reducedMotion || inP >= 1 ? 0 : Math.round((1 - inP) * CARD_RISE);
    p.style(v.card, 'opacity', fx(inP));
    p.style(v.card, 'visibility', inP > 0.001 ? 'visible' : 'hidden');
    p.style(v.card, 'transform', dy ? `translate(0, ${dy}px)` : 'none'); // 2D: see _shared.js paintRise

    const eText = enterAt(ctx, t, enter + seconds(ctx, TEXT_DELAY), TEXT_ENTER);
    v.qalpha = inP * eText;
    paintTextEntrance(ctx, v.question, v.qmask, eText);

    const ePrompt = enterAt(ctx, t, enter + seconds(ctx, TEXT_DELAY + PROMPT_DELAY), PROMPT_ENTER);
    v.promptAlpha = inP * ePrompt;
    paintRise(ctx, v.prompt, ePrompt, PROMPT_RISE);

    if (v.ring) {
      const ring = v.ring;
      const start = v.plan.ring.start;
      const secs = Math.max(0.001, v.plan.ring.seconds);
      const shown = enterAt(ctx, t, start, RING_IN)
        * (1 - ease('ease.exit', progress(t, start + secs, seconds(ctx, RING_OUT))));
      // linear, with no sound (R-07): the arc empties as the think time runs out
      const left = 1 - progress(t, start, secs);
      const dash = strokeDash(ring.length, left);
      ring.alpha = inP * shown;
      ring.text = shown > 0.001 ? String(Math.max(0, Math.ceil(secs * left - 1e-6))) : '';
      p.style(ring.box, 'opacity', fx(shown));
      p.style(ring.box, 'visibility', shown > 0.001 ? 'visible' : 'hidden');
      p.attr(ring.arc, 'stroke-dasharray', dash.dasharray);
      p.attr(ring.arc, 'stroke-dashoffset', dash.dashoffset);
      p.text(ring.num, ring.text);
    }
  },

  /**
   * Where a look_here points (plan §1.4, §1.5, §1.7): the question's card (`<item>`, or `<item>:term:<n>`: the same
   * card) — a shape, so cues.js draws its outline whole just outside the card, never across the question — or the
   * label's text. Its box as painted now, on whole stage px, with the dim group holding it. Null for anything else or
   * off the page.
   * @param {TemplateContext} ctx
   * @param {string} target
   * @returns {FocusRect | null}
   */
  focusRect(ctx, target) {
    if (isFallback(ctx)) {
      return typeof boardGeneric.focusRect === 'function' ? boardGeneric.focusRect(ctx, target) : null;
    }
    const v = ctx.view;
    const id = String(target || '').split(':')[0];
    const card = id === v.item.id;
    const head = v.head && id === v.head.item.id ? v.head : null;
    const el = card ? v.card : head ? head.text : null;
    const holder = card ? v.cardGroup : head ? head.el : null;
    const r = el ? ctx.rectOf(el) : null;
    if (!r || !holder) return null;
    const x0 = Math.round(r.x);
    const y0 = Math.round(r.y);
    return {
      rect: { x: x0, y: y0, w: Math.round(r.x + r.w) - x0, h: Math.round(r.y + r.h) - y0 },
      shape: 'rect', radius: card ? RADIUS.card : 0, el: holder, kind: card ? 'shape' : 'text',
    };
  },

  /**
   * The element holding an item's text, where cue.term finds its `[[term]]` spans (`.rt-k`, `_shared.js richWords`):
   * the question's text, or the label's. Null for anything else.
   * @param {TemplateContext} ctx
   * @param {string} itemId
   * @returns {Element | Element[] | null}
   */
  itemEl(ctx, itemId) {
    if (isFallback(ctx)) return typeof boardGeneric.itemEl === 'function' ? boardGeneric.itemEl(ctx, itemId) : null;
    const v = ctx.view;
    if (itemId === v.item.id) return v.question;
    return v.head && itemId === v.head.item.id ? v.head.text : null;
  },

  /**
   * The content groups a look_here dims (all but the target's): the label and the card; the same elements at every t,
   * and this template never writes their opacity (it animates their children).
   * @param {TemplateContext} ctx
   * @returns {Element[]}
   */
  dimGroups(ctx) {
    if (isFallback(ctx)) return typeof boardGeneric.dimGroups === 'function' ? boardGeneric.dimGroups(ctx) : [];
    const v = ctx.view;
    return [v.head ? v.head.el : null, v.cardGroup].filter(Boolean);
  },

  /**
   * The morph anchor (plan §1.4): none — a prediction's card is not a visual the explaining scene continues (its
   * question comes back as the chip above, not as a morph; B2f pairs the templates that have one).
   * @param {TemplateContext} _ctx
   * @returns {null}
   */
  anchor(_ctx) {
    return null;
  },

  /**
   * Static: this template shows no picture or clip (§4.3 gives a prediction none), so no slot has a media box; a scene
   * it hands to board_generic keeps board_generic's.
   * @param {TimedScene} scene
   * @param {Zones} zones
   * @param {'live' | 'render'} mode
   * @param {string | undefined} slot
   * @param {import('../core.js').Fixes} fixes
   * @returns {{w: number, h: number} | null}
   */
  mediaBox(scene, zones, mode, slot, fixes) {
    if (!drawsScene(scene)) {
      return typeof boardGeneric.mediaBox === 'function'
        ? boardGeneric.mediaBox(scene, zones, mode, slot, fixes) : null;
    }
    return null;
  },

  /**
   * Static: every TeX string the template renders for the scene (`texOf`; the same in both formats).
   * @param {TimedScene} scene
   * @param {ResolvedLook | null} look
   * @param {'landscape' | 'portrait'} fmt
   */
  tex(scene, look, fmt) {
    if (!drawsScene(scene)) return typeof boardGeneric.tex === 'function' ? boardGeneric.tex(scene, look, fmt) : [];
    return texOf(scene);
  },

  /**
   * The text on screen (preflight, §7.12): the label (slot `label`, on the background or on its R-53 scrim), the
   * question (`question`), the instruction (`prompt`) and the ring's number (`ring`, not critical) — the card's on its
   * `surface`, in the ink, size and weight painted now.
   * @param {TemplateContext} ctx
   * @returns {TextEntry[]}
   */
  texts(ctx) {
    if (isFallback(ctx)) return typeof boardGeneric.texts === 'function' ? boardGeneric.texts(ctx) : [];
    const v = ctx.view;
    const L = LAYOUT[ctx.format];
    const floors = TYPE_FLOORS[ctx.format];
    /** @type {TextEntry[]} */
    const out = [];
    if (v.head) {
      const hd = v.head;
      const bg = hd.scrim ? scrimmedBg(hd.scrim, ctx.onBackground, ctx.onBackground.ink2) : ctx.onBackground.bg;
      out.push({
        el: hd.text, role: 'label', slot: 'label', critical: true, ink: ctx.onBackground.ink2, bg,
        alpha: hd.alpha, overflow: hd.overflow, size: hd.size, min: hd.min, weight: L.label.weight,
        text: plainText(String(hd.item.text || '')),
      });
    }
    out.push({
      el: v.question, role: 'title', slot: 'question', critical: true, ink: v.ink, bg: v.surface,
      alpha: v.qalpha, overflow: v.overflow, size: v.size, min: v.min, weight: L.question.weight,
      text: plainText(String(v.item.text || '')),
    });
    out.push({
      el: v.prompt, role: 'label', slot: 'prompt', critical: true, ink: v.ink2, bg: v.surface,
      alpha: v.promptAlpha, overflow: v.promptOverflow, size: v.promptSize,
      min: Math.max(floors.label_min, L.prompt.min), weight: L.prompt.weight, text: PROMPT_TEXT,
    });
    if (v.ring && v.ring.text) {
      out.push({
        el: v.ring.num, role: 'label', slot: 'ring', critical: false, ink: v.ink, bg: v.surface,
        alpha: v.ring.alpha, overflow: 0, size: L.ringNum, min: L.ringNum, weight: 700, text: v.ring.text,
      });
    }
    return out;
  },
};
