# Learning science for Aadhi's two video formats (16:9 ~5 min, 9:16 3-4 min)

Researcher topic key: `learning-science`. Compiled 2026-10-09. The repo was only read, never changed.

## 0. How to read this report

**Evidence grades**
- **[A]** A meta-analysis, a meta-meta-analysis or a very large field dataset.
- **[B]** Several controlled experiments, or one strong randomised controlled trial (RCT).
- **[C]** One small study, indirect evidence, or a qualitative study.
- **[D]** Practitioner, industry or standards guidance, or a design heuristic I derived. These are not learning-outcome evidence.

**Verification tags**
- **(V)**: I checked the number or claim this session against the primary source: the paper's PDF, the publisher's abstract, or the official page.
- **(S)**: I took it from a secondary summary I saw this session, such as a search-engine abstract summary or another paper's description.
- **(M)**: It comes from my prior knowledge of the literature and was **not** re-verified this session. Check it before quoting it in user-facing text.

**Research limits**
- Midway through the work, the WebSearch budget shared by all agents in this run ran out.
- Several classic findings below are therefore tagged (M).
- Springer, SAGE, Wiley, Taylor & Francis and PMC pages blocked direct fetching. I verified the key numbers from open PDFs where they existed: Noetel et al. 2022, Guo et al. 2014, Szpunar et al. 2013, Kämpfe et al. 2011 and Phan 2023.

**Effect sizes.** These are Hedges' g or Cohen's d. As a rough guide, 0.2 is small, 0.5 is medium and 0.8 is large. Noetel et al. (2022) point out that meta-analytic effects are much smaller than the d > 1.0 values in older narrative reviews. Plan for **g ≈ 0.2-0.5 per principle**.

---

## 1. Executive summary: the 12 findings that matter most for Aadhi

1. **The always-on mascot is the textbook "persistent seductive detail".**
   - Removing seductive details improves learning: g = 0.33 [0.18, 0.48], k = 68.
   - The benefit is larger when the detail stays on screen persistently (g = 0.43) than when it is transient (g = 0.12). [A](V) Sundararajan & Adesope 2020, via Noetel et al. 2022.
   - Mayer's image principle (adding the speaker's image) has a median d of only 0.20, positive in 4 of 7 tests. [A/B](V)
   - Instructor-presence meta-analyses show higher motivation and affect, but no reliable gain in learning. A visible instructor also **reduces dwell time on the relevant visuals**. [A](S) Alemdag 2022; Beege et al. 2023.
   - Conclusion: confining Aadhi to a 5-10 s intro and an optional 5-10 s recap is the evidence-aligned design.
2. **Design quality matters most exactly where we are.**
   - Multimedia principles help more in **system-paced** media such as a played video (g = 0.41) than in self-paced media (g = 0.27).
   - They help much more for **complex** material (g = 0.70) than for simple material (g = 0.20). [A](V) Noetel et al. 2022.
   - Engineering lectures delivered as MP4s and reels are both. Every rule below carries more weight for us than for a typical slide deck.
3. **Short wins.**
   - In 6.9 M edX sessions, median engagement time never exceeded about 6 minutes, whatever the video's length.
   - Videos of 0-3 and 3-6 min were watched almost to the end. Over 9 min, students typically watched less than half.
   - Post-video problem attempts fell from 56% (0-3 min) to 31% (12-40 min). [A](V) Guo, Kim & Rubin 2014.
   - The ~5 min horizontal target is inside the safe window. Segment it internally anyway.
4. **Segmenting works, and works best when the instructor sets the segments.**
   - Overall g = 0.34. Instructor-segmented g = 0.41, against learner-segmented g = 0.20. Cognitive load falls (g = 0.23). [A](V) Rey et al. 2019, via Noetel.
5. **Signalling, contiguity and modality are the reliable heavy hitters.**
   - Signalling: g = 0.43, k = 209.
   - Temporal contiguity: g = 0.78.
   - Spatial contiguity: g = 0.63.
   - Modality (narration rather than on-screen text): g = 0.38, or 0.20 after correcting for publication bias. [A](V)
6. **Interpolated retrieval questions are the single best anti-mind-wandering tool.**
   - In a 21-min lecture split into four ~5.5-min segments, short quizzes after each segment cut mind wandering from about 40% to 19% of probes.
   - Notes taken rose from 7-9% to 24% of slides, and final-segment recall rose from 65-70% to 89% (d ≈ 1.0).
   - Anxiety about the final test went *down*. [B](V) Szpunar, Khan & Schacter 2013.
   - Embedded questions help most when combined with good visuals. [B](V) Kestin & Miller 2022.
7. **Background music is not forbidden, but it must stay out of the narration's way.**
   - Coherence research shows music plus sound effects under narration can hurt badly. [B](S) Moreno & Mayer 2000.
   - A 2023 meta-analysis of learning broadly finds a small overall benefit (d = 0.31), largest when music is played **before** the assessed learning. [A](S) de la Mora Velasco et al. 2023.
   - The general meta-analysis finds a null overall effect, but music disturbs reading and memory a little. [A](V) Kämpfe et al. 2011.
   - Lyrics and "fast + loud" are the clear harms.
   - The instructional-video RCT shows the workable recipe. Music at full level during the **non-narrated** pre-training helped retention (η²p = .083). Music **ducked by 10 dB** under narration neither helped nor hurt. [B/C](V) Phan 2023.
8. **Captions are strongly positive for second-language learners.**
   - Captioning second-language video: g = 0.99 for comprehension. [A](V)
   - Mayer lists subtitles for second-language narration as an evidence-based principle. [B](V)
   - Verbal redundancy is small but *positive* overall (g = 0.15). Adding text to audio gives g = 0.29. [A](V)
   - Most of our students learn engineering in English as a second language. Captions should be on by default in both formats.
9. **Pleasant colour and anthropomorphic design help a little, but only when they serve the content.**
   - Brom et al. 2018: g = 0.35. Wong & Adesope 2021: retention 0.35, transfer 0.27, comprehension 0.29. Perceived difficulty falls. [A](V/S)
   - Decorative animation does **not** help (g = −0.05). Representational animation does (g = 0.40). [A](V) Höffler & Leutner 2007, via Noetel.
10. **Voice: conversational, positive, natural-sounding.**
    - Personalisation (conversational style): g = 0.33 [A](V); median d = 1.00 in Mayer's own tests [B](V).
    - Faster, enthusiastic speakers were more engaging. [A](V) Guo 2014.
    - Positive or enthusiastic instructors raise emotion, motivation and achievement. [A](S) 2024 meta-analysis of 37 studies.
    - Modern neural text-to-speech (TTS) mostly matches human narration in learning outcomes. [B/C](S)
    - A "cute" synthesized voice hurt learning and attention. [C](S)
11. **Speech rate.**
    - edX videos ran at 48-254 words per minute (wpm), mean 156. Engagement generally rose with rate. [A](V)
    - Artificial speed-up of video lowers retention (g = −0.41) and raises cognitive load (g = 0.59). Control videos ran at 140-205 wpm. [A](V) Huang, Du & Yang 2025.
    - Our audience is second-language and the playback is fixed-pace, so target about 150-170 wpm.
12. **"Nothing monotonous" must mean variety in the container, never in the code.**
    - Vary background, palette, layout, voice and music between segments and between videos.
    - Keep the *semantic* colour and highlight grammar constant inside a video. Signalling only works if the cue means the same thing every time.
    - Change things at concept boundaries, not mid-explanation.
    - Visual changes that are related to the content raise arousal and memory. [B](V) Lang et al. 2000.
    - Unrelated rapid switches cost memory. [B/C](S)

---

## 2. Mayer's principles: effects, boundary conditions, and what each means for us

**Sources**
- Median d and test counts: Mayer's table (adapted from *Multimedia Learning*, 3rd ed., 2021), reproduced in "The Future of Multimedia Learning" (EdTech Books). (V)
- Pooled g values: Noetel et al. 2022, *Review of Educational Research*, 92(3), 413-454, doi:10.3102/00346543211052329. This covers 29 reviews, 1,189 studies and about 78-79k participants. (V)
- The 2024 summary of the theory is Mayer 2024, *Educational Psychology Review*, doi:10.1007/s10648-023-09842-1. Only the abstract was readable here.

| Principle | Mayer median d (positive tests / total) | Meta-analytic pooled g (Noetel 2022) | Boundary conditions | Implication for Aadhi |
|---|---|---|---|---|
| **Coherence** (cut extraneous material) | 0.86 (18/19) | Removing seductive details: 0.33. Persistent details: 0.43. Transient details: 0.12. Learners without prior experience: 0.52. | Strongest for novices and for details that stay on screen | No persistent decoration; mascot only in intro and recap; background motion slow and low-contrast |
| **Signalling** (cues to what matters) | 0.69 (15/16) | 0.43 [0.35, 0.50], k = 209. Cognitive load −0.25. Relevant fixations +0.39. | Larger for low prior knowledge (Richter 2016) | One highlight per narrated key term, timed to the word |
| **Redundancy** (graphics + narration, without full printed text) | 0.10 (8/12) | Verbal redundancy overall 0.15. Text added to audio 0.29. Audio added to text −0.04 (n.s.). | Redundancy hurts most with long verbatim text plus pictures and fast pacing. It is harmless or helpful with short or keyword text, second-language learners, and no competing pictures. | Board shows keywords only (≤ 8 words per item). Captions are a separate, toggleable track (see §6). |
| **Spatial contiguity** | 0.82 (9/9) | Contiguity 0.74. Spatial 0.63. | Strongest when many elements interact | Labels sit *on* the diagram. The side panel sits next to the board text it explains, never at the far edge. |
| **Temporal contiguity** | 1.31 (8/8) | 0.78 | Strong and reliable | Reveal each item as it is spoken (per-word timing already exists) |
| **Segmenting** | 0.67 (7/7) | 0.34. Instructor-paced segments 0.41. Learner-paced 0.20. Load −0.23. | Bigger for complex material and fast pacing | Explicit 45-90 s chapters (16:9) and 20-40 s beats (9:16), each with a boundary cue |
| **Pre-training** (key terms first) | 0.78 (10/10) | (no pooled estimate) | Helps most for complex, unfamiliar material | 10-20 s "3 key words" card after the hook. Music may play under it if it is not narrated (Phan 2023). |
| **Modality** (spoken rather than printed words) | 1.00 (18/19). Text also reports median 0.65 over 76 studies. | 0.38. 0.20 after bias correction. | Vanishes when pace is slow or self-paced, material is simple, text is long, or learners are experts | Narration carries the explanation. The screen carries visuals and keywords. |
| **Multimedia** (words + pictures) | 1.35 (13/13) | (no pooled estimate) | Pictures must be relevant | Every beat needs a meaningful visual: diagram, chart, animation or example, not stock decoration |
| **Personalisation** (conversational "you/we") | 1.00 (13/15) | 0.33. Cognitive load −0.62. | Can fade over long lessons. Rudeness or over-familiarity can backfire. | Script prompt: second person, contractions, rhetorical questions, polite |
| **Voice** (human-like voice) | 0.74 (6/7) | (no pooled estimate) | Older studies used robotic TTS; modern neural TTS is close to parity (§7) | Neural voices only. No cartoon voices for the main narration. |
| **Image** (speaker's image on screen) | **0.20 (4/7)** | Pedagogical agents 0.19 overall. 2D 0.38, 3D 0.11. Agent that gestures as a signal 0.28-0.44. | Helps only when the agent *signals* (points or gazes at the content) | Aadhi at the start and end only; when present, Aadhi points or looks at the topic card |
| **Embodiment** (gesture, gaze, human-like movement) | 0.58 (15/17) | (no pooled estimate) | Applies to agents that are on screen | Aadhi's intro clip should gesture toward the title or visual. Our own content should "point" with arrows and zooms. |
| **Immersion** (VR versus 2D) | −0.10 (6/9) | (no pooled estimate) | Not useful for learning | Skip 3D or VR for its own sake. Use 3D only when spatial structure is the content. |
| **Generative activity** (summarise, explain, draw, self-test) | 0.71 (37/44) | (no pooled estimate) | Learners need enough knowledge to generate | Pause-and-recall prompts, "explain it in one sentence" prompts, recap as retrieval |
| *Animation versus static* | (not in Mayer's table) | 0.23 (Berney & Bétrancourt). Representational 0.40. Decorational −0.05 (Höffler & Leutner). | Only meaningful motion helps | Animate the *mechanism*, not the wallpaper |
| *Pleasant colours / anthropomorphics* | (not in Mayer's table) | 0.35. Intrinsic motivation 0.25. Perceived difficulty −0.21. | Benefit shrinks if colour does not fit the learning goal | Warm, saturated accents on relevant objects. Give abstract agents (current, virus, packet) faces only when it clarifies their role. |
| *Captions, second-language video* | (Mayer & Fiorella "subtitle principle") | 0.99 comprehension. 0.87 vocabulary. | Large effects | Captions on by default for our learners |

**Moderators that did NOT matter** (Noetel; V):
- Learner education level, presentation format and subject domain did not reliably moderate the effects.
- The expertise-reversal moderator (prior knowledge) did not reach significance at the meta-meta level.
- Treat the principles as defaults for every course and level. Advanced classes can turn scaffolds down a notch (§10).

**A caution against over-designing** [C](S). A 2025 *Frontiers in Psychology* experiment found no learning difference between rich, sparse and no-multimedia formats when content and teaching were already strong. Production gloss does not replace clear explanations and good examples. The pipeline's content stages (concept brief, plan, critic) remain the main lever.

---

## 3. Video length, dropout and segment length

### 3.1 Guo, Kim & Rubin 2014 (ACM L@S; doi:10.1145/2556325.2566239) [A](V)

**Dataset.** 862 videos, 127,839 students and 6,902,358 sessions from four edX courses (Fall 2012).

**Length**
- Median engagement time was at most about 6 min in every length bucket.
- Normalised to video length, the median was about 1.0 for 0-3 and 3-6 min videos and about 0.95 for 6-9 min.
- It fell to about 0.55 for 9-12 min and about 0.2 for 12-40 min.
- For 0-3 min videos, 75% of sessions covered more than three-quarters of the video.
- Post-video problem attempts by video length were 56%, 48%, 43%, 41% and 31%.

**Style**
- Talking head interleaved with slides beat slides alone. In one course, 46% of viewers attempted the problem after the talking-head videos, against 33% after the others.
- The authors suggest picture-in-picture may beat jarring full-screen switches.
- Khan-style continuous hand-drawing held attention 1.5-2× longer than slides or screencasts (40% problem attempts against 31%). "Motion and continuous visual flow" was the recommendation.
- Lectures planned for video beat chopped classroom recordings. Planning had the biggest impact.

**Speech rate**
- Mean was 156 wpm (SD 31), with a range of 48-254.
- Within each length bucket, engagement generally rose with speaking rate, up to 2× between buckets. The 185-254 wpm bucket was the most engaging.
- The authors attribute this to energy and enthusiasm rather than speed itself, and recommend not forcing anyone to slow down.
- They found the 254 wpm videos easy to follow because the same information was also on the slides.

**Tutorials versus lectures**
- Students watched only 2-3 min of tutorial videos regardless of length, and rewatched tutorials more.
- Lectures need a good first-time viewing. Tutorials need skimmability (bookmarks and big visual signposts).

**Limitations.** These are observational data from 2012 STEM MOOCs with self-motivated adults. Engagement is a proxy, not learning.

### 3.2 Attention over time

- The popular "10-15 minute attention span" lacks solid support (Bradbury 2016, *Advances in Physiology Education*; Wilson & Korn 2007). [C](M)
- Attention lapses come early and often in lectures and increase over time. They fall after active segments such as demonstrations and questions (Bunce, Flens & Neiles 2010; Risko et al. 2012). [B/C](M)
- Practical reading: there is no magic minute. Sustained passive input decays, and **structural resets** (questions, demos, scene changes tied to new ideas) restore attention.

### 3.3 Short-form specifics

- **Platform limits.** YouTube Shorts are **up to 3 minutes**, square or vertical. [D](V) YouTube Help.
- My inference: a 3:01-4:00 vertical upload will not be treated as a Short on YouTube.
- Instagram Reels were extended to 3 min in early 2025 [D](M).
- **Fragmentation risk.** TikTok-style rapid context switching impaired prospective memory (n = 60). Equivalent YouTube or Twitter use did not. [C](V) Chiossi et al., CHI 2023, arXiv:2302.03714.
- Our reels should keep **one coherent narrative thread**, a single-topic micro-lesson. They should not be a montage of unrelated facts.
- No controlled study I found compares learning from vertical and horizontal versions of the same lesson. The 9:16 recommendations below extrapolate from multimedia principles and platform behaviour. [D]

### 3.4 Segment length: verdict

**16:9 (~5 min = ~300 s)**
- 3-4 content segments of **60-90 s** each.
- Plus a hook and pre-training block of about 20-30 s, and a retrieval recap of about 30-40 s.
- Szpunar used about 5.5-min segments for a 21-min lecture, so our shorter segments are conservative.
- Segments of 60-90 s give 2-3 natural question points inside a 5-min video.

**9:16 (3-4 min)**
- **5-7 micro-segments of 20-40 s.** Each opens with a micro-hook (question or claim) and closes with a micro-check or "remember this" card.
- If the teacher picks "Shorts-safe", cap the reel at **≤ 2:59** with 4-5 micro-segments.

---

## 4. Attention, pattern interrupts and visual-change cadence

### 4.1 Evidence

- **Orienting responses** [B](V/S)
  - Structural changes (cuts, edits, new elements, sound onsets) trigger automatic orienting responses. This is the Limited Capacity Model of Motivated Mediated Message Processing (LC4MP).
  - Lang, Zhou, Schwartz, Bolls & Potter 2000 (*J. Broadcasting & Electronic Media*, 44(1), 94-109) varied the edit rate *within the same scene* from slow to very fast.
  - Physiological arousal, self-reported arousal and memory all rose with edit rate, without a substantial rise in load.
- **Related versus unrelated cuts** [B/C](S)
  - LC4MP summaries of Lang et al. (1993, *Communication Research* 20(1)) hold that related edits support memory. Unrelated cuts, especially rapid ones, do not.
  - With complex messages, orienting responses can *divert* resources from encoding.
- **Hollywood shot lengths** [D](M)
  - Average shot length fell to about 4 s by the 2000s (Cutting et al.).
  - This is an attention-holding norm for narrative film, not a learning result.
- **Children** [C](M)
  - Very fast, fantastical cartoons briefly impaired executive function in 4-year-olds (Lillard & Peterson 2011).
  - It is a weak analogue for college students, but it argues against frenetic pacing on complex content.
- **Event segmentation** [B](M)
  - People parse continuous experience into events. Changes in context (location, goal, visual scene) are perceived as boundaries.
  - Information at boundaries is encoded strongly, and integration across boundaries is weaker (Zacks & Swallow 2007; Swallow, Zacks & Abrams 2009).
  - Implication: put big visual changes (new background, new palette, new layout) **at concept boundaries**. Keep the visual context stable while one idea is being built.
- **Guo 2014 and Kim et al. 2014** [A](V/M)
  - Continuous motion (Khan-style) beats static slides for engagement.
  - Kim et al. 2014 (L@S) report that re-watch and interaction peaks cluster at visual transitions (M).

### 4.2 Verdict: two kinds of change

1. **Micro-changes** run *within* a concept and are always related to the narration. Examples:
   - an element reveal timed to its word
   - a highlight or underline sweep
   - a zoom or pan to the part being discussed
   - an arrow drawn
   - a value in a chart animating
   - a step of a worked example appearing

   These are "related edits". They raise arousal and memory, and add little load.
2. **Macro-changes** run *between* concepts. Examples:
   - a new background or scene template
   - a palette shift
   - a layout swap (board to full-bleed diagram to split view)
   - a music phrase change
   - a short transition sting

   These are event boundaries. Use them as segment signposts, never in the middle of a derivation.

The numeric cadence is in §11.

---

## 5. Retrieval practice, in-video questions, prequestions and worked examples

**Interpolated testing** [B](V). Szpunar et al. 2013 (*PNAS* 110:6313-6317), Experiment 2:

| Measure | Tested group | Restudy group | No-test group |
|---|---|---|---|
| Mind-wandering probes | 19% | 39% | 41% |
| Slides with notes | 24% | 9% | 7% |
| Final-segment recall | 89% | 65% | 70% |

- d ≈ 1.0-1.4.
- Restudying the same items with answers gave **no** benefit. The act of retrieval mattered, not re-exposure.
- The tested group reported lower anxiety and lower perceived mental effort.

**Questions plus visuals** [B](V)
- Kestin & Miller 2022 (*Phys. Rev. PER* 18, 010148): embedded questions increased learning.
- The benefit was largest when the video also had enhanced visuals. The two effects compound.

**Interaction features meta-analysis** [A](S)
- Ploetzner 2022 (*Interactive Learning Environments*, doi:10.1080/10494820.2022.2123002) reports g ≈ 0.52 for enhanced interaction features in videos, including questions and prompts.
- The boundary condition: in flipped classrooms with plenty of in-class practice, embedded questions in pre-class videos showed no extra benefit (Deng & Gao 2023). [C](S)

**Testing effect** [A](M)
- Practice testing beats restudy with medium effects. Adesope, Trevisan & Sundararajan 2017, *Review of Educational Research*: g ≈ 0.5-0.6.
- The effect holds in real classrooms (Yang et al. 2021, *Psychological Bulletin*, g ≈ 0.5).
- Feedback after the attempt strengthens it.
- Fiorella & Mayer 2018 list practice *without feedback* among features that did not help. [B](S)

**Prequestions and pretesting** [B](M)
- Asking a question *before* the content improves learning of that content in video lessons. It does not hurt, and can help, related content that was not asked about (Carpenter & Toftness 2017, *JARMAC*).
- Pan & Carpenter 2023 (*Educational Psychology Review*) review this prequestion effect.
- This is the learning-science form of the "hook".

**Curiosity** [B](M)
- Information-gap theory (Loewenstein 1994).
- High-curiosity states improve memory for the answer, and even for incidental material shown during that state (Kang et al. 2009; Gruber, Gelman & Ranganath 2014, *Neuron*).
- An opening question the learner cannot yet answer, resolved later in the video, is evidence-backed. It is not just a marketing trick.

**Generative prompts** [B](V)
- The generative-activity principle has median d = 0.71 (37/44).
- Mayer, Fiorella & Stull 2020 list prompts to summarise or explain among their five evidence-based ways to improve instructional video.

**Worked examples** [A](M)
- The worked-example effect is robust for novices. Barbieri et al. 2023's mathematics meta-analysis finds g ≈ 0.4-0.5 (M).
- Fading (gradually removing steps) and self-explanation prompts add to the effect.
- For experts the effect can reverse (expertise reversal; Kalyuga). (M)

**Cognitive conflict / refuting misconceptions** [C](V). Noetel lists "cognitive conflict" (state a common misconception, then refute it) as emerging but not yet meta-analysed. A "common mistake" beat suits engineering topics well.

**Placement verdict**
- Hook prequestion at 0-10 s, answered later in the video.
- After each content segment: one retrieval question, a 3-5 s think pause with a visible countdown, then the reveal with a one-line explanation (feedback).
- In the live web player the video can actually pause for input. In the MP4 the pause is a timed hold.
- The final recap *is* retrieval: ask first, then reveal. Do not just restate.

---

## 6. On-screen text, redundancy and captions

**The evidence**
- **Verbal redundancy** [A](V). Adesope & Nesbit 2012, via Noetel:
  - overall g = 0.15
  - adding text to audio: +0.29
  - adding audio to text: −0.04
  - Benefits held across system- and learner-paced lessons and across language-fluency levels.
- **Mayer's redundancy principle** [B](V). The median d is now only 0.10 (8/12). The classic harm appears with *full verbatim* printed text plus pictures in fast, system-paced lessons.
- **Second-language learners** [A/B](V)
  - Captioning gives g = 0.99 for comprehension (Montero Perez et al. 2013).
  - Mayer, Fiorella & Stull 2020 formalise the "subtitle principle": add subtitles to narrated video in the learner's second language.
  - Lee & Mayer 2018 are the source experiments (M).
- **Abridged on-screen text** [B](M). Short, key-phrase text alongside narration did better than either no text or full text (Yue, Bjork & Bjork 2013). Keywords are the sweet spot.
- **Caption readability** [D](V). Netflix's English Timed Text Style Guide sets:
  - ≤ 42 characters per line
  - ≤ 2 lines
  - ≤ 20 characters per second (cps) for adult programmes, ≤ 17 cps for children's
  - break lines at natural phrase boundaries, bottom-heavy
- **Accessibility.** Captions are required for deaf and hard-of-hearing viewers. Under WCAG 1.2.2 they are a Level A requirement for prerecorded video (M). Muted autoplay is the norm in vertical feeds [D].

**Verdict: resolving the captions-versus-redundancy tension**
1. The **board** carries keywords, formulas, labels and short phrases. It never carries the narration verbatim.
2. **Captions** are a separate, phrase-level track.
   - **On by default** in both formats, because of our second-language audience and muted viewing.
   - Placed in a reserved zone that never overlaps the board, diagram or formula.
   - In 16:9 they stay toggleable (soft subtitles, WebVTT/SRT). Burn them in only on request.
   - In 9:16 burn them in by default: feeds autoplay muted, and many players ignore soft subtitles.
3. **Keyword highlighting inside captions.** A subtle colour change on the current word or key term is common practice in reels [D]. It has no direct learning evidence. Use it only in 9:16, only for key terms (not every word), and in the same accent colour as the board highlight, so the two cues agree.
4. **Reading load.** Caption rate ≤ 17 cps. The repo's board reading-time check (200 wpm English) stays. Add the rule that the board text plus the caption text visible at any moment fits the reading budget of the time it is on screen.

---

## 7. Voice and speech rate

**Human versus synthetic voice**
- Mayer's voice principle has median d = 0.74 [B](V). It was established with older, robotic TTS.
- Craig & Schroeder (2017, 2019) found modern TTS engines comparable to human voices. [B](M)
- Recent work is mostly at parity [B/C](S):
  - Xu et al. 2025, *British Journal of Educational Technology*, doi:10.1111/bjet.13530: AI-generated video gave better vocabulary retention and lower load than recorded video, with lower social presence.
  - Netland et al. 2025, *Computers & Education*: equivalent test performance.
  - Leiker et al. 2023: no difference.
- One 2024 small-N study (n = 30) found a human voice beat two synthesized voices on learning and attention [C](S). The *Journal of Experimental Education* title reports that a "cute" computer-synthesized voice **hindered** learning (doi:10.1080/00220973.2024.2446169).
- **Verdict.** Use high-quality neural voices with natural prosody, and keep a human-recorded option for teachers who want it. Never use novelty, chipmunk or "cute" voices for the explanation. Aadhi's character voice belongs only in the intro and recap.

**Style**
- Conversational (personalisation g = 0.33). [A](V)
- Enthusiastic and positive. A 2024 *Educational Psychology Review* meta-analysis of 37 studies (doi:10.1007/s10648-024-09859-0) found higher emotion, motivation and achievement, with no extra cognitive load. [A](S)
- Guo's most engaging speakers were the energetic ones. [A](V)
- Avoid monotone delivery. Positive does not mean hyperactive.

**Rate**
- edX mean was 156 wpm. The most engaging bucket was 185-254 wpm, but those were native-speaker MOOC audiences with redundant slides. [A](V)
- Speeding up playback lowers retention (g = −0.41) and raises cognitive load (g = +0.59) relative to baselines of 140-205 wpm. [A](V) Huang, Du & Yang 2025, *Frontiers in Psychology* 16:1427609.
- Murphy et al. (2021/22, *Applied Cognitive Psychology*) found small costs at 1.5-2× and clear costs beyond 2×. [B](V)
- Second-language listeners comprehend better at slower rates; lower-intermediate learners did best near 125-130 wpm (Griffiths 1992). [C](M)
- Non-native listeners need about 3 dB more speech-to-background ratio for equal intelligibility. [C](S) EBU Technical Review 2012.
- **Verdict:**
  - 16:9 core narration: **150-165 wpm** effective rate including pauses.
  - Reels: **160-175 wpm**.
  - Hard ceiling: **185 wpm** for any narrated passage.
  - For formulas, code and step-by-step derivations, allow 130-145 wpm.

**Variety across videos**
- No evidence says that changing narrator between videos harms learning.
- Narrator gender matching does not help. Fiorella & Mayer 2018 list learner-instructor gender matching among features that did not improve learning. [B](S)
- So rotating voices across videos is free, and it serves the teacher's anti-monotony goal.
- **Within** a video, keep one primary narrator. Talker changes cost a little perceptual adaptation (talker-variability effects in speech perception, M), and voice consistency acts as a stable cue.
- A second voice is useful when it has a **role**: a "student" asking the misconception question, a quiz announcer, or Aadhi in the intro and recap.
- Dialogue formats delivered by TTS are being studied. In 222 students, effects on motivation depended on the learner and were small to medium (Watanabe et al., arXiv:2608.20822, Aug 2026). [C](V)

**Accent**
- Familiar accents are easier to understand (M).
- For Indian learners, offer en-IN voices among the options and keep at least one in the default rotation.

---

## 8. Background music: what the evidence actually says, and how to use it

### 8.1 Evidence

- **Coherence experiments** [B](S)
  - Moreno & Mayer 2000 (*J. Educ. Psychol.* 92(1)) added background music **and** sound effects to narrated animations (lightning, brakes). Retention and transfer fell, with a very large effect (d ≈ 1.66, as reported by Phan 2023).
  - Mayer classes background music as an extraneous, seductive detail.
  - The caveat: music was combined with sound effects and short, dense, system-paced lessons, the worst case for load.
- **General meta-analysis** [A](V). Kämpfe, Sedlmeier & Renkewitz 2011 (*Psychology of Music* 39(4), 424-448):
  - global effect null
  - background music disturbs reading and has small detrimental effects on memory
  - it improves emotional reactions and sports performance
  - music tempo drives the tempo of activity
  - The null overall effect probably averages out opposite specific effects.
- **Learning meta-analysis** [A](S). de la Mora Velasco, Chen, Hirumi & Bai 2023 (*Psychology of Music* 51(6), 1598-1626, doi:10.1177/03057356231153070):
  - 47 studies, 71 effect sizes
  - small positive overall effect, d = 0.314
  - **medium** effect when music was played **before** the learning assessment
  - small effects for retaining facts and for classical music versus other genres
  - age may moderate
  - It covers learning in general, not narrated video specifically, and studies up to 2021.
- **Instructional-video RCT** [B/C](V). Phan 2023 (*Journal of Educators Online*; ERIC EJ1393962), 2×2 design, N = 92 undergraduates learning in English:
  - The video opened with a pre-training message (character names and roles) and then ran 7:44 of narrated content.
  - Music was Mozart K. 448, 2nd movement, chosen for its steady tempo and dynamics.
  - Music in the **non-narrated pre-training** improved retention (F(1,88) = 8.01, p = .006, η²p = .083).
  - Music **dimmed to −10 dB under narration** had no effect (p = .53).
  - The author concludes music fits best where no narration competes, and that combining music with narration is acceptable if the volume is controlled.
- **Fast and loud is the harm** [B](S). Thompson, Schellenberg & Letnic 2012 (*Psychology of Music* 40(6), 700-708):
  - Instrumental music impaired reading comprehension only in the **fast + loud** condition.
  - A secondary source gives about 150 bpm and about 72 dB as the fast and loud levels; 110 bpm and 60 dB were benign.
- **Lyrics** [B](S)
  - Lyrical music, liked or disliked, impaired reading comprehension relative to quiet and to non-lyrical music, which did not differ from quiet (Perham & Currie 2014, *Applied Cognitive Psychology* 28(2)).
  - This matches the irrelevant-speech and changing-state literature. Intelligible words compete with narration in the same phonological channel.
- **Working memory and personality** [B/C](V/S)
  - Lehmann & Seufert 2017 (*Frontiers in Psychology*, N = 81) played fast instrumental pop at a moderate level.
  - Comprehension was reduced overall, mostly for low working-memory learners. Arousal and mood did not mediate.
  - Gonzalez & Aiello 2019 (*J. Exp. Psychol.: Applied*): music impaired *complex* tasks, while complex music helped *simple* tasks. Preference for external stimulation moderated both.
  - Effects differ between students. A user-controllable music level in the player is warranted.
- **Hearing and second-language listeners** [D](V)
  - WCAG 2.x SC 1.4.7 (Level AAA, written for audio-only content) requires speech to be either free of background sound, able to turn it off, or **≥ 20 dB** above the background. The figure comes from hard-of-hearing research.
  - EBU work indicates non-native listeners need about +3 dB more dialogue-to-background ratio.
  - The repo already ducks narration with ffmpeg `sidechaincompress` (threshold 0.015, ratio 8, attack 15 ms, release 450 ms) on a single looping BGM at linear gain 0.06 (≈ −24 dB applied to the file, not to a loudness target). See `aadhi/compose/ffmpeg.py:1023`.

### 8.2 Verdict: how to use BGM without hurting learning

Music is a **mood and pacing layer**. Its job is boundaries, energy and brand variety. It must never compete with the explanation.

1. **Instrumental only.** No vocals, no lyrics, no vocal chops or spoken samples. This is the clearest harm in the literature.
2. **Two levels, keyed to the narration timeline**, using our per-word timings for a deterministic envelope rather than relying only on a compressor:
   - **Under narration:** music loudness **≥ 20 LU below** the narration (target −22 LU, never closer than −18 LU).
     - This exceeds Phan's −10 dB, because our learners are second-language listeners, watch on phone speakers, and sit in noisy rooms.
     - It also matches the WCAG 20 dB rationale.
   - **In narration gaps of ≥ 1.2 s** (title card, hook music sting, pre-training card, segment transitions, quiz think time, outro): rise to **−8 to −12 LU below** the narration's level.
     - This is where music is shown to *help* (Phan pre-training; de la Mora Velasco "before learning").
   - **Ramps:** duck starts **250-400 ms before** speech onset and finishes by onset. Release begins **400-700 ms after** speech ends, so the music never swells into short breaths between sentences.
3. **Tempo and energy**
   - Under narration, **60-110 BPM**, steady dynamics, sparse arrangement.
   - Avoid prominent lead melodies in the 300 Hz-3 kHz speech band while the narrator talks. Pads, soft piano, plucks, light percussion and lo-fi beats work.
   - Reels may use **up to about 120 BPM** with a stronger beat, still at −20 LU under speech. Avoid "fast + loud" anywhere narration plays.
4. **Genres that fit learning** (instrumental): calm lo-fi or chillhop, ambient or electronic pads, light acoustic, soft piano or classical, mellow cinematic.
   - Classical had a small edge in the 2023 meta-analysis, so it is a good choice for dense or mathematical topics.
   - Reserve upbeat electronic or pop-instrumental for the hook, transitions and reels.
5. **Variety.** Use a different track per video, picked from a mood-tagged pool by topic and format. Optionally change the track or phrase at chapter boundaries, as a macro-change cue. One continuous bed under a single concept is better than switching tracks mid-concept.
6. **Control.**
   - The player offers Music: Off / Low / Normal. This is the WCAG 1.4.7 "can be turned off" route and covers individual differences.
   - The render has an option for no music, or for music only in the gaps.
   - Default is **on, under-narration level as above**.
7. **No sound effects layered on narration**, except brief related cues: a soft tick on reveal, a "ding" on the quiz answer (the repo already has ticks and dings), or a whoosh on macro transitions. These are transient (≤ 1 s), related to the content, and fall in the gaps. Moreno & Mayer's harm came from continuous decorative sound on top of narration.

---

## 9. Colour: variety versus consistency, emotional design and accessibility

**Evidence**
- **Pleasant colours and anthropomorphics** [A](V/S). Brom et al. 2018 found g = 0.35 for learning and lower perceived difficulty. Wong & Adesope 2021 found retention 0.35, transfer 0.27 and comprehension 0.29, with only small effects on affect (0.09).
- **Warm colours and round shapes** [B](S). Um, Plass, Hayward & Homer 2012 (*J. Educ. Psychol.* 104) and Plass et al. 2014 (*Learning & Instruction* 29) found warm colours plus round shapes induce positive emotion and can aid comprehension and transfer. Replications are mixed, and the effect depends on the learner's starting mood and prior knowledge.
- **Colour that does not fit the learning goal** [C](S) gives null or even harmful effects (2024 *Frontiers* review).
- **Colour coding** [B](M). When matching colours link related text and diagram parts (Ozcelik et al. 2009, eye-tracking), learners find relevant information faster and retention and transfer improve. This only works if the colour-to-meaning mapping is **consistent**.
- **Signalling** [A](V) gives g = 0.43. A cue must be learnable as a cue.
- **Orienting and novelty** [B](V/S). Changes draw attention through the orienting response. Repeated identical stimuli habituate (M). This is the argument *for* periodic visual variety.
- **Decorated environments** [C](M). Children in heavily decorated classrooms spent more time off-task and learned less (Fisher, Godwin & Seltman 2014). It is a weak analogue, but it argues for low-salience backgrounds.
- **Seductive details** [A](V). Persistent decoration (g = 0.43 harm) is far worse than transient decoration (0.12).
- **Accessibility** [D](M)
  - WCAG 1.4.3: text contrast ≥ 4.5:1, or ≥ 3:1 for large text.
  - WCAG 1.4.11: graphics contrast ≥ 3:1.
  - WCAG 1.4.1: colour must not be the only carrier of meaning.
  - About 8% of men have colour-vision deficiency (M).

**Verdict: "change colours often" is supported when it means the following.**
1. **A different palette per video**, drawn from a curated set of pre-validated accessible palettes. This gives video-to-video variety for free.
2. **A palette or background shift at each segment boundary**: every 60-90 s in 16:9 and every 20-40 s in 9:16. This combines the orienting benefit with event segmentation. Never shift mid-explanation or mid-derivation.
3. **A fixed semantic colour grammar** inside every video, with roles not hues, mapped per palette:
   - `accent.key`: the key term highlight, consistent per video.
   - `accent.alt`: the second entity in comparisons.
   - `warn`: the misconception or common mistake. Always the same red-orange family across all videos, plus an icon, so it never relies on colour alone.
   - `ok`: the correct answer or result, always green-teal family plus a check icon.
   - Formula and code surfaces are consistent within a video.
   - Diagram colours that link to board terms use the same role colour (colour coding).
4. **Warm, saturated accents on relevant objects**, and calmer, lower-saturation, lower-contrast backgrounds. The background is a stage, not a performer.
5. **Hard accessibility gates**: contrast ≥ 4.5:1 for all text against its actual local background (check against the rendered frame, not the CSS token), ≥ 3:1 for diagram strokes and icons, and no information carried by colour alone.

---

## 10. Instructor / mascot presence and recap

**Evidence summary**
- Image principle d = 0.20. [B](V)
- Pedagogical agents g = 0.19 overall. 2D agents 0.38, 3D agents 0.11 (Castro-Alonso et al. 2021; the moderator is contested). [A](V/S)
- Agents help most when they **signal**: pointing or gesturing gives g = 0.28-0.44 (Davis 2018). [A](V)
- Instructor-presence meta-analyses (Alemdag 2022; Beege et al. 2023) [A](S):
  - learning is unchanged or slightly better on retention, with no transfer benefit
  - motivation and affect improve
  - the visible person draws gaze away from the relevant visuals
- Temporary instructor presence on sparse slides was no better or worse than permanent or no presence (two experiments, N = 126 and N = 219). [B](S) doi:10.1007/s11423-024-10435-0.
- Persistent seductive detail g = 0.43 harm. [A](V)

**Verdict**
- Aadhi's job is **social and motivational framing** (welcome, topic, hook, closing encouragement), which is where presence shows benefits. It is not explanation.
- **Intro:** 5-8 s in 16:9 and 3-5 s in 9:16. Aadhi greets, names the topic and points or looks at the topic card or hook visual (signalling and embodiment). Then Aadhi exits or shrinks off before the first concept.
- **Recap:** optional, 5-10 s in 16:9 and ≤ 5 s in 9:16. Aadhi introduces the retrieval recap ("Let's see what stuck"). The recap content itself is visuals and questions, not Aadhi talking.
- **Nowhere else.** Total Aadhi time is **≤ 20 s per 16:9 video** (≤ 7% of runtime) and **≤ 10 s per reel**.
- **Recap form** (§5): retrieval first (2-3 questions, think pause, reveal), then a 3-bullet "big ideas" card that mirrors the pre-training card, closing the loop.
  - Pre-training (d = 0.78) and recap bracket the lesson. The same three keywords appear in both.

---

## 11. Conflicts and resolutions

| Tension | Resolution |
|---|---|
| "Eye-catching, never monotonous" versus coherence and seductive details | Make variety **related** and **transient**. Keep content motion continuous. Make decoration slow and low-salience. Put big changes at concept boundaries. |
| Captions (accessibility, second language) versus the redundancy principle | Board text is keywords only. Captions run on a separate phrase-level track in a reserved zone, on by default for our audience. Redundancy's harm comes from long verbatim text competing with pictures, which we avoid. |
| BGM for retention and mood versus coherence | Instrumental only. ≥ 20 LU under speech. Louder only in gaps. Player toggle. |
| Frequent colour change versus signalling consistency | Change palette and background at boundaries. Keep the role-based colour grammar fixed. |
| Fast, energetic delivery (Guo) versus second-language comprehension (Huang 2025; Griffiths) | 150-170 wpm with energetic prosody. Energy comes from intonation and enthusiasm, not raw speed. |
| Mascot appeal versus image principle and persistent distraction | Aadhi only in intro and recap, and always signalling (pointing or looking at the content). |
| Reel culture (rapid cuts) versus learning from complex content | Frequent *related* micro-changes. One coherent thread per reel. No unrelated montage. Cut cadence slows on dense steps such as formulas and code. |

---

## DESIGN IMPLICATIONS FOR AADHI

Each rule is concrete, numeric and actionable. The grade in brackets is the evidence strength from §0; [D] numbers are design judgments chosen to sit safely inside the evidence. Rules marked **LINT** can be enforced in the existing lint/critic/pacing stage (`aadhi/pipeline/quality/*`) or in the timeline/QA step.

### A. Format targets and duration splits

**A1. Horizontal 16:9 "Lesson" (target 4:30-5:30; hard max 6:00).** [A] Guo 2014; [D] splits.

| Block | Time | Content |
|---|---|---|
| Cold-open hook + Aadhi intro | 0:00-0:10 (Aadhi 5-8 s) | Hook question or surprising claim on screen from frame 0. Aadhi names the topic and points at the hook visual. |
| Pre-training card | 0:10-0:30 | Exactly 3 key terms, each with icon or visual and a 1-line meaning. Music raised in gaps. |
| Segments S1-S3 (optionally S4) | 0:30-4:10 | 60-90 s each (min 45, max 100). Each ends with a checkpoint question. |
| Worked example | inside one segment, or its own segment | Required for quantitative or procedural topics. |
| Retrieval recap | 4:10-4:50 | Aadhi ≤ 8 s intro, then 2-3 questions with reveal, then a "3 big ideas" card (same 3 terms as pre-training). |
| Outro / next step | last 5-10 s | No new content. |

**A2. Vertical 9:16 "Reel"**
- Two presets: **Shorts-safe ≤ 2:59** (default when the target is YouTube Shorts or Instagram) and **Reel-long 3:00-4:00** (other platforms).
- Hook in the first 3 s: big-type question or claim, with Aadhi ≤ 5 s *under* the hook text.
- Then **5-7 micro-segments of 20-40 s** (4-5 for Shorts-safe). Each segment:
  - opens with a 1-line micro-hook (≤ 8 words on screen)
  - covers **one** idea
  - ends with a "remember this" card or a micro-question
- Recap of 15-25 s at the end, with Aadhi ≤ 5 s.
- One topic, one narrative thread. No unrelated montage. [C] Chiossi 2023; [D].

**A3. Aadhi screen-time budget (LINT).**
- Aadhi is visible only in `intro` and `recap` scene types.
- Totals are **≤ 20 s per 16:9 video and ≤ 10 s per reel**.
- Any single Aadhi shot is ≤ 10 s.
- Aadhi must gesture or gaze at a content element (topic card or hook visual) in at least one shot.
- Aadhi never appears behind board text. [A] seductive-detail and image-principle evidence; agent-signalling evidence.

### B. Segment and scene cadence

**B1. Segment boundaries are explicit.**
- Each segment starts with a 1-2 s boundary cue: chapter title or number, palette or background shift, music phrase change or whoosh.
- Chapters are written to the MP4 metadata and the player (already supported). [A] segmenting g = 0.34-0.41; [B] event segmentation.

**B2. Visual micro-change cadence (related to narration) (LINT on the timeline).**

| | 16:9 | 9:16 |
|---|---|---|
| Median interval between visual events (reveal, highlight, zoom/pan, arrow, chart animation, example step) | **3-5 s** | **1.5-3 s** |
| Max static stretch (no new or changed relevant element) | **8 s** | **5 s** |
| Exception | Formula, code or diagram study holds, flagged `hold_reason`, ≤ 12 s, with a highlight moving through it | Same, ≤ 8 s |

Every micro-change must be tied to a narrated word or phrase within ±300 ms. [B] Lang 2000; [A] temporal contiguity; [D] numbers.

**B3. Macro-change cadence (layout, background or palette).**
- 16:9: at every segment boundary (every 60-90 s). At most one extra macro-change at a sub-concept boundary inside a segment, never mid-sentence or mid-derivation.
- 9:16: at every micro-segment boundary (every 20-40 s), plus at most one layout switch per segment.
- No macro-change in the 2 s before a quiz reveal. [B] event segmentation; [D].

**B4. Unrelated cuts.** At most 1 per 10 s, and only at segment boundaries. Never more than 2 consecutive scenes without a shared visual anchor (same diagram, character or object, or a "morph" transition). [B/C] LC4MP.

**B5. Breathers (keeps the existing `pacing.dense_run` lint).**
- After any dense board (weight ≥ 5), the next 15-30 s must be lighter: an example, an animation or a question.
- In 9:16, no two consecutive micro-segments may both contain a formula or code.

### C. Text density and signalling

**C1. Board text (16:9) (LINT).**
- ≤ 3 text items visible at once, progressively revealed.
- ≤ 8 words per item.
- ≤ 24 words of prose visible at once (formulas and code excluded, with their own budget).
- Never the narration verbatim.
- Keep the existing reading-time check (200 wpm English, factor 1.5). [A] modality, redundancy and coherence; [B] abridged text.

**C2. Reel text (9:16).**
- One headline of ≤ 6 words, plus at most one key phrase of ≤ 8 words.
- Formulas: one at a time, at a large size.
- Minimum type size: body ≥ 54 px and headline ≥ 84 px on a 1080×1920 canvas.
- Keep all text inside the platform-UI safe area, which the layout researcher will specify. [D]

**C3. Signalling (LINT).**
- Every narrated key term that appears on screen gets **one highlight**, synced to its word (onset within −200/+300 ms).
- At most **2 simultaneous highlights**, and at most **1 new highlight per 2 s**.
- The highlight persists while that item is discussed.
- Use arrows, underlines and zooms for diagrams, and the same highlight style throughout a video. [A] g = 0.43.

**C4. Spatial contiguity.**
- Labels sit on or adjacent to diagram parts (≤ 5% of frame width away). Never use legends that need eye travel.
- The side-panel visual sits adjacent to the board item it explains, and the item gets a connector or matching colour role. [A] g = 0.63.

**C5. Visuals must be representational.**
- Every beat needs a meaningful visual (diagram, chart, animation, real photo, example).
- Purely decorative stock images and decorative animation count as **zero visual** for this rule. [A] multimedia d = 1.35; decorational animation −0.05.

### D. Captions

**D1. Default on in both formats.** In 16:9, soft captions (WebVTT/SRT) are toggleable, and burn-in is an option. In 9:16 they are burned in by default. [A] second-language captions g = 0.99; [D] accessibility.

**D2. Limits (LINT).**
- Phrase-level segmentation.
- 16:9: ≤ 42 characters per line and ≤ 2 lines.
- 9:16: ≤ 26 characters per line and ≤ 2 lines.
- **≤ 17 characters per second** (second-language audience), hard max 20 cps.
- Minimum caption duration 1.0 s.
- Captions sit in a reserved zone that never overlaps board items, the formula or the diagram focus. [D] Netflix TTSG, made stricter.

**D3. Word highlighting.** Reels only. Key terms only, not every word. Use the same `accent.key` role colour as the board highlight. [D]

### E. Voice and narration

**E1. Voice pool and rotation.**
- Only high-quality neural voices from the existing providers (edge, gemini, openai, elevenlabs). A human-recorded upload is an option.
- Each video picks a voice different from the previous video in the same course or playlist (round-robin, or least-recently-used per course).
- The pool must include at least one en-IN voice, at least two male and two female voices, and at least two "warm-conversational" styles.
- Block novelty, cartoon or "cute" voices from the main narration. [B/C] voice evidence; [B] gender matching does not matter.

**E2. One primary narrator per video.**
- A secondary voice is allowed only in a role: "student" asking the misconception or question, quiz announcer, or Aadhi in intro and recap.
- Secondary speech ≤ 15% of the narration. [D] plus talker-consistency reasoning.

**E3. Speech rate (LINT on measured TTS output, using word timings).**
- 16:9 core: **150-165 wpm**. Reels: **160-175 wpm**.
- Formula, code or derivation beats: **130-145 wpm**.
- Hard ceiling **185 wpm** for any 10 s window.
- If a provider's output exceeds the ceiling, re-synthesize at a lower rate. Do not time-stretch audio. [A] Huang 2025; [A] Guo 2014; [C] second-language comprehension.

**E4. Pauses.**
- 0.25-0.45 s between sentences.
- 0.6-0.9 s after a key definition.
- 1.0-1.5 s at segment boundaries.
- Quiz think time: **3-5 s** in 16:9 and 2-3 s in reels, with a visible countdown.
- These replace the current fixed `SCENE_TAIL_SECONDS = 0.8` where the scene role calls for it. [B] Szpunar; [D].

**E5. Style prompt for script and TTS.**
- Second person, conversational, polite, positive.
- Rhetorical questions at every segment start.
- Contractions allowed.
- No sarcasm or over-familiar slang.
- Energy through intonation and enthusiasm, not speed. [A] personalisation; [A] positivity.

### F. Background music

**F1. Instrumental only (LINT on track metadata).** Reject tracks tagged vocals, lyrics, choir, or speech samples. [B] lyrics evidence.

**F2. Loudness targets (render step).**
- Narration integrated about **−16 LUFS** (mobile and web norm; the audio researcher should confirm the final delivery target).
- Music **under speech ≤ narration − 20 LU** (target −22, never louder than −18).
- Music **in gaps ≥ 1.2 s** at narration −8 to −12 LU.
- No music under quiz *questions* while they are being read aloud. Music at the gap level under think time is fine.
- Express these levels relative to measured loudness. The current fixed linear gain of 0.06 depends on how loud the music file is. [B] Phan 2023; [D] WCAG 1.4.7 rationale; [C] EBU second-language +3 dB.

**F3. Deterministic ducking envelope from word timings.**
- Duck ramp starts 300 ms (range 250-400) before speech onset. Release starts 500 ms (range 400-700) after speech offset.
- Do not release in gaps < 1.2 s.
- Keep `sidechaincompress` only as a safety limiter on top. [D]

**F4. Tempo and arrangement.**
- Under speech: 60-110 BPM, steady dynamics, no lead melody in the vocal range during speech.
- Reels: up to about 120 BPM, still at narration −20 LU under speech.
- Never "fast + loud": > 130 BPM is allowed only in gaps and stings ≤ 3 s. [B] Thompson 2012; [D].

**F5. Variety.**
- A different track per video, from a mood-tagged pool. Suggested mood tags: calm-focus (lo-fi, ambient), bright-curious (light acoustic, plucks), cinematic-wonder (soft orchestral), classical-focus (piano or strings, for dense math), upbeat (reels and hooks only).
- No track repeats within the last 5 videos of a course.
- An optional phrase or track change at chapter boundaries. No track change mid-segment. [D] plus §8.

**F6. User control.**
- The player has Music Off / Low / Normal; Low is narration −26 LU.
- The render has options for no music and for gaps-only music.
- Default: Normal as in F2. [D] WCAG 1.4.7; [B/C] individual differences.

**F7. Sound effects.**
- Only brief related cues (reveal tick, answer ding, transition whoosh), ≤ 1 s each.
- Never continuous ambience under narration.
- At most 1 SFX per 5 s. [B] Moreno & Mayer 2000.

### G. Questions, hook and recap

**G1. Hook prequestion (LINT: required).**
- Starts within the first 3 s (reel) or 8 s (16:9), shown as on-screen text and spoken.
- Explicitly answered later; mark `answers_hook: true` on the answering beat. [B] prequestion and curiosity research.

**G2. Checkpoint questions (LINT).**
- 16:9: **one retrieval question at the end of each segment** (2-4 per video), first no earlier than 0:60. Then think time (E4) and a reveal with a **one-line explanation** (feedback).
- 9:16: **at least 2 micro-questions**, around 40-60% and at the end.
- Prefer recall or "predict" formats over recognition. Keep multiple choice to ≤ 3 options in reels and ≤ 4 in 16:9. [B] Szpunar (19% vs 40% mind wandering); [A] testing effect; [B] Kestin & Miller.

**G3. Interactive player.** In the live web player, checkpoint questions pause for input. The MP4 uses the timed think hold. Log answers if analytics exist. [A] interaction features g ≈ 0.52 (S).

**G4. Retrieval recap (LINT: required in 16:9, recommended in reels).**
- Placed in the last 10-15% of runtime.
- Ask, then reveal: 2-3 questions, then a 3-item "big ideas" card that reuses the pre-training terms.
- Never a passive verbatim restatement. [A/B] retrieval; [B] pre-training d = 0.78.

**G5. Generative prompt.** At least one "pause and explain it in one sentence" or "predict what happens if…" prompt per 16:9 video, with a 3-5 s hold. [B] generative activity d = 0.71.

### H. Worked examples and misconceptions

**H1. Worked examples.**
- Quantitative or procedural topics need at least **one worked example per 16:9 video** and at least one compact example per reel.
- Reveal step by step, one step per 3-6 s, each step highlighted as it is narrated.
- For a second example, use a **faded** version: the learner predicts the last step during a think hold. [A] worked examples; [B] fading.

**H2. Common-mistake beat.** At least one per 16:9 video where the source supports it: state the misconception, mark it with the `warn` role and icon, then refute it with the visual. [C] cognitive conflict.

### I. Colour and backgrounds

**I1. Palette per video** from a curated set of at least 12 pre-validated accessible palettes, rotating least-recently-used per course. [A] emotional design; [D].

**I2. Palette or background shift at segment boundaries only** (B3). The fixed role grammar (`accent.key`, `accent.alt`, `warn`, `ok`, formula surface, code surface) is identical in function across all videos. `warn` and `ok` keep fixed hue families plus icons. [A] signalling; [B] colour coding.

**I3. Backgrounds**
- Low-salience: animated drift period ≥ 15 s, max motion ≤ 2% of frame width per second.
- Luminance contrast between background regions behind text ≤ 1.5:1.
- No particles, sparkle or high-frequency motion behind text. Allowed in transitions ≤ 1.5 s.
- Saturation lower than the content accents. [A] persistent versus transient seductive details; [C] decorated-environment evidence.

**I4. Accessibility gates (LINT on rendered frames).**
- Text contrast ≥ 4.5:1 against the actual local background, ≥ 3:1 for text ≥ 24 px bold or ≥ 32 px regular in 1080p.
- Diagram strokes and icons ≥ 3:1.
- No meaning carried by colour alone: the `warn` and `ok` icons are mandatory, and series are distinguished by label or pattern too. [D] WCAG 1.4.1, 1.4.3, 1.4.11.

**I5. Anthropomorphics.** Allowed for abstract agents (an electron, a packet, a virus) when it clarifies their role. Not more than one anthropomorphised agent per scene. [A] Brom 2018 g = 0.35.

### J. Editor and external clips

**J1. Inserted external clips** obey the same rules:
- Relevance check: the editor asks the teacher to tag what concept the clip shows.
- A default max clip length of 30 s in 16:9 and 12 s in 9:16 before a warning.
- Clip audio normalised to the narration target. Narration over a clip ducks the clip's audio to −20 LU.
- Captions are generated for the clip's speech. [A] seductive-detail evidence (Mayer, Fiorella & Stull 2020: interesting but irrelevant video does not help); [D].

**J2. Cut-unnecessary-parts tooling** should highlight candidates:
- static stretches beyond B2
- dead air > 1.5 s outside planned holds
- filler words in a human-recorded voice

Guo's producers found tight editing (removing "umm" and pauses) part of what made videos engaging. [A](V) recommendation; [D].

### K. QA metrics to add to `evals/metrics.py` and the post-render check

Each metric is listed with its threshold.

| Metric | 16:9 threshold | 9:16 threshold |
|---|---|---|
| `aadhi_seconds_total` | ≤ 20 | ≤ 10 |
| `max_static_seconds` | ≤ 8 (≤ 12 with `hold_reason`) | ≤ 5 (≤ 8 with `hold_reason`) |
| `median_visual_event_interval_s` | 3-5 | 1.5-3 |
| `segment_seconds` | within 45-100 | within 15-45 |
| `speech_wpm_p90` | ≤ 175 | ≤ 175 |
| `speech_wpm_max_10s` | ≤ 185 | ≤ 185 |
| `caption_cps_max` | ≤ 20 (target ≤ 17) | ≤ 20 (target ≤ 17) |
| `music_under_speech_lu_gap` | ≥ 18 (target 22) | ≥ 18 (target 22) |
| `has_hook_prequestion` | true | true |
| `checkpoint_questions` | ≥ segments − 1 | ≥ 2 |
| `has_retrieval_recap` | true | true |
| `min_text_contrast` | ≥ 4.5 | ≥ 4.5 |
| `highlights_per_key_term` | = 1 | = 1 |
| `voice_differs_from_previous_video_in_course` | true | true |
| `music_track_not_in_last_5` | true | true |

Two further metrics apply to both formats:
- `concurrent_highlights_max` ≤ 2.
- `board_items_visible_max` ≤ 3 in 16:9, and ≤ 2 in 9:16 (headline + 1).

---

## Sources

Tags as in §0: V = verified this session, S = secondary summary seen this session, M = prior knowledge, not re-verified.

**Meta-analyses and reviews**
- Noetel, M., Griffith, S., Delaney, O., Harris, N. R., Sanders, T., Parker, P., del Pozo Cruz, B., & Lonsdale, C. (2022). Multimedia design for learning: An overview of reviews with meta-meta-analysis. *Review of Educational Research*, 92(3), 413-454. https://doi.org/10.3102/00346543211052329. Full PDF read; record: https://acuresearchbank.acu.edu.au/item/8zqy4/multimedia-design-for-learning-an-overview-of-reviews-with-meta-meta-analysis (V)
- Mayer, R. E. (2024). The past, present, and future of the cognitive theory of multimedia learning. *Educational Psychology Review*. https://doi.org/10.1007/s10648-023-09842-1 (abstract only, S)
- Mayer, R. E. "The Future of Multimedia Learning": principles table with median d and tests, adapted from *Multimedia Learning* 3rd ed. https://edtechbooks.org/docx/10349 (V)
- Kämpfe, J., Sedlmeier, P., & Renkewitz, F. (2011). The impact of background music on adult listeners: A meta-analysis. *Psychology of Music*, 39(4), 424-448. https://doi.org/10.1177/0305735610376261 (abstract read from PDF, V)
- de la Mora Velasco, E., Chen, Y., Hirumi, A., & Bai, H. (2023). The impact of background music on learners: A systematic review and meta-analysis. *Psychology of Music*, 51(6), 1598-1626. https://journals.sagepub.com/doi/abs/10.1177/03057356231153070 (abstract via search, S)
- Huang, G., Du, Y., & Yang, H. (2025). Facilitating or hindering learning: a meta-analysis of acceleration on video learning. *Frontiers in Psychology*, 16:1427609. https://pmc.ncbi.nlm.nih.gov/articles/PMC12412133/ (V)
- Wong, R. M., & Adesope, O. O. (2021). Meta-analysis of emotional designs in multimedia learning: A replication and extension study. *Educational Psychology Review*, 33(2), 357-385. https://rex.libraries.wsu.edu/esploro/outputs/journalArticle/Meta-Analysis-of-Emotional-Designs-in-Multimedia/99900601054701842 (S)
- Castro-Alonso, J. C., et al. (2021). Effectiveness of multimedia pedagogical agents predicted by diverse theories: A meta-analysis. *Educational Psychology Review*, 33(3), 989-1015. https://research.birmingham.ac.uk/en/publications/effectiveness-of-multimedia-pedagogical-agents-predicted-by-diver/ (S)
- Beege, M., et al. (2023). The instructor presence effect and its moderators in instructional video: A series of meta-analyses. *Educational Research Review*. https://www.sciencedirect.com/science/article/pii/S1747938X2300057X (S)
- Alemdag, E. (2022). Effects of instructor-present videos on learning, cognitive load, motivation, and social presence: A meta-analysis. *Education and Information Technologies*, 27(9). https://eric.ed.gov/?id=EJ1356668 (S)
- Positive instructors meta-analysis (37 studies), *Educational Psychology Review* (2024). https://link.springer.com/10.1007/s10648-024-09859-0 (S)
- Ploetzner, R. (2022). The effectiveness of enhanced interaction features in educational videos: a meta-analysis. *Interactive Learning Environments*. https://www.researchgate.net/publication/363673950 (S)
- Sundararajan, N., & Adesope, O. (2020). Keep it coherent: A meta-analysis of the seductive details effect. *Educational Psychology Review*, 32(3), 707-734. https://doi.org/10.1007/s10648-020-09522-4. Numbers via Noetel 2022 (V)
- Mayer, R. E., Fiorella, L., & Stull, A. (2020). Five ways to increase the effectiveness of instructional video. *ETR&D*, 68(3). https://doi.org/10.1007/s11423-020-09749-6 (S)
- Fiorella, L., & Mayer, R. E. (2018). What works and doesn't work with instructional video. *Computers in Human Behavior*, 89, 465-470. https://doi.org/10.1016/j.chb.2018.07.015 (S)

**Primary studies**
- Guo, P. J., Kim, J., & Rubin, R. (2014). How video production affects student engagement: An empirical study of MOOC videos. *ACM L@S*, 41-50. https://www.cs.rochester.edu/hci/pubs/pdfs/edX-MOOC-video-production-and-engagement_LAS-2014.pdf (full PDF, V)
- Szpunar, K. K., Khan, N. Y., & Schacter, D. L. (2013). Interpolated memory tests reduce mind wandering and improve learning of online lectures. *PNAS*, 110(16), 6313-6317. https://doi.org/10.1073/pnas.1221764110. Full text via https://dash.harvard.edu/server/api/core/bitstreams/f3608457-a87d-4171-8f75-b068482e8233/content (V)
- Kestin, G., & Miller, K. (2022). Harnessing active engagement in educational videos: Enhanced visuals and embedded questions. *Phys. Rev. PER*, 18, 010148. https://journals.aps.org/prper/abstract/10.1103/PhysRevPhysEducRes.18.010148 (V)
- Phan, V. T. (2023). Effects of background music in instructional videos on learners' retention. *Journal of Educators Online*. https://files.eric.ed.gov/fulltext/EJ1393962.pdf (full PDF, V)
- Lehmann, J. A. M., & Seufert, T. (2017). The influence of background music on learning in the light of different theoretical perspectives and the role of working memory capacity. *Frontiers in Psychology*, 8:1902. https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2017.01902/full (V)
- Thompson, W. F., Schellenberg, E. G., & Letnic, A. K. (2012). Fast and loud background music disrupts reading comprehension. *Psychology of Music*, 40(6), 700-708. https://sites.utm.utoronto.ca/sites/sites.utm.utoronto.ca.glenn_website/files/download/ThompsonEtAl2012.pdf (S)
- Perham, N., & Currie, H. (2014). Does listening to preferred music improve reading comprehension performance? *Applied Cognitive Psychology*, 28(2), 279-284. https://figshare.cardiffmet.ac.uk/articles/journal_contribution/Does_listening_to_preferred_music_improve_reading_comprehension_performance_/19539196 (S)
- Gonzalez, M. F., & Aiello, J. R. (2019). More than meets the ear: Investigating how music affects cognitive task performance. *J. Exp. Psychol.: Applied*. https://doi.org/10.1037/xap0000202 (S)
- Moreno, R., & Mayer, R. E. (2000). A coherence effect in multimedia learning: The case for minimizing irrelevant sounds. *J. Educ. Psychol.*, 92(1), 117-125. Described in Phan 2023 (S/M)
- Murphy, D. H., Hoover, K. M., Agadzhanyan, K., Kuehn, J. C., & Castel, A. D. (2021/2022). Learning in double time: The effect of lecture video speed on immediate and delayed comprehension. *Applied Cognitive Psychology*. https://castel.psych.ucla.edu/wp-content/uploads/sites/111/2022/03/Applied-Cognitive-Psychology-2021-Murphy-Learning-in-double-time-The-effect-of-lecture-video-speed-on-immediate-and.pdf (S)
- Lang, A., Zhou, S., Schwartz, N., Bolls, P. D., & Potter, R. F. (2000). The effects of edits on arousal, attention, and memory for television messages. *J. Broadcasting & Electronic Media*, 44(1), 94-109. https://scholars.cityu.edu.hk/en/publications/the-effects-of-edits-on-arousal-attention-and-memory-for-televisi/ (V). LC4MP overview: https://en.wikipedia.org/wiki/LC4MP (S)
- Chiossi, F., Haliburton, L., Ou, C., Butz, A., & Schmidt, A. (2023). Short-form videos degrade our capacity to retain intentions. *CHI 2023*. https://arxiv.org/abs/2302.03714 (V)
- Temporary instructor presence in lecture slides does not enhance online learning (2024). *ETR&D*. https://doi.org/10.1007/s11423-024-10435-0; repository: https://macau.uni-kiel.de/receive/macau_mods_00008606 (S)
- Xu et al. (2025). From recorded to AI-generated instructional videos. *BJET*. https://bera-journals.onlinelibrary.wiley.com/doi/10.1111/bjet.13530 (S)
- Cute computer-synthesized voice hinders learning performance in instructional videos. *Journal of Experimental Education*. https://www.tandfonline.com/doi/full/10.1080/00220973.2024.2446169 (title and secondary summary, S)
- Watanabe, F., et al. (2026). Interaction effects between learner characteristics and dialogue format in TTS dialogue-based lessons. https://arxiv.org/abs/2608.20822 (V)
- Bannor, G. A., & Dissou Arthur, Y. (2025). Harmonizing lecture slide presentations with background music. *Frontiers in Psychology*, 16:1637073. https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2025.1637073/full (V; qualitative, weak)
- Milman, A., & Paz-Baruch, N. (2025). Preferred background music and mathematics performance. *Frontiers in Education*. https://www.frontiersin.org/journals/education/articles/10.3389/feduc.2025.1613039/full (V; not narrated video, weak relevance)

**Standards and platform pages**
- W3C. Understanding SC 1.4.7 Low or No Background Audio (WCAG 2.2). https://www.w3.org/WAI/WCAG22/Understanding/low-or-no-background-audio.html (V)
- Netflix. English (USA) Timed Text Style Guide. https://partnerhelp.netflixstudios.com/hc/en-us/articles/217350977-English-USA-Timed-Text-Style-Guide (V)
- YouTube Help. Upload YouTube Shorts (Shorts up to 3 minutes, square or vertical). https://support.google.com/youtube/answer/12779649 (V)
- EBU Technical Review 2012 (Fuchs), Dialogue Enhancement: non-native listeners need about 3 dB higher dialogue-to-background ratio. https://tech.ebu.ch/files/live/sites/tech/files/shared/techreview/trev_2012-Q2_Dialogue-Enhancement_Fuchs.pdf (S)

**Cited from prior knowledge, not re-verified this session (M).** Verify before quoting:
- Adesope, Trevisan & Sundararajan 2017 (testing effect, *RER*); Yang et al. 2021 (*Psych. Bulletin*).
- Carpenter & Toftness 2017 (prequestions in video, *JARMAC*); Pan & Carpenter 2023 (*Ed. Psych. Review*).
- Loewenstein 1994; Kang et al. 2009; Gruber, Gelman & Ranganath 2014 (*Neuron*).
- Barbieri et al. 2023 (worked examples meta-analysis); Kalyuga 2007 (expertise reversal).
- Lee & Mayer 2018 (second-language subtitles); Yue, Bjork & Bjork 2013 (abridged text).
- Ozcelik et al. 2009 (colour coding); Fisher, Godwin & Seltman 2014 (decorated classrooms).
- Zacks & Swallow 2007; Swallow, Zacks & Abrams 2009 (event segmentation).
- Cutting et al. (shot length); Lillard & Peterson 2011.
- Bradbury 2016; Wilson & Korn 2007; Bunce et al. 2010; Risko et al. 2012.
- Griffiths 1992 (second-language speech rate); Craig & Schroeder 2017/2019.
- Kim et al. 2014 (in-video dropouts); Instagram Reels 3-min limit (2025).
- WCAG 1.2.2, 1.4.1, 1.4.3 and 1.4.11 thresholds.
