// @ts-check
/**
 * Render-mode page (web/render.html, served at /render-frame) driven by Playwright in the
 * `render_video` job. Contract (docs/ARCHITECTURE.md §9):
 *
 *   window.aadhiRender = {
 *     ready: boolean,            true after timeline, fonts (incl. the timeline's scripts) and MathJax
 *     error: {code, message} | null,
 *     states(sceneIndex) -> [{t, key}],
 *     show({scene, t}) -> Promise<{state_key, media_rect, panel_media_rect, media_fit, notices?}>,
 *                                notices: side-panel placeholder texts rendered title-only instead
 *     showIntro({t}) -> Promise<{state_key, ...}>,
 *   }
 *
 * Auth: a scoped render token arrives in the URL fragment (`#token=...`); it is removed from the
 * URL immediately (history.replaceState) and sent as `Authorization: Bearer` (no cookies).
 */

import { loadMathJax as defaultLoadMathJax, texIdle as defaultTexIdle } from '../shared/libs.js';
import { fontRequests, preloadFonts } from './fonts.js';

export const TIMELINE_ENDPOINT = '/api/render/timeline';
export const MAX_TOKEN_LENGTH = 4096;
/** JWT-like: base64url segments separated by dots. */
export const TOKEN_PATTERN = /^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+){0,4}$/;
export const PLAYER_METHODS = Object.freeze(['load', 'states', 'renderState', 'renderIntro', 'destroy']);
export const STAGE = Object.freeze({ width: 1920, height: 1080 });

export class RenderError extends Error {
  /**
   * @param {string} code
   * @param {string} message
   */
  constructor(code, message) {
    super(message);
    this.name = 'RenderError';
    this.code = code;
  }
}

/**
 * Read the scoped token from the fragment and drop the fragment from the URL (always, even when
 * the token is malformed), so it never lingers in history, screenshots or error reports.
 * @param {{ hash: string, pathname: string, search: string }} loc
 * @param {{ state: any, replaceState: (data: any, unused: string, url?: string | URL | null) => void }} hist
 * @returns {string}
 */
export function takeToken(loc, hist) {
  const hash = String(loc.hash || '');
  const raw = hash.startsWith('#') ? hash.slice(1) : hash;
  if (raw) hist.replaceState(hist.state, '', `${loc.pathname}${loc.search}`);
  const token = new URLSearchParams(raw).get('token') || '';
  if (!token || token.length > MAX_TOKEN_LENGTH || !TOKEN_PATTERN.test(token)) {
    throw new RenderError('token_missing', 'render token missing or malformed in the URL fragment');
  }
  return token;
}

/**
 * Render options carried next to the token in the fragment (read BEFORE takeToken clears it).
 * `glass=1`: panels keep the live player's translucent glass colours (render.html), so the MP4
 * composites them over the mascot like the preview (without the blur).
 * @param {{ hash: string }} loc
 * @returns {{ glass: boolean }}
 */
export function readOptions(loc) {
  const hash = String(loc.hash || '');
  const params = new URLSearchParams(hash.startsWith('#') ? hash.slice(1) : hash);
  return { glass: params.get('glass') === '1' };
}

/**
 * GET the timeline with the scoped token (no cookies, no cache, no redirects).
 * @param {string} token
 * @param {typeof fetch} fetchImpl
 * @returns {Promise<any>}
 */
export async function fetchTimeline(token, fetchImpl) {
  /** @type {Response} */
  let res;
  try {
    res = await fetchImpl(TIMELINE_ENDPOINT, {
      method: 'GET',
      headers: { Authorization: `Bearer ${token}`, Accept: 'application/json' },
      credentials: 'omit',
      cache: 'no-store',
      redirect: 'error',
      referrerPolicy: 'no-referrer',
    });
  } catch (e) {
    throw new RenderError('timeline_network', `GET ${TIMELINE_ENDPOINT} failed: ${e instanceof Error ? e.message : e}`);
  }
  if (!res.ok) {
    let code = '';
    try {
      const body = await res.json();
      code = body && typeof body.code === 'string' ? body.code : '';
    } catch {
      /* not JSON */
    }
    throw new RenderError('timeline_http', `GET ${TIMELINE_ENDPOINT} failed: HTTP ${res.status}${code ? ` (${code})` : ''}`);
  }
  const timeline = await res.json();
  if (!timeline || typeof timeline !== 'object' || !Array.isArray(timeline.scenes)) {
    throw new RenderError('timeline_invalid', 'timeline response has no scenes');
  }
  return timeline;
}

/**
 * Import the player module and return its Player class (fails loudly when the API is missing).
 * @param {() => Promise<any>} importer
 * @returns {Promise<any>}
 */
export async function loadPlayerClass(importer) {
  let mod;
  try {
    mod = await importer();
  } catch (e) {
    throw new RenderError('player_missing', `cannot import web/js/player/player.js: ${e instanceof Error ? e.message : e}`);
  }
  const Player = mod && mod.Player;
  if (typeof Player !== 'function') throw new RenderError('player_api', 'web/js/player/player.js must export class Player');
  return Player;
}

/**
 * @param {any} player
 */
export function assertPlayerApi(player) {
  const missing = PLAYER_METHODS.filter((m) => !player || typeof player[m] !== 'function');
  if (missing.length) throw new RenderError('player_api', `Player is missing render-mode methods: ${missing.join(', ')}`);
}

/**
 * Normalise a renderState/renderIntro result to the documented shape (extra keys preserved).
 * @param {any} r
 * @param {string} fallbackKey
 */
export function normalizeResult(r, fallbackKey) {
  const o = r && typeof r === 'object' ? r : {};
  return {
    ...o,
    state_key: o.state_key != null ? String(o.state_key) : fallbackKey,
    media_rect: o.media_rect || null,
    panel_media_rect: o.panel_media_rect || null,
    media_fit: o.media_fit || null,
  };
}

/**
 * True when ``el`` or an ancestor is set to display: none (computed style; false without a window).
 * @param {Element} el
 */
function isHiddenByStyle(el) {
  const view = el.ownerDocument && el.ownerDocument.defaultView;
  if (!view || typeof view.getComputedStyle !== 'function') return false;
  for (let node = /** @type {Element | null} */ (el); node; node = node.parentElement) {
    if (view.getComputedStyle(node).display === 'none') return true;
  }
  return false;
}

/**
 * The MP4 never records a placeholder: a side panel that shows a notice in render mode (its media
 * is missing or failed to load: "The figure is not available yet." ...) is reduced to its title,
 * as GIF panels are. Only a visible notice counts: a standing hidden one (model3d's "context lost"
 * notice) is not a placeholder. Live and preview players keep their notices.
 * @param {ParentNode} root
 * @returns {string[]} the hidden notice texts (deduplicated)
 */
export function titleOnlyPanels(root) {
  /** @type {string[]} */
  const hidden = [];
  for (const notice of Array.from(root.querySelectorAll('.ap-panel .ap-notice'))) {
    if (notice.closest('[hidden]') || isHiddenByStyle(notice)) continue; // not shown: not a placeholder
    const panel = notice.closest('.ap-panel');
    if (!panel) continue;
    panel.classList.add('ap-panel--title-only');
    const text = (notice.textContent || '').replace(/\s+/g, ' ').trim();
    if (text && !hidden.includes(text)) hidden.push(text);
  }
  return hidden;
}

/**
 * @template T
 * @param {Promise<T>} p
 * @param {number} ms
 * @param {string} what
 * @returns {Promise<T>}
 */
function withTimeout(p, ms, what) {
  return new Promise((resolve, reject) => {
    const id = setTimeout(() => reject(new RenderError('timeout', `${what} timed out after ${ms} ms`)), ms);
    p.then(
      (v) => {
        clearTimeout(id);
        resolve(v);
      },
      (e) => {
        clearTimeout(id);
        reject(e);
      },
    );
  });
}

/**
 * @typedef {object} RenderDeps
 * @property {Window & typeof globalThis} [window]
 * @property {typeof fetch} [fetch]
 * @property {() => Promise<any>} [importPlayer]
 * @property {() => Promise<any>} [loadMathJax]
 * @property {() => Promise<unknown>} [texIdle]
 * @property {() => Promise<void>} [frame]     resolves on the next animation frame
 * @property {number} [loadTimeoutMs]
 */

/**
 * Install `window.aadhiRender` (not ready) and return it.
 * @param {any} win
 */
export function installApi(win) {
  const api = {
    version: 2,
    ready: false,
    /** @type {{ code: string, message: string } | null} */
    error: null,
    /** @type {(sceneIndex: number) => any[]} */
    states: () => {
      throw new RenderError('not_ready', 'aadhiRender is not ready');
    },
    /** @type {(arg: { scene: number, t: number }) => Promise<any>} */
    show: async () => {
      throw new RenderError('not_ready', 'aadhiRender is not ready');
    },
    /** @type {(arg: { t: number }) => Promise<any>} */
    showIntro: async () => {
      throw new RenderError('not_ready', 'aadhiRender is not ready');
    },
  };
  win.aadhiRender = api;
  return api;
}

/**
 * Boot the render page. Records failures on `window.aadhiRender.error` and rethrows.
 * @param {RenderDeps} [deps]
 */
export async function boot(deps = {}) {
  const win = /** @type {any} */ (deps.window || window);
  const doc = /** @type {Document} */ (win.document);
  const fetchImpl = deps.fetch || win.fetch.bind(win);
  const importPlayer = deps.importPlayer || (() => import(new URL('../player/player.js', import.meta.url).href));
  const loadMathJax = deps.loadMathJax || defaultLoadMathJax;
  const texIdle = deps.texIdle || defaultTexIdle;
  const frame = deps.frame || (() => new Promise((resolve) => win.requestAnimationFrame(() => resolve(undefined))));
  const loadTimeout = deps.loadTimeoutMs ?? 120_000;
  const api = installApi(win);

  try {
    const options = readOptions(win.location);
    if (options.glass) doc.documentElement.setAttribute('data-render-glass', '');
    const token = takeToken(win.location, win.history);
    let root = /** @type {HTMLElement | null} */ (doc.querySelector('[data-aadhi-render-root]'));
    if (!root) {
      root = doc.createElement('div');
      root.setAttribute('data-aadhi-render-root', '');
      doc.body.appendChild(root);
    }
    const [timeline, Player] = await Promise.all([fetchTimeline(token, fetchImpl), loadPlayerClass(importPlayer)]);
    doc.documentElement.lang = String(timeline.board_language || timeline.language || 'en');
    const fontsDone = preloadFonts(doc.fonts, fontRequests(timeline));
    const mathJaxDone = loadMathJax();

    const player = new Player(root, { mode: 'render', analytics: null, captions: false, autoplay: false });
    assertPlayerApi(player);
    await withTimeout(Promise.resolve(player.load(timeline)), loadTimeout, 'player.load');
    const fonts = await withTimeout(fontsDone, loadTimeout, 'font loading');
    // Every requested face is vendored (web/vendor/fonts.css): a missing one is a deployment
    // error, and rendering with fallback fonts would wrap text differently from the player.
    if (fonts.missing.length) throw new RenderError('fonts_missing', `no @font-face loaded for: ${fonts.missing.join(', ')}`);
    await withTimeout(Promise.resolve(mathJaxDone), loadTimeout, 'MathJax loading');

    /** Wait until everything the screenshot depends on has settled. */
    const settle = async () => {
      await texIdle();
      const imgs = Array.from(root.querySelectorAll('img')).filter((img) => img.getAttribute('src'));
      await withTimeout(
        Promise.all(imgs.map((img) => (typeof img.decode === 'function' ? img.decode().catch(() => undefined) : undefined))),
        30_000,
        'image decoding',
      );
      if (doc.fonts && doc.fonts.ready) await doc.fonts.ready;
      await frame();
      await frame();
    };
    /** @type {Promise<unknown>} */
    let queue = Promise.resolve();
    /**
     * Serialise renders: one visual state at a time.
     * @template T
     * @param {() => Promise<T>} job
     * @returns {Promise<T>}
     */
    const enqueue = (job) => {
      const run = queue.then(job, job);
      queue = run.then(
        () => undefined,
        () => undefined,
      );
      return run;
    };
    const sceneCount = timeline.scenes.length;
    /** @param {unknown} t */
    const checkTime = (t) => {
      if (typeof t !== 'number' || !Number.isFinite(t) || t < 0) throw new RangeError(`invalid time ${String(t)}`);
      return t;
    };
    /** @param {unknown} i */
    const checkScene = (i) => {
      if (typeof i !== 'number' || !Number.isInteger(i) || i < 0 || i >= sceneCount) throw new RangeError(`invalid scene index ${String(i)}`);
      return i;
    };

    api.states = (sceneIndex) => {
      const states = player.states(checkScene(sceneIndex));
      if (!Array.isArray(states)) throw new RenderError('player_api', 'player.states() must return an array');
      return states;
    };
    api.show = async (arg) => {
      const scene = checkScene(arg && arg.scene);
      const t = checkTime(arg && arg.t);
      return enqueue(async () => {
        const r = await player.renderState({ sceneIndex: scene, t });
        const notices = titleOnlyPanels(root);
        await settle();
        const result = normalizeResult(r, `s${scene}@${t}`);
        if (notices.length) result.notices = notices;
        return result;
      });
    };
    api.showIntro = async (arg) => {
      const t = checkTime(arg && arg.t);
      return enqueue(async () => {
        const r = await player.renderIntro({ t });
        await settle();
        return normalizeResult(r, `intro@${t}`);
      });
    };
    await settle();
    api.ready = true;
    return api;
  } catch (e) {
    const err = e instanceof Error ? e : new Error(String(e));
    api.error = { code: /** @type {any} */ (err).code || 'boot_failed', message: err.message };
    console.error('render page failed to boot:', err);
    throw err;
  }
}

if (typeof document !== 'undefined' && document.documentElement && document.documentElement.dataset.aadhiRender === 'page') {
  boot().catch(() => undefined); // recorded on window.aadhiRender.error for the render worker
}
