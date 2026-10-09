// @ts-check
/**
 * WAI-ARIA tabs: roving tabindex, Arrow/Home/End keys, panels rendered lazily on first
 * selection and kept mounted afterwards (their state survives tab switches).
 */

import { h } from '../../shared/dom.js';
import { uid } from '../util.js';

/**
 * @typedef {object} TabDef
 * @property {string} key
 * @property {string} label
 * @property {() => (Node | { el: Node, destroy?: () => void })} render
 */

/**
 * @param {{ tabs: TabDef[], initial?: string, onChange?: (key: string) => void, label: string, className?: string }} opts
 * @returns {{ el: HTMLElement, select: (key: string, focus?: boolean) => void, setBadge: (key: string, text: string | null, kind?: string) => void, current: () => string, destroy: () => void }}
 */
export function createTabs(opts) {
  const list = h('div', { class: 'tablist', role: 'tablist', 'aria-label': opts.label });
  const panels = h('div', { class: 'tabpanels' });
  /** @type {Map<string, { tab: HTMLButtonElement, panel: HTMLElement, rendered: boolean, destroy?: () => void, badge: HTMLElement }>} */
  const items = new Map();
  let current = '';

  for (const def of opts.tabs) {
    const tabId = uid('tab');
    const panelId = uid('panel');
    const badge = h('span', { class: 'tab-badge', hidden: true });
    const tab = h('button', { type: 'button', class: 'tab', role: 'tab', 'aria-selected': 'false', 'aria-controls': panelId, tabindex: '-1', dataset: { key: def.key } }, h('span', {}, def.label), badge);
    tab.setAttribute('id', tabId);
    const panel = h('div', { class: 'tabpanel', role: 'tabpanel', 'aria-labelledby': tabId, tabindex: '0', hidden: true });
    panel.setAttribute('id', panelId);
    tab.addEventListener('click', () => select(def.key));
    tab.addEventListener('keydown', onKey);
    list.appendChild(tab);
    panels.appendChild(panel);
    items.set(def.key, { tab, panel, rendered: false, badge });
  }

  /** @param {KeyboardEvent} ev */
  function onKey(ev) {
    const keys = [...items.keys()];
    const idx = keys.indexOf(current);
    let next = -1;
    if (ev.key === 'ArrowRight') next = (idx + 1) % keys.length;
    else if (ev.key === 'ArrowLeft') next = (idx - 1 + keys.length) % keys.length;
    else if (ev.key === 'Home') next = 0;
    else if (ev.key === 'End') next = keys.length - 1;
    if (next >= 0) {
      ev.preventDefault();
      select(keys[next], true);
    }
  }

  /**
   * @param {string} key
   * @param {boolean} [focus]
   */
  function select(key, focus = false) {
    const item = items.get(key);
    if (!item) return;
    for (const [k, it] of items) {
      const on = k === key;
      it.tab.setAttribute('aria-selected', on ? 'true' : 'false');
      it.tab.tabIndex = on ? 0 : -1;
      it.tab.classList.toggle('active', on);
      it.panel.hidden = !on;
    }
    if (!item.rendered) {
      const def = /** @type {TabDef} */ (opts.tabs.find((t) => t.key === key));
      const out = def.render();
      if (out instanceof Node) item.panel.appendChild(out);
      else {
        item.panel.appendChild(out.el);
        item.destroy = out.destroy;
      }
      item.rendered = true;
    }
    if (focus) item.tab.focus();
    const changed = current !== key;
    current = key;
    if (changed && opts.onChange) opts.onChange(key);
  }

  const first = opts.initial && items.has(opts.initial) ? opts.initial : opts.tabs[0] && opts.tabs[0].key;
  if (first) select(first);

  return {
    el: h('div', { class: ['tabs', opts.className || ''] }, list, panels),
    select,
    current: () => current,
    setBadge(key, text, kind = '') {
      const item = items.get(key);
      if (!item) return;
      item.badge.textContent = text || '';
      item.badge.hidden = !text;
      item.badge.className = `tab-badge ${kind}`;
    },
    destroy() {
      for (const it of items.values()) if (it.destroy) it.destroy();
    },
  };
}
