// @ts-check
import { test, describe, before, after } from 'node:test';
import assert from 'node:assert/strict';

import { installDom, uninstallDom, fakeTex, tick } from './_dom.js';
import { parseRich, plainText, renderRich, mathSpans, texNode } from '../../js/player/richtext.js';

/** Compact AST rendering for assertions: text, **b**, _i_, `c`, $m$, [[k]]. */
const show = (/** @type {any[]} */ nodes) =>
  nodes
    .map((n) => {
      if (n.t === 'text') return n.v;
      if (n.t === 'code') return `<code:${n.v}>`;
      if (n.t === 'math') return `<math:${n.v}>`;
      return `<${n.t}:${show(n.c)}>`;
    })
    .join('');

describe('parseRich', () => {
  test('plain text is a single text node', () => {
    assert.deepEqual(parseRich('hello world'), [{ t: 'text', v: 'hello world' }]);
    assert.deepEqual(parseRich(''), []);
    assert.deepEqual(parseRich(null), []);
  });

  test('each inline kind', () => {
    assert.equal(show(parseRich('a **b** c')), 'a <b:b> c');
    assert.equal(show(parseRich('a *i* c')), 'a <i:i> c');
    assert.equal(show(parseRich('run `ls -la` now')), 'run <code:ls -la> now');
    assert.equal(show(parseRich('so $V = IR$ holds')), 'so <math:V = IR> holds');
    assert.equal(show(parseRich('the [[ohm]] unit')), 'the <k:ohm> unit');
  });

  test('nesting: italic in bold, bold in italic, math and bold in keywords', () => {
    assert.equal(show(parseRich('**a *b* c**')), '<b:a <i:b> c>');
    assert.equal(show(parseRich('*a **b** c*')), '<i:a <b:b> c>');
    assert.equal(show(parseRich('**a *b***')), '<b:a <i:b>>');
    assert.equal(show(parseRich('[[**Ohm** $\\Omega$]]')), '<k:<b:Ohm> <math:\\Omega>>');
  });

  test('escapes produce literal delimiters', () => {
    assert.equal(show(parseRich('costs \\$5 and \\$10')), 'costs $5 and $10');
    assert.equal(show(parseRich('2 \\* 3 = 6')), '2 * 3 = 6');
    assert.equal(show(parseRich('\\*\\*not bold\\*\\*')), '**not bold**');
    assert.equal(show(parseRich('a \\` b \\[\\[c\\]\\] \\\\ d')), 'a ` b [[c]] \\ d');
    assert.equal(show(parseRich('back\\slash \\q')), 'back\\slash \\q', 'unknown escapes keep the backslash');
  });

  test('flanking rules keep arithmetic and currency literal', () => {
    assert.equal(show(parseRich('2 * 3 * 4')), '2 * 3 * 4');
    assert.equal(show(parseRich('$5 and $10')), '$5 and $10');
    assert.equal(show(parseRich('a ** b')), 'a ** b');
    assert.equal(show(parseRich('$ x $')), '$ x $');
    assert.equal(show(parseRich('$$x$$')), '$$x$$', 'display math is not allowed inline');
  });

  test('intraword asterisks stay literal (plain-text maths; backend tokenizer rule)', () => {
    assert.equal(show(parseRich('Total = 2*3*4 = 24')), 'Total = 2*3*4 = 24');
    assert.equal(plainText('Total = 2*3*4 = 24'), 'Total = 2*3*4 = 24');
    assert.equal(show(parseRich('Energy E = V*I*t joules')), 'Energy E = V*I*t joules');
    assert.equal(show(parseRich('x**2 + y**2 = r**2')), 'x**2 + y**2 = r**2');
    assert.equal(plainText('x**2 + y**2'), 'x**2 + y**2');
    assert.equal(show(parseRich('2**3 and a*b')), '2**3 and a*b');
    assert.equal(show(parseRich('*a*b')), '*a*b', 'a closer followed by a word character is not a closer');
    assert.equal(show(parseRich('**x**2')), '**x**2');
    assert.equal(show(parseRich('see *2*3*')), 'see <i:2*3>', 'intraword stars inside an emphasis stay literal');
    assert.equal(show(parseRich('வேகம்*நேரம்*')), 'வேகம்*நேரம்*', 'Indic letters (incl. vowel signs) are word characters');
  });

  test('emphasis next to punctuation and around runs still works', () => {
    assert.equal(show(parseRich('(*note*), **bold**.')), '(<i:note>), <b:bold>.');
    assert.equal(show(parseRich('"*quoted*"')), '"<i:quoted>"');
    assert.equal(show(parseRich('***both***')), '<b:<i:both>>');
    assert.equal(show(parseRich('a **b *c*** d')), 'a <b:b <i:c>> d');
  });

  test('an escaped dollar right before inline math does not block it', () => {
    assert.equal(show(parseRich('\\$$x$')), '$<math:x>');
    assert.equal(show(parseRich('cost \\$$5 + x$ total')), 'cost $<math:5 + x> total');
    assert.equal(show(parseRich('\\$\\$x$')), '$$x$', 'two escaped dollars stay literal');
    assert.equal(show(parseRich('$$x$')), '$$x$', 'an unescaped $$ still never opens');
  });

  test('pathological delimiter runs stay fast (bounded scan budget)', () => {
    const t0 = Date.now();
    parseRich('*'.repeat(20000));
    parseRich('a*'.repeat(10000));
    parseRich('**a '.repeat(5000));
    parseRich('*a '.repeat(1500) + 'b*'); // far-away closer: every opener would rescan to the end
    parseRich('[[a '.repeat(1000) + ']]');
    parseRich('**a* '.repeat(800));
    assert.ok(Date.now() - t0 < 3000, `took ${Date.now() - t0} ms`);
  });

  test('the scan budget never changes ordinary text', () => {
    const long = 'Ohm says **V = IR** and *current* is [[I]]; $x^2$ too. '.repeat(25); // > 1200 chars
    const nodes = parseRich(long);
    assert.equal(nodes.filter((n) => n.t === 'b').length, 25);
    assert.equal(nodes.filter((n) => n.t === 'i').length, 25);
    assert.equal(nodes.filter((n) => n.t === 'k').length, 25);
    assert.equal(nodes.filter((n) => n.t === 'math').length, 25);
    assert.deepEqual(parseRich(long), nodes, 'deterministic');
  });

  test('unclosed delimiters are literal', () => {
    assert.equal(show(parseRich('**bold')), '**bold');
    assert.equal(show(parseRich('*it')), '*it');
    assert.equal(show(parseRich('`code')), '`code');
    assert.equal(show(parseRich('$x')), '$x');
    assert.equal(show(parseRich('[[key')), '[[key');
    assert.equal(show(parseRich('``')), '``', 'empty code span stays literal');
    assert.equal(show(parseRich('[[]]')), '[[]]', 'empty keyword stays literal');
  });

  test('math keeps TeX escapes and backslashes intact', () => {
    assert.equal(show(parseRich('$\\frac{a}{b}$')), '<math:\\frac{a}{b}>');
    assert.equal(show(parseRich('$a \\$ b$')), '<math:a \\$ b>');
    assert.equal(show(parseRich('$a*b*c$')), '<math:a*b*c>', 'no emphasis inside math');
    assert.equal(show(parseRich('`**x**`')), '<code:**x**>', 'no emphasis inside code');
  });

  test('script-like and HTML-like text stays text', () => {
    const src = '<script>alert(1)</script> <img src=x onerror=alert(1)> &amp;';
    assert.deepEqual(parseRich(src), [{ t: 'text', v: src }]);
  });

  test('pathological input terminates quickly', () => {
    const evil = '**a *'.repeat(240) + '[['.repeat(100) + '$'.repeat(200);
    const t0 = Date.now();
    parseRich(evil);
    assert.ok(Date.now() - t0 < 2000);
  });

  test('plainText and mathSpans', () => {
    assert.equal(plainText('**Ohm** says $V=IR$ in `code` [[here]]'), 'Ohm says V=IR in code here');
    assert.deepEqual(mathSpans('a $x$ **b $y$** [[c $z$]]'), ['x', 'y', 'z']);
  });
});

describe('renderRich (DOM)', () => {
  before(() => installDom());
  after(() => uninstallDom());

  test('builds only text nodes and fixed elements', () => {
    const host = document.createElement('div');
    host.appendChild(renderRich('a **b** *c* `d` [[e]]', { renderTex: fakeTex().render }));
    assert.equal(host.querySelector('strong.rt-b')?.textContent, 'b');
    assert.equal(host.querySelector('em.rt-i')?.textContent, 'c');
    assert.equal(host.querySelector('code.rt-code')?.textContent, 'd');
    assert.equal(host.querySelector('span.rt-k')?.textContent, 'e');
    assert.equal(host.querySelector('.keyword'), null, 'not the class Prism gives code keywords');
    assert.equal(host.textContent, 'a b c d e');
  });

  test('HTML in text is never parsed', () => {
    const host = document.createElement('div');
    host.appendChild(renderRich('<img src=x onerror=alert(1)><script>alert(2)</script> **<b>x</b>**'));
    assert.equal(host.querySelector('img'), null);
    assert.equal(host.querySelector('script'), null);
    assert.equal(host.querySelector('b'), null);
    assert.equal(host.querySelector('strong')?.textContent, '<b>x</b>');
    assert.ok(host.textContent?.includes('<script>alert(2)</script>'));
  });

  test('math is rendered through the injected TeX renderer (inline)', async () => {
    const tex = fakeTex();
    /** @type {Promise<unknown>[]} */
    const pending = [];
    const host = document.createElement('div');
    host.appendChild(renderRich('so $V = IR$ and $x^2$', { renderTex: tex.render, pending }));
    const spans = host.querySelectorAll('.rt-math');
    assert.equal(spans.length, 2);
    assert.ok(spans[0].classList.contains('is-pending'));
    assert.equal(spans[0].textContent, 'V = IR', 'source shown as fallback until rendered');
    await Promise.all(pending);
    assert.deepEqual(tex.calls, [{ latex: 'V = IR', display: false }, { latex: 'x^2', display: false }]);
    assert.equal(spans[0].querySelector('svg')?.getAttribute('data-tex'), 'V = IR');
    assert.equal(spans[0].classList.contains('is-pending'), false);
  });

  test('TeX failures leave the escaped source visible', async () => {
    /** @type {Promise<unknown>[]} */
    const pending = [];
    const host = document.createElement('div');
    host.appendChild(renderRich('$\\bad$', { renderTex: () => Promise.reject(new Error('nope')), pending }));
    await Promise.all(pending);
    const span = /** @type {HTMLElement} */ (host.querySelector('.rt-math'));
    assert.ok(span.classList.contains('is-error'));
    assert.equal(span.textContent, '\\bad');
  });

  test('texNode with an injected renderer returns its node', async () => {
    const tex = fakeTex();
    const node = await texNode('a', true, tex.render);
    assert.equal(node.getAttribute('data-display'), '1');
    await tick();
  });
});
