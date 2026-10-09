"""AST allow-list for free-form Manim code (ARCHITECTURE §8).

Free-form scene code is written by an LLM, so it is treated as untrusted. ``check_code`` parses it
and walks every node; any problem is reported as a human-readable message (fed back to the model
in the repair loop). Rules:

* imports: ``from manim import *`` plus ``math``, ``numpy``, ``random``, ``itertools``,
  ``functools`` (no submodules, no relative imports, no other star imports);
* module objects are **deny by default**: a name bound to a module (``np`` and ``rate_functions``
  from ``from manim import *``, or an allowed import) may only be used as ``module.<attr>`` with an
  attribute from that module's allow-list (:data:`MODULE_ATTRS`: numeric functions and constants,
  ``np.linalg``/``np.random`` helpers, rate-function curves). Modules cannot be stored, passed,
  returned, re-bound or have attributes assigned;
* no ``open/exec/eval/compile/__import__/globals/locals/vars/input/breakpoint/getattr/setattr/
  delattr/dir/type/help/exit/quit``; none of the Manim helpers that touch files, processes, logging
  or global configuration (``SVGMobject``, ``ImageMobject``, ``capture``, ``tempconfig``...);
* no name or attribute starting with ``_`` (``_`` alone is fine), no dunder strings, no frame /
  traceback / file-I/O attributes on objects (``f_globals``, ``tofile``, ``save``, ``renderer``...);
* ``config`` is read-only and only for geometry (``config.frame_width``...);
* no keyword arguments that load files (``file_name``, ``code_file``, ``tex_template``...), no
  string literals that look like file paths or URLs, no TeX file-access macros;
* exactly one ``class <Name>(AadhiScene)`` with an ASCII name; ``super()`` only without arguments
  (``super(Text, self)`` would reach the SVG file loader); no ``global`` statements, metaclasses or
  async code.

:func:`check_template_source` checks template output: ``PARAMS = <literal>`` (validated *data*, never
code, so its strings may contain paths, URLs or dunder names) followed by the static scene file,
which gets the full free-form rules.

Runtime defences complement this list: the injected runtime exports only an allow-list of Manim
names (``from manim import *`` in the sandbox yields no file/process helpers and no module objects
besides ``np`` and ``rate_functions``), the sandbox (no network, read-only FS, env allow-list,
timeout + process-tree kill) and the runtime TeX check in ``runtime/scene_runtime.py``.
"""

from __future__ import annotations

import ast
import re

from .texcheck import MANIM_TEX_DENY_PATTERN

MAX_CODE_CHARS = 20_000
MAX_TEMPLATE_SOURCE_CHARS = 2_000_000
MAX_PROBLEMS = 20

ALLOWED_MODULES = frozenset({"math", "numpy", "random", "itertools", "functools"})

# Module objects that the sandbox runtime leaves in the namespace of ``from manim import *``.
STAR_MODULES: dict[str, str] = {"np": "numpy", "rate_functions": "rate_functions"}

_NUMPY_ATTRS = """
abs absolute add all allclose amax amin angle any append arange arccos arccosh arcsin arcsinh arctan arctan2
arctanh argmax argmin argsort argwhere around array array_equal array_split asarray atleast_1d atleast_2d average
base_repr bincount binary_repr bitwise_and bitwise_or bitwise_xor bool_ broadcast_to cbrt ceil clip column_stack
complex128 concatenate conj conjugate convolve copy cos cosh count_nonzero cross cumprod cumsum deg2rad degrees diag
diff digitize divide dot dtype e einsum empty empty_like euler_gamma exp exp2 expand_dims expm1 eye fabs fft flip
fliplr flipud float32 float64 floating floor floor_divide fmax fmin fmod full full_like gcd geomspace gradient
heaviside histogram hstack hypot identity imag inf inner int32 int64 integer interp invert isclose isfinite isin isinf
isnan kron lcm left_shift linalg linspace log log10 log1p log2 logical_and logical_not logical_or logical_xor logspace
matmul max maximum mean median meshgrid mgrid min minimum mod moveaxis multiply nan nan_to_num nanmax nanmean nanmin
nansum ndarray ndim negative newaxis nonzero number ones ones_like outer percentile pi piecewise poly1d polyfit polyval
power prod quantile rad2deg radians random ravel real reciprocal remainder repeat reshape right_shift rint roll roots
rot90 round searchsorted select shape sign sin sinc sinh size sort split sqrt square squeeze stack std subtract sum
swapaxes tan tanh tensordot tile trace transpose tril triu true_divide trunc unique unwrap vander var vdot vectorize
vstack where zeros zeros_like
"""
_MATH_ATTRS = """
acos acosh asin asinh atan atan2 atanh cbrt ceil comb copysign cos cosh degrees dist e erf erfc exp exp2 expm1 fabs
factorial floor fma fmod frexp fsum gamma gcd hypot inf isclose isfinite isinf isnan isqrt lcm ldexp lgamma log log10
log1p log2 modf nan nextafter perm pi pow prod radians remainder sin sinh sqrt sumprod tan tanh tau trunc ulp
"""
_RATE_FUNCTIONS = """
double_smooth ease_in_back ease_in_bounce ease_in_circ ease_in_cubic ease_in_elastic ease_in_expo ease_in_out_back
ease_in_out_bounce ease_in_out_circ ease_in_out_cubic ease_in_out_elastic ease_in_out_expo ease_in_out_quad
ease_in_out_quart ease_in_out_quint ease_in_out_sine ease_in_quad ease_in_quart ease_in_quint ease_in_sine
ease_out_back ease_out_bounce ease_out_circ ease_out_cubic ease_out_elastic ease_out_expo ease_out_quad ease_out_quart
ease_out_quint ease_out_sine exponential_decay linear lingering not_quite_there running_start rush_from rush_into
slow_into smooth smoothererstep smootherstep smoothstep squish_rate_func there_and_back there_and_back_with_pause
unit_interval wiggle zero
"""

# Attribute allow-list per module (a value that is itself a key here is a sub-module).
MODULE_ATTRS: dict[str, frozenset[str]] = {
    "numpy": frozenset(_NUMPY_ATTRS.split()),
    "numpy.linalg": frozenset(
        "cholesky cond det eig eigh eigvals eigvalsh inv lstsq matrix_power matrix_rank multi_dot norm pinv qr "
        "slogdet solve svd".split()
    ),
    "numpy.fft": frozenset(
        "fft fft2 fftfreq fftshift ifft ifft2 ifftshift irfft rfft rfftfreq".split()
    ),
    "numpy.random": frozenset(
        "beta binomial choice default_rng exponential normal permutation poisson rand randint randn random "
        "random_sample seed shuffle standard_normal uniform".split()
    ),
    "math": frozenset(_MATH_ATTRS.split()),
    "random": frozenset(
        "Random betavariate choice choices expovariate gammavariate gauss lognormvariate normalvariate "
        "paretovariate randint random randrange sample seed shuffle triangular uniform vonmisesvariate "
        "weibullvariate".split()
    ),
    "itertools": frozenset(
        "accumulate batched chain combinations combinations_with_replacement compress count cycle dropwhile "
        "filterfalse groupby islice pairwise permutations product repeat starmap takewhile tee zip_longest".split()
    ),
    "functools": frozenset(
        "cache cached_property cmp_to_key lru_cache partial partialmethod reduce total_ordering wraps".split()
    ),
    "rate_functions": frozenset(_RATE_FUNCTIONS.split()),
}

FORBIDDEN_BUILTINS = frozenset(
    {
        "open", "exec", "eval", "compile", "globals", "locals", "vars", "input", "breakpoint", "getattr", "setattr",
        "delattr", "dir", "type", "help", "exit", "quit", "memoryview",
    }
)
# Manim exports that touch files, processes, logging, plugins or global configuration. The sandbox runtime
# also hides them from `from manim import *`; listing them here gives the model a clear message.
FORBIDDEN_MANIM_NAMES = frozenset(
    {
        "SVGMobject", "ImageMobject", "ImageMobjectFromCamera", "VMobjectFromSVGPath", "Typst", "MathTypst",
        "SceneFileWriter", "CairoRenderer", "tempconfig", "TexTemplate", "TexTemplateLibrary", "TexFontTemplates",
        "ManimConfig", "open_file", "capture", "get_video_metadata", "get_dir_layout", "guarantee_existence",
        "guarantee_empty_existence", "modify_atime", "write_to_movie", "ensure_executable",
        "seek_full_path_from_defaults", "get_full_raster_image_path", "get_full_sound_file_path", "register_font",
        "get_plugins", "list_plugins", "logger", "console", "error_console", "cli_ctx_settings", "CONTEXT_SETTINGS",
        "manim",
    }
)
FORBIDDEN_NAMES = FORBIDDEN_BUILTINS | FORBIDDEN_MANIM_NAMES

# Attributes refused on any object (module attributes are allow-listed separately, see MODULE_ATTRS).
FORBIDDEN_ATTRS = frozenset(
    {
        # file I/O
        "load", "loads", "save", "savez", "savez_compressed", "savetxt", "loadtxt", "genfromtxt", "fromfile",
        "fromregex", "tofile", "memmap", "dump", "dumps", "write_text", "write_bytes", "read_text", "read_bytes",
        "unlink", "rmdir", "mkdir", "chmod", "ctypes", "ctypeslib",
        # processes
        "system", "popen", "spawn", "exec", "eval", "open",
        # manim internals that read/write files, sounds, images or configuration
        "renderer", "file_writer", "add_sound", "save_image", "get_image", "show", "render", "embed",
        "interactive_embed", "tex_template", "media_dir", "digest_args", "digest_parser", "background_image",
        "color_using_background_image", "file_name", "code_file", "init_svg_mobject",
        # introspection / frame walking
        "mro", "gi_frame", "gi_code", "cr_frame", "cr_code", "ag_frame", "ag_code", "f_globals", "f_locals",
        "f_builtins", "f_back", "f_code", "tb_frame", "tb_next", "co_code", "func_globals", "func_code", "im_func",
        "im_self",
    }
)
# Attribute names that must not appear in str.format fields either ("{0.gi_frame.f_globals}").
_INTROSPECTION_ATTRS = re.compile(
    r"\{[^{}]*\.(?:mro|gi_frame|gi_code|cr_frame|cr_code|ag_frame|ag_code|f_globals|f_locals|f_builtins"
    r"|f_back|f_code|tb_frame|tb_next|co_code|func_globals|func_code|im_func|im_self)\b"
)

FORBIDDEN_KWARGS = frozenset(
    {"file_name", "code_file", "filename", "file", "svg_file", "font_path", "font_paths", "tex_template", "image",
     "filename_or_array", "sound_file", "background_image"}
)

# ``config`` may only be *read*, and only for frame geometry.
CONFIG_READ_ATTRS = frozenset(
    {
        "frame_width", "frame_height", "frame_x_radius", "frame_y_radius", "pixel_width", "pixel_height",
        "frame_rate", "background_color", "top", "bottom", "left_side", "right_side", "aspect_ratio",
    }
)

PROTECTED_NAMES = frozenset({"AadhiScene", "BEAT_TIMES", "TOTAL_DURATION", "config"})

# Scene class names reach the manim CLI as an argument: ASCII identifiers only.
SCENE_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")

_DUNDER_STRING = re.compile(r"__\w+__")
_PATH_LIKE = re.compile(
    r"^\s*(?:[A-Za-z]:[\\/]|\\\\|~[\\/]|file:|[a-z]+://|/(?:etc|usr|home|root|proc|sys|tmp|var|opt|work|mnt|dev|bin)\b)"
    r"|(?:^|[\\/])\.\.(?:[\\/]|$)"
)
_TEX_DENY = re.compile(MANIM_TEX_DENY_PATTERN)


def module_bindings(tree: ast.AST) -> dict[str, frozenset[str]]:
    """Names bound to module objects in ``tree`` -> module paths (``np`` -> {"numpy"}).

    Includes the modules the runtime provides through ``from manim import *``. A name imported twice
    (``import math as np``) maps to every module it may refer to.
    """
    bindings: dict[str, set[str]] = {name: {module} for name, module in STAR_MODULES.items()}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in ALLOWED_MODULES:
                    bindings.setdefault(alias.asname or alias.name, set()).add(alias.name)
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module in ALLOWED_MODULES:
            for alias in node.names:
                sub = f"{node.module}.{alias.name}"
                if sub in MODULE_ATTRS:
                    bindings.setdefault(alias.asname or alias.name, set()).add(sub)
    return {name: frozenset(mods) for name, mods in bindings.items()}


def _examples(module: str, limit: int = 12) -> str:
    preferred = ("array", "linspace", "sin", "cos", "sqrt", "pi", "norm", "uniform", "smooth", "reduce", "chain")
    names = sorted(MODULE_ATTRS.get(module, ()), key=lambda n: (n not in preferred, n))
    return ", ".join(names[:limit])


class _Checker(ast.NodeVisitor):
    def __init__(self, modules: dict[str, frozenset[str]]) -> None:
        self.problems: list[str] = []
        self.scene_classes: list[str] = []
        self.modules = modules
        self._config_ok: set[int] = set()
        self._module_ok: set[int] = set()

    # --- helpers ------------------------------------------------------------------------------
    def add(self, node: ast.AST, message: str) -> None:
        line = getattr(node, "lineno", None)
        text = f"line {line}: {message}" if line else message
        if text not in self.problems:
            self.problems.append(text)

    @staticmethod
    def _bad_ident(name: str) -> bool:
        return name.startswith("_") and name != "_"

    def _module_of(self, expr: ast.expr) -> frozenset[str] | None:
        """Module paths ``expr`` statically refers to (``np.linalg`` -> {"numpy.linalg"}); None otherwise."""
        if isinstance(expr, ast.Name):
            return self.modules.get(expr.id)
        if isinstance(expr, ast.Attribute):
            base = self._module_of(expr.value)
            if base:
                subs = frozenset(f"{m}.{expr.attr}" for m in base)
                if all(s in MODULE_ATTRS for s in subs):
                    return subs
        return None

    # --- imports ------------------------------------------------------------------------------
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name not in ALLOWED_MODULES:
                self.add(node, f"import of {alias.name!r} is not allowed (allowed: from manim import *, "
                         f"{', '.join(sorted(ALLOWED_MODULES))})")
            if alias.asname and (self._bad_ident(alias.asname) or alias.asname in FORBIDDEN_NAMES
                                 or alias.asname in PROTECTED_NAMES):
                self.add(node, f"import alias {alias.asname!r} is not allowed")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        names = [a.name for a in node.names]
        if node.level:
            self.add(node, "relative imports are not allowed")
        elif node.module == "manim":
            if names != ["*"]:
                self.add(node, "import manim only as `from manim import *`")
        elif node.module in ALLOWED_MODULES:
            allowed = MODULE_ATTRS[node.module]
            for alias in node.names:
                if alias.name == "*":
                    self.add(node, f"`from {node.module} import *` is not allowed")
                elif alias.name not in allowed:
                    self.add(node, f"importing {alias.name!r} from {node.module} is not allowed "
                             f"(available: {_examples(node.module)}, ...)")
                bound = alias.asname or alias.name
                if bound in PROTECTED_NAMES or bound in FORBIDDEN_NAMES or (alias.asname and self._bad_ident(alias.asname)):
                    self.add(node, f"import alias {bound!r} is not allowed")
        else:
            self.add(node, f"import from {node.module!r} is not allowed (allowed: from manim import *, "
                     f"{', '.join(sorted(ALLOWED_MODULES))})")
        self.generic_visit(node)

    # --- names / attributes -------------------------------------------------------------------
    def visit_Name(self, node: ast.Name) -> None:
        name = node.id
        if self._bad_ident(name):
            self.add(node, f"names starting with '_' are not allowed ({name!r})")
        elif name in FORBIDDEN_NAMES:
            self.add(node, f"{name!r} is not allowed in scene code")
        elif name == "config" and id(node) not in self._config_ok:
            self.add(node, "`config` may only be read as config.frame_width / frame_height / pixel_* / frame_rate")
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            if name in PROTECTED_NAMES:
                self.add(node, f"{name!r} must not be reassigned or deleted")
            elif name in self.modules:
                self.add(node, f"{name!r} is a module and must not be reassigned or deleted; use another name")
        elif name in self.modules and id(node) not in self._module_ok:
            self.add(node, f"module {name!r} can only be used as {name}.<function>(...) "
                     "(modules cannot be stored, passed or returned)")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        attr = node.attr
        is_super_init = (
            attr == "__init__"
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "super"
        )
        if self._bad_ident(attr) and not is_super_init:
            self.add(node, f"attribute names starting with '_' are not allowed (.{attr}); rename helpers "
                     "without the leading underscore")
        elif attr in FORBIDDEN_ATTRS:
            self.add(node, f"attribute .{attr} is not allowed in scene code")
        base = self._module_of(node.value)
        if base:
            self._module_ok.add(id(node.value))
            dotted = ast.unparse(node.value)
            if not isinstance(node.ctx, ast.Load):
                self.add(node, f"module attributes cannot be assigned or deleted ({dotted}.{attr})")
            denied = sorted(m for m in base if attr not in MODULE_ATTRS[m])
            if denied and not self._bad_ident(attr):
                self.add(node, f"{dotted}.{attr} is not available in scene code (allowed {dotted} attributes "
                         f"include: {_examples(denied[0])}, ...)")
            elif not denied and self._module_of(node) and id(node) not in self._module_ok:
                self.add(node, f"module {dotted}.{attr} can only be used as {dotted}.{attr}.<function>(...)")
        if isinstance(node.value, ast.Name) and node.value.id == "config":
            if isinstance(node.ctx, ast.Load) and attr in CONFIG_READ_ATTRS:
                self._config_ok.add(id(node.value))
            else:
                self.add(node, f"config.{attr} cannot be used (config is read-only geometry)")
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if isinstance(node.value, ast.Name) and node.value.id == "config":
            self.add(node, "config[...] is not allowed")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "config":
            self.add(node, "calling config methods is not allowed")
        if isinstance(func, ast.Name) and func.id == "super" and (node.args or node.keywords):
            # super(Text, self).__init__(path) would skip Text and reach the SVG file loader
            self.add(node, "call super() without arguments")
        for kw in node.keywords:
            if kw.arg and kw.arg in FORBIDDEN_KWARGS:
                self.add(node, f"keyword argument {kw.arg!r} is not allowed (no external files)")
        self.generic_visit(node)

    def visit_Dict(self, node: ast.Dict) -> None:
        for key in node.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str) and key.value in FORBIDDEN_KWARGS:
                self.add(node, f"dictionary key {key.value!r} is not allowed (no external files)")
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        value = node.value
        if isinstance(value, bytes):
            self.add(node, "bytes literals are not allowed")
        elif isinstance(value, str):
            if _DUNDER_STRING.search(value) or _INTROSPECTION_ATTRS.search(value):
                self.add(node, "strings containing dunder names or introspection fields are not allowed")
            if _PATH_LIKE.search(value):
                self.add(node, "file paths and URLs are not allowed in scene code (no external files)")
            match = _TEX_DENY.search(value)
            if match:
                self.add(node, f"LaTeX construct {match.group(0)!r} is not allowed")

    # --- definitions / statements -------------------------------------------------------------
    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if self._bad_ident(node.name):
            self.add(node, f"class names starting with '_' are not allowed ({node.name!r})")
        if node.keywords:
            self.add(node, "class keywords (metaclass=...) are not allowed")
        if any(isinstance(b, ast.Name) and b.id == "AadhiScene" for b in node.bases):
            self.scene_classes.append(node.name)
            if not self._bad_ident(node.name) and not SCENE_NAME.fullmatch(node.name):
                self.add(node, f"scene class name {node.name!r} must use ASCII letters, digits and underscores "
                         "only, start with a letter and have at most 64 characters (e.g. OhmsLaw)")
        if node.name in PROTECTED_NAMES:
            self.add(node, f"{node.name!r} must not be redefined")
        self.generic_visit(node)

    def _check_function(self, node: ast.FunctionDef | ast.Lambda) -> None:
        if isinstance(node, ast.FunctionDef):
            if node.name.startswith("_") and node.name != "__init__":
                self.add(node, f"function names starting with '_' are not allowed ({node.name!r})")
            if node.name in PROTECTED_NAMES:
                self.add(node, f"{node.name!r} must not be redefined")
        args = node.args
        for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]:
            if arg is None:
                continue
            if self._bad_ident(arg.arg):
                self.add(node, f"argument names starting with '_' are not allowed ({arg.arg!r})")
            elif arg.arg in self.modules:
                self.add(node, f"argument name {arg.arg!r} shadows a module; use another name")

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._check_function(node)
        self.generic_visit(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._check_function(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.add(node, "async code is not allowed")

    def visit_Await(self, node: ast.Await) -> None:
        self.add(node, "async code is not allowed")

    def visit_Global(self, node: ast.Global) -> None:
        self.add(node, "`global` statements are not allowed")


def _check_tree(tree: ast.Module) -> list[str]:
    checker = _Checker(module_bindings(tree))
    try:
        checker.visit(tree)
    except RecursionError:  # pragma: no cover - pathological nesting
        return ["code is nested too deeply"]
    problems = checker.problems
    if not checker.scene_classes:
        problems.append("define exactly one scene class: `class MyScene(AadhiScene):` with a construct(self) method")
    elif len(checker.scene_classes) > 1:
        problems.append(f"define exactly one AadhiScene subclass (found {', '.join(checker.scene_classes)})")
    return problems[:MAX_PROBLEMS]


def _parse(code: str) -> ast.Module | str:
    try:
        return ast.parse(code)
    except SyntaxError as exc:
        return f"line {exc.lineno}: syntax error: {exc.msg}"
    except (ValueError, RecursionError, MemoryError) as exc:  # pragma: no cover - pathological input
        return f"code cannot be parsed: {type(exc).__name__}"


def check_code(code: str) -> list[str]:
    """Return human-readable problems with free-form scene ``code`` ([] = allowed)."""
    if not isinstance(code, str) or not code.strip():
        return ["code is empty"]
    if len(code) > MAX_CODE_CHARS:
        return [f"code is too long ({len(code)} > {MAX_CODE_CHARS} characters)"]
    if "\x00" in code:
        return ["code contains NUL bytes"]
    tree = _parse(code)
    if isinstance(tree, str):
        return [tree]
    return _check_tree(tree)


def check_template_source(source: str) -> list[str]:
    """Problems with a template's generated source ([] = allowed).

    The source must be ``PARAMS = <literal>`` followed by the template's static scene file.
    ``PARAMS`` is validated data emitted with ``repr`` (so labels such as ``/home`` or ``__init__()``
    are fine) and must be a pure literal; everything after it gets the free-form rules.
    """
    if not isinstance(source, str) or not source.strip():
        return ["template source is empty"]
    if len(source) > MAX_TEMPLATE_SOURCE_CHARS or "\x00" in source:
        return ["template source is too large or contains NUL bytes"]
    tree = _parse(source)
    if isinstance(tree, str):
        return [tree]
    first = tree.body[0] if tree.body else None
    if not (
        isinstance(first, ast.Assign)
        and len(first.targets) == 1
        and isinstance(first.targets[0], ast.Name)
        and first.targets[0].id == "PARAMS"
    ):
        return ["template source must start with `PARAMS = <literal>`"]
    try:
        ast.literal_eval(first.value)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return ["PARAMS must be a plain literal (dicts, lists, strings, numbers, booleans, None)"]
    return _check_tree(ast.Module(body=tree.body[1:], type_ignores=[]))


def check_timing(code: str, n_beats: int | None) -> list[str]:
    """Structural timing check for free-form code: steps must be synchronised with the beats."""
    if not n_beats or n_beats < 2:
        return []
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    if not calls & {"wait_until_beat", "play_step"}:
        return [
            f"the scene has {n_beats} narration beats: call self.wait_until_beat(i) (or self.play_step(i, ...)) "
            "before the animation step of beat i"
        ]
    return []
