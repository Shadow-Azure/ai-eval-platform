# 实现 attempt 级三栏轨迹查看器

[English](m6-05-trace-viewer.en.md) | 中文

```yaml flow
kind: issue
milestone: m6
priority: P0
status: in-progress
scope:
  - .agents/flow/issues/m6-05-trace-viewer.*
  - .agents/flow/milestones/m6-trace-diagnosis.*
  - web/**
  - tests/**
  - docs/**
  - plans/**
  - reports/**
  - scripts/doc-budgets.manifest.json
adr: []
github:
  number: 94
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/94
```

## 背景

M5 的用例详情提供测试与结果入口，但没有独立轨迹页面。M6 需要把指定 attempt 的会话树、waterfall 和 span 属性连成可深链的诊断界面，并在解析降级时仍保留原始文件核查路径。

## 目标

- 按既有原型增加轨迹路由和用例入口，URL 保存 run、case、attempt、session 与 span 选择，刷新、新窗口和返回操作可恢复同一位置。
- 实现左侧会话树、中间 waterfall、右侧 span 摘要/内容/属性/原始记录三栏联动，unlinked 与 degraded 分组清晰可见。
- 切换 attempt 或 session 时取消或忽略过期请求并清理选择，waterfall 只使用统一父子证据，时间异常时按序号布局并提示。
- 大文本按需加载并以安全文本渲染，覆盖加载、空态、错误、处理中、降级、失败和无权限状态，原件入口遵循服务端权限。

## 验收

- 用户从用例行进入指定 attempt 后可以选择 session 与 span，复制 URL 或刷新页面能恢复相同视图，浏览器返回不丢失上下文。
- 快速切换 attempt 不会混入旧 session/span，会话树与 waterfall 关系一致，缺失 parent、unlinked 和时钟不可比均有明确提示。
- 解析失败不影响测试结果展示，用户能看到脱敏原因并在有权限时打开 raw；恶意 HTML/Markdown 和工具输出不会执行脚本。
- 前端测试与视觉对照覆盖三栏联动、深链、长内容、窄窗口、加载/空/错/降级状态和权限差异。
