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
class ParsedCase:
    case_key: str
    result_status: str
    tests: tuple[ParsedTest, ...] = field(default_factory=tuple)
    failure_message: str | None = None


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
    failure_message = entry.get("error") or entry.get("failure_reason")
    return ParsedCase(
        case_key=case_key,
        result_status=result_status,
        tests=tuple(tests),
        failure_message=str(failure_message) if failure_message else None,
    )


def empty_case(case_key: str) -> ParsedCase:
    return ParsedCase(case_key=case_key, result_status="not_scored")
