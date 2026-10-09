// @ts-check
/**
 * Plan review (version status awaiting_review): edit objectives, the concept DAG (list +
 * depends_on chips, cycles prevented), misconceptions, glossary, chapters and planned scenes
 * (reorder, type, goal, key points, panel kind, Manim template, estimates), then
 * POST /plan and /approve-plan. The mount stops early (no leave guard) when the route was left
 * while it was loading.
 */

import { h, clear } from '../../shared/dom.js';
import { get, post } from '../../shared/api.js';
import { button, field, input, select, textarea, spinner, errorState, emptyState, formGrid, group } from '../components/form.js';
import { chipsInput, chipsSelect } from '../components/chips.js';
import { sortableList } from '../components/sortable.js';
import { confirmDialog } from '../components/modal.js';
import { icon, SCENE_TYPE_ICONS } from '../components/icons.js';
import { captureFocus, restoreFocus } from '../components/focusKeep.js';
import * as P from '../lib/planEdit.js';
import { SCENE_TYPES, SCENE_TYPE_LABELS, SIDE_PANEL_KINDS, BLOOM_LEVELS } from '../lib/screenplayEdit.js';
import { clone, deepEqual, formatDuration } from '../util.js';
import { href } from '../router.js';
import { jobProgress } from '../components/jobProgress.js';
import { isActive } from '../lib/jobStages.js';
import { pageHeader, breadcrumbs, jobModal } from './common.js';

/** @type {import('../types.js').ViewMount} */
export async function mount(container, { app, params, signal }) {
  const { id: projectId, vid } = params;
  container.append(breadcrumbs([{ label: 'Projects', hash: '#/projects' }, { label: 'Project', hash: href('project', { id: projectId }) }, { label: 'Plan review' }]));
  const body = h('div', { class: 'plan-review' }, spinner('Loading plan…'));
  container.appendChild(body);

  /** @type {any} */
  let version;
  /** @type {any} */
  let meta = null;
  try {
    [version, meta] = await Promise.all([get(`/api/versions/${vid}`), app.meta().catch(() => null)]);
  } catch (err) {
    if (signal && signal.aborted) return;
    clear(body);
    body.appendChild(errorState('Could not load this version.', () => app.navigate(href('planReview', { id: projectId, vid }), { replace: true })));
    app.reportError(err);
    return;
  }
  if (signal && signal.aborted) return; // the route changed while loading
  if (version.status === 'awaiting_review' && (version.review_stage === 'source' || (version.generation_meta && version.generation_meta.review_stage === 'source'))) {
    // Paused for the teacher's source check, not for a plan: links that say "Review plan" land on its page.
    app.navigate(href('sourceReport', { id: projectId, vid }), { replace: true });
    return;
  }
  if (!version.plan || version.status !== 'awaiting_review') {
    clear(body);
    const generating = version.status === 'generating';
    body.appendChild(
      emptyState(
        'No plan to review',
        generating ? 'The plan is still being written. This page updates when the job pauses for your review.' : 'This version is not waiting for plan approval.',
        h('a', { class: 'btn btn-gold', href: href('project', { id: projectId }) }, 'Go to project'),
        version.screenplay ? h('a', { class: 'btn btn-outline', href: href('editor', { id: projectId, vid }) }, 'Open editor') : null,
      ),
    );
    if (!generating) return;
    // Follow the generation job and re-run this page when it pauses for review.
    /** @type {{ destroy: () => void } | null} */
    let waiting = null;
    let gone = false;
    const self = href('planReview', { id: projectId, vid });
    void activeVersionJob(projectId, vid).then((job) => {
      if (gone || !job) return;
      const widget = jobProgress(job, {
        compact: true,
        reviewHref: self,
        onAwaitingReview: () => app.navigate(self, { replace: true }),
        onSuccess: () => app.navigate(href('editor', { id: projectId, vid })),
      });
      waiting = widget;
      body.appendChild(h('section', { class: 'panel glass', 'aria-label': 'Generation progress' }, widget.el));
    });
    return {
      destroy() {
        gone = true;
        if (waiting) waiting.destroy();
      },
    };
  }

  const templates = (meta && Array.isArray(meta.manim_templates) ? meta.manim_templates : []).map((/** @type {any} */ t) => ({ value: t.name, label: t.title || t.name }));
  const templateNames = templates.length ? templates.map((/** @type {{ value: string }} */ t) => t.value) : null;
  /** @type {any} */
  let plan = clone(version.plan);
  /** @type {any} */
  let saved = clone(version.plan);
  let busy = false;
  let destroyed = false;
  /** Planned scenes whose editor is expanded (kept across re-renders). */
  const openScenes = new Set();
  const dirty = () => !deepEqual(plan, saved);

  const problemsBox = h('div', { class: 'notice warning', role: 'status', 'aria-live': 'polite', hidden: true });
  const totalEl = h('span', { class: 'badge badge-outline' });
  const saveBtn = button('Save plan', { kind: 'outline', icon: 'save' });
  const approveBtn = button('Approve & write scenes', { kind: 'gold', icon: 'check' });
  const sections = h('div', { class: 'plan-sections' });
  const root = h('div', { 'data-dirty': 'false' });

  root.append(
    pageHeader('Review the lecture plan', 'Adjust objectives, concepts and scenes before Aadhi writes the scripts. Approving starts scene writing.', totalEl, saveBtn, approveBtn),
    problemsBox,
    sections,
  );
  clear(body);
  body.appendChild(root);

  const updateStatus = () => {
    const problems = P.validatePlan(plan, templateNames);
    clear(problemsBox);
    problemsBox.hidden = problems.length === 0;
    if (problems.length) problemsBox.append(h('strong', {}, 'Fix before approving:'), h('ul', {}, problems.map((p) => h('li', {}, p))));
    totalEl.textContent = `≈ ${formatDuration(P.planTotalSeconds(plan))} · ${P.allPlannedScenes(plan).length} scenes`;
    saveBtn.disabled = busy || !dirty();
    approveBtn.disabled = busy || problems.length > 0;
    root.dataset.dirty = dirty() ? 'true' : 'false';
  };

  /**
   * @param {any} next
   * @param {boolean} [rerender]
   */
  const commit = (next, rerender = false) => {
    plan = next;
    if (rerender) render();
    else updateStatus();
  };

  function render() {
    const snap = captureFocus(sections);
    clear(sections);
    sections.append(overviewSection(), objectivesSection(), conceptsSection(), misconceptionsSection(), chaptersSection());
    restoreFocus(sections, snap);
    updateStatus();
  }

  // --- overview ---------------------------------------------------------------------------
  function overviewSection() {
    /**
     * @param {string} key
     * @param {string} label
     * @param {number} max
     */
    const text = (key, label, max) =>
      field(label, input({ value: plan[key] || '', maxLength: max, dataset: { fk: `ov:${key}` }, onInput: (v) => commit({ ...plan, [key]: v }) }));
    const glossary = chipsInput({ values: plan.glossary_terms || [], label: 'Glossary terms', placeholder: 'Add a term and press Enter', maxItems: 200, maxLength: 60, onChange: (v) => commit({ ...plan, glossary_terms: v }) });
    return h(
      'section',
      { class: 'panel glass' },
      h('h2', {}, 'Session'),
      formGrid(text('subject_name', 'Subject', 240), text('unit_name', 'Unit', 240), text('session_number', 'Session number', 60), text('session_title', 'Session title', 300)),
      group('Glossary (seeds pronunciation rules)', glossary.el),
      field('Planner notes', textarea({ value: plan.notes || '', rows: 2, dataset: { fk: 'ov:notes' }, onInput: (v) => commit({ ...plan, notes: v }) })),
    );
  }

  // --- objectives ---------------------------------------------------------------------------
  function objectivesSection() {
    const concepts = (plan.concept_map || []).map((/** @type {any} */ c) => ({ value: c.id, label: c.title || c.id }));
    const list = h('ol', { class: 'edit-list' });
    (plan.learning_objectives || []).forEach((/** @type {any} */ o, /** @type {number} */ i) => {
      const update = (/** @type {Record<string, any>} */ patch) => commit({ ...plan, learning_objectives: plan.learning_objectives.map((/** @type {any} */ x, /** @type {number} */ j) => (j === i ? { ...x, ...patch } : x)) });
      const remove = button(`Remove objective ${i + 1}`, { kind: 'ghost', iconOnly: true, icon: 'trash', small: true });
      remove.addEventListener('click', () => commit(P.removeObjective(plan, o.id), true));
      list.appendChild(
        h(
          'li',
          { class: 'edit-row' },
          h('code', { class: 'id-chip' }, o.id),
          h(
            'div',
            { class: 'edit-row-body' },
            field('Objective', textarea({ value: o.text || '', rows: 2, maxLength: 400, dataset: { fk: `obj:${o.id}:text` }, onInput: (v) => update({ text: v }) })),
            formGrid(field("Bloom's level", select({ options: BLOOM_LEVELS.map((b) => ({ value: b, label: b })), value: o.bloom, dataset: { fk: `obj:${o.id}:bloom` }, onChange: (v) => update({ bloom: v }) }))),
            group('Concepts', chipsSelect({ options: concepts, values: o.concept_ids || [], label: `Concepts for objective ${i + 1}`, max: 10, emptyText: 'No concepts yet', onChange: (v) => update({ concept_ids: v }) }).el),
          ),
          remove,
        ),
      );
    });
    const add = button('Add objective', { kind: 'outline', small: true, icon: 'plus', disabled: (plan.learning_objectives || []).length >= 12 });
    add.addEventListener('click', () => {
      const r = P.addObjective(plan, '');
      commit(r.plan, true);
      focusKey(`obj:${r.id}:text`);
    });
    return h('section', { class: 'panel glass' }, h('h2', {}, 'Learning objectives'), h('p', { class: 'muted' }, 'Each objective should be taught and assessed.'), list, add);
  }

  // --- concepts -----------------------------------------------------------------------------
  function conceptsSection() {
    const concepts = plan.concept_map || [];
    const list = h('ol', { class: 'edit-list' });
    concepts.forEach((/** @type {any} */ c, /** @type {number} */ i) => {
      const update = (/** @type {Record<string, any>} */ patch, rerender = false) => commit({ ...plan, concept_map: plan.concept_map.map((/** @type {any} */ x, /** @type {number} */ j) => (j === i ? { ...x, ...patch } : x)) }, rerender);
      const depOptions = P.dependencyOptions(concepts, c.id).map((d) => ({ value: d.id, label: d.title || d.id }));
      const remove = button(`Remove concept ${c.title || c.id}`, { kind: 'ghost', iconOnly: true, icon: 'trash', small: true });
      remove.addEventListener('click', async () => {
        const used = P.allPlannedScenes(plan).filter((/** @type {any} */ s) => s.concept_id === c.id).length;
        if (used && !(await confirmDialog({ title: 'Remove concept?', message: `${used} planned scene(s) use “${c.title}”. They will no longer be linked to a concept.`, confirmLabel: 'Remove', danger: true }))) return;
        commit(P.removeConcept(plan, c.id), true);
      });
      list.appendChild(
        h(
          'li',
          { class: 'edit-row' },
          h('code', { class: 'id-chip' }, c.id),
          h(
            'div',
            { class: 'edit-row-body' },
            formGrid(
              field('Title', input({ value: c.title || '', maxLength: 160, dataset: { fk: `c:${c.id}:title` }, onInput: (v) => update({ title: v }) }), { required: true }),
              field('Kind', select({ options: [{ value: 'core', label: 'Core (taught here)' }, { value: 'prerequisite', label: 'Prerequisite (recalled)' }], value: c.kind || 'core', dataset: { fk: `c:${c.id}:kind` }, onChange: (v) => update({ kind: v }) })),
            ),
            field('Summary', textarea({ value: c.summary || '', rows: 2, maxLength: 600, dataset: { fk: `c:${c.id}:summary` }, onInput: (v) => update({ summary: v }) })),
            group('Depends on', chipsSelect({ options: depOptions, values: c.depends_on || [], label: `Prerequisites of ${c.title || c.id}`, max: 20, emptyText: 'No other concepts', onChange: (v) => update({ depends_on: v }, true) }).el, { hint: 'Concepts that would create a cycle are not offered.' }),
          ),
          remove,
        ),
      );
    });
    const newTitle = input({ placeholder: 'New concept title', maxLength: 160, ariaLabel: 'New concept title' });
    const add = button('Add concept', { kind: 'outline', small: true, icon: 'plus', disabled: concepts.length >= 150 });
    const doAdd = () => {
      const t = newTitle.value.trim();
      if (!t) {
        newTitle.focus();
        return;
      }
      const r = P.addConcept(plan, t);
      commit(r.plan, true);
      focusKey(`c:${r.id}:title`);
    };
    add.addEventListener('click', doAdd);
    newTitle.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter') {
        ev.preventDefault();
        doAdd();
      }
    });
    return h('section', { class: 'panel glass' }, h('h2', {}, 'Concept map'), h('p', { class: 'muted' }, 'Concepts are taught in dependency order; prerequisites are only recalled.'), conceptOrder(), list, h('div', { class: 'row gap' }, newTitle, add));
  }

  /** Topological order preview ("A → B → C"). */
  function conceptOrder() {
    const concepts = plan.concept_map || [];
    if (!concepts.length || P.findCycle(concepts).length) return null;
    const done = new Set();
    const order = [];
    let guard = 0;
    while (order.length < concepts.length && guard++ < concepts.length + 1) {
      for (const c of concepts) {
        if (done.has(c.id)) continue;
        if ((c.depends_on || []).every((/** @type {string} */ d) => done.has(d) || !concepts.some((/** @type {any} */ x) => x.id === d))) {
          done.add(c.id);
          order.push(c.title || c.id);
        }
      }
    }
    return h('p', { class: 'concept-order' }, h('span', { class: 'muted' }, 'Teaching order: '), order.join(' → '));
  }

  // --- misconceptions -----------------------------------------------------------------------
  function misconceptionsSection() {
    const concepts = [{ value: '', label: '— none —' }, ...(plan.concept_map || []).map((/** @type {any} */ c) => ({ value: c.id, label: c.title || c.id }))];
    const list = h('ol', { class: 'edit-list' });
    (plan.misconceptions || []).forEach((/** @type {any} */ m, /** @type {number} */ i) => {
      const update = (/** @type {Record<string, any>} */ patch) => commit({ ...plan, misconceptions: plan.misconceptions.map((/** @type {any} */ x, /** @type {number} */ j) => (j === i ? { ...x, ...patch } : x)) });
      const remove = button(`Remove misconception ${i + 1}`, { kind: 'ghost', iconOnly: true, icon: 'trash', small: true });
      remove.addEventListener('click', () => commit(P.removeMisconception(plan, m.id), true));
      list.appendChild(
        h(
          'li',
          { class: 'edit-row' },
          h('code', { class: 'id-chip' }, m.id),
          h(
            'div',
            { class: 'edit-row-body' },
            field('Students wrongly believe…', textarea({ value: m.statement || '', rows: 2, maxLength: 600, dataset: { fk: `m:${m.id}:statement` }, onInput: (v) => update({ statement: v }) })),
            field('Correction', textarea({ value: m.correction || '', rows: 2, maxLength: 800, dataset: { fk: `m:${m.id}:correction` }, onInput: (v) => update({ correction: v }) })),
            field('Concept', select({ options: concepts, value: m.concept_id || '', dataset: { fk: `m:${m.id}:concept` }, onChange: (v) => update({ concept_id: v || null }) })),
          ),
          remove,
        ),
      );
    });
    const add = button('Add misconception', { kind: 'outline', small: true, icon: 'plus', disabled: (plan.misconceptions || []).length >= 60 });
    add.addEventListener('click', () => {
      const r = P.addMisconception(plan);
      commit(r.plan, true);
      focusKey(`m:${r.id}:statement`);
    });
    return h('section', { class: 'panel glass' }, h('h2', {}, 'Misconceptions to target'), list, add);
  }

  // --- chapters & scenes ------------------------------------------------------------------------
  function chaptersSection() {
    const wrap = h('section', { class: 'panel glass' }, h('h2', {}, 'Chapters & scenes'));
    const concepts = (plan.concept_map || []).map((/** @type {any} */ c) => ({ value: c.id, label: c.title || c.id }));
    (plan.chapters || []).forEach((/** @type {any} */ ch, /** @type {number} */ ci) => {
      const updateChapter = (/** @type {Record<string, any>} */ patch) => commit({ ...plan, chapters: plan.chapters.map((/** @type {any} */ x, /** @type {number} */ j) => (j === ci ? { ...x, ...patch } : x)) });
      const removeCh = button(`Remove chapter ${ch.title}`, { kind: 'ghost', small: true, icon: 'trash', disabled: plan.chapters.length <= 1 });
      removeCh.addEventListener('click', async () => {
        const ok = await confirmDialog({ title: 'Remove chapter?', message: ch.scenes.length ? `Its ${ch.scenes.length} scene(s) move to the neighbouring chapter.` : 'The chapter is empty.', confirmLabel: 'Remove chapter', danger: true });
        if (ok) commit(P.removeChapter(plan, ci), true);
      });
      const sceneList = sortableList({
        items: ch.scenes,
        key: (/** @type {any} */ s) => s.id,
        name: (/** @type {any} */ s, /** @type {number} */ i) => `scene ${i + 1} (${SCENE_TYPE_LABELS[s.type] || s.type})`,
        label: `Scenes of ${ch.title}`,
        render: (/** @type {any} */ s, /** @type {number} */ si) => plannedSceneEditor(ci, si, s),
        onMove: (from, to) => {
          plan = P.movePlannedScene(plan, ci, from, to);
          sceneList.update(plan.chapters[ci].scenes);
          updateStatus();
        },
      });
      const typeSel = select({ options: SCENE_TYPES.map((t) => ({ value: t, label: SCENE_TYPE_LABELS[t] })), value: 'content', ariaLabel: `Type of the new scene in ${ch.title}` });
      const addScene = button('Add scene', { kind: 'outline', small: true, icon: 'plus' });
      addScene.addEventListener('click', () => {
        const r = P.addPlannedScene(plan, ci, typeSel.value);
        commit(r.plan, true);
        focusKey(`s:${r.id}:goal`);
      });
      wrap.appendChild(
        h(
          'div',
          { class: 'chapter-block' },
          h('div', { class: 'row gap wrap align-end' }, field(`Chapter ${ci + 1} title`, input({ value: ch.title || '', maxLength: 240, dataset: { fk: `ch:${ch.id}:title` }, onInput: (v) => updateChapter({ title: v }) })), removeCh),
          group('Concepts covered', chipsSelect({ options: concepts, values: ch.concept_ids || [], label: `Concepts of ${ch.title}`, emptyText: 'No concepts', onChange: (v) => updateChapter({ concept_ids: v }) }).el),
          ch.scenes.length ? sceneList.el : h('p', { class: 'muted' }, 'No scenes in this chapter.'),
          h('div', { class: 'row gap' }, typeSel, addScene),
        ),
      );
    });
    const addCh = button('Add chapter', { kind: 'outline', small: true, icon: 'plus', disabled: (plan.chapters || []).length >= 40 });
    addCh.addEventListener('click', () => commit(P.addChapter(plan, `Chapter ${(plan.chapters || []).length + 1}`), true));
    wrap.appendChild(addCh);
    return wrap;
  }

  /**
   * @param {number} ci
   * @param {number} si
   * @param {any} s PlannedScene
   */
  function plannedSceneEditor(ci, si, s) {
    const upd = (/** @type {Record<string, any>} */ patch, rerender = false) => commit(P.updatePlannedScene(plan, ci, si, patch), rerender);
    const conceptOpts = [{ value: '', label: '— none —' }, ...(plan.concept_map || []).map((/** @type {any} */ c) => ({ value: c.id, label: c.title || c.id }))];
    const panelOpts = [{ value: '', label: 'No side panel' }, ...SIDE_PANEL_KINDS.map((k) => ({ value: k, label: k.replace('_', ' ') }))];
    const templateAllowed = s.type === 'simulation' || s.side_panel_kind === 'manim';
    const chapterOpts = (plan.chapters || []).map((/** @type {any} */ c, /** @type {number} */ j) => ({ value: String(j), label: c.title || `Chapter ${j + 1}` }));
    const moveSel = select({ options: chapterOpts, value: String(ci), ariaLabel: 'Move to chapter', dataset: { fk: `s:${s.id}:chapter` } });
    moveSel.addEventListener('change', () => commit(P.moveSceneToChapter(plan, ci, si, Number(moveSel.value)), true));
    const remove = button(`Remove scene ${s.id}`, { kind: 'ghost', iconOnly: true, icon: 'trash', small: true });
    remove.addEventListener('click', () => commit(P.removePlannedScene(plan, ci, si), true));
    const details = h(
      'details',
      { class: 'planned-scene', open: openScenes.has(s.id) },
      h(
        'summary',
        {},
        icon(SCENE_TYPE_ICONS[s.type] || 'board'),
        h('span', { class: 'ps-type' }, SCENE_TYPE_LABELS[s.type] || s.type),
        h('span', { class: 'ps-goal' }, s.goal || 'No goal yet'),
        h('span', { class: 'muted' }, `${s.est_seconds}s`),
      ),
      formGrid(
        field('Type', select({ options: SCENE_TYPES.map((t) => ({ value: t, label: SCENE_TYPE_LABELS[t] })), value: s.type, dataset: { fk: `s:${s.id}:type` }, onChange: (v) => upd({ type: v }, true) })),
        field('Concept', select({ options: conceptOpts, value: s.concept_id || '', dataset: { fk: `s:${s.id}:concept` }, onChange: (v) => upd({ concept_id: v || null }) })),
        field('Side panel', select({ options: panelOpts, value: s.side_panel_kind || '', dataset: { fk: `s:${s.id}:panel` }, onChange: (v) => upd({ side_panel_kind: v || null }, true) })),
        templateAllowed && templates.length
          ? field('Manim template', select({ options: [{ value: '', label: 'Let Aadhi choose' }, ...templates], value: s.manim_template || '', dataset: { fk: `s:${s.id}:tpl` }, onChange: (v) => upd({ manim_template: v || null }) }))
          : null,
        field('Estimated seconds', input({ type: 'number', min: 5, max: 600, step: 1, value: String(s.est_seconds), dataset: { fk: `s:${s.id}:est` }, onInput: (v) => upd({ est_seconds: Number(v) }) })),
        field('Chapter', moveSel),
      ),
      field('Goal (what the learner gets from this scene)', textarea({ value: s.goal || '', rows: 2, maxLength: 600, dataset: { fk: `s:${s.id}:goal` }, onInput: (v) => upd({ goal: v }) })),
      group('Key points', chipsInput({ values: s.key_points || [], label: 'Key points', maxItems: 12, maxLength: 300, onChange: (v) => upd({ key_points: v }) }).el),
      field('Why this visual helps', input({ value: s.visual_rationale || '', maxLength: 400, dataset: { fk: `s:${s.id}:rationale` }, onInput: (v) => upd({ visual_rationale: v }) })),
      group('Objectives', chipsSelect({ options: (plan.learning_objectives || []).map((/** @type {any} */ o) => ({ value: o.id, label: o.id, title: o.text })), values: s.objective_ids || [], label: 'Objectives', emptyText: 'No objectives', onChange: (v) => upd({ objective_ids: v }) }).el),
      group('Misconceptions to target', chipsSelect({ options: (plan.misconceptions || []).map((/** @type {any} */ m) => ({ value: m.id, label: m.id, title: m.statement })), values: s.misconception_ids || [], label: 'Misconceptions', emptyText: 'No misconceptions', onChange: (v) => upd({ misconception_ids: v }) }).el),
      h('div', { class: 'row end' }, remove),
    );
    details.addEventListener('toggle', () => {
      if (details.open) openScenes.add(s.id);
      else openScenes.delete(s.id);
    });
    return details;
  }

  /** @param {string} key */
  function focusKey(key) {
    queueMicrotask(() => {
      const el = [...sections.querySelectorAll('[data-fk]')].find((x) => /** @type {HTMLElement} */ (x).dataset.fk === key);
      if (!el) return;
      const det = el.closest('details');
      if (det) det.open = true;
      /** @type {HTMLElement} */ (el).focus();
    });
  }

  async function save() {
    busy = true;
    updateStatus();
    try {
      await post(`/api/versions/${vid}/plan`, { plan });
      saved = clone(plan);
      app.toast('Plan saved.', { kind: 'success' });
      return true;
    } catch (err) {
      app.reportError(err, 'Could not save the plan.');
      return false;
    } finally {
      busy = false;
      updateStatus();
    }
  }

  saveBtn.addEventListener('click', () => void save());
  approveBtn.addEventListener('click', async () => {
    if (dirty() && !(await save())) return;
    const ok = await confirmDialog({ title: 'Approve plan?', message: 'Aadhi will now write every scene, voice it and build the visuals. You can still edit everything afterwards.', confirmLabel: 'Approve & continue' });
    if (!ok || destroyed) return;
    busy = true;
    updateStatus();
    try {
      const res = await post(`/api/versions/${vid}/approve-plan`, {});
      app.setLeaveGuard(null);
      if (destroyed) return;
      const editorHash = href('editor', { id: projectId, vid });
      void jobModal('Writing the lecture', res.job, {
        onSuccess: () => {
          if (!destroyed) app.navigate(editorHash);
        },
        description: 'You can close this dialog; progress also shows on the project page.',
      }).then((job) => {
        // Closed by the user (not by leaving the page): continue on the project page.
        if (!destroyed && job && job.status !== 'succeeded') app.navigate(href('project', { id: projectId }));
      });
    } catch (err) {
      app.reportError(err, 'Could not approve the plan.');
      busy = false;
      updateStatus();
    }
  });

  app.setLeaveGuard(async () => !dirty() || confirmDialog({ title: 'Discard plan changes?', message: 'You have unsaved changes to the plan.', confirmLabel: 'Discard', danger: true }));
  render();
  return {
    destroy() {
      destroyed = true;
      app.setLeaveGuard(null);
    },
  };
}

/**
 * The active (queued/running/awaiting review) job working on a version, if any.
 * @param {number} projectId
 * @param {number} versionId
 * @returns {Promise<import('../components/jobProgress.js').JobSummary | null>}
 */
async function activeVersionJob(projectId, versionId) {
  try {
    const res = await get(`/api/jobs?project_id=${projectId}&limit=20`);
    return ((res && res.items) || []).find((/** @type {any} */ j) => j.version_id === versionId && isActive(j)) || null;
  } catch {
    return null;
  }
}
