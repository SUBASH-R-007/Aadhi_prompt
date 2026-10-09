// @ts-check
/**
 * Lesson quality helpers for the editor (pure, no DOM except `measureBoard`):
 *  - `areaOf(code)` / `groupByArea(issues, sp)`: plain-words areas derived from the issue code prefix;
 *  - `statusOf(counts)`: the one-line headline ("Looks good", "3 things to review", "1 needs fixing");
 *  - `SEVERITY_WORDS`: severity in words (shown next to the icon: never colour alone);
 *  - `attachRepairs(issues, repairs)` / `applyRepair(sp, repair)`: the safe repairs of POST /lint
 *    (`quality.repairs`: exact field edits). A repair applies only when every `before` still matches the
 *    draft (ignoring leading and trailing whitespace, which the server strips), as one ordinary edit
 *    (undoable, saved by the normal save);
 *  - `preExportList(issues, sp)`: open errors and warnings for the render dialog (at most 12);
 *  - `measureBoard(root)` / `fitIssue(...)`: the board fit the live player measured in the preview (same
 *    1920x1080 stage and fit as the MP4 render) as a non-blocking warning or note.
 * The Issue JSON contract is unchanged; repairs and measurements are client-side extras.
 */

import { sortIssues, countIssues } from '../../lib/validationIssues.js';

/** @typedef {import('../../lib/validationIssues.js').Issue} Issue */
/** @typedef {{ scene_id: string, path: Array<string | number>, before: string, after: string }} RepairEdit */
/** @typedef {{ code: string, scene_id: string | null, message: string, label: string, edits: RepairEdit[] }} Repair */
/** @typedef {Issue & { repair?: Repair }} QualityIssue */

/** Severity in words (shown with the icon). */
export const SEVERITY_WORDS = Object.freeze(/** @type {Record<string, string>} */ ({ error: 'Needs fixing', warning: 'Please check', info: 'Note' }));

/** Areas in display order. */
export const AREAS = Object.freeze([
  { id: 'editor', label: 'Editor checks' },
  { id: 'teaching', label: 'Content and teaching' },
  { id: 'terms', label: 'Terms and abbreviations' },
  { id: 'formulas', label: 'Formulas' },
  { id: 'code', label: 'Code' },
  { id: 'board', label: 'Board and pacing' },
  { id: 'narration', label: 'Narration' },
  { id: 'visuals', label: 'Visuals and media' },
  { id: 'quizzes', label: 'Quizzes' },
  { id: 'coverage', label: 'Coverage and length' },
  { id: 'titles', label: 'Titles' },
  { id: 'other', label: 'Other' },
]);

const AREA_BY_CODE = /** @type {Record<string, string>} */ ({
  'concept.naming_variant': 'terms',
  'concept.unused': 'coverage',
  'misconception.untargeted': 'coverage',
  'scene.hidden': 'coverage',
  'chapter.all_scenes_hidden': 'coverage',
  'scene.too_long': 'narration',
  'scene.type_disabled': 'visuals',
  'scene.generation_failed': 'teaching',
});

const AREA_BY_PREFIX = /** @type {Record<string, string>} */ ({
  local: 'editor', schema: 'editor',
  terminology: 'terms',
  formula: 'formulas',
  code: 'code',
  board: 'board', pacing: 'board', layout: 'board', example: 'board',
  beat: 'narration', narration: 'narration', sync: 'narration', audio: 'narration', tts: 'narration',
  figure: 'visuals', panel: 'visuals', manim: 'visuals', assets: 'visuals', media: 'visuals', image: 'visuals', video: 'visuals', simulation: 'visuals',
  quiz: 'quizzes',
  content: 'teaching', grounding: 'teaching', factual: 'teaching', flow: 'teaching', pedagogy: 'teaching', clarity: 'teaching', misconception: 'teaching', source: 'teaching', critic: 'teaching',
  objective: 'coverage', lecture: 'coverage',
  title: 'titles',
});

/**
 * Plain-words area of an issue code ("terminology.casing" -> "terms").
 * @param {string | null | undefined} code
 * @returns {string}
 */
export function areaOf(code) {
  const c = String(code || '');
  if (Object.prototype.hasOwnProperty.call(AREA_BY_CODE, c)) return AREA_BY_CODE[c];
  const prefix = c.split('.')[0];
  return Object.prototype.hasOwnProperty.call(AREA_BY_PREFIX, prefix) ? AREA_BY_PREFIX[prefix] : 'other';
}

/**
 * @param {string} id
 * @returns {string}
 */
export function areaLabel(id) {
  const a = AREAS.find((x) => x.id === id);
  return a ? a.label : 'Other';
}

/**
 * Headline for the issues panel.
 * @param {{ error: number, warning: number, info: number }} counts
 * @returns {{ level: 'good' | 'review' | 'fix', text: string }}
 */
export function statusOf(counts) {
  const { error = 0, warning = 0, info = 0 } = counts || {};
  const notes = info ? ` · ${info} note${info === 1 ? '' : 's'}` : '';
  if (error) {
    const review = warning ? `, ${warning} to review` : '';
    return { level: 'fix', text: `${error} need${error === 1 ? 's' : ''} fixing${review}${notes}` };
  }
  if (warning) return { level: 'review', text: `${warning} thing${warning === 1 ? '' : 's'} to review${notes}` };
  return { level: 'good', text: info ? `Looks good${notes}` : 'Looks good: nothing to fix' };
}

/**
 * Issues grouped by area (display order), each group sorted by severity then scene order.
 * @param {QualityIssue[]} issues
 * @param {Record<string, any> | null} sp
 * @returns {{ id: string, label: string, issues: QualityIssue[], counts: { error: number, warning: number, info: number } }[]}
 */
export function groupByArea(issues, sp) {
  /** @type {Map<string, QualityIssue[]>} */
  const by = new Map();
  for (const i of issues) {
    const id = areaOf(i.code);
    if (!by.has(id)) by.set(id, []);
    /** @type {QualityIssue[]} */ (by.get(id)).push(i);
  }
  return AREAS.filter((a) => by.has(a.id)).map((a) => {
    const list = /** @type {QualityIssue[]} */ (sortIssues(/** @type {QualityIssue[]} */ (by.get(a.id)), sp));
    return { id: a.id, label: a.label, issues: list, counts: countIssues(list) };
  });
}

/**
 * @param {{ code?: string, scene_id?: string | null, message?: string }} x
 * @returns {string}
 */
function repairKey(x) {
  return `${x.code || ''}|${x.scene_id || ''}|${x.message || ''}`;
}

/**
 * Attach the lint response's repairs to the issues they fix (matched by code, scene and message).
 * @param {QualityIssue[]} issues
 * @param {unknown} repairs   POST /lint `quality.repairs`
 * @returns {QualityIssue[]}
 */
export function attachRepairs(issues, repairs) {
  if (!Array.isArray(repairs) || !repairs.length) return issues;
  /** @type {Map<string, Repair>} */
  const by = new Map();
  for (const r of repairs) {
    if (r && typeof r === 'object' && Array.isArray(r.edits) && r.edits.length && typeof r.label === 'string') by.set(repairKey(r), /** @type {Repair} */ (r));
  }
  return issues.map((i) => {
    const r = by.get(repairKey(i));
    return r ? { ...i, repair: r } : i;
  });
}

/**
 * @param {any} obj
 * @param {Array<string | number>} path
 * @returns {any}
 */
function getIn(obj, path) {
  let cur = obj;
  for (const key of path) {
    if (cur === null || cur === undefined || typeof cur !== 'object') return undefined;
    cur = cur[key];
  }
  return cur;
}

/**
 * @param {any} obj   a scene clone (mutated)
 * @param {Array<string | number>} path
 * @param {string} value
 */
function setIn(obj, path, value) {
  let cur = obj;
  for (const key of path.slice(0, -1)) cur = cur[key];
  cur[path[path.length - 1]] = value;
}

/**
 * Apply a repair to a screenplay (immutable). Returns null when the draft changed since the check (some
 * `before` no longer matches, ignoring leading and trailing whitespace, which the server strips): nothing
 * is applied then.
 * @param {Record<string, any>} sp
 * @param {Repair} repair
 * @returns {Record<string, any> | null}
 */
export function applyRepair(sp, repair) {
  const edits = (repair && Array.isArray(repair.edits) ? repair.edits : []).filter(
    (e) => e && typeof e.scene_id === 'string' && Array.isArray(e.path) && e.path.length && typeof e.before === 'string' && typeof e.after === 'string',
  );
  if (!edits.length || !sp || !Array.isArray(sp.scenes)) return null;
  const byScene = new Map(sp.scenes.map((/** @type {any} */ s, /** @type {number} */ i) => [s.id, i]));
  for (const e of edits) {
    const idx = byScene.get(e.scene_id);
    if (idx === undefined) return null;
    const cur = getIn(sp.scenes[idx], e.path);
    // The server lints the parsed screenplay (str_strip_whitespace), so `before` never has the outer
    // whitespace a textarea keeps; that whitespace is dropped on save anyway.
    if (typeof cur !== 'string' || (cur !== e.before && cur.trim() !== e.before)) return null;
  }
  const scenes = sp.scenes.slice();
  /** @type {Set<number>} */
  const cloned = new Set();
  for (const e of edits) {
    const idx = /** @type {number} */ (byScene.get(e.scene_id));
    if (!cloned.has(idx)) {
      scenes[idx] = structuredClone(scenes[idx]);
      cloned.add(idx);
    }
    setIn(scenes[idx], e.path, e.after);
  }
  return { ...sp, scenes };
}

export const PRE_EXPORT_MAX = 12;

/**
 * Open errors and warnings for the render dialog (errors first, scene order), at most PRE_EXPORT_MAX.
 * @param {Issue[]} issues
 * @param {Record<string, any> | null} sp
 * @returns {{ items: { severity: string, text: string, sceneId: string | null }[], more: number }}
 */
export function preExportList(issues, sp) {
  const open = sortIssues(issues.filter((i) => i.severity === 'error' || i.severity === 'warning'), sp);
  const titles = new Map(((sp && sp.scenes) || []).map((/** @type {any} */ s, /** @type {number} */ i) => [s.id, `Scene ${i + 1}${s.title ? ` (${s.title})` : ''}`]));
  const items = open.slice(0, PRE_EXPORT_MAX).map((i) => ({
    severity: i.severity,
    text: `${i.scene_id ? titles.get(i.scene_id) || i.scene_id : 'Whole lecture'}: ${i.message}`,
    sceneId: i.scene_id || null,
  }));
  return { items, more: Math.max(0, open.length - PRE_EXPORT_MAX) };
}

// --- board fit measured in the preview ------------------------------------------------------------

/** The player's smallest fit scale (web/js/player/board.js MIN_FIT): below it the board scrolls. */
export const MIN_FIT = 0.62;
/** Text shrunk below this scale is noticeably small in the video. */
export const SMALL_TEXT_FIT = 0.75;

/**
 * The board fit of the scene the preview shows: `{ fit, overflow }`, or null when there is no laid-out board
 * (no board scene, a hidden preview, or a DOM without layout).
 * @param {ParentNode | null} root
 * @returns {{ fit: number, overflow: boolean } | null}
 */
export function measureBoard(root) {
  if (!root) return null;
  const board = /** @type {HTMLElement | null} */ (root.querySelector('.ap-board'));
  const body = board ? /** @type {HTMLElement | null} */ (board.querySelector('.ap-board-body')) : null;
  if (!board || !body || !(body.clientHeight > 0) || !body.children.length) return null;
  const fit = Number.parseFloat(board.style.getPropertyValue('--fit'));
  return { fit: Number.isFinite(fit) && fit > 0 ? fit : 1, overflow: body.scrollHeight > body.clientHeight + 1 };
}

/**
 * A measured board fit as a non-blocking issue (never part of the save checks), or null when it fits.
 * @param {string} sceneId
 * @param {number} index   0-based scene index
 * @param {{ fit: number, overflow: boolean } | null} m
 * @returns {Issue | null}
 */
export function fitIssue(sceneId, index, m) {
  if (!m) return null;
  if (m.overflow) {
    return {
      code: 'board.overflow',
      severity: 'warning',
      message: `In the preview, the board of scene ${index + 1} does not fit even at the smallest text size, so the player and the video scroll it and hide the first items. Split the scene or shorten its items.`,
      scene_id: sceneId,
      beat_id: null,
      source: 'local',
      fixable: false,
    };
  }
  if (m.fit < SMALL_TEXT_FIT) {
    return {
      code: 'board.small_text',
      severity: 'info',
      message: `In the preview, the board of scene ${index + 1} only fits with its text shrunk to ${Math.round(m.fit * 100)}%, which is small in the video. Fewer or shorter items keep it readable.`,
      scene_id: sceneId,
      beat_id: null,
      source: 'local',
      fixable: false,
    };
  }
  return null;
}
