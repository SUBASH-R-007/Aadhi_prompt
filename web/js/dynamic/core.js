// @ts-check
/**
 * Shared pieces of the Dynamic stage (DYNAMIC_VIDEO_DESIGN §7.3, §7.4): frame math that matches the motion
 * engine's chunk planner, the painter every per-frame DOM write goes through (it also hashes the frame's visual
 * inputs for `seek()`'s key), the colour roles a template may use (§4.2, B2 plan §1.9), preflight's remedies as the
 * stage reads them (§7.12, plan §1.2), and the safe zones and type floors of both formats (R-59, R-29).
 *
 * The one reader of the generated catalogue (web/js/dynamic/catalogue.gen.js, plan §1.1): the stage's zones, its role
 * names, the R-29 floors and the template slots come from it. Its runtime use is GEOMETRY ONLY: colours, durations and
 * personality values reach the stage through the timeline alone (presentation.content, look.tokens, ...), so a stored
 * timeline keeps its look when the catalogue changes (the source scan keeps every other module from importing it, and
 * allows only reads of its zones, regions, grid, type floors and scale, layout tokens, caption-style metrics, template
 * slots and role names: never its families, content palettes, cue colours, personalities, caption colours or
 * durations).
 *
 * Pure: no DOM access at import time and no clock. Everything a frame shows is a function of (timeline, t).
 */

import { contrastRatio, fromOklch, mixOklch, toOklch } from '../motion/color.js';
import { CATALOGUE } from './catalogue.gen.js';

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
// Colour roles (§4.2, §4.7; B2 plan §1.2, §1.9)
// ---------------------------------------------------------------------------------------------

/** The closed list of roles a template may ask for (plan §1.9; the catalogue's `roles.colour`, themes.py's order). */
export const ROLES = CATALOGUE.roles.colour;

/**
 * The block roles (plan §1.2, §1.9; the catalogue's `roles.block`): a colour block's base and highlight, the ink and
 * the quieter ink drawn on it, and the title block's scrim (#RRGGBBAA). An engine_version 2 look stores each of them
 * (`look.tokens`, computed by aadhi/compose/dynamic/themes.py); the stage derives one a look lacks as B1 did.
 */
export const BLOCK_ROLES = CATALOGUE.roles.block;

/** The content roles: the presentation's palette (the rest of ROLES are structure: the scene's look). */
const CONTENT_ROLES = Object.freeze(['ink', 'ink2', 'entity:0', 'entity:1', 'entity:2', 'warn', 'ok', 'mark', 'focus']);

/** Fallback content tokens (dark mode, §4.7) for a timeline that carries none. */
const DARK_CONTENT = Object.freeze({
  ink: '#F3F5F9', ink2: '#B9BEC7', 'entity:0': '#0FE3FE', 'entity:1': '#C4A4FE', 'entity:2': '#FEACE3',
  warn: '#F8A87F', ok: '#5DD8AA', mark: '#F2D935', focus: '#F2D935',
});
const DARK_STRUCT = Object.freeze({
  'struct.accent': '#FF936D', bg: '#10132C', 'bg-alt': '#181D37', 'bg-tint': '#22121C', surface: '#252A3F',
});
const HEX = /^#[0-9a-f]{6}$/i;
/** A colour with its alpha (the stored scrim, `#RRGGBBC7`). */
const HEX_ALPHA = /^#[0-9a-f]{8}$/i;

/** Text colours offered for text drawn straight on a saturated block (bumpers, colour blocks). */
const ON_BLOCK = Object.freeze(['#FFFFFF', '#14161C']);

/** Large text on a colour block (the chapter subtitle, the kicker) keeps at least this contrast (R-53: 3:1 large). */
export const BLOCK_INK2_CONTRAST = 3;

/** The title block's scrim (R-53 remedy): the scene's background tone at this alpha (0xc7, 78%; R-53: >= 55%). */
export const SCRIM_ALPHA_HEX = 'c7';

/**
 * The quieter ink on a colour block: the strongest mix of `ink` toward the block (at most 25%, R-52's step back) that
 * still holds 3:1 against every block colour, else `ink` itself (the ink already has only ~4:1 on a saturated block).
 * @param {string} ink
 * @param {string[]} bgs  the block's colours
 */
export function blockInk2(ink, bgs) {
  for (const p of [0.25, 0.2, 0.15, 0.1, 0.05]) {
    const c = mixOklch(ink, bgs[0], p);
    if (Math.min(...bgs.map((b) => contrastRatio(c, b))) >= BLOCK_INK2_CONTRAST) return c;
  }
  return ink;
}

/**
 * @typedef {object} Roles
 * @property {(role: string) => string} color   hex of a role: one of ROLES or BLOCK_ROLES (anything else throws, so a
 *   typo fails in tests)
 * @property {'dark' | 'light'} mode
 * @property {Record<string, string>} tokens   the scene's structure tokens (bg, bg-alt, bg-tint, struct.accent,
 *   surface; from engine_version 2 also the stored block colours, #RRGGBB)
 * @property {(bg: string | string[]) => string} inkOn   the most legible of ink, white and near-black on a background
 *   (on several colours: the one whose worst contrast over them is best)
 * @property {(mode: 'dark' | 'light') => Roles} on   the roles of text drawn on a surface of `mode` (plan §1.9): these
 *   roles for the scene's own mode; for the other one, the content roles of `presentation.content_alt` (a light
 *   family's dark code editor) with the scene's structure roles. Throws when the timeline carries no complete palette
 *   of that mode.
 */

/**
 * The colour roles of one scene: content tokens from the presentation, structure tokens from the scene's look.
 *
 * Block roles, the scrim and the card surface are the look's stored tokens when it has them (engine_version 2, plan
 * §1.2: themes.py computed them, R-54's red lowering and the hold-safe block.ink2 included), each one on its own: the
 * stage feature-detects every field and never branches on the engine version. A look without one (a B1 look) gets the
 * value the B1 stage derived at run time, by the same maths: the block is the accent deepened 38% toward the
 * background in dark families (the accent itself in light ones) with a highlight 35% back toward the accent (10% toward
 * white); block.ink is `inkOn` over both; block.ink2 is `blockInk2` of that ink; the scrim is the background tone at
 * SCRIM_ALPHA_HEX; the surface the background mixed 10% toward the ink (85% toward white in light families).
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
  const mode = modeOfInk(content.ink);
  // a look without a card surface (B2 may resolve it per video only): derived from the scene's background
  if (!(look && look.tokens && HEX.test(String(look.tokens.surface)))) {
    tokens.surface = mode === 'light' ? mixOklch(tokens.bg, '#FFFFFF', 0.85) : mixOklch(tokens.bg, content.ink, 0.1);
  }
  const storedScrim = look && look.tokens ? String(look.tokens.scrim || '') : '';
  /** @type {Record<string, string>} */
  const structure = {
    surface: tokens.surface,
    'struct.accent': tokens['struct.accent'],
    'struct.tint': tokens['bg-tint'],
    'struct.border': tokens['struct.accent'],
    'struct.pip': tokens['struct.accent'],
  };
  const inkOn = inkChooser(content.ink);
  /** @type {Record<string, string> | null} block roles, computed on first use (most scenes have no block) */
  let block = null;
  const blockRoles = () => {
    if (block) return block;
    const accent = tokens['struct.accent'];
    const base = tokens['block.base'] || (mode === 'dark' ? mixOklch(accent, tokens.bg || '#000000', 0.38) : accent);
    const highlight = tokens['block.highlight']
      || (mode === 'dark' ? mixOklch(base, accent, 0.35) : mixOklch(base, '#FFFFFF', 0.1));
    const ink = tokens['block.ink'] || inkOn([base, highlight]);
    const ink2 = tokens['block.ink2'] || blockInk2(ink, [base, highlight]);
    const scrim = HEX_ALPHA.test(storedScrim) ? storedScrim : `${tokens.bg || '#000000'}${SCRIM_ALPHA_HEX}`;
    block = { 'block.base': base, 'block.highlight': highlight, 'block.ink': ink, 'block.ink2': ink2, scrim };
    return block;
  };
  /** @type {Partial<Record<'dark' | 'light', Roles>>} */
  const others = {};
  /** @type {Roles} */
  const roles = rolesOf(content, mode, tokens, structure, blockRoles, inkOn, (m) => {
    if (m === mode) return roles;
    if (!others[m]) others[m] = alternateRoles(presentation, m, tokens, structure, blockRoles, roles);
    return /** @type {Roles} */ (others[m]);
  });
  return roles;
}

/**
 * The mode a content ink belongs to: dark families draw light ink.
 * @param {string} ink
 * @returns {'dark' | 'light'}
 */
function modeOfInk(ink) {
  return contrastRatio(ink, '#000000') > contrastRatio(ink, '#FFFFFF') ? 'dark' : 'light';
}

/**
 * `inkOn` for a palette whose ink is `ink`.
 * @param {string} ink
 * @returns {(bg: string | string[]) => string}
 */
function inkChooser(ink) {
  return (bg) => {
    const bgs = Array.isArray(bg) ? bg : [bg];
    const worst = (/** @type {string} */ c) => Math.min(...bgs.map((b) => contrastRatio(c, b)));
    const options = [ink, ...ON_BLOCK];
    let best = options[0];
    for (const c of options) if (worst(c) > worst(best)) best = c;
    return best;
  };
}

/**
 * A Roles object over a content palette and the scene's structure.
 * @param {Record<string, string>} content
 * @param {'dark' | 'light'} mode
 * @param {Record<string, string>} tokens
 * @param {Record<string, string>} structure
 * @param {() => Record<string, string>} blockRoles
 * @param {(bg: string | string[]) => string} inkOn
 * @param {(mode: 'dark' | 'light') => Roles} on
 * @returns {Roles}
 */
function rolesOf(content, mode, tokens, structure, blockRoles, inkOn, on) {
  /** @type {Record<string, string>} */
  const table = { ...content, ...structure };
  return {
    mode,
    tokens,
    color(role) {
      if (BLOCK_ROLES.includes(role)) return blockRoles()[role];
      const v = table[role];
      if (!v || !ROLES.includes(role)) throw new RangeError(`unknown colour role: ${role}`);
      return v;
    },
    inkOn,
    on(m) {
      if (m !== 'dark' && m !== 'light') throw new RangeError(`unknown colour mode: ${String(m)}`);
      return on(m);
    },
  };
}

/**
 * The roles of text on a surface of the scene's other mode (plan §1.3 `content_alt`): that mode's content palette, the
 * scene's structure. Throws when the timeline has no complete palette of that mode (a B1 timeline has none; no B1
 * template asks for one).
 * @param {TimelinePresentation | null | undefined} presentation
 * @param {'dark' | 'light'} mode
 * @param {Record<string, string>} tokens
 * @param {Record<string, string>} structure
 * @param {() => Record<string, string>} blockRoles
 * @param {Roles} own   the scene's own roles (what `on` gives for the scene's mode)
 * @returns {Roles}
 */
function alternateRoles(presentation, mode, tokens, structure, blockRoles, own) {
  const p = /** @type {any} */ (presentation);
  const alt = /** @type {Record<string, unknown>} */ ((p && p.content_alt) || {});
  /** @type {Record<string, string>} */
  const content = {};
  for (const role of CONTENT_ROLES) if (HEX.test(String(alt[role]))) content[role] = String(alt[role]);
  if (CONTENT_ROLES.some((role) => !content[role]) || modeOfInk(content.ink) !== mode) {
    throw new RangeError(`the timeline has no ${mode} content palette (presentation.content_alt)`);
  }
  const on = (/** @type {'dark' | 'light'} */ m) => (m === mode ? roles : own);
  /** @type {Roles} */
  const roles = rolesOf(content, mode, tokens, structure, blockRoles, inkChooser(content.ink), on);
  return roles;
}

/**
 * Preflight's remedies of one scene as the stage reads them (§7.12): the fixes channel first (`look.fixes`, plan
 * §1.2, deviation 2), B1's legacy fields second (`look.variant === "safe"`, `look.text_fit[slot]`), so an
 * engine_version 1 timeline remedied before B2a keeps its remedies. A slot's size cap is the fixes channel's when it
 * is a positive number, else the legacy one's (else none); a slot's scrim and the safe layout hold when either channel
 * asks for them (tests/compose/dynamic/stub_stage/stage.js mirrors this rule for the preflight tests).
 * @typedef {object} Fixes
 * @property {boolean} safe   the template's compact, zone-safe layout (R-59)
 * @property {Record<string, {size: number | null, scrim: boolean}>} slots   text slot -> its remedies
 */

/**
 * @param {ResolvedLook | null | undefined} look
 * @returns {Fixes}
 */
export function fixesOf(look) {
  const l = /** @type {any} */ (look);
  const fixes = l && l.fixes && typeof l.fixes === 'object' ? l.fixes : {};
  const fixed = fixes.slots && typeof fixes.slots === 'object' ? fixes.slots : {};
  const legacy = l && l.text_fit && typeof l.text_fit === 'object' ? l.text_fit : {};
  /** @param {unknown} s */
  const size = (s) => {
    const v = s && typeof s === 'object' ? Number(/** @type {any} */ (s).size) : NaN;
    return Number.isFinite(v) && v > 0 ? v : null;
  };
  /** @param {unknown} s */
  const scrim = (s) => !!(s && typeof s === 'object' && /** @type {any} */ (s).scrim === true);
  /** @type {Record<string, {size: number | null, scrim: boolean}>} */
  const slots = {};
  for (const slot of new Set([...Object.keys(fixed), ...Object.keys(legacy)])) {
    const own = size(fixed[slot]);
    slots[slot] = { size: own !== null ? own : size(legacy[slot]), scrim: scrim(fixed[slot]) || scrim(legacy[slot]) };
  }
  return { safe: fixes.safe === true || (!!l && l.variant === 'safe'), slots };
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
// Zones (R-59, §2.5) and type floors (R-29): the catalogue's geometry
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
 * @property {Box} textColumn   where card text may sit (narrower than shapes in 9:16: in 9:16, text below y 840 stays
 *                              left of the TikTok action rail at x 780, one column for the whole stack)
 */

/**
 * A box of the catalogue as the stage's own frozen copy, in B1's key order (x0, y0, x1, y1).
 * @param {Box} b
 * @returns {Box}
 */
function box(b) {
  return Object.freeze({ x0: b.x0, y0: b.y0, x1: b.x1, y1: b.y1 });
}

/**
 * The zones of a format: aadhi/compose/dynamic/zones.py (FormatZones.as_stage_dict) through the catalogue, in B1's
 * key order (web/tests/dynamic/catalogue.test.js pins them against B1's literals, value for value and in order).
 * @param {'landscape' | 'portrait'} format
 * @returns {Zones}
 */
function catalogueZones(format) {
  const z = CATALOGUE.zones[format];
  return Object.freeze({
    format,
    width: z.width,
    height: z.height,
    critical: /** @type {Box[]} */ (Object.freeze(z.critical.map(box))),
    caption: box(z.caption),
    header: box(z.header),
    title: box(z.title),
    body: box(z.body),
    textColumn: box(z.textColumn),
  });
}

/** @type {Readonly<Record<'landscape' | 'portrait', Zones>>} */
export const ZONES = Object.freeze({ landscape: catalogueZones('landscape'), portrait: catalogueZones('portrait') });

/**
 * R-29's type floors of a format (zones.py TypeFloors through the catalogue): text never below `text_min` px, labels
 * never below `label_min`; ranges as [min, max] px (null where R-29 sets none); in 9:16 text at <= `heavy_max_px` px is
 * at least `heavy_min_weight`.
 * @typedef {object} TypeFloors
 * @property {number} text_min
 * @property {number} label_min
 * @property {number[]} body
 * @property {number} body_default
 * @property {number[] | null} heading
 * @property {number[] | null} display
 * @property {number[] | null} hook
 * @property {number[]} caption
 * @property {number} caption_min
 * @property {number | null} heavy_max_px
 * @property {number | null} heavy_min_weight
 */

/** @type {Readonly<Record<'landscape' | 'portrait', TypeFloors>>} */
export const TYPE_FLOORS = CATALOGUE.type.floors;

/**
 * One text slot of a template in one format and variant (aadhi/compose/dynamic/templates.py SlotSpec): box width,
 * line count, size range [min, max] px, role, face role, weight, line height, tracking and copy limits.
 * @typedef {object} SlotSpec
 * @property {number | null} w
 * @property {number | null} max_lines
 * @property {number[]} size
 * @property {string} role
 * @property {string} face
 * @property {number} weight
 * @property {number} line_height
 * @property {number} tracking_em
 * @property {number | null} max_words
 * @property {number | null} max_chars
 * @property {number | null} prefer_lines
 * @property {boolean} critical
 */

/**
 * A template's text slots from the catalogue's registry (slot -> format -> variant -> SlotSpec), or null for an id it
 * does not list. Geometry only, as every runtime read of the catalogue.
 * @param {string} id
 * @returns {Readonly<Record<string, Record<string, Record<string, SlotSpec>>>> | null}
 */
export function templateSlots(id) {
  // two reads the source scan can follow: which ids the registry has, then that id's slots (never its durations)
  if (!Object.prototype.hasOwnProperty.call(CATALOGUE.templates, id)) return null;
  return /** @type {Record<string, {slots: any}>} */ (CATALOGUE.templates)[id].slots;
}

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
