"""Phase 17 — the professional video styling system.

A lesson's look is a versioned style family (Academic, Cinematic Education, Children's Education, Corporate Training)
expressed as structured semantic tokens: colour roles, typography, spacing, shapes, surfaces, background treatment,
motion, transition and camera preferences, presenter / visual / caption frames, and accessibility rules. The lesson's
choice (with a few bounded overrides) is resolved ONCE per scene into an effective style that travels with the scene's
cinematic plan (`plan.style.look`), so the page that previews the lesson and the page that records the export read the
same values. Style decides how a scene looks; what it teaches (Phase 15), its composition (Phase 14), when things happen
(Phase 16), the rendering (Phase 13), the presenter's identity (Phase 12) and the media (the Visual Router) are decided
elsewhere and never here.

Precedence (most important first): accessibility (contrast, readable sizes) > the user's explicit choices (lesson
settings, Visual Review) > educational constraints and timing (Phases 16, 15, 14, which own their decisions) > the
style's preferences > decorative defaults. A style never changes a lesson's motion, transition or background setting:
its preferences are offered as defaults when the user picks the style.

Old lessons (no style chosen) resolve to Cinematic Education@1, whose tokens are the page's existing values: they
render exactly as before.
"""
import hashlib
import json
import re

SCHEMA = 1
FAMILIES = ("academic", "cinematic_education", "children_education", "corporate_training")
DEFAULT_FAMILY = "cinematic_education"
# Phase 13's lesson "typography" (palette + title font) is the legacy way a look was chosen: both values are kept, so a
# lesson saved before Phase 17 renders unchanged ("modern" is a hidden variant of the default family, never offered)
LEGACY_TYPOGRAPHY = {"academic": (DEFAULT_FAMILY, None), "modern": (DEFAULT_FAMILY, "modern")}

# Font stacks the page already loads (Google Fonts: Inter, Outfit, JetBrains Mono) plus the system serif: no download
FONTS = {
    "outfit": "'Outfit', sans-serif",
    "inter": "'Inter', sans-serif",
    "serif": "Georgia, 'Times New Roman', serif",
    "mono": "'JetBrains Mono', monospace",
}

TONES = ("dark", "light")
EMPHASIS_STYLES = ("glow", "outline", "soft")          # how a focused element is marked (always shape + colour)
CAPTION_STYLES = ("shadow", "box")                     # captions over the frame: shadowed text, or a dark box behind it
PRESENTER_FRAMES = ("none", "clean", "card", "rounded")
VISUAL_FRAMES = ("panel", "card", "rounded", "plain")
BACKGROUND_PATTERNS = ("none", "dots", "grid", "paper")
MOTION_LEVELS = ("low", "standard")                    # entrances: plain fades (low) or the style's movement
CAMERA_PREFS = ("still", "subtle")                     # Phase 13 owns the camera; this only suggests the lesson default
TRANSITION_PREFS = ("cut", "fade", "soft_fade", "crossfade", "slide", "zoom", "wipe")

# ---- the base: the page's existing look (Cinematic Education@1) -----------------------------------------------------
# Every token the page reads (as the CSS variable --st-<name with dashes>). Values are CSS values made here, never by a
# user: overrides only pick among the named options below.
BASE = {
    # background
    "bg-1": "#140a2e", "bg-2": "#2a1352", "bg-3": "#0d1b3d", "bg-solid": "#1a1036", "backdrop": "#120a26",
    "bg-glow-1": "rgba(126, 74, 226, 0.32)", "bg-glow-2": "rgba(46, 96, 206, 0.22)", "vignette": "rgba(0, 0, 0, 0.42)",
    "bg-pattern": "none", "scrim": "#0b0618",
    # surfaces (the board card, inner panels)
    "surface-1": "rgba(40, 19, 82, 0.92)", "surface-2": "rgba(19, 10, 42, 0.92)", "surface-border": "rgba(255, 215, 0, 0.22)",
    "surface-shadow": "0 24px 60px rgba(0, 0, 0, 0.42), inset 0 1px 0 rgba(255, 255, 255, 0.06)",
    "panel": "rgba(255, 255, 255, 0.055)", "panel-border": "rgba(255, 255, 255, 0.10)",
    "definition-bg": "rgba(255, 255, 255, 0.05)", "formula-bg": "rgba(255, 215, 0, 0.08)", "formula-border": "rgba(255, 215, 0, 0.3)",
    # text
    "text": "rgba(255, 255, 255, 0.95)", "text-secondary": "rgba(255, 255, 255, 0.72)", "text-muted": "rgba(255, 255, 255, 0.55)",
    "heading": "#FFD700", "title-text": "#ffffff",
    "heading-shadow": "0 2px 16px rgba(0, 0, 0, 0.9)", "title-shadow": "0 2px 12px rgba(0, 0, 0, 0.45)",
    "body-shadow": "0 2px 10px rgba(0, 0, 0, 0.8)",
    # colour roles
    "accent": "#FFD700", "accent-rgb": "255, 215, 0", "accent-text": "#FFD700", "on-accent": "#1a1033",
    "accent-2-rgb": "245, 197, 66", "date-text": "#ffd866", "secondary": "#00F0FF", "secondary-rgb": "0, 240, 255",
    "focus-rgb": "255, 215, 0", "info": "#00e5ff", "success": "#00ff88", "error": "#ff5555",
    "callout-bg": "rgba(10, 10, 20, 0.6)", "callout-text": "rgba(255, 255, 255, 0.9)",
    # tables
    "table-bg": "rgba(20, 10, 30, 0.4)", "table-border": "rgba(255, 215, 0, 0.2)", "table-head-bg": "rgba(255, 215, 0, 0.15)",
    "table-head-text": "#FFD700", "row-border": "rgba(255, 255, 255, 0.1)", "stripe": "rgba(255, 255, 255, 0.02)",
    # code and its output (code panels stay dark in every style: the page's Prism theme is dark)
    "code-bg": "rgba(9, 6, 22, 0.88)", "code-border": "rgba(255, 255, 255, 0.08)", "code-accent": "#FFD700",
    "output-bg": "rgba(9, 6, 22, 0.88)", "output-border": "rgba(120, 255, 170, 0.28)", "output-text": "#d6ffe4",
    "output-label": "rgba(196, 255, 216, 0.7)",
    # captions, labels, title overlay
    "caption-text": "#ffffff", "caption-bg": "transparent", "caption-highlight": "#00F0FF",
    "caption-shadow": "0px 2px 8px rgba(0, 0, 0, 0.9), 0px 0px 4px rgba(0, 0, 0, 0.6)", "caption-size": "1.8rem",
    "label-bg": "rgba(255, 215, 0, 0.12)", "label-border": "rgba(255, 215, 0, 0.5)", "label-text": "#ffffff",
    "title-bar": "#FFD700",
    # presenter and visual frames
    "presenter-border": "rgba(255, 215, 0, 0.35)", "presenter-radius": "14px", "presenter-shadow": "0 10px 28px rgba(0, 0, 0, 0.5)",
    "presenter-drop": "drop-shadow(0 8px 18px rgba(0, 0, 0, 0.45))",
    # the card / rounded diagram frames (the default "panel" frame keeps the page's own side panel)
    "visual-bg": "rgba(40, 15, 60, 0.85)", "visual-border": "rgba(255, 215, 0, 0.3)", "visual-radius": "20px",
    "visual-shadow": "0 12px 32px rgba(0, 0, 0, 0.4)",
    # charts and diagrams (procedural visuals read these)
    "chart-1": "#FFD700", "chart-2": "#B026FF", "chart-3": "#00e5ff", "chart-4": "#00ff88",
    "chart-grid": "rgba(255, 255, 255, 0.1)", "chart-tick": "rgba(255, 255, 255, 0.7)",
    # typography
    "font-title": FONTS["outfit"], "font-heading": FONTS["outfit"], "font-body": FONTS["inter"], "font-code": FONTS["mono"],
    "font-caption": FONTS["outfit"],
    "title-weight": "800", "heading-weight": "700", "body-weight": "300", "heading-case": "uppercase", "title-case": "none",
    "title-tracking": "-0.01em", "heading-tracking": "-0.01em", "text-scale": "1", "line-height": "1.45",
    "label-size": "clamp(0.8rem, 1.1vw, 1.2rem)",
    # spacing and shapes
    "space-xs": "0.3em", "space-sm": "0.55em", "space-md": "0.8em", "space-lg": "1.2em", "space-xl": "1.6em",
    "board-pad": "clamp(0.9rem, 1.9vw, 2.2rem) clamp(1rem, 2.3vw, 2.6rem)",
    "radius-sm": "6px", "radius-md": "0.7em", "radius-lg": "clamp(12px, 1.3vw, 22px)", "radius-chip": "999px",
    # motion (entrances and emphasis; Phase 16 decides WHEN, these only how long and how they ease)
    "ease": "cubic-bezier(0.22, 1, 0.36, 1)", "motion-fast": "0.25s", "motion-normal": "0.4s", "motion-slow": "0.6s",
    "entrance-ms": "auto", "entrance-shift": "1",  # auto: the page's own entrance lengths (fades 0.5 s, slides 0.65 s)
}

# The non-CSS part: what the page and the composer read as choices (enums)
BASE_PREFS = {
    "tone": "dark", "emphasis": "glow", "caption": "shadow", "presenter_frame": "clean", "presenter_label": False,
    "visual_frame": "panel", "pattern": "none", "motion": "standard", "camera": "subtle", "transition": "fade",
}

# ---- the four families (composition over inheritance: each is the base plus its own values) -------------------------

FAMILY_DEFS = {
    "cinematic_education": {
        1: {
            "label": "Cinematic Education", "description": "Rich, deep and focused: visual storytelling with controlled depth",
            "tokens": {}, "prefs": {},
            "background_words": "deep indigo and violet, soft gradient light, subtle paper texture",
        },
    },
    "academic": {
        1: {
            "label": "Academic", "description": "Clean and structured: a calm paper page with a strong hierarchy",
            "background_words": "warm ivory paper, very soft light, faint ruled texture",
            "prefs": {"tone": "light", "emphasis": "outline", "caption": "box", "presenter_frame": "clean", "visual_frame": "card",
                      "pattern": "paper", "motion": "low", "camera": "subtle", "transition": "fade"},
            "tokens": {
                "bg-1": "#f4efe3", "bg-2": "#fbf8f1", "bg-3": "#ece5d4", "bg-solid": "#f7f3ea", "backdrop": "#f4efe4",
                "bg-glow-1": "rgba(29, 78, 137, 0.06)", "bg-glow-2": "rgba(155, 35, 53, 0.04)", "vignette": "rgba(60, 40, 10, 0.10)",
                "scrim": "#f7f3ea",
                "surface-1": "#ffffff", "surface-2": "#fdfbf6", "surface-border": "rgba(31, 42, 68, 0.14)",
                "surface-shadow": "0 10px 30px rgba(31, 42, 68, 0.10), 0 1px 0 rgba(31, 42, 68, 0.04)",
                "panel": "rgba(31, 42, 68, 0.035)", "panel-border": "rgba(31, 42, 68, 0.13)",
                "definition-bg": "rgba(29, 78, 137, 0.05)", "formula-bg": "rgba(155, 35, 53, 0.04)", "formula-border": "rgba(155, 35, 53, 0.28)",
                "text": "#1c2638", "text-secondary": "#3f4a5e", "text-muted": "#5f6878",
                "heading": "#1d3d6e", "title-text": "#1c2638", "heading-shadow": "none", "title-shadow": "none", "body-shadow": "none",
                "accent": "#9b2335", "accent-rgb": "155, 35, 53", "accent-text": "#8a1f2f", "on-accent": "#ffffff",
                "accent-2-rgb": "155, 35, 53", "date-text": "#8a1f2f", "secondary": "#1d4e89", "secondary-rgb": "29, 78, 137",
                "focus-rgb": "29, 78, 137", "info": "#1d4e89", "success": "#2f6f4f", "error": "#9b2335",
                "callout-bg": "rgba(31, 42, 68, 0.04)", "callout-text": "#1c2638",
                "table-bg": "#ffffff", "table-border": "rgba(31, 42, 68, 0.18)", "table-head-bg": "rgba(29, 78, 137, 0.08)",
                "table-head-text": "#1d3d6e", "row-border": "rgba(31, 42, 68, 0.10)", "stripe": "rgba(31, 42, 68, 0.025)",
                "code-bg": "#1e2433", "code-border": "rgba(31, 42, 68, 0.25)", "code-accent": "#f4a3ad",
                "output-bg": "#eef3f8", "output-border": "rgba(29, 78, 137, 0.35)", "output-text": "#1c2638", "output-label": "#1d4e89",
                "caption-text": "#ffffff", "caption-bg": "rgba(20, 24, 36, 0.80)", "caption-highlight": "#ffd479", "caption-shadow": "none",
                "label-bg": "#ffffff", "label-border": "rgba(155, 35, 53, 0.45)", "label-text": "#1c2638", "title-bar": "#9b2335",
                "presenter-border": "rgba(31, 42, 68, 0.16)", "presenter-radius": "8px",
                "presenter-shadow": "0 8px 24px rgba(31, 42, 68, 0.14)", "presenter-drop": "drop-shadow(0 6px 12px rgba(31, 42, 68, 0.18))",
                "visual-bg": "#ffffff", "visual-border": "rgba(31, 42, 68, 0.14)", "visual-radius": "8px",
                "visual-shadow": "0 8px 24px rgba(31, 42, 68, 0.10)",
                "chart-1": "#1d4e89", "chart-2": "#9b2335", "chart-3": "#2f7d6d", "chart-4": "#b7791f",
                "chart-grid": "rgba(31, 42, 68, 0.10)", "chart-tick": "#3f4a5e",
                "font-title": FONTS["serif"], "font-heading": FONTS["serif"], "font-caption": FONTS["inter"],
                "title-weight": "700", "heading-weight": "700", "body-weight": "400", "heading-case": "none",
                "title-tracking": "0", "heading-tracking": "0", "line-height": "1.5",
                "radius-sm": "3px", "radius-md": "0.35em", "radius-lg": "clamp(6px, 0.7vw, 10px)", "radius-chip": "4px",
                "entrance-ms": "500", "entrance-shift": "0.5",
            },
        },
    },
    "children_education": {
        1: {
            "label": "Children's Education", "description": "Friendly and engaging: bright, rounded and clear",
            "background_words": "soft warm cream and light sky blue, gentle rounded shapes, playful but calm",
            "prefs": {"tone": "light", "emphasis": "soft", "caption": "box", "presenter_frame": "rounded", "presenter_label": True,
                      "visual_frame": "rounded", "pattern": "dots", "motion": "standard", "camera": "subtle", "transition": "soft_fade"},
            "tokens": {
                "bg-1": "#fff4e0", "bg-2": "#fffaf0", "bg-3": "#e6f4ff", "bg-solid": "#fff7ea", "backdrop": "#fff6e8",
                "bg-glow-1": "rgba(255, 159, 67, 0.16)", "bg-glow-2": "rgba(72, 187, 255, 0.16)", "vignette": "rgba(255, 170, 60, 0.08)",
                "scrim": "#fff7ea",
                "surface-1": "#ffffff", "surface-2": "#fffdf8", "surface-border": "rgba(255, 140, 50, 0.32)",
                "surface-shadow": "0 12px 30px rgba(255, 140, 50, 0.14), 0 2px 0 rgba(255, 140, 50, 0.10)",
                "panel": "rgba(72, 187, 255, 0.08)", "panel-border": "rgba(72, 187, 255, 0.30)",
                "definition-bg": "rgba(72, 187, 255, 0.08)", "formula-bg": "rgba(255, 140, 50, 0.07)", "formula-border": "rgba(255, 140, 50, 0.38)",
                "text": "#2b2d42", "text-secondary": "#45495f", "text-muted": "#5c6075",
                "heading": "#c2410c", "title-text": "#2b2d42", "heading-shadow": "none", "title-shadow": "none", "body-shadow": "none",
                "accent": "#f76707", "accent-rgb": "247, 103, 7", "accent-text": "#c2410c", "on-accent": "#1c1917",
                "accent-2-rgb": "59, 179, 255", "date-text": "#0369a1", "secondary": "#0369a1", "secondary-rgb": "3, 105, 161",
                "focus-rgb": "255, 140, 50", "info": "#0369a1", "success": "#15803d", "error": "#c2410c",
                "callout-bg": "rgba(72, 187, 255, 0.06)", "callout-text": "#2b2d42",
                "table-bg": "#ffffff", "table-border": "rgba(255, 140, 50, 0.30)", "table-head-bg": "rgba(255, 140, 50, 0.12)",
                "table-head-text": "#c2410c", "row-border": "rgba(43, 45, 66, 0.08)", "stripe": "rgba(72, 187, 255, 0.05)",
                "code-bg": "#26213a", "code-border": "rgba(43, 45, 66, 0.2)", "code-accent": "#ffb27a",
                "output-bg": "#ecfdf3", "output-border": "rgba(22, 163, 74, 0.38)", "output-text": "#14532d", "output-label": "#15803d",
                "caption-text": "#ffffff", "caption-bg": "rgba(43, 45, 66, 0.82)", "caption-highlight": "#ffd166", "caption-shadow": "none",
                "label-bg": "#fff4e6", "label-border": "rgba(255, 140, 50, 0.55)", "label-text": "#2b2d42", "title-bar": "#f76707",
                "presenter-border": "rgba(72, 187, 255, 0.55)", "presenter-radius": "28px",
                "presenter-shadow": "0 10px 26px rgba(72, 187, 255, 0.22)", "presenter-drop": "drop-shadow(0 6px 12px rgba(43, 45, 66, 0.18))",
                "visual-bg": "#ffffff", "visual-border": "rgba(72, 187, 255, 0.38)", "visual-radius": "22px",
                "visual-shadow": "0 10px 26px rgba(72, 187, 255, 0.16)",
                "chart-1": "#ff8c32", "chart-2": "#3bb3ff", "chart-3": "#34c38f", "chart-4": "#a78bfa",
                "chart-grid": "rgba(43, 45, 66, 0.10)", "chart-tick": "#45495f",
                "font-title": FONTS["outfit"], "font-heading": FONTS["outfit"], "font-body": FONTS["outfit"], "font-caption": FONTS["outfit"],
                "title-weight": "800", "heading-weight": "700", "body-weight": "400", "heading-case": "none",
                "title-tracking": "0", "heading-tracking": "0", "text-scale": "1.06", "line-height": "1.5",
                "space-sm": "0.65em", "space-md": "0.9em", "space-lg": "1.3em",
                "radius-sm": "10px", "radius-md": "1em", "radius-lg": "clamp(16px, 2vw, 30px)", "radius-chip": "999px",
                "entrance-ms": "650", "entrance-shift": "1.2",
            },
        },
    },
    "corporate_training": {
        1: {
            "label": "Corporate Training", "description": "Professional and focused: crisp navy, clear alignment, restrained",
            "background_words": "dark navy and teal, soft geometric light, clean",
            "prefs": {"tone": "dark", "emphasis": "outline", "caption": "shadow", "presenter_frame": "card", "presenter_label": True,
                      "visual_frame": "card", "pattern": "grid", "motion": "low", "camera": "subtle", "transition": "crossfade"},
            "tokens": {
                "bg-1": "#0b1224", "bg-2": "#1b2a4a", "bg-3": "#10233d", "bg-solid": "#111a2e", "backdrop": "#0c1426",
                "bg-glow-1": "rgba(56, 189, 248, 0.10)", "bg-glow-2": "rgba(30, 64, 175, 0.18)", "vignette": "rgba(0, 0, 0, 0.30)",
                "scrim": "#070c18",
                "surface-1": "rgba(17, 27, 48, 0.95)", "surface-2": "rgba(12, 20, 38, 0.95)", "surface-border": "rgba(148, 163, 184, 0.22)",
                "surface-shadow": "0 16px 40px rgba(0, 0, 0, 0.35), inset 0 1px 0 rgba(255, 255, 255, 0.04)",
                "panel": "rgba(148, 163, 184, 0.06)", "panel-border": "rgba(148, 163, 184, 0.18)",
                "definition-bg": "rgba(56, 189, 248, 0.06)", "formula-bg": "rgba(56, 189, 248, 0.06)", "formula-border": "rgba(56, 189, 248, 0.3)",
                "text": "#e5e9f0", "text-secondary": "#b6c0cf", "text-muted": "#8f9bb0",
                "heading": "#7fd4ff", "title-text": "#f1f5f9", "heading-shadow": "none", "title-shadow": "0 1px 2px rgba(0, 0, 0, 0.35)",
                "body-shadow": "none",
                "accent": "#38bdf8", "accent-rgb": "56, 189, 248", "accent-text": "#7dd3fc", "on-accent": "#0b1224",
                "accent-2-rgb": "56, 189, 248", "date-text": "#7dd3fc", "secondary": "#a5b4fc", "secondary-rgb": "165, 180, 252",
                "focus-rgb": "56, 189, 248", "info": "#7dd3fc", "success": "#6ee7b7", "error": "#fca5a5",
                "callout-bg": "rgba(148, 163, 184, 0.08)", "callout-text": "#e5e9f0",
                "table-bg": "rgba(15, 26, 47, 0.6)", "table-border": "rgba(148, 163, 184, 0.25)", "table-head-bg": "rgba(56, 189, 248, 0.12)",
                "table-head-text": "#7fd4ff", "row-border": "rgba(148, 163, 184, 0.14)", "stripe": "rgba(148, 163, 184, 0.04)",
                "code-bg": "#0a1020", "code-border": "rgba(148, 163, 184, 0.18)", "code-accent": "#7dd3fc",
                "output-bg": "#0a1020", "output-border": "rgba(52, 211, 153, 0.30)", "output-text": "#d1fae5", "output-label": "#6ee7b7",
                "caption-text": "#ffffff", "caption-highlight": "#7dd3fc",
                "label-bg": "rgba(56, 189, 248, 0.10)", "label-border": "rgba(56, 189, 248, 0.5)", "label-text": "#f1f5f9",
                "title-bar": "#38bdf8",
                "presenter-border": "rgba(148, 163, 184, 0.32)", "presenter-radius": "10px",
                "presenter-shadow": "0 10px 26px rgba(0, 0, 0, 0.4)", "presenter-drop": "drop-shadow(0 8px 16px rgba(0, 0, 0, 0.4))",
                "visual-bg": "rgba(12, 20, 38, 0.9)", "visual-border": "rgba(148, 163, 184, 0.22)", "visual-radius": "8px",
                "visual-shadow": "0 12px 30px rgba(0, 0, 0, 0.3)",
                "chart-1": "#38bdf8", "chart-2": "#a5b4fc", "chart-3": "#34d399", "chart-4": "#fbbf24",
                "chart-grid": "rgba(148, 163, 184, 0.14)", "chart-tick": "#b6c0cf",
                "font-title": FONTS["inter"], "font-heading": FONTS["inter"], "font-body": FONTS["inter"], "font-caption": FONTS["inter"],
                "title-weight": "700", "heading-weight": "600", "body-weight": "400", "heading-case": "none",
                "title-tracking": "-0.02em", "heading-tracking": "-0.01em",
                "radius-sm": "4px", "radius-md": "0.4em", "radius-lg": "clamp(8px, 0.8vw, 12px)", "radius-chip": "6px",
                "entrance-ms": "450", "entrance-shift": "0.6",
            },
        },
    },
}

# Phase 13's "modern" typography, kept so lessons saved with it render unchanged (never offered as a choice)
LEGACY_VARIANTS = {
    "modern": {"tokens": {"bg-1": "#0b1224", "bg-2": "#1b2a4a", "bg-3": "#10233d", "bg-solid": "#111a2e", "title-bar": "#7fd4ff",
                          "font-title": FONTS["inter"], "title-tracking": "-0.02em"},
               "background_words": "dark navy and teal, soft geometric light, clean"},
}

# ---- the user's overrides: a few meaningful, bounded choices (never a raw value) --------------------------------------

# Accent colours: (fill, text on the style's surface, text on the fill) for a dark and for a light style
ACCENTS = {
    "gold": {"dark": ("#FFD700", "#FFD700", "#1a1033"), "light": ("#b7791f", "#8a5a12", "#1c1917")},
    "coral": {"dark": ("#ff8a65", "#ffab91", "#1a1033"), "light": ("#e8590c", "#b5420a", "#1c1917")},
    "crimson": {"dark": ("#f87171", "#fca5a5", "#1a1033"), "light": ("#9b2335", "#8a1f2f", "#ffffff")},
    "teal": {"dark": ("#2dd4bf", "#5eead4", "#0b1224"), "light": ("#0f766e", "#0d5f58", "#ffffff")},
    "sky": {"dark": ("#38bdf8", "#7dd3fc", "#0b1224"), "light": ("#0369a1", "#075985", "#ffffff")},
    "violet": {"dark": ("#a78bfa", "#c4b5fd", "#120a26"), "light": ("#6d28d9", "#5b21b6", "#ffffff")},
    "emerald": {"dark": ("#34d399", "#6ee7b7", "#0b1224"), "light": ("#047857", "#065f46", "#ffffff")},
}
TEXT_SIZES = {"standard": 1.0, "large": 1.1, "larger": 1.2}
BACKGROUND_DENSITY = ("plain", "subtle", "rich")
CAPTION_SIZES = {"standard": 1.0, "large": 1.2}
COMPONENT_SIZES = ("standard", "large")
OVERRIDE_OPTIONS = {
    "accent": ("default",) + tuple(ACCENTS),
    "text_size": ("default",) + tuple(TEXT_SIZES),
    "motion": ("default",) + MOTION_LEVELS,
    "background": ("default",) + BACKGROUND_DENSITY,
    "caption_size": ("default",) + tuple(CAPTION_SIZES),
    "code_size": ("default",) + COMPONENT_SIZES,
    "formula_size": ("default",) + COMPONENT_SIZES,
    "diagram_frame": ("default",) + VISUAL_FRAMES,
}
SCENE_OVERRIDE_KEYS = ("accent", "background")   # a scene may differ only in these (consistency across the lesson)

MIN_CONTRAST = 4.5        # WCAG AA for body text
MIN_CONTRAST_LARGE = 3.0  # large text (titles, headings) and non-text marks
MAX_TEXT_SCALE = 1.3
MIN_TEXT_SCALE = 0.9

SAFE_VALUE = re.compile(r"^[#(),.%\-A-Za-z0-9 '/:]{0,200}$")  # ASCII only, spaces only
TOKEN_NAME = re.compile(r"^[a-z0-9-]{1,40}$")
# The only CSS functions a token may use (an allowlist: nothing can fetch, reference or compute outside the value)
CSS_FUNCTIONS = frozenset(("rgb", "rgba", "hsl", "hsla", "linear-gradient", "radial-gradient", "repeating-linear-gradient",
                           "repeating-radial-gradient", "clamp", "calc", "min", "max", "cubic-bezier", "drop-shadow"))
CSS_FUNCTION = re.compile(r"([A-Za-z-]+)\(")
FONT_NAME = re.compile(r"'[A-Za-z ]{1,40}'")  # quotes only around a font's name


def safe_css_value(value):
    """True when a token value is plain CSS from the allowlist: no URL or image function, no var()/attr()/env(), quotes
    only around font names (the page runs the same check before setting a variable)."""
    if not isinstance(value, str) or not SAFE_VALUE.fullmatch(value):
        return False
    low = value.lower()
    if "url" in low or "expression" in low:
        return False
    functions = CSS_FUNCTION.findall(value)
    if any(f.lower() not in CSS_FUNCTIONS for f in functions) or value.count("(") != len(functions):
        return False
    return "'" not in FONT_NAME.sub("", value)


class Invalid(ValueError):
    """An unknown style, version, override key or value."""


# ---- colour arithmetic (WCAG relative luminance, alpha composited over what is behind) --------------------------------

def parse_color(value):
    """(r, g, b, a) from #rgb, #rrggbb or rgb()/rgba(); None for anything else (gradients, 'transparent' is alpha 0)."""
    v = str(value or "").strip().lower()
    if v == "transparent":
        return (0, 0, 0, 0.0)
    m = re.fullmatch(r"#([0-9a-f]{3}|[0-9a-f]{6})", v)
    if m:
        h = m.group(1)
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 1.0)
    m = re.fullmatch(r"rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*(?:,\s*(\d*\.?\d+)\s*)?\)", v)
    if m:
        return (min(255, int(m.group(1))), min(255, int(m.group(2))), min(255, int(m.group(3))),
                max(0.0, min(1.0, float(m.group(4)) if m.group(4) is not None else 1.0)))
    return None


def over(top, bottom):
    """The colour seen when `top` (with alpha) lies over the opaque `bottom`."""
    a = top[3]
    return (round(top[0] * a + bottom[0] * (1 - a)), round(top[1] * a + bottom[1] * (1 - a)), round(top[2] * a + bottom[2] * (1 - a)), 1.0)


def luminance(c):
    def ch(x):
        x = x / 255
        return x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(c[0]) + 0.7152 * ch(c[1]) + 0.0722 * ch(c[2])


def contrast(fg, bg):
    """WCAG contrast ratio of two colour values (alpha composited: fg over bg, bg over black if translucent)."""
    b = parse_color(bg)
    f = parse_color(fg)
    if b is None or f is None:
        return None
    b = over(b, (0, 0, 0, 1.0)) if b[3] < 1 else b
    f = over(f, b) if f[3] < 1 else f
    l1, l2 = sorted((luminance(f), luminance(b)), reverse=True)
    return round((l1 + 0.05) / (l2 + 0.05), 2)


def _hex(c):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(x)))) for x in c[:3])


def _toward(color, target, step):
    c = parse_color(color)
    return _hex(tuple(c[i] + (target[i] - c[i]) * step for i in range(3)))


def ensure_contrast(fg, bg, minimum):
    """fg itself when readable on bg, else fg moved toward black or white (whichever reads) until it is."""
    ratio = contrast(fg, bg)
    if ratio is None or ratio >= minimum:
        return fg, False
    b = parse_color(bg)
    b = over(b, (0, 0, 0, 1.0)) if b[3] < 1 else b
    ends = sorted(((0, 0, 0), (255, 255, 255)), key=lambda end: -(contrast(_hex(end), _hex(b)) or 0))  # the end that reads best first
    for target in ends:
        for i in range(1, 21):
            candidate = _toward(fg, target, i / 20)
            if (contrast(candidate, bg) or 0) >= minimum:
                return candidate, True
    return _hex(ends[0]), True


# ---- resolution ------------------------------------------------------------------------------------------------------

def latest_version(family):
    return max(FAMILY_DEFS[family])


def family_choice(settings):
    """(family, version, variant) the lesson asks for: its style, or the legacy typography (old lessons)."""
    settings = settings or {}
    family = settings.get("style")
    if family:
        if not isinstance(family, str) or family not in FAMILY_DEFS:
            raise Invalid(f"style must be one of {', '.join(FAMILIES)}")
        version = settings.get("style_version")
        if not (isinstance(version, int) and not isinstance(version, bool) and version in FAMILY_DEFS[family]):
            version = None  # none, or an unknown one (e.g. a newer one from elsewhere): the latest this server knows
        return family, version or latest_version(family), None
    typography = settings.get("typography")
    family, variant = LEGACY_TYPOGRAPHY.get(typography if isinstance(typography, str) else "academic", (DEFAULT_FAMILY, None))
    return family, latest_version(family), variant


def check_overrides(overrides, keys=None):
    """The overrides cleaned to known keys and values; Invalid for anything else (never passed through)."""
    if overrides in (None, {}):
        return {}
    if not isinstance(overrides, dict):
        raise Invalid("style overrides must be an object")
    allowed = keys or tuple(OVERRIDE_OPTIONS)
    clean = {}
    for key, value in overrides.items():
        if key not in allowed:
            raise Invalid(f"unknown style override: {str(key)[:40]}")
        if value not in OVERRIDE_OPTIONS[key]:
            raise Invalid(f"{key} must be one of {', '.join(OVERRIDE_OPTIONS[key])}")
        if value != "default":
            clean[key] = value
    return clean


def clean_overrides(raw, keys=None):
    """The valid choices of stored overrides (a saved lesson, a scene's review): each unknown key or value is dropped on
    its own, the others are kept (never raises; the API's own input is checked strictly by check_overrides)."""
    if not isinstance(raw, dict):
        return {}
    allowed = keys or tuple(OVERRIDE_OPTIONS)
    return {k: v for k, v in raw.items() if k in allowed and isinstance(v, str) and v in OVERRIDE_OPTIONS[k] and v != "default"}


def scene_overrides(scene):
    """A scene's own style choices (Visual Review), limited to the keys a scene may change; invalid ones are ignored."""
    review = (scene or {}).get("visual_review") if isinstance(scene, dict) else None
    composition = review.get("composition") if isinstance(review, dict) else None
    raw = (composition or {}).get("overrides") if isinstance(composition, dict) and composition.get("status") in ("approved", "changed") else None
    raw = raw if isinstance(raw, dict) else {}
    found = {k[len("style_"):]: v for k, v in raw.items() if isinstance(k, str) and k.startswith("style_")}
    return clean_overrides(found, SCENE_OVERRIDE_KEYS)


def _pattern(kind, rgb, density):
    alpha = {"plain": 0.0, "subtle": 1.0, "rich": 1.6}[density]
    if kind == "none" or alpha == 0:
        return "none"
    if kind == "dots":
        return f"radial-gradient(rgba({rgb}, {round(0.10 * alpha, 3)}) 1.5px, transparent 1.6px) 0 0 / 26px 26px"
    if kind == "grid":
        a = round(0.05 * alpha, 3)
        return (f"linear-gradient(rgba({rgb}, {a}) 1px, transparent 1px) 0 0 / 48px 48px, "
                f"linear-gradient(90deg, rgba({rgb}, {a}) 1px, transparent 1px) 0 0 / 48px 48px")
    return f"repeating-linear-gradient(0deg, rgba({rgb}, {round(0.035 * alpha, 3)}) 0 1px, transparent 1px 34px)"  # paper


def _scale_alpha(value, factor):
    c = parse_color(value)
    if c is None or c[3] >= 1:
        return value
    return f"rgba({c[0]}, {c[1]}, {c[2]}, {round(min(1.0, c[3] * factor), 3)})"


def resolve(settings=None, scene=None, overrides=None):
    """The effective style of a scene: {family, version, label, tone, prefs, tokens, adjustments, fingerprint, ...}.

    settings: the lesson's cinematic settings (style, style_version, style_overrides; or the legacy typography)
    scene: the scene (its Visual Review style choices), optional
    overrides: the lesson's overrides when not inside settings
    """
    settings = settings or {}
    family, version, variant = family_choice(settings)
    definition = FAMILY_DEFS[family][version]
    lesson = check_overrides(overrides if overrides is not None else settings.get("style_overrides"))
    if not settings.get("style") and overrides is None:
        lesson = {}  # no style chosen: the original look (lesson overrides are kept only with a chosen style)
    local = scene_overrides(scene) if scene is not None else {}
    chosen = {**lesson, **local}
    prefs = {**BASE_PREFS, **definition.get("prefs", {})}
    tokens = {**BASE, **definition.get("tokens", {})}
    words = definition.get("background_words")
    if variant:
        tokens.update(LEGACY_VARIANTS[variant]["tokens"])
        words = LEGACY_VARIANTS[variant]["background_words"]
    tone = prefs["tone"]

    # accent (lesson or scene): the fill, the readable accent text, the text on the fill
    if chosen.get("accent"):
        fill, text, on = ACCENTS[chosen["accent"]][tone]
        c = parse_color(fill)
        rgb = f"{c[0]}, {c[1]}, {c[2]}"
        tokens["code-accent"] = ACCENTS[chosen["accent"]]["dark"][1]
        tokens.update({"accent": fill, "accent-rgb": rgb, "accent-text": text, "on-accent": on, "title-bar": fill,
                       "accent-2-rgb": rgb, "focus-rgb": rgb, "label-border": f"rgba({rgb}, 0.5)", "chart-1": fill})
        if tone == "dark":
            tokens.update({"heading": text, "table-head-text": text, "date-text": text,
                           "label-bg": f"rgba({rgb}, 0.12)", "table-head-bg": f"rgba({rgb}, 0.15)", "table-border": f"rgba({rgb}, 0.2)",
                           "formula-bg": f"rgba({rgb}, 0.08)", "formula-border": f"rgba({rgb}, 0.3)", "surface-border": f"rgba({rgb}, 0.22)"})
        else:
            tokens.update({"date-text": text, "formula-border": f"rgba({rgb}, 0.3)"})
    # background density: how present the glows and the pattern are (content always wins)
    density = chosen.get("background") or "subtle"
    factor = {"plain": 0.0, "subtle": 1.0, "rich": 1.5}[density]
    tokens["bg-glow-1"] = _scale_alpha(tokens["bg-glow-1"], factor) if factor else "transparent"
    tokens["bg-glow-2"] = _scale_alpha(tokens["bg-glow-2"], factor) if factor else "transparent"
    pattern_rgb = "31, 42, 68" if tone == "light" else "255, 255, 255"
    tokens["bg-pattern"] = _pattern(prefs["pattern"], pattern_rgb, density)
    # sizes: text, captions, code and formulas (bounded; the page's fit still shrinks a board that would overflow)
    scale = float(tokens["text-scale"]) * TEXT_SIZES.get(chosen.get("text_size"), 1.0)
    tokens["text-scale"] = _num(max(MIN_TEXT_SCALE, min(MAX_TEXT_SCALE, scale)))
    caption_scale = CAPTION_SIZES.get(chosen.get("caption_size"), 1.0)
    tokens["caption-size"] = "1.8rem" if caption_scale == 1.0 else f"{_num(1.8 * caption_scale)}rem"
    tokens["code-scale"] = "1.12" if chosen.get("code_size") == "large" else "1"
    tokens["formula-scale"] = "1.15" if chosen.get("formula_size") == "large" else "1"
    if chosen.get("motion"):
        prefs["motion"] = chosen["motion"]
    if prefs["motion"] == "low":
        tokens["entrance-shift"] = "0"
    if chosen.get("diagram_frame"):
        prefs["visual_frame"] = chosen["diagram_frame"]

    adjustments = _accessibility(tokens, tone)
    effective_overrides = {k: v for k, v in sorted(lesson.items())}
    look = {
        "schema": SCHEMA, "family": family, "version": version, "id": f"{family}@{version}", "label": definition["label"],
        "variant": variant, "tone": tone, "prefs": prefs, "overrides": effective_overrides, "scene_overrides": dict(sorted(local.items())),
        "adjustments": adjustments, "background_words": words,
        "legacy": not settings.get("style"),  # the lesson chose no style: its original look (not part of the fingerprint)
    }
    look["fingerprint"] = fingerprint(look)
    look["tokens"] = tokens
    return look


def _num(x):
    return f"{x:.3f}".rstrip("0").rstrip(".")


# The readable pairs: (foreground token, background token or a fixed reference, minimum, what it is)
def _surface_ref(tokens):
    """The colour text sits on: the board's surface over the background (translucent surfaces composited)."""
    bg = parse_color(tokens["bg-2"]) or (0, 0, 0, 1.0)
    s = parse_color(tokens["surface-1"]) or bg
    return _hex(over(s, bg) if s[3] < 1 else s)


def _accessibility(tokens, tone):
    """Accessibility over decoration: every text colour reads on what it sits on (adjusted when not); bounded sizes."""
    surface = _surface_ref(tokens)
    background = _hex(over(parse_color(tokens["bg-2"]), (0, 0, 0, 1.0)))
    adjustments = []
    fixed, changed = ensure_contrast(tokens["accent"], surface, MIN_CONTRAST_LARGE)  # bullets, ticks, step numbers: 3:1
    if changed:
        adjustments.append("bullets, ticks and step numbers made darker or lighter to stay readable")
        tokens["accent"] = fixed
        c = parse_color(fixed)
        tokens["accent-rgb"] = f"{c[0]}, {c[1]}, {c[2]}"
    pairs = [
        ("text", surface, MIN_CONTRAST, "body text"), ("text-secondary", surface, MIN_CONTRAST, "secondary text"),
        ("heading", surface, MIN_CONTRAST_LARGE, "headings"), ("accent-text", surface, MIN_CONTRAST, "highlighted words"),
        ("table-head-text", surface, MIN_CONTRAST, "table headers"), ("date-text", surface, MIN_CONTRAST, "dates"),
        ("title-text", background, MIN_CONTRAST_LARGE, "the scene title"), ("callout-text", surface, MIN_CONTRAST, "callouts"),
        ("label-text", tokens["label-bg"] if (parse_color(tokens["label-bg"]) or (0, 0, 0, 0))[3] >= 1 else background,
         MIN_CONTRAST, "label chips"),
        ("output-text", tokens["output-bg"] if (parse_color(tokens["output-bg"]) or (0, 0, 0, 0))[3] >= 1 else surface,
         MIN_CONTRAST, "the code's output"),
        ("on-accent", tokens["accent"], MIN_CONTRAST, "text on the accent"),
        ("code-accent", _hex(over(parse_color(tokens["code-bg"]), parse_color(surface))) if (parse_color(tokens["code-bg"]) or (0, 0, 0, 1))[3] < 1
         else tokens["code-bg"], MIN_CONTRAST, "highlighted words in code"),
        ("date-text", _hex(over(_rgba(tokens["accent-2-rgb"], 0.16), parse_color(surface))), MIN_CONTRAST, "dates on their badge"),
    ]
    if tone == "dark" or (parse_color(tokens["caption-bg"]) or (0, 0, 0, 0))[3] < 0.5:
        pairs.append(("caption-text", "#000000" if tone == "dark" else background, MIN_CONTRAST, "captions"))
    else:
        pairs.append(("caption-text", _hex(over(parse_color(tokens["caption-bg"]), parse_color(background))), MIN_CONTRAST, "captions"))
    for name, bg, minimum, what in pairs:
        fixed, changed = ensure_contrast(tokens[name], bg, minimum)
        if changed:
            adjustments.append(f"{what} made darker or lighter to stay readable")
            tokens[name] = fixed
    return adjustments


def _rgba(rgb, alpha):
    parts = [int(x) for x in str(rgb).split(",")]
    return (parts[0], parts[1], parts[2], alpha)


def fingerprint(look):
    """The effective style's identity: family, version, variant and the overrides that apply (nothing else)."""
    keep = {k: look.get(k) for k in ("schema", "family", "version", "variant", "overrides", "scene_overrides")}
    return hashlib.sha256(json.dumps(keep, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def css_variables(look):
    """{'--st-name': value} for the page (every name and value checked: no URL, no expression, nothing executable)."""
    out = {}
    for name, value in (look.get("tokens") or {}).items():
        value = str(value)
        if not isinstance(name, str) or not TOKEN_NAME.fullmatch(name) or not safe_css_value(value):
            raise Invalid(f"unsafe style token: {name}")
        out[f"--st-{name}"] = value
    return out


def plan_look(settings=None, scene=None):
    """What a scene's cinematic plan carries (`plan.style.look`): the effective style and its CSS variables."""
    look = resolve(settings, scene)
    return {k: look[k] for k in ("schema", "id", "family", "version", "label", "variant", "tone", "prefs", "overrides",
                                 "scene_overrides", "adjustments", "fingerprint", "legacy")} | {"css": css_variables(look)}


def catalog():
    """The families for the style selector: label, description, preferences and a few tokens for a preview card."""
    out = []
    for family in FAMILIES:
        version = latest_version(family)
        look = resolve({"style": family})
        t = look["tokens"]
        out.append({"id": family, "version": version, "label": look["label"], "description": FAMILY_DEFS[family][version]["description"],
                    "tone": look["tone"], "prefs": look["prefs"], "css": css_variables(look),
                    "preview": {k: t[k] for k in ("bg-1", "bg-2", "surface-1", "text", "heading", "accent", "title-text")}})
    return out


def options():
    """The override vocabulary (for the API and the page)."""
    return {k: list(v) for k, v in OVERRIDE_OPTIONS.items()} | {"scene": list(SCENE_OVERRIDE_KEYS)}


def lesson_choice(value):
    """The style choice a saved lesson keeps ({style, style_version, style_overrides}), cleaned; None when there is
    nothing valid to keep (an unknown family, version or override is dropped, never stored)."""
    if not isinstance(value, dict):
        return None
    family = value.get("style")
    if not isinstance(family, str) or family not in FAMILY_DEFS:
        return None
    version = value.get("style_version")
    version = version if isinstance(version, int) and not isinstance(version, bool) and version in FAMILY_DEFS[family] else latest_version(family)
    return {"style": family, "style_version": version, "style_overrides": clean_overrides(value.get("style_overrides"))}


def background_words(settings=None):
    """The words an AI background prompt uses for the lesson's style (the legacy styles keep theirs: cached backgrounds
    stay reusable)."""
    return resolve(settings)["background_words"]
