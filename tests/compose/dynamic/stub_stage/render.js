// Stub of web/js/dynamic/render.js for the QA tests: window.aadhiStage (stage contract v3) around the stub stage.
// Token in the fragment, timeline from GET /api/render/timeline (Bearer), a timer guard installed after `ready`.
// window.STUB (served as /stub-config.js) selects faults: throwAtBoot, bootError, version, strayTimer (see stage.js
// for the rest).
import { createDynamicStage } from '/web/js/dynamic/stage.js';

const cfg = window.STUB || {};
if (cfg.throwAtBoot) throw new Error('stub module failed before defining window.aadhiStage');
const params = new URLSearchParams(location.hash.slice(1));
const token = params.get('token') || '';
history.replaceState(null, '', location.pathname);

let stage = null;

function installGuard() {
  /** @param {string} name */
  const trip = (name) => {
    console.error(`aadhi-guard: ${name} called after ready`);
    throw new Error(`aadhi-guard: ${name} is not allowed in render mode`);
  };
  window.setTimeout = /** @type {any} */ (() => trip('setTimeout'));
  window.setInterval = /** @type {any} */ (() => trip('setInterval'));
}

const ready = (async () => {
  if (cfg.bootError) {
    const err = new Error('stub boot failure');
    err.code = cfg.bootError;
    throw err;
  }
  const res = await fetch('/api/render/timeline', {
    headers: { Authorization: `Bearer ${token}`, Accept: 'application/json' },
    credentials: 'omit',
    cache: 'no-store',
  });
  if (!res.ok) {
    const err = new Error(`timeline HTTP ${res.status}`);
    err.code = 'timeline_http';
    throw err;
  }
  const timeline = await res.json();
  stage = createDynamicStage(document.getElementById('root'), timeline, { mode: 'render' });
  await document.fonts.ready;
  installGuard();
  return { webgl: false };
})();
ready.catch(() => {});

window.aadhiStage = {
  version: cfg.version === undefined ? 3 : cfg.version,
  ready,
  info: () => stage.info(),
  mediaPlan: () => [],
  seek: (frame) => stage.seek(frame),
  textBoxes: () => stage.textBoxes(),
  setLayers: (spec) => stage.setLayers(spec),
};
