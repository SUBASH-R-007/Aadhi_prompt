# B3 engineering plan — Audio system (binding, revision 2)

Revision 1 was written 2026-10-17 on `overhaul/v2` @ 86d8772 from the design (`docs/DYNAMIC_VIDEO_DESIGN.md` §5, §6.3,
§6.7, §10.3 B3, R-36..R-50), three read-only reconnaissance reports and a read of the `tts-openai` worktree.
Revision 2 (same day) answers the 32-finding critique; every finding is answered in **§C "Critique answered"** at the
end, and each fix is also written into the section it changes. Shape copied from `docs/plans/b2/b2_plan_v2.md`:
sequence §A, measured policy §P, frozen contracts §1, rules §2, safeguards §3, branch strategy §4, owner decisions §5,
gates and acceptance §G, deviation register §D, critique answered §C. Where this plan changes the design, the change is
in §D and goes into the design's B3 as-built notes when the step lands.

**Owner instructions this plan obeys.**
- **2026-10-17: OpenAI TTS is the PRIMARY voice provider; Edge is the fallback when no OpenAI key resolves.** This is
  being built on branch `tts-openai`. B3 builds on it and never contradicts it. Where the design's provider and casting
  text says otherwise, §D lists the as-built amendment.
- **2026-10-17: B3 runs in parallel with the rest of B2.** It lives in its own worktree and branch and touches B2's
  files as little as possible.
- Standing rules:
  - Classic output never changes (invariance goldens). The audio pipeline is shared with classic, so every shared
    audio seam is behind ONE predicate (§1.0) and a key gate (§3.1).
  - B1/B2 timelines and **stored Dynamic lectures** stay unchanged unless a step says otherwise and the owner agreed
    (§1.0 legacy rules, O-B9).
  - Fakes only in tests.
  - No hour-scale checks in CI.
  - Commit a rollback point first each session.
  - Process matched to risk (`docs/plans/b2/HANDOFF.md` "Where things are").

**Blocking owner decisions** (no default binds; the named work waits): **O1** before B3b's casting lands, **O2** at the
end of B3-0, **O17** semantics before B3a, **O-B9** before B3b's inheritance and before L2. Everything else has a
default that applies if unanswered (§5).

## 0. Starting state (what the plan depends on)

### 0.1 `tts-openai` as observed 2026-10-17
The work is uncommitted in `C:/t/tts` and still in progress. **Re-check this list when it lands.**
- `Settings.tts_provider` defaults to `"openai"`.
- `factory.narrating_tts_provider(requested, settings)` returns, in order: the requested provider if configured; else
  `TTS_PROVIDER` if configured; else `"edge"` (`KEYLESS_TTS_PROVIDER`).
- `assets._Builder._tts_provider()` returns `(tts, VoiceConfig)`. With no key it steps down to Edge with ONE
  lecture-level `assets.tts_fallback` warning and a key hint, and picks the voice with `fallback_voice_config` (the
  language default, e.g. en-IN Neerja, female), unless the lecture's own voice is an Edge voice (`_narrate_with`).
- `voices.OPENAI_DEFAULTS`: `en-GB → fable`, every other language `nova`. `voices.is_known_voice()` /
  `voices.voice_fits()` are new; `voice_fits` drops an Edge `TTS_VOICE` sent to OpenAI.
- `authoring.narration_choice()` makes `default_presentation` cast the provider that will really narrate.
- `_voice_payers` no longer bills a keyless server provider. The Studio options form is changed.
- It does **not**: send `instructions`, add personas, change `openai.py`, or **freeze the voice of stored lectures**:
  `_reusable` rejects a scene on a `(provider, voice)` mismatch, so a stored classic lecture with
  `tts_provider=None` is re-synthesised on OpenAI after the default flips (O17).

### 0.2 On `overhaul/v2` from B0/H0/B2

**Already built**
- Inert schemas, all excluded at defaults:
  - `VoiceCalibration{natural_wpm, control}` (`screenplay.py:720`), `VoiceCast`, `MusicChoice`,
    `Presentation.voices/.music/.sfx/.pins/.greeting_id/.look_epoch`;
  - `MusicCue`, `MusicPlan{cues, duck, mode ∈ bed|gaps_bookends|off, ref_lufs, level}`, `SfxCue`,
    `Timeline.music/.sfx`;
  - `BeatAudio.persona/.words_exact` (`words_exact` is in `BeatAudio.V3_FIELDS`, B5's "cuts" feature),
    `SceneAudio.lufs/.speaker_gains`, `MusicAsset`, `AssetManifest.music` (a deferred JSON column).
- `pricing.py`: the `music` operation; fail-closed unknown operations; `gpt-4o-mini-tts` at 16 USD per 1 M characters
  (a per-character proxy for the audio-output cost); Lyria, ElevenLabs and procedural rows.
- `_voice_payers` and `_music_payers` read `opts.voice` / `opts.music` through `getattr`. **Those fields do not exist.**
- `compose/loudness.py`: ebur128, two-pass loudnorm, static gain, limiter, `LoudnessTarget`.
- `compose/dynamic/container.py` measures loudness and raises QA problems **only when given a `LoudnessTarget`**
  (`:344-358`); today no caller passes one for Dynamic.
- `PUBLIC_ASSET_KINDS` has `music` and `voice_sample`. `asset_uses` authorises `Presentation.music.asset_key`.
- B2's `authoring.default_presentation` writes `MusicChoice(source="off")` — so `mode` takes its default `"bed"` — and
  a **bare** narrator id (`"nova"`, `"en-IN-NeerjaNeural"`) with **no calibration**. "off is a first-class source,
  not a placeholder" (`authoring.py:153`). Every stored Dynamic lecture has exactly this shape.

**Facts the plan must respect (verified in the code, 2026-10-17)**
- **Dynamic is a predicate of screenplay AND flag.** `timeline.py:754`:
  `dynamic = screenplay.presentation is not None and dynamic_enabled(settings)`; a presentation with the flag off
  builds a CLASSIC timeline (the isolation test's PROBE asserts it). `dynamic_enabled` merges `DYNAMIC_ENABLED` with
  the admin's runtime flag (`runtime_flags`, `app_settings`, applied "when a timeline is served: no rebuild needed").
- **Generation has no DB, user or project in `script.py`.** `assemble_screenplay` / `_default_presentation`
  (`script.py:374-420`) are sync and get only `(plan, options, settings)`. The async job context (`DBJobContext`:
  `user_id`, `project_id`, `assets`, `record_usage`, `settings`) and `_db(ctx, fn)` are in
  `pipeline/orchestrator.py` (`generate_lecture`, script stage at `:919`).
- **No path carries a parent Presentation into a new generation.** `POST /projects/{id}/regenerate`
  (`projects.py:406`) enqueues `generate_lecture` with the options and source only; nothing in `api/generation.py`,
  `version_actions.py`, `projects.py`, `jobs/*` or `orchestrator.py` mentions `presentation` or `look_epoch`.
  `duplicate_version` deep-copies the screenplay and manifest, so a duplicate inherits trivially.
- **Translation copies the presentation verbatim** (`translate.py:316-321`).
- **GC:** `jobs/cleanup.py:199-229` deletes assets of `GC_ASSET_KINDS` that are unreferenced (no `AssetRef`, no
  document mention) and older than 30 days. `AssetRef.project_id` is NOT NULL: there is no system-level pin.
- **Aadhi's intro and recap are synthesised appearances** (`presenter.py` `plan_appearances`, `intro_scene`,
  `recap_scene`), not screenplay scenes, and silent ("it has no scene number: it is not in the editor"). The manifest
  has no slot for their audio; `Presentation` has `greeting_id` and no sign-on id.
- **The Dynamic MP4 still carries the classic mix**: per-scene quiz ticks and ding (`motion/engine.py:7,71-72`), the
  ducked branding BGM (`encode.master_audio_command`, `ffmpeg.bgm_filter`).
- **The live player** builds `this.bgm = new BackgroundMusic(...)` for Dynamic too (`player.js:361-362`) and ducks it
  while a beat speaks (`:788`); it already skips the classic tick/ding for Dynamic (`:360`).
- **Lesson length:** Dynamic lectures today use the classic `target_minutes` (default 15, 3–90). B4's length profiles
  and validator do not exist yet.
- **Fixtures:** the only kitchen-sink fixture is `tests/invariance/goldens/classic_kitchen_sink` (reused by
  `test_presenter.py`). No Dynamic kitchen-sink, gallery or ≥ 30%-dense fixture exists; B2g builds those in parallel.
- `credits.txt` exists nowhere in the code (B4's export bundle).
- `scipy` is only transitive (`requirements.lock` 1.17.1 via manim).
- The container CI job is already red, including `test_loudness.py::test_loudnorm_dynamic_fallback_and_static_gain_
  with_limiter` (loudnorm's dynamic second pass fails on ffmpeg 5.1).
- No test pins the classic `clip_key` or the scene-audio key; the invariance goldens use a synthetic manifest and
  `TTS_PROVIDER=fake`, so they cannot see TTS or key drift.
- The voice catalogue has 9 OpenAI voices; the design lists 13 (ballad, verse, marin, cedar missing). The OpenAI
  adapter accepts any id matching its regex, so a persona voice need not be in `voices.CATALOGUE`.

### 0.3 The retirement clock
**Every `/v1/audio/speech` model retires on 2027-01-06** (OPERATIONS §3.3), 81 days from this plan. B3 takes about
3–4 weeks of build, so the OpenAI-specific work (instructions control, `wpm=N` calibration grammar, per-sentence OpenAI
synthesis, model-keyed calibration assets, Aadhi's brand voice) lives in production for weeks, not months, before the
switch. The successor (O2) is therefore a **B3-0 output**, not a December decision, and every OpenAI-facing contract
in §1 is chosen to stay valid on it (§1.1, §1.3, O8, O9, step B3k).

## A. Sequencing

**Every step runs: implement -> review -> fix -> gates (§G) -> commit.** Steps land in groups (§4.3), so no interim
state is shipped that the next landing would immediately change.

**Review weight by step type** (HANDOFF "Where things are": an AUDIT round whenever a shared file is touched):
- **ADD** (only new modules behind strong gates): ONE review and ONE fix round.
- **SHARED** (touches any file in §4.4's shared table, a B2 file or a `tts-openai` file): an AUDIT round after the fix.
- **OUTPUT** (changes audio or frames something already plays): a skeptic for every finding whose fix would change
  working output.

Each step is sized for one Workflow script (`docs/plans/b3/b3x_workflow.js`, written when the step starts).

**Order rule.** The narration is finished before the music is tuned, because every narration change re-keys the
clips. After that, the earliest steps deliver what is most audible with OpenAI primary.

| Landing | Steps | What ships |
|---|---|---|
| **L0** (now) | B3-0 | Gates, the shared Dynamic predicate, the frozen `history` module, B3's own fixtures |
| **L1** | B3a + B3b + B3c | Finished Dynamic narration: personas, styles, casting, calibration, gains, sentences, R-37 |
| **L2** | B3d + B3e + B3f + B3g | ONE consistent audio state: bed, mix, SFX and live player together |
| **L3** | B3h | Sources, previews, money, API surfaces |
| **L4** | B3k | The O2 successor adapter, **landed by 2026-12-15** (conditional on O2) |
| **L5** | B3i | Aadhi speaks (conditional on O19) |
| **L6** | B3j | Acceptance, docs, final merge |

### B3-0 Setup, safeguards, predicate, history, fixtures, spike. Type: SHARED (one line in `timeline.py`, one function in `compose/presentation.py`).

1. **Worktree, branch and rollback commit.** Worktree `C:/t/b3`, branch `b3-audio` from `overhaul/v2` HEAD (§4).
   Commit a rollback point first.
2. **The shared predicate (§1.0).** Add `is_dynamic(presentation, settings)` beside `dynamic_enabled` in
   `aadhi/compose/presentation.py`, and make `timeline.py:754` call it (behaviour-identical). Add the two pure legacy
   markers `b3_voiced(presentation)` and `b3_music(presentation)` in the same file. No caller uses the markers yet.
   Coordinated with the B2 lead (both files are B2-shared); AUDIT.
3. **Gate tests.** Split by what the unmodified tree can satisfy:
   - **Must pass now and forever:**
     - `tests/invariance/test_audio_keys.py` freezes: the classic `clip_key` payload and value for fixed beats on
       `edge`, `openai` and `fake`; the `scene_audio` key for a fixed clip list; `AUDIO_VERSION`; `TICK_KEY` /
       `DING_KEY` and the sha256 of `tick_wav()` / `ding_wav()`; the OpenAI request dict with no style (extends
       `test_openai_tts_legacy_models_keep_the_same_request`).
     - **Flag-off gate:** a screenplay WITH a presentation built while `dynamic_enabled` is False (environment off,
       and environment on with the runtime flag off) gives `clip_key`, `scene_audio` key, `SceneAudio` and the
       timeline audio fields equal to the same screenplay without a presentation.
     - **Legacy gate:** `tests/fixtures/b3/b2_era/` — a B2-era Dynamic screenplay (bare narrator, no calibration,
       `MusicChoice(source="off")`) and its manifest, built today with the fake TTS and frozen as a fixture. Rebuilt
       after every B3 step: **zero TTS calls**, identical clip and scene-audio keys, identical `SceneAudio`, identical
       timeline (including `music is None`) and the B1 master filtergraph.
     - **Isolation** (extend `tests/compose/test_dynamic_isolation.py`; the PROBE runs in a fresh interpreter):
       - add to the probe a **classic `build_assets` run with the fake TTS** and an **`assemble_screenplay`** call,
         so leaks from `pipeline/assets.py`, `pipeline/audio.py`, `script.py` and `orchestrator.py` are seen;
       - FORBIDDEN gains, explicitly (outside the `aadhi.compose.dynamic` prefix): `aadhi.providers.tts.personas`,
         `aadhi.providers.tts.delivery`, `aadhi.providers.tts.calibration`, `aadhi.pipeline.casting`,
         `aadhi.pipeline.history`, `aadhi.pipeline.music`, `aadhi.pipeline.cast_stage`, `aadhi.providers.music`,
         `aadhi.compose.bs1770`, `scipy`;
       - importing the API app imports no `scipy`;
       - a **Dynamic** `build_timeline` / `preview_timeline` imports no `scipy`, no `aadhi.providers.music.*`, no
         `aadhi.compose.bs1770` (the timeline only reads metadata).
     - **`GET /api/meta` voice-list golden** (`tests/invariance/goldens/_api/meta_voices.json`): pins the classic
       Studio voice and language lists, so B3 cannot change them by accident (§D 28).
   - **Committed as `xfail(strict=True)` until `tts-openai` lands the freeze (O17):** a stored classic lecture with
     `tts_provider=None`, rebuilt after `TTS_PROVIDER` flips, makes the TTS calls O17 prescribes (recommended: zero).
     It fails today on `overhaul/v2` and on `tts-openai` as observed; B3 cannot fix it on `tts-openai`. B3a does not
     start until it passes or the owner has answered O17 otherwise (§5 O17).
4. **History (§1.4), landed now** so B2 and B4 build on one reader instead of their own (`presenter.py` says history
   is §5.3's; `direct.py` lists the history-aware share as missing). `aadhi/pipeline/history.py`: interface frozen,
   implementation complete, read-only, no migration. Its tests: drafts, failed renders, unrendered duplicates and
   other users never count; ordering and tie-breaks; `Render.job_id` NULL rule; the classic-scope rule (O21); query
   count ≤ 3 per call.
5. **B3's own fixtures** (`tests/fixtures/b3/`), built from templates that exist on `overhaul/v2` today, so B3 never
   waits on B2g: `lesson_16x9` (kitchen-sink of today's templates, a quiz with countdown and reveal, chapter bumpers),
   `reel_9x16` (Learning reel), `dense_16x9` (≥ 30% dense runtime), `long_16x9` (a 60-minute synthetic timeline for
   the mix memory bound, metadata only), and `b2_era` (above). If B2g's kitchen-sink lands first, B3j also runs on it.
6. **Spike.** Measurement only, in a scratchpad, owner-approved (O0), ≤ about $1 on the server key. Samples go to
   `C:/t/b3_spike/`, outside the repo. Results go into §P.

   | # | Question | How it is measured |
   |---|---|---|
   | a | Which of the 13 design voices does `gpt-4o-mini-tts` accept? | One request per voice |
   | b | `speed` 0.90 / 1.00 / 1.10: natural re-pacing or a stretch? | Spectral centroid, F0 median, listening. R-37 forbids stretching |
   | c | `instructions` "about N words per minute" for N = 140, 155, 170 | Measured wpm, 3 repeats each, giving its spread |
   | d | The §5.6 style rows as `instructions` | wpm and LUFS shift per row |
   | e | Per-sentence vs per-beat synthesis on a 4-sentence beat | Join audibility (owner listening), prosody resets, LUFS jumps, requests, wall time |
   | f | `instructions` asking for an Indian-English accent | Samples for every accepted voice, for O1 |
   | g | Integrated LUFS and sample-peak spread across voices | Sizes the R-41 gains and their headroom cap |
   | h | Byte-determinism of two identical requests | Expected to differ: the cache is the truth |
   | i | With the owner's Gemini key: the same texts on `gemini-3.8-flash-tts` with style | O2 |
   | j | **The successor candidates (O2)**: OpenAI's post-retirement speech path, Gemini TTS | Voice names offered (intersection with (a)), a style/instructions control, word timings, price, a 4-sentence sample each |

7. **Done when:** §P is filled in; O2 is answered (or the owner says it waits, with B3k then dropped from B3 and the
   risk recorded); O1, O4, O8, O9 answered or their defaults apply; the L0 gates and modules are committed; **L0 lands
   on `overhaul/v2` at the next B2 step boundary** (§4.3).

### B3a OpenAI delivery, the persona registry and the Dynamic voicing branch. Type: SHARED + OUTPUT for B3-voiced Dynamic lectures only.

**Starts only after** `tts-openai` has landed on `overhaul/v2` (O-B2) and the O17 gate passes or O17 is answered.

**Audible result.** Dynamic narration on OpenAI, with segment-aware delivery and a persona per beat — for lectures
that B3b casts. B2-era lectures and every classic lecture sound exactly as before (§1.0).

**New: `aadhi/providers/tts/personas.py`**
- `VoicePersona` (§1.1) and the static `PERSONAS` catalogue: OpenAI personas from §P (a) ∩ O8; the Edge en-* pool of
  §5.2 plus one persona per existing Edge language default; fake personas. **Personas are not added to
  `voices.CATALOGUE` or `LANGUAGES`** (the meta golden stays green, `tts-openai`'s `voices.py` is untouched).
- `persona(id)`, `speakers(personas)`, `resolve_cast_id(raw, *, provider_hint)` (§1.2), `fallback_persona` (§1.6),
  `DEFAULT_CALIBRATION` (a table value per persona used only when calibration cannot run, §1.5).
- Tests: voice ids disjoint across providers; every `voices.CATALOGUE` default is a persona; AnaNeural and
  ta-LK/MY/SG are not narrators (R-40); every enabled OpenAI persona's voice is offered by the O2 successor.

**New: `aadhi/providers/tts/delivery.py`**
- `delivery(segment, role, reel_profile) -> Delivery{style: str, rate_pct: int}`, implementing the §5.6 table.
- `segment_of(scene, beat, template_dense) -> Segment`. Social reels shift one row more energetic.

**`providers/base.py`, adapters**
- `TTSProvider.synthesize(..., style: str | None = None)`, keyword-only. `assets.py` passes `style=` **only when it
  is non-empty**, so existing test doubles and classic calls without the kwarg keep working.
- OpenAI sends `instructions` only when `style` is non-empty AND the model is in the `gpt-4o-mini-tts` family (and,
  after B3k, the successor's equivalent). Never to `tts-1` / `tts-1-hd`.
- Gemini keeps a `style` pass-through (prepended to its prompt), with a request-shape test (O14).
- **Usage:** `characters` stays `len(input)`; the instruction length goes to `meta.instruction_chars` and is not
  priced at the per-character audio rate (§1.12).

**`pipeline/assets.py` — the B3 voicing branch** (§1.0, §1.6, §1.6b)
- Taken **only when `b3_voicing(sp, settings)`** = `is_dynamic(sp.presentation, settings) and b3_voiced(sp.presentation)`.
  Otherwise the build is today's code, byte for byte: classic, flag-off and B2-era (legacy) lectures (B3-0 gates).
- `VoiceConfig` gains `style: str = ""`; the `clip_key` payload gains `"style"` **only when non-empty**.
- Each beat's persona is `presentation.voices.<role>` chosen by `Beat.speaker`, resolved by `personas`; each persona
  gets its own provider instance and `VoiceConfig`; `BeatAudio.persona` is written.
- **Fallback override inside `_tts_provider`** (shared with `tts-openai`): for a B3-voiced lecture, after
  `tts-openai`'s provider resolution steps down to Edge, the voice is `fallback_persona(narrator, language)` (Edge,
  same gender), not `fallback_voice_config`. One `assets.tts_fallback` warning names the persona. The classic and
  legacy branch keeps `tts-openai`'s code exactly.
- **`ModelUnavailable` before the fan-out:** the first clip the build actually needs for the narrator persona is
  synthesised alone, before `SCENE_PARALLEL` starts (it is cached and reused, so the probe costs nothing extra). If it
  raises `ModelUnavailable`, the lecture re-casts to `fallback_persona` before any scene is narrated. A
  `ModelUnavailable` after a successful probe (a retirement mid-build) fails the build retryably; the retry's probe
  re-casts. No finished scene is ever re-narrated inside one build.
- **Reuse (`_reusable`) for B3-voiced scenes** (§1.6c): compared per beat (persona and B3 beat key) and per scene
  (narrating persona, frozen gains), not with the single-valued `(provider, voice)` and `self.clip_key(t)`.
- **`GenerationOptions.tts_rate` on a B3-voiced lecture** is ignored for pacing (calibration and delivery set it),
  with one `info` issue when it is not the default ("The Dynamic voice sets its own pace"). B4's validator later
  refuses it at the API. Classic is unchanged.
- Until B3b, rate is `delivery.rate_pct` through the §P control. Aadhi beats are not voiced (B3i).

**Tests**
- No style: request dict and `clip_key` byte-identical (B3-0 gates); a test double without a `style` kwarg still
  works for classic.
- With a style: `instructions` sent, key differs; `tts-1` never receives `instructions`; Gemini prompt carries style.
- A fake TTS records `style` and `rate`; Dynamic beats carry `persona`; a bare id resolves.
- Keyless: a B3-voiced lecture cast `openai:<female voice>` narrates with a female Edge persona; a classic one keeps
  `fallback_voice_config`.
- `ModelUnavailable` on the probe re-casts before any scene; the fan-out makes no OpenAI call after it.
- Reuse: a B3-voiced lecture rebuilt unchanged makes zero TTS calls; editing one beat re-synthesises that beat only.
- `instruction_chars` recorded, `characters` unchanged.
- The B3-0 gates (classic, flag-off, legacy) stay green.

### B3b Cast stage, casting, calibration, inheritance, translation, frozen gains. Type: SHARED + OUTPUT for Dynamic.

**Audible result.** New Dynamic lessons rotate voices, sit near 155 wpm and play at equal levels.

**New: `aadhi/pipeline/cast_stage.py`** (§1.5b) — the async seam that the sync `script.py` cannot be:
- `async cast_stage(ctx, sp, options, *, parent: Presentation | None, history_entries) -> Screenplay`.
- Called from `pipeline/orchestrator.py` `generate_lecture` as a new stage **"cast" right after "script"**, only when
  `is_dynamic(sp.presentation, ctx.settings)`; checkpointed (`ckpt.save("cast", ...)`) so a retried job does not
  re-roll. History is read through `_db(ctx, history.recent, ...)`; calibration uses `ctx.assets` and
  `ctx.record_usage`. It replaces `presentation.voices` with the cast and the filled calibration (`model_copy`).
- `script.py` and `authoring.py` are **not edited**: `default_presentation` still writes B2's default, which the
  stage overwrites, so `evals/make_fixtures.py --check` and every fixture built from `assemble_screenplay` stay
  unchanged.
- B4's Fresh look / New colours / New layouts actions (not built yet; no API route bumps `look_epoch` today) reuse
  this function in their own job.

**New: `aadhi/pipeline/casting.py`** (§1.5)
- Implements the §5.3 algorithm. The accent policy is a parameter; its server default is set by **O1 (blocking)**.
- Course pins derived from history (O16). The student, Aadhi (brand flag) and reel-from-lesson exclusion as §1.5.

**Inheritance plumbing (new, SHARED)**
- `POST /projects/{id}/regenerate` (and the "Generate again" action, which is this route; re-verified at the step's
  start) puts `parent_version_id` = the project's current version into the `generate_lecture` payload
  (`api/routers/projects.py`, `api/generation.py` `_generation_payload`).
- `orchestrator.generate_lecture` loads the parent's `Presentation` (if any) and passes it to `cast_stage`.
- `duplicate_version` already deep-copies the screenplay: no change, one test.
- Legacy parents (B2-era, no calibration) follow **O-B9**.

**Translation re-cast (new, SHARED)** — in `orchestrator`'s translate handler, after `translate()` returns (not in
`translate.py`): `casting.recast_for_language(presentation, target_language, usable)` keeps each persona whose `reads`
covers the target language (every OpenAI persona), else picks the same-gender persona of the narrating provider for
that language, and calibrates any new persona. Music, look and pins are kept.

**New: `aadhi/providers/tts/calibration.py`** (§1.3)
- A fixed 45-word text per language, versioned by `CALIBRATION_VERSION`.
- `calibrate(persona, *, assets, record_usage)` gives natural wpm, integrated LUFS and sample peak, cached as a
  private `calibration` asset whose `meta` holds them; usage row `operation="tts"`, `purpose="calibration"`.
- `control_for(persona, target_wpm, delivery)` follows §5.4, OpenAI's control from §P.
- At generation the result goes into `Presentation.voices.calibration`; rebuilds use only stored values (E-31).

**Speaker gains, frozen per lecture (R-41, §1.6b)**
- One static gain per persona = `-16 - calibration_lufs`, clamped to ±12 dB and to the headroom cap
  (`-1 dBFS - calibration_peak`), computed **once per lecture**, not from the build's clips.
- Stored in every B3-voiced `SceneAudio.speaker_gains`; a rebuild takes the gains from the previous manifest when it
  has them, else from the calibration asset meta, else re-runs `calibrate` (deterministic key).
- `pipeline/audio.build_scene_audio(..., gains=None)` applies the gain in float before the s16 conversion, through a
  soft limiter at -1 dBFS. The `scene_audio` key payload gains `"gains"` only when non-empty.
- `SceneAudio.lufs` is measured per scene for the preflight's per-speaker spread warning (≤ 2 LU, B3e).

**Other changes**
- `GenerationOptions.voice: VoicePrefs | None` (§1.7), excluded at None.
- `models.PRIVATE_ASSET_KINDS += calibration` (never in `GC_ASSET_KINDS`, §1.12).
- Pure metric functions for B4's lints and evals: `speech_wpm`, `speech_wpm_max_10s`, `secondary_share`,
  `student_density`, `voice_differs_from_previous`, `local_accent_share`.
- The Edge pool extension is **personas-only**, en-* voices only (O14); no new languages.

**Tests** (§10.3)
- Casting deterministic; speakers excluded by speaker id (the Neerja variants count once); accent policy per O1;
  regenerate inherits (payload → orchestrator → stage, end to end with the fake TTS and the DB); duplicate inherits;
  pins work.
- A cast always has the narrator's calibration (also when calibration fails: `DEFAULT_CALIBRATION`).
- Translation: an en-IN lesson cast with an Edge en-IN persona, translated to Tamil, is voiced by a ta-IN persona of
  the same gender; an OpenAI persona is kept.
- Calibration maths: clamps (Edge ±25%, OpenAI per §P).
- Gains: change the scene-audio key but not the beat keys; editing one scene changes no other scene's key; a hot fake
  clip at +12 dB never exceeds -1 dBFS sample peak; a classic build has no gains and an unchanged key.
- The cast stage never runs for classic, flag-off or a missing presentation; the isolation probe stays green.

### B3c Per-sentence synthesis, pauses and the R-37 check. Type: SHARED + OUTPUT for Dynamic.

**Audible result.** Captions and word-anchored events line up on OpenAI narration, with R-42 sentence pauses, and no
10-second window runs above 185 wpm.

**Scope:** B3-voiced lectures only, providers whose `supports_word_timings` is False (openai, gemini, successor).

**Per-sentence synthesis**
- Sentences from a small splitter in `pipeline/sentences.py` (reuses no B2d module); each sentence is its own clip.
- The beat clip is assembled with the R-42 between-sentence pause (16:9 0.25–0.45 s from the punctuation; 9:16
  0.15–0.25 s). Its key is `compute_key("tts_beat", {sentences: [keys], pause_ms, version})` — this is the beat key
  the B3 reuse rule compares (§1.6c).
- Sentence boundaries exact, words inside a sentence from `estimate_words`, `words_exact = False`. Edge keeps per-beat
  synthesis with WordBoundary; `words_exact` True when the provider's word count equals the caption tokens and no
  lexicon `spoken` override applies (§8.3). **`words_exact` is B5's "cuts" field written early** (§D 30).
- `tts_max_parallel`, retry/backoff (429) respected; an over-long sentence is split at clause punctuation.

**Spike-dependent fallback.** If spike (e) shows audible prosody resets: per-beat synthesis plus sentence boundaries
from silence detection (`gap ≥ 120 ms below -40 dBFS` matched to the sentence count; estimate if counts disagree).
Recorded in §P.

**R-37 check (moved here from B3b)** — it needs sentence-level timing; on OpenAI without it the 10-second windows
reduce to the beat average. After synthesis, a beat with any 10 s window above 185 wpm, or more than 10% above
target, is re-synthesised ONCE with the control one step slower. Never time-stretched. Indic narration targets
10 wpm lower.

**Tests**
- Sentence assembly sample-exact; pause bounds per format; classic OpenAI beats still per beat (key gate);
  `words_exact` rules; B2 event anchoring on `lesson_16x9` moves by less than R-15's ±300 ms.
- R-37: one retry, slower control, no time-stretch code path.

**Out of B3c.** R-42's definition and segment-boundary pauses stay with B4's planner and B2e's scene gaps (§D 12).

**L1 lands here** (B3a + B3b + B3c together): stored lectures are untouched (legacy gate), new Dynamic lectures get
the finished narration, and lectures generated between L0 and L1 are legacy (no calibration) and stay as they are.
Music for every Dynamic lecture is still the B1 mix until L2 (B3-cast lectures keep B2's placeholder `MusicChoice`
until B3d's music selection writes a real one, so `b3_music` is False for them — §1.0).

### B3d Music: procedural engine, selection at generation, intake, fitting, duck. Type: SHARED (orchestrator cast stage, `timeline.py`, `pipeline/base.py`, `models.py`, `requirements.txt`).

Two builders work in parallel (engine | selection + fitting) with one integrator.

**New: `aadhi/providers/music/{base,procedural,fake}.py`** (§1.8)
- `MusicProvider.generate(request) -> MusicResult`.
- The procedural engine (§5.11): numpy + scipy + stdlib `wave`; mood progressions; FM electric piano, saw pad, sine
  sub-bass; drums off in classical-focus; Schroeder/Freeverb reverb; master high-pass 35 Hz, tanh, -3 dBFS peak,
  -16 LUFS, 48 kHz stereo int16, seeded TPDF dither.
- **It renders a loopable bed, not a lesson-length track:** 3 sections (A/B/C) of 16 bars each with bar-aligned loop
  points per section, so its length does not depend on the narration (≈ 2–3 min of audio; ≤ 15 s CPU). A 15 s
  preview mode.
- `scipy>=1.17,<2` in `requirements.txt` (BSD-3); the lock unchanged; `lock_requirements.py --check` passes; scipy
  imported lazily and only in the worker.
- The fake provider returns Lyria-shaped responses, including one with lyrics.

**New: `aadhi/pipeline/music.py`** — **selection and the bed run in the cast stage at generation**, not in the asset
stage, so the result lands in the screenplay the generation writes anyway (no asset job ever writes a screenplay; no
conflict with `compare_and_set` or teacher edits):
- `select(...)`: mood weighted by subject, personality (`music_mood`, read-only) and format; key and BPM from
  `rng(look_seed, look_epoch, "music")`; never the same (mood, key, BPM ±4) as the last 3 lessons in history (R-47),
  whose signatures come from the **bed assets' `Asset.meta`** (one `IN` query on ≤ 10 keys), not from loading
  manifests.
- "Auto" order: pinned track; curated pack (none until Q9); AI music (B3h, opt-in); procedural bed.
- Intake (in the worker): ebur128 (I, LRA, TP); normalise to -16 LUFS; BPM grid; **bar-aligned loop points required
  for every source** (self-similarity; if none are found, a whole-track loop with a 2 s qsin crossfade); sections;
  licence and provenance in `Asset.meta` (R-50).
- The result is written as `Presentation.music = MusicChoice(source, provider, mood, asset_key, mode, level)`, which
  regenerate inherits (P7) and `asset_uses` authorises. **B3 never writes `source="off"` with `mode="bed"`** (the B2
  placeholder shape, §1.0): a deliberate "off" is `source="off", mode="off"`.
- The asset stage only copies the bed's analysis from `Asset.meta` into `AssetManifest.music` (no generation, no
  screenplay write) and calls `record_asset_refs`.
- `GenerationOptions.music: MusicPrefs | None`, `.sfx: bool | None`, excluded at None (§1.7).
- `GC_ASSET_KINDS += music` (music is referenced through `AssetRef`, so GC is safe).

**New: `aadhi/compose/dynamic/music_fit.py`** (pure, metadata only, no audio generated)
- `fit(timeline, manifest.music, choice) -> list[MusicCue]`: trim at a bar with a fade, or loop between the stored
  loop points with a one-bar qsin crossfade. **No extension bed**: every source has loop points.
- One bed per video; arrangement (section) changes at chapter (16:9) or act/segment (9:16) boundaries; a new bed only
  at a `builds_on=false` boundary, with a 2 s qsin crossfade.

**New: `aadhi/compose/dynamic/duck.py`** (pure)
- `envelope(timeline, choice, fmt) -> list[(t, dB)]`: speech regions from beat spans plus word spans, merged across
  gaps shorter than G (2.0 s / 1.5 s); the §5.12 level table; dense scenes (`TemplateSpec.dense`, read-only) at the
  floor with 1.5 s fades; R-45 ramps; `gaps_bookends` when ≥ 30% of the runtime is dense.
- `gain_at(duck, t)` uses the frozen curve and floor (§1.9).

**`compose/timeline.py`.** ONE lazy call in the existing Dynamic branch: when `b3_music(presentation)`, it **always**
writes a `MusicPlan` — `mode="off"` with empty cues and an empty duck when the choice is off or no `MusicAsset` is
present — and `Timeline.sfx` (B3f). When `b3_music` is False (B2-era and placeholder lectures), `Timeline.music` stays
None and the B1 mix plays (O-B9). Within the 300 ms / 40-scene budget; the isolation probe's Dynamic run proves no
scipy and no `providers.music.*`.

**Tests**
- Envelope breakpoint goldens; think-time level never above the under-speech level; a 10 s insert leaves no gap.
- Loop crossfades equal-power (summed power within ±0.5 dB).
- Procedural determinism per seed (same-platform sha; cross-platform within 1 LSB RMS, §3.2); R-46 tempo bounds.
- Lyria-shaped fake with lyrics rejected at intake; a track without detectable loop points gets the whole-track loop.
- Selection: two consecutive lessons differ in (mood, key, BPM ±4); regenerate keeps `asset_key`.
- Music off → `MusicPlan(mode="off", cues=[], duck=[])`; placeholder → `music is None`.
- `AADHI_MUSIC_BENCH=1` opt-in for the "≤ 20 s per 5 min" bound; not in CI.

### B3e The mix, master chain and audio preflight. Type: SHARED + OUTPUT for Dynamic MP4.

**Audible result.** The MP4 has the real bed (or none, when off), correct ducking and R-48 loudness.

**New: `aadhi/compose/dynamic/mix.py`** (§1.10) — **chunked**, so memory does not scale with lesson length:
- Processed in 10 s blocks at 48 kHz float32, written to a raw `f32le` scratch file in the render workspace; stems
  per block: narration (`scene_audio` at `scene.start + audio_offset`, resampled 24→48 kHz with `resample_poly`,
  decoded per scene on demand); music (cues, gains in LU relative to narration, 100 Hz high-pass, -3 dB at 2.5 kHz
  Q 1.2, fades 1.0 / 3.0 s for 16:9 and 0.3 / 1.5 s for 9:16, the duck); SFX (B3f).
- Peak memory bound ≤ 300 MB for any timeline up to 120 min; a longer timeline is refused with a clear error
  (`target_minutes` max is 90).
- Master chain: two-pass `loudnorm` in **linear mode only** (I = -14 messaging/youtube, -16 lms/lesson_page; TP -1.5;
  LRA 9); if linear cannot reach the target: static gain plus `alimiter=limit=0.84:attack=5:release=50:level=0`;
  `aresample=48000`; AAC-LC 384 kbps. **Never loudnorm's dynamic mode** (fails on ffmpeg 5.1): re-scope that test and
  add a container-marked test of this chain on 5.1.
- `MIX_VERSION`; the mix key hashes the audio-relevant timeline fields, the asset keys and the parameters.

**Engine seam** (`motion/engine.py` `_build_master`, `encode.py`)
- Branches ONLY on `timeline.music is not None` — which, by B3d's rule, means exactly "a B3 audio timeline" (a
  `MusicPlan` is always written for B3-music lectures, mode `off` included). On that branch the classic per-scene
  ticks, ding and branding BGM are **not** used; R-49's `countdown_tick` / `answer_chime` replace them (B3f, same
  landing).
- A timeline without `music` keeps today's B1 mix byte for byte.
- The mix runs **in preflight, before capture**, so its memory never overlaps Chromium.

**Preflight audio checks** (one registration line in `dynamic/preflight.py`; logic in new
`compose/dynamic/preflight_audio.py`)
- "Music under speech": R-44 20 LU floor and ≤ 3 LU variation; remedy: lower the music gain.
- "Speaker levels": per-persona loudness spread ≤ 2 LU (warning).
- "Master loudness" on the encoded AAC: one automatic re-run with a corrected gain, then a warning.
- **`container.py` receives a `LoudnessTarget` only on the B3-mix branch**; a B1-mix render's container QA is
  unchanged (no loudness measured, no new problem). `qa.loudness` filled on the B3 branch.

**Tests**
- Mix within ±1 LU on the fixtures; music under speech ≥ 20 LU below narration, ≤ 3 LU variation; true peak
  ≤ -1.0 dBTP on the encoded file.
- **Music off:** the MP4 has narration and R-49 SFX only — no branding BGM, no classic ticks or ding (stem check).
- A timeline without `music` produces the B1 master byte for byte (same filtergraph and command), and its
  `ContainerQA` equals today's.
- Chunked mix equals a whole-buffer reference mix on a 2-minute fixture (≤ 1e-6); the `long_16x9` 60-minute
  timeline mixes under the memory bound (slow-marked, `tracemalloc` + RSS).
- LU assertions use the numpy meter; ffmpeg ebur128 is a container-only cross-check (§3.2).

**Docs.** OPERATIONS: the render-worker memory note (chunked, ≤ 300 MB, 120 min refusal).

### B3f SFX (R-49) and the sonic logo. Type: SHARED (`compose/sounds.py` is shared with classic; `timeline.py`).

**`compose/sounds.py`**: append only.
- New synthesised sounds `bumper_whoosh`, `answer_chime`, `countdown_tick`, `accent`, `sonic_logo` (§1.11), parameters
  from the personality's `sfx_pack` (read-only), seeded per video, keys under a new `SFX_VERSION`.
- `tick_wav`, `ding_wav`, `SOUND_VERSION`, `TICK_KEY`, `DING_KEY` untouched (B3-0 gate).

**New: `aadhi/compose/dynamic/sfx.py`** (O15)
- Reads B2's `TimedScene.events` and `TemplateSpec.dense`; does **not** edit `events.py`.
- Writes `Timeline.sfx`: whoosh with a chapter bumper's wipe; chime on an answer reveal; one soft tick 150 ms before
  the reveal at the end of a countdown; the optional accent **only in explanation scenes**, at most once per 20 s
  (16:9) or 8 s (reels), never in dense scenes.
- Each sound ≤ 1 s, placed in a word gap at least the sound's length, never within ±150 ms of a key term.
- **Levels: -16 to -24 dB under narration** (R-49).
- `presentation.sfx` False → no SFX at all, the sonic logo included. Music mode has no effect on SFX.
- The sonic logo (interim, O18) plays at t = 0 under Aadhi's **silent** intro and at the end of every reel. It changes
  no timing: Aadhi's speech start (logo + 0.8 s) belongs to B3i's presenter seam.
- Called from the same lazy line in `build_timeline` as B3d. `mix.py` gains the SFX stem. `VisualEvent.sfx` stays
  unused (§D 7).

**Tests** — every R-49 placement rule; levels within -16..-24 dB; accent only in explanation scenes;
`sfx_per_minute`; none in dense scenes; off switch silences the logo too; determinism per seed; classic tick/ding
bytes unchanged.

### B3g Live-player audio. Type: SHARED (player) + OUTPUT for Dynamic live playback.

**New: `web/js/player/dynamicAudio.js`**
- `MusicBus`: `<audio crossorigin="anonymous">` → `MediaElementAudioSourceNode` → `GainNode`; gain automated from
  `Timeline.music.duck` with `setValueCurveAtTime`, sampled at 100 Hz from the frozen curve and floor; re-synced on
  seek, rate change and resume; never decodes the whole track.
- `SfxBank`: short AudioBuffers fetched same-origin. AudioContext resume on the first gesture.

**`player.js`**
- `MusicBus` and `SfxBank` are constructed **only when `live` (never in render/capture mode) AND Dynamic AND
  `timeline.music` is present**. Render mode never constructs them: the MP4's audio comes from the mix.
- On that branch **`this.bgm` (the classic `BackgroundMusic`) is not constructed and the beat ducking at `:788` does
  not run**, so only one music source can play. With `mode="off"` no `MusicBus` is constructed.
- Without `timeline.music` (classic, flag-off, B2-era) the path is today's, unchanged.

**`controls.js`**
- "Music: Off / Low / Normal", only for Dynamic timelines with a `MusicPlan` whose mode is not off (WCAG 1.4.7).
- **Levels do not stack** (§1.9): viewer Low caps the under-speech music level at -26 LU; a teacher's
  `level="low"` lesson is already there, so viewer Low changes nothing on it.
- The choice is kept in `localStorage` (try/catch). Emits `music_level` through the analytics hook if one exists;
  otherwise a B4 item.

**Parity vectors** — `scripts/gen_duck_vectors.py --check` writes `web/tests/player/duck_vectors.gen.json` from
`duck.gain_at`, floor cases included; JS `gainAt` within 0.05 dB (exactly 0 at the floor). Added to CI's "Generated
files are current" step (a shared CI edit, §4.4).

**Docs.** OPERATIONS §4.4: the CORS rule. CSP unchanged.

**Tests**
- npm: parity vectors; Off/Low/Normal gains and the no-stacking rule; seek re-sync; a Dynamic timeline with a
  `MusicPlan` constructs no `BackgroundMusic`; mode off constructs no `MusicBus` and no `BackgroundMusic`; render
  mode constructs neither bus; classic player tests unchanged.
- Playwright smoke on B3's `lesson_16x9` fixture (headless Chromium cannot judge audibility): assert the
  `GainNode`'s scheduled automation values against the vectors at sampled times, and the music element's
  `currentTime` within 50 ms of the timeline after a seek.

**L2 lands here** (B3d + B3e + B3f + B3g together): MP4 and live player switch at the same commit, and the tick/ding
replacement arrives with the mix that drops the classic ones. No interim state is shipped.

### B3h Sources, previews, money and their API surfaces. Type: SHARED (keys, models, `main.py` router registration, payers).

**Music sources**
- `providers/music/upload.py`: a teacher upload with a rights confirmation and an "instrumental" attestation;
  provenance stored; intake (scipy) runs in a worker job `music_intake`, never in the API.
- `providers/music/lyria.py`: opt-in (`MusicPrefs.ai_opt_in`) and the Gemini key; the fixed instrumental prompt
  template; at most 3 cues (lesson) or 2 (reel), plus at most 1 retry; any result with non-empty lyrics is rejected;
  **disabled by default until Q2 and Q3** (O10, O13).

**New router `aadhi/api/routers/audio.py`** (B3-owned; one `include_router` line in `main.py`):
- `POST /api/projects/{id}/music` (multipart): requires `rights_confirmed` and `instrumental` true; stores a `music`
  asset with provenance in `Asset.meta`; records the `AssetRef`, so `asset_uses` authorises the key; enqueues
  `music_intake`. Size and type limits as the existing upload routes.
- `GET /api/meta/voice-samples`: persona → public `voice_sample` asset URL. Samples are **pre-generated** by an
  owner-run script (`python -m aadhi.providers.tts.samples --generate`, O0/O7), never on request, so no request can
  spend the server key.
- `POST /api/versions/{vid}/hear-terms`: enqueues a `hear_terms` job (≤ 40 words: the hook plus glossary terms, in
  the cast voice, charged to the lecture's payer); the existing job limits (`jobs/limits.py`) are its rate limit.
- The Studio UI for all three is B4's.

**Money**
- Usage rows with `purpose` = `voice_sample` and `hear_terms`; budget pre-checks via `_voice_payers` /
  `_music_payers`, now fed by real `VoicePrefs` / `MusicPrefs`.
- **`voice_sample` and `calibration` are NOT in `GC_ASSET_KINDS`** (no document or `AssetRef` references them, and
  `AssetRef` needs a project): their count is bounded by personas × models × versions (§1.12).

**Disclosure** — `disclosure.providers(manifest) -> list[str]` only. The `credits.txt` line is B4's export bundle;
the outro text and description draft are B2 (`outro_next`) and B4 (O6).

**Keys** — `CREDENTIAL_PROVIDERS` changes only if O14 keeps ElevenLabs in B3. Default: unchanged.

**Tests** — upload refused without the confirmations; an unauthorised `asset_key` refused; music pre-checked against
the budget on the server key; unknown operations charged; Lyria fake rejection and retry bound; sample and
Hear-your-terms usage rows; hear-terms rate-limited by the job limits; GC sweeps `music` and leaves `voice_sample` /
`calibration`; no `scipy` in the API process.

### B3k The successor voice provider (O2). Type: SHARED (`providers/tts`, `factory`, `pricing`). Conditional on O2; lands by 2026-12-15.

- The adapter O2 picks, behind the same `TTSProvider` Protocol, `style` support and per-sentence path.
- Persona ids stay `"openai:<voice>"` when the successor is OpenAI's own path (model `None` = the configured model),
  so stored casts stay valid; otherwise a `resolve_cast_id` mapping from each OpenAI persona to a successor persona of
  the same gender and energy, frozen in `personas.py`.
- Calibration re-runs once per (persona, successor model) (cents, O7); the `wpm=N` control grammar is unchanged.
- Reuse after the switch: an unchanged scene keeps its stored audio (the stored file is the truth); an edited scene is
  voiced on the successor in the same voice name. The owner listens to one mixed lecture before L4.
- Aadhi's brand voice (O9) is chosen from voices the successor offers, so it does not change on 2027-01-06.
- If O2 places the successor outside B3, this step is dropped and §D records the risk and the date.

### B3i Aadhi speaks. Type: SHARED (B2 `presenter.py`, schema escalation) + OUTPUT for Dynamic intros. Only if O19 keeps it in B3.

- **Schema escalation (§1.13):** `AssetManifest.presenter: dict[Literal["intro","recap"], SceneAudio] | None`,
  excluded at None, via `python -m aadhi.schemas.generate --check`, coordinated with the B2 lead (generated JSDoc).
  The asset stage voices the appearance lines (they are not screenplay beats) into it.
- Lines: `aadhi/compose/dynamic/aadhi_lines.py` (pure data, under the isolation prefix): greeting and sign-on pools,
  English first. The greeting is `Presentation.greeting_id` (exists; chosen excluding the last 2 used, from history,
  at the cast stage); the sign-on is derived from `rng(look_seed, look_epoch, "signon")` — no new screenplay field.
- Aadhi's fixed brand persona (O9) is an `app_settings` flag `aadhi_persona` (`runtime_flags.FLAGS`, no migration),
  with an environment default; keyless fallback fixed (default `edge:en-IN-PrabhatNeural`) with a warning.
- `presenter.py` seam (one call, AUDIT, with the B2 lead): `t_exit` = last Aadhi word + 0.3 s, clamped to 5–8 s,
  `aadhi.too_long` past 7.1 s; **speech starts 0.8 s after the sonic logo** (the only timing change, here, not B3f).
- The narrator never calls itself Aadhi (a test on the line pools).

### B3j Acceptance, docs and merge. Type: gate run + owner sign-off.

**Fixtures rendered through the real engine:** B3's `lesson_16x9` (bed), `reel_9x16`, `dense_16x9`
(`gaps_bookends`), plus B2g's kitchen-sink if it has landed. All audio preflight checks pass.

**History acceptance**, fake TTS and the DB: two consecutive rendered lessons by one teacher differ in narrator
speaker and in music; regenerating one keeps both; a B2-era lesson is untouched by every B3 landing.

**Owner artefacts** (in `docs/plans/b3/acceptance.md`): the listening checklist (voices clear, music never masks
speech, no pumping, no clicks, levels equal across voices); the blind test of the procedural bed against the owner's
reference track (O20) — moved here from the design's §5.11 "B4 gate".

**Docs** — the design's B3 as-built notes and every amendment in §D (the full list is §D 31); OPERATIONS (OpenAI
primary, Edge fallback rules, the successor and its date, CORS, memory, flags, usage rows, listening procedure,
opt-in benchmarks); `.env.example` regenerated; `HANDOFF.md` for B3.

**Land:** L6, the final merge into `overhaul/v2` (§4).

## P. Policy from the B3-0 spike (FILL IN; until filled, the defaults in brackets bind)

| Question | Result | Policy |
|---|---|---|
| (a) Voices accepted by gpt-4o-mini-tts | _tbd_ | Personas for accepted voices ∩ the successor's (O8). [Default: the 9 catalogued, narrator-enabled only where the successor offers them] |
| (b) `speed` re-paces naturally within 0.90–1.10? | _tbd_ | If yes: OpenAI control `speed=<x.xx>`. If no or unclear: speed fixed at 1.0 [default] |
| (c) `instructions` wpm control spread | _tbd_ | If 3-repeat spread ≤ ±6 wpm: control `wpm=<N>` via instructions + measure-and-retry once. [Default: wpm=155 instruction, retry once] |
| (d) §5.6 styles via instructions | _tbd_ | Style rows sent as instructions [default on]. A row whose LUFS shift > 2 LU is softened |
| (e) Per-sentence joins | _tbd_ | Per-sentence [default] or silence-detected boundaries (B3c) |
| (f) Indian-English instructions | _tbd_ | Feeds O1 |
| (g) LUFS and peak spread | _tbd_ | Gains clamp ±12 dB and the -1 dBFS headroom cap [default] |
| (h) Byte-determinism | _tbd_ | Cache is the truth either way |
| (i) Gemini 3.8 comparison | _tbd_ | Feeds O2 |
| (j) Successor candidates | _tbd_ | Feeds O2, O8, O9; the control grammar and persona ids must hold on the pick |

## 1. Contracts (frozen by B3)

Schema fields that already exist from B0 are frozen **as they are**. B3 adds **one** schema field, in B3i only (§1.13).
Any other change found necessary is a SHARED step with `python -m aadhi.schemas.generate --check`, coordinated with the
B2 lead because the generated JSDoc conflicts.

### 1.0 Predicates and legacy markers (B3-0; `aadhi/compose/presentation.py`)
- **`is_dynamic(presentation, settings) -> bool`** = `presentation is not None and dynamic_enabled(settings)`. The ONE
  predicate: `timeline.py` (build), `assets.py` (voicing), the orchestrator's cast stage and translate re-cast all
  call it. Never `presentation is not None` alone.
- **`b3_voiced(presentation) -> bool`** = `bool(presentation.voices.calibration)`. A B3 cast always has the narrator's
  calibration (§1.5); B2-era casts have none.
- **`b3_music(presentation) -> bool`** = `not (music.source == "off" and music.mode == "bed")`. `(off, bed)` is B2's
  placeholder shape (`MusicChoice(source="off")` with the default mode); B3 never writes it.
- **Voicing:** `b3_voicing = is_dynamic and b3_voiced`. Everything else is voiced by today's code exactly.
- **Music:** a timeline gets a `MusicPlan` iff it is Dynamic and `b3_music`; otherwise `music is None` → the B1 mix.
- **Flag flips after a build.** The predicate is evaluated when the asset build runs. A lecture voiced while Dynamic
  was on and later served with the flag off keeps its stored narration (the timeline is classic, the audio is the
  lecture's content), the same "no rebuild needed" rule the flag already has for visuals. A rebuild with the flag off
  voices it classic (keys differ → re-synthesis). Recorded in OPERATIONS.

### 1.1 `VoicePersona`
A code catalogue in `aadhi/providers/tts/personas.py`, not a stored schema. A frozen `StrictModel`:
- `id: str`: `"<provider>:<voice>[@<variant>]"`, ≤ 80 characters (`"openai:marin"`, `"edge:en-IN-NeerjaNeural"`,
  `"openai:coral@en-IN"` if O1 (b)). **The id names no model**, so it stays valid when the configured model changes
  (B3k).
- `speaker_id: str` (variants share it; Edge `"ms:<name>"`, OpenAI `"openai:<voice>"`).
- `provider: Literal["edge", "azure", "gemini", "openai", "elevenlabs", "chirp", "sarvam", "fake"]`; `voice: str`;
  `model: str | None` (None = the provider's configured model); `language: str`; `reads: tuple[str, ...]` (OpenAI
  personas read every catalogue language); `accent: str` (OpenAI `"en-US"`, fable `"en-GB"`, `@en-IN` variants);
  `gender: Literal["female", "male", "neutral"]`; `energy: Literal["calm", "neutral", "energetic"]`;
  `roles: frozenset[Literal["narrator", "aadhi", "student"]]`; `formats: frozenset[Literal["landscape", "portrait"]]`;
  `style_prompt: str = ""`; `tags: tuple[str, ...]`; `enabled: bool`; `paid: bool`;
  **`successor_ok: bool`** (offered by the O2 successor; only these are castable for new lectures).

### 1.2 Cast ids in stored lectures
`VoiceCast.narrator / .aadhi / .student` hold persona ids (schema unchanged). `resolve_cast_id(raw, provider_hint)`:
1. a `:` in `raw`: a persona id;
2. a bare id (B2's slice): the persona whose `voice == raw` within `provider_hint` (the stored
   `GenerationOptions.tts_provider`, else the server's narrating provider), else the unique catalogue owner;
3. neither: the narrating provider's language default persona, with a warning.
Stored lectures are never rewritten. B2-era lectures are not voiced through personas at all (§1.0); the reader is for
B3 casts and for O-B9's regenerate path.

### 1.3 `VoiceCalibration` (schema unchanged)
`natural_wpm` 60–300; `control` ≤ 200 characters, **frozen per provider**:

| Provider | `control` |
|---|---|
| Edge / Azure | `"+8%"` (signed integer %, \|x\| ≤ 25) |
| OpenAI and its successor | `"speed=1.05"` (§P b) or `"wpm=155"` (instructions target, §P c) — model-independent |
| ElevenLabs | `"speed=0.95"` (0.7–1.2) |
| Gemini | free style text |

- The calibration asset key is `compute_key("calibration", {provider, model, voice, text_id, CALIBRATION_VERSION})`;
  its `Asset.meta` holds `{natural_wpm, lufs, peak_dbfs, model}` (the gains' source, §1.6b).
- `VoiceCast.calibration` holds at most 20 entries (unchanged).

### 1.4 History (landed at L0)
`history.recent(session, *, user_id, exclude_project_id, fmt: Literal["landscape","portrait"] | None, limit=10)
-> list[HistoryEntry]`. `HistoryEntry` is a frozen dataclass:

| Field | Meaning |
|---|---|
| `project_id`, `version_id`, `fmt`, `finished_at` | |
| `course_key` | `casefold(subject_name.strip())` |
| `narrator_persona`, `narrator_speaker`, `narrator_accent`, `narrator_gender` | via `resolve_cast_id` |
| `music_source`, `music_asset_key` | from `Presentation.music` |
| `music_signature` | `(mood, key, bpm)` from the bed asset's `Asset.meta`, or None (B2-era, off, unknown) |
| `theme_family`, `personality`, `display_font`, `caption_style`, `hook_kind`, `greeting_id`, `intro_layout` | |
| `pins` | `{pin: value}` |

Rules:
- **Counts** only a version with a `Render` row in its completed status with a `video_asset_key`. Drafts, failed or
  cancelled renders and unrendered duplicates never count.
- **Order:** newest first by `Job.finished_at` via `Render.job_id`; when `job_id` is NULL (job cleanup sets it NULL)
  by `Render.created_at`; ties by `version_id` descending. Completion is read from `Render` itself, so a cleaned job
  never hides a render.
- **Which screenplay:** the version's current screenplay. The fields history reads are presentation fields that only
  casting, Fresh look and pins change (an edit after rendering does not change them), so reading the current one is
  "what the lesson is now". Recorded; revisit if B4 lets teachers edit the voice without a re-cast.
- **Scope:** Dynamic lessons only (`presentation` present); classic lessons do not enter rotation (O21).
- **Cost:** ≤ 3 queries per call; ≤ 2 × `limit` screenplays loaded (JSON column); measured on the dev DB in B3-0; if
  over 200 ms, presentation fields are read with a JSON path expression instead.
- Read only at generation, translation, Fresh look, New colours and New layouts; never at rebuild.

### 1.5 Casting
`casting.cast(*, language, fmt, reel_profile, prefs: VoicePrefs | None, usable: set[str], history, look_seed,
look_epoch, parent: VoiceCast | None, exclude_speakers: set[str] = frozenset(), accent_policy) -> VoiceCast`.
- `usable` = providers whose key resolves (`credentials.resolve_keys`), plus Edge.
- **Pool:** personas matching `language` or `reads`, role narrator, format allowed, `enabled and successor_ok`; the
  narrating provider only, unless `any_provider`; paid personas only if the narrating provider is paid or
  `premium_allowed`.
- Order `(prefer_opposite_gender desc, speaker_last_used asc, rng)`, `rng = sha256(f"{look_seed}:{look_epoch}:voice")`,
  never `hash()`. Exclusion covers the last `min(3, speakers(group) - 1)` speakers. A pin bypasses rotation.
- **Inheritance:** `parent` voices are returned unchanged unless `look_epoch` grew; **calibration is then filled for
  any cast persona that lacks it** (so an inherited cast always ends `b3_voiced`). A legacy (B2-era) parent follows
  O-B9.
- **Calibration is always present for the narrator:** if `calibrate` fails, the persona's `DEFAULT_CALIBRATION` table
  value is stored (and a warning), so `b3_voiced` never depends on a provider being up.
- The student: an adult persona of the opposite gender, same provider, energy ≥ neutral, or None. Aadhi: the brand
  flag's persona.

### 1.5b The cast stage
`cast_stage(ctx, sp, options, *, parent, history_entries)` runs in the worker (`orchestrator`), after "script",
checkpointed; it writes `presentation.voices` (B3b) and `presentation.music` (B3d) and, at B3i, `greeting_id`. It is
the only writer of these at generation; rebuilds never call it.

### 1.6 Dynamic voice fallback
- `fallback_persona(persona, language)`: the Edge persona of the same gender, the language default first;
  deterministic.
- Lecture-wide for B3-voiced lectures, recorded in every `BeatAudio.persona`. Control Edge `"+0%"`; its calibration is
  not written to `Presentation` (§D 5).
- Taken in `_tts_provider` (keyless) and on the pre-fan-out probe (`ModelUnavailable`), never mid-fan-out.
- Which voice a REBUILD uses when the stored manifest was voiced by the fallback and a key now exists follows O17's
  answer (recommended: the stored voice is kept until the teacher re-voices).
- Classic and legacy keep H0's / `tts-openai`'s per-build `fallback_voice_config` exactly.

### 1.6b Speaker gains
- `gain(persona) = clamp(-16 - lufs_cal, -12, +12)`, then `min(gain, -1 - peak_cal)`; applied in float, soft-limited
  at -1 dBFS, before the s16 conversion.
- Frozen per lecture: previous manifest's `speaker_gains` → calibration asset meta → re-calibrate.

### 1.6c Reuse of B3-voiced scenes
A B3-voiced scene is reusable when: the scene hash matches; the stored `SceneAudio.provider/voice` equal the lecture's
effective **narrator** persona's; per beat, `BeatAudio.persona` equals the beat's effective persona and
`BeatAudio.clip_asset_key` equals the B3 beat key (the per-sentence `tts_beat` key, or the styled per-beat key on
Edge); and the stored `speaker_gains` equal the lecture's frozen gains. Classic and legacy scenes use today's
`_reusable` unchanged.

### 1.7 `GenerationOptions` additions (`aadhi/pipeline/base.py`; every field excluded at None)
```
class VoicePrefs(StrictModel):
    rotate: bool = True
    narrator: PersonaId | None = None                     # pin, "provider:voice" (what _voice_payers parses)
    accent_policy: Literal["local_only", "mostly_local", "any"] | None = None   # None = server default (O1)
    premium_allowed: bool = False
    any_provider: bool = False
class MusicPrefs(StrictModel):
    source: Literal["auto", "generated", "library", "upload", "procedural", "off"] = "auto"
    track: str | None = Field(default=None, max_length=128)      # asset key, authorised through asset_uses
    mood: Literal["calm_focus", "bright_curious", "cinematic_wonder", "classical_focus", "upbeat_drive"] | None = None
    mode: Literal["auto", "bed", "gaps_bookends", "off"] = "auto"
    level: Literal["normal", "low"] = "normal"
    ai_opt_in: bool = False
GenerationOptions.voice: VoicePrefs | None = None ; .music: MusicPrefs | None = None ; .sfx: bool | None = None
```
- `tests/invariance/goldens/_api/generation_options.json` stays byte-identical.
- `tts_rate` on a B3-voiced lecture: ignored for pacing with an info issue (B3a); B4's validator refuses it.

### 1.8 Music
- **Unchanged schemas:** `MusicChoice`, `MusicCue`, `MusicPlan`, `MusicAsset`.
- **Frozen moods and BPM:** calm_focus 70–90; bright_curious 90–105; cinematic_wonder 70–90; classical_focus 60–80;
  upbeat_drive 100–120, Social reels only. Procedural tempo 64 / 78 / 104 ±8, never above 110 BPM.
- `MusicProvider.generate(MusicRequest{mood, key, bpm, sections, seed, prompt?}) -> MusicResult{wav_bytes, provider,
  model, prompt_hash, seed, licence, lyrics: str | None}`.
- Keys: `sha256(provider, model, prompt_or_params, seed, sections, PROCEDURAL_VERSION)`.
- **Every source has loop points** (bar-aligned per section, or the whole-track crossfade loop). `music_fit` only
  trims and loops; nothing generates audio after the cast stage.
- **Persistence:** the choice lives in `Presentation.music` (written by the cast stage); the analysis in the bed's
  `Asset.meta`, copied to `AssetManifest.music` by the asset stage. The generated file is the source of truth and is
  never regenerated on a re-render.
- **Off:** `MusicChoice(source="off", mode="off")` → `MusicPlan(mode="off", cues=[], duck=[])`. The B2 placeholder
  `(off, bed)` → no `MusicPlan` (§1.0, O-B9).
- `MusicAsset.licence` and `Asset.meta` hold the provenance (R-50).

### 1.9 The duck curve
- `Timeline.music.duck`: `(t, dB)` breakpoints, `t` absolute rounded to 1 ms, dB rounded to 0.01, sorted, `t` strictly
  increasing.
- **Floor:** `DUCK_FLOOR_DB = -120.0`. Every "silence" (dense scenes, `gaps_bookends` under speech, mode off ramps,
  viewer Off) is written as -120.0 — finite, so `StrictModel` (`allow_inf_nan=False`) accepts it — and any
  `g ≤ -120.0` means power exactly 0.
- Between `(t0, g0)` and `(t1, g1)`, with `p(g) = 0 if g ≤ DUCK_FLOOR_DB else 10^(g/10)` and
  `u = (t - t0) / (t1 - t0)`: `p(t) = p0 + (p1 - p0) · sin²(π/2 · u)`; amplitude `√p(t)` (equal-power).
- This is ONE function, `duck.gain_at`, mirrored by JS `gainAt` with the same floor rule. Parity pinned by the
  generated vectors, floor cases included, frozen before the vectors are first generated.
- **Levels:** teacher `level="low"` subtracts 4 LU from every breakpoint above the floor (-26 LU under speech).
  Viewer "Low" applies `offset = min(0, -26 - L_speech)` where `L_speech` is the plan's under-speech level, so the two
  never stack. Viewer "Off" is gain 0.

### 1.10 Mix
- `mix.render(timeline, manifest, store, *, work, target: LoudnessTarget) -> MixResult{path, key, lufs, true_peak,
  lra, music_gap_lu, music_var_lu, reruns}`; chunked in 10 s blocks; ≤ 300 MB; timelines > 120 min refused.
- `MIX_VERSION` is part of the key.
- The test meter is `aadhi/compose/bs1770.py` (numpy/scipy K-weighting and gating), in the isolation FORBIDDEN list;
  ffmpeg ebur128 is the production and container measure.

### 1.11 SFX
- Closed set `{"bumper_whoosh", "answer_chime", "countdown_tick", "accent", "sonic_logo"}`; `SfxCue.sound` takes only
  these (enforced in `sfx.py` with a test, no schema change).
- Levels -16 to -24 dB under narration; accent only in explanation scenes; `presentation.sfx` False silences all,
  logo included.

### 1.12 Usage and assets
- TTS usage rows carry `purpose ∈ {narration, calibration, voice_sample, hear_terms}` in `meta`; operation `tts`.
  `characters = len(input)`; `meta.instruction_chars = len(instructions)`, not priced at the per-character audio rate
  (instructions are text tokens at about $0.60 per 1 M; counting them as audio characters would roughly double the
  recorded per-sentence cost and trip budget pre-checks falsely).
- Music rows use `operation="music"`.
- `PRIVATE_ASSET_KINDS += calibration`. `GC_ASSET_KINDS += music` only. `voice_sample` and `calibration` are
  **excluded from GC** (unreferenced by design, bounded in number, regenerating them spends the server key).

### 1.13 Schema escalations
- B3i only: `AssetManifest.presenter: dict[Literal["intro","recap"], SceneAudio] | None`, excluded at None.
- `BeatAudio.words_exact` is written by B3c on the Dynamic path (an existing B5-feature field, §D 30).

## 2. Rules wired (owner module + test)

| Rule | Module | Test (step) |
|---|---|---|
| R-36 rate 155 (150–160), Social reels 170 | `calibration.control_for`, `delivery` | calibration maths; measured wpm on the fake (B3b) |
| R-37 Indic -10 wpm; ≤ 185 wpm per 10 s; re-synthesise, never stretch | `assets` B3 post-check | one retry, slower control; no time-stretch code path (B3c) |
| R-38 one primary narrator; secondary ≤ 15% | casting; `secondary_share` (B4 lint) | one narrator; lecture-wide fallback (B3a/b) |
| R-39 rotation, accent policy, deterministic, pinnable | `casting` + `history` | §10.3 casting tests; policy per O1 (B3b) |
| R-40 neural/HD only, no novelty voices | `personas` (enabled, roles, successor_ok) | AnaNeural / ta-LK/MY/SG excluded; owner-approved OpenAI list (B3a) |
| R-41 -16 LUFS per speaker, one gain baked in | calibration gains + `audio.build_scene_audio(gains)` | frozen per lecture; headroom; key scope (B3b) |
| R-42 pauses | sentence pauses (B3c); definition and segment pauses B4 / B2e | bounds per format (B3c) |
| R-43 instrumental only | `pipeline/music` intake | Lyria-shaped fake with lyrics rejected (B3d/h) |
| R-44 levels, think time, dense off, `gaps_bookends` default | `duck.envelope` | goldens; think ≤ under-speech; ≥ 20 LU gap (B3d/e) |
| R-45 ramps, equal-power, ≤ 3 LU variation | `duck`, `preflight_audio` | breakpoint goldens; variation check (B3d/e) |
| R-46 tempo | `procedural`, `select` | BPM bounds (B3d) |
| R-47 no repeat in 10 lessons; one bed per video; boundaries; 2 s qsin | `select`, `music_fit` | history exclusion; crossfade power (B3d) |
| R-48 master -14/-16 ±1, TP ≤ -1.0, LRA ≤ 9 | `mix`, `preflight_audio`, `container` (B3 branch only) | ±1 LU on fixtures; TP on the encoded file (B3e) |
| R-49 SFX set, -16..-24 dB, density, placement, accent in explanation only, off switch | `dynamic/sfx` | placement and level rules; `sfx_per_minute` (B3f) |
| R-50 provenance and licence | intake, `Asset.meta`, `MusicAsset.licence` | every music asset has a licence (B3d/h) |
| R-07 / R-44 no rise in think time | `duck` | think-time test (B3d) |
| R-12 / R-13 Aadhi limits from word timings | `presenter.py` seam | `aadhi.too_long` past 7.1 s (B3i) |
| R-51 reads the same history | `history` interface | frozen interface test (B3-0) |
| R-60 AAC-LC 48 kHz stereo 384 k | `mix` encode | container QA (B3e) |
| §5.12 live = MP4 breakpoints | `duck.gain_at` / `gainAt` | generated vectors `--check`, floor included (B3g) |
| P7 regenerate keeps voice and music | cast-stage inheritance; `Presentation.music.asset_key` | end-to-end regenerate test (B3b/d, B3j) |

Eval metric functions provided in B3, wired by B4: `speech_wpm_p90`, `speech_wpm_max_10s`,
`music_under_speech_lu_gap`, `music_level_during_think`, `sfx_per_minute`, `voice_differs_from_previous`,
`local_accent_share`, `music_not_recent`.

## 3. Classic invariance, stored-lecture and determinism safeguards

### 3.1 Classic invariance (binding; each item has a gate)
1. **Keys.** The classic `clip_key` payload gains nothing; `style`, `persona`, sentence unit and gains enter a key only
   on the B3 voicing branch. `scene_audio` key, `AUDIO_VERSION`, `KEEP_PAD_SECONDS`, `INTER_BEAT_GAP_SECONDS`,
   MP3 64 k unchanged. Gate: `tests/invariance/test_audio_keys.py` (B3-0).
2. **Requests.** The OpenAI request without a style is byte-identical; `tts-1*` never gets `instructions`. Gate: the
   adapter request-dict tests.
3. **Sounds.** Tick/ding bytes, `SOUND_VERSION`, `SOUND_KIND`, `TICK_KEY`/`DING_KEY` unchanged; new sounds append.
   Gate: sha test.
4. **ffmpeg builders.** `ffmpeg.bgm_filter`, `audio_mix_filter`, `build_final`, `video.py`'s `TICK_VOLUME` /
   `DING_VOLUME` / `render_bgm` not edited. Gate: the `segments.json` golden.
5. **Player.** Classic `BackgroundMusic`, `SoundEffects` and ducking unchanged; new code only when live + Dynamic +
   `timeline.music`. Gate: `web/tests/player` plus a source-diff check in review.
6. **Imports.** No B3 module and no scipy in a classic build, a classic generation, the API process, or a Dynamic
   `build_timeline`. Gate: the extended isolation probe (§B3-0). Preview `build_timeline` ≤ 300 ms for 40 scenes.
7. **`GenerationOptions`.** New fields excluded at None. Gate: `tests/invariance/goldens/_api/generation_options.json`.
8. **The predicate.** Per-sentence synthesis, delivery styles, gains, personas and the lecture-wide fallback run
   only when **`b3_voicing(sp, settings)` = `is_dynamic(sp.presentation, settings) and b3_voiced(sp.presentation)`**
   (§1.0). A presentation with the flag off is classic. Gates: the flag-off gate (B3-0) and the existing
   `test_assets.py` fallback tests.
9. **The `TTS_PROVIDER` default flip** (`tts-openai`) re-voices *new* classic lectures by owner order; a product
   change outside the goldens, not B3's. Stored lectures are O17, `tts-openai`'s scope; B3-0's xfail test pins it.
10. **Classic disclosure.** No new text in classic videos (O6).
11. **Meta.** The classic voice and language lists in `GET /api/meta` are unchanged. Gate: `meta_voices.json` (B3-0).

### 3.1b Stored Dynamic lectures (binding)
- **B2-era lectures** (no calibration, placeholder music) are voiced and mixed by today's code until the teacher
  regenerates or uses Fresh look, whatever the landing. Gate: the legacy gate (B3-0), run at every step.
- **B3-voiced lectures** never re-synthesise an unchanged beat on rebuild (§1.6c), and gains never move on an edit
  (§1.6b), so a rebuild does not drift (E-31). Gate: B3a/B3b reuse tests.
- No `ENGINE_VERSION` bump is needed by B3: B3 timelines are new; legacy ones must not change (§4.5).

### 3.2 Determinism
- **Seeds:** all from `sha256` over `(look_seed, look_epoch, purpose)`; no global RNG, no `hash()`; history ties by
  `version_id`; cast-stage results checkpointed.
- **TTS:** not byte-deterministic (spike h); the content-addressed cache is the truth; a rebuild re-synthesises only
  when a B3 beat key changes; calibration stored in `Presentation` (E-31).
- **Procedural music:** `numpy.random.Generator(PCG64(seed))`, float64 internally, int16 with seeded TPDF dither; the
  stored file is the truth. Same seed, same platform: byte-identical. Windows vs Linux: ≤ 1 LSB RMS, identical
  analysis (BPM, loop points ±1 ms, LUFS ±0.1). Golden hashes per platform where needed.
- **Envelope, fitting, SFX placement:** pure functions of timeline and manifest metadata, rounded to 1 ms and
  0.01 dB; goldens.
- **Mix:** a deterministic function of its key's inputs (`MIX_VERSION` included); video chunk keys and
  `CHUNK_CACHE_VERSION` untouched.
- **Frames: identical for a FIXED manifest.** B3 changes narration durations (pacing, re-synthesis, sentence pauses)
  and so changes scene starts, captions and frames of *newly built* lectures; that is the product change. What B3 must
  not do is change frames for the same manifest: the gate builds the timeline from each fixed fixture manifest before
  and after the step and requires every field equal except `music` and `sfx`; B2's 1x/3x/reverse frame gate stays
  green.
- **Live vs MP4:** same breakpoints, same curve, same floor (generated parity vectors).
- **ffmpeg versions** (5.1 container / apt on CI / 8.x on Windows): LU assertions use `bs1770.py`; ebur128
  cross-checked in container-marked tests; linear loudnorm only, with the static-gain + `alimiter` fallback.

### 3.3 Owner rules carried
- Fakes only in tests. The spike, calibration and sample generation are owner-approved scripts, never tests.
- No hour-scale CI checks: `AADHI_MUSIC_BENCH=1` and any listening or loudness sweep are opt-in (OPERATIONS).
- B3 never bumps `ENGINE_VERSION` or `CHUNK_CACHE_VERSION`.

## 4. Worktree, branch and merge strategy

### 4.1 Worktree
- `git worktree add -b b3-audio C:/t/b3 overhaul/v2` (HEAD now; `tts-openai` is merged in before B3a).
- Environment: junction `node_modules` (`cmd /c mklink /J`); a real copy of `web/vendor` (the static mount refuses an
  outside junction); run the MAIN `.venv/Scripts/python.exe -m pytest` with cwd `C:/t/b3`; `--basetemp=C:/t/b3_<label>`;
  mind the xdist/venv gotchas in the project memory.
- Teardown: remove the junctions with `cmd /c rmdir` before `git worktree remove --force`.
- About 10 GB free on C:. Clean old `C:/t` leftovers only with the owner's leave.

### 4.2 Sync
- At the start of every step, `git merge overhaul/v2` into `b3-audio`. Never rebase (the branch is pushed).
- Conflicts resolved in `C:/t/b3`; the full gates re-run after the merge.

### 4.3 Landing
- Landings L0–L6 (§A). **L0 lands as soon as B3-0 is done**, so the gates protect `tts-openai` and B2 work now and
  history is one shared reader. L1 and L2 are landed as groups so no shipped interim state is changed by the next
  landing: between L1 and L2 every Dynamic lecture still has the B1 mix in both the MP4 and the live player
  (placeholder music → no `MusicPlan`), and L2 switches MP4, live player and tick/ding replacement in one commit.
- The merge is done in the main tree (`overhaul/v2` is checked out there) **only at a B2 step boundary, with a clean,
  committed tree**, agreed with the B2 lead: `git merge --no-ff b3-audio`, full gates on the merged tree, push.
- B3 never merges into a dirty tree and never commits in the main tree while B2 work is uncommitted there.

### 4.4 File ownership

**B3-owned new files**
- `providers/tts/{personas,delivery,calibration,samples}.py`
- `pipeline/{history,casting,cast_stage,music,sentences}.py`
- `providers/music/*`
- `compose/dynamic/{mix,duck,music_fit,sfx,preflight_audio,aadhi_lines}.py`
- `compose/bs1770.py`, `disclosure.py`
- `api/routers/audio.py`
- `web/js/player/dynamicAudio.js`
- `scripts/gen_duck_vectors.py`
- `tests/fixtures/b3/*` and their tests

**Shared seams.** Each is a single call or a narrow edit, and each gets an AUDIT round.

| File | Seam | Step |
|---|---|---|
| `compose/presentation.py` (B2-shared) | `is_dynamic`, `b3_voiced`, `b3_music` | B3-0 |
| `compose/timeline.py` | `:754` calls `is_dynamic`; one lazy music/SFX call | B3-0, B3d/f |
| `pipeline/assets.py`, `pipeline/audio.py` | B3 voicing branch, `_tts_provider` override, probe, B3 reuse, gains; shared with `tts-openai` and classic | B3a–c |
| `providers/base.py`, `providers/tts/openai.py`, `gemini.py` | `style` kwarg; `instructions`; style pass-through | B3a |
| `pipeline/orchestrator.py` | cast stage after "script"; parent presentation; translate re-cast; job kinds `music_intake`, `hear_terms` | B3b, B3d, B3h |
| `api/routers/projects.py`, `api/generation.py` | `parent_version_id` in the regenerate payload; payers | B3b, B3h |
| `pipeline/base.py` | `VoicePrefs`, `MusicPrefs`, `sfx` | B3b, B3d |
| `compose/motion/engine.py`, `encode.py` | the `timeline.music` branch of the master audio | B3e |
| `compose/dynamic/preflight.py`, `container.py` | one registration line; `LoudnessTarget` on the B3 branch only | B3e |
| `compose/sounds.py` (shared with classic) | append only | B3f |
| `web/js/player/player.js`, `controls.js` | live + Dynamic + `timeline.music` branch; no `BackgroundMusic` there | B3g |
| `models.py`, `jobs/cleanup.py`, `credentials.py` | asset kinds, `GC_ASSET_KINDS += music` | B3b, B3d, B3h |
| `main.py` | `include_router(audio)` | B3h |
| `requirements.txt` | scipy | B3d |
| `.github/workflows/ci.yml` | `gen_duck_vectors.py --check` in "Generated files are current" | B3g |
| `runtime_flags.py` | `aadhi_persona` flag | B3i |
| `schemas/manifest.py` + generated files | `AssetManifest.presenter` | B3i |
| `compose/dynamic/presenter.py` (B2) | Aadhi beat and timing seam, coordinated | B3i |

**Not edited by B3:** `pipeline/script.py`, `compose/dynamic/authoring.py`, `pipeline/translate.py`,
`providers/tts/voices.py` (personas live in `personas.py`), `api/routers/version_actions.py`.

**Forbidden to B3** (B2-owned, actively edited): `compose/dynamic/{events,kinetic,templates,direct,themes}.py` and the
catalogue; `web/js/dynamic/*`; backgrounds and transitions (B2f); `ENGINE_VERSION`, `CHUNK_CACHE_VERSION`; the schema
files, except via §1.13. B2d's word-timing consumers see only `BeatAudio.words`, whose type does not change.

### 4.5 Timeline rebuilds
- Stored Dynamic timelines rebuild only when `engine_version < ENGINE_VERSION` (B2's). A rebuild of a B2-era lecture
  gives `music is None` (placeholder) and the B1 mix: B2's next bump changes nothing audible. B3 needs no bump of its
  own (the former O-B8 is withdrawn, §C 3).

### 4.6 Migrations
- None planned: history uses existing tables, flags use `app_settings`, course pins come from history, the parent
  presentation travels in the job payload. If one becomes unavoidable, coordinate a single `0008_*` with B2 and B4.

## 5. Owner decisions

**BLOCKING** decisions have a recommendation but no binding default; the named work waits for the answer.

| # | Decision | Recommendation / default |
|---|---|---|
| O0 | Approve the paid B3-0 spike (incl. successor probes) and the one-off calibration and voice-sample generation on the server key | Default yes: ≤ $1 spike; calibration of ≤ 13 voices × 45 words and 12 s samples each are cents |
| **O1** | **BLOCKING before B3b's casting lands.** R-39 † (owner requirement 4: ≥ 2 of every 3 en-IN lessons with an Indian-English narrator; §10.3 test "accent policy ≥ 2 of 3 local") conflicts with "OpenAI primary" (OpenAI has no Indian-English voice). This is two owner instructions in tension, not a default. (a) `any` for OpenAI; (b) OpenAI `@en-IN` instruction variants count as local for voices the owner approves after spike (f); (c) keep `mostly_local`: Edge Neerja/Prabhat narrate 2 of 3 en-IN lessons | Recommendation: **(b)** if the owner approves at least one male and one female `@en-IN` variant after listening; otherwise the owner chooses (a) (OpenAI everywhere, R-39 † relaxed, §10.3 test changed) or (c) (R-39 † kept, OpenAI narrates 1 of 3 en-IN lessons). Casting is built with the policy as a parameter, so the answer is one constant |
| **O2** | **BLOCKING at the end of B3-0.** Successor before 2027-01-06: OpenAI's post-retirement speech path, Gemini 3.8 TTS (price doubles 2027-01-01), Azure, or Edge | Recommendation from spike (j): the candidate that offers the most of the castable voices, a style/instructions control and a price within the budget, preferring OpenAI's own path (same key, same persona ids). Built as B3k, landed by 2026-12-15. If the owner places it outside B3: B3k dropped, risk and date recorded, H0's Edge fallback applies on the day |
| O3 | Edge (unlicensed, no SLA) as the production fallback when no OpenAI key resolves | Default: accept for keyless jobs, as `tts-openai` builds it, with the warning. Azure deferred to B7 (Q8 superseded) |
| O4 | OpenAI rate control: `speed` vs `instructions` (R-37 forbids stretching) | Default per §P: `speed` only if natural within 0.90–1.10; else `instructions` + measure-and-retry, speed 1.0 |
| O5 | Word timings for OpenAI | Default: per-sentence synthesis, B3-voiced only, `words_exact=false`. faster-whisper not in B3 (B5) |
| O6 | AI-voice disclosure for **classic** videos narrated by OpenAI | Default: classic unchanged in B3; raised as a compliance item with Q3. Dynamic gets `disclosure.providers()`; its text in the outro (B2) and `credits.txt` (B4) |
| O7 | Who pays for previews, calibration and Hear your terms | Default: calibration and `voice_sample` the server, once per (model, voice, version), pre-generated by script; Hear your terms the lecture's payer |
| O8 | OpenAI voice list and R-40 approval; tags | Default: castable = accepted by `gpt-4o-mini-tts` (spike a) ∩ offered by the O2 successor; the owner listens once. Others stay readable for stored casts but are not cast |
| O9 | Q4: Aadhi's fixed brand voice | Default: an OpenAI male voice from the successor-compatible set the owner picks from samples (`openai:cedar` if both accept it, else `openai:ash`), fixed "friendly, bright, welcoming" instructions; keyless `edge:en-IN-PrabhatNeural` |
| O10 | Q2: AI music default | Default: opt-in only (Lyria behind `ai_opt_in`); decide after the listening test |
| O11 | Q10: default destination loudness | Default: `messaging` → -14 LUFS; LMS and lesson page → -16 |
| O12 | Q9: curated music pack | Default: decide after the B3 listening and blind test |
| O13 | Q3: Lyria and Gemini 18+ terms | Default: Lyria built, disabled in production until legal clears it |
| O14 | Scope cuts given OpenAI primary | Default: ElevenLabs keys and Azure to B7; keep the Gemini `style` pass-through (with a test); drop the §10.3 "Gemini full voice list" from B3 (O2/B7); Edge extension personas-only, en-* voices only, no new languages, no `voices.py`/`LANGUAGES` change |
| O15 | Who places SFX (R-49) | Default: B3, in `compose/dynamic/sfx.py`, reading `TimedScene.events` |
| O16 | Course pins storage before B4's Course picker | Default: derived from history; no migration |
| **O17** | **BLOCKING before B3a; `tts-openai`'s scope.** Stored lectures after the `TTS_PROVIDER` flip: today a full rebuild re-voices them on OpenAI (paid) and a scene-scoped rebuild mixes voices. Also: does a lecture built keyless (Edge + warning) stay on Edge after the owner adds a key? | Recommendation: **freeze per lecture**: when the stored options name no provider, a rebuild reuses the manifest's `SceneAudio.provider/voice` — Edge-built lectures included — and moving to OpenAI is an explicit teacher action (a rebuild with the provider named; the keyless warning says so). New lectures follow "OpenAI primary". Belongs in `tts-openai` before it merges; if it lands without it, B3a adds it in `assets.py` (SHARED, AUDIT) only with the owner's OK. B3-voiced lectures follow the same answer (§1.6) |
| O18 | Sonic logo | Default: interim procedural motif ≤ 1 s in B3; final logo B8 |
| O19 | Aadhi's voice and line pool in B3 (B3i, with the `AssetManifest.presenter` schema escalation) or B4 | Default: B3 (English pool, voice, timing seam); other languages and greeting rotation inputs in B4 |
| O20 | The reference track for the blind test | Default: the owner supplies one licensed instrumental study track |
| O21 | History scope: do a teacher's classic lessons (now also OpenAI-narrated) count for voice rotation? | Default: no — rotation is across Dynamic lessons; classic voice is the teacher's fixed choice |
| O-B2 | Merge order | Default: `tts-openai` first, then B3a onward; L0 now |
| **O-B9** | **BLOCKING before B3b's inheritance and before L2.** Stored B2-era Dynamic lectures (placeholder `MusicChoice(source="off")`, bare narrator, no calibration): (i) what they play after L2; (ii) what regenerate / Fresh look does with them | Recommendation: (i) **unchanged** — legacy voicing and the B1 mix (branding BGM) until the teacher acts; the alternative, honouring "off" literally, silences the branding BGM on every stored lesson. (ii) regenerate keeps the narrator **speaker** (`resolve_cast_id`, then calibrated) and **selects music fresh**, because the placeholder was never a teacher's choice |
| O-B10 | A lecture voiced while Dynamic was on, served after the flag is turned off | Default: keeps its stored narration until rebuilt (§1.0); recorded in OPERATIONS |
| ~~O-B8~~ | ~~`ENGINE_VERSION` bump so stored timelines get music~~ | Withdrawn: no bump needed, and a bump changes nothing audible (§4.5) |

## G. Gates (every step) and acceptance

### Every step, in the worktree (mirrors CI)
- `ruff check aadhi tests scripts evals docker`; `tsc`.
- CI's "Generated files are current" step, in full: `python scripts/gen_env_example.py --check`;
  `python scripts/gen_chromium_seccomp.py --check`; `python evals/make_fixtures.py --check` (the cast stage is outside
  `assemble_screenplay`, so eval fixtures do not drift); `python -m aadhi.schemas.generate --check`;
  `python -m aadhi.compose.dynamic.catalogue --check`; `python -m aadhi.compose.dynamic.faces --check`;
  `python -m aadhi.compose.dynamic.icons --check`; `python scripts/gen_third_party_notices.py --check`;
  plus `scripts/lock_requirements.py --check`; from B3g `scripts/gen_duck_vectors.py --check` (added to that CI step,
  a shared edit listed in §4.4).
- Fast suite (`pytest -n auto -m "not slow and not network"`); `npm test`.
- Invariance goldens including `tests/invariance/goldens/_api/generation_options.json` and `meta_voices.json`; the
  isolation probe and budget test; the audio-key, flag-off and legacy gates.
- Slow-marked tests of the touched areas.
- Container-marked audio tests in the Docker `test` job, with **per-test results cited** (the job as a whole is red
  for unrelated reasons: Manim path scrub, node absent, stale fixtures).
- OUTPUT steps also run: a classic before/after differential (audio samples equal); the fixed-manifest timeline gate
  (every field equal except `music`/`sfx`); B2's frame gate green.

### Batch acceptance (B3j)
- §10.3's tests present and green, as amended in §D.
- Two consecutive lessons differ in speaker and music; regenerate keeps both; B2-era lessons untouched.
- All audio preflight checks pass on B3's fixtures.
- The owner's listening checklist and blind test recorded.
- Docs updated (design as-built, OPERATIONS, `.env.example`, HANDOFF); review records in `docs/plans/b3/`.

## D. Deviation register (copy into the design's B3 as-built)

1. **OpenAI `gpt-4o-mini-tts` is the PRIMARY provider** (owner 2026-10-17); Edge is the keyless fallback and the
   fallback for an unavailable model. Q8 superseded; Azure to B7. §5.1: OpenAI "Primary", Edge "fallback/free"; the
   "In the repo today: `tts-1-hd`" text is stale. The 2027-01-06 retirement and the O2 successor (B3k) recorded.
2. **§5.2.** The primary pool is the OpenAI personas castable on both today's model and the successor; the Edge table
   is the fallback and free pool. Persona ids `"<provider>:<voice>[@variant]"`, model-free; `VoicePersona` gains
   `paid` and `successor_ok`.
3. **§5.3 accent policy** per O1's answer.
4. **§5.4 OpenAI control.** `speed` or `instructions` per §P; `VoiceCalibration.control` grammar §1.3,
   model-independent.
5. **Dynamic fallback is lecture-wide** (not H0's per-scene), at Edge `"+0%"`, same gender, decided before the
   fan-out; no calibration stored for it. Classic H0 unchanged.
6. **Stored bare narrator ids** are read through `resolve_cast_id`, never rewritten; B2-era lectures keep legacy
   voicing (§1.0).
7. **R-49 SFX are placed by `compose/dynamic/sfx.py`, not `events.py`**; `Timeline.sfx` is the only carrier;
   supersedes b2_plan_v2 §2's R-49 line.
8. **ElevenLabs keys and the `azure` credential deferred to B7.** `CREDENTIAL_PROVIDERS` unchanged.
9. **Calibration, `voice_sample` and Hear your terms are paid usage rows** on OpenAI; "Free on Edge" copy applies to
   Edge personas only.
10. **Per-sentence synthesis is B3-voiced only**; "chunks ≤ 1,500" becomes per-sentence requests with clause splits;
    `openai.MAX_INPUT_CHARS` stays 4096; or the silence-boundary fallback per §P (e).
11. **AI disclosure** is Dynamic-only in B3 (`disclosure.providers()`); classic is O6.
12. **R-42.** Only between-sentence pauses are B3's.
13. **Master chain linear loudnorm only**; tests measure LU with `bs1770.py`.
14. **Procedural "≤ 20 s per 5 min" is an opt-in benchmark.**
15. **Lyria built but disabled until Q2/Q3.**
16. **B2-era Dynamic lectures keep the B1 mix** (placeholder music → no `MusicPlan`) per O-B9; no engine bump.
17. **`GenerationOptions.voice/.music/.sfx` land in B3**, excluded at None.
18. **History**: completion from `Render`, order `Job.finished_at` → `Render.created_at`, Dynamic-only (O21), current
    screenplay; course pins derived (O16).
19. **The mix runs in preflight before capture, chunked** (≤ 300 MB; > 120 min refused). §5.12's ~0.5 GB assumed
    4:30–5:30 lessons; today's lessons run up to 90 min.
20. **`scipy` is a direct requirement**, worker-only; joins the open Python-notices gap (§4.12).
21. **Aadhi's persona is an `app_settings` flag**; Edge fallback fixed.
22. **The sonic logo is an interim procedural motif** (B8 final); it changes no timing before B3i.
23. **`voice.*` lints and eval metrics** are pure functions in B3; B4 wires them.
24. **`TTSProvider.synthesize` gains keyword-only `style`**, passed only when non-empty; enters the key only when
    non-empty. Instruction characters go to `meta.instruction_chars`, not to `characters`.
25. **Casting runs in a worker "cast" stage** (orchestrator), not in `script._default_presentation`; regenerate
    carries `parent_version_id`; translation re-casts per §5.3.
26. **Music is selected and the loopable bed generated at the cast stage**, persisted in `Presentation.music`; every
    source has loop points; `music_fit` never generates audio (§5.10's "extend with a procedural bed" dropped).
27. **`voice_sample` and `calibration` are not GC kinds** (the design's GC text would delete them monthly).
28. **The Edge catalogue extension is personas-only, en-* only**; `voices.CATALOGUE`, `LANGUAGES` and `/api/meta`
    unchanged; bn/mr/gu/ur deferred.
29. **Duck floor -120 dB = silence**; viewer Low and teacher low do not stack.
30. **`BeatAudio.words_exact`** (a B5 "cuts" field) is written by B3c on the Dynamic path; write gates apply to
    screenplays only, so this is harmless and recorded.
31. **Design text made stale by OpenAI primary and the deferrals** (all amended at B3j):
    - §10.3 B3 Scope (Azure, ElevenLabs, "Gemini … full voice list" — dropped from B3) and "Contracts frozen: … the new
      credential providers" (none in B3);
    - §5.7 key table: ElevenLabs and Azure "B3" → B7;
    - §5.3 "With Azure configured … local_only" and §5.2's small-pool copy asking for an Azure key;
    - §10.5 Q8 (superseded);
    - §6.12 "fake Azure word boundaries" → fake per-sentence boundaries;
    - review dispositions E-27 and T-7 (Azure-dependent);
    - §5.11's "B4 gate" for the blind test → B3j;
    - §5.12's mix memory figure; §5.x GC kinds; §5.10 extension bed; R-49 levels and placement owner;
    - §10.3 test "accent policy (≥ 2 of every 3 local)" per O1.

## C. Critique answered (revision 2)

Each finding: verdict, then where the fix lives.

1. **[CRITICAL] Voice branch gated on `presentation is not None` only.** Accepted. One predicate `is_dynamic` (§1.0)
   used by `timeline.py:754`, `assets.py`, the cast stage and translate; B3-0 flag-off gate (env off, and runtime flag
   off) asserts classic keys and `SceneAudio`; §3.1 item 8 names it. The flag-flip-after-build case is a recorded
   rule (§1.0, O-B10).
2. **[CRITICAL] Casting seam in `script._default_presentation` infeasible; inheritance needs plumbing.** Accepted.
   Casting, calibration and music selection move to a worker cast stage in `orchestrator.generate_lecture` with
   `ctx` and `_db` (§1.5b, B3b); `script.py`/`authoring.py` untouched. Regenerate carries `parent_version_id`
   (projects router → payload → orchestrator); duplicate already copies. `orchestrator.py`, `projects.py`,
   `api/generation.py` added to §4.4.
3. **[HIGH] B2-era `MusicChoice(source="off")`, bare narrator, no calibration.** Accepted as owner decision
   **O-B9 (blocking)**, not a default. Markers `b3_voiced`/`b3_music` distinguish B2-era from B3 lectures; B3 never
   writes the `(off, bed)` placeholder; a deliberate off is `(off, off)` → `MusicPlan(mode off)`, never the B1 BGM.
   Inherited casts get calibration filled (§1.5), removing the §1.5 contradiction. O-B8 withdrawn: a bump now changes
   nothing audible.
4. **[HIGH] Re-keying makes every stored Dynamic lecture re-synthesise.** Accepted. (i) Legacy rule: no calibration →
   today's voicing exactly; gate on the frozen `b2_era` fixture, zero TTS calls (B3-0, every step). (ii) B3 reuse rule
   per beat and per persona (§1.6c).
5. **[HIGH] Branching on `timeline.music` conflates "has music" with "B3 audio".** Accepted. A `MusicPlan` is always
   written for B3-music lectures (mode off with empty cues), so the branch means "B3 audio"; on it the engine drops
   classic ticks/ding/BGM, and the player builds no `BackgroundMusic`. L2 lands B3d–g together so the tick/ding
   replacement and the live player arrive with the mix. Tests: music off → no BGM and no classic ticks in MP4 and
   live player (B3e, B3g).
6. **[HIGH] No representation for "off" in the duck.** Accepted. `DUCK_FLOOR_DB = -120.0`, power 0 at or below it,
   in Python and JS, with floor vectors, frozen before generation (§1.9).
7. **[HIGH] No place for Aadhi's beats; sonic-logo timing.** Accepted. B3i is a declared schema escalation
   (`AssetManifest.presenter`, §1.13); greeting via `greeting_id`, sign-on derived from the seed (no new screenplay
   field); `aadhi_lines.py` under `compose/dynamic/`. B3f's logo changes no timing (plays under the silent intro);
   the 0.8 s offset is B3i's presenter seam. §1's "adds no field" statement corrected.
8. **[HIGH] Frame gate false/vacuous; no Dynamic fixtures.** Accepted. Restated as "identical for a fixed manifest"
   (§3.2, §G). B3 owns `tests/fixtures/b3/*` built in B3-0 from today's templates; B2g's kitchen-sink is used in
   addition if it lands.
9. **[HIGH] Retirement on 2027-01-06.** Accepted. O2 is a blocking B3-0 output informed by spike (j); persona ids and
   the control grammar are model-independent; castable voices and Aadhi's voice are restricted to the successor's set
   (`successor_ok`, O8, O9); B3k builds the successor adapter, landed by 2026-12-15.
10. **[MEDIUM-HIGH] O17 test cannot pass on the unmodified tree; freeze semantics open.** Accepted. Committed as
    `xfail(strict=True)`; B3a waits for it or the owner's answer. O17 is marked blocking and as `tts-openai`'s scope,
    with the keyless-built question asked explicitly and a recommendation (freeze incl. Edge-built; explicit
    re-voice).
11. **[MEDIUM] Fallback duplication and unspecified `ModelUnavailable` recast.** Accepted. B3a overrides
    `tts-openai`'s keyless voice inside `_tts_provider` for B3-voiced lectures only (same-gender persona); a probe
    synthesis of the first needed narrator clip before the fan-out decides the recast; no finished scene is
    re-narrated within a build (§1.6).
12. **[MEDIUM] Speaker gains unstable, can clip.** Accepted. Gains from calibration LUFS (kept in the calibration
    asset's meta — no schema field), frozen per lecture via `SceneAudio.speaker_gains`, applied in float with a
    headroom cap and soft limiter; headroom test (§1.6b).
13. **[MEDIUM] GC sweeps `voice_sample` and `calibration`.** Accepted. Both excluded from `GC_ASSET_KINDS`; only
    `music` (referenced through `AssetRef`) is added. A "system AssetRef" is impossible (`project_id` NOT NULL).
14. **[MEDIUM] Where the music choice is persisted; R-47 signatures.** Accepted. Selection and the bed happen at the
    cast stage and land in `Presentation.music` in the generation's own screenplay write (no asset job writes a
    screenplay); signatures from `Asset.meta` with one `IN` query (§1.4, B3d).
15. **[MEDIUM] `music_fit` cannot extend with a procedural bed.** Accepted. Every source has loop points (whole-track
    crossfade loop as the floor); extension dropped (§D 26). The isolation probe now asserts that a Dynamic
    `build_timeline`/`preview_timeline` loads no scipy and no `providers.music.*`.
16. **[MEDIUM] Isolation probe cannot see the asset path.** Accepted. The probe adds a classic `build_assets` (fake
    TTS) and `assemble_screenplay`; `aadhi.compose.bs1770` and every B3 module are listed explicitly (B3-0).
17. **[MEDIUM] Translation not covered.** Accepted. Re-cast on translate in the orchestrator's translate handler
    (`translate.py` untouched), with the Tamil test (B3b).
18. **[MEDIUM] B3h API surfaces missing; `credits.txt` does not exist.** Accepted. New `api/routers/audio.py`
    (upload with confirmations and `AssetRef`; sample listing of pre-generated public assets — no on-request spend;
    hear-terms as a rate-limited job). B3 ships only `disclosure.providers()`; `credits.txt` is B4.
19. **[MEDIUM] Mix memory wrong for today's lesson lengths.** Accepted. Chunked mix, ≤ 300 MB, > 120 min refused,
    equality and 60-minute tests; OPERATIONS note updated (B3e, §D 19).
20. **[MEDIUM] `LoudnessTarget` unconditional.** Accepted. Passed only on the B3-mix branch; test that a B1-mix
    render's QA is unchanged (B3e).
21. **[MEDIUM] Live player.** Accepted. (1) Never constructed in render mode; (2) Playwright asserts GainNode
    automation values and `currentTime` sync, not audibility; (3) levels do not stack, defined in §1.9 (B3g).
22. **[MEDIUM] O1 presented as a default.** Accepted. O1 is blocking, framed as two owner instructions in tension,
    with three options and a recommendation; casting takes the policy as a parameter (§5).
23. **[MEDIUM] Landing too late; interim states.** Accepted. L0 lands right after B3-0 (gates, predicate, history,
    fixtures); L1 = B3a–c and L2 = B3d–g land as groups; the L1→L2 interim (B1 mix in MP4 and live player alike) is
    declared (§4.3).
24. **[LOW-MEDIUM] SFX levels and accent scope.** Accepted. -16 to -24 dB; accent only in explanation scenes (B3f,
    §1.11, §2).
25. **[LOW-MEDIUM] Instructions priced as audio characters.** Accepted. `characters = len(input)`; instruction length
    in `meta.instruction_chars`, unpriced at the audio rate (§1.12).
26. **[LOW-MEDIUM, process] Review weights misclassified.** Accepted. B3d and B3f are SHARED (AUDIT); B3-0 is SHARED
    for its predicate lines; B3h and B3k SHARED.
27. **[LOW-MEDIUM] History scope and correctness.** Accepted in part. Classic lessons: a decision (O21, default
    Dynamic-only). Completion read from `Render` itself, so a NULL `job_id` never hides a render. Current vs rendered
    screenplay: **rejected as a change** — the fields history reads are not changed by ordinary edits, and the rendered
    revision's screenplay is not stored (only `built_revision`); the reason is recorded in §1.4 with a revisit trigger.
28. **[LOW] Unspecified interactions.** Accepted. `tts_rate` ignored for pacing on B3-voiced lectures with an info
    issue (B3a); the logo obeys `presentation.sfx`, not the music mode (§1.11); `aadhi_lines.py` in
    `compose/dynamic/`; the R-37 check moved into B3c.
29. **[LOW] Edge catalogue extension not Dynamic-only.** Accepted. Personas-only, en-* only; `voices.py`/`LANGUAGES`
    untouched (no conflict with `tts-openai`); a `/api/meta` voice golden added in B3-0.
30. **[LOW] Gate list incomplete; golden path.** Accepted. §G mirrors CI's full step (seccomp, eval fixtures, faces,
    icons, notices); `ci.yml` listed as a shared edit; path is `tests/invariance/goldens/_api/generation_options.json`.
31. **[LOW] `words_exact` and the Protocol.** Accepted. `words_exact` recorded in §D 30; `style` passed only when
    non-empty, with a test double test (B3a, §D 24).
32. **[LOW] As-built amendments incomplete.** Accepted. §D 31 lists every stale passage named, the dropped "Gemini …
    full voice list" (O14), and B3a adds the Gemini style pass-through test.
