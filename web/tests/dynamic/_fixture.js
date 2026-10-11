// @ts-check
/**
 * A small Dynamic timeline (design §6.7) for the stage tests: a board scene with reveals and a blank step, a chapter
 * card on a colour block entered by a crossfade, a quiz, a simulation clip and a board scene with a picture panel.
 * `dynamicTimeline('portrait')` gives the 1080x1920 reel of the same lecture.
 */

const TOKENS = {
  'struct.accent': '#FF936D', bg: '#10132C', 'bg-alt': '#181D37', 'bg-tint': '#22121C', surface: '#252A3F',
};

/** @param {string} preset @param {string} tone @param {number} seed @param {string} kind @param {number} [seconds] @param {number | null} [frozenAt] */
function look(preset, tone, seed, kind = 'cut', seconds = 0, frozenAt = null) {
  return {
    template: 'board_generic',
    tokens: { ...TOKENS },
    background: { preset, params: { tone }, seed, frozen_at: frozenAt },
    transition_in: { kind, seconds },
  };
}

/**
 * @param {string} id @param {number} start @param {number} speechEnd @param {number} end @param {string} text
 * @param {Record<string, any>} [extra]
 */
function beat(id, start, speechEnd, end, text, extra = {}) {
  return { beat_id: id, index: 0, start, speech_end: speechEnd, end, narration: text, captions: [{ start, end: speechEnd, text }], ...extra };
}

/**
 * @param {'landscape' | 'portrait'} [format]
 * @returns {any}
 */
export function dynamicTimeline(format = 'landscape') {
  const portrait = format === 'portrait';
  const scenes = [
    {
      scene_id: 's-board', index: 0, type: 'content', title: 'Voltage and current', start: 0, duration: 10, audio_offset: 0.5,
      board: [
        { id: 'b1', kind: 'heading', text: 'Charge flows like water' },
        { id: 'b2', kind: 'bullet', text: 'Voltage is the push' },
        { id: 'b3', kind: 'definition', term: 'Current', text: 'The rate of flow of charge' },
        { id: 'b4', kind: 'example_step', text: 'I = 2 A', blank: true },
        { id: 'b5', kind: 'callout_tip', text: 'Units matter' },
      ],
      beats: [
        beat('k1', 0.5, 2, 2.2, 'Charge flows like water.', { board_item_id: 'b2' }),
        beat('k2', 2.2, 4, 4.2, 'Current is the rate of flow.', { board_item_id: 'b3' }),
        beat('k3', 4.2, 6, 6.2, 'Your turn: the current is two amperes.', { board_item_id: 'b4' }),
        beat('k4', 6.2, 8, 8.2, 'Units matter.', { board_item_id: 'b5', fill_item_id: 'b4' }),
      ],
      look: look('mesh_drift', 'bg', 101),
    },
    {
      scene_id: 's-chapter', index: 1, type: 'chapter_card', title: 'The law', chapter_label: 'Part 2', subtitle: 'Why the bulb glows',
      start: 10, duration: 4, board: [], beats: [], look: look('colour_block', 'bg', 202, 'crossfade', 0.45),
    },
    {
      scene_id: 's-quiz', index: 2, type: 'quiz_checkpoint', title: 'Quick check', start: 14, duration: 12, audio_offset: 0.5,
      beats: [beat('q1', 0.5, 2.5, 2.7, 'What happens to the current?')],
      quiz: {
        question: 'We double the voltage. What happens to the current?', options: ['It halves', 'It doubles', 'It stays'],
        correct_index: 1, feedback_wrong: ['', '', ''], explanation: 'I = V / R, so it doubles.',
        countdown_start: 3, countdown_seconds: 5, reveal_start: 8,
      },
      look: look('mesh_drift', 'bg-alt', 303, 'crossfade', 0.45, 0),
    },
    {
      scene_id: 's-sim', index: 3, type: 'simulation', title: 'Watch the current', start: 26, duration: 6, audio_offset: 0.5,
      media: { kind: 'video', asset_key: 'manim-sim', url: '/media/sim.mp4', duration: 4, width: 1280, height: 720, fit: 'contain', end_behavior: 'freeze' },
      beats: [beat('m1', 0.5, 3, 3.2, 'Watch the current.')],
      kinetic_captions: [
        { start: 0.5, end: 1.6, words: [{ text: 'Watch', start: 0.5, end: 0.9 }, { text: 'the', start: 0.9, end: 1.1 }, { text: 'current', start: 1.1, end: 1.6, key: true }], lines: ['Watch the current'] },
        { start: 1.6, end: 3.0, words: [{ text: 'flow', start: 1.6, end: 2 }], lines: ['flow'], blank: true },
      ],
      look: look('mesh_drift', 'bg-tint', 404),
    },
    {
      scene_id: 's-figure', index: 4, type: 'content', title: 'A circuit', start: 32, duration: 8, audio_offset: 0.5,
      board: [{ id: 'f1', kind: 'bullet', text: 'Follow the loop' }],
      side_panel: { panel: { kind: 'image', title: 'Circuit' }, media: { kind: 'image', asset_key: 'img-1', url: '/media/circuit.png', width: 1024, height: 576, fit: 'contain' }, show_at: 1 },
      layout: { show_side_panel: true },
      beats: [beat('f-1', 0.5, 3, 3.2, 'Follow the loop.', { board_item_id: 'f1' })],
      look: look('mesh_drift', 'bg', 505),
    },
  ];
  const captions = [];
  for (const s of scenes) for (const b of s.beats || []) for (const c of b.captions || []) captions.push({ ...c, start: c.start + s.start, end: c.end + s.start });
  return {
    schema_version: 3,
    language: 'en-IN',
    board_language: 'en-IN',
    fps: 30,
    width: portrait ? 1080 : 1920,
    height: portrait ? 1920 : 1080,
    total_duration: 40,
    chapters: [{ start: 0, title: 'Basics' }, { start: 10, title: 'The law' }],
    captions,
    transition_seconds: 0.4,
    scenes,
    presentation: {
      style: 'dynamic', format, safe_zone: 'universal',
      content: { ink: '#F3F5F9', ink2: '#B9BEC7', 'entity:0': '#0FE3FE', 'entity:1': '#C4A4FE', 'entity:2': '#FEACE3', warn: '#F8A87F', ok: '#5DD8AA', mark: '#F2D935', focus: '#F2D935' },
      fonts: { display: 'outfit', body: 'inter', mono: 'jetbrains_mono' },
      personality: portrait ? 'crisp' : 'soft', caption_style: portrait ? 'bold_outline' : 'clean_pill', cue_term_style: 'underline',
      captions_burn: true, sfx: false, motion: 'standard', generated_seconds: 40, engine_version: 1,
    },
  };
}

/** A classic timeline (no presentation): must never reach the stage. */
export function classicTimeline() {
  const t = dynamicTimeline('landscape');
  delete t.presentation;
  for (const s of t.scenes) delete s.look;
  t.schema_version = 2;
  return t;
}
