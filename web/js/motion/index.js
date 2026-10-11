// @ts-check
/**
 * The motion toolkit of the Dynamic stage (design §7.13: in-house, pure functions, no clock, no DOM at
 * import time). Templates, backgrounds, transitions and captions import from here.
 */

export { ease, easingFn, cubicBezier, cssEasing, EASINGS, BEZIER_CURVES, POP_OVERSHOOT } from './easing.js';
export { track, interpolate, progress, stagger } from './track.js';
export {
  parseColor,
  toOklch,
  fromOklch,
  toHex,
  mixOklch,
  mixOklchValues,
  composite,
  gamutMapOklch,
  linearSrgbToOklab,
  oklabToLinearSrgb,
  relativeLuminance,
  contrastRatio,
  ACHROMATIC_CHROMA,
} from './color.js';
export { segmentGraphemes, splitWords, splitLines, joinIndicConjuncts } from './text.js';
export { strokeDash, strokeSegment, staggerProgress } from './draw.js';
export { mulberry32, hashSeed, seededRandom, randomRange, randomInt, pick, shuffle } from './random.js';
export { countUp, countUpValue } from './count.js';
