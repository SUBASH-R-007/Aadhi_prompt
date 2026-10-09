// @ts-check
/**
 * Local autosave of unsaved screenplay drafts in localStorage, scoped per user and version
 * (`aadhi.studio.draft.u<user>.v<version>`), so on a shared lab PC one teacher's unsaved
 * lecture (including teacher-only notes) is never offered to another account. Every draft is
 * removed on explicit sign-out (`clearAllDrafts`) and other users' drafts are removed when a
 * different user signs in on this browser (`noteSignedInUser`). Unscoped drafts written by
 * older builds (`aadhi.studio.draft.v<version>`) have no owner and are removed too.
 *
 * Storage can be unavailable (privacy mode) or full; every access is wrapped in try/catch
 * and failures are reported as `false`/`null`, never thrown.
 */

const PREFIX = 'aadhi.studio.draft.';
const LAST_USER_KEY = 'aadhi.studio.lastUser';
/** Skip drafts larger than this (localStorage quotas are ~5 MB per origin). */
export const MAX_DRAFT_CHARS = 2_500_000;

/**
 * @typedef {object} Draft
 * @property {number} revision      server revision the draft was based on
 * @property {string} savedAt       ISO time
 * @property {Record<string, any>} screenplay
 * @property {Record<string, any> | null} base   screenplay at `revision` (for 3-way merges)
 */

/** @typedef {number | null | undefined} UserId */

/** @returns {Storage | null} */
function defaultStorage() {
  try {
    return typeof localStorage !== 'undefined' ? localStorage : null;
  } catch {
    return null;
  }
}

/** @param {UserId} userId */
function validUser(userId) {
  return typeof userId === 'number' && Number.isInteger(userId) && userId > 0;
}

/**
 * Storage key of a user's draft of a version.
 * @param {number} userId
 * @param {number} versionId
 */
export function draftKey(userId, versionId) {
  return `${PREFIX}u${userId}.v${versionId}`;
}

/**
 * Save a draft. Falls back to storing it without its base when the quota is tight.
 * @param {UserId} userId    signed-in user (nothing is stored without one)
 * @param {number} versionId
 * @param {Omit<Draft, 'savedAt'> & { savedAt?: string }} draft
 * @param {Storage | null} [storage]
 * @returns {boolean} true when stored
 */
export function saveDraft(userId, versionId, draft, storage = defaultStorage()) {
  if (!storage || !validUser(userId)) return false;
  const full = { ...draft, savedAt: draft.savedAt || new Date().toISOString() };
  const attempts = [full, { ...full, base: null }];
  for (const candidate of attempts) {
    let text;
    try {
      text = JSON.stringify(candidate);
    } catch {
      return false;
    }
    if (text.length > MAX_DRAFT_CHARS) continue;
    try {
      storage.setItem(draftKey(/** @type {number} */ (userId), versionId), text);
      return true;
    } catch {
      /* quota exceeded: try the smaller form */
    }
  }
  return false;
}

/**
 * @param {UserId} userId
 * @param {number} versionId
 * @param {Storage | null} [storage]
 * @returns {Draft | null}
 */
export function loadDraft(userId, versionId, storage = defaultStorage()) {
  if (!storage || !validUser(userId)) return null;
  try {
    const raw = storage.getItem(draftKey(/** @type {number} */ (userId), versionId));
    if (!raw) return null;
    const d = JSON.parse(raw);
    if (!d || typeof d !== 'object' || !d.screenplay || typeof d.screenplay !== 'object' || !Number.isInteger(d.revision)) return null;
    return { revision: d.revision, savedAt: String(d.savedAt || ''), screenplay: d.screenplay, base: d.base && typeof d.base === 'object' ? d.base : null };
  } catch {
    return null;
  }
}

/**
 * @param {UserId} userId
 * @param {number} versionId
 * @param {Storage | null} [storage]
 */
export function clearDraft(userId, versionId, storage = defaultStorage()) {
  if (!storage || !validUser(userId)) return;
  try {
    storage.removeItem(draftKey(/** @type {number} */ (userId), versionId));
  } catch {
    /* ignore */
  }
}

/**
 * Remove stored drafts: all of them, or all except `keepUserId`'s (legacy unscoped drafts are
 * always removed).
 * @param {Storage | null} [storage]
 * @param {{ keepUserId?: UserId }} [opts]
 * @returns {number} drafts removed
 */
export function clearAllDrafts(storage = defaultStorage(), opts = {}) {
  if (!storage) return 0;
  const keep = validUser(opts.keepUserId) ? `${PREFIX}u${opts.keepUserId}.` : null;
  let removed = 0;
  try {
    /** @type {string[]} */
    const doomed = [];
    for (let i = 0; i < storage.length; i++) {
      const k = storage.key(i);
      if (k && k.startsWith(PREFIX) && !(keep && k.startsWith(keep))) doomed.push(k);
    }
    for (const k of doomed) {
      try {
        storage.removeItem(k);
        removed += 1;
      } catch {
        /* ignore */
      }
    }
  } catch {
    /* storage unavailable */
  }
  return removed;
}

/**
 * Record who is signed in on this browser; when it is a different user than last time, the
 * previous user's drafts (and legacy unscoped ones) are removed.
 * @param {UserId} userId
 * @param {Storage | null} [storage]
 * @returns {number} drafts removed
 */
export function noteSignedInUser(userId, storage = defaultStorage()) {
  if (!storage || !validUser(userId)) return 0;
  let last = null;
  try {
    last = storage.getItem(LAST_USER_KEY);
  } catch {
    return 0;
  }
  const removed = last === String(userId) ? 0 : clearAllDrafts(storage, { keepUserId: userId });
  try {
    storage.setItem(LAST_USER_KEY, String(userId));
  } catch {
    /* ignore */
  }
  return removed;
}

/**
 * Small per-browser UI preferences (pane sizes, toggles; no lecture content).
 * @template T
 * @param {string} key
 * @param {T} fallback
 * @param {Storage | null} [storage]
 * @returns {T}
 */
export function loadPref(key, fallback, storage = defaultStorage()) {
  if (!storage) return fallback;
  try {
    const raw = storage.getItem(`aadhi.studio.pref.${key}`);
    return raw === null ? fallback : JSON.parse(raw);
  } catch {
    return fallback;
  }
}

/**
 * @param {string} key
 * @param {any} value
 * @param {Storage | null} [storage]
 */
export function savePref(key, value, storage = defaultStorage()) {
  if (!storage) return;
  try {
    storage.setItem(`aadhi.studio.pref.${key}`, JSON.stringify(value));
  } catch {
    /* ignore */
  }
}
