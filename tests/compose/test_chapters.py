"""YouTube chapters and FFMETADATA chapters."""

from __future__ import annotations

from aadhi.compose.chapters import (
    clean_title,
    format_timestamp,
    opening_title,
    to_ffmetadata,
    youtube_chapter_list,
    youtube_chapters,
)
from aadhi.schemas.timeline import Chapter, Timeline


def tl_with(chapters: list[Chapter], total: float) -> Timeline:
    return Timeline(intro=None, scenes=[], total_duration=0.0, chapters=chapters).model_copy(
        update={"total_duration": total})


def test_format_timestamp() -> None:
    assert format_timestamp(0) == "00:00"
    assert format_timestamp(59.999) == "00:59"
    assert format_timestamp(61.2) == "01:01"
    assert format_timestamp(3725) == "1:02:05"
    assert format_timestamp(5, force_hours=True) == "0:00:05"


def test_clean_title() -> None:
    assert clean_title("  Ohm's\n law  ") == "Ohm's law"
    assert clean_title("") == "Chapter"
    assert len(clean_title("x" * 500)) == 100


def test_youtube_rules_first_zero_min_gap_unique() -> None:
    chs = [Chapter(start=11.5, title="Basics"), Chapter(start=15.0, title="Too close"),
           Chapter(start=40.2, title="Ohm's law"), Chapter(start=40.9, title="dup second"),
           Chapter(start=95.0, title="Too late")]
    out = youtube_chapter_list(chs, total_duration=100.0)
    assert [c.title for c in out] == ["Introduction", "Basics", "Ohm's law"]
    assert out[0].start == 0
    starts = [c.start for c in out]
    assert starts == sorted(set(starts))
    assert all(b - a >= 10 for a, b in zip(starts, starts[1:], strict=False))


def test_youtube_real_chapter_near_zero_names_the_first() -> None:
    out = youtube_chapter_list([Chapter(start=3, title="Welcome"), Chapter(start=30, title="Part 2")], 120)
    assert [(c.start, c.title) for c in out] == [(0.0, "Welcome"), (30.0, "Part 2")]


def test_youtube_chapters_text() -> None:
    tl = tl_with([Chapter(start=0, title="Introduction"), Chapter(start=75.4, title="Ohm's   law")], 200)
    assert youtube_chapters(tl) == "00:00 Introduction\n01:15 Ohm's law"
    long_tl = tl_with([Chapter(start=0, title="A"), Chapter(start=3700, title="B")], 4000)
    assert youtube_chapters(long_tl) == "0:00:00 A\n1:01:40 B"
    assert youtube_chapters(tl_with([], 50)) == "00:00 Introduction"


def test_ffmetadata_escaping_and_ranges() -> None:
    text = to_ffmetadata([Chapter(start=0, title="Intro; =#\\"), Chapter(start=12.5, title="Next\nline")], 30.0,
                         title="Ohm's law = V/I", extra={"artist": "REC", "Bad Key": "x"})
    lines = text.splitlines()
    assert lines[0] == ";FFMETADATA1"
    assert "title=Ohm's law \\= V/I" in lines
    assert "artist=REC" in lines and not any(ln.startswith("Bad") for ln in lines)
    assert "title=Intro\\; \\=\\#\\\\" in lines
    assert lines.count("[CHAPTER]") == 2
    assert "START=12500" in lines and "END=30000" in lines and "END=12500" in lines
    assert "title=Next line" in lines


def test_ffmetadata_skips_empty_ranges() -> None:
    text = to_ffmetadata([Chapter(start=0, title="A"), Chapter(start=10, title="B")], 10.0)
    assert text.count("[CHAPTER]") == 1


def test_opening_title_picks_the_first_free_name() -> None:
    assert opening_title([]) == "Introduction"
    assert opening_title(["Basics", "Ohm's law"]) == "Introduction"
    assert opening_title([" introduction "]) == "Opening"
    assert opening_title(["INTRODUCTION", "opening"]) == "Lesson start"
    assert opening_title(["Introduction", "Opening", "Lesson  Start", "Opening 2"]) == "Opening 3"


def test_youtube_synthetic_opening_never_duplicates_a_real_introduction() -> None:
    out = youtube_chapter_list([Chapter(start=11.5, title="Introduction"), Chapter(start=45, title="Ohm's law")], 120)
    assert [(c.start, c.title) for c in out] == [(0.0, "Opening"), (11.0, "Introduction"), (45.0, "Ohm's law")]
    padded = youtube_chapter_list([Chapter(start=30, title="Basics"), Chapter(start=60, title="  introduction ")], 120)
    assert [c.title for c in padded] == ["Opening", "Basics", "introduction"]
    # a real chapter right after 0:00 still names the first chapter (no synthetic one at all)
    near = youtube_chapter_list([Chapter(start=3, title="Introduction"), Chapter(start=40, title="Next")], 120)
    assert [(c.start, c.title) for c in near] == [(0.0, "Introduction"), (40.0, "Next")]


def test_youtube_renames_a_stored_duplicate_opening() -> None:
    """Timelines built before the rule carry 'Introduction' at 0:00 AND a real 'Introduction' later."""
    stored = [Chapter(start=0, title="Introduction"), Chapter(start=11.5, title="Introduction"),
              Chapter(start=50, title="Opening")]
    out = youtube_chapter_list(stored, 200)
    assert [c.title for c in out] == ["Lesson start", "Introduction", "Opening"]
    # no duplicate: unchanged
    assert [c.title for c in youtube_chapter_list([Chapter(start=0, title="Introduction"),
                                                   Chapter(start=20, title="Basics")], 100)] == \
        ["Introduction", "Basics"]
