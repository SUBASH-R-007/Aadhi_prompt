"""Upload validation: every accepted type + disguised/malicious inputs."""

from __future__ import annotations

import io
import json
import shutil
import struct
import subprocess
import wave
import zipfile
import zlib

import pytest
from PIL import Image

from aadhi.security import uploads
from aadhi.security.uploads import UploadRejected, safe_display_name, validate_upload

ALL = {"image", "video", "audio", "pdf", "docx", "json", "text", "markdown"}
MB = 1024 * 1024


# --- sample builders -----------------------------------------------------------


def img(fmt: str, size=(8, 6), mode="RGB") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, (120, 30, 200)).save(buf, fmt)
    return buf.getvalue()


def box(kind: bytes, payload: bytes = b"") -> bytes:
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def mp4(brand: bytes = b"isom", *, moov: bool = True) -> bytes:
    data = box(b"ftyp", brand + b"\x00\x00\x02\x00" + b"isomiso2avc1mp41")
    if moov:
        data += box(b"moov", box(b"mvhd", b"\x00" * 100))
    return data + box(b"mdat", b"\x00" * 64)


def webm(doctype: bytes = b"webm") -> bytes:
    return b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01\x42\x82" + bytes([0x80 | len(doctype)]) + doctype + b"\x00" * 32


def mp3(id3: bool = True) -> bytes:
    frame = b"\xff\xfb\x90\x64" + b"\x00" * 413
    return (b"ID3\x04\x00\x00\x00\x00\x00\x00" if id3 else b"") + frame * 3


def wav() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 160)
    return buf.getvalue()


PDF = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"


def docx(extra: dict[str, bytes] | None = None, *, content_types: bytes | None = None, document: bool = True) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types or b'<?xml version="1.0"?><Types/>')
        z.writestr("_rels/.rels", b"<Relationships/>")
        if document:
            z.writestr("word/document.xml", b"<w:document><w:body><w:p>Hello</w:p></w:body></w:document>")
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return buf.getvalue()


def check(name: str, data: bytes, kinds=ALL, max_bytes=50 * MB):
    return validate_upload(name, data, allowed_kinds=set(kinds), max_bytes=max_bytes)


def rejected(name: str, data: bytes, kinds=ALL, max_bytes=50 * MB) -> UploadRejected:
    with pytest.raises(UploadRejected) as exc:
        check(name, data, kinds, max_bytes)
    return exc.value


# --- accepted ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "builder", "mime", "ext", "kind"),
    [
        ("a.png", lambda: img("PNG"), "image/png", "png", "image"),
        ("a.PNG", lambda: img("PNG"), "image/png", "png", "image"),
        ("a.jpg", lambda: img("JPEG"), "image/jpeg", "jpg", "image"),
        ("a.jpeg", lambda: img("JPEG"), "image/jpeg", "jpg", "image"),
        ("a.gif", lambda: img("GIF", mode="P"), "image/gif", "gif", "image"),
        ("a.webp", lambda: img("WEBP"), "image/webp", "webp", "image"),
        ("v.mp4", mp4, "video/mp4", "mp4", "video"),
        ("v.webm", webm, "video/webm", "webm", "video"),
        ("s.mp3", mp3, "audio/mpeg", "mp3", "audio"),
        ("s2.mp3", lambda: mp3(id3=False), "audio/mpeg", "mp3", "audio"),
        ("s.wav", wav, "audio/wav", "wav", "audio"),
        ("doc.pdf", lambda: PDF, "application/pdf", "pdf", "pdf"),
        ("doc.docx", docx, uploads.DOCX_MIME, "docx", "docx"),
        ("x.json", lambda: b'{"scenes": [1, 2]}', "application/json", "json", "json"),
        ("bom.json", lambda: b'\xef\xbb\xbf{"a": 1}', "application/json", "json", "json"),
        ("notes.txt", lambda: "Ohm's law: V = IR\nதமிழ்\n".encode(), "text/plain", "txt", "text"),
        ("notes.md", lambda: b"# Title\n\n<!-- page 1 -->\n* item\n", "text/markdown", "md", "markdown"),
        ("notes.markdown", lambda: b"Some text", "text/markdown", "md", "markdown"),
    ],
)
def test_accepted(name, builder, mime, ext, kind):
    info = check(name, builder())
    assert (info.mime, info.ext, info.kind) == (mime, ext, kind)


def test_image_dimensions_reported():
    info = check("a.png", img("PNG", size=(33, 17)))
    assert (info.width, info.height) == (33, 17)


# --- type / size gating -----------------------------------------------------------


@pytest.mark.parametrize("name", ["evil.html", "evil.svg", "evil.js", "evil.exe", "noext", "evil.xml", "a.zip", ""])
def test_unknown_extensions(name):
    assert rejected(name, b"<svg onload=alert(1)>").code == "unsupported_type"


def test_kind_not_allowed_here():
    err = rejected("a.png", img("PNG"), kinds={"pdf", "docx"})
    assert err.status_code == 415


def test_size_and_empty():
    assert rejected("a.png", img("PNG"), max_bytes=10).status_code == 413
    assert rejected("a.txt", b"").code == "invalid_upload"


def test_unknown_kind_is_programming_error():
    with pytest.raises(ValueError):
        validate_upload("a.png", img("PNG"), allowed_kinds={"images"}, max_bytes=MB)


# --- disguised / malicious -----------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        b"<html><script>alert(1)</script></html>",
        b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>',
        b"<?xml version='1.0'?><svg/>",
    ],
)
@pytest.mark.parametrize("name", ["x.png", "x.jpg", "x.gif", "x.webp", "x.mp4", "x.pdf", "x.docx", "x.wav"])
def test_markup_disguised_as_binary(name, payload):
    assert rejected(name, payload).status_code == 415


@pytest.mark.parametrize(
    "payload",
    [b"<!DOCTYPE html><p>x</p>", b"  <html>", b"<svg width='1'/>", b"<?xml version='1.0'?>", b"<script>x</script>"],
)
@pytest.mark.parametrize("name", ["x.txt", "x.md"])
def test_markup_disguised_as_text(name, payload):
    assert rejected(name, payload).code == "unsupported_type"


def test_format_mismatch():
    assert rejected("a.png", img("JPEG")).code == "unsupported_type"
    assert rejected("a.jpg", img("PNG")).code == "unsupported_type"
    assert rejected("a.pdf", docx()).code == "unsupported_type"
    assert rejected("a.docx", PDF).code == "unsupported_type"
    assert rejected("a.mp3", wav()).code == "unsupported_type"
    assert rejected("a.txt", img("PNG")).code == "unsupported_type"


def test_png_polyglot_with_trailing_zip():
    data = img("PNG") + docx()
    err = rejected("a.png", data)
    assert err.code == "invalid_upload" and "after its end" in err.message


def test_gif_javascript_header_is_not_a_gif():
    payload = b"GIF89a=alert(document.domain)//" + b"\x00" * 40
    assert rejected("a.gif", payload).code == "invalid_upload"


IMAGE_FORMATS = [("a.png", "PNG", "RGB"), ("a.gif", "GIF", "P"), ("a.jpg", "JPEG", "RGB"), ("a.webp", "WEBP", "RGB")]
HTML_TAIL = b"<html><body><script>alert(document.domain)</script></body></html>"


def _exif() -> bytes:
    exif = Image.Exif()
    exif[0x010F] = "REC camera"  # Make
    exif[0x0110] = "Aadhi 1"  # Model
    return exif.tobytes()


EXIF = _exif()


def rich_img(fmt: str, mode: str, **save: object) -> bytes:
    """A real (noisy, multi-block) image so structure walkers see more than a trivial file."""
    im = Image.effect_noise((64, 48), 80).convert(mode)
    buf = io.BytesIO()
    im.save(buf, fmt, **save)
    return buf.getvalue()


@pytest.mark.parametrize(("name", "fmt", "mode"), IMAGE_FORMATS)
@pytest.mark.parametrize(
    ("tail", "why"),
    [
        (HTML_TAIL, "html"),
        (b"var x = 1;", "js-ending-with-semicolon"),  # ';' is also the GIF trailer byte
        (b"\x00" * 8 + HTML_TAIL, "padding-then-html"),
        (None, "zip"),
    ],
)
def test_valid_image_polyglots_rejected(name, fmt, mode, tail, why):
    data = rich_img(fmt, mode)
    check(name, data)  # the plain image is fine
    err = rejected(name, data + (docx() if tail is None else tail))
    assert err.code == "invalid_upload" and ("after its end" in err.message or "zip" in err.message), why


@pytest.mark.parametrize(("name", "fmt", "mode"), IMAGE_FORMATS)
def test_zero_padding_after_image_is_accepted(name, fmt, mode):
    assert check(name, rich_img(fmt, mode) + b"\x00" * 16).kind == "image"


@pytest.mark.parametrize(
    ("name", "fmt", "mode", "save"),
    [
        ("p.jpg", "JPEG", "RGB", {"progressive": True, "quality": 70}),
        ("r.jpg", "JPEG", "RGB", {"restart_marker_blocks": 1}),
        ("e.jpg", "JPEG", "RGB", {"exif": EXIF, "icc_profile": b"\x00" * 300}),
        ("l.webp", "WEBP", "RGB", {"lossless": True}),
        ("i.png", "PNG", "RGB", {"optimize": True}),
    ],
)
def test_real_encoder_variants_accepted(name, fmt, mode, save):
    assert check(name, rich_img(fmt, mode, **save)).kind == "image"


def test_animated_gif_and_webp_accepted():
    frames = [Image.effect_noise((40, 30), 60 + i * 10).convert("P") for i in range(3)]
    for fmt, name in (("GIF", "anim.gif"), ("WEBP", "anim.webp")):
        buf = io.BytesIO()
        frames[0].save(buf, fmt, save_all=True, append_images=frames[1:], duration=100, loop=0, comment=b"hi")
        assert check(name, buf.getvalue()).kind == "image"


def test_gif_without_trailer_at_block_boundary_is_accepted():
    data = rich_img("GIF", "P")
    assert data.endswith(b";")
    assert check("a.gif", data[:-1]).kind == "image"


def test_gif_comment_hiding_a_zip_is_rejected():
    data = rich_img("GIF", "P")
    archive = bytearray(docx())
    struct.pack_into("<H", archive, len(archive) - 2, 2)  # the zip comment swallows the trailing 00 3B
    blocks = b"".join(bytes([len(c)]) + c for c in (archive[i : i + 255] for i in range(0, len(archive), 255)))
    polyglot = data[:-1] + b"\x21\xfe" + blocks + b"\x00;"
    assert zipfile.is_zipfile(io.BytesIO(polyglot))
    assert "zip" in rejected("a.gif", polyglot).message


@pytest.mark.parametrize(
    ("tail", "ok"),
    [
        (lambda: img("JPEG"), True),  # MPF / Ultra HDR gain map / depth map: a second JPEG
        (lambda: mp4(), True),  # Motion Photo
        (lambda: b"SEFH" + b"\x01" * 40 + b"SEFT", True),  # Samsung trailer
        (lambda: img("JPEG") + HTML_TAIL, False),  # "benign" prefix but carries markup
        (lambda: img("JPEG") + docx(), False),  # also a zip archive
        (lambda: b"RANDOM-GARBAGE" * 4, False),
    ],
    ids=["second-jpeg", "motion-photo", "samsung", "jpeg-plus-html", "jpeg-plus-zip", "garbage"],
)
def test_jpeg_phone_trailers(tail, ok):
    data = rich_img("JPEG", "RGB") + tail()
    if ok:
        assert check("phone.jpg", data).kind == "image"
    else:
        assert rejected("phone.jpg", data).code == "invalid_upload"


def test_truncated_images_rejected():
    for name, fmt, mode in IMAGE_FORMATS:
        data = rich_img(fmt, mode)
        assert rejected(name, data[: len(data) * 2 // 3]).code == "invalid_upload", name


def test_corrupt_png():
    data = bytearray(img("PNG"))
    data[40:60] = b"\x00" * 20  # break IDAT / CRC
    assert rejected("a.png", bytes(data)).code == "invalid_upload"
    assert rejected("a.png", img("PNG")[:30]).code == "invalid_upload"


def _png_with_dimensions(width: int, height: int) -> bytes:
    data = bytearray(img("PNG", size=(1, 1)))
    ihdr = bytearray(data[12:29])  # type + 13 bytes of data
    ihdr[4:12] = struct.pack(">II", width, height)
    data[12:29] = ihdr
    data[29:33] = struct.pack(">I", zlib.crc32(bytes(ihdr)) & 0xFFFFFFFF)
    return bytes(data)


@pytest.mark.parametrize("dims", [(10_000, 6_000), (60_000, 60_000)])
def test_decompression_bomb_png(dims):
    err = rejected("bomb.png", _png_with_dimensions(*dims))
    assert err.code == "invalid_upload"


def test_mp4_checks():
    assert rejected("v.mp4", mp4(brand=b"heic")).code == "unsupported_type"
    assert rejected("v.mp4", mp4(moov=False)).code == "invalid_upload"
    truncated = mp4()[:-70] + box(b"moov", b"")[:4]
    assert rejected("v.mp4", truncated).status_code in (415, 422)
    garbage = box(b"ftyp", b"isom\x00\x00\x02\x00") + b"\x00\x00\x00\x10\x01\x02\x03\x04" + b"x" * 8
    assert rejected("v.mp4", garbage).code == "invalid_upload"


def test_webm_checks():
    assert rejected("v.webm", webm(b"matroska")).code == "unsupported_type"


def test_audio_checks():
    assert rejected("s.mp3", b"\x00" * 1000).code == "unsupported_type"
    bad = bytearray(wav())
    bad[20:22] = struct.pack("<H", 7)  # unsupported audio format tag
    assert rejected("s.wav", bytes(bad)).code == "invalid_upload"


def test_pdf_checks():
    assert rejected("d.pdf", PDF.replace(b"%%EOF", b"")).code == "invalid_upload"
    assert rejected("d.pdf", b"junk" + PDF).code == "unsupported_type"


def test_docx_zip_bomb_ratio():
    bomb = docx({"word/media/zeros.bin": b"\x00" * (8 * MB)})
    assert len(bomb) < 200_000
    err = rejected("bomb.docx", bomb)
    assert err.code == "invalid_upload" and "zip bomb" in err.message


def test_docx_total_uncompressed_cap(monkeypatch):
    monkeypatch.setattr(uploads, "DOCX_MAX_UNCOMPRESSED", 1000)
    err = rejected("big.docx", docx({"word/media/a.txt": bytes(range(256)) * 8}))
    assert "100 MB" in err.message


def test_docx_macros_rejected():
    assert "Macro" in rejected("m.docx", docx({"word/vbaProject.bin": b"VBA"})).message
    ct = b'<Types><Override ContentType="application/vnd.ms-word.document.macroEnabled.main+xml"/></Types>'
    assert "Macro" in rejected("m.docx", docx(content_types=ct)).message


def test_docx_must_be_word():
    assert rejected("plain.docx", docx(document=False)).code == "unsupported_type"
    assert rejected("x.docx", b"PK\x03\x04garbage").code == "invalid_upload"


def test_docx_path_traversal_and_duplicates():
    assert rejected("t.docx", docx({"../evil.xml": b"x"})).code == "invalid_upload"
    with pytest.warns(UserWarning):
        dup = docx({"word/document.xml": b"<again/>"})
    assert "duplicate" in rejected("d.docx", dup).message


def test_docx_encrypted_entry():
    data = bytearray(docx())
    pos = data.find(b"PK\x01\x02")
    while pos != -1:
        flags = struct.unpack_from("<H", data, pos + 8)[0]
        struct.pack_into("<H", data, pos + 8, flags | 0x1)
        pos = data.find(b"PK\x01\x02", pos + 4)
    assert "Encrypted" in rejected("e.docx", bytes(data)).message


@pytest.mark.parametrize(
    "payload",
    [
        b"\xff\xfe{}",
        b'{"a": NaN}',
        b'{"a": Infinity}',
        b'{"a": -Infinity}',
        b"[1e400]",
        b'{"a": -1.5e999}',
        b"{'a': 1}",
        b'{"a": 1',
        b"[" * 100_000 + b"]" * 100_000,
    ],
    ids=[
        "utf16",
        "nan",
        "infinity",
        "minus-infinity",
        "overflow",
        "negative-overflow",
        "single-quotes",
        "truncated",
        "deep-nesting",
    ],
)
def test_json_rejections(payload):
    assert rejected("x.json", payload).code == "invalid_upload"


def test_text_rejections():
    assert "NUL" in rejected("x.txt", b"abc\x00def").message
    assert "UTF-8" in rejected("x.txt", b"caf\xe9").message
    assert "control" in rejected("x.md", b"bell\x07here").message


# --- display names ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\x\\lecture notes.pdf", "lecture notes.pdf"),
        ("a<b>c:d|e?.png", "a_b_c_d_e_.png"),
        ("evil\u202egnp.exe", "evilgnp.exe"),
        ("con.txt", "_con.txt"),
        ("  ..hidden. ", "hidden"),
        ("", "file"),
        ("....", "file"),
        ("tab\tand\nnewline.md", "tabandnewline.md"),
        ("Ｆｕｌｌｗｉｄｔｈ.pdf", "Fullwidth.pdf"),
    ],
)
def test_safe_display_name(raw, expected):
    assert safe_display_name(raw) == expected


def test_safe_display_name_length_keeps_extension():
    name = safe_display_name("x" * 500 + ".docx")
    assert len(name) == 120 and name.endswith(".docx")


# --- real media (ffmpeg) ------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
@pytest.mark.parametrize(
    ("ext", "args"),
    [
        (
            "mp4",
            ["-f", "lavfi", "-i", "testsrc=size=64x48:rate=5:duration=0.4", "-pix_fmt", "yuv420p", "-c:v", "libx264"],
        ),
        ("webm", ["-f", "lavfi", "-i", "testsrc=size=64x48:rate=5:duration=0.4", "-c:v", "libvpx-vp9", "-b:v", "50k"]),
        ("mp3", ["-f", "lavfi", "-i", "sine=frequency=440:duration=0.3", "-c:a", "libmp3lame"]),
        ("wav", ["-f", "lavfi", "-i", "sine=frequency=440:duration=0.3"]),
    ],
)
def test_real_ffmpeg_media(tmp_path, ext, args):
    out = tmp_path / f"sample.{ext}"
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args, str(out)],
        capture_output=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        pytest.skip(f"ffmpeg cannot encode {ext}: {proc.stderr[-200:]!r}")
    info = check(out.name, out.read_bytes())
    assert info.ext == ext


def test_json_payload_sample_roundtrip():
    payload = json.dumps({"subject_name": "x", "scenes": []}).encode()
    assert check("lecture.json", payload, kinds={"json"}).kind == "json"
