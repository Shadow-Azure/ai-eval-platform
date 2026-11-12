# [m8-07] 评测支持追加自定义指令（Prompt 追加）

[English](m8-07-eval-prompt-suffix.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P1
status: in-progress
scope:
  - .agents/flow/issues/m8-07-eval-prompt-suffix.*
  - .agents/flow/issues/m8-04-trace-viewer-redesign.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/roadmap.*
  - prototype/**
  - backend/app/api/evaluations.py
  - backend/app/services/preparations.py
  - backend/app/services/evaluations.py
  - backend/app/api/runners.py
  - backend/app/models/evaluation.py
  - backend/migrations/versions/0022_eval_run_prompt_suffix.py
  - runner/scripts/real-task.mjs
  - runner/lib/runtime-claude-code.mjs
  - runner/lib/prompt.mjs
  - web/src/pages/EvalWizardPage.tsx
  - web/src/pages/EvalDetailPage.tsx
  - web/src/api/evaluations.ts
  - tests/backend/**
  - tests/runner/**
  - plans/**
adr: []
github:
  number: 193
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/193
```

## 背景

当前评测的题面固定取数据集 case 的 problem_statement：Runner 在任务包里原样下发（backend/app/api/runners.py:938），再用 `claude -p <prompt>` 启动（runner/lib/runtime-claude-code.mjs buildClaudeCommand），没有按评测追加 prompt 或系统提示的能力；模型配置只有服务商、端点与模型名，也没有 system prompt 字段（backend/app/models/model.py:54-72）。

实际诉求是：启动评测时追加一段自定义指令到数据集输入之后，例如「这是非交互评测。请使用 superpowers 技能完成任务；不要向用户提问，自主决策并继续」。当前不能直接做到；改数据集 problem_statement（需要新版本、按 case、冻结后不可改）或增加一个指令型 skill（模型不保证调用，也不能保证不提问）都只是绕路。

## 目标

- 评测启动时可选追加一段自定义指令（prompt suffix），追加到每个用例的题面之后，或作为系统提示追加。
- 该指令冻结进 preparation 与 eval_run，参与一致性校验；评测配置页与轨迹可追溯。
- 前端向导可填写，确认页与评测配置展示；空值不改变现有行为。
- 先交付原型与修改前后截图供评审，评审通过后再进入前端与后端开发。

## 方案

- 字段：prepare/create 增加可选 `prompt_suffix`（上限 4000 字符），默认空；空值保持现有行为。
- 冻结：写入 preparation snapshot 与 eval_run，纳入 consistency identity（变更触发 configuration_changed）。
- 下发：Runner 任务包带 `prompt_suffix`；real-task 拼接 `problem_statement + "\n\n## Additional instructions\n" + suffix`，或使用 Claude Code 已支持的 `--append-system-prompt`（系统侧行为约束更强）。建议两者都支持，默认追加到系统提示，保留可选的用户题面追加。
- 前端：向导第 3 步「附加指令（可选）」textarea 与示例；第 4 步确认页展示；摘要显示「已配置 · N 字符」。
- 文案：明确「非交互、不要向用户提问、按需使用 superpowers 技能」。Claude Code 本身以 `-p` 非交互运行，但模型仍可能在输出里提问，需要指令约束。

## 原型

修改前与修改后（第 3 步与第 4 步）截图见 Issue 正文与 release 资产；原型为本地可交互 HTML 预览，本次仅评审设计，不改生产前端。

## 验收

- 原型评审通过，确认字段位置、文案与「空值不改变现有行为」。
- 实现后：prepare/create 冻结并展示 prompt_suffix；Runner 拼接或系统追加生效；轨迹中可见追加文本；空值行为与现有评测一致。
- backend、runner 与 web 相关测试通过。
