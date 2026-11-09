"""Shared HTTP client dependencies for provider calls."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from .config import get_settings


async def get_gateway_client() -> AsyncIterator[httpx.AsyncClient]:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=settings.gateway_timeout_seconds) as client:
        yield client


async def get_provider_client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        yield client
