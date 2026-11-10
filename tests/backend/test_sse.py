"""M3-04 SSE change feed: snapshot cursor replay + terminal close."""

from __future__ import annotations

import json

from case_helpers import auth  # noqa: F401  (keeps runner-token fixtures consistent)


def test_sse_replays_events_and_closes_on_terminal(client, eval_env) -> None:
    run_id = eval_env["run_id"]
    cancelled = client.post(f"/api/v1/evaluations/{run_id}/cancel")
    assert cancelled.status_code == 200

    with client.stream("GET", f"/api/v1/evaluations/{run_id}/events/stream?after_id=0") as response:
        assert response.status_code == 200
        lines = [line for line in response.iter_lines() if line]
    data = [json.loads(line[5:].strip()) for line in lines if line.startswith("data:")]
    assert data
    assert data[0]["event_type"] == "eval.created"
    assert any(item["event_type"] == "eval.cancelled" for item in data)


def test_sse_requires_session(anonymous_client, eval_env) -> None:
    response = anonymous_client.get(f"/api/v1/evaluations/{eval_env['run_id']}/events/stream")
    assert response.status_code == 401


def test_sse_releases_request_session_before_streaming(session, eval_env) -> None:
    """A long-lived SSE must not pin a pooled connection for its lifetime.

    Regression for the QueuePool exhaustion where a handful of open detail
    tabs made every console request answer 500. The route validates the run,
    then closes the request-scoped session before returning the stream.
    """

    from types import SimpleNamespace
    from unittest.mock import patch

    from app.api.evaluations import stream_evaluation_events

    request = SimpleNamespace(headers={})
    with patch.object(session, "close", wraps=session.close) as close_mock:
        stream_evaluation_events(
            eval_env["run_id"], request=request, after_id=0, session=session
        )
    assert close_mock.called
