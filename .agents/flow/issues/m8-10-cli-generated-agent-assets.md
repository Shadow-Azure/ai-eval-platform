# [m8-10] 支持 CLI 生成型 Agent 资产的配置与加载测试

[English](m8-10-cli-generated-agent-assets.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-10-cli-generated-agent-assets.*
  - .agents/flow/issues/m8-09-collapsible-sidebar.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/roadmap.*
  - prototype/**
  - backend/app/api/agent_assets.py
  - backend/app/services/agent_assets.py
  - backend/app/models/agent_asset.py
  - backend/app/api/runners.py
  - backend/app/api/evaluations.py
  - backend/app/services/preflight.py
  - backend/app/services/preparations.py
  - backend/migrations/versions/0023_agent_asset_load_test.py
  - runner/scripts/real-task.mjs
  - runner/lib/runtime-claude-code.mjs
  - runner/lib/asset-bundle.mjs
  - web/src/pages/AssetsPage.tsx
  - web/src/pages/EvalWizardPage.tsx
  - web/src/api/agentAssets.ts
  - web/src/styles/assets.css
  - tests/backend/**
  - tests/runner/**
  - plans/**
adr: []
github:
  number: 202
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/202
```

## 背景

- 平台已支持以 `skills/` 为扫描根的静态资产源（如 Superpowers），但 OpenSpec/CodeSpec 这类仓库是 CLI 工具本身：agent 工作流由 `openspec init/update` 在运行时生成，仓库内没有 SKILL.md 或 commands。
- 实测生产扫描 `chenmr0/OpenSpec-cn`（CLI 源码）得到 0 个资产；上游 `Fission-AI/OpenSpec` 的 12 个 skill 可扫描，但声明 `compatibility: Requires openspec CLI` 与 `allowed-tools: Bash(openspec:*)`。
- 平台只在资产源与扫描结果中展示 `runtime_prerequisites`，Runner 不安装也不执行；执行镜像没有 `openspec`，沙箱网络受限，评测会停在 CLI 调用。
- 因此这类“仓库 + CLI 生成命令”的资产目前无法配置、验证和加载，需要在资产源层补齐运行时前置的配置与加载测试。

## 目标

- 资产源支持声明运行时前置：CLI 安装命令、初始化/生成命令（OpenSpec/CodeSpec 等），可选运行时类型与版本。
- 提供隔离环境里的加载测试：安装 CLI、执行 init/update、扫描生成的 skill/command，返回状态、生成清单与日志。
- 加载测试结果与前置命令冻结进资产快照，评测记录可追溯。
- 评测准备/启动校验：前置未配置或加载测试未通过时阻断或显著警告；通过后把生成资产与前置命令交给 Runner。
- Runner 在沙箱执行前置并注入生成资产；失败时有明确错误、日志与可重试路径。

## 验收

- 原型：资产源向导出现“运行时前置”配置与加载测试面板，资产卡片展示状态与结果（先评审原型）。
- 后端：资产源保存/扫描/加载测试状态机与快照字段；prepare 在未通过时返回可读阻断。
- Runner：隔离执行安装/init 并注入生成资产；失败可见日志与错误码。
- 前端：资产源、评测向导与评测配置的展示与校验完整；未配置时保持现有行为。
- 测试：后端状态机/快照/prepare 校验、Runner 前置执行与注入、Web 构建；生产用 OpenSpec 仓库复测。
