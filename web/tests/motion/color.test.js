// @ts-check
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import {
  parseColor,
  toOklch,
  fromOklch,
  toHex,
  mixOklch,
  gamutMapOklch,
  contrastRatio,
  composite,
  relativeLuminance,
  ACHROMATIC_CHROMA,
} from '../../js/motion/color.js';

const close = (a, b, eps, msg = '') => assert.ok(Math.abs(a - b) <= eps, `${msg} ${a} != ${b} (±${eps})`);
/** Hue distance in degrees on the circle. */
const hueDist = (a, b) => Math.abs((((a - b) % 360) + 540) % 360 - 180);

describe('parseColor', () => {
  test('hex forms, case-insensitive', () => {
    assert.deepEqual(parseColor('#fff'), { r: 255, g: 255, b: 255, a: 1 });
    assert.deepEqual(parseColor('#0FE3FE'), { r: 15, g: 227, b: 254, a: 1 });
    assert.deepEqual(parseColor('#00000080'), { r: 0, g: 0, b: 0, a: 128 / 255 });
    assert.deepEqual(parseColor('#f008'), { r: 255, g: 0, b: 0, a: 136 / 255 });
  });

  test('rgb()/rgba() in comma and space syntax, with percentages and slash alpha', () => {
    assert.deepEqual(parseColor('rgb(255, 128, 0)'), { r: 255, g: 128, b: 0, a: 1 });
    assert.deepEqual(parseColor('rgba(255, 128, 0, 0.5)'), { r: 255, g: 128, b: 0, a: 0.5 });
    assert.deepEqual(parseColor('rgb(100% 0% 0% / 25%)'), { r: 255, g: 0, b: 0, a: 0.25 });
  });

  test('hsl(), oklch() and oklab()', () => {
    assert.equal(toHex(parseColor('hsl(120 100% 50%)') || 'x'), '#00ff00');
    assert.equal(toHex(parseColor('hsl(0, 0%, 100%)') || 'x'), '#ffffff');
    assert.equal(toHex(parseColor('oklch(0.62796 0.25768 29.2339)') || 'x'), '#ff0000');
    assert.equal(toHex(parseColor('oklch(62.796% 0.25768 29.2339deg / 0.5)') || 'x'), '#ff000080');
    assert.equal(toHex(parseColor('oklab(0.62796 0.22486 0.12585)') || 'x'), '#ff0000');
  });

  test('keywords and garbage', () => {
    assert.deepEqual(parseColor('transparent'), { r: 0, g: 0, b: 0, a: 0 });
    assert.deepEqual(parseColor(' White '), { r: 255, g: 255, b: 255, a: 1 });
    for (const bad of ['', 'red', '#ggg', '#12345', 'rgb(1, 2)', 'rgb(a, b, c)', 'var(--x)', null, undefined, 42]) {
      assert.equal(parseColor(/** @type {any} */ (bad)), null, String(bad));
    }
  });

  test('an Rgba object passes through (alpha clamped)', () => {
    assert.deepEqual(parseColor({ r: 1, g: 2, b: 3, a: 7 }), { r: 1, g: 2, b: 3, a: 1 });
  });
});

describe('OKLCH conversion', () => {
  test('reference values for the sRGB primaries and white', () => {
    const ref = [
      ['#ff0000', 0.62796, 0.25768, 29.2339],
      ['#00ff00', 0.86644, 0.29483, 142.4953],
      ['#0000ff', 0.45201, 0.31321, 264.052],
    ];
    for (const [hex, l, c, h] of ref) {
      const o = toOklch(/** @type {string} */ (hex));
      close(o.l, /** @type {number} */ (l), 5e-4, `${hex} L`);
      close(o.c, /** @type {number} */ (c), 5e-4, `${hex} C`);
      close(o.h, /** @type {number} */ (h), 0.05, `${hex} H`);
    }
    const white = toOklch('#ffffff');
    close(white.l, 1, 1e-6, 'white L');
    assert.ok(white.c < ACHROMATIC_CHROMA);
    assert.equal(white.h, 0);
  });

  test('the content palette of §4.7 has the hues the design computed', () => {
    const palette = {
      '#0FE3FE': 210, '#C4A4FE': 300, '#FEACE3': 340, '#F8A87F': 48, '#5DD8AA': 166,
      '#0267C7': 255, '#80369E': 315, '#870C62': 345, '#9F4503': 48, '#016F50': 166,
    };
    for (const [hex, hue] of Object.entries(palette)) assert.ok(hueDist(toOklch(hex).h, hue) <= 1, hex);
  });

  test('sRGB -> OKLCH -> sRGB is exact for every colour on a 52^3 grid', () => {
    let bad = 0;
    for (let r = 0; r < 256; r += 5) {
      for (let g = 0; g < 256; g += 5) {
        for (let b = 0; b < 256; b += 5) {
          const hex = `#${[r, g, b].map((v) => v.toString(16).padStart(2, '0')).join('')}`;
          if (fromOklch(toOklch(hex)) !== hex) bad++;
        }
      }
    }
    assert.equal(bad, 0);
  });

  test('out-of-gamut colours are mapped by chroma reduction: in gamut, lightness kept', () => {
    for (const [l, c, h] of [[0.7, 0.4, 150], [0.5, 0.4, 30], [0.9, 0.3, 264], [0.3, 0.35, 320], [0.95, 0.2, 100]]) {
      const rgb = gamutMapOklch(l, c, h);
      assert.ok(rgb.every((v) => v >= 0 && v <= 1));
      const back = toOklch(fromOklch({ l, c, h }));
      close(back.l, l, 0.02, `L of oklch(${l} ${c} ${h})`);
      assert.ok(back.c < c, 'chroma reduced');
    }
    assert.equal(fromOklch({ l: 1.2, c: 0.1, h: 10 }), '#ffffff');
    assert.equal(fromOklch({ l: -0.1, c: 0.1, h: 10 }), '#000000');
    assert.equal(fromOklch({ l: 0.5, c: 0, h: Number.NaN, a: 0.5 }), '#63636380');
    assert.equal(fromOklch({ l: 0.5, c: 0, h: 0, a: Number.NaN }), '#636363', 'a NaN alpha never reaches the hex');
  });
});

describe('mixOklch', () => {
  test('endpoints are the inputs; p is clamped', () => {
    assert.equal(mixOklch('#0267c7', '#f2d935', 0), '#0267c7');
    assert.equal(mixOklch('#0267c7', '#f2d935', 1), '#f2d935');
    assert.equal(mixOklch('#0267c7', '#f2d935', -1), '#0267c7');
    assert.equal(mixOklch('#0267c7', '#f2d935', 2), '#f2d935');
  });

  test('white to black at 0.5 is OKLab L = 0.5 (#636363)', () => {
    assert.equal(mixOklch('#ffffff', '#000000', 0.5), '#636363');
  });

  test('hue takes the shorter arc', () => {
    // 350° -> 10° passes through 0°, not 180°.
    const a = fromOklch({ l: 0.6, c: 0.15, h: 350 });
    const b = fromOklch({ l: 0.6, c: 0.15, h: 10 });
    assert.ok(hueDist(toOklch(mixOklch(a, b, 0.5)).h, 0) <= 2);
  });

  test('a grey end borrows the other hue (no rainbow sweep from white)', () => {
    const blue = toOklch('#0267c7');
    for (const p of [0.25, 0.5, 0.75]) assert.ok(hueDist(toOklch(mixOklch('#ffffff', '#0267c7', p)).h, blue.h) <= 2, `p=${p}`);
  });

  test('alpha is premultiplied: fading in from transparent keeps the colour', () => {
    const mid = parseColor(mixOklch('transparent', '#0267c7', 0.5));
    assert.ok(mid);
    close(mid.a, 0.5, 1 / 255);
    assert.equal(toHex({ ...mid, a: 1 }), '#0267c7');
  });

  test('perceptual midpoint: lightness moves evenly', () => {
    const a = toOklch('#101830');
    const b = toOklch('#f0e8d0');
    for (const p of [0.1, 0.3, 0.5, 0.7, 0.9]) close(toOklch(mixOklch('#101830', '#f0e8d0', p)).l, a.l + (b.l - a.l) * p, 0.005, `p=${p}`);
  });
});

describe('contrastRatio', () => {
  test('WCAG reference values', () => {
    close(contrastRatio('#000000', '#ffffff'), 21, 1e-9);
    assert.equal(Math.round(contrastRatio('#777777', '#ffffff') * 100) / 100, 4.48);
    assert.equal(contrastRatio('#336699', '#336699'), 1);
    close(contrastRatio('#ffffff', '#000000'), contrastRatio('#000000', '#ffffff'), 1e-12);
    close(relativeLuminance('#ffffff'), 1, 1e-12);
  });

  test('a translucent foreground is composited over the background', () => {
    close(contrastRatio('rgba(255, 255, 255, 0.5)', '#000000'), contrastRatio('rgb(127.5, 127.5, 127.5)', '#000000'), 1e-9);
  });

  test('the dark content inks reach 7:1 on a dark family background', () => {
    // §4.7: ink L >= 0.96 on bg L 0.17-0.24.
    const bg = fromOklch({ l: 0.24, c: 0.06, h: 275 });
    const ink = fromOklch({ l: 0.96, c: 0, h: 0 });
    assert.ok(contrastRatio(ink, bg) >= 7);
  });

  test('bad colours throw', () => {
    assert.throws(() => contrastRatio('nope', '#fff'), TypeError);
  });
});

describe('composite', () => {
  test('source-over in gamma-encoded sRGB, the same as aadhi/compose/dynamic/colour.py composite', () => {
    // expected values computed with colour.py: to_hex(composite(parse_colour(top), parse_colour(bottom)))
    const PY = [
      ['#10132Cc7', '#5A6070', '#20243b'], // the title block's scrim (bg at 78%) over a background colour
      ['#FDF2DDc7', '#BE3D70', '#efcac5'],
      ['#FFFFFF2E', '#10132C', '#3b3e52'],
      ['#14182240', '#FDF2DD', '#c3bbae'],
      ['#10132Cc7', '#FF936D', '#442f3a'],
    ];
    for (const [top, bottom, want] of PY) assert.equal(composite(top, bottom), want, `${top} over ${bottom}`);
    // translucent over translucent: colour.py gives (0.66754, 0, 0.33246, 0.75196)
    assert.equal(composite('#ff000080', '#0000ff80'), '#aa0055c0');
  });

  test('opaque on top hides the bottom; transparent on top leaves it; the contrast of text on the result', () => {
    assert.equal(composite('#123456', '#abcdef'), '#123456');
    assert.equal(composite('transparent', '#abcdef'), '#abcdef');
    assert.equal(composite('transparent', 'transparent'), '#00000000');
    close(contrastRatio('#F3F5F9', composite('#10132Cc7', '#5A6070')), contrastRatio('#F3F5F9', '#20243b'), 1e-12);
    assert.throws(() => composite('nope', '#fff'), TypeError);
  });
});
