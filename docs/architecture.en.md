# Architecture Map

English | [中文](architecture.md)

The repository is in the initialization stage of an AI evaluation platform. The current structure covers engineering collaboration, documentation consistency, and reusable workflows only; runtime architecture, data models, and external interfaces for evaluation jobs are not yet established.

## Components

| Area | Responsibility | Boundary |
|---|---|---|
| `docs/` | Current facts, development method, and bilingual contract | No roadmaps or decision debates |
| `.agents/flow/` | roadmap, milestone, issue state machine | No planning files yet |
| `.agents/skills/` | Task skills and templates synchronized from StudyWiki | Outside bilingual pairing |
| `scripts/` | Mechanical documentation and workflow gates | Not product runtime |
| `.github/workflows/` | CI decisions for docs and flow | No external publishing |

## Documentation pipeline

```text
Markdown base + English pair + pairing record
                 |
                 v
index -> budgets -> wrap -> pairing -> terminology
                 |
                 v
             warnings on local
                 |
                 v
      links + self-tests in CI
```

The Chinese file is `foo.md`, the English side is `foo.en.md`, and the record is `foo.i18n.yaml`. The record stores both blob hashes from the last confirmed state, so drift on either side fails the pairing gate.

## Extension points

- When the product runtime and evaluation executor exist, add their components, data flows, and external dependencies here instead of debating design in README.
- When development tasks begin, create the roadmap, milestones, and issues under `.agents/flow/`; the existing workflow gates activate automatically.
- Add platform-specific checks as independent leaves under `scripts/`, combine them in `run-gates`, and wire them explicitly into CI.
- If documentation volume grows, reorganize layers and budgets before considering a site projection; this repository currently has no VitePress or other generated site.
