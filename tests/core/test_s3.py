"""S3Storage against a botocore Stubber (no network)."""

from __future__ import annotations

import hashlib
import io
from urllib.parse import parse_qs, unquote, urlsplit

import boto3
import pytest
from botocore.config import Config
from botocore.response import StreamingBody
from botocore.stub import ANY, Stubber

from aadhi.config import Settings
from aadhi.storage import build_storage
from aadhi.storage import s3 as s3mod
from aadhi.storage.base import Storage
from aadhi.storage.s3 import S3Storage, s3_media_origins

KEY = "assets/tts/tts-abc/Xyz123.mp3"
_SESSION = boto3.session.Session()  # one session per module: loading the S3 model is slow


def _settings(**kw) -> Settings:
    base = {
        "storage_backend": "s3",
        "s3_bucket": "lectures",
        "s3_region": "us-east-1",
        "s3_prefix": "aadhi/",
        "cdn_base_url": "",
    }
    base.update(kw)
    return Settings(**base)


@pytest.fixture()
def s3():
    client = _SESSION.client(
        "s3",
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
        config=Config(signature_version="s3v4"),  # what S3Storage._make_client configures
    )
    storage = S3Storage(_settings(), client=client)
    with Stubber(client) as stubber:
        yield storage, stubber
        stubber.assert_no_pending_responses()


def _body(data: bytes) -> StreamingBody:
    return StreamingBody(io.BytesIO(data), len(data))


def test_is_a_storage(s3):
    storage, _ = s3
    assert isinstance(storage, Storage) and storage.name == "s3" and storage.local_path(KEY) is None


def test_put_bytes_is_conditional(s3):
    storage, stub = s3
    stub.add_response(
        "put_object",
        {},
        {"Bucket": "lectures", "Key": f"aadhi/{KEY}", "Body": b"mp3", "ContentType": "audio/mpeg", "IfNoneMatch": "*"},
    )
    storage.put_bytes(KEY, b"mp3", "audio/mpeg")


MP3_MD5 = hashlib.md5(b"mp3", usedforsecurity=False).hexdigest()


@pytest.mark.parametrize(
    "head",
    [
        {"ContentLength": 3, "ETag": '"0123456789abcdef0123456789abcdef"'},  # same size, other content
        {"ContentLength": 4, "ETag": f'"{MP3_MD5}"'},  # other size
        {"ContentLength": 3, "ETag": f'"{MP3_MD5}-2"'},  # multipart ETag: not comparable
        {"ContentLength": 3},  # no ETag
        None,  # vanished between the PUT and the HEAD
    ],
)
def test_put_bytes_never_overwrites(s3, head):
    storage, stub = s3
    stub.add_client_error("put_object", service_error_code="PreconditionFailed", http_status_code=412)
    if head is None:
        stub.add_client_error("head_object", service_error_code="404", http_status_code=404)
    else:
        stub.add_response("head_object", head, {"Bucket": "lectures", "Key": f"aadhi/{KEY}"})
    with pytest.raises(FileExistsError):
        storage.put_bytes(KEY, b"mp3", "audio/mpeg")


def test_412_on_retry_of_our_own_write_is_success(s3):
    """botocore retried a PUT whose first attempt succeeded (response lost): the 412 is our object."""
    storage, stub = s3
    stub.add_client_error("put_object", service_error_code="PreconditionFailed", http_status_code=412)
    stub.add_response(
        "head_object",
        {"ContentLength": 3, "ETag": f'"{MP3_MD5.upper()}"'},
        {"Bucket": "lectures", "Key": f"aadhi/{KEY}"},
    )
    storage.put_bytes(KEY, b"mp3", "audio/mpeg")


def test_412_on_retry_of_our_own_file_write_is_success(s3, tmp_path):
    storage, stub = s3
    f = tmp_path / "clip.mp3"
    f.write_bytes(b"file-data")
    stub.add_client_error("put_object", service_error_code="PreconditionFailed", http_status_code=412)
    etag = hashlib.md5(b"file-data", usedforsecurity=False).hexdigest()
    stub.add_response("head_object", {"ContentLength": 9, "ETag": f'"{etag}"'})
    storage.put_file(KEY, f, "audio/mpeg")
    stub.add_client_error("put_object", service_error_code="PreconditionFailed", http_status_code=412)
    stub.add_response("head_object", {"ContentLength": 9, "ETag": '"ffffffffffffffffffffffffffffffff"'})
    with pytest.raises(FileExistsError):
        storage.put_file(KEY, f, "audio/mpeg")


def test_conditional_put_is_retried_by_the_real_client():
    """The production client config retries 5xx: the retry of a lost-response PUT gets a 412."""
    storage = S3Storage(_settings(s3_access_key_id="testing", s3_secret_access_key="testing"))
    retries = storage.client.meta.config.retries
    assert retries["mode"] == "standard" and retries["total_max_attempts"] >= 2


def test_put_falls_back_to_head_check_when_unsupported(s3):
    storage, stub = s3
    stub.add_client_error("put_object", service_error_code="NotImplemented", http_status_code=501)
    stub.add_client_error("head_object", service_error_code="404", http_status_code=404)
    stub.add_response(
        "put_object", {}, {"Bucket": "lectures", "Key": f"aadhi/{KEY}", "Body": b"x", "ContentType": "audio/mpeg"}
    )
    storage.put_bytes(KEY, b"x", "audio/mpeg")
    # Remembered: next write goes straight to HEAD + PUT, and an existing object is refused.
    stub.add_response("head_object", {"ContentLength": 1}, {"Bucket": "lectures", "Key": f"aadhi/{KEY}"})
    with pytest.raises(FileExistsError):
        storage.put_bytes(KEY, b"x", "audio/mpeg")


def test_put_other_errors_propagate(s3):
    storage, stub = s3
    stub.add_client_error("put_object", service_error_code="AccessDenied", http_status_code=403)
    with pytest.raises(Exception, match="AccessDenied"):
        storage.put_bytes(KEY, b"x", "audio/mpeg")


def test_put_file_small(s3, tmp_path):
    storage, stub = s3
    f = tmp_path / "clip.mp3"
    f.write_bytes(b"data")
    stub.add_response(
        "put_object",
        {},
        {"Bucket": "lectures", "Key": f"aadhi/{KEY}", "Body": ANY, "ContentType": "audio/mpeg", "IfNoneMatch": "*"},
    )
    storage.put_file(KEY, f, "audio/mpeg")


def test_put_file_large_uses_multipart(s3, tmp_path, monkeypatch):
    storage, stub = s3
    monkeypatch.setattr(s3mod, "SINGLE_PUT_MAX", 3)
    f = tmp_path / "big.mp4"
    f.write_bytes(b"0123456789")
    calls = []
    monkeypatch.setattr(storage.client, "upload_file", lambda *a, **kw: calls.append((a, kw)))
    stub.add_client_error("head_object", service_error_code="404", http_status_code=404)
    storage.put_file(KEY, f, "video/mp4")
    assert calls == [((str(f), "lectures", f"aadhi/{KEY}"), {"ExtraArgs": {"ContentType": "video/mp4"}})]
    stub.add_response("head_object", {"ContentLength": 10})
    with pytest.raises(FileExistsError):
        storage.put_file(KEY, f, "video/mp4")


def test_get_bytes_and_missing(s3):
    storage, stub = s3
    stub.add_response("get_object", {"Body": _body(b"hello")}, {"Bucket": "lectures", "Key": f"aadhi/{KEY}"})
    assert storage.get_bytes(KEY) == b"hello"
    stub.add_client_error("get_object", service_error_code="NoSuchKey", http_status_code=404)
    with pytest.raises(FileNotFoundError):
        storage.get_bytes(KEY)


def test_download_to(s3, tmp_path):
    storage, stub = s3
    stub.add_response("get_object", {"Body": _body(b"x" * 3000)})
    dest = tmp_path / "out" / "clip.mp3"
    assert storage.download_to(KEY, dest) == dest
    assert dest.read_bytes() == b"x" * 3000
    assert [p.name for p in dest.parent.iterdir()] == ["clip.mp3"]
    stub.add_client_error("get_object", service_error_code="NoSuchKey", http_status_code=404)
    with pytest.raises(FileNotFoundError):
        storage.download_to(KEY, tmp_path / "missing.mp3")


def test_exists_size_delete(s3):
    storage, stub = s3
    stub.add_response("head_object", {"ContentLength": 42}, {"Bucket": "lectures", "Key": f"aadhi/{KEY}"})
    assert storage.exists(KEY) is True
    stub.add_client_error("head_object", service_error_code="404", http_status_code=404)
    assert storage.exists(KEY) is False
    stub.add_client_error("head_object", service_error_code="403", http_status_code=403)
    with pytest.raises(Exception, match="403"):
        storage.exists(KEY)
    assert storage.exists("../etc/passwd") is False
    stub.add_response("head_object", {"ContentLength": 42})
    assert storage.size(KEY) == 42
    stub.add_client_error("head_object", service_error_code="404", http_status_code=404)
    with pytest.raises(FileNotFoundError):
        storage.size(KEY)
    stub.add_response("delete_object", {}, {"Bucket": "lectures", "Key": f"aadhi/{KEY}"})
    storage.delete(KEY)


def test_invalid_keys_rejected(s3):
    storage, _ = s3
    for bad in ("../x", "/abs", "a//b", "con.txt", ""):
        with pytest.raises(ValueError):
            storage.public_url(bad)
        with pytest.raises(ValueError):
            storage.put_bytes(bad, b"x", "text/plain")


def test_public_url_variants():
    client = _SESSION.client("s3", region_name="us-east-1", aws_access_key_id="t", aws_secret_access_key="t")
    plain = S3Storage(_settings(), client=client)
    assert plain.public_url(KEY) == f"/media/{KEY}"
    cdn = S3Storage(_settings(cdn_base_url="https://cdn.rec.edu/"), client=client)
    assert cdn.public_url(KEY) == f"https://cdn.rec.edu/{KEY}"  # CDN origin maps to <bucket>/<S3_PREFIX>
    noprefix = S3Storage(_settings(s3_prefix="", media_url_prefix="/files/"), client=client)
    assert noprefix.public_url(KEY) == f"/files/{KEY}" and noprefix.prefix == ""
    assert S3Storage(_settings(s3_prefix="/aadhi//"), client=client).prefix == "aadhi/"


def test_signed_url_offline(s3):
    storage, _ = s3
    url = storage.signed_url(KEY, 10**9, download_name='Ohm\'s "Law" – தமிழ்.mp4')
    parts = urlsplit(url)
    q = parse_qs(parts.query)
    assert parts.path.endswith(f"/aadhi/{KEY}") and "lectures" in url
    assert q["X-Amz-Expires"] == [str(7 * 24 * 3600)]
    disposition = unquote(q["response-content-disposition"][0])
    assert disposition.startswith('attachment; filename="Ohm\'s _Law_ _ _____.mp4"')
    assert "filename*=UTF-8''" in disposition
    short = parse_qs(urlsplit(storage.signed_url(KEY, 0)).query)
    assert short["X-Amz-Expires"] == ["1"] and "response-content-disposition" not in short


def test_lazy_client_for_r2(monkeypatch):
    settings = _settings(
        s3_endpoint_url="https://acct.r2.cloudflarestorage.com",
        s3_region="auto",
        s3_access_key_id="AKIDTEST",
        s3_secret_access_key="secret-test-value",
    )
    storage = S3Storage(settings)
    assert storage._client is None
    client = storage.client
    assert client.meta.endpoint_url == "https://acct.r2.cloudflarestorage.com"
    assert client.meta.config.s3["addressing_style"] == "path"
    assert client.meta.config.retries["mode"] == "standard"
    assert storage.client is client
    url = storage.signed_url(KEY, 60)
    assert url.startswith(f"https://acct.r2.cloudflarestorage.com/lectures/aadhi/{KEY}?")


def test_factory_and_validation():
    assert isinstance(build_storage(_settings()), S3Storage)
    with pytest.raises(ValueError):
        S3Storage(_settings(s3_bucket=""))


def test_media_origins():
    assert s3_media_origins(_settings(s3_region="ap-south-1")) == [
        "https://lectures.s3.amazonaws.com",
        "https://lectures.s3.ap-south-1.amazonaws.com",
    ]
    assert s3_media_origins(_settings(s3_endpoint_url="http://minio.local:9000/path")) == ["http://minio.local:9000"]
    assert s3_media_origins(_settings(s3_bucket="")) == []
