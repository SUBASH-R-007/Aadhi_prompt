// @ts-check
/**
 * Timer guard for the Dynamic render page (DYNAMIC_VIDEO_DESIGN §7.4 rule 3). Once `aadhiStage.ready` has resolved
 * in render mode, every frame is a pure function of its frame number, so nothing may schedule work on the wall
 * clock: `setTimeout` and `setInterval` are replaced by functions that throw, except for an allow-list of call sites
 * (none in our bundle). Fonts and MathJax start before `ready`, so they keep real timers.
 *
 * A trip is logged with `console.error("aadhi timer guard: ...")` (the render worker keeps such lines apart and a
 * determinism or golden run fails on them), recorded in `window.aadhiStageGuard.trips`, and thrown to the caller.
 */

/** Prefix of every guard log line (aadhi/compose/dynamic/stage_page.py matches "timer guard"). */
export const GUARD_MESSAGE = 'aadhi timer guard';
/** Timer functions the guard replaces. */
export const GUARDED = Object.freeze(['setTimeout', 'setInterval']);

export class GuardError extends Error {
  /** @param {string} api */
  constructor(api) {
    super(`${GUARD_MESSAGE}: ${api} is not allowed after the stage is ready (frames are pure functions of time)`);
    this.name = 'GuardError';
    this.code = 'timer_guard';
  }
}

/**
 * @typedef {object} GuardTrip
 * @property {string} api
 * @property {string} where   the first frames of the caller's stack
 */

/**
 * Replace the window's timers. `allow` holds substrings of a stack (file names) whose calls pass through.
 * @param {any} win
 * @param {{allow?: string[], log?: {error: (...a: any[]) => void}}} [opts]
 * @returns {{trips: GuardTrip[], restore: () => void}}
 */
export function installTimerGuard(win, opts = {}) {
  const allow = (opts.allow || []).filter((s) => typeof s === 'string' && s);
  const log = opts.log || win.console;
  /** @type {GuardTrip[]} */
  const trips = [];
  /** @type {Record<string, any>} */
  const real = {};
  for (const name of GUARDED) {
    real[name] = win[name];
    const original = real[name];
    win[name] = function guarded(/** @type {any[]} */ ...args) {
      const stack = String(new Error().stack || '');
      if (allow.length && allow.some((a) => stack.includes(a))) return original.apply(win, args);
      const where = stack.split('\n').slice(2, 5).map((s) => s.trim()).join(' | ');
      trips.push({ api: name, where });
      try {
        log.error(`${GUARD_MESSAGE}: ${name} called after ready (${where})`);
      } catch {
        /* logging must not hide the trip */
      }
      throw new GuardError(name);
    };
  }
  win.aadhiStageGuard = { installed: true, trips };
  return {
    trips,
    restore() {
      for (const name of GUARDED) win[name] = real[name];
      win.aadhiStageGuard = { installed: false, trips };
    },
  };
}
