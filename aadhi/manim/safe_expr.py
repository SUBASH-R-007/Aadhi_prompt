"""Safe evaluation of math expressions in ``x`` (function_plot template).

Expressions come from the LLM, so they are never ``eval``-ed. They are parsed with :mod:`ast`
and checked against an allow-list (numbers, ``x``, ``pi``/``e``, arithmetic, and a fixed set of
numpy functions); evaluation walks the tree with numpy. ``^`` is accepted as power (math.js style)
and ``ln`` as an alias of ``log``. The curve is sampled on the host and only the samples are
embedded in the Manim scene, so no expression ever runs inside the sandbox.
"""

from __future__ import annotations

import ast
from collections.abc import Callable

import numpy as np

MAX_EXPR_LENGTH = 200
MAX_NODES = 120

_FUNCS: dict[str, tuple[Callable[..., np.ndarray], int]] = {
    "sin": (np.sin, 1),
    "cos": (np.cos, 1),
    "tan": (np.tan, 1),
    "asin": (np.arcsin, 1),
    "acos": (np.arccos, 1),
    "atan": (np.arctan, 1),
    "sinh": (np.sinh, 1),
    "cosh": (np.cosh, 1),
    "tanh": (np.tanh, 1),
    "exp": (np.exp, 1),
    "log": (np.log, 1),
    "ln": (np.log, 1),
    "log10": (np.log10, 1),
    "log2": (np.log2, 1),
    "sqrt": (np.sqrt, 1),
    "cbrt": (np.cbrt, 1),
    "abs": (np.abs, 1),
    "sign": (np.sign, 1),
    "floor": (np.floor, 1),
    "ceil": (np.ceil, 1),
    "round": (np.round, 1),
    "min": (np.minimum, 2),
    "max": (np.maximum, 2),
    "pow": (np.power, 2),
    "mod": (np.mod, 2),
}
_CONSTANTS = {"pi": float(np.pi), "e": float(np.e)}
_BINOPS: dict[type[ast.operator], Callable[[np.ndarray, np.ndarray], np.ndarray]] = {
    ast.Add: np.add,
    ast.Sub: np.subtract,
    ast.Mult: np.multiply,
    ast.Div: np.true_divide,
    ast.Pow: np.power,
    ast.Mod: np.mod,
}
ALLOWED_NAMES = frozenset({"x", *_FUNCS, *_CONSTANTS})


class ExpressionError(ValueError):
    """The expression is not in the allowed subset."""


def parse_expr(expr: str) -> ast.Expression:
    """Parse and validate ``expr``; raise :class:`ExpressionError` with a helpful message."""
    if not isinstance(expr, str) or not expr.strip():
        raise ExpressionError("expression is empty")
    if len(expr) > MAX_EXPR_LENGTH:
        raise ExpressionError(f"expression is too long (max {MAX_EXPR_LENGTH} characters)")
    source = expr.strip().replace("^", "**")
    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(
            f"cannot parse expression {expr!r}: {exc.msg} (write products explicitly, e.g. 2*x)"
        ) from None
    nodes = list(ast.walk(tree))
    if len(nodes) > MAX_NODES:
        raise ExpressionError("expression is too complex")
    for node in nodes:
        _check_node(node, expr)
    return tree


def _check_node(node: ast.AST, expr: str) -> None:
    if isinstance(node, (ast.Expression, ast.Load, ast.operator, ast.unaryop)):
        if isinstance(node, ast.operator) and type(node) not in _BINOPS:
            raise ExpressionError(f"operator {type(node).__name__} is not allowed in {expr!r}")
        if isinstance(node, ast.unaryop) and not isinstance(node, (ast.UAdd, ast.USub)):
            raise ExpressionError(f"operator {type(node).__name__} is not allowed in {expr!r}")
        return
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ExpressionError(f"only numbers are allowed as constants in {expr!r}")
        return
    if isinstance(node, ast.Name):
        if node.id not in ALLOWED_NAMES:
            raise ExpressionError(
                f"unknown name {node.id!r} in {expr!r}; use x, pi, e and functions {sorted(_FUNCS)}"
            )
        return
    if isinstance(node, (ast.BinOp, ast.UnaryOp)):
        return
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
            raise ExpressionError(f"only the functions {sorted(_FUNCS)} may be called in {expr!r}")
        if node.keywords:
            raise ExpressionError(f"keyword arguments are not allowed in {expr!r}")
        arity = _FUNCS[node.func.id][1]
        if len(node.args) != arity:
            raise ExpressionError(f"{node.func.id}() takes {arity} argument(s) in {expr!r}")
        return
    raise ExpressionError(f"{type(node).__name__} is not allowed in expression {expr!r}")


def _eval(node: ast.AST, x: np.ndarray) -> np.ndarray:
    if isinstance(node, ast.Expression):
        return _eval(node.body, x)
    if isinstance(node, ast.Constant):
        return np.full_like(x, float(node.value))
    if isinstance(node, ast.Name):
        if node.id == "x":
            return x
        return np.full_like(x, _CONSTANTS[node.id])
    if isinstance(node, ast.UnaryOp):
        value = _eval(node.operand, x)
        return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.BinOp):
        return _BINOPS[type(node.op)](_eval(node.left, x), _eval(node.right, x))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        fn = _FUNCS[node.func.id][0]
        return fn(*(_eval(a, x) for a in node.args))
    raise ExpressionError(f"cannot evaluate {type(node).__name__}")  # pragma: no cover - parse_expr guards


def evaluate(expr: str, x: np.ndarray | float) -> np.ndarray:
    """Evaluate ``expr`` at ``x`` (array or scalar). Invalid points become ``nan``."""
    tree = parse_expr(expr)
    xs = np.atleast_1d(np.asarray(x, dtype=float))
    with np.errstate(all="ignore"):
        ys = np.asarray(_eval(tree, xs), dtype=float)
    ys = np.broadcast_to(ys, xs.shape).astype(float)
    ys[~np.isfinite(ys)] = np.nan
    return ys


def sample(expr: str, x_min: float, x_max: float, n: int = 240) -> tuple[np.ndarray, np.ndarray]:
    """Sample ``expr`` on ``n`` evenly spaced points of ``[x_min, x_max]``."""
    xs = np.linspace(float(x_min), float(x_max), int(n))
    return xs, evaluate(expr, xs)


def derivative(expr: str, x0: float, h: float = 1e-4) -> float:
    """Central-difference derivative of ``expr`` at ``x0`` (``nan`` when undefined)."""
    ys = evaluate(expr, np.array([x0 - h, x0 + h]))
    return float((ys[1] - ys[0]) / (2 * h))
