# 数据集绑定执行与评分镜像 digest

[English](m5-07-dataset-image-digests.en.md) | 中文

```yaml flow
kind: issue
milestone: m5
priority: P1
status: ready
scope:
  - .agents/flow/issues/m5-07-dataset-image-digests.*
  - backend/**
  - web/**
  - tests/**
  - docs/**
  - reports/**
  - plans/**
adr: []
github:
  number: 104
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/104
```

## 背景

发起评测向导当前要求每次填写执行镜像 digest 与评分镜像 digest；平台已有默认值，但数据集版本没有记录自己的镜像配置，选择数据集后仍要手工确认。

## 目标

- 数据集版本记录执行与评分镜像 digest，导入时可填写；留空时采用平台默认镜像配置。
- 数据集详情展示版本绑定的镜像 digest。
- 发起向导在选定数据集版本后自动带出镜像 digest，仍允许本次覆盖；服务端缺省时按数据集版本、平台默认的顺序解析。

## 验收

- 导入带或不带 digest 都能得到可复现的版本记录，缺省值与平台配置一致。
- 选定版本后向导无需手工填写即可通过预检；显式覆盖与完全缺失两条路径都有测试覆盖。
- 数据集详情可见版本绑定值，后端预检、创建与相关文档同步更新。
