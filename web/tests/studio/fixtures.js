// Shared test fixtures (valid against aadhi/schemas/screenplay.py; checked by
// fixtures_validate.py-style round trips during development).

/** A small but complete v2 screenplay. Returns a fresh copy each call. */
export function sampleScreenplay() {
  return {
    schema_version: 2,
    subject_name: 'Basic Electrical Engineering',
    unit_name: 'Electric Circuits',
    session_number: 'Session 2',
    session_title: "Ohm's Law",
    language: 'en-IN',
    board_language: null,
    learning_objectives: [
      { id: 'obj-1', text: "State Ohm's law", bloom: 'remember', concept_ids: ['ohms-law'] },
      { id: 'obj-2', text: 'Compute current in a resistor', bloom: 'apply', concept_ids: ['ohms-law'] },
    ],
    concept_map: [
      { id: 'voltage', title: 'Voltage', summary: '', depends_on: [], kind: 'prerequisite' },
      { id: 'ohms-law', title: "Ohm's law", summary: '', depends_on: ['voltage'], kind: 'core' },
    ],
    misconceptions: [
      { id: 'm1', concept_id: 'ohms-law', statement: 'Current is used up', correction: 'Charge is conserved' },
    ],
    chapters: [
      { id: 'ch1', title: 'Foundations', scene_ids: ['s1', 's2'] },
      { id: 'ch2', title: 'Practice', scene_ids: ['s3', 's4'] },
    ],
    lexicon: [],
    scenes: [
      {
        id: 's1',
        type: 'title',
        concept_id: null,
        chapter_id: 'ch1',
        title: "Ohm's Law",
        subtitle: null,
        mascot_position: 'center',
        beats: [{ id: 's1-b1', narration: 'Welcome to the session.', spoken: null, board_item_id: null, fill_item_id: null, highlight_item_ids: [], pause_after: 0, visual_cue: null, source_refs: [] }],
        side_panel: null,
        objective_ids: [],
        intent: null,
        notes: '',
        board: [],
      },
      {
        id: 's2',
        type: 'content',
        concept_id: 'ohms-law',
        chapter_id: 'ch1',
        title: 'The law',
        subtitle: null,
        mascot_position: 'left',
        beats: [
          { id: 's2-b1', narration: 'Voltage pushes charge.', spoken: null, board_item_id: 's2-i1', fill_item_id: null, highlight_item_ids: [], pause_after: 0, visual_cue: null, source_refs: [] },
          { id: 's2-b2', narration: 'Here is the formula.', spoken: null, board_item_id: 's2-i2', fill_item_id: null, highlight_item_ids: ['s2-i1'], pause_after: 0.5, visual_cue: null, source_refs: [] },
          { id: 's2-b3', narration: 'Remember it.', spoken: null, board_item_id: null, fill_item_id: null, highlight_item_ids: ['s2-i2', 's2-i1'], pause_after: 0, visual_cue: null, source_refs: [] },
        ],
        side_panel: { kind: 'skill_tree', title: null, rationale: '', show_from_beat_id: 's2-b2' },
        objective_ids: ['obj-1'],
        intent: { goal: 'Introduce the law', key_points: ['V = IR'], source_refs: [] },
        notes: '',
        board: [
          { id: 's2-i1', kind: 'bullet', text: 'Voltage is **pressure**', source_refs: [] },
          { id: 's2-i2', kind: 'formula', text: 'Ohm', latex: 'V = IR', source_refs: [] },
          { id: 's2-i3', kind: 'takeaway', text: 'Linear relation', source_refs: [] },
        ],
      },
      {
        id: 's3',
        type: 'example',
        concept_id: 'ohms-law',
        chapter_id: 'ch2',
        title: 'Worked example',
        subtitle: null,
        mascot_position: 'left',
        beats: [
          { id: 's3-b1', narration: 'Given twelve volts.', spoken: null, board_item_id: 's3-i1', fill_item_id: null, highlight_item_ids: [], pause_after: 0, visual_cue: null, source_refs: [] },
          { id: 's3-b2', narration: 'Step two is blank.', spoken: null, board_item_id: 's3-i2', fill_item_id: null, highlight_item_ids: [], pause_after: 3, visual_cue: null, source_refs: [] },
          { id: 's3-b3', narration: 'Fill it in.', spoken: null, board_item_id: null, fill_item_id: 's3-i2', highlight_item_ids: ['s3-i1'], pause_after: 0, visual_cue: null, source_refs: [] },
        ],
        side_panel: null,
        objective_ids: ['obj-2'],
        intent: null,
        notes: '',
        board: [
          { id: 's3-i1', kind: 'example_step', text: 'V = 12 V, R = 4 Ω', justification: 'Given', blank: false, source_refs: [] },
          { id: 's3-i2', kind: 'example_step', text: 'I = V / R = 3 A', justification: 'Ohm', blank: true, source_refs: [] },
        ],
      },
      {
        id: 's4',
        type: 'quiz_checkpoint',
        concept_id: 'ohms-law',
        chapter_id: 'ch2',
        title: 'Check',
        subtitle: null,
        mascot_position: 'left',
        beats: [{ id: 's4-b1', narration: 'Quick question.', spoken: null, board_item_id: null, fill_item_id: null, highlight_item_ids: [], pause_after: 0, visual_cue: null, source_refs: [] }],
        side_panel: null,
        objective_ids: ['obj-2'],
        intent: null,
        notes: '',
        question: 'What is I when V = 6 and R = 3?',
        options: ['2 A', '18 A', '0.5 A'],
        correct_index: 0,
        feedback_wrong: ['', 'You multiplied', 'You inverted'],
        option_misconception_ids: [null, null, 'm1'],
        explanation: 'I = V/R',
        bloom: 'apply',
        countdown_seconds: 8,
        reveal_beats: [{ id: 's4-b2', narration: 'It is two amperes.', spoken: null, board_item_id: null, fill_item_id: null, highlight_item_ids: [], pause_after: 0, visual_cue: null, source_refs: [] }],
        source_refs: [],
      },
    ],
    companion_sheet: { key_formulas: [], definitions: [], misconceptions: [], practice_problems: [], legacy_markdown: null },
    figures: [{ id: 'fig-1', caption: 'Circuit', page: 1, asset_key: null, width: null, height: null }],
    source: { filename: 'ohm.pdf', mime: 'application/pdf', pages: 3, sha256: null },
  };
}

/**
 * GET /api/meta exactly as docs/API.md documents it (and aadhi/api/routers/meta.py sends it):
 * Manim template entries carry only name/title/description/steps_hint.
 */
export function sampleMeta() {
  return {
    version: '2.0.0',
    languages: [
      { code: 'en-IN', label: 'English (India)' },
      { code: 'ta-IN', label: 'Tamil' },
      { code: 'hi-IN', label: 'Hindi' },
    ],
    tts: {
      default_provider: 'edge',
      providers: [
        {
          id: 'edge',
          label: 'Microsoft Edge (free)',
          configured: true,
          word_timings: true,
          voices: [
            { id: 'en-IN-NeerjaNeural', label: 'Neerja', language: 'en-IN', gender: 'Female' },
            { id: 'en-US-AriaNeural', label: 'Aria', language: 'en-US', gender: 'Female' },
            { id: 'ta-IN-PallaviNeural', label: 'Pallavi', language: 'ta-IN', gender: 'Female' },
          ],
        },
        { id: 'elevenlabs', label: 'ElevenLabs', configured: false, word_timings: true, voices: [] },
      ],
    },
    llm: {
      provider: 'fake',
      configured: true,
      models: { plan: 'p', script: 's', critic: 'c', fast: 'f' },
      engines: [
        { id: 'fake', label: 'Offline test engine', configured: true, models: { plan: 'p', script: 's', critic: 'c', fast: 'f' } },
        { id: 'gemini', label: 'Google Gemini', configured: true, models: { plan: 'gemini-2.5-pro', script: 'gemini-2.5-flash', critic: 'gemini-2.5-flash', fast: 'gemini-2.5-flash' } },
        { id: 'openai', label: 'OpenAI', configured: false, models: { plan: 'gpt-4.1', script: 'gpt-4.1-mini', critic: 'gpt-4.1-mini', fast: 'gpt-4.1-mini' } },
        { id: 'anthropic', label: 'Anthropic Claude', configured: true, models: { plan: 'claude-opus-5-5', script: 'claude-opus-5-5', critic: 'claude-opus-5-5', fast: 'claude-opus-5-5' } },
      ],
      override_allowlist: ['gemini-pro'],
    },
    features: { ai_video: false, manim: true, manim_freeform: true, generated_images: true, gifs: false, render: true, storage: 'local' },
    manim_templates: [
      { name: 'equation_steps', title: 'Equation steps', description: 'Step-by-step algebra', steps_hint: 'one step per item of steps' },
      { name: 'function_plot', title: 'Function plot', description: 'Plot functions step by step', steps_hint: 'one step per item of steps' },
    ],
    limits: { upload_max_mb: 50, max_json_body_mb: 5, max_cost_per_lecture_usd: 5, daily_budget_usd: 15 },
    generation_defaults: {
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
    },
  };
}

/**
 * /api/meta with the template extras requested from the API owner (`params_schema` and
 * `example_params` from aadhi.manim.base.TemplateInfo). Not sent by the server today; the
 * Studio uses them when present (schema form, valid starting parameters).
 */
export function sampleMetaWithTemplateSchemas() {
  const meta = sampleMeta();
  meta.manim_templates = [
    {
      name: 'equation_steps',
      title: 'Equation steps',
      description: 'Step-by-step algebra',
      steps_hint: 'one step per item of steps',
      params_schema: {
        type: 'object',
        properties: { steps: { type: 'array', items: { type: 'string', minLength: 1 }, minItems: 1, title: 'Steps' } },
        required: ['steps'],
      },
      example_params: { steps: ['V = IR', 'I = V/R'] },
    },
  ];
  return meta;
}

/** A signed-in teacher (GET /api/auth/me `user`). */
export function sampleUser(overrides = {}) {
  return { id: 7, username: 'teacher1', role: 'editor', must_change_password: false, is_active: true, daily_budget_usd: null, ...overrides };
}

/** GET /api/versions/{id} for the sample screenplay. */
export function sampleVersion(overrides = {}) {
  return {
    id: 9,
    number: 1,
    label: '',
    status: 'ready',
    language: 'en-IN',
    revision: 4,
    built_revision: 4,
    timeline_stale: false,
    has_timeline: false,
    source_version_id: null,
    issue_counts: {},
    created_at: '2026-09-01T10:00:00Z',
    updated_at: '2026-09-01T10:00:00Z',
    screenplay: sampleScreenplay(),
    issues: [],
    stale_scenes: [],
    project_id: 5,
    generation_meta: {},
    plan: null,
    ...overrides,
  };
}
