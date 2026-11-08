import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..", "..");
const read = (path) => readFileSync(join(root, path), "utf8");

test("deploy workflow builds immutable images and deploys over SSH", () => {
  const workflow = read(".github/workflows/deploy.yml");
  for (const expected of [
    "workflow_dispatch",
    "docker/build-push-action@v6",
    "docker/login-action@v3",
    "ACR_REGISTRY",
    "api@${{ needs.build.outputs.api_digest }}",
    "web@${{ needs.build.outputs.web_digest }}",
    "bash deploy/deploy.sh",
    "rm -rf /opt/ai-eval-platform/deploy",
    "readyz",
    "environment: production",
  ]) {
    assert.ok(workflow.includes(expected), `deploy workflow missing ${expected}`);
  }
  assert.equal(workflow.includes("set -x"), false);
});

test("backend CI runs pytest against PostgreSQL and an empty-database migration", () => {
  const workflow = read(".github/workflows/backend-ci.yml");
  for (const expected of [
    "postgres:16",
    "TEST_DATABASE_URL",
    "pytest",
    "CREATE DATABASE eval_migrate",
    "alembic upgrade head",
    "npm run build",
    "node --test tests/deploy/*.test.mjs",
  ]) {
    assert.ok(workflow.includes(expected), `backend CI missing ${expected}`);
  }
});

test("deploy script migrates before updating services and records the outcome", () => {
  const script = read("deploy/deploy.sh");
  assert.ok(script.includes("--profile tools run --rm migrate"));
  assert.ok(script.includes("up -d --remove-orphans"));
  assert.ok(script.includes("trap on_error ERR"));
  assert.ok(script.indexOf("--profile tools run --rm migrate") < script.indexOf("up -d --remove-orphans"));
  assert.ok(script.includes("record_deployment succeeded"));
  assert.ok(script.includes("rollback.sh"));
  assert.equal(script.includes("set -x"), false);
});

test("rollback restores images without a database downgrade", () => {
  const script = read("deploy/rollback.sh");
  assert.ok(script.includes(".state/previous.env"));
  assert.ok(script.includes("up -d --remove-orphans"));
  assert.equal(script.includes("alembic downgrade"), false);
  assert.equal(script.includes("record_deployment rolled_back"), true);
});

test("production compose swaps object storage for OSS and keeps health checks", () => {
  const compose = read("deploy/docker-compose.prod.yml");
  for (const expected of [
    "image: ${API_IMAGE",
    "image: ${WEB_IMAGE",
    "postgres:",
    "redis:",
    "worker:",
    "web:",
    "migrate:",
    "depends_on:",
    "healthcheck:",
    "migrations/migrate_with_lock.py",
  ]) {
    assert.ok(compose.includes(expected), `prod compose missing ${expected}`);
  }
  assert.equal(compose.includes("minio"), false);
});

test("ECS deploy derives the ACR VPC endpoint for fast pulls", () => {
  const common = read("deploy/common.sh");
  assert.ok(common.includes("normalize_images"));
  assert.ok(common.includes("ACR_REGISTRY_VPC"));
  assert.ok(common.includes("docker login --username"));
});

test("deployment scripts contain no obvious secret logging", () => {
  for (const path of ["deploy/deploy.sh", "deploy/rollback.sh", "deploy/common.sh", "deploy/record-deployment.sh"]) {
    const text = read(path);
    assert.equal(text.includes("set -x"), false, `${path} enables xtrace`);
    for (const forbidden of ["echo $ACR_PASSWORD", "echo $ECS_SSH_KEY", "echo $ALIYUN_ACCESS_KEY_SECRET"]) {
      assert.equal(text.includes(forbidden), false, `${path} logs ${forbidden}`);
    }
  }
});

test("deployment record writes status, image digest and migration revision", () => {
  const script = read("backend/migrations/record_deployment.py");
  for (const expected of [
    "DEPLOY_STATUS",
    "DEPLOY_IMAGE_DIGEST",
    "DEPLOY_MIGRATION_REVISION",
    "INSERT INTO deployment",
    "detail_json",
  ]) {
    assert.ok(script.includes(expected), `record script missing ${expected}`);
  }
});
