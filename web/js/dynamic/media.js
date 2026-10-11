// @ts-check
/**
 * Media in the Dynamic stage (DYNAMIC_VIDEO_DESIGN §7.7).
 *
 * Render mode never uses `<video>` (§7.4): a video is an `<img>` pointed at the worker's pre-extracted frame
 * `/__frames/<key>/<n>.jpg` for every captured frame. `mediaPlan(f0, f1)` lists the frames a chunk needs:
 *
 *   [{key, kind: 'video' | 'aadhi', srcFps, frames: [n, ...], size: [w, h], asset_key, url, duration, end_behavior,
 *     trim_in}]
 *
 * Frame `n` of `key` is the picture at source time `n / srcFps` (`srcFps` = `MediaRef.src_fps`, else the timeline's
 * fps: the worker then extracts at that rate). `size` is the largest size the stage can show the clip at (never above
 * the source or the stage), the same for every chunk, so `key` (which carries it) always names the same pixels.
 * Live mode plays real `<video>` elements kept within 0.1 s of the stage time [ED §8]; the narration stays the clock.
 * Images are plain `<img>` elements; Ken Burns is a transform computed from t.
 */

import { h, safeUrl } from '../shared/dom.js';
import { kenBurnsAt, kenBurnsTransform } from '../player/kenburns.js';
import { Hasher, fx, stageInfo, sceneIndexOfFrame, timelineFps } from './core.js';
import { transitionAt } from './transitions.js';

/** @typedef {import('../shared/types.js').MediaRef} MediaRef */
/** @typedef {import('../shared/types.js').TimedScene} TimedScene */
/** @typedef {import('../shared/types.js').Timeline} Timeline */
/** @typedef {import('./core.js').Painter} Painter */
/** @typedef {{x: number, y: number, w: number, h: number}} Rect */

export const FRAMES_PREFIX = '/__frames/';
/**
 * Whether `mode` can show this picture or clip (the rules of `MediaView`): the render page shows a clip only as the
 * worker's pre-extracted frames (an asset key) and never a GIF (live-only, §7.7); nothing marked `render_in_mp4:
 * false` reaches the MP4; anything else needs a URL. A medium the mode cannot show takes no room in the layout.
 * @param {MediaRef | null | undefined} ref
 * @param {'live' | 'render'} mode
 */
export function shownIn(ref, mode) {
  if (!ref) return false;
  if (mode === 'render') {
    if (ref.render_in_mp4 === false || ref.kind === 'gif') return false;
    return ref.kind === 'video' ? !!ref.asset_key : !!ref.url;
  }
  return !!ref.url;
}

/** Live videos are re-sought when they drift further than this from the stage time [ED §8]. */
export const LIVE_DRIFT = 0.1;
/** Render mode prefetches this many frames ahead of the current one (§7.3 seek step 5). */
export const LOOKAHEAD = 10;

/**
 * Even integer, at least 2 (4:2:0 sizes).
 * @param {number} v
 */
function even(v) {
  return Math.max(2, 2 * Math.round(v / 2));
}

/**
 * Size a source of `srcW x srcH` takes when contained in `box` (aspect kept), never above the source: even ints.
 * @param {{width?: number | null, height?: number | null}} ref
 * @param {{w: number, h: number}} box
 * @returns {[number, number]}
 */
export function mediaSize(ref, box) {
  const sw = Number(ref.width) > 0 ? Number(ref.width) : box.w;
  const sh = Number(ref.height) > 0 ? Number(ref.height) : box.h;
  const k = Math.min(box.w / sw, box.h / sh, 1);
  return [even(sw * k), even(sh * k)];
}

/**
 * The frame key of a clip at a size: path-safe and stable (a 64-bit hash of the asset key).
 * @param {'video' | 'aadhi'} kind
 * @param {string} assetKey
 * @param {[number, number]} size
 */
export function mediaKey(kind, assetKey, size) {
  return `${kind === 'aadhi' ? 'a' : 'v'}-${new Hasher().add(assetKey).digest()}-${size[0]}x${size[1]}`;
}

/**
 * URL of frame `n` of `key` (answered by the render worker's request handler, never by the API).
 * @param {string} key
 * @param {number} n
 */
export function frameUrl(key, n) {
  return `${FRAMES_PREFIX}${key}/${String(Math.max(0, n)).padStart(6, '0')}.jpg`;
}

/**
 * Source frame shown at `local` seconds into the clip's appearance (§7.7 frame selection), looped, frozen or
 * clamped by `end_behavior`.
 * @param {MediaRef} ref
 * @param {number} local
 * @param {number} srcFps
 */
export function sourceFrame(ref, local, srcFps) {
  return wrapFrame(ref, Math.floor((Math.max(0, local) + Number(ref.trim_in || 0)) * srcFps + 1e-6), srcFps);
}

/**
 * A source frame index looped (`loop`) or held on the last frame (`freeze`, `clamp`) inside the clip.
 * @param {MediaRef} ref
 * @param {number} n
 * @param {number} srcFps
 */
export function wrapFrame(ref, n, srcFps) {
  const duration = Number(ref.duration);
  if (!(duration > 0)) return n;
  const count = Math.max(1, Math.floor(duration * srcFps + 1e-6));
  if (ref.end_behavior === 'loop') return n % count;
  return Math.min(n, count - 1);
}

/**
 * Source time shown at `local` seconds (live playback), looped or held like `sourceFrame`.
 * @param {MediaRef} ref
 * @param {number} local
 */
export function sourceTime(ref, local) {
  const t = Math.max(0, local) + Number(ref.trim_in || 0);
  const duration = Number(ref.duration);
  if (!(duration > 0)) return t;
  if (ref.end_behavior === 'loop') return t % duration;
  return Math.min(t, Math.max(0, duration - 0.04));
}

/**
 * @typedef {object} SceneClip
 * @property {'main' | 'panel'} slot
 * @property {MediaRef} ref
 * @property {number} start   scene-relative time the clip starts playing
 */

/**
 * The video clips a scene shows in the MP4 (main media, a video side panel). Hotlinked GIFs are live-only.
 * @param {TimedScene} scene
 * @returns {SceneClip[]}
 */
export function sceneClips(scene) {
  /** @type {SceneClip[]} */
  const out = [];
  const main = scene.media;
  if (main && main.kind === 'video' && main.render_in_mp4 !== false && main.asset_key) out.push({ slot: 'main', ref: main, start: 0 });
  const sp = scene.side_panel;
  const panel = sp && sp.media;
  if (panel && panel.kind === 'video' && panel.render_in_mp4 !== false && panel.asset_key && scene.layout?.show_side_panel !== false) {
    out.push({ slot: 'panel', ref: panel, start: Math.max(0, Number(sp.show_at) || 0) });
  }
  return out;
}

/**
 * @typedef {object} PlanEntry
 * @property {string} key
 * @property {'video' | 'aadhi'} kind
 * @property {number} srcFps
 * @property {number[]} frames
 * @property {[number, number]} size
 * @property {string} asset_key
 * @property {string | null} url
 * @property {number | null} duration
 * @property {string} end_behavior
 * @property {number} trim_in
 */

/**
 * `aadhiStage.mediaPlan(f0, f1)`: the media frames frames [f0, f1) show, per clip (sorted by key).
 *
 * A clip that has not started yet (a side panel before its `show_at`) lists its first frame (`sourceFrame` clamps a
 * negative local time to 0): the page paints that frame into the hidden panel so it is decoded when the panel enters,
 * and the worker serves only frames listed here.
 * @param {Timeline} timeline
 * @param {number} f0
 * @param {number} f1  exclusive
 * @param {(scene: TimedScene, slot: 'main' | 'panel') => {w: number, h: number}} boxFor  largest box of a clip
 * @returns {PlanEntry[]}
 */
export function mediaPlan(timeline, f0, f1, boxFor) {
  const info = stageInfo(timeline);
  const fps = timelineFps(timeline);
  const scenes = timeline.scenes || [];
  /** @type {Map<string, PlanEntry & {set: Set<number>}>} */
  const byKey = new Map();
  /**
   * @param {'video' | 'aadhi'} kind @param {MediaRef} ref @param {string} assetKey @param {[number, number]} size
   * @param {number} srcFps @param {number} n
   */
  const add = (kind, ref, assetKey, size, srcFps, n) => {
    const key = mediaKey(kind, assetKey, size);
    let e = byKey.get(key);
    if (!e) {
      e = {
        key, kind, srcFps, frames: [], size, asset_key: assetKey, url: ref.url || null,
        duration: Number(ref.duration) > 0 ? Number(ref.duration) : null, end_behavior: String(ref.end_behavior || 'loop'),
        trim_in: Number(ref.trim_in || 0), set: new Set(),
      };
      byKey.set(key, e);
    }
    e.set.add(n);
  };
  /** @param {number} index @param {number} sceneT */
  const sceneFrames = (index, sceneT) => {
    const scene = scenes[index];
    for (const clip of sceneClips(scene)) {
      const srcFps = Number(clip.ref.src_fps) > 0 ? Number(clip.ref.src_fps) : fps;
      const box = boxFor(scene, clip.slot);
      const size = mediaSize(clip.ref, box);
      add('video', clip.ref, String(clip.ref.asset_key), size, srcFps, sourceFrame(clip.ref, sceneT - clip.start, srcFps));
    }
    const p = presenterOf(scene);
    if (p && sceneT < p.t_exit) {
      add('aadhi', /** @type {any} */ ({ duration: null, end_behavior: 'clamp' }), p.clip_key, p.size, p.src_fps,
        Math.floor(sceneT * p.src_fps + 1e-6));
    }
  };
  const lo = Math.max(0, Math.floor(f0));
  const hi = Math.min(info.frames, Math.floor(f1));
  for (let f = lo; f < hi; f++) {
    const i = sceneIndexOfFrame(info.scenes, f);
    if (i < 0) continue;
    const t = f / fps;
    const sceneT = Math.max(0, t - Number(scenes[i].start));
    sceneFrames(i, sceneT);
    const tr = transitionAt(timeline, i, sceneT);
    if (tr && tr.outgoing !== null) sceneFrames(tr.outgoing, Number(scenes[tr.outgoing].duration));
  }
  return [...byKey.values()]
    .sort((a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : 0))
    .map(({ set, ...e }) => ({ ...e, frames: [...set].sort((a, b) => a - b) }));
}

/**
 * The presenter clip of a scene's look (`ResolvedLook.presenter`, resolved by B2), normalised; null without one.
 * @param {TimedScene} scene
 * @returns {{clip_key: string, rect: Rect, t_exit: number, src_fps: number, size: [number, number], url: string | null} | null}
 */
export function presenterOf(scene) {
  const p = scene.look && scene.look.presenter;
  if (!p || typeof p.clip_key !== 'string' || !p.clip_key || !Array.isArray(p.rect) || p.rect.length !== 4) return null;
  const [x, y, w, hh] = p.rect.map(Number);
  if (![x, y, w, hh].every(Number.isFinite) || w <= 0 || hh <= 0) return null;
  const tExit = Number(p.t_exit);
  return {
    clip_key: p.clip_key,
    rect: { x, y, w, h: hh },
    t_exit: Number.isFinite(tExit) ? tExit : Number(scene.duration),
    src_fps: Number(p.src_fps) > 0 ? Number(p.src_fps) : 24,
    size: [even(w), even(hh)],
    url: typeof p.url === 'string' ? p.url : null,
  };
}

// ---------------------------------------------------------------------------------------------
// Media elements
// ---------------------------------------------------------------------------------------------

/**
 * @typedef {object} MediaFrameInfo
 * @property {boolean} [playing]
 * @property {number} [rate]
 * @property {boolean} [seeked]
 * @property {boolean} [noFrames]  render page boot: leave the worker's pre-extracted media frames unrequested (the
 *                                 worker serves them per chunk only, §7.7), so `ready` never waits for them
 */

/**
 * One media element of a scene: an image (with Ken Burns), a video (frames in render mode, `<video>` live) or a
 * live-only GIF. The element fills its box with the clip's `fit`.
 */
export class MediaView {
  /**
   * @param {MediaRef} ref
   * @param {{mode: 'live' | 'render', box: {w: number, h: number}, start?: number, sceneDuration: number,
   *   fps: number, reducedMotion?: boolean, alt?: string}} opts
   */
  constructor(ref, opts) {
    this.ref = ref;
    this.mode = opts.mode;
    this.start = opts.start || 0;
    this.sceneDuration = opts.sceneDuration;
    this.reducedMotion = !!opts.reducedMotion;
    this.srcFps = Number(ref.src_fps) > 0 ? Number(ref.src_fps) : opts.fps;
    this.fit = ref.fit === 'cover' ? 'cover' : 'contain';
    /** @type {HTMLVideoElement | null} */
    this.video = null;
    /** @type {HTMLImageElement | null} */
    this.img = null;
    /** @type {string | null} */
    this.key = null;
    /** @type {Promise<unknown> | null} */
    this.decoding = null;
    /** @type {HTMLImageElement[]} */
    this.prefetch = [];
    this.missing = false;
    const isVideo = ref.kind === 'video';
    const live = this.mode !== 'render';
    /** @type {HTMLDivElement} */
    this.el = h('div', { class: ['dyn-media', `dyn-media--${this.fit}`] });
    if (isVideo && !live) {
      if (!ref.asset_key || ref.render_in_mp4 === false) {
        this.missing = true;
      } else {
        this.key = mediaKey('video', String(ref.asset_key), mediaSize(ref, opts.box));
        this.img = h('img', { class: 'dyn-media-el', alt: opts.alt || '', decoding: 'sync', draggable: 'false' });
      }
    } else if (isVideo) {
      const url = ref.url ? safeUrl(ref.url) : '';
      if (!url || url === 'about:blank') this.missing = true;
      else {
        // determinism-allow video: live player only; render mode shows the worker's pre-extracted frames as <img>
        this.video = h('video', { class: 'dyn-media-el', src: url, preload: 'auto', playsinline: true });
        this.video.muted = true;
        this.video.loop = false;
      }
    } else if (ref.kind === 'gif' && !live) {
      this.missing = true; // GIFs run their own clock: live player only (§7.7)
    } else {
      const url = ref.url ? safeUrl(ref.url) : '';
      if (!url || url === 'about:blank' || (!live && ref.render_in_mp4 === false)) this.missing = true;
      else this.img = h('img', { class: 'dyn-media-el', src: url, alt: opts.alt || '', decoding: 'sync', draggable: 'false' });
    }
    const child = this.video || this.img;
    if (child) this.el.appendChild(child);
    else this.el.classList.add('is-missing');
  }

  /**
   * Show the clip at scene-relative `sceneT`.
   * @param {Painter} paint
   * @param {number} sceneT
   * @param {MediaFrameInfo} [frame]
   */
  update(paint, sceneT, frame = {}) {
    const local = sceneT - this.start;
    if (this.key && this.img) {
      if (frame.noFrames) return;
      const n = sourceFrame(this.ref, local, this.srcFps);
      const src = frameUrl(this.key, n);
      if (this.img.getAttribute('src') !== src) this.decoding = null;
      paint.attr(this.img, 'src', src);
      // before the clip starts (a panel shown later) the hidden frame is the first one; the plan lists only that
      if (local >= 0) this._prefetch(n);
    } else if (this.video) {
      this._syncVideo(local, frame);
      paint.note(`v:${fx(sourceTime(this.ref, local), 2)}`);
    } else if (this.img && this.ref.ken_burns && !this.reducedMotion) {
      const kb = kenBurnsAt(this.ref.ken_burns, sceneT, this.sceneDuration);
      paint.style(this.img, 'transform-origin', '0 0');
      paint.style(this.img, 'transform', kenBurnsTransform(kb));
    }
  }

  /** @param {number} n */
  _prefetch(n) {
    if (!this.key || typeof Image === 'undefined') return;
    /** @type {HTMLImageElement[]} */
    const next = [];
    for (let k = 1; k <= LOOKAHEAD; k++) {
      const m = wrapFrame(this.ref, n + k, this.srcFps);
      if (m === n) continue;
      const url = frameUrl(this.key, m);
      const old = this.prefetch.find((im) => im.dataset.src === url);
      if (old) next.push(old);
      else {
        const im = new Image();
        im.dataset.src = url;
        im.src = url;
        next.push(im);
      }
    }
    this.prefetch = next;
  }

  /**
   * @param {number} local
   * @param {MediaFrameInfo} frame
   */
  _syncVideo(local, frame) {
    const v = this.video;
    if (!v) return;
    const target = sourceTime(this.ref, local);
    const playing = !!frame.playing && local >= 0;
    try {
      if (v.readyState >= 1 && !v.seeking && (frame.seeked || !playing || Math.abs(v.currentTime - target) > LIVE_DRIFT)) {
        if (Math.abs(v.currentTime - target) > 1e-3) v.currentTime = target;
      }
      v.playbackRate = Number(frame.rate) > 0 ? Number(frame.rate) : 1;
      if (playing && v.paused) {
        const p = v.play();
        if (p && typeof p.catch === 'function') p.catch(() => undefined);
      } else if (!playing && !v.paused) {
        v.pause();
      }
    } catch {
      /* not seekable yet */
    }
  }

  /** Resolves when the image on screen is decoded (render mode waits for it before capturing). */
  ready() {
    const img = this.img;
    if (!img || !img.getAttribute('src') || typeof img.decode !== 'function') return Promise.resolve();
    if (!this.decoding) {
      const src = img.getAttribute('src');
      this.decoding = img.decode().then(
        () => img.classList.remove('is-broken'),
        (err) => {
          // A missing pre-extracted frame is an engine bug: the capture must fail. A broken picture degrades.
          if (this.key) throw Object.assign(new Error(`media frame failed to load: ${src}`), { code: 'media_frame', cause: err });
          img.classList.add('is-broken');
        },
      );
    }
    return this.decoding;
  }

  destroy() {
    if (this.video) {
      try {
        this.video.pause();
        this.video.removeAttribute('src');
        this.video.load();
      } catch {
        /* ignore */
      }
    }
    this.prefetch = [];
    this.el.remove();
  }
}
