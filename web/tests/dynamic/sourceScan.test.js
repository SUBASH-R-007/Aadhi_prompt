// @ts-check
// Determinism source scan (DYNAMIC_VIDEO_DESIGN §7.4.2, §4.2). Every visual property of the Dynamic stage
// must be a pure function of (timeline, frame), so the stage code may not read a clock, draw unseeded
// random numbers, run its own animation loop or timers, or let CSS / libraries animate on their own.
// The JavaScript under web/js/dynamic/** and web/js/motion/** is parsed with the TypeScript compiler API
// (no ESLint); web/css/dynamic.css and the render page's inline styles are scanned as CSS.
//
// Escape hatch: infrastructure (never web/js/motion/** and never a template) may run the stage's own
// requestAnimationFrame, a pre-`ready` timer or a live-mode <video> with a pragma on the same line or the
// line above, which must give a reason:  // determinism-allow raf: seek() waits one frame (§7.3 step 6)
// The clock, randomness, CSS-motion, library, DOM-purity, structure-role and emphasis rules have none.
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const REPO = fileURLToPath(new URL('../../../', import.meta.url));
const SCANNED_JS_DIRS = ['web/js/dynamic', 'web/js/motion'];
const SCANNED_CSS = ['web/css/dynamic.css'];
const RENDER_PAGE = 'web/render-dynamic.html';
const TEMPLATES_DIR = 'web/js/dynamic/templates/';
const MOTION_DIR = 'web/js/motion/';

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
 * @param {{emphasis?: boolean}} [options]
 * @returns {Finding[]}
 */
export function scanCss(text, file, baseLine = 1, { emphasis = false } = {}) {
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
    for (const f of scanCss(value, file, 1, { emphasis: template })) add(f.rule, node, f.detail);
    if (/<video[\s>/]/i.test(value)) add('video', node, '<video>');
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
      if (template && /^(outline|textDecoration)/.test(member) && endsWithName(obj, 'style')) add('emphasis', node);
      if (member === 'animation' && ts.isPropertyAccessExpression(obj) && obj.name.text === 'defaults' && endsWithName(obj.expression, 'Chart')) {
        const p = node.parent;
        if (p && ts.isBinaryExpression(p) && p.left === node) {
          if (p.right.kind === ts.SyntaxKind.FalseKeyword) chartAnimationOff = true;
          else add('chart-animation', node);
        }
      }
    }

    // --- destructuring Date / performance / Math -------------------------------------------------------
    if (ts.isVariableDeclaration(node) && node.initializer && ts.isObjectBindingPattern(node.name)) {
      for (const el of node.name.elements) {
        const key = (el.propertyName && ts.isIdentifier(el.propertyName) && el.propertyName.text) || (ts.isIdentifier(el.name) && el.name.text);
        if (key === 'now' && (isGlobalRef(node.initializer, 'Date') || isGlobalRef(node.initializer, 'performance'))) add('clock', el);
        if (key === 'random' && isGlobalRef(node.initializer, 'Math')) add('random', el);
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
      const arg0 = node.arguments[0];
      const str0 = arg0 && ts.isStringLiteralLike(arg0) ? arg0.text : null;
      if (name === 'setProperty' && str0 !== null) {
        const arg1 = node.arguments[1];
        const reset = arg1 && ts.isStringLiteralLike(arg1) && CSS_MOTION_ALLOWED_VALUE.test(arg1.text.trim());
        if (/^(-webkit-|-moz-)?(transition|animation)/i.test(str0) && !reset) add('css-motion', node, `setProperty('${str0}')`);
        if (template && /^(outline|text-decoration)/i.test(str0)) add('emphasis', node, `setProperty('${str0}')`);
      }
      // Object.assign(el.style, { transition: '...' })
      if (name === 'assign' && ts.isPropertyAccessExpression(callee) && isGlobalRef(callee.expression, 'Object') && arg0 && endsWithName(arg0, 'style')) {
        for (const src of node.arguments.slice(1)) {
          if (!ts.isObjectLiteralExpression(src)) continue;
          for (const prop of src.properties) {
            const key = prop.name && (ts.isIdentifier(prop.name) || ts.isStringLiteralLike(prop.name)) ? prop.name.text : '';
            const value = ts.isPropertyAssignment(prop) && ts.isStringLiteralLike(prop.initializer) ? prop.initializer.text.trim() : null;
            if (/^(-?webkit-?|-?moz-?)?(transition|animation)/i.test(key) && !(value !== null && CSS_MOTION_ALLOWED_VALUE.test(value))) add('css-motion', prop);
            if (template && /^(outline|textDecoration|text-decoration)/.test(key)) add('emphasis', prop);
          }
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

    // --- modules -------------------------------------------------------------------------------------
    if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) && node.moduleSpecifier && ts.isStringLiteralLike(node.moduleSpecifier)) {
      checkModule(node.moduleSpecifier.text, node);
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
