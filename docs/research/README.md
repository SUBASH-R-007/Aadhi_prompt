# Research reports behind the Dynamic video design

The seven reports that `docs/DYNAMIC_VIDEO_DESIGN.md` cites, researched on 2026-10-09 and kept here unchanged
(owner decision Q12, batch B0). The design cites them by key, e.g. `[LS §8.2; Phan 2023]` = section 8.2 of
`learning-science.md` plus the primary source it rests on; Appendix B of the design lists every primary source
with its URL.

| Key | Report | What it covers |
|---|---|---|
| LS | [learning-science.md](learning-science.md) | Multimedia-learning meta-analyses, video length, segmenting, retrieval practice, captions, voice, music, colour, instructor presence |
| SF | [short-form.md](short-form.md) | Platform limits (Shorts, Reels, TikTok, LinkedIn), encoding, loudness, safe zones, captions, hooks, YouTube long-form rules |
| MR | [motion-rendering.md](motion-rendering.md) | Deterministic browser capture, benchmarks on the development laptop, library licences, WebGL, fonts, the colour pipeline |
| AU | [audio.md](audio.md) | TTS providers and voices, AI music, royalty-free libraries, the procedural music prototype, mixing evidence, ffmpeg filter facts |
| ED | [editor-nle.md](editor-nle.md) | How editors handle insert and cut, the anchored edit model, clip intake, SSRF, stock APIs, YouTube terms, shortcuts |
| CP | [competitive.md](competitive.md) | AI video tools and educational channels, the template catalogue, pacing, motion, typography and palette grammar |
| CB | [codebase.md](codebase.md) | Read-only audit of this repository at `195be39`: where Aadhi and 16:9 are hard-coded, renderer limits, the schema extension pattern, risks |

Read them as a snapshot, not as current documentation:

* Each report grades its own evidence (its section 0 or first lines explain the grades) and lists what it could
  not verify; a shared web-search budget ran out during the session, so some items rest on secondary sources.
* Prices, model names and platform limits are as read on 2026-10-09. Where the code now differs (for example the
  Gemini Omni video provider and the ElevenLabs prices re-read on 2026-10-10), `docs/OPERATIONS.md` and
  `aadhi/usage/pricing.py` are authoritative.
* Line references in `codebase.md` point at commit `195be39` and drift as the code changes.
* The measurement scripts and raw outputs the reports mention (`scratchpad/research/tools/`, `scratchpad/research/out/`,
  `scratchpad/bench/`) stayed in the research session and are not part of the repository.
