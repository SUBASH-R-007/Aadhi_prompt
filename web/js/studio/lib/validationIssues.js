// @ts-check
/**
 * Issue helpers for the editor:
 *  - `localProblems(sp, opts)`: fast client-side checks for fields the server rejects with 422
 *    (empty narration, missing board payloads, quiz shape ...) or that cannot build (Manim
 *    template without parameters). Shown as errors and block save.
 *  - `issuesFromValidationError(detail, sp)`: map Pydantic 422 `detail` locations
 *    (["body","screenplay","scenes",3,"content","beats",0,"narration"]) to scene/beat issues.
 *  - grouping/sorting/counting helpers for the issues panel and badges.
 */

import { isBoardScene, sceneBeats, TEXT_REQUIRED_KINDS, BOARD_ITEM_LABELS } from './screenplayEdit.js';
import { checkExpr } from './expr.js';
import { manimSpecProblem } from './manimTemplates.js';

/**
 * @typedef {object} Issue
 * @property {string} code
 * @property {'error' | 'warning' | 'info'} severity
 * @property {string} message
 * @property {string | null} [scene_id]
 * @property {string | null} [beat_id]
 * @property {string} [source]
 * @property {boolean} [fixable]
 */

const SEVERITY_ORDER = { error: 0, warning: 1, info: 2 };

/**
 * TeX macros the schema rejects (aadhi/schemas/screenplay.py `_FORBIDDEN_TEX`): they could
 * produce links/attributes in MathJax output. Mirrored here for instant feedback.
 */
export const FORBIDDEN_TEX = /\\(href|url|style|class|cssId|require|data|html|unicode|mmlToken|bbox|special|input|include|def|let|newcommand|renewcommand)(?![\p{L}\p{N}_])/u;

/**
 * @param {unknown} latex
 * @returns {string | null} the forbidden macro (e.g. "\\href") or null
 */
export function forbiddenTexMacro(latex) {
  const m = FORBIDDEN_TEX.exec(String(latex || ''));
  return m ? `\\${m[1]}` : null;
}

/**
 * @param {string} code
 * @param {string} message
 * @param {string | null} sceneId
 * @param {string | null} [beatId]
 * @returns {Issue}
 */
function local(code, message, sceneId, beatId = null) {
  return { code, severity: 'error', message, scene_id: sceneId, beat_id: beatId, source: 'local', fixable: false };
}

/**
 * @typedef {object} LocalCheckOptions
 * @property {import('./manimTemplates.js').TemplateMeta[] | null} [manimTemplates]  known templates
 *   (meta.manim_templates): parameters are validated against their schema when it is published
 */

/**
 * Client-side structural checks (subset of the server schema validation).
 * @param {Record<string, any> | null} sp
 * @param {LocalCheckOptions} [opts]
 * @returns {Issue[]}
 */
export function localProblems(sp, opts = {}) {
  /** @type {Issue[]} */
  const out = [];
  if (!sp) return out;
  const templates = opts.manimTemplates || null;
  for (const scene of sp.scenes || []) {
    const sid = scene.id;
    const label = scene.title ? `“${scene.title}”` : sid;
    for (const { beat, phase, index } of sceneBeats(scene)) {
      if (!String(beat.narration || '').trim()) {
        out.push(local('local.beat_empty', `${phase === 'reveal' ? 'Reveal beat' : 'Beat'} ${index + 1} of ${label} has no narration.`, sid, beat.id));
      } else if (String(beat.narration).length > 1500) {
        out.push(local('local.beat_too_long', `Beat ${index + 1} of ${label} is longer than 1500 characters.`, sid, beat.id));
      }
    }
    if (scene.type !== 'chapter_card' && !(scene.beats || []).length) out.push(local('local.no_beats', `${label} needs at least one beat.`, sid));
    if (isBoardScene(scene)) {
      (scene.board || []).forEach((/** @type {any} */ item, /** @type {number} */ i) => {
        const what = `${BOARD_ITEM_LABELS[item.kind] || item.kind} ${i + 1} in ${label}`;
        if (TEXT_REQUIRED_KINDS.has(item.kind) && !String(item.text || '').trim()) out.push(local('local.item_text', `${what} needs text.`, sid));
        if (item.kind === 'definition' && !String(item.term || '').trim() && !String(item.text || '').trim()) out.push(local('local.item_definition', `${what} needs a term or text.`, sid));
        if (item.kind === 'formula' && !String(item.latex || '').trim()) out.push(local('local.item_latex', `${what} needs LaTeX.`, sid));
        const badTex = [item.latex, ...(item.variables || []).map((/** @type {any} */ v) => v && v.symbol_latex)].map(forbiddenTexMacro).find(Boolean);
        if (badTex) out.push(local('local.latex_forbidden', `${what} uses the TeX command ${badTex}, which is not allowed.`, sid));
        if (item.kind === 'code' && !String(item.code || '').trim()) out.push(local('local.item_code', `${what} needs code.`, sid));
        if (item.kind === 'figure' && !item.figure_id) out.push(local('local.item_figure', `${what} needs a figure.`, sid));
        if (item.kind === 'table') {
          const width = (item.headers || []).length;
          if (!width || !(item.rows || []).length) out.push(local('local.item_table', `${what} needs headers and at least one row.`, sid));
          else if (item.rows.some((/** @type {any[]} */ r) => r.length !== width)) out.push(local('local.item_table', `${what} has rows of the wrong length.`, sid));
        }
      });
    }
    if (scene.type === 'quiz_checkpoint') {
      if (!String(scene.question || '').trim()) out.push(local('local.quiz_question', `${label} needs a question.`, sid));
      const opts = (scene.options || []).map((/** @type {string} */ o) => String(o || '').trim());
      if (opts.some((/** @type {string} */ o) => !o)) out.push(local('local.quiz_option_empty', `${label} has an empty answer option.`, sid));
      if (new Set(opts.map((/** @type {string} */ o) => o.toLowerCase())).size !== opts.length) out.push(local('local.quiz_options_distinct', `${label}: answer options must be distinct.`, sid));
    }
    if (scene.type === 'ai_video' && !String(scene.video_prompt || '').trim()) out.push(local('local.video_prompt', `${label} needs a video prompt.`, sid));
    if (scene.type === 'interactive' && !String(scene.p5_code || '').trim()) out.push(local('local.p5_code', `${label} needs p5 code.`, sid));
    if (scene.type === 'simulation') {
      // An uploaded override video replaces the animation, so its parameters are not needed.
      const problem = manimSpecProblem(scene.manim, templates, { requireParams: !scene.override_asset_key });
      if (problem) out.push(local('local.manim_spec', `The animation of ${label} ${problem}.`, sid));
    }
    out.push(...sidePanelProblems(scene, label, templates));
  }
  return out;
}

/**
 * @param {Record<string, any>} scene
 * @param {string} label
 * @param {import('./manimTemplates.js').TemplateMeta[] | null} templates
 * @returns {Issue[]}
 */
function sidePanelProblems(scene, label, templates) {
  const p = scene.side_panel;
  if (!p) return [];
  /** @type {Issue[]} */
  const out = [];
  const sid = scene.id;
  const need = {
    figure: p.figure_id || p.override_asset_key,
    image: p.image_prompt || p.override_asset_key,
    chart: p.chart,
    graph: p.graph,
    model_3d: p.model_3d,
    manim: p.manim || p.override_asset_key,
    terminal: p.terminal,
    quiz: p.quiz,
    gif: p.gif_query,
  };
  if (p.kind !== 'skill_tree' && !(/** @type {any} */ (need))[p.kind]) out.push(local('local.panel_payload', `The ${p.kind} side panel of ${label} is missing its content.`, sid));
  if (p.kind === 'chart' && p.chart) {
    const n = (p.chart.labels || []).length;
    if (!n) out.push(local('local.chart_labels', `The chart in ${label} needs labels.`, sid));
    if (!(p.chart.datasets || []).length) out.push(local('local.chart_datasets', `The chart in ${label} needs a dataset.`, sid));
    for (const ds of p.chart.datasets || []) {
      if ((ds.data || []).length !== n) out.push(local('local.chart_lengths', `Every chart dataset in ${label} needs one value per label.`, sid));
      if ((ds.data || []).some((/** @type {any} */ v) => typeof v !== 'number' || !Number.isFinite(v))) out.push(local('local.chart_values', `Chart values in ${label} must be numbers.`, sid));
    }
  }
  if (p.kind === 'graph' && p.graph) {
    const g = p.graph;
    if (!(g.functions || []).length && !(g.points || []).length) out.push(local('local.graph_empty', `The graph in ${label} needs a function or a point.`, sid));
    for (const f of g.functions || []) {
      const err = checkExpr(String(f.expr || ''));
      if (err) out.push(local('local.graph_expr', `Graph function “${f.expr}” in ${label}: ${err}`, sid));
    }
    if (Array.isArray(g.x_range) && !(g.x_range[0] < g.x_range[1])) out.push(local('local.graph_range', `The graph x range in ${label} must be increasing.`, sid));
    if (Array.isArray(g.y_range) && !(g.y_range[0] < g.y_range[1])) out.push(local('local.graph_range', `The graph y range in ${label} must be increasing.`, sid));
  }
  if (p.kind === 'quiz' && p.quiz) {
    const opts = p.quiz.options || [];
    if (opts.length < 2) out.push(local('local.panel_quiz', `The quiz panel in ${label} needs at least two options.`, sid));
    if (!(p.quiz.correct_index >= 0 && p.quiz.correct_index < opts.length)) out.push(local('local.panel_quiz', `The quiz panel in ${label} has no valid correct option.`, sid));
  }
  if (p.kind === 'model_3d' && p.model_3d && !(p.model_3d.primitives || []).length) {
    out.push(local('local.model_3d', `The 3D model in ${label} needs at least one shape.`, sid));
  }
  if (p.kind === 'manim' && p.manim) {
    const problem = manimSpecProblem(p.manim, templates, { requireParams: !p.override_asset_key });
    if (problem) out.push(local('local.manim_spec', `The animation panel in ${label} ${problem}.`, sid));
  }
  return out;
}

/**
 * Map a Pydantic 422 detail to issues anchored to scenes/beats where possible.
 * @param {any} detail   ApiError.detail (array of {loc, msg, type} or a string)
 * @param {Record<string, any> | null} sp
 * @returns {Issue[]}
 */
export function issuesFromValidationError(detail, sp) {
  if (!Array.isArray(detail)) {
    return [{ code: 'schema.invalid', severity: 'error', message: String(detail || 'The screenplay is invalid.'), scene_id: null, beat_id: null, source: 'server', fixable: false }];
  }
  return detail.map((/** @type {any} */ d) => {
    const loc = Array.isArray(d && d.loc) ? d.loc : [];
    let sceneId = null;
    let beatId = null;
    const si = loc.indexOf('scenes');
    if (si >= 0 && typeof loc[si + 1] === 'number' && sp && sp.scenes && sp.scenes[loc[si + 1]]) {
      const scene = sp.scenes[loc[si + 1]];
      sceneId = scene.id;
      for (const key of ['beats', 'reveal_beats']) {
        const bi = loc.indexOf(key, si);
        if (bi >= 0 && typeof loc[bi + 1] === 'number') {
          const list = scene[key] || [];
          if (list[loc[bi + 1]]) beatId = list[loc[bi + 1]].id;
        }
      }
    }
    const where = loc
      .filter((/** @type {any} */ x) => x !== 'body' && x !== 'screenplay')
      .map(String)
      .join('.');
    const msg = String((d && d.msg) || 'invalid value').replace(/^Value error, /, '');
    return { code: 'schema.invalid', severity: 'error', message: where ? `${where}: ${msg}` : msg, scene_id: sceneId, beat_id: beatId, source: 'server', fixable: false };
  });
}

/**
 * Sort by severity, then scene order.
 * @param {Issue[]} issues
 * @param {Record<string, any> | null} sp
 * @returns {Issue[]}
 */
export function sortIssues(issues, sp) {
  const order = new Map(((sp && sp.scenes) || []).map((/** @type {any} */ s, /** @type {number} */ i) => [s.id, i]));
  return issues.slice().sort((a, b) => {
    const sa = SEVERITY_ORDER[a.severity] ?? 3;
    const sb = SEVERITY_ORDER[b.severity] ?? 3;
    if (sa !== sb) return sa - sb;
    const oa = a.scene_id && order.has(a.scene_id) ? /** @type {number} */ (order.get(a.scene_id)) : -1;
    const ob = b.scene_id && order.has(b.scene_id) ? /** @type {number} */ (order.get(b.scene_id)) : -1;
    return oa - ob;
  });
}

/**
 * @param {Issue[]} issues
 * @returns {{ error: number, warning: number, info: number }}
 */
export function countIssues(issues) {
  const c = { error: 0, warning: 0, info: 0 };
  for (const i of issues) if (i.severity in c) c[i.severity] += 1;
  return c;
}

/**
 * Per-scene counts for scene-list badges.
 * @param {Issue[]} issues
 * @returns {Map<string, { error: number, warning: number, info: number }>}
 */
export function issuesByScene(issues) {
  /** @type {Map<string, { error: number, warning: number, info: number }>} */
  const m = new Map();
  for (const i of issues) {
    if (!i.scene_id) continue;
    if (!m.has(i.scene_id)) m.set(i.scene_id, { error: 0, warning: 0, info: 0 });
    const c = /** @type {{ error: number, warning: number, info: number }} */ (m.get(i.scene_id));
    if (i.severity in c) c[i.severity] += 1;
  }
  return m;
}
