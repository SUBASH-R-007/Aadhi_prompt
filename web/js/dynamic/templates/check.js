// @ts-check
/**
 * `check` (DYNAMIC_VIDEO_DESIGN §4.3 C, R1, class `prompt`, forced for `quiz` scenes, R-06 / R-07; B2 plan §1.4
 * template contract, §1.5 item states, §1.6 slots, §1.11 fit, §2, §P): the retrieval check **after** the explanation —
 * the question, its options, the think ring, then the answer and the explanation line.
 *
 * Data (§4.3 `check`, templates.py `_fit_check`), two forms:
 * - a **quiz** scene (`TimedScene.quiz`, `QuizTiming`): `question`, `options`, `correct_index`, `explanation` and the
 *   narration's own clock - `countdown_start`, `countdown_seconds` (set from §1.4's think time) and `reveal_start`.
 *   A quiz scene has no board items, so events.py writes it no events today and the quiz's own clock is the whole
 *   timetable; a `countdown` or `answer` event of any target (the question has no item id of its own, so it is
 *   reached by kind) overrides that mark, and a `reveal` on `option:<i>` moves that one option off the stagger -
 *   which is every (kind, target) pair `templates.py consumes` declares for this template.
 * - a **board** scene with one blank `question` item and at most one `heading` (`_blank_question`, the shape
 *   `_fit_check` scores): the question is the item's `text`, the answer and explanation its `justification`, and there
 *   are no options (§4.3's `free_recall` / `apply`). Its ring is a `countdown` event, else §1.4's think time from the
 *   entrance; its reveal an `answer` event, else the beat that fills the item, else the ring's end.
 *
 * Kinds (§4.3: `mcq` <= 4 options and 16:9 only, `free_recall`, `true_false`, `apply`; a reel takes free recall or
 * true / false only, R-06) are read from the **data**, never from the stored variant: `kindOf` gives `free_recall`
 * without options, `true_false` with two and `mcq` with more, which is `direct.variant_for`'s own rule. `apply` is a
 * one-step problem with no options, so it is laid out exactly as `free_recall` (direct.py never stores it, and the
 * slot table has one `default` variant per format, so a stored variant can never disagree with the box the page lays
 * text out in - the drift review D7 found with `definition_card`).
 *
 * More options than the format takes (`MAX_OPTIONS`, templates.py `CHECK_MAX_OPTIONS`: 4 in 16:9, 2 in 9:16) is not
 * laid out small, hidden or dropped: the scene goes to `board_generic` **whole** (`drawsScene`), whose quiz stack
 * compacts and collapses cards, with every hook handed on. That is plan §1.4's invariant 2 (never drop content) and
 * the CG-2 parity rule (`_fit_check` must score 0 what this refuses), and it is what a 9:16 build of a 4-option
 * lesson quiz does - a reel's own quiz has two options by R-06.
 *
 * `feedback_wrong` (per-option feedback) is the live player's, as in B1: neither this template nor `board_generic`
 * draws it in an MP4, and §4.3 gives the reveal the explanation line only.
 *
 * Layout. One block per format, measured and fitted once in `layout`, so nothing moves with t:
 * - a **top band** holding the `label` (the heading item's text, else a quiz scene's title - the only text naming the
 *   check) and, at its right, the **think ring**, whose place never changes (R-07: the think time is a visual ring
 *   only, no riser and no sound of the page's);
 * - below it, centred in what is left: the `question` (on the background, in the display face), the **option tiles**
 *   on `surface`, and the `explanation` line (on the background). The explanation's room is reserved from the start,
 *   so the block never reflows when it appears.
 * - **16:9**: the content box is the body's width (x 96-1824) from the title zone's top (y 112) to the body's bottom
 *   (y 900, clear of the caption band); tiles are two fixed 852 px columns (the last row centred), so two options sit
 *   side by side and four make a 2x2 block.
 * - **9:16**: the text column (x 192-780, y 340-1020), the part of the stage both critical bands hold whatever the
 *   row's y (R-59, preflight's `CRITICAL_BANDS`); tiles are stacked, one per row.
 * - A tile keeps its letter column (`mcq`'s A / B / C / D) and its check column whether or not they are drawn, so the
 *   stored slot width is always the one the page lays text out in (recap_retrieval's icon-column rule, plan §1.11);
 *   without letters the text box is centred in the tile instead.
 *
 * Choreography (§4.3, §4.8): the question's words land with the personality's text entrance; **the options appear with
 * a 100 ms stagger**, each rising 24 px as it fades in; the ring runs **linearly** with its number counting whole
 * seconds down; on reveal the correct tile takes the `done` item state - an `ok` check pops beside it and a 2 px `ok`
 * border draws round it (plan §1.5, `_shared.js paintItemState`; §4.3's chime is the resolver's SFX, not the page's) -
 * the other tiles dim to 40 %, and then the explanation line rises 16 px into place. Without options (free recall /
 * apply) there is no tile to mark, and §4.3 gives the state to the tile alone, so the revealed answer is that same
 * line, rising into place under the question (an `ok` border round a full-width line read as a text field, and its
 * check sat over the last word: B2-demo frames). Template durations
 * take the personality's scale (`_shared.js seconds` / `enterAt`); the think time does not (it is the narration's own
 * pause), and neither do the cues (cues.js).
 *
 * Emphasis is cues.js's (§4.2, plan §1.4 / §1.5). `check` consumes `reveal`, `countdown` and `answer` only
 * (templates.py), so the stage draws it no `cue_term` and no `look_here`: R-16's hold is answered by the ring itself.
 * The geometry hooks are still complete and honest - `focusRect` gives a tile (a `shape`, so an outline is drawn
 * outside it, never across its text), the question, the explanation or the label, `itemEl` the board form's question
 * and then its answer in the item's field order, and `dimGroups` every content group. Nothing here writes outline,
 * text-decoration, filter, backdrop-filter or will-change, and no structure role colours content (§4.2): only the
 * ring's arc, a structure mark, takes `struct.accent`.
 *
 * Determinism (§7.4, plan §P): `update` is a pure function of t; every property it paints is painted on every frame
 * with a value (never ''), so a frame never depends on paint order; translates are whole pixels and 2D (see
 * `_shared.js paintRise`); every size and position is measured once in `layout` and never changes with t.
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
  RADIUS, enterAt, fitLines, group, itemStateParts, paintItemState, paintRise, paintTextEntrance, place,
  revealMask, richWords, scrimmedBg, seconds, slotRange,
} from './_shared.js';

/** @typedef {import('./types.js').TemplateContext} TemplateContext */
/** @typedef {import('./types.js').TemplateModule} TemplateModule */
/** @typedef {import('./types.js').FocusRect} FocusRect */
/** @typedef {import('./types.js').TextEntry} TextEntry */
/** @typedef {import('../../shared/types.js').TimedScene} TimedScene */
/** @typedef {import('../../shared/types.js').BoardItem} BoardItem */
/** @typedef {import('../../shared/types.js').QuizTiming} QuizTiming */
/** @typedef {import('../core.js').Box} Box */
/** @typedef {import('../core.js').Zones} Zones */

// ---------------------------------------------------------------------------------------------
// Motion (§4.3 check, §4.8)
// ---------------------------------------------------------------------------------------------

/** Each option tile fades in and rises this far (§4.8 medium move; §4.3's cards rise). */
export const CARD_RISE = 24;
/** The question's words follow its entrance by 80 ms (list_build's number-then-text rule). */
export const TEXT_DELAY = 0.08;
/** ... and enter over 450 ms with the personality's text entrance. */
export const TEXT_ENTER = 0.45;
/** The first option appears this long after the question (board_generic's quiz: INITIAL_DELAY + 0.1). */
export const OPTION_DELAY = 0.45;
/** §4.3: "options appear with a 100 ms stagger". */
export const OPTION_STAGGER = 0.1;
/** Each option enters over 400 ms (§4.8 small-to-medium move). */
export const OPTION_ENTER = 0.4;
/** §4.3: on reveal "the correct tile turns `ok` with a check pop" - the `done` state over 300 ms, `pop` easing. */
export const CHECK_POP = 0.3;
/** ... and the others "dim to 40%" ... */
export const OPTION_DIM = 0.4;
/** ... over 300 ms (R-54's dim ramp is the cue's; this is the template's own, the same length). */
export const DIM_SECONDS = 0.3;
/** §4.3: "then the explanation line appears" - after the dim has settled ... */
export const EXPLAIN_DELAY = 0.5;
/** ... rising 16 px (§4.3 recap_retrieval's answer rise) over 400 ms. */
export const EXPLAIN_RISE = 16;
export const EXPLAIN_ENTER = 0.4;
/** The ring fades in 250 ms after its start mark and leaves 30 % faster (§4.8); it never moves (R-07). */
export const RING_DELAY = 0.25;
export const RING_IN = 0.3;
export const RING_OUT = RING_IN * 0.7;
/** A ring shorter than this is not drawn at all (there is nothing to think about). */
export const RING_MIN = 0.5;
/** The label's entrance (the question's length). */
export const LABEL_ENTER = 0.45;
/** The think ring's stroke width (board_generic's quiz ring, dynamic.css `.dyn-ring-track`). */
export const RING_STROKE = 8;

// ---------------------------------------------------------------------------------------------
// Think time (§1.4: `max(min_fmt, words_to_read / 130 * 60 + 3 s)`, capped at 12 s)
// ---------------------------------------------------------------------------------------------

/** §1.4's reading rate for the think time, words per minute. */
export const THINK_WPM = 130;
/** ... plus this long to decide. */
export const THINK_LEAD = 3;
/** ... capped here. */
export const THINK_CAP = 12;
/** ... and never shorter than this (R-07: >= 5 s in 16:9, >= 4 s in 9:16). */
export const THINK_MIN = Object.freeze({ landscape: 5, portrait: 4 });

/**
 * §1.4's think time for `words` words to read (the question plus its options) in `format`. The planner sets
 * `QuizScene.countdown_seconds` from this formula, so a quiz scene's ring takes the stored seconds; this is what the
 * board form's ring runs for when no `countdown` event times it. Pure.
 * @param {number} words @param {'landscape' | 'portrait'} format
 */
export function thinkSeconds(words, format) {
  const n = Math.max(0, Number(words) || 0);
  const read = (n / THINK_WPM) * 60 + THINK_LEAD;
  return Math.max(THINK_MIN[format], Math.min(THINK_CAP, read));
}

/** Words of a rich-lite string, as §1.4 counts them (whitespace-separated, markup and TeX stripped). @param {string} s */
export function wordCount(s) {
  const text = plainText(String(s || '')).trim();
  return text ? text.split(/\s+/).length : 0;
}

// ---------------------------------------------------------------------------------------------
// Layout (stage px; R-29 floors; the catalogue's zones and layout tokens)
// ---------------------------------------------------------------------------------------------

/** Line heights of the type scale (zones.py TYPE_SCALE, plan §2: Indic heading >= 1.25, Indic body >= 1.45;
 * check.test.js pins them to the catalogue's `type.scale`). */
export const LINE_HEIGHT = Object.freeze({
  heading: Object.freeze({ latin: 1.15, indic: 1.25 }),
  body: Object.freeze({ latin: 1.35, indic: 1.45 }),
  label: Object.freeze({ latin: 1.3, indic: 1.45 }),
});

/** Size steps when fitting (board_generic's and textfit.py's 4 px). */
export const SIZE_STEP = 4;

/**
 * The most options a format's check draws (templates.py `CHECK_MAX_OPTIONS`; §4.3: `mcq` is <= 4 options and 16:9
 * only, and 9:16 takes two). More than this is `board_generic`'s whole scene, never a tile squeezed past R-29.
 */
export const MAX_OPTIONS = Object.freeze({ landscape: 4, portrait: 2 });

/** An mcq option's letter (board_generic's option badges). @param {number} i */
export const optionLetter = (i) => String.fromCharCode(65 + Math.max(0, Math.min(25, Math.round(i))));

/**
 * Per-format layout. `label` / `question` / `option` / `explain`: each slot's size range, line limit, preferred line
 * count and weight. R-29: a label is at least 40 px (16:9) / 44 px (9:16); every other size is above the text floor
 * (36 / 40 px) and, in 9:16, text at most 64 px is at least weight 600. The 16:9 question at 56-72 px is **below**
 * R-29's nominal heading band (84-120) and above its floor, as deviation 31 allows the pilots' sizes: a check question
 * is a whole sentence (the schema allows 600 characters), which no heading size can hold - a B2-demo proposal, pending
 * the owner's review with the reference frames.
 * `ring` / `ringNum`: the think ring's box and its number's size (a label). `pad`: a tile's padding; `gap`: between
 * the block's parts; `tileGap`: between tiles; `labelGap`: under the top band. `badge`: the letter column of a tile
 * (kept whether or not letters are drawn). `perRow`: tiles side by side. `tileW`: a tile's width (16:9 two fixed
 * columns of the content box; 9:16 the text column, given at layout). check.test.js pins the slots this table gives
 * (`slotRows`) to the catalogue's.
 */
export const LAYOUT = Object.freeze({
  landscape: Object.freeze({
    label: Object.freeze({ max: 40, min: 40, lines: 1, prefer: 1, weight: 600 }),
    question: Object.freeze({ max: 72, min: 56, lines: 3, prefer: 2, weight: 700 }),
    option: Object.freeze({ max: 48, min: 40, lines: 2, prefer: 1, weight: 600 }),
    explain: Object.freeze({ max: 48, min: 40, lines: 2, prefer: 1, weight: 400 }),
    ring: 120,
    ringNum: 40,
    pad: 24,
    gap: 24,
    tileGap: 24,
    labelGap: 24,
    badge: 48,
    perRow: 2,
    tileW: 852,
  }),
  portrait: Object.freeze({
    label: Object.freeze({ max: 44, min: 44, lines: 1, prefer: 1, weight: 600 }),
    question: Object.freeze({ max: 60, min: 48, lines: 4, prefer: 3, weight: 700 }),
    option: Object.freeze({ max: 44, min: 40, lines: 2, prefer: 1, weight: 600 }),
    explain: Object.freeze({ max: 44, min: 40, lines: 2, prefer: 1, weight: 600 }),
    ring: 96,
    ringNum: 44,
    pad: 16,
    gap: 16,
    tileGap: 16,
    labelGap: 16,
    badge: 44,
    perRow: 1,
    tileW: 0,
  }),
});

/**
 * The boxes of a format (pure; whole stage px): `content` (everything the template draws in), `label` (the line at the
 * top left of the band), `ring` (the think ring, at the band's right) and `block` (what is left, where the question,
 * the tiles and the explanation are centred). 16:9 runs from the title zone's top (y 112) to the body box's bottom
 * (y 900, clear of the caption band) across the body's width; 9:16 from y 340 to 1020 in the text column (x 192-780),
 * whose x every critical band holds (R-59).
 * @param {'landscape' | 'portrait'} format
 * @param {Zones} zones
 * @returns {{content: Box, label: Box, ring: Box, block: Box}}
 */
export function boxes(format, zones) {
  const L = LAYOUT[format];
  const col = format === 'portrait' ? zones.textColumn : zones.body;
  const content = { x0: col.x0, y0: zones.title.y0, x1: col.x1, y1: zones.body.y1 };
  const labelH = Math.round(L.label.max * LINE_HEIGHT.label.latin);
  const band = Math.max(labelH, L.ring);
  const ring = {
    x0: content.x1 - L.ring, y0: content.y0 + Math.round((band - L.ring) / 2),
    x1: content.x1, y1: content.y0 + Math.round((band - L.ring) / 2) + L.ring,
  };
  const label = {
    x0: content.x0, y0: content.y0 + Math.round((band - labelH) / 2),
    x1: ring.x0 - L.gap, y1: content.y0 + Math.round((band - labelH) / 2) + labelH,
  };
  const block = { x0: content.x0, y0: content.y0 + band + L.labelGap, x1: content.x1, y1: content.y1 };
  return { content, label, ring, block };
}

/** A tile's width in `format` (16:9 the fixed column; 9:16 the text column). @param {'landscape' | 'portrait'} format
 * @param {Zones} zones */
export function tileWidth(format, zones) {
  if (format === 'landscape') return LAYOUT.landscape.tileW;
  const b = boxes(format, zones).block;
  return b.x1 - b.x0;
}

/**
 * A tile's text box width: the tile less its padding, its letter column and the room the `ok` check needs on the
 * right (both kept whether or not they are drawn, so the stored slot width is the one the page lays out).
 * @param {'landscape' | 'portrait'} format @param {Zones} zones
 */
export function tileTextWidth(format, zones) {
  const L = LAYOUT[format];
  return tileWidth(format, zones) - 2 * L.pad - 2 * (L.badge + L.gap);
}

/**
 * The rows `n` tiles are laid out in: at most `per` per row, in order (the last row is centred by `layout`).
 * @param {number} n @param {number} per
 * @returns {number[][]}
 */
export function tileRows(n, per) {
  const width = Math.max(1, per);
  /** @type {number[][]} */
  const rows = [];
  for (let k = 0; k < n; k += width) {
    /** @type {number[]} */
    const row = [];
    for (let i = k; i < Math.min(n, k + width); i++) row.push(i);
    rows.push(row);
  }
  return rows;
}

/**
 * The text slots of this template (aadhi/compose/dynamic/templates.py SlotSpec, what textfit.py fits; plan §1.6,
 * §1.11): slot -> format -> variant -> the spec. One variant, `default`: a check's layout is a function of its data
 * (how many options it has), never of the stored variant, so a stored variant can never disagree with the box the
 * page lays text out in (review D7). Line heights are the Latin ones (Indic text takes LINE_HEIGHT's Indic values,
 * which textfit.py's Indic measure must use). Pure.
 * @param {Zones} landscape @param {Zones} portrait
 */
export function slotRows(landscape, portrait) {
  /** @param {'landscape' | 'portrait'} fmt @param {Zones} zones */
  const table = (fmt, zones) => {
    const L = LAYOUT[fmt];
    const b = boxes(fmt, zones);
    const wide = b.block.x1 - b.block.x0;
    /**
     * @param {{max: number, min: number, lines: number, prefer: number, weight: number}} s
     * @param {number} w @param {'title' | 'body' | 'label'} role @param {'display' | 'body'} face
     * @param {number} lh @param {{critical?: boolean, tabular?: boolean}} [opts]
     */
    const spec = (s, w, role, face, lh, opts = {}) => ({
      w, max_lines: s.lines, size: [s.min, s.max], role, face, weight: s.weight, line_height: lh,
      prefer_lines: s.prefer > 0 ? s.prefer : null, critical: opts.critical !== false, tabular_nums: !!opts.tabular,
    });
    return {
      label: spec(L.label, b.label.x1 - b.label.x0, 'label', 'body', LINE_HEIGHT.label.latin),
      question: spec(L.question, wide, 'title', 'display', LINE_HEIGHT.heading.latin),
      option: spec(L.option, tileTextWidth(fmt, zones), 'body', 'body', LINE_HEIGHT.body.latin),
      explain: spec(L.explain, wide, 'body', 'body', LINE_HEIGHT.body.latin),
      // the countdown's number: a label in the ring's box, never critical (R-07: the think time is the ring itself)
      ring: spec({ max: L.ringNum, min: L.ringNum, lines: 1, prefer: 1, weight: 700 }, L.ring, 'label', 'body',
        LINE_HEIGHT.label.latin, { critical: false, tabular: true }),
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

/** Indic scripts (Devanagari to Sinhala, U+0900-U+0DFF), whose text takes the taller line heights (plan §2). */
const INDIC = new RegExp(`[${['Devanagari', 'Bengali', 'Gurmukhi', 'Gujarati', 'Oriya', 'Tamil', 'Telugu', 'Kannada',
  'Malayalam', 'Sinhala'].map((x) => `\\p{Script=${x}}`).join('')}]`, 'u');

/**
 * The line height of `text` in type role `role` (LINE_HEIGHT: Indic text takes the Indic value).
 * @param {'heading' | 'body' | 'label'} role @param {string} text
 */
export function lineHeight(role, text) {
  return INDIC.test(plainText(String(text || ''))) ? LINE_HEIGHT[role].indic : LINE_HEIGHT[role].latin;
}

/** The board item kinds this template draws (§4.3; `_blank_question`'s shape allows exactly these). */
export const DRAWN_KINDS = Object.freeze(['question', 'heading']);

/** @param {BoardItem} item */
const textOf = (item) => (typeof item.text === 'string' ? item.text.trim() : '');

/** @param {TimedScene} scene @returns {QuizTiming | null} */
const quizOf = (scene) => {
  const q = scene.quiz;
  return q && typeof q.question === 'string' && Array.isArray(q.options) ? q : null;
};

/**
 * What the scene gives this template, from either form (pure):
 * - `label`: the text above the block - the first `heading` item with text (board form), else a quiz scene's title;
 * - `item`: the board form's `question` item (null for a quiz);
 * - `question`: the question's rich-lite text;
 * - `options`: the option texts, in the order the canonicaliser shuffled them;
 * - `correct`: the correct option's index (-1 without options);
 * - `explanation`: the quiz's explanation, or the board item's `justification` (the revealed answer).
 * @param {TimedScene} scene
 * @returns {{label: {text: string, item: BoardItem | null} | null, item: BoardItem | null, question: string,
 *   options: string[], correct: number, explanation: string}}
 */
export function checkItems(scene) {
  const quiz = quizOf(scene);
  /** @type {BoardItem | null} */
  let heading = null;
  /** @type {BoardItem | null} */
  let question = null;
  for (const item of scene.board || []) {
    if (!textOf(item)) continue;
    if (item.kind === 'heading') {
      if (!heading) heading = item;
    } else if (item.kind === 'question') {
      if (!question) question = item;
    }
  }
  const title = String(scene.title || '').trim();
  const label = heading ? { text: textOf(heading), item: heading }
    : quiz && title ? { text: title, item: null } : null;
  if (quiz) {
    const options = quiz.options.map((o) => String(o || '').trim()).filter((o) => o);
    return {
      label,
      item: null,
      question: String(quiz.question || '').trim(),
      options,
      correct: Number.isInteger(quiz.correct_index) ? Number(quiz.correct_index) : -1,
      explanation: String(quiz.explanation || '').trim(),
    };
  }
  return {
    label,
    item: question,
    question: question ? textOf(question) : '',
    options: [],
    correct: -1,
    explanation: question && typeof question.justification === 'string' ? question.justification.trim() : '',
  };
}

/**
 * The check's kind from its data (§4.3's kinds; `direct.variant_for`'s own rule, so the drawn kind and the stored
 * variant agree wherever the format has the variant): no options is `free_recall` (which is how `apply`, a one-step
 * problem, is drawn too), two is `true_false`, more is `mcq`.
 * @param {TimedScene} scene
 * @returns {'free_recall' | 'true_false' | 'mcq'}
 */
export function kindOf(scene) {
  const n = checkItems(scene).options.length;
  return n === 0 ? 'free_recall' : n === 2 ? 'true_false' : 'mcq';
}

/**
 * The look variant `direct.variant_for` stores for this scene in `fmt` (`kindOf`, or `free_recall` when the format
 * has no `mcq`): the page draws what the data says, so this is for the tests that pin the two together.
 * @param {TimedScene} scene @param {'landscape' | 'portrait'} fmt
 */
export function variantOf(scene, fmt) {
  const kind = kindOf(scene);
  return kind === 'mcq' && fmt !== 'landscape' ? 'free_recall' : kind;
}

/** Whether a tile shows its letter (§4.3: `mcq`'s "<= 4 options"; a True / False pair needs no A / B).
 * @param {TimedScene} scene */
export const showsLetters = (scene) => kindOf(scene) === 'mcq';

/**
 * Whether this template draws the scene whole in `format` (templates.py `_fit_check` must score 0 otherwise - the
 * CG-2 parity rule): a quiz scene with a question, 2 to `MAX_OPTIONS[format]` options and a correct index among them,
 * or a board scene with exactly one `question` item with text (§4.3's free recall / apply, whose answer is its
 * `justification`) and at most one `heading`; in both forms nothing else to show - no scene medium, no poster and no
 * shown side panel, which §4.3 gives a check none of and `mediaBox` therefore plans no box for (review D5: a picture
 * planned by no one vanishes from the video). Anything else is `board_generic`'s, every hook handed on.
 * @param {TimedScene} scene
 * @param {'landscape' | 'portrait'} format
 */
export function drawsScene(scene, format) {
  if (scene.media || scene.poster) return false;
  if (scene.side_panel && scene.layout?.show_side_panel !== false) return false;
  const quiz = quizOf(scene);
  let questions = 0;
  let headings = 0;
  for (const item of scene.board || []) {
    if (!textOf(item)) continue;
    const kind = String(item.kind);
    if (!DRAWN_KINDS.includes(kind)) return false;
    if (kind === 'question') questions += 1;
    if (kind === 'heading') headings += 1;
  }
  if (headings > 1) return false;
  if (quiz) {
    // a quiz scene carries no board item (its question is the quiz's); one that does would lose it here
    if (questions > 0) return false;
    const { question, options, correct } = checkItems(scene);
    return !!question && options.length >= 2 && options.length <= MAX_OPTIONS[format]
      && correct >= 0 && correct < options.length;
  }
  return questions === 1 && !!checkItems(scene).question;
}

/**
 * The earliest event of kind `kind` whose target `match` accepts, or null. The stage applies only the kinds
 * `templates.py consumes` declares (`reveal`, `countdown` and `answer`, on an `<item>` or an `option:<i>`); they are
 * read here because they say WHEN, not how.
 * @param {TimedScene} scene @param {string} kind @param {(target: string) => boolean} match
 * @returns {{t: number, dur: number | null} | null}
 */
function firstEvent(scene, kind, match) {
  /** @type {{t: number, dur: number | null} | null} */
  let out = null;
  for (const e of scene.events || []) {
    if (!e || e.kind !== kind || typeof e.t !== 'number' || !Number.isFinite(e.t)) continue;
    if (typeof e.target !== 'string' || !match(e.target)) continue;
    const dur = typeof e.dur === 'number' && Number.isFinite(e.dur) && e.dur > 0 ? e.dur : null;
    if (!out || e.t < out.t) out = { t: e.t, dur };
  }
  return out;
}

/**
 * The first event of kind `kind` on item `id` (its target's item part), or null: the board form's own clock
 * (`countdown` times its ring, `answer` its reveal).
 * @param {TimedScene} scene @param {string} kind @param {string} id
 * @returns {{t: number, dur: number | null} | null}
 */
export function eventFor(scene, kind, id) {
  return firstEvent(scene, kind, (target) => target.split(':')[0] === id);
}

/**
 * The first event of kind `kind` on exactly `target` (`option:<i>`: an option whose entrance the plan moved off the
 * stagger), or null.
 * @param {TimedScene} scene @param {string} kind @param {string} target
 * @returns {{t: number, dur: number | null} | null}
 */
export function eventAt(scene, kind, target) {
  return firstEvent(scene, kind, (t) => t === target);
}

/**
 * The first event of kind `kind`, whatever its target, or null: the quiz form's clock, whose question is the quiz's
 * own and has no board item id, so a `countdown` or `answer` event reaches it by kind (on the question's stand-in or
 * on the correct `option:<i>` - either way it says when the ring runs and when the answer is out).
 * @param {TimedScene} scene @param {string} kind
 * @returns {{t: number, dur: number | null} | null}
 */
export function anyEvent(scene, kind) {
  return firstEvent(scene, kind, () => true);
}

/**
 * The scene's timetable for this template (pure; scene-relative seconds):
 * - `enter`: when the question enters (a quiz starts with the scene; a board form at its item's entrance);
 * - `label`: when the label enters (a heading item's own entrance, else the question's);
 * - `options`: each option's entrance (§4.3's 100 ms stagger after the question, or its own `reveal` event);
 * - `ring`: the think ring's start and length, or null when there is nothing to think about. A `countdown` event
 *   first, then a quiz's own clock (`countdown_start`, `countdown_seconds`), else §1.4's think time from the words on
 *   screen; always cut off at the reveal, so the ring's time is over exactly when the answer appears (R-07);
 * - `revealAt`: an `answer` event first, then a quiz's `reveal_start`, else the beat that fills the blank item (null
 *   when there is nothing to reveal);
 * - `explainAt`: `revealAt` (the explanation's own delay is a template duration, so `update` scales it).
 * @param {TimedScene} scene
 * @param {'landscape' | 'portrait'} format
 * @returns {{enter: number, label: number, options: number[], ring: {start: number, seconds: number} | null,
 *   revealAt: number | null, explainAt: number | null}}
 */
export function timetable(scene, format) {
  const { label, item, question, options, explanation } = checkItems(scene);
  const quiz = quizOf(scene);
  const duration = Math.max(0, Number(scene.duration) || 0);
  const timings = itemTimings(scene);
  const timing = item ? timings.find((x) => x.id === item.id) : null;
  const enter = timing ? timing.enter : 0;
  const head = label && label.item ? timings.find((x) => x.id === /** @type {any} */ (label.item).id) : null;
  // §4.3's 100 ms stagger, unless the plan gave an option a `reveal` event of its own
  const optionAt = options.map((_, i) => {
    const ev = eventAt(scene, 'reveal', `option:${i}`);
    return ev ? Math.max(enter, ev.t) : enter + OPTION_DELAY + OPTION_STAGGER * i;
  });

  /** @type {number | null} */
  let revealAt = null;
  if (quiz) {
    const ev = anyEvent(scene, 'answer');
    revealAt = Math.max(enter, ev ? ev.t : Number(quiz.reveal_start) || 0);
  } else if (explanation) {
    const ev = item ? eventFor(scene, 'answer', item.id) : null;
    const fill = timing && timing.fill !== null ? timing.fill : null;
    revealAt = ev ? Math.max(enter, ev.t) : fill !== null ? Math.max(enter, fill) : null;
  }

  let start = enter + RING_DELAY;
  let length = 0;
  if (quiz) {
    const ev = anyEvent(scene, 'countdown');
    start = Math.max(enter, ev ? ev.t : Number(quiz.countdown_start) || 0);
    length = Math.max(0, ev && ev.dur !== null ? ev.dur : Number(quiz.countdown_seconds) || 0);
  } else {
    const ev = item ? eventFor(scene, 'countdown', item.id) : null;
    if (ev) {
      start = Math.max(enter, ev.t);
      length = ev.dur !== null ? ev.dur : thinkSeconds(wordCount(question) + options.reduce((n, o) => n + wordCount(o), 0), format);
    } else {
      length = thinkSeconds(wordCount(question) + options.reduce((n, o) => n + wordCount(o), 0), format);
    }
  }
  // the ring never runs past the reveal, nor past the scene (R-07: its time is over when the answer appears). A ring
  // that already ends there keeps its own length: `reveal_start - countdown_start` is the same number through a
  // float subtraction, and the shorter one would make the countdown read one frame differently
  const end = duration > 0 ? duration : start + length;
  const stop = Math.min(revealAt !== null ? revealAt : end, end);
  if (stop - start < length - 1e-6) length = Math.max(0, stop - start);
  return {
    enter,
    label: head ? head.enter : enter,
    options: optionAt,
    ring: length >= RING_MIN ? { start, seconds: length } : null,
    revealAt,
    explainAt: revealAt,
  };
}

/**
 * Every TeX string the template renders for the scene (the static `tex` hook; render.js converts them before
 * `ready`): the `$...$` spans of the label, the question, every option and the explanation, inline (`richWords`
 * mounts each with display off), in the order they are built.
 * @param {TimedScene} scene
 * @returns {{tex: string, display: boolean}[]}
 */
export function texOf(scene) {
  const { label, question, options, explanation } = checkItems(scene);
  /** @type {{tex: string, display: boolean}[]} */
  const out = [];
  /** @param {string | null | undefined} text */
  const add = (text) => {
    for (const tex of mathSpans(String(text || ''))) if (tex.trim()) out.push({ tex, display: false });
  };
  if (label) add(label.text);
  add(question);
  for (const option of options) add(option);
  add(explanation);
  return out;
}

// ---------------------------------------------------------------------------------------------
// The template
// ---------------------------------------------------------------------------------------------

/** @type {TemplateModule} */
export default {
  id: 'check',

  /**
   * The DOM: the label, the think ring, the question, one tile per option and the explanation line. No measurement
   * here (plan §1.4).
   * @param {TemplateContext} ctx
   */
  build(ctx) {
    const { scene, format } = ctx;
    if (!drawsScene(scene, format)) {
      // more options than the format takes, a kind this template does not draw, or something to show beside the
      // question: board_generic draws the scene whole
      const whole = boardGeneric.build(ctx);
      whole.ckFallback = true;
      return whole;
    }
    const L = LAYOUT[format];
    const data = checkItems(scene);
    const plan = timetable(scene, format);
    const fixes = fixesOf(ctx.look);
    const baseline = !!ctx.motion && ctx.motion.entrance === 'baseline_reveal';
    const letters = showsLetters(scene);
    const surface = ctx.roles.color('surface');
    const ink = ctx.roles.color('ink');
    const ink2 = ctx.roles.color('ink2');
    const root = h('div', {
      class: ['dyn-ck', `dyn-ck--${format}`],
      style: { position: 'absolute', left: '0px', top: '0px', right: '0px', bottom: '0px' },
    });

    /**
     * A text block on the background, with the R-53 scrim remedy behind it when preflight asked for it. The group is
     * the stage's (it owns a dim group's opacity, plan §1.4), so everything this template animates sits in `inner`.
     * @param {string} cls @param {string} slot @param {string} text
     * @param {{weight: number, align: string, face: string, role: 'heading' | 'body' | 'label', colour: string}} spec
     */
    const onBg = (cls, slot, text, spec) => {
      const lh = lineHeight(spec.role, text);
      const el = h('div', {
        class: cls, dir: 'auto',
        style: {
          margin: '0px', fontFamily: `var(--dyn-font-${spec.face})`, fontWeight: String(spec.weight),
          lineHeight: String(lh), textAlign: spec.align, overflowWrap: 'anywhere', color: spec.colour,
        },
      });
      const { words, tex } = richWords(ctx, el, text);
      const mask = baseline ? revealMask(el) : null;
      const scrim = fixes.slots[slot] && fixes.slots[slot].scrim ? ctx.roles.color('scrim') : null;
      const inner = h('div', { class: `${cls}-box` }, mask || el);
      if (scrim) {
        Object.assign(inner.style, { padding: '8px 24px 10px', borderRadius: `${RADIUS.card}px`, background: scrim });
      }
      const holder = group(`${cls}-group`, inner);
      holder.style.position = 'absolute';
      root.appendChild(holder);
      return { el, mask, inner, holder, words, tex, lh, scrim, text, size: 0, min: 0, overflow: 0, alpha: 0 };
    };

    // the label above the block (the heading item, or a quiz scene's title)
    const head = data.label
      ? onBg('dyn-ck-label', 'label', data.label.text,
        { weight: L.label.weight, align: 'start', face: 'body', role: 'label', colour: ctx.onBackground.ink2 })
      : null;

    // the question (the display face, on the background)
    const question = onBg('dyn-ck-q', 'question', data.question,
      { weight: L.question.weight, align: 'center', face: 'display', role: 'heading', colour: ctx.onBackground.ink });

    // the think ring (R-07: the think time is a visual ring only, with no riser)
    /** @type {any} */
    let ring = null;
    if (plan.ring) {
      const mid = L.ring / 2;
      const r = mid - RING_STROKE; // the stroke's centre line, so the ring stays inside its box
      const track = svg('circle', {
        cx: String(mid), cy: String(mid), r: String(r), class: 'dyn-ck-ring-track',
        fill: 'none', stroke: ctx.onBackground.ink2, 'stroke-width': String(RING_STROKE), 'stroke-opacity': '0.35',
      });
      const arc = svg('circle', {
        cx: String(mid), cy: String(mid), r: String(r), class: 'dyn-ck-ring-arc', fill: 'none',
        stroke: ctx.roles.color('struct.accent'), 'stroke-width': String(RING_STROKE), 'stroke-linecap': 'round',
      });
      const face = svg('svg', {
        class: 'dyn-ck-ring-svg', width: String(L.ring), height: String(L.ring), 'aria-hidden': 'true',
      }, track, arc);
      face.setAttribute('viewBox', `0 0 ${L.ring} ${L.ring}`);
      // the arc starts at 12 o'clock: a quarter turn, set once (never painted)
      Object.assign(face.style, { display: 'block', transform: 'rotate(-90deg)' });
      const num = h('div', {
        class: 'dyn-ck-ring-num',
        style: {
          position: 'absolute', left: '0px', top: '0px', width: `${L.ring}px`, height: `${L.ring}px`,
          display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'var(--dyn-font-body)',
          fontSize: `${L.ringNum}px`, fontWeight: '700', fontVariantNumeric: 'tabular-nums', lineHeight: '1',
          color: ctx.onBackground.ink,
        },
      });
      const box = h('div', {
        class: 'dyn-ck-ring',
        style: { position: 'absolute', left: '0px', top: '0px', width: `${L.ring}px`, height: `${L.ring}px` },
      }, face, num);
      const holder = group('dyn-ck-ring-group', box);
      holder.style.position = 'absolute';
      root.appendChild(holder);
      ring = { box, holder, arc, num, length: 2 * Math.PI * r, alpha: 0, text: '' };
    }

    // one tile per option, on `surface`: the letter (mcq), the text, and the `done` state's ok check and ok border
    const options = data.options.map((text, i) => {
      const lh = lineHeight('body', text);
      const body = h('div', {
        class: 'dyn-ck-o', dir: 'auto',
        style: { position: 'absolute', lineHeight: String(lh), textAlign: letters ? 'start' : 'center', overflowWrap: 'anywhere' },
      });
      const { words, tex } = richWords(ctx, body, text);
      const letter = letters
        ? h('div', {
          class: 'dyn-ck-o-letter', text: optionLetter(i),
          style: {
            position: 'absolute', width: `${L.badge}px`, height: `${L.badge}px`, display: 'flex',
            alignItems: 'center', justifyContent: 'center', fontFamily: 'var(--dyn-font-body)',
            fontSize: `${TYPE_FLOORS[format].label_min}px`, fontWeight: '700', lineHeight: '1',
            borderRadius: `${RADIUS.chip}px`, color: ink2,
          },
        })
        : null;
      // the card carries the weight and ink the state paints, so the text inherits both (plan §1.5)
      const card = h('div', {
        class: 'dyn-ck-tile', dataset: { option: String(i) },
        style: {
          position: 'absolute', boxSizing: 'border-box', borderRadius: `${RADIUS.card}px`, background: surface,
          fontFamily: 'var(--dyn-font-body)', fontWeight: String(L.option.weight), color: ink,
        },
      }, letter, body);
      const parts = itemStateParts(card);
      const holder = group('dyn-ck-tile-group', card);
      holder.style.position = 'absolute';
      root.appendChild(holder);
      return {
        i, text, card, body, letter, parts, holder, words, tex, lh,
        correct: i === data.correct, enter: plan.options[i] || 0,
        size: L.option.max, min: L.option.min, overflow: 0, alpha: 0,
      };
    });

    // the explanation line: the quiz's explanation, or the board form's revealed answer (which takes the `done`
    // state itself, there being no correct tile to mark)
    /** @type {any} */
    let explain = null;
    if (data.explanation && plan.revealAt !== null) {
      explain = onBg('dyn-ck-x', 'explain', data.explanation,
        { weight: L.explain.weight, align: 'center', face: 'body', role: 'body', colour: ctx.onBackground.ink });
    }

    ctx.root.appendChild(root);
    return {
      root, head, question, ring, options, explain, plan, letters, surface, ink, ink2,
      kind: kindOf(scene), item: data.item, label: data.label,
    };
  },

  /**
   * Measure and fit (runs again when a late face arrives; idempotent): the label in its line, then the question,
   * every option at one common size and the explanation, each in its own box; the block is then shrunk step by step
   * (question, options, explanation, each to its slot minimum) until it fits the box, and what is still too tall is
   * reported as overflow rather than clipped. Positions are set here, once; `update` only paints.
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

    // --- the label and the ring (the top band; neither moves) --------------------------------------
    if (v.head) {
      const hd = v.head;
      const range = slotRange(ctx, 'label', { min: L.label.min, max: L.label.max, role: 'label' });
      Object.assign(hd.holder.style, {
        left: `${b.label.x0}px`, top: `${b.label.y0}px`, width: `${b.label.x1 - b.label.x0}px`,
      });
      if (hd.mask) Object.assign(hd.el.style, { position: 'relative', left: '0px' });
      const fit = fitLines(hd.el, {
        max: range.max, min: range.min, lines: L.label.lines, prefer: L.label.prefer, lh: hd.lh, step: SIZE_STEP,
      }, hd.words, hd.tex);
      hd.size = fit.size;
      hd.min = range.min;
      hd.overflow = fit.overflow;
    }
    if (v.ring) place(v.ring.holder, b.ring);

    // --- the block: the question, the tiles and the explanation ------------------------------------
    const wide = b.block.x1 - b.block.x0;
    const tw = tileWidth(format, zones);
    const textW = tileTextWidth(format, zones);
    const qRange = slotRange(ctx, 'question', { min: L.question.min, max: L.question.max, role: 'title' });
    const oRange = slotRange(ctx, 'option', { min: L.option.min, max: L.option.max, role: 'body' });
    const xRange = slotRange(ctx, 'explain', { min: L.explain.min, max: L.explain.max, role: 'body' });

    const q = v.question;
    Object.assign(q.holder.style, { left: `${b.block.x0}px`, width: `${wide}px` });
    if (q.mask) Object.assign(q.el.style, { position: 'relative', left: '0px', width: `${wide}px` });
    for (const o of v.options) {
      Object.assign(o.card.style, { left: '0px', top: '0px', width: `${tw}px` });
      Object.assign(o.body.style, { width: `${textW}px` });
    }
    if (v.explain) {
      Object.assign(v.explain.holder.style, { left: `${b.block.x0}px`, width: `${wide}px` });
      if (v.explain.mask) Object.assign(v.explain.el.style, { position: 'relative', left: '0px', width: `${wide}px` });
    }

    const qFit = fitLines(q.el, {
      max: qRange.max, min: qRange.min, lines: L.question.lines, prefer: L.question.prefer, lh: q.lh, step: SIZE_STEP,
    }, q.words, q.tex);
    /** @type {{size: number, overflow: number}[]} */
    const oFits = v.options.map((/** @type {any} */ o) => fitLines(o.body, {
      max: oRange.max, min: oRange.min, lines: L.option.lines, prefer: L.option.prefer, lh: o.lh, step: SIZE_STEP,
    }, o.words, o.tex));
    const xFit = v.explain
      ? fitLines(v.explain.el, {
        max: xRange.max, min: xRange.min, lines: L.explain.lines, prefer: L.explain.prefer, lh: v.explain.lh,
        step: SIZE_STEP,
      }, v.explain.words, v.explain.tex)
      : { size: xRange.max, overflow: 0 };

    let qSize = qFit.size;
    let oSize = oFits.length ? Math.min(...oFits.map((f) => f.size)) : oRange.max;
    let xSize = xFit.size;
    const rows = tileRows(v.options.length, L.perRow);
    const boxH = Math.max(0, b.block.y1 - b.block.y0);

    /** The heights of the block's parts at the sizes set now (read from the laid-out page). */
    const measure = () => {
      q.el.style.fontSize = `${qSize}px`;
      const qH = Math.max(Math.round(qSize * q.lh), (q.mask || q.el).offsetHeight);
      let textH = 0;
      for (const o of v.options) {
        // the tile's own size too: the `done` state's check icon is 1 em of it (dynamic.css `.dyn-state-check`), so
        // it stays inside the right-hand column the text box leaves for it
        o.card.style.fontSize = `${oSize}px`;
        o.body.style.fontSize = `${oSize}px`;
        textH = Math.max(textH, Math.round(oSize * o.lh), o.body.offsetHeight);
      }
      const tileH = v.options.length ? 2 * L.pad + Math.max(textH, L.badge) : 0;
      const tilesH = rows.length ? rows.length * tileH + (rows.length - 1) * L.tileGap : 0;
      let xH = 0;
      if (v.explain) {
        v.explain.el.style.fontSize = `${xSize}px`;
        xH = Math.max(Math.round(xSize * v.explain.lh), (v.explain.mask || v.explain.el).offsetHeight);
      }
      const total = qH + (tilesH ? L.gap + tilesH : 0) + (xH ? L.gap + xH : 0);
      return { qH, textH, tileH, tilesH, xH, total };
    };

    let m = measure();
    // shrink in one fixed order - the question, then the options, then the explanation - so the result is a function
    // of the laid-out page alone (plan §P: never of paint order), and stop at every slot's minimum
    for (let guard = 0; m.total > boxH && guard < 64; guard++) {
      if (qSize > qRange.min) qSize = Math.max(qRange.min, qSize - SIZE_STEP);
      else if (oSize > oRange.min) oSize = Math.max(oRange.min, oSize - SIZE_STEP);
      else if (v.explain && xSize > xRange.min) xSize = Math.max(xRange.min, xSize - SIZE_STEP);
      else break;
      m = measure();
    }
    const excess = Math.max(0, m.total - boxH);

    q.size = qSize;
    q.min = qRange.min;
    q.overflow = Math.max(qFit.overflow, excess);
    v.options.forEach((/** @type {any} */ o, k) => {
      o.size = oSize;
      o.min = oRange.min;
      o.overflow = oFits[k].overflow;
    });
    if (v.explain) {
      v.explain.size = xSize;
      v.explain.min = xRange.min;
      v.explain.overflow = xFit.overflow;
    }

    // --- place the block, centred in what the band left ------------------------------------------
    let y = b.block.y0 + Math.max(0, Math.round((boxH - m.total) / 2));
    q.holder.style.top = `${y}px`;
    y += m.qH;
    if (v.options.length) {
      y += L.gap;
      const inner = m.tileH - 2 * L.pad;
      rows.forEach((row, r) => {
        const rowW = row.length * tw + (row.length - 1) * L.tileGap;
        const x0 = b.block.x0 + Math.max(0, Math.round((wide - rowW) / 2));
        const top = y + r * (m.tileH + L.tileGap);
        row.forEach((index, c) => {
          const o = v.options[index];
          const x = x0 + c * (tw + L.tileGap);
          place(o.holder, { x0: x, y0: top, x1: x + tw, y1: top + m.tileH });
          o.card.style.height = `${m.tileH}px`;
          if (o.letter) {
            Object.assign(o.letter.style, {
              left: `${L.pad}px`, top: `${L.pad + Math.max(0, Math.round((inner - L.badge) / 2))}px`,
            });
          }
          const tx = o.letter ? L.pad + L.badge + L.gap : Math.round((tw - textW) / 2);
          Object.assign(o.body.style, {
            left: `${tx}px`, top: `${L.pad + Math.max(0, Math.round((inner - m.textH) / 2))}px`,
          });
        });
      });
      y += m.tilesH;
    }
    if (v.explain) {
      y += L.gap;
      v.explain.holder.style.top = `${y}px`;
    }
  },

  /**
   * Paint scene time t: the label, the question and its words, the think ring, each option's staggered entrance, the
   * reveal (the correct tile's `done` state, the others dimmed to 40 %) and the explanation line. A pure function of
   * t: every property painted on an element is painted on every frame, with a value (never '').
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
    const L = LAYOUT[ctx.format];
    const timing = v.plan;

    if (v.head) {
      const e = enterAt(ctx, t, timing.label, LABEL_ENTER);
      v.head.alpha = e;
      paintTextEntrance(ctx, v.head.el, v.head.mask, e);
    }

    // the question's words land with the narration, in the personality's text entrance (§4.3 gives a check's question
    // no card of its own, so there is no block to rise: only its words enter)
    const q = v.question;
    const eText = enterAt(ctx, t, timing.enter + seconds(ctx, TEXT_DELAY), TEXT_ENTER);
    q.alpha = eText;
    paintTextEntrance(ctx, q.el, q.mask, eText);

    if (v.ring) {
      const ring = v.ring;
      const start = timing.ring.start;
      const secs = Math.max(0.001, timing.ring.seconds);
      const shown = enterAt(ctx, t, start, RING_IN)
        * (1 - ease('ease.exit', progress(t, start + secs, seconds(ctx, RING_OUT))));
      // linear, with no riser (§4.3): the arc empties as the think time runs out
      const left = 1 - progress(t, start, secs);
      const dash = strokeDash(ring.length, left);
      ring.alpha = shown;
      ring.text = shown > 0.001 ? String(Math.max(0, Math.ceil(secs * left - 1e-6))) : '';
      p.style(ring.box, 'opacity', fx(shown));
      p.style(ring.box, 'visibility', shown > 0.001 ? 'visible' : 'hidden');
      p.attr(ring.arc, 'stroke-dasharray', dash.dasharray);
      p.attr(ring.arc, 'stroke-dashoffset', dash.dashoffset);
      p.text(ring.num, ring.text);
    }

    const revealAt = timing.revealAt;
    const dim = revealAt === null ? 0 : ease('ease.enter', progress(t, revealAt, seconds(ctx, DIM_SECONDS)));
    const pop = revealAt === null ? 0 : ease('pop', progress(t, revealAt, seconds(ctx, CHECK_POP)));
    for (const o of v.options) {
      const e = enterAt(ctx, t, o.enter, OPTION_ENTER);
      // §4.3: the correct tile turns `ok`; the others dim to 40 % and stay on screen (nothing is faded out to make
      // room, plan §1.4 invariant 2)
      const faded = o.correct ? 1 : 1 - (1 - OPTION_DIM) * dim;
      const alpha = e * faded;
      o.alpha = alpha;
      const rise = ctx.reducedMotion || e >= 1 ? 0 : Math.round((1 - e) * CARD_RISE);
      p.style(o.card, 'opacity', fx(alpha));
      p.style(o.card, 'visibility', alpha > 0.001 ? 'visible' : 'hidden');
      p.style(o.card, 'transform', rise ? `translate(0, ${rise}px)` : 'none');
      paintItemState(ctx, o.card, o.parts, o.correct && pop > 0 ? 'done' : null, pop,
        { weight: L.option.weight, ink: v.ink });
    }

    if (v.explain) {
      // after the dim, but never so late that the scene ends before the line is up (nothing the scene names is left
      // unshown, plan §1.4 invariant 2): a short scene shows it at the reveal itself
      const duration = Math.max(0, Number(ctx.scene.duration) || 0);
      const reveal = /** @type {number} */ (timing.explainAt);
      const at = Math.max(reveal, Math.min(reveal + seconds(ctx, EXPLAIN_DELAY),
        duration - seconds(ctx, EXPLAIN_ENTER)));
      const e = enterAt(ctx, t, at, EXPLAIN_ENTER);
      v.explain.alpha = e;
      paintRise(ctx, v.explain.inner, e, EXPLAIN_RISE);
      p.style(v.explain.inner, 'visibility', e > 0.001 ? 'visible' : 'hidden');
    }
  },

  /**
   * Where a look_here points (plan §1.4, §1.5, §1.7): an option tile (`option:<i>`) - a shape, so cues.js draws its
   * outline whole just outside the tile, never across its text - the board form's question item, or the label's text.
   * Its box as painted now (read after `update`) on whole stage px, with the dim group holding it. Null for anything
   * else or off the page. (`check` consumes no look_here today: R-16's hold is the ring itself. This keeps the
   * contract complete.)
   * @param {TemplateContext} ctx
   * @param {string} target
   * @returns {FocusRect | null}
   */
  focusRect(ctx, target) {
    if (isFallback(ctx)) return typeof boardGeneric.focusRect === 'function' ? boardGeneric.focusRect(ctx, target) : null;
    const v = ctx.view;
    const text = String(target || '');
    const option = /^option:(0|[1-9][0-9]*)$/.exec(text);
    const id = text.split(':')[0];
    const tile = option ? v.options.find((/** @type {any} */ o) => o.i === Number(option[1])) : null;
    const asked = !option && v.item && v.item.id === id;
    const head = !option && v.head && v.label && v.label.item && v.label.item.id === id ? v.head : null;
    const el = tile ? tile.card : asked ? v.question.el : head ? head.el : null;
    const holder = tile ? tile.holder : asked ? v.question.holder : head ? head.holder : null;
    const r = el ? ctx.rectOf(el) : null;
    if (!r || !holder) return null;
    const x0 = Math.round(r.x);
    const y0 = Math.round(r.y);
    const x1 = Math.round(r.x + r.w);
    const y1 = Math.round(r.y + r.h);
    return {
      rect: { x: x0, y: y0, w: x1 - x0, h: y1 - y0 }, shape: 'rect', radius: tile ? RADIUS.card : 0, el: holder,
      kind: tile ? 'shape' : 'text',
    };
  },

  /**
   * The elements holding an item's text, where cue.term would find its [[term]] spans (`.rt-k`, `_shared.js`
   * `richWords`): the board form's question and then its revealed answer, in the item's field order (text,
   * justification), so `<item>:term:<n>` counts over both; the label's text. Null for anything else (a quiz scene
   * has no board item to name, and board_generic's answer for a scene it draws).
   * @param {TemplateContext} ctx
   * @param {string} itemId
   * @returns {Element | Element[] | null}
   */
  itemEl(ctx, itemId) {
    if (isFallback(ctx)) return typeof boardGeneric.itemEl === 'function' ? boardGeneric.itemEl(ctx, itemId) : null;
    const v = ctx.view;
    if (v.item && v.item.id === itemId) {
      return v.explain ? [v.question.el, v.explain.el] : v.question.el;
    }
    return v.head && v.label && v.label.item && v.label.item.id === itemId ? v.head.el : null;
  },

  /**
   * The content groups a look_here dims (all but the target's): the label, the question, the ring, every tile and the
   * explanation; the same elements at every t.
   * @param {TemplateContext} ctx
   * @returns {Element[]}
   */
  dimGroups(ctx) {
    if (isFallback(ctx)) return typeof boardGeneric.dimGroups === 'function' ? boardGeneric.dimGroups(ctx) : [];
    const v = ctx.view;
    return [v.head ? v.head.holder : null, v.question.holder, v.ring ? v.ring.holder : null,
      ...v.options.map((/** @type {any} */ o) => o.holder), v.explain ? v.explain.holder : null].filter(Boolean);
  },

  /**
   * The morph anchor (plan §1.4): none (a check shares no visual with its neighbours; B2f pairs templates that do).
   * @param {TemplateContext} _ctx
   * @returns {null}
   */
  anchor(_ctx) {
    return null;
  },

  /**
   * Static: this template shows no picture or clip (§4.3 gives a check none), so no slot has a media box; a scene it
   * hands to board_generic keeps board_generic's.
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
    return null;
  },

  /**
   * Static: every TeX string the template renders for the scene (`texOf`; the same in both formats, but what it draws
   * is not: a scene with more options than the format takes is board_generic's).
   * @param {TimedScene} scene
   * @param {import('../../shared/types.js').ResolvedLook | null} look
   * @param {'landscape' | 'portrait'} fmt
   */
  tex(scene, look, fmt) {
    if (!drawsScene(scene, fmt)) return typeof boardGeneric.tex === 'function' ? boardGeneric.tex(scene, look, fmt) : [];
    return texOf(scene);
  },

  /**
   * The text on screen (preflight, §7.12): the label (slot `label`), the question (`question`), every option
   * (`option`), the explanation (`explain`) and the ring's number (`ring`, not critical) - the options on the tile's
   * `surface`, the rest on the background or on its R-53 scrim, in the ink, size and weight painted now.
   * @param {TemplateContext} ctx
   * @returns {TextEntry[]}
   */
  texts(ctx) {
    if (isFallback(ctx)) return typeof boardGeneric.texts === 'function' ? boardGeneric.texts(ctx) : [];
    const v = ctx.view;
    const L = LAYOUT[ctx.format];
    /** @type {TextEntry[]} */
    const out = [];
    /** @param {any} part @param {string} slot @param {string} role @param {string} ink @param {number} weight */
    const onBg = (part, slot, role, ink, weight) => {
      const bg = part.scrim ? scrimmedBg(part.scrim, ctx.onBackground, ink) : ctx.onBackground.bg;
      out.push({
        el: part.el, role, slot, critical: true, ink, bg, alpha: part.alpha, overflow: part.overflow,
        size: part.size, min: part.min, weight, text: plainText(String(part.text || '')),
      });
    };
    if (v.head) onBg(v.head, 'label', 'label', ctx.onBackground.ink2, L.label.weight);
    onBg(v.question, 'question', 'title', ctx.onBackground.ink, L.question.weight);
    if (v.ring && v.ring.text) {
      out.push({
        el: v.ring.num, role: 'label', slot: 'ring', critical: false, ink: ctx.onBackground.ink,
        bg: ctx.onBackground.bg, alpha: v.ring.alpha, size: L.ringNum, min: L.ringNum, weight: 700,
        text: v.ring.text,
      });
    }
    for (const o of v.options) {
      out.push({
        el: o.body, role: 'body', slot: 'option', critical: true, ink: v.ink, bg: v.surface, alpha: o.alpha,
        overflow: o.overflow, size: o.size, min: o.min, weight: L.option.weight, text: plainText(String(o.text || '')),
      });
    }
    if (v.explain) onBg(v.explain, 'explain', 'body', ctx.onBackground.ink, L.explain.weight);
    return out;
  },
};

/** @param {TemplateContext} ctx */
const isFallback = (ctx) => !!(ctx.view && ctx.view.ckFallback);
