// @ts-check
/**
 * Password strength hints shown while typing. They mirror the server's
 * `aadhi.auth.passwords.check_password_strength` (NIST 800-63B style: length + deny-list, NO
 * composition rules), so a password the client accepts is never rejected for strength and a
 * passphrase like "correct horse battery staple" is never blocked. The server stays
 * authoritative; its 422 messages are displayed verbatim.
 *
 * `PASSWORD_MIN_LENGTH` is configurable on the server (8 or more) and GET /api/meta does not
 * publish it, so unless the caller knows the configured minimum (`serverMinLength(meta)`),
 * lengths between the floor (8) and the default (10) are only *advisory*: the hint shows,
 * but `blockingHints` lets the server decide.
 */

/** Default `PASSWORD_MIN_LENGTH`. */
export const MIN_PASSWORD_LENGTH = 10;
/** The server never accepts a minimum below this. */
export const SERVER_MIN_LENGTH_FLOOR = 8;
/** bcrypt only uses the first 72 bytes; the server rejects longer passwords. */
export const MAX_PASSWORD_BYTES = 72;
/** Minimum number of different characters. */
export const MIN_DISTINCT_CHARS = 5;

/** Base words that stay guessable however they are decorated (server `_BASE_WORDS`). */
const BASE_WORDS = new Set([
  'password', 'passw', 'admin', 'administrator', 'aadhi', 'aadhiedu', 'eduengine', 'rajalakshmi', 'rec', 'qwerty',
  'qwertyuiop', 'asdfgh', 'welcome', 'letmein', 'changeme', 'iloveyou', 'blackbuck', 'teacher', 'student', 'secret',
  'default', 'login', 'abc', 'abcdef', 'abcdefgh', 'test', 'guest',
]);
/** Common full passwords (server `_COMMON`). */
const COMMON = new Set([
  '1234567890', '0123456789', '12345678910', '123456789012', '1111111111', '0000000000', '9876543210', '1q2w3e4r5t',
  'qazwsxedcr', 'zaq12wsxcde', '1qaz2wsx3edc',
]);

/**
 * @typedef {{ key: string, ok: boolean, text: string, advisory?: boolean }} PasswordHint
 *   advisory: unmet, but the server may still accept it (do not block submission)
 */

/**
 * The server's configured minimum length when /api/meta publishes it (`limits.password_min_length`;
 * requested in the API contract), else null.
 * @param {any} meta
 * @returns {number | null}
 */
export function serverMinLength(meta) {
  const v = meta && meta.limits ? Number(meta.limits.password_min_length) : NaN;
  return Number.isInteger(v) && v >= SERVER_MIN_LENGTH_FLOOR ? v : null;
}

/**
 * Unmet requirements that must block submission (advisory hints excluded).
 * @param {PasswordHint[]} hints
 * @returns {PasswordHint[]}
 */
export function blockingHints(hints) {
  return hints.filter((x) => !x.ok && !x.advisory);
}

/** @param {string} s */
function utf8Bytes(s) {
  return new TextEncoder().encode(s).length;
}

/**
 * True when the password is a deny-listed word or a base word decorated with digits/symbols
 * ("Password123!" -> letters "password").
 * @param {string} normalized  NFKC-normalised password
 */
export function isCommonPassword(normalized) {
  const lowered = normalized.toLowerCase();
  const letters = lowered.replace(/[^a-z]/g, '');
  return COMMON.has(lowered) || BASE_WORDS.has(lowered) || (letters.length >= 3 && BASE_WORDS.has(letters));
}

/**
 * Requirement checklist for a new password (`blockingHints` must be empty to submit).
 * @param {string} password
 * @param {string} [username]
 * @param {number} [minLength]
 * @param {{ exactMin?: boolean }} [opts]  exactMin: `minLength` is the server's configured
 *   value (otherwise a length of at least SERVER_MIN_LENGTH_FLOOR is only advisory)
 * @returns {PasswordHint[]}
 */
export function passwordHints(password, username = '', minLength = MIN_PASSWORD_LENGTH, opts = {}) {
  const raw = String(password || '');
  const p = raw.normalize('NFKC');
  const min = Math.max(8, Math.floor(minLength) || MIN_PASSWORD_LENGTH);
  const chars = [...p];
  const user = String(username || '').trim().toLowerCase();
  // eslint-disable-next-line no-control-regex
  const control = /[\u0000-\u001f\u007f-\u009f]/.test(raw);
  const lengthOk = chars.length >= min;
  /** @type {PasswordHint[]} */
  const hints = [
    {
      key: 'length',
      ok: lengthOk,
      text: `At least ${min} characters (a short phrase works well)`,
      advisory: !lengthOk && !opts.exactMin && chars.length >= SERVER_MIN_LENGTH_FLOOR ? true : undefined,
    },
    { key: 'distinct', ok: new Set(chars).size >= MIN_DISTINCT_CHARS, text: `At least ${MIN_DISTINCT_CHARS} different characters` },
    { key: 'username', ok: !(user.length >= 3 && p.toLowerCase().includes(user)), text: 'Does not contain your username' },
    { key: 'common', ok: p.length > 0 && !isCommonPassword(p), text: 'Not a common or easily guessed password' },
  ];
  if (utf8Bytes(raw) > MAX_PASSWORD_BYTES) hints.push({ key: 'bytes', ok: false, text: `At most ${MAX_PASSWORD_BYTES} bytes (shorter, or fewer non-English characters)` });
  if (control) hints.push({ key: 'control', ok: false, text: 'No control characters' });
  return hints;
}

/**
 * 0..4 score for the strength meter: requirements first, then length/variety as a bonus.
 * @param {string} password
 * @param {string} [username]
 * @param {number} [minLength]
 */
export function passwordScore(password, username = '', minLength = MIN_PASSWORD_LENGTH) {
  if (!password) return 0;
  const hints = passwordHints(password, username, minLength);
  const unmet = hints.filter((x) => !x.ok).length;
  if (unmet) return Math.max(0, Math.min(2, 4 - unmet - 1));
  const p = String(password);
  const classes = [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].filter((re) => re.test(p)).length;
  const long = [...p].length >= Math.max(14, minLength + 4);
  return long || classes >= 3 ? 4 : 3;
}

/**
 * Random temporary password for admin resets (shown once; the user must change it).
 * Uses an unambiguous alphabet and always satisfies `passwordHints`.
 * @param {number} [length]
 */
export function generateTempPassword(length = 16) {
  const n = Math.max(12, Math.min(64, Math.floor(length)));
  const alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789!@#%*-_';
  for (;;) {
    const bytes = new Uint32Array(n);
    crypto.getRandomValues(bytes);
    let out = '';
    for (const b of bytes) out += alphabet[b % alphabet.length];
    if (passwordHints(out).every((x) => x.ok)) return out;
  }
}
