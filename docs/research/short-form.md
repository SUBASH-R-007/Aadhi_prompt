# Research: vertical short-form (9:16) and ~5-min 16:9 educational video specs, as of October 2026

Topic key: `short-form`. Researched on 2026-10-09. I only read the repo; nothing in it was changed.

**Evidence grades used throughout**
- **[A]** Official platform documentation fetched in this session, a standards document, or a peer-reviewed paper I read in full.
- **[B]** An official statement reported by the press, an official source that is older or only partly read, or a large industry dataset.
- **[C]** Third-party guides or practitioner consensus.
- **[D]** My own calculation or inference from A–C.

**Limitation.** The shared web-search budget for this run ran out near the end. A few items (TikTok's official limits, Instagram's 20-minute rollout, TikTok's exact safe-zone pixels) rest on third-party sources and are graded C. They are flagged as items to check in the apps.

---

## 1. Platform length limits, and what happens above 3:00

The owner wants vertical videos of 3–4 minutes. The table shows how each platform treats that length.

| Platform | Max length for a vertical upload to count as a Short/Reel (2026) | What happens above 3:00 | Grade / source |
|---|---|---|---|
| **YouTube Shorts** | **3:00**. Square or vertical uploads made on or after 15 Oct 2024 that are up to 3 minutes long are classified as Shorts automatically; the creator cannot choose. For Official Artist Channels the cut-off date is 8 Dec 2025. | A vertical video longer than 3:00 is a **regular long-form video**, so it does not appear in the Shorts feed. To keep a video from becoming a Short, YouTube suggests a wider ratio such as 16:9. | [A] YT Help 15424877, 12779649; YT Blog, Oct 2024 |
| YouTube Shorts: copyright rule | Any Short **longer than 1 minute** with an active Content ID claim of any kind, including manual claims, is **blocked globally**: it can't be played, recommended or monetised. Shorts under 1 minute are unaffected. When a song from the Audio Library is used inside a 3-minute Short, most tracks are limited to 90 s and some to 60 s or 30 s. | This matters a lot for our background music. A claimed music bed on a 2–3 minute Short kills that Short. | [A] YT Help 15424877 |
| **Instagram Reels** | **3:00** for most accounts since 18 Jan 2025 (it was 90 s before). A 20-minute option has been rolling out to some accounts since late 2025, and Socialinsider (July 2026) quotes the Help Center saying Reels can be up to 20 minutes. | In May 2024 Instagram's content team told creators that Reels **over 90 s can hurt reach**. Several 2026 guides say Reels over 3:00 are not pushed to non-followers or shown in the Reels tab, but I found no Meta document that says so. Socialinsider's data (about 6M Reels, Jan–Jun 2026): 45–60 s had the best median views (10,374) and engagement rate (0.35%); over 180 s had 4,428 views and 0.15%. | 3:00: [B] Social Media Today, 18 Jan 2025. 20-min: [C]. Reach claim: [B] for 90 s (2024), [C] for 3:00. Data: [B] |
| Instagram Reels **ads** | 0 s to **15 min**. 9:16. **1440×2560** recommended. H.264, AAC stereo at 128 kbps or more, 4 GB maximum. | n/a | [A] Meta Ads Guide (fetched) |
| **TikTok** | **10 min** when recording in the app. **Up to 60 min** for uploaded files: tested in May 2024 and described as rolled out in 2025–26 guides, but possibly not on every account. | A 3–4 minute video is a normal TikTok. The Creator Rewards Program pays only for videos **over 1 minute** (third-party guides). | [B] TechCrunch, May 2024 (test). [C] 2026 guides |
| TikTok ads | In-feed: 5–60 s, **21–34 s recommended**, 9:16, at least 540×960. Creative guide (updated June 2025): use sound, shoot 9:16 at 720p or above, keep content in the UI safe zone, **hook within the first 6 s**. | n/a | [B] TikTok Ads Help, via search snippets; the pages refused my direct fetch |
| **Facebook Reels** | **No length or format limit.** Since a 17 Jun 2025 announcement (rolled out over the following months), every Facebook video is shared as a Reel; the limit was 90 s before. Facebook says recommendations are unchanged. | A 3–4 minute video is fine. | [B] TechCrunch, 17 Jun 2025 |
| **LinkedIn** (organic) | **15 min** maximum. Minimum 3 s on desktop, 2 s on mobile. 75 KB to 5 GB. Width:height between 1:2.4 and 2.4:1 (so 9:16 is allowed). 10–60 fps. 192 kbps–30 Mbps. | A 3–4 minute video is fine. | [A] LinkedIn Help a548372 (fetched) |
| LinkedIn ads | 3 s to 30 min, 15–30 s recommended. 9:16 at 720×1280 recommended, 1080×1920 maximum. 4:5 is the preferred vertical ratio. Under 30 fps. Clips under 30 s loop to 30 s. | n/a | [A] LinkedIn Help 85306 |

**What this means for 3–4 minute vertical videos.** Over 3:00, a video is **not a Short** on YouTube and loses Reels-tab reach on Instagram (likely, grade B/C). It is fine on TikTok, Facebook and LinkedIn. So the app needs a **"platform-safe ≤ 3:00"** option.

Timing detail [D]: AAC encoding adds 1,024 "priming" samples (21.3 ms at 48 kHz) plus end padding, so the container duration can run about 20–50 ms longer than the content. Cap the content at **178.0 s** and check that the container's `format.duration` is **below 179.5 s**.

## 2. Encoding, resolution and loudness targets

### 2.1 Video and container

| Item | Target | Grade / source |
|---|---|---|
| YouTube upload (applies to both formats) | MP4 with **no edit lists** and the **moov atom at the front** (faststart). H.264, progressive, **High profile, 2 consecutive B-frames, closed GOP of half the frame rate**, CABAC, VBR, 4:2:0. Upload at the native frame rate (24/25/30/48/50/60). SDR **1080p: 8 Mbps at 24–30 fps, 12 Mbps at 48–60 fps**; 1440p: 16/24; 2160p: 35–45/53–68. **BT.709** primaries, transfer and matrix. Avoid an RGB matrix. | [A] YT Help 1722171 |
| YouTube audio | AAC-LC or Opus at **48 kHz**. Stereo **384 kbps**, mono 128 kbps, 5.1 512 kbps. | [A] YT Help 1722171 |
| YouTube 16:9 sizes | 1920×1080 (1080p), 2560×1440, 3840×2160. Don't add padding or black bars; the player adapts to vertical video. | [A] YT Help 6375112 |
| Instagram/Facebook Reels | 9:16. Ads guide now recommends **1440×2560** (1080×1920 is the usual organic size). H.264, square pixels, fixed frame rate, progressive. AAC stereo at 128 kbps or more. 4 GB maximum. | [A] Meta Ads Guide |
| TikTok | 9:16. **1080×1920 recommended**, 720p minimum (540×960 for ads). | [B]/[C] |
| LinkedIn | See section 1. 10–60 fps; bitrate up to 30 Mbps. | [A] |
| One master file for every platform [D] | **1080×1920 or 1920×1080, 30 fps CFR, H.264 High, yuv420p, BT.709-tagged, closed GOP = 15, bf = 2, about 8–12 Mbps, AAC-LC 48 kHz stereo 320–384 kbps, faststart, no edit list.** A 4-minute file at about 12 Mbps is about 360 MB, well under every platform's limit. | derived |

### 2.2 Loudness

| Item | Finding | Grade |
|---|---|---|
| YouTube | Normalises playback toward about **−14 LUFS** (changed quietly in 2019; YouTube publishes no number). It **only turns loud audio down** and never boosts quiet audio, so quiet uploads simply play quiet. You can check with Stats for nerds → content loudness. | [B] Production Advice / MeterPlugs, Sept 2019 |
| TikTok, Instagram, Facebook | No official figure. Third-party measurements and guides cluster around **−14 LUFS integrated with a −1 dBTP ceiling**; a few suggest −10 to −12. | [C] |
| EBU R 128 s2 (Loudness in Streaming, v3, Nov 2023) | Broadcast reference is −23 LUFS. Streaming may use an interim distribution loudness of **−20 to −16 LUFS** when the provider handles dynamics itself. Smartphones in noisy places are named as the use case with limited headroom. | [A] EBU R 128 s2 |
| AES TD1008 (audio-only streaming) | Speech and mixed content: **−18 LUFS**. Music: at most −16 LUFS. **−1 dBTP** for lossy codecs. It doesn't apply to video; AES71 does. | [B] |
| Speech versus background (accessibility) | WCAG 2.x SC 1.4.7 (AAA, written for audio-only content): background sound must be **at least 20 dB below the foreground speech**, apart from occasional 1–2 s sounds. | [A] W3C |
| **Chosen target** [D] | **−14 LUFS integrated, −1.0 dBTP maximum on the encoded file, LRA about 7 LU.** At −14, YouTube applies 0 dB, so nothing changes. It matches the practical consensus for TikTok and Instagram and suits loud phone listening. | derived |

## 3. Safe zones: where platform UI covers a 1080×1920 frame

| Source | Top | Bottom | Left | Right | Grade |
|---|---|---|---|---|---|
| **YouTube / Google "Universal Video Ad Safe Zones"** (official PDF; meant for ads, but the most conservative YouTube reference) | **288** | **672** | **48** | **192** | [A] Google PDF (read) |
| **Meta Reels** (Instagram Reels ads guide): keep 14% at the top, 35% at the bottom and 6% on each side free of text and logos | 269 (14%) | 672 (35%) | 65 (6%) | 65 (6%) | [A] Meta Ads Guide |
| **TikTok in-feed** (TikTok's labelled 720×1280 template scaled up: 160/440/80 px, per third-party tools) | 240 | 660 | 120 | 120, **plus 300 px on the right below y = 840 for the like/comment/share column** | [C] adkit.so (v2026-09-10), upload-post.com |
| TikTok with a 4-line anchor/caption | 252 | 1014 | 120 | 240 below y = 360 | [C] adkit.so |
| **Intersection: "universal critical box"** [D] | **288** | **672 (y ≤ 1248)** | **120** | **192 (x ≤ 888); keep critical text at x ≤ 780 when y > 840** | derived |

- **Usable box:** x 120–888, y 288–1248, or **768×960 px**, which is only about 36% of the frame.
- **Backgrounds** can use the full frame. Decoration, motion, b-roll and colour fields should all run full-bleed.
- **Captions** below y 840 sit next to TikTok's action column. Keep chunks narrow (≤ 600 px) and centred.

**16:9 (1920×1080)**

| Region | Rule | Grade |
|---|---|---|
| Ads (Google's universal zone for horizontal) | Stepped margins. Corner notches: 352×166 at top-left, 433×133 at top-right. Sides: 38 px left, 152 px right. Bottom: 143 px in the centre, 337 px on the left half, stepped 190/309 px on the right. | [A] Google PDF |
| Organic YouTube | Keep **5% side and top margins (96 px / 54 px)**. Reserve **about 130 px at the bottom** for the progress bar and controls and for YouTube's own CC captions. | [D] |
| End screen | Keep the last 5–20 s free of essential content (see section 8). | |

## 4. Captions that improve retention and learning

### 4.1 Evidence

- **Captions help comprehension, attention and memory.** Gernsbacher (2015, *Policy Insights from the Behavioral and Brain Sciences* 2(1):195–202) reviews more than 100 studies showing this, and the benefit applies to everyone, not only Deaf or hard-of-hearing viewers. The largest gains are for non-native speakers and readers who are still learning. Verbatim captions work as well as elaborated ones, but errors in automatic captions get in the way. [A/B]
- **Many people watch muted.** A Verizon Media and Publicis Media survey (n = 5,616 US adults aged 18–54, 2019) found 69% watch with sound off in public. 80% *said* they are more likely to finish a video when captions are available; that is a stated preference, not a measured lift. 37% said captions make them turn the sound on. [B]
- **Word-by-word "karaoke" highlighting** reliably draws the eye to the word being spoken: fewer but longer fixations and fewer backward eye movements. Its effect on comprehension is **mixed**. In a 2015 study of 35 children it improved semantic memory (28% vs 17%). In a 2018 study of 40 children it had no effect, or a negative one. Weaker readers preferred it. For second-language learners, highlighting changes reading but does not always change outcomes. [B: Gerbier, Bailly & Bosse; L2 enhancement studies]
  - **Use it for attention and for muted viewing.** Don't claim it raises learning by itself.
- **Netflix Timed Text Style Guide (en-US):** at most **42 characters per line** and **2 lines**. Reading speed up to **20 characters/s** for adults and **17 characters/s** for children. Bottom-heavy line breaks, broken after punctuation or before conjunctions and prepositions. Never split an article from its noun, a first name from a last name, or an auxiliary or negation from its verb. [A]
- **BBC subtitle guidelines:** **160–180 words per minute**, about 0.33 s per word minimum. Online lines are **68% of the 16:9 frame width**. Text size is often given as about 8% of video height with a 0.67 factor, i.e. about 5.4% (around 58 px at 1080p). [B/C: secondary copies; bbc.co.uk was not fetchable]
- **Short-form practice:**
  - The dominant style is 1–4 words per chunk, heavy sans-serif, a white fill with a thick dark outline or a box, the active word in an accent colour, and a small "pop" scale on each new chunk.
  - Captions usually sit in the middle to lower-middle of the frame, above the UI.
  - [C] This is consensus only; I found no controlled study comparing chunk sizes.
- **Classic legibility research:** all-caps text reads slower than mixed case (Tinker). Use caps only for one- or two-word emphasis. [B, from prior knowledge, not re-fetched]

### 4.2 Text sizes readable on phones [D]

These are derived from Apple's HIG Dynamic Type sizes: body **17 pt**, minimum **11 pt**, Large Title 34 pt, and avoid light or thin weights. (The HIG page didn't render in my fetch; these values are long-standing.)

**Vertical 1080 px wide**, shown full-width on a phone 393 pt wide (about 2.75 video px per pt; a 360 dp Android gives about 3.0):

| Text | Size |
|---|---|
| Absolute minimum (11 pt) | ≈ 30–33 px, rounded up to **36 px** |
| Body (17 pt) | ≈ 47–51 px, so use **52–64 px** |
| Captions | **64–84 px** |
| Headlines (34 pt or more) | ≈ 94 px or more, so use **96–140 px** |

**Horizontal 1920×1080:**

| Viewing | px per pt | 11 pt | 17 pt |
|---|---|---|---|
| Inline in portrait on a 393 pt phone (the worst common case) | 1920/393 ≈ 4.9 | **54 px** | 83 px |
| Fullscreen landscape (about 852 pt wide) | ≈ 2.25 | 25 px | 38 px |

So in 16:9 the **minimum readable text is 54 px (5% of height)**, body text **60–72 px**, headings **96–140 px**.

**Contrast.** Use WCAG AA: **4.5:1** for normal text and **3:1** for large text. Over moving or colourful backgrounds, put a **scrim at 55–75% opacity** or a **4–7 px outline** behind the text.

## 5. The hook in the first 1–3 seconds

| Evidence | Grade |
|---|---|
| TikTok: more than **63%** of its highest-CTR videos deliver the key message in the **first 3 s**. Prioritise the hook in the first **6 s**. Fast music above **120 BPM** is associated with higher view-through (ads). | [B] TikTok Business blog, Ads Help (June 2025) |
| Facebook IQ, 2016 (850+ ads, Q4 2014–Q4 2015): on mobile, **47%** of an ad's value arrives in the first **3 s** and **74%** within **10 s**. Of people who watch 3 s, 65% reach 10 s and 45% reach 30 s. People recall feed content after **0.25 s** of exposure (Fors Marsh). | [B, old] Marketing Dive / Facebook IQ |
| YouTube's Shorts metric **"Viewed vs. swiped away"**. Third-party benchmarks aim for **70–80% viewed**. If 30–40% or more swipe away instantly, the first 1–3 s is failing. | metric [B], benchmarks [C] |
| YouTube long-form "Intro" key moment = **% of viewers still watching at 0:30**. YouTube's advice is to make sure the title and thumbnail match the content and to experiment with the first 30 s. | [A] YT Help 9314415 |
| Since **31 Mar 2025**, a Shorts view counts at **any start or replay**. The older "engaged views" metric survives in Advanced Mode, and YouTube Partner Program maths is unchanged. Loops and replays therefore raise the visible view count. | [B] PPC Land and other press |

**Rules this leads to** [D]

- **Frame 0 must already be a designed frame:** headline text plus motion. No fade from black and no logo sting first. The first frame doubles as the feed thumbnail on Shorts, Reels and TikTok.
- **Text is on screen by 0.5 s.** The spoken hook (a question, a surprising claim or "by the end you'll be able to…") is finished by **3 s**. The whole hook section ends by **6 s**.
- **Hook types for education:** a counter-intuitive question ("Why doesn't…?"), a common misconception, a striking visual result shown first, a problem posed with stakes, or a "3 things" roadmap.

## 6. Keeping retention up (pattern interrupts, progress cues, loops) and educational evidence

### 6.1 Peer-reviewed and large-scale education evidence

**Guo, Kim & Rubin (2014), L@S.** 6.9 million viewing sessions, 862 videos, 4 edX courses. I read it in full. [A]
- **Median engagement time was at most 6 minutes, whatever the video length.** Normalised engagement:
  - About 100% for videos under 6 min.
  - Less than half for videos over 9 min.
  - About 20% for 12–40 min videos.
- **Problem attempts after a video** fell with length: **56% / 48% / 43% / 41% / 31%** across the 0–3, 3–6, 6–9, 9–12 and 12–40 minute buckets.
- Interleaving a **talking head** with slides beat slides alone.
- **Khan-style freehand drawing** was 1.5–2× more engaging than slides or code screencasts. Its recommendation: add **motion and continuous visual flow**.
- **Speaking rate** ranged from 48 to 254 wpm (mean 156, SD 31). The fastest bucket (**185–254 wpm**) was most engaging, especially for 6–12 minute videos. The mid-pace bucket (145–165 wpm) was least engaging, because those instructors were the least energetic.
  - The paper says don't force speed. Bring out enthusiasm and edit out pauses and fillers.
- Lectures should optimise the first viewing. Tutorials need **visual signposts and bookmarks** for skimming and re-watching.

**Brame (2016), *CBE–Life Sciences Education* 15(4):es6.** A synthesis of cognitive-load principles. [B, summary read] It recommends:
- **Signalling**: on-screen keywords, colour or contrast changes, arrows.
- **Segmenting**: videos of 6 min or less.
- **Weeding**: cut extraneous material. Music, complex backgrounds and extra animation can **raise extraneous load and reduce learning**.
- **Matching modality**: narration and visuals should carry complementary information.
- **Conversational, enthusiastic delivery**, plus guiding questions and interactive questions.

**Moreno & Mayer (2000), coherence effect.** Background music or irrelevant sounds reduced retention and transfer in multimedia lessons. [B, from prior knowledge, not re-fetched in this session]
- This **conflicts** with the owner's music requirement. The fix: keep the background music quiet, instrumental and ducked, and dip or drop it during dense explanations.

**ReelsEd (arXiv 2509.05962, 2025).** GPT-4 cut 10–15 minute lectures into **5–6 labelled reels of 30–60 s**. [B, preprint]
- 62 students were randomly assigned to reels or the original long video.
- Quiz scores: **93.9% vs 79.7%** (p = 0.0001). Quiz time 329 s vs 446 s. Perceived understanding and retention also higher.
- Students asked for **subtitles**, a gallery of reels and downloadable key points.
- Caveats: a 6-item quiz, no delayed retention test, a small single-country sample.

**Open University of Israel (2025).** A randomised trial, n = 120, TikTok micro-learning vs classroom teaching for motor skills: no significant difference. [B, conference paper]

***Computers & Education* (2025) series.** Raises a concern that habitual short-video use encourages surface processing. [C, snippet only]

### 6.2 Pacing evidence

**Lang et al. (2000).** More frequent edits raised arousal, attention and memory for TV messages. [B]
- Fast pacing combined with arousing content hurts **verbal** encoding more than visual encoding (Lang, Potter & Bolls, 1999).
- One unverified secondary claim: recognition drops above about 10 cuts per 2 minutes.
- **Change is good, but too much change, especially combined with dense narration, is not.**

**Shot lengths in film** fell from about 12 s (1930) to about 4 s (1985–2005), and to about 2.5 s in recent action films (Cutting, DeLong & Nothelfer, 2010 and later). [B] Modern viewers are used to visual change every few seconds.

**Practitioner consensus for Shorts, Reels and TikTok** [C]:
- A visual change every **2–4 s**.
- A "re-hook" every **15–30 s** ("but here's the catch…").
- Open loops: tease something, pay it off later.
- A seamless loop ending.
- A visible progress structure ("3 of 5").

### 6.3 Progress cues and loops

- **Progress cues.** YouTube's chapters and Guo's "signposts" support skimming. In vertical video, a chapter label or "2/5" pip row is safest **just below y = 288**, because the top band is covered by the Shorts, TikTok and Reels headers. [D]
- **Loops.** Shorts, Reels and TikTok autoplay in a loop, and Shorts now counts replays as views, so ending on a line that runs naturally back into the opening question raises replays. [C/D]
- **Linking out.** Shorts have no end screens. Use YouTube's **"Related video"** link instead: one per Short, shown under the channel handle, needs advanced-feature access, and can point to the 5-minute horizontal version. [A] YT Help 14075157

## 7. Educational short-form: what the data supports

| Format | Best-supported lengths | Grade |
|---|---|---|
| Single-concept micro-clips | **30–60 s**: ReelsEd's improved quiz scores, and Socialinsider's top band for Instagram reach (45–60 s) | [B] |
| Shorts on YouTube | Up to **180 s** allowed. Performance above 60 s depends on retention, and a Content ID claim above 60 s means a block. | [A]/[C] |
| A whole lesson | Up to about **6 min** before engagement collapses (Guo). **5:00 is in the sweet spot.** | [A] |
| 3–4 min vertical | Allowed on TikTok, Facebook and LinkedIn. **Not a Short** on YouTube over 3:00; weaker Instagram reach over 3:00 (or over 90 s per Instagram's 2024 advice). Treat it as "vertical long-form" made of self-contained micro-chapters. | [D] |

## 8. YouTube long-form specifics for ~5-minute educational videos (16:9)

| Item | Rule | Grade |
|---|---|---|
| **Chapters** | Put them in the description. The first timestamp is **00:00**, with **at least 3** timestamps in ascending order and each chapter **at least 10 s**. Manual chapters override automatic ones. YouTube builds chapters from the description, not from MP4 chapter atoms (the repo already writes `chapters.youtube_chapters()` text that enforces 10 s and 00:00). | [A] YT Help 9884579 |
| **End screen** | Only in the **last 5–20 s**. The video must be **at least 25 s** long. Up to **4 elements** on 16:9 (fewer on other ratios). Not shown on made-for-kids videos, mobile web, YouTube Kids or the Music app. Card teasers and watermarks are suppressed while it shows. YouTube advises planning the last 20 s for it. | [A] YT Help 6388789 |
| **Intro** | YouTube measures the intro as **retention at 0:30**. Deliver the hook and the "what you'll learn" by about **0:15**, and the first real insight before 0:30. A branded intro (Aadhi) should be about **5–8 s at most**, ideally carrying the hook itself rather than playing before it. | [A] metric, [D] numbers |
| **Length** | Target **4:30–5:30**. Engagement is near-complete under 6 min (Guo). | [A]/[D] |
| **Captions** | Upload an SRT or VTT sidecar. Don't burn captions into the YouTube master, because YouTube's own CC would overlap them. Burned-in captions are only for social reposts. | [D] |
| **Thumbnail** | 1280×720 (16:9), consistent with the title (YouTube's intro advice). | [A]/[D] |
| **Encoding** | See 2.1: 1080p30, about 8 Mbps or more (CRF 18 is fine), GOP 15, AAC 384 kbps, 48 kHz. | [A] |

---

## 9. What the repo does today (read-only check; matters for implementation)

- **No loudness normalisation anywhere in `aadhi/`.** There is no `loudnorm` or `ebur128`. The final mux mixes narration with background music at a fixed `bgm_volume = 0.06` (about −24.4 dB), ducked with `sidechaincompress` (threshold 0.015, ratio 8, attack 15 ms, release 450 ms), then encodes AAC at **192 kbps**.
  - With several TTS providers and voices, which is the owner's "different voice per video" request, **final loudness will vary from video to video** unless a normalisation pass is added.
- **Caption burn-in** (`aadhi/compose/ffmpeg.py` `subtitles_filter`) uses SRT with `force_style FontSize = round(height/1080 × 22)` and `MarginV = 36`.
  - libass reads SRT in ffmpeg's default ASS script space (PlayResY 288), so sizes and margins are **already scaled by frame height**. The formula then scales them a second time.
  - At 1080×1920 that gives FontSize 39, which renders at **about 260 px**, and MarginV of about **240 px**, which puts captions **inside the bottom UI zone (672 px)**. The mismatched 4:3 script space may also stretch glyphs.
  - I didn't test this render, so treat it as an inference [D].
  - **Fix:** write native **ASS with `PlayResX/PlayResY` set to the output size**, so units are pixels.
- **The render stage is fixed at 1920×1080** (`STAGE_WIDTH/HEIGHT` in `compose/base.py`, `render_width/height` in config). Vertical output needs a 1080×1920 stage, not a crop.
- **The Aadhi clips are 1280×720 at 24 fps.** Cropping a 9:16 slice (405×720) and enlarging it to 1080×1920 is a **2.67× upscale** and will look soft. Show Aadhi in vertical as a **framed 16:9 card** (for example 840×472, a downscale) or as a cut-out, not a crop.
- **Already compliant:** YouTube chapter text, BT.709 tagging, faststart, CFR, 48 kHz.

---

## DESIGN IMPLICATIONS FOR AADHI

Concrete parameters. [D] marks a derived choice; the evidence is in sections 1–8.

### A. Formats and lengths
1. **Two output formats** (one setting per render):
   - `horizontal`: **1920×1080**.
   - `vertical`: **1080×1920**.
   - Both at **30 fps CFR**.
   - Optional `vertical_hq` at **1440×2560** (Meta's recommended Reels size and YouTube's 1440p quality tier). Off by default because of render time.
2. **Horizontal length:**
   - Target **5:00 ± 0:30**, hard ceiling **6:00** (Guo).
   - 4–6 chapters of **40–75 s** each.
3. **Vertical length profiles** (choose in the UI, with a plain warning):
   - `shorts_safe` (default): target **2:40–2:55**. Content capped at **178.0 s**. QA fails the render if the container duration is **179.5 s or more**. Valid as a YouTube Short and an Instagram Reel everywhere.
   - `extended`: **3:00–4:00**. For TikTok, Facebook, LinkedIn and Instagram followers. On YouTube it becomes a regular vertical video, not a Short. The UI must say this.
   - `micro` cut-downs: one per chapter, **30–60 s (aim for 45–60 s)**, generated from the same timeline. Each starts on that chapter's own mini-hook.
4. **Vertical micro-chapters:**
   - **20–40 s** each, 5–7 per 3 minutes.
   - Each one self-contained (mini-hook, explanation, one-line takeaway), so the micro cut-downs and re-hooks come for free.

### B. Aadhi's screen time (the owner's rule, made measurable)
5. **Aadhi appears only:**
   - in the **intro, 0–6 s** in vertical (maximum 8 s) and **0–8 s** in horizontal (maximum 10 s);
   - optionally in a **recap of 5–10 s**;
   - **nowhere else**.
   - Add a QA lint: total Aadhi time must be **20 s or less** per video, with **no Aadhi between 10 s and the start of the recap**.
6. **In the intro, Aadhi carries the hook.** Overlay the hook headline from frame 0 rather than playing a greeting first.
   - In vertical, show the 16:9 clip as a rounded card about **840×472 px** centred in **y 400–880**, with the hook text above it, starting at y ≥ 300. Never crop and upscale the 1280×720 clip.
7. **Keep the existing full-time-mascot pipeline as the separate "Aadhi" tab.** The new mode uses full-frame designed backgrounds, with no mascot outside rule 5.

### C. Hook and pacing
8. **Frame 0 is a designed cover frame:**
   - headline of **7 words or fewer**, **96–140 px** (vertical) or **110–150 px** (horizontal);
   - motion already running;
   - **no fade from black, no logo first**.
   - It doubles as the feed thumbnail.
9. **Hook timing:** on-screen text by **0.5 s**; spoken hook finished by **3.0 s**; hook section over by **6.0 s** (TikTok guidance).
   - In horizontal, state the learning objective or roadmap by **0:15** and give the first payoff before **0:30** (YouTube's intro metric).
10. **Visual change cadence** — a "change" means a new element, a highlight, a camera push or pan of 3–8%, a cut, or a palette change:
    - vertical: a change every **2–3 s**; **no frame static longer than 4 s**;
    - horizontal: a change every **3–5 s**; **no frame static longer than 8 s**.
    - Add a QA lint that flags static stretches longer than these limits from the timeline's state list.
11. **Re-hooks and pattern interrupts:** vertical every **20–30 s**, horizontal every **30–45 s**. Use a chapter card no longer than **1.2 s** (vertical) or **1.5 s** (horizontal), a question, a "predict this" pause, or a contrast reveal.
    - Don't stack fast cuts on top of dense narration (Lang): during a definition or derivation, **slow to one change per 4–5 s**.
12. **Colour variety without chaos:**
    - Each chapter gets its own palette or background family, changing at **every chapter boundary**: roughly every 20–40 s in vertical and 40–75 s in horizontal.
    - Keep **one palette within a chapter**, for coherence.
    - Accent highlights on key terms can change per beat.
    - All text keeps **WCAG contrast of 4.5:1 or better** (3:1 for headings 72 px and up), using a **55–75% opacity scrim** when the background is busy.
13. **Retrieval moment** (a quick quiz or "pause and predict") every **60–90 s**. Guo found problem attempts fall as videos get longer.
14. **Narration speed** (applies to every voice option): vertical **165–185 wpm**, horizontal **150–170 wpm**, never above **190 wpm** (keeps captions readable: BBC 160–180).
    - Trim pauses over **0.6 s** in vertical and over **0.9 s** in horizontal, except deliberate "think" pauses.
15. **Vertical ending:**
    - recap of **10 s or less** (Aadhi allowed) with 3 keywords;
    - a one-line CTA of **3 s or less**;
    - the final spoken line leads back into the opening question so the loop is seamless;
    - no "thanks for watching" card. Total outro **13 s or less**.
16. **Horizontal ending:** recap of **20–30 s** (Aadhi 5–10 s within it), then a **12–15 s end-screen layout** (allowed range 5–20 s). Clean background with **4 empty slots**; no essential information in the last 15 s.

### D. Layout and safe zones
17. **Vertical "universal critical box"** for anything that must be read:
    - **x 120–888, y 288–1248**;
    - for y **above 840**, keep critical text at **x ≤ 780** (TikTok's action column).
    - Backgrounds, b-roll and decoration go **full-bleed 1080×1920**.
18. **Vertical zones:**
    - progress or chapter pips at **y 300–340**;
    - hook or headline band at **y 340–620**;
    - main visual stage at **y 340–1000**, 768 px wide;
    - **caption band at y 1020–1240**, centred at x = 540, with each chunk **600 px wide or less**.
19. **Horizontal margins:** 5% title-safe (**96 px left and right, 54 px top**); keep the **bottom 130 px** free of essential text (player controls and YouTube CC).
20. **Safe-zone QA:** the render page (`web/render.html`) reports bounding boxes for text elements in each state. The render fails, or warns, if any critical text box falls outside the zone for its format. Safe-zone figures are versioned constants: `YT_SHORTS = (288, 672, 48, 192)`, `META_REELS = (269, 672, 65, 65)`, `TIKTOK = (240, 660, 120, 120, rail = (300, y ≥ 840))`.

### E. Captions
21. **Vertical captions are burned in by default** (muted viewing; Gernsbacher):
    - **1–4 words per chunk**, at most **2 lines** of about **20 characters** or less;
    - font **64–84 px**, weight **700–900**, sentence case (ALL CAPS only for emphasis of 2 words or fewer);
    - white fill with a **5–7 px** black outline and a soft shadow (2–4 px blur, 40–60% opacity), *or* a rounded box at **70–80% opacity**;
    - the **active word in an accent colour** that changes per chapter palette and keeps 4.5:1 contrast against its outline or box;
    - a pop scale of **1.00 → 1.08 → 1.00 over 120 ms** when each chunk appears;
    - each chunk on screen for at least **0.35 s** and at most **2.5 s**;
    - breaks follow the Netflix rules: never split article + noun, number + unit, or auxiliary + verb; break after punctuation.
22. **Horizontal captions:** **not burned** into the YouTube master. Export an **SRT/VTT sidecar** with at most 42 characters per line, 2 lines, and 20 characters/s or less.
    - An optional burned version for social reposts: **54–60 px**, bottom margin **86 px or more**.
23. **Build captions as native ASS** with `PlayResX × PlayResY` equal to the output size (pixel units).
    - Show the active word with a separate Dialogue event per word span, or `\kf`/`\c` plus `\t` for the pop.
    - This replaces the current SRT + `force_style` path, whose sizing and margins break at 1080×1920 (section 9).
    - Word timings already exist from TTS.
24. **On-screen text and captions do different jobs.** Board or overlay labels are **keywords, 6 words or fewer**; the caption is the verbatim narration.
    - Never show the narration as paragraphs on the board, because that duplicates it and adds load (Mayer's redundancy and coherence principles).

### F. Text sizes (pixels in the output frame)
25. **Vertical (1080 wide):**
    - minimum anywhere **36 px**;
    - body and labels **52–64 px**;
    - key terms **72–96 px**;
    - hook or chapter titles **96–140 px**;
    - weight **600 or more** for anything 64 px or smaller; never thin or light weights.
26. **Horizontal (1920×1080):**
    - minimum **54 px**;
    - body **60–72 px**;
    - headings **96–140 px**;
    - **3 text lines or fewer and about 15 words or fewer** per board state.
27. **Fonts:** vendor SIL OFL 1.1 families only (for example Inter, Montserrat, Poppins, Lexend). Check each family's licence file when vendoring.

### G. Audio
28. **Final master loudness:** **−14 LUFS integrated, true peak −1.0 dBTP or lower measured on the encoded AAC, LRA about 7 LU.**
    - Implement as a two-pass `loudnorm` (`I=-14:TP=-1.5:LRA=7`, measured values, `linear=true`), then `aresample=48000`; loudnorm runs internally at 192 kHz.
    - Add an **R128 QA check**: integrated −14 ± 1 LU, true peak ≤ −1.0 dBTP.
29. **Pre-normalise every voice track** to **−16 LUFS** before mixing, so voices from different providers and speakers sound equally loud.
30. **Background music under narration:**
    - while speech plays, the music's short-term loudness stays **20 dB or more below the speech** (about −34 LUFS against −14);
    - in non-speech gaps (cards, transitions) it may rise to about **−22 LUFS**;
    - ducking attack 15–50 ms, release 300–600 ms;
    - **instrumental only, no lyrics**;
    - dip a further 3–6 dB during dense explanations (coherence effect);
    - fade in 1 s and out 2 s (as now).
31. **Background-music licensing:**
    - Use only **self-generated or CC0/owned tracks that are not registered with Content ID**. Any claim blocks a Short over 60 s on YouTube.
    - Store the licence and provenance with every track and every **imported external video clip**. Warn when an imported clip comes from YouTube or another third party.
32. **Audio encoding:** **AAC-LC, 48 kHz stereo, 320–384 kbps.** The current 192k should be raised to 384k to match YouTube's recommendation.

### H. Video encoding (one master per format)
33. **libx264:**
    - High profile, `yuv420p`, BT.709 tags (already present);
    - **`-g 15 -keyint_min 15 -bf 2`** (closed GOP of half the frame rate at 30 fps);
    - **CRF 18** with `-maxrate 12M -bufsize 24M` for 1080p30;
    - `-movflags +faststart`, **`-use_editlist 0`** (YouTube says no edit lists).
    - For `vertical_hq` 1440×2560: about 16 Mbps.

### I. Export bundle and platform metadata
34. **Every render includes:**
    - the MP4;
    - an `.srt` and a `.vtt`;
    - a YouTube description block with the chapter list (existing `youtube_chapters`; require at least 3 chapters, each 10 s or longer, starting at 00:00);
    - a title of about 60 characters or fewer, which the UI can lint;
    - a 1280×720 thumbnail for horizontal, or the cover frame (frame 0) for vertical.
35. **For a Short:** suggest linking the 5-minute horizontal video through YouTube's **"Related video"** field (needs advanced features). Shorts have no end screens.
36. **No third-party watermarks in any export.** Instagram's 2024 guidance said watermarked or recycled content loses reach. Keep the Aadhi brand mark, if any, inside the safe box and at **60% opacity or less**.
37. **Optional TikTok note:** videos must be **over 60 s** to be eligible for Creator Rewards (third-party guides).

### J. QA gates to add (deterministic, from the timeline and the final file)
38. Duration cap per profile (rule 3).
39. Loudness and true peak (rule 28).
40. Safe-zone bounding boxes (rule 20).
41. Maximum static hold (rule 10).
42. Aadhi screen-time window (rule 5).
43. Frame 0 contains readable text in the critical box (rule 8).
44. Caption chunk length and duration bounds (rule 21).
45. Every background-music track and imported clip has a recorded licence (rule 31).

### K. Check in the apps before launch (evidence is only grade C)
- Instagram's current upload maximum (3 vs 15 vs 20 min) and whether Reels over 3:00 reach non-followers.
- TikTok's 60-minute upload availability.
- TikTok's exact in-feed safe-zone overlay (download the official template from TikTok Ads Manager).
- No official figure exists for TikTok or Instagram loudness normalisation.
- Re-check all of these each quarter: the limits changed five times between 2024 and 2026.

---

## Sources (accessed 2026-10-09 unless noted)

**YouTube and Google**
- YouTube Help, *Understand three-minute YouTube Shorts*: https://support.google.com/youtube/answer/15424877 [A]
- YouTube Help, *Upload YouTube Shorts*: https://support.google.com/youtube/answer/12779649 [A]
- YouTube Blog, *Tall updates coming to Shorts* (Oct 2024): https://blog.youtube/news-and-events/tall-updates-coming-to-shorts/ [A, via search]
- YouTube Help, *Recommended upload encoding settings*: https://support.google.com/youtube/answer/1722171 [A]
- YouTube Help, *Video resolution & aspect ratios*: https://support.google.com/youtube/answer/6375112 [A]
- YouTube Help, *Video chapters*: https://support.google.com/youtube/answer/9884579 [A]
- YouTube Help, *End screens*: https://support.google.com/youtube/answer/6388789 [A]
- YouTube Help, *Audience retention / key moments*: https://support.google.com/youtube/answer/9314415 [A]
- YouTube Help, *Add a related video to your Shorts*: https://support.google.com/youtube/answer/14075157 [A]
- Google, *Universal Video Ad Safe Zones* (PDF): https://services.google.com/fh/files/misc/universalsafezones-youtube.pdf [A]
- Google Ads Help, *Square and vertical video*: https://support.google.com/google-ads/answer/9128498 [A]
- PPC Land, Shorts view counting from 31 Mar 2025: https://ppc.land/youtube-changes-how-shorts-views-are-counted-from-march-31/ [B]

**Meta (Instagram and Facebook)**
- Meta Ads Guide, *Instagram Reels video*: https://www.facebook.com/business/ads-guide/update/video/instagram-reels [A]
- Social Media Today, Instagram Reels go to 3 minutes (18 Jan 2025): https://www.socialmediatoday.com/news/instagram-officially-expands-reels-length-to-3-minutes/737766/ [B]
- Social Media Today, Instagram says Reels over 90 s hurt reach (13 May 2024): https://www.socialmediatoday.com/news/instagram-longer-reels-hurt-performance [B]
- Socialinsider, Instagram Reels length data (14 Jul 2026, about 6M Reels): https://socialinsider.io/blog/instagram-reels-length/ [B]
- Inro, Instagram Reels limits (23 Jul 2026): https://www.inro.social/blog/instagram-reels-can-now-be-20-minutes-long-new-time-limit-explained-2025 [C]
- MacMagazine, 20-minute Reels (26 Nov 2025): https://macmagazine.com.br/post/2025/11/26/instagram-libera-reels-de-ate-20-minutos-e-testa-ajuste-do-seu-algoritmo/ [C, snippet only]
- TechCrunch, all Facebook videos become Reels (17 Jun 2025): https://techcrunch.com/2025/06/17/facebook-announces-that-all-videos-on-its-platform-will-soon-be-shared-as-reels [B]
- Marketing Dive / Facebook IQ, mobile video attention (2016): https://www.marketingdive.com/news/facebook-why-mobile-video-ads-must-work-fast/446217/ [B, old]

**TikTok**
- TechCrunch, TikTok tests 60-minute uploads (16 May 2024): https://techcrunch.com/2024/05/16/tiktok-upload-60-minute-videos [B]
- OpenClip, TikTok length limits 2026: https://openclip.app/guides/tiktok-video-length [C]
- TikTok Ads Help, *Creative best practices* (updated June 2025): https://ads.tiktok.com/help/article/creative-best-practices [B, via search; direct fetch refused]
- TikTok Ads Help, *Auction in-feed ads*: https://ads.tiktok.com/help/article/tiktok-auction-in-feed-ads [B, via search]
- TikTok Business blog, *9 creative tips*: https://ads.tiktok.com/business/en-US/blog/9-creative-tips-to-drive-auction-ad-performance [B, via search]
- adkit.so, TikTok safe zones (v2026-09-10): https://adkit.so/tools/safe-zones/tiktok [C]
- upload-post.com, safe-zone checker: https://www.upload-post.com/tools/safe-zone-checker/ [C]

**LinkedIn**
- LinkedIn Help, video upload specs: https://www.linkedin.com/help/linkedin/answer/a548372 [A]
- LinkedIn Help, video ad specs: https://www.linkedin.com/help/linkedin/answer/85306 [A]

**Audio and accessibility standards**
- EBU R 128 s2, *Loudness in Streaming* (v3, Nov 2023): https://tech.ebu.ch/docs/r/r128s2.pdf [A]
- MeterPlugs / Production Advice, YouTube's −14 LUFS reference (18 Sep 2019): https://meterplugs.com/blog/2019/09/18/youtube-changes-loudness-reference-to-14-lufs.html [B]
- AES TD1008 commentary: https://productionadvice.co.uk/td1008/ [B]
- W3C, *WCAG 2.2 Understanding SC 1.4.7*: https://www.w3.org/WAI/WCAG22/Understanding/low-or-no-background-audio.html [A]

**Captions and typography**
- Netflix, *English (USA) Timed Text Style Guide*: https://partnerhelp.netflixstudios.com/hc/en-us/articles/217350977-English-USA-Timed-Text-Style-Guide [A]
- BBC subtitle guidelines, secondary copy: https://clevercast.com/bbc-subtitling-guidelines [C] (primary: https://www.bbc.co.uk/accessibility/forproducts/guides/subtitles/, not fetchable)
- Gernsbacher (2015), *Video Captions Benefit Everyone*, DOI 10.1177/2372732215602130: https://successforkidswithhearingloss.com/wp-content/uploads/2020/04/Video-Captions-Benefit-Everyone_2015.pdf [A/B]
- Verizon Media / Publicis caption survey (2019), via Streaming Media: https://www.streamingmedia.com/Articles/News/Online-Video-News/80-of-Video-Caption-Users-Arent-Hearing-Impaired-Finds-Verizon-131860.aspx [B]
- Gerbier, Bailly & Bosse, synchronised highlighting: https://hal.archives-ouvertes.fr/hal-01575227 and https://www.isca-archive.org/slate_2015/gerbier15_slate.pdf [B]
- Apple HIG, *Typography*: https://developer.apple.com/design/human-interface-guidelines/typography [values from established knowledge; the page did not render]

**Educational video research**
- Guo, Kim & Rubin (2014), L@S, DOI 10.1145/2556325.2566239: https://up.csail.mit.edu/other-pubs/las2014-pguo-engagement.pdf [A, read in full]
- Brame (2016), CBE-LSE 15(4):es6: https://pmc.ncbi.nlm.nih.gov/articles/PMC5132380/ [B, summary]
- ReelsEd, arXiv 2509.05962 (2025): https://arxiv.org/html/2509.05962v1 [B]
- Open University of Israel, TikTok vs classroom trial (2025): https://www.openu.ac.il/Lists/MediaServer_Documents/innovation/chais/2025/b4_4.pdf [B, via search]
- Lang et al. (2000), effects of edits on arousal, attention and memory: https://scholars.cityu.edu.hk/en/publications/the-effects-of-edits-on-arousal-attention-and-memory-for-televisi/ [B, abstract-level]
- Cutting, DeLong & Nothelfer (2010), *Psychological Science*, shot-length trends [B, via secondary summaries]
- Moreno & Mayer (2000), *J. Educational Psychology* 92(1):117–125, coherence effect of music and sounds [B, from prior knowledge, not re-fetched]
