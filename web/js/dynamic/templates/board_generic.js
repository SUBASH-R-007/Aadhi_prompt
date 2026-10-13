// @ts-check
/**
 * `board_generic` (DYNAMIC_VIDEO_DESIGN §4.3 D, R1 since B1, class `fallback`): stacked cards with at most 3 visible
 * (2 in 9:16), older ones collapsed into a "previous" chip, one reveal per beat, and a media-split layout when the
 * scene has a picture or a clip. In B1 it is the only template, so it also draws the other scene types the
 * showcase needs: a chapter card as a title card, a quiz as its question with option cards (countdown ring, then the
 * answer), and a media scene as its picture or clip with the title.
 *
 * Layout follows the safe zones of each format (R-59): 16:9 content in x 96-1824, y 54-912; 9:16 text in x 192-888
 * above y 840 and x 120-780 below (one text column x 192-780 for the whole card stack). Sizes follow R-29.
 * Motion (§4.8): the title's words rise in with a 70 ms stagger (ease.enter, 450 ms); a card enters 120 ms before
 * its anchor (fade + 24 px rise, 500 ms); the oldest card leaves 30% faster (350 ms) just before a new one needs its
 * room, and the stack slides up with it. Everything is a pure function of the scene time.
 */

import { h, svg } from '../../shared/dom.js';
import { ease } from '../../motion/easing.js';
import { progress } from '../../motion/track.js';
import { composite, contrastRatio, mixOklch, toOklch } from '../../motion/color.js';
import { splitWords } from '../../motion/text.js';
import { strokeDash } from '../../motion/draw.js';
import { mathSpans, mountTex, parseRich, plainText, renderRich } from '../../player/richtext.js';
import { icon } from '../../player/icons.js';
import { planScene } from '../../player/schedule.js';
import { SCRIM_ALPHA_HEX, TYPE_FLOORS, fixesOf, fx, templateSlots } from '../core.js';
import { shownIn } from '../media.js';
import { itemTimings, INITIAL_DELAY, INITIAL_STAGGER, REVEAL_LEAD } from '../state.js';

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

/** UI labels (kept together for localisation). */
export const LABELS = Object.freeze({
  info: 'Info',
  tip: 'Tip',
  warning: 'Watch out',
  misconception: 'Myth',
  correction: 'Fact',
  takeaway: 'Key takeaway',
  step: 'Step',
  blank: '______',
  figureMissing: 'Figure unavailable',
  quiz: 'Quick check',
  answer: 'Answer',
  /** @param {number} n */
  earlier: (n) => `+${n} earlier`,
  badges: /** @type {Record<string, string>} */ ({
    example: 'Worked example',
    summary: 'Summary',
    key_takeaway: 'Key takeaways',
    recap: 'Recap',
  }),
});

// Motion (§4.8)
export const TITLE_DELAY = 0.08;
export const WORD_STAGGER = 0.07;
export const WORD_STAGGER_TOTAL = 0.8; // long titles compress their stagger
export const WORD_SECONDS = 0.45;
export const CARD_ENTER = 0.5;
export const CARD_EXIT = 0.35; // exits are 30% shorter than entrances
export const PAST_SECONDS = 0.4;
export const RISE = 24;
export const MEDIA_ENTER = 0.6;
export const REVEAL_SECONDS = 0.35;
/** In 9:16 the fact of a misconception enters this long after its myth (R-63: the fact comes last and dominates). */
export const FACT_DELAY = 1.5;
/** After the answer is revealed, the wrong options leave this long later (the comparison stays readable first). */
export const QUIZ_CLEAR = 0.9;
/** Smallest font scale when fitting a crowded stack; text never goes below 36 px in 16:9 or 40 px in 9:16 (R-29;
 * the CSS `--min-text` floor). */
export const MIN_FIT = Object.freeze({ landscape: 0.75, portrait: 0.72 });
/**
 * A per-format table of one of R-29's floors (aadhi/compose/dynamic/zones.py TYPE_FLOORS, through core.js).
 * @param {'text_min' | 'label_min'} key
 */
const floors = (key) => Object.freeze({ landscape: TYPE_FLOORS.landscape[key], portrait: TYPE_FLOORS.portrait[key] });
/** Text never goes below these sizes (R-29: "never < 36" in 16:9, "never < 40" in 9:16). */
export const MIN_TEXT = floors('text_min');
/** Labels (the chip, card labels, option letters) never go below these sizes (R-29; the CSS `--min-label`). */
export const MIN_LABEL = floors('label_min');
/** This template's text slots in the catalogue's registry (aadhi/compose/dynamic/templates.py, which mirrors LAYOUT
 * below; web/tests/dynamic/catalogue.test.js pins the two against each other). */
const SLOTS = /** @type {Record<string, Record<string, Record<string, import('../core.js').SlotSpec>>>} */ (
  templateSlots('board_generic'));
/** Card text weight (R-29: in 9:16, text at <= 64 px is at least 600; dynamic.css): the catalogue's body slot. */
export const BODY_WEIGHT = Object.freeze({
  landscape: SLOTS.body.landscape.default.weight,
  portrait: SLOTS.body.portrait.default.weight,
});
/** A full stack may shrink this far before fewer cards are shown at a time instead. */
export const GENTLE_FIT = 0.86;
/** In 9:16 a table with more columns than this is drawn one line per row (`stackedTable`), not as a grid. */
export const STACK_TABLE_COLUMNS = 2;
/** A card is fully shown at least this long before it may start to collapse (a card no beat reveals is re-timed to
 * get it; see `layout`), so no card leaves before it was read. */
export const MIN_DWELL = 1.5;
/** Gap between cards of a crowded quiz (a long question): its option cards also lose some padding (`is-compact`). */
export const COMPACT_GAP = Object.freeze({ landscape: 14, portrait: 10 });
/** The quiz countdown ring's box (dynamic.css `.dyn-ring`, the SVG's 96 x 96 view box), stage px. */
export const RING_SIZE = 96;
/** In 16:9 the ring sits at the top right of the content box, level with the kicker and the first title line: the
 * title block ends this far left of it (with the ring's width), so a long question or kicker never runs under it. */
export const RING_GAP = 24;
/** Opacity of the ring's track in the ink it sits on: with a light ink (dark families) and with a dark ink (light
 * families) the track then makes the same step off the family's background (OKLab ΔL about 0.165, 1.7:1). A white
 * track at 18% on a light family's background was invisible (1.02:1). */
export const RING_TRACK_ALPHA = Object.freeze({ lightInk: 0.18, darkInk: 0.25 });
/** The slots `texts()` reports the title block's text in (§7.12: preflight plans its remedies per reported slot): a
 * scrim planned for either is the title block's scrim, behind the kicker, the title and a chapter card's subtitle.
 * The subtitle is reported in slot `title`, so a scrim it needs is planned, and drawn, through slot `title`. */
export const TITLE_BLOCK_SLOTS = Object.freeze(['kicker', 'title']);
/** The title block's scrim (R-53 remedy) is the scene's `scrim` role (core.js): the look's stored scrim, else the
 * scene's background tone at this alpha (0xc7, 78%; R-53: >= 55%). */
export { SCRIM_ALPHA_HEX };
/** Corner radius of a card (dynamic.css `.dyn-card`; the catalogue's layout radius `card`), stage px: `focusRect`'s. */
export const CARD_RADIUS = 24;
/** Corner radius of the media frame (dynamic.css `.dyn-frame`: B1's 28, not the catalogue's 24; A2 open item 6). */
export const FRAME_RADIUS = 28;
/** A figure card's picture is sized for this box (its height LAYOUT[format].figureMax): `mediaBox` slot 'figure'. */
export const FIGURE_BOX_WIDTH = 1200;
/** The `ctx.media` slot of a figure card's picture (scene.figures; media.js sceneClips lists no figure). */
export const FIGURE_SLOT = 'figure';

/**
 * Per-format layout (stage px). Sizes follow R-29: 16:9 body 52 (48-56), labels >= 40, headings 84-120; 9:16
 * body 56-64, labels >= 44, hook headline 88-104.
 */
export const LAYOUT = Object.freeze({
  landscape: Object.freeze({
    kicker: 40,
    title: { max: 96, min: 72, lines: 2, lh: 1.06 },
    titleQuiz: { max: 76, min: 56, lines: 3, lh: 1.12 },
    titleCard: { max: 148, min: 96, lines: 3, lh: 1.02 },
    body: 52,
    gap: 24,
    titleGap: 40,
    maxVisible: 3,
    splitMaxVisible: 3,
    split: 0.55, // cards take the left 55% (left-55 + right-45, §2.5)
    splitGap: 48,
    figureMax: 400,
  }),
  portrait: Object.freeze({
    kicker: 44,
    title: { max: 88, min: 56, lines: 2, prefer: 1, lh: 1.06 }, // R-25: a 1-line headline when it fits at >= 56 px
    titleQuiz: { max: 68, min: 50, lines: 4, lh: 1.12 },
    titleCard: { max: 112, min: 72, lines: 4, lh: 1.04 },
    body: 56,
    gap: 20,
    titleGap: 28,
    maxVisible: 2,
    splitMaxVisible: 1,
    split: 0.55, // media takes at most the top 55% of the stage (what the tallest card leaves, at least 30%)
    splitGap: 28,
    figureMax: 380,
  }),
});

// ---------------------------------------------------------------------------------------------
// What the scene shows (pure)
// ---------------------------------------------------------------------------------------------

/**
 * The picture or clip the scene shows in its media frame: the main media (simulation / AI video), the poster of an
 * interactive scene, else a side panel's picture or clip: the first of them that `mode` can show (`media.shownIn`:
 * the render page leaves out live-only GIFs and media kept out of the MP4, and the scene is laid out without them
 * rather than around an empty frame). Null without one.
 * @param {TimedScene} scene
 * @param {'live' | 'render'} [mode]
 * @returns {{slot: 'main' | 'panel', ref: MediaRef, start: number} | null}
 */
export function mediaOf(scene, mode = 'live') {
  const m = mode === 'render' ? 'render' : 'live';
  if (scene.media && shownIn(scene.media, m)) return { slot: 'main', ref: scene.media, start: 0 };
  if (scene.type === 'interactive' && scene.poster && shownIn(scene.poster, m)) {
    return { slot: 'main', ref: scene.poster, start: 0 };
  }
  const sp = scene.side_panel;
  if (sp && sp.media && scene.layout?.show_side_panel !== false && shownIn(sp.media, m)) {
    return { slot: 'panel', ref: sp.media, start: Math.max(0, Number(sp.show_at) || 0) };
  }
  return null;
}

/**
 * @typedef {object} CardSpec
 * @property {string} id
 * @property {string} kind      a board item kind, 'option', 'explanation' or 'terminal'
 * @property {number} enter     scene-relative entrance start
 * @property {BoardItem} [item]
 * @property {number} [option]  quiz option index
 * @property {number | null} [fill]
 * @property {'myth' | 'fact'} [part]  9:16 shows a misconception as two cards: the myth, then the fact
 * @property {boolean} anchored  it enters on a narration moment (a beat, an event, the quiz clock); false for an item no
 *                               beat reveals (it enters with the scene), which the stack may re-time (`MIN_DWELL`)
 * @property {string} [lead]    the card it follows FACT_DELAY later (a 9:16 fact after its myth)
 */

/**
 * The cards of a scene, in entrance order. In 9:16 a misconception with its correction becomes two cards, the fact
 * entering FACT_DELAY after the myth (one narrow card would not fit the reel's column).
 * @param {TimedScene} scene
 * @param {'landscape' | 'portrait'} [format]
 * @returns {CardSpec[]}
 */
export function cardSpecs(scene, format = 'landscape') {
  /** @type {CardSpec[]} */
  const out = [];
  const quiz = scene.quiz;
  if (quiz) {
    quiz.options.forEach((_, i) => out.push({ id: `option:${i}`, kind: 'option', option: i, enter: INITIAL_DELAY + 0.1 + INITIAL_STAGGER * i, anchored: true }));
    // the answer: wrong options step back, then leave, and the explanation takes their room
    if (quiz.explanation) out.push({ id: 'explanation', kind: 'explanation', enter: Number(quiz.reveal_start) + QUIZ_CLEAR + CARD_EXIT, anchored: true });
    return out;
  }
  const items = new Map((scene.board || []).map((i) => [i.id, i]));
  for (const t of itemTimings(scene)) {
    const item = items.get(t.id);
    if (!item) continue;
    const anchored = !t.initial;
    if (format === 'portrait' && item.kind === 'misconception' && item.justification) {
      out.push({ id: item.id, kind: item.kind, item, enter: t.enter, fill: t.fill, part: 'myth', anchored });
      out.push({ id: `${item.id}:fact`, kind: item.kind, item, enter: t.enter + FACT_DELAY, fill: null, part: 'fact', anchored, lead: item.id });
    } else {
      out.push({ id: item.id, kind: item.kind, item, enter: t.enter, fill: t.fill, anchored });
    }
  }
  out.sort((a, b) => a.enter - b.enter);
  const sp = scene.side_panel;
  if (sp && sp.panel && sp.panel.kind === 'terminal' && sp.panel.terminal && scene.layout?.show_side_panel !== false) {
    // The terminal card enters with its first output line (its anchor), not when the classic side panel opens: in
    // the stack, a card that entered first is the first to be collapsed, before its output was ever read.
    const first = (planScene(scene).terminal?.times || [])[0];
    const showAt = Math.max(0, Number(sp.show_at) || 0);
    const enter = typeof first === 'number' && Number.isFinite(first) ? Math.max(showAt, first - REVEAL_LEAD) : showAt;
    out.push({ id: 'terminal', kind: 'terminal', enter, anchored: true });
    out.sort((a, b) => a.enter - b.enter);
  }
  return out;
}

/**
 * When a quiz card leaves: a wrong option QUIZ_CLEAR after the reveal; the others stay.
 * @param {import('../../shared/types.js').QuizTiming} quiz
 * @param {CardSpec} spec
 */
export function quizExit(quiz, spec) {
  return spec.kind === 'option' && spec.option !== quiz.correct_index ? Number(quiz.reveal_start) + QUIZ_CLEAR : Infinity;
}

/**
 * Layout variant: `title` (chapter cards and scenes with nothing else), `media`, `split` or `stack`.
 * @param {TimedScene} scene
 * @param {'live' | 'render'} [mode]
 * @returns {'title' | 'media' | 'split' | 'stack'}
 */
export function variantOf(scene, mode = 'live') {
  const media = mediaOf(scene, mode === 'render' ? 'render' : 'live');
  const cards = cardSpecs(scene).length;
  if (scene.type === 'chapter_card' || (!media && cards === 0)) return 'title';
  if (media && cards) return 'split';
  if (media) return 'media';
  return 'stack';
}

/**
 * Body box below a title of `titleHeight` px.
 * @param {Zones} zones
 * @param {number} titleBottom
 * @param {'landscape' | 'portrait'} format
 */
function bodyBox(zones, titleBottom, format) {
  const L = LAYOUT[format];
  const top = Math.max(titleBottom + L.titleGap, zones.title.y0 + 60);
  return { x0: zones.body.x0, y0: Math.round(top), x1: zones.body.x1, y1: zones.body.y1 };
}

/**
 * The media frame box and the card column of a variant, below a title ending at `titleBottom`.
 * @param {'title' | 'media' | 'split' | 'stack'} variant
 * @param {Zones} zones
 * @param {number} titleBottom
 * @returns {{media: Box | null, cards: Box | null}}
 */
export function regions(variant, zones, titleBottom) {
  const format = zones.format;
  const L = LAYOUT[format];
  const body = bodyBox(zones, titleBottom, format);
  const col = zones.textColumn;
  if (format === 'landscape') {
    if (variant === 'media') return { media: body, cards: null };
    if (variant === 'split') {
      const split = Math.round(body.x0 + (body.x1 - body.x0) * L.split);
      return { cards: { ...body, x1: split }, media: { ...body, x0: split + L.splitGap } };
    }
    return { media: null, cards: variant === 'stack' ? body : null };
  }
  // portrait: shapes may span x 60-1020; card text stays in the column x 192-780
  if (variant === 'media') return { media: body, cards: null };
  if (variant === 'split') {
    const mid = Math.round(body.y0 + (body.y1 - body.y0) * L.split);
    return { media: { ...body, y1: mid }, cards: { x0: col.x0 - 24, y0: mid + L.splitGap, x1: col.x1 + 24, y1: body.y1 } };
  }
  return { media: null, cards: variant === 'stack' ? { x0: col.x0 - 24, y0: body.y0, x1: col.x1 + 24, y1: body.y1 } : null };
}

/**
 * The largest box the scene's media frame can have (no title shorter than one line at the minimum size): what
 * `mediaPlan` extracts clips at, so the extracted frames are never smaller than shown.
 * @param {TimedScene} scene
 * @param {Zones} zones
 * @param {'live' | 'render'} [mode]   the render page's layout by default (what the frames are extracted for)
 * @returns {{w: number, h: number}}
 */
export function mediaBoxMax(scene, zones, mode = 'render') {
  const L = LAYOUT[zones.format];
  const r = regions(variantOf(scene, mode), zones, zones.title.y0 + L.title.min * L.title.lh);
  const box = r.media || zones.body;
  return { w: box.x1 - box.x0, h: box.y1 - box.y0 };
}

/**
 * Kicker text above the title: a scene-type badge, else the title of the chapter the scene is in (`ctx.chapter`, the
 * stage's state.js chapterTitleAt), unless it repeats the scene's own title.
 * @param {TimedScene} scene
 * @param {string} chapter
 */
export function kickerOf(scene, chapter) {
  if (scene.type === 'chapter_card') return String(scene.chapter_label || '');
  if (scene.quiz) return String(scene.title || LABELS.quiz);
  const badge = LABELS.badges[String(scene.type)];
  if (badge && !normalize(scene.title).includes(normalize(badge))) return badge;
  const title = String(chapter || '');
  return normalize(title) === normalize(scene.title) ? '' : title;
}

/**
 * Every TeX string this template renders for a scene, with its display mode (the static `tex` hook; render.js
 * converts them before `ready`, B2 plan §1.4, §2): a formula item's LaTeX (display) and its variables' symbols
 * (inline), and the `$...$` spans of the rich-lite text of the items (text, term, justification, caption, table headers
 * and cells) and of a quiz (question, explanation, options): every string `build` puts through mountTex / renderRich
 * (B1's render.js field list, unchanged, now the template's own), in that order, repeats included (render.js keeps
 * the first).
 * @param {TimedScene} scene
 * @returns {{tex: string, display: boolean}[]}
 */
export function texOf(scene) {
  /** @type {{tex: string, display: boolean}[]} */
  const out = [];
  /** @param {string | null | undefined} tex @param {boolean} display */
  const add = (tex, display) => {
    if (typeof tex === 'string' && tex.trim()) out.push({ tex, display });
  };
  /** @param {unknown} text */
  const spans = (text) => {
    if (typeof text === 'string' && text.includes('$')) for (const tex of mathSpans(text)) add(tex, false);
  };
  for (const item of scene.board || []) {
    if (item.kind === 'formula') add(item.latex, true);
    for (const v of item.variables || []) add(v.symbol_latex, false);
    const cells = [...(item.headers || []), ...(item.rows || []).flat()];
    for (const text of [item.text, item.term, item.justification, item.caption, ...cells]) spans(text);
  }
  const quiz = scene.quiz;
  if (quiz) for (const text of [quiz.question, quiz.explanation, ...(quiz.options || [])]) spans(text);
  return out;
}

/**
 * The card a target names (B2 plan §1.7): `option:<i>` the quiz option, `<item>` or `<item>:<part>` the item's card
 * (in 9:16 a misconception's first card, its myth), else null.
 * @param {any} v   the view
 * @param {string} target
 */
function cardOf(v, target) {
  const t = String(target || '');
  const id = /^option:\d+$/.test(t) ? t : t.split(':')[0];
  return v.cards.find((/** @type {any} */ c) => c.spec.id === id) || null;
}

/** @param {unknown} s */
function normalize(s) {
  return String(s || '').toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '');
}

// ---------------------------------------------------------------------------------------------
// Building
// ---------------------------------------------------------------------------------------------

/**
 * @param {TemplateContext} ctx
 * @param {string | null | undefined} text
 */
function rich(ctx, text) {
  return renderRich(text, { pending: ctx.pending, renderTex: ctx.renderTex });
}

/**
 * A table too wide for the 9:16 text column (more than STACK_TABLE_COLUMNS columns; a grid of them would be clipped
 * at the R-29 sizes): one line per row, its first cell leading and the other cells after it, under a legend line of
 * the headers in the same order.
 * @param {TemplateContext} ctx
 * @param {string[]} headers
 * @param {string[][]} rows
 */
function stackedTable(ctx, headers, rows) {
  /** @param {string[]} cells @param {string} cls */
  const line = (cells, cls) => h('div', { class: cls }, h('span', { class: 'dyn-tbl-key' }, rich(ctx, cells[0] || '')),
    cells.length > 1 ? ' ' : null,
    cells.length > 1 ? h('span', { class: 'dyn-tbl-vals' }, cells.slice(1).map((c, i) => [i ? ' · ' : null, rich(ctx, c)])) : null);
  return h('div', { class: 'dyn-i dyn-i-table dyn-i-table--stacked' }, headers.length ? line(headers, 'dyn-tbl-legend') : null,
    rows.map((r) => line(r, 'dyn-tbl-row')));
}

/**
 * The body element of one board item.
 * @param {TemplateContext} ctx
 * @param {BoardItem} item
 * @param {{steps: number}} counter
 * @param {any} parts   per-card state the update needs (blank / filled pieces, legend rows)
 * @param {'myth' | 'fact'} [part]  one half of a misconception (9:16)
 */
function itemBody(ctx, item, counter, parts, part = undefined) {
  const k = item.kind;
  if (k === 'misconception' && part) {
    const fact = part === 'fact';
    return h('div', { class: ['dyn-i', 'dyn-i-myth', `dyn-i-myth--${part}`] },
      h('div', { class: ['dyn-myth-row', fact ? 'dyn-tone--ok' : 'dyn-tone--warn'] }, icon(fact ? 'check' : 'cross', { class: 'dyn-icon' }),
        h('span', { class: 'dyn-i-label', text: fact ? LABELS.correction : LABELS.misconception }),
        h('span', { class: 'dyn-i-text' }, rich(ctx, fact ? item.justification : item.text))));
  }
  switch (k) {
    case 'heading':
      return h('div', { class: 'dyn-i dyn-i-heading' }, rich(ctx, item.text));
    case 'bullet':
    case 'step':
      return h('div', { class: 'dyn-i dyn-i-bullet' }, h('span', { class: 'dyn-dot', 'aria-hidden': 'true' }),
        h('span', { class: 'dyn-i-text' }, item.date ? h('span', { class: 'dyn-i-date', text: `${item.date} ` }) : null, rich(ctx, item.text)));
    case 'definition':
      return h('div', { class: 'dyn-i dyn-i-definition' },
        item.term ? h('div', { class: 'dyn-i-term' }, rich(ctx, item.term)) : null,
        item.text ? h('div', { class: 'dyn-i-text' }, rich(ctx, item.text)) : null);
    case 'pair':
      return h('div', { class: 'dyn-i dyn-i-pair' },
        h('div', { class: 'dyn-i-term' }, rich(ctx, item.term)), h('div', { class: 'dyn-i-text' }, rich(ctx, item.text)));
    case 'formula': {
      const tex = h('div', { class: 'dyn-i-tex' });
      mountTex(tex, item.latex || '', true, { pending: ctx.pending, renderTex: ctx.renderTex });
      const plan = planScene(ctx.scene);
      parts.rows = (item.variables || []).map((v, n) => {
        const sym = h('span', { class: 'dyn-var-sym' });
        mountTex(sym, v.symbol_latex, false, { pending: ctx.pending, renderTex: ctx.renderTex });
        const row = h('div', { class: 'dyn-var' }, sym, h('span', { class: 'dyn-var-meaning', text: v.meaning }),
          v.unit ? h('span', { class: 'dyn-var-unit', text: v.unit }) : null);
        const at = plan.sync.partAt.get(`${item.id}|var:${n}`);
        return { el: row, at: at === undefined ? null : at };
      });
      return h('div', { class: 'dyn-i dyn-i-formula' }, tex,
        item.text ? h('div', { class: 'dyn-i-text dyn-i-sub' }, rich(ctx, item.text)) : null,
        parts.rows.length ? h('div', { class: 'dyn-vars' }, parts.rows.map((/** @type {any} */ r) => r.el)) : null);
    }
    case 'callout_info':
    case 'callout_tip':
    case 'callout_warning': {
      const tone = k.slice('callout_'.length);
      const label = tone === 'info' ? LABELS.info : tone === 'tip' ? LABELS.tip : LABELS.warning;
      return h('div', { class: ['dyn-i', 'dyn-i-callout', `dyn-tone--${tone}`] },
        h('div', { class: 'dyn-i-label' }, icon(tone === 'warning' ? 'warning' : tone, { class: 'dyn-icon' }), label),
        h('div', { class: 'dyn-i-text' }, rich(ctx, item.text)));
    }
    case 'misconception':
      return h('div', { class: 'dyn-i dyn-i-myth' },
        h('div', { class: 'dyn-myth-row dyn-tone--warn' }, icon('cross', { class: 'dyn-icon' }),
          h('span', { class: 'dyn-i-label', text: LABELS.misconception }), h('span', { class: 'dyn-i-text' }, rich(ctx, item.text))),
        item.justification ? h('div', { class: 'dyn-myth-row dyn-tone--ok' }, icon('check', { class: 'dyn-icon' }),
          h('span', { class: 'dyn-i-label', text: LABELS.correction }), h('span', { class: 'dyn-i-text' }, rich(ctx, item.justification))) : null);
    case 'code':
      return h('div', { class: 'dyn-i dyn-i-code' }, h('pre', { class: 'dyn-code' }, h('code', { text: item.code || '' })));
    case 'table': {
      const headers = item.headers || [];
      const rows = item.rows || [];
      if (ctx.format === 'portrait' && Math.max(headers.length, ...rows.map((r) => r.length)) > STACK_TABLE_COLUMNS) {
        return stackedTable(ctx, headers, rows);
      }
      return h('div', { class: 'dyn-i dyn-i-table' }, h('table', null,
        headers.length ? h('thead', null, h('tr', null, headers.map((c) => h('th', { scope: 'col' }, rich(ctx, c))))) : null,
        h('tbody', null, rows.map((r) => h('tr', null, r.map((c) => h('td', null, rich(ctx, c))))))));
    }
    case 'figure': {
      const media = ctx.scene.figures ? ctx.scene.figures[item.id] : undefined;
      const cap = item.caption ? h('div', { class: 'dyn-i-caption' }, rich(ctx, item.caption)) : null;
      if (!media || !(media.url || media.asset_key)) {
        return h('div', { class: 'dyn-i dyn-i-figure is-missing' }, h('div', { class: 'dyn-figure-missing', text: LABELS.figureMissing }), cap);
      }
      const L = LAYOUT[ctx.format];
      // sized by the template's mediaBox for FIGURE_SLOT (FIGURE_BOX_WIDTH x figureMax)
      const view = ctx.media(media, { slot: FIGURE_SLOT, alt: plainText(item.caption) || 'Figure' });
      parts.figure = view;
      const aspect = Number(media.width) > 0 && Number(media.height) > 0 ? Number(media.width) / Number(media.height) : 16 / 9;
      const frame = h('div', { class: 'dyn-figure', style: { height: L.figureMax, '--aspect': fx(aspect, 4) } }, view.el);
      parts.figureFrame = frame;
      return h('div', { class: 'dyn-i dyn-i-figure' }, frame, cap);
    }
    case 'example_step':
    case 'question': {
      const isStep = k === 'example_step';
      if (isStep) counter.steps += 1;
      const text = h('span', { class: 'dyn-i-text' }, rich(ctx, item.text));
      const blank = item.blank ? h('span', { class: 'dyn-blank', text: LABELS.blank }) : null;
      parts.blank = blank;
      parts.filledText = item.blank ? text : null;
      return h('div', { class: ['dyn-i', isStep ? 'dyn-i-step' : 'dyn-i-question'] },
        h('div', { class: 'dyn-i-label' }, isStep ? `${LABELS.step} ${counter.steps}` : icon('info', { class: 'dyn-icon' })),
        h('div', { class: 'dyn-i-main' }, h('div', { class: 'dyn-fillable' }, blank, text),
          item.justification ? h('div', { class: 'dyn-i-why' }, rich(ctx, item.justification)) : null));
    }
    case 'takeaway':
      return h('div', { class: 'dyn-i dyn-i-takeaway' }, icon('star', { class: 'dyn-icon' }), h('span', { class: 'dyn-i-text' }, rich(ctx, item.text)));
    case 'stat':
      return h('div', { class: 'dyn-i dyn-i-stat' },
        h('div', { class: 'dyn-stat-value' }, String(item.value || ''), item.unit ? h('span', { class: 'dyn-stat-unit', text: ` ${item.unit}` }) : null),
        h('div', { class: 'dyn-i-text' }, rich(ctx, item.text)));
    case 'quote':
      return h('div', { class: 'dyn-i dyn-i-quote' }, h('div', { class: 'dyn-i-text' }, rich(ctx, item.text)),
        item.caption ? h('div', { class: 'dyn-i-caption' }, rich(ctx, item.caption)) : null);
    default:
      return h('div', { class: 'dyn-i dyn-i-paragraph' }, rich(ctx, item.text));
  }
}

/**
 * Split a title into word spans (Intl.Segmenter units, so Indic clusters stay whole).
 * @param {HTMLElement} host
 * @param {string} text
 * @param {string} locale
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

/** Stands for one TeX or code span in the plain text a rich title's words are found in: one unbreakable unit. */
const OBJECT_CHAR = '\uFFFC';

/**
 * Split rich-lite text (a quiz question: `$TeX$`, `**bold**`, `*italic*`, `` `code` ``, `[[term]]`; the classic quiz
 * renders it with renderRich) into word spans like `wordSpans`, keeping its markup. The words are found in its plain
 * text, where a TeX or code span is one character (never split; it joins the word it touches), and each word span
 * holds the part of the markup that word covers: a bold phrase becomes one <strong> per word, which looks the same
 * (the stage styles `.rt-*` by weight and style only, dynamic.css). TeX goes through `mountTex` with the context's
 * `pending` and `renderTex`, so the render page waits for it as it does for the cards' TeX (render.js converts every
 * quiz question's TeX before `ready`).
 * @param {TemplateContext} ctx
 * @param {HTMLElement} host
 * @param {import('../../player/richtext.js').RichNode[]} nodes   the parsed text
 * @returns {{words: HTMLElement[], tex: boolean[]}}  `tex[i]`: word i holds TeX (an atomic picture: it may stand
 *   taller than a line without being broken inside, and it overflows sideways when wider than the line)
 */
function richWordSpans(ctx, host, nodes) {
  /** @typedef {{t: 'b' | 'i' | 'k', c: import('../../player/richtext.js').RichNode[]}} Mark */
  /** @type {{node: {t: 'text' | 'code' | 'math', v: string}, marks: Mark[], at: number}[]} */
  const leaves = [];
  let plain = '';
  /** @param {import('../../player/richtext.js').RichNode[]} list @param {Mark[]} marks */
  const walk = (list, marks) => {
    for (const n of list) {
      if (n.t === 'text' || n.t === 'code' || n.t === 'math') {
        leaves.push({ node: n, marks, at: plain.length });
        plain += n.t === 'text' ? n.v : OBJECT_CHAR;
      } else {
        walk(n.c, [...marks, n]);
      }
    }
  };
  walk(nodes, []);
  /** @type {number[]} the leaf of every character of `plain` */
  const owner = [];
  leaves.forEach((leaf, i) => {
    const length = leaf.node.t === 'text' ? leaf.node.v.length : 1;
    for (let k = 0; k < length; k++) owner.push(i);
  });
  /**
   * Append the markup of plain[a, b) to `parent`: its leaves (or the part of a text leaf in the range) in order, each
   * inside the elements of its marks; consecutive leaves share the marks they have in common.
   * @param {Node} parent @param {number} a @param {number} b
   * @returns {boolean} whether the range holds TeX
   */
  const fill = (parent, a, b) => {
    /** @type {{mark: Mark, el: HTMLElement}[]} */
    const open = [];
    let tex = false;
    let k = a;
    while (k < b) {
      const i = owner[k];
      const leaf = leaves[i];
      let end = k + 1;
      while (end < b && owner[end] === i) end++;
      let same = 0;
      while (same < open.length && same < leaf.marks.length && open[same].mark === leaf.marks[same]) same++;
      open.length = same;
      for (const mark of leaf.marks.slice(same)) {
        const el = document.createElement(mark.t === 'b' ? 'strong' : mark.t === 'i' ? 'em' : 'span');
        el.className = mark.t === 'k' ? 'rt-k' : mark.t === 'b' ? 'rt-b' : 'rt-i';
        (open.length ? open[open.length - 1].el : parent).appendChild(el);
        open.push({ mark, el });
      }
      const into = open.length ? open[open.length - 1].el : parent;
      const n = leaf.node;
      if (n.t === 'text') {
        into.appendChild(document.createTextNode(n.v.slice(k - leaf.at, end - leaf.at)));
      } else if (n.t === 'code') {
        into.appendChild(h('code', { class: 'rt-code', text: n.v }));
      } else {
        const span = document.createElement('span');
        into.appendChild(span);
        mountTex(span, n.v, false, { pending: ctx.pending, renderTex: ctx.renderTex });
        tex = true;
      }
      k = end;
    }
    return tex;
  };
  /** @type {HTMLElement[]} */
  const words = [];
  /** @type {boolean[]} */
  const tex = [];
  for (const part of splitWords(plain, ctx.locale)) {
    const end = part.index + part.text.length;
    const span = h('span', { class: 'dyn-word' });
    tex.push(fill(span, part.index, end));
    words.push(span);
    host.appendChild(span);
    if (part.ws) fill(host, end, end + part.ws.length);
  }
  return { words, tex };
}

/**
 * What text of `ink` over the title block's translucent `scrim` sits on: the scrim composited over each colour the
 * background shows, the one with the least contrast against `ink` (R-53's worst case). Opaque hex, the colour
 * preflight's contrast check then measures, so a scrim that helps reads as fixed.
 * @param {string} scrim   #rrggbbaa
 * @param {{bg: string, bgs?: string[]}} onBackground
 * @param {string} ink
 */
export function scrimmedBg(scrim, onBackground, ink) {
  const bgs = onBackground.bgs && onBackground.bgs.length ? onBackground.bgs : [onBackground.bg];
  let worst = composite(scrim, bgs[0]);
  let low = contrastRatio(ink, worst);
  for (const b of bgs.slice(1)) {
    const c = composite(scrim, b);
    const ratio = contrastRatio(ink, c);
    if (ratio < low) {
      low = ratio;
      worst = c;
    }
  }
  return worst;
}

/** @type {TemplateModule} */
export default {
  id: 'board_generic',

  /**
   * @param {TemplateContext} ctx
   */
  build(ctx) {
    const { scene, format, roles } = ctx;
    const L = LAYOUT[format];
    const variant = variantOf(scene, ctx.mode);
    const quiz = scene.quiz || null;
    const kicker = kickerOf(scene, ctx.chapter);
    // a quiz question is rich-lite (TeX, bold, terms, code); a scene title is plain text. A question with markup keeps
    // it (richWordSpans); without, its plain text (an escaped `\$` shows as `$`, as renderRich shows it)
    const question = quiz ? parseRich(String(quiz.question || '')) : null;
    const richTitle = !!question && question.some((n) => n.t !== 'text');
    const titleText = quiz ? plainText(String(quiz.question || '')) : String(scene.title || '');
    // preflight's remedies (§7.12) arrive through the look: `variant: "safe"` (the compact layout) and
    // `text_fit[slot]` ({size} px caps a slot's size, {scrim} puts a scrim behind text drawn on the background)
    const tuning = slotTuning(ctx.look, format);
    const baseSpec = variant === 'title' ? L.titleCard : quiz ? L.titleQuiz : L.title;
    const titleSpec = tuning.titleSpec(baseSpec);
    const bodyPx = tuning.bodyPx(L.body);

    // on a colour block the pip takes the text colour (an accent mark on the accent would vanish)
    const pip = h('span', { class: 'dyn-pip', style: { background: ctx.onBackground.block ? ctx.onBackground.ink : roles.color('struct.pip') } });
    const kickerText = h('span', { class: 'dyn-kicker-text', text: kicker });
    const chip = h('span', { class: 'dyn-chip' });
    const header = h('div', { class: 'dyn-header', style: { '--kicker': `${L.kicker}px`, color: ctx.onBackground.ink2 } }, pip, kickerText);
    if (!kicker) header.classList.add('is-empty');
    const title = h('h2', { class: 'dyn-title', dir: 'auto', style: { color: ctx.onBackground.ink, '--lh': String(titleSpec.lh) } });
    /** @type {HTMLElement[]} */
    let words;
    /** @type {boolean[]} */
    let wordTex;
    if (richTitle && question) ({ words, tex: wordTex } = richWordSpans(ctx, title, question));
    else {
      words = wordSpans(title, titleText, ctx.locale);
      wordTex = words.map(() => false);
    }
    const subtitle = variant === 'title' && scene.subtitle
      ? h('p', { class: 'dyn-subtitle', dir: 'auto', text: String(scene.subtitle), style: { color: ctx.onBackground.ink2 } })
      : null;
    const titleBlock = h('div', { class: ['dyn-titleblock', `dyn-titleblock--${variant === 'title' ? 'center' : 'top'}`] }, header, title, subtitle);
    // R-53 remedy: a scrim of >= 55% opacity behind the whole title block (the background tone, so it reads as the
    // page) when preflight asked for one for any text in it: the kicker (critical on a chapter card), the title or
    // the subtitle. texts() then reports that text on the scrim, so the second preflight pass measures the remedy.
    // Its colour is the scene's `scrim` role: the look's stored scrim, else the background tone at SCRIM_ALPHA_HEX.
    const scrim = TITLE_BLOCK_SLOTS.some((slot) => tuning.scrim(slot)) ? roles.color('scrim') : null;
    if (scrim) {
      titleBlock.classList.add('has-scrim');
      titleBlock.style.setProperty('--scrim', scrim);
    }

    // cards
    const counter = { steps: 0 };
    const surface = roles.color('surface');
    const accent = roles.color('struct.border');
    const ink = roles.color('ink');
    const ink2 = roles.color('ink2');
    const cardsEl = h('div', { class: 'dyn-cards', style: { '--body': `${bodyPx}px` } });
    const cards = cardSpecs(scene, format).map((spec, order) => {
      /** @type {any} */
      const parts = {};
      /** @type {HTMLElement} */
      let body;
      if (spec.kind === 'option' && quiz) {
        const letter = String.fromCharCode(65 + /** @type {number} */ (spec.option));
        // only the answer ever shows its check: a wrong option keeps that room for its text (a reel's narrow column)
        parts.check = spec.option === quiz.correct_index ? icon('check', { class: 'dyn-icon dyn-check' }) : null;
        parts.badge = h('span', { class: 'dyn-badge', text: letter });
        body = h('div', { class: 'dyn-i dyn-i-option' }, parts.badge,
          h('span', { class: 'dyn-i-text' }, rich(ctx, quiz.options[/** @type {number} */ (spec.option)])), parts.check);
      } else if (spec.kind === 'explanation' && quiz) {
        body = h('div', { class: 'dyn-i dyn-i-explanation' }, h('div', { class: 'dyn-i-label', text: LABELS.answer }),
          h('div', { class: 'dyn-i-text' }, rich(ctx, quiz.explanation)));
      } else if (spec.kind === 'terminal') {
        const term = scene.side_panel && scene.side_panel.panel.terminal;
        const lines = planScene(scene).terminal?.lines || [];
        parts.lines = lines.map((line) => h('div', { class: 'dyn-term-line', text: line || ' ' }));
        body = h('div', { class: 'dyn-i dyn-i-code dyn-i-terminal' },
          h('pre', { class: 'dyn-code' }, h('div', { class: 'dyn-term-cmd', text: `$ ${term ? term.command : ''}` }), parts.lines));
      } else {
        body = itemBody(ctx, /** @type {BoardItem} */ (spec.item), counter, parts, spec.part);
      }
      const el = h('div', { class: ['dyn-card', `dyn-card--${spec.kind}`], dataset: { item: spec.id } }, body);
      el.style.setProperty('--surface', surface);
      el.style.setProperty('--ink', ink);
      el.style.setProperty('--ink2', ink2);
      el.style.setProperty('--border', accent);
      el.style.setProperty('--warn', roles.color('warn'));
      el.style.setProperty('--ok', roles.color('ok'));
      cardsEl.appendChild(el);
      return { spec, el, body, parts, height: 0, overflow: 0, order, enter0: spec.enter, anchored: spec.anchored !== false, exit: Infinity };
    });

    // media frame
    const media = mediaOf(scene, ctx.mode);
    const frame = media ? h('div', { class: 'dyn-frame' }) : null;
    /** @type {import('../media.js').MediaView | null} */
    let mediaView = null;
    if (media && frame) {
      // sized by this template's mediaBox (mediaBoxMax), the box mediaPlan extracts the clip at
      mediaView = ctx.media(media.ref, { slot: media.slot, start: media.start, alt: String(scene.title || '') });
      frame.appendChild(mediaView.el);
      if (mediaView.missing) {
        // shownIn() said yes but the element has nothing to load (an unsafe URL): the panel's title on the card
        // surface, never an empty black box
        frame.classList.add('is-missing');
        frame.style.setProperty('--surface', surface);
        frame.style.setProperty('--ink2', ink2);
        const label = media.slot === 'panel' ? scene.side_panel?.panel?.title : null;
        if (label) frame.appendChild(h('div', { class: 'dyn-media-missing', text: String(label) }));
      }
    }

    // quiz countdown ring (structure: R-07 think time is a visual ring only)
    /** @type {any} */
    let ring = null;
    if (quiz) {
      const r = 40;
      // the track in the ink it sits on, at the opacity that keeps it visible on dark and light families alike
      const inkL = toOklch(ctx.onBackground.ink).l;
      const trackAlpha = inkL >= 0.5 ? RING_TRACK_ALPHA.lightInk : RING_TRACK_ALPHA.darkInk;
      const track = svg('circle', {
        cx: '48', cy: '48', r: String(r), class: 'dyn-ring-track', stroke: ctx.onBackground.ink, 'stroke-opacity': fx(trackAlpha, 2),
      });
      const arc = svg('circle', { cx: '48', cy: '48', r: String(r), class: 'dyn-ring-arc', stroke: roles.color('struct.accent') });
      const svgEl = svg('svg', { class: 'dyn-ring-svg', width: '96', height: '96', 'aria-hidden': 'true' }, track, arc);
      svgEl.setAttribute('viewBox', '0 0 96 96');
      const num = h('span', { class: 'dyn-ring-num', style: { color: ctx.onBackground.ink } });
      ring = { el: h('div', { class: 'dyn-ring' }, svgEl, num), arc, num, length: 2 * Math.PI * r };
    }

    const root = h('div', { class: ['dyn-board', `dyn-board--${variant}`, `dyn-board--${format}`] }, titleBlock, frame, cardsEl, ring ? ring.el : null);
    const chipWrap = h('div', { class: 'dyn-chipwrap', style: { '--surface': surface, '--ink2': ink2 } }, chip);
    root.appendChild(chipWrap);
    ctx.root.appendChild(root);
    return {
      variant, root, header, kickerText, title, titleSpec, words, wordTex, subtitle, titleBlock, scrim, chip, chipWrap,
      cards, cardsEl, frame, mediaView, media, ring,
      visibleMax: quiz ? cards.length : tuning.safe ? 1 : variant === 'split' ? L.splitMaxVisible : L.maxVisible,
      bodyPx, titleText,
      titleSize: titleSpec.max, titleOverflow: 0, cardsBox: /** @type {Box | null} */ (null),
      mediaBox: /** @type {Box | null} */ (null), fit: 1, mediaAlpha: 0, cardAlpha: cards.map(() => 0),
      exits: cards.map(() => Infinity), cardInk: cards.map(() => ink), ink, ink2, accent, surface, gap: L.gap, compact: false,
      ok: roles.color('ok'), headerAlpha: 0, titleAlpha: 0, subtitleAlpha: 0, ringAlpha: 0,
    };
  },

  /**
   * Measure and fit: title size, the regions below it, the media frame and the card scale.
   * @param {TemplateContext} ctx
   */
  layout(ctx) {
    const v = ctx.view;
    const { zones, format } = ctx;
    const L = LAYOUT[format];
    const spec = v.titleSpec;
    // Title: the largest size (4 px steps) whose line count fits.
    const titleBox = v.variant === 'title' ? centeredTitleBox(zones) : zones.title;
    // 16:9: the countdown ring is level with the kicker and the first title line at the right of the content box,
    // so the title block keeps clear of it (9:16 puts the ring below the title, beside the options)
    const ringRoom = v.ring && format === 'landscape' ? RING_SIZE + RING_GAP : 0;
    v.titleBlock.style.left = `${titleBox.x0}px`;
    v.titleBlock.style.width = `${titleBox.x1 - titleBox.x0 - ringRoom}px`;
    if (v.variant === 'title') {
      v.titleBlock.style.top = `${titleBox.y0}px`;
      v.titleBlock.style.height = `${titleBox.y1 - titleBox.y0}px`;
    } else {
      v.titleBlock.style.top = `${zones.header.y0}px`;
    }
    // A word span taller than one line has been broken inside (overflow-wrap: anywhere). Allowed only for a word that
    // is wider than the column even at the minimum size (a long Indic compound); every other word must stay whole.
    // A word holding TeX is a picture: it may stand taller than a line (a fraction) and is never broken inside, but
    // it runs out of the column sideways when wider than it, so that is what counts for it.
    const broken = (/** @type {number} */ s) => v.words.map((/** @type {HTMLElement} */ w, /** @type {number} */ i) => (v.wordTex[i]
      ? w.offsetWidth > v.title.clientWidth + 1
      : w.offsetHeight > 1.5 * s * spec.lh));
    v.title.style.fontSize = `${spec.min}px`;
    const unavoidable = broken(spec.min);
    // A TeX word wider than the column even at the minimum size runs out of it sideways at every size, the further the
    // larger the title: the title is then held at the minimum, where that overflow is least (and is what preflight
    // blocks at the floor), not drawn at the largest size whose line count fits. A plain word is broken inside the
    // column instead (overflow-wrap), so its unavoidable break costs no size.
    const texTooWide = unavoidable.some((b, i) => b && v.wordTex[i]);
    /** The largest size (4 px steps, the minimum itself last) at which the title takes at most `lines` lines with
     * whole words (an unavoidable break inside a word is not overflow: preflight would block it at the floor). */
    const fitLines = (/** @type {number} */ lines) => {
      let s = texTooWide ? spec.min : spec.max;
      for (;;) {
        v.title.style.fontSize = `${s}px`;
        const whole = broken(s).every((b, i) => !b || unavoidable[i]);
        if (whole && Math.round(v.title.offsetHeight / (s * spec.lh)) <= lines) return { size: s, fits: true };
        if (s <= spec.min) return { size: s, fits: false };
        s = Math.max(spec.min, s - 4);
      }
    };
    let best = spec.prefer ? fitLines(spec.prefer) : { size: spec.max, fits: false };
    if (!best.fits) best = fitLines(spec.lines);
    const size = best.size;
    const fits = best.fits;
    // px beyond its lines at the minimum size
    v.titleOverflow = fits ? 0 : Math.max(1, v.title.offsetHeight - spec.lines * size * spec.lh);
    // ... or past the column's side: TeX wider than the column even at the minimum size, measured at that size, where
    // texTooWide holds the title (never true of plain words, which overflow-wrap breaks)
    const wide = Math.max(0, ...v.words.map((/** @type {HTMLElement} */ w, /** @type {number} */ i) => (v.wordTex[i] ? w.offsetWidth - v.title.clientWidth : 0)));
    if (wide > 2) v.titleOverflow = Math.max(v.titleOverflow, wide); // 2 px: layout rounding
    v.titleSize = size;
    const titleBottom = v.variant === 'title' ? 0 : v.titleBlock.offsetTop + v.titleBlock.offsetHeight;
    let r = regions(v.variant, zones, titleBottom || zones.title.y0 + spec.min * spec.lh);
    if (format === 'portrait' && v.variant === 'split' && r.media && r.cards) {
      // a reel stacks the picture above the cards: the picture gets what the tallest card leaves (25-55%)
      const top = r.media.y0;
      const bodyH = r.cards.y1 - top;
      const w = r.cards.x1 - r.cards.x0;
      for (const c of v.cards) c.el.style.width = `${w}px`;
      v.cardsEl.style.setProperty('--fit', '0.85');
      const tallest = Math.max(0, ...v.cards.map((/** @type {any} */ c) => c.el.offsetHeight));
      const mediaH = Math.round(Math.min(L.split * bodyH, Math.max(0.25 * bodyH, bodyH - tallest - L.splitGap)));
      r = { media: { ...r.media, y1: top + mediaH }, cards: { ...r.cards, y0: top + mediaH + L.splitGap } };
    }
    v.cardsBox = r.cards;
    v.mediaBox = r.media;
    if (v.frame && r.media) {
      const ref = v.media.ref;
      const box = r.media;
      const bw = box.x1 - box.x0;
      const bh = box.y1 - box.y0;
      const aspect = Number(ref.width) > 0 && Number(ref.height) > 0 ? Number(ref.width) / Number(ref.height) : 16 / 9;
      const w = Math.min(bw, bh * aspect);
      const fh = w / aspect;
      // centred, except a reel's split, which lines up with the card column
      const x = format === 'portrait' && v.variant === 'split' ? zones.textColumn.x0 - 24 : box.x0 + (bw - w) / 2;
      const y = format === 'landscape' && v.variant === 'split' ? box.y0 + (bh - fh) / 2 : box.y0 + (v.variant === 'media' ? (bh - fh) / 2 : 0);
      Object.assign(v.frame.style, { left: `${Math.round(x)}px`, top: `${Math.round(y)}px`, width: `${Math.round(w)}px`, height: `${Math.round(fh)}px` });
    }
    if (v.ring) {
      // 16:9: top right of the content box, right of the title block (ringRoom); 9:16: beside the options, below the
      // title (its number stays left of x 888)
      const rx = format === 'landscape' ? zones.critical[0].x1 - RING_SIZE : zones.textColumn.x1 + 32;
      const ry = format === 'landscape' ? zones.header.y0 - 4 : (r.cards ? r.cards.y0 : zones.body.y0);
      Object.assign(v.ring.el.style, { left: `${rx}px`, top: `${ry}px` });
    }
    // Cards: the largest scale (and most visible cards) whose worst run of visible cards fits the column.
    // the kicker keeps its full width unless the "earlier" chip can appear beside it (a card collapses in this scene)
    v.header.classList.add('is-chipless');
    if (!v.cardsBox || !v.cards.length) return;
    const box = v.cardsBox;
    const width = box.x1 - box.x0;
    const avail = box.y1 - box.y0;
    v.cardsEl.style.left = `${box.x0}px`;
    v.cardsEl.style.top = `${box.y0}px`;
    v.cardsEl.style.width = `${width}px`;
    for (const c of v.cards) {
      c.el.style.width = `${width}px`;
      // a picture card never needs more than the column leaves for it (its caption below)
      if (c.parts.figureFrame) c.parts.figureFrame.style.height = `${Math.round(Math.min(L.figureMax, Math.max(140, avail - 140)))}px`;
    }
    // every layout starts from the build's order and entrances (layout runs again when a late face changes widths)
    v.cards = v.cards.slice().sort((/** @type {any} */ a, /** @type {any} */ b) => a.order - b.order);
    for (const c of v.cards) c.spec.enter = c.enter0;
    v.compact = false;
    v.cardsEl.classList.remove('is-compact');
    v.gap = L.gap;
    const measure = (/** @type {number} */ s) => {
      v.cardsEl.style.setProperty('--fit', fx(s, 4));
      return v.cards.map((/** @type {any} */ c) => c.el.offsetHeight);
    };
    const quiz = ctx.scene.quiz || null;
    const n = v.cards.length;
    const enter0 = v.cards.map((/** @type {any} */ c) => c.enter0);
    /**
     * Who is on screen when: entrances (re-timed for cards without a narration anchor) and exits (collapse starts)
     * with at most `m` cards, and with heights `hs` at most the column's height, shown together. A card leaves just
     * before a new one needs its room. A card no beat reveals (they all enter together after the title) that would
     * push out a card shown for less than MIN_DWELL waits for it instead, so every card is read (capped so it still
     * enters a second before the scene ends); a card on its beat enters on time, and with `floors` the card it pushes
     * out stays until it was shown MIN_DWELL when that card has no anchor or was not even in yet (the overlap is
     * fitted below; without `floors`, as when that overlap cannot fit at all, it leaves when the new card needs room).
     * @param {number[] | null} hs @param {number} m @param {boolean} [floors] @returns {{enter: number[], exits: number[]}}
     */
    const plan = (hs, m, floors = true) => {
      const enterAt = enter0.slice();
      const exits = new Array(n).fill(Infinity);
      // a card that follows another (a 9:16 fact after its myth) moves with it
      const followers = v.cards.map((/** @type {any} */ c) => v.cards.findIndex((/** @type {any} */ d) => d.spec.lead && d.spec.lead === c.spec.id));
      if (quiz) return { enter: enterAt, exits: v.cards.map((/** @type {any} */ c) => quizExit(quiz, c.spec)) };
      const latest = Math.max(0, Number(ctx.scene.duration) - CARD_ENTER - 1);
      const pending = [...enterAt.keys()].sort((a, b) => enterAt[a] - enterAt[b] || a - b);
      /** @type {number[]} */
      const shown = [];
      const height = (/** @type {number[]} */ list) => list.reduce((a, j) => a + (hs ? hs[j] : 0), 0) + v.gap * Math.max(0, list.length - 1);
      const over = (/** @type {number} */ j) => shown.length > 0 && (shown.length + 1 > m || (hs !== null && height([...shown, j]) > avail));
      for (let guard = 0; pending.length && guard < 4 * n * n + 8; guard++) {
        const j = /** @type {number} */ (pending.shift());
        if (over(j) && !v.cards[j].anchored) {
          const need = enterAt[shown[0]] + CARD_ENTER + MIN_DWELL + CARD_EXIT;
          const at = Math.min(need, Math.max(latest, enterAt[j]));
          if (at > enterAt[j] + 1e-6) {
            /** @param {number} card @param {number} time */
            const requeue = (card, time) => {
              enterAt[card] = time;
              const was = pending.indexOf(card);
              if (was >= 0) pending.splice(was, 1);
              let k = 0;
              while (k < pending.length && (enterAt[pending[k]] < time || (enterAt[pending[k]] === time && pending[k] < card))) k++;
              pending.splice(k, 0, card);
            };
            requeue(j, at);
            const f = followers[j];
            if (f >= 0 && pending.includes(f) && enterAt[f] < at + FACT_DELAY) requeue(f, at + FACT_DELAY);
            continue;
          }
        }
        while (over(j)) {
          const i = /** @type {number} */ (shown.shift());
          const out = enterAt[j] - CARD_EXIT;
          const read = enterAt[i] + CARD_ENTER + MIN_DWELL;
          exits[i] = floors && (!v.cards[i].anchored || out < enterAt[i] + CARD_ENTER) ? Math.max(out, read) : out;
        }
        shown.push(j);
      }
      return { enter: enterAt, exits };
    };
    /**
     * px each card ends below the column when every earlier card still on screen at its entrance sits above it.
     * @param {number[]} hs @param {{enter: number[], exits: number[]}} s
     */
    const spills = (hs, s) => v.cards.map((/** @type {any} */ c, /** @type {number} */ i) => {
      const at = s.enter[i];
      let sum = 0;
      let count = 0;
      for (let j = 0; j < n; j++) {
        if ((s.enter[j] < at || (s.enter[j] === at && j <= i)) && s.exits[j] + CARD_EXIT > at) {
          sum += hs[j];
          count += 1;
        }
      }
      return sum + v.gap * Math.max(0, count - 1) - avail;
    });
    /** Tallest set of cards on screen together, past the column (<= 0: fits). @param {number[]} hs @param {{enter: number[], exits: number[]}} s */
    const worst = (hs, s) => Math.max(-avail, ...spills(hs, s));
    const minFit = /** @type {number} */ (MIN_FIT[format]);
    /** Largest scale in [lo, 1] at which `fits` holds (`lo` itself when nothing larger does). @param {number} lo @param {(hs: number[]) => boolean} fits */
    const largest = (lo, fits) => {
      if (fits(measure(1))) return 1;
      let a = lo;
      let b = 1;
      for (let i = 0; i < 6; i++) {
        const mid = (a + b) / 2;
        if (fits(measure(mid))) a = mid;
        else b = mid;
      }
      return a;
    };
    const maxM = Math.min(v.visibleMax, n);
    let fit = 1;
    /** @type {(hs: number[]) => {enter: number[], exits: number[]}} */
    let scheduleAt = () => plan(null, maxM);
    if (quiz) {
      // every option stays until the reveal: shrink (down to the minimum) rather than drop one
      scheduleAt = () => plan(null, n);
      const fitsQuiz = (/** @type {number[]} */ hs) => worst(hs, scheduleAt(hs)) <= 0;
      fit = largest(minFit, fitsQuiz);
      if (!fitsQuiz(measure(fit))) {
        // a long question leaves too little room even at the minimum size: tighter option cards and gaps
        v.compact = true;
        v.cardsEl.classList.add('is-compact');
        v.gap = COMPACT_GAP[format];
        fit = largest(minFit, fitsQuiz);
      }
    } else {
      // every card stays until it was read (floors) unless that cannot fit even at the minimum size; then a card leaves
      // when the next one needs its room, as the beats ask
      for (const floors of [true, false]) {
        const counted = plan(null, maxM, floors);
        if (worst(measure(GENTLE_FIT), counted) <= 0) {
          // the full stack fits with at most a gentle shrink
          scheduleAt = () => counted;
          fit = largest(GENTLE_FIT, (hs) => worst(hs, counted) <= 0);
          break;
        }
        // otherwise keep the text large and show fewer cards at a time (each card on its own must fit, and so must a
        // card kept beside the next one until it was read)
        const greedy = (/** @type {number[]} */ hs) => plan(hs, maxM, floors);
        const fits = (/** @type {number[]} */ hs) => Math.max(...hs) <= avail && worst(hs, greedy(hs)) <= 0;
        scheduleAt = greedy;
        fit = largest(minFit, fits);
        if (!floors || fits(measure(fit))) break;
      }
    }
    const heights = measure(fit);
    v.fit = fit;
    const sched = scheduleAt(heights);
    // px a card ends below the column with the cards on screen with it (a quiz keeps every option until the reveal,
    // so a long question can leave too little room even at the minimum size): reported as overflow, so preflight
    // shrinks or blocks ("Shorten text") instead of passing text drawn into the caption band
    const spill = spills(heights, sched);
    v.cards.forEach((/** @type {any} */ c, /** @type {number} */ i) => {
      c.spec.enter = sched.enter[i];
      c.exit = sched.exits[i];
      c.height = heights[i];
      // ... or is clipped at its sides (a wide table, a long unbreakable word): a clipped card never passes silently
      const wide = (c.el.scrollWidth || 0) - (c.el.clientWidth || 0);
      c.overflow = Math.max(0, heights[i] - avail, spill[i], wide > 2 ? wide : 0); // 2 px: layout rounding
    });
    // the stack in entrance order (a re-timed card joins the bottom when it enters)
    v.cards.sort((/** @type {any} */ a, /** @type {any} */ b) => a.spec.enter - b.spec.enter || a.order - b.order);
    v.exits = v.cards.map((/** @type {any} */ c) => c.exit);
    if (!quiz && v.exits.some(Number.isFinite)) v.header.classList.remove('is-chipless');
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
    const rm = ctx.reducedMotion;
    const L = LAYOUT[ctx.format];
    const enter = (/** @type {number} */ start, /** @type {number} */ dur) => ease('ease.enter', progress(t, start, dur));
    const move = (/** @type {number} */ start, /** @type {number} */ dur) => ease('ease.move', progress(t, start, dur));

    // header and title
    v.headerAlpha = enter(0, 0.35);
    p.style(v.header, 'opacity', fx(v.headerAlpha));
    const n = v.words.length;
    const each = n > 1 ? Math.min(WORD_STAGGER, WORD_STAGGER_TOTAL / (n - 1)) : 0;
    const rise = v.variant === 'title' ? 40 : RISE;
    v.titleAlpha = n ? enter(TITLE_DELAY + each * (n - 1), WORD_SECONDS) : 1;
    v.words.forEach((/** @type {HTMLElement} */ w, i) => {
      const e = enter(TITLE_DELAY + each * i, WORD_SECONDS);
      p.style(w, 'opacity', fx(e));
      p.style(w, 'transform', rm || e >= 1 ? 'none' : `translate3d(0, ${Math.round((1 - e) * rise)}px, 0)`);
    });
    v.subtitleAlpha = v.subtitle ? enter(TITLE_DELAY + each * n + 0.1, 0.5) : 0;
    if (v.subtitle) p.style(v.subtitle, 'opacity', fx(v.subtitleAlpha));

    // media
    if (v.frame) {
      const start = v.media.start > 0 ? v.media.start : 0.2;
      const e = enter(start, MEDIA_ENTER);
      v.mediaAlpha = e;
      p.style(v.frame, 'opacity', fx(e));
      // a rise, never a scale: Chromium's decoded-image cache keeps the scales it has drawn a picture at, so a
      // scaling picture could look different depending on the frames painted before (measured in reverse renders)
      p.style(v.frame, 'transform', rm || e >= 1 ? 'none' : `translate3d(0, ${Math.round((1 - e) * RISE)}px, 0)`);
      // painted even while hidden, so the element never keeps a frame from an earlier seek
      if (v.mediaView) v.mediaView.update(p, t, ctx.frameInfo || {});
    }

    // cards: the stack slides up as the oldest card leaves to make room for a new one
    const cards = v.cards;
    const quiz = ctx.scene.quiz || null;
    const revealP = quiz ? move(Number(quiz.reveal_start), REVEAL_SECONDS) : 0;
    let y = 0;
    let collapsed = 0;
    let firstCollapse = Infinity;
    let current = -1;
    cards.forEach((/** @type {any} */ c, k) => {
      if (enter(c.spec.enter, CARD_ENTER) > 0) current = k;
    });
    cards.forEach((/** @type {any} */ c, k) => {
      const e = enter(c.spec.enter, CARD_ENTER);
      const exit = v.exits[k];
      const col = Number.isFinite(exit) ? move(exit, CARD_EXIT) : 0;
      if (!quiz && Number.isFinite(exit)) firstCollapse = Math.min(firstCollapse, exit);
      // the chip counts cards that were shown (a card collapsing before it was ever in is not "earlier" content)
      if (!quiz && col >= 0.5 && exit >= c.spec.enter + CARD_ENTER - 1e-6) collapsed += 1;
      let alpha = e * (1 - Math.min(1, col * 1.5));
      if (quiz && c.spec.kind === 'option') {
        const correct = c.spec.option === quiz.correct_index;
        if (!correct) alpha *= 1 - 0.45 * revealP;
        p.style(c.el, '--border', mixOklch(v.accent, correct ? v.ok : v.accent, revealP));
        if (c.parts.check) p.style(c.parts.check, 'opacity', fx(revealP));
        p.style(c.parts.badge, 'background-color', correct ? mixOklch(v.surface, v.ok, revealP) : v.surface);
      } else if (k < current) {
        // past cards step back: ink2 text, a quieter border (weight and ink, never hue: R-52)
        const past = move(cards[k + 1].spec.enter, PAST_SECONDS);
        v.cardInk[k] = mixOklch(v.ink, v.ink2, past);
        p.style(c.el, '--ink', v.cardInk[k]);
        p.style(c.el, '--border', mixOklch(v.accent, v.surface, 0.55 * past));
      } else {
        v.cardInk[k] = v.ink;
        p.style(c.el, '--ink', v.ink);
        p.style(c.el, '--border', v.accent);
      }
      const dy = rm ? 0 : (1 - e) * RISE - col * 18;
      v.cardAlpha[k] = alpha;
      p.style(c.el, 'opacity', fx(alpha));
      p.style(c.el, 'visibility', alpha > 0.001 ? 'visible' : 'hidden');
      // whole pixels and no scale: Chromium rasterises inline SVG (the icons) differently at fractional offsets
      // depending on what it painted before (measured: reverse renders differed), so positions stay on the grid
      p.style(c.el, 'transform', `translate3d(0, ${Math.round(y + dy)}px, 0)`);
      if (e > 0) y += (c.height + v.gap) * (1 - col);
      paintParts(ctx, c, t, state);
    });

    // the "previous" chip
    const chipAlpha = collapsed > 0 ? enter(firstCollapse, 0.3) : 0;
    p.style(v.chipWrap, 'opacity', fx(chipAlpha));
    p.style(v.chipWrap, 'visibility', chipAlpha > 0.001 ? 'visible' : 'hidden');
    p.text(v.chip, collapsed > 0 ? LABELS.earlier(collapsed) : '');

    // quiz countdown ring
    if (v.ring && quiz) {
      const cd0 = Number(quiz.countdown_start);
      const secs = Math.max(0.001, Number(quiz.countdown_seconds));
      const shown = enter(cd0, 0.3) * (1 - move(Number(quiz.reveal_start), 0.3));
      const dash = strokeDash(v.ring.length, 1 - progress(t, cd0, secs));
      v.ringAlpha = shown;
      p.style(v.ring.el, 'opacity', fx(shown));
      p.style(v.ring.el, 'visibility', shown > 0.001 ? 'visible' : 'hidden');
      p.attr(v.ring.arc, 'stroke-dasharray', dash.dasharray);
      p.attr(v.ring.arc, 'stroke-dashoffset', dash.dashoffset);
      p.text(v.ring.num, state.countdownRemaining === null || state.countdownRemaining === undefined ? '' : String(state.countdownRemaining));
    }
  },

  /**
   * Where cue.look_here points (B2 plan §1.4, §1.5, §1.7): the card of `<item>` / `<item>:<part>` / `option:<i>`, or
   * the media frame for `panel` / `panel:...` (board_generic draws a panel as its picture or clip). Its box as laid out
   * and painted now (read after `update`), its edges rounded to whole stage px (§P: translates on whole pixels), a
   * rectangle with the element's own corner radius (cues.js adds the outline's outset). Null for anything else, or
   * when the element is not on the page.
   * @param {TemplateContext} ctx
   * @param {string} target
   * @returns {FocusRect | null}
   */
  focusRect(ctx, target) {
    const v = ctx.view;
    const t = String(target || '');
    const panel = t === 'panel' || t.startsWith('panel:');
    const card = panel ? null : cardOf(v, t);
    const el = panel ? v.frame : card ? card.el : null;
    const r = el ? ctx.rectOf(el) : null;
    if (!r) return null;
    const x0 = Math.round(r.x);
    const y0 = Math.round(r.y);
    const x1 = Math.round(r.x + r.w);
    const y1 = Math.round(r.y + r.h);
    const radius = panel ? FRAME_RADIUS : CARD_RADIUS;
    return { rect: { x: x0, y: y0, w: x1 - x0, h: y1 - y0 }, shape: 'rect', radius };
  },

  /**
   * The element holding an item's text, where cue.term finds its [[term]] spans (`.rt-k`, kept by renderRich): the
   * body of the item's card (`option:<i>`: the option's), as `cardOf` finds it. Null for anything else.
   * @param {TemplateContext} ctx
   * @param {string} itemId
   */
  itemEl(ctx, itemId) {
    const card = cardOf(ctx.view, itemId);
    return card ? card.body : null;
  },

  /**
   * The content groups cue.look_here dims to 70% (all but the one holding its target): the title block, the media
   * frame and every card, in the page's order.
   * @param {TemplateContext} ctx
   * @returns {Element[]}
   */
  dimGroups(ctx) {
    const v = ctx.view;
    return [v.titleBlock, v.frame, ...v.cardsEl.children].filter(Boolean);
  },

  /**
   * The shared-element morph's anchor (§4.4 shared anchor, B2 plan §1.4): none. The fallback template draws no anchor
   * visual of its own; the morph pairs templates that do (B2f), and a scene without one is entered without it.
   * @param {TemplateContext} _ctx
   * @returns {null}
   */
  anchor(_ctx) {
    return null;
  },

  /**
   * Static: the box a medium of `slot` is sized for (B2 plan §1.4): a figure card's picture FIGURE_BOX_WIDTH x
   * LAYOUT[format].figureMax; the scene's picture or clip (slot 'main' or 'panel': the frame shows one of them)
   * `mediaBoxMax` of `mode`'s layout. The stage sizes `ctx.media`'s views and `mediaPlan`'s clips with this, so every
   * frame key the page asks for is planned. B1's boxes exactly (B1 passed them to ctx.media itself); the frame's box
   * depends on neither the slot nor preflight's fixes.
   * @param {TimedScene} scene
   * @param {Zones} zones
   * @param {'live' | 'render'} mode
   * @param {string | undefined} slot
   * @param {import('../core.js').Fixes} _fixes
   * @returns {{w: number, h: number}}
   */
  mediaBox(scene, zones, mode, slot, _fixes) {
    if (slot === FIGURE_SLOT) return { w: FIGURE_BOX_WIDTH, h: LAYOUT[zones.format].figureMax };
    return mediaBoxMax(scene, zones, mode);
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
   * The text on screen (preflight: safe zones, overflow, contrast).
   * @param {TemplateContext} ctx
   * @returns {TextEntry[]}
   */
  texts(ctx) {
    const v = ctx.view;
    /** @type {TextEntry[]} */
    const out = [];
    const bg = ctx.onBackground.bg;
    // text in the title block sits on its scrim when it has one: the scrim over the background (scrimmedBg)
    const onBlock = (/** @type {string} */ ink) => (v.scrim ? scrimmedBg(v.scrim, ctx.onBackground, ink) : bg);
    const L = LAYOUT[ctx.format];
    const floor = MIN_TEXT[ctx.format];
    const kicker = v.kickerText.textContent || '';
    if (kicker) {
      // a chapter card's label ("Part 2") is part of its message: checked like the title (zones, R-53 contrast)
      out.push({ el: v.kickerText, role: 'label', slot: 'kicker', critical: v.variant === 'title', ink: ctx.onBackground.ink2,
        bg: onBlock(ctx.onBackground.ink2), alpha: v.headerAlpha, size: L.kicker, min: L.kicker, weight: 600, text: kicker });
    }
    if (v.words.length) {
      out.push({ el: v.title, role: 'title', slot: 'title', critical: true, ink: ctx.onBackground.ink, bg: onBlock(ctx.onBackground.ink),
        alpha: v.titleAlpha, overflow: v.titleOverflow, size: v.titleSize, min: v.titleSpec.min, weight: 700, text: v.titleText });
    }
    if (v.subtitle && v.subtitle.textContent) {
      // the chapter card's subtitle (ink2 on the background): in the title block, so its scrim covers it too
      const sub = ctx.format === 'portrait' ? 48 : 52;
      out.push({ el: v.subtitle, role: 'subtitle', slot: 'title', critical: true, ink: ctx.onBackground.ink2,
        bg: onBlock(ctx.onBackground.ink2), alpha: v.subtitleAlpha, size: sub, min: sub,
        weight: ctx.format === 'portrait' ? 600 : 400, text: v.subtitle.textContent });
    }
    if (v.ring && v.ring.num.textContent) {
      // the countdown's number: a label on the background (R-29 floor). Not critical: the think time is the ring itself
      // (R-07), and the ring sits outside the title block (16:9) or beside the options (9:16). Reported so preflight
      // sees its box and size like any other text on screen.
      out.push({ el: v.ring.num, role: 'label', slot: 'ring', critical: false, ink: ctx.onBackground.ink, bg, alpha: v.ringAlpha,
        size: MIN_LABEL[ctx.format], weight: 700, text: v.ring.num.textContent });
    }
    const size = Math.max(floor, v.bodyPx * v.fit);
    const min = Math.max(floor, v.bodyPx * MIN_FIT[ctx.format]);
    v.cards.forEach((/** @type {any} */ c, k) => {
      // the text inside the card's padding (the card's shape may reach further, e.g. under the 9:16 action rail)
      out.push({ el: c.body, role: 'body', slot: 'body', critical: true, ink: v.cardInk[k] || v.ink, bg: v.surface,
        alpha: v.cardAlpha[k], overflow: c.overflow, size, min, weight: BODY_WEIGHT[ctx.format],
        text: (c.body.textContent || '').replace(/\s+/g, ' ').trim() });
    });
    if (v.chip.textContent) {
      out.push({ el: v.chip, role: 'chip', slot: 'chip', critical: false, ink: v.ink2, bg: v.surface, alpha: 1,
        size: MIN_LABEL[ctx.format], weight: 600, text: v.chip.textContent });
    }
    return out;
  },
};

/**
 * The remedies preflight wrote into a look (§7.12), as core.js `fixesOf` reads them: the fixes channel first
 * (`look.fixes`: `{safe}`, `slots[slot].size`, `slots[slot].scrim`; B2 plan §1.2, deviation 2), B1's legacy fields
 * second (`variant: "safe"`, `text_fit[slot]` {size, scrim}), so a timeline remedied before B2a keeps its remedies. A
 * size caps the slot (never below the R-29 floor); `safe` is the compact layout (the title at its minimum, one card
 * at a time); a scrim of either title-block slot goes behind the whole block.
 * @param {import('../../shared/types.js').ResolvedLook | null | undefined} look
 * @param {'landscape' | 'portrait'} format
 */
export function slotTuning(look, format) {
  const fixes = fixesOf(look);
  const floor = MIN_TEXT[format];
  const safe = fixes.safe;
  /** @param {string} slot */
  const fixOf = (slot) => (Object.prototype.hasOwnProperty.call(fixes.slots, slot) ? fixes.slots[slot] : null);
  /** @param {string} slot */
  const cap = (slot) => {
    const s = fixOf(slot);
    return s && s.size !== null ? Math.max(floor, s.size) : null;
  };
  return {
    safe,
    /** @param {{max: number, min: number, lines: number, lh: number}} spec */
    titleSpec(spec) {
      let max = safe ? spec.min : spec.max;
      const c = cap('title');
      if (c !== null) max = Math.min(max, c);
      return { ...spec, max, min: Math.min(spec.min, max) };
    },
    /** @param {number} px */
    bodyPx(px) {
      const c = cap('body');
      return c === null ? px : Math.min(px, c);
    },
    /** @param {string} slot */
    scrim(slot) {
      const s = fixOf(slot);
      return !!(s && s.scrim);
    },
  };
}

/**
 * Box of the centred title block (title variant).
 * @param {Zones} zones
 * @returns {Box}
 */
function centeredTitleBox(zones) {
  if (zones.format === 'landscape') return { x0: 192, y0: 54, x1: 1728, y1: 912 };
  return { x0: 192, y0: 340, x1: 888, y1: 840 };
}

/**
 * Per-card pieces that change inside a card: a blank step filling in, formula legend rows appearing on their word,
 * terminal lines, the figure.
 * @param {TemplateContext} ctx
 * @param {any} c
 * @param {number} t
 * @param {SceneState} state
 */
function paintParts(ctx, c, t, state) {
  const p = ctx.paint;
  const parts = c.parts;
  if (parts.blank && parts.filledText) {
    const fill = c.spec.fill;
    const f = fill === null || fill === undefined ? 0 : ease('ease.enter', progress(t, fill, 0.3));
    p.style(parts.blank, 'opacity', fx(1 - f));
    p.style(parts.filledText, 'opacity', fx(f));
  }
  if (parts.rows) {
    for (const row of parts.rows) {
      const a = row.at === null ? 1 : ease('ease.enter', progress(t, row.at - 0.12, 0.3));
      p.style(row.el, 'opacity', fx(a));
    }
  }
  if (parts.lines) {
    const shown = state.terminalLines.length;
    parts.lines.forEach((/** @type {HTMLElement} */ line, i) => p.style(line, 'visibility', i < shown ? 'visible' : 'hidden'));
  }
  if (parts.figure) parts.figure.update(p, t, ctx.frameInfo || {});
}
