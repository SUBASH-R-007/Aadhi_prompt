// @ts-check
/**
 * Keep keyboard focus across re-renders: controls carry a stable `data-fk` key; before a
 * re-render we remember the focused key (and caret), afterwards we focus the new control
 * with the same key.
 */

/**
 * @typedef {{ key: string, start: number | null, end: number | null } | null} FocusSnapshot
 */

/**
 * @param {HTMLElement} root
 * @returns {FocusSnapshot}
 */
export function captureFocus(root) {
  const el = /** @type {HTMLElement | null} */ (document.activeElement);
  if (!el || !root.contains(el)) return null;
  const host = /** @type {HTMLElement | null} */ (el.closest('[data-fk]'));
  if (!host || !root.contains(host)) return null;
  let start = null;
  let end = null;
  if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) {
    try {
      start = el.selectionStart;
      end = el.selectionEnd;
    } catch {
      /* number inputs throw */
    }
  }
  return { key: /** @type {string} */ (host.dataset.fk), start, end };
}

/**
 * @param {HTMLElement} root
 * @param {FocusSnapshot} snap
 */
export function restoreFocus(root, snap) {
  if (!snap) return;
  for (const el of root.querySelectorAll('[data-fk]')) {
    const host = /** @type {HTMLElement} */ (el);
    if (host.dataset.fk !== snap.key) continue;
    const target = /** @type {HTMLElement} */ (host.matches('input, textarea, select, button, a, [tabindex]') ? host : host.querySelector('input, textarea, select, button') || host);
    target.focus({ preventScroll: true });
    if ((target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement) && snap.start !== null) {
      try {
        target.setSelectionRange(snap.start, snap.end ?? snap.start);
      } catch {
        /* unsupported input type */
      }
    }
    return;
  }
}
