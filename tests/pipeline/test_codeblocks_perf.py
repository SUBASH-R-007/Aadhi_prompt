"""``codeblocks.text_fences`` on hostile "~~~" input: code signals matched in bulk give exactly the per-line
answers, every line is matched at most once, and the work per document is capped (``TILDE_SCAN_BUDGET``)."""

from __future__ import annotations

import random
import time

import pytest

from aadhi.pipeline import codeblocks
from aadhi.pipeline.codeblocks import CODE_SIGNALS, _line_signals, fence_open, text_fences
from aadhi.pipeline.ingest import text_to_markdown

TRICKY = [
    "def f(x):", "class A(B):", "print(x)", "#\x0binclude <a>", "# define X", "import os", "from a import b",
    "return x;", "for i in range(3):", "for (i = 0; i < n; i++)", "for\x0c(a;b;c)", "while (x)", "while (x)",
    "public static void", "SELECT a FROM b", "x;", "x;\t", "x;\x85", "{", " }\x0b", "a => b", "let x = 1",
    "const\x0by = 2", "if (a) {", "if\x85(a)", "elif x:", "else\x0c:", "printf (\"%d\")", "scanf (", "int\x0bx;",
    "static const long y;", "unsigned int *p = 0;", "", "   ", "\t", "plain words only", "~~~ a b c", "while", "(x)",
]


def per_line(line: str) -> int:
    return sum(1 << k for k, p in enumerate(CODE_SIGNALS) if p.search(line)) if line.strip() else -1


def test_bulk_signals_equal_the_per_line_search():
    rng = random.Random(7)
    for _ in range(200):
        chunk = ["".join(rng.choice(TRICKY) for _ in range(rng.randint(1, 3))) for _ in range(rng.randint(1, 60))]
        assert _line_signals(chunk) == [per_line(line) for line in chunk]
    # a "\s" that would cross a line break must not: "while" and "(x)" are on different lines
    assert _line_signals(["while", "(x)"]) == [per_line("while"), per_line("(x)")] == [0, 0]


def decisions(lines: list[str]) -> list[tuple[int, bool]]:
    is_fence = text_fences(lines)
    return [(i, is_fence(i, o)) for i, line in enumerate(lines) if (o := fence_open(line)) is not None]


@pytest.mark.parametrize("bulk", [1, codeblocks._BULK_LINES, 10_000])
def test_bulk_and_per_line_matching_decide_the_same(monkeypatch, bulk):
    """Whichever way a line's signals are matched, every fence is decided as before."""
    rng = random.Random(11)
    docs = [[rng.choice(TRICKY + ["~~~", "~~~~", "~~~python", "```"]) for _ in range(rng.randint(5, 300))]
            for _ in range(60)]
    expected = []
    monkeypatch.setattr(codeblocks, "_BULK_LINES", 10**9)  # per line only
    for doc in docs:
        expected.append(decisions(doc))
    monkeypatch.setattr(codeblocks, "_BULK_LINES", bulk)
    assert [decisions(doc) for doc in docs] == expected


def test_each_line_is_matched_at_most_once(monkeypatch):
    seen: list[int] = []
    real = codeblocks._line_signals

    def spy(chunk):
        seen.append(len(chunk))
        return real(chunk)

    monkeypatch.setattr(codeblocks, "_line_signals", spy)
    text = ("~~~ a b c\n" * 39 + "~~~\n") * 200  # 8,000 overlapping candidate fences
    text_to_markdown(text)
    assert sum(seen) <= text.count("\n")


def test_the_scan_budget_caps_the_work_per_document(monkeypatch):
    code = "~~~\nint x = 1;\nprintf(\"%d\", x);\n~~~\n"
    monkeypatch.setattr(codeblocks, "TILDE_SCAN_BUDGET", 2 * 2)  # room for exactly two candidate fences
    md = text_to_markdown(code * 3 + "~~~c\nint y;\n~~~\n")
    assert md.count('\n~~~\nint x = 1;\nprintf("%d", x);\n~~~') + md.startswith('~~~\nint x = 1;') == 2
    assert md.count("\\~~~") == 2  # the third unlabeled fence stays text (opening and closing line)
    assert "~~~c\nint y;\n~~~" in md  # a fence naming its language needs no scan


def test_hostile_tilde_text_stays_fast():
    text = ("~~~ a b c\n" * 39 + "~~~\n") * 2_000  # 80k candidate fences, 0.8 MB
    start = time.perf_counter()
    text_to_markdown(text)
    assert time.perf_counter() - start < 5.0
