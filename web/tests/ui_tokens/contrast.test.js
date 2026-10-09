// WCAG AA contrast of the design tokens (web/css/tokens.css) for the documented text / background pairs.
// Text tokens need 4.5:1 on every Studio surface (SC 1.4.3); the focus ring needs 3:1 (SC 1.4.11).
// Translucent surfaces are composited over the page backgrounds they sit on, translucent text over the
// surface. Every theme tokens.css defines is checked: the dark :root, and a light theme when one exists
// (`@media (prefers-color-scheme: light)` or a `[data-theme="light"]` rule).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readCss, parseRules, customProperties, resolveVars, parseColor, over, contrast } from './_css.js';

/** Text colours used for real text in the Studio (each must reach AA on every surface). */
const TEXT_TOKENS = ['--c-text', '--c-text-dim', '--c-text-subtle', '--c-gold', '--c-cyan', '--c-green', '--c-red', '--c-accent-text'];
/** Non-text indicators (focus ring) need 3:1 against adjacent colours. */
const UI_TOKENS = ['--c-focus'];
/** Surfaces text sits on: [name, colour token, the opaque background it is drawn over (null = opaque)]. */
const SURFACES = [
  ['page', '--c-bg', null],
  ['raised page', '--c-bg-elev', null],
  ['glass panel', '--c-surface', '--c-bg'],
  ['glass panel on raised page', '--c-surface', '--c-bg-elev'],
  ['card', '--c-surface-soft', '--c-bg'],
  ['solid panel', '--c-surface-solid', null],
];
/** Dark text on gold (gold buttons and badges use near-black text, like the page background). */
const ON_GOLD = [['--c-bg', '--c-gold']];

/** The themes tokens.css defines: name -> resolved custom properties. */
function themes() {
  const rules = parseRules(readCss('tokens.css'));
  const root = rules.find((r) => r.prelude === ':root');
  assert.ok(root, 'tokens.css has a :root block');
  const dark = customProperties(root.body);
  const found = { dark };
  for (const r of rules) {
    if (/^:root\s*\[\s*data-theme\s*=\s*['"]?light['"]?\s*\]$|^\[\s*data-theme\s*=\s*['"]?light['"]?\s*\]$/.test(r.prelude)) {
      found.light = { ...dark, ...customProperties(r.body) };
    }
    if (r.rules && /prefers-color-scheme\s*:\s*light/.test(r.prelude)) {
      const inner = r.rules.find((x) => /^:root\b/.test(x.prelude));
      if (inner) found.light = { ...dark, ...(found.light || {}), ...customProperties(inner.body) };
    }
  }
  return found;
}

/** An opaque colour for a token, composited over `base` when translucent. */
function colour(vars, token, base = null) {
  const c = parseColor(resolveVars(vars[token] ?? `var(${token})`, vars));
  if (c[3] >= 1) return c;
  assert.ok(base, `${token} is translucent: give the background it is drawn over`);
  return over(c, base);
}

function surfaces(vars) {
  return SURFACES.map(([name, token, under]) => [name, colour(vars, token, under ? colour(vars, under) : null)]);
}

test('contrast maths matches WCAG reference values', () => {
  assert.equal(Math.round(contrast(parseColor('#000000'), parseColor('#ffffff')) * 100) / 100, 21);
  assert.equal(Math.round(contrast(parseColor('#777777'), parseColor('#ffffff')) * 100) / 100, 4.48);
  assert.deepEqual(over(parseColor('rgba(255, 255, 255, 0.5)'), parseColor('#000000')), [127.5, 127.5, 127.5, 1]);
});

for (const [theme, vars] of Object.entries(themes())) {
  test(`${theme} theme: Studio text tokens reach WCAG AA (4.5:1) on every surface`, () => {
    const failures = [];
    for (const [name, bg] of surfaces(vars)) {
      for (const token of TEXT_TOKENS) {
        assert.ok(vars[token], `${theme}: ${token} is defined`);
        const ratio = contrast(colour(vars, token, bg), bg);
        if (ratio < 4.5) failures.push(`${token} on ${name}: ${ratio.toFixed(2)}:1`);
      }
    }
    for (const [fg, bg] of ON_GOLD) {
      const back = colour(vars, bg);
      const ratio = contrast(colour(vars, fg, back), back);
      if (ratio < 4.5) failures.push(`${fg} on ${bg}: ${ratio.toFixed(2)}:1`);
    }
    assert.deepEqual(failures, []);
  });

  test(`${theme} theme: the focus ring reaches 3:1 on every surface`, () => {
    const failures = [];
    for (const [name, bg] of surfaces(vars)) {
      for (const token of UI_TOKENS) {
        const ratio = contrast(colour(vars, token, bg), bg);
        if (ratio < 3) failures.push(`${token} on ${name}: ${ratio.toFixed(2)}:1`);
      }
    }
    assert.deepEqual(failures, []);
  });
}

test('the light theme is checked when tokens.css defines one', (t) => {
  if (!themes().light) {
    t.skip('tokens.css defines only the dark theme');
    return;
  }
  assert.ok(themes().light['--c-text']);
});

test('Studio text never uses the tokens below AA (--c-text-faint, --c-purple)', () => {
  const css = readCss('studio.css').split('\n');
  const bad = [];
  css.forEach((line, i) => {
    if (/(^|[\s;{])color\s*:\s*var\(\s*--c-(text-faint|purple)\s*\)/.test(line)) bad.push(`studio.css:${i + 1}: ${line.trim()}`);
  });
  assert.deepEqual(bad, [], 'use --c-text-subtle or --c-accent-text for text');
});

test('the decorative tokens really are below AA, so the rule above matters', () => {
  const { dark } = themes();
  const bg = colour(dark, '--c-bg');
  assert.ok(contrast(colour(dark, '--c-text-faint', bg), bg) < 4.5);
  assert.ok(contrast(colour(dark, '--c-purple', bg), bg) < 4.5);
});
