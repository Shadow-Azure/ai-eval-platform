# 接入官方 SWE-bench 评分、测试级打点与结果指标

[English](m3-04-swebench-scoring.en.md) | 中文

```yaml flow
kind: issue
milestone: m3
priority: P0
status: ready
scope:
  - .agents/flow/**
  - backend/**
  - tests/**
  - pyproject.toml
  - docs/**
  - plans/**
  - reports/**
  - docker-compose.yml
adr: []
github:
  number: 26
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/26
```

## 背景

真实评测必须使用官方 SWE-bench 评分口径，并保留每个测试的状态、分组和失败日志，不能只存一个 resolved 布尔值。当前没有评分产物、测试明细和指标聚合表。

## 目标

- 接入官方评分镜像或 harness，解析 report.json 与测试输出，保留 F2P、P2P 的测试级结果。
- 建立 score_artifact、test_case_result、metric_result 表与导入服务。
- 实现指标注册与白名单聚合器，覆盖 resolved_count、resolved_rate、resolved_rate_scored、execution_failure_rate 和成本、时长指标。
- 提供用例测试明细与评测结果 API，区分运行快照和最终结果。

## 验收

- 评分报告正例和负例可解析为 resolved、unresolved、not_scored 及测试级 outcome。
- resolved_rate 的分母是选中用例总数，错误、空补丁和未完成不从分母剔除。
- 终态结果不随指标配置变化，重算生成新版本并保留审计。
- 结果 API 返回指标版本、口径、分母、is_final 与生成时间。
