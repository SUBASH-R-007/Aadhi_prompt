// @ts-check
/**
 * Test harness for the B2 scene templates (B2c; B2 plan §1.4, §1.5, §P, §2). Each template's own test file
 * (web/tests/dynamic/templates/<id>.test.js) runs its fixture timelines through these checks in jsdom, the unit-test
 * complement of the real-Chromium gates (tests/determinism/test_template_fixtures.py: 1x / 3x / reverse frame hashes;
 * preflight; textfit against the page):
 *
 * - `renderStage(timeline, opts)`: a render-mode stage with immediate TeX idle and frames (and, before the template
 *   is registered in stage.js TEMPLATES, `opts.templates` to add it).
 * - `orderProblems(timeline, frames, opts)`: every frame's key and DOM are the same painted forward, in reverse and
 *   on a fresh page (a frame is a pure function of t, §7.4); an emptied `style=""` is not a difference.
 * - `contractProblems(template, timeline, opts)`: the template contract at run time (plan §1.4, §1.5): the template
 *   writes no `opacity` on its `dimGroups` (the stage owns it), returns the same groups at every t, never writes
 *   `outline`, `text-decoration`, `filter`, `backdrop-filter` or `will-change` through the painter (the source scan
 *   checks the code; this checks what it does), gives whole-pixel `focusRect`s for every look_here target of its
 *   scenes, finds a `.rt-k` span for every cue_term target, declares every TeX string it converts in its static
 *   `tex()` (render.js converts them before `ready`), and reports text entries with a role, a hex ink and an
 *   alpha in [0, 1].
 * An empty list means no problem. Every check here was seen to fail on a template that breaks it (shared.test.js).
 */

import { createDynamicStage } from '../../../js/dynamic/stage.js';
import { cueEvents, termSpans } from '../../../js/dynamic/cues.js';
import { Painter } from '../../../js/dynamic/core.js';

/** @typedef {import('../../../js/dynamic/templates/types.js').TemplateModule} TemplateModule */

const BANNED = /^(outline|text-decoration|filter|backdrop-filter|will-change)(-|$)/;
const HEX = /^#[0-9a-f]{6}$/i;

/**
 * A recording TeX converter (render mode's `renderTex` stand-in): a span holding the TeX source.
 * @returns {{renderTex: (tex: string, display: boolean) => Promise<Element>, seen: {tex: string, display: boolean}[]}}
 */
export function recordingTex() {
  /** @type {{tex: string, display: boolean}[]} */
  const seen = [];
  return {
    seen,
    renderTex: (tex, display) => {
      seen.push({ tex, display });
      const el = document.createElement('span');
      el.className = 'fake-mjx';
      el.textContent = tex;
      return Promise.resolve(el);
    },
  };
}

/**
 * A render-mode stage of `timeline` inside a fresh root (destroy it with `done()`).
 * @param {any} timeline
 * @param {{templates?: Record<string, TemplateModule>, renderTex?: any, mode?: 'live' | 'render',
 *   reducedMotion?: boolean}} [opts]
 */
export function renderStage(timeline, opts = {}) {
  const root = document.createElement('div');
  document.body.appendChild(root);
  const stage = createDynamicStage(root, timeline, {
    mode: opts.mode || 'render', texIdle: () => Promise.resolve(), frame: () => Promise.resolve(),
    ...(opts.templates ? { templates: opts.templates } : {}), ...(opts.renderTex ? { renderTex: opts.renderTex } : {}),
    ...(opts.reducedMotion ? { reducedMotion: true } : {}),
  });
  return { stage, root, done: () => { stage.destroy(); root.remove(); } };
}

/**
 * Frames whose key or DOM differ between a forward pass, a reverse pass and fresh pages (one per frame).
 * @param {any} timeline @param {number[]} frames @param {{templates?: Record<string, TemplateModule>}} [opts]
 * @returns {Promise<string[]>}
 */
export async function orderProblems(timeline, frames, opts = {}) {
  /** @param {number[]} order */
  const run = async (order) => {
    const { stage, done } = renderStage(structuredClone(timeline), { ...opts, renderTex: recordingTex().renderTex });
    /** @type {Map<number, string>} */
    const out = new Map();
    for (const f of order) {
      const { key } = await stage.seek(f);
      out.set(f, `${key}|${stage.el.outerHTML.replace(/ style=""/g, '')}`);
    }
    done();
    return out;
  };
  const forward = await run(frames);
  const backward = await run([...frames].reverse());
  /** @type {string[]} */
  const problems = [];
  for (const f of frames) {
    if (backward.get(f) !== forward.get(f)) problems.push(`frame ${f}: differs painted in reverse`);
    const fresh = (await run([f])).get(f);
    if (fresh !== forward.get(f)) problems.push(`frame ${f}: differs on a fresh page`);
  }
  return problems;
}

/**
 * The template contract at run time for every scene of `timeline` drawn by `template` (see the module comment).
 * @param {TemplateModule} template
 * @param {any} timeline
 * @param {{times?: number[]}} [opts]   times: scene-relative instants to paint (default: a spread over each scene and
 *   every event's time). The template is added through the stage's `templates` seam (wrapped, to watch its writes)
 *   whether or not stage.js registers it.
 * @returns {Promise<string[]>}
 */
export async function contractProblems(template, timeline, opts = {}) {
  /** @type {string[]} */
  const problems = [];
  let inTemplate = false;
  /** @type {{el: any, prop: string}[]} */
  const writes = [];
  /** @type {Map<number, any>} the context each scene was built with */
  const contexts = new Map();
  const wrapped = /** @type {TemplateModule} */ ({
    ...template,
    build(ctx) {
      contexts.set(ctx.index, ctx);
      return template.build(ctx);
    },
    update(ctx, t, state) {
      inTemplate = true;
      try {
        template.update(ctx, t, state);
      } finally {
        inTemplate = false;
      }
    },
  });
  const proto = /** @type {any} */ (Painter.prototype);
  const realStyle = proto.style;
  const realAttr = proto.attr;
  proto.style = function style(/** @type {any} */ el, /** @type {string} */ prop, /** @type {any} */ value) {
    if (inTemplate) writes.push({ el, prop: String(prop).toLowerCase() });
    return realStyle.call(this, el, prop, value);
  };
  proto.attr = function attr(/** @type {any} */ el, /** @type {string} */ name, /** @type {any} */ value) {
    if (inTemplate) writes.push({ el, prop: `@${String(name).toLowerCase()}` });
    return realAttr.call(this, el, name, value);
  };
  const tex = recordingTex();
  const { stage, done } = renderStage(structuredClone(timeline), { templates: { [template.id]: wrapped }, renderTex: tex.renderTex });
  try {
    const info = stage.info();
    const tl = timeline;
    for (const s of info.scenes) {
      const scene = tl.scenes[s.index];
      if (!scene.look || scene.look.template !== template.id) continue;
      const fmt = tl.presentation.format === 'portrait' ? 'portrait' : 'landscape';
      const times = opts.times || [0, 0.5, 1, ...(scene.events || []).flatMap((/** @type {any} */ e) => [e.t, e.t + 0.4]),
        scene.duration / 2, scene.duration - 0.01];
      /** @type {Element[] | null} */
      let groups = null;
      for (const t of times) {
        const f = Math.min(s.f1 - 1, Math.max(s.f0, Math.round((Number(scene.start) + Math.max(0, t)) * info.fps)));
        writes.length = 0;
        await stage.seek(f);
        const ctx = contexts.get(s.index);
        if (!ctx || !ctx.root.isConnected) {
          problems.push(`scene ${s.index}: not on the page at frame ${f}`);
          continue;
        }
        for (const w of writes) {
          if (BANNED.test(w.prop.replace(/^@/, ''))) problems.push(`scene ${s.index} t ${t}: the template writes ${w.prop} (cues.js owns emphasis; §P)`);
        }
        if (typeof template.dimGroups === 'function') {
          const now = template.dimGroups(ctx);
          if (groups && (now.length !== groups.length || now.some((g, i) => g !== /** @type {Element[]} */ (groups)[i]))) {
            problems.push(`scene ${s.index} t ${t}: dimGroups changed (they must be the same elements at every t)`);
          }
          groups = now;
          for (const w of writes) {
            if (w.prop === 'opacity' && now.includes(w.el)) problems.push(`scene ${s.index} t ${t}: the template writes the opacity of a dim group (the stage owns it)`);
          }
        }
        for (const e of cueEvents(scene, template.id)) {
          if (e.kind === 'look_here' && typeof template.focusRect === 'function') {
            const r = template.focusRect(ctx, e.target);
            if (r && !['x', 'y', 'w', 'h'].every((k) => Number.isInteger(/** @type {any} */ (r.rect)[k]))) {
              problems.push(`scene ${s.index}: focusRect(${e.target}) is not on whole px: ${JSON.stringify(r.rect)}`);
            }
          }
          if (e.kind === 'cue_term' && termSpans(template, ctx, e).length === 0) {
            problems.push(`scene ${s.index}: no [[term]] span for cue_term ${e.target}`);
          }
        }
        if (typeof template.texts === 'function') {
          for (const entry of template.texts(ctx)) {
            if (!entry.role || !HEX.test(String(entry.ink)) || (entry.bg !== null && !HEX.test(String(entry.bg)))) {
              problems.push(`scene ${s.index}: a text entry without a role or a hex ink / bg: ${JSON.stringify({ role: entry.role, ink: entry.ink, bg: entry.bg })}`);
            }
            const a = entry.alpha === undefined ? 1 : entry.alpha;
            if (!(a >= 0 && a <= 1)) problems.push(`scene ${s.index}: a text entry's alpha ${a} is outside [0, 1]`);
          }
        }
      }
      // every TeX string converted for this scene is declared in tex() (render.js converts them before `ready`)
      const declared = new Set((typeof template.tex === 'function' ? template.tex(scene, scene.look, fmt) : [])
        .map((x) => `${x.display ? 'D' : 'I'}:${x.tex}`));
      for (const x of tex.seen) {
        const k = `${x.display ? 'D' : 'I'}:${x.tex}`;
        if (!declared.has(k) && sceneOwns(stage, s.index, x.tex)) problems.push(`scene ${s.index}: TeX not declared in tex(): ${k}`);
      }
    }
  } finally {
    proto.style = realStyle;
    proto.attr = realAttr;
    done();
  }
  return [...new Set(problems)];
}

/**
 * Whether the scene's DOM holds TeX `tex` (a recorded conversion belongs to the scene that mounted it).
 * @param {any} stage @param {number} index @param {string} tex
 */
function sceneOwns(stage, index, tex) {
  const el = stage.el.querySelector(`.dyn-scene[data-scene="${index}"]`);
  return !!el && [...el.querySelectorAll('.fake-mjx')].some((m) => m.textContent === tex);
}
