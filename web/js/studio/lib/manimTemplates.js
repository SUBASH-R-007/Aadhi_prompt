// @ts-check
/**
 * Manim template metadata helpers (GET /api/meta `manim_templates`).
 *
 * docs/API.md documents template entries as `{name, title, description, steps_hint}`; the
 * parameter schema and example parameters (`params_schema`, `example_params`, both present on
 * aadhi.manim.base.TemplateInfo) are optional extras the Studio uses when the server sends them.
 * Every library template requires parameters, so a template spec with empty params is reported
 * as a local error instead of being saved as `{}` (it would fail at build time).
 */

import { defaultFor, validateValue } from './jsonSchema.js';
import { clone } from '../util.js';

/**
 * @typedef {object} TemplateMeta
 * @property {string} name
 * @property {string} [title]
 * @property {string} [description]
 * @property {string} [steps_hint]
 * @property {Record<string, any>} [params_schema]
 * @property {Record<string, any>} [example_params]
 */

/**
 * @param {any} meta  GET /api/meta (or null)
 * @returns {TemplateMeta[]}
 */
export function templatesOf(meta) {
  const list = meta && Array.isArray(meta.manim_templates) ? meta.manim_templates : [];
  return list.filter((/** @type {any} */ t) => t && typeof t.name === 'string' && t.name);
}

/**
 * True when free-form Manim code is allowed (absent flag = allowed).
 * @param {any} meta
 */
export function freeformAllowed(meta) {
  return !(meta && meta.features && meta.features.manim_freeform === false);
}

/**
 * @param {TemplateMeta | null | undefined} tpl
 * @returns {boolean} the server sent a usable parameter schema
 */
export function hasParamsSchema(tpl) {
  return !!(tpl && tpl.params_schema && typeof tpl.params_schema === 'object' && !Array.isArray(tpl.params_schema));
}

/**
 * @param {TemplateMeta | null | undefined} tpl
 * @returns {boolean} the server sent non-empty example parameters
 */
export function hasExampleParams(tpl) {
  const ex = tpl && tpl.example_params;
  return !!(ex && typeof ex === 'object' && !Array.isArray(ex) && Object.keys(ex).length);
}

/**
 * Starting parameters for a template: its example, else schema defaults, else `{}` (which
 * `manimSpecProblem` reports until the teacher fills the parameters in).
 * @param {TemplateMeta | null | undefined} tpl
 * @returns {Record<string, any>}
 */
export function initialParams(tpl) {
  if (!tpl) return {};
  if (hasExampleParams(tpl)) return clone(/** @type {Record<string, any>} */ (tpl.example_params));
  if (hasParamsSchema(tpl)) {
    try {
      const v = defaultFor(/** @type {any} */ (tpl.params_schema), /** @type {any} */ (tpl.params_schema));
      if (v && typeof v === 'object' && !Array.isArray(v)) return v;
    } catch {
      /* unusable schema: fall through */
    }
  }
  return {};
}

/**
 * The template new simulation scenes / Manim panels start from (screenplayEdit `manimTemplate`
 * option): the first template with example parameters. When the server publishes no examples,
 * returns null so new specs start from the free-form code skeleton (valid as is) when custom
 * code is allowed, otherwise the first template (its parameters must then be entered).
 * @param {any} meta
 * @returns {{ name: string, example_params: Record<string, any> } | null}
 */
export function defaultManimTemplate(meta) {
  const templates = templatesOf(meta);
  const withExample = templates.find((t) => hasExampleParams(t));
  if (withExample) return { name: withExample.name, example_params: initialParams(withExample) };
  if (freeformAllowed(meta) || !templates.length) return null;
  return { name: templates[0].name, example_params: initialParams(templates[0]) };
}

/**
 * Client-side check of a Manim spec (subset of the server rules).
 * @param {any} spec  {template, params, code}
 * @param {TemplateMeta[] | null} [templates]  known templates (enables schema validation)
 * @param {{ requireParams?: boolean }} [opts]  requireParams=false only checks the spec shape
 *   (e.g. an uploaded video replaces the animation)
 * @returns {string | null} problem text (no trailing period) or null
 */
export function manimSpecProblem(spec, templates = null, opts = {}) {
  const m = spec || {};
  const hasCode = !!(m.code && String(m.code).trim());
  if (!!m.template === hasCode) return 'needs either a template or free-form code';
  if (!m.template) return null;
  const params = m.params && typeof m.params === 'object' && !Array.isArray(m.params) ? m.params : null;
  if (!params) return 'has template parameters that are not a JSON object';
  if (opts.requireParams === false) return null;
  const tpl = templates ? templates.find((t) => t.name === m.template) : undefined;
  if (tpl && hasParamsSchema(tpl)) {
    const errors = validateValue(/** @type {any} */ (tpl.params_schema), params);
    if (errors.length) return `has invalid template parameters (${errors[0].path ? `${errors[0].path}: ` : ''}${errors[0].message.replace(/\.$/, '')})`;
    return null;
  }
  if (!Object.keys(params).length) return `needs parameters for the “${(tpl && tpl.title) || m.template}” template`;
  return null;
}
