"""Fake aadhi.compose.timeline: estimated timings, URL resolution from asset keys."""

from __future__ import annotations

from aadhi.schemas.timeline import IntroSpec, TimedBeat, TimedScene, Timeline

WORDS_PER_SECOND = 2.5


def _build(screenplay, *, version_id=None, revision=None, include_intro=False, estimated=True) -> Timeline:
    intro = IntroSpec(duration=2.0) if include_intro else None
    t = intro.duration if intro else 0.0
    scenes = []
    for i, scene in enumerate(screenplay.scenes):
        beats = []
        cursor = 0.5
        for j, beat in enumerate(scene.all_beats()):
            dur = max(1.0, len(beat.narration.split()) / WORDS_PER_SECOND)
            beats.append(
                TimedBeat(
                    beat_id=beat.id,
                    index=j,
                    start=cursor,
                    speech_end=cursor + dur,
                    end=cursor + dur,
                    narration=beat.narration,
                    estimated=estimated,
                )
            )
            cursor += dur + 0.15
        duration = max(cursor + 0.8, 4.0)
        scenes.append(
            TimedScene(
                scene_id=scene.id, index=i, type=scene.type, title=scene.title, start=t, duration=duration, beats=beats
            )
        )
        t += duration
    return Timeline(
        version_id=version_id,
        screenplay_revision=revision,
        intro=intro,
        scenes=scenes,
        total_duration=t,
        estimated=estimated,
    )


def build_timeline(
    screenplay, manifest, *, settings, version_id=None, revision=None, include_intro=True, branding=None
) -> Timeline:
    return _build(screenplay, version_id=version_id, revision=revision, include_intro=include_intro, estimated=False)


def preview_timeline(screenplay, manifest, *, settings, version_id=None) -> Timeline:
    return _build(screenplay, version_id=version_id, estimated=True)


def resolve_urls(timeline: Timeline, store, *, signed: bool = False) -> Timeline:
    tl = timeline.model_copy(deep=True)
    keys = [s.audio_asset_key for s in tl.scenes if s.audio_asset_key]
    assets = store.get_many(keys)
    for scene in tl.scenes:
        asset = assets.get(scene.audio_asset_key) if scene.audio_asset_key else None
        if asset is not None:
            scene.audio_url = store.url_for(asset.storage_key)
    return tl


def default_branding(settings):
    from aadhi.schemas.timeline import Branding

    return Branding()
