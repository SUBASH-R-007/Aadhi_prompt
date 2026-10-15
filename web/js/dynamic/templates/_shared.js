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
 * - Icons (plan §1.2, B2b): `sanitiseIcon` / `storedIcon` draw `look.icons[item]` after checking it again against the
 *   schema's pattern and caps (the `LookIcon` model of aadhi.schemas.timeline and its JSON schema in web/schemas),
 *   path data set with setAttribute only.
 * - Media (§7.7, §P): `mediaAspect`, `containRect`, `pushHeadroom` (a push_ok template's picture box), `paintMedia`.
 * - Formula tints (§4.7 entity colours, §P (g)): `tintTex` wraps each symbol of a formula in a `\color` placeholder,
 *   `tintTargets` finds the placeholder's SVG groups after MathJax, `paintTint` colours them per frame.
 * Everything a template paints per frame goes through `ctx.paint` (the frame key); build- and layout-time placement
 * (`place`, `fitLines`) writes styles directly, as board_generic's layout does: it is a function of the laid-out page.
 */

import { h, svg } from '../../shared/dom.js';
import { ease } from '../../motion/easing.js';
import { progress } from '../../motion/track.js';
import { splitWords } from '../../motion/text.js';
import { mountTex, parseRich, renderRich } from '../../player/richtext.js';
import { icon } from '../../player/icons.js';
import { GRID, LAYOUT_TOKENS, REGIONS, TYPE_FLOORS, fixesOf, fx, templateSlots } from '../core.js';
import { itemStateStyle } from '../state.js';
import { PUSH_SCALE_MAX } from '../cues.js';

/** @typedef {import('./types.js').TemplateContext} TemplateContext */
/** @typedef {import('../core.js').Box} Box */
/** @typedef {import('../core.js').SlotSpec} SlotSpec */
/** @typedef {import('../../shared/types.js').MediaRef} MediaRef */

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
