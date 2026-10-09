// @ts-check
/**
 * Terminal panel: the command, then output lines revealed over the scene. Deterministic in t:
 * the number of visible lines comes from `sceneState.terminalLines` (the player's schedule: an
 * array of revealed lines, or a count), otherwise from `terminalLineTimes` (same formula; output
 * starts at the scene's 'output' sync cue when the narration says what the program prints).
 * At most TERMINAL_MAX_LINES lines are revealed (the player's cap); when the output is longer, a
 * "N more lines" note appears together with the last revealed line.
 * Text is inserted with textContent only.
 */

import { Disposer, h } from '../../shared/dom.js';
import { countAtOrBefore, terminalLineTimes, terminalOmittedLineCount, terminalOutputAt, terminalOutputLines } from './timing.js';
import { finiteOr, sceneDuration } from './util.js';

/** @typedef {import('./types.js').PanelFactory} PanelFactory */

/**
 * Revealed line count from the player's scene state (schedule.js passes the revealed lines as an
 * array; a plain number is accepted too). null when the player does not provide it.
 * @param {any} state
 * @returns {number | null}
 */
export function playerLineCount(state) {
  const v = state ? state.terminalLines : undefined;
  if (Array.isArray(v)) return v.length;
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

/**
 * The TimedScene this panel belongs to (ctx.timeline.scenes[ctx.sceneIndex]), or null.
 * @param {any} ctx
 */
function sceneOf(ctx) {
  const scenes = ctx && ctx.timeline && Array.isArray(ctx.timeline.scenes) ? ctx.timeline.scenes : null;
  return scenes && typeof ctx.sceneIndex === 'number' ? scenes[ctx.sceneIndex] || null : null;
}

/** @type {PanelFactory} */
export function createTerminalPanel(body, rsp, ctx, chrome) {
  const disposer = new Disposer();
  const spec = (rsp.panel && rsp.panel.terminal) || { command: '', output: '' };
  const showAt = Math.max(0, finiteOr(rsp.show_at, 0));
  const lines = terminalOutputLines(spec.output);
  const omitted = terminalOmittedLineCount(spec.output);
  const times = terminalLineTimes(showAt, sceneDuration(ctx, showAt), lines.length, terminalOutputAt(sceneOf(ctx)));

  chrome.el.classList.add('ap-panel--terminal-skin');
  chrome.head.prepend(
    h('span', { class: 'ap-term-dots', 'aria-hidden': 'true' }, h('i', null), h('i', null), h('i', null)),
  );
  const cursor = h('span', { class: 'ap-term-cursor', 'aria-hidden': 'true' });
  const promptLine = h(
    'div',
    { class: 'ap-term-line ap-term-cmd' },
    h('span', { class: 'ap-term-prompt', text: '$ ' }),
    h('span', { class: 'ap-term-command', text: String(spec.command || '') }),
  );
  /** @type {HTMLElement[]} */
  const outEls = lines.map((line) => h('div', { class: 'ap-term-line ap-term-out', hidden: true, text: line || ' ' }));
  // Lines start at the top; once they overflow, the newest stay visible (see panels.css).
  const more = omitted
    ? h('div', {
        class: 'ap-term-line ap-term-more',
        hidden: true,
        text: `… ${omitted} more line${omitted === 1 ? '' : 's'} not shown`,
      })
    : null;
  const linesBox = h('div', { class: 'ap-term-lines' }, promptLine, outEls, more);
  const screen = h('div', { class: 'ap-term-screen', role: 'log', 'aria-live': ctx.mode === 'live' ? 'polite' : 'off' }, linesBox);
  body.appendChild(screen);

  let shown = -1;
  /** @param {number} count */
  const show = (count) => {
    const n = Math.max(0, Math.min(lines.length, Math.floor(count)));
    if (n === shown) return;
    shown = n;
    outEls.forEach((el, i) => {
      el.hidden = i >= n;
    });
    const complete = n === lines.length && n > 0;
    if (more) more.hidden = !complete;
    const host = more && complete ? more : n > 0 ? outEls[n - 1] : promptLine;
    host.appendChild(cursor);
    screen.dataset.lines = String(n);
  };
  show(0);

  return {
    update(t, state) {
      show(playerLineCount(state) ?? countAtOrBefore(times, t));
    },
    destroy: () => disposer.dispose(),
  };
}

/**
 * Visible line count at scene time t (exported for tests / the player's schedule).
 * @param {import('./types.js').ResolvedSidePanel} rsp
 * @param {number} duration
 * @param {number} t
 * @param {number | null} [outputAt]   the scene's 'output' sync cue (terminalOutputAt), if any
 */
export function terminalLinesAt(rsp, duration, t, outputAt = null) {
  const showAt = Math.max(0, finiteOr(rsp.show_at, 0));
  const lines = terminalOutputLines(rsp.panel && rsp.panel.terminal && rsp.panel.terminal.output);
  return countAtOrBefore(terminalLineTimes(showAt, duration, lines.length, outputAt), t);
}
