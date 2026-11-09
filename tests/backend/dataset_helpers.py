"""Shared dataset-test helpers：读取 vendored 真实切片 fixture。"""

from __future__ import annotations

import json
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "swebench" / "verified-slice.jsonl"


def fixture_records() -> list[dict]:
    """真实 SWE-bench Verified 前 6 条（逐字保留上游字段）。"""

    return [json.loads(line) for line in FIXTURE.read_text().splitlines() if line.strip()]
