// @ts-check
/**
 * "Keyboard shortcuts" dialog: groups of key combinations and what they do, as definition lists.
 *
 *   openShortcutsDialog([{ title: 'Editor', keys: [['Ctrl + S', 'Save'], ...] }, ...]) -> ModalHandle
 *
 * Opening it again while it is open returns the open dialog (the "?" key toggles nothing twice).
 */

import { h } from '../../shared/dom.js';
import { openModal } from './modal.js';

/**
 * @typedef {object} ShortcutGroup
 * @property {string} title
 * @property {Array<[string, string]>} keys   [keys, what they do]; "A / B" lists alternatives
 */

/** @type {import('./modal.js').ModalHandle | null} */
let open = null;

/**
 * Keys as <kbd> elements ("Ctrl + Shift + Z" -> Ctrl, Shift, Z; "A / B" keeps the slash as text).
 * @param {string} combo
 */
function keysOf(combo) {
  /** @type {Array<Node | string>} */
  const out = [];
  combo.split(' / ').forEach((alt, i) => {
    if (i) out.push(' or ');
    alt.split(' + ').forEach((k, j) => {
      if (j) out.push(' + ');
      out.push(h('kbd', {}, k.trim()));
    });
  });
  return out;
}

/**
 * @param {ShortcutGroup[]} groups
 * @returns {import('./modal.js').ModalHandle}
 */
export function openShortcutsDialog(groups) {
  if (open) return open;
  const body = h(
    'div',
    { class: 'shortcuts' },
    groups.map((g) =>
      h(
        'section',
        { class: 'shortcuts-group', 'aria-label': g.title },
        h('h3', {}, g.title),
        h('dl', { class: 'shortcuts-list' }, g.keys.map(([combo, what]) => [h('dt', {}, keysOf(combo)), h('dd', {}, what)])),
      ),
    ),
  );
  const modal = openModal({
    title: 'Keyboard shortcuts',
    body,
    size: 'lg',
    actions: [{ label: 'Close', kind: 'gold', value: null, autofocus: true }],
    onClose: () => {
      open = null;
    },
  });
  open = modal;
  return modal;
}
