# [m8-07] Append custom instructions to evaluation prompts

English | [中文](m8-07-eval-prompt-suffix.md)

```yaml flow
kind: issue
milestone: m8
priority: P1
status: done
scope:
  - .agents/flow/issues/m8-07-eval-prompt-suffix.*
  - .agents/flow/issues/m8-04-trace-viewer-redesign.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/roadmap.*
  - prototype/**
  - backend/app/api/evaluations.py
  - backend/app/services/preparations.py
  - backend/app/services/evaluations.py
  - backend/app/api/runners.py
  - backend/app/models/evaluation.py
  - backend/migrations/versions/0022_eval_run_prompt_suffix.py
  - runner/scripts/real-task.mjs
  - runner/lib/runtime-claude-code.mjs
  - runner/lib/prompt.mjs
  - web/src/pages/EvalWizardPage.tsx
  - web/src/pages/EvalDetailPage.tsx
  - web/src/api/evaluations.ts
  - tests/backend/**
  - tests/runner/**
  - plans/**
adr: []
github:
  number: 193
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/193
```

## Background

An evaluation prompt is fixed to the dataset case's problem_statement: the runner forwards it verbatim in the task payload (backend/app/api/runners.py:938) and launches `claude -p <prompt>` (runner/lib/runtime-claude-code.mjs buildClaudeCommand). There is no per-evaluation prompt or system-prompt append, and the model config only stores provider, endpoint and model name with no system-prompt field (backend/app/models/model.py:54-72).

The requirement is to append custom instructions after the dataset input at launch time, for example: "This is a non-interactive evaluation. Use the superpowers skills to complete the task; do not ask the user questions, decide autonomously and continue." That is not possible today; editing the dataset problem_statement (requires a new version, is per case and immutable) or adding an instruction-only skill (the model may not invoke it and cannot be forced not to ask) are only workarounds.

## Goals

- Optionally append custom instructions (prompt suffix) at launch, after each case prompt or as a system-prompt append.
- Freeze the instructions into the preparation and eval_run, include them in consistency checks, and expose them in the evaluation config and trace.
- Let the frontend wizard collect them, show them on the review step and in the evaluation config, and keep today's behavior when empty.
- Deliver a prototype with before/after screenshots for review first, and only then start frontend and backend development.

## Design

- Field: add optional `prompt_suffix` to prepare/create (max 4000 characters, default empty); an empty value keeps today's behavior.
- Freeze: write it into the preparation snapshot and eval_run, include it in the consistency identity (a change raises configuration_changed).
- Delivery: carry `prompt_suffix` in the runner task payload; real-task appends `problem_statement + "\n\n## Additional instructions\n" + suffix`, or uses Claude Code's supported `--append-system-prompt` (a stronger system-level constraint). Support both, default to the system prompt and keep the optional user-prompt append.
- Frontend: a "Additional instructions (optional)" textarea with an example on wizard step 3, shown on step 4 and in the summary as "configured · N characters".
- Wording: state "non-interactive, do not ask the user questions, use the superpowers skills as needed". Claude Code already runs non-interactively with `-p`, but the model may still ask in its output, so the instruction must constrain it.

## Prototype

Before/after screenshots for steps 3 and 4 are embedded in the issue body and the release assets; the prototype is a local interactive HTML preview, reviewed for design only, with no production frontend change.

## Acceptance

- Prototype review approves the field placement, wording and the "empty keeps current behavior" default.
- After implementation: prepare/create freezes and shows prompt_suffix; the runner append or system-prompt append takes effect; the trace shows the appended text; empty values behave exactly like today.
- Relevant backend, runner and web tests pass.
