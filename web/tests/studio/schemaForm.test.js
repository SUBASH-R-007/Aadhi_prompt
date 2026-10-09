import { resetDom } from './_dom.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import { analyze, deref, defaultFor, validateValue, humanize, joinPath } from '../../js/studio/lib/jsonSchema.js';
import { createSchemaForm } from '../../js/studio/components/schemaForm.js';

// A Pydantic-style schema exercising every supported construct.
const SCHEMA = {
  title: 'Params',
  type: 'object',
  $defs: {
    Point: {
      title: 'Point',
      type: 'object',
      properties: { x: { type: 'number', title: 'X' }, y: { type: 'number', title: 'Y', default: 0 } },
      required: ['x'],
      additionalProperties: false,
    },
    Mode: { enum: ['fast', 'slow'], type: 'string', title: 'Mode' },
  },
  properties: {
    title: { type: 'string', title: 'Title', minLength: 1, maxLength: 20 },
    count: { type: 'integer', minimum: 1, maximum: 5 },
    ratio: { type: 'number', exclusiveMinimum: 0, default: 0.5 },
    show_grid: { type: 'boolean', default: true },
    mode: { $ref: '#/$defs/Mode', default: 'fast' },
    color: { type: 'string', pattern: '^#[0-9a-fA-F]{6}$', default: '#ffd700' },
    note: { anyOf: [{ type: 'string', maxLength: 10 }, { type: 'null' }], default: null, title: 'Note' },
    steps: { type: 'array', items: { type: 'string', minLength: 1 }, minItems: 1, maxItems: 3, title: 'Steps' },
    points: { type: 'array', items: { $ref: '#/$defs/Point' }, default: [] },
    origin: { $ref: '#/$defs/Point' },
    range: { type: 'array', prefixItems: [{ type: 'number' }, { type: 'number' }], items: false, minItems: 2, maxItems: 2 },
    kind: { const: 'plot' },
    extra: { type: 'object', additionalProperties: { type: 'number' } },
  },
  required: ['title', 'count', 'steps', 'origin', 'kind'],
};

test('deref resolves $ref and merges sibling keywords', () => {
  const s = deref({ $ref: '#/$defs/Mode', default: 'slow' }, SCHEMA);
  assert.deepEqual(s.enum, ['fast', 'slow']);
  assert.equal(s.default, 'slow');
  assert.throws(() => deref({ $ref: '#/$defs/Missing' }, SCHEMA), /unresolvable/);
  assert.throws(() => deref({ $ref: 'http://x' }, SCHEMA), /unsupported/);
  const cyc = { $defs: { A: { $ref: '#/$defs/A' } } };
  assert.throws(() => deref({ $ref: '#/$defs/A' }, cyc), /too deep/);
});

test('analyze classifies kinds and nullability', () => {
  const k = (s) => analyze(s, SCHEMA);
  assert.equal(k({ type: 'string' }).kind, 'string');
  assert.equal(k({ type: 'integer' }).kind, 'integer');
  assert.equal(k(SCHEMA.properties.mode).kind, 'enum');
  assert.deepEqual(k(SCHEMA.properties.note), { kind: 'string', nullable: true, schema: { type: 'string', maxLength: 10, default: null, title: 'Note' } });
  assert.equal(k({ type: ['integer', 'null'] }).nullable, true);
  assert.equal(k({ type: ['integer', 'null'] }).kind, 'integer');
  assert.equal(k(SCHEMA.properties.range).kind, 'tuple');
  assert.equal(k(SCHEMA.properties.kind).kind, 'const');
  assert.equal(k(SCHEMA.properties.extra).kind, 'json');
  assert.equal(k({ anyOf: [{ type: 'string' }, { type: 'integer' }] }).kind, 'json');
  assert.equal(k({ properties: { a: {} } }).kind, 'object');
  assert.equal(k({ enum: ['a', null] }).nullable, true);
});

test('defaultFor builds a valid starting value', () => {
  const v = defaultFor(SCHEMA, SCHEMA);
  assert.deepEqual(v, {
    title: '',
    count: 1,
    ratio: 0.5,
    show_grid: true,
    mode: 'fast',
    color: '#ffd700',
    note: null,
    steps: [''],
    points: [],
    origin: { x: 0, y: 0 },
    kind: 'plot',
  });
  assert.equal(defaultFor({ type: 'integer', exclusiveMinimum: 2 }, {}), 3);
  assert.equal(defaultFor({ type: 'number', maximum: -3 }, {}), -3);
});

test('validateValue: types, enums, required, ranges, patterns and arrays', () => {
  const good = { title: 'Ohm', count: 2, steps: ['a'], origin: { x: 1 }, kind: 'plot', range: [0, 1], extra: { a: 1 } };
  assert.deepEqual(validateValue(SCHEMA, good), []);
  const bad = {
    title: '',
    count: 9,
    ratio: 0,
    mode: 'medium',
    color: 'gold',
    note: 'way too long text',
    steps: [],
    points: [{ y: 1 }, { x: 1, z: 2 }],
    origin: { x: 'one' },
    range: [1],
    kind: 'other',
    extra: { a: 'x' },
  };
  const errs = validateValue(SCHEMA, bad);
  const byPath = Object.fromEntries(errs.map((e) => [e.path, e.message]));
  assert.match(byPath.title, /empty/);
  assert.match(byPath.count, /at most 5/);
  assert.match(byPath.ratio, /greater than 0/);
  assert.match(byPath.mode, /one of/);
  assert.match(byPath.color, /format/);
  assert.match(byPath.note, /At most 10/);
  assert.match(byPath.steps, /at least 1/);
  assert.match(byPath['points[0].x'], /required/);
  assert.match(byPath['points[1].z'], /Unknown field/);
  assert.match(byPath['origin.x'], /number/);
  assert.match(byPath['range[1]'], /required/);
  assert.match(byPath.kind, /Must be "plot"/);
  assert.match(byPath['extra.a'], /number/);
  assert.equal(validateValue(SCHEMA, { ...good, count: 2.5 }).length, 1);
  assert.equal(validateValue({ type: 'array', uniqueItems: true }, [1, 1])[0].message, 'Items must be unique.');
  assert.equal(validateValue({ type: 'number', multipleOf: 0.5 }, 0.75)[0].message, 'Must be a multiple of 0.5.');
  assert.deepEqual(validateValue({ type: 'number', multipleOf: 0.1 }, 0.3), []);
  assert.equal(validateValue({ type: 'string' }, null)[0].message, 'A value is required.');
  assert.deepEqual(validateValue(SCHEMA.properties.note, null, SCHEMA), []);
  // missing required keys
  const missing = validateValue(SCHEMA, {}).map((e) => e.path).sort();
  assert.deepEqual(missing, ['count', 'kind', 'origin', 'steps', 'title']);
});

test('helpers: humanize and joinPath', () => {
  assert.equal(humanize('max_value'), 'Max value');
  assert.equal(humanize('xLabel'), 'X Label');
  assert.equal(joinPath('', 'a'), 'a');
  assert.equal(joinPath('a', 0), 'a[0]');
  assert.equal(joinPath('a[0]', 'b'), 'a[0].b');
});

test('form renders controls per type and round-trips the value', () => {
  resetDom();
  const value = { title: 'Ohm', count: 3, ratio: 0.25, show_grid: false, mode: 'slow', color: '#00ff88', note: 'hi', steps: ['a', 'b'], points: [{ x: 1, y: 2 }], origin: { x: 5, y: 6 }, range: [0, 10], kind: 'plot', extra: { k: 2 } };
  const form = createSchemaForm(SCHEMA, value);
  document.body.appendChild(form.el);
  assert.deepEqual(form.getValue(), value);
  const el = form.el;
  assert.ok(el.querySelector('input[type="number"][min="1"][max="5"]'), 'integer input with bounds');
  assert.ok(el.querySelector('input[type="checkbox"]'), 'boolean checkbox');
  const selects = [...el.querySelectorAll('select')];
  assert.ok(selects.some((s) => [...s.options].map((o) => o.textContent).join(',') === 'fast,slow'), 'enum select');
  assert.ok(el.querySelector('textarea.mono'), 'json editor for open dicts');
  assert.equal(el.querySelectorAll('.sf-array-item').length, 3, 'two steps + one point');
  assert.ok([...el.querySelectorAll('label .field-label')].some((l) => l.textContent.startsWith('Title')));
  assert.ok(el.querySelector('[aria-required="true"]'));
  assert.equal(form.validate().length, 0);
});

test('editing inputs updates the value and fires onChange', () => {
  resetDom();
  const changes = [];
  const form = createSchemaForm(SCHEMA, undefined, { onChange: (v) => changes.push(v) });
  document.body.appendChild(form.el);
  const titleInput = form.el.querySelector('input[type="text"]');
  titleInput.value = 'Hello';
  titleInput.dispatchEvent(new Event('input', { bubbles: true }));
  assert.equal(changes.at(-1).title, 'Hello');
  // optional number cleared -> omitted
  const ratio = [...form.el.querySelectorAll('input[type="number"]')].find((i) => i.value === '0.5');
  ratio.value = '';
  ratio.dispatchEvent(new Event('input', { bubbles: true }));
  assert.equal('ratio' in form.getValue(), false);
  // nullable toggle on -> inner editor appears with a string
  const noteToggle = [...form.el.querySelectorAll('.sf-nullable > label input[type="checkbox"]')][0];
  noteToggle.checked = true;
  noteToggle.dispatchEvent(new Event('change', { bubbles: true }));
  assert.equal(form.getValue().note, '');
  noteToggle.checked = false;
  noteToggle.dispatchEvent(new Event('change', { bubbles: true }));
  assert.equal(form.getValue().note, null);
});

test('array editor adds, removes and reorders within min/max items', () => {
  resetDom();
  const form = createSchemaForm(SCHEMA, { title: 'x', count: 1, steps: ['one'], origin: { x: 0 }, kind: 'plot' });
  document.body.appendChild(form.el);
  const stepsFs = [...form.el.querySelectorAll('fieldset.sf-array')].find((f) => f.querySelector('legend').textContent.startsWith('Steps'));
  const addBtn = () => [...stepsFs.querySelectorAll('.sf-array-footer button')][0];
  const removeBtns = () => [...stepsFs.querySelectorAll('.sf-array-tools button[aria-label^="Remove"]')];
  assert.equal(removeBtns()[0].disabled, true, 'cannot go below minItems');
  addBtn().click();
  addBtn().click();
  assert.equal(form.getValue().steps.length, 3);
  assert.equal(addBtn().disabled, true, 'maxItems reached');
  const inputs = [...stepsFs.querySelectorAll('.sf-array-item input')];
  inputs[1].value = 'two';
  inputs[2].value = 'three';
  [...stepsFs.querySelectorAll('.sf-array-tools button[aria-label^="Move"][aria-label$="down"]')][0].click();
  assert.deepEqual(form.getValue().steps, ['two', 'one', 'three']);
  removeBtns()[2].click();
  assert.deepEqual(form.getValue().steps, ['two', 'one']);
});

test('validate() shows inline errors at the right fields and clears them', () => {
  resetDom();
  const form = createSchemaForm(SCHEMA, { title: '', count: 1, steps: [''], origin: { x: 0 }, kind: 'plot' });
  document.body.appendChild(form.el);
  const errs = form.validate();
  const paths = errs.map((e) => e.path).sort();
  assert.deepEqual(paths, ['steps[0]', 'title']);
  const visible = [...form.el.querySelectorAll('.field-error')].filter((e) => !e.hidden).map((e) => e.textContent);
  assert.equal(visible.length, 2);
  assert.ok(form.el.querySelector('[aria-invalid="true"]'));
  const title = form.el.querySelector('input[type="text"]');
  title.value = 'Fixed';
  const step = form.el.querySelector('.sf-array-item input');
  step.value = 'ok';
  assert.deepEqual(form.validate(), []);
  assert.equal([...form.el.querySelectorAll('.field-error')].filter((e) => !e.hidden).length, 0);
});

test('invalid JSON in raw editors is reported', () => {
  resetDom();
  const form = createSchemaForm(SCHEMA, { title: 'x', count: 1, steps: ['a'], origin: { x: 0 }, kind: 'plot', extra: {} });
  document.body.appendChild(form.el);
  const raw = form.el.querySelector('textarea.mono');
  raw.value = '{oops';
  const errs = form.validate();
  assert.equal(errs.length, 1);
  assert.equal(errs[0].path, 'extra');
  assert.match(errs[0].message, /Invalid JSON/);
});

test('setValue rebuilds the form; destroy empties it', () => {
  resetDom();
  const form = createSchemaForm({ type: 'object', properties: { a: { type: 'string' } } }, { a: 'x' });
  form.setValue({ a: 'y' });
  assert.deepEqual(form.getValue(), { a: 'y' });
  form.destroy();
  assert.equal(form.el.childNodes.length, 0);
});

test('unknown keys are preserved when additional properties are allowed', () => {
  const form = createSchemaForm({ type: 'object', properties: { a: { type: 'string' } } }, { a: 'x', legacy: 1 });
  assert.deepEqual(form.getValue(), { a: 'x', legacy: 1 });
  const strict = createSchemaForm({ type: 'object', properties: { a: { type: 'string' } }, additionalProperties: false }, { a: 'x', legacy: 1 });
  assert.deepEqual(strict.getValue(), { a: 'x' });
});
