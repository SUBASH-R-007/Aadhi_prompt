// @ts-check
/**
 * Master playback clock (absolute timeline seconds).
 *
 * While narration plays, the narration <audio> element is the master: time = scene.start +
 * audio_offset + audio.currentTime (via a MasterSource). Between currentTime updates the clock
 * extrapolates at most `maxExtrapolation` seconds, so visuals stay smooth but wait for audio that
 * is buffering (no narration is skipped on a slow network). A master frozen for longer than
 * `maxStall` seconds is given up on (until it moves again) so a dead download cannot freeze the
 * lecture. Otherwise (lead-in, countdown, tail, silent scenes) a performance.now() clock runs at
 * the playback rate. While playing the clock never goes backwards except on seek(); a master that
 * disagrees with the clock by more than `resyncThreshold` is ignored until the player re-aligns it
 * (e.g. right after a seek).
 *
 * Ticks are emitted through requestAnimationFrame only while playing and only when ticking is
 * enabled (live/preview; render mode drives the player explicitly).
 */

import { clamp } from '../shared/format.js';

/**
 * @typedef {object} MasterSource
 * @property {() => (number | null)} time   absolute seconds, or null when not authoritative
 */

/**
 * @typedef {object} ClockOptions
 * @property {() => number} [now]                       milliseconds (default performance.now)
 * @property {(cb: FrameRequestCallback) => number} [requestFrame]
 * @property {(id: number) => void} [cancelFrame]
 * @property {number} [maxExtrapolation]                seconds (default 0.25)
 * @property {number} [resyncThreshold]                 seconds (default 0.5)
 * @property {number} [maxStall]                        seconds a frozen master is waited for (default 8)
 * @property {boolean} [ticking]                        emit rAF ticks while playing (default true)
 */

export const MIN_RATE = 0.25;
export const MAX_RATE = 4;

export class Clock {
  /** @param {ClockOptions} [opts] */
  constructor(opts = {}) {
    this._now = opts.now || (() => performance.now());
    this._requestFrame =
      opts.requestFrame ||
      (typeof requestAnimationFrame === 'function'
        ? (/** @type {FrameRequestCallback} */ cb) => requestAnimationFrame(cb)
        : (/** @type {FrameRequestCallback} */ cb) => /** @type {any} */ (setTimeout(() => cb(this._now()), 16)));
    this._cancelFrame =
      opts.cancelFrame ||
      (typeof cancelAnimationFrame === 'function'
        ? (/** @type {number} */ id) => cancelAnimationFrame(id)
        : (/** @type {number} */ id) => clearTimeout(id));
    this._maxExtrapolation = opts.maxExtrapolation ?? 0.25;
    this._resyncThreshold = opts.resyncThreshold ?? 0.5;
    this._maxStall = opts.maxStall ?? 8;
    /** master value given up on (frozen too long); NaN = none */
    this._stuck = NaN;
    this._ticking = opts.ticking !== false;
    this._playing = false;
    this._rate = 1;
    /** timeline seconds at the anchor */
    this._base = 0;
    /** clock ms at the anchor */
    this._anchor = this._now();
    /** last value returned while playing (monotonic guard) */
    this._last = 0;
    /** @type {MasterSource | null} */
    this._master = null;
    this._masterSynced = false;
    this._lastMasterTime = NaN;
    this._lastMasterAt = 0;
    /** @type {Set<(t: number) => void>} */
    this._listeners = new Set();
    /** @type {number | null} */
    this._frame = null;
  }

  /** @returns {boolean} */
  get playing() {
    return this._playing;
  }

  /** @returns {number} */
  get rate() {
    return this._rate;
  }

  /** Whether the current time comes from the master source. */
  get mastered() {
    return this._masterSynced && this._master !== null;
  }

  /**
   * Current absolute time in seconds.
   * @returns {number}
   */
  time() {
    if (!this._playing) return this._base;
    const now = this._now();
    const perf = this._base + ((now - this._anchor) / 1000) * this._rate;
    let t = perf;
    const raw = this._master ? this._master.time() : null;
    if (raw !== this._stuck) this._stuck = NaN; // a master that moved again is trusted again
    const mt = raw !== null && Number.isFinite(raw) && raw !== this._stuck ? raw : null;
    let synced = false;
    let gaveUp = false;
    if (mt !== null) {
      // While synced, compare with what we last reported (a stalled master is still authoritative);
      // otherwise with the running clock (e.g. after a seek, before the audio was repositioned).
      const ref = this._masterSynced ? this._last : perf;
      if (Math.abs(mt - ref) <= (this._masterSynced ? this._resyncThreshold * 2 : this._resyncThreshold)) {
        synced = true;
        if (mt !== this._lastMasterTime) {
          this._lastMasterTime = mt;
          this._lastMasterAt = now;
        } else if (this._masterSynced && (now - this._lastMasterAt) / 1000 > this._maxStall) {
          this._stuck = mt; // frozen for too long: stop waiting for it
          synced = false;
          gaveUp = true;
        }
      }
    }
    this._masterSynced = synced;
    if (synced && mt !== null) {
      t = mt + Math.min(((now - this._lastMasterAt) / 1000) * this._rate, this._maxExtrapolation);
    } else {
      this._lastMasterTime = NaN;
      if (gaveUp) t = this._last; // continue from where the stall held the clock (no jump)
    }
    if (t < this._last) t = this._last;
    this._last = t;
    this._base = t;
    this._anchor = now;
    return t;
  }

  /** Start (or keep) playing. */
  play() {
    if (this._playing) return;
    this._playing = true;
    this._anchor = this._now();
    this._last = this._base;
    this._lastMasterTime = NaN;
    this._loop();
  }

  /** Pause at the current time. */
  pause() {
    if (!this._playing) return;
    const t = this.time();
    this._playing = false;
    this._base = t;
    this._stopLoop();
  }

  /**
   * Jump to an absolute time (allowed to go backwards). The master must re-align before it is used.
   * @param {number} t
   */
  seek(t) {
    const v = Number.isFinite(t) ? Math.max(0, t) : 0;
    this._base = v;
    this._anchor = this._now();
    this._last = v;
    this._masterSynced = false;
    this._stuck = NaN;
    this._lastMasterTime = NaN;
  }

  /**
   * Playback rate (clamped to [0.25, 4]); re-anchors so time stays continuous.
   * @param {number} rate
   */
  setRate(rate) {
    const t = this.time();
    this._rate = clamp(Number(rate) || 1, MIN_RATE, MAX_RATE);
    this._base = t;
    this._anchor = this._now();
  }

  /**
   * Set (or clear) the master source.
   * @param {MasterSource | null} master
   */
  setMaster(master) {
    if (this._playing) this.time(); // fold the elapsed time into the anchor first
    this._master = master;
    this._masterSynced = false;
    this._stuck = NaN;
    this._lastMasterTime = NaN;
  }

  /**
   * Subscribe to rAF ticks (only while playing).
   * @param {(t: number) => void} fn
   * @returns {() => void} unsubscribe
   */
  onTick(fn) {
    this._listeners.add(fn);
    this._loop();
    return () => {
      this._listeners.delete(fn);
      if (this._listeners.size === 0) this._stopLoop();
    };
  }

  _loop() {
    if (!this._ticking || !this._playing || this._frame !== null || this._listeners.size === 0) return;
    this._frame = this._requestFrame(() => {
      this._frame = null;
      if (!this._playing) return;
      const t = this.time();
      for (const fn of [...this._listeners]) {
        try {
          fn(t);
        } catch (e) {
          console.error('clock tick handler failed', e);
        }
      }
      this._loop();
    });
  }

  _stopLoop() {
    if (this._frame !== null) {
      this._cancelFrame(this._frame);
      this._frame = null;
    }
  }

  /** Stop ticking and drop listeners and the master. */
  destroy() {
    this._playing = false;
    this._stopLoop();
    this._listeners.clear();
    this._master = null;
  }
}

/**
 * Master source backed by a media element: `base + el.currentTime` whenever the element is meant
 * to be playing (not paused, not ended, no error) — including while it buffers or seeks, so the
 * clock waits for the narration instead of running ahead of it.
 * @param {HTMLMediaElement} el
 * @param {number} base   absolute time corresponding to currentTime 0
 * @returns {MasterSource}
 */
export function mediaMaster(el, base) {
  return {
    time() {
      if (!el || el.paused || el.ended || el.error) return null;
      const ct = el.currentTime;
      return Number.isFinite(ct) ? base + ct : null;
    },
  };
}

/**
 * Whether a media element that should be playing is waiting for data (buffering or seeking).
 * @param {HTMLMediaElement | null | undefined} el
 * @returns {boolean}
 */
export function isBuffering(el) {
  return !!el && !el.paused && !el.ended && !el.error && (el.seeking || el.readyState < 3);
}
