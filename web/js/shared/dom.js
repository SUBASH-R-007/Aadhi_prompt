// @ts-check
/**
 * Safe DOM construction helpers. Dynamic strings are ALWAYS inserted as text nodes; there is
 * intentionally no way to pass HTML through these helpers. Never use model-provided ids as DOM
 * `id`/`name` attributes (use `data-*`), to avoid DOM clobbering.
 */

/**
 * @typedef {Record<string, any>} Attrs
 * Supported keys:
 *  - class / className: string | string[] | Record<string, boolean>
 *  - style: Record<string, string | number>   (object only; camelCase or --custom-props)
 *  - dataset: Record<string, string>
 *  - on<Event>: (ev) => void                   function values only, e.g. onClick, onInput
 *  - text: string                              sets textContent
 *  - any other: set as attribute (boolean true -> "", false/null/undefined -> omitted)
 */

/** @typedef {Node | string | number | boolean | null | undefined} ChildAtom */
/** @typedef {ChildAtom | ReadonlyArray<any>} Child  nested arrays are flattened */

const URL_ATTRS = new Set([
  'href', 'src', 'action', 'formaction', 'poster', 'xlink:href', 'srcset', 'imagesrcset', 'data', 'ping',
  'background', 'cite', 'longdesc', 'manifest',
]);
const FORBIDDEN_ATTRS = new Set(['srcdoc', 'innerhtml', 'outerhtml', 'id', 'name', 'formaction', 'ping']);
const FORBIDDEN_SVG_TAGS = new Set(['script', 'foreignobject', 'animate', 'set', 'animatemotion', 'animatetransform']);
const ALLOWED_PROTOCOLS = new Set(['http:', 'https:', 'blob:']);
// eslint-disable-next-line no-control-regex
const CONTROL_CHARS = /[\u0000-\u001F\u007F]/;

/**
 * Allow only http(s)/blob URLs (and data: raster images). Everything else -> 'about:blank'.
 * Relative URLs are resolved against the document base before the protocol check.
 * @param {string} value
 * @param {{ sameOriginOnly?: boolean }} [opts]
 */
export function safeUrl(value, opts = {}) {
  const v = String(value);
  if (CONTROL_CHARS.test(v)) return 'about:blank';
  const trimmed = v.trim();
  if (/^data:image\/(png|jpe?g|gif|webp);base64,[A-Za-z0-9+/=]+$/i.test(trimmed)) return trimmed;
  let url;
  try {
    url = new URL(trimmed, document.baseURI);
  } catch {
    return 'about:blank';
  }
  if (!ALLOWED_PROTOCOLS.has(url.protocol)) return 'about:blank';
  if (opts.sameOriginOnly && url.origin !== location.origin && url.protocol !== 'blob:') return 'about:blank';
  return url.href;
}

/**
 * Create an element.
 * @template {keyof HTMLElementTagNameMap} K
 * @param {K} tag
 * @param {Attrs | null} [attrs]
 * @param {...Child} children
 * @returns {HTMLElementTagNameMap[K]}
 */
export function h(tag, attrs, ...children) {
  if (/^(script|iframe|object|embed|frame|base|meta|link|style)$/i.test(tag) && !(attrs && attrs.__trusted)) {
    throw new Error(`dom.js: <${tag}> must be created explicitly by trusted code`);
  }
  const el = document.createElement(tag);
  if (attrs) applyAttrs(el, attrs);
  append(el, children);
  return el;
}

/**
 * SVG element helper (diagrams such as the skill tree).
 * @param {string} tag
 * @param {Attrs | null} [attrs]
 * @param {...Child} children
 * @returns {SVGElement}
 */
export function svg(tag, attrs, ...children) {
  if (FORBIDDEN_SVG_TAGS.has(tag.toLowerCase())) throw new Error(`dom.js: svg <${tag}> is not allowed`);
  const el = /** @type {SVGElement} */ (document.createElementNS('http://www.w3.org/2000/svg', tag));
  if (attrs) applyAttrs(el, attrs);
  append(el, children);
  return el;
}

/**
 * @param {Element} el
 * @param {Attrs} attrs
 */
export function applyAttrs(el, attrs) {
  for (const [rawKey, value] of Object.entries(attrs)) {
    if (rawKey === '__trusted') continue;
    if (value === undefined || value === null || value === false) continue;
    const key = rawKey === 'className' ? 'class' : rawKey;
    const lower = key.toLowerCase();
    // HTML lower-cases attribute names itself; SVG attributes are case-sensitive (viewBox,
    // preserveAspectRatio, gradientUnits ...), so the original key is kept when setting them.
    const isSvg = el.namespaceURI === 'http://www.w3.org/2000/svg';
    const attrName = isSvg ? key : lower;
    if (lower === 'class') {
      el.setAttribute('class', classNames(value));
    } else if (lower === 'style') {
      if (typeof value !== 'object') throw new Error('dom.js: style must be an object');
      const style = /** @type {HTMLElement} */ (el).style;
      for (const [prop, v] of Object.entries(value)) {
        if (v === undefined || v === null) continue;
        if (prop.startsWith('--')) style.setProperty(prop, String(v));
        else /** @type {any} */ (style)[prop] = typeof v === 'number' && !UNITLESS.has(prop) ? `${v}px` : String(v);
      }
    } else if (lower === 'dataset') {
      for (const [k, v] of Object.entries(value)) /** @type {HTMLElement} */ (el).dataset[k] = String(v);
    } else if (lower.startsWith('on')) {
      if (typeof value !== 'function') throw new Error(`dom.js: ${key} must be a function`);
      el.addEventListener(lower.slice(2), value);
    } else if (lower === 'text') {
      el.textContent = String(value);
    } else if (FORBIDDEN_ATTRS.has(lower) && !(lower === 'id' && attrs.__trusted)) {
      throw new Error(`dom.js: attribute ${key} is not allowed`);
    } else if (URL_ATTRS.has(lower)) {
      el.setAttribute(attrName, safeUrl(String(value)));
    } else {
      el.setAttribute(attrName, value === true ? '' : String(value));
    }
  }
}

const UNITLESS = new Set(['opacity', 'zIndex', 'flexGrow', 'flexShrink', 'fontWeight', 'lineHeight', 'order', 'zoom']);

/** @param {any} value */
export function classNames(value) {
  if (!value) return '';
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) return value.map(classNames).filter(Boolean).join(' ');
  return Object.entries(value)
    .filter(([, on]) => on)
    .map(([k]) => k)
    .join(' ');
}

/**
 * @param {Node} parent
 * @param {Child[]} children
 */
export function append(parent, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false || child === true) continue;
    parent.appendChild(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return parent;
}

/** Remove all children. @param {Node} el */
export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

/** Replace children. @param {Node} el @param {...Child} children */
export function replace(el, ...children) {
  clear(el);
  append(el, children);
  return el;
}

/**
 * @param {string} sel
 * @param {ParentNode} [root]
 * @returns {HTMLElement | null}
 */
export function $(sel, root = document) {
  return /** @type {HTMLElement | null} */ (root.querySelector(sel));
}

/**
 * @param {string} sel
 * @param {ParentNode} [root]
 * @returns {HTMLElement[]}
 */
export function $$(sel, root = document) {
  return /** @type {HTMLElement[]} */ (Array.from(root.querySelectorAll(sel)));
}

/**
 * Collects cleanup callbacks (listeners, timers, observers, WebGL contexts) for destroy().
 */
export class Disposer {
  constructor() {
    /** @type {Array<() => void>} */
    this.fns = [];
  }
  /** @param {() => void} fn */
  add(fn) {
    this.fns.push(fn);
    return fn;
  }
  /**
   * @param {EventTarget} target
   * @param {string} type
   * @param {EventListenerOrEventListenerObject} listener
   * @param {AddEventListenerOptions | boolean} [opts]
   */
  listen(target, type, listener, opts) {
    target.addEventListener(type, listener, opts);
    this.add(() => target.removeEventListener(type, listener, opts));
  }
  /** @param {() => void} fn @param {number} ms */
  timeout(fn, ms) {
    const id = setTimeout(fn, ms);
    this.add(() => clearTimeout(id));
    return id;
  }
  /** @param {() => void} fn @param {number} ms */
  interval(fn, ms) {
    const id = setInterval(fn, ms);
    this.add(() => clearInterval(id));
    return id;
  }
  /** @param {FrameRequestCallback} fn */
  raf(fn) {
    const id = requestAnimationFrame(fn);
    this.add(() => cancelAnimationFrame(id));
    return id;
  }
  dispose() {
    const fns = this.fns.splice(0).reverse();
    for (const fn of fns) {
      try {
        fn();
      } catch (e) {
        console.warn('dispose error', e);
      }
    }
  }
}
