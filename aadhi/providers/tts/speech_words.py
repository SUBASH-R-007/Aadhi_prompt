"""Spoken words for maths symbols per language (en, ta, hi; other languages fall back to en).

``WORDS`` holds single words/phrases, ``TEMPLATES`` the structures whose word order differs between
languages (fractions, powers, roots). ``table(language)`` merges a language over English.
"""

from __future__ import annotations

from functools import lru_cache

WORDS: dict[str, dict[str, str]] = {
    "en": {
        "equals": "equals",
        "plus": "plus",
        "minus": "minus",
        "times": "times",
        "divided_by": "divided by",
        "over": "over",
        "plus_minus": "plus or minus",
        "minus_plus": "minus or plus",
        "approx_equal": "approximately equal to",
        "approximately": "approximately",
        "not_equal": "not equal to",
        "le": "less than or equal to",
        "ge": "greater than or equal to",
        "lt": "less than",
        "gt": "greater than",
        "much_lt": "much less than",
        "much_gt": "much greater than",
        "leads_to": "leads to",
        "tends_to": "tends to",
        "implies": "implies",
        "iff": "if and only if",
        "infinity": "infinity",
        "sum": "sum of",
        "product": "product of",
        "integral": "integral of",
        "op_sum": "sum",
        "op_product": "product",
        "op_integral": "integral",
        "partial": "partial",
        "nabla": "del",
        "proportional": "proportional to",
        "equivalent": "is equivalent to",
        "element_of": "in",
        "not_element_of": "not in",
        "subset": "subset of",
        "union": "union",
        "intersection": "intersection",
        "for_all": "for all",
        "exists": "there exists",
        "angle": "angle",
        "perpendicular": "perpendicular to",
        "parallel": "parallel to",
        "factorial": "factorial",
        "prime": "prime",
        "star": "star",
        "transpose": "transpose",
        "dots": "and so on",
        "degrees": "degrees",
        "degrees_celsius": "degrees Celsius",
        "degrees_fahrenheit": "degrees Fahrenheit",
        "percent": "percent",
        "ohms": "ohms",
        "omega": "omega",
        "and": "and",
        "dollars": "dollars",
        "dollar": "dollar",
        "rupees": "rupees",
        "vector": "vector",
        "hat": "hat",
        "bar": "bar",
        "dot": "dot",
        "prefix_k": "kilo",
        "prefix_M": "mega",
        "prefix_G": "giga",
        "prefix_m": "milli",
        "prefix_u": "micro",
        "prefix_n": "nano",
        "prefix_p": "pico",
        "fn_sin": "sine",
        "fn_cos": "cos",
        "fn_tan": "tan",
        "fn_sec": "sec",
        "fn_csc": "cosec",
        "fn_cot": "cot",
        "fn_arcsin": "arc sine",
        "fn_arccos": "arc cos",
        "fn_arctan": "arc tan",
        "fn_sinh": "hyperbolic sine",
        "fn_cosh": "hyperbolic cos",
        "fn_tanh": "hyperbolic tan",
        "fn_log": "log",
        "fn_ln": "natural log",
        "fn_exp": "exponential",
        "fn_lim": "limit",
        "fn_max": "max",
        "fn_min": "min",
        "fn_det": "determinant",
        "fn_gcd": "g c d",
        "fn_mod": "mod",
    },
    "ta": {
        "equals": "சமம்",
        "plus": "கூட்டல்",
        "minus": "கழித்தல்",
        "times": "பெருக்கல்",
        "divided_by": "வகுத்தல்",
        "over": "வகுத்தல்",
        "plus_minus": "கூட்டல் அல்லது கழித்தல்",
        "minus_plus": "கழித்தல் அல்லது கூட்டல்",
        "approx_equal": "தோராயமாக சமம்",
        "approximately": "தோராயமாக",
        "not_equal": "சமமில்லை",
        "le": "குறைவு அல்லது சமம்",
        "ge": "அதிகம் அல்லது சமம்",
        "lt": "குறைவு",
        "gt": "அதிகம்",
        "leads_to": "ஆகிறது",
        "tends_to": "நோக்கி",
        "implies": "எனவே",
        "infinity": "முடிவிலி",
        "sum": "கூட்டுத்தொகை",
        "integral": "தொகையீடு",
        "op_sum": "கூட்டுத்தொகை",
        "op_integral": "தொகையீடு",
        "proportional": "விகிதசமம்",
        "degrees": "டிகிரி",
        "degrees_celsius": "டிகிரி செல்சியஸ்",
        "degrees_fahrenheit": "டிகிரி ஃபாரன்ஹீட்",
        "percent": "சதவீதம்",
        "ohms": "ஓம்",
        "omega": "ஒமேகா",
        "and": "மற்றும்",
        "rupees": "ரூபாய்",
        "dollars": "டாலர்",
        "dollar": "டாலர்",
        "factorial": "தொடர் பெருக்கம்",
        "vector": "வெக்டர்",
    },
    "hi": {
        "equals": "बराबर",
        "plus": "प्लस",
        "minus": "माइनस",
        "times": "गुणा",
        "divided_by": "भाग",
        "over": "बटा",
        "plus_minus": "प्लस माइनस",
        "minus_plus": "माइनस प्लस",
        "approx_equal": "लगभग बराबर",
        "approximately": "लगभग",
        "not_equal": "बराबर नहीं",
        "le": "से कम या बराबर",
        "ge": "से अधिक या बराबर",
        "lt": "से कम",
        "gt": "से अधिक",
        "leads_to": "देता है",
        "tends_to": "की ओर",
        "implies": "इसलिए",
        "infinity": "अनंत",
        "sum": "योग",
        "integral": "समाकलन",
        "op_sum": "योग",
        "op_integral": "समाकलन",
        "proportional": "समानुपाती",
        "degrees": "डिग्री",
        "degrees_celsius": "डिग्री सेल्सियस",
        "degrees_fahrenheit": "डिग्री फ़ारेनहाइट",
        "percent": "प्रतिशत",
        "ohms": "ओम",
        "omega": "ओमेगा",
        "and": "और",
        "rupees": "रुपये",
        "dollars": "डॉलर",
        "dollar": "डॉलर",
        "factorial": "फैक्टोरियल",
        "vector": "वेक्टर",
    },
}

TEMPLATES: dict[str, dict[str, str]] = {
    "en": {
        "frac": "{num} over {den}",
        "squared": "{base} squared",
        "cubed": "{base} cubed",
        "power": "{base} to the power {exp}",
        "sub": "{base} sub {sub}",
        "sqrt": "square root of {x}",
        "root": "{n}th root of {x}",
        "cbrt": "cube root of {x}",
        "degrees_of": "{base} degrees",
        "big_range": "{op} from {lo} to {hi} of",
        "big_lower": "{op} over {lo} of",
        "lim": "limit as {lo} of",
    },
    "ta": {
        "frac": "{den} இல் {num}",
        "squared": "{base} வர்க்கம்",
        "cubed": "{base} கனம்",
        "power": "{base} அடுக்கு {exp}",
        "sub": "{base} {sub}",
        "sqrt": "{x} இன் வர்க்கமூலம்",
        "root": "{x} இன் {n} ஆம் மூலம்",
        "cbrt": "{x} இன் கனமூலம்",
        "degrees_of": "{base} டிகிரி",
        "big_range": "{lo} முதல் {hi} வரை {op}",
        "big_lower": "{lo} மீது {op}",
        "lim": "{lo} எல்லை",
    },
    "hi": {
        "frac": "{num} बटा {den}",
        "squared": "{base} का वर्ग",
        "cubed": "{base} का घन",
        "power": "{base} की घात {exp}",
        "sub": "{base} {sub}",
        "sqrt": "{x} का वर्गमूल",
        "root": "{x} का {n} वां मूल",
        "cbrt": "{x} का घनमूल",
        "degrees_of": "{base} डिग्री",
        "big_range": "{lo} से {hi} तक {op}",
        "big_lower": "{lo} पर {op}",
        "lim": "{lo} पर सीमा",
    },
}

GREEK: dict[str, dict[str, str]] = {
    "en": {
        "alpha": "alpha", "beta": "beta", "gamma": "gamma", "delta": "delta", "epsilon": "epsilon",
        "varepsilon": "epsilon", "zeta": "zeta", "eta": "eta", "theta": "theta", "vartheta": "theta",
        "iota": "iota", "kappa": "kappa", "lambda": "lambda", "mu": "mu", "nu": "nu", "xi": "xi",
        "omicron": "omicron", "pi": "pi", "varpi": "pi", "rho": "rho", "varrho": "rho", "sigma": "sigma",
        "varsigma": "sigma", "tau": "tau", "upsilon": "upsilon", "phi": "phi", "varphi": "phi", "chi": "chi",
        "psi": "psi", "omega": "omega",
        "Gamma": "gamma", "Delta": "delta", "Theta": "theta", "Lambda": "lambda", "Xi": "xi", "Pi": "pi",
        "Sigma": "sigma", "Upsilon": "upsilon", "Phi": "phi", "Psi": "psi", "Omega": "omega",
    },
    "ta": {
        "alpha": "ஆல்ஃபா", "beta": "பீட்டா", "gamma": "காமா", "delta": "டெல்டா", "theta": "தீட்டா",
        "lambda": "லாம்டா", "mu": "மியூ", "pi": "பை", "sigma": "சிக்மா", "omega": "ஒமேகா",
        "epsilon": "எப்சிலான்", "rho": "ரோ", "tau": "டௌ", "phi": "ஃபை",
    },
    "hi": {
        "alpha": "अल्फा", "beta": "बीटा", "gamma": "गामा", "delta": "डेल्टा", "theta": "थीटा",
        "lambda": "लैम्ब्डा", "mu": "म्यू", "pi": "पाई", "sigma": "सिग्मा", "omega": "ओमेगा",
        "epsilon": "एप्सिलॉन", "rho": "रो", "tau": "टाउ", "phi": "फाई",
    },
}

UNICODE_GREEK: dict[str, str] = {
    "α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon", "ϵ": "epsilon", "ζ": "zeta",
    "η": "eta", "θ": "theta", "ϑ": "theta", "ι": "iota", "κ": "kappa", "λ": "lambda", "μ": "mu", "µ": "mu",
    "ν": "nu", "ξ": "xi", "π": "pi", "ρ": "rho", "σ": "sigma", "ς": "sigma", "τ": "tau", "υ": "upsilon",
    "φ": "phi", "ϕ": "phi", "χ": "chi", "ψ": "psi", "ω": "omega",
    "Γ": "Gamma", "Δ": "Delta", "∆": "Delta", "Θ": "Theta", "Λ": "Lambda", "Ξ": "Xi", "Π": "Pi",
    "Σ": "Sigma", "Φ": "Phi", "Ψ": "Psi", "Ω": "Omega", "\u2126": "Omega",
}


#: SI prefix character -> ``WORDS`` key (``k`` -> "kilo"), used before units such as ohms.
PREFIX_KEYS: dict[str, str] = {
    "k": "prefix_k", "K": "prefix_k", "M": "prefix_M", "G": "prefix_G", "m": "prefix_m", "µ": "prefix_u",
    "μ": "prefix_u", "n": "prefix_n", "p": "prefix_p",
}


def lang_key(language: str | None) -> str:
    """Primary language subtag with a table (``ta-IN`` -> ``ta``); unknown -> ``en``."""
    primary = (language or "en").replace("_", "-").split("-")[0].lower()
    return primary if primary in WORDS else "en"


@lru_cache(maxsize=16)
def table(language: str | None) -> dict[str, str]:
    """Words for ``language`` merged over English."""
    key = lang_key(language)
    return {**WORDS["en"], **WORDS.get(key, {})}


@lru_cache(maxsize=16)
def templates(language: str | None) -> dict[str, str]:
    key = lang_key(language)
    return {**TEMPLATES["en"], **TEMPLATES.get(key, {})}


@lru_cache(maxsize=16)
def greek(language: str | None) -> dict[str, str]:
    key = lang_key(language)
    base = dict(GREEK["en"])
    for name, word in GREEK.get(key, {}).items():
        base[name] = word
        cap = name[:1].upper() + name[1:]
        if cap in base:
            base[cap] = word
    return base
