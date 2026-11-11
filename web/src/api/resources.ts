import { request } from "./evaluations";

export type ProvisioningMode = "fixed" | "spot";
export type ConnectionStatus = "unverified" | "verified" | "failed";

export interface ResourceCredential {
  id: string;
  kind: "model" | "resource" | string;
  provider: string;
  secret_mask: string | null;
  revoked_at: string | null;
  last_rotated_at: string | null;
}

export interface ResourceConnection {
  id: string;
  name: string;
  provider: string;
  region: string | null;
  capabilities: string[];
  status: ConnectionStatus | string;
  last_verified_at: string | null;
  credential_mask: string | null;
}

export interface RunnerSummary {
  total: number;
  online: number;
  busy: number;
  draining: number;
  offline: number;
  last_heartbeat_at: string | null;
  live: number;
}

export interface CapacityState {
  pool_id: string;
  provisioning_mode: ProvisioningMode | string;
  checked_at: string;
  queued_attempts: number;
  idle_runners: number;
  live_runners: number;
  starting_instances: number;
  runners_per_instance: number;
  min_capacity: number;
  max_capacity: number;
  current_size: number;
  desired_capacity: number;
  capacity_gap: number;
  price_cny: string | null;
  price_threshold_cny: string | null;
  price_available: boolean | null;
  provider_error: string | null;
  capacity_reason: string | null;
  last_checked_at: string | null;
  next_check_at: string | null;
}

export interface PoolUtilization {
  /** busy runners / max_capacity, 0–1 */
  ratio: number;
  busy_runners: number;
  live_runners: number;
  capacity: number;
}

export interface PoolCost {
  today_cny: string;
  spot_cny: string;
  fixed_cny: string;
  instance_count: number;
  unpriced_instance_count: number;
  window_start: string | null;
  window_end: string | null;
  basis: string;
}

export interface PoolQueueRisk {
  at_risk: boolean;
  queued_attempts: number;
  threshold: number;
  near_capacity: boolean;
  live_runners: number;
  capacity: number;
  live_ratio: number;
}

export interface PoolUsage {
  utilization: PoolUtilization;
  cost: PoolCost;
  queue_risk: PoolQueueRisk;
}

export interface ResourcePool {
  id: string;
  project_id: string;
  connection_id: string | null;
  name: string;
  provider_key: string;
  runner_group: string | null;
  labels: Record<string, unknown>;
  status: string;
  provisioning_mode: ProvisioningMode | string;
  min_capacity: number;
  max_capacity: number;
  instance_config: Record<string, unknown>;
  price_threshold_cny: string | null;
  price_cny: string | null;
  capacity_reason: string | null;
  last_checked_at: string | null;
  next_check_at: string | null;
  capacity_state: CapacityState;
  runners: RunnerSummary;
  usage: PoolUsage;
}

export interface ResourceEventItem {
  id: string;
  event_type: string;
  resource_pool_id: string | null;
  connection_id: string | null;
  payload: Record<string, unknown>;
  dedupe_key: string | null;
  created_at: string | null;
}

export interface PoolRunner {
  id: string;
  name: string;
  status: string;
  labels: unknown;
  last_heartbeat_at: string | null;
  busy_case_attempt_id: string | null;
}

export interface ResourcePoolDetail extends ResourcePool {
  events: ResourceEventItem[];
}

export interface ProvisioningStep {
  key: string;
  status: "passed" | "failed" | "skipped" | string;
  at: string | null;
  detail: Record<string, unknown>;
}

export interface ProvisioningTestResult {
  instance: {
    id: string;
    provider_instance_id: string;
    resource_pool_id: string;
    status: string;
    spot_strategy: string | null;
    created_at: string | null;
  };
  price_cny: string | null;
  price_threshold_cny: string | null;
  price_available: boolean;
  region: string | null;
  instance_type: string | null;
  released: boolean;
  steps: ProvisioningStep[];
}

export interface ConnectionCreateInput {
  name: string;
  provider: string;
  region?: string | null;
  credential_id: string;
  capabilities: string[];
}

export interface PoolWriteInput {
  name: string;
  connection_id: string | null;
  provisioning_mode: ProvisioningMode;
  min_capacity: number;
  max_capacity: number;
  price_threshold_cny?: string | null;
  instance_config?: Record<string, unknown>;
  runner_group?: string | null;
  labels?: Record<string, unknown>;
}

interface Dictionary {
  [key: string]: unknown;
}

function record(value: unknown): Dictionary | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Dictionary)
    : null;
}

function text(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function optionalText(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

function number(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function booleanOrNull(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function textList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string" && item.length > 0)
    : [];
}

function dictionary(value: unknown): Record<string, unknown> {
  const item = record(value);
  return item ? { ...item } : {};
}

function items(payload: unknown): Dictionary[] {
  const body = record(payload);
  const list = body?.items;
  return Array.isArray(list)
    ? list.filter((item): item is Dictionary => record(item) !== null)
    : [];
}

export function parseRunnerSummary(value: unknown): RunnerSummary {
  const item = record(value) ?? {};
  const online = number(item.online);
  const busy = number(item.busy);
  return {
    total: number(item.total),
    online,
    busy,
    draining: number(item.draining),
    offline: number(item.offline),
    last_heartbeat_at: optionalText(item.last_heartbeat_at),
    live: online + busy,
  };
}

export function parseCapacityState(value: unknown): CapacityState {
  const item = record(value) ?? {};
  return {
    pool_id: text(item.pool_id),
    provisioning_mode: text(item.provisioning_mode, "fixed"),
    checked_at: text(item.checked_at),
    queued_attempts: number(item.queued_attempts),
    idle_runners: number(item.idle_runners),
    live_runners: number(item.live_runners),
    starting_instances: number(item.starting_instances),
    runners_per_instance: number(item.runners_per_instance, 1),
    min_capacity: number(item.min_capacity),
    max_capacity: number(item.max_capacity),
    current_size: number(item.current_size),
    desired_capacity: number(item.desired_capacity),
    capacity_gap: number(item.capacity_gap),
    price_cny: optionalText(item.price_cny),
    price_threshold_cny: optionalText(item.price_threshold_cny),
    price_available: booleanOrNull(item.price_available),
    provider_error: optionalText(item.provider_error),
    capacity_reason: optionalText(item.capacity_reason),
    last_checked_at: optionalText(item.last_checked_at),
    next_check_at: optionalText(item.next_check_at),
  };
}

function parseUsage(value: unknown): PoolUsage {
  const usage = record(value) ?? {};
  const utilization = record(usage.utilization) ?? {};
  const cost = record(usage.cost) ?? {};
  const queueRisk = record(usage.queue_risk) ?? {};
  return {
    utilization: {
      ratio: number(utilization.ratio),
      busy_runners: number(utilization.busy_runners),
      live_runners: number(utilization.live_runners),
      capacity: number(utilization.capacity),
    },
    cost: {
      today_cny: text(cost.today_cny, "0"),
      spot_cny: text(cost.spot_cny, "0"),
      fixed_cny: text(cost.fixed_cny, "0"),
      instance_count: number(cost.instance_count),
      unpriced_instance_count: number(cost.unpriced_instance_count),
      window_start: optionalText(cost.window_start),
      window_end: optionalText(cost.window_end),
      basis: text(cost.basis),
    },
    queue_risk: {
      at_risk: queueRisk.at_risk === true,
      queued_attempts: number(queueRisk.queued_attempts),
      threshold: number(queueRisk.threshold),
      near_capacity: queueRisk.near_capacity === true,
      live_runners: number(queueRisk.live_runners),
      capacity: number(queueRisk.capacity),
      live_ratio: number(queueRisk.live_ratio),
    },
  };
}

function parseConnection(value: Dictionary): ResourceConnection {
  return {
    id: text(value.id),
    name: text(value.name),
    provider: text(value.provider),
    region: optionalText(value.region),
    capabilities: textList(value.capabilities),
    status: text(value.status, "unverified"),
    last_verified_at: optionalText(value.last_verified_at),
    credential_mask: optionalText(value.credential_mask),
  };
}

function parsePool(value: Dictionary): ResourcePool {
  return {
    id: text(value.id),
    project_id: text(value.project_id),
    connection_id: optionalText(value.connection_id),
    name: text(value.name),
    provider_key: text(value.provider_key),
    runner_group: optionalText(value.runner_group),
    labels: dictionary(value.labels),
    status: text(value.status, "active"),
    provisioning_mode: text(value.provisioning_mode, "fixed"),
    min_capacity: number(value.min_capacity),
    max_capacity: number(value.max_capacity),
    instance_config: dictionary(value.instance_config),
    price_threshold_cny: optionalText(value.price_threshold_cny),
    price_cny: optionalText(value.price_cny),
    capacity_reason: optionalText(value.capacity_reason),
    last_checked_at: optionalText(value.last_checked_at),
    next_check_at: optionalText(value.next_check_at),
    capacity_state: parseCapacityState(value.capacity_state),
    runners: parseRunnerSummary(value.runners),
    usage: parseUsage(value.usage),
  };
}

function parseEvent(value: Dictionary): ResourceEventItem {
  return {
    id: text(value.id),
    event_type: text(value.event_type),
    resource_pool_id: optionalText(value.resource_pool_id),
    connection_id: optionalText(value.connection_id),
    payload: dictionary(value.payload),
    dedupe_key: optionalText(value.dedupe_key),
    created_at: optionalText(value.created_at),
  };
}

function parseRunner(value: Dictionary): PoolRunner {
  return {
    id: text(value.id),
    name: text(value.name),
    status: text(value.status, "offline"),
    labels: value.labels ?? [],
    last_heartbeat_at: optionalText(value.last_heartbeat_at),
    busy_case_attempt_id: optionalText(value.busy_case_attempt_id),
  };
}

function parseStep(value: Dictionary): ProvisioningStep {
  return {
    key: text(value.key),
    status: text(value.status, "skipped"),
    at: optionalText(value.at),
    detail: dictionary(value.detail),
  };
}

export async function fetchResourceCredentials(
  signal?: AbortSignal,
): Promise<ResourceCredential[]> {
  const payload = await request("/api/v1/credentials", { signal });
  return items(payload)
    .map((item) => ({
      id: text(item.id),
      kind: text(item.kind, "model"),
      provider: text(item.provider),
      secret_mask: optionalText(item.secret_mask),
      revoked_at: optionalText(item.revoked_at),
      last_rotated_at: optionalText(item.last_rotated_at),
    }))
    .filter((item) => item.id && item.kind === "resource" && !item.revoked_at);
}

export async function createResourceCredential(input: {
  provider: string;
  secret: string;
}): Promise<ResourceCredential> {
  const payload = record(
    await request("/api/v1/credentials", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        provider: input.provider,
        secret: input.secret,
        kind: "resource",
      }),
    }),
  );
  const item = payload ?? {};
  return {
    id: text(item.id),
    kind: text(item.kind, "resource"),
    provider: text(item.provider),
    secret_mask: optionalText(item.secret_mask),
    revoked_at: optionalText(item.revoked_at),
    last_rotated_at: optionalText(item.last_rotated_at),
  };
}

export async function fetchResourceConnections(
  signal?: AbortSignal,
): Promise<ResourceConnection[]> {
  const payload = await request("/api/v1/connections", { signal });
  return items(payload).map(parseConnection);
}

export async function createResourceConnection(
  input: ConnectionCreateInput,
): Promise<ResourceConnection> {
  const payload = await request("/api/v1/connections", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      name: input.name,
      provider: input.provider,
      region: input.region ?? null,
      credential_id: input.credential_id,
      capabilities: input.capabilities,
    }),
  });
  return parseConnection(record(payload) ?? {});
}

export async function verifyResourceConnection(
  connectionId: string,
): Promise<ResourceConnection> {
  const payload = await request(`/api/v1/connections/${connectionId}/test`, {
    method: "POST",
  });
  return parseConnection(record(payload) ?? {});
}

export async function fetchResourcePools(
  signal?: AbortSignal,
): Promise<ResourcePool[]> {
  const payload = await request("/api/v1/resource-pools", { signal });
  return items(payload).map(parsePool);
}

export async function fetchResourcePool(
  poolId: string,
  signal?: AbortSignal,
): Promise<ResourcePoolDetail> {
  const payload = record(await request(`/api/v1/resource-pools/${poolId}`, { signal }));
  const item = payload ?? {};
  return {
    ...parsePool(item),
    events: Array.isArray(item.events)
      ? item.events
          .filter((event): event is Dictionary => record(event) !== null)
          .map(parseEvent)
      : [],
  };
}

export async function fetchPoolRunners(
  poolId: string,
  signal?: AbortSignal,
): Promise<PoolRunner[]> {
  const payload = await request(`/api/v1/resource-pools/${poolId}/runners`, { signal });
  return items(payload).map(parseRunner);
}

export async function createResourcePool(input: PoolWriteInput): Promise<ResourcePool> {
  const payload = await request("/api/v1/resource-pools", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(input),
  });
  return parsePool(record(payload) ?? {});
}

export async function updateResourcePool(
  poolId: string,
  input: PoolWriteInput,
): Promise<ResourcePool> {
  const payload = await request(`/api/v1/resource-pools/${poolId}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(input),
  });
  return parsePool(record(payload) ?? {});
}

export async function testProvisioning(
  poolId: string,
): Promise<ProvisioningTestResult> {
  const payload = record(
    await request(`/api/v1/resource-pools/${poolId}/test-provisioning`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ confirm_cost: true }),
    }),
  );
  const item = payload ?? {};
  const instance = record(item.instance) ?? {};
  return {
    instance: {
      id: text(instance.id),
      provider_instance_id: text(instance.provider_instance_id),
      resource_pool_id: text(instance.resource_pool_id),
      status: text(instance.status),
      spot_strategy: optionalText(instance.spot_strategy),
      created_at: optionalText(instance.created_at),
    },
    price_cny: optionalText(item.price_cny),
    price_threshold_cny: optionalText(item.price_threshold_cny),
    price_available: item.price_available === true,
    region: optionalText(item.region),
    instance_type: optionalText(item.instance_type),
    released: item.released === true,
    steps: Array.isArray(item.steps)
      ? item.steps
          .filter((step): step is Dictionary => record(step) !== null)
          .map(parseStep)
      : [],
  };
}

/** Labels may be an object or an array depending on the writer. */
export function labelList(labels: unknown): string[] {
  if (Array.isArray(labels)) {
    return labels.filter((item): item is string => typeof item === "string" && item.length > 0);
  }
  const item = record(labels);
  if (!item) return [];
  return Object.entries(item).map(([key, value]) =>
    value === true || value === "" ? key : `${key}=${String(value)}`,
  );
}
