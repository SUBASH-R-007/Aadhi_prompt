// @ts-check
/**
 * Keyboard-accessible sortable list.
 *  - Mouse: drag an item's handle (HTML5 drag & drop) onto another position.
 *  - Keyboard: focus the handle, press Space/Enter to pick the item up, Arrow Up/Down to move,
 *    Space/Enter to drop, Escape to cancel; Alt+Arrow moves immediately. Moving focus away
 *    drops the item where it is.
 *  - Moves are announced in a polite live region.
 * The list does not reorder data itself: it calls `onMove(from, to)`; the owner updates its
 * state and either calls `update(items)` or re-renders a whole new list. For the latter, pass a
 * `stateKey`: the keyboard "picked up" state then survives the re-render (a new list created
 * with the same key resumes it) and handles carry `data-fk="<stateKey>:<item key>"` so
 * components/focusKeep.js restores focus to the moved item's handle.
 */

import { h, clear } from '../../shared/dom.js';
import { icon } from './icons.js';

/**
 * @template T
 * @typedef {object} SortableOptions
 * @property {T[]} items
 * @property {(item: T, index: number) => string} key       stable key (not used as DOM id)
 * @property {(item: T, index: number) => string} name      accessible item name
 * @property {(item: T, index: number) => HTMLElement} render  item body (without the handle)
 * @property {(from: number, to: number) => void} onMove
 * @property {string} label                                  list accessible name
 * @property {string} [className]
 * @property {boolean} [disabled]
 * @property {string} [stateKey]   persist the keyboard grab across re-renders of the owner
 */

/**
 * State handed from a list instance to the instance that replaces it, per `stateKey` (module
 * level so it outlives one list). `grabbed` = the keyboard move is still in progress; otherwise
 * the entry only carries the announcement/focus of a finished move and expires quickly.
 * @typedef {{ itemKey: string, grabbed: boolean, origin: number, announce: string | null, at: number }} GrabEntry
 * @type {Map<string, GrabEntry>}
 */
const grabState = new Map();
/** One-shot (non-grab) entries older than this are ignored (the owner did not re-render). */
const HANDOFF_MS = 1500;

/** Forget every persisted grab (tests, route changes). */
export function resetSortableState() {
  grabState.clear();
}

/**
 * @template T
 * @param {SortableOptions<T>} opts
 * @returns {{ el: HTMLElement, update: (items: T[]) => void, focusItem: (key: string) => void, destroy: () => void }}
 */
export function sortableList(opts) {
  let items = opts.items;
  const stateKey = opts.stateKey || null;
  const list = h('ol', { class: ['sortable', opts.className || ''], 'aria-label': opts.label });
  const live = h('div', { class: 'sr-only', 'aria-live': 'polite' });
  const el = h('div', { class: 'sortable-wrap' }, list, live);
  /** @type {number | null} */
  let grabbed = null;
  /** @type {number | null} */
  let grabOrigin = null;
  /** @type {number | null} */
  let dragFrom = null;
  /** @type {string | null} */
  let pendingFocus = null;
  let destroyed = false;

  /** @param {string} msg */
  const announce = (msg) => {
    live.textContent = msg;
  };

  /** @param {string} key */
  const fkOf = (key) => (stateKey ? `${stateKey}:${key}` : null);

  /**
   * Hand state to the list instance the owner may create in `onMove`.
   * @param {string} itemKey
   * @param {boolean} isGrabbed
   * @param {string | null} message
   */
  const persist = (itemKey, isGrabbed, message) => {
    if (stateKey) grabState.set(stateKey, { itemKey, grabbed: isGrabbed, origin: grabOrigin ?? 0, announce: message, at: Date.now() });
  };
  const forget = () => {
    if (stateKey) grabState.delete(stateKey);
  };

  // Resume a keyboard move (or finish announcing one) started in a previous instance.
  if (stateKey && grabState.has(stateKey)) {
    const saved = /** @type {GrabEntry} */ (grabState.get(stateKey));
    const idx = items.findIndex((it, i) => opts.key(it, i) === saved.itemKey);
    const fresh = saved.grabbed || Date.now() - saved.at <= HANDOFF_MS;
    if (!saved.grabbed) grabState.delete(stateKey);
    if (idx >= 0 && fresh && !opts.disabled) {
      if (saved.grabbed) {
        grabbed = idx;
        grabOrigin = Math.max(0, Math.min(saved.origin, items.length - 1));
      }
      if (saved.announce) {
        const msg = saved.announce;
        saved.announce = null;
        // A freshly inserted live region is often not announced; set the text a moment later.
        setTimeout(() => {
          if (!destroyed) announce(msg);
        }, 60);
      }
      pendingFocus = saved.itemKey;
    } else {
      grabState.delete(stateKey);
    }
  }

  const render = () => {
    clear(list);
    items.forEach((item, index) => {
      const name = opts.name(item, index);
      const key = opts.key(item, index);
      const fk = fkOf(key);
      const handle = h(
        'button',
        {
          type: 'button',
          class: 'drag-handle',
          draggable: opts.disabled ? undefined : 'true',
          'aria-label': `Reorder ${name}. Position ${index + 1} of ${items.length}. ${grabbed === index ? 'Picked up: use the arrow keys, then space to drop.' : 'Press space to pick up.'}`,
          'aria-pressed': grabbed === index ? 'true' : 'false',
          disabled: opts.disabled,
          dataset: fk ? { sortKey: key, fk } : { sortKey: key },
        },
        icon('grip'),
      );
      handle.addEventListener('keydown', (ev) => onHandleKey(ev, index));
      handle.addEventListener('blur', () => onHandleBlur(key));
      handle.addEventListener('dragstart', (ev) => {
        dragFrom = index;
        if (ev.dataTransfer) {
          ev.dataTransfer.effectAllowed = 'move';
          ev.dataTransfer.setData('text/plain', String(index));
        }
        li.classList.add('dragging');
      });
      handle.addEventListener('dragend', () => {
        dragFrom = null;
        li.classList.remove('dragging');
        for (const n of list.querySelectorAll('.drop-before, .drop-after')) n.classList.remove('drop-before', 'drop-after');
      });
      const li = h('li', { class: ['sortable-item', grabbed === index ? 'grabbed' : ''] }, handle, opts.render(item, index));
      li.addEventListener('dragover', (ev) => {
        if (dragFrom === null) return;
        ev.preventDefault();
        if (ev.dataTransfer) ev.dataTransfer.dropEffect = 'move';
        const after = isAfter(li, ev);
        li.classList.toggle('drop-after', after);
        li.classList.toggle('drop-before', !after);
      });
      li.addEventListener('dragleave', () => li.classList.remove('drop-before', 'drop-after'));
      li.addEventListener('drop', (ev) => {
        if (dragFrom === null) return;
        ev.preventDefault();
        const after = isAfter(li, ev);
        let to = after ? index + 1 : index;
        if (dragFrom < to) to -= 1;
        const from = dragFrom;
        dragFrom = null;
        if (to !== from) commit(from, to);
      });
      list.appendChild(li);
    });
    if (pendingFocus) {
      const key = pendingFocus;
      pendingFocus = null;
      if (list.isConnected) focusItem(key);
      else if (stateKey) {
        // The owner attaches the new list after building it; focus the moved item once it is in
        // the document, but only when focus was lost with the old list (never steal it from a
        // control the owner or the user focused, e.g. after focusKeep restored it).
        queueMicrotask(() => {
          if (destroyed || !list.isConnected) return;
          const active = document.activeElement;
          if (!active || active === document.body || active === document.documentElement) focusItem(key);
        });
      }
    }
  };

  /**
   * @param {HTMLElement} li
   * @param {DragEvent} ev
   */
  function isAfter(li, ev) {
    const r = li.getBoundingClientRect();
    return r.height > 0 && ev.clientY > r.top + r.height / 2;
  }

  /**
   * @param {number} from
   * @param {number} to
   * @param {string} [message]  announcement (default: "<name> moved to position …")
   */
  function commit(from, to, message) {
    const key = opts.key(items[from], from);
    const name = opts.name(items[from], from);
    const msg = message || `${name} moved to position ${to + 1} of ${items.length}.`;
    pendingFocus = key;
    persist(key, grabbed !== null, msg);
    opts.onMove(from, to);
    announce(msg);
  }

  /**
   * Focus left a handle: when it did not move to the grabbed item's (re-rendered) handle, the
   * keyboard move ends with the item where it is.
   * @param {string} key
   */
  function onHandleBlur(key) {
    if (grabbed === null) return;
    setTimeout(() => {
      if (grabbed === null || destroyed) return;
      const grabbedKey = items[grabbed] ? opts.key(items[grabbed], grabbed) : null;
      if (grabbedKey !== key) return;
      const active = /** @type {HTMLElement | null} */ (document.activeElement);
      const stillOnItem =
        !!active &&
        active.classList.contains('drag-handle') &&
        active.dataset.sortKey === grabbedKey &&
        (list.contains(active) || (!!stateKey && active.dataset.fk === fkOf(grabbedKey)));
      if (stillOnItem) return;
      grabbed = null;
      grabOrigin = null;
      forget();
      if (list.isConnected) render();
    }, 0);
  }

  /**
   * @param {KeyboardEvent} ev
   * @param {number} index
   */
  function onHandleKey(ev, index) {
    const last = items.length - 1;
    if (ev.altKey && (ev.key === 'ArrowUp' || ev.key === 'ArrowDown')) {
      ev.preventDefault();
      const to = ev.key === 'ArrowUp' ? index - 1 : index + 1;
      if (to >= 0 && to <= last) commit(index, to);
      return;
    }
    if (ev.key === ' ' || ev.key === 'Enter') {
      ev.preventDefault();
      if (grabbed === null) {
        grabbed = index;
        grabOrigin = index;
        const key = opts.key(items[index], index);
        persist(key, true, null);
        render();
        focusItem(key);
        announce(`${opts.name(items[index], index)} picked up. Use the arrow keys to move, space to drop, escape to cancel.`);
      } else {
        const name = opts.name(items[grabbed], grabbed);
        const pos = grabbed;
        grabbed = null;
        grabOrigin = null;
        forget();
        const key = opts.key(items[pos], pos);
        render();
        focusItem(key);
        announce(`${name} dropped at position ${pos + 1} of ${items.length}.`);
      }
      return;
    }
    if (grabbed !== null && (ev.key === 'ArrowUp' || ev.key === 'ArrowDown')) {
      ev.preventDefault();
      const to = ev.key === 'ArrowUp' ? grabbed - 1 : grabbed + 1;
      if (to < 0 || to > last) return;
      const from = grabbed;
      grabbed = to;
      commit(from, to);
      return;
    }
    if (grabbed !== null && ev.key === 'Escape') {
      ev.preventDefault();
      ev.stopPropagation();
      const from = grabbed;
      const origin = /** @type {number} */ (grabOrigin);
      grabbed = null;
      grabOrigin = null;
      if (from !== origin) commit(from, origin, 'Move cancelled.');
      else {
        forget();
        render();
        announce('Move cancelled.');
      }
    }
  }

  /** @param {string} key */
  function focusItem(key) {
    for (const b of list.querySelectorAll('.drag-handle')) {
      if (/** @type {HTMLElement} */ (b).dataset.sortKey === key) {
        /** @type {HTMLElement} */ (b).focus();
        return;
      }
    }
  }

  render();
  return {
    el,
    update(next) {
      items = next;
      if (grabbed !== null && grabbed >= items.length) {
        grabbed = null;
        grabOrigin = null;
      }
      // update() mode: this instance lives on, so nothing is handed over.
      if (stateKey && grabState.has(stateKey) && !(/** @type {GrabEntry} */ (grabState.get(stateKey))).grabbed) forget();
      const active = /** @type {HTMLElement | null} */ (document.activeElement);
      if (!pendingFocus && active && list.contains(active) && active.classList.contains('drag-handle')) pendingFocus = active.dataset.sortKey || null;
      render();
    },
    focusItem,
    destroy() {
      destroyed = true;
      clear(list);
    },
  };
}
