// @ts-check
/**
 * `mesh_drift` (DYNAMIC_VIDEO_DESIGN §4.5, R1): four large radial gradients drifting on Lissajous paths over the
 * scene's tone (bg, bg-alt or bg-tint), painted live as a pure function of the background clock.
 *
 * Low salience (R-55): periods 20-30 s (>= 15 s), every blob slower than 2% of the frame width per second, blob
 * colours within a small lightness step of the tone (behind-text luminance ratio <= 1.5:1) and less saturated than
 * the accent. During a hold the stage pins the clock and this preset desaturates by 30% over 600 ms.
 * Painted on a small canvas (1/8 of the stage, upscaled): a moving full-frame gradient would otherwise re-raster
 * the whole page every frame (measured: about 50 ms of a 90 ms frame at 1080p).
 */

import { h } from '../../shared/dom.js';
import { ease } from '../../motion/easing.js';
import { mixOklch } from '../../motion/color.js';
import { hashSeed, mulberry32 } from '../../motion/random.js';
import { canvas2d, desaturate, fx, toneColor } from '../core.js';

/** @typedef {import('../core.js').Painter} Painter */
/** @typedef {import('./types.js').BackgroundContext} BackgroundContext */
/** @typedef {import('./types.js').BackgroundView} BackgroundView */

export const PERIOD_MIN = 20;
export const PERIOD_MAX = 30;
/** Largest drift amplitude per axis, as a fraction of the frame width (keeps every blob under 2% width/s). */
export const AMPLITUDE_MAX = 0.04;
/** Hold desaturation (R-55). */
export const FREEZE_DESATURATION = 0.3;

/**
 * The drifting blobs of a scene (seeded): centre, amplitude, period and phase per axis.
 * @param {number} seed
 * @param {number} width
 * @param {number} height
 */
export function blobPaths(seed, width, height) {
  const rand = mulberry32(hashSeed(seed, 'mesh_drift'));
  const span = (/** @type {number} */ lo, /** @type {number} */ hi) => lo + (hi - lo) * rand();
  /** @type {{cx: number, cy: number, ax: number, ay: number, px: number, py: number, fx: number, fy: number}[]} */
  const out = [];
  // one blob per quadrant (seeded inside it), so the composition is balanced but never the same twice
  const quads = [[0, 0], [1, 0], [0, 1], [1, 1]];
  for (const [qx, qy] of quads) {
    out.push({
      cx: width * (0.12 + 0.5 * qx + 0.26 * rand()),
      cy: height * (0.12 + 0.5 * qy + 0.26 * rand()),
      ax: width * span(0.02, AMPLITUDE_MAX),
      ay: width * span(0.02, AMPLITUDE_MAX),
      px: span(PERIOD_MIN, PERIOD_MAX),
      py: span(PERIOD_MIN, PERIOD_MAX),
      fx: span(0, Math.PI * 2),
      fy: span(0, Math.PI * 2),
    });
  }
  return out;
}

/**
 * Blob centre at background time `t`.
 * @param {{cx: number, cy: number, ax: number, ay: number, px: number, py: number, fx: number, fy: number}} b
 * @param {number} t
 */
export function blobAt(b, t) {
  return {
    x: b.cx + b.ax * Math.sin((2 * Math.PI * t) / b.px + b.fx),
    y: b.cy + b.ay * Math.sin((2 * Math.PI * t) / b.py + b.fy),
  };
}

/**
 * Radial gradient of one blob: its colour fading to transparent at the edge.
 * @param {string} color  hex
 */
function blobImage(color) {
  return `radial-gradient(closest-side, ${color}d9 0%, ${color}66 48%, ${color}00 100%)`;
}

/**
 * The drift advances in steps of 0.2 s (every 6th frame at 30 fps): at most 2.7 px of a soft gradient whose radius is
 * about 900 px, an invisible step, while 5 of 6 frames repaint nothing behind the text (and share their key).
 */
export const DRIFT_STEP = 0.2;

/** The canvas paints at 1/8 of the stage: the gradients are smooth, so the upscale is invisible and cheap. */
export const CANVAS_SCALE = 8;

export default {
  id: 'mesh_drift',

  /**
   * @param {BackgroundContext} ctx
   * @returns {BackgroundView}
   */
  build(ctx) {
    const { roles, zones } = ctx;
    const base = toneColor(roles, ctx.params && ctx.params.tone);
    const accent = roles.color('struct.accent');
    const ink = roles.color('ink');
    // tone neighbours only: an accent tint (below the accent's saturation), bg-alt, bg-tint and a lift toward ink
    const colors = [mixOklch(base, accent, 0.2), roles.tokens['bg-alt'] || base, roles.tokens['bg-tint'] || base, mixOklch(base, ink, 0.07)];
    const size = Math.round(Math.max(zones.width, zones.height) * 0.95);
    const paths = blobPaths(ctx.seed, zones.width, zones.height);
    const k = CANVAS_SCALE;
    const canvas = h('canvas', { class: 'dyn-bg-fill dyn-bg-canvas', width: Math.round(zones.width / k), height: Math.round(zones.height / k) });
    const g = canvas2d(canvas, { alpha: false });
    // Without a canvas (unit tests) one element per blob, moved by transforms; same picture, slower to paint.
    const blobs = g ? [] : paths.map(() => h('div', { class: 'dyn-blob', style: { width: size, height: size } }));
    const el = h('div', { class: 'dyn-bg-fill dyn-bg--mesh' }, g ? canvas : blobs);
    let drawn = '';
    return {
      el,
      colors: () => [base, ...colors],
      /**
       * @param {Painter} paint
       * @param {number} t      background clock (seconds)
       * @param {number} freeze hold progress 0..1
       */
      update(paint, t, freeze) {
        const amount = FREEZE_DESATURATION * ease('ease.move', freeze);
        const fill = desaturate(base, amount);
        const tints = colors.map((c) => desaturate(c, amount));
        const tq = Math.floor(t / DRIFT_STEP + 1e-9) * DRIFT_STEP;
        const at = paths.map((p) => blobAt(p, tq));
        if (g) {
          // the picture is a pure function of these numbers (noted in the frame key); redrawn only when they change
          const state = `${fill}|${tints.join(',')}|${at.map((p) => `${fx(p.x, 2)},${fx(p.y, 2)}`).join(';')}`;
          paint.note(`mesh:${state}`);
          if (state === drawn) return;
          drawn = state;
          g.globalAlpha = 1;
          g.fillStyle = fill;
          g.fillRect(0, 0, canvas.width, canvas.height);
          at.forEach((p, i) => {
            const x = p.x / k;
            const y = p.y / k;
            const r = size / 2 / k;
            const grad = g.createRadialGradient(x, y, 0, x, y, r);
            grad.addColorStop(0, `${tints[i]}d9`);
            grad.addColorStop(0.48, `${tints[i]}66`);
            grad.addColorStop(1, `${tints[i]}00`);
            g.fillStyle = grad;
            g.fillRect(0, 0, canvas.width, canvas.height);
          });
          return;
        }
        paint.style(el, 'background-color', fill);
        blobs.forEach((blob, i) => {
          paint.style(blob, 'background-image', blobImage(tints[i]));
          paint.style(blob, 'transform', `translate3d(${fx(at[i].x - size / 2, 2)}px, ${fx(at[i].y - size / 2, 2)}px, 0)`);
        });
      },
    };
  },
};
