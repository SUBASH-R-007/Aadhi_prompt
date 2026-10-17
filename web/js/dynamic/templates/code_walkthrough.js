// @ts-check
/**
 * `code_walkthrough` (DYNAMIC_VIDEO_DESIGN §4.3 B, R1, class `code`, dense; deviation 13; B2 step 5, the pattern of
 * `docs/plans/b2/b2c_design_notes.md`): a program in a code editor, revealed line by line, its lines pointed at one
 * by one.
 *
 * Data (§4.3, templates.py `_fit_code_walkthrough`): exactly one `code` item (`code` the program, `language` its
 * language) and at most one `heading` above it; in 16:9 the scene's **terminal** side panel beside the editor (§4.3
 * "the terminal slides up on the `output` cue"). The program must fit the editor whole at the slot's minimum size
 * (`codeFits`, deviation 13: R-29's floor wins over §4.3's 34 px): its line boxes (`codeBox(format)`: a whole-pixel
 * pitch per line, the Indic one when the program holds Indic text, plan §2) within the editor's height - less a
 * heading's reserved line when the scene has one - and every line within the format's characters (16:9 60, 38 beside
 * a terminal; 9:16 28), tabs expanded to TAB columns. Anything else - another item, a second heading, a panel of
 * another kind, any panel in 9:16, a longer or wider program - is drawn by **board_generic** whole, every hook handed
 * on (`drawsScene`), and `_fit_code_walkthrough` must refuse exactly those scenes (the CG-2 gate,
 * tests/compose/dynamic/test_templates.py), so the director never sends one here. Nothing a timeline names is
 * silently dropped. The code item's own `text` is not drawn: no code view draws it (classic board.js, board_generic).
 *
 * The editor (§4.3 "an editor frame on a dark surface in every family, line numbers"): the surface is the look's
 * `code.surface` token (`themes.code_surface`, stored by the resolver on the scenes this template draws), and its
 * text takes the dark palette (`roles.on('dark')`: `presentation.content_alt` in a light family, the validated pair
 * themes.py checks). A look without the token draws the editor on the scene's own `surface` in the scene's own palette
 * (identical in a dark family; in a light one the editor stays light rather than guessing an unvalidated dark colour).
 * Line numbers in `ink2` in a gutter two digits wide; the code in `ink`, set `white-space: pre` in the mono face with
 * its programming ligatures off (`!=` stays two characters); Indic runs inside the code (a comment, a string) in the
 * body stack, which carries the lecture's Indic face (the mono face has none). 9:16 sets the code at weight 700
 * (R-29: <= 64 px text at >= 600; JetBrains Mono has 400 and 700).
 *
 * Syntax colours: the closed role list (§4.2, plan §1.9) has no neutral syntax roles - `ok` / `warn` mean right and
 * wrong, `mark` / `focus` are the cues', the structure roles never colour content and the entity roles mean the
 * lesson's entities (R-30) - so the vendored Prism is not used (it would need five or more roles). Instead: code and
 * strings in `ink`, comments in `ink2` (`codeSegments`, a small lexer per language family; an unknown language is not
 * lexed), and an identifier that names one of the lesson's entities (`presentation.entity_colors`, `ctx.entityRole`,
 * exact and case-sensitive, outside strings and comments, and only in a lexed language) in that entity's colour, so
 * `I = V / R` in code wears the colours `I = V / R` wears in the formula.
 *
 * 16:9: the heading (84-96 px, one line) across the top band; the editor right below it (centred in the area when
 * there is no heading), as wide as its longest line needs (at least the centre 60%) and centred across, or - beside
 * a terminal panel - in the left 55%, the terminal's card in the right 45% level with it. The code is set at the
 * largest size (48 down to 36 px, 4 px steps) at which every line fits the editor's width and all of them its height.
 * 9:16: the heading (48-64 px, one line) in the title zone and the editor below it, its code column the upper critical
 * box's width (x 192-888: 28 characters at 40 px are 672 px) and its lines above that box's bottom (y 840, R-59); the
 * gutter sits left of the box (line numbers are a reference aid, reported but not critical).
 *
 * Choreography (§4.3, §4.8): the heading enters with the personality's text entrance; the editor rises 24 px in
 * 450 ms on the code item's beat (state.js `itemTimings`: a `reveal` event wins); then the lines appear **line by
 * line**, one every LINE_STEP, each fading in over LINE_FADE (§4.3's alternative to typing at 30-40 chars/s, which
 * needs a narration-to-line mapping no contract carries yet). The terminal panel slides up on the scene's first
 * `output` event (else at its `show_at`), and its output lines appear as the schedule reveals them
 * (`_shared.js paintPanel`). Durations take the personality's scale; the cues keep their own. The background is frozen
 * and SFX are off for the whole scene (R-55, R-49: `dense` in templates.py, which the resolver reads).
 *
 * Emphasis is cues.js's (§4.2, plan §1.4/§1.5): `focusRect` gives the editor (`<item>`), a line as a **bar**
 * (`<item>:line:<n>`, 0-based, plan §1.7: the line's code after its indentation, from its cap top to just under its
 * baseline, so the pill sits between its neighbours' ink), the heading, and through `_shared.js panelFocusRect` the
 * panel and a terminal row; `dimGroups` are the heading, the editor's frame, every line and the panel (siblings, so a
 * dim never compounds), so a bar on one line dims the others while the frame is kept bright under it. `cueMode` tells
 * cues.js a target on the dark editor of a light family is drawn on a dark surface (its outline takes that palette's
 * `focus`). Nothing here writes `outline`, `text-decoration`, `filter`, `backdrop-filter` or `will-change`, and no cue
 * role (`mark`, `focus`) is used.
 *
 * Determinism (§7.4, plan §P): `update` is a pure function of t (entrances and line fades are closed forms in t);
 * every painted property gets a value on every frame; layout writes geometry once, from the laid-out page (the line
 * boxes are whole-pixel pitches, never measured).
 *
 * Every size, the line cadence and the 1.2 code line height (the type scale's body 1.35 would hold 15 lines at 36 px,
 * not deviation 13's 17) are B2 proposals pending the owner's review through the reference frames.
 */

import { h } from '../../shared/dom.js';
import { mathSpans, plainText } from '../../player/richtext.js';
import { LAYOUT_TOKENS, ZONES, fixesOf, fx } from '../core.js';
import { itemTimings } from '../state.js';
import boardGeneric from './board_generic.js';
import {
  PANEL_TERM, RADIUS, buildPanel, enterAt, fitLines, group, layoutPanel, paintPanel, paintRise, paintTextEntrance,
  panelFocusRect, panelOf, panelTexts, region, revealMask, richWords, scrimmedBg, seconds, shownPanel, slotRange,
} from './_shared.js';

/** @typedef {import('./types.js').TemplateContext} TemplateContext */
/** @typedef {import('./types.js').TemplateModule} TemplateModule */
/** @typedef {import('./types.js').FocusRect} FocusRect */
/** @typedef {import('./types.js').TextEntry} TextEntry */
/** @typedef {import('../../shared/types.js').TimedScene} TimedScene */
/** @typedef {import('../../shared/types.js').BoardItem} BoardItem */
/** @typedef {import('../core.js').Box} Box */
/** @typedef {import('../core.js').Zones} Zones */
/** @typedef {import('../core.js').Roles} Roles */

export const ID = 'code_walkthrough';

/** The look token holding the editor's surface (`themes.code_surface`; the resolver stores it on this template's
 * scenes). */
export const CODE_SURFACE_TOKEN = 'code.surface';

// ---------------------------------------------------------------------------------------------
// Motion (§4.3 code_walkthrough, §4.8)
// ---------------------------------------------------------------------------------------------

/** The editor rises in on the code item's beat (§4.8 medium move 400-500 ms) ... */
export const FRAME_ENTER = 0.45;
/** ... by this many px. */
export const FRAME_RISE = 24;
/** The heading's entrance (§4.8). */
export const HEADING_ENTER = 0.45;
/** Line by line: the first line once the editor has settled (FRAME_ENTER), then one every LINE_STEP ... */
export const LINE_STEP = 0.3;
/** ... each fading in over this long (no move: code does not hop). */
export const LINE_FADE = 0.25;

// ---------------------------------------------------------------------------------------------
// Layout (stage px; R-29 floors; the catalogue's zones and regions). templates.py mirrors the text slots
// (`_code_walkthrough_slots`) and the data rule (`CODE_BOX`, `code_fits`); code_walkthrough.test.js and
// tests/compose/dynamic/test_code_walkthrough_fixtures.py pin the two against each other.
// ---------------------------------------------------------------------------------------------

/** Tab stops, in columns: a tab is expanded to spaces before anything is counted or drawn (Python's expandtabs). */
export const TAB = 4;
/** Code line heights: an editor's 1.2 for Latin, the type scale's Indic body 1.45 when the program holds Indic text
 * (plan §2); every line of a program takes the same one. */
export const CODE_LH = Object.freeze({ latin: 1.2, indic: 1.45 });
/** Heading line heights (the catalogue's type scale: heading 1.15 Latin, >= 1.25 Indic; pinned by the test). */
export const HEADING_LH = Object.freeze({ latin: 1.15, indic: 1.25 });
/** The gutter holds two digits of the mono face (its advance is 0.6 em): ceil(1.2 x size) px. */
export const GUTTER_EM = 1.2;
/** Size steps when fitting (board_generic's and textfit.py's 4 px). */
export const SIZE_STEP = 4;
/**
 * The mono face's vertical metrics in em (JetBrains Mono, both weights: hhea / typo ascent 1020, descent 300, cap
 * height 730 per 1000; its descenders reach 180), and how far below the baseline a look_here bar ends (half the
 * descender): the bar runs from the cap top to there, so at 1.2 line height its 4 px outline, 8 px out, falls in the
 * gap between the lines' ink instead of under the line above (measured on the page).
 */
export const MONO = Object.freeze({ ascent: 1.02, descent: 0.3, cap: 0.73, below: 0.09 });

/**
 * A look_here bar's band inside a line box `pitch` px tall at `size` px: from the cap top to MONO.below under the
 * baseline (the line's text sits on the mono strut, centred by its half-leading). Pure.
 * @param {number} pitch @param {number} size
 * @returns {{top: number, height: number}}
 */
export function barBand(pitch, size) {
  const baseline = (pitch - (MONO.ascent + MONO.descent) * size) / 2 + MONO.ascent * size;
  return { top: baseline - MONO.cap * size, height: (MONO.cap + MONO.below) * size };
}
/** The editor's shadow (the catalogue's level 2, formula_focus's and worked_steps' card). */
export const FRAME_SHADOW = String(LAYOUT_TOKENS.shadow['2']);

/**
 * Per-format layout. `code`: the code's size range (16:9 deviation 13's 36 px floor up to R-29's body 48; 9:16 R-29's
 * 40 px text floor up to 48), `weight` (9:16 700: R-29's heavy rule), `heading` its range (16:9 R-29 headings from 84,
 * board_generic's 96 cap; 9:16 48-64) and its one line, `padX` / `padY` the editor's padding, `gap` between the
 * gutter and the code (24: a look_here bar's stroke, FOCUS_OUTSET + FOCUS_PX = 12 px outside the code, stays 12 px
 * clear of the line number; 16:9 beside a panel 38 characters at 36 px, 820.8 px, still fit the 822 px column),
 * `headingGap` under the heading's reserved line, `chars` / `charsPanel` the characters per line
 * (deviation 13: 16:9 60, 38 beside a terminal; 9:16 28; null: no panel layout in this format).
 */
export const LAYOUT = Object.freeze({
  landscape: Object.freeze({
    code: Object.freeze({ max: 48, min: 36 }),
    weight: 400,
    heading: Object.freeze({ max: 96, min: 84, lines: 1 }),
    padX: 24,
    padY: 24,
    gap: 24,
    headingGap: 24,
    chars: 60,
    charsPanel: /** @type {number | null} */ (38),
  }),
  portrait: Object.freeze({
    code: Object.freeze({ max: 48, min: 40 }),
    weight: 700,
    heading: Object.freeze({ max: 64, min: 48, lines: 1 }),
    padX: 24,
    padY: 16,
    gap: 24,
    headingGap: 16,
    chars: 28,
    charsPanel: /** @type {number | null} */ (null),
  }),
});

/** A line's box at `size` px: whole pixels, the Indic line height when the program holds Indic text. Pure.
 * @param {number} size @param {boolean} indic */
export function pitchAt(size, indic) {
  return Math.round(size * (indic ? CODE_LH.indic : CODE_LH.latin));
}

/** The gutter's width at `size` px (two mono digits). Pure. @param {number} size */
export function gutterAt(size) {
  return Math.ceil(GUTTER_EM * size);
}

/** The heading's reserved line: its largest size at the Indic line height, so any heading's one line fits it. Pure.
 * @param {'landscape' | 'portrait'} format */
export function headingReserve(format) {
  return Math.ceil(LAYOUT[format].heading.max * HEADING_LH.indic);
}

/**
 * The boxes of a format (pure; whole stage px). `heading`: the heading's box (the title zone's width, its reserved
 * line's height). `codeTop`: where the editor's area starts (below the heading's reserved line and gap when
 * `headed`). `linesTop` / `linesBottom`: the band the code's line boxes must lie in when the editor sits at the top of
 * its area (an editor without a heading is centred in [codeTop, linesBottom + padY]). `area`: the editor's horizontal extent (16:9 the
 * whole grid or, beside a panel, the left 55%; 9:16 the code column, the upper critical box's width, with the gutter
 * and padding outside it). `visual`: the panel's box (16:9 with a panel: the right 45% from codeTop down). `minWidth`:
 * the narrowest a 16:9 editor is drawn (the centre 60%).
 * @param {'landscape' | 'portrait'} format
 * @param {Zones} zones
 * @param {boolean} headed
 * @param {boolean} panel
 * @returns {{heading: Box, codeTop: number, linesTop: number, linesBottom: number, area: Box, visual: Box | null,
 *   minWidth: number}}
 */
export function boxes(format, zones, headed, panel) {
  const L = LAYOUT[format];
  const ctx = { format };
  const top = zones.title.y0;
  const heading = { x0: zones.title.x0, y0: top, x1: zones.title.x1, y1: top + headingReserve(format) };
  const codeTop = headed ? heading.y1 + L.headingGap : top;
  if (format === 'portrait') {
    const box = zones.critical[0];
    return {
      heading, codeTop, linesTop: codeTop + L.padY, linesBottom: box.y1,
      area: { x0: box.x0, y0: codeTop, x1: box.x1, y1: box.y1 + L.padY }, visual: null, minWidth: 0,
    };
  }
  const bottom = zones.body.y1;
  const full = region(ctx, 'full');
  const left = region(ctx, 'left-55');
  const right = region(ctx, 'right-45');
  return {
    heading, codeTop, linesTop: codeTop + L.padY, linesBottom: bottom - L.padY,
    area: panel ? { x0: left.x0, y0: codeTop, x1: left.x1, y1: bottom } : { x0: full.x0, y0: codeTop, x1: full.x1, y1: bottom },
    visual: panel ? { x0: right.x0, y0: codeTop, x1: right.x1, y1: bottom } : null,
    minWidth: region(ctx, 'centre-60').x1 - region(ctx, 'centre-60').x0,
  };
}

/**
 * The code column's width at `size` px (pure): 16:9 the editor's area less its padding, the gutter and the gap; 9:16
 * the upper critical box's width (the gutter and padding sit outside it).
 * @param {'landscape' | 'portrait'} format @param {{area: Box}} b @param {number} size
 */
export function textWidth(format, b, size) {
  const L = LAYOUT[format];
  const w = b.area.x1 - b.area.x0;
  return format === 'portrait' ? w : w - 2 * L.padX - gutterAt(size) - L.gap;
}

/**
 * The data rule both sides ask (templates.py `CODE_BOX` / `code_fits` mirror it, number for number): per format, the
 * px the code's line boxes may take (`linesPx` without a heading, `headedPx` below one), a line's box at the minimum
 * size (`pitch`, `pitchIndic`) and the characters per line (`chars`; `charsPanel` beside a terminal, null: no panel
 * layout). Pure.
 * @param {'landscape' | 'portrait'} format @param {Zones} zones
 */
export function codeBox(format, zones) {
  const L = LAYOUT[format];
  const plain = boxes(format, zones, false, false);
  const headed = boxes(format, zones, true, false);
  return {
    linesPx: plain.linesBottom - plain.linesTop,
    headedPx: headed.linesBottom - headed.linesTop,
    pitch: pitchAt(L.code.min, false),
    pitchIndic: pitchAt(L.code.min, true),
    chars: L.chars,
    charsPanel: L.charsPanel,
  };
}

// ---------------------------------------------------------------------------------------------
// What the scene shows (pure)
// ---------------------------------------------------------------------------------------------

/** Python's str.splitlines boundaries (\r\n first, then each single one). */
const LINE_BREAK = /\r\n|[\n\r\u000b\u000c\u001c\u001d\u001e\u0085\u2028\u2029]/;
/** Indic scripts (the Devanagari to Sinhala blocks, U+0900-U+0DFF; templates.py `CODE_INDIC` is the same range). */
const INDIC_RANGE = /[\u0900-\u0DFF]/;

/**
 * A program's lines exactly as Python's `[line.expandtabs(TAB) for line in code.splitlines()]` gives them (templates.py
 * `code_lines`): split at every boundary `str.splitlines` splits at, a final boundary closing the last line rather than
 * opening an empty one, no line for an empty program; then each line's tabs expanded to TAB columns, a column per code
 * point. Pure.
 * @param {string} code
 * @returns {string[]}
 */
export function codeLines(code) {
  const s = String(code || '');
  if (!s) return [];
  const parts = s.split(LINE_BREAK);
  if (parts.length > 1 && parts[parts.length - 1] === '') parts.pop();
  return parts.map(expandTabs);
}

/** @param {string} line */
function expandTabs(line) {
  if (!line.includes('\t')) return line;
  let out = '';
  let col = 0;
  for (const ch of line) {
    if (ch === '\t') {
      const n = TAB - (col % TAB);
      out += ' '.repeat(n);
      col += n;
    } else {
      out += ch;
      col += 1;
    }
  }
  return out;
}

/** Characters as Python's len counts them (code points). @param {string} s */
export function charCount(s) {
  return Array.from(s).length;
}

/** Whether `text` holds Indic script (INDIC_RANGE). @param {string} text */
export function hasIndic(text) {
  return INDIC_RANGE.test(String(text || ''));
}

/**
 * Whether a program fits the editor whole at the minimum size (the data rule; templates.py `code_fits`): at least one
 * line, every line within the characters per line (beside a terminal: `charsPanel`), and its line boxes within the
 * px the format allows with or without a heading. Pure.
 * @param {string} code
 * @param {'landscape' | 'portrait'} format
 * @param {Zones} zones
 * @param {{headed: boolean, panel: boolean}} opts
 */
export function codeFits(code, format, zones, opts) {
  const box = codeBox(format, zones);
  const lines = codeLines(code);
  const chars = opts.panel ? box.charsPanel : box.chars;
  if (!lines.length || chars === null) return false;
  const pitch = hasIndic(code) ? box.pitchIndic : box.pitch;
  if (lines.length * pitch > (opts.headed ? box.headedPx : box.linesPx)) return false;
  return lines.every((line) => charCount(line) <= chars);
}

/**
 * What the scene shows: its `code` item (the first), its `heading` (the first), and the counts `drawsScene` checks.
 * @param {TimedScene} scene
 * @returns {{code: BoardItem | null, heading: BoardItem | null, codes: number, headings: number, others: number}}
 */
export function codeItems(scene) {
  /** @type {BoardItem | null} */
  let code = null;
  /** @type {BoardItem | null} */
  let heading = null;
  let codes = 0;
  let headings = 0;
  let others = 0;
  for (const item of scene.board || []) {
    if (!item) continue;
    if (item.kind === 'code') {
      codes += 1;
      if (!code) code = item;
    } else if (item.kind === 'heading') {
      headings += 1;
      if (!heading) heading = item;
    } else {
      others += 1;
    }
  }
  return { code, heading, codes, headings, others };
}

/**
 * Whether this template draws the scene whole in `format` (templates.py `_fit_code_walkthrough` refuses everything
 * this refuses: the CG-2 gate): exactly one `code` item and at most one `heading`, nothing else on the board, no quiz,
 * main medium or poster; a shown side panel only in 16:9 and only a terminal (`_shared.js panelOf` draws its card);
 * and a program that fits the editor whole (`codeFits`). Anything else is board_generic's, every hook handed on.
 * @param {TimedScene} scene
 * @param {'landscape' | 'portrait'} [format]
 * @param {Zones} [zones]   the format's zones (default: the catalogue's)
 */
export function drawsScene(scene, format = 'landscape', zones) {
  const { code, codes, headings, others } = codeItems(scene);
  if (codes !== 1 || headings > 1 || others > 0 || !code) return false;
  if (scene.quiz || scene.media || scene.poster) return false;
  const shown = !!shownPanel(scene);
  if (shown) {
    if (format !== 'landscape') return false;
    const panel = panelOf(scene, 'render');
    if (!panel || panel.draw !== 'terminal') return false;
  }
  return codeFits(String(code.code || ''), format, zones || defaultZones(format), { headed: headings === 1, panel: shown });
}

/** The catalogue's zones of a format (core.js ZONES). @param {'landscape' | 'portrait'} format */
function defaultZones(format) {
  return ZONES[format];
}

/**
 * The scene's terminal panel as this template draws it (16:9 only; `_shared.js panelOf`), its entrance moved to the
 * scene's first `output` event when it has one (§4.3: "the terminal slides up on the output cue"), or null.
 * @param {TimedScene} scene @param {'landscape' | 'portrait'} format @param {'live' | 'render'} [mode]
 * @returns {import('./_shared.js').PanelPlan | null}
 */
export function panelFor(scene, format, mode = 'live') {
  if (format !== 'landscape') return null;
  const panel = panelOf(scene, mode);
  if (!panel || panel.draw !== 'terminal') return null;
  const at = outputAt(scene);
  return at === null ? panel : { ...panel, start: Math.max(panel.start, at) };
}

/**
 * The scene's first `output` event (on the panel, or on no target), or null.
 * @param {TimedScene} scene
 * @returns {number | null}
 */
export function outputAt(scene) {
  /** @type {number | null} */
  let out = null;
  for (const e of scene.events || []) {
    if (!e || e.kind !== 'output' || typeof e.t !== 'number' || !Number.isFinite(e.t)) continue;
    if (e.target && e.target !== 'panel') continue;
    if (out === null || e.t < out) out = e.t;
  }
  return out;
}

/**
 * When line `k` starts to appear (scene-relative): once the editor has settled after its entrance at `enter`, one
 * line every LINE_STEP, both in the personality's scale (`_shared.js seconds` reads only `ctx.motion`). Pure.
 * @param {TemplateContext} ctx @param {number} enter @param {number} k
 */
export function lineStart(ctx, enter, k) {
  return enter + seconds(ctx, FRAME_ENTER) + k * seconds(ctx, LINE_STEP);
}

/**
 * Every TeX string the template renders for a scene (the static `tex` hook): the `$...$` spans of the heading (inline,
 * as `richWords` mounts them). Code is never TeX.
 * @param {TimedScene} scene
 * @returns {{tex: string, display: boolean}[]}
 */
export function texOf(scene) {
  const { heading } = codeItems(scene);
  return heading ? mathSpans(String(heading.text || '')).filter((s) => s.trim()).map((s) => ({ tex: s, display: false })) : [];
}

/**
 * The 4 px-step sizes from `max` down to `min` (the minimum itself last).
 * @param {number} max @param {number} min
 */
export function sizeSteps(max, min) {
  /** @type {number[]} */
  const out = [];
  for (let s = max; s > min; s -= SIZE_STEP) out.push(s);
  out.push(min);
  return out;
}

// ---------------------------------------------------------------------------------------------
// Comments and strings (a small lexer per language family; colours only, never layout)
// ---------------------------------------------------------------------------------------------

/**
 * @typedef {object} QuoteSpec
 * @property {string} q        the opening and closing quote
 * @property {boolean} escape  a backslash escapes the next character inside
 * @property {boolean} multi   the string may run over several lines
 */
/**
 * @typedef {object} LexSpec
 * @property {string[]} line            line-comment markers
 * @property {[string, string][]} block  block-comment open / close pairs (checked before the line markers)
 * @property {QuoteSpec[]} quotes        string quotes, longest first
 * @property {boolean} [wordStart]      a line marker starts a comment only at the line's start or after whitespace
 *                                      (a shell's `$#`, `${#a}` are not comments)
 * @property {string} [transpose]       a quote that is an operator after an operand character (MATLAB's `A'`)
 */

/** @param {string} q @param {boolean} escape @param {boolean} [multi] @returns {QuoteSpec} */
const quote = (q, escape, multi = false) => ({ q, escape, multi });
/** @type {LexSpec} */
const PYTHON = { line: ['#'], block: [], quotes: [quote('"""', true, true), quote("'''", true, true), quote('"', true), quote("'", true)] };
/** @type {LexSpec} */
const CLIKE = { line: ['//'], block: [['/*', '*/']], quotes: [quote('"', true), quote("'", true)] };
/** @type {LexSpec} */
const JSLIKE = { ...CLIKE, quotes: [quote('`', true, true), quote('"', true), quote("'", true)] };
/** @type {LexSpec} */
const GO = { ...CLIKE, quotes: [quote('`', false, true), quote('"', true), quote("'", true)] };
/** Rust's and Verilog's single quote is a lifetime / a sized literal, never a string. @type {LexSpec} */
const DQ_CLIKE = { ...CLIKE, quotes: [quote('"', true)] };
/** @type {LexSpec} */
const SHELL = { line: ['#'], block: [], quotes: [quote('"', true), quote("'", false)], wordStart: true };
/** @type {LexSpec} */
const HASH = { line: ['#'], block: [], quotes: [quote('"', true), quote("'", true)] };
/** @type {LexSpec} */
const PHP = { line: ['//', '#'], block: [['/*', '*/']], quotes: [quote('"', true), quote("'", true)] };
/** SQL doubles a quote to escape it ('It''s' lexes as two adjacent strings, the same colour). @type {LexSpec} */
const SQL = { line: ['--'], block: [['/*', '*/']], quotes: [quote("'", false), quote('"', false)] };
/** @type {LexSpec} */
const MATLAB = { line: ['%'], block: [], quotes: [quote('"', false), quote("'", false)], transpose: "'" };

/** The lexer of each `BoardItem.language` (lower case, as canonicalize.py writes it); anything else is not lexed. */
export const LEXERS = Object.freeze(/** @type {Record<string, LexSpec>} */ ({
  python: PYTHON, py: PYTHON,
  c: CLIKE, h: CLIKE, cpp: CLIKE, 'c++': CLIKE, cc: CLIKE, hpp: CLIKE, java: CLIKE, cs: CLIKE, csharp: CLIKE,
  'c#': CLIKE, kotlin: CLIKE, kt: CLIKE, swift: CLIKE, scala: CLIKE, dart: CLIKE, groovy: CLIKE,
  javascript: JSLIKE, js: JSLIKE, jsx: JSLIKE, typescript: JSLIKE, ts: JSLIKE, tsx: JSLIKE,
  go: GO, golang: GO, rust: DQ_CLIKE, rs: DQ_CLIKE, verilog: DQ_CLIKE,
  bash: SHELL, sh: SHELL, shell: SHELL, zsh: SHELL,
  ruby: HASH, rb: HASH, r: HASH, perl: HASH,
  php: PHP, sql: SQL, matlab: MATLAB, octave: MATLAB,
}));

/** The lexer of a language, or null. @param {string | null | undefined} language */
export function lexerFor(language) {
  const key = String(language || '').trim().toLowerCase();
  return Object.prototype.hasOwnProperty.call(LEXERS, key) ? LEXERS[key] : null;
}

/** An operand character before a MATLAB quote makes it the transpose operator. */
const OPERAND_END = /[A-Za-z0-9_)\]}.']/;

/**
 * A program's lines as runs of `code`, `string` and `comment` (pure). Block comments and multi-line strings carry over
 * lines; any other string ends with its line (an unterminated one included). Without a lexer every line is one `code`
 * run. Empty runs are left out, so an empty line has none.
 * @param {string[]} lines
 * @param {LexSpec | null} spec
 * @returns {{text: string, kind: 'code' | 'string' | 'comment'}[][]}
 */
export function codeSegments(lines, spec) {
  if (!spec) return lines.map((line) => (line ? [{ text: line, kind: /** @type {'code'} */ ('code') }] : []));
  /** @type {{kind: 'comment', close: string} | {kind: 'string', q: QuoteSpec} | null} */
  let state = null;
  return lines.map((line) => {
    /** @type {{text: string, kind: 'code' | 'string' | 'comment'}[]} */
    const out = [];
    /** @param {string} text @param {'code' | 'string' | 'comment'} kind */
    const push = (text, kind) => {
      if (!text) return;
      const last = out[out.length - 1];
      if (last && last.kind === kind) last.text += text;
      else out.push({ text, kind });
    };
    let i = 0;
    while (i < line.length) {
      if (state && state.kind === 'comment') {
        const end = line.indexOf(state.close, i);
        if (end < 0) {
          push(line.slice(i), 'comment');
          i = line.length;
        } else {
          push(line.slice(i, end + state.close.length), 'comment');
          i = end + state.close.length;
          state = null;
        }
        continue;
      }
      if (state && state.kind === 'string') {
        const q = state.q;
        let j = i;
        let closed = false;
        while (j < line.length) {
          if (q.escape && line[j] === '\\') {
            j += 2;
            continue;
          }
          if (line.startsWith(q.q, j)) {
            j += q.q.length;
            closed = true;
            break;
          }
          j += 1;
        }
        push(line.slice(i, Math.min(j, line.length)), 'string');
        i = Math.min(j, line.length);
        if (closed) state = null;
        continue;
      }
      // code: a block comment, a line comment, a string, or one character of code
      const block = spec.block.find(([open]) => line.startsWith(open, i));
      if (block) {
        push(block[0], 'comment');
        i += block[0].length;
        state = { kind: 'comment', close: block[1] };
        continue;
      }
      const marker = spec.line.find((m) => line.startsWith(m, i));
      if (marker && (!spec.wordStart || i === 0 || /\s/.test(line[i - 1]))) {
        push(line.slice(i), 'comment');
        i = line.length;
        continue;
      }
      const q = spec.quotes.find((x) => line.startsWith(x.q, i));
      if (q && !(spec.transpose === q.q && i > 0 && OPERAND_END.test(line[i - 1]))) {
        push(q.q, 'string');
        i += q.q.length;
        state = { kind: 'string', q };
        continue;
      }
      push(line[i], 'code');
      i += 1;
    }
    // only a multi-line string and a block comment run on past their line
    if (state && state.kind === 'string' && !state.q.multi) state = null;
    return out;
  });
}

/** An ASCII identifier, never the tail of another word, a number or a `$` variable. */
const IDENTIFIER = /(?<![A-Za-z0-9_$])[A-Za-z_][A-Za-z0-9_]*/g;

/**
 * The runs of a `code` run with each identifier that names a lesson entity marked (`role`: its `entity:<n>` role,
 * `roleOf(identifier)`; exact and case-sensitive). Pure.
 * @param {string} text
 * @param {(identifier: string) => string | null} roleOf
 * @returns {{text: string, role: string | null}[]}
 */
export function entityRuns(text, roleOf) {
  /** @type {{text: string, role: string | null}[]} */
  const out = [];
  let at = 0;
  for (const m of text.matchAll(IDENTIFIER)) {
    const role = roleOf(m[0]);
    if (!role) continue;
    const i = /** @type {number} */ (m.index);
    if (i > at) out.push({ text: text.slice(at, i), role: null });
    out.push({ text: m[0], role });
    at = i + m[0].length;
  }
  if (at < text.length) out.push({ text: text.slice(at), role: null });
  return out;
}

/** Indic runs (and the characters that join them) inside a code line: drawn in the body stack (the mono face has
 * no Indic glyphs). */
const INDIC_RUN = /[\u0900-\u0DFF\u200c\u200d]+/g;

/**
 * Append `text` to `host` as text nodes, its Indic runs in spans set in the body stack.
 * @param {HTMLElement} host @param {string} text
 */
function appendScripted(host, text) {
  let at = 0;
  for (const m of text.matchAll(INDIC_RUN)) {
    const i = /** @type {number} */ (m.index);
    if (i > at) host.appendChild(document.createTextNode(text.slice(at, i)));
    host.appendChild(h('span', { class: 'dyn-cw-indic', text: m[0], style: { fontFamily: 'var(--dyn-font-body)' } }));
    at = i + m[0].length;
  }
  if (at < text.length) host.appendChild(document.createTextNode(text.slice(at)));
}

// ---------------------------------------------------------------------------------------------
// The editor's colours
// ---------------------------------------------------------------------------------------------

/** @type {RegExp} */
const HEX = /^#[0-9a-f]{6}$/i;

/**
 * The editor's surface and the roles of the text on it (§4.3 "a dark surface in every family"): the look's
 * `code.surface` with the dark palette (`roles.on('dark')`, the scene's own roles in a dark family); without the token
 * (or without a dark palette in the timeline), the scene's own `surface` and roles. `mode`: the palette's mode.
 * @param {TemplateContext} ctx
 * @returns {{surface: string, roles: Roles, mode: 'dark' | 'light'}}
 */
export function editorLook(ctx) {
  const roles = ctx.roles;
  const token = roles.tokens ? String(roles.tokens[CODE_SURFACE_TOKEN] || '') : '';
  if (HEX.test(token)) {
    if (roles.mode === 'dark') return { surface: token, roles, mode: 'dark' };
    try {
      return { surface: token, roles: roles.on('dark'), mode: 'dark' };
    } catch {
      // no dark palette in the timeline (presentation.content_alt): the scene's own surface and roles below
    }
  }
  return { surface: roles.color('surface'), roles, mode: roles.mode };
}

/**
 * The slots of this template (templates.py SlotSpec, what textfit.py fits; plan §1.6, §1.11): slot -> format ->
 * variant -> {w, max_lines, size, role, face, weight, line_height, prefer_lines, max_chars}. `heading` one line in the
 * title zone's width; `code` per layout variant (`default`, `headed`, and in 16:9 `panel`, `headed_panel`): the code
 * column's width at the minimum size, the most Latin lines its box holds, the characters per line, mono, at the Latin
 * code line height (an Indic program takes CODE_LH.indic, as textfit's Indic measure does); `terminal` the panel's card
 * (16:9 only). Pure.
 * @param {Zones} landscape @param {Zones} portrait
 */
export function slotRows(landscape, portrait) {
  /** @param {'landscape' | 'portrait'} fmt @param {Zones} zones */
  const heading = (fmt, zones) => ({
    w: zones.title.x1 - zones.title.x0, max_lines: LAYOUT[fmt].heading.lines,
    size: [LAYOUT[fmt].heading.min, LAYOUT[fmt].heading.max], role: 'title', face: 'display', weight: 700,
    line_height: HEADING_LH.latin, prefer_lines: null, max_chars: null,
  });
  /** @param {'landscape' | 'portrait'} fmt @param {Zones} zones @param {boolean} headed @param {boolean} panel */
  const code = (fmt, zones, headed, panel) => {
    const L = LAYOUT[fmt];
    const b = boxes(fmt, zones, headed, panel);
    const box = codeBox(fmt, zones);
    return {
      w: textWidth(fmt, b, L.code.min), max_lines: Math.floor((headed ? box.headedPx : box.linesPx) / box.pitch),
      size: [L.code.min, L.code.max], role: 'body', face: 'mono', weight: L.weight, line_height: CODE_LH.latin,
      prefer_lines: null, max_chars: panel ? box.charsPanel : box.chars,
    };
  };
  const right = region({ format: 'landscape' }, 'right-45');
  return {
    heading: { landscape: { default: heading('landscape', landscape) }, portrait: { default: heading('portrait', portrait) } },
    code: {
      landscape: {
        default: code('landscape', landscape, false, false), headed: code('landscape', landscape, true, false),
        panel: code('landscape', landscape, false, true), headed_panel: code('landscape', landscape, true, true),
      },
      portrait: { default: code('portrait', portrait, false, false), headed: code('portrait', portrait, true, false) },
    },
    // the terminal panel's card (16:9 only), mono, in the right 45% less its padding; `_shared.js layoutPanel` fits it
    // on the page and `_shared.js panelTexts` reports it in this slot
    terminal: {
      landscape: {
        default: {
          w: (right.x1 - right.x0) - 2 * PANEL_TERM.landscape.pad, max_lines: null,
          size: [PANEL_TERM.landscape.min, PANEL_TERM.landscape.max], role: 'body', face: 'mono',
          weight: PANEL_TERM.landscape.weight, line_height: PANEL_TERM.landscape.lh, prefer_lines: null, max_chars: null,
        },
      },
    },
  };
}

/** The heading's line height for `text`. @param {string} text */
export function headingLineHeight(text) {
  return hasIndic(plainText(String(text || ''))) ? HEADING_LH.indic : HEADING_LH.latin;
}

/** @param {TemplateContext} ctx */
const isFallback = (ctx) => !!(ctx.view && ctx.view.cwFallback);

// ---------------------------------------------------------------------------------------------
// The template
// ---------------------------------------------------------------------------------------------

/** @type {TemplateModule & {cueMode: (ctx: TemplateContext, target: string) => 'dark' | 'light' | null}} */
export default {
  id: ID,

  /**
   * The DOM, once per scene (no measuring): the heading, the editor's frame, a line element per code line (gutter
   * number and code, its comments and entity identifiers coloured), and the terminal panel.
   * @param {TemplateContext} ctx
   */
  build(ctx) {
    const { scene, format } = ctx;
    if (!drawsScene(scene, format, ctx.zones)) {
      // a board this template does not draw whole: board_generic draws it, every hook handed on
      const inner = boardGeneric.build(ctx);
      inner.cwFallback = true;
      return inner;
    }
    const L = LAYOUT[format];
    const { code, heading } = codeItems(scene);
    const item = /** @type {BoardItem} */ (code);
    const editor = editorLook(ctx);
    const baseline = !!ctx.motion && ctx.motion.entrance === 'baseline_reveal';
    const fixes = fixesOf(ctx.look);
    const root = h('div', {
      class: ['dyn-cw', `dyn-cw--${format}`],
      style: { position: 'absolute', left: '0px', top: '0px', right: '0px', bottom: '0px' },
    });

    // --- the heading (text on the background; R-53's scrim remedy behind it when preflight asked)
    /** @type {any} */
    let head = null;
    if (heading) {
      const lh = headingLineHeight(heading.text || '');
      const text = h('h2', {
        class: 'dyn-cw-heading', dir: 'auto',
        style: {
          margin: '0px', fontFamily: 'var(--dyn-font-display)', fontWeight: '700', lineHeight: String(lh),
          overflowWrap: 'anywhere', color: ctx.onBackground.ink,
        },
      });
      const { words, tex: texFlags } = richWords(ctx, text, String(heading.text || ''));
      const mask = baseline ? revealMask(text) : null;
      const scrim = fixes.slots.heading && fixes.slots.heading.scrim ? ctx.roles.color('scrim') : null;
      const el = group('dyn-cw-heading-group', mask || text);
      el.style.position = 'absolute';
      if (scrim) {
        Object.assign(el.style, {
          padding: '10px 24px 14px', marginLeft: '-24px', borderRadius: `${RADIUS.card}px`, background: scrim,
        });
      }
      root.appendChild(el);
      head = { item: heading, el, text, mask, words, tex: texFlags, lh, scrim, size: L.heading.max, min: L.heading.min, overflow: 0, alpha: 0 };
    }

    // --- the editor: its frame (a dim group of its own), then a dim group per line above it
    const frame = h('div', {
      class: 'dyn-cw-frame',
      style: {
        position: 'absolute', boxSizing: 'border-box', borderRadius: `${RADIUS.card}px`, background: editor.surface,
        boxShadow: FRAME_SHADOW,
      },
    });
    const frameGroup = group('dyn-cw-frame-group', frame);
    Object.assign(frameGroup.style, { position: 'absolute', left: '0px', top: '0px', right: '0px', bottom: '0px' });
    root.appendChild(frameGroup);
    const ink = editor.roles.color('ink');
    const ink2 = editor.roles.color('ink2');
    const codeLayer = h('div', {
      class: 'dyn-cw-code',
      style: {
        position: 'absolute', left: '0px', top: '0px', right: '0px', bottom: '0px', fontFamily: 'var(--dyn-font-mono)',
        fontWeight: String(L.weight), fontVariantLigatures: 'none', color: ink, fontSize: `${L.code.max}px`,
      },
    });
    root.appendChild(codeLayer);
    const texts = codeLines(String(item.code || ''));
    const indic = hasIndic(String(item.code || ''));
    const segments = codeSegments(texts, lexerFor(item.language));
    const lexed = !!lexerFor(item.language);
    /** @param {string} id @returns {string | null} */
    const roleOf = (id) => (lexed && typeof ctx.entityRole === 'function' ? ctx.entityRole(id) : null);
    const lines = texts.map((text, k) => {
      const num = h('span', {
        class: 'dyn-cw-num', text: String(k + 1), 'aria-hidden': 'true',
        style: { display: 'inline-block', flex: 'none', textAlign: 'right', color: ink2, fontVariantNumeric: 'tabular-nums' },
      });
      const span = h('span', {
        class: 'dyn-cw-text', dir: 'ltr', dataset: { line: String(k) },
        style: { display: 'inline-block', flex: 'none', whiteSpace: 'pre' },
      });
      // the line's indentation apart from its code, so a bar points at the code and not at the spaces before it
      const segs = segments[k].map((seg) => ({ ...seg }));
      const lead = /^ +/.exec(text);
      if (lead && segs.length && segs[0].kind === 'code' && segs[0].text.startsWith(lead[0])) {
        span.appendChild(h('span', { class: 'dyn-cw-indent', text: lead[0] }));
        segs[0].text = segs[0].text.slice(lead[0].length);
      }
      const body = h('span', { class: 'dyn-cw-body' });
      span.appendChild(body);
      for (const seg of segs) {
        if (seg.kind === 'comment') {
          const c = h('span', { class: 'dyn-cw-comment', style: { color: ink2 } });
          appendScripted(c, seg.text);
          body.appendChild(c);
        } else if (seg.kind === 'string') {
          const s = h('span', { class: 'dyn-cw-string' });
          appendScripted(s, seg.text);
          body.appendChild(s);
        } else {
          for (const run of entityRuns(seg.text, roleOf)) {
            if (run.role) {
              body.appendChild(h('span', { class: 'dyn-cw-entity', text: run.text, style: { color: editor.roles.color(run.role) } }));
            } else {
              appendScripted(body, run.text);
            }
          }
        }
      }
      const row = h('div', {
        class: 'dyn-cw-row', style: { display: 'flex', alignItems: 'center', height: '100%' },
      }, num, span);
      const el = group('dyn-cw-line', row);
      Object.assign(el.style, { position: 'absolute', whiteSpace: 'pre' });
      codeLayer.appendChild(el);
      return { k, text, el, row, num, span, body, top: 0, pitch: 0, width: 0, overflow: 0, alpha: 0 };
    });

    // --- the terminal panel beside the editor (16:9), entering on the first `output` event
    /** @type {import('./_shared.js').PanelParts | null} */
    let panelParts = null;
    const panel = panelFor(scene, format, ctx.mode);
    if (panel) {
      panelParts = buildPanel(ctx, panel);
      root.appendChild(panelParts.el);
    }

    ctx.root.appendChild(root);
    /** @type {Map<string, number>} */
    const enter = new Map(itemTimings(scene).map((it) => [it.id, it.enter]));
    return {
      root, head, item, editor, frame, frameGroup, codeLayer, lines, indic, panel: panelParts, enter,
      headed: !!heading, size: L.code.max, min: L.code.min, gutter: gutterAt(L.code.max), textW: 0, frameAlpha: 0,
      laidOut: false,
    };
  },

  /**
   * Measure and fit (runs again when a late face arrives; idempotent): the heading in its reserved line, then the
   * code at the largest size whose line boxes fit the editor's height and whose widest line fits its code column;
   * the editor is then sized (16:9 as wide as that line needs, at least the centre 60%) and centred in its area, and
   * every line placed on its whole-pixel pitch. The panel takes the right 45%.
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
    const b = boxes(format, zones, v.headed, !!v.panel);

    // --- the heading, fitted to one line in its reserved box
    if (v.head) {
      const hd = v.head;
      const range = slotRange(ctx, 'heading', { min: L.heading.min, max: L.heading.max, role: 'title' });
      Object.assign(hd.el.style, {
        left: `${b.heading.x0}px`, top: `${b.heading.y0}px`, width: `${b.heading.x1 - b.heading.x0}px`,
      });
      const fit = fitLines(hd.text, { max: range.max, min: range.min, lines: L.heading.lines, lh: hd.lh, step: SIZE_STEP }, hd.words, hd.tex);
      hd.size = fit.size;
      hd.min = range.min;
      hd.overflow = fit.overflow;
    }

    // --- the code: the largest size at which every line box and the widest line fit
    const range = slotRange(ctx, 'code', { min: L.code.min, max: L.code.max, role: 'body' });
    const avail = b.linesBottom - b.linesTop;
    const n = v.lines.length;
    let chosen = range.min;
    /** @type {number[]} */
    let widths = [];
    for (const s of sizeSteps(range.max, range.min)) {
      v.codeLayer.style.fontSize = `${s}px`;
      widths = v.lines.map((/** @type {any} */ line) => line.span.offsetWidth || 0);
      const wide = Math.max(0, ...widths);
      chosen = s;
      if (n * pitchAt(s, v.indic) <= avail && wide <= textWidth(format, b, s)) break;
    }
    v.size = chosen;
    v.min = range.min;
    const pitch = pitchAt(chosen, v.indic);
    const gutter = gutterAt(chosen);
    const textW = textWidth(format, b, chosen);
    const wide = Math.max(0, ...widths);
    v.gutter = gutter;
    v.textW = textW;

    // --- the editor's box: 16:9 alone as wide as the code needs (centred, at least the centre 60%); 16:9 beside a
    // panel the left 55%; 9:16 the code column plus the gutter and padding round it
    /** @type {number} */
    let x0;
    /** @type {number} */
    let x1;
    if (format === 'portrait') {
      x0 = b.area.x0 - L.gap - gutter - L.padX;
      x1 = b.area.x1 + L.padX;
    } else if (v.panel) {
      x0 = b.area.x0;
      x1 = b.area.x1;
    } else {
      const most = b.area.x1 - b.area.x0;
      const w = Math.min(most, Math.max(b.minWidth, 2 * L.padX + gutter + L.gap + Math.ceil(Math.min(wide, textW))));
      x0 = Math.round(b.area.x0 + (most - w) / 2);
      x1 = x0 + w;
    }
    // under a heading the editor starts at its area's top (it belongs to the heading); alone it is centred
    const linesH = n * pitch;
    const frameH = linesH + 2 * L.padY;
    const room = (b.linesBottom + L.padY) - b.codeTop;
    const y0 = v.headed ? b.codeTop : Math.round(b.codeTop + Math.max(0, (room - frameH) / 2));
    Object.assign(v.frame.style, { left: `${x0}px`, top: `${y0}px`, width: `${x1 - x0}px`, height: `${frameH}px` });
    const lineX = format === 'portrait' ? b.area.x0 - L.gap - gutter : x0 + L.padX;
    const lineW = format === 'portrait' ? gutter + L.gap + (b.area.x1 - b.area.x0) : (x1 - x0) - 2 * L.padX;
    v.lines.forEach((/** @type {any} */ line, k) => {
      const top = y0 + L.padY + k * pitch;
      Object.assign(line.el.style, {
        left: `${lineX}px`, top: `${top}px`, width: `${lineW}px`, height: `${pitch}px`, lineHeight: `${pitch}px`,
      });
      line.num.style.width = `${gutter}px`;
      line.num.style.marginRight = `${L.gap}px`;
      line.top = top;
      line.pitch = pitch;
      line.width = widths[k] || 0;
      // what does not fit is reported, never clipped: a line wider than the code column, a line box below the band
      const over = Math.max(0, (widths[k] || 0) - textW);
      const below = Math.max(0, top + pitch - b.linesBottom);
      line.overflow = Math.max(over > 2 ? over : 0, below);
    });

    // --- the terminal panel in the right 45%: centred on the editor when its card is no taller, else from the
    // editor's top down when it fits there, else in the whole box (where `_shared.js layoutPanel` fits it and reports
    // what still does not fit)
    if (v.panel && b.visual) {
      const whole = layoutPanel(ctx, v.panel, b.visual);
      const cardH = whole.y1 - whole.y0;
      if (cardH <= frameH) layoutPanel(ctx, v.panel, { x0: b.visual.x0, y0, x1: b.visual.x1, y1: y0 + frameH });
      else if (y0 + cardH <= b.visual.y1) layoutPanel(ctx, v.panel, { x0: b.visual.x0, y0, x1: b.visual.x1, y1: y0 + cardH });
    }
    v.laidOut = true;
  },

  /**
   * Paint scene time t: the heading's entrance, the editor's rise, each line's fade (one every LINE_STEP once the
   * editor has settled) and the terminal panel. A pure function of t: every property painted on an element is painted
   * on every frame, with a value (never '').
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
    if (v.head) {
      const e = enterAt(ctx, t, v.enter.get(v.head.item.id) || 0, HEADING_ENTER);
      v.head.alpha = e;
      paintTextEntrance(ctx, v.head.text, v.head.mask, e);
    }
    const start = v.enter.get(v.item.id) || 0;
    const e = enterAt(ctx, t, start, FRAME_ENTER);
    v.frameAlpha = e;
    paintRise(ctx, v.frame, e, FRAME_RISE);
    p.style(v.frame, 'visibility', e > 0 ? 'visible' : 'hidden');
    for (const line of v.lines) {
      const a = enterAt(ctx, t, lineStart(ctx, start, line.k), LINE_FADE);
      line.alpha = a;
      p.style(line.row, 'opacity', fx(a));
      p.style(line.row, 'visibility', a > 0 ? 'visible' : 'hidden');
    }
    if (v.panel) paintPanel(ctx, v.panel, t, state);
  },

  /**
   * Where a look_here points (plan §1.4, §1.5, §1.7), as painted now (read after `update`), on whole stage px: the
   * code item - the editor's frame (its lines kept bright); `<item>:line:<n>` - line n (0-based) as a bar round its
   * code (after its indentation) from its cap top to under its baseline, the frame kept bright (null for an empty line or one not on screen yet); the heading's text; the panel and
   * a terminal row (`_shared.js panelFocusRect`).
   * @param {TemplateContext} ctx
   * @param {string} target
   * @returns {FocusRect | null}
   */
  focusRect(ctx, target) {
    if (isFallback(ctx)) return typeof boardGeneric.focusRect === 'function' ? boardGeneric.focusRect(ctx, target) : null;
    const v = ctx.view;
    const text = String(target || '');
    const parts = text.split(':');
    const id = parts[0];
    // `panel` is a reserved target word (plan §1.7), so no board item can have that id
    if (id === 'panel') return v.panel ? panelFocusRect(ctx, v.panel, text) : null;
    /** @param {{x: number, y: number, w: number, h: number} | null} r */
    const whole = (r) => {
      if (!r || !(r.w > 0 && r.h > 0)) return null;
      const x = Math.round(r.x);
      const y = Math.round(r.y);
      return { x, y, w: Math.round(r.x + r.w) - x, h: Math.round(r.y + r.h) - y };
    };
    if (id === v.item.id) {
      if (parts.length === 1) {
        const rect = v.frameAlpha > 0.01 ? whole(ctx.rectOf(v.frame)) : null;
        return rect ? { rect, shape: 'rect', radius: RADIUS.card, el: v.frameGroup, kind: 'shape', keep: v.lines.map((/** @type {any} */ l) => l.el) } : null;
      }
      if (parts.length !== 3 || parts[1] !== 'line' || !/^(0|[1-9][0-9]*)$/.test(parts[2])) return null;
      const line = v.lines[Number(parts[2])];
      if (!line || !line.text || line.alpha <= 0.01) return null;
      // across the line's code (its indentation left out), from its cap top to just under its baseline (`barBand`):
      // the pill then sits between its neighbours' ink
      const r = ctx.rectOf(line.body);
      const box = ctx.rectOf(line.el);
      if (!r || !box || !(r.w > 0 && box.h > 0 && line.pitch > 0)) return null;
      const k = box.h / line.pitch; // page px -> stage px (1 on the render page)
      const band = barBand(line.pitch, v.size);
      const rect = whole({ x: r.x, y: box.y + band.top * k, w: Math.max(r.w, v.size * k), h: band.height * k });
      return rect ? { rect, shape: 'bar', radius: 0, el: line.el, kind: 'shape', keep: [v.frameGroup] } : null;
    }
    if (v.head && v.head.item.id === id && parts.length === 1) {
      const rect = v.head.alpha > 0.01 ? whole(ctx.rectOf(v.head.text)) : null;
      return rect ? { rect, shape: 'rect', radius: 0, el: v.head.el, kind: 'text' } : null;
    }
    return null;
  },

  /**
   * The colour mode of the surface a look_here target is drawn on (cues.js colours its outline from that mode's
   * `focus`): 'dark' for the editor and its lines when the editor is dark in a light family; null (the scene's own)
   * for anything else.
   * @param {TemplateContext} ctx
   * @param {string} target
   * @returns {'dark' | 'light' | null}
   */
  cueMode(ctx, target) {
    if (isFallback(ctx)) return null;
    const v = ctx.view;
    const id = String(target || '').split(':')[0];
    return id === v.item.id && v.editor.mode !== ctx.roles.mode ? v.editor.mode : null;
  },

  /**
   * The elements holding an item's text, where cue.term finds its [[term]] spans: the heading's text. Code has none
   * (it is literal: no rich-lite markup is read in it).
   * @param {TemplateContext} ctx
   * @param {string} itemId
   * @returns {Element | Element[] | null}
   */
  itemEl(ctx, itemId) {
    if (isFallback(ctx)) return typeof boardGeneric.itemEl === 'function' ? boardGeneric.itemEl(ctx, itemId) : null;
    const v = ctx.view;
    return v.head && v.head.item.id === itemId ? v.head.text : null;
  },

  /**
   * The content groups a look_here dims (all but the target's): the heading, the editor's frame, every line and the
   * panel; the same elements at every t. This template writes the opacity of what is inside them, never theirs.
   * @param {TemplateContext} ctx
   * @returns {Element[]}
   */
  dimGroups(ctx) {
    if (isFallback(ctx)) return typeof boardGeneric.dimGroups === 'function' ? boardGeneric.dimGroups(ctx) : [];
    const v = ctx.view;
    return [v.head ? v.head.el : null, v.frameGroup, ...v.lines.map((/** @type {any} */ l) => l.el), v.panel ? v.panel.el : null]
      .filter(Boolean);
  },

  /** The shared-element morph's anchor (B2f): none. */
  anchor() {
    return null;
  },

  /**
   * Static: the box a medium of `slot` is sized for. This template shows no medium (its only panel is a terminal
   * card); a scene it hands to board_generic gets board_generic's box, so `mediaPlan` and `ctx.media` agree.
   * @param {TimedScene} scene
   * @param {Zones} zones
   * @param {'live' | 'render'} mode
   * @param {string | undefined} slot
   * @param {import('../core.js').Fixes} fixes
   * @returns {{w: number, h: number} | null}
   */
  mediaBox(scene, zones, mode, slot, fixes) {
    if (!drawsScene(scene, zones.format, zones)) {
      return typeof boardGeneric.mediaBox === 'function' ? boardGeneric.mediaBox(scene, zones, mode, slot, fixes) : null;
    }
    return null;
  },

  /**
   * Static: every TeX string this template converts for the scene (`texOf`: the heading's; board_generic's for a
   * scene it hands on).
   * @param {TimedScene} scene
   * @param {import('../../shared/types.js').ResolvedLook | null} look
   * @param {'landscape' | 'portrait'} fmt
   */
  tex(scene, look, fmt) {
    if (!drawsScene(scene, fmt)) return typeof boardGeneric.tex === 'function' ? boardGeneric.tex(scene, look, fmt) : [];
    return texOf(scene);
  },

  /**
   * The text on screen (preflight, §7.12): the heading (slot `heading`, on the background's worst case or its scrim),
   * each code line (slot `code`, critical, in the editor's ink on its surface, at the size and weight laid out, with
   * the px it overflows), each line number (not critical: a reference aid, in `ink2`) and the terminal panel's card
   * (slot `terminal`).
   * @param {TemplateContext} ctx
   * @returns {TextEntry[]}
   */
  texts(ctx) {
    if (isFallback(ctx)) return typeof boardGeneric.texts === 'function' ? boardGeneric.texts(ctx) : [];
    const v = ctx.view;
    const L = LAYOUT[ctx.format];
    const onBg = ctx.onBackground;
    /** @type {TextEntry[]} */
    const out = [];
    if (v.head) {
      const hd = v.head;
      out.push({
        el: hd.text, role: 'title', slot: 'heading', critical: true, ink: onBg.ink,
        bg: hd.scrim ? scrimmedBg(hd.scrim, onBg, onBg.ink) : onBg.bg, alpha: hd.alpha, overflow: hd.overflow,
        size: hd.size, min: hd.min ?? hd.size, weight: 700, text: plainText(String(hd.item.text || '')),
      });
    }
    const ink = v.editor.roles.color('ink');
    const ink2 = v.editor.roles.color('ink2');
    for (const line of v.lines) {
      const alpha = v.frameAlpha * line.alpha;
      if (line.text.trim()) {
        out.push({
          el: line.span, role: 'body', slot: 'code', critical: true, ink, bg: v.editor.surface, alpha,
          overflow: line.overflow, size: v.size, min: v.min, weight: L.weight, text: line.text,
        });
      }
      out.push({
        el: line.num, role: 'body', critical: false, ink: ink2, bg: v.editor.surface, alpha, size: v.size,
        min: v.min, weight: L.weight, text: String(line.k + 1),
      });
    }
    if (v.panel) out.push(...panelTexts(ctx, v.panel));
    return out;
  },
};
