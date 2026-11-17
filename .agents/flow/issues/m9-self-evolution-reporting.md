# 自进化上报：评测启动配置、bad case 上报与 session 轨迹包下载

[English](m9-self-evolution-reporting.en.md) | 中文

```yaml flow
kind: issue
milestone: m9
priority: P0
status: in-progress
scope:
  - .gitignore
  - .github/**
  - backend/app/**
  - backend/ai_eval_platform.egg-info/**
  - backend/migrations/**
  - deploy/**
  - scripts/**
  - web/src/**
  - prototype/**
  - tests/**
  - docs/**
  - plans/**
  - .agents/flow/**
adr: []
github:
  number: 263
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/263
```

## 背景

harness-evolution（自进化平台）已合入 per-report session source 机制（Shadow-Azure/harness-evolution#286，issue #282）：bad case 上报契约 `POST /api/workspaces/{wid}/bad-cases` 新增可选 `sessionSource` 字段（`runtime` + `sessionUrlTemplate`），轨迹回链改为按上报自带的 URL 模板调用单接口 envelope（一次返回 runtime 身份、一层 childSessionIds 与规范化 NDJSON 轨迹），授权由模板自带短时签名承担，不收凭据字段。

评测平台侧缺少对应的发送端：评测启动时无法配置自进化上报目标，执行结束后未通过用例不会上报，平台也没有可供 harness-evolution 回链的 session 下载接口。方案讨论见 `plans/2026-11-16-swebench-badcase-report-proposal.html`。

## 目标

- 发起评测向导新增"自进化上报（可选）"配置：自进化平台基础 URL、工作区 ID、上报令牌（whk_ 前缀）；令牌按凭据掩码处理，回显不落明文。
- 评测运行终态后，把最终 SWE-bench 执行结果中非 `resolved` 的用例（`unresolved` / `execution_failed`，不含 `not_scored`）作为 bad case 逐条上报到目标工作区；字段按 harness-evolution `BadCaseIngest` 契约映射，`badCaseId` 确定性生成保证重放幂等（重复上报 200 覆盖，不占配额）。
- 每条上报携带 per-report `sessionSource`：`runtime` 取评测 agent 运行时键，`sessionUrlTemplate` 指向本平台的 session envelope 下载端点并自带短时签名。
- 平台新增 session 轨迹包下载端点：按 sessionId 返回 envelope JSON（sessionId 回显、runtime、一层 childSessionIds、NDJSON trajectory），签名校验失败稳定 401；单会话 64 MiB / 单行 1 MiB / 100 万行上限对齐 harness-evolution 拉取侧。
- 上报失败可观测、可重试（阶梯退避），不阻塞评测主流程；409 配额耗尽视为终态失败并告警。
- 原型与前端向导同步更新（Step 3 可选段 + Step 4 摘要行），修改前后截图回贴本 issue。

## 验收

- 后端：配置了自进化上报的评测 run 终态后，非 resolved 用例生成确定性 `badCaseId` 并成功 POST 到目标工作区（含 `sessionSource`）；重复触发不产生重复 bad case。
- 后端：session 下载端点对有效签名返回 envelope（含主/子会话与 NDJSON 轨迹），对无效/过期签名返回 401，对不存在会话返回 404。
- 前端：发起评测向导可配置/留空自进化上报；Step 4 确认页与摘要侧栏展示上报目标摘要；令牌输入掩码、不回显明文。
- 测试：上报映射、幂等、退避、签名校验、envelope 组装（含子会话一层展开与上限防御）均有单元测试；CI 全绿。
- 文档：`plans/m1-02-fullstack-design/` 相关章节与 `docs/` 现状文档同步；本 issue 附原型修改前后截图。
