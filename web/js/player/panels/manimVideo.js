// @ts-check
/**
 * Video side panel (Manim animations, uploaded clips). Live/preview: a muted inline <video>
 * synced to scene time (currentTime = clamp(t - show_at), freeze or loop at the end) and played
 * at the player's speed (frame.rate when the player passes it, otherwise estimated from scene time
 * vs wall time), with a drift seek only as a fallback. Render: the video is hidden (transparent
 * hole, the compositor draws the media there) and mediaRect() reports the content rect in stage
 * pixels (the whole box while the aspect ratio is unknown); the card backdrop is clipped around
 * the hole.
 */

import { Disposer, h } from '../../shared/dom.js';
import { containRect, finiteOr, rectInStage, settleWithin, showNotice, stripRich } from './util.js';

/** @typedef {import('./types.js').PanelFactory} PanelFactory */
/** @typedef {import('./types.js').StageRect} StageRect */

/** Seek when the video drifts further than this (x the playback rate, >= 1) from the scene clock. */
export const DRIFT_TOLERANCE = 0.3;
/** Last-frame margin used for `freeze` (seeking to exactly `duration` shows a black frame in some browsers). */
export const FREEZE_EPSILON = 0.04;
/** Player speed limits (web/js/player/clock.js MIN_RATE / MAX_RATE). */
export const MIN_RATE = 0.25;
export const MAX_RATE = 4;
/** Playback rates are applied in steps of this size (avoids churn from jittery estimates). */
export const RATE_STEP = 0.05;
/** The rate estimate uses the scene clock over this much wall time... */
export const RATE_WINDOW_MS = 1000;
/** ...and needs at least this much before it replaces the current rate. */
export const RATE_MIN_SPAN_MS = 200;
/** An estimate must differ from the applied rate by more than this to change it (hysteresis). */
const RATE_HYSTERESIS = 0.035;

/** @returns {number} wall-clock milliseconds */
function wallNow() {
  return typeof performance !== 'undefined' && typeof performance.now === 'function' ? performance.now() : Date.now();
}

/**
 * Snap a playback rate to the player's range and RATE_STEP grid.
 * @param {number} rate
 */
export function quantizeRate(rate) {
  if (!Number.isFinite(rate) || rate <= 0) return 1;
  const q = Math.round(rate / RATE_STEP) * RATE_STEP;
  return Math.round(Math.min(MAX_RATE, Math.max(MIN_RATE, q)) * 100) / 100;
}

/**
 * Estimates the player's playback rate from the scene times passed to update() and the wall
 * clock (the panel contract carries no rate). Samples cover one continuous advancing run.
 */
export class RateEstimator {
  constructor() {
    /** @type {{ t: number, w: number }[]} */
    this.samples = [];
  }

  /** Forget the current run (seek, pause, hidden). */
  reset() {
    this.samples = [];
  }

  /**
   * Record an advancing frame and return the rate estimate, or null while it is not reliable yet.
   * @param {number} t      scene seconds
   * @param {number} wallMs wall-clock milliseconds
   * @returns {number | null}
   */
  sample(t, wallMs) {
    const s = this.samples;
    s.push({ t, w: wallMs });
    while (s.length > 2 && wallMs - s[1].w >= RATE_WINDOW_MS) s.shift();
    const first = s[0];
    const span = wallMs - first.w;
    if (span < RATE_MIN_SPAN_MS) return null;
    const rate = (t - first.t) / (span / 1000);
    return Number.isFinite(rate) && rate > 0 ? rate : null;
  }
}

/**
 * Media time for scene time t.
 * @param {number} t
 * @param {number} showAt
 * @param {number} duration   media duration (0/NaN = unknown)
 * @param {'loop' | 'freeze' | undefined | null} endBehavior
 */
export function videoTimeAt(t, showAt, duration, endBehavior) {
  const local = Math.max(0, t - showAt);
  if (!(duration > 0)) return local;
  if (endBehavior === 'loop') return local % duration;
  return Math.min(local, Math.max(0, duration - FREEZE_EPSILON));
}

/**
 * CSS clip-path that paints everything except `hole` (both in the element's local px).
 * @param {number} w
 * @param {number} hgt
 * @param {{ left: number, top: number, width: number, height: number }} hole
 */
export function holeClipPath(w, hgt, hole) {
  const r = (/** @type {number} */ v) => `${Math.round(v * 10) / 10}px`;
  const x0 = hole.left;
  const y0 = hole.top;
  const x1 = hole.left + hole.width;
  const y1 = hole.top + hole.height;
  return (
    `polygon(evenodd, 0 0, ${r(w)} 0, ${r(w)} ${r(hgt)}, 0 ${r(hgt)}, 0 0, ` +
    `${r(x0)} ${r(y0)}, ${r(x1)} ${r(y0)}, ${r(x1)} ${r(y1)}, ${r(x0)} ${r(y1)}, ${r(x0)} ${r(y0)})`
  );
}

/** @type {PanelFactory} */
export function createManimPanel(body, rsp, ctx, chrome) {
  const disposer = new Disposer();
  const media = rsp.media || null;
  const render = ctx.mode === 'render';
  const showAt = Math.max(0, finiteOr(rsp.show_at, 0));
  const url = media && media.url ? String(media.url) : '';
  if (!url) {
    showNotice(body, 'The animation has not been rendered yet.');
    return { destroy: () => disposer.dispose(), mediaRect: () => null };
  }
  const endBehavior = media && media.end_behavior === 'loop' ? 'loop' : 'freeze';
  const video = h('video', {
    class: ['ap-video', media && media.fit === 'cover' ? 'is-cover' : 'is-contain'],
    src: url,
    muted: true,
    playsinline: true,
    preload: render ? 'metadata' : 'auto',
    disablepictureinpicture: true,
    disableremoteplayback: true,
    'aria-label': stripRich(rsp.panel && rsp.panel.title) || 'Animation',
  });
  video.muted = true;
  video.defaultMuted = true;
  video.loop = false; // looping is driven by the scene clock
  const box = h('div', { class: 'ap-media-box ap-video-box' }, video);
  body.appendChild(box);
  if (render) {
    chrome.el.classList.add('ap-panel--hole');
    video.style.visibility = 'hidden';
  }

  const duration = () => finiteOr(media && media.duration, 0) || finiteOr(video.duration, 0);
  /** @type {number | null} */
  let pendingSeek = null;
  /** @param {number} time */
  const seekTo = (time) => {
    if (video.readyState >= 1) {
      try {
        video.currentTime = time;
      } catch {
        pendingSeek = time;
      }
    } else pendingSeek = time;
  };
  disposer.listen(video, 'loadedmetadata', () => {
    if (pendingSeek !== null) {
      const s = pendingSeek;
      pendingSeek = null;
      seekTo(s);
    }
  });
  const pause = () => {
    if (!video.paused) video.pause();
  };
  const play = () => {
    if (!video.paused) return;
    const p = video.play();
    if (p && typeof p.catch === 'function') p.catch(() => undefined);
  };

  /** @type {ReturnType<typeof setTimeout> | null} */
  let stallTimer = null;
  disposer.add(() => {
    if (stallTimer) clearTimeout(stallTimer);
  });
  /** @type {number | null} */
  let lastT = null;
  let lastWall = 0;
  let visible = false;
  const rates = new RateEstimator();
  /** @param {number} rate */
  const applyRate = (rate) => {
    const q = quantizeRate(rate);
    if (Math.abs(video.playbackRate - q) > 1e-6) video.playbackRate = q;
  };

  const metadata = settleWithin(
    new Promise((resolve) => {
      if (video.readyState >= 1) resolve(undefined);
      else disposer.listen(video, 'loadedmetadata', () => resolve(undefined), { once: true });
    }),
    4000,
  );

  /** Content rect of the video inside its box, in client px. */
  const contentClientRect = () => {
    const b = box.getBoundingClientRect();
    const aw = finiteOr(media && media.width, 0) || video.videoWidth;
    const ah = finiteOr(media && media.height, 0) || video.videoHeight;
    return media && media.fit === 'cover' ? { left: b.left, top: b.top, width: b.width, height: b.height } : containRect(b, aw, ah);
  };
  /** Clip the card backdrop around the hole so the PNG is transparent exactly where the media goes. */
  const layoutHole = () => {
    const backdrop = /** @type {HTMLElement | null} */ (chrome.el.querySelector('.ap-backdrop'));
    if (!backdrop) return;
    const br = backdrop.getBoundingClientRect();
    const scale = backdrop.offsetWidth > 0 ? br.width / backdrop.offsetWidth : 1;
    const c = contentClientRect();
    if (!(br.width > 0 && c.width > 0)) return;
    backdrop.style.clipPath = holeClipPath(br.width / scale, br.height / scale, {
      left: (c.left - br.left) / scale,
      top: (c.top - br.top) / scale,
      width: c.width / scale,
      height: c.height / scale,
    });
  };

  return {
    update(t, _state, isVisible, frame) {
      visible = isVisible;
      if (render) {
        if (isVisible) layoutHole();
        return;
      }
      const wall = wallNow();
      const wallDt = lastT === null ? 0 : Math.max(0, wall - lastWall) / 1000;
      // Advancing = scene time moved forward no faster than the fastest playback speed allows
      // (with slack for coarse frames); anything else is a seek.
      const stopped = !!frame && (frame.seeked === true || frame.playing === false);
      const advancing = lastT !== null && t > lastT && t - lastT < Math.max(0.5, MAX_RATE * wallDt + 0.1) && !stopped;
      lastT = t;
      lastWall = wall;
      const target = videoTimeAt(t, showAt, duration(), endBehavior);
      const ended = endBehavior === 'freeze' && duration() > 0 && t - showAt >= duration() - FREEZE_EPSILON;
      if (!isVisible || !advancing || ended) {
        rates.reset();
        pause();
        if (Math.abs(video.currentTime - target) > 0.04) seekTo(target);
        return;
      }
      // Follow the player's speed: from the frame info when the player provides it, otherwise
      // estimated from scene time vs wall time (seeking alone would stutter at 1.5x / 2x).
      const given = frame && typeof frame.rate === 'number' && Number.isFinite(frame.rate) && frame.rate > 0 ? frame.rate : null;
      if (given !== null) applyRate(given);
      else {
        const estimate = rates.sample(t, wall);
        if (estimate !== null && Math.abs(estimate - video.playbackRate) > RATE_HYSTERESIS) applyRate(estimate);
      }
      const tolerance = DRIFT_TOLERANCE * Math.max(1, video.playbackRate);
      if (Math.abs(video.currentTime - target) > tolerance) seekTo(target);
      play();
      // The player stops calling update() when paused: stop the video shortly after.
      if (stallTimer) clearTimeout(stallTimer);
      stallTimer = setTimeout(pause, 400);
    },
    /** @returns {StageRect | null} */
    mediaRect() {
      // Visibility is enforced by createPanel's wrapper (null while hidden).
      if (render) layoutHole();
      const r = rectInStage(box, ctx, contentClientRect());
      const finite = [r.x, r.y, r.width, r.height].every(Number.isFinite);
      return finite && r.width > 0 && r.height > 0 ? r : null;
    },
    async ready() {
      const knownSize = finiteOr(media && media.width, 0) > 0 && finiteOr(media && media.height, 0) > 0;
      if (!(render && knownSize)) await metadata; // render needs only the aspect ratio
      if (render && visible) layoutHole();
    },
    destroy() {
      disposer.dispose();
      video.pause();
      video.removeAttribute('src');
      try {
        video.load(); // release the decoder / network connection
      } catch {
        /* jsdom */
      }
    },
  };
}
