// @ts-check
/**
 * Pure, deterministic panel timing (scene-relative seconds, rounded to ms).
 *
 * Everything a panel shows is a function of `(scene, t)`. The player's render-mode schedule
 * (`schedule.stateTimes`) should merge `panelStateTimes(scene)` so the MP4 renderer screenshots
 * every panel state change (terminal lines, quiz-teaser reveal, panel appearance).
 */

import { clamp, finiteOr, round3 } from './util.js';

/**
 * Terminal: at most this many output lines are revealed. MUST equal the player's cap
 * (web/js/player/schedule.js TERMINAL_MAX_LINES): render-mode states come from the schedule's line
 * times merged with panelStateTimes(), and both must list the same instants (tests compare them).
 * The cap also bounds render states per scene (aadhi/compose MAX_STATES_PER_SCENE = 400).
 */
export const TERMINAL_MAX_LINES = 200;

/** Quiz teaser: the answer is revealed between 1.5 s and 4 s before the scene ends. */
export const QUIZ_TEASER_MIN_LEAD = 1.5;
export const QUIZ_TEASER_MAX_LEAD = 4;

/**
 * All display lines of terminal output: CRLF/CR tolerant, trailing blank lines dropped (no cap).
 * Same split as schedule.js terminalOutputLines.
 * @param {unknown} output
 * @returns {string[]}
 */
export function splitTerminalOutput(output) {
  const lines = String(output || '').replace(/\r\n?/g, '\n').split('\n');
  while (lines.length && lines[lines.length - 1].trim() === '') lines.pop();
  return lines;
}

/**
 * The revealed terminal lines: splitTerminalOutput capped at TERMINAL_MAX_LINES (identical to the
 * player's schedule.js terminalOutputLines).
 * @param {unknown} output
 * @returns {string[]}
 */
export function terminalOutputLines(output) {
  return splitTerminalOutput(output).slice(0, TERMINAL_MAX_LINES);
}

/**
 * Output lines beyond the cap (never revealed; the panel says how many were left out).
 * @param {unknown} output
 */
export function terminalOmittedLineCount(output) {
  return Math.max(0, splitTerminalOutput(output).length - TERMINAL_MAX_LINES);
}

/**
 * Times at which terminal output lines appear. Identical to the player's schedule
 * (`web/js/player/schedule.js` terminalLineTimes): evenly spaced across [show_at, duration], the
 * first line one step after the panel appears and the last one step before the scene ends, so
 * render-mode state times and the panel agree exactly. With `outputAt` (the narration says what the
 * program prints: the scene's 'output' sync cue, see terminalOutputAt) the first line appears at
 * max(show_at, outputAt) and the others follow evenly, the last one step before the scene ends.
 * @param {number} showAt        panel show time (scene-relative)
 * @param {number} sceneDuration
 * @param {number} lineCount
 * @param {number | null} [outputAt]
 * @returns {number[]}           increasing, length === lineCount
 */
export function terminalLineTimes(showAt, sceneDuration, lineCount, outputAt = null) {
  const n = Math.max(0, Math.floor(finiteOr(lineCount, 0)));
  const start = Math.max(0, finiteOr(showAt, 0));
  /** @type {number[]} */
  const times = [];
  if (typeof outputAt === 'number' && Number.isFinite(outputAt)) {
    const from = Math.max(start, outputAt);
    const step = Math.max(0, finiteOr(sceneDuration, from) - from) / Math.max(1, n);
    for (let k = 0; k < n; k++) times.push(from + k * step);
    return times;
  }
  const span = Math.max(0, finiteOr(sceneDuration, start) - start);
  for (let k = 0; k < n; k++) times.push(start + ((k + 1) * span) / (n + 1));
  return times;
}

/**
 * When a scene's terminal output starts: the earliest 'output' sync cue (TimedScene.sync_cues,
 * aadhi/compose/sync.py), or null when the narration never says what the program prints. Same rule
 * as the player's schedule.js syncPlan.
 * @param {{ sync_cues?: any[] } | null | undefined} scene  TimedScene
 * @returns {number | null}
 */
export function terminalOutputAt(scene) {
  const cues = scene && Array.isArray(scene.sync_cues) ? scene.sync_cues : [];
  let at = null;
  for (const c of cues) {
    const start = c && c.kind === 'output' ? Number(c.start) : NaN;
    if (Number.isFinite(start) && start >= 0 && (at === null || start < at)) at = start;
  }
  return at;
}

/**
 * Number of entries of the sorted `times` that are <= t.
 * @param {number[]} times
 * @param {number} t
 */
export function countAtOrBefore(times, t) {
  let lo = 0;
  let hi = times.length;
  const x = t + 1e-6;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (times[mid] <= x) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

/**
 * When the quiz teaser reveals its answer: near the end of the scene, but never before half of
 * the panel's visible window has passed (time to think).
 * @param {number} showAt
 * @param {number} sceneDuration
 */
export function quizTeaserRevealTime(showAt, sceneDuration) {
  const show = Math.max(0, finiteOr(showAt, 0));
  const duration = Math.max(show, finiteOr(sceneDuration, show));
  const visible = duration - show;
  const lead = clamp(visible * 0.25, QUIZ_TEASER_MIN_LEAD, QUIZ_TEASER_MAX_LEAD);
  return round3(Math.max(show + visible * 0.5, duration - lead));
}

/**
 * Scene-relative times at which a scene's side panel changes its look. Terminal lines and the quiz teaser are timed
 * without the hold of the teacher's minimum duration (`hold_seconds`), like the panels themselves (util.js
 * sceneDuration).
 * @param {{ duration?: number, hold_seconds?: number, side_panel?: { panel?: any, show_at?: number } | null, layout?: any, sync_cues?: any[] }} scene TimedScene
 * @returns {number[]} sorted, unique
 */
export function panelStateTimes(scene) {
  const rsp = scene && scene.side_panel;
  if (!rsp || !rsp.panel) return [];
  if (scene.layout && scene.layout.show_side_panel === false) return []; // the player hides it
  const showAt = Math.max(0, finiteOr(rsp.show_at, 0));
  const duration = finiteOr(scene.duration, showAt);
  const hold = finiteOr(scene.hold_seconds, 0);
  const contentEnd = hold > 0 ? Math.max(showAt, round3(duration - hold)) : duration;
  /** @type {number[]} */
  const times = [showAt];
  const kind = rsp.panel.kind;
  if (kind === 'terminal' && rsp.panel.terminal) {
    const count = terminalOutputLines(rsp.panel.terminal.output).length;
    times.push(...terminalLineTimes(showAt, contentEnd, count, terminalOutputAt(scene)));
  } else if (kind === 'quiz' && rsp.panel.quiz) {
    times.push(quizTeaserRevealTime(showAt, contentEnd));
  }
  return [...new Set(times.filter((x) => x <= duration + 1e-6))].sort((a, b) => a - b);
}
