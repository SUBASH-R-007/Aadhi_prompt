// @ts-check
import { test, describe, after, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { installDom, uninstallDom, fakeTex, tick } from './_dom.js';
import { loadTimeline } from './_fixture.js';

installDom();
/** @type {any} */ (globalThis).__AADHI_NO_AUTOBOOT__ = true;
const { parseRoute, fetchLecture, describeError, boot } = await import('../../js/player/watch.js');
const { ApiError } = await import('../../js/shared/api.js');

const here = dirname(fileURLToPath(import.meta.url));
const watchHtml = readFileSync(join(here, '..', '..', 'watch.html'), 'utf8');

after(() => uninstallDom());

/** Load watch.html's body into the jsdom document. */
function mountPage() {
  const body = /<body[^>]*>([\s\S]*)<\/body>/.exec(watchHtml);
  document.body.innerHTML = body ? body[1] : ''; // trusted static test fixture
}

const deps = {
  renderTex: fakeTex().render,
  texIdle: () => Promise.resolve(),
  loadPrism: async () => null,
  createPanel: (/** @type {HTMLElement} */ c) => {
    const el = document.createElement('div');
    c.appendChild(el);
    return { el, update() {}, destroy() {} };
  },
  waitForFonts: false,
  sendAnalytics: async () => {},
};

describe('watch.html', () => {
  test('is CSP-compatible: no inline script or style, module entry point and stylesheets', () => {
    assert.equal(/<script(?![^>]*\bsrc=)[^>]*>/i.test(watchHtml), false, 'no inline <script>');
    assert.equal(/<style/i.test(watchHtml), false, 'no <style> blocks');
    assert.equal(/\sstyle=/i.test(watchHtml), false, 'no style attributes');
    assert.equal(/\son[a-z]+=/i.test(watchHtml), false, 'no inline event handlers');
    assert.match(watchHtml, /<script type="module" src="\/web\/js\/player\/watch\.js"><\/script>/);
    for (const css of ['tokens', 'base', 'player', 'panels']) assert.ok(watchHtml.includes(`/web/css/${css}.css`), css);
    assert.match(watchHtml, /name="referrer" content="same-origin"/);
    assert.match(watchHtml, /noindex/);
  });
});

describe('routing and loading', () => {
  test('parseRoute', () => {
    assert.deepEqual(parseRoute('/watch/AbC-123_xyz'), { kind: 'watch', token: 'AbC-123_xyz' });
    assert.deepEqual(parseRoute('/watch/AbC-123_xyz/'), { kind: 'watch', token: 'AbC-123_xyz' });
    assert.deepEqual(parseRoute('/preview/42'), { kind: 'preview', versionId: 42 });
    assert.equal(parseRoute('/preview/0'), null);
    assert.equal(parseRoute('/watch/../../etc'), null);
    assert.equal(parseRoute('/watch/abc'), null, 'too short');
    assert.equal(parseRoute('/watch/<script>'), null);
    assert.equal(parseRoute('/'), null);
  });

  test('fetchLecture: share links use the public endpoint, previews the version timeline', async () => {
    const timeline = loadTimeline();
    /** @type {string[]} */
    const paths = [];
    const w = await fetchLecture({ kind: 'watch', token: 'tok_123456' }, async (p) => {
      paths.push(p);
      return { timeline, project: { title: "Ohm's Law", subject_name: 'BEE', session_title: 'Session 2' }, share_token: 'tok_123456' };
    });
    assert.equal(paths[0], '/api/public/watch/tok_123456');
    assert.equal(w.mode, 'live');
    assert.equal(w.title, "Ohm's Law");
    assert.equal(w.subtitle, 'BEE · Session 2');
    assert.deepEqual(w.analytics, { shareToken: 'tok_123456' });
    const p = await fetchLecture({ kind: 'preview', versionId: 7 }, async (path) => {
      paths.push(path);
      return timeline;
    });
    assert.equal(paths[1], '/api/versions/7/timeline');
    assert.equal(p.mode, 'preview');
    assert.equal(p.analytics, null);
    assert.equal(p.title, "Ohm's Law");
  });

  test('describeError maps API failures to friendly messages', () => {
    const watch = /** @type {const} */ ({ kind: 'watch', token: 'tok_123456' });
    const preview = /** @type {const} */ ({ kind: 'preview', versionId: 3 });
    assert.match(describeError(new ApiError(404, { detail: 'x', code: 'not_found' }, null), watch).detail, /expired|revoked/);
    assert.equal(describeError(new ApiError(404, null, null), preview).title, 'No timeline yet');
    assert.equal(describeError(new ApiError(401, null, null), preview).signIn, true);
    assert.equal(describeError(new ApiError(429, null, 5), watch).retry, true);
    assert.equal(describeError(new TypeError('Failed to fetch'), watch).retry, true);
    assert.equal(describeError(null, null).title, 'Link not recognised');
  });
});

describe('boot', () => {
  afterEach(() => document.body.replaceChildren());

  test('success: title, player and controls are built; prefs are restored', async () => {
    mountPage();
    try {
      window.localStorage.setItem('aadhi_player_prefs', JSON.stringify({ volume: 0.5, rate: 1.25, captions: false, captionSize: 'l' }));
    } catch {
      /* ignore */
    }
    const timeline = loadTimeline();
    const res = await boot({
      pathname: '/watch/tok_123456',
      fetchJson: async () => ({ timeline, project: { title: "Ohm's <b>Law</b>" }, share_token: 'tok_123456' }),
      playerDeps: deps,
    });
    assert.ok(res);
    const { player, controls } = /** @type {any} */ (res);
    assert.equal(document.querySelector('.watch-title-main')?.textContent, "Ohm's <b>Law</b>");
    assert.equal(document.querySelector('.watch-title-main b'), null);
    assert.ok(document.title.startsWith("Ohm's <b>Law</b>"));
    assert.ok(document.querySelector('[data-player] .aadhi-player.mode-live'));
    assert.ok(document.querySelector('[data-player] .ap-controls'));
    assert.equal(player.volume, 0.5);
    assert.equal(player.rate, 1.25);
    assert.equal(player.captionsOn, false);
    assert.equal(document.querySelector('.watch-status'), null, 'loading state removed');
    controls.destroy();
    player.destroy();
  });

  test('preview: badge for estimated timelines, no analytics', async () => {
    mountPage();
    const timeline = { ...loadTimeline(), estimated: true };
    const res = await boot({ pathname: '/preview/7', fetchJson: async () => timeline, playerDeps: deps });
    const { player, controls } = /** @type {any} */ (res);
    assert.equal(document.querySelector('.watch-badge')?.textContent, 'Preview · estimated timing');
    assert.equal(player.mode, 'preview');
    assert.equal(player.analytics, null);
    controls.destroy();
    player.destroy();
  });

  test('errors: 404 share link shows an explanation without retry; network errors offer retry', async () => {
    mountPage();
    const res = await boot({
      pathname: '/watch/tok_123456',
      fetchJson: async () => {
        throw new ApiError(404, { detail: 'not found', code: 'not_found' }, null);
      },
    });
    assert.equal(res, null);
    assert.equal(document.querySelector('.watch-status-title')?.textContent, 'Lecture unavailable');
    assert.equal(document.querySelector('.watch-status button'), null);
    assert.equal(document.querySelector('.watch-status')?.getAttribute('role'), 'alert');

    let attempts = 0;
    const timeline = loadTimeline();
    const fetchJson = async () => {
      attempts++;
      if (attempts === 1) throw new TypeError('Failed to fetch');
      return { timeline, project: { title: 'T' }, share_token: 'tok_123456' };
    };
    await boot({ pathname: '/watch/tok_123456', fetchJson, playerDeps: deps });
    const retry = /** @type {HTMLButtonElement} */ (document.querySelector('.watch-status button'));
    assert.equal(retry.textContent, 'Try again');
    retry.click();
    await tick(20);
    assert.equal(attempts, 2);
    assert.ok(document.querySelector('.aadhi-player'));
    // entering the back/forward cache only pauses (the page may be restored)
    const PTE = /** @type {any} */ (window).PageTransitionEvent;
    assert.ok(PTE, 'jsdom provides PageTransitionEvent');
    window.dispatchEvent(new PTE('pagehide', { persisted: true }));
    assert.ok(document.querySelector('.aadhi-player'), 'bfcache: player kept');
    // leaving the page for good tears the player down (releases timers, flushes analytics)
    window.dispatchEvent(new PTE('pagehide', { persisted: false }));
    assert.equal(document.querySelector('.aadhi-player'), null);
  });

  test('unknown routes and broken timelines show error states', async () => {
    mountPage();
    assert.equal(await boot({ pathname: '/somewhere', fetchJson: async () => ({}) }), null);
    assert.equal(document.querySelector('.watch-status-title')?.textContent, 'Link not recognised');
    mountPage();
    const errLog = console.error;
    console.error = () => {};
    try {
      const res = await boot({ pathname: '/preview/3', fetchJson: async () => ({ scenes: null }), playerDeps: deps });
      assert.equal(res, null);
    } finally {
      console.error = errLog;
    }
    assert.equal(document.querySelector('.watch-status-title')?.textContent, 'This lecture could not be played');
    assert.equal(document.querySelector('.aadhi-player'), null);
  });
});
