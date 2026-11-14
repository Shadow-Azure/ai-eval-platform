#!/usr/bin/env bash
set -euo pipefail

expected_digest="${1:?managed env payload sha256 required}"
env_file="${ENV_FILE:-/opt/ai-eval-platform/.env}"
mkdir -p "$(dirname "$env_file")"
umask 077

expected_file="$(mktemp)"
payload_file="$(mktemp)"
output_file="$(mktemp "${env_file}.XXXXXX")"
cleanup() {
  rm -f "$expected_file" "$payload_file" "$output_file"
}
trap cleanup EXIT HUP INT TERM

long_lived_keys=(
  SECRET_BOX_KEY
  POSTGRES_PASSWORD
  AUTH_SESSION_SECRET
  GATEWAY_TOKEN_SECRET
  RUNNER_TASK_SECRET
  RUNNER_HEARTBEAT_TOKEN
  RUNNER_ADMIN_TOKEN
)

for name in "${long_lived_keys[@]}"; do
  if ! IFS= read -r line; then
    echo "managed env payload is incomplete before ${name}" >&2
    exit 1
  fi
  read -r provided_name provided_hash <<<"$line"
  if [ "$provided_name" != "$name" ] || [[ ! "$provided_hash" =~ ^[0-9a-f]{64}$ ]]; then
    echo "managed env expected-hash header is invalid for ${name}" >&2
    exit 1
  fi
  printf '%s %s\n' "$provided_name" "$provided_hash" >>"$expected_file"
done

if ! IFS= read -r delimiter || [ "$delimiter" != "--payload--" ]; then
  echo "managed env payload is missing its delimiter" >&2
  exit 1
fi

cat >"$payload_file"
actual_digest="$(sha256sum "$payload_file" | cut -d' ' -f1)"
if [ "$actual_digest" != "$expected_digest" ]; then
  echo "managed env payload checksum mismatch; installed .env was not changed" >&2
  exit 1
fi

present=0
missing=0
while read -r name expected_hash; do
  value="$(sed -n "s/^${name}=//p" "$env_file" 2>/dev/null | tail -1 || true)"
  if [ -z "$value" ]; then
    missing=$((missing + 1))
    continue
  fi
  present=$((present + 1))
  actual_hash="$(printf '%s' "$value" | sha256sum | cut -d' ' -f1)"
  if [ "$actual_hash" != "$expected_hash" ]; then
    echo "Existing host long-lived secret ${name} mismatch with GitHub Secrets; refusing to replace ${env_file}" >&2
    exit 1
  fi
done <"$expected_file"

if [ "$present" -gt 0 ] && [ "$missing" -gt 0 ]; then
  echo "Existing host .env is partially populated with long-lived secrets; refusing to replace ${env_file}" >&2
  exit 1
fi

if [ -f "$env_file" ]; then
  chmod 600 "$env_file"
  grep -Ev "^(S3_ENDPOINT|S3_BUCKET|S3_ACCESS_KEY|S3_SECRET_KEY|S3_REGION|S3_ADDRESSING_STYLE|GITHUB_TOKEN|DATABASE_STORAGE_LIMIT_BYTES|DATASET_IMAGE_REGISTRIES|SECRET_BOX_KEY|POSTGRES_PASSWORD|AUTH_SESSION_SECRET|GATEWAY_TOKEN_SECRET|RUNNER_TASK_SECRET|RUNNER_HEARTBEAT_TOKEN|RUNNER_ADMIN_TOKEN|RUNNER_EGRESS_PROXY|RUNNER_EGRESS_PROXY_BIND_ADDRESS|RUNNER_EGRESS_PROXY_PUBLIC_BIND_ADDRESS|API_BIND_ADDRESS|RUNNER_CALLBACK_BASE_URL|RUNNER_SPOT_RUNNER_TARBALL_URL|RUNNER_SCRIPTS_BUNDLE_NAME|RUNNER_SCRIPTS_BUNDLE_MD5|RUNNER_SCRIPTS_BUNDLE_SHA256|RUNNER_SCRIPTS_BUNDLE_SIZE)=" "$env_file" >"$output_file" || {
    status=$?
    [ "$status" -eq 1 ] || exit "$status"
  }
fi

cat "$payload_file" >>"$output_file"
chmod 600 "$output_file"
mv "$output_file" "$env_file"
trap - EXIT HUP INT TERM
