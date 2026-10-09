// @ts-check
/**
 * Pure layered DAG layout for the concept map ("skill tree"), Sugiyama style:
 *   1. levels = longest path from the roots (cycles, impossible per schema, are broken defensively);
 *   2. long edges get dummy vertices on intermediate layers;
 *   3. barycenter sweeps reduce edge crossings (best ordering kept);
 *   4. positions along each layer minimise distance to neighbours under min-separation (isotonic
 *      regression);
 *   5. edges become smooth cubic curves leaving and entering nodes along the flow direction.
 *
 * Fitting the panel: the SVG viewBox scales the whole layout into the panel, so what matters is the
 * font size *after* that scale (`renderedFont`). Two orientations are tried: top-to-bottom ('tb',
 * prerequisites on top: deep, narrow maps) and left-to-right ('lr': wide, shallow maps, e.g. many
 * prerequisites feeding one concept). For each, the largest font whose layout fits the box is used
 * (labels wrap to <= 3 lines); TB is kept unless LR is clearly more legible. When nothing fits even
 * at the minimum font, the most legible candidate is returned and `renderedFont < minFont` tells
 * the caller to show a focused subset instead (skillTree.js).
 * No DOM access: unit-tested in node.
 */

/**
 * @typedef {object} ConceptInput
 * @property {string} id
 * @property {string} [title]
 * @property {string[]} [depends_on]
 * @property {string} [kind]   'core' | 'prerequisite'
 */

/** @typedef {'tb' | 'lr'} Orientation */

/**
 * @typedef {object} LayoutNode
 * @property {string} id
 * @property {string} title
 * @property {'core' | 'prerequisite'} kind
 * @property {number} level
 * @property {number} order   position within its level
 * @property {number} x       centre
 * @property {number} y       centre
 * @property {number} w
 * @property {number} h
 * @property {string[]} lines wrapped label
 */

/**
 * @typedef {object} LayoutEdge
 * @property {string} from    prerequisite
 * @property {string} to      dependent
 * @property {{ x: number, y: number }[]} points  from the prerequisite's exit side through dummies
 *   to the dependent's entry side (tb: bottom -> top centre; lr: right -> left middle)
 * @property {string} d       SVG path data
 */

/**
 * @typedef {object} SkillTreeLayout
 * @property {Orientation} orientation
 * @property {number} width
 * @property {number} height
 * @property {number} fontSize
 * @property {number} lineHeight
 * @property {number} padX     inner horizontal padding of a node
 * @property {number} levels
 * @property {number} scale         viewBox scale in the box: min(boxW / width, boxH / height),
 *   capped at MAX_UPSCALE (drawTree limits the SVG size accordingly)
 * @property {number} renderedFont  fontSize * scale: label size as displayed
 * @property {boolean} fits         width <= boxW and height <= boxH
 * @property {LayoutNode[]} nodes
 * @property {LayoutEdge[]} edges
 * @property {Array<[string, string]>} brokenEdges  edges dropped to break cycles (normally empty)
 * @property {number} crossings
 */

/**
 * @typedef {object} LayoutOptions
 * @property {number} [width]       target box (CSS px); default 440
 * @property {number} [height]      default 640
 * @property {number} [maxFont]     default 20
 * @property {number} [minFont]     default 11
 * @property {number} [maxLines]    default 3
 * @property {number} [iterations]  barycenter sweeps; default 12
 * @property {Orientation} [orientation]  force one orientation (default: choose)
 */

/** @typedef {{ key: string, real: boolean, layer: number, up: Vertex[], down: Vertex[] }} Vertex */
/** @typedef {{ id: string, title: string, kind: 'core' | 'prerequisite', deps: string[] }} NormConcept */

const ELLIPSIS = '…';

/** TB (the conventional reading) is kept unless LR renders labels this many times larger. */
export const LR_ADVANTAGE = 1.2;
/** A small map is enlarged at most this much to fill the panel (drawTree caps the SVG size). */
export const MAX_UPSCALE = 1.25;
/** Label wrap width limits, in multiples of the font size (words up to ~7 Latin letters fit). */
const MIN_NODE_EM = 5.5;
const MAX_NODE_EM = 14;
/** Narrowest box (a box hugs its label: short labels get narrow boxes). */
const MIN_BOX_EM = 3;
/** Each fitting step shrinks the font by this factor. */
const SHRINK = 0.9;

/**
 * Normalise concepts: unique ids (first wins), known deps only, no self-loops, no duplicates.
 * @param {ConceptInput[]} concepts
 * @returns {{ ids: string[], byId: Map<string, NormConcept> }}
 */
export function normalizeConcepts(concepts) {
  /** @type {Map<string, NormConcept>} */
  const byId = new Map();
  for (const c of Array.isArray(concepts) ? concepts : []) {
    if (!c || typeof c.id !== 'string' || !c.id || byId.has(c.id)) continue;
    byId.set(c.id, {
      id: c.id,
      title: String(c.title ?? c.id).trim() || c.id,
      kind: c.kind === 'prerequisite' ? 'prerequisite' : 'core',
      deps: Array.isArray(c.depends_on) ? c.depends_on.filter((d) => typeof d === 'string') : [],
    });
  }
  for (const n of byId.values()) {
    n.deps = [...new Set(n.deps)].filter((d) => d !== n.id && byId.has(d));
  }
  return { ids: [...byId.keys()], byId };
}

/**
 * Longest-path levels with Kahn's algorithm. If a cycle exists (it cannot after schema
 * validation, but the player must never hang), the remaining node with the fewest unresolved
 * prerequisites is released and its unresolved incoming edges are dropped.
 * @param {string[]} ids
 * @param {Map<string, string[]>} deps   id -> prerequisite ids
 * @returns {{ level: Map<string, number>, edges: Array<[string, string]>, broken: Array<[string, string]> }}
 */
export function assignLevels(ids, deps) {
  /** @type {Map<string, string[]>} */
  const children = new Map(ids.map((id) => [id, []]));
  for (const id of ids) for (const d of deps.get(id) || []) children.get(d)?.push(id);
  const remaining = new Map(ids.map((id) => [id, (deps.get(id) || []).length]));
  const level = new Map(ids.map((id) => [id, 0]));
  /** @type {Set<string>} */
  const done = new Set();
  /** @type {Array<[string, string]>} */
  const broken = [];
  /** @type {string[]} */
  const queue = ids.filter((id) => remaining.get(id) === 0);
  let head = 0;
  while (done.size < ids.length) {
    if (head >= queue.length) {
      /** @type {string | null} */
      let pick = null;
      for (const id of ids) {
        if (done.has(id)) continue;
        if (pick === null || /** @type {number} */ (remaining.get(id)) < /** @type {number} */ (remaining.get(pick))) {
          pick = id;
        }
      }
      if (pick === null) break;
      for (const d of deps.get(pick) || []) if (!done.has(d)) broken.push([d, pick]);
      remaining.set(pick, 0);
      queue.push(pick);
    }
    const id = queue[head++];
    if (done.has(id)) continue;
    done.add(id);
    const lv = /** @type {number} */ (level.get(id));
    for (const c of children.get(id) || []) {
      if (done.has(c)) continue;
      level.set(c, Math.max(/** @type {number} */ (level.get(c)), lv + 1));
      const r = /** @type {number} */ (remaining.get(c)) - 1;
      remaining.set(c, r);
      if (r === 0) queue.push(c);
    }
  }
  const brokenKeys = new Set(broken.map(([a, b]) => `${a}\u0000${b}`));
  /** @type {Array<[string, string]>} */
  const edges = [];
  for (const id of ids) {
    for (const d of deps.get(id) || []) if (!brokenKeys.has(`${d}\u0000${id}`)) edges.push([d, id]);
  }
  return { level, edges, broken };
}

/**
 * Greedy word wrap with hard breaks for over-long words; at most `maxLines` (ellipsis).
 * @param {string} text
 * @param {number} maxChars
 * @param {number} [maxLines]
 * @returns {string[]}
 */
export function wrapLabel(text, maxChars, maxLines = 3) {
  const limit = Math.max(1, Math.floor(maxChars));
  const words = String(text ?? '').trim().split(/\s+/).filter(Boolean);
  /** @type {string[]} */
  const lines = [];
  let cur = '';
  for (let word of words) {
    while (Array.from(word).length > limit) {
      const chars = Array.from(word);
      if (cur) {
        lines.push(cur);
        cur = '';
      }
      lines.push(chars.slice(0, limit).join(''));
      word = chars.slice(limit).join('');
    }
    if (!cur) cur = word;
    else if (Array.from(cur).length + 1 + Array.from(word).length <= limit) cur = `${cur} ${word}`;
    else {
      lines.push(cur);
      cur = word;
    }
  }
  if (cur) lines.push(cur);
  if (lines.length > maxLines) {
    const kept = lines.slice(0, maxLines);
    const last = Array.from(kept[maxLines - 1]);
    kept[maxLines - 1] = `${last.slice(0, Math.max(1, limit - 1)).join('').replace(/\s+$/, '')}${ELLIPSIS}`;
    return kept;
  }
  return lines.length ? lines : [''];
}

/**
 * Number of edge crossings between two adjacent layers (inversion count, O(E log E)).
 * @param {Array<[number, number]>} pairs  (upper position, lower position)
 */
export function countPairCrossings(pairs) {
  const sorted = [...pairs].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const seq = sorted.map((p) => p[1]);
  return mergeCount(seq, 0, seq.length);
}

/**
 * @param {number[]} a
 * @param {number} lo
 * @param {number} hi
 * @returns {number}
 */
function mergeCount(a, lo, hi) {
  if (hi - lo < 2) return 0;
  const mid = (lo + hi) >> 1;
  let count = mergeCount(a, lo, mid) + mergeCount(a, mid, hi);
  /** @type {number[]} */
  const merged = [];
  let i = lo;
  let j = mid;
  while (i < mid && j < hi) {
    if (a[i] <= a[j]) merged.push(a[i++]);
    else {
      count += mid - i;
      merged.push(a[j++]);
    }
  }
  while (i < mid) merged.push(a[i++]);
  while (j < hi) merged.push(a[j++]);
  for (let k = 0; k < merged.length; k++) a[lo + k] = merged[k];
  return count;
}

/**
 * Weighted isotonic regression (pool adjacent violators): nondecreasing fit to `values`.
 * @param {number[]} values
 * @param {number[]} weights
 * @returns {number[]}
 */
export function isotonic(values, weights) {
  /** @type {{ v: number, w: number, n: number }[]} */
  const blocks = [];
  for (let i = 0; i < values.length; i++) {
    blocks.push({ v: values[i], w: weights[i] || 1, n: 1 });
    while (blocks.length > 1 && blocks[blocks.length - 2].v > blocks[blocks.length - 1].v) {
      const b = /** @type {{ v: number, w: number, n: number }} */ (blocks.pop());
      const a = blocks[blocks.length - 1];
      a.v = (a.v * a.w + b.v * b.w) / (a.w + b.w);
      a.w += b.w;
      a.n += b.n;
    }
  }
  /** @type {number[]} */
  const out = [];
  for (const b of blocks) for (let k = 0; k < b.n; k++) out.push(b.v);
  return out;
}

/**
 * Concepts near the active one (ancestors within `up` hops, dependents within `down` hops):
 * keeps big maps legible in a small panel.
 * @param {ConceptInput[]} concepts
 * @param {string | null | undefined} activeId
 * @param {{ up?: number, down?: number }} [opts]
 * @returns {ConceptInput[]}
 */
export function focusConcepts(concepts, activeId, opts = {}) {
  const { ids, byId } = normalizeConcepts(concepts);
  if (!activeId || !byId.has(activeId)) return concepts;
  const up = opts.up ?? 2;
  const down = opts.down ?? 1;
  /** @type {Map<string, string[]>} */
  const children = new Map(ids.map((id) => [id, []]));
  for (const n of byId.values()) for (const d of n.deps) children.get(d)?.push(n.id);
  const keep = new Set([activeId]);
  /**
   * @param {(id: string) => string[]} next
   * @param {number} hops
   */
  const walk = (next, hops) => {
    let frontier = [activeId];
    for (let i = 0; i < hops; i++) {
      /** @type {string[]} */
      const nxt = [];
      for (const id of frontier) {
        for (const m of next(id)) {
          if (!keep.has(m)) {
            keep.add(m);
            nxt.push(m);
          }
        }
      }
      frontier = nxt;
    }
  };
  walk((id) => byId.get(id)?.deps || [], up);
  walk((id) => children.get(id) || [], down);
  return ids
    .filter((id) => keep.has(id))
    .map((id) => {
      const n = /** @type {NormConcept} */ (byId.get(id));
      return { id, title: n.title, kind: n.kind, depends_on: n.deps.filter((d) => keep.has(d)) };
    });
}

/** @param {string} s */
function charFactor(s) {
  // Indic scripts and other non-Latin glyphs are wider than Latin in Inter/Noto.
  return /[^\u0000-ɏ -⁯]/.test(s) ? 0.74 : 0.56;
}

/** @param {number} v */
const r1 = (v) => Math.round(v * 10) / 10;

/**
 * @param {number} v
 * @param {number} lo
 * @param {number} hi
 */
const clampTo = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

/**
 * The layered graph shared by both orientations (ordering is orientation independent).
 * @typedef {object} Graph
 * @property {string[]} ids
 * @property {Map<string, NormConcept>} byId
 * @property {number} levels
 * @property {Vertex[][]} layers         final order within each layer
 * @property {Map<string, Vertex>} vertices  real vertices by concept id
 * @property {Array<[string, string]>} edges
 * @property {Map<string, Vertex[]>} chains  edge key -> vertices from .. to (dummies between)
 * @property {Array<[string, string]>} broken
 * @property {Map<Vertex, number>} pos   index within the layer
 * @property {number} crossings
 */

/**
 * Levels, dummy vertices and crossing reduction.
 * @param {ConceptInput[]} concepts
 * @param {number} iterations
 * @returns {Graph}
 */
function buildGraph(concepts, iterations) {
  const { ids, byId } = normalizeConcepts(concepts);
  const deps = new Map(ids.map((id) => [id, /** @type {NormConcept} */ (byId.get(id)).deps]));
  const { level, edges, broken } = assignLevels(ids, deps);
  const levels = ids.length ? Math.max(...ids.map((id) => /** @type {number} */ (level.get(id)))) + 1 : 0;
  /** @type {Vertex[][]} */
  const layers = Array.from({ length: levels }, () => []);
  /** @type {Map<string, Vertex>} */
  const vertices = new Map();
  for (const id of ids) {
    /** @type {Vertex} */
    const v = { key: id, real: true, layer: /** @type {number} */ (level.get(id)), up: [], down: [] };
    vertices.set(id, v);
    layers[v.layer].push(v);
  }
  /** @type {Map<string, Vertex[]>} */
  const chains = new Map();
  let dummyCount = 0;
  for (const [from, to] of edges) {
    const a = /** @type {Vertex} */ (vertices.get(from));
    const b = /** @type {Vertex} */ (vertices.get(to));
    /** @type {Vertex[]} */
    const chain = [a];
    for (let l = a.layer + 1; l < b.layer; l++) {
      /** @type {Vertex} */
      const d = { key: `\u0000d${dummyCount++}`, real: false, layer: l, up: [], down: [] };
      layers[l].push(d);
      chain.push(d);
    }
    chain.push(b);
    for (let k = 0; k + 1 < chain.length; k++) {
      chain[k].down.push(chain[k + 1]);
      chain[k + 1].up.push(chain[k]);
    }
    chains.set(`${from}\u0000${to}`, chain);
  }

  /** @type {Map<Vertex, number>} */
  const pos = new Map();
  const index = () => {
    for (const layer of layers) layer.forEach((v, i) => pos.set(v, i));
  };
  const crossings = () => {
    let total = 0;
    for (let l = 0; l + 1 < layers.length; l++) {
      /** @type {Array<[number, number]>} */
      const pairs = [];
      for (const u of layers[l]) {
        for (const v of u.down) pairs.push([/** @type {number} */ (pos.get(u)), /** @type {number} */ (pos.get(v))]);
      }
      total += countPairCrossings(pairs);
    }
    return total;
  };
  /**
   * @param {Vertex[]} layer
   * @param {(v: Vertex) => Vertex[]} nbrs
   */
  const reorder = (layer, nbrs) => {
    const keyed = layer.map((v, i) => {
      const ns = nbrs(v);
      const bary = ns.length ? ns.reduce((s, n) => s + /** @type {number} */ (pos.get(n)), 0) / ns.length : i;
      return { v, bary, i };
    });
    keyed.sort((p, q) => p.bary - q.bary || p.i - q.i);
    keyed.forEach((k, i) => {
      layer[i] = k.v;
      pos.set(k.v, i);
    });
  };
  index();
  let best = crossings();
  let bestOrder = layers.map((l) => [...l]);
  for (let it = 0; it < iterations && best > 0; it++) {
    for (let l = 1; l < layers.length; l++) reorder(layers[l], (v) => v.up);
    for (let l = layers.length - 2; l >= 0; l--) reorder(layers[l], (v) => v.down);
    const c = crossings();
    if (c < best) {
      best = c;
      bestOrder = layers.map((l) => [...l]);
    }
  }
  bestOrder.forEach((l, i) => {
    layers[i] = l;
  });
  index();
  return { ids, byId, levels, layers, vertices, edges, chains, broken, pos, crossings: best };
}

/**
 * Geometry of one orientation at one font size. "Cross" is the axis along a layer (x for tb, y for
 * lr); "main" is the flow axis between layers.
 * @typedef {object} Geometry
 * @property {Orientation} orientation
 * @property {number} fontSize
 * @property {number} lineH
 * @property {number} padX
 * @property {Map<string, string[]>} lines
 * @property {(id: string) => number} nodeWidth
 * @property {(id: string) => number} nodeH
 * @property {Map<Vertex, number>} cross   layer-axis centre (before `shift`)
 * @property {number[]} layerMain          flow-axis centre of each layer
 * @property {number} shift
 * @property {number} width
 * @property {number} height
 */

/**
 * @param {Graph} g
 * @param {Orientation} orientation
 * @param {number} f       font size
 * @param {number} boxW
 * @param {number} boxH
 * @param {number} maxLines
 * @returns {Geometry}
 */
function geometry(g, orientation, f, boxW, boxH, maxLines) {
  const tb = orientation === 'tb';
  const { layers, levels } = g;
  const pad = Math.max(6, Math.min(boxW, boxH) * 0.03);
  const padX = f * 0.6;
  const padY = f * 0.45;
  const lineH = f * 1.22;
  const crossGap = tb ? f * 0.9 : f * 0.6;
  const mainGap = tb ? f * 1.5 : f * 2.2;
  let nodeW;
  if (tb) {
    // width budget of the busiest layer (dummy vertices only need a narrow lane)
    const slots = Math.max(1, ...layers.map((l) => l.reduce((n, v) => n + (v.real ? 1 : 0.25), 0)));
    nodeW = (boxW - 2 * pad - crossGap * (slots - 1)) / slots;
  } else {
    const cols = Math.max(1, levels);
    nodeW = (boxW - 2 * pad - mainGap * (cols - 1)) / cols;
  }
  nodeW = clampTo(nodeW, f * MIN_NODE_EM, f * MAX_NODE_EM);
  // Labels wrap at nodeW; each box then shrinks to its longest line, so short labels leave room.
  /** @type {Map<string, string[]>} */
  const lines = new Map();
  /** @type {Map<string, number>} */
  const widths = new Map();
  for (const id of g.ids) {
    const title = /** @type {NormConcept} */ (g.byId.get(id)).title;
    const charW = f * charFactor(title);
    const wrapped = wrapLabel(title, Math.max(5, Math.floor((nodeW - 2 * padX) / charW)), maxLines);
    lines.set(id, wrapped);
    const longest = Math.max(...wrapped.map((l) => Array.from(l).length));
    widths.set(id, clampTo(longest * charW + 2 * padX, f * MIN_BOX_EM, nodeW));
  }
  const nodeWidth = (/** @type {string} */ id) => /** @type {number} */ (widths.get(id));
  const nodeH = (/** @type {string} */ id) => (lines.get(id) || ['']).length * lineH + 2 * padY;
  /** @param {Vertex} v extent along the layer axis */
  const ext = (v) => (v.real ? (tb ? nodeWidth(v.key) : nodeH(v.key)) : 0);
  /** @param {Vertex} a @param {Vertex} b */
  const sep = (a, b) => {
    if (a.real && b.real) return (ext(a) + ext(b)) / 2 + crossGap;
    if (a.real || b.real) return ext(a.real ? a : b) / 2 + crossGap * 0.6;
    return crossGap * 0.6;
  };

  // --- layer-axis positions: start packed, then pull towards neighbours under separation ---
  /** @type {Map<Vertex, number>} */
  const cross = new Map();
  for (const layer of layers) {
    let c = 0;
    layer.forEach((v, i) => {
      if (i > 0) c += sep(layer[i - 1], v);
      cross.set(v, c);
    });
    for (const v of layer) cross.set(v, /** @type {number} */ (cross.get(v)) - c / 2);
  }
  const at = (/** @type {Vertex} */ v) => /** @type {number} */ (cross.get(v));
  /**
   * @param {Vertex[]} layer
   * @param {(v: Vertex) => Vertex[]} nbrs
   */
  const place = (layer, nbrs) => {
    if (!layer.length) return;
    const offsets = [0];
    for (let i = 1; i < layer.length; i++) offsets.push(offsets[i - 1] + sep(layer[i - 1], layer[i]));
    const desired = layer.map((v) => {
      const ns = nbrs(v);
      return ns.length ? ns.reduce((s, n) => s + at(n), 0) / ns.length : at(v);
    });
    const fitted = isotonic(
      desired.map((d, i) => d - offsets[i]),
      layer.map((v) => (v.real ? 1 : 0.6)),
    );
    layer.forEach((v, i) => cross.set(v, fitted[i] + offsets[i]));
  };
  for (let it = 0; it < 6; it++) {
    for (let l = 1; l < layers.length; l++) place(layers[l], (v) => v.up);
    for (let l = layers.length - 2; l >= 0; l--) place(layers[l], (v) => v.down);
  }
  for (let l = 0; l < layers.length; l++) place(layers[l], (v) => [...v.up, ...v.down]);

  // --- flow-axis positions ---
  const thickness = layers.map((layer) => {
    const real = layer.filter((v) => v.real).map((v) => v.key);
    return tb ? Math.max(lineH + 2 * padY, ...real.map(nodeH)) : Math.max(f * MIN_BOX_EM, ...real.map(nodeWidth));
  });
  /** @type {number[]} */
  const layerMain = [];
  let m = pad;
  for (let l = 0; l < levels; l++) {
    layerMain.push(m + thickness[l] / 2);
    m += thickness[l] + mainGap;
  }
  const mainSize = levels ? m - mainGap + pad : 2 * pad;

  let lo = Infinity;
  let hi = -Infinity;
  for (const layer of layers) {
    for (const v of layer) {
      const c = at(v);
      const e = ext(v) / 2;
      lo = Math.min(lo, c - e);
      hi = Math.max(hi, c + e);
    }
  }
  if (!Number.isFinite(lo)) {
    lo = 0;
    hi = 0;
  }
  const crossSize = Math.max(hi - lo + 2 * pad, 2 * pad);
  return {
    orientation,
    fontSize: f,
    lineH,
    padX,
    lines,
    nodeWidth,
    nodeH,
    cross,
    layerMain,
    shift: pad - lo,
    width: r1(tb ? crossSize : mainSize),
    height: r1(tb ? mainSize : crossSize),
  };
}

/**
 * @typedef {object} Fit
 * @property {Geometry} geo
 * @property {number} scale
 * @property {boolean} fits
 * @property {number} score   label size as displayed, never counting a scale-up (fits: the font)
 */

/**
 * Largest font (maxFont .. minFont) whose layout fits the box; when none fits, the most legible.
 * @param {Graph} g
 * @param {Orientation} orientation
 * @param {number} boxW
 * @param {number} boxH
 * @param {{ minFont: number, maxFont: number, maxLines: number }} o
 * @returns {Fit}
 */
function fitOrientation(g, orientation, boxW, boxH, o) {
  let f = Math.min(o.maxFont, Math.max(o.minFont, boxW / 24));
  /** @type {Fit | null} */
  let best = null;
  for (let attempt = 0; attempt < 24; attempt++) {
    const geo = geometry(g, orientation, f, boxW, boxH, o.maxLines);
    const scale = Math.min(MAX_UPSCALE, boxW / geo.width, boxH / geo.height);
    const fits = geo.width <= boxW + 0.5 && geo.height <= boxH + 0.5;
    const fit = { geo, scale, fits, score: f * Math.min(1, scale) };
    if (fits) return fit;
    if (!best || fit.score > best.score + 1e-9) best = fit;
    if (f <= o.minFont) break;
    f = Math.max(o.minFont, f * SHRINK);
  }
  return /** @type {Fit} */ (best);
}

/**
 * Lay out the concept map inside a `width` x `height` box.
 * @param {ConceptInput[]} concepts
 * @param {LayoutOptions} [opts]
 * @returns {SkillTreeLayout}
 */
export function layoutSkillTree(concepts, opts = {}) {
  const boxW = Math.max(120, opts.width || 440);
  const boxH = Math.max(120, opts.height || 640);
  const minFont = opts.minFont ?? 11;
  const o = { minFont, maxFont: Math.max(minFont, opts.maxFont ?? 20), maxLines: Math.max(1, opts.maxLines ?? 3) };
  const g = buildGraph(concepts, Math.max(0, opts.iterations ?? 12));

  /** @type {Fit} */
  let chosen;
  if (opts.orientation === 'tb' || opts.orientation === 'lr') {
    chosen = fitOrientation(g, opts.orientation, boxW, boxH, o);
  } else {
    const tb = fitOrientation(g, 'tb', boxW, boxH, o);
    const lr = fitOrientation(g, 'lr', boxW, boxH, o);
    const useLr = tb.fits ? lr.score > tb.score * LR_ADVANTAGE : lr.score > tb.score;
    chosen = useLr ? lr : tb;
  }
  return buildLayout(g, chosen);
}

/**
 * Turn the chosen geometry into nodes and edges in layout coordinates.
 * @param {Graph} g
 * @param {Fit} fit
 * @returns {SkillTreeLayout}
 */
function buildLayout(g, fit) {
  const { geo } = fit;
  const tb = geo.orientation === 'tb';
  const crossAt = (/** @type {Vertex} */ v) => /** @type {number} */ (geo.cross.get(v)) + geo.shift;
  /** @type {LayoutNode[]} */
  const nodes = g.ids.map((id) => {
    const v = /** @type {Vertex} */ (g.vertices.get(id));
    const n = /** @type {NormConcept} */ (g.byId.get(id));
    const c = crossAt(v);
    const mPos = geo.layerMain[v.layer];
    return {
      id,
      title: n.title,
      kind: n.kind,
      level: v.layer,
      order: /** @type {number} */ (g.pos.get(v)),
      x: r1(tb ? c : mPos),
      y: r1(tb ? mPos : c),
      w: r1(geo.nodeWidth(id)),
      h: r1(geo.nodeH(id)),
      lines: geo.lines.get(id) || [n.title],
    };
  });
  const nodeById = new Map(nodes.map((n) => [n.id, n]));
  /** @type {LayoutEdge[]} */
  const edges = [];
  for (const [from, to] of g.edges) {
    const chain = /** @type {Vertex[]} */ (g.chains.get(`${from}\u0000${to}`));
    const a = /** @type {LayoutNode} */ (nodeById.get(from));
    const b = /** @type {LayoutNode} */ (nodeById.get(to));
    const points = [tb ? { x: a.x, y: r1(a.y + a.h / 2) } : { x: r1(a.x + a.w / 2), y: a.y }];
    for (const d of chain.slice(1, -1)) {
      const c = r1(crossAt(d));
      const mPos = r1(geo.layerMain[d.layer]);
      points.push(tb ? { x: c, y: mPos } : { x: mPos, y: c });
    }
    points.push(tb ? { x: b.x, y: r1(b.y - b.h / 2) } : { x: r1(b.x - b.w / 2), y: b.y });
    edges.push({ from, to, points, d: curvePath(points, geo.orientation) });
  }
  return {
    orientation: geo.orientation,
    width: geo.width,
    height: geo.height,
    fontSize: r1(geo.fontSize),
    lineHeight: r1(geo.lineH),
    padX: r1(geo.padX),
    levels: g.levels,
    scale: Math.round(fit.scale * 1000) / 1000,
    renderedFont: r1(geo.fontSize * fit.scale),
    fits: fit.fits,
    nodes,
    edges,
    brokenEdges: g.broken,
    crossings: g.crossings,
  };
}

/**
 * Smooth path through points using cubic segments whose tangents follow the flow direction at
 * every point (vertical for 'tb', horizontal for 'lr').
 * @param {{ x: number, y: number }[]} points
 * @param {Orientation} [orientation]
 */
export function curvePath(points, orientation = 'tb') {
  if (!points.length) return '';
  let d = `M${points[0].x} ${points[0].y}`;
  for (let i = 1; i < points.length; i++) {
    const p = points[i - 1];
    const q = points[i];
    if (orientation === 'lr') {
      const mx = r1((p.x + q.x) / 2);
      d += ` C${mx} ${p.y} ${mx} ${q.y} ${q.x} ${q.y}`;
    } else {
      const my = r1((p.y + q.y) / 2);
      d += ` C${p.x} ${my} ${q.x} ${my} ${q.x} ${q.y}`;
    }
  }
  return d;
}
