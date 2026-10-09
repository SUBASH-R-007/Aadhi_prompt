// @ts-check
/**
 * Beats editor: narration (with length/time hints), spoken override, pause, and for board
 * scenes reveal/fill/highlight controls constrained to valid items (lib/screenplayEdit.js
 * revealOptions/fillOptions/highlightOptions); visual cues for simulations; source refs.
 * Beats reorder by drag or keyboard; references are repaired after every move. From the second main beat on,
 * "Split here" makes this beat and the ones after it a new scene (splitScene.js; not for quizzes, chapter
 * cards and animations).
 */

import { h } from '../../../shared/dom.js';
import { field, select, textarea, input, button, group } from '../../components/form.js';
import { chipsInput, chipsSelect } from '../../components/chips.js';
import { sortableList } from '../../components/sortable.js';
import * as E from '../../lib/screenplayEdit.js';
import { stripRichLite } from '../../lib/richLite.js';
import { estimateSpeechSeconds } from '../../util.js';
import { canSplitType } from './splitScene.js';

/** @typedef {import('./context.js').InspectorCtx} InspectorCtx */

const CHUNK_RE = /^c\d{4,5}$/;

/**
 * @param {any} item
 */
function itemLabel(item) {
  const text = stripRichLite(item.term || item.text || item.latex || item.caption || item.code || '').trim();
  return `${E.BOARD_ITEM_LABELS[item.kind] || item.kind}: ${text.slice(0, 48) || item.id}`;
}

/**
 * @param {InspectorCtx} ctx
 * @param {'main' | 'reveal'} phase
 * @returns {HTMLElement}
 */
export function beatsEditor(ctx, phase = 'main') {
  const scene = ctx.scene;
  const beats = phase === 'reveal' ? scene.reveal_beats || [] : scene.beats || [];
  const board = E.isBoardScene(scene) && phase === 'main';
  const isSim = scene.type === 'simulation' || (scene.side_panel && scene.side_panel.kind === 'manim');
  const title = phase === 'reveal' ? 'Answer reveal narration' : scene.type === 'quiz_checkpoint' ? 'Question narration' : 'Narration beats';

  const list = sortableList({
    items: beats,
    key: (b) => b.id,
    name: (_b, i) => `beat ${i + 1}`,
    label: title,
    className: 'beat-list',
    stateKey: `beats:${scene.id}:${phase}`,
    render: (b, i) => beatEditor(ctx, b, i, phase, board, isSim),
    onMove: (from, to) => ctx.editScene((s) => E.moveBeat(s, from, to, phase), { structural: true, label: 'Move beat' }),
  });

  const add = button('Add beat', { kind: 'outline', small: true, icon: 'plus', disabled: !E.canAddBeat(scene, phase) });
  add.addEventListener('click', () => {
    const after = beats.length - 1;
    ctx.editScene((s) => E.addBeat(s, after, phase).scene, { structural: true, label: 'Add beat' });
    ctx.selectBeat(phase, after + 1);
  });

  const words = beats.reduce((/** @type {number} */ t, /** @type {any} */ b) => t + estimateSpeechSeconds(b.narration || '') + Number(b.pause_after || 0), 0);
  return h(
    'section',
    { class: 'inspector-section beats-section', 'aria-label': title },
    h('div', { class: 'section-head' }, h('h3', {}, title), h('span', { class: 'muted small' }, `${beats.length} beat${beats.length === 1 ? '' : 's'} · ≈ ${Math.round(words)} s`)),
    board ? h('p', { class: 'muted small' }, 'Each beat is voiced separately. A beat can reveal one board item, fill one blank example step and re-emphasise up to two visible items.') : null,
    beats.length ? list.el : h('p', { class: 'muted' }, scene.type === 'chapter_card' ? 'Chapter cards may be silent.' : 'No beats yet.'),
    add,
  );
}

/**
 * @param {InspectorCtx} ctx
 * @param {any} beat
 * @param {number} i
 * @param {'main' | 'reveal'} phase
 * @param {boolean} board
 * @param {boolean} isSim
 */
function beatEditor(ctx, beat, i, phase, board, isSim) {
  const scene = ctx.scene;
  const fk = (/** @type {string} */ f) => `beat:${phase}:${beat.id}:${f}`;
  /** @param {(b: any) => void} mutate @param {import('./context.js').EditOptions} [o] */
  const edit = (mutate, o = {}) =>
    ctx.editScene((s) => {
      const list = phase === 'reveal' ? s.reveal_beats : s.beats;
      const target = list.find((/** @type {any} */ b) => b.id === beat.id);
      if (target) mutate(target);
      return E.repairSceneRefs(s);
    }, o);

  const count = h('span', { class: 'muted small beat-count' });
  const updateCount = (/** @type {string} */ text) => {
    const len = text.length;
    count.textContent = `${len}/1500 · ≈ ${estimateSpeechSeconds(text).toFixed(1)} s`;
    count.classList.toggle('warn', len > 600);
  };
  const narration = textarea({
    value: beat.narration || '',
    rows: 3,
    maxLength: 1500,
    placeholder: 'What Aadhi says in this beat (plain words; formulas spelled out).',
    dataset: { fk: fk('narration') },
    onInput: (v) => {
      updateCount(v);
      edit((b) => {
        b.narration = v;
      }, { coalesce: fk('narration') });
    },
  });
  updateCount(beat.narration || '');
  narration.addEventListener('focus', () => ctx.selectBeat(phase, i));

  const spoken = textarea({
    value: beat.spoken || '',
    rows: 2,
    maxLength: 2000,
    placeholder: 'Optional: how to pronounce this beat (captions still show the narration).',
    dataset: { fk: fk('spoken') },
    onInput: (v) => edit((b) => {
      b.spoken = v.trim() ? v : null;
    }, { coalesce: fk('spoken') }),
  });
  const pause = input({
    type: 'number',
    min: 0,
    max: 8,
    step: 0.5,
    value: String(beat.pause_after || 0),
    dataset: { fk: fk('pause') },
    onInput: (v) => {
      const n = Math.max(0, Math.min(8, Number(v) || 0));
      edit((b) => {
        b.pause_after = n;
      }, { coalesce: fk('pause') });
    },
  });

  const controls = [];
  if (board) {
    const reveal = E.revealOptions(scene, i);
    controls.push(
      field(
        'Reveals',
        select({
          options: [{ value: '', label: 'Nothing' }, ...reveal.map((it) => ({ value: it.id, label: itemLabel(it) }))],
          value: beat.board_item_id || '',
          dataset: { fk: fk('reveal') },
          onChange: (v) => ctx.editScene((s) => E.setBeatReveal(s, i, v || null), { structural: true, label: 'Change what a beat reveals' }),
        }),
        { hint: 'Items without a revealing beat are visible from the start.' },
      ),
    );
    const fills = E.fillOptions(scene, i);
    const hasBlanks = (scene.board || []).some((/** @type {any} */ it) => it.kind === 'example_step' && it.blank);
    if (hasBlanks) {
      controls.push(
        field(
          'Fills blank step',
          select({
            options: [{ value: '', label: 'Nothing' }, ...fills.map((it) => ({ value: it.id, label: itemLabel(it) }))],
            value: beat.fill_item_id || '',
            dataset: { fk: fk('fill') },
            onChange: (v) => ctx.editScene((s) => E.setBeatFill(s, i, v || null), { structural: true, label: 'Change what a beat fills' }),
          }),
          { hint: fills.length ? 'Blanks revealed by this beat or earlier.' : 'No blank step is visible yet at this beat.' },
        ),
      );
    }
    const hl = E.highlightOptions(scene, i);
    controls.push(
      group(
        'Re-emphasise (max 2)',
        chipsSelect({
          options: hl.map((it) => ({ value: it.id, label: itemLabel(it) })),
          values: beat.highlight_item_ids || [],
          max: E.MAX_HIGHLIGHTS,
          label: `Highlights for beat ${i + 1}`,
          emptyText: 'No items are visible before this beat.',
          onChange: (v) => ctx.editScene((s) => E.setBeatHighlights(s, i, v), { structural: true, label: 'Change highlights' }),
        }).el,
      ),
    );
  }
  if (isSim && phase === 'main') {
    controls.push(
      field(
        'Visual cue (animation step)',
        input({
          value: beat.visual_cue || '',
          maxLength: 600,
          placeholder: 'What the animation shows during this beat',
          dataset: { fk: fk('cue') },
          onInput: (v) => edit((b) => {
            b.visual_cue = v.trim() ? v : null;
          }, { coalesce: fk('cue') }),
        }),
      ),
    );
  }

  const refs = chipsInput({
    separators: /[,\s]+/,
    values: beat.source_refs || [],
    label: 'Source chunks',
    placeholder: 'c0001',
    maxItems: 10,
    maxLength: 6,
    validate: (v) => (CHUNK_RE.test(v) ? null : 'Use chunk ids like c0012.'),
    onChange: (v) => edit((b) => {
      b.source_refs = v;
    }),
  });

  const insert = button(`Add beat after beat ${i + 1}`, { kind: 'ghost', small: true, iconOnly: true, icon: 'plus', disabled: !E.canAddBeat(scene, phase) });
  insert.addEventListener('click', () => {
    ctx.editScene((s) => E.addBeat(s, i, phase).scene, { structural: true, label: 'Add beat' });
    ctx.selectBeat(phase, i + 1);
  });
  const remove = button(`Delete beat ${i + 1}`, { kind: 'ghost', small: true, iconOnly: true, icon: 'trash', disabled: !E.canRemoveBeat(scene, phase) });
  remove.addEventListener('click', () => ctx.editScene((s) => E.removeBeat(s, i, phase), { structural: true, label: 'Delete beat' }));
  // Split the scene here: beats from this one on become a new scene (never inside a beat; splitScene.js).
  const splitHere = phase === 'main' && i > 0 && ctx.splitScene && canSplitType(scene) ? button('Split here', { kind: 'ghost', small: true, className: 'beat-split', title: `Split the scene: beats ${i + 1} to ${(scene.beats || []).length} become a new scene` }) : null;
  if (splitHere && ctx.splitScene) {
    const split = ctx.splitScene;
    splitHere.addEventListener('click', () => split(i));
  }

  const selected = ctx.selectedBeat && ctx.selectedBeat.phase === phase && ctx.selectedBeat.index === i;
  const spokenOpen = !!beat.spoken || ctx.openSections.has(fk('more'));
  const more = h(
    'details',
    { class: 'beat-more', open: spokenOpen },
    h('summary', {}, 'Pronunciation & sources'),
    field('Spoken override', spoken),
    group('Source chunks', refs.el),
  );
  more.addEventListener('toggle', () => (more.open ? ctx.openSections.add(fk('more')) : ctx.openSections.delete(fk('more'))));
  return h(
    'div',
    { class: ['beat', selected ? 'selected' : ''], dataset: { beatIndex: String(i), beatPhase: phase } },
    h('div', { class: 'beat-head' }, h('span', { class: 'beat-label' }, `Beat ${i + 1}`), h('code', { class: 'id-chip small' }, beat.id), count, h('span', { class: 'spacer' }), splitHere, insert, remove),
    field('Narration', narration, { required: true }),
    h('div', { class: 'beat-grid' }, field('Pause after (s)', pause), ...controls),
    more,
  );
}
