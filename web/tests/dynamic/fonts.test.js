// @ts-check
// The Dynamic stage's type (web/js/dynamic/fonts.js; DYNAMIC_VIDEO_DESIGN §4.6, §7.3; B2 plan §1.3, §2 "Fonts"):
// the stacks and the render page's font requests from presentation.fonts and the generated face table
// (web/js/dynamic/faces.gen.js, from aadhi/compose/dynamic/faces.py).
//
// No look changes in B2b: a B1-form presentation.fonts (B1 and B2a timelines) gives exactly what the B1 stage gave
// (oracle: the B1 stage's own fontStacks, copied below verbatim from stage.js at c3a1e9b, and render/fonts.js
// fontRequests), and the B2 form the B2b resolver writes (aadhi/compose/dynamic/faces.py resolver_fonts: B1's faces)
// gives the same stacks and requests, string for string. The B2 form draws and loads any vendored face it names.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';

import { FACES } from '../../js/dynamic/faces.gen.js';
import { B1_DISPLAY_FACES, fontStacks, indicFamilies, isB2Fonts, stageFontRequests, stageRoles } from '../../js/dynamic/fonts.js';
import { fontStacks as stageFontStacks } from '../../js/dynamic/stage.js';
import { BASE_FONTS, LATIN_SAMPLE, SCRIPT_FONTS, fontRequests, scriptForLanguage } from '../../js/render/fonts.js';
import { dynamicTimeline } from './_fixture.js';

const WEB = new URL('../../', import.meta.url);
const read = (/** @type {string} */ rel) => readFileSync(new URL(rel, WEB), 'utf8');

// --- the B1 stage's own code (stage.js at c3a1e9b), the oracle of the B1 form ------------------------------------
const B1_DISPLAY = Object.freeze(/** @type {Record<string, string>} */ ({ outfit: 'Outfit', inter: 'Inter' }));
const B1_INDIC = Object.freeze(['Noto Sans Tamil', 'Noto Sans Devanagari', 'Noto Sans Telugu', 'Noto Sans Kannada', 'Noto Sans Malayalam']);
/** @param {any} presentation @param {string} [language] */
function b1FontStacks(presentation, language = '') {
  const fonts = (presentation && presentation.fonts) || {};
  const display = B1_DISPLAY[String(fonts.display || 'outfit')] || 'Outfit';
  const script = scriptForLanguage(language);
  const own = script ? /** @type {Record<string, {family: string}>} */ (SCRIPT_FONTS)[script].family : null;
  const indic = (own ? [own, ...B1_INDIC.filter((f) => f !== own)] : B1_INDIC).map((f) => `'${f}'`).join(', ');
  return {
    display: `'${display}', 'Inter', ${indic}, sans-serif`,
    body: `'Inter', ${indic}, sans-serif`,
    mono: "'JetBrains Mono', ui-monospace, monospace",
  };
}

const LANGUAGES = ['', 'en-IN', 'ta-IN', 'hi-IN', 'mr', 'te-IN', 'kn', 'ml-IN', 'sa-IN', 'TA_in', 'xx'];
/** Display ids a stored B1-form timeline may carry (any catalogue id; the B1 stage drew two of them). */
const B1_IDS = ['outfit', 'inter', 'poppins', 'lexend', 'montserrat', 'jetbrains_mono', 'noto_sans_tamil', 'anek', 'nope', undefined];

/** faces.py resolver_fonts: what the B2b resolver stores for a presentation's display_font. */
const resolverFonts = (/** @type {string | undefined} */ display) => ({
  display: display === 'inter' ? 'inter' : 'outfit', body: 'inter', mono: 'jetbrains_mono', indic: 'noto_sans',
});

test('B1 form: the stacks are the B1 stage\'s, for every display id and language (no look changes)', () => {
  for (const display of B1_IDS) {
    for (const body of [undefined, 'inter', 'atkinson_hyperlegible_next']) {
      const presentation = { fonts: { display, body, mono: 'jetbrains_mono' } };
      for (const lang of LANGUAGES) assert.deepEqual(fontStacks(presentation, lang), b1FontStacks(presentation, lang), `${display} ${body} ${lang}`);
    }
  }
  for (const p of [{}, null, undefined, { fonts: null }, { fonts: {} }, { fonts: { indic: 7 } }]) assert.deepEqual(fontStacks(p), b1FontStacks(p));
  assert.equal(stageFontStacks, fontStacks, 'stage.js re-exports the one implementation');
});

test('B2 form as the B2b resolver writes it: the same stacks as the B1 stage drew for that presentation', () => {
  for (const display of B1_IDS) {
    const b1 = { fonts: { display, body: 'inter', mono: 'jetbrains_mono' } };
    const b2 = { fonts: resolverFonts(display) };
    assert.ok(isB2Fonts(b2.fonts) && !isB2Fonts(b1.fonts));
    for (const lang of LANGUAGES) assert.deepEqual(fontStacks(b2, lang), b1FontStacks(b1, lang), `${display} ${lang}`);
  }
});

test('B2 form: each role drawn as named when vendored for it, else its default; ids never reach CSS as text', () => {
  const p = { fonts: { display: 'poppins', body: 'atkinson_hyperlegible_next', mono: 'jetbrains_mono', indic: 'anek' } };
  assert.deepEqual(stageRoles(p), { display: 'poppins', body: 'atkinson_hyperlegible_next', mono: 'jetbrains_mono', indic: 'anek' });
  const s = fontStacks(p, 'hi-IN');
  assert.equal(s.display, "'Poppins', 'Atkinson Hyperlegible Next', 'Anek Devanagari', 'Anek Tamil', 'Anek Telugu', 'Anek Kannada', 'Anek Malayalam', sans-serif");
  assert.equal(s.body, "'Atkinson Hyperlegible Next', 'Anek Devanagari', 'Anek Tamil', 'Anek Telugu', 'Anek Kannada', 'Anek Malayalam', sans-serif");
  assert.equal(s.mono, "'JetBrains Mono', ui-monospace, monospace");
  assert.match(fontStacks({ fonts: { display: 'lexend', indic: 'noto_sans' } }).display, /^'Lexend', 'Inter', 'Noto Sans Tamil'/);
  assert.match(fontStacks({ fonts: { display: 'inter', indic: 'noto_sans' } }).display, /^'Inter', 'Inter', /, 'a body face may be the display face (B1 drew inter)');
  // not vendored (W2), the wrong role, unknown, or an inherited property: the role's default
  for (const bad of ['montserrat', 'jetbrains_mono', 'noto_sans_tamil', 'anek_tamil', 'nope', 'constructor', '__proto__', 'toString', '', 7, null]) {
    const r = stageRoles({ fonts: { display: bad, body: bad, mono: bad, indic: typeof bad === 'string' ? bad : 'noto_sans' } });
    assert.deepEqual(r, FACES.defaults, String(bad));
    const css = Object.values(fontStacks({ fonts: { display: bad, body: bad, mono: bad, indic: 'x' } })).join(' ');
    assert.doesNotMatch(css, /function|object|undefined|null|nope|constructor/i, String(bad));
  }
  // the B1 form no longer reads an inherited property of the display table either (B1 wrote a function's text)
  for (const id of ['constructor', '__proto__', 'hasOwnProperty']) assert.match(fontStacks({ fonts: { display: id } }).display, /^'Outfit', 'Inter', 'Noto Sans Tamil'/);
});

test('the Indic families come in the lecture\'s own script first, every family once', () => {
  for (const group of Object.keys(FACES.indic)) {
    assert.deepEqual(indicFamilies(group), FACES.scripts.map((/** @type {string} */ s) => FACES.indic[group][s]));
    for (const [prefix, script] of Object.entries(FACES.languages)) {
      const order = indicFamilies(group, `${prefix}-IN`);
      assert.equal(order[0], FACES.indic[group][script]);
      assert.equal(new Set(order).size, FACES.scripts.length);
    }
  }
});

/** Timelines that exercise the requests: languages, Indic content, both formats. */
function timelines() {
  const out = [];
  for (const [language, board] of [['en-IN', 'en-IN'], ['ta-IN', 'ta-IN'], ['en-IN', 'hi-IN'], ['mr-IN', 'te-IN'], ['xx', undefined]]) {
    for (const text of ['Ohm\'s law', 'மின்னழுத்தம் and ವಿದ್ಯುತ್', 'മലയാളം हिन्दी తెలుగు']) {
      for (const format of /** @type {const} */ (['landscape', 'portrait'])) {
        const tl = /** @type {any} */ (dynamicTimeline(format));
        tl.language = language;
        tl.board_language = board;
        tl.scenes[0].board[1].text = text;
        out.push(tl);
      }
    }
  }
  return out;
}

test('B1 form: the render page loads B1\'s requests exactly (render/fonts.js fontRequests)', () => {
  for (const tl of timelines()) {
    for (const display of B1_IDS) {
      tl.presentation.fonts = { display, body: 'inter', mono: 'jetbrains_mono' };
      assert.deepEqual(stageFontRequests(tl), fontRequests(tl));
    }
  }
  assert.deepEqual(stageFontRequests({}), fontRequests({}));
  assert.deepEqual(stageFontRequests(null), fontRequests(null));
});

test('B2 form as the B2b resolver writes it: the same requests as B1 when display is Outfit (B1\'s faces)', () => {
  for (const tl of timelines()) {
    const b1 = fontRequests(tl);
    tl.presentation.fonts = resolverFonts('outfit');
    assert.deepEqual(stageFontRequests(tl), b1);
    // display Inter: the families the stacks hold (Inter, JetBrains Mono, the Indic ones), each with B1's weights;
    // Outfit is in no stack then, so it is not loaded
    tl.presentation.fonts = resolverFonts('inter');
    assert.deepEqual(stageFontRequests(tl), b1.filter((r) => r.family !== 'Outfit'));
  }
});

test('B2 form: every requested face is the family a stack holds, at the weights the table requests', () => {
  const tl = /** @type {any} */ (dynamicTimeline());
  tl.language = 'ta-IN';
  tl.scenes[0].board[1].text = 'हिन्दी';
  tl.presentation.fonts = { display: 'poppins', body: 'atkinson_hyperlegible_next', mono: 'jetbrains_mono', indic: 'anek' };
  const reqs = stageFontRequests(tl);
  assert.deepEqual(reqs.map((r) => `${r.family} ${r.weight}`), [
    'Atkinson Hyperlegible Next 400', 'Atkinson Hyperlegible Next 600', 'Atkinson Hyperlegible Next 700',
    'Poppins 400', 'Poppins 600', 'Poppins 700', 'Poppins 900', 'JetBrains Mono 400', 'JetBrains Mono 700',
    'Anek Devanagari 400', 'Anek Devanagari 700', 'Anek Tamil 400', 'Anek Tamil 700',
  ]);
  assert.ok(reqs.slice(0, 9).every((r) => r.sample === LATIN_SAMPLE));
  assert.equal(reqs[9].sample, `${SCRIPT_FONTS.devanagari.sample}${LATIN_SAMPLE}`);
  const stacks = Object.values(fontStacks(tl.presentation, tl.board_language)).join(', ');
  for (const r of reqs) assert.ok(stacks.includes(`'${r.family}'`), `${r.family} is in a stack`);
});

test('the face table agrees with the stage\'s B1 constants and render/fonts.js', () => {
  assert.deepEqual(FACES.b1_display, Object.keys(B1_DISPLAY_FACES));
  assert.deepEqual(Object.keys(B1_DISPLAY_FACES), Object.keys(B1_DISPLAY));
  for (const [id, name] of Object.entries(B1_DISPLAY_FACES)) assert.equal(FACES.families[id].name, name);
  assert.deepEqual(FACES.scripts, Object.keys(SCRIPT_FONTS), 'the Indic scripts, in the stacks\' default order');
  for (const s of FACES.scripts) assert.equal(FACES.families[FACES.indic.noto_sans[s]].name, SCRIPT_FONTS[s].family);
  for (const [prefix, script] of Object.entries(FACES.languages)) assert.equal(scriptForLanguage(prefix), script, prefix);
  for (const prefix of ['en', 'sa', 'ne', 'bn', '']) assert.equal(scriptForLanguage(prefix), FACES.languages[prefix] || null, prefix);
  for (const b of BASE_FONTS) {
    const fam = Object.values(FACES.families).find((f) => f.name === b.family);
    assert.ok(fam && fam.css === 'classic', b.family);
    assert.deepEqual(fam.requests, b.weights, `${b.family}: the table requests B1's weights`);
  }
  for (const [group, perScript] of Object.entries(FACES.indic)) {
    assert.deepEqual(Object.keys(perScript).sort(), [...FACES.scripts].sort(), group); // keys sorted; FACES.scripts orders them
    for (const id of Object.values(perScript)) assert.ok(FACES.families[id].vendored && FACES.families[id].role === 'indic', id);
  }
  for (const [role, id] of Object.entries(FACES.defaults)) {
    if (role === 'indic') assert.ok(Object.prototype.hasOwnProperty.call(FACES.indic, id));
    else assert.ok(FACES.families[id].vendored && FACES.families[id].role === role, role);
  }
  assert.ok(Object.isFrozen(FACES) && Object.isFrozen(FACES.families.poppins) && Object.isFrozen(FACES.faces[0].weight));
});

test('every face of a requestable family is declared: dynamic-fonts.css and the vendored fonts.css', (t) => {
  const css = read('css/dynamic-fonts.css');
  const declared = new Set(css.split('\n').filter((l) => l.startsWith('@font-face')));
  let dynamic = 0;
  for (const f of FACES.faces) {
    const fam = FACES.families[f.family];
    if (fam.css !== 'dynamic') continue;
    dynamic += 1;
    const weight = f.weight[0] === f.weight[1] ? `${f.weight[0]}` : `${f.weight[0]} ${f.weight[1]}`;
    const line = `@font-face{font-family:'${fam.name}';font-style:normal;font-weight:${weight};font-display:block;` +
      `src:url('../vendor/${f.file}') format('woff2');unicode-range:${f.unicode_range.join(',')};}`;
    assert.ok(declared.has(line), line);
  }
  assert.equal(declared.size, dynamic, 'dynamic-fonts.css declares the table\'s B2b faces and nothing else');
  for (const name of ['Inter', 'Outfit', 'JetBrains Mono', 'Noto Sans']) assert.ok(!css.includes(`font-family:'${name}`), `${name} stays in fonts.css`);
  if (!existsSync(new URL('vendor/fonts.css', WEB))) {
    t.diagnostic('web/vendor is not vendored (npm run vendor): the classic sheet and the files were not checked');
    return;
  }
  const classic = read('vendor/fonts.css');
  for (const f of FACES.faces) {
    const fam = FACES.families[f.family];
    if (fam.css === 'classic') {
      const line = `@font-face{font-family:'${fam.name}';font-style:normal;font-weight:${f.weight[0]};font-display:block;` +
        `src:url('./${f.file}') format('woff2');unicode-range:${f.unicode_range.join(',')};}`;
      assert.ok(classic.includes(line), line);
    }
    assert.ok(existsSync(new URL(`vendor/${f.file}`, WEB)), `web/vendor/${f.file}`);
  }
  for (const fam of Object.values(FACES.families)) {
    if (fam.css === 'dynamic') assert.match(read(`vendor/${fam.dir}/LICENSE`), /SIL OPEN FONT LICENSE Version 1\.1/, fam.name);
  }
});

test('the pages that host the stage link dynamic-fonts.css after dynamic.css; the classic render page does not', () => {
  for (const page of ['render-dynamic.html', 'index.html', 'watch.html']) {
    const html = read(page);
    const a = html.indexOf('href="/web/css/dynamic.css"');
    const b = html.indexOf('<link rel="stylesheet" href="/web/css/dynamic-fonts.css" />');
    assert.ok(a > 0 && b > a, page);
  }
  assert.ok(!read('render.html').includes('dynamic-fonts.css'), 'the classic render page is untouched');
});
