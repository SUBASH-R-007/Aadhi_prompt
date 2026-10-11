// @ts-check
/**
 * Shared pieces of the Dynamic stage (DYNAMIC_VIDEO_DESIGN §7.3, §7.4): frame math that matches the motion
 * engine's chunk planner, the painter every per-frame DOM write goes through (it also hashes the frame's visual
 * inputs for `seek()`'s key), the colour roles a template may use (§4.2), and the safe zones of both formats (R-59).
 *
 * Pure: no DOM access at import time and no clock. Everything a frame shows is a function of (timeline, t).
 */

import { contrastRatio, fromOklch, mixOklch, toOklch } from '../motion/color.js';

/** @typedef {import('../shared/types.js').Timeline} Timeline */
/** @typedef {import('../shared/types.js').TimedScene} TimedScene */
/** @typedef {import('../shared/types.js').ResolvedLook} ResolvedLook */
/** @typedef {import('../shared/types.js').TimelinePresentation} TimelinePresentation */

// ---------------------------------------------------------------------------------------------
// Frames
// ---------------------------------------------------------------------------------------------

/** Dynamic videos are 30 fps CFR (design §2.1); a timeline without a usable rate uses this. */
export const DEFAULT_FPS = 30;

/**
 * Frame number of an absolute time: the motion engine's rounding (`math.floor(t * fps + 0.5)`,
 * aadhi/compose/motion/chunks.py scene_frame_bounds), so the page and the worker agree on every boundary.
 * @param {number} seconds
 * @param {number} fps
 */
export function frameOf(seconds, fps) {
  return Math.floor(seconds * fps + 0.5);
}

/**
 * The timeline's frame rate (a positive integer), DEFAULT_FPS otherwise.
 * @param {any} timeline
 */
export function timelineFps(timeline) {
  const fps = timeline && timeline.fps;
  return typeof fps === 'number' && Number.isInteger(fps) && fps > 0 && fps <= 240 ? fps : DEFAULT_FPS;
}

/**
 * `aadhiStage.info()`: stage size, frame rate, frame count and each scene's frame range [f0, f1). Scenes are
 * contiguous and have at least one frame each (scene_frame_bounds).
 * @param {Timeline} timeline
 * @returns {{width: number, height: number, fps: number, frames: number, scenes: {index: number, f0: number, f1: number}[]}}
 */
export function stageInfo(timeline) {
  const fps = timelineFps(timeline);
  const scenes = timeline.scenes || [];
  const edges = [...scenes.map((s) => Number(s.start) || 0), Number(timeline.total_duration) || 0].map((e) => frameOf(e, fps));
  /** @type {{index: number, f0: number, f1: number}[]} */
  const out = [];
  for (let i = 0; i < scenes.length; i++) {
    const f0 = i ? out[i - 1].f1 : edges[0];
    out.push({ index: i, f0, f1: Math.max(f0 + 1, edges[i + 1]) });
  }
  return {
    width: Number(timeline.width) || 1920,
    height: Number(timeline.height) || 1080,
    fps,
    frames: out.length ? out[out.length - 1].f1 : 0,
    scenes: out,
  };
}

/**
 * Index of the scene that owns frame `f` (by frame range), clamped to the first / last scene; -1 without scenes.
 * @param {{f0: number, f1: number}[]} ranges
 * @param {number} f
 */
export function sceneIndexOfFrame(ranges, f) {
  if (!ranges.length) return -1;
  let lo = 0;
  let hi = ranges.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (ranges[mid].f0 <= f) lo = mid;
    else hi = mid - 1;
  }
  return lo;
}

// ---------------------------------------------------------------------------------------------
// Hashing and the painter
// ---------------------------------------------------------------------------------------------

/**
 * Two independent 32-bit FNV-style lanes over UTF-16 code units: a 64-bit state for frame keys (deduplication must
 * not confuse two different frames; 32 bits alone would collide across a long video).
 */
export class Hasher {
  constructor() {
    this.a = 0x811c9dc5;
    this.b = 0x01000193 ^ 0x5bd1e995;
  }

  /** @param {string} s */
  add(s) {
    let a = this.a;
    let b = this.b;
    for (let i = 0; i < s.length; i++) {
      const c = s.charCodeAt(i);
      a = Math.imul(a ^ c, 0x01000193);
      b = Math.imul(b ^ c, 0x5bd1e995);
      b ^= b >>> 15;
    }
    // a separator, so ("ab", "c") and ("a", "bc") differ
    this.a = Math.imul(a ^ 0x1f, 0x01000193);
    this.b = Math.imul(b ^ 0x1f, 0x5bd1e995);
    return this;
  }

  /** 16 hex digits. */
  digest() {
    const hex = (/** @type {number} */ v) => (v >>> 0).toString(16).padStart(8, '0');
    return `${hex(this.a)}${hex(this.b)}`;
  }
}

/**
 * The only way the stage writes time-dependent DOM state. Every call is hashed (in call order) into the frame key,
 * so two frames with the same key look the same; DOM writes are skipped when the value did not change (the cache
 * mirrors the DOM, so skipping never depends on history).
 */
export class Painter {
  constructor() {
    /** @type {WeakMap<object, Map<string, string>>} */
    this.last = new WeakMap();
    this.hash = new Hasher();
    this.writes = 0;
  }

  /** Start a frame. */
  begin() {
    this.hash = new Hasher();
    this.writes = 0;
  }

  /**
   * @param {object} el
   * @param {string} key
   * @param {string} value
   * @returns {boolean} true when the DOM must be written
   */
  _changed(el, key, value) {
    this.hash.add(key).add(value);
    let m = this.last.get(el);
    if (!m) {
      m = new Map();
      this.last.set(el, m);
    }
    if (m.get(key) === value) return false;
    m.set(key, value);
    this.writes += 1;
    return true;
  }

  /**
   * Set a style property ('' removes it). Custom properties (`--x`) work too.
   * @param {HTMLElement | SVGElement} el
   * @param {string} prop   CSS property name (kebab-case)
   * @param {string | number} value
   */
  style(el, prop, value) {
    const v = String(value);
    if (this._changed(el, `s:${prop}`, v)) {
      if (v === '') el.style.removeProperty(prop);
      else el.style.setProperty(prop, v);
    }
  }

  /**
   * @param {Element} el
   * @param {string} value
   */
  text(el, value) {
    const v = String(value);
    if (this._changed(el, 't:', v)) el.textContent = v;
  }

  /**
   * Set (or with null remove) an attribute.
   * @param {Element} el
   * @param {string} name
   * @param {string | null} value
   */
  attr(el, name, value) {
    const v = value === null ? '\u0000' : String(value);
    if (this._changed(el, `a:${name}`, v)) {
      if (value === null) el.removeAttribute(name);
      else el.setAttribute(name, v);
    }
  }

  /**
   * Hash a value that is not a DOM write (e.g. the scene indices on screen).
   * @param {string} value
   */
  note(value) {
    this.hash.add(value);
  }

  /** Forget cached values (after DOM nodes were rebuilt). */
  reset() {
    this.last = new WeakMap();
  }

  digest() {
    return this.hash.digest();
  }
}

/**
 * Round for CSS output: stable strings, and no sub-1/1000 jitter between machines.
 * @param {number} v
 * @param {number} [digits]
 */
export function fx(v, digits = 3) {
  const s = (Math.round(v * 10 ** digits) / 10 ** digits).toFixed(digits);
  return s === '-0.000' || s === '-0.00' || s === '-0' ? s.slice(1) : s;
}

// ---------------------------------------------------------------------------------------------
// Colour roles (§4.2, §4.7)
// ---------------------------------------------------------------------------------------------

/** The closed list of roles a template may ask for. */
export const ROLES = Object.freeze([
  'ink', 'ink2', 'surface', 'entity:0', 'entity:1', 'entity:2', 'warn', 'ok', 'mark', 'focus',
  'struct.accent', 'struct.tint', 'struct.border', 'struct.pip',
]);

/** Fallback content tokens (dark mode, §4.7) for a timeline that carries none. */
const DARK_CONTENT = Object.freeze({
  ink: '#F3F5F9', ink2: '#B9BEC7', 'entity:0': '#0FE3FE', 'entity:1': '#C4A4FE', 'entity:2': '#FEACE3',
  warn: '#F8A87F', ok: '#5DD8AA', mark: '#F2D935', focus: '#F2D935',
});
const DARK_STRUCT = Object.freeze({
  'struct.accent': '#FF936D', bg: '#10132C', 'bg-alt': '#181D37', 'bg-tint': '#22121C', surface: '#252A3F',
});
const HEX = /^#[0-9a-f]{6}$/i;

/** Text colours offered for text drawn straight on a saturated block (bumpers, colour blocks). */
const ON_BLOCK = Object.freeze(['#FFFFFF', '#14161C']);

/**
 * @typedef {object} Roles
 * @property {(role: string) => string} color   hex of a role (unknown roles throw: a typo fails in tests)
 * @property {'dark' | 'light'} mode
 * @property {Record<string, string>} tokens   the scene's structure tokens (bg, bg-alt, bg-tint, struct.accent, surface)
 * @property {(bg: string | string[]) => string} inkOn   the most legible of ink, white and near-black on a background
 *   (on several colours: the one whose worst contrast over them is best)
 */

/**
 * The colour roles of one scene: content tokens from the presentation, structure tokens from the scene's look.
 * @param {TimelinePresentation | null | undefined} presentation
 * @param {ResolvedLook | null | undefined} look
 * @returns {Roles}
 */
export function makeRoles(presentation, look) {
  /** @type {Record<string, string>} */
  const content = { ...DARK_CONTENT };
  for (const [k, v] of Object.entries((presentation && presentation.content) || {})) if (HEX.test(String(v))) content[k] = String(v);
  /** @type {Record<string, string>} */
  const tokens = { ...DARK_STRUCT };
  for (const [k, v] of Object.entries((look && look.tokens) || {})) if (HEX.test(String(v))) tokens[k] = String(v);
  const mode = contrastRatio(content.ink, '#000000') > contrastRatio(content.ink, '#FFFFFF') ? 'dark' : 'light';
  // a look without a card surface (B2 may resolve it per video only): derived from the scene's background
  if (!(look && look.tokens && HEX.test(String(look.tokens.surface)))) {
    tokens.surface = mode === 'light' ? mixOklch(tokens.bg, '#FFFFFF', 0.85) : mixOklch(tokens.bg, content.ink, 0.1);
  }
  /** @type {Record<string, string>} */
  const table = {
    ...content,
    surface: tokens.surface,
    'struct.accent': tokens['struct.accent'],
    'struct.tint': tokens['bg-tint'],
    'struct.border': tokens['struct.accent'],
    'struct.pip': tokens['struct.accent'],
  };
  return {
    mode,
    tokens,
    color(role) {
      const v = table[role];
      if (!v || !ROLES.includes(role)) throw new RangeError(`unknown colour role: ${role}`);
      return v;
    },
    inkOn(bg) {
      const bgs = Array.isArray(bg) ? bg : [bg];
      const worst = (/** @type {string} */ c) => Math.min(...bgs.map((b) => contrastRatio(c, b)));
      const options = [content.ink, ...ON_BLOCK];
      let best = options[0];
      for (const c of options) if (worst(c) > worst(best)) best = c;
      return best;
    },
  };
}

/**
 * The background tone of a scene (§4.5): bg, bg-alt or bg-tint.
 * @param {Roles} roles
 * @param {unknown} tone
 */
export function toneColor(roles, tone) {
  const key = tone === 'bg-alt' || tone === 'bg-tint' ? tone : 'bg';
  return roles.tokens[key] || roles.tokens.bg;
}

/**
 * Desaturate a colour by `amount` (0..1): its OKLCH chroma scaled by (1 - amount), lightness and hue kept (the hold
 * freeze of R-55).
 * @param {string} hex
 * @param {number} amount
 */
export function desaturate(hex, amount) {
  if (!(amount > 0)) return hex;
  const c = toOklch(hex);
  return fromOklch({ l: c.l, c: c.c * (1 - Math.min(1, amount)), h: c.h });
}

/**
 * A 2D context for a canvas, or null where the page has none (unit tests under jsdom): callers fall back to DOM.
 * @param {HTMLCanvasElement} canvas
 * @param {CanvasRenderingContext2DSettings} [settings]
 * @returns {CanvasRenderingContext2D | null}
 */
export function canvas2d(canvas, settings) {
  const win = canvas.ownerDocument && canvas.ownerDocument.defaultView;
  if (!win || typeof (/** @type {any} */ (win).CanvasRenderingContext2D) !== 'function') return null;
  try {
    return canvas.getContext('2d', settings);
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------------------------
// Zones (R-59, §2.5)
// ---------------------------------------------------------------------------------------------

/**
 * @typedef {{x0: number, y0: number, x1: number, y1: number}} Box
 * @typedef {object} Zones
 * @property {'landscape' | 'portrait'} format
 * @property {number} width
 * @property {number} height
 * @property {Box[]} critical   boxes critical text must stay inside (any one of them)
 * @property {Box} caption      the caption band
 * @property {Box} header       kicker / pips row
 * @property {Box} title        title or headline slot
 * @property {Box} body         the content stage (cards, media)
 * @property {Box} textColumn   where card text may sit (narrower than shapes in 9:16)
 */

/** @type {Readonly<Record<'landscape' | 'portrait', Zones>>} */
export const ZONES = Object.freeze({
  landscape: Object.freeze({
    format: 'landscape',
    width: 1920,
    height: 1080,
    critical: [{ x0: 96, y0: 54, x1: 1824, y1: 912 }],
    caption: { x0: 96, y0: 924, x1: 1824, y1: 1032 },
    header: { x0: 96, y0: 54, x1: 1824, y1: 102 },
    title: { x0: 96, y0: 112, x1: 1824, y1: 352 },
    body: { x0: 96, y0: 300, x1: 1824, y1: 900 },
    textColumn: { x0: 96, y0: 300, x1: 1824, y1: 900 },
  }),
  portrait: Object.freeze({
    format: 'portrait',
    width: 1080,
    height: 1920,
    critical: [
      { x0: 192, y0: 288, x1: 888, y1: 840 },
      { x0: 120, y0: 840, x1: 780, y1: 1248 },
    ],
    caption: { x0: 120, y0: 1040, x1: 780, y1: 1240 },
    header: { x0: 192, y0: 296, x1: 888, y1: 336 },
    title: { x0: 192, y0: 340, x1: 888, y1: 460 },
    body: { x0: 60, y0: 470, x1: 1020, y1: 1020 },
    // text below y 840 must stay left of the TikTok action rail (x 780): one column for the whole stack
    textColumn: { x0: 192, y0: 470, x1: 780, y1: 1020 },
  }),
});

/**
 * The zones of a timeline's format (portrait when the stage is taller than wide).
 * @param {Timeline} timeline
 * @returns {Zones}
 */
export function zonesFor(timeline) {
  const p = timeline.presentation;
  const portrait = p ? p.format === 'portrait' : Number(timeline.height) > Number(timeline.width);
  return portrait ? ZONES.portrait : ZONES.landscape;
}

/**
 * True when the box lies inside one of the critical boxes (1 px tolerance).
 * @param {{x: number, y: number, w: number, h: number}} r
 * @param {Box[]} boxes
 */
export function insideAny(r, boxes) {
  return boxes.some((b) => r.x >= b.x0 - 1 && r.y >= b.y0 - 1 && r.x + r.w <= b.x1 + 1 && r.y + r.h <= b.y1 + 1);
}
