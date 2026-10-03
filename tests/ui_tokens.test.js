// Phase 21: the product UI's shared design tokens (ui.css) — loaded first, only `:root` tokens and opt-in `.ui-*` classes (no
// element selectors, no `*`: nothing changes until an area adopts it), literal values only (the product UI never follows the
// lesson's video style: no `var(--st-*)`, `var(--text-gold)`, `var(--font-*)`), and the UI's own CSS files never read the
// lesson style's `--st-*` tokens. Run: node --test tests/
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const read = name => fs.readFileSync(path.join(__dirname, '..', name), 'utf8');
const ui = read('ui.css');
const page = read('index.html');
const withoutComments = css => css.replace(/\/\*[\s\S]*?\*\//g, '');

test('ui.css is loaded before every other style of the page', () => {
    const link = page.indexOf('<link rel="stylesheet" href="ui.css">');
    assert.ok(link > 0, 'the page loads ui.css');
    assert.ok(link < page.indexOf('<style'), 'before the page\'s own styles');
});

test('ui.css has only :root tokens and opt-in .ui-* classes, with literal values', () => {
    const css = withoutComments(ui);
    const selectors = [...css.matchAll(/([^{}]+)\{[^{}]*\}/g)].map(m => m[1].trim()).filter(s => !s.startsWith('@'));
    assert.ok(selectors.length > 5);
    for (const group of selectors) {
        for (const selector of group.split(',').map(s => s.trim())) {
            assert.ok(selector === ':root' || /^\.ui-[a-z0-9-]+(\[[^\]]+\])?(:[a-z-]+(\([^)]*\))?)*$/.test(selector),
                `only :root and .ui-* (got "${selector}")`);
        }
    }
    assert.ok(!/var\(--(st|cine|text-gold|font-)/.test(css), 'never the lesson style or the stage\'s tokens');
    const tokens = [...css.matchAll(/--ui-([a-z0-9-]+)\s*:/g)].map(m => m[1]);
    for (const needed of ['bg', 'surface', 'surface-2', 'text', 'muted', 'primary', 'accent', 'success', 'warning', 'danger', 'border', 'focus',
        'disabled-opacity', 'space-4', 'radius-md', 'shadow-2', 'text-title', 'text-body', 'text-caption', 'control-md']) {
        assert.ok(tokens.includes(needed), `--ui-${needed}`);
    }
});

test('contrast: every text colour of the tokens reads on every product surface (WCAG AA 4.5 : 1 and more)', () => {
    const css = withoutComments(ui);
    const value = name => {
        const m = css.match(new RegExp(`--ui-${name}:\s*([^;]+);`));
        assert.ok(m, `--ui-${name}`);
        const v = m[1].trim();
        const rgb = v.match(/^rgb\((\d+),\s*(\d+),\s*(\d+)\)$/);
        if (rgb) return rgb.slice(1).map(Number);
        const hex = v.match(/^#([0-9a-f]{6})$/i);
        assert.ok(hex, `--ui-${name} is a literal colour (got ${v})`);
        return [0, 2, 4].map(i => parseInt(hex[1].slice(i, i + 2), 16));
    };
    const luminance = c => {
        const [r, g, b] = c.map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); });
        return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    };
    const ratio = (a, b) => { const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
    for (const surface of ['bg', 'surface', 'surface-2', 'surface-3']) {
        for (const text of ['text', 'text-strong', 'muted', 'primary', 'accent-text', 'success', 'warning', 'danger', 'info']) {
            const r = ratio(value(text), value(surface));
            assert.ok(r >= 4.5, `--ui-${text} on --ui-${surface}: ${r.toFixed(2)} : 1`);
        }
    }
    assert.ok(ratio(value('on-primary'), value('primary')) >= 4.5, 'the text on a gold button');
});
