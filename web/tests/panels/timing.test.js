import { test } from 'node:test';
import assert from 'node:assert/strict';

import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import {
  QUIZ_TEASER_MAX_LEAD,
  QUIZ_TEASER_MIN_LEAD,
  TERMINAL_MAX_LINES,
  countAtOrBefore,
  panelStateTimes,
  quizTeaserRevealTime,
  splitTerminalOutput,
  terminalLineTimes,
  terminalOmittedLineCount,
  terminalOutputLines,
} from '../../js/player/panels/timing.js';

/** The player's pure schedule module (owned by the player area), or null when absent. */
async function loadSchedule() {
  const url = new URL('../../js/player/schedule.js', import.meta.url);
  if (!existsSync(fileURLToPath(url))) return null;
  return import(url.href);
}

/** A terminal scene: `lines` output lines of ~8 characters (250 lines = 2139 chars < 3000). */
function terminalScene(lines, duration = 60, showAt = 2) {
  const output = Array.from({ length: lines }, (_, i) => `line ${i}`).join('\n');
  return {
    scene_id: 'term',
    index: 3,
    type: 'board',
    start: 0,
    duration,
    beats: [],
    board: [],
    side_panel: { show_at: showAt, panel: { kind: 'terminal', terminal: { command: 'ls -l', output } } },
  };
}

const nondecreasing = (a) => a.every((x, i) => i === 0 || x >= a[i - 1]);

test('terminalOutputLines splits CRLF/CR and drops trailing blank lines', () => {
  assert.deepEqual(terminalOutputLines('a\r\nb\rc\n\n  \n'), ['a', 'b', 'c']);
  assert.deepEqual(terminalOutputLines('a\n\nb'), ['a', '', 'b']);
  assert.deepEqual(terminalOutputLines(''), []);
  assert.deepEqual(terminalOutputLines(null), []);
  assert.equal(terminalOutputLines('x\n'.repeat(500)).length, TERMINAL_MAX_LINES);
});

test('lines beyond the cap are counted, never revealed', () => {
  const output = Array.from({ length: 250 }, (_, i) => `l${i}`).join('\n');
  assert.equal(splitTerminalOutput(output).length, 250);
  assert.equal(terminalOutputLines(output).length, TERMINAL_MAX_LINES);
  assert.equal(terminalOmittedLineCount(output), 50);
  assert.equal(terminalOmittedLineCount('a\nb'), 0);
  assert.equal(terminalOmittedLineCount(null), 0);
});

test('the panel splits terminal output exactly like the player schedule (same cap)', async (t) => {
  const schedule = await loadSchedule();
  if (!schedule || typeof schedule.terminalOutputLines !== 'function') return t.skip('schedule.terminalOutputLines not available');
  assert.equal(TERMINAL_MAX_LINES, schedule.TERMINAL_MAX_LINES, 'one cap for panel and schedule');
  const many = (n) => Array.from({ length: n }, (_, i) => `out ${i}`).join('\n');
  const outputs = ['', '   ', '\n\n', 'a', 'a\r\nb\rc\n\n  \n', 'a\n\nb', many(199), many(200), many(201), many(250)];
  for (const output of [...outputs, 'x\n'.repeat(500), '\r\n'.repeat(3)]) {
    assert.deepEqual(terminalOutputLines(output), schedule.terminalOutputLines(output), JSON.stringify(output.slice(0, 20)));
  }
});

test('a 250-line terminal adds no render states beyond the schedule and stays under the compositor limit', async (t) => {
  const schedule = await loadSchedule();
  if (!schedule || typeof schedule.renderStateList !== 'function') return t.skip('schedule.renderStateList not available');
  for (const lines of [9, 199, 250, 500]) {
    const scene = terminalScene(lines);
    const panel = panelStateTimes(scene);
    const own = new Set(schedule.stateTimes(scene));
    for (const x of panel) assert.ok(own.has(x), `panel time ${x} is a schedule state time (${lines} lines)`);
    assert.equal(panel.length, 1 + Math.min(lines, TERMINAL_MAX_LINES), 'show_at + one time per revealed line');
    const merged = schedule.renderStateList(scene, panel);
    assert.equal(merged.length, schedule.renderStateList(scene).length, `no extra states (${lines} lines)`);
    assert.ok(merged.length <= 400, `aadhi/compose keeps at most 400 states per scene (${merged.length})`);
    // the panel reveals exactly the schedule's line count at every state
    const visible = terminalOutputLines(scene.side_panel.panel.terminal.output);
    const times = terminalLineTimes(2, 60, visible.length);
    for (const { t: at } of merged) {
      assert.equal(countAtOrBefore(times, at), schedule.sceneStateAt(scene, at).terminalLines.length, `t=${at}`);
    }
  }
});

test('terminal lines are evenly spaced inside (show_at, duration)', () => {
  const times = terminalLineTimes(2, 20, 5);
  assert.equal(times.length, 5);
  assert.ok(nondecreasing(times));
  assert.ok(times[0] > 2 && times.at(-1) < 20);
  const step = 18 / 6;
  times.forEach((t, k) => assert.ok(Math.abs(t - (2 + (k + 1) * step)) < 1e-12));
  assert.deepEqual(terminalLineTimes(0, 10, 0), []);
  assert.deepEqual(terminalLineTimes(5, 3, 2), [5, 5], 'degenerate window collapses onto show_at');
  assert.deepEqual(terminalLineTimes(1.5, 12.25, 7), terminalLineTimes(1.5, 12.25, 7));
});

test('terminal timing matches the player schedule when it is available', async (t) => {
  const url = new URL('../../js/player/schedule.js', import.meta.url);
  if (!existsSync(fileURLToPath(url))) return t.skip('player schedule.js not present');
  const schedule = await import(url.href);
  if (typeof schedule.terminalLineTimes !== 'function') return t.skip('schedule.terminalLineTimes not exported');
  for (const [show, dur, n] of [[0, 10, 3], [2.5, 31.2, 9], [4, 4.5, 1]]) {
    assert.deepEqual(terminalLineTimes(show, dur, n), schedule.terminalLineTimes(n, show, dur));
  }
});

test('countAtOrBefore counts with a small epsilon', () => {
  const times = [1, 2, 2, 3.5];
  assert.equal(countAtOrBefore(times, 0.5), 0);
  assert.equal(countAtOrBefore(times, 1), 1);
  assert.equal(countAtOrBefore(times, 2 - 1e-7), 3);
  assert.equal(countAtOrBefore(times, 3.49), 3);
  assert.equal(countAtOrBefore(times, 99), 4);
  assert.equal(countAtOrBefore([], 5), 0);
});

test('quiz teaser reveals near the end but after half of the visible window', () => {
  const r = quizTeaserRevealTime(0, 20);
  assert.equal(r, 20 - QUIZ_TEASER_MAX_LEAD);
  const short = quizTeaserRevealTime(0, 4);
  assert.ok(short >= 2 && short <= 4 - QUIZ_TEASER_MIN_LEAD + 1e-9);
  const mid = quizTeaserRevealTime(5, 13); // visible 8 s -> lead 2 s
  assert.equal(mid, 11);
  const tiny = quizTeaserRevealTime(9, 10);
  assert.equal(tiny, 9.5);
  assert.equal(quizTeaserRevealTime(3, 2), 3, 'degenerate window');
});

test('panelStateTimes merges show_at, terminal lines and the teaser reveal', () => {
  const terminal = {
    duration: 12,
    side_panel: { show_at: 1, panel: { kind: 'terminal', terminal: { command: 'ls', output: 'a\nb\nc' } } },
  };
  const t = panelStateTimes(terminal);
  assert.deepEqual(t, [1, ...terminalLineTimes(1, 12, 3)]);
  const quiz = {
    duration: 15,
    side_panel: { show_at: 2, panel: { kind: 'quiz', quiz: { question: 'q', options: ['a', 'b'], correct_index: 0 } } },
  };
  assert.deepEqual(panelStateTimes(quiz), [2, quizTeaserRevealTime(2, 15)]);
  assert.deepEqual(panelStateTimes({ duration: 9, side_panel: { show_at: 0, panel: { kind: 'chart' } } }), [0]);
  assert.deepEqual(panelStateTimes({ duration: 9, side_panel: null }), []);
  const hidden = { ...terminal, layout: { show_side_panel: false } };
  assert.deepEqual(panelStateTimes(hidden), [], 'the player hides the panel: no panel states');
  assert.deepEqual(panelStateTimes(/** @type {any} */ (null)), []);
});
