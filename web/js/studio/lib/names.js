// @ts-check
/**
 * Lecture names as lists show them (pure).
 * `uniqueJoin` drops repeated name parts ("Physics · Physics" reads "Physics"); `distinctTitles` gives
 * lectures that share a title a short suffix so they can be told apart in a list.
 */

/**
 * Comparison form of a name part: trimmed, inner whitespace collapsed, case-folded.
 * @param {unknown} value
 */
export function nameKey(value) {
  return String(value ?? '')
    .normalize('NFC')
    .replace(/\s+/g, ' ')
    .trim()
    .toLocaleLowerCase('en');
}

/**
 * Join name parts, skipping empty ones and parts already shown (case- and whitespace-insensitive).
 * @param {Array<string | number | null | undefined>} parts
 * @param {string} [sep]
 */
export function uniqueJoin(parts, sep = ' · ') {
  const seen = new Set();
  /** @type {string[]} */
  const out = [];
  for (const part of parts) {
    const text = String(part ?? '').replace(/\s+/g, ' ').trim();
    const key = nameKey(text);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    out.push(text);
  }
  return out.join(sep);
}

/** Fields that may tell two lectures with one title apart, in the order they are tried. */
const DISTINGUISHING_FIELDS = /** @type {const} */ (['session_number', 'unit_name', 'subject_name', 'language']);

/**
 * "3 Oct 2026" for a suffix (null when the date cannot be read).
 * @param {string | null | undefined} iso
 */
function shortDate(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
}

/**
 * Suffixes that tell apart items sharing one title: the first field whose values are all present and
 * different (session, unit, subject, language), else the creation date, else a running number (by id).
 * Items with a unique title get no suffix.
 * @template {{ id: number, title?: string | null, created_at?: string | null } & Record<string, any>} T
 * @param {T[]} items
 * @param {{ title?: (item: T) => string, fields?: readonly string[] }} [opts]
 * @returns {Map<number, string>} item id -> suffix (only for items that need one)
 */
export function distinctTitles(items, opts = {}) {
  const titleOf = opts.title || ((/** @type {T} */ it) => String(it.title || ''));
  const fields = opts.fields || DISTINGUISHING_FIELDS;
  /** @type {Map<string, T[]>} */
  const groups = new Map();
  for (const it of items) {
    if (!it || typeof it.id !== 'number') continue;
    const key = nameKey(titleOf(it));
    if (!key) continue;
    const group = groups.get(key) || [];
    if (!group.some((g) => g.id === it.id)) group.push(it);
    groups.set(key, group);
  }
  /** @type {Map<number, string>} */
  const out = new Map();
  for (const group of groups.values()) {
    if (group.length < 2) continue;
    const titleKey = nameKey(titleOf(group[0]));
    /** @type {((it: T) => string | null) | null} */
    let pick = null;
    for (const field of fields) {
      const values = group.map((it) => String(it[field] ?? '').trim());
      const keys = values.map(nameKey);
      if (keys.every((k) => k && k !== titleKey) && new Set(keys).size === group.length) {
        pick = (it) => String(it[field] ?? '').trim();
        break;
      }
    }
    if (!pick) {
      const dates = group.map((it) => shortDate(it.created_at));
      if (dates.every(Boolean) && new Set(dates).size === group.length) pick = (it) => shortDate(it.created_at);
    }
    if (pick) {
      for (const it of group) out.set(it.id, /** @type {string} */ (pick(it)));
      continue;
    }
    [...group].sort((a, b) => a.id - b.id).forEach((it, i) => out.set(it.id, `(${i + 1})`));
  }
  return out;
}

/**
 * A title with its distinguishing suffix ("Ohm's law · Session 2", "Ohm's law (2)").
 * @param {string} title
 * @param {string | undefined} suffix
 */
export function withSuffix(title, suffix) {
  if (!suffix) return title;
  return suffix.startsWith('(') ? `${title} ${suffix}` : `${title} · ${suffix}`;
}
