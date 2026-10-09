// @ts-check
/**
 * Change password (also the forced flow while must_change_password is set). Shows live
 * strength hints; the server's strength rules are authoritative and their messages are shown.
 */

import { h, clear } from '../../shared/dom.js';
import { post, get, ApiError } from '../../shared/api.js';
import { field, input, button } from '../components/form.js';
import { passwordHints, passwordScore, blockingHints, MIN_PASSWORD_LENGTH } from '../lib/password.js';
import { icon } from '../components/icons.js';
import { errorMessage } from '../errors.js';

/** @type {import('../types.js').ViewMount} */
export function mount(container, { app }) {
  const user = app.user();
  const forced = !!(user && user.must_change_password);
  const current = input({ type: 'password', autocomplete: 'current-password', required: true, maxLength: 256 });
  const next = input({ type: 'password', autocomplete: 'new-password', required: true, maxLength: 256 });
  const confirm = input({ type: 'password', autocomplete: 'new-password', required: true, maxLength: 256 });
  const hintsList = h('ul', { class: 'pw-hints', 'aria-label': 'Password requirements' });
  const meter = h('div', { class: 'pw-meter', 'aria-hidden': 'true' }, h('div', { class: 'pw-meter-fill' }));
  const meterText = h('span', { class: 'sr-only', 'aria-live': 'polite' });
  const status = h('div', { class: 'form-status', role: 'alert', hidden: true });
  const submit = button('Change password', { kind: 'gold', type: 'submit' });

  const renderHints = () => {
    const hints = passwordHints(next.value, user ? user.username : '', MIN_PASSWORD_LENGTH);
    clear(hintsList);
    for (const hint of hints) {
      const state = hint.ok ? ' (met)' : hint.advisory ? ' (recommended)' : ' (not met)';
      hintsList.appendChild(h('li', { class: hint.ok ? 'ok' : hint.advisory ? 'advisory' : 'todo' }, icon(hint.ok ? 'check' : 'close', { size: 14 }), h('span', {}, hint.text), h('span', { class: 'sr-only' }, state)));
    }
    const score = passwordScore(next.value, user ? user.username : '');
    const fill = /** @type {HTMLElement} */ (meter.firstElementChild);
    fill.style.width = `${(score / 4) * 100}%`;
    fill.dataset.score = String(score);
    meterText.textContent = next.value ? `Strength ${['very weak', 'weak', 'fair', 'good', 'strong'][score]}` : '';
  };
  next.addEventListener('input', renderHints);
  renderHints();

  const form = h(
    'form',
    { class: 'auth-form', novalidate: true },
    field('Current password', current, { required: true, hint: forced ? 'Use the temporary password you were given.' : undefined }),
    field('New password', next, { required: true }),
    meter,
    meterText,
    hintsList,
    field('Confirm new password', confirm, { required: true }),
    status,
    submit,
  );

  /** @param {string} msg */
  const fail = (msg) => {
    status.textContent = msg;
    status.hidden = false;
  };

  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    status.hidden = true;
    if (!current.value || !next.value) return fail('Fill in all fields.');
    if (next.value !== confirm.value) {
      confirm.focus();
      return fail('The new passwords do not match.');
    }
    if (next.value === current.value) return fail('The new password must be different from the current one.');
    // Length between 8 and the default minimum is left to the server (configurable minimum).
    const unmet = blockingHints(passwordHints(next.value, user ? user.username : ''));
    if (unmet.length) return fail(`The new password is too weak: ${unmet.map((x) => x.text.toLowerCase()).join('; ')}.`);
    submit.disabled = true;
    try {
      await post('/api/auth/change-password', { current_password: current.value, new_password: next.value });
      current.value = '';
      next.value = '';
      confirm.value = '';
      // The server re-issued the session cookie and cleared the flag.
      try {
        const me = await get('/api/auth/me');
        app.setUser(me.user);
      } catch {
        if (user) app.setUser({ ...user, must_change_password: false });
      }
      app.toast('Password changed. Other sessions were signed out.', { kind: 'success' });
      app.continueAfterLogin();
    } catch (err) {
      if (err instanceof ApiError && (err.status === 400 || err.status === 401 || err.status === 403) && err.code !== 'csrf') {
        fail(err.code === 'unauthenticated' ? 'Your session has ended. Please sign in again.' : 'The current password is incorrect.');
      } else if (err instanceof ApiError && err.status === 422) {
        fail(err.message || 'The new password does not meet the requirements.');
      } else {
        fail(errorMessage(err, 'Could not change the password.'));
      }
    } finally {
      submit.disabled = false;
    }
  });

  container.appendChild(
    h(
      'div',
      { class: 'auth-page' },
      h(
        'section',
        { class: 'auth-card glass' },
        h('h1', {}, forced ? 'Choose a new password' : 'Change password'),
        forced ? h('p', { class: 'notice' }, 'For security, you must choose your own password before continuing.') : null,
        form,
        forced ? null : h('p', {}, h('a', { href: '#/projects' }, 'Back to projects')),
      ),
    ),
  );
  queueMicrotask(() => current.focus());
}
