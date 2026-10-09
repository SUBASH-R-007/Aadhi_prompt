<!-- PROMPT_VERSION: manim-freeform-rules-2 -->
Rules for free-form Manim scene code (Manim Community v0.21, Cairo renderer):

Structure
- Start with `from manim import *`. You may also import `math`, `numpy as np`, `random`, `itertools`, `functools`. Nothing else.
- Define exactly one scene class that subclasses `AadhiScene` (it is provided for you; do not define or import it) with a `construct(self)` method. Name it with ASCII letters and digits, e.g. `class OhmsLaw(AadhiScene):`.
- The scene is synchronised with narration beats. Before the animation that belongs to beat `i`, call `self.wait_until_beat(i)` (beats are numbered from 0). Use exactly one animation step per beat, in order. `self.play_step(i, *animations)` does both: it waits for beat `i` and plays the animations so they finish before beat `i + 1`.
- Keep each `self.play(...)` short: `run_time=self.step_run_time(i)` fits the beat window. Never call `self.wait()` with long or computed durations; the scene is padded to its total length automatically (`self.finish()` runs at the end).

Layout and readability
- The frame is 16:9 (side panels are portrait 4:5; `self.IS_PANEL` tells you which). Keep everything inside the safe area: build the content, then call `self.fit_to_safe_area(group)` to scale and centre it.
- `self.content_box()` returns `(cx, cy, width, height)` of the safe area; `self.show_title(text)` adds a title at the top; `self.note(text)` returns animations that show a short caption at the bottom (call `self.reserve_notes()` first if you use notes).
- Use `self.label(text, role="label")` for text (it picks fonts for Tamil, Hindi and other scripts) and `self.math(r"...")` for formulas (falls back to plain text if LaTeX is unavailable). Roles: title, heading, label, body, note, small.
- Write LaTeX in raw strings: `self.math(r"\frac{V}{R}")`. Never use `\input`, `\def`, `\newcommand`, `\usepackage`, `\write` or other file/macro commands.
- To show source code, use `Code(code_string="...", language="python")`.
- Use at most about 12 visible objects at once, font sizes from the roles, and leave space between elements so nothing overlaps.
- Theme colours: `AADHI_GOLD`, `AADHI_PURPLE`, `AADHI_CYAN`, `AADHI_WHITE`, `AADHI_MUTED`, `AADHI_GREEN`, `AADHI_RED`, `AADHI_ORANGE`, `AADHI_PINK`, `AADHI_BLUE`. The background is already set.

Modules
- `np`, `math`, `random`, `itertools`, `functools` and `rate_functions` are modules: use them only as `np.array(...)`, `np.linalg.norm(v)`, `math.sin(x)`, `rate_functions.smooth`. Never assign to these names, store a module in a variable or pass it to a function.
- Only common maths functions and constants of these modules are available (array creation, elementwise maths, `np.linalg`, `np.random`, rate-function curves); file and system functions are not.

Forbidden (the code is rejected otherwise)
- Files, images, sounds and the network: no `open`, `SVGMobject`, `ImageMobject`, `Typst`, `add_sound`, `Code(code_file=...)`, file paths or URLs.
- Reserved names that cannot be used at all, not even as variable names: `open`, `exec`, `eval`, `compile`, `getattr`, `setattr`, `delattr`, `globals`, `locals`, `vars`, `dir`, `type`, `input`, `help`, `exit`, `quit`, `breakpoint`, `memoryview`, `manim`, `logger`, `console`, `capture`, `tempconfig`, `TexTemplate`, `register_font`. Any other name (for example `camera`, `scene`, `signal`, `code`, `color`, `path`) is fine for your own variables.
- Any name or attribute starting with an underscore (write `helper`, not `_helper`), dunder strings, `global` statements, `async` code.
- Changing `config` (you may read `config.frame_width` / `config.frame_height`), custom TeX templates, `self.renderer`, file-related attributes such as `.save`, `.load`, `.tofile`, `.file_name`.
- Infinite loops or very long loops; the render has a strict time limit.
