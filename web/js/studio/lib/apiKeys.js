// @ts-check
/**
 * "Bring your own API key" helpers (GET/PUT/DELETE /api/keys and /api/admin/keys): client-side
 * key checks that mirror the server's (`aadhi/credentials.py` validate_api_key: stripped,
 * 16–512 letters, digits, '-' or '_'; the server stays authoritative), status texts for a
 * saved key, and scrubbing of a typed key from any message before it is shown. A key is only
 * ever held in its masked key input; nothing here stores or logs it.
 */

import { pyStrip, LLM_ENGINE_LABELS } from './optionsForm.js';
import { formatRelative } from '../util.js';

/** Providers a key can be saved for (mirrors aadhi/credentials.py KEY_PROVIDERS). */
export const KEY_PROVIDERS = /** @type {const} */ (['gemini', 'openai', 'anthropic']);

/** Accepted key length in characters, after stripping surrounding whitespace. */
export const API_KEY_LIMITS = Object.freeze({ min: 16, max: 512 });

/** The characters the providers' keys use (mirrors aadhi/credentials.py _KEY_CHARS). */
const KEY_CHARS = /^[A-Za-z0-9_-]+$/;

/** Whitespace, control and invisible formatting characters: never part of a real key. */
// eslint-disable-next-line no-control-regex
const KEY_BAD_CHARS = /[\s\u0000-\u001f\u007f-\u009f­​-‏⁠-⁤﻿]/u;

/**
 * Check a typed key the way the server does before sending it.
 * @param {string} raw
 * @returns {{ ok: true, value: string } | { ok: false, error: string }}
 */
export function validateApiKey(raw) {
  const key = pyStrip(String(raw ?? ''));
  if (!key) return { ok: false, error: 'Paste your API key.' };
  const length = [...key].length;
  if (KEY_BAD_CHARS.test(key)) return { ok: false, error: 'The key contains spaces or invisible characters. Copy it again from your provider.' };
  if (!KEY_CHARS.test(key)) return { ok: false, error: "An API key contains only letters, digits, '-' and '_'. Copy it again from your provider." };
  if (length < API_KEY_LIMITS.min) return { ok: false, error: `That is too short for an API key (at least ${API_KEY_LIMITS.min} characters).` };
  if (length > API_KEY_LIMITS.max) return { ok: false, error: `That is too long for an API key (at most ${API_KEY_LIMITS.max} characters).` };
  return { ok: true, value: key };
}

/**
 * Remove every occurrence of the given secrets from a message (server and network errors are
 * shown to the user; a typed key must never be echoed back on screen).
 * @param {string} message
 * @param {Array<string | null | undefined>} secrets
 * @returns {string}
 */
export function scrubSecret(message, secrets) {
  let out = String(message ?? '');
  const list = secrets
    .map((s) => (s === null || s === undefined ? '' : String(s)))
    .filter((s) => s.length >= 4)
    .sort((a, b) => b.length - a.length);
  for (const secret of list) out = out.split(secret).join('[key hidden]');
  return out;
}

/**
 * Status pill of a saved key.
 * @param {import('../types.js').CredentialView | null | undefined} view
 * @returns {{ text: string, kind: 'ok' | 'attention' | 'muted' }}
 */
export function credentialStatus(view) {
  if (!view) return { text: 'Not set', kind: 'muted' };
  if (view.readable === false) return { text: 'Needs re-entry', kind: 'attention' };
  return { text: 'Saved', kind: 'ok' };
}

/**
 * The last key test, as shown under a saved key (null when there is no readable key).
 * `last_error` wins: it is only kept while the latest test failed.
 * @param {import('../types.js').CredentialView | null | undefined} view
 * @param {number} [now]
 * @returns {{ text: string, ok: boolean | null } | null}
 */
export function lastTestText(view, now = Date.now()) {
  if (!view || view.readable === false) return null;
  if (view.last_error) return { text: `Last test failed: ${view.last_error}`, ok: false };
  if (view.last_verified_at) return { text: `Last tested ${formatRelative(view.last_verified_at, now)}`, ok: true };
  return { text: 'Not tested yet', ok: null };
}

/**
 * Display name of a key provider (the server label, else the built-in engine name).
 * @param {{ provider: string, label?: string | null }} row
 */
export function providerLabel(row) {
  if (row.label) return row.label;
  return /** @type {Record<string, string>} */ (LLM_ENGINE_LABELS)[row.provider] || row.provider;
}
