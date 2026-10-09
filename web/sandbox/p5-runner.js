// @ts-check
/**
 * p5.js sandbox runner (classic script; loaded by web/sandbox/p5.html after p5.min.js).
 *
 * Protocol with the parent (the player), origin-checked both ways:
 *   sandbox -> parent: {type:'ready'} once the page has loaded, {type:'heartbeat'} every second,
 *                      {type:'error', message} (always posted with targetOrigin = the app origin)
 *   parent -> sandbox: {type:'run', code}, {type:'pause'}, {type:'resume'} accepted ONLY when
 *                      ev.source === window.parent and ev.origin === the app origin. The app origin
 *                      is `?origin=` when given (validated: it must equal this page's own URL origin)
 *                      or this page's URL origin (the sandbox is served by the app itself).
 *                      One run per frame; reload the iframe to run new code.
 * The sketch runs in p5 global mode: its code is injected as an inline script element (allowed by
 * the sandbox CSP), then `new p5()` starts it. The canvas is CSS-scaled to fit the iframe (p5 maps
 * pointer coordinates through the CSS scale). Nothing here touches cookies or storage.
 */
(function (global) {
  'use strict';

  var MAX_CODE_CHARS = 64 * 1024;
  var MAX_MESSAGE_CHARS = 500;
  var HEARTBEAT_MS = 1000;

  /**
   * Validate the ?origin= parameter: a bare http(s) origin equal to this page's URL origin.
   * @param {unknown} raw
   * @param {string | null | undefined} selfOrigin  location.origin (the URL's origin, not the opaque one)
   * @returns {string | null}
   */
  function validateOrigin(raw, selfOrigin) {
    if (typeof raw !== 'string' || !raw || raw.length > 256) return null;
    var u;
    try {
      u = new URL(raw);
    } catch (e) {
      return null;
    }
    if (u.protocol !== 'https:' && u.protocol !== 'http:') return null;
    if (u.username || u.password) return null;
    if (u.origin === 'null' || raw.replace(/\/$/, '') !== u.origin) return null;
    if (selfOrigin && selfOrigin !== 'null' && u.origin !== selfOrigin) return null;
    return u.origin;
  }

  /**
   * @typedef {object} RunnerEnv
   * @property {any} win            the sandbox window
   * @property {any} parentWin      window.parent
   * @property {Document} doc
   * @property {string} expectedOrigin
   * @property {() => any} getP5    returns the p5 constructor
   */

  /**
   * @param {RunnerEnv} env
   */
  function createRunner(env) {
    var started = false;
    var paused = false;
    /** @type {any} */
    var instance = null;
    /** @type {any} */
    var heartbeat = null;

    /** @param {Record<string, unknown>} msg */
    function post(msg) {
      try {
        env.parentWin.postMessage(msg, env.expectedOrigin);
      } catch (e) {
        /* parent gone */
      }
    }

    /** @param {unknown} message */
    function postError(message) {
      post({ type: 'error', message: String(message || 'Script error').slice(0, MAX_MESSAGE_CHARS) });
    }

    /** @param {any} ev */
    function onError(ev) {
      var msg = ev && (ev.message || (ev.error && ev.error.message));
      postError(msg || 'Script error');
    }

    /** @param {any} ev */
    function onRejection(ev) {
      var r = ev && ev.reason;
      postError((r && r.message) || r || 'Unhandled promise rejection');
    }

    /** Scale the sketch canvas (CSS only) to fit the iframe, preserving its aspect ratio. */
    function fitCanvas() {
      var c = env.doc.querySelector('canvas');
      if (!c) return;
      var w = Number(env.win.width) || c.width;
      var h = Number(env.win.height) || c.height;
      var vw = env.win.innerWidth;
      var vh = env.win.innerHeight;
      if (!(w > 0 && h > 0 && vw > 0 && vh > 0)) return;
      var scale = Math.min(vw / w, vh / h);
      var cw = Math.floor(w * scale) + 'px';
      var ch = Math.floor(h * scale) + 'px';
      if (c.style.width !== cw) c.style.width = cw;
      if (c.style.height !== ch) c.style.height = ch;
    }

    /** @param {string} code */
    function run(code) {
      var P5 = env.getP5();
      if (typeof P5 !== 'function') {
        postError('p5.js failed to load');
        return;
      }
      P5.disableFriendlyErrors = true;
      if (P5.prototype && typeof P5.prototype.registerMethod === 'function') {
        P5.prototype.registerMethod('post', fitCanvas);
      }
      var threw = false;
      var watch = function () {
        threw = true;
      };
      env.win.addEventListener('error', watch);
      var script = env.doc.createElement('script');
      script.textContent = code + '\n//# sourceURL=aadhi-sketch.js';
      try {
        (env.doc.body || env.doc.documentElement).appendChild(script);
      } finally {
        script.remove();
        env.win.removeEventListener('error', watch);
      }
      if (threw) return; // the error listener already reported it
      if (typeof env.win.setup !== 'function' && typeof env.win.draw !== 'function') {
        postError('The sketch must define setup() or draw().');
        return;
      }
      try {
        instance = new P5();
      } catch (e) {
        postError(e && /** @type {any} */ (e).message ? /** @type {any} */ (e).message : e);
        return;
      }
      applyPause();
      fitCanvas();
    }

    /** Pause/resume the draw loop (heartbeats continue, so the watchdog stays satisfied). */
    function applyPause() {
      if (!instance) return;
      try {
        if (paused && typeof instance.noLoop === 'function') instance.noLoop();
        else if (!paused && typeof instance.loop === 'function') instance.loop();
      } catch (e) {
        /* sketch not set up yet */
      }
    }

    /** @param {any} ev */
    function onMessage(ev) {
      if (ev.source !== env.parentWin || ev.origin !== env.expectedOrigin) return;
      var data = ev.data;
      if (!data || typeof data !== 'object') return;
      if (data.type === 'pause' || data.type === 'resume') {
        paused = data.type === 'pause';
        applyPause();
        return;
      }
      if (data.type !== 'run') return;
      if (typeof data.code !== 'string' || !data.code.trim()) {
        postError('No sketch code was provided.');
        return;
      }
      if (data.code.length > MAX_CODE_CHARS) {
        postError('The sketch is too large.');
        return;
      }
      if (started) {
        postError('A sketch is already running; reload the sandbox to run new code.');
        return;
      }
      started = true;
      run(data.code);
    }

    function start() {
      env.win.addEventListener('message', onMessage);
      env.win.addEventListener('error', onError);
      env.win.addEventListener('unhandledrejection', onRejection);
      env.win.addEventListener('resize', fitCanvas);
      heartbeat = env.win.setInterval(function () {
        post({ type: 'heartbeat' });
        fitCanvas();
      }, HEARTBEAT_MS);
      // After `load`, p5's own global-mode auto-init has run (and found no sketch), so our
      // `new p5()` starts immediately and is the only instance.
      if (env.doc.readyState === 'complete') post({ type: 'ready' });
      else env.win.addEventListener('load', postReady);
    }

    function postReady() {
      env.win.removeEventListener('load', postReady);
      post({ type: 'ready' });
    }

    function stop() {
      env.win.removeEventListener('message', onMessage);
      env.win.removeEventListener('error', onError);
      env.win.removeEventListener('unhandledrejection', onRejection);
      env.win.removeEventListener('resize', fitCanvas);
      env.win.removeEventListener('load', postReady);
      if (heartbeat !== null) env.win.clearInterval(heartbeat);
      heartbeat = null;
      if (instance && typeof instance.remove === 'function') {
        try {
          instance.remove();
        } catch (e) {
          /* ignore */
        }
      }
      instance = null;
    }

    return {
      start: start,
      stop: stop,
      fitCanvas: fitCanvas,
      /** @returns {boolean} */
      isStarted: function () {
        return started;
      },
      /** @returns {boolean} */
      isPaused: function () {
        return paused;
      },
    };
  }

  if (global.__AADHI_P5_TEST__) {
    // Test seam (node --test + jsdom): expose the factory instead of auto-starting.
    global.__aadhiP5Sandbox = { createRunner: createRunner, validateOrigin: validateOrigin };
    return;
  }

  var param = new URLSearchParams(global.location.search).get('origin');
  var expected = validateOrigin(param === null ? global.location.origin : param, global.location.origin);
  if (!expected) {
    console.error('p5 sandbox: invalid ?origin= parameter (or unknown page origin); refusing to run.');
    return;
  }
  if (!global.parent || global.parent === global) {
    console.error('p5 sandbox: must be embedded in the Aadhi player.');
    return;
  }
  createRunner({
    win: global,
    parentWin: global.parent,
    doc: global.document,
    expectedOrigin: expected,
    getP5: function () {
      return global.p5;
    },
  }).start();
})(/** @type {any} */ (typeof window !== 'undefined' ? window : globalThis));
