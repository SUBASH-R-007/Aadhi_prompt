<!-- PROMPT_VERSION: 2 -->
# Your task: write one real-world footage scene

A short generated video clip shows something real while Aadhi narrates over it. It exists to connect
the idea to the physical world the learner lives in.

# The clip (`video_prompt`)

- Describe realistic footage: subject, action, setting, camera (for example "slow dolly-in",
  "close-up", "aerial"), lighting and mood, in 2–4 sentences. Example: "Close-up of a ceiling fan
  in an Indian classroom slowly speeding up, sunlight through the windows, shallow depth of field,
  documentary style."
- Show the phenomenon itself; the clip must make sense without sound. "Author's visual
  suggestions", when given, may tell you what real scene the author had in mind.
- The clip shows only the real world: no mascot, no animal character, no cartoon, no on-screen text,
  no logos, no recognisable public figures.
- `rationale`: one sentence on what the learner understands better by seeing this.
- `fallback_image_prompt`: a single still image that conveys the same idea (used when video is
  unavailable). `fallback_figure_id`: a figure id from "Figures" if one shows it better, else null.

# Narration

- 2–5 beats. Bridge in from the previous scene ("This scene" → `bridge_in`), point at what the
  learner is seeing ("Notice how the blades blur as the speed rises"), then connect it to the
  concept and lead on to the next scene.
- Talk about what is on screen, never about the clip itself as a piece of media.
- Spoken text only; leave `board`, `fill_previous_blank` and `highlight_steps` empty.
- `title`: short, in the board language.
