# Research report: voices and background music for Aadhi EduEngine videos (topic key: `audio`)

Researched 2026-10-09. The repo was only read, never edited. No paid API calls were made. The only network calls besides reading web pages went to the free Microsoft Edge read-aloud service: one voice-list call and five short test syntheses. Local measurements used ffmpeg 8.1.1 (gyan.dev full build) and numpy/scipy/soundfile from the project venv. The scripts are in `scratchpad/research/tools/` and the outputs in `scratchpad/research/out/`.

**Evidence grades used below:**
- **[P]** is a primary source I read this session: official docs, pricing or terms pages, the paper itself, or the live API.
- **[M]** is something I measured locally this session.
- **[S]** is a secondary or third-party source and should be verified before use.
- **[J]** is my engineering judgement or common practice, with no study behind it.

> **Limitation.** The session's shared web-search budget ran out partway through. Topics with fewer sources than planned are marked "not verified" in §6. Beatoven, Mubert and Soundraw got search snippets only. SoundFont licences, MMS-aligner weight licences and Instagram/TikTok loudness specs were not verified at all.

---

## 0. Executive summary

1. **Today's mix pumps hard and the BGM is monotonous [M].**
   - The single `video_template/bgm.mp3` is 80 s long, mastered loud at **−9.7 LUFS** and loops about 4× in a 5-minute video.
   - With the current `sidechaincompress=threshold=0.015:ratio=8:attack=15:release=450` at `volume=0.06`, the music's momentary loudness swings by **24–26 LU** between words and pauses. That is audible "pumping".
   - How deep the music ducks depends on how loud each voice is: about 22 LU under Neerja and about 28 LU under Prabhat.
2. **Voices differ a lot in loudness and pace [M].**
   - Edge voices at `rate=+0%` range from **−16.9 to −22.0 LUFS** and from **147 to 201 wpm** on the same text.
   - Rotating voices without normalising loudness and calibrating rate per voice will sound inconsistent from one video to the next.
3. **A configured TTS model is about to be shut down [P].**
   - `gemini_tts_model = "gemini-2.5-flash-preview-tts"` in `aadhi/config.py` shuts down **17 Nov 2026**. The same date applies to `gemini-2.5-pro-preview-tts` and `gemini-3.1-flash-tts-preview`.
   - Google names `gemini-3.8-flash-tts` or `gemini-3.8-flash-lite-tts` as replacements. They are cheaper ($9 or $6 per 1M audio tokens until 31 Dec 2026, doubling on 1 Jan 2027) and add style annotations, inline tags and an extended voice library with "hundreds" of voices.
4. **Best value for voice variety [P]:**
   - **Free:** Edge. The live catalogue has **322 voices**, including 2 per Indic language and about 40 English voices.
   - **Cheap and official:**
     - Gemini 3.8 Flash TTS: about $0.07 per 5-minute video, 30 prebuilt voices plus the extended library, 130+ languages including all target Indic languages.
     - Azure Neural: the same voices as Edge, plus SSML styles and word boundaries, with 0.5M characters free per month.
     - Google Chirp 3 HD: 30 voices in all 9 target Indian locales, $30 per 1M characters.
     - Sarvam Bulbul: an Indian-language specialist with 35+ voices on v3 and 200+ on v4-flash.
   - **Premium:** ElevenLabs v4, 90+ languages, $0.08 per 1K characters.
5. **Word timings come natively only from Edge, Azure and ElevenLabs** (the `/with-timestamps` endpoint). Gemini, OpenAI, Chirp 3 HD and Sarvam don't document timestamps. They need sentence-level chunking plus forced alignment or the existing `estimate_words` fallback [P].
6. **Best AI music options for a SaaS in 2026 [P]:**
   - **Google Lyria 3.5** through the Gemini API: **$0.08 per full song** (a couple of minutes, 44.1 kHz stereo, MP3/WAV, SynthID). Instrumental by prompt. Google claims no ownership of outputs.
   - **ElevenLabs Eleven Music**: $0.15/min, up to 5 min per track, `force_instrumental`, and a **composition plan with enforced per-section durations**. That plan lets one score follow the video's chapters exactly. Commercial use needs a Starter or higher plan, and some contexts are excluded.
   - **Lyria RealTime** (experimental): instrumental-only, 48 kHz, with BPM, density, brightness and drum/bass mute controls. It renders in real time, so a 5-minute bed takes 5 minutes.
7. **Royalty-free libraries are a poor fit for automated SaaS use [P].**
   - Pixabay has **no audio API** and forbids bulk or systematic downloading.
   - Freesound's API is free **only for non-commercial use**.
   - Jamendo's free API tier is non-commercial; commercial use goes through Jamendo Licensing.
   - YouTube Audio Library has no API and is built around YouTube.
   - The clean commercial route is the **Epidemic Sound Partner API** (quote-based, with sub-licensing to end users).
   - A small hand-curated pack under CC-BY (for example incompetech) with credits is a free fallback.
8. **Procedural music in pure numpy/scipy (BSD licences) works [M].**
   - A prototype renders a **5-minute stereo lo-fi, ambient or bright bed in 9–13 s** on this laptop, with no licence risk.
   - Quality is "pleasant generic bed" at best [J]. Use it as an offline/demo fallback, not the main source.
9. **The learning research is clear and cuts against "louder, busier music" [P].**
   - Background music played under narration **reduced retention and transfer** in Moreno & Mayer (2000).
   - **Fast and loud** instrumental music hurt reading comprehension, while slow or soft music did not (Thompson, Schellenberg & Letnic, 2011/12).
   - Listeners preferred **at least 10 LU** of speech-over-music loudness difference, and non-experts about 4 LU more (Torcoli et al., 2019).
   - So make music prominent only where nobody is speaking (intro, transitions, think-pauses, recap stings), keep it quiet and steady under speech, and never use lyrics.
10. **Speaking rate:** edX data show engagement rising with faster, more enthusiastic speech (mean 156 wpm; 185–254 wpm was the most engaging bucket). The authors warn that rate stands in for enthusiasm, so the lever is energetic delivery, not raw speed [P].

---

## 1. Repo baseline (what exists today)

| Area | Current state (read from the repo) | Issue |
|---|---|---|
| Voice catalogue (`aadhi/providers/tts/voices.py`) | Edge 24 voices across 8 languages; OpenAI 9 voices (no `ballad`, `verse`, `marin`, `cedar`); Gemini 14 of 30; ElevenLabs 10 hard-coded premade IDs | Missing bn-IN, mr-IN, gu-IN, ur-IN (all on Edge) and about 25 English Edge voices. OpenAI's recommended `marin`/`cedar` are absent. |
| Models (`aadhi/config.py`) | `gemini-2.5-flash-preview-tts`, `tts-1-hd`, `eleven_multilingual_v2` | Gemini model shuts down 17 Nov 2026. `tts-1-hd` has no `instructions` steering. `eleven_multilingual_v2` is now "previous generation" and covers only Hindi and Tamil among Indic languages. |
| Word timings | Edge `WordBoundary` (native), ElevenLabs character alignment, Gemini and OpenAI none (falls back to `estimate_words` in `aadhi/pipeline/audio.py`) | — |
| BGM (`aadhi/compose/ffmpeg.py::bgm_filter`) | One looping `bgm.mp3`, `volume=0.06`, 1 s fade-in, 2 s fade-out, `sidechaincompress threshold=0.015 ratio=8 attack=15 release=450 makeup=1`, `amix normalize=0` | No final loudness normalisation or true-peak limiter. One track for every video. |

**Measurements [M]:**

| Measured | Result |
|---|---|
| `bgm.mp3` | 80.0 s, mp3 256 kbps, **I = −9.7 LUFS**, LRA 2.8 LU, peak 0.0 dBFS |
| Music level after `volume=0.06` | −35.0 LUFS |
| Music after the current ducking (18 s narration) | Neerja: I = −42.9 LUFS, momentary range −57.1 to −33.3 (**23.8 LU swing**). Prabhat: I = −44.6 LUFS, range −61.1 to −35.6 (**25.5 LU swing**) |

Edge narration, same 45-word English text (Tamil: one sentence), speech span measured with `silencedetect` at −45 dB:

| Voice (rate) | Integrated loudness | Speech rate |
|---|---|---|
| en-IN-NeerjaNeural (+0%) | −20.7 LUFS | 159 wpm |
| en-IN-PrabhatNeural (+0%) | −16.9 LUFS | 147 wpm |
| en-US-AndrewMultilingualNeural (+0%) | −22.0 LUFS | 183 wpm |
| en-US-EmmaMultilingualNeural (+10%) | −17.9 LUFS | 221 wpm (about 201 wpm at +0%) |
| ta-IN-PallaviNeural (+0%) | −20.8 LUFS | — |

All Edge clips start with 0.13–0.26 s of silence.

---

## 2. Text-to-speech (A)

### 2.1 Provider comparison (Oct 2026)

| Provider / model | Voices | Indian-language coverage | Delivery control | Word timings | Price (list) | Licence / commercial notes | Status |
|---|---|---|---|---|---|---|---|
| **Microsoft Edge read-aloud** via `edge-tts` 7.2.8 (LGPLv3) | 322 live; see §2.2 | en-IN 3, hi/ta/te/kn/ml/bn/mr/gu/ur-IN 2 each, plus ta-LK/MY/SG and bn-BD accents | `rate` (±%), `pitch` (Hz), `volume`. **No custom SSML or styles**: the service only accepts one `<voice>` with one `<prosody>` [P README] | **Yes**, `WordBoundary` (the app requests it explicitly) | Free | Unofficial use of Edge's consumer service; no SLA or licence grant [J]. The library is LGPLv3 [P metadata]. | Works today |
| **Azure AI Speech** (licensed version of the same voices) | en-IN about 14 standard voices plus 6 DragonHD (Aarti, Arjun, Neerja, Diya, Meera, Lavanya); hi-IN 9 standard plus 4 MAI-Voice-2 HD (preview); ta/te/kn/ml/bn/mr/gu 2 each [P] | As listed | Full SSML. **Styles:** en-IN Neerja and hi-IN Swara (cheerful, empathetic, newscast); en-US Jenny/Aria/Guy and others (excited, friendly, narration-professional…); hi-IN MAI-Voice-2 voices 13–18 styles [P] | **Yes**, WordBoundary events | F0 tier 0.5M characters/month free [P]. Pay-as-you-go about $15–16 per 1M characters [S] (official page hides figures) | Licensed commercial service | GA (HD voices partly preview) |
| **Gemini 3.8 Flash TTS** `gemini-3.8-flash-tts` and **Flash-Lite TTS** `gemini-3.8-flash-lite-tts` | 30 prebuilt voices (Zephyr Bright, Puck Upbeat, Charon Informative, Kore Firm, Fenrir Excitable, Leda Youthful, Orus Firm, Aoede Breezy, Callirrhoe Easy-going, Autonoe Bright, Enceladus Breathy, Iapetus Clear, Umbriel Easy-going, Algieba Smooth, Despina Smooth, Erinome Clear, Algenib Gravelly, Rasalgethi Informative, Laomedeia Upbeat, Achernar Soft, Alnilam Firm, Schedar Even, Gacrux Mature, Pulcherrima Forward, Achird Friendly, Zubenelgenubi Casual, Vindemiatrix Gentle, Sadachbia Lively, Sadaltager Knowledgeable, Sulafat Warm), an **Extended Voice Library** ("hundreds", filterable by accent/region/persona/context), voice design and voice replication [P] | 130+ languages (Flash) / 100+ (Lite), including Hindi, Tamil, Telugu, Kannada, Malayalam, Bangla, Marathi, Gujarati, Punjabi, Odia, Assamese… [P] | `speech_metadata.style` per turn (e.g. "cheerful and friendly"); inline tags `<short pause>`, `<long pause>`, `<laugh>`, `<sigh>`, `<whispers>`, `<breath>`…; up to 2 speakers per request [P] | **No** (not documented) | Flash: $0.50 per 1M input tokens, **$9 per 1M audio tokens** (from 1 Jan 2027: $1 and $18). Lite: $0.50 and **$6** ($1 and $12 in 2027). 25 audio tokens per second. Free tier exists [P] | Google claims no ownership of outputs. Free-tier data may be used for training. **Users must be 18+, and apps "directed at or likely accessed by" under-18s are excluded** [P terms] | Current. Older TTS models shut down 17 Nov 2026 [P] |
| **OpenAI** `gpt-4o-mini-tts` (snapshot `-2025-12-15`) | 13: alloy, ash, ballad, coral, echo, fable, nova, onyx, sage, shimmer, verse, **marin, cedar** ("best quality") [P] | Follows Whisper's language list (Hindi and others); voices optimised for English [P] | `instructions` (accent, emotion, intonation, speed, tone, whispering) [P] | **No** | $0.60 per 1M text tokens, **$12 per 1M audio tokens**; 2,000-token input limit [P]; about $0.015/min [S] | **Must disclose to end users that the voice is AI-generated** [P] | Current; `tts-1`/`tts-1-hd` still listed |
| **ElevenLabs** `eleven_v4` / `eleven_v4_turbo` (flagship), `eleven_v3`, `eleven_multilingual_v2`, `eleven_flash_v2_5` | Large voice library (API access to the library needs a paid plan) [P] | v4 and v3: Hindi, Tamil, Telugu, Kannada, Malayalam, Bengali, Marathi, Gujarati. v2 and Flash: **Hindi and Tamil only** [P] | v4 Turbo and v3 Conversational: audio tags. `voice_settings`: stability, similarity_boost, style, speed. `previous_text`/`next_text` stitching [P] | **Yes**, `/with-timestamps` gives character alignment. Support on v4/v3 is **not documented**, so probe it [P] | v4 **$0.08 per 1K characters** (promo $0.022 until 12 Oct), v4 Turbo $0.04 ($0.011 promo), v3 $0.08, Multilingual v2 $0.08, Flash $0.04 [P] | Free plan is non-commercial and requires an "elevenlabs.io" credit. Paid plans include a commercial licence, except for Beta Services [P] | v4 current; v2 "previous generation" |
| **Google Cloud TTS Chirp 3: HD** | 30 voices (same names as Gemini), in every target Indian locale [P] | en-IN, hi, ta, te, kn, ml, bn, mr, gu-IN (pa-IN preview) [P] | `speaking_rate` 0.25–2.0; `[pause short]`/`[pause long]` in `markup`; SSML subset in preview [P] | **No** (not documented) | **$30 per 1M characters**, first 1M free [S official table via search] | Standard GCP terms | GA (updated 2026-10-07) |
| **Sarvam AI Bulbul** v3 / v4-flash | v3: 35+ named speakers (Shubh, Aditya, Ritu, Priya, Neha, Kavya, Rahul…). v4-flash: 200+ persona voices [P] | hi, bn, ta, te, gu, kn, ml, mr, pa, od, en-IN; `enhi` code-mixing on v4-flash [P] | `pace` 0.5–2.0; native script strongly recommended [P] | Not documented | v3 **₹30 per 10K characters** (beta), v2 ₹15; ₹1,000 free credits [S] | Indian vendor; terms not reviewed | v4-flash "latest" |
| **Kokoro-82M** (self-hosted) | 54 voices (v1.0) [P] | Hindi not confirmed on the model card [P] | — | Token timestamps (not verified) | Free (Apache-2.0 weights) [P] | Non-English text goes through espeak-ng, which is GPL; check before bundling [J] | v1.0 (27 Jan 2025) |

**Cost per 5-minute English video** (about 775 words, about 4,700 characters, 300 s of audio). Arithmetic is from the list prices above; Indic scripts usually run 1.5–2× the characters [J].

| Provider | Cost per video |
|---|---|
| Edge | $0 |
| Gemini 3.8 Flash-Lite TTS | **$0.045** ($0.09 from 2027) |
| Gemini 3.8 Flash TTS | **$0.068** ($0.135 from 2027) |
| Azure Neural | ~$0.07 |
| OpenAI gpt-4o-mini-tts | ~$0.075 [S] |
| Chirp 3 HD | $0.14 |
| Sarvam v3 | ₹14 |
| ElevenLabs Flash v2.5 | $0.19 |
| ElevenLabs v4 | **$0.38** |

A 3.5-minute reel costs about 0.7× these figures.

### 2.2 Edge voice catalogue

Pulled live from `edge_tts.list_voices()` on 2026-10-09 [M/P]. The tags after each name are Microsoft's own content categories and personalities.

- **en-IN:** NeerjaNeural F, **NeerjaExpressiveNeural F**, PrabhatNeural M.
- **en-US:**
  - Warm/Confident: **Andrew** M and AndrewMultilingual M.
  - Expressive/Caring: **Ava** F and AvaMultilingual F.
  - Approachable/Casual: **Brian** M and BrianMultilingual M.
  - Cheerful/Clear: **Emma** F and EmmaMultilingual F.
  - Christopher M (Reliable/Authority), Eric M (Rational), Guy M (Passion), Jenny F (Friendly/Considerate), Michelle F (Friendly/Pleasant), Roger M (Lively), Steffan M (Rational), Aria F (Positive/Confident).
  - Ana F, tagged *Cartoon/Cute*: a child-like voice that suits a "curious student" role.
- **en-GB:** Libby F, Maisie F, Ryan M, Sonia F, Thomas M.
- **Other English:** en-AU Natasha F and WilliamMultilingual M; en-IE Connor M and Emily F; en-CA Clara F and Liam M; en-NZ Mitchell M and Molly F; en-SG Luna F and Wayne M; en-ZA Leah F and Luke M.
- **Indic:**
  - hi-IN Swara F and Madhur M; ta-IN Pallavi F and Valluvar M; te-IN Shruti F and Mohan M; kn-IN Sapna F and Gagan M; ml-IN Sobhana F and Midhun M.
  - **bn-IN Tanishaa F and Bashkar M; mr-IN Aarohi F and Manohar M; gu-IN Dhwani F and Niranjan M; ur-IN Gul F and Salman M.** These are not in the repo yet.
  - Accent variants: ta-LK Saranya and Kumar, ta-MY Kani and Surya, ta-SG Venba and Anbu, bn-BD Nabanita and Pradeep.
- **Other multilingual voices:** de-DE Florian and Seraphina; fr-FR Remy and Vivienne; it-IT Giuseppe; ko-KR Hyunsu; pt-BR Thalita. These can read English or other languages with a foreign accent; listen before exposing them [J].
- **Not on Edge:** pa-IN, or-IN, as-IN.

**What this means:** with Edge alone, an Indic-language teacher gets only **two voices**. Real variety in Indic languages needs Gemini 3.8, Chirp 3 HD, Sarvam or Azure (hi-IN). English has more than 30 good voices.

### 2.3 Steering delivery per provider

- **Gemini 3.8.** Put stage directions in the `speech_metadata.style` annotation, not in the transcript, because the text is read verbatim. Use `<short pause>`/`<long pause>` for timing.
  - Accent is not a style. Choose an Extended Library voice with the right accent filter, or create a voice by description [P].
  - Multi-speaker (2 speakers, prebuilt voices only) suits narrator-plus-student exchanges. One third-party report says timbres may not separate reliably [S], so prefer separate single-speaker calls.
- **OpenAI.** Use `instructions`, e.g. *"Warm, patient Indian-English teacher; brisk but clear; slight smile; emphasise key terms; 1-beat pause after each definition."* The 2,000-token input cap means chunking by beat or sentence [P].
- **ElevenLabs.** Lower `stability` gives more expressive delivery; `style` > 0 adds latency. Use `previous_text`/`next_text` to keep prosody continuous across beat chunks. Audio tags such as `[excited]` and `[whispers]` work on v4 Turbo and v3 Conversational [P].
- **Azure.** Use `<mstts:express-as style="cheerful|empathetic|newscast">` on the voices that support it. **Edge rejects this markup**, so styles need Azure.
- **Chirp 3 HD and Sarvam.** Only pace and pause controls are available.

### 2.4 Word timings: matrix and strategy

| Provider | Native timings | Fallback |
|---|---|---|
| Edge, Azure | Word boundaries | — |
| ElevenLabs | Characters → words (`alignment.py` exists) | — |
| Gemini, OpenAI, Chirp 3 HD, Sarvam | None documented | (a) synthesise **per sentence**, so sentence boundaries are exact and only within-sentence words are estimated (character-proportional, as `estimate_words` does today); or (b) forced alignment |

**Forced alignment options:**
- `faster-whisper` (MIT; `word_timestamps=True`; CPU int8 transcribes 13 min in about 1m42s single-batch on an i7-12700K [P]). It is transcription-based, so align its words to the known script by sequence matching [J].
- MMS-based aligners in torchaudio would cover Indic languages well, but I believe the MMS weights are **CC-BY-NC**. That was **not verified** this session, so don't ship them without checking.

Captions and karaoke highlighting tolerate about 80–120 ms of error [J]. Sentence-level chunking already gets most words inside that window.

### 2.5 Evidence on voice, pace and learning

- **Pace and enthusiasm.** Guo, Kim & Rubin (2014), 6.9M edX sessions [P, read in the PDF]:
  - Engagement drops sharply after about 6 minutes.
  - Speaking rate ranged 48–254 wpm (mean 156, sd 31). Within each length bucket, engagement rose up to 2× with speaking rate, and the **185–254 wpm** bucket was the most engaging.
  - The authors say rate is a surface feature correlated with enthusiasm. They advise bringing out enthusiasm and editing out pauses and filler, not forcing speed.
  - Implication: energetic voices and styles matter more than raw wpm. The data are from 2012 MOOC learners and record watch time, not learning.
- **Synthetic versus human voice.** Craig & Schroeder (2017) found a modern TTS voice matched a human voice on transfer and persona ratings, while a classic engine did worse [S, summarised in later papers]. Later results are mixed. A 2020 Computers & Education study found voice quality did not affect learning but did affect trust [S]. Implication: use the most natural voices available, which are neural, HD, v4 or Gemini-class.
- **Video length for reels.** No controlled evidence was found this session on 3–4 minute vertical educational reels. Guo's 0–6 minute bucket is the closest data [J].

### 2.6 Making every video sound different

See §7 for exact rules. The ideas:

1. **Voice persona registry.** Each persona records provider, voice, language, gender, energy (calm, neutral or energetic), natural wpm, loudness offset, best format (long-form or reel) and subject affinity.
2. **Rotation without repeats.** Per teacher (or per series), pick the least-recently-used eligible persona. Exclude the last *k* voices used for that teacher. Alternate gender when the pool allows. Make the choice deterministic from `(teacher_id, video_index)` so re-renders reproduce.
3. **Match energy to format and segment.** Reels get high-energy personas with a faster calibrated rate. Long-form gets warm, clear personas. Each segment type gets a style prompt on steerable providers: hook = curious and energetic; explanation = warm and measured; misconception = emphatic; recap = upbeat and confident; quiz = playful.
4. **Second voice used sparingly.** A "curious student" voice (opposite gender or child-like, e.g. en-US-Ana) for quiz questions and "wait, why?" interjections, at most 15–25% of runtime [J]. This breaks up single-narrator monotony.
5. **Calibrate rate per voice** (measured spread 147–201 wpm) so "target 160 wpm" means 160 wpm whichever voice is chosen.
6. **Normalise loudness per voice** (measured spread 5.1 LU) so music balance stays constant.
7. **Optional, with consent: the teacher's own voice.** Gemini 3.8 voice replication, OpenAI custom voices and ElevenLabs cloning all require consent recordings [P].

---

## 3. Background music (B)

### 3.1 AI music generation available to developers

| Service / model | Access and status | Length and format | Control | Price | Commercial licence and caveats |
|---|---|---|---|---|---|
| **Google Lyria 3.5** `lyria-3.5` (Gemini API, Interactions API) | "Stable" (models page, 2026-09-16) [P] | "A couple of minutes", steerable by prompt or timestamps; **44.1 kHz stereo; MP3, or WAV via `response_format`**; lyrics and structure returned as text [P] | Text plus up to 10 images; BPM, key and mood stated in the prompt; vocals **on by default**, so add "Instrumental only, no vocals"; single-turn only; non-deterministic [P] | **$0.08 per song**, no free tier [P pricing] | Google claims no ownership and may generate similar output for others. Safety filter blocks artist-voice and copyrighted-lyric prompts. **SynthID watermark**. 18+ / no under-18-directed apps clause [P] |
| **Lyria 3 Clip** `lyria-3-clip-preview` | Preview [P] | Always 30 s; 44.1 kHz stereo MP3 [P] | As above | **$0.04 per clip** [P] | As above; good for stingers, intros and loops |
| **Lyria RealTime** `models/lyria-realtime-exp` | **Experimental** [P] | Streams raw 16-bit PCM, **48 kHz stereo**; **instrumental only** [P] | Weighted prompts (blendable live), `bpm` 60–200, `density`/`brightness` 0–1, `scale`, `mute_bass`, `mute_drums`, `only_bass_and_drums`, `guidance` 0–6, `temperature`, `seed`; bpm/scale changes need a context reset [P] | Not stated | Watermarked. Real-time only, so a 5-minute bed takes 5 minutes of wall-clock time. Ideal for layer-based energy changes (drums on or off per section) |
| **Vertex AI Lyria 2 / Lyria 3** (`lyria-002`, `lyria-3`) | Listed on Vertex [P] | Lyria 3 Pro about 184 s [S] | — | Not verified | SynthID; enterprise route |
| **ElevenLabs Eleven Music** `music_v2_5` (best), `music_v2`, `music_v1` (default!) | GA [P] | `music_length_ms` 3,000–600,000 in the API docs, but the pricing page says a **5-minute limit per track** (conflict). MP3 48 kHz/192 kbps by default on v2; also PCM and Opus [P] | `prompt` with `force_instrumental=true` (guaranteed instrumental; works with `prompt` only), **or `composition_plan`** as chunks, each with text, **duration (always enforced on v2)**, positive and negative styles and context adherence. Inpainting and C2PA signing available [P] | **$0.15/min**; fine-tune $1.50 [P] | Commercial on Starter and above; free plan is non-commercial. The Eleven Music v1 terms (customers enrolled from 21 Nov 2025) restrict paid self-serve plans in some contexts: offline TV/radio/film, ads, high-traffic public events, and **music libraries of more than 25 outputs licensed to third parties** [S, search excerpt of the terms page]. Don't build a reusable shared library from these outputs without an Enterprise deal; generate per video [J] |
| **Stable Audio 2.5** (Stability API; also fal and Replicate) | GA, released 10 Sep 2025 [P] | Up to 3 min; 44.1 kHz stereo [S]; under 2 s on a GPU (vendor claim) [P] | Text-to-audio, audio-to-audio, inpaint | About 20 credits ≈ **$0.20 per generation** [S]; the official pricing page is JS-rendered and wasn't readable | Stability says it was "trained on a fully licensed dataset" and is "commercially safe" [P]. Exact API output terms not verified |
| **Stable Audio Open 1.0** (self-host) | Weights on HF [P] | ≤ 47 s, 44.1 kHz | — | GPU cost | **Stability AI Community License** (commercial use subject to its terms; revenue threshold not verified). Trained on CC0, CC-BY and CC-Sampling+ Freesound/FMA. Better at sound effects than music [P] |
| **ACE-Step** (self-host) | v1.5 released 28 Jan 2026 [P] | Up to 4 min; RTF about 27× on A100 and 34× on RTX 4090 (27 steps); 8 GB VRAM mode [P] | Text, tags, lyrics; instrumental supported | GPU cost | Code Apache-2.0; v1-3.5B weights tagged Apache-2.0 [P]. **Training data not disclosed**, so provenance is a risk [J] |
| **Meta MusicGen / AudioCraft** | — | — | — | — | Code MIT, **weights CC-BY-NC 4.0: not usable commercially** [P] |
| **Beatoven.ai** | API existence unclear [S] | — | — | Plans about $10–20/month; $3 per downloaded minute [S] | "Fairly Trained" certified; licence wording conflicts between sources [S]. Not verified |
| **Mubert API** | Custom/enterprise pricing [S] | — | — | Quote | Render licences exclude Content ID and streaming releases [S]. Not verified |
| **Soundraw API** | Not researched (search budget exhausted) | — | — | — | — |
| **Suno / Udio** | No official public developer API found in this session | — | — | — | Avoid [J] |

**Music cost per 5-minute video:**

| Option | Cost |
|---|---|
| Lyria 3.5, one cue per ~2 chapters (2–3 songs) | **$0.16–0.24** |
| ElevenLabs Music, 5 min | **$0.75** |
| Stable Audio 2.5, 2 generations | about $0.40 [S] |
| Procedural | **$0** |

### 3.2 Royalty-free libraries: can they be automated in a SaaS?

| Library | API? | Licence of content | Automated SaaS use |
|---|---|---|---|
| **Pixabay** | Images and videos only; **no music/audio endpoint** [P] | Pixabay Content License: commercial use, no attribution needed. No **standalone** distribution; no bulk or systematic copying; may not be used to build a competing service (terms updated 18 Nov 2024) [P] | Manual curation only. A small hand-picked set used *inside* rendered videos is a creative work and allowed [J]. Don't expose raw tracks for download |
| **Freesound** | Yes | Per sound: CC0, CC-BY or CC-BY-NC | **API free only for non-commercial use**; commercial needs a separate agreement [P] |
| **Jamendo** | Yes (v3) | Per-track CC; many tracks are NC or ND | Free API tier is non-commercial; commercial video use goes through **Jamendo Licensing** [S] |
| **Free Music Archive** | No current public API (not re-verified) | Per-track CC | Manual only |
| **YouTube Audio Library** | No | YouTube Help is built around use in YouTube videos; CC tracks need credit [S] | Not suitable |
| **Openverse** | Yes (`/v1/audio/`, `license_type=commercial,modification`) [S] | Aggregates Jamendo, Freesound and Wikimedia with per-item licence and attribution | Usable for discovery, but each source's terms still apply [J] |
| **Epidemic Sound Partner API** | Yes | Commercial licence with **sub-licensing to end users** on paid tiers; free tier is prototyping only (50 downloads and 100 streams, lifetime) [S, from their developer site] | **The clean licensed route**; quote-based |
| **incompetech (Kevin MacLeod)** | No | Creative Commons with generated credit lines, or a paid no-attribution licence [P] | Bundle a curated pack with credits in the video description and end card |

### 3.3 Procedural or algorithmic music in Python (no GPL dependencies)

**Licence-safe stack [P/M]:**
- numpy (BSD-3), scipy (BSD), soundfile (BSD-3), all already in the venv; pyloudnorm (MIT, BS.1770-4) for measuring.
- **Avoid in-process `pedalboard`.** It is GPL-3.0 through JUCE and the VST3 SDK [P].
- FluidSynth is LGPL-2.1 [J, not re-verified]. SoundFont licences were not verified, so synthesise instead of sampling.

**Recipe** (prototype in `scratchpad/research/tools/procbed.py`, measured [M]):
- **Harmony.**
  - Lo-fi or neo-soul: Imaj7–vi7–ii7–V7(9).
  - Corporate or bright: I–V–vi–IV.
  - Ambient: I–IV slow alternation or a modal vamp (Dorian or Lydian).
  - Use close-position voicings around C4, root-position bass, a randomised key per video, and one chord per bar.
- **Timbres.**
  - Electric piano: 2-operator FM (modulation index 1.8, decaying, with a 12 ms strum).
  - Pad: 3 detuned saws (±0.4–0.5%), 0.8 s attack, low-passed at 1.2–1.8 kHz (lo-fi/ambient) or 4 kHz (bright).
  - Sine sub-bass.
  - Drums: kick is a sine sweep 140→50 Hz; snare is band-passed noise (0.9–5 kHz); hat is high-passed noise above 7 kHz; **58% swing** for lo-fi.
  - Lo-fi texture: sparse vinyl crackle plus a −48 dB hiss.
- **Space.** A Freeverb/Schroeder reverb: 4 combs at 1,422–1,617 samples, g = 0.8; 2 allpasses at 225 and 556 samples, g = 0.5; wet 0.22.
  - Implement the combs **block-wise in numpy**. `scipy.lfilter` with a 1,557-order denominator was 5–7× slower: 70 s versus 9–13 s for 5 minutes.
- **Master.** High-pass at 35 Hz, `tanh` soft clip, peak at −3 dBFS, 2 s fades.
- **Tempo.** Lo-fi 78 bpm, ambient 64, bright 104.
- **Measured results.**
  - 300 s stereo, 44.1 kHz renders in **13.3 s (lo-fi), 9.0 s (ambient) and 9.6 s (bright)** on this laptop.
  - Loudness: −16.3, −13.6 and −15.5 LUFS. LRA 1.3–3.3 LU, so the beds are steady and duck predictably.
- **Realistic quality [J].** Pleasant, inoffensive and clearly synthetic, roughly "royalty-free corporate bed" from 2005. Fine under speech at −18 to −20 LU. Weak when exposed, as in intros. Use AI music for exposed moments and procedural music as the offline/demo/no-key fallback.
- **Variety knobs (deterministic from a seed).** Key, progression family, bpm (±8), instrument set, swing (50–60%), filter cutoff, drum pattern, and per-section layer muting.

---

## 4. Mixing for learning

### 4.1 Evidence

| Finding | Source | Strength |
|---|---|---|
| Background music and/or sounds under a narrated animation **lowered retention and transfer**; explained by music competing with narration in auditory working memory ("coherence principle") | Moreno & Mayer 2000, *J. Educ. Psych.* 92:117–125 | [P] abstract; controlled experiments, college students |
| **Fast and loud** instrumental music significantly lowered reading comprehension; slow or soft did not. The effect is an interaction: tempo matters only when the music is loud | Thompson, Schellenberg & Letnic 2011/12, *Psychology of Music* 40(6):700–708 | [P] abstract via search, n = 25; levels reported as 110 vs 150 bpm and 60 vs 72 dB [S] |
| Preferred speech-over-music loudness difference **≥ 10 LU** (≥ 15 LU over ambience); **non-experts prefer about 4 LU more** than experts; preferences vary widely between people (IQR about 5.7 LU) | Torcoli et al. 2019, *JAES* 67(12); follow-up 2023 (arXiv 2305.19100) | [P] abstract; n = 22 |
| Lyrics or intelligible speech in music disrupt verbal tasks (irrelevant-speech effect) | Widely replicated; not re-fetched this session | [J/S] |
| A 2026 meta-analysis (13 reports, 26 effect sizes): music **raised attentional engagement**; the memory cost appeared only when tests competed with concurrent narration | Amato 2026, *Cultura & Saúde* | [S] single author, newer and unreplicated |
| Seductive details (including music) hurt retention (small–medium) and transfer (medium) | Rey 2012 (cited in summaries) | [S] |

**Synthesis [J].** Music can buy engagement and emotional tone. Placed under dense narration it costs learning. Put the energy where nobody is speaking (intro, chapter transitions, think-pauses, recap stingers). Keep the bed **steady, quiet, slow-to-moderate and vocal-free** under speech. Drop it entirely for very dense segments such as derivations, code walkthroughs and definitions.

### 4.2 Loudness targets

| Target | Value | Source |
|---|---|---|
| YouTube playback reference | about **−14 LUFS**; never boosts quiet uploads; leave about 1 dB true-peak headroom for re-encoding | [S], Production Advice / Meterplugs |
| AES TD1008 (2021) speech and "assorted" content | **−18 LUFS** | [S] |
| AES TD1008 music | ≤ **−16 LUFS** | [S] |
| AES TD1008 true peak | −1 dBTP at the codec input | [S] |
| AES TD1008 note | Speech sounds 2–3 dB louder than music at equal BS.1770 loudness | [S] |
| EBU R128 broadcast | −23 LUFS ±0.5, −1 dBTP | [J], well known; not re-fetched |
| Instagram Reels / TikTok | **No official spec found**; third parties say −14 (or −10 to −12) | [S], unverified |

### 4.3 ffmpeg facts (local ffmpeg 8.1.1, `-h filter=…`) [M/P]

| Filter | Options (range, default) |
|---|---|
| `sidechaincompress` | `threshold` 0.000977–1 (0.125); `ratio` **1–20** (2); `attack` 0.01–2000 ms (20); `release` 0.01–9000 ms (250); `makeup` 1–64; `knee` 1–8 (2.83); `link` average\|maximum; `detection` peak\|**rms**; `level_sc`; `mix`. Gain reduction tracks how far the key exceeds the threshold, so **ducking depth varies with voice loudness**. This is why the measured swing was 24–26 LU |
| `sidechaingate` | Has `range` (maximum gain reduction, default 0.06125 ≈ −24 dB): a fixed-depth duck |
| `loudnorm` | `I` −70 to −5 (−24), `LRA` 1–50 (7), `TP` −9 to 0 (−2), `measured_*`, `offset`, `linear` (true), `print_format` json\|summary. Two-pass with `linear=true` gives clean static gain |
| `alimiter` | `limit` 0.0625–1, `attack` 0.1–80 ms (5), `release` 1–8000 ms (50), `level` auto-level (**default true; set false**) |
| `amix` | `normalize` (default **true**; the repo correctly uses 0), `weights`, `duration`, `dropout_transition` |
| `acrossfade` | `d`, `o`, `c1`/`c2` curves (tri, qsin, hsin, esin, log, ipar, qua, cub, squ, cbr, par, exp, …) |
| `volume` | `eval=frame` allows a time-varying expression |
| `amultiply` | Multiplies two audio streams, so a precomputed gain envelope can be applied |
| Also present | `ebur128` (with `metadata=1` for per-frame momentary loudness), `silencedetect`, `speechnorm`, `dynaudnorm`, `firequalizer`, `apad`, `atempo` |

**Better ducking for this app [J].** We know every word's start and end time, so we don't need a reactive compressor. Compute a deterministic gain envelope from the beat and word timings, with lookahead, a hold across short gaps and a fixed depth. Apply it with numpy, or with `amultiply` against a generated envelope WAV. The result is identical across voices, testable, and doesn't pump.

---

## 5. Section structure for music (long-form vs reel)

| Moment | Long-form 16:9 (~5 min) | Reel 9:16 (3–4 min) |
|---|---|---|
| Aadhi intro (5–10 s) | Sonic logo or sting 2–4 s, the **same motif every video** for brand recognition [J]; music exposed at −6 to −8 LU below the narration target | Hook music from t = 0 (no fade-in > 0.3 s) |
| Body | New cue or variation **per chapter** (every 50–90 s), crossfaded 2.0 s on a qsin curve at the scene transition | Energy-layer change every **15–25 s** (drums or bass in and out, filter opens); new cue every 45–60 s |
| Think-pause / quiz countdown | Music lifts to −10 LU, optional tick pulse; stinger on the answer reveal | Same, shorter |
| Dense segment (derivation, code, definition) | Music fades out over 1.5 s, or drops to −26 LU | Drop to −24 LU |
| Recap (Aadhi optional, 5–10 s) | Brighter variant of the opening motif | Same |
| Outro | Music up to −8 LU; 3 s fade | 1.5 s fade |

---

## 6. Not verified, and open risks

- **Not verified (search budget ran out):**
  - Azure official USD prices (the page hides them).
  - Stable Audio API price and output terms.
  - Beatoven, Mubert and Soundraw API terms.
  - Exact Instagram and TikTok loudness normalisation.
  - MMS aligner weight licence.
  - SoundFont licences.
  - The FluidSynth licence (believed LGPL-2.1).
  - Exact incompetech CC version.
  - Eleven Music v1 Commercial Rights table (only a search excerpt was seen).
  - Whether ElevenLabs v4/v3 support `/with-timestamps`.
  - Whether Gemini's Extended Voice Library has Indian-English accents.
- **Edge TTS is unofficial.** Microsoft can block it at any time and grants no licence for third-party commercial use [J]. Keep Azure, with the same voice names, as the licensed equivalent.
- **Gemini API terms** require users to be 18+ and exclude apps "directed at or likely accessed by" under-18s [P]. The Studio is teacher-facing but the videos are watched by students, so get legal review before making Gemini or Lyria the default for school deployments.
- **OpenAI** requires telling end users that the voice is AI [P]. Lyria embeds SynthID; ElevenLabs Music can sign with C2PA. An "AI-generated narration and music" line in the end card and description covers all providers [J].
- **Non-deterministic generators.** Lyria and ElevenLabs Music vary between calls even with the same prompt [P]. Cache the produced file in the asset manifest by content hash and never regenerate on re-render [J].
- **Lyria 3.5 includes vocals by default** [P]. Reject any generation that returns non-empty lyrics text [J].

---

## 7. DESIGN IMPLICATIONS FOR AADHI

### A. Voice providers and catalogue

1. **Migrate Gemini TTS before 17 Nov 2026.** Change the default `gemini_tts_model` to `gemini-3.8-flash-tts`; use `gemini-3.8-flash-lite-tts` for drafts and previews.
   - Use the Interactions API shape with `speech_metadata.style` and `<short pause>`/`<long pause>` tags.
   - Add all 30 prebuilt voices with their descriptors (§2.1).
   - Requires `google-genai` ≥ 2.25.0.
2. **OpenAI:** default to `gpt-4o-mini-tts` with `marin` or `cedar`. Add `ballad` and `verse`. Send `instructions` for each segment type. Chunk input to ≤ 1,500 tokens per call (cap is 2,000).
3. **ElevenLabs:**
   - Make `eleven_v4` the quality default and `eleven_flash_v2_5` the cheap option.
   - Probe `/with-timestamps` with v4 at startup or in a test, and fall back to sentence chunking if alignment is null.
   - Load voices dynamically (`GET /v1/voices`) instead of hard-coding IDs.
   - Block commercial renders on free-plan keys, or require the "elevenlabs.io" credit.
4. **Extend the Edge catalogue:**
   - Add bn-IN, mr-IN, gu-IN, ur-IN.
   - Add the English voices: en-US Ava, Brian, Emma, Andrew (non-multilingual), Christopher, Eric, Michelle, Roger, Steffan, Ana; en-GB Maisie; en-AU, en-IE, en-CA, en-NZ, en-SG and en-ZA pairs.
   - Add ta-LK, ta-MY and ta-SG as "accent variants", **off by default** for Tamil Nadu audiences.
   - Store Microsoft's personality tags as persona metadata.
5. **Add three provider adapters:**
   - **Azure** (licensed Edge equivalent; adds styles and word boundaries; free 0.5M characters/month ≈ 100 five-minute English videos).
   - **Google Chirp 3 HD** (30 voices × 9 Indian locales).
   - **Sarvam Bulbul** (v3 / v4-flash, Indic specialist).
   - All implement the same `TTSProvider` and declare `supports_word_timings`.
6. **Word timings:**
   - For providers without them, synthesise per sentence; sentence ends are exact and within-sentence words are character-proportional.
   - Optionally add `faster-whisper` (MIT, int8 CPU) alignment matched against the known script.
   - Accept ≤ 120 ms word error for captions.
   - Do not ship MMS-weight aligners until their licence is confirmed.

### B. Voice variety rules

7. **Persona registry.** Each persona is `{id, provider, voice, lang, gender, energy∈{calm,neutral,energetic}, natural_wpm, loudness_offset_LU, formats⊆{long,reel}, subjects[], style_prompt}`.
   - Measure `natural_wpm` and `loudness_offset` once per voice with a fixed 45-word calibration text. Cache by provider, voice and model.
8. **Rotation:**
   - Choose by `LRU(teacher_id)` among personas matching the language and format.
   - **Exclude the last min(3, pool−1) voices.** Alternate gender when the pool has both.
   - Make the choice deterministic from `seed = hash(teacher_id, project_id)` and store it in the screenplay. Re-renders never change the voice.
   - Offer a "keep this voice for the series" pin.
9. **Pace targets (calibrated per voice).** `rate% = round((target_wpm / natural_wpm − 1) × 100)`, clamped to ±25%.

   | Format | Target wpm |
   |---|---|
   | Long-form English | **155–165** |
   | Long-form Indic | **140–155** |
   | Reels | **170–185** |
   | Hooks (first 3 s) | +5% |
   | Definitions | −5% |

10. **Pauses:**

    | Where | Long-form | Reel |
    |---|---|---|
    | Between sentences | 250–350 ms | 150–220 ms |
    | After a definition or question | 600–900 ms | 400–600 ms |
    | Before a quiz answer reveal | 1.5–3 s countdown | 1.5–3 s countdown |

    Trim TTS lead and tail silence to ≤ 60 ms; Edge adds 130–260 ms.
11. **Style prompts per segment type** (Gemini `style`, OpenAI `instructions`, Azure `express-as` where supported):

    | Segment | Style |
    |---|---|
    | hook | curious, energetic |
    | explain | warm, clear, measured |
    | example | conversational |
    | misconception | emphatic, slower |
    | recap | upbeat, confident |
    | quiz | playful |

    Reels shift every segment one step more energetic.
12. **Second voice.** Optional "student" persona (opposite gender, or en-US-Ana) for quiz questions and interjections. **≤ 20% of narration time, ≤ 1 interjection per 45 s.** Same provider as the narrator so the timbre class matches. Normalised to the same loudness.
13. **Narration loudness:**
    - Normalise the **whole narration track per voice**, with one static gain per voice computed over all of that voice's clips: **−16 LUFS integrated** for long-form and **−15 LUFS** for reels, before mixing.
    - Never normalise each beat clip separately; short-clip integrated loudness is unstable.

### C. Music sourcing

14. **Source order:**
    1. **Lyria 3.5** with the prompt suffix "Instrumental only, no vocals, no choir, no spoken words". Reject any result whose returned lyrics text is non-empty.
    2. **ElevenLabs Music `music_v2_5`** with a `composition_plan` whose chunk durations equal the chapter durations, plus negative styles `["vocals","singing","choir","spoken word"]`. Use the simple-`prompt` path when `force_instrumental=true` is needed.
    3. **Procedural numpy engine** (offline, demo, no keys).
    4. **Curated licensed pack**: Epidemic Partner API if contracted, else a hand-picked CC-BY or Pixabay set used only inside renders, with credits.

    Never use MusicGen weights, YouTube Audio Library, or the Freesound or Jamendo APIs for commercial renders.
15. **Generate music after narration timing is known.** Request `chapter_duration + 4 s`, then trim and fade. Lyria has no exact length, so generate about 2-minute cues and crossfade them. ElevenLabs v2 enforces chunk durations.
    - Run music jobs in parallel with image and visual asset jobs.
    - Budget: ≤ $0.25 per long-form video with Lyria, ≤ $0.75 with ElevenLabs.
16. **Cache every generated cue** in the AssetManifest by `sha256(provider, model, prompt, seed, duration)`. Re-renders reuse it; regenerate only when the user asks.
17. **No repeats.**
    - Never reuse a cue in the same teacher's last **10** videos.
    - Never reuse the same (genre, key, bpm±4) triple in the last **3**.
    - Within one video, never repeat a cue in two chapters, except the deliberate Aadhi brand motif in the intro and recap.
18. **Music palette by format:**

    | Format | Tempo | Moods |
    |---|---|---|
    | Long-form | **70–95 bpm** | lo-fi, soft acoustic, light electronic, ambient piano, minimal corporate |
    | Reels | **95–120 bpm** | upbeat electronic, light percussion, pluck melodies |
    | Never | **> 130 bpm** with dense drums | — |

    **Never lyrics.** Map subject to mood (e.g. biology → organic acoustic, CS → light electronic, history → cinematic strings), with a per-video random draw among 3 or more moods per subject.
19. **Procedural engine parameters** (fallback):

    | Parameter | Values |
    |---|---|
    | Sample rate | 44.1 kHz stereo |
    | Progression families | 4 (§3.3) |
    | Key | random per video |
    | bpm | 64 (ambient) / 78 (lo-fi) / 104 (bright), ±8 |
    | Swing | 50–60% |
    | Pad low-pass | 1.2–4 kHz |
    | Reverb | wet 0.18–0.25 |
    | Master | peak −3 dBFS, then loudness-normalise the bed to −16 LUFS |

    Expected render time is about 10 s per 5 minutes. Implement comb filters block-wise, not with high-order `lfilter`.

### D. Mixing and mastering

20. **Normalise every music asset** to **−16 LUFS integrated** (measure with `ebur128`/pyloudnorm) before applying any mix gains. Today's `bgm.mp3` is −9.7 LUFS. Express all music gains **in LU relative to the narration target**, never as a fixed linear `volume`.
21. **Music levels relative to narration (narration ≈ −16 LUFS):**

    | Situation | Long-form | Reel |
    |---|---|---|
    | Under speech | **−20 LU** (≈ −36 LUFS) | **−17 LU** (≈ −32 LUFS) |
    | In speech gaps ≥ 0.8 s | −14 LU | −12 LU |
    | Think-pauses and quiz countdowns | −10 LU | −10 LU |
    | Intro/outro with no speech | −6 to −8 LU | −6 to −8 LU |
    | Dense segments | music off (or −26 LU) | −24 LU |

    The floor is **never closer than 15 LU under speech**: Torcoli's ≥ 10 LU plus 4 LU for non-experts, plus margin for learning and L2 listeners.
22. **Replace the reactive `sidechaincompress` with a deterministic envelope** built from word and beat timings:
    - Speech regions are words merged across gaps < 0.8 s.
    - Duck starts **200 ms before** speech onset (lookahead), with a **150 ms** attack ramp.
    - Hold through the region, then release over **700 ms** (long-form) or **400 ms** (reel), starting 150 ms after the last word.
    - Use equal-power (sine) ramps. Duck depth is the gap level minus the under-speech level (6 dB long-form, 5 dB reel).
    - Apply in numpy, or with ffmpeg `amultiply` against an envelope stream.
    - **QA assertion:** under-speech music momentary loudness varies ≤ 3 LU across the video (today 24–26 LU).
    - If a compressor fallback is kept, use `sidechaincompress=threshold=0.05:ratio=4:attack=80:release=900:knee=4:detection=rms`, set `level_sc` from the measured narration loudness, and use music gain −14 LU. This still varies with the voice.
23. **Spectral room for speech [J].** On the music bus only, apply a high-pass at 100 Hz (`highpass=f=100`) and a gentle presence dip `equalizer=f=2500:t=q:w=1.2:g=-4` while speech is active. For mono-speaker phone playback, keep the music's stereo width (narration stays centred).
24. **Transitions:**
    - Chapter-cue change: `acrossfade=d=2.0:c1=qsin:c2=qsin`, with the crossfade midpoint on the scene cut.
    - Reels: 1.0 s crossfade or a hard cut on a downbeat with a 0.3–0.8 s riser or whoosh.
    - Fade-in: 1.0 s long-form, ≤ 0.3 s reels. Fade-out: 3 s long-form, 1.5 s reels.
25. **Master chain:**
    1. Sum with `amix=normalize=0`.
    2. Two-pass `loudnorm` with `linear=true`, targets **I = −16 LUFS, TP = −1.5 dBTP, LRA = 11** (long-form; LMS/YouTube-friendly) or **I = −14 LUFS, TP = −1.5 dBTP** (reels).
    3. If linear mode can't reach the target, use a static gain plus `alimiter=limit=0.84:attack=5:release=50:level=0` (≈ −1.5 dBFS).

    Encode AAC 48 kHz stereo at ≥ 160 kbps (192 kbps for reels).
26. **Post-render audio QA** (extend the existing post-render QA with `ebur128=peak=true`):
    - |I − target| ≤ 1.0 LU.
    - TP ≤ −1.0 dBTP.
    - Narration-to-music difference under speech ≥ 15 LU (measured on the stems).
    - No music silence > 2 s outside dense segments, unless intended.
    - No clipping.
    - Fail the render, or warn in Visual Review, on violation.

### E. Product and UI

27. **Studio audio panel:**
    - Voice: auto-rotate, or pin a persona; preview a 5 s sample using the cached calibration text.
    - Second voice on or off.
    - Music: auto, a mood or genre picker, or "no music".
    - Music intensity: subtle (default, −20 LU) or lively (−17 LU). Never louder than −15 LU under speech.
    - Per-chapter "music off" toggles.
    - Show the estimated per-video cost per provider (rates in §2.1 and §3.1).
28. **Editor.** Treat narration, music and SFX as **separate stems** in the timeline:
    - Cutting a section recomputes the duck envelope and re-crossfades the music at the cut (0.5 s qsin). Music is not simply chopped.
    - Inserted external video clips get their own audio stem. Duck their audio to −20 LU under narration or mute it; when a clip plays with its own audio and no narration, duck music under the clip to −18 LU.
29. **Compliance:**
    - End-card and description line: "Narration and music generated with AI (provider list)".
    - Store per-asset licence metadata: provider, plan, licence or terms URL, attribution text.
    - Block free-tier ElevenLabs outputs from commercial exports.
    - Gate Gemini, Lyria and OpenAI behind an "adult teacher account" acknowledgement until legal review of the under-18 clauses is done.
30. **Default stack recommendation:**

    | Use | Voice | Music |
    |---|---|---|
    | **Free / demo** | Edge, calibrated and normalised | procedural engine |
    | **Standard** | Gemini 3.8 Flash TTS (English and Indic) or Azure (en-IN and hi-IN styles) | Lyria 3.5 |
    | **Premium** | ElevenLabs v4 | ElevenLabs Music v2.5 with chapter-aligned composition plans |
    | **Indic-first schools** | Sarvam Bulbul v3 / v4-flash or Chirp 3 HD | — |

---

## Sources

**TTS**
- Gemini speech generation: https://ai.google.dev/gemini-api/docs/speech-generation (last updated 2026-10-01)
- Gemini pricing: https://ai.google.dev/gemini-api/docs/pricing
- Gemini deprecations: https://ai.google.dev/gemini-api/docs/deprecations
- Gemini API additional terms: https://ai.google.dev/gemini-api/terms (effective 2026-03-23)
- OpenAI TTS guide: https://developers.openai.com/api/docs/guides/text-to-speech
- OpenAI pricing: https://developers.openai.com/api/docs/pricing
- OpenAI gpt-4o-mini-tts model page: https://developers.openai.com/api/docs/models/gpt-4o-mini-tts
- ElevenLabs models: https://elevenlabs.io/docs/models
- ElevenLabs API pricing: https://elevenlabs.io/pricing/api
- ElevenLabs with-timestamps endpoint: https://elevenlabs.io/docs/api-reference/text-to-speech/convert-with-timestamps
- ElevenLabs TTS capabilities: https://elevenlabs.io/docs/capabilities/text-to-speech
- ElevenLabs publishing rules: https://elevenlabs.io/docs/help-center/legal/can-i-publish-the-content-i-generate-on-the-platform
- edge-tts README: https://github.com/rany2/edge-tts (plus the live `edge_tts.list_voices()` call on 2026-10-09 and installed package metadata showing LGPLv3)
- Azure language support: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support?tabs=tts
- Azure pricing: https://azure.microsoft.com/en-us/pricing/details/cognitive-services/speech-services/ (F0 0.5M characters; USD figures hidden). Third-party ~$15–16 per 1M: https://speechify.ai/compare/build/text-to-speech/azure
- Chirp 3 HD: https://docs.cloud.google.com/text-to-speech/docs/chirp3-hd (2026-10-07)
- Google Cloud TTS pricing: https://cloud.google.com/text-to-speech/pricing
- Sarvam Bulbul docs: https://docs.sarvam.ai/api-reference-docs/getting-started/models/bulbul
- Sarvam pricing: https://sarvam.ai/api-pricing
- Kokoro-82M: https://huggingface.co/hexgrad/Kokoro-82M
- faster-whisper: https://github.com/SYSTRAN/faster-whisper
- pyloudnorm: https://github.com/csteinmetz1/pyloudnorm

**Music**
- Lyria music generation: https://ai.google.dev/gemini-api/docs/music-generation (2026-10-01)
- Lyria 3.5 model page: https://ai.google.dev/gemini-api/docs/models/lyria-3.5-pro-preview
- Lyria RealTime: https://ai.google.dev/gemini-api/docs/realtime-music-generation (2026-09-17)
- Vertex AI Lyria: https://docs.cloud.google.com/vertex-ai/generative-ai/docs/music/overview
- ElevenLabs Music compose endpoint: https://elevenlabs.io/docs/api-reference/music/compose
- Eleven Music v1 terms: https://elevenlabs.io/eleven-music-v1-terms
- Stable Audio 2.5 announcement: https://stability.ai/news-updates/stability-ai-introduces-stable-audio-25-the-first-audio-model-built-for-enterprise-sound-production-at-scale. Third-party pricing: https://sacra.com/c/stability-ai/
- Stable Audio Open 1.0: https://huggingface.co/stabilityai/stable-audio-open-1.0
- ACE-Step: https://github.com/ace-step/ACE-Step and https://huggingface.co/ACE-Step/ACE-Step-v1-3.5B
- AudioCraft / MusicGen: https://github.com/facebookresearch/audiocraft
- Mubert (third-party, unverified): https://vidmuse.ai/blog/mubert
- Beatoven (third-party, unverified): https://costbench.com/software/ai-music-apis/beatoven-ai/
- Pixabay API docs: https://pixabay.com/api/docs/
- Pixabay license summary: https://pixabay.com/service/license-summary/
- Pixabay terms: https://pixabay.com/service/terms/ (updated 2024-11-18)
- Freesound API terms: https://freesound.org/docs/api/terms_of_use.html
- Jamendo API terms: https://devportal.jamendo.com/api_terms_of_use
- Jamendo Licensing: https://support-licensing.jamendo.com/getting-started
- YouTube Audio Library help: https://support.google.com/youtube/answer/3376882
- Epidemic Sound developers: https://developers.epidemicsound.com/ and https://help.epidemicsound.com/hc/en-us/articles/35854988839570-Getting-Started-with-the-API
- incompetech licences: https://incompetech.com/music/royalty-free/licenses/
- Openverse (third-party summary): https://jentic.com/apis/openverse
- pedalboard (GPL-3.0): https://github.com/spotify/pedalboard

**Learning and mixing**
- Moreno & Mayer 2000: https://tecfa.unige.ch/tecfa/teaching/methodo/Moreno_Mayer00.pdf
- Thompson, Schellenberg & Letnic: https://research.bond.edu.au/en/publications/fast-and-loud-background-music-disrupts-reading-comprehension/
- Torcoli et al. 2019: https://cris.fau.de/publications/234640600 and https://aes2.org/publications/elibrary-page/?id=20711
- Torcoli et al. 2023 follow-up: https://arxiv.org/abs/2305.19100
- Guo, Kim & Rubin 2014 (read in the PDF): https://up.csail.mit.edu/other-pubs/las2014-pguo-engagement.pdf
- Voice-effect literature summary: https://www.doi.org/10.1007/s10639-022-11255-6
- de la Mora Velasco & Hirumi 2020 (abstract not retrieved): https://discovery.ifa.de/vufind/Record/OLC2121632514
- 2023 background-music meta-analysis (abstract not retrieved): https://journals.sagepub.com/doi/full/10.1177/03057356231153070
- Amato 2026: https://culturaesaude.med.br/en/background-music-increases-viewer-engagement-a-systematic-review-and-meta-analysis/
- AES TD1008 summaries: https://productionadvice.co.uk/td1008/ and https://www.production-expert.com/production-expert-1/aes-update-loudness-recommendations-for-audio-only-streaming
- YouTube −14 LUFS: https://productionadvice.co.uk/stats-for-nerds/ and https://www.meterplugs.com/blog/2019/09/18/youtube-changes-loudness-reference-to-14-lufs.html
- ffmpeg filter options: local `ffmpeg -h filter=<name>` (8.1.1) and https://ffmpeg.org/ffmpeg-filters.html
