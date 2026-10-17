# Dynamic video mode and editor upgrade: design

> **Status:** proposed design, **revision 2**, 2026-10-10. Branch `overhaul/v2` at `195be39`. Nothing in this document is implemented yet.
> **Decides:** the new default **Dynamic video** mode, which makes ~5-minute 16:9 lessons and 3–4-minute 9:16 reels with Aadhi only in the intro and an optional recap. It also covers rotation of visuals, voices and music, the editor upgrade (insert external clips, cut unwanted parts), and the batch plan.
> **Keeps:** the existing Aadhi-based generation, which moves to its own **Aadhi presenter** tab. Its outputs stay unchanged (§3.6).
> **Revision 2** answers three reviews (pedagogy, engineering, teacher experience; 93 findings). Every finding is resolved in Appendix C, either accepted (with the section that changed) or rejected with the evidence. Appendix D lists what could not be verified.
> **Precedence:** as in `docs/ARCHITECTURE.md`, once a batch lands its contract files (`aadhi/schemas/*`, `docs/INTERFACES.md`, `docs/API.md`) win over this document. Each batch updates those docs as part of its acceptance (§10).

---

## 0. How to read this document

### 0.1 Research reports cited

| Key | Report (`docs/research/<file>`) | What it covers |
|---|---|---|
| **LS** | `learning-science.md` | Multimedia-learning meta-analyses, length, segmenting, retrieval, captions, voice, music, colour, instructor presence |
| **SF** | `short-form.md` | Platform limits (Shorts/Reels/TikTok/LinkedIn), encoding, loudness, safe zones, captions, hooks, YouTube long-form rules |
| **MR** | `motion-rendering.md` | Deterministic browser capture, benchmarks measured on the dev laptop, library licences, WebGL, fonts, colour pipeline |
| **AU** | `audio.md` | TTS providers and voices, AI music, royalty-free libraries, procedural music prototype, mixing evidence, ffmpeg filter facts |
| **ED** | `editor-nle.md` | How editors handle insert/cut, B-Script study, anchored edit model, clip ingest, SSRF, stock APIs, YouTube terms, shortcuts |
| **CP** | `competitive.md` | AI video tools and educational channels, template catalogue, pacing/motion/typography/palette grammar |
| **CB** | `codebase.md` | Read-only audit of this repo: where Aadhi and 16:9 are hard-coded, renderer limits, schema extension pattern, risks |
| **RV** | the three revision-2 reviews (pedagogy, engineering, teacher UX) and this revision's own measurements (scratchpad `rev/`: palette and colour-vision search, text-fit measurement with the vendored Inter Bold, MP4 start-time probe) | Findings L-1…L-28, E-1…E-36, T-1…T-29 (Appendix C) |

The reports are kept in `docs/research/` (owner answer Q12; copied in B0 and indexed by `docs/research/README.md`). Appendix B lists every primary source with its URL, so this document stands on its own.

Citation form: `[LS §8.2; Phan 2023]` means the report section plus the primary source it rests on. `[RV L-4]` points at a review finding.

### 0.2 Evidence labels

| Label | Meaning |
|---|---|
| **strong** | Meta-analysis, meta-meta-analysis or a very large field dataset (e.g. Noetel 2022, Guo 2014) |
| **moderate** | Controlled experiments, or an official platform/standard document (e.g. Szpunar 2013, YouTube Help, WCAG) |
| **practice** | Practitioner consensus, vendor documentation about other products, or a secondary source not re-verified |
| **unverified (M)** | Cited from a reviewer's memory and not re-verified in this session. Never the sole basis of a hard rule; listed in Appendix D |
| **measured** | Measured on our hardware this session |
| **design** | Our choice, made to sit safely inside the evidence |

Rules carry ids `R-xx` (§1.5). Lint codes, QA checks and tests refer to those ids.

### 0.3 What changed in revision 2

1. **Learning first, variety inside meaning.** Templates now carry a *semantic class*; variety may only swap templates inside a class (§4.2, §4.4). Chapter accents colour structure only, never content (§4.7). Reels default to a *Learning* profile (§2.1). SFX are rare (§4.10). Captions are phrase chunks in sentence case (§4.11).
2. **No silent content loss.** A scope check after the brief offers a series of lessons or a focus choice, and reports what is left out (§2.2, §6.4).
3. **Retrieval that works.** Think time follows the reading load, with no music swell or riser; predictions come before the explanation; worked examples fade (§2.3, §4.3).
4. **Stable for teachers.** Regenerating keeps the look and voice; editor saves never re-style untouched scenes; duration and quality problems are fixed or flagged before rendering, never after (§5.3, §6.8, §7.12).
5. **Safe to ship.** A this-week Gemini TTS hotfix on the deployed branch; asset authorisation for every new key field; no silent loss of new fields on older builds; CI gates that actually run in the container (§10).
6. **A thin first release covering both formats**, with effort estimates per batch, followed by the editor and the variety expansion (§10).

---

## Executive summary: the decisions

1. **Dynamic is the new default mode and Aadhi presenter keeps its own tab.**
   - Both modes write the same `Screenplay`. A new optional `Screenplay.presentation` selects Dynamic.
   - A lecture without `presentation` is the classic mode. It is built, timed and rendered as today, and an invariance harness blocks drift (§3.6).
2. **Two formats share one pipeline.**

   | Format | Canvas | Length | Platform-safe preset |
   |---|---|---|---|
   | Lesson | 1920×1080 | 5:00 (4:30–5:30) of generated content | — |
   | Reel | 1080×1920 | 3:30 (3:00–4:00) | **≤ 2:58**, plus "Export as 2 parts" |

   Both are 30 fps CFR. A source that needs more than one lesson's worth of narration becomes a **series** of 5-minute lessons when the teacher agrees; nothing is dropped silently (§2.2).
3. **Aadhi appears only in the intro (5–8 s) and an optional recap (5–8 s)**, built from the existing clips. His line ends before anything asks the viewer to think. Four seeded 16:9 intro layouts keep the gesture toward the title. The *popup* clip (pseudo-equations) and the *right* clip (mirrored crest) are never used (§3).
4. **Content stays typed data; the look is chosen by deterministic art direction.**
   - The LLM writes beats and board items as today, plus five structured item kinds (`question`, `stat`, `step`, `quote`, `pair`) and a `visual_intent` hint.
   - `pipeline/direct.py` picks the template, background, transition and palette. It swaps templates only inside a **semantic class**, so variety never changes what a scene claims (§4.4).
5. **Variety in the container, never in the code** [LS §1.12].
   - Per video: theme family, display face, motion personality, caption style, voice, music, hook kind, intro layout and checkpoint order.
   - Per chapter (16:9) or act (9:16): the structure accent. Per scene: template, layout variant and background tone.
   - Fixed in every video: the content colours (`warn`, `ok`, the `mark` highlighter and three entity colours chosen for colour-vision safety) and the two cue styles (§4.7, §4.8).
6. **Motion engine: "the page owns every pixel".** Every output frame is a pure function of `t`, drawn by the same stage code as the live player, captured with CDP in parallel chunks and concatenated without re-encoding. Render time is provisional until the B1 container benchmark (§7).
7. **Audio is rotated, calibrated and mixed for comprehension.**
   - Voices rotate per lesson inside the audience's accent pool (Indian English for en-IN by default), calibrated for rate and loudness.
   - Music is a mood and variety layer that is at best neutral under speech: −22 LU under speech, never closer than −20, nothing rising during think time. The free procedural bed plays only under speech; music is exposed on its own only when it comes from Lyria, a curated pack or the teacher (§5).
8. **The Gemini TTS model shuts down on 17 Nov 2026.** A standalone hotfix (H0) migrates it this week on the deployed branch and on v2 (§10).
9. **Edits are anchored to content, not output seconds.** Cuts are word ranges on beats; inserted clips are a `clip` scene type; B-roll is an overlay anchored to a word. Clips arrive by resumable upload, stock search, a direct file link fetched through an SSRF-safe path, or screen/camera recording; YouTube pages are refused (§8).
10. **Delivery** is a hotfix plus ten batches (H0, B0–B9). The first usable release (B4) covers **both** formats with a thin, polished set: 11 content and practice templates plus the 12 bookend, forced and fallback modules every lesson needs, 4 theme families, 2 personalities, 2 caption styles, procedural and Lyria music, Edge or Azure voices. The editor follows in B5–B6 and the full variety set in B7 (§10).

---

## 1. Goals, non-goals, success metrics and the rulebook

### 1.1 Goals

| # | Goal | Requirement |
|---|---|---|
| G1 | Replace the always-on mascot background with designed, content-driven motion graphics, as the default mode | 1, 2 |
| G2 | Produce two formats from one pipeline: a 16:9 lesson (~5 min) and a 9:16 reel (3–4 min, plus a platform-safe ≤ 2:58 preset and a two-part export) | 6 |
| G3 | Show Aadhi only in the session intro and an optional recap, 5–8 s each | 1 |
| G4 | Vary by rule without changing meaning: per-video theme, typography, motion personality, voice, music, hook kind and layouts; per-chapter or per-act structure colour; per-scene template within the scene's semantic class | 2, 4, 5, 9 |
| G5 | Teach effectively: hook prequestion, pre-training, segmenting, signalling, faded worked examples, refutation, prediction before explanation, retrieval with adequate think time, relational recap, phrase captions | 2, 9 |
| G6 | Rotate calibrated neural voices from the audience's accent pool; give every video its own instrumental music, mixed for comprehension and platform loudness | 3, 4 |
| G7 | Make the editor insert external clips and cut unwanted parts reliably, without losing edits on rebuilds, and without restyling scenes the teacher did not touch | 8 |
| G8 | Keep preview equal to export: one deterministic stage, frame-accurate MP4 | 9 |
| G9 | Keep the Aadhi-based generation intact in its own tab | 7 |
| G10 | Never drop teacher content silently: report coverage and offer a series | 2, 9 |

### 1.2 Non-goals for v1

- **No YouTube or social-platform downloading.** It is forbidden by YouTube's Terms and API policies [ED §4].
- **No ASR captions of our own narration.** We already have the script and the word timings [CP §6.5].
- **No in-browser encoding** with WebCodecs, ffmpeg.wasm or Remotion. The server renders [ED §8].
- **No GSAP, Remotion or Theatre.js Studio** (licences, §7.13).
- **No LLM-written animation code.** The model chooses typed presets only; CSP stays `script-src 'self'`.
- **No 9:16 version of the classic Aadhi presenter mode.** It stays 16:9 as today.
- **No new Aadhi artwork in the first release.** The asset upgrade is batch B8, now recommended rather than optional (§3.5).
- **No annotation pins placed by the LLM on generated rasters.** The model never sees the image, so its coordinates would be guesses.
- **No general multi-track NLE.** The editor has the fixed lanes of §8.8.
- **No branching or interactivity beyond the checkpoint pause** in the live player.

### 1.3 Success metrics

**Engagement** (from the existing per-scene analytics, plus new checkpoint events):

| # | Metric | Target | Basis |
|---|---|---|---|
| E1 | Share of plays still watching at 0:30 | 16:9 ≥ 80%; 9:16 ≥ 70% | YouTube "Intro" metric; Shorts "viewed vs swiped" benchmarks [SF §5] (practice) |
| E2 | Average percentage viewed | 16:9 ≥ 75%, reels ≥ 60%; Dynamic ≥ classic +15% relative on matched lessons | Guo 2014 [LS §3.1] (strong) |
| E3 | Drop-off cliffs | No scene loses more than 2× the video's median per-scene drop | design |
| E4 | Checkpoint participation (live player) | ≥ 60% of viewers who reach a checkpoint answer it | design |

**Learning:**

| # | Metric | Target | Basis |
|---|---|---|---|
| L1 | Checkpoint first-attempt accuracy | 50–85% per question; outside that range flags the question for review | design (desirable difficulty) |
| L2 | Classroom pilot (§10, B9): **within-student crossover**, three arms (Classic, Dynamic, Dynamic-lean: no SFX, music in gaps only, static backgrounds), ≥ 6 matched topics in a balanced Latin square, pretest covariate, immediate and 7-day delayed tests with **transfer items** | Dynamic non-inferior to Classic at a 0.25 SD margin on both tests; Dynamic vs Dynamic-lean estimated | ≈ 126 students (conservative: one topic per arm per student, ρ = 0.5, one-sided α 0.025, 80% power) [RV L-19]. A between-sections design at Δ ≥ −0.1 SD would need ≈ 1,570 per arm, which is infeasible |
| L3 | Structural learning rules hold | 100% (lint): hook answered; each pre-training term used in ≥ 2 later scenes; every recap idea relational; ≥ 1 recap question at apply/transfer level; every myth refuted in its chapter; every prediction placed before its answer | [LS §5, §10; RV L-10, L-14, L-16] |

**Production and variety:**

| # | Metric | Target |
|---|---|---|
| P1 | Motion-engine render time at 1080p30 | Provisional: 5-min 16:9 ≤ 8 min and 3.5-min reel ≤ 6 min inside the production render container (§7.10). The numbers are fixed by the B1 container benchmark; if missed, the documented fallbacks apply and Q7 is reopened |
| P2 | Determinism | Byte-identical frames for 1×300, 3×100 and reverse-order renders (Linux container) |
| P3 | Preview/export parity | SSIM ≥ 0.98 at 20 random instants (`evals/render_parity.py`) |
| P4 | Rotation compliance | 100% of videos satisfy R-39 (voice), R-47 (music) and R-51 (theme family) against the history of §5.3 |
| P5 | Cost per 5-min lesson, per provider | Free stack (Edge + procedural music) $0. Azure TTS ≈ $0.07. Gemini 3.8 Flash TTS ≈ $0.07 (≈ $0.14 from 1 Jan 2027). Lyria 3.5 ≤ $0.32 (≤ 3 cues + ≤ 1 retry). ElevenLabs Music ≈ $0.78 (B7, opt-in only) |
| P6 | Editor usability | 5 teachers each insert, trim and cut a clip in ≤ 3 min (moderated test) |
| P7 | Teacher predictability | Regenerate keeps voice, theme and music in 100% of cases unless "Fresh look" is ticked; an editor save changes the look of no scene the teacher did not touch (property test) |

### 1.4 Definitions used by rules, lints, budgets and evals

| Term | Definition |
|---|---|
| **Speech wpm** | Words ÷ the sum over beats of (last word end − first word start), from TTS word timings. It includes pauses inside a beat and excludes `pause_after`, holds, think time, bumpers and silences between beats. This one definition is used by R-36, calibration, `voice.rate`, the planner's budgets and the evals [RV L-28]. Our 155 speech wpm is about 135–140 wpm over the whole runtime, slower than LS's 150–165 effective-rate band, which is deliberate for L2 listeners |
| **Generated runtime** | Timeline duration minus teacher-inserted `clip` scenes. Duration rules (R-01) apply to it. The platform-safe container limit applies to the whole file |
| **Think time** | `max(min_fmt, words_to_read ÷ 130 × 60 + 3 s)`, capped at 12 s, where `words_to_read` is the question plus its options; `min_fmt` is 5 s (16:9) or 4 s (9:16); free recall of three ideas gets ≥ 6 s [RV L-4] |
| **Semantic class** | What a template claims about its content: `structure`, `prompt`, `definition`, `statement`, `enumeration`, `contrast`, `correspondence`, `sequence`, `cause`, `completion`, `hierarchy`, `quantity`, `formula`, `procedure`, `code`, `table`, `refutation`, `media`, `fallback` (§4.2) |
| **Exposed music** | Music audible without speech over it (bookends, bumpers, gaps ≥ G). Only *exposable* sources (Lyria, ElevenLabs, a curated pack, teacher upload) are ever exposed; the procedural bed is *bed-only* (§5.8) |
| **Anchored event** | A visual event whose target is named in the narration within ±300 ms (an item revealed on its anchor word, a sync cue, a fill, a `var` cue). Only anchored events count for pacing (R-15) [RV L-24] |
| **Act (9:16)** | Hook (intro + promise), Build 1 (segments up to the mid-reel question), Build 2, Check (recap + outro) |

### 1.5 The rulebook

These are the numbers the system enforces. Enforcement column:
- `gen` means the generator or prompt aims for it.
- `lint:<code>` means a deterministic lint (§6.9).
- `pre` means a preflight check on the built timeline before capture (§7.12). It fixes automatically where it can; otherwise it blocks with a teacher-facing action.
- `qa` means a post-render container check (§7.12).
- `ui` means an editor warning.

Rules changed in revision 2 are marked †.

**Structure**

| Id | Rule | 16:9 lesson | 9:16 reel | Enforced | Evidence |
|---|---|---|---|---|---|
| R-01 † | Duration (generated runtime) | Target 5:00 (4:30–5:30), hard max 6:00. Teacher-inserted clip time is excluded and shown as information | Target 3:30 (3:00–4:00). Platform-safe: content ≤ 178.0 s **and** container < 179.5 s, including inserted clips | gen, `pre` (fit step, §6.4), lint `format.duration` | Guo 2014 (strong) [LS §3.1]; Shorts ≤ 3:00, AAC priming [SF §1] (moderate) |
| R-02 | Segments | 3 chapters (2–4), 60–90 s each (45–100 allowed) | 5–7 micro-segments of 20–40 s (4–5 platform-safe), grouped in 4 acts | gen, lint `format.segments` | Segmenting g = 0.34 [LS §2] (strong) |
| R-03 † | Hook | Spoken hook finished by 15 s; first new insight by 0:30 | Hook text on screen at frame 0; spoken hook first in Aadhi's line, ≤ 8 words, ending by 3.0 s measured from word timings | gen, lint `hook.late` | Prequestions/curiosity [LS §5] (moderate); TikTok first 3–6 s [SF §5] (practice) |
| R-04 † | Hook answered | An `answers` beat names the hook item in a later scene | same | lint `hook.unanswered` | [LS G1] |
| R-05 † | Pre-training | 2–4 component terms by 0:22, each used in ≥ 2 later scenes | Optional; one-line promise by 10 s | gen, lint `pretraining.missing`, `pretraining.unused` | Adapted from the pre-training principle, which was measured as a separate pre-training phase (Mayer, Mathias & Wetzell 2002) [LS §2] (design, informed by moderate evidence) [RV L-10] |
| R-06 † | Checkpoints | One retrieval check after each chapter's explanation, first ≥ 60 s; ≤ 4 options; types in a seeded order (MCQ, free recall, true/false, apply) | ≥ 2 checks (mid-reel and end), free recall or single answer only; platform-safe keeps both | gen, lint `checkpoint.*` | Szpunar 2013 [LS §5] (moderate) |
| R-07 † | Think time | §1.4 formula, ≥ 5 s, visual ring only, the narrator invites a pause at the first check | §1.4 formula, ≥ 4 s | timeline, lint `checkpoint.think_short` | Retrieval effort is the active ingredient; re-exposure without retrieval gave no benefit (Szpunar 2013) [RV L-4] |
| R-08 † | Retrieval recap | In the last 10–15%: 2 questions (≥ 1 apply or transfer), then free recall of the big ideas (≥ 6 s), then "3 big ideas" as **relational statements** that use the pre-training terms (e.g. "↑R → ↓I at fixed V"). No myths | 1 free-recall question + 3 relational keywords | gen, lint `recap.*`, `myth.in_recap` | Testing effect [LS §5]; [RV L-10, L-16] |
| R-09 † | Worked example | ≥ 1 for quantitative or procedural topics, one step per 3–6 s; in any example of ≥ 3 steps the final step is a "Your turn" blank with a 4–6 s hold before it fills; a second example is faded | ≥ 1 compact example, last step a "Your turn" blank | lint `example.missing`, `example.no_fade` | Worked-example and fading effects [LS H1]; `evals/metrics.py` already measures fading |
| R-10 | Misconception | ≥ 1 myth→fact scene when the plan lists misconceptions | optional | lint `misconception.unused` | Muller 2008 [CP S5]; cognitive conflict [LS H2] |
| R-11 | Outro | No new content. 6–10 s, or 15 s with "end-screen space" | ≤ 3 s call to action; last line loops back to the hook | gen | YouTube end screen 5–20 s [SF §8] |
| R-61 † | Coverage | Every essential brief concept is covered, deliberately deselected, or assigned to another part of a series; never thinned out of worked examples | same | `pre` scope check, lint `coverage.dropped` | Guo 2014: longer material → more short videos, not denser ones [RV L-1] |
| R-62 † | Prediction before explanation | A `predict` scene sits before the scene that explains the outcome, in the same chapter, and that scene answers it | same | lint `predict.order`, `predict.unanswered` | Predict–observe–explain (Crouch et al. 2004) (unverified (M)) [RV L-14] |
| R-63 † | Myths | A myth hook is posed as "True or false: …?" with no MYTH stamp and refuted in the same chapter; the fact card is the scene's final frame and visually dominant | same | lint `myth.unrefuted`, `myth.in_recap` | Debunking Handbook 2020 (unverified (M)) [RV L-16] |

**Aadhi**

| Id | Rule | 16:9 | 9:16 | Enforced | Evidence |
|---|---|---|---|---|---|
| R-12 † | Aadhi appears only in the scene carrying `look.presenter` at the start, and in the recap | Intro 5–8 s; recap 5–8 s (default 6.0); total ≤ 16 s | Intro 5–8 s; recap 5–8 s (default 5.0, off by default); total ≤ 16 s | lint `aadhi.*` | Persistent seductive details g = 0.43; instructor presence draws gaze [LS §1.1, §10] (strong); owner's 5–10 s per appearance |
| R-13 † | Aadhi signals and is brief | ≤ 16 words; the title is on the side his gesture opens to; his line ends before any think time | Hook first (≤ 8 words), then a sign-on of ≤ 5 words | lint `aadhi.words`, `aadhi.too_long` (measured) | Agents help when they signal (g = 0.28–0.44) [LS §10] |
| R-14 | Clips | Never `popup` or `right` in Dynamic mode | same | code | §3.2 audit |

**Pacing**

| Id | Rule | 16:9 | 9:16 Learning (default) | 9:16 Social energy (opt-in) | Enforced | Evidence |
|---|---|---|---|---|---|---|
| R-15 † | Anchored-event cadence | Median gap 3–5 s (dense beats 4–5 s); max static 8 s | Median 2.5–4 s (dense 4–5 s); max static 6 s | Median 1.5–3 s; max static 4 s | lint `pacing.static` (anchored events only) | Lang 2000 [LS §4] (moderate); fast pacing hurts verbal encoding more than visual (Lang, Potter & Bolls 1999) [SF §6.2]; [RV L-6, L-24] |
| R-16 | Holds | A hold with `hold_reason` (formula, code, diagram, think) may last ≤ 12 s if a cue moves or a ring runs; the background freezes (R-55) | ≤ 8 s | ≤ 8 s | lint `pacing.hold` | [LS B2] |
| R-17 | Word anchoring | Every content event starts within −200/+300 ms of its anchor word; reveals start 120 ms before word onset | same | same | `events.py` | Temporal contiguity g = 0.78 [LS §2] (strong) |
| R-18 † | Macro changes | Structure accent at every chapter boundary; background **preset** fixed per video, except at a chapter boundary marked `builds_on = false` or with energy *Lively*; never mid-chapter; never in the 2 s before a reveal | Accent and background at act boundaries | Accent and background at segment boundaries | `direct.py` | Memory for links across event boundaries is weaker (Swallow, Zacks & Abrams 2009; DuBrow & Davachi 2013) (unverified (M)) [RV L-12] |
| R-19 | Scene length | 6–30 s (worked example or quiz ≤ 45 s) | 4–20 s (≤ 30 s) | same | lint `scene.length` | design |
| R-20 † | Template variety | No template twice in a row (except forced or `continues_from` scenes); ≤ 25% share; distinct templates ≥ min(5, classes present) | ≤ 30% share; same distinct rule | same | `direct.py`, lint `look.variety` (system) | design; [RV L-2, E-12] |
| R-21 | Breathers | After a dense run (weight ≥ 5), 15–30 s lighter | No two consecutive micro-segments with formula or code | same | lint | [LS B5] |
| R-22 | Re-engagement | Question, myth, stat, predict or bumper at least every 90 s | At least every 45 s, plus a micro-hook at each segment start | same | lint `pacing.reengage` | [LS §5]; [SF C11] |
| R-23 | Transitions | ≥ 50% hard cuts within chapters; stylised ≤ 30%; whip ≤ 2 per video; each ≤ 0.8 s | Stylised ≤ 45%; whip ≤ 4 | same | `direct.py` | [CP §6.3] (design) |
| R-24 | No repeated stylised transition | Never the same stylised transition twice in a row; never a whip into formula or code | same | same | `direct.py` | design |

**Text and signalling**

| Id | Rule | 16:9 | 9:16 | Enforced | Evidence |
|---|---|---|---|---|---|
| R-25 † | On-screen text | ≤ 3 text items visible; ≤ 8 words per item; ≤ 24 prose words visible; never the narration verbatim, **except** in `aadhi_intro`, `hook_question`, `kinetic_statement` and `quote_card`, where the on-screen text *is* the caption (R-31) | Headline ≤ 1 line in explanation templates | lint `text.*` | Modality, redundancy [LS §2, §6] |
| R-26 † | Signalling | Exactly two cue styles, implemented once: `cue.term` (key-term emphasis, one style per video) and `cue.look_here` (pointer to a part, row, bar or line). One `cue.term` per narrated key term; ≤ 2 concurrent cues; ≤ 1 new cue per 2 s; punch-zoom counts as `look_here`, ≤ 1 per 20 s | same | `events.py`, source scan, lint `signal.*` | Signalling g = 0.43 [LS §2] (strong) [RV L-11] |
| R-27 | Spatial contiguity | Labels on or within 5% of frame width of their part; charts direct-labelled, no legends | same | templates | Spatial contiguity g = 0.63 (strong) |
| R-28 | Representational visuals | Every scene carries a meaning-bearing visual; decoration counts as none; variety never swaps a representational template for a typography-only one | same | lint `visual.decorative`, `direct.py` | Multimedia d = 1.35; decorative animation −0.05 [LS §2] |
| R-29 | Type sizes (logical px at 1080 output) | Body 52 (48–56); labels ≥ 40; never < 36; headings 84–120; display 140–200 | Body 56–64; labels ≥ 44; never < 40; hook headline 88–104 auto-fit; weight ≥ 600 at ≤ 64 px | theme tokens, `pre` text boxes | [SF §4.2] (design) |
| R-30 | Formula scenes | No camera drift or punch-zoom; hold ≥ 3 s after the formula completes; entity colours constant per lesson | same | templates | [CP §5]; coherence |
| R-64 † | Reading budget | In every window between caption-chunk changes, board words newly revealed + caption words visible ≤ window seconds × 200 ÷ 60. Text that must be read *without* narration (quiz options, silent cards) is budgeted at 130 wpm | same | lint `text.reading_budget` | LS §6.4 rule; 200 wpm is the repo's existing English reading rate; 130 wpm for unsupported reading [RV L-8] |

**Captions**

| Id | Rule | 16:9 | 9:16 | Enforced | Evidence |
|---|---|---|---|---|---|
| R-31 † | Default | Set by "Where will students watch?" (§2.6): burned for WhatsApp/Drive/classroom and LMS; clean master + `.srt`/`.vtt` + default-on soft track for YouTube and our lesson page. Captions are blanked while on-screen text matches the narration (≥ 70% token overlap) | Burned + sidecars | render options, `kinetic.py` | Captions with standard phrase subtitles helped L2 listening and vocabulary, g = 0.99 (Montero Perez et al. 2013, language learning) [LS §6]; we extend it to engineering content by inference (design) [RV L-7, L-9] |
| R-32 † | Shape | ≤ 42 chars/line, ≤ 2 lines, ≥ 1.0 s | Phrase chunks ≤ 32 chars, ≤ 2 lines each measured to fit 660 px, 1.5–3.0 s on screen (≥ 1.0 s hard) | `captions.py`, `kinetic.py`, lint `captions.*` | Netflix TTSG [SF §4]; word-by-word chunks pull gaze from images (Rajendran et al. 2013) (unverified (M)) |
| R-33 | Reading rate | ≤ 17 chars/s target, 20 hard | same | lint | [LS D2] |
| R-34 | Placement | Captions only in the reserved caption band, at a fixed position for the whole video | same | layout, `pre` | [LS D2]; Netflix TTSG |
| R-35 † | Emphasis | Sentence case always. A key term is coloured `mark` only on its first two mentions and never while the same term carries a cue on the board. Active-word emphasis is off by default (option: by opacity) | same | `kinetic.py` | Signalling consistency [LS D3]; all caps reads slower [SF §4.1] |

**Voice**

| Id | Rule | 16:9 | 9:16 Learning | 9:16 Social | Enforced | Evidence |
|---|---|---|---|---|---|---|
| R-36 † | Speech wpm (§1.4) | 155 (150–160) | 155 (150–160) | 170 (165–175) | calibration, lint `voice.rate` | Acceleration g = −0.41 [LS §7]; [RV L-6] |
| R-37 | Rate adjustments | Dense beats (formula/code/derivation) −10%; Indic narration −10 wpm; ceiling 185 wpm in any 10 s window; never time-stretch, re-synthesise instead | same | same | same | [LS E3]; [AU B9] |
| R-38 | Speakers | One primary narrator; Aadhi and "student" voices only in role; secondary speech ≤ 15% of narration time | same | same | lint `voice.secondary_share` | [LS §7] |
| R-39 † | Rotation | The narrator **speaker** differs from the last min(3, speakers − 1) in the teacher's history (§5.3), rotating inside the accent pool first; for en-IN audiences ≥ 2 of every 3 lessons use an Indian-English narrator by default; deterministic; pinnable | same | same | `casting.py` | Familiar accents are easier to understand (Major et al. 2002; Harding 2012) (unverified (M)); owner requirement 4 [RV L-20, T-9] |
| R-40 | Voice quality | Neural or HD voices only; no novelty or "cute" voices for narration | same | same | registry | [LS §7] |
| R-41 † | Loudness | Narration normalised per speaker to −16 LUFS integrated with one static gain per speaker per video, **baked into the scene audio asset** so player and MP4 match | same | same | `audio.py` | Edge voices span −16.9 to −22.0 LUFS [AU §1] (measured) [RV E-17] |
| R-42 † | Pauses | Between sentences 0.25–0.45 s; after a definition 0.6–0.9 s; segment boundary 1.0–1.5 s. TTS lead/tail silence is already trimmed to a 30 ms pad by `build_scene_audio` | 0.15–0.25 / 0.4–0.6 / 0.5–0.8 s | same | `audio.py` | [LS E4]; `audio.py:31` [RV E-30] |

**Music and SFX**

| Id | Rule | 16:9 | 9:16 Learning | 9:16 Social | Enforced | Evidence |
|---|---|---|---|---|---|---|
| R-43 | Instrumental only | No vocals, lyrics, choir or spoken samples (reject at intake) | same | same | `music.py` | Lyrics impair comprehension [LS §8.1] |
| R-44 † | Music level vs the narration stem | Under speech −22 LU (never closer than −20). Think time: under-speech level or off, never higher. Gaps ≥ 2.0 s, bumpers and speech-free bookends: −14 / −8 LU for **exposable** sources only; the procedural bed stays at the under-speech level. Dense scenes (formula, code, worked example): off with 1.5 s fades. Mode `gaps_bookends` (off under speech) is the default for dense lessons (≥ 30% dense runtime) | Under speech −22; gaps ≥ 1.5 s | Under speech −21; floor −20 | `mix.py`, `pre` | Music ducked 10 dB under narration had no effect in Phan 2023 (p = .53); only music under silent on-screen text helped; WCAG 1.4.7 20 dB rationale; changing-state sound hurts demanding tasks (Kämpfe 2011 [A]) [LS §8; RV L-18, T-20] |
| R-45 | Ducking | Deterministic envelope from word timings. Duck begins 300 ms before speech, 200 ms ramp; release 500 ms after speech, 700 ms ramp (16:9) or 400 ms (9:16); equal-power curves | same | same | `mix.py`, `pre`: momentary music level under speech varies ≤ 3 LU | Today's sidechain swings 24–26 LU [AU §1] (measured) |
| R-46 † | Tempo | 60–110 BPM | ≤ 110 BPM | ≤ 120 BPM | `music.py` | Fast + loud harms; fast instrumental pop reduced comprehension for low working-memory learners (Lehmann & Seufert 2017) [LS §8.1] |
| R-47 † | Rotation and structure | A library track never repeats within the teacher's last 10 lessons (§5.3); generated or procedural music is unique per video. **One bed per video**: arrangement (layers) changes at chapter boundaries; a new bed only at a `builds_on = false` boundary (2 s qsin crossfade) | One bed; layer changes at act boundaries | Layer changes at segment boundaries | `music.py` | Owner requirement 3 (design); [RV L-12] |
| R-48 | Master loudness | −14 LUFS integrated ±1, or −16 by destination (§2.6); true peak ≤ −1.0 dBTP on the encoded file; LRA ≤ 9 LU | same | same | `mix.py`, `pre`, `qa` | YouTube −14 reference; EBU R128 s2 [SF §2.2] |
| R-49 † | SFX | Only on chapter bumpers (whoosh), answer reveals (chime) and the end of a countdown (one soft tick). In explanation scenes an optional accent ≤ 1 per 20 s. **None** in formula, code or worked-example scenes. ≤ 1 s each; in word gaps; never within ±150 ms of a key term; −16 to −24 dB under narration. Render option "Sound effects: off" | Accent ≤ 1 per 8 s | Accent ≤ 1 per 8 s | `events.py`, `mix.py` | Moreno & Mayer 2000: added sounds and music reduced learning; Phan 2023's d = 1.66 harm was for music plus environmental sounds [LS §8; RV L-5] |
| R-50 | Claim-free | Every music track and SFX has recorded provenance and licence; nothing registered with Content ID | same | same | `music.py`, `pre` | [SF §1] |

**Colour and accessibility**

| Id | Rule | Both formats | Enforced | Evidence |
|---|---|---|---|---|
| R-51 † | Theme family per video | Skip any family used in the teacher's last min(4, eligible families − 1) lessons (§5.3) | `direct.py` | [CP §9.2] (design) |
| R-52 † | Structure colour rotates; content colour never does | `struct.accent` changes ≥ 30° at each chapter (16:9) or act (9:16) boundary, cross-faded 600–1000 ms, and colours **only structure**: bumpers, background tint (C ≤ 0.07 on content scenes), progress pips, card borders. Content (items, nodes, rows, bars, tiles, symbols, captions) uses only `content.*` tokens, which are fixed in every video. "Current vs past" is shown by weight, ink and outline, not hue | themes, source scan, validator | Colour coding needs a consistent mapping (Ozcelik 2009, unverified (M)); [LS §9]; [RV L-3] |
| R-53 | Contrast | Body text ≥ 4.5:1; text ≥ 32 px regular or ≥ 24 px bold ≥ 3:1; formula symbols and entity-coloured text ≥ 4.5:1; captions ≥ 7:1 target; graphics ≥ 3:1; colour never the only carrier of meaning | validator (CI), `pre`, `qa` (warning) | WCAG 1.4.1/1.4.3/1.4.11 |
| R-54 | Flash safety | ≤ 3 flashes in any 1 s; full-frame colour changes cross-fade ≥ 300 ms; no saturated-red flashes | transitions | WCAG 2.3.1 [MR §8] |
| R-55 † | Low-salience backgrounds | Motion period ≥ 15 s; ≤ 2% of frame width per second; behind-text luminance ratio ≤ 1.5:1; no particles behind text; saturation below the accents. **Frozen and desaturated by 30%** while `hold_reason` is set or in `formula_focus`, `worked_steps` and `code_walkthrough` (600 ms ease) | backgrounds, `pre` | Persistent vs transient details [LS I3]; [RV L-23] |
| R-56 | Colour budget | ≤ 5 theme colours plus neutrals; ≤ 3 hue families on screen at once | themes | [CP §9.1] |

**Media, safe zones and export**

| Id | Rule | Both formats | Enforced | Evidence |
|---|---|---|---|---|
| R-57 † | Inserted clips | Warn when an overlay is > 30 s (16:9) or > 12 s (9:16), when inserted footage is > 40% of runtime, or when a clip has speech and no captions. Inserted time never counts against R-01 except for the platform-safe container limit | `ui`, lint `clip.*` | Seductive details [LS J1]; [RV T-3] |
| R-58 | Licence on file | Every asset has licence metadata; stock credits are displayed; platform page URLs are refused | ingest, `pre` | [ED §4] |
| R-59 † | Safe zones | **16:9:** critical content in x 96–1824, y 54–912; caption band y 924–1032. **9:16:** critical text x 192–888 for y 288–840; x 120–780 for y 840–1248; caption band x 120–780, y 1040–1240 | layout, `pre` text boxes | Intersection of YouTube, Meta and TikTok zones [SF §3]; [RV T-4] |
| R-60 | Export | §2.8 specification | `qa` | [SF §2] |

---

## 2. Formats

### 2.1 Presets and reel profiles

| | **Lesson** (16:9) | **Reel** (9:16) | **Reel, platform-safe** (9:16) |
|---|---|---|---|
| Canvas (logical = output) | 1920×1080 | 1080×1920 | 1080×1920 |
| Frame rate | 30 fps CFR | 30 fps CFR | 30 fps CFR |
| Length (generated runtime) | 5:00 target (4:30–5:30), max 6:00 | 3:30 target (3:00–4:00) | content ≤ 178.0 s, container < 179.5 s |
| Narration words (Learning / Social) | 540–620 | 410–480 / 460–530 | 350–400 / 390–430 |
| Speech wpm (§1.4) | 155 | 155 / 170 | 155 / 170 |
| Structure | 3 chapters × 60–90 s | 4 acts; 5–7 micro-segments × 20–40 s | 4 acts; 4–5 micro-segments |
| Checkpoints | 1 check per chapter (2–3) + recap | Mid-reel check + end check + recap | same as Reel |
| Aadhi | Intro 5–8 s; recap 6 s (on by default) | Intro 5–8 s; recap 5 s (off by default) | same as Reel |
| Captions | By destination (§2.6) | Burned phrase chunks + sidecars | same |
| Music under speech | −22 LU (or off in `gaps_bookends`) | −22 / −21 LU | same |
| Where it plays well | YouTube, LMS, classroom projector, WhatsApp/Drive | TikTok, Facebook, LinkedIn, Instagram followers; on YouTube it is a regular vertical video, **not a Short** | YouTube Shorts, Instagram Reels, everywhere; or "Export as 2 parts" (§2.7) |

**Word budgets.** Speech time is generated runtime minus planned silences: think time, bumpers, holds, "Your turn" holds, `pause_after`, the sonic logo and the outro tail. For a 5:00 lesson that is about 300 − 78 = 222 s, giving ≈ 573 words at 155 speech wpm; for a 3:30 Learning reel about 210 − 38 = 172 s, ≈ 444 words. `plan_rules` budgets words per scene from these numbers. `depth` moves the budget by −10% (overview) or +10% (deep) inside the band, never outside R-01 (§6.3).

**Reel profiles** [RV L-6]. A 9:16 reel combines fast speech, burned captions, frequent events, music and SFX; LS found no learning study of that combination [LS §3.3]. The default is therefore the **Learning** profile. **Social energy** is opt-in, labelled "untested for learning", and is measured in the B9 pilot before it could become a default (Q14).

| | Learning (default) | Social energy (opt-in) |
|---|---|---|
| Speech wpm | 155 (150–160) | 170 (165–175) |
| Event cadence (R-15) | median 2.5–4 s, max static 6 s | median 1.5–3 s, max static 4 s |
| Music | −22 LU under speech, ≤ 110 BPM, layers change at act boundaries | −21 LU, ≤ 120 BPM, layers change at segment boundaries |
| SFX | accents ≤ 1 per 8 s | same |
| Accent and background | change per act | change per segment |
| Captions | phrase chunks, no active-word emphasis | phrase chunks, active word by opacity |

**Why the reel default stays at 3–4 minutes.** The owner asked for 3–4-minute reels. Over 3:00 a vertical video is not a Short on YouTube, and Instagram reach beyond 3:00 is uncertain [SF §1, CP §3]. The Studio offers **Platform-safe ≤ 2:58** and **Export as 2 parts** as one-click alternatives with a plain-language note (Q1).

### 2.2 Coverage, scope and series

A 5-minute lesson holds about 575 spoken words. Classic lectures default to 15 minutes (`GenerationOptions.target_minutes = 15`, `aadhi/pipeline/base.py:76`), and SME scripts are written for that. Squeezing such a source into one lesson would drop concepts or thin the explanations, against R-61 [RV L-1, T-6].

**Scope check** (`aadhi/pipeline/scope.py`, deterministic, runs right after `brief`):
1. Estimate the words each essential concept needs from the brief: `45 + 25 × |must_explain| + 12 × |key_facts| + 110 × [the concept has a worked example]`, plus a fixed overhead for intro, checks and recap (150 words for a lesson, 100 for a reel). The coefficients are calibrated in B4 against the three eval fixtures and stored with a version.
2. If the estimate is within the format budget × 1.15, generation continues.
3. Otherwise the job pauses in the existing `awaiting_review` status (the mechanism `review_plan` uses) with a **scope proposal**, and the Studio asks the teacher (§9.3):
   - **Make a series** of N lessons (N = ⌈estimate ÷ budget⌉, at most 6). Concepts are split in teaching order, keeping each concept with its prerequisites; each part lists its concepts and "Part n of N".
   - **Focus on…**: a concept picker; deselected concepts are recorded as deliberately left out.
   - **Make one condensed lesson anyway**: allowed, with the coverage report stating what is left out. Worked examples are never thinned to fit; whole concepts are left out instead.
4. With "Let me review the plan" off and no answer needed, nothing pauses.

**Series.** A series is N projects sharing `Project.settings["series"] = {id, title, index, count, concepts}`; no migration is needed because `settings` is already a JSON column. The current job continues as Part 1. The other parts are created as projects with their concept lists and a **Generate** button each, or all queued at once if the teacher chooses "Generate all parts now" (each part is a normal billed generation; the estimate shows the total). Every part after the first receives a one-sentence "Last time…" bridge from the previous part's concept list. Rotation runs across the series as for any lessons (§5.3), unless the teacher pins voice or look "for the series".

**Coverage report.** Every lesson's source report lists each essential concept as *taught*, *in part n*, *left out by you* or *left out to fit*. Lint `coverage.dropped` (§6.9) is an **error** when an essential concept is missing without one of those dispositions; it is teacher-facing with the actions "Make a series", "Choose focus" and "Accept".

### 2.3 Segment blueprints

**16:9 lesson, 5:00** [RV L-14, L-15, L-25]:

| Block | Time | Templates (§4.3) | Notes |
|---|---|---|---|
| Aadhi intro | 0:00–0:07 | `aadhi_intro` (one of 4 layouts) | Sonic logo 0.0–0.8 s; Aadhi greets (rotating pool), names the session and asks the hook. His line ends, then the brand wipe |
| Key terms | 0:07–0:19 | `key_terms` | 2–4 component terms, ≈ 3–4 s each, each with an icon |
| Chapter 1 | 0:19–1:35 | `chapter_bumper` (with mini-roadmap of all chapters) + 3–5 scenes; `predict` before the scene that explains the outcome | First new insight by 0:30. Ends with a check (type from the seeded order) |
| Chapter 2 | 1:35–2:55 | `chapter_bumper` + 3–5 scenes; `worked_steps` if quantitative (last step "Your turn") | Ends with a check |
| Chapter 3 | 2:55–4:10 | `chapter_bumper` + 3–5 scenes; `myth_fact` if the plan has misconceptions; a faded second example if quantitative | Ends with a check or `takeaway_stamp` |
| Aadhi recap (optional) | 4:10–4:16 | `aadhi_recap` | Left clip, prompt card on his gesture side. His line ends and he exits before any think time |
| Retrieval recap | 4:16–4:50 | `recap_retrieval` | 2 questions (≥ 1 apply/transfer), free recall of the big ideas (≥ 6 s), then 3 relational big-idea tiles |
| Outro | 4:50–5:00 | `outro_next` | Next step (or "Part n+1" in a series). Optional 15 s end-screen space |

There is no separate roadmap scene: the first bumper carries the mini-roadmap, which saves 4–7 s before the first insight. `roadmap` remains available for 4-chapter lessons.

**9:16 reel, 3:30** (one concept per reel; each micro-segment is one step of it) [RV L-26, L-27]:

| Act | Time | Templates | Notes |
|---|---|---|---|
| Hook | 0:00–0:11 | `aadhi_intro` (portrait), then a 3–5 s promise (`kinetic_statement`) | Hook headline fully set on **frame 0**, the feed cover. Aadhi says the hook first (≤ 8 words, ends by 3.0 s), then a sign-on of ≤ 5 words |
| Build 1 | 0:11–1:40 | 2–4 micro-segments: micro-hook (≤ 3 s), 2–4 scenes, then a "remember this" card or a `predict` | Accent and background stay for the act |
| Mid-reel check | ~1:40 | `check` (free recall or single answer) | Required, also in platform-safe |
| Build 2 | 1:45–3:00 | 2–3 micro-segments | New accent and background at the act boundary |
| Check | 3:00–3:30 | `aadhi_recap` (optional), `recap_retrieval` (1 free-recall question + 3 relational keywords), `outro_next` | The last spoken line leads back into the opening question, for a seamless loop [SF §6.3]; the sonic logo plays on the outro |

**Platform-safe 2:58** keeps the four acts with 4–5 micro-segments and **both** checks.

**One thread per reel.** A reel teaches one concept, never a montage of unrelated facts. Rapid unrelated context switching impaired prospective memory in Chiossi et al. 2023 [LS §3.3].

### 2.4 Pacing

Pacing is enforced by R-15 to R-24. Two kinds of change [LS §4.2]:

- **Micro-changes inside a concept**, always tied to a narrated word: a reveal, a `cue.term`, a `cue.look_here` (including a camera push onto the named part), an arrow drawing, a value animating, a worked-example step. Only these *anchored events* count for R-15. Dense content (definitions, derivations, code) slows to one change every 4–5 s [SF C11; Lang, Potter & Bolls 1999].
- **Macro-changes between concepts**: a new structure accent, a stylised transition, a music arrangement change, and (rarely, R-18) a new background preset. They happen at chapter boundaries (16:9) or act boundaries (9:16 Learning), never mid-derivation or in the 2 s before a reveal.

**Exceptions to the static limit.**
- A planned hold may run to 12 s (16:9) or 8 s (9:16): formula study, code reading, diagram study, think time and "Your turn".
- During a hold a cue must move through the content or a countdown ring must run (R-16), and the background is frozen (R-55).
- Background drift, decorative camera moves and Ken Burns on an unnamed photo never count as events. A playing video counts as motion, so it is not static.

### 2.5 Safe zones and layout grid

**16:9 (1920×1080):**

```
x=0      96                                                    1824     1920
y=0   ┌───────────────────────────────────────────────────────────────────┐
y=54  │   ┌───────────────── critical content box ───────────────────┐    │
      │   │ 12 columns, 24 px gutters; templates use named regions:   │    │
      │   │ full | left-55 + right-45 | centre-60 | top-band + stage  │    │
y=912 │   └───────────────────────────────────────────────────────────┘    │
y=924 │        ┌────────── caption band (2 lines × 52 px) ──────────┐       │
y=1032│        └────────────────────────────────────────────────────┘       │
y=1080└───────────────────────────────────────────────────────────────────┘
```

- **Margins.** 96 px left and right, 54 px top. The bottom 130 px is kept free of essential text because the YouTube progress bar, controls and CC draw there [SF D19].
- **End screen.** The last 15 s keeps a clean right half when "end-screen space" is on [SF §8].

**9:16 (1080×1920)**, the intersection of the YouTube, Meta and TikTok zones [SF §3], revised after measuring real text with the vendored Inter Bold [RV T-4, T-16]:

```
y=0     ┌────────────────────── 1080 ──────────────────────┐
        │ platform header — background and decoration only │
y=288   ├──────────────────────────────────────────────────┤
y=300   │   progress pips "2/5" + act label (40 px)        │
y=340   │   HOOK band (intro, hook only): ≤ 4 lines,       │
        │   88–104 px auto-fit, text x 192–888             │
        │   — or — 1-line HEADLINE (y 340–440) + STAGE      │
y=460   │   VISUAL stage y 460–1040 (580 px); "tall" variant│
        │   without headline: y 340–1040 (700 px).          │
        │   Shapes may span x 60–1020; text x 192–888 above │
y=840   │   y 840, x 120–780 below (TikTok action rail)     │
y=1040  │   CAPTION band x 120–780 (660 px), fixed position │
y=1248  ├──────────────────────────────────────────────────┤
        │ platform UI zone — background, media, Aadhi's    │
        │ lower body; nothing that must be read            │
y=1920  └──────────────────────────────────────────────────┘
```

- **Constants** in `aadhi/compose/dynamic/zones.py` (versioned): `YT_SHORTS = (288, 672, 48, 192)`, `META_REELS = (269, 672, 65, 65)`, `TIKTOK = (240, 660, 120, 120, rail=(300, y ≥ 840))`, and `UNIVERSAL` = their intersection. Below y 840 the intersection is x 120–780, which is why the caption band uses it.
- **Measured fit.** At 96 px, "Why doesn't the Moon fall down to Earth?" (8 words) wraps to 4 lines in the 696 px column (≈ 403 px tall), and "Can a wire stop current?" to 2 lines. The hook band therefore holds 4 lines; the old 220 px band held 2. At 60 px, "Voltage equals current" measures 670 px and does not fit one 660 px caption line, so captions wrap by measurement, never by character count (§4.6).
- **Captions keep one position** for the whole video [R-34]. Moving them between the top and bottom per template, as the review suggested, would make viewers search for them; the visual gains its room from the one-line headline and the tall variant instead (Appendix C, T-16).
- **Inserted 16:9 clips** shown `contain_blur` sit at y 360–968 (1080×608) with captions below on the blur, so captions never cover the footage. *As built (B1), hand-over to B2/B6:* the stage's `MediaView` (`web/js/dynamic/media.js`) always centres the medium in its box and has no anchor or position option, so this layout (a full-frame blur with the clip placed at y 360–968) needs one first: an object position, or a separate medium rect inside the backdrop's box.
- **Studio overlay.** The preview can overlay the zones (§9.6).
- *As built (B2a):* `aadhi/compose/dynamic/zones.py` (`ZONES_VERSION` 1) is the one source of these numbers, and the generated catalogue (§4.7) carries them to the stage: `core.js` `ZONES` is built from it and equals B1's literal value for value, and preflight's bands and type floors come from it with B1's values and types. **16:9:** the 12-column grid covers x 96–1824, y 54–912 (122 px columns, 24 px gutters); the named regions are `full` (the critical box), `left-55` x 96–1034, `right-45` x 1058–1824, `centre-60` x 442–1478 (all y 54–912), `top-band` y 54–352 and `stage` y 376–912 (both x 96–1824); the end-screen half is x 960–1920 for the last 15 s. **9:16:** `hook` x 192–888, y 340–760; `headline` x 192–888, y 340–440; `stage` x 60–1020, y 460–1040; `tall` x 60–1020, y 340–1040; `text-above` x 192–888, y 288–840; `text-below` x 120–780, y 840–1248; `clip` x 0–1080, y 360–968. The platform tuples are read as (top, bottom, left, right, rail); `UNIVERSAL` = (288, 672, 120, 192, a 300 px rail from y 840), whose safe boxes are x 120–888 above y 840 and x 120–780 below; R-59's text column x 192–888 above y 840 is that box made symmetric about the centre. Two readings are A2's, not this design's (pending the owner's review): `left-55`, `right-45` and `centre-60` are whole-pixel fractions of the critical width around a centred 24 px gutter rather than grid spans (55% of 12 columns is 6.6 columns), and `top-band` / `stage` are B1's header and title rows.

### 2.6 Caption policy and "Where will students watch?"

YouTube ignores embedded `mov_text` tracks, and browser and LMS players generally need a sidecar file, so a soft track alone leaves most classroom viewers without captions [RV L-9, T-10] (player behaviour: unverified (M), tested in B4). The form therefore asks one question, **"Where will students watch?"**, which sets the caption, loudness and packaging defaults. The teacher can still change each one in the render dialog.

| Destination | Primary file | Captions | Loudness | Also |
|---|---|---|---|---|
| **WhatsApp, Drive or classroom** | burned | burned in | −14 LUFS | — |
| **LMS** (Moodle, Classroom, …) | burned | burned in + `.srt`/`.vtt` to upload | −16 LUFS | — |
| **YouTube** | clean | `.srt` to upload + soft track (default-on disposition) | −14 LUFS | end-screen space (16:9), description block, platform-safe suggested for reels |
| **Our lesson page** | clean | drawn by the player, on by default | −16 LUFS | — |

**Both versions.** "Also export the other version" produces the second file without a second capture: the motion engine captures the clean stage once, the caption layer is captured as transparent states at each chunk change by the same caption code (≈ 150 states for 5 minutes), and ffmpeg overlays them in one extra encode pass (§7.5). When burned is the only file, captions are drawn in the captured page directly.

| | 16:9 | 9:16 |
|---|---|---|
| Source | Script text with TTS word timings, never ASR | same |
| Chunking | Phrase captions, ≤ 42 chars/line, ≤ 2 lines, ≥ 1.0 s | Phrase chunks ≤ 32 chars, ≤ 2 lines measured to fit 660 px at the chosen size, 1.5–3.0 s (≥ 1.0 s hard) |
| Case | Sentence case | Sentence case. Upper case only for a single emphasised word of ≤ 2 words |
| Size | 48–54 px, weight 600, on a pill at 70–80% opacity | 56–64 px, weight 700, outline or pill; a single long word may shrink to ≥ 52 px |
| Emphasis | A key term in `mark` on its first two mentions, never while it carries a board cue | same; active-word emphasis off by default |
| Redundant text | The band is blank while on-screen text matches the narration (≥ 70% token overlap) | same |
| Line breaks | Netflix rules: bottom-heavy; never split article + noun, number + unit, or auxiliary + verb [SF §4.1] | same |

These replace the classic burn-in path for Dynamic videos. The classic tab stays 16:9 and unchanged.

### 2.7 Platform limits, platform-safe and two-part export

| Platform | Vertical limit (Oct 2026) | What a 3–4-minute vertical video becomes | Grade |
|---|---|---|---|
| YouTube Shorts | Vertical or square uploads ≤ 3:00 are Shorts automatically. A Short over 1 min with **any** Content ID claim is blocked worldwide | Over 3:00: a regular long-form video, not in the Shorts feed | moderate [SF §1] |
| Instagram Reels | 3:00 for most accounts (Jan 2025); longer uploads rolling out to some | Reach beyond 3:00 is unverified | practice [SF §1] |
| TikTok | 10 min in-app, up to 60 min uploaded | A normal TikTok | practice |
| Facebook | No limit; every video is a Reel | Fine | moderate |
| LinkedIn | 15 min, aspect ratio 1:2.4 to 2.4:1 | Fine | moderate |

- **Platform-safe** caps content at 178.0 s, leaving room for AAC priming and padding; preflight refuses a container of 179.5 s or more after the fit step (§6.4) [SF §1]. Every music track must be claim-free (R-50).
- **Export as 2 parts for Shorts/Reels** [RV T-15]. The reel is cut at the micro-segment boundary nearest the midpoint. Part 1 ends with a 1.5 s "Part 2 next" card; Part 2 opens with a 1.5 s "Part 2" card built from that segment's micro-hook (R-22). Each part must be ≤ 2:58; otherwise the option is disabled with the reason. Because chunks are scene-aligned, most chunks are reused; only the two cards and the two audio mixes are new.
- The limits changed five times between 2024 and 2026, so B9 re-checks them every quarter [SF K].

### 2.8 Export specification

| Item | 16:9 lesson | 9:16 reel |
|---|---|---|
| Resolution | 1920×1080 | 1080×1920 |
| Frame rate | 30 fps CFR | same |
| Video | H.264 High, yuv420p, closed GOP of 15 (`-g 15 -keyint_min 15 -sc_threshold 0`, `open-gop=0`), `-bf 2`, BT.709 primaries, transfer and matrix in TV range via `setparams`. Preset and CRF from the B1 benchmark (default `veryfast`, CRF 17, `-maxrate 12M -bufsize 24M`; §7.5) | same |
| Container | MP4 with `-movflags +faststart+negative_cts_offsets`, so video and audio both start at 0 (measured: with `-use_editlist 0` alone, video started at 0.0667 s; with the flag, ffmpeg 8.1 still writes one `elst` edit list per track, each mapping its track to 0, so the check is on the decoded start times, not on the absence of an edit list) [RV E-6] | same |
| Audio | AAC-LC, 48 kHz stereo, 384 kbps | same |
| Loudness | −14 or −16 LUFS ±1 by destination; true peak ≤ −1.0 dBTP on the encoded file; LRA ≤ 9 LU | same |
| Captions | By destination (§2.6); `.srt` + `.vtt` always | Burned + `.srt` + `.vtt` |
| Chapters | ffmetadata chapters + a YouTube description block (≥ 3 chapters, each ≥ 10 s, starting 00:00) | Description block optional |
| Thumbnail | A designed 1280×720 frame (`thumbnail` stage template: title, key visual, theme colours) plus a title and description draft [RV T-26] | Cover = frame 0 at 1080×1920; the reel card's position is seeded so covers differ |
| Credits | `credits.txt`: voices, music, stock clips with creators, licences, and the AI disclosure line (§5.7) | same |
| Draft | 1280×720, `veryfast`, CRF 23 | 720×1280 |

The specification follows YouTube's recommended upload settings and Meta's guide [SF §2.1]. Today's encoder already does BT.709, `+faststart`, CFR and 48 kHz.

---

## 3. Aadhi: intro and recap only

### 3.1 Rules

The rules are R-12 to R-14 (§1.5).

- **Aadhi's job is social and motivational framing.** He welcomes the viewer, names the topic, poses the hook and opens the recap. Explaining is not his job: instructor-presence meta-analyses show motivation gains but no reliable learning gains, and a visible presenter pulls gaze from the relevant visuals [LS §10].
- **When he is on screen, he signals.** His gesture opens toward the title or prompt. Agents that gesture toward content are the ones that help, g = 0.28–0.44 [LS §10].
- **He is gone before anyone has to think.** No think time, countdown or quiz runs while his clip plays [RV L-4].
- **Lint.** `aadhi.misplaced` is an **error** for generated screenplays when the presenter appears anywhere except the opening presenter scene and the recap scene. For teacher edits it is a **warning**: inserting a clip before the intro or reordering scenes is the teacher's choice [RV E-24]. `aadhi.too_long` is an error when an appearance or the total exceeds R-12, measured from TTS word timings.

### 3.2 What the existing assets allow

The clips are in `video_template/*_clean.mp4`, with posters in `video_template/posters/`.

| Asset | Facts | Use in Dynamic mode |
|---|---|---|
| `aadhi_left_clean.mp4` | 1280×720, 24 fps, exactly 8.000 s [CB §2 #24]. Full-body Aadhi at the left (≈ x 90–450 of 1280), open-arm presenting gesture toward the right, plain white studio wall, empty wall on the right 60% | **All four 16:9 intro layouts** and the **16:9 recap** |
| `aadhi_center_clean.mp4` | Same format; Aadhi centred (≈ x 470–835), full body, same studio; no gesture toward a side | **9:16 intro and recap** (square card) |
| `aadhi_popup_clean.mp4` | Head-and-shoulders over a whiteboard of AI-generated **pseudo-equations** | **Never.** Engineering students would read the board as wrong maths |
| `aadhi_right_clean.mp4` | A horizontal mirror of the left clip: the "R" crest reads backwards | **Never.** The brand mark is mirrored |
| `logo_animation_clean.mp4` | 10 s | Not in the intro. Its last frame supplies the logo bug for the outro |
| `bgm.mp3` | 80 s, −9.7 LUFS, one track for every video [AU §1] | Not used by Dynamic mode (§5.8) |

**Scaling.** 24 fps clips play in a 30 fps output by showing source frame `floor((t − t0) × 24)`, a 1-1-1-2 cadence, acceptable for ≤ 8 s [MR §4.3]. 720 → 1080 is the 1.5× upscale the classic mode already ships; the 9:16 card is a 1.03× scale of a 720² crop. A full-height 9:16 crop (2.67×) is never used.

### 3.3 Compositions

**Timing rule for every Aadhi scene** [RV E-21, L-22]. Aadhi's speech start is fixed (0.8 s in 16:9 after the sonic logo, 0.15 s in 9:16). The exit time is **derived**: `t_exit = last Aadhi word end + 0.3 s`. The scene length is `t_exit + exit duration`, clamped to [5.0, 8.0] s. If his speech would end later than 7.1 s, `aadhi.too_long` asks for a shorter line (fixable: the greeting is shortened first). If it ends early, the title holds until 5.0 s. The clip never needs to loop or freeze, because 8.0 s is the clip length.

**Greeting and sign-on lines** come from vetted pools in `aadhi/pipeline/aadhi_lines.py` (per language, e.g. "Hi, I'm Aadhi!", "Welcome back!", "Ready? Let's go."), rotated per teacher excluding the last 2 used. The LLM writes only the session naming and the hook. Pool lines count toward the word budget [RV T-8].

**16:9 `aadhi_intro`** (default ≤ 7.0 s). One of four layouts is seeded per video, excluding the teacher's last used. All four put the title on the side Aadhi's gesture opens to (his right, our right), so none of them points away from the content [RV T-8, L-21].

| Layout | Composition |
|---|---|
| `fullframe` | The left clip fills the frame (1.5×). Title stack on the studio wall at x 860–1824 |
| `card_left` | Theme background. A 720×720 crop of the left clip (source x 0–720) as a card at x 96–816, y 180–900, 40 px radius. Title stack at x 880–1824 |
| `pip` | Theme hook background with the hook question large at x 640–1824. A 480×480 card of the same crop at x 96–576, y 432–912 |
| `split` | Source x 0–576 scaled 1.5× fills x 0–864. A colour-block panel at x 864–1920 carries the title |

*Title stack:* session chip (subject · unit · session, 36 px), title (display face, 96–120 px, ≤ 2 lines), hook question (60–64 px, the keyword with `cue.term`). Logo bug at the top right at 60% opacity. On the studio wall, ink and accent come from a fixed `studio` surface token; B2 measures the wall's luminance over all 192 frames in the title region so the validator checks the worst case.

*Choreography.*

| Time | What happens |
|---|---|
| 0.0 s | Clip and chip visible; the sonic logo (≤ 1 s, the only constant brand element) plays at the speech-free level and ends before speech |
| 0.15 s | Title words rise 24 px with a 70 ms stagger (`ease.enter`, 450 ms) |
| 0.8 s | Aadhi speaks: greeting, session, hook (≤ 16 words) |
| Hook spoken | The hook question appears word by word with his words; the caption band stays blank for those words (R-31) |
| `t_exit` | **Brand wipe**: a colour block in the chapter-1 accent sweeps across (`ease.hero`, 600 ms) with a whoosh |

**9:16 `aadhi_intro`** (5.0–8.0 s).
- **Frame 0** already shows the background, the session chip (y 300–340) and the **hook headline fully set** in the hook band (y 340–760), auto-fitted to ≤ 4 lines at 88–104 px by measured width, ≤ 8 words. `portrait.hook_overflow` fires before TTS if it cannot fit at 88 px [RV T-4].
- The centre clip is cropped to 720×720 and shown 1.03× as a **744×744 card** at y 800–1544. Its x position is seeded per video (x 168–912, 96–840 or 240–984) so feed covers differ [RV T-8]. The card's lower part sits in the platform UI zone, which only needs Aadhi's body, nothing to read.
- Aadhi says the **hook first** (≤ 8 words, ending by 3.0 s by word timings), then a sign-on of ≤ 5 words, with no "Hi, I'm Aadhi" opener [RV L-21, T-17]. Captions are blank for the hook words and show the sign-on on a pill over the card.
- The card settles from 0.96 to 1.0 over 400 ms and exits at `t_exit` by sliding down (`ease.exit`, 300 ms) while the next scene's iris opens. No sting plays before speech; the sonic logo plays on the outro.
- *Known deviation:* the centre clip does not gesture toward the headline above it. The hook is the only text on frame 0, so the gaze target is unambiguous; B8's native vertical clips add an upward pointing gesture (Q17).

**16:9 `aadhi_recap`** (default 6.0 s, 5–8 s).
- The 720×720 left-clip card at x 96–816, y 180–900. On the right, on his gesture side (x 880–1824), a prompt card: "Quick check: what are the 3 big ideas?" with three empty slots and **no countdown** [RV L-4, L-21].
- Aadhi says ≤ 12 words. At `t_exit` the card exits; `recap_retrieval` then starts the free-recall think time with Aadhi gone and the music at the under-speech level.

**9:16 `aadhi_recap`** (default 5.0 s, off by default). The centre-clip 744×744 card at y 800–1544, the prompt in the hook band, ≤ 10 words.

**Speakers.** Aadhi's lines are beats with `speaker = "aadhi"` (§5.5), spoken in the fixed Aadhi brand voice chosen in Settings → Brand (Q4). The rotating narrator takes over from the next scene and never calls itself Aadhi.

**No classic intro.** Dynamic timelines have no `IntroSpec`: no 5.5 s logo animation and no title cards. Frame 0 is a designed frame; session details are on the chip and in the MP4 metadata.

### 3.4 Disabling Aadhi

The recap appearance is a toggle (on by default in 16:9, off in 9:16). The intro appearance cannot be turned off in Dynamic mode, by the owner's requirement 1. An administrator setting `DYNAMIC_AADHI_INTRO=false` exists only for white-label or test deployments; `aadhi_intro` then falls back to `hook_question`.

### 3.5 Asset upgrade (batch B8, recommended; Q5)

Two usable clips cannot give every video a different opening, and neither can point upward in 9:16 [RV T-8]. B8 is therefore recommended rather than optional:
1. **Alpha-matted Aadhi** for the centre and left clips, so he can stand on any theme background. One-time job reviewed by a person. The matting tool must permit commercial use: BiRefNet is believed to be MIT-licensed (verify); BRIA RMBG weights are non-commercial, so avoid them. Store as an H.264 colour and matte pair (alphamerge in ffmpeg; a canvas composite in the page).
2. **Native clips:** 30 fps, ≥ 4 gestures (point right, point up, open arms, "thinking"), plus native 1080×1920 versions.
3. **Regenerate or retire `popup`** with a clean or correct board.

### 3.6 The Aadhi presenter tab: kept intact

- **What it is.** The existing generation is the **Aadhi presenter** tab: the current form, prompts, `canonicalize` defaults (`_DEFAULT_MASCOT`), `presenter_lint`, the classic timeline (mascot clips, logo and title-card intro, single BGM), and the state-screenshot renderer (`compose/video.py`).
- **How it stays unchanged.** `GenerationOptions.video_style` defaults to `"aadhi"` and is left out of the JSON at that default, so the API keeps today's behaviour for old clients and for `regenerate`. Only the Studio's Dynamic tab sends `"dynamic"`.
- **Invariance harness** (added in B0, blocking in every batch) [RV E-15]. Rendered pixels depend on the Chromium build and OS, and MP4 bytes on the x264 build, so a stored frame-hash list cannot be compared across machines. The harness has two parts:
  1. **Platform-independent goldens** for `tests/core/fixtures/showcase_ohms_law.json` without `presentation` and for `tests/invariance/fixtures/classic_kitchen_sink.json` (every classic scene type, hidden and held scenes): the screenplay JSON, every `scene_hash`, the built and preview timeline JSON, the default `GenerationOptions` JSON, and every ffmpeg command line of the MP4 render (filter graphs and side files; state-PNG digests replaced by state ids, paths by `<branding>` / `<asset:KEY>`). As built: `tests/invariance/` (README there).
  2. **Differential pixel test** in the Docker `test` image: render the fixture at the merge base and at HEAD in the same container and compare the decoded output. A difference beyond the measured tolerances below fails, unless the PR carries the `classic-change` label (the batch declares it and the review approves an intended classic change); the differential is then reported, not blocking.
  3. **Tolerances** (measured in B0). The classic renderer is not bit-stable even against itself: Chromium's antialiasing of a few rounded panel corners varies by up to 15 pixels and 7 levels per screenshot, x264 then spreads tiny differences over the following frames (no pixel above 14 levels, mean difference up to 0.005), and the ducked music tail varies in its last 0.1 s. So "any difference fails" cannot hold, and `tests/invariance/pixeldiff.py` allows: state screenshots compared losslessly with at most 16 pixels differing by more than 12 levels (a state on one side only fails); decoded frames with at most 16 pixels differing by more than 24 of 255 and a mean difference of at most 0.05; audio with no sample differing by more than 2, the last 0.5 s reported but not judged. A changed glyph or a 1 px shift still fails; `--exact` requires identical output.
- **Schema pattern.** Every new field uses the established `exclude_if` default pattern (`hidden`, `min_seconds`, `variant`, `sync_cues`) [CB §0.1].
- **No reflow between modes.** A Dynamic screenplay opened in the presenter tab is not reflowed. Switching style means "Remake as …", which makes a new version through the pipeline (§6.10).

---

## 4. Visual system

### 4.1 Principles

1. **Signal, don't decorate.** Motion goes to things that carry meaning: reveals timed to words, the two cues, arrows, step builds, count-ups. Decorative animation has no learning effect (g = −0.05); representational animation helps (g = 0.40); persistent decoration hurts (g = 0.43 for removing it) [LS §2, §9].
2. **Variety in the container, never in the code** [LS §1.12]. What varies: background, layout variant, theme family, display face, motion personality, caption style, voice, music, hook kind, intro layout. What never varies inside a video: the content colours and the two cue styles learners rely on.
3. **Variety never changes meaning.** A template makes a claim about its content (contrast, sequence, completion…). The art director may only swap templates that make the same claim (same semantic class), and never replaces a representational visual with typography alone [RV L-2].
4. **Change at boundaries, keep context while an idea is built** [LS §4.1]. The structure accent changes at chapter or act boundaries; the background preset usually stays for the whole lesson (R-18); a shared visual anchor carries across boundaries that continue the same argument [RV L-12].
5. **Bounded themes.** A theme is a bundle (palette + faces + personality + caption style + SFX pack + music mood) that swaps as a unit, as Pictory, Synthesia and Vyond do [CP §4].
6. **Everything is a pure function of time.** Every visual property is computed from `(scene, t)` and the timeline; the same code drives the live player and the MP4 (§7.4).

### 4.2 Template contract

A **scene template** is a typed layout plus choreography. It renders one scene from the screenplay's existing data.

| Side | File | Contents |
|---|---|---|
| Python | `aadhi/compose/dynamic/templates.py` | One `TemplateSpec` per template: `id`, `formats`, `semantic_class`, `representational: bool`, `variants`, `forced_for` (scene types it must render), `fit(scene, fmt) -> float` (0 = cannot render), `duration=(min, max)` per format, `events(scene, timed) -> list[VisualEvent]`, `holds` (allowed `hold_reason`s), `release` (`R1`, `W2`, `B6`) |
| JS | `web/js/dynamic/templates/<id>.js` | `export default {id, build(ctx), update(ctx, t, state), focusRect(ctx, targetId)}`, where `ctx = {scene, look, roles, cues, zones, format, rng}` |

- **Who decides what.** Python decides *when* (it writes `TimedScene.events`, which the lint checks); JS decides *how*.
- **Colours by role only.** Templates get colours from `ctx.roles.color(role)` with roles from a closed list (`ink`, `ink2`, `surface`, `entity:<n>`, `warn`, `ok`, `mark`, `focus`, and the structure roles `struct.accent`, `struct.tint`, `struct.border`, `struct.pip`). Structure roles may be used only on structure elements; a source-scan test (§7.4) fails a template that colours an item, node, row, bar, tile, symbol or caption with a structure role [RV L-3].
- **Cues only through `ctx.cues`.** `cues.term(...)` and `cues.lookHere(...)` (§4.8) are the only emphasis treatments; the source scan rejects any other highlight code in templates [RV L-11].
- **Parity test.** The Python and JS id lists, classes and variants match.
- **Data.** Templates read existing fields (`BoardItem` kinds, `SidePanel` kinds, scene types) and the five item kinds of §6.7.
- "Beat" means the narration beat that reveals an item. Reveals start 120 ms before the anchor word (R-17).

**Semantic classes.**

| Class | Claim | Templates |
|---|---|---|
| `structure` | Orientation, not content | `aadhi_intro`, `aadhi_recap`, `chapter_bumper`, `roadmap`, `outro_next`, `thumbnail` |
| `prompt` | "Think about this" | `hook_question`, `predict`, `check`, `recap_retrieval` |
| `definition` | "X means Y" | `key_terms`, `definition_card` |
| `statement` | One principle in words (typography only) | `kinetic_statement`, `takeaway_stamp`, `quote_card` |
| `enumeration` | Unordered members of a set | `list_build` |
| `contrast` | A differs from B | `versus` |
| `correspondence` | A maps to B (analogy) | `analogy_bridge` |
| `sequence` | Ordered steps or times | `process_flow`, `cycle_loop`, `timeline_track` |
| `cause` | A causes B | `cause_chain` |
| `completion` | Items done or required | `checklist` |
| `hierarchy` | Parts of a whole | `concept_tree` |
| `quantity` | Numbers and functions | `stat_punch`, `chart_build`, `function_plot` |
| `formula` | An equation and its symbols | `formula_focus` |
| `procedure` | A worked example | `worked_steps` |
| `code` | A program | `code_walkthrough` |
| `table` | A grid of attributes | `table_reveal` |
| `refutation` | A belief corrected | `myth_fact` |
| `media` | A representational picture or motion | `media_split`, `full_bleed_media`, `spotlight_image`, `diagram_build`, `model_spin`, `animation_stage`, `interactive_poster`, `clip_scene` |
| `fallback` | Anything | `board_generic` (board scenes only) |

*As built (B2a).* `aadhi/compose/dynamic/templates.py` holds a `TemplateSpec` for all 41 templates, the class table above (19 rows, frozen; `registry_problems()` checks the registry against it, and a Python and a JS test pin it row by row) and the §4.4 signal rows. Fields beyond the Python row of the table above (deviation register 5, §10.3 B2): `implemented` (true only for `board_generic` so far; `direct.py` picks only implemented templates), `dense` (R-55 frozen background and no SFX: exactly `formula_focus`, `worked_steps` and `code_walkthrough`), `push_ok` (`media_split`, `spotlight_image`), `slots` (per slot, format and variant: width, lines, size range, role, face, weight, line height, word or character limits; filled for `board_generic` from its JS, empty until a template is built) and `consumes` (the event kinds and target forms a template draws). `variants` are per format; `duration` is a `Duration(min, max, per_item, end_screen)` per format; `fit(scene, fmt, *, last=False, chapter_end=False)` returns 1.0 (preferred), 0.8 (alternative), 0.5 (stretched: the data is past the template's limit), 0.2 (`board_generic` as the fallback) or 0. `events()` is declared and returns nothing until B2d; `templates.py` and `events.py` never import each other.
- **Targets** (`VisualEvent.target`, `parse_target` / `format_target`): `<item>`, `<item>:line:<n>`, `<item>:var:<symbol>`, `<item>:step:<n>`, `<item>:row:<n>`, `<item>:column:<n>`, `panel`, `panel:bar:<series>:<i>`, `panel:point:<series>:<i>`, `panel:row:<n>`, `panel:column:<n>`, `option:<i>`. Indices are 0-based, without leading zeros; `<series>` is a dataset index, never a label (a label changes with translation); `<symbol>` is the variable's `symbol_latex` as written, never an index: B1's sync part `var:<n>` becomes `<item>:var:<variables[n].symbol_latex>` when `events.py` writes it (B2d). Board items named `panel` or `option` cannot be targets. The W2 templates' pins, SVG groups and tree nodes have no form yet.
- **JS module** (`templates/types.js` `TemplateModule`): `{id, build, layout?, update, texts?, focusRect?, itemEl?, dimGroups?, anchor?}` plus static `mediaBox?(scene, zones, mode, slot, fixes)` and `tex?(scene, look, fmt)`. `focusRect(ctx, target)` gives `{rect, shape, radius}` on whole pixels; `itemEl` the item's card body (its `.rt-k` term spans kept); `dimGroups` the groups a `look_here` dims; `anchor` the morph anchor (`null` in `board_generic`). The context has no `timeline`; it gets `chapter` (the kicker's chapter title, `state.js` `chapterTitleAt`, B1's rule). `ctx.media(ref, {slot, start, alt, box?})` takes a clip's box from the template's `mediaBox`, the hook `mediaPlan` asks too, and only a picture may pass a box of its own. `ctx.motion` and `ctx.outgoing` come with their first user (B2c); `ctx.cues` is not shipped (cues are stage-driven, deviation 3).
- **Roles** (`core.js` `makeRoles`): `color()` takes the 14 roles above and the block roles `block.base`, `block.highlight`, `block.ink`, `block.ink2` and `scrim`. Each block role is the look's stored token when that field is present and valid, otherwise B1's run-time derivation, decided field by field and never by `engine_version`. `roles.on(mode)` gives the other mode's content palette (`presentation.content_alt`) with the scene's structure roles and throws without a complete one. Unknown roles throw.
- **Open (B2c).** In 9:16 a misconception with a justification is drawn as two cards, and every target, `<id>:fact` included, names the myth card, so a `cue.term` on a term of the correction cannot reach it there (in 16:9 one card holds both). B2c chooses a part form in the target grammar or an `itemEl` change before `cues.js` uses the contract (documented in `board_generic` `itemEl`, pinned by a test in both formats).

### 4.3 Scene-template catalogue (41 templates)

**Release.** `R1` ships in the first release (B2 builds them, B4 wires generation): 23 modules, namely 8 bookend templates (`aadhi_intro`, `aadhi_recap`, `hook_question`, `key_terms`, `chapter_bumper`, `recap_retrieval`, `outro_next`, `thumbnail`), 11 content and practice templates (`definition_card`, `kinetic_statement`, `list_build`, `media_split`, `formula_focus`, `worked_steps`, `code_walkthrough`, `chart_build`, `myth_fact`, `predict`, `check`) and 4 forced or fallback ones (`full_bleed_media`, `animation_stage`, `interactive_poster`, `board_generic`). `W2` (17 templates) ships in B7. `clip_scene` ships in B6. Templates not yet released are disabled in `direct.py`; their scenes fall back within their class or to `board_generic`.

#### A. Bookends and structure

**`aadhi_intro`** (R1, structure) — session introduction plus the hook; layouts and timing in §3.3. Data: the opening BoardScene with `look.presenter`; Aadhi beats; items `heading` (title) and `question` (hook).

**`aadhi_recap`** (R1, structure) — social cue opening the retrieval recap; §3.3. Data: a `recap` BoardScene with `look.presenter`, one Aadhi beat, a `question` prompt.

**`hook_question`** (R1, prompt) — prequestion and curiosity gap [LS §5].
- **Data:** a BoardScene with role `hook` and one `question` item (≤ 12 words; ≤ 8 in 9:16). `variant` follows the hook kind (§6.5): `question`, `true_false` (a "True or false?" pill above a statement; **no MYTH stamp**, R-63), `stakes`, `mystery` (an image at 30% opacity behind the text, blurred when the asset is built because CSS blur costs ≈ 22 ms per frame [MR §3]), `challenge`.
- **16:9:** `centre-60`, 112–140 px, ≤ 3 lines. **9:16:** the hook band, auto-fit (§2.5).
- **Choreography:** words land with the narration (rise 24 px + fade, 450 ms, 70 ms stagger); the keyword takes `cue.term`; the "?" glyph pops once (no SFX); hold ≥ 0.8 s after the last word. The caption band is blank while the words are on screen.
- **Timing:** 4–10 s (16:9), 3–6 s (9:16).

**`roadmap`** (W2, structure) — advance organiser for 4-chapter lessons. Normally the first `chapter_bumper`'s mini-roadmap replaces it (§2.3).

**`key_terms`** (R1, definition) — pre-training of 2–4 component terms (R-05).
- **Data:** role `pretraining`, 2–4 `definition` items: term ≤ 3 words, meaning ≤ 8 words, `icon_hint`.
- **16:9:** 2–4 cards in a row (card width = (1728 − gutters) ÷ n). **9:16:** stacked rows in the text column.
- **Choreography:** one card per beat: rise 32 px (450 ms), the icon's strokes draw (400 ms), term then meaning 80 ms apart.
- **Timing:** 3–4 s per term (16:9), optional in 9:16.

**`chapter_bumper`** (R1, structure) — boundary marker and the only place the structure accent changes (R-18, R-52).
- **Data:** a `ChapterCardScene` (`chapter_label`, `title`; may be silent).
- **16:9:** a full-bleed colour-block wipe in the new accent; outline numeral "02" at 320 px (*as built: a tint of the block ink, not a stroked outline — §10.3 B2, review D4*); title at 96 px; mini-roadmap of all chapters along the bottom (past chapters ticked, the current one lit). **9:16:** numeral 240 px, title centred.
- **Shared anchor** [RV L-12]. When the chapter is `builds_on` the previous one (planner field, default true), the previous chapter's anchor visual (its formula, key figure or last takeaway, chosen by `direct.py`) morphs into a ≤ 160 px "from last chapter" chip that stays at the top left through the next scene.
- **Choreography:** wipe 450 ms `ease.hero`; numeral slides 40 px (500 ms), title 120 ms later; the node lights; hard cut out; one whoosh (R-49); the music changes arrangement.
- **Timing:** 2–3 s (16:9), 1.2–1.8 s (9:16; only at act boundaries in the Learning profile).

**`recap_retrieval`** (R1, prompt) — R-08.
- **Data:** a `recap` BoardScene: 2 `question` items (`blank=True`, answer in `justification`, ≥ 1 with `level` apply or transfer), each filled by a later beat whose `pause_after` is the think time (§1.4); a free-recall prompt; then 3 `takeaway` items, each a relational statement using pre-training terms (lint `recap.relational`).
- **16:9:** a centred question card with a think ring and the answer beneath; then the free-recall card ("Name the big ideas", ≥ 6 s ring); then 3 tiles on `surface` with icons. **9:16:** 1 question, free recall, 3 stacked tiles.
- **Choreography:** the ring runs linearly with no sound and music at the under-speech level or off; on reveal the answer rises 16 px, an `ok` check pops and the chime plays (R-49); tiles flip in (rotateX 90° → 0, 400 ms) as each is named. Tiles never take the chapter accent [RV L-3].
- **Timing:** 25–40 s (16:9), 14–20 s (9:16).

**`outro_next`** (R1, structure) — closure and next step ("Part n+1: …" in a series). 16:9: takeaway left, "Next up" right; with end-screen space the right half stays clean for 15 s. 9:16: ≤ 3 s call to action plus the loop line; the sonic logo plays here. The card rises (500 ms), the logo resolves, the music ends on a phrase with a 3 s fade (1.5 s in 9:16). Timing: 6–10 s or 15 s (16:9), 3–5 s (9:16).

**`thumbnail`** (R1, structure; single frame, not in the timeline) — a designed 1280×720 frame: the lesson title or hook (≤ 5 words, display face, auto-fit), the lesson's first representational image or the key-term icon, the family's colours, and the session chip. The description draft is assembled deterministically from the title, the brief's topic line, the chapters, the credits and the AI disclosure [RV T-26].

#### B. Explanation

**`definition_card`** (R1, definition).
- **Data:** a `definition` item plus an optional `bullet` example.
- **Variants:** `card_icon_right`, `card_image_left`, `centred`. In dark families the first scene of a chapter may use the light "paper interlude" surface, at most once per chapter and **only at a boundary** [RV L-12].
- **Choreography:** the card rises 24 px (400 ms); the term reveals from its baseline; the meaning's words follow the narration; the key word takes `cue.term`.
- **Timing:** 6–12 s (16:9), 4–8 s (9:16).

**`kinetic_statement`** (R1, statement) — one principle set as typography. Data: a `takeaway`, `paragraph`, `heading` or `callout_*` item of ≤ 12 words (≤ 8 in 9:16). 16:9: 100–140 px, 2–4 lines, left-aligned; 9:16: the hook band and stage. Words land as spoken; the keyword takes `cue.term`; completed lines dim to 70%; captions are blank. Exempt from `text.verbatim`. Timing: 3–7 s (16:9), 2–5 s (9:16).

**`list_build`** (R1, enumeration).
- **Data:** ≥ 2 `bullet` items, an optional `heading`, optional `icon_hint`s.
- **16:9:** a numbered list in the left 55%; the right 45% shows the current item's large icon or the scene's panel. The current item is bold, full `ink` with a 3 px `ink` outline; earlier items are `ink2` (no accent colour).
- **9:16:** a **persistent compact stack**: earlier items stay as ≥ 40 px rows at the top of the stage while the new item appears large below, never a one-item carousel [RV L-13].
- **Choreography:** each item slides 24 px (450 ms) on its beat; number first, text 80 ms later.
- **Timing:** 3–5 s per item.

**`media_split`** (R1, media) — the explainer frame. Data: ≤ 3 text items and a `figure`, `image`, `chart`, `graph` or `terminal` panel. 16:9: media 55% (24 px radius, soft shadow), text 45%; the media side changes **only at chapter boundaries** [RV L-2]. 9:16: one-line headline, media in the stage, key phrase above the caption band. Choreography: media scales 0.96 → 1 (500 ms); bullets per beat; a `focus` sync cue becomes `cue.lookHere` (push ≤ 3%). Timing: 8–20 s (16:9), 5–12 s (9:16).

**`full_bleed_media`** (R1, media; forced for `ai_video`). The media covers the frame; in 9:16 it is cropped to its focus point or shown `contain_blur` (§2.5). A lower-third keyword label (≤ 4 words) over a 55% scrim. Stills get Ken Burns 1.00 → 1.06 (counts as an event only when the narration names the subject). At most 2 consecutive. Timing: 3–12 s.

**`spotlight_image`** (W2, media). A `figure` item or image panel; pins exist **only on source figures**, placed by the teacher in Visual Review. The camera push toward the named region is a `cue.lookHere`. Timing: 6–12 s.

**`diagram_build`** (W2, media; new) — dynamic drawing [CP finding #4; Fiorella & Mayer 2016, d ≈ .54] [RV T-25].
- **Data:** an SVG figure (teacher-uploaded, sanitised with the vendored DOMPurify SVG profile) whose top-level `<g>` groups are bound to beats in Visual Review (default: document order, one group per beat).
- **Choreography:** each group's strokes draw (stroke-dashoffset from `t`, 600–1200 ms) on its beat, then fills fade in; labels appear on their anchor words; the named group takes `cue.lookHere`.
- Manim-sourced drawing stays in `animation_stage`. Timing: 8–30 s.

**`versus`** (W2, contrast). A `table` with exactly 2 value columns (+ optional attribute column), ≤ 4 rows, or `visual_intent="compare"` with two `definition` items. The two columns take entity colours only when their headers are already mapped entities (§4.7); otherwise `ink` with icons. A "VS" badge (no SFX). One row pair per beat (350 ms). Never chosen for `pair` items (correspondence) [RV L-2]. Timing: 8–16 s.

**`table_reveal`** (W2, table). Sticky header, one row per beat; the active row via `cue.lookHere`; a named column via the existing `column:<n>` emphasis cue mapped to `lookHere`. 9:16: ≤ 3 columns; otherwise rows become stacked cards in a **persistent compact stack** [RV L-13]. Timing: 10–20 s.

**`analogy_bridge`** (W2, correspondence). ≥ 2 `pair` items (familiar ↔ concept); familiar side first, hold 0.8 s, then the concept side; an "≈" badge; each mapping line draws (400 ms) on its beat. Timing: 8–15 s.

**`process_flow`** (W2, sequence). 3–6 `step` items with `sequence="flow"`. Nodes pop on their beats; arrows draw; a token travels on "then"/"next"; the current node is bold with an `ink` outline, past nodes `ink2`. Timing: 10–25 s.

**`cycle_loop`**, **`timeline_track`** (W2, sequence) and **`cause_chain`** (W2, cause): as process_flow, with arcs drawn clockwise, an axis with dated cards, or domino cards with labels on "so", "because", "therefore". Timing: 8–25 s.

**`concept_tree`** (W2, hierarchy). A `skill_tree` panel or `concept_map` subset; radial (16:9) or top-down (9:16); the named node takes `cue.lookHere`. Timing: 8–15 s.

**`formula_focus`** (R1, formula).
- **Data:** a `formula` item with `variables`.
- **16:9:** MathJax SVG at 1.6× board size; up to three symbols take their lesson-wide **entity colour** (§4.7), the rest stay `ink`; legend rows appear on `var` cues under braces; the result is boxed.
- **9:16:** fitted to 768 px, breaking at "=" if wider; otherwise lint `portrait.formula_wide`.
- **Choreography:** fade in (400 ms); each symbol tints on its `var` cue; braces draw (300 ms); **no camera motion** (R-30); hold ≥ 3 s after completion; background frozen (R-55); no SFX.
- **Timing:** 10–25 s (16:9), 8–16 s (9:16).

**`worked_steps`** (R1, procedure) [RV L-15].
- **Data:** `example_step` items (blank steps plus fill beats) and an optional formula.
- **16:9:** steps in the left 55%; the formula with the given values on the right; on a fill the value **flies into its slot** (600 ms, `ease.move`).
- **9:16:** a persistent compact stack of the earlier steps (≥ 40 px rows) above the current step [RV L-13].
- **Fading:** in any example of ≥ 3 steps the final step is a **"Your turn"** blank with a 4–6 s think ring before its fill beat; a second example in the lesson leaves at least half its steps blank, each with a think hold. Lint `example.no_fade`.
- **Choreography:** each step slides in (400 ms); a blank types in at 30 chars/s on its fill beat; the final answer gets an `ok` outline and check icon; background frozen; no SFX.
- **Timing:** 15–45 s, may continue over 2 scenes (`continues_from`) (16:9); 10–30 s (9:16).

**`code_walkthrough`** (R1, code). An editor frame on a dark surface in every family, line numbers; ≤ 18 lines of ≤ 60 chars at 34 px (9:16: ≤ 10 lines of ≤ 32 chars, else `portrait.code_too_wide`). Lines type at 30–40 chars/s when narrated or appear line by line; the focused line takes `cue.lookHere` (a bar, no zoom); the terminal slides up on the `output` cue; background frozen; **no key-tick SFX** [RV L-5]. Timing: 10–30 s (16:9), 8–20 s (9:16).

**`chart_build`** (R1, quantity). A `chart` panel (`ChartSpec`) drawn by the vendored Chart.js with animations **off**: our evaluator interpolates data per frame and calls `chart.update('none')`. Up to 3 series take entity colours; further series use `ink2` tints with dash patterns; all series are **direct-labelled** (R-27); gridlines at 10% alpha. Axes fade in (300 ms); bars grow (600 ms, 80 ms stagger) or the line draws (1–1.5 s); values count up; the named bar takes `cue.lookHere`. Timing: 8–20 s.

**`function_plot`** (W2, quantity), **`model_spin`** (W2, media): as before — progressive curve sampling with a readout; three.js posed from `t`, named part via `cue.lookHere`. Timing: 6–25 s.

**`animation_stage`** (R1, media; forced for `simulation`).
- **Data:** a `simulation` scene (`ManimSpec`, one step per beat) or a `manim` panel.
- **Colours** [RV L-17]. The Manim background is the stored asset family's `bg` (§4.7, image harmonisation), and Manim's named colours map to content roles (`BLUE` → entity 0, `PURPLE` → entity 1, `PINK` → entity 2, `YELLOW` → `mark`, `GREEN` → `ok`, `RED`/`ORANGE` → `warn`, `WHITE` → `ink`), so contrast follows from the validated tokens. When the writer sets `colour_is_content` (resistor bands, flame tests, wiring colours, spectra), the literal colours are kept and the clip renders on Manim's default dark background inside a dark card.
- That is a new Manim config input and content key, so Dynamic lectures cost one extra Manim render. 9:16 uses the existing 1080×1350 panel profile [CB §3]. A step label appears as a lower third on each beat. Timing: 8–40 s.

#### C. Practice and consolidation

**`myth_fact`** (R1, refutation) [RV L-16].
- **Data:** a `misconception` item (`text` + `justification`). The myth line may be spoken by the `student` voice (§5.5) [CP S5].
- **16:9:** a statement card labelled "Common belief" with the `warn` icon; on the next beat a strike-through draws (300 ms) and the card shrinks to 70% and dims to 40%; the **FACT** card (`ok` icon, larger, full contrast) slides in with the correction and the reason and **stays as the final frame**. **9:16:** the myth card in the upper stage, pushed up and shrunk by the fact card.
- No stamp sound; the fact reveal may use the answer chime (R-49). Never placed in a recap (lint `myth.in_recap`).
- **Timing:** 8–14 s (16:9), 6–10 s (9:16).

**`predict`** (R1, prompt) — prediction **before** the explanation (R-62) [RV L-14].
- **Data:** a `question` item (`blank=True`); its answer beat (`Beat.answers`) lives in the **next explaining scene** of the same chapter.
- **Layout:** the question card, "Pause and predict", and a think ring (§1.4). No answer here.
- **In the explaining scene:** a small "Your prediction?" chip with the question stays at the top until the answering beat, where it resolves with the answer.
- **Live player:** pauses for an optional typed or tapped prediction (§9.7).
- **Timing:** 6–12 s (16:9), 5–9 s (9:16).

**`check`** (R1, prompt; forced for `quiz` scenes) — retrieval **after** the explanation.
- **Kinds** (`QuizScene` and board `question` items): `mcq` (≤ 4 options; 16:9 only), `free_recall`, `true_false`, `apply` (a one-step problem). The order of kinds across a lesson's chapter checks is a seeded permutation, so lessons do not all close their chapters the same way [RV T-8]. Reels use `free_recall` or `true_false` only [RV L-4].
- **Think time:** §1.4, computed from the words to read; `QuizScene.countdown_seconds` is set from it (the field allows 3–30 s). The first check of a lesson includes the narrator line "Pause now if you need more time" (varied from a pool).
- **Choreography:** options appear with a 100 ms stagger; the ring runs with no riser and music at the under-speech level or off; on reveal the correct tile turns `ok` with a check pop and the chime; others dim to 40%; the explanation line appears.
- **Live player:** pauses for input, as today.
- **Timing:** 15–30 s (16:9), 10–18 s (9:16).

**`takeaway_stamp`** (W2, statement). A `takeaway` of ≤ 15 words on a card rotated −2°, labelled "Remember"; drops from scale 1.1 to 1 (300 ms), no sound. Timing: 4–7 s.

**`quote_card`** (W2, statement). A `quote` item (≤ 25 words, attribution in `caption`) that **must be verbatim from the source** (`quote.ungrounded` error otherwise); ≤ 1 per video; captions blank while it is read. Timing: 5–8 s.

**`checklist`** (W2, completion). Only for items with `sequence="checklist"`, never as an alternative for plain bullets [RV L-2]. Each check mark draws (250 ms) when the narration says it is done; no click sound. Timing: 8–20 s.

**`stat_punch`** (W2, quantity). A `stat` item (`value`, `unit`, context ≤ 10 words) with **required `source_refs`** (`stat.ungrounded` error). The number counts up (1000 ms) with tabular numerals; an optional landing accent counts toward the ≤ 1 per 20 s accent budget. Enables the `stat` hook kind. Timing: 4–7 s.

#### D. Media, interactive and fallback

**`clip_scene`** (B6, media; forced for `clip`). A teacher-inserted external clip (§8.2). Fit `contain_blur` by default when the aspect differs, or `cover` with a focus point. A credit lower third for the first 3 s; clip captions in the caption band.

**`interactive_poster`** (R1, media; forced for `interactive`). MP4: the poster with Ken Burns and a "Try it in the lesson page" chip. Live player: the sandboxed p5 sketch, as today.

**`board_generic`** (R1 since B1, fallback). Only for board scenes no other template accepts [RV E-12]: stacked cards with ≤ 3 visible, older ones collapsed into a "previous" chip, one reveal per beat, a `media_split` layout when there is a panel.

*As built (B2a): what the registry reads from this section* (`templates.py`; a value this design does not give is marked in place as an A2 proposal or reading, pending the owner's review, and the catalogue's header lists them):
- 41 specs: 23 R1, 17 W2 and `clip_scene` (B6), as counted above; `thumbnail` has the 16:9 format only. Forced scene types: chapter cards → `chapter_bumper`, quiz scenes → `check`, `simulation` → `animation_stage`, `ai_video` → `full_bleed_media`, `interactive` → `interactive_poster`, `clip` → `clip_scene`.
- Durations are the Timing lines above (per item for `key_terms` and `list_build`; R-19's scene length where a template gives none). `outro_next` is 6–10 s, or exactly 15 s with end-screen space, in 16:9 (`Duration.end_screen`; never a length between the two) and 3–5 s in 9:16. `key_terms` keeps its per-term range in 9:16: "optional in 9:16" is read as a choice about using the template, not about its length (A2 reading).
- `code_walkthrough` takes ≤ 17 lines of ≤ 60 characters in 16:9 and ≤ 10 lines of ≤ 28 characters in 9:16 (deviation 13: R-29's floor wins over 34 px and 32 characters). `check` offers `mcq` in 16:9 only; in 9:16 it takes two options (free recall, true/false), and more is "stretched".
- `hook_question` still lists its `mystery` variant, which deviation 16 defers to B4 (the asset-time blur and B4's hook rotation); the template is not implemented, so nothing can choose it before then.
- A2 proposals: `media_split`'s variant names `media_left` / `media_right` (this section says only that the media side changes at chapter boundaries; 9:16 has none), and which templates take `hold_reason` `diagram` (`media_split`, `spotlight_image`, `diagram_build`, `process_flow`, `cycle_loop`, `timeline_track`, `cause_chain`, `concept_tree`, `chart_build`, `function_plot`, `model_spin`; `board_generic` takes every reason, as in B1).
- B1's `board_generic` keeps its 28 px media frame and presenter radius; the media token (`media_split`'s "24 px radius") is 24.

### 4.4 Template selection (`aadhi/pipeline/direct.py`)

Selection is deterministic and needs no LLM. It runs after canonicalisation, before the screenplay is written (§6.1), and on editor saves only for scenes without a settled look (§6.8). Inputs: the screenplay, the format, `look_seed` and `layout_epoch` (§6.7), the teacher's history (§5.3), and each scene's `look.lock`.

**Seed.** `look_seed = int.from_bytes(sha256(f"{project_id}:{format}:{look_epoch}").digest()[:8], "big") & 0x7FFF_FFFF` [RV E-34, T-1]. It does not include the version number, so regenerating a lesson reproduces its look.

**Class of a scene.** From the signal table:

| Signal in the scene | Class | Preferred | Same-class alternatives |
|---|---|---|---|
| The opening scene with `look.presenter` | structure | `aadhi_intro` (forced) | `hook_question` if the presenter is disabled |
| Role `hook` | prompt | `hook_question` (variant by hook kind) | `stat_punch` only for the `stat` hook kind (W2) |
| Role `pretraining` | definition | `key_terms` | — |
| `chapter_card` scene | structure | `chapter_bumper` (forced) | — |
| A `definition` (+ ≤ 1 bullet) | definition | `definition_card` (variants) | — |
| A single `takeaway`, `paragraph` or `heading` | statement | `kinetic_statement` | `takeaway_stamp` (chapter end) |
| `quote` | statement | `quote_card` | `kinetic_statement` |
| ≥ 2 bullets | enumeration | `list_build` (variants) | — |
| `step` + `sequence` flow / cycle / timeline | sequence | `process_flow` / `cycle_loop` / `timeline_track` | each other only when the data fits (e.g. a flow without dates never becomes a timeline) |
| `step` + `sequence="chain"` | cause | `cause_chain` | — |
| `sequence="checklist"` | completion | `checklist` | — |
| `pair`s | correspondence | `analogy_bridge` | — |
| `table`, 2 value columns, or `visual_intent="compare"` | contrast | `versus` | — |
| `table`, ≥ 3 columns | table | `table_reveal` | — |
| `stat` | quantity | `stat_punch` | — |
| `chart` panel / `graph` panel | quantity | `chart_build` / `function_plot` | — |
| `formula` | formula | `formula_focus` | — |
| `example_step`s | procedure | `worked_steps` | — |
| `code` | code | `code_walkthrough` | — |
| `misconception` | refutation | `myth_fact` | — |
| `question` (blank) with role `predict` / `check` | prompt | `predict` / `check` | — |
| `quiz` scene | prompt | `check` (forced) | — |
| ≤ 3 text items + a media panel | media | `media_split` | `spotlight_image` (figure/image), `full_bleed_media` (application image), `diagram_build` (SVG figure) |
| `skill_tree` panel | hierarchy | `concept_tree` | — |
| `simulation` / `ai_video` / `interactive` / `clip` | media | `animation_stage` / `full_bleed_media` / `interactive_poster` / `clip_scene` (all forced) | — |
| `recap` with presenter / `recap` | structure / prompt | `aadhi_recap` / `recap_retrieval` | — |
| Last scene | structure | `outro_next` | — |
| Any other board scene | fallback | `board_generic` | — |

**Algorithm.**

```
for scene in shown scenes, in order:
    if scene.look.lock in ("pinned", "settled") and fit(scene.look.template) > 0: keep; continue
    if scene.type or role forces a template: assign it; continue
        # forced templates are exempt from the repeat and share penalties [RV E-12]
    C = enabled templates of the scene's class with fit(scene, fmt) > 0
    if C is empty: C = [board_generic] for board scenes (never for other scene types)
    score(t) = fit(t)
             + 0.15 · [t matches scene.intent.visual_intent]
             − 1.00 · [t == previous template and scene.look.continues_from is None]   (R-20)
             − 0.40 · [choosing t would exceed the 25% (16:9) / 30% (9:16) share]
             + 0.10 · [t unused in the last 3 scenes]
             + 0.05 · rng(look_seed, layout_epoch, scene.id)                  (tie-break)
    pick argmax; never a typography-only template for a scene with a representational alternative (R-28)
post-pass, bounded to len(scenes) iterations:
    stop when distinct templates ≥ min(5, number of classes present) [RV L-2]
    find the same-class swap with fit ≥ 0.6 and the smallest score loss that keeps R-20, R-23 and R-28
    if none exists: stop (look.variety, system-level warning)
    apply it and re-check R-20 and R-23
then: variants per scene (the media side changes only at chapter boundaries), one background preset per
      video (R-18), the transition per boundary (§4.9), structure accents per chapter or act (§4.7),
      entity colours (§4.7), measured text fit (§4.6)
```

**Output.** `SceneLook` on every scene (§6.7): `template`, `variant`, `background`, `transition_in`, `presenter`, `palette_slot`, `continues_from`, and `lock`. After the first generation every look is written with `lock = "settled"`.

**Locks** [RV T-2, E-23]. `auto`: chosen by `direct.py` and re-chosen on the next run. `settled`: chosen once and kept; `direct.py` re-picks it only if its template's `fit()` becomes 0 after an edit, choosing among templates compatible with the fixed neighbours. `pinned`: the teacher's choice, never changed. Only **New layouts** (re-pick settled templates) and **New colours** (re-roll family, accents and background) re-style settled scenes (§8.8). Variety rules on settled scenes are warnings in the Look lane, never automatic re-picks.

**Property tests** (B2): determinism; termination of the post-pass; two consecutive `quiz`, `clip`, `ai_video` or `simulation` scenes keep their forced templates; `board_generic` never renders a non-board scene; swaps never cross classes; editing one scene's text never changes another scene's settled look.

*As built (B2a).* `templates.signals(scene, *, last, chapter_end)` returns every row above that a scene matches, in table order (a forced scene type returns only its own row), and each spec's `fit()` scores the scene; `direct.py` (B2e) chooses among them (for example the first row whose class has a candidate with fit > 0, then R-28). Readings of the table: the single-statement row also takes one `callout_*` item (`kinetic_statement`'s own data, §4.3); the media row is triggered by a media panel or by one `figure` board item, and also names `animation_stage` (a `manim` panel) and `model_spin` (a `model_3d` panel); two "same-class" alternatives cross classes (`hook_question` in the presenter row, `stat_punch` in the hook row: `CROSS_CLASS_ALTERNATIVES`), so B2e must admit a named alternative of another class; a forced template never scores 0 on its scene type. `board_generic` scores 0.2 on chapter-card, quiz, simulation, AI-video and interactive scenes because B1 still draws them, so the property test "`board_generic` never renders a non-board scene" becomes a `direct.py` rule once those forced templates exist. `diagram_build` scores 0 (its SVG-group binding is not on the scene yet, W2); `roadmap` and `thumbnail` have no row and score 0. The resolver honours `SceneLook.palette_slot` from B2a (§4.7); `direct.py` writes it in B2e.

### 4.5 Background system

Backgrounds are pure, periodic functions of `t`, painted in the stage as CSS custom properties updated per frame or as a Canvas2D layer.

- **16:9:** one preset per video. A new preset is allowed only at a chapter boundary whose chapter has `builds_on = false`, or at every chapter boundary with energy *Lively* (R-18) [RV L-12]. Inside the video each scene takes a **variant**: a tone step `bg` → `bg-alt` → `bg-tint` and a seeded composition offset.
- **9:16 Learning:** the preset changes at act boundaries; **Social energy:** at segment boundaries.
- **Holds:** while `hold_reason` is set and throughout `formula_focus`, `worked_steps` and `code_walkthrough`, the background **freezes** (its time is pinned to the scene start) and desaturates by 30%, eased over 600 ms (R-55) [RV L-23].

| Preset | Look | Personality fit | Release, tier and cost |
|---|---|---|---|
| `mesh_drift` | 3–4 large radial gradients drifting on Lissajous paths (period 20–30 s) | Soft, Cinematic | R1; CSS, ≈ 0 ms |
| `dot_field` | A dot grid with a travelling opacity wave (period 16 s) | Crisp | R1; CSS |
| `paper_grain` | Cream paper, static grain, ±1% scale breathing (period 20 s) | Editorial | R1; CSS + a vendored grain PNG |
| `soft_waves` | 3 SVG sine waves in the bottom 30%, parallax at 18, 24 and 30 s | Soft | R1; SVG |
| `spotlight` | A radial vignette breathing ±3% (period 16 s) | any | R1; CSS |
| `colour_block` | A solid accent block with grain, for hooks and bumpers only | any | R1; CSS |
| `aurora_band`, `blueprint_grid`, `chalk_slate`, `geo_shapes` | as named | Cinematic, Crisp, Chalk, Bouncy | W2 |
| `bokeh_field` | ≤ 60 seeded closed-form particles, masked away from text | Cinematic | W2; Canvas2D ≈ 2 ms |
| `shader_mesh`, `shader_grain`, `shader_warp`, `shader_waves` | Paper Shaders 0.0.81 (`speed=0`, `setFrame(t·1000)`, 0.5 scale) | Cinematic | W2; WebGL on SwiftShader ≈ +28 ms per frame [MR §3]; bookend scenes only, ≤ 2 per minute on CPU hosts; CSS twin if the probe fails |

**Text contrast over backgrounds.** Text sits on a card surface, on a scrim with ≥ 55% opacity, or on a background whose worst-case luminance under the text box keeps the R-53 ratio. Each preset declares its worst-case luminance per palette, computed analytically when the catalogue is built; the validator forces the scrim on for any preset and palette pair that cannot hold contrast.

### 4.6 Typography and measured text fit

| Role | Face | Licence |
|---|---|---|
| Body | **Inter** (vendored), fixed per teacher. Option "High legibility": **Atkinson Hyperlegible Next** | OFL 1.1 (Atkinson verified [CP S57]) |
| Display (rotates per video, excluding the last 3 the teacher used) | R1: Outfit (vendored), Poppins, Lexend. W2: Montserrat, Sora, Space Grotesk, Bricolage Grotesque, Baloo 2; Editorial: Fraunces or DM Serif Display | Believed OFL; **verify each LICENSE file when vendoring** [CP §9.4] |
| Indic display and body | **Anek** (variable; 9 Indic scripts + Latin), or Noto Sans per script (vendored). Rotation varies Anek's axes instead of the face | OFL 1.1 (Anek verified [CP S58]) |
| Code | JetBrains Mono (vendored) | OFL |
| Maths | MathJax SVG (existing, hardened) | Apache-2.0 |

**Sizes** follow R-29 and the R-53 thresholds. **Lines:** display 1.0–1.05 line height with −1 to −2% tracking; headings 1.15; body 1.35; body lines ≤ 45 chars (16:9).

**Casing and numerals.** Sentence case everywhere, including every caption style [RV L-7, T-21]. All caps only for labels of ≤ 4 words and single stat units, with +4–6% tracking; all caps reads slower [SF §4.1]. Count-ups and tables use tabular numerals. At most two families plus mono per video.

**Measured text fit** (`aadhi/compose/dynamic/textfit.py`) [RV T-4]. Every text slot (hook band, headline, card text, caption chunk) is fitted by **measured advance widths**, never by character counts:
- Python measures with the vendored fonts through Pillow's FreeType binding at `direct.py` time, choosing the font size within the slot's range and the line breaks, so the lints `portrait.hook_overflow`, `text.overflow` and the caption chunker can fire **before TTS**. A 4% width slack covers kerning differences; Indic scripts get 8% because Pillow's basic layout does not shape them. *(B2b measured both premises false: a relative slack alone does not bound Latin kerning, and no slack bounds Indic shaping; see "As built (B2b): measured text fit" below. **Superseded by B2b-H:** Python now shapes every script with HarfBuzz on the vendored faces, as Chromium does, with a 0.5% + 1/64 px margin; see "As built (B2b-H)" below.)*
- The page lays out with CSS and reports `overflow` per text box from `textBoxes()`; preflight shrinks an overflowing box down to its minimum size, then flags it (§7.12).
- B2 verifies that Pillow in the Debian image reads WOFF2 (it does on the dev machine); if not, `scripts/vendor.mjs` also stores TTF copies for measurement. *(Superseded by B2b-H: text fit no longer uses Pillow. fontTools decodes the WOFF2 files and HarfBuzz shapes them, the same code on every platform.)*

**Loading.** WOFF2 files vendored through `scripts/vendor.mjs` with their LICENSE files; `document.fonts.load()` for every family, weight and script before `ready`; **emoji are images** (Fluent Emoji or Noto), never glyphs [MR §5].

*As built (B2b): faces* (`aadhi/compose/dynamic/faces.py`, `web/js/dynamic/fonts.js`; `docs/plans/b2/b2b_review.md`):
- **Vendored in B2b**, from exact-pinned Fontsource 5.3.0 packages (published 2026-07-19), into `web/vendor/fonts/dynamic/<family id>/`, each folder holding exactly its faces and its package's LICENSE, byte for byte:
  - Poppins and Lexend (display; weights 400, 600, 700 and 900; the `latin` and `latin-ext` subsets). Poppins' Devanagari subset is left out on purpose: Indic text is drawn by the Indic family.
  - Atkinson Hyperlegible Next (the "High legibility" body; 400, 600, 700; `latin`, `latin-ext`).
  - Anek Tamil, Devanagari, Telugu, Kannada and Malayalam (`@fontsource-variable/anek-*`): the variable `wght` file (100–800) of the script subset only. The file's own default is `wght` 500, so every measurement sets the axis.
- **Unchanged:** Inter, Outfit, JetBrains Mono and the Noto Sans Indic faces stay where B1 vendored them (`web/vendor/fonts/`, `web/vendor/fonts.css`, `vendor.mjs` `FONTS`). `compose/video.py` `_copy_fonts` still copies only the top-level files of `web/vendor/fonts` into classic burn-ins, so the new sub-folder never reaches them (pinned by `tests/compose/dynamic/test_faces.py`).
- **Licences**, read from each package's LICENSE: all 16 font packages (the 8 new and the 8 already vendored) carry the full SIL OFL 1.1 text with a copyright line and no Reserved Font Name. The names: Poppins "Copyright 2020 The Poppins Project Authors", Lexend "Copyright 2019 The Lexend Project Authors", Atkinson Hyperlegible Next "Copyright 2020-2024 The Atkinson Hyperlegible Next Project Authors", Anek (five identical files) "Copyright 2021 The Anek Project Authors". The licence review also decoded the name tables of the vendored WOFF2 files: their copyright strings match and name no Reserved Font Name. That matters because Fontsource's unicode-range subsetting is a modification under the OFL. `vendor.mjs` refuses a LICENSE without the OFL 1.1 text.
- **One face table.** `faces.py` is the single source. It describes every family (the classic ones row for row too; a test pins them against `fonts.css` and `vendor.mjs`'s range table) and the W2 display families as listed but not vendored. It generates `web/css/dynamic-fonts.css` (one `@font-face` per B2b face, `font-display: block`, new family names only) and `web/js/dynamic/faces.gen.js` (a frozen data copy). `python -m aadhi.compose.dynamic.faces --check` runs in CI. `vendor.mjs` copies the B2b faces from the generated table through `scripts/vendor_faces.mjs`, which stops on a version other than the pin, a licence that is not OFL 1.1, a `unicode-range` that differs from the package's `unicode.json`, or a face path outside its family folder. `render-dynamic.html`, `index.html` and `watch.html` link `dynamic-fonts.css` after `dynamic.css`; the classic `render.html` does not.
- **`presentation.fonts`** is `{display, body, mono, indic}` (§6.7). The stage reads two forms, told apart by the fields (never by the look-engine generation):
  - the **B1 form** (no `indic`; B1 and B2a timelines) is drawn as the B1 stage drew it: display `outfit` or `inter` as named, any other id as Outfit; Inter; JetBrains Mono; the Noto Sans faces. The render page loads B1's requests.
  - the **B2 form** (`indic` names `noto_sans` or `anek`): each role is the family it names when that family is vendored for the role, else the role's default. The stacks are display = display face, body face, Indic families; body = body face, Indic families; mono = mono face. The lecture's own script comes first among the Indic families.
- **No look changed.** The B2b resolver stores `faces.resolver_fonts(display_font)`: the B2 form naming exactly the faces the B1 stage drew. A display id the B1 stage did not draw (`poppins`, `lexend`) is stored as `outfit`, which it drew instead. Choosing and rotating the display face is B2e's `direct.py`. Measured in real Chromium: the B1 form and the resolver's form of the same timeline give identical frames (`tests/determinism/test_stage_fonts.py`), and the review's differential found 0 of 11,031 frame pairs differing (§10.3 B2).
- **Weight and range matching** that the text fit mirrors: `faces.match_weight` follows CSS Fonts 4 §5.2, and among overlapping `unicode-range`s the last-declared face wins (§4.5 of the same spec).
- **Deviations from this section** (B2b): Anek is vendored for the five scripts the app supports, not all nine, and only its `wght` file, so the Indic "rotation varies Anek's axes" (`wdth`) waits for W2 (plan deviation 19). The R1 display weights are 400/600/700/900 (no 300). The display rotation itself is B2e's.

*As built (B2b): measured text fit* (`aadhi/compose/dynamic/textfit.py`, `kerning.py`; not imported by any production path yet: `direct.py`, the lints and the caption chunker wire it in B2d/B2e). **Superseded in part by B2b-H** (the next block). Its measurement (Pillow), margins (4% / 8%, the 0.06 em floor), kerning table, certification list, Indic rule and Pillow notes no longer hold, and `kerning.py` / `kerning.json` are deleted. They are kept here as the record of what B2b built and measured. Its face resolution, layout rules and page proofs carry over, as the B2b-H block says.
- **Faces, as the page resolves them.** The stack of the slot's role comes from `presentation.fonts` (either form, as a dict or the schema's `TimelineFonts`; any other value raises `TypeError`) and the board language. Each grapheme cluster goes to the first family that has a face for it: CSS weight matching, the last-declared range first, and a cmap check. The check matters because a range can promise more than the file holds: Poppins' `latin-ext` file lacks ħ, and the page falls back to Inter, as textfit does.
- **Measurement.** Pillow FreeType with `Layout.BASIC` (never raqm) on the vendored WOFF2 at 2048 px, scaled linearly; variable axes are always set; one process lock guards every Pillow call. It fails closed: a face that cannot be read, or whose axes differ from the table's, raises `FaceError`.
- **Layout rules mirrored from the page:** `splitWords`' word units and CSS whitespace collapsing; letter-spacing per cluster (per code point when positive); `board_generic`'s `fitLines` (4 px steps from the maximum, the minimum last, `prefer_lines` first, the line limit). An over-wide word breaks only when the page's "unavoidable" rule applies, after a hyphen first, never inside a cluster.
- **Conservative width** (plan §1.11): the advance × 1.04 (Latin; 1.08 for an Indic estimate), plus 0.06 em per word, plus the most the page's kerning, ligatures and contextual alternates can add to the word's pairs and triples. The **optimistic width**, from which textfit decides that the page certainly breaks a word, takes the slack off and adds each pair's and triple's own lower bound.
- **The kerning table** (`aadhi/compose/dynamic/kerning.json`, version 3, about 2.9 MB) was measured once in Chromium for every vendored Latin face. Pillow applies no GPOS and no GSUB, and the page's adjustments are often positive (Outfit `(j` +0.098 em, Atkinson `qj` +0.154 em, Inter `ïï` +0.166 em) and add up per pair (`f'f'f'f'`). It stores, per face:
  - every pair's bound above and below;
  - every context triple's excess beyond its two pairs, both sides (Inter draws the `x` of `2x3` as a wider `×`; Outfit's `ffi` is one glyph);
  - the runs whose interactions reach four or more characters, which are never bounded. These are Inter's "chain" characters (punctuation and operators whose alternates pass along a run: `A*****` is drawn 0.24 em wider than its pairs and triples add up to), open four-character runs (`10x1`, Outfit `ffiw`), arrows and digits before a fraction slash.

  Regenerating the table (`python -m aadhi.compose.dynamic.kerning`, 85–90 minutes on the development machine) needs Chromium and `web/vendor`. Its `--check` is not in CI; `tests/determinism/test_kerning_table.py` (slow) measures the faces again and probes random and targeted runs against the bounds.
- **What is certified.** `fits` is True or False for Latin text the table bounds:
  - True means the page fits too.
  - False means the page overflows, or fits only within Python's margins.

  `fits` is None (an estimate only) for an Indic cluster, a combining sequence, a character no vendored face draws (the page uses a system font), and a run the kerning table does not bound. In Inter that includes ordinary text such as `step-by-step`, `f(x)`, `(a+b)` and `10x10`. `FitResult.stored()` gives the §6.7 `text_fit` entry `{size, lines}` (the line count); it is for certified results only.
- **Proven on the real page** (`tests/determinism/test_textfit_page.py`, slow):
  - Every bounded corpus word in every vendored Latin face, at every slot weight and letter-spacing, is drawn within its conservative width.
  - On `board_generic`'s title, kicker and subtitle, in both formats and four font sets: when Python fits, the page overflow is 0 at a page size ≥ Python's. When Python overflows, the page overflows or fits only within Python's margins.
  - A word wider than the box at every size is read as broken, and Python's size is at most one 4 px step below the page's.
- **Indic: the 8% premise does not hold.** Measured in Chromium against Pillow's basic layout, over every consonant × vowel sign and consonant + virama + consonant of the five scripts, in Noto Sans and Anek, the misses go both ways. Conjuncts are drawn up to 75% narrower (Kannada `ತ್ಝ`; the Devanagari median is −19%, Telugu and Kannada −42% to −59%). Some syllables are drawn far wider (Kannada `ಕೋ` 3.1×, Devanagari `रू` 1.9×, Tamil `டீ` 1.17–1.27×, Malayalam up to 2.2×). No slack bounds that. **Owner decision (2026-10-14):** Indic text fit will use HarfBuzz shaping (`uharfbuzz`, with `fontTools` and `brotli` to read WOFF2), built as the step after B2b. Until then Indic fits are estimates: advisory for B2d's chunker and B2e's lints, never blocking, and never stored as a size cap. The same step may replace the Latin kerning table with shaped measurement (a proposal, not decided). *(B2b-H: done for every script. The advisory rule is lifted for certified Indic fits, and the kerning table is retired.)*
- **Pillow and WOFF2.** Pillow 12.3.0 with FreeType 2.14.3 (no raqm) reads all 77 vendored face files on the Windows development machine, Anek's variable axis included. The fast suite pins every face's sample advance (`tests/compose/dynamic/test_textfit.py`, and `test_faces.py` opens each face), and it runs in the CI Python job and in the container's fast suite. **The Debian image check is still open:** the container job has never run. If Debian's FreeType cannot read WOFF2 or gives other advances, that test fails; then pin per platform, or have `vendor.mjs` also store TTF copies for measurement. *(B2b-H: no longer applies to text fit, which reads no face through Pillow. `test_faces.py` still opens each face with Pillow, which is now only a readability check.)*
- **Deviations** (B2b): the 0.06 em per-word floor and the kerning table beyond the plan's 4%, which alone was not conservative; "within 4% slack" in plan §1.11 is read as "within Python's margins". Indic text, combining sequences, uncovered characters and unbounded Latin runs are not certified. `fit_slot` takes a `width` override where a template draws text narrower than its slot (`board_generic`'s kicker sits 30 px right of the header's left edge). *(B2b-H: the floor and the table are retired, and the margin is now 0.5% + 1/64 px. The `width` override stays.)*

*As built (B2b-H): text fit shaped with HarfBuzz* (`aadhi/compose/dynamic/shaping.py`, new; `textfit.py`, rewritten; `docs/plans/b2/b2b_h_review.md`). Still not imported by any production path: `direct.py`, the lints and the caption chunker wire it in B2d/B2e.
- **Owner decisions.**
  - 2026-10-14: text fit uses HarfBuzz shaping, with three pinned Python dependencies: `uharfbuzz`, `fontTools` (WOFF2 decoding) and `brotli`.
  - 2026-10-15: shape every script, Latin too, provided a Chromium parity probe confirms that the shaped widths are what the page draws. If parity holds, retire the B2b kerning table. If it fails for a script or face, keep the closest correct design there and report the evidence.
  - No many-minute measurement in CI; long re-measurements are opt-in through an environment variable.

  Parity held for every vendored face, weight and script the stage draws, so every script is shaped and the kerning table is retired.
- **Shaping** (`shaping.py`), mirroring Chromium; the parity probe measured each point:
  - **Face files.** Each WOFF2 file is decoded once per process with fontTools (and brotli), using the steps of `fontTools.ttLib.woff2.decompress`, and kept in memory as sfnt.
  - **`unicode-range`.** Each font is a HarfBuzz sub-font whose glyph lookup is the cmap restricted to the face's ranges, as Chromium's is. The subset files map more than their ranges: Outfit's `latin` file maps U+0301 and U+0102, and the Indic files map the space.
  - **Variable axes.** The file's axes must be the face table's. `wght` is always set from the CSS weight (clamped to the face's range; Anek's file default is 500).
  - **Features.** HarfBuzz's defaults, which are Chromium's, plus `kern`, `liga` and `clig` from `dynamic.css`. When the letter-spacing is not 0, Chromium drops `liga`, `clig` and `calt` (measured), and so does textfit. A slot drawn with `font-variant-numeric: tabular-nums` (`board_generic`'s countdown ring; `SlotSpec.tabular_nums`) is shaped with `tnum`.
  - **Shaper.** HarfBuzz's OpenType shaper `ot` is always named. Without that, `HB_SHAPER_LIST` could select `fallback` (no GSUB or GPOS) or, on Windows, `uniscribe` or `directwrite`. A refusal is a `FaceError`.
  - **Script runs.** Runs are split as Chromium's `ScriptRunIterator` splits them (ported): Script_Extensions in ICU's preference order, paired brackets, and Common and Inherited characters taking the run's script. So a word made only of digits and U+202F NARROW NO-BREAK SPACE (`1 000`, `5 %`, `₹ 100` written with it) is a Mongolian run on its own.
  - **Language.** The page's `lang`: the board language, else `en`, as BCP 47. Marathi's `locl` forms are reproduced: ल is 0.678 em in `hi-IN` and 0.699 em in `mr-IN` in Noto Sans Devanagari.
  - **Direction.** Left to right.
  - **One Unicode database** for the text model. Python 3.11's `unicodedata` is Unicode 14.0, so `NEWER_UCD` adds the characters the vendored faces draw that it does not know: U+0CF3, a combining mark since Unicode 15, and U+11B00–11B09. A test checks that every character a vendored face draws has a known category.
  - **Units.** 65,536 units per em, so widths scale linearly with the font size, as the page draws them unhinted.
  - **Fail closed.** Each of these raises, never falls back silently: a file fontTools cannot decode, a face in which HarfBuzz finds no glyphs or no cmap, axes other than the table's, a variable face given no weight, a refused shaper, a missing `brotli` (a `FaceError` naming it), and a missing `uharfbuzz` or `fontTools` (`ImportError`).
- **Chromium's font fallback** (`textfit.shape_text`). Each script run is shaped with the stack's faces in order. A face keeps the HarfBuzz clusters it draws with no missing glyph, as it shaped them, and leaves the rest to the next face. What no face draws is drawn by a system font (`uncovered`). Kept as shaped includes a face's kerning against a glyph it lacks: Atkinson 700 draws `ŶA` 3 px narrower than `Ŷ` and `A` apart at 100 px. Before this was mirrored, residuals reached 10 px (`Ă`) and 5 px (Atkinson mixed `latin` / `latin-ext` words).
- **Letter-spacing.**
  - It is added after every HarfBuzz cluster that draws something, as Chromium's `ShapeResultSpacing` does. A Tamil consonant + virama + consonant takes it twice; a cluster that is only a ZWNJ takes none.
  - Inside a run of a cursive script, only a space takes it. So the U+202F number words above are drawn without it: at −0.015 em, `1 000` is 7.5 px wider at 100 px than with it.
  - The spaces between words take it when it is positive, and leave it out when it is negative (never under-estimated).
- **Words, whitespace and lines.** These are kept from B2b, with these changes:
  - **Collapsing.** `collapse()` makes each *maximal* run of collapsible whitespace one space. A zero-width or other space between two spaces keeps both: `x` space U+200B space `y` is 28.1 px wider than `x y` in Inter 400 at 100 px. A line feed next to a zero-width space draws nothing, together with the spaces around it. A form feed or vertical tab is drawn (about 0.9 em), not collapsed.
  - **Flow text** (kicker, subtitle, card and body text) is one text node on the page. Every word is measured both as its own text node and inside the whole text (`Word.readings`, because the script runs can resolve differently). The conservative width takes the wider reading and the optimistic width the narrower.
  - **Words joined by only zero-width spaces** (`joins()`) are shaped together too, because HarfBuzz skips U+200B: Atkinson 400 draws `q` U+200B `j` 13 px wider than apart. The conservative whitespace between two such words that share a line adds what the pair draws wider across it (`Word.join_em`). A chain of three or more is certified only when no part of it draws wider than its pairs add up to (`Measured.joins_bounded`; Inter's `A` U+200B `-` U+200B `*` does). Chains longer than `JOIN_WORDS` (24) are not certified.
  - **Over-wide words.** A title word is its own inline-block. A word wider than the column at the minimum size breaks after a hyphen first, else between grapheme clusters. Each piece is shaped on its own with its word's script runs, as the page shapes a line's part of a word.
- **Margins, re-derived from the probe.** The B2b slack (4% Latin, 8% Indic), the 0.06 em per-word floor and the kerning table are replaced by `SLACK` = 0.5% of the shaped advance plus `BOX_PX` = 1/64 px (Chromium's layout unit: an inline box's width is rounded up to it).
  - Conservative width: `size · (em · 1.005 + tracking · spacings) + 1/64`, taking the wider reading.
  - Optimistic width (used to decide that an over-wide word certainly breaks): `max(0, size · (em · 0.995 + tracking · spacings))`, taking the narrower reading.

  Over every probe run, the page drew a word at most 0.0531 px from its shaped width. Beyond the layout unit, that is at most 5.28e-4 of the width (0.053%), so 0.5% is about 9 times what the development machine needs. The rest is kept for what that machine cannot show: Linux Chromium's glyph advances, and a Chromium or HarfBuzz release whose shaping moves. The slow probe re-measures both, and runs in the container.
- **The parity probe** (`tests/determinism/test_textfit_parity.py`, slow).
  - **Setup.** The real `/render-dynamic` page with the stage's own CSS, font stacks and `lang`; Playwright 1.63 Chromium on Windows 11. Each text is an inline-block under `white-space: nowrap`, which shapes as the stage's `normal` does. `pre` breaks the kerning across a ZWNJ, and the stage never draws it.
  - **What it asserts.** For every certified word, the page width lies between the optimistic and conservative widths, and `|page − shaped| ≤ 1/16 px + 5e-4 × width`.
  - **What it measures:**
    - every vendored face and weight the stage draws, with each slot's letter-spacing: the display families at 700 and 900 and also at 400 and 600 (a quote's text, a stat's unit), the body families, JetBrains Mono, and Anek at the `wght` 800 a 900 bold run is clamped to;
    - the B2b review's runs and 1,500 random runs per Latin style;
    - every consonant × vowel sign and consonant + virama + consonant of the five Indic scripts in Noto Sans and Anek: 9,487 syllables, Devanagari as `hi-IN` and `mr-IN`;
    - `test_textfit_page`'s strings (typical, longest, very long word, Tamil, Hindi, mixed Tamil + English);
    - tabular numerals;
    - whitespace widths and line breaks;
    - flow lines;
    - words joined by zero-width spaces.
  - **Results (residual = page − shaped, px):**

    | Run | Latin max / median | Indic max / median | Other |
    |---|---|---|---|
    | Build, default (each style at its smallest and largest slot size) | 0.0510 / 0.0067 over 82,260 widths | 0.0321 / 0.0076 over 252,660 | — |
    | Build, extended (every 4 px size, 6,000 random runs per style; 192 s) | 0.0531 / 0.0086 over 1,953,627 | 0.0322 / 0.0077 over 1,010,640 | — |
    | Fix round 1, default (styles added) | 0.0500 / 0.0070 over 109,118 | 0.0321 over 303,192 | tabular 0.0130 over 210; beyond the layout unit ≤ 3.65e-4 of the width |
    | Fix round 2, extended (456 s) | 0.0522 over 2,203,799 | 0.0324 over 1,667,556 | 3.87 M widths in all |
    | Fix round 3, default | 0.0510 | 0.0321 | flow lines: Python − page 0.93–124.8 px, never negative; zero-width-space joins: 8,227 certified, 9 not, 0 violations |

    Per style, the maximum is 0.011–0.051 px; medians are 0.001–0.010 px everywhere. Every Indic syllable is drawn by its vendored face.
  - **Run times.** The default probe takes about 2 minutes (Latin 35 s, Indic 50 s) and runs in the slow suite, so in CI's slow and container jobs. The extended probe (`AADHI_TEXTFIT_PARITY_FULL=1`, 5.5–7.5 minutes) runs on demand only (`docs/OPERATIONS.md`).
- **What is certified** (`FitResult.fits`):
  - **True or False:** Latin, Indic and mixed text, combining sequences a vendored face draws, and the runs B2b left uncertified (`step-by-step`, `f(x)`, `10x10`, `ffiw`). True means the page fits too; False means the page overflows, or fits only within Python's 0.5% + 1/64 px. B2b's Indic rule ("advisory, never store an Indic size") is lifted: B2d and B2e may treat a certified Indic fit like a Latin one, and B2e may store its `stored()`.
  - **None (an estimate; a lint must not block on it):**
    - a cluster, or part of a grapheme cluster, that no vendored face draws (Greek and maths symbols such as π, Ω, √);
    - whitespace no vendored face draws (U+3000, U+1680, U+2028, U+2029, form feed, vertical tab);
    - whitespace the page does not break at as the line model does (`plain_separator()`): an en, em, thin or other typographic space, the BOM, or a space before a zero-width space, between, before or after the words. So `5 %` written with a thin space (U+2009) is not certified;
    - a word that starts with a combining mark that is not default-ignorable;
    - a word holding a character that can change the bidi embedding level (`splits_bidi()`: classes R, AL and the embeddings, overrides and isolates with their terminators). This is uncertified, not modelled; the left-to-right mark U+200E stays certified;
    - a chain of words joined by zero-width spaces that is unbounded or longer than 24 words.
- **Known gaps: open after the review** (`b2b_h_review.md`). After the third fix round, the audit found two remaining ways text can be certified as fitting while the page draws more lines (it rated both as blockers):
  - **A zero-width-space pair at a line break in flow text.** The join gain is counted only when both words share a line. Chromium measures a line that ends at the zero-width space with the joined shaping, so it breaks one word earlier. Example: Atkinson 700 at −0.015 em, `xx q` U+200B `j xx` at 200 px: Python certifies 2 lines and the page draws 3.
  - **An over-wide word that starts with a hyphen.** The break after a leading hyphen (UAX #14 HY) is never taken in `_pieces`, so the page draws one more line than Python certifies. Example: Inter 700, `go -abcdefgh` at 300 px. The textfit docstring's claim that a break after a hyphen "only gives the page fewer lines" is wrong for a leading hyphen.

  Both must be fixed, with an audit, before B2b-H is committed as final. Also open:
  - Flow text is measured word by word. That keeps Python's line count conservative: up to 125 px on a long 148 px line, while the real slots are 40–56 px.
  - For an over-wide word, Python's stored size can still be one 4 px step below the page's.
  - Linux Chromium is unmeasured.
- **Retired:**
  - `kerning.py`, `kerning.json` (2.88 MB; 85–90 minutes to regenerate), `tests/compose/dynamic/test_kerning.py`, and `tests/determinism/test_kerning_table.py` with its `AADHI_KERNING_REMEASURE`. The review's measured page widths moved into the fast suite as parity cases.
  - Inside textfit: the Pillow measurement (`load_face`, the `.notdef` probe, the FreeType lock, `REF_PX`, `PROBE_PX`), `SLACK_LATIN`, `SLACK_INDIC`, `KERN_FLOOR_EM`, `tracking_count`, `advance_em`, `kern_bounds` and `Word.kern_*`.

  textfit no longer imports Pillow. Pillow stays a dependency, because other code uses it.
- **Dependencies** (`requirements.txt`, "Dynamic video text fit" block; `requirements.lock` regenerated by `scripts/lock_requirements.py`, which reported no Linux gaps and needs no `PLATFORM_PINS` entry):

  | Package | Bound | Locked | Licence | Wheels (CPython 3.11) |
  |---|---|---|---|---|
  | `uharfbuzz` | `>=0.56.2,<0.57` | 0.56.2 (2026-09-21) | Apache-2.0; bundles HarfBuzz 14.5.0 ("Old MIT"), whose COPYING the wheel does not include | cp310-abi3, `win_amd64` and manylinux2014 / _2_17 / _2_28 x86_64 |
  | `fonttools` | `>=4.66,<5` | 4.66.1 (2026-09-29) | MIT, "Copyright (c) 2017 Just van Rossum" (also ships `LICENSE.external`, the OFL text of its test fonts) | cp311 `win_amd64` and manylinux2014 x86_64, plus `py3-none-any` |
  | `brotli` | `>=1.2,<2` | 1.2.0 (2025-11-05) | MIT, "Copyright (c) 2009, 2010, 2013-2016 by the Brotli Authors" | cp311 `win_amd64` and manylinux2014 x86_64 |

  - **Requirements.** None of the three has required dependencies. `uharfbuzz` is bounded to the 0.56 series because a minor bump can change shaping. Re-run the extended parity probe before raising it, or after a Playwright (Chromium) bump.
  - **Docker.** The image's pydeps stage installs `-c requirements.lock -r requirements.txt`, so it picks up all three. The image was not built.
  - **Side effect.** Installing `brotli` makes `httpx`, `urllib3` and `aiohttp` import it eagerly, advertise `br` in `Accept-Encoding` and decode brotli responses transparently (nothing in `aadhi` reads a raw body).
  - **Notices.** The Python dependencies' notices are a gap for the owner (§4.12).
- **No visible change.** Nothing outside `textfit` and `shaping` imports either module. The diff touches no `web/` file, `direct.py`, preflight, `presentation.py` or schema. `SlotSpec.tabular_nums` is Python-only: the catalogue `--check` passes. Classic isolation (`tests/compose/test_dynamic_isolation.py`, fresh interpreter):
  - A classic build loads none of `uharfbuzz`, `fontTools` or `brotli`. `brotli` is excused only beside an HTTP client that imports it.
  - A Dynamic build loads neither `textfit` nor `shaping` yet.
- **Deviations** (B2b-H):
  - Plan §1.11's "within 4% / 8% slack" now reads "within 0.5% + 1/64 px".
  - Plan §2 "Fonts": "Layout.BASIC … pin advance widths" is now HarfBuzz shaping, with the shaped widths of all 77 face files pinned in the fast suite. These are the same on every platform: no Debian FreeType dependency.
  - The new field `SlotSpec.tabular_nums`.
  - Bidi controls and non-plain whitespace are uncertified rather than modelled.
  - Chromium's East Asian segment-break removal is not modelled. A test pins that no vendored face draws an East Asian wide character, so text it affects is uncertified anyway.

### 4.7 Colour system

Colours are generated in OKLCH, clamped to the sRGB gamut and exported as hex for the page and for Python [CP §9.2]. They come in two namespaces that never mix [RV L-3].

**Content colours (`content.*`): fixed in every video, by mode.** They carry meaning, so they never rotate. Values were computed this session (scratchpad `rev/palette.py`, `rev/search.py`):

| Role | Dark families | Light families | Contrast (worst background in the mode) | Use |
|---|---|---|---|---|
| `ink` / `ink2` | L ≥ 0.96 / 0.78 | L ≤ 0.22 / 0.42 | ≥ 7:1 / ≥ 4.5:1 | All text, current/past states |
| `entity:0` (blue) | `#0FE3FE` (H 210) | `#0267C7` (H 255) | 8.1 / 4.8 | Formula symbols, chart series, compared entities: **one lesson-wide mapping** |
| `entity:1` (violet) | `#C4A4FE` (H 300) | `#80369E` (H 315) | 6.1 / 6.1 | same |
| `entity:2` (rose) | `#FEACE3` (H 340) | `#870C62` (H 345) | 7.4 / 8.0 | same |
| `warn` | `#F8A87F` (H 48) | `#9F4503` (H 48) | 6.6 / 5.4 | Errors, myths; always with an icon |
| `ok` | `#5DD8AA` (H 166) | `#016F50` (H 166) | 7.2 / 5.3 | Correct answers, facts; always with an icon |
| `mark` | `#F2D935` marker under dark ink | the same yellow at 35% under `ink` | ink on mark ≥ 12:1 | `cue.term` highlighter, caption key terms |
| `focus` | `mark` | `ink` | ≥ 3:1 (graphics) | `cue.lookHere` outlines |
| `info` | — | — | — | No hue: info callouts use `ink2` with an "i" icon, which frees the blue band for entities |

- **Hue separation.** Every entity hue is ≥ 30° from `warn` (48°), `mark` (95–100°) and `ok` (166°) and from the other entities. With `info` neutral, the only space left is blue to rose, so the palette has **three** entity slots, within the review's "at most 4".
- **Colour vision.** Under simulated protanopia, deuteranopia and tritanopia (Machado 2009 matrices), the worst entity-to-entity OKLab distance is 0.051 (dark) and 0.068 (light), the best a grid search over the allowed hues (5° steps) and the lightness band found; with four slots it falls to 0.048. Entity colour is therefore always redundant: symbols keep their letters, series are direct-labelled, and compared columns have headers and icons (R-53).
- **Mapping.** `Presentation.entity_colors: dict[str, int]` maps a normalised symbol or entity name to a slot for the whole lesson: formula variables first (by first appearance), then compared entities. A fourth quantity stays `ink`. The same entity keeps the same colour in every scene (consistent mapping) [LS §9].

**Structure colours (`struct.*`): rotate.**

| Token | Dark mode | Light mode | Rotates? |
|---|---|---|---|
| `bg` | L 0.17–0.24, C 0.02–0.06, H = family hue | L 0.95–0.98, C 0.01–0.03 | per video (family) |
| `bg-alt` / `bg-tint` | L +0.04 / accent hue at C ≤ 0.07 | L −0.03 / C ≤ 0.03 | per scene (variant) |
| `surface` | L 0.27–0.32 | L 0.99–1.00 | per video |
| `struct.accent` | L 0.74–0.80, C 0.13–0.19, H = h0 + Δk | L 0.52–0.60, C 0.14–0.20 | per chapter (16:9) or act (9:16), ≥ 30° apart |

`struct.accent` is used **only** on structure: chapter bumpers, the intro wipe and colour-block hooks (saturated), progress pips and card borders (4–6 px), and the background tint (C ≤ 0.07 on content scenes). It never colours an item, node, row, bar, tile, symbol or caption, so an orange accent can never be mistaken for `warn` [RV L-3].

**Twelve families** (R1 ships the four marked ✓; hues in OKLCH degrees). Because the accent only colours structure, accent windows may overlap content hues without changing meaning.

| # | Family | Mode | bg H | Accent window (h0) | Default personality | R1 |
|---|---|---|---|---|---|---|
| 1 | Midnight Indigo | dark | 275 | 40–110 | Cinematic → Soft in R1 | ✓ |
| 2 | Deep Teal | dark | 200 | 0–70 | Crisp | ✓ |
| 3 | Aubergine | dark | 320 | 140–210 | Cinematic | |
| 4 | Charcoal Math | dark, C ≈ 0.01 | 260 | 200–280 | Chalk | |
| 5 | Forest Night | dark | 155 | 20–90 | Soft | |
| 6 | Cosmic | dark, C 0.07 | 285 | 30–70 / 170–210 alternating | Cinematic | |
| 7 | Paper & Ink | light, cream | 85 | 0–70 | Editorial → Crisp in R1 | ✓ |
| 8 | Blueprint | light grid | 230 | 230–290 / 30–70 alternating | Crisp | |
| 9 | Mint Studio | light | 170 | 270–340 | Soft | ✓ |
| 10 | Sunrise | light | 60 | 260–330 | Bouncy-soft | |
| 11 | Chalkboard | dark green, L 0.28 | 150 | 60–130 | Chalk | |
| 12 | Bold Blocks | dark body + colour-block hooks and bumpers | any | any | Crisp | |

**Per-video rotation (`direct.py`).**
1. Filter families by the teacher's mode preference, the audience and the institution's brand kit (§9.8).
2. Exclude the families used in the teacher's last min(4, eligible − 1) lessons (R-51, history in §5.3).
3. Choose with `rng(look_seed)`, unless the teacher saw and kept a preview (§9.6).
4. Set each chapter's or act's accent to `h0 + Δk`, alternating ends and middle of the window so neighbours differ by ≥ 30°.
5. Run the background-variant sequence (§4.5), then validate.

**Validator** (`aadhi/compose/dynamic/themes.py` → `web/dynamic/themes.json`, CI) [RV L-3]:
- every content colour against every background, surface and preset worst case of its mode (R-53), including the measured Aadhi studio wall;
- every pair of content colours for hue separation (≥ 30° between all fixed and entity hues) and simulated colour-vision distance (≥ 0.05 OKLab); the content set is fixed, so this is computed once and pinned by a test;
- every structure accent against the text drawn on it (white or ink on bumpers, ≥ 3:1 for large text);
- a failing structure pair is nudged up to ±0.06 L; content colours are never nudged — a failure there fails the build.

**Image harmonisation** [RV L-17, E-23]. Generated image prompts get a style suffix from the personality and family ("flat vector illustration, deep teal and coral palette, soft lighting"), **except** when the writer sets `colour_is_content` or the image role is `diagram` or `photo`. The suffix and the Manim background come from `Presentation.asset_style`, the family and personality stored **at generation**, so "New colours" never regenerates pictures or Manim clips. "Restyle pictures" is a separate, paid action that shows its cost first.

**Flash safety** follows R-54.

*As built (B2a)* (`aadhi/compose/dynamic/colour_math.py`, `themes.py`, `catalogue.py`; `compose/presentation.py`):
- **One colour module.** `colour_math.py` is the only colour arithmetic in Python: OKLab/OKLCH with Ottosson's matrices, the CSS Color 4 gamut mapping, JS rounding, WCAG contrast, compositing, OKLCH mixing and desaturation, exactly as `web/js/motion/color.js` and `core.js` compute them; `colour.py` and `presentation.py` use it. `tests/fixtures/colour_vectors.json`, written from the JS by `scripts/gen_colour_vectors.mjs`, is checked by both test suites, so a drift on either side fails.
- **The catalogue instead of `themes.json`** (deviation 1). `python -m aadhi.compose.dynamic.catalogue` generates `web/js/dynamic/catalogue.gen.js` (version 1), a sorted, byte-deterministic ES module frozen at every level (`--check` in CI): zones, regions, grid, platform zones, type floors and scale, layout tokens, caption styles (geometry under `metrics`, colours apart under `colours`), cue colours per mode, the template, background, transition and personality registries, the class table, the role list, the forced-scrim table, the families and content palettes, and `studio_surface` (null until B2f measures the studio wall). Its runtime use is geometry only: `core.js` is its one reader, and the source scan allows only reads of the zones, regions, grid, end-screen box, platform zones, type, layout tokens, caption metrics, template slots and the vocabularies. Colours, durations and personality values reach the stage only through the timeline, so a stored timeline keeps its look when the catalogue changes.
- **Validator** (`themes.validate()`, pure): no finding on the released families or the content palette (pinned by a test). Worst measured values: hue separation 39.9° (dark) and 30.2° (light); colour-vision distance between entities 0.051 (dark) and 0.068 (light), as stated above; ink on mark 12.7:1 (dark). In light families `entity:0..2` and `warn` are restricted to text ≥ 32 px (§7.5 chroma rule). R-55's behind-text luminance ratio ≤ 1.5 is reported as an advisory, not a finding, because it depends on how a preset composes its colours: from its pure colours (a conservative bound) `mesh_drift` reaches 1.54 on midnight_indigo and 1.60 on deep_teal; B2f's per-preset R-55 tests own it.
- **Stored structure colours.** From engine 2 the resolver stores ten tokens per scene in `look.tokens`: `struct.accent`, `bg`, `bg-alt`, `bg-tint` and `surface` (B1's five, unchanged), and `block.base`, `block.highlight`, `block.ink`, `block.ink2` and `scrim` (what the B1 stage derived at run time). The stage reads each stored one and derives a missing one as B1 did, so an engine-1 timeline draws as before. Three rules apply on top, in Python only: a full-frame block in the saturated-red range gets its chroma lowered (R-54: paper_ink slot 2, block `#C44323` → `#B05641`; on W2, blueprint slots 1 and 5); block text is checked through the hold desaturation too (R-55); and an accent whose block text misses 3:1 may be nudged by up to ±0.06 L (no family needs it today).
- **Block text** (the owner's decision on the A1 review, `docs/plans/b2/a1_review.md`). `block.ink` is the stage's own choice (`inkOn` over the shown block), so no block flips between white and near-black text; only if that choice missed 3:1 during a hold would it be made over the shown and the held colours (never the case in the catalogue). `block.ink2` must hold 3:1 both shown and held: where the B1 stage's ink2 falls to 2.96–2.998:1 during a hold, the stored ink2 is one mix step nearer the ink (≥ 3.17:1). Against the B1 stage this changes, at every slot of the same residue mod 3 (the accent period): midnight_indigo slot 0 ink2 `#F2E5EA` → `#F6EDF1` and slot 2 `#F4E4E5` → `#F8EDEE`; deep_teal slot 0 ink2 `#EAE7F2` → `#F1EFF7`; paper_ink slot 2 the red-lowered block (highlight `#CC593D` → `#BA6853`, ink2 `#F8DBD3` → `#F2DDD7`, ink stays white). On W2 the same rule changes forest_night 1, cosmic 2 and 4, and bold_blocks 0. These colours show only on colour-block scenes (chapter cards, hooks) and in the crossfade out of them.
- **Rotation.** A scene's palette slot is `SceneLook.palette_slot` when set, else the index of its chapter in `Timeline.chapters`, found by the kicker's rule (the last chapter starting at or before the scene, 1 ms tolerance). This fixes a B1 mismatch: B1 read only `Screenplay.chapters` and `scene.chapter_id`, so a lecture without them kept one accent for the whole video while its chapter list (from chapter cards or concept changes) showed several. Such lectures now change accent, tint, block colours and tone at those chapters; a first screenplay chapter that starts after scene 0 gives the opening chapter its own slot; consecutive chapters with the same title are one; a `chapter_id` missing from a non-empty `Screenplay.chapters` is ignored. Lectures with `Screenplay.chapters` (the usual case) keep every B1 token (pinned against B1's own output, `tests/compose/fixtures/b1_looks.json`).
- **Content.** `presentation.content_alt` carries the other mode's content palette (`roles.on(mode)`, for the dark code-editor surface of a light family); the editor surface colour itself (`themes.code_surface`) is not in the timeline yet (B2f, before `code_walkthrough` can draw it). `presentation.entity_colors` carries the lesson-wide map (deviation 22).

### 4.8 Motion tokens, cues and personalities

**Easing** (pure cubic-bezier solver in `web/js/motion/easing.js`):

| Token | Curve | Use | Source |
|---|---|---|---|
| `ease.enter` | `cubic-bezier(0, 0, 0.3, 1)` | entrances | IBM Carbon expressive entrance [CP S25] |
| `ease.exit` | `cubic-bezier(0.3, 0, 0.8, 0.15)` | exits | Material 3 emphasized-accelerate [MR §6] |
| `ease.move` | `cubic-bezier(0.4, 0.14, 0.3, 1)` | on-screen moves | Carbon standard expressive |
| `ease.hero` | `cubic-bezier(0.05, 0.7, 0.1, 1)` | wipes, big moves | Material 3 emphasized-decelerate |
| `linear` | — | counters, rings, drifts | — |
| `pop` | analytic spring with a single 1.08 overshoot | icons and "?" only; never text | [CP §6.2] |

**Durations:** micro 200–300 ms; entrance 400–600 ms; large move 600–900 ms; scene transition 400–700 ms (chapter wipe ≤ 800 ms); stagger 60–120 ms per item or word; count-up 800–1200 ms; stroke draw 600–1200 ms per group. Nothing moves faster than one screen width per second outside transitions [MR F23].

**The two cues** (`web/js/dynamic/cues.js`, implemented once; R-26) [RV L-11]:

| Cue | Look | Rules |
|---|---|---|
| `cue.term` | One style per video, chosen by the personality: a `mark` highlighter sweep (300 ms) behind the term, or a 4 px `mark`/`ink` underline drawn left to right | One per narrated key term, on its word; also drives the caption key-term colour (R-35) |
| `cue.look_here` | A 4 px `focus` outline (rounded rect, ring or bar) drawn around the target in 250 ms, other elements dimmed to 70%; or a camera push of 2–6% toward the target | Diagram parts, rows, bars, lines, nodes, code lines; punch-zoom (1.00 → 1.10 in 250 ms) counts as `look_here`, ≤ 1 per 20 s, never on formulas |

Both cues are anchored events. ≤ 2 cues concurrent; ≤ 1 new cue per 2 s; a cue never repeats on the same item within 4 s.

**Choreography grammar.** A reveal begins 120 ms before its anchor word (R-17); at most one camera move at a time; nothing else enters during a camera move; exits are 30% shorter than entrances.

**Motion personalities.** One per video, never one of the teacher's last 2 (when the pool allows). R1 ships **Crisp** and **Soft**.

| Personality | Duration scale | Text entrance | Transition family | `cue.term` style | Caption style (§4.11) | SFX pack | Music mood (§5.9) | Release |
|---|---|---|---|---|---|---|---|---|
| Crisp | ×0.85 | revealed from the baseline | cut / push / colour wipe | underline | `bold_outline` (9:16), `clean_pill` (16:9) | clean, short | `bright-curious` | R1 |
| Soft | ×1.15 | fade + 16 px rise | crossfade / iris | marker | `clean_pill` | soft bells | `calm-focus` | R1 |
| Cinematic | ×1.25 | opacity rise + tracking-in | crossfade / zoom-through | marker | `glow_outline` | deep, sub hit | `cinematic-wonder` | W2 |
| Editorial | ×1.0 | highlighter swipe, typewriter | cut / paper slide | marker | `marker` | paper, pen | `classical-focus` | W2 |
| Chalk | ×1.1 | stroke draw-on | cut / wipe | underline | `minimal_lower` | soft chalk | `calm-focus` (lo-fi) | W2 |
| Bouncy-soft | ×0.9 | pop with one overshoot (icons only) | push / iris | marker | `bold_outline` | soft pops | `bright-curious` | W2 |

**Reduced motion** [RV T-22]. When the viewer's device prefers reduced motion, the live player turns whips and zoom-throughs into crossfades, camera pushes and punch-zooms into the outline form of `cue.look_here`, and switches off parallax and Ken Burns. This is the second allowed live/MP4 difference (§7.3). The render option **Calm motion** (W2) applies the same mapping to the MP4.

**As built (B1): emphasis inside a title.** A Dynamic quiz question keeps its rich-lite markup (TeX, `**bold**`, `[[term]]`, code), drawn word by word by `board_generic`. Emphasis there is by weight only: `**bold**` is drawn at weight 900 against the 700 title (`web/css/dynamic.css` `.dyn-title .rt-b`; Outfit 900 is vendored and preloaded by the render page). With the Inter display face, and in Indic scripts, whose heaviest vendored face is 700, it falls back to that 700 face without a synthesised bold, so there it does not stand out. A `[[term]]` in a title keeps the title's weight until B2's `cue.term` (`cues.js`) draws term emphasis; templates must not add colour or highlight emphasis of their own.

**As built (B2-0 and B2a).**
- *Transform policy* (the B2-0 spike, deviation 12; `docs/plans/b2/b2_plan_v2.md` §P). Measured on Windows with `--disable-partial-raster`, 90 frames per case rendered 1×90, 3×30 on fresh pages and in reverse, CDP JPEG SHA-256: scaled text 0.90–1.10, an SVG icon popping to 1.08, a `rotateX` 90° → 0 card, an `<img>` downscaled 0.96–1 from a larger source, a grain tile scaled ±1%, a canvas `drawImage` crop, MathJax `\color` under `ui/safe` (the fills addressable as `fill` attributes), Chart.js with `animation: false` and `update('none')`, and a `background-clip: text` marker sweep gave 0 mismatches each. The scale and 3D motion of this section is therefore allowed as specified (camera push, punch-zoom, settles, media 0.96 → 1, Ken Burns, flips, icon pops, background breathing); B1's "no scale for moving cards" (§7.4) was a symptom of partial raster. Conditions: a picture only ever downscales, from a source at least its largest displayed size; translates stay on whole pixels; every template fixture runs the 1×/3×/reverse gate. The Linux (container) confirmation is open.
- *Personalities* (`themes.py`, 6 rows): Editorial has both text entrances (highlighter swipe, typewriter); Bouncy-soft's `pop` is marked icons-only (never text; its text entrance is settled with its W2 release); a released personality's R1 transitions are kept apart from its family (Soft: cut and crossfade, iris being W2). `presentation.motion_scale` is the personality's duration scale (Crisp 0.85, Soft 1.15; left out of the JSON at 1.0). An unreleased or unknown personality takes its family's R1 default for the stage (Cinematic on Midnight Indigo → Soft, 1.15) while `presentation.personality` keeps the requested id, as in B1 (for the owner's review).
- *Cue colours* per mode (`marker_ink`, `marker_alpha`, `underline`), the dim alpha (0.7) and the scrim alpha (≥ 0.55; the stage uses 0.78) are in the catalogue; `cues.js` arrives in B2c.

### 4.9 Transitions

All transitions are rendered in the page. During `[0, d)` of the incoming scene B, the stage paints the final state of scene A (a pure function of time) with B's state and combines them. The timeline time base is unchanged, because the fade stays inside the incoming scene [CB §1].

| Transition | Means | Duration | Where allowed | Release |
|---|---|---|---|---|
| Hard cut | The same thread continues | 0 | Default inside a chapter (≥ 50%, R-23) | R1 |
| Crossfade | Soft continuation | 300–500 ms | After reflective beats; Soft | R1 |
| Push / slide (direction = progress) | "Next step" | 450–550 ms | Process, list and timeline continuations | R1 |
| Colour-block wipe | **New chapter or act**: the accent changes here | 450–600 ms | Only into `chapter_bumper` and out of `aadhi_intro` | R1 |
| Shared-element morph | The same object persists | 600 ms | Formula → `worked_steps`; a `builds_on` anchor across a bumper; `continues_from` | R1 |
| Iris | Spotlight, focus | 500 ms | Into a hook; answer reveals | W2 |
| Zoom-through / match | "Look closer" | 600–800 ms | Consecutive scenes sharing an anchor | W2 |
| Whip (3-copy opacity trail) | Topic jump | 350 ms | ≤ 2 per 16:9 video, ≤ 4 per reel; never into formula or code (R-24) | W2 |

Stylised transitions are ≤ 30% of boundaries (16:9) or ≤ 45% (9:16); the same stylised transition never appears twice in a row; full-frame colour changes cross-fade over ≥ 300 ms (R-54). Only the chapter wipe carries a sound (R-49).

*As built (B2a)* (`compose/presentation.py`). The resolver stores a crossfade's length as clamp(scene / 2, 0.3 s, 0.45 s) (`crossfade_seconds`), and a scene shorter than 0.3 s would get a cut instead. That branch is unreachable today (every scene lasts at least 0.5 s), and `direct.py` and B6's clip handling must never give such a scene a colour block, which R-54 requires to fade. The stage already clamped every crossfade to 300–500 ms and to its scene (`transitions.js`, B1), so the stored value changes nothing on screen (B1 stored d / 2 for scenes shorter than 0.9 s, which the stage raised to 0.3 s below 0.6 s). The choice of transition is B1's: a cut into the first scene; a crossfade into and out of a colour block (R-54); else the teacher's cut or crossfade; else a crossfade where the structure accent changes and a hard cut inside a chapter (R-23). An accent change now also comes from a palette slot (§4.7): a palette-slot change inside a chapter cross-fades for 0.3–0.45 s, an interim rule (A2 proposal, pending the owner's review) until B2e's `accent_fade` gives R-52's 600–1000 ms accent fade. Push, colour wipe and morph arrive in B2f. The W2 personalities Editorial and Chalk name `paper_slide` and `wipe`, which this table does not have (recorded; settled with their release).

### 4.10 Sound design policy

All SFX are **synthesised in-house**, deterministically, in `aadhi/compose/sounds.py`, which already synthesises the tick and ding with numpy; they are claim-free (R-50). Each personality changes the synthesis parameters, seeded per video.

**Why so few** [RV L-5]. Moreno & Mayer 2000 found that adding sounds and music to a narrated animation reduced learning; to the reviewer's recollection the sounds were brief effects tied to animation events, close to what a per-reveal "pop" would be (unverified (M)). Phan 2023's d = 1.66 harm was for music plus environmental sounds together (read in full by the reviewer). LS itself recommended at most one SFX per 5 s. Revision 1's density (up to 75 SFX in a lesson) is therefore withdrawn.

| Event | Sound | Length | Level vs narration | Placement |
|---|---|---|---|---|
| Chapter bumper | Whoosh (band-passed noise sweep); a musical swell of +4 dB only with exposable music | ≤ 1 s | −14 to −18 dB | With the wipe, in silence |
| Answer reveal (check, recap, myth fact) | Chime (2-partial bell; exists as "ding") | 0.8 s | −16 dB | On the reveal |
| End of a countdown | One soft tick | 0.1 s | −22 dB | 150 ms before the reveal |
| Optional accent in explanation scenes (stat landing, a stylised transition) | Soft impact or short whoosh | ≤ 0.6 s | −18 to −24 dB | ≤ 1 per 20 s (16:9), ≤ 1 per 8 s (reels); never in formula, code or worked-example scenes |

**Removed:** the per-reveal pop, the "?" and VS pops, the myth and takeaway thuds, checklist clicks, code key ticks and the countdown riser. **Hard rules:** no SFX within ±150 ms of a narrated key term; no continuous ambience; render option **Sound effects: off** (and the Dynamic-lean pilot arm, §10).

### 4.11 Captions in the stage

**Chunking** (`aadhi/compose/dynamic/kinetic.py`) builds `TimedScene.kinetic_captions` from `TimedBeat.words` [RV L-7, T-4]:
- Chunks are **phrase units**: it reuses the line-break rules of `captions.py` and never splits article + noun, number + unit or auxiliary + verb.
- 9:16: ≤ 32 chars per chunk, laid out in ≤ 2 lines each measured to fit 660 px at the style's size (§4.6); a single word too long for one line at 56 px may shrink to ≥ 52 px; 1.5–3.0 s on screen (≥ 1.0 s hard); reading rate R-33. A chunk that is too fast is merged with its neighbour; one too long is split at a phrase boundary.
- 16:9 uses the existing phrase captions (`Timeline.captions`), drawn in the page when burned.
- **Key terms** (glossary `LexiconEntry` terms and `[[term]]` board terms) are flagged `key: true` only on their first two mentions in the video and never while the same term carries a board cue (R-35).
- **Blanking.** While on-screen text matches the concurrent narration (≥ 70% token overlap: `aadhi_intro` hook words, `hook_question`, `kinetic_statement`, `quote_card`), the chunk is suppressed; the on-screen text is the caption (R-31) [RV L-8].

**Styles.** One per video, chosen by the personality. All are **sentence case**, have **no per-chunk scale pop**, and have **no active-word emphasis by default** (the option "Highlight the spoken word" uses opacity, never colour) [RV L-7, T-21].

| Style | Look | Default for | Release |
|---|---|---|---|
| `clean_pill` | Sentence chunks; white, weight 600, on a translucent dark pill | 16:9 everywhere; 9:16 Soft | R1 |
| `bold_outline` | Weight 700, white with a 6 px dark outline | 9:16 Crisp | R1 |
| `highlight_box` | Key terms boxed in `mark` | 9:16 alternative | W2 |
| `marker` | Key terms get a `mark` swipe | Editorial | W2 |
| `minimal_lower` | Small phrase chunks low in the band | Chalk | W2 |
| `glow_outline` | Soft outer glow (pre-computed text shadow, no blur filter) | Cinematic | W2 |

**In-page rendering.** Captions are part of the stage, so the live player and the MP4 match. The MP4 never relies on libass for Dynamic videos. Sidecar `.srt`/`.vtt` files carry the phrase captions for accessibility tools and platforms. For the same reason the live player offers no caption-size choice for a Dynamic video (as built in B1, §9.7): its captions keep their format's size, as in the MP4.

### 4.12 Icons, illustrations, fonts and data

All are vendored (CSP `script-src 'self'`), pinned with SRI in `web/vendor/manifest.json`, with licences in `THIRD_PARTY_NOTICES` (Apache NOTICE files included) [MR §6]. *As built (B2b):* `THIRD_PARTY_NOTICES` exists and is generated (below); B1 had none.

| Asset set | Licence | Verdict | Use |
|---|---|---|---|
| **Lucide** | ISC (+ MIT for Feather-derived icons) | adopt (primary, R1) | Item icons from `icon_hint` |
| **Phosphor** (`@phosphor-icons/core`) | MIT | adopt (W2) | Fill and duotone weights |
| **Tabler Icons** | MIT | adopt (R1) | Coverage gaps |
| Material Symbols | Apache-2.0 | optional | — |
| **Fluent Emoji** | MIT | adopt (images, W2) | Reactions in 9:16, opt-in |
| Noto Emoji images | Apache-2.0 (fonts OFL) | adopt (images) | Fallback |
| **Open Peeps**, **Open Doodles** | CC0 | adopt (W2) | Characters in scenarios and the student role |
| Twemoji | CC-BY 4.0 graphics | avoid (attribution burden) | — |
| OpenMoji | CC BY-SA 4.0 | **avoid** (share-alike) | — |
| unDraw | Custom terms forbid adding to apps and automated download | **avoid** | — |
| LottieFiles free animations | Lottie Simple License; no compiling into a competing service | not in v1 | — |
| Natural Earth | Believed public domain, **verify** | future map template | — |
| Fonts | OFL 1.1 (verify each, §4.6) | adopt | — |

**Icon resolution is deterministic.** The writer emits `icon_hint` (one keyword, ≤ 24 chars, never translated). `aadhi/compose/dynamic/icons.py` matches it against an offline index built from Lucide's tag metadata, falling back to Tabler and Phosphor. No match means no icon. The LLM never names icon files.

*As built (B2b): icons* (`aadhi/compose/dynamic/icons.py`; `docs/plans/b2/b2b_review.md`):
- **Sets.** Lucide (`lucide-static` 1.48.0, published 2026-09-24) and Tabler (`@tabler/icons` 3.48.0, published 2026-09-22; outline style only) are exact-pinned runtime dependencies. Their licences, read from the packages:
  - `lucide-static`: ISC, "Copyright (c) 2026 Lucide Icons and Contributors", plus the MIT notice for its Feather-derived icons, "Copyright (c) 2013-present Cole Bemis".
  - `@tabler/icons`: MIT, "Copyright (c) 2020-2026 Paweł Kuna".

  The generator refuses to build unless those lines are in the files. Phosphor is W2 and not vendored, so "falling back to Tabler and Phosphor" is Tabler only.
- **One offline index, committed.** `python -m aadhi.compose.dynamic.icons` generates `aadhi/compose/dynamic/icon_index.json` (index version 1, about 1.37 MB; `--check` runs in CI). It is committed rather than built at vendor time, so the resolver, the image and CI never need `node_modules` at run time, and a library update shows as a reviewable diff. It holds each set's package, version and full licence text (the index is the vendored copy those licences travel with), every usable icon's path data, and the keyword maps:
  - Lucide: 1,844 icons, 263 deprecated names kept as aliases (`home` → `house`), 3,292 tags.
  - Tabler: 4,341 icons, 3,507 tags.

  Nothing is served from `web/vendor`: a timeline carries its own icons' geometry (`ResolvedLook.icons`, §6.7), so `vendor.mjs` copies no icon file.
- **Normalisation** (plan §1.2), failing closed:
  - Every element becomes path `d`: circles, ellipses, rects, lines, polylines and polygons become their SVG 2 equivalent paths. A strict SVG 2 path parser re-spells every path in one canonical form (exact decimals, no exponents, bounded numbers), so the schema's allow-list pattern and caps hold by construction, and `sanitise_icon` checks them again on any stored value.
  - DTDs, entities, processing instructions and CDATA are refused before the XML parser runs.
  - Any other element, an attribute outside the allow-list, a transform, a style, nesting, a fill or a changed root style excludes the icon, with its reason recorded in the index.
  - **Excluded:** Tabler's 376 Brand icons (trade marks), and every icon with a filled part: 46 Tabler icons and 11 Lucide names (palette, tag, key-round and others). Chromium strokes a small filled dot with a hole when it is drawn stroke-only (measured), and `LookIcon` has one `filled` flag per icon. Hints such as "palette" and "tag" fall through to Tabler's stroke versions.
- **Matching an `icon_hint`.** The hint is normalised (NFKC, case-folded, ASCII only). A hint holding a character other than letters, digits and word separators (`C++`, `C#`, `f(x)`), or whose key is one character, matches nothing. The key is tried as written, without hyphens, then as English singulars of its last word.
  - **Owner decision (2026-10-14): exact name first (either set), then Lucide tags, then Tabler tags.** The tiers are Lucide name or alias, Tabler name, Lucide tag, Tabler tag; in each tier Lucide comes first.
  - Where several icons share a tag, the index decides once, when it is built: an icon whose name negates its picture (a word `off` or `slash`, or a last word `x`) never stands for a tag; a reviewed preference list (`PREFERRED`: 21 Lucide and 3 Tabler entries) picks the icon for common teaching words; otherwise a fixed ranking decides. `IGNORED_TAGS` (9 Lucide, 10 Tabler) drops tags whose every icon would mislead a lesson (for example a shopping cart for "carbon", a credit card for "plastic").
  - No match gives no icon. A damaged index raises an error rather than silently giving no icon.
  - `PREFERRED`, `IGNORED_TAGS`, the negation rule, the singular rules and the refusal of symbol-bearing or one-character hints are B2b proposals, pending the owner's review. The vocabulary is English only: an Indic hint gives no icon, which matches "never translated". 211 common teaching keywords resolve to pinned icons; words such as "photosynthesis" or "kidney" get none.
- **The scene's map.** `scene_icons(items)` builds `ResolvedLook.icons` (board item id → icon) in item order, at most 32, from objects with `id` and `icon_hint` or from mappings with those keys; anything else raises `TypeError`. Each entry is A2's unchanged `LookIcon`: `{name, set, version (the package version), viewBox [0, 0, 24, 24], stroke_width 2, filled false, paths}`. Filling `look.icons` is B2e's `direct.py`. Drawing it is B2c's `templates/_shared.js` (plan §A step 3), which must sanitise again before drawing: the schema's `ICON_PATH_RE` and caps, `d` set with `setAttribute` (never `innerHTML`), the stored stroke width, round caps and joins, `fill="none"`.
- **Fidelity, measured in Chromium** (`tests/determinism/test_icon_pixels.py`, slow): all 6,185 indexed icons were drawn at 96 px from their stored paths and compared with the libraries' own files. The 5,430 path-only icons are pixel-identical. Of the 755 with a converted shape, 729 are identical, and the worst differs by 8 grey levels on 28 pixels (anti-aliasing of circles). Measured on Windows only; the container run is pending.
- **Deviations** (B2b): the committed index instead of icon files under `web/vendor`; filled icons excluded; no Phosphor fallback (W2); Lucide's deprecated file names used as aliases; Tabler's Brand category excluded.

*As built (B2b): `THIRD_PARTY_NOTICES`* (`scripts/gen_third_party_notices.py`; `--check` in CI after `npm ci`; copied into the runtime image at `/app/THIRD_PARTY_NOTICES`; LF forced by `.gitattributes`):
- **Packages.** It lists every runtime package of `package-lock.json` (every entry not marked `dev`) except type declarations (`@types/*`): 36 packages, namely the libraries, the packages they depend on, the 16 font packages and the 2 icon sets. Each section gives the exact version, the declared licence, the dependants, where its files are vendored, and the full text of every licence-like file it ships at its top level (Apache `NOTICE` files included: mathjs'; DOMPurify's two licences). `seedrandom` and `javascript-natural-sort` ship no licence file; their text is read from the README's licence section and the source header. The generator fails when a package is missing, at another version than the lock, or has no licence text (exit 2), and `--check` exits 1 when the file is stale.
- **What the vendored bundles hold** (added by the review, finding B2b-CONF-1 / LIC-1). Prebuilt browser bundles carry other projects' code that has no lock entry, so a reviewed table (`VENDORED`) covers every file `vendor.mjs` serves (a test walks `web/vendor`). It lists each third-party component inside them, 37 in all, with the version the bundle holds and its licence text, committed verbatim in `scripts/third_party_licences/`:
  - `p5.min.js`: `core-js`, `regenerator-runtime`, `opentype.js` (with `tiny-inflate` and `string.prototype.codepointat`), `libtess` (SGI Free Software License B 2.0, its notice taken verbatim from the libtess module inside p5's build), `gifenc`, `omggif`, `es6-promise`, `whatwg-fetch`, `fetch-jsonp`, `file-saver`, `buffer`, `base64-js`, `ieee754`, `process`, `path-browserify`, and code copied from stacktrace.js, Raphaël and Processing.
  - `three.core.js`: `earcut` 3.0.2 (ISC), a `paper.js` port (MIT) and Geometric Tools code (BSL-1.0).
  - MathJax: `mj-context-menu`, `speech-rule-engine` and `mhchemparser` (Apache-2.0).
  - Chart.js: the `@kurkle/color` it bundles (0.3.2). The DOMPurify bundle: a `regenerator-runtime` helper.
  - mathjs: its bundled dependencies at the bundle's versions (for example `decimal.js` 10.5.0, where the lock has 10.6.0). `vendor.mjs` now also copies `math.js.LICENSE.txt` beside `math.js`, so the bundle's banner resolves, and the notices quote it.

  Generation fails closed when a reviewed version drifts, a component's evidence disappears from its bundle, p5's module table names a package no component accounts for, or a copyright statement appears that the table does not acknowledge. A comment that only credits an algorithm or a book is recorded as a credit, not as licensed code.
- **Obligations to note:** p5 1.11.11 is LGPL-2.1 (vendored before B2b); `libtess` requires its SGI notice with every copy, and `p5.min.js` strips it, so the notices file is where it ships.
- **Open:** three of the committed component texts (omggif, stacktrace.js, the Node `path` notice), and libtess, are checked by a test against p5's own source comments. The others were read and look right, but no one in the review compared them with the published packages (the round-1 fixer did not download them, because a download needs the user's permission). The generator's docstring says each was checked against its source; that claim comes from the interrupted first fix attempt and has not been re-verified.
- **Python runtime dependencies (B2b-H): a gap for the owner.** `scripts/gen_third_party_notices.py` covers the npm packages and the files under `web/vendor` only. The Python packages the image installs have never been in `THIRD_PARTY_NOTICES`; this was true before B2b-H for the image's other Python packages too. B2b-H adds three:
  - `uharfbuzz` 0.56.2 (Apache-2.0). It statically bundles HarfBuzz 14.5.0, under the "Old MIT" licence, but its wheel ships only its own `LICENSE`, not HarfBuzz's `COPYING`.
  - `fonttools` 4.66.1 (MIT, "Copyright (c) 2017 Just van Rossum"; plus `LICENSE.external`, the OFL text of its test fonts).
  - `brotli` 1.2.0 (MIT, "Copyright (c) 2009, 2010, 2013-2016 by the Brotli Authors").

  Whether and how the notices should cover Python packages (and where HarfBuzz's own text comes from) is the owner's decision. The licences are recorded in §4.6 "As built (B2b-H)" and §7.13.

---

## 5. Audio

### 5.1 TTS providers and models

| Provider | In the repo today | Target | Voices | Native word timings | 5-min English lesson | Notes |
|---|---|---|---|---|---|---|
| **Edge** (`edge-tts` 7.2.8, LGPLv3) | Default provider until 2026-10-17, now the keyless fallback of OpenAI TTS (as-built note below); 24 voices in `voices.py` | Extend the catalogue (bn-IN, mr-IN, gu-IN, ur-IN and ≈ 25 English voices) in B3 | 322 live voices; en-IN has 2 speakers (Neerja, Prabhat) | Yes (`WordBoundary`) | $0 | Unofficial consumer service with no SLA or licence grant. **Development, demo and free tier only** if Q8 chooses Azure |
| **Azure AI Speech** | — | New adapter in **B3** when Q8 = Azure [RV E-27, T-7] | The Edge voices under licence, HD voices, SSML styles. en-IN: 14 standard + 6 HD voices (list fetched by the review) | Yes: Speech SDK `WordBoundary` events, or batch synthesis with `wordBoundaryEnabled` over REST if the SDK's licence or Linux dependencies are unsuitable (checked in B3) | ≈ $0.07; 0.5 M chars/month free | The licensed equivalent of Edge; recommended production default |
| **Gemini** | `gemini-2.5-flash-preview-tts`, **shuts down 17 Nov 2026** (`main:server.py:758`; v2 has used `gemini-3.8-flash-tts` since H0, `aadhi/config.py:180`) | **Hotfix H0 this week:** `gemini-3.8-flash-tts` for finals, `gemini-3.8-flash-lite-tts` for previews; `requirements.txt` floor `google-genai>=2.26` (3.8 TTS needs 2.25; Omni's Interactions API was verified against 2.26, the version the lock pins) | 30 prebuilt voices; 130+ languages | No → synthesise per sentence | $0.068 / $0.045; both double on 1 Jan 2027 | `speech_metadata.style` and pause tags. Terms: users 18+, apps "likely accessed by" under-18s excluded (Q3) |
| **OpenAI** | `tts-1-hd` | `gpt-4o-mini-tts` (snapshot `-2025-12-15`) in B3, with `instructions` | 13 | No | ≈ $0.075 | Input ≤ 2,000 tokens, so chunks ≤ 1,500. AI voice **must be disclosed** |
| **ElevenLabs** | `eleven_multilingual_v2`; key only from `.env` | `eleven_v4` / `eleven_flash_v2_5` in B3, voices from `GET /v1/voices`, **personal and server keys** (§5.7) | Voice library | `/with-timestamps`: probe on v4, else per-sentence | price row re-read in B0 | Free plan non-commercial: exports blocked on free-plan keys |
| Google Chirp 3 HD | — | B7; needs Google Cloud credentials (a service account), a new credential type — not the Gemini key | 30 voices in 9 Indian locales | No | $0.14 | |
| Sarvam Bulbul v3 / v4-flash | — | B7, after a terms review | 35+ / 200+ Indian voices; `enhi` code-mixing | Not documented | ≈ ₹14 | Indic specialist |
| Kokoro-82M | — | **Not adopted** | | | | Non-English text goes through espeak-ng (GPL) [AU §2.1] |

**As built (owner decision, 2026-10-17): OpenAI TTS is the primary voice.** `TTS_PROVIDER` defaults to `openai`
(`gpt-4o-mini-tts` on `/v1/audio/speech`; Edge was the default before). A job narrates with OpenAI when it has an
OpenAI key (the owner's personal key, else the server's); without one it narrates with the free Edge voice for the
lecture's language and one lecture-level `assets.tts_fallback` warning, never a failed job, and the up-front budget
check counts no OpenAI cost for it (`aadhi/pipeline/assets.py` `_Builder._tts_provider`, `aadhi/api/generation.py`
`_narrating_tts`, `aadhi/providers/factory.py` `narrating_tts_provider`). A lecture's explicit choice of another
configured provider wins. OpenAI's voice follows the lecture's language (`voices.OPENAI_DEFAULTS`: Nova for every
language, the voice OpenAI lectures had before); the Edge fallback keeps Edge's per-language voices and an Edge
`TTS_VOICE`; a saved voice of another provider than the one narrating (an Edge voice saved on "Server default") is
never sent to it (`voices.voice_fits`): that provider's default for the language narrates, with an
`assets.tts_voice_replaced` note. The Dynamic narrator is cast by the same rule (`authoring.narration_choice`). The
row above still holds: every `/v1/audio/speech` model is scheduled to retire on 2027-01-06, after which builds narrate
with Edge through the H0 `ModelUnavailable` fallback (§10.3) until `TTS_PROVIDER` moves or a Realtime-API adapter
exists (docs/OPERATIONS.md §3.4). OpenAI has no native word timings: each beat is one clip and its words are
spread over it by word length (`pipeline/audio.py` `estimate_words`) until the per-sentence path below exists.

**Word timings without native support.** Synthesise **per sentence**: sentence boundaries are exact and words inside a sentence are placed by characters (today's `estimate_words`); captions tolerate 80–120 ms [AU §2.4]. Such beats are marked `words_exact = false` (§8.2), which limits splicing. Forced alignment with `faster-whisper` (MIT) is optional.

### 5.2 Voice persona registry

`aadhi/providers/tts/personas.py` holds a static catalogue.

```python
class VoicePersona(StrictModel):
    id: str                                   # "edge:en-IN-NeerjaNeural"
    speaker_id: str                           # "ms:neerja": variants of one speaker share it [RV T-9]
    provider: Literal["edge", "azure", "gemini", "openai", "elevenlabs", "chirp", "sarvam", "fake"]
    voice: str
    model: str | None = None
    language: str                             # BCP-47 the voice speaks natively
    reads: list[str] = []                     # extra languages it reads well
    accent: str                               # "en-IN", "en-US", "en-GB", ...
    gender: Literal["female", "male", "neutral"]
    energy: Literal["calm", "neutral", "energetic"]
    roles: set[Literal["narrator", "aadhi", "student"]] = {"narrator"}
    formats: set[Literal["landscape", "portrait"]] = {"landscape", "portrait"}
    style_prompt: str = ""                    # steerable providers only
    tags: list[str] = []
    enabled: bool = True
```

**Initial free English pool (Edge)** [AU §2.2]. Rotation counts **speakers**, so the two Neerja variants count once:

| Persona | Speaker | Gender | Energy | Tags |
|---|---|---|---|---|
| en-IN NeerjaNeural | ms:neerja | F | neutral | |
| en-IN NeerjaExpressiveNeural | ms:neerja | F | energetic | |
| en-IN PrabhatNeural | ms:prabhat | M | neutral | |
| en-US AndrewMultilingualNeural | ms:andrew | M | neutral | Warm, Confident |
| en-US AvaMultilingualNeural | ms:ava | F | energetic | Expressive, Caring |
| en-US BrianMultilingualNeural | ms:brian | M | neutral | Approachable |
| en-US EmmaMultilingualNeural | ms:emma | F | energetic | Cheerful, Clear |
| en-GB RyanNeural | ms:ryan | M | neutral | |
| en-GB SoniaNeural | ms:sonia | F | neutral | |
| en-AU WilliamMultilingualNeural | ms:william | M | neutral | |

**Excluded from narration:** en-US AnaNeural, tagged *Cartoon/Cute* (R-40), and the ta-LK, ta-MY and ta-SG variants, off by default for Tamil Nadu audiences [AU B4].

**Small pools are said plainly** [RV T-9]. When the eligible pool has fewer than 3 speakers, the Studio shows e.g. *"Only 2 Tamil voices are available. Add an Azure, Google or Sarvam key for more variety."*

### 5.3 Casting, rotation and history (`aadhi/pipeline/casting.py`, `aadhi/pipeline/history.py`)

**Rotation is per lesson, not per version** [RV T-1].
- **Regenerate, duplicate and "Generate again"** inherit the parent version's whole `Presentation`: voice cast and calibration, music choice (including its asset key), theme family, personality, display font, caption style, hook kind, intro layout and `look_epoch`. A **Fresh look** checkbox re-rolls them (bumps `look_epoch` and re-casts against history).
- **Translation** keeps the look and re-casts voices for the target language (necessary), keeping the previous speaker when it reads that language.
- **A reel made from a lesson** is its own rotation entry: it gets a fresh look and voice, excluding the parent lesson's.

**History** (`history.py`, no new table): for each of the teacher's **other** projects, the newest version with a **completed render**, per format, ordered by render completion time, newest first. Drafts, failed runs and unrendered duplicates never count; students only see rendered videos. Every rotation rule (R-39, R-47, R-51, display font, personality, caption style, hook kind, greeting, intro layout) reads this list. History is read at generation, Fresh look and New colours/New layouts time only, never at rebuild.

**Course.** The subject field becomes a **Course picker** listing the teacher's existing subjects (distinct `Project.subject_name`, case-folded) plus "New course" [RV T-1]. A blank field is matched case-insensitively against existing courses before a new key is created, so extraction differences no longer split a course. Course pins ("Keep this voice for the course") are stored per course.

**Accent policy** [RV L-20, T-9]. The audience locale sets the accent pool.
- `mostly_local` (default for en-IN): a non-local narrator is allowed only if both of the previous two lessons were local, so at least 2 of every 3 lessons have an Indian-English narrator. With Azure configured the local pool has ≥ 8 speakers and the default becomes `local_only`.
- `local_only` and `any` are teacher choices; the Studio's voice list has an accent filter.

```
pool       = enabled personas whose language or reads match the narration language, role narrator,
             format allowed, from usable providers (a key resolves through credentials.resolve_keys;
             the lecture's provider unless "any provider"; paid personas only if premium is allowed)
local      = pool filtered to the audience accent
group      = local if the accent policy requires it this time, else pool
history_sp = narrator speaker ids from history, newest first
exclude    = the last min(3, speakers(group) − 1) of history_sp                    (R-39)
candidates = personas in group whose speaker is not excluded
prefer     = the gender opposite to the previous lesson's narrator, when available
narrator   = first of candidates by (prefer desc, speaker last-used asc, rng(look_seed, "voice"))
aadhi      = the Brand setting's Aadhi persona (fixed; Q4)
student    = an adult persona of the opposite gender, same provider, energy ≥ neutral; None if unused
```

**Pinning.** "Keep this voice for the course" bypasses rotation and is recorded in `presentation.pins`.

**Hear it first** [RV T-27]. The form plays a generic `voice_sample` (§9.6). In plan review and in the editor, **Hear your terms** synthesises the hook plus the glossary terms (≤ 40 words) in the cast voice, so a teacher can catch mispronounced jargon and fix it with a `LexiconEntry` respelling (`spoken`), which works for every provider. It is free on Edge; the cost is shown for paid voices.

### 5.4 Calibration of rate and loudness

**What is measured.** For each `(provider, model, voice)`, once: the natural speech wpm (§1.4) and the integrated loudness, over a fixed 45-word calibration text per language. Results are cached as a private `calibration` asset. Calibration synthesis is a usage row like any TTS call (free on Edge) [RV E-9].

**Stored with the lesson** [RV E-31]. The resolved control value (rate %, speed or style text) and the natural wpm are written into `Presentation.voices` at generation time. Rebuilds use those stored values, so a cache loss or provider drift cannot change TTS keys, timings or anchored edits.

**Reaching the target rate (R-36):**

| Provider | Control | Method |
|---|---|---|
| Edge / Azure | prosody `rate` % | `rate = round((target / natural − 1) × 100)`, clamped to ±25% [AU B9] |
| ElevenLabs | `voice_settings.speed` (0.7–1.2) | `speed = clamp(target / natural)` |
| Gemini | style text plus pause tags | measure; outside the band, re-synthesise once with an adjusted style |
| OpenAI | `instructions` ("about N words per minute") | same measure-and-retry |

**After synthesis.** Each beat's speech wpm is measured. If any 10 s window exceeds 185 wpm, that beat is re-synthesised once at a lower rate. Audio is **never** time-stretched [LS E3].

**Loudness** [RV E-17]. After all beats exist, `build_assets` measures each speaker's concatenated speech and computes one static gain per speaker to reach −16 LUFS (R-41). The gains are **applied when the `scene_audio` asset is built**, and the scene-audio key includes them, so the live player and the MP4 play the same levels. Beat TTS caches are untouched, and classic lectures (no gains) keep their keys.

**Silence trimming already exists**: `build_scene_audio` trims every clip's lead and tail silence to a 30 ms pad (`aadhi/pipeline/audio.py:31`, `KEEP_PAD_SECONDS`). Revision 1's extra Dynamic-only trim option is dropped [RV E-30].

### 5.5 Speakers

`Beat.speaker: Literal["narrator", "aadhi", "student"] = "narrator"` is left out of the JSON at its default. Each beat is synthesised with its speaker's persona.

| Lint | Severity | Rule |
|---|---|---|
| `voice.aadhi_misplaced` | error | An `aadhi` beat outside the intro or recap scene |
| `voice.secondary_share` | warning (system) | Student and Aadhi speech together exceed 15% of narration time (R-38) |
| `voice.student_density` | warning (system) | More than 1 student interjection per 45 s |
| `voice.student_role` | warning | A student line anywhere other than the myth line of `myth_fact`, a `predict` prompt or a `check` question |

Voicing a student's misconception before refuting it roughly doubled scores in Muller's dialogue study [CP S5]; a second voice helps when it has a role [LS §7].

### 5.6 Delivery styles

Style text goes to Gemini `speech_metadata.style`, OpenAI `instructions` or an Azure `express-as` style, where supported; ElevenLabs maps it to `stability` and `style`; Edge gets rate and pitch only [AU §2.3]. The Social energy reel profile shifts every row one step more energetic; the Learning profile does not.

| Segment | Style | Rate adjustment |
|---|---|---|
| Hook | Curious, energetic, a warm smile | +5% |
| Explanation (default) | Warm, clear, measured, conversational ("you", "we") | 0 |
| Definition | Clear, deliberate | −5% |
| Formula, code, derivation steps | Patient, step-by-step | −10% (R-37) |
| Misconception (fact part) | Emphatic, slower on the correction | −5% |
| Question / check | Inviting | 0 |
| Recap | Upbeat, confident | 0 |
| Aadhi lines | Friendly, bright, welcoming | +5% |

Script style: second person, contractions allowed, a rhetorical question at each segment start, polite, no sarcasm or over-familiar slang. Energy comes from intonation, not speed. Personalisation g = 0.33 [LS §7, E5].

### 5.7 Keys, budgets, cost and disclosure

**Keys** [RV E-8]. The paying key is resolved by `credentials.resolve_keys` (personal, then the server key saved in the Studio, then `.env`). Today that covers only `("gemini", "openai", "anthropic")` (`aadhi/models.py:74`). Each new paid provider extends `CREDENTIAL_PROVIDERS`, the `resolve_keys` mapping, the key tests and the Studio keys page in the batch that adds it:

| Provider | Batch | Key kinds |
|---|---|---|
| ElevenLabs (TTS, and Music in B7) | B3 | personal + server |
| Azure Speech (key + region) | B3 (if Q8 = Azure) | personal + server |
| Lyria | B3 | uses the **Gemini** key, so it is the bring-your-own-key path for AI music |
| Pexels, Pixabay | B6 | server only (the browser never sees them) |
| Sarvam | B7 | personal + server |
| Google Cloud (Chirp 3 HD) | B7 | a service-account JSON, a new encrypted credential type |

ElevenLabs Music exports are blocked on free-tier keys: its API pricing page licenses commercial music use only on Starter and above (fetched by the review).

**Budgets** [RV E-9].
- `aadhi/usage/pricing.py` gains a `music` operation, priced per second (ElevenLabs) or per unit (Lyria).
- `estimate_cost` no longer returns 0.0 for an unknown operation: it charges the global default rate and logs a warning (fail closed).
- `_media_billed_to_server` (`aadhi/api/generation.py:244`) covers music, premium TTS and calibration synthesis, so `ensure_budget` pre-checks them when the server pays.
- Usage rows are recorded for calibration, "Hear your terms", "New music" and Restyle pictures.
- Price rows: Gemini 3.8 TTS (with the 1 Jan 2027 step), `gpt-4o-mini-tts`, ElevenLabs v4 and Flash (the current $300 per million characters looks stale against the list price; re-read in B0), Azure per character, Lyria per song, ElevenLabs Music per minute.

**Cost in the Studio.** Each voice and music choice shows its estimated cost per video, from `pricing.py`; free choices say "Free".

**Disclosure.** Every Dynamic export includes "Narration (and music) generated with AI (provider list)" in `credits.txt` and the description draft; the outro card shows it at 36 px. OpenAI requires AI-voice disclosure, Lyria embeds SynthID and ElevenLabs can sign C2PA; one line covers all [AU §6]. The wording is Q3.

### 5.8 Music sources

Music is a **mood and variety layer**. Under speech it is at best neutral for learning: in Phan 2023, the only video RCT behind our levels (N = 92, literature content, a 6-question retention test), music ducked 10 dB under narration had no effect (p = .53), and the condition that helped played music at full level under **silent on-screen text** [RV L-18]. Music is therefore never presented to teachers as a retention feature.

| Source | Licence and terms | Cost | Exposable? | Verdict |
|---|---|---|---|---|
| **Procedural engine** (numpy + scipy, ours) | Ours; unique per seed; claim-free | $0; ≈ 10 s CPU per 5 min [AU §3.3] | **No: bed-only** | **R1 (B3).** Always available. Plays only at the under-speech level, so its synthetic sound is never exposed [RV T-7] |
| **Teacher upload** | The teacher confirms the rights; provenance stored | — | Yes | **R1 (B3)** |
| **Google Lyria 3.5** (`lyria-3.5`) | Google claims no ownership; SynthID. Vocals are on by default, so the prompt asks for instrumental only and any result with non-empty lyrics text is **rejected**. 18+ terms (Q3) | $0.08 per song (~2 min): ≤ 3 cues per lesson + ≤ 1 retry ≤ $0.32 | Yes | **R1 (B3), opt-in** per teacher or by an org setting (Q2); uses the Gemini key |
| Lyria 3 Clip (30 s) | Same | $0.04 | Yes | Optional sonic-logo variants |
| **ElevenLabs Music** `music_v2_5` | Commercial use on Starter and above; the v1 terms restrict pooled music libraries, so music is **generated per video and never pooled** | $0.15/min ≈ $0.78 per lesson | Yes | **B7, opt-in only** [RV E-28] |
| Curated licensed pack (owner-supplied) | Licence per track; must be claim-free | varies | Yes | Optional (Q9): the cheapest route to good exposed music |
| incompetech | CC-BY with credit, or a paid licence | — | Yes | Optional pack with credits |
| Pixabay music (hand-picked) | No API; some tracks carry Content ID claims | $0 | — | **Avoid by default** (R-50) |
| Freesound API, Jamendo API | API tiers are non-commercial | — | — | **Reject** |
| MusicGen / AudioCraft weights | CC-BY-NC 4.0 | — | — | **Reject** |
| YouTube Audio Library; Suno / Udio | No API / no official developer API | — | — | **Reject** |
| Stable Audio 2.5 | API terms not verified | ≈ $0.20 | — | Not in v1 |

**"Auto" music** picks the first that applies [RV E-28]:
1. a track the teacher pinned;
2. a curated-pack track not used in the teacher's last 10 lessons, if a pack is configured;
3. AI music (Lyria first, ElevenLabs in B7) when the teacher or the org setting opted in, a key resolves and the budget allows;
4. the procedural bed.

### 5.9 Selecting music: mood, subject and format

| Mood | Palette | Tempo | Typical use |
|---|---|---|---|
| `calm-focus` | Lo-fi, ambient pads, soft keys | 70–90 BPM | Default lesson explanation; Soft, Chalk |
| `bright-curious` | Light acoustic, plucks, marimba | 90–105 | Crisp, Bouncy, younger audiences |
| `cinematic-wonder` | Soft orchestral, pads | 70–90 | Science "wonder" topics; Cinematic |
| `classical-focus` | Piano and strings, no lead melody in the speech band | 60–80 | Dense maths; Editorial [LS §8.2] |
| `upbeat-drive` | Light electronic, percussion | 100–120 | Social-energy reels only (R-46) |

- **Subject weights:** maths and physics derivations → classical-focus, calm-focus; computer science → calm-focus, bright-curious; biology and chemistry → bright-curious, cinematic-wonder; history → cinematic-wonder. The personality and format adjust them; the draw is seeded by `look_seed`.
- **History rule:** the same (mood, key, BPM ± 4) is never used in the teacher's last 3 lessons; library tracks never within their last 10 (R-47).
- **Within a video:** one bed; arrangement changes at chapter (16:9) or act (9:16) boundaries; a new bed only at a `builds_on = false` boundary (R-47).

**Music modes** (`MusicChoice.mode`):

| Mode | Under speech | Exposed regions | Default for |
|---|---|---|---|
| `bed` | −22 LU (−21 Social reels) | exposable sources only (R-44) | lessons with < 30% dense runtime; reels |
| `gaps_bookends` | off | exposable sources in bumpers, gaps ≥ 2.0 s and bookends; the procedural bed reduces to the sonic logo | lessons with ≥ 30% dense runtime (formula, code, worked examples) [RV L-18] |
| `off` | off | off | teacher choice; the Dynamic-lean pilot arm uses `gaps_bookends` |

### 5.10 Generating, caching and fitting music

**When.** `aadhi/pipeline/music.py` runs in the asset stage after narration exists, in parallel with image and visual jobs.

**What it requests.** Lyria: one cue per chapter or act (≤ 3 per lesson, ≤ 2 per reel), all with the **same** prompt, key and BPM so they form one bed; ≤ 1 retry in total for rejected results. ElevenLabs (B7): one `composition_plan` with one section per chapter, each 20% longer than needed. Procedural: one render with section variants.

**Prompt (AI providers).**

> Instrumental only — no vocals, no choir, no spoken words. {mood description}. {genre}, {bpm} BPM, key of {key}. Steady dynamics, sparse arrangement, no prominent lead melody between 300 Hz and 3 kHz; a quiet bed under a teacher's voice.

**Intake.**
1. Decode; measure integrated loudness, LRA and true peak (`ebur128`).
2. Lyria results must have empty lyrics text.
3. Normalise the asset to −16 LUFS (today's `bgm.mp3` is −9.7 LUFS [AU §1]).
4. **Analyse for fitting:** the bar grid from the requested BPM, bar-aligned loop regions found by a self-similarity check, and the section starts. Stored in the asset's meta (`bpm`, `key`, `loop_points`, `sections`).

**Fitting on every build** [RV E-10]. `MusicPlan` is rebuilt deterministically in every `build_timeline`, because clip inserts, cuts, hidden scenes and re-voicing all change chapter lengths. For each span between music boundaries:
- shorter than the cue: trim at a bar boundary with a fade;
- longer: loop between stored loop points with a one-bar qsin crossfade;
- no loop points: extend with a procedural bed in the same key and BPM, at the under-speech level.

"New music" stays an explicit, paid action. Test: inserting a 10 s clip leaves no music gap and no unequal-power seam.

**Caching.** Generation is non-deterministic, so the produced file is the source of truth and is **never regenerated on a re-render** [AU §6]. Asset keys are content-addressed by `sha256(provider, model, prompt, seed, durations)`; because such keys can be guessed, the asset authorisation of §6.7 covers `MusicChoice.asset_key` [RV E-2].

### 5.11 The procedural engine (`aadhi/providers/music/procedural.py`)

Derived from the measured prototype [AU §3.3]. It uses **numpy and scipy** (scipy 1.17.1 is already locked through manim) and writes WAV with the standard-library `wave` module. It does **not** use `soundfile`, which would add LGPL libsndfile, or `pedalboard`, which is GPL-3.0 [RV E-36].

| Element | Choice |
|---|---|
| Progressions | lo-fi Imaj7–vi7–ii7–V7(9); bright I–V–vi–IV; ambient I–IV or a modal vamp; classical arpeggiated I–vi–IV–V |
| Key | Seeded per video |
| Timbres | 2-operator FM electric piano; a pad of 3 detuned saws, low-pass 1.2–4 kHz; sine sub-bass |
| Drums | Kick sine sweep 140 → 50 Hz; noise snare 0.9–5 kHz; hat > 7 kHz; swing 50–60%. Off in `classical-focus` |
| Tempo | 64 (ambient), 78 (lo-fi), 104 (bright), each ±8; ≤ 110 BPM (R-46) |
| Reverb | Block-wise numpy Schroeder/Freeverb, wet 0.18–0.25 |
| Master | High-pass 35 Hz, tanh soft clip, peak −3 dBFS, normalised to −16 LUFS; 48 kHz stereo |
| Structure | Section variants (layer muting, filter opening) only at R-47 boundaries |
| Sonic logo | A fixed ≤ 1 s motif rendered once and reused at the start of every lesson and the end of every reel [RV T-8, L-22] |

**Honest quality.** The output is pleasant but clearly synthetic: fine under speech, weak when exposed [AU §3.3]. Hence bed-only, and the **B4 gate**: the owner compares the default bed with a reference track in a blind listening test before the first release [RV T-7].

### 5.12 The mix (`aadhi/compose/dynamic/mix.py`)

The mix is numpy float32 at 48 kHz, computed once for the whole edited timeline, never per video chunk [MR §4.2]. A 5-minute stereo stem is ≈ 115 MB, so the mix needs ≈ 0.5 GB with temporaries, budgeted in the render container (§7.6) [RV E-4].

**Stems.**

| Stem | How it is built |
|---|---|
| Narration | The `scene_audio` assets (speaker gains already applied, §5.4), resampled 24 → 48 kHz, placed at `scene.start + audio_offset` |
| Clip audio | Inserted clips (§8.6) |
| Music | Cues from `MusicPlan` with 2 s qsin crossfades at bed changes. Fade-in 1.0 s (16:9) or ≤ 0.3 s (9:16); fade-out 3.0 s or 1.5 s. Gains in LU relative to the narration stem. 100 Hz high-pass and a static −3 dB dip at 2.5 kHz (Q 1.2) on the music bus [AU D20–D24] |
| SFX | At `TimedScene.events` times, only the R-49 set |

**Envelope (R-44, R-45).**
1. **Speech regions** are all speakers' word spans plus clip-speech regions, merged across gaps shorter than `G` (2.0 s in 16:9, 1.5 s in 9:16; longer than the research's 1.2 s so the music does not swell between sentences, which would be changing-state sound [RV L-4]).
2. Each instant gets a target level:

   | Where | Level |
   |---|---|
   | Inside a speech region | −22 LU (−21 Social reels); off in `gaps_bookends` |
   | Think time, "Your turn" and countdowns | the under-speech level, or off in `gaps_bookends`; **never higher**, no riser [RV L-4] |
   | Gap ≥ `G`, bumper | −14 LU, exposable sources only; otherwise unchanged |
   | Before the first word or after the last | −8 LU for exposable sources; the procedural bed holds its under-speech level and the sonic logo plays |
   | Dense scenes (`formula_focus`, `worked_steps`, `code_walkthrough`) | off, with 1.5 s fades at the scene edges |

3. Ramps: duck starts 300 ms before speech with a 200 ms ramp; release starts 500 ms after speech with a 700 ms (16:9) or 400 ms (9:16) ramp; equal-power curves.
4. The breakpoints are stored in `Timeline.music.duck: list[(t, gain_db)]`. The same list drives numpy (MP4) and Web Audio (live player).
5. `sidechaincompress` is not used; today's sidechain swings 24–26 LU [AU §1].

**Master chain.** Sum the stems; two-pass `loudnorm` (`I=-14` or `-16` by destination, `TP=-1.5`, `LRA=9`, `linear=true`); if linear mode cannot reach the target, a static gain plus `alimiter=limit=0.84:attack=5:release=50:level=0` (≈ −1.5 dBFS) [AU D25]; `aresample=48000`; AAC-LC 384 kbps. The audio is encoded and **measured in preflight**, before capture; a miss re-runs the chain with a corrected gain automatically (§7.12) [RV T-3].

**Caching.** The mix key hashes the audio-relevant timeline fields, the asset keys and the mix parameters.

**Live player** [RV E-17].
- Narration stays on per-scene `<audio>` (the master clock); speaker gains are already in the asset.
- Music streams through an `<audio>` element → `MediaElementAudioSourceNode` → `GainNode` automated from `Timeline.music.duck`, re-synced on seek. Decoding the whole track into an AudioBuffer would cost ≈ 115 MB on a student phone, so only short SFX use AudioBuffers.
- Music and SFX are served from the app origin through the existing asset route, or from a bucket configured for CORS with `crossorigin="anonymous"` (a cross-origin source without CORS outputs silence through Web Audio). SFX fetched with `fetch` stay same-origin, so CSP `connect-src 'self'` (`aadhi/security/headers.py:65`) is unchanged; the CORS rule is documented in `OPERATIONS.md`.
- Viewers get **Music: Off / Low (−26 LU) / Normal**, the WCAG 1.4.7 "can be turned off" route [LS F6].

**Classic mode** keeps its own `bgm_filter` path, unchanged (§3.6).

---

## 6. Generation pipeline changes

### 6.1 Flow

```
Studio "Dynamic video" tab ─► POST /api/projects  (GenerationOptions: video_style="dynamic", video_format,
                                                   length_profile, reel_profile, destination, look/voice/music
                                                   preferences, series part or focus concepts)
worker generate_lecture  (pipeline/orchestrator._generate)
  ingest → source_scope → brief (concept focus)                               unchanged
  scope.py        coverage estimate; may pause in awaiting_review ("scope")    NEW (§6.4)
  casting.py      presentation.voices; inherited Presentation on regenerate    NEW (§5.3)
  rotation        hook kind, intro layout, check order, greeting (history)     NEW (§6.5)
  plan            plan.md + prompts/dynamic_plan.md (format block)             CHANGED prompt + PlannedScene fields
  plan_rules      per-format budgets, coverage, order rules                    EXTENDED
  (plan review)   Dynamic view when "Let me review the plan" is on             EXTENDED (§9.4)
  script          scene_*.md + prompts/dynamic_scene.md → gen_models → canonicalize   EXTENDED
  lint / critic / repair   validate.py + dynamic_lint.py; only failing scenes rewritten EXTENDED
  companion                                                                    unchanged
  direct.py       templates, variants, backgrounds, transitions, accents, entity colours, text fit  NEW (§4.4)
  WRITE screenplay (CAS)
  _build_and_finish
    assets.build_assets   TTS with the speaker's persona and stored calibration; speaker gains baked into
                          scene audio (§5.4); images per format aspect with the stored style suffix unless
                          colour_is_content; Manim with the stored asset-style background and colour map
    pipeline/music.py     music assets, after narration timing (§5.10)                       NEW
    pipeline/fit.py       duration fit for the platform-safe profile (§6.4)                  NEW
    compose.timeline.build_timeline   + presentation, ResolvedLook, events.py, kinetic captions,
                          MusicPlan fitting and duck breakpoints, SFX cues                     EXTENDED
render_video job  preflight (§7.12) → engine "motion" when timeline.presentation.style == "dynamic", else "states"
```

### 6.2 What is reused as is

- **Content generation:** `ingest`, `source_scope`, `brief`, checkpointing, per-scene writers, gen-model validation in the re-ask loop, canonicalisation that never lets the model author ids, the deterministic lint, the LLM critic, scene-scoped repair, the companion sheet.
- **Assets and timing:** content-addressed assets, TTS per beat, `sync.py` word anchors (`var`, `emphasis`, `output`, `focus`), the `build_timeline` time base, silence trimming.
- **Studio and editor:** autosave, undo, merge and "Generated vs edited", including the batch-4 work re-verified in B0.

Dynamic mode adds **structure** (format budgets, roles, new item kinds, scope) and **direction** (`direct.py`). It does not rewrite the content stages.

### 6.3 `GenerationOptions` and the Dynamic form

Every field below is left out of the JSON at its default, so old clients, `regenerate` and stored settings behave exactly as today [CB §6.1].

```python
video_style:    Literal["aadhi", "dynamic"] = "aadhi"
video_format:   Literal["landscape", "portrait"] = "landscape"
length_profile: Literal["standard", "platform_safe"] = "standard"
reel_profile:   Literal["learning", "social"] = "learning"
destination:    Literal["messaging", "lms", "youtube", "lesson_page"] | None = None   # None = Settings default (Q10)
look:   LookPrefs | None   = None  # theme_mode auto|dark|light; family/personality/font pins; energy calm|balanced|lively;
                                   # resolved: the choices of a preview strip the teacher kept (§9.6); fresh_look: bool
voice:  VoicePrefs | None  = None  # rotate; narrator pin; accent_policy local_only|mostly_local|any;
                                   # premium_allowed; any_provider
music:  MusicPrefs | None  = None  # source auto|generated|library|upload|procedural|off; track key; mood pin;
                                   # mode auto|bed|gaps_bookends|off; level normal|low; ai_opt_in: bool
aadhi_recap:   bool | None = None  # None = format default (§2.1)
captions_burn: bool | None = None  # None = destination default (§2.6)
sfx:           bool | None = None  # None = on
series_part:   SeriesPart | None = None   # {series_id, index, count, concepts} (§2.2)
focus_concepts: list[str] | None = None   # brief concept keys the teacher kept (§2.2)
```

**Fields Dynamic does not use** [RV T-5]. For `video_style = "dynamic"` a validator requires these to be at their defaults, so nothing is silently ignored: `target_minutes` (length comes from the format), `tts_rate` (it would break R-36 calibration), `include_quizzes` and `quiz_every_n_concepts` (R-06 sets checks). The form maps the others:

| Classic field | In the Dynamic form |
|---|---|
| `tts_provider`, `tts_voice` | The Voice control: a provider filter and the narrator pin |
| `depth` (overview / standard / deep) | The word budget −10% / 0 / +10% inside the format band |
| `allow_gifs`, `allow_interactive` | Under "Advanced", labelled "web lesson page only", default off |
| `allow_freeform_manim` | Under "Advanced", as today |
| `review_plan` | "Let me review the plan first" |
| Extra instructions | Relabelled "Extra instructions" (Aadhi only speaks the intro) |

A frontend test asserts the Dynamic form never sends a field Dynamic does not use.

**Energy** maps to R-15, R-18 and R-23: *Calm* moves the event cadence to the upper bound and halves stylised transitions; *Lively* moves to the lower bound and allows a background preset change at every chapter boundary. It never goes outside the rulebook.

### 6.4 Scope check, series and duration fit

**Scope check** (`aadhi/pipeline/scope.py`, after `brief`; §2.2).
- The estimate and the decision are deterministic and versioned (`SCOPE_VERSION`).
- An over-budget source pauses the job in `awaiting_review` with `generation_meta.stage = "scope_review"`, the same mechanism `review_plan` uses (`orchestrator.py:854–867`). The proposal (estimate, budget, the N-part split with concept lists) is stored in `generation_meta`.
- `POST /api/versions/{vid}/scope` takes `{choice: "series" | "focus" | "condensed", focus_concepts?, generate_all?}`. "series" writes `Project.settings["series"]` on this project, creates the other part projects (each with `series_part` options and the shared settings), optionally enqueues them, and resumes this job as Part 1; "focus" resumes with `focus_concepts`; "condensed" resumes and records the left-out concepts for the coverage report.
- Parts after the first receive the previous part's concept list as planner context for a one-sentence bridge. Series parts never share checkpoints; each part's generation is billed separately and the estimate shows the total.

**Duration fit** (`aadhi/pipeline/fit.py`, after `build_assets`) [RV E-20, T-3]. TTS length is unknown until the audio exists, and Edge voices run 147–201 wpm before calibration, so a platform-safe reel can overrun. The fit step applies, in order, only what is needed:
1. tighten `pause_after` and holds toward the lower R-42 / R-16 bounds;
2. drop the optional Aadhi recap;
3. shorten think times to their R-07 minimum;
4. re-synthesise beats up to +5% rate, staying inside R-36 (free on Edge; on paid voices only if the budget allows);
5. if still over, preflight refuses before capture and the Studio offers **Fit to 2:58**: a review-first proposal to hide the lowest-priority micro-segment (never a check or the hook), applied as one undo step.

For standard lessons and reels the same steps are offered as suggestions only; generated runtime outside R-01 is a teacher-facing warning with a live runtime meter in the editor (§8.8).

### 6.5 Planner

`prompts/dynamic_plan.md` is appended to `plan.md` when `video_style == "dynamic"`, with one block per format. It instructs:

- **Budget.** `{words}` and `{wpm}` from §2.1; scene budgets come from `plan_rules`. Cover every concept assigned to this lesson (`series_part.concepts` or the focus list); never shorten a worked example to fit — leave a concept out and say so instead.
- **Structure** (§2.3):
  - **The opening scene is the Aadhi intro.** Write only the session naming and the hook; the greeting (16:9) or sign-on (9:16) comes from the vetted pool and is inserted deterministically (§3.3). 16:9 ≤ 16 words in total; 9:16 hook first, ≤ 8 words.
  - **Hook kind.** Use the `{hook_kind}` chosen by rotation (question, true/false, stakes, mystery, challenge, story; stat from B7), excluding the teacher's last 2 kinds; if the source cannot support it, use a question. A true/false hook is a statement to judge, never labelled a myth, and is refuted in chapter 1. Name the scene that answers the hook.
  - **16:9:** 2–4 component terms for pre-training, each used again in ≥ 2 later scenes; 2–4 chapters of 60–90 s with `builds_on` set per chapter; a `predict` before the scene that explains a surprising outcome; one check after each chapter's explanation, in the given `{check_order}`; at least one worked example for quantitative or procedural topics, ending with a "Your turn" step, and a faded second example when there is room; a misconception scene when the brief lists one; a recap whose big ideas are relational statements and whose questions include ≥ 1 apply or transfer question; an outro.
  - **9:16:** one concept for the whole reel; 5–7 micro-segments of 20–40 s, each one step of that concept, each opening with a micro-hook of ≤ 8 words and ending with a "remember this" line or a micro-question; a mid-reel check and an end check (free recall or single answer); a last line that leads back to the opening question.
- **Visual intent.** Every scene gets a `visual_intent`: `hook | context | pretraining | define | explain | compare | sequence | cycle | timeline | cause | quantity | formula | example | code | data | spatial | analogy | misconception | predict | check | apply | summarize | recap`.
- **Persona.** The narrator is a friendly guide speaking in the second person and never calls itself Aadhi.

`PlannedScene` gains `visual_intent` (optional). `NarrativeRoleName` gains `pretraining`, `predict`, `check` and `recap` (extending the `Literal` is backward compatible). The plan gains `hook: {question, kind, answered_in}` and `chapters[].builds_on`.

`plan_rules` enforces the format budgets before any scene is written, the cheapest place to fix structure: segment count and lengths; check count and placement after explanations; each `predict` before its answering scene in the same chapter; exactly one presenter scene, first; the recap; words-per-scene ceilings; every assigned concept planned.

**Rotation inputs** chosen before planning, from history (§5.3): hook kind (excluding the last 2), check-kind order (a seeded permutation), greeting or sign-on (excluding the last 2), 16:9 intro layout (excluding the last), 9:16 card position. They are stored in `Presentation` so rebuilds never re-roll them [RV T-8].

### 6.6 Scene writers

`prompts/dynamic_scene.md` is appended to `scene_board.md`, `scene_quiz.md`, `scene_chapter.md`, `scene_simulation.md` and `scene_ai_video.md` for Dynamic lectures. It instructs:

- **Board text** is keywords, labels and formulas only (R-25); never copy the narration except in hook and statement scenes; mark key terms with `[[term]]`.
- **Structured items** (§6.7): `question` (`blank=true`, answer in `justification`, `level` recall/apply/transfer; a later beat with `answers` fills it), `stat` (number, unit, context; **source refs required**), `step` + `sequence`, `quote` (**verbatim from the source**), `pair` (familiar ↔ concept).
- **`colour_is_content`.** Set it when colours in a picture or animation carry meaning (resistor bands, flame tests, indicators, wiring, spectra). It stops the palette suffix and the Manim colour mapping [RV L-17].
- **`icon_hint`** is one plain keyword per item where an icon helps.
- **Beats** may set `speaker="student"` only for the myth line, a prediction prompt or a check question. Beat length ≤ 30 words (16:9) or ≤ 20 (9:16).
- **Myths** are refuted in the same scene, ending on the fact; never repeated in the recap.
- **No visual direction.** The writer never names templates, colours, fonts or animations.

`style.md` gains a Dynamic section (§5.6). Every changed prompt bumps its `PROMPT_VERSION`, and the eval baselines are re-run in the same batch.

### 6.7 Schema changes

**Every field below is added in B0 as inert** — parsed, validated and preserved by every transform — even when its feature ships later [RV E-3]. Writes that use a feature not yet enabled (`DYNAMIC_FEATURES`; the names frozen in B0 are `presentation`, `items_v3`, `cuts`, `overlays`, `clip` and `voice_override`, the teacher's recorded narration) are refused by `PUT /screenplay`, lint, the timeline preview and import (the API and the `import_legacy` JSON job) with 422 `feature_unavailable`; a feature the stored lecture already uses stays editable after it is switched off, so a teacher is never locked out; tests enable the flags. Classic documents serialise exactly as today (`exclude_if` defaults).

**`aadhi/schemas/screenplay.py`**

```python
class VoiceCalibration(StrictModel):
    natural_wpm: float = Field(ge=60, le=300)
    control: str = Field(max_length=200)        # "+8%", "speed=1.05" or a style text, as sent (§5.4)

class VoiceCast(StrictModel):
    narrator: str                               # VoicePersona.id
    aadhi: str | None = None
    student: str | None = None
    calibration: dict[str, VoiceCalibration] = {}

class MusicChoice(StrictModel):
    source: Literal["generated", "library", "upload", "procedural", "off"]
    provider: str | None = None                 # "lyria" | "elevenlabs" | "procedural" | None
    mood: str | None = None
    asset_key: str | None = None                # authorised through asset_uses (below)
    mode: Literal["bed", "gaps_bookends", "off"] = "bed"
    level: Literal["normal", "low"] = "normal"

class HookRef(StrictModel):
    question_item: str                          # "<scene_id>/<item_id>"
    kind: Literal["question", "true_false", "stakes", "mystery", "challenge", "story", "stat"]
    answer_beat: str | None = None              # "<scene_id>/<beat_id>"

class AssetStyle(StrictModel):                  # frozen at generation; drives image suffix and Manim bg
    family: str
    personality: str

class Presentation(StrictModel):                # absent = classic Aadhi presenter mode
    style: Literal["dynamic"] = "dynamic"
    format: Literal["landscape", "portrait"] = "landscape"
    length_profile: Literal["standard", "platform_safe"] = "standard"
    reel_profile: Literal["learning", "social"] = "learning"
    destination: Literal["messaging", "lms", "youtube", "lesson_page"] = "messaging"
    look_seed: int = Field(ge=0, le=2**31 - 1)  # §4.4
    look_epoch: int = Field(default=0, ge=0)    # bumped by Fresh look / New colours
    layout_epoch: int = Field(default=0, ge=0)  # bumped by New layouts
    theme_family: str                           # catalogue ids, pattern ^[a-z0-9_-]{1,40}$
    personality: str
    display_font: str
    caption_style: str
    cue_term_style: Literal["marker", "underline"]
    intro_layout: Literal["fullframe", "card_left", "pip", "split"] | None = None
    reel_card_x: int | None = Field(default=None, ge=0, le=1080)
    hook_kind: str
    check_order: list[str] = []
    greeting_id: str | None = None
    asset_style: AssetStyle
    voices: VoiceCast
    music: MusicChoice
    entity_colors: dict[str, int] = {}          # normalised symbol/entity -> slot 0..2
    hook: HookRef | None = None
    aadhi_recap: bool = True
    captions_burn: bool = True
    sfx: bool = True
    motion: Literal["standard", "calm"] = "standard"
    pins: list[Literal["theme", "personality", "display_font", "voice", "music", "caption_style"]] = []

class PresenterSpec(StrictModel):
    clip: Literal["left", "center"]             # never popup/right (R-14)
    seconds: float = Field(ge=2.0, le=8.0)
    layout: Literal["fullframe", "card_left", "pip", "split", "card"]

class SceneLook(StrictModel):                   # set by direct.py or the teacher; never LLM-facing
    template: str | None = None
    variant: str | None = None
    background: str | None = None
    palette_slot: int | None = Field(default=None, ge=0, le=40)
    transition_in: str | None = None
    hold_reason: Literal["formula", "code", "diagram", "think"] | None = None
    presenter: PresenterSpec | None = None
    continues_from: str | None = None           # scene id: part B of a split continues A's final state
    lock: Literal["auto", "settled", "pinned"] = "auto"

Screenplay.presentation: Presentation | None              (exclude None)
Screenplay.chapters[].builds_on: bool = True              (exclude True)
SceneBase.look: SceneLook | None                          (exclude None)
SceneBase.colour_is_content: bool = False                 (exclude False; also on SidePanel)
SceneIntent.visual_intent: VisualIntent | None            (exclude None)
Beat.speaker: Literal["narrator", "aadhi", "student"]     (exclude "narrator")
Beat.answers: str | None                                  (exclude None; "<scene_id>/<item_id>", hook or predict)
Beat.cuts: list[WordRange]; Beat.overlays: list[Overlay]  (exclude empty; §8.2)
Beat.voice_override_asset_key: str | None                 (exclude None; the teacher's own recording, §8.4)
BoardScene.sequence: Literal["flow", "cycle", "timeline", "chain", "checklist"] | None   (exclude None)
BoardItemKind += question, stat, step, quote, pair
BoardItem += value (stat; ^-?[0-9][0-9.,]*$), unit, date, icon_hint (≤ 24 chars),
             pins: list[FigurePin], level: Literal["recall", "apply", "transfer"] | None   # all excluded at None/empty
NarrativeRoleName += pretraining, predict, check, recap
SceneType += "clip"  (ClipScene, §8.2; never plannable)
```

Validators extend `BoardItem._kind_fields`: `stat` needs `value` and `text`; `pair` needs `term` and `text`; `quote` needs `text` and `caption`; `question` and `step` need `text`; `blank` is allowed on `example_step` and `question`; `Beat.fill_item_id` may target a blank `question`; `Beat.answers` must name an item in an earlier scene.

**As built in B0.** `FigurePin` (left open above) is `{id, x, y, label}`, and `pins` are allowed on `figure` items only; overlay ids are unique within a scene; `PlannedScene` refuses `clip` explicitly (widening the scene types would otherwise let plans contain clip scenes). `PlannedScene.visual_intent`, the plan hook and the generator-facing narrative roles wait for B4 (§6.5), because they change what the model is asked to produce. `aadhi/schemas/features.py` holds the feature names, the marker logic and the walk that finds the features a document uses; `python -m aadhi.schemas.generate` regenerates `web/schemas/*.schema.json` (`--check` in CI).

**Reader-version marker** [RV E-3]. `StrictModel` uses `extra="ignore"` (`screenplay.py:155`), so an older build would drop unknown fields silently and write the stripped document back. From B0:
- a screenplay with any v3 field is written with `schema_version = 3`; classic documents stay `2`;
- every loader (API, worker, import) refuses a document whose `schema_version` is above the highest it knows, with 409 `newer_document` (API) or a fatal `newer_document` job error, instead of ignoring keys;
- because B0 knows every v3 field and refuses newer documents, **B0 is the rollback floor**: once any v3 document exists, never deploy a build older than B0;
- **deploy order** (in `OPERATIONS.md`): stop the workers, migrate and start the API, then start the workers on the same image; `docker compose up -d` alone restarts services independently.

**Transform survival test** (B0). A property test builds a maximal Dynamic screenplay (every new field, every new kind, a clip scene, cuts, overlays) and asserts it survives unchanged through: `PUT /screenplay`, revert, the autosave merge, `sceneMerge.js`, `splitScene.js`, `convertSceneShape` (which drops `hidden`/`min_seconds` today, `screenplayEdit.js:83–86, 301–304`), `regenerate_scene` carry-over, `translate`, and export/import.

**Asset authorisation** [RV E-2]. `asset_uses()` (`aadhi/api/screenplay_service.py:56`) feeds `asset_key_errors`, `authorize_asset_keys` and import's `strip_unauthorized_asset_keys`. B0 extends it to every new key field:

| Field | Allowed asset kinds | Family |
|---|---|---|
| `ClipScene.clip.asset_key`, `Overlay.clip.asset_key` | `clip`, `upload`, `video` | motion |
| `Presentation.music.asset_key` | `music`, `upload` | audio (new) |
| `Beat.voice_override_asset_key` | `narration_upload` | audio |

`AssetRef` rows are inserted for the project when such an asset is uploaded, normalised or generated. Tests: a `PUT` naming another teacher's clip or music key returns 422; import strips it. As built in B0, the table is `aadhi/assets_auth.FIELD_RULES` and `record_asset_refs` is the one helper that inserts `AssetRef` rows. No migration is needed: `asset_refs` already has its unique `(project_id, asset_key)` and `Asset.kind` is a string column. Import removes an overlay or a clip scene whose clip it cannot keep (its key is required) and takes a removed scene out of `chapters[].scene_ids` and `look.continues_from`.

**Classic fallback map** so a Dynamic screenplay never breaks a classic surface such as the editor's rich preview:

| New kind | Rendered in classic as |
|---|---|
| `question` | `callout_tip`; the answer shows when filled |
| `stat` | `heading` + `paragraph` |
| `step` | `bullet` |
| `quote` | `callout_info` |
| `pair` | `bullet` "term — text" |

**Hashing.** `look` and `overlays` join the `scene_hash` exclude set (`pipeline/assets.py:202–220`), so a look change or an overlay edit never forces a `build_assets` job; `cuts`, `speaker` and `voice_override_asset_key` stay in the hash because they change the audio [RV E-24].

**Translation fields** (`TRANSLATABLE_FIELDS`, `screenplay.py:52–60`) gain `BoardItem.unit`, `BoardItem.date` and `ClipCaption.text`. `icon_hint`, `value`, `ClipScene.credit` and stock attributions are **protected**: an attribution must stay exactly as the licence requires.

**`aadhi/schemas/timeline.py`**

```python
class TimelinePresentation(StrictModel):
    style: Literal["dynamic"]
    format: Literal["landscape", "portrait"]
    safe_zone: str = "universal"
    content: dict[str, str]           # resolved content tokens (hex): ink, ink2, entity:0..2, warn, ok, mark, focus
    fonts: dict[str, str]             # role -> family id
    personality: str
    caption_style: str
    cue_term_style: str
    captions_burn: bool
    sfx: bool
    motion: Literal["standard", "calm"]
    generated_seconds: float          # R-01 (§1.4)
    engine_version: int = 1           # stage contract (§7.3)

class ResolvedLook(StrictModel):
    template: str
    variant: str | None = None
    tokens: dict[str, str]            # per scene: struct.accent, bg, bg-alt, bg-tint (resolved hex)
    background: dict[str, Any]        # {preset, params, seed, frozen_at: float | None}
    transition_in: dict[str, Any]     # {kind, seconds}
    hold_reason: str | None = None
    presenter: dict[str, Any] | None = None   # {clip_key, crop:[x,y,w,h], rect:[x,y,w,h], t_exit, src_fps}
    text_fit: dict[str, Any] = {}     # slot -> {size, lines} from textfit.py

class VisualEvent(StrictModel):       # Python decides WHEN (events.py); JS decides HOW
    t: float                          # scene-relative
    kind: Literal["reveal", "fill", "cue_term", "look_here", "draw", "count", "var", "output",
                  "countdown", "answer", "exit"]
    target: str | None = None         # board item id, "item:part", "panel"
    dur: float | None = None
    anchored: bool = False            # target named within ±300 ms (§1.4); only these count for R-15
    sfx: str | None = None
    words: str = ""                   # anchor words (Studio only)

class KineticWord(StrictModel):  text: str; start: float; end: float; key: bool = False
class KineticChunk(StrictModel): start: float; end: float; words: list[KineticWord]; lines: list[str]; blank: bool = False

class MusicCue(StrictModel):
    asset_key: str; start: float; end: float; offset: float = 0.0
    gain_lu: float = 0.0; fade_in: float; fade_out: float; xfade: Literal["qsin", "tri"] = "qsin"
    loops: list[tuple[float, float]] = []          # fitted loop segments (§5.10)
class MusicPlan(StrictModel):
    cues: list[MusicCue]; duck: list[tuple[float, float]]   # (absolute t, gain dB)
    mode: Literal["bed", "gaps_bookends", "off"]; ref_lufs: float = -16.0; level: Literal["normal", "low"] = "normal"
class SfxCue(StrictModel): t: float; sound: str; gain_db: float

Timeline.presentation: TimelinePresentation | None   (exclude None)
Timeline.music: MusicPlan | None                     (exclude None)
Timeline.sfx: list[SfxCue]                           (exclude empty)
TimedScene.look: ResolvedLook | None                 (exclude None)
TimedScene.events: list[VisualEvent]                 (exclude empty)
TimedScene.kinetic_captions: list[KineticChunk]      (exclude empty)
TimedScene.overlays: list[TimedOverlay]              (exclude empty; §8.2)
MediaRef += trim_in: float = 0, audio: bool = False, volume_db: float = 0, focus: tuple[float, float] | None,
            src_fps: float | None, end_behavior: Literal["freeze", "loop", "clamp"] = "clamp"; fit adds "contain_blur"
```

**As built in B0.** `MediaRef.end_behavior` keeps its existing default `"loop"` (a `"clamp"` default would change every classic timeline); `"clamp"` and `fit="contain_blur"` are accepted values the player handles from B1/B6. `build_timeline` sizes `width` x `height` with `compose.base.stage_size(presentation.format)`.

**As built in B2a** (`aadhi/schemas/timeline.py`; every addition is left out of the JSON at its default, so a B1 timeline reads and writes back byte for byte; deviation 4):
- `TimelinePresentation.engine_version` is the **look-engine generation** (deviation 21): 1 = B1's resolver, 2 = B2's (`compose.presentation.ENGINE_VERSION`); the stage contract stays `aadhiStage` v3 (§7.3). New fields: `content_alt` (the other mode's content tokens, with the keys of `content`), `family`, `mode`, `motion_scale` (0 < x ≤ 4, left out at 1.0) and `entity_colors` (the lesson-wide map, at most 60 entries, slots 0–2, with `Presentation.entity_colors`' rules; deviation 22).
- `ResolvedLook.tokens` carries the ten keys of §4.7 from engine 2 (upper-case `#RRGGBB`; `scrim` is `#RRGGBBC7`); engine 1 has the first five. New fields: `fixes` `{safe, slots: {slot: {size?, scrim?}}}` (preflight's channel, deviation 2, §7.12); `icons` (`{item_id: {name, set, version, viewBox, stroke_width, filled, paths}}`, at most 32 per scene, each path at most 4,096 characters of path data matching an allow-list pattern); `refs` `{roadmap?: [{title, state}], anchor?: {kind, text | tex, icon?}, prediction?: {text, resolve_t?}, next?: {title}, accent_next?}`; `accent_fade` (0.6–1.0 s); `continues_from` (kept only when it names the scene just before). `variant` is direct.py's and never `"safe"` in a B2-resolved timeline.
- Not filled by the resolver yet: `accent_fade` and `tokens.accent_prev` (B2e), `refs` and `icons` (B2e; B2b built the icon module, §4.12), 9:16 act- and segment-based slots (B2e). `presenter` keeps its B1 shape until B2f. (`fonts.indic` is filled from B2b, below.)
- For the owner's review (not in this design): the roadmap states `done` / `current` / `upcoming`; the anchor kinds `formula` / `figure` / `takeaway`; the icon caps (32 paths of 4,096 characters, 32 icons per scene, stroke width 0–8).
- A stored Dynamic timeline of the current revision built by an older generation is served rebuilt (§7.3).

**As built in B2b** (`aadhi/schemas/timeline.py`, `compose/presentation.py`; §4.6):
- `TimelinePresentation.fonts` is a typed `TimelineFonts {display?, body?, mono?, indic?}` (it was `dict[str, str]`). Each value is a `CatalogueId` naming a family of `compose/dynamic/faces.py`, never CSS text, and each key is left out when unset, so a stored B1 or B2a `{display, body, mono}` reads and writes back byte for byte (tested on 5 shapes, and on 39 timelines built by B1, B2a and B2b). Like every schema model it ignores unknown keys: a stored fonts dict with other keys would lose them on rewrite (the B1 and B2a resolvers wrote only those three).
- `indic` (`noto_sans` or `anek`) marks the **B2 form**, which the stage feature-detects (§7.3). The B2b resolver fills it: `fonts = faces.resolver_fonts(display_font)`, which names exactly the faces the B1 stage drew. A display id the B1 stage did not draw is stored as `outfit`. `ENGINE_VERSION` stays 2, so B2a-built engine-2 timelines are not rebuilt and keep their B1 form; both forms draw the same frames (§10.3 B2).
- `ResolvedLook.icons` and its A2 caps are unchanged. `icons.scene_icons` produces valid entries, and every indexed icon fits the caps (at most 32 paths). `ResolvedLook.text_fit` is still unwritten; `textfit.FitResult.stored()` gives the proposed entry `{size, lines}` (the line count) for B2e to write for certified fits only. The stage reads `text_fit[slot].size` as a size cap (`core.js` `fixesOf`), so an Indic estimate must never be stored. *(B2b-H: Indic text is shaped with HarfBuzz and certified like Latin text, so a certified Indic `stored()` may be written. The rule is now the general one: write only certified results (`fits` True or False), never an estimate (`fits` None), whatever the script. The schema is unchanged.)*

**Timeline invariants.** `width` and `height` follow the format; `intro` is `None` for Dynamic; the existing time-base validator is unchanged; new checks keep events, kinetic chunks and overlays inside their scene. Resolved tokens are **stored** in the timeline, so old timelines keep their look if the catalogue changes [CB §2 #23].

**`aadhi/schemas/manifest.py` and models**

| Location | Change |
|---|---|
| `BeatAudio` | `persona: str \| None`; `words_exact: bool` — true when the provider's word count equals the caption tokens and no `spoken` override applies (§8.3) [RV E-11] |
| `SceneAudio` | `lufs: float \| None`; `speaker_gains: dict[str, float]` (§5.4) |
| `AssetManifest` | `music: list[MusicAsset{asset_key, provider, model, prompt_hash, seed, duration, lufs, bpm, key, loop_points, sections, licence}]` |
| `MediaInfo` | `has_audio: bool` |

All are excluded at their defaults.
- **Licence metadata.** `Asset.meta` carries `provider`, `source_url`, `creator`, `licence_id`, `licence_url`, `attribution`, `rights_confirmed_by`, `confirmed_at`.
- **Asset kinds.** `models.PUBLIC_ASSET_KINDS` gains `music`, `clip`, `clip_proxy`, `clip_sprite`, `waveform`, `voice_sample`, `thumbnail` and `narration_upload` (a string column, no migration); `calibration` stays private [RV E-32]. `GC_ASSET_KINDS` (`aadhi/jobs/cleanup.py:61`) gains each kind in the batch that introduces it [RV E-26].
- **Generated artifacts.** `web/schemas/*.schema.json` and the JSDoc types are regenerated, as the existing consistency checks require.

### 6.8 Deterministic stages and the editor

| Module | Decides | Inputs | When |
|---|---|---|---|
| `pipeline/scope.py` | coverage estimate and series split | brief, format, depth | after `brief` (§6.4) |
| `pipeline/casting.py` | narrator, Aadhi and student personas | language, format, keys, preferences, history | generation, Fresh look (§5.3) |
| `pipeline/direct.py` | templates, variants, backgrounds, transitions, accents, family, personality, fonts, caption style, entity colours, `hold_reason`, text fit | screenplay, format, seeds, history, locks | generation; on save **only for scenes whose look is `auto` or whose template no longer fits**; New colours / New layouts |
| `compose/dynamic/events.py` | event times, anchoring, cue targets, SFX times | timed beats, words, sync cues, template specs | every `build_timeline` |
| `compose/dynamic/kinetic.py` | caption chunks and blanking | words, lexicon, `[[term]]`, on-screen text | every `build_timeline` |
| `pipeline/music.py` + `compose/dynamic/mix.py` | music assets, plan fitting, envelope | chapters, timings, preferences, history | assets; every `build_timeline` |
| `pipeline/fit.py` | duration fit | timeline, profile | after `build_assets` (§6.4) |

**Editor saves** [RV E-23, T-2]. When `direct.py` fills looks on save, the `PUT /screenplay` response returns the updated screenplay and its new ETag; the editor adopts it as the result of its own save, not as a remote change to merge. A one-sentence edit therefore never re-styles other scenes, never invalidates their render chunks and never rewrites the document under autosave.

**Determinism.** Every stage is a pure function of its inputs and the stored seeds; the seeds and every resolved choice are stored, so a rebuild never re-rolls. History is read only at generation, Fresh look and the two shuffle actions.

### 6.9 Validation and lint

These codes live in `aadhi/pipeline/dynamic_lint.py` and run only for Dynamic lectures. `fixable=True` means a scene rewrite may fix it; a non-fixable finding never triggers a paid rewrite. **Audience** [RV T-18]: *teacher* findings appear in the issues panel in plain words with a one-click action; *system* findings go only to evals, the job log and the Look lane hints, so the panel never fills with things a teacher cannot act on.

| Code | Severity | Fixable | Audience | Rule / action |
|---|---|---|---|---|
| `coverage.dropped` | error | no | teacher | R-61 · Make a series / Choose focus / Accept |
| `format.duration` | warning (platform-safe: preflight error) | no | teacher | R-01 · Fit to 2:58 / Hide a scene |
| `format.segments` | warning | no | system | R-02 |
| `hook.late` / `hook.unanswered` | warning / error | yes | teacher | R-03, R-04 · Rewrite |
| `pretraining.missing` / `pretraining.unused` | warning | yes | system | R-05 |
| `checkpoint.missing` / `checkpoint.early` / `checkpoint.think_short` | warning | yes | teacher / system / system | R-06, R-07 · Add a check |
| `predict.order` / `predict.unanswered` | error | yes | system (repair) | R-62 |
| `recap.missing` / `recap.relational` / `recap.transfer_missing` | error (16:9) / warning / warning | yes | teacher / system / system | R-08 · Add recap |
| `example.missing` / `example.no_fade` | warning | no / yes | system | R-09 |
| `misconception.unused` | warning | no | system | R-10 |
| `myth.unrefuted` / `myth.in_recap` | error | yes | teacher | R-63 · Rewrite |
| `aadhi.misplaced` | error (generated) / warning (teacher edit) | no | teacher | R-12 · Move to start |
| `aadhi.too_long` / `aadhi.words` | error | yes | teacher | R-12, R-13 · Shorten Aadhi's line |
| `pacing.static` / `pacing.hold` / `pacing.reengage` | warning | no | system (Look lane hint) | R-15, R-16, R-22 |
| `scene.length` / `look.variety` | warning | no | system | R-19, R-20 |
| `text.too_long` / `text.overflow` / `text.reading_budget` | warning | yes | teacher | R-25, §4.6, R-64 · Shorten text |
| `text.verbatim` | warning | yes | system | R-25 (exempt templates listed there) |
| `signal.too_many` | info | no | system | R-26 |
| `captions.cps` | warning | auto (chunker) | system | R-33 |
| `voice.rate` | warning | re-synthesise | teacher | R-36 · Re-voice |
| `voice.aadhi_misplaced` / `voice.secondary_share` / `voice.student_role` | error / warning / warning | yes / no / yes | system | R-38 |
| `stat.ungrounded` / `quote.ungrounded` | error | yes | teacher | §4.3 · Rewrite / Remove |
| `portrait.hook_overflow` / `portrait.code_too_wide` / `portrait.formula_wide` / `portrait.table_wide` | warning | yes | teacher | §2.5, §4.3 · Rewrite shorter |
| `clip.*`, `edit.cut_stale`, `overlay.reanchored`, `overlay.hides_reveals` | warning / info | no | teacher | §8 · Trim / Mute / Restore |

**Per-format thresholds** replace today's global constants [CB §7.4]:

| Constant | Classic (unchanged) | Dynamic 16:9 | Dynamic 9:16 |
|---|---|---|---|
| `MAX_BOARD_ITEMS` | 6 | 6 (≤ 3 visible at once) | 4 |
| `BEAT_MAX_WORDS` | 60 | 30 | 20 |
| `SCENE_MAX_SECONDS` | 150 | 45 | 30 |
| `CODE_MAX_LINES` / `CODE_MAX_LINE_CHARS` | 18 / 80 | 18 / 60 | 10 / 32 |
| `TABLE_MAX_COLS` | 4 | 4 | 3 |

**Skipped in Dynamic:** `presenter_lint` and `layout.mascot_crowds_board` [CB §2 #16].

**The critic prompt** gains Dynamic checks: the hook is a real question; one concept per reel; Aadhi's lines are brief; no claims beyond the source in `stat` or `quote`; recap ideas are relational; myths are refuted at once.

### 6.10 Reels from lessons and "Remake"

**"Make a reel from this lecture"** (project page) and **"Also make a reel"** (form) start a generation with `video_format="portrait"`. Checkpoints are job-scoped and load only on retries (`aadhi/pipeline/checkpoint.py`: "another job … never reuses them"), so the parent's work is passed **explicitly** [RV E-22]: the parent version's brief (`orchestrator._version_brief`), its source extract and its source overrides go into the new job's payload. No ingest cost is repeated. The parent screenplay is extra planner context, with the instruction to condense to **one concept** with 5–7 micro-segments while keeping the hook. The result is a new `ProjectVersion` with its own `Presentation` (fresh look and voice, excluding the parent's); the versions list shows a format chip. A reel is a **re-cut, not a crop** [CP §7.2].

**"Remake as Dynamic video"** for classic lectures uses the same explicit hand-over.

### 6.11 Translation

`translate` handles Dynamic lectures as follows:
- copies `presentation` and every `look`; re-casts voices for the target language (§5.3);
- switches the display face to Anek for Indic scripts;
- rebuilds kinetic captions with grapheme-aware segmentation (`Intl.Segmenter` in the page; the same rules in `kinetic.py`);
- drops narration cuts and reports how many;
- keeps clip scenes and overlays, and **carries overlay anchors** through the translation as inline markers (`⟦o:<id>⟧…⟦/o⟧`) that the translation prompt must preserve; an overlay whose marker is lost falls back to the beat start with `overlay.reanchored` [RV E-24];
- translates the fields listed in §6.7 and protects attributions and icon hints.

### 6.12 Evals and offline fakes

`aadhi/evals/metrics.py` gains these metrics, computed from the timeline and screenplay, with thresholds from §1.5 [LS K]:

| Group | Metrics |
|---|---|
| Aadhi and structure | `aadhi_seconds_total`, `segment_seconds`, `has_hook_prequestion`, `checkpoint_questions`, `think_seconds_min`, `has_retrieval_recap`, `recap_relational_share`, `predict_before_explain`, `distinct_templates`, `distinct_classes`, `coverage_share` |
| Pacing and signalling | `max_static_seconds` (anchored events), `median_anchored_event_interval_s`, `cues_per_key_term`, `concurrent_cues_max`, `board_items_visible_max`, `sfx_per_minute` |
| Speech and captions | `speech_wpm_p90`, `speech_wpm_max_10s`, `caption_cps_max`, `reading_budget_max_ratio` |
| Audio and contrast | `music_under_speech_lu_gap`, `music_level_during_think`, `min_text_contrast` |
| Fading | the existing fading metric (blank steps, fills, think pauses) |
| Rotation | `voice_differs_from_previous`, `local_accent_share`, `music_not_recent`, `family_not_recent` |

**Offline fakes** [RV E-16]. CI forces fake providers, and the fake writer (`aadhi/pipeline/fake_content.py`) knows only today's kinds, so the new error-level lints would fire on the offline end-to-end run and on the demo. Each batch extends the fakes it needs:
- **B3:** fake calibration, fake music (Lyria-shaped responses including a rejected one with lyrics), fake Azure word boundaries;
- **B4:** the fake writer produces an Aadhi intro with `speaker="aadhi"` beats, 2–4 pre-training terms reused later, a hook with an `answers` beat, a predict and its answer, checks, a faded worked example, a refuted myth, a relational recap and every new item kind;
- **B6:** a fake stock provider (Pexels/Pixabay shapes, rate-limit responses).

**Runs.** The three PDFs in `evals/fixtures` run in both formats in B4. `evals/render_parity.py` gains instants inside transitions and animations; `evals/video_qa.py` gains the new checks.

---

## 7. Rendering

### 7.1 Two engines

| Engine | Used for | How it works |
|---|---|---|
| `states` (existing) | Aadhi presenter tab | One transparent PNG per visual state; ffmpeg composites the mascot clip, media in rectangles and premultiplied cross-fades (`compose/video.py`, `screenshot.py`, `ffmpeg.py`). **Unchanged.** |
| `motion` (new) | Dynamic videos | **The page owns every pixel.** Every output frame is captured from the stage at an explicit time; workers run in parallel on scene-aligned chunks; the chunks are concatenated; audio is mixed once |

The render job runs **preflight** first (§7.12), then picks the engine from `timeline.presentation.style`; the render token needs no new claim [CB §1]. One render never mixes engines.

### 7.2 Why full-frame capture

The state engine cannot express continuous motion: it allows only cuts and 0.15 s cross-fades between still states, CSS animation is disabled in render mode, and the page runs with `reduced_motion="reduce"` [CB §5.1].

| | Hybrid: states + "motion windows" + ffmpeg backdrop and media [CB §5.4] | **Full frame, page owns pixels** [MR §4] (chosen) |
|---|---|---|
| Media | Fixed rectangle per scene; cannot move with the camera, take a mask or carry annotations | Media frames are page elements: camera moves, masks, rounded corners and annotations over footage work |
| Transitions | Composited in ffmpeg inside the incoming segment | Any transition, in the page, as a pure function of `t` |
| Backgrounds | Pre-rendered seamless loops | Painted live; loops need no seam; frozen during holds (R-55) |
| Preview parity | Two compositors to keep equal | One compositor: the live player's stage code |
| Cost | Fewer captures while motion stays rare | Every frame captured (§7.10) |
| Precedent | — | Remotion, HyperFrames, Motion Canvas and Revideo all "seek, don't play" [MR §0] |

Dynamic mode has anchored motion every few seconds (R-15), so the hybrid would degrade toward per-frame capture while keeping two compositors. **Static-run deduplication** (reuse the previous frame's bytes when the stage reports an unchanged state key) saves 20–50% on hold-heavy scenes — more now that backgrounds freeze during holds — and ships in B9 after correctness is established [MR §4.4].

**What was measured, and what was not** [RV E-5]. MR measured on the dev laptop (i7-13650HX, chrome-headless-shell 153, Playwright 1.63):

| Method | ms per frame |
|---|---|
| Playwright `page.screenshot` PNG + `omit_background` (today) | ≈ 390–400 |
| CDP `Page.captureScreenshot` PNG + `optimizeForSpeed` (rich / simple page) | ≈ 110 / 34 |
| **CDP JPEG q90**, 1920×1080 / 1080×1920 | **36.4 / 40.9** |

Aggregate capture throughput was 26.5 fps with 1 worker, 53.1 with 4 and 59.3 with 6; one worker with x264 `veryfast` produced 20.5 fps end to end. Those runs were **capture-only** (90 frames per worker including browser launch), used `--disable-frame-rate-limit` (not in today's `CHROMIUM_ARGS`, `screenshot.py:50–63`), had **no** request interception (production routes every request through Python, `screenshot.py:433`), no media `<img>` frames, no MathJax or Chart.js, and no `requestAnimationFrame` wait in `seek()`. They are evidence that the approach is viable, not a render-time estimate. The B1 acceptance benchmark (§7.10) replaces them.

### 7.3 Stage contract v3

The render page is `web/render-dynamic.html` + `web/js/dynamic/render.js`, served by a **new route `/render-dynamic`** with the same token-in-fragment scheme, origin allow-list and Chromium sandbox as `/render-frame` [RV E-19]. `/render-frame` is a static page and never sees the timeline (`aadhi/api/routers/pages.py:57`, token in the fragment, `screenshot.py:158`), so the **worker** picks the route from the timeline it already holds. The classic `web/render.html` contract (`aadhiRender`, version 2) is unchanged.

```js
window.aadhiStage = {
  version: 3,
  ready,            // Promise<{webgl: boolean}>: timeline loaded; document.fonts.load() for every
                    // family/weight/script in use; MathJax idle; static images decoded; WebGL probe
  info(),           // -> {width, height, fps, frames, scenes: [{index, f0, f1}]}
  mediaPlan(f0, f1),// -> [{key, kind: "video"|"aadhi", srcFps, frames: [srcIndex, ...], size: [w, h]}]
  seek(frame),      // -> Promise<{key}>; t = frame / fps; pure; frames may be requested in any order
  textBoxes(),      // -> [{x, y, w, h, role, critical, ink, bg, overflow}] for preflight (zones, contrast, fit)
  setLayers(spec),  // {captions: bool, stage: bool}: the caption-only pass for a second version (§7.5)
};
```

`seek(frame)`:
1. Set `t = frame / fps` exactly.
2. Compute the scene state with `schedule.sceneStateAt`, extended with `TimedScene.events`.
3. Run the template's `update(t)`; paint the background at `t` (or its frozen time).
4. Composite the transition (§4.9) and the captions (unless blanked).
5. Point each visible media element at its pre-extracted frame (`/__frames/…`, §7.7) and `await img.decode()`; a lookahead of 10 frames is prefetched.
6. Wait for MathJax idle, then one `requestAnimationFrame`.
7. Resolve with `key`, a hash of every visual input (used for deduplication).

There are no fixed sleeps.

*As built (B1).* The frozen contract with its additions (`mediaPlan` fields, `textBoxes()` roles and slots, the render-mode caption layer) is in `docs/INTERFACES.md`. The engine checks the page of **every** capture worker, and the remedy capture's page, against the timeline before it captures: `info()` must give the stage size, the rate, the total frames and every scene's frame range exactly (`compose/motion/engine.py` `_check_layout`); a page that lists no scenes for a timeline that has some is a `stage_contract` mismatch too (`stage.scene_mismatch`).

*As built (B2a)* (additive; `docs/INTERFACES.md`):
- **Look-engine generation** (§6.7). `web/js/dynamic/engine.js` `ENGINE_VERSION = 2`. A timeline whose `presentation.engine_version` is higher is refused with `timeline_invalid`: the render page rejects `ready` before it loads a font, `createDynamicStage` throws, and the player's `load()` rejects a live or preview Dynamic timeline before it builds anything. A value that is not an integer ≥ 1 is read as 1, as the API reads it. Otherwise the stage feature-detects fields and never branches on the generation for drawing.
- **Serve-time rebuild.** `api/timelines.current_timeline` also rebuilds (`build_timeline` is pure; the stored row is not written) a Dynamic build of the current revision that is still wanted Dynamic and whose `engine_version` is below `ENGINE_VERSION`, so the player shows what a new MP4 shows. A newer generation and a stale build are served as built. The timeline ETag carries `engine=<ENGINE_VERSION>`, and a 304 is still answered before any rebuild.
- **TeX.** The render page pre-converts the union of every scene's template `tex()` (B1's field list for `board_generic`) through one page-wide converter that never evicts (the live player's keeps 400 strings), and the stage converts through it too. `aadhiStage.texConversions()` (additive) counts the conversions started after the declared strings were converted, the first frame's included, and a declared string whose conversion failed and is retried: 0 means every template declared its TeX. Templates convert TeX only through `ctx.renderTex` (source scan, §7.4). The gallery (B2g) asserts 0 per fixture; `stage_page.py` does not read it yet.

*As built (B2b)* (additive; `docs/INTERFACES.md`):
- **Font requests.** `ready` loads `web/js/dynamic/fonts.js` `stageFontRequests(timeline)` before it resolves.
  - For the **B1 form** of `presentation.fonts` (no `indic`) these are `render/fonts.js` `fontRequests`, unchanged: B1 and B2a timelines load exactly what they loaded before (a jsdom test checks the boot's `document.fonts.load` calls, in order).
  - For the **B2 form** they are the requested weights of the body, display and mono families (each family once, in that order) with a Latin sample, then the Indic group's family for each script the timeline uses, with that script's sample. For the resolver's display `outfit` that list equals B1's. For display `inter` it is B1's without the four Outfit faces: no stack holds Outfit then, so the pixels are the same.
  - A real-Chromium test (`tests/determinism/test_stage_fonts.py`) checks that every face is loaded before `ready` and none after. The faces in use are loaded, the B2b faces stay unloaded for a B1 timeline, and nothing is still loading or in error when the frames are captured.
- **Stacks.** `fontStacks` moved from `stage.js` to `fonts.js` (re-exported). Its B1 path is B1's code with one deliberate change: the display id is looked up as an own property. An id that is an `Object.prototype` name (`constructor`, `__proto__`) used to write a function's text into the CSS stack and is now drawn as Outfit. No resolver or planner produces such an id.
- `render/fonts.js` exports `LATIN_SAMPLE` (no behaviour change). The live player (`web/js/player/fonts.js` `ensureFonts`, live and Studio) still preloads B1's faces only. Nothing changes until `direct.py` names another face; once B2e does, the live player would load Poppins, Lexend, Atkinson or Anek lazily (a brief fallback flash; the render page is unaffected). B2e should preload `stageFontRequests` for Dynamic timelines.

**Live player.** `web/js/dynamic/stage.js` plugs into the existing player shell (controls, clock, captions toggle, analytics, checkpoint interaction). Each `requestAnimationFrame` reads `t` from `clock.js` and calls `stage.update(t)`. Media play as real `<video>` elements using proxies (§8.4), re-sought when drift exceeds 0.1 s [ED §8]. The narration `<audio>` stays the master clock.

**Allowed live/MP4 differences** (both flagged in analytics):
1. **Low-end devices:** if the live frame time stays above 24 ms for 2 s, particles and shader tiers drop to their CSS twins.
2. **Reduced motion:** the mapping of §4.8 when the viewer's device prefers reduced motion [RV T-22].

### 7.4 Determinism strategy

1. **Explicit time.** Every visual property is a pure function of `(timeline, frame)`. No library runs its own clock.
2. **Banned in `web/js/dynamic/**` and `web/js/motion/**`**, enforced by a `node --test` **source scan** that parses the files with the TypeScript compiler API (`typescript` is already a devDependency; no ESLint is added) [RV E-14], plus a scan of `web/css/dynamic.css`:
   - `Date.now`, `performance.now`, `Math.random`;
   - template-owned `requestAnimationFrame` loops;
   - CSS `transition`, `animation` and `@keyframes`;
   - Chart.js animations, tsParticles and other self-clocked libraries;
   - `<video>` in render mode [MR B5];
   - structure colour roles on content elements, and emphasis code outside `cues.js` (§4.2).
3. **Timer guard** [RV E-7]. After `ready` resolves in render mode, `web/js/dynamic/guard.js` replaces `setTimeout` and `setInterval` with functions that throw, except for an allow-list of call sites (none in our bundle). Font and MathJax start-up run before `ready`, so they keep real timers. A guard trip is logged and fails the golden and determinism tests.
4. **No `page.clock`.** Playwright's clock overrides `requestAnimationFrame`, `setTimeout` and `requestIdleCallback`, which fire only when the clock is advanced, so a paused clock would deadlock `seek()` (step 6) and the font and MathJax waits. Revision 1's `page.clock.install()` backstop is withdrawn. CDP `Animation.setPlaybackRate(0)` stays as the backstop for stray CSS animation.
5. **Seeded randomness.** mulberry32 seeded with `hash(look_seed, scene_id, purpose)`.
6. **Fonts and text.** Vendored WOFF2 with explicit loads; emoji as images; the existing flags `--force-color-profile=srgb`, `--font-render-hinting=none`, `--disable-lcd-text`.
7. **Tests.**
   - **(a) Determinism**, in the Docker `test` image (blocking CI job, §10): 300 frames rendered as 1×300, as 3×100 and in reverse order give identical SHA-256 per frame.
   - **(b) Parity:** live player against MP4 at 20 random instants, SSIM ≥ 0.98 (`evals/render_parity.py`).
   - **(c) Windows development:** Windows renders are compared only with Windows previews; DirectWrite and FreeType rasterise text differently [MR §5].

*As built (B1).* Measured necessities, each found because frames depended on the order they were rendered in:
- **No `will-change`**, whole-pixel positions and no scale for moving cards, and a media frame that enters by rising, never by scaling (`board_generic.js`). The one `will-change` in the stage is a `contain_blur` clip's box (`.dyn-media--contain_blur.dyn-media--clip` in `web/css/dynamic.css`; `MediaView` adds `dyn-media--clip` to video views). Chromium composites every shown 2D canvas as a layer of its own (CDP reason "Canvas", even with `--disable-gpu`), so the backdrop canvas (§7.7) gave the clip above it a layer whose contents depended on render order: 93 of 120 16:9 frames differed between a forward and a reverse render, by up to 62 levels. With the box promoted (it has no transform of its own) none differ in either format. Pictures, stills with Ken Burns included, must not be promoted: with the box promoted, the Ken Burns still failed 62 of 120 9:16 frames.
- **Ken Burns** scales a still inside its frame with a CSS scale and translate (`web/js/player/kenburns.js`); measured order-independent on Windows in both formats, across the 50% scale where Chromium changes mip level (`tests/determinism/test_kenburns_determinism.py`); its run in the container is pending with the CI `container` job.
- **No partial raster.** Chromium re-rasterised only the invalidated part of a tile, and Skia's antialiasing over that smaller clip differed (≤ 3 levels) from a full-tile raster: a MathJax glyph in a card that changed colour then depended on what was painted before it, and 76 of 300 frames of the portrait QA fixture differed between 1×300 and reverse. The Dynamic stage's Chromium is therefore launched with `DETERMINISM_ARGS = ("--disable-partial-raster",)` (`aadhi/compose/dynamic/stage_page.py`) on top of `CHROMIUM_ARGS`: 0 frames differ in either format, at no measurable capture cost (median 73–74 ms per frame with and without the flag on the development laptop, whose timings are noisy). The classic capture keeps `CHROMIUM_ARGS` unchanged (§3.6).
- **Tests.** (a) is `tests/determinism/test_frame_hashes.py` (the QA fixture in both formats) and `test_kenburns_determinism.py` (a Ken Burns still, a `contain_blur` still and a `contain_blur` clip from pre-extracted frames served through `/__frames/`, the clip both with its backdrop shown and in a frame that fits it), all marked `slow` and `container`. `tests/compose/motion/test_launch_args.py` pins the capture worker's real launch flags to `StageOptions.chromium_args()`, which is part of the chunk cache key (§7.6): a new launch flag goes into both `stage_page.stage_browser` and `StageOptions.chromium_args()`.

*As built (B2a): scan additions* (`docs/plans/b2/b2_plan_v2.md` §P, §1.1, §1.4, §2; `web/tests/dynamic/sourceScan.test.js`, each rule with self-tests that must catch and must pass):
- A template may not write `will-change`, `filter`, `backdrop-filter` (rule `template-style`) or `outline`, `text-decoration` (rule `emphasis`) through any path the scan knows: style properties, `setProperty`, `Object.assign` on a style, CSS text, the Painter's `style` / `attr`, `h()` / `svg()` style objects and attributes, `setAttribute` / `setAttributeNS` / `toggleAttribute`, the CSS Typed OM (`attributeStyleMap.set` / `append`) and attributes in markup strings. Names are matched as camelCase style keys and case-insensitively; a name or markup the scan cannot read is refused. `dynamic.css` declares none of them except its one measured `will-change`.
- A template never reads `.timeline` (rule `timeline`).
- A template converts TeX only through `ctx.renderTex` (rule `tex`): `mountTex`, `renderRich` and `texNode` imported by their own names and only called, each call handing on the converter as the last word on that option; no MathJax and no `shared/libs.js` converter directly.
- Only `core.js` imports the catalogue, and every read of it follows an allowed geometry path (rule `catalogue`); `catalogue.gen.js` is one frozen literal (rule `catalogue-data`).
- **Not followed** (listed in the scan's header): an alias of a style object or of a write function, `Reflect.set` / `Object.defineProperty` on a style, a markup attribute name produced by a substitution, stylesheets other than `dynamic.css` and the render page's, a local `renderTex` that is not the context's, and a module outside `templates/` that converts TeX for a template. A context not named `ctx` is also outside the `tex` rule. The three gaps the B2a review's last audit found (a markup attribute after `/` or straight after a quoted value, another object's `.renderTex`, a TeX helper re-exported under another name) were closed after the review (`docs/plans/b2/b2a_review.md`).

*As built (B2b): scan additions.* The generated `web/css/dynamic-fonts.css` is scanned like `dynamic.css`. The generated face table `web/js/dynamic/faces.gen.js` must be one frozen literal (rule `faces-data`, the catalogue's data rule generalised), and `fonts.js` is its only reader in the stage.

### 7.5 Capture and encode

**Capture.** Each worker runs one chrome-headless-shell process with one page and a CDP session: viewport = format size at DPR 1; `Page.captureScreenshot {format: "jpeg", quality: 92, optimizeForSpeed: true}` [MR C11]; today's `CHROMIUM_ARGS` (plus `--use-angle=swiftshader --enable-unsafe-swiftshader` only when the plan has WebGL presets, W2). Never `--run-all-compositor-stages-before-draw`, which spins ≈ 3.3 cores while idle [MR C12]. Not launched with `reduced_motion="reduce"`. Whether `--disable-frame-rate-limit` is safe and useful in production is decided by the B1 benchmark. *As built (B1):* the flags are `CHROMIUM_ARGS` plus `DETERMINISM_ARGS` (`--disable-partial-raster`, §7.4) plus the SwiftShader pair only with WebGL presets; the launch (`stage_page.stage_browser`) and the chunk cache key (`StageOptions.chromium_args()`) use the same list. `--disable-frame-rate-limit` is not used (inconclusive in the B1 measurements). x264 also gets `-threads 4` (§10.3 B1).

**Encode.** Every chunk uses identical parameters, so every chunk starts with an IDR frame and concatenation is safe:

```
ffmpeg -f image2pipe -c:v mjpeg -framerate 30 -i pipe:0
  -vf "scale=in_color_matrix=bt601:in_range=full:out_color_matrix=bt709:out_range=tv,format=yuv420p,
       setparams=range=tv:colorspace=bt709:color_primaries=bt709:color_trc=bt709"
  -c:v libx264 -profile:v high -preset ${MOTION_X264_PRESET:-veryfast} -crf ${MOTION_X264_CRF:-17}
  -maxrate 12M -bufsize 24M -g 15 -keyint_min 15 -sc_threshold 0 -bf 2 -x264-params open-gop=0
  -an -f mpegts chunk_NNNN.ts
```

- **Preset** [RV E-5]. Measured with 6 parallel 1080p encodes: `veryfast` 236 fps aggregate, `medium` 128 fps. Encoding competes with capture for the same cores, so the default is `veryfast` at CRF 17 (about the quality of `medium` at CRF 18 for a higher bitrate). The B1 benchmark may move it to `faster` or `medium` if there is headroom.
- **Colour conversion.** Chrome's JPEG output is full-range BT.601 with an sRGB ICC profile, so the conversion is explicit; MR verified the tags with ffprobe [MR §5].
- **Draft renders** use `veryfast`, CRF 23 and a 720p stage scale.
- **Final file.** Chunks are MPEG-TS, joined with the concat demuxer and `-c copy` (measured frame-exact: 6 chunks gave exactly 1,800 frames), and remuxed once into the MP4 with the preflight-encoded audio (§5.12), ffmetadata chapters and, for clean files, the `mov_text` track with a default-on disposition (`-disposition:s:0 default`), using `-movflags +faststart+negative_cts_offsets` [RV E-6]. Measured on ffmpeg 8.1: with `-use_editlist 0` alone the video started at 0.0667 s (2 frames after the audio); with `negative_cts_offsets` both start at 0. The file still carries one `elst` edit list per track (B0, ffmpeg 8.1), so the QA checks decoded start times rather than the absence of an edit list. B0's feature checks (`tests/compose/test_ffmpeg_features.py`, marker `container`) verify the flag on Debian's ffmpeg 5.1 in the Docker `test` image: they probe the raw timestamps with `-ignore_editlist 1` (a decoded start of 0 also comes from the default `elst`) next to a control mux without the flag, which must start late.
- **Second version** (§2.6). When both clean and burned files are requested, the clean master is captured; then the caption layer alone is captured as transparent PNG states at every caption change (`setLayers({stage: false, captions: true})`, ≈ 150 states for 5 minutes), and one ffmpeg `overlay` pass with the same encoder settings produces the burned copy.
- **Chroma.** JPEG 4:2:0 followed by x264 4:2:0 softens small saturated red or blue text, so the validator requires such text to have L ≥ 0.6 or be ≥ 32 px [MR §5].

### 7.6 Parallelism, chunks and caching

**Workers** [RV E-4].
- `W = min(6, ⌊cgroup CPU quota⌋ − 1, physical cores − 1, cpuset − 1)`, at least 1 (as built, `compose/motion/resources.py` `CpuBudget.auto_workers`: a Linux cpuset from `sched_getaffinity` bounds it too); override with `RENDER_MOTION_WORKERS`. Inside a container, Python 3.11's `os.cpu_count()` and psutil report the host's cores and ignore the CPU quota, so the quota is read from cgroup v2 `cpu.max` (or v1 `cpu.cfs_quota_us / cpu.cfs_period_us`). An "8-vCPU" server usually has 4 physical cores, which gives W = 3.
- Going from 4 to 6 workers gained only 12% [MR D15].
- **Host-wide CPU slots.** Motion renders take slots from `RENDER_CPU_SLOTS` (default = physical cores − 1), implemented as `flock`ed slot files in `RENDER_SLOT_DIR`, a host directory bind-mounted into every render container on the host, so concurrent renders in several containers do not oversubscribe the machine.
- **Correction to revision 1:** `RENDER_CONCURRENCY` limits concurrent segment encodes inside one classic render (`compose/video.py:535–549, 861–869`; `OPERATIONS.md` §4.2); jobs per container are capped by `WORKER_CONCURRENCY` (render worker default 1).
- **Compose profile.** The shipped `render-worker` limits (`RENDER_WORKER_CPUS` 4, memory 6g, shm 1gb) are too small for W workers plus encoders plus the 0.5 GB mix. A `motion` profile in `docker-compose.yml` sets ≥ 8 CPUs, ≥ 10 GB and `shm_size: 2gb`; `OPERATIONS.md` documents it. The final numbers come from the B1 container benchmark.
- A page is recycled after 1,800 frames, or when its fps falls below 50% of its first chunk [MR §3].
- *As built (B1):* every worker's page is checked against the timeline before the capture (§7.3), not only the first one's; a page that fails to come up is closed before the error is raised, so a retry on a fresh page leaves no orphan renderer. The post-render remedy that captures chunks again (§7.12) runs after the capture's slots were released, so it waits for a CPU slot of its own (cancel-aware) and holds it until its browser is closed.

**Chunks.** Scene-aligned, 240–600 frames (8–20 s); long scenes split mid-scene; assigned longest-first.

**Cache key.** `sha256` of: the stage bundle hash (`web/js/dynamic`, `web/js/motion`, `dynamic.css`, fonts, vendor manifest); `engine_version`; the frame range; the timeline slices that affect those frames (the scene, the outgoing scene's final state, the global presentation); the media asset keys; the encoder parameters; the Chromium version. Chunks live in the render workspace (`compose/workspace.py`); a retry or a one-scene edit re-renders only the chunks it touches [CB §1]. Settled looks (§4.4) keep untouched scenes' chunks valid across edits. *As built (B1):* the key also holds the capture parameters (JPEG quality, layers and the Chromium launch flags, `StageOptions.capture_fingerprint`), and chunks are reused only by a retry of the same render (a new render gets a new workspace); the full field list is in `docs/INTERFACES.md` (`motion.chunks.chunk_key`).

*As built (B2a):* the timeline slices also hold `background_clock`, the stage's background time at the chunk's first frame (`state.js` `backgroundTime`: the time minus every earlier frozen stretch, mirrored by `chunks.background_clock`), because a hold in an earlier scene moves the background of every later chunk and no slice of those chunks covered it; `CHUNK_CACHE_VERSION` is 3. The kicker's chapter title was already hashed (`_kicker_chapters`), so taking `ctx.timeline` away from the templates needed no change. Frame keys are not comparable across look-engine generations: an engine-2 build stores upper-case hex where B1's run-time mixes are lower-case, so the hashed style strings differ while the pixels are equal (keys are compared only within one render).

**Disk.** JPEG frames are piped, never written. Media frames are extracted per chunk and deleted after it. `RENDER_MIN_FREE_GB` still applies.

### 7.7 Media in the stage

- **Extraction.** For each chunk, `mediaPlan(f0, f1)` lists the media frames it needs; the worker extracts them from each mezzanine with `ffmpeg -ss <exact> -i <mezzanine> -frames:v N -vf scale=W:H -q:v 3 f_%06d.jpg` (seeking before `-i` is frame-accurate when transcoding [ED §7]), at the largest size they are displayed in the chunk, never above stage size.
- **Serving** [RV E-18]. The page requests `/__frames/<chunk>/<media>/<n>.jpg`; the worker's existing request handler (`context.route`, `screenshot.py:433`) answers with `route.fulfill(path=…)` from its local workspace. No API route, no shared volume and no extra load on the API container (≈ 30 requests/s per worker otherwise).
- **Frame selection.** Source frame `floor((t − media_start + trim_in) · srcFps + 1e-6)`, then looped, frozen or clamped by `end_behavior` [MR §2.2].
- **Sources:** Veo videos, Manim videos, inserted clips (30 fps mezzanines, §8.4), Aadhi clips (24 fps; extracted once per clip, crop and size, and cached as branding derivatives). GIPHY GIFs stay live-only.
- **Images** are plain `<img>` elements; Ken Burns is a CSS transform computed from `t`. A medium shown `contain_blur` (as built in B1, `web/js/dynamic/media.js` `BACKDROP_WIDTH`) is contained in its box over a blurred, darkened copy of the same frame: the copy is drawn into a canvas 64 px wide (the box's aspect) with the canvas's own `blur(2px) brightness(0.6)`, drawn slightly larger than the canvas so its edges average picture, and the canvas is stretched over the box, whose smooth upscaling does the rest of the blur. It reads the element the medium itself shows (the pre-extracted frame's `<img>`, a still, or the live `<video>`), so `mediaPlan` lists no extra frames and no extra extraction runs. When the medium covers its box to within 1 px at each side (`backdropShown`) no backdrop is drawn and its canvas is not shown; `board_generic` sizes a frame to the medium's aspect whenever that size is known, so this is the usual case there. Every shown canvas is a composited layer, which is why a `contain_blur` clip's box carries the stage's one `will-change` (§7.4). The classic player still draws `contain_blur` as `contain`.

*As built (B1):*
- **Overlays** (`TimedOverlay`) are not drawn by the stage before B6, so `mediaPlan` never lists them and the engine fetches no overlay clip before the capture (`compose/motion/engine.py` `OVERLAYS_DRAWN = False`; `video_media_keys(timeline, *, overlays=OVERLAYS_DRAWN)`): a missing overlay asset cannot fail a render it would not appear in. B6 sets `OVERLAYS_DRAWN = True` with the stage's overlay layer, and overlay clips are then required like scene and panel clips.
- **Cleanup.** A chunk's extracted media frames are removed, and no longer served, when the chunk fails anywhere from its media plan on (a broken plan, a failed extraction or encoder start), as after a capture.
- **Live clips.** A paused clip that cannot seek when it is painted (its metadata not in yet, or a scrub's seek still in flight) is sought to the last requested time once it can (`MediaView._resync` on `loadedmetadata` and `seeked`), never while playing and never again for a time it was already sought to (a browser that clamps the seek to a shorter clip's end would otherwise loop). The `contain_blur` backdrop of a live clip is drawn again on `loadeddata` and `seeked`, with no stage repaint. A live clip plays at the player's rate (`MediaView`, and `stage.js` `syncClip` for the presenter clip). Hand-over to B2: the live presenter clip (`syncClip`) needs the same seek-when-ready treatment once `ResolvedLook.presenter` carries a live `url`.

### 7.8 WebGL policy

- `compose/capabilities.py` gains a WebGL2 probe recording `UNMASKED_RENDERER_WEBGL` (SwiftShader through ANGLE on CPU hosts) [MR §5].
- Shader presets arrive only in B7, for bookend scenes: canvas at 0.5 scale, ≤ 4 fbm octaves, ≤ 30 ms per frame.
- A failed probe uses the CSS twin and records a preflight warning, never a failure.
- `--enable-unsafe-swiftshader` raises risk only for untrusted content; the render page loads only our bundle and typed data, and the sandbox and allow-list stay.

### 7.9 Windows development and Linux Docker

- **Windows:** fully supported (headless shell + CDP capture). `HeadlessExperimental.beginFrame` is not used: on Windows its animation time did not follow `frameTimeTicks` and it was no faster [MR §2.1].
- **Linux Docker** (Debian bookworm, ffmpeg 5.1). CI's pytest job runs on ubuntu-24.04 with that distribution's ffmpeg 6.1, never inside the image, so a new Docker **`test` build target** (the runtime image plus test dependencies; the runtime image itself stays free of pytest, as the existing CI check requires) runs the container-only suites [RV E-14]: determinism, golden frames, the differential classic pixel test, and the ffmpeg feature checks on 5.1 — `setparams`, `image2pipe`/`mjpeg`, the concat demuxer with MPEG-TS chunks and `-bf 2` (exact frame count), `-movflags negative_cts_offsets` (video and audio start at 0), `loudnorm`, `ebur128`, `alimiter`, `-x264-params`, and `zscale` with libzimg for HDR clips [RV E-35]. The dev machine runs ffmpeg 8.1 [CB §11 #7].
- The Playwright version pins the headless-shell build.

### 7.10 Performance budget

| Item | Budget | Basis |
|---|---|---|
| CSS-tier frame at 1080p (evaluate + capture) | ≤ 45 ms | 36 ms measured capture-only [MR]; confirmed in B1 |
| Media-frame decode per visible clip | ≤ 8 ms per frame, budgeted separately | B1 measurement [RV E-5] |
| Shader-tier frame | ≤ 70 ms | 65 ms measured (0.5 scale) |
| 5-min 16:9 lesson in the production container | ≤ 8 min (provisional, P1) | B1 benchmark |
| 3.5-min reel | ≤ 6 min (provisional) | B1 benchmark |
| Fixed overhead (boot, fonts, MathJax, preflight, extraction, mix, mux) | ≤ 60 s | to measure |
| Memory per worker | ≤ 600 MB, plus ≈ 0.5 GB for the mix | to measure in B1 |
| 720p draft | ≈ 0.5× the final time | [MR §4.4] |

**B1 acceptance benchmark** [RV E-5]: inside the production render image, under the `motion` compose profile, through the production capture path (route interception, `/render-dynamic`, rAF wait in `seek()`, the chosen x264 preset), on four real templates: `formula_focus` (MathJax), `chart_build` (Chart.js), `media_split` with a 1080p clip, and a scene with burned captions. It records fps per worker, end-to-end time for a 5-minute fixture, and memory. If the budget is missed: try `veryfast`/`faster` at CRF 17, then 720p drafts by default for previews, then static-run deduplication (B9), and reopen Q7 with the measured numbers.

*As built (B1):* `scripts/bench_motion.py` renders the showcase lecture with a synthetic presentation through `board_generic` only (the only template B1 has), with a narration tone on every scene, a 1080p 30 fps test clip on every AI-video and simulation scene and, with `--burn-captions`, burned captions; Chart.js is not exercised (the stage draws no chart panel before B2). `formula_focus`, `chart_build` and `media_split` arrive in B2, so the benchmark is to be run again after B2. The production app image carries neither `scripts/` nor `tests/` (the showcase fixture), so the benchmark runs with both bind-mounted into the container (`docs/OPERATIONS.md` §4.2).

- **Performance regression job.** A non-blocking CI job on a fixed self-hosted runner (when the owner provides one) re-runs the benchmark per merge and posts the numbers; shared runners are too noisy for a blocking gate [RV E-14].
- **Render estimate in the Studio:** `frames / (benchmark_fps × W × 0.85)`, calibrated by a 60-frame benchmark when a worker starts.

### 7.11 Audio in the render

The video chunks carry no audio. The master track is built from the whole edited timeline (§5.12), encoded and measured **in preflight**, and muxed once. Because audio comes from the timeline and never from capture timing, picture and sound cannot drift.

### 7.12 Preflight and QA

**Principle** [RV E-13, T-3]. Today a QA failure fails the job as `render_invalid` after the whole capture (`OPERATIONS.md` §4.2). Dynamic renders check everything deterministic **before** capture, fix what can be fixed automatically, and never throw away a finished render for something the teacher could not have caused.

**Preflight** (`compose/dynamic/preflight.py`, before capture; seconds, not minutes). It loads the stage once and seeks sampled frames: 24 spread frames, every scene start, and every caption-chunk start in 9:16.

| Check | Rule | On failure |
|---|---|---|
| Duration profile | R-01; platform-safe container < 179.5 s | Fit step (§6.4); then a teacher action ("Fit to 2:58"); never a late failure |
| Safe zones | R-59, every critical box from `textBoxes()` | Force the template's safe variant; else block with "Shorten text" on that scene |
| Text overflow | §4.6 | Shrink to the slot minimum; else block with "Shorten text" |
| Contrast (analytic) | R-53 from the declared text and background colours | Force the scrim on; never blocks |
| Aadhi time | R-12 | Shorten the hold to the derived exit; else block with "Shorten Aadhi's line" |
| Frame 0 (9:16) | The hook headline box exists inside the hook band | Block with "Shorten hook" |
| Licences | R-50, R-58 | Block with "Add licence / Remove clip" |
| Music under speech | R-44 floor (20 LU) and ≤ 3 LU variation, from the stems | Lower the music gain automatically |
| Master loudness | R-48 on the encoded AAC | Re-run the master chain once with a corrected gain; then warn |
| Captions | R-32, R-33 | Re-chunk automatically; then warn |

**Post-render checks** (container level). Only these can be errors, and each has an automatic remedy; the job fails only if the remedy fails, which means an engine bug:

| Check | Remedy |
|---|---|
| Frame count equals the timeline exactly | Re-render the chunks whose counts differ |
| First video PTS = 0 and video/audio starts differ by < 1 frame | Re-mux |
| Loudness and true peak on the encoded file | Re-encode the audio and re-mux |
| Colour tags, frame grid, declared rate, caption track | Re-mux |

**Post-render warnings** (the MP4 is kept): contrast estimated from the captured pixels (a ring around each sampled box) — a critical box below threshold triggers one automatic re-render of its chunks with the scrim forced; static stretches; black edges; the optional 3-frame determinism spot check. Results are stored in `Render.options["qa"]` as today.

*As built (B1)* (`compose/dynamic/preflight.py`; the checks and the stored document: `docs/ARCHITECTURE.md` §14, `docs/API.md`):
- **Sampling** also takes every scene's last frame and every beat's settled frame; in 9:16 every phrase caption's first frame. The checks are text overflow, safe zones, analytic contrast and the R-29 type floor (warn only).
- **Captions** are checked only when the render burns them: the page draws exactly the render's caption layer (`setLayers` after every load, the second round's reload included), and the request's `burn_captions` wins over `presentation.captions_burn`. With burned captions no other critical text may reach into the caption band.
- **Frame count.** Preflight expects the frames the engine captures and the page reports: the scene-bounds total (`motion.chunks.scene_frame_bounds`, `web/js/dynamic/core.js` `stageInfo`). Every scene keeps at least one frame, so a final scene shorter than half a frame makes it one more than the rounded duration.
- **Two rounds, one item each.** Remedies are written into the served timeline's looks and the touched scenes are checked again. One item is kept per (check, scene, slot) across both rounds: the second round re-scans every frame of a touched scene and sees its other findings again, which stay the first round's items.
- **Remedy slots.** The title block reports its text in the slots `kicker` and `title` only; a chapter card's subtitle is reported in slot `title` and is remedied through it. A scrim planned for either slot scrims the whole title block (kicker, title and subtitle; a centred block's scrim stays centred), and `textBoxes()` then reports the scrim composited over the background as the text's `bg`, so the second round measures the remedy and a contrast finding it fixes ends `fixed`. A TeX word wider than the column even at the title's minimum size holds the title at that minimum, and its sideways overflow is reported at that size, so preflight blocks at the floor without a shrink round. A quiz's countdown number is reported as a non-critical label (slot `ring`).
- **Post-render remedies.** Chunks with a wrong frame count are captured again on a fresh browser that waits for a CPU slot of its own (cancel-aware), since the capture's slots were released when it ended; the audio is encoded again; the MP4 is muxed again. Loudness is not checked in B1 (the engine passes `inspect_container` no target; `qa.loudness` is null).

*As built (B2a):* remedies are written to the **fixes channel** `look.fixes` (deviation 2): "force the template's safe variant" is `fixes.safe`, a shrink `fixes.slots[slot].size`, a scrim `fixes.slots[slot].scrim`; `variant` and `text_fit` are never written, so a B2-resolved timeline never carries `variant: "safe"`. The stage reads the fixes first and B1's legacy fields second (`core.js` `fixesOf`: a slot's size is the fixes value when it is a positive number, else the legacy one; a scrim or the safe layout holds when either channel asks), so engine-1 timelines keep working, and preflight counts a scene as already safe when either channel says so. Applying the same remedies twice changes nothing, and the input is never changed. Zones and type floors come from `zones.py`; a slot minimum that is not positive falls back to the format's floor. Remedies still reach only the MP4 (deviation 24: B4 stores them in the lecture).

### 7.13 Library and licence verdicts

| Library | Version | Licence | Verdict |
|---|---|---|---|
| **In-house `web/js/motion/`** (easing, tracks, `Intl.Segmenter` text split, stroke draw, seeded RNG, count-up; ≈ 400–600 lines) | — | ours | **Adopt.** Pure functions, unit-tested, no CSP change |
| anime.js | 4.5.0 | MIT | Approved fallback only if `morphTo` or motion paths are needed [MR §6] |
| GSAP | 3.15.0 | Proprietary "no charge" licence | **Avoid**: forbids use in tools that let users build visual animations without code [MR §6, CP S52] |
| Remotion | 4.0.534 | Source-available | **Avoid**: company licence above 3 employees, and a React rewrite |
| Theatre.js | 0.7.2 | core Apache-2.0, studio **AGPL-3.0** | Avoid |
| Motion Canvas | 3.17.2 | MIT | Avoid (stale since Dec 2024) |
| HyperFrames / Revideo | 0.8.143 / 0.11.0 | Apache-2.0 / MIT | Reference architecture only |
| **Paper Shaders** | 0.0.81 | Apache-2.0 (LICENSE fetched by the review; NOTICE shipped) | **Adopt in B7**, pinned (pre-1.0) |
| webgl-noise | — | MIT | For any custom GLSL |
| lottie-web (light build) | 5.13.0 | MIT | Deferred |
| dotLottie, Rive runtime | 0.81 / 2.44 | MIT | Not in v1: need `'wasm-unsafe-eval'` |
| tsParticles | 4.4.0 | MIT | Avoid: runs its own clock |
| Chart.js, three.js, MathJax, math.js, DOMPurify | vendored | MIT / MIT / Apache-2.0 / Apache-2.0 / MPL-2.0 or Apache-2.0 | Keep, driven from `t`; DOMPurify also sanitises SVG figures for `diagram_build` |
| ESLint | — | MIT | **Not added**: the determinism rules run as a `node --test` source scan with the existing `typescript` devDependency |
| `tus-js-client` | — | MIT | Adopt in B6 (§8.4) |
| `faster-whisper` | — | MIT | Optional (clip captions, alignment) |
| `soundfile` | — | BSD wrapper, bundles LGPL libsndfile | **Not added**: WAV via the standard `wave` module (§5.11) |
| `uharfbuzz` (Python) | 0.56.2, bounded `<0.57` | Apache-2.0; bundles HarfBuzz 14.5.0 ("Old MIT") | **Adopted in B2b-H** for text fit (§4.6); Dynamic gate only |
| `fonttools` (Python) | 4.66.1 | MIT | **Adopted in B2b-H**: decodes the vendored WOFF2 faces for shaping; Dynamic gate only |
| `brotli` (Python) | 1.2.0 | MIT | **Adopted in B2b-H**: WOFF2's compression. Once installed, `httpx`, `urllib3` and `aiohttp` also import it and accept `br` responses |
| Mediabunny, ffmpeg.wasm, WebCodecs stacks | — | MPL-2.0 / — | Not needed: the server renders |

Every vendored library is pinned with SRI in `web/vendor/manifest.json` through `scripts/vendor.mjs`; `THIRD_PARTY_NOTICES` is generated and includes the Apache NOTICE files [MR §8.16]. *As built (B2b):* generated by `scripts/gen_third_party_notices.py` (`--check` in CI), with the runtime packages' licence and NOTICE texts and the third-party code inside the vendored bundles (§4.12); B1 had no notices file.

---

## 8. Editor: insert external clips and cut unwanted parts

### 8.1 Principles

1. **Edits anchor to content, never to output seconds.** Timings change whenever the voice changes, a beat is re-voiced, a scene is regenerated, the lecture is translated, or `hidden` or `min_seconds` changes. A cut stored as "12.4–15.1 s" would land on the wrong words [ED §1, §9].
2. **The screenplay is the edit decision list.** Every edit is a pure screenplay transform with an undo label, as `splitScene.js` is today, so autosave, undo, merge (`sceneMerge.js`), revert and "Generated vs edited" keep working [CB §9.3]. The B0 transform-survival test (§6.7) guards this.
3. **Edits are local.** An edit changes the scenes it touches and nothing else: looks are settled (§4.4), music re-fits deterministically (§5.10), and only the touched render chunks are re-rendered [RV T-2].
4. **Ripple by default.** The narration spine never has gaps; time is held only through `min_seconds` and `pause_after` [ED D2].
5. **Non-destructive and review-first.** Removed words and media can be restored; automatic suggestions are proposals the teacher confirms [ED §2, §3].
6. **The script is the main editing surface; the timeline is the precise one.** In B-Script (CHI 2019), inserting B-roll from the transcript was faster and easier than on a timeline [ED §3].
7. **Preview equals export.** The same compiled timeline drives the live player and the motion engine.

### 8.2 Data model

```python
class WordRange(StrictModel):                 # a narration cut
    start: int = Field(ge=0)                  # index into the beat's caption tokens
    end: int                                  # exclusive, > start
    text: str = Field(max_length=600)         # the removed words, for re-anchoring
    mode: Literal["splice", "revoice"] = "splice"

class ClipRange(StrictModel):
    asset_key: str = Field(max_length=128)    # asset kind "clip" (normalised mezzanine, §8.4); authorised (§6.7)
    in_frame: int = Field(ge=0)               # 30 fps grid of the mezzanine
    out_frame: int                            # exclusive; ≥ in + 15 (clip scene) / + 30 (overlay)

class Overlay(StrictModel):                   # B-roll shown over narration
    id: Slug
    at_word: int = Field(ge=0)                # anchor word index in the beat ...
    at_text: str = Field(max_length=120)      # ... and its text, for re-anchoring
    clip: ClipRange
    region: Literal["full", "panel"] = "full"
    sound: Literal["mute", "quiet"] = "mute"  # quiet = narration − 20 LU
    concept_id: Slug | None = None
    keep_full_length: bool = False            # see "Overlays and reveals" (§8.3)

class ClipCaption(StrictModel):
    start: float; end: float; text: str = Field(max_length=300)   # clip-relative seconds

class ClipScene(SceneBase):                   # a teacher-inserted external clip
    type: Literal["clip"]
    beats: list[Beat] = Field(default_factory=list, max_length=40)   # optional voice-over
    clip: ClipRange
    sound: Literal["keep", "mute"] = "keep"
    gain_db: float = Field(default=0.0, ge=-30.0, le=12.0)          # after loudness matching
    fit: Literal["contain_blur", "contain", "cover"] = "contain_blur"
    focus: dict[Literal["landscape", "portrait"], tuple[float, float]] = {}
    end_behavior: Literal["freeze", "loop"] = "freeze"
    captions: list[ClipCaption] = Field(default_factory=list, max_length=500)
    credit: str = Field(default="", max_length=200)                 # protected from translation (§6.7)

Beat.cuts: list[WordRange]   = Field(default_factory=list, max_length=20, exclude_if=lambda v: not v)
Beat.overlays: list[Overlay] = Field(default_factory=list, max_length=4,  exclude_if=lambda v: not v)

# timeline side
TimedScene.overlays: list[TimedOverlay]       # {id, start, end, media: MediaRef, region, sound} (exclude empty)
TimedScene.type == "clip": media = MediaRef(kind="video", trim_in, audio, volume_db, fit, focus, end_behavior)
```

**The `clip` scene type.** `"clip"` joins `Scene`, `SCENE_TYPES` and `SceneType` (inert from B0). It is **never plannable**: the `PlannedScene` validator and `plan_rules` reject it [CB §6.2]. About 22 backend and 12 frontend files branch on `ai_video` today; that list is the B6 checklist for `clip`.

**Clip scene duration** [RV E-24]. A clip scene lasts `max(clip length, voice-over narration + its pauses)`. When the voice-over is longer, the clip **freezes on its last frame** (default) or loops (`end_behavior="loop"`), and the editor says so.

**Overlay bounds** [RV E-24]. An overlay is clamped at its scene's end, because the timeline and chunk keys are per scene. The editor offers **Extend the scene to fit**, which sets `min_seconds`.

**What word indices refer to** [RV E-11]. They index the canonical caption tokens of `Beat.narration`, the tokens the captions show. TTS words can differ (numbers, units, `LexiconEntry.spoken` overrides), and `map_words` (`aadhi/pipeline/audio.py:130–160`) then interpolates timings by character position. The per-beat **token-to-word map** is therefore built where `map_words` runs and stored in the manifest, and `BeatAudio.words_exact` records whether the timings are exact (counts match and no `spoken` override). (`providers/tts/speech_words.py`, named in revision 1, is a maths-symbol table and is not involved.)

### 8.3 How edits compile

1. **Narration splice** in `pipeline/audio.build_scene_audio` on 24 kHz PCM, with no new TTS call [ED D7–D10]:
   - **Allowed** on beats with `words_exact = true`, and on interpolated beats only where a silence of ≥ 40 ms below −40 dBFS lies within ±200 ms of each cut boundary. Otherwise the editor offers `mode="revoice"` (the beat is synthesised again without the words; one cached beat of TTS).
   - **Boundary:** the quietest 10 ms window in `[prev_word.end − 0.06 s, next_word.start + 0.06 s]`.
   - **Joining pause:** `clamp(max(gap_before, gap_after), 0.12 s, 0.35 s)`; 10 ms equal-power fades on both sides.
   - **Keys:** the scene narration key gains a digest of the cuts only when cuts exist; beat TTS keys never change.
2. **Range cuts are not allowed on estimated timings** (`TimedBeat.estimated`, before TTS exists): the editor asks to build the lecture first [RV E-24].
3. **Rebuild.** Captions, kinetic captions, sync cues and visual events are rebuilt from the remaining words, so they stay consistent by construction. A board item revealed by a beat still appears at that beat's start. If a cut removes the words an on-screen item is anchored to, the editor asks: **keep the item** (it appears at the beat start) or **remove it** [RV T-19]. If a whole beat is removed, a dialog asks whether its item moves to the next beat or is removed. Deleting a beat deletes its overlays after a confirmation that says how many [ED D4].
4. **Clips and overlays.** A clip scene becomes a `TimedScene(type="clip")`; overlays become `TimedScene.overlays` from their anchor word. Both are media frames for the motion engine (§7.7) and stems for the mix (§5.12); the music plan re-fits (§5.10).
5. **Overlays and reveals** [RV T-12]. A `region="full"` overlay would hide the board while reveals anchored under it fire unseen, losing the signalling of R-17 and R-26. By default the overlay is **shortened to end 0.3 s before the next anchored reveal**, with the note *"Shortened so 2 reveals stay visible. Keep full length?"* With `keep_full_length`, the hidden reveals are deferred to the overlay's end and `overlay.hides_reveals` (warning) records it.
6. **Re-anchoring.** When a beat's narration changes, each cut and overlay is checked in order: the exact tokens at the stored index; otherwise a unique exact match of the stored text elsewhere in the beat; otherwise a cut is marked **stale** (`edit.cut_stale`) and **not applied** — it never cuts words the teacher did not choose — and an overlay falls back to the beat start (`overlay.reanchored`).
7. **Voice changes.** Word indices do not depend on the voice, so cuts re-apply to the new timings (subject to rule 1).
8. **Translation** drops narration cuts and reports the count; it keeps clip scenes and overlays and carries overlay anchors as markers (§6.11).

### 8.4 Getting clips in

| Source | Implementation | Limits and licence |
|---|---|---|
| **Upload** | Resumable **tus 1.0** (core + creation extension in FastAPI), 8 MB chunks; `tus-js-client` (MIT) vendored and configured to send the CSRF header, because cookie authentication goes through the CSRF middleware [RV E-25]. Today's in-memory upload (`upload_io.py`, 50 MB) stays for images | `CLIP_UPLOAD_MAX_MB = 1024`; ≤ 15 min; ≤ 4096 px; ≤ 120 fps; mp4, webm, mov, mkv. Rights checkbox required [ED D12] |
| **My library** | The media library picker (`openLibraryPicker({kind: 'video'})`) | — |
| **Free stock** | Server-side proxy for **Pexels** and **Pixabay** with server keys (§5.7); searches cached 24 h; within 200/h and 20k/month (Pexels) and 100 per 60 s (Pixabay) per key, backing off on 429; only the chosen file is downloaded (≤ 1080p preferred); the search is pre-filled from the selected sentence | Free licences; the API terms require showing the source ("Provided by Pexels/Pixabay") and the creator credit is auto-filled. No mass downloads [ED §4] |
| **Link to a video file** | SSRF-safe fetch in a worker job (`aadhi/providers/_safe_fetch.py`), §8.10 | Same size cap; 20 imports per user per hour |
| **Record my screen** | `getDisplayMedia` + `MediaRecorder` (`vp9,opus` → `vp8,opus` → `mp4`), 2 s timeslice, uploaded through tus | ≤ 10 min; the normaliser fixes variable frame rate and missing duration [ED D29] |
| **Record with camera** [RV T-24] | `getUserMedia` + the same `MediaRecorder` and tus path | ≤ 10 min |
| **Record my own narration** for a beat [RV T-24] | `getUserMedia` (audio) → tus → normalised to 24 kHz mono → `Beat.voice_override_asset_key` (asset kind `narration_upload`). Word timings are estimated by characters (`words_exact = false`) or aligned with the optional `faster-whisper`; the teacher's voice gets its own speaker gain (R-41) | ≤ 60 s per beat |

**Platform changes these sources need** [RV E-25]:
- `Permissions-Policy` (`aadhi/security/headers.py:24`) sets `display-capture=()`, `camera=()` and `microphone=()` today. Studio pages get `display-capture=(self)`, `camera=(self)` and `microphone=(self)`; every other page keeps them off.
- The reverse proxy must accept request bodies of at least 16 MB on the tus route (8 MB chunks plus overhead); `OPERATIONS.md` documents the nginx/Caddy setting.

**Refused.**
- **YouTube** (`youtube.com`, `youtu.be`, `m.`, `music.`) and other platform pages (Instagram, TikTok, Facebook, X, Vimeo pages): *"YouTube doesn't allow downloads. For your own video, use YouTube Studio → Content → ⋮ → Download, then upload the file."* [ED D27; YouTube Terms; API policies §III.E.1.a].
- **Mixkit** (terms forbid making items available to third parties) and **Coverr** (production use needs a paid plan) [ED §4].

**Normalisation job (`normalize_clip`)** [ED D13–D14; RV T-13, E-26]. A separate job kind keeps clip processing from starving renders; timeout 3 × duration + 60 s. It is added to `ALL_JOB_KINDS` (`aadhi/config.py:43–51`), to the general worker's `WORKER_KINDS` in `docker-compose.yml` (compose sets the list explicitly, so it would otherwise never run) and to `OPERATIONS.md`. It does not mutate a version, so the `uq_jobs_active_version` index and `VERSION_MUTATING_KINDS` are unchanged. Outputs, **in this order** so the editor can trim early:

| Order | Output | Specification |
|---|---|---|
| 1 | Probe | ffprobe on the local file after a magic-byte check; out-of-limit inputs rejected |
| 2 | **Proxy** (editor, live player) | 960×540 (540×960 vertical), CRF 28, **GOP 10** for fast exact seeks, AAC 96 kbps |
| 3 | Sprite, poster, waveform peaks | ≤ 100 tiles of 160×90, JPEG q≈70; poster at 1 s; 100 peaks/s int8 min/max computed with ffmpeg + numpy (no GPL/LGPL waveform libraries) |
| 4 | **Mezzanine** (render) | H.264 High yuv420p, BT.709 TV range, **CFR 30**, long side ≤ 1920, `-g 30`, CRF 18 `veryfast`; AAC-LC 48 kHz stereo 160 kbps; rotation applied; HLG/PQ tone-mapped (zscale → `tonemap=hable:desat=0` → BT.709) **only** when `color_transfer` is `arib-std-b67` or `smpte2084` |
| — | `Asset.meta` | Duration, fps, size, `has_audio`, integrated LUFS, true peak, LRA, speech ratio, licence fields (§6.7) |

`GC_ASSET_KINDS` gains `clip`, `clip_proxy`, `clip_sprite`, `waveform` and `narration_upload` in B6.

### 8.5 Cutting and correcting

- **Edit sentence** [RV T-19]. The most common need is to *change* what is said (a wrong fact, a mispronunciation). The teacher retypes the sentence; that beat is re-voiced (cached, cost shown on paid voices) and its captions, events and anchors rebuild. A pronunciation fix can instead be saved as a lexicon respelling for the whole lesson.
- **Cut in the Script view.** Select words and press Delete for a ripple cut. Double-click selects a sentence, the default unit because it splices most naturally. A cut inside a sentence shows *"May sound abrupt. Re-voice instead?"* [ED D11]. **Show removed** displays struck-through words with Restore.
- **Range cut on the timeline** (I/O marks, then Delete), resolved into anchored operations in order: hide whole scenes; remove whole beats; cut word ranges; shorten `pause_after`; trim or split clip scenes; trim overlays. A one-line summary (*"Removes: 2 sentences in scene 3 · 4.2 s of 'Lab demo' · a 1.1 s pause"*); one undo step; ranges over 10 s show a preview list first [ED §9]. Not available on estimated timings (§8.3).
- **Clip trimming.** Handles snap to the 30 fps grid; minimum 15 frames for a clip scene and 30 for an overlay; **S** splits a clip scene at the playhead (`splitScene.js` gains a time-based split for clips).
- **Review-first suggestions** [ED D34; LS J2]: tighten pauses (gaps over 0.8 s → 0.35 s); clip silences (−35 dB for ≥ 0.6 s, sensitivity slider); static stretches beyond R-15; dead air over 1.5 s outside planned holds. Each can be toggled and adjusted; **Apply** is one undo step.

### 8.6 Audio of inserted clips

| "How it plays" | Picture | Clip sound (default) | Narration | Music |
|---|---|---|---|---|
| **Pause the narration and play the clip** (`ClipScene`, `sound="keep"`) | Full frame (`fit`) | **Kept**, normalised to −16 LUFS (gain clamped to [−30, +12] dB) then `gain_db`; voice-over beats duck it to −20 LU | Silent, or voice-over | Fades out over 0.5 s before the clip and back over 1.0 s after |
| **Show it over the narration** (`Overlay`) | Full frame or panel region | **Muted**; "Quiet" = narration − 20 LU (WCAG 1.4.7) | Continues | Normal ducking |
| **Replace this scene's picture** (the existing override pick, folded into the same dialog) [RV T-13] | Panel or main media | Muted | Continues | Normal |

Every clip in and out point gets a 20 ms audio fade; joins use per-side `afade`, not `acrossfade`, which would shorten the result [ED §6]. Preflight measures clip-versus-narration levels.

### 8.7 Captions after edits

- **Narration captions** (phrase and kinetic) are rebuilt from the remaining words after any edit, so they cannot drift.
- **Clip captions** come from an imported `.srt`/`.vtt` mapped through the trim (cues shorter than 0.3 s after trimming are dropped), or from **Transcribe** (the optional `faster-whisper`, CPU int8), reviewed by the teacher before use. A clip that keeps speech without captions raises `clip.sound_no_captions` (info) [ED D18].
- Clip captions flow into the burned captions, the phrase captions and the sidecars.

### 8.8 UX and shortcuts

- **Script tab** (default editor view for Dynamic lectures). The whole lecture as running text, grouped by scene and beat; the current word highlights during playback; clicking a word seeks to it. The selection toolbar offers **Edit sentence · Cut · Insert a clip after this sentence · Show a clip over this · Re-voice**.
- **Where an insert lands** [RV T-12]. A clip scene can only sit between scenes, and `splitScene.js` splits only at beat boundaries, so "Insert a clip" **snaps to the end of the sentence** (beat) and says so: *"The clip plays after '…resistance.'"* Inside scenes that cannot be split (check, chapter card, simulation) it snaps to the scene end. When a split is needed, part B gets `look.continues_from = <A>`: it starts from A's final visual state with the same template (exempt from R-20), so a half-built list or flow never restarts without its first items.
- **Runtime meter** [RV T-3]. The editor header shows generated runtime against the format target and cap, plus inserted time; for platform-safe reels it shows the container limit and offers "Fit to 2:58".
- **Timeline strip** (`timelineStrip.js`, extending batch 4's lanes) [ED D30–D33; RV T-18]. **Narration** (waveform from the existing loudness envelope, estimated beats hatched) and **Clips and overlays** (filmstrip, trim handles) are shown by default; **Look**, **Music** (cue blocks and duck envelope), **Captions** and **Chapters** are collapsed. I/O marks, playhead, zoom; snapping within 8 px to word gaps, beat and scene edges, the playhead, marks and clip edges.
- **Insert-clip dialog** [RV T-13]: **1. Source → 2. How it plays → Insert.** Trimming does not wait for processing: while a local file uploads, the dialog trims it in the browser from a blob URL (H.264, VP9, WebM; HEVC waits for the proxy, which the normaliser produces first). After Insert, an **Adjust** panel (trim, sound, fit with a focus point for `cover`) previews in place. Uploads and links need the rights checkbox; stock credits fill themselves.
- **Look inspector** [RV T-18]. By default: layout thumbnails of the templates in the scene's class and **Shuffle this scene**. Under **More**: variant, background, accent slot, incoming transition. **Reset to automatic** sets the lock back to `auto`.
- **Lecture-level look actions:** **New colours** (re-roll family, accents and background preset; bumps `look_epoch`; pictures keep their stored style) and **New layouts** (re-pick settled templates within their classes; bumps `layout_epoch`). Both are undoable, keep pins, and re-render only the chunks they change. **Restyle pictures** regenerates AI images and Manim clips in the new family, after a cost confirmation.
- **Voice and music panel.** Narrator persona with **Hear your terms** (§5.3) and the cost of re-voicing (a job); music source, mood and track with a preview; "New music" with its cost; music level and mode.

**Shortcuts.** None fires while focus is in an `input`, `textarea`, `select`, `button`, `[contenteditable]`, checkbox or radio, an open dialog, or native media controls (generalising the batch-4 Space fix) [CB §11 #12].

| Key | Action |
|---|---|
| Space / K | Play-pause / pause |
| L | Play; repeat for 1.5× and 2× |
| J | Back 5 s; repeat for 10 s and 20 s |
| I / O | Set the in / out mark |
| Alt+X | Clear marks |
| Shift+I / Shift+O | Go to the in / out mark |
| ← / → | Previous / next word, or ±1 frame when a clip is selected |
| Shift+← / Shift+→ | ±1 s |
| S | Split the selected clip (or scene) at the playhead |
| Delete | Ripple-delete the selection or the marked range |
| Ctrl+Z / Ctrl+Shift+Z | Undo / redo |
| Ctrl+= / Ctrl+− / Ctrl+0 | Zoom in / out / fit |
| N | Toggle snapping |
| Alt (held) | Suspend snapping |
| ? | Help |

### 8.9 Preview and export parity

- The editor preview uses the preview timeline, with estimated timings hatched until TTS exists.
- The editor and live player play **proxies**; the motion engine renders from **mezzanines**; both read the same `TimedScene` fields through the same stage code.
- `evals/render_parity.py` gains clip, overlay and cut cases.

### 8.10 Security and rights

- **SSRF-safe link import** [ED D26; OWASP]: HTTPS on port 443 only; redirects followed manually (≤ 3), each re-resolved and re-validated; the name resolved **once** and the connection made **to that pinned IP** with SNI and Host preserved (avoiding the 2026 rebinding mistake, CVE-2026-55391); any address that is not `is_global` rejected, including IPv4 embedded in IPv4-mapped, 6to4 and NAT64 addresses, and CGNAT 100.64/10; timeouts 10 s to connect and 300 s in total; only `video/*` or `application/octet-stream`, then a magic-byte check and ffprobe; in production, egress through a proxy that allows only port 443. `providers/_http.fetch_limited` remains for allow-listed provider hosts.
- **Local processing.** ffmpeg and ffprobe always run with `-protocol_whitelist file,pipe`.
- **Access** [RV E-2]. Clip, overlay, music and narration keys are authorised per owner through `asset_uses` and `AssetRef` (§6.7); revision 1's "as today" was wrong, because `asset_uses` did not list these fields.
- **Rights and credits.** Uploads, recordings and links require a rights confirmation; URL imports require a credit; stock credits fill automatically. Credits appear in `credits.txt`, the description draft and the share page.
- **Storage.** A mezzanine plus proxy is ≈ 1.3× the source. Unreferenced clips are garbage-collected after `CLIP_GC_DAYS` (30) [ED §11].
- **Tests.** The SSRF suite covers DNS rebinding, redirect chains, private, link-local, CGNAT and IPv6-mapped targets, oversized bodies and wrong content types; a tus upload without the CSRF header is refused.

---

## 9. Studio UX

### 9.1 Navigation

- **Two tabs on the "New lecture" page** (`views/newProject.js`, which already uses `createTabs`) [CB §9.1]:
  - **Dynamic video** — default, shown first;
  - **Aadhi presenter** — the current form, unchanged, with one line: *"Classic style: Aadhi presents throughout."*
- The last chosen tab is remembered per user in this browser (`aadhi.studio.pref.*`).
- Projects and versions show a style chip (*Dynamic* / *Presenter*), a format chip (*16:9* / *9:16*) and, in a series, *Part n of N*.

### 9.2 The Dynamic video form

Built from `optionsFields.js` and `lib/optionsForm.js`, with exactly these fields [RV T-5]:

1. **Source.** Upload or Paste, as today.
2. **Format.** Two large cards:
   - **Lesson · 16:9 · about 5 min**, with the checkbox **Also make a reel**;
   - **Reel · 9:16 · 3–4 min**, with **Platform-safe (≤ 2:58)** and the note *"Over 3 minutes, YouTube publishes vertical videos as regular videos, not Shorts. You can also export a reel as 2 parts."*
3. **Lecture details.** **Course** (a picker of the teacher's existing courses plus "New course", §5.3), unit, session number, session title (optional: taken from the document).
4. **Language and audience.** Narration language, board language, audience, and **Depth** (Overview / Standard / Deep, which moves the word budget inside the format band). No length or quiz-frequency fields.
5. **Where will students watch?** WhatsApp, Drive or classroom · LMS · YouTube · Our lesson page (§2.6). The default comes from Settings (Q10).
6. **Style**, collapsed to a summary such as *"Auto — a fresh look, voice and music every lesson"*, with **Change**:

   | Control | Options |
   |---|---|
   | **Look** | **Auto (rotates)**, a theme family (swatches) or a personality, **Surprise me**; Light / Dark / Auto; Energy: Calm / Balanced / Lively |
   | **Voice** | **Auto (rotates within the accent you choose)**; an accent filter (default: Indian English mostly, §5.3); a persona list with ▶ sample and *Free* or *≈ $0.07 per lesson*; **Keep this voice for the course**; **Premium voices** with cost. Small pools are stated plainly |
   | **Music** | **Auto**, a mood, **My track** (upload) or **None**; **Use AI music** (opt-in, *≈ $0.16–0.32 per lesson*) unless the org setting already allows it; ▶ preview; a note on the source (*Built-in soft bed* / *AI-generated* / *Library*) |
   | **Aadhi** | *"Aadhi introduces the session (5–8 s)."* Toggle **Aadhi in the recap** (default on for 16:9, off for 9:16) |
   | **Captions** | Copy follows the destination: *"Captions: burned in"* or *"Captions: a track viewers switch on, plus a file to upload"*; a switch to change it |
   | **Sound effects** | On / Off |
   | **Reel energy** (reels only) | **Learning** (default) / **Social energy** (*"faster and louder; not yet tested for learning"*) |

   **Use as my default** saves the Style choices and the destination for this teacher (§9.8).
7. **AI engine and keys.** The existing Gemini / OpenAI / Claude choice with personal and server keys.
8. **Advanced.** Picture provider (as today); "Allow custom animations" (`allow_freeform_manim`); GIFs and interactive sketches labelled *"web lesson page only"*.
9. **Let me review the plan first** (`review_plan`).
10. **Extra instructions.**
11. **Estimate.** The LLM estimate plus TTS, music and images, plus the render-time estimate (§7.10); all costs from `aadhi/usage/pricing.py`. For a series, the total for all parts.

A frontend test asserts that the Dynamic form never sends `target_minutes`, `tts_rate`, `include_quizzes` or `quiz_every_n_concepts`.

### 9.3 Scope and series step

When the scope check pauses a generation (§6.4), the project page shows, in plain words [RV L-1, T-6]:

> *Your document has about 14 minutes of teaching in it. A 5-minute lesson can hold about a third of that.*
> **Make a series of 3 lessons** (recommended) · **Focus on some topics** · **Make one shorter lesson anyway**

- **Series** lists each part's concepts and lets the teacher move a concept between neighbouring parts; **Generate all parts now** or **Part 1 now** (the others get a Generate button), with the total estimate.
- **Focus** is a checklist of the brief's concepts with their estimated seconds and a running total against the budget.
- **One shorter lesson** shows what will be left out before it continues.
- Every lesson's source report lists each concept as taught, in another part, left out by the teacher or left out to fit (§2.2).

### 9.4 Plan review in Dynamic mode

`review_plan` already pauses after planning (`base.py:102`; `planReview.js` edits scene types, order and estimates). In Dynamic mode [RV T-14]:
- **Bookends are locked**, each with a one-line reason: the Aadhi intro (*"Aadhi introduces every lesson"*), the recap (*"Students remember more when they recall at the end"*) and the outro. The recap's Aadhi appearance can be switched off.
- The plan is shown as its **segment structure** (chapters or acts, with checks, predictions and the worked example marked) with a **runtime meter** against the format band.
- **Approve** runs `plan_rules`; failures are listed as plain-language fixes with one-click actions (*"Chapter 2 has no check. Add one?"*).
- The cast **voice** (with **Hear your terms**), the **music** and the **look** are shown with **Change** links, because casting runs before planning.

### 9.5 Project page, render and exports

- **Actions** (`projectDetail.js`): **Make a reel from this lecture** (§6.10); **Remake as Dynamic video** for classic lectures; **Generate again**, which keeps the look, voice and music unless **Fresh look** is ticked (§5.3).
- **Render dialog:** destination (pre-filled), captions (burned or clean), **Also export the other version**, loudness (−14 / −16 LUFS, pre-filled by destination), draft or final, sound effects on/off, Calm motion (W2), **Export as 2 parts** (reels, §2.7), and the preflight results with their one-click fixes before anything is captured (§7.12).
  *As built (B1):* the editor's "Render MP4" and the project page's "Make the video" tell a Dynamic render from a classic one only by the server's answer, `GET /api/versions/{vid}/render/preflight` `presentation` (null for a classic render, including a lecture with a presentation while `DYNAMIC_ENABLED` is off; `web/js/studio/lib/renderOptions.js` `dynamicRender(preflight)`). Without that answer (the request failed, or the editor did not check a stale lecture) the classic dialog is shown. The Dynamic dialog pre-checks "Burn captions" from `captions_burn`, always sends `burn_captions`, has no intro option and, in the editor, sends `soft_subtitles` explicitly when the captions are not burned (pre-checked: a clean file's default-on track, R-31). The other options of this dialog are not built yet.
- **Downloads:** the MP4(s), `.srt`, `.vtt`, the chapters and description draft, the thumbnail or cover, `credits.txt`, and a short **"Which file goes where"** note [RV L-9]: YouTube → the clean MP4 + `.srt`; LMS → the burned MP4 (+ `.vtt` if the LMS takes captions); WhatsApp, Drive or a projector → the burned MP4.

### 9.6 Previews

- **Look preview strip** [RV E-33, T-11]. Four frames (intro, definition, list, bumper) drawn in the browser by the real stage code from a built-in mini-timeline. The candidates come from `GET /api/dynamic/look-candidates?format=&mode=`, which applies the teacher's history exclusions and the brand kit, so the strip never shows a family that generation would skip. **Surprise me** draws locally from that set. When the teacher keeps a strip, the form sends its resolved family, personality, display font and caption style in `LookPrefs.resolved`, and generation uses exactly those (they are not "pins for the course" unless the teacher ticks that).
- **Voice sample** [RV E-32]. A public `voice_sample` asset per persona (a fixed 12 s text, synthesised once and cached; free on Edge, a usage row on paid providers), played with ▶. Calibration clips stay private. **Hear your terms** appears in plan review and the editor (§5.3).
- **Music preview.** The procedural engine renders a 15 s preview of a mood in ≈ 1 s. AI music cannot be previewed before generation, which the UI states; after generation the editor's music lane plays the real track.
- **Safe-zone overlay (9:16).** A toggle in the editor and watch previews showing the critical box, the caption band and a **generic** UI silhouette (no platform logos).

### 9.7 Live player (watch page)

- **Captions** on by default in both formats.
- **Music: Off / Low / Normal** (§5.12).
- **Checkpoints** (`predict`, `check`) pause for an answer, with **Show answer**; the MP4 uses the timed think hold [LS G3].
- **Reduced motion** is honoured (§4.8).
- **Analytics events**, added to the existing per-scene drop-off data: checkpoint answered or skipped and whether correct; prediction made; music level changed; captions toggled; seek-backs per scene. They feed E1–E4 and L1 (§1.3).

*As built (B1)* (`web/js/player/player.js`, `controls.js`, `web/css/player.css` §12–13, `web/css/studio.css`):
- **The box takes the stage's shape.** The player sizes a Dynamic stage from the timeline and marks its root (`data-stage-shape` plus `--ap-stage-w` / `--ap-stage-h`) while the stage is not 1920×1080, so the watch page's `.watch-player` and the Studio preview's `.preview-box` take a 9:16 reel's shape instead of a pillar in a 16:9 box; classic and 16:9 boxes are unchanged. The reel's watch box height is measured on the small viewport (`100svh`, the browser's toolbars shown), with `100vh` as the fallback; the Studio's box is at most 60% of the window tall.
- **The control bar fits the box.** A reel's box is the `ap-shaped-box` size container, so its bar follows the box's width, not the viewport's: up to 720 px the phone bar; at 410 px and below no mute; at 370 px no time (the seek bar shows the progress); at 260 px no scene list; at 220 px no speed. Play/pause, the seek bar, captions and fullscreen always stay. Below 136 px (a phone held sideways) the bar wraps onto a second line, and anything that still does not fit wraps instead of being clipped; fullscreen shows the full bar. Typical boxes: 518 px wide at 1920×1080 (the full phone bar), 342 px at 1366×768 and 259 px in the Studio preview (parts dropped). `tests/web/test_player_controls_fit.py` checks this in Chromium. Known classic limits, unchanged: the classic Studio preview (16:9, about 431 px wide) clips Captions, Caption size, Speed, Scenes and Fullscreen, and the classic watch page on 360–375 px phones clips Fullscreen.
- **No caption-size choice for Dynamic videos.** Their captions are part of the stage, drawn at their format's size in its band (§4.11, R-34) exactly as in the MP4, so the size button is disabled and says "Caption size: set by this video"; the viewer's size preference is kept for their classic lectures. The lift that keeps captions above the shown control bar is measured from the format's caption band (`Player.captionBottom`: 48 stage px above the bottom in 16:9, 680 in 9:16, which a bar rarely reaches).
- **Less work for a Dynamic lecture.** It preloads no quiz tick or ding, computes the classic scene state only to duck the branding music, paints nothing in a hidden tab except at scene changes, and reports a stage that fails to paint once per scene and message (at most 20 per timeline, `MAX_STAGE_FAILURES`).

### 9.8 Teacher defaults and the brand kit

- **Teacher defaults** [RV T-23]. **Use as my default** stores the teacher's Style choices and destination server-side, so they follow the teacher across devices: a `users.preferences` JSON column (Alembic migration, B4).
- **Site settings** (B4): a `site_settings` key-value table (migration) holds the default destination (Q10), the Aadhi brand voice (Q4) and whether AI music is allowed by default (Q2).
- **Brand kit** (B7; Settings → Brand, administrators): institution logo for the intro corner and outro, an optional name strip on the outro, and allowed or excluded theme families. Every competitor offers one [CP §4].

---

## 10. Phased implementation plan

### 10.1 Release shape and batch map

The plan is a this-week hotfix, then a **thin first release (R1) that covers both formats**, then the editor, then the variety expansion [RV E-29].

```
H0 Gemini TTS hotfix (this week; main and overhaul/v2)
B0 foundations ─┬─► B1 motion engine ─► B2 visual system (R1 set) ─┐
                └─► B3 audio system (R1 set) ───────────────────────┴─► B4 FIRST RELEASE: lesson + reel
B4 ─► B5 editor I (script edits, cuts, look) ─► B6 editor II (clips and recordings)
B4 ─► B7 variety and distribution expansion   (parallel with B5–B6 given a second developer)
B2 ─► B8 Aadhi asset upgrade                   (asset production can start at any time)
B4 ─► B9 measurement and tuning                (continuous)
```

**Estimates** are for one full-time developer working with AI assistance, in weeks, as ranges; they include tests and docs but not review waiting time.

| Batch | Estimate | Cumulative (one developer) |
|---|---|---|
| H0 | 0.5–1 day | — |
| B0 | 2–3 | 2–3 |
| B1 | 3–4 | 5–7 |
| B2 | 4–5 | 9–12 |
| B3 | 3–4 (parallel with B1–B2 given a second developer) | 12–16 |
| B4 | 4–6 | **16–22 → R1** (≈ 11–14 with two developers) |
| B5 | 3–4 | 19–26 |
| B6 | 4–5 | 23–31 |
| B7 | 6–8 (splittable) | 29–39 |
| B8 | 1–2 + external asset production | — |
| B9 | 2–3 + a 10–12-week pilot | — |

### 10.2 Gates for every batch

1. The full Python suite, the frontend suite, the typecheck, the linter and the four consistency checks pass.
2. From B0, the **Docker `test` job** passes: the classic invariance harness (goldens + differential pixel test, §3.6), the ffmpeg 5.1 feature checks, and from B1 the determinism suite.
3. The transform-survival test (§6.7) passes, and every new lint, transform and endpoint has tests.
4. Fakes keep every test offline [ARCHITECTURE §1.9]; a batch that adds a provider adds its fake.
5. `ARCHITECTURE.md`, `INTERFACES.md`, `API.md`, `PROMPTS.md` and `OPERATIONS.md` are updated for the batch's contracts.
6. A review pass is completed before merging; its findings are fixed or recorded with a reason.

### 10.3 Batch details

#### H0. Gemini TTS hotfix (this week, standalone)

`gemini-2.5-flash-preview-tts` shuts down on **17 Nov 2026** (Google's deprecations page; prices of the replacements double on 1 Jan 2027). `main` is the default and deployed branch, and v2 is unmerged, so bundling the fix into B0 on v2 would miss the deadline [RV E-1].
- **Scope.** On `main`: `server.py:758` → `gemini-3.8-flash-tts`, and `test_gemini.py`. On `overhaul/v2`: `aadhi/config.py:180` (`gemini_tts_model`) → `gemini-3.8-flash-tts`; `requirements.txt` floor `google-genai>=2.26` (Gemini 3.8 TTS needs 2.25, but Gemini Omni's Interactions API, `client.aio.interactions` and `google.genai._gaos`, was verified against 2.26, and the floors in `requirements.txt` are the verified versions; the lock pins 2.26.0); the pricing row with the 2027 step. A missing-model error falls back to Edge with a visible warning; this covers a retired OpenAI speech model too (`OPENAI_TTS_MODEL`: every `/v1/audio/speech` model retires on 2027-01-06).
- **Acceptance.** Tests pass on both branches with recorded fixtures; the owner runs a real-key smoke test on the deployed instance; deployed well before 17 Nov.
- **Estimate.** 0.5–1 day. The adapter's style and pause-tag improvements wait for B3.
- **Status (2026-10-11).** Done on `overhaul/v2`: the `gemini-3.8-flash-tts` default, the pricing row, the `google-genai>=2.26` floor and the Edge fallback with a warning. **Open on `main`**, the deployed branch, which still calls `gemini-2.5-flash-preview-tts` (`server.py:758`) and stops narrating when it shuts down on 17 Nov 2026: the `main` hotfix is the owner's decision.
- **As built (v2).** `ModelUnavailable` (`aadhi/providers/base.py`, a `ProviderUnavailable`) is raised by both speech adapters for HTTP 404 / 410 on a request naming the configured model: `GeminiTTS` (`GEMINI_TTS_MODEL` shut down, renamed or misspelt) and `OpenAITTS` (`OPENAI_TTS_MODEL` retired, renamed or misspelt, or a model the API key has no access to, which OpenAI answers with the same 404 `model_not_found`). It comes after that one request, never retried, with nothing generated or billed. On it, `aadhi/pipeline/assets.py` switches the build once to the free Edge voice for the lecture's language (`fallback_voice_config`: `TTS_VOICE` when it is an Edge voice (`voices.voice_fits`) and the lecture speaks `DEFAULT_LANGUAGE`, else Edge's default voice for the language; same rate) and adds one lecture-level `assets.tts_fallback` warning ("The voice model '<model>' of the voice provider '<provider>' is not available (it was retired or renamed, or the API key has no access to it); the free Edge voice '<voice>' was used instead.") and a job-log warning that names the model and the switch first. The scene that met the error is narrated again wholly in Edge (every scene has one voice), and the fallback clips are keyed exactly like an Edge lecture's (`_Builder.clip_key(text, voice)`), so nothing is stored under the failed voice's keys. The next build tries the lecture's voice again: the scenes the fallback narrated are rebuilt and their Edge clips come from the cache. Without Edge on the server (`ProviderNotConfigured`) the scenes still fail with `assets.tts_failed`.

#### B0. Foundations and safety (no visible change)

**Scope**
1. **Inert schema** for every field and enum of §6.7 and §8.2, with `DYNAMIC_FEATURES` write gates; JSON schemas and JSDoc types regenerated [RV E-3].
2. **Reader-version marker** (`schema_version = 3` for v3 documents) and the `newer_document` refusal in the API, workers and import; the deploy order and the "B0 is the rollback floor" rule in `OPERATIONS.md`.
3. **Asset authorisation:** `asset_uses` for clip, overlay, music and narration keys; an `AssetRef` helper for uploads and generated assets [RV E-2].
4. **Transform-survival property test** (Python and node) over every transform listed in §6.7.
5. **`convertSceneShape`** keeps `hidden`, `min_seconds` and `look` through one shared common-keys list (`screenplayEdit.js:83–86, 301–304`); `repair.carry_over` keeps `look`; `look` and `overlays` join the `scene_hash` exclude set.
6. **Format plumbing:** `compose/base.stage_size(format)` and `output_size(tl, settings)`; viewport and render page sized from the timeline (classic stays 1920×1080); a `width` parameter for `captions.split_caption`; aspect-aware QA edge sampling.
7. **Invariance harness and CI:** platform-independent goldens and the differential pixel test (§3.6); a Docker `test` build target and a blocking CI job that runs the container suites [RV E-14, E-15].
8. **ffmpeg 5.1 feature checks** in the test image (§7.9), including `negative_cts_offsets` and MPEG-TS concatenation with `-bf 2`.
9. **`compose/loudness.py`:** `ebur128` and `loudnorm` JSON helpers.
10. **Pricing:** unknown operations fail closed; the `music` operation; `_media_billed_to_server` covers music, premium TTS and calibration; the ElevenLabs row re-read against the current price page [RV E-9].
11. **Batch-4 regression tests**: one test for **each** item of "Batch 4: review fixes" in `docs/INTEGRATION_ANDRYAN.md` (hidden-scene issues served as notes and skipped by pacing checks; `POST /build` on hidden scenes; `scene_number` in preflight and logs; the `quality.COMPUTE` sentinel; revert restoring the chapter listing; autosave holding on a clashing job result; adoption as a labelled undo step; the job lock; Space leaving form controls alone; edits during a revert; "Save as a copy"; failed autosave retry; pending inspector edits before the lock; the keyboard slider; the Videos page not redrawing on presigned-link changes and keeping videos past 100; the preview's fresh-link retry; the failed-lesson workflow strip; the source-report formula and digit-run rules). Any that fail are fixed in B0, before B5 builds on them [RV E-29, T-28]. As built: `tests/api/test_batch4_regressions.py`, `tests/pipeline/test_batch4_regressions.py` and `web/tests/studio/batch4Regressions.test.js`; two gaps were fixed (the failed-lesson workflow strip with unsaved edits, and edits typed during "Save as a copy").
12. Copy the research reports to `docs/research/` if Q12 is yes (done: Q12 was yes).

**Contracts frozen:** the v3 schema fields and enums; `DYNAMIC_FEATURES` names; the `newer_document` error; the `asset_uses` table of §6.7; `stage_size`/`output_size`.

**Tests:** schema round-trips with defaults excluded; `feature_unavailable` on gated writes; a document with `schema_version` 4 is refused; foreign clip and music keys rejected by `PUT` (422) and stripped by import; transform survival; `convertSceneShape`; goldens and the differential pixel test; ffmpeg features on 5.1; pricing fail-closed; the batch-4 list (the three files of item 11).

**Acceptance:** all gates; classic goldens unchanged and pixels within the measured tolerances (§3.6); every batch-4 fix has a passing test.

**Estimate:** 2–3 weeks.

**As built (B0 integration, 2026-10-10).** Deviations and additions, each recorded where it applies:
- Feature names: `voice_override` joined the frozen `DYNAMIC_FEATURES` list (the teacher's recorded narration, B6).
- `MediaRef.end_behavior` keeps the `"loop"` default (§6.7, timeline); `FigurePin` is `{id, x, y, label}`.
- No migration: the `asset_uses` table is code (`aadhi/assets_auth.FIELD_RULES`), `asset_refs` already exists, and
  `models.PUBLIC_ASSET_KINDS` gained the eight new kinds (a string column). Gemini Omni clips are keyed by
  `OMNI_MODEL`, every other provider's clips by `VEO_MODEL` as before.
- The differential pixel test uses measured tolerances, not "any difference fails" (§3.6); `+negative_cts_offsets`
  still writes an `elst` per track (§2.8, §7.5), so its feature check probes the raw timestamps with
  `-ignore_editlist 1` next to a control mux without the flag (2 frames late on ffmpeg 8.1).
- The goldens also pin the classic render's command lines with burned-in captions and with the soft `mov_text` track
  (`segments_burn.json`, `segments_soft.json`; identical from the pre-B0 code); CI runs a second differential with
  `--burn-captions` when the caption path changed. CI reads the `classic-change` label at run time (re-runs and the
  push after a merge see it; a direct push declares the change with a `Classic-Change: approved` commit trailer).
- Burned-in captions size from the frame's short side, so a portrait render gets the classic glyph size (libass
  already scales by the frame height); landscape sizes are unchanged.
- Rollback-floor fixes: plans rebuilt from a screenplay (repair, regenerate) leave clip scenes out and give v3 roles
  as their classic counterparts until B4 widens `PlannedScene`; `repair.carry_over` keeps the scene's own v3 role and
  `visual_intent`; regenerating a clip scene is 409 `action_unavailable`. `PUT /screenplay`, scene revert and the Visual Review
  writes refuse a newer stored asset manifest before writing. The asset-key lookup batches its keys (SQLite's bind limit).
- The CI job `container` selects the ffmpeg checks by the `container` marker; the test image sets
  `AADHI_EXPECT_FFMPEG=5.1` (the version pin) and `AADHI_CONTAINER_TESTS=1`. "Container suites (Docker test image)"
  must be made a required check in the repository's branch protection (an owner setting). Since the B1 completion
  pass, `AADHI_CONTAINER_TESTS=1` also makes the B1 suites' tool and bundle checks fail instead of skip
  (`tests/compose/dynamic/helpers.missing`: ffmpeg / ffprobe, Playwright, its Chromium, the stage bundle; also the
  live-app fixture of `tests/api/test_render_dynamic_route.py` and the tools fixture of
  `tests/invariance/test_render_slow.py`), so the blocking job cannot pass green by skipping them.
- Added outside the plan: the Gemini Omni video provider (`aadhi/providers/video/omni.py`, `VIDEO_PROVIDERS`,
  `OMNI_MODEL`). Google lists the Veo 3.1 previews for shutdown from 2026-10-22 with `gemini-omni-1.1-flash` as the
  replacement; a chain that has `veo` gets `omni` as its last backup unless `VIDEO_PROVIDERS` sets the order. That
  automatic successor is used only while Veo is unavailable (a model no longer served, an account refusal): a Veo
  timeout, rate limit or bad output keeps the still image, as before, because the accepted Veo job may still bill;
  naming `omni` (`VIDEO_FALLBACK_PROVIDERS=omni`, `VIDEO_PROVIDERS=veo,omni`) opts in to it after those failures
  too. The adapter keeps one client per job event loop, closed by the job's last clip.
- `build_timeline` sizes the stage from `presentation.format`; translation sends `BoardItem.unit`, `BoardItem.date`
  and `ClipCaption.text` with the on-screen text.

**Open after B0** (carried into the named batches):
- Owner decision: whether a scene revert, which puts the scene back exactly as Aadhi wrote it, should keep the
  teacher's `look` the way it drops `hidden` and `min_seconds` today (by design so far). (The automatic Omni backup
  now follows only an unavailable Veo, see above.)
- B4: widen `PlannedScene.narrative_role` to the v3 roles (§6.5); until then a rebuilt plan maps them.
- Assets: an Omni or Veo create that got no answer within the live job still ends `failed` in the job's operation
  record (only a crash leaves it `submitting`); consider keeping such a record ambiguous (`_submit_video`).
- The CI job `container` has not run yet (Docker Desktop is installed on the development machine, but its C: drive
  has about 7 GB free, too little for the production image): its first run may surface ffmpeg 5.1 differences; the
  branch-protection setting is the owner's.
- B1/B6 player: done in B1 for the Dynamic stage. The portrait `Player` sizes its stage from the timeline and the
  watch and Studio preview boxes take its shape (§9.7); `fit="contain_blur"` (§7.7) and `end_behavior="clamp"` are
  drawn by the Dynamic stage. The classic player and renderer have neither (the classic player draws `contain_blur`
  as `contain`); B6's inserted clips need an anchor for `contain_blur` (§2.5).
- B3/B6: call `record_asset_refs` for generated music, `normalize_clip` mezzanines, tus narration uploads and stock or
  link imports, with kinds `music` / `clip` / `narration_upload`; add each kind to `GC_ASSET_KINDS` with its batch.
- B5 editor: keep `Beat.answers` and the hook valid in `sanitizeScreenplayRefs`, `deleteBoardItem`, `removeBeat`,
  `deleteScene` and `moveScene`; the new item kinds in `BOARD_ITEM_KINDS` / `TEXT_REQUIRED_KINDS` (and `clip` in
  `SCENE_TYPES` with B6); mark cuts and overlays stale when narration changes (§8.3.6).
- B6: the time-based split of a clip scene with `look.continues_from`; decide whether scene regeneration re-anchors
  or warns about the `cuts`, `overlays` and `voice_override_asset_key` it drops with the rewritten beats.
- B3/B4: `GenerationOptions.voice` (`premium_allowed`, `any_provider`, `narrator` as `"provider:voice"`) and
  `.music` (`source`, `ai_opt_in`) are the names the budget pre-check already reads.

#### B1. Motion engine core

**Scope**
- `web/js/motion/` (easing and cubic-bezier solver, tracks, OKLCH interpolation, `Intl.Segmenter` splitting, stroke drawing, mulberry32, count-up).
- `web/js/dynamic/stage.js`, `render.js`, `guard.js`, `web/render-dynamic.html` and the `/render-dynamic` route (§7.3); the source-scan test (§7.4).
- `aadhi/compose/motion/`: worker pool with the cgroup-aware W, host-wide slots (`RENDER_SLOT_DIR`), CDP capture, chunk planner, encoder pipe with `MOTION_X264_PRESET`/`CRF`, concat, mux with `negative_cts_offsets`, media extraction served through `route.fulfill`, chunk cache keys, page recycling.
- Preflight framework (`textBoxes()` sampling, result schema in `Render.options["qa"]`) and the post-render container checks with their remedies (§7.12).
- Engine switch in the render job; `board_generic`, the `mesh_drift` and `colour_block` backgrounds, cut and crossfade, in-page phrase captions and the caption-only layer mode; a minimal `TimelinePresentation` and `ResolvedLook`; the admin flag `DYNAMIC_ENABLED`; the `motion` compose profile; the benchmark harness.

**Contracts frozen:** stage contract v3; the `/render-dynamic` route; `RENDER_MOTION_WORKERS`, `RENDER_CPU_SLOTS`, `RENDER_SLOT_DIR`, `MOTION_X264_PRESET`, `MOTION_X264_CRF`; the chunk cache key; the preflight result schema.

**Tests:** determinism (1×300, 3×100, reverse) in Docker; parity SSIM ≥ 0.98 at 20 instants; easing accuracy ≤ 1e-4; grapheme splitting for Tamil, Devanagari and Telugu; exact frame count; first video PTS 0 and A/V start within one frame; guard trips on a stray timer; W from fake cgroup files; slot locking across processes; decoded-frame continuity across chunk joins.

**Acceptance**
- The showcase fixture with a synthetic `presentation` renders in 16:9 and 9:16 through `board_generic`, passing preflight and the container checks.
- **The B1 benchmark** (§7.10) is run in the production render image; P1's numbers and the compose profile are set from it, and Q7 is answered with measured data.
- A reviewer steps frame by frame through 2 transitions and 3 scenes in the MP4 and the live player side by side: identical layout, no flicker, no dropped or duplicated frames, no seams at chunk joins.

**Estimate:** 3–4 weeks.

**Status (B1 integration 2026-10-10, completion pass 2026-10-11): built and integrated; the acceptance renders and
the automated parity and determinism checks pass on the development laptop. Still open: the production-image
benchmark under the `motion` profile, the first run of the CI `container` job and the owner's frame-by-frame
review.** Contracts as frozen above (stage contract v3, `/render-dynamic`, the settings, the chunk cache key, the
preflight result schema 1). Classic output unchanged: the goldens pass, and the differential pixel test of the
showcase against the pre-B1 tree gave 9,701 of 9,701 frames identical, identical audio and an equal timeline
(Windows, ffmpeg 8.1); the completion pass keeps the classic capture's Chromium flags (`CHROMIUM_ARGS`) and a
classic render's options as they were. Details: `ARCHITECTURE.md` §14, `INTERFACES.md`, `API.md`, `OPERATIONS.md`
§3, §4.2.
- **Acceptance.** The showcase (16 scenes, 5.2 min, three AI clips and a simulation as test clips, a quiz, two chapter
  cards) with a synthetic presentation rendered through the real `render_video` job in a throwaway app with fake
  providers: 16:9 burned, 16:9 clean with the caption track, and 9:16 burned. Preflight passed (117 / 289 sampled
  frames, nothing to fix), every container check passed without a remedy (9,356 frames exactly, video and audio
  starting at 0, BT.709 TV range). On the QA fixture (`tests/determinism`, both formats): live player vs MP4 SSIM
  ≥ 0.98 at 20 instants, and 1×300, 3×100 and reverse give identical frames. Frames at every scene start,
  mid-transition, scene middle and end and at every third caption change of the showcase were inspected in both
  formats.
- **Fixed while integrating** (each found by measurement or by looking at the frames): the render page booted only
  when frame 0 showed no clip (its first paint asked for per-chunk media frames before any were served; the boot
  paint now leaves them out); media a mode cannot show (a GIPHY GIF in the MP4) left an empty black box (the scene
  is now laid out without it, and the render's warning says so); a 4-column table was clipped and ran into the
  caption band in 9:16 (more than two columns are drawn one line per row); a terminal card collapsed before its
  output appeared (it now enters with its first output line); clipped text was not reported (`textBoxes().overflow`
  also counts sideways clipping) and a card shown only between two beats was never sampled (preflight also samples
  every beat's settled frame and, in 9:16, every phrase caption); the AAC encode of the master audio (17-26 s for 5
  minutes at 384 kbps) held up the final mux (the master is now encoded during the capture and the mux copies it:
  41 s → 1-2 s); a crashed page is now a retryable `stage_crashed`; one Playwright driver serves all workers (≈140 MB
  per worker saved; no measurable speed difference).
- **Fixed after the B1 review** (each with a regression test): a video panel shown after the first chunk failed the
  render (the page painted its first frame while hidden, the plan did not list it); clips at another rate than the
  page grid (24 fps Veo) showed frames that depended on where the chunk started (now extracted at their own rate, page
  index n → `floor(n * rate / srcFps)`); the live player's classic CSS (`.rt-k` gold glow, `.rt-code` chip) changed
  the stage's rich text (the stage now resets it; the parity eval loads `watch.html`'s stylesheets, and a dark and a
  light family are checked to the pixel); presigned image URLs expired in long renders (signed again for every page);
  a renderer crash during the CDP capture aborted the attempt (now a retryable `stage_crashed`); chapter titles (the
  kicker) were not in the chunk key (`v: 2`); a failed audio master was seen only after the capture; a Hindi danda
  lazily loaded Noto Sans Tamil mid-render (the lecture's script now comes first in the font stacks, and `seek()`
  waits for a face its frame pulled in, then measures and paints again); a crowded 9:16 quiz drew its last option into
  the caption band with `overflow: 0` (tighter option cards when needed; what still does not fit is overflow, and with
  burned captions preflight treats the caption band as taken); 9:16 captions cut words inside grapheme clusters and
  left one-word chunks of 0.31 s (cluster-safe splits keeping word timing; minimum-cost grouping: the showcase's
  shortest chunk 0.63 s, 12 of 168 under 1.0 s instead of 42 of 172); cards no beat reveals were never shown when more
  entered together than fit (they are re-timed to stay 1.5 s each; the chip counts only cards that were seen); the
  title fitter accepted words broken inside; ink2 on a colour block fell to 2.6:1 (now ≥ 3:1, and the subtitle is
  checked); 9:16 card text is weight 600 and labels ≥ 44 px (40 px in 16:9), with a warn-only `type_floor` preflight
  check (an added key in the schema-1 `checks`); `DYNAMIC_ENABLED` now applies when a timeline is served, so the
  player and the MP4 never show different engines and turning it off stops the Dynamic stage at once.
- **Closed in the B1 completion pass (2026-10-11)** (each with a regression test):
  - *Preflight and engine:* findings are no longer duplicated across the two rounds (one item per check, scene and
    slot); preflight draws and checks the render's own caption layer (`setLayers` after every load, the reload
    included), not `presentation.captions_burn`; it expects the engine's scene-bounds frame count (a final scene under
    half a frame keeps one frame); every worker's page, and the remedy's, is checked against the timeline, and an
    empty `info().scenes` is a `stage_contract` mismatch; a page that fails to open is closed; a failed chunk leaves
    no extracted media frames; overlay clips are not fetched before the capture until B6 (`engine.OVERLAYS_DRAWN`);
    the `rerender_chunks` remedy waits for and holds one CPU slot; chart, graph, 3D model, skill tree and quiz side
    panels, which the stage does not draw, give a render warning (reason `panel_not_drawn`) instead of being dropped
    silently; Dynamic word-anchored sync cues snap to the 30 fps grid (`DYNAMIC_FPS`), not `RENDER_FPS`.
  - *Determinism:* Chromium's partial raster made frames depend on render order (76 of 300 portrait QA-fixture
    frames differed between 1×300 and reverse, ≤ 3 levels); the Dynamic stage now launches with
    `--disable-partial-raster` (`stage_page.DETERMINISM_ARGS`, also in the chunk key): 0 mismatches in both formats,
    no measurable capture cost (§7.4, §7.5). Ken Burns stills and `contain_blur` stills and clips are covered by
    `tests/determinism/test_kenburns_determinism.py` (Windows, both formats); `tests/compose/motion/test_launch_args.py`
    keeps the launch flags and the cache key's flags equal.
  - *Stage:* `fit: "contain_blur"` is drawn (§7.7); crossfades are clamped to 300–500 ms (`transitions.js`
    `CROSSFADE_MAX` 0.5, was 0.8); quiz questions keep their rich-lite markup (TeX, bold, terms, code) word by word,
    bold at weight 900 (§4.8); a scrim that preflight plans for the kicker, the title or the subtitle scrims the whole
    title block and `textBoxes()` reports the composited scrim colour, so contrast remedies end `fixed`, and a centred
    block's scrim stays centred; in 16:9 the title block ends 24 px left of the 96 px quiz ring (120 px narrower),
    `textBoxes()` reports the countdown number as a non-critical label, and in 9:16 the number is 44 px (R-29); light
    families: `dynamic.css` has no family-dependent literal colours left (the stage and each scene set `--dyn-ink` and
    `--dyn-page`, which drive the stage, the scene and the matte around contained media; the ring track is the ink at
    18% for a light ink, 25% for a dark one); the live presenter clip follows the playback rate; a paused live clip
    is sought once it can seek (§7.7).
  - *Player and Studio:* the portrait Player is done: the watch and Studio preview boxes take a reel's shape, the
    control bar fits the box, and Dynamic captions keep their format's size (§9.7); the render dialogs take a Dynamic
    lecture's defaults from `GET .../render/preflight` `presentation` (§9.5); the job stepper follows the engine
    (classic: screenshots, segments, mux, check, store; motion: preflight, capture, mux, check, store).
  - *API:* `POST /render` without `burn_captions` lets a Dynamic lecture's `presentation.captions_burn` decide (the
    field was always stored as `false`, so the presentation never applied); a classic render still stores `false`.
  - *Tests:* `tests/conftest.py` also pins `DYNAMIC_ENABLED`, `RENDER_MOTION_WORKERS`, `RENDER_CPU_SLOTS`,
    `RENDER_SLOT_DIR`, `MOTION_X264_PRESET` and `MOTION_X264_CRF` to their defaults; in the Docker test image
    (`AADHI_CONTAINER_TESTS=1`) a missing tool or bundle fails the B1 suites instead of skipping them (§10.3 B0, as
    built), `tests/api/test_render_dynamic_route.py`'s live app and `tests/invariance/test_render_slow.py`'s tools
    included; that route's contract test launches with `DETERMINISM_ARGS` and accepts a chapter card's critical
    `kicker` slot.
  - *Alongside:* H0 on v2 (`ModelUnavailable` and the Edge fallback for retired Gemini and OpenAI speech models,
    `google-genai>=2.26`, §10.3 H0); a provider rate limit while building the concept brief ends the attempt and the
    job is retried, instead of planning without the brief; a translation copies its source version's `ingest_key` and
    `source_overrides`, so regenerating one of its scenes reads the extract its scenes cite; a recap moved after the
    hook no longer leaves an empty chapter behind.
- **Deviations from this design:** `-threads 4` added to §7.5 (x264's default took 765 MB per encoder and 4 encoders
  reached 67 fps together; with 4 threads 290 MB and 156 fps); chunks are reused only by a retry of the same render
  (§7.6: a new render gets a new workspace); preflight also samples scene ends and beats (§7.12); no `will-change`
  (except a `contain_blur` clip's box), whole-pixel positions, media frames that rise instead of scaling, and
  `--disable-partial-raster` on the Dynamic stage's Chromium, all needed for order-independent frames (§7.4);
  `mesh_drift` paints on a 1/8 canvas stepping every 0.2 s; `--disable-frame-rate-limit` is not used (inconclusive
  in the B1 measurements); the template contract adds `layout()` / `texts()` to §4.2 and has no `ctx.cues` yet
  (B2's `cues.js`); `TemplateContext.onBackground` also carries `bgs` (every background colour).
- **Measured on the development laptop** (i7-13650HX, Windows, chrome-headless-shell 153, ffmpeg 8.1; W = 6; runs of
  one configuration varied by up to ±25%): one page 41-64 ms per 1080p frame (`seek()` 4-8 ms + CDP JPEG 37-55 ms;
  budget 45 ms; the background canvas costs 8-20 ms of it); 6 workers 6-12 fps each, 35-60 fps together; the 5-minute
  16:9 benchmark in 163-170 s, the 3.5-minute reel in 144 s, the 5.2-minute showcase through the job in 193-281 s;
  fixed costs per render 10-25 s (preflight 4-12 s); memory 0.6-1.0 GB per worker plus one 0.2-0.3 GB driver.
- **Open (owner or later batches):** the owner's frame-by-frame side-by-side of MP4 and live player (2 transitions,
  3 scenes; the automated parity and chunk-join tests pass); the B1 container benchmark in the production image
  under the `motion` profile, which sets P1, the profile limits and answers Q7, and the CI `container` job's first
  run on ffmpeg 5.1 (it includes the determinism suites, which have so far run on Windows only). Neither ran
  locally: Docker Desktop is installed, but the development machine's C: drive has about 7 GB free, too little for
  the production image. The app image has no `scripts/` or `tests/`, so the benchmark runs with both bind-mounted
  (the command is in `OPERATIONS.md` §4.2). Hand-overs: B2's `chart_build` removes `"chart"` from
  `compose/motion/job.py` `UNDRAWN_PANEL_KINDS` (and so for any other panel kind a B2 template draws); B6 sets
  `engine.OVERLAYS_DRAWN = True` with the overlay layer; B2 gives `ResolvedLook.presenter` a live `url` and the live
  presenter clip the paused-seek handling of `MediaView` (§7.7); B2/B6 add an anchor to `MediaView` for the 9:16
  inserted-clip layout (§2.5). Also open: preflight's remedies reach only the MP4
  until B2/B4 store them in the lecture (an allowed difference until then); a 9:16 split layout shows its picture
  small when the cards are tall, and a 9:16 terminal card can still collapse before its last line (B2's
  `media_split` and `code_terminal`); the analytics flags for the allowed live/MP4 differences and the low-end
  fallback (B4); static-run deduplication, the burned second version and draft renders (B4/B9); a cheaper
  `mesh_drift` composite (its canvas is the largest per-frame capture cost); 9:16 caption chunks that stay under
  1.0 s because their beat is too fast for any grouping at the fake TTS rates (12 in the showcase; B2's `kinetic.py`
  merges across beats with measured widths, about 20 characters per line at 56 px); a card revealed on the same beat
  as a taller card that cannot sit beside it (the showcase's 9:16 terminal scene, "The law is ready") is still pushed
  out before it is shown (B2's `code_terminal` and `media_split` layouts).

#### B2. Visual system and the R1 templates

**Scope**
- `compose/dynamic/themes.py` → `web/dynamic/themes.json`: the fixed content palette (§4.7), the 4 R1 families, structure accents, the validator (contrast, hue separation, colour-vision distance), the role API.
- Fonts vendored with verified licence files: Inter, Outfit, Poppins, Lexend, Atkinson Hyperlegible Next, Anek, JetBrains Mono. Icons: Lucide and Tabler with the offline index.
- The 6 R1 backgrounds with the hold freeze; cut, crossfade, push, colour wipe and shared-element morph; the Crisp and Soft personalities; `cues.js`.
- `textfit.py` (including the Pillow WOFF2 check in the container; *B2b-H: text fit shapes with HarfBuzz and no longer reads faces through Pillow, §4.6*) and `zones.py` with the safe-zone overlay.
- `kinetic.py` (phrase chunks, measured widths, blanking, the key-term rule) and the `clean_pill` and `bold_outline` caption styles.
- **The 23 R1 template modules** (§4.3) in both formats, including the four 16:9 intro layouts, the 9:16 card and the Aadhi frame-extraction cache. A template that draws a side-panel kind B1 leaves out removes that kind from `aadhi/compose/motion/job.py` `UNDRAWN_PANEL_KINDS` (`chart_build`: `"chart"`), so its `panel_not_drawn` render warning goes with it.
- `direct.py` (classes, locks, bounded post-pass, entity mapping, accents, backgrounds) and `events.py` (anchoring, cue limits, R-49 SFX policy).
- A dev-only **template gallery** (`web/dynamic/gallery.html`): every template × format × the 4 families, with the longest allowed text and Tamil and Hindi text.

**Contracts frozen:** `TemplateSpec` fields; the semantic-class table; the role and cue APIs; `SceneLook.lock` semantics; the `themes.json` schema.

**Tests:** Python/JS catalogue parity; the content palette pinned (contrast ≥ R-53 on every background of its mode, hue separation ≥ 30°, colour-vision distance ≥ 0.05); the structure-role and cue source scans; the `direct.py` property tests of §4.4; R-17 anchoring, R-26 cue limits and R-49 SFX placement; the chunker (R-32, R-33, Indic text, measured widths, blanking, first-two-mentions); text fit against page overflow on the gallery fixtures; gallery golden frames in Docker.

**Acceptance:** the owner signs off the gallery against a checklist — legible at phone size; contrast; safe zones; no clipping or overflow with the longest text and with Tamil and Hindi; smooth motion; no flashes; colour never the only cue; "would a student want to keep watching?" — and every R1 template passes in both formats.

**Estimate:** 4–5 weeks.

**Status (B2a, 2026-10-14): contracts and foundations built and reviewed; B2b–B2g to do.** B2 is built in steps (`docs/plans/b2/b2_plan_v2.md` §A): B2-0 (the transform spike, §4.8), B2a (contracts and foundations, with no visible change for B1 timelines), then B2b assets (fonts, icons, `textfit.py`), B2c a vertical slice (`formula_focus`, `media_split`, `list_build` and `cues.js` through every gate), B2d the time engines (`events.py`, `kinetic.py`), B2e `direct.py` and the resolver, B2f the remaining templates, backgrounds, transitions and personalities, and B2g the gallery and acceptance. B2a is the commits `7687c4e` and `56ed3e9` (A1: colour and themes), `6489b2d` (A2: zones, registry, catalogue, schema, fixes, resolver, serving) and `6de3ddf` (B: the JS consumers), plus the fixes of its review. Details: §2.5, §4.2–§4.4, §4.7–§4.9, §6.7, §7.3, §7.4, §7.6, §7.12, `docs/INTERFACES.md`, `docs/plans/b2/b2a_review.md`.
- **Built.** Python: `colour_math.py`, `themes.py` and its validator, `zones.py`, `templates.py` (41 specs, the class table, the signal rows, the target grammar), `catalogue.py` → `web/js/dynamic/catalogue.gen.js` (checked in CI), the timeline schema additions, the fixes channel, the resolver's stored tokens and palette slots, and the serve-time rebuild. JS: `core.js` reads the catalogue (zones, roles, type floors, slots) and the stored tokens; `engine.js`; the template contract hooks in `board_generic`; fixes-first remedies; page-wide TeX conversion with its counter; the new scan rules.
- **No visible change for B1 timelines.** A timeline served as engine 1 draws exactly what B1 drew. In real Chromium (the real `/render-dynamic`, CDP JPEG SHA-256), the B1 and the B2a stages gave 3,768 of 3,768 sampled frames identical on the QA fixture (the default dark look and paper_ink, both formats) and the showcase (four R1 families, both formats, and a variant without chapters), and in a second comparison 0 of 2,204 frames differed (the QA fixture and resolver-built lectures); in jsdom, 12,800 sampled frames of 32 B1-shaped timelines (legacy remedies, TeX, media, all 12 R1 palette slots) gave the same frame key, DOM and media plan. A timeline the B2a resolver builds (engine 2) differs from B1 only where intended: on the owner-decision palette slots' colour-block chapter cards and the crossfade out of them (showcase, both formats: midnight_indigo slot 2, 32 of 256 sampled frames, the kicker and subtitle ink only; paper_ink slot 2, 33 of 256, the block itself; deep_teal and mint_studio unchanged), and in lectures whose screenplay names no chapters, where the accent now changes at their chapters (§4.7). Classic output is unchanged (the invariance goldens; a fresh-interpreter test shows a classic build imports no Dynamic module, and a 40-scene Dynamic preview builds in about 45 ms against a 300 ms budget).
- **Contracts frozen** (plan §1): the catalogue (version 1); `TemplateSpec` and the class table; the target grammar; the JS template module and context; the role API; the `ResolvedLook` and `TimelinePresentation` additions; the fixes channel; `engine_version` as the look-engine generation.
- **Deviations realised** (plan §D): 1 `catalogue.gen.js` instead of `web/dynamic/themes.json` (§4.7); 2 the fixes channel instead of the safe variant through `variant` (§7.12); 4 the schema additions (§6.7); 5 the `TemplateSpec` additions (§4.2); 12 the transform policy (B2-0, §4.8); 13 the `code_walkthrough` limits (§4.3); 21 `engine_version` as the look-engine generation (§6.7, §7.3); 22 the lesson-wide entity map (§6.7). New in B2a: the block-ink rule (owner decision: `block.ink` the stage's own choice, `block.ink2` hold-safe; §4.7); the chapter-detection fix (§4.7); R-54 crossfades stored as clamp(d / 2, 0.3, 0.45) s with a cut below 0.3 s, and the interim in-chapter palette-slot crossfade (§4.9); the background clock in the chunk key and `CHUNK_CACHE_VERSION` 3 (§7.6); the serve-time rebuild of older generations and the engine in the ETag (§7.3). Values marked in the code as "A2 proposal, pending owner review": the 16:9 named regions (§2.5), `media_split`'s variant names and the `diagram` hold list (§4.3), the spacing steps 8–128, the icon card and feature sizes (96 / 320 px), the Indic heading line height ≥ 1.25, `key_terms`' 9:16 timing (an A2 reading), and the in-chapter palette-slot crossfade; also for the owner's review: the roadmap states, anchor kinds and icon caps (§6.7) and the `motion_scale` of an unreleased personality (§4.8).
- **Review** (`docs/plans/b2/b2a_review.md`): four lenses (the plan, Python, JS, and a real-Chromium differential), one skeptic per finding, two fix rounds, each audited. 16 findings: 4 refuted, 11 fixed, 1 deferred to B2c (the split 9:16 misconception, §4.2); the second audit found 3 more gaps in the source scan's guards, closed by the lead after the workflow (§7.4). Gates after the fixes: ruff, the six generated-file checks, `tsc`, `npm test` (1,235 pass, 1 skipped), the fast suite (4,874 passed, 5 skipped) and the slow Chromium tests touched by B2a (28) pass.
- **Open.** The three scan gaps (§7.4); the split misconception's target (B2c); the owner's review of the values above; `ctx.motion` / `ctx.outgoing` (B2c); `accent_fade`, `refs`, `icons`, `fonts.indic` and the 9:16 act slots (B2b/B2e); the code-editor surface token (B2f); `board_generic`'s `mediaBox` ignores the fixes, so when a title is capped below its minimum a clip can be shown up to about 3% larger than it was extracted (B1 behaviour; `media_split` sizes its box from the fixes, B2c); a figure that is a video, or a video poster, still has no planned frames in render mode (B1 gap); `anchor()` is null (the morph, B2f); `stage_page.py` does not assert `texConversions()` yet (B2g); the live watch page shows its generic "could not be played" message for a newer-engine timeline (a "reload" message would fit better); the R-55 luminance-ratio advisories on `mesh_drift` (B2f); the classic player now loads `engine.js` (one module without imports; classic output unchanged). Not run for B2a: the slow suite beyond the Chromium tests it touches, the Linux container run and the B1 container benchmark.

**Status (B2b, 2026-10-15): assets built, reviewed and committed.** B2b is plan §A step 2: fonts (with the face table), icons and `textfit.py`. Its build is the commit `d93226d` ("WIP B2b"). The review's first fix round is partly in `0e14281`: that fixer was cut off by a network error, and a second fixer reviewed, kept and completed its changes. The rest of the fixes are in `f923487`, and the docs in `d63ff94`. **The text-fit parts below (Pillow measurement, the kerning table, the 4% / 8% margins, the Indic advisory rule, the Debian WOFF2 check) are superseded by B2b-H** ("Status (B2b-H)" below; §4.6). Details: §4.6, §4.12, §6.7, §7.3, §7.4, §7.13, `docs/INTERFACES.md`, `docs/OPERATIONS.md`, `docs/plans/b2/b2b_review.md`.
- **Built.**
  - **Fonts:** Poppins, Lexend, Atkinson Hyperlegible Next and Anek (five scripts) vendored under `web/vendor/fonts/dynamic/` with verified OFL 1.1 licences. One face table, `faces.py`, generates `web/css/dynamic-fonts.css` and `web/js/dynamic/faces.gen.js` (`--check` in CI), and `vendor.mjs` copies the faces from it (`vendor_faces.mjs`, fail closed). `presentation.fonts {display, body, mono, indic}` is typed and filled by the resolver with B1's faces. The stage's `fontStacks` and `stageFontRequests` (`fonts.js`) feature-detect the B2 form.
  - **Icons:** Lucide 1.48.0 and Tabler 3.48.0 pinned; the committed offline index (`icon_index.json`, `--check` in CI); `icons.py` (normalisation to canonical path `d`, sanitising, matching, `scene_icons`).
  - **Text fit:** `textfit.py` with the measured kerning table `kerning.json` (`kerning.py`); Latin certified, Indic, uncovered and unbounded runs estimated only.
  - **Notices:** `THIRD_PARTY_NOTICES`, generated with `--check` in CI, covering the 36 runtime packages and the 37 third-party components inside the vendored bundles; shipped in the image.
- **No visible change.** Existing timelines draw the same frames. The review's differential ran in real Chromium (each side its own real app and `/render-dynamic` page, CDP JPEG q92 hashes) on the QA fixture and the showcase in dark and light families, both formats, display ids `outfit`, `poppins`, `lexend` and `inter`, with Tamil and Hindi cases. It compared c3a1e9b (B2a) with the B2b build, and found 0 of 11,031 frame pairs differing in three comparisons:
  - (A) the same B2a-built timeline on both stages;
  - (B) the same B1-built (engine 1, built by the B1 code at `3226e20`) timeline on both stages;
  - (C) each side's own resolver and stage on the same inputs.

  As controls, Outfit against Inter differs in 312 of 341 frames, and a reverse-order capture gives the same hashes. The B2b resolver's JSON differs from B2a's only by `fonts.indic = "noto_sans"` and, for display `poppins` and `lexend`, `fonts.display = "outfit"`. The fix rounds after the differential changed no drawing code: the only `web/` source changes are comments, `web/vendor` gains `mathjs/math.js.LICENSE.txt` (a text file no page loads), and textfit and kerning have no production importer. Classic output is unchanged (the invariance goldens; a classic build imports no `aadhi.compose.dynamic` module and no `PIL.ImageFont`).
- **Owner decisions (2026-10-14).** Indic text fit uses HarfBuzz shaping (`uharfbuzz` + `fontTools` + `brotli`), built as the step after B2b; until then Indic fits are estimates and advisory. Icon matching is exact name first (either set), then Lucide tags, then Tabler tags.
- **Deviations realised** (plan §D): 19 Anek's axis rotation stays W2, and only its `wght` file is vendored, for the five supported scripts (§4.6). New in B2b:
  - Fonts: the R1 display weights 400/600/700/900 (§4.6); the resolver stores `outfit` for a display id the B1 stage did not draw, until B2e (§6.7); the B1-form own-property lookup of the display id (§7.3).
  - Text fit: the per-word 0.06 em floor and the measured kerning table beyond the plan's 4%, with "within 4% slack" read as "within Python's margins" (§4.6). Indic text, combining sequences, uncovered characters and unbounded Latin runs are not certified.
  - Icons: the committed icon index instead of icon files in `web/vendor`; filled icons and Tabler's brands excluded; no Phosphor fallback; Lucide's deprecated names as aliases (§4.12).
  - Notices: the bundled-component table and the vendored `math.js.LICENSE.txt` (§4.12).

  For the owner's review (B2b proposals): `PREFERRED`, `IGNORED_TAGS`, the negation and singular rules, and the refusal of symbol-bearing and one-character hints (§4.12).
- **Review** (`docs/plans/b2/b2b_review.md`): four lenses (the plan, licences, code, and a real-Chromium differential), one or two skeptics per finding, two fix rounds, each audited. 10 findings, none refuted, all fixed:
  - 1 blocker: the notices missed the code inside the vendored bundles.
  - 3 major: the same gap seen by the licence lens, `f'f'f'f'` certified while overflowing, and `TimelineFonts` silently read as B1 defaults.
  - 6 minor.

  The first audit found Inter's chained contexts still uncertifiable by pairs and triples (major), a regression of the stored size for over-wide Inter and Lexend words (major), and the wrong year in the libtess notice (minor). The second round fixed all three, and the second audit found nothing.
- **Gates after the fixes:** ruff; the nine generated-file checks (the six of B2a plus `faces`, `icons` and `gen_third_party_notices`); `tsc`; `npm test` (1,252 tests: 1,251 pass, 1 skipped); the fast suite (5,289 passed, 5 skipped); the slow Chromium tests (56 passed: B2a's four files plus `test_stage_fonts`, `test_icon_pixels`, `test_textfit_page` and `test_kerning_table`). The kerning table was regenerated (85 min); its own `--check` was not re-run, but the second audit re-measured 5 faces and matched the committed rows byte for byte.
- **Open.**
  - The Debian WOFF2 check and the Linux runs of the slow probes: the container job has never run (§4.6).
  - The HarfBuzz step (owner decision). *Done: B2b-H.*
  - The B2c icon helper in `_shared.js`. `look.icons` and the display rotation are B2e's; so is the live player's `stageFontRequests` preload (§7.3). B2e must also decide whether `RefAnchor.icon` (a `CatalogueId`) names an icon that is also put in `look.icons`.
  - The owner's review of the icon lists.
  - Slot geometry for B2c: `board_generic`'s kicker box is 30 px narrower than its slot's `w`; an over-long kicker is cut with an ellipsis and reported as no overflow; the card text slot's `w` is the card's outer width; `.dyn-subtitle` has no `overflow-wrap`; `SlotSpec` has no size step.
  - Greek and maths symbols (π, Ω, √, →) are in no vendored Latin subset, so Chromium draws them with a system font that differs between Windows and Debian (a determinism risk B1 already had); vendoring Inter's Greek and symbol subsets would fix it.
  - For an over-wide word, Python's stored size can be one 4 px step below the page's.
  - A few glyphs are drawn wider than Pillow's advance outside the slot weights (Lexend 900 `″` +5.7%), which textfit does not measure today. *(B2b-H: no longer applies; textfit measures shaped widths, weight 900 included.)*
  - The bundled-component licence texts not compared with their packages (§4.12).
  - Cost: `lucide-static` unpacks to about 51 MB in `npm ci` and `@tabler/icons` to about 11 MB (neither reaches the image); `icon_index.json` is 1.37 MB and `kerning.json` 2.9 MB in the repository (*B2b-H deleted `kerning.json`*).
  - Not run: the Docker image build, the container job, and the kerning generator's `--check` (*the generator is deleted in B2b-H*).

**Status (B2b-H, 2026-10-15): HarfBuzz text fit built, reviewed and committed (`65e6f1d`); two audit findings accepted open for the deadline.** B2b-H is the step the owner added after B2b. It rebuilt the text fit on HarfBuzz shaping for every script and retired the kerning table. The build and most of the review's first fix round are the commit `87fa371` ("WIP B2b-H"). The rest of round 1 (one test assertion), rounds 2 and 3 (`textfit.py`, `test_textfit.py`, `test_textfit_parity.py`) and this docs pass are in the working tree. Details: §4.6 "As built (B2b-H)", §4.12, §6.7, §7.13, `docs/INTERFACES.md`, `docs/OPERATIONS.md`, `docs/plans/b2/b2b_h_review.md`.
- **Built.**
  - **Shaping:** `aadhi/compose/dynamic/shaping.py` decodes WOFF2 with fontTools and brotli, then shapes with uharfbuzz using the stage's `unicode-range` filter, axes, features, script runs (a port of Chromium's `ScriptRunIterator`) and language.
  - **Text fit:** `textfit.py` adds Chromium's font fallback, letter-spacing per cluster, the cursive-script rule and `tnum` for the ring (`SlotSpec.tabular_nums`). It also adds whitespace collapsing as the page does it, flow readings, zero-width-space joins, and the margins 0.5% + 1/64 px.
  - **Probe:** the slow parity probe `tests/determinism/test_textfit_parity.py`, with the opt-in extended form `AADHI_TEXTFIT_PARITY_FULL=1`.
  - **Dependencies:** `uharfbuzz` 0.56.2, `fonttools` 4.66.1 and `brotli` 1.2.0 in `requirements.txt` and `requirements.lock`.
  - **Retired:** `kerning.py`, `kerning.json` and their two test files.
- **No visible change.** textfit is still unwired. No `web/` file, `direct.py`, preflight, the resolver or the schema changed. Classic isolation now also forbids the three shaping libraries; `brotli` is excused only beside an HTTP client that imports it.
- **Owner decisions (2026-10-14/15).** Text fit uses HarfBuzz (2026-10-14). Shape every script if a Chromium parity probe confirms parity, then retire the kerning table (2026-10-15). Parity held for every face, weight and script, so both were done. Icon order is unchanged. No many-minute check runs in CI: the extended probe is opt-in.
- **Deviations realised:**
  - Plan §1.11's margin is 0.5% + 1/64 px instead of 4% / 8%.
  - Plan §2 "Fonts" uses HarfBuzz shaped widths instead of `Layout.BASIC` advances.
  - `SlotSpec.tabular_nums` is a new field (Python only).
  - Bidi controls and non-plain whitespace are left uncertified rather than modelled (§4.6).
- **Review** (`docs/plans/b2/b2b_h_review.md`): three lenses (accuracy in real Chromium, dependencies, code). Every finding had one or two skeptics; three fix rounds followed, each audited.
  - 12 findings, none refuted: 1 blocker, 3 major and 8 minor after the skeptics. DEP-1, the `HB_SHAPER_LIST` dependence, was rated major by its lens and lowered to minor by both skeptics.
  - Round 1 fixed 10 and left the 2 documentation findings to this docs pass.
  - The audits found 4 more: 2 fixed in round 2; 1 fixed in round 3 for words on the same line only; 1 pre-existing and untouched.
  - **Open after round 3, both rated blockers by the audit:** the break before a zero-width-space-joined word in flow text, and an over-wide word that starts with a hyphen (§4.6 "Known gaps"). Both must be fixed and audited before B2b-H is committed as final.
- **Gates on the final tree** (round 3; the third audit reproduced them on a scratch worktree):
  - ruff clean; all ten generated-file checks pass (B2b's nine plus `lock_requirements --check`); `tsc` clean.
  - `npm test`: 1,252 tests, 1,251 pass, 1 skipped.
  - Fast suite: 5,399 passed, 5 skipped, 0 failed.
  - Slow Chromium gate: 58 passed in 5 min 8 s. It ran `test_textfit_page`, `test_textfit_parity`, `test_stage_fonts`, `test_board_real_page`, `test_stage_layout`, `test_live_css` and `test_preflight_stage`.
  - Extended probe: last run after round 2, 5 passed in 456 s. Round 3's new test has no extended mode.
  - Mutations: 31 in the build (2 of them in real Chromium), 9 in round 1 (1 in real Chromium), 18 in round 2 and 7 in round 3, all killed. Round 3 could not exercise the `join_spacings` term of the chain bound.
- **Open.**
  - The two audit findings above.
  - Linux Chromium is unmeasured (the container job has never run), and the Docker image was not built.
  - The Python notices gap (§4.12, owner).
  - Re-run the extended probe before a `uharfbuzz` minor bump, or after a Playwright bump.
  - Characters in no vendored subset stay uncertified (Greek, maths symbols).
  - Flow text is measured word by word (conservative).
  - For an over-wide word, Python's stored size can be one 4 px step below the page's (owner or B2e).
  - `test_faces.py` still opens every face with Pillow, which is now only a readability check.
  - Wiring textfit into `direct.py`, preflight and the chunker (B2d/B2e).

**Status (B2c, 2026-10-15): the vertical slice built, reviewed and merged (deadline mode).** B2c is plan §A step 3: the
shared template layer, the cues and three pilot templates through every gate. It is the commit `3f3899b` on the branch
`b2c`, merged into `overhaul/v2` as `6ff938a`. From here on the owner's deadline process applies (one review round, at
most one fix round, no skeptic loops; "Deadline mode" below). Details: §4.2–§4.4, §4.7–§4.9, §7.12,
`docs/plans/b2/b2c_review.md` (what the review found) and `docs/plans/b2/b2c_design_notes.md` (**the as-built pattern
every later template follows**).
- **Built.**
  - **`web/js/dynamic/templates/_shared.js`:** the layer every template draws through — catalogue geometry (`region`,
    `gridSpan`, `boxRect`, `insetBox`, `place`, `SPACE`, `RADIUS`), text (`rich`, `tex`, `richWords` keeping the
    `[[term]]` `.rt-k` spans a term cue needs, `fitLines` as the page fits, `slotRange` with preflight's fixes and the
    R-29 floor), motion (`seconds` for the personality's scale, `enterAt`, `paintRise`, `revealMask` +
    `paintTextEntrance`, `group`), item states, the §7.12 scrim helper moved out of `board_generic`, the icon helper
    that re-sanitises a stored `LookIcon` before drawing it (`ICON_PATH_RE` and the schema's caps, `d` through
    `setAttribute` only), media (`mediaAspect`, `containRect`, `pushHeadroom`, `paintMedia`) and the §4.7 formula
    tints (`tintTex`, `tintTargets`, `paintTint`).
  - **`web/js/dynamic/cues.js`:** `cue.term` and `cue.look_here`, drawn by the stage from the scene's events for every
    template alike (plan §1.5, deviation 3), only for the kinds and target forms the template's `consumes` declares.
    A template contributes geometry only (`itemEl`, `focusRect`, `dimGroups`).
  - **`state.js`:** the item states (current / past / done / struck) and `itemStateStyle`, beside B2a's entrance times,
    frozen background clock and caption.
  - **Three pilots, both formats and the dark and light families:** `formula_focus` (MathJax at up to 1.6x
    `board_generic`'s formula size in a drawn box, per-symbol entity tints, braces placed at the symbol's own glyphs, a
    legend row per variable, the R-16 look_here walk, dense so the background freezes at 0 and makes no sound),
    `media_split` (a clip or picture beside text, its box sized from the fixes unlike `board_generic`'s, `push_ok`,
    media side flipping only at a chapter boundary) and `list_build` (per-item build-up with the item states and the
    2 px `ink2` current-item border). Registered in `stage.js` `TEMPLATES` and in `templates.py` with
    `implemented=True`, `slots` mirroring the JS constants (pinned against each other by a jsdom test) and `consumes`.
  - **Gates of their own:** `tests/determinism/test_template_pages.py` (the 1x/3x/reverse order-independence gate in
    real Chromium), `test_template_fixtures.py`, `test_template_clips.py`, `test_formula_focus_page.py`, the jsdom
    contract tests under `web/tests/dynamic/templates/`, and the manual reference-frame contact sheet
    `docs/plans/b2/b2c_frames/index.html` (`python -m tests.compose.dynamic.make_reference_frames`).
- **No visible change.** `presentation.TEMPLATES` is still `("board_generic",)`, so the pilots draw only a timeline
  that names them and no existing timeline moved: the no-visible-change differential on the day's 12 timelines gave
  2,664 frames with 0 differences. Classic output is unchanged (the invariance goldens; the slow classic differential
  passed in the 72-test group).
- **Measured.** A `contain_blur` clip under the 3 % look_here push renders differently by frame order (80 of 120
  frames in both formats; 63 of 120 in 9:16 in reverse order with its backdrop), while stills under the same push and
  clips under the medium's own 0.96 → 1 entrance do not. **So a scene that shows a clip never pushes**: its look_here
  cues keep the outline form in both modes (`cues.js` `showsClip`, `tests/determinism/test_template_clips.py`).
- **Deviations and readings** (`b2c_design_notes.md` §6): the term cue styles the term's own span rather than drawing
  in the `.dyn-cues` layer (a marker belongs under its glyphs, and a layer above cannot put it there without copying
  the line layout); cue timings are the design's absolute values, never scaled by the personality; a cue without `dur`
  holds 2 s; R-59's 696 px critical column wins over §4.3's 768 px for `formula_focus` in 9:16 (the 768 px is the
  card); `list_build`'s current item takes plan §1.5's 2 px `ink2` inset border instead of §4.3's 3 px outline; 9:16
  `media_split` gives the medium 16–46 % of the stage (owner decision); R-25 is a lint concern, not a template one.
- **Deadline mode (owner, 2026-10-15).** About two days remained, so the owner chose a working demo over the full B2
  scope: parallel build agents writing only their own files plus one integrator, ONE review round, at most one fix
  round, no skeptic loops, and a push of `overhaul/v2` after each landed phase. B2c was reviewed that way: three
  lenses (design/visual, code and gates, and the no-visible-change differential), one skeptic per finding, one fix
  round; the audit and docs agents were skipped for time. 18 findings survived the skeptics: 15 fixed, 3 deferred
  (V8, V9, NVC-1). The fix round also found a preflight defect and fixed it: `Finding.merge` kept the first sighting's
  status, so an overflow first seen mid-entrance never blocked; a checked sighting now wins.
- **Gates on the merged tree:** ruff, `tsc` and `git diff --check` clean; all ten generated-file checks pass
  (`lock_requirements --check` included, after the merge with B2b-H's pins); `npm test` 1,401 passed, 0 failed; the
  fast suite 5,330 passed, 0 failed; the B2c slow Chromium gates 94 passed; the other slow gates, the classic
  invariance differential included, 72 passed.
- **Open.** The three deferred findings (V8, V9, NVC-1); every template size is a B2c proposal awaiting the owner's
  review of the reference frames; `textfit` is still not wired into rendering (B2d/B2e); `anchor()` is still null (the
  morph, B2f); the remaining R1 templates, `direct.py` and `events.py` are the demo phase
  (`docs/plans/b2/demo_workflow.js`).


**Status (B2 demo phase, 2026-10-15): the director wired in, six more templates registered, engine generation 3.**
The demo phase is `docs/plans/b2/demo_workflow.js`: six template builders and the Director in parallel, then one
integrator. It is what makes a real lecture use the new templates instead of drawing `board_generic` everywhere.
- **The Director.** `aadhi/compose/dynamic/direct.py` (`plan(screenplay, scenes, fmt, chapters)` -> one `ScenePlan`
  per scene: template, variant, events, fit, class, forced/locked) implements §4.4 gated to what the stage can draw
  (`direct.candidate`: `implemented`, release R1, the lecture's format - the only gate, so a template becomes
  eligible the moment its `implemented` flag flips). `aadhi/compose/dynamic/events.py` writes the scene's
  `VisualEvent`s (`reveal` 120 ms before its beat at exactly `state.js itemTimings`' time, `fill`, `var`, `draw`,
  `cue_term` on the first two narrated mentions of a `[[term]]` lesson-wide, `look_here` from focus/emphasis cues and
  the R-16 hold walk), all inside R-26's cue budget.
- **Wired into the build.** `compose/presentation.py resolve_looks` takes the template and the variant from the plan
  (`plans` argument, computed lazily when not given so a classic build still loads no Dynamic module) and
  `compose/timeline.py` puts each plan's events on the timed scene. `presentation.TEMPLATES` is now the implemented
  R1 set, not `("board_generic",)`.
  - **Behaviour change:** a scene with `look.template` and `lock="auto"` may now be re-directed (§4.4 "auto may be
    re-directed on save"); `pinned` and `settled` are honoured, and a lock naming a template the stage has not got is
    ignored instead of drawing nothing.
  - **Every Dynamic scene now carries `events`**, `board_generic` ones included. Their reveal and fill times are
    exactly the ones `state.js itemTimings` already computes from the beats, so no pixel moves; the stored timeline
    grows and a rebuilt project's chunk-cache keys change (cache misses only).
- **Look-engine generation 3** (`presentation.ENGINE_VERSION`, `web/js/dynamic/engine.js`): a stored generation-2
  timeline draws `board_generic` everywhere where a rebuild would now draw a real template, so `aadhi/api/timelines.py`
  rebuilds it at serve time and every cached copy is refreshed by the ETag.
- **Six more templates built and registered** (`stage.js TEMPLATES`, `templates.py` `implemented=True` + `slots` +
  `consumes`, catalogue regenerated), each with its own jsdom contract test, fixture generator and fixtures in both
  formats and both families: `chapter_bumper` (tinted numeral, kicker, title, 16:9 mini-roadmap),
  `definition_card` (term + meaning on a card, three variants), `hook_question` (one large question, the 9:16
  frame0 cover rule, the "True or false?" pill), `key_terms` (2-4 cards in 16:9, rows in 9:16),
  `recap_retrieval` (prompt card, think ring, answer, takeaway tiles) and `worked_steps` (steps with their states,
  a blank typing in, the think ring, the formula card). The catalogue's `SlotSpec` gained `tabular_nums`, which it
  had not carried.
- **Data-fit parity (B2c review CG-2) extended.** A template's Python `fit` must refuse what its JS `drawsScene`
  refuses, or the Director picks it and the page silently draws `board_generic`. The demo render found exactly that
  on `worked_steps`, so `_fit_hook_question`, `_fit_worked_steps`, `_fit_key_terms`, `_fit_definition_card` and
  `_fit_recap_retrieval` carry the same side-panel guards their pages do. `_fit_worked_steps` also accepts the one
  top line the page draws (`heading | callout_info | paragraph`), because the generator writes a worked example's
  setup as a `callout_info`.
- **Review fixes (B2-demo review, 2026-10-15).** The §4.4 `example` row and `_fit_worked_steps` now ask one shared
  question (`_worked_shape`): while only the fit was widened, every shape the row did not claim scored
  `min(best, data) = 0` and `worked_steps` was unreachable for a generated worked example (D1). `fake_content`
  therefore speaks an example's closing takeaway instead of writing it to the board, which is the example scene's
  only item of a kind `worked_steps` does not draw. `recap_retrieval` refuses a scene with a side panel on both
  sides (D5: it plans no media box, so the panel's picture was fetched by no one and vanished). `direct.variant_for`
  mirrors `definition_card.js variantOf` exactly, which means `centred` until B2e writes `look.icons` (D7: a stored
  `card_icon_right` the page draws as `centred` would fit text to a slot 324 px too wide). In `key_terms` the 9:16
  icon is a square of the term's own fitted size on the term's line (D2: it was ~16 px on a line of its own,
  because an `em` on the icon resolved against the inherited base size and the term was block-level), and its icons
  are all-or-nothing like `recap_retrieval`'s and `list_build`'s (D6). The `chapter_bumper` numeral is a tint of the
  block ink with no `-webkit-text-stroke` (D4): in HTML that stroke is painted over the fill, so the internal joins
  of a glyph whose outline self-overlaps - the "1" of "01" in the display face - were drawn too and the numeral
  read as a grid of boxes at 320 px. §4.3's "outline numeral" is a deviation from here on. The owner's contact
  sheet is built from the captures instead of a hard-coded list of B2c's three pilots (D3), so all nine implemented
  templates are in it: 304 frames, re-captured after these fixes.
- **The demo.** The showcase lecture (`tests/core/fixtures/showcase_ohms_law.json`) was built four times (16:9 and
  9:16, one dark and one light R1 family) and rendered to MP4 by the real engine; the files, the per-scene template
  table and the reasons the remaining scenes still fall back are in `final_videos/demo_b2/README.md` (git-ignored).
  Reference frames for every implemented template, both formats, dark and light, are
  `docs/plans/b2/demo_frames/index.html` (`python -m tests.compose.dynamic.make_reference_frames`);
  `docs/plans/b2/b2c_frames/` keeps the earlier B2c-only sheet.
- **A generated lecture is now Dynamic at all.** `compose/dynamic/authoring.py` writes a default
  `Screenplay.presentation` for a newly generated lecture (`pipeline/script.py` `_default_presentation`, which the
  real generation calls with the job's settings). Before it, a presentation could reach a lecture only through an edit
  or an import, so a freshly generated lecture was always classic whatever the flag said. Every value is a released R1
  one; music is `off` and only the teacher's own narrator is cast (B3 owns the rest).
  - **The look rotates** on the lecture's own seed (owner decision, 2026-10-15), so two lectures do not look alike:
    the theme family (the four R1 families, two dark and two light), the personality (`crisp` / `soft`) and the hook
    kind, each drawn from its own SHA-256 stream so they never move together, and `look_epoch` re-rolls them for
    Fresh look / New colours while a plain rebuild never does (§6.8). The caption style and the cue style are
    *derived* from the chosen personality, which the design's table already pairs them with, so a 9:16 Crisp lecture
    takes `bold_outline` captions instead of the hardcoded `clean_pill` the first draft stored.
  - **`display_font` does not rotate yet:** `faces.resolver_fonts` maps a display id the B1 stage never drew to
    `outfit`, so storing `poppins` would change the stored look and nothing on screen. It waits for B2e's resolver
    and the live player's `stageFontRequests` preload (§4.6, §7.3). `check_order`, `greeting_id`, `intro_layout` and
    `reel_card_x` wait for `check` and `aadhi_intro`.
- **The mode is a switch in the Studio, not only an environment variable.** `models.AppSetting` (`app_settings`,
  migration `0007_app_settings`) stores an administrator's switches; `aadhi/runtime_flags.py` reads them on a 5 s
  per-process cache and falls back to the environment whenever there is no database to read, so a switch is never the
  reason something fails to run. `compose.presentation.dynamic_enabled` is the single place the stored switch and
  `DYNAMIC_ENABLED` are merged, so the API, the job workers and the compose layer always agree with no signature
  change anywhere. `GET` / `PATCH /api/admin/settings` (admins only) read and set it, and the Studio's Administration
  page carries a **Video mode** switch that also says which of the two sources is in force. It governs what happens
  next: a lecture already generated keeps the style it was made with, and a Dynamic lecture served while the switch is
  off is played and rendered as a classic one.
- **Gates on the committed tree** (`3094d0f`, the lead's run after the fix round): ruff, `tsc` and all ten
  generated-file checks clean; `npm test` 1,584 tests (1,583 pass, 1 skipped); the fast suite **5,595 passed,
  5 skipped, 0 failed** (12 min 26 s); the API suite 510 passed; the classic invariance goldens 60 passed, so classic
  output is unchanged; the slow Chromium gates `test_template_pages`, `test_template_fixtures` and
  `test_template_clips` 182 passed in the fix round; migration `0007` runs from scratch on an empty database. The
  first fast run after the migration failed three stale migration expectations (`test_alembic`'s pinned revision
  list, and two `test_review_migration` cases that ran `alembic check` with the database at `0006`, which is no
  longer the head the models describe); all three are fixed at the root, and `0007` has its own migration test.
- **Open.** The scenes the demo still draws with `board_generic` need the remaining R1 templates
  (`full_bleed_media` for `ai_video`, `animation_stage` for `simulation`, `check` for a quiz checkpoint,
  `aadhi_intro` / `outro_next` / `myth_fact` / `predict` / `code_walkthrough` / `chart_build` /
  `kinetic_statement`); §4.4's bounded-variety post-pass, the energy parameter and B2e's resolver work
  (`palette_slot`, accents and `accent_fade`, `refs`, `icons`, `text_fit`, display-face rotation) are not built;
  every size of the six new templates is a proposal awaiting the owner's review of the reference frames. A template
  that draws no medium now refuses such a scene in both its `fit` and its `drawsScene`, so the scene keeps its panel
  under `board_generic`; the parity is kept guard by guard, not by a test that walks every template.


**Status (B2 step 4, 2026-10-16): the templates the demo lecture actually fell back on.** The owner re-prioritised
step 4 from the original order (which would have converted one showcase scene) to the templates the fallback scenes
need: `full_bleed_media`, `animation_stage`, side-panel support in `worked_steps` / `key_terms`, `check` and
`predict`. Built by `docs/plans/b2/r1_workflow.js` (five parallel builders, one integrator, one review, one fix).
Fourteen of the 23 R1 templates now draw (the side-panel layer is not a template of its own). Record: `final_videos/demo_b2/README.md` (the per-scene table and every remaining
fallback's reason), `docs/plans/b2/demo_frames/index.html`.
- **Built.** `full_bleed_media` (forced for `ai_video`: the medium fills the frame, the words sit on an always-on
  scrim lower third, and `texts()` reports the colour the text really sits on - the scrim composited over the
  darkest and the lightest medium, the worse of the two, measured 8.2-9.6:1 for the label and 4.5-4.9:1 for the
  sub-line in all four families); `animation_stage` (forced for `simulation`); `check` (forced for a quiz, `mcq` in
  16:9); `predict` with the "Your prediction?" chip that carries into the next explaining scene; and the shared
  side-panel layer in `_shared.js`, which lets `worked_steps` and `key_terms` draw a scene that carries a panel
  instead of refusing it.
- **The demo.** In 16:9 the showcase lecture now draws **11 of its 16 scenes with a B2 template** (it was 4):
  `full_bleed_media` x3, `chapter_bumper` x2, `worked_steps` x2, `animation_stage`, `check`, `list_build` and
  `media_split`. In 9:16 it draws 6, because `mcq` is 16:9 only (R-06), a side panel is drawn in 16:9 only, and
  `media_split`'s reel phrase cannot hold these two scenes' text.
- **A blocker the review found: the 9:16 builds could not be rendered at all.** Dynamic preflight returned two
  blocking `text_overflow` findings on the portrait showcase, so no real portrait render of this lecture was
  possible; `media_split`'s reel phrase (two lines of 48-64 px in a 588 px column) was being handed a 9-, an 11-, a
  14- and a 22-word item, drawn anyway and reported, and the chart card beside a five-line body was squeezed to an
  unreadable 196x110 px stand-in reading "Dra" / "w". `templates.py MEDIA_SPLIT_PHRASE_WORDS` now keeps those scenes
  off the template in 9:16, so they fall back to `board_generic`, which holds the text, and both portrait builds
  render. **The demo renderer had also been skipping preflight entirely** (it called `render_motion` directly, unlike
  the production path): it now runs `run_preflight` and `raise_if_blocked`, with an explicit
  `--allow-preflight-blocks` escape hatch, so a blocked demo can no longer be rendered by accident.
- **Review and fix (deadline process).** One reviewer, one fix round. 5 findings: 1 blocker (above), 2 major
  (`full_bleed_media` and `animation_stage` each accepted a scene carrying a side panel they draw none of, silently
  dropping its content - both now refuse such a scene, the CG-2 rule again) and 2 minor (the demo renderer's missing
  preflight; a chunk-key guard test that had been weakened with a comment-stripping pass and a lookbehind, now a
  module-level pattern again). All five fixed.
- **Gates.** ruff, `tsc`, `git diff --check` and all ten generated-file checks clean; the fast suite 5,647 passed,
  5 skipped, 0 failed; `npm test` 1,732 tests (1,731 pass, 1 skipped); the real-Chromium gates
  `test_template_fixtures` + `test_template_clips` 65 passed (the 1x/3x/reverse frame-hash gate on all 60 fixtures,
  the four new templates included) and `test_template_pages` 181 passed (real preflight on every fixture with zero
  blocking and zero `type_floor` findings); `tests/invariance` green, so classic output is unchanged.
- **Open.** `s2`, `s7`, `s15`, `s16`: no R1 template claims their board shape. `s5` carries two definitions plus a
  `misconception`, which is `myth_fact`'s data, so whether `key_terms` may carry a misconception card is an owner
  decision. A `graph` / `chart` / `model_3d` panel is drawn as its authored title on the card surface, not as the
  plot: **the Dynamic engine still has no chart path** (`chart_build`, `function_plot`, `model_spin` are unbuilt), so
  s11's graph is a card reading "I = V / R (straight line!)" beside the steps. `mcq` in 9:16 and a reel side panel
  are owner decisions on the reel layout. Still unbuilt: `myth_fact`, `aadhi_intro`, `outro_next`,
  `code_walkthrough`, `chart_build`, `kinetic_statement`, `aadhi_recap`, `thumbnail`, `interactive_poster` - and
  `aadhi_intro` is the one that would put the Aadhi presenter back into a Dynamic video, which no template does
  today.

**Status (B2 step 5, 2026-10-17): the owner's four decisions, seven more templates, engine generation 5.** Built by
`docs/plans/b2/step5_workflow.js` (seven parallel builders, one integrator). **Twenty-one of the 23 R1 templates now
draw** (unbuilt: `thumbnail`, `interactive_poster`). Record: `final_videos/demo_b2/README.md` (the per-scene table and
every remaining fallback's reason), `docs/plans/b2/demo_frames/index.html`, the deviation register (plan §D, 37-44).
- **Built.** `aadhi_intro` (the four 16:9 layouts and the 9:16 card, the presenter frame-extraction cache under
  `RENDER_WORK_DIR/derivatives/aadhi`, the brand wipe into the next scene's colour) and `aadhi_recap`
  (`dynamic/presenter.py`, `web/js/dynamic/presenter.js`); `chart_build` with a reusable chart engine (`_charts.js`,
  `charts.py`: Chart.js draws marks and gridlines only, every label is page text); `myth_fact`; `outro_next`;
  `code_walkthrough`; `kinetic_statement`; and the reel adaptations in `check`, `key_terms`, `worked_steps` and
  `_shared.js` (`reels.py`).
- **Wired in.** The timeline builder synthesises Aadhi's intro for every Dynamic video (and, in 16:9, his recap card
  before the lecture's retrieval recap when `Presentation.aadhi_recap`; `DYNAMIC_AADHI_INTRO=false` turns both off); the
  director forces the presenter templates on those scenes, tries §4.4's positional "Last scene" row first (so a
  closing takeaway ends on `outro_next`), gives the graph row `chart_build` while `function_plot` is unbuilt, and
  rewrites a 9:16 panelled scene's events for its panel beat; the timeline gives that scene a narration lead, holds an
  `end_screen` outro to 15 s and writes `refs.next` from a caller's `next_title` only; the resolver stores
  `code.surface` on `code_walkthrough`'s scenes; `events.py` reveals a chart's bar or point on the word that names it
  and walks a code hold line by line; `cues.js` colours a look_here outline on the dark editor from `content_alt`
  (`TemplateModule.cueMode`); chart and graph panels are plots in every template that draws panels through
  `_shared.js` (`job.py` stops warning for those); `QuizTiming.option_misconception_ids` (Dynamic builds only).
  `ENGINE_VERSION` is 5 (presentation.py, engine.js and every pin), so stored Dynamic timelines rebuild when served.
- **Found by the integration and fixed at the root: the reels decision blocked the 9:16 showcase.** With a reel quiz
  drawn as a two-option `check` and a reel side panel as `worked_steps`' own beat, the first 9:16 render was refused
  by preflight: s9's 23-word question and 15-word explanation and s11 / s12's 17- and 13-word headings do not fit the
  reel's slots at their minimum sizes (the F3 lesson again: the fits took the shape, not the text). The fits now take a
  9:16 scene only when its text fits (`templates.py REEL_WRAP`: a greedy word wrap at per-slot capacities measured with
  textfit, never fewer lines than the certified fit on 1,900 samples per slot, pinned by a test), so those three fall
  back whole and the reel adaptations draw the scenes whose text a reel holds.
- **The demo.** In 16:9 the showcase lecture opens with Aadhi and draws **12 of its 16 scenes with a B2 template**,
  plus Aadhi's intro: `s5` is `myth_fact` (owner decision 3) and `s11`'s graph is a real plot. In 9:16 it draws 6
  plus the intro. All four builds pass preflight with zero findings. (The integration's 16:9 builds also showed the
  recap card before `s3`; the review's R5-1 below removed it.)
- **Review fixes (step-5 review, 2026-10-17), each with a test that fails without it.** (R5-1) The 16:9 recap card
  went before the *first* recap scene, in the showcase `s3` "Previously in ..." (prior knowledge, a `list_build` list),
  so Aadhi asked "Quick check: what are the 3 big ideas?" before anything was taught. It now goes before the
  lecture's retrieval recap: the last recap scene `recap_retrieval` draws (`direct.row_template`), never one ahead of
  the first chapter card; a lecture with none, the showcase included, has no card. (R5-2) A screenplay's own presenter
  scene with an empty board (the session title as the scene's title) fitted no template, so the video opened with
  `board_generic` and no Aadhi: `aadhi_intro` takes the scene's own title when the board has no heading (fit and page
  changed together, the parity gate extended to presenter scenes), and an intro is synthesised before a first
  presenter scene the template does not draw (a teacher's lock on another template). (R5-3) The render's
  "cannot be rendered yet (scene N)" message counted timeline positions, one or two past the editor's numbers once the
  intro (and recap) are in; it names the editor's scene and calls a synthesised appearance "Aadhi's intro" /
  "Aadhi's recap" (with "shorten the session title" as the action for the intro), and the Studio no longer numbers
  such an item "Scene 1" (`web/js/studio/lib/sceneName.js`). (R5-4) A lecture opening with its own title scene showed
  the session title twice in a row; the intro then names the unit (else the subject) and the chip leaves that part
  out (reading recorded in `presenter.py`: drawing that scene as the presenter scene would put the narrator's voice
  and an unbounded line on Aadhi, against §5.5 and R-13). (R5-5) The 9:16 intro cut the session title at 32
  characters ("Ohm's Law - The Rule That Runs…") although the headline holds it: the title is set whole in both
  formats, and one that does not fit is reported, so preflight asks for a shorter one.
- **Gates (integration, 2026-10-17; no review round yet).** ruff, `tsc`, `git diff --check` and all ten
  generated-file checks clean; the fast suite 5,820 passed, 5 skipped, 0 failed; `npm test` 1,934 tests (1,933 pass,
  1 skipped); real Chromium: `test_template_fixtures` 95 passed and 1 strict `xfail` (the 1x/3x/reverse frame-hash
  gate on all 96 fixtures; the xfail is the stage defect below), `test_template_pages` 289 passed (real preflight on
  every fixture as served and with captions burned: zero blocking and zero `type_floor` findings),
  `test_template_clips` 5, `test_presenter_pages` 8 (Aadhi from the real clip's cached crop, order-independent, drawn
  1:1), the stage set (`test_board_real_page`, `test_stage_layout`, `test_live_css`, `test_stage_fonts`,
  `test_preflight_stage`) 35; the classic invariance differential against `8561a44` 16 passed, so classic output is
  unchanged. The 378 earlier reference frames came out byte-identical; the contact sheet gained 169 frames of the new
  templates (547).
- **Open (owner).** (1) **A stage determinism defect** the `outro_next.landscape` fixture reproduces: after a
  crossfade, a translucent burned caption pill over the next scene blends one colour level differently by seek
  history (Chromium keeps the layers composited only when the page played through the crossfade). The measured fix,
  `will-change: opacity` on `.dyn-scene`, makes every order agree but changes 7,314 of 26,112 frames in 67 of the 96
  template fixtures, so it was not applied; the fixture's gate is a strict `xfail` naming it. (2) `media_split` and
  `board_generic` still draw a chart or graph panel's title, not the plot (`job.py` warns): wiring the chart engine
  there needs a measured camera push over a canvas, and `board_generic` must keep B1 timelines' frames (an
  engine-generation gate). (3) The reel's slots cannot hold the showcase's s9 / s11 / s12 text: a reel-specific text
  (B4's planner) or larger reel slots. (4) Aadhi is silent until the Aadhi voice and the greeting / sign-on pools
  (B3 / B4); the logo bug and the sonic logo are not drawn; a 9:16 Aadhi recap needs B4's per-format option; the
  teacher's last-used intro layout needs the history (§5.3). (5) `refs.next` has no source until B4's series. (6)
  Every size, limit and timing of the seven templates is a B2 proposal pending the owner's review of the reference
  frames (plan §D 37-44); pie, doughnut and radar charts are title cards; `kinetic_statement`'s caption blanking is
  B2d's. (7) Two readings from the review fixes: the intro names the unit when the lecture's first scene already shows
  the session title (R5-4), and a session title the intro's slot cannot hold now blocks the render until it is
  shortened, where it used to be cut (R5-5); a lint before TTS (§3.3 `portrait.hook_overflow`) would catch it earlier.

#### B3. Audio system (in parallel with B1–B2)

**Scope**
- Persona registry with `speaker_id` and accents; `history.py`; `casting.py` (per-lesson rotation, inheritance on regenerate, accent policy, course pins); calibration stored in `Presentation`.
- Providers: the Edge catalogue extension; **Azure** (if Q8 = Azure; SDK or REST decided on licence and Linux dependencies); OpenAI `gpt-4o-mini-tts`; ElevenLabs v4 with personal and server keys; the Gemini adapter's style and pause-tag shape and full voice list; per-sentence synthesis and `words_exact`.
- Speaker gains baked into `scene_audio`; public `voice_sample` assets.
- Music: `providers/music/` (`procedural` with numpy + scipy + `wave`, `upload`, `lyria` opt-in, `fake`); `pipeline/music.py` with intake analysis and fitting; `compose/dynamic/mix.py` (modes, levels, envelope, master chain, preflight encode); `sounds.py` limited to the R-49 set.
- Live-player audio (`<audio>` → `MediaElementAudioSourceNode` → `GainNode`; SFX buffers; Off/Low/Normal).
- Keys and money: `CREDENTIAL_PROVIDERS` += `elevenlabs`, `azure`; the Studio keys page; price rows; budget pre-checks; usage rows for calibration and samples. `GC_ASSET_KINDS` += `music`, `voice_sample`, `calibration`. Fakes (§6.12).

**Contracts frozen:** `VoicePersona`, `VoiceCast`/`VoiceCalibration`, `MusicChoice`, `MusicPlan`, `MusicAsset`; the history rule; the `music` usage operation; the new credential providers.

**Tests:** casting determinism; speaker-level exclusion (the two Neerja variants count once); accent policy (≥ 2 of every 3 local); regenerate inherits the presentation and Fresh look re-rolls it; pins; calibration maths; baked gains change the scene-audio key but not beat keys; envelope breakpoints as goldens; think-time level never above the under-speech level; mix within ±1 LU on the fixtures; music under speech ≥ 20 LU with ≤ 3 LU variation; music fitting (a 10 s insert leaves no gap; equal-power loop crossfades); Lyria lyrics rejection; procedural determinism per seed and ≤ 20 s for 5 minutes; live player and MP4 use the same breakpoints; key resolution for the new providers; music pre-checked against the budget on the server key; unknown operations charged.

**Acceptance**
- Two consecutive rendered lessons by one teacher differ in narrator speaker and music; regenerating one keeps both.
- All audio preflight checks pass on 3 fixtures.
- The owner signs off a listening checklist (voices clear; music never masks speech; no pumping; no clicks; levels equal across voices) and records the **blind listening test** of the procedural bed against a reference track [RV T-7].

**Estimate:** 3–4 weeks.

#### B4. First release: Dynamic lesson and reel end to end

**Scope**
- **Generation:** `GenerationOptions` with the unused-field validator; `dynamic_plan.md`, `dynamic_scene.md` and the `style.md` Dynamic section (with `PROMPT_VERSION` bumps); `gen_models` and `canonicalize` for the new kinds, `sequence`, question blanks and `level`, `answers`, `speaker`, `icon_hint`, `visual_intent` and `colour_is_content`; `scope.py`, the scope endpoint and series creation; the rotation inputs (hook kind, check order, greeting, intro layout, card position); per-format `plan_rules`; `dynamic_lint.py` with audiences; critic additions; classic fallbacks; `fit.py`; the extended fake writer; the eval metrics.
- **Reels:** portrait layouts for every R1 template; the Learning and Social profiles; platform-safe with the fit step; **Make a reel** and **Also make a reel** with the explicit parent hand-over (§6.10); cover export.
- **Studio:** the two tabs; the Dynamic form with exactly the fields of §9.2; destination; the course picker; the scope and series step; Dynamic plan review; the render dialog with preflight fixes; the export bundle (both versions, `.srt`, `.vtt`, chapters, thumbnail, description draft, `credits.txt`, "Which file goes where"); previews (the look-candidates endpoint, voice samples, music); teacher defaults (`users.preferences` migration); `site_settings` (migration: destination, Aadhi voice, AI-music default); live player (checkpoints, prediction input, reduced motion, music control); the safe-zone overlay.
- **Docs** updated.

**Contracts frozen:** the Dynamic `GenerationOptions`; the scope endpoint and series settings shape; lint codes and audiences; export file names; the look-candidates endpoint.

**Tests:** validators and canonicalisation; every lint code; `plan_rules` budgets and order rules (predict before its answer, checks after explanations); the scope estimate on the fixtures; series creation; the fit step; an **offline end-to-end run** with fake providers that generates and renders a Dynamic lesson and a reel with zero error-level lints; evals on the 3 fixtures; the form never sends unused fields; regenerate keeps the presentation; classic invariance.

**Acceptance**
- The 3 fixtures in both formats render with: zero error-level lints; preflight passing without manual fixes; generated runtime in band (lessons 4:30–5:30, reels 3:00–4:00, platform-safe container < 179.5 s); Aadhi only in the intro and recap within R-12; distinct templates ≥ min(5, classes present); think times at or above their minimums; SFX within R-49; music under speech ≥ 20 LU.
- A 15-minute SME source produces a scope proposal, and the series path creates parts whose concepts together cover the brief.
- The owner rates each video ≥ 4/5 on hook, clarity, visual variety, "never monotonous", legibility and audio.
- **Delivery check:** the owner uploads the files to YouTube (unlisted) and the college LMS and sends one over WhatsApp, and confirms captions appear where §9.5 says they will. This settles the player behaviour the reviews could not verify.
- Render time within the P1 numbers set in B1.

**Estimate:** 4–6 weeks.

#### B5. Editor I: script edits, cuts and look editing

**Scope:** the Script view (Edit sentence, Cut, Re-voice, insert entry points); cuts with the `words_exact` rules and the token map; the range-cut resolver (not on estimated timings); review-first suggestions; keep/remove for anchored items; timeline lanes with the default set and the runtime meter; the Look inspector (default view and More); New colours, New layouts and Shuffle this scene; Reset to automatic; the settled-lock save path with the `PUT` response; Restyle pictures with cost confirmation; changing voice or music from the editor (re-voice job, New music); `edit.cut_stale`; labels in `changes.js`.

**Contracts frozen:** the `PUT /screenplay` response with screenplay and ETag when looks are filled; `POST /api/versions/{vid}/look/colours` and `/look/layouts`; `WordRange` semantics.

**Tests:** splice quality measured numerically (no sample discontinuity above threshold at the join; RMS continuity); interpolated beats without silence fall back to re-voice; captions, sync cues and events stay consistent after cuts; re-anchoring cases; a voice change re-applies cuts; translation drops them; the range-cut order produces one undo step; editing one scene never changes another scene's settled look (property test); autosave and merge with server-filled looks; keyboard focus guards.

**Acceptance:** a mid-scene sentence cut re-renders without an audible click and captions match speech within 120 ms; an edit re-renders only the touched chunks (cache-hit ratio recorded); five teachers complete "fix a wrong sentence, cut two sentences and give one scene a new layout" unaided.

**Estimate:** 3–4 weeks.

#### B6. Editor II: external clips and recordings

**Scope:** the tus endpoint and client (CSRF header) and the proxy body-limit documentation; the `normalize_clip` job (proxy first; `ALL_JOB_KINDS`, compose `WORKER_KINDS`, docs; GC kinds); `ClipScene` (duration rule, `end_behavior`) through the `ai_video` blast-radius checklist [CB §6.2]; overlays (clamp, Extend the scene, reveal shortening, `keep_full_length`) and `TimedOverlay`; the two-step insert dialog with in-browser trim and the Adjust panel, "Replace this scene's picture", sentence snapping and `continues_from`; all sources of §8.4 (upload, library, Pexels and Pixabay with server keys, SSRF-safe links, screen, camera, own narration); `Permissions-Policy` for Studio pages; clip audio modes; clip captions (import, Transcribe); rights and credits; the `clip.*` lints; media extraction for clips; clips in the live player via proxies; the fake stock provider; enabling the `clip` and `overlays` features. The stage's overlay layer sets `aadhi/compose/motion/engine.py` `OVERLAYS_DRAWN = True`, so `video_media_keys` then requires overlay clips before the capture (B1 fetches none, since it draws no overlay); a `contain_blur` inserted clip needs a medium anchor in `MediaView` first (§2.5).

**Contracts frozen:** the tus routes; the `normalize_clip` payload; `ClipScene` and `Overlay` (enabled); the stock proxy endpoints.

**Tests:** the SSRF suite; refusals of YouTube and platform URLs; tus resume and the CSRF requirement; normaliser outputs (CFR 30, colour tags, rotation, tone mapping only for HDR input, output order); trims frame-exact; audio levels as in §8.6; overlay clamping and reveal shortening; the clip duration rule (freeze); stock rate-limit backoff; credits in the export; clip keys authorised end to end.

**Acceptance:** a rotated 60 s HEVC phone clip with its own audio is inserted after a sentence, trimmed and cut, in a lesson and in a reel; the renders are frame-exact, pass audio preflight and have no music gap; P6 ≤ 3 minutes; a screen recording and a camera recording go through the same path.

**Estimate:** 4–5 weeks.

#### B7. Variety and distribution expansion

**Scope:** the 17 W2 templates (including `diagram_build` and the `stat` hook kind); the other 8 families (12 in all); 4 more personalities and caption styles (6 each); the remaining backgrounds; iris, zoom-through and whip transitions; Phosphor, Fluent Emoji, Open Peeps and Open Doodles; more display faces with verified licences; ElevenLabs Music (opt-in, Starter-or-above keys only); Chirp 3 HD (the service-account credential type) and Sarvam (after a terms review); the Paper Shaders tier with the WebGL probe and CSS twins; the brand kit; Export as 2 parts; the Calm motion render option.

**Contracts frozen:** new template ids and classes; the `google_cloud` credential type; the brand-kit settings shape.

**Tests:** catalogue parity; the validator on 12 families; the `direct.py` property tests unchanged; ElevenLabs Music request shapes (negative styles, composition plan) and the free-tier block; the WebGL fallback; shader golden frames on SwiftShader; 2-part export (each part ≤ 2:58, chunks reused); the brand kit filters look candidates.

**Acceptance:** gallery sign-off for every template; a Tamil lesson has ≥ 4 rotating speakers with a Google or Sarvam key; shader scenes ≤ 70 ms per frame; a 2-part export is accepted as two Shorts (owner test).

**Estimate:** 6–8 weeks; splittable into templates and providers; can run in parallel with B5–B6.

#### B8. Aadhi asset upgrade (recommended; Q5)

**Scope:** alpha-matted centre and left clips (a licence-verified matting tool, human review); ≥ 4 native gestures including pointing up; native 1080×1920 clips; cutout layouts in both formats, with the 9:16 card pointing at the hook; `popup` regenerated or retired; the final sonic logo.

**Acceptance:** the owner approves at 1080p and on a phone; no edge halos on 3 families; the 9:16 intro gestures toward the hook.

**Estimate:** 1–2 weeks of engineering plus external asset production (2–4 weeks calendar).

#### B9. Measurement and tuning (continuous after B4)

**Scope:** analytics events and the metrics dashboard (§1.3); the **classroom pilot** as designed in L2 (within-student crossover, Classic / Dynamic / Dynamic-lean, ≥ 6 matched topics in a Latin square, pretest covariate, immediate and 7-day delayed tests with transfer items, a pre-registered analysis plan); the music question measured through Dynamic vs Dynamic-lean, plus an optional A/B of a silent, music-backed key-terms card (the one condition that helped in Phan 2023); the Social energy profile measured before any default change; static-run deduplication; throughput tuning; pacing constants calibrated against reference videos with the owner's permission [CP §12]; a quarterly platform-limit check [SF K]; threshold updates through evals.

**Acceptance:** the dashboard is populated; the pilot report reaches the owner; decisions on the reel profile and music defaults are recorded with their data.

**Estimate:** 2–3 weeks of engineering plus a 10–12-week pilot.

### 10.4 Risks and mitigations

| # | Risk | Likelihood / impact | Mitigation |
|---|---|---|---|
| 1 | The Gemini TTS model shuts down (17 Nov 2026) before a fix is deployed | High / High | H0 this week on the deployed branch; Edge fallback with a warning |
| 2 | Older builds silently drop new fields (rollback, mixed-version workers) | Medium / High | Inert fields in B0, `schema_version` 3 refusal, B0 as rollback floor, documented deploy order, transform-survival test |
| 3 | Cross-tenant asset use through new key fields | Low / High | `asset_uses` and `AssetRef` in B0, 422 and import-strip tests |
| 4 | Render time on production hardware exceeds the budget | Medium / Medium | B1 container benchmark sets P1; `veryfast` chunks; cgroup-aware W; host slots; 720p drafts; dedup (B9); visible estimate; Q7 |
| 5 | Non-determinism (stray clocks, fonts) causes flicker or seams | Medium / High | Source scan, timer guard, `Animation.setPlaybackRate(0)`, determinism and golden tests in Docker |
| 6 | "Eye-catching" becomes seductive-detail overload | Medium / High | Semantic classes, structure-only accents, rare SFX, frozen backgrounds in holds, the Learning reel profile, the Dynamic-lean pilot arm |
| 7 | A finished render is thrown away by QA | Low / Medium | Deterministic checks in preflight; container-level errors only, each with an automatic remedy |
| 8 | Teachers lose content or control (silent dropping, restyled scenes, new voices on regenerate) | Medium / High | Scope check and coverage report; settled looks; inherited presentation; review-first edits |
| 9 | Procedural music sounds cheap | High / Medium | Bed-only use; blind listening gate; Lyria opt-in; curated pack (Q9) |
| 10 | Text contrast or overflow fails over moving backgrounds and in Indic scripts | Medium / Medium | Content palette validated once; scrims; measured text fit; page overflow check with auto-shrink; Indic gallery fixtures |
| 11 | Entity colours confusable for colour-blind viewers | Medium / Low | Colour always redundant (symbols, direct labels, headers); computed colour-vision distances pinned by a test |
| 12 | Licences drift (Paper Shaders pre-1.0, AI music terms, Edge unofficial, fonts, Azure SDK) | Medium / Medium | Pinned versions, licence metadata per asset, Azure for production, quarterly review, Appendix D items checked in their batches |
| 13 | Provider terms: 18+ users, apps used by under-18s, AI disclosure | Medium / High | Legal review (Q3), disclosure line, provider choice per deployment |
| 14 | Content ID blocks a reel | Low–Medium / High | Claim-free sources only, provenance recorded |
| 15 | The LLM ignores structure; eval baselines shift | Medium / Medium | `plan_rules` before scene writing, deterministic `direct.py`, evals re-run each batch |
| 16 | Schema blast radius (`clip` touches ≈ 34 files; new kinds) | Medium / Medium | Checklist, classic fallbacks, `exclude_if` defaults, invariance harness |
| 17 | Edits anchor wrongly when TTS words differ from caption tokens | Medium / Medium | Token map where `map_words` runs, `words_exact`, stale edits never applied |
| 18 | SSRF or malicious media | Low / High | Pinned-IP fetch on 443, egress proxy, local ffprobe, protocol whitelist, timeouts, CSRF on tus |
| 19 | The pilot cannot recruit enough students | Medium / Medium | Crossover design (≈ 126 students), several classes over a term; report effect sizes with intervals if under-powered (Q16) |
| 20 | Platform limits change again | High / Low | Versioned constants, quarterly check |
| 21 | ffmpeg differs between Debian 5.1 and the dev machine's 8.1 | Medium / Medium | Feature checks in the Docker test job |

### 10.5 Open questions for the owner

| # | Question | Recommendation |
|---|---|---|
| Q1 | Should reels default to **3:00–4:00** (your request) or **platform-safe ≤ 2:58**? Over 3:00 YouTube treats a vertical video as a regular video | Keep 3–4 min; platform-safe and "Export as 2 parts" are one click |
| Q2 | May the app use **AI music** by default when a key is available (Lyria ≤ $0.32 per lesson), or only when a teacher opts in? | Opt-in at first; decide after the B3 listening test |
| Q3 | **Legal review:** Gemini and Lyria terms (users 18+, apps "likely accessed by" under-18s) when some first-years may be 17; the AI-disclosure wording | Get a lawyer's view before school-wide rollout |
| Q4 | **Aadhi's voice:** a fixed brand voice (which one?) or the rotating narrator? | A fixed brand voice |
| Q5 | Approve never using `popup` and `right` in Dynamic videos, and fund **B8** (matted and native vertical clips with pointing gestures)? | Yes, and start asset production now |
| Q6 | Create free **Pexels** and **Pixabay** API keys and accept their "Provided by …" display? | Yes |
| Q7 | **Production render hardware** for the render container and the acceptable time per video | ≥ 8 CPUs and ≥ 10 GB for the render worker; confirm after the B1 benchmark |
| Q8 | **Edge** (free, unofficial) or **Azure** (licensed, ≈ $0.07 per lesson, 0.5 M chars/month free) in production? This decides B3's scope | Azure in production; Edge for development and demos |
| Q9 | Buy or supply a **curated, claim-free music pack**? It is the cheapest way for exposed music to sound good | Worth considering after the B3 listening test |
| Q10 | **Where do students mainly watch?** (WhatsApp/Drive/classroom, LMS, YouTube, our lesson page). It sets the default destination: captions burned or not, −14 or −16 LUFS | Tell us; until then the default is WhatsApp/Drive/classroom (burned captions) |
| Q11 | **Branding:** logo on the intro corner and outro only, or a watermark throughout? | Intro and outro only; brand kit in B7 |
| Q12 | Keep the research reports in the repo under `docs/research/`? | Yes |
| Q13 | Lessons keep **one background per video** (learning evidence: event boundaries split one argument) and change the structure colour each chapter; "Lively" changes the background per chapter too. Is that enough change for you? | Yes; review it in the B2 gallery |
| Q14 | Reels default to the **Learning** profile (155 wpm, quieter music, calmer pacing); **Social energy** is opt-in until the pilot measures it. Agree? | Yes |
| Q15 | When a document is too long for one lesson, should the Studio recommend **a series** or **focusing**? | Recommend a series |
| Q16 | Can you run the **pilot** with ≈ 126 students over ≥ 6 matched topics (within one term)? | Yes if possible; otherwise a smaller pilot reported as exploratory |
| Q17 | Until B8, the 9:16 Aadhi card cannot gesture toward the hook above it. Accept for the first release? | Yes |

---

## Appendix A. Where the research disagreed, and what this design chose

| # | Topic | What the reports said | Decision | Why |
|---|---|---|---|---|
| 1 | Render architecture | MR: full-frame JPEG capture of every frame. CB: hybrid of states, motion windows and ffmpeg | Full frame; static-run deduplication later (B9) | One compositor equals the live player; media can move, take masks and carry annotations (§7.2) |
| 2 | Motion library | MR: anime.js 4.5 (MIT). CB, CP: in-house | In-house `web/js/motion/`; anime.js as fallback | Determinism by construction, no CSP change (§7.13) |
| 3 | Speech rate | LS 150–165 / 160–175 (effective). SF 150–170 / 165–185. AU 155–165 / 170–185. CP 160–180 / 170–190. RV: one definition; reels as slow as lessons by default | **155 speech wpm for lessons and Learning reels; 170 for Social reels**; ceiling 185; dense −10%; Indic −10 (§1.4) | L2 audience; speeding up costs learning (g = −0.41); fast pacing hurts verbal encoding; energy from delivery, not rate |
| 4 | Music under speech | LS −22 LU (floor −18). AU −20 / −17. CP −22 to −26 dB. SF, ED ≥ 20 dB. RV: −22 learning, −21 social, floor −20 | **−22 (lessons, Learning reels), −21 (Social), floor −20; off in dense lessons' `gaps_bookends` mode** | WCAG 1.4.7 20 dB rationale; music ducked under narration was neutral at best (Phan 2023) |
| 5 | Gap before music rises | LS 1.2 s. AU 0.8 s | **2.0 s / 1.5 s**, and only for exposable sources | Fewer swells between sentences = less changing-state sound |
| 6 | Master loudness | SF −14. AU −16 / −14. ED −16 | **By destination**: −14 (WhatsApp/Drive, YouTube), −16 (LMS, lesson page) | Platforms normalise at −14; classroom and LMS playback benefit from headroom (Q10) |
| 7 | Longest static stretch | LS 8 / 5 s. SF 8 / 4. CP 6 / 3 | **8 s (lessons), 6 s (Learning reels), 4 s (Social)**, holds up to 12 / 8 s; only anchored events count | Middle of the evidence; decorative motion no longer satisfies the rule |
| 8 | How often colour changes | LS: segment boundaries only. MR: every scene. CP: background tone every 2 scenes. RV: accents off content; one background per lesson | **Structure accent per chapter or act; background preset per lesson (16:9); tone variant per scene; content colours fixed** | Consistent colour mapping; event boundaries; variety still lands every 60–90 s |
| 9 | Captions in 16:9 | LS: on by default. SF: don't burn into a YouTube master. RV: soft tracks are invisible on YouTube, LMS and messaging | **By destination** (§2.6); both versions on request | Captions must be visible where students actually watch |
| 10 | Caption emphasis | SF, CP: accent on the active word. LS: key terms only. RV: no active word, phrase chunks, sentence case | **Phrase chunks, sentence case, no active-word emphasis by default; key terms in `mark` on first two mentions** | Word-level emphasis pulls gaze from the visual; colour stays a meaningful cue |
| 11 | Aadhi in 9:16 | SF: 840×472 card. CB: band. MR: 720² crop. CP: top half | **720² crop of the centre clip → 744² card (1.03×) below a 4-line hook band** | Full body, sharp, frame-0 headline readable as a cover |
| 12 | Aadhi screen time | LS ≤ 20 / ≤ 10 s. SF ≤ 20 s. CP 10–20 / 8–14 s. Owner: 5–10 s per appearance | **5–8 s per appearance, ≤ 16 s in total in both formats** | Owner's specification; the 8 s clip length is the upper bound |
| 13 | Default reel length | Owner: 3–4 min. SF, CP: Shorts-safe | **3–4 min + one-click platform-safe + Export as 2 parts** | The owner's explicit request (Q1) |
| 14 | GOP | MR `-g 60`. SF closed GOP of 15 | **Closed GOP of 15** | YouTube's specification |
| 15 | AAC bitrate | SF 384 kbps. AU ≥ 160–192 | **384 kbps** | YouTube's recommendation |
| 16 | Voice exclusion window | LS: previous video. AU, CP: last 3. CB: last 2. RV: count speakers; keep accents familiar | **Last min(3, speakers − 1) speakers, inside the accent policy** | Works for small pools; familiar accents for L2 listeners |
| 17 | Music repeat window | LS 5. AU 10. CP 5. CB 3 | **Library tracks: last 10 lessons; (mood, key, BPM): last 3; generated music unique** | Strictest rule where it costs nothing |
| 18 | 9:16 safe box | SF x 120–888. CP x 96–888 | **Text x 192–888 above y 840; x 120–780 below; caption band x 120–780** | Intersection of the strictest zones; measured text fit |
| 19 | 16:9 body size | SF ≥ 54 px. CP 40–48. MR ≥ 36 | **Body 52, labels ≥ 40, never < 36** | Phone viewing vs board density |
| 20 | Narration normalisation | CB: per beat. AU: per voice | **One static gain per speaker per video, baked into the scene audio** | Short-clip loudness is unstable; player and MP4 must match |
| 21 | Clip upload cap | ED 1 GB / 15 min. CB 500 MB / 10 min | **1 GB / 15 min**, configurable | Phone footage is 100–200 MB per minute |
| 22 | 16:9 chapter length | LS 60–90 s. SF 40–75. CP 45–90 | **60–90 s** (45–100 allowed) | Segmenting evidence |
| 23 | Who chooses the template | CP: the planner. CB: deterministic `direct.py`. RV: only within the content's semantic class | **LLM gives structure and `visual_intent`; `direct.py` chooses within the class** | Variety is a global constraint; meaning must not change |
| 24 | Backgrounds | CB: pre-rendered loops. MR: in the page | **Procedural, in the page; frozen during holds** | One compositor; no competition with dense content |
| 25 | WebGL shaders | MR: Paper Shaders for backgrounds | **Bookends only, in B7; CSS by default** | CPU cost; pre-1.0 library |
| 26 | Kinetic captions | CB, SF: libass ASS karaoke | **In the page** | Frames are captured anyway; exact parity |
| 27 | Ducking | Existing `sidechaincompress`. AU: deterministic envelope | **Envelope from word timings** | Measured 24–26 LU pumping today |
| 28 | Pre-training | LS: exactly 3 terms. RV: 2–4, used later; recap relational | **2–4 component terms, each used in ≥ 2 later scenes** | The effect was measured as separate pre-training, not as a card; terms must earn their place |
| 29 | SFX density | LS ≤ 1 per 5 s. Revision 1: ≤ 1 per 4 s / 2 s. RV: bumpers, reveals, countdown end | **R-49: three structural sounds; accents ≤ 1 per 20 s / 8 s; none in dense scenes** | Moreno & Mayer 2000; Phan 2023 |
| 30 | Think time | Revision 1: 3–5 s / 2–3 s. RV: reading-based | **§1.4 formula, ≥ 5 s / 4 s, ≥ 6 s for free recall of 3 ideas** | Retrieval effort is the active ingredient |

---

## Appendix B. Primary sources

Grades and verification follow the originating report. Access date: 2026-10-09. Sources added in revision 2 that were cited from a reviewer's memory are marked **(M)** and listed again in Appendix D.

### Learning science and instructional video

- Noetel, M. et al. (2022). Multimedia design for learning: an overview of reviews with meta-meta-analysis. *Review of Educational Research* 92(3), 413–454. https://doi.org/10.3102/00346543211052329 [LS]
- Mayer, R. E. Principles table, adapted from *Multimedia Learning* (3rd ed.). https://edtechbooks.org/docx/10349 ; Mayer (2024), *Educ. Psychol. Rev.* https://doi.org/10.1007/s10648-023-09842-1 [LS]
- Mayer, R. E., Mathias, A., & Wetzell, K. (2002). Fostering understanding of multimedia messages through pre-training. *J. Exp. Psychol.: Applied* 8(3). [RV L-10; d via Phan 2023, p. 4]
- Guo, P. J., Kim, J., & Rubin, R. (2014). How video production affects student engagement. *ACM L@S*. https://up.csail.mit.edu/other-pubs/las2014-pguo-engagement.pdf [LS, SF, CP]
- Szpunar, K. K., Khan, N. Y., & Schacter, D. L. (2013). Interpolated memory tests reduce mind wandering. *PNAS* 110(16). https://doi.org/10.1073/pnas.1221764110 [LS, CP]
- Kestin, G., & Miller, K. (2022). Enhanced visuals and embedded questions. *Phys. Rev. PER* 18, 010148. [LS]
- Sundararajan, N., & Adesope, O. (2020). Seductive details meta-analysis. *Educ. Psychol. Rev.* 32(3). https://doi.org/10.1007/s10648-020-09522-4 [LS, CP]
- Castro-Alonso, J. C. et al. (2021). Pedagogical agents meta-analysis. *Educ. Psychol. Rev.* 33(3). [LS]; Beege, M. et al. (2023). Instructor presence meta-analyses. *Educ. Res. Rev.* https://www.sciencedirect.com/science/article/pii/S1747938X2300057X [LS]
- Brom, C., Stárková, T., & D'Mello, S. (2018). Emotional design meta-analysis. *Educ. Res. Rev.* 25. [CP]; Wong & Adesope (2021). *Educ. Psychol. Rev.* 33(2). [LS]
- Fiorella, L., & Mayer, R. E. (2016). Dynamic drawing in video lessons. *J. Educ. Psychol.* 108. [CP]
- Muller, D. A. (2008). Designing effective multimedia for physics education; *JCAL* 24(2). https://veritasium.com/publications [CP]
- Gernsbacher, M. A. (2015). Video captions benefit everyone. *PIBBS* 2(1). [SF, CP]
- Montero Perez, M., Van Den Noortgate, W., & Desmet, P. (2013). Captioned video for L2 listening and vocabulary acquisition: a meta-analysis. *System* 41(3). [LS; scope per RV L-7]
- Lang, A. et al. (2000). The effects of edits on arousal, attention, and memory. *J. Broadcasting & Electronic Media* 44(1). [LS, SF, CP]
- Lang, A., Potter, R. F., & Bolls, P. (1999). Something for nothing: is visual encoding automatic? *Media Psychology* 1(2). [SF §6.2]
- Chiossi, F. et al. (2023). Short-form videos degrade our capacity to retain intentions. *CHI 2023*. https://arxiv.org/abs/2302.03714 [LS]
- Huang, G., Du, Y., & Yang, H. (2025). Meta-analysis of acceleration on video learning. *Front. Psychol.* 16:1427609. https://pmc.ncbi.nlm.nih.gov/articles/PMC12412133/ [LS]
- Moreno, R., & Mayer, R. E. (2000). A coherence effect in multimedia learning: minimizing irrelevant sounds. *J. Educ. Psychol.* 92(1). https://tecfa.unige.ch/tecfa/teaching/methodo/Moreno_Mayer00.pdf [LS, AU; nature of the sounds (M)]
- Phan, V. T. (2023). Background music in instructional videos and retention. *J. Educators Online*. https://files.eric.ed.gov/fulltext/EJ1393962.pdf [LS; read in full by RV]
- Kämpfe, J. et al. (2011). Background music meta-analysis. *Psychology of Music* 39(4). https://doi.org/10.1177/0305735610376261 ; de la Mora Velasco, E. et al. (2023). *Psychology of Music* 51(6). [LS]
- Thompson, W. F., Schellenberg, E. G., & Letnic, A. K. (2012). Fast and loud background music disrupts reading comprehension. *Psychology of Music* 40(6). [LS, AU]
- Lehmann, J. A. M., & Seufert, T. (2017). The influence of background music on learning in the light of different theoretical perspectives and the role of working memory capacity. *Front. Psychol.* 8. [RV L-6]
- Gonzalez, M. F., & Aiello, J. R. (2019). More than meets the ear: investigating how music affects cognitive task performance. *J. Exp. Psychol.: Applied* 25(3). (M)
- Torcoli, M. et al. (2019). Preferred levels for background ducking. *JAES* 67(12). https://aes2.org/publications/elibrary-page/?id=20711 [AU]
- Leahy, W., & Sweller, J. (2011). Cognitive load theory, modality of presentation and the transient information effect. *Applied Cognitive Psychology* 25(6). (M); Wong, A., Leahy, W., Marcus, N., & Sweller, J. (2012). *Learning and Instruction* 22(6). (M)
- Crouch, C. H., Fagen, A. P., Callan, J. P., & Mazur, E. (2004). Classroom demonstrations: learning tools or entertainment? *Am. J. Phys.* 72(6). (M)
- Lewandowsky, S. et al. (2020). *The Debunking Handbook 2020*. (M)
- Swallow, K. M., Zacks, J. M., & Abrams, R. A. (2009). Event boundaries in perception affect memory encoding and updating. *J. Exp. Psychol.: General* 138(2). (M); DuBrow, S., & Davachi, L. (2013). *J. Exp. Psychol.: General* 142(4). (M)
- Rajendran, D. J., Duchowski, A. T., Orero, P., Martínez, J., & Romero-Fresco, P. (2013). Effects of text chunking on subtitling. *Perspectives* 21(1). (M)
- Major, R. C., Fitzmaurice, S. F., Bunta, F., & Balasubramanian, C. (2002). The effects of nonnative accents on listening comprehension. *TESOL Quarterly* 36(2). (M); Harding, L. (2012). Accent, listening assessment and the potential for a shared-L1 advantage. *Language Testing* 29(2). (M)
- Ozcelik, E., Karakus, T., Kursun, E., & Cagiltay, K. (2009). An eye-tracking study of how color coding affects multimedia learning. *Computers & Education* 53(2). (M)
- Huber, B. et al. (2019). B-Script: transcript-based B-roll video editing with recommendations. *CHI 2019*. https://arxiv.org/abs/1902.11216 [ED]
- ReelsEd (2025). arXiv 2509.05962. https://arxiv.org/html/2509.05962v1 [SF]

### Platforms, specifications and standards

- YouTube Help: three-minute Shorts https://support.google.com/youtube/answer/15424877 · upload encoding https://support.google.com/youtube/answer/1722171 · chapters https://support.google.com/youtube/answer/9884579 · end screens https://support.google.com/youtube/answer/6388789 · retention key moments https://support.google.com/youtube/answer/9314415 · download your own videos https://support.google.com/youtube/answer/56100 [SF, ED]
- Google Universal Video Ad Safe Zones (PDF). https://services.google.com/fh/files/misc/universalsafezones-youtube.pdf [SF]
- Meta Ads Guide: Instagram Reels video. https://www.facebook.com/business/ads-guide/update/video/instagram-reels [SF]
- LinkedIn Help: video specs. https://www.linkedin.com/help/linkedin/answer/a548372 [SF]
- Netflix English (USA) Timed Text Style Guide. https://partnerhelp.netflixstudios.com/hc/en-us/articles/217350977-English-USA-Timed-Text-Style-Guide [LS, SF, CP]
- W3C WCAG 2.2: 1.4.3 https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html · 1.4.7 https://www.w3.org/WAI/WCAG22/Understanding/low-or-no-background-audio.html · 2.3.1 https://www.w3.org/WAI/WCAG22/Understanding/three-flashes-or-below-threshold.html
- EBU R 128 s2, Loudness in Streaming (v3). https://tech.ebu.ch/docs/r/r128s2.pdf [SF]
- YouTube Terms of Service https://www.youtube.com/static?template=terms ; YouTube API Developer Policies https://developers.google.com/youtube/terms/developer-policies [ED]
- OWASP SSRF Prevention Cheat Sheet. https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html [ED]
- Machado, G. M., Oliveira, M. M., & Fernandes, L. A. F. (2009). A physiologically-based model for simulation of color vision deficiency. *IEEE TVCG* 15(6). [matrices used for §4.7]

### Audio providers and licences

- Gemini speech generation https://ai.google.dev/gemini-api/docs/speech-generation · pricing https://ai.google.dev/gemini-api/docs/pricing · deprecations https://ai.google.dev/gemini-api/docs/deprecations · terms https://ai.google.dev/gemini-api/terms · Lyria https://ai.google.dev/gemini-api/docs/music-generation [AU; deprecation date and prices re-checked by RV]
- OpenAI text-to-speech https://developers.openai.com/api/docs/guides/text-to-speech · pricing https://developers.openai.com/api/docs/pricing [AU]
- ElevenLabs models https://elevenlabs.io/docs/models · API pricing https://elevenlabs.io/pricing/api · with-timestamps https://elevenlabs.io/docs/api-reference/text-to-speech/convert-with-timestamps · Music compose https://elevenlabs.io/docs/api-reference/music/compose · Eleven Music v1 terms https://elevenlabs.io/eleven-music-v1-terms [AU; music tiers re-checked by RV]
- edge-tts https://github.com/rany2/edge-tts ; Azure Speech language support https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support?tabs=tts ; Chirp 3 HD https://docs.cloud.google.com/text-to-speech/docs/chirp3-hd ; Sarvam Bulbul https://docs.sarvam.ai/api-reference-docs/getting-started/models/bulbul [AU, RV]
- Pixabay terms https://pixabay.com/service/terms/ · Pixabay API https://pixabay.com/api/docs/ · Freesound API terms https://freesound.org/docs/api/terms_of_use.html · Jamendo API terms https://devportal.jamendo.com/api_terms_of_use · AudioCraft https://github.com/facebookresearch/audiocraft · pedalboard (GPL-3.0) https://github.com/spotify/pedalboard [AU]
- Pexels API https://www.pexels.com/api/documentation/ · Pexels licence https://www.pexels.com/license/ · Mixkit licence https://mixkit.co/license/ · Coverr API https://api.coverr.co/docs/start [ED]

### Rendering and libraries

- Chrome DevTools Protocol (Page, HeadlessExperimental, Animation, Emulation). https://raw.githubusercontent.com/ChromeDevTools/devtools-protocol/master/pdl/domains/Page.pdl [MR, CB]
- Playwright Clock https://playwright.dev/docs/clock (overrides `requestAnimationFrame` and timers; basis for withdrawing `page.clock`) ; release notes https://playwright.dev/python/docs/release-notes [MR, RV]
- HeyGen, "HTML to Video" (2026) https://www.heygen.com/research/html-to-video ; HyperFrames https://github.com/heygen-com/hyperframes [MR]
- Remotion licence https://raw.githubusercontent.com/remotion-dev/remotion/main/LICENSE.md ; pricing https://www.remotion.pro/license [MR, CB]
- GSAP Standard "No Charge" licence https://gsap.com/standard-license [MR, CP, CB]
- anime.js https://animejs.com/documentation/ [MR]
- Paper Shaders https://github.com/paper-design/shaders (LICENSE https://raw.githubusercontent.com/paper-design/shaders/main/LICENSE) ; lottie-web https://github.com/airbnb/lottie-web [MR, RV]
- Chromium SwiftShader fallback removal (blink-dev). https://groups.google.com/a/chromium.org/g/blink-dev/c/yhFguWS_3pM/m/oLrB5up_BwAJ [MR]
- FFmpeg filters https://ffmpeg.org/ffmpeg-filters.html ; FFmpeg formats (`movflags`) https://ffmpeg.org/ffmpeg-formats.html ; seeking https://trac.ffmpeg.org/wiki/Seeking [AU, ED, RV]
- tus resumable upload protocol https://www.tus.io/protocols/resumable-upload ; OpenTimelineIO https://opentimelineio.readthedocs.io/ [ED]

### Design systems, type and icons

- IBM Carbon motion https://carbondesignsystem.com/elements/motion/overview/ ; Material 3 motion tokens (Apache-2.0) [MR, CP]
- OKLCH / CSS Color 4 https://developer.chrome.com/docs/css-ui/access-colors-spaces ; Okabe–Ito palette (Wong 2011, *Nature Methods*) [CP]
- Atkinson Hyperlegible Next (OFL) https://github.com/googlefonts/atkinson-hyperlegible-next ; Anek (OFL) https://github.com/EkType/Anek [CP]
- Lucide, Tabler, Phosphor, Material Symbols, Fluent Emoji, Noto Emoji, Open Peeps, Open Doodles: licence files read in MR §6
- Competitor documentation (Synthesia, Pictory, Vyond, Canva, Descript, Kapwing, Submagic, Opus Clip, Lumen5, CapCut): see CP §13

---

## Appendix C. Critique resolution

Every finding of the three revision-2 reviews, in their order. **Accepted** means adopted as proposed. **Accepted with changes** means the problem is fixed by a different or narrower remedy, with the reason. **Partly rejected** means part of the proposal is declined, with the evidence. No finding is rejected outright.

### C.1 Pedagogy review (L)

| # | Finding | Verdict | Resolution | Where |
|---|---|---|---|---|
| L-1 | A 15-min source squeezed into 5:00 with no series or coverage report | Accepted | Scope check after `brief`; series / focus / condensed choice; `coverage.dropped` error; worked examples never thinned | §2.2, §6.4, §9.3, R-61 |
| L-2 | Variety engine changes meaning (pair→versus, bullets→checklist, definition→statement, media side) | Accepted | Semantic classes; swaps only within class; never representational → typography; distinct ≥ min(5, classes); media side changes only at chapter boundaries | §4.2, §4.4, R-20, R-28 |
| L-3 | Chapter accents collide with fixed hues; variable colours fail contrast | Accepted with changes | Accent colours structure only (bumpers, tint, pips, borders); current/past by weight and ink; a fixed content palette computed this session (3 entity slots, ≥ 30° from warn, ok and mark, contrast ≥ 4.5:1 on every background of its mode); `info` made neutral, which is what frees the blue band; validator checks all fixed hues and colour-vision distance. Three slots, not four: under the hue constraints the best four-colour set falls to 0.048 OKLab worst-case colour-vision distance | §4.7, R-52, R-53 |
| L-4 | Retrieval moments too short; Aadhi on screen and music rising during think time | Accepted | Think-time formula (≥ 5 s / 4 s; ≥ 6 s for 3-idea free recall); reels free recall or single answer; Aadhi exits before think time; music at the under-speech level or off, no riser. The "Pause now" line is said at the first check (from a varied pool), not at every check, so it does not become a refrain | §1.4, R-07, R-44, §3.3, §4.3 |
| L-5 | SFX too dense; Moreno & Mayer misread | Accepted | SFX only on bumpers, answer reveals and countdown end; accents ≤ 1 per 20 s / 8 s; none in dense scenes; removed pops, clicks, key ticks, thuds and the riser; "Sound effects: off"; the evidence statement corrected and marked (M) | §4.10, R-49 |
| L-6 | The reel stacks every risk factor | Accepted | Learning profile by default (155 wpm, −22 LU, ≤ 110 BPM, layer changes at act boundaries, accents ≤ 1 per 8 s, phrase captions); Social energy opt-in and measured in the pilot | §2.1, R-15, R-36, R-44, R-46 |
| L-7 | Kinetic captions are not the kind the evidence supports | Accepted with changes | Phrase chunks, sentence case, no pop, no active-word emphasis by default, key terms on first two mentions only, evidence label corrected. "One line ≤ 32 chars" does not fit the 660 px reel band at legible sizes (measured: ≈ 20 chars at 56 px), so a 32-char chunk may wrap to 2 measured lines | §2.6, §4.11, R-31, R-32, R-35 |
| L-8 | Text triple-redundant; no combined reading budget | Accepted with changes | Caption band blanked while on-screen text matches narration; those templates exempt from `text.verbatim`; `text.reading_budget` added. Captions repeat the narration at 155 wpm, so a 130 wpm combined budget could never pass; the combined budget uses the repo's existing 200 wpm reading rate and 130 wpm applies to text read without narration (quiz options, silent cards) | §2.6, §4.11, R-25, R-64 |
| L-9 | 16:9 soft captions invisible on YouTube, LMS, messaging | Accepted with changes | "Where will students watch?" sets burn-in, loudness and packaging; both versions on request without a second capture; "Which file goes where" note; B4 delivery check settles the unverified player behaviour | §2.6, §7.5, §9.5 |
| L-10 | Pre-training overclaimed; recap reduced to vocabulary | Accepted | 2–4 component terms each used in ≥ 2 later scenes; relational big ideas; ≥ 1 apply/transfer recap question; R-05 evidence relabelled | R-05, R-08, §4.3 |
| L-11 | Nine emphasis treatments contradict "one highlight style" | Accepted | Two cues (`cue.term`, `cue.look_here`) implemented once; templates may call only these; punch-zoom counts as `look_here`, ≤ 1 per 20 s | §4.8, R-26 |
| L-12 | Chapter boundaries as strong as possible split one argument | Accepted with changes | One background preset per lesson; the accent and bumper mark chapters; shared-element anchor when `builds_on`; reels change per act; paper interlude only at a boundary. Exceptions: a preset change is allowed at a `builds_on = false` chapter or with energy *Lively*, to honour the owner's "change colours often" (Q13) | §4.5, §4.3, R-18, R-47 |
| L-13 | 9:16 carousels make content disappear | Accepted | Persistent compact stacks (≥ 40 px) in `list_build`, `table_reveal` and `worked_steps` | §4.3 |
| L-14 | `predict_reveal` mixes prediction and retrieval | Accepted | Split into `predict` (before the explaining scene, answered by it; `predict.order`) and `check` (after) | §4.3, R-62, §6.5 |
| L-15 | Worked examples have no fading or self-explanation | Accepted | "Your turn" final step with a 4–6 s hold; a faded second example; `example.no_fade` | R-09, §4.3 |
| L-16 | A myth can open the video and stay unrefuted | Accepted | True/false hook without a MYTH stamp, refuted in the same chapter; fact card final and dominant; myths never in recap | R-63, §4.3 |
| L-17 | Image prompts and Manim colours recolour meaningful colour | Accepted | `colour_is_content`; no suffix for diagram/photo roles; Manim colours mapped to content roles, literal colours on a dark card when meaningful | §4.3, §4.7, §6.6 |
| L-18 | Music presented as a retention feature | Accepted | Music described as a mood and variety layer, at best neutral under speech; `gaps_bookends` default for dense lessons; music measured in the pilot (Dynamic-lean arm) with an optional silent key-terms card A/B | §5.8, §5.9, §10.3 B9 |
| L-19 | The pilot cannot be run as written | Accepted with changes | Within-student crossover, pretest, 0.25 SD margin, ≈ 126 students, Dynamic-lean arm, delayed transfer items. With three arms the design needs ≥ 6 matched topics (two per arm) in a Latin square rather than 4 | §1.3 L2, §10.3 B9, Q16 |
| L-20 | Voice rotation brings unfamiliar accents | Accepted | Accent policy from the audience locale; for en-IN ≥ 2 of every 3 lessons local (stricter than the proposed 50%), `local_only` once Azure gives ≥ 8 local speakers; rotation inside the accent; accent filter | §5.3, R-39 |
| L-21 | Aadhi does not always signal; R-03 vs R-13 conflict | Accepted with changes | 16:9 recap uses the left clip with the prompt on his gesture side; every 16:9 intro layout keeps the gesture toward the title; 9:16 hook first (≤ 8 words, ends by 3.0 s by word timings). No existing clip can gesture upward in 9:16; that part waits for B8 (Q17) | §3.3, R-03, R-13 |
| L-22 | The brand sting plays over the hook | Accepted | A ≤ 1 s sonic logo that ends before speech in 16:9; none before speech in 9:16 (it plays on the outro) | §3.3, §5.11 |
| L-23 | Backgrounds keep moving during dense holds | Accepted | Frozen and 30% desaturated during holds and in formula, worked-example and code scenes | §4.5, R-55 |
| L-24 | Pacing lint satisfied by meaningless motion | Accepted | Only anchored events count; dense beats get a 4–5 s median band | §1.4, R-15, §6.7 (`VisualEvent.anchored`) |
| L-25 | First insight after 0:30 | Accepted | Roadmap folded into the first bumper; key terms by 0:19; first insight by 0:30 | §2.3, R-03 |
| L-26 | Platform-safe reel has one checkpoint | Accepted | Both checks kept in platform-safe | §2.3, R-06 |
| L-27 | Reel scope stated two ways | Accepted | One concept per reel; each segment one step of it | §2.3, §6.5, §6.10 |
| L-28 | Speech rate has no single definition | Accepted | One "speech wpm" definition used everywhere; relation to effective rate stated | §1.4, R-36 |

### C.2 Engineering review (E)

| # | Finding | Verdict | Resolution | Where |
|---|---|---|---|---|
| E-1 | Gemini fix on the wrong branch | Accepted | Standalone hotfix H0 this week on `main` (`server.py:758`, `test_gemini.py`) and v2 (`config.py:180`); `google-genai>=2.26` floor (2.25 for 3.8 TTS; 2.26 is the version Omni was verified against) | §5.1, §10.3 H0 |
| E-2 | New asset-key fields bypass per-project authorisation | Accepted | `asset_uses` extended for clip, overlay, music and narration keys; `AssetRef` rows; 422 and import-strip tests (B0) | §6.7, §8.10 |
| E-3 | Dynamic and edit data lost silently on older builds and transforms | Accepted | All future fields inert in B0 with write gates; `schema_version` 3 and `newer_document` refusal; B0 as rollback floor; transform-survival property test; deploy order | §6.7, §10.3 B0 |
| E-4 | Render-worker limits and W computed wrongly | Accepted | cgroup-aware W; `motion` compose profile (≥ 8 CPUs, ≥ 10 GB, 2 GB shm); host-wide `flock` slots; `render_concurrency` description corrected; mix memory budgeted; P1 re-measured in B1 | §7.6, §7.10 |
| E-5 | The benchmark is not the specified pipeline | Accepted | Benchmark caveats stated; B1 acceptance benchmark in the container through the production path on four real templates; `veryfast` CRF 17 default; media decode budgeted separately | §7.2, §7.5, §7.10 |
| E-6 | `-use_editlist 0` with `-bf 2` starts video 2 frames late | Accepted (re-measured) | `+faststart+negative_cts_offsets`; QA check on first PTS and A/V start; verified on 8.1 this session (0.0667 s → 0), to verify on 5.1 in B0 | §2.8, §7.5, §7.9 |
| E-7 | `page.clock` deadlocks `seek()` | Accepted | `page.clock` withdrawn; timer guard after `ready`; `Animation.setPlaybackRate(0)` kept | §7.4 |
| E-8 | BYOK does not cover the new paid providers | Accepted | `CREDENTIAL_PROVIDERS` and `resolve_keys` extended per batch; Lyria via the Gemini key; ElevenLabs Music blocked on free tiers; Chirp as a new credential type | §5.7 |
| E-9 | Budgets treat music as free | Accepted | `music` operation; unknown operations fail closed; `_media_billed_to_server` extended; usage rows for calibration, samples, New music | §5.7, §10.3 B0 |
| E-10 | AI music does not survive edits | Accepted | `MusicPlan` re-fitted on every build (trim at bars, loop points, procedural extension); test with a 10 s insert | §5.10 |
| E-11 | Splices unsafe where timings are interpolated | Accepted | `words_exact` per beat; splice only exact or silence-checked beats; token map where `map_words` runs; `speech_words.py` reference corrected | §8.2, §8.3, §6.7 |
| E-12 | `direct.py` can loop and misassign forced scene types | Accepted | Forced templates exempt from penalties; `board_generic` board-only; bounded post-pass with re-checks; property tests | §4.4 |
| E-13 | Post-render QA errors discard finished renders | Accepted | Deterministic checks in preflight; container-level post-render errors only, each with an automatic remedy; everything else a warning | §7.12 |
| E-14 | Described test gates do not exist in CI | Accepted | Docker `test` build target and blocking CI job; `node --test` source scan with the existing TypeScript parser instead of adding ESLint; non-blocking perf job on a fixed runner | §7.4, §7.9, §7.10, §10.2 |
| E-15 | Classic invariance depends on the environment | Accepted | Platform-independent goldens plus a same-container differential pixel test | §3.6 |
| E-16 | Offline tests cannot produce a lint-clean Dynamic lecture | Accepted | Fake writer, calibration, music and stock fakes per batch | §6.12 |
| E-17 | Live-player audio will not match the MP4 | Accepted | Speaker gains baked into scene audio; music via media element and GainNode; SFX buffers only; same-origin or CORS-configured media documented | §5.4, §5.12 |
| E-18 | Serve media frames from the worker | Accepted | `/__frames/…` answered by `route.fulfill` in the worker | §7.7 |
| E-19 | `/render-frame` cannot select by timeline style | Accepted | New `/render-dynamic` route chosen by the worker | §7.3 |
| E-20 | Nothing fits duration after TTS | Accepted | `fit.py`: tighten, drop recap, shorten think time, +5% re-synthesis, then a preflight refusal with "Fit to 2:58" | §6.4 |
| E-21 | Aadhi intro timing contradicts its word budget | Accepted with changes | Exit times derived from the last word + 0.3 s; scene clamped to [5.0, 8.0] s; ≤ 16 words in 16:9; sting ends before speech. In 9:16 the cap is hook ≤ 8 + sign-on ≤ 5 words with a measured end ≤ 7.4 s rather than 10 words: the 10-word figure came from the fixed 4.4 s exit, which is now derived, and the owner asked for 5–10 s appearances | §3.3, R-12, R-13 |
| E-22 | Reels and Remake cannot reuse checkpoints | Accepted | The parent's brief, source extract and overrides passed explicitly | §6.10 |
| E-23 | Editor side effects (re-direct on save, style in content keys) | Accepted | Settled locks; `direct.py` on save only for `auto` or unfit looks; PUT returns screenplay + ETag; `asset_style` frozen; Restyle pictures is explicit and costed | §4.4, §4.7, §6.8 |
| E-24 | Gaps in the edit model | Partly rejected | Accepted: overlay clamp/extend, clip duration with freeze, translation anchor markers, no range cuts on estimated timings, new translatable fields, overlays out of `scene_hash`, intro found by `look.presenter` with a warning for teacher edits. Declined: translating `ClipScene.credit` — an attribution must stay exactly as the licence requires, so credits are protected; clip captions are translated | §6.7, §6.11, §8.2, §8.3, §3.1 |
| E-25 | Screen recording and tus need platform changes | Accepted | `Permissions-Policy` per Studio page; CSRF header from tus-js-client; proxy body-limit documentation | §8.4 |
| E-26 | New job and asset kinds need wiring | Accepted | `normalize_clip` in `ALL_JOB_KINDS`, compose and docs (not version-mutating, so no index change); new asset kinds added to `GC_ASSET_KINDS` in their batches | §6.7, §8.4 |
| E-27 | The production default voice has no licence until B8 | Accepted | Azure adapter moved to B3 when Q8 = Azure; SDK or REST word timings decided on licence and Linux dependencies; Edge for development | §5.1, §10.3 B3 |
| E-28 | Cost targets inconsistent | Accepted | Auto uses the bed unless the teacher or org opts in; Lyria before ElevenLabs; ≤ 1 retry; price rows updated; P5 restated per provider | §1.3, §5.8 |
| E-29 | Phased plan unrealistic | Accepted with changes | Estimates per batch; a thin R1 covering both formats; the full batch-4 list re-verified in B0. R1 has 11 content and practice templates (the review's "about 12") plus 12 bookend, forced and fallback modules that every lesson and existing scene type requires | §10.1, §10.3, §4.3 |
| E-30 | Silence trimming already exists | Accepted | Extra trim option dropped; R-42 points at the existing 30 ms pad | §5.4, R-42 |
| E-31 | Calibration read from a cache at build time | Accepted | Resolved control values stored in `Presentation.voices` | §5.4, §6.7 |
| E-32 | Private calibration asset played in the browser | Accepted | Public `voice_sample` asset | §9.6, §6.7 |
| E-33 | The look preview cannot use the generation seed | Accepted | Server candidate endpoint with history exclusions; kept strips sent as `LookPrefs.resolved` | §9.6 |
| E-34 | `look_seed` reduction unspecified | Accepted | First 8 bytes of SHA-256 masked to 31 bits | §4.4 |
| E-35 | More ffmpeg 5.1 checks | Accepted | `negative_cts_offsets`, TS concat with `-bf 2`, zscale/libzimg added | §7.9 |
| E-36 | soundfile adds LGPL libsndfile | Accepted | numpy + scipy + stdlib `wave` | §5.11, §7.13 |

### C.3 Teacher-experience review (T)

| # | Finding | Verdict | Resolution | Where |
|---|---|---|---|---|
| T-1 | Regenerating changes look and voice; drafts consume rotation history | Accepted | Rotation per lesson; regenerate/duplicate inherit `Presentation`; Fresh look; seed without version number; history = newest rendered version of other projects; Course picker | §5.3, §4.4 |
| T-2 | Re-running `direct.py` on save restyles untouched scenes | Accepted | Settled locks; picks only for new or unfit scenes; variety rules as hints; only New layouts re-picks | §4.4, §6.8 |
| T-3 | Late render failures; inserted clips break the cap | Accepted | Generated runtime excludes inserted clips (platform-safe container limit still includes them); preflight; runtime meter; Fit to 2:58; automatic contrast and loudness fixes | R-01, §6.4, §7.12, §8.8 |
| T-4 | 9:16 hook band and caption band impossible as specified | Accepted | Measured text fit; hook band y 340–760 with ≤ 4 lines at 88–104 px; 744² Aadhi card at y 800–1544; captions x 120–780 sentence case 56–64 px, ≥ 52 px for one long word; R-32 rewritten as measured fit (re-measured this session) | §2.5, §3.3, §4.6, R-32, R-59 |
| T-5 | The Dynamic form is unspecified | Accepted | Exact field list; unused classic fields validated away; depth mapped to the word budget; GIFs/interactive labelled; test that the form never sends unused fields | §6.3, §9.2 |
| T-6 | A fixed 5:00 silently drops most of a source | Accepted | Merged with L-1 | §2.2, §9.3 |
| T-7 | Default music not good; production voice too late | Partly rejected | Accepted: procedural bed under speech only; blind listening gate before B4; Lyria (Gemini key) and Azure in B3. Declined: ElevenLabs Music in B3 — it costs ≈ $0.78 per lesson against Lyria's ≤ $0.32, and the engineering review asked to prefer Lyria (E-28); it ships opt-in in B7 | §5.8, §5.11, §10.3 B3 |
| T-8 | Constant parts make every video open and close the same way | Accepted with changes | Rotated hook kinds, greeting pool, seeded checkpoint order, 4 intro layouts, seeded reel card position, ≤ 1 s sonic logo only, B8 recommended. The "card right of the title" layout is replaced by `pip` and `split`, because a card right of the title would point Aadhi's gesture away from it (L-21) | §3.3, §6.5, §3.5 |
| T-9 | Voice variety partly illusory; foreign accents for en-IN | Accepted with changes | `speaker_id`; rotation by speaker; ≥ 2 of 3 lessons local by default, `local_only` once Azure supplies ≥ 8 local speakers; small pools stated plainly. Foreign accents stay possible in 1 of 3 lessons with the Edge pool (2 local speakers), because a 2-speaker local pool cannot otherwise rotate | §5.2, §5.3 |
| T-10 | 16:9 captions effectively off | Accepted | Merged with L-9; default-on disposition on the soft track; copy changed | §2.6, §9.2 |
| T-11 | The look preview cannot match generation | Accepted | Merged with E-33 | §9.6 |
| T-12 | Mid-scene "Insert clip here" undefined | Accepted with changes | Snap to sentence end; scene end in unsplittable scenes; `continues_from`. For full overlays the default is to **shorten** the overlay to end 0.3 s before the next reveal (deferring reveals would break word anchoring, R-17); deferral is the opt-in `keep_full_length` with a warning | §8.3, §8.8 |
| T-13 | The insert dialog blocks; three overlapping paths | Accepted | Two-step dialog; in-browser trim during upload; proxy-first normaliser; Adjust panel; "Replace this scene's picture" folded in | §8.4, §8.6, §8.8 |
| T-14 | Plan review not designed for Dynamic | Accepted | Locked bookends with reasons; segment structure and runtime meter; plan rules on approve with plain fixes; voice, music and look with Change links | §9.4 |
| T-15 | The default reel is not a Short | Accepted | Export as 2 parts (≤ 2:58 each), in B7 | §2.7 |
| T-16 | The reel's visual is small and captions sit over it | Partly rejected | Accepted: more room for the visual (one-line headline, stage y 460–1040; tall variant y 340–1040) and `contain_blur` clips at y 360–968 with captions below. Declined: moving captions between the top and bottom bands per template — caption position must stay fixed within a video so viewers need not search for it (Netflix TTSG; R-34) | §2.5 |
| T-17 | Spoken hook by 3 s cannot coexist with a greeting | Accepted | Hook first (≤ 8 words), sign-on after (≤ 5), no opener; measured `hook.late` | §3.3, R-03 |
| T-18 | Jargon warnings; too much exposed at once | Accepted | Teacher vs system lint audiences; inspector defaults with More; two default lanes; New colours / New layouts | §6.9, §8.8 |
| T-19 | The Script view cannot correct a word | Accepted | Edit sentence with re-voice; keep/remove prompt when a cut removes an item's anchor words | §8.3, §8.5 |
| T-20 | Reel music level breaks the 20 dB rationale | Accepted | −22 LU (lessons, Learning reels), −21 (Social), floor −20; preflight error below 20 | R-44, §7.12 |
| T-21 | All-caps captions | Accepted | Sentence case in every style; uppercase only for a single emphasised word of ≤ 2 words | §2.6, §4.11 |
| T-22 | Reduced-motion viewers get full motion | Accepted | Live-player mapping; second allowed live/MP4 difference; Calm motion render option | §4.8, §7.3 |
| T-23 | No brand kit or teacher defaults | Accepted | "Use as my default" (server-side, B4); site settings (B4); brand kit (B7) | §9.8 |
| T-24 | No self-recording | Accepted | Record with camera; record my own narration for a beat (B6) | §8.4 |
| T-25 | No draw-on diagram template | Accepted | `diagram_build` for SVG figures (W2, B7); Manim drawing stays in `animation_stage` | §4.3 |
| T-26 | The thumbnail is just the hook frame | Accepted | `thumbnail` template plus title and description drafts | §2.8, §4.3 |
| T-27 | Pronunciation not checked for the assigned voice | Accepted with changes | "Hear your terms" (hook + glossary in the cast voice) in plan review and the editor, with lexicon respellings. The form itself plays a generic sample, because the hook and terms do not exist until the plan does | §5.3, §9.6 |
| T-28 | B0 verifies only part of batch 4 | Accepted | One regression test per listed fix | §10.3 B0 |
| T-29 | 9:16 Aadhi below the owner's 5–10 s | Accepted | 5 s minimum for both 9:16 appearances | R-12, §3.3 |

---

## Appendix D. What could not be verified

The reviewers' web-search budgets ran out during their runs, and this revision added no new web research (it re-checked repo citations and re-ran measurements locally). The items below rest on memory, on a single secondary source, or on measurements not yet made in the production environment. None is the sole basis of a hard rule; each has a verification step.

| # | Claim | Status | Verification step |
|---|---|---|---|
| 1 | Moreno & Mayer 2000 used brief, event-synchronised sounds | Reviewer memory (M); the source host refused the connection | Read the paper before B2's SFX policy is finalised |
| 2 | Word-by-word captions pull gaze from images (Rajendran et al. 2013) | (M) | Literature check in B2; the design does not depend on it (phrase chunks are the Netflix norm anyway) |
| 3 | Event-boundary memory effects (Swallow 2009; DuBrow & Davachi 2013) | (M) | Literature check; Q13 lets the owner judge the variety trade-off in the gallery |
| 4 | Transient-information effect (Leahy & Sweller 2011; Wong et al. 2012) | (M) | Literature check |
| 5 | Predict–observe–explain benefit (Crouch et al. 2004) | (M) | Literature check |
| 6 | Debunking Handbook 2020 guidance on immediate refutation | (M) | Read the handbook |
| 7 | Familiar accents aid L2 comprehension (Major 2002; Harding 2012) | (M) | Literature check; the pilot records accent |
| 8 | Colour coding needs a consistent mapping (Ozcelik 2009) | (M) | Literature check |
| 9 | Gonzalez & Aiello 2019 changing-state findings | (M) | Literature check |
| 10 | YouTube, Chrome/Firefox, LMS and WhatsApp/Drive handling of in-band `mov_text` captions; where students actually watch | Unverified | B4 delivery check (§10.3) and Q10 |
| 11 | `-movflags negative_cts_offsets` on Debian's ffmpeg 5.1 | Measured on 8.1 only | B0 ffmpeg feature checks in the Docker test image |
| 12 | Render time, memory and the best x264 preset in the production container; whether `--disable-frame-rate-limit` helps safely | Not measured on the production path | B1 benchmark (§7.10) |
| 13 | Pillow reads WOFF2 in the Debian image; Python text fit matches Chrome within 4% (8% for Indic) | B2b: Pillow 12.3.0 / FreeType 2.14.3 reads every vendored WOFF2 on the Windows dev machine; Latin is bounded only with a measured kerning table plus a 0.06 em floor (4% alone was not conservative), verified on the real page on Windows (`tests/determinism/test_textfit_page.py`); **Indic refuted**: Pillow's basic layout misses Chromium by −75% to +210% per cluster, so HarfBuzz shaping was decided (§4.6). **B2b-H:** Python shapes every script with HarfBuzz on the vendored faces (no Pillow), and the Chromium parity probe (`tests/determinism/test_textfit_parity.py`) finds every face, weight and script within 0.054 px of the shaped width in every run (the extended runs measured 2.96 M and 3.87 M word widths; max 0.0531 px Latin, 0.0324 px Indic), with a 0.5% + 1/64 px margin. The kerning table is retired. Two line-breaking gaps found by the B2b-H audit are open (§4.6) | The Linux runs of the slow page tests and of the parity probe (container job never run); the two B2b-H audit findings; the B2g gallery comparison |
| 14 | Azure Speech SDK licence terms and Linux dependencies | Not reviewed | B3 decision between SDK and batch REST |
| 15 | ElevenLabs API list price (the stale $300/M row) | Reviewer-fetched page, not re-checked here | B0 pricing update |
| 16 | Procedural music quality when exposed | Prototype judgement only [AU §3.3] | B3 blind listening test |
| 17 | Display-font licences other than Atkinson and Anek; Natural Earth; BiRefNet | B2b read the LICENSE of every vendored font package (Poppins, Lexend, Atkinson Hyperlegible Next, Anek and the B1 faces): OFL 1.1, no Reserved Font Name (§4.6). The W2 display faces, Natural Earth and BiRefNet are still believed OFL / PD / MIT | Read each LICENSE when vendoring (W2, B7, B8) |
| 18 | Edge TTS terms for production use | No licence grant found | Q8 (Azure in production) |
| 19 | Gemini and Lyria age terms for 17-year-old students | Terms read by AU; legal meaning unclear | Q3 legal review |
| 20 | Instagram's 3:00 limit and reach beyond it | Practice-grade [SF §1] | Quarterly platform check (B9) |
| 21 | Pilot sample size (≈ 126) | Reviewer's computation, conservative (one topic per arm per student, ρ = 0.5); a 2-topic-per-arm design needs fewer | Pre-registered power analysis in B9 |
| 22 | The scope-estimate coefficients (§2.2) | Design guesses | Calibrated on the eval fixtures in B4 |

**Verified in this revision:** the review's repo citations used here (`base.py:76`, `screenplay.py:155/660`, `screenplay_service.py:56`, `models.py:74`, `audio.py:31`, `headers.py:24/65`, `config.py:43–51/179`, `cleanup.py:61`, `pricing.py:240–259`, `generation.py:244`, `pages.py:57`, `checkpoint.py`, `docker-compose.yml` render-worker limits, `ci.yml`, `package.json`, `main:server.py:758`); the MP4 start-time measurement with and without `negative_cts_offsets`; the 9:16 text-fit measurements with the vendored Inter Bold; the content palette's contrast, hue separation and simulated colour-vision distances.

---

*End of design.*
