# [m8-42] 轨迹页会话区改为顶部下拉，关键节点与原始文件可折叠

[English](m8-42-trace-session-picker.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: ready
scope:
  - web/src/**
  - tests/**
  - .agents/flow/**
  - plans/**
adr: []
github:
  number: 294
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/294
```

## 背景

轨迹页左侧当前按「关键节点 → 会话 → 原始文件」顺序平铺：会话以树形列表展开，主 Agent 与子 Agent 混在列表中；有多个子 Agent 时列表变长，关键节点被推挤。页面顶部只显示主 Agent 的 sessionId，选中子 Agent 后，用户无法在会话切换处确认当前子会话的原生 sessionId，只能从右侧详情或原始文件反查。

「关键节点」是轨迹阅读的主导航，但「原始文件」默认展开后长期占用左栏，低频操作挤占了高频导航；两者都需要可折叠，且应让关键节点默认可见、原始文件默认收起。

## 目标

- 将「会话」移到左栏最上方，改为下拉选择器；选项覆盖主 Agent 与全部子 Agent，每项显示角色（主 Agent / 子 Agent / 未关联）、Agent 名称、原生 sessionId 与 span 数。
- 下拉默认选中主 Agent 根会话；被选中会话在触发器内显示其原生 sessionId；顶部 attemptId / 主 Agent sessionId 双行标识保持既有语义，不随子会话切换。
- 「关键节点」改为可折叠，默认展开；折叠后只保留标题与节点数，展开后保持现有跳转与高亮行为。
- 「原始文件」改为可折叠，默认折叠；折叠时在标题旁显示文件数与主/子 Agent 摘要，展开后保留逐文件受控下载与轨迹包入口。
- 本次直接更新控制台前端（`web/src` 中的交互原型页面）并附修改前后截图；不改后端 API、trace 数据模型或归一化口径。

## 修改前后截图

截图来自本地控制台，使用同一份合成轨迹（1 个主 Agent、1 个子 Agent、1 个未关联会话、18 个 span）和同一条路由；Before 为 `1338f6a`，After 为本分支，1440×900 浅色主题，账号信息已脱敏。

**Before：左栏为「关键节点 → 会话 → 原始文件」，会话是树形列表，原始文件默认展开。**

![Before：轨迹页左栏](https://github.com/Shadow-Azure/ai-eval-platform/releases/download/m8-42-trace-session-picker-evidence-20261118/m8-42-before-trace-outline.png)

**After 1：会话移到左栏顶部并变为下拉，默认主 Agent；关键节点默认展开，原始文件默认折叠。**

![After：默认状态](https://github.com/Shadow-Azure/ai-eval-platform/releases/download/m8-42-trace-session-picker-evidence-20261118/m8-42-after-trace-default.png)

**After 2：下拉展开，选项区分主 Agent / 子 Agent / 未关联，显示 Agent 名称、原生 sessionId 与 span 数。**

![After：会话下拉](https://github.com/Shadow-Azure/ai-eval-platform/releases/download/m8-42-trace-session-picker-evidence-20261118/m8-42-after-trace-dropdown.png)

**After 3：切换到子 Agent，触发器显示子会话原生 sessionId，中间轨迹与右侧详情联动；顶部主 Agent sessionId 保持不变。**

![After：选中子 Agent](https://github.com/Shadow-Azure/ai-eval-platform/releases/download/m8-42-trace-session-picker-evidence-20261118/m8-42-after-trace-subagent.png)

**After 4：折叠关键节点、展开原始文件，验证两个 section 均可切换。**

![After：折叠与展开](https://github.com/Shadow-Azure/ai-eval-platform/releases/download/m8-42-trace-session-picker-evidence-20261118/m8-42-after-trace-toggles.png)

## 验收

- 轨迹页左栏顺序为「会话下拉 → 关键节点 → 原始文件」，会话下拉默认主 Agent，切换子 Agent 后中间轨迹与右侧详情联动，触发器显示当前会话的 sessionId。
- 下拉选项能区分主 Agent、子 Agent 与未关联会话，显示 Agent 名称、sessionId 与 span 数；未关联会话不被误认为主 Agent。
- 关键节点默认展开，点击标题可折叠/展开；原始文件默认折叠，点击标题可展开；两者均可用键盘操作，带 `aria-expanded`。
- 现有筛选、搜索、深链、时间线、详情检查器、原始文件下载与轨迹包导出不回归；`web` 类型检查、构建与 `npm test` 通过。
