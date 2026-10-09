// @ts-check
/**
 * MascotController: the background layer of the stage. Loops the branding mascot clips
 * (Branding.mascot_clips, position -> URL; popup_bottom_left/right share one clip) muted and
 * crossfades when the scene's mascot position changes. In live mode a subtle speech-reactive
 * scale/bob is driven by setLevel() (WebAudio analyser of the narration). Render mode shows nothing:
 * the MP4 renderer composites the clip behind the transparent screenshots.
 *
 * Robustness (live/preview only; render mode builds no clips and none of this runs):
 *  - preload: with opts.positions (the positions the lecture uses) only those clips load eagerly; the
 *    others fetch metadata until they are first needed. Without it every clip loads eagerly.
 *  - posters: a clip at /branding/<name>.mp4 shows /branding/posters/<name>.jpg (its frame 0) until
 *    it has a frame of its own (posterUrl()).
 *  - frame-gated crossfade: the clip on screen stays until the incoming one has a frame (capped at
 *    MASCOT_TIMING.swapTimeoutMs), so a position change never flashes the empty stage; rapid changes
 *    never leave a stray clip playing. `active` always names the target clip at once.
 *  - recovery: a clip that fails to load is retried after 1 s and 3 s with a cache-busting URL, then
 *    marked failed (one more try whenever it becomes active again). play() rejections other than
 *    AbortError count, and playAttempts in a row switch to the fallback; NotAllowedError (autoplay
 *    blocked) switches at once and the clip waits for the next setPlaying(true) (a user gesture).
 *  - watchdog: tick() (called by the player on every frame, throttled to watchdogMs) restarts a clip
 *    the browser paused and pauses stray clips; a clip frozen while playing (no currentTime progress
 *    for stallMs) gets the fallback at once and a reload on every second strike. A clip still
 *    buffering while its data keeps arriving (a slow network: 'progress' events) keeps its poster and
 *    is not reloaded, since a reload would throw the partial download away.
 *  - fallback: while the active clip is retrying, stalled, blocked or broken, its poster is shown with
 *    a gentle CSS breathing motion (the speech bob still applies). A missing poster falls back to the
 *    static background, so the stage never shows a hole where Aadhi should be. The first progress of
 *    the active clip ends the fallback.
 *  getStatus() reports the state and counters (tests, diagnostics).
 *
 * Behaviour state and cue bubble (all modes): setState(state, cue) takes SceneState.mascotState /
 * mascotCue (mascot-state.js, a pure function of (scene, t), so seeking and render mode agree). The
 * state is written to data-state on the layer (a hook for optional per-state clips). The cue bubble
 * ('dots' / 'question' / 'success') is NOT part of this layer: mountCue() puts it in the scene root,
 * which render-mode screenshots capture, so the MP4 shows the same bubble at the same stage pixels.
 *
 * Speech motion: setLevel() takes the narration loudness, preferably from the build-time envelope
 * (TimedScene.audio_envelope, audio.js envelopeLevel), else from the WebAudio meter.
 *
 * Rig hook: Branding.mascot_rig_url names an optional artist-made rig (e.g. Rive). It is not loaded
 * yet; loadRig() is the integration point and currently resolves to null so the clips are used.
 */

import { Disposer, h, safeUrl } from '../shared/dom.js';
import { clamp } from '../shared/format.js';
import { releaseMedia } from './audio.js';
import { cueAnchor } from './mascot-state.js';

/** @typedef {import('../shared/types.js').Branding} Branding */
/** @typedef {import('../shared/types.js').PlayerMode} PlayerMode */

export const MASCOT_TIMING = Object.freeze({
  crossfadeMs: 600, // the .ap-mascot-clip opacity transition (player.css); the outgoing clip pauses after it
  swapTimeoutMs: 1500, // longest wait for the incoming clip's first frame before crossfading anyway
  retryDelaysMs: Object.freeze([1000, 3000]), // reloads after a load error, then the clip is "failed"
  watchdogMs: 1000,
  stallMs: 3500, // no currentTime progress for this long while playing: the clip is frozen
  playAttempts: 3, // failed play() calls in a row before the fallback
});

/** @typedef {'idle' | 'loading' | 'loaded' | 'error' | 'failed'} ClipStatus */

/**
 * @typedef {object} ClipRecord
 * @property {string} url             branding URL (the key in MascotController.clips)
 * @property {HTMLVideoElement} el
 * @property {ClipStatus} status      error: a reload is scheduled; failed: retries used up
 * @property {number} retries
 * @property {ReturnType<typeof setTimeout> | null} retryTimer
 * @property {number} playFailures
 * @property {number} lastTime        currentTime at the last progress check
 * @property {number} lastProgressAt  ms (opts.now clock)
 * @property {number} strikes         stall detections without progress in between
 * @property {number} lastDataAt      ms of the last 'progress' event (media data arriving)
 */

/**
 * @typedef {object} MascotOptions
 * @property {PlayerMode} mode
 * @property {boolean} [reducedMotion]
 * @property {Iterable<string | null | undefined>} [positions]  positions the lecture uses (eager preload)
 * @property {() => number} [now]                               ms clock (watchdog)
 */

const POSTER_CLIP = /^\/branding\/([A-Za-z0-9][A-Za-z0-9_.-]*)\.(?:mp4|webm)$/;

/**
 * Poster (frame-0 still) of a branding clip: /branding/<name>.mp4 -> /branding/posters/<name>.jpg
 * (files in Settings.branding_dir/posters). Clips elsewhere have no poster.
 * @param {string | null | undefined} clipUrl
 * @returns {string | null}
 */
export function posterUrl(clipUrl) {
  const m = POSTER_CLIP.exec(String(clipUrl || ''));
  return m ? `/branding/posters/${m[1]}.jpg` : null;
}

/** @returns {number} */
function defaultNow() {
  return typeof performance !== 'undefined' ? performance.now() : Date.now();
}

/** @returns {boolean} */
function documentHidden() {
  return typeof document !== 'undefined' && document.visibilityState === 'hidden';
}

export class MascotController {
  /**
   * @param {HTMLElement} layer   stage background layer
   * @param {Branding | null | undefined} branding
   * @param {MascotOptions} opts
   */
  constructor(layer, branding, opts) {
    this.mode = opts.mode;
    this.reducedMotion = !!opts.reducedMotion;
    this.branding = branding || {};
    this.now = typeof opts.now === 'function' ? opts.now : defaultNow;
    /** @type {Map<string, HTMLVideoElement>} */
    this.clips = new Map();
    /** @type {Map<HTMLVideoElement, ClipRecord>} */
    this.records = new Map();
    /** @type {HTMLVideoElement | null} the clip of the current position */
    this.active = null;
    /** @type {HTMLVideoElement | null} the clip faded in (lags `active` until the incoming clip has a frame) */
    this.shown = null;
    /** @type {HTMLVideoElement | null} the clip fading out (keeps playing until the crossfade ends) */
    this.fading = null;
    /** @type {string | null} */
    this.position = null;
    this.playing = false;
    this.visible = this.mode !== 'render';
    /** play() was refused without a user gesture; retried on the next setPlaying(true) */
    this.blocked = false;
    /** @type {{ reason: string } | null} */
    this.fallback = null;
    this.counts = { switches: 0, fallbacks: 0, stalls: 0, reloads: 0, errors: 0, playFailures: 0 };
    this.level = 0;
    /** @type {string} behaviour state (SceneState.mascotState) */
    this.state = 'idle';
    /** @type {string | null} cue shown in the bubble (SceneState.mascotCue) */
    this.cue = null;
    /** @type {HTMLDivElement | null} the cue bubble of the current scene (lives in the scene root) */
    this.cueEl = null;
    /** @type {Set<ReturnType<typeof setTimeout>>} */
    this.timers = new Set();
    this.disposer = new Disposer();
    this.destroyed = false;
    this._swapSeq = 0;
    /** @type {(() => void) | null} */
    this._cancelSwap = null;
    this._retrySeq = 0;
    this._lastTick = -Infinity;
    /** @type {Set<string>} */
    this._warned = new Set();
    this.rigUrl = this.branding.mascot_rig_url || null;
    /** @type {HTMLDivElement} */
    this.motion = h('div', { class: 'ap-mascot-motion' });
    /** @type {HTMLDivElement} */
    this.el = h('div', {
      class: ['ap-mascot', 'is-paused', { 'is-hidden': this.mode === 'render' }], 'aria-hidden': 'true', dataset: { state: this.state },
    }, this.motion);
    /** @type {HTMLImageElement | null} */
    this.background = null;
    /** @type {HTMLImageElement[]} two fallback frames, so posters crossfade on a position change */
    this.frames = [];
    layer.appendChild(this.el);
    if (this.mode === 'render') return;
    if (this.branding.static_background_url) {
      this.background = h('img', { class: 'ap-mascot-bg', src: this.branding.static_background_url, alt: '', decoding: 'async' });
      this.motion.appendChild(this.background);
    }
    const eager = opts.positions ? new Set([...opts.positions].map((p) => this.clipUrl(p)).filter(Boolean)) : null;
    const urls = new Set(Object.values(this.branding.mascot_clips || {}).filter(Boolean));
    for (const url of urls) {
      const preload = !eager || eager.has(url) ? 'auto' : 'metadata';
      /** @type {HTMLVideoElement} */
      const v = h('video', {
        class: 'ap-mascot-clip', src: url, poster: posterUrl(url), preload, playsinline: true, 'aria-hidden': 'true',
      });
      v.muted = true;
      v.defaultMuted = true;
      v.loop = true;
      this.clips.set(url, v);
      /** @type {ClipRecord} */
      const rec = {
        url, el: v, status: preload === 'auto' ? 'loading' : 'idle', retries: 0, retryTimer: null, playFailures: 0,
        lastTime: -1, lastProgressAt: this.now(), strikes: 0, lastDataAt: -Infinity,
      };
      this.records.set(v, rec);
      this._listen(rec);
      this.motion.appendChild(v);
    }
    for (let i = 0; i < 2; i++) {
      /** @type {HTMLImageElement} */
      const img = h('img', { class: 'ap-mascot-fallback', alt: '', decoding: 'async' });
      this.disposer.listen(img, 'error', () => this._onFrameError(img));
      this.frames.push(img);
      this.motion.appendChild(img);
    }
  }

  /**
   * Clip URL for a position (falls back to the "hidden" clip, then to the static background).
   * @param {string | null | undefined} position
   * @returns {string | null}
   */
  clipUrl(position) {
    const clips = this.branding.mascot_clips || {};
    const key = position || 'left';
    return clips[key] || (key.startsWith('popup') ? clips.popup_bottom_left || clips.popup_bottom_right : null) || clips.hidden || null;
  }

  /**
   * Crossfade to the clip of a position (once it has a frame to show).
   * @param {string | null | undefined} position
   * @param {{ origin?: string }} [opts]   transform-origin for the speech motion
   */
  setPosition(position, opts = {}) {
    if (this.mode === 'render') return;
    if (opts.origin) {
      this.motion.style.transformOrigin = opts.origin;
      this.el.style.setProperty('--ap-mascot-origin', opts.origin);
    }
    const key = position || 'left';
    if (key === this.position) return;
    this.position = key;
    this.el.dataset.position = key;
    const url = this.clipUrl(key);
    const next = url ? this.clips.get(url) || null : null;
    if (next === this.active) {
      // Same clip (popup left/right share one) or still no clip (the static background shows).
      if (!next && this.background) this.background.classList.add('is-active');
      return;
    }
    this.active = next;
    this.counts.switches++;
    if (next) this._activate(/** @type {ClipRecord} */ (this.records.get(next)));
    else this._setFallback(null);
    this._pauseStrays();
    this._scheduleSwap(next);
  }

  /** @param {ClipRecord} rec */
  _activate(rec) {
    const v = rec.el;
    if (v.preload !== 'auto') v.preload = 'auto';
    if (rec.status === 'idle') rec.status = 'loading';
    rec.lastProgressAt = this.now();
    rec.lastTime = -1;
    rec.strikes = 0;
    rec.playFailures = 0;
    if (rec.status === 'failed') {
      // One more attempt at a clip that failed earlier; its poster covers the wait.
      rec.retries = MASCOT_TIMING.retryDelaysMs.length;
      this._enterFallback('clip failed to load');
      this._reload(rec);
    } else if (rec.status === 'error') {
      this._enterFallback('clip error, retrying');
    } else {
      if (this.fallback) this._showFrame(); // the new position's poster while the fallback is up
      if (this.playing) this._play(v);
    }
  }

  /**
   * Swap the visible clip now, or once `next` has decoded a frame (capped at swapTimeoutMs).
   * @param {HTMLVideoElement | null} next
   */
  _scheduleSwap(next) {
    if (this._cancelSwap) this._cancelSwap();
    const seq = ++this._swapSeq;
    if (!next || !this.shown || next.readyState >= 2) {
      this._swap(next);
      return;
    }
    const swap = () => {
      if (seq === this._swapSeq && !this.destroyed) this._swap(next);
    };
    next.addEventListener('loadeddata', swap);
    const timer = this._later(swap, MASCOT_TIMING.swapTimeoutMs);
    this._cancelSwap = () => {
      next.removeEventListener('loadeddata', swap);
      clearTimeout(timer);
      this.timers.delete(timer);
      this._cancelSwap = null;
    };
  }

  /** @param {HTMLVideoElement | null} next */
  _swap(next) {
    if (this._cancelSwap) this._cancelSwap();
    const prev = this.shown;
    this.shown = next;
    for (const v of this.clips.values()) v.classList.toggle('is-active', v === next);
    // The static background shows whenever no clip covers this position (also on the first call).
    if (this.background) this.background.classList.toggle('is-active', !next);
    if (!prev || prev === next) return;
    // Both clips play through the CSS crossfade, then only the active one keeps playing.
    this.fading = prev;
    this._later(() => {
      if (this.fading === prev) this.fading = null;
      if (prev !== this.active && prev !== this.shown) prev.pause();
    }, MASCOT_TIMING.crossfadeMs);
  }

  /** Pause every clip that is neither the active one, the one on screen nor the one fading out. */
  _pauseStrays() {
    for (const v of this.clips.values()) {
      if (v !== this.active && v !== this.shown && v !== this.fading && !v.paused) v.pause();
    }
  }

  /**
   * @param {() => void} fn
   * @param {number} ms
   */
  _later(fn, ms) {
    const timer = setTimeout(() => {
      this.timers.delete(timer);
      fn();
    }, ms);
    this.timers.add(timer);
    return timer;
  }

  /** @param {HTMLVideoElement} v */
  _play(v) {
    const rec = this.records.get(v);
    /** @type {any} */
    let p;
    try {
      p = v.play();
    } catch (err) {
      p = Promise.reject(err);
    }
    if (!p || typeof p.then !== 'function') return;
    p.then(
      () => {
        if (!rec || this.destroyed) return;
        rec.playFailures = 0;
        if (v === this.active) this._exitFallbackIfHealthy();
      },
      (/** @type {any} */ err) => {
        if (rec) this._onPlayRejected(rec, err);
      },
    );
  }

  /**
   * @param {ClipRecord} rec
   * @param {any} err
   */
  _onPlayRejected(rec, err) {
    const name = err && err.name;
    // AbortError: a later pause()/load() superseded the call. Load errors have their own retry path.
    if (this.destroyed || name === 'AbortError' || rec.el !== this.active || !this.playing) return;
    if (rec.status === 'error' || rec.status === 'failed') return;
    if (name === 'NotAllowedError') {
      // Autoplay refused (e.g. iOS Low Power Mode refuses even muted clips): wait for a gesture.
      this.blocked = true;
      this._enterFallback('autoplay blocked');
      return;
    }
    rec.playFailures++;
    this.counts.playFailures++;
    if (rec.playFailures >= MASCOT_TIMING.playAttempts) this._enterFallback('playback keeps failing');
  }

  /**
   * Loop the active clip while the lecture plays.
   * @param {boolean} playing
   */
  setPlaying(playing) {
    this.playing = !!playing;
    this.el.classList.toggle('is-paused', !this.playing);
    if (this.cueEl) this.cueEl.classList.toggle('is-paused', !this.playing); // the thinking dots hold still
    if (this.playing) this.blocked = false; // called from a user gesture (play button): try again
    if (!this.playing) {
      for (const v of this.clips.values()) if (!v.paused) v.pause();
      return;
    }
    if (!this.active) return;
    const rec = this.records.get(this.active);
    if (rec) rec.lastProgressAt = this.now();
    this._play(this.active);
  }

  /**
   * Watchdog (live/preview): the player calls this on every frame; it runs at most once per
   * MASCOT_TIMING.watchdogMs unless forced (e.g. when the tab becomes visible again).
   * @param {{ force?: boolean }} [opts]
   */
  tick(opts = {}) {
    if (this.mode === 'render' || this.destroyed) return;
    const now = this.now();
    if (!opts.force && now - this._lastTick < MASCOT_TIMING.watchdogMs) return;
    // After a gap (hidden tab, paused lecture) progress is measured afresh: no stall verdict yet.
    const gap = now - this._lastTick > 2 * MASCOT_TIMING.watchdogMs;
    this._lastTick = now;
    if (documentHidden()) {
      this._lastTick = -Infinity; // browsers pause background video on purpose; resume when visible
      return;
    }
    this._pauseStrays();
    const v = this.active;
    if (!v || !this.playing || !this.visible) return;
    const rec = this.records.get(v);
    if (!rec) return;
    if (rec.status === 'failed') {
      this._enterFallback('clip failed to load');
      return;
    }
    if (rec.status === 'error') return; // a reload is scheduled
    if (v.paused) {
      // Paused by the browser (power saving, media keys) or a start that failed: start again.
      rec.lastProgressAt = now;
      if (!this.blocked) this._play(v);
      return;
    }
    if (gap) {
      rec.lastProgressAt = now;
      rec.lastTime = v.currentTime;
      return;
    }
    this._noteProgress(rec, now);
    const frozenFor = now - rec.lastProgressAt;
    if (frozenFor >= MASCOT_TIMING.stallMs) {
      rec.lastProgressAt = now;
      if (v.readyState < 3 && now - rec.lastDataAt < MASCOT_TIMING.stallMs) {
        // Still buffering while data keeps arriving (slow network), not frozen: the poster covers the
        // wait; a reload would throw the partial download away and start again from byte 0.
        this._enterFallback('clip buffering');
        return;
      }
      // Frozen while "playing": the poster at once, a reload if it stays stuck.
      this.counts.stalls++;
      rec.strikes++;
      this._enterFallback('clip stalled');
      if (rec.strikes % 2 === 0) this._reload(rec);
    }
  }

  /**
   * Speech-reactive motion (live mode). `level` is the narration loudness 0..1.
   * @param {number} level
   */
  setLevel(level) {
    if (this.mode !== 'live' && this.mode !== 'preview') return;
    if (this.reducedMotion) return;
    const target = clamp(level, 0, 1);
    this.level += (target - this.level) * (target > this.level ? 0.35 : 0.12);
    if (this.level < 0.002) this.level = 0;
    const s = this.level;
    this.motion.style.transform = s ? `translateY(${(-5 * s).toFixed(2)}px) scale(${(1 + 0.012 * s).toFixed(4)})` : '';
  }

  /**
   * Put the cue bubble for a scene into its root (the scene layer, captured by render-mode screenshots),
   * at the stage pixels beside Aadhi's head for `position` (mascot-state.js CUE_ANCHORS). Positions
   * without a free spot (popup, hidden) get no bubble. The previous scene's bubble goes with its root.
   * @param {HTMLElement} parent   the scene root
   * @param {string | null | undefined} position   Layout.mascot_position
   */
  mountCue(parent, position) {
    if (this.cueEl) this.cueEl.remove();
    this.cueEl = null;
    this.cue = null;
    const anchor = cueAnchor(position);
    if (!anchor) return;
    /** @type {HTMLDivElement} */
    const el = h('div', {
      class: ['ap-mascot-cue', { 'is-paused': !this.playing }], 'aria-hidden': 'true', style: { left: anchor.x, top: anchor.y },
    }, h('span', { class: 'ap-mascot-cue-bubble' }, h('i'), h('i'), h('i')));
    parent.appendChild(el);
    this.cueEl = el;
  }

  /**
   * Behaviour state and cue of the current frame (SceneState.mascotState / mascotCue). Repeating the
   * current values does nothing. The bubble keeps its last cue while it fades out.
   * @param {string | null | undefined} state
   * @param {string | null | undefined} [cue]
   */
  setState(state, cue = null) {
    const next = state || 'idle';
    if (next !== this.state) {
      this.state = next;
      this.el.dataset.state = next;
    }
    const shown = (this.cueEl && cue) || null;
    if (shown === this.cue) return;
    this.cue = shown;
    if (!this.cueEl) return;
    if (shown) this.cueEl.dataset.cue = shown;
    this.cueEl.classList.toggle('is-shown', !!shown);
  }

  /** Show or hide the whole layer (e.g. during the intro). @param {boolean} visible */
  setVisible(visible) {
    this.visible = !!visible && this.mode !== 'render';
    this.el.classList.toggle('is-hidden', !this.visible);
  }

  /**
   * State and counters, for tests and diagnostics.
   * @returns {{ position: string | null, playing: boolean, visible: boolean, blocked: boolean,
   *   clip: string | null, status: ClipStatus | null, shown: string | null, fallback: string | null,
   *   clips: Record<string, ClipStatus>, counts: MascotController['counts'], state: string, cue: string | null }}
   */
  getStatus() {
    const rec = this.active ? this.records.get(this.active) || null : null;
    const shown = this.shown ? this.records.get(this.shown) || null : null;
    /** @type {Record<string, ClipStatus>} */
    const clips = {};
    for (const r of this.records.values()) clips[r.url] = r.status;
    return {
      position: this.position,
      playing: this.playing,
      visible: this.visible,
      blocked: this.blocked,
      clip: rec ? rec.url : null,
      status: rec ? rec.status : null,
      shown: shown ? shown.url : null,
      fallback: this.fallback ? this.fallback.reason : null,
      clips,
      counts: { ...this.counts },
      state: this.state,
      cue: this.cue,
    };
  }

  /**
   * Integration point for an artist-made rig (Branding.mascot_rig_url). Not implemented yet.
   * @returns {Promise<null>}
   */
  async loadRig() {
    return null;
  }

  destroy() {
    this.destroyed = true;
    if (this._cancelSwap) this._cancelSwap();
    for (const t of this.timers) clearTimeout(t);
    this.timers.clear();
    for (const rec of this.records.values()) if (rec.retryTimer) clearTimeout(rec.retryTimer);
    this.disposer.dispose();
    for (const v of this.clips.values()) releaseMedia(v);
    this.clips.clear();
    this.records.clear();
    this.active = this.shown = this.fading = null;
    if (this.cueEl) this.cueEl.remove();
    this.cueEl = null;
    this.el.remove();
  }

  // -------------------------------------------------------------------------------------------
  // Clip events and recovery
  // -------------------------------------------------------------------------------------------

  /** @param {ClipRecord} rec */
  _listen(rec) {
    const v = rec.el;
    const loaded = () => {
      if (rec.status === 'loading' || rec.status === 'idle') rec.status = 'loaded';
      rec.retries = 0;
    };
    this.disposer.listen(v, 'loadeddata', loaded);
    this.disposer.listen(v, 'canplay', loaded);
    this.disposer.listen(v, 'playing', () => {
      if (v !== this.active && v !== this.shown && v !== this.fading) {
        v.pause(); // only the clips on screen may play
        return;
      }
      rec.playFailures = 0;
      rec.lastProgressAt = this.now();
      if (v === this.active) this._exitFallbackIfHealthy();
    });
    this.disposer.listen(v, 'timeupdate', () => this._noteProgress(rec, this.now()));
    this.disposer.listen(v, 'progress', () => {
      rec.lastDataAt = this.now();
    });
    this.disposer.listen(v, 'ended', () => {
      // `loop` normally prevents this: restart rather than freeze on the last frame.
      if (v !== this.active || !this.playing) return;
      v.currentTime = 0;
      this._play(v);
    });
    this.disposer.listen(v, 'error', () => this._onError(rec));
  }

  /**
   * @param {ClipRecord} rec
   * @param {number} now
   */
  _noteProgress(rec, now) {
    const t = rec.el.currentTime;
    if (t === rec.lastTime) return;
    rec.lastTime = t;
    if (rec.el.paused) return; // a seek moves currentTime without playing
    rec.lastProgressAt = now;
    rec.strikes = 0;
    if (rec.el === this.active) this._exitFallbackIfHealthy();
  }

  /** @param {ClipRecord} rec */
  _onError(rec) {
    if (this.destroyed) return;
    this.counts.errors++;
    if (rec.retryTimer) clearTimeout(rec.retryTimer);
    rec.retryTimer = null;
    const delays = MASCOT_TIMING.retryDelaysMs;
    if (rec.retries < delays.length) {
      rec.status = 'error';
      const delay = delays[rec.retries++];
      rec.retryTimer = setTimeout(() => {
        rec.retryTimer = null;
        if (!this.destroyed) this._reload(rec);
      }, delay);
    } else {
      rec.status = 'failed';
      this._warnOnce(rec.url, `mascot: clip ${rec.url} could not be loaded; showing its poster instead`);
    }
    if (rec.el === this.active) this._enterFallback(rec.status === 'failed' ? 'clip failed to load' : 'clip error, retrying');
  }

  /**
   * Load the clip again from a fresh URL (also gets past a bad cached response).
   * @param {ClipRecord} rec
   */
  _reload(rec) {
    this.counts.reloads++;
    rec.status = 'loading';
    rec.lastProgressAt = this.now();
    rec.lastTime = -1;
    const v = rec.el;
    v.preload = 'auto';
    v.setAttribute('src', safeUrl(`${rec.url}${rec.url.includes('?') ? '&' : '?'}r=${++this._retrySeq}`)); // restarts the load
    if (v === this.active && this.playing) this._play(v);
  }

  // -------------------------------------------------------------------------------------------
  // Fallback (poster frames)
  // -------------------------------------------------------------------------------------------

  /** @param {string} reason */
  _enterFallback(reason) {
    if (!this.fallback) this.counts.fallbacks++;
    this._setFallback({ reason });
  }

  _exitFallbackIfHealthy() {
    const v = this.active;
    if (!this.fallback || !v) return;
    const rec = this.records.get(v);
    if (v.paused || v.readyState < 2 || !rec || rec.status === 'failed' || rec.status === 'error') return;
    this._setFallback(null);
  }

  /** @param {{ reason: string } | null} fallback */
  _setFallback(fallback) {
    this.fallback = fallback;
    this.el.classList.toggle('is-fallback', !!fallback);
    if (fallback) this._showFrame();
  }

  /** Put the active clip's poster (or the static background) on the fallback frame, crossfading. */
  _showFrame() {
    if (this.frames.length < 2) return;
    const url = this.active ? this.records.get(this.active)?.url : null;
    const poster = posterUrl(url);
    const still = this.branding.static_background_url || null;
    const src = poster || still;
    const current = this.frames.find((f) => f.classList.contains('is-current')) || null;
    if (!src) {
      if (current) current.classList.remove('is-current');
      return;
    }
    if (current && current.dataset.for === src) return;
    const next = current === this.frames[0] ? this.frames[1] : this.frames[0];
    next.dataset.for = src;
    next.classList.toggle('is-still', src !== poster);
    next.setAttribute('src', safeUrl(src));
    next.classList.add('is-current');
    if (current) current.classList.remove('is-current');
  }

  /** @param {HTMLImageElement} img */
  _onFrameError(img) {
    if (this.destroyed) return;
    const still = this.branding.static_background_url;
    if (still && !img.classList.contains('is-still')) {
      // Last resort: the empty studio, so the stage never shows a hole where Aadhi should be.
      img.classList.add('is-still');
      img.setAttribute('src', safeUrl(still));
    } else {
      img.classList.remove('is-current');
    }
  }

  /**
   * @param {string} key
   * @param {string} message
   */
  _warnOnce(key, message) {
    if (this._warned.has(key)) return;
    this._warned.add(key);
    console.warn(message);
  }
}
