<!-- PROMPT_VERSION: 2 -->
# Your task: write one chapter card

A chapter card is a short title card that opens a new part of the lecture. It gives the learner a
moment to reset and tells them what this part is about. It continues the same session: it does not
restart the lecture, repeat its objectives or announce a new video.

- `title`: the chapter title in the board language (2–6 words), taken from "This scene" → chapter;
  it names the ideas of this part, never a clip, segment or timing.
- `subtitle`: optional, one short line saying what the learner will be able to do in this part.
- `chapter_label`: a short label in the board language such as "Part 2" (the number is in
  "This scene" → chapter_label).
- `beats`: zero, one or two short beats (one sentence each). Bridge from the previous part and
  announce this one, following "This scene" → `bridge_in`: "You now know what resistance is. In this
  part, you'll put a number on it." Leave `board`, `fill_previous_blank` and `highlight_steps` empty.
- `mascot_position`: leave null.
