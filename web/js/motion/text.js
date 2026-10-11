// @ts-check
/**
 * Text splitting for kinetic type (design §4.6, §4.11) with `Intl.Segmenter`: user-perceived characters
 * (graphemes) that never cut an Indic syllable, words with their punctuation attached, and greedy or
 * balanced line breaking with a caller-supplied measure function. No DOM: the page measures with a
 * canvas or a hidden element and passes the function in.
 *
 * Indic conjuncts. Unicode 15.1 (ICU 74) keeps a consonant + virama + consonant cluster in one grapheme
 * for Devanagari, Bengali, Gujarati, Oriya, Telugu and Malayalam (rule GB9c). Older engines (Safari and
 * older Chromium builds of the live player) split it, and no version joins Kannada conjuncts or the Tamil
 * ligatures க்ஷ (kssa) and ஸ்ரீ (shri), which fonts draw as one glyph. `joinIndicConjuncts` applies the
 * rule on top of whatever the engine returned, so every runtime splits the same way and an animated
 * letter never tears a conjunct apart. Tamil's visible pulli (்) otherwise ends a cluster, as Unicode says.
 */

/**
 * A word unit: the word with its attached punctuation, the whitespace that follows it, and its offset.
 * @typedef {object} WordPart
 * @property {string} text      the word, e.g. `world!` (never contains breaking whitespace)
 * @property {string} ws        whitespace after it (`''` at the end; may contain `\n`)
 * @property {number} index     UTF-16 offset of `text` in the input
 * @property {boolean} wordLike false for units that are only punctuation or symbols (e.g. `—`, `=`)
 */

/** Segmenters are costly to build; one per (granularity, locale). */
const segmenters = new Map();

/**
 * @param {'grapheme' | 'word'} granularity
 * @param {string | undefined} locale
 * @returns {Intl.Segmenter}
 */
function segmenter(granularity, locale) {
  const key = `${granularity}|${locale || ''}`;
  let seg = segmenters.get(key);
  if (!seg) {
    try {
      seg = new Intl.Segmenter(locale || undefined, { granularity });
    } catch {
      seg = new Intl.Segmenter(undefined, { granularity }); // invalid locale tag: the root rules
    }
    segmenters.set(key, seg);
  }
  return seg;
}

/**
 * Brahmic scripts whose virama joins the next consonant into a conjunct: block start and the code-point
 * ranges of their consonants (offsets in the block). The virama is at offset 0x4D in each block.
 * @type {Array<[number, Array<[number, number]>]>}
 */
const CONJUNCT_SCRIPTS = [
  [0x0900, [[0x15, 0x39], [0x58, 0x5f], [0x78, 0x7f]]], // Devanagari
  [0x0980, [[0x15, 0x39], [0x5c, 0x5f], [0x70, 0x71]]], // Bengali
  [0x0a80, [[0x15, 0x39], [0x79, 0x79]]], // Gujarati
  [0x0b00, [[0x15, 0x39], [0x5c, 0x5f], [0x71, 0x71]]], // Oriya
  [0x0c00, [[0x15, 0x39], [0x58, 0x5a]]], // Telugu
  [0x0c80, [[0x15, 0x39], [0x5d, 0x5e]]], // Kannada
  [0x0d00, [[0x15, 0x3a]]], // Malayalam
];
const VIRAMA_OFFSET = 0x4d;
const ZWJ = 0x200d;

/** The conjunct-forming block a virama belongs to, or -1. */
function viramaBlock(cp) {
  for (const [base] of CONJUNCT_SCRIPTS) if (cp === base + VIRAMA_OFFSET) return base;
  return -1;
}

/** Whether `cp` is a consonant of the block starting at `base`. */
function isConsonantOf(cp, base) {
  const entry = CONJUNCT_SCRIPTS.find(([b]) => b === base);
  if (!entry) return false;
  const off = cp - base;
  return entry[1].some(([lo, hi]) => off >= lo && off <= hi);
}

/** Tamil sequences that fonts draw as one ligature: [cluster ending in pulli, start of the next]. */
const TAMIL_LIGATURES = [
  ['க்', 'ஷ'], // க் + ஷ = க்ஷ (kssa)
  ['ஸ்', 'ரீ'], // ஸ் + ரீ = ஸ்ரீ (shri)
];

/**
 * Whether cluster `next` continues the conjunct that cluster `prev` leaves open.
 * @param {string} prev @param {string} next
 */
function continuesConjunct(prev, next) {
  const cps = [...prev];
  let last = cps.length - 1;
  if (last > 0 && cps[last].codePointAt(0) === ZWJ) last--; // virama + ZWJ asks for a half form: still joined
  const virama = cps[last]?.codePointAt(0) ?? -1;
  const base = viramaBlock(virama);
  const first = next.codePointAt(0) ?? -1;
  if (base >= 0) {
    // The virama must follow a consonant (possibly with a nukta) of the same script.
    return last > 0 && isConsonantOf(first, base);
  }
  return TAMIL_LIGATURES.some(([end, start]) => prev.endsWith(end) && next.startsWith(start));
}

/**
 * Join grapheme clusters that an engine split inside an Indic conjunct (see the module note). Never splits.
 * @param {string[]} clusters
 * @returns {string[]}
 */
export function joinIndicConjuncts(clusters) {
  /** @type {string[]} */
  const out = [];
  for (const c of clusters) {
    if (out.length && continuesConjunct(out[out.length - 1], c)) out[out.length - 1] += c;
    else out.push(c);
  }
  return out;
}

/**
 * User-perceived characters of `str` (extended grapheme clusters, Indic conjuncts kept whole).
 * `segmentGraphemes(s).join('') === s` always holds.
 * @param {string} str
 * @param {string} [locale]  BCP 47 tag, e.g. `ta-IN` (grapheme rules barely depend on it)
 * @returns {string[]}
 */
export function segmentGraphemes(str, locale) {
  const s = String(str ?? '');
  if (!s) return [];
  const raw = Array.from(segmenter('grapheme', locale).segment(s), (x) => x.segment);
  return joinIndicConjuncts(raw);
}

/** No-break spaces (NBSP, figure space, narrow NBSP): they glue a number to its unit, so they stay inside a word. */
const GLUE_RE = /^[\u00a0\u2007\u202f]+$/;
/** Whitespace (including the zero-width space, a break opportunity). */
const SPACE_RE = /^[\s​]+$/u;
/** Scripts written without spaces between words: there, word boundaries are unit boundaries. */
const SPACELESS_RE = /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Thai}\p{Script=Lao}\p{Script=Khmer}\p{Script=Myanmar}]/u;

/**
 * Words of `str` for kinetic type: one unit per space-separated word, with punctuation attached
 * (`"world!"`, `"(see"`, `"10 V"` when joined by a no-break space). In scripts written without spaces
 * the `Intl.Segmenter` word boundaries split units. Leading whitespace is dropped; otherwise
 * `parts.map((p) => p.text + p.ws).join('')` reproduces the input.
 * @param {string} str
 * @param {string} [locale]
 * @returns {WordPart[]}
 */
export function splitWords(str, locale) {
  const s = String(str ?? '');
  /** @type {WordPart[]} */
  const parts = [];
  for (const seg of segmenter('word', locale).segment(s)) {
    const text = seg.segment;
    const cur = parts[parts.length - 1];
    if (SPACE_RE.test(text) && !GLUE_RE.test(text)) {
      if (cur) cur.ws += text;
      continue;
    }
    const wordLike = Boolean(seg.isWordLike);
    const spaceless = cur && wordLike && SPACELESS_RE.test(text) && SPACELESS_RE.test(cur.text.slice(-1));
    if (cur && cur.ws === '' && !spaceless) {
      cur.text += text;
      cur.wordLike = cur.wordLike || wordLike;
    } else {
      parts.push({ text, ws: '', index: seg.index, wordLike });
    }
  }
  return parts;
}

/**
 * Break `str` into lines no wider than `maxWidth` (greedy, at word units), measuring candidate lines with
 * `measure`. Hard line breaks (`\n`) are kept. A word wider than a whole line is broken between graphemes,
 * never inside one. With `balance`, the same number of lines is kept but the width is narrowed as far as
 * possible, so the lines come out even (like CSS `text-wrap: balance`).
 * @param {string} str
 * @param {number} maxWidth
 * @param {(text: string) => number} measure  width of a single line of text
 * @param {{locale?: string, balance?: boolean}} [options]
 * @returns {string[]} lines without leading or trailing whitespace
 */
export function splitLines(str, maxWidth, measure, { locale, balance = false } = {}) {
  const paragraphs = String(str ?? '').split(/\r\n|\r|\n/);
  /** @type {string[]} */
  const lines = [];
  for (const para of paragraphs) {
    const words = splitWords(para, locale);
    if (!words.length) {
      lines.push('');
      continue;
    }
    let out = greedyLines(words, maxWidth, measure, locale);
    if (balance && out.length > 1) {
      // Narrow the width while the line count holds; never below the widest word, so no word breaks.
      let lo = Math.max(0, ...words.map((w) => measure(w.text))) - 0.5;
      let hi = maxWidth;
      for (let i = 0; i < 24 && hi - lo > 0.5; i++) {
        const mid = (lo + hi) / 2;
        const trial = greedyLines(words, mid, measure, locale);
        if (trial.length === out.length && trial.every((l) => measure(l) <= mid)) hi = mid;
        else lo = mid;
      }
      out = greedyLines(words, hi, measure, locale);
    }
    lines.push(...out);
  }
  return lines;
}

/**
 * @param {WordPart[]} words @param {number} maxWidth
 * @param {(text: string) => number} measure @param {string | undefined} locale
 * @returns {string[]}
 */
function greedyLines(words, maxWidth, measure, locale) {
  /** @type {string[]} */
  const out = [];
  let line = '';
  let gap = ''; // whitespace owed before the next word on this line
  for (const w of words) {
    if (line) {
      const candidate = `${line}${gap}${w.text}`;
      if (measure(candidate) <= maxWidth) {
        line = candidate;
        gap = w.ws;
        continue;
      }
      out.push(line);
    }
    if (measure(w.text) <= maxWidth) {
      line = w.text;
    } else {
      // A word wider than the line: break it between graphemes.
      const pieces = breakWord(w.text, maxWidth, measure, locale);
      out.push(...pieces.slice(0, -1));
      line = pieces[pieces.length - 1];
    }
    gap = w.ws;
  }
  if (line) out.push(line);
  return out;
}

/**
 * Break one over-long word into pieces of whole graphemes, each at most `maxWidth` wide (at least one
 * grapheme per piece, so a single too-wide grapheme still makes progress).
 * @param {string} word @param {number} maxWidth
 * @param {(text: string) => number} measure @param {string | undefined} locale
 * @returns {string[]}
 */
function breakWord(word, maxWidth, measure, locale) {
  /** @type {string[]} */
  const pieces = [];
  let piece = '';
  for (const g of segmentGraphemes(word, locale)) {
    if (piece && measure(piece + g) > maxWidth) {
      pieces.push(piece);
      piece = g;
    } else {
      piece += g;
    }
  }
  if (piece) pieces.push(piece);
  return pieces;
}
