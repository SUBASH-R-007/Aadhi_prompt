// @ts-check
/**
 * Install a jsdom window as the global DOM for node --test, plus small fakes for APIs jsdom does
 * not implement (media playback, ResizeObserver, fonts). Import this module BEFORE importing any
 * player module that touches the DOM.
 */
import { JSDOM } from 'jsdom';

/** @type {JSDOM | null} */
let current = null;

const GLOBALS = [
  'window', 'document', 'location', 'Node', 'Element', 'HTMLElement', 'SVGElement', 'DocumentFragment', 'Text',
  'Event', 'CustomEvent', 'KeyboardEvent', 'MouseEvent', 'PointerEvent', 'MessageEvent', 'getComputedStyle',
  'requestAnimationFrame', 'cancelAnimationFrame', 'HTMLMediaElement', 'HTMLVideoElement', 'HTMLAudioElement',
  'HTMLImageElement', 'HTMLIFrameElement', 'HTMLButtonElement', 'HTMLInputElement', 'sessionStorage', 'localStorage',
  'Image', 'Audio', 'DOMRect',
];

/** Media elements that have been played (for advanceMedia). @type {Set<any>} */
const playing = new Set();

/**
 * Advance currentTime of every playing (not paused) media element, like real playback would.
 * @param {number} seconds
 */
export function advanceMedia(seconds) {
  for (const el of playing) {
    if (!el.paused) el.currentTime = el.currentTime + seconds * (el.playbackRate || 1);
  }
}

/**
 * Fake media element behaviour: play()/pause() toggle `paused`, currentTime is writable,
 * readyState is HAVE_ENOUGH_DATA once a src is set.
 * @param {any} win
 */
function patchMedia(win) {
  const proto = win.HTMLMediaElement.prototype;
  const state = new WeakMap();
  /** @param {any} el */
  const s = (el) => {
    let v = state.get(el);
    if (!v) {
      v = { paused: true, currentTime: 0, playbackRate: 1, volume: 1, muted: false, ended: false, seeking: false };
      state.set(el, v);
    }
    return v;
  };
  Object.defineProperty(proto, 'paused', { configurable: true, get() { return s(this).paused; } });
  Object.defineProperty(proto, 'ended', { configurable: true, get() { return s(this).ended; } });
  Object.defineProperty(proto, 'seeking', { configurable: true, get() { return s(this).seeking; } });
  Object.defineProperty(proto, 'readyState', { configurable: true, get() { return this.getAttribute('src') ? 4 : 0; } });
  Object.defineProperty(proto, 'duration', { configurable: true, get() { return Number(this.dataset.fakeDuration || NaN); } });
  for (const key of ['currentTime', 'playbackRate', 'volume']) {
    Object.defineProperty(proto, key, {
      configurable: true,
      get() { return s(this)[key]; },
      set(v) { s(this)[key] = Number(v); },
    });
  }
  Object.defineProperty(proto, 'muted', {
    configurable: true,
    get() { return s(this).muted; },
    set(v) { s(this).muted = !!v; },
  });
  proto.play = function play() {
    s(this).paused = false;
    playing.add(this);
    win.__plays = (win.__plays || 0) + 1;
    return Promise.resolve();
  };
  proto.pause = function pause() {
    s(this).paused = true;
  };
  proto.load = function load() {};
  win.HTMLImageElement.prototype.decode = function decode() {
    return Promise.resolve();
  };
}

/**
 * @param {{ url?: string }} [opts]
 * @returns {JSDOM}
 */
export function installDom(opts = {}) {
  const dom = new JSDOM('<!doctype html><html><head></head><body></body></html>', {
    url: opts.url || 'http://localhost/watch/test-token-123456',
    pretendToBeVisual: true,
  });
  const win = /** @type {any} */ (dom.window);
  patchMedia(win);
  if (!win.ResizeObserver) {
    win.ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    };
  }
  const g = /** @type {any} */ (globalThis);
  for (const key of GLOBALS) {
    if (win[key] === undefined) continue;
    Object.defineProperty(g, key, { configurable: true, writable: true, value: key === 'window' ? win : win[key] });
  }
  g.ResizeObserver = win.ResizeObserver;
  current = dom;
  return dom;
}

/** Close the current jsdom window. */
export function uninstallDom() {
  if (current) current.window.close();
  current = null;
  playing.clear();
}

/** @param {number} [ms] */
export const tick = (ms = 0) => new Promise((r) => setTimeout(r, ms));

/**
 * Fake TeX renderer that records calls and returns a <svg data-tex> element (never parses HTML).
 * @returns {{ render: (latex: string, display: boolean) => Promise<Element>, calls: {latex: string, display: boolean}[] }}
 */
export function fakeTex() {
  /** @type {{latex: string, display: boolean}[]} */
  const calls = [];
  return {
    calls,
    render(latex, display) {
      calls.push({ latex, display });
      const el = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      el.setAttribute('data-tex', latex);
      el.setAttribute('data-display', display ? '1' : '0');
      return Promise.resolve(el);
    },
  };
}
