// @ts-check
/**
 * JSON-Schema -> form generator (Manim template params and any other Pydantic schema).
 *
 *   const form = createSchemaForm(schema, value, { onChange });
 *   container.append(form.el);
 *   form.getValue(); form.validate(); // -> [{path, message}] and shows errors inline
 *
 * Kinds (see lib/jsonSchema.js analyze): string (input/textarea), number/integer (number
 * input), boolean (checkbox), enum (select), const (read-only), nullable (toggle + editor),
 * object (fieldset), array (add/remove/reorder rows, min/max items), tuple (fixed rows),
 * json (raw JSON textarea for constructs the generator does not model).
 */

import { h, clear } from '../../shared/dom.js';
import { analyze, defaultFor, humanize, joinPath, validateValue } from '../lib/jsonSchema.js';
import { clone } from '../util.js';
import { button } from './form.js';

/** @typedef {import('../lib/jsonSchema.js').Schema} Schema */
/** @typedef {import('../lib/jsonSchema.js').SchemaError} SchemaError */

/**
 * @typedef {object} Editor
 * @property {HTMLElement} el
 * @property {() => any} get           current value (undefined = empty optional)
 * @property {(path: string) => void} [register]
 * @property {() => SchemaError[]} [problems]  local problems (e.g. JSON syntax)
 */

/**
 * @typedef {object} SchemaFormOptions
 * @property {(value: any) => void} [onChange]
 * @property {boolean} [disabled]
 * @property {string} [rootLabel]
 */

/**
 * @typedef {object} SchemaForm
 * @property {HTMLElement} el
 * @property {() => any} getValue
 * @property {(value: any) => void} setValue
 * @property {() => SchemaError[]} validate      validates and shows errors inline
 * @property {(errors: SchemaError[]) => void} showErrors
 * @property {() => void} destroy
 */

/** Marker for unparseable JSON in raw editors. */
export class InvalidJson {
  /** @param {string} text @param {string} message */
  constructor(text, message) {
    this.text = text;
    this.message = message;
  }
}

/**
 * Build a form for `schema` initialised with `value` (defaults fill missing parts).
 * @param {Schema} schema
 * @param {any} value
 * @param {SchemaFormOptions} [opts]
 * @returns {SchemaForm}
 */
export function createSchemaForm(schema, value, opts = {}) {
  const root = schema;
  const el = h('div', { class: 'schema-form' });
  /** @type {Map<string, HTMLElement>} */
  let errorSlots = new Map();
  /** @type {Editor} */
  let editor;
  let destroyed = false;

  const notify = () => {
    if (!destroyed && opts.onChange) opts.onChange(getValue());
  };

  /** @param {any} v */
  const build = (v) => {
    errorSlots = new Map();
    const initial = v === undefined ? defaultFor(schema, root) : v;
    editor = buildEditor(schema, initial, { root, path: '', label: opts.rootLabel || '', required: true, notify, errorSlots, disabled: !!opts.disabled });
    clear(el);
    el.appendChild(editor.el);
  };

  const getValue = () => editor.get();

  /** @param {SchemaError[]} errors */
  const showErrors = (errors) => {
    for (const slot of errorSlots.values()) {
      slot.textContent = '';
      slot.hidden = true;
      const ctl = slot.parentElement && slot.parentElement.querySelector('input, select, textarea');
      if (ctl) ctl.removeAttribute('aria-invalid');
    }
    for (const err of errors) {
      let p = err.path;
      let slot = errorSlots.get(p);
      while (!slot && p) {
        p = parentPath(p);
        slot = errorSlots.get(p);
      }
      if (!slot) continue;
      slot.textContent = slot.textContent ? `${slot.textContent} ${err.message}` : err.message;
      slot.hidden = false;
      const ctl = slot.parentElement && slot.parentElement.querySelector(':scope > label input, :scope > label select, :scope > label textarea, :scope > textarea');
      if (ctl) ctl.setAttribute('aria-invalid', 'true');
    }
  };

  const validate = () => {
    const local = collectProblems(editor);
    const v = getValue();
    const errors = local.length ? local : validateValue(schema, stripInvalid(v), root, '');
    showErrors(errors);
    return errors;
  };

  build(value);
  return {
    el,
    getValue,
    setValue: (v) => build(clone(v)),
    validate,
    showErrors,
    destroy: () => {
      destroyed = true;
      clear(el);
    },
  };
}

/** @param {Editor} editor @returns {SchemaError[]} */
function collectProblems(editor) {
  return editor.problems ? editor.problems() : [];
}

/** Replace InvalidJson markers by undefined (their errors are reported separately). @param {any} v @returns {any} */
function stripInvalid(v) {
  if (v instanceof InvalidJson) return undefined;
  if (Array.isArray(v)) return v.map(stripInvalid);
  if (v && typeof v === 'object') {
    /** @type {Record<string, any>} */
    const out = {};
    for (const [k, x] of Object.entries(v)) out[k] = stripInvalid(x);
    return out;
  }
  return v;
}

/** @param {string} path */
function parentPath(path) {
  const m = path.match(/^(.*)(\[\d+\]|\.[^.[\]]+)$/);
  if (m) return m[1];
  return '';
}

/**
 * @typedef {object} BuildCtx
 * @property {Schema} root
 * @property {string} path
 * @property {string} label
 * @property {boolean} required
 * @property {() => void} notify
 * @property {Map<string, HTMLElement>} errorSlots
 * @property {boolean} disabled
 */

/**
 * @param {BuildCtx} ctx
 */
function errorSlot(ctx) {
  const slot = h('div', { class: 'field-error', role: 'alert', hidden: true });
  ctx.errorSlots.set(ctx.path, slot);
  return slot;
}

/**
 * @param {Schema} s
 */
function descriptionOf(s) {
  return typeof s.description === 'string' && s.description.trim() ? s.description.trim() : null;
}

/**
 * @param {Schema} schema
 * @param {any} value
 * @param {BuildCtx} ctx
 * @returns {Editor}
 */
function buildEditor(schema, value, ctx) {
  let info;
  try {
    info = analyze(schema, ctx.root);
  } catch {
    return jsonEditor({}, value, ctx);
  }
  if (info.nullable && info.kind !== 'enum') return nullableEditor(schema, info.schema, value, ctx);
  return kindEditor(info.kind, info.schema, value, ctx, info.nullable);
}

/**
 * @param {string} kind
 * @param {Schema} s
 * @param {any} value
 * @param {BuildCtx} ctx
 * @param {boolean} nullable
 * @returns {Editor}
 */
function kindEditor(kind, s, value, ctx, nullable) {
  switch (kind) {
    case 'string':
      return stringEditor(s, value, ctx);
    case 'number':
    case 'integer':
      return numberEditor(s, value, ctx, kind === 'integer');
    case 'boolean':
      return booleanEditor(s, value, ctx);
    case 'enum':
      return enumEditor(s, value, ctx, nullable);
    case 'const':
      return constEditor(s, ctx);
    case 'object':
      return objectEditor(s, value, ctx);
    case 'array':
      return arrayEditor(s, value, ctx);
    case 'tuple':
      return tupleEditor(s, value, ctx);
    default:
      return jsonEditor(s, value, ctx);
  }
}

/**
 * Wrap a simple control with label, description and error slot.
 * @param {BuildCtx} ctx
 * @param {Schema} s
 * @param {HTMLElement} control
 * @param {boolean} [inline]
 */
function labelled(ctx, s, control, inline = false) {
  const text = s.title || (ctx.label ? humanize(ctx.label) : '');
  const desc = descriptionOf(s);
  const slot = errorSlot(ctx);
  if (!text) return h('div', { class: 'sf-field' }, control, desc ? h('div', { class: 'field-hint' }, desc) : null, slot);
  const lab = inline
    ? h('label', { class: 'field field-inline' }, control, h('span', { class: 'field-label' }, text))
    : h('label', { class: 'field' }, h('span', { class: 'field-label' }, text, ctx.required ? h('span', { class: 'req', 'aria-hidden': 'true' }, ' *') : null), control);
  if (ctx.required) control.setAttribute('aria-required', 'true');
  return h('div', { class: ['sf-field', 'field-wrap', inline ? 'field-wrap-inline' : ''] }, lab, desc ? h('div', { class: 'field-hint' }, desc) : null, slot);
}

/** @param {Schema} s @param {any} value @param {BuildCtx} ctx @returns {Editor} */
function stringEditor(s, value, ctx) {
  const long = (typeof s.maxLength === 'number' && s.maxLength > 160) || s.format === 'textarea' || (typeof value === 'string' && value.includes('\n'));
  /** @type {HTMLInputElement | HTMLTextAreaElement} */
  const control = long
    ? h('textarea', { class: 'input textarea', rows: '3', maxlength: s.maxLength ? String(s.maxLength) : undefined, disabled: ctx.disabled })
    : h('input', { class: 'input', type: 'text', maxlength: s.maxLength ? String(s.maxLength) : undefined, disabled: ctx.disabled });
  if (s.examples && Array.isArray(s.examples) && s.examples.length) control.setAttribute('placeholder', String(s.examples[0]));
  control.value = typeof value === 'string' ? value : value == null ? '' : String(value);
  control.addEventListener('input', ctx.notify);
  const required = ctx.required;
  return {
    el: labelled(ctx, s, control),
    get: () => (control.value === '' && !required ? undefined : control.value),
  };
}

/** @param {Schema} s @param {any} value @param {BuildCtx} ctx @param {boolean} integer @returns {Editor} */
function numberEditor(s, value, ctx, integer) {
  const control = h('input', {
    class: 'input',
    type: 'number',
    step: integer ? '1' : s.multipleOf ? String(s.multipleOf) : 'any',
    min: typeof s.minimum === 'number' ? String(s.minimum) : undefined,
    max: typeof s.maximum === 'number' ? String(s.maximum) : undefined,
    disabled: ctx.disabled,
    inputmode: integer ? 'numeric' : 'decimal',
  });
  control.value = typeof value === 'number' && Number.isFinite(value) ? String(value) : '';
  control.addEventListener('input', ctx.notify);
  return {
    el: labelled(ctx, s, control),
    get: () => {
      const raw = control.value.trim();
      if (raw === '') return undefined;
      const n = Number(raw);
      return Number.isFinite(n) ? n : raw;
    },
  };
}

/** @param {Schema} s @param {any} value @param {BuildCtx} ctx @returns {Editor} */
function booleanEditor(s, value, ctx) {
  const control = h('input', { type: 'checkbox', class: 'checkbox', disabled: ctx.disabled });
  control.checked = value === true;
  control.addEventListener('change', ctx.notify);
  return { el: labelled(ctx, s, control, true), get: () => control.checked };
}

/** @param {Schema} s @param {any} value @param {BuildCtx} ctx @param {boolean} nullable @returns {Editor} */
function enumEditor(s, value, ctx, nullable) {
  const values = /** @type {any[]} */ (s.enum);
  // An empty choice means "null" (nullable) or "use the server default" (optional without a
  // schema default); optional enums with a default always show a concrete value.
  const allowEmpty = nullable || (!ctx.required && !('default' in s));
  const control = h(
    'select',
    { class: 'input select', disabled: ctx.disabled },
    allowEmpty ? h('option', { value: '' }, nullable ? '— none —' : '— default —') : null,
    values.map((v, i) => h('option', { value: String(i) }, String(v))),
  );
  const idx = values.findIndex((v) => v === value);
  control.value = idx >= 0 ? String(idx) : allowEmpty ? '' : '0';
  control.addEventListener('change', ctx.notify);
  return {
    el: labelled(ctx, s, control),
    get: () => {
      if (control.value === '') return nullable ? null : undefined;
      return values[Number(control.value)];
    },
  };
}

/** @param {Schema} s @param {BuildCtx} ctx @returns {Editor} */
function constEditor(s, ctx) {
  const text = s.title || (ctx.label ? humanize(ctx.label) : 'Value');
  errorSlot(ctx);
  return {
    el: h('div', { class: 'sf-const' }, h('span', { class: 'field-label' }, text), h('code', {}, JSON.stringify(s.const))),
    get: () => clone(s.const),
  };
}

/**
 * @param {Schema} original
 * @param {Schema} inner
 * @param {any} value
 * @param {BuildCtx} ctx
 * @returns {Editor}
 */
function nullableEditor(original, inner, value, ctx) {
  const text = inner.title || (ctx.label ? humanize(ctx.label) : 'Value');
  const toggle = h('input', { type: 'checkbox', class: 'checkbox', disabled: ctx.disabled });
  toggle.checked = value !== null && value !== undefined;
  const body = h('div', { class: 'sf-nullable-body' });
  /** @type {Editor | null} */
  let innerEditor = null;
  const slot = errorSlot(ctx);
  const render = () => {
    clear(body);
    innerEditor = null;
    if (!toggle.checked) return;
    const { default: _d, title: _t, ...bare } = inner;
    const start = value !== null && value !== undefined ? value : defaultFor(bare, ctx.root);
    // The inner editor shares this path: collect its slots separately so it does not replace
    // the toggle's own error slot, then expose the nested ones.
    /** @type {Map<string, HTMLElement>} */
    const childSlots = new Map();
    innerEditor = kindEditor(analyze(bare, ctx.root).kind, bare, start, { ...ctx, label: '', required: true, errorSlots: childSlots }, false);
    for (const [p, el] of childSlots) if (p !== ctx.path) ctx.errorSlots.set(p, el);
    body.appendChild(innerEditor.el);
  };
  toggle.addEventListener('change', () => {
    value = undefined;
    render();
    ctx.notify();
  });
  render();
  const desc = descriptionOf(inner) || descriptionOf(original);
  return {
    el: h(
      'div',
      { class: 'sf-nullable field-wrap' },
      h('label', { class: 'field field-inline' }, toggle, h('span', { class: 'field-label' }, text)),
      desc ? h('div', { class: 'field-hint' }, desc) : null,
      body,
      slot,
    ),
    get: () => (toggle.checked && innerEditor ? innerEditor.get() : null),
    problems: () => (toggle.checked && innerEditor && innerEditor.problems ? innerEditor.problems() : []),
  };
}

/** @param {Schema} s @param {any} value @param {BuildCtx} ctx @returns {Editor} */
function objectEditor(s, value, ctx) {
  const obj = value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  const required = new Set(s.required || []);
  /** @type {[string, Editor][]} */
  const editors = [];
  const props = /** @type {Record<string, Schema>} */ (s.properties || {});
  for (const [key, prop] of Object.entries(props)) {
    const childPath = joinPath(ctx.path, key);
    const childValue = key in obj ? obj[key] : required.has(key) ? defaultFor(prop, ctx.root) : defaultIfPresent(prop, ctx.root);
    editors.push([key, buildEditor(prop, childValue, { ...ctx, path: childPath, label: key, required: required.has(key) })]);
  }
  // Keep unknown keys (e.g. from a newer template version) when extra keys are allowed.
  const extras = s.additionalProperties === false ? {} : Object.fromEntries(Object.entries(obj).filter(([k]) => !(k in props)));
  const slot = errorSlot(ctx);
  const title = s.title && ctx.path ? s.title : ctx.label ? humanize(ctx.label) : '';
  const desc = descriptionOf(s);
  const content = [desc && ctx.path ? h('div', { class: 'field-hint' }, desc) : null, slot, editors.map(([, e]) => e.el)];
  const el = ctx.path
    ? h('fieldset', { class: 'fieldset sf-object' }, h('legend', {}, title || 'Details'), ...content)
    : h('div', { class: 'sf-object sf-root' }, ...content);
  return {
    el,
    get: () => {
      /** @type {Record<string, any>} */
      const out = clone(extras);
      for (const [key, e] of editors) {
        const v = e.get();
        if (v !== undefined) out[key] = v;
      }
      return out;
    },
    problems: () => editors.flatMap(([, e]) => (e.problems ? e.problems() : [])),
  };
}

/**
 * Optional properties start empty unless the schema has a default.
 * @param {Schema} prop
 * @param {Schema} root
 */
function defaultIfPresent(prop, root) {
  const info = analyze(prop, root);
  if ('default' in info.schema) return clone(info.schema.default);
  return info.nullable ? null : undefined;
}

/** @param {Schema} s @param {any} value @param {BuildCtx} ctx @returns {Editor} */
function arrayEditor(s, value, ctx) {
  /** @type {any[]} */
  let items = Array.isArray(value) ? value.slice() : [];
  const itemSchema = s.items && typeof s.items === 'object' ? s.items : {};
  const list = h('ol', { class: 'sf-array-list' });
  /** @type {Editor[]} */
  let editors = [];
  const slot = errorSlot(ctx);
  const title = s.title || (ctx.label ? humanize(ctx.label) : 'Items');
  const addBtn = button(`Add ${singular(title)}`, { kind: 'outline', small: true, icon: 'plus', disabled: ctx.disabled });
  const count = h('span', { class: 'sf-count muted' });

  const snapshot = () => editors.map((e) => e.get());
  const render = () => {
    clear(list);
    editors = items.map((item, i) => {
      const editor = buildEditor(itemSchema, item, { ...ctx, path: joinPath(ctx.path, i), label: `${singular(title)} ${i + 1}`, required: true });
      const up = button(`Move ${singular(title)} ${i + 1} up`, { kind: 'ghost', iconOnly: true, icon: 'up', small: true, disabled: ctx.disabled || i === 0 });
      const down = button(`Move ${singular(title)} ${i + 1} down`, { kind: 'ghost', iconOnly: true, icon: 'down', small: true, disabled: ctx.disabled || i === items.length - 1 });
      const remove = button(`Remove ${singular(title)} ${i + 1}`, {
        kind: 'ghost',
        iconOnly: true,
        icon: 'trash',
        small: true,
        disabled: ctx.disabled || (typeof s.minItems === 'number' && items.length <= s.minItems),
      });
      up.addEventListener('click', () => move(i, i - 1));
      down.addEventListener('click', () => move(i, i + 1));
      remove.addEventListener('click', () => {
        items = snapshot();
        items.splice(i, 1);
        render();
        ctx.notify();
        focusIn(list, Math.min(i, items.length - 1), 'input, select, textarea, button');
      });
      list.appendChild(h('li', { class: 'sf-array-item' }, h('div', { class: 'sf-array-body' }, editor.el), h('div', { class: 'sf-array-tools' }, up, down, remove)));
      return editor;
    });
    addBtn.disabled = ctx.disabled || (typeof s.maxItems === 'number' && items.length >= s.maxItems);
    const limits = [typeof s.minItems === 'number' ? `min ${s.minItems}` : '', typeof s.maxItems === 'number' ? `max ${s.maxItems}` : ''].filter(Boolean).join(', ');
    count.textContent = `${items.length} item${items.length === 1 ? '' : 's'}${limits ? ` (${limits})` : ''}`;
  };
  /** @param {number} from @param {number} to */
  const move = (from, to) => {
    items = snapshot();
    if (to < 0 || to >= items.length) return;
    const [x] = items.splice(from, 1);
    items.splice(to, 0, x);
    render();
    ctx.notify();
    focusIn(list, to, 'input, select, textarea');
  };
  addBtn.addEventListener('click', () => {
    items = snapshot();
    items.push(defaultFor(itemSchema, ctx.root));
    render();
    ctx.notify();
    focusIn(list, items.length - 1, 'input, select, textarea');
  });
  render();
  const desc = descriptionOf(s);
  return {
    el: h(
      'fieldset',
      { class: 'fieldset sf-array' },
      h('legend', {}, title, ctx.required && typeof s.minItems === 'number' && s.minItems > 0 ? h('span', { class: 'req', 'aria-hidden': 'true' }, ' *') : null),
      desc ? h('div', { class: 'field-hint' }, desc) : null,
      slot,
      list,
      h('div', { class: 'row gap sf-array-footer' }, addBtn, count),
    ),
    get: () => snapshot(),
    problems: () => editors.flatMap((e) => (e.problems ? e.problems() : [])),
  };
}

/** @param {Schema} s @param {any} value @param {BuildCtx} ctx @returns {Editor} */
function tupleEditor(s, value, ctx) {
  const arr = Array.isArray(value) ? value : [];
  const prefix = /** @type {Schema[]} */ (s.prefixItems);
  const slot = errorSlot(ctx);
  const editors = prefix.map((p, i) =>
    buildEditor(p, i < arr.length ? arr[i] : defaultFor(p, ctx.root), { ...ctx, path: joinPath(ctx.path, i), label: p.title || `#${i + 1}`, required: true }),
  );
  const title = s.title || (ctx.label ? humanize(ctx.label) : 'Values');
  return {
    el: h('fieldset', { class: 'fieldset sf-tuple' }, h('legend', {}, title), descriptionOf(s) ? h('div', { class: 'field-hint' }, descriptionOf(s)) : null, slot, h('div', { class: 'sf-tuple-row' }, editors.map((e) => e.el))),
    get: () => editors.map((e) => e.get()),
    problems: () => editors.flatMap((e) => (e.problems ? e.problems() : [])),
  };
}

/** @param {Schema} s @param {any} value @param {BuildCtx} ctx @returns {Editor} */
function jsonEditor(s, value, ctx) {
  const control = h('textarea', { class: 'input textarea mono', rows: '4', spellcheck: 'false', disabled: ctx.disabled });
  control.value = value === undefined ? '' : JSON.stringify(value, null, 2);
  control.addEventListener('input', ctx.notify);
  const parse = () => {
    const raw = control.value.trim();
    if (raw === '') return undefined;
    try {
      return JSON.parse(raw);
    } catch (e) {
      return new InvalidJson(raw, /** @type {Error} */ (e).message);
    }
  };
  const path = ctx.path;
  return {
    el: labelled({ ...ctx }, { ...s, description: [descriptionOf(s), 'Edit as JSON.'].filter(Boolean).join(' ') }, control),
    get: parse,
    problems: () => {
      const v = parse();
      return v instanceof InvalidJson ? [{ path, message: `Invalid JSON: ${v.message}` }] : [];
    },
  };
}

/** @param {string} word */
function singular(word) {
  const w = String(word || 'item').trim();
  if (/ies$/i.test(w)) return `${w.slice(0, -3)}y`;
  if (/ses$/i.test(w)) return w.slice(0, -2);
  if (/s$/i.test(w) && !/ss$/i.test(w)) return w.slice(0, -1);
  return w;
}

/**
 * Focus the first matching control inside the n-th list item.
 * @param {HTMLElement} list
 * @param {number} index
 * @param {string} selector
 */
function focusIn(list, index, selector) {
  if (index < 0) return;
  const li = list.children[index];
  const target = li && /** @type {HTMLElement | null} */ (li.querySelector(selector));
  if (target) target.focus();
}
