// @ts-check
/**
 * GenerationOptions (aadhi/pipeline/base.py) <-> generation form values.
 *
 * The form collects raw control values (strings from inputs/selects, booleans from checkboxes);
 * `buildGenerationOptions` coerces and validates them into the exact payload the API expects,
 * honouring server feature flags from GET /api/meta (features the server cannot provide are
 * forced off instead of being silently ignored). The AI engine (`llm_provider`) must be one
 * the server lists in `llm.engines` and has configured; null means the server default engine.
 * The image provider (`image_provider`) is only sent when the server lists choosable providers
 * (`image.providers`); null means the server's IMAGE_PROVIDER and its backups.
 * `prefer_library_visuals` (use a well-matching picture of the teacher's media library instead of
 * generating one) is only sent to servers that have the media library (`library` in GET /api/meta).
 */

/** Hard limits mirrored from GenerationOptions (server re-validates). */
export const OPTION_LIMITS = Object.freeze({
  target_minutes: { min: 3, max: 90 },
  quiz_every_n_concepts: { min: 1, max: 6 },
  max_ai_videos: { min: 0, max: 8 },
  audience: { max: 200 },
  subject_name: { max: 240 },
  unit_name: { max: 240 },
  session_number: { max: 60 },
  session_title: { max: 300 },
  previous_session_summary: { max: 4000 },
  extra_instructions: { max: 4000 },
});

export const DEPTHS = /** @type {const} */ (['overview', 'standard', 'deep']);
export const TTS_PROVIDERS = /** @type {const} */ (['edge', 'gemini', 'openai', 'elevenlabs', 'fake']);
/** GenerationOptions.image_provider values (a lecture's choice for its generated images). */
export const IMAGE_PROVIDERS = /** @type {const} */ (['gemini', 'pollinations']);
/** GenerationOptions.llm_provider values ("fake" only exists on test/demo servers). */
export const LLM_PROVIDERS = /** @type {const} */ (['gemini', 'openai', 'anthropic', 'fake']);
/** Fallback engine names (mirrors aadhi/providers/factory.py LLM_ENGINE_LABELS) when meta has none. */
export const LLM_ENGINE_LABELS = Object.freeze({ gemini: 'Google Gemini', openai: 'OpenAI', anthropic: 'Anthropic Claude', fake: 'Offline test engine' });
const VOICE_RE = /^[A-Za-z0-9_-]{1,64}$/;
const RATE_RE = /^[+-]\d{1,2}%$/;
const OPENAI_MODEL_RE = /^(gpt|o\d|chatgpt|ft:gpt)/i;

/**
 * Fallback defaults when the server sends no generation_defaults (mirrors GenerationOptions; the
 * conditional `image_provider` and `prefer_library_visuals` are left out: they are only sent when the
 * server lists image providers / has the media library).
 */
export const FALLBACK_DEFAULTS = Object.freeze({
  language: 'en-IN',
  board_language: null,
  audience: 'first-year engineering undergraduates',
  target_minutes: 15,
  depth: 'standard',
  subject_name: null,
  unit_name: null,
  session_number: null,
  session_title: null,
  previous_session_summary: '',
  extra_instructions: '',
  include_quizzes: true,
  quiz_every_n_concepts: 2,
  allow_manim: true,
  allow_freeform_manim: true,
  allow_generated_images: true,
  allow_ai_video: false,
  max_ai_videos: 2,
  allow_interactive: false,
  allow_gifs: false,
  review_plan: false,
  tts_provider: null,
  tts_voice: null,
  tts_rate: null,
  llm_provider: null,
  llm_model_plan: null,
  llm_model_script: null,
});

/**
 * @typedef {Record<string, string | boolean | number | null | undefined>} OptionValues
 */

/**
 * Initial form values from meta.generation_defaults (missing keys fall back to the schema
 * defaults). Nullable strings become '' for form controls.
 * @param {any} meta
 * @returns {OptionValues}
 */
export function optionsFormDefaults(meta) {
  const defaults = { ...FALLBACK_DEFAULTS, ...((meta && meta.generation_defaults) || {}) };
  /** @type {OptionValues} */
  const values = {};
  for (const [k, v] of Object.entries(defaults)) {
    if (typeof v === 'boolean') values[k] = v;
    else values[k] = v === null || v === undefined ? '' : String(v);
  }
  return values;
}

/**
 * @param {any} meta
 * @returns {string[]}
 */
function languageCodes(meta) {
  const langs = meta && Array.isArray(meta.languages) ? meta.languages : [];
  return langs.map((/** @type {any} */ l) => l.code);
}

/**
 * @param {any} meta
 * @param {string} key
 */
function feature(meta, key) {
  const f = meta && meta.features;
  return f ? !!f[key] : true;
}

/**
 * @param {unknown} v
 */
function str(v) {
  return v === undefined || v === null ? '' : String(v).trim();
}

/**
 * @param {unknown} v
 * @returns {boolean}
 */
function bool(v) {
  return v === true || v === 'true' || v === 'on' || v === '1';
}

/**
 * Coerce + validate form values into a GenerationOptions payload.
 * @param {OptionValues} values
 * @param {any} meta   GET /api/meta response (languages, tts, features, llm)
 * @param {{ isAdmin?: boolean }} [opts]
 * @returns {{ options: Record<string, any>, errors: Record<string, string> }}
 */
export function buildGenerationOptions(values, meta, opts = {}) {
  /** @type {Record<string, string>} */
  const errors = {};
  /** @type {Record<string, any>} */
  const o = {};
  const codes = languageCodes(meta);

  const language = str(values.language) || FALLBACK_DEFAULTS.language;
  if (codes.length && !codes.includes(language)) errors.language = 'Choose a supported language.';
  o.language = language;

  const board = str(values.board_language);
  if (board && codes.length && !codes.includes(board)) errors.board_language = 'Choose a supported board language.';
  o.board_language = board && board !== language ? board : null;

  const audience = str(values.audience);
  o.audience = audience || FALLBACK_DEFAULTS.audience;
  if (o.audience.length > OPTION_LIMITS.audience.max) errors.audience = `At most ${OPTION_LIMITS.audience.max} characters.`;

  o.target_minutes = intField(values.target_minutes, 'target_minutes', FALLBACK_DEFAULTS.target_minutes, errors);
  const depth = str(values.depth) || FALLBACK_DEFAULTS.depth;
  if (!(/** @type {readonly string[]} */ (DEPTHS)).includes(depth)) errors.depth = 'Choose overview, standard or deep.';
  o.depth = depth;

  for (const key of /** @type {const} */ (['subject_name', 'unit_name', 'session_number', 'session_title'])) {
    const v = str(values[key]);
    if (v.length > OPTION_LIMITS[key].max) errors[key] = `At most ${OPTION_LIMITS[key].max} characters.`;
    o[key] = v || null;
  }
  for (const key of /** @type {const} */ (['previous_session_summary', 'extra_instructions'])) {
    const v = str(values[key]);
    if (v.length > OPTION_LIMITS[key].max) errors[key] = `At most ${OPTION_LIMITS[key].max} characters.`;
    o[key] = v;
  }

  o.include_quizzes = bool(values.include_quizzes);
  o.quiz_every_n_concepts = intField(values.quiz_every_n_concepts, 'quiz_every_n_concepts', FALLBACK_DEFAULTS.quiz_every_n_concepts, errors);

  o.allow_manim = bool(values.allow_manim) && feature(meta, 'manim');
  o.allow_freeform_manim = o.allow_manim && bool(values.allow_freeform_manim) && feature(meta, 'manim_freeform');
  o.allow_generated_images = bool(values.allow_generated_images) && feature(meta, 'generated_images');
  o.allow_ai_video = bool(values.allow_ai_video) && !!(meta && meta.features && meta.features.ai_video);
  o.max_ai_videos = o.allow_ai_video
    ? intField(values.max_ai_videos, 'max_ai_videos', FALLBACK_DEFAULTS.max_ai_videos, errors)
    : clampInt(values.max_ai_videos, 'max_ai_videos', FALLBACK_DEFAULTS.max_ai_videos);
  o.allow_interactive = bool(values.allow_interactive);
  o.allow_gifs = bool(values.allow_gifs) && feature(meta, 'gifs');
  o.review_plan = bool(values.review_plan);
  // Library pictures instead of generated ones: only on servers with the media library (always sent
  // there, so unticking it on a lecture that had it switches it off).
  if (hasLibrary(meta)) o.prefer_library_visuals = bool(values.prefer_library_visuals);

  // Image provider: only on servers that list choosable providers ('' / images off = server default).
  const imageProviders = choosableImageProviders(meta);
  if (imageProviders.length) {
    const chosen = o.allow_generated_images ? str(values.image_provider) : '';
    if (chosen) {
      const known = imageProviders.find((p) => p.id === chosen);
      if (!(/** @type {readonly string[]} */ (IMAGE_PROVIDERS)).includes(chosen) || !known) errors.image_provider = 'Unknown image provider.';
      else if (known.configured === false) errors.image_provider = 'This image provider is not configured on the server.';
    }
    o.image_provider = chosen || null;
  }

  const provider = str(values.tts_provider);
  const providers = meta && meta.tts && Array.isArray(meta.tts.providers) ? meta.tts.providers : [];
  if (provider) {
    const known = providers.length ? providers.find((/** @type {any} */ p) => p.id === provider) : { configured: true };
    if (!(/** @type {readonly string[]} */ (TTS_PROVIDERS)).includes(provider) || !known) errors.tts_provider = 'Unknown voice provider.';
    else if (known.configured === false) errors.tts_provider = 'This voice provider is not configured on the server.';
  }
  o.tts_provider = provider || null;
  const voice = str(values.tts_voice);
  if (voice && !VOICE_RE.test(voice)) errors.tts_voice = 'Invalid voice id.';
  o.tts_voice = voice || null;
  const rate = str(values.tts_rate);
  if (rate && !RATE_RE.test(rate)) errors.tts_rate = 'Use a percentage like +10% or -5%.';
  o.tts_rate = rate || null;

  // AI engine: '' = server default; servers that publish no engine list only accept the default.
  // Servers with an engine list also refuse the default when it has no API key (422).
  const engine = str(values.llm_provider);
  if (engine) {
    const known = (llmEngines(meta) || []).find((e) => e.id === engine);
    if (!(/** @type {readonly string[]} */ (LLM_PROVIDERS)).includes(engine) || !known) errors.llm_provider = 'Unknown AI engine.';
    else if (known.configured === false) errors.llm_provider = 'This AI engine is not configured on the server.';
  } else if (llmEngines(meta) && !defaultEngineConfigured(meta)) {
    errors.llm_provider = 'The server default AI engine is not configured. Choose another engine.';
  }
  o.llm_provider = engine || null;

  // Model overrides are admin-only, limited to the server allow-list and to the effective
  // engine's model family (the API strips others).
  const allow = meta && meta.llm && Array.isArray(meta.llm.override_allowlist) ? meta.llm.override_allowlist : [];
  const effective = effectiveEngine(meta, errors.llm_provider ? '' : engine);
  for (const key of /** @type {const} */ (['llm_model_plan', 'llm_model_script'])) {
    const v = str(values[key]);
    if (v && opts.isAdmin && allow.includes(v) && modelFitsEngine(v, effective)) o[key] = v;
    else {
      if (v && opts.isAdmin) errors[key] = allow.includes(v) ? 'This model belongs to a different AI engine.' : 'Model is not in the server allow-list.';
      o[key] = null;
    }
  }
  return { options: o, errors };
}

/**
 * Whether the server has the media library (GET /api/meta `library`).
 * @param {any} meta
 * @returns {boolean}
 */
export function hasLibrary(meta) {
  return !!(meta && meta.library && typeof meta.library === 'object');
}

/**
 * Image providers a lecture may choose (GET /api/meta `image.providers`; empty when the server
 * lists none or generated images are off there).
 * @param {any} meta
 * @returns {{ id: string, label: string, configured?: boolean, paid?: boolean }[]}
 */
export function choosableImageProviders(meta) {
  const list = meta && meta.image && Array.isArray(meta.image.providers) ? meta.image.providers : [];
  return list.filter((/** @type {any} */ p) => p && typeof p.id === 'string');
}

/** Characters Python's str.strip() removes (str.isspace); String.prototype.trim differs (U+001C–U+001F, U+0085, U+FEFF). */
const PY_SPACE = '\\t\\n\\v\\f\\r\\x1c-\\x20\\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000';
const PY_STRIP_RE = new RegExp(`^[${PY_SPACE}]+|[${PY_SPACE}]+$`, 'g');

/**
 * `str.strip()` exactly as Python does it (the server's rules, not String.prototype.trim).
 * @param {string} s
 */
export function pyStrip(s) {
  return String(s).replace(PY_STRIP_RE, '');
}

/**
 * Engine family of a model name (mirrors aadhi/providers/factory.py engine_for_model, which
 * strips surrounding whitespace with str.strip() and matches case-sensitively, except the
 * case-insensitive OpenAI pattern).
 * @param {string} model
 * @returns {'gemini' | 'openai' | 'anthropic' | null}
 */
export function engineForModel(model) {
  const m = pyStrip(model || '');
  if (m.startsWith('gemini')) return 'gemini';
  if (OPENAI_MODEL_RE.test(m)) return 'openai';
  if (m.startsWith('claude')) return 'anthropic';
  return null;
}

/**
 * Whether an override model may be used with an engine ('' = unknown engine: no filtering;
 * the offline test engine accepts any allow-listed model).
 * @param {string} model
 * @param {string} engine
 */
function modelFitsEngine(model, engine) {
  return !engine || engine === 'fake' || engineForModel(model) === engine;
}

/**
 * The AI engines the server offers (GET /api/meta `llm.engines`), or null for servers that
 * predate per-lecture engines.
 * @param {any} meta
 * @returns {import('../types.js').LlmEngineInfo[] | null}
 */
export function llmEngines(meta) {
  const engines = meta && meta.llm && meta.llm.engines;
  return Array.isArray(engines) ? engines : null;
}

/**
 * Engine a lecture uses: the chosen one, else the server default.
 * @param {any} meta
 * @param {string | null | undefined} engineId   '' / null = server default
 * @returns {string}   '' when the server does not say
 */
export function effectiveEngine(meta, engineId) {
  return str(engineId) || str(meta && meta.llm && meta.llm.provider);
}

/**
 * Whether the server default engine can run (`llm.configured`; unknown counts as configured).
 * @param {any} meta
 */
export function defaultEngineConfigured(meta) {
  return !(meta && meta.llm && meta.llm.configured === false);
}

/**
 * Engine the picker starts on: the initial choice, else the server default, or the first
 * configured engine when the default has no API key ('' = server default).
 * @param {any} meta
 * @param {string | null | undefined} initial
 * @returns {string}
 */
export function initialEngine(meta, initial) {
  const chosen = str(initial);
  if (chosen || defaultEngineConfigured(meta)) return chosen;
  const usable = (llmEngines(meta) || []).find((e) => e.configured !== false);
  return usable ? usable.id : '';
}

/**
 * Option text of an engine in the picker: "(not configured)" without an API key, "(your key)"
 * when it runs on the user's own key.
 * @param {import('../types.js').LlmEngineInfo} engine
 * @returns {string}
 */
export function engineOptionLabel(engine) {
  if (engine.configured === false) return `${engine.label} (not configured)`;
  if (engine.key_source === 'personal') return `${engine.label} (your key)`;
  return engine.label;
}

/**
 * Whether users may save their own API keys here (GET /api/meta `api_keys.personal_enabled`).
 * @param {any} meta
 * @returns {boolean}
 */
export function personalKeysEnabled(meta) {
  return !!(meta && meta.api_keys && meta.api_keys.personal_enabled);
}

/**
 * Display name of an engine (server label, else the built-in name, else the id).
 * @param {any} meta
 * @param {string} engineId
 */
export function engineLabel(meta, engineId) {
  const known = (llmEngines(meta) || []).find((e) => e.id === engineId);
  if (known && known.label) return known.label;
  return /** @type {Record<string, string>} */ (LLM_ENGINE_LABELS)[engineId] || engineId;
}

/**
 * Models an engine uses per tier. The server default engine falls back to `llm.models` on
 * servers without an engine list.
 * @param {any} meta
 * @param {string} engineId   '' = server default engine
 * @returns {Partial<import('../types.js').LlmModels>}
 */
export function engineModels(meta, engineId) {
  const llm = (meta && meta.llm) || {};
  const id = effectiveEngine(meta, engineId);
  const known = (llmEngines(meta) || []).find((e) => e.id === id);
  if (known && known.models) return known.models;
  return id && id === str(llm.provider) && llm.models ? llm.models : {};
}

/**
 * Admin override models usable with an engine: the allow-list filtered to that engine's family.
 * @param {any} meta
 * @param {string} engineId   '' = server default engine
 * @returns {string[]}
 */
export function overrideModelsFor(meta, engineId) {
  const allow = meta && meta.llm && Array.isArray(meta.llm.override_allowlist) ? meta.llm.override_allowlist : [];
  const engine = effectiveEngine(meta, engineId);
  return allow.filter((/** @type {string} */ m) => modelFitsEngine(m, engine));
}

/**
 * @param {unknown} raw
 * @param {'target_minutes' | 'quiz_every_n_concepts' | 'max_ai_videos'} key
 * @param {number} fallback
 * @param {Record<string, string>} errors
 */
function intField(raw, key, fallback, errors) {
  const lim = OPTION_LIMITS[key];
  const s = str(raw);
  if (s === '') return fallback;
  const n = Number(s);
  if (!Number.isInteger(n)) {
    errors[key] = 'Enter a whole number.';
    return fallback;
  }
  if (n < lim.min || n > lim.max) {
    errors[key] = `Must be between ${lim.min} and ${lim.max}.`;
    return Math.min(lim.max, Math.max(lim.min, n));
  }
  return n;
}

/**
 * Integer within limits without reporting errors (hidden/disabled fields).
 * @param {unknown} raw
 * @param {'target_minutes' | 'quiz_every_n_concepts' | 'max_ai_videos'} key
 * @param {number} fallback
 */
function clampInt(raw, key, fallback) {
  const lim = OPTION_LIMITS[key];
  const n = Number(str(raw));
  if (str(raw) === '' || !Number.isInteger(n)) return fallback;
  return Math.min(lim.max, Math.max(lim.min, n));
}

/**
 * Voices of a TTS provider for a language (matching on the primary subtag as fallback,
 * e.g. "en-IN" voices first, then other "en-*" voices).
 * @param {any} meta
 * @param {string} providerId   '' = server default provider
 * @param {string} language
 * @returns {{ id: string, label: string, language: string, gender?: string }[]}
 */
export function voicesFor(meta, providerId, language) {
  const tts = meta && meta.tts;
  if (!tts || !Array.isArray(tts.providers)) return [];
  const pid = providerId || tts.default_provider;
  const provider = tts.providers.find((/** @type {any} */ p) => p.id === pid);
  if (!provider || !Array.isArray(provider.voices)) return [];
  const exact = provider.voices.filter((/** @type {any} */ v) => v.language === language);
  const primary = String(language || '').split('-')[0];
  const related = provider.voices.filter(
    (/** @type {any} */ v) => v.language !== language && String(v.language || '').split('-')[0] === primary,
  );
  return [...exact, ...related];
}

/** Allowed source documents for POST /api/projects. */
export const SOURCE_EXTENSIONS = /** @type {const} */ (['.pdf', '.docx', '.txt', '.md']);
const SOURCE_MIME = new Set([
  'application/pdf',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'text/plain',
  'text/markdown',
  'text/x-markdown',
  '',
  'application/octet-stream',
]);

/**
 * Client-side check of an uploaded source document (the server re-checks magic bytes).
 * @param {{ name: string, size: number, type?: string }} file
 * @param {number} maxMb
 * @returns {string | null} error message or null when acceptable
 */
export function validateSourceFile(file, maxMb) {
  if (!file) return 'Choose a file.';
  const name = String(file.name || '').toLowerCase();
  const ext = name.includes('.') ? name.slice(name.lastIndexOf('.')) : '';
  if (!(/** @type {readonly string[]} */ (SOURCE_EXTENSIONS)).includes(ext)) {
    return 'Unsupported file type. Upload a PDF, Word (.docx), text (.txt) or Markdown (.md) file.';
  }
  if (file.type && !SOURCE_MIME.has(file.type)) return `The file looks like ${file.type}, not a ${ext} document.`;
  if (!file.size) return 'The file is empty.';
  const max = (Number(maxMb) || 50) * 1024 * 1024;
  if (file.size > max) return `The file is larger than the ${maxMb} MB upload limit.`;
  return null;
}

/** Media accepted by POST /api/uploads per purpose. */
export const MEDIA_RULES = Object.freeze({
  scene_media: { exts: ['.png', '.jpg', '.jpeg', '.webp', '.gif', '.mp4', '.webm'], label: 'image or video' },
  figure: { exts: ['.png', '.jpg', '.jpeg', '.webp', '.gif'], label: 'image' },
  side_panel: { exts: ['.png', '.jpg', '.jpeg', '.webp', '.gif', '.mp4', '.webm'], label: 'image or video' },
  poster: { exts: ['.png', '.jpg', '.jpeg', '.webp'], label: 'image' },
});

/**
 * @param {{ name: string, size: number, type?: string }} file
 * @param {keyof typeof MEDIA_RULES} purpose
 * @param {number} maxMb
 * @returns {string | null}
 */
export function validateMediaFile(file, purpose, maxMb) {
  const rule = MEDIA_RULES[purpose];
  if (!rule) return 'Unknown upload purpose.';
  if (!file) return 'Choose a file.';
  const name = String(file.name || '').toLowerCase();
  const ext = name.includes('.') ? name.slice(name.lastIndexOf('.')) : '';
  if (!rule.exts.includes(ext)) return `Upload an ${rule.label} (${rule.exts.join(', ')}).`;
  if (file.type && !/^(image|video)\//.test(file.type)) return `The file looks like ${file.type}, not an ${rule.label}.`;
  if (!file.size) return 'The file is empty.';
  if (file.size > (Number(maxMb) || 50) * 1024 * 1024) return `The file is larger than the ${maxMb} MB upload limit.`;
  return null;
}
