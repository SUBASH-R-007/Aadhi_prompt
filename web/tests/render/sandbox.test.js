import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM, VirtualConsole } from 'jsdom';

const RUNNER = readFileSync(new URL('../../sandbox/p5-runner.js', import.meta.url), 'utf8');
const PAGE = readFileSync(new URL('../../sandbox/p5.html', import.meta.url), 'utf8');
const APP = 'https://app.test';

/** Objects posted from the jsdom realm have foreign prototypes: compare plain copies. */
const plain = (v) => JSON.parse(JSON.stringify(v));

/**
 * A sandbox window that runs inline scripts (like the sandboxed iframe) with a fake parent.
 * Storage and cookies throw when touched, proving the runner never uses them.
 */
function sandboxWindow({ origin = APP, testMode = true, embedded = true } = {}) {
  const dom = new JSDOM('<!doctype html><html><head></head><body></body></html>', {
    url: origin === null ? `${APP}/sandbox/p5` : `${APP}/sandbox/p5?origin=${encodeURIComponent(origin)}`,
    runScripts: 'dangerously',
    pretendToBeVisual: true,
    virtualConsole: new VirtualConsole(), // sketch errors are expected in some tests
  });
  const w = dom.window;
  const posted = [];
  const parent = { postMessage: (msg, target) => posted.push({ msg, target }) };
  if (embedded) Object.defineProperty(w, 'parent', { value: parent, configurable: true });
  const touched = [];
  for (const key of ['localStorage', 'sessionStorage', 'indexedDB']) {
    Object.defineProperty(w, key, {
      configurable: true,
      get() {
        touched.push(key);
        throw new Error(`${key} must not be used`);
      },
    });
  }
  Object.defineProperty(w.document, 'cookie', {
    configurable: true,
    get() {
      touched.push('cookie');
      return '';
    },
    set() {
      touched.push('cookie');
    },
  });
  const intervals = [];
  w.setInterval = (fn, ms) => {
    intervals.push({ fn, ms });
    return intervals.length;
  };
  w.clearInterval = () => {};
  const errors = [];
  w.console.error = (...a) => errors.push(a.join(' '));
  if (testMode) w.__AADHI_P5_TEST__ = true;
  w.eval(RUNNER);
  return { w, parent, posted, touched, intervals, errors };
}

/** Resolve once the window fired `load` (the runner posts `ready` only then). */
function loaded(w) {
  return w.document.readyState === 'complete'
    ? Promise.resolve()
    : new Promise((resolve) => w.addEventListener('load', () => setTimeout(resolve, 0), { once: true }));
}

/** Fake p5 constructor recording global-mode starts. */
function fakeP5(w) {
  const created = [];
  function P5() {
    created.push(this);
    if (typeof w.setup === 'function') w.setup();
    const c = w.document.createElement('canvas');
    c.width = 800;
    c.height = 600;
    w.document.body.appendChild(c);
    w.width = 800;
    w.height = 600;
  }
  P5.prototype.registerMethod = (name, fn) => {
    P5.registered = [name, fn];
  };
  P5.prototype.remove = function remove() {
    this.removed = true;
  };
  P5.prototype.noLoop = function noLoop() {
    this.looping = false;
  };
  P5.prototype.loop = function loop() {
    this.looping = true;
  };
  P5.created = created;
  return P5;
}

/** Dispatch a message event with an arbitrary source/origin. */
function message(w, data, { source, origin }) {
  const ev = new w.Event('message');
  Object.defineProperty(ev, 'data', { value: data });
  Object.defineProperty(ev, 'source', { value: source });
  Object.defineProperty(ev, 'origin', { value: origin });
  w.dispatchEvent(ev);
}

function started(env) {
  const P5 = fakeP5(env.w);
  const runner = env.w.__aadhiP5Sandbox.createRunner({
    win: env.w,
    parentWin: env.parent,
    doc: env.w.document,
    expectedOrigin: APP,
    getP5: () => P5,
  });
  runner.start();
  return { runner, P5 };
}

test('validateOrigin accepts only the bare app origin', () => {
  const { w } = sandboxWindow();
  const { validateOrigin } = w.__aadhiP5Sandbox;
  assert.equal(validateOrigin(APP, APP), APP);
  assert.equal(validateOrigin(`${APP}/`, APP), APP);
  assert.equal(validateOrigin('http://localhost:8000', 'http://localhost:8000'), 'http://localhost:8000');
  for (const bad of ['', null, '*', 'null', 'javascript:alert(1)', 'data:text/html,x', `${APP}/path`, 'https://evil.test', 'https://user:pw@app.test', 'ftp://app.test', 'x'.repeat(300)]) {
    assert.equal(validateOrigin(bad, APP), null, String(bad));
  }
});

test('posts ready to the app origin only (after load) and sends heartbeats every second', async () => {
  const env = sandboxWindow();
  started(env);
  await loaded(env.w);
  assert.deepEqual(plain(env.posted[0]), { msg: { type: 'ready' }, target: APP });
  assert.equal(env.intervals.length, 1);
  assert.equal(env.intervals[0].ms, 1000);
  env.intervals[0].fn();
  assert.deepEqual(plain(env.posted.at(-1)), { msg: { type: 'heartbeat' }, target: APP });
  assert.ok(env.posted.every((p) => p.target === APP), 'never posts to "*"');
});

test('ignores run messages from other windows or origins', () => {
  const env = sandboxWindow();
  const { P5 } = started(env);
  const code = 'window.__ran = true; function setup() {}';
  message(env.w, { type: 'run', code }, { source: env.w, origin: APP }); // self, not the parent
  message(env.w, { type: 'run', code }, { source: {}, origin: APP });
  message(env.w, { type: 'run', code }, { source: env.parent, origin: 'https://evil.test' });
  message(env.w, { type: 'run', code }, { source: env.parent, origin: 'null' });
  message(env.w, 'run', { source: env.parent, origin: APP });
  assert.equal(env.w.__ran, undefined);
  assert.equal(P5.created.length, 0);
});

test('pause/resume from the parent toggle the draw loop, before or after the run', () => {
  const env = sandboxWindow();
  const { P5, runner } = started(env);
  const from = { source: env.parent, origin: APP };
  message(env.w, { type: 'pause' }, from);
  assert.ok(runner.isPaused());
  message(env.w, { type: 'run', code: 'function draw() {}' }, from);
  assert.equal(P5.created[0].looping, false, 'a pause received before the run is applied');
  message(env.w, { type: 'resume' }, from);
  assert.equal(P5.created[0].looping, true);
  message(env.w, { type: 'pause' }, { source: env.parent, origin: 'https://evil.test' });
  assert.equal(P5.created[0].looping, true, 'foreign origins cannot pause');
  message(env.w, { type: 'pause' }, from);
  assert.equal(P5.created[0].looping, false);
});

test('runs the sketch in global mode and fits the canvas to the frame', () => {
  const env = sandboxWindow();
  const { P5, runner } = started(env);
  Object.defineProperty(env.w, 'innerWidth', { value: 400, configurable: true });
  Object.defineProperty(env.w, 'innerHeight', { value: 400, configurable: true });
  message(env.w, { type: 'run', code: 'var setupCalls = 0; function setup() { setupCalls++; } function draw() {}' }, { source: env.parent, origin: APP });
  assert.equal(P5.created.length, 1);
  assert.equal(env.w.setupCalls, 1);
  assert.equal(P5.registered[0], 'post');
  const canvas = env.w.document.querySelector('canvas');
  assert.equal(canvas.style.width, '400px');
  assert.equal(canvas.style.height, '300px');
  assert.equal(env.w.document.querySelectorAll('script').length, 0, 'injected script element removed');
  assert.ok(runner.isStarted());
  message(env.w, { type: 'run', code: 'function setup() {}' }, { source: env.parent, origin: APP });
  assert.equal(P5.created.length, 1, 'one run per frame');
  assert.match(env.posted.at(-1).msg.message, /already running/);
  assert.deepEqual(env.touched, [], 'no storage or cookie access');
  runner.stop();
  assert.ok(P5.created[0].removed);
});

test('forwards sketch errors to the parent', () => {
  const env = sandboxWindow();
  const { P5 } = started(env);
  message(env.w, { type: 'run', code: 'function setup( {' }, { source: env.parent, origin: APP });
  assert.equal(P5.created.length, 0, 'syntax errors never start p5');
  const err = env.posted.find((p) => p.msg.type === 'error');
  assert.ok(err, 'error posted');
  assert.equal(err.target, APP);
  assert.ok(err.msg.message.length > 0 && err.msg.message.length <= 500);

  const env2 = sandboxWindow();
  const s2 = started(env2);
  message(env2.w, { type: 'run', code: 'var x = 1;' }, { source: env2.parent, origin: APP });
  assert.equal(s2.P5.created.length, 0);
  assert.match(env2.posted.at(-1).msg.message, /setup\(\) or draw\(\)/);

  const env3 = sandboxWindow();
  started(env3);
  message(env3.w, { type: 'run', code: '' }, { source: env3.parent, origin: APP });
  message(env3.w, { type: 'run', code: 'x'.repeat(70000) }, { source: env3.parent, origin: APP });
  const msgs = env3.posted.filter((p) => p.msg.type === 'error').map((p) => p.msg.message);
  assert.deepEqual(msgs, ['No sketch code was provided.', 'The sketch is too large.']);
});

test('auto-start refuses a foreign origin or an unembedded page', async () => {
  const foreign = sandboxWindow({ origin: 'https://evil.test', testMode: false });
  await loaded(foreign.w);
  assert.equal(foreign.posted.length, 0);
  assert.match(foreign.errors.join(), /invalid \?origin=/);
  const top = sandboxWindow({ testMode: false, embedded: false });
  await loaded(top.w);
  assert.equal(top.posted.length, 0);
  assert.match(top.errors.join(), /must be embedded/);
  const ok = sandboxWindow({ testMode: false });
  await loaded(ok.w);
  assert.deepEqual(plain(ok.posted[0]), { msg: { type: 'ready' }, target: APP });
});

test('without ?origin= the sandbox trusts only its own URL origin (served by the app)', async () => {
  const env = sandboxWindow({ origin: null, testMode: false });
  await loaded(env.w);
  assert.deepEqual(plain(env.posted[0]), { msg: { type: 'ready' }, target: APP });
  assert.equal(env.errors.length, 0);
});

test('p5.html loads classic same-origin scripts only', () => {
  const scripts = [...PAGE.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)];
  assert.deepEqual(
    scripts.map((s) => /src="([^"]+)"/.exec(s[1])[1]),
    ['/web/vendor/p5/p5.min.js', '/web/sandbox/p5-runner.js'],
  );
  for (const s of scripts) {
    assert.doesNotMatch(s[1], /type="module"/, 'module scripts would need CORS from an opaque origin');
    assert.equal(s[2].trim(), '');
  }
  assert.doesNotMatch(PAGE, /https?:\/\//, 'no external resources');
});
