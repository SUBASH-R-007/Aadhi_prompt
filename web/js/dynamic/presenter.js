// @ts-check
/**
 * Aadhi on the Dynamic stage (DYNAMIC_VIDEO_DESIGN §3.2-§3.4, §4.3 `aadhi_intro` / `aadhi_recap`; B2 plan §1.2
 * `ResolvedLook.presenter`, §1.12 the crop, §2 "Aadhi"): what the stage needs to draw the presenter of a scene, as pure
 * functions of the scene's look and the scene time, and the placements the two presenter templates lay their text
 * around. The Python side is `aadhi/compose/dynamic/presenter.py`; `web/tests/dynamic/presenter.test.js` and
 * `tests/compose/dynamic/test_presenter.py` pin the two to the same numbers.
 *
 * The presenter is a MEDIUM (§7.7), drawn by the stage's Slot below the template's content: in render mode an `<img>`
 * of the worker's pre-extracted frames of the clip's crop at the presenter's box size (`presenterKey`: the frames'
 * name carries the crop, plan §1.12), so a frame is drawn 1:1 and never scaled or pushed (B2c deviation 28: a clip
 * under a scale or push was measured order-dependent); live, a muted `<video>` sized so the crop fills the box
 * (`liveVideoBox`). Its motion is whole-pixel translation and opacity only, a pure function of t (`presenterPaint`):
 * the 9:16 intro card settles by a 16 px rise (§3.3's 0.96 -> 1 scale replaced, see SETTLE_RISE), slides down at its
 * exit (§3.3 "ease.exit, 300 ms"), the recap card fades out (300 ms), and under the 16:9 intro's brand wipe the
 * presenter stays until the block has covered him (the template draws the wipe, `wipeProgress`).
 *
 * A look without a presenter, or with a malformed one, has none (`presenterSpec` returns null): nothing is drawn and
 * no frame is planned, exactly as before this module. A presenter without `crop` / `exit_s` / `enter_kind` (the form
 * the stage read before) keeps that behaviour value for value: the same frame key, shown until `t_exit`, unmoved.
 */

import { ease } from '../motion/easing.js';
import { progress } from '../motion/track.js';
import { Hasher } from './core.js';

/** @typedef {import('../shared/types.js').TimedScene} TimedScene */
/** @typedef {{x: number, y: number, w: number, h: number}} Rect */

// ---------------------------------------------------------------------------------------------
// Clips and placements (§3.2, §3.3; presenter.py CLIPS, LANDSCAPE_PLACEMENTS, reel_placement)
// ---------------------------------------------------------------------------------------------

/** The clips' format (§3.2: 1280x720, 24 fps, exactly 8.000 s = 192 frames). */
export const CLIP_SIZE = Object.freeze([1280, 720]);
export const CLIP_FPS = 24;
export const CLIP_FRAMES = 192;

/** The four 16:9 intro layouts (§3.3; templates.py variants, in that order) and the 9:16 card. */
export const INTRO_LAYOUTS = Object.freeze(['fullframe', 'card_left', 'pip', 'split']);
export const REEL_LAYOUT = 'card';
/** The recap's layout per format (§3.3: the 16:9 recap is card_left's 720x720 card). */
export const RECAP_LAYOUT = Object.freeze({ landscape: 'card_left', portrait: REEL_LAYOUT });
/** The 9:16 card: 744x744 at y 800-1544, at one of three seeded x positions (§3.3). */
export const REEL_CARD_XS = Object.freeze([168, 96, 240]);
export const REEL_CARD = Object.freeze({ size: 744, y: 800 });
/** The centre clip's square crop, centred on Aadhi (source px). */
export const REEL_CROP = Object.freeze([280, 0, 720, 720]);
/** The rounded presenter card's radius (catalogue layout `radius.presenter_card`). */
export const CARD_RADIUS = 40;

/**
 * Where each 16:9 layout draws the presenter: `rect` (stage px), `crop` (source px, the same aspect) and `radius`.
 * @type {Readonly<Record<string, {rect: readonly number[], crop: readonly number[], radius: number}>>}
 */
export const LANDSCAPE_PLACEMENTS = Object.freeze({
  fullframe: Object.freeze({ rect: Object.freeze([0, 0, 1920, 1080]), crop: Object.freeze([0, 0, 1280, 720]), radius: 0 }),
  card_left: Object.freeze({ rect: Object.freeze([96, 180, 720, 720]), crop: Object.freeze([0, 0, 720, 720]), radius: CARD_RADIUS }),
  pip: Object.freeze({ rect: Object.freeze([96, 432, 480, 480]), crop: Object.freeze([0, 0, 720, 720]), radius: CARD_RADIUS }),
  split: Object.freeze({ rect: Object.freeze([0, 0, 864, 1080]), crop: Object.freeze([0, 0, 576, 720]), radius: 0 }),
});

/**
 * The placement of `layout` in `format` (9:16: the card at `cardX`, default the first position).
 * @param {'landscape' | 'portrait'} format @param {string} layout @param {number} [cardX]
 * @returns {{rect: number[], crop: number[], radius: number}}
 */
export function placement(format, layout, cardX) {
  if (format === 'portrait') {
    const x = cardX === undefined ? REEL_CARD_XS[0] : cardX;
    if (layout !== REEL_LAYOUT || !REEL_CARD_XS.includes(x)) throw new RangeError(`no 9:16 presenter ${layout} at ${x}`);
    return { rect: [x, REEL_CARD.y, REEL_CARD.size, REEL_CARD.size], crop: [...REEL_CROP], radius: CARD_RADIUS };
  }
  const p = LANDSCAPE_PLACEMENTS[layout];
  if (!p) throw new RangeError(`no 16:9 presenter layout ${layout}`);
  return { rect: [...p.rect], crop: [...p.crop], radius: p.radius };
}

// ---------------------------------------------------------------------------------------------
// Motion (§3.3; presenter.py exit_of / enter_of)
// ---------------------------------------------------------------------------------------------

/** The 16:9 intro's brand wipe: a block in the next scene's accent sweeps across over 600 ms (`ease.hero`). */
export const WIPE_SECONDS = 0.6;
/** The 9:16 intro card slides down over 300 ms (`ease.exit`); the recap card fades over 300 ms. */
export const SLIDE_SECONDS = 0.3;
export const FADE_SECONDS = 0.3;
/**
 * The 9:16 card's settle (§3.3: "settles from 0.96 to 1.0 over 400 ms"): a clip is never scaled (B2c deviation 28),
 * so the card rises SETTLE_RISE px into place over the same 400 ms instead, on whole pixels. Frame 0 shows it whole
 * (the reel's cover), 16 px low.
 */
export const SETTLE_SECONDS = 0.4;
export const SETTLE_RISE = 16;
/** The exit and entrance kinds a stored presenter may name (anything else: none). */
export const EXIT_KINDS = Object.freeze(['wipe', 'slide_down', 'fade']);
export const ENTER_KINDS = Object.freeze(['none', 'rise']);

/**
 * Even integer, at least 2 (4:2:0 frame sizes; media.js `even`).
 * @param {number} v
 */
function even(v) {
  return Math.max(2, 2 * Math.round(v / 2));
}

/**
 * Four finite numbers (a rect or crop) or null.
 * @param {unknown} v
 * @returns {number[] | null}
 */
function quad(v) {
  if (!Array.isArray(v) || v.length !== 4) return null;
  const n = v.map(Number);
  return n.every(Number.isFinite) ? n : null;
}

/**
 * The frame key of a presenter clip: path-safe and stable. Without a crop it is media.js `mediaKey('aadhi', ...)`
 * exactly (`a-<hash(clip)>-WxH`, the key the stage used before the crop); with one, the crop is hashed in
 * (`a-<hash(clip|x,y,w,h)>-WxH`, plan §1.12), so frames of two crops never share a name.
 * @param {string} clipKey @param {number[] | null} crop @param {[number, number]} size
 */
export function presenterKey(clipKey, crop, size) {
  const h = new Hasher().add(clipKey);
  if (crop) h.add(crop.join(','));
  return `a-${h.digest()}-${size[0]}x${size[1]}`;
}

/**
 * @typedef {object} PresenterSpec
 * @property {string} clip_key      the branding clip (a file name the worker resolves)
 * @property {Rect} rect            stage px (the presenter's box)
 * @property {number[] | null} crop source px [x, y, w, h], or null (the whole clip, centred `cover`)
 * @property {number | null} radius  corner radius px (null: not stored, the stylesheet's)
 * @property {number} t_exit        the exit starts (scene seconds)
 * @property {string} exit_kind     'wipe' | 'slide_down' | 'fade' | 'none'
 * @property {number} exit_s        the exit's length (0: hidden at t_exit, the stage's rule before the exit kinds)
 * @property {number} t_hide        the presenter is gone from here (t_exit + exit_s)
 * @property {string} enter_kind    'rise' | 'none'
 * @property {number} src_fps
 * @property {[number, number]} size   the frames' size (the box, even px)
 * @property {string} key           their name (presenterKey)
 * @property {string | null} url    the clip's URL (live)
 * @property {string} layout        the stored layout ('' when none)
 */

/**
 * The presenter of a scene's look (`ResolvedLook.presenter`), normalised; null without a valid one. A stored crop is
 * kept only when it lies inside the clip with a positive size (else the clip is shown whole, as before the crop).
 * @param {TimedScene} scene
 * @returns {PresenterSpec | null}
 */
export function presenterSpec(scene) {
  const p = /** @type {any} */ (scene && scene.look && scene.look.presenter);
  if (!p || typeof p.clip_key !== 'string' || !p.clip_key) return null;
  const r = quad(p.rect);
  if (!r || r[2] <= 0 || r[3] <= 0) return null;
  const c = quad(p.crop);
  const crop = c && c[0] >= 0 && c[1] >= 0 && c[2] >= 2 && c[3] >= 2 ? c.map(Math.round) : null;
  const tExit = Number(p.t_exit);
  const t_exit = Number.isFinite(tExit) ? tExit : Number(scene.duration);
  const exitKind = EXIT_KINDS.includes(String(p.exit_kind)) ? String(p.exit_kind) : 'none';
  const exitS = Number(p.exit_s);
  const exit_s = exitKind !== 'none' && Number.isFinite(exitS) && exitS > 0 ? exitS : 0;
  /** @type {[number, number]} */
  const size = [even(r[2]), even(r[3])];
  const radius = Number(p.radius);
  return {
    clip_key: p.clip_key,
    rect: { x: r[0], y: r[1], w: r[2], h: r[3] },
    crop,
    radius: p.radius !== null && p.radius !== undefined && Number.isFinite(radius) && radius >= 0 ? radius : null,
    t_exit,
    exit_kind: exit_s > 0 ? exitKind : 'none',
    exit_s,
    t_hide: t_exit + exit_s,
    enter_kind: ENTER_KINDS.includes(String(p.enter_kind)) ? String(p.enter_kind) : 'none',
    src_fps: Number(p.src_fps) > 0 ? Number(p.src_fps) : CLIP_FPS,
    size,
    key: presenterKey(p.clip_key, crop, size),
    url: typeof p.url === 'string' ? p.url : null,
    layout: typeof p.layout === 'string' ? p.layout : '',
  };
}

/**
 * The source frame on screen at scene time `t` (§3.2: a 24 fps clip in a 30 fps video shows frame
 * `floor(t * 24)`, a 1-1-1-2 cadence; the clip is as long as the longest presenter scene, so it never loops).
 * @param {PresenterSpec} spec @param {number} t
 */
export function presenterFrame(spec, t) {
  return Math.max(0, Math.floor(Math.max(0, t) * spec.src_fps + 1e-6));
}

/**
 * Whether the presenter is on screen at scene time `t` (until the end of his exit).
 * @param {PresenterSpec} spec @param {number} t
 */
export function presenterShown(spec, t) {
  return t < spec.t_hide;
}

/**
 * How the presenter is painted at scene time `t`: `visible`, a whole-pixel `dy` (the settle and the slide) and an
 * `opacity` (the fade). A pure function of t; under reduced motion (live only) the settle and the slide are dropped
 * (the card is simply there, and leaves at the end of its exit) and the fade stays a fade.
 * @param {PresenterSpec} spec @param {number} t @param {{reducedMotion?: boolean, stageHeight?: number}} [opts]
 * @returns {{visible: boolean, dy: number, opacity: number}}
 */
export function presenterPaint(spec, t, opts = {}) {
  const visible = presenterShown(spec, t);
  const still = !!opts.reducedMotion;
  let dy = 0;
  let opacity = 1;
  if (visible && spec.enter_kind === 'rise' && !still) {
    dy = Math.round((1 - ease('ease.enter', progress(t, 0, SETTLE_SECONDS))) * SETTLE_RISE);
  }
  if (visible && spec.exit_s > 0) {
    const u = progress(t, spec.t_exit, spec.exit_s);
    if (spec.exit_kind === 'slide_down' && !still) {
      const travel = Math.max(0, (opts.stageHeight || 1920) - spec.rect.y);
      dy += Math.round(ease('ease.exit', u) * travel);
    } else if (spec.exit_kind === 'fade') {
      opacity = 1 - ease('ease.exit', u);
    }
  }
  return { visible, dy, opacity: Math.round(opacity * 1000) / 1000 };
}

/**
 * Live mode: where the `<video>` of the whole `srcW x srcH` clip sits inside the presenter's box so its crop fills the
 * box exactly (the box clips the rest: `.dyn-presenter` has `overflow: hidden`). Null without a crop (the element
 * covers the box, as before). Whole px.
 * @param {PresenterSpec} spec @param {number} [srcW] @param {number} [srcH]
 * @returns {{left: number, top: number, width: number, height: number} | null}
 */
export function liveVideoBox(spec, srcW = CLIP_SIZE[0], srcH = CLIP_SIZE[1]) {
  if (!spec.crop) return null;
  const [x, y, w, h] = spec.crop;
  const kx = spec.rect.w / w;
  const ky = spec.rect.h / h;
  // `0 - n`, never `-n`: a crop at the clip's edge gives 0, not -0 (a style value of '-0px' would be one more key)
  return { left: 0 - Math.round(x * kx), top: 0 - Math.round(y * ky), width: Math.round(srcW * kx), height: Math.round(srcH * ky) };
}

/**
 * The brand wipe's progress at scene time `t` (0 before `tExit`, 1 from `tExit + seconds`), `ease.hero` (§3.3).
 * @param {number} t @param {number} tExit @param {number} [seconds]
 */
export function wipeProgress(t, tExit, seconds = WIPE_SECONDS) {
  return ease('ease.hero', progress(t, tExit, seconds));
}

// ---------------------------------------------------------------------------------------------
// The studio wall (§3.3; presenter.py WALL, WALL_TITLE_BOX, wall_inks)
// ---------------------------------------------------------------------------------------------

/** The `fullframe` title stack's box: on the measured studio wall (x 860-1824, y 54-855). */
export const WALL_TITLE_BOX = Object.freeze({ x0: 860, y0: 54, x1: 1824, y1: 855 });

const HEX = /^#[0-9a-f]{6}$/i;

/**
 * The text colours on the studio wall a look stores (`presenter.wall_ink` / `wall_ink2` over `wall`, the darkest
 * colour measured in the title region), or null when the look has none.
 * @param {any} look
 * @returns {{ink: string, ink2: string, bg: string} | null}
 */
export function wallInks(look) {
  const p = look && look.presenter;
  if (!p || !HEX.test(String(p.wall_ink)) || !HEX.test(String(p.wall_ink2)) || !HEX.test(String(p.wall))) return null;
  return { ink: String(p.wall_ink), ink2: String(p.wall_ink2), bg: String(p.wall) };
}

/**
 * The colour the 16:9 brand wipe sweeps in: the next scene's block colour (`look.refs.accent_next`, R-54), else this
 * scene's own `block.base` (`fallback`).
 * @param {any} look @param {string} fallback
 */
export function wipeColour(look, fallback) {
  const c = look && look.refs && look.refs.accent_next;
  return HEX.test(String(c)) ? String(c) : fallback;
}
