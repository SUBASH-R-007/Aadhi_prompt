// @ts-check
/**
 * `colour_block` (DYNAMIC_VIDEO_DESIGN §4.5, R1): a solid structure-accent block with a soft static grain, for hooks
 * and chapter bumpers only. Static apart from the hold desaturation (R-55); scenes entering or leaving it always
 * cross-fade (R-54). The grain is a seeded noise tile drawn once per page, so every frame is a pure function of the
 * scene. Its colours are the scene's block roles (core.js makeRoles): an engine_version 2 look's stored `block.base` /
 * `block.highlight` (R-54's red lowering included), else B1's run-time derivation.
 */

import { h } from '../../shared/dom.js';
import { ease } from '../../motion/easing.js';
import { hashSeed, mulberry32 } from '../../motion/random.js';
import { canvas2d, desaturate } from '../core.js';
import { FREEZE_DESATURATION } from './mesh_drift.js';

/** @typedef {import('../core.js').Painter} Painter */
/** @typedef {import('./types.js').BackgroundContext} BackgroundContext */
/** @typedef {import('./types.js').BackgroundView} BackgroundView */

export const GRAIN_SIZE = 48;
/** The tile is shown 8x larger (smoothly scaled, web/css/dynamic.css): a soft mottle of ~8 px cells. A per-pixel
 * grain would not survive the JPEG capture and the H.264 encode, so the MP4 would differ from the live player. */
export const GRAIN_SCALE = 8;
/** Grain alpha (0..255): a texture, never a pattern you notice. */
export const GRAIN_ALPHA = 14;

/** @type {Map<number, string>} */
const grainCache = new Map();

/**
 * A seeded noise tile as a PNG data URL ('' where no canvas is available, e.g. in unit tests).
 * @param {number} seed
 */
export function grainTile(seed) {
  const cached = grainCache.get(seed);
  if (cached !== undefined) return cached;
  let url = '';
  try {
    const canvas = document.createElement('canvas');
    canvas.width = GRAIN_SIZE;
    canvas.height = GRAIN_SIZE;
    const g = canvas2d(canvas);
    if (g) {
      const img = g.createImageData(GRAIN_SIZE, GRAIN_SIZE);
      const rand = mulberry32(hashSeed(seed, 'grain'));
      for (let i = 0; i < img.data.length; i += 4) {
        const v = rand() < 0.5 ? 0 : 255;
        img.data[i] = v;
        img.data[i + 1] = v;
        img.data[i + 2] = v;
        img.data[i + 3] = Math.floor(rand() * GRAIN_ALPHA);
      }
      g.putImageData(img, 0, 0);
      url = canvas.toDataURL('image/png');
    }
  } catch {
    url = '';
  }
  grainCache.set(seed, url);
  return url;
}

/**
 * The block's main colour (the `block.base` role): the stored one, else the accent deepened toward the background tone
 * (dark families) or kept (light ones).
 * @param {import('../core.js').Roles} roles
 */
export function blockColor(roles) {
  return roles.color('block.base');
}

/**
 * Both colours of the block's gradient (the `block.base` and `block.highlight` roles): the main colour and a soft
 * highlight on the same side of the light/dark divide, so one text colour holds its contrast over the whole block
 * (R-53; the `block.ink` role is picked over both).
 * @param {import('../core.js').Roles} roles
 * @returns {{base: string, highlight: string}}
 */
export function blockColors(roles) {
  return { base: roles.color('block.base'), highlight: roles.color('block.highlight') };
}

export default {
  id: 'colour_block',

  /**
   * @param {BackgroundContext} ctx
   * @returns {BackgroundView}
   */
  build(ctx) {
    // a soft highlight (top left) over the block colour, so a saturated accent never reads as a flat flash of colour
    const { base: accent, highlight: light } = blockColors(ctx.roles);
    const grain = grainTile(1 + (ctx.seed % 7)); // a handful of tiles per page, reused across scenes
    const grainEl = h('div', { class: 'dyn-bg-fill dyn-bg-grain' });
    if (grain) {
      grainEl.style.backgroundImage = `url("${grain}")`;
      if (typeof Image !== 'undefined') {
        const probe = new Image();
        probe.src = grain;
        if (typeof probe.decode === 'function') ctx.pending.push(probe.decode().catch(() => undefined));
      }
    }
    const el = h('div', { class: 'dyn-bg-fill dyn-bg--block' }, grainEl);
    return {
      el,
      colors: () => [accent, light],
      /**
       * @param {Painter} paint
       * @param {number} _t
       * @param {number} freeze
       */
      update(paint, _t, freeze) {
        const amount = FREEZE_DESATURATION * ease('ease.move', freeze);
        const a = desaturate(accent, amount);
        const l = desaturate(light, amount);
        paint.style(el, 'background-color', a);
        paint.style(el, 'background-image', `radial-gradient(130% 100% at 16% 10%, ${l} 0%, ${a} 70%)`);
      },
    };
  },
};
