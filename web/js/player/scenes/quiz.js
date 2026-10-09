// @ts-check
/**
 * Quiz checkpoint (retrieval practice): question + options are read aloud, a countdown ring gives
 * thinking time (tick each second, ding at the reveal in live mode), then the correct option is
 * revealed with feedback on wrong options and the explanation. In live/preview mode options are
 * clickable: a click records the learner's answer (event 'quizanswer' + analytics) and highlights
 * it, but never pauses or changes the timeline.
 */

import { h } from '../../shared/dom.js';
import { clamp, optionLetter } from '../../shared/format.js';
import { icon } from '../icons.js';
import { renderRich } from '../richtext.js';

/** @typedef {import('./types.js').SceneContext} SceneContext */
/** @typedef {import('./types.js').SceneView} SceneView */
/** @typedef {import('./types.js').FrameInfo} FrameInfo */
/** @typedef {import('../../shared/types.js').SceneState} SceneState */

export const QUIZ_LABELS = Object.freeze({
  think: 'Think it over…',
  yourAnswer: 'Your answer',
  explanation: 'Why',
  locked: 'Answer locked in',
});

/** @implements {SceneView} */
export class QuizSceneView {
  /** @param {SceneContext} ctx */
  constructor(ctx) {
    this.ctx = ctx;
    const quiz = ctx.scene.quiz;
    this.quiz = quiz || null;
    /** @type {Promise<unknown>[]} */
    this.pending = [];
    /** @type {number | null} */
    this.answer = null;
    this.revealed = false;
    /** @type {number | null} */
    this.lastRemaining = null;
    this.interactive = ctx.mode !== 'render';
    /** @type {HTMLElement[]} */
    this.options = [];
    const rich = (/** @type {string | null | undefined} */ s) => renderRich(s, { renderTex: ctx.renderTex, pending: this.pending });

    const q = quiz || { question: ctx.scene.title || '', options: [], feedback_wrong: [], explanation: '', correct_index: -1 };
    this.options = q.options.map((text, i) => {
      const why = (q.feedback_wrong || [])[i];
      const children = [
        h('span', { class: 'ap-quiz-letter', 'aria-hidden': 'true', text: optionLetter(i) }),
        h('span', { class: 'ap-quiz-text' }, rich(text)),
        why ? h('span', { class: 'ap-quiz-why' }, rich(why)) : null,
        h('span', { class: 'ap-quiz-mark', 'aria-hidden': 'true' }, icon('check', { class: 'ap-quiz-mark-ok' }), icon('cross', { class: 'ap-quiz-mark-no' })),
      ];
      const attrs = { class: 'ap-quiz-opt', dataset: { index: String(i) } };
      return this.interactive
        ? h('button', { ...attrs, type: 'button', 'aria-pressed': 'false', onClick: () => this.choose(i) }, children)
        : h('div', attrs, children);
    });
    /** @type {HTMLSpanElement} */
    this.ringNumber = h('span', { class: 'ap-ring-num' });
    /** @type {HTMLDivElement} */
    this.ring = h('div', { class: 'ap-ring', role: 'timer', 'aria-live': 'off' }, h('div', { class: 'ap-ring-inner' }, this.ringNumber));
    /** @type {HTMLDivElement} */
    this.countdown = h('div', { class: 'ap-quiz-countdown', 'aria-hidden': 'true' }, this.ring, h('div', { class: 'ap-quiz-countdown-label', text: QUIZ_LABELS.think }));
    /** @type {HTMLDivElement} */
    this.explanation = h(
      'div',
      { class: 'ap-quiz-explanation', 'aria-live': 'polite' },
      h('span', { class: 'ap-quiz-explanation-label', text: QUIZ_LABELS.explanation }),
      h('span', { class: 'ap-quiz-explanation-text' }, rich(q.explanation)),
    );
    /** @type {HTMLDivElement} */
    this.card = h(
      'div',
      { class: 'ap-quiz', role: 'group', 'aria-label': 'Quiz checkpoint' },
      ctx.scene.title ? h('div', { class: 'ap-badge', text: ctx.scene.title }) : null,
      h('h2', { class: 'ap-quiz-q' }, rich(q.question)),
      h('div', { class: ['ap-quiz-options', { 'is-two-col': this.options.length >= 3 }] }, this.options),
      this.countdown,
      q.explanation ? this.explanation : null,
    );
    /** @type {HTMLDivElement} */
    this.el = h('div', { class: 'ap-zone ap-zone--board' }, this.card);
  }

  /**
   * Learner picks an option (live/preview). Ignored after the reveal or once answered.
   * @param {number} index
   */
  choose(index) {
    if (!this.quiz || this.revealed || this.answer !== null) return;
    if (index < 0 || index >= this.quiz.options.length) return;
    this.answer = index;
    this.options.forEach((el, i) => {
      el.classList.toggle('is-chosen', i === index);
      el.setAttribute('aria-pressed', i === index ? 'true' : 'false');
    });
    this.card.classList.add('is-answered');
    this.ctx.emit('quizanswer', {
      sceneIndex: this.ctx.sceneIndex,
      sceneId: this.ctx.scene.scene_id,
      choice: index,
      correct: index === this.quiz.correct_index,
    });
  }

  /**
   * @param {number} t
   * @param {SceneState} state
   * @param {FrameInfo} frame
   */
  update(t, state, frame) {
    const quiz = this.quiz;
    this.card.dataset.phase = state.phase;
    const live = this.ctx.mode !== 'render';
    const audible = live && frame.playing && !frame.seeked;
    // countdown
    const remaining = state.countdownRemaining;
    const counting = remaining !== null && !!quiz;
    this.countdown.classList.toggle('is-active', counting);
    if (counting && quiz) {
      this.ringNumber.textContent = String(remaining);
      const span = Math.max(1e-6, quiz.reveal_start - quiz.countdown_start);
      // Live mode sweeps smoothly; render mode shows the discrete second (exact per state).
      const fraction = live ? clamp((quiz.reveal_start - t) / span, 0, 1) : clamp(remaining / Math.max(1, quiz.countdown_seconds), 0, 1);
      this.ring.style.setProperty('--p', fraction.toFixed(4));
      // One tick per countdown second, including the first one at countdown_start (the change
      // from "not counting"), exactly like the MP4 (compose/video.py: countdown_start + k,
      // k = 0..n-1). Seeked frames (incl. the first frame of the scene) are silent.
      if (audible && remaining !== this.lastRemaining && this.ctx.playSound) this.ctx.playSound('tick');
    }
    this.lastRemaining = counting ? remaining : null;
    // reveal
    const revealed = state.quizRevealed && !!quiz;
    if (revealed !== this.revealed) {
      if (revealed && audible && this.ctx.playSound) this.ctx.playSound('ding');
      this.revealed = revealed;
      this.card.classList.toggle('is-revealed', revealed);
      this.options.forEach((el, i) => {
        const correct = !!quiz && i === quiz.correct_index;
        el.classList.toggle('is-correct', revealed && correct);
        el.classList.toggle('is-wrong', revealed && !correct);
        if (el instanceof HTMLButtonElement) el.disabled = revealed || this.answer !== null;
      });
    }
    if (this.interactive) {
      for (const el of this.options) if (el instanceof HTMLButtonElement) el.disabled = this.revealed || this.answer !== null;
    }
  }

  async ready() {
    await Promise.allSettled(this.pending.slice());
  }

  destroy() {
    this.el.remove();
    this.options = [];
  }
}
