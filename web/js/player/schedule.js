// @ts-check
/**
 * Pure scheduling: everything visible in a scene is a function of (scene, t), where t is the
 * scene-relative time in seconds. Live playback, seeking and render mode all go through
 * sceneStateAt(), and stateTimes() lists every t at which its output can change, so render mode
 * can screenshot exactly the distinct visual states.
 *
 * Phases:
 *   lead       before the first beat starts (or before audio_offset for silent scenes)
 *   beat       a beat's narration is being spoken: [start, speech_end)
 *   pause      a beat's pause_after / the inter-beat gap, or a gap between beat groups
 *   countdown  quiz: [countdown_start, reveal_start)
 *   reveal     quiz: from reveal_start until the last reveal beat ends
 *   tail       after the last beat ended (breathing room before the next scene)
 *
 * No DOM access; safe to import from Node tests.
 */

import { clamp, fnv1a, num, roundTo } from '../shared/format.js';
import { mascotStateAt, mascotStateTimes } from './mascot-state.js';

/** @typedef {import('../shared/types.js').TimedScene} TimedScene */
/** @typedef {import('../shared/types.js').TimedBeat} TimedBeat */
/** @typedef {import('../shared/types.js').CaptionCue} CaptionCue */
/** @typedef {import('../shared/types.js').SceneState} SceneState */
/** @typedef {import('../shared/types.js').Timeline} Timeline */
/** @typedef {import('../shared/types.js').IntroSpec} IntroSpec */

/** Tolerance for float time comparisons that must round toward the boundary (countdown seconds). */
const EPS = 1e-6;

/**
 * @typedef {object} TerminalPlan
 * @property {string[]} lines
 * @property {number[]} times   scene-relative reveal time of each line
 */

/**
 * Derived, per-scene lookup data (cached by scene object identity; timelines are immutable).
 * @typedef {object} ScenePlan
 * @property {TimedBeat[]} beats
 * @property {number[]} order          beat array indices sorted by start
 * @property {number[]} slotEnd        per beat array index: end of the slot in which it is current
 * @property {Map<string, number>} revealAt
 * @property {Map<string, number>} fillAt
 * @property {CaptionCue[]} cues       sorted by start
 * @property {number} narrationStart
 * @property {number} narrationEnd
 * @property {number | null} revealEnd quiz: end of the last reveal beat
 * @property {number | null} panelShowAt
 * @property {TerminalPlan | null} terminal
 * @property {string[]} itemIds
 * @property {SyncPlan} sync           word-anchored moments
 */

/** @type {WeakMap<object, ScenePlan>} */
const plans = new WeakMap();

/**
 * Return the cue active at t (start <= t < end), or null. `cues` must be sorted by start.
 * @param {CaptionCue[] | null | undefined} cues
 * @param {number} t
 * @returns {CaptionCue | null}
 */
export function selectCue(cues, t) {
  if (!cues || cues.length === 0) return null;
  let lo = 0;
  let hi = cues.length - 1;
  let idx = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (cues[mid].start <= t) {
      idx = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  // Cues should not overlap; tolerate a short overlap by checking a few predecessors.
  for (let i = idx; i >= 0 && i >= idx - 3; i--) {
    if (t < cues[i].end) return cues[i];
  }
  return null;
}

/** At most this many terminal output lines are shown (same cap as panels/timing.js). */
export const TERMINAL_MAX_LINES = 200;

/**
 * Split terminal output into display lines (CRLF tolerant, trailing blank lines dropped, capped at
 * TERMINAL_MAX_LINES) — identical to the terminal panel's own split.
 * @param {string | null | undefined} output
 * @returns {string[]}
 */
export function terminalOutputLines(output) {
  const lines = String(output || '').replace(/\r\n?/g, '\n').split('\n');
  while (lines.length && lines[lines.length - 1].trim() === '') lines.pop();
  return lines.slice(0, TERMINAL_MAX_LINES);
}

/**
 * End of a scene's content (scene-relative): its duration without the hold the teacher's minimum duration added
 * (TimedScene.hold_seconds, aadhi/compose/timeline.py), during which nothing new appears. Terminal lines (here and
 * in panels/timing.js) are spread up to this time, so a hold never changes them. The duration itself without a hold.
 * Rounded to the millisecond like every stored time, so it equals the duration the scene has without the hold.
 * @param {{ duration?: number, hold_seconds?: number }} scene  TimedScene
 * @returns {number}
 */
export function contentEnd(scene) {
  const duration = num(scene.duration, 0);
  const hold = num(scene.hold_seconds, 0);
  return hold > 0 ? Math.max(0, roundTo(duration - hold)) : duration;
}

/**
 * Reveal times of terminal lines: evenly spaced across [show_at, duration], the first line one
 * step after the panel appears and the last one step before the scene ends. When the narration
 * says what the program prints (`outputAt`, the scene's 'output' sync cue), the first line appears
 * at max(show_at, outputAt) and the others follow evenly, the last one step before the scene ends.
 * Same formula as panels/timing.js terminalLineTimes.
 * @param {number} lineCount
 * @param {number} showAt
 * @param {number} duration
 * @param {number | null} [outputAt]
 * @returns {number[]}
 */
export function terminalLineTimes(lineCount, showAt, duration, outputAt = null) {
  const start = Math.max(0, showAt);
  /** @type {number[]} */
  const times = [];
  if (typeof outputAt === 'number' && Number.isFinite(outputAt)) {
    const from = Math.max(start, outputAt);
    const step = Math.max(0, duration - from) / Math.max(1, lineCount);
    for (let k = 0; k < lineCount; k++) times.push(from + k * step);
    return times;
  }
  const span = Math.max(0, duration - start);
  for (let k = 0; k < lineCount; k++) times.push(start + ((k + 1) * span) / (lineCount + 1));
  return times;
}

/**
 * Word-anchored moments of a scene (TimedScene.sync_cues, computed by aadhi/compose/sync.py),
 * indexed for sceneStateAt. Malformed cues are skipped.
 * @typedef {object} SyncPlan
 * @property {Map<string, number>} partAt   `${item}|var:<n>` -> time the legend row appears
 * @property {{ item: string, part: string | null, start: number, end: number }[]} emphasis
 * @property {Set<string>} anchored         `${beat_id}|${item}`: authored highlights replaced by cues
 * @property {{ start: number, end: number }[]} focus
 * @property {number | null} outputAt       terminal output starts here (null: spread over the scene)
 * @property {number[]} times               every cue boundary
 */

/**
 * @param {TimedScene} scene
 * @returns {SyncPlan}
 */
export function syncPlan(scene) {
  /** @type {SyncPlan} */
  const out = { partAt: new Map(), emphasis: [], anchored: new Set(), focus: [], outputAt: null, times: [] };
  const cues = Array.isArray(scene.sync_cues) ? scene.sync_cues : [];
  for (const c of cues) {
    const start = c ? Number(c.start) : NaN;
    if (!Number.isFinite(start) || start < 0) continue;
    const end = c.end === null || c.end === undefined ? Infinity : Number(c.end);
    if (Number.isNaN(end) || end <= start) continue;
    const item = typeof c.item_id === 'string' && c.item_id ? c.item_id : null;
    const part = typeof c.part === 'string' && c.part ? c.part : null;
    if (c.kind === 'var' && item && part) {
      const key = `${item}|${part}`;
      const cur = out.partAt.get(key);
      if (cur === undefined || start < cur) out.partAt.set(key, start);
      out.times.push(start);
    } else if (c.kind === 'emphasis' && item) {
      out.emphasis.push({ item, part, start, end });
      if (typeof c.beat_id === 'string' && c.beat_id) out.anchored.add(`${c.beat_id}|${item}`);
      out.times.push(start, end);
    } else if (c.kind === 'output') {
      if (out.outputAt === null || start < out.outputAt) out.outputAt = start;
    } else if (c.kind === 'focus') {
      out.focus.push({ start, end });
      out.times.push(start, end);
    }
  }
  return out;
}

/**
 * @param {TimedScene} scene
 * @returns {ScenePlan}
 */
export function planScene(scene) {
  const cached = plans.get(scene);
  if (cached) return cached;
  const beats = Array.isArray(scene.beats) ? scene.beats : [];
  const order = beats.map((_, i) => i).sort((a, b) => beats[a].start - beats[b].start || a - b);
  const isQuiz = !!scene.quiz;
  /** @param {TimedBeat} b */
  const group = (b) => (isQuiz && b.phase === 'reveal' ? 'reveal' : 'main');
  /** @type {number[]} */
  const slotEnd = new Array(beats.length).fill(0);
  for (let k = 0; k < order.length; k++) {
    const b = beats[order[k]];
    const next = k + 1 < order.length ? beats[order[k + 1]] : null;
    slotEnd[order[k]] = next && group(next) === group(b) ? Math.max(b.start, next.start) : b.end;
  }
  /** @type {Map<string, number>} */
  const revealAt = new Map();
  /** @type {Map<string, number>} */
  const fillAt = new Map();
  /** @type {CaptionCue[]} */
  const cues = [];
  /** @param {Map<string, number>} map @param {string} id @param {number} at */
  const keepEarliest = (map, id, at) => {
    const cur = map.get(id);
    if (cur === undefined || at < cur) map.set(id, at);
  };
  for (const b of beats) {
    if (b.board_item_id) keepEarliest(revealAt, b.board_item_id, b.start);
    if (b.fill_item_id) keepEarliest(fillAt, b.fill_item_id, b.start);
    for (const c of b.captions || []) cues.push(c);
  }
  cues.sort((a, b) => a.start - b.start || a.end - b.end);
  const offset = num(scene.audio_offset, 0);
  const narrationStart = beats.length ? Math.min(...beats.map((b) => b.start)) : offset;
  const narrationEnd = beats.length ? Math.max(...beats.map((b) => b.end)) : offset;
  const revealBeats = isQuiz ? beats.filter((b) => b.phase === 'reveal') : [];
  const revealEnd = revealBeats.length ? Math.max(...revealBeats.map((b) => b.end)) : null;
  const sp = scene.side_panel;
  const showPanel = !!sp && !!sp.panel && scene.layout?.show_side_panel !== false;
  const panelShowAt = showPanel && sp ? Math.max(0, num(sp.show_at, 0)) : null;
  const sync = syncPlan(scene);
  /** @type {TerminalPlan | null} */
  let terminal = null;
  if (showPanel && sp && sp.panel.kind === 'terminal' && sp.panel.terminal) {
    const lines = terminalOutputLines(sp.panel.terminal.output);
    terminal = { lines, times: terminalLineTimes(lines.length, num(panelShowAt, 0), contentEnd(scene), sync.outputAt) };
  }
  const plan = {
    beats,
    order,
    slotEnd,
    revealAt,
    fillAt,
    cues,
    narrationStart,
    narrationEnd,
    revealEnd,
    panelShowAt,
    terminal,
    itemIds: (scene.board || []).map((i) => i.id),
    sync,
  };
  plans.set(scene, plan);
  return plan;
}

/**
 * Whole countdown seconds of a quiz (>= 0).
 * @param {import('../shared/types.js').QuizTiming} quiz
 */
function countdownSeconds(quiz) {
  return Math.max(0, Math.round(num(quiz.countdown_seconds, 0)));
}

/**
 * Index (into scene.beats) of the beat whose slot contains t, or -1.
 * @param {ScenePlan} plan
 * @param {number} t
 */
function currentBeat(plan, t) {
  for (let k = plan.order.length - 1; k >= 0; k--) {
    const i = plan.order[k];
    if (plan.beats[i].start <= t) return t < plan.slotEnd[i] ? i : -1;
  }
  return -1;
}

/**
 * The complete visual/narration state of a scene at scene-relative time t.
 * @param {TimedScene} scene
 * @param {number} t   scene-relative seconds
 * @returns {SceneState}
 */
export function sceneStateAt(scene, t) {
  const plan = planScene(scene);
  const quiz = scene.quiz || null;
  let beatIndex = currentBeat(plan, t);
  /** @type {import('../shared/types.js').ScenePhase} */
  let phase;
  if (quiz && t >= quiz.reveal_start) {
    phase = plan.revealEnd !== null && t >= plan.revealEnd ? 'tail' : 'reveal';
    if (beatIndex >= 0 && plan.beats[beatIndex].phase !== 'reveal') beatIndex = -1;
    if (phase === 'tail') beatIndex = -1;
  } else if (quiz && t >= quiz.countdown_start) {
    phase = 'countdown';
    beatIndex = -1;
  } else if (beatIndex >= 0) {
    phase = t < plan.beats[beatIndex].speech_end ? 'beat' : 'pause';
  } else if (t < plan.narrationStart) {
    phase = 'lead';
  } else if (t < plan.narrationEnd) {
    phase = 'pause';
  } else {
    phase = 'tail';
  }

  /** @type {Set<string>} */
  const visible = new Set();
  for (const id of plan.itemIds) {
    const at = plan.revealAt.get(id);
    if (at === undefined || at <= t) visible.add(id);
  }
  /** @type {Set<string>} */
  const filled = new Set();
  for (const [id, at] of plan.fillAt) if (at <= t && visible.has(id)) filled.add(id);
  /** @type {Set<string>} */
  const highlight = new Set();
  const sync = plan.sync;
  for (const b of plan.beats) {
    if (b.start <= t && t < b.end) {
      for (const id of b.highlight_item_ids || []) {
        // a word-anchored highlight (sync cue) replaces the beat-long one of that item
        if (visible.has(id) && !sync.anchored.has(`${b.beat_id}|${id}`)) highlight.add(id);
      }
    }
  }
  /** @type {Set<string>} */
  const emphasisParts = new Set();
  for (const e of sync.emphasis) {
    if (e.start <= t && t < e.end && visible.has(e.item)) {
      highlight.add(e.item);
      if (e.part) emphasisParts.add(`${e.item}|${e.part}`);
    }
  }
  /** @type {Set<string>} */
  const pendingParts = new Set();
  for (const [key, at] of sync.partAt) {
    if (t < at && visible.has(key.slice(0, key.indexOf('|')))) pendingParts.add(key);
  }
  let activeItemId = null;
  if (beatIndex >= 0) {
    const b = plan.beats[beatIndex];
    const id = b.board_item_id || b.fill_item_id || null;
    activeItemId = id && visible.has(id) ? id : null;
  }
  const cue = selectCue(plan.cues, t);
  let countdownRemaining = null;
  if (quiz && phase === 'countdown') {
    // Compare against the exact boundaries stateTimes() emits (countdown_start + k), no epsilon.
    const secs = countdownSeconds(quiz);
    let elapsed = 0;
    while (elapsed < secs && quiz.countdown_start + (elapsed + 1) <= t) elapsed++;
    countdownRemaining = clamp(secs - elapsed, 0, secs);
  }
  const panelVisible = plan.panelShowAt !== null && t >= plan.panelShowAt;
  /** @type {string[]} */
  let revealedLines = [];
  if (plan.terminal && panelVisible) {
    let n = 0;
    while (n < plan.terminal.times.length && plan.terminal.times[n] <= t) n++;
    revealedLines = plan.terminal.lines.slice(0, n);
  }
  const panelFocus = panelVisible && sync.focus.some((f) => f.start <= t && t < f.end);
  const mascot = mascotStateAt(scene, plan, t, phase, beatIndex, activeItemId); // Aadhi's state + cue bubble
  return {
    phase,
    beatIndex,
    visibleItemIds: visible,
    filledItemIds: filled,
    highlightItemIds: highlight,
    activeItemId,
    caption: cue ? cue.text : null,
    countdownRemaining,
    quizRevealed: !!quiz && t >= quiz.reveal_start,
    panelVisible,
    terminalLines: revealedLines,
    mascotState: mascot.state,
    mascotCue: mascot.cue,
    pendingParts,
    emphasisParts,
    panelFocus,
  };
}

/**
 * Sorted, de-duplicated scene-relative times in [0, duration) at which sceneStateAt() output can
 * change: scene start, beat start/speech_end/end/slot ends (reveals, fills, highlights, active
 * item), caption cue boundaries, phase boundaries, quiz countdown seconds and reveal, panel
 * show_at, terminal lines and the word-anchored sync cues (legend rows, emphasis, panel focus).
 * @param {TimedScene} scene
 * @returns {number[]}
 */
export function stateTimes(scene) {
  const plan = planScene(scene);
  const duration = num(scene.duration, 0);
  /** @type {number[]} */
  const raw = [0, plan.narrationStart, plan.narrationEnd];
  plan.beats.forEach((b, i) => raw.push(b.start, b.speech_end, b.end, plan.slotEnd[i]));
  for (const c of plan.cues) raw.push(c.start, c.end);
  const quiz = scene.quiz;
  if (quiz) {
    const secs = countdownSeconds(quiz);
    for (let k = 0; k <= secs; k++) raw.push(quiz.countdown_start + k);
    raw.push(quiz.reveal_start);
    if (plan.revealEnd !== null) raw.push(plan.revealEnd);
  }
  if (plan.panelShowAt !== null) raw.push(plan.panelShowAt);
  if (plan.terminal) raw.push(...plan.terminal.times);
  raw.push(...plan.sync.times); // word-anchored legend rows, emphasis and panel focus
  raw.push(...mascotStateTimes(scene, plan)); // thinking starts inside long pauses (cue bubble)
  const times = raw.filter((x) => Number.isFinite(x) && x >= 0 && x < duration).sort((a, b) => a - b);
  /** @type {number[]} */
  const out = [];
  for (const x of times) if (out.length === 0 || x - out[out.length - 1] > 1e-9) out.push(x);
  if (out.length === 0 || out[0] !== 0) out.unshift(0);
  return out;
}

/**
 * Stable key of the *visual* part of a state (what a render-mode screenshot shows). Phase and
 * captions are excluded (captions are hidden in render mode); the scene index is a prefix so keys
 * never collide across scenes.
 * @param {TimedScene} scene
 * @param {SceneState} state
 * @param {number | null} [extraSegment]  index of the extra (panel-internal) time segment, if any
 * @returns {string}
 */
export function stateKey(scene, state, extraSegment = null) {
  const sorted = (/** @type {Set<string>} */ s) => [...s].sort().join(',');
  const parts = [
    scene.scene_id,
    `v:${sorted(state.visibleItemIds)}`,
    `f:${sorted(state.filledItemIds)}`,
    `h:${sorted(state.highlightItemIds)}`,
    `a:${state.activeItemId ?? ''}`,
    `c:${state.countdownRemaining ?? ''}`,
    `r:${state.quizRevealed ? 1 : 0}`,
    `p:${state.panelVisible ? 1 : 0}`,
    `l:${state.terminalLines.length}`,
  ];
  // the cue bubble beside Aadhi is part of the screenshot; appended only when shown, so keys of states
  // without a bubble are unchanged
  if (state.mascotCue) parts.push(`m:${state.mascotCue}`);
  // word-anchored sync parts: appended only when present, so keys of scenes without cues are unchanged
  if (state.pendingParts && state.pendingParts.size) parts.push(`vp:${sorted(state.pendingParts)}`);
  if (state.emphasisParts && state.emphasisParts.size) parts.push(`e:${sorted(state.emphasisParts)}`);
  if (state.panelFocus) parts.push('pf:1');
  if (extraSegment !== null && extraSegment !== undefined) parts.push(`x:${extraSegment}`);
  return `s${num(scene.index, 0)}-${fnv1a(parts.join('|'))}`;
}

/**
 * Normalise extra state times (e.g. panels' panelStateTimes): finite, inside [0, duration), sorted.
 * @param {TimedScene} scene
 * @param {number[] | null | undefined} extraTimes
 * @returns {number[]}
 */
export function normalizeExtraTimes(scene, extraTimes) {
  const duration = num(scene.duration, 0);
  const xs = (extraTimes || []).filter((x) => typeof x === 'number' && Number.isFinite(x) && x >= 0 && x < duration);
  return [...new Set(xs)].sort((a, b) => a - b);
}

/**
 * Number of sorted times <= t (exact comparison: the same floats are used as state times).
 * @param {number[]} times
 * @param {number} t
 */
function countAtOrBefore(times, t) {
  let n = 0;
  while (n < times.length && times[n] <= t) n++;
  return n;
}

/**
 * Render key at scene time t, including the segment of the extra (panel) times.
 * @param {TimedScene} scene
 * @param {number} t
 * @param {number[]} [extraTimes]   already normalised (normalizeExtraTimes)
 * @returns {string}
 */
export function renderKeyAt(scene, t, extraTimes = []) {
  const state = sceneStateAt(scene, t);
  return stateKey(scene, state, extraTimes.length ? countAtOrBefore(extraTimes, t) : null);
}

/**
 * Render-mode state list of a scene: [{t, key}] at stateTimes() (plus `extraTimes`, e.g. the side
 * panel's own state changes), merging consecutive times whose visual key is identical.
 * @param {TimedScene} scene
 * @param {number[] | null} [extraTimes]
 * @returns {{t: number, key: string}[]}
 */
export function renderStateList(scene, extraTimes = null) {
  const extra = normalizeExtraTimes(scene, extraTimes);
  const times = [...new Set([...stateTimes(scene), ...extra])].sort((a, b) => a - b);
  /** @type {{t: number, key: string}[]} */
  const out = [];
  for (const t of times) {
    const key = renderKeyAt(scene, t, extra);
    if (out.length === 0 || out[out.length - 1].key !== key) out.push({ t, key });
  }
  return out;
}

/**
 * Index of the scene playing at absolute time t; -1 during the intro (or with no scenes).
 * Times past the end map to the last scene.
 * @param {Timeline} timeline
 * @param {number} t
 * @returns {number}
 */
export function sceneIndexAt(timeline, t) {
  const scenes = timeline.scenes || [];
  if (scenes.length === 0 || t < scenes[0].start) return -1;
  let lo = 0;
  let hi = scenes.length - 1;
  let idx = 0;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (scenes[mid].start <= t) {
      idx = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return idx;
}

/**
 * Index of the intro title card shown at absolute time t, or -1 (logo phase / outside cards).
 * @param {IntroSpec | null | undefined} intro
 * @param {number} t
 * @returns {number}
 */
export function introCardAt(intro, t) {
  const cards = intro?.cards || [];
  for (let i = 0; i < cards.length; i++) {
    if (cards[i].start <= t && t < cards[i].start + cards[i].duration) return i;
  }
  return -1;
}

/**
 * Index of the chapter containing absolute time t, or -1 before the first chapter.
 * @param {import('../shared/types.js').Chapter[] | null | undefined} chapters
 * @param {number} t
 */
export function chapterIndexAt(chapters, t) {
  let idx = -1;
  (chapters || []).forEach((c, i) => {
    if (c.start <= t + EPS) idx = i;
  });
  return idx;
}

/**
 * Concept progress for the skill-tree panel: concepts of earlier scenes are done, the current
 * scene's concept is active.
 * @param {Timeline} timeline
 * @param {number} sceneIndex
 * @returns {{doneIds: Set<string>, activeId: string | null}}
 */
export function conceptStateAt(timeline, sceneIndex) {
  const scenes = timeline.scenes || [];
  const activeId = scenes[sceneIndex]?.concept_id || null;
  /** @type {Set<string>} */
  const doneIds = new Set();
  for (let i = 0; i < sceneIndex && i < scenes.length; i++) {
    const c = scenes[i].concept_id;
    if (c && c !== activeId) doneIds.add(c);
  }
  return { doneIds, activeId };
}
