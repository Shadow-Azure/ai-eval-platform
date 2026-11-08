# Development Flow Gates

English | [中文](README.md)

> This directory defines the roadmap → milestone → issue contract. No roadmap, milestone, or issue content is created yet; the gates activate after planning.

## Layers

| Layer | File | Responsibility |
|---|---|---|
| roadmap | `roadmap.md` | Single source of truth for the ordered milestone sequence |
| milestone | `milestones/<id>-*.md` | Stage objective and acceptance criteria |
| issue | `issues/<milestone-id>-*.md` | Smallest work unit, priority, and allowed change scope |

## Machine-readable fence

Each work document contains exactly one `yaml flow` fence. Bilingual pairs keep that fence byte-for-byte identical.

The roadmap contains `kind: roadmap` and `milestones`. A milestone contains `kind: milestone`, `id`, `title`, `status`, and `github: {number, url}`. An issue contains `kind: issue`, `milestone`, `priority`, `status`, `scope`, `adr`, and `github: {number, url}`. One bootstrap issue in the first milestone may use `bootstrap: true` until its GitHub number is assigned.

## Closed sets

- issue status: `backlog` / `ready` / `in-progress` / `done`
- milestone status: `planned` / `active` / `done`
- priority: `P0` / `P1` / `P2`

## Progression

- The local issue number matches its GitHub issue number; commits and PR titles must contain `(#N)`.
- An issue with `github.number: null` must remain in `backlog`.
- Issues move serially across milestones at the same priority and may run in parallel within the same milestone and priority.
- A milestone is `done` only when all of its issues are `done`.

## Gates

| Check | Where | What it blocks |
|---|---|---|
| `npm test` | local + CI | Regressions in the gate implementation |
| `npm run verify:flow` | local + CI | File contract, state machine, priority, scope, and ADR links |
| `verify-flow --diff origin/main` | PR CI | Missing issue references, invalid issue state, or out-of-scope changes |
| `npm run verify:flow-online` | PR CI | GitHub issue/milestone drift plus missing PR milestone or Project links |
| `commit-msg` | local | Commit subjects without `(#N)` |

Until `roadmap.md` exists, CI reports the gates as pending and exits successfully. The first roadmap merge activates them without another workflow change.

Project checking is mandatory and needs a `FLOW_TOKEN` repository secret with user-level Projects v2 read access. Local online checks require an authenticated GitHub CLI.

## Tooling

```sh
npm run install:hooks
npm run flow:new-issue -- --milestone <id> --priority P0 --slug <kebab> \
  --title <Chinese title> --title-en "<English title>" --scope "<glob,glob>"
npm run flow:sync
```
