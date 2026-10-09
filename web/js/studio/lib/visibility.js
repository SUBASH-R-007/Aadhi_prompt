// @ts-check
/**
 * Page visibility for Studio polling. A poll that comes due while the tab is hidden does no request; it
 * waits and runs at once when the tab is visible again, so a job that finished in the meantime shows as
 * soon as the teacher returns. Live SSE streams stay open (the server caps them per user).
 */

/**
 * True while the page is hidden (another tab, a minimised window, a locked lab PC).
 * @param {Document | null} [doc]
 */
export function pageHidden(doc = typeof document === 'undefined' ? null : document) {
  return !!doc && (doc.hidden === true || doc.visibilityState === 'hidden');
}

/**
 * Call `fn` once, the next time the page becomes visible.
 * @param {() => void} fn
 * @param {Document} [doc]
 * @returns {() => void} cancel (idempotent)
 */
export function onceVisible(fn, doc = document) {
  let done = false;
  const cancel = () => {
    if (done) return;
    done = true;
    doc.removeEventListener('visibilitychange', handler);
  };
  const handler = () => {
    if (pageHidden(doc)) return;
    cancel();
    fn();
  };
  doc.addEventListener('visibilitychange', handler);
  return cancel;
}
