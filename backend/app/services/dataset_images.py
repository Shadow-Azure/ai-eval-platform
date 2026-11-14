"""M8-25 dataset environment image resolution and public registry probing.

The upstream SWE-bench dataset does not carry an ``environment_image`` column.
The official evaluation harness derives a per-instance image name from the
instance id and architecture::

    swebench/sweb.eval.x86_64.<org>_1776_<repo>-<issue>:latest

This module resolves that execution artifact for every case, probes an ordered
list of configured public registries, and blocks a dataset version when none of
them can serve every selected image. It never reads private Git credentials and
never writes anything to the dataset tables; the caller decides how to persist
the resolved per-case image and how to surface the report.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx

from ..config import Settings, get_settings

SWEBENCH_IMAGE_TEMPLATE = "swebench/sweb.eval.{arch}.{org}_1776_{repo}-{issue}:latest"
DEFAULT_ARCH = "x86_64"
FOUND = "found"
NOT_FOUND = "not_found"
AUTH_REQUIRED = "auth_required"
RATE_LIMITED = "rate_limited"
ERROR = "error"
SKIPPED = "skipped"
BLOCKED = "blocked"
PASSED = "passed"
MAX_REPORT_CASES = 200
_DOCKER_HUB_HOSTS = {"docker.io", "index.docker.io", "registry-1.docker.io"}
_INSTANCE_RE = re.compile(r"^(?P<org>[^_]+)__(?P<repo>.+)-(?P<issue>[0-9]+)$")


class DatasetImageUnavailable(Exception):
    """Every configured public source failed to serve at least one case image."""

    def __init__(self, check: dict[str, Any]) -> None:
        self.check = check
        super().__init__("数据集运行时镜像在已配置公开源中不可用")


@dataclass(frozen=True)
class RegistrySource:
    """One ordered public registry source.

    ``prefix`` is an optional repository namespace (for example a GHCR org),
    so a source may be ``ghcr.io/acme`` and the final path becomes
    ``acme/swebench/...``.
    """

    name: str
    registry: str
    prefix: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "registry": self.registry, "prefix": self.prefix}


@dataclass
class SourceProbe:
    source: RegistrySource
    status: str
    http_status: int | None = None
    error: str | None = None
    elapsed_ms: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.source.name,
            "registry": self.source.registry,
            "prefix": self.source.prefix,
            "status": self.status,
            "http_status": self.http_status,
            "error": self.error,
            "elapsed_ms": self.elapsed_ms,
        }


@dataclass
class ImageProbe:
    image: str
    status: str
    source: str | None = None
    results: list[SourceProbe] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "image": self.image,
            "status": self.status,
            "source": self.source,
            "results": [result.as_dict() for result in self.results],
        }


def parse_instance_id(instance_id: str) -> tuple[str, str, str] | None:
    """Return ``(org, repo, issue)`` for a SWE-bench instance id.

    ``astropy__astropy-12907`` -> ``("astropy", "astropy", "12907")``.
    """

    match = _INSTANCE_RE.match((instance_id or "").strip())
    if not match:
        return None
    return match.group("org"), match.group("repo"), match.group("issue")


def swebench_image_for_instance(instance_id: str, *, arch: str = DEFAULT_ARCH) -> str | None:
    parsed = parse_instance_id(instance_id)
    if parsed is None:
        return None
    org, repo, issue = parsed
    return SWEBENCH_IMAGE_TEMPLATE.format(arch=arch, org=org, repo=repo, issue=issue)


def image_for_case(
    adapter_key: str,
    case_key: str,
    raw: Mapping[str, Any] | None = None,
    *,
    arch: str = DEFAULT_ARCH,
) -> str | None:
    """Resolve the execution image for one dataset case.

    An explicit backend-provided image (some derived datasets add
    ``image_name`` / ``docker_image``) wins. SWE-bench falls back to the
    official per-instance naming convention. Adapters without a known image
    convention return ``None``; the caller may use a version-level image.
    """

    raw = raw or {}
    for key in ("environment_image", "image_name", "docker_image", "image"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if adapter_key == "swebench":
        return swebench_image_for_instance(str(raw.get("instance_id") or case_key), arch=arch)
    return None


def parse_registry_source(value: str) -> RegistrySource | None:
    """Parse ``host[/namespace]`` (or a URL) into an ordered source."""

    text = (value or "").strip().rstrip("/")
    if not text:
        return None
    if "://" in text:
        text = text.split("://", 1)[1]
    parts = [part for part in text.split("/") if part]
    if not parts:
        return None
    registry = parts[0]
    prefix = "/".join(parts[1:])
    name = registry if not prefix else f"{registry}/{prefix}"
    if registry in _DOCKER_HUB_HOSTS:
        registry = "registry-1.docker.io"
        name = "Docker Hub" if not prefix else name
    return RegistrySource(name=name, registry=registry, prefix=prefix)


def configured_sources(settings: Settings | None = None) -> list[RegistrySource]:
    settings = settings or get_settings()
    raw = getattr(settings, "dataset_image_registries", "") or ""
    sources: list[RegistrySource] = []
    seen: set[tuple[str, str]] = set()
    for item in raw.split(","):
        source = parse_registry_source(item)
        if source is None:
            continue
        key = (source.registry, source.prefix)
        if key in seen:
            continue
        seen.add(key)
        sources.append(source)
    return sources


def _split_image(image: str) -> tuple[str, str]:
    """Split an image reference into ``(repository, tag_or_digest)``.

    A leading registry host is stripped so the same repository path can be
    probed against every configured source (Docker Hub, mirror, GHCR prefix).
    """

    value = (image or "").strip()
    if not value:
        return "", "latest"
    if "@" in value:
        repository, digest = value.split("@", 1)
        return repository, f"@{digest}"
    last = value.rsplit("/", 1)[-1]
    if ":" in last:
        repository, tag = value.rsplit(":", 1)
        return repository, tag
    return value, "latest"


def _repository_path(image: str) -> tuple[str, str]:
    repository, ref = _split_image(image)
    first = repository.split("/", 1)[0]
    if "." in first or ":" in first or first == "localhost":
        repository = repository.split("/", 1)[1] if "/" in repository else repository
    return repository, ref


def _docker_hub_token(client: httpx.Client, repository: str, timeout: float) -> str | None:
    try:
        response = client.get(
            "https://auth.docker.io/token",
            params={
                "service": "registry.docker.io",
                "scope": f"repository:{repository}:pull",
            },
            timeout=timeout,
        )
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    token = payload.get("token") if isinstance(payload, dict) else None
    return token if isinstance(token, str) and token else None


def _manifest_url(source: RegistrySource, repository: str, ref: str) -> str:
    path = f"/v2/{quote(repository, safe='/')}/manifests/{quote(ref, safe=':@')}"
    return f"https://{source.registry}{path}"


def probe_source(
    image: str,
    source: RegistrySource,
    *,
    timeout: float,
    client: httpx.Client | None = None,
) -> SourceProbe:
    """Probe one image in one registry via the Docker Registry v2 API."""

    started = datetime.now(UTC)
    repository, ref = _repository_path(image)
    if not repository:
        return SourceProbe(source=source, status=ERROR, error="镜像引用为空")
    owns_client = client is None
    client = client or httpx.Client(follow_redirects=True)
    try:
        headers = {
            "Accept": (
                "application/vnd.docker.distribution.manifest.v2+json,"
                "application/vnd.oci.image.manifest.v1+json,"
                "application/vnd.docker.distribution.manifest.list.v2+json,"
                "application/vnd.oci.image.index.v1+json"
            ),
            "User-Agent": "ai-eval-platform-dataset-image-probe",
        }
        if source.registry in _DOCKER_HUB_HOSTS or source.registry == "registry-1.docker.io":
            token = _docker_hub_token(client, repository, timeout)
            if token:
                headers["Authorization"] = f"Bearer {token}"
        try:
            response = client.get(
                _manifest_url(source, repository, ref),
                headers=headers,
                timeout=timeout,
            )
        except httpx.TimeoutException:
            return SourceProbe(source=source, status=ERROR, error="探测超时")
        except httpx.HTTPError as exc:
            return SourceProbe(source=source, status=ERROR, error=type(exc).__name__)
        elapsed = int((datetime.now(UTC) - started).total_seconds() * 1000)
        if response.status_code == 200:
            return SourceProbe(source=source, status=FOUND, http_status=200, elapsed_ms=elapsed)
        if response.status_code in (401, 403):
            return SourceProbe(
                source=source,
                status=AUTH_REQUIRED,
                http_status=response.status_code,
                error="镜像源要求认证",
                elapsed_ms=elapsed,
            )
        if response.status_code == 404:
            return SourceProbe(
                source=source,
                status=NOT_FOUND,
                http_status=404,
                elapsed_ms=elapsed,
            )
        if response.status_code == 429:
            return SourceProbe(
                source=source,
                status=RATE_LIMITED,
                http_status=429,
                error="镜像源限速",
                elapsed_ms=elapsed,
            )
        return SourceProbe(
            source=source,
            status=ERROR,
            http_status=response.status_code,
            error=f"HTTP {response.status_code}",
            elapsed_ms=elapsed,
        )
    finally:
        if owns_client:
            client.close()


def probe_image(
    image: str,
    sources: list[RegistrySource],
    *,
    timeout: float,
    client: httpx.Client | None = None,
) -> ImageProbe:
    owns_client = client is None
    client = client or httpx.Client(follow_redirects=True)
    results: list[SourceProbe] = []
    try:
        for source in sources:
            try:
                result = probe_source(image, source, timeout=timeout, client=client)
            except Exception as exc:  # never let one bad source abort the whole report
                result = SourceProbe(source=source, status=ERROR, error=type(exc).__name__)
            results.append(result)
            if result.status == FOUND:
                return ImageProbe(image=image, status=FOUND, source=source.name, results=results)
        return ImageProbe(image=image, status=BLOCKED, results=results)
    finally:
        if owns_client:
            client.close()


def probe_case_images(
    case_images: Mapping[str, str],
    *,
    settings: Settings | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Probe every unique case image against the ordered public sources.

    Returns a JSON-safe ``image_check`` report. ``status`` is ``skipped`` when
    no case declares an image, ``passed`` when every image was found, and
    ``blocked`` when at least one image was not found in any configured source.
    """

    settings = settings or get_settings()
    sources = configured_sources(settings)
    unique_images = sorted({image for image in case_images.values() if image})
    generated_at = datetime.now(UTC).isoformat(timespec="seconds")
    if not case_images or not unique_images:
        return {
            "status": SKIPPED,
            "reason": "数据集用例未声明运行时镜像",
            "sources": [source.as_dict() for source in sources],
            "checked": 0,
            "available": 0,
            "cases": [],
            "unavailable": [],
            "cases_truncated": 0,
            "generated_at": generated_at,
        }
    if not sources:
        enabled = bool(getattr(settings, "dataset_image_check_enabled", True))
        return {
            "status": BLOCKED if enabled else SKIPPED,
            "reason": (
                "未配置公开镜像源（DATASET_IMAGE_REGISTRIES），无法校验镜像"
                if enabled
                else "公开镜像源检查已关闭"
            ),
            "sources": [],
            "checked": len(unique_images),
            "available": 0,
            "cases": [],
            "unavailable": [],
            "cases_truncated": 0,
            "generated_at": generated_at,
        }

    timeout = max(1.0, float(getattr(settings, "dataset_image_probe_timeout_seconds", 8.0)))
    concurrency = max(1, min(32, int(getattr(settings, "dataset_image_probe_concurrency", 8))))
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        probes = list(
            pool.map(
                lambda image: probe_image(image, sources, timeout=timeout, client=client),
                unique_images,
            )
        )
    by_image = {probe.image: probe for probe in probes}
    # Aggregate a source-level status for the UI: found if that source served
    # at least one image, otherwise the most actionable failure seen.
    source_statuses: dict[tuple[str, str], list[str]] = {
        (source.registry, source.prefix): [] for source in sources
    }
    for probe in probes:
        for result in probe.results:
            source_statuses.setdefault((result.source.registry, result.source.prefix), []).append(
                result.status
            )
    source_summary: list[dict[str, Any]] = []
    for source in sources:
        statuses = source_statuses.get((source.registry, source.prefix), [])
        if "found" in statuses:
            summary_status = FOUND
        elif not statuses:
            summary_status = "skipped"
        elif AUTH_REQUIRED in statuses:
            summary_status = AUTH_REQUIRED
        elif RATE_LIMITED in statuses:
            summary_status = RATE_LIMITED
        elif ERROR in statuses:
            summary_status = ERROR
        else:
            summary_status = NOT_FOUND
        source_summary.append({**source.as_dict(), "status": summary_status})
    cases: list[dict[str, Any]] = []
    unavailable: list[dict[str, Any]] = []
    for case_key, image in case_images.items():
        probe = by_image.get(image)
        status = probe.status if probe is not None else BLOCKED
        item = {
            "case_key": case_key,
            "image": image,
            "status": status,
            "source": probe.source if probe is not None else None,
            "results": [result.as_dict() for result in (probe.results if probe else [])],
        }
        cases.append(item)
        if status != FOUND:
            unavailable.append(item)
    available = len(cases) - len(unavailable)
    status = PASSED if not unavailable else BLOCKED
    return {
        "status": status,
        "reason": None,
        "sources": source_summary,
        "checked": len(cases),
        "available": available,
        "cases": cases[:MAX_REPORT_CASES],
        "unavailable": unavailable,
        "cases_truncated": max(0, len(cases) - MAX_REPORT_CASES),
        "generated_at": generated_at,
    }
