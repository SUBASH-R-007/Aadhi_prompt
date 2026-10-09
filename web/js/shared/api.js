// @ts-check
/**
 * API client. Same-origin, cookie session, CSRF header on every mutating request.
 * See docs/API.md for endpoints and shapes. Error envelope: {detail, code, ...extra}.
 */

export const CSRF_HEADER = 'X-Aadhi-CSRF';

export class ApiError extends Error {
  /**
   * @param {number} status
   * @param {any} body         parsed JSON error body (or null)
   * @param {number | null} retryAfter  seconds from Retry-After, if any
   */
  constructor(status, body, retryAfter) {
    const detail = body && typeof body === 'object' ? body.detail : body;
    super(formatDetail(detail) || `HTTP ${status}`);
    this.status = status;
    this.body = body;
    this.detail = detail;
    /** @type {string | undefined} */
    this.code = body && typeof body === 'object' ? body.code : undefined;
    this.retryAfter = retryAfter;
  }
}

/** @param {any} detail */
function formatDetail(detail) {
  if (detail == null) return '';
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => {
        if (typeof d === 'string') return d;
        const loc = Array.isArray(d.loc) ? d.loc.filter((/** @type {any} */ x) => x !== 'body').join('.') : '';
        return loc ? `${loc}: ${d.msg}` : d.msg || JSON.stringify(d);
      })
      .join('\n');
  }
  if (typeof detail === 'object' && detail.message) return String(detail.message);
  return JSON.stringify(detail);
}

/** @type {Set<(err: ApiError) => void>} */
const errorHandlers = new Set();

/**
 * Global hook for 401 (session expired) and 403 password_change_required.
 * @param {(err: ApiError) => void} fn
 */
export function onAuthError(fn) {
  errorHandlers.add(fn);
  return () => errorHandlers.delete(fn);
}

/**
 * @typedef {object} RequestOptions
 * @property {string} [method]
 * @property {any} [json]            JSON body
 * @property {FormData} [form]       multipart body
 * @property {Record<string, string>} [headers]
 * @property {AbortSignal} [signal]
 * @property {boolean} [keepalive]
 * @property {'json' | 'text' | 'blob' | 'response'} [as]
 */

/**
 * @param {string} path
 * @param {RequestOptions} [opts]
 * @returns {Promise<any>}
 */
export async function api(path, opts = {}) {
  const method = (opts.method || (opts.json !== undefined || opts.form ? 'POST' : 'GET')).toUpperCase();
  /** @type {Record<string, string>} */
  const headers = { Accept: 'application/json', ...(opts.headers || {}) };
  /** @type {BodyInit | undefined} */
  let body;
  if (opts.json !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(opts.json);
  } else if (opts.form) {
    body = opts.form;
  }
  if (method !== 'GET' && method !== 'HEAD') headers[CSRF_HEADER] = '1';

  const res = await fetch(path, {
    method,
    headers,
    body,
    credentials: 'same-origin',
    signal: opts.signal,
    keepalive: opts.keepalive,
  });
  if (opts.as === 'response') return res;
  if (!res.ok) {
    let parsed = null;
    try {
      parsed = await res.json();
    } catch {
      parsed = { detail: res.statusText };
    }
    const ra = res.headers.get('Retry-After');
    const err = new ApiError(res.status, parsed, ra ? Number(ra) : null);
    if (res.status === 401 || (res.status === 403 && err.code === 'password_change_required')) {
      errorHandlers.forEach((fn) => fn(err));
    }
    throw err;
  }
  if (res.status === 204) return null;
  if (opts.as === 'text') return res.text();
  if (opts.as === 'blob') return res.blob();
  const ct = res.headers.get('content-type') || '';
  return ct.includes('application/json') ? res.json() : res.text();
}

export const get = (/** @type {string} */ path, /** @type {RequestOptions} */ opts = {}) =>
  api(path, { ...opts, method: 'GET' });
export const post = (/** @type {string} */ path, /** @type {any} */ json, /** @type {RequestOptions} */ opts = {}) =>
  api(path, { ...opts, method: 'POST', json: json === undefined ? {} : json });
export const put = (/** @type {string} */ path, /** @type {any} */ json, /** @type {RequestOptions} */ opts = {}) =>
  api(path, { ...opts, method: 'PUT', json });
export const patch = (/** @type {string} */ path, /** @type {any} */ json, /** @type {RequestOptions} */ opts = {}) =>
  api(path, { ...opts, method: 'PATCH', json });
export const del = (/** @type {string} */ path, /** @type {RequestOptions} */ opts = {}) =>
  api(path, { ...opts, method: 'DELETE' });

/**
 * Subscribe to a server-sent-event stream (e.g. /api/jobs/{id}/stream).
 * Closes automatically after an `end` event (so EventSource does not reconnect forever).
 * @param {string} path
 * @param {Record<string, (data: any, ev: MessageEvent) => void>} handlers keyed by event name
 * @param {{ onError?: (ev: Event) => void }} [opts]
 * @returns {{ close: () => void, source: EventSource }}
 */
export function sse(path, handlers, opts = {}) {
  const source = new EventSource(path, { withCredentials: true });
  const names = new Set([...Object.keys(handlers), 'end']);
  for (const name of names) {
    source.addEventListener(name, (ev) => {
      const msg = /** @type {MessageEvent} */ (ev);
      let data = msg.data;
      try {
        data = JSON.parse(msg.data);
      } catch {
        /* plain text */
      }
      if (handlers[name]) handlers[name](data, msg);
      if (name === 'end') source.close();
    });
  }
  source.onerror = (ev) => {
    // CLOSED = server rejected (401/404) or gave up; CONNECTING = transient, EventSource retries.
    if (source.readyState === EventSource.CLOSED && opts.onError) opts.onError(ev);
  };
  return { source, close: () => source.close() };
}
