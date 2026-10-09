// @ts-check
/**
 * Accessible form building blocks (labels wrap controls; hints/errors are wired with
 * aria-describedby; invalid controls get aria-invalid). All text goes through textContent.
 */

import { h } from '../../shared/dom.js';
import { uid } from '../util.js';
import { icon } from './icons.js';

/**
 * @typedef {object} FieldOptions
 * @property {string} [hint]
 * @property {boolean} [required]
 * @property {string} [className]
 * @property {boolean} [inline]   label and control on one row (checkbox style)
 */

/**
 * Labelled field wrapper. Returns the wrapper; `setError(msg)` on the returned object shows
 * an inline error and toggles aria-invalid on the control.
 * @param {string} label
 * @param {HTMLElement} control
 * @param {FieldOptions} [opts]
 * @returns {HTMLElement & { setError: (msg: string | null) => void, control: HTMLElement }}
 */
export function field(label, control, opts = {}) {
  const hintId = opts.hint ? uid('hint') : null;
  const errId = uid('err');
  const describedBy = [hintId, errId].filter(Boolean).join(' ');
  const existing = control.getAttribute('aria-describedby');
  control.setAttribute('aria-describedby', existing ? `${existing} ${describedBy}` : describedBy);
  if (opts.required) control.setAttribute('aria-required', 'true');
  const errorEl = h('div', { class: 'field-error', role: 'alert', hidden: true });
  errorEl.setAttribute('id', errId);
  /** @type {HTMLElement | null} */
  let hintEl = null;
  if (hintId) {
    hintEl = h('div', { class: 'field-hint', text: opts.hint });
    hintEl.setAttribute('id', hintId);
  }
  const labelEl = h(
    'label',
    { class: ['field', opts.inline ? 'field-inline' : '', opts.className || ''] },
    opts.inline ? [control, h('span', { class: 'field-label' }, label)] : [h('span', { class: 'field-label' }, label, opts.required ? h('span', { class: 'req', 'aria-hidden': 'true', text: ' *' }) : null), control],
  );
  const wrap = /** @type {HTMLElement & { setError: (msg: string | null) => void, control: HTMLElement }} */ (
    /** @type {unknown} */ (h('div', { class: ['field-wrap', opts.inline ? 'field-wrap-inline' : ''] }, labelEl, hintEl, errorEl))
  );
  wrap.control = control;
  wrap.setError = (msg) => {
    if (msg) {
      errorEl.textContent = msg;
      errorEl.hidden = false;
      control.setAttribute('aria-invalid', 'true');
    } else {
      errorEl.textContent = '';
      errorEl.hidden = true;
      control.removeAttribute('aria-invalid');
    }
  };
  return wrap;
}

/**
 * Labelled group for composite controls (chip lists, option lists) that cannot live inside a
 * <label>. Uses role=group + aria-labelledby.
 * @param {string} label
 * @param {import('../../shared/dom.js').Child} content
 * @param {{ hint?: string, className?: string }} [opts]
 */
export function group(label, content, opts = {}) {
  const labelId = uid('grp');
  const labelEl = h('div', { class: 'field-label' }, label);
  labelEl.setAttribute('id', labelId);
  const el = h(
    'div',
    { class: ['field-wrap', 'field-group', opts.className || ''], role: 'group', 'aria-labelledby': labelId },
    labelEl,
    content,
    opts.hint ? h('div', { class: 'field-hint', text: opts.hint }) : null,
  );
  return el;
}

/**
 * @typedef {object} InputOptions
 * @property {string} [value]
 * @property {string} [type]
 * @property {string} [placeholder]
 * @property {number} [maxLength]
 * @property {boolean} [required]
 * @property {boolean} [disabled]
 * @property {string} [autocomplete]
 * @property {string} [className]
 * @property {Record<string, string>} [dataset]
 * @property {(value: string, ev: Event) => void} [onInput]
 * @property {(value: string, ev: Event) => void} [onChange]
 * @property {string | number} [min]
 * @property {string | number} [max]
 * @property {string | number} [step]
 * @property {string} [ariaLabel]
 * @property {string} [inputMode]
 * @property {string} [spellcheck]
 */

/**
 * @param {InputOptions} [opts]
 * @returns {HTMLInputElement}
 */
export function input(opts = {}) {
  const el = h('input', {
    class: ['input', opts.className || ''],
    type: opts.type || 'text',
    placeholder: opts.placeholder,
    maxlength: opts.maxLength !== undefined ? String(opts.maxLength) : undefined,
    required: opts.required,
    disabled: opts.disabled,
    autocomplete: opts.autocomplete,
    min: opts.min !== undefined ? String(opts.min) : undefined,
    max: opts.max !== undefined ? String(opts.max) : undefined,
    step: opts.step !== undefined ? String(opts.step) : undefined,
    'aria-label': opts.ariaLabel,
    inputmode: opts.inputMode,
    spellcheck: opts.spellcheck,
    dataset: opts.dataset,
  });
  el.value = opts.value ?? '';
  if (opts.onInput) {
    const fn = opts.onInput;
    el.addEventListener('input', (ev) => fn(el.value, ev));
  }
  if (opts.onChange) {
    const fn = opts.onChange;
    el.addEventListener('change', (ev) => fn(el.value, ev));
  }
  return el;
}

/**
 * @param {InputOptions & { rows?: number, mono?: boolean }} [opts]
 * @returns {HTMLTextAreaElement}
 */
export function textarea(opts = {}) {
  const el = h('textarea', {
    class: ['input', 'textarea', opts.mono ? 'mono' : '', opts.className || ''],
    rows: String(opts.rows || 3),
    placeholder: opts.placeholder,
    maxlength: opts.maxLength !== undefined ? String(opts.maxLength) : undefined,
    required: opts.required,
    disabled: opts.disabled,
    'aria-label': opts.ariaLabel,
    spellcheck: opts.mono ? 'false' : opts.spellcheck,
    dataset: opts.dataset,
  });
  el.value = opts.value ?? '';
  if (opts.onInput) {
    const fn = opts.onInput;
    el.addEventListener('input', (ev) => fn(el.value, ev));
  }
  if (opts.onChange) {
    const fn = opts.onChange;
    el.addEventListener('change', (ev) => fn(el.value, ev));
  }
  if (opts.mono) el.addEventListener('keydown', codeTabHandler);
  return el;
}

/**
 * Tab inserts spaces in code editors; Escape then Tab leaves the field (keyboard trap guard).
 * @param {KeyboardEvent} ev
 */
function codeTabHandler(ev) {
  const el = /** @type {HTMLTextAreaElement} */ (ev.currentTarget);
  if (ev.key === 'Escape') {
    el.dataset.tabExit = '1';
    return;
  }
  if (ev.key !== 'Tab' || ev.shiftKey || ev.ctrlKey || ev.altKey || ev.metaKey) return;
  if (el.dataset.tabExit === '1') {
    delete el.dataset.tabExit;
    return;
  }
  ev.preventDefault();
  const start = el.selectionStart;
  const end = el.selectionEnd;
  el.setRangeText('    ', start, end, 'end');
  el.dispatchEvent(new Event('input', { bubbles: true }));
}

/**
 * @typedef {{ value: string, label: string, disabled?: boolean }} SelectOption
 */

/**
 * @param {{ options: SelectOption[], value?: string | null, onChange?: (value: string, ev: Event) => void,
 *   disabled?: boolean, className?: string, ariaLabel?: string, dataset?: Record<string, string>, required?: boolean }} opts
 * @returns {HTMLSelectElement}
 */
export function select(opts) {
  const el = h(
    'select',
    {
      class: ['input', 'select', opts.className || ''],
      disabled: opts.disabled,
      'aria-label': opts.ariaLabel,
      dataset: opts.dataset,
      required: opts.required,
    },
    opts.options.map((o) => h('option', { value: o.value, disabled: o.disabled }, o.label)),
  );
  el.value = opts.value ?? '';
  if (el.value !== (opts.value ?? '') && opts.value) {
    // Keep unknown current values visible instead of silently switching.
    el.appendChild(h('option', { value: opts.value }, `${opts.value} (unknown)`));
    el.value = opts.value;
  }
  if (opts.onChange) {
    const fn = opts.onChange;
    el.addEventListener('change', (ev) => fn(el.value, ev));
  }
  return el;
}

/**
 * Checkbox with its own label.
 * @param {{ label: string, checked?: boolean, onChange?: (checked: boolean, ev: Event) => void, disabled?: boolean, hint?: string, dataset?: Record<string, string> }} opts
 * @returns {HTMLElement & { input: HTMLInputElement }}
 */
export function checkbox(opts) {
  const box = h('input', { type: 'checkbox', class: 'checkbox', disabled: opts.disabled, dataset: opts.dataset });
  box.checked = !!opts.checked;
  if (opts.onChange) {
    const fn = opts.onChange;
    box.addEventListener('change', (ev) => fn(box.checked, ev));
  }
  const wrap = /** @type {HTMLElement & { input: HTMLInputElement }} */ (
    /** @type {unknown} */ (field(opts.label, box, { inline: true, hint: opts.hint }))
  );
  wrap.input = box;
  return wrap;
}

/**
 * @typedef {'primary' | 'gold' | 'outline' | 'danger' | 'ghost'} ButtonKind
 */

/**
 * @param {string} label
 * @param {{ kind?: ButtonKind, onClick?: (ev: MouseEvent) => void, icon?: string, iconOnly?: boolean,
 *   type?: 'button' | 'submit', disabled?: boolean, small?: boolean, title?: string, className?: string,
 *   dataset?: Record<string, string>, ariaPressed?: boolean, ariaExpanded?: boolean }} [opts]
 * @returns {HTMLButtonElement}
 */
export function button(label, opts = {}) {
  const el = h(
    'button',
    {
      type: opts.type || 'button',
      class: ['btn', `btn-${opts.kind || 'primary'}`, opts.small ? 'btn-sm' : '', opts.iconOnly ? 'btn-icon' : '', opts.className || ''],
      disabled: opts.disabled,
      title: opts.title || (opts.iconOnly ? label : undefined),
      'aria-label': opts.iconOnly ? label : undefined,
      'aria-pressed': opts.ariaPressed === undefined ? undefined : String(opts.ariaPressed),
      'aria-expanded': opts.ariaExpanded === undefined ? undefined : String(opts.ariaExpanded),
      dataset: opts.dataset,
    },
    opts.icon ? icon(opts.icon) : null,
    opts.iconOnly ? null : h('span', { class: 'btn-label' }, label),
  );
  if (opts.onClick) el.addEventListener('click', /** @type {EventListener} */ (opts.onClick));
  return el;
}

/**
 * Link styled as a button (downloads, new-tab previews).
 * @param {string} label
 * @param {string} hrefValue
 * @param {{ kind?: ButtonKind, icon?: string, download?: boolean | string, newTab?: boolean, small?: boolean }} [opts]
 */
export function linkButton(label, hrefValue, opts = {}) {
  return h(
    'a',
    {
      class: ['btn', `btn-${opts.kind || 'outline'}`, opts.small ? 'btn-sm' : ''],
      href: hrefValue,
      download: opts.download === true ? '' : opts.download || undefined,
      target: opts.newTab ? '_blank' : undefined,
      rel: opts.newTab ? 'noopener noreferrer' : undefined,
    },
    opts.icon ? icon(opts.icon) : null,
    h('span', { class: 'btn-label' }, label),
  );
}

/**
 * @param {string} legend
 * @param {...import('../../shared/dom.js').Child} children
 */
export function fieldset(legend, ...children) {
  return h('fieldset', { class: 'fieldset' }, h('legend', {}, legend), ...children);
}

/**
 * Responsive grid of fields.
 * @param {...import('../../shared/dom.js').Child} children
 */
export function formGrid(...children) {
  return h('div', { class: 'form-grid' }, ...children);
}

/**
 * Small loading placeholder with an accessible label.
 * @param {string} [label]
 */
export function spinner(label = 'Loading…') {
  return h('div', { class: 'spinner-wrap', role: 'status' }, h('span', { class: 'spinner', 'aria-hidden': 'true' }), h('span', { class: 'spinner-label' }, label));
}

/**
 * Empty-state block.
 * @param {string} title
 * @param {string} [body]
 * @param {...import('../../shared/dom.js').Child} actions
 */
export function emptyState(title, body, ...actions) {
  return h('div', { class: 'empty-state' }, h('h3', {}, title), body ? h('p', {}, body) : null, actions.length ? h('div', { class: 'row gap' }, ...actions) : null);
}

/**
 * Error block with optional retry.
 * @param {string} message
 * @param {(() => void) | null} [retry]
 */
export function errorState(message, retry = null) {
  return h(
    'div',
    { class: 'error-state', role: 'alert' },
    icon('error'),
    h('p', {}, message),
    retry ? button('Try again', { kind: 'outline', onClick: () => retry(), icon: 'refresh' }) : null,
  );
}
