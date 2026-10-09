// @ts-check
/**
 * API keys ("bring your own key"). Every user can save personal keys (GET/PUT/DELETE
 * /api/keys/{provider}, POST …/test) that run only the lectures they generate; admins save
 * server keys for everyone in the Admin page (`serverKeysSection`, /api/admin/keys), which
 * replace the keys in the server's .env file. Keys are write-only: the API returns a masked
 * hint, never the key, and a typed key is never kept outside its masked key input
 * (components/apiKeyCard.js). While personal keys are switched off, keys saved earlier are still
 * listed so their owner can remove them (deleting always works on the server).
 */

import { h, clear } from '../../shared/dom.js';
import { get, put, post, del, ApiError } from '../../shared/api.js';
import { spinner, errorState } from '../components/form.js';
import { confirmDialog } from '../components/modal.js';
import { apiKeyCard } from '../components/apiKeyCard.js';
import { credentialStatus, lastTestText, providerLabel } from '../lib/apiKeys.js';
import { pageHeader } from './common.js';

/** @typedef {import('../types.js').PersonalKeyRow} PersonalKeyRow */
/** @typedef {import('../types.js').ServerKeyRow} ServerKeyRow */
/** @typedef {import('../types.js').CredentialView} CredentialView */
/** @typedef {import('../components/apiKeyCard.js').KeyCardState} KeyCardState */

export const INTRO = 'Use your own API keys. A key you save is stored encrypted, used only for lectures you generate, and never shown again.';

/**
 * The "last test" line of a saved key (null when there is nothing to report).
 * @param {CredentialView | null} view
 */
function testLine(view) {
  const t = lastTestText(view);
  if (!t) return null;
  return h('p', { class: t.ok === false ? 'bad-text small' : 'muted small' }, t.text);
}

/**
 * Card texts for a personal key (pure apart from building nodes; exported for tests).
 * @param {PersonalKeyRow} row
 * @returns {KeyCardState}
 */
export function describePersonal(row) {
  const view = row.personal;
  const unreadable = !!view && view.readable === false;
  /** @type {KeyCardState['lines']} */
  const lines = [];
  if (unreadable) lines.push(h('p', { class: 'small' }, 'This key can no longer be read on the server. Enter it again to keep using it.'));
  lines.push(testLine(view));
  let note;
  if (row.server_available) note = "Without your key, your lectures use the server's key.";
  else if (view && !unreadable) note = 'The server has no key for this engine, so only your key can run it.';
  else note = 'The server has no key for this engine – add yours to use it.';
  lines.push(h('p', { class: 'muted small' }, note));
  return {
    status: credentialStatus(view),
    hint: view && !unreadable ? view.hint : null,
    lines,
    saveLabel: !view ? 'Add key' : unreadable ? 'Re-enter key' : 'Replace',
    canTest: !!view && !unreadable,
    canRemove: !!view,
  };
}

/**
 * Card texts for a personal key saved before personal keys were switched off: removal only.
 * @param {PersonalKeyRow} row
 * @returns {KeyCardState}
 */
export function describeSwitchedOff(row) {
  const view = row.personal;
  return {
    status: view ? { text: 'Not in use', kind: 'muted' } : { text: 'Removed', kind: 'muted' },
    hint: view && view.readable !== false ? view.hint : null,
    lines: view ? [h('p', { class: 'muted small' }, 'Personal keys are turned off, so this key is not used. You can still remove it from the server.')] : [],
    saveLabel: null,
    canTest: false,
    canRemove: !!view,
  };
}

/**
 * Card texts for a server key (admin).
 * @param {ServerKeyRow} row
 * @param {boolean} enabled   keys may be saved in the Studio
 * @returns {KeyCardState}
 */
export function describeServer(row, enabled) {
  const stored = row.stored;
  const unreadable = !!stored && stored.readable === false;
  // A readable key saved in the Studio that the server ignores (saving keys in the Studio is off).
  const ignored = !!stored && !unreadable && row.active_source !== 'stored';
  /** @type {KeyCardState['status']} */
  let status;
  if (unreadable) status = { text: 'Needs re-entry', kind: 'attention' };
  else if (row.active_source === 'stored') status = { text: 'Saved in Studio', kind: 'ok' };
  else if (row.active_source === 'env') status = { text: 'From .env', kind: 'busy' };
  else status = { text: 'Not set', kind: 'muted' };
  /** @type {KeyCardState['lines']} */
  const lines = [];
  if (unreadable) {
    lines.push(h('p', { class: 'small' }, `The key saved in the Studio can no longer be read on the server. Enter it again.${row.env ? ' Until then the .env key is used.' : ''}`));
  }
  if (ignored) {
    lines.push(h('p', { class: 'muted small' }, `A key saved in the Studio (${stored.hint}) is not used while saving keys in the Studio is turned off.`));
  }
  if (row.active_source === 'stored') lines.push(testLine(stored));
  if (row.active_source === 'stored' && row.env) lines.push(h('p', { class: 'muted small' }, "The server's .env file also has a key; it applies again if you remove this one."));
  if (row.active_source === 'env') {
    lines.push(h('p', { class: 'muted small' }, `Comes from the server's .env file.${enabled ? ' A key saved here replaces it for everyone.' : ''}`));
  }
  if (!row.active_source && !unreadable) {
    lines.push(h('p', { class: 'muted small' }, ignored ? 'No .env key for this engine, so the server has no active key.' : 'No server key for this engine.'));
  }
  return {
    status,
    hint: stored && !unreadable && row.active_source === 'stored' ? stored.hint : null,
    lines,
    saveLabel: !enabled ? null : !stored ? 'Add key' : unreadable ? 'Re-enter key' : 'Replace',
    canTest: !!row.active_source && !(row.active_source === 'stored' && unreadable),
    canRemove: !!stored,
  };
}

/**
 * DELETE that treats "already gone" (404) as done.
 * @param {string} path
 */
async function deleteKey(path) {
  try {
    await del(path);
  } catch (err) {
    if (!(err instanceof ApiError && err.status === 404)) throw err;
  }
}

/** @type {import('../types.js').ViewMount} */
export function mount(container, { app }) {
  let destroyed = false;
  const body = h('div', { class: 'api-keys' }, spinner('Loading your API keys…'));
  container.append(pageHeader('API keys', INTRO), body);

  async function load() {
    clear(body);
    body.appendChild(spinner('Loading your API keys…'));
    try {
      /** @type {import('../types.js').PersonalKeysResponse} */
      const data = await get('/api/keys');
      if (destroyed) return;
      render(data);
    } catch (err) {
      if (destroyed) return;
      clear(body);
      body.appendChild(errorState('Could not load your API keys.', () => void load()));
      app.reportError(err);
    }
  }

  /**
   * One provider's card.
   * @param {PersonalKeyRow} row
   * @param {(row: PersonalKeyRow) => KeyCardState} describe
   * @param {boolean} enabled   personal keys are in use on this server
   */
  function keyCard(row, describe, enabled) {
    const label = providerLabel(row);
    const path = `/api/keys/${encodeURIComponent(row.provider)}`;
    return apiKeyCard({
      provider: row.provider,
      label,
      row,
      describe,
      save: (apiKey) => put(path, { api_key: apiKey }),
      test: () => post(`${path}/test`, {}),
      refresh: async () => {
        /** @type {import('../types.js').PersonalKeysResponse} */
        const fresh = await get('/api/keys');
        return (fresh.providers || []).find((p) => p.provider === row.provider) || null;
      },
      remove: async (current) => {
        let message;
        if (!enabled) message = 'The key is deleted from the server. It is not in use while personal keys are turned off.';
        else if (current.server_available) message = "The key is deleted from the server. Your lectures use the server's key again.";
        else message = 'The key is deleted from the server. The server has no key for this engine, so you cannot choose it until you add a key again.';
        const ok = await confirmDialog({ title: `Remove your ${label} key?`, message, confirmLabel: 'Remove key', danger: true });
        if (!ok) return null;
        await deleteKey(path);
        return { ...current, personal: null };
      },
      toast: app.toast,
      reportError: app.reportError,
      onChange: () => app.invalidateMeta(),
    }).el;
  }

  /** @param {import('../types.js').PersonalKeysResponse} data */
  function render(data) {
    clear(body);
    const rows = data && Array.isArray(data.providers) ? data.providers : [];
    if (!data || !data.enabled) {
      body.append(h('p', { class: 'notice' }, "Personal API keys are turned off on this server. Your lectures use the server's keys."));
      // Keys saved before the switch was turned off stay on the server until removed: offer that.
      const saved = rows.filter((row) => row.personal);
      if (saved.length) body.append(h('div', { class: 'card-grid key-grid' }, saved.map((row) => keyCard(row, describeSwitchedOff, false))));
      return;
    }
    if (!rows.length) {
      body.append(h('p', { class: 'muted' }, 'This server lists no providers that accept a key.'));
      return;
    }
    body.append(
      h('div', { class: 'card-grid key-grid' }, rows.map((row) => keyCard(row, describePersonal, true))),
      h('p', { class: 'muted small' }, 'Spending on your own keys does not count toward your daily budget; the per-lecture cost limit still applies. A saved Gemini or OpenAI key also pays for that provider’s voices and images in your lectures.'),
    );
  }

  void load();
  return {
    destroy() {
      destroyed = true;
    },
  };
}

/**
 * The admin's "Server API keys" panel (Admin page). Servers that predate saved keys (404) hide it.
 * @param {HTMLElement} host   panel element to fill
 * @param {import('../types.js').AppContext} app
 * @returns {{ reload: () => Promise<void>, destroy: () => void }}
 */
export function serverKeysSection(host, app) {
  let destroyed = false;

  async function reload() {
    clear(host);
    host.append(h('h2', {}, 'Server API keys'), spinner('Loading server keys…'));
    try {
      /** @type {import('../types.js').ServerKeysResponse} */
      const data = await get('/api/admin/keys');
      if (destroyed) return;
      render(data);
    } catch (err) {
      if (destroyed) return;
      clear(host);
      if (err instanceof ApiError && err.status === 404) {
        host.hidden = true;
        return;
      }
      host.append(h('h2', {}, 'Server API keys'), errorState('Could not load the server keys.', () => void reload()));
      app.reportError(err);
    }
  }

  /** @param {import('../types.js').ServerKeysResponse} data */
  function render(data) {
    clear(host);
    host.hidden = false;
    const enabled = !!(data && data.enabled);
    host.append(
      h('h2', {}, 'Server API keys'),
      h('p', { class: 'muted' }, "Keys saved here are used for everyone's lectures and replace the keys in the server's .env file. They are stored encrypted and never shown again."),
    );
    if (!enabled) host.append(h('p', { class: 'notice warning' }, 'Saving keys in the Studio is turned off on this server, so only the keys in its .env file are used.'));
    const rows = data && Array.isArray(data.providers) ? data.providers : [];
    host.append(
      h(
        'div',
        { class: 'card-grid key-grid' },
        rows.map((row) => {
          const label = providerLabel(row);
          const path = `/api/admin/keys/${encodeURIComponent(row.provider)}`;
          return apiKeyCard({
            provider: row.provider,
            label,
            row,
            describe: (r) => describeServer(r, enabled),
            save: (apiKey) => put(path, { api_key: apiKey }),
            test: () => post(`${path}/test`, {}),
            refresh: async () => {
              /** @type {import('../types.js').ServerKeysResponse} */
              const fresh = await get('/api/admin/keys');
              return (fresh.providers || []).find((p) => p.provider === row.provider) || null;
            },
            remove: async (current) => {
              const ok = await confirmDialog({
                title: `Remove the server's ${label} key?`,
                message: current.env
                  ? "The key saved in the Studio is deleted. The key in the server's .env file then applies again."
                  : "The key saved in the Studio is deleted. The server's .env file has no key for this engine, so only users with their own key can use it.",
                confirmLabel: 'Remove key',
                danger: true,
              });
              if (!ok) return null;
              await deleteKey(path);
              return { ...current, stored: null, active_source: current.env ? 'env' : null };
            },
            toast: app.toast,
            reportError: app.reportError,
            onChange: () => app.invalidateMeta(),
          }).el;
        }),
      ),
    );
  }

  void reload();
  return {
    reload,
    destroy() {
      destroyed = true;
    },
  };
}
