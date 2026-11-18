# [m8-42] Move the trace session selector to a top dropdown and make outline sections collapsible

English | [中文](m8-42-trace-session-picker.md)

```yaml flow
kind: issue
milestone: m8
priority: P2
status: ready
scope:
  - web/src/**
  - prototype/**
  - tests/**
  - .agents/flow/**
  - plans/**
  - reports/**
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
- Update both `prototype/trace-viewer-redesign/index.html` and the console frontend under `web/src`, then check the UI against the prototype. Do not change the backend API, trace data model or normalization semantics.

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

## Prototype before and after

Prototype file: `prototype/trace-viewer-redesign/index.html`, same prototype, 1440×900, light theme.

**Prototype before: the session card sits in the left rail; key nodes and raw files are both expanded.**

![Prototype before](https://github.com/Shadow-Azure/ai-eval-platform/releases/download/m8-42-trace-session-picker-evidence-20261118/m8-42-proto-before.png)

**Prototype after: sessions move to a top dropdown (default main Agent); key nodes stay expanded and raw files start collapsed.**

![Prototype after](https://github.com/Shadow-Azure/ai-eval-platform/releases/download/m8-42-trace-session-picker-evidence-20261118/m8-42-proto-after-default.png)

**Prototype after: the dropdown lists main Agent / subagents / unlinked, with sessionId and span count.**

![Prototype dropdown](https://github.com/Shadow-Azure/ai-eval-platform/releases/download/m8-42-trace-session-picker-evidence-20261118/m8-42-proto-after-dropdown.png)

**Prototype after: key nodes collapsed and raw files expanded.**

![Prototype toggles](https://github.com/Shadow-Azure/ai-eval-platform/releases/download/m8-42-trace-session-picker-evidence-20261118/m8-42-proto-after-toggles.png)

## Acceptance

- The left rail order is "session dropdown → key nodes → raw files". The dropdown defaults to the main Agent; switching to a subagent updates the center trace and the right details, and the trigger shows the selected session's sessionId.
- Dropdown options distinguish the main Agent, subagents and unlinked sessions, showing Agent name, sessionId and span count; unlinked sessions are never presented as the main Agent.
- Key nodes are expanded by default and collapse/expand from their heading; raw files are collapsed by default and expand from their heading. Both are keyboard operable and expose `aria-expanded`.
- Existing filters, search, deep links, timeline, detail inspector, raw-file downloads and trace-archive export do not regress; the `web` typecheck, build and `npm test` pass.
- The console left rail matches `prototype/trace-viewer-redesign` for the session dropdown and collapsible sections; `plans/m1-02-fullstack-design` §3.7/§5.3 now describe the current three-column structure.
