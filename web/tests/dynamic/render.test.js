import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

import { STAGE_VERSION, boot, installStageApi, probeWebgl, timelineTex } from '../../js/dynamic/render.js';
import { TIMELINE_ENDPOINT } from '../../js/render/render.js';
import { classicTimeline, dynamicTimeline } from './_fixture.js';

const TOKEN = 'eyJhbGciOiJIUzI1NiJ9.eyJ0eXAiOiJzY29wZWQifQ.c2lnbmF0dXJlLXNpZ25hdHVyZQ';

/** A render-dynamic page in jsdom, with its globals installed (the stage builds DOM through `document`). */
function makePage(hash = `#token=${TOKEN}`) {
  const dom = new JSDOM('<!doctype html><html data-aadhi-render="dynamic"><body><div data-aadhi-stage-root></div></body></html>', {
    url: `https://app.test/render-dynamic${hash}`,
    pretendToBeVisual: true,
  });
  const w = /** @type {any} */ (dom.window);
  const loads = [];
  w.document.fonts = {
    load: (font, text) => {
      loads.push([font, text]);
      return Promise.resolve([{}]);
    },
    ready: Promise.resolve(),
  };
  w.HTMLImageElement.prototype.decode = () => Promise.resolve();
  const g = /** @type {any} */ (globalThis);
  for (const key of ['window', 'document', 'Node', 'Element', 'HTMLElement', 'HTMLImageElement', 'HTMLVideoElement', 'Image', 'DOMRect']) {
    Object.defineProperty(g, key, { configurable: true, writable: true, value: key === 'window' ? w : w[key] });
  }
  return { w, loads };
}

function jsonResponse(body, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

/** @param {any} w @param {any} timeline @param {any} [extra] */
async function bootWith(w, timeline, extra = {}) {
  const calls = [];
  const fetch = async (url, init) => {
    calls.push([url, init]);
    return typeof timeline === 'number' ? jsonResponse({ code: 'not_found' }, timeline) : jsonResponse(timeline);
  };
  const api = await boot({ window: w, fetch, loadMathJax: async () => ({}), texIdle: () => Promise.resolve(), frame: () => Promise.resolve(), ...extra });
  return { api, calls };
}

/** @param {Promise<any>} p */
async function failure(p) {
  try {
    await p;
  } catch (e) {
    return e;
  }
  return null;
}

test('the page boots: window.aadhiStage v3, ready resolves {webgl}, every method wired', async () => {
  const { w } = makePage();
  const { api, calls } = await bootWith(w, dynamicTimeline());
  assert.equal(w.aadhiStage, api);
  assert.equal(api.version, STAGE_VERSION);
  assert.deepEqual(await api.ready, { webgl: false });
  assert.equal(api.error, null);
  assert.equal(api.info().frames, 1200);
  assert.match((await api.seek(30)).key, /^[0-9a-f]{16}$/);
  assert.ok(Array.isArray(api.textBoxes()));
  assert.ok(Array.isArray(api.mediaPlan(0, 1200)));
  assert.deepEqual(api.setLayers({ captions: false }), { stage: true, captions: false });
  // the token went to the timeline endpoint as a bearer header and left the URL at once
  assert.equal(calls[0][0], TIMELINE_ENDPOINT);
  assert.equal(calls[0][1].headers.Authorization, `Bearer ${TOKEN}`);
  assert.equal(calls[0][1].credentials, 'omit');
  assert.equal(w.location.hash, '');
  assert.equal(w.document.documentElement.lang, 'en-IN');
  assert.equal(w.document.documentElement.style.getPropertyValue('--render-stage-width'), '1920px', 'the page sized from the timeline');
});

test('a lecture that opens on a clip boots: ready never waits for the per-chunk media frames (§7.7)', async () => {
  const { w } = makePage();
  const tl = dynamicTimeline();
  const sim = { ...tl.scenes[3], index: 0, start: 0 };
  tl.scenes = [sim];
  tl.total_duration = sim.duration;
  tl.captions = tl.captions.filter((/** @type {any} */ c) => c.start >= 26).map((/** @type {any} */ c) => ({ ...c, start: c.start - 26, end: c.end - 26 }));
  tl.chapters = [{ start: 0, title: 'Watch' }];
  // the worker answers /__frames/ only while it captures a chunk: before that every frame is a 404
  w.HTMLImageElement.prototype.decode = function decode() {
    return String(this.getAttribute('src') || '').includes('/__frames/') ? Promise.reject(new Error('404')) : Promise.resolve();
  };
  const frameSrcs = () => [...w.document.querySelectorAll('img')].map((/** @type {any} */ im) => String(im.getAttribute('src') || '')).filter((s) => s.includes('/__frames/'));
  const { api } = await bootWith(w, tl);
  assert.deepEqual(await api.ready, { webgl: false });
  assert.equal(api.error, null);
  assert.deepEqual(frameSrcs(), [], 'no media frame requested before ready');
  assert.match(api.mediaPlan(0, 30)[0].key, /^v-/);
  // a real seek asks for its frame (and fails loudly when the worker has none: an engine bug, never a blank clip)
  await assert.rejects(api.seek(0), /media frame failed to load: \/__frames\//);
  assert.equal(frameSrcs().length, 1);
});

test('after ready the timer guard is on: a stray timer throws and is recorded', async () => {
  const { w } = makePage();
  const { api } = await bootWith(w, dynamicTimeline());
  await api.ready;
  assert.equal(w.aadhiStageGuard.installed, true);
  assert.throws(() => w.setTimeout(() => {}, 10), /timer guard/);
  assert.equal(w.aadhiStageGuard.trips.length, 1);
});

test('a classic timeline is refused (it must never reach the motion engine)', async () => {
  const { w } = makePage();
  const { api } = await bootWith(w, classicTimeline());
  const err = await failure(api.ready);
  assert.equal(err.code, 'timeline_invalid');
  assert.equal(api.error.code, 'timeline_invalid');
  assert.throws(() => api.info(), /not ready/);
});

test('boot failures reject ready with a code the worker understands', async () => {
  {
    const { w } = makePage('');
    const { api } = await bootWith(w, dynamicTimeline());
    assert.equal((await failure(api.ready)).code, 'token_missing');
  }
  {
    const { w } = makePage();
    const { api } = await bootWith(w, 403);
    const err = await failure(api.ready);
    assert.equal(err.code, 'timeline_http');
    assert.match(err.message, /HTTP 403/);
  }
  {
    const { w } = makePage();
    w.document.fonts.load = () => Promise.resolve([]);
    const { api } = await bootWith(w, dynamicTimeline());
    assert.equal((await failure(api.ready)).code, 'fonts_missing');
  }
});

test('every font of the timeline is loaded before ready (Indic scripts included)', async () => {
  const { w, loads } = makePage();
  const tl = dynamicTimeline();
  tl.scenes[0].board[1].text = 'மின்னழுத்தம் தள்ளுகிறது';
  const { api } = await bootWith(w, tl);
  await api.ready;
  const families = new Set(loads.map(([font]) => /"(.+)"/.exec(font)[1]));
  for (const f of ['Inter', 'Outfit', 'JetBrains Mono', 'Noto Sans Tamil']) assert.ok(families.has(f), f);
});

test('TeX in the timeline is converted before ready (no MathJax work while frames are captured)', async () => {
  const tl = dynamicTimeline();
  tl.scenes[0].board.push({ id: 'fx', kind: 'formula', latex: 'V = IR', variables: [{ symbol_latex: 'V', meaning: 'voltage' }] });
  tl.scenes[0].board[1].text = 'Ohm: $I = V/R$ and **bold**';
  tl.scenes[2].quiz.options[0] = 'It is $2I$';
  assert.deepEqual(timelineTex(tl), [
    { tex: 'I = V/R', display: false },
    { tex: 'V = IR', display: true },
    { tex: 'V', display: false },
    { tex: '2I', display: false },
  ]);
  const { w } = makePage();
  const converted = [];
  const { api } = await bootWith(w, tl, {
    renderTex: async (tex, display) => {
      converted.push([tex, display]);
      return w.document.createElement('span');
    },
  });
  assert.deepEqual(await api.ready, { webgl: false });
  assert.deepEqual(converted.slice(0, 4).map(([tex]) => tex), ['I = V/R', 'V = IR', 'V', '2I'], 'converted before the first frame');
});

test('installStageApi defines aadhiStage at once (the worker waits for it), not ready yet', async () => {
  const w = /** @type {any} */ ({});
  const { api, reject } = installStageApi(w);
  assert.equal(w.aadhiStage.version, 3);
  assert.throws(() => api.info(), /not ready/);
  await assert.rejects(api.seek(0), /not ready/);
  reject(Object.assign(new Error('x'), { code: 'boot_failed' }));
  assert.equal((await failure(api.ready)).code, 'boot_failed');
});

test('probeWebgl is false without a WebGL2 context', () => {
  const { w } = makePage();
  assert.equal(probeWebgl(w.document), false);
});

test('render-dynamic.html: module script only, the stage root, dynamic.css, no inline script', () => {
  const html = readFileSync(new URL('../../render-dynamic.html', import.meta.url), 'utf8');
  assert.match(html, /<html lang="en" data-aadhi-render="dynamic">/);
  assert.match(html, /<script type="module" src="\/web\/js\/dynamic\/render.js"><\/script>/);
  assert.match(html, /<link rel="stylesheet" href="\/web\/css\/dynamic.css" \/>/);
  assert.match(html, /data-aadhi-stage-root/);
  assert.equal((html.match(/<script/g) || []).length, 1);
  assert.doesNotMatch(html, /player\.css|panels\.css/, 'no stylesheet with live-player animations');
  assert.match(html, /name="referrer" content="no-referrer"/);
});
