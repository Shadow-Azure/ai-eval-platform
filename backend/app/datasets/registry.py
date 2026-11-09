"""Dataset adapter registry：后续数据集只加适配器、注册即可用。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import DatasetAdapter

_REGISTRY: dict[str, DatasetAdapter] = {}


class AdapterNotFound(LookupError):
    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(f"未注册的数据集适配器：{key}")


def register_adapter(adapter: DatasetAdapter) -> None:
    _REGISTRY[adapter.key] = adapter


def unregister_adapter(key: str) -> None:
    _REGISTRY.pop(key, None)


def get_adapter(key: str) -> DatasetAdapter:
    adapter = _REGISTRY.get(key)
    if adapter is None:
        raise AdapterNotFound(key)
    return adapter


def registered_adapter_keys() -> list[str]:
    return sorted(_REGISTRY)
