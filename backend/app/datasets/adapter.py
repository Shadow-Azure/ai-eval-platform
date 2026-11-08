"""Dataset adapter contract and registry.

Adapters normalize an upstream source into immutable cases and describe the
metric definitions the dataset version ships with. The registry is a closed
mapping: adding a dataset means adding an adapter module, not branching the
import flow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class AdapterCaseError(ValueError):
    """A single upstream row failed validation; the row is skipped with a report."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class NormalizedCase:
    case_key: str
    repo: str
    base_commit: str
    problem_statement: str
    reference_patch: str
    test_patch: str
    fail_to_pass: tuple[str, ...] = ()
    pass_to_pass: tuple[str, ...] = ()
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class BundleFile:
    path: str
    content: bytes
    visibility: str


@dataclass(frozen=True)
class MetricSpec:
    metric_key: str
    version: int
    display_name: str
    description: str
    aggregator_key: str
    formula_text: str
    display_kind: str = "number"
    unit: str = "count"
    direction: str = "higher"
    denominator_policy: str = "selected_total"
    config: dict = field(default_factory=dict)


class DatasetAdapter(Protocol):
    key: str
    adapter_version: str
    task_family: str
    environment_kind: str

    def parse(self, raw: dict[str, object]) -> NormalizedCase: ...

    def bundle(self, case: NormalizedCase) -> tuple[BundleFile, ...]: ...

    def metric_specs(self) -> tuple[MetricSpec, ...]: ...


_REGISTRY: dict[str, DatasetAdapter] = {}


def register(adapter: DatasetAdapter) -> DatasetAdapter:
    _REGISTRY[adapter.key] = adapter
    return adapter


def get_adapter(key: str) -> DatasetAdapter:
    try:
        return _REGISTRY[key]
    except KeyError as error:
        raise AdapterCaseError(
            "unknown_adapter", f"未注册的数据集适配器：{key}"
        ) from error


def adapter_keys() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


# Importing an adapter module registers it. Keep this at the bottom to avoid
# importing the protocol module from itself.
from . import swebench as _swebench  # noqa: E402,F401
