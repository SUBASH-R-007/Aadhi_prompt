import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

import {
  PLAYER_METHODS,
  RenderError,
  TIMELINE_ENDPOINT,
  boot,
  fetchTimeline,
  normalizeResult,
  readOptions,
  takeToken,
  titleOnlyPanels,
} from '../../js/render/render.js';

const TOKEN = 'eyJhbGciOiJIUzI1NiJ9.eyJ0eXAiOiJzY29wZWQifQ.c2lnbmF0dXJlLXNpZ25hdHVyZQ';

function makeWindow(hash = `#token=${TOKEN}`) {
  const dom = new JSDOM('<!doctype html><html><body><div data-aadhi-render-root></div></body></html>', {
    url: `https://app.test/render-frame?x=1${hash}`,
    pretendToBeVisual: true,
  });
  const w = dom.window;
  const loads = [];
  w.document.fonts = {
    load: (font, text) => {
      loads.push([font, text]);
      return Promise.resolve([{}]);
    },
    ready: Promise.resolve(),
  };
  return { w, loads };
}

function jsonResponse(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  };
}

const TIMELINE = {
  language: 'ta-IN',
  board_language: 'en-IN',
  width: 1920,
  height: 1080,
  scenes: [
    { scene_id: 's1', index: 0, duration: 5, title: 'Intro' },
    { scene_id: 's2', index: 1, duration: 6, title: 'Ohm' },
  ],
};

function fakePlayerModule(log, overrides = {}) {
  class Player {
    constructor(root, opts) {
      log.push(['construct', root.hasAttribute('data-aadhi-render-root'), opts]);
    }
    async load(timeline) {
      log.push(['load', timeline.scenes.length]);
    }
    states(i) {
      return [{ t: 0, key: `s${i}-a` }, { t: 1.5, key: `s${i}-b` }];
    }
    async renderState({ sceneIndex, t }) {
      log.push(['renderState', sceneIndex, t]);
      await new Promise((r) => setTimeout(r, 5));
      log.push(['renderState:done', sceneIndex, t]);
      return { state_key: `s${sceneIndex}-${t}`, media_rect: null, panel_media_rect: { x: 1, y: 2, width: 3, height: 4 }, media_fit: null };
    }
    async renderIntro({ t }) {
      return { state_key: `intro-${t}`, media_rect: { x: 0, y: 0, width: 1920, height: 1080 }, media_fit: 'cover' };
    }
    destroy() {}
  }
  Object.assign(Player.prototype, overrides);
  return { Player };
}

function deps(w, log, extra = {}) {
  const fetches = [];
  return {
    fetches,
    deps: {
      window: w,
      fetch: async (url, init) => {
        fetches.push([url, init]);
        return jsonResponse(TIMELINE);
      },
      importPlayer: async () => fakePlayerModule(log),
      loadMathJax: async () => {
        log.push(['mathjax']);
        return {};
      },
      texIdle: async () => undefined,
      frame: () => new Promise((r) => setTimeout(r, 0)),
      ...extra,
    },
  };
}

test('takeToken reads the fragment and removes it from the URL', () => {
  const { w } = makeWindow();
  const token = takeToken(w.location, w.history);
  assert.equal(token, TOKEN);
  assert.equal(w.location.hash, '');
  assert.equal(w.location.href, 'https://app.test/render-frame?x=1');
});

test('takeToken rejects missing or malformed tokens but still clears the fragment', () => {
  for (const hash of ['', '#', '#foo=bar', '#token=', '#token=has%20space', '#token=<script>', `#token=${'a'.repeat(5000)}`]) {
    const { w } = makeWindow(hash);
    assert.throws(() => takeToken(w.location, w.history), (e) => e instanceof RenderError && e.code === 'token_missing', hash);
    assert.equal(w.location.hash, '', `fragment cleared for ${hash}`);
  }
});

test('fetchTimeline sends the bearer token without cookies or caching', async () => {
  const calls = [];
  const tl = await fetchTimeline(TOKEN, async (url, init) => {
    calls.push([url, init]);
    return jsonResponse(TIMELINE);
  });
  assert.equal(tl.scenes.length, 2);
  const [url, init] = calls[0];
  assert.equal(url, TIMELINE_ENDPOINT);
  assert.equal(url, '/api/render/timeline');
  assert.equal(init.headers.Authorization, `Bearer ${TOKEN}`);
  assert.equal(init.credentials, 'omit');
  assert.equal(init.cache, 'no-store');
  assert.equal(init.method, 'GET');
});

test('fetchTimeline errors carry the HTTP status and code but never the token', async () => {
  await assert.rejects(
    fetchTimeline(TOKEN, async () => jsonResponse({ detail: 'expired', code: 'unauthenticated' }, 401)),
    (e) => e.code === 'timeline_http' && /HTTP 401 \(unauthenticated\)/.test(e.message) && !e.message.includes(TOKEN),
  );
  await assert.rejects(fetchTimeline(TOKEN, async () => jsonResponse({ nope: true })), /no scenes/);
  await assert.rejects(
    fetchTimeline(TOKEN, async () => {
      throw new Error('offline');
    }),
    (e) => e.code === 'timeline_network',
  );
});

test('boot: loads everything, then exposes ready/states/show/showIntro', async () => {
  const { w, loads } = makeWindow();
  const log = [];
  const { deps: d, fetches } = deps(w, log);
  const api = await boot(d);
  assert.equal(w.aadhiRender, api);
  assert.equal(api.ready, true);
  assert.equal(api.error, null);
  assert.equal(w.location.hash, '', 'token removed from the URL');
  assert.equal(fetches.length, 1);
  assert.equal(fetches[0][1].headers.Authorization, `Bearer ${TOKEN}`);
  const construct = log.find((e) => e[0] === 'construct');
  assert.equal(construct[1], true, 'player mounted in the render root');
  assert.equal(construct[2].mode, 'render');
  assert.equal(construct[2].analytics, null);
  assert.ok(log.some((e) => e[0] === 'mathjax'));
  assert.equal(w.document.documentElement.lang, 'en-IN');
  const families = loads.map((l) => l[0]);
  for (const f of ['400 32px "Inter"', '600 32px "Inter"', '700 32px "Inter"', '400 32px "Outfit"', '700 32px "Outfit"', '900 32px "Outfit"', '400 32px "JetBrains Mono"', '400 32px "Noto Sans Tamil"', '700 32px "Noto Sans Tamil"']) {
    assert.ok(families.includes(f), `loads ${f}`);
  }

  assert.deepEqual(api.states(1), [{ t: 0, key: 's1-a' }, { t: 1.5, key: 's1-b' }]);
  const shown = await api.show({ scene: 1, t: 1.5 });
  assert.deepEqual(shown, { state_key: 's1-1.5', media_rect: null, panel_media_rect: { x: 1, y: 2, width: 3, height: 4 }, media_fit: null });
  const intro = await api.showIntro({ t: 2 });
  assert.equal(intro.state_key, 'intro-2');
  assert.equal(intro.media_fit, 'cover');
  assert.equal(intro.panel_media_rect, null);
});

test('show() serialises renders and validates its arguments', async () => {
  const { w } = makeWindow();
  const log = [];
  const api = await boot(deps(w, log).deps);
  log.length = 0;
  await Promise.all([api.show({ scene: 0, t: 1 }), api.show({ scene: 1, t: 2 })]);
  assert.deepEqual(
    log.map((e) => e[0]),
    ['renderState', 'renderState:done', 'renderState', 'renderState:done'],
    'one state at a time',
  );
  await assert.rejects(api.show({ scene: 2, t: 0 }), RangeError);
  await assert.rejects(api.show({ scene: 0.5, t: 0 }), RangeError);
  await assert.rejects(api.show({ scene: 0, t: -1 }), RangeError);
  await assert.rejects(api.show({ scene: 0, t: Number.NaN }), RangeError);
  await assert.rejects(api.showIntro({ t: 'x' }), RangeError);
  assert.throws(() => api.states(-1), RangeError);
});

test('boot fails loudly without a token and never calls the API', async () => {
  const { w } = makeWindow('');
  const log = [];
  const { deps: d, fetches } = deps(w, log);
  const errors = [];
  const origError = console.error;
  console.error = (...a) => errors.push(a);
  try {
    await assert.rejects(boot(d), (e) => e.code === 'token_missing');
  } finally {
    console.error = origError;
  }
  assert.equal(fetches.length, 0);
  assert.equal(w.aadhiRender.ready, false);
  assert.deepEqual(w.aadhiRender.error.code, 'token_missing');
  await assert.rejects(w.aadhiRender.show({ scene: 0, t: 0 }), /not ready/);
  assert.equal(errors.length, 1);
});

test('boot fails loudly when the player API is missing', async () => {
  const quiet = console.error;
  console.error = () => {};
  try {
    for (const [importPlayer, pattern] of [
      [async () => ({}), /must export class Player/],
      [async () => fakePlayerModule([], { renderState: undefined, renderIntro: 'nope' }), /missing render-mode methods: renderState, renderIntro/],
      [async () => {
        throw new Error('404 player.js');
      }, /cannot import .*404 player\.js/],
    ]) {
      const { w } = makeWindow();
      const { deps: d } = deps(w, [], { importPlayer });
      await assert.rejects(boot(d), pattern);
      assert.equal(w.aadhiRender.ready, false);
      assert.equal(w.aadhiRender.error.code.startsWith('player_'), true);
    }
  } finally {
    console.error = quiet;
  }
  assert.deepEqual([...PLAYER_METHODS], ['load', 'states', 'renderState', 'renderIntro', 'destroy']);
});

test('boot reports HTTP failures from the timeline endpoint', async () => {
  const quiet = console.error;
  console.error = () => {};
  try {
    const { w } = makeWindow();
    const { deps: d } = deps(w, [], { fetch: async () => jsonResponse({ code: 'forbidden' }, 403) });
    await assert.rejects(boot(d), /HTTP 403/);
    assert.equal(w.aadhiRender.error.code, 'timeline_http');
  } finally {
    console.error = quiet;
  }
});

test('boot fails when a requested font face is missing (no fallback-font MP4s)', async () => {
  const quiet = console.error;
  console.error = () => {};
  try {
    const { w } = makeWindow();
    // Inter 700 and the timeline's Tamil faces have no @font-face (load() resolves [] without error)
    w.document.fonts = {
      load: (font) => Promise.resolve(/"Inter"/.test(font) && font.startsWith('700') ? [] : /Tamil/.test(font) ? [] : [{}]),
      ready: Promise.resolve(),
    };
    const log = [];
    await assert.rejects(boot(deps(w, log).deps), (e) => e instanceof RenderError && e.code === 'fonts_missing');
    assert.equal(w.aadhiRender.ready, false);
    assert.equal(w.aadhiRender.error.code, 'fonts_missing');
    assert.match(w.aadhiRender.error.message, /Inter 700/);
    assert.match(w.aadhiRender.error.message, /Noto Sans Tamil 400/);
    assert.throws(() => w.aadhiRender.states(0), /not ready/);

    // no FontFaceSet at all: every face is missing
    const { w: w2 } = makeWindow();
    w2.document.fonts = undefined;
    await assert.rejects(boot(deps(w2, []).deps), (e) => e.code === 'fonts_missing');
    assert.equal(w2.aadhiRender.ready, false);
  } finally {
    console.error = quiet;
  }
});

test('normalizeResult fills the documented keys', () => {
  assert.deepEqual(normalizeResult(null, 'k'), { state_key: 'k', media_rect: null, panel_media_rect: null, media_fit: null });
  assert.deepEqual(normalizeResult({ state_key: 7, extra: 1 }, 'k'), { state_key: '7', extra: 1, media_rect: null, panel_media_rect: null, media_fit: null });
});

test('render.html is CSP-compatible and wires the module', () => {
  const html = readFileSync(new URL('../../render.html', import.meta.url), 'utf8');
  const scripts = [...html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)];
  assert.equal(scripts.length, 1);
  assert.match(scripts[0][1], /type="module"/);
  assert.match(scripts[0][1], /src="\/web\/js\/render\/render\.js"/);
  assert.equal(scripts[0][2].trim(), '', 'no inline script');
  assert.doesNotMatch(html, /\son[a-z]+=/i, 'no inline event handlers');
  assert.match(html, /data-aadhi-render="page"/);
  assert.match(html, /data-aadhi-render-root/);
  assert.match(html, /\/web\/css\/panels\.css/);
  assert.match(html, /background: transparent/);
  assert.match(html, /width: 1920px;\s*height: 1080px/);
});

test('titleOnlyPanels reduces side panels showing a placeholder notice to their title', () => {
  const { w } = makeWindow();
  const root = w.document.querySelector('[data-aadhi-render-root]');
  root.innerHTML =
    '<section class="ap-panel"><header>Figure</header><div class="ap-body"><div class="ap-notice ap-notice--info">The figure is not  available yet.</div></div></section>' +
    '<section class="ap-panel"><div class="ap-body"><canvas class="ap-tree-canvas"></canvas><div class="ap-notice">The figure is not available yet.</div></div></section>' +
    '<section class="ap-panel" id="ok"><div class="ap-body"><img class="ap-img" src="x.png"></div></section>' +
    '<div class="ap-board"><div class="ap-notice">not a panel</div></div>';
  assert.deepEqual(titleOnlyPanels(root), ['The figure is not available yet.']);
  const panels = [...root.querySelectorAll('.ap-panel')];
  assert.deepEqual(
    panels.map((p) => p.classList.contains('ap-panel--title-only')),
    [true, true, false],
  );
  assert.deepEqual(titleOnlyPanels(w.document.createElement('div')), []);
});

test('titleOnlyPanels ignores notices that are not shown (model3d keeps a hidden "context lost" notice)', () => {
  const { w } = makeWindow();
  const root = w.document.querySelector('[data-aadhi-render-root]');
  root.innerHTML =
    '<section class="ap-panel" id="m3d"><div class="ap-body"><canvas></canvas><div class="ap-notice ap-3d-lost" hidden>3D view paused (graphics context lost).</div></div></section>' +
    '<section class="ap-panel" id="wrapped"><div class="ap-body"><div hidden><div class="ap-notice">Not shown.</div></div></div></section>' +
    '<section class="ap-panel" id="styled"><div class="ap-body"><div class="ap-notice" style="display:none">Not shown either.</div></div></section>';
  assert.deepEqual(titleOnlyPanels(root), []);
  assert.equal(root.querySelectorAll('.ap-panel--title-only').length, 0);
  root.querySelector('.ap-3d-lost').hidden = false; // the context really was lost: now it is a placeholder
  assert.deepEqual(titleOnlyPanels(root), ['3D view paused (graphics context lost).']);
  assert.ok(root.querySelector('#m3d').classList.contains('ap-panel--title-only'));
});

test('show() reports hidden panel notices only when there are some', async () => {
  const { w } = makeWindow();
  const log = [];
  const root = w.document.querySelector('[data-aadhi-render-root]');
  const d = deps(w, log, {
    importPlayer: async () =>
      fakePlayerModule(log, {
        async renderState({ sceneIndex, t }) {
          root.innerHTML = sceneIndex === 1 ? '<div class="ap-panel"><div class="ap-body"><div class="ap-notice">The illustration has not been generated yet.</div></div></div>' : '';
          return { state_key: `s${sceneIndex}-${t}` };
        },
      }),
  }).deps;
  const api = await boot(d);
  const plain = await api.show({ scene: 0, t: 0 });
  assert.equal('notices' in plain, false);
  const shown = await api.show({ scene: 1, t: 0 });
  assert.deepEqual(shown.notices, ['The illustration has not been generated yet.']);
  assert.ok(root.querySelector('.ap-panel').classList.contains('ap-panel--title-only'));
});

test('readOptions reads the glass flag next to the token, and boot applies it before clearing the fragment', async () => {
  assert.deepEqual(readOptions({ hash: `#token=${TOKEN}&glass=1` }), { glass: true });
  assert.deepEqual(readOptions({ hash: `#token=${TOKEN}` }), { glass: false });
  assert.deepEqual(readOptions({ hash: '' }), { glass: false });
  const { w } = makeWindow(`#token=${TOKEN}&glass=1`);
  await boot(deps(w, []).deps);
  assert.equal(w.document.documentElement.hasAttribute('data-render-glass'), true);
  assert.equal(w.location.hash, '', 'options and token removed from the URL');
  const plain = makeWindow();
  await boot(deps(plain.w, []).deps);
  assert.equal(plain.w.document.documentElement.hasAttribute('data-render-glass'), false);
});

test('render.html glass colours stay in sync with the live player surfaces', () => {
  const html = readFileSync(new URL('../../render.html', import.meta.url), 'utf8');
  const playerCss = readFileSync(new URL('../../css/player.css', import.meta.url), 'utf8');
  const panelsCss = readFileSync(new URL('../../css/panels.css', import.meta.url), 'utf8');
  /** First `background:` declared in the plain (live) rule for `selector`. */
  const live = (css, selector) => {
    const m = new RegExp(String.raw`^${selector.replace(/[.]/g, '\\.')} \{([^}]*)\}`, 'm').exec(css);
    assert.ok(m, `live rule ${selector}`);
    return /background:\s*([^;]+);/.exec(m[1])[1].trim();
  };
  /** The glass override in render.html for `selector`. */
  const glass = (selector) => {
    const re = new RegExp(String.raw`html\[data-render-glass\] \.render-mode ${selector.replace(/[.]/g, '\\.')}[\s,][^{]*\{\s*background:\s*([^;]+);`);
    const m = re.exec(html);
    assert.ok(m, `glass override for ${selector}`);
    return m[1].trim();
  };
  for (const sel of ['.ap-board', '.ap-card', '.ap-quiz', '.ap-media-title', '.ap-media-subtitle', '.ap-media-missing']) {
    assert.equal(glass(sel), live(playerCss, sel), sel);
  }
  assert.equal(live(panelsCss, '.ap-backdrop'), 'var(--c-surface)');
  assert.match(html, /html\[data-render-glass\] \.ap-panel\[data-mode='render'\]:not\(\.ap-panel--terminal-skin\) \.ap-backdrop \{\s*background: var\(--c-surface\);/);
});
