"""richlite.tokenize: span rules (bold/italic/code/math/keyword, escapes) and linear time on unclosed markers."""

from __future__ import annotations

import time

import pytest

from aadhi.pipeline.richlite import Span, to_plain, tokenize


def spans(text: str) -> list[tuple[str, str]]:
    return [(s.kind, s.text) for s in tokenize(text)]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("plain", [("text", "plain")]),
        ("a **bold** b", [("text", "a "), ("bold", "bold"), ("text", " b")]),
        ("**V = IR**", [("bold", "V = IR")]),
        ("**a*b**", [("bold", "a*b")]),
        ("**a***", [("bold", "a*")]),
        # the optional tail is greedy: a later closer wins over a one-character content
        ("**$**]\\**", [("bold", "$**]\\")]),
        ("**x**", [("bold", "x")]),
        ("**xy** and **z**", [("bold", "xy"), ("text", " and "), ("bold", "z")]),
        ("**x**y**", [("bold", "x**y")]),  # a closer followed by a word character does not close
        ("a**b**", [("text", "a**b**")]),  # intraword asterisks stay literal
        ("** spaced**", [("text", "** spaced**")]),
        ("**unclosed", [("text", "**unclosed")]),
        ("*it* and `code` and $V=IR$ and [[term]]",
         [("italic", "it"), ("text", " and "), ("code", "code"), ("text", " and "), ("math", "V=IR"),
          ("text", " and "), ("keyword", "term")]),
        ("\\*not italic\\*", [("text", "*not italic*")]),
        ("costs \\$5 and $x$", [("text", "costs $5 and "), ("math", "x")]),
        ("**multi\nline**", [("bold", "multi\nline")]),
        ("[[a [b]]", [("keyword", "a [b")]),
        ("[[a] b]]", [("text", "[[a] b]]")]),  # the first "]" must start the closing "]]"
        ("[[]]", [("text", "[[]]")]),
        ("[[[x]]", [("keyword", "[x")]),
    ],
)
def test_span_rules(text: str, expected: list[tuple[str, str]]) -> None:
    assert spans(text) == expected


def test_spans_are_span_objects() -> None:
    assert tokenize("**b**") == [Span("bold", "b")]
    assert tokenize("") == [] and to_plain("**Ohm's** *law*") == "Ohm's law"


@pytest.mark.parametrize(("unit", "times"), [("**a ", 6000), ("**yyy ", 6000), (" x", 12000), ("[[", 8000), ("[[a", 6000)],
                         ids=["unclosed-bold", "unclosed-bold-words", "one-bold-opener", "unclosed-keywords",
                              "unclosed-keyword-words"])
def test_unclosed_markers_stay_linear(unit: str, times: int) -> None:
    # The single-regex tokenizer took ~1 s for 24k characters of unclosed "**" or "[[" (quadratic); lint and
    # quality checks tokenize every field of a 200-scene screenplay.
    crafted = "**a" + unit * times if unit == " x" else unit * times
    t0 = time.perf_counter()
    out = tokenize(crafted)
    assert time.perf_counter() - t0 < 0.5
    assert "".join(s.text for s in out) == crafted  # nothing is bold, nothing is lost
