// @ts-check
/**
 * Board scenes (title, content, example, summary, key_takeaway, recap and unknown types): the
 * purple glass board in the board zone, driven by the schedule state.
 */

import { h } from '../../shared/dom.js';
import { BoardView } from '../board.js';

/** @typedef {import('./types.js').SceneContext} SceneContext */
/** @typedef {import('./types.js').SceneView} SceneView */

/** @implements {SceneView} */
export class BoardSceneView {
  /** @param {SceneContext} ctx */
  constructor(ctx) {
    this.ctx = ctx;
    this.board = new BoardView(ctx.scene, { mode: ctx.mode, renderTex: ctx.renderTex, loadPrism: ctx.loadPrism });
    /** @type {HTMLDivElement} */
    this.el = h('div', { class: 'ap-zone ap-zone--board' }, this.board.el);
  }

  /**
   * @param {number} _t
   * @param {import('../../shared/types.js').SceneState} state
   */
  update(_t, state) {
    this.board.update(state);
  }

  layout() {
    this.board.fit();
  }

  async ready() {
    await this.board.ready();
  }

  destroy() {
    this.board.destroy();
    this.el.remove();
  }
}
