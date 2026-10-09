// @ts-check
/**
 * Collapsible inspector sections. `foldSection(section, key, closed)` turns the section's heading
 * (`.section-head h2/h3`) into a disclosure button (aria-expanded / aria-controls) and puts the rest of the
 * section in a body that is hidden while folded; the head's other controls stay where they are. Sections are
 * open unless the teacher folded them: `closed` (a Set kept by the editor across inspector renders) holds the
 * folded keys. `revealFolded(el)` unfolds every section around `el` first, so an issue jump or a focused beat
 * is never inside a folded section.
 */

import { h } from '../../../shared/dom.js';
import { icon } from '../../components/icons.js';
import { uid } from '../../util.js';

/**
 * @param {HTMLElement} section   an `.inspector-section` whose first child is its `.section-head`
 * @param {string} key            remembered per key (e.g. "board", "beats:main", "panel")
 * @param {Set<string>} closed    folded section keys
 * @returns {HTMLElement} the section
 */
export function foldSection(section, key, closed) {
  const head = /** @type {HTMLElement | null} */ (section.querySelector(':scope > .section-head'));
  const heading = head ? /** @type {HTMLElement | null} */ (head.querySelector('h2, h3')) : null;
  if (!head || !heading) return section;
  const body = h('div', { class: 'section-body' });
  body.setAttribute('id', uid('sec'));
  for (const child of [...section.childNodes]) if (child !== head) body.appendChild(child);
  section.appendChild(body);
  const toggle = h('button', { type: 'button', class: 'section-toggle', 'aria-controls': body.getAttribute('id'), dataset: { fold: key } }, icon('down', { size: 14 }));
  toggle.append(...[...heading.childNodes]);
  heading.appendChild(toggle);
  /** @param {boolean} open */
  const apply = (open) => {
    toggle.setAttribute('aria-expanded', String(open));
    body.hidden = !open;
    section.classList.toggle('is-folded', !open);
  };
  apply(!closed.has(key));
  toggle.addEventListener('click', () => {
    const open = body.hidden;
    if (open) closed.delete(key);
    else closed.add(key);
    apply(open);
  });
  section.dataset.foldKey = key;
  return section;
}

/**
 * Unfold every folded section that contains `el` (and forget that it was folded).
 * @param {Element | null} el
 * @param {Set<string>} [closed]
 */
export function revealFolded(el, closed) {
  let node = el ? el.parentElement : null;
  while (node) {
    if (node.classList.contains('section-body') && node.hidden) {
      const section = node.parentElement;
      const toggle = section ? /** @type {HTMLElement | null} */ (section.querySelector(':scope > .section-head .section-toggle')) : null;
      if (toggle) toggle.click();
      else node.hidden = false;
      if (closed && section && section.dataset.foldKey) closed.delete(section.dataset.foldKey);
    }
    node = node.parentElement;
  }
}
