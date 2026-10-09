// @ts-check
/**
 * Quiz teaser panel: a small question with options; the answer is revealed near the end of the
 * scene at `quizTeaserRevealTime` (deterministic in t). In live mode a learner may tap an option
 * to commit a guess before the reveal (purely local; analytics belong to full quiz scenes).
 */

import { Disposer, h } from '../../shared/dom.js';
import { texIdle } from '../../shared/libs.js';
import { quizTeaserRevealTime } from './timing.js';
import { finiteOr, sceneDuration, stripRich } from './util.js';

/** @typedef {import('./types.js').PanelFactory} PanelFactory */

/** @type {((text: string) => DocumentFragment) | null} */
let richRenderer = null;
/** @type {Promise<void> | null} */
let richLoading = null;

/** Load the player's rich-lite renderer once (optional: falls back to plain text). */
function loadRich() {
  if (!richLoading) {
    // Non-literal specifier: richtext.js belongs to the player module and is optional here.
    richLoading = import(new URL('../richtext.js', import.meta.url).href).then(
      (m) => {
        if (typeof m.renderRich === 'function') richRenderer = m.renderRich;
      },
      () => undefined,
    );
  }
  return richLoading;
}

/**
 * @param {HTMLElement} el
 * @param {unknown} text
 */
function fillRich(el, text) {
  const s = String(text ?? '');
  if (richRenderer && /[*`$[]/.test(s)) {
    try {
      el.replaceChildren(richRenderer(s));
      return;
    } catch (e) {
      console.warn('quiz teaser: rich text failed', e);
    }
  }
  el.textContent = stripRich(s);
}

const LETTERS = 'ABCDE';

/** @type {PanelFactory} */
export function createQuizTeaserPanel(body, rsp, ctx) {
  const disposer = new Disposer();
  const spec = (rsp.panel && rsp.panel.quiz) || { question: '', options: [], correct_index: 0 };
  const options = Array.isArray(spec.options) ? spec.options.slice(0, LETTERS.length) : [];
  const correct = Math.max(0, Math.min(options.length - 1, Math.floor(finiteOr(spec.correct_index, 0))));
  const showAt = Math.max(0, finiteOr(rsp.show_at, 0));
  const revealAt = quizTeaserRevealTime(showAt, sceneDuration(ctx, showAt));
  const interactive = ctx.mode === 'live';

  const question = h('p', { class: 'ap-quiz-question' });
  /** @type {HTMLElement[]} */
  const texts = [];
  const items = options.map((/** @type {unknown} */ _opt, /** @type {number} */ i) => {
    const text = h('span', { class: 'ap-quiz-text' });
    texts.push(text);
    const item = h(
      interactive ? 'button' : 'div',
      {
        class: 'ap-quiz-option',
        dataset: { index: String(i) },
        ...(interactive ? { type: 'button' } : {}),
      },
      h('span', { class: 'ap-quiz-letter', text: LETTERS[i] }),
      text,
      h('span', { class: 'ap-quiz-mark', 'aria-hidden': 'true' }),
    );
    return item;
  });
  const status = h('p', { class: 'ap-quiz-status', 'aria-live': interactive ? 'polite' : 'off' });
  const list = h('div', { class: 'ap-quiz-options', role: 'list' }, items);
  body.append(question, list, status);

  const fillAll = () => {
    fillRich(question, spec.question);
    options.forEach((/** @type {unknown} */ opt, /** @type {number} */ i) => fillRich(texts[i], opt));
  };
  fillAll();
  const richReady = loadRich().then(() => {
    if (richRenderer) fillAll();
  });

  /** @type {number | null} */
  let guess = null;
  /** @type {boolean | null} */
  let revealed = null;
  /** @param {boolean} on */
  const setRevealed = (on) => {
    if (on === revealed) return;
    revealed = on;
    body.dataset.revealed = on ? 'true' : 'false';
    items.forEach((el, i) => {
      el.classList.toggle('is-correct', on && i === correct);
      el.classList.toggle('is-dim', on && i !== correct);
      el.classList.toggle('is-wrong', on && guess === i && i !== correct);
      if (interactive) /** @type {HTMLButtonElement} */ (el).disabled = on;
    });
    status.textContent = on
      ? guess === null
        ? `Answer: ${LETTERS[correct]}`
        : guess === correct
          ? `Answer: ${LETTERS[correct]} — well done!`
          : `Answer: ${LETTERS[correct]}`
      : 'Think about it… the answer appears soon.';
  };
  setRevealed(false);

  if (interactive) {
    items.forEach((el, i) => {
      disposer.listen(el, 'click', () => {
        if (revealed) return;
        guess = i;
        items.forEach((o, k) => o.classList.toggle('is-guess', k === i));
      });
    });
  }

  return {
    update(t) {
      setRevealed(t + 1e-6 >= revealAt);
    },
    ready: async () => {
      await richReady;
      await texIdle(); // inline $math$ in options is typeset asynchronously
    },
    destroy: () => disposer.dispose(),
  };
}
