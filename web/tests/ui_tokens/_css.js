// Helpers for the design-token tests: a small CSS reader (rules, custom properties) and WCAG maths.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

/** Absolute path of a file under web/css. */
export const cssPath = (name) => fileURLToPath(new URL(`../../css/${name}`, import.meta.url));

/** A stylesheet's text with comments blanked out (line numbers are kept). */
export function readCss(name) {
  const text = readFileSync(cssPath(name), 'utf8');
  return text.replace(/\/\*[\s\S]*?\*\//g, (c) => c.replace(/[^\n]/g, ' '));
}

/**
 * Top-level rules of a stylesheet (nested blocks such as @media are returned with their own `rules`).
 * @returns {Array<{ prelude: string, body: string, line: number, rules?: any[] }>}
 */
export function parseRules(css, baseLine = 1) {
  const out = [];
  let depth = 0;
  let start = 0;
  let open = -1;
  for (let i = 0; i < css.length; i++) {
    const ch = css[i];
    if (ch === '{') {
      if (depth === 0) open = i;
      depth++;
    } else if (ch === '}') {
      depth--;
      if (depth === 0 && open >= 0) {
        const prelude = css.slice(start, open).trim().replace(/^[;\s]+/, '');
        const body = css.slice(open + 1, i);
        const line = baseLine + countLines(css.slice(0, open));
        const rule = { prelude, body, line };
        if (prelude.startsWith('@media') || prelude.startsWith('@supports') || prelude.startsWith('@layer')) {
          rule.rules = parseRules(body, line);
        }
        out.push(rule);
        start = i + 1;
        open = -1;
      }
    } else if (ch === ';' && depth === 0) {
      start = i + 1; // @import ... ;
    }
  }
  return out;
}

function countLines(s) {
  let n = 0;
  for (const ch of s) if (ch === '\n') n++;
  return n;
}

/** Custom properties declared in a rule body ({ '--c-bg': '#07050d', ... }). */
export function customProperties(body) {
  const vars = {};
  for (const m of body.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) vars[m[1]] = m[2].trim();
  return vars;
}

/** Resolve `var(--x)` references (with fallbacks) against a token map. */
export function resolveVars(value, vars, seen = new Set()) {
  return String(value).replace(/var\(\s*(--[\w-]+)\s*(?:,\s*([^)]+))?\)/g, (_, name, fallback) => {
    if (seen.has(name)) throw new Error(`cyclic token ${name}`);
    if (vars[name] !== undefined) return resolveVars(vars[name], vars, new Set([...seen, name]));
    if (fallback !== undefined) return resolveVars(fallback, vars, seen);
    throw new Error(`unknown token ${name}`);
  });
}

/** Parse #rgb, #rrggbb, rgb() and rgba() into [r, g, b, a]. */
export function parseColor(value) {
  const v = String(value).trim().toLowerCase();
  let m = v.match(/^#([0-9a-f]{3})$/);
  if (m) return [...m[1]].map((c) => parseInt(c + c, 16)).concat(1);
  m = v.match(/^#([0-9a-f]{6})$/);
  if (m) return [0, 2, 4].map((i) => parseInt(m[1].slice(i, i + 2), 16)).concat(1);
  m = v.match(/^rgba?\(([^)]+)\)$/);
  if (m) {
    const parts = m[1].split(/[\s,/]+/).filter(Boolean).map(Number);
    if (parts.length < 3 || parts.some((n) => Number.isNaN(n))) throw new Error(`bad colour ${value}`);
    return [parts[0], parts[1], parts[2], parts.length > 3 ? parts[3] : 1];
  }
  throw new Error(`unsupported colour ${value}`);
}

/** A (possibly translucent) colour composited over an opaque one. */
export function over(fg, bg) {
  const a = fg[3];
  return [0, 1, 2].map((i) => fg[i] * a + bg[i] * (1 - a)).concat(1);
}

/** WCAG 2 relative luminance of an opaque colour. */
export function luminance(c) {
  const [r, g, b] = c.slice(0, 3).map((v) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** WCAG 2 contrast ratio of two opaque colours. */
export function contrast(a, b) {
  const la = luminance(a);
  const lb = luminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}
