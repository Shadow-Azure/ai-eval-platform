# M11 Machine triggers and candidate injection for self-evolution

English | [中文](m11-self-evolution-trigger.md)

Extend the three stable execution APIs of evaluation templates from console-session semantics to machine triggers: add a service-token identity, and let template launches inject the asset configuration of a chosen repository and candidate commit through optional parameters (the commit is the authoritative identity with a resolution assertion); the platform only provides measurements and identity echo, while verdicts and promotion stay with harness-evolution, and the candidate-evaluation template keeps self-evolution reporting disabled.

```yaml flow
kind: milestone
id: m11
title: 自进化机器触发与候选注入
status: planned
github:
  number: 12
  url: https://github.com/Shadow-Azure/ai-eval-platform/milestone/12
```
