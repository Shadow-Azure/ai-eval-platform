"""SWE-bench official report parser.

The harness writes one report entry per ``instance_id`` with ``resolved``,
``patch_successfully_applied`` and ``tests_status`` containing F2P/P2P
success and failure lists. The platform keeps every test entry instead of
only the final boolean.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class ScoreParseError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ParsedTest:
    suite_name: str
    test_name: str
    outcome: str
    native_status: str
    test_group: str
    expected: bool = True
    file_path: str | None = None
    failure_message: str | None = None


@dataclass(frozen=True)
class ParsedFailure:
    """Structured terminal failure carried by the runner's score entry.

    M8 (#375): the runner is the single classification point. The platform
    keeps the code for grouping, the human-readable message (upstream 429 body,
    request id, asset install stderr) for display, and the retryable hint so a
    completed execution failure can enter the existing attempt budget.
    """

    code: str
    message: str | None = None
    phase: str | None = None
    request_id: str | None = None
    http_status: int | None = None
    retryable: bool | None = None

    def as_dict(self) -> dict[str, object]:
        detail: dict[str, object] = {"code": self.code}
        for key, value in (
            ("message", self.message),
            ("phase", self.phase),
            ("request_id", self.request_id),
            ("http_status", self.http_status),
            ("retryable", self.retryable),
        ):
            if value is not None:
                detail[key] = value
        return detail


@dataclass(frozen=True)
class ParsedCase:
    case_key: str
    result_status: str
    tests: tuple[ParsedTest, ...] = field(default_factory=tuple)
    failure_message: str | None = None
    failure: ParsedFailure | None = None


def _test_entry(name: str, group: str, native_status: str) -> ParsedTest:
    parts = name.split("::")
    if len(parts) >= 2:
        suite_name, test_name = parts[0], "::".join(parts[1:])
    else:
        suite_name, test_name = "", name
    file_path = suite_name if suite_name.endswith(".py") or "/" in suite_name else None
    return ParsedTest(
        suite_name=suite_name,
        test_name=test_name,
        outcome="passed" if native_status == "success" else "failed",
        native_status=native_status,
        test_group=group,
        expected=True,
        file_path=file_path,
    )


def _status_block(entry: dict, key: str) -> dict:
    block = entry.get("tests_status") or {}
    if not isinstance(block, dict):
        return {}
    value = block.get(key) or {}
    return value if isinstance(value, dict) else {}


def _names(block: dict, key: str) -> list[str]:
    value = block.get(key) or []
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


FAILURE_CODE_LIMIT = 64
FAILURE_MESSAGE_LIMIT = 1000
FAILURE_PHASE_LIMIT = 64
FAILURE_REQUEST_ID_LIMIT = 128


def _clean_text(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    return text[:limit]


def _first_text(values: dict, *keys: str, limit: int) -> str | None:
    for key in keys:
        text = _clean_text(values.get(key), limit)
        if text:
            return text
    return None


def _parse_failure(entry: dict) -> ParsedFailure | None:
    """Read ``entry.failure`` (new runner) or fall back to ``entry.error``.

    Old runners only wrote ``error`` and sometimes ``message``. Turning that
    pair into the same structured shape keeps the console's category label
    working during a rolling upgrade, while ``message`` finally reaches the
    case summary instead of being discarded.
    """

    raw = entry.get("failure")
    values = raw if isinstance(raw, dict) else {}
    code = _first_text(values, "code", "error_code", "error", limit=FAILURE_CODE_LIMIT)
    message = _first_text(
        values, "message", "detail", "upstream_message", limit=FAILURE_MESSAGE_LIMIT
    )
    legacy_error = entry.get("error")
    if code is None and isinstance(legacy_error, str):
        code = _clean_text(legacy_error, FAILURE_CODE_LIMIT)
    if code is None and isinstance(entry.get("failure_reason"), str):
        # Old asset-install rows wrote the node code into failure_reason only.
        code = _clean_text(entry.get("failure_reason"), FAILURE_CODE_LIMIT)
    if message is None:
        message = _clean_text(entry.get("message"), FAILURE_MESSAGE_LIMIT)
    if code is None and message is None:
        return None
    if code is None:
        code = "agent_failed"
    phase = _first_text(values, "phase", limit=FAILURE_PHASE_LIMIT)
    request_id = _first_text(
        values, "request_id", "requestId", limit=FAILURE_REQUEST_ID_LIMIT
    )
    http_status = values.get("http_status")
    if isinstance(http_status, bool) or not isinstance(http_status, int):
        http_status = None
    retryable = values.get("retryable")
    if not isinstance(retryable, bool):
        retryable = None
    if message == code:
        message = None
    return ParsedFailure(
        code=code,
        message=message,
        phase=phase,
        request_id=request_id,
        http_status=http_status,
        retryable=retryable,
    )


def normalize_failure_detail(value: object) -> dict | None:
    """Validate a callback/failure payload's ``failure`` object."""

    if not isinstance(value, dict):
        return None
    parsed = _parse_failure({"failure": value})
    return parsed.as_dict() if parsed is not None else None


def parse_report(report: object, case_key: str) -> ParsedCase:
    if not isinstance(report, dict):
        raise ScoreParseError("invalid_report", "评分报告必须是 JSON 对象")
    entry = report.get(case_key)
    if entry is None:
        raise ScoreParseError("case_missing", f"评分报告不包含用例 {case_key}")
    if not isinstance(entry, dict):
        raise ScoreParseError("invalid_case_entry", "评分报告的用例条目必须是对象")

    tests: list[ParsedTest] = []
    for report_key, group in (("FAIL_TO_PASS", "F2P"), ("PASS_TO_PASS", "P2P")):
        block = _status_block(entry, report_key)
        for native_status in ("success", "failure"):
            for name in _names(block, native_status):
                tests.append(_test_entry(name, group, native_status))

    resolved = entry.get("resolved")
    if resolved is True:
        result_status = "resolved"
    elif resolved is False:
        result_status = "unresolved"
    else:
        result_status = "not_scored"
    failure = _parse_failure(entry)
    legacy_failure = entry.get("error") or entry.get("failure_reason")
    failure_message = failure.message if failure and failure.message else legacy_failure
    return ParsedCase(
        case_key=case_key,
        result_status=result_status,
        tests=tuple(tests),
        failure_message=str(failure_message)[:FAILURE_MESSAGE_LIMIT] if failure_message else None,
        failure=failure,
    )


def empty_case(case_key: str) -> ParsedCase:
    return ParsedCase(case_key=case_key, result_status="not_scored")
