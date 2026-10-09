// @ts-check
/**
 * Pure LecturePlan edit helpers (aadhi/pipeline/base.py: LecturePlan, PlannedChapter,
 * PlannedScene). Immutable like screenplayEdit.js; keeps ids unique, the concept graph
 * acyclic and references (concept/objective/misconception ids) valid.
 */

import { clone, moveIndex } from '../util.js';
import { toSlug, uniqueId } from './screenplayEdit.js';

/** @typedef {Record<string, any>} Plan */

/**
 * Concepts on a dependency cycle (empty when acyclic). Kahn's algorithm.
 * @param {{ id: string, depends_on?: string[] }[]} concepts
 * @returns {string[]}
 */
export function findCycle(concepts) {
  const ids = new Set(concepts.map((c) => c.id));
  /** @type {Map<string, number>} */
  const indeg = new Map(concepts.map((c) => [c.id, 0]));
  /** @type {Map<string, string[]>} */
  const rev = new Map(concepts.map((c) => [c.id, []]));
  for (const c of concepts) {
    for (const d of c.depends_on || []) {
      if (!ids.has(d)) continue;
      indeg.set(c.id, /** @type {number} */ (indeg.get(c.id)) + 1);
      /** @type {string[]} */ (rev.get(d)).push(c.id);
    }
  }
  const queue = [...indeg].filter(([, n]) => n === 0).map(([id]) => id);
  let seen = 0;
  while (queue.length) {
    const n = /** @type {string} */ (queue.shift());
    seen += 1;
    for (const m of /** @type {string[]} */ (rev.get(n))) {
      indeg.set(m, /** @type {number} */ (indeg.get(m)) - 1);
      if (indeg.get(m) === 0) queue.push(m);
    }
  }
  if (seen === concepts.length) return [];
  return [...indeg].filter(([, n]) => n > 0).map(([id]) => id).sort();
}

/**
 * Would adding `conceptId depends_on depId` create a cycle (or a self-dependency)?
 * @param {{ id: string, depends_on?: string[] }[]} concepts
 * @param {string} conceptId
 * @param {string} depId
 */
export function wouldCreateCycle(concepts, conceptId, depId) {
  if (conceptId === depId) return true;
  const next = concepts.map((c) => (c.id === conceptId ? { ...c, depends_on: [...(c.depends_on || []), depId] } : c));
  return findCycle(next).length > 0;
}

/**
 * Concepts that may be added as dependencies of `conceptId` without creating a cycle.
 * @param {{ id: string, title?: string, depends_on?: string[] }[]} concepts
 * @param {string} conceptId
 */
export function dependencyOptions(concepts, conceptId) {
  const current = concepts.find((c) => c.id === conceptId);
  const existing = new Set((current && current.depends_on) || []);
  return concepts.filter((c) => c.id !== conceptId && (existing.has(c.id) || !wouldCreateCycle(concepts, conceptId, c.id)));
}

/**
 * @param {Plan} plan
 * @param {string} title
 * @returns {{ plan: Plan, id: string }}
 */
export function addConcept(plan, title) {
  const taken = (plan.concept_map || []).map((/** @type {any} */ c) => c.id);
  const id = uniqueId(toSlug(title || 'concept', 'concept'), taken);
  const concept = { id, title: title || 'New concept', summary: '', depends_on: [], kind: 'core' };
  return { plan: { ...plan, concept_map: [...(plan.concept_map || []), concept] }, id };
}

/**
 * Remove a concept and every reference to it.
 * @param {Plan} plan
 * @param {string} id
 * @returns {Plan}
 */
export function removeConcept(plan, id) {
  const out = clone(plan);
  out.concept_map = (out.concept_map || [])
    .filter((/** @type {any} */ c) => c.id !== id)
    .map((/** @type {any} */ c) => ({ ...c, depends_on: (c.depends_on || []).filter((/** @type {string} */ d) => d !== id) }));
  out.learning_objectives = (out.learning_objectives || []).map((/** @type {any} */ o) => ({
    ...o,
    concept_ids: (o.concept_ids || []).filter((/** @type {string} */ c) => c !== id),
  }));
  out.misconceptions = (out.misconceptions || []).map((/** @type {any} */ m) => (m.concept_id === id ? { ...m, concept_id: null } : m));
  out.chapters = (out.chapters || []).map((/** @type {any} */ ch) => ({
    ...ch,
    concept_ids: (ch.concept_ids || []).filter((/** @type {string} */ c) => c !== id),
    scenes: (ch.scenes || []).map((/** @type {any} */ s) => (s.concept_id === id ? { ...s, concept_id: null } : s)),
  }));
  return out;
}

/**
 * @param {Plan} plan
 * @param {string} text
 * @returns {{ plan: Plan, id: string }}
 */
export function addObjective(plan, text) {
  const taken = new Set((plan.learning_objectives || []).map((/** @type {any} */ o) => o.id));
  let n = taken.size + 1;
  while (taken.has(`obj-${n}`)) n += 1;
  const id = `obj-${n}`;
  return { plan: { ...plan, learning_objectives: [...(plan.learning_objectives || []), { id, text, bloom: 'understand', concept_ids: [] }] }, id };
}

/**
 * @param {Plan} plan
 * @param {string} id
 * @returns {Plan}
 */
export function removeObjective(plan, id) {
  const out = clone(plan);
  out.learning_objectives = (out.learning_objectives || []).filter((/** @type {any} */ o) => o.id !== id);
  out.chapters = (out.chapters || []).map((/** @type {any} */ ch) => ({
    ...ch,
    scenes: (ch.scenes || []).map((/** @type {any} */ s) => ({ ...s, objective_ids: (s.objective_ids || []).filter((/** @type {string} */ o) => o !== id) })),
  }));
  return out;
}

/**
 * @param {Plan} plan
 * @returns {{ plan: Plan, id: string }}
 */
export function addMisconception(plan) {
  const taken = new Set((plan.misconceptions || []).map((/** @type {any} */ m) => m.id));
  let n = taken.size + 1;
  while (taken.has(`m${n}`)) n += 1;
  const id = `m${n}`;
  return { plan: { ...plan, misconceptions: [...(plan.misconceptions || []), { id, concept_id: null, statement: '', correction: '' }] }, id };
}

/**
 * @param {Plan} plan
 * @param {string} id
 * @returns {Plan}
 */
export function removeMisconception(plan, id) {
  const out = clone(plan);
  out.misconceptions = (out.misconceptions || []).filter((/** @type {any} */ m) => m.id !== id);
  out.chapters = (out.chapters || []).map((/** @type {any} */ ch) => ({
    ...ch,
    scenes: (ch.scenes || []).map((/** @type {any} */ s) => ({ ...s, misconception_ids: (s.misconception_ids || []).filter((/** @type {string} */ m) => m !== id) })),
  }));
  return out;
}

/** @param {Plan} plan */
export function allPlannedScenes(plan) {
  return (plan.chapters || []).flatMap((/** @type {any} */ ch) => ch.scenes || []);
}

/**
 * @param {Plan} plan
 * @param {string} title
 * @returns {Plan}
 */
export function addChapter(plan, title) {
  const taken = (plan.chapters || []).map((/** @type {any} */ c) => c.id);
  const id = uniqueId(toSlug(title || 'chapter', 'chapter'), taken);
  return { ...plan, chapters: [...(plan.chapters || []), { id, title: title || 'New chapter', concept_ids: [], scenes: [] }] };
}

/**
 * Remove a chapter; its scenes move to the previous chapter (or the next one) unless
 * `dropScenes` is set.
 * @param {Plan} plan
 * @param {number} index
 * @param {boolean} [dropScenes]
 * @returns {Plan}
 */
export function removeChapter(plan, index, dropScenes = false) {
  const chapters = clone(plan.chapters || []);
  const [removed] = chapters.splice(index, 1);
  if (!removed) return plan;
  if (!dropScenes && removed.scenes.length && chapters.length) {
    const target = chapters[Math.max(0, index - 1)];
    target.scenes = index - 1 >= 0 ? [...target.scenes, ...removed.scenes] : [...removed.scenes, ...target.scenes];
  }
  return { ...plan, chapters };
}

/**
 * @param {Plan} plan
 * @param {number} chapterIndex
 * @param {string} type
 * @param {number} [afterIndex]   -1 = first; default = append
 * @returns {{ plan: Plan, id: string }}
 */
export function addPlannedScene(plan, chapterIndex, type, afterIndex) {
  const taken = new Set(allPlannedScenes(plan).map((/** @type {any} */ s) => s.id));
  let n = taken.size + 1;
  while (taken.has(`s${n}`)) n += 1;
  const id = `s${n}`;
  const chapters = clone(plan.chapters || []);
  const ch = chapters[chapterIndex];
  if (!ch) throw new Error('no such chapter');
  const scene = {
    id,
    type,
    concept_id: null,
    objective_ids: [],
    goal: '',
    key_points: [],
    source_refs: [],
    side_panel_kind: null,
    visual_rationale: '',
    manim_template: null,
    misconception_ids: [],
    est_seconds: type === 'quiz_checkpoint' ? 30 : type === 'chapter_card' ? 8 : 45,
  };
  const at = afterIndex === undefined ? ch.scenes.length : Math.max(0, Math.min(afterIndex + 1, ch.scenes.length));
  ch.scenes.splice(at, 0, scene);
  return { plan: { ...plan, chapters }, id };
}

/**
 * @param {Plan} plan
 * @param {number} chapterIndex
 * @param {number} sceneIndex
 * @returns {Plan}
 */
export function removePlannedScene(plan, chapterIndex, sceneIndex) {
  const chapters = clone(plan.chapters || []);
  if (!chapters[chapterIndex]) return plan;
  chapters[chapterIndex].scenes.splice(sceneIndex, 1);
  return { ...plan, chapters };
}

/**
 * @param {Plan} plan
 * @param {number} chapterIndex
 * @param {number} from
 * @param {number} to
 * @returns {Plan}
 */
export function movePlannedScene(plan, chapterIndex, from, to) {
  const chapters = clone(plan.chapters || []);
  if (!chapters[chapterIndex]) return plan;
  chapters[chapterIndex].scenes = moveIndex(chapters[chapterIndex].scenes, from, to);
  return { ...plan, chapters };
}

/**
 * Move a scene to the end (or start) of another chapter.
 * @param {Plan} plan
 * @param {number} fromChapter
 * @param {number} sceneIndex
 * @param {number} toChapter
 * @param {'start' | 'end'} [where]
 * @returns {Plan}
 */
export function moveSceneToChapter(plan, fromChapter, sceneIndex, toChapter, where = 'end') {
  if (fromChapter === toChapter) return plan;
  const chapters = clone(plan.chapters || []);
  const src = chapters[fromChapter];
  const dst = chapters[toChapter];
  if (!src || !dst || !src.scenes[sceneIndex]) return plan;
  const [scene] = src.scenes.splice(sceneIndex, 1);
  if (where === 'start') dst.scenes.unshift(scene);
  else dst.scenes.push(scene);
  return { ...plan, chapters };
}

/**
 * Patch a planned scene; `manim_template` is cleared when it is no longer allowed.
 * @param {Plan} plan
 * @param {number} chapterIndex
 * @param {number} sceneIndex
 * @param {Record<string, any>} patch
 * @returns {Plan}
 */
export function updatePlannedScene(plan, chapterIndex, sceneIndex, patch) {
  const chapters = clone(plan.chapters || []);
  const scene = chapters[chapterIndex] && chapters[chapterIndex].scenes[sceneIndex];
  if (!scene) return plan;
  const { id: _id, ...rest } = patch;
  const next = { ...scene, ...rest };
  if (next.manim_template && next.type !== 'simulation' && next.side_panel_kind !== 'manim') next.manim_template = null;
  chapters[chapterIndex].scenes[sceneIndex] = next;
  return { ...plan, chapters };
}

/** @param {Plan} plan */
export function planTotalSeconds(plan) {
  return allPlannedScenes(plan).reduce((/** @type {number} */ t, /** @type {any} */ s) => t + (Number(s.est_seconds) || 0), 0);
}

/**
 * Client-side plan checks mirroring LecturePlan validation (+ template names from meta).
 * @param {Plan} plan
 * @param {string[] | null} [templateNames]
 * @returns {string[]}
 */
export function validatePlan(plan, templateNames = null) {
  /** @type {string[]} */
  const problems = [];
  const concepts = plan.concept_map || [];
  const conceptIds = new Set(concepts.map((/** @type {any} */ c) => c.id));
  if (conceptIds.size !== concepts.length) problems.push('Concept ids must be unique.');
  const cycle = findCycle(concepts);
  if (cycle.length) problems.push(`The concept map has a cycle among: ${cycle.join(', ')}.`);
  for (const c of concepts) {
    if (!String(c.title || '').trim()) problems.push(`Concept ${c.id} needs a title.`);
    const missing = (c.depends_on || []).filter((/** @type {string} */ d) => !conceptIds.has(d));
    if (missing.length) problems.push(`Concept ${c.id} depends on unknown concepts: ${missing.join(', ')}.`);
  }
  const objectives = new Set((plan.learning_objectives || []).map((/** @type {any} */ o) => o.id));
  for (const o of plan.learning_objectives || []) if (!String(o.text || '').trim()) problems.push(`Objective ${o.id} needs text.`);
  const miscs = new Set((plan.misconceptions || []).map((/** @type {any} */ m) => m.id));
  for (const m of plan.misconceptions || []) {
    if (!String(m.statement || '').trim() || !String(m.correction || '').trim()) problems.push(`Misconception ${m.id} needs a statement and a correction.`);
  }
  const seen = new Set();
  for (const ch of plan.chapters || []) {
    if (!String(ch.title || '').trim()) problems.push(`Chapter ${ch.id} needs a title.`);
    for (const s of ch.scenes || []) {
      if (seen.has(s.id)) problems.push(`Duplicate scene id ${s.id}.`);
      seen.add(s.id);
      if (s.concept_id && conceptIds.size && !conceptIds.has(s.concept_id)) problems.push(`Scene ${s.id}: unknown concept ${s.concept_id}.`);
      if (objectives.size && (s.objective_ids || []).some((/** @type {string} */ o) => !objectives.has(o))) problems.push(`Scene ${s.id}: unknown objectives.`);
      if (miscs.size && (s.misconception_ids || []).some((/** @type {string} */ m) => !miscs.has(m))) problems.push(`Scene ${s.id}: unknown misconceptions.`);
      if (s.manim_template && s.type !== 'simulation' && s.side_panel_kind !== 'manim') problems.push(`Scene ${s.id}: a Manim template needs a simulation scene or a manim panel.`);
      if (s.manim_template && templateNames && !templateNames.includes(s.manim_template)) problems.push(`Scene ${s.id}: unknown Manim template ${s.manim_template}.`);
      const est = Number(s.est_seconds);
      if (!Number.isInteger(est) || est < 5 || est > 600) problems.push(`Scene ${s.id}: estimated seconds must be 5–600.`);
    }
  }
  return problems;
}
