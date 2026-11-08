# Documentation Engineering

English | [中文](README.md)

This repository treats documentation as a verifiable software artifact: Node scripts mechanically check the index, budgets, structure, links, bilingual pairing, and terminology. The mechanism comes from StudyWiki's core docs-as-code pipeline and is scoped to the current initialization stage by leaving out application-specific gates.

## Document map

| Document | Purpose |
|---|---|
| [AGENTS.md](AGENTS.md) | Documentation writing standards and repository instructions |
| [architecture.md](architecture.en.md) | Current repository components, boundaries, and extension points |
| [design/eval-platform-architecture.md](design/eval-platform-architecture.en.md) | Finalized evaluation platform architecture: structure, flow, data model, deployment, and CI/CD |
| [development.md](development.en.md) | Local commands, documentation loop, and CI |
| [i18n/README.md](i18n/README.en.md) | Bilingual trio and structural mirror contract |
| [i18n/terminology.md](i18n/terminology.en.md) | Chinese-English terms and banned alternatives |
| [i18n/translation-prompt.md](i18n/translation-prompt.en.md) | Template for completing the English side |

Every new `docs/**/*.md` must be added to this table; `.en.md` files follow their base entry and are not listed separately.

## Documentation lifecycle

Choose the owning layer → update the Chinese base → make the minimal English counterpart change → re-record with `npm run record:i18n -- <base.md>` → run `npm run lint:docs` → run `npm run verify:docs` → let CI decide.

## Gate modes

| Command | Scope | Use |
|---|---|---|
| `npm run lint:docs` | Offline, sub-second leaves | Quick check before commit |
| `npm run verify:docs` | Quick mode + dead links + all self-tests | CI and full acceptance |
| `npm run route:gates` | Commands selected from changed paths | Local gate selection |

## Ownership boundaries

- `docs/` describes current facts and does not store roadmaps or decision debates.
- `.agents/flow/` is the single home for development workflow files.
- `.agents/skills/` contains reusable task instructions synchronized from StudyWiki and is outside bilingual pairing.
- `scripts/` contains gate implementations; changes there must include tests and update this page's command description.
