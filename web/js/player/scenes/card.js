// @ts-check
/**
 * Chapter card scene (segmenting principle): a full-height card in the board zone with the chapter
 * badge (chapter_label), title and subtitle. Static, so it has a single render state.
 */

import { h } from '../../shared/dom.js';

/** @typedef {import('./types.js').SceneContext} SceneContext */
/** @typedef {import('./types.js').SceneView} SceneView */

/** @implements {SceneView} */
export class ChapterCardView {
  /** @param {SceneContext} ctx */
  constructor(ctx) {
    const s = ctx.scene;
    /** @type {HTMLDivElement} */
    this.el = h(
      'div',
      { class: 'ap-zone ap-zone--board' },
      h(
        'div',
        { class: 'ap-card', role: 'region', 'aria-label': s.title || s.chapter_label || 'Chapter' },
        s.chapter_label ? h('div', { class: 'ap-card-badge', text: s.chapter_label }) : null,
        h('h2', { class: 'ap-card-title', text: s.title || '' }),
        s.subtitle ? h('p', { class: 'ap-card-subtitle', text: s.subtitle }) : null,
        h('div', { class: 'ap-card-rule', 'aria-hidden': 'true' }),
      ),
    );
  }

  update() {}

  async ready() {}

  destroy() {
    this.el.remove();
  }
}
