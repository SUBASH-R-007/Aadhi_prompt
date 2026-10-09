// Helpers for view-level tests (import './_dom.js' first in the test file).
import { sampleMeta, sampleUser } from './fixtures.js';

/**
 * A fake AppContext recording navigations, toasts, errors and the leave guard.
 * @param {Record<string, any>} [overrides]
 */
export function fakeApp(overrides = {}) {
  const rec = { toasts: [], navs: [], errors: [], titles: [], guard: null, guardCalls: 0, metaInvalidations: 0 };
  const app = {
    rec,
    user: () => sampleUser(),
    meta: async () => sampleMeta(),
    invalidateMeta: () => {
      rec.metaInvalidations += 1;
    },
    navigate: (hash) => rec.navs.push(hash),
    replaceHash: () => {},
    toast: (message, opts = {}) => {
      rec.toasts.push({ message, ...opts });
      return () => {};
    },
    setUser: () => {},
    setTitle: (t) => rec.titles.push(t),
    setLeaveGuard: (g) => {
      rec.guardCalls += 1;
      rec.guard = g;
    },
    reportError: (err, fallback) => rec.errors.push(fallback || (err && err.message) || String(err)),
    continueAfterLogin: () => {},
    ...overrides,
  };
  return app;
}

/** A promise with its resolve/reject exposed (to hold a mocked request open). */
export function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

/**
 * The first button (or link) whose text includes `text`.
 * @param {ParentNode} root
 * @param {string} text
 * @param {string} [selector]
 */
export function byText(root, text, selector = 'button, a') {
  return [...root.querySelectorAll(selector)].find((b) => b.textContent.trim().includes(text)) || null;
}

/** Buttons of the top-most open modal. */
export function modalButtons() {
  const dialogs = document.querySelectorAll('.modal');
  const top = dialogs[dialogs.length - 1];
  return top ? [...top.querySelectorAll('.modal-footer button')] : [];
}

/** Click the top modal's footer button labelled `label`. */
export function clickModal(label) {
  const b = modalButtons().find((x) => x.textContent.trim() === label);
  if (!b) throw new Error(`no modal button "${label}" (have: ${modalButtons().map((x) => x.textContent.trim()).join(', ')})`);
  b.click();
}

/**
 * Set a text control's value and fire `input` like a user.
 * @param {HTMLInputElement | HTMLTextAreaElement} el
 * @param {string} value
 */
export function typeInto(el, value) {
  el.value = value;
  el.dispatchEvent(new window.Event('input', { bubbles: true }));
}

/**
 * Wait until `cond()` holds.
 * @param {() => any} cond
 * @param {number} [ms]
 */
export async function waitFor(cond, ms = 3000) {
  const end = Date.now() + ms;
  for (;;) {
    const v = cond();
    if (v) return v;
    if (Date.now() > end) throw new Error('waitFor timed out');
    await new Promise((r) => setTimeout(r, 5));
  }
}

/** Parsed JSON body of a recorded mockFetch call. */
export function jsonBody(call) {
  return JSON.parse(call.init.body);
}
