// @ts-check
/**
 * Shared parts of the B2 scene templates (DYNAMIC_VIDEO_DESIGN §4.2-§4.8; B2 plan §1.4, §1.5, §1.11, §P), so
 * every template lays out, fits, reveals, draws icons, media and formula tints the same way. A template module,
 * scanned by every template rule of the source scan (web/tests/dynamic/sourceScan.test.js: no clock, no emphasis, no
 * will-change / filter, TeX only through `ctx.renderTex`, catalogue geometry only through core.js, no `.timeline`).
 *
 * What is here, and the rule each part keeps:
 * - Geometry on the catalogue (plan §1.1): `region`, `gridSpan`, `boxRect`, `insetBox`, `place`, SPACE, RADIUS.
 * - Text: `rich` / `tex` (renderRich / mountTex with the context's converter), `richWords` (a rich-lite string split
 *   into word spans that keep their markup, [[term]] `.rt-k` spans included, so cue.term finds them, plan §1.5),
 *   `fitLines` (the page's fit: the largest size in 4 px steps whose line count fits, board_generic's rule, which
 *   textfit.py mirrors), `slotRange` (a slot's size range with preflight's fixes and the R-29 floor).
 * - Motion (§4.8): `seconds` (the personality's duration scale), `enterAt`, `paintRise` (fade and rise on whole
 *   pixels), `revealMask` + `paintTextEntrance` (the personality's text entrance), `group` (a dim group the stage owns,
 *   plan §1.4).
 * - Item states (plan §1.5, state.js ITEM_STATES): `itemStateParts`, `layoutStrike`, `paintItemState`.
 * - Scrims (§7.12 scrim fix): `scrimmedBg`, the colour text on a scrim is reported on (moved here from board_generic
 *   in B2c, which re-exports it).
 * - Icons (plan §1.2, B2b): `sanitiseIcon` / `storedIcon` draw `look.icons[item]` after checking it again against the
 *   schema's pattern and caps (the `LookIcon` model of aadhi.schemas.timeline and its JSON schema in web/schemas),
 *   path data set with setAttribute only.
 * - Media (§7.7, §P): `mediaAspect`, `containRect`, `pushHeadroom` (a push_ok template's picture box), `paintMedia`.
 * - Side panels (§4.3 "the scene's panel", B2-demo): `panelOf` / `drawsPanel` (which panels a template may claim, the
 *   one question its Python `fit` must mirror), `buildPanel` / `layoutPanel` / `paintPanel` and the geometry and
 *   reporting hooks `panelFocusRect` / `panelTexts` / `panelMediaBox` - the three forms board_generic and media_split
 *   already draw (a medium, a terminal card, the panel's title on the card surface) and nothing else.
 * - Formula tints (§4.7 entity colours, §P (g)): `tintTex` wraps each symbol of a formula in a `\color` placeholder,
 *   `tintTargets` finds the placeholder's SVG groups after MathJax, `paintTint` colours them per frame.
 * Everything a template paints per frame goes through `ctx.paint` (the frame key); build- and layout-time placement
 * (`place`, `fitLines`) writes styles directly, as board_generic's layout does: it is a function of the laid-out page.
 */

import { h, svg } from '../../shared/dom.js';
import { ease } from '../../motion/easing.js';
import { progress } from '../../motion/track.js';
import { composite, contrastRatio } from '../../motion/color.js';
import { splitWords } from '../../motion/text.js';
import { mountTex, parseRich, renderRich } from '../../player/richtext.js';
import { icon } from '../../player/icons.js';
import { planScene } from '../../player/schedule.js';
import { GRID, LAYOUT_TOKENS, REGIONS, TYPE_FLOORS, fixesOf, fx, parseTarget, templateSlots } from '../core.js';
import { shownIn } from '../media.js';
import { itemStateStyle } from '../state.js';
import { PUSH_SCALE_MAX } from '../cues.js';

/** @typedef {import('./types.js').TemplateContext} TemplateContext */
/** @typedef {import('../core.js').Box} Box */
/** @typedef {import('../core.js').SlotSpec} SlotSpec */
/** @typedef {import('../../shared/types.js').MediaRef} MediaRef */
/** @typedef {import('../../shared/types.js').TimedScene} TimedScene */

// ---------------------------------------------------------------------------------------------
// Geometry (the catalogue's zones, regions, grid and layout tokens; plan §1.1)
// ---------------------------------------------------------------------------------------------

/** The 8 px spacing steps (8, 16, 24, 32, 40, 48, 64, 80, 96, 128; A2 proposal, pending owner review). */
export const SPACE = Object.freeze([...LAYOUT_TOKENS.spacing.steps]);
/** Corner radii: card 24, media 24, chip (pill) 999, presenter card 40. */
export const RADIUS = Object.freeze({ ...LAYOUT_TOKENS.radius });

/**
 * A named region of the scene's format (§2.5; core.js REGIONS): 16:9 `full`, `left-55`, `right-45`, `centre-60`,
 * `top-band`, `stage`; 9:16 `hook`, `headline`, `stage`, `tall`, `clip`, `text-above`, `text-below`. Throws for a name
 * the format does not have (a typo fails in tests).
 * @param {{format: 'landscape' | 'portrait'}} ctx
 * @param {string} name
 * @returns {Box}
 */
export function region(ctx, name) {
  const table = REGIONS[ctx.format];
  if (!Object.prototype.hasOwnProperty.call(table, name)) throw new RangeError(`no region ${name} in ${ctx.format}`);
  const b = table[name];
  return { x0: b.x0, y0: b.y0, x1: b.x1, y1: b.y1 };
}

/**
 * Columns `first`..`last` (0-based, inclusive) of the 16:9 grid (12 columns of 122 px, 24 px gutters), full height of
 * the grid box.
 * @param {number} first @param {number} last
 * @returns {Box}
 */
export function gridSpan(first, last) {
  const g = GRID.landscape;
  const a = Math.max(0, Math.min(first, last));
  const b = Math.min(g.columns - 1, Math.max(first, last));
  const step = g.column_width + g.gutter;
  return { x0: g.box.x0 + a * step, y0: g.box.y0, x1: g.box.x0 + b * step + g.column_width, y1: g.box.y1 };
}

/** @param {Box} b */
export function boxRect(b) {
  return { x: b.x0, y: b.y0, w: b.x1 - b.x0, h: b.y1 - b.y0 };
}

/**
 * A box grown (negative) or shrunk (positive) by `px` on every side.
 * @param {Box} b @param {number} px
 * @returns {Box}
 */
export function insetBox(b, px) {
  return { x0: b.x0 + px, y0: b.y0 + px, x1: b.x1 - px, y1: b.y1 - px };
}

/**
 * Place an absolutely positioned element on a box (whole px; build / layout time, not per frame).
 * @param {HTMLElement} el @param {Box} b
 */
export function place(el, b) {
  el.style.position = 'absolute';
  el.style.left = `${Math.round(b.x0)}px`;
  el.style.top = `${Math.round(b.y0)}px`;
  el.style.width = `${Math.round(b.x1 - b.x0)}px`;
  el.style.height = `${Math.round(b.y1 - b.y0)}px`;
}

// ---------------------------------------------------------------------------------------------
// Text
// ---------------------------------------------------------------------------------------------

/**
 * Rich-lite text (`**bold**`, `*italic*`, `` `code` ``, `$TeX$`, `[[term]]`) as DOM, its TeX through the context's
 * converter (the render page's counted one; plan §2). A [[term]] stays a `.rt-k` span (cue.term's target).
 * @param {TemplateContext} ctx @param {string | null | undefined} text
 */
export function rich(ctx, text) {
  return renderRich(text, { pending: ctx.pending, renderTex: ctx.renderTex });
}

/**
 * Mount TeX into `host` through the context's converter (declare the same string in the template's static `tex()`).
 * @param {TemplateContext} ctx @param {HTMLElement} host @param {string} latex @param {boolean} display
 */
export function tex(ctx, host, latex, display) {
  mountTex(host, latex, display, { pending: ctx.pending, renderTex: ctx.renderTex });
}

/** Stands for one TeX or code span in the plain text a rich string's words are found in: one unbreakable unit. */
const OBJECT_CHAR = '￼';

/**
 * Split rich-lite text into word spans (`.dyn-word`, Intl.Segmenter units through motion/text.js splitWords, so Indic
 * clusters stay whole), each holding the part of the markup its word covers: a [[term]] over two words becomes one
 * `.rt-k` piece per word (and one for the space between), each carrying the term's ordinal in the text
 * (`data-dyn-term`), so cue.term sweeps them as one term, left to right (cues.js termsOf); TeX and code spans are
 * single units that join the word they touch. board_generic's quiz-title splitter (`richWordSpans`), shared.
 * `tex[i]`: word i holds TeX (an atomic picture: `fitLines` measures it by its width).
 * @param {TemplateContext} ctx
 * @param {HTMLElement} host
 * @param {string} text
 * @returns {{words: HTMLElement[], tex: boolean[]}}
 */
export function richWords(ctx, host, text) {
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
  walk(parseRich(String(text || '')), []);
  /** @type {Map<Mark, string>} each [[term]]'s ordinal in the text: its word pieces share it (`data-dyn-term`) */
  const termIds = new Map();
  /** @type {number[]} the leaf of every character of `plain` */
  const owner = [];
  leaves.forEach((leaf, i) => {
    const length = leaf.node.t === 'text' ? leaf.node.v.length : 1;
    for (let k = 0; k < length; k++) owner.push(i);
  });
  /** @param {Node} parent @param {number} a @param {number} b @returns {boolean} the range holds TeX */
  const fill = (parent, a, b) => {
    /** @type {{mark: Mark, el: HTMLElement}[]} */
    const open = [];
    let hasTex = false;
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
        if (mark.t === 'k') {
          if (!termIds.has(mark)) termIds.set(mark, String(termIds.size));
          el.setAttribute('data-dyn-term', /** @type {string} */ (termIds.get(mark)));
        }
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
        hasTex = true;
      }
      k = end;
    }
    return hasTex;
  };
  /** @type {HTMLElement[]} */
  const words = [];
  /** @type {boolean[]} */
  const texFlags = [];
  for (const part of splitWords(plain, ctx.locale)) {
    const end = part.index + part.text.length;
    const span = h('span', { class: 'dyn-word' });
    texFlags.push(fill(span, part.index, end));
    words.push(span);
    host.appendChild(span);
    if (part.ws) fill(host, end, end + part.ws.length);
  }
  return { words, tex: texFlags };
}

/**
 * The size range of text slot `slot` (plan §1.11: the page decides the pixels; the slot's [min, max] from the
 * catalogue or the template's own spec), with preflight's remedies (core.js fixesOf: a slot's size cap, from the fixes
 * channel or a legacy `text_fit` size, both upper bounds) and the R-29 floor of its role (`label` / `chip`: the label
 * floor; any other: the text floor). `safe` (the compact, zone-safe layout) holds the slot at its minimum.
 * @param {TemplateContext} ctx
 * @param {string} slot
 * @param {{min: number, max: number, role?: string}} spec
 * @returns {{min: number, max: number}}
 */
export function slotRange(ctx, slot, spec) {
  const floors = TYPE_FLOORS[ctx.format];
  const floor = spec.role === 'label' || spec.role === 'chip' ? floors.label_min : floors.text_min;
  const fixes = fixesOf(ctx.look);
  const own = Object.prototype.hasOwnProperty.call(fixes.slots, slot) ? fixes.slots[slot].size : null;
  const min = Math.max(floor, spec.min);
  let max = Math.max(min, spec.max);
  if (fixes.safe) max = min;
  if (own !== null) max = Math.max(min, Math.min(max, own));
  return { min, max };
}

/**
 * A template's slot spec from the catalogue's registry (templates.py SlotSpec: what textfit.py fits in Python), or
 * null when the registry has none for that slot, format and variant (`default` when the variant has none).
 * @param {string} templateId @param {string} slot @param {'landscape' | 'portrait'} format @param {string} [variant]
 * @returns {SlotSpec | null}
 */
export function slotSpec(templateId, slot, format, variant = 'default') {
  const slots = templateSlots(templateId);
  const perFormat = slots && Object.prototype.hasOwnProperty.call(slots, slot) ? slots[slot][format] : null;
  if (!perFormat) return null;
  return perFormat[variant] || perFormat.default || null;
}

/**
 * Fit text in its box the way the page measures it (board_generic's title rule, which textfit.py mirrors; plan
 * §1.11): the largest size, in `step` px steps from `max` down to `min` (the minimum itself last), at which the text
 * takes at most `lines` lines (first `prefer` lines, when given and it fits at >= min) with every word whole. A word
 * broken inside even at the minimum (wider than the box: a long Indic compound) does not count against a size; a TeX
 * word wider than the box holds the text at its minimum (it runs out sideways at every size). Leaves the element at
 * the chosen size. `overflow`: px beyond `lines` at the minimum, or the widest TeX word's excess (> 2 px rounding).
 * `words` / `tex`: the word spans of `el` (richWords); without them only the line count is checked.
 * @param {HTMLElement} el
 * @param {{max: number, min: number, lines: number, lh: number, prefer?: number, step?: number}} spec
 * @param {HTMLElement[]} [words]
 * @param {boolean[]} [texFlags]
 * @returns {{size: number, fits: boolean, overflow: number}}
 */
export function fitLines(el, spec, words = [], texFlags = []) {
  const step = spec.step && spec.step > 0 ? spec.step : 4;
  const isTex = (/** @type {number} */ i) => !!texFlags[i];
  const broken = (/** @type {number} */ s) => words.map((w, i) => (isTex(i)
    ? w.offsetWidth > el.clientWidth + 1
    : w.offsetHeight > 1.5 * s * spec.lh));
  el.style.fontSize = `${spec.min}px`;
  const unavoidable = broken(spec.min);
  const texTooWide = unavoidable.some((b, i) => b && isTex(i));
  const fit = (/** @type {number} */ lines) => {
    let s = texTooWide ? spec.min : spec.max;
    for (;;) {
      el.style.fontSize = `${s}px`;
      const whole = broken(s).every((b, i) => !b || unavoidable[i]);
      if (whole && Math.round(el.offsetHeight / (s * spec.lh)) <= lines) return { size: s, fits: true };
      if (s <= spec.min) return { size: s, fits: false };
      s = Math.max(spec.min, s - step);
    }
  };
  let best = spec.prefer ? fit(spec.prefer) : { size: spec.max, fits: false };
  if (!best.fits) best = fit(spec.lines);
  el.style.fontSize = `${best.size}px`;
  let overflow = best.fits ? 0 : Math.max(1, el.offsetHeight - spec.lines * best.size * spec.lh);
  const wide = Math.max(0, ...words.map((w, i) => (isTex(i) ? w.offsetWidth - el.clientWidth : 0)));
  if (wide > 2) overflow = Math.max(overflow, wide);
  return { size: best.size, fits: best.fits, overflow };
}

// ---------------------------------------------------------------------------------------------
// Motion (§4.8)
// ---------------------------------------------------------------------------------------------

/**
 * A template duration in the personality's scale (`ctx.motion.scale`: Crisp 0.85, Soft 1.15; 1 without one).
 * @param {TemplateContext} ctx @param {number} s
 */
export function seconds(ctx, s) {
  const k = ctx.motion && ctx.motion.scale > 0 ? ctx.motion.scale : 1;
  return s * k;
}

/**
 * Entrance progress at t of something entering at `start` for `dur` seconds (scaled by the personality),
 * eased with `ease.enter` (§4.8).
 * @param {TemplateContext} ctx @param {number} t @param {number} start @param {number} dur
 */
export function enterAt(ctx, t, start, dur) {
  return ease('ease.enter', progress(t, start, seconds(ctx, dur)));
}

/**
 * Paint a fade-and-rise entrance at progress `e` (0..1): opacity e and a rise of `rise` px, on whole pixels (§P: whole
 * pixel translates); without the rise under reduced motion (live only, §4.8). The translate is 2D, never
 * `translate3d`: measured in real Chromium (B2c, Windows, --disable-partial-raster), TeX (an inline MathJax SVG) moved
 * by `translate3d` inside an overflow-clipped mask rasterised up to 2 levels differently in reverse paint order; the
 * 2D translate gave 0 mismatches (formula_focus.landscape.light). board_generic keeps its B1 `translate3d` (frames of
 * existing timelines never change).
 * @param {TemplateContext} ctx @param {HTMLElement | SVGElement} el @param {number} e @param {number} rise
 */
export function paintRise(ctx, el, e, rise) {
  const p = ctx.paint;
  p.style(el, 'opacity', fx(e));
  const dy = ctx.reducedMotion || e >= 1 ? 0 : Math.round((1 - e) * rise);
  p.style(el, 'transform', dy ? `translate(0, ${dy}px)` : 'none');
}

/** Soft's text entrance rises this far (§4.8: "fade + 16 px rise"; themes.py TEXT_ENTRANCE_RISE_PX). */
export const TEXT_RISE = 16;

/**
 * The mask a `baseline_reveal` text entrance (Crisp, §4.8: "revealed from the baseline") slides its text up inside:
 * wrap the text element at build time and hand both to `paintTextEntrance`.
 * @param {HTMLElement} el
 */
export function revealMask(el) {
  return h('span', { class: 'dyn-reveal-mask' }, el);
}

/**
 * Paint the personality's text entrance (`ctx.motion.entrance`) at progress `e`: `baseline_reveal` slides the text up
 * from below its mask's bottom edge (`mask`: from `revealMask`; its height is read once laid out), any other (Soft's
 * `fade_rise`, and the W2 entrances until their release) fades in with a TEXT_RISE rise. Under reduced motion (live
 * only) every entrance is a fade in place.
 * @param {TemplateContext} ctx @param {HTMLElement} el @param {HTMLElement | null} mask @param {number} e
 */
export function paintTextEntrance(ctx, el, mask, e) {
  const p = ctx.paint;
  const kind = ctx.motion ? ctx.motion.entrance : 'fade_rise';
  if (kind === 'baseline_reveal' && mask && !ctx.reducedMotion) {
    const height = mask.offsetHeight || 0;
    const dy = e >= 1 ? 0 : Math.round((1 - e) * height);
    p.style(el, 'opacity', e > 0 ? '1' : '0');
    p.style(el, 'transform', dy ? `translate(0, ${dy}px)` : 'none'); // 2D: see paintRise
    return;
  }
  paintRise(ctx, el, e, TEXT_RISE);
}

/**
 * A content group: an element the stage dims to 70% while a look_here points elsewhere (the template returns it from
 * `dimGroups`; the template itself never writes its opacity, plan §1.4). Put the parts whose opacity the template
 * animates inside it.
 * @param {string} cls @param {...(Node | null)} children
 */
export function group(cls, ...children) {
  return h('div', { class: ['dyn-group', cls] }, ...children.filter(Boolean));
}

// ---------------------------------------------------------------------------------------------
// Item states (plan §1.5; state.js ITEM_STATES)
// ---------------------------------------------------------------------------------------------

/** The heaviest weight a state gives text (`current`: 700): lay text out at it, so a state change never re-wraps. */
export const STATE_WEIGHT = 700;
/** The state border's width (the catalogue's `outline.state_border_px`, 2 px). */
export const STATE_BORDER = Number(LAYOUT_TOKENS.outline.state_border_px);
/** A `struck` line draws in 300 ms (§4.3 myth_fact "a strike-through draws (300 ms)"). */
export const STRIKE_DRAW = 0.3;

/**
 * What `itemStateParts` makes: the host, the `done` check, the `struck` layer and its lines (`layoutStrike`).
 * @typedef {object} ItemStateParts
 * @property {HTMLElement} host
 * @property {SVGElement} check
 * @property {SVGElement} strike
 * @property {SVGElement[]} lines
 * @property {number} length   the struck lines' total length, px
 */

/**
 * The parts an item state draws, made inside `host` (which the stylesheet must position: `position: relative` or
 * absolute): a `done` check icon and a `struck` line layer. Call once at build time.
 * @param {HTMLElement} host
 * @returns {ItemStateParts}
 */
export function itemStateParts(host) {
  host.classList.add('dyn-stateful');
  const check = icon('check', { class: 'dyn-state-mark dyn-state-check' });
  const strike = svg('svg', { class: 'dyn-state-mark dyn-state-strike', 'aria-hidden': 'true' });
  host.append(check, strike);
  return { host, check, strike, lines: [], length: 0 };
}

/**
 * Lay out the `struck` line(s) of `parts` through the line boxes of `textEl` (layout time; one line per text line,
 * at its middle, whole px, in host coordinates). Without layout (unit tests) there are no lines.
 * @param {TemplateContext} ctx @param {ItemStateParts} parts @param {HTMLElement} textEl
 */
export function layoutStrike(ctx, parts, textEl) {
  for (const l of parts.lines) l.remove();
  parts.lines = [];
  parts.length = 0;
  const host = ctx.rectOf(parts.host);
  const whole = textEl.getBoundingClientRect();
  const r = ctx.rectOf(textEl);
  if (!host || !r || !(whole.width > 0)) return;
  const k = r.w / whole.width; // page px -> stage px
  Object.assign(parts.strike.style, { left: '0px', top: '0px', width: `${Math.round(host.w)}px`, height: `${Math.round(host.h)}px` });
  for (const f of textEl.getClientRects()) {
    if (!(f.width > 0)) continue;
    const x0 = Math.round(r.x + (f.left - whole.left) * k - host.x);
    const x1 = Math.round(r.x + (f.right - whole.left) * k - host.x);
    const y = Math.round(r.y + ((f.top + f.bottom) / 2 - whole.top) * k - host.y);
    const line = svg('line', { x1: String(x0), y1: String(y), x2: String(x1), y2: String(y), 'stroke-width': '4', 'stroke-linecap': 'round' });
    parts.strike.appendChild(line);
    parts.lines.push(line);
    parts.length += x1 - x0;
  }
}

/**
 * Paint item state `state` (state.js ITEM_STATES, or null: none) on `el` at strength `k` (0..1, the template's eased
 * change; a `struck` line is drawn over STRIKE_DRAW as k goes 0 -> 1): the text weight and ink, a 2 px inset border
 * (`--dyn-state-border`; never `outline`), the `done` check and the `struck` line. Writes `font-weight`, `color` and
 * `--dyn-state-border` on `el` (so the template writes neither on it).
 *
 * Every property gets a value on every call, never `''` (an item without a state is painted in `base`: its weight and
 * ink, and a transparent border). Clearing a property and writing it again later moves it to the end of the style
 * attribute, so a past -> current element would serialise differently from one painted current fresh, and the frame
 * would depend on paint order (B2c, list_build's probe; plan §P condition 3).
 * @param {TemplateContext} ctx
 * @param {HTMLElement} el
 * @param {ItemStateParts | null} parts
 * @param {string | null} state
 * @param {number} k
 * @param {{weight: number, ink: string}} [base]   the item's look without a state (default: weight 400, `ink`)
 */
export function paintItemState(ctx, el, parts, state, k, base) {
  const p = ctx.paint;
  const s = state ? itemStateStyle(state, ctx.roles) : null;
  const b = base || { weight: 400, ink: ctx.roles.color('ink') };
  const on = Math.min(1, Math.max(0, k));
  p.style(el, 'font-weight', String(s && s.weight !== null && on > 0 ? s.weight : b.weight));
  p.style(el, 'color', s && on > 0 ? s.ink : b.ink);
  const alpha = s && s.border && on > 0 ? Math.round(on * 255).toString(16).padStart(2, '0') : '00';
  p.style(el, '--dyn-state-border', `${(s && s.border) || b.ink}${alpha}`);
  if (!parts) return;
  p.style(parts.check, 'color', s && s.check ? s.check : b.ink);
  p.style(parts.check, 'opacity', s && s.check ? fx(on) : '0');
  p.style(parts.check, 'visibility', s && s.check && on > 0 ? 'visible' : 'hidden');
  const strike = s && s.strike ? s.strike : null;
  p.style(parts.strike, 'visibility', strike && on > 0 ? 'visible' : 'hidden');
  for (const line of parts.lines) {
    const len = Math.max(1, Number(line.getAttribute('x2')) - Number(line.getAttribute('x1')));
    p.attr(line, 'stroke', strike || 'none');
    p.attr(line, 'stroke-dasharray', `${len} ${len}`);
    p.attr(line, 'stroke-dashoffset', fx(len * (1 - on), 2));
  }
}

/**
 * What text of `ink` over a translucent `scrim` sits on (a template's scrim fix; board_generic's title block): the
 * scrim composited over each colour the background shows, the one with the least contrast against `ink` (R-53's
 * worst case). Opaque hex, the colour preflight's contrast check then measures, so a scrim that helps reads as fixed.
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

// ---------------------------------------------------------------------------------------------
// Icons (plan §1.2; B2b: the stored icon geometry, sanitised again before drawing)
// ---------------------------------------------------------------------------------------------

/**
 * Path data only (ICON_PATH_RE of aadhi.schemas.timeline, the `LookIcon.paths` pattern of its JSON schema in
 * web/schemas; web/tests/dynamic/templates/shared.test.js pins the two equal): a moveto, then numbers, commands and
 * separators. No attribute, URL, entity or markup can be written in it.
 */
export const ICON_PATH_RE = /^\s*[Mm][MmZzLlHhVvCcSsQqTtAa0-9eE.,+\-\s]*$/;
/** The schema's caps: characters per path, paths per icon, stroke width, name / set / version lengths. */
export const ICON_PATH_MAX_CHARS = 4096;
export const ICON_PATHS_MAX = 32;
export const ICON_STROKE_MAX = 8;

/**
 * A stored icon checked again (the schema checked it when the timeline was built; the stage does not trust that a
 * served timeline went through it): a viewBox of four finite numbers with a positive width and height, a stroke width
 * in [0, 8] (default 2), `filled` a boolean, 1-32 paths each matching ICON_PATH_RE and at most 4096 characters.
 * Anything else gives null (the icon is not drawn). Pure.
 * @param {unknown} raw
 * @returns {{viewBox: [number, number, number, number], strokeWidth: number, filled: boolean, paths: string[]} | null}
 */
export function sanitiseIcon(raw) {
  if (!raw || typeof raw !== 'object') return null;
  const r = /** @type {any} */ (raw);
  const vb = r.viewBox;
  if (!Array.isArray(vb) || vb.length !== 4 || !vb.every((n) => typeof n === 'number' && Number.isFinite(n))) return null;
  if (!(vb[2] > 0 && vb[3] > 0)) return null;
  const sw = r.stroke_width === undefined ? 2 : r.stroke_width;
  if (typeof sw !== 'number' || !Number.isFinite(sw) || sw < 0 || sw > ICON_STROKE_MAX) return null;
  if (r.filled !== undefined && typeof r.filled !== 'boolean') return null;
  const paths = r.paths;
  if (!Array.isArray(paths) || paths.length < 1 || paths.length > ICON_PATHS_MAX) return null;
  for (const d of paths) {
    if (typeof d !== 'string' || !d.length || d.length > ICON_PATH_MAX_CHARS || !ICON_PATH_RE.test(d)) return null;
  }
  return { viewBox: [vb[0], vb[1], vb[2], vb[3]], strokeWidth: sw, filled: r.filled === true, paths: paths.slice() };
}

/**
 * The icon `look.icons[itemId]` as an inline SVG (`.dyn-sicon`; `block`: `.dyn-sicon--block`), or null when the look
 * has none or it fails `sanitiseIcon`. Drawn in `currentColor` (the caller colours it from a content role; never a
 * structure role on content, §4.2), `size` px square (the catalogue's icon sizes: `card_px` 96, `feature_px` 320, or
 * `inline_em` in text), its stored stroke width, round caps and joins, no fill unless it is a filled icon. Every path
 * is a new element whose `d` is set with setAttribute (never markup).
 * @param {TemplateContext} ctx
 * @param {string} itemId
 * @param {{size: number | string, block?: boolean}} opts   size: px, or a CSS length such as '1.05em'
 * @returns {SVGElement | null}
 */
export function storedIcon(ctx, itemId, opts) {
  const icons = ctx.look && /** @type {any} */ (ctx.look).icons;
  const raw = icons && typeof icons === 'object' && Object.prototype.hasOwnProperty.call(icons, itemId) ? icons[itemId] : null;
  const ok = sanitiseIcon(raw);
  if (!ok) return null;
  const size = typeof opts.size === 'number' ? `${opts.size}px` : String(opts.size);
  const el = svg('svg', {
    class: ['dyn-sicon', opts.block ? 'dyn-sicon--block' : ''], 'aria-hidden': 'true', focusable: 'false',
    fill: ok.filled ? 'currentColor' : 'none', stroke: ok.filled ? 'none' : 'currentColor',
    'stroke-width': String(ok.strokeWidth), 'stroke-linecap': 'round', 'stroke-linejoin': 'round',
  });
  el.setAttribute('viewBox', ok.viewBox.map((n) => String(n)).join(' '));
  el.style.width = size;
  el.style.height = size;
  for (const d of ok.paths) {
    const path = svg('path', null);
    path.setAttribute('d', d);
    el.appendChild(path);
  }
  return el;
}

// ---------------------------------------------------------------------------------------------
// Media (§7.7; §P: pictures only ever downscale)
// ---------------------------------------------------------------------------------------------

/**
 * A medium's aspect ratio (width / height) from its stored size, 16:9 without one.
 * @param {MediaRef | null | undefined} ref
 */
export function mediaAspect(ref) {
  const w = ref ? Number(ref.width) : NaN;
  const hh = ref ? Number(ref.height) : NaN;
  return w > 0 && hh > 0 ? w / hh : 16 / 9;
}

/**
 * The largest rectangle of `aspect` inside `box`, centred (or aligned: `alignX` / `alignY` 0 = start, 0.5 = centre,
 * 1 = end), on whole pixels.
 * @param {number} aspect @param {Box} box @param {{alignX?: number, alignY?: number}} [align]
 * @returns {{x: number, y: number, w: number, h: number}}
 */
export function containRect(aspect, box, align = {}) {
  const bw = box.x1 - box.x0;
  const bh = box.y1 - box.y0;
  const w = Math.min(bw, bh * aspect);
  const hh = w / aspect;
  const ax = align.alignX === undefined ? 0.5 : align.alignX;
  const ay = align.alignY === undefined ? 0.5 : align.alignY;
  const x = Math.round(box.x0 + (bw - w) * ax);
  const y = Math.round(box.y0 + (bh - hh) * ay);
  return { x, y, w: Math.round(w), h: Math.round(hh) };
}

/**
 * The box a push_ok template's medium must be extracted at (its static `mediaBox`): its largest shown box grown by the
 * most a look_here push can scale it (cues.js PUSH_SCALE_MAX), rounded up, so the picture only ever downscales while
 * pushed (§P condition 1).
 * @param {{w: number, h: number}} box
 * @returns {{w: number, h: number}}
 */
export function pushHeadroom(box) {
  return { w: Math.ceil(box.w * (1 + PUSH_SCALE_MAX)), h: Math.ceil(box.h * (1 + PUSH_SCALE_MAX)) };
}

/**
 * Paint a media view at scene time t (pictures, Ken Burns, clips: media.js MediaView), with the live playback state.
 * Paint it every frame, also while hidden, so an element never keeps a frame from an earlier seek.
 * @param {TemplateContext} ctx @param {import('../media.js').MediaView} view @param {number} t
 */
export function paintMedia(ctx, view, t) {
  view.update(ctx.paint, t, ctx.frameInfo || {});
}

// ---------------------------------------------------------------------------------------------
// Side panels (§4.3 "the scene's panel", §7.7; the B2-demo side-panel slice: one helper for every template that
// shows a scene's side panel beside its own content, so a panel is drawn, timed, pointed at and reported the same
// way everywhere)
//
// It draws exactly the three panel forms `board_generic` (the fallback, which must be able to draw every scene
// whole) and `media_split` already draw, and nothing else:
//
// 1. a **medium** (`PANEL_MEDIA_KINDS`: the `figure` / `image` / `manim` / `gif` panels the resolver gives a
//    `ResolvedSidePanel.media`, `compose.timeline._panel_media`), contained in the panel's box at its own aspect;
// 2. a **terminal card** (`terminal`): the command and the output lines the classic panel's schedule reveals
//    (`player/schedule.js planScene`, `SceneState.terminalLines`), mono, on the card surface;
// 3. the panel's **title** on the card surface, for a panel whose own content this mode cannot show - a GIF in the
//    MP4, an unsafe URL (`media.shownIn`; board_generic's `.dyn-media-missing`, media_split's `MISSING_LABEL_PX`,
//    list_build's `.dyn-lb-missing`) - and for the `PANEL_TITLED_KINDS`, whose stored spec the Dynamic engine draws
//    nowhere yet (`chart` is Chart.js's `chart_build`, `graph` is `function_plot`, `model_3d` is `model_spin`: none
//    of them implemented, and board_generic draws none of them either, so there is no chart or plot path to reuse
//    and this slice invents none). Such a panel is only claimed when it HAS a title (`panelOf` gives null
//    otherwise): the authored caption is content worth drawing, an empty card is not.
//
// Every other kind (`skill_tree`, `quiz`) is nobody's here: `panelOf` gives null, `drawsPanel` is false and the
// template hands the whole scene to board_generic (B2c review CG-2 - a template's Python `fit` must refuse exactly
// what its JS refuses, or the director picks it and the page silently draws board_generic).
// ---------------------------------------------------------------------------------------------

/** Panel kinds whose content is a resolved medium (board_generic's `mediaOf` slot `panel`). */
export const PANEL_MEDIA_KINDS = Object.freeze(['figure', 'image', 'manim', 'gif']);
/** Panel kinds the Dynamic engine draws nowhere yet: shown as the panel's own title on the card surface. */
export const PANEL_TITLED_KINDS = Object.freeze(['chart', 'graph', 'model_3d']);
/** Every panel kind this helper draws (templates.py `SHARED_PANELS` mirrors it, pinned by the templates' tests). */
export const PANEL_KINDS = Object.freeze([...PANEL_MEDIA_KINDS, 'terminal', ...PANEL_TITLED_KINDS]);
/** A panel shown from the scene's start enters this long after it (board_generic's media frame, 200 ms). */
export const PANEL_DELAY = 0.2;
/** A panel's entrance (§4.3 media_split's 500 ms, as a fade and rise; a clip only ever fades, see `paintPanel`). */
export const PANEL_ENTER = 0.5;
/** How far a panel rises in (board_generic's and media_split's media frame, §4.8). */
export const PANEL_RISE = 24;
/** The panel's title on the card surface (board_generic's `.dyn-media-missing`, media_split's MISSING_LABEL_PX). */
export const PANEL_LABEL_PX = Object.freeze({ landscape: 52, portrait: 48 });
/** A terminal card's mono text and padding per format (media_split's `terminal` slot: 9:16 at weight 700, R-29,
 * since JetBrains Mono has 400 and 700 only). */
export const PANEL_TERM = Object.freeze({
  landscape: Object.freeze({ max: 44, min: 36, lh: 1.35, weight: 400, pad: 32 }),
  portrait: Object.freeze({ max: 44, min: 40, lh: 1.35, weight: 700, pad: 28 }),
});
/** The slot a terminal card's text is reported in (media_split's name, so preflight's remedies read the same). */
export const PANEL_TERM_SLOT = 'terminal';

/**
 * What `panelOf` describes: the panel as this helper draws it.
 * @typedef {object} PanelPlan
 * @property {string} kind            the stored `SidePanel.kind`
 * @property {'media' | 'terminal' | 'label'} draw   which of the three forms above
 * @property {MediaRef | null} ref    its medium, kept even when this mode cannot show it (the panel is laid out at
 *                                    its aspect either way, so the live player and the MP4 share one layout)
 * @property {boolean} shown          `media.shownIn`: this mode can show `ref`
 * @property {string} title           the panel's title, else the scene's (never empty)
 * @property {number} start           scene-relative second the panel appears (`ResolvedSidePanel.show_at`)
 * @property {{command: string, lines: string[]} | null} terminal
 */

/**
 * The scene's side panel when the layout shows it (null without one, or with `layout.show_side_panel` false:
 * board_generic's, media_split's and list_build's one question).
 * @param {TimedScene} scene
 * @returns {import('../../shared/types.js').ResolvedSidePanel | null}
 */
export function shownPanel(scene) {
  const sp = scene.side_panel;
  return sp && sp.panel && scene.layout?.show_side_panel !== false ? sp : null;
}

/**
 * The scene's panel as this helper draws it (see the section header), or null when it has none the helper draws -
 * which is also what makes the scene board_generic's (`drawsPanel`). Pure.
 * @param {TimedScene} scene
 * @param {'live' | 'render'} [mode]
 * @returns {PanelPlan | null}
 */
export function panelOf(scene, mode = 'live') {
  const sp = shownPanel(scene);
  if (!sp) return null;
  const kind = String(sp.panel.kind || '');
  const start = Math.max(0, Number(sp.show_at) || 0);
  const own = typeof sp.panel.title === 'string' ? sp.panel.title.trim() : '';
  const title = own || String(scene.title || '');
  if (kind === 'terminal') {
    if (!sp.panel.terminal) return null;
    const plan = planScene(scene).terminal;
    return {
      kind, draw: 'terminal', ref: null, shown: true, title, start,
      terminal: { command: String(sp.panel.terminal.command || ''), lines: plan ? plan.lines.slice() : [] },
    };
  }
  if (PANEL_MEDIA_KINDS.includes(kind)) {
    const ref = sp.media || null;
    const shown = !!ref && shownIn(ref, mode === 'render' ? 'render' : 'live');
    return { kind, draw: shown ? 'media' : 'label', ref, shown, title, start, terminal: null };
  }
  // a stored spec the Dynamic engine draws nowhere yet: its authored title, and only when it has one
  if (PANEL_TITLED_KINDS.includes(kind) && own) {
    return { kind, draw: 'label', ref: null, shown: false, title: own, start, terminal: null };
  }
  return null;
}

/**
 * Whether a template that uses this helper can draw the scene's panel: it has none shown, or one `panelOf` draws.
 * A template's `drawsScene` asks this, and its Python `fit` must refuse exactly the same scenes (CG-2). Pure, and
 * independent of the mode (a GIF is claimed in both: the MP4 shows its title card).
 * @param {TimedScene} scene
 */
export function drawsPanel(scene) {
  return !shownPanel(scene) || panelOf(scene, 'render') !== null;
}

/**
 * What `buildPanel` makes (the template keeps it in its view and hands it back to the other helpers).
 * @typedef {object} PanelParts
 * @property {PanelPlan} panel
 * @property {HTMLElement} el        the dim group (the template returns it from `dimGroups`; the stage owns its
 *                                  opacity, so everything animated sits inside it)
 * @property {HTMLElement} frame     the panel's own box, placed by `layoutPanel`
 * @property {import('../media.js').MediaView | null} view
 * @property {HTMLElement | null} label
 * @property {{pre: HTMLElement, cmd: HTMLElement, lines: HTMLElement[], size: number, min: number,
 *   overflow: number} | null} term
 * @property {number} start
 * @property {Box | null} box        the frame's box as laid out
 * @property {number} alpha          its entrance now (read by `panelTexts` / `panelFocusRect` after `update`)
 */

/**
 * The panel's DOM, once per scene (build time; no measuring): the frame, inside a dim group of its own over the
 * whole stage, holding its medium, its terminal card or its title. The caller appends `parts.el` to its root.
 * @param {TemplateContext} ctx
 * @param {PanelPlan} panel
 * @returns {PanelParts}
 */
export function buildPanel(ctx, panel) {
  const surface = ctx.roles.color('surface');
  // every style key is written literally (the source scan refuses a style name it cannot read, plan §P)
  const frame = h('div', {
    class: 'dyn-panel-frame',
    style: {
      position: 'absolute', overflow: 'hidden', borderRadius: `${RADIUS.media}px`,
      boxShadow: String(LAYOUT_TOKENS.shadow['3']),
    },
  });
  /** @type {import('../media.js').MediaView | null} */
  let view = null;
  /** @type {HTMLElement | null} */
  let label = null;
  /** @type {PanelParts['term']} */
  let term = null;
  if (panel.draw === 'media' && panel.ref) {
    // sized by the template's own mediaBox for slot 'panel' (the box mediaPlan extracts a clip at)
    view = ctx.media(panel.ref, { slot: 'panel', start: panel.start, alt: panel.title });
    frame.appendChild(view.el);
  }
  if (panel.draw === 'terminal' && panel.terminal) {
    const T = PANEL_TERM[ctx.format];
    const lines = panel.terminal.lines.map((line) => h('div', { class: 'dyn-panel-term-line', text: line || ' ' }));
    const cmd = h('div', {
      class: 'dyn-panel-term-cmd', style: { fontWeight: '700' }, text: `$ ${panel.terminal.command}`,
    });
    const pre = h('pre', {
      class: 'dyn-panel-term',
      style: {
        margin: '0px', fontFamily: 'var(--dyn-font-mono)', fontWeight: String(T.weight), lineHeight: String(T.lh),
        whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', color: ctx.roles.color('ink'),
      },
    }, cmd, lines);
    frame.style.background = surface;
    frame.style.padding = `${T.pad}px`;
    frame.appendChild(pre);
    term = { pre, cmd, lines, size: T.max, min: T.min, overflow: 0 };
  } else if (!view || view.missing) {
    // nothing this mode can show (a GIF in the MP4, an unsafe URL), or a kind drawn nowhere yet: the panel's title
    // on the card surface, never an empty box (board_generic's, media_split's and list_build's convention)
    frame.style.background = surface;
    label = h('div', {
      class: 'dyn-panel-missing', dir: 'auto', text: panel.title || '·',
      style: {
        position: 'absolute', left: '0px', top: '0px', right: '0px', bottom: '0px', display: 'flex',
        alignItems: 'center', justifyContent: 'center', padding: '0 1em', textAlign: 'center',
        fontSize: `${PANEL_LABEL_PX[ctx.format]}px`, fontWeight: '600', lineHeight: '1.2',
        color: ctx.roles.color('ink2'), overflowWrap: 'anywhere',
      },
    });
    frame.appendChild(label);
  }
  const el = group('dyn-panel', frame);
  Object.assign(el.style, { position: 'absolute', left: '0px', top: '0px', right: '0px', bottom: '0px' });
  return { panel, el, frame, view, label, term, start: panel.start, box: null, alpha: 0 };
}

/**
 * Place the panel in `box` (layout time; idempotent, so it may run again when a late face arrives): a medium (or a
 * title card standing in for one) contained at the medium's aspect and centred; a terminal card the box's width and
 * its fitted text's height, centred. Returns the frame's box and keeps it on `parts.box`.
 * @param {TemplateContext} ctx
 * @param {PanelParts} parts
 * @param {Box} box
 * @returns {Box}
 */
export function layoutPanel(ctx, parts, box) {
  /** @type {{x: number, y: number, w: number, h: number}} */
  let r;
  if (parts.term) {
    const T = PANEL_TERM[ctx.format];
    const range = slotRange(ctx, PANEL_TERM_SLOT, { min: T.min, max: T.max, role: 'body' });
    const w = Math.round(box.x1 - box.x0);
    const avail = (box.y1 - box.y0) - 2 * T.pad;
    parts.frame.style.width = `${w}px`;
    parts.frame.style.height = 'auto';
    // the largest mono size whose card fits the box (media_split's 2 px walk; its floor is R-29's label floor)
    let s = Math.max(range.min, range.max);
    for (;;) {
      parts.term.pre.style.fontSize = `${s}px`;
      if (parts.term.pre.offsetHeight <= avail || s <= range.min) break;
      s = Math.max(range.min, s - 2);
    }
    parts.term.size = s;
    parts.term.min = range.min;
    const contentH = parts.term.pre.offsetHeight;
    const wide = (parts.term.pre.scrollWidth || 0) - (parts.term.pre.clientWidth || 0);
    // what still does not fit is reported, never clipped silently (plan §1.4: preflight blocks with "Shorten text")
    parts.term.overflow = Math.max(0, contentH - avail, wide > 2 ? wide : 0);
    const hh = Math.min(box.y1 - box.y0, contentH + 2 * T.pad);
    r = { x: Math.round(box.x0), y: Math.round(box.y0 + ((box.y1 - box.y0) - hh) / 2), w, h: Math.round(hh) };
  } else {
    r = containRect(mediaAspect(parts.panel.ref), box);
  }
  Object.assign(parts.frame.style, { left: `${r.x}px`, top: `${r.y}px`, width: `${r.w}px`, height: `${r.h}px` });
  parts.box = { x0: r.x, y0: r.y, x1: r.x + r.w, y1: r.y + r.h };
  return parts.box;
}

/**
 * Paint the panel at scene time t: its entrance (a fade and rise from `start`, or PANEL_DELAY after the scene when
 * it is there from the start), its medium every frame (also while hidden, so the element never keeps a frame from an
 * earlier seek) and a terminal's output lines as the schedule reveals them (`SceneState.terminalLines`, a pure
 * function of (timeline, t)). A clip only ever fades: it is never scaled nor moved (B2c, test_template_clips.py).
 * Every property is written on every frame with a value (never ''), so a frame never depends on paint order.
 * @param {TemplateContext} ctx
 * @param {PanelParts} parts
 * @param {number} t
 * @param {import('../../shared/types.js').SceneState} [state]
 */
export function paintPanel(ctx, parts, t, state) {
  const p = ctx.paint;
  const e = enterAt(ctx, t, parts.start > 0 ? parts.start : PANEL_DELAY, PANEL_ENTER);
  parts.alpha = e;
  const clip = !!parts.panel.ref && parts.panel.ref.kind === 'video';
  paintRise(ctx, parts.frame, e, clip ? 0 : PANEL_RISE);
  p.style(parts.frame, 'visibility', e > 0 ? 'visible' : 'hidden');
  if (parts.view) paintMedia(ctx, parts.view, t);
  if (parts.term) {
    const lines = state && state.terminalLines ? state.terminalLines.length : parts.term.lines.length;
    parts.term.lines.forEach((line, i) => p.style(line, 'visibility', i < lines ? 'visible' : 'hidden'));
  }
}

/**
 * Where a look_here on the panel points (plan §1.5, §1.7; the `panel` and `panel:row:<n>` target forms of
 * templates.py `_PANEL_PARTS`): `panel` the panel's frame (a shape, so cues.js draws the outline round it and never
 * across it), `panel:row:<n>` the n-th output line of a terminal card (a bar). Null for any other target, for a row
 * this panel has not got, or while the panel is not on screen (read after `update`). Whole stage px.
 * @param {TemplateContext} ctx
 * @param {PanelParts} parts
 * @param {string} target
 * @returns {import('./types.js').FocusRect | null}
 */
export function panelFocusRect(ctx, parts, target) {
  const parsed = parseTarget(String(target || ''));
  if (!parsed) return null;
  /** @type {Element | null} */
  let el = null;
  /** @type {'rect' | 'bar'} */
  let shape = 'rect';
  let radius = RADIUS.media;
  if (parsed.form === 'panel') {
    el = parts.alpha > 0.01 ? parts.frame : null;
  } else if (parsed.form === 'panel:row') {
    const i = typeof parsed.index === 'number' ? parsed.index : -1;
    el = parts.term && parts.alpha > 0.01 ? parts.term.lines[i] || null : null;
    shape = 'bar';
    radius = 0;
  } else {
    return null;
  }
  const r = el ? ctx.rectOf(el) : null;
  if (!r || !(r.w > 0 && r.h > 0)) return null;
  const x0 = Math.round(r.x);
  const y0 = Math.round(r.y);
  return {
    rect: { x: x0, y: y0, w: Math.round(r.x + r.w) - x0, h: Math.round(r.y + r.h) - y0 },
    shape, radius, el: parts.el, kind: 'shape',
  };
}

/**
 * The panel's text on screen (preflight, §7.12): a terminal card's text (slot `terminal`, critical, on the card
 * surface, with the overflow `layoutPanel` measured) and a title card's label (never critical: it stands in for a
 * picture, R-07's reading in board_generic and media_split). Nothing for a panel showing its medium.
 * @param {TemplateContext} ctx
 * @param {PanelParts} parts
 * @returns {import('./types.js').TextEntry[]}
 */
export function panelTexts(ctx, parts) {
  /** @type {import('./types.js').TextEntry[]} */
  const out = [];
  const surface = ctx.roles.color('surface');
  if (parts.term) {
    const T = PANEL_TERM[ctx.format];
    const command = parts.panel.terminal ? parts.panel.terminal.command : '';
    const lines = parts.panel.terminal ? parts.panel.terminal.lines : [];
    out.push({
      el: parts.term.pre, role: 'body', slot: PANEL_TERM_SLOT, critical: true, ink: ctx.roles.color('ink'),
      bg: surface, alpha: parts.alpha, overflow: parts.term.overflow, size: parts.term.size, min: parts.term.min,
      weight: T.weight, text: [`$ ${command}`, ...lines].join('\n'),
    });
  }
  if (parts.label) {
    out.push({
      el: parts.label, role: 'label', critical: false, ink: ctx.roles.color('ink2'), bg: surface,
      alpha: parts.alpha, size: PANEL_LABEL_PX[ctx.format], weight: 600, text: parts.label.textContent || '',
    });
  }
  return out;
}

/**
 * Static: the box the panel's medium is sized for (a template's `mediaBox` for slot `panel`; `mediaPlan` extracts a
 * clip at it and `ctx.media` shows it at it, so every frame the page asks for is one the worker has). `box`: the
 * panel's own box in the template's layout, the largest it is ever shown in. Null for any other slot and for a panel
 * with no medium (a terminal or a title card asks for none).
 * @param {TimedScene} scene
 * @param {Box} box
 * @param {string | undefined} slot
 * @returns {{w: number, h: number} | null}
 */
export function panelMediaBox(scene, box, slot) {
  if (slot !== 'panel') return null;
  const panel = panelOf(scene, 'render');
  if (!panel || !panel.ref) return null;
  return { w: Math.round(box.x1 - box.x0), h: Math.round(box.y1 - box.y0) };
}

// ---------------------------------------------------------------------------------------------
// Formula tints (§4.7 entity colours; plan §P (g): MathJax keeps `\color{#hex}` under ui/safe as `fill` attributes)
// ---------------------------------------------------------------------------------------------

/** Placeholder colours: `#0A0B` + a two-digit index (near-black, never a palette colour). */
export const TINT_BASE = '#0A0B';
/** At most this many tinted symbols per formula (placeholders 00..FF). */
export const TINT_MAX = 256;
/** Commands whose argument is upright text (units, names), where a symbol is never tinted. */
const TEXT_COMMANDS = new Set(['\\text', '\\textrm', '\\textbf', '\\textit', '\\mathrm', '\\operatorname', '\\mbox', '\\textnormal']);

/**
 * TeX tokens with their source offsets: control words (`\alpha`, with the spaces after them), control symbols (`\,`),
 * braces, `^` / `_`, other single characters; whitespace is skipped (insignificant in maths).
 * @param {string} s
 * @returns {{v: string, a: number, b: number}[]}
 */
export function texTokens(s) {
  /** @type {{v: string, a: number, b: number}[]} */
  const out = [];
  let i = 0;
  while (i < s.length) {
    const c = s[i];
    if (/\s/.test(c)) {
      i++;
      continue;
    }
    if (c === '\\') {
      let j = i + 1;
      if (j < s.length && /[A-Za-z]/.test(s[j])) {
        while (j < s.length && /[A-Za-z]/.test(s[j])) j++;
      } else {
        j = Math.min(s.length, j + 1);
      }
      out.push({ v: s.slice(i, j), a: i, b: j });
      i = j;
      continue;
    }
    out.push({ v: c, a: i, b: i + 1 });
    i++;
  }
  return out;
}

/**
 * The formula `latex` with every occurrence of each symbol (`symbols`, `FormulaVariable.symbol_latex`, as written)
 * wrapped in a `\color` placeholder of its own, `{\color{#0A0Bnn}...}`, and the placeholder of each symbol. A symbol
 * matches a run of the formula's tokens (whitespace ignored) that is a whole atom: not inside a script (`x_{v}` keeps
 * its `v`), not inside upright text (`\mathrm{m}` is a unit, not the symbol `m`), not followed by a subscript the
 * symbol lacks (`v` never matches the `v` of `v_0`; a power is fine: `v^2`'s base is `v`), and not part of a control
 * word. Longer symbols are matched first and a token is wrapped at most once. Deterministic and pure, so the static
 * `tex()` hook and `build` produce the same string (render.js converts it before `ready`).
 * @param {string} latex
 * @param {readonly string[]} symbols
 * @returns {{tex: string, tints: {symbol: string, fill: string}[]}}
 */
export function tintTex(latex, symbols) {
  const src = String(latex || '');
  const toks = texTokens(src);
  // per token: inside a script group or an upright-text argument (no match may start or run there)
  const blocked = new Array(toks.length).fill(false);
  /** @type {boolean[]} */
  const stack = [];
  for (let i = 0; i < toks.length; i++) {
    const v = toks[i].v;
    if (v === '{') {
      const prev = i > 0 ? toks[i - 1].v : '';
      stack.push(prev === '_' || prev === '^' || TEXT_COMMANDS.has(prev) || (stack.length > 0 && stack[stack.length - 1]));
    } else if (v === '}') {
      stack.pop();
    }
    if (stack.length && stack[stack.length - 1]) blocked[i] = true;
    if (i > 0 && (toks[i - 1].v === '_' || toks[i - 1].v === '^') && v !== '{') blocked[i] = true;
  }
  const used = new Array(toks.length).fill(false);
  /** @type {{a: number, b: number, fill: string}[]} */
  const wraps = [];
  /** @type {{symbol: string, fill: string}[]} */
  const tints = [];
  const unique = [...new Set(symbols.map((s) => String(s || '')).filter((s) => s.trim()))].slice(0, TINT_MAX);
  const order = unique.map((s, k) => ({ s, k, t: texTokens(s).map((x) => x.v) }))
    .sort((x, y) => y.t.length - x.t.length || x.k - y.k);
  for (const { s, k, t } of order) {
    const fill = `${TINT_BASE}${k.toString(16).toUpperCase().padStart(2, '0')}`;
    tints.push({ symbol: s, fill });
    if (!t.length || !balanced(t)) continue;
    for (let i = 0; i + t.length <= toks.length; i++) {
      // the atom must start outside a script or upright text (its own subscript tokens are part of it)
      let ok = !blocked[i];
      for (let j = 0; j < t.length && ok; j++) ok = toks[i + j].v === t[j] && !used[i + j];
      if (!ok) continue;
      const after = toks[i + t.length];
      if (after && after.v === '_') continue; // a subscripted atom is another symbol
      for (let j = 0; j < t.length; j++) used[i + j] = true;
      wraps.push({ a: toks[i].a, b: toks[i + t.length - 1].b, fill });
      i += t.length - 1;
    }
  }
  tints.sort((x, y) => unique.indexOf(x.symbol) - unique.indexOf(y.symbol));
  wraps.sort((x, y) => x.a - y.a);
  let out = '';
  let at = 0;
  for (const w of wraps) {
    out += `${src.slice(at, w.a)}{\\color{${w.fill}}${src.slice(w.a, w.b)}}`;
    at = w.b;
  }
  out += src.slice(at);
  return { tex: out, tints };
}

/** @param {string[]} t */
function balanced(t) {
  let depth = 0;
  for (const v of t) {
    if (v === '{') depth++;
    else if (v === '}' && --depth < 0) return false;
  }
  return depth === 0;
}

/**
 * The SVG elements MathJax drew for each placeholder of `tints` inside `host` (after the TeX is in: call it from
 * `layout`): every element whose `fill` is the placeholder (any case). The first call tags them
 * (`data-dyn-tint="<fill>"`), so a later call (layout runs again when a late face arrives) finds them after
 * `paintTint` has recoloured them. Symbols with no element map to [].
 * @param {Element} host
 * @param {{symbol: string, fill: string}[]} tints
 * @returns {Map<string, Element[]>}
 */
export function tintTargets(host, tints) {
  /** @type {Map<string, Element[]>} */
  const out = new Map(tints.map((t) => [t.symbol, []]));
  const bySymbol = new Map(tints.map((t) => [t.fill.toUpperCase(), t.symbol]));
  for (const el of host.querySelectorAll('[data-dyn-tint], [fill]')) {
    const tagged = el.getAttribute('data-dyn-tint');
    const fill = String(tagged || el.getAttribute('fill')).toUpperCase();
    const symbol = bySymbol.get(fill);
    if (symbol === undefined) continue;
    if (!tagged) el.setAttribute('data-dyn-tint', fill);
    /** @type {Element[]} */ (out.get(symbol)).push(el);
  }
  return out;
}

/**
 * Colour a symbol's placeholder elements (fill and stroke) with `colour`, or with the surrounding ink (`currentColor`)
 * when null: per frame, through the painter.
 * @param {TemplateContext} ctx @param {Element[]} els @param {string | null} colour
 */
export function paintTint(ctx, els, colour) {
  const c = colour || 'currentColor';
  for (const el of els) {
    ctx.paint.attr(el, 'fill', c);
    if (el.hasAttribute('stroke')) ctx.paint.attr(el, 'stroke', c);
  }
}

/**
 * The colour a symbol tints to (§4.7: its lesson-wide entity colour, `ctx.entityRole`), or null (it stays `ink`).
 * @param {TemplateContext} ctx @param {string} symbol
 */
export function entityColour(ctx, symbol) {
  const role = typeof ctx.entityRole === 'function' ? ctx.entityRole(symbol) : null;
  return role ? ctx.roles.color(role) : null;
}
