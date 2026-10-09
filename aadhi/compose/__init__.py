"""Compose: timeline building, URL resolution, captions, chapters and the MP4 renderer.

Modules (import them directly; this package import stays cheap):

* ``timeline``   build_timeline / preview_timeline / resolve_urls / default_branding
* ``captions``   split_caption / to_srt (+ libass-safe burn-in variant) / to_vtt
* ``chapters``   youtube_chapters / to_ffmetadata
* ``sounds``     deterministic tick + ding effects stored as assets
* ``ffmpeg``     async runner, probing, version gate, filter-graph builders
* ``frames``     state PNG normalisation, the transparent blank frame, premultiplied cross-fade frames
* ``aio``        deadlines with cancel polling, fail-fast task groups
* ``screenshot`` Playwright render-mode screenshots
* ``video``      the ``render_video`` job handler
"""
