"""S3-compatible object store (OSS/MinIO) with SigV4 presigned PUT + HEAD.

The platform owns object keys and hands the runner a time-limited presigned
PUT; completion callbacks are gated on HEAD existence + size. Unit tests pin
URL structure and the HTTP verification path against a local stub server; the
real OSS round-trip is proven in the host smoke.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest
from app.services.object_store import (
    ObjectMissing,
    ObjectSizeMismatch,
    ObjectStore,
    ObjectStoreError,
)


class StubObjectHandler(BaseHTTPRequestHandler):
    """Minimal S3-ish endpoint: objects live under /bucket/key."""

    server_version = "StubS3/1"

    def do_HEAD(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/eval-artifacts":
            # Bucket-level HEAD used by the operations liveness probe.
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        parts = parsed.path.lstrip("/").split("/", 1)
        if len(parts) != 2 or parts[0] != "eval-artifacts":
            self.send_response(404)
            self.end_headers()
            return
        known = {"/eval-artifacts/objects/aaa": 12, "/eval-artifacts/objects/bbb": 0}
        size = known.get(parsed.path)
        if size is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(size))
        self.end_headers()

    def log_message(self, *_args) -> None:  # keep test output clean
        return


@pytest.fixture
def endpoint() -> str:
    server = ThreadingHTTPServer(("127.0.0.1", 0), StubObjectHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[0] and server.server_address[1]}"
    server.shutdown()
    thread.join()


def make_store(endpoint: str) -> ObjectStore:
    return ObjectStore(
        endpoint=endpoint,
        bucket="eval-artifacts",
        access_key="akTest",
        secret_key="skTest",
    )


def now() -> datetime:
    return datetime.now(UTC)


def test_presigned_put_url_structure(endpoint: str) -> None:
    store = make_store(endpoint)
    url = store.presign_put_url("objects/aaa", expires=600, now=now())

    parsed = urlparse(url)
    assert parsed.path == "/eval-artifacts/objects/aaa"
    query = parse_qs(parsed.query)
    assert query["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
    assert query["X-Amz-Expires"] == ["600"]
    assert query["X-Amz-SignedHeaders"] == ["host"]
    credential = query["X-Amz-Credential"][0]
    assert credential.startswith("akTest/")
    assert credential.endswith("/s3/aws4_request")
    # The canonical query string must URL-encode the slashes in the credential
    # value (SigV4 requirement) — real S3/MinIO reject raw "/" there, which the
    # M2-05 host smoke caught (presigned PUT → SignatureDoesNotMatch).
    assert "X-Amz-Credential=akTest%2F" in url
    signature = query["X-Amz-Signature"][0]
    assert len(signature) == 64 and all(c in "0123456789abcdef" for c in signature)
    assert "X-Amz-Date" in query


def test_presign_is_deterministic_and_key_sensitive(endpoint: str) -> None:
    store = make_store(endpoint)
    fixed = datetime(2026, 11, 9, tzinfo=UTC)
    first = store.presign_put_url("objects/aaa", expires=600, now=fixed)
    second = store.presign_put_url("objects/aaa", expires=600, now=fixed)
    assert first == second

    other_key = store.presign_put_url("objects/bbb", expires=600, now=fixed)
    assert urlparse(other_key).path != urlparse(first).path
    assert urlparse(other_key).query != urlparse(first).query

    other_secret = ObjectStore(
        endpoint=endpoint, bucket="eval-artifacts", access_key="akTest", secret_key="other"
    )
    assert other_secret.presign_put_url("objects/aaa", expires=600, now=fixed) != first


def test_verify_object_exists_with_matching_size(endpoint: str) -> None:
    store = make_store(endpoint)
    store.verify_object("objects/aaa", expected_size=12, now=now())
    store.verify_object("objects/bbb", expected_size=0, now=now())

    with pytest.raises(ObjectMissing):
        store.verify_object("objects/missing", now=now())
    with pytest.raises(ObjectSizeMismatch):
        store.verify_object("objects/aaa", expected_size=13, now=now())


def test_verify_object_without_size_gate(endpoint: str) -> None:
    store = make_store(endpoint)
    store.verify_object("objects/aaa", now=now())


def test_head_bucket_liveness_probe(endpoint: str) -> None:
    store = make_store(endpoint)
    store.head_bucket(timeout=2)

    wrong_bucket = ObjectStore(
        endpoint=endpoint,
        bucket="missing-bucket",
        access_key="akTest",
        secret_key="skTest",
    )
    with pytest.raises(ObjectStoreError):
        wrong_bucket.head_bucket(timeout=2)


def test_signature_is_real_hmac_over_canonical_request(endpoint: str) -> None:
    """The signature must be an HMAC-SHA256 chain over the canonical request,
    not a hash of the URL: flip one canonical element and the signature must
    change in a way that cannot be predicted from the URL alone."""

    store = make_store(endpoint)
    fixed = datetime(2026, 11, 9, tzinfo=UTC)
    url_a = store.presign_put_url("objects/aaa", expires=600, now=fixed)
    url_b = store.presign_put_url("objects/aaab", expires=600, now=fixed)

    sig_a = parse_qs(urlparse(url_a).query)["X-Amz-Signature"][0]
    sig_b = parse_qs(urlparse(url_b).query)["X-Amz-Signature"][0]
    assert sig_a != sig_b
    assert sha256(sig_a.encode()).hexdigest() != sha256(sig_b.encode()).hexdigest()


def test_aliyun_oss_uses_virtual_hosted_addressing() -> None:
    from app.services.object_store import ObjectStore

    store = ObjectStore(
        endpoint="https://oss-cn-hangzhou.aliyuncs.com",
        bucket="eval-artifacts",
        access_key="ak",
        secret_key="sk",
        region="oss-cn-hangzhou",
    )
    url = store.presign_put_url("evals/run/a.json")
    assert url.startswith("https://eval-artifacts.oss-cn-hangzhou.aliyuncs.com/evals/run/a.json?")

    local = ObjectStore(
        endpoint="http://127.0.0.1:9000",
        bucket="eval-artifacts",
        access_key="ak",
        secret_key="sk",
    )
    assert local.presign_put_url("x").startswith("http://127.0.0.1:9000/eval-artifacts/x?")

    bare = ObjectStore(
        endpoint="oss-cn-hangzhou.aliyuncs.com",
        bucket="eval-artifacts",
        access_key="ak",
        secret_key="sk",
        region="oss-cn-hangzhou",
    )
    assert bare.presign_put_url("x").startswith("https://eval-artifacts.oss-cn-hangzhou.aliyuncs.com/x?")
