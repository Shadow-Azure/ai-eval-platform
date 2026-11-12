# 评测/会话轨迹：主 Agent 与子 Agent 轨迹打包下载

[English](m8-05-trace-export.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-05-trace-export.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/roadmap.*
  - scripts/doc-budgets.manifest.json
  - backend/app/api/traces.py
  - backend/app/services/trace_export.py
  - backend/app/services/trace_queries.py
  - tests/backend/test_trace_export_api.py
  - tests/trace-export-model.test.mjs
  - web/src/api/traces.ts
  - web/src/features/traces/traceExportModel.ts
  - web/src/pages/TracePage.tsx
  - web/src/styles/trace-viewer.css
  - prototype/**
  - plans/**
adr: []
github:
  number: 186
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/186
```

## 背景

轨迹页已能按 attempt 展示主 Agent 与全部子 Agent 的会话树，但原始轨迹只能逐个下载：顶栏「原始会话文件」实际只取 `files[0]`，Raw source 需要逐文件点击，且没有清单说明是否覆盖全部子 Agent。需要一次导出当前 attempt 的完整轨迹包。

## 目标

- attempt 级导出：把当前 attempt 的主 Agent 与全部子 Agent 原始文件打成一个 ZIP。
- ZIP 按 `main/` 与 `subagents/<agent>/` 归档，并附 `manifest.json` 记录 case、attempt、session、文件 sha256 与缺失项。
- 轨迹页提供「下载轨迹包」入口与包含范围、目录结构、权限说明的弹窗；仅管理员可用并写审计。
- 个别原生对象缺失时跳过该文件并在清单标记，不生成坏包，也不阻断其余轨迹。

## 验收

- 管理员一次点击得到 ZIP，主 Agent 与全部子 Agent 文件、manifest 齐全，数量与轨迹页一致。
- 只读账号收到 403、未登录收到 401；导出写审计，响应与清单不泄露 object key 或密钥。
- 原生对象缺失时仍可导出且清单标记 missing；没有可导出文件时返回可读错误。
- 后端测试、`npm test`、`web` 类型检查与构建通过；PR 附下载入口与 ZIP 内容验证证据。
