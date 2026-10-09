"""Targeted clean-up of extracted text: ligatures, invisible / non-breaking spaces and mojibake.

PDF and DOCX extraction hands over compatibility characters that read badly in a prompt and on a
board: the ``ﬁ`` in "veriﬁcation" (U+FB01), non-breaking and zero-width spaces, and text that was
saved as UTF-8 but read as Windows-1252 somewhere on its way ("â€™" for "’", "â€“" for "–").

Only these known characters and sequences are replaced. Full Unicode NFKC normalisation is never
applied: it would also turn "x²" into "x2", "½" into "1/2" and "Ω" (ohm sign) into other code
points, which changes the subject matter. Zero-width joiners and non-joiners (U+200D, U+200C) are
kept because Indic scripts (Tamil, Hindi, Malayalam ...) need them to render correctly.
"""

from __future__ import annotations

import re

LIGATURES = {
    "ﬀ": "ff",
    "ﬁ": "fi",
    "ﬂ": "fl",
    "ﬃ": "ffi",
    "ﬄ": "ffl",
    "ﬅ": "st",  # long s + t
    "ﬆ": "st",
}
# non-breaking and fixed-width spaces -> a normal space
SPACES = {c: " " for c in (" ", " ", " ", " ", "　", *map(chr, range(0x2000, 0x200B)))}
# invisible characters that only break words apart (soft hyphen, zero-width space, word joiner, BOM)
INVISIBLE = {c: "" for c in ("­", "​", "⁠", "﻿")}
# UTF-8 read as Windows-1252: the sequences that turn up in uploaded notes (longest first when applied)
MOJIBAKE = {
    "â€“": "–", "â€”": "—", "â€’": "‒", "â€•": "―", "â€˜": "‘", "â€™": "’", "â€š": "‚", "â€œ": "“",
    "â€\u009d": "”", "â€�": "”", "â€ž": "„", "â€¢": "•", "â€¦": "…", "â€²": "′", "â€³": "″", "â€°": "‰",
    "â„¢": "™", "âˆ’": "−", "Ã—": "×", "Ã·": "÷", "Â°": "°", "Â±": "±", "Â·": "·", "Âµ": "µ", "Â²": "²",
    "Â³": "³", "Â¹": "¹", "Â½": "½", "Â¼": "¼", "Â¾": "¾", "Â©": "©", "Â®": "®", "Â§": "§", "Â«": "«", "Â»": "»",
    "Â ": " ", "Ã©": "é", "Ã¨": "è", "Ã¶": "ö", "Ã¼": "ü", "Ã¤": "ä", "Ã±": "ñ", "Ã§": "ç", "Ã¡": "á",
    "Ã³": "ó", "Ãº": "ú", "Ã­": "í",
    # a byte Windows-1252 leaves undefined, lost as U+FFFD: the likeliest character in a technical source
    "Ï�": "ρ", "â�»": "⁻",
}
_MOJIBAKE_RE = re.compile("|".join(re.escape(k) for k in sorted(MOJIBAKE, key=len, reverse=True)))
_BARE_RIGHT_QUOTE = re.compile("â€(?![\u0080-¿€‚ƒ„…†‡ˆ‰Š‹ŒŽ‘’“”•–—˜™š›œžŸ])")  # its last byte was lost
_TABLE = str.maketrans({**LIGATURES, **SPACES, **INVISIBLE})


def _cp1252_bytes() -> dict[str, int]:
    """Character -> byte for text decoded as Windows-1252 (the five undefined bytes stay C1 controls, as
    lenient readers leave them; any C1 control maps back to its byte)."""
    out: dict[str, int] = {}
    for b in range(0x80, 0x100):
        try:
            out[bytes([b]).decode("cp1252")] = b
        except UnicodeDecodeError:
            out[chr(b)] = b
    for b in range(0x80, 0xA0):
        out.setdefault(chr(b), b)
    return out


_BYTE_OF = _cp1252_bytes()


def _byte_class(lo: int, hi: int) -> str:
    return "[" + "".join(re.escape(c) for c, b in _BYTE_OF.items() if lo <= b <= hi) + "]"


_CONT = _byte_class(0x80, 0xBF)
# one UTF-8 sequence (2, 3 or 4 bytes) read as Windows-1252: "Î©" for "Ω", "Ïƒ" for "σ", "â‰¤" for "≤", "â\x81»" for "⁻"
_UTF8_AS_CP1252 = re.compile(
    f"{_byte_class(0xC2, 0xDF)}{_CONT}|{_byte_class(0xE0, 0xEF)}{_CONT}{{2}}|{_byte_class(0xF0, 0xF4)}{_CONT}{{3}}")
_LEADS = frozenset(c for c, b in _BYTE_OF.items() if 0xC2 <= b <= 0xF4)


def _redecode(m: re.Match[str]) -> str:
    """The character a mojibake sequence stands for, when its bytes are valid UTF-8 (else the text as it was)."""
    run = m.group(0)
    try:
        return bytes(_BYTE_OF[c] for c in run).decode("utf-8")
    except (KeyError, UnicodeDecodeError):
        return run


def normalize_extracted_text(text: str) -> str:
    """``text`` with ligatures spelled out, odd spaces made normal, invisible characters removed and
    mojibake repaired. Everything else (superscripts, symbols, Greek letters) is untouched.

    Mojibake is repaired in two passes: every complete UTF-8 sequence that was read as Windows-1252
    ("Î©" -> "Ω", "Ïƒ" -> "σ", "â‰¤" -> "≤", "âˆš" -> "√", "â\\x81»Â³" -> "⁻³") is decoded back when its
    bytes are valid UTF-8 (the ftfy approach); then the table fixes sequences that lost a byte."""
    if not text:
        return text or ""
    if any(c in _LEADS for c in text):
        text = _UTF8_AS_CP1252.sub(_redecode, text)
    if "â" in text or "Ã" in text or "Â" in text:
        text = _MOJIBAKE_RE.sub(lambda m: MOJIBAKE[m.group(0)], text)
        text = _BARE_RIGHT_QUOTE.sub("”", text)
    return text.translate(_TABLE)
