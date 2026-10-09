# ruff: noqa: F403, F405, E402
"""Aadhi Manim runtime: injected verbatim in front of every scene script.

This file is NEVER imported by the application; ``aadhi.manim.aadhi_scene`` reads it as text and
prepends a ``_AADHI_CFG = {...}`` header (beat times, duration, target, theme, fonts...). It runs
inside the sandbox and provides:

* ``AadhiScene`` (a ``MovingCameraScene``) with exact beat timing (``wait_until_beat``,
  ``play_step``, ``step_run_time``, ``finish``), safe-area layout helpers, themed text/maths
  helpers with per-script font selection and a plain-text fallback when LaTeX is unavailable;
* safety patches: every TeX expression is checked against the deny pattern before it reaches
  LaTeX, custom TeX templates are refused, and extra flags are added so a missing package fails
  fast instead of prompting (``AADHI_TEX_FLAGS`` for latex, e.g. MiKTeX ``-disable-installer``;
  ``AADHI_DVISVGM_FLAGS`` for dvisvgm, e.g. ``--miktex-disable-installer``); ``Code`` refuses
  ``code_file``, scenes refuse ``add_sound``, SVG mobjects only load the SVGs Manim itself renders
  for text and formulas, and raster images (background images) are refused (no host file reads);
* an export allow-list: at the end of this file ``manim.__all__`` is narrowed so that
  ``from manim import *`` in scene code yields only mobjects, animations, scenes, colours,
  constants and maths helpers (no file/process/logging helpers such as ``capture`` or
  ``guarantee_empty_existence``, and no module objects other than ``np`` and ``rate_functions``);
* compact error reports (``[AADHI_ERROR] ... [/AADHI_ERROR]``) with user-code line numbers.

Every module-level helper that user code must not touch is underscore-prefixed: the AST guard
rejects any name or attribute starting with ``_`` in user code.
"""

import os as _aadhi_os
import pathlib as _aadhi_pathlib
import re as _aadhi_re
import subprocess as _aadhi_subprocess
import sys as _aadhi_sys
import textwrap as _aadhi_textwrap
import traceback as _aadhi_traceback
import types as _aadhi_types

import manim as _aadhi_manim
import manim.camera.camera as _aadhi_camera_mod
import manim.mobject.svg.svg_mobject as _aadhi_svg_mod
import manim.mobject.types.image_mobject as _aadhi_image_mod
import manim.utils.tex_file_writing as _aadhi_tfw
from manim import *

_AADHI = {
    "beat_times": [],
    "total_duration": 10.0,
    "target": "fullscreen",
    "language": "en-IN",
    "has_latex": True,
    "background": "#1A0B2E",
    "pixel_width": 0,
    "pixel_height": 0,
    "user_line_offset": 0,
    "tex_deny": "",
    "script_fonts": {"latin": []},
}
_AADHI.update(globals().get("_AADHI_CFG") or {})

BEAT_TIMES = [float(t) for t in _AADHI["beat_times"]]
TOTAL_DURATION = float(_AADHI["total_duration"])

AADHI_BG = _AADHI["background"]
AADHI_GOLD = "#FFD700"
AADHI_PURPLE = "#B026FF"
AADHI_CYAN = "#00E5FF"
AADHI_WHITE = "#FFFFFF"
AADHI_MUTED = "#B9A7D6"
AADHI_GREEN = "#3DDC97"
AADHI_RED = "#FF5C7A"
AADHI_ORANGE = "#FF9F43"
AADHI_PINK = "#FF6AD5"
AADHI_BLUE = "#4DA3FF"
AADHI_COLORS = {
    "gold": AADHI_GOLD,
    "purple": AADHI_PURPLE,
    "cyan": AADHI_CYAN,
    "white": AADHI_WHITE,
    "muted": AADHI_MUTED,
    "green": AADHI_GREEN,
    "red": AADHI_RED,
    "orange": AADHI_ORANGE,
    "pink": AADHI_PINK,
    "blue": AADHI_BLUE,
}
AADHI_PALETTE = [AADHI_CYAN, AADHI_GOLD, AADHI_PINK, AADHI_GREEN, AADHI_ORANGE, AADHI_BLUE, AADHI_PURPLE, AADHI_RED]

config.background_color = AADHI_BG
if _AADHI["pixel_width"] and _AADHI["pixel_height"]:
    config.pixel_width = int(_AADHI["pixel_width"])
    config.pixel_height = int(_AADHI["pixel_height"])
    config.frame_height = 8.0
    config.frame_width = 8.0 * int(_AADHI["pixel_width"]) / int(_AADHI["pixel_height"])


# --- TeX safety -------------------------------------------------------------------------------

_AADHI_TEX_DENY = _aadhi_re.compile(_AADHI["tex_deny"]) if _AADHI["tex_deny"] else None
_AADHI_TEX_FLAGS = [f for f in _aadhi_os.environ.get("AADHI_TEX_FLAGS", "").split() if f.startswith("-")]
_AADHI_TEX_PROBE = config.tex_template.get_texcode_for_expression("AADHIPROBE")
_AADHI_TEX_ENGINE = (config.tex_template.tex_compiler, config.tex_template.output_format)
_aadhi_orig_generate_tex_file = _aadhi_tfw.generate_tex_file
_aadhi_orig_make_cmd = _aadhi_tfw.make_tex_compilation_command
# MathTex wraps every part in these exact dvisvgm group markers itself; they are the only
# \special allowed through.
_AADHI_MATHTEX_MARKERS = _aadhi_re.compile(
    r"\\special\{dvisvgm:raw <g id='unique\d{3}(?:substring)?'>\}|\\special\{dvisvgm:raw </g>\}"
)


def _aadhi_generate_tex_file(expression, environment=None, tex_template=None):
    for part in (_AADHI_MATHTEX_MARKERS.sub("", str(expression)), environment or ""):
        if _AADHI_TEX_DENY is not None and _AADHI_TEX_DENY.search(str(part)):
            raise ValueError(
                "LaTeX expression uses a forbidden construct (file access, catcode tricks and "
                f"macro definitions are not allowed): {str(part)[:120]!r}"
            )
    template = tex_template if tex_template is not None else config.tex_template
    if (
        template.get_texcode_for_expression("AADHIPROBE") != _AADHI_TEX_PROBE
        or (template.tex_compiler, template.output_format) != _AADHI_TEX_ENGINE
    ):
        raise ValueError("custom TeX templates are not allowed")
    return _aadhi_orig_generate_tex_file(expression, environment, tex_template)


def _aadhi_make_cmd(tex_compiler, output_format, tex_file, tex_dir):
    cmd = list(_aadhi_orig_make_cmd(tex_compiler, output_format, tex_file, tex_dir))
    if _AADHI_TEX_FLAGS and cmd:
        cmd = [cmd[0], *_AADHI_TEX_FLAGS, *cmd[1:]]
    return cmd


_AADHI_DVISVGM_FLAGS = [f for f in _aadhi_os.environ.get("AADHI_DVISVGM_FLAGS", "").split() if f.startswith("-")]


class _AadhiTexSubprocess:
    """``subprocess`` as seen by manim's TeX writer: ``dvisvgm`` calls get ``AADHI_DVISVGM_FLAGS``."""

    def __getattr__(self, name):
        return getattr(_aadhi_subprocess, name)

    def run(self, command, *args, **kwargs):
        if _AADHI_DVISVGM_FLAGS and isinstance(command, (list, tuple)) and command and str(command[0]) == "dvisvgm":
            command = [command[0], *_AADHI_DVISVGM_FLAGS, *command[1:]]
        return _aadhi_subprocess.run(command, *args, **kwargs)


_aadhi_orig_tfw_subprocess = _aadhi_tfw.subprocess
_aadhi_tfw.generate_tex_file = _aadhi_generate_tex_file
_aadhi_tfw.make_tex_compilation_command = _aadhi_make_cmd
_aadhi_tfw.subprocess = _AadhiTexSubprocess()


# --- file access through allowed classes ----------------------------------------------------------

_aadhi_orig_code_init = Code.__init__
_aadhi_orig_add_sound = Scene.add_sound
_aadhi_orig_svg_et = _aadhi_svg_mod.ET
_aadhi_orig_raster_paths = (_aadhi_camera_mod.get_full_raster_image_path, _aadhi_image_mod.get_full_raster_image_path)


def _aadhi_check_svg_source(source):
    # Text, MarkupText and (Math)Tex render their own SVG files into these directories; any other
    # SVG/XML file (a host file reached through a subclass or an overridden get_file_path) is refused.
    resolved = _aadhi_pathlib.Path(_aadhi_os.fspath(source)).resolve()
    roots = [_aadhi_pathlib.Path(config.get_dir(key)).resolve() for key in ("text_dir", "tex_dir")]
    if not any(root in resolved.parents for root in roots):
        raise ValueError("SVG files are not allowed in Aadhi scenes (only text and formulas rendered by Manim)")


class _AadhiSvgElementTree:
    """``xml.etree.ElementTree`` as seen by manim's SVG loader: ``parse`` only reads Manim's own SVGs."""

    def __getattr__(self, name):
        return getattr(_aadhi_orig_svg_et, name)

    def parse(self, source, *args, **kwargs):
        _aadhi_check_svg_source(source)
        return _aadhi_orig_svg_et.parse(source, *args, **kwargs)


def _aadhi_no_image_files(*args, **kwargs):
    raise ValueError("image files are not allowed in Aadhi scenes (no background images or ImageMobject)")


_aadhi_svg_mod.ET = _AadhiSvgElementTree()
_aadhi_camera_mod.get_full_raster_image_path = _aadhi_no_image_files
_aadhi_image_mod.get_full_raster_image_path = _aadhi_no_image_files


def _aadhi_code_init(self, code_file=None, *args, **kwargs):
    if code_file is not None:
        raise ValueError("Code(code_file=...) is not allowed in Aadhi scenes: pass the source as code_string=...")
    _aadhi_orig_code_init(self, None, *args, **kwargs)


def _aadhi_add_sound(self, *args, **kwargs):
    raise ValueError("sounds are not allowed in Aadhi scenes (the narration is added separately)")


Code.__init__ = _aadhi_code_init
Scene.add_sound = _aadhi_add_sound


# --- error reporting --------------------------------------------------------------------------


def _aadhi_report_error(exc):
    try:
        script = _aadhi_os.path.abspath(__file__)
        offset = int(_AADHI["user_line_offset"])
        frames = []
        for fr in _aadhi_traceback.extract_tb(exc.__traceback__):
            if _aadhi_os.path.abspath(fr.filename) == script and fr.lineno > offset:
                frames.append(f"  line {fr.lineno - offset}, in {fr.name}: {(fr.line or '').strip()}")
        message = "".join(_aadhi_traceback.format_exception_only(type(exc), exc)).strip()
        report = "\n".join(["[AADHI_ERROR]", *frames[-8:], message[:3000], "[/AADHI_ERROR]"])
        _aadhi_sys.stderr.write("\n" + report + "\n")
        _aadhi_sys.stderr.flush()
    except Exception:  # pragma: no cover - reporting must never mask the real error
        pass


# --- fonts ------------------------------------------------------------------------------------

_AADHI_SCRIPT_RANGES = (
    (0x0B80, 0x0BFF, "tamil"),
    (0x0900, 0x097F, "devanagari"),
    (0x0C00, 0x0C7F, "telugu"),
    (0x0C80, 0x0CFF, "kannada"),
    (0x0D00, 0x0D7F, "malayalam"),
    (0x0980, 0x09FF, "bengali"),
    (0x0A80, 0x0AFF, "gujarati"),
    (0x0A00, 0x0A7F, "gurmukhi"),
    (0x0B00, 0x0B7F, "oriya"),
)
try:
    import manimpango as _aadhi_pango

    _AADHI_FONTS_AVAILABLE = frozenset(_aadhi_pango.list_fonts())
except Exception:  # pragma: no cover - fonts are optional
    _AADHI_FONTS_AVAILABLE = frozenset()


def _aadhi_pick_font(candidates):
    for name in candidates:
        if name in _AADHI_FONTS_AVAILABLE:
            return name
    return ""


_AADHI_FONT_BY_SCRIPT = {name: _aadhi_pick_font(c) for name, c in (_AADHI["script_fonts"] or {}).items()}


def aadhi_script_of(text):
    """Dominant Indic script of ``text`` ('latin' when none)."""
    counts = {}
    for ch in str(text):
        cp = ord(ch)
        if cp < 0x0900 or cp > 0x0D7F:
            continue
        for lo, hi, name in _AADHI_SCRIPT_RANGES:
            if lo <= cp <= hi:
                counts[name] = counts.get(name, 0) + 1
                break
    if not counts:
        return "latin"
    return max(counts, key=counts.get)


def aadhi_font_for(text):
    """Installed font for the script of ``text`` ('' = Pango default with automatic fallback)."""
    return _AADHI_FONT_BY_SCRIPT.get(aadhi_script_of(text)) or _AADHI_FONT_BY_SCRIPT.get("latin") or ""


# --- LaTeX -> plain text fallback ---------------------------------------------------------------

_AADHI_TEX_SYMBOLS = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "varepsilon": "ε",
    "zeta": "ζ", "eta": "η", "theta": "θ", "vartheta": "ϑ", "iota": "ι", "kappa": "κ",
    "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ", "pi": "π", "rho": "ρ", "sigma": "σ",
    "tau": "τ", "upsilon": "υ", "phi": "φ", "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω",
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ", "Pi": "Π", "Sigma": "Σ",
    "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω", "cdot": "·", "times": "×", "div": "÷", "pm": "±",
    "mp": "∓", "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥", "neq": "≠", "ne": "≠",
    "approx": "≈", "equiv": "≡", "sim": "∼", "propto": "∝", "infty": "∞", "to": "→",
    "rightarrow": "→", "leftarrow": "←", "Rightarrow": "⇒", "Leftarrow": "⇐", "iff": "⇔",
    "Leftrightarrow": "⇔", "implies": "⇒", "sum": "Σ", "prod": "Π", "int": "∫", "oint": "∮",
    "partial": "∂", "nabla": "∇", "degree": "°", "circ": "°", "in": "∈", "notin": "∉",
    "subset": "⊂", "subseteq": "⊆", "cup": "∪", "cap": "∩", "emptyset": "∅", "forall": "∀",
    "exists": "∃", "neg": "¬", "lnot": "¬", "land": "∧", "wedge": "∧", "lor": "∨", "vee": "∨",
    "oplus": "⊕", "angle": "∠", "perp": "⊥", "parallel": "∥", "ldots": "…", "cdots": "⋯",
    "dots": "…", "prime": "′", "hbar": "ħ", "ell": "ℓ", "Re": "ℜ", "Im": "ℑ", "star": "⋆",
    "quad": "  ", "qquad": "    ", "lim": "lim", "sin": "sin", "cos": "cos", "tan": "tan",
    "log": "log", "ln": "ln", "exp": "exp", "max": "max", "min": "min", "det": "det",
}
_AADHI_SUP = str.maketrans("0123456789+-=()ni", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ")
_AADHI_SUB = str.maketrans("0123456789+-=()", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎")
_AADHI_SUP_OK = set("0123456789+-=()ni")
_AADHI_SUB_OK = set("0123456789+-=()")


def _aadhi_paren(s):
    s = s.strip()
    return s if len(s) <= 1 or _aadhi_re.fullmatch(r"[A-Za-z0-9.]+", s) else f"({s})"


def _aadhi_script(s, table, ok, mark):
    s = s.strip()
    if s and all(c in ok for c in s):
        return s.translate(table)
    return f"{mark}{_aadhi_paren(s)}"


def aadhi_tex_to_plain(tex):
    """Best-effort readable Unicode rendering of simple LaTeX (used when LaTeX is unavailable)."""
    s = str(tex)
    s = _aadhi_re.sub(r"\\(left|right|big|Big|bigg|Bigg)\b\.?", "", s)
    s = _aadhi_re.sub(r"\\[,;:! ]", " ", s)
    s = s.replace("\\\\", "\n").replace("&", "")
    styled = r"\\(?:text|mathrm|mathbf|mathit|mathsf|mathtt|operatorname|textbf|textit|boldsymbol|vec|hat|bar|overline|underline|mathcal|mathbb)\{([^{}]*)\}"
    for _ in range(12):
        before = s
        s = _aadhi_re.sub(r"\\[dt]?frac\{([^{}]*)\}\{([^{}]*)\}", lambda m: f"{_aadhi_paren(m.group(1))}/{_aadhi_paren(m.group(2))}", s)
        s = _aadhi_re.sub(r"\\sqrt\{([^{}]*)\}", lambda m: "√" + _aadhi_paren(m.group(1)), s)
        s = _aadhi_re.sub(styled, r"\1", s)
        s = _aadhi_re.sub(r"\^\{([^{}]*)\}", lambda m: _aadhi_script(m.group(1), _AADHI_SUP, _AADHI_SUP_OK, "^"), s)
        s = _aadhi_re.sub(r"_\{([^{}]*)\}", lambda m: _aadhi_script(m.group(1), _AADHI_SUB, _AADHI_SUB_OK, "_"), s)
        if s == before:
            break
    s = _aadhi_re.sub(r"\^([A-Za-z0-9])", lambda m: _aadhi_script(m.group(1), _AADHI_SUP, _AADHI_SUP_OK, "^"), s)
    s = _aadhi_re.sub(r"_([A-Za-z0-9])", lambda m: _aadhi_script(m.group(1), _AADHI_SUB, _AADHI_SUB_OK, "_"), s)
    s = _aadhi_re.sub(r"\\([A-Za-z]+)", lambda m: _AADHI_TEX_SYMBOLS.get(m.group(1), m.group(1)), s)
    s = s.replace("{", "").replace("}", "").replace("\\", "")
    s = _aadhi_re.sub(r"[ \t]+", " ", s)
    return s.strip() or " "


_AADHI_TEX_OPERATORS = {
    "times", "cdot", "div", "pm", "mp", "leq", "le", "geq", "ge", "neq", "ne", "approx", "equiv",
    "to", "rightarrow", "Rightarrow", "implies", "propto",
}


def aadhi_tex_parts(tex):
    """Split TeX at top-level operators (=, +, -, <, >, \\times, \\cdot ...) for TransformMatchingTex."""
    tex = str(tex)
    if any(token in tex for token in ("\\left", "\\right", "\\begin", "&", "\\\\", "%")):
        return [tex]
    parts, current, depth, i = [], "", 0, 0
    while i < len(tex):
        ch = tex[i]
        if ch == "\\":
            m = _aadhi_re.match(r"\\([A-Za-z]+|.)", tex[i:])
            token = m.group(0)
            if depth == 0 and m.group(1) in _AADHI_TEX_OPERATORS:
                parts.extend([current, token])
                current = ""
            else:
                current += token
            i += len(token)
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        if depth == 0 and ch in "=+-<>" and not current.rstrip().endswith(("^", "_")):
            parts.extend([current, ch])
            current = ""
        else:
            current += ch
        i += 1
    parts.append(current)
    parts = [p.strip() for p in parts if p.strip()]
    return parts or [tex]


# --- the base scene -----------------------------------------------------------------------------


class AadhiScene(MovingCameraScene):
    """Base class of every Aadhi scene: beat timing, safe areas, theme, fonts."""

    BEAT_TIMES = BEAT_TIMES
    TOTAL_DURATION = TOTAL_DURATION
    TARGET = _AADHI["target"]
    IS_PANEL = _AADHI["target"] == "panel"
    LANGUAGE = _AADHI["language"]
    HAS_LATEX = bool(_AADHI["has_latex"])
    MARGIN = 0.35
    TITLE_ZONE = 0.95
    NOTE_ZONE = 1.05
    FONT_SIZES = {"title": 40, "heading": 34, "label": 30, "body": 28, "note": 26, "small": 22, "math": 44}

    def setup(self):
        super().setup()
        self.camera.background_color = AADHI_BG
        self.title_mob = None
        self.note_mob = None
        self.uses_title = False
        self.uses_notes = False
        self.step_total = len(self.BEAT_TIMES)

    def render(self, preview=False):
        try:
            return super().render(preview)
        except BaseException as exc:
            _aadhi_report_error(exc)
            raise

    def tear_down(self):
        super().tear_down()
        self.finish()

    # --- timing ---------------------------------------------------------------------------
    @property
    def frame_dt(self):
        """Duration of one video frame in seconds."""
        return 1.0 / float(config.frame_rate)

    def now(self):
        """Current scene time in seconds (exact, from the renderer)."""
        return float(self.renderer.time)

    def declare_steps(self, n):
        """Tell the scene how many animation steps it has (steps beyond the beats share the tail)."""
        self.step_total = max(int(n), 0)

    def beat_time(self, i):
        """Start time of beat ``i`` (extra steps without a beat are spread over the remaining time)."""
        times = self.BEAT_TIMES
        i = max(int(i), 0)
        if i < len(times):
            return max(0.0, float(times[i]))
        total_steps = max(self.step_total, i + 1)
        if not times:
            return self.TOTAL_DURATION * i / max(total_steps, 1)
        last = float(times[-1])
        extra = total_steps - len(times)
        k = i - len(times) + 1
        return last + max(0.0, self.TOTAL_DURATION - last) * k / (extra + 1)

    def beat_window(self, i):
        """Seconds from the start of beat ``i`` to the next beat (or the end of the scene)."""
        n = max(self.step_total, len(self.BEAT_TIMES))
        end = self.beat_time(i + 1) if i + 1 < n else self.TOTAL_DURATION
        return max(end - self.beat_time(i), 0.0)

    def is_static(self):
        """True when nothing in the scene has time-based updaters (a wait can freeze the frame)."""
        if self.always_update_mobjects or self.updaters:
            return False
        return not any(m.has_time_based_updater() for m in self.get_mobject_family_members())

    def hold_frames(self, frames):
        """Hold the scene (updaters keep running) for exactly ``frames`` video frames.

        Manim renders ``int(d / dt)`` frames for a frozen wait but ``ceil(d / dt)`` for a wait with
        updaters, and both are fragile at exact multiples; half-frame margins make the count exact.
        """
        frames = int(frames)
        if frames <= 0:
            return
        dt = self.frame_dt
        if self.is_static():
            self.wait((frames + 0.5) * dt, frozen_frame=True)
        else:
            self.wait(dt if frames == 1 else (frames - 0.5) * dt, frozen_frame=False)

    def frames_until(self, t):
        """Whole frames from now until scene time ``t`` (rounded to the nearest frame; >= 0)."""
        return max(int(round((float(t) - self.now()) / self.frame_dt)), 0)

    def wait_until_beat(self, i):
        """Hold the current frame until beat ``i`` starts (no-op when already there or late)."""
        self.hold_frames(self.frames_until(self.beat_time(i)))

    def step_run_time(self, i, preferred=1.0, fraction=0.75):
        """A run time for step ``i`` that ends well before the next beat starts."""
        left = self.beat_window(i) - max(0.0, self.now() - self.beat_time(i))
        return max(min(float(preferred), left * fraction), self.frame_dt)

    def play_step(self, i, *animations, preferred=1.0, **kwargs):
        """``wait_until_beat(i)`` then play ``animations`` so they finish inside beat ``i``."""
        self.wait_until_beat(i)
        if animations:
            self.play(*animations, run_time=self.step_run_time(i, preferred), **kwargs)

    def finish(self):
        """Hold the last frame until ``TOTAL_DURATION`` (idempotent; exact to the frame)."""
        frames = self.frames_until(self.TOTAL_DURATION)
        if frames <= 0 and self.renderer.num_plays == 0:
            frames = 1  # a scene that never played anything still needs one frame of video
        self.hold_frames(frames)

    # --- layout -----------------------------------------------------------------------------
    def frame_size(self):
        """(width, height) of the visible frame in scene units."""
        return float(config.frame_width), float(config.frame_height)

    def content_box(self, title=None, notes=None):
        """(cx, cy, width, height) of the safe content area (below the title, above the notes)."""
        fw, fh = self.frame_size()
        title = self.uses_title if title is None else title
        notes = self.uses_notes if notes is None else notes
        top = fh / 2 - self.MARGIN - (self.TITLE_ZONE if title else 0.0)
        bottom = -fh / 2 + self.MARGIN + (self.NOTE_ZONE if notes else 0.0)
        left = -fw / 2 + self.MARGIN
        right = fw / 2 - self.MARGIN
        return (left + right) / 2, (top + bottom) / 2, right - left, top - bottom

    def content_center(self):
        """Centre point of the safe content area."""
        cx, cy, _w, _h = self.content_box()
        return np.array([cx, cy, 0.0])

    def fit_to_safe_area(self, mobj, grow=False, max_grow=1.6, title=None, notes=None):
        """Scale ``mobj`` down (or up when ``grow``) to fit the content area and centre it there."""
        cx, cy, w, h = self.content_box(title, notes)
        width = max(float(mobj.width), 1e-6)
        height = max(float(mobj.height), 1e-6)
        factor = min(w / width, h / height)
        if factor < 1.0:
            mobj.scale(factor)
        elif grow and factor > 1.0:
            mobj.scale(min(factor, max_grow))
        mobj.move_to(np.array([cx, cy, 0.0]))
        return mobj

    def color(self, name, default=AADHI_WHITE):
        """Theme colour by name (gold, purple, cyan, white, muted, green, red, orange, pink, blue)."""
        return AADHI_COLORS.get(str(name or "").lower(), default)

    def palette(self, i):
        """The i-th colour of the theme palette (cycled)."""
        return AADHI_PALETTE[int(i) % len(AADHI_PALETTE)]

    # --- text -------------------------------------------------------------------------------
    def text_size(self, role="body"):
        """Font size for a role: title, heading, label, body, note, small, math."""
        size = self.FONT_SIZES.get(role, 28)
        return size * (0.9 if self.IS_PANEL else 1.0)

    def label(self, text, role="label", color=AADHI_WHITE, size=None, weight=NORMAL, max_width=None):
        """Themed ``Text`` using an installed font for the text's script (Tamil, Devanagari...)."""
        text = str(text)
        if not text.strip():
            return VGroup()
        mob = Text(text, font=aadhi_font_for(text), font_size=size or self.text_size(role), color=color, weight=weight)
        if max_width and mob.width > max_width:
            mob.scale_to_fit_width(max_width)
        return mob

    def paragraph(self, text, width=None, role="body", color=AADHI_WHITE, size=None, max_lines=4):
        """Word-wrapped ``Text`` that fits ``width`` scene units in at most ``max_lines`` lines.

        The text is measured once on a single line; when it is too wide it is split into the smallest
        number of balanced lines that fit (no orphan words). Nothing is ever dropped: if it needs more
        than ``max_lines`` lines, the lines get longer and the result is scaled down to ``width``.
        """
        text = " ".join(str(text).split())
        if not text:
            return VGroup()
        fw, _fh = self.frame_size()
        width = width or (fw - 2 * self.MARGIN)
        font_size = size or self.text_size(role)
        font = aadhi_font_for(text)
        single = Text(text, font=font, font_size=font_size, color=color)
        if single.width <= width:
            return single
        count = min(max(1, int(max_lines)), int(np.ceil(single.width / width)))
        chars = max(6, int(np.ceil(len(text) / count)))
        lines = _aadhi_textwrap.wrap(text, width=chars, break_long_words=False) or [text]
        while len(lines) > count:
            chars += 1
            lines = _aadhi_textwrap.wrap(text, width=chars, break_long_words=False) or [text]
        mob = Text("\n".join(lines), font=font, font_size=font_size, color=color, line_spacing=0.9)
        if mob.width > width:
            mob.scale_to_fit_width(width)
        return mob

    def math(self, tex, role="math", color=AADHI_WHITE, size=None, split=False):
        """``MathTex`` (or a readable Unicode ``Text`` fallback when LaTeX is unavailable).

        ``split=True`` isolates top-level terms so ``TransformMatchingTex`` can match them.
        """
        size = size or self.text_size(role)
        if self.HAS_LATEX:
            parts = aadhi_tex_parts(tex) if split else [tex]
            return MathTex(*parts, font_size=size, color=color)
        plain = aadhi_tex_to_plain(tex)
        return Text(plain, font=aadhi_font_for(plain), font_size=size * 0.8, color=color)

    def show_title(self, text, color=AADHI_GOLD):
        """Add a title in the title zone at the current time (no animation)."""
        self.uses_title = True
        if not str(text).strip():
            return VGroup()
        fw, fh = self.frame_size()
        mob = self.label(text, role="title", color=color, weight=BOLD, max_width=fw - 2 * self.MARGIN)
        mob.move_to(np.array([0.0, fh / 2 - self.MARGIN - self.TITLE_ZONE / 2 + 0.08, 0.0]))
        rule_w = min(max(mob.width, 1.0), fw - 2 * self.MARGIN)
        rule = Line(LEFT * rule_w / 2, RIGHT * rule_w / 2, color=AADHI_PURPLE, stroke_width=3)
        rule.next_to(mob, DOWN, buff=0.1)
        group = VGroup(mob, rule)
        self.add(group)
        self.title_mob = group
        return group

    def reserve_notes(self):
        """Reserve the bottom note zone so content never overlaps step notes."""
        self.uses_notes = True

    def note(self, text, color=AADHI_WHITE):
        """Animations replacing the current step note (bottom zone) with ``text`` ('' clears it)."""
        self.uses_notes = True
        animations = []
        if self.note_mob is not None:
            animations.append(FadeOut(self.note_mob))
            self.note_mob = None
        if str(text or "").strip():
            fw, fh = self.frame_size()
            mob = self.paragraph(text, width=fw - 2 * self.MARGIN - 0.3, role="note", color=color, max_lines=2)
            if mob.height > self.NOTE_ZONE - 0.15:
                mob.scale_to_fit_height(self.NOTE_ZONE - 0.15)
            mob.move_to(np.array([0.0, -fh / 2 + self.MARGIN + self.NOTE_ZONE / 2, 0.0]))
            animations.append(FadeIn(mob, shift=UP * 0.1))
            self.note_mob = mob
        return animations

    def highlight_box(self, mobj, color=AADHI_GOLD, buff=0.12):
        """Rounded highlight rectangle around ``mobj``."""
        return SurroundingRectangle(mobj, color=color, buff=buff, corner_radius=0.08, stroke_width=4)


# --- export allow-list for scene code -------------------------------------------------------------
# Scene code starts with `from manim import *`. Narrow manim's star export to what scenes need
# (deny by default): anything else - file/process/logging helpers, plugins, renderers, image/SVG
# loaders and every module object except numpy and the rate functions - is not importable and is
# removed from this namespace. The runtime above only uses names that stay exported.

_AADHI_EXPORT_MODULES = frozenset({"np", "rate_functions"})
_AADHI_EXPORT_ORIGIN_PREFIXES = ("manim.animation.", "manim.mobject.", "manim.utils.color.")
_AADHI_EXPORT_ORIGINS = frozenset(
    {
        "manim.constants", "manim.utils.bezier", "manim.utils.config_ops", "manim.utils.debug",
        "manim.utils.iterables", "manim.utils.paths", "manim.utils.rate_functions", "manim.utils.simple_functions",
        "manim.utils.space_ops", "manim.scene.scene", "manim.scene.moving_camera_scene", "manim.scene.three_d_scene",
        "manim.scene.vector_space_scene", "manim.scene.zoomed_scene",
    }
)
_AADHI_HIDDEN_ORIGIN_PREFIXES = (
    "manim.mobject.types.image_mobject", "manim.mobject.svg.svg_mobject", "manim.mobject.text.typst_mobject",
    "manim.mobject.opengl.",
)
_AADHI_HIDDEN_NAMES = frozenset({"register_font"})
_AADHI_EXPORT_DATA_TYPES = (int, float, complex, str, np.ndarray, ManimColor, _aadhi_types.UnionType)


def _aadhi_exportable(name, value):
    if name.startswith("_") or name in _AADHI_HIDDEN_NAMES:
        return False
    if name == "config":
        return True  # read-only geometry for scene code (enforced by the AST guard)
    if isinstance(value, _aadhi_types.ModuleType):
        return name in _AADHI_EXPORT_MODULES
    if callable(value):
        origin = getattr(value, "__module__", None) or ""
        if origin.startswith(_AADHI_HIDDEN_ORIGIN_PREFIXES):
            return False
        return origin in _AADHI_EXPORT_ORIGINS or origin.startswith(_AADHI_EXPORT_ORIGIN_PREFIXES)
    return isinstance(value, _AADHI_EXPORT_DATA_TYPES)


_aadhi_orig_manim_all = getattr(_aadhi_manim, "__all__", None)
_AADHI_EXPORTS = frozenset(n for n, v in vars(_aadhi_manim).items() if _aadhi_exportable(n, v))
_aadhi_manim.__all__ = sorted(_AADHI_EXPORTS)
for _aadhi_name in [n for n in list(globals()) if not n.startswith("_") and n in vars(_aadhi_manim)]:
    if _aadhi_name not in _AADHI_EXPORTS and globals()[_aadhi_name] is vars(_aadhi_manim)[_aadhi_name]:
        del globals()[_aadhi_name]
