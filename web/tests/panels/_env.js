// Test helper (not a test file): installs a jsdom window as the global DOM for panel modules.
import { JSDOM } from 'jsdom';

const GLOBAL_KEYS = [
  'window', 'document', 'Node', 'Element', 'HTMLElement', 'SVGElement', 'HTMLImageElement', 'HTMLCanvasElement',
  'HTMLVideoElement', 'HTMLMediaElement', 'DocumentFragment', 'Event', 'CustomEvent', 'MouseEvent', 'KeyboardEvent',
  'EventTarget', 'location', 'history', 'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame',
];

/**
 * Install a fresh jsdom as globals. Returns the window.
 * @param {{ url?: string, html?: string }} [opts]
 */
export function installDom(opts = {}) {
  const dom = new JSDOM(opts.html || '<!doctype html><html><head></head><body></body></html>', {
    url: opts.url || 'https://app.test/watch/abc',
    pretendToBeVisual: true,
  });
  const w = dom.window;
  for (const key of GLOBAL_KEYS) {
    Object.defineProperty(globalThis, key, { value: w[key], configurable: true, writable: true });
  }
  Object.defineProperty(globalThis, 'navigator', { value: w.navigator, configurable: true, writable: true });
  globalThis.devicePixelRatio = 1;
  return w;
}

/**
 * Recording fake for CanvasRenderingContext2D (jsdom has no canvas).
 */
export function fakeContext2d() {
  const calls = [];
  const ctx = {
    calls,
    canvas: null,
    font: '',
    fillStyle: '',
    strokeStyle: '',
    lineWidth: 1,
    lineJoin: 'miter',
    lineCap: 'butt',
    textAlign: 'start',
    textBaseline: 'alphabetic',
    measureText: (s) => ({ width: String(s).length * 7 }),
  };
  for (const name of [
    'clearRect', 'beginPath', 'moveTo', 'lineTo', 'stroke', 'fill', 'fillText', 'save', 'restore', 'translate',
    'rotate', 'rect', 'clip', 'arc', 'setTransform',
  ]) {
    ctx[name] = (...args) => {
      calls.push([name, ...args]);
    };
  }
  return ctx;
}

/** Make every canvas return one shared recording 2D context. */
export function stubCanvas(w) {
  const ctx = fakeContext2d();
  w.HTMLCanvasElement.prototype.getContext = function getContext(type) {
    return type === '2d' ? ctx : null;
  };
  return ctx;
}

/**
 * Track listeners added/removed on every EventTarget (to prove destroy() releases them).
 * @returns {{ active: () => number, restore: () => void }}
 */
export function trackListeners(w) {
  const proto = w.EventTarget.prototype;
  const add = proto.addEventListener;
  const remove = proto.removeEventListener;
  const live = new Map(); // target -> Set of "type|listener"
  const keyOf = (type, fn, opts) => {
    const capture = typeof opts === 'boolean' ? opts : !!(opts && opts.capture);
    return { type, fn, capture };
  };
  proto.addEventListener = function patchedAdd(type, fn, opts) {
    if (fn) {
      const set = live.get(this) || [];
      const k = keyOf(type, fn, opts);
      if (!set.some((x) => x.type === k.type && x.fn === k.fn && x.capture === k.capture)) set.push(k);
      live.set(this, set);
    }
    return add.call(this, type, fn, opts);
  };
  proto.removeEventListener = function patchedRemove(type, fn, opts) {
    const set = live.get(this);
    if (set) {
      const k = keyOf(type, fn, opts);
      const i = set.findIndex((x) => x.type === k.type && x.fn === k.fn && x.capture === k.capture);
      if (i >= 0) set.splice(i, 1);
    }
    return remove.call(this, type, fn, opts);
  };
  return {
    active: () => [...live.values()].reduce((n, s) => n + s.length, 0),
    restore: () => {
      proto.addEventListener = add;
      proto.removeEventListener = remove;
    },
  };
}

/** A minimal timeline with one scene owning the given side panel. */
export function timelineWith(sidePanel, extra = {}) {
  return {
    width: 1920,
    height: 1080,
    language: 'en-IN',
    board_language: 'en-IN',
    concept_map: [],
    scenes: [{ scene_id: 's1', index: 0, type: 'board', start: 0, duration: 20, side_panel: sidePanel, beats: [] }],
    ...extra,
  };
}

/** Container element attached to the document. */
export function container(doc) {
  const el = doc.createElement('div');
  el.className = 'side';
  doc.body.appendChild(el);
  return el;
}

/** Flush pending promise jobs a few times. */
export async function flush(times = 5) {
  for (let i = 0; i < times; i++) await new Promise((r) => setTimeout(r, 0));
}
