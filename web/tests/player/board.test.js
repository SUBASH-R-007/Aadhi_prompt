// @ts-check
import { test, describe, before, after } from 'node:test';
import assert from 'node:assert/strict';

import { installDom, uninstallDom, fakeTex } from './_dom.js';
import { loadTimeline, sceneById } from './_fixture.js';

installDom();
const { BoardView, prismLanguage, LABELS, MIN_FIT } = await import('../../js/player/board.js');
const { sceneStateAt } = await import('../../js/player/schedule.js');

const timeline = loadTimeline();
const ohm = sceneById(timeline, 's-ohm');
const example = sceneById(timeline, 's-example');

/** Fake Prism that records highlighted elements and writes escaped markup like the real one. */
function fakePrism() {
  /** @type {string[]} */
  const langs = [];
  /** @type {Element[]} */
  const highlighted = [];
  return {
    langs,
    highlighted,
    /** @param {string} lang */
    async load(lang) {
      langs.push(lang);
      return {
        /** @param {Element} el */
        highlightElement(el) {
          highlighted.push(el);
          const text = el.textContent || '';
          el.textContent = '';
          const span = document.createElement('span');
          span.className = 'token keyword';
          span.textContent = text;
          el.appendChild(span);
        },
      };
    },
  };
}

/**
 * @param {any} scene
 * @param {{ tex?: ReturnType<typeof fakeTex>, prism?: ReturnType<typeof fakePrism> }} [deps]
 */
function makeBoard(scene, deps = {}) {
  const tex = deps.tex || fakeTex();
  const prism = deps.prism || fakePrism();
  const board = new BoardView(scene, { mode: 'live', renderTex: tex.render, loadPrism: prism.load });
  document.body.appendChild(board.el);
  return { board, tex, prism };
}

describe('BoardView rendering', () => {
  after(() => {
    document.body.replaceChildren();
  });

  test('renders every item kind with data-item attributes and no DOM ids', () => {
    const { board } = makeBoard(ohm);
    const kinds = [...board.el.querySelectorAll('[data-item]')].map((el) => /** @type {HTMLElement} */ (el).dataset.kind);
    assert.deepEqual(kinds, ohm.board?.map((i) => i.kind));
    assert.equal(board.el.querySelectorAll('[id]').length, 0, 'model ids never become DOM ids');
    assert.equal(board.el.querySelector('[data-item="i-head"]')?.tagName, 'H3');
    assert.ok(board.el.querySelector('[data-item="i-def"] .bi-term'));
    assert.ok(board.el.querySelector('[data-item="i-info"].bi-callout--info'));
    assert.ok(board.el.querySelector('[data-item="i-tip"].bi-callout--tip'));
    assert.ok(board.el.querySelector('[data-item="i-warn"].bi-callout--warning'));
    assert.equal(board.el.querySelector('[data-item="i-warn"] .bi-callout-label')?.textContent, LABELS.warning);
    assert.equal(board.el.querySelectorAll('[data-item="i-mis"] .bi-mis-row').length, 2);
    assert.ok(board.el.querySelector('[data-item="i-take"] svg.ap-icon'));
    assert.equal(board.el.querySelector('.ap-board-title')?.textContent, "Ohm's Law");
  });

  test('script-like text in items stays text', () => {
    const { board } = makeBoard(ohm);
    const para = /** @type {HTMLElement} */ (board.el.querySelector('[data-item="i-para"]'));
    assert.equal(para.querySelector('script'), null);
    assert.ok(para.textContent?.includes('<script>alert(1)</script>'));
    assert.ok(para.textContent?.startsWith('Costs $5'), 'escaped dollar is literal');
    assert.equal(document.querySelectorAll('script').length, 0);
  });

  test('formula: display TeX, meaning and a variables legend via renderTex', async () => {
    const { board, tex } = makeBoard(ohm);
    await board.ready();
    const formula = /** @type {HTMLElement} */ (board.el.querySelector('[data-item="i-formula"]'));
    assert.equal(formula.querySelector('.bi-formula-tex svg')?.getAttribute('data-tex'), 'V = I R');
    assert.ok(tex.calls.some((c) => c.latex === 'V = I R' && c.display === true));
    const vars = [...formula.querySelectorAll('.bi-var')];
    assert.equal(vars.length, 3);
    assert.equal(vars[0].querySelector('.bi-var-meaning')?.textContent, 'voltage');
    assert.equal(vars[2].querySelector('.bi-var-unit')?.textContent, 'Ω');
    assert.ok(tex.calls.some((c) => c.latex === 'R' && c.display === false));
    assert.equal(formula.querySelector('.bi-formula-text')?.textContent, 'Voltage equals current times resistance');
  });

  test('code: textContent first, then Prism.highlightElement; HTML in code stays text', async () => {
    const { board, prism } = makeBoard(example);
    const code = /** @type {HTMLElement} */ (board.el.querySelector('[data-item="e-code"] code'));
    assert.ok(code.classList.contains('language-python'));
    assert.ok(code.textContent?.includes('<b>3.0</b>'));
    await board.ready();
    assert.deepEqual(prism.langs, ['python']);
    assert.equal(prism.highlighted[0], code);
    assert.equal(code.querySelector('b'), null);
    assert.ok(code.textContent?.includes('<b>3.0</b>'));
  });

  test('unknown code languages render as plain text without Prism', async () => {
    const scene = { ...example, board: [{ id: 'c', kind: 'code', language: 'cobol', code: 'DISPLAY "HI"' }] };
    const { board, prism } = makeBoard(scene);
    await board.ready();
    assert.deepEqual(prism.langs, []);
    assert.ok(board.el.querySelector('code.language-none'));
    assert.equal(prismLanguage('C++'), 'cpp');
    assert.equal(prismLanguage('js'), 'javascript');
    assert.equal(prismLanguage('<script>'), null);
  });

  test('table: header and cells rendered as rich text', () => {
    const { board } = makeBoard(example);
    const table = /** @type {HTMLElement} */ (board.el.querySelector('[data-item="e-table"] table'));
    assert.deepEqual([...table.querySelectorAll('th')].map((th) => th.textContent), ['V (V)', 'R (Ω)', 'I (A)']);
    assert.equal(table.querySelectorAll('tbody tr').length, 2);
    assert.equal(table.querySelector('tbody td strong')?.textContent, '3');
  });

  test('figure: image from scene.figures[item.id] with caption alt, decoded in ready()', async () => {
    const { board } = makeBoard(example);
    const img = /** @type {HTMLImageElement} */ (board.el.querySelector('[data-item="e-fig"] img'));
    assert.equal(img.getAttribute('src'), 'http://localhost/media/assets/figure/circuit.png');
    assert.equal(img.getAttribute('alt'), 'Series circuit');
    assert.equal(board.images.length, 1);
    await board.ready();
  });

  test('figure without media shows a placeholder', () => {
    const scene = { ...example, figures: {} };
    const { board } = makeBoard(scene);
    assert.ok(board.el.querySelector('[data-item="e-fig"].is-missing .bi-figure-missing'));
  });

  test('unsafe figure URLs are neutralised by dom.js', () => {
    const scene = { ...example, figures: { 'e-fig': { kind: 'image', url: 'javascript:alert(1)' } } };
    const { board } = makeBoard(scene);
    assert.equal(board.el.querySelector('[data-item="e-fig"] img')?.getAttribute('src'), 'about:blank');
  });

  test('example steps are numbered; blank steps show a placeholder until filled', () => {
    const { board } = makeBoard(example);
    const nums = [...board.el.querySelectorAll('.bi-step-num')].map((n) => n.textContent);
    assert.deepEqual(nums, ['Step 1', 'Step 2', 'Step 3']);
    const step = /** @type {HTMLElement} */ (board.el.querySelector('[data-item="e-step1"]'));
    assert.ok(step.classList.contains('is-blank'));
    assert.equal(step.querySelector('.bi-step-blank')?.textContent, LABELS.blank);
    assert.ok(step.querySelector('.bi-step-text'), 'answer is in the DOM, hidden by CSS until filled');
  });
});

describe('BoardView state', () => {
  after(() => document.body.replaceChildren());

  test('reveal, active and highlight classes follow sceneStateAt', () => {
    const { board } = makeBoard(ohm);
    board.update(sceneStateAt(ohm, 0));
    const el = (/** @type {string} */ id) => /** @type {HTMLElement} */ (board.el.querySelector(`[data-item="${id}"]`));
    assert.ok(el('i-head').classList.contains('is-visible'));
    assert.equal(el('i-def').classList.contains('is-visible'), false);
    assert.equal(el('i-def').getAttribute('aria-hidden'), 'true');
    board.update(sceneStateAt(ohm, 0.5));
    assert.ok(el('i-def').classList.contains('is-visible'));
    assert.ok(el('i-def').classList.contains('is-active'));
    assert.equal(el('i-def').getAttribute('aria-hidden'), 'false');
    board.update(sceneStateAt(ohm, 7.2));
    assert.ok(el('i-formula').classList.contains('is-highlight'));
    assert.ok(el('i-info').classList.contains('is-active'));
    assert.equal(el('i-def').classList.contains('is-active'), false);
    board.update(sceneStateAt(ohm, 0));
    assert.equal(el('i-formula').classList.contains('is-visible'), false, 'seeking back hides items again');
    assert.equal(el('i-formula').classList.contains('is-highlight'), false);
  });

  test('fill state toggles is-filled', () => {
    const { board } = makeBoard(example);
    const step = /** @type {HTMLElement} */ (board.el.querySelector('[data-item="e-step1"]'));
    board.update(sceneStateAt(example, 3));
    assert.equal(step.classList.contains('is-filled'), false);
    board.update(sceneStateAt(example, 6.5));
    assert.ok(step.classList.contains('is-filled'));
  });

  test('fit() keeps scale 1 when content fits and never goes below MIN_FIT', () => {
    const { board } = makeBoard(ohm);
    assert.equal(board.fit(), 1);
    // simulate an overflowing body: scrollHeight always larger than clientHeight
    Object.defineProperty(board.body, 'scrollHeight', { configurable: true, get: () => 5000 });
    Object.defineProperty(board.body, 'clientHeight', { configurable: true, get: () => 400 });
    const s = board.fit();
    assert.ok(s >= MIN_FIT && s < MIN_FIT + 0.01, `scale ${s}`);
    assert.equal(board.el.style.getPropertyValue('--fit'), s.toFixed(4));
  });

  test('fit() finds the largest scale that fits (binary search on overflow)', () => {
    const { board } = makeBoard(ohm);
    Object.defineProperty(board.body, 'scrollHeight', {
      configurable: true,
      get: () => 1000 * Number(board.el.style.getPropertyValue('--fit') || 1),
    });
    Object.defineProperty(board.body, 'clientHeight', { configurable: true, get: () => 800 });
    const s = board.fit();
    assert.ok(s <= 0.8 && s > 0.78, `scale ${s}`);
  });

  test('scene-type badge is shown unless the title already says it', () => {
    const badgeOf = (/** @type {any} */ scene) => makeBoard(scene).board.el.querySelector('.ap-badge')?.textContent ?? null;
    assert.equal(badgeOf({ ...example, title: 'Find the current' }), LABELS.example);
    assert.equal(badgeOf({ ...example, title: 'Worked  example!' }), null, 'case/punctuation-insensitive match');
    assert.equal(badgeOf({ ...example, type: 'recap', title: 'Recap' }), null);
    assert.equal(badgeOf({ ...example, type: 'content', title: 'Anything' }), null, 'content scenes have no badge');
  });

  test('destroy removes the element', () => {
    const { board } = makeBoard(ohm);
    board.destroy();
    assert.equal(board.el.isConnected, false);
    assert.equal(board.items.size, 0);
  });
});

before(() => {});
after(() => uninstallDom());
