// @ts-check
/**
 * Inspector section "Timing and visibility": skip the scene in the video (`hidden`: kept in the lecture,
 * left out of the preview, the MP4, captions and chapters, and nothing is generated for it) and hold it on
 * screen for at least N seconds (`min_seconds`, 1–600: the scene's end is padded, the narration is never cut
 * or sped up). Both fields are left out of the scene at their defaults (unchecked / empty), so a scene the
 * teacher never touched stays byte-identical. The last scene that plays cannot be skipped.
 */

import { h } from '../../../shared/dom.js';
import { field, input, button, checkbox } from '../../components/form.js';
import { estimateNarrationSeconds } from './sceneList.js';

/** @typedef {import('./context.js').InspectorCtx} InspectorCtx */

export const MIN_HOLD_SECONDS = 1;
export const MAX_HOLD_SECONDS = 600;

/**
 * Parse the hold field: null = empty (automatic), NaN = invalid, else seconds rounded to 0.1.
 * @param {string} text
 * @returns {number | null}
 */
export function parseHold(text) {
  const t = String(text || '').trim().replace(',', '.');
  if (!t) return null;
  const n = Number(t);
  if (!Number.isFinite(n) || n < MIN_HOLD_SECONDS || n > MAX_HOLD_SECONDS) return NaN;
  return Math.round(n * 10) / 10;
}

/**
 * @param {InspectorCtx} ctx
 * @returns {HTMLElement}
 */
export function sceneTimingSection(ctx) {
  const scene = ctx.scene;
  const hidden = scene.hidden === true;
  const lastVisible = !hidden && !(ctx.sp.scenes || []).some((/** @type {any} */ s) => s.id !== scene.id && s.hidden !== true);
  const skip = checkbox({
    label: 'Skip this scene in the video',
    checked: hidden,
    disabled: lastVisible,
    dataset: { fk: 'scene:hidden' },
    hint: lastVisible
      ? 'This is the only scene that plays, so it cannot be skipped.'
      : 'The scene stays in the lecture but is left out of the preview, the video, captions and chapters. Nothing is generated for it while it is skipped.',
    onChange: (on) =>
      ctx.editScene(
        (s) => {
          if (on) s.hidden = true;
          else delete s.hidden;
        },
        { structural: true, label: on ? 'Skip scene in the video' : 'Show scene in the video' },
      ),
  });

  const narration = estimateNarrationSeconds(scene);
  const current = typeof scene.min_seconds === 'number' && Number.isFinite(scene.min_seconds) ? scene.min_seconds : null;
  const hold = input({
    type: 'number',
    min: MIN_HOLD_SECONDS,
    max: MAX_HOLD_SECONDS,
    step: 0.5,
    value: current === null ? '' : String(current),
    placeholder: 'Automatic',
    inputMode: 'decimal',
    dataset: { fk: 'scene:min_seconds' },
    onInput: (v) => apply(v),
  });
  const holdField = field('Show for at least (seconds)', hold, {
    hint: `About ${Math.max(0, Math.round(narration))} s from the narration. A longer time keeps the scene on screen after Aadhi stops speaking (for example to read a diagram); the narration is never cut.`,
  });
  let last = current;
  const clearBtn = button('Clear', { kind: 'ghost', small: true, disabled: current === null, title: 'Use the narration’s length again' });
  clearBtn.addEventListener('click', () => {
    hold.value = '';
    apply('');
  });

  /** @param {string} text */
  function apply(text) {
    const value = parseHold(text);
    if (value !== null && Number.isNaN(value)) {
      holdField.setError(`Enter a number of seconds from ${MIN_HOLD_SECONDS} to ${MAX_HOLD_SECONDS}, or leave it empty.`);
      return;
    }
    holdField.setError(null);
    clearBtn.disabled = value === null;
    if (value === last) return;
    last = value;
    ctx.editScene(
      (s) => {
        if (value === null) delete s.min_seconds;
        else s.min_seconds = value;
      },
      { coalesce: 'scene:min_seconds' },
    );
  }

  return h(
    'section',
    { class: 'inspector-section timing-section', 'aria-label': 'Timing and visibility' },
    h('div', { class: 'section-head' }, h('h3', {}, 'Timing and visibility'), hidden ? h('span', { class: 'badge badge-outline' }, 'Skipped in the video') : null),
    skip,
    h('div', { class: 'row gap wrap timing-hold' }, holdField, clearBtn),
  );
}
