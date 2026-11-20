#!/bin/bash
# Build an Aliyun custom image from a prepared cold spot runner host.
#
# Usage:
#   ALIYUN_ACCESS_KEY_ID=... ALIYUN_ACCESS_KEY_SECRET=... \
#     ./infra/runner-image/build.sh \
#       --instance-id i-bp1... --region cn-hangzhou --version 2026-11-21
#
# The instance is a throwaway Ubuntu builder created by the operator from the
# latest official Ubuntu 22.04/24.04 image. This script runs prepare.sh through
# Cloud Assistant, reboots once when a package upgrade asks for it, verifies
# the host is clean, stops the instance, creates the image, and deletes the
# builder. It never touches a production pool: updating `instance_config.image_id`
# and rolling back stay explicit operator actions documented in README.md.
#
# Use --dry-run to print the cloud calls without changing anything.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PREPARE="$ROOT/infra/runner-image/prepare.sh"
REGION="${ALIYUN_REGION:-cn-hangzhou}"
INSTANCE_ID=""
VERSION=""
IMAGE_NAME=""
KEEP_INSTANCE=0
DRY_RUN=0
MAX_PREPARE_REBOOTS=2

usage() {
  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --instance-id) INSTANCE_ID="${2:-}"; shift 2 ;;
    --region) REGION="${2:-}"; shift 2 ;;
    --version) VERSION="${2:-}"; shift 2 ;;
    --image-name) IMAGE_NAME="${2:-}"; shift 2 ;;
    --keep-instance) KEEP_INSTANCE=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage 0 ;;
    *) echo "build.sh: unknown argument: $1" >&2; usage 1 ;;
  esac
done

if [ -z "$INSTANCE_ID" ] || [ -z "$VERSION" ]; then
  echo "build.sh: --instance-id and --version are required" >&2
  usage 1
fi
if [ ! -f "$PREPARE" ]; then
  echo "build.sh: missing $PREPARE" >&2
  exit 1
fi
IMAGE_NAME="${IMAGE_NAME:-ai-eval-runner-${VERSION//[^a-zA-Z0-9-]/-}}"
RECORD_DIR="$ROOT/infra/runner-image/versions"
RECORD="$RECORD_DIR/${VERSION}.json"

run() {
  if [ "$DRY_RUN" -eq 1 ]; then
    printf 'dry-run: %s\n' "$*"
    return 0
  fi
  "$@"
}

aliyun_json() {
  # aliyun prints one JSON document; keep stdout clean for jq.
  aliyun ecs "$@"
}

if [ "$DRY_RUN" -eq 0 ]; then
  command -v aliyun >/dev/null 2>&1 || { echo "build.sh: aliyun CLI is required" >&2; exit 1; }
  command -v jq >/dev/null 2>&1 || { echo "build.sh: jq is required" >&2; exit 1; }
  command -v base64 >/dev/null 2>&1 || { echo "build.sh: base64 is required" >&2; exit 1; }
fi

instance_status() {
  aliyun_json DescribeInstances --RegionId "$REGION" \
    --InstanceIds "[\"$INSTANCE_ID\"]" \
    | jq -r '.Instances.Instance[0].Status // empty'
}

wait_instance() {
  local wanted="$1" deadline=$(( $(date +%s) + 1200 ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if [ "$(instance_status)" = "$wanted" ]; then
      return 0
    fi
    sleep 10
  done
  echo "build.sh: instance $INSTANCE_ID did not reach $wanted within 20 minutes" >&2
  return 1
}

wait_invocation() {
  local invoke_id="$1" deadline=$(( $(date +%s) + 2700 )) result status exit_code
  while [ "$(date +%s)" -lt "$deadline" ]; do
    result="$(aliyun_json DescribeInvocationResults --RegionId "$REGION" \
      --InvokeId "$invoke_id" --InstanceId "$INSTANCE_ID")"
    status="$(printf '%s' "$result" | jq -r '.Invocation.InvocationResults.InvocationResult[0].InvocationStatus // empty')"
    exit_code="$(printf '%s' "$result" | jq -r '.Invocation.InvocationResults.InvocationResult[0].ExitCode // 0')"
    if [ "$status" = "Success" ] || [ "$status" = "Failed" ]; then
      printf '%s\n' "$result" | jq -r '.Invocation.InvocationResults.InvocationResult[0].Output // ""' >&2 || true
      RUNNER_IMAGE_PREPARE_EXIT="$exit_code"
      return 0
    fi
    sleep 15
  done
  echo "build.sh: prepare command $invoke_id did not finish within 45 minutes" >&2
  return 1
}

run_prepare() {
  local content invoke_id
  content="$(base64 < "$PREPARE" | tr -d '\n')"
  invoke_id="$(aliyun_json RunCommand --RegionId "$REGION" \
    --InstanceId.1 "$INSTANCE_ID" \
    --Type RunShellScript \
    --ContentEncoding Base64 \
    --CommandContent "$content" \
    --Name "eval-runner-image-prepare-${VERSION}" \
    --Timeout 1800 \
    | jq -r '.InvokeId // empty')"
  if [ -z "$invoke_id" ]; then
    echo "build.sh: RunCommand returned no InvokeId" >&2
    return 1
  fi
  echo "build.sh: prepare command $invoke_id started"
  wait_invocation "$invoke_id"
}

# --- prepare (with at most one controlled reboot) --------------------------
if [ "$DRY_RUN" -eq 1 ]; then
  echo "dry-run: RunCommand with base64($PREPARE) on $INSTANCE_ID"
  echo "dry-run: on exit 2 -> RebootInstance and rerun prepare"
else
  attempt=1
  while :; do
    run_prepare
    if [ "${RUNNER_IMAGE_PREPARE_EXIT:-1}" = "0" ]; then
      break
    fi
    if [ "${RUNNER_IMAGE_PREPARE_EXIT:-1}" != "2" ] || [ "$attempt" -ge "$MAX_PREPARE_REBOOTS" ]; then
      echo "build.sh: prepare.sh failed with exit ${RUNNER_IMAGE_PREPARE_EXIT:-unknown}" >&2
      exit 1
    fi
    echo "build.sh: prepare requested a reboot; rebooting the builder (attempt $attempt)"
    aliyun_json RebootInstance --InstanceId "$INSTANCE_ID" >/dev/null
    sleep 20
    wait_instance Running
    attempt=$((attempt + 1))
  done
fi

# The image must not carry a pending reboot, or every derived spot instance
# would refuse the first job.
if [ "$DRY_RUN" -eq 1 ]; then
  echo "dry-run: verify /var/run/reboot-required is absent, timers masked, marker present"
else
  verify_id="$(aliyun_json RunCommand --RegionId "$REGION" \
    --InstanceId.1 "$INSTANCE_ID" --Type RunShellScript --ContentEncoding Base64 \
    --CommandContent "$(printf '%s' 'set -e; test -f /etc/eval/runner-image-baked; test ! -f /var/run/reboot-required; test "$(systemctl is-enabled apt-daily.timer apt-daily-upgrade.timer unattended-upgrades.service ua-timer.timer | grep -c "^masked$")" -eq 4' | base64 | tr -d '\n')" \
    --Name "eval-runner-image-verify-${VERSION}" --Timeout 600 \
    | jq -r '.InvokeId // empty')"
  wait_invocation "$verify_id"
  if [ "${RUNNER_IMAGE_PREPARE_EXIT:-1}" != "0" ]; then
    echo "build.sh: image verification failed" >&2
    exit 1
  fi
fi

# --- snapshot --------------------------------------------------------------
run aliyun_json StopInstance --RegionId "$REGION" --InstanceId "$INSTANCE_ID" --ForceStop false >/dev/null
if [ "$DRY_RUN" -eq 0 ]; then
  wait_instance Stopped
fi
IMAGE_ID="$(run aliyun_json CreateImage --RegionId "$REGION" --InstanceId "$INSTANCE_ID" \
  --ImageName "$IMAGE_NAME" \
  --Description "AI eval cold spot runner ${VERSION}; prepared by infra/runner-image/prepare.sh" \
  --Tag.1.Key "eval-runner-image-version" --Tag.1.Value "$VERSION" \
  | jq -r '.ImageId // empty' 2>/dev/null || true)"
if [ "$DRY_RUN" -eq 0 ] && [ -z "$IMAGE_ID" ]; then
  echo "build.sh: CreateImage returned no ImageId" >&2
  exit 1
fi
if [ "$DRY_RUN" -eq 1 ]; then
  IMAGE_ID="i-dryrun"
else
  deadline=$(( $(date +%s) + 3600 ))
  status=""
  while [ "$(date +%s)" -lt "$deadline" ]; do
    status="$(aliyun_json DescribeImages --RegionId "$REGION" --ImageOwnerAlias self \
      --ImageName "$IMAGE_NAME" \
      | jq -r '.Images.Image[0].Status // empty')"
    [ "$status" = "Available" ] && break
    sleep 15
  done
  if [ "$status" != "Available" ]; then
    echo "build.sh: image $IMAGE_NAME did not become Available within 60 minutes" >&2
    exit 1
  fi
fi

if [ "$KEEP_INSTANCE" -eq 1 ] || [ "$DRY_RUN" -eq 1 ]; then
  echo "build.sh: keeping builder $INSTANCE_ID"
else
  aliyun_json DeleteInstance --RegionId "$REGION" --InstanceId "$INSTANCE_ID" --Force true >/dev/null
fi

if [ "$DRY_RUN" -eq 0 ]; then
  mkdir -p "$RECORD_DIR"
  cat > "$RECORD" <<RECORD_JSON
{
  "version": "${VERSION}",
  "image_id": "${IMAGE_ID}",
  "image_name": "${IMAGE_NAME}",
  "region": "${REGION}",
  "builder_instance_id": "${INSTANCE_ID}",
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "source_commit": "$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo unknown)",
  "rollback": "set the pool instance_config.image_id back to the previous record"
}
RECORD_JSON
  echo "build.sh: image ready: ${IMAGE_ID}"
  echo "build.sh: record: ${RECORD}"
else
  echo "dry-run: image ${IMAGE_NAME} from ${INSTANCE_ID}"
fi
