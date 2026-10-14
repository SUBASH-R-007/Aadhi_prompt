// @ts-check
// Determinism source scan (DYNAMIC_VIDEO_DESIGN §7.4.2, §4.2). Every visual property of the Dynamic stage
// must be a pure function of (timeline, frame), so the stage code may not read a clock, draw unseeded
// random numbers, run its own animation loop or timers, or let CSS / libraries animate on their own.
// The JavaScript under web/js/dynamic/** and web/js/motion/** is parsed with the TypeScript compiler API
// (no ESLint); web/css/dynamic.css, web/css/dynamic-fonts.css (B2b, generated) and the render page's inline styles are
// scanned as CSS.
//
// Escape hatch: infrastructure (never web/js/motion/** and never a template) may run the stage's own
// requestAnimationFrame, a pre-`ready` timer or a live-mode <video> with a pragma on the same line or the
// line above, which must give a reason:  // determinism-allow raf: seek() waits one frame (§7.3 step 6)
// The clock, randomness, CSS-motion, library, DOM-purity, structure-role, emphasis, template-style, tex, timeline and
// catalogue rules have none.
//
// B2 additions (plan §P, §1.1, §1.4, §2): a template may not write will-change, filter or backdrop-filter
// (order-dependent layers and per-frame raster work) nor outline or text-decoration (emphasis is cues.js's) through ANY
// path the scan knows: a style property, setProperty, Object.assign on a style, CSS text, the Painter
// (`ctx.paint.style(el, prop, v)`, `paint.attr(el, name, v)`), an h()/svg() style object or SVG presentation attribute,
// setAttribute / setAttributeNS / toggleAttribute, the CSS Typed OM (`attributeStyleMap.set / append`) and attributes
// in markup (any string or template literal: innerHTML, outerHTML, insertAdjacentHTML, ...); a property or attribute
// name, or markup, the scan cannot read (a computed one, a variable) is refused in templates, and so are
// setAttributeNode and any other use of attributeStyleMap. A name is matched both as a camelCase style key and
// case-insensitively (`FILTER="..."` in markup, `setProperty('FILTER', ...)`: the parser, setProperty and the Typed OM
// lower-case it). Templates never read `.timeline` (the context carries what they show), and convert TeX (mountTex,
// renderRich, texNode) only through `ctx.renderTex`, the render page's counted converter: each call hands on
// `ctx.renderTex` (or a `renderTex` destructured from the context; never another object's `.renderTex`) as the last
// word on that option, the helpers are imported statically by their own names and only ever called (never aliased,
// re-bound, re-exported under another name or handed on as values), and
// MathJax and shared/libs.js's renderTex / loadMathJax are not used at all. Limits (not followed): an alias of a style
// object or of a write function (`const s = el.style; s.filter = ...`), Reflect.set / Object.defineProperty on a
// style, a markup attribute name produced by a substitution (`<rect ${name}="...">`), stylesheets other than dynamic.css,
// dynamic-fonts.css and the render page's; a local `renderTex` that is not the context's, a context not named `ctx`, and a module outside
// templates/ that converts TeX for a template.
// Only core.js imports the generated catalogue, as `{ CATALOGUE }`; every read of it follows a member path the plan
// allows at run time (geometry only: CATALOGUE_RUNTIME), so no colour, duration or personality value reaches the stage
// from it; CATALOGUE is never handed on as a value; and web/js/dynamic/catalogue.gen.js itself is data: one frozen
// literal.
// The generated face table (web/js/dynamic/faces.gen.js, B2b) is data under the same rules, and only fonts.js reads it.
// dynamic.css (whose classes the templates set) declares none of those properties but one measured will-change.
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const REPO = fileURLToPath(new URL('../../../', import.meta.url));
const SCANNED_JS_DIRS = ['web/js/dynamic', 'web/js/motion'];
const SCANNED_CSS = ['web/css/dynamic.css', 'web/css/dynamic-fonts.css'];
const RENDER_PAGE = 'web/render-dynamic.html';
const TEMPLATES_DIR = 'web/js/dynamic/templates/';
const MOTION_DIR = 'web/js/motion/';
/** The generated catalogue (plan §1.1) and its one reader. */
const CATALOGUE_FILE = 'web/js/dynamic/catalogue.gen.js';
const CATALOGUE_READERS = new Set(['web/js/dynamic/core.js']);
/** The generated face table (B2b; aadhi/compose/dynamic/faces.py) and its one reader in the stage. */
const FACES_FILE = 'web/js/dynamic/faces.gen.js';
const FACES_READERS = new Set(['web/js/dynamic/fonts.js']);
/**
 * What the stage may read off CATALOGUE (plan §1.1: runtime use is geometry only), as member paths (`*`: any key). A
 * read is allowed when one of these is a prefix of its path: the zones, regions, grid, end-screen half (a format's
 * `box`; its `seconds` sit apart) and platform zones; the type floors and scale; the layout tokens; a caption style's
 * `metrics` (its geometry; its `colours` sit apart); a template's `slots`; and the vocabularies (role names, target
 * forms, the versions). Everything else (the families, content palettes, cue colours, personalities, scrims table,
 * studio surface, caption colours, the end-screen length, template durations, transition lengths, backgrounds, the
 * class table, a computed key, CATALOGUE itself as a value) is for the gallery and the parity tests.
 */
const CATALOGUE_RUNTIME = [
  ['version'], ['zones_version'], ['zones'], ['regions'], ['grid'], ['end_screen', '*', 'box'], ['platforms'], ['type'],
  ['layout'], ['captions', '*', 'metrics'], ['templates', '*', 'slots'], ['roles'], ['targets'],
];

/** Rule ids and what they forbid (shown in failure messages). */
const RULES = {
  clock: 'wall clock (Date.now, performance.now, new Date(), Date(), document.timeline)',
  random: 'unseeded randomness (Math.random, crypto.getRandomValues/randomUUID): use motion/random.js',
  raf: 'requestAnimationFrame outside the stage loop (templates and motion may not own one)',
  timer: 'timer (setTimeout, setInterval, setImmediate, requestIdleCallback)',
  'css-motion': 'CSS transition, animation or @keyframes',
  waapi: 'Web Animations API (element.animate, new Animation, new KeyframeEffect) runs on the document clock',
  'chart-animation': 'Chart.js animation not switched off (options.animation must be false)',
  'self-clocked': 'self-clocked or banned animation library (§7.13: GSAP, tsParticles, Remotion, anime.js, Lottie, Rive, ...)',
  video: '<video> in stage code (render mode shows pre-extracted frames, §7.7)',
  dom: 'DOM or host global in web/js/motion (the toolkit is pure)',
  'struct-role': 'structure colour role (struct.*) on a content element (§4.2)',
  emphasis: 'emphasis treatment outside cues.js (mark/focus roles, outline, text-decoration; §4.2, §4.8)',
  'template-style': 'will-change, filter or backdrop-filter written by a template, or a style / attribute name the scan cannot read (plan §P)',
  tex: 'a template converts TeX without ctx.renderTex (mountTex / renderRich / texNode called without it, aliased or imported another way; MathJax or shared/libs.js directly): the render page caches and counts every conversion through it (plan §2)',
  timeline: 'a template reads the timeline (plan §1.4: no ctx.timeline; the context and look.refs carry what it shows)',
  catalogue: 'the generated catalogue imported outside core.js, or a part of it read that is not geometry (plan §1.1: zones, regions, grid, end-screen box, platforms, type, layout, caption metrics, template slots, vocabularies only)',
  'catalogue-data': 'web/js/dynamic/catalogue.gen.js must be data only: one frozen literal (generated by aadhi.compose.dynamic.catalogue)',
  'faces-data': 'web/js/dynamic/faces.gen.js must be data only: one frozen literal (generated by aadhi.compose.dynamic.faces)',
};
/** Rules a reasoned pragma may waive, outside web/js/motion and the templates. */
const PRAGMA_RULES = new Set(['raf', 'timer', 'video']);
const PRAGMA_RE = /determinism-allow\s+([a-z-]+)\s*:\s*(\S.{4,})/;

const TIMERS = new Set(['setTimeout', 'setInterval', 'setImmediate', 'requestIdleCallback']);
const HOST_OBJECTS = new Set(['window', 'globalThis', 'self']);
const SELF_CLOCKED_GLOBALS = new Set(['gsap', 'tsParticles', 'anime', 'lottie', 'Lottie', 'Rive', 'Motion', 'Velocity', 'Popmotion']);
/** npm package names (bare specifiers, or a vendor/<package>/ directory) of self-clocked or banned libraries. */
const SELF_CLOCKED_PACKAGES =
  /^(gsap|tsparticles(-[\w-]+)?|@tsparticles\/[\w-]+|remotion|@remotion\/[\w-]+|animejs|lottie-web|lottie|@lottiefiles\/[\w-]+|@rive-app\/[\w-]+|@theatre\/[\w-]+|popmotion|framer-motion|motion|velocity-animate|@mojs\/[\w-]+|@motionone\/[\w-]+|aos)$/i;
/** File names of their builds (anime.esm.js, gsap.min.js, lottie_light.js, rive.js, ...). */
const SELF_CLOCKED_FILES = /^(gsap|tsparticles|anime|lottie|dotlottie|rive|popmotion|velocity|mo\.?js|theatre)([._-][\w.-]*)?\.m?js$/i;

/**
 * Whether an import specifier or script URL names a self-clocked or banned library. Our own modules
 * (`../motion/easing.js`, `/web/js/dynamic/...`) never match: only bare package names, `vendor/<package>/`
 * directories and the libraries' build file names do.
 * @param {string} spec
 */
function isSelfClockedModule(spec) {
  const path = spec.split(/[?#]/)[0];
  const isPath = /^(\.{1,2}\/|\/|[a-z]+:)/i.test(path);
  if (!isPath) {
    const pkg = path.startsWith('@') ? path.split('/').slice(0, 2).join('/') : path.split('/')[0];
    return SELF_CLOCKED_PACKAGES.test(pkg);
  }
  const vendor = /\/vendor\/((?:@[\w.-]+\/)?[\w.-]+)/i.exec(path);
  if (vendor && SELF_CLOCKED_PACKAGES.test(vendor[1])) return true;
  return SELF_CLOCKED_FILES.test(path.split('/').pop() || '');
}

/**
 * Whether an import specifier names the generated catalogue (web/js/dynamic/catalogue.gen.js, by any relative path).
 * @param {string} spec
 */
function isCatalogueModule(spec) {
  return /(^|\/)catalogue\.gen\.js$/.test(spec.split(/[?#]/)[0]);
}

/**
 * Whether a declaration importing the catalogue is `import { CATALOGUE } from '...'` exactly: no default or namespace
 * import, no rename, no re-export (the scan follows reads of the name CATALOGUE).
 * @param {ts.ImportDeclaration | ts.ExportDeclaration} node
 */
function isPlainCatalogueImport(node) {
  if (!ts.isImportDeclaration(node)) return false;
  const clause = node.importClause;
  if (!clause || clause.name || clause.isTypeOnly || !clause.namedBindings || !ts.isNamedImports(clause.namedBindings)) return false;
  const els = clause.namedBindings.elements;
  return els.length === 1 && els[0].name.text === 'CATALOGUE' && (!els[0].propertyName || els[0].propertyName.text === 'CATALOGUE');
}

/**
 * Whether `expr` is the global `name` itself or read off a host object (window.performance, self.Math).
 * Local look-alikes (`state.performance`, `rng.Math`) do not count.
 * @param {ts.Node} expr @param {string} name
 */
function isGlobalRef(expr, name) {
  if (ts.isParenthesizedExpression(expr)) return isGlobalRef(expr.expression, name);
  if (ts.isIdentifier(expr)) return expr.text === name;
  if ((ts.isPropertyAccessExpression(expr) || ts.isElementAccessExpression(expr)) && memberName(expr) === name) {
    return [...HOST_OBJECTS].some((h) => isGlobalRef(expr.expression, h));
  }
  return false;
}

/** Host and DOM globals the pure motion toolkit may not touch. */
const DOM_GLOBALS = new Set([
  'window', 'document', 'navigator', 'location', 'history', 'localStorage', 'sessionStorage', 'indexedDB',
  'fetch', 'XMLHttpRequest', 'WebSocket', 'getComputedStyle', 'matchMedia', 'globalThis', 'self', 'HTMLElement',
  'Element', 'customElements', 'Image', 'Audio', 'OffscreenCanvas', 'CSS', 'screen', 'devicePixelRatio',
  'requestAnimationFrame', 'cancelAnimationFrame', 'MutationObserver', 'ResizeObserver', 'IntersectionObserver',
]);
/** A CSS declaration of transition/animation in CSS or in a JS string; `none` / `0s` resets are allowed. */
const CSS_MOTION_DECL = /(?:^|[;{\s"'`])((?:-webkit-|-moz-)?(?:transition|animation)(?:-[a-z-]+)?)\s*:\s*([^;}"'`]*)/gi;
const CSS_MOTION_ALLOWED_VALUE = /^(none|0m?s|all\s+0m?s|initial|unset)(\s*!important)?$/i;
const CSS_KEYFRAMES = /@(?:-webkit-|-moz-)?keyframes\b/i;
const CSS_SMOOTH_SCROLL = /scroll-behavior\s*:\s*smooth/i;
const CSS_EMPHASIS_DECL = /(?:^|[;{\s"'`])(text-decoration(?:-[a-z-]+)?|outline(?:-[a-z-]+)?)\s*:\s*([^;}"'`]*)/gi;
/** will-change, filter and backdrop-filter in CSS text (templates: plan §P); a reset is allowed, as for emphasis. */
const CSS_LAYER_DECL = /(?:^|[;{\s"'`])((?:-webkit-|-moz-)?(?:will-change|backdrop-filter|filter))\s*:\s*([^;}"'`]*)/gi;
const CSS_LAYER_ALLOWED_VALUE = /^(none|auto|initial|unset)(\s*!important)?$/i;
/** dynamic.css declarations of the properties templates may not write: only these (selector -> property) exist, each
 * with its measurement in the stylesheet (a contain_blur clip's box is the one promoted layer, dynamic.css). */
const CSS_STYLE_ALLOWED = new Map([['.dyn-media--contain_blur.dyn-media--clip', 'will-change']]);
/** The TeX helpers of web/js/player/richtext.js and where each takes its converter (options object or argument). */
const TEX_HELPERS = new Map([['mountTex', { index: 3, options: true }], ['renderRich', { index: 1, options: true }], ['texNode', { index: 2, options: false }]]);
/** The modules that convert TeX: the helpers' (player/richtext.js) and the converter behind them (shared/libs.js), whose
 * own converter and MathJax loader a template may not import. */
const TEX_MODULES = /(^|\/)(richtext|libs)\.js$/;
const LIBS_MODULE = /(^|\/)libs\.js$/;
const LIBS_TEX = new Set(['renderTex', 'loadMathJax']);
/** An attribute in a tag of markup a template writes (innerHTML, insertAdjacentHTML, ...) naming a property templates
 * may not write (`filter="..."`, `text-decoration="..."`; a style attribute is read as CSS text). */
const MARKUP_ATTR = /<[a-z][\w:-]*\b[^<>]*?[\s/"']((?:-webkit-|-moz-)?(?:will-change|backdrop-filter|filter|outline(?:-[a-z]+)?|text-decoration(?:-[a-z]+)?))\s*=/gi;
/** The DOM's markup sinks: properties, and calls with the index of their markup argument. */
const MARKUP_PROPS = new Set(['innerHTML', 'outerHTML']);
const MARKUP_CALLS = new Map([['insertAdjacentHTML', 1], ['createContextualFragment', 0], ['parseFromString', 0], ['write', 0], ['writeln', 0], ['setHTMLUnsafe', 0]]);

/** Content elements a structure role may not colour (§4.2), and structure parts that may carry it. */
const CONTENT_NOUNS = new Set(['item', 'items', 'node', 'nodes', 'row', 'rows', 'bar', 'bars', 'tile', 'tiles', 'symbol', 'symbols', 'caption', 'captions']);
const STRUCTURE_NOUNS = new Set(['progress', 'pip', 'pips', 'bumper', 'border', 'borders', 'wipe', 'tint', 'background', 'bg', 'backdrop', 'chrome', 'block', 'band', 'rule', 'divider']);

/**
 * @typedef {{file: string, line: number, rule: string, detail: string}} Finding
 */

/** @param {string} rel */
const isMotion = (rel) => rel.startsWith(MOTION_DIR);
/** @param {string} rel */
const isTemplate = (rel) => rel.startsWith(TEMPLATES_DIR);

/** Words of identifiers and strings: camelCase, snake_case, kebab-case and dotted names split apart. */
function words(text) {
  return String(text)
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter(Boolean);
}

/**
 * Whether `expr` is the name `name`, or ends with `.name` / `['name']` (window.performance, self.Math, ...).
 * @param {ts.Node} expr @param {string} name
 */
function endsWithName(expr, name) {
  if (ts.isIdentifier(expr)) return expr.text === name;
  if (ts.isPropertyAccessExpression(expr)) return expr.name.text === name;
  if (ts.isElementAccessExpression(expr)) return ts.isStringLiteralLike(expr.argumentExpression) && expr.argumentExpression.text === name;
  if (ts.isParenthesizedExpression(expr)) return endsWithName(expr.expression, name);
  return false;
}

/** The member name of a property or element access, or null. */
function memberName(node) {
  if (ts.isPropertyAccessExpression(node)) return node.name.text;
  if (ts.isElementAccessExpression(node) && ts.isStringLiteralLike(node.argumentExpression)) return node.argumentExpression.text;
  return null;
}

/**
 * Whether member path `path` (keys, `*` for a computed one) starts with pattern `pattern` (`*` there: any key).
 * @param {string[]} pattern @param {string[]} path
 */
function pathStartsWith(pattern, path) {
  return pattern.length <= path.length && pattern.every((k, i) => k === '*' || k === path[i]);
}

/**
 * Whether the stage may read member path `path` of CATALOGUE (plan §1.1): some CATALOGUE_RUNTIME entry is a prefix of
 * it. With `keysOnly` (the path is only tested for which keys it has: `k in P`, an own-property test) it is enough that
 * an entry continues the path with any key (`templates` for `templates.*.slots`).
 * @param {string[]} path @param {boolean} [keysOnly]
 */
function catalogueReadAllowed(path, keysOnly = false) {
  return CATALOGUE_RUNTIME.some((pattern) => pathStartsWith(pattern, path) || (keysOnly && pathStartsWith(pattern.slice(0, path.length + 1), [...path, '*']) && pattern.length > path.length));
}

/**
 * The member path a reference to CATALOGUE reads, through parentheses and JSDoc casts (`CATALOGUE.captions[s].metrics`
 * reads ['captions', '*', 'metrics']; `*` is a key the scan cannot name), and the outermost node of that read.
 * @param {ts.Identifier} id
 * @returns {{path: string[], top: ts.Node}}
 */
function cataloguePath(id) {
  /** @type {string[]} */
  const path = [];
  /** @type {ts.Node} */
  let n = id;
  for (let p = n.parent; p; p = n.parent) {
    if (ts.isParenthesizedExpression(p) || ts.isNonNullExpression(p)) {
      n = p;
    } else if ((ts.isPropertyAccessExpression(p) || ts.isElementAccessExpression(p)) && p.expression === n) {
      path.push(memberName(p) ?? '*');
      n = p;
    } else {
      break;
    }
  }
  return { path, top: n };
}

/**
 * The member paths an object or array binding pattern reads off a value at `prefix` (a rest element reads all of it).
 * @param {ts.BindingPattern} pattern @param {string[]} prefix
 * @returns {string[][]}
 */
function bindingPaths(pattern, prefix) {
  /** @type {string[][]} */
  const out = [];
  for (const el of pattern.elements) {
    if (ts.isOmittedExpression(el)) continue;
    if (el.dotDotDotToken) {
      out.push([...prefix]);
      continue;
    }
    let key = '*';
    if (ts.isObjectBindingPattern(pattern)) {
      const name = el.propertyName || el.name;
      key = ts.isIdentifier(name) || ts.isStringLiteralLike(name) ? name.text : '*';
    }
    if (ts.isObjectBindingPattern(el.name) || ts.isArrayBindingPattern(el.name)) out.push(...bindingPaths(el.name, [...prefix, key]));
    else out.push([...prefix, key]);
  }
  return out;
}

/** Whether `node` (a value at some path) is only tested for its keys: `k in node`, `Object.hasOwn(node, k)`,
 * `Object.prototype.hasOwnProperty.call(node, k)`.
 * @param {ts.Node} node */
function isKeyTest(node) {
  const p = node.parent;
  if (!p) return false;
  if (ts.isBinaryExpression(p) && p.operatorToken.kind === ts.SyntaxKind.InKeyword && p.right === node) return true;
  if (ts.isCallExpression(p) && p.arguments[0] === node) {
    const callee = p.expression.getText().replace(/\s+/g, '');
    return callee === 'Object.hasOwn' || callee === 'Object.prototype.hasOwnProperty.call';
  }
  return false;
}

/**
 * A style property or attribute name in CSS's kebab-case without a vendor prefix: `willChange`, `WebkitBackdropFilter`,
 * `-webkit-backdrop-filter` and `textDecorationLine` become `will-change`, `backdrop-filter`, `backdrop-filter` and
 * `text-decoration-line`; custom properties (`--x`) are kept.
 * @param {string} prop
 */
function cssName(prop) {
  const p = String(prop);
  if (p.startsWith('--')) return p;
  return p
    .replace(/^(webkit|moz|ms)(?=[A-Z])/, (m) => `-${m}`)
    .replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)
    .toLowerCase()
    .replace(/^-(webkit|moz|ms|o)-/, '');
}

/**
 * The rule a template breaks by writing style property (or SVG presentation attribute) `prop`, or null: outline and
 * text-decoration are emphasis (cues.js's, §4.2, §4.8); will-change, filter and backdrop-filter are template-style
 * (plan §P: a promoted layer keeps the raster of its first state, a filter re-rasterises every frame).
 * The name is read both ways it can reach the page: as a camelCase style key (`textDecoration`, `WebkitFilter`) and
 * case-insensitively, as the HTML parser, setAttribute on an HTML element, setProperty and the Typed OM read it (they
 * lower-case `FILTER`, `Text-Decoration`, `-WEBKIT-BACKDROP-FILTER`); either reading naming a banned property counts.
 * @param {string} prop
 * @returns {'emphasis' | 'template-style' | null}
 */
function templateStyleRule(prop) {
  for (const p of [cssName(prop), cssName(String(prop).toLowerCase())]) {
    if (/^(outline|text-decoration)(-|$)/.test(p)) return 'emphasis';
    if (p === 'will-change' || p === 'filter' || p === 'backdrop-filter') return 'template-style';
  }
  return null;
}

/**
 * Whether `node` hands on the context's TeX converter: `ctx.renderTex` / `ctx['renderTex']` (the object must be the
 * template's context, named `ctx` by convention) or a name `renderTex` (destructured from the context), through
 * parentheses. Another object's `.renderTex` (`opts.renderTex`, `this.renderTex`, `window.renderTex`) may be undefined
 * at run time and is refused. Anything else (undefined, null, false, a function
 * of the template's own, `opts.renderTex || f`) is not: richtext.js would fall back to its own uncounted MathJax cache
 * (`opts.renderTex || libRenderTex`) or convert without the page's cache.
 * @param {ts.Node} node
 */
function isContextConverter(node) {
  if (ts.isParenthesizedExpression(node)) return isContextConverter(node.expression);
  if (ts.isIdentifier(node)) return node.text === 'renderTex';
  return (ts.isPropertyAccessExpression(node) || ts.isElementAccessExpression(node)) && memberName(node) === 'renderTex'
    && ts.isIdentifier(node.expression) && node.expression.text === 'ctx';
}

/** Whether `node` is text the scan reads (a string literal, or a template whose literal parts it reads). */
const isReadableText = (node) => !!node && (ts.isStringLiteralLike(node) || ts.isTemplateExpression(node));

/** Whether an Identifier is a reference (not a declaration name, property name or object-literal key). */
function isReference(id) {
  const p = id.parent;
  if (!p) return true;
  if ((ts.isPropertyAccessExpression(p) || ts.isPropertyAssignment(p) || ts.isMethodDeclaration(p) || ts.isPropertyDeclaration(p)) && p.name === id) return false;
  if ((ts.isVariableDeclaration(p) || ts.isParameter(p) || ts.isFunctionDeclaration(p) || ts.isFunctionExpression(p) || ts.isClassDeclaration(p) || ts.isBindingElement(p)) && p.name === id) return false;
  if (ts.isBindingElement(p) && p.propertyName === id) return false;
  if (ts.isImportSpecifier(p) || ts.isExportSpecifier(p) || ts.isImportClause(p) || ts.isNamespaceImport(p)) return false;
  if (ts.isLabeledStatement(p) || ts.isBreakOrContinueStatement(p)) return false;
  if (ts.isGetAccessor(p) || ts.isSetAccessor(p)) return false;
  return true;
}

/** The nearest enclosing statement (or the node itself). */
function enclosingStatement(node) {
  let n = node;
  while (n.parent && !ts.isSourceFile(n.parent) && !ts.isBlock(n.parent) && !ts.isCaseClause(n.parent) && !ts.isDefaultClause(n.parent)) n = n.parent;
  return n;
}

/**
 * CSS findings in a stylesheet or a CSS fragment (comments blanked; line numbers kept).
 * @param {string} text @param {string} file @param {number} [baseLine]
 * @param {{emphasis?: boolean, templateStyle?: boolean}} [options]   template text: emphasis and template-style too
 * @returns {Finding[]}
 */
export function scanCss(text, file, baseLine = 1, { emphasis = false, templateStyle = false } = {}) {
  const css = text.replace(/\/\*[\s\S]*?\*\//g, (c) => c.replace(/[^\n]/g, ' '));
  /** @type {Finding[]} */
  const out = [];
  const lineAt = (i) => baseLine + (css.slice(0, i).match(/\n/g) || []).length;
  for (const m of css.matchAll(new RegExp(CSS_KEYFRAMES, 'gi'))) out.push({ file, line: lineAt(m.index ?? 0), rule: 'css-motion', detail: m[0] });
  for (const m of css.matchAll(new RegExp(CSS_MOTION_DECL))) {
    const value = m[2].trim();
    if (!CSS_MOTION_ALLOWED_VALUE.test(value)) out.push({ file, line: lineAt(m.index ?? 0), rule: 'css-motion', detail: `${m[1]}: ${value}` });
  }
  for (const m of css.matchAll(new RegExp(CSS_SMOOTH_SCROLL, 'gi'))) out.push({ file, line: lineAt(m.index ?? 0), rule: 'css-motion', detail: m[0] });
  if (emphasis) {
    for (const m of css.matchAll(new RegExp(CSS_EMPHASIS_DECL))) {
      const value = m[2].trim();
      if (!/^(none|0)(\s*!important)?$/i.test(value)) out.push({ file, line: lineAt(m.index ?? 0), rule: 'emphasis', detail: `${m[1]}: ${value}` });
    }
  }
  if (templateStyle) {
    for (const m of css.matchAll(new RegExp(CSS_LAYER_DECL))) {
      const value = m[2].trim();
      if (!CSS_LAYER_ALLOWED_VALUE.test(value)) out.push({ file, line: lineAt(m.index ?? 0), rule: 'template-style', detail: `${m[1]}: ${value}` });
    }
  }
  return out;
}

/**
 * dynamic.css declarations of the properties templates may not write (will-change, filter, backdrop-filter, outline,
 * text-decoration; templates set its classes, so a class carrying one is another path): findings for each one outside
 * CSS_STYLE_ALLOWED. Rules are read as `selectors { declarations }` (the stylesheet has no at-rules; one with a nested
 * block is read rule by rule).
 * @param {string} text @param {string} file
 * @returns {Finding[]}
 */
export function scanStyleCss(text, file) {
  const css = text.replace(/\/\*[\s\S]*?\*\//g, (c) => c.replace(/[^\n]/g, ' '));
  /** @type {Finding[]} */
  const out = [];
  for (const m of css.matchAll(/([^{}]*)\{([^{}]*)\}/g)) {
    const selector = m[1].trim().replace(/\s+/g, ' ');
    const body = (m.index ?? 0) + m[1].length + 1; // where the declarations start, after the '{'
    for (const d of m[2].matchAll(/(?:^|;)\s*([-a-zA-Z]+)\s*:\s*([^;]*)/g)) {
      const prop = d[1].toLowerCase();
      const rule = templateStyleRule(prop);
      if (!rule || CSS_STYLE_ALLOWED.get(selector) === cssName(prop)) continue;
      const line = (css.slice(0, body + (d.index ?? 0) + d[0].indexOf(d[1])).match(/\n/g) || []).length + 1;
      out.push({ file, line, rule, detail: `${selector} { ${prop}: ${d[2].trim()} }` });
    }
  }
  return out;
}

/**
 * Whether a stylesheet switches CSS motion off for every element and pseudo-element
 * (`*, *::before, *::after { transition: none !important; animation: none !important; }`).
 * @param {string} text
 */
export function hasMotionKillSwitch(text) {
  const css = text.replace(/\/\*[\s\S]*?\*\//g, ' ');
  for (const m of css.matchAll(/([^{}]*)\{([^{}]*)\}/g)) {
    const selectors = m[1].split(',').map((s) => s.trim());
    const body = m[2];
    if (
      selectors.includes('*') &&
      selectors.some((s) => /^\*::?before$/.test(s)) &&
      selectors.some((s) => /^\*::?after$/.test(s)) &&
      /(^|[;\s])transition\s*:\s*none\s*!important/i.test(body) &&
      /(^|[;\s])animation\s*:\s*none\s*!important/i.test(body)
    ) {
      return true;
    }
  }
  return false;
}

/**
 * Scan one JavaScript module.
 * @param {string} text
 * @param {string} file  repo-relative path with forward slashes (decides the motion / template rules)
 * @returns {Finding[]}
 */
export function scanJs(text, file) {
  const sf = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
  const motion = isMotion(file);
  const template = isTemplate(file);
  /** @type {Finding[]} */
  const found = [];
  /** @param {string} rule @param {ts.Node} node @param {string} [detail] */
  const add = (rule, node, detail) => {
    const line = sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1;
    found.push({ file, line, rule, detail: detail ?? node.getText(sf).slice(0, 80) });
  };

  let chartConstructed = /** @type {ts.Node | null} */ (null);
  let chartAnimationOff = false;
  /** Structure-role references (templates): the literal nodes and the names they are stored under. */
  const structRefs = [];
  const structAliases = new Set();

  /** @param {string} spec @param {ts.Node} node */
  const checkModule = (spec, node) => {
    if (isSelfClockedModule(spec)) add('self-clocked', node, spec);
  };

  /** CSS text inside a JS string or template. @param {string} value @param {ts.Node} node */
  const checkCssText = (value, node) => {
    for (const f of scanCss(value, file, 1, { emphasis: template, templateStyle: template })) add(f.rule, node, f.detail);
    if (/<video[\s>/]/i.test(value)) add('video', node, '<video>');
  };

  /**
   * A template writing style property (or SVG presentation attribute) `prop` through any path: the rule it breaks.
   * @param {string} prop @param {ts.Node} node @param {string} detail
   */
  const checkTemplateStyle = (prop, node, detail) => {
    const rule = templateStyleRule(prop);
    if (rule) add(rule, node, detail);
  };

  /**
   * A template's attribute write (setAttribute, the Painter's attr, an h() / svg() attribute object): the presentation
   * attributes filter and text-decoration, and the style attribute, whose value must be text the scan reads.
   * @param {ts.Node | undefined} name @param {ts.Node | undefined} value @param {ts.Node} node @param {string} via
   */
  const checkTemplateAttr = (name, value, node, via) => {
    if (!name || !ts.isStringLiteralLike(name)) {
      add('template-style', node, `${via}: an attribute name the scan cannot read`);
      return;
    }
    if (name.text.toLowerCase() === 'style') {
      if (value && !isReadableText(value)) add('template-style', node, `${via}: a style attribute the scan cannot read`);
      return;
    }
    checkTemplateStyle(name.text, node, `${via}('${name.text}')`);
  };

  /**
   * The properties of a style object a template writes (Object.assign onto a style, an h() / svg() `style` object).
   * @param {ts.ObjectLiteralExpression} obj @param {string} via
   */
  const checkStyleObject = (obj, via) => {
    for (const prop of obj.properties) {
      const key = prop.name && (ts.isIdentifier(prop.name) || ts.isStringLiteralLike(prop.name)) ? prop.name.text : null;
      if (key === null) add('template-style', prop, `${via}: a style property the scan cannot read`);
      else checkTemplateStyle(key, prop, `${via} { ${key} }`);
    }
  };

  /** @param {ts.Node} node */
  function visit(node) {
    // --- member access: clocks, randomness, style properties ---------------------------------------
    const member = memberName(node);
    if (member !== null && (ts.isPropertyAccessExpression(node) || ts.isElementAccessExpression(node))) {
      const obj = node.expression;
      if (member === 'now' && (isGlobalRef(obj, 'Date') || isGlobalRef(obj, 'performance'))) add('clock', node);
      if (member === 'timeline' && isGlobalRef(obj, 'document')) add('clock', node);
      if (member === 'random' && isGlobalRef(obj, 'Math')) add('random', node);
      if ((member === 'getRandomValues' || member === 'randomUUID') && isGlobalRef(obj, 'crypto')) add('random', node);
      if (/^(webkit|moz)?(transition|animation)/i.test(member) && endsWithName(obj, 'style')) {
        // Writing one is banned, except a reset (`el.style.transition = 'none'`); reading one is harmless.
        const p = node.parent;
        if (p && ts.isBinaryExpression(p) && p.left === node && p.operatorToken.kind >= ts.SyntaxKind.FirstAssignment && p.operatorToken.kind <= ts.SyntaxKind.LastAssignment) {
          if (!(ts.isStringLiteralLike(p.right) && CSS_MOTION_ALLOWED_VALUE.test(p.right.text.trim()))) add('css-motion', node);
        }
      }
      if (template && endsWithName(obj, 'style')) {
        checkTemplateStyle(member, node, node.getText(sf).slice(0, 80));
        // cssText of text the scan cannot read (literal text is scanned as CSS where it is written)
        const p = node.parent;
        if (member === 'cssText' && p && ts.isBinaryExpression(p) && p.left === node && !isReadableText(p.right)) {
          add('template-style', node, 'style.cssText: text the scan cannot read');
        }
      }
      if (template && member === 'timeline' && !isGlobalRef(obj, 'document')) add('timeline', node);
      // markup written as a property: its text must be text the scan reads (its literal parts are scanned as strings)
      if (template && MARKUP_PROPS.has(member)) {
        const p = node.parent;
        if (p && ts.isBinaryExpression(p) && p.left === node && p.operatorToken.kind >= ts.SyntaxKind.FirstAssignment && p.operatorToken.kind <= ts.SyntaxKind.LastAssignment && !isReadableText(p.right)) {
          add('template-style', node, `${member}: markup the scan cannot read`);
        }
      }
      // the CSS Typed OM: el.attributeStyleMap.set / append (prop, value) are the writes the scan follows
      if (template && member === 'attributeStyleMap') {
        const access = node.parent;
        const method = access && (ts.isPropertyAccessExpression(access) || ts.isElementAccessExpression(access)) && access.expression === node ? memberName(access) : null;
        const call = access && access.parent;
        const called = method !== null && !!call && ts.isCallExpression(call) && call.expression === access;
        if (called && (method === 'set' || method === 'append')) {
          const prop = /** @type {ts.CallExpression} */ (call).arguments[0];
          if (prop && ts.isStringLiteralLike(prop)) checkTemplateStyle(prop.text, call, `attributeStyleMap.${method}('${prop.text}')`);
          else add('template-style', call, `attributeStyleMap.${method}: a property name the scan cannot read`);
        } else if (!(called && ['get', 'getAll', 'has', 'delete', 'clear'].includes(String(method)))) {
          add('template-style', node, 'attributeStyleMap: a style write the scan cannot follow');
        }
      }
      if (member === 'animation' && ts.isPropertyAccessExpression(obj) && obj.name.text === 'defaults' && endsWithName(obj.expression, 'Chart')) {
        const p = node.parent;
        if (p && ts.isBinaryExpression(p) && p.left === node) {
          if (p.right.kind === ts.SyntaxKind.FalseKeyword) chartAnimationOff = true;
          else add('chart-animation', node);
        }
      }
    }

    if (template && ts.isElementAccessExpression(node) && memberName(node) === null && endsWithName(node.expression, 'style')) {
      add('template-style', node, 'style[...]: a style property the scan cannot read');
    }

    // --- the catalogue: every read of CATALOGUE is one plan §1.1 allows (geometry), wherever it is written ------
    if (ts.isIdentifier(node) && node.text === 'CATALOGUE' && isReference(node)) {
      const { path, top } = cataloguePath(node);
      const decl = top.parent;
      const paths = decl && ts.isVariableDeclaration(decl) && decl.initializer === top && !ts.isIdentifier(decl.name)
        ? bindingPaths(decl.name, path)
        : [path];
      const keysOnly = paths.length === 1 && paths[0] === path && isKeyTest(top);
      for (const q of paths) {
        if (!catalogueReadAllowed(q, keysOnly)) add('catalogue', node, `CATALOGUE${q.map((k) => (k === '*' ? '[...]' : `.${k}`)).join('')}`);
      }
    }
    // `export { CATALOGUE }` (any file): another module could read all of it
    if (ts.isExportSpecifier(node) && (node.propertyName || node.name).text === 'CATALOGUE') add('catalogue', node, 'export { CATALOGUE }');

    // --- destructuring Date / performance / Math; a template destructuring the timeline -----------------------
    if (ts.isVariableDeclaration(node) && node.initializer && ts.isObjectBindingPattern(node.name)) {
      for (const el of node.name.elements) {
        const key = (el.propertyName && ts.isIdentifier(el.propertyName) && el.propertyName.text) || (ts.isIdentifier(el.name) && el.name.text);
        if (key === 'now' && (isGlobalRef(node.initializer, 'Date') || isGlobalRef(node.initializer, 'performance'))) add('clock', el);
        if (key === 'random' && isGlobalRef(node.initializer, 'Math')) add('random', el);
      }
    }
    // `const { timeline } = ctx`, a parameter `{ timeline }`, a nested pattern: any binding of `timeline`
    if (template && ts.isObjectBindingPattern(node)) {
      for (const el of node.elements) {
        const named = el.propertyName && (ts.isIdentifier(el.propertyName) || ts.isStringLiteralLike(el.propertyName)) ? el.propertyName.text : null;
        const key = named || (!el.propertyName && ts.isIdentifier(el.name) ? el.name.text : null);
        if (key === 'timeline') add('timeline', el);
      }
    }

    // --- calls ---------------------------------------------------------------------------------------
    if (ts.isCallExpression(node)) {
      const callee = node.expression;
      const name = ts.isIdentifier(callee) ? callee.text : memberName(callee);
      // Any receiver counts (window.setTimeout, an injected win.setTimeout, ...).
      if (name && TIMERS.has(name)) add('timer', node, `${name}()`);
      if (ts.isIdentifier(callee) && callee.text === 'Date') add('clock', node, 'Date()');
      if (callee.kind === ts.SyntaxKind.ImportKeyword && node.arguments[0] && ts.isStringLiteralLike(node.arguments[0])) checkModule(node.arguments[0].text, node);
      if (callee.kind === ts.SyntaxKind.ImportKeyword) {
        // a dynamic import of the catalogue (its one reader imports `{ CATALOGUE }` statically, the name the scan
        // follows), or of a module the scan cannot name (it might be the catalogue)
        const spec = node.arguments[0] && ts.isStringLiteralLike(node.arguments[0]) ? node.arguments[0].text : null;
        if (spec === null || isCatalogueModule(spec)) add('catalogue', node, spec === null ? 'import(<computed>)' : spec);
      }
      const arg0 = node.arguments[0];
      const str0 = arg0 && ts.isStringLiteralLike(arg0) ? arg0.text : null;
      if (name === 'setProperty' && str0 !== null) {
        const arg1 = node.arguments[1];
        const reset = arg1 && ts.isStringLiteralLike(arg1) && CSS_MOTION_ALLOWED_VALUE.test(arg1.text.trim());
        if (/^(-webkit-|-moz-)?(transition|animation)/i.test(str0) && !reset) add('css-motion', node, `setProperty('${str0}')`);
        if (template) checkTemplateStyle(str0, node, `setProperty('${str0}')`);
      }
      if (template && name === 'setProperty' && str0 === null) add('template-style', node, 'setProperty: a property name the scan cannot read');
      // the Painter (core.js): paint.style(el, prop, value) and paint.attr(el, name, value), every per-frame write
      if (template && !ts.isIdentifier(callee) && (name === 'style' || name === 'attr') && node.arguments.length >= 2) {
        const prop = node.arguments[1];
        if (name === 'attr') checkTemplateAttr(prop, node.arguments[2], node, 'paint.attr');
        else if (!ts.isStringLiteralLike(prop)) add('template-style', node, 'paint.style: a property name the scan cannot read');
        else checkTemplateStyle(prop.text, node, `paint.style(el, '${prop.text}')`);
      }
      if (template && name === 'setAttribute') checkTemplateAttr(arg0, node.arguments[1], node, 'setAttribute');
      if (template && name === 'setAttributeNS') checkTemplateAttr(node.arguments[1], node.arguments[2], node, 'setAttributeNS');
      if (template && name === 'toggleAttribute') checkTemplateAttr(arg0, undefined, node, 'toggleAttribute');
      if (template && (name === 'setAttributeNode' || name === 'setAttributeNodeNS')) add('template-style', node, `${name}: an attribute the scan cannot read`);
      if (template && name && !ts.isIdentifier(callee) && MARKUP_CALLS.has(name)) {
        const markup = node.arguments[/** @type {number} */ (MARKUP_CALLS.get(name))];
        if (!isReadableText(markup)) add('template-style', node, `${name}: markup the scan cannot read`);
      }
      // TeX goes through the context's converter, which the render page counts (texConversions) and never evicts
      if (template && name && TEX_HELPERS.has(name)) {
        const { index, options } = /** @type {{index: number, options: boolean}} */ (TEX_HELPERS.get(name));
        const given = node.arguments[index];
        let passes = false;
        if (given && !options) passes = isContextConverter(given);
        if (given && options && ts.isObjectLiteralExpression(given)) {
          // the option the helper reads is the last one that can set `renderTex` (a later spread, computed key, method or
          // duplicate overrides an earlier one), and it must hand on the context's converter
          const setters = given.properties.filter((p) => {
            if (ts.isSpreadAssignment(p)) return true;
            const key = p.name && (ts.isIdentifier(p.name) || ts.isStringLiteralLike(p.name) || ts.isNumericLiteral(p.name)) ? p.name.text : null;
            return key === null || key === 'renderTex';
          });
          const last = setters[setters.length - 1];
          passes = !!last && ((ts.isShorthandPropertyAssignment(last) && last.name.text === 'renderTex')
            || (ts.isPropertyAssignment(last) && isContextConverter(last.initializer)));
        }
        if (!passes) add('tex', node, `${name}() without ctx.renderTex`);
      }
      // the element helpers of web/js/shared/dom.js: h(tag, attrs, ...children) and svg(tag, attrs, ...children)
      if (template && ts.isIdentifier(callee) && (callee.text === 'h' || callee.text === 'svg') && node.arguments[1]) {
        const attrs = node.arguments[1];
        if (ts.isObjectLiteralExpression(attrs)) {
          for (const prop of attrs.properties) {
            const key = prop.name && (ts.isIdentifier(prop.name) || ts.isStringLiteralLike(prop.name)) ? prop.name.text : null;
            if (key === null) {
              add('template-style', prop, `${callee.text}(): attributes the scan cannot read`);
              continue;
            }
            const value = ts.isPropertyAssignment(prop) ? prop.initializer : null;
            if (key === 'style') {
              if (value && ts.isObjectLiteralExpression(value)) checkStyleObject(value, `${callee.text}() style`);
              else if (!value || !isReadableText(value)) add('template-style', prop, `${callee.text}(): a style the scan cannot read`);
            } else if (key !== 'class' && key !== 'dataset') {
              checkTemplateStyle(key, prop, `${callee.text}() { ${key} }`);
            }
          }
        } else if (!(attrs.kind === ts.SyntaxKind.NullKeyword || (ts.isIdentifier(attrs) && attrs.text === 'undefined'))) {
          add('template-style', attrs, `${callee.text}(): attributes the scan cannot read`);
        }
      }
      // Object.assign(el.style, { transition: '...' })
      if (name === 'assign' && ts.isPropertyAccessExpression(callee) && isGlobalRef(callee.expression, 'Object') && arg0 && endsWithName(arg0, 'style')) {
        for (const src of node.arguments.slice(1)) {
          if (!ts.isObjectLiteralExpression(src)) {
            if (template) add('template-style', src, 'Object.assign(style, ...): properties the scan cannot read');
            continue;
          }
          for (const prop of src.properties) {
            const key = prop.name && (ts.isIdentifier(prop.name) || ts.isStringLiteralLike(prop.name)) ? prop.name.text : '';
            const value = ts.isPropertyAssignment(prop) && ts.isStringLiteralLike(prop.initializer) ? prop.initializer.text.trim() : null;
            if (/^(-?webkit-?|-?moz-?)?(transition|animation)/i.test(key) && !(value !== null && CSS_MOTION_ALLOWED_VALUE.test(value))) add('css-motion', prop);
          }
          if (template) checkStyleObject(src, 'Object.assign(style)');
        }
      }
      if (name === 'animate' && !ts.isIdentifier(callee) && arg0 && (ts.isArrayLiteralExpression(arg0) || ts.isObjectLiteralExpression(arg0))) add('waapi', node, '.animate(keyframes, ...)');
      if ((name === 'createElement' || name === 'createElementNS') && node.arguments.some((a) => ts.isStringLiteralLike(a) && a.text.toLowerCase() === 'video')) add('video', node, "createElement('video')");
      // The element helper of web/js/shared/dom.js: h('video', attrs, ...children).
      if (ts.isIdentifier(callee) && callee.text === 'h' && str0 !== null && str0.toLowerCase() === 'video') add('video', node, "h('video')");
      if (template && name === 'color' && (str0 === 'mark' || str0 === 'focus')) add('emphasis', node, `color('${str0}')`);
    }

    // --- constructors --------------------------------------------------------------------------------
    if (ts.isNewExpression(node)) {
      const ctor = node.expression;
      if (isGlobalRef(ctor, 'Date') && (!node.arguments || node.arguments.length === 0)) add('clock', node, 'new Date()');
      if (endsWithName(ctor, 'Animation') || endsWithName(ctor, 'KeyframeEffect')) add('waapi', node);
      if (endsWithName(ctor, 'Chart')) chartConstructed = chartConstructed || node;
    }

    // --- identifiers: rAF, banned globals, DOM in motion ---------------------------------------------
    if (ts.isIdentifier(node) && isReference(node)) {
      const id = node.text;
      if (id === 'requestAnimationFrame') add('raf', node, 'requestAnimationFrame');
      if (SELF_CLOCKED_GLOBALS.has(id)) add('self-clocked', node, id);
      if (motion && DOM_GLOBALS.has(id) && id !== 'requestAnimationFrame') add('dom', node, id);
    }
    // Through any window object (window., globalThis., an injected `win` or `ctx.win`).
    if (ts.isPropertyAccessExpression(node) && node.name.text === 'requestAnimationFrame') {
      add('raf', node, 'requestAnimationFrame');
    }

    // --- TeX in templates: the helpers are only ever called by their own names (the tex rule checks each call), so
    // a helper handed on as a value (`const r = renderRich`, `{ renderRich }`, `rt.renderRich.call(...)`) or bound
    // under another name (`const { renderRich: rr } = mod`) is refused, and so are MathJax itself and its loaders --
    if (template) {
      const helper = ts.isIdentifier(node) && isReference(node) ? node.text
        : ts.isPropertyAccessExpression(node) || ts.isElementAccessExpression(node) ? memberName(node) : null;
      if (helper !== null && TEX_HELPERS.has(helper) && !(node.parent && ts.isCallExpression(node.parent) && node.parent.expression === node)) {
        add('tex', node, `${helper} used as a value (the tex rule follows calls by the helper's name only)`);
      }
      if (helper === 'MathJax') add('tex', node, 'MathJax (convert TeX through ctx.renderTex)');
      if (ts.isBindingElement(node) && node.propertyName && (ts.isIdentifier(node.propertyName) || ts.isStringLiteralLike(node.propertyName))
        && TEX_HELPERS.has(node.propertyName.text) && !(ts.isIdentifier(node.name) && node.name.text === node.propertyName.text)) {
        add('tex', node, `${node.propertyName.text} bound as ${node.name.getText(sf)}`);
      }
      if (ts.isCallExpression(node) && node.expression.kind === ts.SyntaxKind.ImportKeyword && node.arguments[0] && ts.isStringLiteralLike(node.arguments[0])
        && TEX_MODULES.test(node.arguments[0].text.split(/[?#]/)[0])) {
        add('tex', node, `import('${node.arguments[0].text}') (import the TeX helpers statically, by name)`);
      }
    }

    // --- modules -------------------------------------------------------------------------------------
    // a TeX helper re-exported under another name (with or without a module): another template's calls of the new
    // name would escape the tex rule
    if (template && ts.isExportDeclaration(node) && node.exportClause && ts.isNamedExports(node.exportClause)) {
      for (const el of node.exportClause.elements) {
        const local = (el.propertyName || el.name).text;
        if (TEX_HELPERS.has(local) && el.name.text !== local) add('tex', el, `${local} re-exported as ${el.name.text}`);
      }
    }
    if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) && node.moduleSpecifier && ts.isStringLiteralLike(node.moduleSpecifier)) {
      checkModule(node.moduleSpecifier.text, node);
      // a TeX helper imported under another name (or through a namespace) would hide its calls from the tex rule, and
      // shared/libs.js's own converter and MathJax loader convert without the render page's count
      if (template && ts.isImportDeclaration(node) && node.importClause && node.importClause.namedBindings) {
        const nb = node.importClause.namedBindings;
        const spec = node.moduleSpecifier.text.split(/[?#]/)[0];
        if (ts.isNamespaceImport(nb) && TEX_MODULES.test(spec)) add('tex', node, `import * from ${spec} (import its helpers by name)`);
        if (ts.isNamedImports(nb)) {
          for (const el of nb.elements) {
            const imported = (el.propertyName || el.name).text;
            if (el.propertyName && TEX_HELPERS.has(imported) && el.name.text !== imported) add('tex', el, `${imported} imported as ${el.name.text}`);
            if (LIBS_MODULE.test(spec) && LIBS_TEX.has(imported)) add('tex', el, `${imported} from ${spec} (convert TeX through ctx.renderTex)`);
          }
        }
      }
      if (isCatalogueModule(node.moduleSpecifier.text)) {
        if (!CATALOGUE_READERS.has(file)) add('catalogue', node, node.moduleSpecifier.text);
        else if (!isPlainCatalogueImport(node)) add('catalogue', node, 'import the catalogue as `{ CATALOGUE }` (the name the scan follows)');
      }
    }

    // --- object literals: Chart.js options -----------------------------------------------------------
    if (ts.isPropertyAssignment(node)) {
      const key = ts.isIdentifier(node.name) || ts.isStringLiteralLike(node.name) ? node.name.text : null;
      if (key === 'animation' && node.initializer.kind === ts.SyntaxKind.FalseKeyword) chartAnimationOff = true;
      const owner = node.parent && node.parent.parent;
      const inOptions = owner && ts.isPropertyAssignment(owner) && (ts.isIdentifier(owner.name) || ts.isStringLiteralLike(owner.name)) && owner.name.text === 'options';
      if (inOptions && (key === 'animation' || key === 'animations' || key === 'transitions') && node.initializer.kind !== ts.SyntaxKind.FalseKeyword) {
        add('chart-animation', node, `options.${key}`);
      }
    }

    // --- markup in a template's strings: a tag attribute it may not write (a template literal is read whole, its
    // substitutions as placeholders, so a tag split by ${...} is still one tag) -------------------------------
    if (template && (ts.isStringLiteralLike(node) || ts.isTemplateExpression(node))) {
      const text = ts.isTemplateExpression(node) ? node.head.text + node.templateSpans.map((sp) => `0${sp.literal.text}`).join('') : node.text;
      for (const m of text.matchAll(new RegExp(MARKUP_ATTR))) checkTemplateStyle(m[1], node, `markup attribute ${m[1]}=`);
    }

    // --- strings: CSS text, <video>, structure roles ---------------------------------------------------
    if (ts.isStringLiteralLike(node) || ts.isTemplateHead(node) || ts.isTemplateMiddle(node) || ts.isTemplateTail(node)) {
      const value = node.text;
      const isModuleSpec = node.parent && (ts.isImportDeclaration(node.parent) || ts.isExportDeclaration(node.parent));
      if (!isModuleSpec) checkCssText(value, node);
      if (template && /^struct\.|--struct-/.test(value)) structRefs.push(node);
    }

    ts.forEachChild(node, visit);
  }
  visit(sf);

  if (chartConstructed && !chartAnimationOff) add('chart-animation', chartConstructed, 'new Chart(...) without animation: false');

  // --- structure roles in templates: the statement that uses one must not name a content element ----
  if (template) {
    const statementWords = (stmt, exclude) => {
      const out = [];
      const collect = (n) => {
        if (n !== exclude && ts.isIdentifier(n)) out.push(...words(n.text));
        else if (n !== exclude && ts.isStringLiteralLike(n) && !/^struct\./.test(n.text)) out.push(...words(n.text));
        ts.forEachChild(n, collect);
      };
      collect(stmt);
      return out;
    };
    const judge = (stmt, node, exclude) => {
      const w = statementWords(stmt, exclude);
      if (w.some((x) => CONTENT_NOUNS.has(x)) && !w.some((x) => STRUCTURE_NOUNS.has(x))) add('struct-role', node);
    };
    for (const ref of structRefs) {
      const stmt = enclosingStatement(ref);
      // `const accent = roles.color('struct.accent')`: follow the name to where it is used.
      let decl = ref.parent;
      while (decl && !ts.isVariableDeclaration(decl) && decl !== stmt) decl = decl.parent;
      if (decl && ts.isVariableDeclaration(decl) && ts.isIdentifier(decl.name)) {
        structAliases.add(decl.name.text);
        judge(stmt, ref, decl.name);
      } else {
        judge(stmt, ref, null);
      }
    }
    if (structAliases.size) {
      const visitAlias = (n) => {
        if (ts.isIdentifier(n) && structAliases.has(n.text) && isReference(n)) judge(enclosingStatement(n), n, n);
        ts.forEachChild(n, visitAlias);
      };
      visitAlias(sf);
    }
  }

  return applyPragmas(found, text, file);
}

/**
 * Drop findings waived by a reasoned pragma (only for PRAGMA_RULES, never in motion or templates).
 * @param {Finding[]} found @param {string} text @param {string} file
 */
function applyPragmas(found, text, file) {
  if (isMotion(file) || isTemplate(file)) return dedupe(found);
  const lines = text.split(/\r?\n/);
  const waived = (f) => {
    if (!PRAGMA_RULES.has(f.rule)) return false;
    for (const l of [lines[f.line - 1], lines[f.line - 2]]) {
      const m = l && PRAGMA_RE.exec(l);
      if (m && m[1] === f.rule) return true;
    }
    return false;
  };
  return dedupe(found.filter((f) => !waived(f)));
}

/** One finding per (line, rule, detail). @param {Finding[]} found */
function dedupe(found) {
  const seen = new Set();
  return found.filter((f) => {
    const key = `${f.file}:${f.line}:${f.rule}:${f.detail}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

/**
 * Findings for the generated catalogue being anything but data (plan §1.1; rule `catalogue-data`): besides comments it
 * holds the `freezeInside` helper, which calls nothing but Object.values, Object.freeze and itself and assigns nothing,
 * and one `export const CATALOGUE = Object.freeze(freezeInside({...}))` whose literal holds only strings, numbers (a
 * leading minus allowed), booleans, null, arrays and objects with plain keys: no import, no other statement, no call,
 * reference, function, getter, spread, computed key or template inside the data. The generated face table (B2b,
 * `faces.gen.js`, `export const FACES`) is held to the same rules under its own rule id (`faces-data`).
 * @param {string} text @param {string} [file] @param {string} [name]  the exported constant
 * @param {'catalogue-data' | 'faces-data'} [rule]
 * @returns {Finding[]}
 */
export function scanCatalogue(text, file = CATALOGUE_FILE, name = 'CATALOGUE', rule = 'catalogue-data') {
  const sf = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
  /** @type {Finding[]} */
  const found = [];
  /** @param {ts.Node} node @param {string} detail */
  const add = (node, detail) => {
    const line = sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1;
    found.push({ file, line, rule, detail });
  };
  /** @param {ts.Node} n */
  const data = (n) => {
    if (ts.isStringLiteral(n) || ts.isNumericLiteral(n)) return;
    if (n.kind === ts.SyntaxKind.TrueKeyword || n.kind === ts.SyntaxKind.FalseKeyword || n.kind === ts.SyntaxKind.NullKeyword) return;
    if (ts.isPrefixUnaryExpression(n) && n.operator === ts.SyntaxKind.MinusToken && ts.isNumericLiteral(n.operand)) return;
    if (ts.isArrayLiteralExpression(n)) {
      for (const e of n.elements) data(e);
      return;
    }
    if (ts.isObjectLiteralExpression(n)) {
      for (const prop of n.properties) {
        const plainKey = prop.name && (ts.isStringLiteral(prop.name) || ts.isIdentifier(prop.name) || ts.isNumericLiteral(prop.name));
        if (ts.isPropertyAssignment(prop) && plainKey) data(prop.initializer);
        else add(prop, `not data: ${prop.getText(sf).slice(0, 60)}`);
      }
      return;
    }
    add(n, `not data: ${n.getText(sf).slice(0, 60)}`);
  };
  /** @param {ts.FunctionDeclaration} fn */
  const helper = (fn) => {
    /** @param {ts.Node} n */
    const walk = (n) => {
      if (ts.isCallExpression(n)) {
        const c = n.expression;
        const allowed = (ts.isIdentifier(c) && c.text === 'freezeInside')
          || (ts.isPropertyAccessExpression(c) && ts.isIdentifier(c.expression) && c.expression.text === 'Object' && ['values', 'freeze'].includes(c.name.text));
        if (!allowed) add(n, `the helper calls ${c.getText(sf).slice(0, 40)}`);
      }
      if (ts.isNewExpression(n) || ts.isAwaitExpression(n) || ts.isYieldExpression(n)) add(n, 'the helper does more than freeze');
      if (ts.isBinaryExpression(n) && n.operatorToken.kind >= ts.SyntaxKind.FirstAssignment && n.operatorToken.kind <= ts.SyntaxKind.LastAssignment) {
        add(n, 'the helper assigns');
      }
      ts.forEachChild(n, walk);
    };
    if (fn.body) walk(fn.body);
  };
  let helpers = 0;
  let exports = 0;
  for (const st of sf.statements) {
    if (ts.isFunctionDeclaration(st) && st.name && st.name.text === 'freezeInside' && !st.modifiers) {
      helpers += 1;
      helper(st);
      continue;
    }
    const exported = ts.isVariableStatement(st) && (st.modifiers || []).some((m) => m.kind === ts.SyntaxKind.ExportKeyword);
    const decl = exported && st.declarationList.declarations.length === 1 ? st.declarationList.declarations[0] : null;
    const init = decl && (st.declarationList.flags & ts.NodeFlags.Const) && ts.isIdentifier(decl.name) && decl.name.text === name ? decl.initializer : null;
    const outer = init && ts.isCallExpression(init) && init.arguments.length === 1 && init.expression.getText(sf) === 'Object.freeze' ? init.arguments[0] : null;
    const inner = outer && ts.isCallExpression(outer) && outer.arguments.length === 1 && outer.expression.getText(sf) === 'freezeInside' ? outer.arguments[0] : null;
    if (inner && ts.isObjectLiteralExpression(inner)) {
      exports += 1;
      data(inner);
      continue;
    }
    add(st, `not the catalogue's data: ${st.getText(sf).slice(0, 60)}`);
  }
  if (helpers !== 1 || exports !== 1) found.push({ file, line: 1, rule, detail: `${exports} ${name} exports and ${helpers} freezeInside helpers (one of each)` });
  return found;
}

/**
 * Findings in an HTML page: inline <style> blocks and style attributes (CSS rules), inline scripts (JS
 * rules), <video> elements; and the local stylesheets it links.
 * @param {string} html @param {string} file
 * @returns {{findings: Finding[], sheets: string[], inlineCss: string}}
 */
export function scanHtml(html, file) {
  /** @type {Finding[]} */
  const findings = [];
  const lineAt = (i) => (html.slice(0, i).match(/\n/g) || []).length + 1;
  let inlineCss = '';
  for (const m of html.matchAll(/<style\b[^>]*>([\s\S]*?)<\/style>/gi)) {
    inlineCss += `${m[1]}\n`;
    findings.push(...scanCss(m[1], file, lineAt((m.index ?? 0) + m[0].indexOf(m[1]))));
  }
  for (const m of html.matchAll(/\sstyle\s*=\s*("([^"]*)"|'([^']*)')/gi)) findings.push(...scanCss(m[2] ?? m[3] ?? '', file, lineAt(m.index ?? 0)));
  for (const m of html.matchAll(/<script\b(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi)) {
    const base = lineAt(m.index ?? 0) - 1;
    for (const f of scanJs(m[1], file)) findings.push({ ...f, line: f.line + base });
  }
  for (const m of html.matchAll(/<script\b[^>]*\bsrc\s*=\s*["']([^"']+)["']/gi)) {
    if (isSelfClockedModule(m[1])) findings.push({ file, line: lineAt(m.index ?? 0), rule: 'self-clocked', detail: m[1] });
  }
  for (const m of html.matchAll(/<video\b/gi)) findings.push({ file, line: lineAt(m.index ?? 0), rule: 'video', detail: '<video>' });
  const sheets = [];
  for (const m of html.matchAll(/<link\b[^>]*>/gi)) {
    if (!/\brel\s*=\s*["']?stylesheet/i.test(m[0])) continue;
    const href = /\bhref\s*=\s*["']([^"']+)["']/i.exec(m[0]);
    if (href) sheets.push(href[1]);
  }
  return { findings, sheets, inlineCss };
}

// -------------------------------------------------------------------------------------------------
// The repository
// -------------------------------------------------------------------------------------------------

/** Repo-relative paths (forward slashes) of the JS modules under `dir`. */
function listJs(dir) {
  const abs = `${REPO}${dir}`;
  if (!existsSync(abs)) return [];
  /** @type {string[]} */
  const out = [];
  for (const entry of readdirSync(abs, { withFileTypes: true })) {
    const rel = `${dir}/${entry.name}`;
    if (entry.isDirectory()) out.push(...listJs(rel));
    else if (/\.(m?js)$/.test(entry.name)) out.push(rel);
  }
  return out.sort();
}

const read = (rel) => readFileSync(`${REPO}${rel}`, 'utf8');
/** @param {Finding[]} found */
const report = (found) =>
  found
    .map((f) => {
      const hint = PRAGMA_RULES.has(f.rule) && !isMotion(f.file) && !isTemplate(f.file) ? ` (if this is stage infrastructure or live-only code, say why: // determinism-allow ${f.rule}: <reason>)` : '';
      return `${f.file}:${f.line} [${f.rule}] ${f.detail} — ${RULES[f.rule] || ''}${hint}`;
    })
    .join('\n');

describe('determinism source scan: the repository', () => {
  test('web/js/dynamic/** and web/js/motion/** contain no banned construct', () => {
    const files = SCANNED_JS_DIRS.flatMap(listJs);
    assert.ok(files.filter(isMotion).length >= 8, `the scan sees the motion toolkit (${files.length} files)`);
    const found = files.flatMap((f) => scanJs(read(f), f));
    assert.equal(found.length, 0, `\n${report(found)}`);
  });

  test('web/js/dynamic/catalogue.gen.js is data only, and only core.js imports it (checked with the rest above)', () => {
    const found = scanCatalogue(read(CATALOGUE_FILE));
    assert.equal(found.length, 0, `\n${report(found)}`);
    const importers = SCANNED_JS_DIRS.flatMap(listJs).filter((f) => /catalogue\.gen\.js['"]/.test(read(f)));
    assert.deepEqual(importers, [...CATALOGUE_READERS], 'the catalogue has exactly one reader');
  });

  test('web/js/dynamic/faces.gen.js is data only, and only fonts.js imports it in the stage (B2b face table)', () => {
    const found = scanCatalogue(read(FACES_FILE), FACES_FILE, 'FACES', 'faces-data');
    assert.equal(found.length, 0, `\n${report(found)}`);
    const importers = SCANNED_JS_DIRS.flatMap(listJs).filter((f) => /faces\.gen\.js['"]/.test(read(f)));
    assert.deepEqual(importers, [...FACES_READERS], 'the face table has exactly one reader in the stage');
  });

  test('web/css/dynamic.css declares no will-change, filter, backdrop-filter, outline or text-decoration but its measured exception', () => {
    const found = SCANNED_CSS.flatMap((f) => scanStyleCss(read(f), f));
    assert.equal(found.length, 0, `\n${report(found)}`);
    const css = read('web/css/dynamic.css');
    for (const [selector, prop] of CSS_STYLE_ALLOWED) assert.match(css, new RegExp(`${selector.replace(/[.]/g, '\\.')} \\{[^}]*${prop}:`), 'the exception is still there');
  });

  test('web/css/dynamic.css has no CSS transition, animation or @keyframes', (t) => {
    const present = SCANNED_CSS.filter((f) => existsSync(`${REPO}${f}`));
    if (!present.length) {
      t.diagnostic('web/css/dynamic.css does not exist yet');
      return;
    }
    const found = present.flatMap((f) => scanCss(read(f), f));
    assert.equal(found.length, 0, `\n${report(found)}`);
  });

  test('the render page has no inline CSS motion, inline script hazard or <video>, and neutralises motion in linked stylesheets', (t) => {
    if (!existsSync(`${REPO}${RENDER_PAGE}`)) {
      t.diagnostic(`${RENDER_PAGE} does not exist yet`);
      return;
    }
    const { findings, sheets, inlineCss } = scanHtml(read(RENDER_PAGE), RENDER_PAGE);
    assert.equal(findings.length, 0, `\n${report(findings)}`);
    // Any other stylesheet the page links (the shared player or panel styles, say) may carry transitions for the
    // live UI; then the page must switch CSS motion off for every element, or class changes would animate on
    // the wall clock between captured frames.
    const local = sheets
      .map((href) => href.replace(/^\//, '').split(/[?#]/)[0])
      .filter((rel) => rel.startsWith('web/') && existsSync(`${REPO}${rel}`) && !SCANNED_CSS.includes(rel));
    const moving = local.filter((rel) => scanCss(read(rel), rel).length > 0);
    if (moving.length) {
      const own = [inlineCss, ...SCANNED_CSS.filter((f) => existsSync(`${REPO}${f}`)).map(read)].join('\n');
      assert.ok(
        hasMotionKillSwitch(own),
        `${RENDER_PAGE} links ${moving.join(', ')}, which animate; add to web/css/dynamic.css: ` +
          '*, *::before, *::after { transition: none !important; animation: none !important; }',
      );
    }
  });
});

// -------------------------------------------------------------------------------------------------
// The scanner itself: every rule fires on a bad snippet and stays quiet on the look-alikes
// -------------------------------------------------------------------------------------------------

const STAGE = 'web/js/dynamic/stage.js';
const TEMPLATE = 'web/js/dynamic/templates/board_generic.js';
const MOTION = 'web/js/motion/example.js';
/** @param {string} code @param {string} [file] */
const rules = (code, file = STAGE) => scanJs(code, file).map((f) => f.rule);

describe('scanner: clocks and randomness', () => {
  test('every way of reading the wall clock is caught', () => {
    for (const code of [
      'const t = Date.now();',
      "const t = Date['now']();",
      'const t = performance.now();',
      'const t = window.performance.now();',
      'const t = globalThis.performance.now();',
      'const { now } = performance;',
      'const { now: clock } = Date;',
      'const d = new Date();',
      'const d = new Date;',
      'const s = Date();',
      'const tl = document.timeline.currentTime;',
    ]) {
      assert.deepEqual(rules(code), ['clock'], code);
    }
  });

  test('explicit dates and look-alikes are fine', () => {
    for (const code of ['const d = new Date(0);', 'const p = Date.parse(s);', 'const n = schedule.now(t);', 'const x = state.performance.now;', '// Date.now() is banned here']) {
      assert.deepEqual(rules(code), [], code);
    }
  });

  test('unseeded randomness is caught; seeded generators are fine', () => {
    for (const code of ['const r = Math.random();', "const r = Math['random']();", 'const { random } = Math;', 'crypto.getRandomValues(buf);', 'const id = self.crypto.randomUUID();']) {
      assert.deepEqual(rules(code), ['random'], code);
    }
    assert.deepEqual(rules("import { seededRandom } from '../motion/random.js'; const r = seededRandom(1, 's', 'bg'); r();"), []);
    assert.deepEqual(rules('const v = rng.random();'), []);
  });
});

describe('scanner: loops and timers', () => {
  test('requestAnimationFrame and timers are caught, with or without window.', () => {
    assert.deepEqual(rules('requestAnimationFrame(loop);'), ['raf']);
    assert.deepEqual(rules('window.requestAnimationFrame(loop);'), ['raf']);
    assert.deepEqual(rules('ctx.win.requestAnimationFrame(loop);'), ['raf']);
    for (const code of ['setTimeout(f, 10);', 'window.setInterval(f, 10);', 'globalThis.setImmediate(f);', 'requestIdleCallback(f);', 'win.setTimeout(f, 0);']) {
      assert.deepEqual(rules(code), ['timer'], code);
    }
  });

  test('a reasoned pragma waives raf / timer / video in stage infrastructure only', () => {
    const raf = 'await new Promise((r) => requestAnimationFrame(r)); // determinism-allow raf: seek() waits one frame (§7.3 step 6)';
    assert.deepEqual(rules(raf), []);
    assert.deepEqual(rules(`// determinism-allow timer: font load timeout before ready\nsetTimeout(f, 5000);`), []);
    assert.deepEqual(rules('setTimeout(f, 5000); // determinism-allow timer:'), ['timer'], 'a reason is required');
    assert.deepEqual(rules('setTimeout(f, 5000); // determinism-allow raf: wrong rule named here'), ['timer']);
    assert.deepEqual(rules(raf, TEMPLATE), ['raf'], 'templates may not own a loop, pragma or not');
    assert.deepEqual(rules(raf, MOTION), ['raf'], 'the motion toolkit may not either');
    assert.deepEqual(rules('const t = Date.now(); // determinism-allow clock: no such waiver'), ['clock']);
  });

  test('a guard that replaces the timers is not a timer call', () => {
    assert.deepEqual(rules('const real = window.setTimeout; window.setTimeout = () => { throw new Error("timer after ready"); };'), []);
  });
});

describe('scanner: CSS motion, WAAPI, Chart.js and libraries', () => {
  test('CSS transitions and animations from JS are caught; resets are fine', () => {
    for (const code of [
      "el.style.transition = 'opacity 200ms';",
      "el.style.animationName = 'spin';",
      "el.style['transition'] = 'all 1s';",
      "el.style.setProperty('transition', 'opacity 1s');",
      "Object.assign(el.style, { opacity: '0', transitionDuration: '300ms' });",
      'el.style.animation = preset.css;',
      'el.style.cssText = "opacity: 0; transition: opacity 0.3s ease";',
      'const css = `@keyframes spin { to { transform: rotate(1turn) } }`;',
      'root.innerHTML = \'<div style="animation: pulse 2s infinite"></div>\';',
    ]) {
      assert.deepEqual(rules(code), ['css-motion'], code);
    }
    for (const code of [
      "el.style.transition = 'none';",
      "el.style.setProperty('animation', 'none');",
      "Object.assign(el.style, { transition: 'none', opacity: '1' });",
      "el.style.cssText = 'transition: none !important';",
      'const was = el.style.transition;',
      'const look = scene.look.transition_in;',
      "const kind = 'transition';",
    ]) {
      assert.ok(!rules(code).includes('css-motion'), code);
    }
  });

  test('WAAPI is caught; our own animate(t) helpers are not', () => {
    assert.deepEqual(rules('el.animate([{ opacity: 0 }, { opacity: 1 }], 300);'), ['waapi']);
    assert.deepEqual(rules('el.animate({ opacity: [0, 1] }, { duration: 300 });'), ['waapi']);
    assert.deepEqual(rules('const a = new Animation(effect, document.timeline);'), ['waapi', 'clock']);
    assert.deepEqual(rules('template.animate(t);'), []);
  });

  test('Chart.js must run with animation: false', () => {
    assert.deepEqual(rules('const c = new Chart(ctx, { type: "bar", data, options: { animation: false } });'), []);
    assert.deepEqual(rules('const c = new Chart(ctx, { type: "bar", data, options: { animation: { duration: 400 } } });'), ['chart-animation', 'chart-animation']);
    assert.deepEqual(rules('const c = new Chart(ctx, cfg);'), ['chart-animation']);
    assert.deepEqual(rules('Chart.defaults.animation = false; const c = new Chart(ctx, cfg);'), []);
    assert.deepEqual(rules('Chart.defaults.animation = { duration: 1 };'), ['chart-animation']);
    assert.deepEqual(rules('const preset = { animation: "rise" };'), [], 'our own data may use the word');
  });

  test('self-clocked and banned libraries are caught by import, dynamic import, script or global', () => {
    for (const code of [
      "import gsap from 'gsap';",
      "import { tsParticles } from '@tsparticles/engine';",
      "import { animate } from '../../vendor/animejs/anime.esm.js';",
      "import lottie from '/web/vendor/lottie-web/lottie_light.js';",
      "const m = await import('motion');",
      "export { Player } from '@remotion/player';",
      'gsap.to(el, { x: 100 });',
      'tsParticles.load("bg", opts);',
    ]) {
      assert.ok(rules(code).includes('self-clocked'), code);
    }
    for (const code of ["import { ease } from '../motion/index.js';", "import { Chart } from '../../vendor/chart.js/chart.umd.js';", "import { emotion } from './emotion.js';"]) {
      assert.ok(!rules(code).includes('self-clocked'), code);
    }
  });

  test('<video> is caught in render code; a live-only module can waive it with a reason', () => {
    assert.deepEqual(rules("const v = document.createElement('video');"), ['video']);
    assert.deepEqual(rules("root.innerHTML = '<video src=\"a.mp4\"></video>';"), ['video']);
    assert.deepEqual(rules("// determinism-allow video: live mode only, render mode uses /__frames images\nconst v = document.createElement('video');"), []);
    assert.deepEqual(rules("const v = h('video', { src: url, preload: 'auto' });"), ['video']);
    assert.deepEqual(rules("const img = document.createElement('img');"), []);
    assert.deepEqual(rules("plan.add('video', ref, key); const kind = 'video';"), [], 'the media kind is not an element');
  });
});

describe('scanner: purity of web/js/motion', () => {
  test('DOM and host globals are caught in the toolkit, not in stage code', () => {
    for (const code of ['const w = window.innerWidth;', 'document.body.append(x);', 'const s = getComputedStyle(el);', 'const g = globalThis.Intl;', 'fetch(url);']) {
      assert.ok(rules(code, MOTION).includes('dom'), code);
      assert.ok(!rules(code, STAGE).includes('dom'), code);
    }
  });

  test('locals, parameters and property names that look like globals are fine', () => {
    assert.deepEqual(rules('function f(o) { return o.document + o.window; }', MOTION), []);
    assert.deepEqual(rules('const x = { location: 1, screen: 2 };', MOTION), []);
    assert.deepEqual(rules('const seg = new Intl.Segmenter("ta", { granularity: "grapheme" });', MOTION), []);
  });
});

describe('scanner: template rules (§4.2)', () => {
  test('a structure role on a content element is caught', () => {
    assert.deepEqual(rules("itemEl.style.color = ctx.roles.color('struct.accent');", TEMPLATE), ['struct-role']);
    assert.deepEqual(rules("const accent = ctx.roles.color('struct.accent');\nnode.style.fill = accent;", TEMPLATE), ['struct-role']);
    assert.deepEqual(rules("barRect.setAttribute('fill', 'var(--struct-accent)');", TEMPLATE), ['struct-role']);
  });

  test('a structure role on structure (pips, borders, bumpers, tint) is fine, also outside templates', () => {
    assert.deepEqual(rules("progressPip.style.background = ctx.roles.color('struct.pip');", TEMPLATE), []);
    assert.deepEqual(rules("const accent = ctx.roles.color('struct.accent');\nitemCard.style.borderColor = accent;", TEMPLATE), []);
    assert.deepEqual(rules("bumper.style.background = ctx.roles.color('struct.accent');", TEMPLATE), []);
    assert.deepEqual(rules("itemEl.style.color = roles.color('struct.accent');", STAGE), []);
  });

  test('emphasis belongs to cues.js: mark / focus roles, outlines and underlines are caught in templates', () => {
    assert.deepEqual(rules("termEl.style.background = ctx.roles.color('mark');", TEMPLATE), ['emphasis']);
    assert.deepEqual(rules("el.style.outline = '4px solid ' + ctx.roles.color('ink');", TEMPLATE), ['emphasis']);
    assert.deepEqual(rules("el.style.textDecoration = 'underline';", TEMPLATE), ['emphasis']);
    assert.deepEqual(rules("el.style.cssText = 'text-decoration: underline';", TEMPLATE), ['emphasis']);
    assert.deepEqual(rules("ctx.cues.term(t, 'item-3');", TEMPLATE), []);
    assert.deepEqual(rules("el.style.outline = 'none';", TEMPLATE), ['emphasis'], 'templates do not touch outlines at all');
    assert.deepEqual(rules("termEl.style.background = roles.color('mark');", 'web/js/dynamic/cues.js'), []);
  });

  test('will-change, filter and backdrop-filter are caught in templates through every path (plan §P)', () => {
    for (const code of [
      "el.style.willChange = 'transform';",
      "el.style.filter = 'blur(2px)';",
      "el.style['backdrop-filter'] = 'blur(4px)';",
      "el.style.WebkitBackdropFilter = 'blur(4px)';",
      "el.style.setProperty('will-change', 'opacity');",
      "el.style.setProperty('-webkit-backdrop-filter', 'blur(4px)');",
      "Object.assign(el.style, { filter: 'none' });",
      'ctx.paint.style(el, \'filter\', `blur(${r}px)`);',
      "paint.style(card, 'will-change', 'transform');",
      "p.style(el, 'backdropFilter', 'blur(2px)');",
      "h('div', { style: { willChange: 'transform' } });",
      "svg('rect', { filter: 'url(#soft)' });",
      "el.setAttribute('filter', 'url(#soft)');",
      "ctx.paint.attr(node, 'filter', 'url(#soft)');",
      "el.style.cssText = 'opacity: 1; filter: blur(2px)';",
      'const css = `will-change: ${prop}`;',
      // the namespaced attribute call (SVG presentation attributes), the CSS Typed OM and attributes in markup
      "el.setAttributeNS(null, 'filter', 'url(#soft)');",
      "el.setAttributeNS(SVG_NS, 'will-change', 'transform');",
      "el.toggleAttribute('filter');",
      "el.attributeStyleMap.set('filter', 'blur(2px)');",
      "el.attributeStyleMap.append('will-change', 'transform');",
      "el.attributeStyleMap.set('backdrop-filter', CSS.px(2));",
      'el.innerHTML = \'<rect filter="url(#a)"></rect>\';',
      "el.outerHTML = '<g will-change=\"transform\"></g>';",
      'el.insertAdjacentHTML(\'beforeend\', `<rect x="${x}" filter="url(#a)"/>`);',
      'const markup = \'<svg><feImage backdrop-filter="x"/></svg>\';',
      // names are case-insensitive where the page reads them so: the HTML parser (SVG in HTML too), setProperty, the
      // Typed OM and setAttribute on an HTML element all lower-case them
      'el.innerHTML = \'<rect FILTER="url(#a)"></rect>\';',
      'el.innerHTML = \'<rect Filter="url(#a)"></rect>\';',
      'el.innerHTML = `<G Will-Change="transform"></G>`;',
      "el.style.setProperty('FILTER', 'blur(2px)');",
      "el.style.setProperty('-WEBKIT-BACKDROP-FILTER', 'blur(2px)');",
      "ctx.paint.style(el, 'Filter', 'blur(2px)');",
      "el.attributeStyleMap.set('FILTER', 'blur(2px)');",
      "el.setAttribute('FILTER', 'url(#soft)');",
    ]) {
      assert.deepEqual(rules(code, TEMPLATE), ['template-style'], code);
      assert.deepEqual(rules(code, STAGE), [], `${code} (stage infrastructure may: the rule is the templates')`);
    }
  });

  test('outline and text-decoration are caught in templates through the Painter, h() and attributes too', () => {
    for (const code of [
      "ctx.paint.style(el, 'outline', '4px solid');",
      "p.style(el, 'outline-offset', '8px');",
      "paint.style(word, 'text-decoration-line', 'underline');",
      "h('span', { style: { textDecoration: 'underline' } });",
      "svg('text', { 'text-decoration': 'underline' });",
      "ctx.paint.attr(t, 'text-decoration', 'underline');",
      "el.setAttributeNS(null, 'text-decoration', 'underline');",
      "el.attributeStyleMap.set('outline', '4px solid');",
      'el.innerHTML = \'<text text-decoration="underline">x</text>\';',
      'el.innerHTML = \'<text Text-Decoration="underline">x</text>\';',
      'el.innerHTML = \'<TEXT TEXT-DECORATION-LINE="underline">x</TEXT>\';',
      // the HTML tokenizer starts an attribute after '/' and straight after a quoted value too
      'el.innerHTML = \'<text/text-decoration="underline">x</text>\';',
      'el.innerHTML = \'<text x="0"text-decoration="underline">x</text>\';',
      "el.style.setProperty('OUTLINE', '4px solid');",
      "ctx.paint.attr(t, 'TEXT-DECORATION', 'underline');",
    ]) {
      assert.deepEqual(rules(code, TEMPLATE), ['emphasis'], code);
    }
  });

  test('a style or attribute name the scan cannot read is refused in templates', () => {
    for (const code of [
      'ctx.paint.style(el, prop, v);',
      'el.style[prop] = v;',
      'el.style.setProperty(name, v);',
      'Object.assign(el.style, styles);',
      "h('div', { style: styles });",
      "h('div', attrs, child);",
      "h('div', { [key]: v });",
      "h('div', { ...attrs });",
      'el.setAttribute(name, v);',
      "el.setAttribute('style', css);",
      'ctx.paint.attr(el, name, v);',
      'el.style.cssText = css;',
      'el.setAttributeNS(null, name, v);',
      "el.setAttributeNS(null, 'style', css);",
      'el.setAttributeNode(attr);',
      'el.attributeStyleMap.set(prop, v);',
      'const m = el.attributeStyleMap;',
      'el.innerHTML = markup;',
      'el.outerHTML += html;',
      "el.insertAdjacentHTML('afterbegin', html);",
      "const doc = new DOMParser().parseFromString(text, 'image/svg+xml');",
      'const frag = range.createContextualFragment(html);',
    ]) {
      assert.deepEqual(rules(code, TEMPLATE), ['template-style'], code);
      assert.deepEqual(rules(code, STAGE), [], code);
    }
  });

  test('ordinary styles, attributes and data objects are fine in templates', () => {
    for (const code of [
      "ctx.paint.style(el, 'opacity', fx(a));",
      "p.style(c.el, '--ink', ink);",
      "p.style(w, 'transform', `translate3d(0, ${y}px, 0)`);",
      "h('div', { class: 'dyn-header', style: { '--kicker': '40px', color: ink } }, pip);",
      "svg('circle', { cx: '48', stroke: ink, 'stroke-opacity': '0.18' });",
      "el.style.setProperty('--scrim', scrim);",
      "paint.attr(arc, 'stroke-dasharray', dash);",
      "h('table', null, rows);",
      "h('div', { class: 'x', dataset: { item: id } });",
      "el.style.cssText = 'opacity: 1';",
      "el.setAttribute('viewBox', '0 0 96 96');",
      "const preset = { filter: 'blur', outline: 'card' };",
      "g.filter = 'blur(2px)';",
      "el.setAttributeNS(null, 'stroke-width', '2');",
      "el.attributeStyleMap.set('opacity', 0.5);",
      "el.attributeStyleMap.delete('transform');",
      "el.innerHTML = '';",
      'el.innerHTML = \'<rect x="0" fill="none"></rect>\';',
      "el.insertAdjacentHTML('beforeend', '<span class=\"dyn-chip\"></span>');",
      "const note = 'a filter = the sieve';",
      'el.innerHTML = \'<svg viewBox="0 0 9 9"><Rect X="0" Fill="none"/></svg>\';',
      "el.style.setProperty('Opacity', '1');",
    ]) {
      assert.deepEqual(rules(code, TEMPLATE), [], code);
    }
  });

  test('templates convert TeX through ctx.renderTex, which the render page caches and counts (plan §2)', () => {
    for (const code of [
      "mountTex(host, latex, true, { pending: ctx.pending });",
      'mountTex(host, latex, false);',
      'mountTex(host, latex, false, opts);',
      'mountTex(host, latex, false, { pending: ctx.pending, renderTex: undefined });',
      'renderRich(text);',
      'renderRich(text, { pending: ctx.pending });',
      'renderRich(text, { ...opts });',
      'texNode(latex, true);',
      'texNode(latex, true, undefined);',
      'rt.renderRich(text, {});',
      "import { mountTex as mt } from '../../player/richtext.js';",
      "import * as rt from '../../player/richtext.js';",
      // a value that is not the context's converter: richtext falls back to its own MathJax cache
      'renderRich(text, { pending: ctx.pending, renderTex: null });',
      'mountTex(host, latex, false, { renderTex: false });',
      'mountTex(host, latex, false, { renderTex: myTex });',
      'renderRich(text, { renderTex: opts.renderTex || fallback });',
      'renderRich(text, { renderTex() { return x; } });',
      'texNode(latex, true, null);',
      'texNode(latex, true, convert);',
      // another object's renderTex may be undefined at run time
      'renderRich(text, { renderTex: opts.renderTex });',
      'renderRich(text, { renderTex: this.renderTex });',
      'texNode(latex, true, window.renderTex);',
      'mountTex(h, l, true, { renderTex: ({}).renderTex });',
      // re-exported under another name, with or without a module
      "export { renderRich as rr } from '../../player/richtext.js';",
      'export { mountTex as mt };',
      // a later option overrides the converter
      'renderRich(text, { renderTex: ctx.renderTex, ...opts });',
      'renderRich(text, { renderTex: ctx.renderTex, renderTex: null });',
      'renderRich(text, { renderTex: ctx.renderTex, [key]: null });',
      // a helper handed on as a value or bound under another name: its calls would escape the rule
      'const r = renderRich; r(text);',
      'const f = rt.renderRich;',
      'renderRich.call(null, text);',
      'const helpers = { mountTex };',
      'items.map(texNode);',
      "const { renderRich: rr } = helpers;",
      "const { 'mountTex': mt } = helpers;",
      // a dynamic import, the converter behind the helpers, MathJax itself
      "const mod = await import('../../player/richtext.js');",
      "import { renderTex } from '../../shared/libs.js';",
      "import { loadMathJax as load } from '../../shared/libs.js';",
      "import * as libs from '../../shared/libs.js';",
      "const lib = await import('../../shared/libs.js?v=2');",
      'const svgEl = MathJax.tex2svg(latex);',
      'window.MathJax.typesetPromise([el]);',
    ]) {
      const found = rules(code, TEMPLATE);
      assert.ok(found.length > 0 && found.every((r) => r === 'tex'), `${code}: ${found}`);
      assert.deepEqual(rules(code, STAGE), [], `${code} (the rule is the templates')`);
    }
    assert.deepEqual(rules("const { renderRich: rr } = await import('../../player/richtext.js');", TEMPLATE), ['tex', 'tex'],
      'the dynamic import and the renamed binding are both caught');
    for (const code of [
      'mountTex(tex, item.latex, true, { pending: ctx.pending, renderTex: ctx.renderTex });',
      'renderRich(text, { pending: ctx.pending, renderTex: ctx.renderTex });',
      'const { renderTex } = ctx; renderRich(text, { renderTex });',
      "renderRich(text, { ...opts, renderTex: ctx['renderTex'], pending: ctx.pending });",
      'texNode(latex, false, ctx.renderTex);',
      'texNode(latex, false, (ctx.renderTex));',
      "import { mathSpans, mountTex, parseRich, plainText, renderRich } from '../../player/richtext.js';",
      "const { renderRich } = helpers; renderRich(text, { renderTex: ctx.renderTex });",
      "import { loadLib } from '../../shared/libs.js';",
      'const o = { renderRich: 1, texNode: 2 };',
    ]) {
      assert.deepEqual(rules(code, TEMPLATE), [], code);
    }
  });

  test('templates never read the timeline (plan §1.4): ctx.timeline, any .timeline, a destructured timeline', () => {
    for (const code of [
      'const k = kickerOf(scene, ctx.timeline);',
      "const t = ctx['timeline'];",
      'const { timeline } = ctx;',
      'const { timeline: tl } = ctx;',
      'function build({ scene, timeline }) { return scene; }',
      'const chapters = stage.timeline.chapters;',
    ]) {
      assert.deepEqual(rules(code, TEMPLATE), ['timeline'], code);
      assert.deepEqual(rules(code, STAGE), [], `${code} (the stage reads it)`);
    }
    for (const code of ['const k = kickerOf(scene, ctx.chapter);', 'const w = { timeline: 1 };', 'const c = scene.chapters;']) {
      assert.deepEqual(rules(code, TEMPLATE), [], code);
    }
    assert.deepEqual(rules('const tl = document.timeline;', TEMPLATE), ['clock'], 'the document timeline is a clock');
  });

  test('only core.js imports the catalogue, and it reads geometry only: no colour, duration or personality (plan §1.1)', () => {
    const CORE = 'web/js/dynamic/core.js';
    for (const [code, file] of [
      ["import { CATALOGUE } from './catalogue.gen.js';", STAGE],
      ["import { CATALOGUE } from '../catalogue.gen.js';", TEMPLATE],
      ["export { CATALOGUE } from './catalogue.gen.js';", 'web/js/dynamic/media.js'],
      ["const m = await import('./catalogue.gen.js');", STAGE],
      ['const m = await import(name);', STAGE],
      ["import { CATALOGUE } from '../dynamic/catalogue.gen.js?v=2';", MOTION],
      ['const f = CATALOGUE.families;', CORE],
      ['const { content } = CATALOGUE;', CORE],
      ["const p = CATALOGUE['personalities'];", TEMPLATE],
      ['const ink = CATALOGUE.cues.dark.marker_ink;', 'web/js/dynamic/backgrounds/colour_block.js'],
      // the one reader: colours, durations and personality values beside the geometry it may read
      ['const k = CATALOGUE.captions.clean_pill.colours.key_ink;', CORE],
      ['const s = CATALOGUE.captions[style];', CORE],
      ['const { colours } = CATALOGUE.captions.clean_pill;', CORE],
      ['const { captions: { clean_pill: { colours } } } = CATALOGUE;', CORE],
      ['const s = CATALOGUE.scrims;', CORE],
      ['const d = CATALOGUE.templates[id].duration.landscape.max;', CORE],
      ['const t = CATALOGUE.templates[id];', CORE],
      ['const sec = CATALOGUE.transitions.crossfade.seconds;', CORE],
      // the end screen's length beside its box (plan §1.1: only the end-screen half is runtime zone data)
      ['const s = CATALOGUE.end_screen.landscape.seconds;', CORE],
      ['const s = CATALOGUE.end_screen[fmt].seconds;', CORE],
      ['const { seconds } = CATALOGUE.end_screen.landscape;', CORE],
      ['const e = CATALOGUE.end_screen.landscape;', CORE],
      ['const e = CATALOGUE.end_screen;', CORE],
      ['const w = CATALOGUE.studio_surface;', CORE],
      ['const b = CATALOGUE.backgrounds;', CORE],
      ['const c = CATALOGUE.semantic_classes;', CORE],
      ['const x = CATALOGUE[key];', CORE],
      ['const { zones, ...rest } = CATALOGUE;', CORE],
      ['const [first] = CATALOGUE;', CORE],
      ["const has = 'families' in CATALOGUE;", CORE],
      ['const c = CATALOGUE;', CORE],
      ['use(CATALOGUE);', CORE],
      ['export default CATALOGUE;', CORE],
      ['export { CATALOGUE };', CORE],
      ['const o = { CATALOGUE };', CORE],
      ['const d = /** @type {any} */ (CATALOGUE.templates)[id].duration;', CORE],
      // the reader imports `{ CATALOGUE }` as such, statically (the name the scan follows)
      ["import * as cat from './catalogue.gen.js';", CORE],
      ["import { CATALOGUE as C } from './catalogue.gen.js';", CORE],
      ["export { CATALOGUE } from './catalogue.gen.js';", CORE],
      ["const m = await import('./catalogue.gen.js');", CORE],
    ]) {
      assert.ok(rules(code, file).length > 0 && rules(code, file).every((r) => r === 'catalogue'), `${file}: ${code}`);
    }
    for (const [code, file] of [
      ["import { CATALOGUE } from './catalogue.gen.js';", CORE],
      ['const z = CATALOGUE.zones.landscape;', CORE],
      ['const { zones, type: { floors } } = CATALOGUE;', CORE],
      ['const m = CATALOGUE.captions.clean_pill.metrics;', CORE],
      ['const px = CATALOGUE.captions[style].metrics.padding_x;', CORE],
      ['const { metrics } = CATALOGUE.captions[style];', CORE],
      ['const s = CATALOGUE.templates[id].slots;', CORE],
      ['const s = /** @type {any} */ (CATALOGUE.templates)[id].slots;', CORE],
      ['const ok = Object.prototype.hasOwnProperty.call(CATALOGUE.templates, id);', CORE],
      ['const ok = Object.hasOwn(CATALOGUE.captions, id) && id in CATALOGUE.templates;', CORE],
      ['const r = CATALOGUE.roles.colour;', CORE],
      ['const steps = CATALOGUE.layout.spacing.steps;', CORE],
      ['const b = CATALOGUE.end_screen.landscape.box;', CORE],
      ['const { box } = CATALOGUE.end_screen[fmt];', CORE],
      ['const x0 = CATALOGUE.end_screen.landscape.box.x0;', CORE],
      ['const has = fmt in CATALOGUE.end_screen;', CORE],
      ['const v = CATALOGUE.version + CATALOGUE.zones_version;', CORE],
      ['const c = presentation.content;', TEMPLATE],
      ['const { families } = registry;', STAGE],
      ["import { ZONES } from '../core.js';", TEMPLATE],
    ]) {
      assert.deepEqual(rules(code, file), [], `${file}: ${code}`);
    }
  });
});

describe('scanner: the catalogue is data, dynamic.css carries no template-banned style', () => {
  const HELPER = 'function freezeInside(value) {\n  for (const v of Object.values(value)) {\n    if (v !== null && typeof v === \'object\') Object.freeze(freezeInside(v));\n  }\n  return value;\n}\n';
  /** @param {string} literal @param {string} [extra] */
  const catalogue = (literal, extra = '') => `// generated\n${extra}${HELPER}export const CATALOGUE = Object.freeze(freezeInside(${literal}));\n`;

  test('a frozen literal of plain data passes; the committed file is checked by the repository test', () => {
    assert.deepEqual(scanCatalogue(catalogue('{"a": [1, -2.5, "x", true, false, null], "b": {"c": {}}, d: 3}')), []);
  });

  test('anything but data is caught', () => {
    for (const [literal, extra] of [
      ['{"a": () => 1}', ''],
      ['{"a": Date.now()}', ''],
      ['{"a": other}', ''],
      ['{"a": `x${y}`}', ''],
      ['{get a() { return 1; }}', ''],
      ['{...base}', ''],
      ['{[key]: 1}', ''],
      ['{"a": [...list]}', ''],
      ['{"a": 1}', "import { x } from './x.js';\n"],
      ['{"a": 1}', 'window.x = 1;\n'],
      ['{"a": 1}', 'export const OTHER = 1;\n'],
    ]) {
      const found = scanCatalogue(catalogue(literal, extra));
      assert.ok(found.length > 0 && found.every((f) => f.rule === 'catalogue-data'), `${extra}${literal}`);
    }
    const sneaky = catalogue('{"a": 1}').replace('Object.freeze(freezeInside(v))', 'fetch(v)');
    assert.ok(scanCatalogue(sneaky).some((f) => /calls fetch/.test(f.detail)), 'the helper may only freeze');
    assert.ok(scanCatalogue('export const CATALOGUE = {"a": 1};\n').length > 0, 'not frozen');
  });

  test('the face table is held to the same rules under its own name and rule id', () => {
    const faces = (/** @type {string} */ literal) => catalogue(literal).replace('export const CATALOGUE', 'export const FACES');
    assert.deepEqual(scanCatalogue(faces('{"a": [1, "x"]}'), FACES_FILE, 'FACES', 'faces-data'), []);
    const bad = scanCatalogue(faces('{"a": Date.now()}'), FACES_FILE, 'FACES', 'faces-data');
    assert.ok(bad.length > 0 && bad.every((f) => f.rule === 'faces-data'));
    const misnamed = scanCatalogue(catalogue('{"a": 1}'), FACES_FILE, 'FACES', 'faces-data');
    assert.ok(misnamed.some((f) => /0 FACES exports/.test(f.detail)), 'a CATALOGUE export is not the face table');
  });

  test('dynamic.css: the banned properties are caught outside the measured exception', () => {
    const css = [
      '.dyn-a { opacity: 1; filter: blur(2px); }',
      '.dyn-b {',
      '  outline: 4px solid var(--ink);',
      '}',
      '.dyn-c { -webkit-backdrop-filter: blur(3px); }',
      '.dyn-d { will-change: transform; }',
      '.dyn-media--contain_blur.dyn-media--clip { will-change: transform; }',
      '.dyn-e { --filter-x: 1; -webkit-text-stroke: 12px #000; text-decoration-line: underline; }',
      '/* .dyn-f { filter: none } */',
    ].join('\n');
    assert.deepEqual(scanStyleCss(css, 'web/css/dynamic.css').map((f) => [f.line, f.rule]),
      [[1, 'template-style'], [3, 'emphasis'], [5, 'template-style'], [6, 'template-style'], [8, 'emphasis']]);
    assert.deepEqual(scanStyleCss('.dyn-media--contain_blur.dyn-media--clip { filter: blur(1px); }', 'x.css').map((f) => f.rule), ['template-style'],
      'the exception covers its one property only');
  });
});

describe('scanner: CSS and the render page', () => {
  test('stylesheet rules', () => {
    const css = [
      '.a { transition: opacity 0.2s; }',
      '.b { animation: spin 1s infinite; }',
      '.c { -webkit-animation-name: x; }',
      '@keyframes spin { to { transform: rotate(1turn); } }',
      'html { scroll-behavior: smooth; }',
      '.ok { transition: none; animation: none !important; --transition-x: 1; }',
      '/* transition: opacity 1s */',
      '.e { transition-duration: 0s; }',
    ].join('\n');
    const found = scanCss(css, 'web/css/dynamic.css');
    assert.deepEqual(
      found.map((f) => [f.line, f.rule]),
      [[4, 'css-motion'], [1, 'css-motion'], [2, 'css-motion'], [3, 'css-motion'], [5, 'css-motion']],
    );
  });

  test('the kill switch must cover pseudo-elements and use !important', () => {
    assert.equal(hasMotionKillSwitch('*, *::before, *::after { transition: none !important; animation: none !important; }'), true);
    assert.equal(hasMotionKillSwitch('* { transition: none !important; animation: none !important; }'), false);
    assert.equal(hasMotionKillSwitch('*, *::before, *::after { transition: none; animation: none; }'), false);
  });

  test('HTML: inline styles, inline scripts, script sources, <video> and linked sheets', () => {
    const html = [
      '<!doctype html><html><head>',
      '<link rel="stylesheet" href="/web/css/tokens.css" />',
      '<link rel="stylesheet" href="/web/css/dynamic.css">',
      '<style>.x { transition: transform 1s; }</style>',
      '<script type="module" src="/web/js/dynamic/render.js"></script>',
      '<script src="https://cdn.example/gsap.min.js"></script>',
      '<script>const t = Date.now();</script>',
      '</head><body><div style="animation: a 1s"></div><video></video></body></html>',
    ].join('\n');
    const { findings, sheets } = scanHtml(html, RENDER_PAGE);
    assert.deepEqual(sheets, ['/web/css/tokens.css', '/web/css/dynamic.css']);
    assert.deepEqual(findings.map((f) => [f.line, f.rule]).sort(), [[4, 'css-motion'], [6, 'self-clocked'], [7, 'clock'], [8, 'css-motion'], [8, 'video']].sort());
  });
});
