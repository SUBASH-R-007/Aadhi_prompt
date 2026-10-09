// @ts-check
/**
 * Audio plumbing for live/preview playback:
 *   NarrationDeck  two reusable <audio> elements (current scene + preloaded next scene)
 *   envelopeLevel  narration loudness at a scene time from the build-time envelope
 *                  (TimedScene.audio_envelope): drives the speech-reactive mascot
 *   SpeechMeter    WebAudio AnalyserNode on the narration elements: the fallback for timelines built
 *                  before the envelope existed (same-origin audio only)
 *   SoundEffects   quiz countdown tick / reveal ding (branding sounds)
 *   BackgroundMusic looping BGM, ducked while narration is speaking
 * Render mode creates none of these (the MP4 renderer mixes audio with ffmpeg).
 */

import { safeUrl } from '../shared/dom.js';
import { clamp, num } from '../shared/format.js';

/** Envelope rate when a scene does not say (aadhi/pipeline/envelope.py ENVELOPE_FPS). */
export const ENVELOPE_FPS = 30;

/** @type {WeakMap<object, Uint8Array | null>} decoded envelopes by scene object (timelines are immutable) */
const envelopes = new WeakMap();

/**
 * Decode a base64 loudness envelope (one byte per frame); null when absent or malformed.
 * @param {string | null | undefined} text
 * @returns {Uint8Array | null}
 */
export function decodeEnvelope(text) {
  if (typeof text !== 'string' || text === '') return null;
  try {
    const bin = atob(text);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out.length ? out : null;
  } catch {
    return null;
  }
}

/**
 * @param {import('../shared/types.js').TimedScene} scene
 * @returns {Uint8Array | null}
 */
function sceneEnvelope(scene) {
  let env = envelopes.get(scene);
  if (env === undefined) {
    env = decodeEnvelope(scene.audio_envelope);
    envelopes.set(scene, env);
  }
  return env;
}

/** @param {import('../shared/types.js').TimedScene} scene */
export function hasEnvelope(scene) {
  return sceneEnvelope(scene) !== null;
}

/**
 * Narration loudness 0..1 at scene-relative time `sceneT` from the scene's build-time envelope
 * (0 outside the narration), or null when the scene has none. Deterministic: works with cross-origin
 * (S3/CDN) audio, without WebAudio or a user gesture.
 * @param {import('../shared/types.js').TimedScene} scene
 * @param {number} sceneT
 * @returns {number | null}
 */
export function envelopeLevel(scene, sceneT) {
  const env = sceneEnvelope(scene);
  if (!env) return null;
  const fps = num(scene.audio_envelope_fps, ENVELOPE_FPS) > 0 ? num(scene.audio_envelope_fps, ENVELOPE_FPS) : ENVELOPE_FPS;
  const i = Math.floor((sceneT - num(scene.audio_offset, 0)) * fps);
  return i >= 0 && i < env.length ? env[i] / 255 : 0;
}

/**
 * Whether the WebAudio meter is worth creating: some narrated scene has no envelope, and every
 * narration file is same-origin (routing a cross-origin element through WebAudio would silence it).
 * @param {import('../shared/types.js').TimedScene[]} scenes
 */
export function needsSpeechMeter(scenes) {
  return scenes.some((s) => !!s.audio_url && !hasEnvelope(s)) && scenes.every((s) => isSameOrigin(s.audio_url));
}

/**
 * @param {string | null | undefined} url
 * @returns {string | null}
 */
function mediaUrl(url) {
  if (!url) return null;
  const safe = safeUrl(url);
  return safe === 'about:blank' ? null : safe;
}

/**
 * Whether a URL resolves to the page origin (WebAudio can only analyse same-origin media without
 * CORS; routing a tainted element through WebAudio would silence it).
 * @param {string | null | undefined} url
 */
export function isSameOrigin(url) {
  if (!url) return true;
  try {
    return new URL(url, document.baseURI).origin === location.origin;
  } catch {
    return false;
  }
}

/** Fully release a media element (stops network activity). @param {HTMLMediaElement} el */
export function releaseMedia(el) {
  try {
    el.pause();
    el.removeAttribute('src');
    el.load();
  } catch {
    /* ignore */
  }
}

/**
 * @typedef {object} NarrationSlot
 * @property {HTMLAudioElement} el
 * @property {number} index
 * @property {string | null} url
 */

export class NarrationDeck {
  constructor() {
    /** @type {NarrationSlot[]} */
    this.slots = [0, 1].map(() => {
      const el = document.createElement('audio');
      el.preload = 'auto';
      el.setAttribute('playsinline', '');
      return { el, index: -1, url: null };
    });
    /** @type {NarrationSlot | null} */
    this.current = null;
    this.volume = 1;
    this.muted = false;
    this.rate = 1;
  }

  /** @returns {HTMLAudioElement[]} */
  get elements() {
    return this.slots.map((s) => s.el);
  }

  /**
   * @param {NarrationSlot} slot
   * @param {number} index
   * @param {string | null} url
   */
  _assign(slot, index, url) {
    slot.el.pause();
    slot.index = index;
    slot.url = url;
    if (url) {
      slot.el.src = url;
      slot.el.load();
    } else {
      slot.el.removeAttribute('src');
    }
    this._apply(slot.el);
  }

  /** @param {HTMLAudioElement} el */
  _apply(el) {
    el.volume = this.volume;
    el.muted = this.muted;
    el.playbackRate = this.rate;
  }

  /**
   * The narration element of a scene (reusing a preloaded one); null when the scene is silent.
   * @param {number} index
   * @param {string | null | undefined} rawUrl
   * @returns {HTMLAudioElement | null}
   */
  use(index, rawUrl) {
    const url = mediaUrl(rawUrl);
    if (!url) {
      this.current = null;
      return null;
    }
    let slot = this.slots.find((s) => s.index === index && s.url === url) || null;
    if (!slot) {
      slot = this.slots.find((s) => s !== this.current) || this.slots[0];
      this._assign(slot, index, url);
    }
    for (const s of this.slots) if (s !== slot) s.el.pause();
    this.current = slot;
    return slot.el;
  }

  /**
   * Start buffering a scene's narration in the spare element.
   * @param {number} index
   * @param {string | null | undefined} rawUrl
   */
  preload(index, rawUrl) {
    const url = mediaUrl(rawUrl);
    if (!url || this.slots.some((s) => s.index === index && s.url === url)) return;
    const spare = this.slots.find((s) => s !== this.current);
    if (spare) this._assign(spare, index, url);
  }

  /** @param {number} volume @param {boolean} muted */
  setVolume(volume, muted) {
    this.volume = clamp(volume, 0, 1);
    this.muted = !!muted;
    for (const s of this.slots) this._apply(s.el);
  }

  /** @param {number} rate */
  setRate(rate) {
    this.rate = rate;
    for (const s of this.slots) s.el.playbackRate = rate;
  }

  pauseAll() {
    for (const s of this.slots) s.el.pause();
  }

  destroy() {
    for (const s of this.slots) releaseMedia(s.el);
    this.current = null;
  }
}

export class SpeechMeter {
  constructor() {
    /** @type {AudioContext | null} */
    this.ctx = null;
    /** @type {AnalyserNode | null} */
    this.analyser = null;
    /** @type {WeakSet<HTMLMediaElement>} */
    this.connected = new WeakSet();
    /** @type {Uint8Array | null} */
    this.buf = null;
    this.failed = false;
  }

  /**
   * Route media elements through an analyser. Must be called from a user gesture (play button);
   * only connects once the AudioContext is running, so audio is never silenced by a suspended graph.
   * @param {HTMLMediaElement[]} elements
   * @returns {Promise<boolean>}
   */
  async attach(elements) {
    if (this.failed) return false;
    try {
      if (!this.ctx) {
        const AC = /** @type {any} */ (window).AudioContext || /** @type {any} */ (window).webkitAudioContext;
        if (!AC) {
          this.failed = true;
          return false;
        }
        this.ctx = /** @type {AudioContext} */ (new AC());
        this.analyser = this.ctx.createAnalyser();
        this.analyser.fftSize = 512;
        this.analyser.smoothingTimeConstant = 0.6;
        this.analyser.connect(this.ctx.destination);
        this.buf = new Uint8Array(this.analyser.fftSize);
      }
      if (this.ctx.state !== 'running') await this.ctx.resume();
      if (this.ctx.state !== 'running' || !this.analyser) return false;
      for (const el of elements) {
        if (this.connected.has(el)) continue;
        this.ctx.createMediaElementSource(el).connect(this.analyser);
        this.connected.add(el);
      }
      return true;
    } catch (e) {
      console.warn('speech meter unavailable', e);
      this.failed = true;
      return false;
    }
  }

  /** Re-resume after the browser suspended the context (e.g. tab switch). */
  resume() {
    if (this.ctx && this.ctx.state === 'suspended') this.ctx.resume().catch(() => {});
  }

  /**
   * Current loudness 0..1 (RMS of the waveform), or null without an analyser.
   * @returns {number | null}
   */
  level() {
    if (!this.analyser || !this.buf) return null;
    this.analyser.getByteTimeDomainData(/** @type {any} */ (this.buf));
    let sum = 0;
    for (let i = 0; i < this.buf.length; i++) {
      const v = (this.buf[i] - 128) / 128;
      sum += v * v;
    }
    return clamp(Math.sqrt(sum / this.buf.length) * 4, 0, 1);
  }

  destroy() {
    if (this.ctx) this.ctx.close().catch(() => {});
    this.ctx = null;
    this.analyser = null;
  }
}

export class SoundEffects {
  /**
   * @param {{ tick?: string | null, ding?: string | null }} urls
   */
  constructor(urls) {
    /** @type {Map<string, HTMLAudioElement>} */
    this.sounds = new Map();
    for (const [name, raw] of Object.entries(urls)) {
      const url = mediaUrl(raw);
      if (!url) continue;
      const el = document.createElement('audio');
      el.preload = 'auto';
      el.src = url;
      this.sounds.set(name, el);
    }
    this.volume = 1;
    this.muted = false;
  }

  /** @param {string} name */
  play(name) {
    const el = this.sounds.get(name);
    if (!el || this.muted) return;
    try {
      el.volume = this.volume;
      el.currentTime = 0;
      const p = el.play();
      if (p && typeof p.catch === 'function') p.catch(() => {});
    } catch {
      /* ignore */
    }
  }

  /** @param {number} volume @param {boolean} muted */
  setVolume(volume, muted) {
    this.volume = clamp(volume, 0, 1);
    this.muted = !!muted;
  }

  destroy() {
    for (const el of this.sounds.values()) releaseMedia(el);
    this.sounds.clear();
  }
}

export class BackgroundMusic {
  /**
   * @param {string | null | undefined} url
   * @param {number} level   Branding.bgm_volume
   */
  constructor(url, level) {
    const src = mediaUrl(url);
    /** @type {HTMLAudioElement | null} */
    this.el = null;
    if (src) {
      this.el = document.createElement('audio');
      this.el.preload = 'auto';
      this.el.loop = true;
      this.el.src = src;
    }
    this.level = clamp(Number.isFinite(level) ? level : 0.06, 0, 1);
    this.volume = 1;
    this.muted = false;
    this.ducked = false;
    this.enabled = true;
  }

  _apply() {
    if (!this.el) return;
    this.el.volume = clamp(this.level * this.volume * (this.ducked ? 0.5 : 1), 0, 1);
    this.el.muted = this.muted;
  }

  /** @param {boolean} playing */
  setPlaying(playing) {
    if (!this.el) return;
    if (playing && this.enabled) {
      this._apply();
      if (this.el.paused) {
        const p = this.el.play();
        if (p && typeof p.catch === 'function') p.catch(() => {});
      }
    } else if (!this.el.paused) {
      this.el.pause();
    }
  }

  /** @param {boolean} ducked */
  setDucked(ducked) {
    if (ducked === this.ducked) return;
    this.ducked = ducked;
    this._apply();
  }

  /** @param {number} volume @param {boolean} muted */
  setVolume(volume, muted) {
    this.volume = clamp(volume, 0, 1);
    this.muted = !!muted;
    this._apply();
  }

  destroy() {
    if (this.el) releaseMedia(this.el);
    this.el = null;
  }
}
