# [m8] 成功完成回调后释放 Runner 绑定

[English](m8-runner-completion-release.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: in-progress
scope:
  - .agents/flow/issues/m8-runner-completion-release.*
  - backend/app/services/case_attempts.py
  - tests/backend/test_case_callbacks.py
  - tests/backend/test_reconciler.py
adr: []
github:
  number: 281
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/281
```

## 背景

2026-11-18 生产 fixed 池复现：用例成功完成（resolved）后，`runner.busy_case_attempt_id` 仍指向该用例，下一次 fixed 评测的 `started` 回调被 409 `runner_busy_conflict` 拒绝，Runner 记 `stale_attempt` 后退出，评测停留在 queued，只能取消后手动释放绑定再重跑。失败/超时/spot 中断路径已通过 `lifecycle.release_runner_binding` 释放绑定；成功完成路径由 `finalize_completion` 收口，却没有释放，且 Runner 终态回调不带 `X-Runner-Id`，API 边缘的 `release_case` 也不会触发。

## 目标

- 在 `finalize_completion` 的完成事务内释放该 attempt 绑定的 Runner，覆盖 Runner 回调与 reconciler 补写完成两条路径。
- 无绑定、无 runner_id 或绑定属于其他用例时保持 no-op，不改变失败/spot/预算暂停的既有释放语义。
- 补充回归测试，覆盖 started 绑定后 completed 释放、以及没有 `X-Runner-Id` 的真实回调形态。

## 验收

- `started` 绑定 Runner 后发送 `completed`（不带 `X-Runner-Id`），Runner 的 `busy_case_attempt_id` 清空、`status` 回到 `online`。
- reconciler 补写完成时同样释放绑定；未绑定 Runner 的用例完成不产生副作用。
- 既有 case callback、lifecycle、reconciler 测试与 CI 全绿。
