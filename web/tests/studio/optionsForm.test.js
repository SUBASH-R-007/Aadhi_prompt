import './_dom.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  buildGenerationOptions,
  optionsFormDefaults,
  voicesFor,
  validateSourceFile,
  validateMediaFile,
  engineForModel,
  engineLabel,
  engineModels,
  overrideModelsFor,
  initialEngine,
  hasLibrary,
  FALLBACK_DEFAULTS,
} from '../../js/studio/lib/optionsForm.js';
import { sampleMeta } from './fixtures.js';

const GENERATION_OPTION_KEYS = Object.keys(FALLBACK_DEFAULTS).sort();

test('defaults round-trip into the exact server defaults', () => {
  const meta = sampleMeta();
  const values = optionsFormDefaults(meta);
  assert.equal(values.board_language, '', 'nullable -> empty control');
  assert.equal(values.include_quizzes, true);
  const { options, errors } = buildGenerationOptions(values, meta);
  assert.deepEqual(errors, {});
  assert.deepEqual(options, meta.generation_defaults);
  assert.deepEqual(Object.keys(options).sort(), GENERATION_OPTION_KEYS);
});

test('defaults fall back to the schema defaults without meta', () => {
  const { options, errors } = buildGenerationOptions(optionsFormDefaults(null), null);
  assert.deepEqual(errors, {});
  assert.deepEqual(options, { ...FALLBACK_DEFAULTS });
});

test('coerces strings, numbers and nullable fields', () => {
  const meta = sampleMeta();
  const values = {
    ...optionsFormDefaults(meta),
    language: 'ta-IN',
    board_language: 'en-IN',
    audience: '  second-year ECE  ',
    target_minutes: '25',
    depth: 'deep',
    subject_name: ' Circuits ',
    unit_name: '',
    session_number: 'Session 3',
    session_title: '   ',
    previous_session_summary: 'We covered KVL.',
    extra_instructions: 'Use Indian examples',
    include_quizzes: 'on',
    quiz_every_n_concepts: '3',
    tts_provider: 'edge',
    tts_voice: 'ta-IN-PallaviNeural',
    tts_rate: '+10%',
  };
  const { options, errors } = buildGenerationOptions(values, meta);
  assert.deepEqual(errors, {});
  assert.equal(options.language, 'ta-IN');
  assert.equal(options.board_language, 'en-IN');
  assert.equal(options.audience, 'second-year ECE');
  assert.equal(options.target_minutes, 25);
  assert.equal(options.depth, 'deep');
  assert.equal(options.subject_name, 'Circuits');
  assert.equal(options.unit_name, null);
  assert.equal(options.session_title, null);
  assert.equal(options.quiz_every_n_concepts, 3);
  assert.equal(options.include_quizzes, true);
  assert.equal(options.tts_provider, 'edge');
  assert.equal(options.tts_voice, 'ta-IN-PallaviNeural');
  assert.equal(options.tts_rate, '+10%');
});

test('board language equal to narration language becomes null', () => {
  const meta = sampleMeta();
  const { options } = buildGenerationOptions({ ...optionsFormDefaults(meta), language: 'hi-IN', board_language: 'hi-IN' }, meta);
  assert.equal(options.board_language, null);
});

test('range and format errors are reported per field', () => {
  const meta = sampleMeta();
  const { errors, options } = buildGenerationOptions(
    {
      ...optionsFormDefaults(meta),
      language: 'fr-FR',
      target_minutes: '200',
      quiz_every_n_concepts: '1.5',
      depth: 'extreme',
      tts_voice: 'bad voice!',
      tts_rate: '10%',
      extra_instructions: 'x'.repeat(4001),
    },
    meta,
  );
  assert.deepEqual(Object.keys(errors).sort(), ['depth', 'extra_instructions', 'language', 'quiz_every_n_concepts', 'target_minutes', 'tts_rate', 'tts_voice']);
  assert.equal(options.target_minutes, 90, 'clamped');
});

test('features the server lacks are forced off', () => {
  const meta = sampleMeta();
  meta.features = { ...meta.features, manim: false, gifs: false, generated_images: false, ai_video: false };
  const { options } = buildGenerationOptions(
    { ...optionsFormDefaults(meta), allow_manim: true, allow_freeform_manim: true, allow_gifs: true, allow_generated_images: true, allow_ai_video: true, max_ai_videos: '5' },
    meta,
  );
  assert.equal(options.allow_manim, false);
  assert.equal(options.allow_freeform_manim, false, 'free-form requires manim');
  assert.equal(options.allow_gifs, false);
  assert.equal(options.allow_generated_images, false);
  assert.equal(options.allow_ai_video, false);
  assert.equal(options.max_ai_videos, 5, 'kept (clamped) but unused');
});

test('ai video max is validated when enabled', () => {
  const meta = sampleMeta();
  meta.features.ai_video = true;
  let r = buildGenerationOptions({ ...optionsFormDefaults(meta), allow_ai_video: true, max_ai_videos: '4' }, meta);
  assert.equal(r.options.allow_ai_video, true);
  assert.equal(r.options.max_ai_videos, 4);
  r = buildGenerationOptions({ ...optionsFormDefaults(meta), allow_ai_video: true, max_ai_videos: '9' }, meta);
  assert.ok(r.errors.max_ai_videos);
});

test('free-form manim stays off when manim is off', () => {
  const meta = sampleMeta();
  const { options } = buildGenerationOptions({ ...optionsFormDefaults(meta), allow_manim: false, allow_freeform_manim: true }, meta);
  assert.equal(options.allow_freeform_manim, false);
});

test('tts provider must exist and be configured', () => {
  const meta = sampleMeta();
  let r = buildGenerationOptions({ ...optionsFormDefaults(meta), tts_provider: 'elevenlabs' }, meta);
  assert.match(r.errors.tts_provider, /not configured/);
  r = buildGenerationOptions({ ...optionsFormDefaults(meta), tts_provider: 'acme' }, meta);
  assert.match(r.errors.tts_provider, /Unknown/);
});

test('model overrides only for admins and only from the allow-list', () => {
  const meta = sampleMeta();
  const values = { ...optionsFormDefaults(meta), llm_model_plan: 'gemini-pro', llm_model_script: 'evil-model' };
  let r = buildGenerationOptions(values, meta, { isAdmin: false });
  assert.equal(r.options.llm_model_plan, null);
  assert.equal(r.options.llm_model_script, null);
  assert.deepEqual(r.errors, {});
  r = buildGenerationOptions(values, meta, { isAdmin: true });
  assert.equal(r.options.llm_model_plan, 'gemini-pro');
  assert.equal(r.options.llm_model_script, null);
  assert.ok(r.errors.llm_model_script);
});

/** sampleMeta() on a production-like server: Gemini by default, no offline engine, a mixed allow-list. */
function engineMeta() {
  const meta = sampleMeta();
  meta.llm.provider = 'gemini';
  meta.llm.models = { ...meta.llm.engines[1].models };
  meta.llm.engines = meta.llm.engines.filter((e) => e.id !== 'fake');
  meta.llm.override_allowlist = ['gemini-pro', 'gemini-2.5-flash', 'gpt-4.1', 'o3-mini', 'claude-opus-5-5', 'mystery-model'];
  return meta;
}

test('the AI engine defaults to the server default (null)', () => {
  const meta = sampleMeta();
  assert.equal(optionsFormDefaults(meta).llm_provider, '');
  const { options, errors } = buildGenerationOptions(optionsFormDefaults(meta), meta);
  assert.deepEqual(errors, {});
  assert.equal(options.llm_provider, null);
});

test('a listed, configured AI engine is sent as llm_provider', () => {
  const meta = engineMeta();
  for (const id of ['gemini', 'anthropic']) {
    const { options, errors } = buildGenerationOptions({ ...optionsFormDefaults(meta), llm_provider: id }, meta);
    assert.deepEqual(errors, {});
    assert.equal(options.llm_provider, id);
  }
});

test('the AI engine must be listed and configured', () => {
  const meta = engineMeta();
  let r = buildGenerationOptions({ ...optionsFormDefaults(meta), llm_provider: 'openai' }, meta);
  assert.equal(r.errors.llm_provider, 'This AI engine is not configured on the server.');
  r = buildGenerationOptions({ ...optionsFormDefaults(meta), llm_provider: 'mistral' }, meta);
  assert.equal(r.errors.llm_provider, 'Unknown AI engine.');
  r = buildGenerationOptions({ ...optionsFormDefaults(meta), llm_provider: 'fake' }, meta);
  assert.equal(r.errors.llm_provider, 'Unknown AI engine.', 'the offline engine only exists where the server lists it');
  r = buildGenerationOptions({ ...optionsFormDefaults(sampleMeta()), llm_provider: 'fake' }, sampleMeta());
  assert.deepEqual(r.errors, {});
  assert.equal(r.options.llm_provider, 'fake');
});

test('the server default is refused when its engine has no API key (as the API does)', () => {
  const meta = engineMeta();
  meta.llm.configured = false;
  let r = buildGenerationOptions(optionsFormDefaults(meta), meta);
  assert.equal(r.errors.llm_provider, 'The server default AI engine is not configured. Choose another engine.');
  assert.equal(r.options.llm_provider, null);
  r = buildGenerationOptions({ ...optionsFormDefaults(meta), llm_provider: 'anthropic' }, meta);
  assert.deepEqual(r.errors, {});
  delete meta.llm.engines;
  r = buildGenerationOptions(optionsFormDefaults(meta), meta);
  assert.deepEqual(r.errors, {}, 'servers without an engine list do not check the default');
});

test('initialEngine keeps a choice, else the default, else the first configured engine', () => {
  const meta = engineMeta();
  assert.equal(initialEngine(meta, ''), '');
  assert.equal(initialEngine(meta, 'anthropic'), 'anthropic');
  meta.llm.configured = false;
  assert.equal(initialEngine(meta, null), 'gemini');
  meta.llm.engines = meta.llm.engines.filter((e) => e.id !== 'gemini');
  assert.equal(initialEngine(meta, ''), 'anthropic', 'unconfigured engines are skipped');
  meta.llm.engines = meta.llm.engines.map((e) => ({ ...e, configured: false }));
  assert.equal(initialEngine(meta, ''), '', 'nothing usable: stay on the server default');
});

test('servers without an engine list only accept the default engine', () => {
  const meta = sampleMeta();
  delete meta.llm.engines;
  let r = buildGenerationOptions(optionsFormDefaults(meta), meta);
  assert.deepEqual(r.errors, {});
  assert.equal(r.options.llm_provider, null);
  r = buildGenerationOptions({ ...optionsFormDefaults(meta), llm_provider: 'gemini' }, meta);
  assert.equal(r.errors.llm_provider, 'Unknown AI engine.');
  r = buildGenerationOptions({ ...optionsFormDefaults(null), llm_provider: 'gemini' }, null);
  assert.equal(r.errors.llm_provider, 'Unknown AI engine.');
});

test('engineForModel mirrors the server model families', () => {
  assert.equal(engineForModel('gemini-2.5-pro'), 'gemini');
  for (const m of ['gpt-4.1', 'GPT-4.1-mini', 'o3-mini', 'o1', 'chatgpt-4o-latest', 'ft:gpt-4o:acme']) assert.equal(engineForModel(m), 'openai', m);
  assert.equal(engineForModel('claude-opus-5-5'), 'anthropic');
  for (const m of ['omni-1', 'llama-3', '', 'my-gemini']) assert.equal(engineForModel(m), null, m);
});

test('engineForModel strips whitespace exactly like the server (Python str.strip)', () => {
  // Expected values computed with aadhi.providers.factory.engine_for_model.
  const cases = [
    [' claude-opus-5-5\n', 'anthropic'],
    ['\tgpt-4.1 ', 'openai'],
    ['\x1cgemini-2.5-pro\x1f', 'gemini'], // separators: Python strips them, String.prototype.trim does not
    ['\x85o3-mini　', 'openai'], // NEL: Python whitespace, not JS
    ['\xa0claude-sonnet-5-5 ', 'anthropic'],
    ['﻿claude-opus-5-5', null], // BOM: JS trim removes it, Python keeps it
    ['claude-opus-5-5﻿', 'anthropic'],
    ['​gemini-2.5-pro', null], // zero-width space is not whitespace anywhere
    ['Claude-opus-5-5', null], // case-sensitive prefixes...
    [' GPT-5', 'openai'], // ...except the OpenAI pattern
    ['   ', null],
  ];
  for (const [model, engine] of cases) assert.equal(engineForModel(/** @type {string} */ (model)), engine, JSON.stringify(model));
});

test('admin overrides are limited to the effective engine family', () => {
  const meta = engineMeta();
  assert.deepEqual(overrideModelsFor(meta, ''), ['gemini-pro', 'gemini-2.5-flash'], 'server default engine');
  assert.deepEqual(overrideModelsFor(meta, 'openai'), ['gpt-4.1', 'o3-mini']);
  assert.deepEqual(overrideModelsFor(meta, 'anthropic'), ['claude-opus-5-5']);
  assert.deepEqual(overrideModelsFor({ ...meta, llm: { ...meta.llm, provider: 'fake' } }, ''), meta.llm.override_allowlist, 'offline engine takes any');

  const values = { ...optionsFormDefaults(meta), llm_provider: 'anthropic', llm_model_plan: 'claude-opus-5-5', llm_model_script: 'gemini-pro' };
  let r = buildGenerationOptions(values, meta, { isAdmin: true });
  assert.equal(r.options.llm_model_plan, 'claude-opus-5-5');
  assert.equal(r.options.llm_model_script, null);
  assert.equal(r.errors.llm_model_script, 'This model belongs to a different AI engine.');
  r = buildGenerationOptions({ ...values, llm_provider: '' }, meta, { isAdmin: true });
  assert.equal(r.options.llm_model_script, 'gemini-pro', 'server default engine is Gemini');
  assert.ok(r.errors.llm_model_plan);
  r = buildGenerationOptions(values, meta, { isAdmin: false });
  assert.deepEqual(r.errors, {}, 'non-admin overrides are dropped silently');
});

test('engine labels and models come from the server list, with fallbacks', () => {
  const meta = engineMeta();
  assert.equal(engineLabel(meta, 'anthropic'), 'Anthropic Claude');
  assert.equal(engineLabel(meta, 'fake'), 'Offline test engine', 'built-in name when unlisted');
  assert.equal(engineLabel(meta, 'acme'), 'acme');
  assert.equal(engineModels(meta, '').plan, 'gemini-2.5-pro');
  assert.equal(engineModels(meta, 'openai').script, 'gpt-4.1-mini');
  const old = sampleMeta();
  delete old.llm.engines;
  assert.deepEqual(engineModels(old, ''), old.llm.models, 'older servers: the default engine models');
  assert.deepEqual(engineModels(old, 'gemini'), {});
  assert.deepEqual(engineModels(null, ''), {});
});

test('voicesFor filters by provider and language (exact first, then same primary language)', () => {
  const meta = sampleMeta();
  assert.deepEqual(voicesFor(meta, '', 'en-IN').map((v) => v.id), ['en-IN-NeerjaNeural', 'en-US-AriaNeural']);
  assert.deepEqual(voicesFor(meta, 'edge', 'ta-IN').map((v) => v.id), ['ta-IN-PallaviNeural']);
  assert.deepEqual(voicesFor(meta, 'elevenlabs', 'en-IN'), []);
  assert.deepEqual(voicesFor(null, 'edge', 'en-IN'), []);
});

test('validateSourceFile checks type and size', () => {
  assert.equal(validateSourceFile({ name: 'notes.PDF', size: 1000, type: 'application/pdf' }, 50), null);
  assert.equal(validateSourceFile({ name: 'a.docx', size: 10, type: '' }, 50), null);
  assert.equal(validateSourceFile({ name: 'a.md', size: 10, type: 'text/markdown' }, 50), null);
  assert.match(validateSourceFile({ name: 'a.exe', size: 10 }, 50), /Unsupported/);
  assert.match(validateSourceFile({ name: 'a.pdf', size: 0 }, 50), /empty/);
  assert.match(validateSourceFile({ name: 'a.pdf', size: 51 * 1024 * 1024 }, 50), /larger/);
  assert.match(validateSourceFile({ name: 'a.pdf', size: 10, type: 'image/png' }, 50), /looks like/);
  assert.match(validateSourceFile(null, 50), /Choose/);
});

test('validateMediaFile follows the upload purposes', () => {
  assert.equal(validateMediaFile({ name: 'a.mp4', size: 10, type: 'video/mp4' }, 'scene_media', 50), null);
  assert.match(validateMediaFile({ name: 'a.mp4', size: 10, type: 'video/mp4' }, 'figure', 50), /image/);
  assert.match(validateMediaFile({ name: 'a.png', size: 10, type: 'image/png' }, 'nope', 50), /purpose/);
  assert.match(validateMediaFile({ name: 'a.png', size: 10, type: 'text/html' }, 'poster', 50), /looks like/);
});

/** sampleMeta() on a server that lists choosable image providers (GET /api/meta `image`). */
function imageMeta() {
  const meta = sampleMeta();
  meta.image = {
    default_provider: 'pollinations',
    providers: [
      { id: 'gemini', label: 'Google Gemini', configured: false, paid: true },
      { id: 'pollinations', label: 'Pollinations', configured: true, paid: false },
    ],
  };
  meta.generation_defaults = { ...meta.generation_defaults, image_provider: null };
  return meta;
}

test('image provider: only sent when the server lists providers; null means the server default', () => {
  const plain = buildGenerationOptions({ ...optionsFormDefaults(sampleMeta()), image_provider: 'gemini' }, sampleMeta());
  assert.equal('image_provider' in plain.options, false, 'servers without a list never receive it');
  assert.deepEqual(plain.errors, {});

  const meta = imageMeta();
  const def = buildGenerationOptions(optionsFormDefaults(meta), meta);
  assert.deepEqual(def.errors, {});
  assert.equal(def.options.image_provider, null);
  assert.deepEqual(def.options, meta.generation_defaults, 'defaults still round-trip');

  const chosen = buildGenerationOptions({ ...optionsFormDefaults(meta), image_provider: 'pollinations' }, meta);
  assert.equal(chosen.options.image_provider, 'pollinations');
  const off = buildGenerationOptions({ ...optionsFormDefaults(meta), image_provider: 'pollinations', allow_generated_images: false }, meta);
  assert.equal(off.options.image_provider, null, 'no choice while illustrations are off');
});

test('image provider: must be listed and configured', () => {
  const meta = imageMeta();
  const base = optionsFormDefaults(meta);
  assert.equal(buildGenerationOptions({ ...base, image_provider: 'gemini' }, meta).errors.image_provider, 'This image provider is not configured on the server.');
  assert.equal(buildGenerationOptions({ ...base, image_provider: 'dalle' }, meta).errors.image_provider, 'Unknown image provider.');
});

test('prefer_library_visuals: only sent to servers with the media library, always a boolean there', () => {
  const plain = buildGenerationOptions({ ...optionsFormDefaults(sampleMeta()), prefer_library_visuals: true }, sampleMeta());
  assert.equal('prefer_library_visuals' in plain.options, false, 'servers without a library never receive it');
  assert.equal(hasLibrary(sampleMeta()), false);
  const meta = { ...sampleMeta(), library: { auto_save_generated: true, ai_describe_enabled: false } };
  assert.equal(hasLibrary(meta), true);
  assert.equal(buildGenerationOptions(optionsFormDefaults(meta), meta).options.prefer_library_visuals, false);
  assert.equal(buildGenerationOptions({ ...optionsFormDefaults(meta), prefer_library_visuals: true }, meta).options.prefer_library_visuals, true);
  assert.equal(buildGenerationOptions({ ...optionsFormDefaults(meta), prefer_library_visuals: 'on' }, meta).options.prefer_library_visuals, true);
  assert.deepEqual(buildGenerationOptions(optionsFormDefaults(meta), meta).errors, {});
});
