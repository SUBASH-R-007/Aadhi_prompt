// @ts-check
/**
 * Learner analytics: batched POST /api/analytics/events through shared/api.js (fetch with
 * keepalive + the CSRF header; never sendBeacon). Events carry the absolute time `t` and the
 * scene-relative `scene_t`. The viewer id is random, per browser session (sessionStorage), and is
 * regenerated if storage is unavailable. Failures never affect playback.
 */

import { api } from '../shared/api.js';
import { ANALYTICS_EVENTS } from '../shared/types.js';

export const ENDPOINT = '/api/analytics/events';
export const VIEWER_KEY = 'aadhi_viewer_id';
export const MAX_EVENTS_PER_REQUEST = 100;
export const MAX_BODY_BYTES = 30 * 1024; // server cap is 32 KB
const MAX_QUEUE = 500;
const VIEWER_RE = /^[A-Za-z0-9_-]{16,64}$/;
const ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';

/**
 * Random URL-safe id (crypto.getRandomValues; 64-symbol alphabet so there is no modulo bias).
 * @param {number} [length]
 */
export function randomId(length = 24) {
  const bytes = new Uint8Array(length);
  globalThis.crypto.getRandomValues(bytes);
  let out = '';
  for (const b of bytes) out += ALPHABET[b & 63];
  return out;
}

/** @returns {Storage | null} */
function sessionStore() {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

/**
 * The per-session viewer id (persisted in sessionStorage when possible).
 * @param {Storage | null} [storage]
 */
export function viewerId(storage = sessionStore()) {
  try {
    const existing = storage ? storage.getItem(VIEWER_KEY) : null;
    if (existing && VIEWER_RE.test(existing)) return existing;
  } catch {
    /* storage blocked */
  }
  const id = randomId(24);
  try {
    if (storage) storage.setItem(VIEWER_KEY, id);
  } catch {
    /* storage blocked */
  }
  return id;
}

/**
 * @typedef {object} AnalyticsEvent
 * @property {string} event
 * @property {number} t
 * @property {string} [scene_id]
 * @property {number} [scene_t]
 * @property {Record<string, unknown>} [data]
 */

/**
 * @typedef {object} AnalyticsOptions
 * @property {number | null} [versionId]
 * @property {string | null} [shareToken]
 * @property {(body: any) => Promise<unknown>} [send]   transport (default: api() POST with keepalive)
 * @property {Storage | null} [storage]
 * @property {number} [flushIntervalMs]
 * @property {number} [batchSize]                       flush as soon as this many events are queued
 */

/** @param {number} x */
const round3 = (x) => Math.round(Math.max(0, x) * 1000) / 1000;

export class Analytics {
  /** @param {AnalyticsOptions} opts */
  constructor(opts) {
    this.shareToken = opts.shareToken || null;
    this.versionId = opts.versionId ?? null;
    this.viewerId = viewerId(opts.storage === undefined ? sessionStore() : opts.storage);
    this.send = opts.send || ((/** @type {any} */ body) => api(ENDPOINT, { method: 'POST', json: body, keepalive: true }));
    this.batchSize = opts.batchSize || 20;
    /** @type {AnalyticsEvent[]} */
    this.queue = [];
    this.inFlight = 0;
    this.destroyed = false;
    this.timer = setInterval(() => this.flush(), opts.flushIntervalMs || 10000);
    this.onPageHide = () => this.flush();
    this.onVisibility = () => {
      if (document.visibilityState === 'hidden') this.flush();
    };
    if (typeof window !== 'undefined') {
      window.addEventListener('pagehide', this.onPageHide);
      document.addEventListener('visibilitychange', this.onVisibility);
    }
  }

  /** Whether events can be attributed (a share token or a version id). */
  get enabled() {
    return !!this.shareToken || this.versionId !== null;
  }

  /**
   * Queue an event.
   * @param {string} event   one of ANALYTICS_EVENTS
   * @param {{ sceneId?: string | null, t: number, sceneT?: number | null, data?: Record<string, unknown> }} info
   */
  track(event, info) {
    if (this.destroyed || !this.enabled || !ANALYTICS_EVENTS.includes(event)) return;
    /** @type {AnalyticsEvent} */
    const ev = { event, t: round3(Number(info.t) || 0) };
    if (info.sceneId) ev.scene_id = String(info.sceneId).slice(0, 64);
    if (info.sceneT !== undefined && info.sceneT !== null && Number.isFinite(info.sceneT)) ev.scene_t = round3(info.sceneT);
    if (info.data) ev.data = info.data;
    this.queue.push(ev);
    if (this.queue.length > MAX_QUEUE) this.queue.splice(0, this.queue.length - MAX_QUEUE);
    if (this.queue.length >= this.batchSize) this.flush();
  }

  /** @param {AnalyticsEvent[]} events */
  body(events) {
    /** @type {Record<string, unknown>} */
    const body = { viewer_id: this.viewerId, events };
    if (this.shareToken) body.share_token = this.shareToken;
    else body.version_id = this.versionId;
    return body;
  }

  /**
   * Split queued events into request bodies (<= 100 events and <= ~30 KB each).
   * @param {AnalyticsEvent[]} events
   * @returns {Record<string, unknown>[]}
   */
  batches(events) {
    /** @type {Record<string, unknown>[]} */
    const out = [];
    /** @type {AnalyticsEvent[]} */
    let cur = [];
    for (const ev of events) {
      const next = [...cur, ev];
      if (cur.length && (next.length > MAX_EVENTS_PER_REQUEST || JSON.stringify(this.body(next)).length > MAX_BODY_BYTES)) {
        out.push(this.body(cur));
        cur = [ev];
      } else {
        cur = next;
      }
    }
    if (cur.length) out.push(this.body(cur));
    return out;
  }

  /**
   * Send everything queued. Network failures re-queue the events once; HTTP errors drop them.
   * @returns {Promise<void>}
   */
  async flush() {
    if (!this.queue.length) return;
    const events = this.queue.splice(0);
    await Promise.all(
      this.batches(events).map(async (body) => {
        this.inFlight += 1;
        try {
          await this.send(body);
        } catch (err) {
          const status = /** @type {any} */ (err)?.status;
          const evs = /** @type {AnalyticsEvent[]} */ (body.events);
          if (!status && !this.destroyed && !evs.some((e) => /** @type {any} */ (e)._retried)) {
            for (const e of evs) Object.defineProperty(e, '_retried', { value: true, enumerable: false });
            this.queue.unshift(...evs);
          }
        } finally {
          this.inFlight -= 1;
        }
      }),
    );
  }

  /** Flush (keepalive) and stop timers/listeners. */
  destroy() {
    if (this.destroyed) return;
    this.flush();
    this.destroyed = true;
    clearInterval(this.timer);
    if (typeof window !== 'undefined') {
      window.removeEventListener('pagehide', this.onPageHide);
      document.removeEventListener('visibilitychange', this.onVisibility);
    }
  }
}
