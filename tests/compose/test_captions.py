"""Caption splitting and SRT/VTT serialisation."""

from __future__ import annotations

import re

import pytest

from aadhi.compose.captions import (
    MAX_LINE_CHARS,
    balance_lines,
    escape_burn_in_text,
    escape_srt_text,
    escape_vtt_text,
    estimate_words,
    normalize_cues,
    shift_cues,
    split_caption,
    to_srt,
    to_vtt,
    tokenize,
    wrap_tokens,
)
from aadhi.schemas.timeline import CaptionCue, TimedWord

LONG = ("Ohm's law states that the current through a conductor between two points is directly proportional "
        "to the voltage across the two points. Introducing the constant of proportionality, the resistance, "
        "one arrives at the usual mathematical equation that describes this relationship.")


def words_for(text: str, start: float = 0.0, per_word: float = 0.3) -> list[TimedWord]:
    return [TimedWord(text=w, start=start + i * per_word, end=start + (i + 1) * per_word - 0.02)
            for i, w in enumerate(text.split())]


def assert_cue_shape(cues: list[CaptionCue]) -> None:
    for c in cues:
        lines = c.text.split("\n")
        assert 1 <= len(lines) <= 2, c.text
        assert all(len(ln) <= MAX_LINE_CHARS for ln in lines), c.text
        assert c.end > c.start
    for a, b in zip(cues, cues[1:], strict=False):
        assert a.end <= b.start + 1e-9


def test_tokenize_hard_splits_long_tokens() -> None:
    toks = tokenize("a " + "x" * 100 + " b")
    assert toks[0] == "a" and toks[-1] == "b"
    assert all(len(t) <= MAX_LINE_CHARS for t in toks)
    assert "".join(toks[1:-1]) == "x" * 100


def test_wrap_and_balance() -> None:
    toks = "one two three four five six seven eight nine ten eleven twelve".split()
    lines = wrap_tokens(toks, 30)
    assert all(len(ln) <= 30 for ln in lines)
    balanced = balance_lines("aaaa bbbb cccc dddd eeee ffff gggg hhhh iiii jjjj".split(), 42)
    assert len(balanced) == 2
    assert abs(len(balanced[0]) - len(balanced[1])) <= 5


def test_split_caption_short_text_single_cue_timed_by_words() -> None:
    text = "Voltage is proportional to current."
    cues = split_caption(text, words_for(text, start=2.0))
    assert len(cues) == 1
    assert cues[0].start == pytest.approx(2.0)
    assert cues[0].end == pytest.approx(2.0 + 5 * 0.3 - 0.02, abs=1e-3)
    assert cues[0].text == text


def test_split_caption_long_text_respects_two_lines_and_order() -> None:
    cues = split_caption(LONG, words_for(LONG, start=1.0))
    assert len(cues) >= 3
    assert_cue_shape(cues)
    assert " ".join(c.text.replace("\n", " ") for c in cues) == " ".join(LONG.split())
    # cue boundaries prefer sentence ends
    assert any(c.text.rstrip().endswith("points.") for c in cues)


def test_split_caption_closes_small_gaps_between_cues() -> None:
    cues = split_caption(LONG, words_for(LONG, start=0.0))
    for a, b in zip(cues, cues[1:], strict=False):
        assert b.start - a.end < 0.5 + 1e-6


def test_split_caption_word_count_mismatch_uses_proportional_timing() -> None:
    text = "The resistance is 5 ohms"
    words = [TimedWord(text="the", start=1.0, end=1.4), TimedWord(text="resistance", start=1.4, end=2.0),
             TimedWord(text="is", start=2.0, end=2.2), TimedWord(text="five", start=2.2, end=2.6),
             TimedWord(text="ohms", start=2.6, end=3.0), TimedWord(text="extra", start=3.0, end=3.5)]
    cues = split_caption(text, words)
    assert cues[0].start == pytest.approx(1.0)
    assert cues[-1].end == pytest.approx(3.5)
    assert cues[0].text == text  # written form, not the spoken tokens


def test_split_caption_without_words_uses_window_or_estimate() -> None:
    cues = split_caption(LONG, [], start=10.0, end=30.0)
    assert cues[0].start == pytest.approx(10.0)
    assert cues[-1].end == pytest.approx(30.0, abs=1e-3)
    est = split_caption("Hello there", None)
    assert est[0].start == 0.0 and est[-1].end == pytest.approx(len("Hello there") * 0.065, abs=1e-3)
    assert split_caption("   ", []) == []


def test_estimate_words_covers_window() -> None:
    ws = estimate_words("a bb ccc", 1.0, 2.0)
    assert [w.text for w in ws] == ["a", "bb", "ccc"]
    assert ws[0].start == 1.0 and ws[-1].end == pytest.approx(2.0)
    assert all(a.end <= b.start + 1e-9 for a, b in zip(ws, ws[1:], strict=False))
    assert estimate_words("", 0.0) == []


def test_normalize_cues_sorts_clips_and_drops() -> None:
    cues = [CaptionCue(start=5, end=9, text="b"), CaptionCue(start=1, end=6, text="a"),
            CaptionCue(start=9, end=9.01, text="tiny"), CaptionCue(start=-1, end=0.5, text="neg"),
            CaptionCue(start=12, end=13, text="  ")]
    out = normalize_cues(cues)
    assert [c.text for c in out] == ["neg", "a", "b"]
    assert out[0].start == 0.0
    assert out[1].end == 5.0  # clipped to the next start


def test_shift_cues() -> None:
    out = shift_cues([CaptionCue(start=1, end=2, text="x")], 10.25)
    assert (out[0].start, out[0].end) == (11.25, 12.25)


def test_to_srt_format_numbering_and_no_overlap() -> None:
    cues = [CaptionCue(start=0.0004, end=1.5, text="Hello\nworld"), CaptionCue(start=1.4996, end=3725.0, text="Next")]
    srt = to_srt(cues)
    blocks = srt.strip().split("\n\n")
    assert blocks[0].splitlines() == ["1", "00:00:00,000 --> 00:00:01,500", "Hello", "world"]
    assert blocks[1].splitlines()[1] == "00:00:01,500 --> 01:02:05,000"
    assert re.fullmatch(r"(\d+\n\d\d:\d\d:\d\d,\d{3} --> \d\d:\d\d:\d\d,\d{3}\n(.+\n)+\n?)+", srt + "\n")


def test_to_srt_escapes_markup_and_separator() -> None:
    srt = to_srt([CaptionCue(start=0, end=1, text="a <i>b</i> --> c {\\an8} d\n\nsecond")])
    body = srt.splitlines()[2:]
    assert "<i>" not in srt and "{\\an8}" not in srt
    assert "-->" not in body[0]
    assert body == [escape_srt_text("a <i>b</i> --> c {\\an8} d"), "second"]
    assert "a < b" in escape_srt_text("a < b")  # plain comparison stays readable


BS = chr(92)  # backslash
WJ = chr(0x2060)  # word joiner


def test_burn_in_escaping_neutralises_libass_sequences() -> None:
    text = f"Open C:{BS}New folder, set {{1, 2}} and a{BS}hb {{{BS}an8}} x{BS}{BS}y"
    out = escape_burn_in_text(text)
    assert f"{BS}N" not in out and f"{BS}h" not in out and f"{BS}{BS}" not in out
    assert out.count(BS + WJ) == text.count(BS)  # every backslash is followed by the invisible joiner
    assert f"{BS}{{1, 2{BS}}}" in out  # braces escaped: libass shows them instead of hiding the block
    assert out.replace(WJ, "").replace(BS + "{", "{").replace(BS + "}", "}") == escape_srt_text(text)
    cues = [CaptionCue(start=0, end=1, text=text)]
    assert WJ in to_srt(cues, burn_in=True) and WJ not in to_srt(cues)  # downloadable SRT stays literal
    assert to_srt(cues, burn_in=True).splitlines()[:2] == to_srt(cues).splitlines()[:2]


def test_to_vtt_header_escaping_and_times() -> None:
    vtt = to_vtt([CaptionCue(start=61.25, end=62.5, text="R < 5 & V > 2 --> ok")])
    assert vtt.startswith("WEBVTT\n\n")
    assert "00:01:01.250 --> 00:01:02.500" in vtt
    assert "R &lt; 5 &amp; V &gt; 2 --&gt; ok" in vtt
    assert escape_vtt_text("a\n\n\nb") == "a\nb"


def test_serialisers_drop_zero_length_after_rounding() -> None:
    cues = [CaptionCue(start=1.0, end=1.0004, text="x"), CaptionCue(start=2, end=3, text="y")]
    assert to_srt(cues).count("-->") == 1
    assert to_vtt(cues).count("-->") == 1
    assert to_srt([]) == ""
    assert to_vtt([]) == "WEBVTT\n"
