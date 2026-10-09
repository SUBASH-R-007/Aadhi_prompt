// @ts-check
/**
 * Font readiness (self-hosted fonts from web/vendor/fonts.css). Render mode must not screenshot
 * before every family used on the stage — including the Indic script of the lecture — is loaded;
 * live mode waits briefly to avoid a flash of fallback text.
 */

/** Script fonts per language prefix: [family, sample text that selects the script subset]. */
const SCRIPT_FONTS = /** @type {Record<string, [string, string]>} */ ({
  ta: ['Noto Sans Tamil', 'தமிழ்'],
  hi: ['Noto Sans Devanagari', 'हिन्दी'],
  mr: ['Noto Sans Devanagari', 'मराठी'],
  sa: ['Noto Sans Devanagari', 'संस्कृत'],
  ne: ['Noto Sans Devanagari', 'नेपाली'],
  te: ['Noto Sans Telugu', 'తెలుగు'],
  kn: ['Noto Sans Kannada', 'ಕನ್ನಡ'],
  ml: ['Noto Sans Malayalam', 'മലയാളം'],
});

const BASE_FONTS = /** @type {[string, string][]} */ ([
  ['300 32px Inter', 'Aa'],
  ['400 32px Inter', 'Aa'],
  ['600 32px Inter', 'Aa'],
  ['700 32px Inter', 'Aa'],
  ['400 32px Outfit', 'Aa'],
  ['700 32px Outfit', 'Aa'],
  ['900 32px Outfit', 'Aa'],
  ['400 32px "JetBrains Mono"', 'Aa'],
]);

/**
 * CSS font shorthands (+ sample text) needed for a lecture language (BCP-47, e.g. "ta-IN").
 * @param {string | null | undefined} language
 * @returns {[string, string][]}
 */
export function fontSpecsFor(language) {
  const specs = BASE_FONTS.slice();
  const prefix = String(language || '').toLowerCase().split(/[-_]/)[0];
  const script = SCRIPT_FONTS[prefix];
  if (script) {
    specs.push([`400 32px "${script[0]}"`, script[1]], [`700 32px "${script[0]}"`, script[1]]);
  }
  return specs;
}

/**
 * Load the fonts for the given languages; resolves (never rejects) when done or after the timeout.
 * @param {(string | null | undefined)[]} languages
 * @param {{ timeoutMs?: number }} [opts]
 * @returns {Promise<boolean>} true when every font loaded in time
 */
export async function ensureFonts(languages, opts = {}) {
  const fonts = typeof document !== 'undefined' ? /** @type {any} */ (document).fonts : null;
  if (!fonts || typeof fonts.load !== 'function') return true;
  /** @type {Map<string, string>} */
  const unique = new Map();
  for (const lang of languages) for (const [font, sample] of fontSpecsFor(lang)) unique.set(font, sample);
  const work = Promise.all([...unique].map(([font, sample]) => fonts.load(font, sample).catch(() => []))).then(() => fonts.ready).then(
    () => true,
    () => false,
  );
  if (!opts.timeoutMs) return work;
  /** @type {ReturnType<typeof setTimeout> | undefined} */
  let timer;
  const timeout = new Promise((resolve) => {
    timer = setTimeout(() => resolve(false), opts.timeoutMs);
  });
  const result = await Promise.race([work, timeout]);
  clearTimeout(timer);
  return /** @type {boolean} */ (result);
}
