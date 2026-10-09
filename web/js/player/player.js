// @ts-check
/**
 * Aadhi lecture player (ARCHITECTURE §10). One Timeline, three modes:
 *   live     public watch page: narration audio is the master clock, analytics, interactive quiz/p5
 *   preview  studio preview: same as live without analytics
 *   render   driven by the MP4 renderer: states(i) / renderState({sceneIndex, t}) / renderIntro({t})
 *            render exactly schedule.sceneStateAt() on a transparent stage with transitions disabled
 *            and media/mascot/captions hidden (holes), and report the media rects in stage pixels.
 *
 *   const player = new Player(rootEl, { mode: 'live', analytics: { shareToken }, captions: true });
 *   await player.load(timeline);
 *   player.play(); player.pause(); player.seek(42); player.seekScene(3);
 *   const off = player.on('timeupdate', ({ t }) => ...);
 *   player.destroy();
 *
 * Event details worth knowing: 'scenechange' {index, scene} uses index -1 / scene null for the
 * intro; 'ended' {t, completed} has completed=false when the end was reached by a seek (only
 * continuous playback records the 'complete' analytics event). load() may be called again: the
 * player starts over (new analytics session). 'blocked' {t, reason: 'autoplay'} fires when the
 * browser refuses to start the narration without a user gesture: the player pauses itself (a 'pause'
 * event with reason 'blocked' follows), so the controls offer play again and that click is the
 * gesture.
 */

import { Disposer, h } from '../shared/dom.js';
import { loadPrism as libLoadPrism, renderTex as libRenderTex, texIdle as libTexIdle } from '../shared/libs.js';
import { clamp, num } from '../shared/format.js';
import { Clock, isBuffering, mediaMaster } from './clock.js';
import { Stage, stageRect } from './stage.js';
import { applyZones, computeZones, STAGE_HEIGHT, STAGE_WIDTH } from './layout.js';
import { conceptStateAt, normalizeExtraTimes, renderKeyAt, renderStateList, sceneIndexAt, sceneStateAt } from './schedule.js';
import { CaptionsView } from './captions.js';
import { MascotController } from './mascot.js';
import { IntroView, introStates } from './intro.js';
import { createSceneView } from './scenes/index.js';
import { BackgroundMusic, envelopeLevel, NarrationDeck, needsSpeechMeter, releaseMedia, SoundEffects, SpeechMeter } from './audio.js';
import { Analytics } from './analytics.js';
import { buildPanel, loadPanelModule } from './panel-loader.js';
import { ensureFonts } from './fonts.js';
import { mathSpans, texNode } from './richtext.js';

/** @typedef {import('../shared/types.js').Timeline} Timeline */
/** @typedef {import('../shared/types.js').TimedScene} TimedScene */
/** @typedef {import('../shared/types.js').SceneState} SceneState */
/** @typedef {import('../shared/types.js').PlayerMode} PlayerMode */
/** @typedef {import('../shared/types.js').RenderResult} RenderResult */
/** @typedef {import('../shared/types.js').StageRect} StageRect */
/** @typedef {import('./scenes/types.js').SceneView} SceneView */
/** @typedef {import('./scenes/types.js').FrameInfo} FrameInfo */
/** @typedef {import('./panel-loader.js').PanelFactory} PanelFactory */
/** @typedef {ReturnType<typeof buildPanel>} Panel */

export const PLAYER_EVENTS = Object.freeze([
  'ready', 'timeupdate', 'scenechange', 'play', 'pause', 'ended', 'quizanswer', 'error', 'ratechange',
  'volumechange', 'captionschange', 'buffering', 'blocked',
]);

/**
 * @typedef {object} PlayerDeps
 * @property {(latex: string, display: boolean) => Promise<Element>} [renderTex]
 * @property {() => Promise<unknown>} [texIdle]
 * @property {(language: string) => Promise<any>} [loadPrism]
 * @property {PanelFactory} [createPanel]
 * @property {(scene: TimedScene) => number[]} [panelStateTimes]   with createPanel: panel-internal state times
 * @property {() => number} [now]                         ms clock
 * @property {(cb: FrameRequestCallback) => number} [requestFrame]
 * @property {(id: number) => void} [cancelFrame]
 * @property {(body: any) => Promise<unknown>} [sendAnalytics]
 * @property {boolean} [waitForFonts]                     default true
 */

/**
 * @typedef {object} PlayerOptions
 * @property {PlayerMode} [mode]
 * @property {{ versionId?: number | null, shareToken?: string | null } | null} [analytics]
 * @property {boolean} [captions]           captions on (live/preview), default true
 * @property {'s' | 'm' | 'l'} [captionSize]
 * @property {boolean} [autoplay]
 * @property {number} [volume]              0..1
 * @property {boolean} [muted]
 * @property {number} [rate]
 * @property {string} [sandboxUrl]          p5 sandbox page (default /sandbox/p5)
 * @property {PlayerDeps} [deps]            injection points (tests, embedding)
 */

const SEEK_TRACK_MIN_DELTA = 0.5;
const NARRATION_DRIFT = 0.25;
const NARRATION_RESYNC_MS = 1000;
const NARRATION_RETRY_MS = 1000; // a rejected narration play() is retried at most this often
const LIVE_FONT_TIMEOUT_MS = 1500;
const RENDER_FONT_TIMEOUT_MS = 20000;
/** Hidden tabs get no animation frames: this timer keeps scene changes and narration going. */
export const BACKGROUND_TICK_MS = 250;

/** @returns {Promise<void>} */
function nextFrame() {
  return new Promise((resolve) => {
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(() => resolve());
    else setTimeout(resolve, 16);
  });
}

/**
 * Normalise whatever a panel's mediaRect() returns into stage pixels.
 * @param {any} value
 * @param {Stage} stage
 * @returns {StageRect | null}
 */
function toStageRect(value, stage) {
  if (!value) return null;
  if (typeof Element !== 'undefined' && value instanceof Element) return stage.rectOf(value);
  const width = num(value.width ?? value.w, NaN);
  const height = num(value.height ?? value.h, NaN);
  if (!(width >= 0 && height >= 0)) return null;
  if (typeof value.left === 'number' && typeof value.top === 'number' && typeof DOMRect !== 'undefined' && value instanceof DOMRect) {
    return stage.toStageRect(value); // client coordinates
  }
  return stageRect(num(value.x ?? value.left, 0), num(value.y ?? value.top, 0), width, height);
}

export class Player {
  /**
   * @param {HTMLElement} root
   * @param {PlayerOptions} [opts]
   */
  constructor(root, opts = {}) {
    /** @type {PlayerMode} */
    this.mode = opts.mode === 'render' || opts.mode === 'preview' ? opts.mode : 'live';
    this.opts = opts;
    this.deps = opts.deps || {};
    this.root = root;
    this.disposer = new Disposer();
    /** Listeners bound to the loaded timeline (disposed by _teardownTimeline, reusable). */
    this.timelineDisposer = new Disposer();
    /** @type {Map<string, Set<(detail: any) => void>>} */
    this.handlers = new Map();
    /** @type {Timeline | null} */
    this.timeline = null;
    this.duration = 0;
    this.transition = 0.4;
    this.sceneIndex = -2; // -2: nothing mounted, -1: intro
    /** @type {SceneView | null} */
    this.sceneView = null;
    /** @type {HTMLElement | null} */
    this.sceneRoot = null;
    /** @type {HTMLElement | null} */
    this.sideZone = null;
    /** @type {Panel | null} */
    this.panel = null;
    /** @type {SceneState | null} */
    this.state = null;
    this.volume = clamp(num(opts.volume, 1), 0, 1);
    this.muted = !!opts.muted;
    this.rate = clamp(num(opts.rate, 1), 0.25, 4);
    this.captionsOn = opts.captions !== false;
    this.ended = false;
    this.destroyed = false;
    this.loaded = false;
    this._loadSeq = 0;
    this.sessionStarted = false;
    this._seeked = true;
    this._sceneSeeked = true;
    this._lastNarrationFix = 0;
    this._narrationErrorScene = -1;
    /** ms (Date.now) of the last rejected narration play(); other than autoplay blocks, retried at most once a second */
    this._narrationRejectedAt = -Infinity;
    this._meterOn = false;
    this._meterTried = false;
    this._captionLift = 0;
    this.buffering = false;
    /** @type {ReturnType<typeof setInterval> | null} */
    this._bgTimer = null;
    /** @type {Map<string, HTMLVideoElement | HTMLImageElement>} */
    this._preloaded = new Map();
    /** @type {Promise<unknown>} */
    this._renderChain = Promise.resolve();
    /** @type {PanelFactory | null} */
    this._panelFactory = null;
    /** @type {((scene: TimedScene) => number[]) | null} */
    this._panelTimes = null;
    this._reducedMotion =
      typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    /** @type {HTMLDivElement} */
    this.el = h('div', {
      class: ['aadhi-player', `mode-${this.mode}`, { 'render-mode': this.mode === 'render', 'reduced-motion': this._reducedMotion }],
    });
    root.appendChild(this.el);
    this.stage = new Stage(this.el);
    this.layers = {
      bg: h('div', { class: 'ap-layer ap-layer--bg' }),
      scene: h('div', { class: 'ap-layer ap-layer--scene' }),
      captions: h('div', { class: 'ap-layer ap-layer--captions' }),
      intro: h('div', { class: 'ap-layer ap-layer--intro' }),
    };
    this.stage.el.append(this.layers.bg, this.layers.scene, this.layers.intro, this.layers.captions);
    this.clock = new Clock({
      now: this.deps.now,
      requestFrame: this.deps.requestFrame,
      cancelFrame: this.deps.cancelFrame,
      ticking: this.mode !== 'render',
    });
    /** @type {MascotController | null} */
    this.mascot = null;
    /** @type {CaptionsView | null} */
    this.captions = null;
    /** @type {IntroView | null} */
    this.intro = null;
    /** @type {NarrationDeck | null} */
    this.narration = null;
    /** @type {SpeechMeter | null} */
    this.meter = null;
    /** @type {SoundEffects | null} */
    this.sfx = null;
    /** @type {BackgroundMusic | null} */
    this.bgm = null;
    /** @type {Analytics | null} */
    this.analytics = null;
  }

  // -------------------------------------------------------------------------------------------
  // Events
  // -------------------------------------------------------------------------------------------

  /**
   * Subscribe to an event; returns an unsubscribe function.
   * @param {string} event   see PLAYER_EVENTS
   * @param {(detail: any) => void} fn
   * @returns {() => void}
   */
  on(event, fn) {
    let set = this.handlers.get(event);
    if (!set) {
      set = new Set();
      this.handlers.set(event, set);
    }
    set.add(fn);
    return () => this.off(event, fn);
  }

  /**
   * @param {string} event
   * @param {(detail: any) => void} fn
   */
  off(event, fn) {
    const set = this.handlers.get(event);
    if (set) set.delete(fn);
  }

  /**
   * @param {string} event
   * @param {any} [detail]
   */
  _emit(event, detail) {
    const set = this.handlers.get(event);
    if (!set) return;
    for (const fn of [...set]) {
      try {
        fn(detail);
      } catch (err) {
        console.error(`player ${event} handler failed`, err);
      }
    }
  }

  /**
   * Report a non-fatal problem.
   * @param {string} message
   * @param {unknown} [error]
   */
  _error(message, error) {
    if (this.handlers.get('error')?.size) this._emit('error', { message, error });
    else console.warn(`player: ${message}`, error || '');
  }

  // -------------------------------------------------------------------------------------------
  // Loading
  // -------------------------------------------------------------------------------------------

  /**
   * Load a timeline: builds the stage, preloads branding and the first scenes.
   * @param {Timeline} timeline
   */
  async load(timeline) {
    if (this.destroyed) throw new Error('player destroyed');
    if (!timeline || !Array.isArray(timeline.scenes) || !Number.isFinite(timeline.total_duration)) {
      throw new TypeError('invalid timeline: expected {scenes: [...], total_duration: number}');
    }
    const seq = ++this._loadSeq; // a newer load() supersedes this one while it awaits
    if (this.timeline) this._teardownTimeline();
    this.timeline = timeline;
    this.duration = Math.max(0, num(timeline.total_duration, 0));
    this.transition = Math.max(0, num(timeline.transition_seconds, 0.4));
    const lang = timeline.board_language || timeline.language || 'en';
    this.el.lang = lang;
    const branding = timeline.branding || {};
    const live = this.mode !== 'render';

    this.mascot = new MascotController(this.layers.bg, branding, {
      mode: this.mode,
      reducedMotion: this._reducedMotion,
      positions: timeline.scenes.map((s) => s.layout?.mascot_position || 'left'), // only these clips load eagerly
      now: this.deps.now,
    });
    this.captions = new CaptionsView(this.layers.captions, { enabled: live && this.captionsOn, size: this.opts.captionSize });
    if (timeline.intro && num(timeline.intro.duration, 0) > 0) {
      this.intro = new IntroView(this.layers.intro, timeline.intro, { mode: this.mode });
    }
    if (live) {
      this.narration = new NarrationDeck();
      this.sfx = new SoundEffects({ tick: branding.tick_url, ding: branding.ding_url });
      this.bgm = new BackgroundMusic(branding.bgm_url, num(branding.bgm_volume, 0.06));
      // WebAudio meter only as the fallback for narration without a build-time loudness envelope
      if (needsSpeechMeter(timeline.scenes)) this.meter = new SpeechMeter();
      this._applyVolume();
      this.narration.setRate(this.rate);
      this.clock.setRate(this.rate);
      const a = this.opts.analytics;
      if (a && (a.shareToken || a.versionId !== undefined)) {
        this.analytics = new Analytics({ shareToken: a.shareToken, versionId: a.versionId, send: this.deps.sendAnalytics });
      }
      this.timelineDisposer.add(this.clock.onTick((t) => this._renderFrame(t)));
      if (typeof document !== 'undefined') {
        this.timelineDisposer.listen(document, 'visibilitychange', () => {
          if (document.visibilityState === 'visible') {
            if (this.meter) this.meter.resume();
            if (this.mascot) this.mascot.tick({ force: true }); // restart a clip the browser paused
          }
          this._syncBackgroundTicker();
        });
      }
    }
    const panels = await loadPanelModule(
      this.deps.createPanel ? { createPanel: this.deps.createPanel, panelStateTimes: this.deps.panelStateTimes } : undefined,
    );
    if (this.destroyed || seq !== this._loadSeq) return;
    this._panelFactory = panels.createPanel;
    this._panelTimes = panels.panelStateTimes;
    if (this.deps.waitForFonts !== false) {
      await ensureFonts([lang, timeline.language], { timeoutMs: live ? LIVE_FONT_TIMEOUT_MS : RENDER_FONT_TIMEOUT_MS });
    }
    if (this.destroyed || seq !== this._loadSeq) return;
    this.loaded = true;
    this.clock.seek(0);
    this._seeked = true;
    if (live) this._renderFrame(0);
    this._emit('ready', { duration: this.duration, scenes: timeline.scenes.length });
    if (this.opts.autoplay && live) this.play();
  }

  /**
   * Remove everything built for the current timeline and reset the per-timeline state, so a
   * re-load starts exactly like a first load (used by re-load and destroy).
   */
  _teardownTimeline() {
    this.timelineDisposer.dispose();
    this.clock.pause();
    this.clock.setMaster(null);
    this.loaded = false;
    this._syncBackgroundTicker();
    this._setBuffering(false);
    this._unmountScene();
    for (const part of [this.mascot, this.captions, this.intro, this.narration, this.meter, this.sfx, this.bgm]) {
      if (part) part.destroy();
    }
    if (this.analytics) this.analytics.destroy();
    for (const el of this._preloaded.values()) if (el instanceof HTMLVideoElement) releaseMedia(el);
    this._preloaded.clear();
    this.mascot = this.captions = this.intro = this.narration = this.meter = this.sfx = this.bgm = this.analytics = null;
    this.sceneIndex = -2;
    this.state = null;
    this.ended = false;
    this.sessionStarted = false;
    this._meterTried = false;
    this._meterOn = false;
    this._seeked = true;
    this._sceneSeeked = true;
    this._narrationErrorScene = -1;
    this._narrationRejectedAt = -Infinity;
    this.timeline = null;
  }

  // -------------------------------------------------------------------------------------------
  // Transport
  // -------------------------------------------------------------------------------------------

  /** Current absolute time (seconds). */
  get currentTime() {
    return Math.min(this.clock.time(), this.duration);
  }

  /** @returns {boolean} */
  get paused() {
    return !this.clock.playing;
  }

  /** Start or resume playback (call from a user gesture the first time: audio unlock). */
  play() {
    if (!this.loaded || this.destroyed || this.mode === 'render') return;
    if (this.clock.playing) return;
    if (this.ended || this.clock.time() >= this.duration) {
      this.ended = false;
      this.seek(0, { track: false });
    }
    this.clock.play();
    this._narrationRejectedAt = -Infinity; // a (user) play: start the narration at once
    if (this.meter) {
      if (!this._meterTried && this.narration) {
        this._meterTried = true;
        this.meter.attach(this.narration.elements).then((ok) => {
          this._meterOn = ok;
        });
      } else {
        this.meter.resume();
      }
    }
    if (this.bgm) this.bgm.setPlaying(true);
    if (this.mascot) this.mascot.setPlaying(true);
    if (this.sceneView && this.sceneView.setPlaying) this.sceneView.setPlaying(true, this.rate);
    const t = this.clock.time();
    this._track(this.sessionStarted ? 'resume' : 'session_start', t);
    if (!this.sessionStarted) {
      this.sessionStarted = true;
      if (this.sceneIndex >= 0) this._track('scene_enter', t);
    }
    this._renderFrame(t);
    this._syncBackgroundTicker();
    this._emit('play', { t });
  }

  /**
   * Pause playback.
   * @param {{ reason?: string }} [opts]  reason (internal, e.g. 'blocked'): added to the 'pause' event
   *   and its analytics record
   */
  pause(opts = {}) {
    if (!this.clock.playing) return;
    this.clock.pause();
    this._syncBackgroundTicker();
    this._pauseOutputs();
    const t = this.clock.time();
    const reason = opts && typeof opts.reason === 'string' ? opts.reason : null;
    // the analytics server accepts only the 'blocked' tag on a pause (any other reason is not recorded)
    this._track('pause', t, reason === 'blocked' ? { reason } : undefined);
    this._renderFrame(t);
    this._emit('pause', reason ? { t, reason } : { t });
  }

  /** Toggle play/pause. */
  toggle() {
    if (this.clock.playing) this.pause();
    else this.play();
  }

  /**
   * While playing in a hidden tab, drive frames from a slow timer (rAF does not run there), so the
   * lecture keeps advancing scene by scene with its narration, like a background video.
   */
  _syncBackgroundTicker() {
    const hidden = typeof document !== 'undefined' && document.visibilityState === 'hidden';
    const want = this.mode !== 'render' && this.loaded && !this.destroyed && this.clock.playing && hidden;
    if (want && this._bgTimer === null) {
      this._bgTimer = setInterval(() => {
        if (this.clock.playing) this._renderFrame(this.clock.time());
        else this._syncBackgroundTicker();
      }, BACKGROUND_TICK_MS);
    } else if (!want && this._bgTimer !== null) {
      clearInterval(this._bgTimer);
      this._bgTimer = null;
    }
  }

  /**
   * Narration is waiting for data while it should be playing (shown as a spinner; the clock waits).
   * @param {boolean} on
   */
  _setBuffering(on) {
    if (on === this.buffering) return;
    this.buffering = on;
    this.el.classList.toggle('is-buffering', on);
    this._emit('buffering', { buffering: on });
  }

  _pauseOutputs() {
    this._setBuffering(false);
    if (this.narration) this.narration.pauseAll();
    if (this.bgm) this.bgm.setPlaying(false);
    if (this.mascot) this.mascot.setPlaying(false);
    if (this.sceneView && this.sceneView.setPlaying) this.sceneView.setPlaying(false, this.rate);
  }

  /**
   * Seek to an absolute time (clamped to [0, duration]).
   * @param {number} seconds
   * @param {{ track?: boolean, from?: number }} [opts]
   *   track: record a 'seek' analytics event (default true); from: origin of the seek for analytics
   *   (e.g. where a scrubber drag started)
   */
  seek(seconds, opts = {}) {
    if (!this.loaded || this.destroyed) return;
    const from = Number.isFinite(opts.from) ? /** @type {number} */ (opts.from) : this.clock.time();
    const t = clamp(num(seconds, 0), 0, this.duration);
    this.clock.seek(t);
    if (t < this.duration) this.ended = false;
    this._seeked = true;
    this._sceneSeeked = true;
    if (this.narration) this.narration.pauseAll();
    if (opts.track !== false && Math.abs(t - from) >= SEEK_TRACK_MIN_DELTA) {
      this._track('seek', t, { from: Math.round(from * 1000) / 1000, to: Math.round(t * 1000) / 1000 });
    }
    if (this.mode !== 'render') this._renderFrame(t);
  }

  /**
   * Seek to the start of a scene.
   * @param {number} index
   */
  seekScene(index) {
    const scenes = this.timeline ? this.timeline.scenes : [];
    if (!scenes.length) return;
    const i = clamp(Math.round(num(index, 0)), 0, scenes.length - 1);
    this.seek(scenes[i].start);
  }

  /** @param {number} rate */
  setRate(rate) {
    this.rate = clamp(num(rate, 1), 0.25, 4);
    this.clock.setRate(this.rate);
    if (this.narration) this.narration.setRate(this.rate);
    this._emit('ratechange', { rate: this.rate });
  }

  /** @param {number} volume 0..1 */
  setVolume(volume) {
    this.volume = clamp(num(volume, 1), 0, 1);
    if (this.volume > 0 && this.muted) this.muted = false;
    this._applyVolume();
  }

  /** @param {boolean} muted */
  setMuted(muted) {
    this.muted = !!muted;
    this._applyVolume();
  }

  _applyVolume() {
    if (this.narration) this.narration.setVolume(this.volume, this.muted);
    if (this.sfx) this.sfx.setVolume(this.volume, this.muted);
    if (this.bgm) this.bgm.setVolume(this.volume, this.muted);
    if (this.intro) this.intro.setVolume(this.volume, this.muted);
    this._emit('volumechange', { volume: this.volume, muted: this.muted });
  }

  /** @param {boolean} on */
  setCaptions(on) {
    this.captionsOn = !!on;
    if (this.captions) this.captions.setEnabled(this.captionsOn && this.mode !== 'render');
    this._emit('captionschange', { enabled: this.captionsOn, size: this.captions ? this.captions.size : 'm' });
  }

  /**
   * Raise the captions (stage pixels) so an overlay such as the visible control bar does not cover
   * them. Live/preview only; render mode never shows captions.
   * @param {number} stagePx
   */
  setCaptionLift(stagePx) {
    const lift = this.mode === 'render' ? 0 : Math.max(0, Math.round(num(stagePx, 0)));
    if (lift === this._captionLift) return;
    this._captionLift = lift;
    this.el.style.setProperty('--caption-lift', `${lift}px`);
  }

  /** @param {'s' | 'm' | 'l'} size */
  setCaptionSize(size) {
    if (this.captions) this.captions.setSize(size);
    this._emit('captionschange', { enabled: this.captionsOn, size: this.captions ? this.captions.size : size });
  }

  // -------------------------------------------------------------------------------------------
  // Frame rendering (live / preview)
  // -------------------------------------------------------------------------------------------

  /**
   * Render the stage for absolute time t.
   * @param {number} rawT
   */
  _renderFrame(rawT) {
    if (!this.timeline || this.destroyed) return;
    let t = rawT;
    if (this.duration > 0 && t >= this.duration) {
      t = this.duration;
      if (this.clock.playing) {
        // Only continuous playback completes the lecture; a seek (scrubber, End key) to the end
        // just stops there.
        const completed = !this._seeked;
        this._stopAtEnd();
        this._renderFrame(t); // the final frame, paused (the clock no longer plays: no recursion)
        this._finishEnd(completed);
        return;
      }
    }
    const seeked = this._seeked;
    this._seeked = false;
    /** @type {FrameInfo} */
    const frame = { playing: this.clock.playing, rate: this.rate, seeked };
    const inIntro = !!this.intro && this.intro.covers(t);
    if (this.intro) this.intro.update(t, frame);
    if (inIntro) {
      if (this.sceneIndex !== -1) this._enterScene(-1, seeked);
      if (this.mascot) this.mascot.setVisible(false);
      if (this.captions) this.captions.show(null);
      if (this.bgm) this.bgm.setDucked(false);
      this._setBuffering(false);
      this._emit('timeupdate', { t, duration: this.duration, sceneIndex: -1, sceneT: t });
      return;
    }
    const scenes = this.timeline.scenes;
    if (!scenes.length) return;
    const i = Math.max(0, sceneIndexAt(this.timeline, t));
    if (i !== this.sceneIndex) this._enterScene(i, seeked);
    const scene = scenes[i];
    const sceneT = Math.max(0, t - scene.start);
    const state = sceneStateAt(scene, sceneT);
    this.state = state;
    const sceneFrame = { ...frame, seeked: frame.seeked || this._sceneSeeked };
    this._sceneSeeked = false;
    if (this.sceneRoot) {
      const fade = this.transition > 0 && !this._reducedMotion ? clamp(sceneT / this.transition, 0, 1) : 1;
      this.sceneRoot.style.opacity = fade >= 1 ? '' : fade.toFixed(3);
    }
    if (this.sceneView) {
      try {
        this.sceneView.update(sceneT, state, sceneFrame);
      } catch (err) {
        this._error('scene update failed', err);
      }
    }
    this._updatePanel(sceneT, state);
    if (this.captions) this.captions.show(state.caption);
    this._syncNarration(scene, sceneT, sceneFrame);
    if (this.mascot) {
      let level = 0;
      // build-time envelope (deterministic, any audio host) > WebAudio meter > synthetic bob while speaking
      const envelope = frame.playing ? envelopeLevel(scene, sceneT) : null;
      if (envelope !== null) {
        level = envelope;
      } else if (frame.playing && state.phase === 'beat') {
        const measured = this._meterOn && this.meter ? this.meter.level() : null;
        level = measured !== null ? measured : 0.45 + 0.35 * Math.abs(Math.sin(sceneT * 7.3));
      }
      this.mascot.setLevel(level);
      this.mascot.setState(state.mascotState, state.mascotCue);
      this.mascot.tick(); // watchdog, throttled inside
    }
    if (this.bgm) this.bgm.setDucked(state.phase === 'beat');
    this._emit('timeupdate', { t, duration: this.duration, sceneIndex: i, sceneT });
  }

  /**
   * @param {number} sceneT
   * @param {SceneState} state
   */
  _updatePanel(sceneT, state) {
    if (!this.sideZone) return;
    this.sideZone.classList.toggle('is-visible', state.panelVisible);
    if (this.panel) this.panel.update(sceneT, state);
  }

  /**
   * Keep the scene narration in step with the clock (live/preview).
   * @param {TimedScene} scene
   * @param {number} sceneT
   * @param {FrameInfo} frame
   */
  _syncNarration(scene, sceneT, frame) {
    const deck = this.narration;
    const el = deck && deck.current ? deck.current.el : null;
    if (!el) {
      this._setBuffering(false);
      return;
    }
    const local = sceneT - num(scene.audio_offset, 0);
    const known = num(scene.audio_duration, 0) > 0 ? num(scene.audio_duration, 0) : num(el.duration, Infinity);
    const inside = local >= 0 && local < known - 0.02;
    this._setBuffering(frame.playing && inside && isBuffering(el));
    if (frame.playing && inside) {
      const now = Date.now();
      const drift = Math.abs(el.currentTime - local);
      const canSeek = el.readyState >= 1 && !el.seeking;
      if (canSeek && (frame.seeked || (!this.clock.mastered && drift > NARRATION_DRIFT && now - this._lastNarrationFix > NARRATION_RESYNC_MS))) {
        this._lastNarrationFix = now;
        try {
          el.currentTime = Math.max(0, local);
        } catch {
          /* not seekable yet */
        }
      }
      if (el.paused && now - this._narrationRejectedAt >= NARRATION_RETRY_MS) {
        el.playbackRate = this.rate;
        const p = el.play();
        if (p && typeof p.catch === 'function') {
          p.catch((err) => {
            if (err && err.name === 'AbortError') return;
            this._narrationRejectedAt = Date.now();
            if (err && err.name === 'NotAllowedError') {
              // Autoplay refused (no user gesture yet, e.g. after a back/forward-cache restore): pause, so
              // the controls offer play again and that click is the gesture. Never retried per frame.
              if (!this.clock.playing) return;
              this._emit('blocked', { t: this.clock.time(), reason: 'autoplay' });
              this.pause({ reason: 'blocked' });
              return;
            }
            this._error('narration could not start', err);
          });
        }
      }
    } else {
      if (!el.paused) el.pause();
      if (frame.seeked && el.readyState >= 1) {
        try {
          el.currentTime = clamp(local, 0, Number.isFinite(known) ? known : Math.max(0, local));
        } catch {
          /* ignore */
        }
      }
    }
  }

  /** The playing clock reached the end: stop the clock and every output there. */
  _stopAtEnd() {
    this.clock.pause();
    this._syncBackgroundTicker();
    this.clock.seek(this.duration);
    this.ended = true;
    this._pauseOutputs();
  }

  /**
   * After the final frame: record completion (only when the end was reached by continuous
   * playback, never by a seek) and announce the end.
   * @param {boolean} completed
   */
  _finishEnd(completed) {
    if (completed) {
      if (this.sceneIndex >= 0) this._track('scene_complete', this.duration, undefined, this.sceneIndex);
      this._track('complete', this.duration);
    }
    if (this.analytics) this.analytics.flush();
    this._emit('ended', { t: this.duration, completed });
  }

  // -------------------------------------------------------------------------------------------
  // Scenes
  // -------------------------------------------------------------------------------------------

  _unmountScene() {
    if (this.sceneView) {
      try {
        this.sceneView.destroy();
      } catch (err) {
        console.warn('scene destroy failed', err);
      }
    }
    if (this.panel) this.panel.destroy();
    if (this.sceneRoot) this.sceneRoot.remove();
    this.sceneView = null;
    this.panel = null;
    this.sideZone = null;
    this.sceneRoot = null;
  }

  /**
   * Mount scene i (-1 = intro: nothing mounted).
   * @param {number} i
   * @param {boolean} [discontinuity]  entered by a seek (not by playing through the previous scene)
   */
  _enterScene(i, discontinuity = true) {
    const timeline = /** @type {Timeline} */ (this.timeline);
    const prev = this.sceneIndex;
    if (this.clock.playing && !discontinuity && prev >= 0 && i === prev + 1) {
      this._track('scene_complete', timeline.scenes[i].start, undefined, prev);
    }
    this._unmountScene();
    this.sceneIndex = i;
    this._sceneSeeked = true;
    if (i < 0) {
      if (this.narration) {
        this.clock.setMaster(null);
        this._preload(0); // the first scene's narration/media/TeX load while the intro plays
      }
      this._emit('scenechange', { index: -1, scene: null }); // the intro (no scene)
      return;
    }
    const scene = timeline.scenes[i];
    const hasPanel = !!(scene.side_panel && scene.side_panel.panel) && scene.layout?.show_side_panel !== false;
    const zones = computeZones(scene.layout?.mascot_position, { sidePanel: hasPanel });
    /** @type {HTMLDivElement} */
    const root = h('div', {
      class: ['ap-scene', `ap-scene--${String(scene.type).replace(/[^a-z0-9_-]/gi, '')}`, { 'is-fullscreen-media': !!scene.layout?.fullscreen_media }],
      dataset: { sceneIndex: String(i), type: String(scene.type) },
    });
    applyZones(root, zones);
    this.layers.scene.appendChild(root);
    this.sceneRoot = root;
    /** @type {import('./scenes/types.js').SceneContext} */
    const ctx = {
      mode: this.mode,
      timeline,
      scene,
      sceneIndex: i,
      zones,
      renderTex: this.deps.renderTex,
      loadPrism: this.deps.loadPrism || libLoadPrism,
      emit: (event, detail) => this._onSceneEvent(event, detail),
      playSound: (name) => {
        if (this.sfx) this.sfx.play(name);
      },
      takePreloaded: (url) => this._takePreloaded(url),
      sandboxUrl: this.opts.sandboxUrl,
      now: this.deps.now,
    };
    try {
      this.sceneView = createSceneView(ctx);
      root.appendChild(this.sceneView.el);
    } catch (err) {
      this._error(`scene ${scene.scene_id} failed to render`, err);
      this.sceneView = null;
    }
    if (hasPanel && scene.side_panel) {
      /** @type {HTMLDivElement} */
      const side = h('div', { class: 'ap-zone ap-zone--side' });
      root.appendChild(side);
      this.sideZone = side;
      this.panel = buildPanel(
        this._panelFactory || ((c) => ({ el: c, update() {}, destroy() {} })),
        side,
        scene.side_panel,
        /** @type {any} */ ({ mode: this.mode, timeline, sceneIndex: i, conceptState: conceptStateAt(timeline, i), stageEl: this.stage.el }),
        (err) => this._error('side panel failed', err),
      );
    }
    const view = this.sceneView;
    if (view) {
      // A view entered while the lecture plays (play-through or seek) must start running at once
      // (e.g. the p5 sandbox is told 'resume', not 'pause', when it reports ready).
      if (view.setPlaying) {
        try {
          view.setPlaying(this.clock.playing, this.rate);
        } catch (err) {
          this._error('scene setPlaying failed', err);
        }
      }
      if (view.layout) view.layout();
      view.ready().then(
        () => {
          if (this.sceneView === view && view.layout && this.mode !== 'render') view.layout();
        },
        () => {},
      );
    }
    if (this.mascot) {
      this.mascot.setVisible(true);
      this.mascot.setPosition(scene.layout?.mascot_position || 'left', { origin: zones.mascotOrigin });
      this.mascot.mountCue(root, scene.layout?.mascot_position); // cue bubble: in the scene layer (screenshotted)
    }
    if (this.narration) {
      const el = this.narration.use(i, scene.audio_url);
      if (el && this._narrationErrorScene !== i) {
        el.onerror = () => {
          if (this._narrationErrorScene === i) return;
          this._narrationErrorScene = i;
          this._error(`narration audio for scene ${scene.scene_id} failed to load`);
        };
      }
      this.clock.setMaster(el ? mediaMaster(el, scene.start + num(scene.audio_offset, 0)) : null);
      this._preload(i + 1);
    }
    if (this.sessionStarted) this._track('scene_enter', scene.start, undefined, i);
    this._emit('scenechange', { index: i, scene });
  }

  /**
   * Scene views report learner interactions here.
   * @param {string} event
   * @param {any} detail
   */
  _onSceneEvent(event, detail) {
    if (event === 'quizanswer') {
      const t = this.clock.time();
      this._track('quiz_answer', t, { choice: detail.choice, correct: !!detail.correct }, detail.sceneIndex);
    }
    this._emit(event, detail);
  }

  /**
   * Preload a scene's narration, media and TeX (live/preview).
   * @param {number} i
   */
  _preload(i) {
    const scene = this.timeline ? this.timeline.scenes[i] : null;
    if (!scene) return;
    if (this.narration) this.narration.preload(i, scene.audio_url);
    const media = scene.media;
    if (media && media.url && !this._preloaded.has(media.url)) {
      if (media.kind === 'video') {
        /** @type {HTMLVideoElement} */
        const v = h('video', { src: media.url, preload: 'auto', playsinline: true });
        v.muted = true;
        this._preloaded.set(media.url, v);
      } else {
        this._warmImage(media.url);
      }
    }
    for (const fig of Object.values(scene.figures || {})) if (fig && fig.url) this._warmImage(fig.url);
    if (scene.poster && scene.poster.url) this._warmImage(scene.poster.url);
    if (!this.deps.renderTex) this._warmTex(scene);
    // keep the preload map small: drop entries not used by the current or next scene
    if (this._preloaded.size > 6) {
      const keep = new Set([scene.media?.url, this.timeline?.scenes[this.sceneIndex]?.media?.url]);
      for (const [url, el] of this._preloaded) {
        if (!keep.has(url)) {
          if (el instanceof HTMLVideoElement) releaseMedia(el);
          this._preloaded.delete(url);
        }
      }
    }
  }

  /**
   * Convert a scene's TeX ahead of time (cached in richtext.texNode), so MathJax is loaded and the
   * board appears without a flash of TeX source.
   * @param {TimedScene} scene
   */
  _warmTex(scene) {
    const warm = (/** @type {string} */ tex, /** @type {boolean} */ display) => {
      texNode(tex, display, libRenderTex).catch(() => {});
    };
    for (const item of scene.board || []) {
      if (item.kind === 'formula' && item.latex) warm(item.latex, true);
      for (const v of item.variables || []) warm(v.symbol_latex, false);
      const texts = [item.text, item.term, item.justification, item.caption, ...(item.headers || []), ...(item.rows || []).flat()];
      for (const text of texts) for (const tex of mathSpans(text)) warm(tex, false);
    }
    const quiz = scene.quiz;
    if (quiz) for (const text of [quiz.question, quiz.explanation, ...quiz.options]) for (const tex of mathSpans(text)) warm(tex, false);
  }

  /** @param {string} url */
  _warmImage(url) {
    if (this._preloaded.has(url)) return;
    /** @type {HTMLImageElement} */
    const img = h('img', { src: url, alt: '', decoding: 'async' });
    this._preloaded.set(url, img);
  }

  /**
   * Hand a preloaded video to a scene view (it then owns and releases it).
   * @param {string} url
   * @returns {HTMLVideoElement | null}
   */
  _takePreloaded(url) {
    const el = this._preloaded.get(url);
    if (el instanceof HTMLVideoElement) {
      this._preloaded.delete(url);
      return el;
    }
    return null;
  }

  // -------------------------------------------------------------------------------------------
  // Analytics
  // -------------------------------------------------------------------------------------------

  /**
   * @param {string} event
   * @param {number} t            absolute seconds
   * @param {Record<string, unknown>} [data]
   * @param {number} [sceneIndex] defaults to the scene playing at `t` (not the mounted one: a seek is
   *                              tracked before the target scene is mounted); -1 during the intro
   */
  _track(event, t, data, sceneIndex) {
    if (!this.analytics || !this.timeline) return;
    const idx = sceneIndex ?? sceneIndexAt(this.timeline, t);
    const scene = idx >= 0 ? this.timeline.scenes[idx] : null;
    this.analytics.track(event, {
      t,
      sceneId: scene ? scene.scene_id : null,
      sceneT: scene ? clamp(t - scene.start, 0, scene.duration) : null,
      data,
    });
  }

  // -------------------------------------------------------------------------------------------
  // Render mode
  // -------------------------------------------------------------------------------------------

  /**
   * Render-mode state list of a scene: [{t, key}] (scene-relative t, distinct visual states).
   * @param {number} sceneIndex
   * @returns {{t: number, key: string}[]}
   */
  states(sceneIndex) {
    const scene = this._sceneAt(sceneIndex);
    return renderStateList(scene, this._extraTimes(scene));
  }

  /**
   * Side-panel state change times (panels' panelStateTimes), normalised; [] without a panel.
   * @param {TimedScene} scene
   * @returns {number[]}
   */
  _extraTimes(scene) {
    if (!this._panelTimes || !scene.side_panel || scene.layout?.show_side_panel === false) return [];
    try {
      return normalizeExtraTimes(scene, this._panelTimes(scene));
    } catch (err) {
      this._error('panelStateTimes failed', err);
      return [];
    }
  }

  /**
   * Render-mode state list of the intro: [{t, key}] (absolute t).
   * @returns {{t: number, key: string}[]}
   */
  introStates() {
    return introStates(this.timeline ? this.timeline.intro : null);
  }

  /** @param {number} sceneIndex */
  _sceneAt(sceneIndex) {
    const scenes = this.timeline ? this.timeline.scenes : [];
    const scene = scenes[sceneIndex];
    if (!scene) throw new RangeError(`no scene ${sceneIndex}`);
    return scene;
  }

  /**
   * Render exactly sceneStateAt(scene, t) and resolve once the frame is stable (TeX, images, fonts,
   * panel ready, two animation frames). Calls are serialised.
   * @param {{ sceneIndex: number, t: number }} req   t is scene-relative
   * @returns {Promise<RenderResult>}
   */
  renderState(req) {
    const job = this._renderChain.then(() => this._renderStateNow(req));
    this._renderChain = job.catch(() => undefined);
    return job;
  }

  /**
   * @param {{ sceneIndex: number, t: number }} req
   * @returns {Promise<RenderResult>}
   */
  async _renderStateNow({ sceneIndex, t }) {
    if (!this.loaded) throw new Error('timeline not loaded');
    const scene = this._sceneAt(sceneIndex);
    const sceneT = clamp(num(t, 0), 0, Math.max(0, scene.duration));
    if (this.intro) this.intro.renderAt(Infinity);
    if (this.sceneIndex !== sceneIndex) this._enterScene(sceneIndex);
    const state = sceneStateAt(scene, sceneT);
    this.state = state;
    if (this.sceneRoot) this.sceneRoot.style.opacity = '';
    const frame = { playing: false, rate: 1, seeked: true };
    if (this.sceneView) this.sceneView.update(sceneT, state, frame);
    this._updatePanel(sceneT, state);
    if (this.mascot) this.mascot.setState(state.mascotState, state.mascotCue); // the cue bubble is in the screenshot
    await this._settle();
    const view = this.sceneView;
    const mediaEl = view && view.mediaElement ? view.mediaElement() : null;
    let panelRect = null;
    if (this.panel && state.panelVisible && this.panel.mediaRect) {
      try {
        panelRect = toStageRect(this.panel.mediaRect(), this.stage);
      } catch (err) {
        this._error('panel mediaRect failed', err);
      }
    }
    return {
      state_key: renderKeyAt(scene, sceneT, this._extraTimes(scene)),
      media_rect: mediaEl ? this.stage.rectOf(mediaEl) : null,
      panel_media_rect: panelRect,
      media_fit: view && view.mediaFit && mediaEl ? view.mediaFit() : null,
    };
  }

  /**
   * Render the intro at absolute time t (title cards at full opacity, transparent background).
   * The whole stage is reported as the media rect: ffmpeg composites the logo video / background.
   * @param {{ t: number }} req
   * @returns {Promise<RenderResult>}
   */
  renderIntro(req) {
    const job = this._renderChain.then(async () => {
      if (!this.loaded) throw new Error('timeline not loaded');
      if (this.sceneIndex !== -1) this._enterScene(-1);
      const key = this.intro ? this.intro.renderAt(num(req.t, 0)) : 'intro-none';
      await this._settle();
      return {
        state_key: key,
        media_rect: this.intro ? stageRect(0, 0, STAGE_WIDTH, STAGE_HEIGHT) : null,
        panel_media_rect: null,
        media_fit: this.intro ? /** @type {'cover'} */ ('cover') : null,
      };
    });
    this._renderChain = job.catch(() => undefined);
    return job;
  }

  /** Wait until the current frame is stable (render mode readiness contract). */
  async _settle() {
    const waits = [];
    if (this.sceneView) waits.push(this.sceneView.ready());
    if (this.panel && this.panel.ready) waits.push(this.panel.ready());
    await Promise.allSettled(waits);
    const texIdle = this.deps.texIdle || libTexIdle;
    await texIdle();
    const imgs = /** @type {HTMLImageElement[]} */ (Array.from(this.stage.el.querySelectorAll('img')));
    await Promise.allSettled(imgs.map((img) => (img.isConnected && typeof img.decode === 'function' ? img.decode() : Promise.resolve())));
    const fonts = typeof document !== 'undefined' ? /** @type {any} */ (document).fonts : null;
    if (fonts && fonts.ready) await fonts.ready;
    if (this.sceneView && this.sceneView.layout) this.sceneView.layout();
    await nextFrame();
    await nextFrame();
  }

  // -------------------------------------------------------------------------------------------
  // Teardown
  // -------------------------------------------------------------------------------------------

  /** Release everything: timers, media, audio graph, WebGL (panels), listeners, DOM. */
  destroy() {
    if (this.destroyed) return;
    this._teardownTimeline();
    this.destroyed = true;
    this.clock.destroy();
    this.disposer.dispose();
    this.stage.destroy();
    this.el.remove();
    this.handlers.clear();
    this.timeline = null;
  }
}
