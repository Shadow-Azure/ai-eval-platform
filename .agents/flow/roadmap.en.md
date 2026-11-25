# Roadmap

English | [中文](roadmap.md)

The evaluation platform first establishes a deployable, callback-capable vertical execution foundation, then adds real evaluation, concurrent provisioning, product configuration, trace diagnosis, and extensibility; milestones proceed in dependency order and each one delivers independently verifiable system capability; M8 focuses on fixing issues exposed in real use without expanding feature scope; M9 connects the bidirectional link with the harness-evolution platform: evaluations can configure bad case reporting at launch, report by final SWE-bench outcome after execution, and serve a signed session trajectory download endpoint; M10 turns frequently used evaluation combinations into reusable templates with stable APIs for triggering, status, and results; M11 opens the three stable APIs to machine triggers: a service-token identity and optional injection of a candidate commit's asset configuration at launch (the commit is authoritative with an assertion), with measurements and identity echo owned by the platform and verdicts and promotion owned by harness-evolution.

```yaml flow
kind: roadmap
milestones:
  - m1
  - m2
  - m3
  - m4
  - m5
  - m6
  - m7
  - m8
  - m9
  - m10
  - m11
```
