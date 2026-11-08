# 注册数据集并导入 SWE-bench 切片与任务包

[English](m3-01-swebench-dataset-import.en.md) | 中文

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
  number: 23
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/23
```

## 背景

M3 要在真实用例上评测，必须先把数据集导入为不可变版本，并生成区分 Agent 可见与评分专用的任务包。当前只有评测运行表，没有数据集、版本、用例、指标定义和导入流程。

## 目标

- 建立 dataset、dataset_version、dataset_case、dataset_import、metric_definition 与版本指标关联表及迁移。
- 实现 DatasetAdapter 注册表与 SWE-bench 适配器，覆盖来源校验、流式导入、字段映射与指标定义。
- 生成五文件任务包与 manifest 并写入对象存储，区分 Agent 可见文件和评分专用文件。
- 提供数据集、版本、用例、指标与导入进度 API，导入 worker 可重入且支持部分失败。

## 验收

- SWE-bench Verified 切片可导入为一个版本，用例数与来源一致，重复导入不产生重复版本。
- 非法来源和部分失败有明确错误报告，合法用例仍可入库。
- 任务包 manifest 记录每个文件的 digest 和可见性，Agent 包不包含参考补丁与测试补丁。
- 用例分页、筛选和指标定义查询有自动化测试覆盖。
