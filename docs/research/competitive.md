# Research report: what makes educational videos engaging, and a visual grammar we can generate procedurally

Topic key: `competitive`. Researched 2026-10-09 for Aadhi EduEngine v2 (branch `overhaul/v2`). Read-only research; no repo edits.

## 0. How to read this report

**Evidence grades** (shown as [A], [B] or [C] after claims):
- **[A]** Peer-reviewed study, meta-analysis or large primary dataset. I read the primary PDF for Guo et al. 2014 (S1). For the other [A] items I worked from abstracts, publisher records or reliable summaries, and say so where it matters.
- **[B]** Official vendor or platform documentation, help-centre page, standard or licence text.
- **[C]** Secondary sources: vendor marketing, freelancer or tool blogs, trade press, leaked documents, or my own observation of channels. Treat numbers marked [C] as working estimates to tune with analytics, not facts.

**Limits.** Partway through the session the shared web-search budget (200 searches across all agents) ran out. Later checks used direct fetches of primary pages only. Gaps I could not close are listed in §12. Source IDs (S1…S60) are listed with URLs in §13.

**Repo context, read-only.** Today a scene is a `BoardScene` with `BoardItem` kinds (heading, bullet, paragraph, definition, formula, callouts, misconception, code, table, figure, example_step, takeaway) plus one `SidePanel` (skill_tree, figure, image, chart, graph, model_3d, manim, terminal, quiz, gif). `Layout.mascot_position` defaults to `"left"`. The MP4 renderer takes one screenshot per visual state and crossfades the states in ffmpeg. The BGM is a single `bgm.mp3` at volume 0.06, ducked by sidechain compression. Vendored fonts: Inter, Outfit, JetBrains Mono, and Noto Sans in Devanagari, Kannada, Malayalam, Tamil and Telugu. The template catalogue in §8 is mapped onto these structures.

---

## 1. Executive summary: the 15 findings that should drive the redesign

1. **The always-on mascot has no evidence behind it.** Mayer's *image principle*: learners do not necessarily learn better with the speaker's image on screen [A, S3]. Meta-analyses of pedagogical agents find small effects (about g = 0.20) [A, S13]. A character pays off for motivation and social cues in short bursts (intro, recap, a question). It should not sit behind every frame. This backs the teacher's request.
2. **Keep the 16:9 video at or under 6 minutes, and plan it in segments.** In 6.9 M edX sessions, median engagement time was at most about 6 minutes whatever the video length. Problem-attempt rates after a video fell as length grew: 56%, 48%, 43%, 41% and 31% for the 0–3, 3–6, 6–9, 9–12 and 12–40 minute buckets [A, S1]. A 5-minute target is right.
3. **A brisk narration pace helps.** Speaking rates ran from 48 to 254 wpm (mean 156). Within the same length bucket, engagement rose up to 2x with speaking rate, and the 185–254 wpm bucket engaged most [A, S1]. Target 160–180 wpm for 16:9 and 170–190 wpm for 9:16.
4. **Drawing on screen beats static slides.** Khan-style tablet drawing held students 1.5–2x longer than slides or code, with 40% vs 31% problem attempts [A, S1]. The *dynamic drawing principle* (Fiorella & Mayer 2016): watching a diagram being drawn improves transfer compared with seeing it pre-drawn, most of all for novices [A, S4]. Every diagram should therefore **build on screen**, not appear whole.
5. **Address misconceptions head-on.** Muller's physics-video research found that "clear" explanations left misconceptions in place. A student-teacher dialogue that voiced the misconception first roughly doubled post-test scores (to about 12 of 26) [A, S5]. Build in a **Myth vs Fact** template and an optional second "student" voice.
6. **Questions before and during the video help learning.** Prequestions before video segments improved retention [A, S6], though less in long authentic lectures. Interpolated tests halved mind-wandering in a 21-minute lecture [A, S7]. Open with a hook question and add a predict-then-reveal or quiz beat every 60–120 s.
7. **Captions help comprehension, attention and memory for most viewers** (review of 100+ studies) [A, S8]. Burn them in for 9:16. Offer a soft or burned track for 16:9.
8. **Pleasant, warm colours and friendly shapes give a moderate retention gain.** Meta-analysis: d ≈ 0.39 for retention, with lower perceived difficulty [A, S9]. The colours have to serve the content; decorative colour that does not align with it gives no gain, or a loss [A, S9].
9. **Irrelevant "spice" hurts.** The seductive-details meta-analysis found g = −0.33 [A, S10]. Background music is a classic seductive detail [A, S11], and Brame lists removing music among the "weeding" practices [A/B, S2]. **BGM must be low under speech, instrumental, louder only where nobody is speaking, and B-roll must be relevant.**
10. **Pace with "related edits", not random cuts.** Lang's lab: more same-scene camera changes raised arousal and memory without much extra load. Unrelated cuts do not enter memory, and the effect of too many cuts reverses [A/B, S15]. In practice: change *something* every few seconds within a scene (zoom, highlight, build step), and change the *scene* less often.
11. **Hollywood's average shot length fell from about 12 s (1930) to about 2.5–4 s today** [A, S16]. Short-form editors work at about 2–5 s per shot [C, S49]. These figures set the ceiling for 9:16, not the floor for teaching.
12. **AI video tools converge on one model:** a typed **layout library** (Intro, Speaker, Media, Text, Numbers) plus **themes** (font pair, palette, text animation, transition), with **per-scene overrides**, a **brand kit**, **word-level animated captions**, and automatic **B-roll, zoom and SFX** with a frequency knob [B, S29–S40]. Vyond's **Color Shuffler** (up to 5 brand colours, a new arrangement per click) is the closest existing feature to our colour-rotation need [B, S34]. **None of these tools build lessons from the teacher's source document or run pedagogy checks** (misconceptions, retrieval, worked examples). That is our differentiator.
13. **Hard platform constraint for reels:** since 15 Oct 2024, YouTube treats vertical or square uploads **up to 3 minutes** as Shorts. **Any active Content ID claim on a 1–3 minute Short blocks it globally** [B, S17]. Instagram Reels go up to 3 minutes (Jan 2025) [C, S21]. A **3–4 minute vertical video is not a Short on YouTube.** It becomes a vertical long-form video. We need a "Shorts-safe ≤ 2:58" preset, and every music track must be claim-free.
14. **The first 30 seconds are measured.** YouTube's "Intro" metric is the share of viewers still watching at 30 s, and its help centre treats about 30 s as a typical intro [B, S19]. The leaked MrBeast production guide says the first minute must deliver on the title's promise and adds "re-engagement" beats around minutes 3 and 6 [C, S44].
15. **Variety needs rules.** Pictory, Synthesia and Vyond all use a bounded *theme* that swaps as a unit, so colours never drift freely [B]. The 3Blue1Brown convention keeps variable colours constant [C, S46 and observation]. Our rotation should vary background, accent hue, display face, caption style, transition family, music and voice **between videos and chapters**, and keep **semantic colours and variable colours fixed within a lesson**.

---

## 2. Learning-science constraints

| Principle | What it says | Evidence | Rule it implies for Aadhi |
|---|---|---|---|
| Segmenting / length | Median engagement ≤ about 6 min. Shorter videos are much more engaging. | [A] S1, S2 | 16:9 target 4:30–5:30. Chapters of 45–90 s. |
| Speaking rate | Faster speakers are more engaging (185–254 wpm bucket best). | [A] S1 | TTS rate 160–180 wpm (16:9), 170–190 wpm (9:16). Slow down only around formulas. |
| Personal feel | Informal, eye-contact talking head beat studio lectures. Interspersing a face with slides beat slides alone (46% vs 33% problem attempts). | [A] S1 | Aadhi's intro and recap give the personal moment. The rest uses a conversational script (personalization principle [A] S3). |
| Image principle | A presenter's image on screen does not by itself improve learning. | [A] S3 | Aadhi only in intro and recap, ≤ 10 s each. `mascot_position="hidden"` elsewhere. |
| Pedagogical agents | Small positive effects (g ≈ 0.20). Larger for K-12. | [A] S13 | A brief mascot is worth keeping for school audiences. Do not let it compete with content. |
| Signaling | Cues such as highlights, arrows, colour or contrast changes and keywords improve learning. | [A] S2, S3 | Every reveal or emphasis follows a narration word (already in `sync.py`). Highlight one thing at a time. |
| Coherence / weeding | Remove extraneous words, pictures, sounds and music. Seductive details g = −0.33. | [A] S3, S10, S11, S2 | Every visual needs a rationale (already linted). Music −22 to −26 dB under speech. B-roll must be relevant. |
| Redundancy | Do not put the full narration as on-screen text next to graphics. Short keyword labels are fine. | [A] S3 | On-screen text is ≤ 7 words per label. Full sentences only as captions (captions are a separate, toggleable layer). |
| Temporal/spatial contiguity | Show words and pictures at the same time and close together. | [A] S3 | Labels sit on the diagram with leader lines, not in a separate legend. They appear when the word is spoken. |
| Dynamic drawing | Watching the instructor draw improves transfer (2019 replication: d = .54). Strongest for novices. | [A] S4 | Diagram build-up template (T20). Stroke-draw animations synced to beats. |
| Misconceptions first | Dialogue that voices and refutes misconceptions roughly doubled scores. | [A] S5 | Myth vs Fact (T15). Optional second "student" voice. Hook can state the common wrong belief. |
| Prequestions | Asking before the segment improves retention. The effect is weaker for long, authentic lectures. | [A] S6 | Hook question (T02) answered later. Predict-then-reveal (T16). |
| Interpolated testing | Tests during the lecture halved mind-wandering and improved retention. Later studies are mixed. | [A] S7 | One retrieval moment every 60–120 s (T16 or T37). |
| Captions | Improve comprehension, attention and memory. Verbatim captions are as good as elaborated ones. | [A] S8 | Burned-in, word-timed captions by default in 9:16. Optional in 16:9. |
| Emotional design | Pleasant colours and anthropomorphic shapes give retention d ≈ 0.39 and lower perceived difficulty. Colour must match the content. | [A] S9 | Warm, friendly palettes and rounded shapes. Colour carries meaning (semantic roles), never decoration only. |
| Whiteboard animation | Beat narrated slides on learning and enjoyment (n = 568). A drawing hand raises intrinsic motivation, with no learning difference. | [A/C] S42 | A drawing pen or cursor is optional decoration on T20. |
| Voice | Modern TTS is not worse than a human voice for learning (Craig & Schroeder 2017/2019). Human voice raises persona ratings. | [A] S14 | Rotating neural TTS voices is fine. Choose the most natural voices and keep one voice per video. |
| Pacing (Lang) | Related edits raise arousal and memory without much load. Unrelated cuts are not encoded, and too many cuts cause overload. | [A/B] S15 | Frequent within-scene "visual events". Scene changes every 8–20 s (16:9). Slow down for dense content. |

---

## 3. Platform and format facts (current as of Oct 2026)

| Fact | Value | Grade/source |
|---|---|---|
| YouTube Shorts maximum length | 3:00 for square or vertical uploads on or after 15 Oct 2024. Wider formats are always long-form. | [B] S17 |
| Shorts 1–3 min with any active Content ID claim | Blocked globally, not recommended, not monetised. Shorts under 1 min are unaffected. | [B] S17, S18 |
| Shorts Audio Library songs in 3-min Shorts | Most usable for up to 90 s. YouTube Audio Library royalty-free music is not claimed. | [B] S17 |
| Instagram Reels in-app maximum | 3:00 (Mosseri, Jan 2025). Longer uploads are rolling out to some accounts. Reach beyond 3 min is unverified. | [C] S21 |
| TikTok | Up to 10 min recorded in-app and 60 min uploaded (press coverage). | [C] S17 coverage |
| YouTube "Intro" metric | % still watching at 30 s. A typical intro is about 30 s. "Above typical" means ≥ 50% retained at 30 s. | [B] S19 |
| YouTube upload encoding | MP4 with faststart, H.264 High, closed GOP of half the frame rate, 4:2:0, BT.709. 1080p at 24–30 fps: 8 Mbps. At 48–60 fps: 12 Mbps. AAC-LC 48 kHz, 384 kbps stereo. | [B] S20 |
| 9:16 UI safe area (1080×1920) | Sources disagree. Conservative cross-platform box for critical text: **x 96–888, y 288–1248**. Captions are safest centred at **y ≈ 1100–1240**. Keep the bottom ~25–35% and the right ~15–18% clear of key content. | [C] S22 |
| Subtitle norms | Netflix English: ≤ 42 characters per line, ≤ 2 lines, ≤ 20 cps for adults and 17 cps for children, bottom-heavy line breaks. | [B] S23 |
| Text contrast | WCAG 2.2 AA: 4.5:1 for body text, 3:1 for large text (≥ 24 px, or ≥ 18.5 px bold). AAA: 7:1. | [B] S24 |

**Implication for the 3–4 minute vertical requirement.** A 3:30 vertical video uploads to YouTube as a regular, non-Shorts video. It loses the Shorts feed but has no claim-blocking rule. On Instagram it exceeds the 3-minute in-app reel length for most accounts. Offer two vertical presets:
- **"Reel ≤ 2:58"** is the default and is distribution-safe everywhere.
- **"Vertical lesson 3–4 min"** shows a one-line warning about the platform limits.

---

## 4. Competitive teardown: AI video tools

| Tool | Templates / scene model | Transitions & motion | Captions | Theme / brand rotation | Music & voice | Formats | What we copy |
|---|---|---|---|---|---|---|---|
| **Synthesia** [B, S29] | 100+ **Layouts** typed by role (Intro, Speaker, Media, Text, Numbers, …) with suggestions based on the scene's content. Templates are saved scene sets. ≤ 50 scenes. | Per-scene. One-click layout swap keeps content. | Supported | Brand kit with **several themes; a different theme can be picked per scene**. Built-in themes Ember, Bloom, Dusk, Crimson. | Avatar voices | Landscape, portrait, square filters | Typed template library; content-aware suggestion; per-scene theme override. |
| **HeyGen** [B/C, S30] | 75+ templates. AI scenes matched to script mood. PPT/PDF import. | Scene transitions in the new editor | In-editor captions **from the script** (Apr 2025), with styleable font, size and colour | Brand kit (logos, colours, fonts) plus brand glossary | Voice clone, "Voice Director" for emphasis, Video Agent (Sep 2025) | Multi | Captions from script text rather than ASR (we already have the exact narration). Glossary for pronunciation. |
| **Pictory** [B, S31] | Auto scenes from text or media | **Each style bundles font pair, palette, text animation type and transition design.** Style can be per scene. | Saveable caption styles | Styles Library (Sleek, Bold Edge, Clean, Navy Blue, Indigo Ink). "Bold for headlines, softer for narration". Brand kit blends with style. | Stock music | Multi | **Theme = bundle** (fonts + colours + text motion + transition). Per-scene override. |
| **InVideo AI** [B/C, S32] | Prompt up to 32k characters. Draft with script, stock media (~16 M assets), voiceover, music, text overlays. "Theatre mode" preview. | Auto | Overlays | n/a | Voices, clones, SFX | Multi | Full-draft-first UX ("watch the whole draft once before editing"). |
| **Fliki** [B, S33] | Script to scenes | — | Word-by-word, phrase or full-text display | — | 2,500–5,000+ voices in 80+ languages; emotion on marked voices; ~20 k pre-cleared music tracks (2023); speed and pitch controls; pronunciation editor | Multi | Three caption granularities. Per-voice emotion flags. Pronunciation lexicon (we have `Beat.spoken`). |
| **Vyond / Vyond Go** [B, S34] | Layouts define cast and mode (dialogue, to-camera, avatar plus text). Three art styles (whiteboard, contemporary, business). | Character actions | — | **Color Shuffler: up to 5 brand colours remapped onto scenes and props, a new scheme per click.** Max 5 colours. | Music library | — | A **"Shuffle colours" button** and a deterministic shuffle seed per video. The 5-colour cap. |
| **Canva** [B, S35] | Magic Design suggests layouts from content (up to ~10 media files). Magic Animate picks motion that fits the design. | Page transitions **0.1–2.5 s**; practical advice is 0.5–1 s and 1–2 styles per video [C] | — | Brand kit; Styles tab applies brand fonts and colours to all pages | — | Multi | Auto-choose motion from content. Cap transition length and variety. |
| **Lumen5** [B, S41] | Template is fixed per video. Format switchable 16:9, 1:1 or 9:16 after creation. Per-scene export for story taps. | Kinetic text presets | Text highlight colour for keywords | Brand kit (colours, logo, fonts) | Stock music | Multi | One project, many formats (reflow, not crop). Keyword highlight colour. |
| **VideoScribe** [B/A, S42] | Whiteboard hand-drawn reveal; per-element draw time | Draw-on | — | — | AI script, images, TTS | — | Stroke-draw reveal with an optional pen or hand (motivation effect). |
| **Kapwing** [B/C, S40] | Editor with Smart Cut, now **Auto Cut** (removes silences, filler words, retakes). AI resizer. Safe-zone overlay. | — | Auto subtitles with styles | Brand kit | Clean Audio | Multi | **Silence and filler auto-cut** in our editor. Safe-zone overlay toggle. |
| **Descript** [B/C, S37] | Scenes with layouts (B-roll, titles, multicam). **Text-based editing: delete words in the transcript to cut the video.** Underlord agent. | Auto transitions | Yes | Layout packs | Stock music; Studio Sound | Multi | **Transcript-driven cutting** maps directly onto our beats and word timings. |
| **Opus Clip** [B/C, S38] | Long-to-short clipping with a "virality score". **AI reframe** to 1:1, 16:9, 9:16 with speaker/object tracking. | — | Animated caption presets with emoji highlights | 1–2 brand templates per plan | — | Multi | Auto-reframe imported 16:9 clips to 9:16. A "hook strength" score for our own hook lint. |
| **Submagic** [B/C, S39] | Short-form auto-editor | **Magic Zoom with 4 styles (smooth, crash, expo, linear) at transcript-chosen moments.** B-roll from stock or uploads with a **frequency slider**. SFX and emoji transitions. Smart Trim (dead air, fillers, bad takes). | Dozens of animated templates, keyword highlights, auto-emoji | — | Music, SFX | 9:16 | **Keyword punch-zoom** on emphasised words. **B-roll frequency knob.** SFX on transitions. |
| **CapCut** [C, S36] | Templates with preset transitions and effects. AutoCut stitching. "Long video to shorts". | **Auto Beats** markers on the music, cuts synced to the beat | Auto captions with template styles (Glow, Trending, Multiline) | — | Music library | Multi | **Beat grid**: snap scene and transition boundaries to the nearest music beat (±120 ms) when it does not shift narration. |
| **Visla / Steve.ai / Animaker** [C, S43] | Scene-based editors. Steve.ai: script split into scenes, ≤ 60 scenes and ≤ 20 min. Animaker: template plus characters, 180+ languages claimed. Visla: slides and recordings to narrated lessons. | Per scene | Yes | Brand kits | Avatars, voices, clones | Multi | Confirms the market's "scene list plus inspector" editing model. |

**Common denominators** (what users now expect):
1. A typed layout or template library with content-aware suggestion.
2. A theme defined as a bundle (fonts + palette + text animation + transition), applied whole-video, with per-scene override.
3. Word-level animated captions with keyword highlighting.
4. Automated B-roll, punch-zooms and SFX, each with a density knob.
5. Transcript-based trimming and silence or filler removal.
6. One project rendering to 16:9, 1:1 and 9:16.
7. Safe-zone overlays.
8. Brand kits.

**Gaps competitors leave**, which are our differentiation:
1. Grounding in the teacher's source with citations.
2. Pedagogical structure: misconceptions, retrieval, faded worked examples, prerequisites.
3. Formula, graph, code and 3D primitives.
4. Deterministic preview equal to the export.
5. Variety enforced by rules rather than by the user clicking "shuffle".

---

## 5. Competitive teardown: professional educational channels

All rows are [C] **observational** unless cited. They come from familiarity with the channels, not frame-counted measurements. §12 lists how to measure them properly.

| Channel | Shot / visual-change rhythm | Colour | Typography | Motion style | Signature devices to replicate procedurally |
|---|---|---|---|---|---|
| **Kurzgesagt** | Continuous camera moves through an illustrated world. New visual idea every few seconds. Few hard cuts. Uses match-cuts and pushes. | Highly saturated flat vector art on dark navy or violet space backgrounds, with warm accents and glows. A distinct palette per video. | Little on-screen text. Short bold labels. | Everything moves gently (idle loops). Parallax layers. Camera dolly through scenes. | Slow continuous drift on every scene (scale 1.00→1.04). Parallax layers. Glow on accent objects. Per-video palette. **Original score and dense sound design per video** [C, S45]. About 200 illustrated panels per video [C, S45]. |
| **3Blue1Brown** | Long holds. Transforms instead of cuts. Patient pacing. | Near-black background with a few saturated hues. **Each mathematical object keeps one colour throughout the video.** | Rendered LaTeX. Minimal labels. | Morphing between equation states, objects transforming, smooth camera. | Manim-style formula morphs. A **stable variable-colour map** per lesson. Occasional characters ("pi creatures") voice the learner's questions. Visuals first, explanation shaped around them [C, S46]. |
| **CrashCourse** | Very fast host delivery. Frequent graphic cards. Animated "Thought Bubble" segments by Thought Café. | Bright flat illustration | Bold title cards | Cut-out style animation, comedic beats | Rapid card inserts. A ~1–2 min animated story segment per episode. Fast scripts (~100 k words across 40 × 12-min episodes in CS) [C]. |
| **TED-Ed** | About 3–6 min lessons. **A different animator per lesson, so each video has its own art style and palette** [C, S47]. | Varies per lesson | Minimal text | Visual metaphors throughout | Varies the look **per video** while the format stays the same. A teacher, scriptwriter, animator and voice actor team. |
| **Khan Academy** | No cuts. Continuous drawing on a black canvas. | Black background with colour-coded pen strokes | Handwriting | Draw-on | The **drawing build-up** and colour-coded pens. Supported by S1 and S4. |
| **Veritasium** | Cold open with a question or a counter-intuitive demo, often street interviews exposing wrong intuitions. | Natural footage plus clean graphics | Clean sans labels | Live action, animation, demos | **Misconception-first** hooks (Muller thesis) [A, S5]. |
| **Ali Abdaal** | Talking head with text pop-ups and B-roll. Short-form at ~2–5 s per shot [C, S49]. | Neutral grade; key words in yellow | Handwritten or sentence-case labels; phrase-level captions [C] | Pop-in text, list numbering | Numbered list structure. Keyword highlight colour. Motion design concentrated in the first 30 s [C, S49]. |
| **Physics Girl** | Demos, slow motion, enthusiastic host | Natural | Hand-drawn annotations over footage | Annotation draw-on | **Annotate on top of real footage or images** (arrows and circles drawn over B-roll). |
| **Vox / Johnny Harris** | Archival footage, maps, documents | Paper textures, muted base with a red or yellow accent | Bold sans plus serif headlines | **Highlighter swipes on document text**, map zooms (GEOlayers in After Effects [C, S50]), 2.5D parallax photos, deliberately choppy low-frame-rate graphics [C, S50] | Highlighter swipe. Map zoom with pin. Paper texture background. Stepped 12 fps motion as a stylistic option. |
| **Zack D. Films** (Shorts) | **20–45 s 3D clips** (job postings [B, S48]). Curiosity hook ("Did you know…"). Fast cuts, captions, SFX [C]. | Stylised 3D | Big word-by-word captions | Continuous 3D camera | One payoff per short. Question hook at frame 0. SFX on every cut. |
| **Educational Reels / Shorts accounts** (general pattern) | 1.5–3 s between visual events | High contrast, bold colour blocks | 2–4-word karaoke captions in the centre third | Punch-zooms, pop-ins | **Hook text visible at frame 0.** One idea per screen. Loopable endings. |

**Scene-type taxonomy across these channels:** title or cold open; hook question; big number or stat; definition card; comparison or versus; timeline; process or flow; cycle; diagram build-up; labelled figure; formula derivation; code walkthrough; map; chart build or race; list; myth vs fact; question and answer (predict and reveal); quote; real-world application montage; B-roll interlude; recap; next-step. All of these appear in the catalogue in §8.

---

## 6. Visual grammar rules we can implement procedurally

### 6.1 Pacing ("nothing monotonous", turned into numbers)

There are two kinds of change:
- A **visual event** is a related edit inside a scene: an element entrance, a highlight, a punch-zoom, a build step, a camera drift change or a counter.
- A **scene change** is a new template or a new background.

| Parameter | 16:9 (~5 min) | 9:16 (≤ 3 min / 3–4 min) | Basis |
|---|---|---|---|
| Maximum time with no visual event | **6 s** (median 3–4 s) | **3 s** (median 1.5–2.5 s) | S15 related edits; S16; S49 [A/C] |
| Allowed exceptions | `pause_after` thinking pauses ≤ 4 s; formula holds ≤ 5 s | Thinking pause ≤ 2.5 s | S1, S3 |
| Scene (template) duration | 8–20 s, median ~12 s | 4–12 s, median ~7 s | S15 [A/B] + observation [C] |
| Re-engagement beat (question, stat punch, myth vs fact, quiz, chapter bumper) | Every 60–90 s | Every 30–45 s | S7, S44 [A/C] |
| Distinct templates per video | ≥ 8 (target 10–12) | ≥ 6 (target 7–9) | Variety rule |
| Same template in consecutive scenes | Never (except T27 steps inside one example) | Never | Variety rule |
| Share of scenes using one template | ≤ 25% | ≤ 30% | Variety rule |
| Background tone change | At least every 2 scenes | At least every 2 scenes | Variety rule |
| Total Aadhi screen time | 10–20 s (intro 5–8 s + recap 5–10 s) | 8–14 s | Teacher requirement + S3/S13 |

### 6.2 Motion (easing and durations for 30 fps video)

UI design systems publish motion tokens. Video needs slightly longer, more "expressive" values.
- **Entrance:** cubic-bezier(0, 0, 0.3, 1) (Carbon expressive entrance) [B, S25].
- **Exit:** cubic-bezier(0.4, 0.14, 1, 1) [B, S25].
- **Standard (moves that stay on screen):** cubic-bezier(0.4, 0.14, 0.3, 1) [B, S25].
- **Big hero moves (zooms, wipes):** cubic-bezier(0.05, 0.7, 0.1, 1), the Material 3 "emphasized decelerate" [C, S26].
- **Avoid:** linear motion except for counters, cursors and continuous drifts. Avoid bounce on text (Carbon avoids unnatural, decorative curves [B, S25]). A single mild overshoot of 1.0→1.08→1.0 is allowed only on "pop" accents (icons, "?", stamps).
- Carbon scales duration with distance and size [B, S25]. Its UI tokens are 70, 110, 150, 240, 400 and 700 ms. For video, use:

| Motion | Duration | Stagger |
|---|---|---|
| Micro (highlight on, underline, tick) | 200–300 ms | — |
| Text or element entrance (rise 16–32 px plus fade) | 400–600 ms | 60–120 ms per word or item |
| Large move (panel slide, chart grow, camera punch) | 600–900 ms | 80 ms per bar |
| Scene transition | 400–700 ms (chapter wipe ≤ 800 ms) | — |
| Count-up numbers | 800–1200 ms, ease-out | — |
| Stroke draw (diagram) | 600–1200 ms per stroke group, synced to beats | — |
| Continuous drift (Ken Burns / "alive" camera) | Scale 1.00→1.04–1.06 over the whole shot | — |
| Punch-zoom on keyword | Scale 1.00→1.10 in 250 ms, hold, settle back over 600 ms; ≤ 1 per 8 s | — |

The motion rules are the same in the live player and the MP4. They must stay a pure function of `(scene, t)`, as `schedule.js` already is.

### 6.3 Transitions vocabulary (choose a family per video; see §9.3)

| Transition | Meaning to the viewer | Use |
|---|---|---|
| Hard cut | Same thread continues | Default inside a chapter (≥ 50% of boundaries) |
| Crossfade 300–500 ms | Soft continuation, time passing | Calm personalities, after reflective beats |
| Push or slide (direction = progress) | "Next step" | Process, timeline and list continuations |
| Colour-block wipe | **New chapter**, the palette-rotation moment | Only at chapter bumpers (T05) |
| Zoom-through / match-cut | "Look closer", the same object at more detail | T35, from overview into detail |
| Shared-element morph | The same object persists across scenes | Formula → worked example. Icon in roadmap → chapter bumper. |
| Iris or circle reveal | Spotlight or focus | Hook reveal, answer reveal |
| Whip-pan with motion blur and whoosh | Energy, topic jump | ≤ 2 per video in 16:9, ≤ 4 in 9:16. Never into a formula. |
| Mask or shape reveal (on brand shape) | Brand flourish | Intro and outro only |

Rules:
- Never use the same stylised transition twice in a row.
- Stylised transitions make up ≤ 30% of boundaries in 16:9 and ≤ 45% in 9:16.
- Scene transitions take ≤ 1 s, in line with Canva's practical guidance [C, S35].
- No full-frame luminance flips faster than a 300 ms crossfade, and never more than 3 flashes in any 1 s (WCAG 2.3.1, S24 family).

### 6.4 Typography sizes (logical stages 1920×1080 and 1080×1920)

| Role | 16:9 | 9:16 | Weight | Notes |
|---|---|---|---|---|
| Display (hook, stat, chapter number) | 120–200 px | 110–180 px | 800–900 | Tracking −1% to −2%. Line height 1.0–1.05. |
| Title H1 | 84–96 px | 76–88 px | 700–800 | ≤ 2 lines |
| H2 / card heading | 56–64 px | 56–64 px | 700 | |
| Body / bullet | 40–48 px | 46–54 px | 400–500 | ≤ 7 words per bullet (redundancy). ≤ 45 characters per line. |
| Label on diagram | 32–38 px | 36–42 px | 600 | Minimum 32 px at 1080p |
| Code | 30–34 px | 32–36 px | 400 | 16:9: ≤ 18 lines, ≤ 60 chars. 9:16: ≤ 12 lines, ≤ 32 chars. |
| Captions | 44–52 px, ≤ 42 chars per line, ≤ 2 lines | 60–72 px, 2–4 words per chunk, ≤ 2 lines | 600 (16:9) / 800 (9:16) | ≤ 20 cps [B, S23]. Contrast ≥ 7:1 or a 6–8 px stroke or pill. |

Contrast: body text ≥ 4.5:1 and large or display ≥ 3:1 (WCAG AA [B, S24]). Captions should aim for ≥ 7:1.

### 6.5 Captions: six rotating styles (one per video, constant within the video)

1. **Clean subtitle** (16:9 default): sentence chunks, white 600 weight on a translucent dark pill, bottom centre.
2. **Bold karaoke** (9:16 default): 2–3 words per group (2–4 allowed), UPPERCASE, white with a 7 px dark stroke, active word in `--hl` with a 1.08 scale pop over 120 ms (Hormozi-style conventions [C, S51]; Kapwing template places captions lower-middle [C]).
3. **Highlight box:** active word on an accent rectangle [C, S51].
4. **Marker:** sentence case, with glossary terms underlined by a marker swipe as they are spoken.
5. **Minimal lowercase:** phrase chunks, white, keywords yellow (Ali-style [C, S49]).
6. **Glow outline:** soft outer glow, for dark cinematic themes (CapCut "Glow"-like [C, S36]).

Further rules:
- Captions come from the **script text with TTS word timings**, never from ASR (HeyGen does the same [B, S30]). They are exact.
- Glossary terms (`LexiconEntry`) always take the highlight colour.
- Emojis are off by default (academic tone). They can be enabled per teacher.

### 6.6 Sound design grammar

Evidence: Moreno & Mayer added music or sounds to narrated animations and framed both as auditory load [A, S11]. Brame lists eliminating music under "weeding" [A/B, S2]. A design-based study (de la Mora Velasco & Hirumi) improved videos *with* music and SFX, but I could not retrieve its findings, so its effect is unknown [A, S12]. Creators and tools use SFX on transitions and titles (Ali Abdaal's own course; Submagic) [C, S49, S39].

Synthesis: **sound marks structure and event boundaries; it never plays over the key words.**

| Event | Sound | Level vs narration peak | Placement rule |
|---|---|---|---|
| Scene transition (stylised) | Whoosh 0.3–0.6 s | −12 to −16 dB | Start 100 ms before the cut |
| Element reveal / pop accent | Soft pop or click | −18 to −24 dB | In word gaps (use word timings). ≤ 1 per 2 s (16:9), ≤ 1 per 1 s (9:16). |
| Count-up / stat | Rolling ticks plus a soft impact on landing | −16 dB (impact) | Impact on the final value frame |
| Predict / quiz countdown | Riser or ticking 2–5 s | −20 dB | Stop 150 ms before the reveal |
| Correct reveal | Chime or ding | −14 dB | Exists today ("ticks/ding") |
| Myth stamp / wrong | Muted buzz or thud (not harsh) | −18 dB | |
| Chapter bumper | Whoosh plus musical swell (+4–6 dB) | — | Music returns to bed level before narration resumes |
| Code typing | Very soft key ticks | −28 dB | Only while lines type with no narration over them |

**Music bed:** instrumental only, with no lyrics. Under speech it sits at **−22 to −26 dB below narration** (today's 0.06 linear ≈ −24 dB is in range). With no speech (intro, bumpers, outro, countdowns) it rises to −10 to −14 dB. It drops further, or pauses, during formula derivations.

**Master:** about −14 LUFS integrated, true peak ≤ −1 dBTP, a common streaming target. It is grade [C] here: I did not verify it against platform docs this session.

All music must be **claim-free** because of the 1–3 min Shorts blocking rule [B, S17]. Note the Pixabay licence: free commercial use is allowed only as part of a new creative work. Shipping raw files as a "standalone" library is prohibited, so serve SFX only as mixed audio inside renders and never as downloadable files [B, S56].

### 6.7 Hook taxonomy (rotate; one per video)

1. **Question:** "Why doesn't the Moon fall down?" (prequestion effect [A, S6]).
2. **Misconception:** "Most people think heavier things fall faster. They don't." [A, S5]
3. **Surprising number:** "Your body makes 3.8 million cells every second." Must be grounded in the source.
4. **Stakes or application:** "This one equation keeps bridges standing."
5. **Visual mystery:** a cropped or blurred image revealed later (zoom-out reveal).
6. **Challenge:** "Can you solve this before I do? 10 seconds."
7. **Story:** a one-sentence scenario.

Timing:
- **9:16:** hook text is legible on **frame 0**, with no fade-from-black, and the spoken hook ends by 3 s.
- **16:9:** hook done by 15 s. Promise and roadmap by 30 s (YouTube Intro metric [B, S19]).

---

## 7. Format blueprints

### 7.1 Horizontal 16:9, ~5:00 (target 4:30–5:30, about 750–900 words at 160–180 wpm)

| Segment | Time | Templates |
|---|---|---|
| Aadhi cold open (greets, says the hook) | 0:00–0:07 | T01 (+ T02 overlay) |
| Hook / question | 0:07–0:18 | T02 or T15 or T10 |
| Title + roadmap | 0:18–0:30 | T03 → T04 |
| Chapter 1 (bumper + 4–6 scenes) | ~0:30–1:45 | T05 + mixed |
| Re-engagement (predict / quiz) | ~1:45 | T16 |
| Chapter 2 | ~1:50–3:00 | T05 + mixed |
| Quiz checkpoint | ~3:00 | T37 |
| Chapter 3 | ~3:20–4:20 | T05 + mixed |
| Aadhi recap: retrieval prompt first, then review | ~4:20–4:30 | T06 |
| Recap grid + takeaway | ~4:30–4:50 | T39 / T38 |
| Outro / next step | ~4:50–5:00 | T07 |

### 7.2 Vertical 9:16 reel (default ≤ 2:58; "long" 3:00–4:00), about 480–700 words at 170–190 wpm

| Segment | Time | Notes |
|---|---|---|
| Aadhi + hook at frame 0 | 0:00–0:05 | Aadhi in the top ~50%. Hook text in the safe box below. |
| Promise | 0:05–0:12 | One line: "In 3 minutes you'll be able to…" |
| 2–3 chapters × 40–60 s | | One idea per screen, carousel style. No accumulating boards. Bumpers 1.5–2 s. |
| Predict / quiz | every 30–45 s | T16 short form (3 s countdown) |
| Aadhi recap | last 10–15 s | 5–8 s Aadhi + 5 s recap stack |
| End | last 1–2 s | Loop-friendly: the last frame echoes the hook question, now answered |

**Reflow, not crop.** 9:16 is a separate *cut* of the same lesson: fewer scenes and tighter narration, generated from the same screenplay plan with a vertical layout variant. Lumen5 and Opus Clip both treat format as a first-class switch [B, S41, S38]. The existing mascot clips are 1280×720. A centre crop to 9:16 gives roughly 405×720, which is too soft for the top half of 1080×1920. Budget a **dedicated vertical Aadhi intro/recap clip** (≥ 1080 px wide) or a transparent mascot cut-out.

---

## 8. Scene-template catalogue (40 templates)

**Notation**
- Times are seconds from scene start.
- E = entrance easing cubic-bezier(0,0,0.3,1). X = exit easing.
- Theme tokens: `--bg --bg-alt --surface --ink --ink-2 --accent --accent-2 --hl --good --bad`.
- "Maps" names the existing screenplay structure each template renders. "New data" names fields the planner must add.

### A. Bookends and structure

**T01 · Aadhi Cold Open (session intro).**
- *Purpose:* personal greeting and topic promise. The only place Aadhi appears besides T06.
- *Use:* first scene, always.
- *16:9:* Aadhi clip at left 40% over the theme gradient (not the old full-frame mascot background). Lesson title and topic chip at right.
- *9:16:* Aadhi in the top half (y 0–960). Hook text card in the safe box (y 1000–1240).
- *Choreography:* 0.0 the gradient is already drifting (no black frame). 0.15 Aadhi scales 0.92→1.0 (500 ms E). 0.5 title words stagger in (80 ms each, rise 24 px, 500 ms E). 1.3 underline draws (300 ms). Aadhi speaks a greeting and the hook (≤ 20 words). Exit 6.5 s: Aadhi slides out (400 ms X) as T02 builds or crossfades.
- *Duration:* 5–8 s (16:9), 4–6 s (9:16).
- *Sound:* music at intro level, ducking at the first word.
- *Maps:* `BoardScene(type="title")` with `mascot_position="center|left"`.

**T02 · Hook Question.**
- *Purpose:* curiosity gap and prequestion.
- *Use:* scene 2, or overlaid on T01 in 9:16.
- *16:9:* one question centred, 96–140 px, keyword in `--hl` marker. Optional blurred "mystery" image behind at 30% opacity.
- *9:16:* question in the upper-middle safe box, ≤ 9 words.
- *Choreography:* words appear in sync with the narration word timings (120 ms each, rise 16 px). Keyword marker swipe (300 ms) when spoken. "?" pops (scale 0→1.08→1, 240 ms) with a soft pop SFX. Hold 1 s after the last word.
- *Duration:* 4–8 s.
- *New data:* `hook.kind`, `hook.text`, `hook.answer_scene_id` (the hook gets answered later).

**T03 · Title Card.**
- *Purpose:* names the lesson. Once per video.
- *16:9:* title 96 px left-aligned with subject icon, 2–3 learning-objective chips below.
- *9:16:* centred title and 2 chips.
- *Choreography:* title mask-reveals from the baseline (word stagger 60 ms, 500 ms E). Chips pop 100 ms apart. Whole card drifts scale 1.0→1.02.
- *Duration:* 3–5 s.
- *Maps:* `type="title"`, `LearningObjective`.

**T04 · Roadmap.**
- *Purpose:* advance organiser of 3–5 chapters (pre-training principle [A, S3]).
- *16:9:* horizontal metro line with nodes and labels.
- *9:16:* vertical line.
- *Choreography:* line draws (800 ms standard). Nodes pop with 100 ms stagger. Labels fade 200 ms after their node. Current node pulses once.
- *Duration:* 5–9 s.
- *Reuse:* a mini version appears in every T05 (continuity).
- *Maps:* chapters / `ChapterRef`.

**T05 · Chapter Bumper.**
- *Purpose:* boundary marker. **The palette-rotation moment.**
- *16:9:* full-bleed colour-block wipe in the new chapter accent. Outline numeral "02" (300–400 px) at left, chapter title right, mini roadmap bottom.
- *9:16:* numeral at top third, title centre.
- *Choreography:* wipe 450 ms (emphasized). Numeral slides 40 px (500 ms E), title follows 120 ms later. Roadmap node lights.
- *Duration:* 2.5–4 s (16:9), 1.5–2 s (9:16).
- *Sound:* whoosh plus musical swell.
- *Maps:* `ChapterCardScene`.

**T06 · Aadhi Recap Intro.**
- *Purpose:* social cue for consolidation, retrieval before review.
- *Layout:* Aadhi at one side (16:9) or the top half (9:16). Thought bubble with 3 empty slots and the prompt "Name the 3 big ideas".
- *Choreography:* Aadhi enters (500 ms E). Bubble pops. 3 s countdown ring with a soft riser. Then T39 shows the answers.
- *Duration:* 5–10 s.
- *Maps:* `type="recap"` with mascot.

**T07 · Outro / Next Step.**
- *Purpose:* closure plus where to go next.
- *Layout:* "Next up: …" card, one-line takeaway, course or brand mark.
- *Choreography:* card rises (500 ms). Music ends on a phrase (fade 1.5 s).
- *Duration:* 4–6 s.
- *Maps:* `type="summary"` / new `outro`.

### B. Core explanation

**T08 · Definition Card.**
- *Purpose:* formal term plus plain meaning. Also a "light interlude" in dark themes.
- *16:9:* paper-surface card at 60% width. Term in display face 96–120 px. Optional part-of-speech / pronunciation line. Meaning at 44 px. "e.g." line in `--ink-2`.
- *9:16:* full-width card, term 88 px.
- *Choreography:* card rises 24 px and fades (400 ms E). Term mask-reveals. Meaning words follow narration. Key word underlines when spoken.
- *Duration:* 6–12 s.
- *Maps:* `BoardItemKind.definition`.

**T09 · Kinetic Statement.**
- *Purpose:* one principle or key sentence as typography. This is "the caption as the visual".
- *Layout:* ≤ 12 words, 100–160 px (16:9) or 90–130 px (9:16), 2–4 lines, left-aligned or centred.
- *Choreography:* words land as spoken (rise plus fade, 120 ms). The keyword turns `--accent` and scales 1.15. On a long sentence, completed lines dim to 70%.
- *Duration:* 3–6 s.
- *Maps:* `paragraph`, `takeaway`, `heading`.

**T10 · Big Number / Stat Punch.**
- *Purpose:* numeric fact made memorable.
- *16:9:* number 180–240 px in the left 45%, unit beside it, one-line context right, source tag 24 px bottom-right.
- *9:16:* number centred at 35% height, context below.
- *Choreography:* count-up 0→N (1000 ms ease-out; integer frames). Impact SFX on the final frame. Context fades in at 300 ms.
- *Duration:* 4–7 s.
- *Rule:* the number must come from the source (`source_refs` required).
- *New data:* `stat {value, unit, context, source_ref}`.

**T11 · List Build.**
- *Purpose:* 3–5 parallel points.
- *16:9:* numbered list in the left 55% with an icon per item in the right panel. Earlier items dim to 60% and the current item gets a 6 px accent bar.
- *9:16:* **one item per screen** (carousel) with a "2/4" counter and swipe-push transition.
- *Choreography:* item slides 24 px from the left (450 ms E) when its beat starts. 80 ms stagger for number then text.
- *Duration:* 3–5 s per item.
- *Maps:* `bullet` sequences.

**T12 · Analogy Split.**
- *Purpose:* map a familiar system onto the concept (water pipe ↔ circuit).
- *16:9:* familiar image left, concept diagram right, "≈" badge in the middle.
- *9:16:* familiar on top, concept below.
- *Choreography:* familiar appears first (500 ms). Hold 1 s. Concept appears. Mapping lines draw one per beat between corresponding parts (400 ms each) with matching colours.
- *Duration:* 8–15 s.
- *New data:* `analogy {source, target, pairs[]}`.

**T13 · Versus / Comparison.**
- *Purpose:* contrast two things.
- *16:9:* two columns tinted `--accent` and `--accent-2`. Headers with icons. "VS" badge pops in the centre. 2–4 attribute rows.
- *9:16:* top/bottom halves, or two swipe cards plus a verdict card.
- *Choreography:* headers slide in from opposite sides (500 ms E). Rows reveal in pairs per beat. Final verdict row highlights.
- *Duration:* 8–16 s.
- *Maps:* 2-column `table`, or new `comparison`.

**T14 · Table Reveal.**
- *Purpose:* structured multi-attribute data.
- *16:9:* header row sticky. Rows reveal per beat. Active row in `--hl` at 25% alpha. A named column glows (existing `emphasis` cue).
- *9:16:* ≤ 3 columns, or transpose into stacked cards (one row per card).
- *Choreography:* row fades and slides 12 px (350 ms) with a 60 ms cell stagger.
- *Duration:* 10–20 s.
- *Maps:* `table`.

**T15 · Myth vs Fact.**
- *Purpose:* surface and refute a misconception [A, S5].
- *Layout:* statement card with a "MYTH" stamp in `--bad`. Next beat: a strike-through draws across it (300 ms), it fades to 50%, and a "FACT" card in `--good` slides in with the correction and its justification.
- *Optional:* the myth is spoken by a **second "student" voice** and the fact by the main voice.
- *Duration:* 8–14 s.
- *Sound:* soft buzz on the myth stamp, chime on the fact.
- *Maps:* `misconception` (`text` + `justification`), `Misconception`.
- *Rule:* at least once per lesson when the plan has misconceptions.

**T16 · Predict & Reveal.**
- *Purpose:* low-stakes prequestion or retrieval inside the flow.
- *Layout:* question plus 2–4 option chips, or an open prompt. Countdown ring (3 s in 9:16, 4–5 s in 16:9) with "Pause and think" in `--ink-2`.
- *Choreography:* riser during the countdown. At reveal the correct chip scales 1.06 with a `--good` outline, the others dim to 40%, and a one-sentence "why" appears.
- *Duration:* 10–20 s.
- *Maps:* `QuizTeaserSpec` / `quiz` panel.

**T17 · Quote Card.**
- *Purpose:* authoritative or memorable wording **taken from the source only** (no invented quotes).
- *Layout:* serif display face, large quotation marks in `--accent` at 20% alpha, ≤ 25 words, attribution 32 px.
- *Choreography:* quote marks scale in, then words fade in by phrase.
- *Duration:* 5–8 s.
- *Frequency:* ≤ 1 per video.
- *New data:* `quote {text, attribution, source_ref}`.

**T18 · Spotlight Image.**
- *Purpose:* make a photo or illustration teach.
- *Layout:* full-bleed image with a Ken Burns push (1.00→1.06). 1–3 numbered pins with leader lines and labels, plus a vignette.
- *9:16:* crop focus to the subject, labels in a lower card.
- *Choreography:* pins drop (300 ms, slight overshoot) when the part is named. Leader lines draw 250 ms. Labels fade in.
- *Duration:* 6–12 s.
- *Maps:* `image` / `figure` panel, `KenBurns`.
- *New data:* `annotations[{x,y,label,beat_id}]`.

**T19 · Annotated Figure (Label the Diagram).**
- *Purpose:* name the parts of a structure.
- *Layout:* figure centred at 60% width. Labels on both sides with leader lines (spatial contiguity).
- *Quiz variant:* labels appear as "?" first, then resolve.
- *Choreography:* one label per beat (line 250 ms, then text 300 ms).
- *Duration:* 8–16 s.
- *Maps:* `figure` + annotations.

**T20 · Diagram Build-up (draw-on).**
- *Purpose:* the dynamic drawing principle [A, S4].
- *Layout:* vector diagram on a clean board (`--surface` or blueprint grid). Optional pen-tip cursor or drawing hand [A, S42].
- *Choreography:* stroke groups draw with stroke-dashoffset (600–1200 ms each) when their beat starts. Fills fade in 200 ms after a stroke closes. Labels follow.
- *Duration:* 10–25 s.
- *Source:* Manim, generated SVG, or traced figure.
- *New data:* `diagram {svg_ref, groups[{id, beat_id}]}`.

**T21 · Process Flow.**
- *Purpose:* ordered steps or mechanism.
- *16:9:* 3–6 rounded nodes left→right, wrapping to 2 rows past 4.
- *9:16:* a vertical column.
- *Choreography:* node pops (300 ms). An arrow draws to the next node (400 ms). A token dot travels along the arrow (600 ms) as the narration says "then". The current node gets `--accent` and earlier nodes `--ink-2`.
- *Duration:* 10–25 s.
- *New data:* `flow {nodes[], edges[], beat map}`.

**T22 · Cycle.**
- *Purpose:* cyclic processes (water cycle, cell cycle).
- *Layout:* nodes on a circle (r ≈ 300 px in 16:9, 380 px in 9:16) with the centre label.
- *Choreography:* arcs draw clockwise per beat. A highlight orbits once the cycle completes.
- *Duration:* 10–20 s.
- *New data:* `cycle {nodes[]}`.

**T23 · Timeline.**
- *Purpose:* chronology.
- *16:9:* horizontal axis with events above and below, alternating. The camera pans along long timelines with 2-layer parallax.
- *9:16:* vertical axis that scrolls up.
- *Choreography:* axis draws (800 ms). Each event's dot pops and its card rises (400 ms) at its beat. The year label counts or flips.
- *Duration:* 10–25 s.
- *New data:* `timeline {events[{date,label,beat_id}]}`.

**T24 · Concept Map / Tree.**
- *Purpose:* how ideas relate. Prerequisites, taxonomy.
- *Layout:* central node with branches (radial in 16:9, top-down tree in 9:16).
- *Choreography:* branches grow (500 ms E) and nodes pop with 80 ms stagger. The relevant node glows when named.
- *Duration:* 8–15 s.
- *Maps:* `skill_tree`, `ConceptNode`.

**T25 · Cause → Effect Chain.**
- *Purpose:* reasoning chains ("so", "therefore").
- *Layout:* 3–5 cards in a row or column connected by labelled arrows.
- *Choreography:* each card pushes the next ("domino": translate 16 px plus rotate 2°, 300 ms). The arrow label appears on "so" or "because".
- *Duration:* 8–15 s.
- *New data:* `chain[]`.

**T26 · Formula Breakdown.**
- *Purpose:* equation meaning, the 3Blue1Brown style.
- *Layout:* formula centred (MathJax). **Each symbol has one lesson-wide colour** (from the variable palette). Braces or labels under terms. Legend rows (existing `var` sync cue). Final result boxed.
- *Choreography:* formula fades in whole (16:9) or term by term. Each variable tints when named. A brace draws (300 ms). **No camera drift or punch-zoom**, and hold ≥ 3 s after the full formula (coherence).
- *Duration:* 10–25 s.
- *Maps:* `formula` + `FormulaVariable`.

**T27 · Worked Example Steps.**
- *Purpose:* worked examples with fading (blanks).
- *16:9:* step list left, scratch area right. A given value "flies" into the formula slot (shared-element move, 600 ms standard).
- *9:16:* one step per screen with the previous step miniaturised at the top.
- *Choreography:* step slides in (400 ms). A blank fills with a typewriter effect when its beat fills it. The final answer gets a `--good` outline.
- *Duration:* 15–40 s (may span 2 scenes).
- *Maps:* `example_step` (`blank`, `fill_item_id`).

**T28 · Code Walkthrough.**
- *Purpose:* program reading and execution.
- *Layout:* editor frame (dark, Prism), line-number gutter, active-line highlight bar, terminal panel sliding up.
- *9:16:* ≤ 12 lines, ≤ 32 chars, 34 px.
- *Choreography:* lines type at 30–40 chars/s, or appear per line when narrated. The focused line gets a 1.12× punch. Terminal output starts on "prints/returns" (existing `output` cue).
- *Duration:* 10–30 s.
- *Maps:* `code`, `terminal`.

**T29 · Chart Build (and Chart Race).**
- *Purpose:* quantitative comparison or trend.
- *Layout:* bars or lines in Okabe-Ito colours, **direct labels instead of a legend**, gridlines at 10% alpha.
- *Choreography:* axes fade (300 ms). Bars grow from 0 (600 ms ease-out, 80 ms stagger). Values count up. The named bar gets a `--hl` outline. Race variant: ranks animate over time steps (needs a time series).
- *Duration:* 8–20 s.
- *Maps:* `chart` (`ChartSpec`). The MP4 needs per-frame capture for growth (see §11.3).

**T30 · Function Plot.**
- *Purpose:* mathematical relationships.
- *Layout:* axes centred. The curve traces left→right (1–2 s). A moving point shows a coordinate readout. Parameter sweep: a slider moves and the curve morphs.
- *Choreography:* axes draw (500 ms), then the curve, then the point.
- *Duration:* 10–25 s.
- *Maps:* `graph` (`GraphSpec`).

**T31 · Map / Location.**
- *Purpose:* geography and history.
- *Layout:* vector map (offline, e.g. Natural Earth, which I believe is public domain; verify). Zoom from globe or continent to region (1.5–2.5 s, emphasized). Pin drops with a ripple. Route lines draw.
- *Duration:* 6–15 s.
- *New data:* `map {region, pins[], routes[]}`.

**T32 · 3D Object Spin.**
- *Purpose:* spatial structure.
- *Layout:* full-bleed 3D model with a slow orbit (15–25°/s). A part highlights with a label when named.
- *Duration:* 6–15 s.
- *Maps:* `model_3d`.

**T33 · B-roll Full-bleed.**
- *Purpose:* real-world grounding and visual rest.
- *Layout:* video or image full-bleed with a slow push and a lower-third keyword label (≤ 4 words).
- *Rules:* ≤ 2 consecutive clips, 3–8 s each, **relevance required** (seductive details [A, S10]). Teacher-imported clips use the same template, with trim in/out and fit = cover.
- *Maps:* `ai_video`, library video, upload.

**T34 · Media + Text Split.**
- *Purpose:* the classic explainer frame (today's board plus side panel, restyled).
- *16:9:* media at 55–60% width with a 24 px radius and soft shadow. 2–3 bullets beside it. **The media side alternates left and right on consecutive uses.**
- *9:16:* media top 50%, text below.
- *Choreography:* media scales 0.96→1 (500 ms E), bullets per beat.
- *Duration:* 8–20 s.
- *Maps:* `BoardScene` + `SidePanel`.

**T35 · Zoom into Detail ("powers of ten").**
- *Purpose:* macro to micro (organ → cell → organelle).
- *Layout:* wide view, then a scale-in on a region (1→4×, 1.5–2 s, emphasized curve), then a match-cut into a detailed diagram.
- *Duration:* 6–12 s.
- *New data:* `zoom {from_ref, region, to_ref}`.

**T36 · Checklist / How-to.**
- *Purpose:* procedures (lab steps, algorithm usage).
- *Layout:* checkbox rows. A check mark draws (250 ms) with a soft click SFX when each step is narrated as done.
- *Duration:* 8–20 s.
- *Maps:* `bullet` with procedural intent.

### C. Practice and consolidation

**T37 · Quiz Checkpoint.**
- *Purpose:* retrieval practice [A, S7].
- *Layout:* question, 3–4 lettered options, 5 s countdown ring in the MP4 (interactive in live).
- *Choreography:* options stagger 100 ms. Countdown riser. Reveal: the correct option turns `--good` with a check pop and the others dim. A one-line explanation follows.
- *Duration:* 15–25 s.
- *Frequency:* every 2–2.5 min in 16:9, or 1 per reel.
- *Maps:* `QuizScene`.

**T38 · Key Takeaway Stamp.**
- *Purpose:* end-of-chapter consolidation.
- *Layout:* sticky-note or stamp card rotated −2° on a paper texture, "REMEMBER" label, ≤ 15 words.
- *Choreography:* card drops (scale 1.1→1, 300 ms) with a soft thud.
- *Duration:* 4–7 s.
- *Maps:* `takeaway`, `type="key_takeaway"`.

**T39 · Recap Grid.**
- *Purpose:* review that reuses each chapter's own visual as a retrieval cue.
- *16:9:* 2×3 tiles, each a miniature of the chapter's hero visual in the chapter's accent colour.
- *9:16:* vertical stack of 3 tiles.
- *Choreography:* tiles flip in (400 ms) as each is named. The current tile scales 1.05.
- *Duration:* 10–20 s.
- *Maps:* `type="recap"`.

**T40 · Real-World Application Mosaic.**
- *Purpose:* answers "why care?" (motivation, application).
- *Layout:* 3–4 tiles (photos or illustrations) with independent Ken Burns moves and label chips ("in your phone", "in bridges").
- *Choreography:* tiles reveal with a 150 ms stagger.
- *Duration:* 6–12 s.
- *New data:* `applications[{label, image_prompt|asset}]`.

### 8.1 Template selection rules for the planner (content signal → template)

| Signal in plan or screenplay | Preferred → alternatives |
|---|---|
| `definition` item | T08 → T09 |
| `formula` item | T26 (always). Follow with T27 if there is an example. |
| `example_step` items | T27 |
| `code` / `terminal` | T28 |
| `table`, 2 columns, contrastive headers | T13 |
| `table`, ≥ 3 columns | T14 (9:16: transposed cards) |
| `misconception` | T15 (and as a hook candidate) |
| `takeaway` | T38 (chapter end) or T09 |
| ≥ 3 parallel bullets | T11 |
| Sequence words (first/then/next/finally), or a process concept | T21 |
| Cycle language ("repeats", "cycle", "returns to") | T22 |
| ≥ 3 dates or eras | T23 |
| A number with a unit or percentage that the narration stresses | T10 |
| "like", "imagine", "think of it as" | T12 |
| "vs", "unlike", "compared with", "difference between" | T13 |
| Place names / geography | T31 |
| `chart` / `graph` / `model_3d` panels | T29 / T30 / T32 |
| `figure` with nameable parts | T19 (static) or T20 (build-up) |
| Structure within structure | T35 |
| Procedure / lab | T36 |
| Prerequisites or overview | T24 / T04 |
| Every 60–90 s without a question | Insert T16 or T37 |

---

## 9. Palette, typography and motion-personality rotation ("variety without chaos")

### 9.1 Principles

- **Vary between videos and between chapters. Stay consistent inside a chapter.** TED-Ed varies art style per lesson [C, S47]. Pictory, Synthesia and Vyond bound variety inside themes [B]. Kurzgesagt uses a distinct palette per video while the format stays fixed [C].
- **Never rotate meaning-bearing colours.** Semantic roles (`--good`, `--bad`, `--warn`, `--hl`) and the **variable-colour map** (each formula symbol keeps one colour for the whole lesson; 3B1B convention) stay fixed within a lesson. Signaling relies on that consistency [A, S2/S3].
- **At most 5 theme colours, plus neutrals.** This mirrors Vyond's 5-colour shuffler cap [B, S34]. At most 3 hue families on screen at once.
- **Deterministic randomness.** The seed is `hash(project_id, version_number, format)`, so the preview, the MP4 and a re-render are identical (preview-equals-export is a core principle of the app). A "Shuffle look" button bumps a `look_seed` stored in the screenplay.

### 9.2 Palette generation in OKLCH

OKLCH is perceptually uniform. Holding L and C while sweeping H gives evenly weighted palettes, but chroma must be gamut-clamped to sRGB [B, S28]. Browser support: Chrome/Edge 111, Firefox 113, Safari 15.4 [B, S28]. The renderer's Chromium already supports it. Convert to hex in Python for ffmpeg-drawn elements.

Token recipe per theme:

| Token | Dark mode | Light mode |
|---|---|---|
| `--bg` | L 0.17–0.24, C 0.02–0.06, H = family hue | L 0.95–0.98, C 0.01–0.03 |
| `--bg-alt` | `--bg` with L +0.04 | L −0.03 |
| `--bg-tint` (every 3rd scene) | H = chapter accent hue, C 0.05–0.07 | Same hue, C 0.03 |
| `--surface` (cards) | L 0.27–0.32 | L 1.00 / 0.99 |
| `--ink` | L ≥ 0.96 (contrast ≥ 12:1 target) | L ≤ 0.22 |
| `--ink-2` | L 0.78 | L 0.42 |
| `--accent` (chapter k) | L 0.74–0.80, C 0.13–0.19, H = h0 + k·Δ | L 0.52–0.60, C 0.14–0.20 |
| `--accent-2` | Accent H ± 150–180° (complement), same L and C | Same |
| `--hl` | Fixed yellow H ≈ 95–100, L 0.88 (dark) / marker alpha 0.35 (light) | Same |
| `--good` / `--bad` / `--warn` | Okabe-Ito-derived bluish green (#009E73), vermillion (#D55E00), orange (#E69F00), with L adjusted per mode | Same |
| Charts | Okabe-Ito order: #E69F00, #56B4E9, #009E73, #F0E442, #0072B2, #D55E00, #CC79A7 [B/C, S27] | Same |

Δ (chapter hue step) cycles through +35°, +70° and −35° inside the family's allowed hue window, so chapters feel related but different.

**Twelve starter theme families.** Each family has dark and light variants and a recommended motion personality. These are design starting points, to be tuned by eye and checked by the contrast validator.

| # | Family | Mode | bg H | Accent hues (h0, alt) | Personality |
|---|---|---|---|---|---|
| 1 | Midnight Indigo | dark | 275 | 75 amber / 210 cyan | Cinematic |
| 2 | Deep Teal | dark | 200 | 30 coral / 130 lime | Crisp |
| 3 | Aubergine | dark | 320 | 165 mint / 90 gold | Cinematic |
| 4 | Charcoal Math (3B1B-like) | dark, C ≈ 0.01 | 260 | 250 blue / 100 yellow / 350 pink | Chalk-precise |
| 5 | Forest Night | dark | 155 | 55 peach / 230 sky | Soft |
| 6 | Cosmic (Kurzgesagt-like) | dark, C 0.07 | 285 | 50 orange / 190 teal | Cinematic |
| 7 | Paper & Ink (Vox-like) | light, cream | 85 | 25 red / 100 marker | Editorial |
| 8 | Blueprint | light grid | 230 | 255 blue / 55 orange | Crisp |
| 9 | Mint Studio | light | 170 | 300 purple / 30 coral | Soft |
| 10 | Sunrise | light | 60 | 290 violet / 195 teal | Bouncy-soft |
| 11 | Chalkboard | dark green (L 0.28) | 150 | 95 chalk yellow / 350 chalk pink | Chalk |
| 12 | Bold Blocks | colour-block bg for hooks and bumpers only (L 0.55–0.62); body scenes use its dark variant | any | white ink on blocks (≥ 3:1, large text only) | Crisp |

**Rotation algorithm (per video):**
1. Filter the families by the teacher's preference (dark/light/auto) and by audience (younger audiences favour 5, 9, 10, 11, 12).
2. Exclude the **last 4 families used** by the same course or owner. Keep this history from rendered versions.
3. Pick by seeded RNG.
4. For each chapter k, set the accent H to `h0 + Δ_k`.
5. For scenes: background cycles `bg → bg-alt → bg-tint` with no immediate repeat. Definition, quote and takeaway scenes in dark families use a light paper "interlude" surface (luminance shift as a pattern interrupt), at most 1 per chapter.
6. **Validate** every text/background pair: ≥ 4.5:1 body, ≥ 3:1 for large display, target ≥ 7:1 for captions [B, S24]. If a pair fails, nudge L by ±0.02 steps and re-test.
7. Check adjacent scenes: the change in background L should be between 0.03 and 0.35 (noticeable, not jarring), except at chapter bumpers.

### 9.3 Motion personalities (bundles, Pictory-style)

Each personality bundles easing set + transition family + caption style + SFX pack + text animation. One personality per video, rotated with the same exclusion rule.

| Personality | Text entrance | Default transition / stylised | Caption style | SFX pack | Music mood |
|---|---|---|---|---|---|
| Crisp | Mask-reveal from baseline, 450 ms | Cut / push | Bold karaoke or Highlight box | Clean clicks, short whoosh | Upbeat electronic, 100–120 BPM |
| Soft | Fade plus rise 16 px, 600 ms | Crossfade / iris | Clean subtitle | Soft pops, bells | Warm acoustic, 80–100 BPM |
| Cinematic | Blur-to-sharp plus tracking-in, 700 ms | Crossfade / zoom-through, parallax | Glow outline | Deep whoosh, sub hits | Ambient cinematic |
| Editorial | Highlighter swipe, typewriter | Cut / paper slide | Marker | Paper, pen, stamp | Minimal piano or pizzicato |
| Chalk | Stroke draw-on | Cut / wipe | Minimal lowercase | Chalk taps (very soft) | Lo-fi, 70–90 BPM |
| Bouncy-soft (younger learners) | Pop scale with one overshoot | Push / iris | Bold karaoke | Pops, boings (sparing) | Playful marimba or ukulele |

### 9.4 Typography rotation

- **Body face is fixed per teacher setting.** Inter (vendored) by default. Atkinson Hyperlegible Next is a "high legibility" option: SIL OFL 1.1, Braille Institute, released 2025 [B/C, S57]. Readability is not something to vary.
- **Display face rotates per video** from a pool, excluding the last 3 used in the course. Candidates:
  - Outfit (vendored), Poppins, Montserrat, Sora, Space Grotesk, Lexend, Bricolage Grotesque, Baloo 2 (friendly, rounded).
  - Serif display for the Editorial personality: Fraunces or DM Serif Display.
  - All are, to my knowledge, OFL on Google Fonts / Fontsource. **Verify each licence file when vendoring.** I verified only Atkinson Hyperlegible Next and Anek this session.
- **Indic-script lessons** (the app vendors Devanagari, Kannada, Malayalam, Tamil and Telugu): use **Anek** for display. It covers Bangla, Devanagari, Gujarati, Gurmukhi, Kannada, Malayalam, Odia, Tamil, Telugu and Latin, is SIL OFL 1.1, and is variable in weight and width [B, S58]. Use Noto Sans for body. The rotation then varies Anek's **width and weight axes** plus colour, so Indic lessons still get variety without unsupported faces.
- **Mono:** JetBrains Mono (vendored).
- Never use more than 2 families plus mono in one video. All-caps only for ≤ 4-word labels, stats and caption style 2, with +4–6% tracking.

### 9.5 Voice and music rotation (interface with the audio researchers)

- **One narrator voice per video, a different voice across consecutive videos.** Exclude the last 3 voices per course and match language and locale. Voice quality matters more than human vs synthetic (S14).
- **Optional second voice** for the "student" lines in T15 and T16 (Muller dialogue effect [A, S5]).
- **Music:** one track per video (or one per chapter at most, crossfaded at bumpers). Match the mood to the personality. Instrumental and claim-free. Exclude the last 5 tracks per course. Snap bumper transitions to the nearest beat (±120 ms) when this does not move narration (CapCut Auto Beats idea [C, S36]).

---

## 10. Editor lessons from competitors (requirement 8)

| Capability | Who does it | Our implementation idea |
|---|---|---|
| Cut by editing text | Descript [B/C, S37] | Strike words or beats in the narration view to cut the matching audio and visuals. Word timings already exist. Re-synth only the edited beats. |
| Auto-remove silences and fillers | Kapwing Auto Cut, Submagic Smart Trim [B/C] | For TTS this is mostly moot. For **imported external clips**, add a silence detector (ffmpeg `silencedetect`) that proposes trims. |
| Insert external video with trim | All tools | T33 template: in/out trim handles, fit cover/contain, mute/duck its audio, optional caption from its transcript. Auto-reframe 16:9 → 9:16 using a crop focus point (Opus Clip-style [B/C, S38]). |
| B-roll density knob | Submagic slider [B/C, S39] | Per-video "visual energy" slider (Calm / Balanced / Lively). It scales the visual-event interval (6 → 3 s in 16:9), B-roll frequency and stylised-transition share. |
| Per-scene theme override | Synthesia, Pictory [B] | Scene inspector: template picker (suggested first), background variant, accent override, transition in/out. |
| "Shuffle colours" | Vyond [B, S34] | Bump `look_seed`. Shuffle colours only, or the whole look (colours + display face + personality). |
| Safe-zone overlay | Kapwing, Adkit [C] | Toggle a 9:16 overlay of the safe box (x 96–888, y 288–1248) plus UI zones. Lint text outside it. |
| Beat markers | CapCut [C, S36] | Show music beats on the timeline strip. Snap bumpers to them. |
| Scene split, hide, min-hold | Already in batch 4 | Keep. Add "duplicate as other template" (layout swap keeps content, as Synthesia does [B, S29]). |

---

## 11. Engineering notes that follow from the grammar

These are flagged for the architecture or rendering researcher, not decided here.

1. **Continuous motion needs per-frame capture.** Count-ups, stroke draws, Ken Burns, word-timed caption pops and chart growth cannot be expressed as "one screenshot per state plus a crossfade". The scene layer must be rendered per frame (30 fps) where something animates, using the same pure `(scene, t)` functions in player and render (today's parity principle). Static holds can still reuse one frame.
2. **Animation library licences.**
   - GSAP is free, including for commercial use, since 30 Apr 2025. However, its standard licence **prohibits use in tools that let users build visual animations without code in competition with Webflow** [B, S52]. A Studio with a visual scene editor is a grey area. Avoid it.
   - **anime.js v4 is MIT** [B, S54].
   - **Motion Canvas is MIT** [B, S55].
   - **Remotion requires a paid company licence for companies of 4 or more people**: $25 per seat per month, or $0.01 per render with a $100 monthly minimum for automators [B, S53].
   - Lowest-risk option: our own small easing and keyframe evaluator (cubic-bezier plus springs as pure functions), which also guarantees determinism.
3. **Fonts.** Vendor only OFL faces through `scripts/vendor.mjs`, with licence files. Keep the CSP same-origin.
4. **SFX and music assets.** Ship only as mixed render output. Pixabay forbids standalone redistribution [B, S56]. Prefer CC0 or owned sources.

---

## 12. Gaps, uncertainties and how to close them

- **No measured shot-length data for the named educational channels** was found. The channel rows in §5 are observational [C]. To close this: run PySceneDetect over 5 recent videos per channel, with the user's permission to download, and compute median scene length and cuts per minute separately for 16:9 and Shorts. Then tune §6.1.
- **Platform safe zones** come from third parties, with large disagreement on the bottom margin (~290 px spread) [C, S22]. Validate with real uploads.
- **Instagram and TikTok length limits** are secondary-sourced [C, S21].
- **Background-music effect size in instructional video** is unresolved: the 2020 and 2023 reviews by de la Mora Velasco et al. were identified but not read [S12]. The conservative ducking rule in §6.6 stands until then.
- **Lang's "> 10 cuts in 2 minutes" recognition drop-off** appears in secondary coverage; I could not confirm it in the primary paper [S15]. I used the qualitative finding only.
- **Material 3 duration tokens** could not be read from the official page. Carbon values were verified [B, S25].
- **Loudness target** (−14 LUFS) was not verified against platform docs this session [C].
- **Natural Earth** being public domain is from my own knowledge and was not verified this session.
- **Display-font licences** other than Atkinson Hyperlegible Next and Anek were not re-verified this session.
- The **web-search budget ran out** late in the session. Remaining checks were direct fetches of primary pages. A follow-up session could verify the remaining [C] items.

---

## 13. Sources (accessed 2026-10-09 unless noted)

| ID | Source | Grade |
|---|---|---|
| S1 | Guo, Kim, Rubin (2014). How video production affects student engagement. L@S '14. https://up.csail.mit.edu/other-pubs/las2014-pguo-engagement.pdf (DOI 10.1145/2556325.2566239). Primary PDF read. | A |
| S2 | Brame (2016). Effective educational videos. CBE-LSE 15(4):es6. https://pmc.ncbi.nlm.nih.gov/articles/PMC5132380/ ; summary https://teach.ucmerced.edu/sites/crte.ucmerced.edu/files/documents/effective_videos_brame_summary.pdf | A/B |
| S3 | Mayer's multimedia principles (summaries): https://teaching.pitt.edu/wp-content/uploads/2023/03/multimedia-principles-checklist.pdf ; https://u.osu.edu/multimedialearning/?p=11 ; Mayer (2008) Am. Psych. 63(8) | A (via summaries) |
| S4 | Fiorella & Mayer (2016) JEP 108:528–546; dynamic drawing: https://ecrhub.org/publications/fostering-generative-learning-video-lessons-benefits-instructor-generated-drawings-and ; https://escholarship.org/uc/item/55v7s9t1 | A |
| S5 | Muller (2008) thesis and JCAL 24(2):144–155; https://veritasium.com/publications ; https://www.scientificamerican.com/article/how-youtube-star-derek-muller-of-veritasium-is-challenging-scientific/ | A |
| S6 | Carpenter & Toftness (2017) JARMAC 6(1):104–109; https://bps.org.uk/research-digest/heres-simple-way-boost-your-learning-videos-prequestion ; Toftness et al.: https://pure.royalholloway.ac.uk/files/30437641/JARMAC_Toftness_et_al.pdf | A |
| S7 | Szpunar, Khan, Schacter (2013) PNAS. https://pmc.ncbi.nlm.nih.gov/articles/PMC3631699/ ; interview https://medicalresearch.com/interpolated-memory-tests-reduce-mind-wandering-and-improve-learning-of-online-lectures/ | A |
| S8 | Gernsbacher (2015). Video captions benefit everyone. PIBBS 2(1). https://successforkidswithhearingloss.com/wp-content/uploads/2020/04/Video-Captions-Benefit-Everyone_2015.pdf | A |
| S9 | Brom, Stárková, D'Mello (2018). Emotional design meta-analysis. Educ. Res. Rev. 25. https://artemis.ms.mff.cuni.cz/main/papers/emodesign_manuscript_1rev_180928_FINAL_norev.pdf | A |
| S10 | Sundararajan & Adesope (2020) seductive details meta-analysis. https://news.wsu.edu/press-release/2020/03/19/seductive-details-inhibit-learning | A (via press release) |
| S11 | Moreno & Mayer (2000). https://tecfa.unige.ch/tecfa/teaching/methodo/Moreno_Mayer00.pdf | A |
| S12 | de la Mora Velasco & Hirumi (2020) ETR&D 68(6):2817–2837, DOI 10.1007/s11423-020-09783-4; 2023 review: https://journals.sagepub.com/doi/full/10.1177/03057356231153070 (findings not read) | A (unread) |
| S13 | Schroeder, Adesope, Gilbert (2013) JECR 49(1):1–39. https://rex.libraries.wsu.edu/esploro/outputs/journalArticle/How-Effective-are-Pedagogical-Agents-for/99900601056901842 ; Castro-Alonso et al. 2021 (g = 0.20) via https://journals.sagepub.com/doi/ref/10.1177/07356331211041701 | A |
| S14 | Craig & Schroeder (2017) Computers & Education 114:193–205; 2019 follow-up; summary https://www.readspeaker.com/blog/elearning-voice (vendor) | A/C |
| S15 | Lang et al. (1999) JBEM 43(4):451–475; Lang et al. (2000) edits study https://scholars.cityu.edu.hk/en/publications/the-effects-of-edits-on-arousal-attention-and-memory-for-televisi/ ; LC4MP https://en.wikipedia.org/wiki/LC4MP | A/B |
| S16 | Cutting et al., shot-length trends. https://pmc.ncbi.nlm.nih.gov/articles/PMC3485803/ ; https://journals.sagepub.com/doi/abs/10.1068/i0441aap | A |
| S17 | YouTube Help: Understand three-minute YouTube Shorts. https://support.google.com/youtube/answer/15424877 (fetched) | B |
| S18 | YouTube Help: Music eligibility for Shorts. https://support.google.com/youtube/answer/13486873 | B |
| S19 | YouTube Help: Measure key moments for audience retention. https://support.google.com/youtube/answer/9314415 | B |
| S20 | YouTube Help: Recommended upload encoding settings. https://support.google.com/youtube/answer/1722171 (fetched) | B |
| S21 | Instagram Reels 3 min (Jan 2025): https://buffer.com/resources/instagram-reels-length ; https://www.etvbharat.com/en/!technology/instagram-increases-reels-length-to-3-minutes-changing-to-long-form-video-format-enn25012001576 | C |
| S22 | Safe zones: https://www.ignitesocialmedia.com/content-creation/what-are-the-safe-zones-for-tiktoks-and-instagram-reels/ (Jul 2025); https://adkit.so/tools/safe-zones/youtube (Mar 2026); https://www.aicarousels.com/free-tools/youtube-safe-zone-checker ; https://www.kapwing.com/video-editor/youtube-shorts/safe-zone ; TikTok ads help https://ads.tiktok.com/help/ | C |
| S23 | Netflix English (USA) Timed Text Style Guide. https://partnerhelp.netflixstudios.com/hc/en-us/articles/217350977-English-USA-Timed-Text-Style-Guide (fetched) | B |
| S24 | W3C WCAG 2.2 Understanding 1.4.3. https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html (fetched); 2.3.1 three flashes https://www.w3.org/WAI/WCAG22/Understanding/three-flashes-or-below-threshold.html (not re-fetched) | B |
| S25 | IBM Carbon motion. https://carbondesignsystem.com/elements/motion/overview/ (fetched: durations 70/110/150/240/400/700 ms; curves) | B |
| S26 | Material 3 easing (secondary): https://smithery.ai/skills/copyleftdev/google-material-design ; Flutter token generator https://flutter.googlesource.com/mirrors/flutter/+/HEAD/dev/tools/gen_defaults/lib/motion_template.dart | C |
| S27 | Okabe-Ito palette: https://conceptviz.app/blog/okabe-ito-palette-hex-codes-complete-reference ; https://siegal.bio.nyu.edu/color-palette/ (Wong 2011, Nature Methods) | B/C |
| S28 | OKLCH / CSS Color 4: https://developer.chrome.com/docs/css-ui/access-colors-spaces ; https://dev.to/irrationalapps/why-your-generated-color-palettes-look-muddy-and-what-oklch-does-about-it-410c | B |
| S29 | Synthesia: https://www.synthesia.io/updates/build-better-videos-with-layouts (fetched); https://help.synthesia.io/en/articles/9767149-how-do-i-use-my-brand-kit ; https://help.synthesia.io/en/articles/6341519-templates | B |
| S30 | HeyGen: https://www.heygen.com/ai-studio ; changelog https://www.heygen.com/news ; review https://creatify.ai/review/heygen-ai | B/C |
| S31 | Pictory Academy: https://pictory.ai/academy/how-to-use-styles-library-pictory-ai (fetched) | B |
| S32 | InVideo v3: https://www.businesswire.com/news/home/20241114426121/en/ ; https://petapixel.com/2024/11/22/invideo-lets-you-generate-full-length-videos-using-ai-or-stock-footage | B/C |
| S33 | Fliki: https://fliki.ai/voices ; https://fliki.ai/whats-new/premium-music-library | B |
| S34 | Vyond: https://www.vyond.com/product/brand-management/ ; https://product.vyond.com/stay-on-brand-with-custom-color-palettes/ ; https://www.vyond.com/blog/prompting-ai-getting-the-best-results-from-vyond-go/ | B |
| S35 | Canva: https://www.canva.com/help/use-magic-design/ ; https://www.canva.com/help/page-transitions/ ; https://canva.com/pro/animator/ | B |
| S36 | CapCut: https://www.capcut.com/resource/caption-template ; https://www.capcut.com/resource/capcut-autocut ; https://apps.apple.com/qa/ipad/story/id1705686145 | C |
| S37 | Descript: https://descript.com/video ; https://descript.com/underlord | B/C |
| S38 | Opus Clip: https://www.opus.pro/home-a-b ; pricing https://www.opus.pro/es-es/pricing | B/C |
| S39 | Submagic: https://submagic.co/features/auto-zooms ; https://www.submagic.co/de/features/auto-video-editor | B/C |
| S40 | Kapwing: https://kapwing.com/resources/kapwing-direct ; https://www.kapwing.com/tools/audio-editor/silence-remover | B/C |
| S41 | Lumen5 help: https://help.lumen5.com/en/article/how-do-i-select-a-template-start-creating-a-video-in-lumen5-11aod0v/ ; https://help.lumen5.com/en/category/styling-controls-jagmkr/ | B |
| S42 | VideoScribe and whiteboard research: https://www.videoscribe.co/en/how-to-create-a-whiteboard-video/ ; Turkay (2016) https://www.vpal.harvard.edu/publications/educational-impact-whiteboard-animations-experiment-using-popular-social-science ; AJET https://ajet.org.au/index.php/AJET/article/view/7639 ; 2023 drawing-hand study https://doaj.org/article/31f9d965c811475aba2d03192335846b | A/C |
| S43 | Visla https://www.visla.us/education ; Steve.ai/Animaker https://www.capterra.ca/compare/1031225/1059333/steve-ai/vs/visla ; https://www.animaker.com | C |
| S44 | MrBeast production guide (leaked 2024): https://werd.io/how-to-succeed-in-mrbeast-production-leaked-pdf/ ; https://www.sherwood.news/culture/mrbeast-youtube-leaked-internal-success-document | C |
| S45 | Kurzgesagt process: https://www.toolfarm.com/?p=77543 ; https://www.gigazine.net/gsc_news/en/20200217-science-animation-making/ | C |
| S46 | 3Blue1Brown FAQ: https://www.3blue1brown.com/faq/ ; Aperiodical interview https://aperiodical.com/2023/09/eipi-to-watch-3blue1brown/ | B/C |
| S47 | TED-Ed: https://www.ofcom.org.uk/siteassets/resources/documents/tv-radio-and-on-demand/broadcast-guidance/psb/psb3/10._ted-ed.pdf (2014); https://www.kqed.org/education/474416/the-student-produced-ted-ed-lesson-a-lesson-worth-sharing | C |
| S48 | Zack D. Films recruiting: https://zackdfilms.recruitee.com/o/3d-generalist/c/new ; https://zackdfilms.recruitee.com/o/animation-pipeline-director | B |
| S49 | Ali Abdaal style: https://www.choppity.com/tools/recreate-video-editing-style/ali-abdaal/ ; https://blog.adobe.com/en/publish/2024/09/05/how-ai-helps-ali-abdaal-save-hours-every-week ; Skillshare course page | C |
| S50 | Vox / Johnny Harris: https://www.premiumbeat.com/blog/replicating-vox-motion-graphic/ ; https://aescripts.com/learn/how-johnny-harris-makes-maps/ | C |
| S51 | Short-form caption conventions: https://www.kapwing.com/explore/hormozi-style-captions-template ; https://openclip.app/use-cases/hormozi-style-captions ; https://www.clipspeed.ai/tools/caption-style-preview.html | C |
| S52 | GSAP Standard "No Charge" licence: https://gsap.com/community/standard-license/ ; https://webflow.com/blog/gsap-becomes-free (30 Apr 2025) | B |
| S53 | Remotion licence and pricing: https://www.remotion.dev/docs/license ; https://remotion.dev/pricing | B |
| S54 | anime.js (MIT, v4): https://github.com/juliangarnier/anime (fetched) | B |
| S55 | Motion Canvas (MIT): https://github.com/motion-canvas/motion-canvas (fetched) | B |
| S56 | Pixabay Terms of Service: https://pixabay.com/service/terms/ | B |
| S57 | Atkinson Hyperlegible Next (OFL 1.1): https://github.com/googlefonts/atkinson-hyperlegible-next ; https://dfsg-new-queue.debian.org/reviews/fonts-atkinson-hyperlegible-next/0.0~git20250221.7925f50-2 | B/C |
| S58 | Anek (OFL 1.1, 9 Indic scripts + Latin, variable weight/width): https://github.com/EkType/Anek (fetched) | B |
| S59 | Script pacing ~130 wpm guidance: https://ctl.columbia.edu/?p=29582 | C |
| S60 | Subtitle reading speeds: https://clevercast.com/bbc-subtitling-guidelines (BBC 160–180 wpm) | C |

---

## DESIGN IMPLICATIONS FOR AADHI

Concrete rules, parameters and choices. Each has a default and, where useful, a lint check, so "nothing monotonous" can be tested automatically.

**A. Mascot (requirements 1 and 7)**
1. Dynamic mode sets `mascot_position="hidden"` on every scene except T01 (intro) and T06 (recap). Lint: Aadhi ≤ 10 s per appearance, ≤ 2 appearances, total ≤ 20 s in 16:9 and ≤ 14 s in 9:16. Basis: image principle and agent meta-analyses (S3, S13).
2. Aadhi-based generation stays unchanged in its own tab ("Classic with Aadhi"). Dynamic mode becomes the default tab. Both modes write the same `Screenplay`. A new `Screenplay.style_mode: "dynamic" | "classic_aadhi"` selects the renderer path.
3. Commission or produce a vertical Aadhi intro/recap clip at ≥ 1080 px wide (or a transparent cut-out). Centre-cropping today's 1280×720 clips gives about 405×720, which is too soft.

**B. Duration and pacing (requirement 6)**
4. 16:9: target 4:30–5:30, hard limit 6:00 (S1). About 750–900 words at 160–180 wpm. 3–4 chapters of 45–90 s.
5. 9:16: default "Reel ≤ 2:58" (YouTube Shorts and Instagram limits, S17, S21). Optional "Vertical lesson 3:00–4:00" with a warning that YouTube will publish it as a regular, non-Short video. 170–190 wpm, 480–700 words, 2–3 chapters.
6. TTS speaking rate defaults: 170 wpm (16:9), 180 wpm (9:16). Automatically slow by 10% in formula and code scenes.
7. Visual-event gap: ≤ 6 s (16:9) and ≤ 3 s (9:16), except `pause_after` ≤ 4 s and formula holds ≤ 5 s. Lint error if exceeded.
8. Scene length: 8–20 s (16:9), 4–12 s (9:16). No template twice in a row. No template in more than 25% (16:9) or 30% (9:16) of scenes. At least 8 (16:9) or 6 (9:16) distinct templates per video.
9. A re-engagement beat (T16, T37, T10, T15 or T05) at least every 90 s (16:9) or 45 s (9:16). One T37 quiz every 2–2.5 min in 16:9 and 1 per reel.
10. Hook: 9:16 hook text visible at frame 0 and spoken by 3 s. 16:9 hook done by 15 s, promise and roadmap by 30 s (S19). The hook question must be answered later (`hook.answer_scene_id`).

**C. Templates and pedagogy (requirements 2 and 9)**
11. Implement the 40 templates in §8 as a typed `template` field on scenes, with 16:9 and 9:16 layout variants each. Ship in this order: T01, T02, T05, T08, T09, T10, T11, T13, T15, T16, T20, T21, T26, T27, T28, T29, T34, T37, T38, T39 (20 templates covering all current `BoardItemKind`s). Then add the rest.
12. The planner selects templates with the §8.1 signal table. A misconception in the plan forces at least one T15. Every `figure`, `manim` or diagram uses build-up (T19 or T20), never a static pop-in (S4).
13. On-screen text ≤ 7 words per label. Full sentences only in captions (redundancy, S3). Every reveal or highlight is anchored to a narration word time (existing `sync.py`).
14. Formula scenes: one lesson-wide colour per symbol (stored once in `Screenplay.variable_colors`), no camera drift or punch-zoom, hold ≥ 3 s after the formula is complete.
15. 9:16 never accumulates a board. One idea per screen (carousel) with an "n/N" counter. Tables have ≤ 3 columns or become transposed cards. Code ≤ 12 lines × 32 characters.
16. Optional dialogue: a second "student" voice for the myth lines in T15 and T16 (S5). Off by default; one toggle.

**D. Colour, type and motion rotation (requirements 2 and 5)**
17. Look seed = `hash(project_id, version_number, format)`, stored as `look_seed` so preview equals the MP4. A "Shuffle look" button increments it (Vyond-style, S34).
18. Twelve theme families (§9.2), generated in OKLCH. Exclude the last 4 families per course. Chapter accent hue steps through +35°, +70° and −35°. The background cycles `bg → bg-alt → bg-tint` with no immediate repeat. At most 1 light "interlude" scene per chapter in dark themes.
19. Never rotate `--good`, `--bad`, `--warn` or `--hl`, or the variable colours, within a lesson. Charts use the Okabe-Ito order with direct labels (S27).
20. Contrast validator in the build: ≥ 4.5:1 body, ≥ 3:1 for display ≥ 24 px, ≥ 7:1 target for captions (S24). Auto-nudge L in ±0.02 steps and fail the build only if it cannot be fixed. At most 5 theme colours plus neutrals, and at most 3 hue families on screen.
21. Adjacent-scene background change in L between 0.03 and 0.35, except at bumpers. Full-frame colour changes cross-fade over ≥ 300 ms and never exceed 3 flashes per second (WCAG 2.3.1).
22. One display face per video from an OFL pool, excluding the last 3 per course. Body face fixed (Inter, or Atkinson Hyperlegible Next for "high legibility"). Indic lessons use Anek (vary width and weight) plus Noto Sans (S58). Verify each licence when vendoring.
23. One motion personality per video (Crisp, Soft, Cinematic, Editorial, Chalk, Bouncy-soft), each bundling easing, transitions, caption style, SFX pack and music mood (§9.3). Exclude the last 2 personalities per course.
24. Easing: entrance cubic-bezier(0,0,0.3,1), exit (0.4,0.14,1,1), standard (0.4,0.14,0.3,1), hero (0.05,0.7,0.1,1). Durations: micro 200–300 ms, entrance 400–600 ms, large move 600–900 ms, transitions 400–700 ms (chapter wipe ≤ 800 ms), stagger 60–120 ms, count-up 800–1200 ms, Ken Burns 1.00→1.04–1.06 per shot.
25. Transitions: ≥ 50% hard cuts inside chapters. Stylised transitions ≤ 30% (16:9) and ≤ 45% (9:16). Never the same stylised transition twice in a row. Colour-block wipes only at chapter bumpers. Whip-pans ≤ 2 (16:9) or ≤ 4 (9:16), and never into formula or code scenes.
26. Punch-zoom (1.00→1.10 in 250 ms) on at most 1 emphasised keyword per 8 s, never during formulas (Submagic-style, S39).

**E. Captions (requirement 2)**
27. Captions come from the script plus TTS word timings, never ASR. 16:9: ≤ 42 characters per line, ≤ 2 lines, ≤ 20 cps (S23), 44–52 px. 9:16: 2–4 words per chunk, 60–72 px, weight 800, 7 px stroke or pill, centred at y ≈ 1100–1240 inside the safe box x 96–888, y 288–1248.
28. Six caption styles (§6.5), one per video from the motion personality. Glossary terms always use `--hl`. Burned-in by default for 9:16. Optional and selectable (existing mov_text track) for 16:9.

**F. Sound (requirements 3 and 4, structure only)**
29. Music bed under speech at −22 to −26 dB relative to narration (current 0.06 ≈ −24 dB is fine). −10 to −14 dB in speech-free intro, bumpers, countdowns and outro. Duck further during formulas. Instrumental only. Every track claim-free (1–3 min Shorts with any Content ID claim are blocked, S17).
30. One track per video, excluding the last 5 per course, chosen by personality mood. Optional per-chapter track changes crossfade at bumpers. Snap bumper boundaries to the nearest music beat within ±120 ms when narration does not move.
31. SFX map (§6.6): whoosh on stylised transitions (−12 to −16 dB), pops in word gaps (≤ 1 per 2 s in 16:9, ≤ 1 per 1 s in 9:16), riser on countdowns, chime on correct answers, no SFX over a spoken key term (place them using word timings). Master at about −14 LUFS, ≤ −1 dBTP (verify).
32. One narrator voice per video, excluding the last 3 voices per course, matched to language and locale. An optional second voice is used only for dialogue templates.

**G. Editor (requirement 8)**
33. Transcript-based cutting: deleting words or beats in the narration view removes the matching audio, captions and visual events. Only the edited beats are re-synthesised (Descript model, S37).
34. External video insertion as T33: upload or library clip with in/out trim handles, fit cover/contain, audio mute/duck/keep, optional auto-caption, and a 9:16 focus-point reframe (Opus Clip model, S38). Offer `silencedetect`-based trim suggestions on imported clips (Kapwing/Submagic model, S40, S39).
35. Scene inspector: template swap that keeps content (Synthesia, S29), background variant, accent override, transition in/out, "visual energy" slider (Calm/Balanced/Lively, which scales rules 7, 8 and 25), safe-zone overlay toggle for 9:16, music beat markers on the timeline strip.

**H. Rendering and licences**
36. Animated templates need per-frame capture (30 fps) of the scene layer, computed from the same pure `(scene, t)` functions as the player. Static holds may reuse a single frame. This replaces "one screenshot per state" for animated spans.
37. Do not adopt GSAP (its no-code visual-builder restriction, S52) or Remotion (paid company licence for 4+ people, S53). Use an in-house deterministic easing and keyframe evaluator, or anime.js v4 (MIT, S54). Motion Canvas (MIT, S55) is the fallback for Manim-like vector builds.
38. Ship SFX and music only inside rendered mixes, never as downloadable standalone files (Pixabay-style licence terms, S56). Prefer CC0 or owned assets, and record the licence per asset in the manifest.

**I. Quality gates (lint and critic additions)**
39. Variety linter on the timeline: rules 7–10, 18, 21 and 25–26. Run as `issue` warnings in Visual Review; errors only for safe-zone violations, contrast failures, and Aadhi outside T01/T06 in dynamic mode.
40. Track per-course history of family, display face, personality, voice and music (derived from rendered versions) so the "different look per video" exclusions in rules 18, 22, 23, 30 and 32 can be enforced without new user settings.
