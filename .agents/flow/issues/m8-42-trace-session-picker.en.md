# [m8-42] Move the trace session selector to a top dropdown and make outline sections collapsible

English | [中文](m8-42-trace-session-picker.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: ready
scope:
  - web/src/**
  - tests/**
  - .agents/flow/**
  - plans/**
adr: []
github:
  number: 294
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/294
```

## Background

The trace page's left rail currently stacks "key nodes → sessions → raw files": sessions render as an expanded tree that mixes the main Agent and subagents; with several subagents the list grows and pushes the key-node navigation down. The page header only shows the main Agent's sessionId, so after selecting a subagent the user cannot confirm that sub-session's native sessionId where sessions are switched, and has to inspect the right-hand details or raw files instead.

"Key nodes" is the primary navigation for reading a trace, but "raw files" is expanded by default and permanently occupies the rail, so a low-frequency action crowds out high-frequency navigation. Both sections should be collapsible, with key nodes visible by default and raw files collapsed by default.

## Goals

- Move "sessions" to the top of the left rail and turn it into a dropdown. Options cover the main Agent and every subagent, each showing its role (main Agent / subagent / unlinked), Agent name, native sessionId and span count.
- Default the dropdown to the main Agent root session. The selected session shows its native sessionId in the trigger. Keep the existing top attemptId / main-Agent-sessionId two-line identity semantics; it must not follow sub-session switches.
- Make "key nodes" collapsible and expanded by default. When collapsed, keep only the heading and node count; when expanded, preserve the existing jump and active-highlight behavior.
- Make "raw files" collapsible and collapsed by default. While collapsed, show the file count and main/subagent summary beside the heading; when expanded, keep per-file controlled downloads and the trace-archive entry.
- Update the console frontend directly (the interactive prototype page under `web/src`) with before/after screenshots. Do not change the backend API, trace data model or normalization semantics.

## Before and after

The screenshots come from the local console using the same synthetic trace (one main Agent, one subagent, one unlinked session, 18 spans) and the same route; Before is `1338f6a`, After is this branch, at 1440×900 in the light theme, with account details redacted.

**Before: the left rail is "key nodes → sessions → raw files"; sessions are a tree list and raw files are expanded by default.**

![Before: trace left rail](https://github.com/Shadow-Azure/ai-eval-platform/releases/download/m8-42-trace-session-picker-evidence-20261118/m8-42-before-trace-outline.png)

**After 1: sessions move to the top of the left rail as a dropdown, defaulting to the main Agent; key nodes stay expanded and raw files start collapsed.**

![After: default state](https://github.com/Shadow-Azure/ai-eval-platform/releases/download/m8-42-trace-session-picker-evidence-20261118/m8-42-after-trace-default.png)

**After 2: the dropdown lists the main Agent, subagents and unlinked sessions, each with Agent name, native sessionId and span count.**

![After: session dropdown](https://github.com/Shadow-Azure/ai-eval-platform/releases/download/m8-42-trace-session-picker-evidence-20261118/m8-42-after-trace-dropdown.png)

**After 3: selecting a subagent shows its native sessionId in the trigger and updates the center trace and right details; the top main-Agent sessionId stays unchanged.**

![After: subagent selected](https://github.com/Shadow-Azure/ai-eval-platform/releases/download/m8-42-trace-session-picker-evidence-20261118/m8-42-after-trace-subagent.png)

**After 4: key nodes collapsed and raw files expanded, confirming both sections toggle.**

![After: collapsed and expanded](https://github.com/Shadow-Azure/ai-eval-platform/releases/download/m8-42-trace-session-picker-evidence-20261118/m8-42-after-trace-toggles.png)

## Acceptance

- The left rail order is "session dropdown → key nodes → raw files". The dropdown defaults to the main Agent; switching to a subagent updates the center trace and the right details, and the trigger shows the selected session's sessionId.
- Dropdown options distinguish the main Agent, subagents and unlinked sessions, showing Agent name, sessionId and span count; unlinked sessions are never presented as the main Agent.
- Key nodes are expanded by default and collapse/expand from their heading; raw files are collapsed by default and expand from their heading. Both are keyboard operable and expose `aria-expanded`.
- Existing filters, search, deep links, timeline, detail inspector, raw-file downloads and trace-archive export do not regress; the `web` typecheck, build and `npm test` pass.
