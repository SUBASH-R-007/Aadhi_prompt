// @ts-check
/**
 * Test helper (not a test file) for the chart engine (`templates/_charts.js`) and `chart_build`: the real Chart.js (the
 * npm build of the version web/vendor carries) and the real math.js on the jsdom window, and a canvas whose 2D context
 * records every call (jsdom has no canvas). `_charts.js` loads both through `shared/libs.js loadLib`, which returns a
 * global that is already there, so nothing is fetched; `core.js canvas2d` asks for `CanvasRenderingContext2D`, which this
 * defines.
 */

import { createRequire } from 'node:module';
import { Chart, registerables } from 'chart.js';

Chart.register(...registerables);
const require = createRequire(import.meta.url);
const mathjs = require('mathjs');

/** The drawing methods of CanvasRenderingContext2D the recording context answers (each records its call). */
const METHODS = ['arc', 'arcTo', 'beginPath', 'bezierCurveTo', 'clearRect', 'clip', 'closePath', 'drawImage',
  'ellipse', 'fill', 'fillRect', 'fillText', 'lineTo', 'moveTo', 'quadraticCurveTo', 'rect', 'reset', 'resetTransform',
  'restore', 'rotate', 'roundRect', 'save', 'scale', 'setLineDash', 'setTransform', 'stroke', 'strokeRect',
  'strokeText', 'transform', 'translate'];

/**
 * A recording 2D context: the drawing methods record their name and arguments (`calls`), `measureText` gives 7 px per
 * character, gradients take colour stops, and every other property is plain data (so `ctx.length` is undefined, as on
 * a real context: Chart.js reads it to tell a context from a list of canvases).
 * @param {any} canvas
 */
export function recordingContext(canvas) {
  /** @type {any[][]} */
  const calls = [];
  /** @type {any} */
  const ctx = {
    canvas, calls, lineDash: [], fillStyle: '#000000', strokeStyle: '#000000', lineWidth: 1, font: '10px sans-serif',
    textAlign: 'start', textBaseline: 'alphabetic', globalAlpha: 1, lineCap: 'butt', lineJoin: 'miter', lineDashOffset: 0,
    measureText: (/** @type {string} */ s) => ({ width: String(s).length * 7 }),
    getLineDash() {
      return ctx.lineDash.slice();
    },
    getTransform: () => ({ a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 }),
    createLinearGradient: () => ({ addColorStop() {} }),
    createRadialGradient: () => ({ addColorStop() {} }),
    createPattern: () => ({}),
  };
  for (const name of METHODS) {
    ctx[name] = (/** @type {any[]} */ ...args) => {
      calls.push([name, ...args]);
      if (name === 'setLineDash') ctx.lineDash = Array.isArray(args[0]) ? args[0].slice() : [];
    };
  }
  return ctx;
}

/**
 * Put Chart.js, math.js and a recording canvas on the current jsdom window (call after installDom). Returns the
 * contexts made so far (one per canvas).
 * @returns {{contexts: any[], Chart: any}}
 */
export function installChartEnv() {
  const win = /** @type {any} */ (globalThis.window);
  /** @type {any[]} */
  const contexts = [];
  win.CanvasRenderingContext2D = class CanvasRenderingContext2D {};
  win.HTMLCanvasElement.prototype.getContext = function getContext(/** @type {string} */ type) {
    // only a chart's canvas: every other canvas of the stage (the backgrounds) keeps jsdom's "no context", as in the
    // other template tests, and falls back to DOM as it always does there
    if (type !== '2d' || !this.classList.contains('dyn-chart-canvas')) return null;
    if (!this.__ctx) {
      this.__ctx = recordingContext(this);
      contexts.push(this.__ctx);
    }
    return this.__ctx;
  };
  win.Chart = Chart;
  win.math = mathjs;
  return { contexts, Chart };
}
