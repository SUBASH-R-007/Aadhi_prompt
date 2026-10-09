"""Voice catalogue per TTS provider and language.

Languages: en-IN, en-US, en-GB, ta-IN, hi-IN, te-IN, kn-IN, ml-IN (``aadhi.pipeline.base.SUPPORTED_LANGUAGES``).
Edge voices are language-specific; OpenAI, Gemini and ElevenLabs voices are multilingual (they read
whatever language the text is in), so they are listed for every language.
"""

from __future__ import annotations

from typing import TypedDict

LANGUAGES = ("en-IN", "en-US", "en-GB", "ta-IN", "hi-IN", "te-IN", "kn-IN", "ml-IN")
MULTI = "multi"


class Voice(TypedDict):
    id: str
    label: str
    language: str
    gender: str


def _v(vid: str, label: str, language: str, gender: str) -> Voice:
    return {"id": vid, "label": label, "language": language, "gender": gender}


EDGE_VOICES: tuple[Voice, ...] = (
    _v("en-IN-NeerjaNeural", "Neerja", "en-IN", "Female"),
    _v("en-IN-PrabhatNeural", "Prabhat", "en-IN", "Male"),
    _v("en-IN-NeerjaExpressiveNeural", "Neerja (expressive)", "en-IN", "Female"),
    _v("en-US-AndrewMultilingualNeural", "Andrew (multilingual)", "en-US", "Male"),
    _v("en-US-AvaMultilingualNeural", "Ava (multilingual)", "en-US", "Female"),
    _v("en-US-EmmaMultilingualNeural", "Emma (multilingual)", "en-US", "Female"),
    _v("en-US-BrianMultilingualNeural", "Brian (multilingual)", "en-US", "Male"),
    _v("en-US-JennyNeural", "Jenny", "en-US", "Female"),
    _v("en-US-GuyNeural", "Guy", "en-US", "Male"),
    _v("en-US-AriaNeural", "Aria", "en-US", "Female"),
    _v("en-GB-RyanNeural", "Ryan", "en-GB", "Male"),
    _v("en-GB-SoniaNeural", "Sonia", "en-GB", "Female"),
    _v("en-GB-LibbyNeural", "Libby", "en-GB", "Female"),
    _v("en-GB-ThomasNeural", "Thomas", "en-GB", "Male"),
    _v("ta-IN-PallaviNeural", "Pallavi", "ta-IN", "Female"),
    _v("ta-IN-ValluvarNeural", "Valluvar", "ta-IN", "Male"),
    _v("hi-IN-SwaraNeural", "Swara", "hi-IN", "Female"),
    _v("hi-IN-MadhurNeural", "Madhur", "hi-IN", "Male"),
    _v("te-IN-ShrutiNeural", "Shruti", "te-IN", "Female"),
    _v("te-IN-MohanNeural", "Mohan", "te-IN", "Male"),
    _v("kn-IN-SapnaNeural", "Sapna", "kn-IN", "Female"),
    _v("kn-IN-GaganNeural", "Gagan", "kn-IN", "Male"),
    _v("ml-IN-SobhanaNeural", "Sobhana", "ml-IN", "Female"),
    _v("ml-IN-MidhunNeural", "Midhun", "ml-IN", "Male"),
)

OPENAI_VOICES: tuple[Voice, ...] = (
    _v("nova", "Nova", MULTI, "Female"),
    _v("alloy", "Alloy", MULTI, "Neutral"),
    _v("ash", "Ash", MULTI, "Male"),
    _v("coral", "Coral", MULTI, "Female"),
    _v("echo", "Echo", MULTI, "Male"),
    _v("fable", "Fable", MULTI, "Male"),
    _v("onyx", "Onyx", MULTI, "Male"),
    _v("sage", "Sage", MULTI, "Female"),
    _v("shimmer", "Shimmer", MULTI, "Female"),
)

GEMINI_VOICES: tuple[Voice, ...] = (
    _v("Kore", "Kore (firm)", MULTI, "Female"),
    _v("Charon", "Charon (informative)", MULTI, "Male"),
    _v("Puck", "Puck (upbeat)", MULTI, "Male"),
    _v("Zephyr", "Zephyr (bright)", MULTI, "Female"),
    _v("Aoede", "Aoede (breezy)", MULTI, "Female"),
    _v("Leda", "Leda (youthful)", MULTI, "Female"),
    _v("Orus", "Orus (firm)", MULTI, "Male"),
    _v("Fenrir", "Fenrir (excitable)", MULTI, "Male"),
    _v("Iapetus", "Iapetus (clear)", MULTI, "Male"),
    _v("Erinome", "Erinome (clear)", MULTI, "Female"),
    _v("Sadaltager", "Sadaltager (knowledgeable)", MULTI, "Male"),
    _v("Sulafat", "Sulafat (warm)", MULTI, "Female"),
    _v("Achird", "Achird (friendly)", MULTI, "Male"),
    _v("Despina", "Despina (smooth)", MULTI, "Female"),
)

ELEVENLABS_VOICES: tuple[Voice, ...] = (
    _v("21m00Tcm4TlvDq8ikWAM", "Rachel", MULTI, "Female"),
    _v("pNInz6obpgDQGcFmaJgB", "Adam", MULTI, "Male"),
    _v("EXAVITQu4vr4xnSDxMaL", "Sarah", MULTI, "Female"),
    _v("JBFqnCBsd6RMkjVDRZzb", "George", MULTI, "Male"),
    _v("onwK4e9ZLuTAKqWW03F9", "Daniel", MULTI, "Male"),
    _v("pFZP5JQG7iQjIQuC4Bku", "Lily", MULTI, "Female"),
    _v("XrExE9yKIg1WjnnlVkGX", "Matilda", MULTI, "Female"),
    _v("nPczCjzI2devNBz1zQrb", "Brian", MULTI, "Male"),
    _v("TX3LPaxmHKxFdv7VOQHJ", "Liam", MULTI, "Male"),
    _v("FGY2WhTYpPnrIDTdsKH5", "Laura", MULTI, "Female"),
)

FAKE_VOICES: tuple[Voice, ...] = (
    _v("fake-female", "Test voice (female)", MULTI, "Female"),
    _v("fake-male", "Test voice (male)", MULTI, "Male"),
)

CATALOGUE: dict[str, tuple[Voice, ...]] = {
    "edge": EDGE_VOICES,
    "openai": OPENAI_VOICES,
    "gemini": GEMINI_VOICES,
    "elevenlabs": ELEVENLABS_VOICES,
    "fake": FAKE_VOICES,
}

EDGE_DEFAULTS: dict[str, str] = {
    "en-IN": "en-IN-NeerjaNeural",
    "en-US": "en-US-AndrewMultilingualNeural",
    "en-GB": "en-GB-RyanNeural",
    "ta-IN": "ta-IN-PallaviNeural",
    "hi-IN": "hi-IN-SwaraNeural",
    "te-IN": "te-IN-ShrutiNeural",
    "kn-IN": "kn-IN-SapnaNeural",
    "ml-IN": "ml-IN-SobhanaNeural",
}
EDGE_FALLBACK = "en-US-AndrewMultilingualNeural"  # multilingual: reads most languages acceptably
MULTILINGUAL_DEFAULTS: dict[str, str] = {
    "openai": "nova",
    "gemini": "Kore",
    "elevenlabs": "21m00Tcm4TlvDq8ikWAM",
    "fake": "fake-female",
}


def _normalise_language(language: str | None) -> str:
    """``ta`` -> ``ta-IN``; ``en_in`` -> ``en-IN``; unknown values are returned cleaned."""
    if not language:
        return ""
    raw = language.strip().replace("_", "-")
    parts = raw.split("-")
    tag = parts[0].lower() + ("-" + parts[1].upper() if len(parts) > 1 and parts[1] else "")
    if tag in LANGUAGES:
        return tag
    primary = parts[0].lower()
    if len(parts) == 1:
        for lang in LANGUAGES:
            if lang.split("-")[0] == primary:
                return lang
    return tag


def default_voice(provider: str, language: str) -> str:
    """Default voice id for ``provider`` speaking ``language`` (BCP-47)."""
    provider = (provider or "").lower()
    if provider == "edge":
        lang = _normalise_language(language)
        if lang in EDGE_DEFAULTS:
            return EDGE_DEFAULTS[lang]
        primary = lang.split("-")[0]
        if primary == "en":
            return EDGE_DEFAULTS["en-US"]
        return EDGE_FALLBACK
    if provider in MULTILINGUAL_DEFAULTS:
        return MULTILINGUAL_DEFAULTS[provider]
    raise KeyError(f"unknown TTS provider {provider!r}")


def voices_for(provider: str, language: str | None = None) -> list[dict[str, str]]:
    """``[{id, label, language, gender}]`` for the UI.

    Edge voices are filtered by ``language`` (all voices when ``None``). Multilingual providers
    return every voice, labelled with the requested ``language`` (or ``"multi"``).
    """
    provider = (provider or "").lower()
    if provider not in CATALOGUE:
        raise KeyError(f"unknown TTS provider {provider!r}")
    voices = CATALOGUE[provider]
    if provider == "edge":
        if language is None:
            return [dict(v) for v in voices]
        lang = _normalise_language(language)
        return [dict(v) for v in voices if v["language"] == lang]
    label_lang = _normalise_language(language) if language else MULTI
    return [{**v, "language": label_lang} for v in voices]


def is_known_voice(provider: str, voice: str) -> bool:
    return any(v["id"] == voice for v in CATALOGUE.get((provider or "").lower(), ()))
