"""Centralized model gateway (M3-02, design 04 §7).

Runners call this endpoint with a short-lived token minted at manifest time.
The gateway verifies the token, re-checks the run/shard/attempt binding,
decrypts the provider credential, and proxies the request. Provider keys never
leave this process and are never echoed in errors or usage rows.

Two wire protocols are exposed because M3 needs both:

- ``POST /model-gateway/v1/chat/completions`` — OpenAI compatible
- ``POST /model-gateway/anthropic/v1/messages`` — Anthropic compatible, used
  by Claude Code via ``ANTHROPIC_BASE_URL``

Both routes share token validation, model allow-listing, budget checks,
timeouts and usage accounting.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy.orm import Session

from ..clients import get_gateway_client
from ..config import get_settings
from ..db import SessionLocal, get_session
from ..model_gateway import service
from ..model_gateway.tokens import (
    GatewayClaims,
    GatewayTokenError,
    verify_gateway_token,
)
from ..models import ModelConfig
from ..secrets import get_secret_box

router = APIRouter(tags=["model-gateway"])


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "request_id": None}},
    )


def _gateway_response(error: service.GatewayError) -> JSONResponse:
    return _error_response(error.status_code, error.code, error.message)


def get_session_factory():
    return SessionLocal


def _request_token(request: Request) -> str:
    authorization = request.headers.get("authorization")
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
        if token:
            return token
    # Claude Code may send the token as x-api-key (ANTHROPIC_AUTH_TOKEN).
    api_key = request.headers.get("x-api-key")
    if api_key:
        return api_key.strip()
    raise GatewayTokenError("missing_token", "缺少模型网关短期 token")


def _authorize(
    request: Request, session: Session
) -> tuple[GatewayClaims, ModelConfig, str]:
    settings = get_settings()
    claims = verify_gateway_token(_request_token(request), settings.gateway_secret)
    service.validate_claims_binding(session, claims)
    config = service.resolve_config(session, claims)
    try:
        service.ensure_budget(session, claims)
    except service.GatewayError as error:
        if error.code == "budget_paused":
            try:
                service.record_usage(session, claims, config, status="budget_paused")
            except Exception:
                session.rollback()
        raise
    secret = service.read_secret(session, config, get_secret_box())
    return claims, config, secret


def _record(session: Session, claims: GatewayClaims, config: ModelConfig, **kwargs) -> None:
    try:
        service.record_usage(session, claims, config, **kwargs)
    except Exception:
        # Usage accounting must never turn a successful model response into a
        # failed request; a missing row is visible in reconciliation.
        session.rollback()


def _tracked_stream(
    body: AsyncIterator[bytes], usage: dict[str, int | bool], *, anthropic: bool
) -> AsyncIterator[bytes]:
    """Yield upstream bytes while accumulating usage from SSE events."""

    async def generator() -> AsyncIterator[bytes]:
        prompt = completion = 0
        saw_done = False
        buffer = b""
        async for chunk in body:
            yield chunk
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                line = line.strip()
                if not line.startswith(b"data:"):
                    continue
                raw = line[5:].strip()
                if not raw:
                    continue
                if raw == b"[DONE]":
                    saw_done = True
                    continue
                try:
                    event = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                if anthropic:
                    if event.get("type") == "message_start":
                        event_prompt, event_completion = service.anthropic_usage_from_payload(event)
                        prompt = event_prompt or prompt
                        completion = event_completion or completion
                    elif event.get("type") == "message_delta":
                        _, event_completion = service.anthropic_usage_from_payload(event)
                        completion = event_completion or completion
                    elif event.get("type") == "message_stop":
                        saw_done = True
                else:
                    event_prompt, event_completion = service.usage_from_payload(event)
                    if event_prompt or event_completion:
                        prompt, completion = event_prompt, event_completion
        usage.update(prompt=prompt, completion=completion, done=saw_done)

    return generator()


async def _forward(
    *,
    request: Request,
    session: Session,
    client: httpx.AsyncClient,
    session_factory,
    claims: GatewayClaims,
    config: ModelConfig,
    secret: str,
    payload: dict,
    anthropic: bool,
) -> Response:
    model = payload.get("model")
    if not isinstance(model, str) or not model:
        return _error_response(400, "model_required", "请求缺少 model")
    try:
        service.check_model_allowed(model, claims, config)
    except service.GatewayError as error:
        return _gateway_response(error)

    stream = bool(payload.get("stream"))
    provider_headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream" if stream else "application/json",
    }
    if anthropic:
        provider_headers["x-api-key"] = secret
        provider_headers["anthropic-version"] = request.headers.get(
            "anthropic-version", "2023-06-01"
        )
        beta = request.headers.get("anthropic-beta")
        if beta:
            provider_headers["anthropic-beta"] = beta
        url = service.upstream_messages_url(config.base_url)
    else:
        provider_headers["Authorization"] = f"Bearer {secret}"
        url = service.upstream_url(config.base_url)

    try:
        upstream_request = client.build_request("POST", url, json=payload, headers=provider_headers)
        upstream = await client.send(upstream_request, stream=stream)
    except httpx.HTTPError:
        _record(session, claims, config, status="failed")
        return _error_response(503, "provider_unavailable", "模型供应商不可达")

    request_id = service.provider_request_id(upstream)
    if upstream.status_code >= 400:
        await upstream.aread()
        _record(session, claims, config, status="failed", request_id=request_id)
        return _gateway_response(service.normalize_upstream_error(upstream.status_code))

    if not stream:
        content = await upstream.aread()
        try:
            data = json.loads(content)
        except ValueError:
            data = {}
        if anthropic:
            prompt, completion = service.anthropic_usage_from_payload(data)
        else:
            prompt, completion = service.usage_from_payload(data)
        _record(
            session,
            claims,
            config,
            status="succeeded",
            prompt_tokens=prompt,
            completion_tokens=completion,
            request_id=request_id,
        )
        response_headers = {"x-request-id": request_id} if request_id else {}
        return Response(
            content=content,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "application/json"),
            headers=response_headers,
        )

    media_type = upstream.headers.get("content-type", "text/event-stream")
    usage: dict[str, int | bool] = {"prompt": 0, "completion": 0, "done": False}
    tracked = _tracked_stream(upstream.aiter_bytes(), usage, anthropic=anthropic)

    async def body() -> AsyncIterator[bytes]:
        try:
            async for chunk in tracked:
                yield chunk
        finally:
            await upstream.aclose()
            try:
                with session_factory() as usage_session:
                    service.record_usage(
                        usage_session,
                        claims,
                        config,
                        status="succeeded" if usage.get("done") else "failed",
                        prompt_tokens=int(usage.get("prompt", 0)),
                        completion_tokens=int(usage.get("completion", 0)),
                        request_id=request_id,
                    )
            except Exception:
                pass

    return StreamingResponse(
        body(),
        media_type=media_type,
        headers={"Cache-Control": "no-cache"},
    )


async def _handle(
    request: Request,
    session: Session,
    client: httpx.AsyncClient,
    session_factory,
    *,
    anthropic: bool,
):
    try:
        payload = await request.json()
    except ValueError:
        return _error_response(400, "invalid_json", "请求体必须是 JSON")
    if not isinstance(payload, dict):
        return _error_response(400, "invalid_json", "请求体必须是 JSON 对象")
    try:
        claims, config, secret = _authorize(request, session)
    except GatewayTokenError as error:
        return _error_response(401, error.code, error.message)
    except service.GatewayError as error:
        return _gateway_response(error)
    return await _forward(
        request=request,
        session=session,
        client=client,
        session_factory=session_factory,
        claims=claims,
        config=config,
        secret=secret,
        payload=payload,
        anthropic=anthropic,
    )


@router.post("/model-gateway/v1/chat/completions")
async def chat_completions(
    request: Request,
    session: Session = Depends(get_session),
    client: httpx.AsyncClient = Depends(get_gateway_client),
    session_factory=Depends(get_session_factory),
):
    """OpenAI-compatible proxy used by the runner."""

    return await _handle(request, session, client, session_factory, anthropic=False)


@router.post("/model-gateway/anthropic/v1/messages")
async def anthropic_messages(
    request: Request,
    session: Session = Depends(get_session),
    client: httpx.AsyncClient = Depends(get_gateway_client),
    session_factory=Depends(get_session_factory),
):
    """Anthropic-compatible proxy used by Claude Code via ANTHROPIC_BASE_URL."""

    return await _handle(request, session, client, session_factory, anthropic=True)
