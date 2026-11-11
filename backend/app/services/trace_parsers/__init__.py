"""Trace parser registry and format detection (M6-02)."""

from __future__ import annotations

from pathlib import PurePosixPath

from .base import ParsedFile, ParsedSession, ParsedSpan, ParseError, ParseInput, TraceParser
from .generic import GenericJsonlParser


def detect_format(
    *,
    logical_path: str | None = None,
    content_type: str | None = None,
    native_format: str | None = None,
    data: bytes | None = None,
) -> str:
    """Detect a native format without guessing beyond cheap structural clues."""

    hints: list[str] = []
    suffix = PurePosixPath(logical_path or "").suffix.lower()
    if suffix:
        hints.append(suffix)
    for value in (native_format, content_type):
        if value:
            hints.append(value.lower())
    joined = " ".join(hints)
    if "jsonl" in joined or "ndjson" in joined:
        return "jsonl"
    if suffix == ".json" or "json" in joined:
        return "json"
    if "text" in joined or "log" in joined:
        return "text"
    if data:
        stripped = data.lstrip()
        if stripped.startswith(b"{"):
            return "jsonl"
        if stripped.startswith(b"["):
            return "json"
    return "unknown"


class ParserRegistry:
    """Resolve a parser by runtime, detected format and format version.

    ``parser_version`` is the replay key: callers may pin an older version to
    reproduce a previous import, while the default uses the latest matching
    parser. Unknown runtime/format combinations fall back to the generic
    parser registered for ``*``.
    """

    def __init__(self, parsers: tuple[TraceParser, ...] = ()) -> None:
        self._parsers = list(parsers)

    def register(self, parser: TraceParser) -> None:
        self._parsers.append(parser)

    def _matching(
        self,
        *,
        runtime_key: str,
        detected_format: str,
        format_version: str | None,
    ) -> list[TraceParser]:
        return [
            parser
            for parser in self._parsers
            if ("*" in parser.runtime_keys or runtime_key in parser.runtime_keys)
            and ("*" in parser.formats or detected_format in parser.formats)
            and (
                "*" in parser.format_versions
                or format_version is None
                or format_version in parser.format_versions
            )
        ]

    def resolve(
        self,
        *,
        runtime_key: str,
        detected_format: str,
        format_version: str | None = None,
        parser_version: str | None = None,
    ) -> TraceParser:
        candidates = self._matching(
            runtime_key=runtime_key,
            detected_format=detected_format,
            format_version=format_version,
        )
        if parser_version is not None:
            for parser in candidates:
                if parser.parser_version == parser_version:
                    return parser
            for parser in self._parsers:
                if parser.parser_version == parser_version:
                    return parser
            raise LookupError(
                f"没有指定版本的轨迹解析器：{parser_version}"
            )
        if candidates:
            return candidates[-1]
        raise LookupError(f"没有匹配的轨迹解析器：{runtime_key}/{detected_format}")

    @property
    def default(self) -> TraceParser:
        for parser in self._parsers:
            if parser.parser_version == "raw-jsonl-1":
                return parser
        raise LookupError("默认轨迹解析器未注册")


DEFAULT_REGISTRY = ParserRegistry((GenericJsonlParser(),))

__all__ = [
    "DEFAULT_REGISTRY",
    "ParseError",
    "ParseInput",
    "ParsedFile",
    "ParsedSession",
    "ParsedSpan",
    "ParserRegistry",
    "TraceParser",
    "detect_format",
]
