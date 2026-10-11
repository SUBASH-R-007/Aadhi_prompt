// @ts-check
/**
 * Seeded randomness (design §7.4.5): mulberry32 seeded with `hashSeed(look_seed, scene_id, purpose)`.
 * `Math.random` is banned in the stage, so every "random" choice (particle positions, blob paths,
 * composition offsets) is reproducible from the timeline.
 *
 * `hashSeed` is specified so Python can reproduce it: every part becomes a string (`String(part)`; null
 * and undefined become ""), the parts are joined with U+001F, encoded as UTF-8 and hashed with 32-bit
 * FNV-1a (offset 0x811C9DC5, prime 0x01000193); the result goes through the murmur3 `fmix32` finaliser
 * (xor-shift 16, × 0x85EBCA6B, xor-shift 13, × 0xC2B2AE35, xor-shift 16). Pass integers and strings:
 * JavaScript and Python print other values differently (`1e-7` vs `1e-07`, `1` vs `1.0`, `true` vs `True`).
 * Test vectors: web/tests/motion/random.test.js.
 */

/** @typedef {() => number} Random  returns a float in [0, 1) */

/**
 * The mulberry32 generator (Tommy Ettinger): a 32-bit state, period 2^32, fast and good enough for
 * visuals. Never use it for anything secret.
 * @param {number} seed  any number; only its low 32 bits count
 * @returns {Random}
 */
export function mulberry32(seed) {
  let a = seed >>> 0;
  return function next() {
    a = (a + 0x6d2b79f5) | 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/**
 * UTF-8 bytes of a string (no TextEncoder needed, so the module has no environment dependencies).
 * @param {string} s
 * @returns {number[]}
 */
function utf8(s) {
  /** @type {number[]} */
  const out = [];
  for (const ch of s) {
    let cp = /** @type {number} */ (ch.codePointAt(0));
    if (cp >= 0xd800 && cp <= 0xdfff) cp = 0xfffd; // lone surrogate: the replacement character, as UTF-8 encoders do
    if (cp < 0x80) out.push(cp);
    else if (cp < 0x800) out.push(0xc0 | (cp >> 6), 0x80 | (cp & 63));
    else if (cp < 0x10000) out.push(0xe0 | (cp >> 12), 0x80 | ((cp >> 6) & 63), 0x80 | (cp & 63));
    else out.push(0xf0 | (cp >> 18), 0x80 | ((cp >> 12) & 63), 0x80 | ((cp >> 6) & 63), 0x80 | (cp & 63));
  }
  return out;
}

/**
 * A 32-bit seed from any number of parts: `hashSeed(lookSeed, scene.id, 'background')`.
 * The order of the parts matters, and ("ab", "c") differs from ("a", "bc").
 * @param {...(string | number | boolean | null | undefined)} parts
 * @returns {number} an unsigned 32-bit integer
 */
export function hashSeed(...parts) {
  const text = parts.map((p) => (p == null ? '' : String(p))).join('\u001f');
  let h = 0x811c9dc5;
  for (const byte of utf8(text)) {
    h ^= byte;
    h = Math.imul(h, 0x01000193);
  }
  h ^= h >>> 16;
  h = Math.imul(h, 0x85ebca6b);
  h ^= h >>> 13;
  h = Math.imul(h, 0xc2b2ae35);
  h ^= h >>> 16;
  return h >>> 0;
}

/**
 * A generator seeded from parts: `seededRandom(lookSeed, scene.id, 'particles')`.
 * @param {...(string | number | boolean | null | undefined)} parts
 * @returns {Random}
 */
export function seededRandom(...parts) {
  return mulberry32(hashSeed(...parts));
}

/**
 * A float in [lo, hi).
 * @param {Random} rand @param {number} lo @param {number} hi
 */
export function randomRange(rand, lo, hi) {
  return lo + (hi - lo) * rand();
}

/**
 * An integer in [lo, hi] (both inclusive).
 * @param {Random} rand @param {number} lo @param {number} hi
 */
export function randomInt(rand, lo, hi) {
  const a = Math.ceil(Math.min(lo, hi));
  const b = Math.floor(Math.max(lo, hi));
  return a + Math.floor(rand() * (b - a + 1));
}

/**
 * One element of a non-empty array.
 * @template T
 * @param {Random} rand @param {readonly T[]} items
 * @returns {T}
 */
export function pick(rand, items) {
  if (!items.length) throw new RangeError('pick: empty array');
  return items[Math.floor(rand() * items.length)];
}

/**
 * A shuffled copy (Fisher–Yates); the input is not changed.
 * @template T
 * @param {Random} rand @param {readonly T[]} items
 * @returns {T[]}
 */
export function shuffle(rand, items) {
  const out = items.slice();
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}
