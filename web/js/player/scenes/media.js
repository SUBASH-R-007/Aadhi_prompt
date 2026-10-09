// @ts-check
/**
 * Media scenes (simulation = Manim video, ai_video = B-roll or a Ken-Burns still, GIFs): the
 * media fills the media zone (the board region) with the title above; the mascot stays visible
 * beside it, except in the content-first popup layout (the default of media scenes), where the
 * large media covers the close-up (layout.js).
 * Video time is the scene time (loop or freeze on the last frame per MediaRef.end_behavior), so
 * seeking is exact; stills get the Ken-Burns transform computed from t.
 * Render mode hides the media element (transparent hole) and reports the box + fit so ffmpeg can
 * composite the real media underneath.
 */

import { h } from '../../shared/dom.js';
import { num } from '../../shared/format.js';
import { kenBurnsAt, kenBurnsTransform } from '../kenburns.js';
import { releaseMedia } from '../audio.js';
import { fitAspect, mediaAspect } from '../layout.js';

/** @typedef {import('./types.js').SceneContext} SceneContext */
/** @typedef {import('./types.js').SceneView} SceneView */
/** @typedef {import('./types.js').FrameInfo} FrameInfo */
/** @typedef {import('../../shared/types.js').MediaRef} MediaRef */
/** @typedef {import('../layout.js').Rect} Rect */

const DRIFT_TOLERANCE = 0.35;
const FREEZE_EPSILON = 0.04;
/** Box aspect when the media does not declare its size (Manim renders, AI video and its stills,
 * and sketches are 16:9). */
export const DEFAULT_MEDIA_ASPECT = 16 / 9;

/** Media title metrics (player.css .ap-media-title): 40px uppercase title face, 26px side padding;
 * 0.62em is a generous average advance of an uppercase Outfit/Inter glyph. */
const TITLE_FONT_PX = 40;
const TITLE_CHAR_EM = 0.62;
const TITLE_PADDING_PX = 2 * 26 + 2;

/**
 * Whether a media title fits on one line at full size in a band `width` stage pixels wide
 * (a pure estimate, so live and render mode choose the same style).
 * @param {string} text
 * @param {number} width
 * @returns {boolean}
 */
export function titleFitsOneLine(text, width) {
  return Array.from(text || '').length * TITLE_CHAR_EM * TITLE_FONT_PX <= width - TITLE_PADDING_PX;
}

/**
 * Place the media box inside the scene's media zone with the media's aspect ratio (so the gold
 * frame hugs the picture and the render-mode hole matches what ffmpeg composites), and align the
 * title band with it (a long title gets the smaller two-line style instead of being cut).
 * Positions are deterministic stage pixels (layout.js zones).
 * @param {SceneContext} ctx
 * @param {HTMLElement} box     absolutely positioned inside the media zone
 * @param {HTMLElement} title   the title band (.ap-zone--title)
 * @param {number | null} aspect
 * @returns {Rect} the box rect in stage pixels
 */
export function placeMediaBox(ctx, box, title, aspect) {
  const zone = ctx.zones.media;
  const r = fitAspect(zone, aspect);
  box.style.left = `${r.x - zone.x}px`;
  box.style.top = `${r.y - zone.y}px`;
  box.style.width = `${r.w}px`;
  box.style.height = `${r.h}px`;
  title.style.left = `${r.x}px`;
  title.style.width = `${r.w}px`;
  title.classList.toggle('is-long', !titleFitsOneLine(ctx.scene.title || '', r.w));
  return r;
}

/**
 * Media-relative time for scene time t.
 * @param {number} t
 * @param {number} duration      media duration (<= 0 or NaN: unknown)
 * @param {'loop' | 'freeze' | undefined} endBehavior
 * @returns {number}
 */
export function mediaTimeAt(t, duration, endBehavior) {
  const tt = Math.max(0, t);
  if (!(duration > 0)) return tt;
  if (endBehavior === 'freeze') return Math.min(tt, Math.max(0, duration - FREEZE_EPSILON));
  return tt % duration;
}

/**
 * Title band used above fullscreen media (simulation, ai_video, interactive).
 * @param {import('../../shared/types.js').TimedScene} scene
 */
export function titleBand(scene) {
  return h(
    'div',
    { class: 'ap-zone ap-zone--title' },
    h('h2', { class: 'ap-media-title', text: scene.title || '' }),
    scene.subtitle ? h('p', { class: 'ap-media-subtitle', text: scene.subtitle }) : null,
  );
}

/** @implements {SceneView} */
export class MediaSceneView {
  /** @param {SceneContext} ctx */
  constructor(ctx) {
    this.ctx = ctx;
    const scene = ctx.scene;
    /** @type {MediaRef | null} */
    this.media = scene.media || null;
    this.fit = this.media?.fit === 'cover' ? 'cover' : 'contain';
    /** @type {HTMLVideoElement | null} */
    this.video = null;
    /** @type {HTMLImageElement | null} */
    this.image = null;
    this.renderable = !(ctx.mode === 'render' && this.media && this.media.render_in_mp4 === false);
    /** @type {HTMLDivElement} */
    this.box = h('div', { class: ['ap-media-box', `fit-${this.fit}`] });
    const m = this.media;
    if (m && m.url && this.renderable) {
      if (m.kind === 'video') {
        const adopted = ctx.mode !== 'render' && ctx.takePreloaded ? ctx.takePreloaded(m.url) : null;
        /** @type {HTMLVideoElement} */
        const v = adopted || h('video', { src: m.url, preload: 'auto', playsinline: true });
        v.classList.add('ap-media-el');
        v.muted = true; // B-roll / animations carry no narration
        v.defaultMuted = true;
        v.loop = m.end_behavior !== 'freeze';
        v.setAttribute('aria-label', scene.title || 'Animation');
        this.video = v;
        this.box.appendChild(v);
      } else {
        this.image = h('img', {
          class: ['ap-media-el', { 'has-kenburns': !!m.ken_burns && m.kind === 'image' }],
          src: m.url,
          alt: scene.title || '',
          decoding: 'async',
          loading: 'eager',
        });
        this.box.appendChild(this.image);
      }
    } else {
      this.box.classList.add('is-missing');
      this.box.appendChild(h('div', { class: 'ap-media-missing', text: scene.title || '' }));
    }
    if (m && m.attribution) {
      this.box.appendChild(
        m.link_url && ctx.mode !== 'render'
          ? h('a', { class: 'ap-media-attribution', href: m.link_url, target: '_blank', rel: 'noopener noreferrer', text: m.attribution })
          : h('div', { class: 'ap-media-attribution', text: m.attribution }),
      );
    }
    const title = titleBand(scene);
    /** Media box rect in stage pixels (also the render-mode hole). */
    this.rect = placeMediaBox(ctx, this.box, title, mediaAspect(m) ?? DEFAULT_MEDIA_ASPECT);
    /** @type {HTMLDivElement} */
    this.el = h('div', { class: 'ap-scene-media' }, title, h('div', { class: 'ap-zone ap-zone--media' }, this.box));
    this.lastTransform = '';
  }

  /** Media duration from the MediaRef or the element. */
  mediaDuration() {
    const d = num(this.media?.duration, NaN);
    if (d > 0) return d;
    return this.video ? num(this.video.duration, NaN) : NaN;
  }

  /**
   * @param {number} t
   * @param {import('../../shared/types.js').SceneState} _state
   * @param {FrameInfo} frame
   */
  update(t, _state, frame) {
    if (this.ctx.mode === 'render') return;
    if (this.video) this.syncVideo(t, frame);
    if (this.image && this.media?.ken_burns) {
      const tf = kenBurnsTransform(kenBurnsAt(this.media.ken_burns, t, num(this.ctx.scene.duration, 0)));
      if (tf !== this.lastTransform) {
        this.lastTransform = tf;
        this.image.style.transform = tf;
      }
    }
  }

  /**
   * Keep the video at the scene time: play/pause with the clock, correct drift, freeze at the end.
   * @param {number} t
   * @param {FrameInfo} frame
   */
  syncVideo(t, frame) {
    const v = /** @type {HTMLVideoElement} */ (this.video);
    const duration = this.mediaDuration();
    const target = mediaTimeAt(t, duration, this.media?.end_behavior);
    const frozen = this.media?.end_behavior === 'freeze' && duration > 0 && t >= duration - FREEZE_EPSILON;
    if (v.playbackRate !== frame.rate) v.playbackRate = frame.rate;
    const drift = Math.abs(v.currentTime - target);
    // Looping videos wrap around: a drift of ~duration is not a drift.
    const wrapped = v.loop && duration > 0 && Math.abs(drift - duration) < DRIFT_TOLERANCE;
    if (v.readyState >= 1 && (frame.seeked || (drift > DRIFT_TOLERANCE && !wrapped) || frozen)) {
      try {
        if (Math.abs(v.currentTime - target) > 0.01) v.currentTime = target;
      } catch {
        /* not seekable yet */
      }
    }
    if (frame.playing && !frozen) {
      if (v.paused) {
        const p = v.play();
        if (p && typeof p.catch === 'function') p.catch(() => {});
      }
    } else if (!v.paused) {
      v.pause();
    }
  }

  /** @param {boolean} playing */
  setPlaying(playing) {
    if (this.video && !playing && !this.video.paused) this.video.pause();
  }

  mediaElement() {
    return this.media && this.media.url && this.renderable ? this.box : null;
  }

  /** @returns {'contain' | 'cover' | null} */
  mediaFit() {
    return this.mediaElement() ? /** @type {'contain' | 'cover'} */ (this.fit) : null;
  }

  async ready() {
    // Render mode never shows the media itself; live mode does not block on media download.
  }

  destroy() {
    if (this.video) releaseMedia(this.video);
    this.video = null;
    this.el.remove();
  }
}
