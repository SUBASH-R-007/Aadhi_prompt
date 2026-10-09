// @ts-check
/**
 * Manim spec editor: either a library template with a params form generated from the
 * template's JSON schema (components/schemaForm.js; meta.manim_templates[].params_schema when
 * the server publishes it), or free-form code (AadhiScene subclass, `self.wait_until_beat(i)`
 * per beat). Without a published schema the parameters are edited as JSON; empty parameters
 * are reported by validationIssues (every template needs parameters).
 * Text edits are debounced; `flush()` delivers a pending edit immediately (the owner calls it
 * before switching scenes) and `destroy()` flushes too.
 */

import { h, clear } from '../../../shared/dom.js';
import { field, select, textarea, group } from '../../components/form.js';
import { createSchemaForm } from '../../components/schemaForm.js';
import { debounce, uid } from '../../util.js';
import { DEFAULT_MANIM_CODE } from '../../lib/screenplayEdit.js';
import { templatesOf, freeformAllowed, initialParams, hasParamsSchema, hasExampleParams } from '../../lib/manimTemplates.js';

/**
 * @typedef {{ template: string | null, params: Record<string, any>, code: string | null }} ManimSpec
 */

/**
 * @param {{ spec: ManimSpec, meta: any, beatsCount: number, target: 'fullscreen' | 'panel', onChange: (spec: ManimSpec) => void, fkPrefix: string }} opts
 * @returns {{ el: HTMLElement, flush: () => void, destroy: () => void }}
 */
export function manimEditor(opts) {
  const templates = templatesOf(opts.meta);
  const freeform = freeformAllowed(opts.meta);
  /** @type {ManimSpec} */
  let spec = { template: opts.spec.template || null, params: opts.spec.params || {}, code: opts.spec.code || null };
  const name = uid('manim-mode');
  const body = h('div', { class: 'manim-body' });
  /** @type {{ destroy: () => void } | null} */
  let form = null;

  /**
   * @param {'template' | 'code'} mode
   * @param {string} label
   * @param {boolean} [disabled]
   */
  const radio = (mode, label, disabled = false) => {
    const r = h('input', { type: 'radio', value: mode, disabled, dataset: { fk: `${opts.fkPrefix}:mode:${mode}` } });
    r.name = name; // generated id, never model data (dom.js forbids name attributes)
    r.checked = (mode === 'code') === !!spec.code;
    r.addEventListener('change', () => {
      if (!r.checked) return;
      emitDebounced.cancel();
      if (mode === 'code') {
        spec = { template: null, params: {}, code: spec.code || DEFAULT_MANIM_CODE };
      } else {
        const tpl = templates[0];
        spec = tpl ? { template: tpl.name, params: initialParams(tpl), code: null } : { template: null, params: {}, code: null };
      }
      emit();
      render();
    });
    return h('label', { class: 'field field-inline' }, r, h('span', {}, label));
  };

  const emit = () => opts.onChange({ ...spec });
  const emitDebounced = debounce(emit, 250);

  function render() {
    if (form) form.destroy();
    form = null;
    clear(body);
    if (spec.code !== null && spec.code !== undefined && !spec.template) {
      const code = textarea({
        value: spec.code || '',
        rows: 14,
        mono: true,
        maxLength: 20000,
        dataset: { fk: `${opts.fkPrefix}:code` },
        onInput: (v) => {
          spec = { template: null, params: {}, code: v };
          emitDebounced();
        },
      });
      body.append(
        field('Manim code', code, {
          hint: `Define one class extending AadhiScene and call self.wait_until_beat(i) before the animation for beat i (this scene has ${opts.beatsCount} beat${opts.beatsCount === 1 ? '' : 's'}). Runs in a sandbox; imports are limited to manim, math and numpy. Press Esc then Tab to leave the editor.`,
        }),
      );
      return;
    }
    if (!templates.length) {
      body.append(h('p', { class: 'muted' }, 'This server lists no Manim templates.'), paramsJson());
      return;
    }
    const tpl = templates.find((t) => t.name === spec.template);
    const tplSel = select({
      options: [...(tpl ? [] : [{ value: spec.template || '', label: spec.template ? `${spec.template} (unknown)` : 'Choose a template' }]), ...templates.map((t) => ({ value: t.name, label: t.title || t.name }))],
      value: spec.template || '',
      dataset: { fk: `${opts.fkPrefix}:template` },
      onChange: (v) => {
        emitDebounced.cancel();
        const next = templates.find((t) => t.name === v);
        spec = { template: v, params: initialParams(next), code: null };
        emit();
        render();
      },
    });
    body.append(field('Template', tplSel));
    if (tpl) {
      body.append(
        h(
          'div',
          { class: 'template-info' },
          tpl.description ? h('p', {}, tpl.description) : null,
          tpl.steps_hint ? h('p', { class: 'muted small' }, `Steps: ${tpl.steps_hint}. One animation step plays per beat (this scene has ${opts.beatsCount}).`) : null,
        ),
      );
    }
    if (tpl && hasParamsSchema(tpl)) {
      const sf = createSchemaForm(/** @type {any} */ (tpl.params_schema), spec.params, {
        onChange: (value) => {
          spec = { ...spec, params: value };
          validateSoon();
          emitDebounced();
        },
      });
      const validateSoon = debounce(() => sf.validate(), 400);
      form = {
        destroy: () => {
          validateSoon.cancel();
          sf.destroy();
        },
      };
      body.append(group('Parameters', sf.el, { hint: 'Fields come from the template definition.' }));
      sf.validate();
    } else {
      if (tpl && !hasExampleParams(tpl)) {
        body.append(
          h(
            'p',
            { class: 'notice small' },
            'This server does not publish the parameter fields of its templates. Enter the parameters as a JSON object (see the description above); the lecture check reports anything the template does not accept.',
          ),
        );
      }
      body.append(paramsJson());
    }
  }

  function paramsJson() {
    const error = h('div', { class: 'field-error', role: 'alert', hidden: true });
    const ta = textarea({
      value: JSON.stringify(spec.params || {}, null, 2),
      rows: 8,
      mono: true,
      dataset: { fk: `${opts.fkPrefix}:params` },
      onInput: (v) => {
        try {
          const parsed = JSON.parse(v || '{}');
          if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('Parameters must be a JSON object.');
          error.hidden = true;
          spec = { ...spec, params: parsed };
          emitDebounced();
        } catch (e) {
          error.textContent = e instanceof Error ? e.message : 'Invalid JSON';
          error.hidden = false;
        }
      },
    });
    return h('div', {}, field('Parameters (JSON)', ta, { hint: 'The server validates parameters against the template.' }), error);
  }

  const el = h(
    'div',
    { class: 'manim-editor' },
    h('div', { class: 'row gap wrap', role: 'radiogroup', 'aria-label': 'Animation source' }, radio('template', 'Template (recommended)', !templates.length && !spec.template), radio('code', 'Custom code', !freeform && !spec.code)),
    !freeform ? h('p', { class: 'muted small' }, 'Custom code is disabled on this server.') : null,
    body,
  );
  render();
  return {
    el,
    flush() {
      emitDebounced.flush();
    },
    destroy() {
      emitDebounced.flush();
      if (form) form.destroy();
      form = null;
    },
  };
}
