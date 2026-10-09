// @ts-check
/**
 * GenerationOptions form (built from GET /api/meta: languages, AI engines, tts providers/voices,
 * feature flags, generation_defaults). Used by "New lecture" and "Regenerate lecture".
 * Raw control values are converted by lib/optionsForm.js buildGenerationOptions.
 * Controls disabled because the server lacks a feature stay disabled when the whole form is
 * re-enabled (setDisabled(false) after a failed upload).
 * The AI engine picker only appears when the server lists its engines (`llm.engines`); the
 * admin model overrides follow the chosen engine (other engines' models are not offered).
 * Engines that run on the user's own API key are labelled "(your key)"; when personal keys
 * are allowed and an engine has no key, a link points to the API keys page.
 * Servers that list choosable image providers (`image.providers`) get an image provider picker
 * under "Generated illustrations" (disabled while illustrations are off). Servers with the media
 * library (`library`) offer "Prefer pictures from my library" (`prefer_library_visuals`).
 */

import { h, clear } from '../../shared/dom.js';
import { field, input, select, checkbox, textarea, fieldset, formGrid } from '../components/form.js';
import { uid } from '../util.js';
import {
  optionsFormDefaults,
  buildGenerationOptions,
  voicesFor,
  OPTION_LIMITS,
  llmEngines,
  engineLabel,
  engineModels,
  overrideModelsFor,
  defaultEngineConfigured,
  initialEngine,
  engineOptionLabel,
  personalKeysEnabled,
  choosableImageProviders,
  hasLibrary,
} from '../lib/optionsForm.js';

/**
 * @typedef {object} OptionsForm
 * @property {HTMLElement} el
 * @property {() => import('../lib/optionsForm.js').OptionValues} values
 * @property {() => { options: Record<string, any>, errors: Record<string, string> }} build  validate + show errors
 * @property {(disabled: boolean) => void} setDisabled
 */

const DEPTH_LABELS = { overview: 'Overview (fewer, lighter scenes)', standard: 'Standard', deep: 'Deep (more examples and checks)' };
const RATES = ['', '-20%', '-10%', '+10%', '+20%'];

/**
 * @param {any} meta
 * @param {{ isAdmin?: boolean, initial?: Record<string, any>, includeMeta?: boolean }} [opts]
 * @returns {OptionsForm}
 */
export function createOptionsForm(meta, opts = {}) {
  const defaults = optionsFormDefaults({ ...meta, generation_defaults: { ...(meta && meta.generation_defaults), ...(opts.initial || {}) } });
  const features = (meta && meta.features) || {};
  /** @type {Map<string, HTMLElement & { setError?: (m: string | null) => void }>} */
  const wraps = new Map();
  /** @type {Map<string, HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>} */
  const controls = new Map();

  /**
   * @param {string} key
   * @param {HTMLElement & { setError?: (m: string | null) => void }} wrap
   * @param {HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement} control
   */
  const reg = (key, wrap, control) => {
    wraps.set(key, wrap);
    controls.set(key, control);
    return wrap;
  };
  /** Controls disabled by server features (never re-enabled by setDisabled(false)). */
  /** @type {Set<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>} */
  const featureLocked = new Set();
  /**
   * @param {string} key
   * @param {string} label
   * @param {{ hint?: string, max?: number, placeholder?: string, rows?: number }} [o]
   */
  const textField = (key, label, o = {}) => {
    const c = o.rows ? textarea({ value: String(defaults[key] ?? ''), maxLength: o.max, rows: o.rows, placeholder: o.placeholder }) : input({ value: String(defaults[key] ?? ''), maxLength: o.max, placeholder: o.placeholder });
    return reg(key, field(label, c, { hint: o.hint }), c);
  };
  /**
   * @param {string} key
   * @param {string} label
   * @param {{ hint?: string, disabled?: boolean, note?: string }} [o]
   */
  const boolField = (key, label, o = {}) => {
    const cb = checkbox({ label, checked: !!defaults[key] && !o.disabled, disabled: o.disabled, hint: o.note || o.hint });
    if (o.disabled) featureLocked.add(cb.input);
    return reg(key, cb, cb.input);
  };

  const languages = (meta && meta.languages) || [{ code: 'en-IN', label: 'English (India)' }];
  const langOptions = languages.map((/** @type {any} */ l) => ({ value: l.code, label: l.label }));
  const language = select({ options: langOptions, value: String(defaults.language) });
  const boardLanguage = select({ options: [{ value: '', label: 'Same as narration' }, ...langOptions], value: String(defaults.board_language || '') });
  const minutes = input({ type: 'number', value: String(defaults.target_minutes), min: OPTION_LIMITS.target_minutes.min, max: OPTION_LIMITS.target_minutes.max, step: 1 });
  const depth = select({ options: Object.entries(DEPTH_LABELS).map(([value, label]) => ({ value, label })), value: String(defaults.depth) });
  const quizEvery = input({ type: 'number', value: String(defaults.quiz_every_n_concepts), min: 1, max: 6, step: 1 });
  const maxVideos = input({ type: 'number', value: String(defaults.max_ai_videos), min: 0, max: 8, step: 1 });

  const tts = (meta && meta.tts) || { providers: [] };
  const defaultProvider = (tts.providers || []).find((/** @type {any} */ p) => p.id === tts.default_provider);
  const provider = select({
    options: [
      { value: '', label: `Server default${defaultProvider ? ` (${defaultProvider.label})` : ''}` },
      ...(tts.providers || []).map((/** @type {any} */ p) => ({ value: p.id, label: p.configured ? p.label : `${p.label} (not configured)`, disabled: !p.configured })),
    ],
    value: String(defaults.tts_provider || ''),
  });
  const voice = select({ options: [{ value: '', label: 'Default voice for the language' }], value: '' });
  const rate = select({ options: RATES.map((r) => ({ value: r, label: r ? `${r} speed` : 'Normal speed' })), value: String(defaults.tts_rate || '') });

  const renderVoices = () => {
    const keep = voice.value || String(defaults.tts_voice || '');
    clear(voice);
    voice.appendChild(h('option', { value: '' }, 'Default voice for the language'));
    for (const v of voicesFor(meta, provider.value, language.value)) {
      voice.appendChild(h('option', { value: v.id }, `${v.label}${v.gender ? ` · ${v.gender}` : ''} (${v.language})`));
    }
    voice.value = [...voice.options].some((o) => o.value === keep) ? keep : '';
  };
  provider.addEventListener('change', renderVoices);
  language.addEventListener('change', renderVoices);
  renderVoices();

  // AI engine ('' = server default). Servers without an engine list get no picker. When the
  // default engine has no API key the picker starts on the first configured engine.
  const engines = llmEngines(meta);
  const defaultEngine = String((meta && meta.llm && meta.llm.provider) || '');
  const defaultInfo = (engines || []).find((e) => e.id === defaultEngine);
  const defaultNote = !defaultEngineConfigured(meta) ? ', not configured' : defaultInfo && defaultInfo.key_source === 'personal' ? ', your key' : '';
  const engine = engines
    ? select({
        options: [
          { value: '', label: `Server default${defaultEngine ? ` (${engineLabel(meta, defaultEngine)}${defaultNote})` : ''}` },
          ...engines.map((e) => ({ value: e.id, label: engineOptionLabel(e), disabled: e.configured === false })),
        ],
        value: initialEngine(meta, String(defaults.llm_provider || '')),
      })
    : null;
  // Personal keys allowed and an engine without a key: point to the API keys page.
  const ownKeyHint =
    engines && personalKeysEnabled(meta) && engines.some((e) => e.configured === false)
      ? h('p', { class: 'field-hint own-key-hint' }, 'Have your own key? ', h('a', { href: '#/keys' }, 'Add it on the API keys page.'))
      : null;
  const modelHint = h('p', { class: 'field-hint', 'aria-live': 'polite' });
  const allow = meta && meta.llm && Array.isArray(meta.llm.override_allowlist) ? meta.llm.override_allowlist : [];
  const adminModels = !!opts.isAdmin && allow.length > 0;
  const planModel = adminModels ? select({ options: [] }) : null;
  const scriptModel = adminModels ? select({ options: [] }) : null;

  /**
   * List the allow-listed models of the chosen engine; a choice that no longer fits is cleared.
   * @param {boolean} [initial]   take the starting choice from the form defaults
   */
  const renderOverrides = (initial = false) => {
    const models = overrideModelsFor(meta, engine ? engine.value : '');
    for (const [key, sel] of /** @type {const} */ ([['llm_model_plan', planModel], ['llm_model_script', scriptModel]])) {
      if (!sel) continue;
      const keep = initial ? String(defaults[key] || '') : sel.value;
      clear(sel);
      sel.appendChild(h('option', { value: '' }, 'Engine default'));
      for (const m of models) sel.appendChild(h('option', { value: m }, m));
      sel.value = models.includes(keep) ? keep : '';
    }
  };
  /** "Plans with … · writes scenes with …" for the chosen (or default) engine and overrides. */
  const renderModelHint = () => {
    const models = engineModels(meta, engine ? engine.value : '');
    const plan = (planModel && planModel.value) || models.plan;
    const script = (scriptModel && scriptModel.value) || models.script;
    const text = [plan ? `plans with ${plan}` : '', script ? `writes scenes with ${script}` : ''].filter(Boolean).join(' · ');
    modelHint.textContent = text ? text[0].toUpperCase() + text.slice(1) : '';
    modelHint.hidden = !text;
  };
  renderOverrides(true);
  renderModelHint();
  if (engine) {
    engine.addEventListener('change', () => {
      renderOverrides();
      renderModelHint();
    });
  }
  for (const sel of [planModel, scriptModel]) if (sel) sel.addEventListener('change', renderModelHint);

  const quizzes = boolField('include_quizzes', 'Include quiz checkpoints (retrieval practice)');
  const manim = boolField('allow_manim', 'Manim animations', { disabled: features.manim === false, note: features.manim === false ? 'Not available on this server.' : undefined });
  const freeform = boolField('allow_freeform_manim', 'Allow free-form (custom) Manim code', {
    disabled: features.manim === false || features.manim_freeform === false,
    note: features.manim_freeform === false ? 'Only template animations are available on this server.' : 'Runs in the sandbox; templates are always preferred.',
  });
  const images = boolField('allow_generated_images', 'Generated illustrations', { disabled: features.generated_images === false, note: features.generated_images === false ? 'Not available on this server.' : undefined });
  // Which service draws them ('' = the server's choice, with its backups).
  const imageInfo = (meta && meta.image) || {};
  const imageChoices = features.generated_images === false ? [] : choosableImageProviders(meta);
  const defaultImage = imageChoices.find((p) => p.id === imageInfo.default_provider);
  const imageProvider = imageChoices.length
    ? select({
        options: [
          { value: '', label: `Server default${defaultImage ? ` (${defaultImage.label})` : ''}` },
          ...imageChoices.map((p) => ({ value: p.id, label: `${p.label}${p.paid ? ' (paid)' : ''}${p.configured === false ? ' (not configured)' : ''}`, disabled: p.configured === false })),
        ],
        value: String(defaults.image_provider || ''),
      })
    : null;
  const libraryPictures = hasLibrary(meta)
    ? boolField('prefer_library_visuals', 'Prefer pictures from my library', {
        hint: 'A side-panel illustration uses a closely matching picture from your library instead of a new one.',
      })
    : null;
  const aiVideo = features.ai_video ? boolField('allow_ai_video', 'AI video clips (real-world footage)', { note: 'Costly; capped per lecture.' }) : null;
  const interactive = boolField('allow_interactive', 'Interactive p5 sketches (web player only)');
  const gifs = boolField('allow_gifs', 'Reaction GIFs (web player only)', { disabled: features.gifs === false, note: features.gifs === false ? 'Not configured on this server.' : undefined });
  const review = boolField('review_plan', 'Let me review the lecture plan before scripts are written');

  const syncDisabled = () => {
    quizEvery.disabled = !(/** @type {HTMLInputElement} */ (controls.get('include_quizzes'))).checked;
    const manimOn = (/** @type {HTMLInputElement} */ (controls.get('allow_manim'))).checked;
    const ff = /** @type {HTMLInputElement} */ (controls.get('allow_freeform_manim'));
    ff.disabled = !manimOn || features.manim === false || features.manim_freeform === false;
    if (ff.disabled) ff.checked = false;
    if (aiVideo) maxVideos.disabled = !(/** @type {HTMLInputElement} */ (controls.get('allow_ai_video'))).checked;
    if (imageProvider) imageProvider.disabled = !(/** @type {HTMLInputElement} */ (controls.get('allow_generated_images'))).checked;
  };
  for (const key of ['include_quizzes', 'allow_manim', 'allow_ai_video', 'allow_generated_images']) {
    const c = controls.get(key);
    if (c) c.addEventListener('change', syncDisabled);
  }

  const sections = [];
  if (opts.includeMeta) {
    sections.push(
      fieldset(
        'Lecture details',
        formGrid(
          textField('subject_name', 'Subject', { max: OPTION_LIMITS.subject_name.max, placeholder: 'e.g. Basic Electrical Engineering' }),
          textField('unit_name', 'Unit', { max: OPTION_LIMITS.unit_name.max, placeholder: 'e.g. Electric Circuits' }),
          textField('session_number', 'Session number', { max: OPTION_LIMITS.session_number.max, placeholder: 'e.g. Session 2' }),
          textField('session_title', 'Session title', { max: OPTION_LIMITS.session_title.max, placeholder: "e.g. Ohm's Law" }),
        ),
        h('p', { class: 'field-hint' }, 'Leave blank to let Aadhi take them from the document.'),
      ),
    );
  }
  if (engine) {
    const engineHint = personalKeysEnabled(meta)
      ? "Which AI writes this lecture. Each engine needs an API key: the server's or your own."
      : 'Which AI writes this lecture. Each engine needs its API key on the server.';
    const engineField = field('Engine', engine, { hint: engineHint });
    const hintId = uid('hint');
    modelHint.setAttribute('id', hintId);
    engine.setAttribute('aria-describedby', `${engine.getAttribute('aria-describedby') || ''} ${hintId}`.trim());
    sections.push(fieldset('AI engine', formGrid(reg('llm_provider', engineField, engine)), modelHint, ownKeyHint));
  }
  sections.push(
    fieldset(
      'Language & audience',
      formGrid(
        reg('language', field('Narration language', language), language),
        reg('board_language', field('Board language', boardLanguage, { hint: 'Text on the board can stay in English while narration is translated.' }), boardLanguage),
        textField('audience', 'Audience', { max: OPTION_LIMITS.audience.max }),
        reg('target_minutes', field('Target length (minutes)', minutes, { hint: '3–90 minutes' }), minutes),
        reg('depth', field('Depth', depth), depth),
      ),
    ),
    fieldset('Pedagogy', quizzes, reg('quiz_every_n_concepts', field('Quiz after every N concepts', quizEvery, { hint: '1–6' }), quizEvery), review),
    fieldset('Media', manim, freeform, images, imageProvider ? reg('image_provider', field('Image provider', imageProvider, { hint: 'Which service draws the illustrations.' }), imageProvider) : null, libraryPictures, aiVideo, aiVideo ? reg('max_ai_videos', field('Maximum AI clips', maxVideos, { hint: '0–8' }), maxVideos) : null, interactive, gifs),
    fieldset(
      'Voice',
      formGrid(
        reg('tts_provider', field('Voice provider', provider), provider),
        reg('tts_voice', field('Voice', voice, { hint: 'Voices are filtered by narration language.' }), voice),
        reg('tts_rate', field('Speaking rate', rate), rate),
      ),
    ),
    fieldset(
      'Context',
      textField('previous_session_summary', 'Previous session summary', { rows: 3, max: OPTION_LIMITS.previous_session_summary.max, hint: 'Optional. Adds a short recap at the start.' }),
      textField('extra_instructions', 'Extra instructions for Aadhi', { rows: 3, max: OPTION_LIMITS.extra_instructions.max, placeholder: 'e.g. Use examples from Indian power grids; avoid calculus.' }),
    ),
  );
  if (planModel && scriptModel) {
    sections.push(
      h(
        'details',
        { class: 'advanced' },
        h('summary', {}, 'Advanced (admin): model overrides'),
        formGrid(reg('llm_model_plan', field('Planning model', planModel), planModel), reg('llm_model_script', field('Script model', scriptModel), scriptModel)),
        h('p', { class: 'field-hint' }, 'Only allow-listed models of the selected AI engine are offered.'),
      ),
    );
  }
  syncDisabled();

  const values = () => {
    /** @type {import('../lib/optionsForm.js').OptionValues} */
    const out = { ...defaults };
    for (const [key, c] of controls) {
      out[key] = c instanceof HTMLInputElement && c.type === 'checkbox' ? c.checked : c.value;
    }
    return out;
  };

  return {
    el: h('div', { class: 'options-form' }, sections),
    values,
    build() {
      const result = buildGenerationOptions(values(), meta, { isAdmin: opts.isAdmin });
      for (const [key, wrap] of wraps) if (wrap.setError) wrap.setError(result.errors[key] || null);
      const firstBad = Object.keys(result.errors)[0];
      if (firstBad && controls.get(firstBad)) /** @type {HTMLElement} */ (controls.get(firstBad)).focus();
      return result;
    },
    setDisabled(disabled) {
      for (const c of controls.values()) c.disabled = disabled || featureLocked.has(c);
      if (!disabled) syncDisabled();
    },
  };
}
