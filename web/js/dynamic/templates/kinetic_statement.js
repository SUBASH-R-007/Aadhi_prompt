// @ts-check
/**
 * `kinetic_statement` (DYNAMIC_VIDEO_DESIGN §4.3 B, R1, class `statement`; B2 plan §1.4 the template contract, §1.5 the
 * cues, §1.11 the slots): one principle set as typography, landing phrase by phrase on the narration's own words.
 *
 * Data (§4.3, templates.py `_fit_kinetic_statement`): a BoardScene whose board is **one** `takeaway`, `paragraph`,
 * `heading` or `callout_*` item (§4.4's statement row), or one `quote` (the quote row names this template as
 * `quote_card`'s alternative), with text. §4.3 gives <= 12 words (<= 8 in 9:16); Python scores a longer statement
 * FIT_STRETCHED up to MAX_WORDS, the most this page sets in its lines at the slot's minimum (measured with textfit on
 * the vendored faces: 19-22 words of ordinary English fill four 100 px lines of the 16:9 column, 9-11 words four
 * 88 px lines of the 9:16 one), and past MAX_WORDS the scene is `board_generic`'s whole, so a long statement never
 * blocks a render with "Shorten text" (the B2 review's F3 lesson, media_split's MEDIA_SPLIT_PHRASE_WORDS). Typography
 * only: a scene with any other item, a shown side panel or a medium of its own is handed to `board_generic` whole,
 * every hook with it (`drawsScene`, list_build's convention), and `_fit_kinetic_statement` scores every such scene 0
 * (B2c review CG-2: the fit refuses what the page refuses, or the director picks a template the page then hands on).
 * Nothing an item carries is dropped: a callout keeps its kind as a small label above the statement (board_generic's
 * own wording: "Info", "Tip", "Watch out"), and a quote keeps its `caption` (who said it) as an attribution line below.
 *
 * The R-25 exception (§4.3, R-31): the on-screen text IS the caption. The narration says the statement, the words land
 * as they are spoken, and the caption band is blank meanwhile - kinetic.py's blanking (B2d), not this template's.
 *
 * 16:9 (§4.3: 100-140 px, 2-4 lines, left-aligned): the statement is set in the display face at 700, in grid columns
 * 1-10 (x 96-1532: a ten-column measure keeps a short principle on two or more lines at 140 px), the stack centred
 * vertically between the header row and the bottom of the critical box.
 *
 * 9:16 (§4.3 "the hook band and stage"; §2.3 the reel's Hook act: "aadhi_intro, then a 3-5 s promise
 * (`kinetic_statement`)"): the statement is set in the hook band's column at R-29's hook sizes (88-104 px), centred on
 * the hook band (x 192-888, y 340-760) - the place the reel's hook headline just held, so the promise lands where the
 * eye already is - and a statement taller than the band grows evenly into the stage, inside the upper critical box
 * (the catalogue's `text-above` region, y 288-840; R-59). When such a scene opens the reel (`setsAtFrame0`), frame 0
 * is the feed cover (R-03, §3.3 frame0_hook): the statement is set whole and lit from frame 0 there instead of landing.
 *
 * Choreography (§4.3, §4.8; every template duration is scaled by the personality, `_shared.js seconds` / `enterAt`):
 * - The statement is split into **phrases** (`phrasesOf`: at the punctuation that ends a clause, and at most
 *   PHRASE_MAX_WORDS words each, split evenly). Each phrase lands with the personality's text entrance
 *   (`ctx.motion.entrance`, `_shared.js paintTextEntrance`: Soft's fade + 16 px rise, Crisp's reveal from the baseline
 *   inside a per-word mask) when its first word is SPOKEN: `phraseTimes` aligns the statement's words with the scene's
 *   timed narration words (`TimedBeat.words`, from the item's own entrance on), and a phrase enters REVEAL_LEAD before
 *   its first matched word (R-17). A phrase whose words the narration does not say verbatim (a TeX symbol, a
 *   contraction, a translation) is placed between its neighbours, and with no word timing at all the phrases are spread
 *   over the revealing beat's speech. Every phrase is in before the scene ends.
 * - Completed lines dim to 70% (§4.3; the catalogue's dim alpha): a line dims over LINE_DIM once the next line's first
 *   phrase starts landing, so the line being spoken is the brightest - but not while a term cue the stage draws on it
 *   is still on screen (`termCueEnds`: a marker or underline is drawn, and contrast-checked, at full strength, so its
 *   line waits for it to leave). The dim is the opacity of each word's mask, never of a dim group (the stage owns
 *   those), and the dimmed ink is what preflight checks (`texts`).
 * - A callout's label fades in with the item's entrance; a quote's attribution lands when the narration names it, else
 *   ATTRIBUTION_DELAY after the last phrase.
 * - The keyword takes `cue.term` (cues.js, from `itemEl`): every word goes through `_shared.js richWords`, so the
 *   `[[term]]` `.rt-k` spans are kept, and `<item>` and `<item>:term:<n>` both resolve.
 *
 * The per-word mask (`dyn-ks-mask`) is an inline-block aligned to the line's top, padded by MASK_PAD_EM on every side
 * and pulled back by the same negative margin: its margin box is exactly the word's, so the paragraph wraps and
 * measures exactly as plain text does (textfit.py's model), while the reveal's clip reaches MASK_PAD_EM past the line
 * box (the display line height is tighter than the glyphs' extent, and a clip at the line box would cut descenders
 * and marks). It clips only while a word is being revealed from its baseline; settled text is never clipped.
 *
 * Emphasis is cues.js's (§4.2, plan §1.4/§1.5): `itemEl` hands it the statement (and a quote's attribution, the item's
 * `caption` field, in field order), `focusRect` the statement's box and `dimGroups` the label, the statement and the
 * attribution. Nothing here writes outline, text-decoration, filter, backdrop-filter or will-change, and no structure
 * role colours the text (§4.2).
 *
 * Determinism (§7.4, plan §P): `update` is a pure function of t - every phrase time is fixed at build from the scene's
 * data (never from the DOM), the lines at layout from the laid-out page - and every property it paints gets a value on
 * every frame; translates are whole pixels and 2D (`paintTextEntrance`).
 *
 * The sizes, the 16:9 measure, the 9:16 placement, the phrase length and the dim timing are B2 proposals pending the
 * owner's review of the reference frames: aadhi/compose/dynamic/templates.py mirrors LAYOUT as this template's slots
 * (`slotRows`), and a test pins the two together.
 */

import { h } from '../../shared/dom.js';
import { ease } from '../../motion/easing.js';
import { progress } from '../../motion/track.js';
import { composite } from '../../motion/color.js';
import { splitWords } from '../../motion/text.js';
import { mathSpans, parseRich, plainText } from '../../player/richtext.js';
import { LAYOUT_TOKENS, TYPE_FLOORS, fixesOf, fx } from '../core.js';
import { EXIT_FACTOR, TERM_DRAW, cueEvents } from '../cues.js';
import { REVEAL_LEAD, itemTimings } from '../state.js';
import boardGeneric, { LABELS as BOARD_LABELS } from './board_generic.js';
import {
  RADIUS, SPACE, enterAt, gridSpan, group, paintTextEntrance, region, revealMask, richWords, scrimmedBg, seconds,
  slotRange,
} from './_shared.js';

/** @typedef {import('./types.js').TemplateContext} TemplateContext */
/** @typedef {import('./types.js').TemplateModule} TemplateModule */
/** @typedef {import('./types.js').FocusRect} FocusRect */
/** @typedef {import('./types.js').TextEntry} TextEntry */
/** @typedef {import('../../shared/types.js').TimedScene} TimedScene */
/** @typedef {import('../../shared/types.js').BoardItem} BoardItem */
/** @typedef {import('../core.js').Box} Box */
/** @typedef {import('../core.js').Zones} Zones */
/** @typedef {'landscape' | 'portrait'} Format */

export const ID = 'kinetic_statement';

// ---------------------------------------------------------------------------------------------
// Data (§4.3, §4.4; templates.py STATEMENT_KINDS, _fit_kinetic_statement, KINETIC_STATEMENT_WORDS)
// ---------------------------------------------------------------------------------------------

/** The statement row's item kinds (templates.py STATEMENT_KINDS). */
export const STATEMENT_KINDS = Object.freeze(['takeaway', 'paragraph', 'heading', 'callout_info', 'callout_tip',
  'callout_warning']);
/** The quote row's kind: this template is `quote_card`'s alternative (§4.4). */
export const QUOTE_KIND = 'quote';
/** §4.3's limit: <= 12 words (<= 8 in 9:16). Past it, Python scores the scene FIT_STRETCHED. */
export const DESIGN_WORDS = Object.freeze({ landscape: 12, portrait: 8 });
/**
 * The most words this page sets (templates.py KINETIC_STATEMENT_WORDS, pinned equal by a test): measured with textfit
 * on the vendored faces, ordinary English statements of 19-22 words fit the 16:9 statement's four lines at 100 px and
 * of 9-11 words the 9:16 one's four lines at 88 px, so the caps sit at or below the worst case. A longer statement is
 * board_generic's whole (`drawsScene`), as `_fit_kinetic_statement` scores it 0.
 */
export const MAX_WORDS = Object.freeze({ landscape: 18, portrait: 9 });

/** A callout's label above its statement: board_generic's wording for the same kinds (one vocabulary, B4 i18n). */
export const LABELS = Object.freeze({
  callout_info: BOARD_LABELS.info,
  callout_tip: BOARD_LABELS.tip,
  callout_warning: BOARD_LABELS.warning,
});
/** What precedes a quote's attribution (the slot measures it with the caption). */
export const ATTRIBUTION_PREFIX = '— ';

// ---------------------------------------------------------------------------------------------
// Motion (§4.3 kinetic_statement, §4.8), seconds before the personality's scale
// ---------------------------------------------------------------------------------------------

/** §4.8: a text entrance takes 400-600 ms (the pilots' 450 ms). */
export const PHRASE_ENTER = 0.45;
/** A phrase is at most this many words (a longer clause is split evenly). */
export const PHRASE_MAX_WORDS = 3;
/** How far ahead of the last matched narration word the next statement word is looked for. */
export const MATCH_WINDOW = 8;
/** The spacing of phrases the narration's timing cannot place (seconds; speech time, so never scaled). */
export const FALLBACK_GAP = 0.3;
/** Every phrase starts at least this long before its entrance would run past the scene's end. */
export const SETTLE_MARGIN = 0.1;
/** A callout's label fades in with the item's entrance. */
export const LABEL_ENTER = 0.35;
/** A quote's attribution follows the last phrase by this much when the narration does not name it. */
export const ATTRIBUTION_DELAY = 0.15;
/** §4.3 "completed lines dim to 70%": the dim's ramp (>= R-54's 300 ms at Crisp's x0.85) and its alpha. */
export const LINE_DIM = 0.4;
export const DIM_ALPHA = Number(LAYOUT_TOKENS.alpha.dim);
/** The per-word mask's padding past the word's box on every side, pulled back by a negative margin (em). */
export const MASK_PAD_EM = 0.3;

// ---------------------------------------------------------------------------------------------
// Layout (stage px; the catalogue's regions, grid, type floors and layout tokens)
// ---------------------------------------------------------------------------------------------

/** Line heights of the type scale (catalogue `type.scale`; plan §2: Indic display >= 1.25, Indic body >= 1.45). */
export const LINE_HEIGHT = Object.freeze({
  display: Object.freeze({ latin: 1.05, indic: 1.25 }),
  body: Object.freeze({ latin: 1.35, indic: 1.45 }),
});
/** The display face's tracking (§4.6 type scale display -0.02..-0.01; hook_question's and the pilots' value). */
export const TRACKING_EM = -0.015;
/** Size steps when fitting (board_generic's and textfit.py's 4 px). */
export const SIZE_STEP = 4;
/** 16:9: the statement's measure, grid columns 1-10 (0-based, inclusive). */
export const COLUMNS = Object.freeze([0, 9]);

/**
 * Per-format layout. `statement`: §4.3's 100-140 px over at most 4 lines in 16:9; R-29's 9:16 `hook` range (88-104 px)
 * over at most 4 lines in the hook band's column (four lines fit the 552 px `text-above` box at every size, Indic line
 * heights included, so Python's line-count fit and the page's height check never disagree at the minimum).
 * `label`: a callout's kind, one line at board_generic's kicker size (R-29's label floor, weight 600: R-29's 9:16
 * weight clause below 64 px). `attribution`: a quote's caption, at most `attributionLines` lines at the label size.
 * `gap`: between the stack's rows (the 8 px spacing scale). At the minimum, four statement lines fit the box beside a
 * label in every script and beside a two-line attribution in Latin; a four-line Indic quote under a two-line Indic
 * caption is past the 9:16 box by about 40 px, which the page reports (preflight: "Shorten text") rather than clips.
 */
export const LAYOUT = Object.freeze({
  landscape: Object.freeze({
    statement: Object.freeze({ max: 140, min: 100, lines: 4 }),
    label: 40,
    attribution: 48,
    attributionLines: 2,
    gap: SPACE[4], // 40
  }),
  portrait: Object.freeze({
    statement: Object.freeze({ max: 104, min: 88, lines: 4 }),
    label: 44,
    attribution: 44,
    attributionLines: 2,
    gap: SPACE[2], // 24
  }),
});

/**
 * The boxes of a format (pure; whole stage px): `stack`, where the label, the statement and the attribution are set
 * (its width is the slot's), and `band`, what the stack is centred on. 16:9: grid columns 1-10 from below the header
 * row to the bottom of the critical box, centred in itself. 9:16: the `text-above` region (x 192-888, y 288-840: the
 * upper critical box), centred on the `hook` region (y 340-760).
 * @param {Format} format
 * @param {Zones} zones
 * @returns {{stack: Box, band: Box}}
 */
export function boxes(format, zones) {
  const ctx = { format };
  if (format === 'portrait') return { stack: region(ctx, 'text-above'), band: region(ctx, 'hook') };
  const full = region(ctx, 'full');
  const cols = gridSpan(COLUMNS[0], COLUMNS[1]);
  const stack = { x0: cols.x0, y0: Math.max(full.y0, zones.header.y1 + SPACE[2]), x1: cols.x1, y1: full.y1 };
  return { stack, band: stack };
}

/**
 * Where the stack's top goes for a stack `height` px tall (whole px): centred on the band, never above the stack box's
 * top nor below its bottom (a stack taller than the box starts at its top and reports its overflow).
 * @param {{stack: Box, band: Box}} b @param {number} height
 */
export function stackTop(b, height) {
  const centre = (b.band.y0 + b.band.y1) / 2;
  const top = Math.min(Math.round(centre - height / 2), b.stack.y1 - Math.round(height));
  return Math.max(b.stack.y0, top);
}

/**
 * The text slots of this template (templates.py `SlotSpec`, what textfit.py fits in Python; plan §1.6, §1.11):
 * slot -> format -> variant -> spec (no look variants: `default` only). Line heights are the Latin ones (Indic text
 * takes LINE_HEIGHT's Indic values, which textfit.py's Indic measure uses). The attribution's text is
 * ATTRIBUTION_PREFIX + the caption. Pure.
 * @param {Zones} landscape @param {Zones} portrait
 */
export function slotRows(landscape, portrait) {
  /** @type {Record<string, Record<string, Record<string, any>>>} */
  const out = { statement: {}, label: {}, attribution: {} };
  for (const [fmt, zones] of /** @type {const} */ ([['landscape', landscape], ['portrait', portrait]])) {
    const L = LAYOUT[fmt];
    const b = boxes(fmt, zones).stack;
    const w = b.x1 - b.x0;
    out.statement[fmt] = {
      default: {
        w, max_lines: L.statement.lines, size: [L.statement.min, L.statement.max], role: 'title', face: 'display',
        weight: 700, line_height: LINE_HEIGHT.display.latin, tracking_em: TRACKING_EM, prefer_lines: null,
      },
    };
    /** @param {number} size @param {number} lines */
    const label = (size, lines) => ({
      w, max_lines: lines, size: [size, size], role: 'label', face: 'body', weight: 600,
      line_height: LINE_HEIGHT.body.latin, tracking_em: 0, prefer_lines: null,
    });
    out.label[fmt] = { default: label(L.label, 1) };
    out.attribution[fmt] = { default: label(L.attribution, L.attributionLines) };
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
 * @param {'display' | 'body'} role @param {string} text
 */
export function lineHeight(role, text) {
  return INDIC.test(plainText(String(text || ''))) ? LINE_HEIGHT[role].indic : LINE_HEIGHT[role].latin;
}

/** Python's `str.isspace` characters (what `str.split()` / `str.strip()` treat as whitespace), as a class body. */
const PY_SPACE = '\\t\\n\\v\\f\\r\\x1c-\\x1f \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000';
const PY_SPACES = new RegExp(`[${PY_SPACE}]+`);
const PY_BLANK = new RegExp(`^[${PY_SPACE}]*$`);
/** templates.py `_TEX`: a `$...$` span not opened by a backslash, one word for the count. */
const TEX_SPAN = /(?<!\\)\$[^$]*\$/g;

/**
 * Words as the R-25 lint and templates.py `_words` count them: whitespace-delimited (Python's whitespace), a `$TeX$`
 * span one word, the rich-lite markup counted as written. Pure.
 * @param {string | null | undefined} text
 */
export function countWords(text) {
  if (!text) return 0;
  return String(text).replace(TEX_SPAN, ' x ').split(PY_SPACES).filter((w) => w.length > 0).length;
}

/**
 * Whether `text` has something besides Python's whitespace (`str.strip()` non-empty). Pure.
 * @param {unknown} text
 */
export function hasText(text) {
  return typeof text === 'string' && !PY_BLANK.test(text);
}

/**
 * The scene's statement: its board's one item when that item is a statement kind or a quote with text (null
 * otherwise; a second item, of any kind, makes it board_generic's: `_fit_kinetic_statement` counts every item).
 * @param {TimedScene} scene
 * @returns {BoardItem | null}
 */
export function statementOf(scene) {
  const board = scene.board || [];
  if (board.length !== 1) return null;
  const item = board[0];
  const kind = String(item && item.kind);
  if (!STATEMENT_KINDS.includes(kind) && kind !== QUOTE_KIND) return null;
  return hasText(item.text) ? item : null;
}

/**
 * The scene's side panel when the layout shows it (null without one): this template draws none.
 * @param {TimedScene} scene
 */
export function shownPanel(scene) {
  const sp = scene.side_panel;
  return sp && scene.layout?.show_side_panel !== false ? sp : null;
}

/**
 * Whether this template draws the scene whole in `format` (else board_generic does, every hook handed on;
 * `_fit_kinetic_statement` scores such a scene 0): one statement item with text, at most MAX_WORDS[format] words,
 * no shown side panel and no medium of its own.
 * @param {TimedScene} scene @param {Format | string} [format]
 */
export function drawsScene(scene, format = 'landscape') {
  const item = statementOf(scene);
  if (!item || shownPanel(scene) || scene.media) return false;
  return countWords(item.text) <= MAX_WORDS[format === 'portrait' ? 'portrait' : 'landscape'];
}

/**
 * The quote's attribution (its `caption`), or null for any other kind or a blank caption.
 * @param {BoardItem} item
 */
export function attributionOf(item) {
  return String(item.kind) === QUOTE_KIND && hasText(item.caption) ? String(item.caption) : null;
}

/**
 * Whether the statement is set whole on frame 0 (the reel's feed cover, R-03 / §3.3 frame0_hook): a 9:16 scene that
 * opens the video. Everywhere else its phrases land with the narration.
 * @param {Format} format @param {number} index
 */
export function setsAtFrame0(format, index) {
  return format === 'portrait' && index === 0;
}

/**
 * Every TeX string the template renders for the scene (the static `tex` hook; render.js converts them before `ready`):
 * the `$...$` spans of the statement and of a quote's attribution, inline (`richWords` mounts each with display off).
 * @param {TimedScene} scene
 * @returns {{tex: string, display: boolean}[]}
 */
export function texOf(scene) {
  const item = statementOf(scene);
  if (!item) return [];
  /** @type {{tex: string, display: boolean}[]} */
  const out = [];
  for (const text of [item.text, attributionOf(item)]) {
    if (!text) continue;
    for (const tex of mathSpans(String(text))) if (tex.trim()) out.push({ tex, display: false });
  }
  return out;
}

/** Stands for one TeX or code span (richWords' own placeholder): never spoken, so it matches no narration word. */
const OBJECT_CHAR = '￼';

/**
 * The visible words of rich-lite `text`, exactly as `_shared.js richWords` splits them (the same parse, the same
 * `splitWords` on the same plain text, a TeX or code span one OBJECT_CHAR), but from the data alone - never from the
 * DOM, whose TeX arrives later - so the phrase times are a function of the scene. Pure.
 * @param {string} text @param {string} [locale]
 * @returns {string[]}
 */
export function wordTexts(text, locale) {
  let plain = '';
  /** @param {import('../../player/richtext.js').RichNode[]} nodes */
  const walk = (nodes) => {
    for (const n of nodes) {
      if (n.t === 'text') plain += n.v;
      else if (n.t === 'code' || n.t === 'math') plain += OBJECT_CHAR;
      else walk(n.c);
    }
  };
  walk(parseRich(String(text || '')));
  return splitWords(plain, locale).map((p) => p.text);
}

/**
 * A word as the alignment compares it: NFKC, lower case, letters, marks and digits only ('' for a word with none:
 * TeX, code, punctuation). Pure.
 * @param {string} word
 */
export function normWord(word) {
  return String(word || '').normalize('NFKC').toLowerCase().replace(/[^\p{L}\p{M}\p{N}]+/gu, '');
}

/** A clause ends after a word ending in one of these (then any closing quote or bracket): a phrase boundary. */
const CLAUSE_END = /[,;:.!?…–—।॥、。，！？；：]["'”’»)\]]*$/u;

/**
 * The statement's phrases, as lists of word indices: split after every word that ends a clause (CLAUSE_END), then
 * each clause longer than PHRASE_MAX_WORDS into the fewest even parts (the earlier parts one word longer). Pure.
 * @param {string[]} words
 * @returns {number[][]}
 */
export function phrasesOf(words) {
  /** @type {number[][]} */
  const clauses = [];
  /** @type {number[]} */
  let cur = [];
  words.forEach((w, i) => {
    cur.push(i);
    if (CLAUSE_END.test(w)) {
      clauses.push(cur);
      cur = [];
    }
  });
  if (cur.length) clauses.push(cur);
  /** @type {number[][]} */
  const out = [];
  for (const c of clauses) {
    const parts = Math.ceil(c.length / PHRASE_MAX_WORDS);
    const base = Math.floor(c.length / parts);
    const extra = c.length % parts;
    let k = 0;
    for (let p = 0; p < parts; p++) {
      const size = base + (p < extra ? 1 : 0);
      out.push(c.slice(k, k + size));
      k += size;
    }
  }
  return out;
}

/**
 * The scene's narration words from `from` on (scene-relative seconds), in spoken order. Pure.
 * @param {TimedScene} scene @param {number} from
 * @returns {{text: string, start: number}[]}
 */
export function spokenWords(scene, from) {
  /** @type {{text: string, start: number, order: number}[]} */
  const out = [];
  for (const beat of scene.beats || []) {
    for (const w of beat.words || []) {
      const start = Number(w && w.start);
      if (Number.isFinite(start) && start >= from - 1e-6) out.push({ text: String(w.text || ''), start, order: out.length });
    }
  }
  out.sort((a, b) => a.start - b.start || a.order - b.order);
  return out.map(({ text, start }) => ({ text, start }));
}

/**
 * When each word of `tokens` (normWord; '' never matches) is spoken: each is looked for among the next MATCH_WINDOW
 * narration words after the last match, in order (a word the narration skips or says differently stays null, and the
 * search does not move). Pure.
 * @param {string[]} tokens @param {{text: string, start: number}[]} spoken
 * @returns {(number | null)[]}
 */
export function alignWords(tokens, spoken) {
  const said = spoken.map((w) => normWord(w.text));
  /** @type {(number | null)[]} */
  const out = [];
  let p = 0;
  for (const tok of tokens) {
    let hit = -1;
    if (tok) {
      for (let j = p; j < Math.min(said.length, p + MATCH_WINDOW); j++) {
        if (said[j] === tok) {
          hit = j;
          break;
        }
      }
    }
    if (hit >= 0) {
      out.push(spoken[hit].start);
      p = hit + 1;
    } else {
      out.push(null);
    }
  }
  return out;
}

/**
 * When each phrase starts landing, and when the attribution does (scene-relative seconds; pure): REVEAL_LEAD before
 * the phrase's first matched word (R-17), never before the item's entrance (`enter`), never so late that its entrance
 * (`enterDur`) would not finish SETTLE_MARGIN before the scene's end, and never before the phrase ahead of it. A
 * phrase with no matched word is placed between its matched neighbours by word position (or FALLBACK_GAP from the one
 * it has); with no match at all the phrases are spread over the speech of the beat that reveals the item (else the
 * first beat from the entrance on), else FALLBACK_GAP apart from the entrance. The attribution: its own first matched
 * word, else ATTRIBUTION_DELAY after the last phrase is in.
 * @param {TimedScene} scene
 * @param {{id: string}} item
 * @param {string[]} words        the statement's visible words (wordTexts)
 * @param {number[][]} phrases    phrasesOf(words)
 * @param {string[]} captionWords the attribution's words (wordTexts; [] without one)
 * @param {number} enter          the item's entrance (state.js itemTimings)
 * @param {number} enterDur       a phrase's entrance, scaled (seconds(ctx, PHRASE_ENTER))
 * @returns {{phrases: number[], attribution: number}}
 */
export function phraseTimes(scene, item, words, phrases, captionWords, enter, enterDur) {
  const spoken = spokenWords(scene, enter);
  const at = alignWords([...words, ...captionWords].map(normWord), spoken);
  const n = phrases.length;
  /** @type {(number | null)[]} */
  const raw = phrases.map((idx) => {
    const hits = /** @type {number[]} */ (idx.map((i) => at[i]).filter((x) => x !== null));
    return hits.length ? Math.min(...hits) - REVEAL_LEAD : null;
  });
  const first = phrases.map((idx) => idx[0]);
  const known = raw.map((x, k) => (x === null ? -1 : k)).filter((k) => k >= 0);
  /** @type {number[]} */
  const out = [];
  if (!known.length) {
    const beats = scene.beats || [];
    const beat = beats.find((b) => b.board_item_id === item.id) || beats.find((b) => Number(b.start) >= enter - 1e-6);
    const total = Math.max(1, words.length);
    for (let k = 0; k < n; k++) {
      if (beat && Number(beat.speech_end) > Number(beat.start)) {
        const a = Number(beat.start);
        out.push(a + (Number(beat.speech_end) - a) * (first[k] / total) - REVEAL_LEAD);
      } else {
        out.push(enter + FALLBACK_GAP * k);
      }
    }
  } else {
    for (let k = 0; k < n; k++) {
      const r = raw[k];
      if (r !== null) {
        out.push(r);
        continue;
      }
      const prev = [...known].reverse().find((j) => j < k);
      const next = known.find((j) => j > k);
      if (prev !== undefined && next !== undefined) {
        const a = /** @type {number} */ (raw[prev]);
        const b = /** @type {number} */ (raw[next]);
        out.push(a + (b - a) * ((first[k] - first[prev]) / Math.max(1, first[next] - first[prev])));
      } else if (prev !== undefined) {
        out.push(/** @type {number} */ (raw[prev]) + FALLBACK_GAP * (k - prev));
      } else {
        out.push(/** @type {number} */ (raw[/** @type {number} */ (next)]) - FALLBACK_GAP * (/** @type {number} */ (next) - k));
      }
    }
  }
  const hi = Math.max(enter, Number(scene.duration) - enterDur - SETTLE_MARGIN);
  /** @param {number} t */
  const clamp = (t) => Math.min(hi, Math.max(enter, t));
  for (let k = 0; k < n; k++) out[k] = clamp(Math.max(out[k], k > 0 ? out[k - 1] : -Infinity));
  const last = n ? out[n - 1] : enter;
  const said = /** @type {number[]} */ (at.slice(words.length).filter((x) => x !== null));
  const attribution = clamp(Math.max(last, said.length ? Math.min(...said) - REVEAL_LEAD : last + enterDur + ATTRIBUTION_DELAY));
  return { phrases: out, attribution };
}

/**
 * The line each word mask sits on (0-based), from the laid-out page: a mask whose top is more than half a line below
 * the current line's top starts the next line (a word broken over lines counts on its first). Layout time only.
 * @param {HTMLElement[]} masks @param {number} linePx
 * @returns {number[]}
 */
export function lineIndices(masks, linePx) {
  /** @type {number[]} */
  const out = [];
  let line = 0;
  /** @type {number | null} */
  let top = null;
  for (const m of masks) {
    const y = m.offsetTop;
    if (top === null) top = y;
    else if (y > top + linePx / 2) {
      line++;
      top = y;
    }
    out.push(line);
  }
  return out;
}

/**
 * When each line is completed (pure): when the first word of the line after it starts landing, but never while a
 * term cue on that line is still on screen (`busy[l]`: when the last one has left) - a marker or an underline is
 * drawn at full strength, so its line waits for it; Infinity for the last line (it never dims).
 * @param {number[]} lineOf @param {number[]} wordAt @param {number[]} [busy]
 * @returns {number[]}
 */
export function lineDoneTimes(lineOf, wordAt, busy = []) {
  const lines = lineOf.length ? Math.max(...lineOf) + 1 : 0;
  /** @type {number[]} */
  const out = [];
  for (let l = 0; l < lines; l++) {
    const i = lineOf.findIndex((x) => x > l);
    out.push(i >= 0 ? Math.max(wordAt[i], Number.isFinite(busy[l]) ? busy[l] : -Infinity) : Infinity);
  }
  return out;
}

/**
 * When the term cues the stage draws on the statement have left (scene seconds), per term ordinal of the statement
 * (`data-dyn-term`, `_shared.js richWords`): cues.js `cueEvents` (the events this template's `consumes` lets through)
 * of kind `cue_term` on the item (`<item>`: every term; `<item>:term:<n>`: the n-th, the statement's terms coming first
 * in `itemEl`'s field order), each until its hold ends plus its exit (cues.js: TERM_DRAW x EXIT_FACTOR). Pure.
 * @param {TimedScene} scene @param {string} itemId @param {number} terms   the statement's term count
 * @returns {number[]}
 */
export function termCueEnds(scene, itemId, terms) {
  /** @type {number[]} */
  const out = Array.from({ length: terms }, () => -Infinity);
  for (const c of cueEvents(scene, ID)) {
    if (c.kind !== 'cue_term' || String(c.parsed.item) !== itemId) continue;
    const end = c.hold + TERM_DRAW * EXIT_FACTOR;
    const which = c.parsed.form === 'item:term' ? [Number(c.parsed.index)] : out.map((_, n) => n);
    for (const n of which) if (n >= 0 && n < terms) out[n] = Math.max(out[n], end);
  }
  return out;
}

/**
 * The lines each term of the statement is on (layout time): the lines of the masks holding its `.rt-k` pieces (the
 * piece over the space between two words of a term sits outside both masks and counts on neither).
 * @param {HTMLElement} text @param {HTMLElement[]} masks @param {number[]} lineOf
 * @returns {Map<number, Set<number>>}
 */
export function termLines(text, masks, lineOf) {
  /** @type {Map<number, Set<number>>} */
  const out = new Map();
  for (const span of text.querySelectorAll('.rt-k')) {
    const n = Number(span.getAttribute('data-dyn-term'));
    const mask = span.closest('.dyn-ks-mask');
    const i = mask ? masks.indexOf(/** @type {HTMLElement} */ (mask)) : -1;
    if (!Number.isInteger(n) || i < 0) continue;
    if (!out.has(n)) out.set(n, new Set());
    /** @type {Set<number>} */ (out.get(n)).add(lineOf[i]);
  }
  return out;
}

/**
 * `_shared.js fitLines`' fit with the box's height as a second condition: the largest size, in `step` px steps from
 * `max` down to `min` (the minimum itself last), at which the text takes at most `lines` lines, is at most `height` px
 * tall and keeps every word whole (a word broken even at the minimum does not count against a size; a TeX word wider
 * than the box holds the text at its minimum). Leaves the element at the chosen size. `overflow`: px past the smaller
 * of `lines` lines and `height` at the minimum, or the widest TeX word's excess (> 2 px rounding).
 * @param {HTMLElement} el
 * @param {{max: number, min: number, lines: number, lh: number, step?: number}} spec
 * @param {HTMLElement[]} [words]
 * @param {boolean[]} [texFlags]
 * @param {number} [height]
 * @returns {{size: number, fits: boolean, overflow: number}}
 */
export function fitInHeight(el, spec, words = [], texFlags = [], height = Infinity) {
  const step = spec.step && spec.step > 0 ? spec.step : SIZE_STEP;
  const isTex = (/** @type {number} */ i) => !!texFlags[i];
  const broken = (/** @type {number} */ s) => words.map((w, i) => (isTex(i)
    ? w.offsetWidth > el.clientWidth + 1
    : w.offsetHeight > 1.5 * s * spec.lh));
  el.style.fontSize = `${spec.min}px`;
  const unavoidable = broken(spec.min);
  const texTooWide = unavoidable.some((b, i) => b && isTex(i));
  const atMin = el.offsetHeight;
  let size = spec.min;
  let fits = false;
  for (let s = texTooWide ? spec.min : Math.max(spec.min, spec.max); ; s = Math.max(spec.min, s - step)) {
    el.style.fontSize = `${s}px`;
    const whole = broken(s).every((b, i) => !b || unavoidable[i]);
    const tall = el.offsetHeight;
    if (whole && Math.round(tall / (s * spec.lh)) <= spec.lines && tall <= height + 1) {
      size = s;
      fits = true;
      break;
    }
    if (s <= spec.min) break;
  }
  el.style.fontSize = `${size}px`;
  const allowed = Math.min(spec.lines * spec.min * spec.lh, height);
  let overflow = fits ? 0 : Math.max(1, Math.round(atMin - allowed));
  const wide = Math.max(0, ...words.map((w, i) => (isTex(i) ? w.offsetWidth - el.clientWidth : 0)));
  if (wide > 2) overflow = Math.max(overflow, wide);
  return { size, fits, overflow };
}

/**
 * `ink` at opacity `f` over `bg` (opaque hex): the colour a dimmed line is drawn in, which preflight checks.
 * @param {string} ink @param {number} f @param {string} bg
 */
export function dimmedInk(ink, f, bg) {
  if (!(f < 1)) return ink;
  const a = Math.round(Math.max(0, f) * 255).toString(16).padStart(2, '0');
  return composite(`${ink.slice(0, 7)}${a}`, bg);
}

// ---------------------------------------------------------------------------------------------
// The template
// ---------------------------------------------------------------------------------------------

/** @param {TemplateContext} ctx */
const isFallback = (ctx) => !!(ctx.view && ctx.view.ksFallback);

/** @type {TemplateModule} */
export default {
  id: ID,

  /**
   * The DOM: a stack (label, statement, attribution), each part in a dim group of its own so the stage owns the dim
   * and this template animates only their children; every statement word in its mask. The phrase times are fixed
   * here, from the scene's data. No measuring.
   * @param {TemplateContext} ctx
   */
  build(ctx) {
    const { scene, format } = ctx;
    if (!drawsScene(scene, format)) {
      // another item, a panel, a medium or a statement past MAX_WORDS: board_generic draws the scene whole
      const inner = boardGeneric.build(ctx);
      inner.ksFallback = true;
      return inner;
    }
    const L = LAYOUT[format];
    const item = /** @type {BoardItem} */ (statementOf(scene));
    const kind = String(item.kind);
    const fixes = fixesOf(ctx.look);
    const ink = ctx.onBackground.ink;
    const ink2 = ctx.onBackground.ink2;
    const cover = setsAtFrame0(format, ctx.index);
    const reveal = !!ctx.motion && ctx.motion.entrance === 'baseline_reveal';
    const root = h('div', {
      class: ['dyn-ks', `dyn-ks--${format}`],
      style: { position: 'absolute', left: '0px', top: '0px', right: '0px', bottom: '0px' },
    });
    const stack = h('div', {
      class: 'dyn-ks-stack',
      style: { display: 'flex', flexDirection: 'column', alignItems: 'stretch', gap: `${L.gap}px` },
    });

    // a callout's kind, one line above the statement
    /** @type {any} */
    let label = null;
    const labelText = Object.prototype.hasOwnProperty.call(LABELS, kind)
      ? /** @type {Record<string, string>} */ (LABELS)[kind] : null;
    if (labelText) {
      const el = h('div', {
        class: 'dyn-ks-label', text: labelText,
        style: {
          color: ink2, fontFamily: 'var(--dyn-font-body)', fontWeight: '600', fontSize: `${L.label}px`,
          lineHeight: String(LINE_HEIGHT.body.latin), whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
        },
      });
      const mask = revealMask(el);
      const g = group('dyn-ks-label-group', mask);
      stack.appendChild(g);
      label = { el, mask, g, text: labelText, alpha: 0, overflow: 0 };
    }

    // the statement: every word in a mask (the reveal's clip and the completed line's dim)
    const text = h('p', {
      class: 'dyn-ks-text', dir: 'auto', dataset: { item: item.id },
      style: {
        position: 'relative', margin: '0px', color: ink, fontFamily: 'var(--dyn-font-display)', fontWeight: '700',
        fontSize: `${L.statement.max}px`, lineHeight: String(lineHeight('display', item.text || '')),
        letterSpacing: `${TRACKING_EM}em`, overflowWrap: 'anywhere',
      },
    });
    const { words, tex } = richWords(ctx, text, String(item.text || ''));
    const masks = words.map((w) => {
      const m = h('span', {
        class: 'dyn-ks-mask',
        style: { display: 'inline-block', verticalAlign: 'top', padding: `${MASK_PAD_EM}em`, margin: `-${MASK_PAD_EM}em` },
      });
      /** @type {Node} */ (w.parentNode).insertBefore(m, w);
      m.appendChild(w);
      return m;
    });
    const textGroup = group('dyn-ks-textgroup', text);
    stack.appendChild(textGroup);

    // a quote's attribution, below
    /** @type {any} */
    let attr = null;
    const caption = attributionOf(item);
    if (caption !== null) {
      const el = h('div', {
        class: 'dyn-ks-attr', dir: 'auto',
        style: {
          margin: '0px', color: ink2, fontFamily: 'var(--dyn-font-body)', fontWeight: '600', fontSize: `${L.attribution}px`,
          lineHeight: String(lineHeight('body', caption)), overflowWrap: 'anywhere',
        },
      }, h('span', { class: 'dyn-ks-attr-dash', text: ATTRIBUTION_PREFIX }));
      const aw = richWords(ctx, el, caption);
      const mask = revealMask(el);
      const g = group('dyn-ks-attr-group', mask);
      stack.appendChild(g);
      attr = { el, mask, g, words: aw.words, tex: aw.tex, caption, alpha: 0, overflow: 0, lh: lineHeight('body', caption) };
    }

    // R-53's scrim remedy behind the whole stack when preflight asked for one for any of its slots
    const scrim = ['statement', 'label', 'attribution'].some((s) => fixes.slots[s] && fixes.slots[s].scrim)
      ? ctx.roles.color('scrim') : null;
    const holder = h('div', { class: 'dyn-ks-holder', style: { position: 'absolute', boxSizing: 'border-box' } }, stack);
    if (scrim) {
      Object.assign(holder.style, {
        padding: `${SPACE[1]}px ${SPACE[2]}px`, borderRadius: `${RADIUS.card}px`, background: scrim,
      });
    }
    root.appendChild(holder);
    ctx.root.appendChild(root);

    // when each phrase lands: from the data alone (never the DOM), so a frame is a function of t
    const enterDur = seconds(ctx, PHRASE_ENTER);
    const timing = new Map(itemTimings(scene).map((x) => [x.id, x.enter]));
    const enter = cover ? 0 : Math.max(0, Number(timing.get(item.id)) || 0);
    const texts = wordTexts(String(item.text || ''), ctx.locale);
    // richWords and wordTexts split the same plain text the same way (kinetic_statement.test.js pins it); should they
    // ever disagree, the list is cut or padded to the drawn words, so every drawn word still has a phrase and a time
    const said = texts.length === words.length ? texts : words.map((_, i) => texts[i] || '');
    const phrases = phrasesOf(said);
    const times = phraseTimes(scene, item, said, phrases, caption !== null ? wordTexts(caption, ctx.locale) : [],
      enter, enterDur);
    /** @type {number[]} */
    const wordAt = words.map(() => enter);
    phrases.forEach((idx, k) => idx.forEach((i) => { wordAt[i] = cover ? 0 : times.phrases[k]; }));
    return {
      root, holder, stack, item, kind, cover, reveal, label, text, textGroup, words, wordTex: tex, masks, attr, scrim,
      enter, wordAt, phrases, attrAt: cover ? 0 : times.attribution,
      wordAlpha: words.map(() => 0), wordDim: words.map(() => 1),
      lineOf: words.map(() => 0), lineDone: [Infinity],
      size: L.statement.max, min: L.statement.min, overflow: 0,
    };
  },

  /**
   * Measure and fit (idempotent; runs again when a late face arrives): the label's and the attribution's overflow,
   * the statement's size in the height they leave it, then the stack's top (centred on the band) and the lines.
   * @param {TemplateContext} ctx
   */
  layout(ctx) {
    if (isFallback(ctx)) {
      if (typeof boardGeneric.layout === 'function') boardGeneric.layout(ctx);
      return;
    }
    const v = ctx.view;
    const { format } = ctx;
    const L = LAYOUT[format];
    const b = boxes(format, ctx.zones);
    const padX = v.scrim ? SPACE[2] : 0;
    const padY = v.scrim ? SPACE[1] : 0;
    // the content box is the slot's width exactly; a scrim's padding reaches past it
    v.holder.style.left = `${Math.round(b.stack.x0 - padX)}px`;
    v.holder.style.width = `${Math.round(b.stack.x1 - b.stack.x0 + 2 * padX)}px`;
    v.holder.style.top = `${Math.round(b.stack.y0)}px`;

    let taken = 0;
    if (v.label) {
      // one line at board_generic's kicker size: px it runs past its column (never clipped silently, §7.12)
      v.label.overflow = Math.max(0, v.label.el.scrollWidth - v.label.el.clientWidth);
      taken += v.label.el.offsetHeight + L.gap;
    }
    if (v.attr) {
      const line = L.attribution * v.attr.lh;
      const tall = v.attr.el.offsetHeight;
      const lines = Math.round(tall / line);
      v.attr.overflow = lines > L.attributionLines ? Math.max(1, Math.round(tall - L.attributionLines * line)) : 0;
      const wide = Math.max(0, ...v.attr.words.map((/** @type {HTMLElement} */ w, /** @type {number} */ i) => (v.attr.tex[i]
        ? w.offsetWidth - v.attr.el.clientWidth : 0)));
      if (wide > 2) v.attr.overflow = Math.max(v.attr.overflow, wide);
      taken += Math.min(tall, Math.ceil(L.attributionLines * line)) + L.gap;
    }
    const height = Math.max(0, b.stack.y1 - b.stack.y0 - 2 * padY - taken);
    const range = slotRange(ctx, 'statement', { min: L.statement.min, max: L.statement.max, role: 'title' });
    const lh = lineHeight('display', v.item.text || '');
    const fit = fitInHeight(v.text, { max: range.max, min: range.min, lines: L.statement.lines, lh, step: SIZE_STEP },
      v.words, v.wordTex, height);
    v.size = fit.size;
    v.min = range.min;
    v.overflow = fit.overflow;

    // centred on the band, inside the stack box
    v.holder.style.top = `${stackTop(b, v.holder.offsetHeight)}px`;
    // the lines, as laid out now (the completed-line dim), each waiting for the term cues on it to leave
    v.lineOf = lineIndices(v.masks, v.size * lh);
    const terms = termLines(v.text, v.masks, v.lineOf);
    const ends = termCueEnds(ctx.scene, v.item.id, terms.size ? Math.max(...terms.keys()) + 1 : 0);
    /** @type {number[]} */
    const busy = [];
    for (const [n, lines] of terms) for (const l of lines) busy[l] = Math.max(busy[l] ?? -Infinity, ends[n]);
    v.lineDone = lineDoneTimes(v.lineOf, v.wordAt, busy);
  },

  /**
   * Paint scene time t: the label's entrance, each phrase's entrance on its spoken word, the completed lines' dim and
   * the attribution's entrance. A pure function of t: every property is painted on every frame, with a value.
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
    const cover = !!v.cover;
    if (v.label) {
      const e = cover ? 1 : enterAt(ctx, t, v.enter, LABEL_ENTER);
      v.label.alpha = e;
      paintTextEntrance(ctx, v.label.el, v.label.mask, e);
    }
    const dimFor = seconds(ctx, LINE_DIM);
    v.words.forEach((/** @type {HTMLElement} */ w, /** @type {number} */ i) => {
      const e = cover ? 1 : enterAt(ctx, t, v.wordAt[i], PHRASE_ENTER);
      v.wordAlpha[i] = e;
      const mask = v.masks[i];
      paintTextEntrance(ctx, w, mask, e);
      // the baseline reveal's clip, only while the word is being revealed (settled text is never clipped)
      p.style(mask, 'overflow', v.reveal && !ctx.reducedMotion && e < 1 ? 'hidden' : 'visible');
      const done = v.lineDone[v.lineOf[i]];
      const k = cover || !Number.isFinite(done) ? 0 : ease('ease.move', progress(t, done, dimFor));
      const f = 1 - (1 - DIM_ALPHA) * k;
      v.wordDim[i] = f;
      p.style(mask, 'opacity', fx(f));
    });
    if (v.attr) {
      const e = cover ? 1 : enterAt(ctx, t, v.attrAt, PHRASE_ENTER);
      v.attr.alpha = e;
      paintTextEntrance(ctx, v.attr.el, v.attr.mask, e);
    }
  },

  /**
   * Where a look_here points (plan §1.4, §1.7): the statement's box (`<item>`, `<item>:term:<n>`), as painted now, on
   * whole stage px; null for anything else or off the page.
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
    return {
      rect: { x: x0, y: y0, w: Math.round(r.x + r.w) - x0, h: Math.round(r.y + r.h) - y0 },
      shape: 'rect', radius: 0, el: v.textGroup, kind: 'text',
    };
  },

  /**
   * The elements holding the item's text, where cue.term finds its `[[term]]` spans (`.rt-k`, `_shared.js
   * richWords`), in the item's field order: the statement (`text`), then a quote's attribution (`caption`).
   * @param {TemplateContext} ctx
   * @param {string} itemId
   * @returns {Element | Element[] | null}
   */
  itemEl(ctx, itemId) {
    if (isFallback(ctx)) return typeof boardGeneric.itemEl === 'function' ? boardGeneric.itemEl(ctx, itemId) : null;
    const v = ctx.view;
    if (itemId !== v.item.id) return null;
    return v.attr ? [v.text, v.attr.el] : v.text;
  },

  /**
   * The content groups a look_here dims (all but the target's): the label, the statement and the attribution - the
   * same elements at every t; this template never writes their opacity (it animates their children).
   * @param {TemplateContext} ctx
   * @returns {Element[]}
   */
  dimGroups(ctx) {
    if (isFallback(ctx)) return typeof boardGeneric.dimGroups === 'function' ? boardGeneric.dimGroups(ctx) : [];
    const v = ctx.view;
    return [v.label ? v.label.g : null, v.textGroup, v.attr ? v.attr.g : null].filter(Boolean);
  },

  /**
   * The morph anchor (plan §1.4): none in R1 (B2f pairs the templates that have one; a chapter's last takeaway is
   * §4.3's candidate, chosen by direct.py then).
   * @param {TemplateContext} _ctx
   * @returns {null}
   */
  anchor(_ctx) {
    return null;
  },

  /**
   * Static: no medium (typography only); a scene this template does not draw keeps board_generic's box.
   * @param {TimedScene} scene
   * @param {Zones} zones
   * @param {'live' | 'render'} mode
   * @param {string | undefined} slot
   * @param {import('../core.js').Fixes} fixes
   * @returns {{w: number, h: number} | null}
   */
  mediaBox(scene, zones, mode, slot, fixes) {
    if (!drawsScene(scene, zones && zones.format === 'portrait' ? 'portrait' : 'landscape')) {
      return typeof boardGeneric.mediaBox === 'function' ? boardGeneric.mediaBox(scene, zones, mode, slot, fixes) : null;
    }
    return null;
  },

  /**
   * Static: every TeX string the template renders for the scene (`texOf`; board_generic's for a scene it hands on).
   * @param {TimedScene} scene
   * @param {import('../../shared/types.js').ResolvedLook | null} look
   * @param {Format} fmt
   */
  tex(scene, look, fmt) {
    if (!drawsScene(scene, fmt)) return typeof boardGeneric.tex === 'function' ? boardGeneric.tex(scene, look, fmt) : [];
    return texOf(scene);
  },

  /**
   * The text on screen (preflight, §7.12): the label (slot `label`), every statement word (slot `statement`: a word's
   * own box, in the ink it is drawn in now - a completed line's dimmed ink, R-53 holds for it too) and the attribution
   * (`attribution`), all critical, on the background's worst case (or on the R-53 scrim over it).
   * @param {TemplateContext} ctx
   * @returns {TextEntry[]}
   */
  texts(ctx) {
    if (isFallback(ctx)) return typeof boardGeneric.texts === 'function' ? boardGeneric.texts(ctx) : [];
    const v = ctx.view;
    const L = LAYOUT[ctx.format];
    const floors = TYPE_FLOORS[ctx.format];
    /** @param {string} ink */
    const bgOf = (ink) => (v.scrim ? scrimmedBg(v.scrim, ctx.onBackground, ink) : ctx.onBackground.bg);
    /** @type {TextEntry[]} */
    const out = [];
    const ink2 = ctx.onBackground.ink2;
    if (v.label) {
      out.push({
        el: v.label.el, role: 'label', slot: 'label', critical: true, ink: ink2, bg: bgOf(ink2), alpha: v.label.alpha,
        overflow: v.label.overflow, size: L.label, min: Math.max(floors.label_min, L.label), weight: 600, text: v.label.text,
      });
    }
    const ink = ctx.onBackground.ink;
    const bg = bgOf(ink);
    const plain = plainText(String(v.item.text || ''));
    v.words.forEach((/** @type {HTMLElement} */ w, /** @type {number} */ i) => {
      out.push({
        el: w, role: 'title', slot: 'statement', critical: true, ink: dimmedInk(ink, v.wordDim[i], bg), bg,
        alpha: v.wordAlpha[i], overflow: v.overflow, size: v.size, min: v.min, weight: 700, text: plain,
      });
    });
    if (v.attr) {
      out.push({
        el: v.attr.el, role: 'label', slot: 'attribution', critical: true, ink: ink2, bg: bgOf(ink2), alpha: v.attr.alpha,
        overflow: v.attr.overflow, size: L.attribution, min: Math.max(floors.label_min, L.attribution), weight: 600,
        text: `${ATTRIBUTION_PREFIX}${plainText(v.attr.caption)}`,
      });
    }
    return out;
  },
};
