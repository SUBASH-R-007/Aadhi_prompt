// @ts-check
/**
 * Menu button (WAI-ARIA menu pattern): Enter/Space/ArrowDown opens and focuses the first item,
 * Arrow keys move, Home/End jump, Escape/Tab close, outside clicks close.
 */

import { h } from '../../shared/dom.js';
import { icon } from './icons.js';

/**
 * @typedef {object} MenuItem
 * @property {string} label
 * @property {string} [icon]
 * @property {() => void} [onClick]
 * @property {string} [href]          link items (downloads, new tabs)
 * @property {boolean | string} [download]
 * @property {boolean} [newTab]
 * @property {boolean} [danger]
 * @property {boolean} [disabled]
 */

/**
 * @param {string} label
 * @param {MenuItem[]} items
 * @param {{ icon?: string, kind?: string, small?: boolean, align?: 'left' | 'right' }} [opts]
 * @returns {{ el: HTMLElement, destroy: () => void }}
 */
export function menuButton(label, items, opts = {}) {
  const trigger = h(
    'button',
    { type: 'button', class: ['btn', `btn-${opts.kind || 'outline'}`, opts.small ? 'btn-sm' : ''], 'aria-haspopup': 'menu', 'aria-expanded': 'false' },
    opts.icon ? icon(opts.icon) : null,
    h('span', { class: 'btn-label' }, label),
    icon('down', { size: 14 }),
  );
  /** @type {HTMLElement[]} */
  const entries = items.map((it) => {
    const content = [it.icon ? icon(it.icon) : null, h('span', {}, it.label)];
    const el = it.href
      ? h('a', { class: ['menu-item', it.danger ? 'danger' : ''], role: 'menuitem', tabindex: '-1', href: it.href, download: it.download === true ? '' : it.download || undefined, target: it.newTab ? '_blank' : undefined, rel: it.newTab ? 'noopener noreferrer' : undefined, 'aria-disabled': it.disabled ? 'true' : undefined }, content)
      : h('button', { type: 'button', class: ['menu-item', it.danger ? 'danger' : ''], role: 'menuitem', tabindex: '-1', disabled: it.disabled }, content);
    el.addEventListener('click', (ev) => {
      if (it.disabled) {
        ev.preventDefault();
        return;
      }
      close(false);
      if (it.onClick) it.onClick();
    });
    return el;
  });
  const menu = h('div', { class: ['menu', opts.align === 'left' ? 'menu-left' : 'menu-right'], role: 'menu', hidden: true }, entries);
  const wrap = h('div', { class: 'menu-wrap' }, trigger, menu);

  /** @param {number} i */
  const focusAt = (i) => {
    const enabled = entries.filter((e) => !(/** @type {HTMLButtonElement} */ (e).disabled));
    if (!enabled.length) return;
    enabled[(i + enabled.length) % enabled.length].focus();
  };
  /** @param {boolean} [focusFirst] */
  const open = (focusFirst = true) => {
    menu.hidden = false;
    trigger.setAttribute('aria-expanded', 'true');
    document.addEventListener('mousedown', outside, true);
    if (focusFirst) focusAt(0);
  };
  /** @param {boolean} [restoreFocus] */
  function close(restoreFocus = true) {
    if (menu.hidden) return;
    menu.hidden = true;
    trigger.setAttribute('aria-expanded', 'false');
    document.removeEventListener('mousedown', outside, true);
    if (restoreFocus) trigger.focus();
  }
  /** @param {MouseEvent} ev */
  function outside(ev) {
    if (!wrap.contains(/** @type {Node} */ (ev.target))) close(false);
  }
  trigger.addEventListener('click', () => (menu.hidden ? open() : close()));
  trigger.addEventListener('keydown', (ev) => {
    if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
      ev.preventDefault();
      open(false);
      focusAt(ev.key === 'ArrowDown' ? 0 : -1);
    }
  });
  menu.addEventListener('keydown', (ev) => {
    const enabled = entries.filter((e) => !(/** @type {HTMLButtonElement} */ (e).disabled));
    const idx = enabled.indexOf(/** @type {HTMLElement} */ (document.activeElement));
    if (ev.key === 'ArrowDown') {
      ev.preventDefault();
      focusAt(idx + 1);
    } else if (ev.key === 'ArrowUp') {
      ev.preventDefault();
      focusAt(idx - 1);
    } else if (ev.key === 'Home') {
      ev.preventDefault();
      focusAt(0);
    } else if (ev.key === 'End') {
      ev.preventDefault();
      focusAt(-1);
    } else if (ev.key === 'Escape') {
      ev.preventDefault();
      ev.stopPropagation();
      close();
    } else if (ev.key === 'Tab') {
      close(false);
    }
  });
  return {
    el: wrap,
    destroy: () => document.removeEventListener('mousedown', outside, true),
  };
}
