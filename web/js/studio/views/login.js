// @ts-check
/**
 * Sign-in view. Errors are deliberately generic (no hint whether the username exists);
 * rate limiting (429) disables the form with a countdown from Retry-After.
 */

import { h } from '../../shared/dom.js';
import { post, ApiError } from '../../shared/api.js';
import { field, input, button } from '../components/form.js';
import { brandMark } from '../components/icons.js';
import { errorMessage } from '../errors.js';
import { APP_SUBTITLE, APP_TITLE } from '../brand.js';

/** @type {import('../types.js').ViewMount} */
export function mount(container, { app }) {
  const username = input({ autocomplete: 'username', required: true, maxLength: 64, spellcheck: 'false' });
  const password = input({ type: 'password', autocomplete: 'current-password', required: true, maxLength: 256 });
  const reveal = button('Show password', { kind: 'ghost', small: true, ariaPressed: false });
  reveal.addEventListener('click', () => {
    const show = password.type === 'password';
    password.type = show ? 'text' : 'password';
    reveal.setAttribute('aria-pressed', show ? 'true' : 'false');
    reveal.querySelector('.btn-label')?.replaceChildren(show ? 'Hide password' : 'Show password');
  });
  const status = h('div', { class: 'form-status', role: 'alert', hidden: true });
  const submit = button('Sign in', { kind: 'gold', type: 'submit', icon: 'logout' });
  /** @type {ReturnType<typeof setInterval> | null} */
  let countdown = null;

  /** @param {string} msg */
  const showError = (msg) => {
    status.textContent = msg;
    status.hidden = false;
  };

  /** @param {number} seconds */
  const lockFor = (seconds) => {
    let left = Math.max(1, Math.ceil(seconds));
    submit.disabled = true;
    const tick = () => {
      showError(`Too many sign-in attempts. Try again in ${left} s.`);
      left -= 1;
      if (left < 0) {
        if (countdown) clearInterval(countdown);
        countdown = null;
        submit.disabled = false;
        status.hidden = true;
      }
    };
    tick();
    countdown = setInterval(tick, 1000);
  };

  const form = h(
    'form',
    { class: 'auth-form', novalidate: true, 'aria-describedby': undefined },
    field('Username', username, { required: true }),
    field('Password', password, { required: true }),
    h('div', { class: 'row space-between' }, reveal),
    status,
    submit,
  );
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    status.hidden = true;
    const u = username.value.trim();
    const p = password.value;
    if (!u || !p) {
      showError('Enter your username and password.');
      (u ? password : username).focus();
      return;
    }
    submit.disabled = true;
    submit.setAttribute('aria-busy', 'true');
    try {
      const res = await post('/api/auth/login', { username: u, password: p });
      password.value = '';
      app.setUser(res.user);
      app.continueAfterLogin();
    } catch (err) {
      password.value = '';
      if (err instanceof ApiError && err.status === 429) {
        lockFor(err.retryAfter || 60);
        return;
      }
      if (err instanceof ApiError && (err.status === 401 || err.status === 400 || err.status === 422)) {
        showError('Invalid username or password.');
      } else {
        showError(errorMessage(err, 'Sign-in failed. Please try again.'));
      }
      password.focus();
    } finally {
      submit.removeAttribute('aria-busy');
      if (!countdown) submit.disabled = false;
    }
  });

  container.appendChild(
    h(
      'div',
      { class: 'auth-page' },
      h(
        'section',
        { class: 'auth-card glass' },
        h('div', { class: 'auth-brand' }, brandMark(72), h('div', {}, h('p', { class: 'auth-app' }, APP_TITLE), h('p', { class: 'auth-sub' }, APP_SUBTITLE))),
        h('h1', {}, 'Sign in'),
        h('p', { class: 'muted' }, 'Teachers sign in to create and edit lectures. Students watch through share links and never need an account.'),
        form,
      ),
    ),
  );
  queueMicrotask(() => username.focus());
  return {
    destroy() {
      if (countdown) clearInterval(countdown);
    },
  };
}
