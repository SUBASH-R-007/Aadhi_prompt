// Test helper: installs a jsdom window as the global DOM for Studio modules.
// Import this module FIRST in every test file that touches the DOM.
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><head></head><body></body></html>', {
  url: 'http://localhost/',
  pretendToBeVisual: true,
});
const { window } = dom;

const GLOBALS = [
  'window', 'document', 'Node', 'Element', 'HTMLElement', 'HTMLInputElement', 'HTMLSelectElement',
  'HTMLTextAreaElement', 'HTMLButtonElement', 'HTMLCanvasElement', 'SVGElement', 'Event', 'KeyboardEvent',
  'MouseEvent', 'CustomEvent', 'FocusEvent', 'DocumentFragment', 'DOMParser', 'localStorage', 'sessionStorage',
  'location', 'history', 'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame', 'FormData', 'Blob',
  'File', 'FileList', 'DataTransfer', 'MutationObserver',
];

for (const name of GLOBALS) {
  if (name === 'window') {
    Object.defineProperty(globalThis, 'window', { value: window, configurable: true, writable: true });
    continue;
  }
  const value = window[name];
  if (value === undefined) continue;
  Object.defineProperty(globalThis, name, {
    value: typeof value === 'function' && /^[a-z]/.test(name) ? value.bind(window) : value,
    configurable: true,
    writable: true,
  });
}
Object.defineProperty(globalThis, 'navigator', { value: window.navigator, configurable: true, writable: true });

/** Remove everything from <body> between tests. */
export function resetDom() {
  document.body.textContent = '';
  document.head.textContent = '';
  window.location.hash = '';
}

/** Wait for queued microtasks/timers. */
export function tick(ms = 0) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Install a fake `fetch` answering by "METHOD /path" keys. Handlers return
 * { status, body, headers } or a plain body (status 200). Records every call.
 * @param {Record<string, any>} routes
 */
export function mockFetch(routes) {
  const calls = [];
  const fake = async (url, init = {}) => {
    const method = (init.method || 'GET').toUpperCase();
    const path = String(url);
    const key = `${method} ${path}`;
    const bare = `${method} ${path.split('?')[0]}`;
    calls.push({ method, path, init });
    let handler = routes[key] ?? routes[bare];
    if (handler === undefined) {
      for (const [k, v] of Object.entries(routes)) {
        if (k.startsWith('re:') && new RegExp(k.slice(3)).test(key)) {
          handler = v;
          break;
        }
      }
    }
    if (handler === undefined) handler = { status: 404, body: { detail: 'not found', code: 'not_found' } };
    const res = typeof handler === 'function' ? await handler({ method, path, init }) : handler;
    const spec = res && typeof res === 'object' && 'status' in res && ('body' in res || res.status === 204) ? res : { status: 200, body: res };
    const headers = new Map(Object.entries({ 'content-type': 'application/json', ...(spec.headers || {}) }).map(([k, v]) => [k.toLowerCase(), v]));
    return {
      ok: spec.status >= 200 && spec.status < 300,
      status: spec.status,
      statusText: String(spec.status),
      headers: { get: (n) => headers.get(String(n).toLowerCase()) ?? null },
      json: async () => (typeof spec.body === 'string' ? JSON.parse(spec.body) : spec.body),
      text: async () => (typeof spec.body === 'string' ? spec.body : JSON.stringify(spec.body)),
      blob: async () => new window.Blob([JSON.stringify(spec.body)]),
    };
  };
  globalThis.fetch = fake;
  return calls;
}

/** Minimal EventSource stub (tests drive it through `instances`). */
export class FakeEventSource {
  static instances = [];
  static CLOSED = 2;
  static CONNECTING = 0;
  static OPEN = 1;
  constructor(url) {
    this.url = url;
    this.readyState = 1;
    this.listeners = new Map();
    this.onerror = null;
    FakeEventSource.instances.push(this);
  }
  addEventListener(name, fn) {
    if (!this.listeners.has(name)) this.listeners.set(name, []);
    this.listeners.get(name).push(fn);
  }
  emit(name, data) {
    for (const fn of this.listeners.get(name) || []) fn({ data: JSON.stringify(data) });
  }
  fail() {
    this.readyState = 2;
    if (this.onerror) this.onerror(new window.Event('error'));
  }
  close() {
    this.readyState = 2;
    this.closed = true;
  }
}
globalThis.EventSource = FakeEventSource;
window.EventSource = FakeEventSource;

export { dom, window };
