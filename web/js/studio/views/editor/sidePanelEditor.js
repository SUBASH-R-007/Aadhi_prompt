// @ts-check
/**
 * Side panel editor: kind picker (keeps title/rationale when switching), common fields
 * (title, rationale, show from beat) and a form per kind: figure, image (prompt or upload),
 * chart (type, labels, datasets, axis labels; lengths validated), graph (math.js functions
 * checked against the allow-list, points, ranges), 3D model (primitives with shape-specific
 * sizes), Manim (template/code), terminal, quiz teaser, GIF.
 */

import { h, append } from '../../../shared/dom.js';
import { field, select, textarea, input, button, checkbox, group, formGrid } from '../../components/form.js';
import * as E from '../../lib/screenplayEdit.js';
import { checkExpr } from '../../lib/expr.js';
import { uid } from '../../util.js';
import { mediaUpload } from './mediaUpload.js';
import { manimEditor } from './manimEditor.js';
import { defaultManimTemplate } from '../../lib/manimTemplates.js';

/** @typedef {import('./context.js').InspectorCtx} InspectorCtx */

const KIND_LABELS = /** @type {Record<string, string>} */ ({
  skill_tree: 'Concept map progress',
  figure: 'Source figure',
  image: 'Illustration',
  chart: 'Chart',
  graph: 'Function graph',
  model_3d: '3D model',
  manim: 'Animation (Manim)',
  terminal: 'Terminal',
  quiz: 'Quiz teaser',
  gif: 'GIF (web player only)',
});

/**
 * @param {InspectorCtx} ctx
 * @param {Array<{ destroy: () => void, flush?: () => void }>} disposables
 * @returns {HTMLElement}
 */
export function sidePanelEditor(ctx, disposables) {
  const scene = ctx.scene;
  const panel = scene.side_panel;
  const kindSel = select({
    options: [{ value: '', label: 'No side panel' }, ...E.SIDE_PANEL_KINDS.map((k) => ({ value: k, label: KIND_LABELS[k] || k }))],
    value: panel ? panel.kind : '',
    dataset: { fk: 'panel:kind' },
    onChange: (v) => ctx.editScene((s) => ({ ...s, side_panel: E.sidePanelOfKind(v || null, s.side_panel, ctx.sp, defaultManimTemplate(ctx.meta)) }), { structural: true }),
  });
  const section = h('section', { class: 'inspector-section panel-section', 'aria-label': 'Side panel' }, h('div', { class: 'section-head' }, h('h3', {}, 'Side panel')), field('Kind', kindSel));
  if (!panel) {
    section.append(h('p', { class: 'muted small' }, 'Optional visual beside the board. Use one only when it helps learning (coherence principle).'));
    return section;
  }
  /**
   * Mutate the panel of the latest scene.
   * @param {(p: any) => void} fn
   * @param {import('./context.js').EditOptions} [o]
   */
  const editPanel = (fn, o = {}) =>
    ctx.editScene((s) => {
      if (s.side_panel) fn(s.side_panel);
    }, o);

  const beats = E.sceneBeats(scene).map((b) => b.beat);
  append(section, [
    formGrid(
      field('Title', input({ value: panel.title || '', maxLength: 160, dataset: { fk: 'panel:title' }, onInput: (v) => editPanel((p) => { p.title = v || null; }, { coalesce: 'panel:title' }) })),
      field(
        'Show from',
        select({
          options: [{ value: '', label: 'Scene start' }, ...beats.map((b, i) => ({ value: b.id, label: `Beat ${i + 1}: ${String(b.narration || '').slice(0, 40)}` }))],
          value: panel.show_from_beat_id || '',
          dataset: { fk: 'panel:show' },
          onChange: (v) => editPanel((p) => { p.show_from_beat_id = v || null; }),
        }),
      ),
    ),
    panel.kind !== 'skill_tree'
      ? field('Why this visual helps', input({ value: panel.rationale || '', maxLength: 400, placeholder: 'e.g. The graph shows the linear relation the narration describes.', dataset: { fk: 'panel:rationale' }, onInput: (v) => editPanel((p) => { p.rationale = v; }, { coalesce: 'panel:rationale' }) }), { hint: 'Required by the pedagogy check for every visual except the concept map.' })
      : null,
  ]);

  switch (panel.kind) {
    case 'figure': {
      const figures = ctx.sp.figures || [];
      section.append(
        field('Source figure', select({ options: [{ value: '', label: '— none —' }, ...figures.map((/** @type {any} */ f) => ({ value: f.id, label: `${f.id}${f.caption ? ` · ${f.caption.slice(0, 40)}` : ''}` }))], value: panel.figure_id || '', dataset: { fk: 'panel:figure' }, onChange: (v) => editPanel((p) => { p.figure_id = v || null; }) })),
        overrideUpload(ctx, 'side_panel', panel.override_asset_key, 'Or upload an image'),
      );
      break;
    }
    case 'image':
      section.append(
        field('Illustration prompt', textarea({ value: panel.image_prompt || '', rows: 3, maxLength: 800, placeholder: 'Describe the illustration (no text in the image).', dataset: { fk: 'panel:prompt' }, onInput: (v) => editPanel((p) => { p.image_prompt = v || null; }, { coalesce: 'panel:prompt' }) })),
        overrideUpload(ctx, 'side_panel', panel.override_asset_key, 'Or upload your own image'),
      );
      break;
    case 'chart':
      section.append(chartForm(ctx, panel.chart, editPanel));
      break;
    case 'graph':
      section.append(graphForm(ctx, panel.graph, editPanel));
      break;
    case 'model_3d':
      section.append(modelForm(ctx, panel.model_3d, editPanel));
      break;
    case 'manim': {
      const ed = manimEditor({
        spec: panel.manim || { template: null, params: {}, code: null },
        meta: ctx.meta,
        beatsCount: beats.length,
        target: 'panel',
        fkPrefix: 'panel:manim',
        onChange: (spec) => editPanel((p) => { p.manim = spec; }, { coalesce: 'panel:manim' }),
      });
      disposables.push(ed);
      section.append(ed.el, overrideUpload(ctx, 'side_panel', panel.override_asset_key, 'Or upload a video instead'));
      break;
    }
    case 'terminal': {
      const t = panel.terminal || { command: '', output: '' };
      section.append(
        field('Command', input({ value: t.command || '', maxLength: 300, spellcheck: 'false', dataset: { fk: 'panel:cmd' }, onInput: (v) => editPanel((p) => { p.terminal = { ...(p.terminal || {}), command: v }; }, { coalesce: 'panel:cmd' }) })),
        field('Output (revealed line by line)', textarea({ value: t.output || '', rows: 6, mono: true, maxLength: 3000, dataset: { fk: 'panel:out' }, onInput: (v) => editPanel((p) => { p.terminal = { ...(p.terminal || {}), output: v }; }, { coalesce: 'panel:out' }) })),
      );
      break;
    }
    case 'quiz':
      section.append(teaserForm(ctx, panel.quiz, editPanel));
      break;
    case 'gif':
      section.append(field('GIF search phrase', input({ value: panel.gif_query || '', maxLength: 80, dataset: { fk: 'panel:gif' }, onInput: (v) => editPanel((p) => { p.gif_query = v; }, { coalesce: 'panel:gif' }) }), { hint: 'Shown in the web player only; MP4 exports skip GIFs.' }));
      break;
    default:
      section.append(h('p', { class: 'muted small' }, 'Shows the concept map with this scene’s concept highlighted.'));
  }
  return section;
}

/**
 * @param {InspectorCtx} ctx
 * @param {'side_panel' | 'figure'} purpose
 * @param {string | null} key
 * @param {string} label
 */
function overrideUpload(ctx, purpose, key, label) {
  return mediaUpload({
    purpose,
    projectId: ctx.projectId,
    assetKey: key || null,
    label,
    maxMb: ctx.maxUploadMb,
    pickLibrary: ctx.pickLibrary,
    onChange: (assetKey) => ctx.editScene((s) => {
      if (s.side_panel) s.side_panel.override_asset_key = assetKey;
    }),
  });
}

/**
 * Parse "1, 2.5, 3" into numbers (NaN marks invalid entries).
 * @param {string} text
 */
export function parseNumberList(text) {
  return String(text)
    .split(/[,;\s]+/)
    .map((s) => s.trim())
    .filter(Boolean)
    .map((s) => Number(s));
}

/**
 * @param {string} text
 */
export function parseLabelList(text) {
  return String(text)
    .split(/\n|,/)
    .map((s) => s.trim())
    .filter(Boolean);
}

/**
 * @param {InspectorCtx} ctx
 * @param {any} chart
 * @param {(fn: (p: any) => void, o?: import('./context.js').EditOptions) => void} editPanel
 */
function chartForm(ctx, chart, editPanel) {
  const c = chart || { chart_type: 'bar', labels: [], datasets: [] };
  const problems = h('div', { class: 'field-error', role: 'alert', hidden: true });
  /** working copy for validation between non-structural edits */
  let cur = JSON.parse(JSON.stringify(c));
  const check = () => {
    const errs = [];
    if (!cur.labels.length) errs.push('Add at least one label.');
    cur.datasets.forEach((/** @type {any} */ d, /** @type {number} */ i) => {
      if (d.data.some((/** @type {number} */ v) => !Number.isFinite(v))) errs.push(`Dataset ${i + 1}: values must be numbers.`);
      if (d.data.length !== cur.labels.length) errs.push(`Dataset ${i + 1}: ${d.data.length} values for ${cur.labels.length} labels.`);
    });
    problems.textContent = errs.join(' ');
    problems.hidden = errs.length === 0;
  };
  /** @param {(x: any) => void} fn @param {string} key */
  const save = (fn, key) => {
    fn(cur);
    check();
    const snapshot = JSON.parse(JSON.stringify(cur));
    snapshot.datasets = snapshot.datasets.map((/** @type {any} */ d) => ({ ...d, data: d.data.filter((/** @type {number} */ v) => Number.isFinite(v)) }));
    editPanel((p) => { p.chart = snapshot; }, { coalesce: key });
  };
  const datasets = c.datasets.map((/** @type {any} */ d, /** @type {number} */ i) => {
    const del = button(`Remove dataset ${i + 1}`, { kind: 'ghost', small: true, iconOnly: true, icon: 'trash', disabled: c.datasets.length <= 1 });
    del.addEventListener('click', () => editPanel((p) => { p.chart.datasets.splice(i, 1); }, { structural: true }));
    return h(
      'div',
      { class: 'dataset-row' },
      input({ value: d.label || '', maxLength: 120, ariaLabel: `Dataset ${i + 1} label`, placeholder: 'Series name', dataset: { fk: `panel:chart:ds:${i}:label` }, onInput: (v) => save((x) => { x.datasets[i].label = v; }, `chart:ds:${i}:label`) }),
      input({ value: (d.data || []).join(', '), ariaLabel: `Dataset ${i + 1} values`, placeholder: '1, 2, 3', dataset: { fk: `panel:chart:ds:${i}:data` }, onInput: (v) => save((x) => { x.datasets[i].data = parseNumberList(v); }, `chart:ds:${i}:data`) }),
      del,
    );
  });
  const addDs = button('Add dataset', { kind: 'ghost', small: true, icon: 'plus', disabled: c.datasets.length >= 6 });
  addDs.addEventListener('click', () => editPanel((p) => { p.chart.datasets.push({ label: `Series ${p.chart.datasets.length + 1}`, data: p.chart.labels.map(() => 0) }); }, { structural: true }));
  check();
  return h(
    'div',
    { class: 'chart-form' },
    formGrid(
      field('Chart type', select({ options: ['bar', 'line', 'pie', 'doughnut', 'radar'].map((t) => ({ value: t, label: t })), value: c.chart_type, dataset: { fk: 'panel:chart:type' }, onChange: (v) => save((x) => { x.chart_type = v; }, 'chart:type') })),
      field('X axis label', input({ value: c.x_label || '', maxLength: 120, dataset: { fk: 'panel:chart:x' }, onInput: (v) => save((x) => { x.x_label = v || null; }, 'chart:x') })),
      field('Y axis label', input({ value: c.y_label || '', maxLength: 120, dataset: { fk: 'panel:chart:y' }, onInput: (v) => save((x) => { x.y_label = v || null; }, 'chart:y') })),
    ),
    field('Labels (comma or one per line)', textarea({ value: (c.labels || []).join('\n'), rows: 3, dataset: { fk: 'panel:chart:labels' }, onInput: (v) => save((x) => { x.labels = parseLabelList(v).slice(0, 64); }, 'chart:labels') }), { hint: 'Up to 64 labels; every dataset needs one value per label.' }),
    group('Datasets (values separated by commas)', h('div', { class: 'datasets' }, datasets, addDs)),
    problems,
  );
}

/**
 * @param {InspectorCtx} ctx
 * @param {any} graph
 * @param {(fn: (p: any) => void, o?: import('./context.js').EditOptions) => void} editPanel
 */
function graphForm(ctx, graph, editPanel) {
  const g = graph || { functions: [], points: [], x_range: [-5, 5], y_range: null };
  const fns = (g.functions || []).map((/** @type {any} */ f, /** @type {number} */ i) => {
    const err = h('div', { class: 'field-error', role: 'alert', hidden: true });
    const show = (/** @type {string} */ expr) => {
      const p = checkExpr(expr);
      err.textContent = p || '';
      err.hidden = !p;
    };
    show(f.expr || '');
    const del = button(`Remove function ${i + 1}`, { kind: 'ghost', small: true, iconOnly: true, icon: 'trash' });
    del.addEventListener('click', () => editPanel((p) => { p.graph.functions.splice(i, 1); }, { structural: true }));
    return h(
      'div',
      { class: 'fn-row' },
      h('span', { class: 'mono' }, 'y ='),
      input({ value: f.expr || '', maxLength: 200, spellcheck: 'false', ariaLabel: `Function ${i + 1} expression in x`, placeholder: 'x^2 + 1', dataset: { fk: `panel:graph:fn:${i}` }, onInput: (v) => { show(v); editPanel((p) => { p.graph.functions[i].expr = v; }, { coalesce: `graph:fn:${i}` }); } }),
      input({ value: f.label || '', maxLength: 80, ariaLabel: `Function ${i + 1} label`, placeholder: 'label', dataset: { fk: `panel:graph:fnl:${i}` }, onInput: (v) => editPanel((p) => { p.graph.functions[i].label = v || null; }, { coalesce: `graph:fnl:${i}` }) }),
      del,
      err,
    );
  });
  const addFn = button('Add function', { kind: 'ghost', small: true, icon: 'plus', disabled: (g.functions || []).length >= 4 });
  addFn.addEventListener('click', () => editPanel((p) => { p.graph.functions.push({ expr: 'x', label: null }); }, { structural: true }));
  const pts = (g.points || []).map((/** @type {any} */ pt, /** @type {number} */ i) => {
    const del = button(`Remove point ${i + 1}`, { kind: 'ghost', small: true, iconOnly: true, icon: 'trash' });
    del.addEventListener('click', () => editPanel((p) => { p.graph.points.splice(i, 1); }, { structural: true }));
    /** @param {'x' | 'y'} axis */
    const num = (axis) => input({ type: 'number', step: 'any', value: String(pt[axis] ?? 0), ariaLabel: `Point ${i + 1} ${axis}`, dataset: { fk: `panel:graph:pt:${i}:${axis}` }, onInput: (v) => { const n = Number(v); if (v !== '' && Number.isFinite(n)) editPanel((p) => { p.graph.points[i][axis] = n; }, { coalesce: `graph:pt:${i}:${axis}` }); } });
    return h('div', { class: 'pt-row' }, num('x'), num('y'), input({ value: pt.label || '', maxLength: 60, ariaLabel: `Point ${i + 1} label`, placeholder: 'label', dataset: { fk: `panel:graph:pt:${i}:l` }, onInput: (v) => editPanel((p) => { p.graph.points[i].label = v || null; }, { coalesce: `graph:pt:${i}:l` }) }), del);
  });
  const addPt = button('Add point', { kind: 'ghost', small: true, icon: 'plus', disabled: (g.points || []).length >= 20 });
  addPt.addEventListener('click', () => editPanel((p) => { p.graph.points.push({ x: 0, y: 0, label: null }); }, { structural: true }));
  const rangeErr = h('div', { class: 'field-error', role: 'alert', hidden: true });
  /**
   * @param {'x_range' | 'y_range'} key
   * @param {0 | 1} idx
   * @param {string} label
   */
  const rangeInput = (key, idx, label) =>
    field(
      label,
      input({
        type: 'number',
        step: 'any',
        value: String(((key === 'x_range' ? g.x_range : g.y_range) || [-5, 5])[idx]),
        dataset: { fk: `panel:graph:${key}:${idx}` },
        onInput: (v) => {
          const n = Number(v);
          if (v === '' || !Number.isFinite(n)) return;
          editPanel((p) => {
            const r = (p.graph[key] || [-5, 5]).slice();
            r[idx] = n;
            p.graph[key] = r;
            rangeErr.textContent = r[0] < r[1] ? '' : `${key === 'x_range' ? 'x' : 'y'} range must be increasing.`;
            rangeErr.hidden = r[0] < r[1];
          }, { coalesce: `graph:${key}:${idx}` });
        },
      }),
    );
  const autoY = checkbox({ label: 'Automatic y range', checked: !g.y_range, dataset: { fk: 'panel:graph:autoy' }, onChange: (v) => editPanel((p) => { p.graph.y_range = v ? null : [-5, 5]; }, { structural: true }) });
  return h(
    'div',
    { class: 'graph-form' },
    group('Functions of x (math.js syntax: sin, cos, exp, log, sqrt, abs, ^ …)', h('div', {}, fns, addFn)),
    group('Points', h('div', {}, pts, addPt)),
    formGrid(rangeInput('x_range', 0, 'x from'), rangeInput('x_range', 1, 'x to'), g.y_range ? rangeInput('y_range', 0, 'y from') : null, g.y_range ? rangeInput('y_range', 1, 'y to') : null),
    autoY,
    rangeErr,
    formGrid(
      field('X axis label', input({ value: g.x_label || '', maxLength: 60, dataset: { fk: 'panel:graph:xl' }, onInput: (v) => editPanel((p) => { p.graph.x_label = v || null; }, { coalesce: 'graph:xl' }) })),
      field('Y axis label', input({ value: g.y_label || '', maxLength: 60, dataset: { fk: 'panel:graph:yl' }, onInput: (v) => editPanel((p) => { p.graph.y_label = v || null; }, { coalesce: 'graph:yl' }) })),
    ),
  );
}

/**
 * @param {InspectorCtx} ctx
 * @param {any} model
 * @param {(fn: (p: any) => void, o?: import('./context.js').EditOptions) => void} editPanel
 */
function modelForm(ctx, model, editPanel) {
  const m = model || { primitives: [], auto_rotate: true };
  const rows = (m.primitives || []).map((/** @type {any} */ pr, /** @type {number} */ i) => {
    const sizeLabels = E.PRIMITIVE_SIZE_LABELS[pr.shape] || ['Size'];
    const del = button(`Remove shape ${i + 1}`, { kind: 'ghost', small: true, iconOnly: true, icon: 'trash', disabled: m.primitives.length <= 1 });
    del.addEventListener('click', () => editPanel((p) => { p.model_3d.primitives.splice(i, 1); }, { structural: true }));
    /**
     * @param {'position' | 'size'} key
     * @param {number} j
     * @param {string} label
     */
    const vec = (key, j, label) =>
      input({
        type: 'number',
        step: 'any',
        value: String((pr[key] || [])[j] ?? (key === 'size' ? 1 : 0)),
        ariaLabel: `Shape ${i + 1} ${label}`,
        dataset: { fk: `panel:m3d:${i}:${key}:${j}` },
        onInput: (v) => {
          const n = Number(v);
          if (v === '' || !Number.isFinite(n)) return;
          editPanel((p) => {
            const arr = (p.model_3d.primitives[i][key] || []).slice();
            arr[j] = n;
            p.model_3d.primitives[i][key] = arr;
          }, { coalesce: `m3d:${i}:${key}:${j}` });
        },
      });
    const color = h('input', { type: 'color', value: /^#[0-9a-fA-F]{6}$/.test(pr.color || '') ? pr.color : '#b026ff', 'aria-label': `Shape ${i + 1} colour`, dataset: { fk: `panel:m3d:${i}:color` } });
    color.addEventListener('input', () => editPanel((p) => { p.model_3d.primitives[i].color = color.value; }, { coalesce: `m3d:${i}:color` }));
    return h(
      'fieldset',
      { class: 'fieldset prim' },
      h('legend', {}, `Shape ${i + 1}`),
      h(
        'div',
        { class: 'row gap wrap align-end' },
        field(
          'Shape',
          select({
            options: ['sphere', 'box', 'cylinder', 'cone', 'torus', 'arrow'].map((s) => ({ value: s, label: s })),
            value: pr.shape,
            dataset: { fk: `panel:m3d:${i}:shape` },
            onChange: (v) => editPanel((p) => {
              const n = (E.PRIMITIVE_SIZE_LABELS[v] || ['Size']).length;
              const prim = p.model_3d.primitives[i];
              prim.shape = v;
              prim.size = Array.from({ length: n }, (_, k) => (prim.size || [])[k] ?? 1);
            }, { structural: true }),
          }),
        ),
        field('Colour', color),
        field('Label', input({ value: pr.label || '', maxLength: 60, dataset: { fk: `panel:m3d:${i}:label` }, onInput: (v) => editPanel((p) => { p.model_3d.primitives[i].label = v || null; }, { coalesce: `m3d:${i}:label` }) })),
        del,
      ),
      group('Position (x, y, z)', h('div', { class: 'vec-row' }, vec('position', 0, 'x'), vec('position', 1, 'y'), vec('position', 2, 'z'))),
      group(`Size (${sizeLabels.join(', ')})`, h('div', { class: 'vec-row' }, sizeLabels.map((l, j) => vec('size', j, l)))),
    );
  });
  const add = button('Add shape', { kind: 'ghost', small: true, icon: 'plus', disabled: (m.primitives || []).length >= 40 });
  add.addEventListener('click', () => editPanel((p) => { p.model_3d.primitives.push({ shape: 'box', position: [0, 0, 0], size: [1, 1, 1], color: '#ffd700', label: null }); }, { structural: true }));
  return h(
    'div',
    { class: 'model-form' },
    rows,
    add,
    checkbox({ label: 'Rotate slowly in the web player (MP4 shows a fixed angle)', checked: m.auto_rotate !== false, dataset: { fk: 'panel:m3d:rot' }, onChange: (v) => editPanel((p) => { p.model_3d.auto_rotate = v; }) }),
  );
}

/**
 * @param {InspectorCtx} ctx
 * @param {any} quiz
 * @param {(fn: (p: any) => void, o?: import('./context.js').EditOptions) => void} editPanel
 */
function teaserForm(ctx, quiz, editPanel) {
  const q = quiz || { question: '', options: ['', ''], correct_index: 0 };
  const name = uid('teaser');
  const opts = q.options.map((/** @type {string} */ o, /** @type {number} */ i) => {
    const radio = h('input', { type: 'radio', 'aria-label': `Option ${i + 1} is correct`, dataset: { fk: `panel:tq:${i}:ok` } });
    radio.name = name; // generated group name (not model data)
    radio.checked = q.correct_index === i;
    radio.addEventListener('change', () => editPanel((p) => { p.quiz.correct_index = i; }));
    const del = button(`Remove option ${i + 1}`, { kind: 'ghost', small: true, iconOnly: true, icon: 'trash', disabled: q.options.length <= 2 });
    del.addEventListener('click', () => editPanel((p) => {
      p.quiz.options.splice(i, 1);
      if (p.quiz.correct_index >= p.quiz.options.length || p.quiz.correct_index === i) p.quiz.correct_index = 0;
      else if (p.quiz.correct_index > i) p.quiz.correct_index -= 1;
    }, { structural: true }));
    return h('div', { class: 'opt-row' }, radio, input({ value: o, maxLength: 240, ariaLabel: `Option ${i + 1}`, dataset: { fk: `panel:tq:${i}` }, onInput: (v) => editPanel((p) => { p.quiz.options[i] = v; }, { coalesce: `tq:${i}` }) }), del);
  });
  const add = button('Add option', { kind: 'ghost', small: true, icon: 'plus', disabled: q.options.length >= 5 });
  add.addEventListener('click', () => editPanel((p) => { p.quiz.options.push(''); }, { structural: true }));
  return h(
    'div',
    { class: 'teaser-form' },
    field('Question', input({ value: q.question || '', maxLength: 400, dataset: { fk: 'panel:tq:q' }, onInput: (v) => editPanel((p) => { p.quiz.question = v; }, { coalesce: 'tq:q' }) })),
    group('Options (select the correct one)', h('div', { role: 'radiogroup' }, opts, add)),
  );
}
