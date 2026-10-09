<!-- PROMPT_VERSION: 2 -->
# Your task: write one board scene

You write a single scene of the lecture as a sequence of beats. Each beat is what Aadhi says, plus
(optionally) the one board item that appears as the beat starts. Read "This scene" for the goal,
key points, narrative role, bridge, concept, objectives and the planned visual; read "Neighbouring
scenes" so you continue from the previous scene and set up the next one without repeating either;
use "Relevant source" for the facts.

# Shape of a good scene by type

- **title (cold open)**: 2–4 beats. Open with the hook — a question, a puzzle or a real situation the
  learner recognises — then name today's topic. Board: a `heading` with the session title and at
  most one or two hook items (a question as a `callout_info`, for example). End by telling the
  learner what they will be able to do by the end of the session. This is the lecture's only
  opening: no other scene introduces the session again.
- **recap**: 2–4 beats. Retrieve, don't repeat: ask a quick question about the previous session,
  answer it, then connect it to today. Board: two or three `bullet` or `takeaway` items.
- **content**: 4–8 beats. Bridge in → concrete situation → idea → precise statement → example (and a
  non-example after a definition) → why it matters. Board: a `heading` (optional), then the key
  items — `definition`, `formula` with `variables`, `bullet`, `table` or `figure`. Add a
  `misconception` item when "Misconceptions to target" lists one for this scene: state the wrong
  belief, then correct it in the next beat with the reason.
- **example** (worked example): 5–9 beats. State the problem (a `paragraph` or `callout_info`), list
  the givens, then one `example_step` per beat with its `justification`. Fade the last one or two
  steps: reveal them with `blank: true`, invite the learner to try, `pause_after` 2–4 seconds, then
  fill with `fill_previous_blank: true` in the next beat and explain. Finish by checking the answer
  makes sense (units, size).
- **summary / key_takeaway**: 3–5 beats. Each beat reveals one `takeaway` that recalls an idea in a
  fresh, connected way (not a copy of the objectives); the last beat looks ahead or gives a
  memorable closing line.

# Board rules (recap)

- At most five board items; short phrases that complement, not duplicate, the narration.
- A beat reveals at most one item; `highlight_steps` points back to at most two earlier items
  (1-based numbers in reveal order) when you refer to them again.
- Formulas go in `formula` items (`latex` without dollar signs). Inline maths in other items uses
  `$…$`.
- Use a `figure` item only with an id from "Figures", and refer to it in the narration
  ("In this circuit diagram…").
- Board text names ideas, never the source's packaging (no clip or segment labels, timings or
  "coming up next").

# Side panel

Fill `side_panel` only when "This scene" names a `side_panel_kind`; use exactly that kind, put
the planned reason (refined if you like) in `rationale`, and fill only the fields of that kind:
`figure_id` (from "Figures"); `image_prompt` (a concrete description of a real object or setting,
no text in the image); `graph_functions` (math.js expressions in `x`, e.g. `x^2 - 4`, `sin(x)`,
with `graph_x_min`/`graph_x_max`); `chart_*` (labels and one value per label in each dataset);
`model_primitives`; `terminal_command`/`terminal_output`; `quiz_*`; `manim_params` (follow the
"Animation template" schema; the animation advances one step per beat from `show_from_beat`).
When "Author's visual suggestions" describe a fitting picture or animation, base the panel on it.
Mention the panel in the narration when it appears. Otherwise set `side_panel` to null.

# Output fields

- `title`: short scene title in the board language (2–7 words) that names this scene's idea and
  differs from the other scenes' titles. `subtitle`: optional.
- `mascot_position`: usually leave null (default); `center` suits a title scene.
- `beats[]`: `narration`, optional `spoken`, `board` (or null), `fill_previous_blank`,
  `highlight_steps`, `pause_after`, `source_refs` (chunk ids used by this beat).
- `teacher_notes`: optional note for the teacher (sources you could not verify, suggestions).
