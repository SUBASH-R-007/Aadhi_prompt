<!-- PROMPT_VERSION: 2 -->
# Your task: write one interactive exploration scene

The learner explores an idea with a small p5.js sketch (web player only; the exported video shows a
still frame). Exploration teaches when changing one input and seeing the effect reveals the rule.

# The sketch (`p5_code`)

- Global-mode p5.js: define `setup()` (call `createCanvas(windowWidth, windowHeight)`) and `draw()`.
- One clear control — a slider drawn on the canvas, mouse position or arrow keys — and a live
  visual response with labelled values and units. "Author's visual suggestions", when given, may
  suggest what to vary and what to show.
- Self-contained: no network access (`fetch`, `loadJSON`, images, fonts), no DOM elements outside
  the canvas, no external libraries. Under about 120 lines, readable variable names.
- Dark background (`#1A0B2E`), purple and gold accents, text size at least 18.

# Narration

- 2–4 beats: bridge in from the previous scene ("This scene" → `bridge_in`), say what to try ("Drag
  the slider to the right and watch the current"), what to notice, and the rule they just
  discovered. Mention that the exported version shows a snapshot.
- Spoken text only; leave `board`, `fill_previous_blank` and `highlight_steps` empty.
- `title`: short, in the board language.
