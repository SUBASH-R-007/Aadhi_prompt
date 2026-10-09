// @ts-check
/**
 * Small, dependency-free helpers shared by the Studio (formatting, cloning, debouncing).
 * Everything here is pure or only touches timers, so it is unit-testable under node.
 */

/**
 * @template {(...args: any[]) => void} F
 * @typedef {F & { cancel: () => void, flush: () => void, pending: () => boolean }} Debounced
 */

/**
 * Trailing-edge debounce with cancel/flush.
 * @template {(...args: any[]) => void} F
 * @param {F} fn
 * @param {number} ms
 * @returns {Debounced<F>}
 */
export function debounce(fn, ms) {
  /** @type {ReturnType<typeof setTimeout> | null} */
  let timer = null;
  /** @type {any[] | null} */
  let lastArgs = null;
  const run = () => {
    timer = null;
    const args = lastArgs || [];
    lastArgs = null;
    fn(...args);
  };
  const debounced = /** @type {Debounced<F>} */ (
    /** @type {unknown} */ (
      (/** @type {any[]} */ ...args) => {
        lastArgs = args;
        if (timer) clearTimeout(timer);
        timer = setTimeout(run, ms);
      }
    )
  );
  debounced.cancel = () => {
    if (timer) clearTimeout(timer);
    timer = null;
    lastArgs = null;
  };
  debounced.flush = () => {
    if (timer) {
      clearTimeout(timer);
      run();
    }
  };
  debounced.pending = () => timer !== null;
  return debounced;
}

/**
 * Deep clone of JSON-compatible data.
 * @template T
 * @param {T} value
 * @returns {T}
 */
export function clone(value) {
  if (value === undefined || value === null || typeof value !== 'object') return value;
  if (typeof structuredClone === 'function') return structuredClone(value);
  return JSON.parse(JSON.stringify(value));
}

/**
 * Structural equality for JSON-compatible data (object key order is ignored;
 * `undefined` properties are treated as absent, like JSON serialisation).
 * @param {any} a
 * @param {any} b
 * @returns {boolean}
 */
export function deepEqual(a, b) {
  if (a === b) return true;
  if (typeof a !== typeof b) return false;
  if (a === null || b === null || typeof a !== 'object') return Number.isNaN(a) && Number.isNaN(b);
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  if (Array.isArray(a)) {
    if (a.length !== b.length) return false;
    for (let i = 0; i < a.length; i++) if (!deepEqual(a[i], b[i])) return false;
    return true;
  }
  const ka = Object.keys(a).filter((k) => a[k] !== undefined);
  const kb = Object.keys(b).filter((k) => b[k] !== undefined);
  if (ka.length !== kb.length) return false;
  for (const k of ka) {
    if (!Object.prototype.hasOwnProperty.call(b, k) || !deepEqual(a[k], b[k])) return false;
  }
  return true;
}

let uidCounter = 0;

/**
 * Unique DOM id for label/aria wiring. Never derived from model data (DOM-clobbering safe).
 * @param {string} [prefix]
 */
export function uid(prefix = 'el') {
  uidCounter += 1;
  return `st-${prefix}-${uidCounter}`;
}

/**
 * @param {number} n
 * @param {number} lo
 * @param {number} hi
 */
export function clamp(n, lo, hi) {
  return Math.min(hi, Math.max(lo, n));
}

/**
 * Move one element of an array (returns a new array). Out-of-range indices are clamped.
 * @template T
 * @param {readonly T[]} list
 * @param {number} from
 * @param {number} to
 * @returns {T[]}
 */
export function moveIndex(list, from, to) {
  const out = list.slice();
  if (from < 0 || from >= out.length) return out;
  const target = clamp(to, 0, out.length - 1);
  const [item] = out.splice(from, 1);
  out.splice(target, 0, item);
  return out;
}

/**
 * Seconds -> "m:ss" or "h:mm:ss".
 * @param {number | null | undefined} seconds
 */
export function formatDuration(seconds) {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return '–';
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const pad = (/** @type {number} */ n) => String(n).padStart(2, '0');
  return h > 0 ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`;
}

/**
 * @param {number | null | undefined} usd
 */
export function formatUsd(usd) {
  if (usd === null || usd === undefined || !Number.isFinite(usd)) return '–';
  if (usd !== 0 && Math.abs(usd) < 0.01) return `$${usd.toFixed(4)}`;
  return `$${usd.toFixed(2)}`;
}

/**
 * @param {number | null | undefined} bytes
 */
export function formatBytes(bytes) {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes)) return '–';
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB'];
  let v = bytes / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(v >= 10 ? 0 : 1)} ${units[i]}`;
}

/**
 * ISO timestamp -> short local date/time ("12 Mar 2026, 14:05").
 * @param {string | null | undefined} iso
 */
export function formatDate(iso) {
  if (!iso) return '–';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '–';
  return d.toLocaleString(undefined, { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

/**
 * ISO timestamp -> "5 min ago" style text.
 * @param {string | null | undefined} iso
 * @param {number} [now]
 */
export function formatRelative(iso, now = Date.now()) {
  if (!iso) return '–';
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return '–';
  const diff = Math.round((now - t) / 1000);
  const abs = Math.abs(diff);
  const suffix = diff >= 0 ? 'ago' : 'from now';
  if (abs < 45) return diff >= 0 ? 'just now' : 'in a moment';
  if (abs < 3600) return `${Math.round(abs / 60)} min ${suffix}`;
  if (abs < 86400) return `${Math.round(abs / 3600)} h ${suffix}`;
  if (abs < 86400 * 30) return `${Math.round(abs / 86400)} d ${suffix}`;
  return formatDate(iso);
}

/**
 * @param {number} n
 * @param {string} word
 * @param {string} [pluralWord]
 */
export function plural(n, word, pluralWord) {
  return `${n} ${n === 1 ? word : pluralWord || `${word}s`}`;
}

/** Average narration pace used for editor estimates (words per minute). */
export const SPEECH_WPM = 150;

/**
 * Rough spoken duration of a narration string (editor hints only; the server measures TTS).
 * @param {string} text
 */
export function estimateSpeechSeconds(text) {
  const words = String(text || '').trim().split(/\s+/).filter(Boolean).length;
  return words === 0 ? 0 : (words / SPEECH_WPM) * 60;
}

/**
 * True when the user is typing in an editable control (keyboard shortcuts must not fire).
 * @param {EventTarget | null} target
 */
export function isEditableTarget(target) {
  if (!target || typeof (/** @type {any} */ (target).closest) !== 'function') return false;
  const el = /** @type {HTMLElement} */ (target);
  if (el.isContentEditable) return true;
  const tag = el.tagName;
  if (tag === 'TEXTAREA' || tag === 'SELECT') return true;
  if (tag === 'INPUT') {
    const type = (/** @type {HTMLInputElement} */ (el).type || 'text').toLowerCase();
    return !['checkbox', 'radio', 'button', 'submit', 'reset', 'range', 'color', 'file'].includes(type);
  }
  return false;
}

/**
 * Copy text to the clipboard (falls back to a hidden textarea + execCommand).
 * @param {string} text
 * @returns {Promise<boolean>}
 */
export async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through */
  }
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand('copy');
    ta.remove();
    return ok;
  } catch {
    return false;
  }
}

/**
 * Absolute URL for an app path (share links, preview links).
 * @param {string} path
 */
export function absoluteUrl(path) {
  try {
    return new URL(path, location.origin).href;
  } catch {
    return path;
  }
}
