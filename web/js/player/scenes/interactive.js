// @ts-check
/**
 * Interactive scene: a p5.js sketch that runs ONLY inside the sandboxed iframe /sandbox/p5
 * (sandbox="allow-scripts", opaque origin, its own strict CSP). Protocol (postMessage):
 *   sandbox -> player  {type:'ready'}                 page loaded, waiting for code
 *   player  -> sandbox {type:'run', code}             sketch source (public lecture content)
 *   sandbox -> player  {type:'heartbeat'}             at least every ~1 s while alive
 *   sandbox -> player  {type:'error', message}        sketch failed
 *   player  -> sandbox {type:'pause'} / {type:'resume'}
 * Messages are accepted only when ev.source === iframe.contentWindow and ev.origin === 'null'.
 * A watchdog tears the iframe down if it never becomes ready or stops sending heartbeats (e.g. an
 * infinite loop), and shows the poster/title fallback instead. Render mode never creates the iframe:
 * it shows the poster image (or a title card) directly in the screenshot.
 */

import { h } from '../../shared/dom.js';
import { mediaAspect } from '../layout.js';
import { DEFAULT_MEDIA_ASPECT, placeMediaBox, titleBand } from './media.js';

/** @typedef {import('./types.js').SceneContext} SceneContext */
/** @typedef {import('./types.js').SceneView} SceneView */

export const SANDBOX_URL = '/sandbox/p5';
export const LOAD_TIMEOUT_MS = 10000;
export const HEARTBEAT_TIMEOUT_MS = 4000;
export const INTERACTIVE_LABELS = Object.freeze({
  stopped: 'The interactive sketch stopped responding.',
  failed: 'The interactive sketch could not run.',
  hint: 'Interactive — try it!',
});

/** @implements {SceneView} */
export class InteractiveSceneView {
  /** @param {SceneContext} ctx */
  constructor(ctx) {
    this.ctx = ctx;
    this.now = ctx.now || (() => Date.now());
    /** @type {'idle' | 'loading' | 'running' | 'failed'} */
    this.status = 'idle';
    /** @type {HTMLIFrameElement | null} */
    this.iframe = null;
    /** @type {ReturnType<typeof setInterval> | null} */
    this.watchdog = null;
    this.createdAt = 0;
    this.lastSeen = 0;
    this.playing = false;
    /** @type {HTMLImageElement[]} */
    this.images = [];
    /** Holder of the iframe or the fallback (poster / title card). @type {HTMLDivElement} */
    this.content = h('div', { class: 'ap-interactive-content' });
    /** @type {HTMLDivElement} */
    this.message = h('div', { class: 'ap-interactive-msg', role: 'status' });
    /** @type {HTMLDivElement} */
    this.box = h('div', { class: 'ap-media-box ap-interactive-box fit-contain' }, this.content, this.message);
    const title = titleBand(ctx.scene);
    placeMediaBox(ctx, this.box, title, mediaAspect(ctx.scene.poster) ?? DEFAULT_MEDIA_ASPECT);
    /** @type {HTMLDivElement} */
    this.el = h('div', { class: 'ap-scene-media ap-scene-interactive' }, title, h('div', { class: 'ap-zone ap-zone--media' }, this.box));
    this.onMessage = (/** @type {MessageEvent} */ ev) => this.handleMessage(ev);
    if (ctx.mode === 'render' || !ctx.scene.p5_code) {
      this.showFallback('');
    } else {
      this.start();
    }
  }

  /** Create the sandboxed iframe and the watchdog. */
  start() {
    this.status = 'loading';
    this.createdAt = this.now();
    this.lastSeen = this.createdAt;
    /** @type {HTMLIFrameElement} */
    const frame = h('iframe', {
      __trusted: true,
      class: 'ap-sandbox',
      src: this.ctx.sandboxUrl || SANDBOX_URL,
      sandbox: 'allow-scripts',
      title: this.ctx.scene.title || 'Interactive sketch',
      referrerpolicy: 'no-referrer',
      loading: 'eager',
    });
    this.iframe = frame;
    this.content.appendChild(frame);
    this.box.classList.add('is-live');
    window.addEventListener('message', this.onMessage);
    this.watchdog = setInterval(() => this.checkWatchdog(), 1000);
  }

  /** @param {MessageEvent} ev */
  handleMessage(ev) {
    if (!this.iframe || ev.source !== this.iframe.contentWindow || ev.origin !== 'null') return;
    const data = ev.data;
    if (!data || typeof data !== 'object' || typeof data.type !== 'string') return;
    this.lastSeen = this.now();
    if (data.type === 'ready' && this.status === 'loading') {
      this.status = 'running';
      this.post({ type: 'run', code: String(this.ctx.scene.p5_code || '') });
      if (!this.playing) this.post({ type: 'pause' });
    } else if (data.type === 'error') {
      this.fail(INTERACTIVE_LABELS.failed);
    }
  }

  /** @param {Record<string, unknown>} msg */
  post(msg) {
    const win = this.iframe && this.iframe.contentWindow;
    // The sandbox has an opaque origin, so '*' is the only usable target; payloads are public.
    if (win) win.postMessage(msg, '*');
  }

  checkWatchdog() {
    if (this.status === 'loading' && this.now() - this.createdAt > LOAD_TIMEOUT_MS) this.fail(INTERACTIVE_LABELS.failed);
    else if (this.status === 'running' && this.now() - this.lastSeen > HEARTBEAT_TIMEOUT_MS) this.fail(INTERACTIVE_LABELS.stopped);
  }

  /** Tear down the iframe and show the fallback. @param {string} message */
  fail(message) {
    this.status = 'failed';
    this.teardown();
    this.showFallback(message);
  }

  teardown() {
    window.removeEventListener('message', this.onMessage);
    if (this.watchdog !== null) clearInterval(this.watchdog);
    this.watchdog = null;
    if (this.iframe) {
      try {
        this.iframe.src = 'about:blank';
      } catch {
        /* ignore */
      }
      this.iframe.remove();
    }
    this.iframe = null;
    this.box.classList.remove('is-live');
  }

  /** Poster image (MediaRef) or a title card; shown in render mode and after failures. @param {string} message */
  showFallback(message) {
    const poster = this.ctx.scene.poster;
    this.content.replaceChildren();
    if (poster && poster.url) {
      /** @type {HTMLImageElement} */
      const img = h('img', { class: 'ap-poster', src: poster.url, alt: this.ctx.scene.title || '', decoding: 'async', loading: 'eager' });
      this.images.push(img);
      this.content.appendChild(img);
      this.box.classList.toggle('fit-cover', poster.fit === 'cover');
    } else {
      this.content.appendChild(h('div', { class: 'ap-media-missing', text: this.ctx.scene.title || INTERACTIVE_LABELS.hint }));
    }
    this.message.textContent = message;
    this.message.classList.toggle('has-text', !!message);
  }

  /**
   * Follow the clock every frame (like the media view), so the sketch runs exactly while the
   * lecture plays, whichever way the scene was entered.
   * @param {number} _t
   * @param {import('../../shared/types.js').SceneState} _state
   * @param {import('./types.js').FrameInfo} frame
   */
  update(_t, _state, frame) {
    if (this.ctx.mode !== 'render') this.setPlaying(!!frame.playing);
  }

  /**
   * Run or freeze the sketch (posted to the sandbox once it is running; the state is remembered
   * until then and applied when it reports ready).
   * @param {boolean} playing
   */
  setPlaying(playing) {
    if (playing === this.playing) return;
    this.playing = playing;
    if (this.status === 'running') this.post({ type: playing ? 'resume' : 'pause' });
  }

  mediaElement() {
    return null; // the poster is drawn into the screenshot itself
  }

  mediaFit() {
    return null;
  }

  async ready() {
    await Promise.allSettled(this.images.map((img) => (typeof img.decode === 'function' ? img.decode() : Promise.resolve())));
  }

  destroy() {
    this.teardown();
    this.el.remove();
  }
}
