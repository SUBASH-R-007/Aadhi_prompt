// @ts-check
/**
 * The Dynamic stage (DYNAMIC_VIDEO_DESIGN §7.3): one compositor for the live player and the MP4.
 *
 *   const stage = createDynamicStage(root, timeline, { mode: 'live' | 'render' });
 *   stage.update(t)            live: paint absolute time t (the player's clock; the narration stays the master)
 *   await stage.seek(frame)    render: paint frame/fps exactly, wait for TeX, pictures and media frames, then one
 *                              animation frame; resolves {key}, a hash of every visual input (deduplication)
 *   stage.info()               {width, height, fps, frames, scenes: [{index, f0, f1}]}
 *   stage.mediaPlan(f0, f1)    media frames frames [f0, f1) show (media.js)
 *   stage.textBoxes()          [{x, y, w, h, role, critical, ink, bg, overflow, alpha, scene}] (preflight, §7.12)
 *   stage.setLayers({captions, stage})   the caption-only layer for a burned second version (§7.5)
 *   stage.destroy()
 *
 * Each frame: the scene that owns the frame is painted by its template at scene time t, over its background at the
 * background clock (frozen in holds); then its cues (cues.js: `cue.term` and `cue.look_here` from the scene's events,
 * B2 plan §1.5); during a crossfade the previous scene's final state is painted below it (§4.9); then the caption.
 * Every write goes through the Painter, so the key covers everything that moved.
 * Only a timeline whose presentation style is "dynamic" may reach this module (a classic one is refused), and only one
 * of a look-engine generation it knows (engine.js; it feature-detects the fields of those it knows).
 */

import { h } from '../shared/dom.js';
import { sceneIndexAt } from '../player/schedule.js';
import { composite, contrastRatio } from '../motion/color.js';
import { hashSeed, mulberry32 } from '../motion/random.js';
import { Painter, entityRole, fixesOf, makeRoles, sceneIndexOfFrame, stageInfo, toneColor, zonesFor } from './core.js';
import { cameraAround, createCues, cueLayer, cueNeeds } from './cues.js';
import { engineProblem } from './engine.js';
import { fontStacks } from './fonts.js';
import { backgroundTime, captionAt, chapterTitleAt, dynamicStateAt, freezeProgress } from './state.js';
import { paintTransition, transitionAt } from './transitions.js';
import { CaptionLayer, captionStyle } from './captions.js';
import { LIVE_DRIFT, MediaView, mediaPlan as planMedia, presenterOf, frameUrl, mediaKey } from './media.js';
import boardGeneric from './templates/board_generic.js';
import chapterBumper from './templates/chapter_bumper.js';
import definitionCard from './templates/definition_card.js';
import formulaFocus from './templates/formula_focus.js';
import hookQuestion from './templates/hook_question.js';
import keyTerms from './templates/key_terms.js';
import listBuild from './templates/list_build.js';
import mediaSplit from './templates/media_split.js';
import recapRetrieval from './templates/recap_retrieval.js';
import workedSteps from './templates/worked_steps.js';
import meshDrift from './backgrounds/mesh_drift.js';
import colourBlock from './backgrounds/colour_block.js';

/** @typedef {import('../shared/types.js').Timeline} Timeline */
/** @typedef {import('../shared/types.js').TimedScene} TimedScene */
/** @typedef {import('./templates/types.js').TemplateContext} TemplateContext */
/** @typedef {import('./templates/types.js').TemplateModule} TemplateModule */
/** @typedef {import('./templates/types.js').MediaOptions} MediaOptions */
/** @typedef {import('./templates/types.js').TextEntry} TextEntry */
/** @typedef {import('./media.js').MediaFrameInfo} MediaFrameInfo */
/** @typedef {import('./core.js').Zones} Zones */

/**
 * Templates by id (B1: board_generic; B2c: the pilots formula_focus, list_build and media_split; the demo phase:
 * chapter_bumper, definition_card, hook_question, key_terms, recap_retrieval and worked_steps; an unknown or
 * unreleased id falls back to board_generic). The catalogue's registry lists exactly these as `implemented`
 * (web/tests/dynamic/catalogue.test.js, tests/compose/dynamic/test_templates.py). The resolver asks
 * `compose/dynamic/direct.py` which of them draws each scene (presentation.TEMPLATES), so a real lecture now uses
 * them; `direct.candidate` is the gate, so a template joins the resolver's set by flipping `implemented`.
 * @type {Readonly<Record<string, TemplateModule>>}
 */
export const TEMPLATES = Object.freeze({
  board_generic: boardGeneric,
  chapter_bumper: chapterBumper,
  definition_card: definitionCard,
  formula_focus: formulaFocus,
  hook_question: hookQuestion,
  key_terms: keyTerms,
  list_build: listBuild,
  media_split: mediaSplit,
  recap_retrieval: recapRetrieval,
  worked_steps: workedSteps,
});
/** Background presets by id (B1: mesh_drift and colour_block; unknown ids get mesh_drift). */
export const BACKGROUNDS = Object.freeze({ mesh_drift: meshDrift, colour_block: colourBlock });

/**
 * True for a timeline the Dynamic stage renders (its presentation style is "dynamic").
 * @param {any} timeline
 */
export function isDynamic(timeline) {
  return !!(timeline && timeline.presentation && timeline.presentation.style === 'dynamic');
}

/**
 * The template a scene is drawn with: `look.template` when the registry has it, else board_generic.
 * @param {TimedScene} scene
 * @param {Readonly<Record<string, TemplateModule>>} [registry]   TEMPLATES (a stage built with `templates` passes
 *   its own)
 * @returns {TemplateModule}
 */
export function templateFor(scene, registry = TEMPLATES) {
  const id = scene.look && scene.look.template;
  return Object.prototype.hasOwnProperty.call(registry, String(id)) ? registry[String(id)] : boardGeneric;
}

/**
 * The box a medium of `slot` in `scene` is sized for (B2 plan §1.4): its template's static `mediaBox` in `mode`'s
 * layout (with the scene's fixes), the whole stage when the template has none or names none. `mediaPlan` (render
 * layout) and `ctx.media` (the stage's mode) both ask here, so in render mode a clip's frame key is always one the plan
 * lists, whatever the template.
 * @param {TimedScene} scene
 * @param {Zones} zones
 * @param {'live' | 'render'} mode
 * @param {string | undefined} slot
 * @param {Readonly<Record<string, TemplateModule>>} [registry]
 * @returns {{w: number, h: number}}
 */
export function mediaBoxOf(scene, zones, mode, slot, registry = TEMPLATES) {
  const tpl = templateFor(scene, registry);
  const box = typeof tpl.mediaBox === 'function' ? tpl.mediaBox(scene, zones, mode, slot, fixesOf(scene.look)) : null;
  return box && box.w > 0 && box.h > 0 ? { w: box.w, h: box.h } : { w: zones.width, h: zones.height };
}

/** The text entrance a timeline without `presentation.text_entrance` gets (Soft's fade + 16 px rise, §4.8). */
export const DEFAULT_TEXT_ENTRANCE = 'fade_rise';

/**
 * `ctx.motion` (plan §1.4): the personality's motion values as the timeline carries them (never from the catalogue,
 * plan §1.1): `scale`, the duration scale (`presentation.motion_scale`, §4.8: Crisp 0.85, Soft 1.15; 1 when absent:
 * B1's timings), and `entrance`, the text entrance (`presentation.text_entrance`, B2c: `fade_rise` Soft,
 * `baseline_reveal` Crisp, the W2 ones by name; DEFAULT_TEXT_ENTRANCE when absent). Frozen.
 * @param {any} presentation
 * @returns {Readonly<{scale: number, entrance: string}>}
 */
export function motionOf(presentation) {
  const p = presentation || {};
  const scale = typeof p.motion_scale === 'number' && Number.isFinite(p.motion_scale) && p.motion_scale > 0 && p.motion_scale <= 4
    ? p.motion_scale : 1;
  const entrance = typeof p.text_entrance === 'string' && /^[a-z0-9_-]{1,40}$/.test(p.text_entrance)
    ? p.text_entrance : DEFAULT_TEXT_ENTRANCE;
  return Object.freeze({ scale, entrance });
}

/**
 * @param {TimedScene} scene
 */
export function backgroundFor(scene) {
  const id = scene.look && scene.look.background ? scene.look.background.preset : null;
  return /** @type {Record<string, typeof meshDrift>} */ (BACKGROUNDS)[String(id)] || meshDrift;
}

/**
 * CSS font stacks of the presentation (display, body, mono): fonts.js, from presentation.fonts and the face table (B2b;
 * a B1-form presentation draws exactly what the B1 stage drew).
 */
export { fontStacks } from './fonts.js';

/** The quieter ink on a colour block and its 3:1 floor (R-53): core.js, where the block roles are made. */
export { BLOCK_INK2_CONTRAST, blockInk2 } from './core.js';

/**
 * Keep a live clip at `t` (re-sought when it drifts by more than 0.1 s, or after a seek), playing at the player's
 * rate (like `MediaView`: at 1.5x a clip left at 1x would fall 0.1 s behind every 0.2 s and be re-sought, a stutter)
 * and playing or paused.
 * @param {HTMLVideoElement} v
 * @param {number} t
 * @param {boolean} playing
 * @param {MediaFrameInfo} frameInfo
 */
export function syncClip(v, t, playing, frameInfo) {
  try {
    if (v.readyState >= 1 && !v.seeking && (frameInfo.seeked || !playing || Math.abs(v.currentTime - t) > LIVE_DRIFT)) {
      if (Math.abs(v.currentTime - t) > 1e-3) v.currentTime = Math.max(0, t);
    }
    const rate = Number(frameInfo.rate) > 0 ? Number(frameInfo.rate) : 1;
    if (v.playbackRate !== rate) v.playbackRate = rate;
    if (playing && v.paused) {
      const pr = v.play();
      if (pr && typeof pr.catch === 'function') pr.catch(() => undefined);
    } else if (!playing && !v.paused) {
      v.pause();
    }
  } catch {
    /* not seekable yet */
  }
}

/**
 * One scene on the stage: its background, its template, its cues and (from B2) its presenter clip.
 *
 * DOM: `.dyn-scene` > background, [presenter], content, [cue layer]. The content is the template's layer (`ctx.root`);
 * for a scene whose template may push the camera on a look_here (push_ok, cues.js) it sits inside the Slot's own
 * `.dyn-camera` wrapper (templates never transform `ctx.root`, plan §1.4), and a scene with look_here cues gets the
 * `.dyn-cues` layer above it (below the captions, which the stage draws above every scene). Both exist only for a scene
 * with cue events its template draws, so every other scene's DOM, writes and frame keys are what they were before B2c.
 */
class Slot {
  /**
   * @param {any} stage   the stage internals
   * @param {number} index
   */
  constructor(stage, index) {
    const timeline = /** @type {Timeline} */ (stage.timeline);
    /** @type {TimedScene} */
    this.scene = timeline.scenes[index];
    this.index = index;
    this.stage = stage;
    const scene = this.scene;
    const look = scene.look || null;
    const roles = makeRoles(timeline.presentation, look);
    /** @type {Promise<unknown>[]} */
    this.pending = [];
    /** @type {MediaView[]} */
    this.media = [];
    const background = look && look.background ? look.background : {};
    const seed = Number(background.seed) >>> 0;
    // text drawn on a colour block takes the block's inks (the `block.ink` / `block.ink2` roles: the look's stored
    // ones, else picked over the block's colours as B1 did), on anything else the content inks
    const block = backgroundFor(scene) === colourBlock;
    const ink = block ? roles.color('block.ink') : roles.color('ink');
    this.template = templateFor(scene, stage.templates);
    const needs = cueNeeds(scene, this.template.id);
    // The scene's own page colour and ink for what the stylesheet draws without a template colour (dynamic.css: the
    // matte around contained media, inherited text): from the roles, so a light family never gets a dark literal.
    this.el = h('div', {
      class: 'dyn-scene', dataset: { scene: String(index) },
      style: { '--dyn-page': toneColor(roles, background.params && background.params.tone), '--dyn-ink': ink },
    });
    this.bg = backgroundFor(scene).build({
      roles, zones: stage.zones, params: background.params || {}, seed, mode: stage.mode, pending: this.pending,
    });
    this.el.appendChild(this.bg.el);
    const content = h('div', { class: 'dyn-content' });
    /** @type {HTMLElement | null} the push wrapper (cues.js), only where a look_here may push */
    this.camera = needs.camera ? cameraAround(content) : null;
    this.el.appendChild(this.camera || content);
    /** @type {HTMLElement | null} the look_here outline layer, only for a scene with look_here cues */
    this.cueLayer = needs.layer ? cueLayer() : null;
    if (this.cueLayer) this.el.appendChild(this.cueLayer);
    // attached before the template builds (so it can measure), in scene order whatever the order of seeks
    const next = [...stage.scenesEl.children].find((c) => Number(/** @type {HTMLElement} */ (c).dataset.scene) > index);
    stage.scenesEl.insertBefore(this.el, next || null);
    // the colour behind text drawn on the background: its worst case against the ink (R-53, analytic); every colour
    // of the background too, for text over a translucent scrim (its worst case depends on the text's own ink)
    const bgs = this.bg.colors();
    const worst = bgs.reduce((a, b) => (contrastRatio(ink, b) < contrastRatio(ink, a) ? b : a));
    // No `timeline` in the context (B2 plan §1.4): what the template shows of other scenes is handed to it (the
    // chapter its kicker names; chunks.py hashes that title into the key of every chunk that shows the scene)
    /** @type {TemplateContext} */
    this.ctx = {
      scene, index, look, chapter: chapterTitleAt(timeline, Number(scene.start)), roles, zones: stage.zones,
      format: stage.zones.format,
      rng: mulberry32(hashSeed(seed, scene.scene_id, 'template')),
      mode: stage.mode, reducedMotion: stage.reducedMotion, root: content, paint: stage.painter, pending: this.pending,
      media: (ref, opts) => this._media(ref, opts),
      onBackground: { ink, ink2: block ? roles.color('block.ink2') : roles.color('ink2'), bg: worst, bgs, block },
      rectOf: (el) => stage.rectOf(el),
      locale: stage.locale,
      renderTex: stage.renderTex,
      motion: stage.motion,
      outgoing: null,
      entityRole: (symbol) => entityRole(stage.entityColors, symbol),
    };
    this.ctx.view = this.template.build(this.ctx);
    /** @type {import('./cues.js').SlotCues | null} */
    this.cues = needs.events.length ? createCues({
      template: this.template, ctx: this.ctx, events: needs.events, layer: this.cueLayer, camera: this.camera,
      termStyle: stage.cueTermStyle, pushOk: needs.camera, reducedMotion: stage.reducedMotion,
    }) : null;
    this.presenter = this._presenter();
    this.laidOut = false;
    /** @type {Promise<void> | null} */
    this._prepared = null;
  }

  /**
   * `ctx.media`: a picture or clip, sized for its template's `mediaBox` of `opts.slot` (`mediaBoxOf`, the box
   * `mediaPlan` extracts a clip at), or a picture for the box it passes (a picture has no frame key).
   * @param {import('../shared/types.js').MediaRef} ref
   * @param {MediaOptions} [opts]
   */
  _media(ref, opts = {}) {
    const own = ref.kind !== 'video' && opts.box && opts.box.w > 0 && opts.box.h > 0 ? opts.box : null;
    const box = own || mediaBoxOf(this.scene, this.stage.zones, this.stage.mode, opts.slot, this.stage.templates);
    const view = new MediaView(ref, {
      mode: this.stage.mode, box, start: opts.start, sceneDuration: Number(this.scene.duration) || 0,
      fps: this.stage.info.fps, reducedMotion: this.stage.reducedMotion, alt: opts.alt,
    });
    this.media.push(view);
    if (!view.key) this.pending.push(view.ready()); // pictures: decoded before the first frame (frames: per seek)
    return view;
  }

  /** The presenter clip (ResolvedLook.presenter, B2): frames in render mode, a muted clip live. */
  _presenter() {
    const p = presenterOf(this.scene);
    if (!p) return null;
    /** @type {HTMLImageElement | HTMLVideoElement | null} */
    let media = null;
    const key = mediaKey('aadhi', p.clip_key, p.size);
    if (this.stage.mode === 'render') media = h('img', { class: 'dyn-media-el', alt: '', decoding: 'sync' });
    else if (p.url) {
      // determinism-allow video: live player only; render mode shows the worker's pre-extracted frames as <img>
      media = h('video', { class: 'dyn-media-el', src: p.url, preload: 'auto', playsinline: true });
      media.muted = true;
    }
    if (!media) return null;
    const el = h('div', { class: 'dyn-presenter', style: { left: p.rect.x, top: p.rect.y, width: p.rect.w, height: p.rect.h } }, media);
    this.el.insertBefore(el, this.el.children[1] || null);
    return { el, media, spec: p, key };
  }

  /** Wait for TeX, pictures and fonts, then measure (once). Never rejects. */
  prepare() {
    if (!this._prepared) {
      this._prepared = (async () => {
        let count = -1;
        while (count !== this.pending.length) {
          count = this.pending.length;
          await Promise.allSettled(this.pending.slice());
        }
        this.layout();
      })();
    }
    return this._prepared;
  }

  /** Measure and fit the template (idempotent). */
  layout() {
    if (this.stage.destroyed || !this.el.isConnected) return;
    if (typeof this.template.layout === 'function') this.template.layout(this.ctx);
    this.laidOut = true;
  }

  /**
   * Paint scene time `sceneT` with the background at background-clock time `bgT`.
   * @param {Painter} paint
   * @param {number} sceneT
   * @param {number} bgT
   * @param {MediaFrameInfo} frameInfo
   */
  update(paint, sceneT, bgT, frameInfo) {
    this.ctx.frameInfo = frameInfo;
    paint.note(`s${this.index}`); // the time itself is not a visual input: a frozen hold repeats its key
    this.bg.update(paint, bgT, freezeProgress(this.scene, sceneT));
    if (this.cues) this.cues.begin(paint);
    this.template.update(this.ctx, sceneT, dynamicStateAt(this.scene, sceneT));
    // the cues after the template: they read its geometry as painted this frame (plan §1.5)
    if (this.cues) this.cues.update(paint, sceneT);
    const pr = this.presenter;
    if (pr) {
      const on = sceneT < pr.spec.t_exit;
      paint.style(pr.el, 'visibility', on ? 'visible' : 'hidden');
      if (pr.media instanceof HTMLImageElement) {
        if (on && !frameInfo.noFrames) paint.attr(pr.media, 'src', frameUrl(pr.key, Math.floor(sceneT * pr.spec.src_fps + 1e-6)));
      } else {
        syncClip(/** @type {HTMLVideoElement} */ (pr.media), sceneT, on && !!frameInfo.playing, frameInfo);
      }
    }
  }

  /** Resolves when every picture and media frame on screen is decoded (rejects on a missing media frame). */
  mediaReady() {
    /** @type {Promise<unknown>[]} */
    const waits = this.media.filter((m) => m.key).map((m) => m.ready());
    const pr = this.presenter;
    if (pr && pr.media instanceof HTMLImageElement && pr.media.getAttribute('src') && typeof pr.media.decode === 'function') {
      waits.push(pr.media.decode());
    }
    return Promise.all(waits);
  }

  /**
   * The template's text entries; text a look_here dims keeps its place among the critical text, reported with the
   * colours it is drawn in (plan §1.5): its group at opacity f over what lies behind the group (the background's
   * worst case), so the ink is ink at f over that and the background behind the text is its own colour at f over it.
   * @returns {TextEntry[]}
   */
  texts() {
    const out = typeof this.template.texts === 'function' ? this.template.texts(this.ctx) : [];
    if (!this.cues) return out;
    const cues = this.cues;
    const under = this.ctx.onBackground.bg;
    return out.map((e) => {
      const f = cues.dimOf(e.el);
      if (!(f < 1)) return e;
      const a = Math.round(f * 255).toString(16).padStart(2, '0');
      const over = (/** @type {string} */ c) => composite(`${c.slice(0, 7)}${a}`, under);
      return { ...e, ink: over(e.ink), bg: e.bg ? over(e.bg) : e.bg };
    });
  }

  destroy() {
    for (const m of this.media) m.destroy();
    this.media = [];
    const pr = this.presenter;
    if (pr && !(pr.media instanceof HTMLImageElement)) {
      try {
        pr.media.pause();
        pr.media.removeAttribute('src');
        pr.media.load();
      } catch {
        /* ignore */
      }
    }
    this.el.remove();
  }
}

/**
 * @typedef {object} StageOptions
 * @property {'live' | 'render'} [mode]
 * @property {boolean} [captions]       captions drawn (default: on live; the presentation's captions_burn in render)
 * @property {boolean} [reducedMotion]  live only (§4.8): entrances fade in place, no Ken Burns, still background
 * @property {() => Promise<unknown>} [texIdle]   resolves when MathJax is idle (render mode)
 * @property {() => Promise<unknown>} [frame]     resolves on the next animation frame (render mode)
 * @property {() => void} [onInvalidate]  live: called when a scene finished laying out (repaint the current time)
 * @property {import('../player/richtext.js').TexRenderer} [renderTex]   TeX converter: the render page passes its
 *   page-wide one (render.js pageTexRenderer: never evicts, counts what templates leave out of tex()); live mode and
 *   the tests without one get richtext's MathJax with its 400-string cache
 * @property {Readonly<Record<string, TemplateModule>>} [templates]   templates added to TEMPLATES for this stage only,
 *   by id (a test seam: the unit tests drive cues.js through a stand-in template, and a template's own tests may run
 *   before it is registered). Each id keeps its catalogue entry (its consumes and push_ok). Never passed by the render
 *   page, the player or the editor.
 */

/**
 * Create the stage inside `root` for a Dynamic timeline.
 * @param {HTMLElement} root
 * @param {Timeline} timeline
 * @param {StageOptions} [opts]
 */
export function createDynamicStage(root, timeline, opts = {}) {
  if (!isDynamic(timeline) || !Array.isArray(timeline.scenes)) {
    const why = 'createDynamicStage: the timeline has no Dynamic presentation';
    throw Object.assign(new TypeError(why), { code: 'timeline_invalid' });
  }
  const newer = engineProblem(timeline);
  if (newer) throw Object.assign(new TypeError(`createDynamicStage: ${newer}`), { code: 'timeline_invalid' });
  const mode = opts.mode === 'render' ? 'render' : 'live';
  const info = stageInfo(timeline);
  const zones = zonesFor(timeline);
  const presentation = /** @type {any} */ (timeline.presentation);
  const locale = String(timeline.board_language || timeline.language || 'en');
  const fonts = fontStacks(presentation, locale);
  // the stage's own page colour and ink (under the scenes; each scene sets its own): the first scene's roles
  const first = timeline.scenes[0];
  const firstLook = first && first.look ? first.look : null;
  const firstRoles = makeRoles(presentation, firstLook);
  const el = h('div', {
    class: ['dyn-stage', `dyn-stage--${zones.format}`, `dyn-mode--${mode}`],
    lang: locale,
    style: {
      width: info.width, height: info.height, '--dyn-font-display': fonts.display, '--dyn-font-body': fonts.body,
      '--dyn-font-mono': fonts.mono, '--dyn-ink': firstRoles.color('ink'),
      '--dyn-page': toneColor(firstRoles, firstLook && firstLook.background && firstLook.background.params ? firstLook.background.params.tone : null),
    },
  });
  const scenesEl = h('div', { class: 'dyn-scenes' });
  const captionHost = h('div', { class: 'dyn-captionlayer' });
  el.append(scenesEl, captionHost);
  root.appendChild(el);

  const stage = {
    timeline, mode, info, zones, locale, el, scenesEl, painter: new Painter(), destroyed: false,
    reducedMotion: mode === 'live' && !!opts.reducedMotion,
    renderTex: opts.renderTex,
    templates: opts.templates ? Object.freeze({ ...TEMPLATES, ...opts.templates }) : TEMPLATES,
    motion: motionOf(presentation),
    cueTermStyle: /** @type {'marker' | 'underline'} */ (presentation.cue_term_style === 'underline' ? 'underline' : 'marker'),
    entityColors: presentation.entity_colors && typeof presentation.entity_colors === 'object' ? presentation.entity_colors : {},
    /** @param {Element} node */
    rectOf(node) {
      if (!node || !node.isConnected) return null;
      const o = el.getBoundingClientRect();
      const r = node.getBoundingClientRect();
      const k = o.width > 0 ? o.width / info.width : 1;
      return { x: (r.left - o.left) / k, y: (r.top - o.top) / k, w: r.width / k, h: r.height / k };
    },
  };
  const captions = new CaptionLayer(captionHost, { zones, style: captionStyle(presentation, zones.format), locale });
  // A face that arrives late changes text widths, so every mounted scene measures again. Live: at once, then a repaint.
  // Render: the page preloads the faces of the lecture's scripts before `ready`, but text can still pull in a face it
  // did not preload (a character another script's subset claims, a word in a third script); seek() then waits for it
  // and measures and paints the frame again, so a frame never depends on whether the face was in when it was laid out.
  const fontSet = el.ownerDocument ? /** @type {any} */ (el.ownerDocument).fonts : null;
  let fontsChanged = false;
  const onFonts = () => {
    if (stage.destroyed) return;
    if (mode === 'render') {
      fontsChanged = true;
      return;
    }
    for (const s of slots.values()) s.layout();
    if (opts.onInvalidate) opts.onInvalidate();
  };
  if (fontSet && typeof fontSet.addEventListener === 'function') fontSet.addEventListener('loadingdone', onFonts);
  const layers = { stage: true, captions: opts.captions !== undefined ? !!opts.captions : mode === 'render' ? !!presentation.captions_burn : true };
  captions.setEnabled(layers.captions);
  /** @type {Map<number, Slot>} */
  const slots = new Map();
  /** @type {{index: number, sceneT: number, t: number, outgoing: number | null}} */
  let current = { index: -1, sceneT: 0, t: 0, outgoing: null };
  /** @type {Promise<unknown>} */
  let queue = Promise.resolve();
  const texIdle = opts.texIdle || (() => Promise.resolve());
  const nextFrame = opts.frame || (() => Promise.resolve());

  /** @param {number} i */
  const slotFor = (i) => {
    let s = slots.get(i);
    if (!s) {
      s = new Slot(stage, i);
      slots.set(i, s);
      if (mode === 'live') {
        s.layout(); // measure now; again once TeX and pictures are in
        const built = s;
        s.prepare().then(() => {
          if (!stage.destroyed && slots.get(i) === built && opts.onInvalidate) opts.onInvalidate();
        });
      }
    }
    return s;
  };

  /**
   * Scenes on screen at (i, sceneT): the scene and, during a crossfade, the one before it.
   * @param {number} i
   * @param {number} sceneT
   */
  const onScreen = (i, sceneT) => {
    const tr = transitionAt(timeline, i, sceneT);
    return { tr, outgoing: tr && tr.outgoing !== null ? tr.outgoing : null };
  };

  /** @param {Set<number>} keep */
  const prune = (keep) => {
    for (const [i, s] of slots) {
      if (!keep.has(i)) {
        s.destroy();
        slots.delete(i);
      }
    }
  };

  /**
   * Paint one instant. Returns the frame key.
   * @param {number} i        scene index
   * @param {number} sceneT   scene-relative time
   * @param {number} t        absolute time
   * @param {MediaFrameInfo} frameInfo
   */
  const paint = (i, sceneT, t, frameInfo) => {
    const p = stage.painter;
    p.begin();
    const scene = timeline.scenes[i];
    const { tr, outgoing } = onScreen(i, sceneT);
    p.note(`L${layers.stage ? 1 : 0}${layers.captions ? 1 : 0}|i${i}|o${outgoing === null ? '' : outgoing}`);
    if (layers.stage) {
      prune(new Set(outgoing === null ? [i] : [i, outgoing]));
      const bgT = stage.reducedMotion ? 0 : backgroundTime(timeline, t);
      const inc = slotFor(i);
      // what the incoming template may know of the scene it is entered from (plan §1.4 ctx.outgoing): data only
      inc.ctx.outgoing = outgoing === null || !tr ? null : Object.freeze({
        index: outgoing, scene: timeline.scenes[outgoing], look: timeline.scenes[outgoing].look || null, kind: tr.kind, p: tr.p,
      });
      inc.update(p, sceneT, bgT, frameInfo);
      /** @type {Slot | null} */
      let out = null;
      if (outgoing !== null) {
        out = slotFor(outgoing);
        out.update(p, Number(timeline.scenes[outgoing].duration), bgT, frameInfo);
      }
      paintTransition(p, inc.el, out ? out.el : null, tr);
    }
    p.style(scenesEl, 'visibility', layers.stage ? 'visible' : 'hidden');
    captions.update(p, layers.captions ? captionAt(scene, sceneT) : null);
    current = { index: i, sceneT, t, outgoing };
    return p.digest();
  };

  const api = {
    el,

    /**
     * Live: paint absolute time t.
     * @param {number} t
     * @param {MediaFrameInfo} [frameInfo]
     */
    update(t, frameInfo = {}) {
      if (stage.destroyed || !timeline.scenes.length) return '';
      const total = Number(timeline.total_duration) || 0;
      const tt = Math.min(Math.max(0, Number(t) || 0), total);
      const i = Math.max(0, sceneIndexAt(timeline, tt));
      const scene = timeline.scenes[i];
      const sceneT = Math.min(Math.max(0, tt - Number(scene.start)), Number(scene.duration));
      return paint(i, sceneT, tt, frameInfo);
    },

    /**
     * Render: paint frame `frame` (t = frame / fps exactly) and wait until it is stable. Calls are serialised;
     * frames may be requested in any order. `{frames: false}` (the render page's boot paint only) leaves the
     * pre-extracted media frames unrequested: the worker serves them per chunk (§7.7), not before `ready`.
     * @param {number} frame
     * @param {{frames?: boolean}} [opts]
     * @returns {Promise<{key: string}>}
     */
    seek(frame, opts = {}) {
      const frameInfo = opts.frames === false ? { noFrames: true } : {};
      const job = queue.then(async () => {
        if (stage.destroyed) throw new Error('stage destroyed');
        if (typeof frame !== 'number' || !Number.isInteger(frame) || frame < 0 || frame >= info.frames) {
          throw new RangeError(`seek: frame ${String(frame)} outside 0..${info.frames - 1}`);
        }
        const t = frame / info.fps;
        const i = sceneIndexOfFrame(info.scenes, frame);
        const scene = timeline.scenes[i];
        const sceneT = Math.min(Math.max(0, t - Number(scene.start)), Number(scene.duration));
        if (layers.stage) {
          const { outgoing } = onScreen(i, sceneT);
          const need = outgoing === null ? [i] : [outgoing, i];
          for (const n of need) await slotFor(n).prepare();
        }
        let key = paint(i, sceneT, t, frameInfo);
        if (layers.stage) {
          await Promise.all([...slots.values()].map((s) => s.mediaReady()));
        }
        await texIdle();
        await nextFrame();
        // A face this frame's text pulled in (font-display: block hides that text until it is in) or one that came in
        // since the scenes were measured: wait for it, measure again and repaint (no timers: the FontFaceSet's promise).
        if (fontSet && (fontSet.status === 'loading' || fontsChanged)) {
          while (fontSet.status === 'loading') await fontSet.ready;
          fontsChanged = false;
          for (const s of slots.values()) s.layout();
          key = paint(i, sceneT, t, frameInfo);
          if (layers.stage) await Promise.all([...slots.values()].map((s) => s.mediaReady()));
          await nextFrame();
        }
        return { key };
      });
      queue = job.catch(() => undefined);
      return job;
    },

    info() {
      return { ...info, scenes: info.scenes.map((s) => ({ ...s })) };
    },

    /**
     * @param {number} f0
     * @param {number} f1  exclusive
     */
    mediaPlan(f0, f1) {
      // the render page's layout: what the worker extracts frames for (live mode shows clips as <video>)
      return planMedia(timeline, Number(f0), Number(f1), (scene, slot) => mediaBoxOf(scene, zones, 'render', slot, stage.templates));
    },

    /** Text boxes of the frame on screen, in stage px. */
    textBoxes() {
      /** @type {any[]} */
      const out = [];
      /** @param {TextEntry} e @param {number | null} scene */
      const add = (e, scene) => {
        const alpha = e.alpha === undefined ? 1 : e.alpha;
        if (!(alpha > 0.01)) return;
        const r = stage.rectOf(e.el);
        if (!r || !(r.w > 0 && r.h > 0)) return;
        const overflow = typeof e.overflow === 'number' ? Math.round(e.overflow * 10) / 10 : e.overflow ? 1 : 0;
        out.push({
          x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.w), h: Math.round(r.h), role: e.role,
          // critical text is checked only when fully shown: mid-entrance it still moves (and cannot be read)
          critical: !!e.critical && alpha >= 0.99, ink: e.ink, bg: e.bg, overflow,
          alpha: Math.round(alpha * 1000) / 1000, scene,
          ...(e.slot ? { slot: e.slot } : {}), ...(e.size ? { size: Math.round(e.size * 10) / 10 } : {}),
          ...(e.min ? { min: Math.round(e.min * 10) / 10 } : {}), ...(e.weight ? { weight: e.weight } : {}),
          ...(e.text ? { text: String(e.text).slice(0, 120) } : {}),
        });
      };
      if (layers.stage) {
        for (const i of [current.outgoing, current.index]) {
          const s = i === null ? undefined : slots.get(i);
          if (s) for (const e of s.texts()) add(e, i);
        }
      }
      const cap = captions.textEntry();
      if (cap) add(cap, current.index >= 0 ? current.index : null);
      return out;
    },

    /**
     * Which layers are drawn: {captions, stage}. `{stage: false, captions: true}` is the caption-only layer on a
     * transparent page (§7.5 second version).
     * @param {{captions?: boolean, stage?: boolean}} spec
     */
    setLayers(spec) {
      const s = spec || {};
      if (s.captions !== undefined) layers.captions = !!s.captions;
      if (s.stage !== undefined) layers.stage = !!s.stage;
      captions.setEnabled(layers.captions);
      el.classList.toggle('is-captions-only', !layers.stage);
      if (mode === 'live' && current.index >= 0 && !stage.destroyed) paint(current.index, current.sceneT, current.t, {});
      return { ...layers };
    },

    /** The layers drawn now. */
    layers() {
      return { ...layers };
    },

    destroy() {
      if (stage.destroyed) return;
      stage.destroyed = true;
      if (fontSet && typeof fontSet.removeEventListener === 'function') fontSet.removeEventListener('loadingdone', onFonts);
      prune(new Set());
      captions.destroy();
      el.remove();
    },
  };
  return api;
}
