"""TeX deny-list and the safe math expression evaluator."""

from __future__ import annotations

import math

import numpy as np
import pytest

from aadhi.manim.safe_expr import ExpressionError, derivative, evaluate, parse_expr, sample
from aadhi.manim.texcheck import check_tex, tex_problem


@pytest.mark.parametrize(
    "tex",
    [
        r"V = I R",
        r"I = \frac{V}{R} = \frac{12}{4}",
        r"\sum_{i=1}^{n} i = \frac{n(n+1)}{2}",
        r"\left( a + b \right)^2",
        r"\begin{pmatrix} 1 & 2 \ 3 & 4 \end{pmatrix}",
        r"\begin{cases} x & x > 0 \ -x & x \le 0 \end{cases}",
        r"\vec{F} = m\vec{a}",
        r"\fontsize{12}{14}\selectfont x",
        r"\text{speed} = \frac{d}{t}",
        r"\lim_{x \to 0} \frac{\sin x}{x} = 1",
        r"\theta \approx 30^\circ",
    ],
)
def test_common_math_is_allowed(tex: str) -> None:
    assert tex_problem(tex) is None
    assert check_tex(tex) == tex


@pytest.mark.parametrize(
    "tex",
    [
        r"\input{/etc/passwd}",
        r"\include{secret}",
        r"\verbatiminput{x}",
        r"\lstinputlisting{x}",
        r"\includegraphics{x.png}",
        r"^^5cinput{x}",
        r"\csname input\endcsname{x}",
        r"\expandafter\x",
        r"\def\x{1}",
        r"\newcommand{\x}{1}",
        r"\catcode`\|=0",
        r"\immediate\write18{rm -rf /}",
        r"\openin1=x",
        r"\read1 to \x",
        r"\usepackage{shellesc}",
        r"\directlua{os.execute('x')}",
        r"\begin{filecontents}{x.tex}",
        r"\href{http://x}{y}",
        r"\url{http://x}",
        r"\special{ps: x}",
        r"\scantokens{x}",
        r"\font\x=cmr10",
        r"\let\a\b",
        r"\makeatletter",
        r"\pdfprimitive x",
    ],
)
def test_dangerous_tex_is_refused(tex: str) -> None:
    assert tex_problem(tex)
    with pytest.raises(ValueError):
        check_tex(tex)


def test_tex_structure_checks() -> None:
    assert "unmatched '{'" in tex_problem(r"\frac{a}{b")
    assert "unmatched '}'" in tex_problem(r"a}")
    assert tex_problem(r"\{ a \}") is None  # escaped braces are fine
    assert "too long" in tex_problem("x" * 5000)
    assert tex_problem(123) == "latex must be a string"  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("expr", "x", "expected"),
    [
        ("x^2", 3.0, 9.0),
        ("0.5*x**2 - 2", 2.0, 0.0),
        ("sin(x)", math.pi / 2, 1.0),
        ("exp(-x)", 0.0, 1.0),
        ("ln(e)", 0.0, 1.0),
        ("max(x, 1)", 0.5, 1.0),
        ("abs(-x) + pi", 1.0, 1.0 + math.pi),
        ("mod(x, 3)", 7.0, 1.0),
        ("sqrt(x)", 16.0, 4.0),
        ("-x", 2.0, -2.0),
    ],
)
def test_evaluate(expr: str, x: float, expected: float) -> None:
    assert evaluate(expr, x)[0] == pytest.approx(expected)


@pytest.mark.parametrize(
    "expr",
    [
        "__import__('os')",
        "x.real",
        "(lambda: 1)()",
        "open('x')",
        "[x for x in y]",
        "x if x else 1",
        "x < 1",
        "'abc'",
        "sin(x, y=2)",
        "sin()",
        "y",
        "2x",
        "x @ x",
        "x // 2",
        "",
        "1j",
        "True",
        "x" * 300,
    ],
)
def test_parse_rejects_unsafe_expressions(expr: str) -> None:
    with pytest.raises(ExpressionError):
        parse_expr(expr)


def test_sample_marks_undefined_points_nan() -> None:
    xs, ys = sample("1/x", -1, 1, 5)
    assert np.isnan(ys[2]) and ys[0] == pytest.approx(-1.0)
    _, ys = sample("sqrt(x)", -1, 1, 3)
    assert np.isnan(ys[0]) and ys[2] == pytest.approx(1.0)
    _, ys = sample("10^x^x", 0, 10, 4)  # overflow -> nan, never hangs
    assert np.isnan(ys[-1])


def test_derivative() -> None:
    assert derivative("x^2", 3.0) == pytest.approx(6.0, rel=1e-4)
    assert math.isnan(derivative("sqrt(x)", -1.0))
