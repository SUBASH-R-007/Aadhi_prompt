// @ts-check
import { test, describe, after } from 'node:test';
import assert from 'node:assert/strict';

import { installDom, uninstallDom, tick } from './_dom.js';

installDom();
const { Analytics, viewerId, randomId, VIEWER_KEY, MAX_EVENTS_PER_REQUEST, MAX_BODY_BYTES } = await import('../../js/player/analytics.js');

after(() => uninstallDom());

/** In-memory Storage. */
function memoryStorage() {
  /** @type {Map<string, string>} */
  const m = new Map();
  return /** @type {Storage} */ (/** @type {unknown} */ ({
    getItem: (/** @type {string} */ k) => (m.has(k) ? m.get(k) : null),
    setItem: (/** @type {string} */ k, /** @type {string} */ v) => m.set(k, String(v)),
    removeItem: (/** @type {string} */ k) => m.delete(k),
  }));
}

describe('viewer id', () => {
  test('random, URL-safe, 24 chars', () => {
    const a = randomId();
    assert.match(a, /^[A-Za-z0-9_-]{24}$/);
    assert.notEqual(a, randomId());
  });

  test('persisted per session in sessionStorage', () => {
    const store = memoryStorage();
    const a = viewerId(store);
    assert.equal(viewerId(store), a);
    assert.equal(store.getItem(VIEWER_KEY), a);
  });

  test('invalid stored ids are replaced; blocked storage still yields an id', () => {
    const store = memoryStorage();
    store.setItem(VIEWER_KEY, 'bad id!');
    assert.match(viewerId(store), /^[A-Za-z0-9_-]{24}$/);
    const throwing = /** @type {Storage} */ (/** @type {unknown} */ ({
      getItem() {
        throw new Error('SecurityError');
      },
      setItem() {
        throw new Error('SecurityError');
      },
    }));
    assert.match(viewerId(throwing), /^[A-Za-z0-9_-]{24}$/);
    assert.match(viewerId(null), /^[A-Za-z0-9_-]{24}$/);
  });
});

describe('Analytics', () => {
  /** @param {Partial<import('../../js/player/analytics.js').AnalyticsOptions>} [opts] */
  function make(opts = {}) {
    /** @type {any[]} */
    const sent = [];
    const a = new Analytics({
      shareToken: 'tok-1234567890',
      storage: memoryStorage(),
      send: async (body) => {
        sent.push(JSON.parse(JSON.stringify(body)));
      },
      flushIntervalMs: 60_000,
      ...opts,
    });
    return { a, sent };
  }

  test('share-token bodies: viewer id + events with t, scene_id, scene_t, data', async () => {
    const { a, sent } = make();
    a.track('session_start', { t: 0 });
    a.track('quiz_answer', { t: 12.34567, sceneId: 's-quiz', sceneT: 3.21, data: { choice: 2, correct: true } });
    await a.flush();
    assert.equal(sent.length, 1);
    assert.equal(sent[0].share_token, 'tok-1234567890');
    assert.equal('version_id' in sent[0], false);
    assert.match(sent[0].viewer_id, /^[A-Za-z0-9_-]{16,64}$/);
    assert.deepEqual(sent[0].events, [
      { event: 'session_start', t: 0 },
      { event: 'quiz_answer', t: 12.346, scene_id: 's-quiz', scene_t: 3.21, data: { choice: 2, correct: true } },
    ]);
    a.destroy();
  });

  test('version-id bodies when there is no share token', async () => {
    const { a, sent } = make({ shareToken: null, versionId: 7 });
    a.track('pause', { t: 1, sceneId: 's1', sceneT: 1 });
    await a.flush();
    assert.equal(sent[0].version_id, 7);
    assert.equal('share_token' in sent[0], false);
    a.destroy();
  });

  test('unknown events and disabled analytics are dropped', async () => {
    const { a, sent } = make();
    a.track('hack', { t: 1 });
    await a.flush();
    assert.equal(sent.length, 0);
    const off = make({ shareToken: null, versionId: null });
    off.a.track('pause', { t: 1 });
    await off.a.flush();
    assert.equal(off.sent.length, 0);
    a.destroy();
    off.a.destroy();
  });

  test('flushes automatically once the batch size is reached', async () => {
    const { a, sent } = make({ batchSize: 3 });
    a.track('seek', { t: 1, data: { from: 0, to: 1 } });
    a.track('seek', { t: 2, data: { from: 1, to: 2 } });
    assert.equal(sent.length, 0);
    a.track('seek', { t: 3, data: { from: 2, to: 3 } });
    await tick();
    assert.equal(sent.length, 1);
    assert.equal(sent[0].events.length, 3);
    a.destroy();
  });

  test('splits into requests of <= 100 events and <= 30 KB', () => {
    const { a } = make({ batchSize: 10_000 });
    const events = Array.from({ length: 250 }, (_, i) => ({ event: 'seek', t: i, scene_id: `scene-${'x'.repeat(40)}-${i}`, data: { from: i, to: i + 1 } }));
    const bodies = a.batches(events);
    assert.ok(bodies.length >= 3);
    for (const b of bodies) {
      assert.ok(/** @type {any[]} */ (b.events).length <= MAX_EVENTS_PER_REQUEST);
      assert.ok(JSON.stringify(b).length <= MAX_BODY_BYTES);
    }
    assert.equal(bodies.reduce((n, b) => n + /** @type {any[]} */ (b.events).length, 0), 250);
    a.destroy();
  });

  test('network failures re-queue once; HTTP errors drop', async () => {
    let calls = 0;
    const a = new Analytics({
      shareToken: 'tok-1234567890',
      storage: memoryStorage(),
      send: async () => {
        calls++;
        throw new TypeError('Failed to fetch');
      },
    });
    a.track('pause', { t: 1 });
    await a.flush();
    assert.equal(a.queue.length, 1, 're-queued after a network error');
    await a.flush();
    assert.equal(a.queue.length, 0, 'dropped after the retry');
    assert.equal(calls, 2);
    const b = new Analytics({
      shareToken: 'tok-1234567890',
      storage: memoryStorage(),
      send: async () => {
        throw Object.assign(new Error('rate limited'), { status: 429 });
      },
    });
    b.track('pause', { t: 1 });
    await b.flush();
    assert.equal(b.queue.length, 0);
    a.destroy();
    b.destroy();
  });

  test('pagehide flushes; destroy flushes and stops', async () => {
    const { a, sent } = make();
    a.track('pause', { t: 1 });
    window.dispatchEvent(new window.Event('pagehide'));
    await tick();
    assert.equal(sent.length, 1);
    a.track('resume', { t: 2 });
    a.destroy();
    await tick();
    assert.equal(sent.length, 2);
    a.track('pause', { t: 3 });
    await a.flush();
    assert.equal(sent.length, 2, 'no tracking after destroy');
  });

  test('default transport posts JSON with keepalive and the CSRF header', async () => {
    /** @type {any[]} */
    const calls = [];
    const orig = globalThis.fetch;
    globalThis.fetch = /** @type {any} */ (
      async (/** @type {string} */ url, /** @type {any} */ init) => {
        calls.push({ url, init });
        return new Response(null, { status: 202 });
      }
    );
    try {
      const a = new Analytics({ shareToken: 'tok-1234567890', storage: memoryStorage() });
      a.track('complete', { t: 99 });
      await a.flush();
      a.destroy();
    } finally {
      globalThis.fetch = orig;
    }
    assert.equal(calls[0].url, '/api/analytics/events');
    assert.equal(calls[0].init.method, 'POST');
    assert.equal(calls[0].init.keepalive, true);
    assert.equal(calls[0].init.headers['X-Aadhi-CSRF'], '1');
    assert.equal(JSON.parse(calls[0].init.body).events[0].event, 'complete');
  });
});
