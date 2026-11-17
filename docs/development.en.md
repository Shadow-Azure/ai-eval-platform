# Development Guide

English | [中文](development.md)

The standing gates depend on Node.js, while the control plane and backend tests depend on Python 3.12; install with `npm install` and `uv pip install --python .venv/bin/python -e ".[dev]"` respectively.

## Environment

- Node.js 22 or newer.
- Python 3.12 or newer for the control plane, migrations, and pytest.
- Git for pairing blob hashes, workflow diffs, and commit hooks.
- Online workflow verification additionally needs an authenticated GitHub CLI and `FLOW_TOKEN`.

## Identity configuration

Console login needs the session signing secret `AUTH_SESSION_SECRET`, which is required in production and generated once into the host `.env` by the deploy script; local development may omit it. The default administrator is created idempotently by `python -m app.seed` from `AUTH_BOOTSTRAP_ADMIN_EMAIL` and `AUTH_BOOTSTRAP_ADMIN_PASSWORD`, while production deployment runs `python -m app.manage create-admin --password-stdin` from GitHub Secrets. `python -m app.manage set-password --email <email> --password-stdin` resets a password while revoking that account's active sessions.

## Common commands

```sh
npm test
npm run lint:docs
npm run verify:docs
npm run route:gates
npm run verify:flow
npm run verify:flow-online
npm run record:i18n -- docs/README.md
```

`npm test` runs native Node tests; `lint:docs` is the offline quick mode; `verify:docs` is the full CI mode; `route:gates` recommends the smallest combination for the current worktree.

## Spot resource smoke

The real Aliyun smoke needs `ALIYUN_ACCESS_KEY_ID`, `ALIYUN_ACCESS_KEY_SECRET`, `RUNNER_ADMIN_TOKEN` (repository-admin PAT with `repo` scope), and `SPOT_SMOKE_CONFIRM=1`. Add the `run-spot-smoke` label to a PR to trigger the `spot-smoke` workflow; it creates one spot instance, runs the `runner-smoke` job, releases the instance, and uploads evidence as an artifact. Regular CI never creates cloud resources.

## Dataset image tarball publishing

A cold spot runner prefers the OSS-presigned image tarball delivered in the task and uses public mirrors only as a fallback. Publish from the fixed host (the compose stack is running and the image is already local); the script runs `docker save`, compresses with gzip, obtains a presigned PUT URL for the same key through `python -m app.manage dataset-image`, uploads, and verifies with a presigned HEAD.

```sh
bash scripts/publish-dataset-image.sh swebench/sweb.eval.x86_64.astropy_1776_astropy-12907:latest
```

The object key defaults to `dataset-images/<sha256 of the image reference>.tar.gz`; the Aliyun OSS internal endpoint is derived from `s3_endpoint`, and `S3_INTERNAL_ENDPOINT` overrides it for special deployments. OSS stays private and instances only receive short-lived URLs; unpublished images fall back to public sources automatically. Configure lifecycle and capacity monitoring for the actual image count, because the full SWE-bench image set reaches 1TB-scale storage.

## Editing documentation

1. Correct facts on the Chinese base first, then make the minimal structurally mirrored update to the English side.
2. Run `npm run record:i18n -- <base.md>` to update the trio record.
3. Run `npm run lint:docs`, then run `npm run verify:docs` after it passes.
4. Add new documents to both the index and `scripts/doc-budgets.manifest.json`.

## Editing gates

Add a failing case under `tests/docs-gates.test.mjs` or another Node test before implementing the script change. Every new leaf must join a `run-gates` mode; documentation checks have no bypass only when CI always runs the full `verify:docs` mode.

## CI

`.github/workflows/docs-gates.yml` runs `npm test` and `npm run verify:docs` on main pushes and pull requests. `.github/workflows/flow-gates.yml` independently owns workflow state and GitHub two-sided consistency.
