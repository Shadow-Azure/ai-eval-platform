"""S3-compatible object store access (Aliyun OSS / MinIO), stdlib only.

M2 has no boto3 dependency, so the store speaks SigV4 directly:

- ``presign_put_url`` — query-string presigned PUT (``UNSIGNED-PAYLOAD``)
  handed to the runner; object keys are platform-owned per design 04 §5.
- ``verify_object`` — presigned HEAD gated on existence + size, used by
  completion callbacks ("只收到回调但对象不存在时进入 artifact_missing").

Region is configurable: MinIO accepts any value, Aliyun OSS S3-compatibility
expects the OSS region id (e.g. ``oss-cn-hangzhou``) — proven live in the
M2-05 host smoke.
"""

from __future__ import annotations

import hashlib
import hmac
import urllib.error
import urllib.request
from datetime import UTC, datetime
from urllib.parse import quote, urlparse


class ObjectStoreError(Exception):
    """Base class for object-store verification failures."""


class ObjectMissing(ObjectStoreError):
    pass


class ObjectSizeMismatch(ObjectStoreError):
    pass


def _uri_encode_path(path: str) -> str:
    # S3 style: encode each segment, keep '/' as a literal separator.
    return quote(path, safe="/")


def _sign(key: bytes, value: str) -> bytes:
    return hmac.new(key, value.encode(), hashlib.sha256).digest()


class ObjectStore:
    def __init__(
        self,
        *,
        endpoint: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        region: str = "us-east-1",
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.bucket = bucket
        self.access_key = access_key
        self.secret_key = secret_key
        self.region = region

    # -- SigV4 helpers ------------------------------------------------------

    def _amz_date(self, now: datetime) -> tuple[str, str]:
        aware = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
        stamp = aware.strftime("%Y%m%dT%H%M%SZ")
        return stamp, stamp[:8]

    def _signing_key(self, date_stamp: str) -> bytes:
        key = _sign(f"AWS4{self.secret_key}".encode(), date_stamp)
        key = _sign(key, self.region)
        key = _sign(key, "s3")
        return _sign(key, "aws4_request")

    def _presign(self, method: str, object_key: str, expires: int, now: datetime) -> str:
        amz_date, date_stamp = self._amz_date(now)
        parsed = urlparse(self.endpoint)
        host = parsed.netloc
        canonical_uri = _uri_encode_path(f"/{self.bucket}/{object_key}")
        scope = f"{date_stamp}/{self.region}/s3/aws4_request"
        # SigV4: the credential element in the canonical query is URL-encoded
        # including its "/" separators (raw slashes → SignatureDoesNotMatch on
        # real S3/MinIO; caught by the M2-05 host smoke).
        credential = quote(f"{self.access_key}/{scope}", safe="")
        query = "&".join(
            [
                "X-Amz-Algorithm=AWS4-HMAC-SHA256",
                f"X-Amz-Credential={credential}",
                f"X-Amz-Date={amz_date}",
                f"X-Amz-Expires={expires}",
                "X-Amz-SignedHeaders=host",
            ]
        )
        canonical_request = "\n".join(
            [method, canonical_uri, query, f"host:{host}\n", "host", "UNSIGNED-PAYLOAD"]
        )
        string_to_sign = "\n".join(
            [
                "AWS4-HMAC-SHA256",
                amz_date,
                scope,
                hashlib.sha256(canonical_request.encode()).hexdigest(),
            ]
        )
        signature = hmac.new(
            self._signing_key(date_stamp), string_to_sign.encode(), hashlib.sha256
        ).hexdigest()
        return f"{self.endpoint}{canonical_uri}?{query}&X-Amz-Signature={signature}"

    def _presigned_head(self, object_key: str, *, expires: int, now: datetime) -> str:
        return self._presign("HEAD", object_key, expires, now)

    # -- Public surface -----------------------------------------------------

    def presign_put_url(
        self,
        object_key: str,
        *,
        expires: int = 900,
        now: datetime | None = None,
    ) -> str:
        """Presigned PUT URL for one platform-owned object key."""

        now = now or datetime.now(UTC)
        return self._presign("PUT", object_key, expires, now)

    def head_object(self, object_key: str, *, now: datetime | None = None) -> int:
        """Return the stored object size; raise ObjectMissing when absent."""

        now = now or datetime.now(UTC)
        url = self._presigned_head(object_key, expires=60, now=now)
        request = urllib.request.Request(url, method="HEAD")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                length = response.headers.get("Content-Length")
                return int(length) if length is not None else 0
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise ObjectMissing(object_key) from exc
            raise ObjectStoreError(f"HEAD {object_key} 返回 HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise ObjectStoreError(f"HEAD {object_key} 失败：{exc.reason}") from exc

    def verify_object(
        self,
        object_key: str,
        *,
        expected_size: int | None = None,
        now: datetime | None = None,
    ) -> None:
        """Existence + size gate for completion manifests."""

        size = self.head_object(object_key, now=now)
        if expected_size is not None and size != expected_size:
            raise ObjectSizeMismatch(
                f"对象 {object_key} 大小不一致：远端 {size}，回调声明 {expected_size}"
            )


def object_store_from_settings(settings) -> ObjectStore | None:
    """Build the store from platform settings; None when unconfigured."""

    if not (settings.s3_endpoint and settings.s3_access_key and settings.s3_secret_key):
        return None
    return ObjectStore(
        endpoint=settings.s3_endpoint,
        bucket=settings.s3_bucket,
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key,
        region=settings.s3_region,
    )
