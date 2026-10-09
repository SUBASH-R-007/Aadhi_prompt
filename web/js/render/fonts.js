// @ts-check
/**
 * Font preloading for the render page: every family/weight the player uses, plus the Noto
 * family for each Indic script in the timeline's languages or content. Faces are split by
 * unicode-range (web/vendor/fonts.css), so each load passes sample text covering the subsets.
 */

/** @typedef {{ family: string, weight: number, sample: string }} FontRequest */

const LATIN_SAMPLE = 'AaZz09Āā…';

/** Every vendored family/weight the CSS may use (superset of Inter 400/600/700, Outfit 400/700/900,
 * JetBrains Mono 400): preloading all of them means no face is fetched lazily mid-render. */
export const BASE_FONTS = Object.freeze([
  { family: 'Inter', weights: [300, 400, 600, 700] },
  { family: 'Outfit', weights: [300, 400, 700, 900] },
  { family: 'JetBrains Mono', weights: [400, 700] },
]);

/** Indic scripts: Noto family, sample text and the Unicode block used to detect them. */
export const SCRIPT_FONTS = Object.freeze({
  tamil: { family: 'Noto Sans Tamil', sample: 'தமிழ்', re: /[஀-௿]/ },
  devanagari: { family: 'Noto Sans Devanagari', sample: 'हिन्दी', re: /[ऀ-ॿ]/ },
  telugu: { family: 'Noto Sans Telugu', sample: 'తెలుగు', re: /[ఀ-౿]/ },
  kannada: { family: 'Noto Sans Kannada', sample: 'ಕನ್ನಡ', re: /[ಀ-೿]/ },
  malayalam: { family: 'Noto Sans Malayalam', sample: 'മലയാളം', re: /[ഀ-ൿ]/ },
});

/** Language prefix -> script key. */
const LANGUAGE_SCRIPTS = Object.freeze({ ta: 'tamil', hi: 'devanagari', mr: 'devanagari', te: 'telugu', kn: 'kannada', ml: 'malayalam' });

const NOTO_WEIGHTS = [400, 700];

/**
 * Script keys implied by a BCP-47 language code ("ta-IN" -> "tamil").
 * @param {unknown} language
 * @returns {string | null}
 */
export function scriptForLanguage(language) {
  const prefix = String(language || '').toLowerCase().split(/[-_]/)[0];
  return /** @type {Record<string, string>} */ (LANGUAGE_SCRIPTS)[prefix] || null;
}

/**
 * Collect every string in the timeline (bounded) and detect the Indic scripts present.
 * @param {unknown} timeline
 * @param {number} [budget]  max characters inspected
 * @returns {Set<string>}
 */
export function detectScripts(timeline, budget = 2_000_000) {
  /** @type {Set<string>} */
  const found = new Set();
  const entries = Object.entries(SCRIPT_FONTS);
  let seen = 0;
  /** @type {unknown[]} */
  const stack = [timeline];
  while (stack.length && seen < budget && found.size < entries.length) {
    const v = stack.pop();
    if (typeof v === 'string') {
      seen += v.length;
      if (/[ऀ-ൿ]/.test(v)) for (const [key, s] of entries) if (s.re.test(v)) found.add(key);
    } else if (Array.isArray(v)) {
      for (const x of v) stack.push(x);
    } else if (v && typeof v === 'object') {
      for (const x of Object.values(v)) stack.push(x);
    }
  }
  return found;
}

/**
 * The fonts to load before rendering a timeline.
 * @param {any} timeline
 * @returns {FontRequest[]}
 */
export function fontRequests(timeline) {
  /** @type {FontRequest[]} */
  const out = [];
  for (const f of BASE_FONTS) for (const weight of f.weights) out.push({ family: f.family, weight, sample: LATIN_SAMPLE });
  const scripts = detectScripts(timeline);
  for (const lang of [timeline && timeline.language, timeline && timeline.board_language]) {
    const s = scriptForLanguage(lang);
    if (s) scripts.add(s);
  }
  for (const key of [...scripts].sort()) {
    const s = /** @type {Record<string, { family: string, sample: string }>} */ (SCRIPT_FONTS)[key];
    for (const weight of NOTO_WEIGHTS) out.push({ family: s.family, weight, sample: `${s.sample}${LATIN_SAMPLE}` });
  }
  return out;
}

/**
 * Load all requested faces. Rejects if a face fails to load (network/decode error); a family
 * with no matching @font-face is reported in the returned `missing` list instead.
 * @param {FontFaceSet | undefined | null} fontSet  document.fonts
 * @param {FontRequest[]} requests
 * @returns {Promise<{ loaded: number, missing: string[] }>}
 */
export async function preloadFonts(fontSet, requests) {
  if (!fontSet || typeof fontSet.load !== 'function') return { loaded: 0, missing: requests.map(describe) };
  const results = await Promise.all(
    requests.map((r) => fontSet.load(`${r.weight} 32px "${r.family}"`, r.sample).then((faces) => ({ r, faces }))),
  );
  if (fontSet.ready) await fontSet.ready;
  const missing = results.filter((x) => !x.faces || x.faces.length === 0).map((x) => describe(x.r));
  return { loaded: results.length - missing.length, missing };
}

/** @param {FontRequest} r */
function describe(r) {
  return `${r.family} ${r.weight}`;
}
