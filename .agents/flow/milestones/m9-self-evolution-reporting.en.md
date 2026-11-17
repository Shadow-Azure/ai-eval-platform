# M9 Self-Evolution Reporting Loop

English | [中文](m9-self-evolution-reporting.md)

Integrate with the harness-evolution self-evolution platform: evaluations can configure bad case reporting at launch (base URL, workspace, and ingest credential); after a run finishes, non-resolved cases are reported by final SWE-bench outcome to the target workspace (with per-report sessionSource); the platform also serves a signed session trajectory download endpoint, forming a bidirectional "evaluation produces evidence, self-evolution platform consumes evidence" loop.

```yaml flow
kind: milestone
id: m9
title: 自进化上报闭环
status: planned
github:
  number: 10
  url: https://github.com/Shadow-Azure/ai-eval-platform/milestone/10
```
