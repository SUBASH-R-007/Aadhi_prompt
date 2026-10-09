"""Teacher uploads (validation, asset refs) and the /media endpoint (namespace, headers, Range)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from aadhi.models import Asset, AssetRef
from aadhi.storage.assets import Produced
from tests.api.factories import add_project, tiny_png


def upload(c, project_id: int, *, name="figure.png", data=None, purpose="figure"):
    return c.post(
        "/api/uploads",
        files={"file": (name, tiny_png() if data is None else data, "application/octet-stream")},
        data={"purpose": purpose, "project_id": str(project_id)},
    )


def setup_project(api):
    alice, c = api.editor("alice")
    with api.db() as db:
        project, version = add_project(db, alice)
    return alice, c, project, version


def test_upload_image_records_asset_ref(api):
    _, c, project, _ = setup_project(api)
    r = upload(c, project.id)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["asset_key"].startswith("upload-")
    assert body["url"].startswith("/media/assets/upload/")
    assert body["mime"] == "image/png" and body["kind"] == "image"
    assert (body["width"], body["height"]) == (3, 2)
    with api.db() as db:
        refs = db.execute(select(AssetRef.asset_key).where(AssetRef.project_id == project.id)).scalars().all()
        assert refs == [body["asset_key"]]
        assert db.execute(select(Asset.kind).where(Asset.key == body["asset_key"])).scalar_one() == "upload"
    again = upload(c, project.id)  # same bytes: same key, no duplicate ref
    assert again.json()["asset_key"] == body["asset_key"]
    with api.db() as db:
        assert len(db.execute(select(AssetRef).where(AssetRef.project_id == project.id)).scalars().all()) == 1
    served = c.get(body["url"])
    assert served.status_code == 200 and served.content == tiny_png()


def test_upload_rejects_markup_and_mismatched_types(api):
    _, c, project, _ = setup_project(api)
    html = upload(c, project.id, name="x.html", data=b"<!doctype html><script>alert(1)</script>")
    assert html.status_code == 415 and html.json()["code"] == "unsupported_type"
    svg = upload(c, project.id, name="x.svg", data=b"<svg xmlns='http://www.w3.org/2000/svg'><script/></svg>")
    assert svg.status_code == 415
    disguised = upload(c, project.id, name="x.png", data=b"<html><body>hi</body></html>")
    assert disguised.status_code in (415, 422)
    pdf = upload(c, project.id, name="x.pdf", data=b"%PDF-1.4 ...")
    assert pdf.status_code == 415


def test_upload_purpose_restricts_kinds(api):
    _, c, project, _ = setup_project(api)
    mp4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 32
    r = upload(c, project.id, name="clip.mp4", data=mp4, purpose="figure")
    assert r.status_code == 415
    bad_purpose = upload(c, project.id, purpose="avatar")
    assert bad_purpose.status_code == 422


def test_upload_size_limits_return_413_and_store_nothing(api):
    _, c, project, _ = setup_project(api)
    api.settings.upload_max_mb = 1
    mib = 1024 * 1024
    # Within the middleware's multipart allowance, but the file itself exceeds UPLOAD_MAX_MB.
    over_file = upload(c, project.id, name="big.png", data=tiny_png() + b"\0" * (mib + 10))
    assert over_file.status_code == 413 and over_file.json()["code"] == "too_large"
    # Declared body larger than the middleware cap: refused before the body is read.
    over_body = upload(c, project.id, name="big.png", data=b"\0" * (3 * mib))
    assert over_body.status_code == 413 and over_body.json()["code"] == "too_large"
    source = c.post("/api/projects", files={"file": ("notes.md", b"# T\n" + b"x" * (mib + 100), "text/markdown")})
    assert source.status_code == 413 and source.json()["code"] == "too_large"
    with api.db() as db:
        assert db.execute(select(Asset).where(Asset.kind.in_(("upload", "source")))).first() is None


def test_upload_to_someone_elses_project_is_404(api):
    alice, c, project, _ = setup_project(api)
    bob = api.user("bob")
    with api.db() as db:
        theirs, _ = add_project(db, bob)
    r = upload(c, theirs.id)
    assert r.status_code == 404
    with api.db() as db:
        assert db.execute(select(AssetRef).where(AssetRef.project_id == theirs.id)).first() is None


def test_media_serves_public_assets_with_strict_headers(api, asset_store):
    asset = asset_store.put("upload-media-test", "upload", Produced(data=tiny_png(), mime="image/png"))
    c = api.client(browser=False)
    r = c.get(f"/media/{asset.storage_key}")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-security-policy"] == "sandbox; default-src 'none'"
    assert "content-disposition" not in r.headers
    head = c.head(f"/media/{asset.storage_key}")
    assert head.status_code == 200 and head.content == b""


def test_media_range_requests_return_206(api, asset_store):
    data = bytes(range(256)) * 8
    asset = asset_store.put("tts-range-test", "tts", Produced(data=data, mime="audio/mpeg"))
    c = api.client(browser=False)
    r = c.get(f"/media/{asset.storage_key}", headers={"Range": "bytes=10-19"})
    assert r.status_code == 206
    assert r.headers["content-range"] == f"bytes 10-19/{len(data)}"
    assert r.content == data[10:20]
    assert r.headers["accept-ranges"] == "bytes"
    tail = c.get(f"/media/{asset.storage_key}", headers={"Range": "bytes=-5"})
    assert tail.status_code == 206 and tail.content == data[-5:]
    bad = c.get(f"/media/{asset.storage_key}", headers={"Range": f"bytes={len(data) + 10}-"})
    assert bad.status_code == 416


def test_non_media_types_are_downloads(api, asset_store):
    asset = asset_store.put("captions-json", "captions", Produced(data=b'{"a": 1}', mime="application/json"))
    r = api.client(browser=False).get(f"/media/{asset.storage_key}")
    assert r.status_code == 200
    assert r.headers["content-disposition"].startswith("attachment")


def test_private_and_invalid_keys_are_404(api, asset_store):
    asset = asset_store.put("source-private-test", "source", Produced(data=b"%PDF-1.4 secret", mime="application/pdf"))
    assert asset.storage_key.startswith("private/")
    c = api.client(browser=False)
    assert c.get(f"/media/{asset.storage_key}").status_code == 404
    assert c.get("/media/assets/../" + asset.storage_key).status_code == 404
    assert c.get("/media/assets/upload/nope/missing.png").status_code == 404
    assert c.get("/media/assets/upload/%2e%2e/x.png").status_code == 404
    assert c.get("/media/assets/con/x.png").status_code == 404
    r = c.get("/media/assets/upload/none/x.png")
    assert r.json()["code"] == "not_found"


@pytest.mark.slow
def test_video_upload_is_probed_with_ffprobe(api, tmp_path):
    import shutil
    import subprocess

    ffmpeg = shutil.which(api.settings.ffmpeg_path)
    if ffmpeg is None:
        pytest.skip("ffmpeg not installed")
    clip = tmp_path / "clip.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=purple:s=64x48:d=1",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(clip),
        ],
        check=True,
        timeout=60,
    )
    _, c, project, _ = setup_project(api)
    r = upload(c, project.id, name="clip.mp4", data=clip.read_bytes(), purpose="scene_media")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["kind"] == "video" and body["mime"] == "video/mp4"
    assert (body["width"], body["height"]) == (64, 48)
    assert abs(body["duration_s"] - 1.0) < 0.2
    ranged = c.get(body["url"], headers={"Range": "bytes=0-99"})
    assert ranged.status_code == 206 and len(ranged.content) == 100
