# [m8] 出口代理空密码 Basic 凭据导致 407：Runner 补非空占位密码与回归测试

[English](m8-egress-proxy-empty-password.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: done
scope:
  - .agents/flow/issues/m8-egress-proxy-empty-password.*
  - runner/scripts/real-task.mjs
  - tests/runner/**
  - tests/deploy/**
  - deploy/proxy/**
  - docs/**
  - scripts/doc-budgets.manifest.json
  - plans/**
adr: []
github:
  number: 442
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/442
```

## 背景

2026-11-26 起，依赖出口代理下载 GitHub 资产的评测在 install 阶段被 407 拒绝（`asset_source_install_failed` / E407），候选评测无法进入 agent 执行。Runner 把 egress token 放在 HTTP Basic 用户名位、密码留空；Squid 对空密码凭据直接返回 407，不会调用 auth helper。同一问题在多个自进化 run 复现，完整诊断与对照实验见 issue #442。生产影响按 P0 处理；流程档位记为 P2，以符合 m8 当前最高未清档（P1）下的并行规则。

## 目标

- `runner/scripts/real-task.mjs` 的 `credentialedEgressProxy()` 使用非空占位密码，生成 `http://<token>:x@<host>:<port>`。
- Runner 与代理两侧的契约注释、`docs/architecture` 中英示例同步为「用户名位放 token、密码位放非空占位符」。
- 增加回归测试：断言生成的代理 URL 密码段非空；helper 在收到非空占位密码时仍只校验用户名 token。

## 验收

- 有 token 时资产 install 与 agent exec 的 `HTTPS_PROXY` / `HTTP_PROXY` 均为 `http://<token>:x@<host>:<port>`，密码段非空；旧实现的空密码形态会让测试失败。
- 无 token 时仍注入不带凭据的私网地址，行为不变。
- `node --test tests/runner/real-task-runtime-setup.test.mjs`、`node --test tests/deploy/egress-proxy.test.mjs`、`npm test`、`npm run lint:docs` 与 `npm run verify:flow` 通过；PR CI 通过。
- issue #442 已用真实 Squid 验证空密码 407、非空密码 200；本 PR 用单测锁定 URL 形态，部署后按验收复跑一次真实 asset install。
