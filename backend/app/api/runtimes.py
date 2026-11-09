"""Runtime capability + image digest surface for the launch wizard (M3-04)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..config import Settings, get_settings
from ..runtimes.claude_code import ClaudeCodeAdapter

router = APIRouter(tags=["runtimes"])


@router.get("/api/v1/runtimes")
def list_runtimes(settings: Settings = Depends(get_settings)) -> dict:
    adapter = ClaudeCodeAdapter()
    return {
        "items": [
            {
                "key": adapter.runtime_key,
                "version": adapter.runtime_version,
                "adapter_version": adapter.adapter_version,
                "supported_dataset_adapters": list(adapter.supported_dataset_adapters),
                "required_runner_labels": list(adapter.required_runner_labels),
                "execution_image_digest": settings.default_execution_image_digest,
                "scoring_image_digest": settings.default_scoring_image_digest,
            }
        ]
    }
