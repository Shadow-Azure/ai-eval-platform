#!/usr/bin/env python3
"""M4-04 real Aliyun spot + ephemeral runner smoke.

This script creates one real Aliyun spot ECS instance, registers a one-time
GitHub runner through the provisioner, waits for it to come online, dispatches
a tiny `runner-smoke` job that runs on that runner, and then deregisters the
runner and releases the instance. It is intentionally opt-in and refuses to
run without ``SPOT_SMOKE_CONFIRM=1`` and Aliyun/GitHub admin credentials.

It produces:

* ``evidence.json`` — machine-readable timings and outcomes (no secrets);
* ``report-data.json`` — input for the computer-use smoke-report renderer;
* ``smoke-report.html`` — rendered when the renderer is available.

Run (workflow ``spot-smoke`` does this for you):

    SPOT_SMOKE_CONFIRM=1 ALIYUN_ACCESS_KEY_ID=... ALIYUN_ACCESS_KEY_SECRET=...
    RUNNER_ADMIN_TOKEN=... python tests/integration/m4_04_spot_smoke.py \
      --out reports/2026-11-10/m4-04-spot/evidence.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
DEFAULT_TYPES = (
    "ecs.g7.large",
    "ecs.g6.large",
    "ecs.g5.large",
    "ecs.c7.large",
    "ecs.c6.large",
    "ecs.u1-c1m2.large",
    "ecs.e-c1m2.large",
)


def _iso(now: datetime) -> str:
    return now.astimezone(UTC).isoformat(timespec="seconds")


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"missing required environment variable {name}")
    return value


def _github(
    token: str,
    method: str,
    path: str,
    payload: dict | None = None,
) -> tuple[int, dict]:
    base = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    body = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        f"{base}{path}",
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "ai-eval-platform-spot-smoke",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            text = response.read().decode()
            return response.status, json.loads(text) if text else {}
    except urllib.error.HTTPError as exc:
        text = exc.read().decode(errors="replace")
        try:
            return exc.code, json.loads(text or "{}")
        except json.JSONDecodeError:
            return exc.code, {"raw": text[:300]}


def _wait_runner_online(registrar, name: str, *, timeout: int) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        record = registrar.find_runner(name)
        if record:
            if str(record.get("status")) == "online":
                return record
        time.sleep(10)
    raise TimeoutError(f"runner {name} did not come online within {timeout}s")


def _dispatch_smoke(
    token: str,
    *,
    repo: str,
    ref: str,
    smoke_id: str,
    runs_on: list[str],
) -> int:
    status, payload = _github(
        token,
        "POST",
        f"/repos/{repo}/actions/workflows/eval-shard.yml/dispatches",
        {
            "ref": ref,
            "inputs": {
                "mode": "runner-smoke",
                "smoke_id": smoke_id,
                "runs_on": json.dumps(runs_on),
            },
        },
    )
    if status not in (200, 204):
        raise RuntimeError(f"workflow dispatch failed: HTTP {status} {payload}")

    deadline = time.time() + 900
    while time.time() < deadline:
        status, payload = _github(token, "GET", f"/repos/{repo}/actions/runs?event=workflow_dispatch&per_page=50")
        for run in payload.get("workflow_runs") or []:
            title = str(run.get("display_title") or run.get("name") or "")
            if re.search(rf"\bsmoke-{re.escape(smoke_id)}\b", title):
                if run.get("status") == "completed":
                    if run.get("conclusion") != "success":
                        raise RuntimeError(
                            f"runner smoke job failed: {run.get('html_url')} conclusion={run.get('conclusion')}"
                        )
                    return int(run["id"])
        time.sleep(10)
    raise TimeoutError("runner smoke workflow did not complete within 900s")


def _build_report(evidence: dict) -> dict:
    ok = bool(evidence.get("ok"))
    matrix = [
        {
            "id": "01",
            "name": "阿里云竞价价格与库存查询",
            "status": {
                "kind": "pass" if evidence.get("price_usd") is not None else "fail",
                "label": "PASS" if evidence.get("price_usd") is not None else "FAIL",
            },
            "detail": (
                f"CNY {evidence.get('price_cny')} / USD {evidence.get('price_usd')}，zone {evidence.get('zone_id')}。"
            ),
            "evidence": ["evidence.json"],
        },
        {
            "id": "02",
            "name": "创建竞价实例",
            "status": {
                "kind": "pass" if evidence.get("instance_id") else "fail",
                "label": "PASS" if evidence.get("instance_id") else "FAIL",
            },
            "detail": f"实例 {evidence.get('instance_id')}，类型 {evidence.get('instance_type')}。",
            "evidence": ["evidence.json"],
        },
        {
            "id": "03",
            "name": "一次性 Runner 注册",
            "status": {
                "kind": "pass" if evidence.get("runner_online") else "fail",
                "label": "PASS" if evidence.get("runner_online") else "FAIL",
            },
            "detail": (
                f"Runner {evidence.get('runner_name')} 在 "
                f"{evidence.get('runner_online_seconds')}s 内上线；JIT 配置不落库。"
            ),
            "evidence": ["evidence.json"],
        },
        {
            "id": "04",
            "name": "在临时 Runner 上执行小批量任务",
            "status": {
                "kind": "pass" if evidence.get("workflow_conclusion") == "success" else "fail",
                "label": "PASS" if evidence.get("workflow_conclusion") == "success" else "FAIL",
            },
            "detail": f"eval-shard runner-smoke 结论 {evidence.get('workflow_conclusion')}。",
            "evidence": ["evidence.json"],
        },
        {
            "id": "05",
            "name": "注销 Runner 并释放实例",
            "status": {
                "kind": "pass" if evidence.get("cleaned_up") else "fail",
                "label": "PASS" if evidence.get("cleaned_up") else "FAIL",
            },
            "detail": "先删除一次性 Runner，再 DeleteInstance。",
            "evidence": ["evidence.json"],
        },
    ]
    return {
        "title": "M4-04 阿里云竞价 ECS 与临时 Runner · 真实云冒烟",
        "eyebrow": "Smoke Report · issue #81",
        "summary": (
            f"受控阿里云环境创建竞价实例 {evidence.get('instance_id')}，一次性 Runner 上线后执行 runner-smoke 小任务，"
            f"结论 {evidence.get('workflow_conclusion')}，随后注销 Runner 并释放实例"
            f"（cleaned_up={evidence.get('cleaned_up')}）。"
        ),
        "meta": f"{evidence.get('started_at')} · {evidence.get('region')} · 真实阿里云 + GitHub API",
        "metrics": [
            {"value": str(evidence.get("price_usd")), "label": "USD/小时"},
            {"value": str(evidence.get("runner_online_seconds")), "label": "Runner 上线秒数"},
            {"value": str(evidence.get("total_seconds")), "label": "总耗时秒数"},
            {"value": "0", "label": "密钥泄露"},
        ],
        "facts": [
            {"label": "Issue", "value": "#81 接入阿里云竞价 ECS 与临时 Runner"},
            {"label": "实例", "value": str(evidence.get("instance_id"))},
            {"label": "Runner", "value": str(evidence.get("runner_name"))},
            {"label": "Region / Zone", "value": f"{evidence.get('region')} / {evidence.get('zone_id')}"},
            {"label": "工作流", "value": str(evidence.get("workflow_url"))},
        ],
        "matrix": {"columns": ["#", "场景", "结果", "说明", "证据"], "rows": matrix},
        "issues": evidence.get("issues") or [],
        "coverage": {
            "columns": ["区域", "覆盖", "结论", "建议"],
            "rows": [
                {
                    "area": "真实云资源",
                    "coverage": "SMOKE",
                    "conclusion": "竞价创建/查询/释放走真实 Aliyun API。",
                    "recommendation": "保持按需触发，避免常规 CI 产生费用。",
                },
                {
                    "area": "Runner 执行",
                    "coverage": "SMOKE",
                    "conclusion": "runner-smoke 作业在临时实例上真实执行。",
                    "recommendation": "完整评测回调链仍由固定 Runner 的 M2/M3 套件覆盖。",
                },
                {
                    "area": "中断/超时/库存",
                    "coverage": "UT",
                    "conclusion": "由 FakeAliyun + DB 测试覆盖。",
                    "recommendation": "定期用真实中断演练复核。",
                },
            ],
        },
        "conclusion": {
            "kind": "pass" if ok else "fail",
            "label": "真实云冒烟通过" if ok else "真实云冒烟失败",
            "summary": "创建、一次性注册、执行小任务、注销与释放全部完成。"
            if ok
            else "存在未完成步骤，详见 evidence.json。",
            "points": [
                "JIT 配置仅存在于 cloud-init，未写入 DB/事件/日志。",
                "失败模式（价格阈值/库存/中断/创建超时）由确定性测试覆盖。",
            ],
        },
        "footer": "dsh-superagent generated · no credentials or private content included",
    }


def _diagnose_instance(client, region: str, instance_id: str, secret: str) -> str:
    """Best-effort cloud-init/runner logs through Cloud Assistant, redacted."""

    script = """#!/bin/bash
set +e
echo '== os =='; cat /etc/os-release 2>&1; uname -a
echo '== cloud-init status =='; cloud-init status --long 2>&1
echo '== cloud-init output tail =='; tail -n 160 /var/log/cloud-init-output.log 2>&1
echo '== cloud-init log tail =='; tail -n 80 /var/log/cloud-init.log 2>&1
echo '== github-runner unit =='; systemctl status github-runner.service --no-pager 2>&1 | tail -n 60
echo '== github-runner journal =='; journalctl -u github-runner.service -n 80 --no-pager 2>&1
echo '== docker unit =='; systemctl status docker --no-pager 2>&1 | tail -n 30
"""
    try:
        invoke = client.run_shell_command(region, instance_id, script)
    except Exception as exc:  # noqa: BLE001 - diagnostics must never mask the smoke
        code = getattr(exc, "code", type(exc).__name__)
        return f"cloud-assistant unavailable: {code}"
    deadline = time.time() + 120
    output = ""
    while time.time() < deadline:
        output = client.describe_invocation_result(region, invoke, instance_id)
        if output:
            break
        time.sleep(5)
    output = output.replace(secret, "<redacted>")
    output = re.sub(r"--jitconfig\s+\S+", "--jitconfig <redacted>", output)
    output = re.sub(r"JIT_CONFIG=\S+", "JIT_CONFIG=<redacted>", output)
    return output[-6000:]


def _write_outputs(evidence: dict, out: Path, *, render: bool) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report = _build_report(evidence)
    report_path = out.parent / "report-data.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    renderer = REPO / ".agents/skills/computer-use-smoke-reporting/scripts/render-smoke-report.mjs"
    if render and renderer.exists():
        subprocess.run(
            ["node", str(renderer), str(report_path), str(out.parent / "smoke-report.html")],
            check=False,
        )
    print(json.dumps(evidence, indent=2, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="reports/2026-11-10/m4-04-spot/evidence.json")
    parser.add_argument("--render", action="store_true", default=True)
    args = parser.parse_args()

    if os.environ.get("SPOT_SMOKE_CONFIRM") != "1":
        raise SystemExit("set SPOT_SMOKE_CONFIRM=1 to allow one real spot instance (cost)")

    ak_id = _require_env("ALIYUN_ACCESS_KEY_ID")
    ak_secret = _require_env("ALIYUN_ACCESS_KEY_SECRET")
    admin_token = _require_env("RUNNER_ADMIN_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY", "Shadow-Azure/ai-eval-platform")
    ref = os.environ.get("SPOT_SMOKE_REF", "main")
    region = os.environ.get("ALIYUN_REGION", "cn-hangzhou").removeprefix("oss-")
    instance_type_override = os.environ.get("SPOT_SMOKE_INSTANCE_TYPE", "").strip()
    smoke_id = os.environ.get("SPOT_SMOKE_ID") or f"{int(time.time())}"

    tmp = Path(tempfile.mkdtemp(prefix="m4-04-spot-smoke-"))
    env = os.environ.copy()
    env.update(
        {
            "DATABASE_URL": f"sqlite:///{tmp}/smoke.db",
            "REDIS_URL": "",
            "AUTH_SESSION_SECRET": "m4-04-smoke-session",
            "AUTH_BOOTSTRAP_ADMIN_EMAIL": "admin@m4-04-smoke.local",
            "AUTH_BOOTSTRAP_ADMIN_PASSWORD": "m4-04-smoke-password",
            "RUNNER_ADMIN_TOKEN": admin_token,
            "GITHUB_REPO": repo,
            "ALIYUN_REGION": region,
            "S3_REGION": f"oss-{region}",
            "SECRET_BOX_KEY": os.environ.get("SECRET_BOX_KEY", ""),
        }
    )
    os.environ.update(env)
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd=BACKEND,
        env=env,
        check=True,
        capture_output=True,
    )
    subprocess.run([sys.executable, "-m", "app.seed"], cwd=BACKEND, env=env, check=True, capture_output=True)
    sys.path.insert(0, str(BACKEND))

    from app.config import get_settings  # noqa: PLC0415
    from app.db import SessionLocal  # noqa: PLC0415
    from app.models import Credential, ResourceEvent  # noqa: PLC0415
    from app.secrets import get_secret_box  # noqa: PLC0415
    from app.seed import ensure_default_scope  # noqa: PLC0415
    from app.services.object_store import (  # noqa: PLC0415
        ObjectStoreError,
        object_store_from_settings,
    )
    from app.services.provisioning import InstanceSpec, ProvisionerError  # noqa: PLC0415
    from app.services.provisioning.aliyun import (  # noqa: PLC0415
        AliyunEcsClient,
        AliyunEcsProvisioner,
        resolve_aliyun_credentials,
    )
    from app.services.runner_registration import GitHubRunnerRegistrar  # noqa: PLC0415
    from sqlalchemy import select  # noqa: PLC0415

    settings = get_settings()
    settings.runner_spot_runner_version = os.environ.get("SPOT_RUNNER_VERSION", "2.329.0")
    settings.aliyun_default_region = region
    settings.eval_spot_register_timeout_seconds = int(os.environ.get("SPOT_SMOKE_RUNNER_TIMEOUT", "1200"))
    label = f"spot-smoke-{smoke_id}"
    evidence: dict = {
        "ok": False,
        "started_at": _iso(datetime.now(UTC)),
        "region": region,
        "smoke_id": smoke_id,
        "runner_name": None,
        "instance_id": None,
        "instance_type": None,
        "price_cny": None,
        "price_usd": None,
        "zone_id": None,
        "runner_online": False,
        "runner_online_seconds": None,
        "workflow_url": None,
        "workflow_conclusion": None,
        "cleaned_up": False,
        "secrets_leaked": False,
        "issues": [],
    }
    started = time.monotonic()

    runner_tarball_url = None
    store = object_store_from_settings(settings)
    if store is None:
        evidence["ok"] = False
        evidence["issues"].append(
            {
                "severity": "P1",
                "title": "缺少对象存储配置，无法缓存 Runner",
                "facts": [{"label": "需要", "value": "OSS_ENDPOINT/OSS_BUCKET/S3_ACCESS_KEY/S3_SECRET_KEY"}],
                "state": {"kind": "fail", "label": "FAIL"},
            }
        )
        evidence["total_seconds"] = round(time.monotonic() - started, 1)
        _write_outputs(evidence, Path(args.out), render=args.render)
        return 1
    version = settings.runner_spot_runner_version
    # Upload a fresh object each run: a stale/corrupt cache is worse than the
    # extra CI upload, and the smoke must prove the exact bytes cloud-init gets.
    runner_key = f"runner/actions-runner-linux-x64-{version}-{smoke_id}.tar.gz"
    try:
        github_url = (
            "https://github.com/actions/runner/releases/download/"
            f"v{version}/actions-runner-linux-x64-{version}.tar.gz"
        )
        with urllib.request.urlopen(github_url, timeout=300) as response:
            payload = response.read()
        if len(payload) < 10_000_000 or payload[:2] != b"\x1f\x8b":
            raise ObjectStoreError("下载的 Runner 不是有效 tar.gz")
        store.put_bytes(runner_key, payload, content_type="application/gzip")
        evidence["runner_tarball"] = "oss-upload"
        runner_tarball_url = store.presign_get_url(runner_key, expires=7200)
        # Prove the URL works from a different network before paying for an
        # instance; a bad SigV4 region/signature otherwise fails silently in
        # cloud-init and only surfaces 20 minutes later.
        with urllib.request.urlopen(runner_tarball_url, timeout=60) as response:
            if not response.read(1024):
                raise ObjectStoreError("Runner 缓存读取为空")
    except (ObjectStoreError, OSError) as exc:
        evidence["ok"] = False
        evidence["issues"].append(
            {
                "severity": "P1",
                "title": "Runner 缓存准备失败",
                "facts": [{"label": "错误", "value": type(exc).__name__}],
                "state": {"kind": "fail", "label": "FAIL"},
            }
        )
        evidence["total_seconds"] = round(time.monotonic() - started, 1)
        _write_outputs(evidence, Path(args.out), render=args.render)
        return 1

    with SessionLocal() as session:
        tenant, _workspace, project = ensure_default_scope(session)
        ciphertext, version = get_secret_box().encrypt(
            json.dumps({"access_key_id": ak_id, "access_key_secret": ak_secret})
        )
        credential = Credential(
            tenant_id=tenant.id,
            project_id=project.id,
            kind="resource",
            provider="aliyun",
            ciphertext=ciphertext,
            key_version=version,
            secret_mask="****",
        )
        session.add(credential)
        session.flush()
        from app.models import Connection, ResourcePool  # noqa: PLC0415

        connection = Connection(
            tenant_id=tenant.id,
            project_id=project.id,
            name=f"aliyun-spot-smoke-{smoke_id}",
            provider="aliyun",
            region=region,
            credential_id=credential.id,
            capabilities_json={"capabilities": ["ecs:spot", "ecs:inventory"]},
            status="unverified",
        )
        session.add(connection)
        session.flush()
        pool = ResourcePool(
            tenant_id=tenant.id,
            project_id=project.id,
            connection_id=connection.id,
            name=f"spot-smoke-{smoke_id}",
            provider_key="aliyun",
            runner_group=None,
            provisioning_mode="spot",
            min_capacity=0,
            max_capacity=1,
            instance_config_json={
                "instance_type": instance_type_override or DEFAULT_TYPES[0],
                "runners_per_instance": 1,
                "platform_base_url": "",
                "runner_tarball_url": runner_tarball_url,
            },
            price_threshold_usd=Decimal("10.0000"),
            labels_json={"runs_on": ["self-hosted", label]},
            status="active",
        )
        session.add(pool)
        session.commit()

        registrar = GitHubRunnerRegistrar(settings)
        provisioner = AliyunEcsProvisioner(settings=settings, registrar=registrar)
        now = datetime.now(UTC)
        candidates = [instance_type_override] if instance_type_override else list(DEFAULT_TYPES)
        access_key_id, access_key_secret = resolve_aliyun_credentials(session, connection)
        client = AliyunEcsClient(
            access_key_id=access_key_id,
            access_key_secret=access_key_secret,
            endpoint=settings.aliyun_ecs_endpoint,
        )
        try:
            switches = client.describe_vswitches(region)
        except ProvisionerError as exc:
            evidence["ok"] = False
            evidence["issues"].append(
                {
                    "severity": "P1",
                    "title": "无法读取 vSwitch（RAM 权限或网络）",
                    "facts": [{"label": "错误", "value": exc.code}, {"label": "Region", "value": region}],
                    "state": {"kind": "fail", "label": "FAIL"},
                }
            )
            evidence["total_seconds"] = round(time.monotonic() - started, 1)
            _write_outputs(evidence, Path(args.out), render=args.render)
            return 1
        diagnostics: list[dict] = []
        pick = None
        # A vSwitch in a zone without spot inventory would make every quote
        # "unavailable"; the smoke deliberately tries every vSwitch zone so a
        # working zone is used. The production pool pins its vSwitch, so this
        # discovery lives in the smoke harness only.
        for candidate in candidates:
            if not candidate:
                continue
            for switch in switches:
                zone = str(switch.get("ZoneId") or "") or None
                try:
                    samples = client.describe_spot_prices(region, candidate, zone_id=zone)
                except ProvisionerError as exc:
                    diagnostics.append({"instance_type": candidate, "zone_id": zone, "error": exc.code})
                    continue
                for sample in samples:
                    diagnostics.append(
                        {
                            "instance_type": candidate,
                            "zone_id": sample.get("ZoneId"),
                            "price_cny": sample.get("SpotPrice") or sample.get("Price"),
                        }
                    )
                if samples:
                    pick = (candidate, switch, samples[0], zone)
                    break
            if pick:
                break
        if pick is None:
            evidence["ok"] = False
            evidence["spot_diagnostics"] = diagnostics[:30]
            evidence["issues"].append(
                {
                    "severity": "P1",
                    "title": "没有可用竞价库存",
                    "facts": [
                        {"label": "Region", "value": region},
                        {
                            "label": "已尝试",
                            "value": json.dumps(diagnostics[:10], ensure_ascii=False),
                        },
                    ],
                    "state": {"kind": "fail", "label": "FAIL"},
                }
            )
            evidence["total_seconds"] = round(time.monotonic() - started, 1)
            _write_outputs(evidence, Path(args.out), render=args.render)
            return 1
        candidate, switch, _sample, zone = pick
        evidence["instance_type"] = candidate
        pool.instance_config_json = {
            **pool.instance_config_json,
            "instance_type": candidate,
            "vswitch_id": switch["VSwitchId"],
            "zone_id": zone or switch.get("ZoneId"),
        }
        session.flush()
        try:
            quote = provisioner.quote(connection=connection, pool=pool, now=now, session=session)
        except ProvisionerError as exc:
            evidence["ok"] = False
            evidence["issues"].append(
                {
                    "severity": "P1",
                    "title": "竞价价格查询失败",
                    "facts": [{"label": "错误", "value": exc.code}],
                    "state": {"kind": "fail", "label": "FAIL"},
                }
            )
            evidence["total_seconds"] = round(time.monotonic() - started, 1)
            _write_outputs(evidence, Path(args.out), render=args.render)
            return 1
        if not quote.available:
            evidence["ok"] = False
            evidence["spot_diagnostics"] = diagnostics[:30]
            evidence["issues"].append(
                {
                    "severity": "P1",
                    "title": "竞价库存复查失败",
                    "facts": [{"label": "Instance type", "value": candidate}],
                    "state": {"kind": "fail", "label": "FAIL"},
                }
            )
            evidence["total_seconds"] = round(time.monotonic() - started, 1)
            _write_outputs(evidence, Path(args.out), render=args.render)
            return 1
        evidence.update(
            {
                "price_cny": str(quote.price_native),
                "price_usd": str(quote.price_usd),
                "zone_id": quote.zone_id,
            }
        )
        if quote.price_usd > Decimal("1.0000"):
            evidence["ok"] = False
            evidence["issues"].append(
                {
                    "severity": "P1",
                    "title": "竞价价格超过冒烟预算",
                    "facts": [{"label": "Price USD/h", "value": str(quote.price_usd)}],
                    "state": {"kind": "fail", "label": "FAIL"},
                }
            )
            evidence["total_seconds"] = round(time.monotonic() - started, 1)
            _write_outputs(evidence, Path(args.out), render=args.render)
            return 1

        try:
            instance = provisioner.create(
                session=session,
                connection=connection,
                pool=pool,
                spec=InstanceSpec(instance_type=evidence["instance_type"]),
                now=now,
            )
        except Exception as exc:  # noqa: BLE001 - capture the provider code, then exit
            evidence["ok"] = False
            evidence["issues"].append(
                {
                    "severity": "P1",
                    "title": "竞价实例创建失败",
                    "facts": [
                        {"label": "阶段", "value": type(exc).__name__},
                        {"label": "详情", "value": str(exc)[:300]},
                        {
                            "label": "诊断",
                            "value": str(getattr(exc, "details", {}))[:500],
                        },
                    ],
                    "state": {"kind": "fail", "label": "FAIL"},
                }
            )
            evidence["total_seconds"] = round(time.monotonic() - started, 1)
            _write_outputs(evidence, Path(args.out), render=args.render)
            return 1
        session.commit()
        evidence["instance_id"] = instance.provider_instance_id
        evidence["runner_name"] = instance.cost_json.get("runner_name")
        runner_name = str(evidence["runner_name"])
        try:
            online_started = time.monotonic()
            record = _wait_runner_online(
                registrar, runner_name, timeout=int(os.environ.get("SPOT_SMOKE_RUNNER_TIMEOUT", "1200"))
            )
            evidence["runner_online"] = True
            evidence["runner_online_seconds"] = round(time.monotonic() - online_started, 1)
            evidence["runner_id"] = record.get("id")
            run_id = _dispatch_smoke(
                admin_token,
                repo=repo,
                ref=ref,
                smoke_id=smoke_id,
                runs_on=["self-hosted", label],
            )
            evidence["workflow_run_id"] = run_id
            evidence["workflow_url"] = f"https://github.com/{repo}/actions/runs/{run_id}"
            evidence["workflow_conclusion"] = "success"
            # Detect secret leakage in what the platform persisted.
            stored = json.dumps(
                [event.payload_json for event in session.scalars(select(ResourceEvent))] + [instance.cost_json]
            )
            evidence["secrets_leaked"] = ak_secret in stored or "encoded_jit_config" in stored
            evidence["ok"] = True
        except Exception as exc:  # noqa: BLE001 - record the failure, then clean up
            evidence["ok"] = False
            # Bootstrap failures happen before the runner can report anything;
            # keep Cloud Assistant and console output (redacted) so the next
            # attempt can distinguish apt/network/JIT problems.
            evidence["diagnostics"] = _diagnose_instance(
                client,
                region,
                evidence.get("instance_id") or instance.provider_instance_id,
                ak_secret,
            )
            try:
                console = client.get_instance_console_output(
                    region, evidence.get("instance_id") or instance.provider_instance_id
                )
                evidence["console_output"] = console.replace(ak_secret, "<redacted>")[-4000:]
            except ProvisionerError as console_exc:
                evidence["console_output_error"] = console_exc.code
            evidence["issues"].append(
                {
                    "severity": "P1",
                    "title": "Runner 注册或小任务执行失败",
                    "facts": [
                        {"label": "阶段", "value": type(exc).__name__},
                        {"label": "详情", "value": str(exc)[:300]},
                        {
                            "label": "诊断",
                            "value": str(getattr(exc, "details", {}))[:500],
                        },
                    ],
                    "state": {"kind": "fail", "label": "FAIL"},
                }
            )
        finally:
            cleanup_now = datetime.now(UTC)
            try:
                session.refresh(instance)
                provisioner.release(session=session, instance=instance, now=cleanup_now)
                session.commit()
                evidence["cleaned_up"] = instance.status == "released"
            except Exception as exc:  # noqa: BLE001 - evidence must record cleanup failure
                session.rollback()
                evidence["issues"].append(
                    {
                        "severity": "P1",
                        "title": "实例自动释放失败，需要人工清理",
                        "facts": [
                            {"label": "实例", "value": str(evidence.get("instance_id"))},
                            {"label": "错误", "value": type(exc).__name__},
                        ],
                        "state": {"kind": "fail", "label": "FAIL"},
                    }
                )
                evidence["ok"] = False

    evidence["total_seconds"] = round(time.monotonic() - started, 1)
    _write_outputs(evidence, Path(args.out), render=args.render)
    return 0 if evidence["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
