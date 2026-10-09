import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { BASE_FONTS, SCRIPT_FONTS, detectScripts, fontRequests, preloadFonts, scriptForLanguage } from '../../js/render/fonts.js';

test('base fonts cover every family/weight used by the player', () => {
  const pairs = BASE_FONTS.flatMap((f) => f.weights.map((wt) => `${f.family} ${wt}`));
  for (const required of ['Inter 400', 'Inter 600', 'Inter 700', 'Outfit 400', 'Outfit 700', 'Outfit 900', 'JetBrains Mono 400']) {
    assert.ok(pairs.includes(required), required);
  }
  assert.equal(pairs.length, 10);
});

test('every requested face exists in the vendored fonts.css', () => {
  const css = readFileSync(new URL('../../vendor/fonts.css', import.meta.url), 'utf8');
  const faces = new Set([...css.matchAll(/font-family:'([^']+)';font-style:normal;font-weight:(\d+)/g)].map((m) => `${m[1]} ${m[2]}`));
  const all = fontRequests({ language: 'ta-IN', board_language: 'hi-IN', scenes: [{ title: 'తెలుగు ಕನ್ನಡ മലയാളം' }] });
  for (const r of all) assert.ok(faces.has(`${r.family} ${r.weight}`), `${r.family} ${r.weight} is vendored`);
});

test('scriptForLanguage maps BCP-47 codes to Indic scripts', () => {
  assert.equal(scriptForLanguage('ta-IN'), 'tamil');
  assert.equal(scriptForLanguage('hi-IN'), 'devanagari');
  assert.equal(scriptForLanguage('te_IN'), 'telugu');
  assert.equal(scriptForLanguage('KN-in'), 'kannada');
  assert.equal(scriptForLanguage('ml-IN'), 'malayalam');
  assert.equal(scriptForLanguage('en-IN'), null);
  assert.equal(scriptForLanguage(undefined), null);
});

test('detectScripts finds scripts anywhere in the timeline', () => {
  const tl = { scenes: [{ board: [{ text: 'Ohm: விதி' }], side_panel: { panel: { title: 'नियम' } } }], meta: { n: 3 } };
  assert.deepEqual([...detectScripts(tl)].sort(), ['devanagari', 'tamil']);
  assert.equal(detectScripts({ scenes: [{ title: 'English only' }] }).size, 0);
  assert.equal(detectScripts(null).size, 0);
});

test('fontRequests adds Noto faces for the timeline languages and content, with script samples', () => {
  const reqs = fontRequests({ language: 'ta-IN', board_language: 'en-IN', scenes: [] });
  const tamil = reqs.filter((r) => r.family === 'Noto Sans Tamil');
  assert.deepEqual(tamil.map((r) => r.weight), [400, 700]);
  assert.ok(SCRIPT_FONTS.tamil.re.test(tamil[0].sample), 'sample text triggers the Tamil unicode-range face');
  assert.ok(/[A-Za-z]/.test(tamil[0].sample) && /Ā/.test(tamil[0].sample), 'and the latin/latin-ext faces');
  assert.equal(fontRequests({ language: 'en-IN', scenes: [] }).length, 10);
});

test('preloadFonts loads every face, waits for readiness and reports missing families', async () => {
  const calls = [];
  let readyAwaited = false;
  const fontSet = {
    load: async (font, text) => {
      calls.push([font, text]);
      return font.includes('Outfit') ? [] : [{}];
    },
    get ready() {
      readyAwaited = true;
      return Promise.resolve();
    },
  };
  const res = await preloadFonts(/** @type {any} */ (fontSet), fontRequests({ language: 'en-IN' }));
  assert.equal(calls.length, 10);
  assert.ok(readyAwaited);
  assert.deepEqual(res.missing, ['Outfit 300', 'Outfit 400', 'Outfit 700', 'Outfit 900']);
  assert.equal(res.loaded, 6);
  await assert.rejects(
    preloadFonts(/** @type {any} */ ({ load: async () => Promise.reject(new Error('network')) }), fontRequests({})),
    /network/,
  );
  const none = await preloadFonts(null, fontRequests({}));
  assert.equal(none.missing.length, 10);
});
