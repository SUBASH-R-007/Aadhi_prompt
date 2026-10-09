# REC – Aadhi EduEngine v2 — Documentation

This is the entry point for teachers, developers and AI assistants working on the project. It
replaces the v1 `DOCUMENTATION.md` / `handoff.md`, which described a system that no longer exists.

| Read this | For |
|---|---|
| [README.md](README.md) | What it is, quick start |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Design principles, pipeline, data model, rendering, security model |
| [docs/API.md](docs/API.md) | HTTP API |
| [docs/INTERFACES.md](docs/INTERFACES.md) | Module boundaries (who exports what) |
| [docs/SECURITY.md](docs/SECURITY.md) | Threat model, controls, incident notes |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Setup, configuration, deployment, scaling, upgrades |
| [docs/EVALS.md](docs/EVALS.md) | Measuring lecture quality and catching prompt regressions |

## 1. How a lecture is made

1. **Upload**: a teacher uploads a PDF/DOCX/TXT/MD and chooses options. Options include narration
   language, board language, audience, target length, quizzes, animations, images, AI video and
   voice.
2. **Ingest**: the source is converted into page-anchored text chunks, tables and extracted
   figures. Scanned or maths-heavy PDFs are also passed natively to the multimodal model.
3. **Plan**: learning objectives, a concept dependency graph, common misconceptions, chapters, and a
   scene-by-scene outline with a stated goal and visual rationale per scene. Teachers can review and
   edit the plan before anything else is generated.
4. **Script**: each scene is written in parallel as narration *beats* paired with concise board items.
   Worked examples fade (blank steps the narration later fills in). Quizzes use real
   misconceptions as distractors.
5. **Validate**: deterministic lint checks plus an AI critic. The critic checks claims against the
   cited source chunks, and checks pedagogy (concrete before abstract, example + non-example,
   bridging, visuals referenced in the narration). Only failing scenes are rewritten.
6. **Companion sheet**: formulas, definitions and misconceptions are taken from the lecture, plus
   practice problems with an answer key.
7. **Assets**: narration is synthesised beat by beat (exact timings). Manim animations are rendered
   from tested templates and timed to the narration. Images are generated. Real-world AI footage is
   used only when enabled and within budget.
8. **Timeline**: everything is laid out on one timeline. The browser player and the MP4 renderer both
   play this same timeline.
9. **Edit & export**: teachers edit any scene, beat or board item, preview instantly, regenerate a
   single scene, render the MP4, share a watch link with students, and read the learning analytics.

## 2. Studio guide (teachers)

* **Projects**: every upload is a project. Generations and translations create new *versions*, so
  earlier versions stay available.
* **Editor**: the scene list is on the left; issues show as badges. The selected scene's beats,
  board items and side panel are in the middle. The live preview is on the right. Every save is
  linted; scenes whose narration or visuals changed are marked *stale* until you press **Build**.
* **Regenerate scene**: describe what to change ("use a water-pipe analogy", "make the circuit
  parallel") and only that scene is rewritten.
* **Render MP4**: produces the video, SRT/VTT captions and YouTube chapter timestamps.
* **Share**: creates a watch link for students (optional expiry, revocable). Students need no
  account.
* **Analytics**: drop-off per scene, quiz accuracy and distractor choices, and flagged confusing
  scenes.
* **Translate**: creates a Tamil, Hindi or other language version with a matching voice. Technical
  terms in the lexicon can stay in English.
* **API keys**: save your own Gemini, OpenAI or Claude key to use it for the lectures you start
  (admins save server keys for everyone on the Admin page). See
  [README: Your own API keys](README.md#your-own-api-keys).
* **Library**: your own pictures and clips (uploads and the media your builds generated), with
  titles, descriptions and keywords you can search. Every media control in the editor has
  **Choose from library**. Private to you. See [README: Media library](README.md#media-library).
* **Review visuals**: every scene's visual of a version in one place, with where it came from and
  whether it is ready. Approve it, ask for a new AI version, use a library picture, upload a
  replacement or remove it. The editor's issues panel offers **Rebuild this scene** and **Review
  visual** for media problems, and **Generate again…** (after a confirmation) for an AI video that
  may already have been paid for. See [README: Visual Review](README.md#visual-review).

## 3. Developers

Start with [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), then [docs/INTERFACES.md](docs/INTERFACES.md).
Conventions: [ARCHITECTURE §12](docs/ARCHITECTURE.md#12-conventions). Run the tests before every
change:

```bash
.venv/Scripts/python -m pytest -m "not slow"
```
```bash
npm test
```
