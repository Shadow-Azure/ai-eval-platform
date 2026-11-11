"""M6-03: Claude Code native trace normalization and relation recovery."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from app.models import TraceFile, TraceSession, TraceSpan
from app.services import trace_ingestion
from app.services.trace_parsers import (
    DEFAULT_REGISTRY,
    ClaudeCodeParser,
    ParseInput,
    ParserRegistry,
)
from object_stub import StubObjectStore
from sqlalchemy import select
from trace_ingest_helpers import attach_manifest, create_env, metadata_object, trace_object

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "traces" / "claude-code" / "2.1.278"


@pytest.fixture
def claude_trace_env(session, configure_settings):
    with StubObjectStore() as store:
        configure_settings(
            S3_ENDPOINT=store.endpoint,
            S3_ACCESS_KEY="ak",
            S3_SECRET_KEY="sk",
        )
        yield create_env(session, store)


def _input(
    path: str,
    *,
    fixture_name: str | None = None,
    artifact_kind: str = "trace",
) -> ParseInput:
    content = (FIXTURES / (fixture_name or Path(path).name)).read_bytes()
    return ParseInput(
        runtime_key="claude-code",
        runtime_version="2.1.278",
        native_format=(
            "claude-code.subagent.meta+json" if artifact_kind == "trace_metadata" else "claude-code.session+jsonl"
        ),
        format_version="1",
        detected_format="json" if artifact_kind == "trace_metadata" else "jsonl",
        logical_path=path,
        object_key=f"fixtures/{path}",
        sha256="f" * 64,
        content_type=("application/json" if artifact_kind == "trace_metadata" else "application/x-ndjson"),
        size=len(content),
        artifact_kind=artifact_kind,
    )


def _parse(name: str, *, logical_path: str | None = None, artifact_kind: str = "trace"):
    logical_path = logical_path or f"native/projects/-testbed/{name}"
    context = _input(logical_path, fixture_name=name, artifact_kind=artifact_kind)
    return ClaudeCodeParser().parse((FIXTURES / name).read_bytes(), context)


def _session(env, path: str, *, kind: str = "trace", native_format: str | None = None):
    name = Path(path).name
    if name == "session-root.jsonl":
        fixture_name = "root-parallel.jsonl"
    elif name == "session-duplicates.jsonl":
        fixture_name = "duplicate-and-fragment.jsonl"
    elif name == "session-root.resume.jsonl":
        fixture_name = "resume-root.jsonl"
    elif name == "agent-agent-alpha.jsonl":
        fixture_name = "subagent-agent-alpha.jsonl"
    elif name == "agent-agent-beta.jsonl":
        fixture_name = "subagent-agent-beta.jsonl"
    elif name == "agent-agent-alpha.meta.json":
        fixture_name = "agent-alpha.meta.json"
    elif name == "agent-agent-beta.meta.json":
        fixture_name = "agent-beta.meta.json"
    else:
        fixture_name = name
    content = (FIXTURES / fixture_name).read_bytes()
    return trace_object(
        env,
        logical_path=path,
        content=content,
        kind=kind,
        native_format=(
            native_format
            or ("claude-code.subagent.meta+json" if kind == "trace_metadata" else "claude-code.session+jsonl")
        ),
        content_type=("application/json" if kind == "trace_metadata" else "application/x-ndjson"),
    )


def _rows(session, attempt):
    files = list(session.scalars(select(TraceFile).where(TraceFile.eval_case_attempt_id == attempt.id)))
    rows = list(session.scalars(select(TraceSession).where(TraceSession.eval_case_attempt_id == attempt.id)))
    spans = list(session.scalars(select(TraceSpan).where(TraceSpan.session_id.in_([row.id for row in rows]))))
    return files, rows, spans


def test_registry_resolves_versioned_claude_parser() -> None:
    parser = DEFAULT_REGISTRY.resolve(
        runtime_key="claude-code",
        detected_format="jsonl",
        format_version="1",
    )
    assert parser.parser_version == "claude-code-2.1.278-v1"
    assert isinstance(parser, ClaudeCodeParser)


def test_parser_normalizes_native_spans_and_final_usage() -> None:
    parsed = _parse(
        "root-parallel.jsonl",
        logical_path="native/projects/-testbed/session-root.jsonl",
    )

    assert parsed.status == "ready"
    assert len(parsed.sessions) == 1
    session = parsed.sessions[0]
    assert session.native_session_id == "session-root"
    assert session.agent_id is None
    assert session.metadata["final_usage"] == {
        "input_tokens": 130,
        "output_tokens": 30,
        "cost_usd": "0.0125",
    }

    by_id = {span.native_event_id: span for span in session.spans}
    agent_span = by_id["agent:session-root"]
    assert agent_span.kind == "AGENT"
    assert agent_span.input_tokens == 130
    assert agent_span.output_tokens == 30
    assert agent_span.cost == Decimal("0.0125")
    assert agent_span.attributes["gen_ai.operation.name"] == "invoke_agent"
    assert agent_span.attributes["eval.parser.version"] == "claude-code-2.1.278-v1"

    llm = by_id["message:msg-root-1"]
    assert llm.kind == "LLM"
    assert llm.input_tokens == 100
    assert llm.output_tokens == 20
    assert llm.attributes["gen_ai.request.model"] == "claude-sonnet-4"
    assert llm.attributes["openinference.span.kind"] == "LLM"

    agent_tools = [
        span for span in session.spans if span.kind == "AGENT" and span.attributes.get("gen_ai.tool.name") == "Agent"
    ]
    assert {span.attributes["gen_ai.tool.call.id"] for span in agent_tools} == {
        "call-alpha",
        "call-beta",
    }
    assert {span.attributes["claude.child.agent_id"] for span in agent_tools} == {
        "agent-alpha",
        "agent-beta",
    }
    assert {span.kind for span in session.spans} >= {
        "AGENT",
        "LLM",
        "TOOL",
        "MESSAGE",
        "THINKING",
    }


def test_parallel_subagents_require_root_and_meta_evidence(claude_trace_env) -> None:
    env = claude_trace_env
    attempt = env["attempt"]
    base = "native/projects/-testbed/session-root"
    objects = [
        _session(env, f"{base}.jsonl"),
        _session(
            env,
            f"{base}/subagents/agent-agent-alpha.meta.json",
            kind="trace_metadata",
        ),
        _session(env, f"{base}/subagents/agent-agent-alpha.jsonl"),
        _session(
            env,
            f"{base}/subagents/agent-agent-beta.meta.json",
            kind="trace_metadata",
        ),
        _session(env, f"{base}/subagents/agent-agent-beta.jsonl"),
    ]
    attach_manifest(env["session"], attempt, objects)

    result = trace_ingestion.ingest_attempt(
        env["session"], attempt, parser_registry=ParserRegistry((ClaudeCodeParser(),))
    )

    assert result["trace_status"] == "ready", result
    _files, sessions, spans = _rows(env["session"], attempt)
    by_key = {row.native_session_key: row for row in sessions}
    assert set(by_key) == {
        "session-root:root",
        "session-root:agent-alpha",
        "session-root:agent-beta",
    }
    root = by_key["session-root:root"]
    alpha = by_key["session-root:agent-alpha"]
    beta = by_key["session-root:agent-beta"]
    assert alpha.parent_session_id == root.id
    assert beta.parent_session_id == root.id
    assert alpha.agent_name == "Explore"
    assert beta.agent_name == "general-purpose"
    assert alpha.metadata_json["spawn_depth"] == 1
    assert alpha.relation_evidence_json["confidence"] == "authoritative"
    assert alpha.relation_evidence_json["tool_use_id"] == "call-alpha"
    assert beta.relation_evidence_json["tool_use_id"] == "call-beta"
    assert alpha.ordinal < beta.ordinal

    child_spans = [span for span in spans if span.session_id == alpha.id]
    final = next(span for span in child_spans if span.native_event_id == "agent:agent-alpha")
    assert (final.input_tokens, final.output_tokens) == (16, 10)
    assert final.cost == Decimal("0.0025")


def test_duplicate_message_is_one_llm_span_and_orphan_is_fragment(
    claude_trace_env,
) -> None:
    env = claude_trace_env
    attempt = env["attempt"]
    obj = _session(
        env,
        "native/projects/-testbed/session-duplicates.jsonl",
    )
    attach_manifest(env["session"], attempt, [obj])

    trace_ingestion.ingest_attempt(env["session"], attempt, parser_registry=ParserRegistry((ClaudeCodeParser(),)))

    _files, sessions, spans = _rows(env["session"], attempt)
    assert len(sessions) == 1
    llms = [span for span in spans if span.native_event_id == "message:msg-duplicate"]
    assert len(llms) == 1
    assert llms[0].input_tokens == 50
    assert llms[0].output_tokens == 5

    llm = llms[0]
    assert llm.parent_span_id is None
    assert llm.attributes_json["trace.fragment_root"] is True
    assert llm.attributes_json["trace.parent_uuid"] == "missing-parent"
    first_thinking = next(span for span in spans if span.native_event_id == "dup-a1:block:0")
    assert first_thinking.parent_span_id == llm.id

    tool_results = [
        span
        for span in spans
        if span.kind == "TOOL"
        and span.name == "tool_result"
        and span.attributes_json.get("gen_ai.tool.call.id") == "large-tool"
    ]
    assert len(tool_results) == 1
    preview = tool_results[0].content_preview or ""
    assert "sk-verysecretvalue123" not in preview
    assert "supersecret" not in preview
    assert "[REDACTED]" in preview
    assert tool_results[0].attributes_json["content.truncated"] is True
    assert tool_results[0].attributes_json["content.externalized"] is True
    assert tool_results[0].attributes_json["content.locator"] == "dup-r1:block:0"
    assert tool_results[0].content_object_key
    assert tool_results[0].attributes_json["content.media_type"] == "text/plain"
    assert "html" not in tool_results[0].attributes_json

    final = next(span for span in spans if span.native_event_id == "agent:session-duplicates")
    assert final.cost == Decimal("0.0042")


def test_same_session_without_root_and_meta_evidence_is_unlinked(
    claude_trace_env,
) -> None:
    env = claude_trace_env
    attempt = env["attempt"]
    objects = [
        _session(
            env,
            "native/projects/-testbed/session-root/subagents/agent-agent-alpha.jsonl",
        ),
        _session(
            env,
            "native/projects/-testbed/session-root/subagents/agent-agent-alpha.meta.json",
            kind="trace_metadata",
        ),
    ]
    attach_manifest(env["session"], attempt, objects)

    result = trace_ingestion.ingest_attempt(
        env["session"], attempt, parser_registry=ParserRegistry((ClaudeCodeParser(),))
    )

    assert result["trace_status"] == "degraded"
    _files, sessions, _spans = _rows(env["session"], attempt)
    child = sessions[0]
    assert child.native_session_id == "session-root"
    assert child.agent_id == "agent-alpha"
    assert child.parent_session_id is None
    assert child.status == "unlinked"
    assert child.relation_evidence_json["reason"] == "missing_root_spawn_evidence"


def test_resume_file_with_same_session_id_does_not_become_subagent(
    claude_trace_env,
) -> None:
    env = claude_trace_env
    attempt = env["attempt"]
    objects = [
        _session(env, "native/projects/-testbed/session-root.jsonl"),
        _session(env, "native/projects/-testbed/session-root.resume.jsonl"),
    ]
    attach_manifest(env["session"], attempt, objects, capture_status="partial")

    trace_ingestion.ingest_attempt(env["session"], attempt, parser_registry=ParserRegistry((ClaudeCodeParser(),)))

    _files, sessions, _spans = _rows(env["session"], attempt)
    assert len(sessions) == 2
    resume = next(row for row in sessions if row.metadata_json.get("native_agent_kind") == "resume")
    assert resume.native_session_id == "session-root"
    assert resume.parent_session_id is None
    assert resume.agent_id and resume.agent_id.startswith("resume:")


def test_unknown_claude_format_version_degrades_without_sessions() -> None:
    content = (FIXTURES / "root-parallel.jsonl").read_bytes()
    context = _input(
        "native/projects/-testbed/session-root.jsonl",
        fixture_name="root-parallel.jsonl",
    )
    context = ParseInput(**{**context.__dict__, "format_version": "future-999"})

    parsed = ClaudeCodeParser().parse(content, context)

    assert parsed.status == "degraded"
    assert parsed.error_code == "unknown_format_version"
    assert parsed.sessions == []
    assert "future-999" in (parsed.error_summary or "")
    parser = DEFAULT_REGISTRY.resolve(
        runtime_key="claude-code",
        detected_format="jsonl",
        format_version="future-999",
    )
    assert parser.parser_version == "claude-code-2.1.278-v1"


def test_unknown_claude_native_format_degrades_without_guessing() -> None:
    context = ParseInput(
        runtime_key="claude-code",
        runtime_version="2.1.278",
        native_format="application/octet-stream",
        format_version="1",
        detected_format="unknown",
        logical_path="native/opaque.bin",
        object_key="fixtures/opaque.bin",
        sha256="f" * 64,
        content_type="application/octet-stream",
        size=8,
        artifact_kind="trace",
    )

    parsed = ClaudeCodeParser().parse(b"opaque\n", context)

    assert parsed.status == "degraded"
    assert parsed.error_code == "unknown_format"
    assert parsed.sessions == []
    parser = DEFAULT_REGISTRY.resolve(
        runtime_key="claude-code",
        detected_format="unknown",
        format_version="1",
    )
    assert isinstance(parser, ClaudeCodeParser)


def test_unknown_claude_format_is_preserved_as_degraded_file(
    claude_trace_env,
) -> None:
    env = claude_trace_env
    attempt = env["attempt"]
    obj = trace_object(
        env,
        logical_path="native/opaque.bin",
        content=b"opaque\n",
        native_format=None,
        content_type="application/octet-stream",
    )
    attach_manifest(env["session"], attempt, [obj])

    result = trace_ingestion.ingest_attempt(
        env["session"],
        attempt,
        parser_registry=ParserRegistry((ClaudeCodeParser(),)),
    )

    assert result["trace_status"] == "degraded"
    files, sessions, spans = _rows(env["session"], attempt)
    assert files[0].status == "degraded"
    assert files[0].error_code == "unknown_format"
    assert sessions == []
    assert spans == []


def test_subagent_filename_and_record_agent_id_must_match() -> None:
    content = (FIXTURES / "subagent-agent-alpha.jsonl").read_bytes()
    context = _input(
        "native/projects/-testbed/session-root/subagents/agent-agent-other.jsonl",
        fixture_name="subagent-agent-alpha.jsonl",
    )

    parsed = ClaudeCodeParser().parse(content, context)

    assert parsed.status == "degraded"
    assert parsed.error_code == "agent_id_mismatch"
    assert parsed.sessions == []


def test_duplicate_tool_call_with_new_record_uuid_is_deduplicated() -> None:
    records = [json.loads(line) for line in (FIXTURES / "root-parallel.jsonl").read_text().splitlines()]
    assistant = next(record for record in records if record.get("uuid") == "a-root")
    duplicate = json.loads(json.dumps(assistant))
    duplicate["uuid"] = "a-root-copy"
    records.append(duplicate)
    content = ("\n".join(json.dumps(record) for record in records) + "\n").encode()
    context = _input(
        "native/projects/-testbed/session-root.jsonl",
        fixture_name="root-parallel.jsonl",
    )

    parsed = ClaudeCodeParser().parse(content, context)

    assert parsed.status == "ready"
    tool_calls = [span for span in parsed.sessions[0].spans if span.native_event_id == "tool:call-alpha"]
    assert len(tool_calls) == 1


def test_metadata_tool_use_mismatch_keeps_child_unlinked(claude_trace_env) -> None:
    env = claude_trace_env
    attempt = env["attempt"]
    bad_meta = metadata_object(
        env,
        logical_path=("native/projects/-testbed/session-root/subagents/agent-agent-alpha.meta.json"),
        payload={"agentType": "Explore", "toolUseId": "call-wrong", "spawnDepth": 1},
    )
    objects = [
        _session(env, "native/projects/-testbed/session-root.jsonl"),
        _session(
            env,
            "native/projects/-testbed/session-root/subagents/agent-agent-alpha.jsonl",
        ),
        bad_meta,
    ]
    attach_manifest(env["session"], attempt, objects)

    result = trace_ingestion.ingest_attempt(
        env["session"], attempt, parser_registry=ParserRegistry((ClaudeCodeParser(),))
    )

    assert result["trace_status"] == "degraded"
    _files, sessions, _spans = _rows(env["session"], attempt)
    child = next(row for row in sessions if row.agent_id == "agent-alpha")
    assert child.parent_session_id is None
    assert child.status == "unlinked"
    assert child.relation_evidence_json["reason"] == "metadata_tool_use_mismatch"


def test_missing_child_file_degrades_root_and_keeps_raw_provenance(
    claude_trace_env,
) -> None:
    env = claude_trace_env
    attempt = env["attempt"]
    obj = _session(env, "native/projects/-testbed/session-root.jsonl")
    attach_manifest(env["session"], attempt, [obj], capture_status="partial")

    result = trace_ingestion.ingest_attempt(
        env["session"], attempt, parser_registry=ParserRegistry((ClaudeCodeParser(),))
    )

    assert result["trace_status"] == "incomplete"
    files, sessions, _spans = _rows(env["session"], attempt)
    root_file = next(row for row in files if row.artifact_kind == "trace")
    assert root_file.status == "degraded"
    assert root_file.error_code == "missing_child_trace"
    root = sessions[0]
    assert root.status == "degraded"
    assert root.relation_evidence_json["missing_child_agent_ids"] == [
        "agent-alpha",
        "agent-beta",
    ]
    assert root_file.object_key and root_file.sha256


def test_missing_metadata_keeps_child_unlinked(claude_trace_env) -> None:
    env = claude_trace_env
    attempt = env["attempt"]
    objects = [
        _session(env, "native/projects/-testbed/session-root.jsonl"),
        _session(
            env,
            "native/projects/-testbed/session-root/subagents/agent-agent-alpha.jsonl",
        ),
    ]
    attach_manifest(env["session"], attempt, objects)

    result = trace_ingestion.ingest_attempt(
        env["session"], attempt, parser_registry=ParserRegistry((ClaudeCodeParser(),))
    )

    assert result["trace_status"] == "degraded"
    _files, sessions, _spans = _rows(env["session"], attempt)
    child = next(row for row in sessions if row.agent_id == "agent-alpha")
    assert child.parent_session_id is None
    assert child.status == "unlinked"
    assert child.relation_evidence_json["reason"] == "missing_metadata"


def test_reingesting_claude_files_does_not_duplicate_tree(claude_trace_env) -> None:
    env = claude_trace_env
    attempt = env["attempt"]
    base = "native/projects/-testbed/session-root"
    objects = [
        _session(env, f"{base}.jsonl"),
        _session(
            env,
            f"{base}/subagents/agent-agent-alpha.meta.json",
            kind="trace_metadata",
        ),
        _session(env, f"{base}/subagents/agent-agent-alpha.jsonl"),
        _session(
            env,
            f"{base}/subagents/agent-agent-beta.meta.json",
            kind="trace_metadata",
        ),
        _session(env, f"{base}/subagents/agent-agent-beta.jsonl"),
    ]
    attach_manifest(env["session"], attempt, objects)
    registry = ParserRegistry((ClaudeCodeParser(),))

    trace_ingestion.ingest_attempt(env["session"], attempt, parser_registry=registry)
    _files, first_sessions, first_spans = _rows(env["session"], attempt)
    first_counts = (
        {(row.native_session_key, row.ordinal) for row in first_sessions},
        {
            (
                next(
                    session_row.native_session_key
                    for session_row in first_sessions
                    if session_row.id == span.session_id
                ),
                span.native_event_id,
                span.ordinal,
            )
            for span in first_spans
        },
    )

    second = trace_ingestion.ingest_attempt(env["session"], attempt, parser_registry=registry)
    _files, second_sessions, second_spans = _rows(env["session"], attempt)

    assert second["replaced"] is True
    second_counts = (
        {(row.native_session_key, row.ordinal) for row in second_sessions},
        {
            (
                next(
                    session_row.native_session_key
                    for session_row in second_sessions
                    if session_row.id == span.session_id
                ),
                span.native_event_id,
                span.ordinal,
            )
            for span in second_spans
        },
    )
    assert second_counts == first_counts


def test_partial_claude_tree_is_atomically_replayed_when_children_arrive(
    claude_trace_env,
) -> None:
    env = claude_trace_env
    attempt = env["attempt"]
    base = "native/projects/-testbed/session-root"
    root = _session(env, f"{base}.jsonl")
    alpha_meta = _session(
        env,
        f"{base}/subagents/agent-agent-alpha.meta.json",
        kind="trace_metadata",
    )
    attach_manifest(
        env["session"],
        attempt,
        [root, alpha_meta],
        capture_status="partial",
    )
    registry = ParserRegistry((ClaudeCodeParser(),))
    trace_ingestion.ingest_attempt(env["session"], attempt, parser_registry=registry)
    _files, initial_sessions, _spans = _rows(env["session"], attempt)
    assert len(initial_sessions) == 1

    children = [
        _session(env, f"{base}/subagents/agent-agent-alpha.jsonl"),
        _session(
            env,
            f"{base}/subagents/agent-agent-beta.meta.json",
            kind="trace_metadata",
        ),
        _session(env, f"{base}/subagents/agent-agent-beta.jsonl"),
    ]
    attach_manifest(env["session"], attempt, [root, alpha_meta, *children])

    result = trace_ingestion.ingest_attempt(env["session"], attempt, parser_registry=registry)

    assert result["replaced"] is True
    assert result["trace_status"] == "ready"
    _files, sessions, _spans = _rows(env["session"], attempt)
    by_key = {row.native_session_key: row for row in sessions}
    assert by_key["session-root:agent-alpha"].parent_session_id == by_key["session-root:root"].id
    assert by_key["session-root:agent-beta"].parent_session_id == by_key["session-root:root"].id
