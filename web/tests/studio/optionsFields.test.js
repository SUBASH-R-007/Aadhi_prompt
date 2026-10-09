import { resetDom } from './_dom.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import { createOptionsForm } from '../../js/studio/views/optionsFields.js';
import { sampleMeta } from './fixtures.js';

/** sampleMeta() on a production-like server: Gemini by default, no offline engine, a mixed allow-list. */
function engineMeta() {
  const meta = sampleMeta();
  meta.llm.provider = 'gemini';
  meta.llm.models = { ...meta.llm.engines[1].models };
  meta.llm.engines = meta.llm.engines.filter((e) => e.id !== 'fake');
  meta.llm.override_allowlist = ['gemini-pro', 'gemini-2.5-flash', 'gpt-4.1', 'claude-opus-5-5'];
  return meta;
}

/** Mount a form into the document (focus and aria lookups need it attached). */
function mountForm(meta, opts) {
  resetDom();
  const form = createOptionsForm(meta, opts);
  document.body.appendChild(form.el);
  return form;
}

/** @param {HTMLElement} root */
function legends(root) {
  return [...root.querySelectorAll('fieldset > legend')].map((l) => l.textContent);
}

/** The control of the field labelled `label`. */
function control(root, label) {
  const lab = [...root.querySelectorAll('label.field')].find((l) => l.querySelector('.field-label').textContent === label);
  return lab ? lab.querySelector('select, input, textarea') : null;
}

/** @param {HTMLSelectElement} sel */
function optionList(sel) {
  return [...sel.options].map((o) => ({ value: o.value, label: o.textContent, disabled: o.disabled }));
}

/** @param {HTMLSelectElement} sel */
function choose(sel, value) {
  sel.value = value;
  sel.dispatchEvent(new window.Event('change', { bubbles: true }));
}

/** The live "Plans with … · writes scenes with …" line of the AI engine section. */
function modelHint(root) {
  const fs = [...root.querySelectorAll('fieldset')].find((f) => f.querySelector('legend').textContent === 'AI engine');
  return fs.querySelector('p.field-hint[aria-live]');
}

test('the AI engine section follows the lecture details (or comes first without them)', () => {
  let form = mountForm(sampleMeta(), { includeMeta: true });
  assert.deepEqual(legends(form.el).slice(0, 3), ['Lecture details', 'AI engine', 'Language & audience']);
  form = mountForm(sampleMeta(), {});
  assert.equal(legends(form.el)[0], 'AI engine');
});

test('the engine dropdown lists the server engines; unconfigured ones are disabled', () => {
  const form = mountForm(sampleMeta(), { includeMeta: true });
  const engine = control(form.el, 'Engine');
  assert.ok(engine instanceof HTMLSelectElement);
  assert.deepEqual(optionList(engine), [
    { value: '', label: 'Server default (Offline test engine)', disabled: false },
    { value: 'fake', label: 'Offline test engine', disabled: false },
    { value: 'gemini', label: 'Google Gemini', disabled: false },
    { value: 'openai', label: 'OpenAI (not configured)', disabled: true },
    { value: 'anthropic', label: 'Anthropic Claude', disabled: false },
  ]);
  assert.equal(engine.value, '');
  assert.match(engine.closest('.field-wrap').textContent, /Which AI writes this lecture\. Each engine needs its API key on the server\./);
});

test('without the offline engine in the list there is no offline option', () => {
  const form = mountForm(engineMeta(), {});
  const engine = control(form.el, 'Engine');
  assert.equal(engine.options[0].textContent, 'Server default (Google Gemini)');
  assert.deepEqual([...engine.options].map((o) => o.value), ['', 'gemini', 'openai', 'anthropic']);
});

test('an unconfigured server default is labelled and the picker starts on a configured engine', () => {
  const meta = engineMeta();
  meta.llm.configured = false;
  meta.llm.engines = meta.llm.engines.map((e) => (e.id === 'gemini' ? { ...e, configured: false } : e));
  const form = mountForm(meta, {});
  const engine = control(form.el, 'Engine');
  assert.equal(engine.options[0].textContent, 'Server default (Google Gemini, not configured)');
  assert.equal(engine.value, 'anthropic');
  assert.equal(modelHint(form.el).textContent, 'Plans with claude-opus-5-5 · writes scenes with claude-opus-5-5');
  assert.deepEqual(form.build().errors, {});
  choose(engine, '');
  assert.equal(form.build().errors.llm_provider, 'The server default AI engine is not configured. Choose another engine.');
});

test('the model hint follows the selected (or default) engine', () => {
  const form = mountForm(engineMeta(), {});
  const engine = control(form.el, 'Engine');
  const hint = modelHint(form.el);
  assert.equal(hint.textContent, 'Plans with gemini-2.5-pro · writes scenes with gemini-2.5-flash');
  assert.ok(engine.getAttribute('aria-describedby').split(' ').includes(hint.getAttribute('id')), 'announced with the select');
  choose(engine, 'anthropic');
  assert.equal(hint.textContent, 'Plans with claude-opus-5-5 · writes scenes with claude-opus-5-5');
  choose(engine, '');
  assert.equal(hint.textContent, 'Plans with gemini-2.5-pro · writes scenes with gemini-2.5-flash');
});

test('values() and build() carry llm_provider', () => {
  const form = mountForm(engineMeta(), { includeMeta: true });
  const engine = control(form.el, 'Engine');
  assert.equal(form.values().llm_provider, '');
  assert.equal(form.build().options.llm_provider, null);
  choose(engine, 'anthropic');
  assert.equal(form.values().llm_provider, 'anthropic');
  const { options, errors } = form.build();
  assert.deepEqual(errors, {});
  assert.equal(options.llm_provider, 'anthropic');
});

test('an unconfigured engine (e.g. a stale initial value) is reported on the field', () => {
  const form = mountForm(engineMeta(), { includeMeta: true, initial: { llm_provider: 'openai' } });
  const engine = control(form.el, 'Engine');
  assert.equal(engine.value, 'openai');
  const { errors } = form.build();
  assert.equal(errors.llm_provider, 'This AI engine is not configured on the server.');
  assert.equal(engine.getAttribute('aria-invalid'), 'true');
  assert.match(engine.closest('.field-wrap').querySelector('.field-error').textContent, /not configured/);
  assert.equal(document.activeElement, engine, 'the first invalid control gets focus');
  choose(engine, 'gemini');
  assert.deepEqual(form.build().errors, {});
  assert.equal(engine.hasAttribute('aria-invalid'), false);
});

test('servers without an engine list show no engine picker and send the default', () => {
  const meta = sampleMeta();
  delete meta.llm.engines;
  const form = mountForm(meta, { includeMeta: true });
  assert.equal(legends(form.el).includes('AI engine'), false);
  assert.equal(control(form.el, 'Engine'), null);
  const { options, errors } = form.build();
  assert.deepEqual(errors, {});
  assert.equal(options.llm_provider, null);
});

test('setDisabled(false) re-enables the picker but not the unconfigured engines', () => {
  const form = mountForm(engineMeta(), {});
  const engine = control(form.el, 'Engine');
  form.setDisabled(true);
  assert.equal(engine.disabled, true);
  form.setDisabled(false);
  assert.equal(engine.disabled, false);
  assert.equal([...engine.options].find((o) => o.value === 'openai').disabled, true);
});

test('admin model overrides only offer the selected engine and drop a choice that no longer fits', () => {
  const form = mountForm(engineMeta(), { isAdmin: true });
  const engine = control(form.el, 'Engine');
  const plan = control(form.el, 'Planning model');
  const script = control(form.el, 'Script model');
  assert.deepEqual([...plan.options].map((o) => o.value), ['', 'gemini-pro', 'gemini-2.5-flash'], 'server default engine is Gemini');
  choose(plan, 'gemini-pro');
  assert.equal(modelHint(form.el).textContent, 'Plans with gemini-pro · writes scenes with gemini-2.5-flash', 'the hint shows the override');
  choose(engine, 'anthropic');
  assert.deepEqual([...plan.options].map((o) => o.value), ['', 'claude-opus-5-5']);
  assert.deepEqual([...script.options].map((o) => o.value), ['', 'claude-opus-5-5']);
  assert.equal(plan.value, '', 'the Gemini override was cleared');
  choose(script, 'claude-opus-5-5');
  const { options, errors } = form.build();
  assert.deepEqual(errors, {});
  assert.equal(options.llm_provider, 'anthropic');
  assert.equal(options.llm_model_plan, null);
  assert.equal(options.llm_model_script, 'claude-opus-5-5');
  choose(engine, 'anthropic');
  assert.equal(script.value, 'claude-opus-5-5', 'a fitting choice is kept');
});

test('admin overrides start from fitting initial values only', () => {
  let form = mountForm(engineMeta(), { isAdmin: true, initial: { llm_provider: 'anthropic', llm_model_plan: 'claude-opus-5-5', llm_model_script: 'gpt-4.1' } });
  assert.equal(control(form.el, 'Planning model').value, 'claude-opus-5-5');
  assert.equal(control(form.el, 'Script model').value, '');
  form = mountForm(sampleMeta(), { isAdmin: true });
  assert.deepEqual([...control(form.el, 'Planning model').options].map((o) => o.value), ['', 'gemini-pro'], 'the offline engine takes any allow-listed model');
  form = mountForm(sampleMeta(), { isAdmin: false });
  assert.equal(control(form.el, 'Planning model'), null, 'no overrides for non-admins');
});

test('image provider picker: listed providers, unconfigured disabled, follows the illustrations switch', () => {
  const meta = sampleMeta();
  assert.equal(control(mountForm(meta).el, 'Image provider'), null, 'no picker without a server list');
  meta.image = {
    default_provider: 'pollinations',
    providers: [
      { id: 'gemini', label: 'Google Gemini', configured: false, paid: true },
      { id: 'pollinations', label: 'Pollinations', configured: true, paid: false },
    ],
  };
  const form = mountForm(meta, { initial: { image_provider: 'pollinations' } });
  const sel = control(form.el, 'Image provider');
  assert.ok(sel);
  assert.deepEqual(optionList(sel), [
    { value: '', label: 'Server default (Pollinations)', disabled: false },
    { value: 'gemini', label: 'Google Gemini (paid) (not configured)', disabled: true },
    { value: 'pollinations', label: 'Pollinations', disabled: false },
  ]);
  assert.equal(sel.value, 'pollinations');
  assert.equal(form.build().options.image_provider, 'pollinations');
  const images = [...form.el.querySelectorAll('input[type="checkbox"]')].find((i) => /Generated illustrations/.test(i.parentElement.parentElement.textContent));
  images.checked = false;
  images.dispatchEvent(new window.Event('change'));
  assert.equal(sel.disabled, true);
  assert.equal(form.build().options.image_provider, null);
  meta.features.generated_images = false;
  assert.equal(control(mountForm(meta).el, 'Image provider'), null, 'no picker when the server has illustrations off');
});

test('"Prefer pictures from my library" appears only on servers with the media library', () => {
  const box = (form) => [...form.el.querySelectorAll('input[type="checkbox"]')].find((i) => /Prefer pictures from my library/.test(i.parentElement.parentElement.textContent));
  assert.equal(box(mountForm(sampleMeta())), undefined, 'no media library: no option');
  assert.equal('prefer_library_visuals' in mountForm(sampleMeta()).build().options, false);

  const meta = { ...sampleMeta(), library: { auto_save_generated: true, ai_describe_enabled: false } };
  meta.generation_defaults = { ...meta.generation_defaults, prefer_library_visuals: false };
  let form = mountForm(meta);
  const cb = box(form);
  assert.ok(cb);
  assert.equal(cb.checked, false);
  assert.equal(form.build().options.prefer_library_visuals, false);
  cb.checked = true;
  assert.equal(form.build().options.prefer_library_visuals, true);
  // a lecture that had it (Regenerate): starts ticked, and unticking sends false
  form = mountForm(meta, { initial: { prefer_library_visuals: true } });
  assert.equal(box(form).checked, true);
  box(form).checked = false;
  assert.equal(form.build().options.prefer_library_visuals, false);
});
