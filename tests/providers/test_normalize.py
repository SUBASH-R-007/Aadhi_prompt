"""tts.normalize / tts.mathspeech: speech text normalisation and the pronunciation lexicon."""

from __future__ import annotations

import pytest

from aadhi.providers.tts.mathspeech import latex_to_speech
from aadhi.providers.tts.normalize import apply_lexicon, normalize_for_speech
from aadhi.providers.tts.speech_words import lang_key, table
from aadhi.schemas.screenplay import LexiconEntry


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Ohm's law: $V = IR$ where **V** is voltage.", "Ohm's law: V equals I R where V is voltage."),
        (r"The area is $\pi r^2$.", "The area is pi r squared."),
        (r"Volume $\frac{4}{3}\pi r^3$", "Volume 4 over 3 pi r cubed"),
        (r"Use $x_i$ here", "Use x sub i here"),
        (r"$\sqrt{b^2 - 4ac}$", "square root of b squared minus 4 a c"),
        (r"$e^{-x}$", "e to the power minus x"),
        (r"$x^n$", "x to the power n"),
        (r"$A^T$", "A transpose"),
        (r"$90^\circ$", "90 degrees"),
        (r"$\sqrt[3]{8}$ and $\sqrt[4]{x}$", "cube root of 8 and 4th root of x"),
        (r"$\sigma \le \alpha$", "sigma less than or equal to alpha"),
        (r"$a \ge b$, $a \neq b$, $a \approx b$", "a greater than or equal to b, a not equal to b, a approximately equal to b"),
        (r"$F = m \cdot a$ and $2 \times 3$", "F equals m times a and 2 times 3"),
        (r"$\Delta V = 5\Omega$", "delta V equals 5 ohms"),
        (r"$\lim_{x \to 0} \frac{\sin x}{x} = 1$", "limit as x tends to 0 of sine x over x equals 1"),
        (r"$\sum_{i=1}^{n} i$", "sum from i equals 1 to n of i"),
        (r"$\int_0^1 x\,dx$", "integral from 0 to 1 of x d x"),
        (r"$\infty$ and $\pm 1$", "infinity and plus or minus 1"),
        (r"$\text{speed} = \frac{d}{t}$", "speed equals d over t"),
        (r"\$5 costs $x$", "5 dollars costs x"),
        # engineering notation (review findings)
        (r"$V_{out} = V_{in} \frac{R_2}{R_1 + R_2}$", "V sub out equals V sub in R sub 2 over R sub 1 plus R sub 2"),
        (r"$P_{max}$ and $I_{DS}$ and $R_{th}$", "P sub max and I sub DS and R sub th"),
        (r"$a_{ij}$, $T_{ijk}$, $V_{rms}$", "a sub i j, T sub i j k, V sub r m s"),
        (r"$R = 4.7\,\text{k}\Omega$", "R equals 4.7 kilo ohms"),
        (r"$2.2\,k\Omega$ and $1\,M\Omega$ and $5 m\Omega$", "2.2 kilo ohms and 1 mega ohms and 5 milli ohms"),
        (r"$4.7\,\mathrm{k}\Omega$ and $10~\Omega$", "4.7 kilo ohms and 10 ohms"),
        (r"$3\,\mu\Omega$", "3 micro ohms"),
        (r"$C = 5\,\mu F$", "C equals 5 micro F"),
        (r"$10^3 \Omega$", "10 cubed ohms"),
        (r"$\Omega$ and $\mu$ alone", "omega and mu alone"),
        (r"$10 k$ stays", "10 k stays"),
        ("$4.7\\,k\u2126$ ohm sign", "4.7 kilo ohms ohm sign"),
    ],
)
def test_maths_en(text: str, expected: str) -> None:
    assert normalize_for_speech(text, "en-IN") == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("A 10 kΩ resistor and a 5Ω one", "A 10 kilo ohms resistor and a 5 ohms one"),
        ("3 µΩ and Ω alone", "3 micro ohms and omega alone"),
        ("At 25°C and 98.6 °F, angle 45°", "At 25 degrees Celsius and 98.6 degrees Fahrenheit, angle 45 degrees"),
        ("50% duty", "50 percent duty"),
        ("E = mc^2", "E equals m c squared"),
        ("x² + y³ = z", "x squared plus y cubed equals z"),
        ("H₂O", "H 2 O"),
        ("x ≈ 3 ± 0.1", "x approximately equal to 3 plus or minus 0.1"),
        ("Δx → 0", "delta x leads to 0"),
        ("a ≤ b ≥ c ≠ d", "a less than or equal to b greater than or equal to c not equal to d"),
        ("5 × 3 ÷ 2", "5 times 3 divided by 2"),
        ("θ = 30°", "theta equals 30 degrees"),
        ("C++ and -5 and 3 - 2", "C plus plus and minus 5 and 3 minus 2"),
        ("first-year students, pages 10-20", "first-year students, pages 10-20"),
        ("2*3 and a * b", "2 times 3 and a times b"),
        ("₹200, ₹1,00,000.50 total", "200 rupees, 1,00,000.50 rupees total"),
        ("~30 V", "approximately 30 V"),
        ("salt & water", "salt and water"),
        ("x <= y and y >= z", "x less than or equal to y and y greater than or equal to z"),
        (r"raw \frac{a}{b} and \alpha", "raw a over b and alpha"),
        ("V_1 and V_{out}", "V sub 1 and V sub out"),
        ("V_in and a_{ij}", "V sub in and a sub i j"),
        (r"R = 10\Omega", "R equals 10 ohms"),
        (r"R = 2.2\,\text{k}\Omega and 4.7\,k\Omega", "R equals 2.2 kilo ohms and 4.7 kilo ohms"),
        (r"3\,\mu\Omega, C = 5\,\mu F", "3 micro ohms, C equals 5 micro F"),
        (r"the \Omega symbol", "the omega symbol"),
        ("2 \u00b5F and 3 \u03bcH", "2 micro F and 3 micro H"),
        ("10 k\u2126 and 5 \u03a9", "10 kilo ohms and 5 ohms"),
        ("snake_case stays", "snake_case stays"),
        ("√2 and √(x+1)", "square root of 2 and square root of x plus 1"),
    ],
)
def test_symbols_en(text: str, expected: str) -> None:
    assert normalize_for_speech(text, "en-US") == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("The term [[impedance]] is *important*.", "The term impedance is important."),
        ("Run `print(x)` now", "Run print(x) now"),
        ("**Bold** and *italic* and ***both***", "Bold and italic and both"),
        ("## Heading\nBody   text", "Heading Body text"),
        ("A literal \\* star", "A literal star star"),
        ("", ""),
        ("  spaced   out  ,  text  ", "spaced out, text"),
    ],
)
def test_markup(text: str, expected: str) -> None:
    assert normalize_for_speech(text, "en-IN") == expected


def test_tamil_and_hindi_tables() -> None:
    ta = normalize_for_speech(r"$V = IR$, $x^2$, $\frac{a}{b}$, 50% at 25°C", "ta-IN")
    assert ta == "V சமம் I R, x வர்க்கம், b இல் a, 50 சதவீதம் at 25 டிகிரி செல்சியஸ்"
    hi = normalize_for_speech(r"$V = IR$, $x^2$, $\frac{a}{b}$, 50% at 25°C", "hi-IN")
    assert hi == "V बराबर I R, x का वर्ग, a बटा b, 50 प्रतिशत at 25 डिग्री सेल्सियस"
    assert normalize_for_speech(r"$\alpha + \pi$", "hi-IN") == "अल्फा प्लस पाई"
    assert normalize_for_speech(r"$\sqrt{x}$", "ta-IN") == "x இன் வர்க்கமூலம்"


def test_other_languages_fall_back_to_english() -> None:
    assert normalize_for_speech("$V = IR$ 50%", "te-IN") == "V equals I R 50 percent"
    assert normalize_for_speech("$x^2$", "kn-IN") == "x squared"
    assert lang_key("ml-IN") == "en" and lang_key("ta") == "ta" and lang_key(None) == "en"
    assert table("ta-IN")["sum"] != table("en")["sum"]
    assert table("ta-IN")["nabla"] == table("en")["nabla"]  # missing entries fall back


def test_latex_reader_is_robust() -> None:
    for junk in (r"\frac{", r"x^", r"\sqrt[3", "}}}{{{", r"\unknowncmd{x}", "$$", r"\left( x \right)"):
        assert isinstance(latex_to_speech(junk), str)
    assert latex_to_speech(r"\unknowncmd") == "unknowncmd"
    assert latex_to_speech(r"\left( x + 1 \right)") == "x plus 1"
    assert latex_to_speech(r"\vec{F} = m\vec{a}") == "vector F equals m vector a"
    assert latex_to_speech(r"50\%") == "50 percent"


@pytest.mark.parametrize(
    "text",
    ["$" + "{" * 400 + "$", "$" + "x^{" * 200 + "$", "$" + "{" * 5000 + "x" + "}" * 5000 + "$",
     "$" + "\\sqrt" * 3000 + "x$", "$" + "\\frac{" * 500 + "$"],
)
def test_deep_nesting_never_raises(text: str) -> None:
    out = normalize_for_speech(text, "en-IN")
    assert isinstance(out, str) and "{" not in out and "\\" not in out


def test_moderate_nesting_keeps_structure_and_is_fast() -> None:
    import time

    assert latex_to_speech("x^{y^{z}}") == "x to the power y to the power z"
    nested = "x^{" * 25 + "2" + "}" * 25  # was exponential (re-parsed every level for ^2 detection)
    start = time.perf_counter()
    out = latex_to_speech(nested)
    assert time.perf_counter() - start < 1.0
    assert out.startswith("x to the power x to the power") and out.endswith("x squared")


def test_latex_to_speech_falls_back_on_parser_errors(monkeypatch) -> None:
    from aadhi.providers.tts import mathspeech

    def boom(self, stop=None):
        raise ValueError("parser bug")

    monkeypatch.setattr(mathspeech._Reader, "seq", boom)
    assert latex_to_speech(r"\frac{a}{b} + x_1") == "frac a b + x 1"


def test_subscript_text_rules() -> None:
    from aadhi.providers.tts.mathspeech import subscript_text

    assert [subscript_text(x) for x in ("out", "max", "in", "CE", "eq", "load")] == ["out", "max", "in", "CE", "eq",
                                                                                   "load"]
    assert [subscript_text(x) for x in ("ij", "ab", "ijk", "rms", "x")] == ["i j", "a b", "i j k", "r m s", "x"]


# --- lexicon ---------------------------------------------------------------------------------


LEXICON = [
    LexiconEntry(written="NumPy", spoken="num pie"),
    LexiconEntry(written="SQL", spoken="sequel"),
    LexiconEntry(written="MySQL", spoken="my sequel"),
    LexiconEntry(written="C++", spoken="C plus plus"),
    LexiconEntry(written="Ohm's law", spoken="Ohms law"),
    LexiconEntry(written="தமிழ்", spoken="தமிழ் மொழி", language="ta"),
    LexiconEntry(written="data", spoken="day-ta", language="en-US"),
]


def test_lexicon_whole_word_case_insensitive_longest_first() -> None:
    out = apply_lexicon("numpy and SQL with MySQL; sqlite stays; c++ code; Ohm's  law", LEXICON, "en-IN")
    assert out == "num pie and sequel with my sequel; sqlite stays; C plus plus code; Ohms law"


def test_lexicon_language_filter() -> None:
    text = "தமிழ் தமிழ்நாடு data"
    assert apply_lexicon(text, LEXICON, "en-IN") == text
    assert apply_lexicon(text, LEXICON, "en-US") == "தமிழ் தமிழ்நாடு day-ta"
    assert apply_lexicon(text, LEXICON, "ta-IN") == "தமிழ் மொழி தமிழ்நாடு data"


def test_lexicon_single_pass_and_dicts() -> None:
    lex = [{"written": "AI", "spoken": "A I"}, {"written": "A I", "spoken": "should not chain"}]
    assert apply_lexicon("AI is here", lex, "en-IN") == "A I is here"
    assert apply_lexicon("", lex, "en-IN") == ""
    assert apply_lexicon("nothing", [], "en-IN") == "nothing"


def test_lexicon_then_normalize_pipeline() -> None:
    text = apply_lexicon("In NumPy, $y = x^2$", LEXICON, "en-IN")
    assert normalize_for_speech(text, "en-IN") == "In num pie, y equals x squared"


@pytest.mark.parametrize("language", ["en-IN", "ta-IN", "hi-IN"])
def test_normalisation_is_idempotent(language: str) -> None:
    samples = [
        r"Ohm's law: $V = IR$, so at 25°C a 10 kΩ resistor carries ~3 mA (±5%).",
        r"$\frac{dV}{dt} = -\frac{V}{RC}$ and x² → ∞",
        "Plain sentence without any maths.",
    ]
    for text in samples:
        once = normalize_for_speech(text, language)
        assert normalize_for_speech(once, language) == once
