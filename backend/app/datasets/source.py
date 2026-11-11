"""Streaming JSON / JSONL source reader for dataset imports (M5-15).

The import console accepts files up to 1 GB, so a source is never loaded into
memory as one JSON document. Two layouts are supported and detected from the
first non-whitespace byte:

- ``[`` → one JSON array of case objects, decoded incrementally with
  :meth:`json.JSONDecoder.raw_decode`;
- anything else → JSON Lines, one case object per line.

Malformed input is reported per position instead of aborting the whole import:
the validator turns each :class:`SourceParseIssue` into a diagnosable issue and
lets the remaining records be inspected. A structurally broken JSON array (no
closing bracket, trailing garbage) is fatal because later records cannot be
located reliably.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CHUNK_SIZE = 1024 * 1024
# A single case must fit this bound; it keeps a corrupt array from growing the
# read buffer without limit while an opening bracket is never closed.
MAX_RECORD_BYTES = 64 * 1024 * 1024


class SourceFormatError(Exception):
    """Fatal source layout error (the file cannot be read as records)."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass
class SourceRecord:
    """One source position: either a decoded object or a local parse issue."""

    index: int
    line: int | None = None
    value: dict[str, Any] | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.value is not None and self.error is None


def _is_object(value: Any) -> bool:
    return isinstance(value, dict)


def _decode_error(line: int, message: str) -> SourceRecord:
    return SourceRecord(index=line, line=line, error=message)


def iter_source_records(path: Path) -> Iterator[SourceRecord]:
    """Yield records from a JSON array or JSONL file without buffering it all."""

    with path.open("rb") as raw:
        head = raw.read(4096)
        # A UTF-8 BOM is common in files exported by spreadsheet tools.
        if head.startswith(b"\xef\xbb\xbf"):
            head = head[3:]
        if not head.strip():
            raise SourceFormatError("empty_source", "数据文件为空")
        text_head = head.lstrip()
        mode = "json" if text_head.startswith(b"[") else "jsonl"
        raw.seek(0)
        if mode == "json":
            with path.open("r", encoding="utf-8-sig") as source:
                yield from _iter_json_array(source)
        else:
            with path.open("r", encoding="utf-8-sig") as source:
                yield from _iter_json_lines(source)


def source_mode(path: Path) -> str:
    """Return ``json`` or ``jsonl`` for the detected layout (report metadata)."""

    with path.open("rb") as raw:
        head = raw.read(4096)
    if head.startswith(b"\xef\xbb\xbf"):
        head = head[3:]
    if not head.strip():
        raise SourceFormatError("empty_source", "数据文件为空")
    return "json" if head.lstrip().startswith(b"[") else "jsonl"


def _iter_json_lines(source) -> Iterator[SourceRecord]:  # noqa: ANN001
    for line_number, line in enumerate(source, start=1):
        if not line.strip():
            continue
        if len(line.encode("utf-8")) > MAX_RECORD_BYTES:
            yield _decode_error(line_number, "单条记录超过 64 MB 上限")
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            yield _decode_error(line_number, f"第 {line_number} 行不是合法 JSON：{exc.msg}")
            continue
        if not _is_object(value):
            yield _decode_error(line_number, f"第 {line_number} 行必须是 JSON 对象")
            continue
        yield SourceRecord(index=line_number, line=line_number, value=value)


def _iter_json_array(source) -> Iterator[SourceRecord]:  # noqa: ANN001
    decoder = json.JSONDecoder()
    buffer = ""
    position = 0
    index = 0
    eof = False
    started = False
    ended = False

    def fill() -> bool:
        nonlocal buffer, position, eof
        if eof:
            return False
        chunk = source.read(CHUNK_SIZE)
        if not chunk:
            eof = True
            return False
        if position:
            buffer = buffer[position:]
            position = 0
        buffer += chunk
        if len(buffer.encode("utf-8")) > MAX_RECORD_BYTES:
            raise SourceFormatError("record_too_large", "单条记录超过 64 MB 上限")
        return True

    def skip_ws() -> None:
        nonlocal position
        while True:
            while position < len(buffer) and buffer[position] in " \t\r\n":
                position += 1
            if position < len(buffer):
                return
            if not fill():
                return

    def peek() -> str:
        skip_ws()
        if position < len(buffer):
            return buffer[position]
        return ""

    fill()
    if peek() != "[":
        raise SourceFormatError("invalid_json", "JSON 文件必须以数组开头")
    started = True
    position += 1

    while True:
        char = peek()
        if char == "]":
            ended = True
            position += 1
            break
        if char == "":
            raise SourceFormatError("invalid_json", "JSON 数组缺少结束的 ]")
        if index > 0:
            if char != ",":
                raise SourceFormatError("invalid_json", "JSON 数组元素之间缺少逗号")
            position += 1
            if peek() == "]":  # tolerate a trailing comma
                ended = True
                position += 1
                break
        index += 1
        while True:
            try:
                value, end = decoder.raw_decode(buffer, position)
            except json.JSONDecodeError as exc:
                if fill():
                    continue
                raise SourceFormatError(
                    "invalid_json", f"JSON 数组在第 {index} 条记录附近损坏：{exc.msg}"
                ) from exc
            position = end
            break
        if not _is_object(value):
            yield _decode_error(index, f"数组第 {index} 条记录必须是 JSON 对象")
        else:
            yield SourceRecord(index=index, value=value)

    skip_ws()
    if peek() != "" or not ended or not started:
        raise SourceFormatError("invalid_json", "JSON 数组结束后存在多余内容")
