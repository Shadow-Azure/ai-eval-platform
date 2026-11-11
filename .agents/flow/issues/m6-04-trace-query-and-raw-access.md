# 提供轨迹查询 API 与受控原始文件访问

[English](m6-04-trace-query-and-raw-access.en.md) | 中文

```yaml flow
kind: issue
milestone: m6
priority: P0
status: done
scope:
  - .agents/flow/issues/m6-04-trace-query-and-raw-access.*
  - .agents/flow/milestones/m6-trace-diagnosis.*
  - backend/**
  - tests/**
  - plans/**
  - reports/**
  - docs/**
  - scripts/doc-budgets.manifest.json
adr: []
github:
  number: 93
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/93
```

## 背景

轨迹入库后，前端仍需要稳定的 attempt 级查询契约和安全的原件访问方式。原始会话可能包含凭据、隐藏测试、完整 thinking 和工具输出，不能通过公开对象键或永久 URL 绕过平台权限。

## 目标

- 提供用例 attempt 的轨迹首页、session 树、session spans 分页、span 详情和大内容按需读取 API，返回导入状态、parser 版本与数据质量信息。
- 所有查询校验租户、case run 和 attempt 归属，使用稳定排序与游标分页，避免深层轨迹和跨 session 查询产生不可控的数据量。
- 原始文件通过平台签发限时下载地址，只允许访问登记的 trace_file；下载要求管理员权限并写审计，只读角色默认看不到完整 thinking。
- API 对 degraded、failed、unlinked 和时间不可比数据给出明确字段，错误响应不泄露对象凭据、敏感正文或其他租户信息。

## 验收

- 用户能查询指定 attempt 的完整会话树并分页读取 span，切换 attempt 不会复用另一 attempt 的标识或内容。
- 跨租户、跨项目和无权限请求被拒绝，客户端传入任意 object key 不能下载未登记对象，限时 URL 过期后不可继续使用。
- 原件下载有权限与审计记录，只读角色的 thinking 和敏感字段按策略隐藏，API 不返回长期凭据。
- 后端测试覆盖分页边界、深链归属、权限、审计、降级响应和大内容读取，常规查询无 N+1 放大。
