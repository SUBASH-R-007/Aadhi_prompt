// @ts-check
/**
 * The two cues (DYNAMIC_VIDEO_DESIGN §4.8, R-26; B2 plan §1.5, deviation 3): `cue.term` and `cue.look_here`, drawn by
 * the stage from the scene's visual events, never by a template. Templates expose geometry only (§1.4: `itemEl`,
 * `focusRect`, `dimGroups`); this module turns `TimedScene.events` of kind `cue_term` / `look_here` into pixels, the
 * same way for every template, so a learner sees exactly two emphasis styles (§4.1 principle 2) and events.py's
 * limits (R-26: <= 2 concurrent, <= 1 new per 2 s, no repeat within 4 s; Python's budget) see every cue drawn.
 *
 * Which events: those the scene's template consumes (the catalogue's `templates.<id>.consumes`, core.js
 * `templateBehaviour`: the kind, and the target's form among the forms the template resolves for that kind,
 * `consumes.forms`), with a target in the §1.7 grammar (core.js `parseTarget`).
 * Anything else is skipped, never guessed, so a template that declares no cue (board_generic) is never given one.
 *
 * Timing (a pure function of the scene time t; no state carried between frames):
 * - a cue starts at `event.t` (events.py puts it on its anchor word) and holds until `t + max(draw, dur)` (`dur`: the
 *   event's, else CUE_HOLD), then leaves in 70% of its draw time (§4.8: exits 30% shorter);
 * - `cue.term` sweeps over TERM_DRAW (300 ms, left to right) and leaves by sweeping off from the left, so no frame
 *   shows a translucent marker under light glyphs;
 * - `cue.look_here` draws its outline in OUTLINE_DRAW (250 ms) and dims the other content groups to the catalogue's
 *   dim alpha (0.7) over DIM_RAMP (>= 300 ms, R-54's floor for a large luminance change), back over the same time.
 * Cue timings are the design's absolute values: the personality's duration scale (`ctx.motion.scale`) does not apply
 * to them (§4.1 principle 2: the two cue styles never vary inside a video, and learners rely on them).
 *
 * `cue.term` (§4.7, §4.8; plan §1.5) is drawn on the term's own span (`.rt-k` inside `itemEl`), as background layers
 * under its glyphs, one style per video (`presentation.cue_term_style`: `marker`, else `underline`):
 * - MARKER, dark families: an opaque `mark` band, and the glyphs switch to the dark on-block ink exactly where the band
 *   has swept (a `background-clip: text` layer with hard stops moving with the band; B2-0 (i) measured it
 *   order-independent), so no frame shows light ink on yellow or dark ink on the dark page. TeX inside a term (an SVG,
 *   whose fill is its `color`) switches as a whole when the band covers its centre.
 * - MARKER, light families: `mark` at 35% under the unchanged ink.
 * - UNDERLINE: a 4 px band (`mark` in dark families, `ink` in light ones) at the bottom of the span, drawn left to
 *   right.
 * - A term broken over lines is swept on every line at once (`box-decoration-break: clone`).
 * Reading of the plan: §1.5 places the cue layer above the content, but a marker must lie under its glyphs and a
 * layer above cannot put it there without copying the paragraph's line layout, so the term cue styles its span in
 * place (as plan v1 §1.4 had it); the `.dyn-cues` layer carries the look_here outline.
 *
 * `cue.look_here` (§4.8, plan §1.5): the template's `focusRect(ctx, target)` (whole px, read after its update this
 * frame) gets a 4 px `focus` outline outset by >= 8 px (rect, ring or bar), drawn in the Slot's `.dyn-cues` layer and
 * clamped to the critical zone that holds the target (text) or to the shapes area (a picture, card or drawing,
 * `FocusRect.kind` 'shape': `shapeArea`, B2c review CG-1); every `dimGroups` element that does not hold the target
 * (nor one of the elements `FocusRect.keep` names) fades to 70%. On a template whose spec allows it (`push_ok`) the cue is a camera push instead (a scale of the Slot's
 * `.dyn-camera` wrapper about the target's centre, PUSH_SCALE; §P (a) measured scaled text order-independent), except
 * in the live player under reduced motion, where it keeps the outline form (§4.8; render mode never applies it), and
 * in a scene that shows a clip (`showsClip`), which never pushes: its look_here cues keep the outline form in both
 * modes. Measured (B2c integration, real Chromium on Windows, --disable-partial-raster, tests/determinism/
 * test_template_clips.py): a contain_blur clip under the 3% push differed by render order (a page started inside the
 * push drew every later frame of the clip differently: 80 of 120 frames, in both formats, with the box's
 * `will-change: transform` layer, dynamic.css; 63 of 120 9:16 frames in reverse order with its backdrop shown, with
 * or without that layer), while stills under the same push, and clips under the medium's own 0.96 -> 1 entrance,
 * were order-independent. A clip is a new picture every frame, so a raster scale Chromium keeps from an earlier
 * frame shows; §P (d) measured a downscaled still only.
 *
 * Text that a look_here dims stays critical for preflight: `dimOf(el)` gives the stage the factor it composites into
 * the reported ink and background (stage.js textBoxes, plan §1.5).
 */

import { svg, h } from '../shared/dom.js';
import { ease } from '../motion/easing.js';
import { progress } from '../motion/track.js';
import { LAYOUT_TOKENS, cueColours, fx, parseTarget, templateBehaviour } from './core.js';

/** @typedef {import('../shared/types.js').TimedScene} TimedScene */
/** @typedef {import('./core.js').Painter} Painter */
/** @typedef {import('./core.js').Roles} Roles */
/** @typedef {import('./core.js').Zones} Zones */
/** @typedef {import('./core.js').Target} Target */
/** @typedef {import('./templates/types.js').TemplateModule} TemplateModule */
/** @typedef {import('./templates/types.js').TemplateContext} TemplateContext */
/** @typedef {import('./templates/types.js').FocusRect} FocusRect */

/** The event kinds this module draws. */
export const CUE_KINDS = Object.freeze(['cue_term', 'look_here']);
/** §4.8: the marker sweeps (and the underline draws) in 300 ms. */
export const TERM_DRAW = 0.3;
/** §4.8: the look_here outline draws in 250 ms. */
export const OUTLINE_DRAW = 0.25;
/** Plan §1.5: the other content dims to 70% over >= 300 ms (R-54's floor for a large luminance change), and back. */
export const DIM_RAMP = 0.3;
/** How long a cue without `dur` stays after its start (seconds; events.py, B2d, writes `dur`). */
export const CUE_HOLD = 2;
/** §4.8: exits are 30% shorter than entrances. */
export const EXIT_FACTOR = 0.7;
/** The camera push of a push_ok template: 3% (§4.3 media_split "push <= 3%"; §4.8 allows 2-6%), in over PUSH_IN. */
export const PUSH_SCALE = 0.03;
/** §4.8 "large move 600-900 ms": the push eases in over 600 ms and out over 70% of that. */
export const PUSH_IN = 0.6;
/**
 * The most a push ever scales the camera (§4.8: 2-6%): a push_ok template sizes the box its pictures are extracted at
 * (`mediaBox`) by 1 + PUSH_SCALE_MAX, so a pushed picture still only ever downscales (§P condition 1).
 */
export const PUSH_SCALE_MAX = 0.06;

/** The dim alpha, outline width, outset and underline width (the catalogue's layout tokens, plan §1.1). */
export const DIM_ALPHA = Number(LAYOUT_TOKENS.alpha.dim);
export const FOCUS_PX = Number(LAYOUT_TOKENS.outline.focus_px);
export const FOCUS_OUTSET = Number(LAYOUT_TOKENS.outline.focus_outset_px);
export const UNDERLINE_PX = Number(LAYOUT_TOKENS.outline.underline_px);

/** The style properties a term cue writes on its span (every one, every frame: '' when the cue is off). */
const TERM_PROPS = Object.freeze([
  'background-image', 'background-size', 'background-position', 'background-repeat', 'background-clip',
  '-webkit-background-clip', '-webkit-text-fill-color', 'box-decoration-break', '-webkit-box-decoration-break',
]);

/**
 * One cue event as this module reads it.
 * @typedef {object} CueEvent
 * @property {'cue_term' | 'look_here'} kind
 * @property {string} target      the event's target string
 * @property {Target} parsed
 * @property {number} start       scene seconds
 * @property {number} hold        when its exit starts
 * @property {number} order       position in the scene's events (ties)
 */

/**
 * The cue events of a scene that its template draws, in time order (ties: event order).
 * @param {TimedScene} scene
 * @param {string} templateId   the template the stage draws the scene with (stage.js templateFor)
 * @returns {CueEvent[]}
 */
export function cueEvents(scene, templateId) {
  const behaviour = templateBehaviour(templateId);
  if (!behaviour) return [];
  /** @type {CueEvent[]} */
  const out = [];
  (scene.events || []).forEach((e, order) => {
    if (!e || (e.kind !== 'cue_term' && e.kind !== 'look_here')) return;
    if (!behaviour.consumes.kinds.includes(e.kind) || typeof e.t !== 'number' || !Number.isFinite(e.t)) return;
    const parsed = parseTarget(e.target);
    // the target forms the template resolves for this kind (templates.py Consumes.forms: B2c review CG-5, a cross
    // product of kinds and forms let through pairs no template draws, such as a cue_term on a panel)
    if (!parsed || !behaviour.consumes.forms[e.kind].includes(parsed.form)) return;
    const draw = e.kind === 'cue_term' ? TERM_DRAW : OUTLINE_DRAW;
    const dur = typeof e.dur === 'number' && Number.isFinite(e.dur) && e.dur > 0 ? e.dur : CUE_HOLD;
    out.push({ kind: e.kind, target: String(e.target), parsed, start: e.t, hold: e.t + Math.max(draw, dur), order });
  });
  out.sort((a, b) => a.start - b.start || a.order - b.order);
  return out;
}

/**
 * How far a cue is in at scene time t (0..1): its entrance over `inDur` (ease.move), times one minus its exit over
 * `outDur` from `hold`.
 * @param {number} t @param {number} start @param {number} inDur @param {number} hold @param {number} outDur
 */
export function cueLevel(t, start, inDur, hold, outDur) {
  const on = ease('ease.move', progress(t, start, inDur));
  const off = ease('ease.move', progress(t, hold, outDur));
  return on * (1 - off);
}

/**
 * The swept band of a term cue at t: [a, b] as fractions of the span's width (b: how far the sweep in has reached,
 * a: how far the sweep off has cleared), or null when the cue is not on screen.
 * @param {CueEvent} cue @param {number} t
 * @returns {{a: number, b: number} | null}
 */
export function termBand(cue, t) {
  if (!(t >= cue.start)) return null;
  const exit = TERM_DRAW * EXIT_FACTOR;
  if (t >= cue.hold + exit) return null;
  const b = ease('ease.move', progress(t, cue.start, TERM_DRAW));
  const a = ease('ease.move', progress(t, cue.hold, exit));
  return b > a ? { a, b } : null;
}

/** @param {number} f */
const pct = (f) => `${fx(Math.min(1, Math.max(0, f)) * 100, 2)}%`;

/**
 * A two-colour hard-stop gradient: `outside` before a and after b, `inside` between them.
 * @param {string} outside @param {string} inside @param {number} a @param {number} b
 */
export function bandGradient(outside, inside, a, b) {
  return `linear-gradient(90deg, ${outside} ${pct(a)}, ${inside} ${pct(a)}, ${inside} ${pct(b)}, ${outside} ${pct(b)})`;
}

/**
 * The style a term cue gives its span with band [a, b] (plan §1.5; see the module comment), as property -> value
 * ('' clears). Pure: unit-tested per style and mode, with the glyph contrast checked at every sweep step.
 * @param {'marker' | 'underline'} style
 * @param {'dark' | 'light'} mode
 * @param {{mark: string, markerInk: string, markerAlpha: number, underline: string}} colours   core.js cueColours
 * @param {{a: number, b: number} | null} band
 * @returns {Record<string, string>}
 */
export function termStyle(style, mode, colours, band) {
  /** @type {Record<string, string>} */
  const out = {};
  for (const p of TERM_PROPS) out[p] = '';
  if (!band) return out;
  out['box-decoration-break'] = 'clone';
  out['-webkit-box-decoration-break'] = 'clone';
  out['background-repeat'] = 'no-repeat';
  if (style === 'underline') {
    out['background-image'] = bandGradient('transparent', colours.underline, band.a, band.b);
    out['background-size'] = `100% ${UNDERLINE_PX}px`;
    out['background-position'] = '0 100%';
    return out;
  }
  if (mode === 'dark') {
    // the glyph layer (clipped to the text) above the marker band: dark ink where swept, the span's own ink elsewhere
    out['background-image'] = `${bandGradient('currentColor', colours.markerInk, band.a, band.b)}, ${bandGradient('transparent', colours.mark, band.a, band.b)}`;
    out['background-clip'] = 'text, border-box';
    out['-webkit-background-clip'] = 'text, border-box';
    out['-webkit-text-fill-color'] = 'transparent';
    out['background-size'] = '100% 100%, 100% 100%';
    out['background-position'] = '0 0, 0 0';
    return out;
  }
  const alpha = Math.round(Math.min(1, Math.max(0, colours.markerAlpha)) * 255).toString(16).padStart(2, '0');
  out['background-image'] = bandGradient('transparent', `${colours.mark}${alpha}`, band.a, band.b);
  out['background-size'] = '100% 100%';
  out['background-position'] = '0 0';
  return out;
}

/**
 * The outline path of a look_here around `focus`, outset so its inner edge clears the target by FOCUS_OUTSET, its
 * box clamped to `zone`: `rect` a rounded rectangle (the target's radius grown by the outset), `bar` a pill around a
 * line, `ring` a circle around the target (its square clamped). Returns the SVG path data and its length (for the
 * draw), on whole and half pixels only.
 * @param {FocusRect} focus
 * @param {{x0: number, y0: number, x1: number, y1: number} | null} zone
 * @returns {{d: string, length: number, box: {x0: number, y0: number, x1: number, y1: number}} | null}
 */
export function outlinePath(focus, zone) {
  const r = focus.rect;
  if (!(r.w > 0 && r.h > 0)) return null;
  const grow = FOCUS_OUTSET + FOCUS_PX / 2; // the stroke's centre line
  let box = { x0: r.x - grow, y0: r.y - grow, x1: r.x + r.w + grow, y1: r.y + r.h + grow };
  if (focus.shape === 'ring') {
    const cx = r.x + r.w / 2;
    const cy = r.y + r.h / 2;
    const rad = Math.hypot(r.w, r.h) / 2 + grow;
    box = { x0: cx - rad, y0: cy - rad, x1: cx + rad, y1: cy + rad };
  }
  if (zone) {
    const half = FOCUS_PX / 2;
    box = {
      x0: Math.max(box.x0, zone.x0 + half), y0: Math.max(box.y0, zone.y0 + half),
      x1: Math.min(box.x1, zone.x1 - half), y1: Math.min(box.y1, zone.y1 - half),
    };
  }
  const x0 = Math.round(box.x0 * 2) / 2;
  const y0 = Math.round(box.y0 * 2) / 2;
  const x1 = Math.round(box.x1 * 2) / 2;
  const y1 = Math.round(box.y1 * 2) / 2;
  const w = x1 - x0;
  const hh = y1 - y0;
  if (!(w > 0 && hh > 0)) return null;
  if (focus.shape === 'ring') {
    const rad = Math.min(w, hh) / 2;
    const cx = x0 + w / 2;
    const cy = y0 + hh / 2;
    const d = `M${fx(cx, 1)} ${fx(cy - rad, 1)}A${fx(rad, 1)} ${fx(rad, 1)} 0 1 1 ${fx(cx, 1)} ${fx(cy + rad, 1)}A${fx(rad, 1)} ${fx(rad, 1)} 0 1 1 ${fx(cx, 1)} ${fx(cy - rad, 1)}Z`;
    return { d, length: 2 * Math.PI * rad, box: { x0, y0, x1, y1 } };
  }
  const want = focus.shape === 'bar' ? hh / 2 : Math.max(0, Number(focus.radius) || 0) + grow;
  const rad = Math.min(want, w / 2, hh / 2);
  const q = (/** @type {number} */ v) => fx(v, 1);
  // clockwise from the top edge's left end
  const d = `M${q(x0 + rad)} ${q(y0)}H${q(x1 - rad)}A${q(rad)} ${q(rad)} 0 0 1 ${q(x1)} ${q(y0 + rad)}V${q(y1 - rad)}`
    + `A${q(rad)} ${q(rad)} 0 0 1 ${q(x1 - rad)} ${q(y1)}H${q(x0 + rad)}A${q(rad)} ${q(rad)} 0 0 1 ${q(x0)} ${q(y1 - rad)}`
    + `V${q(y0 + rad)}A${q(rad)} ${q(rad)} 0 0 1 ${q(x0 + rad)} ${q(y0)}Z`;
  const length = 2 * (w - 2 * rad) + 2 * (hh - 2 * rad) + 2 * Math.PI * rad;
  return { d, length, box: { x0, y0, x1, y1 } };
}

/**
 * The box a look_here outline is clamped to (R-59; preflight `inside_bands`): the critical boxes the target overlaps,
 * their shared x range and their combined y range. The 9:16 boxes meet at y 840 (x 192-888 above, 120-780 below), so a
 * 9:16 row whose text crosses y 840 keeps its whole outline (clamped to x 192-780) instead of being cut at 840 through
 * the text, which clamping to the one box holding its centre did (B2c, list_build's report). zoneOf when the target
 * overlaps none (or the boxes share no x range).
 * @param {Zones} zones @param {{x: number, y: number, w: number, h: number}} r
 * @returns {{x0: number, y0: number, x1: number, y1: number} | null}
 */
export function clampOf(zones, r) {
  const hit = zones.critical.filter((b) => Math.min(b.y1, r.y + r.h) > Math.max(b.y0, r.y)
    && Math.min(b.x1, r.x + r.w) > Math.max(b.x0, r.x));
  if (hit.length < 2) return hit.length ? hit[0] : zoneOf(zones, r);
  const box = {
    x0: Math.max(...hit.map((b) => b.x0)), y0: Math.min(...hit.map((b) => b.y0)),
    x1: Math.min(...hit.map((b) => b.x1)), y1: Math.max(...hit.map((b) => b.y1)),
  };
  return box.x1 > box.x0 ? box : zoneOf(zones, r);
}

/**
 * The box a look_here outline around a non-text target (`FocusRect.kind` 'shape': a picture, a card, a drawing) is
 * clamped to (B2c review CG-1): the bounds of the content stage and the critical boxes (zones.body: shapes span x
 * 60-1020 in 9:16, wider than the text column), grown by the outline's reach (FOCUS_OUTSET + FOCUS_PX) so an outline
 * around anything inside them is never cut, inside the frame and above the caption band. Clamping such a target to
 * the critical text boxes (clampOf) drew its outline across it: a 9:16 media_split picture spanning x 277-802 got its
 * right edge at x 778, inside the picture; a 9:16 formula_focus card (x 156-924) got x 194-886, on the formula's own
 * text column (measured on the real page, B2c review).
 * @param {Zones} zones
 * @returns {{x0: number, y0: number, x1: number, y1: number}}
 */
export function shapeArea(zones) {
  const grow = FOCUS_OUTSET + FOCUS_PX;
  const boxes = [zones.body, ...zones.critical];
  return {
    x0: Math.max(0, Math.min(...boxes.map((b) => b.x0)) - grow),
    y0: Math.max(0, Math.min(...boxes.map((b) => b.y0)) - grow),
    x1: Math.min(zones.width, Math.max(...boxes.map((b) => b.x1)) + grow),
    y1: Math.min(zones.caption.y0, Math.max(...boxes.map((b) => b.y1)) + grow),
  };
}

/**
 * The critical box (R-59) a target lies in: the one holding its centre, else the one it overlaps most; null without
 * critical boxes.
 * @param {Zones} zones @param {{x: number, y: number, w: number, h: number}} r
 */
export function zoneOf(zones, r) {
  const cx = r.x + r.w / 2;
  const cy = r.y + r.h / 2;
  const inside = zones.critical.find((b) => cx >= b.x0 && cx <= b.x1 && cy >= b.y0 && cy <= b.y1);
  if (inside) return inside;
  let best = null;
  let most = -1;
  for (const b of zones.critical) {
    const ow = Math.max(0, Math.min(b.x1, r.x + r.w) - Math.max(b.x0, r.x));
    const oh = Math.max(0, Math.min(b.y1, r.y + r.h) - Math.max(b.y0, r.y));
    if (ow * oh > most) {
      most = ow * oh;
      best = b;
    }
  }
  return best;
}

/**
 * The elements an `itemEl` result names, in order (a template may return one element or, for an item drawn in
 * several pieces, such as a 9:16 misconception's myth and fact cards, an array in the item's field order).
 * @param {Element | Element[] | null | undefined} found
 * @returns {Element[]}
 */
export function asElements(found) {
  if (!found) return [];
  return (Array.isArray(found) ? found : [found]).filter((e) => !!e && typeof e === 'object' && 'querySelectorAll' in e);
}

/**
 * The [[terms]] of the elements an `itemEl` returned, in order, each as its pieces: a term is one `.rt-k` span
 * (renderRich), or several when a template split it into word spans (_shared.js `richWords` gives every piece of one
 * term the same `data-dyn-term` within its element, so consecutive pieces with the same value are one term).
 * @param {Element[]} els
 * @returns {HTMLElement[][]}
 */
export function termsOf(els) {
  /** @type {HTMLElement[][]} */
  const out = [];
  for (const el of els) {
    /** @type {HTMLElement[]} */
    const spans = [];
    if (el.classList && el.classList.contains('rt-k')) spans.push(/** @type {HTMLElement} */ (el));
    for (const s of el.querySelectorAll('.rt-k')) spans.push(/** @type {HTMLElement} */ (s));
    /** @type {string | null} */
    let last = null;
    for (const span of spans) {
      const id = span.getAttribute('data-dyn-term');
      if (id !== null && id === last && out.length) out[out.length - 1].push(span);
      else out.push([span]);
      last = id;
    }
  }
  return out;
}

/**
 * The pieces a cue_term target sweeps, each with the share of its term's sweep it takes (`from`..`to`, by the
 * length of its text, so a term split into word pieces sweeps left to right across them): every term of the item's
 * elements (`<item>`, `option:<i>`; all at once), or its n-th term (`<item>:term:<n>`, plan §1.7 as extended in
 * B2c); none for any other form.
 * @param {TemplateModule} template @param {TemplateContext} ctx @param {CueEvent} cue
 * @returns {{span: HTMLElement, from: number, to: number}[]}
 */
export function termPieces(template, ctx, cue) {
  if (typeof template.itemEl !== 'function') return [];
  const p = cue.parsed;
  const key = p.form === 'item' || p.form === 'item:term' ? String(p.item) : cue.target;
  const terms = termsOf(asElements(/** @type {any} */ (template.itemEl(ctx, key))));
  const n = /** @type {number} */ (p.index);
  const chosen = p.form === 'item:term' ? (terms[n] ? [terms[n]] : []) : terms;
  /** @type {{span: HTMLElement, from: number, to: number}[]} */
  const out = [];
  for (const pieces of chosen) {
    const lengths = pieces.map((s) => Math.max(1, (s.textContent || '').length));
    const total = lengths.reduce((a, b) => a + b, 0);
    let at = 0;
    pieces.forEach((span, i) => {
      out.push({ span, from: at / total, to: (at + lengths[i]) / total });
      at += lengths[i];
    });
  }
  return out;
}

/**
 * The spans of `termPieces` (the harness and tests).
 * @param {TemplateModule} template @param {TemplateContext} ctx @param {CueEvent} cue
 * @returns {HTMLElement[]}
 */
export function termSpans(template, ctx, cue) {
  return termPieces(template, ctx, cue).map((x) => x.span);
}

/**
 * A term's band as one of its pieces (`from`..`to` of the term's sweep) sees it, or null when it does not reach it.
 * @param {{a: number, b: number} | null} band @param {number} from @param {number} to
 * @returns {{a: number, b: number} | null}
 */
export function pieceBand(band, from, to) {
  if (!band) return null;
  const w = to - from;
  if (!(w > 0)) return null;
  const a = Math.min(1, Math.max(0, (band.a - from) / w));
  const b = Math.min(1, Math.max(0, (band.b - from) / w));
  return b > a ? { a, b } : null;
}

/**
 * The horizontal centre of `node` as a fraction of the line fragment of `span` that holds it (0..1), for the TeX
 * colour switch; 0.5 when the page has no layout (unit tests) or no fragment holds it.
 * @param {HTMLElement} span @param {Element} node
 */
function centreFraction(span, node) {
  const r = node.getBoundingClientRect();
  const cx = r.left + r.width / 2;
  const cy = r.top + r.height / 2;
  for (const f of span.getClientRects()) {
    if (f.width > 0 && cy >= f.top - 1 && cy <= f.bottom + 1 && cx >= f.left - 1 && cx <= f.right + 1) {
      return Math.min(1, Math.max(0, (cx - f.left) / f.width));
    }
  }
  return 0.5;
}

/**
 * The cue drawer of one Slot (stage.js), built after its template.
 * @typedef {object} SlotCues
 * @property {(paint: Painter) => void} begin     before template.update: the camera back to identity, so the
 *   template's geometry is read unpushed (no frame depends on the push of the frame before it)
 * @property {(paint: Painter, t: number) => void} update   after template.update: draw the cues of scene time t
 * @property {(el: Element) => number} dimOf      the look_here dim factor on `el` now (1: not dimmed)
 * @property {CueEvent[]} events
 */

/**
 * Whether `scene` shows a clip (a video medium: its own `media` or its side panel's, in any mode): such a scene never
 * pushes the camera (see the header: a clip under a push is drawn differently by render order).
 * @param {TimedScene} scene
 */
export function showsClip(scene) {
  const own = scene.media;
  const panel = scene.side_panel ? scene.side_panel.media : null;
  return (!!own && own.kind === 'video') || (!!panel && panel.kind === 'video');
}

/**
 * Whether a Slot of `scene` drawn by `templateId` needs the camera wrapper (a look_here it may push: a push_ok template,
 * never over a clip, `showsClip`) and the cue layer.
 * @param {TimedScene} scene @param {string} templateId
 */
export function cueNeeds(scene, templateId) {
  const events = cueEvents(scene, templateId);
  const behaviour = templateBehaviour(templateId);
  return {
    events,
    camera: !!behaviour && behaviour.pushOk && !showsClip(scene) && events.some((e) => e.kind === 'look_here'),
    layer: events.some((e) => e.kind === 'look_here'),
  };
}

/**
 * @param {object} opts
 * @param {TemplateModule} opts.template
 * @param {TemplateContext} opts.ctx
 * @param {CueEvent[]} opts.events            cueEvents(scene, template id)
 * @param {HTMLElement | null} opts.layer     the Slot's `.dyn-cues` layer (look_here outlines)
 * @param {HTMLElement | null} opts.camera    the Slot's `.dyn-camera` wrapper (push_ok templates)
 * @param {'marker' | 'underline'} opts.termStyle   presentation.cue_term_style
 * @param {boolean} opts.pushOk
 * @param {boolean} opts.reducedMotion        live only (stage.js): the push keeps the outline form
 * @returns {SlotCues}
 */
export function createCues({ template, ctx, events, layer, camera, termStyle: style, pushOk, reducedMotion }) {
  const roles = /** @type {Roles} */ (ctx.roles);
  const colours = cueColours(roles);
  const focusColour = roles.color('focus');
  const push = pushOk && !!camera && !reducedMotion;
  const terms = events.filter((e) => e.kind === 'cue_term');
  const looks = events.filter((e) => e.kind === 'look_here');
  /** @type {SVGElement | null} */
  let svgEl = null;
  /** @type {SVGElement[]} */
  const paths = [];
  if (layer && looks.length && !push) {
    svgEl = svg('svg', { class: 'dyn-cues-svg', width: String(ctx.zones.width), height: String(ctx.zones.height), 'aria-hidden': 'true' });
    svgEl.setAttribute('viewBox', `0 0 ${ctx.zones.width} ${ctx.zones.height}`);
    for (let i = 0; i < looks.length; i++) {
      const path = svg('path', {
        class: 'dyn-cue-outline', fill: 'none', stroke: focusColour, 'stroke-width': String(FOCUS_PX),
        'stroke-linecap': 'round', 'stroke-linejoin': 'round',
      });
      // hidden until drawn; its style attribute comes first, so the DOM of a frame never depends on the order of seeks
      path.style.visibility = 'hidden';
      svgEl.appendChild(path);
      paths.push(path);
    }
    layer.appendChild(svgEl);
  }
  /** Dim factor per group element, as painted this frame. @type {Map<Element, number>} */
  let dims = new Map();

  /**
   * The pieces of every term cue, resolved on the first frame (itemEl is static after build, so the set of spans the
   * cues write is the same whatever frame comes first).
   * @type {{span: HTMLElement, from: number, to: number}[][] | null}
   */
  let termTargets = null;
  const resolveTerms = () => {
    if (!termTargets) termTargets = terms.map((cue) => termPieces(template, ctx, cue));
    return termTargets;
  };

  return {
    events,

    begin(paint) {
      if (camera) {
        paint.style(camera, 'transform', '');
        paint.style(camera, 'transform-origin', '');
      }
    },

    update(paint, t) {
      // cue.term: every span any term cue names, each frame (the latest cue on a span wins)
      if (terms.length) {
        const targets = resolveTerms();
        /** @type {Map<HTMLElement, {a: number, b: number} | null>} */
        const bands = new Map();
        targets.forEach((pieces, i) => {
          const band = termBand(terms[i], t);
          for (const { span, from, to } of pieces) {
            const own = pieceBand(band, from, to);
            if (own || !bands.has(span)) bands.set(span, own);
          }
        });
        for (const [span, band] of bands) {
          const css = termStyle(style, roles.mode, colours, band);
          for (const prop of TERM_PROPS) paint.style(span, prop, css[prop]);
          if (style === 'marker' && roles.mode === 'dark') {
            // TeX in a term: its SVG takes the dark ink while the band covers its centre
            for (const tex of span.querySelectorAll('svg')) {
              const c = band ? centreFraction(span, tex) : -1;
              paint.style(/** @type {SVGElement} */ (tex), 'color', band && c >= band.a && c <= band.b ? colours.markerInk : '');
            }
          }
        }
      }
      if (!looks.length) return;
      // cue.look_here: the outline (or the push) and the dim of the other groups
      const groups = typeof template.dimGroups === 'function' ? template.dimGroups(ctx).filter(Boolean) : [];
      /** @type {Map<Element, number>} */
      const level = new Map(groups.map((g) => [g, 0]));
      let pushLevel = 0;
      /** @type {{x: number, y: number} | null} */
      let origin = null;
      looks.forEach((cue, i) => {
        const outExit = OUTLINE_DRAW * EXIT_FACTOR;
        const live = t >= cue.start && t < cue.hold + Math.max(DIM_RAMP, push ? PUSH_IN * EXIT_FACTOR : outExit);
        const focus = live && typeof template.focusRect === 'function' ? template.focusRect(ctx, cue.target) : null;
        const path = paths[i];
        if (!focus) {
          if (path) hideOutline(paint, path);
          return;
        }
        if (push) {
          const k = cueLevel(t, cue.start, PUSH_IN, cue.hold, PUSH_IN * EXIT_FACTOR);
          if (k > pushLevel) {
            pushLevel = k;
            origin = { x: Math.round(focus.rect.x + focus.rect.w / 2), y: Math.round(focus.rect.y + focus.rect.h / 2) };
          }
          return;
        }
        // text: inside the critical text boxes (R-59); a picture, card or drawing: the shapes area (CG-1)
        const zone = focus.kind === 'shape' ? shapeArea(ctx.zones) : clampOf(ctx.zones, focus.rect);
        const shape = outlinePath(focus, zone);
        const draw = ease('ease.move', progress(t, cue.start, OUTLINE_DRAW));
        const fade = 1 - ease('ease.move', progress(t, cue.hold, outExit));
        if (path) {
          if (shape && draw > 0 && fade > 0) {
            const len = shape.length;
            paint.attr(path, 'd', shape.d);
            paint.attr(path, 'stroke-dasharray', `${fx(len, 2)} ${fx(len, 2)}`);
            paint.attr(path, 'stroke-dashoffset', fx(len * (1 - draw), 2));
            paint.style(path, 'opacity', fx(fade));
            paint.style(path, 'visibility', 'visible');
          } else {
            hideOutline(paint, path);
          }
        }
        const k = cueLevel(t, cue.start, DIM_RAMP, cue.hold, DIM_RAMP);
        const holder = /** @type {Element | undefined} */ (focus.el);
        const centre = { x: focus.rect.x + focus.rect.w / 2, y: focus.rect.y + focus.rect.h / 2 };
        const keep = Array.isArray(focus.keep) ? focus.keep.filter(Boolean) : [];
        for (const g of groups) {
          if (holdsTarget(ctx, g, holder, centre) || keep.some((k) => g === k || g.contains(k) || k.contains(g))) continue;
          level.set(g, Math.max(/** @type {number} */ (level.get(g)), k));
        }
      });
      if (camera) {
        if (pushLevel > 0 && origin) {
          const o = /** @type {{x: number, y: number}} */ (origin);
          paint.style(camera, 'transform-origin', `${o.x}px ${o.y}px`);
          paint.style(camera, 'transform', `scale(${fx(1 + PUSH_SCALE * pushLevel, 4)})`);
        }
      }
      dims = new Map();
      for (const [g, k] of level) {
        const f = 1 - (1 - DIM_ALPHA) * k;
        dims.set(g, f);
        paint.style(/** @type {HTMLElement} */ (g), 'opacity', k > 0 ? fx(f) : '');
      }
    },

    dimOf(el) {
      let f = 1;
      for (const [g, d] of dims) if (d < 1 && (g === el || g.contains(el))) f = Math.min(f, d);
      return f;
    },
  };
}

/**
 * An outline path off screen: every value the drawn form writes is cleared too, so its DOM never depends on the
 * frames painted before.
 * @param {Painter} paint @param {SVGElement} path
 */
function hideOutline(paint, path) {
  paint.attr(path, 'd', null);
  paint.attr(path, 'stroke-dasharray', null);
  paint.attr(path, 'stroke-dashoffset', null);
  paint.style(path, 'opacity', '');
  paint.style(path, 'visibility', 'hidden');
}

/**
 * Whether content group `g` holds the look_here target: it contains (or is) the target's element when focusRect named
 * one, else its box holds the target's centre.
 * @param {TemplateContext} ctx @param {Element} g @param {Element | undefined} holder @param {{x: number, y: number}} c
 */
function holdsTarget(ctx, g, holder, c) {
  if (holder) return g === holder || g.contains(holder) || holder.contains(g);
  const r = ctx.rectOf(g);
  return !!r && c.x >= r.x && c.x <= r.x + r.w && c.y >= r.y && c.y <= r.y + r.h;
}

/** The Slot's cue layer and camera wrapper (created only for a scene with cue events its template draws). */
export function cueLayer() {
  return h('div', { class: 'dyn-cues', 'aria-hidden': 'true' });
}

/** @param {HTMLElement} content */
export function cameraAround(content) {
  return h('div', { class: 'dyn-camera' }, content);
}
