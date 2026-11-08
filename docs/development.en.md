# Development Guide

English | [中文](development.md)

This repository currently depends only on Node.js for documentation and workflow gates. The initial project has no third-party npm dependencies, so no package installation step is required.

## Environment

- Node.js 22 or newer.
- Git for pairing blob hashes, workflow diffs, and commit hooks.
- Online workflow verification additionally needs an authenticated GitHub CLI and `FLOW_TOKEN`.

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

## Editing documentation

1. Correct facts on the Chinese base first, then make the minimal structurally mirrored update to the English side.
2. Run `npm run record:i18n -- <base.md>` to update the trio record.
3. Run `npm run lint:docs`, then run `npm run verify:docs` after it passes.
4. Add new documents to both the index and `scripts/doc-budgets.manifest.json`.

## Editing gates

Add a failing case under `tests/docs-gates.test.mjs` or another Node test before implementing the script change. Every new leaf must join a `run-gates` mode; documentation checks have no bypass only when CI always runs the full `verify:docs` mode.

## CI

`.github/workflows/docs-gates.yml` runs `npm test` and `npm run verify:docs` on main pushes and pull requests. `.github/workflows/flow-gates.yml` independently owns workflow state and GitHub two-sided consistency.
