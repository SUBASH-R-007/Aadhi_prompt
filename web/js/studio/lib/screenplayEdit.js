// @ts-check
/**
 * Pure, immutable screenplay edit helpers (aadhi/schemas/screenplay.py is the contract).
 *
 * Every function returns NEW objects and never mutates its inputs, so the editor can keep an
 * undo history of snapshots and the conflict merger can diff them. Each edit keeps the
 * structural invariants the server validates:
 *   - a board item is revealed by at most one beat; references point at existing items;
 *   - a beat may only fill a *blank* example_step that is revealed at or before that beat,
 *     and each blank is filled at most once;
 *   - highlights (max 2) point at items already visible at that beat;
 *   - only board scenes reference board items;
 *   - side_panel.show_from_beat_id and formula legend rows' beat_id name a beat of the scene;
 *   - quiz arrays (feedback_wrong, option_misconception_ids) stay aligned with options;
 *   - ids are unique slugs (`{scene}-b{n}` for beats, `{scene}-i{n}` for items).
 */

import { clone, moveIndex } from '../util.js';

/** @typedef {Record<string, any>} Screenplay */
/** @typedef {Record<string, any>} Scene */
/** @typedef {Record<string, any>} Beat */
/** @typedef {Record<string, any>} BoardItem */
/** @typedef {'main' | 'reveal'} BeatPhase */

export const BOARD_TYPES = /** @type {const} */ (['title', 'content', 'example', 'summary', 'key_takeaway', 'recap']);
export const SCENE_TYPES = /** @type {const} */ ([
  'title', 'content', 'example', 'summary', 'key_takeaway', 'recap',
  'chapter_card', 'simulation', 'ai_video', 'interactive', 'quiz_checkpoint',
]);
export const SCENE_TYPE_LABELS = /** @type {Record<string, string>} */ ({
  title: 'Title',
  content: 'Content',
  example: 'Worked example',
  summary: 'Summary',
  key_takeaway: 'Key takeaway',
  recap: 'Recap',
  chapter_card: 'Chapter card',
  simulation: 'Simulation (Manim)',
  ai_video: 'Real-world video',
  interactive: 'Interactive (p5)',
  quiz_checkpoint: 'Quiz checkpoint',
});
export const BOARD_ITEM_KINDS = /** @type {const} */ ([
  'heading', 'bullet', 'paragraph', 'definition', 'formula', 'callout_info', 'callout_tip', 'callout_warning',
  'misconception', 'code', 'table', 'figure', 'example_step', 'takeaway',
]);
export const BOARD_ITEM_LABELS = /** @type {Record<string, string>} */ ({
  heading: 'Heading',
  bullet: 'Bullet',
  paragraph: 'Paragraph',
  definition: 'Definition',
  formula: 'Formula',
  callout_info: 'Info callout',
  callout_tip: 'Tip callout',
  callout_warning: 'Warning callout',
  misconception: 'Misconception',
  code: 'Code',
  table: 'Table',
  figure: 'Figure',
  example_step: 'Example step',
  takeaway: 'Takeaway',
});
/** Kinds whose `text` is required by the schema. */
export const TEXT_REQUIRED_KINDS = new Set([
  'heading', 'bullet', 'paragraph', 'callout_info', 'callout_tip', 'callout_warning', 'misconception', 'example_step', 'takeaway',
]);
export const SIDE_PANEL_KINDS = /** @type {const} */ ([
  'skill_tree', 'figure', 'image', 'chart', 'graph', 'model_3d', 'manim', 'terminal', 'quiz', 'gif',
]);
export const MASCOT_POSITIONS = /** @type {const} */ (['left', 'right', 'center', 'popup_bottom_left', 'popup_bottom_right', 'hidden']);
export const BLOOM_LEVELS = /** @type {const} */ (['remember', 'understand', 'apply', 'analyze', 'evaluate', 'create']);

export const MAX_BOARD_ITEMS = 12;
export const MAX_BEATS = 40;
export const MAX_CHAPTER_CARD_BEATS = 4;
export const MAX_REVEAL_BEATS = 10;
export const MAX_HIGHLIGHTS = 2;
export const MAX_QUIZ_OPTIONS = 5;
export const MIN_QUIZ_OPTIONS = 2;
export const MAX_SCENES = 200;

const COMMON_SCENE_KEYS = [
  'id', 'concept_id', 'chapter_id', 'title', 'subtitle', 'mascot_position', 'beats', 'side_panel', 'objective_ids',
  'intent', 'notes',
];

/** @param {Scene | null | undefined} scene */
export function isBoardScene(scene) {
  return !!scene && /** @type {readonly string[]} */ (BOARD_TYPES).includes(scene.type);
}

/**
 * The first `max` UTF-16 units of `text`, never ending in half of a surrogate pair (an emoji cut in
 * two is a lone surrogate, which the server refuses to store).
 * @param {string} text
 * @param {number} max
 */
export function truncateText(text, max) {
  const cut = String(text).slice(0, max);
  return /[\uD800-\uDBFF]$/.test(cut) ? cut.slice(0, -1) : cut;
}

/**
 * Slug rule of the schema (`^[a-z0-9][a-z0-9_\-]{0,63}$`).
 * @param {string} value
 * @param {string} [fallback]
 */
export function toSlug(value, fallback = 'item') {
  let v = String(value || '')
    .normalize('NFKD')
    .replace(/[^\x20-\x7e]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, '-')
    .replace(/^[-_]+|[-_]+$/g, '');
  if (!v) v = fallback;
  if (!/^[a-z0-9]/.test(v)) v = `${fallback}-${v}`;
  return v.slice(0, 64);
}

/**
 * Unique id from a base slug and the ids already taken ("s3", "s3-2", "s3-3" ...).
 * @param {string} base
 * @param {Iterable<string>} taken
 */
export function uniqueId(base, taken) {
  const set = taken instanceof Set ? taken : new Set(taken);
  const slug = toSlug(base);
  if (!set.has(slug)) return slug;
  for (let n = 2; n < 100000; n++) {
    const suffix = `-${n}`;
    const candidate = `${slug.slice(0, 64 - suffix.length)}${suffix}`;
    if (!set.has(candidate)) return candidate;
  }
  throw new Error('could not allocate a unique id');
}

/**
 * Next id of the form `{prefix}{n}`: one more than the highest existing number, so ids of
 * deleted things are not reused (a reused id would look like an edit of the old one to the
 * conflict merger and to asset caches). Respects the 64-char slug limit.
 * @param {string} prefix
 * @param {Iterable<string>} taken
 */
function sequentialId(prefix, taken) {
  const set = taken instanceof Set ? taken : new Set(taken);
  let max = 0;
  for (const id of set) {
    if (typeof id !== 'string') continue;
    for (const p of [prefix, prefix.slice(0, 63)]) {
      if (id.startsWith(p)) {
        const rest = id.slice(p.length);
        if (/^\d{1,6}$/.test(rest)) max = Math.max(max, Number(rest));
      }
    }
  }
  for (let n = max + 1; n < 1000000; n++) {
    const suffix = String(n);
    const candidate = `${prefix.slice(0, 64 - suffix.length)}${suffix}`;
    if (!set.has(candidate)) return candidate;
  }
  throw new Error('could not allocate a sequential id');
}

/**
 * Beats of a scene in timeline order with their phase and per-phase index.
 * @param {Scene} scene
 * @returns {{ beat: Beat, phase: BeatPhase, index: number }[]}
 */
export function sceneBeats(scene) {
  const main = (scene.beats || []).map((/** @type {Beat} */ beat, /** @type {number} */ index) => ({ beat, phase: /** @type {BeatPhase} */ ('main'), index }));
  const reveal = (scene.reveal_beats || []).map((/** @type {Beat} */ beat, /** @type {number} */ index) => ({ beat, phase: /** @type {BeatPhase} */ ('reveal'), index }));
  return [...main, ...reveal];
}

/** @param {Scene} scene */
function beatIdSet(scene) {
  return new Set(sceneBeats(scene).map((b) => String(b.beat.id)));
}

/** @param {Scene} scene */
export function nextBeatId(scene) {
  return sequentialId(`${scene.id}-b`, beatIdSet(scene));
}

/** @param {Scene} scene */
export function nextItemId(scene) {
  return sequentialId(`${scene.id}-i`, (scene.board || []).map((/** @type {BoardItem} */ i) => String(i.id)));
}

/**
 * A new (empty) beat. Narration must be written before saving (it is required by the schema;
 * `localProblems` reports empty narration).
 * @param {Scene} scene
 * @param {Partial<Beat>} [fields]
 * @returns {Beat}
 */
export function newBeat(scene, fields = {}) {
  return {
    id: nextBeatId(scene),
    narration: '',
    spoken: null,
    board_item_id: null,
    fill_item_id: null,
    highlight_item_ids: [],
    pause_after: 0,
    visual_cue: null,
    source_refs: [],
    ...fields,
  };
}

/**
 * Minimal board item of a kind, with placeholder payloads where the schema requires them.
 * @param {string} kind
 * @param {Scene} scene
 * @param {Screenplay | null} [sp]  needed for `figure` items (first source figure)
 * @returns {BoardItem}
 */
export function newBoardItem(kind, scene, sp = null) {
  /** @type {BoardItem} */
  const item = { id: nextItemId(scene), kind, text: '', source_refs: [] };
  return applyKindDefaults(item, kind, sp);
}

/**
 * @param {BoardItem} item
 * @param {string} kind
 * @param {Screenplay | null} sp
 */
function applyKindDefaults(item, kind, sp) {
  /** @type {BoardItem} */
  const out = { ...item, kind };
  if (kind === 'formula' && !(out.latex || '').trim()) out.latex = 'y = mx + c';
  if (kind === 'code') {
    if (!(out.code || '').trim()) out.code = out.text || '# code';
    if (!out.language) out.language = 'python';
  }
  if (kind === 'table') {
    if (!Array.isArray(out.headers) || out.headers.length === 0) out.headers = ['Column 1', 'Column 2'];
    const width = out.headers.length;
    if (!Array.isArray(out.rows) || out.rows.length === 0) out.rows = [new Array(width).fill('')];
    out.rows = out.rows.map((/** @type {string[]} */ r) => normaliseRow(r, width));
  }
  if (kind === 'figure' && !out.figure_id) {
    const first = sp && Array.isArray(sp.figures) && sp.figures.length ? sp.figures[0].id : null;
    out.figure_id = first;
  }
  if (kind === 'definition' && !out.term && !out.text) out.term = '';
  if (kind !== 'example_step') out.blank = false;
  else if (out.blank === undefined) out.blank = false;
  if (kind !== 'misconception') out.misconception_id = null;
  return out;
}

/**
 * @param {string[]} row
 * @param {number} width
 */
function normaliseRow(row, width) {
  const r = Array.isArray(row) ? row.slice(0, width).map((c) => String(c ?? '')) : [];
  while (r.length < width) r.push('');
  return r;
}

/**
 * A new scene of the given type with valid structure (beats need narration before saving).
 * @param {string} type
 * @param {Screenplay} sp
 * @param {{ title?: string, manimTemplate?: { name: string, example_params?: Record<string, any> } | null }} [opts]
 * @returns {Scene}
 */
export function newScene(type, sp, opts = {}) {
  const taken = new Set((sp.scenes || []).map((/** @type {Scene} */ s) => String(s.id)));
  const id = sequentialId('s', taken);
  /** @type {Scene} */
  const base = {
    id,
    type: '', // converted below; an empty source type means "nothing to carry over"
    concept_id: null,
    chapter_id: null,
    title: opts.title || '',
    subtitle: null,
    mascot_position: 'left',
    beats: [],
    side_panel: null,
    objective_ids: [],
    intent: null,
    notes: '',
  };
  return convertSceneShape(base, type, opts);
}

/**
 * Give a scene of (possibly) another type the fields its new type needs and drop the others.
 * @param {Scene} scene
 * @param {string} type
 * @param {{ manimTemplate?: { name: string, example_params?: Record<string, any> } | null }} [opts]
 * @returns {Scene}
 */
function convertSceneShape(scene, type, opts = {}) {
  /** @type {Scene} */
  const out = {};
  for (const k of COMMON_SCENE_KEYS) if (k in scene) out[k] = clone(scene[k]);
  out.type = type;
  out.beats = Array.isArray(out.beats) ? out.beats : [];
  const wasQuiz = scene.type === 'quiz_checkpoint';
  if (isBoardScene({ type })) {
    out.board = isBoardScene(scene) ? clone(scene.board || []) : [];
  } else if (type === 'chapter_card') {
    out.chapter_label = scene.chapter_label || 'Part 1';
    out.beats = out.beats.slice(0, MAX_CHAPTER_CARD_BEATS);
  } else if (type === 'simulation') {
    const tpl = opts.manimTemplate;
    out.manim = scene.manim
      ? clone(scene.manim)
      : tpl
        ? { template: tpl.name, params: clone(tpl.example_params || {}), code: null }
        : { template: null, params: {}, code: DEFAULT_MANIM_CODE };
    out.override_asset_key = scene.type === 'simulation' ? scene.override_asset_key || null : null;
  } else if (type === 'ai_video') {
    out.video_prompt = scene.video_prompt || scene.title || '';
    out.rationale = scene.rationale || '';
    out.fallback_image_prompt = scene.fallback_image_prompt || null;
    out.fallback_figure_id = scene.fallback_figure_id || null;
    out.override_asset_key = scene.type === 'ai_video' ? scene.override_asset_key || null : null;
  } else if (type === 'interactive') {
    out.p5_code = scene.p5_code || DEFAULT_P5_CODE;
    out.poster_override_asset_key = scene.poster_override_asset_key || null;
  } else if (type === 'quiz_checkpoint') {
    if (wasQuiz) {
      for (const k of QUIZ_KEYS) if (k in scene) out[k] = clone(scene[k]);
    } else {
      out.question = scene.title || '';
      out.options = ['', ''];
      out.correct_index = 0;
      out.feedback_wrong = ['', ''];
      out.option_misconception_ids = [null, null];
      out.explanation = '';
      out.bloom = 'understand';
      out.countdown_seconds = 8;
      out.reveal_beats = [];
      out.source_refs = [];
    }
  }
  if (type !== 'chapter_card' && out.beats.length === 0) out.beats = [newBeat(out)];
  if (type === 'quiz_checkpoint' && (!out.reveal_beats || out.reveal_beats.length === 0)) {
    out.reveal_beats = [newBeat(out)];
  }
  return repairSceneRefs(out);
}

const QUIZ_KEYS = [
  'question', 'options', 'correct_index', 'feedback_wrong', 'option_misconception_ids', 'explanation', 'bloom',
  'countdown_seconds', 'reveal_beats', 'source_refs',
];

export const DEFAULT_MANIM_CODE = [
  'class Explainer(AadhiScene):',
  '    def construct(self):',
  '        self.wait_until_beat(0)',
  '        title = Text("Title", font_size=48)',
  '        self.play(Write(title))',
  '        self.finish()',
  '',
].join('\n');

export const DEFAULT_P5_CODE = [
  'function setup() {',
  '  createCanvas(640, 360);',
  '}',
  '',
  'function draw() {',
  '  background(26, 11, 46);',
  '  fill(255, 215, 0);',
  '  circle(mouseX, mouseY, 40);',
  '}',
  '',
].join('\n');

// ---------------------------------------------------------------------------
// Scene-level operations (screenplay in, screenplay out)
// ---------------------------------------------------------------------------

/**
 * Index of a scene by id (-1 if absent).
 * @param {Screenplay} sp
 * @param {string} sceneId
 */
export function sceneIndex(sp, sceneId) {
  return (sp.scenes || []).findIndex((/** @type {Scene} */ s) => s.id === sceneId);
}

/**
 * Replace one scene (by id) using an updater that receives a deep clone.
 * @param {Screenplay} sp
 * @param {string} sceneId
 * @param {(scene: Scene) => Scene | void} fn
 * @returns {Screenplay}
 */
export function updateScene(sp, sceneId, fn) {
  const idx = sceneIndex(sp, sceneId);
  if (idx < 0) return sp;
  const draft = clone(sp.scenes[idx]);
  const result = fn(draft);
  const next = result === undefined ? draft : result;
  const scenes = sp.scenes.slice();
  scenes[idx] = next;
  return { ...sp, scenes };
}

/**
 * Move a scene; chapter scene lists are re-sorted to follow the new scene order.
 * @param {Screenplay} sp
 * @param {number} from
 * @param {number} to
 * @returns {Screenplay}
 */
export function moveScene(sp, from, to) {
  const scenes = moveIndex(sp.scenes || [], from, to);
  return syncChapterOrder({ ...sp, scenes });
}

/**
 * Insert a scene at an index (clamped) and register it in the chapter named by `chapter_id`.
 * @param {Screenplay} sp
 * @param {Scene} scene
 * @param {number} index
 * @returns {Screenplay}
 */
export function insertScene(sp, scene, index) {
  if ((sp.scenes || []).length >= MAX_SCENES) throw new Error(`a lecture can have at most ${MAX_SCENES} scenes`);
  if (sceneIndex(sp, scene.id) >= 0) throw new Error(`scene id ${scene.id} already exists`);
  const scenes = (sp.scenes || []).slice();
  const at = Math.max(0, Math.min(index, scenes.length));
  scenes.splice(at, 0, scene);
  let chapters = sp.chapters || [];
  if (scene.chapter_id && chapters.some((/** @type {any} */ c) => c.id === scene.chapter_id)) {
    chapters = chapters.map((/** @type {any} */ c) =>
      c.id === scene.chapter_id && !c.scene_ids.includes(scene.id) ? { ...c, scene_ids: [...c.scene_ids, scene.id] } : c,
    );
  }
  return syncChapterOrder({ ...sp, scenes, chapters });
}

/**
 * Add a new scene of `type` after `afterIndex` (-1 = at the start). The new scene joins the
 * chapter of its predecessor.
 * @param {Screenplay} sp
 * @param {string} type
 * @param {number} afterIndex
 * @param {{ title?: string, manimTemplate?: { name: string, example_params?: Record<string, any> } | null }} [opts]
 * @returns {{ screenplay: Screenplay, scene: Scene }}
 */
export function addScene(sp, type, afterIndex, opts = {}) {
  if (!(/** @type {readonly string[]} */ (SCENE_TYPES)).includes(type)) throw new Error(`unknown scene type ${type}`);
  const scene = newScene(type, sp, opts);
  const prev = afterIndex >= 0 ? sp.scenes[afterIndex] : null;
  if (prev && prev.chapter_id) scene.chapter_id = prev.chapter_id;
  if (prev && prev.concept_id && isBoardScene(scene)) scene.concept_id = prev.concept_id;
  return { screenplay: insertScene(sp, scene, afterIndex + 1), scene };
}

/**
 * Duplicate a scene right after the original. Beat/item ids are re-issued under the new scene
 * id and every internal reference is rewritten.
 * @param {Screenplay} sp
 * @param {number} index
 * @returns {{ screenplay: Screenplay, scene: Scene }}
 */
export function duplicateScene(sp, index) {
  const original = sp.scenes[index];
  if (!original) throw new Error('no such scene');
  const taken = new Set(sp.scenes.map((/** @type {Scene} */ s) => String(s.id)));
  const newId = uniqueId(`${original.id}-copy`, taken);
  const copy = clone(original);
  copy.id = newId;
  /** @type {Map<string, string>} */
  const itemMap = new Map();
  const itemIds = new Set();
  for (const item of copy.board || []) {
    const nid = sequentialId(`${newId}-i`, itemIds);
    itemIds.add(nid);
    itemMap.set(item.id, nid);
    item.id = nid;
  }
  /** @type {Map<string, string>} */
  const beatMap = new Map();
  const beatIds = new Set();
  for (const { beat } of sceneBeats(copy)) {
    const nid = sequentialId(`${newId}-b`, beatIds);
    beatIds.add(nid);
    beatMap.set(beat.id, nid);
    beat.id = nid;
    if (beat.board_item_id) beat.board_item_id = itemMap.get(beat.board_item_id) || null;
    if (beat.fill_item_id) beat.fill_item_id = itemMap.get(beat.fill_item_id) || null;
    beat.highlight_item_ids = (beat.highlight_item_ids || []).map((/** @type {string} */ h) => itemMap.get(h)).filter(Boolean);
  }
  if (copy.side_panel && copy.side_panel.show_from_beat_id) {
    copy.side_panel.show_from_beat_id = beatMap.get(copy.side_panel.show_from_beat_id) || null;
  }
  for (const item of copy.board || []) {
    for (const v of item.variables || []) {
      if (v && v.beat_id) {
        const mapped = beatMap.get(v.beat_id);
        if (mapped) v.beat_id = mapped;
        else delete v.beat_id;
      }
    }
  }
  if (copy.title) copy.title = truncateText(`${copy.title} (copy)`, 240);
  return { screenplay: insertScene(sp, repairSceneRefs(copy), index + 1), scene: copy };
}

/**
 * Delete a scene and remove it from chapters.
 * @param {Screenplay} sp
 * @param {number} index
 * @returns {Screenplay}
 */
export function deleteScene(sp, index) {
  const scene = sp.scenes[index];
  if (!scene) return sp;
  const scenes = sp.scenes.filter((/** @type {Scene} */ _s, /** @type {number} */ i) => i !== index);
  const chapters = (sp.chapters || []).map((/** @type {any} */ c) => ({
    ...c,
    scene_ids: c.scene_ids.filter((/** @type {string} */ id) => id !== scene.id),
  }));
  return { ...sp, scenes, chapters };
}

/**
 * Change a scene's type, keeping the common fields (and the board between board types).
 * @param {Screenplay} sp
 * @param {string} sceneId
 * @param {string} type
 * @param {{ manimTemplate?: { name: string, example_params?: Record<string, any> } | null }} [opts]
 * @returns {Screenplay}
 */
export function changeSceneType(sp, sceneId, type, opts = {}) {
  if (!(/** @type {readonly string[]} */ (SCENE_TYPES)).includes(type)) throw new Error(`unknown scene type ${type}`);
  return updateScene(sp, sceneId, (scene) => (scene.type === type ? scene : convertSceneShape(scene, type, opts)));
}

/**
 * Re-sort each chapter's scene_ids to follow scene order (unknown ids are dropped).
 * @param {Screenplay} sp
 * @returns {Screenplay}
 */
export function syncChapterOrder(sp) {
  if (!Array.isArray(sp.chapters) || sp.chapters.length === 0) return sp;
  const order = new Map((sp.scenes || []).map((/** @type {Scene} */ s, /** @type {number} */ i) => [s.id, i]));
  const chapters = sp.chapters.map((/** @type {any} */ c) => ({
    ...c,
    scene_ids: c.scene_ids
      .filter((/** @type {string} */ id) => order.has(id))
      .sort((/** @type {string} */ a, /** @type {string} */ b) => /** @type {number} */ (order.get(a)) - /** @type {number} */ (order.get(b))),
  }));
  return { ...sp, chapters };
}

// ---------------------------------------------------------------------------
// Beat operations (scene in, scene out)
// ---------------------------------------------------------------------------

/**
 * @param {Scene} scene
 * @param {BeatPhase} phase
 */
function beatList(scene, phase) {
  return phase === 'reveal' ? scene.reveal_beats || [] : scene.beats || [];
}

/**
 * @param {Scene} scene
 * @param {BeatPhase} phase
 */
export function maxBeats(scene, phase = 'main') {
  if (phase === 'reveal') return MAX_REVEAL_BEATS;
  return scene.type === 'chapter_card' ? MAX_CHAPTER_CARD_BEATS : MAX_BEATS;
}

/**
 * @param {Scene} scene
 * @param {BeatPhase} phase
 */
export function minBeats(scene, phase = 'main') {
  if (phase === 'reveal') return 1;
  return scene.type === 'chapter_card' ? 0 : 1;
}

/** @param {Scene} scene @param {BeatPhase} [phase] */
export function canAddBeat(scene, phase = 'main') {
  return beatList(scene, phase).length < maxBeats(scene, phase);
}

/** @param {Scene} scene @param {BeatPhase} [phase] */
export function canRemoveBeat(scene, phase = 'main') {
  return beatList(scene, phase).length > minBeats(scene, phase);
}

/**
 * Insert a new beat after `afterIndex` (-1 = first).
 * @param {Scene} scene
 * @param {number} afterIndex
 * @param {BeatPhase} [phase]
 * @param {Partial<Beat>} [fields]
 * @returns {{ scene: Scene, beat: Beat }}
 */
export function addBeat(scene, afterIndex, phase = 'main', fields = {}) {
  if (!canAddBeat(scene, phase)) throw new Error(`at most ${maxBeats(scene, phase)} beats`);
  const out = clone(scene);
  const beat = newBeat(out, fields);
  const list = beatList(out, phase).slice();
  list.splice(Math.max(0, Math.min(afterIndex + 1, list.length)), 0, beat);
  if (phase === 'reveal') out.reveal_beats = list;
  else out.beats = list;
  return { scene: repairSceneRefs(out), beat };
}

/**
 * Remove a beat. Items it revealed become visible from scene start; fills/highlights that are
 * no longer valid are cleared; a side panel shown from this beat moves to the beat that takes
 * its place (or the previous one).
 * @param {Scene} scene
 * @param {number} index
 * @param {BeatPhase} [phase]
 * @returns {Scene}
 */
export function removeBeat(scene, index, phase = 'main') {
  const list = beatList(scene, phase);
  if (!list[index]) return scene;
  if (!canRemoveBeat(scene, phase)) throw new Error('a scene needs at least one beat');
  const out = clone(scene);
  const removed = list[index];
  const next = beatList(out, phase).filter((/** @type {Beat} */ _b, /** @type {number} */ i) => i !== index);
  if (phase === 'reveal') out.reveal_beats = next;
  else out.beats = next;
  if (out.side_panel && out.side_panel.show_from_beat_id === removed.id) {
    const replacement = next[index] || next[index - 1] || null;
    out.side_panel = { ...out.side_panel, show_from_beat_id: replacement ? replacement.id : null };
  }
  return repairSceneRefs(out);
}

/**
 * Reorder beats; references that become invalid (fill before reveal, highlight before the item
 * is visible) are cleared.
 * @param {Scene} scene
 * @param {number} from
 * @param {number} to
 * @param {BeatPhase} [phase]
 * @returns {Scene}
 */
export function moveBeat(scene, from, to, phase = 'main') {
  const out = clone(scene);
  const moved = moveIndex(beatList(out, phase), from, to);
  if (phase === 'reveal') out.reveal_beats = moved;
  else out.beats = moved;
  return repairSceneRefs(out);
}

/**
 * Patch fields of one beat (`id` cannot change through this helper).
 * @param {Scene} scene
 * @param {number} index
 * @param {Partial<Beat>} patch
 * @param {BeatPhase} [phase]
 * @returns {Scene}
 */
export function updateBeat(scene, index, patch, phase = 'main') {
  const out = clone(scene);
  const list = beatList(out, phase);
  if (!list[index]) return scene;
  const { id: _ignored, ...rest } = patch;
  list[index] = { ...list[index], ...rest };
  return repairSceneRefs(out);
}

/**
 * Map item id -> index of the main beat that reveals it.
 * @param {Scene} scene
 * @returns {Map<string, number>}
 */
export function revealIndex(scene) {
  /** @type {Map<string, number>} */
  const m = new Map();
  (scene.beats || []).forEach((/** @type {Beat} */ b, /** @type {number} */ i) => {
    if (b.board_item_id && !m.has(b.board_item_id)) m.set(b.board_item_id, i);
  });
  return m;
}

/**
 * Items a beat may reveal: every item not revealed by another beat.
 * @param {Scene} scene
 * @param {number} beatIdx
 * @returns {BoardItem[]}
 */
export function revealOptions(scene, beatIdx) {
  if (!isBoardScene(scene)) return [];
  const owner = new Map();
  (scene.beats || []).forEach((/** @type {Beat} */ b, /** @type {number} */ i) => {
    if (b.board_item_id) owner.set(b.board_item_id, i);
  });
  return (scene.board || []).filter((/** @type {BoardItem} */ it) => !owner.has(it.id) || owner.get(it.id) === beatIdx);
}

/**
 * Blank example steps a beat may fill: revealed at or before the beat (or visible from the
 * start) and not filled by another beat.
 * @param {Scene} scene
 * @param {number} beatIdx
 * @returns {BoardItem[]}
 */
export function fillOptions(scene, beatIdx) {
  if (!isBoardScene(scene)) return [];
  const rev = revealIndex(scene);
  const filledBy = new Map();
  (scene.beats || []).forEach((/** @type {Beat} */ b, /** @type {number} */ i) => {
    if (b.fill_item_id) filledBy.set(b.fill_item_id, i);
  });
  return (scene.board || []).filter((/** @type {BoardItem} */ it) => {
    if (it.kind !== 'example_step' || !it.blank) return false;
    const r = rev.has(it.id) ? /** @type {number} */ (rev.get(it.id)) : -1;
    if (r > beatIdx) return false;
    return !filledBy.has(it.id) || filledBy.get(it.id) === beatIdx;
  });
}

/**
 * Items a beat may highlight: visible before the beat starts (revealed by an earlier beat or
 * never revealed, i.e. visible from scene start).
 * @param {Scene} scene
 * @param {number} beatIdx
 * @returns {BoardItem[]}
 */
export function highlightOptions(scene, beatIdx) {
  if (!isBoardScene(scene)) return [];
  const rev = revealIndex(scene);
  return (scene.board || []).filter((/** @type {BoardItem} */ it) => {
    const r = rev.has(it.id) ? /** @type {number} */ (rev.get(it.id)) : -1;
    return r < beatIdx;
  });
}

/**
 * Set which item a beat reveals; if another beat revealed it, that beat stops revealing it.
 * @param {Scene} scene
 * @param {number} beatIdx
 * @param {string | null} itemId
 * @returns {Scene}
 */
export function setBeatReveal(scene, beatIdx, itemId) {
  if (!isBoardScene(scene) || !scene.beats[beatIdx]) return scene;
  if (itemId && !(scene.board || []).some((/** @type {BoardItem} */ i) => i.id === itemId)) {
    throw new Error(`unknown board item ${itemId}`);
  }
  const out = clone(scene);
  out.beats.forEach((/** @type {Beat} */ b, /** @type {number} */ i) => {
    if (i === beatIdx) b.board_item_id = itemId || null;
    else if (itemId && b.board_item_id === itemId) b.board_item_id = null;
  });
  return repairSceneRefs(out);
}

/**
 * Set which blank step a beat fills; invalid choices throw (UI offers only `fillOptions`).
 * @param {Scene} scene
 * @param {number} beatIdx
 * @param {string | null} itemId
 * @returns {Scene}
 */
export function setBeatFill(scene, beatIdx, itemId) {
  if (!isBoardScene(scene) || !scene.beats[beatIdx]) return scene;
  if (itemId) {
    const ok = fillOptions(scene, beatIdx).some((i) => i.id === itemId);
    if (!ok) throw new Error(`item ${itemId} cannot be filled by this beat`);
  }
  const out = clone(scene);
  out.beats[beatIdx].fill_item_id = itemId || null;
  return repairSceneRefs(out);
}

/**
 * Set a beat's highlights (deduplicated, at most 2, only items visible before the beat).
 * @param {Scene} scene
 * @param {number} beatIdx
 * @param {string[]} ids
 * @returns {Scene}
 */
export function setBeatHighlights(scene, beatIdx, ids) {
  if (!isBoardScene(scene) || !scene.beats[beatIdx]) return scene;
  const allowed = new Set(highlightOptions(scene, beatIdx).map((i) => i.id));
  const clean = [...new Set(ids)].filter((id) => allowed.has(id)).slice(0, MAX_HIGHLIGHTS);
  const out = clone(scene);
  out.beats[beatIdx].highlight_item_ids = clean;
  return out;
}

// ---------------------------------------------------------------------------
// Board item operations (scene in, scene out)
// ---------------------------------------------------------------------------

/**
 * @param {Scene} scene
 * @param {string} kind
 * @param {number} afterIndex  -1 = first
 * @param {Screenplay | null} [sp]
 * @returns {{ scene: Scene, item: BoardItem }}
 */
export function addBoardItem(scene, kind, afterIndex, sp = null) {
  if (!isBoardScene(scene)) throw new Error('only board scenes have board items');
  if ((scene.board || []).length >= MAX_BOARD_ITEMS) throw new Error(`at most ${MAX_BOARD_ITEMS} board items`);
  // A figure item without source figures starts with figure_id null (reported by
  // localProblems) until the teacher uploads one (addFigure).
  const out = clone(scene);
  const item = newBoardItem(kind, out, sp);
  const board = (out.board || []).slice();
  board.splice(Math.max(0, Math.min(afterIndex + 1, board.length)), 0, item);
  out.board = board;
  return { scene: out, item };
}

/**
 * Delete a board item and clear every reference to it (reveal, fill, highlights).
 * @param {Scene} scene
 * @param {string} itemId
 * @returns {Scene}
 */
export function deleteBoardItem(scene, itemId) {
  if (!isBoardScene(scene)) return scene;
  const out = clone(scene);
  out.board = (out.board || []).filter((/** @type {BoardItem} */ i) => i.id !== itemId);
  for (const b of out.beats || []) {
    if (b.board_item_id === itemId) b.board_item_id = null;
    if (b.fill_item_id === itemId) b.fill_item_id = null;
    b.highlight_item_ids = (b.highlight_item_ids || []).filter((/** @type {string} */ h) => h !== itemId);
  }
  return repairSceneRefs(out);
}

/**
 * Reorder board items (layout order; reveal order is set by beats).
 * @param {Scene} scene
 * @param {number} from
 * @param {number} to
 * @returns {Scene}
 */
export function moveBoardItem(scene, from, to) {
  const out = clone(scene);
  out.board = moveIndex(out.board || [], from, to);
  return out;
}

/**
 * Patch a board item's fields (id/kind changes go through the dedicated helpers).
 * @param {Scene} scene
 * @param {string} itemId
 * @param {Partial<BoardItem>} patch
 * @returns {Scene}
 */
export function updateBoardItem(scene, itemId, patch) {
  const out = clone(scene);
  const idx = (out.board || []).findIndex((/** @type {BoardItem} */ i) => i.id === itemId);
  if (idx < 0) return scene;
  const { id: _id, kind: _kind, blank: _blank, ...rest } = patch;
  out.board[idx] = { ...out.board[idx], ...rest };
  return out;
}

/**
 * Change an item's kind, filling required payload defaults. Leaving `example_step` clears
 * `blank` and any beat that filled it.
 * @param {Scene} scene
 * @param {string} itemId
 * @param {string} kind
 * @param {Screenplay | null} [sp]
 * @returns {Scene}
 */
export function setBoardItemKind(scene, itemId, kind, sp = null) {
  if (!(/** @type {readonly string[]} */ (BOARD_ITEM_KINDS)).includes(kind)) throw new Error(`unknown board item kind ${kind}`);
  const out = clone(scene);
  const idx = (out.board || []).findIndex((/** @type {BoardItem} */ i) => i.id === itemId);
  if (idx < 0) return scene;
  out.board[idx] = applyKindDefaults(out.board[idx], kind, sp);
  return repairSceneRefs(out);
}

/**
 * Toggle whether an example step starts blank; un-blanking clears the beat that filled it.
 * @param {Scene} scene
 * @param {string} itemId
 * @param {boolean} blank
 * @returns {Scene}
 */
export function setItemBlank(scene, itemId, blank) {
  const out = clone(scene);
  const item = (out.board || []).find((/** @type {BoardItem} */ i) => i.id === itemId);
  if (!item) return scene;
  if (blank && item.kind !== 'example_step') throw new Error('only example steps can be blank');
  item.blank = !!blank;
  return repairSceneRefs(out);
}

// ---------------------------------------------------------------------------
// Quiz helpers
// ---------------------------------------------------------------------------

/**
 * @param {Scene} scene
 * @returns {Scene}
 */
export function addQuizOption(scene) {
  if (scene.type !== 'quiz_checkpoint') return scene;
  if (scene.options.length >= MAX_QUIZ_OPTIONS) throw new Error(`at most ${MAX_QUIZ_OPTIONS} options`);
  const out = clone(scene);
  out.options.push('');
  out.feedback_wrong = [...alignArray(out.feedback_wrong, out.options.length - 1, ''), ''];
  out.option_misconception_ids = [...alignArray(out.option_misconception_ids, out.options.length - 1, null), null];
  return repairSceneRefs(out);
}

/**
 * Remove an option keeping `correct_index` on the same answer (or 0 if it was removed).
 * @param {Scene} scene
 * @param {number} index
 * @returns {Scene}
 */
export function removeQuizOption(scene, index) {
  if (scene.type !== 'quiz_checkpoint') return scene;
  if (scene.options.length <= MIN_QUIZ_OPTIONS) throw new Error(`a quiz needs at least ${MIN_QUIZ_OPTIONS} options`);
  const out = clone(scene);
  const n = out.options.length;
  const fb = alignArray(out.feedback_wrong, n, '');
  const mis = alignArray(out.option_misconception_ids, n, null);
  out.options.splice(index, 1);
  fb.splice(index, 1);
  mis.splice(index, 1);
  out.feedback_wrong = fb;
  out.option_misconception_ids = mis;
  if (out.correct_index === index) out.correct_index = 0;
  else if (out.correct_index > index) out.correct_index -= 1;
  return repairSceneRefs(out);
}

/**
 * Move a quiz option (feedback/misconception/correct index follow it).
 * @param {Scene} scene
 * @param {number} from
 * @param {number} to
 * @returns {Scene}
 */
export function moveQuizOption(scene, from, to) {
  if (scene.type !== 'quiz_checkpoint') return scene;
  const out = clone(scene);
  const n = out.options.length;
  const order = moveIndex([...Array(n).keys()], from, to);
  const fb = alignArray(out.feedback_wrong, n, '');
  const mis = alignArray(out.option_misconception_ids, n, null);
  out.options = order.map((i) => scene.options[i]);
  out.feedback_wrong = order.map((i) => fb[i]);
  out.option_misconception_ids = order.map((i) => mis[i]);
  out.correct_index = order.indexOf(scene.correct_index);
  return repairSceneRefs(out);
}

/**
 * @template T
 * @param {T[] | null | undefined} arr
 * @param {number} n
 * @param {T} fill
 * @returns {T[]}
 */
function alignArray(arr, n, fill) {
  const out = Array.isArray(arr) ? arr.slice(0, n) : [];
  while (out.length < n) out.push(fill);
  return out;
}

// ---------------------------------------------------------------------------
// Invariant repair
// ---------------------------------------------------------------------------

/**
 * Return a copy of `scene` with every cross-reference made valid (see module docs).
 * Idempotent; used after each structural edit and after merges.
 * @param {Scene} scene
 * @returns {Scene}
 */
export function repairSceneRefs(scene) {
  const out = { ...scene };
  out.beats = (scene.beats || []).map((/** @type {Beat} */ b) => ({ ...b, highlight_item_ids: [...(b.highlight_item_ids || [])] }));
  if (Array.isArray(scene.reveal_beats)) {
    out.reveal_beats = scene.reveal_beats.map((/** @type {Beat} */ b) => ({
      ...b,
      board_item_id: null,
      fill_item_id: null,
      highlight_item_ids: [],
    }));
  }
  if (!isBoardScene(out)) {
    for (const b of out.beats) {
      b.board_item_id = null;
      b.fill_item_id = null;
      b.highlight_item_ids = [];
    }
  } else {
    out.board = (scene.board || []).map((/** @type {BoardItem} */ i) =>
      i.kind !== 'example_step' && i.blank ? { ...i, blank: false } : i,
    );
    // A legend row's beat (FormulaVariable.beat_id) that is no longer a beat of the scene: automatic again.
    const beatIds = new Set(out.beats.map((/** @type {Beat} */ b) => b.id));
    const dangling = (/** @type {any} */ v) => !!(v && v.beat_id && !beatIds.has(v.beat_id));
    out.board = out.board.map((/** @type {BoardItem} */ i) =>
      Array.isArray(i.variables) && i.variables.some(dangling)
        ? {
            ...i,
            variables: i.variables.map((/** @type {any} */ v) => {
              if (!dangling(v)) return v;
              const { beat_id: _gone, ...rest } = v;
              return rest;
            }),
          }
        : i,
    );
    const items = new Map(out.board.map((/** @type {BoardItem} */ i) => [i.id, i]));
    /** @type {Map<string, number>} */
    const revealedAt = new Map();
    out.beats.forEach((/** @type {Beat} */ b, /** @type {number} */ idx) => {
      if (b.board_item_id && (!items.has(b.board_item_id) || revealedAt.has(b.board_item_id))) b.board_item_id = null;
      if (b.board_item_id) revealedAt.set(b.board_item_id, idx);
    });
    const filled = new Set();
    out.beats.forEach((/** @type {Beat} */ b, /** @type {number} */ idx) => {
      if (b.fill_item_id) {
        const item = items.get(b.fill_item_id);
        const r = revealedAt.has(b.fill_item_id) ? /** @type {number} */ (revealedAt.get(b.fill_item_id)) : -1;
        const ok = item && item.kind === 'example_step' && item.blank && !filled.has(b.fill_item_id) && r <= idx;
        if (ok) filled.add(b.fill_item_id);
        else b.fill_item_id = null;
      }
      const seen = new Set();
      b.highlight_item_ids = b.highlight_item_ids.filter((/** @type {string} */ h) => {
        if (!items.has(h) || seen.has(h)) return false;
        const r = revealedAt.has(h) ? /** @type {number} */ (revealedAt.get(h)) : -1;
        if (r >= idx) return false;
        seen.add(h);
        return true;
      }).slice(0, MAX_HIGHLIGHTS);
    });
  }
  if (out.side_panel && out.side_panel.show_from_beat_id) {
    const ids = new Set(sceneBeats(out).map((x) => x.beat.id));
    if (!ids.has(out.side_panel.show_from_beat_id)) out.side_panel = { ...out.side_panel, show_from_beat_id: null };
  }
  if (out.type === 'quiz_checkpoint' && Array.isArray(out.options)) {
    const n = out.options.length;
    out.feedback_wrong = alignArray(out.feedback_wrong, n, '');
    out.option_misconception_ids = alignArray(out.option_misconception_ids, n, null);
    if (!Number.isInteger(out.correct_index) || out.correct_index < 0 || out.correct_index >= n) out.correct_index = 0;
    out.feedback_wrong = out.feedback_wrong.map((/** @type {string} */ f, /** @type {number} */ i) => (i === out.correct_index ? '' : f));
  }
  return out;
}

/**
 * Drop cross-references to things that no longer exist at screenplay level (after merges or
 * deletions): chapter scene lists, scene concept/chapter/objective ids, quiz misconception ids,
 * side-panel/board figure ids are left alone when no figure list exists.
 * @param {Screenplay} sp
 * @returns {Screenplay}
 */
export function sanitizeScreenplayRefs(sp) {
  const sceneIds = new Set((sp.scenes || []).map((/** @type {Scene} */ s) => s.id));
  const concepts = new Set((sp.concept_map || []).map((/** @type {any} */ c) => c.id));
  const chapters = new Set((sp.chapters || []).map((/** @type {any} */ c) => c.id));
  const objectives = new Set((sp.learning_objectives || []).map((/** @type {any} */ o) => o.id));
  const miscs = new Set((sp.misconceptions || []).map((/** @type {any} */ m) => m.id));
  const scenes = (sp.scenes || []).map((/** @type {Scene} */ s) => {
    const out = { ...s };
    if (concepts.size && out.concept_id && !concepts.has(out.concept_id)) out.concept_id = null;
    if (chapters.size && out.chapter_id && !chapters.has(out.chapter_id)) out.chapter_id = null;
    if (objectives.size) out.objective_ids = (out.objective_ids || []).filter((/** @type {string} */ o) => objectives.has(o));
    if (out.type === 'quiz_checkpoint' && miscs.size) {
      out.option_misconception_ids = (out.option_misconception_ids || []).map((/** @type {string | null} */ m) => (m && miscs.has(m) ? m : null));
    }
    return repairSceneRefs(out);
  });
  const chapterList = (sp.chapters || []).map((/** @type {any} */ c) => ({
    ...c,
    scene_ids: (c.scene_ids || []).filter((/** @type {string} */ id) => sceneIds.has(id)),
  }));
  const conceptList = (sp.concept_map || []).map((/** @type {any} */ c) => ({
    ...c,
    depends_on: (c.depends_on || []).filter((/** @type {string} */ d) => concepts.has(d)),
  }));
  return { ...sp, scenes, chapters: chapterList, concept_map: conceptList };
}

/**
 * Move a scene to another chapter (or none): updates `scene.chapter_id` and the chapter
 * scene lists together.
 * @param {Screenplay} sp
 * @param {string} sceneId
 * @param {string | null} chapterId
 * @returns {Screenplay}
 */
export function setSceneChapter(sp, sceneId, chapterId) {
  if (chapterId && !(sp.chapters || []).some((/** @type {any} */ c) => c.id === chapterId)) throw new Error(`unknown chapter ${chapterId}`);
  const next = updateScene(sp, sceneId, (s) => ({ ...s, chapter_id: chapterId || null }));
  const chapters = (next.chapters || []).map((/** @type {any} */ c) => {
    const without = c.scene_ids.filter((/** @type {string} */ id) => id !== sceneId);
    return { ...c, scene_ids: c.id === chapterId ? [...without, sceneId] : without };
  });
  return syncChapterOrder({ ...next, chapters });
}

/**
 * Register an uploaded image as a source figure (board `figure` items and figure panels
 * reference figures by id).
 * @param {Screenplay} sp
 * @param {{ asset_key: string, caption?: string, width?: number | null, height?: number | null }} info
 * @returns {{ screenplay: Screenplay, figureId: string }}
 */
export function addFigure(sp, info) {
  const taken = (sp.figures || []).map((/** @type {any} */ f) => f.id);
  const figureId = sequentialId('fig-up', taken);
  const figure = { id: figureId, caption: info.caption || '', page: null, asset_key: info.asset_key, width: info.width ?? null, height: info.height ?? null };
  return { screenplay: { ...sp, figures: [...(sp.figures || []), figure] }, figureId };
}

/**
 * Minimal valid side panel of a kind (keeps title/rationale/show_from when switching kinds).
 * @param {string | null} kind   null removes the panel
 * @param {Record<string, any> | null} previous
 * @param {Screenplay | null} sp
 * @param {{ name: string, example_params?: Record<string, any> } | null} [manimTemplate]
 * @returns {Record<string, any> | null}
 */
export function sidePanelOfKind(kind, previous, sp, manimTemplate = null) {
  if (!kind) return null;
  const keep = previous ? { title: previous.title ?? null, rationale: previous.rationale || '', show_from_beat_id: previous.show_from_beat_id ?? null } : { title: null, rationale: '', show_from_beat_id: null };
  /** @type {Record<string, any>} */
  const panel = {
    kind,
    ...keep,
    figure_id: null,
    image_prompt: null,
    chart: null,
    graph: null,
    model_3d: null,
    manim: null,
    terminal: null,
    quiz: null,
    gif_query: null,
    override_asset_key: null,
  };
  if (previous && previous.kind === kind) return { ...panel, ...previous };
  switch (kind) {
    case 'figure':
      panel.figure_id = sp && sp.figures && sp.figures.length ? sp.figures[0].id : null;
      break;
    case 'image':
      panel.image_prompt = '';
      break;
    case 'chart':
      panel.chart = { chart_type: 'bar', labels: ['A', 'B'], datasets: [{ label: 'Series 1', data: [1, 2] }], x_label: null, y_label: null };
      break;
    case 'graph':
      panel.graph = { functions: [{ expr: 'x^2', label: null }], points: [], x_range: [-5, 5], y_range: null, x_label: null, y_label: null };
      break;
    case 'model_3d':
      panel.model_3d = { primitives: [{ shape: 'sphere', position: [0, 0, 0], size: [1], color: '#b026ff', label: null }], auto_rotate: true };
      break;
    case 'manim':
      panel.manim = manimTemplate
        ? { template: manimTemplate.name, params: clone(manimTemplate.example_params || {}), code: null }
        : { template: null, params: {}, code: DEFAULT_MANIM_CODE };
      break;
    case 'terminal':
      panel.terminal = { command: '', output: '' };
      break;
    case 'quiz':
      panel.quiz = { question: '', options: ['', ''], correct_index: 0 };
      break;
    case 'gif':
      panel.gif_query = '';
      break;
    default:
      break;
  }
  return panel;
}

/** Size components per 3D primitive shape (Primitive3D.size semantics). */
export const PRIMITIVE_SIZE_LABELS = /** @type {Record<string, string[]>} */ ({
  sphere: ['Radius'],
  box: ['Width', 'Height', 'Depth'],
  cylinder: ['Radius', 'Height'],
  cone: ['Radius', 'Height'],
  torus: ['Ring radius', 'Tube radius'],
  arrow: ['dx', 'dy', 'dz'],
});

/**
 * Find a scene by id.
 * @param {Screenplay | null} sp
 * @param {string | null} sceneId
 * @returns {Scene | null}
 */
export function findScene(sp, sceneId) {
  if (!sp || !sceneId) return null;
  return (sp.scenes || []).find((/** @type {Scene} */ s) => s.id === sceneId) || null;
}
