// @ts-check
/**
 * Python/JS colour parity vectors (DYNAMIC_VIDEO_DESIGN §4.7; B2 plan §1.2): runs the stage's own colour code —
 * web/js/motion/color.js and the colour helpers of web/js/dynamic (core.js desaturate/makeRoles/inkOn,
 * colour_block.js blockColors and colors(), mesh_drift.js colors(), stage.js blockInk2, board_generic.js's scrim and
 * scrimmedBg) — on a fixed, deterministic set of inputs and writes tests/fixtures/colour_vectors.json.
 *
 *   node scripts/gen_colour_vectors.mjs           write the file
 *   node scripts/gen_colour_vectors.mjs --check   exit 1 when the committed file is not what the JS produces
 *
 * Two tests read the committed file, each needing only its own runtime: tests/compose/dynamic/test_colour_math.py
 * (aadhi/compose/dynamic/colour_math.py and themes.py reproduce every vector: hex equal, floats within 1e-9) and
 * web/tests/motion/colourVectors.test.js (this module still produces the committed file byte for byte). A change to
 * the colour code on either side therefore fails CI until both agree and the file is regenerated.
 *
 * The stage section's inputs are the B1 structure tokens of the four R1 families (palette slots 0-2) and the
 * content inks; the Python test checks that every released (family, slot) of aadhi/compose/dynamic/themes.py is
 * among them, and that FAMILY_WINDOWS below equals the catalogue's accent windows, so a catalogue change asks for a
 * regeneration here. Output: keys sorted at every level, each section sorted, one record per line.
 */

import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { JSDOM } from 'jsdom';

import {
  composite, contrastRatio, fromOklch, gamutMapOklch, mixOklch, mixOklchValues, relativeLuminance, toHex, toOklch,
} from '../web/js/motion/color.js';
import { desaturate, makeRoles, ZONES } from '../web/js/dynamic/core.js';
import colourBlock, { blockColors } from '../web/js/dynamic/backgrounds/colour_block.js';
import meshDrift from '../web/js/dynamic/backgrounds/mesh_drift.js';
import { blockInk2 } from '../web/js/dynamic/stage.js';
import { SCRIM_ALPHA_HEX, scrimmedBg } from '../web/js/dynamic/templates/board_generic.js';

export const OUT = fileURLToPath(new URL('../tests/fixtures/colour_vectors.json', import.meta.url));
export const VERSION = 1;

/** Accent windows of the twelve §4.7 families (themes.py FAMILIES; the Python test checks they are equal). */
export const FAMILY_WINDOWS = Object.freeze({
  midnight_indigo: [[40, 110]], deep_teal: [[0, 70]], aubergine: [[140, 210]], charcoal_math: [[200, 280]],
  forest_night: [[20, 90]], cosmic: [[30, 70], [170, 210]], paper_ink: [[0, 70]], blueprint: [[230, 290], [30, 70]],
  mint_studio: [[270, 340]], sunrise: [[260, 330]], chalkboard: [[60, 130]], bold_blocks: [[0, 300]],
});
/** struct.accent L/C per mode (themes.py ACCENT_LC) and the validator's nudge range (+-0.06 L). */
const ACCENT_LC = Object.freeze({ dark: [0.77, 0.16], light: [0.56, 0.17] });
const NUDGES = [-0.06, 0, 0.06];

/** Content inks (themes.py CONTENT, B1 values). */
const CONTENT = Object.freeze({
  dark: { ink: '#F3F5F9', ink2: '#B9BEC7', 'entity:0': '#0FE3FE', 'entity:1': '#C4A4FE', 'entity:2': '#FEACE3', warn: '#F8A87F', ok: '#5DD8AA', mark: '#F2D935', focus: '#F2D935' },
  light: { ink: '#141822', ink2: '#484D58', 'entity:0': '#0267C7', 'entity:1': '#80369E', 'entity:2': '#870C62', warn: '#9F4503', ok: '#016F50', mark: '#F2D935', focus: '#141822' },
});

/** B1 structure tokens of the R1 families, palette slots 0-2 (aadhi/compose/presentation.py, pinned in themes). */
const R1_TOKENS = Object.freeze({
  'midnight_indigo/0': ['dark', '#FF936D', '#10132C', '#181D37', '#26120C', '#252A3F'],
  'midnight_indigo/1': ['dark', '#BBBC22', '#10132C', '#181D37', '#191A06', '#252A3F'],
  'midnight_indigo/2': ['dark', '#EFA30F', '#10132C', '#181D37', '#221605', '#252A3F'],
  'deep_teal/0': ['dark', '#FF89B0', '#001B1C', '#002527', '#251118', '#0E3133'],
  'deep_teal/1': ['dark', '#F49F1E', '#001B1C', '#002527', '#221505', '#0E3133'],
  'deep_teal/2': ['dark', '#FF9277', '#001B1C', '#002527', '#26120D', '#0E3133'],
  'paper_ink/0': ['light', '#BE3D70', '#FDF2DD', '#F3E8D3', '#FFEAEF', '#FFFDFA'],
  'paper_ink/1': ['light', '#A16600', '#FDF2DD', '#F3E8D3', '#FDEDDC', '#FFFDFA'],
  'paper_ink/2': ['light', '#C44323', '#FDF2DD', '#F3E8D3', '#FFEBE6', '#FFFDFA'],
  'mint_studio/0': ['light', '#4F69D7', '#E4F9F1', '#DAEFE7', '#EAF0FF', '#FBFEFD'],
  'mint_studio/1': ['light', '#B24393', '#E4F9F1', '#DAEFE7', '#FEE9F6', '#FBFEFD'],
  'mint_studio/2': ['light', '#8C54C3', '#E4F9F1', '#DAEFE7', '#F4ECFF', '#FBFEFD'],
});
const TONES = ['bg', 'bg-alt', 'bg-tint'];

/** A small deterministic generator (an LCG; Math.random is banned in the stage and not wanted here either). */
function lcg(seed) {
  let s = seed >>> 0;
  return () => {
    s = (Math.imul(s, 1664525) + 1013904223) >>> 0;
    return s / 4294967296;
  };
}
const hex2 = (/** @type {number} */ n) => n.toString(16).padStart(2, '0');

/** Every hex input of the primitive sections: a 6-level grid, seeded random colours, the palette, alphas. */
function hexInputs() {
  const out = new Set();
  const levels = [0x00, 0x33, 0x66, 0x99, 0xcc, 0xff];
  for (const r of levels) for (const g of levels) for (const b of levels) out.add(`#${hex2(r)}${hex2(g)}${hex2(b)}`);
  const rand = lcg(20261013);
  for (let i = 0; i < 96; i++) out.add(`#${hex2(Math.floor(rand() * 256))}${hex2(Math.floor(rand() * 256))}${hex2(Math.floor(rand() * 256))}`);
  for (const p of Object.values(CONTENT)) for (const v of Object.values(p)) out.add(v);
  for (const t of Object.values(R1_TOKENS)) for (const v of t.slice(1)) out.add(v);
  for (const v of ['#FFFFFF', '#14161C', '#0B0D14', '#808080', '#fff', '#0008', '#ff000080', '#0000ff00', '#F2D93559', '#10132Cc7', '#FDF2DDC7']) out.add(v);
  return [...out];
}

/** OKLCH inputs of from_oklch / gamut_map: accents of every family window at 5° steps at both modes' L/C (and the
 * nudged L), an out-of-gamut grid, and the edges (L <= 0, L >= 1, negative chroma, hues outside 0..360, alpha). */
function oklchInputs() {
  /** @type {{l: number, c: number, h: number, a?: number}[]} */
  const out = [];
  for (const windows of Object.values(FAMILY_WINDOWS)) {
    for (const [lo, hi] of windows) {
      for (let h = lo; h <= hi; h += 5) {
        for (const [l, c] of Object.values(ACCENT_LC)) for (const d of NUDGES) out.push({ l: Math.round((l + d) * 100) / 100, c, h: h % 360 });
      }
    }
  }
  for (let li = 1; li <= 19; li += 2) {
    for (let h = 0; h < 360; h += 15) for (const c of [0.15, 0.25, 0.37]) out.push({ l: li / 20, c, h });
  }
  for (const [l, c, h] of [[0.2, 0.05, 275], [0.24, 0.045, 200], [0.965, 0.03, 85], [0.995, 0.004, 170], [0.29, 0.04, 275], [0.97, 0.006, 265], [0.21, 0.02, 265]]) out.push({ l, c, h });
  for (const v of [{ l: 0, c: 0.1, h: 30 }, { l: -0.1, c: 0.1, h: 30 }, { l: 1, c: 0.1, h: 30 }, { l: 1.2, c: 0.3, h: 30 }, { l: 0.5, c: -0.1, h: 30 }, { l: 0.5, c: 0, h: 0 },
    { l: 0.6, c: 0.2, h: -30 }, { l: 0.6, c: 0.2, h: 725 }, { l: 0.6, c: 0.2, h: 359.9999 }, { l: 0.62796, c: 0.25768, h: 29.234 }, { l: 0.9, c: 0.4, h: 140 },
    { l: 0.5, c: 0.1, h: 200, a: 0.5 }, { l: 0.5, c: 0.1, h: 200, a: 0 }, { l: 0.5, c: 0.1, h: 200, a: 2 }, { l: 0.5, c: 0.1, h: 200, a: -1 }, { l: 0.5, c: 0.5, h: 200, a: 0.25 }]) out.push(v);
  return out;
}

/** Colours mixed in the mix section: palette anchors, greys and translucent colours. */
const MIX_COLOURS = ['#F3F5F9', '#141822', '#0FE3FE', '#870C62', '#F2D935', '#FF936D', '#10132C', '#FDF2DD', '#4F69D7', '#C44323',
  '#000000', '#FFFFFF', '#808080', '#ff000080', '#0000ff00', 'transparent', '#00ff0040'];
const MIX_P = [0, 0.05, 0.07, 0.1, 0.2, 0.25, 0.35, 0.38, 0.5, 0.85, 1, -0.5, 1.5];
/** Pairs mixed at every p besides the quarter above, which never pairs two greys: grey to grey (both ends borrow no
 * hue, so the result's hue is 0), opaque and translucent. */
const MIX_PAIRS = [['#000000', '#808080'], ['#808080', '#FFFFFF'], ['#FFFFFF', '#000000'], ['#333333', '#CCCCCC'],
  ['#80808080', '#000000'], ['#808080', '#FFFFFF00']];
/** OKLCH pairs whose hue difference sits on the shorter-arc boundary (|d| just below, at and just above 180°, both
 * signs, and across 0°), mixed with mixOklchValues at every p. */
const MIX_HUE_PAIRS = [[10, 189.5], [10, 190], [10, 190.5], [190.5, 10], [190, 10], [189.5, 10], [350, 170.25],
  [350, 169.75], [0.25, 180.5]];

/** The stage's colours for one token set, from the JS itself (jsdom provides the document the backgrounds build). */
function stageVector(name, [mode, accent, bg, alt, tint, surface]) {
  const content = CONTENT[/** @type {'dark' | 'light'} */ (mode)];
  const presentation = /** @type {any} */ ({ content });
  const tokens = { 'struct.accent': accent, bg, 'bg-alt': alt, 'bg-tint': tint, surface };
  const roles = makeRoles(presentation, /** @type {any} */ ({ tokens }));
  const noSurface = makeRoles(presentation, /** @type {any} */ ({ tokens: { ...tokens, surface: undefined } }));
  const { base, highlight } = blockColors(roles);
  const block = [base, highlight];
  const ink = roles.inkOn(block);
  const ctx = (/** @type {string} */ tone) => /** @type {any} */ ({ roles, zones: ZONES.landscape, params: { tone }, seed: 7, mode: 'render', pending: [] });
  /** @type {Record<string, any>} */
  const mesh = {};
  const scrim = `${roles.tokens.bg || '#000000'}${SCRIM_ALPHA_HEX}`;
  for (const tone of TONES) {
    const colors = meshDrift.build(ctx(tone)).colors();
    const worst = colors.reduce((a, b) => (contrastRatio(content.ink, b) < contrastRatio(content.ink, a) ? b : a));
    /** @type {Record<string, string>} */
    const scrimmed = {};
    for (const role of ['ink', 'ink2', 'entity:0', 'warn']) scrimmed[role] = scrimmedBg(scrim, { bg: worst, bgs: colors }, content[role]);
    mesh[tone] = { colors, desaturated: colors.map((c) => desaturate(c, 0.3)), scrimmed };
  }
  return {
    name, mode: roles.mode, content, tokens,
    block: { base, highlight, ink, ink2: blockInk2(ink, block), colors: colourBlock.build(ctx('bg')).colors() },
    ink_on: { ink: roles.inkOn(bg), ink_surface: roles.inkOn([bg, surface]) },
    surface_fallback: noSurface.tokens.surface, scrim, mesh,
  };
}

/** Run `fn` with a jsdom document as the global DOM, restoring whatever was there. */
function withDom(fn) {
  const dom = new JSDOM('<!doctype html><html><body></body></html>');
  const g = /** @type {any} */ (globalThis);
  const saved = { window: g.window, document: g.document, Node: g.Node };
  g.window = dom.window;
  g.document = dom.window.document;
  g.Node = dom.window.Node;
  try {
    return fn();
  } finally {
    Object.assign(g, saved);
    dom.window.close();
  }
}

/** Every vector, as plain data. */
export function buildVectors() {
  const hexes = hexInputs();
  const hexOklch = hexes.map((hex) => {
    const v = toOklch(hex);
    return { hex, l: v.l, c: v.c, h: v.h, a: v.a, back: fromOklch(v), to_hex: toHex(hex), luminance: relativeLuminance(hex) };
  });
  const fromOk = oklchInputs().map((v) => {
    const rgb = gamutMapOklch(v.l, Math.max(0, v.c), (((v.h % 360) + 360) % 360));
    return { ...v, hex: fromOklch(v), rgb };
  });
  const pairs = [];
  for (let i = 0; i < MIX_COLOURS.length; i++) {
    for (let j = 0; j < MIX_COLOURS.length; j++) {
      if (i === j || (i * 7 + j * 3) % 4 !== 0) continue; // a deterministic quarter of the ordered pairs
      pairs.push([MIX_COLOURS[i], MIX_COLOURS[j]]);
    }
  }
  const mixes = [];
  for (const [a, b] of [...pairs, ...MIX_PAIRS]) {
    for (const p of MIX_P) mixes.push({ a, b, p, hex: mixOklch(a, b, p), value: mixOklchValues(toOklch(a), toOklch(b), p) });
  }
  const mixValues = [];
  for (const [ha, hb] of MIX_HUE_PAIRS) {
    const a = { l: 0.6, c: 0.12, h: ha, a: 1 };
    const b = { l: 0.7, c: 0.08, h: hb, a: 1 };
    for (const p of MIX_P) mixValues.push({ a, b, p, value: mixOklchValues(a, b, p) });
  }
  const rand = lcg(7);
  const pick = () => hexes[Math.floor(rand() * hexes.length)];
  const contrast = [];
  const composites = [];
  for (let i = 0; i < 300; i++) {
    const fg = pick();
    const bg = pick();
    contrast.push({ fg, bg, ratio: contrastRatio(fg, bg) });
    const top = `${fg.length === 7 ? fg : toHex(fg).slice(0, 7)}${hex2(Math.floor(rand() * 256))}`;
    composites.push({ top, bottom: bg, hex: composite(top, bg) });
  }
  const desat = [];
  for (const hex of hexes.slice(0, 120)) for (const amount of [-1, 0, 0.1, 0.3, 1, 1.5]) desat.push({ hex, amount, out: desaturate(hex, amount) });
  const stage = withDom(() => Object.entries(R1_TOKENS).map(([name, t]) => stageVector(name, t)));
  return {
    version: VERSION,
    generator: 'scripts/gen_colour_vectors.mjs',
    families: FAMILY_WINDOWS,
    accent_lc: ACCENT_LC,
    hex_oklch: hexOklch,
    from_oklch: fromOk,
    mix: mixes,
    mix_values: mixValues,
    contrast,
    composite: composites,
    desaturate: desat,
    stage,
  };
}

/** @param {any} v @returns {any} */
function sortKeys(v) {
  if (Array.isArray(v)) return v.map(sortKeys);
  if (v && typeof v === 'object') return Object.fromEntries(Object.keys(v).sort().map((k) => [k, sortKeys(v[k])]));
  return v;
}

/** Canonical text: keys sorted, each section's records sorted by their own JSON, one record per line. */
export function serialise(vectors) {
  const lines = ['{'];
  const keys = Object.keys(vectors).sort();
  keys.forEach((key, i) => {
    const v = sortKeys(vectors[key]);
    const comma = i < keys.length - 1 ? ',' : '';
    if (Array.isArray(v)) {
      const rows = v.map((r) => JSON.stringify(r)).sort();
      lines.push(`  ${JSON.stringify(key)}: [`);
      rows.forEach((r, j) => lines.push(`    ${r}${j < rows.length - 1 ? ',' : ''}`));
      lines.push(`  ]${comma}`);
    } else {
      lines.push(`  ${JSON.stringify(key)}: ${JSON.stringify(v)}${comma}`);
    }
  });
  lines.push('}');
  return `${lines.join('\n')}\n`;
}

function main() {
  const text = serialise(buildVectors());
  if (process.argv.includes('--check')) {
    let current = '';
    try {
      current = readFileSync(OUT, 'utf8');
    } catch {
      current = '';
    }
    if (current !== text) {
      console.error(`${OUT} is stale: run node scripts/gen_colour_vectors.mjs`);
      process.exit(1);
    }
    console.log(`${OUT} is current`);
    return;
  }
  writeFileSync(OUT, text);
  console.log(`wrote ${OUT}`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) main();
