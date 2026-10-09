// @ts-check
/**
 * Scene inspector: common fields (type, title, mascot, concept, chapter, objectives, intent,
 * notes), the type-specific editor (board / chapter card / simulation / real-world video /
 * interactive / quiz), beats and the side panel. Under the scene heading, a line says where the
 * scene's visual came from at the last build (visualInfo.js; the editor fills it in once GET
 * /visual-review has loaded) with a link to the Visual review. Returns a handle whose flush() delivers
 * pending (debounced) sub-editor edits and whose destroy() releases sub-editors (flushing
 * them too). Every edit targets `ctx.scene.id` (the owner binds `ctx.editScene` to it), so a
 * late flush can never land in another scene.
 * Under the heading, a second line compares the scene with what Aadhi generated (GET /changes: edited, added
 * in the editor, moved) and offers "Compare and revert…". "Timing and visibility" (sceneTiming.js) skips the
 * scene in the video or holds it longer. The sections after the first can be folded (inspectorFold.js); the
 * folds are remembered while the editor is open.
 */

import { h } from '../../../shared/dom.js';
import { field, select, textarea, input, button, group, formGrid } from '../../components/form.js';
import { chipsInput, chipsSelect } from '../../components/chips.js';
import { confirmDialog } from '../../components/modal.js';
import { icon, SCENE_TYPE_ICONS } from '../../components/icons.js';
import * as E from '../../lib/screenplayEdit.js';
import { beatsEditor } from './beatsEditor.js';
import { boardEditor } from './boardEditor.js';
import { sidePanelEditor } from './sidePanelEditor.js';
import { manimEditor } from './manimEditor.js';
import { quizEditor } from './quizEditor.js';
import { mediaUpload } from './mediaUpload.js';
import { defaultManimTemplate } from '../../lib/manimTemplates.js';
import { visualInfo } from './visualInfo.js';
import { sceneTimingSection } from './sceneTiming.js';
import { foldSection } from './inspectorFold.js';
import { fieldWords } from './changes.js';

/** @typedef {import('./context.js').InspectorCtx} InspectorCtx */

const MASCOT_LABELS = /** @type {Record<string, string>} */ ({
  left: 'Left',
  right: 'Right',
  center: 'Centre',
  popup_bottom_left: 'Pop-up bottom left',
  popup_bottom_right: 'Pop-up bottom right',
  hidden: 'Hidden',
});

/**
 * @param {InspectorCtx} ctx
 * @returns {{ el: HTMLElement, flush: () => void, destroy: () => void }}
 */
export function renderInspector(ctx) {
  const scene = ctx.scene;
  /** @type {Array<{ destroy: () => void, flush?: () => void }>} */
  const disposables = [];
  const index = ctx.sp.scenes.findIndex((/** @type {any} */ s) => s.id === scene.id);

  const typeSel = select({
    options: E.SCENE_TYPES.map((t) => ({ value: t, label: E.SCENE_TYPE_LABELS[t] })),
    value: scene.type,
    dataset: { fk: 'scene:type' },
    onChange: async (v) => {
      const lossy = !(E.isBoardScene(scene) && E.isBoardScene({ type: v }));
      if (lossy) {
        const ok = await confirmDialog({
          title: 'Change scene type?',
          message: `Converting to “${E.SCENE_TYPE_LABELS[v]}” keeps the title, beats and side panel; type-specific content (board, quiz, animation, code) is removed.`,
          confirmLabel: 'Change type',
          danger: true,
        });
        if (!ok) {
          typeSel.value = scene.type;
          return;
        }
      }
      ctx.editScreenplay((sp) => E.changeSceneType(sp, scene.id, v, { manimTemplate: defaultManimTemplate(ctx.meta) }), { structural: true, label: 'Change scene type' });
    },
  });

  const concepts = ctx.sp.concept_map || [];
  const chapters = ctx.sp.chapters || [];
  const objectives = ctx.sp.learning_objectives || [];
  /** @param {(s: any) => void} fn @param {string} [coalesce] */
  const set = (fn, coalesce) => ctx.editScene((s) => { fn(s); }, { coalesce });

  const regen = button('Regenerate scene…', { kind: 'outline', small: true, icon: 'wand' });
  regen.addEventListener('click', () => ctx.regenerateScene());
  const dup = button('Duplicate', { kind: 'ghost', small: true, icon: 'copy' });
  dup.addEventListener('click', () => ctx.duplicateScene());
  const del = button('Delete', { kind: 'ghost', small: true, icon: 'trash', disabled: ctx.sp.scenes.length <= 1 });
  del.addEventListener('click', () => ctx.deleteScene());

  const intent = scene.intent || { goal: '', key_points: [], source_refs: [] };
  const intentDetails = h(
    'details',
    { class: 'sub-details', open: ctx.openSections.has('scene:intent') },
    h('summary', {}, 'Teaching intent & notes (teacher-only)'),
    field('Goal', textarea({ value: intent.goal || '', rows: 2, maxLength: 600, dataset: { fk: 'scene:goal' }, onInput: (v) => set((s) => { s.intent = { ...(s.intent || { key_points: [], source_refs: [] }), goal: v }; }, 'scene:goal') })),
    group('Key points', chipsInput({ values: intent.key_points || [], label: 'Key points', maxItems: 12, maxLength: 300, onChange: (v) => set((s) => { s.intent = { ...(s.intent || { goal: '', source_refs: [] }), key_points: v }; }) }).el),
    field('Notes', textarea({ value: scene.notes || '', rows: 3, maxLength: 4000, dataset: { fk: 'scene:notes' }, onInput: (v) => set((s) => { s.notes = v; }, 'scene:notes') })),
  );
  intentDetails.addEventListener('toggle', () => (intentDetails.open ? ctx.openSections.add('scene:intent') : ctx.openSections.delete('scene:intent')));

  const common = h(
    'section',
    { class: 'inspector-section', 'aria-label': 'Scene' },
    h('div', { class: 'section-head' }, h('h2', { class: 'inspector-title' }, icon(SCENE_TYPE_ICONS[scene.type] || 'board'), `Scene ${index + 1}`), h('code', { class: 'id-chip' }, scene.id), h('span', { class: 'spacer' }), regen, dup, del),
    // The editor replaces this host's content when the visual review loads (no inspector re-render).
    h('div', { class: 'visual-info-host', dataset: { visualInfo: scene.id } }, visualInfo(ctx.visual, ctx.reviewHref)),
    changeLine(ctx),
    formGrid(
      field('Type', typeSel),
      field('Title', input({ value: scene.title || '', maxLength: 240, dataset: { fk: 'scene:title' }, onInput: (v) => set((s) => { s.title = v; }, 'scene:title') })),
      field('Subtitle', input({ value: scene.subtitle || '', maxLength: 320, dataset: { fk: 'scene:subtitle' }, onInput: (v) => set((s) => { s.subtitle = v || null; }, 'scene:subtitle') })),
      field('Mascot (Aadhi)', select({ options: E.MASCOT_POSITIONS.map((p) => ({ value: p, label: MASCOT_LABELS[p] || p })), value: scene.mascot_position || 'left', dataset: { fk: 'scene:mascot' }, onChange: (v) => set((s) => { s.mascot_position = v; }) })),
      field('Concept', select({ options: [{ value: '', label: '— none —' }, ...concepts.map((/** @type {any} */ c) => ({ value: c.id, label: c.title || c.id }))], value: scene.concept_id || '', dataset: { fk: 'scene:concept' }, onChange: (v) => set((s) => { s.concept_id = v || null; }) })),
      chapters.length
        ? field('Chapter', select({ options: [{ value: '', label: '— none —' }, ...chapters.map((/** @type {any} */ c) => ({ value: c.id, label: c.title || c.id }))], value: scene.chapter_id || '', dataset: { fk: 'scene:chapter' }, onChange: (v) => ctx.editScreenplay((sp) => E.setSceneChapter(sp, scene.id, v || null), { structural: true, label: 'Change chapter' }) }))
        : null,
    ),
    objectives.length
      ? group('Objectives addressed', chipsSelect({ options: objectives.map((/** @type {any} */ o) => ({ value: o.id, label: o.id, title: o.text })), values: scene.objective_ids || [], label: 'Objectives', max: 10, onChange: (v) => set((s) => { s.objective_ids = v; }) }).el)
      : null,
    intentDetails,
  );

  /** @type {HTMLElement[]} */
  const parts = [common];
  parts.push(sceneTimingSection(ctx));
  if (E.isBoardScene(scene)) parts.push(boardEditor(ctx));
  else if (scene.type === 'chapter_card') {
    parts.push(
      h(
        'section',
        { class: 'inspector-section', 'aria-label': 'Chapter card' },
        h('div', { class: 'section-head' }, h('h3', {}, 'Chapter card')),
        field('Chapter label', input({ value: scene.chapter_label || '', maxLength: 60, placeholder: 'Part 1', dataset: { fk: 'scene:chlabel' }, onInput: (v) => set((s) => { s.chapter_label = v; }, 'scene:chlabel') })),
      ),
    );
  } else if (scene.type === 'simulation') {
    const ed = manimEditor({
      spec: scene.manim || { template: null, params: {}, code: null },
      meta: ctx.meta,
      beatsCount: (scene.beats || []).length,
      target: 'fullscreen',
      fkPrefix: 'scene:manim',
      onChange: (spec) => set((s) => { s.manim = spec; }, 'scene:manim'),
    });
    disposables.push(ed);
    parts.push(
      h(
        'section',
        { class: 'inspector-section', 'aria-label': 'Animation' },
        h('div', { class: 'section-head' }, h('h3', {}, 'Animation')),
        ed.el,
        mediaUpload({ purpose: 'scene_media', projectId: ctx.projectId, assetKey: scene.override_asset_key || null, label: 'Replace with an uploaded video', accept: ['.mp4', '.webm'], maxMb: ctx.maxUploadMb, pickLibrary: ctx.pickLibrary, onChange: (key) => set((s) => { s.override_asset_key = key; }) }),
      ),
    );
  } else if (scene.type === 'ai_video') {
    const figures = ctx.sp.figures || [];
    parts.push(
      h(
        'section',
        { class: 'inspector-section', 'aria-label': 'Real-world video' },
        h('div', { class: 'section-head' }, h('h3', {}, 'Real-world footage')),
        field('Video prompt', textarea({ value: scene.video_prompt || '', rows: 3, maxLength: 1200, dataset: { fk: 'scene:vprompt' }, onInput: (v) => set((s) => { s.video_prompt = v; }, 'scene:vprompt') }), { required: true, hint: 'Describe real-world footage (no mascot, no on-screen text).' }),
        field('Why this footage helps', input({ value: scene.rationale || '', maxLength: 400, dataset: { fk: 'scene:vrat' }, onInput: (v) => set((s) => { s.rationale = v; }, 'scene:vrat') })),
        field('Fallback still (when video is disabled or over budget)', textarea({ value: scene.fallback_image_prompt || '', rows: 2, maxLength: 800, dataset: { fk: 'scene:vfb' }, onInput: (v) => set((s) => { s.fallback_image_prompt = v || null; }, 'scene:vfb') })),
        figures.length
          ? field('Or use a source figure as the fallback', select({ options: [{ value: '', label: '— none —' }, ...figures.map((/** @type {any} */ f) => ({ value: f.id, label: f.caption ? `${f.id} · ${f.caption.slice(0, 40)}` : f.id }))], value: scene.fallback_figure_id || '', dataset: { fk: 'scene:vfig' }, onChange: (v) => set((s) => { s.fallback_figure_id = v || null; }) }))
          : null,
        mediaUpload({ purpose: 'scene_media', projectId: ctx.projectId, assetKey: scene.override_asset_key || null, label: 'Use your own video or image', maxMb: ctx.maxUploadMb, pickLibrary: ctx.pickLibrary, onChange: (key) => set((s) => { s.override_asset_key = key; }) }),
      ),
    );
  } else if (scene.type === 'interactive') {
    parts.push(
      h(
        'section',
        { class: 'inspector-section', 'aria-label': 'Interactive sketch' },
        h('div', { class: 'section-head' }, h('h3', {}, 'Interactive p5 sketch')),
        h('p', { class: 'muted small' }, 'Runs only inside the sandboxed player frame (no network, no storage). MP4 exports show the poster image.'),
        field('p5.js code', textarea({ value: scene.p5_code || '', rows: 14, mono: true, maxLength: 20000, dataset: { fk: 'scene:p5' }, onInput: (v) => set((s) => { s.p5_code = v; }, 'scene:p5') }), { required: true, hint: 'Press Esc then Tab to leave the code editor.' }),
        mediaUpload({ purpose: 'poster', projectId: ctx.projectId, assetKey: scene.poster_override_asset_key || null, label: 'Poster image for video exports', maxMb: ctx.maxUploadMb, pickLibrary: ctx.pickLibrary, onChange: (key) => set((s) => { s.poster_override_asset_key = key; }) }),
      ),
    );
  } else if (scene.type === 'quiz_checkpoint') {
    parts.push(quizEditor(ctx));
  }

  parts.push(beatsEditor(ctx, 'main'));
  if (scene.type === 'quiz_checkpoint') parts.push(beatsEditor(ctx, 'reveal'));
  parts.push(sidePanelEditor(ctx, disposables));
  const closed = ctx.closedSections;
  if (closed) {
    // Every section after the first can be folded, keyed by what it is (kept when switching scenes).
    parts.slice(1).forEach((section) => foldSection(section, foldKey(section), closed));
  }

  return {
    el: h('div', { class: 'inspector', dataset: { sceneType: scene.type, sceneId: scene.id } }, parts),
    flush() {
      for (const d of disposables.slice()) {
        try {
          if (d.flush) d.flush();
        } catch (err) {
          console.warn('inspector flush', err);
        }
      }
    },
    destroy() {
      for (const d of disposables.splice(0)) {
        try {
          d.destroy();
        } catch (err) {
          console.warn('inspector dispose', err);
        }
      }
    },
  };
}

/**
 * The fold key of an inspector section: its role, not the scene (a folded board stays folded in every scene).
 * @param {HTMLElement} section
 */
function foldKey(section) {
  return String(section.getAttribute('aria-label') || section.className || 'section').toLowerCase();
}

/**
 * "Edited since Aadhi wrote it: narration, title. Compare and revert…" (or added / moved / earlier versions).
 * @param {InspectorCtx} ctx
 * @returns {HTMLElement | null}
 */
function changeLine(ctx) {
  const c = ctx.change;
  if (!c) return null;
  /** @type {string | null} */
  let text = null;
  if (c.status === 'edited') {
    const words = fieldWords(c.fields_changed);
    text = words.length ? `Edited since Aadhi wrote it: ${words.join(', ')}.` : 'Edited since Aadhi wrote it.';
  } else if (c.status === 'added') {
    text = 'Added in the editor.';
  } else if (c.status === 'moved') {
    text = Number.isInteger(c.generated_index) ? `Moved: Aadhi placed it as scene ${/** @type {number} */ (c.generated_index) + 1}.` : 'Moved from where Aadhi placed it.';
  }
  const history = Number(c.history) || 0;
  if (!text && history > 0) text = `Regenerated before: ${history === 1 ? 'an earlier version is' : `${history} earlier versions are`} kept.`;
  if (!text) return null;
  const canRevert = !!ctx.revertScene && (c.status === 'edited' || history > 0);
  let revert = null;
  if (canRevert && ctx.revertScene) {
    const fn = ctx.revertScene;
    revert = button(c.status === 'edited' ? 'Compare and revert…' : 'Restore an earlier version…', { kind: 'ghost', small: true, icon: 'undo' });
    revert.addEventListener('click', () => fn());
  }
  return h('div', { class: 'change-line', dataset: { change: c.status } }, h('span', { class: 'muted small' }, text), revert);
}
