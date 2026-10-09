// @ts-check
/**
 * Accessible modal dialogs: role=dialog + aria-modal, labelled by the title, focus moved
 * into the dialog and trapped (Tab/Shift+Tab), Escape and backdrop close when dismissible,
 * the app root is made `inert` while open, and focus returns to the opener on close.
 * Stacked dialogs are supported (only the top one handles keys).
 * While an async action runs (busy), Escape, the close button and backdrop clicks do not
 * dismiss the dialog, so the action's result (e.g. a created job) always reaches the caller.
 * Programmatic `close()` (route changes) still works.
 */

import { h, clear } from '../../shared/dom.js';
import { uid } from '../util.js';
import { button, field, textarea as textareaInput, input as textInput } from './form.js';

/**
 * @typedef {object} ModalAction
 * @property {string} label
 * @property {import('./form.js').ButtonKind} [kind]
 * @property {any} [value]                         resolved value when this action closes the modal
 * @property {(modal: ModalHandle) => any} [onClick]  return false to keep the modal open; a
 *   Promise disables the buttons until it settles; a returned non-undefined value (other than
 *   false/true) becomes the result
 * @property {boolean} [autofocus]
 * @property {boolean} [submit]                    triggered by Enter in single-line inputs
 */

/**
 * @typedef {object} ModalHandle
 * @property {HTMLElement} el        the dialog element
 * @property {HTMLElement} body
 * @property {(value?: any) => void} close
 * @property {Promise<any>} result   resolves with the close value (null when dismissed)
 * @property {(busy: boolean) => void} setBusy
 * @property {(message: string | null) => void} setError
 */

/** @type {ModalHandle[]} */
const stack = [];

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * @param {HTMLElement} root
 * @returns {HTMLElement[]}
 */
function focusables(root) {
  return /** @type {HTMLElement[]} */ (Array.from(root.querySelectorAll(FOCUSABLE))).filter(
    (el) => !el.hasAttribute('inert') && !el.closest('[hidden]'),
  );
}

/** Root element made inert while a modal is open. */
function appRoot() {
  return /** @type {HTMLElement | null} */ (document.querySelector('[data-app-root]'));
}

/**
 * Open a modal dialog.
 * @param {{ title: string, body: import('../../shared/dom.js').Child, actions?: ModalAction[], dismissible?: boolean,
 *   size?: 'sm' | 'md' | 'lg' | 'xl', onClose?: (value: any) => void, initialFocus?: HTMLElement | null, description?: string,
 *   returnFocus?: () => HTMLElement | null }} opts
 *   `returnFocus`: where focus goes on close when the element that opened the dialog is gone (the page was redrawn
 *   meanwhile); without it, or when it finds nothing, focus is not moved
 * @returns {ModalHandle}
 */
export function openModal(opts) {
  const titleId = uid('dlg-title');
  const opener = /** @type {HTMLElement | null} */ (document.activeElement);
  const dismissible = opts.dismissible !== false;
  const titleEl = h('h2', { class: 'modal-title' }, opts.title);
  titleEl.setAttribute('id', titleId);
  const errorEl = h('div', { class: 'modal-error', role: 'alert', hidden: true });
  const body = h('div', { class: 'modal-body' }, opts.body);
  const footer = h('div', { class: 'modal-footer' });
  const closeBtn = dismissible ? button('Close dialog', { kind: 'ghost', iconOnly: true, icon: 'close', className: 'modal-close' }) : null;
  const dialog = h(
    'div',
    { class: ['modal', `modal-${opts.size || 'md'}`], role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': titleId, tabindex: '-1' },
    h('div', { class: 'modal-header' }, titleEl, closeBtn),
    opts.description ? h('p', { class: 'modal-description' }, opts.description) : null,
    errorEl,
    body,
    footer,
  );
  const backdrop = h('div', { class: 'modal-backdrop' }, dialog);

  /** @type {(v: any) => void} */
  let resolveResult = () => {};
  /** @type {Promise<any>} */
  const result = new Promise((resolve) => {
    resolveResult = resolve;
  });
  let closed = false;
  let busy = false;
  /** @type {HTMLButtonElement[]} */
  const buttons = [];

  /** @type {ModalHandle} */
  const handle = {
    el: dialog,
    body,
    result,
    close(value = null) {
      if (closed) return;
      closed = true;
      document.removeEventListener('keydown', onKey, true);
      backdrop.remove();
      const idx = stack.indexOf(handle);
      if (idx >= 0) stack.splice(idx, 1);
      const root = appRoot();
      if (root && stack.length === 0) root.removeAttribute('inert');
      const back = opener && opener.isConnected ? opener : opts.returnFocus ? opts.returnFocus() : null;
      if (back && typeof back.focus === 'function') back.focus();
      if (opts.onClose) opts.onClose(value);
      resolveResult(value);
    },
    setBusy(value) {
      busy = !!value;
      dialog.setAttribute('aria-busy', busy ? 'true' : 'false');
      for (const b of buttons) b.disabled = busy;
      if (closeBtn) closeBtn.disabled = busy;
    },
    setError(message) {
      errorEl.textContent = message || '';
      errorEl.hidden = !message;
    },
  };

  /** @param {ModalAction} action */
  const runAction = async (action) => {
    if (!action.onClick) {
      handle.close(action.value ?? null);
      return;
    }
    handle.setError(null);
    let out;
    try {
      const maybe = action.onClick(handle);
      if (maybe && typeof maybe.then === 'function') {
        handle.setBusy(true);
        out = await maybe;
      } else {
        out = maybe;
      }
    } catch (err) {
      handle.setBusy(false);
      handle.setError(err instanceof Error ? err.message : String(err));
      return;
    }
    if (closed) return;
    handle.setBusy(false);
    if (out === false) return;
    handle.close(out === undefined || out === true ? action.value ?? null : out);
  };

  for (const action of opts.actions || []) {
    const b = button(action.label, { kind: action.kind || 'outline', onClick: () => void runAction(action) });
    if (action.submit) b.dataset.submit = '1';
    buttons.push(b);
    footer.appendChild(b);
  }
  if (!buttons.length) footer.hidden = true;

  /** @param {KeyboardEvent} ev */
  function onKey(ev) {
    if (stack[stack.length - 1] !== handle) return;
    if (ev.key === 'Escape' && dismissible) {
      ev.preventDefault();
      ev.stopPropagation();
      if (!busy) handle.close(null);
      return;
    }
    if (ev.key === 'Tab') {
      const items = focusables(dialog);
      if (!items.length) {
        ev.preventDefault();
        dialog.focus();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const active = /** @type {HTMLElement | null} */ (document.activeElement);
      if (ev.shiftKey && (active === first || !dialog.contains(active))) {
        ev.preventDefault();
        last.focus();
      } else if (!ev.shiftKey && (active === last || !dialog.contains(active))) {
        ev.preventDefault();
        first.focus();
      }
      return;
    }
    if (ev.key === 'Enter' && !ev.shiftKey && ev.target instanceof HTMLInputElement && ev.target.type !== 'checkbox') {
      const submitIndex = (opts.actions || []).findIndex((a) => a.submit);
      if (submitIndex >= 0 && !buttons[submitIndex].disabled) {
        ev.preventDefault();
        void runAction(/** @type {ModalAction} */ ((opts.actions || [])[submitIndex]));
      }
    }
  }

  if (closeBtn)
    closeBtn.addEventListener('click', () => {
      if (!busy) handle.close(null);
    });
  if (dismissible) {
    backdrop.addEventListener('mousedown', (ev) => {
      if (ev.target === backdrop && !busy) handle.close(null);
    });
  }
  document.addEventListener('keydown', onKey, true);
  const root = appRoot();
  if (root) root.setAttribute('inert', '');
  document.body.appendChild(backdrop);
  stack.push(handle);

  const auto = (opts.actions || []).findIndex((a) => a.autofocus);
  const target =
    opts.initialFocus ||
    /** @type {HTMLElement | null} */ (body.querySelector('[autofocus], input:not([type="hidden"]), select, textarea')) ||
    (auto >= 0 ? buttons[auto] : null) ||
    buttons[buttons.length - 1] ||
    dialog;
  queueMicrotask(() => target.focus());
  return handle;
}

/** Close every open modal (route changes). */
export function closeAllModals() {
  for (const m of stack.slice().reverse()) m.close(null);
}

/**
 * Yes/no confirmation.
 * @param {{ title: string, message: string, confirmLabel?: string, cancelLabel?: string, danger?: boolean, details?: string[] }} opts
 * @returns {Promise<boolean>}
 */
export async function confirmDialog(opts) {
  const body = h(
    'div',
    {},
    h('p', {}, opts.message),
    opts.details && opts.details.length ? h('ul', { class: 'modal-list' }, opts.details.map((d) => h('li', {}, d))) : null,
  );
  const m = openModal({
    title: opts.title,
    body,
    size: 'sm',
    actions: [
      { label: opts.cancelLabel || 'Cancel', kind: 'outline', value: false, autofocus: !!opts.danger },
      { label: opts.confirmLabel || 'Confirm', kind: opts.danger ? 'danger' : 'gold', value: true, autofocus: !opts.danger },
    ],
  });
  return (await m.result) === true;
}

/**
 * Ask for one text value.
 * @param {{ title: string, label: string, value?: string, placeholder?: string, multiline?: boolean, required?: boolean,
 *   maxLength?: number, confirmLabel?: string, hint?: string, validate?: (v: string) => string | null, message?: string }} opts
 * @returns {Promise<string | null>}
 */
export async function promptDialog(opts) {
  const control = opts.multiline
    ? textareaInput({ value: opts.value || '', placeholder: opts.placeholder, maxLength: opts.maxLength, rows: 5 })
    : textInput({ value: opts.value || '', placeholder: opts.placeholder, maxLength: opts.maxLength });
  const wrap = field(opts.label, control, { required: opts.required, hint: opts.hint });
  const m = openModal({
    title: opts.title,
    body: h('div', {}, opts.message ? h('p', {}, opts.message) : null, wrap),
    size: 'md',
    initialFocus: control,
    actions: [
      { label: 'Cancel', kind: 'outline', value: null },
      {
        label: opts.confirmLabel || 'OK',
        kind: 'gold',
        submit: !opts.multiline,
        onClick: () => {
          const v = control.value.trim();
          if (opts.required && !v) {
            wrap.setError('This field is required.');
            return false;
          }
          const problem = opts.validate ? opts.validate(v) : null;
          if (problem) {
            wrap.setError(problem);
            return false;
          }
          return v;
        },
      },
    ],
  });
  const v = await m.result;
  return typeof v === 'string' ? v : null;
}

/**
 * Informational dialog.
 * @param {{ title: string, message: string, details?: string[], size?: 'sm' | 'md' | 'lg' }} opts
 * @returns {Promise<void>}
 */
export async function alertDialog(opts) {
  const m = openModal({
    title: opts.title,
    size: opts.size || 'sm',
    body: h('div', {}, h('p', {}, opts.message), opts.details && opts.details.length ? h('ul', { class: 'modal-list' }, opts.details.map((d) => h('li', {}, d))) : null),
    actions: [{ label: 'OK', kind: 'gold', value: true, autofocus: true }],
  });
  await m.result;
}

/**
 * Pick one of several actions.
 * @param {{ title: string, message: string, choices: { label: string, value: string, kind?: import('./form.js').ButtonKind }[], details?: string[] }} opts
 * @returns {Promise<string | null>}
 */
export async function choiceDialog(opts) {
  const m = openModal({
    title: opts.title,
    size: 'md',
    body: h('div', {}, h('p', {}, opts.message), opts.details && opts.details.length ? h('ul', { class: 'modal-list' }, opts.details.map((d) => h('li', {}, d))) : null),
    actions: opts.choices.map((c) => ({ label: c.label, kind: c.kind || 'outline', value: c.value })),
  });
  const v = await m.result;
  return typeof v === 'string' ? v : null;
}

/**
 * Replace a modal body's content (multi-step dialogs).
 * @param {ModalHandle} modal
 * @param {import('../../shared/dom.js').Child} content
 */
export function setModalBody(modal, content) {
  clear(modal.body);
  modal.body.append(...[content].flat().filter((c) => c instanceof Node));
}
