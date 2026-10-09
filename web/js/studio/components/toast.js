// @ts-check
/**
 * Toast notifications in a polite live region (errors use role=alert). Toasts pause their
 * timeout while hovered or focused and can carry one action button.
 */

import { h } from '../../shared/dom.js';
import { icon } from './icons.js';

/** @typedef {'info' | 'success' | 'warning' | 'error'} ToastKind */
/**
 * @typedef {object} ToastOptions
 * @property {ToastKind} [kind]
 * @property {number} [timeout]   ms; 0 = sticky (errors default to 8 s)
 * @property {{ label: string, onClick: () => void }} [action]
 */

/** @type {HTMLElement | null} */
let region = null;
const MAX_TOASTS = 4;

/**
 * Create (or adopt) the toast region. Called by app.js; lazily created otherwise.
 * @param {HTMLElement} [parent]
 */
export function initToasts(parent = document.body) {
  if (region && region.isConnected) return region;
  region = h('div', { class: 'toasts', role: 'region', 'aria-label': 'Notifications', 'aria-live': 'polite', 'aria-relevant': 'additions' });
  parent.appendChild(region);
  return region;
}

/**
 * Show a toast. Returns a function that dismisses it.
 * @param {string} message
 * @param {ToastOptions} [opts]
 * @returns {() => void}
 */
export function toast(message, opts = {}) {
  const host = region && region.isConnected ? region : initToasts();
  const kind = opts.kind || 'info';
  const timeout = opts.timeout ?? (kind === 'error' ? 8000 : 4500);
  const iconName = { info: 'info', success: 'check', warning: 'warning', error: 'error' }[kind];
  /** @type {ReturnType<typeof setTimeout> | null} */
  let timer = null;
  const dismiss = () => {
    if (timer) clearTimeout(timer);
    el.classList.add('toast-leaving');
    setTimeout(() => el.remove(), 180);
  };
  const actionBtn = opts.action
    ? h('button', { type: 'button', class: 'btn btn-ghost btn-sm toast-action', onClick: () => { /** @type {any} */ (opts.action).onClick(); dismiss(); } }, opts.action.label)
    : null;
  const closeBtn = h('button', { type: 'button', class: 'btn btn-ghost btn-icon btn-sm toast-close', 'aria-label': 'Dismiss notification', onClick: dismiss }, icon('close', { size: 14 }));
  const el = h(
    'div',
    { class: ['toast', `toast-${kind}`], role: kind === 'error' ? 'alert' : 'status' },
    icon(iconName),
    h('div', { class: 'toast-message' }, message),
    actionBtn,
    closeBtn,
  );
  const arm = () => {
    if (timeout > 0) timer = setTimeout(dismiss, timeout);
  };
  const disarm = () => {
    if (timer) clearTimeout(timer);
    timer = null;
  };
  el.addEventListener('mouseenter', disarm);
  el.addEventListener('mouseleave', arm);
  el.addEventListener('focusin', disarm);
  el.addEventListener('focusout', arm);
  host.appendChild(el);
  while (host.children.length > MAX_TOASTS) host.firstElementChild?.remove();
  arm();
  return dismiss;
}
