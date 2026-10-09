// @ts-check
/**
 * Rich-lite: the tiny inline markup allowed in board text, table cells and quiz text
 * (aadhi/schemas/screenplay.py RICH_LITE). It is TOKENISED into a small AST and turned into DOM
 * nodes with createElement/createTextNode — it is never parsed as HTML, so model output cannot
 * inject markup.
 *
 *   **bold**   *italic*   `code`   $inline TeX$   [[keyword]]   \$ \* \` \[ \] \\ escapes
 *
 * Rules (flanking so "2 * 3", "2*3*4", "x**2" and "$5 and $10" stay literal):
 *   - `*`/`**` open only before a non-space and close only after a non-space;
 *   - intraword asterisks are literal (the backend tokenizer's rule, aadhi/pipeline/richlite.py):
 *     the `*` run of an opener must not follow a word character and the `*` run of a closer must
 *     not be followed by one, so "V*I*t" and "x**2 + y**2" keep their multiplication/power signs;
 *   - `$` opens before a non-space (not `$$`, unless the first `$` is an escaped `\$`); a closing
 *     `$` follows a non-space and is not followed by a digit; `\$` inside math stays TeX;
 *   - unmatched delimiters are literal text; bold/italic/keyword nest, code and math are leaves.
 */

import { renderTex as libRenderTex } from '../shared/libs.js';

/**
 * @typedef {{t: 'text', v: string} | {t: 'code', v: string} | {t: 'math', v: string}
 *   | {t: 'b', c: RichNode[]} | {t: 'i', c: RichNode[]} | {t: 'k', c: RichNode[]}} RichNode
 */

/** @typedef {(latex: string, display: boolean) => Promise<Element>} TexRenderer */

const ESCAPABLE = new Set(['\\', '$', '*', '`', '[', ']', '_']);
const MAX_DEPTH = 6;
const MAX_MATH = 1200;
/** Scan budget (characters visited by all nested attempts): beyond it, no new span opens, so
 * hostile input stays linear while the result remains a pure function of the text. */
const SCAN_BUDGET_BASE = 60000;
const SCAN_BUDGET_PER_CHAR = 24;

/** @param {string | undefined} ch */
const isSpace = (ch) => ch === undefined || /\s/.test(ch);

/** Letters (incl. combining marks of Indic scripts), digits and `_`: a word character. */
const WORD_CHAR = /[\p{L}\p{M}\p{N}_]/u;

/** @param {string | undefined} ch */
const isWord = (ch) => ch !== undefined && WORD_CHAR.test(ch);

/**
 * Flanking context of every `*` in O(n): before[i] = a word character precedes the `*` run that
 * contains position i; after[i] = a word character follows it.
 * @param {string} src
 * @returns {{ before: Uint8Array, after: Uint8Array }}
 */
function starRuns(src) {
  const n = src.length;
  const before = new Uint8Array(n);
  const after = new Uint8Array(n);
  for (let i = 0; i < n; i++) {
    if (src[i] !== '*') continue;
    before[i] = i > 0 && src[i - 1] === '*' ? before[i - 1] : isWord(src[i - 1]) ? 1 : 0;
  }
  for (let i = n - 1; i >= 0; i--) {
    if (src[i] !== '*') continue;
    after[i] = src[i + 1] === '*' ? after[i + 1] : isWord(src[i + 1]) ? 1 : 0;
  }
  return { before, after };
}

/**
 * Parse rich-lite text into an AST (pure; safe for any input: worst case O(n^2) time, O(n) memory).
 * @param {string | null | undefined} text
 * @returns {RichNode[]}
 */
export function parseRich(text) {
  const src = String(text ?? '');
  const runs = starRuns(src);
  /** @type {Map<string, {nodes: RichNode[], end: number, closed: boolean}>} */
  const memo = new Map();
  const budget = SCAN_BUDGET_BASE + SCAN_BUDGET_PER_CHAR * src.length;
  let steps = 0;
  /**
   * @param {string} closer
   * @param {number} i
   */
  const closesAt = (closer, i) =>
    i > 0 && src.startsWith(closer, i) && !isSpace(src[i - 1]) && !((closer === '*' || closer === '**') && runs.after[i]);
  /** Last position where each closer could close (-1: nowhere): spans that cannot close fail at once. */
  /** @type {Record<string, number>} */
  const lastCloser = { '*': -1, '**': -1, ']]': -1 };
  for (const c of Object.keys(lastCloser)) {
    for (let i = src.length - c.length; i > 0; i--) {
      if (closesAt(c, i)) {
        lastCloser[c] = i;
        break;
      }
    }
  }

  /**
   * Find the closing `$` of inline math whose content starts at `from`, or -1.
   * @param {number} from
   */
  function mathClose(from) {
    if (isSpace(src[from]) || src[from] === '$') return -1;
    for (let j = from; j < src.length && j - from <= MAX_MATH; j++) {
      const ch = src[j];
      if (ch === '\\') {
        j++;
        continue;
      }
      if (ch === '$') {
        if (src[j + 1] === '$') return -1; // `$$` (display math) is not part of rich-lite
        if (!isSpace(src[j - 1]) && !/[0-9]/.test(src[j + 1] || '')) return j;
      }
    }
    return -1;
  }

  /**
   * @param {number} start
   * @param {string} closer   '' for the top level
   * @param {number} depth
   * @returns {{nodes: RichNode[], end: number, closed: boolean}}
   */
  function parse(start, closer, depth) {
    const key = `${closer}|${start}|${depth}`;
    const hit = memo.get(key);
    if (hit) return hit;
    if (closer && lastCloser[closer] <= start) {
      const res = { nodes: [], end: src.length, closed: false };
      memo.set(key, res);
      return res;
    }
    /** @type {RichNode[]} */
    const nodes = [];
    let buf = '';
    const flush = () => {
      if (buf) nodes.push({ t: 'text', v: buf });
      buf = '';
    };
    /**
     * Try a nested span; returns the position after it, or -1 when it is not closed/empty.
     * @param {'b' | 'i' | 'k'} type
     * @param {number} contentStart
     * @param {string} close
     */
    const nested = (type, contentStart, close) => {
      if (depth >= MAX_DEPTH || steps > budget) return -1;
      const r = parse(contentStart, close, depth + 1);
      if (!r.closed || r.nodes.length === 0) return -1;
      flush();
      nodes.push({ t: type, c: r.nodes });
      return r.end;
    };
    /** Index of the last character produced by an escape (`\$` before `$x$` must not block it). */
    let escapedAt = -1;
    let i = start;
    while (i < src.length) {
      steps++;
      if (closer && i > start && closesAt(closer, i)) {
        flush();
        const res = { nodes, end: i + closer.length, closed: true };
        memo.set(key, res);
        return res;
      }
      const ch = src[i];
      if (ch === '\\' && ESCAPABLE.has(src[i + 1])) {
        buf += src[i + 1];
        escapedAt = i + 1;
        i += 2;
        continue;
      }
      if (ch === '`') {
        const j = src.indexOf('`', i + 1);
        if (j > i + 1) {
          flush();
          nodes.push({ t: 'code', v: src.slice(i + 1, j) });
          i = j + 1;
          continue;
        }
      } else if (ch === '$' && (src[i - 1] !== '$' || escapedAt === i - 1)) {
        const j = mathClose(i + 1);
        if (j > i + 1) {
          flush();
          nodes.push({ t: 'math', v: src.slice(i + 1, j) });
          i = j + 1;
          continue;
        }
      } else if (ch === '[' && src[i + 1] === '[') {
        const after = nested('k', i + 2, ']]');
        if (after >= 0) {
          i = after;
          continue;
        }
      } else if (ch === '*' && !runs.before[i]) {
        if (src[i + 1] === '*' && !isSpace(src[i + 2])) {
          const after = nested('b', i + 2, '**');
          if (after >= 0) {
            i = after;
            continue;
          }
        }
        if (!isSpace(src[i + 1]) && src[i + 1] !== '*') {
          const after = nested('i', i + 1, '*');
          if (after >= 0) {
            i = after;
            continue;
          }
        }
      }
      buf += ch;
      i++;
    }
    flush();
    // An unclosed nested span is discarded by its caller: do not keep its nodes (memory stays O(n)).
    const res = { nodes: closer ? [] : nodes, end: i, closed: false };
    memo.set(key, res);
    return res;
  }

  return mergeText(parse(0, '', 0).nodes);
}

/**
 * Merge adjacent text nodes (recursively).
 * @param {RichNode[]} nodes
 * @returns {RichNode[]}
 */
function mergeText(nodes) {
  /** @type {RichNode[]} */
  const out = [];
  for (const n of nodes) {
    const last = out[out.length - 1];
    if (n.t === 'text' && last && last.t === 'text') {
      out[out.length - 1] = { t: 'text', v: last.v + n.v };
    } else if (n.t === 'b' || n.t === 'i' || n.t === 'k') {
      out.push({ t: n.t, c: mergeText(n.c) });
    } else {
      out.push(n);
    }
  }
  return out;
}

/**
 * Plain text of rich-lite (markup removed; math and code keep their source).
 * @param {string | null | undefined} text
 * @returns {string}
 */
export function plainText(text) {
  /** @param {RichNode[]} nodes @returns {string} */
  const walk = (nodes) => nodes.map((n) => ('v' in n ? n.v : walk(n.c))).join('');
  return walk(parseRich(text));
}

/**
 * Extract every inline TeX string (used to pre-warm MathJax for the next scene).
 * @param {string | null | undefined} text
 * @returns {string[]}
 */
export function mathSpans(text) {
  /** @type {string[]} */
  const out = [];
  /** @param {RichNode[]} nodes */
  const walk = (nodes) => {
    for (const n of nodes) {
      if (n.t === 'math') out.push(n.v);
      else if ('c' in n) walk(n.c);
    }
  };
  walk(parseRich(text));
  return out;
}

// ---------------------------------------------------------------------------------------------
// TeX rendering with a small cache (clones of converted nodes)
// ---------------------------------------------------------------------------------------------

/** @type {Map<string, Promise<Element>>} */
const texCache = new Map();
const TEX_CACHE_MAX = 400;

/**
 * Convert TeX once per (latex, display, renderer) and hand out deep clones.
 * @param {string} latex
 * @param {boolean} display
 * @param {TexRenderer} [render]
 * @returns {Promise<Element>}
 */
export function texNode(latex, display, render = libRenderTex) {
  if (render !== libRenderTex) return render(latex, display); // injected renderers (tests) are not cached
  const key = `${display ? 'D' : 'I'}:${latex}`;
  let p = texCache.get(key);
  if (!p) {
    p = render(latex, display);
    p.catch(() => texCache.delete(key));
    if (texCache.size >= TEX_CACHE_MAX) {
      const oldest = texCache.keys().next().value;
      if (oldest !== undefined) texCache.delete(oldest);
    }
    texCache.set(key, p);
  }
  return p.then((node) => /** @type {Element} */ (node.cloneNode(true)));
}

/**
 * Render TeX into `host` (a span/div). Shows the source as a hidden fallback until MathJax is done;
 * on failure the source stays visible in monospace (class is-error).
 * @param {HTMLElement} host
 * @param {string} latex
 * @param {boolean} display
 * @param {{ renderTex?: TexRenderer, pending?: Promise<unknown>[] }} [opts]
 */
export function mountTex(host, latex, display, opts = {}) {
  host.classList.add('rt-math', 'is-pending');
  host.dataset.display = display ? '1' : '0';
  host.textContent = latex;
  const p = texNode(latex, display, opts.renderTex || libRenderTex).then(
    (node) => {
      host.replaceChildren(node);
      host.classList.remove('is-pending');
    },
    () => {
      host.classList.remove('is-pending');
      host.classList.add('is-error');
    },
  );
  if (opts.pending) opts.pending.push(p);
  return p;
}

/**
 * Rich-lite text -> DocumentFragment (text nodes and a fixed set of elements only).
 * @param {string | null | undefined} text
 * @param {{ renderTex?: TexRenderer, pending?: Promise<unknown>[] }} [opts]
 *   renderTex: TeX converter (defaults to shared/libs.js renderTex);
 *   pending: collects the TeX promises so callers can await them (render mode).
 * @returns {DocumentFragment}
 */
export function renderRich(text, opts = {}) {
  const frag = document.createDocumentFragment();
  /**
   * @param {Node} parent
   * @param {RichNode[]} nodes
   */
  const build = (parent, nodes) => {
    for (const n of nodes) {
      if (n.t === 'text') {
        parent.appendChild(document.createTextNode(n.v));
      } else if (n.t === 'code') {
        const el = document.createElement('code');
        el.className = 'rt-code';
        el.textContent = n.v;
        parent.appendChild(el);
      } else if (n.t === 'math') {
        const el = document.createElement('span');
        parent.appendChild(el);
        mountTex(el, n.v, false, opts);
      } else {
        const el = document.createElement(n.t === 'b' ? 'strong' : n.t === 'i' ? 'em' : 'span');
        el.className = n.t === 'k' ? 'rt-k' : n.t === 'b' ? 'rt-b' : 'rt-i';
        build(el, n.c);
        parent.appendChild(el);
      }
    }
  };
  build(frag, parseRich(text));
  return frag;
}
