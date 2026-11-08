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
from ..model_gateway.tokens import TokenError, verify_token
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


@router.post("/model-gateway/v1/chat/completions")
async def chat_completions(
    request: Request,
    session: Session = Depends(get_session),
    client: httpx.AsyncClient = Depends(get_gateway_client),
    session_factory=Depends(get_session_factory),
):
    settings = get_settings()
    try:
        token = service.extract_bearer(request.headers.get("authorization"))
        claims = verify_token(token, settings.gateway_token_secret)
    except TokenError as error:
        return _error_response(401, error.code, error.message)
    except service.GatewayError as error:
        return _gateway_response(error)

    try:
        payload = await request.json()
    except ValueError:
        return _error_response(400, "invalid_json", "请求体必须是 JSON")
    if not isinstance(payload, dict):
        return _error_response(400, "invalid_json", "请求体必须是 JSON 对象")
    model = payload.get("model")
    if not isinstance(model, str) or not model:
        return _error_response(400, "model_required", "请求缺少 model")

    try:
        config = service.resolve_config(session, claims)
        service.check_model_allowed(model, claims, config)
        service.ensure_budget(session, claims)
        secret = service.read_secret(session, config, get_secret_box())
    except service.GatewayError as error:
        if error.code == "budget_paused":
            service.record_usage(
                session,
                claims,
                config,
                status="budget_paused",
            )
        return _gateway_response(error)

    stream = bool(payload.get("stream"))
    upstream_request = client.build_request(
        "POST",
        service.upstream_url(config.base_url),
        json=payload,
        headers={
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
        },
    )
    try:
        upstream = await client.send(upstream_request, stream=stream)
    except httpx.HTTPError as error:
        service.record_usage(session, claims, config, status="failed")
        return _error_response(503, "provider_unavailable", f"模型供应商不可达：{error}")

    request_id = service.provider_request_id(upstream)
    if upstream.status_code >= 400:
        await upstream.aread()
        service.record_usage(
            session, claims, config, status="failed", request_id=request_id
        )
        mapped = service.normalize_upstream_error(upstream.status_code)
        return _gateway_response(mapped)

    if not stream:
        content = await upstream.aread()
        try:
            data = json.loads(content)
        except ValueError:
            data = {}
        prompt, completion = service.usage_from_payload(data)
        service.record_usage(
            session,
            claims,
            config,
            status="succeeded",
            prompt_tokens=prompt,
            completion_tokens=completion,
            request_id=request_id,
        )
        headers = {"x-request-id": request_id} if request_id else {}
        return Response(
            content=content,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "application/json"),
            headers=headers,
        )

    media_type = upstream.headers.get("content-type", "text/event-stream")

    async def body() -> AsyncIterator[bytes]:
        prompt = completion = 0
        saw_done = False
        buffer = b""
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    line = line.strip()
                    if not line.startswith(b"data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == b"[DONE]":
                        saw_done = True
                        continue
                    if not raw:
                        continue
                    try:
                        event = json.loads(raw)
                    except ValueError:
                        continue
                    event_prompt, event_completion = service.usage_from_payload(event)
                    if event_prompt or event_completion:
                        prompt, completion = event_prompt, event_completion
        finally:
            await upstream.aclose()
            try:
                with session_factory() as usage_session:
                    service.record_usage(
                        usage_session,
                        claims,
                        config,
                        status="succeeded" if saw_done else "failed",
                        prompt_tokens=prompt,
                        completion_tokens=completion,
                        request_id=request_id,
                    )
            except Exception:  # usage accounting must not break the stream tail
                pass

    return StreamingResponse(
        body(),
        media_type=media_type,
        headers={"Cache-Control": "no-cache"},
    )


@router.post("/model-gateway/anthropic/v1/messages")
async def anthropic_messages(
    request: Request,
    session: Session = Depends(get_session),
    client: httpx.AsyncClient = Depends(get_gateway_client),
    session_factory=Depends(get_session_factory),
):
    """Anthropic-compatible proxy used by Claude Code via ANTHROPIC_BASE_URL."""

    settings = get_settings()
    try:
        token = service.extract_bearer(request.headers.get("authorization"))
        claims = verify_token(token, settings.gateway_token_secret)
    except TokenError as error:
        return _error_response(401, error.code, error.message)
    except service.GatewayError as error:
        return _gateway_response(error)

    try:
        payload = await request.json()
    except ValueError:
        return _error_response(400, "invalid_json", "请求体必须是 JSON")
    if not isinstance(payload, dict):
        return _error_response(400, "invalid_json", "请求体必须是 JSON 对象")
    model = payload.get("model")
    if not isinstance(model, str) or not model:
        return _error_response(400, "model_required", "请求缺少 model")

    try:
        config = service.resolve_config(session, claims)
        service.check_model_allowed(model, claims, config)
        service.ensure_budget(session, claims)
        secret = service.read_secret(session, config, get_secret_box())
    except service.GatewayError as error:
        if error.code == "budget_paused":
            service.record_usage(session, claims, config, status="budget_paused")
        return _gateway_response(error)

    stream = bool(payload.get("stream"))
    upstream_request = client.build_request(
        "POST",
        service.upstream_messages_url(config.base_url),
        json=payload,
        headers={
            "x-api-key": secret,
            "anthropic-version": request.headers.get("anthropic-version", "2023-06-01"),
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
        },
    )
    try:
        upstream = await client.send(upstream_request, stream=stream)
    except httpx.HTTPError as error:
        service.record_usage(session, claims, config, status="failed")
        return _error_response(503, "provider_unavailable", f"模型供应商不可达：{error}")

    request_id = service.provider_request_id(upstream)
    if upstream.status_code >= 400:
        await upstream.aread()
        service.record_usage(
            session, claims, config, status="failed", request_id=request_id
        )
        mapped = service.normalize_upstream_error(upstream.status_code)
        return _gateway_response(mapped)

    if not stream:
        content = await upstream.aread()
        try:
            data = json.loads(content)
        except ValueError:
            data = {}
        prompt, completion = service.anthropic_usage_from_payload(data)
        service.record_usage(
            session,
            claims,
            config,
            status="succeeded",
            prompt_tokens=prompt,
            completion_tokens=completion,
            request_id=request_id,
        )
        headers = {"x-request-id": request_id} if request_id else {}
        return Response(
            content=content,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "application/json"),
            headers=headers,
        )

    media_type = upstream.headers.get("content-type", "text/event-stream")

    async def body() -> AsyncIterator[bytes]:
        prompt = completion = 0
        saw_done = False
        buffer = b""
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    line = line.strip()
                    if not line.startswith(b"data:"):
                        continue
                    raw = line[5:].strip()
                    if not raw or raw == b"[DONE]":
                        continue
                    try:
                        event = json.loads(raw)
                    except ValueError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    if event.get("type") == "message_start":
                        event_prompt, event_completion = service.anthropic_usage_from_payload(event)
                        prompt = event_prompt or prompt
                        completion = event_completion or completion
                    elif event.get("type") == "message_delta":
                        _, event_completion = service.anthropic_usage_from_payload(event)
                        completion = event_completion or completion
                    elif event.get("type") == "message_stop":
                        saw_done = True
        finally:
            await upstream.aclose()
            try:
                with session_factory() as usage_session:
                    service.record_usage(
                        usage_session,
                        claims,
                        config,
                        status="succeeded" if saw_done else "failed",
                        prompt_tokens=prompt,
                        completion_tokens=completion,
                        request_id=request_id,
                    )
            except Exception:  # usage accounting must not break the stream tail
                pass

    return StreamingResponse(
        body(),
        media_type=media_type,
        headers={"Cache-Control": "no-cache"},
    )
