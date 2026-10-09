// @ts-check
/**
 * Chip controls:
 *  - chipsInput: free-text list editor (key points, glossary terms, source refs);
 *    Enter (or leaving the field) adds, Backspace on an empty input removes the last chip.
 *    Entries are kept whole by default (key points are sentences and may contain commas);
 *    token lists such as chunk ids pass `separators` (e.g. /[,\s]+/) to split pasted text and
 *    to commit on a separator key.
 *  - chipsSelect: multi-select from known options as toggle buttons (aria-pressed),
 *    optional maximum (e.g. two highlights per beat).
 */

import { h, clear } from '../../shared/dom.js';
import { icon } from './icons.js';

/** Default separators: line breaks only (a typed comma is part of the entry). */
const LINE_BREAKS = /[\r\n]+/;

/**
 * @param {{ values: string[], onChange: (values: string[]) => void, placeholder?: string, maxItems?: number,
 *   maxLength?: number, validate?: (v: string) => string | null, label: string, disabled?: boolean,
 *   separators?: RegExp }} opts
 * @returns {{ el: HTMLElement, setValues: (v: string[]) => void, input: HTMLInputElement }}
 */
export function chipsInput(opts) {
  let values = opts.values.slice();
  const custom = opts.separators || null;
  // Stateless copy (no g/y flag) for splitting and single-key tests.
  const separators = custom ? new RegExp(custom.source, custom.flags.replace(/[gy]/g, '')) : LINE_BREAKS;
  const list = h('ul', { class: 'chips', 'aria-label': opts.label });
  const error = h('div', { class: 'field-error', role: 'alert', hidden: true });
  const input = h('input', {
    class: 'input chips-input',
    type: 'text',
    placeholder: opts.placeholder || 'Type and press Enter',
    maxlength: opts.maxLength ? String(opts.maxLength) : undefined,
    'aria-label': `Add to ${opts.label}`,
    disabled: opts.disabled,
  });

  const render = () => {
    clear(list);
    values.forEach((v, i) => {
      const remove = h(
        'button',
        { type: 'button', class: 'chip-remove', 'aria-label': `Remove ${v}`, disabled: opts.disabled, onClick: () => removeAt(i) },
        icon('close', { size: 12 }),
      );
      list.appendChild(h('li', { class: 'chip' }, h('span', { class: 'chip-text' }, v), remove));
    });
    input.disabled = !!opts.disabled || (opts.maxItems !== undefined && values.length >= opts.maxItems);
  };
  /** @param {number} i */
  const removeAt = (i) => {
    values = values.filter((_, j) => j !== i);
    render();
    opts.onChange(values.slice());
    input.focus();
  };
  const commit = () => {
    const parts = input.value
      .split(separators)
      .map((s) => s.trim())
      .filter(Boolean);
    if (!parts.length) return;
    for (const p of parts) {
      if (opts.maxItems !== undefined && values.length >= opts.maxItems) break;
      const problem = opts.validate ? opts.validate(p) : null;
      if (problem) {
        error.textContent = problem;
        error.hidden = false;
        return;
      }
      if (!values.includes(p)) values.push(p);
    }
    error.hidden = true;
    input.value = '';
    render();
    opts.onChange(values.slice());
  };
  input.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' || (custom && ev.key.length === 1 && separators.test(ev.key))) {
      ev.preventDefault();
      commit();
    } else if (ev.key === 'Backspace' && input.value === '' && values.length) {
      removeAt(values.length - 1);
    }
  });
  input.addEventListener('blur', commit);
  render();
  return {
    el: h('div', { class: 'chips-field' }, list, input, error),
    input,
    setValues(v) {
      values = v.slice();
      render();
    },
  };
}

/**
 * @param {{ options: { value: string, label: string, title?: string }[], values: string[], onChange: (values: string[]) => void,
 *   max?: number, label: string, emptyText?: string, disabled?: boolean }} opts
 * @returns {{ el: HTMLElement, setValues: (v: string[]) => void }}
 */
export function chipsSelect(opts) {
  let values = opts.values.filter((v) => opts.options.some((o) => o.value === v));
  const el = h('div', { class: 'chips chips-select', role: 'group', 'aria-label': opts.label });
  const render = () => {
    clear(el);
    if (!opts.options.length) {
      el.appendChild(h('span', { class: 'muted' }, opts.emptyText || 'Nothing to choose'));
      return;
    }
    const full = opts.max !== undefined && values.length >= opts.max;
    for (const o of opts.options) {
      const on = values.includes(o.value);
      const b = h(
        'button',
        {
          type: 'button',
          class: ['chip', 'chip-toggle', on ? 'on' : ''],
          'aria-pressed': on ? 'true' : 'false',
          title: o.title || o.label,
          disabled: opts.disabled || (!on && full),
        },
        on ? icon('check', { size: 12 }) : null,
        h('span', { class: 'chip-text' }, o.label),
      );
      b.addEventListener('click', () => {
        values = on ? values.filter((v) => v !== o.value) : [...values, o.value];
        render();
        opts.onChange(values.slice());
        const again = /** @type {HTMLElement | null} */ (el.querySelectorAll('button')[opts.options.indexOf(o)]);
        if (again) again.focus();
      });
      el.appendChild(b);
    }
  };
  render();
  return {
    el,
    setValues(v) {
      values = v.slice();
      render();
    },
  };
}
