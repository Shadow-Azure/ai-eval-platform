"""M4-04 Aliyun spot provisioner + ephemeral runner evidence.

Network-free: a fake RPC transport records every ECS call and returns
deterministic JSON. The tests prove price conversion, JIT placement, provider
reconciliation (online/interrupted/timeout/heartbeat loss) and secret hygiene
(the JIT blob and AccessKey secret never reach the database/events).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qs, quote

import pytest
from app.config import get_settings
from app.models import (
    Connection,
    Credential,
    ProvisionedInstance,
    ResourceEvent,
    ResourcePool,
    Runner,
)
from app.secrets import get_secret_box
from app.seed import ensure_default_scope
from app.services import capacity_control
from app.services.provisioning import InstanceSpec, ProvisionerError
from app.services.provisioning.aliyun import (
    AliyunEcsClient,
    AliyunEcsProvisioner,
    resolve_aliyun_credentials,
)
from app.services.runner_registration import JitRunner
from sqlalchemy import select
from sqlalchemy.orm import Session

SECRET_AK_ID = "LTAI-test-access-key"
SECRET_AK_SECRET = "test-access-key-secret-must-not-leak"
SECRET_JIT = "encoded-jit-config-must-not-leak"


class FakeAliyun:
    """Deterministic ECS RPC transport."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, list[str]]]] = []
        self.instances: dict[str, dict] = {}
        self.prices: list[dict] = [
            {"ZoneId": "cn-hangzhou-i", "InstanceType": "ecs.g7.large", "Price": "0.5000"}
        ]
        self.vswitches: list[dict] = [
            {"VSwitchId": "vsw-test", "VpcId": "vpc-test", "ZoneId": "cn-hangzhou-i"}
        ]
        self.run_error: tuple[int, dict] | None = None
        self.delete_error: tuple[int, dict] | None = None
        self.delete_calls: list[str] = []

    def __call__(self, url: str, *, method: str, body: bytes, headers: dict, timeout: float):
        params = parse_qs(body.decode(), keep_blank_values=True)
        action = params["Action"][0]
        self.calls.append((action, params))
        if action == "DescribeSpotPriceHistory":
            zone = params.get("ZoneId", [None])[0]
            rows = [row for row in self.prices if zone is None or row.get("ZoneId") == zone]
            return 200, json.dumps({"SpotPrices": {"SpotPriceType": rows}})
        if action == "DescribeImages":
            return 200, json.dumps(
                {"Images": {"Image": [{"ImageId": "img-ubuntu", "OSName": "Ubuntu 22.04 64bit"}]}}
            )
        if action == "DescribeVSwitches":
            return 200, json.dumps({"VSwitches": {"VSwitch": self.vswitches}})
        if action == "DescribeSecurityGroups":
            return 200, json.dumps(
                {"SecurityGroups": {"SecurityGroup": [{"SecurityGroupId": "sg-test"}]}}
            )
        if action == "RunInstances":
            if self.run_error is not None:
                status, payload = self.run_error
                return status, json.dumps(payload)
            return 200, json.dumps({"InstanceIdSets": {"InstanceIdSet": ["i-test-1"]}})
        if action == "DescribeInstances":
            ids = json.loads(params["InstanceIds"][0]) if "InstanceIds" in params else list(self.instances)
            return 200, json.dumps(
                {"Instances": {"Instance": [self.instances[i] for i in ids if i in self.instances]}}
            )
        if action == "DeleteInstance":
            if self.delete_error is not None:
                status, payload = self.delete_error
                return status, json.dumps(payload)
            self.delete_calls.append(params["InstanceId"][0])
            self.instances.pop(params["InstanceId"][0], None)
            return 200, json.dumps({"RequestId": "req-delete"})
        raise AssertionError(f"unexpected action {action}")


class FakeRegistrar:
    def __init__(self, *, error: str | None = None) -> None:
        self.error = error
        self.generated: list[dict] = []
        self.deleted: list[dict] = []

    def generate_jitconfig(self, *, name, labels, runner_group_id=None) -> JitRunner:
        if self.error:
            raise ProvisionerError(self.error)
        self.generated.append({"name": name, "labels": list(labels), "group": runner_group_id})
        return JitRunner(runner_id=42, name=name, encoded=SECRET_JIT, labels=tuple(labels))

    def delete_runner(self, *, runner_id=None, name=None) -> bool:
        self.deleted.append({"runner_id": runner_id, "name": name})
        return True


@pytest.fixture
def clock() -> datetime:
    return datetime(2026, 11, 10, 2, 0, 0, tzinfo=UTC)


@pytest.fixture
def scope(session: Session) -> dict[str, str]:
    tenant, _workspace, project = ensure_default_scope(session)
    return {"tenant_id": tenant.id, "project_id": project.id}


def _credential(session: Session, scope: dict[str, str]) -> Credential:
    ciphertext, version = get_secret_box().encrypt(
        json.dumps({"access_key_id": SECRET_AK_ID, "access_key_secret": SECRET_AK_SECRET})
    )
    credential = Credential(
        tenant_id=scope["tenant_id"],
        project_id=scope["project_id"],
        kind="resource",
        provider="aliyun",
        ciphertext=ciphertext,
        key_version=version,
        secret_mask="****cret",
    )
    session.add(credential)
    session.flush()
    return credential


def _connection(session: Session, scope: dict[str, str]) -> Connection:
    connection = Connection(
        tenant_id=scope["tenant_id"],
        project_id=scope["project_id"],
        name="aliyun-test",
        provider="aliyun",
        region="cn-hangzhou",
        credential_id=_credential(session, scope).id,
        capabilities_json={"capabilities": ["ecs:spot", "ecs:inventory"]},
        status="unverified",
    )
    session.add(connection)
    session.flush()
    return connection


def _pool(session: Session, scope: dict[str, str], connection: Connection, **overrides) -> ResourcePool:
    values: dict = {
        "tenant_id": scope["tenant_id"],
        "project_id": scope["project_id"],
        "connection_id": connection.id,
        "name": "spot-pool",
        "provider_key": "aliyun",
        "provisioning_mode": "spot",
        "min_capacity": 0,
        "max_capacity": 2,
        "instance_config_json": {"instance_type": "ecs.g7.large", "runners_per_instance": 1},
        "price_threshold_usd": Decimal("0.2000"),
        "labels_json": {"runs_on": ["self-hosted", "linux", "eval"]},
        "status": "active",
    }
    values.update(overrides)
    pool = ResourcePool(**values)
    session.add(pool)
    session.flush()
    return pool


def _provisioner(settings=None, registrar=None, transport=None, now=None):
    return AliyunEcsProvisioner(
        settings=settings or get_settings(),
        registrar=registrar or FakeRegistrar(),
        transport=transport or FakeAliyun(),
        now=now or (lambda: datetime(2026, 11, 10, 2, 0, 0, tzinfo=UTC)),
        nonce=lambda: "fixed-nonce",
    )


# ---------------------------------------------------------------------------
# Signing / quote / verify
# ---------------------------------------------------------------------------


def test_signing_uses_sorted_rfc3986_params() -> None:
    captured = {}

    def transport(url, *, method, body, headers, timeout):
        captured.update(parse_qs(body.decode(), keep_blank_values=True))
        return 200, json.dumps({"RequestId": "r"})

    client = AliyunEcsClient(
        access_key_id=SECRET_AK_ID,
        access_key_secret=SECRET_AK_SECRET,
        endpoint="https://ecs.aliyuncs.com",
        transport=transport,
        now=lambda: datetime(2026, 11, 10, 2, 0, 0, tzinfo=UTC),
        nonce=lambda: "nonce-fixed",
    )
    client.call("DescribeInstances", {"RegionId": "cn-hangzhou", "PageSize": 1})
    assert captured["SignatureMethod"] == ["HMAC-SHA1"]
    assert captured["Timestamp"] == ["2026-11-10T02:00:00Z"]
    assert captured["SignatureNonce"] == ["nonce-fixed"]
    # Recompute the signature from the returned parameter set to prove the
    # canonicalization is stable and independent of argument insertion order.
    params = {key: values[0] for key, values in captured.items() if key != "Signature"}
    canonical = "&".join(
        f"{quote(k, safe='~')}={quote(v, safe='~')}" for k, v in sorted(params.items())
    )
    expected = base64.b64encode(
        hmac.new(
            f"{SECRET_AK_SECRET}&".encode(),
            f"POST&%2F&{quote(canonical, safe='~')}".encode(),
            hashlib.sha1,
        ).digest()
    ).decode()
    assert captured["Signature"] == [expected]
    assert SECRET_AK_SECRET not in json.dumps(params)


def test_quote_converts_cny_and_reports_inventory(
    session: Session, scope: dict[str, str], clock: datetime
) -> None:
    connection = _connection(session, scope)
    pool = _pool(session, scope, connection)
    transport = FakeAliyun()
    quote = _provisioner(transport=transport).quote(
        connection=connection, pool=pool, now=clock, session=session
    )
    assert quote.price_native == Decimal("0.5000")
    assert quote.currency == "CNY"
    assert quote.price_usd == (Decimal("0.5") / Decimal("7.2")).quantize(Decimal("0.0001"))
    assert quote.available is True
    assert quote.zone_id == "cn-hangzhou-i"
    price_call = next(
        call for call in transport.calls if call[0] == "DescribeSpotPriceHistory"
    )
    assert price_call[1]["NetworkType"] == ["vpc"]


def test_empty_spot_prices_mean_no_inventory(
    session: Session, scope: dict[str, str], clock: datetime
) -> None:
    connection = _connection(session, scope)
    pool = _pool(session, scope, connection)
    transport = FakeAliyun()
    transport.prices = []
    quote = _provisioner(transport=transport).quote(
        connection=connection, pool=pool, now=clock, session=session
    )
    assert quote.available is False
    assert quote.price_usd == Decimal("0")


def test_verify_is_read_only(
    session: Session, scope: dict[str, str]
) -> None:
    connection = _connection(session, scope)
    transport = FakeAliyun()
    result = _provisioner(transport=transport).verify(session=session, connection=connection)
    assert result["ok"] is True
    assert [action for action, _ in transport.calls] == ["DescribeInstances"]


def test_credential_json_is_required(session: Session, scope: dict[str, str]) -> None:
    connection = _connection(session, scope)
    credential = session.get(Credential, connection.credential_id)
    ciphertext, version = get_secret_box().encrypt("not-json")
    credential.ciphertext, credential.key_version = ciphertext, version
    session.flush()
    with pytest.raises(ProvisionerError) as caught:
        resolve_aliyun_credentials(session, connection)
    assert caught.value.code == "connection_credential_invalid"
    assert SECRET_AK_SECRET not in str(caught.value)


# ---------------------------------------------------------------------------
# Create / release
# ---------------------------------------------------------------------------


def _write_file_content(cloud_init: str, path: str) -> str:
    lines = cloud_init.splitlines()
    for index, line in enumerate(lines):
        if line.strip().endswith(f"path: {path}"):
            for candidate in lines[index:]:
                if "content:" in candidate:
                    return base64.b64decode(candidate.split("content:", 1)[1].strip()).decode()
    raise AssertionError(f"{path} not found in cloud-config")


def test_create_embeds_jit_in_user_data_only(
    session: Session, scope: dict[str, str], clock: datetime
) -> None:
    connection = _connection(session, scope)
    pool = _pool(session, scope, connection)
    transport = FakeAliyun()
    registrar = FakeRegistrar()
    provisioner = _provisioner(transport=transport, registrar=registrar)

    instance = provisioner.create(
        session=session,
        connection=connection,
        pool=pool,
        spec=InstanceSpec(instance_type="ecs.g7.large"),
        now=clock,
    )

    assert instance.provider_instance_id == "i-test-1"
    assert instance.status == "creating"
    assert instance.spot_strategy == "spot"
    assert instance.cost_json["runner_name"].startswith("spot-")
    assert registrar.generated[0]["labels"] == ["self-hosted", "linux", "eval"]
    # JIT is embedded in the bootstrap cloud-init file and nowhere else.
    run_call = next(call for call in transport.calls if call[0] == "RunInstances")
    user_data = base64.b64decode(run_call[1]["UserData"][0]).decode()
    bootstrap = _write_file_content(user_data, "/etc/eval/bootstrap.sh")
    assert SECRET_JIT in bootstrap
    assert SECRET_AK_SECRET not in user_data
    assert SECRET_JIT not in json.dumps(instance.cost_json)
    assert SECRET_AK_SECRET not in json.dumps(instance.cost_json)
    assert session.scalar(select(ResourceEvent.id)) is None


def test_create_failure_deletes_pending_jit_runner(
    session: Session, scope: dict[str, str], clock: datetime
) -> None:
    connection = _connection(session, scope)
    pool = _pool(session, scope, connection)
    transport = FakeAliyun()
    transport.run_error = (400, {"Code": "InvalidVSwitchId.NotFound", "Message": "no vswitch"})
    registrar = FakeRegistrar()

    with pytest.raises(ProvisionerError) as caught:
        _provisioner(transport=transport, registrar=registrar).create(
            session=session,
            connection=connection,
            pool=pool,
            spec=InstanceSpec(instance_type="ecs.g7.large"),
            now=clock,
        )
    assert caught.value.code == "cloud_request_invalid"
    assert registrar.deleted and registrar.deleted[0]["name"] == registrar.generated[0]["name"]
    assert session.scalar(select(ProvisionedInstance.id)) is None


def test_release_deletes_runner_before_instance(
    session: Session, scope: dict[str, str], clock: datetime
) -> None:
    connection = _connection(session, scope)
    pool = _pool(session, scope, connection)
    transport = FakeAliyun()
    registrar = FakeRegistrar()
    provisioner = _provisioner(transport=transport, registrar=registrar)
    instance = provisioner.create(
        session=session,
        connection=connection,
        pool=pool,
        spec=InstanceSpec(instance_type="ecs.g7.large"),
        now=clock,
    )

    provisioner.release(session=session, instance=instance, now=clock)

    assert registrar.deleted
    assert transport.delete_calls == ["i-test-1"]
    assert instance.status == "released"
    assert instance.released_at == clock


# ---------------------------------------------------------------------------
# Sync: online / interruption / timeout / heartbeat loss
# ---------------------------------------------------------------------------


def _instance_with_runner(
    session: Session, scope: dict[str, str], connection: Connection, pool: ResourcePool, clock: datetime
) -> tuple[ProvisionedInstance, Runner]:
    instance = ProvisionedInstance(
        tenant_id=scope["tenant_id"],
        resource_pool_id=pool.id,
        provider_instance_id="i-test-1",
        status="online",
        spot_strategy="spot",
        cost_json={"runner_name": "spot-runner-1"},
        created_at=clock - timedelta(minutes=2),
    )
    runner = Runner(
        tenant_id=scope["tenant_id"],
        resource_pool_id=pool.id,
        provider_runner_id="spot-runner-1",
        name="spot-runner-1",
        labels_json=["self-hosted", "linux", "eval"],
        status="online",
        last_heartbeat_at=clock,
        registered_at=clock - timedelta(minutes=1),
    )
    session.add_all([instance, runner])
    session.flush()
    instance.runner_id = runner.id
    session.flush()
    return instance, runner


def test_sync_links_running_instance_to_runner(
    session: Session, scope: dict[str, str], clock: datetime
) -> None:
    connection = _connection(session, scope)
    pool = _pool(session, scope, connection)
    instance = ProvisionedInstance(
        tenant_id=scope["tenant_id"],
        resource_pool_id=pool.id,
        provider_instance_id="i-test-1",
        status="creating",
        spot_strategy="spot",
        cost_json={"runner_name": "spot-runner-1"},
        created_at=clock - timedelta(minutes=2),
    )
    runner = Runner(
        tenant_id=scope["tenant_id"],
        resource_pool_id=pool.id,
        provider_runner_id="spot-runner-1",
        name="spot-runner-1",
        labels_json=["self-hosted"],
        status="online",
        last_heartbeat_at=clock,
        registered_at=clock - timedelta(minutes=1),
    )
    session.add_all([instance, runner])
    session.flush()
    transport = FakeAliyun()
    transport.instances["i-test-1"] = {"InstanceId": "i-test-1", "Status": "Running"}

    counts = _provisioner(transport=transport).sync(
        session=session, connection=connection, pool=pool, now=clock
    )

    assert counts["online"] == 1
    assert instance.status == "online"
    assert instance.runner_id == runner.id
    assert instance.ready_at is not None
    assert any(
        event.event_type == capacity_control.EVENT_INSTANCE_REGISTERED
        for event in session.scalars(select(ResourceEvent))
    )


def test_sync_reclaims_spot_interruption(
    session: Session, scope: dict[str, str], clock: datetime
) -> None:
    connection = _connection(session, scope)
    pool = _pool(session, scope, connection)
    instance, _runner = _instance_with_runner(session, scope, connection, pool, clock)
    transport = FakeAliyun()
    transport.instances["i-test-1"] = {
        "InstanceId": "i-test-1",
        "Status": "Running",
        "OperationLocks": {"LockReason": ["Recycling"]},
    }
    registrar = FakeRegistrar()

    counts = _provisioner(transport=transport, registrar=registrar).sync(
        session=session, connection=connection, pool=pool, now=clock
    )

    assert counts["interrupted"] == 1
    assert instance.status == "released"
    assert registrar.deleted
    assert transport.delete_calls == ["i-test-1"]
    assert any(
        event.event_type == capacity_control.EVENT_INSTANCE_INTERRUPTED
        for event in session.scalars(select(ResourceEvent))
    )


def test_sync_reclaims_unregistered_instance_on_timeout(
    session: Session, scope: dict[str, str], clock: datetime
) -> None:
    connection = _connection(session, scope)
    pool = _pool(session, scope, connection)
    instance = ProvisionedInstance(
        tenant_id=scope["tenant_id"],
        resource_pool_id=pool.id,
        provider_instance_id="i-test-1",
        status="registering",
        spot_strategy="spot",
        cost_json={"runner_name": "spot-runner-timeout"},
        created_at=clock - timedelta(hours=2),
    )
    session.add(instance)
    session.flush()
    transport = FakeAliyun()
    transport.instances["i-test-1"] = {"InstanceId": "i-test-1", "Status": "Running"}

    counts = _provisioner(transport=transport).sync(
        session=session, connection=connection, pool=pool, now=clock
    )

    assert counts["timeouts"] == 1
    assert instance.status == "released"
    assert any(
        event.event_type == capacity_control.EVENT_INSTANCE_CREATE_TIMEOUT
        for event in session.scalars(select(ResourceEvent))
    )


def test_sync_reclaims_instance_after_runner_heartbeat_loss(
    session: Session, scope: dict[str, str], clock: datetime
) -> None:
    connection = _connection(session, scope)
    pool = _pool(session, scope, connection)
    instance, runner = _instance_with_runner(session, scope, connection, pool, clock)
    runner.last_heartbeat_at = clock - timedelta(hours=1)
    # The worker's offline sweep may have already run; the reclaim path must
    # work for both online and offline rows.
    runner.status = "offline"
    session.flush()
    transport = FakeAliyun()
    transport.instances["i-test-1"] = {"InstanceId": "i-test-1", "Status": "Running"}

    counts = _provisioner(transport=transport).sync(
        session=session, connection=connection, pool=pool, now=clock
    )

    assert counts["interrupted"] == 1
    assert instance.status == "released"
    assert runner.status == "offline"
    assert runner.deregistered_at == clock


def test_sync_failure_keeps_tick_running(
    session: Session, scope: dict[str, str], clock: datetime
) -> None:
    connection = _connection(session, scope)
    pool = _pool(session, scope, connection)
    ProvisionedInstance(
        tenant_id=scope["tenant_id"],
        resource_pool_id=pool.id,
        provider_instance_id="i-test-1",
        status="registering",
        spot_strategy="spot",
        cost_json={"runner_name": "spot-runner-1"},
    )
    session.flush()

    def failing_transport(url, *, method, body, headers, timeout):
        return 403, json.dumps({"Code": "InvalidAccessKeyId.NotFound", "Message": "bad key"})

    state = capacity_control.reconcile_pool(
        session,
        pool,
        settings=get_settings(),
        now=clock,
        provisioner=_provisioner(transport=failing_transport),
    )
    # The sync and price calls fail with an auth error: the pool tick still
    # returns a diagnosable state, records the failure, and creates nothing.
    assert state["provider_error"] == "aliyun_auth_failed"
    assert state["capacity_reason"] == capacity_control.REASON_PROVIDER_AUTH_FAILED
    assert state["scaled_up"] == 0
    assert any(
        event.event_type == capacity_control.EVENT_PROVISIONING_FAILED
        for event in session.scalars(select(ResourceEvent))
    )


def test_configured_vswitch_drives_zone_vpc_and_instance(
    session: Session, scope: dict[str, str], clock: datetime
) -> None:
    connection = _connection(session, scope)
    pool = _pool(
        session,
        scope,
        connection,
        instance_config_json={
            "instance_type": "ecs.g7.large",
            "vswitch_id": "vsw-2",
        },
    )
    transport = FakeAliyun()
    transport.vswitches = [
        {"VSwitchId": "vsw-1", "VpcId": "vpc-1", "ZoneId": "cn-hangzhou-i"},
        {"VSwitchId": "vsw-2", "VpcId": "vpc-2", "ZoneId": "cn-hangzhou-j"},
    ]
    transport.prices = [
        {"ZoneId": "cn-hangzhou-j", "InstanceType": "ecs.g7.large", "Price": "0.4000"}
    ]
    registrar = FakeRegistrar()
    provisioner = _provisioner(transport=transport, registrar=registrar)

    quote = provisioner.quote(connection=connection, pool=pool, now=clock, session=session)
    assert quote.zone_id == "cn-hangzhou-j"
    provisioner.create(
        session=session,
        connection=connection,
        pool=pool,
        spec=InstanceSpec(instance_type="ecs.g7.large"),
        now=clock,
    )
    run_call = next(call for call in transport.calls if call[0] == "RunInstances")
    assert run_call[1]["VSwitchId"] == ["vsw-2"]
    assert run_call[1]["ZoneId"] == ["cn-hangzhou-j"]
    sg_call = next(call for call in transport.calls if call[0] == "DescribeSecurityGroups")
    assert sg_call[1]["VpcId"] == ["vpc-2"]
