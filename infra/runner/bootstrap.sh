#!/usr/bin/env bash
# One-command, idempotent bootstrap for a fixed Linux self-hosted runner host.
#
# Usage (from a machine with SSH access; single command):
#   ssh -i <key> root@<host> 'bash -s' -- < infra/runner/bootstrap.sh
# with parameters passed as environment variables, e.g.:
#   ssh root@<host> "PLATFORM_BASE_URL=http://127.0.0.1:8000 \
#     RUNNER_HEARTBEAT_TOKEN=... bash -s" < infra/runner/bootstrap.sh
#
# Parameters (all optional unless noted; empty means "keep existing value"):
#   PLATFORM_BASE_URL          platform API base URL (required on fresh env file)
#   RUNNER_HEARTBEAT_TOKEN     runner-plane token (required on fresh env file)
#   RUNNER_ID / RUNNER_NAME    runner identity (default fixed-linux-eval)
#   RUNNER_LABELS              comma-separated labels (default self-hosted,linux,eval)
#   HEARTBEAT_INTERVAL_SECONDS heartbeat interval, >=5 (default 30)
#   SYNC_AGENT=1               overwrite a drifted heartbeat.mjs with this copy
#   RUNNER_INSTALL=1           also install/register the GitHub Actions runner
#                              (requires RUNNER_REGISTRATION_TOKEN + GITHUB_REPO;
#                              never touches an existing registration otherwise)
#   RUNNER_SELF_TEST=1         send one heartbeat and report the result
#
# Test hooks: INSTALL_ROOT (filesystem prefix), SYSTEMCTL, NODE_BIN.
# No long-lived secret is embedded or echoed; the token only flows env → env
# file (root:root 600), matching /etc/eval-platform/runner.env convention.
set -euo pipefail

INSTALL_ROOT="${INSTALL_ROOT:-}"
RUNNER_DIR_SYS="/opt/ai-eval-platform-runner"
ETC_DIR_SYS="/etc/eval-platform"
UNIT_DIR_SYS="/etc/systemd/system"
AGENT_SERVICE="${AGENT_SERVICE:-eval-platform-heartbeat}"

SYSTEMCTL="${SYSTEMCTL:-systemctl}"
NODE_BIN="${NODE_BIN:-$(command -v node || true)}"
if [ -z "$NODE_BIN" ]; then
  echo "bootstrap: 未找到 node（Need node >= 20）" >&2
  exit 1
fi

RUNNER_DIR="$INSTALL_ROOT$RUNNER_DIR_SYS"
ETC_DIR="$INSTALL_ROOT$ETC_DIR_SYS"
UNIT_DIR="$INSTALL_ROOT$UNIT_DIR_SYS"
UNIT_FILE="$UNIT_DIR/$AGENT_SERVICE.service"
ENV_FILE="$ETC_DIR/runner.env"

mkdir -p "$RUNNER_DIR" "$ETC_DIR" "$UNIT_DIR"

CHANGED=()

# --- heartbeat agent --------------------------------------------------------
AGENT_STATE="" # installed | agent-synced | "" (unchanged/kept)
install_agent() {
  local target="$RUNNER_DIR/heartbeat.mjs"
  local want
  want="$(mktemp)"
  cat > "$want" <<'AGENT_EOF'
#!/usr/bin/env node
import process from "node:process";

export function parseLabels(value) {
  return (value || "self-hosted,linux,eval")
    .split(",")
    .map((label) => label.trim())
    .filter(Boolean);
}

export function buildPayload(env) {
  const attempt = env.RUNNER_CURRENT_CASE_ATTEMPT_ID || null;
  return {
    runner_id: env.RUNNER_ID || env.RUNNER_NAME || "fixed-linux-runner",
    name: env.RUNNER_NAME || env.RUNNER_ID || "fixed-linux-runner",
    labels: parseLabels(env.RUNNER_LABELS),
    status: attempt ? "busy" : "online",
    current_case_attempt_id: attempt,
  };
}

export async function sendHeartbeat(env, fetchImpl = fetch) {
  const base = (env.PLATFORM_BASE_URL || "").replace(/\/$/, "");
  if (!base) throw new Error("PLATFORM_BASE_URL 未配置");
  if (!env.RUNNER_HEARTBEAT_TOKEN) throw new Error("RUNNER_HEARTBEAT_TOKEN 未配置");
  const response = await fetchImpl(`${base}/runner/v1/runners/heartbeat`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${env.RUNNER_HEARTBEAT_TOKEN}`,
    },
    body: JSON.stringify(buildPayload(env)),
    signal: AbortSignal.timeout(5000),
  });
  if (!response.ok) {
    throw new Error(`heartbeat failed: HTTP ${response.status} ${await response.text()}`);
  }
  return response.json();
}

async function main() {
  const interval = Math.max(5, Number(process.env.HEARTBEAT_INTERVAL_SECONDS || 30));
  let stopped = false;
  process.on("SIGTERM", () => { stopped = true; });
  process.on("SIGINT", () => { stopped = true; });
  while (!stopped) {
    try {
      const result = await sendHeartbeat(process.env);
      console.log(JSON.stringify({ event: "heartbeat", status: result.status ?? "ok", at: new Date().toISOString() }));
    } catch (error) {
      console.error(`heartbeat: ${error.message ?? error}`);
    }
    await new Promise((resolve) => setTimeout(resolve, interval * 1000));
  }
}

if (import.meta.url === `file://${process.argv[1]}`) main();
AGENT_EOF
  if [ -f "$target" ] && cmp -s "$want" "$target"; then
    rm -f "$want"
    return 0
  fi
  if [ -f "$target" ] && [ "${SYNC_AGENT:-0}" != "1" ]; then
    rm -f "$want"
    AGENT_DRIFTED=1
    return 0
  fi
  mv "$want" "$target"
  chmod 755 "$target"
  if [ "${SYNC_AGENT:-0}" = "1" ]; then
    AGENT_STATE="agent-synced"
  else
    AGENT_STATE="installed"
  fi
}

# --- env file ---------------------------------------------------------------
ENV_CHANGED=0
merge_env_file() {
  local file="$1"
  shift
  if [ ! -f "$file" ]; then
    : > "$file"
    ENV_CHANGED=1
  fi
  local kv key value
  for kv in "$@"; do
    key="${kv%%=*}"
    value="${kv#*=}"
    # Empty value means "keep whatever is already there".
    [ -n "$value" ] || continue
    if grep -Fqx "$kv" "$file"; then
      continue
    fi
    if grep -q "^${key}=" "$file"; then
      awk -v k="$key" -v v="$value" 'BEGIN{FS=OFS="="} $1==k{print k "=" v; next} {print}' \
        "$file" > "$file.tmp"
      mv "$file.tmp" "$file"
    else
      printf '%s\n' "$kv" >> "$file"
    fi
    ENV_CHANGED=1
  done
  chmod 600 "$file"
}

# --- systemd unit -----------------------------------------------------------
UNIT_STATE="" # "unit" when (re)written
install_unit() {
  local want
  want="$(mktemp)"
  cat > "$want" <<UNIT_EOF
[Unit]
Description=AI Eval Platform fixed runner heartbeat
After=network-online.target

[Service]
EnvironmentFile=$ETC_DIR_SYS/runner.env
ExecStart=$NODE_BIN $RUNNER_DIR_SYS/heartbeat.mjs
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
UNIT_EOF
  if [ -f "$UNIT_FILE" ] && cmp -s "$want" "$UNIT_FILE"; then
    rm -f "$want"
    return 0
  fi
  mv "$want" "$UNIT_FILE"
  chmod 644 "$UNIT_FILE"
  UNIT_STATE="unit"
}

# --- plan the provided keys -------------------------------------------------
if [ ! -f "$ENV_FILE" ]; then
  for required in PLATFORM_BASE_URL RUNNER_HEARTBEAT_TOKEN; do
    if [ -z "${!required:-}" ]; then
      echo "bootstrap: 全新安装需要 ${required}（env 提供；机器上只落 root:root 600 的 env 文件）" >&2
      exit 1
    fi
  done
fi

ENVS=()
ENVS+=("PLATFORM_BASE_URL=${PLATFORM_BASE_URL:-}")
ENVS+=("RUNNER_HEARTBEAT_TOKEN=${RUNNER_HEARTBEAT_TOKEN:-}")
ENVS+=("RUNNER_ID=${RUNNER_ID:-fixed-linux-eval}")
ENVS+=("RUNNER_NAME=${RUNNER_NAME:-fixed-linux-eval}")
ENVS+=("RUNNER_LABELS=${RUNNER_LABELS:-self-hosted,linux,eval}")
ENVS+=("HEARTBEAT_INTERVAL_SECONDS=${HEARTBEAT_INTERVAL_SECONDS:-30}")

install_agent
if [ -n "$AGENT_STATE" ]; then
  CHANGED+=("$AGENT_STATE")
fi

merge_env_file "$ENV_FILE" "${ENVS[@]}"
if [ "$ENV_CHANGED" -eq 1 ]; then
  CHANGED+=("env")
fi

install_unit
if [ -n "$UNIT_STATE" ]; then
  CHANGED+=("$UNIT_STATE")
fi

if [ -n "$UNIT_STATE" ]; then
  "$SYSTEMCTL" daemon-reload
  "$SYSTEMCTL" restart "$AGENT_SERVICE"
  CHANGED+=("service-restarted")
elif [ "$ENV_CHANGED" -eq 1 ]; then
  # env changed → the running service must re-read EnvironmentFile.
  "$SYSTEMCTL" restart "$AGENT_SERVICE"
  CHANGED+=("service-restarted")
fi

"$SYSTEMCTL" enable --now "$AGENT_SERVICE"
"$SYSTEMCTL" is-active "$AGENT_SERVICE" || true

# --- optional GitHub Actions runner registration (default: off) -------------
if [ "${RUNNER_INSTALL:-0}" = "1" ]; then
  for required in RUNNER_REGISTRATION_TOKEN GITHUB_REPO; do
    if [ -z "${!required:-}" ]; then
      echo "bootstrap: RUNNER_INSTALL=1 需要 ${required}" >&2
      exit 1
    fi
  done
  RUNNER_VERSION="${RUNNER_VERSION:-2.338.2}"
  if [ ! -f "$RUNNER_DIR/config.sh" ]; then
    TARBALL="actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz"
    curl -fsSL -o "$RUNNER_DIR/runner.tar.gz" \
      "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/${TARBALL}"
    tar -xzf "$RUNNER_DIR/runner.tar.gz" -C "$RUNNER_DIR"
    rm -f "$RUNNER_DIR/runner.tar.gz"
    CHANGED+=("runner-binaries")
  fi
  if [ ! -f "$RUNNER_DIR/.credentials" ] || [ "${FORCE_REREGISTER:-0}" = "1" ]; then
    (cd "$RUNNER_DIR" && ./config.sh \
      --url "$GITHUB_REPO" \
      --token "$RUNNER_REGISTRATION_TOKEN" \
      --name "${RUNNER_NAME:-fixed-linux-eval}" \
      --labels "${RUNNER_LABELS:-self-hosted,linux,eval}" \
      --replace --unattended)
    (cd "$RUNNER_DIR" && ./svc.sh install && ./svc.sh start)
    CHANGED+=("runner-registered")
  fi
fi

# --- optional single-shot self test -----------------------------------------
SELF_TEST_STATUS="skipped"
if [ "${RUNNER_SELF_TEST:-0}" = "1" ]; then
  if SELF_TEST_STATUS="$("$NODE_BIN" --input-type=module -e "
import { sendHeartbeat } from 'file://$RUNNER_DIR_SYS/heartbeat.mjs';
sendHeartbeat(process.env)
  .then((r) => console.log(r.status ?? 'ok'))
  .catch((e) => { console.error(String(e.message ?? e)); process.exit(1); });
")"; then
    CHANGED+=("self-test:$SELF_TEST_STATUS")
  else
    echo "bootstrap: self-test 失败" >&2
    exit 1
  fi
fi

if [ "${AGENT_DRIFTED:-0}" = "1" ]; then
  echo "bootstrap: 提示：主机 heartbeat.mjs 与本次副本不同，未覆盖（设 SYNC_AGENT=1 同步）"
fi
if [ "${#CHANGED[@]}" -eq 0 ]; then
  echo "bootstrap: unchanged（幂等重入，无任何变更）"
else
  echo "bootstrap: changed: ${CHANGED[*]}"
fi
