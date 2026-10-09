<!-- PROMPT_VERSION: 2 -->
# Your task: write one animated simulation scene

A full-screen animation shows a process unfolding while Aadhi narrates. The animation advances
**one step per beat**: beat 1 plays step 1, beat 2 plays step 2, and so on. Narration and animation
must tell the same story at the same moment (temporal contiguity).

# With a template (when "Animation template" is present)

- Fill `params` following the template's parameter schema; the example params show the expected
  shape. Keep labels short; use the board language for labels.
- The template's "steps" note tells you how params map to steps. Write exactly that many beats.
- For each beat write `visual_cue`: what the learner sees in that step ("The second term moves to
  the right-hand side and changes sign").

# Without a template (free-form code)

Write Python for Manim Community edition in `code`:

- Exactly one class `class <Name>(AadhiScene):` with a `construct(self)` method. `AadhiScene` is
  provided; do not import it.
- Import only from `manim`, `math`, `numpy`, `random`, `itertools`, `functools`. No file, network or
  system access, no `open`, `exec`, `eval`, `getattr`, dunder attributes, `SVGMobject` or
  `ImageMobject`.
- Call `self.wait_until_beat(i)` (0-based) before the animations of step `i`, and `self.finish()`
  at the end. Keep everything inside the frame; use `Text` for words and `MathTex` for formulas;
  keep at most about eight objects on screen.
- Write exactly one beat per `wait_until_beat` step, each with a `visual_cue`.

# Ideas from the source's author

When "Author's visual suggestions" are given, use their teaching idea (what is compared, what moves,
what changes state) to choose the steps, and drop decorative effects (glows, camera moves, fades).
They shape the animation; they are never read aloud.

# Narration

- Beat 1 bridges from the previous scene ("This scene" → `bridge_in`) and sets up what the learner
  is about to watch and what to look for.
- Each following beat explains what is happening in its step and *why*, pointing at the visual
  ("See how the current arrow shrinks as the resistance grows").
- The last beat states the insight the animation demonstrates.
- Spoken text only; leave `board`, `fill_previous_blank` and `highlight_steps` empty.
- `title`: short, in the board language, naming the idea shown. `mascot_position`: leave null.
