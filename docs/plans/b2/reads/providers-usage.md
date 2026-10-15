# providers-usage: aadhi/providers/** (factory, base, shared plumbing, llm/*, tts/*, image/*, video/*, gif/*; there is no music/ package) and aadhi/usage/** (pricing.py, service.py)


## summary
aadhi/providers is the only place the app talks to external AI services. It defines Protocols in base.py for LLM, TTS, image, video (including resumable paid jobs) and GIF providers, plus the Usage record and a typed error hierarchy. factory.py builds and caches adapters per Settings object identity (an LRU of 64). Adapters exist for: LLM (gemini, openai, anthropic, fake); TTS (edge, gemini, openai, elevenlabs, fake); images (gemini/Imagen, pollinations, fake); video (veo, omni = Gemini Omni through the Interactions API, fake); and GIFs (giphy, fake). No music provider exists anywhere. The adapters share their plumbing. call_with_retries (_retry.py) wraps tenacity and sends job-log notices (_notify.py). _stream.py detects stalled streams for OpenAI and Claude. Every error goes through make_error, which redacts secrets. GeminiCaller/KeyPool rotate Gemini keys on a 429 and can keep one client per event loop. LoopLocal keeps per-loop httpx/SDK clients, because every job runs in its own event loop. health.py sorts media failures into categories that drive the fallback chain and its cooldowns. operations.py stores durable checkpoints of paid Veo/Omni jobs so a restarted worker resumes the job instead of paying again. All five TTS adapters measure duration (ffprobe, or the WAV header) and return a SpeechResult with clip-relative word timings. The timings come from three places: native Edge WordBoundary events, ElevenLabs character alignment turned into words, or exact synthetic timings in FakeTTS. Gemini and OpenAI return none, so pipeline/audio.py estimates word times from word length (words_estimated=True). The speech text pipeline lives here too (normalize.py, mathspeech.py, speech_words.py: lexicon, then markup and LaTeX turned into words in en/ta/hi). Credentials are never read inside the slice except from Settings fields. aadhi.credentials.resolve_keys hands the job a model_copy of Settings with the personal or Studio keys swapped in, and the factory caches providers per that copy. aadhi/usage/pricing.py turns every Usage into an estimated USD cost from a deep-mergeable price table (PRICING_FILE plus dated scheduled steps). It already includes the B0 additions: a music operation, unknown operations failing closed, ElevenLabs list prices, Gemini 3.8 TTS with the 2027 doubling, and Omni per-token prices. usage/service.py records UsageEvents and enforces the per-job and daily (UTC) budgets with SQL aggregates. In this slice the Dynamic-video work is: H0 partly done (config default and the WAV handling), B0 pricing done, and the Omni provider added. None of B3 exists (personas, casting, Azure, style/instructions, per-sentence synthesis, music providers), and B2 has no provider code. B2 touches this slice only through word timings and the image style suffix.

## contracts
PROTOCOLS (aadhi/providers/base.py):
- TTSProvider.synthesize(text, *, voice, language, rate="+0%", on_usage) -> SpeechResult (182-201). It has no style, instructions, pitch, model or speaker parameter.
- SpeechResult (171-179): mime is audio/mpeg or audio/wav; duration is always measured, never estimated; words are clip-relative WordTiming (164-168); words=[] means the provider gave no timings.
- LLMProvider.generate_json/generate_text (120-158).
- ImageProvider.generate(prompt, *, aspect, on_usage) (226).
- VideoProvider.generate(prompt, *, aspect, reference_image, timeout_s, on_usage) (245).
- ResumableVideoProvider adds checkpoint= and resume(operation, key_fingerprint, ...) (279-307).
- GifProvider.search (320).
- Optional class attributes, read with getattr (218-223): paid, aspects, max_prompt_chars, resumable, reports_sending (veo.py:117, omni.py:200), supports_word_timings (TTS).
- OperationCheckpoint: submitted(op, fp), usage_recorded(), optional sending()/not_sent() (260-276).

USAGE AND PRICING:
- Usage fields (32-42). Operations priced: llm, vision, tts, image, video, gif, music. Anything else is charged at the global default rates of every quantity, with at least one unit; only provider "fake" stays free (pricing.py:41-44, 425-439).
- Meta keys read by pricing: cache_read_tokens, cache_write_tokens (Anthropic, llm/anthropic.py:105) and thoughts_tokens (Gemini image, gemini_common.py:421).
- Video usage meta: status = generated | abandoned | failed, plus estimated (veo.py:194-199, omni.py:268-284).
- Unit bases: TTS is priced per 1M characters (tts/*: characters=len(text)); image per unit plus image_tokens; video per second; music {unit, second}.
- PRICING_FILE deep-merges over the table, then scheduled steps ({from, prices}, UTC dates). A "scheduled" list in the file replaces the built-in steps (pricing.py:268-301).
- estimate_cost(usage, settings, *, on=None) (369).
- Consumers: jobs/context.py:96-106 price_usage returns 0.0 on any exception (fails open); pipeline/integrations.py:319.
- Budgets (usage/service.py): days are UTC; only billed_to="server" counts against the daily budget (91-101); job limit MAX_COST_PER_LECTURE_USD (111-139).

FACTORY AND META SHAPES (factory.py):
- get_llm(settings, engine) 141; get_tts(name, settings) 210; get_image(settings, name) 349; get_video(settings, name) 374; get_gif(settings) 460. The argument order is not uniform.
- available_tts_providers -> [{id,label,configured,word_timings,voices}] (243). available_llm_engines -> [{id,label,configured,models}] (173). available_image_providers -> [{id,label,configured,paid}] (292). media_provider_status shape 404-457.
- voices_for -> [{id,label,language,gender}] (voices.py:161).
- voices.LANGUAGES must equal pipeline.base.SUPPORTED_LANGUAGES (tests/providers/test_voices_factory.py:22).
- The fake provider is allowed only when APP_ENV=test or that provider is configured as fake (factory.py:73).

CREDENTIALS (outside the slice, but the slice depends on it):
- Adapters read keys only from Settings fields: gemini_api_key/gemini_api_keys (all_gemini_keys, config.py:484), openai_api_key, anthropic_api_key, elevenlabs_api_key, giphy_api_key.
- credentials.resolve_keys (credentials.py:346-403) returns the same Settings object when no saved key applies; otherwise a cached model_copy with the gemini/openai/anthropic keys replaced (and the Gemini pool emptied). The factory caches providers per that object's identity.
- CREDENTIAL_PROVIDERS = (gemini, openai, anthropic) (models.py:74). KEY_PROVIDER_LABELS is built from factory.LLM_ENGINE_LABELS (credentials.py:73).
- USAGE_KEY_PROVIDER maps veo, omni and lyria to gemini (credentials.py:76).
- personal_key_rejection reads ProviderError.status 401/403, or Gemini 400 invalid-key text (credentials.py:412-439). So every adapter must keep ProviderError.provider and status meaningful. ProviderUnavailable stays a ProviderError subclass for this reason (base.py:72-78).
- ElevenLabs is server-key only (api/generation.py:241 _PAID_TTS_KEYS["elevenlabs"]=None). Lyria pays with the Gemini key (api/generation.py:246).

MEDIA CHAIN AND HEALTH:
- Category to behaviour (health.py:62-103): blocked never falls back; unavailable gets the long cooldown; rate_limited, transient and invalid_output get the short cooldown.
- Cooldown scopes are "server" or "user:<id>".

PAID JOBS:
- Job payload key "provider_operations". Statuses are listed at operations.py:48-56. Only key fingerprints are stored.
- A create request that got no answer is never re-sent: veo.py:275-309, omni.py:422-453.

SETTINGS USED:
- TTS: TTS_PROVIDER, TTS_VOICE, TTS_RATE, TTS_MAX_PARALLEL, ELEVENLABS_MODEL (eleven_multilingual_v2), OPENAI_TTS_MODEL (gpt-4o-mini-tts), GEMINI_TTS_MODEL (gemini-3.8-flash-tts) (config.py:173-180).
- Images: IMAGE_PROVIDER, IMAGE_MODEL, IMAGE_STYLE_SUFFIX, IMAGE_FALLBACK_PROVIDERS.
- Video: VIDEO_PROVIDER(S), VIDEO_FALLBACK_PROVIDERS, VEO_MODEL, OMNI_MODEL, VEO_DURATION_SECONDS, AI_VIDEO_TIMEOUT_SECONDS.
- Media limits and cooldowns: MEDIA_PROVIDER_(AUTH_)COOLDOWN_SECONDS, AI_MAX_IMAGE_BYTES, AI_MAX_VIDEO_BYTES.
- LLM: LLM_TIMEOUT/FIRST_RESPONSE/IDLE_SECONDS; ANTHROPIC_EFFORT, ANTHROPIC_MAX_TOKENS, ANTHROPIC_REFUSAL_FALLBACK, ANTHROPIC_TIMEOUT_SECONDS.
- Budget and tools: PRICING_FILE, MAX_COST_PER_LECTURE_USD, DAILY_BUDGET_USD_PER_USER, FFMPEG_PATH/FFPROBE_PATH.

PIPELINE INVARIANTS THAT DEPEND ON THIS SLICE:
- The TTS clip cache key is compute_key("tts", {text, voice, provider, rate, model, language}) (pipeline/assets.py:528-532). Adding any audio-changing parameter (style, instructions, voice_settings) must enter this key.
- pipeline/audio.py decodes every clip to mono 24 kHz s16 (SAMPLE_RATE 29). Provider word timings are mapped onto the written tokens: one to one when the counts match, else interpolated by character position (130-163). With no timings, estimate_words (116) is used and words_estimated=True.

## conventions
- Every module starts with `from __future__ import annotations` and a module docstring that explains why, with doc references (e.g. "DYNAMIC_VIDEO_DESIGN §5.7"). Lines run to about 120 characters. Constants are UPPER_CASE at the top, and private helpers start with an underscore.
- No SDK is imported at module import time. Use _lazy.import_off_loop, genai_types() or load_edge_tts() inside async code. tests/providers/test_tts_adapters.py:159 enforces this (test_provider_modules_do_not_import_sdks_at_import_time). CPU work (base64 of more than 1 MB, hashing large files, Pillow, WAV rendering) runs in asyncio.to_thread.
- Clients are per event loop: each job runs in a fresh loop. Use LoopLocal (_common.py:107), loop_local_client (_http.py:162), or GeminiCaller(per_loop=True) with loop_lease (gemini_common.py:256-309).
- Every network call goes through call_with_retries with a classify function and a plain-words service name, e.g. "voice service (Edge)", used for job-log notices. SDK-internal retries are switched off (max_retries=0; Omni _no_sdk_retries).
- Errors are always built with make_error(cls, provider, message, settings=, status=, host=, detail=), which redacts, truncates and strips partial secrets. Never put request data or keys in messages. Use the subclasses deliberately: ContentBlocked, RateLimited, ProviderUnavailable (keyless/server 401/402/403), InvalidMediaOutput, OperationLost, PaidSubmissionUncertain. Broad excepts carry `# noqa: BLE001 - <reason>`.
- Billing rule: report Usage through emit_usage as soon as a billable response is in hand, before safety checks, decoding, probing or verification (gemini.py TTS 94-96; openai TTS 100-102; elevenlabs 113-115; image/gemini.py 96-98; veo/omni _finish). Paid jobs report an estimated "abandoned" usage when given up. Sinks may raise BudgetExceeded, and that propagates.
- Usage.provider names the usage provider (veo, omni and lyria are distinct from gemini but billed to the Gemini key). Edge TTS uses the voice id as the model.
- Dependency injection seams for tests: client_factory, sleep, max_attempts, poll_interval_s, communicate_factory (Edge), base_url/search_url (REST), timeout_s.
- Determinism: fakes derive everything from sha256 of their inputs (stable_seed, render_png, render, render_gif). Pollinations seeds from the prompt, so the same prompt gives the same image.
- Every provider kind has an offline fake (design gate 10.2.4: "a batch that adds a provider adds its fake").
- Fake LLM tests use FakeLLM.register(schema_name, responder) and clear_registry().
- tests/providers/conftest.py resets FakeLLM, the provider cache, key pools and provider health around every test. A new process-wide cache needs a reset_* function wired there.
- tests/providers/fakes.py builds real google.genai types and openai-shaped fakes. tests/providers/media_fakes.py provides scripted, resumable media stand-ins.
- Pricing tests pin dates (on=dt.date(2026,10,10)) and call pricing.reset_price_cache.
- Factory caching relies on Settings identity. Never mutate Settings; use model_copy.
- Shared module-level state (pools, health, price cache) is guarded with threading.Lock.
- Ordering and sets: dict.fromkeys is used for order-preserving de-duplication.
- Paid video adapters follow the Veo/Omni template: an uncertain flag, sending()/not_sent() around the create, submitted() before polling, _note_usage after each charge, and abandoned usage on ProviderError, CancelledError and other Exceptions.

## dynamic_status
H0 (Gemini TTS hotfix, design 2355-2360): PARTIAL.
- Done: the config default is gemini-3.8-flash-tts (config.py:180). The adapter keeps a RIFF/WAV answer and wraps headerless PCM (tts/gemini.py:101-106). The pricing row exists with the 2027-01-01 step (pricing.py:173, 234). Tests: test_tts_adapters.py:356-371, test_pricing.py:152.
- NOT done: the requirements.txt floor is still google-genai>=2.0,<3 (requirements.txt:30), not >=2.25. The lock has 2.26.0.
- NOT done: "A missing-model error falls back to Edge with a visible warning". GeminiTTS maps a 404 to a plain ProviderError (it goes through gemini_error, gemini_common.py:179-199). The pipeline falls back only when the provider is ProviderNotConfigured at build time (pipeline/assets.py:373-386).

B0 items in this slice: DONE.
- Unknown operations fail closed (pricing.py:41-44, 393-394, 425-439).
- The music operation: Lyria per song (also under provider "gemini"), ElevenLabs Music per second, procedural and fake free, unknown providers pay both rates (pricing.py:208-222, 391-413).
- ElevenLabs TTS list prices re-read (pricing.py:164-170).
- gpt-4o-mini-tts row (163).
- Tests: tests/usage/test_music_and_fail_closed.py and tests/usage/test_billing_coverage.py (the _media_billed_to_server music/premium/calibration payers at api/generation.py:240-300).
- Missing from the §5.7 price list: an Azure per-character row. An "azure" TTS usage would fall to tts.default 30/1M, which is fail-high.
- Added outside the plan, as recorded in the B0 as-built notes: the Gemini Omni provider (video/omni.py), its pricing row (pricing.py:68-76, 201) and VIDEO_PROVIDERS ordering.

B1: no provider-side work was required, and none was found in this slice. The last commit touched omni/veo/pricing/gemini_common (git show a75acf8). The slice has no uncommitted changes.

B2: nothing provider-side exists, and nothing is needed except the hooks listed under touchpoints.

B3 (audio): NOT STARTED in this slice.
- No tts/personas.py (VoicePersona registry).
- No casting.py or history.py. A grep for personas.py, casting.py, providers/music, mix.py over the code finds only the design doc and docs/research.
- No Azure adapter. TTS_PROVIDERS is still (edge, gemini, openai, elevenlabs, fake) (factory.py:34).
- No providers/music/ package (procedural, upload, lyria, fake) and no factory get_music.
- No per-sentence synthesis or words_exact production. BeatAudio.words_exact and persona are inert schema fields (schemas/manifest.py:47-48).
- No voice_sample or calibration producers. The "calibration" asset kind is absent (models.py:244-248), and GC_ASSET_KINDS is unchanged (jobs/cleanup.py:61).
- Catalogue gaps against the design:
  - Edge has 24 voices (voices.py:27-52). It lacks bn-IN/mr-IN/gu-IN/ur-IN, the extra English voices, and en-AU WilliamMultilingualNeural from the §5.2 persona table.
  - OpenAI lists 9 voices (design: 13).
  - Gemini lists 14 of 30.
  - ElevenLabs has 10 static ids (design: GET /v1/voices).
- OpenAI sends no `instructions` (tts/openai.py:8 says so explicitly). The design wants instructions for style and the measure-and-retry rate.
- Gemini sends no speech_metadata.style or pause tags (tts/gemini.py:83-91).
- ElevenLabs is still eleven_multilingual_v2 (config.py:178) with fixed stability/similarity (elevenlabs.py:89). The design wants v4/Flash with style mapping, and personal plus server keys. CREDENTIAL_PROVIDERS (models.py:74) still lacks elevenlabs and azure.
- Edge passes no pitch (edge.py:99), although edge-tts 7.2.8 accepts it (TTSConfig(voice, rate, volume, pitch, boundary)).

B3 pre-wiring that DOES exist outside the slice:
- Schemas: VoiceCalibration, VoiceCast, MusicChoice (schemas/screenplay.py:720-746); Presentation.voices/music (792-793); Beat.speaker (491); MusicAsset (manifest.py:102-122); SceneAudio.lufs/speaker_gains (manifest.py:72-73); timeline MusicPlan (schemas/timeline.py:268).
- Billing: USAGE_KEY_PROVIDER "lyria" (credentials.py:76); _PAID_MUSIC_KEYS (api/generation.py:246); voice/music payer logic duck-typed for GenerationOptions.voice/.music (api/generation.py:254-284).
- Asset kinds: "music" and "voice_sample" in PUBLIC_ASSET_KINDS (models.py:246).

Doc divergences:
- Design §5.1 says OpenAI "in the repo today: tts-1-hd". The code default is already gpt-4o-mini-tts (config.py:179), but without instructions.
- §5.1 cites "v2 aadhi/config.py:179" for gemini_tts_model; it is now line 180 and already migrated.
- The tts/gemini.py docstring (lines 5-6) says every catalogue voice is among the 30 prebuilt voices, but only 14 are listed. B3 asks for the full list.

## gaps_todos
There are no TODO, FIXME, XXX or NotImplemented markers in aadhi/providers or aadhi/usage (the only hits are _process.py:73, which catches NotImplementedError, and synth.py:33, a pragma). The gaps below are half-done features or unused code:
- providers/music/ does not exist (procedural, upload, lyria, fake). factory has no get_music. gemini_common._SERVICE_NAMES (53-59) has no music/lyria scope, so a Lyria adapter on GeminiCaller would log "Gemini service".
- No Azure TTS adapter and no Azure price row (pricing.py:159-176).
- No VoicePersona registry. voices.py is a flat catalogue, and is_known_voice (voices.py:180) is used only in tests. Nothing in production checks that options.tts_voice belongs to the chosen provider or language (pipeline/assets.py:303; GenerationOptions.tts_voice pattern only, pipeline/base.py:106).
- Gemini/OpenAI word timings: none from the provider and no per-sentence synthesis (design §5.1). Captions fall back to the length-weighted estimate in pipeline/audio.py:116.
- The TTS protocol has no style/instructions/pitch/model/speaker inputs (base.py:193-201). The fake TTS ignores any style.
- ElevenLabs voices are static. There is no /v1/voices fetch and no personal/server key path (elevenlabs.py:42, 59).
- OpenAI TTS does not chunk. Text over 4096 characters is refused (openai.py:84-86), and the 2000-token limit of gpt-4o-mini-tts is not checked locally (header 7-8).
- Edge does not expose pitch or volume (edge.py:99).
- The "calibration" asset kind and the GC kinds for music/voice_sample/calibration are missing (models.py:244-248; jobs/cleanup.py:61).
- H0 leftovers: the requirements.txt floor (google-genai>=2.0, line 30), and the Gemini TTS missing-model fallback to Edge with a warning.
- Unused or near-unused: TTS_WORD_TIMINGS (factory.py:42) is a second copy of each adapter's supports_word_timings. CHOOSABLE_IMAGE_PROVIDERS (factory.py:289) is used only inside factory.
- Test coverage: no direct tests for operations.JobCheckpoint.sending/not_sent outside the media-chain tests (OperationStore appears in tests/providers/test_media_chain.py and tests/storage/test_claim_operations.py). No tests reference _music_rate directly (it is covered through estimate_cost). There are no fakes or tests for music providers, because none exist.

## bugs_or_risks
1. H0 dependency floor not applied.
- requirements.txt:30 says `google-genai>=2.0,<3`, while the design requires `>=2.25` (DYNAMIC_VIDEO_DESIGN.md:2358).
- video/omni.py:5-6 was checked against 2.26 and needs client.aio.interactions plus google.genai._gaos (omni.py:94-95, 157).
- An install that does not use requirements.lock can resolve an older SDK. interactions_of (omni.py:167-171) would then raise AttributeError, which is mapped to a generic "failed" ProviderError. It cannot bill, but Omni silently never works.

2. H0 Edge fallback missing.
- When GEMINI_TTS_MODEL is retired or misspelt, GeminiTTS raises a plain ProviderError (tts/gemini.py:93, via gemini_error at gemini_common.py:185-195). It does not raise ProviderUnavailable.
- pipeline/assets.py:373-386 falls back to the server default only on ProviderNotConfigured. So every beat fails, instead of the H0 behaviour: fall back to Edge with a visible warning.

3. Conflicting image style suffix (bites B2/B4).
- providers/_common.py:183-189 style_prompt always appends settings.image_style_suffix ("purple and gold accent palette", config.py:185-187) unless that exact text is already in the prompt. The pipeline also appends it (pipeline/assets.py:680-681).
- When B2 drives image style from Presentation.asset_style / theme family, the server's purple/gold suffix will still be appended by GeminiImage.generate (image/gemini.py:106) and PollinationsImage.generate (pollinations.py:71). The prompt then contradicts the theme palette.

4. Gemini TTS ignores rate, but rate is in the cache key.
- tts/gemini.py:75 drops rate, but pipeline/assets.py:531 includes rate in the TTS clip key.
- Changing TTS_RATE or options.tts_rate therefore re-synthesises, and re-bills, identical Gemini audio.

5. OpenAI TTS length risk.
- The local limit is 4096 characters (tts/openai.py:30, 84-86), but gpt-4o-mini-tts accepts at most 2000 tokens (the same file's header, lines 7-8; design §5.1 "chunks ≤ 1,500").
- A long beat, or densely tokenised Indic text under 4096 characters, passes locally and then fails at the API with a 400. There is no chunking, so that beat's scene audio fails.

6. Fail-open pricing on malformed overrides.
- jobs/context.py:101-106 turns any exception from estimate_cost into $0.
- pricing.py assumes section values are dicts: _family iterates whatever prices.get(section) holds (310-318), and _per_provider calls entry.get (329-335).
- A PRICING_FILE with e.g. `"llm": "x"` or `"tts": {"default": 5}` makes those calls raise, so every such call is priced $0 and passes the budget. This contradicts the "fail closed" intent (pricing.py:41-44).

7. Possible under-pricing from longest-prefix families.
- An unlisted variant inherits its base family's price (pricing.py:310-318). Examples: "o3" (109) for o3-pro-style ids, and "gpt-5" (102) for gpt-5-pro-style ids.
- If such a variant costs more, budgets under-estimate it. The module keeps an over-estimate policy for Claude only (pricing.py:24-25, 151-154). This only matters if such models are configured or allow-listed (LLM_MODEL_ALLOWLIST).

8. Over-counting a Gemini image with no image.
- A blocked or text-only answer reports units=0 (image/gemini.py:97-98), but estimate_cost charges max(units, 1) images (pricing.py:380). Such an answer is billed a full image on top of its tokens.
- This errs high, so it is safe, but it is not documented as intentional.

9. Unbounded process-wide caches.
- gemini_common._POOLS (109-122) gains one KeyPool per distinct key list per scope (llm/tts/image/video/omni). Each personal Gemini key adds up to 5 entries, and nothing is ever evicted.
- GeminiLLM._uploads (llm/gemini.py:112, 151-152) keeps expired entries forever.
- OpenAILLM._file_ids (llm/openai.py:115, 129-133) never expires and has no lock. If OpenAI deletes a file, every later request reusing that cached id will be rejected for the life of the process.

10. Two copies of the word-timing flags.
- factory.TTS_WORD_TIMINGS (42) duplicates each adapter's supports_word_timings (edge.py:75, gemini.py:46, openai.py:52, elevenlabs.py:31, fake.py:89).
- /api/meta reads the factory copy (243-262), so a B3 change to one copy can make the Studio show the wrong capability.

11. ElevenLabs speed is clamped silently.
- elevenlabs.py:93 clamps speed to 0.7-1.2; rate_factor already clamps to 0.5-2.0 (tts/fake.py:42). A configured "-50%" becomes 0.7 with no warning.
- OpenAI's wider 0.25-4.0 clamp (openai.py:91) is unreachable for the same reason.

12. Pollinations download failures get no cooldown.
- ResponseTooLarge and UnsafeRedirect from fetch_limited end up as a plain ProviderError (pollinations.py:97-99). health.classify_failure treats that as "failed" (no cooldown) rather than invalid_output (health.py:76-86).

13. Real adapters import from the fake module.
- OpenAI and ElevenLabs import rate_factor from tts/fake.py (openai.py:27, elevenlabs.py:18). It is harmless today, but a B3 change to the fake's rate semantics would silently change real requests.

## b2_b3_touchpoints
B2 (visual system) hooks into this slice:
- Word timings feed kinetic.py phrase chunking and events.py R-17 cue anchoring.
  - Exact native timings: Edge (tts/edge.py:106-113, clamped 160-162), ElevenLabs (elevenlabs.py:123-132 via tts/alignment.py:12-44) and FakeTTS (tts/fake.py:56-82).
  - Gemini (tts/gemini.py:108) and OpenAI (tts/openai.py:106) return words=[]. The pipeline then estimates them by length (pipeline/audio.py:116-127, words_estimated=True). kinetic/events must therefore tolerate estimated words and should use BeatAudio.words_estimated/words_exact (schemas/manifest.py:43, 48).
  - The spoken text differs from the captions (normalize.py:173-180 turns "=" into "equals" and LaTeX into words). Provider words are mapped back onto the written tokens by character position (pipeline/audio.py:130-163).
- Image style per theme: _common.style_prompt (183-189) and its callers image/gemini.py:106 and image/pollinations.py:71 must accept a per-lecture style. Otherwise IMAGE_STYLE_SUFFIX (config.py:185) overrides the AssetStyle family/personality (schemas/screenplay.py:759-763, 791). The pipeline's copy is at pipeline/assets.py:680-681.
- Portrait (9:16) media: aspect support is in ASPECT_DIMS (_common.py:161-170, 9:16 = 720x1280), GEMINI_ASPECTS/IMAGEN_ASPECTS (image/gemini.py:31-32), VEO_ASPECTS (veo.py:80), OMNI_ASPECTS (omni.py:85) and the soft aspect warning (image/verify.py:117-127). Templates such as media_split and the backgrounds rely on these.
- GIFs: GifResult is a hotlinked URL only (base.py:310-317; giphy.py:31-57). B1 already lays the scene out without it in the MP4.
- No fonts or icons come from providers.

B3 (audio) hooks:
- Protocol: extend TTSProvider.synthesize (base.py:193-201) with optional style/instructions/pitch/speaker/model keyword arguments, defaulting so older adapters keep working.
  - SpeechResult (171-179) may need words_exact or model.
  - Every adapter and FakeTTS must accept the new keyword arguments. FakeTTS is the reference for exact timings (tts/fake.py:56-118).
- Persona registry: create aadhi/providers/tts/personas.py next to voices.py (CATALOGUE 101, EDGE_DEFAULTS 109, default_voice 145, voices_for 161).
  - Extend EDGE_VOICES (27-52) with bn/mr/gu/ur and en-AU William, and add them to LANGUAGES (voices.py:12), which must stay equal to pipeline/base.py SUPPORTED_LANGUAGES (line 45; tests/providers/test_voices_factory.py:22).
  - GEMINI_VOICES needs all 30 (66-81); OPENAI_VOICES needs 13 (54-64).
- Adapters:
  - Edge: pass pitch in the Communicate(...) call (edge.py:99); _RATE_RE is at 22.
  - Gemini: add speech_metadata.style and pause tags in the GenerateContentConfig/contents (tts/gemini.py:83-91), plus per-sentence synthesis for timings.
  - OpenAI: send instructions for gpt-4o-mini-tts only (tts/openai.py:93-97, header 6-12) and chunk to ≤ 1,500 characters.
  - ElevenLabs: v4/Flash model (config.py:178), stability/style mapping (elevenlabs.py:86-93), and keys from resolved Settings (42, 59).
  - Azure: a new adapter registered in factory.TTS_PROVIDERS/TTS_LABELS/TTS_WORD_TIMINGS (34-42), tts_configured (195-207) and get_tts build (219-240).
- Credentials:
  - Adding elevenlabs/azure to CREDENTIAL_PROVIDERS (models.py:74) will raise a KeyError at import in credentials.py:73, because KEY_PROVIDER_LABELS indexes factory.LLM_ENGINE_LABELS (factory.py:80-85), which has only the LLM engines. The labels must move or be extended.
  - resolve_keys swaps f"{provider}_api_key" (credentials.py:391-395), which works for elevenlabs_api_key. Azure also needs a region.
  - Update _PAID_TTS_KEYS (api/generation.py:241) and USAGE_KEY_PROVIDER (credentials.py:76).
- Cache key: any new audio-changing parameter (style, instructions, pitch, voice_settings, persona) must enter the TTS clip key (pipeline/assets.py:528-532), or cached clips will be reused wrongly.
- Music:
  - Create aadhi/providers/music/ (procedural, upload, lyria, fake) with a factory get_music. Follow get_tts's (name, settings) or the others' (settings, name) argument order consistently.
  - Lyria reuses GeminiCaller (gemini_common.py:232) with a new scope plus a _SERVICE_NAMES entry (53-59). Report Usage(provider="lyria" or "gemini", operation="music", units, seconds), already priced at pricing.py:213-222 and 391-413.
  - Lyria's personal-key billing works through USAGE_KEY_PROVIDER["lyria"] (credentials.py:76).
  - The procedural engine can reuse audio_utils.pcm_to_wav/wav_duration (tts/audio_utils.py:13-33), media.probe_media (media.py:98) and _process.run_process (91) for ffmpeg.
  - Add a fake music provider (gate 10.2.4) and a reset hook in tests/providers/conftest.py if it caches anything.
- Calibration and "Hear your terms": calibration synthesis is a normal TTS Usage row (characters) priced by pricing.py tts rows (159-176), and the budget pre-check is already wired (api/generation.py:254-272). It needs a "calibration" asset kind (models.py:244-248) and GC kinds (jobs/cleanup.py:61).
- Price rows: add Azure per character to DEFAULT_PRICES["tts"] (pricing.py:159-176). Today it falls to tts.default 30/1M.
- Mix and loudness: narration arrives at mixed sample rates (Edge/OpenAI 24 kHz, edge.py:164 and openai.py:107; ElevenLabs 44.1 kHz, elevenlabs.py:134; Gemini as reported, gemini.py:101-106). pipeline/audio.py:29 resamples everything to 24 kHz mono before the design's 24 → 48 kHz mix (§5.12).

## files
- aadhi/providers/base.py (324): Provider Protocols, Usage record, error hierarchy, media result dataclasses, the paid-job checkpoint protocol || Usage 32-42 (provider, model, operation: llm|vision|tts|image|video|gif|music; tokens/characters/seconds/units/meta); UsageSink 45; ProviderError 51 (status, provider); RateLimited 60; ContentBlocked 64; ProviderNotConfigured 68; ProviderUnavailable 72 (401/402/403 of keyless/server providers); InvalidMediaOutput 81; OperationLost 86; PaidSubmissionUncertain 91; FileInput 100; ImageInput 109; SemanticValidator 117; LLMProvider 120-158 (generate_json with validate/validation_retries; generate_text); WordTiming 164-168 (clip-relative seconds); SpeechResult 171-179 (audio, mime, measured duration, words, voice, provider, sample_rate); TTSProvider 182-201 (name, supports_word_timings, default_voice, voices, synthesize(text, *, voice, language, rate='+0%', on_usage)); ImageResult 207 (warnings); optional adapter attrs comment 218-223 (paid, aspects, max_prompt_chars, resumable); ImageProvider 226; VideoResult 235; VideoProvider 245; OperationCheckpoint 260-276 (submitted, usage_recorded, optional sending/not_sent); ResumableVideoProvider 279-307 (generate(checkpoint=), resume); GifResult 310; GifProvider 320
- aadhi/providers/factory.py (471): Builds and caches providers per Settings identity; engine/model resolution; /api/meta lists; media chains and admin status || TTS_PROVIDERS 34; TTS_LABELS 35; TTS_WORD_TIMINGS 42 (duplicates the adapters' supports_word_timings); _cached 55-70 (key (id(settings),kind,name), LRU 64); _fake_allowed 73 (APP_ENV=test or that provider configured as fake); LLM_ENGINES 79; LLM_ENGINE_LABELS 80-85 (also used by credentials.KEY_PROVIDER_LABELS); LLM_TIERS 86; engine_for_model 92; llm_configured 104; llm_models 118-138; get_llm(settings, engine) 141; available_llm_engines 173; tts_configured 195-207; get_tts(name, settings) 210-240 (NOTE: argument order differs from the other getters); available_tts_providers 243-262 -> [{id,label,configured,word_timings,voices}]; IMAGE_PROVIDERS/VIDEO_PROVIDERS 267-268; media_chain 278; CHOOSABLE_IMAGE_PROVIDERS 289; available_image_providers 292; media_class 308; media_unavailable_reason 339 (gemini/veo/omni need a Gemini key); get_image(settings,name) 349; get_video(settings,name) 374; media_configured 399; media_provider_status 404-457; get_gif 460-471 (GIPHY key, else fake in tests, else None). No get_music.
- aadhi/providers/gemini_common.py (423): Shared Gemini plumbing: key pool rotation, the retrying caller, error classification and mapping, response helpers, Usage from usage_metadata || GEMINI_HOST 37; BLOCK_FINISH_REASONS 40; RECITATION_FINISH_REASON 48; _SERVICE_NAMES 53-59 (llm,tts,image,video,omni; no music scope); KeyPool 62-106; key_pool(keys, scope) 113 (process-wide _POOLS 109, never evicted); reset_key_pools 125; classify_gemini_error 161; gemini_error 179; genai_types 202 (SDK imported off-loop); GeminiCaller 232-341 (per_loop clients + loop_lease 290; call 311 with wait_for + call_with_retries, max_attempts default min(10,4+pool size)); block_reason 360; check_blocked 380; is_truncated 389; response_parts/response_text 394-404; usage_from_response 407-423 (thinking tokens billed as output; meta thoughts_tokens, cached_tokens)
- aadhi/providers/_common.py (206): Small shared helpers: redacted errors, usage emission, fingerprints, per-loop cache, aspect sizes, style suffix || make_error 48-76 (redact before and after truncating to 300 chars; strips a dangling partial secret); error_for_status 79 (429->RateLimited, 401/402/403->ProviderUnavailable); emit_usage 93 (sink exceptions such as BudgetExceeded propagate); fingerprint 102 (sha256[:16]); LoopLocal 107-158; ASPECT_DIMS 161-170 (16:9 1280x720, 9:16 720x1280 ...); aspect_dims 173; stable_seed 178; style_prompt 183-189 (appends settings.image_style_suffix unless already present); describe_exception 192; safe_attr 199
- aadhi/providers/_retry.py (131): tenacity retry policy for every adapter, with job-log notices || MAX_RETRY_AFTER_S 31; ErrorInfo 36; NOT_RETRYABLE 46; call_with_retries 49-126 (exponential backoff with jitter, honours Retry-After up to max_retry_after, 0.2 s when the key pool has a fresh key, WaitingWatch per attempt, notify_retry before each sleep); no_sleep 129
- aadhi/providers/_http.py (164): httpx helpers for REST providers: status errors, Retry-After, a safe size-capped download with https redirects limited to allowed hosts || HttpStatusError 21; parse_retry_after 31; raise_for_status 59; classify_http_error 69 (429, 408/409/425, 5xx, transport errors); UnsafeRedirect 82; ResponseTooLarge 86; host_allowed 90; fetch_limited 102-144; _read_capped 147; loop_local_client 162
- aadhi/providers/_notify.py (381): Plain-language retry and 'still waiting' notices for the job log, sent to a sink held in a ContextVar, plus throttling || WAITING_AFTER_S/EVERY_S/MAX 36-38; ProviderNotice 51-76 (data()); PROVIDER_NOTICE ContextVar 90; provider_notices 93; retry_notice 148; notify_retry 180; waiting_notice 192; WaitingWatch 205-298; hold_waiting 305; release_waiting 318; NoticeThrottle 335-381
- aadhi/providers/_stream.py (110): Stall detection for streamed LLM requests (a limit for the first sign of life and an idle limit) || UPLOAD_ALLOWANCE_BYTES_PER_S 31; StreamStalled(TimeoutError) 36; payload_size 46; first_response_limit 58; StallGuard 64-110 (touch(expect_silence))
- aadhi/providers/_process.py (91): Async subprocess runner with a timeout that kills the whole process tree (ffmpeg/ffprobe), with a thread fallback for loops that cannot spawn || kill_tree 27; _run_blocking 40; run_process 57-91 (ProviderError provider='local' when the binary is missing or times out)
- aadhi/providers/_lazy.py (34): Imports heavy SDKs in a worker thread and caches the fully initialised module || import_off_loop 18; imported 32
- aadhi/providers/media.py (131): ffprobe inspection (duration, size, streams, sample rate); bytes are written to a temp file first || _SNIFF 17; MediaInfo 31-39 (duration, width, height, format_name, has_video, has_audio, sample_rate); sniff_extension 42; _parse_probe 65; probe_media 98-131 (-protocol_whitelist file,pipe)
- aadhi/providers/health.py (179): Failure categories for media providers and a process-wide cooldown map that reorders the chain || BLOCKED/UNAVAILABLE/RATE_LIMITED/TRANSIENT/INVALID_OUTPUT/NOT_CONFIGURED/LOST/FAILED 49-56; classify_failure 62-89; fallback_allowed 92 (never after a safety block); cooldown_seconds 97; Cooling 106; ProviderHealth 112-166 (note_failure, note_success, cooling, order, snapshot); provider_health 172; reset_provider_health 177
- aadhi/providers/operations.py (216): Durable records of paid long-running jobs in the job payload, mirrored on the asset generation claim, so a restart resumes instead of paying again || PAYLOAD_KEY 'provider_operations' 48; statuses pending/submitting/submitted/completed/failed/abandoned/lost/ambiguous 49-56; MAX_RECORDS 57; OperationRecord 63-92 (key_fingerprint only, never the key); OperationStore 108-187 (get 122 also prefers an inherited claim operation; save 144 writes memory, the claim row and a lease-fenced jobs row); JobCheckpoint 190-216 (submitted, usage_recorded, sending, not_sent)
- aadhi/providers/openai_common.py (178): OpenAI client per event loop, retry classification, error mapping (including errors inside streams) || OPENAI_HOST 18; _STREAM_RETRY_CODES 23; OpenAIStreamError 30; classify_openai_error 47 (insufficient_quota is not retried); openai_error 77; OpenAICaller 109-178 (SDK max_retries=0; service name for notices)
- aadhi/providers/anthropic_common.py (187): Anthropic client per event loop, retries (429/529/5xx and mid-stream httpx2 errors), error mapping || _STREAM_ERROR_STATUS 23; error_status 35; _stream_transport_errors 43 (httpx2, the SDK's own transport); classify_anthropic_error 58; anthropic_error 82; AnthropicCaller 106-187 (timeout plus idle read timeout)
- aadhi/providers/llm/gemini.py (281): Gemini LLM: generate_content with response_json_schema; inline parts or the Files API under a 19 MB budget; temperature sent only to 1.x/2.x models || INLINE_LIMIT_BYTES 52; UPLOAD_TTL_S 53; sends_temperature 60; base64_size 65; plan_uploads 70; GeminiLLM 88-281 (_uploaded_uri 127 caches in _uploads 112, never evicted; _complete 173; generate_json 217; generate_text 252 re-asks once after a recitation stop)
- aadhi/providers/llm/openai.py (385): OpenAI LLM: streamed Responses API (chat-completions fallback), a stall guard, a no-stream fallback for unverified orgs, json_schema with strict false || _REASONING_MODEL 51; resolve_model 60 (maps Gemini names to gpt-4.1/-mini); _stream_refused 80; OpenAILLM 97-385 (_file_ids 115, never expires; _stream_response 177; _responses 218; _chat 263; generate_json 335; generate_text 364)
- aadhi/providers/llm/anthropic.py (375): Claude LLM: streamed beta Messages with output_config.format/effort, prompt-cache breakpoints, server-side refusal fallbacks with per-attempt usage, schema-in-prompt fallback || MAX_REQUEST_BYTES 46; MAX_TOKENS_CAP 47; FALLBACK_BETA 48; _EFFORT_MODELS 52; _NO_XHIGH 56; _FALLBACK_MODELS 57; resolve_model 63; effort_for 73; supports_fallbacks 85; max_tokens_for 90; _hop_usage 96 (cache_read/write in meta); AnthropicLLM 130-375 (_request 184; _rejected_feature 223; _usages 240; _completion 262; _complete 274)
- aadhi/providers/llm/structured.py (221): Parse/validate/semantic-check/re-ask loop shared by every LLM adapter || CURRENT_ATTEMPT/CURRENT_PROBLEMS ContextVars 37-39; Turn 50; Completion 58-69 (extra_usage); parse_json_text 82; format_validation_error 112; reask_message 123; settle 134 (reports usage, then raises ContentBlocked); recitation_error 145; check_output 157; run_structured 179-221
- aadhi/providers/llm/schema.py (316): Pydantic model to provider JSON schema conversion plus the LLM-compatibility gate || PROVIDERS 36; _FORBIDDEN_KEYS 41; LLMSchemaError 70; llm_compatibility_problems 147; assert_llm_compatible 155; constraint_hint 198 (Anthropic restates constraints in descriptions); _convert 218; to_provider_schema 301 (lru-cached, deep copy); schema_name 313
- aadhi/providers/llm/fake.py (246): Deterministic offline LLM: scripted responders per schema name, else synthesised schema-valid instances; runs the same structured loop || current_attempt 45; FakeLLM 104-246 (class-level _responders; register 120; unregister 126; register_text 131; clear_registry 140; calls deque of MAX_CALL_RECORDS 100; generate_json 157; generate_text 212)
- aadhi/providers/llm/synth.py (449): Builds schema-valid instances from the fake provider schema (minimal/filled/rich), including regex examples and repair || example_for_pattern 160; _value 277; synthesize 341; _repair 388; candidates 447 (lru-cached JSON)
- aadhi/providers/tts/voices.py (181): Static voice catalogue and defaults per TTS provider and language || LANGUAGES 12 (en-IN,en-US,en-GB,ta-IN,hi-IN,te-IN,kn-IN,ml-IN; a test pins it to pipeline SUPPORTED_LANGUAGES); Voice 16; EDGE_VOICES 27-52 (24 voices); OPENAI_VOICES 54-64 (9); GEMINI_VOICES 66-81 (14); ELEVENLABS_VOICES 83-94 (10 static ids); FAKE_VOICES 96; CATALOGUE 101; EDGE_DEFAULTS 109-118; EDGE_FALLBACK 119; MULTILINGUAL_DEFAULTS 120-125 (nova, Kore, Rachel, fake-female); _normalise_language 128; default_voice 145; voices_for 161; is_known_voice 180 (used only by tests)
- aadhi/providers/tts/edge.py (164): Edge read-aloud TTS (edge-tts 7.2.8): MP3 with native WordBoundary timings || EDGE_HOST 19; TICKS_PER_SECOND 20 (100 ns units); _VOICE_RE 21; _RATE_RE 22 (^[+-]\d{1,3}%$); classify_edge_error 52; load_edge_tts 44; EdgeTTS 71-164 (_stream 98: Communicate(text, voice, rate=, boundary='WordBoundary', receive_timeout); punctuation stripped from words; synthesize 118: usage characters, model=voice id, words clamped to the probed duration, sample_rate hard-coded 24000; pitch and volume not exposed)
- aadhi/providers/tts/gemini.py (109): Gemini speech model with prebuilt voices; WAV out (headerless PCM gets a WAV header); no word timings; rate ignored || _VOICE_RE 29; _audio_from_parts 33; GeminiTTS 42-109 (GeminiCaller scope 'tts', timeout 180 s; synthesize 66: contents=text, no style or speech_metadata; usage reported before check_blocked; model=settings.gemini_tts_model)
- aadhi/providers/tts/openai.py (107): OpenAI audio.speech TTS: MP3, no word timings, rate mapped to speed || MAX_INPUT_CHARS 4096 30; _VOICE_RE 31; _read_binary 34; OpenAITTS 48-107 (instructions deliberately not sent, header lines 6-12; speed from fake.rate_factor 91)
- aadhi/providers/tts/elevenlabs.py (134): ElevenLabs REST /with-timestamps: MP3 at 44.1 kHz, character alignment converted to words || OUTPUT_FORMAT mp3_44100_128 23; _VOICE_RE 24; ElevenLabsTTS 27-134 (key only from settings.elevenlabs_api_key 42/59; voice_settings stability 0.5, similarity 0.75 hard-coded 89; speed clamped 0.7-1.2 93; alignment or normalized_alignment 123; usage before decode/probe 114)
- aadhi/providers/tts/fake.py (118): Deterministic offline TTS: 24 kHz WAV with one tone burst per word and exact word timings; also exports rate_factor, which real adapters use || SAMPLE_RATE 24 000; LEAD_S 0.12; TAIL_S 0.2; SECONDS_PER_CHAR 0.06; GAP_S 0.06 (24-33); rate_factor 36-42 (clamped 0.5..2.0); render 56-82; FakeTTS 85-118
- aadhi/providers/tts/alignment.py (44): Groups per-character timings into words || words_from_characters 12-44
- aadhi/providers/tts/audio_utils.py (56): PCM to WAV, exact WAV duration and sample rate, probe_duration || pcm_to_wav 13; wav_duration 24; wav_sample_rate 36; probe_duration 45-56
- aadhi/providers/tts/normalize.py (235): Text to speakable text (markup stripped, $latex$ and raw LaTeX spoken, units and symbols as words) and the pronunciation lexicon || normalize_for_speech 173-180; _strip_markup 64; _raw_units 75; _raw_maths 99; _symbols 110-160; _cleanup 163; apply_lexicon 209-235 (longest match first, whole words, ASCII case-insensitive, language matching)
- aadhi/providers/tts/mathspeech.py (396): LaTeX to spoken words via a recursive-descent reader that never raises || MAX_DEPTH 21; subscript_text 71; _Reader 88-365 (seq, scripts, command, big_operator, unit_after_number); plain_text 368; latex_to_speech 375-396
- aadhi/providers/tts/speech_words.py (294): Per-language word and template tables (en/ta/hi) for maths speech || WORDS 11; TEMPLATES 181; GREEK 226; UNICODE_GREEK 249; PREFIX_KEYS 260; lang_key 266; table 273; templates 280; greek 286 (lru-cached dicts)
- aadhi/providers/image/gemini.py (116): Gemini image models (generate_content with IMAGE output, thought images skipped) or Imagen || GEMINI_ASPECTS 31; IMAGEN_ASPECTS 32; GeminiImage 35-116 (paid True; aspects property 55; _imagen 59; _gemini 77, usage units=1 only when an image is present; generate 104 uses style_prompt and inspect_generated; no variant kwarg)
- aadhi/providers/image/pollinations.py (103): Keyless Pollinations GET image API: seeded and reproducible, size-capped safe fetch, 402 mapped to ProviderUnavailable || POLLINATIONS_BASE_URL 29; MAX_PROMPT_CHARS 1500 32; PollinationsImage 35-103 (generate(prompt, aspect, on_usage, variant=0) 66; the pipeline probes for the variant kwarg)
- aadhi/providers/image/verify.py (127): Pillow checks (format allow-list, pixel cap, minimum side, byte cap) plus soft warnings (flat image, aspect far off) || MAX_PIXELS 24; MIN_SIDE 25; ImageInfo 31; looks_blank 46; inspect_image_sync 60; inspect_image 96; inspect_generated 103; media_warnings 117
- aadhi/providers/image/fake.py (63): Deterministic gradient PNG || render_png 20; FakeImage 47-63
- aadhi/providers/video/veo.py (371): Veo long-running clips: checkpoint before polling, resume, abandoned-usage estimate, no double submit after an unanswered create || VEO_ASPECTS 80; NEGATIVE_PROMPT 81; DEFAULT_VIDEO_SECONDS 82; VEO3_DURATIONS 84; clip_seconds 90; VeoVideo 106-371 (paid/resumable/reports_sending 114-118; billed_seconds 188; _usage 194 per requested seconds, meta status/estimated; generate 256 with the uncertain flag and PaidSubmissionUncertain 275-309; 404/410 -> ProviderUnavailable 305; resume 330)
- aadhi/providers/video/omni.py (518): Gemini Omni clips through client.aio.interactions (background=True); per-token usage; same paid-job rules as Veo; per-loop clients with a lease || OMNI_ASPECTS 85; RESOLUTION 720p 86; VIDEO_TOKENS_PER_SECOND 5792 87; ESTIMATED_SECONDS 10 88; EXCLUSIONS 90; translate_error 113; _no_sdk_retries 149; interactions_of 167; _video_tokens 174; OmniVideo 192-518 (GeminiCaller per_loop=True scope 'omni' 218; _usage 268 seconds=video tokens/5792; _poll 311 cancels at the deadline; _finish 360 requires MP4; generate 397 / _generate 414; resume 475)
- aadhi/providers/video/fake.py (78): ffmpeg testsrc2 4 s H.264 clip with the hue taken from the prompt || FAKE_SECONDS 17; render_test_video 24; FakeVideo 50-78
- aadhi/providers/gif/giphy.py (127): GIPHY search; hotlinked https giphy.com URLs only (live player only) || RATINGS 16; MAX_RETRY_AFTER_S 18; _is_giphy_url 21; pick_result 31; GiphyGif 60-127
- aadhi/providers/gif/fake.py (56): Offline animated GIF returned as a data: URL || render_gif 20; FakeGif 37-56
- aadhi/providers/__init__.py, llm/__init__.py, tts/__init__.py, image/__init__.py, video/__init__.py, gif/__init__.py (12): Package docstrings only || none
- aadhi/usage/pricing.py (439): Estimated USD per Usage from DEFAULT_PRICES plus scheduled steps plus PRICING_FILE; fails closed for unknown operations || CACHE_WRITE/READ_MULTIPLIER 65-66; OMNI constants 74-76; DEFAULT_PRICES 81-238 (llm 83-148, llm_provider_default 151-157, tts 159-176, image 178-187, image_tokens 190-196, video 198-205, gif 207, music 213-222, scheduled 224-237); _deep_merge 244; _due_steps 254; load_prices 268-301 (cached by path, mtime and day); reset_price_cache 304; _family 310 (longest prefix); _per_provider 329; _llm_rate 341; _llm_cost 354; estimate_cost(usage, settings, *, on) 369-395; _music_rate 398; _music_cost 411; _unknown_operation_cost 425-439
- aadhi/usage/service.py (241): UsageEvent recording, budgets (per job and daily UTC per user, server-billed only), summaries || BILLED_TO 34; record_usage 59; spent_today 91; user_daily_budget 104; check_budget 111-139 (raises jobs.base.BudgetExceeded); usage_for_user 152; usage_for_project 186; usage_admin 209
- aadhi/usage/__init__.py (1): Package docstring || none