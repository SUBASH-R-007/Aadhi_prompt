// @ts-check
/**
 * One API key card, used by the personal "API keys" page and the admin's "Server API keys":
 * status pill, masked key, detail lines (last test, notes), an Add/Replace inline entry, Test
 * (the result stays on the card) and Remove. The caller supplies the texts (`describe`) and
 * the requests (`save`, `test`, `remove`, `refresh`); the card owns the interaction.
 *
 * Key hygiene: a typed key only lives in the masked key input's value. The input is emptied
 * after a save and when the entry closes; the key is never written to the DOM as text, a
 * URL, storage or the console, and it is scrubbed from any error message shown.
 */

import { h, clear, replace } from '../../shared/dom.js';
import { button, field, input } from './form.js';
import { icon } from './icons.js';
import { errorMessage } from '../errors.js';
import { validateApiKey, scrubSecret, API_KEY_LIMITS } from '../lib/apiKeys.js';

/**
 * What a card shows for its current row.
 * @typedef {object} KeyCardState
 * @property {{ text: string, kind: 'ok' | 'attention' | 'muted' | 'busy' | 'bad' }} status
 * @property {string | null} hint      masked key ("sk-ant-…a1B2"), null when none is saved
 * @property {import('../../shared/dom.js').Child[]} lines   detail lines under the status
 * @property {string | null} saveLabel "Add key" / "Replace"; null = saving is not offered
 * @property {boolean} canTest
 * @property {boolean} canRemove
 */

/**
 * @typedef {object} KeyCardOptions
 * @property {string} provider
 * @property {string} label                      provider display name
 * @property {any} row                           the provider's row from the API
 * @property {(row: any) => KeyCardState} describe
 * @property {(apiKey: string) => Promise<any>} save          resolves with the new row
 * @property {() => Promise<import('../types.js').KeyTestResult>} test
 * @property {(row: any) => Promise<any | null>} remove      asks for confirmation; the new row, or null when cancelled
 * @property {() => Promise<any | null>} [refresh]            the row after a test (last tested time)
 * @property {(message: string, opts?: import('./toast.js').ToastOptions) => void} toast
 * @property {(err: unknown, fallback?: string) => void} reportError
 * @property {() => void} [onChange]                          after a key was saved or removed
 */

/**
 * @param {KeyCardOptions} opts
 * @returns {{ el: HTMLElement, update: (row: any) => void, row: () => any }}
 */
export function apiKeyCard(opts) {
  const { label } = opts;
  let row = opts.row;
  let busy = false;
  /** @type {{ close: (restoreFocus?: boolean) => void } | null} */
  let entry = null;

  const badge = h('span', { class: 'badge' });
  const hintLine = h('p', { class: 'key-hint' });
  const lines = h('div', { class: 'key-lines' });
  const result = h('p', { class: 'key-result', role: 'status', 'aria-live': 'polite', hidden: true });
  const actions = h('div', { class: 'key-actions row gap wrap' });
  const entrySlot = h('div', { class: 'key-entry-slot' });
  const el = h(
    'section',
    { class: 'card key-card', dataset: { provider: opts.provider }, 'aria-label': `${label} API key` },
    h('div', { class: 'row gap wrap space-between' }, h('h2', { class: 'card-title' }, label), badge),
    hintLine,
    lines,
    result,
    actions,
    entrySlot,
  );

  /** @type {HTMLButtonElement | null} */
  let primaryBtn = null;
  /** @type {HTMLButtonElement | null} */
  let testBtn = null;

  function render() {
    const state = opts.describe(row);
    badge.className = `badge badge-${state.status.kind}`;
    badge.textContent = state.status.text;
    clear(hintLine);
    hintLine.hidden = !state.hint;
    if (state.hint) hintLine.append(h('span', { class: 'muted' }, 'Key '), h('code', { class: 'mono' }, state.hint));
    replace(lines, state.lines);
    clear(actions);
    primaryBtn = null;
    testBtn = null;
    if (state.saveLabel) {
      primaryBtn = button(state.saveLabel, { kind: state.hint ? 'outline' : 'gold', small: true, icon: 'key', onClick: () => openEntry() });
      actions.append(primaryBtn);
    }
    if (state.canTest) {
      testBtn = button('Test', { kind: 'outline', small: true, icon: 'check', onClick: () => void runTest() });
      actions.append(testBtn);
    }
    if (state.canRemove) actions.append(button('Remove', { kind: 'danger', small: true, icon: 'trash', onClick: () => void runRemove() }));
    actions.hidden = !!entry || !actions.childElementCount;
    setBusy(busy);
  }

  /** @param {boolean} value */
  function setBusy(value) {
    busy = value;
    el.setAttribute('aria-busy', value ? 'true' : 'false');
    for (const b of actions.querySelectorAll('button')) /** @type {HTMLButtonElement} */ (b).disabled = value;
  }

  /**
   * @param {boolean | null} ok   null = in progress
   * @param {string} text
   */
  function showResult(ok, text) {
    clear(result);
    result.className = ['key-result', ok === true ? 'ok' : ok === false ? 'bad' : ''].filter(Boolean).join(' ');
    if (ok !== null) result.append(icon(ok ? 'check' : 'error', { size: 16 }));
    result.append(h('span', {}, text));
    result.hidden = false;
  }

  function hideResult() {
    clear(result);
    result.hidden = true;
  }

  /** @param {any} next */
  function update(next) {
    row = next;
    render();
  }

  /** Inline key entry (one per card): masked key input, Save and Cancel. */
  function openEntry() {
    if (entry || busy) return;
    const keyInput = input({
      type: 'password',
      autocomplete: 'new-password',
      spellcheck: 'false',
      maxLength: API_KEY_LIMITS.max * 2,
      ariaLabel: `${label} API key`,
      placeholder: 'Paste the key',
    });
    keyInput.setAttribute('autocapitalize', 'off');
    keyInput.setAttribute('autocorrect', 'off');
    // type=password keeps the key masked in every browser (CSS text-security is not universal);
    // autocomplete=new-password stops saved passwords being filled in, and the data-* attributes
    // keep LastPass and 1Password away.
    keyInput.setAttribute('data-lpignore', 'true');
    keyInput.setAttribute('data-1p-ignore', 'true');
    const wrap = field('API key', keyInput, { hint: 'Stored encrypted. It is never shown again, only its last 4 characters.' });
    const save = button('Save key', { kind: 'gold', small: true, icon: 'save' });
    const cancel = button('Cancel', { kind: 'outline', small: true });
    const box = h('div', { class: 'key-entry' }, wrap, h('div', { class: 'row gap' }, save, cancel));
    let saving = false;

    /** @param {boolean} [restoreFocus] */
    const close = (restoreFocus = true) => {
      keyInput.value = '';
      box.remove();
      entry = null;
      actions.hidden = !actions.childElementCount;
      if (restoreFocus && primaryBtn) primaryBtn.focus();
    };

    const submit = async () => {
      if (saving) return;
      const typed = keyInput.value;
      const checked = validateApiKey(typed);
      if (!checked.ok) {
        wrap.setError(checked.error);
        keyInput.focus();
        return;
      }
      wrap.setError(null);
      saving = true;
      save.disabled = true;
      cancel.disabled = true;
      keyInput.disabled = true;
      try {
        const next = await opts.save(checked.value);
        close(false);
        hideResult();
        update(next);
        opts.toast(`${label} key saved.`, { kind: 'success' });
        if (opts.onChange) opts.onChange();
        if (primaryBtn) primaryBtn.focus();
      } catch (err) {
        saving = false;
        save.disabled = false;
        cancel.disabled = false;
        keyInput.disabled = false;
        wrap.setError(scrubSecret(errorMessage(err, 'Could not save the key.'), [typed, checked.value]));
        keyInput.focus();
      }
    };

    save.addEventListener('click', () => void submit());
    cancel.addEventListener('click', () => close());
    keyInput.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter') {
        ev.preventDefault();
        void submit();
      } else if (ev.key === 'Escape' && !saving) {
        ev.preventDefault();
        close();
      }
    });
    keyInput.addEventListener('input', () => wrap.setError(null));

    entry = { close };
    actions.hidden = true;
    entrySlot.append(box);
    keyInput.focus();
  }

  async function runTest() {
    if (busy) return;
    setBusy(true);
    showResult(null, 'Testing the key…');
    try {
      const res = await opts.test();
      const message = String((res && res.message) || '').trim();
      showResult(!!(res && res.ok), res && res.ok ? `Test passed.${message ? ` ${message}` : ''}` : `Test failed.${message ? ` ${message}` : ' The provider rejected the key.'}`);
    } catch (err) {
      showResult(false, errorMessage(err, 'Could not test the key.'));
    }
    if (opts.refresh) {
      try {
        const fresh = await opts.refresh();
        if (fresh) row = fresh;
      } catch {
        /* the card keeps its last known state */
      }
    }
    busy = false;
    render();
    // render() rebuilt the buttons, so the Test button that had focus is gone: bring focus back to
    // the card, unless the user moved it elsewhere while the test ran.
    const active = document.activeElement;
    if (el.isConnected && (!active || active === document.body)) {
      const target = testBtn || primaryBtn;
      if (target) target.focus();
    }
  }

  async function runRemove() {
    if (busy) return;
    setBusy(true);
    try {
      const next = await opts.remove(row);
      if (next === null || next === undefined) return;
      if (entry) entry.close(false);
      hideResult();
      update(next);
      opts.toast(`${label} key removed.`, { kind: 'success' });
      if (opts.onChange) opts.onChange();
    } catch (err) {
      opts.reportError(err, 'Could not remove the key.');
    } finally {
      setBusy(false);
      if (!el.contains(document.activeElement) && primaryBtn) primaryBtn.focus();
    }
  }

  render();
  return { el, update, row: () => row };
}
