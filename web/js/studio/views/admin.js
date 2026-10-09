// @ts-check
/**
 * Admin: users (create, role, active toggle, daily budget, reset password -> must change on
 * next sign-in), server API keys saved in the Studio (views/apiKeys.js serverKeysSection), the
 * media provider chains (GET /api/admin/media-providers) and per-user usage (GET /api/admin/usage).
 */

import { h, clear } from '../../shared/dom.js';
import { get, post, patch } from '../../shared/api.js';
import { button, field, input, select, checkbox, spinner, errorState, formGrid } from '../components/form.js';
import { openModal, confirmDialog, alertDialog } from '../components/modal.js';
import { generateTempPassword, passwordHints, blockingHints, serverMinLength, MIN_PASSWORD_LENGTH } from '../lib/password.js';
import { formatDate, formatRelative, formatUsd, copyText } from '../util.js';
import { pageHeader } from './common.js';
import { serverKeysSection } from './apiKeys.js';

/** Mirrors aadhi/api/routers/admin.py USERNAME_PATTERN. */
export const USERNAME_RE = /^[A-Za-z0-9][A-Za-z0-9_.-]{2,63}$/;

/**
 * Parse the budget input: '' = default (null), otherwise a non-negative number.
 * @param {string} raw
 * @returns {{ ok: true, value: number | null } | { ok: false, error: string }}
 */
export function parseBudget(raw) {
  const s = String(raw).trim();
  if (s === '') return { ok: true, value: null };
  const n = Number(s);
  if (!Number.isFinite(n) || n < 0) return { ok: false, error: 'Enter a non-negative amount, or leave empty for the default.' };
  if (n > 10000) return { ok: false, error: 'That budget looks too large.' };
  return { ok: true, value: Math.round(n * 100) / 100 };
}

/** Labels of GET /api/admin/media-providers states (aadhi/providers/factory.py media_provider_status). */
export const PROVIDER_STATE_LABELS = /** @type {Record<string, string>} */ ({
  available: 'Available',
  not_configured: 'Not configured',
  cooling: 'Temporarily behind the others',
});

/**
 * One line describing a media provider of the status panel (no secrets: the server redacts reasons).
 * @param {any} p
 * @returns {string}
 */
export function describeMediaProvider(p) {
  const parts = [p.role === 'preferred' ? 'Preferred' : `Backup ${p.order}`, PROVIDER_STATE_LABELS[p.state] || String(p.state || 'unknown')];
  if (p.paid) parts.push('paid per generation');
  if (p.state === 'cooling' && Number(p.cooldown_seconds_left) > 0) parts.push(`for about ${Math.max(1, Math.round(Number(p.cooldown_seconds_left) / 60))} min`);
  return parts.join(' · ');
}

/**
 * The "Media providers" panel: image and video provider chains (state, reason, paid flag).
 * @param {HTMLElement} host
 * @param {any} status GET /api/admin/media-providers, or null when unavailable
 */
export function renderMediaProviders(host, status) {
  clear(host);
  host.append(h('h2', {}, 'Media providers'));
  if (!status) {
    host.append(h('p', { class: 'muted' }, 'Provider status is unavailable.'));
    return;
  }
  for (const [kind, title] of /** @type {const} */ ([['image', 'Generated images'], ['video', 'AI video']])) {
    const chain = status[kind] || { enabled: false, providers: [] };
    const items = (chain.providers || []).map((/** @type {any} */ p) =>
      h(
        'li',
        { class: `media-provider state-${p.state}`, 'data-provider': p.name },
        h('strong', {}, p.label || p.name),
        p.model ? h('span', { class: 'muted small' }, ` (${p.model})`) : null,
        h('div', { class: 'small' }, describeMediaProvider(p)),
        p.reason ? h('div', { class: 'muted small' }, p.reason) : null,
      ),
    );
    host.append(
      h('h3', {}, title),
      chain.enabled ? h('ol', { class: 'media-provider-list' }, items) : h('p', { class: 'muted' }, 'Switched off on this server.'),
    );
  }
  host.append(h('p', { class: 'muted small' }, 'Backups are tried in order when the preferred provider is not configured, asks for payment, is down or returns unusable media; never after a safety refusal. Paid backups bill the server key, or the teacher’s own key.'));
  if (status.cooldowns_source === 'recent worker job events') {
    host.append(h('p', { class: 'muted small media-cooldown-source' }, 'Cooldowns are the ones the job workers recorded in recent job logs; a provider that has recovered since still shows here until its cooldown ends.'));
  }
}

/** @type {import('../types.js').ViewMount} */
export function mount(container, { app }) {
  let destroyed = false;
  const me = app.user();
  const createBtn = button('Create user', { kind: 'gold', icon: 'plus' });
  const usersHost = h('section', { class: 'panel glass' }, spinner('Loading users…'));
  const usageHost = h('section', { class: 'panel glass' });
  const keysHost = h('section', { class: 'panel glass server-keys' });
  const providersHost = h('section', { class: 'panel glass media-providers' });
  container.append(pageHeader('Administration', 'Teacher accounts, budgets and server API keys.', createBtn), usersHost, keysHost, providersHost, usageHost);
  const serverKeys = serverKeysSection(keysHost, app);

  async function loadProviders() {
    const status = await get('/api/admin/media-providers').catch(() => null);
    if (!destroyed) renderMediaProviders(providersHost, status);
  }

  /** @type {Map<number, any>} */
  let usage = new Map();

  async function load() {
    try {
      const [users, adminUsage] = await Promise.all([get('/api/admin/users'), get('/api/admin/usage?days=30').catch(() => null)]);
      if (destroyed) return;
      usage = new Map(((adminUsage && adminUsage.users) || []).map((/** @type {any} */ u) => [u.user_id, u]));
      renderUsers(users.items || []);
      renderUsage(adminUsage);
    } catch (err) {
      if (destroyed) return;
      clear(usersHost);
      usersHost.appendChild(errorState('Could not load users.', () => void load()));
      app.reportError(err);
    }
  }

  /** @param {any[]} users */
  function renderUsers(users) {
    clear(usersHost);
    usersHost.append(h('h2', {}, `Users (${users.length})`));
    const rows = users.map((u) => {
      const self = me && me.id === u.id;
      const role = select({ options: [{ value: 'editor', label: 'Editor' }, { value: 'admin', label: 'Admin' }], value: u.role, ariaLabel: `Role of ${u.username}`, disabled: !!self });
      role.addEventListener('change', async () => {
        const ok = await confirmDialog({ title: 'Change role?', message: `${u.username} becomes ${role.value === 'admin' ? 'an administrator (full access to every project)' : 'an editor (own projects only)'}. Their sessions are signed out.`, confirmLabel: 'Change role' });
        if (!ok) {
          role.value = u.role;
          return;
        }
        await update(u, { role: role.value }, `Role of ${u.username} changed.`);
      });
      const active = checkbox({ label: u.is_active ? 'Active' : 'Disabled', checked: !!u.is_active, disabled: !!self });
      active.input.addEventListener('change', async () => {
        const enable = active.input.checked;
        const ok = await confirmDialog({ title: enable ? 'Enable account?' : 'Disable account?', message: enable ? `${u.username} can sign in again.` : `${u.username} is signed out and cannot sign in. Their projects are kept.`, confirmLabel: enable ? 'Enable' : 'Disable', danger: !enable });
        if (!ok) {
          active.input.checked = !enable;
          return;
        }
        await update(u, { is_active: enable }, enable ? `${u.username} enabled.` : `${u.username} disabled.`);
      });
      const budget = input({ type: 'number', min: 0, step: 0.5, value: u.daily_budget_usd === null || u.daily_budget_usd === undefined ? '' : String(u.daily_budget_usd), placeholder: 'default', ariaLabel: `Daily budget of ${u.username} (USD)` });
      const budgetErr = h('div', { class: 'field-error', role: 'alert', hidden: true });
      const saveBudget = async () => {
        const parsed = parseBudget(budget.value);
        if (!parsed.ok) {
          budgetErr.textContent = parsed.error;
          budgetErr.hidden = false;
          return;
        }
        budgetErr.hidden = true;
        if (parsed.value === (u.daily_budget_usd ?? null)) return;
        await update(u, { daily_budget_usd: parsed.value }, `Budget of ${u.username} saved.`);
      };
      budget.addEventListener('change', () => void saveBudget());
      budget.addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter') void saveBudget();
      });
      // Resetting your own password would end your session at once: use Change password.
      const reset = button('Reset password', { kind: 'outline', small: true, disabled: !!self, title: self ? 'Use “Change password” in your account menu for your own account.' : undefined });
      if (!self) reset.addEventListener('click', () => void resetPassword(u));
      const spend = usage.get(u.id);
      return h(
        'tr',
        { class: u.is_active ? '' : 'muted-row' },
        h('th', { scope: 'row' }, u.username, self ? h('span', { class: 'badge badge-outline' }, 'you') : null, u.must_change_password ? h('div', { class: 'small muted' }, 'must change password') : null),
        h('td', {}, role),
        h('td', {}, active),
        h('td', {}, budget, budgetErr),
        h('td', {}, spend ? `${formatUsd(spend.today_usd)} today · ${formatUsd(spend.total_usd)} / 30 d` : '–'),
        h('td', {}, u.last_login_at ? h('time', { datetime: u.last_login_at, title: formatDate(u.last_login_at) }, formatRelative(u.last_login_at)) : 'never'),
        h('td', {}, reset),
      );
    });
    usersHost.append(
      h(
        'div',
        { class: 'table-wrap' },
        h(
          'table',
          { class: 'table' },
          h('caption', { class: 'sr-only' }, 'Users'),
          h('thead', {}, h('tr', {}, ['User', 'Role', 'Status', 'Daily budget (USD)', 'Spend', 'Last sign-in', ''].map((t) => h('th', { scope: 'col' }, t)))),
          h('tbody', {}, rows),
        ),
      ),
      h('p', { class: 'muted small' }, 'Leave the budget empty to use the server default. Role, status and password changes sign the user out everywhere.'),
    );
  }

  /** @param {any} adminUsage */
  function renderUsage(adminUsage) {
    clear(usageHost);
    usageHost.append(h('h2', {}, 'Spending (last 30 days)'));
    if (!adminUsage) {
      usageHost.append(h('p', { class: 'muted' }, 'Usage data is unavailable.'));
      return;
    }
    const users = (adminUsage.users || []).slice().sort((/** @type {any} */ a, /** @type {any} */ b) => b.total_usd - a.total_usd);
    // `own_key_usd`: the part users paid with their own API keys (not billed to the server).
    const ownTotal = Number(adminUsage.own_key_usd) || 0;
    const showOwn = ownTotal > 0 || users.some((/** @type {any} */ u) => Number(u.own_key_usd) > 0);
    // today_usd is server-billed (what the daily budget measures); 30 days counts every payer.
    const columns = ['User', showOwn ? 'Today (server)' : 'Today', '30 days', ...(showOwn ? ['Own keys, 30 days'] : [])];
    usageHost.append(
      h('p', {}, `Total: ${formatUsd(adminUsage.total_usd)}`, showOwn ? h('span', { class: 'muted' }, ` (${formatUsd(ownTotal)} of it paid with users’ own API keys)`) : null),
      users.length
        ? h(
            'table',
            { class: 'table compact' },
            h('thead', {}, h('tr', {}, columns.map((t) => h('th', { scope: 'col' }, t)))),
            h(
              'tbody',
              {},
              users.map((/** @type {any} */ u) =>
                h('tr', {}, h('th', { scope: 'row' }, u.username), h('td', {}, formatUsd(u.today_usd)), h('td', {}, formatUsd(u.total_usd)), showOwn ? h('td', {}, formatUsd(Number(u.own_key_usd) || 0)) : null),
              ),
            ),
          )
        : h('p', { class: 'muted' }, 'No spending yet.'),
    );
  }

  /**
   * @param {any} u
   * @param {Record<string, any>} body
   * @param {string} message
   */
  async function update(u, body, message) {
    try {
      await patch(`/api/admin/users/${u.id}`, body);
      app.toast(message, { kind: 'success' });
    } catch (err) {
      app.reportError(err, 'Could not update the user.');
    }
    await load();
  }

  /**
   * Strength hints with the server's configured minimum when /api/meta publishes it.
   * @param {string} pw
   * @param {string} username
   */
  async function strengthHints(pw, username) {
    const meta = await app.meta().catch(() => null);
    const min = serverMinLength(meta);
    return passwordHints(pw, username, min ?? MIN_PASSWORD_LENGTH, { exactMin: min !== null });
  }

  /** @param {any} u */
  async function resetPassword(u) {
    const temp = generateTempPassword();
    const pw = input({ value: temp, maxLength: 256, autocomplete: 'off', spellcheck: 'false' });
    const modal = openModal({
      title: `Reset password for ${u.username}`,
      description: 'Give this temporary password to the user privately. They must choose a new one at their next sign-in, and all their sessions end now.',
      body: field('Temporary password', pw),
      actions: [
        { label: 'Cancel', kind: 'outline', value: null },
        {
          label: 'Reset password',
          kind: 'danger',
          onClick: async () => {
            const unmet = blockingHints(await strengthHints(pw.value, u.username));
            if (unmet.length) throw new Error(`Too weak: ${unmet.map((x) => x.text.toLowerCase()).join('; ')}.`);
            await patch(`/api/admin/users/${u.id}`, { password: pw.value, must_change_password: true });
            return pw.value;
          },
        },
      ],
    });
    const value = await modal.result;
    if (typeof value !== 'string') return;
    const copied = await copyText(value);
    await alertDialog({ title: 'Password reset', message: copied ? 'The temporary password was copied to your clipboard. It will not be shown again.' : 'Copy the temporary password now; it will not be shown again.', details: copied ? [] : [value] });
    await load();
  }

  createBtn.addEventListener('click', async () => {
    const username = input({ maxLength: 64, autocomplete: 'off', spellcheck: 'false' });
    const pw = input({ value: generateTempPassword(), maxLength: 256, autocomplete: 'off', spellcheck: 'false' });
    const role = select({ options: [{ value: 'editor', label: 'Editor (teacher)' }, { value: 'admin', label: 'Admin' }], value: 'editor' });
    const modal = openModal({
      title: 'Create user',
      description: 'New users must change this temporary password at their first sign-in.',
      body: formGrid(field('Username', username, { required: true, hint: '3–64 letters, digits, dots, dashes or underscores; starts with a letter or digit.' }), field('Temporary password', pw, { required: true }), field('Role', role)),
      actions: [
        { label: 'Cancel', kind: 'outline', value: null },
        {
          label: 'Create',
          kind: 'gold',
          submit: true,
          onClick: async () => {
            const name = username.value.trim();
            if (!USERNAME_RE.test(name)) throw new Error('Choose a username of 3–64 letters, digits, dots, dashes or underscores, starting with a letter or digit.');
            const unmet = blockingHints(await strengthHints(pw.value, name));
            if (unmet.length) throw new Error(`Password too weak: ${unmet.map((x) => x.text.toLowerCase()).join('; ')}.`);
            await post('/api/admin/users', { username: name, password: pw.value, role: role.value });
            return { name, password: pw.value };
          },
        },
      ],
    });
    const created = await modal.result;
    if (!created) return;
    const copied = await copyText(created.password);
    app.toast(copied ? `User ${created.name} created; temporary password copied.` : `User ${created.name} created.`, { kind: 'success' });
    await load();
  });

  void load();
  void loadProviders();
  return {
    destroy() {
      destroyed = true;
      serverKeys.destroy();
    },
  };
}
