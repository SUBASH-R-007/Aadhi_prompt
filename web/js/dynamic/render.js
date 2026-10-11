// @ts-check
/**
 * Render page of the motion engine (web/render-dynamic.html, served at /render-dynamic) driven by the render worker
 * (aadhi/compose/dynamic/stage_page.py). Stage contract v3 (DYNAMIC_VIDEO_DESIGN §7.3):
 *
 *   window.aadhiStage = {
 *     version: 3,
 *     ready,             Promise<{webgl: boolean}>: timeline loaded, every font in use loaded, MathJax idle with every
 *                        TeX string of the timeline converted, the first frame painted; rejects with {code, message}
 *     info(),            {width, height, fps, frames, scenes: [{index, f0, f1}]}
 *     mediaPlan(f0, f1), [{key, kind: 'video' | 'aadhi', srcFps, frames: [n, ...], size: [w, h], ...}] for [f0, f1)
 *     seek(frame),       Promise<{key}>: t = frame / fps; pure; frames in any order; no fixed sleeps
 *     textBoxes(),       [{x, y, w, h, role, critical, ink, bg, overflow, ...}] of the frame on screen
 *     setLayers(spec),   {captions, stage}: {stage: false, captions: true} is the caption-only layer
 *   };
 *
 * Same token-in-fragment scheme as /render-frame (web/js/render/render.js): the scoped token is read from
 * `#token=...`, removed from the URL at once and sent as `Authorization: Bearer`. After `ready` the timer guard
 * (guard.js) is installed. The classic page and its contract (`aadhiRender`, version 2) are untouched.
 */

import { loadMathJax as defaultLoadMathJax, texIdle as defaultTexIdle } from '../shared/libs.js';
import { RenderError, applyStageSize, fetchTimeline, stageSize, takeToken } from '../render/render.js';
import { fontRequests, preloadFonts } from '../render/fonts.js';
import { mathSpans, texNode } from '../player/richtext.js';
import { createDynamicStage, isDynamic } from './stage.js';
import { installTimerGuard } from './guard.js';

/** @typedef {import('../shared/types.js').Timeline} Timeline */

export const STAGE_VERSION = 3;

/**
 * Every TeX string the timeline shows, with its display mode (converted before `ready`, so no MathJax work, and
 * none of its timers, happens while frames are captured).
 * @param {Timeline} timeline
 * @returns {{tex: string, display: boolean}[]}
 */
export function timelineTex(timeline) {
  /** @type {Map<string, {tex: string, display: boolean}>} */
  const out = new Map();
  /** @param {string | null | undefined} tex @param {boolean} display */
  const add = (tex, display) => {
    if (typeof tex === 'string' && tex.trim()) out.set(`${display ? 'D' : 'I'}:${tex}`, { tex, display });
  };
  /** @param {unknown} text */
  const spans = (text) => {
    if (typeof text === 'string' && text.includes('$')) for (const tex of mathSpans(text)) add(tex, false);
  };
  for (const scene of timeline.scenes || []) {
    for (const item of scene.board || []) {
      if (item.kind === 'formula') add(item.latex, true);
      for (const v of item.variables || []) add(v.symbol_latex, false);
      for (const text of [item.text, item.term, item.justification, item.caption, ...(item.headers || []), ...(item.rows || []).flat()]) spans(text);
    }
    const quiz = scene.quiz;
    if (quiz) for (const text of [quiz.question, quiz.explanation, ...(quiz.options || [])]) spans(text);
  }
  return [...out.values()];
}

/**
 * Install `window.aadhiStage` (not ready yet: every method throws `not_ready`) and return it with the functions
 * that settle `ready`.
 * @param {any} win
 */
export function installStageApi(win) {
  /** @type {(v: {webgl: boolean}) => void} */
  let resolve = () => {};
  /** @type {(e: unknown) => void} */
  let reject = () => {};
  /** @type {Promise<{webgl: boolean}>} */
  const ready = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  ready.catch(() => undefined); // a boot failure is read through `ready` by the worker: never an unhandled rejection
  const notReady = () => {
    throw new RenderError('not_ready', 'aadhiStage is not ready');
  };
  const api = {
    version: STAGE_VERSION,
    ready,
    /** @type {{code: string, message: string} | null} */
    error: null,
    /** @type {() => any} */
    info: notReady,
    /** @type {(f0: number, f1: number) => any[]} */
    mediaPlan: notReady,
    /** @type {(frame: number) => Promise<{key: string}>} */
    seek: async () => notReady(),
    /** @type {() => any[]} */
    textBoxes: notReady,
    /** @type {(spec: {captions?: boolean, stage?: boolean}) => any} */
    setLayers: notReady,
  };
  win.aadhiStage = api;
  return { api, resolve, reject };
}

/**
 * Whether the page can create a WebGL2 context (only probed when the timeline uses a shader preset, B7).
 * @param {Document} doc
 */
export function probeWebgl(doc) {
  try {
    const canvas = doc.createElement('canvas');
    const gl = /** @type {any} */ (canvas.getContext('webgl2'));
    if (!gl) return false;
    const lose = gl.getExtension('WEBGL_lose_context');
    if (lose) lose.loseContext();
    return true;
  } catch {
    return false;
  }
}

/**
 * @param {Timeline} timeline
 */
function usesShaders(timeline) {
  return (timeline.scenes || []).some((s) => {
    const preset = s.look && s.look.background ? String(s.look.background.preset || '') : '';
    return preset.startsWith('shader_');
  });
}

/**
 * @typedef {object} BootDeps
 * @property {any} [window]
 * @property {typeof fetch} [fetch]
 * @property {() => Promise<any>} [loadMathJax]
 * @property {() => Promise<unknown>} [texIdle]
 * @property {(tex: string, display: boolean) => Promise<Element>} [renderTex]   TeX converter (tests; default MathJax)
 * @property {() => Promise<void>} [frame]     resolves on the next animation frame
 * @property {boolean} [guard]                 install the timer guard after ready (default true)
 */

/**
 * Boot the page. Settles `aadhiStage.ready`; never throws (failures reject `ready` with {code, message}).
 * @param {BootDeps} [deps]
 */
export async function boot(deps = {}) {
  const win = deps.window || window;
  const doc = /** @type {Document} */ (win.document);
  const fetchImpl = deps.fetch || win.fetch.bind(win);
  const loadMathJax = deps.loadMathJax || defaultLoadMathJax;
  const texIdle = deps.texIdle || defaultTexIdle;
  // determinism-allow raf: seek() waits one animation frame before the capture (§7.3 step 6), never a loop
  const frame = deps.frame || (() => new Promise((resolve) => win.requestAnimationFrame(() => resolve(undefined))));
  const { api, resolve, reject } = installStageApi(win);
  try {
    const token = takeToken(win.location, win.history);
    let root = /** @type {HTMLElement | null} */ (doc.querySelector('[data-aadhi-stage-root]'));
    if (!root) {
      root = doc.createElement('div');
      root.setAttribute('data-aadhi-stage-root', '');
      doc.body.appendChild(root);
    }
    const timeline = /** @type {Timeline} */ (await fetchTimeline(token, fetchImpl));
    if (!isDynamic(timeline)) {
      // a classic timeline must never reach the motion engine (the worker picks the route from the timeline)
      throw new RenderError('timeline_invalid', 'the timeline has no Dynamic presentation (use /render-frame)');
    }
    doc.documentElement.lang = String(timeline.board_language || timeline.language || 'en');
    applyStageSize(doc, stageSize(timeline));
    const fonts = await preloadFonts(doc.fonts, fontRequests(timeline));
    // Every face is vendored (web/vendor/fonts.css): a missing one is a deployment error, and fallback fonts would
    // wrap text differently from the live player.
    if (fonts.missing.length) throw new RenderError('fonts_missing', `no @font-face loaded for: ${fonts.missing.join(', ')}`);
    const tex = timelineTex(timeline);
    if (tex.length) {
      if (!deps.renderTex) await loadMathJax();
      await Promise.allSettled(tex.map((x) => (deps.renderTex ? deps.renderTex(x.tex, x.display) : texNode(x.tex, x.display))));
      await texIdle();
    }
    const webgl = usesShaders(timeline) ? probeWebgl(doc) : false;
    const stage = createDynamicStage(root, timeline, { mode: 'render', texIdle, frame, renderTex: deps.renderTex });
    win.__aadhiDynamicStage = stage; // debugging only
    // The first frame: its fonts, TeX and pictures are in, so `ready` means ready. Media frames are not: the worker
    // serves them per chunk (§7.7), and a lecture that opens on a clip would otherwise never boot.
    await stage.seek(0, { frames: false });
    api.info = () => stage.info();
    api.mediaPlan = (f0, f1) => stage.mediaPlan(f0, f1);
    api.seek = (f) => stage.seek(f);
    api.textBoxes = () => stage.textBoxes();
    api.setLayers = (spec) => stage.setLayers(spec);
    if (deps.guard !== false) installTimerGuard(win);
    resolve({ webgl });
    return api;
  } catch (e) {
    const err = e instanceof Error ? e : new Error(String(e));
    const code = /** @type {any} */ (err).code || 'boot_failed';
    api.error = { code, message: err.message };
    console.error('Dynamic render page failed to boot:', err);
    reject(Object.assign(new Error(err.message), { code }));
    return api;
  }
}

if (typeof document !== 'undefined' && document.documentElement && document.documentElement.dataset.aadhiRender === 'dynamic') {
  boot();
}
