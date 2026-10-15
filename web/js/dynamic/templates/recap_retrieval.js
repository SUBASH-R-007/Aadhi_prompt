// @ts-check
/**
 * `recap_retrieval` (DYNAMIC_VIDEO_DESIGN §4.3 A, R1, class `prompt`, R-08; B2 plan §1.4 template contract, §1.5 cues
 * and item states, §1.6 slots, §1.11 fit, §2, §P): the retrieval recap — prompts revealed, thought about against a
 * ring, then answered, and finally the lesson's big ideas as tiles.
 *
 * Data (§4.3 `recap_retrieval`, templates.py `_fit_recap_retrieval`): a `recap` BoardScene with
 * - `question` items. One with a `justification` is a **retrieval prompt**: the question is asked, the ring runs the
 *   think time, then the justification is revealed as the answer. One **without** a justification is a **free-recall
 *   prompt** ("Name the big ideas"), which the learner answers out loud: its ring runs until the next prompt or the
 *   first tile. A `paragraph` item is a free-recall prompt too (§4.3 "a free-recall prompt" is not a `question` kind).
 * - `takeaway` items: the relational big-idea **tiles** (§4.3: 3 of them, on `surface`, with icons).
 * - an optional `heading` item: the recap's label above the card (§4.3 gives the scene no title of its own).
 *
 * Nothing a timeline names is silently dropped (plan §1.4 invariant 2): a scene holding an item kind this template
 * does not draw, a second heading, no question at all, or a medium - a scene medium, a poster or a shown side panel,
 * which §4.3 gives the recap none of and `mediaBox` therefore plans no box for (`drawsScene`) - is drawn by
 * board_generic **whole**, with every hook handed on (formula_focus's and list_build's convention), and
 * `_fit_recap_retrieval` scores such a scene 0 so direct.py never sends it here (B2-demo review D5: without the
 * panel guard the picture beside a recap was planned by no one and vanished from the video).
 *
 * Layout. One **prompt stage** and one **tile stage**, each the content box of its format, because §4.3's recap is a
 * sequence ("a centred question card ... then the free-recall card ... then 3 tiles"): at most one prompt card is on
 * screen at a time (the one entered last), and it leaves when the next prompt or the first tile enters, inside a
 * crossfade. This is the design's own choreography, not text faded out to make room: a card that is up is reported in
 * `texts()` at the alpha it is painted at, and anything that does not fit at its minimum size is reported as overflow,
 * so preflight blocks with "Shorten text" rather than the page clipping it.
 * - **16:9** (§4.3): the prompt card is centred in `centre-60`; the tiles are 3 columns of the 12-column grid
 *   (560 px each, the grid's own 24 px gutters), their icons above their text, the row centred in the content box.
 * - **9:16** (§4.3 "1 question, free recall, 3 stacked tiles"): the card and the tiles fill the text column
 *   (x 192-780, y to 1020: the part of the stage both critical boxes hold, R-59), the tiles stacked, each icon beside
 *   its text. A tile's text box keeps the icon column's width whether or not icons are drawn (so the stored slot width
 *   is the one the page lays out, plan §1.11); without icons the text box is centred in the tile instead.
 * - Icons are all or nothing (§4.12 "no match means no icon"; B2c review V12): one tile without a stored icon and no
 *   tile draws one, so a row of tiles never has a hole in it.
 *
 * A card holds its question, then the ring and the answer in **one shared slot** beneath it: the ring's time is over
 * exactly when the answer is revealed, so the answer rises where the learner was already looking, and the card never
 * has to be tall enough for both at once (which overflowed both formats at R-29's sizes).
 *
 * Choreography (§4.3, §4.8): a card rises 24 px as it fades in (500 ms), its question's words follow 80 ms later with
 * the personality's text entrance; **the ring runs linearly** with no sound (R-07: the think time is the ring itself),
 * its number counting whole seconds down; on reveal the answer rises 16 px and takes the `done` item state, so an `ok`
 * check pops beside it and a 2 px `ok` border draws round it (plan §1.5, `_shared.js paintItemState`; §4.3's chime is
 * the resolver's SFX, not the page's); each tile flips in as it is named. Template durations take the personality's
 * scale (`_shared.js seconds` / `enterAt`); the think time does not (it is the narration's own pause, not a template
 * duration) and neither do the cues (cues.js).
 *
 * §4.3's tile flip is "rotateX 90° -> 0" with no perspective, which is exactly `scaleY(0 -> 1)`; it is painted as that
 * 2D scale, for the reason `_shared.js paintRise` gives for not using `translate3d` (B2c measured a 3D transform
 * rasterising up to 2 levels differently in reverse paint order; the 2D form gave 0 mismatches).
 *
 * Emphasis is cues.js's (§4.2, plan §1.4 / §1.5): `itemEl` hands it a prompt's question **and** its answer, in the
 * item's field order (text, justification), so `<item>` sweeps every [[term]] of both and `<item>:term:<n>` the n-th
 * over them; a tile hands it its text. `focusRect` gives a card (a shape, so its outline is drawn outside it) and
 * `dimGroups` the label, every prompt and every tile. Nothing here writes outline, text-decoration, filter,
 * backdrop-filter or will-change, and no structure role colours a tile, an answer or a caption (§4.2): only the ring's
 * arc, a structure mark, takes `struct.accent`.
 *
 * Determinism (§7.4, plan §P): `update` is a pure function of t; every property it paints is painted on every frame
 * with a value (never ''), so a frame never depends on paint order; translates are whole pixels; every size and
 * position is measured once in `layout` from the laid-out page and never changes with t.
 */

import { h, svg } from '../../shared/dom.js';
import { ease } from '../../motion/easing.js';
import { progress } from '../../motion/track.js';
import { strokeDash } from '../../motion/draw.js';
import { mathSpans, plainText } from '../../player/richtext.js';
import { LAYOUT_TOKENS, fixesOf, fx } from '../core.js';
import { itemTimings } from '../state.js';
import boardGeneric from './board_generic.js';
import {
  RADIUS, enterAt, fitLines, group, itemStateParts, paintItemState, paintRise, paintTextEntrance, place, region,
  revealMask, richWords, scrimmedBg, seconds, slotRange, storedIcon,
} from './_shared.js';

/** @typedef {import('./types.js').TemplateContext} TemplateContext */
/** @typedef {import('./types.js').TemplateModule} TemplateModule */
/** @typedef {import('./types.js').FocusRect} FocusRect */
/** @typedef {import('./types.js').TextEntry} TextEntry */
/** @typedef {import('../../shared/types.js').TimedScene} TimedScene */
/** @typedef {import('../../shared/types.js').BoardItem} BoardItem */
/** @typedef {import('../core.js').Box} Box */
/** @typedef {import('../core.js').Zones} Zones */

// ---------------------------------------------------------------------------------------------
// Motion (§4.3 recap_retrieval, §4.8)
// ---------------------------------------------------------------------------------------------

/** A card fades in and rises this far (§4.3 outro_next / definition_card: a card rises; §4.8 medium move). */
export const CARD_RISE = 24;
/** ... over 500 ms (§4.8 "medium move 400-500 ms"). */
export const CARD_ENTER = 0.5;
/** A card leaves 30% faster than it entered (§4.8 exits). */
export const CARD_EXIT = CARD_ENTER * 0.7;
/** The question's words follow its card by 80 ms (list_build's number-then-text rule, §4.3). */
export const TEXT_DELAY = 0.08;
/** ... and enter over 450 ms with the personality's text entrance. */
export const TEXT_ENTER = 0.45;
/** §4.3: "on reveal the answer rises 16 px" ... */
export const ANSWER_RISE = 16;
/** ... over 400 ms (§4.8 small-to-medium move). */
export const ANSWER_ENTER = 0.4;
/** ... and "an `ok` check pops" (the `done` item state, plan §1.5), over 300 ms with the `pop` easing (§4.8). */
export const CHECK_POP = 0.3;
/** §4.3: "tiles flip in (rotateX 90° -> 0, 400 ms) as each is named". */
export const TILE_FLIP = 0.4;
/** The ring fades in 300 ms after its card and leaves 30% faster (§4.8); it never moves (R-07). */
export const RING_DELAY = 0.25;
export const RING_IN = 0.3;
export const RING_OUT = RING_IN * 0.7;
/** A ring shorter than this is not drawn at all (nothing to think about). */
export const RING_MIN = 0.5;
/** The think time of a retrieval prompt no beat and no event answers (§4.3: the beat's `pause_after` is the think
 * time; without one, R-07's shortest useful think time). */
export const THINK_DEFAULT = 4;
/** The label's entrance (the cards' length). */
export const LABEL_ENTER = 0.45;

// ---------------------------------------------------------------------------------------------
// Layout (stage px; R-29 floors; the catalogue's regions and layout tokens)
// ---------------------------------------------------------------------------------------------

/** Line heights of the type scale (zones.py TYPE_SCALE, plan §2: Indic body >= 1.45, Indic heading >= 1.25;
 * recap_retrieval.test.js pins them to the catalogue's `type.scale`). */
export const LINE_HEIGHT = Object.freeze({
  heading: Object.freeze({ latin: 1.15, indic: 1.25 }),
  body: Object.freeze({ latin: 1.35, indic: 1.45 }),
  label: Object.freeze({ latin: 1.3, indic: 1.45 }),
});

/** A tile's icon: the catalogue's card icon size (§4.12, A2 proposal 96 px). */
export const ICON_SIZE = Number(LAYOUT_TOKENS.icon.card_px);
/** The think ring's stroke width (board_generic's quiz ring, dynamic.css `.dyn-ring-track`). */
export const RING_STROKE = 8;

/**
 * Per-format layout. `label` / `prompt` / `answer` / `tile`: each slot's size range, line limit and preferred line
 * count (R-29: labels >= 40 px in 16:9 and >= 44 px in 9:16; 16:9 headings 84-120, capped at board_generic's 96 so a
 * recap's question matches a board title; 16:9 body 48-56 down to 40 for a long answer, above the 36 px text floor;
 * 9:16 text at most 64 px is at least weight 600, so every 9:16 body slot here is 600). `ring`: the think ring's box,
 * `ringNum` its number's size (a label). `pad`: a card's padding; `gap`: between a card's parts; `tileGap`: between
 * tiles; `labelGap`: between the label and the card. `tileW`: a tile's width (16:9 three columns of the 12-column
 * grid: 4 columns of 122 px plus 3 of its 24 px gutters; 9:16 the text column, given at layout). `tilesPerRow`: tiles
 * side by side (16:9 §4.3's three; 9:16 stacked). `iconSide`: the icon sits beside the text (9:16) rather than above
 * it. recap_retrieval.test.js pins the slots this table gives (`slotRows`) to the catalogue's.
 */
export const LAYOUT = Object.freeze({
  landscape: Object.freeze({
    label: Object.freeze({ max: 40, min: 40, lines: 1, prefer: 1, weight: 600 }),
    prompt: Object.freeze({ max: 96, min: 84, lines: 3, prefer: 2, weight: 700 }),
    answer: Object.freeze({ max: 48, min: 40, lines: 3, prefer: 0, weight: 400 }),
    tile: Object.freeze({ max: 48, min: 40, lines: 3, prefer: 0, weight: 600 }),
    ring: 120,
    ringNum: 40,
    pad: 32,
    gap: 24,
    tileGap: 24,
    labelGap: 24,
    tileW: 560,
    tilesPerRow: 3,
    iconSide: false,
  }),
  portrait: Object.freeze({
    label: Object.freeze({ max: 44, min: 44, lines: 1, prefer: 1, weight: 600 }),
    prompt: Object.freeze({ max: 64, min: 56, lines: 3, prefer: 2, weight: 700 }),
    answer: Object.freeze({ max: 44, min: 40, lines: 3, prefer: 0, weight: 600 }),
    tile: Object.freeze({ max: 44, min: 40, lines: 2, prefer: 0, weight: 600 }),
    ring: 96,
    ringNum: 44,
    pad: 24,
    gap: 24,
    tileGap: 16,
    labelGap: 20,
    tileW: 0,
    tilesPerRow: 1,
    iconSide: true,
  }),
});

/** Size steps when fitting (board_generic's and textfit.py's 4 px). */
export const SIZE_STEP = 4;

/**
 * The boxes of a format (pure; whole stage px): `label` (the line above the card), `prompt` (where a prompt card is
 * centred, 16:9 the centre 60%, 9:16 the text column) and `tiles` (16:9 the whole content width, 9:16 the text
 * column). 16:9 runs from the title zone's top (y 112) to the body box's bottom (y 900, 24 px above the caption
 * band); 9:16 from y 340 to 1020, the part of the stage both critical boxes hold (R-59).
 * @param {'landscape' | 'portrait'} format
 * @param {Zones} zones
 * @returns {{label: Box, prompt: Box, tiles: Box}}
 */
export function boxes(format, zones) {
  const L = LAYOUT[format];
  const top = zones.title.y0;
  if (format === 'portrait') {
    const col = zones.textColumn;
    const box = { x0: col.x0, y0: top, x1: col.x1, y1: col.y1 };
    return { label: { ...box, y1: Math.round(top + L.label.max * LINE_HEIGHT.label.latin) }, prompt: box, tiles: box };
  }
  const ctx = { format };
  const full = region(ctx, 'full');
  const mid = region(ctx, 'centre-60');
  const bottom = zones.body.y1;
  return {
    label: { x0: mid.x0, y0: top, x1: mid.x1, y1: Math.round(top + L.label.max * LINE_HEIGHT.label.latin) },
    prompt: { x0: mid.x0, y0: top, x1: mid.x1, y1: bottom },
    tiles: { x0: full.x0, y0: top, x1: full.x1, y1: bottom },
  };
}

/** A tile's width in `format` (16:9 the fixed three-column width; 9:16 the text column). @param {'landscape' |
 * 'portrait'} format @param {Zones} zones */
export function tileWidth(format, zones) {
  if (format === 'landscape') return LAYOUT.landscape.tileW;
  const b = boxes(format, zones).tiles;
  return b.x1 - b.x0;
}

/**
 * A tile's text box width: the tile less its padding, less the icon column (9:16, where the icon sits beside the
 * text; the column is kept whether or not icons are drawn, so the stored slot width is the one the page lays out).
 * @param {'landscape' | 'portrait'} format @param {Zones} zones
 */
export function tileTextWidth(format, zones) {
  const L = LAYOUT[format];
  const inner = tileWidth(format, zones) - 2 * L.pad;
  return L.iconSide ? inner - ICON_SIZE - L.gap : inner;
}

/**
 * The text slots of this template (aadhi/compose/dynamic/templates.py SlotSpec, what textfit.py fits; plan §1.6,
 * §1.11): slot -> format -> variant -> the spec. One variant, `default` (§4.3 gives the template no look variant; the
 * number of tiles changes how many sit side by side, never a tile's width). Line heights are the Latin ones (Indic
 * text takes LINE_HEIGHT's Indic values, which textfit.py's Indic measure must use). Pure.
 * @param {Zones} landscape @param {Zones} portrait
 */
export function slotRows(landscape, portrait) {
  /** @param {'landscape' | 'portrait'} fmt @param {Zones} zones */
  const table = (fmt, zones) => {
    const L = LAYOUT[fmt];
    const b = boxes(fmt, zones);
    const cardText = b.prompt.x1 - b.prompt.x0 - 2 * L.pad;
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
      prompt: spec(L.prompt, cardText, 'title', 'display', LINE_HEIGHT.heading.latin),
      answer: spec(L.answer, cardText, 'body', 'body', LINE_HEIGHT.body.latin),
      tile: spec(L.tile, tileTextWidth(fmt, zones), 'body', 'body', LINE_HEIGHT.body.latin),
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
    out[slot] = { landscape: { default: l[/** @type {keyof typeof l} */ (slot)] }, portrait: { default: p[/** @type {keyof typeof p} */ (slot)] } };
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

/** The item kinds this template draws (§4.3; `_fit_recap_retrieval` allows exactly these). */
export const DRAWN_KINDS = Object.freeze(['heading', 'question', 'paragraph', 'takeaway']);

/** @param {BoardItem} item */
const textOf = (item) => (typeof item.text === 'string' ? item.text.trim() : '');

/**
 * What the scene gives this template: its `label` (the first `heading` item with text), its `prompts` (every
 * `question` item with text — with its `justification` as the `answer` when it has one, else a free-recall prompt —
 * and every `paragraph` item with text, a free-recall prompt too), and its `tiles` (every `takeaway` item with text),
 * each in board order.
 * @param {TimedScene} scene
 * @returns {{label: BoardItem | null, prompts: {item: BoardItem, answer: string | null}[], tiles: BoardItem[]}}
 */
export function recapItems(scene) {
  /** @type {BoardItem | null} */
  let label = null;
  /** @type {{item: BoardItem, answer: string | null}[]} */
  const prompts = [];
  /** @type {BoardItem[]} */
  const tiles = [];
  for (const item of scene.board || []) {
    if (!textOf(item)) continue;
    if (item.kind === 'heading') {
      if (!label) label = item;
    } else if (item.kind === 'question') {
      const answer = typeof item.justification === 'string' && item.justification.trim() ? item.justification.trim() : null;
      prompts.push({ item, answer });
    } else if (item.kind === 'paragraph') {
      prompts.push({ item, answer: null });
    } else if (item.kind === 'takeaway') {
      tiles.push(item);
    }
  }
  return { label, prompts, tiles };
}

/**
 * Whether this template draws the scene whole (templates.py `_fit_recap_retrieval` gives 0 otherwise): at least one
 * `question` item with text (§4.3's retrieval prompt), at most one `heading` with text (it draws one label), every
 * item with text of a kind it draws, and nothing to show beside them - §4.3 gives the recap no picture and this
 * template plans no media box, so a scene with a shown side panel or a scene medium would lose it (B2-demo review
 * D5). Anything else is board_generic's, every hook handed on.
 * @param {TimedScene} scene
 */
export function drawsScene(scene) {
  if (scene.media || scene.poster) return false;
  if (scene.side_panel && scene.layout?.show_side_panel !== false) return false;
  let questions = 0;
  let headings = 0;
  for (const item of scene.board || []) {
    if (!textOf(item)) continue;
    const kind = String(item.kind);
    if (!DRAWN_KINDS.includes(kind)) return false;
    if (kind === 'question') questions += 1;
    if (kind === 'heading') headings += 1;
  }
  return questions >= 1 && headings <= 1;
}

/**
 * The first event of kind `kind` on item `id` (its target's item part), or null. The stage applies only the kinds
 * `templates.py consumes` declares; these are read here because they say WHEN, not how (plan §4.2 "Python decides
 * when"): `answer` moves a prompt's reveal, `countdown` its ring.
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
 * The scene's timetable for this template (pure; scene-relative seconds): per prompt its entrance, when its card
 * leaves (`until`: the next prompt's entrance, else the first tile's, else the scene's end), when its answer appears
 * (`answerAt`: an `answer` event, else the beat that fills it — `state.js itemTimings` — else THINK_DEFAULT after the
 * entrance; null without an answer) and its ring (`ring`: a `countdown` event's start and length, else from the
 * entrance to the answer or to `until`; null when there is nothing to think about); and per tile its entrance.
 * @param {TimedScene} scene
 * @param {{item: BoardItem, answer: string | null}[]} prompts
 * @param {BoardItem[]} tiles
 * @param {BoardItem | null} [label]
 * @returns {{prompts: Map<string, {enter: number, until: number, exits: boolean, answerAt: number | null,
 *   ring: {start: number, seconds: number} | null}>, tiles: Map<string, number>, order: string[], label: number}}
 */
export function timetable(scene, prompts, tiles, label = null) {
  const timings = itemTimings(scene);
  /** @type {Map<string, {enter: number, fill: number | null}>} */
  const byId = new Map(timings.map((it) => [it.id, { enter: it.enter, fill: it.fill }]));
  const duration = Math.max(0, Number(scene.duration) || 0);
  const at = (/** @type {string} */ id) => {
    const t = byId.get(id);
    return t ? t.enter : 0;
  };
  /** @type {Map<string, number>} */
  const tileAt = new Map(tiles.map((item) => [item.id, at(item.id)]));
  const firstTile = tiles.length ? Math.min(...[...tileAt.values()]) : null;
  // prompts in entrance order (ties keep board order: itemTimings is already sorted that way)
  const ids = new Set(prompts.map((p) => p.item.id));
  const order = timings.filter((it) => ids.has(it.id)).map((it) => it.id);
  /** @type {Map<string, {enter: number, until: number, exits: boolean, answerAt: number | null,
   *   ring: {start: number, seconds: number} | null}>} */
  const out = new Map();
  for (const p of prompts) {
    const id = p.item.id;
    const enter = at(id);
    const i = order.indexOf(id);
    const next = i >= 0 && i + 1 < order.length ? at(order[i + 1]) : null;
    const ends = [next, firstTile].filter((x) => x !== null && x > enter);
    const until = ends.length ? Math.min(.../** @type {number[]} */ (ends)) : duration;
    /** @type {number | null} */
    let answerAt = null;
    if (p.answer !== null) {
      const ev = eventFor(scene, 'answer', id);
      const fill = byId.get(id)?.fill ?? null;
      answerAt = Math.max(enter, ev ? ev.t : fill !== null ? fill : enter + THINK_DEFAULT);
    }
    const countdown = eventFor(scene, 'countdown', id);
    const start = countdown ? countdown.t : enter + RING_DELAY;
    const stop = countdown && countdown.dur !== null ? start + countdown.dur : answerAt !== null ? answerAt : until;
    const length = Math.max(0, stop - start);
    // nothing comes after the last prompt of a scene with no tiles: it stays up to the end instead of fading out
    out.set(id, { enter, until, exits: ends.length > 0, answerAt, ring: length >= RING_MIN ? { start, seconds: length } : null });
  }
  return { prompts: out, tiles: tileAt, order, label: label ? at(label.id) : 0 };
}

/**
 * Every TeX string the template renders for the scene (the static `tex` hook; render.js converts them before `ready`):
 * the `$...$` spans of the label, of every prompt's question and answer and of every tile, inline (`richWords` mounts
 * each with display off), in the order they are built.
 * @param {TimedScene} scene
 * @returns {{tex: string, display: boolean}[]}
 */
export function texOf(scene) {
  const { label, prompts, tiles } = recapItems(scene);
  /** @type {{tex: string, display: boolean}[]} */
  const out = [];
  /** @param {string | null | undefined} text */
  const add = (text) => {
    for (const tex of mathSpans(String(text || ''))) if (tex.trim()) out.push({ tex, display: false });
  };
  if (label) add(label.text);
  for (const p of prompts) {
    add(p.item.text);
    add(p.answer);
  }
  for (const item of tiles) add(item.text);
  return out;
}

/**
 * The rows `n` tiles are laid out in: at most `per` per row, in order.
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

// ---------------------------------------------------------------------------------------------
// The template
// ---------------------------------------------------------------------------------------------

/** @type {TemplateModule} */
export default {
  id: 'recap_retrieval',

  /**
   * The DOM: the label, one card per prompt (question, ring, answer) and one card per tile (icon, text). No
   * measurement here (plan §1.4).
   * @param {TemplateContext} ctx
   */
  build(ctx) {
    const { scene, format } = ctx;
    if (!drawsScene(scene)) {
      // an item kind this template does not draw (a look naming it on such a scene): board_generic draws it whole
      const inner = boardGeneric.build(ctx);
      inner.rrFallback = true;
      return inner;
    }
    const L = LAYOUT[format];
    const { label, prompts: items, tiles: tileItems } = recapItems(scene);
    const plan = timetable(scene, items, tileItems, label);
    const fixes = fixesOf(ctx.look);
    const baseline = !!ctx.motion && ctx.motion.entrance === 'baseline_reveal';
    const surface = ctx.roles.color('surface');
    const ink = ctx.roles.color('ink');
    const ink2 = ctx.roles.color('ink2');
    const root = h('div', {
      class: ['dyn-rr', `dyn-rr--${format}`],
      style: { position: 'absolute', left: '0px', top: '0px', right: '0px', bottom: '0px' },
    });

    // the label above the card: on the background (R-53's scrim remedy behind it when preflight asked)
    /** @type {any} */
    let head = null;
    if (label) {
      const lh = lineHeight('label', label.text || '');
      const text = h('div', {
        class: 'dyn-rr-label', dir: 'auto',
        style: {
          margin: '0px', fontFamily: 'var(--dyn-font-body)', fontWeight: String(L.label.weight),
          lineHeight: String(lh), textAlign: 'center', overflowWrap: 'anywhere', color: ctx.onBackground.ink2,
        },
      });
      const { words, tex } = richWords(ctx, text, String(label.text || ''));
      const mask = baseline ? revealMask(text) : null;
      const scrim = fixes.slots.label && fixes.slots.label.scrim ? ctx.roles.color('scrim') : null;
      const el = group('dyn-rr-label-group', mask || text);
      el.style.position = 'absolute';
      if (scrim) Object.assign(el.style, { padding: '8px 24px 10px', borderRadius: `${RADIUS.chip}px`, background: scrim });
      root.appendChild(el);
      head = {
        item: label, el, text, mask, words, tex, lh, scrim, enter: plan.label,
        size: L.label.max, min: L.label.min, overflow: 0, alpha: 0,
      };
    }

    // one card per prompt: the question, the think ring, the answer (all absolutely placed at layout, so no part of a
    // card moves when another part appears)
    const prompts = items.map((p, k) => {
      const timing = /** @type {any} */ (plan.prompts.get(p.item.id));
      const qlh = lineHeight('heading', p.item.text || '');
      const question = h('div', {
        class: 'dyn-rr-q', dir: 'auto',
        style: {
          position: 'absolute', fontFamily: 'var(--dyn-font-display)', fontWeight: String(L.prompt.weight),
          lineHeight: String(qlh), textAlign: 'center', overflowWrap: 'anywhere', color: ink,
        },
      });
      const q = richWords(ctx, question, String(p.item.text || ''));
      const qmask = baseline ? revealMask(question) : null;
      if (qmask) qmask.style.position = 'absolute';
      // the think ring (R-07: the think time is a visual ring only; its arc is a structure mark)
      /** @type {any} */
      let ring = null;
      if (timing.ring) {
        const mid = L.ring / 2;
        const r = mid - RING_STROKE; // the stroke's centre line, so the ring stays inside its box
        const track = svg('circle', {
          cx: String(mid), cy: String(mid), r: String(r), class: 'dyn-rr-ring-track',
          fill: 'none', stroke: ink2, 'stroke-width': String(RING_STROKE), 'stroke-opacity': '0.35',
        });
        const arc = svg('circle', {
          cx: String(mid), cy: String(mid), r: String(r), class: 'dyn-rr-ring-arc', fill: 'none',
          stroke: ctx.roles.color('struct.accent'), 'stroke-width': String(RING_STROKE), 'stroke-linecap': 'round',
        });
        const face = svg('svg', { class: 'dyn-rr-ring-svg', width: String(L.ring), height: String(L.ring), 'aria-hidden': 'true' }, track, arc);
        face.setAttribute('viewBox', `0 0 ${L.ring} ${L.ring}`);
        // the arc starts at 12 o'clock: a quarter turn, set once (never painted)
        Object.assign(face.style, { display: 'block', transform: 'rotate(-90deg)' });
        const num = h('div', {
          class: 'dyn-rr-ring-num',
          style: {
            position: 'absolute', left: '0px', top: '0px', width: `${L.ring}px`, height: `${L.ring}px`,
            display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'var(--dyn-font-body)',
            fontSize: `${L.ringNum}px`, fontWeight: '700', fontVariantNumeric: 'tabular-nums', lineHeight: '1',
            color: ink,
          },
        });
        const box = h('div', {
          class: 'dyn-rr-ring',
          style: { position: 'absolute', width: `${L.ring}px`, height: `${L.ring}px` },
        }, face, num);
        ring = { box, arc, num, length: 2 * Math.PI * r, alpha: 0, text: '' };
      }
      // the answer: the `done` item state draws the ok check and the ok border on it (plan §1.5)
      /** @type {any} */
      let answer = null;
      if (p.answer !== null) {
        const alh = lineHeight('body', p.answer);
        const el = h('div', {
          class: 'dyn-rr-a', dir: 'auto',
          style: {
            position: 'absolute', boxSizing: 'border-box', fontFamily: 'var(--dyn-font-body)',
            lineHeight: String(alh), textAlign: 'center', overflowWrap: 'anywhere', borderRadius: `${RADIUS.card}px`,
          },
        });
        const a = richWords(ctx, el, p.answer);
        const parts = itemStateParts(el);
        answer = { el, parts, words: a.words, tex: a.tex, lh: alh, text: p.answer, size: L.answer.max, min: L.answer.min, overflow: 0, alpha: 0 };
      }
      const card = h('div', {
        class: 'dyn-rr-card', dataset: { item: p.item.id },
        style: {
          position: 'absolute', boxSizing: 'border-box', borderRadius: `${RADIUS.card}px`, background: surface,
          padding: `${L.pad}px`,
        },
      }, qmask || question, ring ? ring.box : null, answer ? answer.el : null);
      const el = group('dyn-rr-prompt', card);
      el.style.position = 'absolute';
      root.appendChild(el);
      return {
        p, k, el, card, question, qmask, words: q.words, wordTex: q.tex, qlh, ring, answer, timing,
        size: L.prompt.max, min: L.prompt.min, overflow: 0, alpha: 0, qalpha: 0,
      };
    });

    // the tiles: §4.3's big-idea tiles on `surface`, with their stored icons (all or nothing, B2c review V12)
    const drawn = tileItems.map((item) => storedIcon(ctx, item.id, { size: ICON_SIZE, block: true }));
    const icons = drawn.length > 0 && drawn.every(Boolean) ? drawn : null;
    const tiles = tileItems.map((item, k) => {
      const lh = lineHeight('body', item.text || '');
      const text = h('div', {
        class: 'dyn-rr-t', dir: 'auto',
        style: {
          position: 'absolute', fontFamily: 'var(--dyn-font-body)', fontWeight: String(L.tile.weight),
          lineHeight: String(lh), textAlign: L.iconSide ? 'start' : 'center', overflowWrap: 'anywhere', color: ink,
        },
      });
      const { words, tex } = richWords(ctx, text, String(item.text || ''));
      // the icon takes the tile's own ink, a content role (§4.2: never a structure role on content; §4.3: a tile
      // never takes the chapter accent)
      const glyph = icons ? h('div', { class: 'dyn-rr-icon', style: { position: 'absolute', color: ink } }, icons[k]) : null;
      const card = h('div', {
        class: 'dyn-rr-tile', dataset: { item: item.id },
        style: {
          position: 'absolute', boxSizing: 'border-box', borderRadius: `${RADIUS.card}px`, background: surface,
          padding: `${L.pad}px`, transformOrigin: 'center center',
        },
      }, glyph, text);
      const el = group('dyn-rr-tile-group', card);
      el.style.position = 'absolute';
      root.appendChild(el);
      return { item, k, el, card, text, words, wordTex: tex, lh, glyph, enter: plan.tiles.get(item.id) || 0, size: L.tile.max, min: L.tile.min, overflow: 0, alpha: 0 };
    });

    ctx.root.appendChild(root);
    return {
      root, head, prompts, tiles, icons: !!icons, plan, surface, ink, ink2,
      promptSize: L.prompt.max, answerSize: L.answer.max, tileSize: L.tile.max,
      promptMin: L.prompt.min, answerMin: L.answer.min, tileMin: L.tile.min,
    };
  },

  /**
   * Measure and fit (runs again when a late face arrives; idempotent): the label in its line, then every prompt card's
   * question at one common size and every answer at one common size (so a card never resizes text another card
   * already set), then the tiles at one common size. Positions are set here, once; `update` only paints.
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
    let promptTop = b.prompt.y0;
    if (v.head) {
      const hd = v.head;
      const range = slotRange(ctx, 'label', { min: L.label.min, max: L.label.max, role: 'label' });
      Object.assign(hd.el.style, { left: `${b.label.x0}px`, top: `${b.label.y0}px`, width: `${b.label.x1 - b.label.x0}px` });
      const fit = fitLines(hd.text, { max: range.max, min: range.min, lines: L.label.lines, prefer: L.label.prefer, lh: hd.lh, step: SIZE_STEP }, hd.words, hd.tex);
      hd.size = fit.size;
      hd.min = range.min;
      hd.overflow = fit.overflow;
      const bottom = hd.el.offsetTop + hd.el.offsetHeight;
      promptTop = Math.round(Math.max(bottom, b.label.y0 + range.min * hd.lh) + L.labelGap);
    }

    // --- the prompt cards --------------------------------------------------------------------------
    const cardW = b.prompt.x1 - b.prompt.x0;
    const innerW = cardW - 2 * L.pad;
    const promptH = Math.max(0, b.prompt.y1 - promptTop);
    const qRange = slotRange(ctx, 'prompt', { min: L.prompt.min, max: L.prompt.max, role: 'title' });
    const aRange = slotRange(ctx, 'answer', { min: L.answer.min, max: L.answer.max, role: 'body' });
    v.promptMin = qRange.min;
    v.answerMin = aRange.min;
    for (const pr of v.prompts) {
      Object.assign(pr.el.style, { left: `${b.prompt.x0}px`, top: `${promptTop}px`, width: `${cardW}px` });
      Object.assign(pr.card.style, { left: '0px', width: `${cardW}px` });
      const host = pr.qmask || pr.question;
      Object.assign(host.style, { left: `${L.pad}px`, width: `${innerW}px` });
      if (pr.qmask) Object.assign(pr.question.style, { position: 'relative', left: '0px', width: `${innerW}px` });
      if (pr.answer) Object.assign(pr.answer.el.style, { left: `${L.pad}px`, width: `${innerW}px` });
    }
    // one size for every question, one for every answer: the smallest each fits at
    /** @type {{size: number, overflow: number}[]} */
    const qFits = v.prompts.map((/** @type {any} */ pr) => fitLines(pr.question,
      { max: qRange.max, min: qRange.min, lines: L.prompt.lines, prefer: L.prompt.prefer, lh: pr.qlh, step: SIZE_STEP }, pr.words, pr.wordTex));
    const qSize = qFits.length ? Math.min(...qFits.map((f) => f.size)) : qRange.max;
    /** @type {{size: number, overflow: number}[]} */
    const aFits = v.prompts.map((/** @type {any} */ pr) => (pr.answer
      ? fitLines(pr.answer.el, { max: aRange.max, min: aRange.min, lines: L.answer.lines, lh: pr.answer.lh, step: SIZE_STEP }, pr.answer.words, pr.answer.tex)
      : { size: aRange.max, overflow: 0 }));
    const aSize = aFits.length ? Math.min(...aFits.map((f) => f.size)) : aRange.max;
    v.promptSize = qSize;
    v.answerSize = aSize;
    v.prompts.forEach((/** @type {any} */ pr, k) => {
      pr.question.style.fontSize = `${qSize}px`;
      pr.size = qSize;
      pr.min = qRange.min;
      if (pr.answer) {
        pr.answer.el.style.fontSize = `${aSize}px`;
        pr.answer.size = aSize;
        pr.answer.min = aRange.min;
        pr.answer.overflow = aFits[k].overflow;
      }
      // the parts, top to bottom, from what the page laid out at those sizes. The ring and the answer share one slot
      // under the question: the ring's time is over exactly when the answer is revealed, so the answer rises where
      // the learner was already looking, and a card never has to hold both at full height.
      const qH = Math.max(Math.round(qSize * pr.qlh), (pr.qmask || pr.question).offsetHeight);
      let y = L.pad;
      (pr.qmask || pr.question).style.top = `${y}px`;
      y += qH;
      let under = 0;
      if (pr.ring || pr.answer) y += L.gap;
      if (pr.ring) {
        Object.assign(pr.ring.box.style, { left: `${Math.round((cardW - L.ring) / 2)}px`, top: `${y}px` });
        under = L.ring;
      }
      if (pr.answer) {
        pr.answer.el.style.top = `${y}px`;
        under = Math.max(under, Math.round(aSize * pr.answer.lh), pr.answer.el.offsetHeight);
      }
      const cardH = y + under + L.pad;
      pr.card.style.height = `${cardH}px`;
      pr.card.style.top = `${Math.max(0, Math.round((promptH - cardH) / 2))}px`;
      pr.overflow = Math.max(qFits[k].overflow, Math.max(0, cardH - promptH));
    });

    // --- the tiles ---------------------------------------------------------------------------------
    const tw = tileWidth(format, zones);
    const textW = tileTextWidth(format, zones);
    const tRange = slotRange(ctx, 'tile', { min: L.tile.min, max: L.tile.max, role: 'body' });
    v.tileMin = tRange.min;
    for (const tile of v.tiles) {
      Object.assign(tile.card.style, { left: '0px', top: '0px', width: `${tw}px` });
      Object.assign(tile.text.style, { width: `${textW}px` });
    }
    /** @type {{size: number, overflow: number}[]} */
    const tFits = v.tiles.map((/** @type {any} */ tile) => fitLines(tile.text,
      { max: tRange.max, min: tRange.min, lines: L.tile.lines, lh: tile.lh, step: SIZE_STEP }, tile.words, tile.wordTex));
    const tSize = tFits.length ? Math.min(...tFits.map((f) => f.size)) : tRange.max;
    v.tileSize = tSize;
    let textH = 0;
    v.tiles.forEach((/** @type {any} */ tile, k) => {
      tile.text.style.fontSize = `${tSize}px`;
      tile.size = tSize;
      tile.min = tRange.min;
      textH = Math.max(textH, Math.round(tSize * tile.lh), tile.text.offsetHeight);
      tile.fit = tFits[k].overflow;
    });
    // every tile the same height: the tallest text, with the icon above it (16:9) or beside it (9:16)
    const iconRoom = v.icons && !L.iconSide ? ICON_SIZE + L.gap : 0;
    const tileH = 2 * L.pad + (L.iconSide ? Math.max(textH, v.icons ? ICON_SIZE : 0) : iconRoom + textH);
    const rows = tileRows(v.tiles.length, L.tilesPerRow);
    const blockH = rows.length ? rows.length * tileH + (rows.length - 1) * L.tileGap : 0;
    // the tiles share the content box with the cards, but never with the label, which stays up through the scene
    const tilesTop = Math.max(b.tiles.y0, promptTop);
    const tilesH = Math.max(0, b.tiles.y1 - tilesTop);
    const top0 = tilesTop + Math.max(0, Math.round((tilesH - blockH) / 2));
    const boxW = b.tiles.x1 - b.tiles.x0;
    rows.forEach((row, r) => {
      const rowW = row.length * tw + (row.length - 1) * L.tileGap;
      const x0 = b.tiles.x0 + Math.max(0, Math.round((boxW - rowW) / 2));
      const y = top0 + r * (tileH + L.tileGap);
      row.forEach((index, c) => {
        const tile = v.tiles[index];
        place(tile.el, { x0: x0 + c * (tw + L.tileGap), y0: y, x1: x0 + c * (tw + L.tileGap) + tw, y1: y + tileH });
        tile.card.style.height = `${tileH}px`;
        const inner = tileH - 2 * L.pad;
        if (L.iconSide) {
          if (tile.glyph) {
            Object.assign(tile.glyph.style, {
              left: `${L.pad}px`, top: `${L.pad + Math.max(0, Math.round((inner - ICON_SIZE) / 2))}px`,
              width: `${ICON_SIZE}px`, height: `${ICON_SIZE}px`,
            });
          }
          const tx = v.icons ? L.pad + ICON_SIZE + L.gap : Math.round((tw - textW) / 2);
          Object.assign(tile.text.style, { left: `${tx}px`, top: `${L.pad + Math.max(0, Math.round((inner - textH) / 2))}px` });
        } else {
          if (tile.glyph) {
            Object.assign(tile.glyph.style, {
              left: `${Math.round((tw - ICON_SIZE) / 2)}px`, top: `${L.pad}px`, width: `${ICON_SIZE}px`, height: `${ICON_SIZE}px`,
            });
          }
          Object.assign(tile.text.style, { left: `${Math.round((tw - textW) / 2)}px`, top: `${L.pad + iconRoom}px` });
        }
        tile.overflow = Math.max(tile.fit || 0, Math.max(0, blockH - tilesH));
      });
    });
  },

  /**
   * Paint scene time t: the label's entrance; each prompt card's entrance, its question's words, its ring and its
   * answer; each tile's flip. A pure function of t: every property painted on an element is painted on every frame,
   * with a value (never '').
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

    if (v.head) {
      const e = enterAt(ctx, t, v.head.enter, LABEL_ENTER);
      v.head.alpha = e;
      paintTextEntrance(ctx, v.head.text, v.head.mask, e);
    }

    const exitDur = seconds(ctx, CARD_EXIT);
    const delay = seconds(ctx, TEXT_DELAY);
    for (const pr of v.prompts) {
      const timing = pr.timing;
      const inP = enterAt(ctx, t, timing.enter, CARD_ENTER);
      // the card leaves when the next prompt or the first tile enters (§4.3's sequence), never before it is in
      const exitAt = Math.max(timing.enter, timing.until - exitDur);
      const out = timing.exits && timing.until > timing.enter ? ease('ease.exit', progress(t, exitAt, exitDur)) : 0;
      const alpha = inP * (1 - out);
      pr.alpha = alpha;
      const dy = ctx.reducedMotion || inP >= 1 ? 0 : Math.round((1 - inP) * CARD_RISE);
      p.style(pr.card, 'opacity', fx(alpha));
      p.style(pr.card, 'visibility', alpha > 0.001 ? 'visible' : 'hidden');
      p.style(pr.card, 'transform', dy ? `translate(0, ${dy}px)` : 'none'); // 2D: see _shared.js paintRise
      const eText = enterAt(ctx, t, timing.enter + delay, TEXT_ENTER);
      pr.qalpha = alpha * eText;
      paintTextEntrance(ctx, pr.question, pr.qmask, eText);

      if (pr.ring) {
        const ring = pr.ring;
        const start = timing.ring.start;
        const secs = Math.max(0.001, timing.ring.seconds);
        const shown = enterAt(ctx, t, start, RING_IN) * (1 - ease('ease.exit', progress(t, start + secs, seconds(ctx, RING_OUT))));
        // linear, with no sound (§4.3): the arc empties as the think time runs out
        const left = 1 - progress(t, start, secs);
        const dash = strokeDash(ring.length, left);
        ring.alpha = alpha * shown;
        ring.text = shown > 0.001 ? String(Math.max(0, Math.ceil(secs * left - 1e-6))) : '';
        p.style(ring.box, 'opacity', fx(shown));
        p.style(ring.box, 'visibility', shown > 0.001 ? 'visible' : 'hidden');
        p.attr(ring.arc, 'stroke-dasharray', dash.dasharray);
        p.attr(ring.arc, 'stroke-dashoffset', dash.dashoffset);
        p.text(ring.num, ring.text);
      }

      if (pr.answer) {
        const a = pr.answer;
        const at = /** @type {number} */ (timing.answerAt);
        const e = enterAt(ctx, t, at, ANSWER_ENTER);
        a.alpha = alpha * e;
        paintRise(ctx, a.el, e, ANSWER_RISE);
        p.style(a.el, 'visibility', e > 0.001 ? 'visible' : 'hidden');
        // the `done` state: the ok check pops and the ok border draws (plan §1.5)
        const k = ease('pop', progress(t, at, seconds(ctx, CHECK_POP)));
        paintItemState(ctx, a.el, a.parts, e > 0 ? 'done' : null, k, { weight: L.answer.weight, ink: v.ink });
      }
    }

    for (const tile of v.tiles) {
      // the flip: §4.3's rotateX 90° -> 0 without perspective, painted as the 2D scale it is
      const e = enterAt(ctx, t, tile.enter, TILE_FLIP);
      tile.alpha = e;
      p.style(tile.card, 'opacity', fx(e));
      p.style(tile.card, 'visibility', e > 0.001 ? 'visible' : 'hidden');
      p.style(tile.card, 'transform', ctx.reducedMotion || e >= 1 ? 'none' : `scaleY(${fx(Math.max(0.001, e))})`);
    }
  },

  /**
   * Where a look_here points (plan §1.4, §1.5, §1.7): a prompt's card or a tile's card (`<item>`, or
   * `<item>:term:<n>`: the same card) — a shape, so cues.js draws its outline whole just outside the card
   * (`shapeArea`), never across the text — or the label's text. Its box as painted now (read after `update`) on whole
   * stage px, with the dim group holding it. Null for anything else or off the page.
   * @param {TemplateContext} ctx
   * @param {string} target
   * @returns {FocusRect | null}
   */
  focusRect(ctx, target) {
    if (isFallback(ctx)) return typeof boardGeneric.focusRect === 'function' ? boardGeneric.focusRect(ctx, target) : null;
    const v = ctx.view;
    const id = String(target || '').split(':')[0];
    const prompt = v.prompts.find((/** @type {any} */ x) => x.p.item.id === id);
    const tile = v.tiles.find((/** @type {any} */ x) => x.item.id === id);
    const head = v.head && v.head.item.id === id ? v.head : null;
    const el = prompt ? prompt.card : tile ? tile.card : head ? head.text : null;
    const holder = prompt ? prompt.el : tile ? tile.el : head ? head.el : null;
    const r = el ? ctx.rectOf(el) : null;
    if (!r || !holder) return null;
    const x0 = Math.round(r.x);
    const y0 = Math.round(r.y);
    const x1 = Math.round(r.x + r.w);
    const y1 = Math.round(r.y + r.h);
    const card = !!(prompt || tile);
    return {
      rect: { x: x0, y: y0, w: x1 - x0, h: y1 - y0 }, shape: 'rect', radius: card ? RADIUS.card : 0, el: holder,
      kind: card ? 'shape' : 'text',
    };
  },

  /**
   * The elements holding an item's text, where cue.term finds its [[term]] spans (`.rt-k`, one per word piece with the
   * term's `data-dyn-term`, `_shared.js richWords`): a prompt's question and then its answer, in the item's field
   * order (text, justification), so `<item>:term:<n>` counts over both; a tile's text; the label's text. Null for
   * anything else (board_generic's answer for a scene it draws).
   * @param {TemplateContext} ctx
   * @param {string} itemId
   * @returns {Element | Element[] | null}
   */
  itemEl(ctx, itemId) {
    if (isFallback(ctx)) return typeof boardGeneric.itemEl === 'function' ? boardGeneric.itemEl(ctx, itemId) : null;
    const v = ctx.view;
    const prompt = v.prompts.find((/** @type {any} */ x) => x.p.item.id === itemId);
    if (prompt) return prompt.answer ? [prompt.question, prompt.answer.el] : prompt.question;
    const tile = v.tiles.find((/** @type {any} */ x) => x.item.id === itemId);
    if (tile) return tile.text;
    return v.head && v.head.item.id === itemId ? v.head.text : null;
  },

  /**
   * The content groups a look_here dims (all but the target's): the label, every prompt, every tile; the same
   * elements at every t.
   * @param {TemplateContext} ctx
   * @returns {Element[]}
   */
  dimGroups(ctx) {
    if (isFallback(ctx)) return typeof boardGeneric.dimGroups === 'function' ? boardGeneric.dimGroups(ctx) : [];
    const v = ctx.view;
    return [v.head ? v.head.el : null, ...v.prompts.map((/** @type {any} */ x) => x.el),
      ...v.tiles.map((/** @type {any} */ x) => x.el)].filter(Boolean);
  },

  /**
   * The morph anchor (plan §1.4): none (a recap has no single shared visual; B2f pairs templates that have one).
   * @param {TemplateContext} _ctx
   * @returns {null}
   */
  anchor(_ctx) {
    return null;
  },

  /**
   * Static: this template shows no picture or clip (§4.3 gives the recap none), so no slot has a media box; a scene it
   * hands to board_generic keeps board_generic's.
   * @param {TimedScene} scene
   * @param {Zones} zones
   * @param {'live' | 'render'} mode
   * @param {string | undefined} slot
   * @param {import('../core.js').Fixes} fixes
   * @returns {{w: number, h: number} | null}
   */
  mediaBox(scene, zones, mode, slot, fixes) {
    if (!drawsScene(scene)) return typeof boardGeneric.mediaBox === 'function' ? boardGeneric.mediaBox(scene, zones, mode, slot, fixes) : null;
    return null;
  },

  /**
   * Static: every TeX string the template renders for the scene (`texOf`; the same in both formats).
   * @param {TimedScene} scene
   * @param {import('../../shared/types.js').ResolvedLook | null} look
   * @param {'landscape' | 'portrait'} fmt
   */
  tex(scene, look, fmt) {
    if (!drawsScene(scene)) return typeof boardGeneric.tex === 'function' ? boardGeneric.tex(scene, look, fmt) : [];
    return texOf(scene);
  },

  /**
   * The text on screen (preflight, §7.12): the label (slot `label`, on the background or on its R-53 scrim), every
   * question (slot `prompt`), every answer (slot `answer`), every ring's number (slot `ring`, not critical) and every
   * tile (slot `tile`) — the card ones on the card's `surface`, in the ink, size and weight painted now.
   * @param {TemplateContext} ctx
   * @returns {TextEntry[]}
   */
  texts(ctx) {
    if (isFallback(ctx)) return typeof boardGeneric.texts === 'function' ? boardGeneric.texts(ctx) : [];
    const v = ctx.view;
    const L = LAYOUT[ctx.format];
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
    for (const pr of v.prompts) {
      out.push({
        el: pr.question, role: 'title', slot: 'prompt', critical: true, ink: v.ink, bg: v.surface,
        alpha: pr.qalpha, overflow: pr.overflow, size: pr.size, min: pr.min, weight: L.prompt.weight,
        text: plainText(String(pr.p.item.text || '')),
      });
      if (pr.ring && pr.ring.text) {
        out.push({
          el: pr.ring.num, role: 'label', slot: 'ring', critical: false, ink: v.ink, bg: v.surface,
          alpha: pr.ring.alpha, size: L.ringNum, min: L.ringNum, weight: 700, text: pr.ring.text,
        });
      }
      if (pr.answer) {
        out.push({
          el: pr.answer.el, role: 'body', slot: 'answer', critical: true, ink: v.ink, bg: v.surface,
          alpha: pr.answer.alpha, overflow: pr.answer.overflow, size: pr.answer.size, min: pr.answer.min,
          weight: L.answer.weight, text: plainText(String(pr.answer.text || '')),
        });
      }
    }
    for (const tile of v.tiles) {
      out.push({
        el: tile.text, role: 'body', slot: 'tile', critical: true, ink: v.ink, bg: v.surface,
        alpha: tile.alpha, overflow: tile.overflow, size: tile.size, min: tile.min, weight: L.tile.weight,
        text: plainText(String(tile.item.text || '')),
      });
    }
    return out;
  },
};

/** @param {TemplateContext} ctx */
const isFallback = (ctx) => !!(ctx.view && ctx.view.rrFallback);
