"""AST guard for free-form Manim code (deny-by-default module access, reserved names, template sources)."""

from __future__ import annotations

import builtins
import functools
import itertools
import math
import random
import textwrap
import types

import numpy
import pytest

from aadhi.manim.guard import (
    FORBIDDEN_ATTRS,
    FORBIDDEN_BUILTINS,
    FORBIDDEN_MANIM_NAMES,
    MODULE_ATTRS,
    STAR_MODULES,
    check_code,
    check_template_source,
    check_timing,
    module_bindings,
)

GOOD = textwrap.dedent(
    '''
    from manim import *
    import numpy as np
    import math
    from functools import reduce
    import random as rnd


    def helper(x):
        return x * 2


    class OhmsLaw(AadhiScene):
        def construct(self):
            self.show_title("Ohm's law")
            eq = self.math(r"V = I R")
            box = self.highlight_box(eq)
            dots = VGroup(*[Dot(np.array([math.cos(k), math.sin(k), 0])) for k in range(6)])
            width = config.frame_width / 2
            label = self.label(f"width {width:.1f}", role="small")
            for _ in range(2):
                pass
            self.wait_until_beat(0)
            self.play(Write(eq), run_time=self.step_run_time(0))
            self.play_step(1, Create(box), FadeIn(dots))
            self.wait_until_beat(2)
            self.play(eq.animate.shift(UP), FadeIn(label), rate_func=rate_functions.smooth)
            text = "{} volts".format(12)
            self.add(Text(text))
            reduce(lambda a, b: a + b, [1, 2, 3])
            rnd.seed(1)
            v = np.linalg.norm(np.array([3.0, 4.0, 0.0])) + np.random.default_rng(1).uniform()
    '''
)


def scene(snippet: str, prelude: str = "from manim import *\nimport numpy as np\n") -> str:
    return prelude + snippet + "\n\nclass S(AadhiScene):\n    def construct(self):\n        pass\n"


def test_good_code_passes() -> None:
    assert check_code(GOOD) == []


@pytest.mark.parametrize(
    ("snippet", "needle"),
    [
        ("import os", "import of 'os'"),
        ("import subprocess", "import of 'subprocess'"),
        ("import numpy.linalg", "import of 'numpy.linalg'"),
        ("from os import system", "import from 'os'"),
        ("from math import *", "from math import *"),
        ("from manim import Circle", "from manim import *"),
        ("from . import x", "relative imports"),
        ("from numpy import load", "importing 'load'"),
        ("from numpy import lib", "importing 'lib'"),
        ("import numpy as __np", "import alias"),
        ("x = open('a.txt')", "'open' is not allowed"),
        ("exec('print(1)')", "'exec' is not allowed"),
        ("eval('1+1')", "'eval' is not allowed"),
        ("compile('1', 'f', 'eval')", "'compile' is not allowed"),
        ("__import__('os')", "names starting with '_'"),
        ("globals()", "'globals' is not allowed"),
        ("locals()", "'locals' is not allowed"),
        ("vars(self)", "'vars' is not allowed"),
        ("input()", "'input' is not allowed"),
        ("breakpoint()", "'breakpoint' is not allowed"),
        ("getattr(self, 'x')", "'getattr' is not allowed"),
        ("setattr(self, 'x', 1)", "'setattr' is not allowed"),
        ("delattr(self, 'x')", "'delattr' is not allowed"),
        ("dir(self)", "'dir' is not allowed"),
        ("type(self)", "'type' is not allowed"),
        ("x = self.__class__", "attribute names starting with '_'"),
        ("x = self._private", "attribute names starting with '_'"),
        ("x = ().__class__.__bases__", "attribute names starting with '_'"),
        ("x = super().__getattribute__", "attribute names starting with '_'"),
        ("x = self.__init__", "attribute names starting with '_'"),
        ("x = {}['__builtins__']", "dunder"),
        ("x = '{0.gi_frame.f_globals}'.format(g)", "introspection"),
        ("g = (i for i in []); f = g.gi_frame", "gi_frame"),
        ("f = g.f_globals", "f_globals"),
        ("m = SVGMobject('logo.svg')", "'SVGMobject' is not allowed"),
        ("m = ImageMobject('x.png')", "'ImageMobject' is not allowed"),
        ("m = Typst('#read(\"x\")')", "'Typst' is not allowed"),
        ("self.add_sound('x.wav')", ".add_sound"),
        ("self.renderer.file_writer", ".renderer"),
        ("self.camera.get_image()", ".get_image"),
        ("self.camera.background_image = 'x.png'", ".background_image"),
        ("t = Text('x'); t.file_name = 'secret.svg'", ".file_name"),
        ("Square().color_using_background_image(p)", ".color_using_background_image"),
        ("class T(Text):\n    def __init__(self, p):\n        super(Text, self).__init__(p)",
         "call super() without arguments"),
        ("x = super(Circle)", "call super() without arguments"),
        ("np.save('x.npy', a)", "np.save is not available"),
        ("np.load('x.npy')", "np.load is not available"),
        ("a.tofile('x')", ".tofile"),
        ("a.ctypes.data", ".ctypes"),
        ("np.lib.npyio", "np.lib is not available"),
        ("np.ctypeslib", "np.ctypeslib is not available"),
        ("np.testing.assert_equal", "np.testing is not available"),
        ("open_file('x')", "'open_file' is not allowed"),
        ("capture(['cmd', '/c', 'dir'])", "'capture' is not allowed"),
        ("guarantee_empty_existence(x)", "'guarantee_empty_existence' is not allowed"),
        ("get_video_metadata(x)", "'get_video_metadata' is not allowed"),
        ("logger.info('x')", "'logger' is not allowed"),
        ("console.print('x')", "'console' is not allowed"),
        ("with tempconfig({}): pass", "'tempconfig' is not allowed"),
        ("config.media_dir = '/tmp'", "config.media_dir"),
        ("config['media_dir'] = 'x'", "config[...]"),
        ("config.frame_width = 3", "config.frame_width cannot be used"),
        ("config.update({})", "config"),
        ("c = config", "`config` may only be read"),
        ("t = TexTemplate()", "'TexTemplate' is not allowed"),
        ("m = MathTex('x', tex_template=t)", "keyword argument 'tex_template'"),
        ("c = Code(code_file='server.py')", "keyword argument 'code_file'"),
        ("c = Code(**{'code_file': 'x'})", "dictionary key 'code_file'"),
        ("v = VMobject(background_image='x.png')", "keyword argument 'background_image'"),
        ("t = Text('C:/Users/secret.txt')", "file paths and URLs"),
        ("t = Text('/etc/passwd')", "file paths and URLs"),
        ("t = Text('https://evil.example/x')", "file paths and URLs"),
        ("t = Text('../../.env')", "file paths and URLs"),
        ("m = MathTex(r'\\input{/etc/passwd}')", "LaTeX construct"),
        ("m = MathTex(r'^^5cinput{x}')", "LaTeX construct"),
        ("m = Tex(r'\\begin{filecontents}{x}')", "LaTeX construct"),
        ("b = b'raw'", "bytes literals"),
        ("global BEAT_TIMES", "`global` statements"),
        ("BEAT_TIMES = [0]", "must not be reassigned"),
        ("del AadhiScene", "must not be reassigned"),
        ("def _helper(): pass", "function names starting with '_'"),
        ("f = lambda _x: _x", "argument names starting with '_'"),
        ("async def go(): pass", "async code"),
        ("np.array([1]).dump('x.pkl')", ".dump"),
        ("with register_font('C:/f.ttf'): pass", "'register_font' is not allowed"),
        ("get_plugins()", "'get_plugins' is not allowed"),
        ("list_plugins()", "'list_plugins' is not allowed"),
        ("r = CairoRenderer()", "'CairoRenderer' is not allowed"),
        ("self.interactive_embed()", ".interactive_embed"),
        ("self.embed()", ".embed"),
        ("x = f'{self.__dict__}'", "attribute names starting with '_'"),
        ("x = [c for c in ().__class__.__base__.__subclasses__()]", "attribute names starting with '_'"),
    ],
)
def test_forbidden_constructs(snippet: str, needle: str) -> None:
    problems = check_code(scene(snippet))
    assert problems, f"expected a problem for {snippet!r}"
    assert any(needle in p for p in problems), problems


def test_problems_carry_line_numbers() -> None:
    code = "from manim import *\n\nclass S(AadhiScene):\n    def construct(self):\n        eval('1')\n"
    assert check_code(code) == ["line 5: 'eval' is not allowed in scene code"]


# --- module objects: deny by default ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("snippet", "needle"),
    [
        # attributes that are not on the module's allow-list (the reviewer's probe first)
        ("x = np.some_unlisted_function", "np.some_unlisted_function is not available"),
        ("x = np.ma.dumps(a)", "np.ma is not available"),
        ("x = np.rec.fromfile('x')", "np.rec is not available"),
        ("x = np.char.add('a', 'b')", "np.char is not available"),
        ("x = np.f2py", "np.f2py is not available"),
        ("x = np.linalg.lapack_lite", "np.linalg.lapack_lite is not available"),
        ("x = np.random.bit_generator", "np.random.bit_generator is not available"),
        ("x = rate_functions.np", "rate_functions.np is not available"),
        ("x = rate_functions.wraps", "rate_functions.wraps is not available"),
        ("x = rate_functions.typing", "rate_functions.typing is not available"),
        ("import math\nx = math.sys", "math.sys is not available"),
        ("import random\nx = random.SystemRandom()", "random.SystemRandom is not available"),
        ("import functools\nx = functools.update_wrapper", "functools.update_wrapper is not available"),
        ("import itertools\nx = itertools.nothing", "itertools.nothing is not available"),
        ("import numpy\nx = numpy.lib", "numpy.lib is not available"),
        ("import numpy as n2\nx = n2.save", "n2.save is not available"),
        ("from numpy import linalg\nx = linalg.lapack_lite", "linalg.lapack_lite is not available"),
        ("from numpy import random as npr\nx = npr.mtrand", "npr.mtrand is not available"),
        ("x = np.__dict__", "attribute names starting with '_'"),
        # modules cannot escape (stored, passed, returned, iterated)
        ("x = np", "module 'np' can only be used"),
        ("f(np)", "module 'np' can only be used"),
        ("xs = [np, math]", "module 'np' can only be used"),
        ("g = lambda: rate_functions", "module 'rate_functions' can only be used"),
        ("import math\ndef f():\n    return math", "module 'math' can only be used"),
        ("x = np.linalg", "module np.linalg can only be used"),
        ("f(np.random)", "module np.random can only be used"),
        ("from numpy import linalg\nx = linalg", "module 'linalg' can only be used"),
        # modules cannot be re-bound, shadowed or patched
        ("np = 3", "'np' is a module and must not be reassigned"),
        ("import math\nfor math in range(3): pass", "'math' is a module and must not be reassigned"),
        ("del np", "'np' is a module and must not be reassigned"),
        ("rate_functions = None", "'rate_functions' is a module"),
        ("def f(np): pass", "argument name 'np' shadows a module"),
        ("np.pi = 3", "module attributes cannot be assigned"),
        ("rate_functions.smooth = linear", "module attributes cannot be assigned"),
        ("import math\ndel math.pi", "module attributes cannot be assigned"),
    ],
)
def test_module_access_is_deny_by_default(snippet: str, needle: str) -> None:
    problems = check_code(scene(snippet))
    assert any(needle in p for p in problems), problems


def test_module_problems_suggest_allowed_attributes() -> None:
    (problem,) = [p for p in check_code(scene("x = np.lib")) if "np.lib" in p]
    assert "array" in problem and "linspace" in problem


def test_allowed_module_usage_passes() -> None:
    code = textwrap.dedent(
        """
        from manim import *
        import numpy
        import math
        import random
        import itertools
        import functools
        from numpy import linalg, array, pi as PI_NP
        from numpy import random as npr
        from math import sqrt, tau
        from itertools import combinations


        class S(AadhiScene):
            def construct(self):
                a = np.array([1.0, 2.0, 0.0]) + numpy.zeros(3) + array([0, 0, 0])
                n = np.linalg.norm(a) + linalg.norm(a) + linalg.det(np.eye(3))
                r = np.random.uniform(0, 1) + npr.uniform() + random.uniform(0, 1)
                g = np.random.default_rng(3)
                k = g.integers(0, 5)
                xs = np.linspace(0, math.tau, 20)
                ys = np.sin(xs) * math.sqrt(2) + sqrt(3) + tau + PI_NP
                pairs = list(itertools.combinations(range(4), 2)) + list(combinations(range(3), 2))
                total = functools.reduce(lambda p, q: p + q, range(5))
                flat = list(itertools.chain.from_iterable([[1], [2]]))
                f = rate_functions.ease_in_out_sine
                ok = isinstance(a, np.ndarray) and np.binary_repr(5, width=4) == "0101"
                spectrum = np.abs(np.fft.rfft(np.sin(xs))) + np.fft.rfftfreq(20)
                bits = np.bitwise_xor(np.array([1, 0]), np.array([1, 1]))
                self.play_step(0, FadeIn(Dot()), rate_func=rate_functions.there_and_back)
        """
    )
    assert check_code(code) == []


def test_module_bindings() -> None:
    import ast

    tree = ast.parse("import numpy as n2\nimport math\nfrom numpy import linalg as la, array\nimport math as np\n")
    bindings = module_bindings(tree)
    assert bindings["n2"] == {"numpy"} and bindings["math"] == {"math"} and bindings["la"] == {"numpy.linalg"}
    assert bindings["np"] == {"numpy", "math"} and bindings["rate_functions"] == {"rate_functions"}
    assert "array" not in bindings
    # a name bound to two modules may only use attributes both allow
    assert any("np.linspace" in p for p in check_code(scene("x = np.linspace(0, 1)", "from manim import *\nimport math as np\n")))
    assert check_code(scene("x = np.sin(1)", "from manim import *\nimport math as np\n")) == []


# --- names that are fine as identifiers (reviewer finding: false positives) -------------------------


def test_ordinary_identifiers_are_not_reserved() -> None:
    code = textwrap.dedent(
        """
        from manim import *


        class SignalsAndCode(AadhiScene):
            def construct(self):
                camera = self.camera.frame
                frame = self.camera.frame
                signal = FunctionGraph(lambda t: np.sin(t), x_range=[-3, 3])
                code = Code(code_string="print('hi')", language="python")
                core = Circle()
                scene = VGroup(signal, core)
                color = BLUE
                types = ["int", "float"]
                io = self.label("I/O")
                http = self.label("HTTP")
                platform = Rectangle()
                inspect = Dot()
                gc = 3
                glob = "*.txt"
                unit = 2
                constants = [1, 2]
                utils = []
                renderer = "cairo"
                os = "Linux"
                sys = "system"
                socket = Square()
                Path = Line(LEFT, RIGHT)
                self.core = core
                self.signal = signal
                path = Arc(radius=2)
                dot = Dot(path.get_start())
                self.play_step(0, camera.animate.scale(1.2))
                self.play_step(1, MoveAlongPath(dot, path=path), FadeIn(code))
                self.play_step(2, Create(scene, run_time=1), FadeToColor(core, color))
        """
    )
    assert check_code(code) == []


def test_reserved_names_are_documented_in_the_rules() -> None:
    from aadhi.manim.prompts import freeform_rules

    rules = freeform_rules()
    reserved = {"open", "exec", "eval", "compile", "getattr", "setattr", "delattr", "globals", "locals", "vars", "dir",
                "type", "input", "help", "exit", "quit", "breakpoint", "memoryview", "manim", "logger", "console",
                "capture", "tempconfig", "TexTemplate", "register_font"}
    assert reserved <= FORBIDDEN_BUILTINS | FORBIDDEN_MANIM_NAMES
    for name in reserved:
        assert f"`{name}`" in rules, name
    assert "camera" in rules and "signal" in rules and "np.linalg.norm" in rules


def test_forbidden_builtins_are_real_builtins() -> None:
    assert all(hasattr(builtins, name) for name in FORBIDDEN_BUILTINS)


# --- scene classes ---------------------------------------------------------------------------------


def test_scene_class_rules() -> None:
    assert any("exactly one scene class" in p for p in check_code("from manim import *\nx = 1\n"))
    two = "from manim import *\nclass A(AadhiScene):\n    pass\nclass B(AadhiScene):\n    pass\n"
    assert any("exactly one AadhiScene subclass" in p for p in check_code(two))
    meta = "from manim import *\nclass A(AadhiScene, metaclass=X):\n    pass\n"
    assert any("metaclass" in p for p in check_code(meta))
    redefine = "from manim import *\nclass AadhiScene(Scene):\n    pass\nclass A(AadhiScene):\n    pass\n"
    assert any("must not be redefined" in p for p in check_code(redefine))


@pytest.mark.parametrize("name", ["Ohms_Law", "Scene2", "S", "A" * 64, "ohm_law_v2"])
def test_scene_names_accepted(name: str) -> None:
    assert check_code(f"from manim import *\nclass {name}(AadhiScene):\n    pass\n") == []


@pytest.mark.parametrize("name", ["ஓமின்விதி", "Ohmé", "A" * 65])
def test_scene_names_rejected(name: str) -> None:
    problems = check_code(f"from manim import *\nclass {name}(AadhiScene):\n    pass\n")
    assert any("ASCII letters" in p for p in problems), problems


def test_syntax_errors_and_limits() -> None:
    assert check_code("class S(AadhiScene)\n    pass")[0].startswith("line 1: syntax error")
    assert check_code("") == ["code is empty"]
    assert "too long" in check_code("x = 1\n" * 5000)[0]
    assert check_code("x = '\x00'") == ["code contains NUL bytes"]


def test_helper_init_and_plain_underscore_allowed() -> None:
    code = textwrap.dedent(
        """
        from manim import *
        class Box(VGroup):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
        class S(AadhiScene):
            def construct(self):
                for _ in range(3):
                    self.add(Box())
        """
    )
    assert check_code(code) == []


def test_check_timing() -> None:
    no_sync = "from manim import *\nclass S(AadhiScene):\n    def construct(self):\n        self.play(Create(Circle()))\n"
    assert check_timing(no_sync, 3) and "wait_until_beat" in check_timing(no_sync, 3)[0]
    assert check_timing(no_sync, 1) == []
    assert check_timing(no_sync, None) == []
    assert check_timing(GOOD, 3) == []


# --- template sources: PARAMS is data ---------------------------------------------------------------

TEMPLATE_SCENE = "from manim import *\n\nclass T(AadhiScene):\n    def construct(self):\n        self.label(PARAMS['title'])\n"


def test_template_params_may_contain_paths_urls_and_dunders() -> None:
    params = {"title": "/home/__init__()", "nodes": ["/etc", "https://api.example.com", "C:\\Users\\x", "../.env",
                                                     "Python __main__ module", r"\input{x}", "{0.gi_frame}"]}
    source = f"PARAMS = {params!r}\n\n" + TEMPLATE_SCENE
    assert check_template_source(source) == []
    assert check_code(source) != [], "the same strings are rejected in free-form code"


@pytest.mark.parametrize(
    ("source", "needle"),
    [
        ("", "empty"),
        (TEMPLATE_SCENE, "must start with `PARAMS = <literal>`"),
        ("X = {}\n" + TEMPLATE_SCENE, "must start with `PARAMS = <literal>`"),
        ("PARAMS = A = {}\n" + TEMPLATE_SCENE, "must start with `PARAMS = <literal>`"),
        ("PARAMS = open('x')\n" + TEMPLATE_SCENE, "plain literal"),
        ("PARAMS = {'a': [1, 2] + [3]}\n" + TEMPLATE_SCENE, "plain literal"),
        ("PARAMS = {}\nimport os\n" + TEMPLATE_SCENE, "import of 'os'"),
        ("PARAMS = {}\nx = '/etc/passwd'\n" + TEMPLATE_SCENE, "file paths and URLs"),
        ("PARAMS = {}\nfrom manim import *\n", "exactly one scene class"),
        ("PARAMS = {\n", "syntax error"),
        ("PARAMS = 1\x00", "NUL"),
    ],
)
def test_template_source_rules(source: str, needle: str) -> None:
    problems = check_template_source(source)
    assert any(needle in p for p in problems), problems


# --- allow-lists stay in sync with the real modules --------------------------------------------------

REAL_MODULES = {
    "numpy": numpy,
    "numpy.linalg": numpy.linalg,
    "numpy.fft": numpy.fft,
    "numpy.random": numpy.random,
    "math": math,
    "random": random,
    "itertools": itertools,
    "functools": functools,
}
VERSION_DEPENDENT = {("math", "sumprod"), ("math", "fma"), ("itertools", "batched")}
DANGER = {"os", "sys", "subprocess", "shutil", "pathlib", "io", "builtins", "importlib", "ctypes", "socket", "pickle",
          "tempfile", "glob", "inspect", "types", "threading", "multiprocessing", "platform", "urllib", "http", "runpy",
          "code", "gc", "marshal", "signal", "logging", "zipfile", "tarfile", "webbrowser"}


def _real_module(path: str):
    if path == "rate_functions":
        from manim.utils import rate_functions

        return rate_functions
    return REAL_MODULES[path]


def test_module_allow_lists_resolve_to_safe_objects() -> None:
    assert set(MODULE_ATTRS) == set(REAL_MODULES) | {"rate_functions"}
    assert set(STAR_MODULES.values()) <= set(MODULE_ATTRS)
    for path, attrs in MODULE_ATTRS.items():
        module = _real_module(path)
        for attr in attrs:
            if (path, attr) in VERSION_DEPENDENT and not hasattr(module, attr):
                continue
            assert hasattr(module, attr), f"{path}.{attr} does not exist"
            value = getattr(module, attr)
            if isinstance(value, types.ModuleType):
                assert f"{path}.{attr}" in MODULE_ATTRS, f"{path}.{attr} is a module without an allow-list"
                assert not set(value.__name__.split(".")) & DANGER
            else:
                owner = getattr(value, "__module__", "") or ""
                assert not set(owner.split(".")) & DANGER, f"{path}.{attr} comes from {owner}"
            assert attr not in FORBIDDEN_ATTRS


def test_rate_function_allow_list_matches_manim() -> None:
    from manim.utils import rate_functions

    curves = {name for name, value in vars(rate_functions).items()
              if not name.startswith("_") and callable(value) and not isinstance(value, type)
              and getattr(value, "__module__", "") == rate_functions.__name__}
    assert MODULE_ATTRS["rate_functions"] == curves
