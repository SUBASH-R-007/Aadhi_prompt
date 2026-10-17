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
 * A paused clip that cannot seek when it is painted (no metadata yet, a seek in flight) is sought once it can, without
 * a repaint (`MediaView._resync`). Images are plain `<img>` elements; Ken Burns is a transform computed from t. A
 * medium shown `contain_blur` sits contained in its box over a blurred copy of the same frame (`BACKDROP_WIDTH`): no
 * other frames are extracted.
 */

import { h, safeUrl } from '../shared/dom.js';
import { kenBurnsAt, kenBurnsTransform } from '../player/kenburns.js';
import { Hasher, canvas2d, fx, stageInfo, sceneIndexOfFrame, timelineFps } from './core.js';
import { transitionAt } from './transitions.js';
import { presenterFrame, presenterShown, presenterSpec } from './presenter.js';

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
 * `fit: "contain_blur"` (§2.5, §8.2: inserted clips; B2's `full_bleed_media` in 9:16): the medium is contained in its
 * box over a blurred, darkened cover-fit copy of the same frame that fills the box. The copy is drawn into a small
 * canvas (this many px wide, the box's aspect) with the canvas' own blur and brightness filter, and the canvas is
 * stretched over the box, whose smooth upscaling does the rest of the blur. A CSS `filter: blur()` over the box would
 * cost about 22 ms per 1080p frame (§4.3 [MR §3]); this blurs about 2,000 px. The drawing is a function of the frame's
 * pixels and the box's laid-out size only, so it is the same in any render order, and it reads the element the medium
 * itself shows (the render page's `<img>` of the pre-extracted frame, a picture, or the live `<video>`): `mediaPlan`
 * lists no other frame or size for it. A medium that covers its box has none (`backdropShown`). Render mode draws it
 * once that frame is decoded, before the capture; live mode on every paint and when the clip's frame arrives after one
 * (its first frame, the end of a seek: no repaint needed).
 */
export const BACKDROP_WIDTH = 64;
/** Blur radius in backdrop px (about 30 stage px once stretched over a 1080 px wide box). */
export const BACKDROP_BLUR = 2;
/** Brightness of the backdrop copy: darkened, so the contained medium stands out from its own copy. */
export const BACKDROP_BRIGHTNESS = 0.6;

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
 * @property {number[]} [crop]   'aadhi' only, when the look stores one: the source rect [x, y, w, h] the worker crops
 *                               before scaling to `size` (B2 plan §1.12; presenter.js presenterKey hashes it into `key`)
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
   * @param {number} srcFps @param {number} n @param {string} [own] the frames' key when the medium names its own
   * (the presenter's, which carries its crop) @param {number[] | null} [crop]
   */
  const add = (kind, ref, assetKey, size, srcFps, n, own, crop = null) => {
    const key = own || mediaKey(kind, assetKey, size);
    let e = byKey.get(key);
    if (!e) {
      e = {
        key, kind, srcFps, frames: [], size, asset_key: assetKey, url: ref.url || null,
        duration: Number(ref.duration) > 0 ? Number(ref.duration) : null, end_behavior: String(ref.end_behavior || 'loop'),
        trim_in: Number(ref.trim_in || 0), ...(crop ? { crop: [...crop] } : {}), set: new Set(),
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
    // the presenter (presenter.js): planned while he is on screen, his exit included, under the key that names his crop
    const p = presenterSpec(scene);
    if (p && presenterShown(p, sceneT)) {
      add('aadhi', /** @type {any} */ ({ duration: null, end_behavior: 'clamp' }), p.clip_key, p.size, p.src_fps,
        presenterFrame(p, sceneT), p.key, p.crop);
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
 * The presenter clip of a scene's look (`ResolvedLook.presenter`), normalised; null without one. presenter.js
 * `presenterSpec` (step 5: the crop, the exit and the entrance; the same values as before for a presenter without them).
 * @param {TimedScene} scene
 * @returns {import('./presenter.js').PresenterSpec | null}
 */
export function presenterOf(scene) {
  return presenterSpec(scene);
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
 * The `fit` a MediaView draws: `cover`, `contain_blur` (contained over a blurred copy, `BACKDROP_WIDTH`), else
 * `contain`.
 * @param {MediaRef} ref
 * @returns {'cover' | 'contain' | 'contain_blur'}
 */
export function mediaFit(ref) {
  return ref.fit === 'cover' || ref.fit === 'contain_blur' ? ref.fit : 'contain';
}

/**
 * The source rect of a `w x h` picture that covers a `cw x ch` box (centred; the box's aspect).
 * @param {number} w @param {number} h @param {number} cw @param {number} ch
 * @returns {{x: number, y: number, w: number, h: number}}
 */
export function coverCrop(w, h, cw, ch) {
  const k = Math.max(cw / w, ch / h);
  const sw = Math.min(w, cw / k);
  const sh = Math.min(h, ch / k);
  return { x: (w - sw) / 2, y: (h - sh) / 2, w: sw, h: sh };
}

/**
 * Whether a `contain_blur` backdrop shows at all: a `sw x sh` medium contained in a `bw x bh` box leaves room for it
 * unless it covers the box to within a pixel at each side (board_generic sizes a medium's frame to the medium's own
 * aspect whenever its size is known, so that is the usual case there). A covered backdrop is not drawn and its canvas
 * is not shown: nothing of it would be seen, and a shown canvas is a composited layer of its own (dynamic.css).
 * @param {number} sw @param {number} sh @param {number} bw @param {number} bh
 */
export function backdropShown(sw, sh, bw, bh) {
  if (!(sw > 0 && sh > 0 && bw > 0 && bh > 0)) return true;
  const k = Math.min(bw / sw, bh / sh);
  return bw - sw * k >= 2 || bh - sh * k >= 2;
}

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
    this.box = opts.box;
    this.start = opts.start || 0;
    this.sceneDuration = opts.sceneDuration;
    this.reducedMotion = !!opts.reducedMotion;
    this.srcFps = Number(ref.src_fps) > 0 ? Number(ref.src_fps) : opts.fps;
    this.fit = mediaFit(ref);
    /** @type {HTMLCanvasElement | null} the `contain_blur` backdrop */
    this.backdrop = null;
    /** @type {CanvasRenderingContext2D | null | undefined} */
    this.backdropCtx = undefined;
    /** what the backdrop shows now (source, its size, the canvas size): drawn again only when that changes */
    this.backdropKey = '';
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
    /** @type {{local: number, frame: MediaFrameInfo} | null} live: the clip time the stage asked for last (`_resync`) */
    this.liveWant = null;
    /** live: the source time this view last sought the clip to */
    this.liveSought = NaN;
    /** @type {[string, EventListener][]} listeners on the live `<video>` (removed by `destroy`) */
    this.listeners = [];
    const isVideo = ref.kind === 'video';
    const live = this.mode !== 'render';
    // `dyn-media--clip`: a new picture every frame (dynamic.css gives a contain_blur clip's box a layer of its own)
    /** @type {HTMLDivElement} */
    this.el = h('div', { class: ['dyn-media', `dyn-media--${this.fit}`, isVideo ? 'dyn-media--clip' : null] });
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
    if (child && this.fit === 'contain_blur') {
      this.backdrop = h('canvas', { class: 'dyn-media-backdrop', 'aria-hidden': 'true' });
      this.el.appendChild(this.backdrop);
    }
    if (child) this.el.appendChild(child);
    else this.el.classList.add('is-missing');
    const v = this.video;
    if (v) {
      // Live only: render mode never makes a <video> (a clip is an <img> of its pre-extracted frame, drawn by update()
      // and ready() alone), so these listeners never touch the MP4, which stays a pure function of the seek. The live
      // player paints on its own frames only: every animation frame while playing, once per seek or relayout while
      // paused (web/js/player/player.js). What a paused clip does after that paint is caught here, with no repaint:
      // it can seek only once its metadata is in and one seek at a time (a scrub asks for many), so the time asked
      // for last is applied when it can be (`_resync`); and the contain_blur backdrop, which reads the clip itself, is
      // drawn when the frame the clip shows arrives (its first frame, the end of a seek: readyState is >= 2 at
      // 'seeked' in Chromium), not left showing the frame before the seek, or nothing, until the next play.
      this._listen(v, 'loadedmetadata', () => this._resync());
      this._listen(v, 'seeked', () => {
        this._resync();
        this._backdrop();
      });
      if (this.backdrop) this._listen(v, 'loadeddata', () => this._backdrop());
    }
  }

  /**
   * @param {HTMLVideoElement} el
   * @param {string} type
   * @param {EventListener} fn
   */
  _listen(el, type, fn) {
    el.addEventListener(type, fn);
    this.listeners.push([type, fn]);
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
    // a frame already in (a picture, a cached frame, the live clip) and a box laid out anew both show at once
    this._backdrop();
  }

  /**
   * Draw the `contain_blur` backdrop from what the element shows now: a picture or frame only once it is completely
   * available (its new `src` loaded, not the previous picture it keeps showing meanwhile), a live clip once it has a
   * frame at its time and is not seeking. No-op for any other fit.
   */
  _backdrop() {
    if (!this.backdrop) return;
    const v = this.video;
    if (v) {
      if (v.readyState >= 2 && !v.seeking && v.videoWidth > 0) this._drawBackdrop(v, v.videoWidth, v.videoHeight, `t:${v.currentTime}`);
      return;
    }
    const img = this.img;
    const src = img ? img.getAttribute('src') : null;
    if (img && src && img.complete && img.naturalWidth > 0) this._drawBackdrop(img, img.naturalWidth, img.naturalHeight, src);
  }

  /**
   * The cover-fit copy of `source` (`sw x sh` px, showing `id`) in the backdrop canvas, blurred and darkened (see
   * BACKDROP_WIDTH), for the box as laid out now; none (the canvas not shown) while the medium covers the box
   * (`backdropShown`). Drawn again only when the picture or the box changed: the canvas then already holds exactly
   * this drawing.
   * @param {CanvasImageSource} source
   * @param {number} sw
   * @param {number} sh
   * @param {string} id
   */
  _drawBackdrop(source, sw, sh, id) {
    const canvas = this.backdrop;
    if (!canvas) return;
    // the box the medium is shown in (a template may lay it out at another aspect than the box it planned)
    const bw = this.el.clientWidth || this.box.w;
    const bh = this.el.clientHeight || this.box.h;
    if (!(bw > 0 && bh > 0)) return;
    const key = `${id}|${sw}x${sh}|${bw}x${bh}`;
    if (key === this.backdropKey) return;
    const shown = backdropShown(sw, sh, bw, bh);
    canvas.style.display = shown ? '' : 'none';
    if (!shown) {
      this.backdropKey = key;
      return;
    }
    if (this.backdropCtx === undefined) this.backdropCtx = canvas2d(canvas);
    const g = this.backdropCtx;
    if (!g) return;
    const cw = BACKDROP_WIDTH;
    const ch = Math.max(1, Math.round((cw * bh) / bw));
    if (canvas.width !== cw) canvas.width = cw;
    if (canvas.height !== ch) canvas.height = ch;
    const crop = coverCrop(sw, sh, cw, ch);
    // drawn a little larger than the canvas, so the blur at its edges averages picture, not the transparent outside
    const pad = 3 * BACKDROP_BLUR;
    g.save();
    g.clearRect(0, 0, cw, ch);
    g.filter = `blur(${BACKDROP_BLUR}px) brightness(${BACKDROP_BRIGHTNESS})`;
    g.imageSmoothingEnabled = true;
    g.imageSmoothingQuality = 'high';
    g.drawImage(source, crop.x, crop.y, crop.w, crop.h, -pad, -pad, cw + 2 * pad, ch + 2 * pad);
    g.restore();
    this.backdropKey = key;
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
    this.liveWant = { local, frame: { ...frame } };
    const target = sourceTime(this.ref, local);
    const playing = !!frame.playing && local >= 0;
    try {
      if (v.readyState >= 1 && !v.seeking && (frame.seeked || !playing || Math.abs(v.currentTime - target) > LIVE_DRIFT)) {
        if (Math.abs(v.currentTime - target) > 1e-3) {
          v.currentTime = target;
          this.liveSought = target;
        }
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

  /**
   * Live, paused: seek the clip to the time the stage asked for last when it could not seek then (its metadata was
   * not in yet, or another seek was in flight). Never while playing (the next animation frame syncs the clip), and
   * never again for a time it was already sought to: a clip shorter than its declared duration clamps that seek to
   * its end, and chasing the time would seek forever.
   */
  _resync() {
    const want = this.liveWant;
    if (!want || want.frame.playing) return;
    if (Math.abs(sourceTime(this.ref, want.local) - this.liveSought) <= 1e-3) return;
    this._syncVideo(want.local, want.frame);
  }

  /** Resolves when the image on screen is decoded (render mode waits for it before capturing). */
  ready() {
    const img = this.img;
    if (!img || !img.getAttribute('src') || typeof img.decode !== 'function') return Promise.resolve();
    if (!this.decoding) {
      const src = img.getAttribute('src');
      this.decoding = img.decode().then(
        () => {
          img.classList.remove('is-broken');
          this._backdrop(); // the frame is in: its blurred copy too, before the capture
        },
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
      for (const [type, fn] of this.listeners) this.video.removeEventListener(type, fn);
      this.listeners = [];
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
