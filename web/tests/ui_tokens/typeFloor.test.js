// 12 px type floor for the Studio: no font size in studio.css (or an absolute one in base.css) below
// 0.75rem / 12px. Sizes relative to their parent (em, %) below 1 need a floor: `max(var(--fs-min), 0.85em)`.
// Player stage typography (player.css, panels.css) is out of scope: it scales with the 1920 x 1080 stage.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { readCss, parseRules, customProperties, resolveVars } from './_css.js';

const FLOOR_PX = 12;
const ROOT_PX = 16;

const tokens = customProperties(parseRules(readCss('tokens.css')).find((r) => r.prelude === ':root').body);

/**
 * Smallest size a font-size value can render at, in px (relative sizes: `{ relative: factor }`).
 * @returns {{ px: number } | { relative: number } | { keyword: string }}
 */
export function smallestSize(raw) {
  const value = resolveVars(raw, tokens).trim().replace(/\s*!important$/, '');
  const fn = value.match(/^(max|min|clamp)\((.*)\)$/);
  if (fn) {
    const args = splitArgs(fn[2]).map(smallestSize);
    const abs = args.filter((a) => 'px' in a).map((a) => /** @type {any} */ (a).px);
    if (fn[1] === 'max') return abs.length ? { px: Math.max(...abs) } : args[0];
    if (fn[1] === 'clamp') return 'px' in args[0] ? args[0] : { relative: 0 };
    return abs.length === args.length ? { px: Math.min(...abs) } : { relative: 0 }; // min() with a relative part
  }
  let m = value.match(/^([0-9.]+)rem$/);
  if (m) return { px: Number(m[1]) * ROOT_PX };
  m = value.match(/^([0-9.]+)px$/);
  if (m) return { px: Number(m[1]) };
  m = value.match(/^([0-9.]+)em$/);
  if (m) return { relative: Number(m[1]) };
  m = value.match(/^([0-9.]+)%$/);
  if (m) return { relative: Number(m[1]) / 100 };
  m = value.match(/^calc\(/);
  if (m) return { relative: 0 };
  return { keyword: value };
}

function splitArgs(s) {
  const out = [];
  let depth = 0;
  let cur = '';
  for (const ch of s) {
    if (ch === '(') depth++;
    if (ch === ')') depth--;
    if (ch === ',' && depth === 0) {
      out.push(cur);
      cur = '';
    } else cur += ch;
  }
  out.push(cur);
  return out.map((x) => x.trim());
}

/** font-size declarations (and the size inside `font:` shorthands) of a stylesheet, with line numbers. */
function fontSizes(name) {
  const lines = readCss(name).split('\n');
  const found = [];
  lines.forEach((line, i) => {
    for (const m of line.matchAll(/(?:^|[\s;{])font-size\s*:\s*([^;}]+)/g)) found.push({ line: i + 1, value: m[1].trim() });
    const short = line.match(/(?:^|[\s;{])font\s*:\s*([^;}]+)/);
    if (short && !/^(inherit|initial|unset)$/.test(short[1].trim())) {
      const size = short[1].match(/(?:^|\s)([0-9.]+(?:rem|px|em|%))(?:\/|\s)/);
      if (size) found.push({ line: i + 1, value: size[1] });
    }
  });
  return found;
}

test('the floor token is 12 px', () => {
  assert.deepEqual(smallestSize('var(--fs-min)'), { px: FLOOR_PX });
});

test('smallestSize reads the forms the Studio uses', () => {
  assert.deepEqual(smallestSize('0.7rem'), { px: 11.2 });
  assert.deepEqual(smallestSize('13px'), { px: 13 });
  assert.deepEqual(smallestSize('max(var(--fs-min), 0.85em)'), { px: 12 });
  assert.deepEqual(smallestSize('clamp(1.6rem, 2.4vw, 2.2rem)'), { px: 25.6 });
  assert.deepEqual(smallestSize('0.85em'), { relative: 0.85 });
});

test('studio.css: no text below 12 px (relative sizes below 1em need a floor)', () => {
  const bad = [];
  for (const { line, value } of fontSizes('studio.css')) {
    const size = smallestSize(value);
    if ('px' in size && size.px < FLOOR_PX) bad.push(`studio.css:${line}: font-size ${value} (${size.px}px)`);
    if ('relative' in size && size.relative < 1) bad.push(`studio.css:${line}: font-size ${value} has no 12 px floor`);
    if ('keyword' in size && /^(smaller|x-small|xx-small|small)$/.test(size.keyword)) bad.push(`studio.css:${line}: font-size ${value}`);
  }
  assert.deepEqual(bad, []);
});

test('base.css: no absolute font size below 12 px', () => {
  const bad = [];
  for (const { line, value } of fontSizes('base.css')) {
    const size = smallestSize(value);
    if ('px' in size && size.px < FLOOR_PX) bad.push(`base.css:${line}: font-size ${value} (${size.px}px)`);
  }
  assert.deepEqual(bad, []);
});

test('Studio scripts set no inline font size below 12 px', () => {
  const dir = fileURLToPath(new URL('../../js/studio/', import.meta.url));
  const files = [];
  const walk = (d) => {
    for (const name of readdirSync(d)) {
      const p = join(d, name);
      if (statSync(p).isDirectory()) walk(p);
      else if (name.endsWith('.js')) files.push(p);
    }
  };
  walk(dir);
  const bad = [];
  for (const file of files) {
    const text = readFileSync(file, 'utf8');
    for (const m of text.matchAll(/font-?size['"]?\s*[:=]\s*['"`]([0-9.]+(?:px|rem))/gi)) {
      const size = smallestSize(m[1]);
      if ('px' in size && size.px < FLOOR_PX) bad.push(`${file}: ${m[0]}`);
    }
  }
  assert.deepEqual(bad, []);
});
