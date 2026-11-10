"""Aliyun ECS spot provisioner + ephemeral runner orchestration (M4-04).

Design references: 06 §6 (spot capacity), §6.2 (instance lifecycle) and
§6.3 (failure/retry classes). The capacity controller owns scheduling; this
module owns the cloud API and the one-time runner registration.

Provider flow:

    quote   -> DescribeSpotPriceHistory (price + inventory in one call)
    verify  -> DescribeInstances (read-only credential check)
    create  -> generate JIT config -> RunInstances(SpotAsPriceGo, UserData)
    sync    -> DescribeInstances -> Running/Stopped/locks -> link runner,
               detect interruption/heartbeat loss/create timeout
    release -> delete JIT runner -> DeleteInstance

Secrets policy: the Aliyun AccessKey pair is decrypted from the tenant
``Credential`` row in memory, the encoded JIT blob is only embedded in cloud
init and never stored in ``ProvisionedInstance``/events/logs, and every error
is reduced to a stable code plus a short provider message.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import Settings
from ...models import Connection, Credential, ProvisionedInstance, ResourcePool, Runner
from ...secrets import SecretBoxError, get_secret_box
from ..runner_registration import GitHubRunnerRegistrar
from . import (
    CAPABILITY_ECS_INVENTORY,
    CAPABILITY_ECS_SPOT,
    InstanceSpec,
    PriceQuote,
    ProvisionerError,
)
from .spot_cloud_init import render_cloud_init

ALIYUN_PROVIDER = "aliyun"
API_VERSION = "2014-05-26"
PROVIDER_NAME = "Aliyun ECS"
DEFAULT_ZONE_ID = None

AliyunTransport = Callable[..., tuple[int, str]]

# Aliyun RPC error codes grouped by recovery behavior. The capacity controller
# only needs the stable ProvisionerError.code; the original code is kept in
# details for diagnosis.
AUTH_ERROR_CODES = {
    "InvalidAccessKeyId",
    "InvalidAccessKeyId.NotFound",
    "SignatureDoesNotMatch",
    "Forbidden",
    "Forbidden.RAM",
    "NoPermission",
    "InvalidSecurityToken.Expired",
}
INVENTORY_ERROR_CODES = {
    "InsufficientInstanceCapacity",
    "QuotaExceeded",
    "InvalidInstanceType",
    "InvalidInstanceType.NotFound",
    "InvalidZoneId.NotFound",
    "Zone.NotOnSale",
    "NoAvailableInstanceType",
    "OperationDenied.NoStock",
}
CONFIG_ERROR_CODES = {
    "InvalidParameter",
    "InvalidParameterValue",
    "MissingParameter",
    "InvalidVSwitchId.NotFound",
    "InvalidSecurityGroupId.NotFound",
    "InvalidImageId.NotFound",
    "InvalidRegionId.NotFound",
}
TRANSIENT_ERROR_CODES = {
    "Throttling",
    "Throttling.User",
    "ServiceUnavailable",
    "InternalError",
    "OperationConflict",
    "RequestTimeout",
}
NOT_FOUND_INSTANCE_CODES = {"InvalidInstanceId.NotFound", "InvalidInstanceId"}

INSTANCE_ACTIVE_STATUSES = ("requested", "creating", "registering", "online")
SPOT_INTERRUPTION_LOCKS = ("recycl", "spot", "maintenance", "beingrecycled")


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    aware = _aware(value)
    return None if aware is None else aware.isoformat(timespec="microseconds")


def _percent_encode(value: object) -> str:
    return quote(str(value), safe="~")


def _transport_error(exc: Exception) -> ProvisionerError:
    return ProvisionerError(
        "cloud_transport_error",
        f"Aliyun ECS 请求传输失败：{type(exc).__name__}",
    )


def _default_transport(
    url: str, *, method: str, body: bytes, headers: dict[str, str], timeout: float
) -> tuple[int, str]:
    import httpx

    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.request(method, url, content=body, headers=headers)
            return response.status_code, response.text
    except httpx.HTTPError as exc:  # network/DNS/timeout -> retryable
        raise OSError(type(exc).__name__) from exc


class AliyunEcsClient:
    """Minimal RPC client for the ECS 2014-05-26 API.

    Only the actions the provisioner needs are implemented; every call is
    signed with the AccessKey pair and returns parsed JSON. ``transport`` is
    injectable so tests never hit the cloud.
    """

    def __init__(
        self,
        *,
        access_key_id: str,
        access_key_secret: str,
        endpoint: str,
        transport: AliyunTransport | None = None,
        timeout: float = 25.0,
        now: Callable[[], datetime] | None = None,
        nonce: Callable[[], str] | None = None,
    ) -> None:
        self.access_key_id = access_key_id
        self._access_key_secret = access_key_secret
        self.endpoint = endpoint
        self._transport = transport or _default_transport
        self.timeout = timeout
        self._now = now or (lambda: datetime.now(UTC))
        self._nonce = nonce or (lambda: uuid.uuid4().hex)

    # -- signing ------------------------------------------------------------

    def _params(self, action: str, params: dict[str, object]) -> dict[str, str]:
        values = {
            "Format": "JSON",
            "Version": API_VERSION,
            "AccessKeyId": self.access_key_id,
            "SignatureMethod": "HMAC-SHA1",
            "Timestamp": self._now().astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "SignatureVersion": "1.0",
            "SignatureNonce": self._nonce(),
            "Action": action,
        }
        for key, value in params.items():
            if value is None:
                continue
            values[str(key)] = str(value)
        return values

    def _signature(self, params: dict[str, str]) -> str:
        canonical = "&".join(
            f"{_percent_encode(key)}={_percent_encode(value)}"
            for key, value in sorted(params.items())
        )
        string_to_sign = f"POST&%2F&{_percent_encode(canonical)}"
        digest = hmac.new(
            f"{self._access_key_secret}&".encode(),
            string_to_sign.encode(),
            hashlib.sha1,
        ).digest()
        return base64.b64encode(digest).decode()

    # -- HTTP ---------------------------------------------------------------

    def call(self, action: str, params: dict[str, object] | None = None) -> dict[str, Any]:
        values = self._params(action, params or {})
        signature = self._signature(values)
        canonical = "&".join(
            f"{_percent_encode(key)}={_percent_encode(value)}"
            for key, value in sorted(values.items())
        )
        body = f"{canonical}&Signature={_percent_encode(signature)}".encode()
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        try:
            status, text = self._transport(
                self.endpoint, method="POST", body=body, headers=headers, timeout=self.timeout
            )
        except Exception as exc:
            raise _transport_error(exc) from exc

        try:
            payload = json.loads(text or "{}")
        except json.JSONDecodeError as exc:
            if status >= 400:
                raise ProvisionerError(
                    "cloud_api_failed",
                    f"Aliyun ECS 返回 HTTP {status}",
                    details={"status": status, "action": action},
                ) from exc
            raise ProvisionerError(
                "cloud_api_unreadable",
                f"Aliyun ECS {action} 返回非 JSON",
                details={"status": status, "action": action},
            ) from exc

        if status >= 400 or (isinstance(payload, dict) and payload.get("Code")):
            code = str(payload.get("Code") or f"HTTP{status}")
            message = str(payload.get("Message") or "")[:200]
            raise self._error(code, message, action=action, status=status)
        if not isinstance(payload, dict):
            raise ProvisionerError(
                "cloud_api_unreadable",
                f"Aliyun ECS {action} 返回结构异常",
                details={"status": status, "action": action},
            )
        return payload

    @staticmethod
    def _error(code: str, message: str, *, action: str, status: int) -> ProvisionerError:
        details = {"provider_code": code, "action": action, "status": status}
        if code in AUTH_ERROR_CODES:
            return ProvisionerError("aliyun_auth_failed", "阿里云 AccessKey 无效或无权限", details=details)
        if code in INVENTORY_ERROR_CODES:
            return ProvisionerError("inventory_unavailable", message or code, details=details)
        if code in CONFIG_ERROR_CODES:
            return ProvisionerError("cloud_request_invalid", message or code, details=details)
        if code in TRANSIENT_ERROR_CODES:
            return ProvisionerError("provider_throttled", message or code, details=details)
        return ProvisionerError("cloud_api_failed", message or code, details=details)

    # -- ECS actions --------------------------------------------------------

    def describe_instances(
        self,
        region_id: str,
        *,
        instance_ids: list[str] | None = None,
        page_size: int = 100,
    ) -> dict[str, dict[str, Any]]:
        params: dict[str, object] = {"RegionId": region_id, "PageSize": page_size}
        if instance_ids:
            params["InstanceIds"] = json.dumps(instance_ids)
        payload = self.call("DescribeInstances", params)
        rows = (payload.get("Instances") or {}).get("Instance") or []
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            if isinstance(row, dict) and row.get("InstanceId"):
                result[str(row["InstanceId"])] = row
        return result

    def describe_spot_prices(
        self, region_id: str, instance_type: str, *, zone_id: str | None = None
    ) -> list[dict[str, Any]]:
        params: dict[str, object] = {
            "RegionId": region_id,
            "InstanceType": instance_type,
            # Aliyun requires this even though the response is network-agnostic.
            # The platform creates VPC instances only.
            "NetworkType": "vpc",
        }
        if zone_id:
            params["ZoneId"] = zone_id
        payload = self.call("DescribeSpotPriceHistory", params)
        rows = (payload.get("SpotPrices") or {}).get("SpotPriceType") or []
        if isinstance(rows, dict):
            rows = [rows]
        return [row for row in rows if isinstance(row, dict)]

    def describe_images(
        self, region_id: str, instance_type: str
    ) -> str | None:
        payload = self.call(
            "DescribeImages",
            {
                "RegionId": region_id,
                "InstanceType": instance_type,
                "ImageOwnerAlias": "system",
                "OSType": "linux",
                "Architecture": "x86_64",
                "PageSize": 50,
            },
        )
        rows = (payload.get("Images") or {}).get("Image") or []
        if isinstance(rows, dict):
            rows = [rows]
        # Prefer Ubuntu 22.04/24.04; fall back to the first system Linux image.
        for row in rows:
            if not isinstance(row, dict):
                continue
            os_name = str(row.get("OSName") or "")
            if "Ubuntu" in os_name:
                image_id = row.get("ImageId")
                if isinstance(image_id, str) and image_id:
                    return image_id
        for row in rows:
            image_id = row.get("ImageId") if isinstance(row, dict) else None
            if isinstance(image_id, str) and image_id:
                return image_id
        return None

    def describe_vswitches(self, region_id: str) -> list[dict[str, Any]]:
        payload = self.call("DescribeVSwitches", {"RegionId": region_id, "PageSize": 50})
        rows = (payload.get("VSwitches") or {}).get("VSwitch") or []
        if isinstance(rows, dict):
            rows = [rows]
        return [row for row in rows if isinstance(row, dict) and row.get("VSwitchId")]

    def describe_security_groups(
        self, region_id: str, *, vpc_id: str | None = None
    ) -> list[dict[str, Any]]:
        params: dict[str, object] = {"RegionId": region_id, "PageSize": 50}
        if vpc_id:
            params["VpcId"] = vpc_id
        payload = self.call("DescribeSecurityGroups", params)
        rows = (payload.get("SecurityGroups") or {}).get("SecurityGroup") or []
        if isinstance(rows, dict):
            rows = [rows]
        return [row for row in rows if isinstance(row, dict) and row.get("SecurityGroupId")]

    def run_instance(
        self,
        *,
        region_id: str,
        image_id: str,
        instance_type: str,
        security_group_id: str,
        vswitch_id: str,
        instance_name: str,
        user_data: str,
        zone_id: str | None = None,
        internet_max_bandwidth_out: int = 5,
    ) -> str:
        params: dict[str, object] = {
            "RegionId": region_id,
            "ImageId": image_id,
            "InstanceType": instance_type,
            "SecurityGroupId": security_group_id,
            "VSwitchId": vswitch_id,
            "InstanceName": instance_name,
            "Amount": 1,
            "SpotStrategy": "SpotAsPriceGo",
            "InternetMaxBandwidthOut": internet_max_bandwidth_out,
            "InternetChargeType": "PayByTraffic",
            "SystemDisk.Category": "cloud_essd",
            "SystemDisk.Size": 40,
            "UserData": base64.b64encode(user_data.encode()).decode(),
            "ClientToken": uuid.uuid4().hex,
            "Tag.1.Key": "managed-by",
            "Tag.1.Value": "ai-eval-platform",
            "Tag.2.Key": "platform-instance",
            "Tag.2.Value": instance_name,
        }
        if zone_id:
            params["ZoneId"] = zone_id
        payload = self.call("RunInstances", params)
        instances = (payload.get("InstanceIdSets") or {}).get("InstanceIdSet") or []
        if not instances or not isinstance(instances[0], str):
            raise ProvisionerError("cloud_api_unreadable", "Aliyun RunInstances 未返回实例 ID")
        return instances[0]

    def delete_instance(self, region_id: str, instance_id: str) -> bool:
        try:
            self.call(
                "DeleteInstance",
                {"RegionId": region_id, "InstanceId": instance_id, "Force": "true"},
            )
        except ProvisionerError as exc:
            if exc.code == "cloud_api_failed" and (
                exc.details.get("provider_code") in NOT_FOUND_INSTANCE_CODES
            ):
                return False
            raise
        return True


def _pool_config(pool: ResourcePool) -> dict[str, Any]:
    return dict(pool.instance_config_json) if isinstance(pool.instance_config_json, dict) else {}


def _labels_config(pool: ResourcePool) -> dict[str, Any]:
    return dict(pool.labels_json) if isinstance(pool.labels_json, dict) else {}


def _runner_labels(settings: Settings, pool: ResourcePool) -> list[str]:
    for source in (_pool_config(pool), _labels_config(pool)):
        for key in ("runs_on", "runner_labels", "labels"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return [item.strip() for item in value.split(",") if item.strip()]
            if isinstance(value, (list, tuple)):
                labels = [str(item).strip() for item in value if str(item).strip()]
                if labels:
                    return labels
    return [item.strip() for item in settings.runner_spot_labels.split(",") if item.strip()]


def _config_str(config: dict[str, Any], key: str) -> str | None:
    value = config.get(key)
    return value if isinstance(value, str) and value.strip() else None


def resolve_aliyun_credentials(
    session: Session, connection: Connection
) -> tuple[str, str]:
    """Decrypt ``{"access_key_id", "access_key_secret"}`` for ``connection``."""

    credential = session.get(Credential, connection.credential_id)
    if credential is None:
        raise ProvisionerError("connection_credential_invalid", "资源连接没有可用凭据")
    if credential.tenant_id != connection.tenant_id or credential.kind != "resource":
        raise ProvisionerError("connection_credential_invalid", "资源连接凭据归属或类型不匹配")
    if credential.revoked_at is not None:
        raise ProvisionerError("connection_credential_invalid", "资源连接凭据已撤销")
    try:
        secret = get_secret_box().decrypt(credential.ciphertext, credential.key_version)
    except SecretBoxError as exc:
        raise ProvisionerError("connection_credential_unreadable", "资源连接凭据无法解密") from exc
    try:
        payload = json.loads(secret)
    except json.JSONDecodeError as exc:
        raise ProvisionerError(
            "connection_credential_invalid",
            "阿里云凭据必须是包含 access_key_id 与 access_key_secret 的 JSON",
        ) from exc
    if not isinstance(payload, dict):
        raise ProvisionerError("connection_credential_invalid", "阿里云凭据结构无效")
    access_key_id = payload.get("access_key_id") or payload.get("accessKeyId")
    access_key_secret = payload.get("access_key_secret") or payload.get("accessKeySecret")
    if not isinstance(access_key_id, str) or not access_key_id.strip():
        raise ProvisionerError("connection_credential_invalid", "阿里云凭据缺少 access_key_id")
    if not isinstance(access_key_secret, str) or not access_key_secret.strip():
        raise ProvisionerError("connection_credential_invalid", "阿里云凭据缺少 access_key_secret")
    return access_key_id.strip(), access_key_secret.strip()


class AliyunEcsProvisioner:
    """Provisioner for Aliyun spot ECS instances with ephemeral runners."""

    provider = ALIYUN_PROVIDER

    def __init__(
        self,
        *,
        settings: Settings,
        registrar: GitHubRunnerRegistrar,
        transport: AliyunTransport | None = None,
        now: Callable[[], datetime] | None = None,
        nonce: Callable[[], str] | None = None,
    ) -> None:
        self.settings = settings
        self.registrar = registrar
        self._transport = transport
        self._now = now
        self._nonce = nonce
        self._image_cache: dict[tuple[str, str], str] = {}
        self._vswitch_cache: dict[str, list[dict[str, Any]]] = {}

    # -- helpers ------------------------------------------------------------

    def capabilities(self, connection: Connection | None) -> set[str]:
        return {CAPABILITY_ECS_SPOT, CAPABILITY_ECS_INVENTORY}

    def _region(self, connection: Connection | None, config: dict[str, Any]) -> str:
        if connection is not None and connection.region:
            return connection.region
        configured = _config_str(config, "region") or _config_str(config, "region_id")
        return configured or self.settings.aliyun_default_region

    def _client(self, session: Session, connection: Connection) -> AliyunEcsClient:
        access_key_id, access_key_secret = resolve_aliyun_credentials(session, connection)
        return AliyunEcsClient(
            access_key_id=access_key_id,
            access_key_secret=access_key_secret,
            endpoint=self.settings.aliyun_ecs_endpoint,
            transport=self._transport,
            now=self._now,
            nonce=self._nonce,
        )

    def _images(self, client: AliyunEcsClient, region: str, instance_type: str) -> str:
        key = (region, instance_type)
        if key not in self._image_cache:
            image_id = client.describe_images(region, instance_type)
            if not image_id:
                raise ProvisionerError(
                    "cloud_request_invalid",
                    "没有可用的系统镜像，请在资源池 instance_config 中配置 image_id",
                    details={"region": region, "instance_type": instance_type},
                )
            self._image_cache[key] = image_id
        return self._image_cache[key]

    def _vswitches(self, client: AliyunEcsClient, region: str) -> list[dict[str, Any]]:
        if region not in self._vswitch_cache:
            self._vswitch_cache[region] = client.describe_vswitches(region)
        return self._vswitch_cache[region]

    def _vswitch_choice(
        self, client: AliyunEcsClient, region: str, config: dict[str, Any]
    ) -> dict[str, Any]:
        """Pick the configured vSwitch, or the first region vSwitch.

        Resolving the vSwitch first keeps the quoted zone and the VPC used for
        the security-group lookup consistent with the instance that
        ``RunInstances`` will actually create.
        """

        configured = _config_str(config, "vswitch_id") or _config_str(config, "v_switch_id")
        switches = self._vswitches(client, region)
        if configured:
            for switch in switches:
                if str(switch.get("VSwitchId")) == configured:
                    return switch
            raise ProvisionerError(
                "cloud_request_invalid",
                "配置的 vSwitch 在区域内不存在",
                details={"region": region, "vswitch_id": configured},
            )
        if not switches:
            raise ProvisionerError(
                "cloud_request_invalid",
                "阿里云账号下没有可用 vSwitch，请在资源池 instance_config 中配置 vswitch_id",
                details={"region": region},
            )
        return switches[0]

    def _zone_for(self, client: AliyunEcsClient, region: str, config: dict[str, Any]) -> str | None:
        configured = _config_str(config, "zone_id") or _config_str(config, "availability_zone")
        if configured:
            return configured
        chosen = self._vswitch_choice(client, region, config)
        zone_id = chosen.get("ZoneId")
        return str(zone_id) if zone_id else None

    def _pick_network(
        self, client: AliyunEcsClient, region: str, config: dict[str, Any]
    ) -> tuple[str, str, str | None]:
        chosen = self._vswitch_choice(client, region, config)
        vswitch_id = _config_str(config, "vswitch_id") or _config_str(config, "v_switch_id")
        if not vswitch_id:
            vswitch_id = str(chosen["VSwitchId"])
        vpc_id = _config_str(config, "vpc_id") or (
            str(chosen["VpcId"]) if chosen.get("VpcId") else None
        )
        zone_id = _config_str(config, "zone_id") or _config_str(config, "availability_zone")
        if not zone_id and chosen.get("ZoneId"):
            zone_id = str(chosen["ZoneId"])
        security_group_id = _config_str(config, "security_group_id") or _config_str(
            config, "security_group"
        )
        if not security_group_id:
            groups = client.describe_security_groups(region, vpc_id=vpc_id)
            if not groups:
                raise ProvisionerError(
                    "cloud_request_invalid",
                    "阿里云账号下没有可用安全组，请在资源池 instance_config 中配置 security_group_id",
                    details={"region": region},
                )
            security_group_id = str(groups[0]["SecurityGroupId"])
        return vswitch_id, security_group_id, zone_id

    def _runner_name(self, pool: ResourcePool) -> str:
        return f"spot-{pool.id[:8]}-{uuid.uuid4().hex[:8]}"

    def _user_data(
        self,
        *,
        pool: ResourcePool,
        runner_name: str,
        encoded_jit: str,
        config: dict[str, Any],
    ) -> str:
        if "platform_base_url" in config:
            configured_base = config.get("platform_base_url")
            base_url = configured_base if isinstance(configured_base, str) else ""
        else:
            base_url = self.settings.runner_callback_base_url
        return render_cloud_init(
            platform_base_url=base_url,
            runner_name=runner_name,
            resource_pool_id=pool.id,
            labels=_runner_labels(self.settings, pool),
            encoded_jit=encoded_jit,
            heartbeat_token=self.settings.runner_heartbeat_token,
            runner_version=self.settings.runner_spot_runner_version,
        )

    @staticmethod
    def _record(
        session: Session,
        pool: ResourcePool,
        event_type: str,
        payload: dict[str, Any],
        *,
        dedupe_key: str | None = None,
    ) -> None:
        # Local import: capacity_control imports this module at load time.
        from ..capacity_control import append_resource_event

        append_resource_event(session, pool, event_type, payload, dedupe_key=dedupe_key)

    # -- Provisioner API ----------------------------------------------------

    def verify(self, *, session: Session, connection: Connection) -> dict[str, Any]:
        config = {"region": connection.region}
        client = self._client(session, connection)
        region = self._region(connection, config)
        rows = client.describe_instances(region, page_size=1)
        return {
            "ok": True,
            "provider": self.provider,
            "region": region,
            "visible_instances": len(rows),
            "capabilities": sorted(self.capabilities(connection)),
        }

    def quote(
        self,
        *,
        connection: Connection | None,
        pool: ResourcePool,
        now: datetime,
        session: Session | None = None,
    ) -> PriceQuote:
        if connection is None:
            raise ProvisionerError("connection_missing", "竞价资源池必须引用资源连接")
        if session is None:
            raise ProvisionerError("cloud_session_missing", "价格查询需要数据库会话")
        config = _pool_config(pool)
        region = self._region(connection, config)
        instance_type = (
            _config_str(config, "instance_type")
            or self.settings.aliyun_default_instance_type
        )
        client = self._client(session, connection)
        zone_id = self._zone_for(client, region, config)
        prices = client.describe_spot_prices(region, instance_type, zone_id=zone_id)
        samples: list[tuple[Decimal, dict[str, Any]]] = []
        for row in prices:
            # The ECS 2014-05-26 response names the hourly price ``SpotPrice``
            # (with ``OriginPrice`` alongside); older fixtures used ``Price``.
            raw_price = row.get("SpotPrice", row.get("Price"))
            try:
                price = Decimal(str(raw_price))
            except (InvalidOperation, TypeError, ValueError):
                continue
            samples.append((price, row))
        if not samples:
            return PriceQuote(
                price_usd=Decimal("0"),
                available=False,
                region=region,
                instance_type=instance_type,
                checked_at=now,
                price_native=Decimal("0"),
                currency="CNY",
                zone_id=zone_id,
            )
        native, sample = min(samples, key=lambda item: item[0])
        try:
            rate = Decimal(str(self.settings.aliyun_cny_per_usd))
            if rate <= 0:
                raise InvalidOperation
            price_usd = (native / rate).quantize(Decimal("0.0001"))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ProvisionerError(
                "price_conversion_invalid", "ALIYUN_CNY_PER_USD 必须是正数"
            ) from exc
        return PriceQuote(
            price_usd=price_usd,
            available=True,
            region=region,
            instance_type=instance_type,
            checked_at=now,
            price_native=native,
            currency="CNY",
            zone_id=str(sample.get("ZoneId") or zone_id or "") or None,
        )

    def create(
        self,
        *,
        session: Session,
        connection: Connection | None,
        pool: ResourcePool,
        spec: InstanceSpec,
        now: datetime,
    ) -> ProvisionedInstance:
        if connection is None:
            raise ProvisionerError("connection_missing", "竞价资源池必须引用资源连接")
        config = _pool_config(pool)
        region = self._region(connection, config)
        client = self._client(session, connection)
        vswitch_id, security_group_id, zone_id = self._pick_network(client, region, config)
        image_id = spec.image_id or _config_str(config, "image_id")
        if not image_id:
            image_id = self._images(client, region, spec.instance_type)
        runner_name = self._runner_name(pool)
        labels = _runner_labels(self.settings, pool)
        group_id = config.get("runner_group_id", self.settings.runner_spot_runner_group_id)
        try:
            group_id = int(group_id) if group_id is not None else None
        except (TypeError, ValueError):
            group_id = None
        jit = self.registrar.generate_jitconfig(
            name=runner_name, labels=labels, runner_group_id=group_id
        )
        user_data = self._user_data(
            pool=pool, runner_name=runner_name, encoded_jit=jit.encoded, config=config
        )
        try:
            provider_instance_id = client.run_instance(
                region_id=region,
                image_id=image_id,
                instance_type=spec.instance_type,
                security_group_id=security_group_id,
                vswitch_id=vswitch_id,
                instance_name=runner_name,
                user_data=user_data,
                zone_id=zone_id,
                internet_max_bandwidth_out=int(config.get("internet_max_bandwidth_out") or 5),
            )
        except ProvisionerError:
            # Do not leave a JIT runner pointing at an instance that never
            # existed. Deletion is best effort; the original failure wins.
            try:
                self.registrar.delete_runner(runner_id=jit.runner_id, name=jit.name)
            except ProvisionerError:
                pass
            raise
        instance = ProvisionedInstance(
            tenant_id=pool.tenant_id,
            resource_pool_id=pool.id,
            provider_instance_id=provider_instance_id,
            status="creating",
            spot_strategy="spot",
            cost_json={
                "provider": self.provider,
                "runner_name": jit.name,
                "github_runner_id": jit.runner_id,
                "region": region,
                "zone_id": zone_id,
                "instance_type": spec.instance_type,
                "image_id": image_id,
                "vswitch_id": vswitch_id,
                "security_group_id": security_group_id,
                "requested_at": _iso(now),
            },
        )
        session.add(instance)
        session.flush()
        return instance

    def release(
        self,
        *,
        session: Session,
        instance: ProvisionedInstance,
        now: datetime,
    ) -> None:
        pool = session.get(ResourcePool, instance.resource_pool_id)
        if pool is None:
            raise ProvisionerError("resource_pool_not_found", "资源池不存在，无法释放实例")
        connection = session.get(Connection, pool.connection_id) if pool.connection_id else None
        if connection is None:
            raise ProvisionerError("connection_missing", "资源池没有资源连接，无法释放实例")
        cost = dict(instance.cost_json) if isinstance(instance.cost_json, dict) else {}
        runner_name = cost.get("runner_name")
        runner_db = session.get(Runner, instance.runner_id) if instance.runner_id else None
        if not isinstance(runner_name, str) or not runner_name:
            runner_name = runner_db.provider_runner_id if runner_db is not None else None
        if runner_name:
            github_runner_id = cost.get("github_runner_id")
            self.registrar.delete_runner(
                runner_id=github_runner_id if isinstance(github_runner_id, int) else None,
                name=runner_name,
            )
        client = self._client(session, connection)
        client.delete_instance(self._region(connection, _pool_config(pool)), instance.provider_instance_id)
        instance.status = "released"
        instance.released_at = now
        cost["released_at"] = _iso(now)
        instance.cost_json = cost
        session.flush()

    def _reclaim(
        self,
        session: Session,
        pool: ResourcePool,
        instance: ProvisionedInstance,
        *,
        reason: str,
        now: datetime,
        event_type: str,
        detail: str | None = None,
    ) -> None:
        from ..capacity_control import EVENT_RELEASE_FAILED

        cost = dict(instance.cost_json) if isinstance(instance.cost_json, dict) else {}
        cost["reclaimed_reason"] = reason
        cost["reclaimed_at"] = _iso(now)
        if detail:
            cost["reclaim_detail"] = detail
        instance.cost_json = cost
        if instance.runner_id:
            runner = session.get(Runner, instance.runner_id)
            if runner is not None:
                runner.status = "offline"
                runner.deregistered_at = runner.deregistered_at or now
                session.flush()
        self._record(
            session,
            pool,
            event_type,
            {
                "instance_id": instance.id,
                "provider_instance_id": instance.provider_instance_id,
                "reason": reason,
                "detail": detail,
                "runner_name": cost.get("runner_name"),
            },
            dedupe_key=f"{pool.id}:{event_type}:{instance.id}",
        )
        try:
            self.release(session=session, instance=instance, now=now)
        except ProvisionerError as exc:
            instance.status = "draining"
            session.flush()
            self._record(
                session,
                pool,
                EVENT_RELEASE_FAILED,
                {
                    "instance_id": instance.id,
                    "provider_instance_id": instance.provider_instance_id,
                    "code": exc.code,
                    "message": exc.message,
                    "reason": reason,
                },
                dedupe_key=f"{pool.id}:release_failed:{instance.id}:{exc.code}",
            )

    def sync(
        self,
        *,
        session: Session,
        connection: Connection | None,
        pool: ResourcePool,
        now: datetime,
    ) -> dict[str, int]:
        from ..capacity_control import (
            EVENT_INSTANCE_CREATE_TIMEOUT,
            EVENT_INSTANCE_INTERRUPTED,
            EVENT_INSTANCE_REGISTERED,
            EVENT_PROVIDER_SYNC_FAILED,
        )

        counters = {"checked": 0, "online": 0, "interrupted": 0, "timeouts": 0, "errors": 0}
        if connection is None:
            return counters
        instances = list(
            session.scalars(
                select(ProvisionedInstance)
                .where(
                    ProvisionedInstance.resource_pool_id == pool.id,
                    ProvisionedInstance.status.in_(INSTANCE_ACTIVE_STATUSES),
                )
                .order_by(ProvisionedInstance.id)
            )
        )
        if not instances:
            return counters
        config = _pool_config(pool)
        region = self._region(connection, config)
        try:
            client = self._client(session, connection)
            remote = client.describe_instances(
                region, instance_ids=[item.provider_instance_id for item in instances]
            )
        except ProvisionerError as exc:
            counters["errors"] = 1
            self._record(
                session,
                pool,
                EVENT_PROVIDER_SYNC_FAILED,
                {"code": exc.code, "message": exc.message},
                dedupe_key=f"{pool.id}:sync_failed:{exc.code}",
            )
            raise
        timeout = timedelta(seconds=self.settings.eval_spot_register_timeout_seconds)
        lost_grace = timedelta(seconds=self.settings.eval_spot_runner_lost_grace_seconds)
        heartbeat_timeout = timedelta(seconds=self.settings.runner_heartbeat_timeout_seconds)
        for instance in instances:
            counters["checked"] += 1
            cost = dict(instance.cost_json) if isinstance(instance.cost_json, dict) else {}
            runner_name = cost.get("runner_name")
            runner = (
                session.scalar(
                    select(Runner).where(
                        Runner.resource_pool_id == pool.id,
                        Runner.provider_runner_id == runner_name,
                    )
                )
                if isinstance(runner_name, str) and runner_name
                else None
            )
            age = (now - _aware(instance.created_at)) if _aware(instance.created_at) else timedelta(0)
            row = remote.get(instance.provider_instance_id)
            if row is None:
                # Aliyun's list API is eventually consistent right after
                # RunInstances; only a persistent absence is an interruption.
                if age > timedelta(seconds=300):
                    counters["interrupted"] += 1
                    self._reclaim(
                        session,
                        pool,
                        instance,
                        reason="instance_missing",
                        now=now,
                        event_type=EVENT_INSTANCE_INTERRUPTED,
                    )
                continue
            status = str(row.get("Status") or "")
            locks = (row.get("OperationLocks") or {}).get("LockReason") or []
            lock_text = " ".join(str(item) for item in locks).lower()
            unhealthy_lock = any(token in lock_text for token in SPOT_INTERRUPTION_LOCKS)
            if row.get("Status") in ("Stopped", "Stopping"):
                counters["interrupted"] += 1
                self._reclaim(
                    session,
                    pool,
                    instance,
                    reason="provider_stopped",
                    now=now,
                    event_type=EVENT_INSTANCE_INTERRUPTED,
                    detail=status,
                )
                continue
            if unhealthy_lock or status not in ("Running", "Starting", "Pending"):
                counters["interrupted"] += 1
                self._reclaim(
                    session,
                    pool,
                    instance,
                    reason="spot_interrupted" if unhealthy_lock else "provider_unhealthy",
                    now=now,
                    event_type=EVENT_INSTANCE_INTERRUPTED,
                    detail=lock_text or status,
                )
                continue
            if status != "Running":
                if instance.status == "requested":
                    instance.status = "creating"
                if age > timeout:
                    counters["timeouts"] += 1
                    self._reclaim(
                        session,
                        pool,
                        instance,
                        reason="create_timeout",
                        now=now,
                        event_type=EVENT_INSTANCE_CREATE_TIMEOUT,
                    )
                continue
            if runner is not None and runner.status in ("online", "busy"):
                if instance.status != "online":
                    instance.status = "online"
                    instance.runner_id = runner.id
                    instance.ready_at = instance.ready_at or runner.registered_at or now
                    counters["online"] += 1
                    cost["last_state"] = "Running"
                    cost["registered_at"] = _iso(now)
                    instance.cost_json = cost
                    self._record(
                        session,
                        pool,
                        EVENT_INSTANCE_REGISTERED,
                        {
                            "instance_id": instance.id,
                            "provider_instance_id": instance.provider_instance_id,
                            "runner_id": runner.id,
                            "runner_name": runner.provider_runner_id,
                        },
                        dedupe_key=f"{pool.id}:instance_registered:{instance.id}",
                    )
                last_seen = _aware(runner.last_heartbeat_at)
                if last_seen is not None and now - last_seen > heartbeat_timeout + lost_grace:
                    counters["interrupted"] += 1
                    self._reclaim(
                        session,
                        pool,
                        instance,
                        reason="runner_heartbeat_lost",
                        now=now,
                        event_type=EVENT_INSTANCE_INTERRUPTED,
                        detail=f"last_heartbeat={_iso(last_seen)}",
                    )
                continue
            if instance.status == "online":
                # The worker's offline sweep may have already flipped the
                # runner, or the row is gone after a restart. Reclaim only
                # after the same heartbeat grace so a healthy long job is
                # never killed by a single stale sample.
                last_seen = _aware(runner.last_heartbeat_at) if runner is not None else None
                if runner is None:
                    if age > timeout + lost_grace:
                        counters["interrupted"] += 1
                        self._reclaim(
                            session,
                            pool,
                            instance,
                            reason="runner_missing",
                            now=now,
                            event_type=EVENT_INSTANCE_INTERRUPTED,
                        )
                elif last_seen is not None and now - last_seen > heartbeat_timeout + lost_grace:
                    counters["interrupted"] += 1
                    self._reclaim(
                        session,
                        pool,
                        instance,
                        reason="runner_heartbeat_lost",
                        now=now,
                        event_type=EVENT_INSTANCE_INTERRUPTED,
                        detail=f"last_heartbeat={_iso(last_seen)}",
                    )
                continue
            if instance.status in ("requested", "creating"):
                instance.status = "registering"
            if age > timeout:
                counters["timeouts"] += 1
                self._reclaim(
                    session,
                    pool,
                    instance,
                    reason="register_timeout",
                    now=now,
                    event_type=EVENT_INSTANCE_CREATE_TIMEOUT,
                )
        session.flush()
        return counters

    def health(self, *, instance: ProvisionedInstance) -> str:
        if instance.status == "online":
            return "healthy"
        if instance.status == "interrupted":
            return "interrupted"
        if instance.status == "released":
            return "released"
        return "starting"
