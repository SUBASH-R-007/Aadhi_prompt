"""Manim request security (Phase 10): render profiles and their hard limits, static checks of generated
code, the code fix-ups the old pipeline applied, and the deterministic fingerprint of a render request.

Generated Manim code is UNTRUSTED. The static checks here are an early, explainable rejection layer only:
Python cannot be made safe by inspecting its source. The security boundary is the sandbox
(manim_sandbox.py: OS isolation of files, network and processes, plus resource limits).
"""
import ast
import hashlib
import json
import os
import re

MANIM_VERSION = "0.21.0"  # the version installed and tested; recorded in every fingerprint and asset


def _env_int(name, default, env=None):
    env = os.environ if env is None else env
    try:
        return int(float(env.get(name) or default))
    except ValueError:
        return default


# ---- render profiles ------------------------------------------------------------------------------------

# Hard maximums: configuration may lower them, never raise them past these
HARD_MAX = {"width": 1920, "height": 1080, "fps": 60, "duration": 300, "timeout": 900, "memory_mb": 4096,
            "output_mb": 500, "workspace_mb": 1500, "processes": 64, "source_kb": 200, "scenes": 5, "cpu_percent": 100}

PROFILES = {
    # name: resolution, frame rate, render limits
    "preview": {"width": 854, "height": 480, "fps": 15, "timeout": 90, "memory_mb": 1024, "max_duration": 45},
    "standard": {"width": 1280, "height": 720, "fps": 30, "timeout": 180, "memory_mb": 1536, "max_duration": 60},  # the old -qm
    "high_quality": {"width": 1920, "height": 1080, "fps": 30, "timeout": 300, "memory_mb": 2048, "max_duration": 60},
}
DEFAULT_PROFILE = "standard"
BACKGROUND = "#1A0B2E"  # the lesson's board colour (the old pipeline set it on every scene)


def limits(profile_name=DEFAULT_PROFILE, env=None):
    """The limits one render runs under: the profile, lowered by configuration, capped by HARD_MAX.
    Nothing a request or the generated code says can raise them."""
    if profile_name not in PROFILES:
        raise ValueError(f"unknown render profile {profile_name!r}")
    p = PROFILES[profile_name]
    cap = lambda value, name: max(1, min(value, HARD_MAX[name]))  # noqa: E731
    max_fps = cap(_env_int("MANIM_MAX_FPS", HARD_MAX["fps"], env), "fps")
    max_duration = cap(_env_int("MANIM_MAX_DURATION", HARD_MAX["duration"], env), "duration")
    max_height = cap(_env_int("MANIM_MAX_RESOLUTION", HARD_MAX["height"], env), "height")
    height = min(p["height"], max_height)
    width = min(p["width"], HARD_MAX["width"], int(round(height * 16 / 9 / 2)) * 2)
    fps = min(p["fps"], max_fps)
    duration = min(p["max_duration"], max_duration)
    return {
        "profile": profile_name, "width": width, "height": height, "fps": fps, "max_duration": duration,
        "max_frames": int(duration * fps),
        "timeout": cap(_env_int("MANIM_TIMEOUT", p["timeout"], env), "timeout"),
        "memory_mb": cap(_env_int("MANIM_MEMORY_LIMIT_MB", p["memory_mb"], env), "memory_mb"),
        "cpu_percent": cap(_env_int("MANIM_CPU_LIMIT_PERCENT", 50, env), "cpu_percent"),
        "processes": cap(_env_int("MANIM_MAX_PROCESSES", 1, env), "processes"),
        "output_mb": cap(_env_int("MANIM_MAX_OUTPUT_MB", 150, env), "output_mb"),
        "workspace_mb": cap(_env_int("MANIM_MAX_WORKSPACE_MB", 800, env), "workspace_mb"),
        "source_kb": cap(_env_int("MANIM_MAX_SOURCE_KB", 100, env), "source_kb"),
        "max_scenes": cap(_env_int("MANIM_MAX_SCENES", 3, env), "scenes"),
        "background": BACKGROUND,
    }


# ---- static checks -----------------------------------------------------------------------------------------

class UnsafeCode(ValueError):
    """The code asks for something the sandbox does not allow (category + a safe message)."""

    def __init__(self, category, message, detail=""):
        super().__init__(message)
        self.category = category
        self.message = message
        self.detail = detail


# What educational Manim scenes need; everything else is refused before it reaches the sandbox
ALLOWED_IMPORTS = {"manim", "math", "cmath", "numpy", "random", "itertools", "functools", "operator", "typing",
                   "colorsys", "fractions", "decimal", "statistics", "string", "collections", "copy", "enum",
                   "dataclasses", "numbers", "abc", "__future__"}
FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__", "open", "input", "breakpoint", "globals", "locals",
                   "vars", "memoryview", "help", "exit", "quit", "setattr", "delattr"}
FORBIDDEN_NAMES = {"__builtins__", "__loader__", "__spec__", "builtins", "os", "sys", "subprocess", "socket",
                   "ctypes", "importlib", "shutil", "pathlib"}
# numpy / manim attributes that reach the file system or the interpreter
FORBIDDEN_ATTRIBUTES = {"system", "popen", "spawn", "fork", "exec", "load", "loadtxt", "genfromtxt", "fromfile",
                        "tofile", "save", "savez", "savetxt", "memmap", "ctypeslib", "lib", "ctypes"}
MAX_AST_NODES = 60000


def check_source(code, lim):
    """Early rejection of generated code (defence in depth; the sandbox is the boundary). Returns the Scene
    class names, or raises UnsafeCode."""
    if not isinstance(code, str) or not code.strip():
        raise UnsafeCode("invalid_code", "The animation code is empty.")
    if len(code.encode("utf-8")) > lim["source_kb"] * 1024:
        raise UnsafeCode("source_limit", f"The animation code is longer than {lim['source_kb']} KB.")
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise UnsafeCode("invalid_code", f"The animation code is not valid Python (line {e.lineno}).")
    nodes = 0
    for node in ast.walk(tree):
        nodes += 1
        if nodes > MAX_AST_NODES:
            raise UnsafeCode("source_limit", "The animation code is too large to check.")
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            if isinstance(node, ast.ImportFrom) and node.level:
                raise UnsafeCode("unsafe_code", "This animation requested an operation that is not allowed in the Manim sandbox.",
                                 "relative import")
            for name in names:
                if name.split(".")[0] not in ALLOWED_IMPORTS:
                    raise UnsafeCode("unsafe_code", "This animation requested an operation that is not allowed in the Manim sandbox.",
                                     f"import {name}")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALLS:
            raise UnsafeCode("unsafe_code", "This animation requested an operation that is not allowed in the Manim sandbox.",
                             f"call {node.func.id}()")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            raise UnsafeCode("unsafe_code", "This animation requested an operation that is not allowed in the Manim sandbox.",
                             f"name {node.id}")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__") and node.attr.endswith("__") and node.attr not in ("__init__", "__name__"):
                raise UnsafeCode("unsafe_code", "This animation requested an operation that is not allowed in the Manim sandbox.",
                                 f"attribute {node.attr}")
            if node.attr in FORBIDDEN_ATTRIBUTES:
                raise UnsafeCode("unsafe_code", "This animation requested an operation that is not allowed in the Manim sandbox.",
                                 f"attribute {node.attr}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and re.match(r"^__\w+__$", node.value):
            raise UnsafeCode("unsafe_code", "This animation requested an operation that is not allowed in the Manim sandbox.",
                             "dunder string")
    scenes = [n.name for n in tree.body if isinstance(n, ast.ClassDef) and any(
        (isinstance(b, ast.Name) and b.id.endswith("Scene")) or (isinstance(b, ast.Attribute) and b.attr.endswith("Scene"))
        for b in n.bases)]
    if not scenes:
        raise UnsafeCode("invalid_code", "Could not find a valid Manim Scene class in the provided code.")
    if len(scenes) > lim["max_scenes"]:
        raise UnsafeCode("scene_limit", f"The animation defines {len(scenes)} scenes; at most {lim['max_scenes']} are allowed.")
    return scenes


# ---- fix-ups of common generation mistakes (moved from the old /render; content, not security) -----------------

FIXUPS = {
    r'\.get_top_left\(\)': '.get_corner(UL)', r'\.get_top_right\(\)': '.get_corner(UR)',
    r'\.get_bottom_left\(\)': '.get_corner(DL)', r'\.get_bottom_right\(\)': '.get_corner(DR)',
    r'\.get_top_left\b': '.get_corner(UL)', r'\.get_top_right\b': '.get_corner(UR)',
    r'\.get_bottom_left\b': '.get_corner(DL)', r'\.get_bottom_right\b': '.get_corner(DR)',
    r'\.reverse_path\(\)': '.reverse_direction()',
    r'SVGMobject\([^)]+\)': 'Square(side_length=1).set_fill(WHITE, opacity=0.2)',
    r'ImageMobject\([^)]+\)': 'Rectangle(width=1.5, height=1).set_fill(WHITE, opacity=0.2)',
    r'GrowArrow\(': 'Create(', r',\s*scale_tips=True': '', r',\s*stroke_dash_2darray=\[[^\]]*\]': '',
}


def fix_code(code):
    """The old pipeline's fix-ups: file-based mobjects become shapes (the sandbox has no files to load), a few
    renamed methods, Rectangle(corner_radius=...) → RoundedRectangle. Line endings unified."""
    code = (code or "").replace("\r\n", "\n").replace("\r", "\n")
    for pattern, repl in FIXUPS.items():
        code = re.sub(pattern, repl, code)
    out, idx = [], 0
    for match in re.finditer(r"(?<![A-Za-z0-9_])Rectangle\s*\(", code):
        start = match.end()
        depth, i = 1, start
        while i < len(code) and depth:
            depth += {"(": 1, ")": -1}.get(code[i], 0)
            i += 1
        if "corner_radius" in code[start:i - 1]:
            out.append(code[idx:match.start()] + "Rounded" + code[match.start():match.end()])
            idx = match.end()
    out.append(code[idx:])
    return "".join(out)


def scene_name(code, scenes):
    """The scene to render: the one named Scene-like class (the first if several)."""
    return scenes[0]


def fingerprint(code, lim, scene):
    """The deterministic identity of a render: the exact code (after fix-ups), the Manim version, the scene and
    the settings that change the picture. No time, path or worker is part of it."""
    data = {"code_sha256": hashlib.sha256(code.encode("utf-8")).hexdigest(), "manim": MANIM_VERSION, "scene": scene,
            "width": lim["width"], "height": lim["height"], "fps": lim["fps"], "background": lim["background"]}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest(), data
