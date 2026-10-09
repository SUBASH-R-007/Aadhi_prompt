// @ts-check
/**
 * Small pure helpers of the editor's history and of "what changed since Aadhi wrote it":
 *  - undo labels: `editLabel(key)` names an inspector edit from its coalesce key ("Edit narration");
 *  - GET /api/versions/{vid}/changes: `changesBySceneId`, `changeBadge` (scene list), `fieldWords` (the
 *    fields a teacher changed, in plain words), `restorePosition` (where a removed scene comes back).
 */

/** Undo/redo history depth (snapshots share structure, so this stays small in memory). */
export const HISTORY_LIMIT = 200;

/** Inspector coalesce keys (data-fk names) -> the undo label of the edit. */
const EDIT_LABELS = /** @type {Array<[RegExp, string]>} */ ([
  [/^scene:title$/, 'Edit title'],
  [/^scene:subtitle$/, 'Edit subtitle'],
  [/^scene:(goal|notes)$/, 'Edit teacher notes'],
  [/^scene:chlabel$/, 'Edit chapter label'],
  [/^scene:manim/, 'Edit animation'],
  [/^scene:vprompt$/, 'Edit video description'],
  [/^scene:(vrat|vfb)$/, 'Edit footage details'],
  [/^scene:p5$/, 'Edit sketch code'],
  [/^scene:min_seconds$/, 'Change minimum duration'],
  [/^beat:[a-z]+:.+:narration$/, 'Edit narration'],
  [/^beat:[a-z]+:.+:spoken$/, 'Edit pronunciation'],
  [/^beat:[a-z]+:.+:pause$/, 'Change pause'],
  [/^beat:[a-z]+:.+:cue$/, 'Edit visual cue'],
  [/^item:/, 'Edit board'],
  [/^(panel|chart|graph|m3d|tq):/, 'Edit side panel'],
  [/^quiz:/, 'Edit quiz'],
]);

/**
 * Undo label of an inspector edit, from its coalesce key (without the scene prefix).
 * @param {string | null | undefined} key
 * @returns {string}
 */
export function editLabel(key) {
  const k = String(key || '');
  for (const [re, label] of EDIT_LABELS) if (re.test(k)) return label;
  return 'Edit scene';
}

/**
 * @typedef {import('../../types.js').SceneChange} SceneChange
 * @typedef {import('../../types.js').VersionChanges} VersionChanges
 */

/**
 * Scene id -> its change (scenes of the current screenplay and removed ones).
 * @param {VersionChanges | null | undefined} res
 * @returns {Map<string, SceneChange>}
 */
export function changesBySceneId(res) {
  /** @type {Map<string, SceneChange>} */
  const out = new Map();
  if (!res || !res.available || !Array.isArray(res.scenes)) return out;
  for (const c of res.scenes) {
    if (c && typeof c.scene_id === 'string') out.set(c.scene_id, c);
  }
  return out;
}

/** Scene fields (GET /changes `fields_changed`) in plain words. */
const FIELD_WORDS = /** @type {Record<string, string>} */ ({
  title: 'title',
  subtitle: 'subtitle',
  type: 'scene type',
  beats: 'narration',
  reveal_beats: 'answer narration',
  narration: 'narration',
  board: 'board',
  side_panel: 'side panel',
  mascot_position: 'Aadhi’s position',
  concept_id: 'concept',
  chapter_id: 'chapter',
  objective_ids: 'objectives',
  intent: 'teaching intent',
  notes: 'teacher notes',
  hidden: 'shown or skipped',
  min_seconds: 'minimum duration',
  chapter_label: 'chapter label',
  manim: 'animation',
  override_asset_key: 'media',
  poster_override_asset_key: 'poster',
  video_prompt: 'video description',
  rationale: 'footage note',
  fallback_image_prompt: 'fallback picture',
  fallback_figure_id: 'fallback picture',
  variant: 'AI version',
  p5_code: 'sketch code',
  question: 'quiz question',
  options: 'quiz options',
  correct_index: 'correct answer',
  feedback_wrong: 'quiz feedback',
  option_misconception_ids: 'quiz feedback',
  explanation: 'quiz explanation',
  bloom: 'question level',
  countdown_seconds: 'countdown',
  source_refs: 'sources',
});

/**
 * The changed fields in plain words, without repeats ("narration, board, title").
 * @param {string[] | null | undefined} fields
 * @returns {string[]}
 */
export function fieldWords(fields) {
  /** @type {string[]} */
  const out = [];
  for (const f of fields || []) {
    const word = FIELD_WORDS[f] || String(f).replace(/_/g, ' ').trim();
    if (word && !out.includes(word)) out.push(word);
  }
  return out;
}

/**
 * The scene list's marker for a scene's change; null when there is nothing to show.
 * @param {SceneChange | null | undefined} change
 * @returns {{ label: string, title: string, status: string } | null}
 */
export function changeBadge(change) {
  if (!change) return null;
  if (change.status === 'edited') {
    const words = fieldWords(change.fields_changed);
    return { status: 'edited', label: 'Edited', title: words.length ? `Changed since Aadhi wrote it: ${words.join(', ')}` : 'Changed since Aadhi wrote it' };
  }
  if (change.status === 'added') return { status: 'added', label: 'New', title: 'Added in the editor' };
  if (change.status === 'moved') {
    const from = Number.isInteger(change.generated_index) ? ` (was scene ${/** @type {number} */ (change.generated_index) + 1})` : '';
    return { status: 'moved', label: 'Moved', title: `Moved from where Aadhi placed it${from}` };
  }
  return null;
}

/**
 * Where a removed scene comes back in the current screenplay: right after the nearest scene that came
 * before it in the generated order and is still there (0 when none is).
 * @param {SceneChange[]} all
 * @param {SceneChange} removed
 * @returns {number}
 */
export function restorePosition(all, removed) {
  const g = Number.isInteger(removed.generated_index) ? /** @type {number} */ (removed.generated_index) : -1;
  let best = -1;
  let bestGen = -1;
  for (const c of all) {
    if (c === removed || c.status === 'removed') continue;
    const cg = Number.isInteger(c.generated_index) ? /** @type {number} */ (c.generated_index) : -1;
    const ci = Number.isInteger(c.current_index) ? /** @type {number} */ (c.current_index) : -1;
    if (cg < 0 || ci < 0 || cg >= g) continue;
    if (cg > bestGen) {
      bestGen = cg;
      best = ci;
    }
  }
  return best + 1;
}
