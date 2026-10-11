// @ts-check
/**
 * Count-ups for statistics (design §4.8: 800–1200 ms, linear by default; tabular numerals are the
 * stylesheet's job). The displayed number is a pure function of progress and is formatted with
 * `Intl.NumberFormat`, so `en-IN` groups in lakhs and crores (1,00,000) and other locales use their own
 * separators. The locale defaults to `en-IN`, never to the machine's, so every renderer agrees.
 */

import { easingFn } from './easing.js';

/** @typedef {import('./easing.js').EasingLike} EasingLike */
/**
 * @typedef {object} CountOptions
 * @property {number} [decimals]           fraction digits shown (default 0)
 * @property {string} [locale]             BCP 47 tag (default `en-IN`)
 * @property {boolean} [useGrouping]       thousands separators (default true)
 * @property {string} [numberingSystem]    e.g. `tamldec` or `deva` digits (default: the locale's)
 * @property {EasingLike} [ease]           applied to `p` first (default `linear`)
 */

const DEFAULT_LOCALE = 'en-IN';
const formatters = new Map();

/**
 * @param {string} locale @param {number} decimals @param {boolean} useGrouping @param {string | undefined} nu
 * @returns {Intl.NumberFormat}
 */
function formatter(locale, decimals, useGrouping, nu) {
  const key = `${locale}|${decimals}|${useGrouping}|${nu || ''}`;
  let f = formatters.get(key);
  if (!f) {
    /** @type {Intl.NumberFormatOptions} */
    const opts = { minimumFractionDigits: decimals, maximumFractionDigits: decimals, useGrouping };
    if (nu) opts.numberingSystem = nu;
    try {
      f = new Intl.NumberFormat(locale, /** @type {any} */ ({ ...opts, signDisplay: 'negative' })); // hides "-0"
    } catch {
      try {
        f = new Intl.NumberFormat(locale, opts); // engines without signDisplay "negative"
      } catch {
        f = new Intl.NumberFormat(DEFAULT_LOCALE, opts); // invalid locale tag
      }
    }
    formatters.set(key, f);
  }
  return f;
}

/** @param {unknown} d */
function fractionDigits(d) {
  return typeof d === 'number' && Number.isFinite(d) ? Math.min(20, Math.max(0, Math.round(d))) : 0;
}

/**
 * The numeric value of a count-up at progress `p` (clamped to [0, 1], eased), rounded to `decimals`.
 * Exactly `from` at p <= 0 and exactly `to` at p >= 1.
 * @param {number} from @param {number} to @param {number} p
 * @param {{decimals?: number, ease?: EasingLike}} [options]
 * @returns {number}
 */
export function countUpValue(from, to, p, { decimals = 0, ease } = {}) {
  const d = fractionDigits(decimals);
  const u = !(p > 0) ? 0 : p >= 1 ? 1 : easingFn(ease)(p);
  const raw = u <= 0 ? from : u >= 1 ? to : from + (to - from) * u;
  const scale = 10 ** d;
  const rounded = Math.round(raw * scale) / scale;
  return rounded === 0 ? 0 : rounded; // no negative zero
}

/**
 * The formatted count-up text at progress `p`: `countUp(0, 250000, 1)` is `"2,50,000"`.
 * @param {number} from @param {number} to @param {number} p  progress 0..1 (clamped)
 * @param {CountOptions} [options]
 * @returns {string}
 */
export function countUp(from, to, p, { decimals = 0, locale = DEFAULT_LOCALE, useGrouping = true, numberingSystem, ease } = {}) {
  const d = fractionDigits(decimals);
  const value = countUpValue(from, to, p, { decimals: d, ease });
  return formatter(locale || DEFAULT_LOCALE, d, useGrouping !== false, numberingSystem).format(value);
}
