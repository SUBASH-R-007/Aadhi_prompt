// @ts-check
/**
 * Quiz checkpoint editor: question, 2–5 options with the correct answer, per-option feedback
 * and linked misconception (distractor design), explanation, Bloom level, countdown and
 * sources. Option arrays stay aligned through lib/screenplayEdit.js quiz helpers.
 */

import { h } from '../../../shared/dom.js';
import { field, select, textarea, input, button, group, formGrid } from '../../components/form.js';
import { chipsInput } from '../../components/chips.js';
import * as E from '../../lib/screenplayEdit.js';
import { uid } from '../../util.js';

/** @typedef {import('./context.js').InspectorCtx} InspectorCtx */

const CHUNK_RE = /^c\d{4,5}$/;

/**
 * @param {InspectorCtx} ctx
 * @returns {HTMLElement}
 */
export function quizEditor(ctx) {
  const q = ctx.scene;
  const groupName = uid('quiz-correct');
  const miscs = ctx.sp.misconceptions || [];
  /** @param {(s: any) => void} fn @param {import('./context.js').EditOptions} [o] */
  const edit = (fn, o) => ctx.editScene((s) => {
    fn(s);
    return E.repairSceneRefs(s);
  }, o);

  const rows = (q.options || []).map((/** @type {string} */ opt, /** @type {number} */ i) => {
    const correct = q.correct_index === i;
    const radio = h('input', { type: 'radio', 'aria-label': `Option ${i + 1} is the correct answer`, dataset: { fk: `quiz:${i}:ok` } });
    radio.name = groupName; // generated, never model data
    radio.checked = correct;
    radio.addEventListener('change', () => edit((s) => { s.correct_index = i; }, { structural: true }));
    const up = button(`Move option ${i + 1} up`, { kind: 'ghost', small: true, iconOnly: true, icon: 'up', disabled: i === 0 });
    up.addEventListener('click', () => ctx.editScene((s) => E.moveQuizOption(s, i, i - 1), { structural: true }));
    const down = button(`Move option ${i + 1} down`, { kind: 'ghost', small: true, iconOnly: true, icon: 'down', disabled: i === q.options.length - 1 });
    down.addEventListener('click', () => ctx.editScene((s) => E.moveQuizOption(s, i, i + 1), { structural: true }));
    const del = button(`Remove option ${i + 1}`, { kind: 'ghost', small: true, iconOnly: true, icon: 'trash', disabled: q.options.length <= E.MIN_QUIZ_OPTIONS });
    del.addEventListener('click', () => ctx.editScene((s) => E.removeQuizOption(s, i), { structural: true }));
    return h(
      'li',
      { class: ['quiz-option', correct ? 'correct' : ''] },
      h(
        'div',
        { class: 'row gap align-center' },
        h('label', { class: 'field-inline correct-pick' }, radio, h('span', { class: 'sr-only' }, 'Correct')),
        input({ value: opt, maxLength: 240, ariaLabel: `Option ${i + 1}`, placeholder: `Option ${String.fromCharCode(65 + i)}`, dataset: { fk: `quiz:${i}:text` }, onInput: (v) => edit((s) => { s.options[i] = v; }, { coalesce: `quiz:${i}:text` }) }),
        correct ? h('span', { class: 'badge badge-ok' }, 'Correct') : null,
        up,
        down,
        del,
      ),
      correct
        ? null
        : formGrid(
            field('Feedback when chosen', input({ value: (q.feedback_wrong || [])[i] || '', maxLength: 400, placeholder: 'Why this is tempting but wrong', dataset: { fk: `quiz:${i}:fb` }, onInput: (v) => edit((s) => { s.feedback_wrong[i] = v; }, { coalesce: `quiz:${i}:fb` }) })),
            field(
              'Targets misconception',
              select({
                options: [{ value: '', label: '— none —' }, ...miscs.map((/** @type {any} */ m) => ({ value: m.id, label: `${m.id}: ${String(m.statement).slice(0, 40)}` }))],
                value: (q.option_misconception_ids || [])[i] || '',
                dataset: { fk: `quiz:${i}:mis` },
                onChange: (v) => edit((s) => { s.option_misconception_ids[i] = v || null; }),
              }),
            ),
          ),
    );
  });
  const add = button('Add option', { kind: 'outline', small: true, icon: 'plus', disabled: (q.options || []).length >= E.MAX_QUIZ_OPTIONS });
  add.addEventListener('click', () => ctx.editScene((s) => E.addQuizOption(s), { structural: true }));

  const refs = chipsInput({
    separators: /[,\s]+/,
    values: q.source_refs || [],
    label: 'Source chunks',
    placeholder: 'c0001',
    maxItems: 10,
    maxLength: 6,
    validate: (v) => (CHUNK_RE.test(v) ? null : 'Use chunk ids like c0012.'),
    onChange: (v) => edit((s) => { s.source_refs = v; }),
  });

  return h(
    'section',
    { class: 'inspector-section quiz-section', 'aria-label': 'Quiz' },
    h('div', { class: 'section-head' }, h('h3', {}, 'Quiz checkpoint')),
    h('p', { class: 'muted small' }, 'Options are shown in this order. Good distractors target real misconceptions.'),
    field('Question', textarea({ value: q.question || '', rows: 2, maxLength: 600, dataset: { fk: 'quiz:question' }, onInput: (v) => edit((s) => { s.question = v; }, { coalesce: 'quiz:question' }) }), { required: true }),
    group('Answer options', h('ol', { class: 'quiz-options', role: 'radiogroup', 'aria-label': 'Correct answer' }, rows)),
    add,
    field('Explanation (shown after the answer)', textarea({ value: q.explanation || '', rows: 2, maxLength: 900, dataset: { fk: 'quiz:expl' }, onInput: (v) => edit((s) => { s.explanation = v; }, { coalesce: 'quiz:expl' }) })),
    formGrid(
      field("Bloom's level", select({ options: E.BLOOM_LEVELS.map((b) => ({ value: b, label: b })), value: q.bloom || 'understand', dataset: { fk: 'quiz:bloom' }, onChange: (v) => edit((s) => { s.bloom = v; }) })),
      field('Thinking time (s)', input({ type: 'number', min: 3, max: 30, step: 1, value: String(q.countdown_seconds ?? 8), dataset: { fk: 'quiz:countdown' }, onInput: (v) => { const n = Math.round(Number(v)); if (Number.isFinite(n) && n >= 3 && n <= 30) edit((s) => { s.countdown_seconds = n; }, { coalesce: 'quiz:countdown' }); } }), { hint: '3–30 seconds of countdown before the reveal.' }),
    ),
    group('Source chunks', refs.el),
  );
}
