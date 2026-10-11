# Research: an in-browser editor for Aadhi's generated lectures (topic `editor-nle`)

Date: 2026-10-09. Scope: how teachers who are not video editors can **insert outside video clips** and **cut out unwanted parts** reliably, in an app that builds each video as screenplay -> timeline -> deterministic MP4 render. The repo was read but not changed. Files read: `docs/ARCHITECTURE.md`, `aadhi/schemas/timeline.py`, `aadhi/pipeline/audio.py`, `aadhi/compose/video.py`, `aadhi/providers/_http.py`, the TTS adapters and `web/js/studio/views/editor/timelineStrip.js`.

Each finding is tagged with how strong its evidence is:
- **[A]** official documentation, a licence text, a standard, or a peer-reviewed paper.
- **[B]** a reputable secondary source, or a vendor help page describing that vendor's own product.
- **[C]** marketing, forums, third-party summaries, or my own inference. These are labelled as such.

**Limitation.** The web-search budget shared by this run ran out partway through. Six items below rest on secondary sources only, and three were not checked at all; all are marked in the text:
- Secondary only: CapCut's timeline toggles, Canva's split shortcut, WeVideo's shortcuts (the page returned 403), YouTube's −14 LUFS figure, Mixkit's full licence text, and the licence of `tuspyserver`.
- Not checked: InVideo's timeline help (the page returned 404), Wikimedia Commons and NASA as stock sources, and the details of ffmpeg's `tile` filter.

---

## 1. Executive summary

1. **Do not add a free-form, absolute-time EDL on top of the built timeline.** In Aadhi, timings change whenever the voice changes, the TTS runs again, a scene is regenerated, the lecture is translated, or `min_seconds` or `hidden` changes. The new requirements ask for a different voice in each video, so a cut stored as "12.4 s to 15.1 s" would land on the wrong words.
   - Store edits **anchored to content** instead: scene id, beat id and word index, plus the clip's own source times.
   - `build_timeline` compiles those anchors into times. This is Final Cut Pro's "primary storyline + connected clips" model [A], with Aadhi's narration as the primary storyline.
2. **Cut narration at the audio-assembly stage, not in the render.** `pipeline/audio.build_scene_audio` already decodes each beat's TTS clip to 24 kHz PCM, trims it and joins the beats with sample-exact offsets.
   - Cutting a word range inside that join gives captions, sync cues and board reveals that are correct by construction, because all three are rebuilt from the remaining `TimedWord`s.
   - It also costs nothing: no new TTS call.
3. **Make the script the main editing surface, with the timeline as a precise secondary view.** The best controlled evidence is B-Script (CHI 2019, 110 novice editors) [A]. Inserting B-roll from a transcript was faster and easier than doing it on a timeline, and recommendations made the videos more engaging.
   - Descript, Kapwing and others ship transcript deletion as their main cutting tool [B].
4. **Offer two ways to insert a clip:**
   - **Pause narration and play the clip**: a new `clip` scene on the spine. The clip's own sound is kept by default.
   - **Show over the narration (B-roll)**: an overlay attached to a word. The clip's sound is muted by default.
   - Every delete is a **ripple** delete: the narration spine is magnetic and never leaves gaps, the way Final Cut's magnetic timeline and CapCut's main-track magnet work [A/C].
5. **Never use stream copy (`-c copy`) for an edit.** Stream-copy cuts snap to keyframes and can start with a frozen or blank picture or an audio/video offset [A/B].
   - Re-encode every segment. The renderer already does this.
   - Seek with `-ss` before `-i`, which is frame-accurate when transcoding (FFmpeg ≥ 2.1) [A].
   - Snap all clip in and out points to the 30 fps frame grid.
6. **Normalise every inserted clip on the server** into a mezzanine file plus a proxy, a thumbnail sprite and waveform peaks:
   - Mezzanine: H.264, yuv420p, BT.709 SDR, constant 30 fps, AAC 48 kHz, 1 s GOP.
   - Phone footage is often HDR (HLG/PQ) and HEVC, which needs tone mapping and transcoding. Screen recordings from MediaRecorder have a variable frame rate and no duration or seek index [B].
7. **Importing from a link: direct media-file URLs only**, fetched through an SSRF-safe fetcher that resolves DNS once, pins the IP and re-validates every redirect (OWASP) [A].
   - **Refuse YouTube and other platform watch pages.** YouTube's Terms and its API policies (§III.E.1.a, updated 2026-09-14) forbid downloading or caching its content without written approval [A].
   - Point creators to YouTube Studio's own download of their uploads (720p or 360p) [A].
8. **Stock footage:** integrate the **Pexels** API (200 requests/hour and 20k/month by default, attribution link required) and the **Pixabay** API (100 requests per 60 s, cache 24 h, show the source) [A].
   - Leave **Mixkit** out: its terms forbid making items available to third parties [B].
   - Leave **Coverr** out for now: production use needs a paid plan and its attribution rule is contradictory [B].
9. **Audio rules for inserted clips:**
   - Normalise kept clip sound to the narration loudness.
   - Duck B-roll sound at least 20 dB under speech (WCAG 1.4.7 [A]).
   - Fade background music out under a clip that keeps its sound.
   - Use fades of about 5–20 ms at every audio splice to prevent clicks [C, practitioner consensus].
10. **Use the browser for preview only, not encoding.** The live player plays the compiled timeline with `<video>` elements on short-GOP proxies, and `requestVideoFrameCallback` confirms frames (Baseline since Oct 2024) [A].
    - No WebCodecs, ffmpeg.wasm or Remotion is needed. Remotion requires a paid company licence for organisations with more than 3 employees [A].

---

## 2. What existing editors do (UX survey)

| Product | Main editing model | Cutting | Inserting media | Clip audio | Evidence |
|---|---|---|---|---|---|
| **Descript** | Script first: the transcript is linked to the media, and deleting text deletes media. | Delete words (non-destructive, with "Restore removed media" at the edit boundary). **Ignore** strikes the text through and skips it in playback, subtitles and export. "Shorten word gaps" uses a duration filter and a target, e.g. 200 ms, with Shorten / Shorten all. "Remove filler words" works per instance, with an **"Avoid harsh cuts"** option that skips removals that would clip neighbouring speech. "Smooth jump cuts" uses AI regeneration on the seams, is billed in credits, and offers a review-first or apply-to-all mode. | **Layers** belong to *scenes*. Selecting a script range and dropping media on it creates scene boundaries at the start and end of the selection. Layers are not transcribed. | Not in the pages read. | [B] help.descript.com |
| **Kapwing** | Timeline, plus "Trim with Transcript". | Deleting transcript text trims the video. **Smart Cut**: a sensitivity slider; each detected silence can be toggled (red = remove, gray = keep), its handles widen or narrow the cut, there is a Reset, and **Done** applies the cuts as split clips. "Find Scenes" uses the same review pattern for scene splits. | Upload, or paste a URL; it advertises YouTube, Drive, Instagram and other sites. | Not in the pages read. | [B] kapwing.com/help |
| **Clipchamp** | Track timeline that snaps clips together. | **S** splits; Delete removes; gaps are removed one at a time or with "Delete all visual gaps", and the app asks again at export. Keyboard trim is Z+arrows for the start and C+arrows for the end. | Upload and a stock library. | Not covered. | [A] support.microsoft.com |
| **CapCut** | A "main track" with a **magnet**, so deleting or inserting closes gaps; **auto snapping** (N); **linkage** keeps overlays attached to the main clip. | Split, delete, magnet ripple. | Stock and uploads. | — | [C] forums and course outlines only |
| **Canva video** | Pages/scenes plus a timeline. | **S** or right-click "Split"; cutting out a middle part means two splits and a delete. | Large stock library. | — | [C] (official help not reached) |
| **VEED** | Timeline. | Split and trim. | Stock audio and video on paid plans. | Per-clip mute or volume, "detach audio" or "Extract Audio". No automatic ducking was documented. | [B] support.veed.io |
| **WeVideo** (education market) | Timeline. | **S** splits at the playhead; **I/O** trim the selected clip to the left or right of the playhead; Ctrl/Cmd+Z undoes. | — | — | [C] search snippet of the official page (403) |
| **InVideo** | AI-generated scenes plus a timeline. | Split and cut on the timeline. | Stock. | — | not verified (404) |
| **Final Cut Pro** (reference model) | **Magnetic timeline**: a primary storyline, with connected clips (B-roll, titles, music) attached to it. Moving a storyline clip moves everything attached; neighbours close up or move aside so there are no holes; gap clips hold time. | Ripple by default; the Position tool overwrites and leaves a gap. | Connected clips go above, audio below. | — | [A] support.apple.com |
| **Premiere Pro** (conventions) | Tracks. | **J/K/L** shuttle (repeated J or L goes faster), **I/O** marks, Shift+I/O go to the marks, arrows step one frame, **Ripple Delete** (Shift+Delete) closes the gap while Clear leaves it. Lift and Extract keys differ between sources. | — | — | [B/C] (Adobe's own list is the CS6 default set) |

**Takeaways**
- Non-expert products converge on a small set of tools: **split (S), delete with automatic gap closing, drag-trim handles, transcript deletion, and silence detection with a review step.**
- I/O marks and J/K/L are what experts expect. Novice tools (Clipchamp, Canva) leave them out or hide them.
- Every serious product makes deletion **non-destructive and restorable** (Descript's restore removed media, Ignore).
- Silence or filler removal is always **review-first** (toggle each cut, adjust handles, then apply), never silent.

---

## 3. Research on text-based vs timeline editing

- **B-Script** (Huber, Shin, Russell, Wang, Mysore; CHI 2019; within-subject study with 110 novice crowd editors) [A]:
  - Users navigate by clicking the transcript, search online B-roll repositories, add clips with a button, and adjust each clip's position and length *on the transcript*.
  - The transcript interface was **easier and faster for inserting B-roll** than a timeline; a repeated-measures ANOVA found a significant effect of interface on editing time and workload.
  - **Recommendations** (where to insert, which keywords to search) produced **more engaging videos**.
  - Expert editors chose similar insertion points, anchored to keywords in the narration. This supports anchoring overlays to **words**.
  - Caveats: crowd workers, a 30-minute cap, and 70 of 180 participants dropped out.
- **Berthouzoz, Li & Agrawala, "Tools for placing cuts and transitions in interview video"** (SIGGRAPH 2012, DOI 10.1145/2185520.2185563) [A]:
  - Transcript-driven cutting, suggesting cut points where the speaker is silent and still.
  - Cuts can be shown openly (jump cut, fade) or hidden.
  - One-click removal of fillers and repeated words, and inserting pauses for emphasis.
  - The principle to take: **cut where there is low audio energy**.
- **LAVE** (IUI 2024; 8 participants, exploratory) [A, weak]: language descriptions of footage let an LLM agent help with editing; users wanted to switch between the agent and direct manipulation and refine what the agent did.
  - For Aadhi: a "suggest clips / suggest cuts" assistant should propose, and the teacher should confirm in the same UI.
- **"Rewriting Video"** (arXiv 2601.08565, 2026 preprint) [C]: experts gravitated to low-level edits, while novices were open to higher-level narrative changes. This supports offering both sentence-level cuts (novices) and frame-level trims (power users).
- **Guo, Kim & Rubin 2014** (L@S, 6.9M sessions) [A for "shorter is more engaging"; secondary sources disagree on what exactly the "6 minutes" figure means]. Relevant to soft limits on the length of inserted clips.

---

## 4. Editing primitives and conventions to adopt

- **Ripple delete everywhere on the spine.**
  - In Aadhi a "gap" means nothing: the picture is generated from the screenplay.
  - Deleting removes the content and the following content closes up, like Final Cut and CapCut's magnet.
  - "Lift" (leave a gap) is replaced by the existing **"Show for at least"** (`min_seconds`) and the beat `pause_after`.
- **Split:** keep the existing "Split here" at beat boundaries (a pure screenplay transform), and add **S** = split the selected *inserted clip* at the playhead (frame-snapped).
- **Trim:** drag the edge handles of an inserted clip or overlay, or use I/O.
- **Marks:** I/O set a range, Delete removes it (ripple), with a preview list when the range is longer than 10 s.
- **Shuttle:** J/K/L. True reverse playback is unreliable in browsers, so map J to "jump back" (step −5 s; repeated presses step further) [C, engineering judgement].
- **Snapping:** to word gaps (always on inside narration), beat and scene boundaries, the playhead, I/O marks and clip edges; threshold of a few pixels; hold Alt to suspend; N toggles it (CapCut convention).
- **Undo:** the existing labelled 200-step history (`changes.js`). Each user gesture is one step ("Cut 'so basically' in scene 3").
- **Non-destructive deletions:** removed words stay visible struck through when "Show removed" is on, and clicking one offers Restore (Descript model).
- **Review-first automation:** any automatic cut (silences in an inserted clip, "remove ums" in an uploaded talk, "tighten pauses") opens a review list with toggle and handles before **Apply** (Kapwing Smart Cut model).
- **Shortcuts must not fire in form controls.** Batch 4 already shipped a bug where Space stopped ticking checkboxes. The shortcut handler must skip `input`, `textarea`, `select`, `button`, `[contenteditable]`, `[role=checkbox]`, open dialogs and the media player's own controls.

---

## 5. Getting media in

### 5.1 Uploads
- The current limit is `upload_max_mb = 50`, and uploads accept mp4 and webm. Phone video at 1080p is roughly 100–200 MB per minute [C], so 50 MB is too small for clips.
  - Add **mov** (QuickTime `ftyp qt`, which iPhones produce) and optionally **mkv**.
  - Raise the video limit to about 1 GB through **resumable chunked upload**:
    - **tus 1.0.0** (a protocol dated 2016-03-25; resumes by HEAD to get the offset, then PATCH from it) [A].
    - `tus-js-client` is MIT [B]; vendor it to fit the CSP.
    - The core protocol plus the creation extension is small enough to implement in FastAPI directly. `tuspyserver` exists, but its licence and maturity are unverified [C].
- Validate in this order:
  1. Magic bytes.
  2. Then **ffprobe on the local file** (`-protocol_whitelist file`, as `compose` already does).
  3. Check: a video stream exists; duration ≤ 15 min; dimensions ≤ 4096; fps ≤ 120; no attachment streams.

### 5.2 Direct links (server fetch, SSRF-safe)
OWASP's guidance for fetching arbitrary URLs [A]:
- Allow only http/https; Aadhi should accept **https only**. Disable automatic redirects.
- Resolve A and AAAA records and reject non-public addresses (loopback, RFC1918, link-local including the metadata address 169.254.169.254, multicast, IPv6 ::1, fc00::/7, fe80::/10, ff00::/8).
- **Connect to the validated IP** while keeping the hostname for Host, SNI and certificate checks; validating DNS separately does not stop rebinding.
- Reject URLs that different parsers read differently, and add egress firewall rules.

Python ecosystem [C]:
- A 2026 advisory (CVE-2026-55391) shows the classic mistake: validating the resolved IP and then letting httpx resolve the name again.
- The robust pattern is an httpcore network backend that resolves inside `connect_tcp`, validates and connects to that same IP. `ssrfguard` and `httpx-secure` claim to do this but are unproven.
- Python's `is_private` misses CGNAT (100.64.0.0/10), so use `is_global` plus explicit checks.
- Smokescreen (Stripe's egress proxy) is the network-layer backstop.

The repo's `providers/_http.fetch_limited` already does https-only, manual redirects (≤ 3, each re-checked against an allow-list) and a streamed size cap. **It relies on a host allow-list, which arbitrary teacher URLs cannot use**, so it needs a pinned-IP connector for this case.

Platform pages:
- **YouTube:** the Terms forbid downloading except where YouTube allows it or with written permission [A], and the API policies forbid downloading, caching, storing, offline playback and separating audio from video (§III.E.1.a/b, §III.I.7; updated 2026-09-14) [A]. Detect `youtube.com`, `youtu.be`, `m.youtube.com` and `music.youtube.com` and refuse with guidance.
  - YouTube Studio → Content → ⋮ → Download gives creators MP4 copies of their *own* uploads at 720p or 360p [A].
- Kapwing does import YouTube links [B]. That is a legal risk Aadhi should not copy.
- Refuse other platform pages too (Instagram, TikTok, Facebook, X, Vimeo pages): they are HTML, not media, and governed by their own terms.

### 5.3 Stock libraries

| Source | API | Limits | Licence and attribution | Verdict |
|---|---|---|---|---|
| **Pexels** | `GET https://api.pexels.com/v1/videos/search` (the old `/videos/` path is deprecated); `orientation`; `size` large=4K / medium=Full HD / small=HD; `per_page` ≤ 80. `video_files[]` has `quality`, `width`, `height`, `fps` and `link`; `video_pictures[]` gives previews. | 200 requests/hour and 20,000/month by default; removed for free if the app is eligible. Rate headers come on 2xx responses only. Working around the limit gets the key terminated. | Licence: free, no attribution required for the content, editing allowed. No selling unaltered copies, no redistribution on stock or wallpaper platforms, no implied endorsement, no use as a trademark. **API rule:** show a prominent "Provided by Pexels" link and credit the creator ("Video by X on Pexels"). Don't replicate Pexels' core functionality. | **Integrate** [A] |
| **Pixabay** | `GET https://pixabay.com/api/videos/`. `videos.{large (4K, may be empty), medium (1080p), small (720p), tiny (540p)}` each with `url`, `width`, `height`, `size` and `thumbnail`. | 100 requests per 60 s per key; **cache requests for 24 h**. | Content licence: free, attribution optional, modification allowed; no standalone or unaltered distribution; trademark and recognisable-person limits. **API rules:** show where results come from whenever they are displayed. Videos may be embedded, but storing them on your own server is recommended. Images must not be permanently hotlinked. **No systematic mass downloads.** | **Integrate** [A] |
| **Coverr** | api.coverr.co, key per app. | Demo: 50 requests/hour. Production: 2,000 requests/hour, and only with a paid Pro/Ultimate plan. | The FAQ says no attribution is needed and commercial use is fine, but an older developers page says the Coverr logo is required. | **Defer** [B] |
| **Mixkit** | No public API found. | — | Free licence allows commercial use without attribution. The terms forbid making Mixkit or any item **available to any third party**, and reselling items in original form. | **Do not integrate**; teachers may upload clips they downloaded themselves [B] |
| Wikimedia Commons / NASA | — | — | Per-file licences; not researched (search budget). | Later |

Record every stock asset's provenance in `Asset.meta`: provider, provider id, creator name, creator URL, page URL, licence name, licence URL, and the fetch time. Credits then generate automatically (end card or description text, plus a "Credits" section in the share page).

### 5.4 Screen recordings
- Record with `getDisplayMedia` + `MediaRecorder`. Chrome and Firefox produce WebM (VP8/VP9 + Opus); Safari produces MP4.
- MediaRecorder WebM files usually have **no duration and no seek index**, which makes them slow to seek and unplayable in Safari [B, Remotion docs].
- MediaRecorder **drops frames under load, producing variable frame rate**; no standard option forces a constant rate yet (W3C issue #177) [B].
- Fix both on the server by re-encoding to the mezzanine with `fps=30`. `fix-webm-duration` (client) or a `-c copy -cues_to_front 1` remux only fixes seeking.
- The CSP already allows `media-src blob:`. Capture requires a secure context and a user gesture.

---

## 6. Ingest normalisation: mezzanine, proxy, thumbnails, waveforms

Why normalise: the browser and ffmpeg must agree on every frame, and inputs vary a lot.
- **HEVC:** iPhone default. Chrome plays it only where the platform's hardware decoder supports it (Chrome 107+ on Windows, macOS, Android and ChromeOS, 108+ on Linux), with user reports of failures [C].
- **HDR HLG/PQ** (`color_transfer` arib-std-b67 / smpte2084) needs tone mapping, or it looks washed out in SDR. Use zscale linearise → float → BT.709 primaries → `tonemap=hable:desat=0` → BT.709 TV range yuv420p. This needs zimg in the ffmpeg build.
  - **Only tone-map files that really are PQ or HLG.** Apple's HLG may need a manual check [C].
- **Variable frame rate** (screen recordings, phones), rotation metadata, odd sizes, 4:2:2/10-bit sources, mono or 5.1 audio.

Mezzanine (what the renderer reads), set by `CLIP_*` settings:
- H.264 High, yuv420p 8-bit, tagged BT.709 TV range (matching `RENDER_COLOR_BT709`).
- **CFR 30 fps** (= `render_fps`). Rotation applied. Long side ≤ 1920 (1080×1920 for vertical). Even dimensions.
- `-g 30 -keyint_min 30 -sc_threshold 0` (1 s GOP). CRF about 18, preset veryfast.
- AAC-LC 48 kHz stereo at 160 kbps; `+faststart`.
- Measure and store integrated loudness, true peak and LRA (ebur128 / `loudnorm` print), and a speech ratio (silencedetect).

Proxy (what the editor plays):
- 960×540 for 16:9, 540×960 for 9:16, CRF 28, **GOP 10 frames** (fast, precise seeks: short keyframe intervals make frame-accurate seeking much faster [B, Firefox developer, WHATWG]).
- AAC 96 kbps, `+faststart`.

Thumbnail sprite: one JPEG grid of 160×90 tiles (90×160 for vertical), at most 100 tiles per clip (1 per second for clips ≤ 100 s, otherwise spread evenly), plus a poster frame at 1 s.

Waveform peaks:
- Decode to mono s16 at 8 kHz with ffmpeg, then numpy min/max pairs at **100 peaks per second**, int8, stored as a small binary or JSON asset.
- Draw them on canvas in the existing timeline strip.
- **Avoid GPL or LGPL libraries in the web bundle:** BBC audiowaveform is GPL-3.0 and peaks.js is LGPL-3.0 [A]. wavesurfer.js is BSD-3-Clause [A] but not needed.
- For narration, reuse the existing 30-per-second loudness envelope (`SceneAudio.envelope`).

---

## 7. Clip audio vs narration vs background music

Modes per inserted clip:

| Mode | Picture | Clip sound default | Narration | BGM |
|---|---|---|---|---|
| **Pause narration and play the clip** (`clip` scene) | full frame | **Keep**, loudness-normalised to the narration target | silent (it is a separate scene) | fade out over 0.5 s, back in 1 s after |
| **Show over narration** (overlay / B-roll) | full frame or in the panel area | **Mute**; option "Quiet" = 20 dB under the narration | continues | normal ducking |
| **Replace the scene's picture** (existing library pick) | side panel or main media | mute | continues | normal |

Supporting facts:
- **WCAG 1.4.7 (AAA)**: background sound in speech-focused audio should be off, switchable off, or **at least 20 dB below the speech**, except brief 1–2 s sounds [A]. The existing BGM `sidechaincompress` should be checked against this, and "Quiet" clip sound should use it.
- **Loudness:**
  - AES TD1008 anchors speech at about **−18 LUFS** for streaming, with music 2–3 LU higher, because listeners hear speech at the same measured level as louder [B].
  - Podcast and online-video practice is −16 LUFS with a −1 dBTP ceiling [C].
  - YouTube's −14 LUFS is widely cited but **not in YouTube's own help**. Stable Volume is a mobile playback feature that leaves music alone, and the Stats for Nerds readouts suggest YouTube turns loud uploads down and does not turn quiet ones up [C].
  - Recommendation: master everything to **−16 LUFS integrated, ≤ −1.5 dBTP** (headroom for AAC), and normalise kept clip sound to the same target with at most +12 dB of gain, so noise is not pumped up.
- **Clicks at splices:** practitioners use 1–5 ms crossfades for speech and start around 10 ms for general material; zero-crossing alone is not enough. Keep fades shorter than one frame (33 ms at 30 fps) [C].
  - Aadhi: narration splices at **10 ms equal-power** fades, placed in silence. Clip in and out points get 20 ms `afade`s.
  - `acrossfade` overlaps by default (`o=1`) and shortens the result by the fade length. To keep the timing exact, use per-side `afade` with silence padding instead [B].

---

## 8. Frame-accurate cutting with ffmpeg

- `-ss` **before** `-i` (input seeking) jumps by keyframe and is fast. Since **FFmpeg 2.1 it is also frame-accurate when transcoding** [A].
- **With `-c copy` it is not frame-accurate:**
  - The cut starts at a keyframe, often a few frames early, or begins with audio and no picture.
  - Reusing a stream-copied segment with the concat demuxer needs `-avoid_negative_ts`; `-copyts` overrides it [A/B].
  - libx264's default GOP of 250 means a keyframe only every ~10 s at 25 fps [A].
- `-ss` before `-i` resets timestamps, so `-to` is counted from the seek point; prefer `-t` or a frame count [A].
- MPEG-TS/PS inputs seek inaccurately (ticket #5093) [A]. This is one more reason to normalise everything to MP4 first.

Recipe for an inserted clip segment (deterministic):
```
ffmpeg -protocol_whitelist file,pipe -ss {in_frame/30:.6f} -i mezz.mp4 \
  -frames:v {out_frame-in_frame} -filter_complex "[0:v]setpts=PTS-STARTPTS,<fit/pad/blur-fill>[v];
  [0:a]atrim=0:{dur},asetpts=PTS-STARTPTS,afade=t=in:d=0.02,afade=t=out:st={dur-0.02}:d=0.02,
  volume={gain_db}dB[a]" ... (re-encode with the segment encoder settings)
```
The mezzanine is already constant 30 fps on the render grid, so frame indices map exactly and no `fps` filter is needed here.

---

## 9. What runs in the browser

- **Seeking:** setting `currentTime` asks for a precise seek, though the spec lets browsers approximate for speed. `fastSeek()` is keyframe-only and not Baseline; `seekToNextFrame()` is non-standard and deprecated [A/B].
  - To show frame *n*: set `currentTime = (n + 0.5)/fps`, wait for `seeked`, then confirm with `requestVideoFrameCallback` metadata `mediaTime` (Baseline since October 2024) [A].
- **WebCodecs** only encodes and decodes; containers need a library such as Mediabunny, which is MPL-2.0, can be used in closed-source products, and supports `trim`, `fit` and `frameRate` [A/B]. Mediabunny's optional MP3 and AAC encoders bring LGPL code [B].
  - **Not needed:** Aadhi's server renders, and the editor only previews.
- **ffmpeg.wasm**: the repo is MIT, but the bundled FFmpeg's licence depends on the build; it is "experimental" [B]. Not needed.
- **Remotion**: free only for individuals, non-profits and companies with ≤ 3 employees. Otherwise paid: Creators $25/seat/month, Automators $0.01 per render with a $100/month minimum [A]. Not needed: the renderer already follows the same "screenshot the player" idea.
- Conclusion: the live player gains two element types:
  - A **clip scene**: its `<video>` is the master clock while it plays, as narration `<audio>` is now.
  - An **overlay** `<video>`: muted, synchronised to the scene clock, reusing the side-panel video's `videoTimeAt = t - show_at` logic.
  - Both play **proxies**; the renderer reads **mezzanines**.

---

## 10. The edit layer: anchored edits compiled into the timeline

### 10.1 Why not a separate absolute-time EDL (CMX3600 / OTIO style)
OTIO models a timeline as stacks of tracks of clips, gaps and transitions, where each clip has a `source_range` inside its media's `available_range`, using rational time [A]. That is the right model for *media* (inserted clips) and for export. It is the wrong model for *generated narration*, because Aadhi's times are outputs of the build, not inputs:
- A different voice per video changes every word timing.
- Regenerating a scene or editing a beat re-runs TTS.
- Translation changes all the words.
- `hidden` and `min_seconds` move every later scene.
- Every absolute cut would then drift onto the wrong words.

The fix is **Final Cut's model**: a **primary storyline** (scenes → beats → words) with **connected clips** anchored to it [A].

### 10.2 Data model (Pydantic sketch, in `schemas/screenplay.py`)
Follow the repo's pattern: each new field is **left out of the JSON at its default**, so existing lectures hash, cache and render exactly as before.

```python
class WordRange(StrictModel):            # a cut inside one beat's narration
    id: str                              # "{beat_id}-x{n}"
    start: int = Field(ge=0)             # index into the beat's spoken tokens (TimedBeat.words order)
    end: int                             # exclusive; start < end <= len(words)
    text: str = Field(max_length=400)    # the removed words, for display + re-anchoring + stale check
    mode: Literal["splice", "revoice"] = "splice"   # splice = cut audio (free); revoice = re-synthesise beat without them

class ClipRange(StrictModel):            # media time, frame-quantised to the mezzanine (30 fps)
    asset_key: str                       # authorised via AssetRef like every key
    in_frame: int = Field(ge=0)
    out_frame: int                       # exclusive; out_frame - in_frame >= 15 (0.5 s)

class ClipLook(StrictModel):
    fit: Literal["contain_blur", "contain", "cover"] = "contain_blur"
    focus_x: float = Field(0.5, ge=0, le=1)   # cover: which part stays in frame (per aspect)
    focus_y: float = Field(0.5, ge=0, le=1)

class Overlay(StrictModel):              # B-roll connected to a word
    id: str
    at_word: int = 0                     # starts when this word of the beat starts
    at_text: str = ""                    # anchor word text (re-anchoring)
    clip: ClipRange
    region: Literal["full", "panel"] = "full"
    sound: Literal["mute", "quiet"] = "mute"     # quiet = 20 dB under narration
    look: ClipLook = ClipLook()
    freeze_last: bool = False            # if the clip is shorter than wanted

# Beat gains:  cuts: list[WordRange] = []   overlays: list[Overlay] = []   (both omitted when empty)

class ClipScene(SceneBase):              # "Pause narration and play the clip"
    type: Literal["clip"]
    clip: ClipRange
    sound: Literal["keep", "mute"] = "keep"
    gain_db: float = Field(0, ge=-30, le=12)
    look: dict[Literal["16:9", "9:16"], ClipLook] = {}   # per output aspect
    captions: list[CaptionCue] = []      # optional, in CLIP SOURCE seconds; mapped through the range
    credit: str | None = None            # "Video by X on Pexels"; auto from Asset.meta
    narration_intro: str | None = None   # optional one-line TTS lead-in ("Watch this experiment.")
```

Why it is shaped this way:
- **Inserting between scenes** is just a `ClipScene` in the scene list. It inherits reorder (dragging on the existing strip), hide, `min_seconds`, chapters, "Generated vs edited", revert, merge and undo.
- **Inserting in the middle of a scene** reuses the existing pure "Split here", then puts the clip scene between the two halves. This is one undo step: "Insert clip after 'resistance'".
- **Cuts and overlays live on the beat**, so they move with the beat when scenes are split, merged or reordered. Deleting a beat deletes the overlays attached to it, as a connected clip goes with its storyline clip. The UI must say so: "also removes 1 clip shown over these words".

### 10.3 Compilation (deterministic, in the existing build stages)
1. **Assets: narration splice** (`pipeline/audio.build_scene_audio`). For each beat with `cuts`, after its PCM is decoded and trimmed:
   - Map every word range to sample positions.
   - Place each cut boundary at the **lowest-energy 10 ms window** inside the gap between words: between word k−1's end and word k's start, widened by ±60 ms.
   - Remove the span. Replace the joined gap with a pause of `clamp(max(gap_before, gap_after), 0.12, 0.35)` s, so the speech does not sound rushed.
   - Apply 10 ms fades on each side and shift the remaining word timings.
   - The scene narration's asset key gains a digest of the cuts, **only when cuts exist**. Beat TTS keys are unchanged, so a cut costs no TTS.
   - **Estimated word timings:** the OpenAI and Gemini TTS adapters return no word timings (`supports_word_timings = False`, `words_estimated`). There, a cut is allowed only where a real silence exists (≥ 40 ms below −40 dBFS within ±200 ms of the estimated boundary). Otherwise the editor offers **"Re-voice this sentence without these words"** (`mode="revoice"`: that beat alone is synthesised again, cached). This is Descript's "Avoid harsh cuts" applied to Aadhi.
2. **Timeline** (`compose/timeline.build_timeline`):
   - `ClipScene` → `TimedScene(type="clip", media=MediaRef(kind="video", source_in, source_out, …), duration = frames/30 [+ intro line])`.
   - Overlays → `TimedScene.overlays: list[TimedOverlay(start, end, media, region, sound)]` with `start = word.start`. The end is capped at the scene end minus the transition; past that, freeze the last frame or trim, with an editor warning.
   - Captions, chapters and sync cues are rebuilt from the remaining words as today, so **captions stay consistent by construction**.
   - Clip-scene captions: map each cue from source time `[s, e]` to `[max(s, in) − in, min(e, out) − in]` plus the scene start, and drop cues shorter than 0.3 s.
3. **Render** (`compose/video`):
   - A clip scene is one ffmpeg segment from the mezzanine (§8). It needs no Playwright states, unless a credit or lower-third PNG is shown for its first 3 s.
   - Overlays are `MediaLayer`s with `visible_from` / `visible_until`.
   - The audio mixer adds the clip sound, and BGM fades out over clip-scene ranges.
   - Segment caching in `RenderWorkspace` means inserting one clip re-encodes only the affected segments.
4. **Player:** the same `TimedScene` fields feed `schedule.js`, so preview and MP4 stay identical; `evals/render_parity.py` should gain clip cases.

### 10.4 Robustness rules
- **Re-anchoring.** On any change to a beat's narration (teacher edit, regeneration, repair):
  - Each `WordRange` checks that the tokens at `[start, end)` still equal `text`.
  - If not, it searches the beat for that exact token sequence: a unique match is moved there; anything else marks the cut **stale**.
  - A stale cut is **not applied** and gets a warning issue `edit.cut_stale` with "Find again / Remove". Never cut words the teacher did not choose.
  - Overlays use `at_text` the same way and fall back to the beat start with an info note.
- **Translation:** narration cuts are tied to the language. `translate` drops them and lists the dropped cuts in the result notice. Clip scenes and overlays carry over, because beat ids are preserved.
- **Voice change:** word indices refer to spoken tokens, which do not depend on the voice, so the cuts re-apply to the new timings automatically.
- **Generated vs edited and revert:** a clip scene shows as `added`; beat cuts and overlays show as "edited" fields of the beat.
- **Validation (lint):**
  - `edit.cut_stale` (warning).
  - `clip.too_long_for_format` (info): a single insert over 60 s in a 9:16 reel, or over 120 s in a 16:9 video.
  - `clip.share_high` (info): inserted footage over 40% of the runtime.
  - `clip.sound_no_captions` (info): a kept clip with speech but no captions.
  - `clip.source_missing` (error).
  - `overlay.truncated` (info).

### 10.5 The "cut a time range" tool resolves into anchored operations
When the teacher marks I/O on the timeline (or drags across the waveform) and presses Delete:
1. Split the range by scene.
2. A **whole scene** inside the range → `hidden = true`.
3. A **whole beat** inside the range → remove the beat. If it reveals a board item, ask whether to remove the item or move its reveal to the next beat.
4. A **partial beat** → a `WordRange` over the words whose midpoints fall inside the range (snapped to word gaps).
5. **Pause-only** parts → reduce that beat's `pause_after`.
6. Inside a **clip scene** → a range at the start or end trims `in_frame` / `out_frame`; a range in the middle splits the clip scene in two (same asset, two ranges).
7. Inside an **overlay** → trim it.

Show a summary ("Removes: 2 sentences in scene 3 · 4.2 s of 'Lab demo' · a 1.1 s pause") and apply it as **one undo step**.

### 10.6 Export (optional, later)
"Export for Premiere/Resolve": the rendered per-scene segments plus an **OTIO** timeline (OpenTimelineIO: Apache-2.0, ASWF, official C++ and Python; no official JavaScript binding found [B]).

---

## 11. Recommended UX

**Layout.** The editor keeps its scene list, inspector and preview, and gains a **"Script" tab**: the whole lecture as running text, grouped by scene and beat.
- Words highlight as they play; click a word to seek.
- Select words → floating toolbar: **Cut** · **Insert clip here** · **Show a clip over this** · **Re-voice**.
- Removed words appear struck through when "Show removed" is on; click one to Restore.

The timeline strip gains:
- A **Clips lane** (clip scenes and overlays with thumbnails from the sprite).
- A **waveform** on the narration lane (from the envelope) and on clip blocks (from peaks).
- I/O marks on the ruler.
- Draggable trim handles on clip blocks. Dragging a whole block moves it, as scene blocks move now.

**Insert-clip flow** (B-Script pattern):
1. Put the cursor on a word in Script, or the playhead on the timeline, and choose **Insert clip**.
2. Choose a source: tabs **Upload · My library · Free stock (Pexels, Pixabay) · Link to a video file · Record my screen**.
   - The stock search is **pre-filled with keywords from the selected sentence** (B-Script found such recommendations raise engagement).
3. Choose how it plays: **"Pause and play the clip"** or **"Show over my narration"**.
4. **Trim dialog:**
   - Filmstrip and waveform, with play-range and loop buttons.
   - I/O keys, ←/→ step one frame, Shift+←/→ step 1 s.
   - In and out time fields as m:ss.ff.
   - The default range is the whole clip, capped at 30 s for overlays.
   - A live preview in both aspects if the project has both.
5. **Sound:**
   - Keep / Mute for "pause and play".
   - Mute / Quiet for overlays.
   - A volume slider (−30 to +12 dB) with a loudness meter showing "matches narration".
6. **Fit** (only shown when the clip's aspect differs from the output):
   - Blurred background (default), Fit, or Fill, with a focus point dragged on the thumbnail.
   - The focus point is stored per output aspect.
7. **Rights checkbox** for uploads and links: "I have the right to use this clip". Stock credit is added automatically.

**Cut flow:**
- Select words, then Delete. A mid-sentence selection shows a hint: "Cutting inside a sentence can sound abrupt. Re-voice instead?"
- Double-click selects a sentence.
- **Tighten pauses** (Descript's "Shorten word gaps" plus Kapwing's review): pauses longer than 0.8 s are listed, each shortened to 0.35 s, with toggle, preview and Apply.

**Silence and ums in an uploaded clip that keeps its sound:** run a silencedetect review with Smart-Cut style toggles and handles; Apply splits the clip scene into ranges.

**Feedback:**
- Every long job (import, normalise, stock download) shows progress in the existing job UI.
- The clip block shows "Preparing…" with its poster until the proxy is ready; it stays playable as an estimate.

---

## 12. Risks and open questions

- **Prosody after a mid-sentence splice** can sound unnatural even with a clean seam. That is why the editor defaults to sentence-level cuts and offers Re-voice.
- **Storage:** mezzanine plus proxy is about 1.3× the source. Unused clips need garbage collection (the library already keeps assets in use).
- **Cost and time:** normalising a 5-minute 1080p clip takes about 1–2 minutes of CPU with veryfast [C]. Use a dedicated worker kind (`normalize_clip`) so it does not starve renders.
- **Third-party content:** stock attribution must survive into share pages and descriptions. YouTube/platform refusals must be friendly and explain the legal alternative.
- **Unverified items:** see the Limitation note at the top.

---

## DESIGN IMPLICATIONS FOR AADHI

**Editing model**
1. Store all edits **anchored to content, never to absolute output seconds**:
   - Narration cuts are `Beat.cuts: list[WordRange{start, end, text, mode}]`.
   - B-roll is `Beat.overlays: list[Overlay{at_word, at_text, clip, region, sound, look}]`.
   - Inserts are a new scene type `ClipScene(type="clip")`.
   - All new fields are left out of the JSON at their defaults, so existing hashes and caches are unchanged.
2. All deletes on the narration spine are **ripple**; no "lift" and no gap clips. `min_seconds` and `pause_after` are the only ways to hold time.
3. A timeline range cut is resolved into, in order: hide whole scenes → remove whole beats → word ranges → shorten `pause_after` → trim or split clip scenes → trim overlays. Show a one-line summary and apply it as **1 undo step**.
4. Deleting a beat removes its overlays (connected-clip behaviour), with a confirmation that names how many.
5. Re-anchor on every narration change:
   - Exact token check, then a unique exact-sequence search, otherwise mark **stale** (`edit.cut_stale`, warning) and **do not apply** the cut.
   - Overlays fall back to the beat start (info).
6. `translate` drops narration cuts and reports the count. It keeps clip scenes and overlays.

**Narration splicing** (in `build_scene_audio`, 24 kHz PCM)
7. Cut boundary = the minimum-RMS 10 ms window within [prev_word.end − 0.06 s, next_word.start + 0.06 s].
8. The pause at the join = clamp(max(gap_before, gap_after), **0.12 s, 0.35 s**). Use **10 ms** equal-power fades on both sides of each splice.
9. With estimated word timings (OpenAI and Gemini TTS: `words_estimated=True`):
   - Allow a splice only if a silence of **≥ 40 ms below −40 dBFS** exists within **±200 ms** of the boundary.
   - Otherwise offer `mode="revoice"`: synthesise that beat again without the words. Cost: 1 beat of TTS, cached by text.
10. The scene-narration key includes a digest of the cuts only when cuts exist. Beat TTS keys are never affected.
11. Default selection is the sentence (double-click). A mid-sentence cut shows a "may sound abrupt, Re-voice?" hint.

**Inserted clips**
12. Accept **mp4, webm, mov** (+ mkv), uploads **≤ 1024 MB** (`CLIP_UPLOAD_MAX_MB`), source duration **≤ 15 min**, dimensions ≤ 4096, fps ≤ 120.
    - Use resumable **tus 1.0** uploads in **8 MB** chunks: implement the core protocol plus the creation extension in FastAPI; vendor `tus-js-client` (MIT).
    - Keep the 50 MB limit for images.
13. Normalise to a **mezzanine**:
    - H.264 High yuv420p, BT.709 TV range, **CFR 30 fps**, long side ≤ 1920.
    - `-g 30 -keyint_min 30 -sc_threshold 0`, CRF 18, preset veryfast.
    - AAC-LC 48 kHz stereo 160 kbps, `+faststart`.
    - Rotation applied; HLG/PQ tone-mapped with zscale + `tonemap=hable:desat=0`, only when `color_transfer` is arib-std-b67 or smpte2084.
14. **Proxy:** 960×540 (540×960 vertical), CRF 28, **GOP 10**, AAC 96 kbps.
    - **Sprite:** ≤ 100 tiles of 160×90 (1 per second up to 100 s, then spread evenly), JPEG quality about 70, target ≤ 200 KB.
    - **Poster** at 1 s.
    - **Peaks:** 100 per second, int8 min/max mono.
    - Store integrated LUFS, true peak, LRA and a speech ratio in `Asset.meta`.
    - Run all of this in a separate job kind `normalize_clip` with timeout = 3 × duration + 60 s.
15. Clip ranges are **frame indices on the 30 fps grid**. Minimum lengths: **15 frames (0.5 s)** for a clip scene, **30 frames (1 s)** for an overlay.
16. Defaults: overlay length ≤ **30 s**; a soft warning for a single insert over **60 s in 9:16 reels** or **120 s in 16:9**, and for inserted footage over **40% of the runtime**.
17. Fit defaults when the aspects differ: **`contain_blur`**, a background copy scaled to cover with `boxblur=20:2` and brightness −0.15 behind the contained clip. `cover` uses a per-aspect focus point, default (0.5, 0.5).
18. A clip scene that keeps its sound and contains speech without captions raises `clip.sound_no_captions` (info). Teacher captions are mapped through the range; cues shorter than **0.3 s** after clipping are dropped.

**Audio**
19. Master target **−16 LUFS integrated, ≤ −1.5 dBTP**. Normalise kept clip sound to the same target with gain clamped to **[−30, +12] dB**.
20. Overlay sound "Quiet" = **20 dB below the narration** (WCAG 1.4.7). BGM stays ≥ 20 dB under speech; verify the existing `sidechaincompress` settings against this.
21. BGM **fades out over 0.5 s** at the start of a clip scene that keeps its sound and **back in over 1.0 s** after it. Clip in and out points get **20 ms** fades.

**Render and preview**
22. Never use `-c copy` for an edited segment. Use `-ss` before `-i` on the mezzanine plus `-frames:v N`, re-encode, `setpts=PTS-STARTPTS`, `asetpts`. Always `-protocol_whitelist file,pipe` on local files.
23. A clip scene is a single ffmpeg segment (no Playwright states, unless the credit or lower-third PNG appears for its first **3 s**). Overlays are `MediaLayer`s with visible_from / visible_until. Add clip and overlay cases to `evals/render_parity.py`.
24. Player:
    - A clip scene's `<video>` is the master clock.
    - Overlays are muted `<video>`s synchronised to the scene clock: re-seek when drift exceeds **0.1 s**.
    - Show frame *n* by seeking to (n + 0.5)/30 and confirming with `requestVideoFrameCallback`.
    - Proxies in the editor, mezzanines in the render.
25. No in-browser encoding stack (WebCodecs, Mediabunny, ffmpeg.wasm, Remotion). No GPL or LGPL waveform libraries; draw peaks on canvas in `timelineStrip.js`.

**Import sources**
26. **Direct links:**
    - https on port 443 only, run in a worker job, ≤ **3 redirects**, each re-resolved and re-validated.
    - Resolve once and **connect to the pinned IP** (httpcore backend), keeping SNI and Host.
    - Reject any address that is not `ip.is_global`, plus IPv4 addresses embedded in IPv4-mapped, 6to4 and NAT64 IPv6 addresses.
    - Connect timeout **10 s**, total **300 s**, the same size cap as uploads.
    - Accept only `video/*` or `application/octet-stream`, then check magic bytes and ffprobe.
    - **20 imports per user per hour.** Production uses an egress proxy (for example Smokescreen) that allows only 443.
27. **Refuse** YouTube (`youtube.com`, `youtu.be`, `m.`, `music.`) and other platform page URLs. Message: "YouTube doesn't allow downloads. For your own video, use YouTube Studio → Content → ⋮ → Download, then upload the file."
28. **Stock: Pexels + Pixabay only**, through a server-side proxy that keeps the keys secret.
    - Cache search responses for **24 h** (Pixabay requires it).
    - Keep within **200/h and 20k/month** (Pexels) and **100 per 60 s** (Pixabay) per server key; back off on 429 and read the rate headers.
    - Download **only the chosen file**, preferring the ≤ 1080p rendition (Pexels `quality: "hd"`, Pixabay `medium`).
    - Show "Provided by Pexels/Pixabay" in the picker. Store provider, id, creator, URLs and licence in `Asset.meta`, and auto-credit "Video by X on Pexels" in the credits and share page.
    - No Mixkit integration (its terms forbid making items available to third parties). Coverr is deferred.
29. **Screen recording:**
    - Try `video/webm;codecs=vp9,opus`, then `vp8,opus`, then `video/mp4`.
    - **≤ 10 min**, 2 s `timeslice`, upload through tus, then `normalize_clip` (fixes variable frame rate and missing duration).

**UX and keyboard**
30. Add a **Script view** (transcript of the beats) with a selection toolbar: Cut · Insert clip here · Show a clip over this · Re-voice. Include a **"Show removed"** toggle with struck-through words and Restore.
31. The insert dialog follows a fixed order: source → how it plays (pause vs over) → trim → sound → fit → rights. Pre-fill the stock search with **keywords from the selected sentence**.
32. Shortcuts:

    | Key | Action |
    |---|---|
    | Space | play / pause |
    | K | pause |
    | L | play; repeated presses give 1× → 1.5× → 2× |
    | J | back 5 s; repeated presses back 10 s, then 20 s |
    | I / O | set in / out |
    | Alt+X | clear marks |
    | Shift+I / Shift+O | go to the in / out mark |
    | ← / → | previous / next word, or ±1 frame when a clip is selected |
    | Shift+← / Shift+→ | ±1 s |
    | S | split the selected clip |
    | Delete | ripple-delete the selection or marked range; a preview list appears if longer than 10 s |
    | Ctrl+Z / Ctrl+Shift+Z | undo / redo |
    | Ctrl+= / Ctrl+− / Ctrl+0 | zoom in / zoom out / fit |
    | N | toggle snapping |
    | Alt (held) | suspend snapping |
    | ? | help |

    **None of them fire** in `input`, `textarea`, `select`, `button`, `[contenteditable]`, a checkbox or radio, an open dialog, or native media controls.
33. **Snapping:** **8 px** threshold to word gaps (always on in narration), beat and scene edges, the playhead, marks and clip edges.
34. **Automatic cuts are review-first:**
    - "Tighten pauses": gaps **> 0.8 s → 0.35 s**.
    - Clip silence detection: **−35 dB for ≥ 0.6 s** by default, with a sensitivity slider.
    - Each item can be toggled and has handles; **Apply** creates one undo step.
35. **Lint codes to add:** `edit.cut_stale` (warning), `clip.source_missing` (error), `clip.too_long_for_format`, `clip.share_high`, `clip.sound_no_captions`, `overlay.truncated` (info). None of them start a paid rewrite.

---

## Sources

- Descript help:
  - [Edit like a doc](https://help.descript.com/getting-started/edit-like-a-doc)
  - [Delete vs ignore](https://help.descript.com/script-editing/delete-vs-ignore)
  - [Shorten word gaps](https://help.descript.com/script-editing/shorten-word-gaps)
  - [Filler words](https://help.descript.com/script-editing/filler-words)
  - [Smooth jump cuts](https://help.descript.com/script-editing/smooth-jump-cuts)
  - [Add layer](https://help.descript.com/visuals/add-layer.md)
- Kapwing:
  - [Smart Cut & Find Scenes](https://www.kapwing.com/help/how-to-use-smart-cut/)
  - [Trim with Transcript](https://www.kapwing.com/resources/trim-with-transcript/)
  - [Upload / URL import](https://kapwing.com/help/upload)
- Microsoft Clipchamp:
  - [Keyboard shortcuts](https://support.microsoft.com/en-us/clipchamp/keyboard-shortcuts-for-clipchamp)
  - [Remove gaps](https://support.microsoft.com/en-us/Clipchamp/how-to-remove-gaps-on-the-timeline)
- [WeVideo keyboard shortcuts](https://support.wevideo.com/hc/en-us/articles/215518747) (snippet only; page returned 403)
- [VEED: adjust or mute audio](https://support.veed.io/en/articles/6982740-how-to-adjust-or-mute-the-audio)
- [Canva video splitter](https://www.canva.com/features/video-splitter/)
- CapCut (secondary): [forum on linkage and auto snap](https://www.capeditcut.com/community/capcut/overlays-on-the-second-track-no-longer-move-with-the-main-clip-auto-snap-enabled/)
- [Apple Final Cut Pro User Guide: magnetic timeline, connected clips](https://support.apple.com/guide/final-cut-pro/verb8fcfc133/mac)
- [Adobe Premiere Pro default keyboard shortcuts](https://helpx.adobe.com/au/premiere-pro/using/default-keyboard-shortcuts.chromeless.html)
- Papers:
  - [Huber et al., B-Script, CHI 2019](https://arxiv.org/abs/1902.11216)
  - [Berthouzoz, Li & Agrawala, SIGGRAPH 2012](https://history.siggraph.org/?p=110546)
  - [LAVE, IUI 2024](https://arxiv.org/abs/2402.10294v1)
  - [Rewriting Video, 2026 preprint](https://arxiv.org/pdf/2601.08565)
  - [Guo, Kim & Rubin, L@S 2014](https://up.csail.mit.edu/other-pubs/las2014-pguo-engagement.pdf)
- Pexels:
  - [API documentation](https://www.pexels.com/api/documentation/)
  - [Licence](https://www.pexels.com/license/)
- Pixabay:
  - [API docs](https://pixabay.com/api/docs/)
  - [Licence summary](https://pixabay.com/service/license-summary/)
- Coverr:
  - [API start](https://api.coverr.co/docs/start)
  - [Developers page](https://coverr.co/es/developers)
- Mixkit:
  - [Licence index](https://mixkit.co/license/)
  - [Official info page](https://mixkit.co/llm-info/)
- YouTube:
  - [Terms of Service](https://www.youtube.com/static?template=terms)
  - [API Services Developer Policies](https://developers.google.com/youtube/terms/developer-policies)
  - [Download your own videos](https://support.google.com/youtube/answer/56100)
  - [Stable volume](https://support.google.com/youtube/answer/14106294)
- SSRF:
  - [OWASP SSRF Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html)
  - [ssrfguard](https://pypi.org/project/ssrfguard/)
  - [httpx-secure](https://pypi.org/p/httpx-secure)
  - [CVE-2026-55391 advisory](https://corgea.com/advisories/vulnerabilities/CVE-2026-55391)
- FFmpeg:
  - [Seeking wiki](https://trac.ffmpeg.org/wiki/Seeking)
  - [Ticket #5093](https://trac.ffmpeg.org/ticket/5093)
  - [acrossfade patch thread](https://ffmpeg.org/pipermail/ffmpeg-devel/2015-July/176173.html)
- Browser media (MDN):
  - [requestVideoFrameCallback](https://developer.mozilla.org/en-US/docs/Web/API/HTMLVideoElement/requestVideoFrameCallback)
  - [fastSeek](https://developer.mozilla.org/en-US/docs/Web/API/HTMLMediaElement/fastSeek)
  - [seekToNextFrame](https://developer.mozilla.org/docs/Web/API/HTMLMediaElement/seekToNextFrame)
  - [WebCodecs API](https://developer.mozilla.org/en-US/docs/Web/API/WebCodecs_API)
- [WHATWG frame-accuracy thread, 2011](https://lists.w3.org/Archives/Public/public-whatwg-archive/2011Jan/0442.html)
- Mediabunny:
  - [Site](https://mediabunny.dev/)
  - [Converting media files](https://mediabunny.dev/guide/converting-media-files)
- [ffmpeg.wasm](https://github.com/ffmpegwasm/ffmpeg.wasm)
- [Remotion licence and pricing](https://www.remotion.dev/docs/license)
- Waveform libraries:
  - [BBC audiowaveform](https://github.com/bbc/audiowaveform)
  - [peaks.js](https://github.com/bbc/peaks.js)
  - [wavesurfer.js](https://github.com/katspaugh/wavesurfer.js)
- MediaRecorder:
  - [Remotion: fix MediaRecorder video](https://remotion.dev/docs/webcodecs/fix-mediarecorder-video)
  - [fix-webm-duration](https://github.com/yusitnikov/fix-webm-duration)
  - [W3C mediacapture-record #177](https://github.com/w3c/mediacapture-record/issues/177)
- HEVC in Chrome:
  - [Community guide](https://fr.github.com/StaZhu/enable-chromium-hevc-hardware-decoding)
  - [Streaming Learning Center](https://streaminglearningcenter.com/?p=17448)
- HDR tone mapping (community): [video-use write-up](https://instagit.com/browser-use/video-use/how-hdr-to-sdr-tone-mapping-works/)
- OpenTimelineIO:
  - [Timeline structure](https://opentimelineio.readthedocs.io/en/latest/tutorials/otio-timeline-structure.html)
  - [Repository](https://github.com/AcademySoftwareFoundation/OpenTimelineIO)
- tus:
  - [Protocol](https://www.tus.io/protocols/resumable-upload)
  - [tuspyserver](https://pypi.org/project/tuspyserver)
- Accessibility and loudness:
  - [WCAG 2.2 SC 1.4.7](https://www.w3.org/WAI/WCAG22/Understanding/low-or-no-background-audio.html)
  - [AES TD1008 summary (Radio World)](https://www.radioworld.com/?p=115138)
  - [Production Expert on TD1008](https://www.production-expert.com/production-expert-1/aes-update-loudness-recommendations-for-audio-only-streaming)
  - [Audio normalisation (Wikipedia)](https://en.wikipedia.org/wiki/Audio_normalization)
  - [Music under narration levels (VidPros)](https://vidpros.com/fix-background-music-too-loud-video/)
- Splice crossfades (practitioners):
  - [Sound On Sound](https://www.soundonsound.com/techniques/removing-clicks-crossfades-sonar)
  - [Ardour forum](https://discourse.ardour.org/t/cut-on-zero-crossings/79932)
  - [Podcast Engineering School](https://podcastengineeringschool.com/crossfading-when-editing-audio/)
