"""Compose: timeline building, URL resolution, captions, chapters and the MP4 renderer.

Modules (import them directly; this package import stays cheap):

* ``base``       stage and output sizes (stage_size / timeline_stage / output_size), branding and timing constants
* ``timeline``   build_timeline / preview_timeline / resolve_urls / default_branding
* ``captions``   split_caption (line ``width``) / to_srt (+ libass-safe burn-in variant) / to_vtt
* ``chapters``   youtube_chapters / to_ffmetadata
* ``sounds``     deterministic tick + ding effects stored as assets
* ``ffmpeg``     async runner, probing, version gate, filter-graph builders
* ``loudness``   EBU R128: ebur128 measurements, two-pass loudnorm JSON helpers, static gain, limiter (Dynamic audio)
* ``qa``         post-render checks of the MP4 (frames, rate, colour, audibility, aspect-aware black edges)
* ``frames``     state PNG normalisation, the transparent blank frame, premultiplied cross-fade frames
* ``aio``        deadlines with cancel polling, fail-fast task groups
* ``screenshot`` Playwright render-mode screenshots
* ``video``      the ``render_video`` job handler
"""
