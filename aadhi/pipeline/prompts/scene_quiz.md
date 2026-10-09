<!-- PROMPT_VERSION: 2 -->
# Your task: write one retrieval-practice quiz

The learner sees a multiple-choice question, thinks during a countdown, then sees the answer
revealed and explained. Retrieval works when the question makes them *use* what they learned, not
recognise a phrase they just heard.

# The question

- Assess the objectives listed in "This scene". Match the Bloom level: prefer a small application
  or a "what happens if…" question over recall of a definition.
- Ask about the subject matter taught so far, never about the lecture or its source document. When
  the key points carry a question from the source, you may use it, sharpened into a good
  multiple-choice item.
- One clear question, answerable from this lecture alone, readable in under ten seconds. Include
  the numbers it needs. Avoid "Which of the following is NOT…" and "all of the above".
- `correct`: the correct option text, short and unambiguous.

# Distractors (wrong options)

- Two or three distractors, each a mistake a real student makes. Base them on the listed
  misconceptions and set `misconception_key` to that misconception's key; use null when a distractor
  reflects a different slip (a unit error, a swapped formula).
- Make every option similar in length and style, so the correct one does not stand out.
- `why_wrong`: one or two kind sentences that name the mistake and point to the right idea, shown to
  learners who pick that option.
- The answer position is shuffled later; just give the correct option and the distractors.

# Narration

- `question_beats` (1–2 beats): lead in from what was just taught ("Let's check that idea"), read
  the question naturally and invite the learner to choose: "Take a moment — pause the video if you
  like." Do not hint at the answer.
- `countdown_seconds`: thinking time, usually 6–12 seconds (longer for calculations).
- `reveal_beats` (1–3 beats): state the correct answer, explain why it is right, and address the most
  tempting distractor. Spoken text only, no symbols.
- `explanation`: a short written explanation shown on screen at the reveal (board language).
- `bloom`: the level this question assesses. `source_refs`: chunk ids supporting the answer.
- `title`: a short title such as "Quick check: Ohm's law" in the board language.
- Leave `board`, `fill_previous_blank` and `highlight_steps` empty in every beat.
