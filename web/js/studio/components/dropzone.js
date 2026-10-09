// @ts-check
/**
 * File drop zone: drag & drop or keyboard/click to browse, client-side validation, selected
 * file summary with a remove button. The hidden <input type=file> is the single source of
 * selection for keyboard and assistive-technology users.
 */

import { h, clear } from '../../shared/dom.js';
import { formatBytes } from '../util.js';
import { button } from './form.js';
import { icon } from './icons.js';

/**
 * @param {{ accept: string[], label: string, hint?: string, validate?: (file: File) => string | null,
 *   onFile: (file: File | null) => void, disabled?: boolean }} opts
 * @returns {{ el: HTMLElement, reset: () => void, file: () => File | null, setDisabled: (d: boolean) => void, destroy: () => void }}
 */
export function dropZone(opts) {
  /** @type {File | null} */
  let current = null;
  const fileInput = h('input', { type: 'file', class: 'sr-only', accept: opts.accept.join(','), tabindex: '-1', 'aria-hidden': 'true' });
  const error = h('div', { class: 'field-error', role: 'alert', hidden: true });
  const summary = h('div', { class: 'dropzone-summary', 'aria-live': 'polite' });
  const browse = button('Choose file', { kind: 'outline', icon: 'upload', disabled: opts.disabled });
  // The zone itself is only a drop target (mouse users can also click it); keyboard and
  // screen-reader users use the visible "Choose file" button, so no nested interactive roles.
  const zone = h(
    'div',
    { class: 'dropzone', 'aria-disabled': opts.disabled ? 'true' : 'false' },
    icon('upload', { size: 36 }),
    h('div', { class: 'dropzone-title' }, opts.label),
    opts.hint ? h('div', { class: 'dropzone-hint' }, opts.hint) : null,
    browse,
  );
  let disabled = !!opts.disabled;

  const open = () => {
    if (!disabled) fileInput.click();
  };
  /** @param {File | null} file */
  const choose = (file) => {
    error.hidden = true;
    if (!file) return;
    const problem = opts.validate ? opts.validate(file) : null;
    if (problem) {
      error.textContent = problem;
      error.hidden = false;
      return;
    }
    current = file;
    renderSummary();
    opts.onFile(file);
  };
  const renderSummary = () => {
    clear(summary);
    zone.classList.toggle('has-file', !!current);
    if (!current) return;
    const remove = button(`Remove ${current.name}`, { kind: 'ghost', iconOnly: true, icon: 'close', small: true, disabled });
    remove.addEventListener('click', () => {
      current = null;
      fileInput.value = '';
      renderSummary();
      opts.onFile(null);
      browse.focus();
    });
    summary.appendChild(h('div', { class: 'file-pill' }, icon('file'), h('span', { class: 'file-name' }, current.name), h('span', { class: 'muted' }, formatBytes(current.size)), remove));
  };

  browse.addEventListener('click', (ev) => {
    ev.stopPropagation();
    open();
  });
  zone.addEventListener('click', open);
  fileInput.addEventListener('change', () => choose(fileInput.files && fileInput.files[0] ? fileInput.files[0] : null));
  /** @param {DragEvent} ev */
  const over = (ev) => {
    if (disabled) return;
    ev.preventDefault();
    zone.classList.add('dragover');
  };
  zone.addEventListener('dragenter', over);
  zone.addEventListener('dragover', over);
  zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
  zone.addEventListener('drop', (ev) => {
    ev.preventDefault();
    zone.classList.remove('dragover');
    if (disabled) return;
    const files = ev.dataTransfer && ev.dataTransfer.files;
    if (!files || !files.length) return;
    if (files.length > 1) {
      error.textContent = 'Drop one file at a time.';
      error.hidden = false;
      return;
    }
    choose(files[0]);
  });

  return {
    el: h('div', { class: 'dropzone-wrap' }, zone, fileInput, summary, error),
    reset() {
      current = null;
      fileInput.value = '';
      renderSummary();
    },
    file: () => current,
    setDisabled(d) {
      disabled = d;
      browse.disabled = d;
      zone.setAttribute('aria-disabled', d ? 'true' : 'false');
      renderSummary();
    },
    destroy() {
      current = null;
    },
  };
}
