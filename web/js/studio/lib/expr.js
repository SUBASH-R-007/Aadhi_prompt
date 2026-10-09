// @ts-check
/**
 * Client-side mirror of the graph-expression allow-list in aadhi/schemas/screenplay.py
 * (`_check_expr`). The player evaluates expressions with math.js only; this check gives the
 * teacher immediate feedback before the server validates.
 */

export const EXPR_FUNCS = new Set([
  'sin', 'cos', 'tan', 'asin', 'acos', 'atan', 'sinh', 'cosh', 'tanh', 'exp', 'log', 'log10', 'log2',
  'sqrt', 'cbrt', 'abs', 'sign', 'floor', 'ceil', 'round', 'min', 'max', 'pow', 'mod', 'pi', 'e', 'x',
]);

const TOKEN = /\s*(?:(\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|\.\d+)|([A-Za-z_][A-Za-z0-9_]*)|(\*\*|[-+*/^(),.]))/y;

/**
 * @param {string} expr
 * @returns {string | null} problem description, or null when the expression is allowed
 */
export function checkExpr(expr) {
  const s = String(expr || '');
  if (!s.trim()) return 'Enter an expression in x, e.g. x^2 + 1.';
  if (s.length > 200) return 'At most 200 characters.';
  let pos = 0;
  let depth = 0;
  while (pos < s.length) {
    TOKEN.lastIndex = pos;
    const m = TOKEN.exec(s);
    if (!m || m.index !== pos || TOKEN.lastIndex === pos) {
      if (s.slice(pos).trim() === '') break;
      return `Unsupported character “${s.slice(pos, pos + 1).trim() || s[pos]}”.`;
    }
    if (m[2] && !EXPR_FUNCS.has(m[2])) return `Unknown name “${m[2]}” (use x and math functions).`;
    if (m[3] === '(') depth += 1;
    if (m[3] === ')') depth -= 1;
    if (depth < 0) return 'Unbalanced parentheses.';
    pos = TOKEN.lastIndex;
  }
  if (depth !== 0) return 'Unbalanced parentheses.';
  return null;
}
