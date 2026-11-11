"""Small redaction helpers for trace summaries and previews (M6-02)."""

from __future__ import annotations

import re
from typing import Any

MAX_SUMMARY = 1000
MAX_PREVIEW = 500

_SECRET_KEY = (
    r"(?:api[ _-]?key|access[ _-]?key|secret[ _-]?key|authorization|cookie|"
    r"token|password|passwd|secret|private[ _-]?key)"
)
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
        "[REDACTED PRIVATE KEY]",
    ),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer [REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"), "sk-[REDACTED]"),
    (re.compile(r"\bAKIA[A-Z0-9]{16}\b"), "AKIA[REDACTED]"),
    (re.compile(r"(://[^/\s:@]+:)[^/\s@]+@"), r"\1[REDACTED]@"),
    (
        re.compile(
            rf"([\"']?{_SECRET_KEY}[\"']?\s*[:=]\s*)([\"']?)[^\"'\s,;}}]+",
            re.I,
        ),
        r"\1\2[REDACTED]",
    ),
)


def redact_text(value: Any, *, limit: int = MAX_SUMMARY) -> str:
    """Mask common credentials, then truncate to ``limit`` characters."""

    text = "" if value is None else str(value)
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    if len(text) > limit:
        text = text[: max(0, limit - 1)] + "…"
    return text


def summarize_error(value: Any, *, limit: int = 240) -> str:
    """Short, safe error summary; never returns raw line content by itself."""

    return redact_text(value, limit=limit)
