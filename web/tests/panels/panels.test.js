import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

import { container, flush, installDom, stubCanvas, timelineWith, trackListeners } from './_env.js';
import { PANEL_KINDS, createPanel, factoryFor } from '../../js/player/panels/index.js';
import { createManimPanel } from '../../js/player/panels/manimVideo.js';
import { createImagePanel } from '../../js/player/panels/image.js';
import { createGifPanel } from '../../js/player/panels/gif.js';
import { TERMINAL_MAX_LINES, terminalLineTimes } from '../../js/player/panels/timing.js';
import { quizTeaserRevealTime } from '../../js/player/panels/timing.js';
import { MIN_LEGIBLE_FONT, chooseView, deriveConceptState, focusAnchor } from '../../js/player/panels/skillTree.js';
import { MAX_UPSCALE } from '../../js/player/panels/skillTreeLayout.js';
import { MAX_RATE, MIN_RATE, RateEstimator, quantizeRate } from '../../js/player/panels/manimVideo.js';
import { containRect } from '../../js/player/panels/util.js';

const w = installDom();
stubCanvas(w);
const require = createRequire(import.meta.url);
w.math = require('mathjs');
class FakeChart {
  static instances = [];
  constructor(canvas, config) {
    this.canvas = canvas;
    this.config = config;
    this.destroyed = false;
    FakeChart.instances.push(this);
  }
  destroy() {
    this.destroyed = true;
  }
}
w.Chart = FakeChart;
w.HTMLImageElement.prototype.decode = function decode() {
  return Promise.resolve();
};

const doc = w.document;

/** One valid ResolvedSidePanel per kind. */
function sample(kind) {
  const panels = {
    skill_tree: { panel: { kind: 'skill_tree' } },
    figure: { panel: { kind: 'figure', figure_id: 'f1', title: 'Circuit' }, media: { kind: 'image', url: '/media/assets/f.png' } },
    image: { panel: { kind: 'image', image_prompt: 'x' }, media: { kind: 'image', url: '/media/assets/i.png' } },
    chart: { panel: { kind: 'chart', chart: { chart_type: 'bar', labels: ['a', 'b'], datasets: [{ label: 'v', data: [1, 2] }] } } },
    graph: { panel: { kind: 'graph', graph: { functions: [{ expr: 'x^2' }], x_range: [-2, 2] } } },
    model_3d: { panel: { kind: 'model_3d', model_3d: { primitives: [{ shape: 'sphere', size: [1] }] } } },
    manim: { panel: { kind: 'manim', manim: { template: 'equation_steps' } }, media: { kind: 'video', url: '/media/assets/m.mp4', duration: 4 } },
    terminal: { panel: { kind: 'terminal', terminal: { command: 'ls', output: 'a\nb' } } },
    quiz: { panel: { kind: 'quiz', quiz: { question: 'Q?', options: ['A', 'B'], correct_index: 1 } } },
    gif: { panel: { kind: 'gif', gif_query: 'cat' }, media: { kind: 'gif', url: 'https://media.giphy.com/x.gif', render_in_mp4: false } },
  };
  return { show_at: 0, ...panels[kind] };
}

function stubMedia() {
  const proto = w.HTMLMediaElement.prototype;
  const log = [];
  Object.defineProperty(proto, 'readyState', { get() { return this._rs ?? 4; }, configurable: true });
  Object.defineProperty(proto, 'paused', { get() { return !this._playing; }, configurable: true });
  Object.defineProperty(proto, 'currentTime', {
    get() { return this._ct ?? 0; },
    set(v) { this._ct = v; log.push(['seek', v]); },
    configurable: true,
  });
  proto.play = function play() { this._playing = true; log.push(['play']); return Promise.resolve(); };
  proto.pause = function pause() { this._playing = false; log.push(['pause']); };
  proto.load = function load() { log.push(['load']); };
  return log;
}
const mediaLog = stubMedia();

/** Stub getBoundingClientRect on an element. */
function rect(el, left, top, width, height) {
  el.getBoundingClientRect = () => ({ left, top, width, height, right: left + width, bottom: top + height, x: left, y: top });
}

test('PANEL_KINDS covers every SidePanelKind of the schema', () => {
  assert.deepEqual([...PANEL_KINDS].sort(), ['chart', 'figure', 'gif', 'graph', 'image', 'manim', 'model_3d', 'quiz', 'skill_tree', 'terminal']);
});

test('createPanel validates its inputs', () => {
  assert.throws(() => createPanel(null, sample('chart'), { mode: 'live' }), TypeError);
  assert.throws(() => createPanel(container(doc), /** @type {any} */ ({}), { mode: 'live' }), TypeError);
});

test('every kind builds a titled card inside the container and destroy() removes it', async () => {
  for (const mode of ['live', 'preview', 'render']) {
    for (const kind of PANEL_KINDS) {
      const box = container(doc);
      const rsp = sample(kind);
      const p = createPanel(box, rsp, { mode, timeline: timelineWith(rsp), sceneIndex: 0 });
      assert.equal(p.el.parentElement, box, `${kind}/${mode} appended`);
      assert.equal(p.el.dataset.kind, kind);
      assert.equal(p.el.dataset.mode, mode);
      assert.ok(p.el.querySelector('.ap-title').textContent.length > 0);
      p.update(0.5, { panelVisible: true });
      if (kind !== 'model_3d') await p.ready(); // three.js cannot load under node (covered in libs tests)
      p.destroy();
      p.destroy(); // idempotent
      assert.equal(box.children.length, 0, `${kind}/${mode} removed`);
      p.update(1, {}); // no-op after destroy
      box.remove();
    }
  }
});

test('destroy() releases every listener the panels added', async () => {
  const tracker = trackListeners(w);
  try {
    for (const mode of ['live', 'preview', 'render']) {
      const panels = [];
      for (const kind of PANEL_KINDS) {
        const rsp = sample(kind);
        const p = createPanel(container(doc), rsp, { mode, timeline: timelineWith(rsp), sceneIndex: 0 });
        p.update(0.2, { panelVisible: true });
        p.update(0.3, { panelVisible: true });
        panels.push(p);
      }
      await flush();
      assert.ok(tracker.active() > 0, 'panels registered listeners');
      for (const p of panels) p.destroy();
      await flush();
      assert.equal(tracker.active(), 0, `all listeners removed (${mode})`);
    }
  } finally {
    tracker.restore();
  }
});

test('unknown kinds render a notice instead of throwing', () => {
  const box = container(doc);
  const p = createPanel(box, { panel: { kind: 'hologram' } }, { mode: 'live' });
  assert.match(p.el.textContent, /could not be displayed/);
  assert.equal(factoryFor('__proto__', { panel: { kind: '__proto__' } }), null);
  p.destroy();
});

test('factoryFor follows the resolved media kind', () => {
  assert.equal(factoryFor('image', { panel: {}, media: { kind: 'video' } }), createManimPanel);
  assert.equal(factoryFor('manim', { panel: {}, media: { kind: 'image' } }), createImagePanel);
  assert.equal(factoryFor('figure', { panel: {}, media: { kind: 'gif' } }), createGifPanel);
  assert.equal(factoryFor('terminal', { panel: {}, media: { kind: 'video' } }).name, 'createTerminalPanel');
});

test('visibility follows sceneState.panelVisible, else t >= show_at', () => {
  const rsp = { ...sample('terminal'), show_at: 3 };
  const p = createPanel(container(doc), rsp, { mode: 'live', timeline: timelineWith(rsp), sceneIndex: 0 });
  assert.equal(p.el.dataset.visible, 'false');
  assert.equal(p.el.getAttribute('aria-hidden'), 'true');
  p.update(1, {});
  assert.equal(p.el.dataset.visible, 'false');
  p.update(3, {});
  assert.equal(p.el.dataset.visible, 'true');
  assert.equal(p.el.getAttribute('aria-hidden'), 'false');
  p.update(5, { panelVisible: false });
  assert.equal(p.el.dataset.visible, 'false');
  p.update(0, { panelVisible: true });
  assert.equal(p.el.dataset.visible, 'true');
  p.destroy();
});

test('terminal reveals lines deterministically and only as text', () => {
  const rsp = {
    show_at: 1,
    panel: { kind: 'terminal', title: 'Run it', terminal: { command: 'python ohm.py', output: 'V = 10\n<img src=x onerror=alert(1)>\nI = 2' } },
  };
  const tl = timelineWith(rsp);
  tl.scenes[0].duration = 9;
  const p = createPanel(container(doc), rsp, { mode: 'render', timeline: tl, sceneIndex: 0 });
  const visibleLines = () => [...p.el.querySelectorAll('.ap-term-out')].filter((l) => !l.hidden).map((l) => l.textContent);
  assert.equal(p.el.querySelector('.ap-term-command').textContent, 'python ohm.py');
  assert.equal(p.el.querySelector('.ap-term-prompt').textContent, '$ ');
  const times = terminalLineTimes(1, 9, 3);
  p.update(0.5, {});
  assert.deepEqual(visibleLines(), []);
  p.update(times[0], {});
  assert.deepEqual(visibleLines(), ['V = 10']);
  p.update(times[2], {});
  assert.deepEqual(visibleLines(), ['V = 10', '<img src=x onerror=alert(1)>', 'I = 2']);
  assert.equal(p.el.querySelector('img'), null, 'markup is never parsed');
  p.update(times[0] - 0.1, {});
  assert.deepEqual(visibleLines(), [], 'seeking back hides lines again');
  // the player's schedule is authoritative when it provides the revealed lines
  p.update(8, { terminalLines: ['V = 10'] });
  assert.deepEqual(visibleLines(), ['V = 10']);
  p.update(8, { terminalLines: 2 });
  assert.equal(visibleLines().length, 2);
  assert.ok(p.el.querySelector('.ap-term-cursor'));
  p.destroy();
});

test('terminal output beyond the cap ends with an "N more lines" note, revealed with the last line', () => {
  const output = Array.from({ length: 250 }, (_, i) => `row ${i}`).join('\n');
  const rsp = { show_at: 0, panel: { kind: 'terminal', terminal: { command: 'seq 250', output } } };
  const tl = timelineWith(rsp);
  tl.scenes[0].duration = 60;
  const p = createPanel(container(doc), rsp, { mode: 'render', timeline: tl, sceneIndex: 0 });
  const outs = p.el.querySelectorAll('.ap-term-out');
  assert.equal(outs.length, TERMINAL_MAX_LINES);
  const more = p.el.querySelector('.ap-term-more');
  assert.equal(more.textContent, '… 50 more lines not shown');
  const lines = Array.from({ length: TERMINAL_MAX_LINES }, (_, i) => `row ${i}`);
  p.update(30, { terminalLines: lines.slice(0, 199) });
  assert.equal(more.hidden, true);
  assert.equal(outs[198].lastElementChild.className, 'ap-term-cursor');
  p.update(59, { terminalLines: lines });
  assert.equal(more.hidden, false, 'shown together with the last revealed line');
  assert.equal(more.lastElementChild.className, 'ap-term-cursor');
  assert.equal(p.el.querySelector('.ap-term-screen').dataset.lines, String(TERMINAL_MAX_LINES));
  p.update(59, { terminalLines: 999 });
  assert.equal(p.el.querySelector('.ap-term-screen').dataset.lines, String(TERMINAL_MAX_LINES), 'clamped to the cap');
  p.destroy();
  const short = createPanel(container(doc), sample('terminal'), { mode: 'render', timeline: timelineWith(sample('terminal')), sceneIndex: 0 });
  assert.equal(short.el.querySelector('.ap-term-more'), null);
  short.destroy();
});

test('quiz teaser reveals the answer near the end, deterministic in t', () => {
  const rsp = { show_at: 0, panel: { kind: 'quiz', quiz: { question: 'Which law relates V, I and R?', options: ['Faraday', 'Ohm', 'Lenz'], correct_index: 1 } } };
  const tl = timelineWith(rsp);
  const reveal = quizTeaserRevealTime(0, 20);
  const p = createPanel(container(doc), rsp, { mode: 'render', timeline: tl, sceneIndex: 0 });
  const opts = () => [...p.el.querySelectorAll('.ap-quiz-option')];
  assert.equal(p.el.querySelector('.ap-quiz-question').textContent, 'Which law relates V, I and R?');
  assert.deepEqual(opts().map((o) => o.querySelector('.ap-quiz-letter').textContent), ['A', 'B', 'C']);
  assert.equal(p.el.querySelector('button'), null, 'render mode is not interactive');
  p.update(reveal - 0.01, {});
  assert.equal(p.el.querySelectorAll('.is-correct').length, 0);
  p.update(reveal, {});
  assert.ok(opts()[1].classList.contains('is-correct'));
  assert.ok(opts()[0].classList.contains('is-dim') && opts()[2].classList.contains('is-dim'));
  assert.match(p.el.querySelector('.ap-quiz-status').textContent, /Answer: B/);
  p.update(2, {});
  assert.equal(p.el.querySelectorAll('.is-correct').length, 0, 'seeking back hides the answer');
  p.destroy();
});

test('quiz teaser guesses in live mode', () => {
  const rsp = { show_at: 0, panel: { kind: 'quiz', quiz: { question: 'Q', options: ['x', 'y'], correct_index: 0 } } };
  const p = createPanel(container(doc), rsp, { mode: 'live', timeline: timelineWith(rsp), sceneIndex: 0 });
  const buttons = [...p.el.querySelectorAll('button.ap-quiz-option')];
  assert.equal(buttons.length, 2);
  buttons[1].click();
  assert.ok(buttons[1].classList.contains('is-guess'));
  p.update(19, {});
  assert.ok(buttons[1].classList.contains('is-wrong'));
  assert.ok(buttons[0].classList.contains('is-correct'));
  assert.ok(buttons.every((b) => b.disabled));
  p.destroy();
});

const CONCEPTS = [
  { id: 'charge', title: 'Charge & current', kind: 'prerequisite' },
  { id: 'ohm', title: "Ohm's law", depends_on: ['charge'] },
  { id: 'power', title: '<script>alert(1)</script> Power', depends_on: ['ohm'] },
  { id: 'series', title: 'Series circuits', depends_on: ['ohm'] },
];

test('skill tree renders states, prerequisites and safe labels', () => {
  const rsp = { show_at: 0, panel: { kind: 'skill_tree' } };
  const tl = timelineWith(rsp, { concept_map: CONCEPTS });
  const p = createPanel(container(doc), rsp, {
    mode: 'render',
    timeline: tl,
    sceneIndex: 0,
    conceptState: { doneIds: new Set(['charge']), activeId: 'ohm' },
  });
  const svgEl = p.el.querySelector('svg.ap-tree-svg');
  assert.ok(svgEl);
  assert.match(svgEl.getAttribute('viewBox'), /^0 0 [\d.]+ [\d.]+$/, 'case-sensitive viewBox attribute');
  assert.equal(svgEl.getAttribute('preserveAspectRatio'), 'xMidYMid meet');
  const nodes = new Map([...p.el.querySelectorAll('.ap-tree-node')].map((n) => [n.dataset.concept, n]));
  assert.equal(nodes.size, 4);
  assert.equal(nodes.get('charge').dataset.state, 'done');
  assert.equal(nodes.get('charge').dataset.kind, 'prerequisite');
  assert.equal(nodes.get('ohm').dataset.state, 'active');
  assert.equal(nodes.get('power').dataset.state, 'todo');
  assert.ok(nodes.get('charge').querySelector('.ap-tree-badge'), 'done badge');
  assert.equal(p.el.querySelectorAll('.ap-tree-edge').length, 3);
  assert.equal(p.el.querySelector('script'), null);
  assert.match(nodes.get('power').textContent, /<script>/);
  assert.equal(p.el.querySelectorAll('.ap-tree-key').length, 4);
  for (const el of p.el.querySelectorAll('[id]')) assert.fail(`unexpected id attribute ${el.id}`);
  p.destroy();
});

test('skill tree derives concept state from the timeline when ctx has none', () => {
  const rsp = { show_at: 0, panel: { kind: 'skill_tree' } };
  const tl = timelineWith(rsp, { concept_map: CONCEPTS });
  tl.scenes = [
    { scene_id: 'a', index: 0, concept_id: 'charge', duration: 5 },
    { scene_id: 'b', index: 1, concept_id: 'ohm', duration: 5 },
    { scene_id: 'c', index: 2, concept_id: 'power', duration: 5, side_panel: rsp },
  ];
  const st = deriveConceptState(tl, 2);
  assert.deepEqual([...st.doneIds].sort(), ['charge', 'ohm']);
  assert.equal(st.activeId, 'power');
  const p = createPanel(container(doc), rsp, { mode: 'live', timeline: tl, sceneIndex: 2 });
  const states = Object.fromEntries([...p.el.querySelectorAll('.ap-tree-node')].map((n) => [n.dataset.concept, n.dataset.state]));
  assert.deepEqual(states, { charge: 'done', ohm: 'done', power: 'active', series: 'todo' });
  p.destroy();
});

test('skill tree focuses on the neighbourhood of the active concept in big maps', () => {
  const big = Array.from({ length: 30 }, (_, i) => ({ id: `k${i}`, title: `Concept ${i}`, depends_on: i ? [`k${i - 1}`] : [] }));
  const rsp = { show_at: 0, panel: { kind: 'skill_tree' } };
  const p = createPanel(container(doc), rsp, {
    mode: 'render',
    timeline: timelineWith(rsp, { concept_map: big }),
    sceneIndex: 0,
    conceptState: { doneIds: [], activeId: 'k15' },
  });
  assert.equal(p.el.querySelectorAll('.ap-tree-node').length, 4);
  assert.match(p.el.querySelector('.ap-tree-focus').textContent, /Showing 4 of 30/);
  p.destroy();
});

test('skill tree draws wide, shallow maps left-to-right with right-pointing arrows and a capped upscale', () => {
  const prereqs = Array.from({ length: 8 }, (_, i) => ({ id: `p${i}`, title: `Prerequisite ${i}`, kind: 'prerequisite' }));
  const map = [...prereqs, { id: 'core', title: 'Core idea', depends_on: prereqs.map((p) => p.id) }];
  const rsp = { show_at: 0, panel: { kind: 'skill_tree' } };
  const p = createPanel(container(doc), rsp, {
    mode: 'render',
    timeline: timelineWith(rsp, { concept_map: map }),
    sceneIndex: 0,
    conceptState: { doneIds: [], activeId: 'core' },
  });
  const svgEl = p.el.querySelector('svg.ap-tree-svg');
  assert.equal(svgEl.dataset.orientation, 'lr');
  const [, , vbW, vbH] = svgEl.getAttribute('viewBox').split(' ').map(Number);
  assert.equal(svgEl.style.maxWidth, `${Math.ceil(vbW * MAX_UPSCALE)}px`);
  assert.equal(svgEl.style.maxHeight, `${Math.ceil(vbH * MAX_UPSCALE)}px`);
  assert.equal(p.el.querySelectorAll('.ap-tree-node').length, 9, 'the whole map is legible: no focus');
  assert.equal(p.el.querySelector('.ap-tree-focus'), null);
  // arrow heads point right: the tip (last vertex) is to the right of the base
  for (const a of p.el.querySelectorAll('.ap-tree-arrow')) {
    const nums = a.getAttribute('d').match(/-?[\d.]+/g).map(Number);
    assert.ok(nums[4] > nums[0] && nums[4] > nums[2] && nums[0] === nums[2], a.getAttribute('d'));
  }
  p.destroy();
});

test('focusAnchor: active concept, else the next learnable one, else the last done one', () => {
  const map = [
    { id: 'a' },
    { id: 'b', depends_on: ['a'] },
    { id: 'c', depends_on: ['b'] },
    { id: 'd', depends_on: ['a', 'zzz'] },
  ];
  assert.equal(focusAnchor(map, { doneIds: new Set(), activeId: 'c' }), 'c');
  assert.equal(focusAnchor(map, { doneIds: new Set(), activeId: 'nope' }), 'a');
  assert.equal(focusAnchor(map, { doneIds: new Set(['a']), activeId: null }), 'b');
  assert.equal(focusAnchor(map, { doneIds: new Set(['a', 'b', 'c', 'd']), activeId: null }), 'd');
  assert.equal(focusAnchor([], { doneIds: new Set(), activeId: null }), null);
});

test('skill tree focuses by legibility, also without an active concept', () => {
  const chain = Array.from({ length: 30 }, (_, i) => ({ id: `k${i}`, title: `Concept ${i}`, depends_on: i ? [`k${i - 1}`] : [] }));
  const state = { doneIds: new Set(Array.from({ length: 10 }, (_, i) => `k${i}`)), activeId: null };
  const view = chooseView(chain, state, { width: 388, height: 580 });
  assert.ok(view.concepts.length < 30);
  assert.ok(view.concepts.some((c) => c.id === 'k10'), 'centred on the next concept to learn');
  assert.ok(view.layout.renderedFont >= MIN_LEGIBLE_FONT);
  // a small map is never focused
  const small = chain.slice(0, 5);
  assert.equal(chooseView(small, state, { width: 388, height: 580 }).concepts, small);
  // render: legend says what is shown
  const rsp = { show_at: 0, panel: { kind: 'skill_tree' } };
  const p = createPanel(container(doc), rsp, {
    mode: 'render',
    timeline: timelineWith(rsp, { concept_map: chain }),
    sceneIndex: 0,
    conceptState: state,
  });
  assert.match(p.el.querySelector('.ap-tree-focus').textContent, /^Showing \d+ of 30 concepts$/);
  assert.equal(p.el.querySelectorAll('.ap-tree-legend .ap-tree-focus').length, 1);
  p.destroy();
});

test('skill tree fuzz: random maps are legible (whole or focused) in the side-zone canvas', () => {
  let seed = 4242;
  const rnd = () => {
    seed = (seed * 1103515245 + 12345) & 0x7fffffff;
    return seed / 0x7fffffff;
  };
  for (let k = 0; k < 150; k++) {
    const n = 1 + Math.floor(rnd() * 30);
    const map = [];
    for (let i = 0; i < n; i++) {
      const deps = [];
      for (let j = Math.floor(rnd() * 3); j > 0 && i > 0; j--) deps.push(`n${Math.floor(rnd() * i)}`);
      map.push({ id: `n${i}`, title: rnd() < 0.5 ? `Concept number ${i} with a longer title` : `Topic ${i}`, depends_on: deps });
    }
    const active = rnd() < 0.5 ? `n${Math.floor(rnd() * n)}` : null;
    const view = chooseView(map, { doneIds: new Set(), activeId: active }, { width: 388, height: 580 });
    assert.ok(view.layout.renderedFont >= MIN_LEGIBLE_FONT, `map ${k} (${n} concepts): ${view.layout.renderedFont}px`);
    if (n <= 11) assert.equal(view.concepts.length, n, `map ${k}: small maps are shown whole`);
  }
});

test('skill tree without a concept map shows a notice', () => {
  const rsp = { show_at: 0, panel: { kind: 'skill_tree' } };
  const p = createPanel(container(doc), rsp, { mode: 'live', timeline: timelineWith(rsp), sceneIndex: 0 });
  assert.match(p.el.textContent, /not available/);
  p.destroy();
});

test('figure: safe src, alt, caption, decoded in ready(), no media hole', async () => {
  const decoded = [];
  const original = w.HTMLImageElement.prototype.decode;
  w.HTMLImageElement.prototype.decode = function decode() {
    decoded.push(this.getAttribute('src'));
    return Promise.resolve();
  };
  try {
    const rsp = {
      show_at: 0,
      panel: { kind: 'figure', figure_id: 'f1', title: 'Series **circuit**' },
      media: { kind: 'image', url: '/media/assets/figure/abc/x.png', width: 800, height: 600, attribution: 'Source: page 3' },
    };
    const p = createPanel(container(doc), rsp, { mode: 'render', timeline: timelineWith(rsp), sceneIndex: 0 });
    const img = p.el.querySelector('img');
    assert.equal(img.getAttribute('src'), 'https://app.test/media/assets/figure/abc/x.png');
    assert.equal(img.getAttribute('alt'), 'Series circuit');
    assert.equal(p.el.querySelector('.ap-title').textContent, 'Series circuit');
    assert.equal(p.el.querySelector('figcaption').textContent, 'Source: page 3');
    assert.equal(p.mediaRect, undefined, 'static images are part of the screenshot');
    await p.ready();
    assert.deepEqual(decoded, ['https://app.test/media/assets/figure/abc/x.png']);
    p.destroy();

    const evil = { ...rsp, media: { kind: 'image', url: 'javascript:alert(1)' } };
    const q = createPanel(container(doc), evil, { mode: 'live', timeline: timelineWith(evil), sceneIndex: 0 });
    assert.equal(q.el.querySelector('img').getAttribute('src'), 'about:blank');
    q.destroy();

    const missing = { show_at: 0, panel: { kind: 'image', image_prompt: 'x' }, media: null };
    const m = createPanel(container(doc), missing, { mode: 'render' });
    assert.match(m.el.textContent, /not been generated/);
    await m.ready();
    m.destroy();
  } finally {
    w.HTMLImageElement.prototype.decode = original;
  }
});

test('gif: hotlink with attribution in live mode, title only in render mode', () => {
  const rsp = {
    show_at: 0,
    panel: { kind: 'gif', gif_query: 'resistor', title: 'Feel the resistance' },
    media: { kind: 'gif', url: 'https://media.giphy.com/media/abc/giphy.gif', attribution: 'Powered by GIPHY', link_url: 'https://giphy.com/gifs/abc', render_in_mp4: false },
  };
  const live = createPanel(container(doc), rsp, { mode: 'live' });
  assert.equal(live.el.querySelector('img').getAttribute('src'), 'https://media.giphy.com/media/abc/giphy.gif');
  assert.equal(live.el.querySelector('img').getAttribute('referrerpolicy'), 'no-referrer');
  const link = live.el.querySelector('a.ap-gif-credit');
  assert.equal(link.textContent, 'Powered by GIPHY');
  assert.equal(link.getAttribute('href'), 'https://giphy.com/gifs/abc');
  assert.match(link.getAttribute('rel'), /noopener/);
  live.destroy();
  const noLink = createPanel(container(doc), { ...rsp, media: { ...rsp.media, link_url: 'javascript:alert(1)', attribution: null } }, { mode: 'live' });
  assert.equal(noLink.el.querySelector('a'), null);
  assert.equal(noLink.el.querySelector('.ap-gif-credit').textContent, 'Powered by GIPHY');
  noLink.destroy();
  const render = createPanel(container(doc), rsp, { mode: 'render' });
  assert.ok(render.el.classList.contains('ap-panel--title-only'));
  assert.equal(render.el.querySelector('img'), null);
  assert.equal(render.el.querySelector('.ap-title').textContent, 'Feel the resistance');
  render.destroy();
});

test('manim video: render-mode hole and stage-pixel media rect', async () => {
  const stage = doc.createElement('div');
  stage.className = 'ap-stage';
  doc.body.appendChild(stage);
  rect(stage, 100, 50, 960, 540); // stage scaled by 0.5
  const box = doc.createElement('div');
  stage.appendChild(box);
  const rsp = {
    show_at: 2,
    panel: { kind: 'manim', manim: { template: 'equation_steps' } },
    media: { kind: 'video', url: '/media/assets/manim/k/v.mp4', duration: 6, width: 1280, height: 720, end_behavior: 'freeze', fit: 'contain' },
  };
  const p = createPanel(box, rsp, { mode: 'render', timeline: timelineWith(rsp), sceneIndex: 0 });
  const video = p.el.querySelector('video');
  assert.equal(video.style.visibility, 'hidden');
  assert.ok(video.muted);
  assert.ok(video.hasAttribute('playsinline'));
  assert.ok(p.el.classList.contains('ap-panel--hole'));
  const mediaBox = p.el.querySelector('.ap-video-box');
  const backdrop = p.el.querySelector('.ap-backdrop');
  rect(backdrop, 100 + 0.5 * 1380, 50 + 0.5 * 140, 0.5 * 520, 0.5 * 860);
  rect(mediaBox, 100 + 0.5 * 1400, 50 + 0.5 * 200, 0.5 * 480, 0.5 * 270);
  p.update(1, {});
  assert.equal(p.mediaRect(), null, 'hidden before show_at');
  p.update(3, {});
  assert.deepEqual(p.mediaRect(), { x: 1400, y: 200, width: 480, height: 270 });
  assert.match(backdrop.style.clipPath, /^polygon\(evenodd, /);
  // letterboxed: a square box shows a 16:9 video in its middle
  rect(mediaBox, 100 + 0.5 * 1400, 50 + 0.5 * 200, 0.5 * 480, 0.5 * 480);
  assert.deepEqual(p.mediaRect(), { x: 1400, y: 305, width: 480, height: 270 });
  await p.ready();
  const before = mediaLog.length;
  p.update(4, {});
  assert.equal(mediaLog.slice(before).filter((e) => e[0] === 'play').length, 0, 'render mode never plays');
  p.destroy();
  assert.equal(video.hasAttribute('src'), false);
  assert.equal(p.mediaRect(), null);
  stage.remove();
});

test('manim video: live sync to the scene clock with freeze and loop', () => {
  const rsp = {
    show_at: 2,
    panel: { kind: 'manim', manim: { template: 'x' } },
    media: { kind: 'video', url: '/media/v.mp4', duration: 6, end_behavior: 'freeze' },
  };
  const p = createPanel(container(doc), rsp, { mode: 'live', timeline: timelineWith(rsp), sceneIndex: 0 });
  const video = p.el.querySelector('video');
  p.update(2.5, {}); // first frame: not advancing yet -> paused at 0.5
  assert.ok(video.paused);
  assert.equal(video.currentTime, 0.5);
  p.update(2.6, {}); // advancing, drift 0.1 < tolerance -> play, no seek
  assert.ok(!video.paused);
  assert.equal(video.currentTime, 0.5);
  video.currentTime = 0.6;
  p.update(5, {}); // jump (seek) -> paused and seeked to 3
  assert.ok(video.paused);
  assert.equal(video.currentTime, 3);
  p.update(5.1, {});
  assert.ok(!video.paused);
  p.update(9, {}); // jumped past the end -> freeze on the last frame
  p.update(9.1, {});
  assert.ok(video.paused);
  assert.ok(Math.abs(video.currentTime - 5.96) < 1e-9);
  p.update(1, {}); // before show_at
  assert.ok(video.paused);
  assert.equal(video.currentTime, 0);
  p.destroy();

  const loop = { ...rsp, media: { ...rsp.media, end_behavior: 'loop' } };
  const q = createPanel(container(doc), loop, { mode: 'live', timeline: timelineWith(loop), sceneIndex: 0 });
  q.update(9, {});
  assert.equal(q.el.querySelector('video').currentTime, 1);
  q.destroy();
});

test('containRect copies DOMRect fields (prototype getters) instead of spreading them', () => {
  const r = new w.DOMRect(100, 50, 400, 300);
  assert.deepEqual({ ...r }, {}, 'precondition: a DOMRect spread is empty');
  assert.deepEqual(containRect(r, 0, 0), { left: 100, top: 50, width: 400, height: 300 });
  assert.deepEqual(containRect(r, Number.NaN, 9), { left: 100, top: 50, width: 400, height: 300 });
  assert.deepEqual(containRect(r, 4, 1), { left: 100, top: 150, width: 400, height: 100 });
  assert.deepEqual(containRect(new w.DOMRect(1, 2, 0, 0), 16, 9), { left: 1, top: 2, width: 0, height: 0 });
});

test('manim video: unknown aspect (no width/height, no metadata) reports the whole box, never NaN', async () => {
  const stage = doc.createElement('div');
  stage.className = 'ap-stage';
  doc.body.appendChild(stage);
  stage.getBoundingClientRect = () => new w.DOMRect(100, 50, 960, 540); // stage scaled by 0.5
  const box = doc.createElement('div');
  stage.appendChild(box);
  // compose/timeline.py _panel_media: a panel override MediaRef has no width/height
  const rsp = { show_at: 0, panel: { kind: 'image', image_prompt: 'x' }, media: { kind: 'video', url: '/media/assets/up.mp4', fit: 'contain' } };
  const p = createPanel(box, rsp, { mode: 'render', timeline: timelineWith(rsp), sceneIndex: 0 });
  const video = p.el.querySelector('video');
  video._rs = 0; // metadata never loads (e.g. a codec the render Chromium cannot decode)
  const mediaBox = p.el.querySelector('.ap-video-box');
  mediaBox.getBoundingClientRect = () => new w.DOMRect(100 + 0.5 * 1400, 50 + 0.5 * 200, 0.5 * 480, 0.5 * 600);
  p.el.querySelector('.ap-backdrop').getBoundingClientRect = () => new w.DOMRect(100 + 0.5 * 1380, 50 + 0.5 * 140, 260, 430);
  p.update(1, {});
  const r = p.mediaRect();
  assert.deepEqual(r, { x: 1400, y: 200, width: 480, height: 600 });
  assert.ok(Object.values(r).every(Number.isFinite));
  assert.match(p.el.querySelector('.ap-backdrop').style.clipPath, /^polygon\(evenodd, /, 'hole cut for the whole box');
  // a collapsed box yields null, not a zero/NaN rect
  mediaBox.getBoundingClientRect = () => new w.DOMRect(0, 0, 0, 0);
  assert.equal(p.mediaRect(), null);
  p.destroy();
  stage.remove();
});

test('RateEstimator and quantizeRate', () => {
  const est = new RateEstimator();
  assert.equal(est.sample(0, 0), null);
  assert.equal(est.sample(0.15, 100), null, 'needs RATE_MIN_SPAN_MS of wall time');
  assert.ok(Math.abs(est.sample(0.375, 250) - 1.5) < 1e-9);
  for (let wall = 266; wall < 3000; wall += 16) est.sample(0.375 + ((wall - 250) / 1000) * 2, wall);
  assert.ok(Math.abs(est.sample(0.375 + (2750 / 1000) * 2, 3000) - 2) < 0.05, 'the window follows a speed change');
  est.reset();
  assert.equal(est.sample(10, 5000), null);
  assert.equal(quantizeRate(1.52), 1.5);
  assert.equal(quantizeRate(1.98), 2);
  assert.equal(quantizeRate(0.1), MIN_RATE);
  assert.equal(quantizeRate(9), MAX_RATE);
  assert.equal(quantizeRate(Number.NaN), 1);
});

/**
 * Drive a live manim panel at `rate` x real time for `seconds` (60 fps), with a fake wall clock and
 * a stub <video> whose currentTime advances at its own playbackRate while playing.
 * @returns {{ seeks: number, video: HTMLVideoElement }}
 */
function simulatePlayback(t, rate, { seconds = 10, frameInfo = false } = {}) {
  let wall = 1000;
  t.mock.method(performance, 'now', () => wall);
  const rsp = { show_at: 0, panel: { kind: 'manim', manim: { template: 'x' } }, media: { kind: 'video', url: '/m.mp4', duration: 120 } };
  const tl = timelineWith(rsp);
  tl.scenes[0].duration = 200;
  const p = createPanel(container(doc), rsp, { mode: 'live', timeline: tl, sceneIndex: 0 });
  const video = p.el.querySelector('video');
  const dt = 1000 / 60;
  let sceneT = 0.5;
  video._ct = 0.5;
  p.update(sceneT, {}, frameInfo ? { playing: true, rate, seeked: false } : undefined);
  const before = mediaLog.length;
  for (let i = 0; i < seconds * 60; i++) {
    wall += dt;
    sceneT += (rate * dt) / 1000;
    if (!video.paused) video._ct += (video.playbackRate * dt) / 1000;
    p.update(sceneT, {}, frameInfo ? { playing: true, rate, seeked: false } : undefined);
  }
  const seeks = mediaLog.slice(before).filter((e) => e[0] === 'seek').length;
  const result = { seeks, video, rate: video.playbackRate, drift: Math.abs(video.currentTime - sceneT) };
  p.destroy();
  return result;
}

test('manim video follows the playback speed (estimated) instead of seeking every few frames', (t) => {
  for (const rate of [0.25, 0.75, 1, 1.5, 2, 4]) {
    const r = simulatePlayback(t, rate);
    assert.equal(r.rate, rate, `playbackRate follows ${rate}x`);
    assert.ok(r.seeks <= 1, `${rate}x: at most one corrective seek (${r.seeks})`);
    assert.ok(r.drift < 0.3 * Math.max(1, rate), `${rate}x: in sync (${r.drift.toFixed(3)} s)`);
  }
});

test('manim video uses the player frame rate when it is passed to update()', (t) => {
  const r = simulatePlayback(t, 2, { seconds: 2, frameInfo: true });
  assert.equal(r.rate, 2);
  assert.equal(r.seeks, 0, 'no drift at all: the rate is known from the first frame');
});

test('manim video pauses on frame.playing === false and re-syncs on frame.seeked', () => {
  const rsp = { show_at: 0, panel: { kind: 'manim', manim: { template: 'x' } }, media: { kind: 'video', url: '/m.mp4', duration: 30 } };
  const p = createPanel(container(doc), rsp, { mode: 'live', timeline: timelineWith(rsp), sceneIndex: 0 });
  const video = p.el.querySelector('video');
  p.update(1, {}, { playing: true, rate: 1, seeked: false });
  p.update(1.02, {}, { playing: true, rate: 1, seeked: false });
  assert.equal(video.paused, false);
  p.update(1.04, {}, { playing: false, rate: 1, seeked: false });
  assert.equal(video.paused, true, 'the player paused');
  video._ct = 1.04;
  p.update(1.06, {}, { playing: true, rate: 1, seeked: false });
  assert.equal(video.paused, false);
  p.update(1.1, {}, { playing: true, rate: 1, seeked: true }); // a small seek still re-syncs exactly
  assert.equal(video.paused, true);
  assert.equal(video.currentTime, 1.1);
  p.destroy();
});

test('manim panel without rendered media shows a notice and no hole', () => {
  const rsp = { show_at: 0, panel: { kind: 'manim', manim: { template: 'x' } }, media: null };
  const p = createPanel(container(doc), rsp, { mode: 'render' });
  assert.match(p.el.textContent, /not been rendered/);
  assert.equal(p.mediaRect(), null);
  p.destroy();
});

test('chart panel: render mode is static, live mode animates on first show', async () => {
  FakeChart.instances.length = 0;
  const rsp = sample('chart');
  const r = createPanel(container(doc), rsp, { mode: 'render', timeline: timelineWith(rsp), sceneIndex: 0 });
  r.update(0, {});
  await r.ready();
  assert.equal(FakeChart.instances.length, 1);
  const cfg = FakeChart.instances[0].config;
  assert.equal(cfg.options.animation, false);
  assert.equal(cfg.options.responsive, false);
  assert.equal(cfg.options.devicePixelRatio, 1);
  assert.ok(FakeChart.instances[0].canvas.width > 0);
  r.destroy();
  assert.ok(FakeChart.instances[0].destroyed);

  const hidden = { ...rsp, show_at: 5 };
  const l = createPanel(container(doc), hidden, { mode: 'live', timeline: timelineWith(hidden), sceneIndex: 0 });
  await l.ready();
  l.update(1, {});
  await flush();
  assert.equal(FakeChart.instances.length, 1, 'not created while hidden');
  l.update(5, {});
  l.update(5.1, {});
  await flush();
  assert.equal(FakeChart.instances.length, 2, 'created once when shown');
  assert.equal(typeof FakeChart.instances[1].config.options.animation, 'object');
  l.destroy();
  assert.ok(FakeChart.instances[1].destroyed);
});
