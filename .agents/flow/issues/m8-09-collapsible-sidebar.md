# [m8-09] 左侧导航栏支持收缩与展开

[English](m8-09-collapsible-sidebar.en.md) | 中文

```yaml flow
kind: issue
milestone: m8
priority: P2
status: done
scope:
  - .agents/flow/issues/m8-09-collapsible-sidebar.*
  - .agents/flow/milestones/m8-bug-fixes.*
  - .agents/flow/roadmap.*
  - prototype/**
  - web/src/app/**
  - web/src/components/AppShell.tsx
  - web/src/components/Sidebar.tsx
  - web/src/components/Icon.tsx
  - web/src/styles/shell.css
  - web/src/styles/tokens.css
  - web/screenshots/**
  - plans/**
adr: []
github:
  number: 198
  url: https://github.com/Shadow-Azure/ai-eval-platform/issues/198
```

## 背景

应用外壳的左侧导航固定占用 236px（`web/src/styles/tokens.css` 的 `--sidebar`），在评测详情、会话轨迹、数据集详情等需要横向空间的页面里持续挤占内容区；希望导航可以收起成图标栏，并在需要时展开，同时不改变现有导航层级与权限。

## 目标

- 左侧导航在 236px 展开态与 72px 收起态之间切换，主内容区同步让出或收回空间。
- 收起态保留全部一级入口、当前页高亮和悬浮标签，用户不必先展开就能判断当前位置。
- 切换入口可键盘操作，状态在当前浏览器刷新与路由切换后保持；默认展开，不改变现有首屏。
- 后端不新增接口、表或迁移；导航权限仍由现有会话与 admin 过滤决定。
- 先交付设计、原型和修改前后截图；方向确认后在同一分支落地生产前端（PR #199）。

## 前端设计

### 布局与状态

| 项目 | 展开态 | 收起态 |
|---|---|---|
| 侧栏宽度 | 236px | 72px |
| 品牌区 | EP 标识 + 名称/副标题 | 仅 EP 标识，居中 |
| 分组标题 | 显示 | 隐藏，用分隔线保留分组节奏 |
| 导航项 | 图标 + 文案 | 仅图标，居中；悬停或聚焦显示标签 |
| 当前页 | 左侧蓝色竖条 + 浅蓝底 | 保留浅蓝底、蓝色图标与左侧竖条 |
| 租户区 | 头像 + 名称/说明 | 仅头像，title 提供完整信息 |
| 切换按钮 | 底部「收起导航」 | 底部仅图标，标签「展开导航」 |

- 切换按钮放在侧栏底部、租户区上方，避免与品牌区争抢首屏视觉，也符合常见后台导航的收起位置。
- 主内容 `margin-left` 跟随宽度变化，使用 180ms 过渡；`prefers-reduced-motion: reduce` 时取消动画。
- 应用现有最小宽度 1080px，不新增移动端抽屉；窄屏继续使用现有页面横向滚动行为，避免一次改动引入两套导航。

### 交互与可访问性

- 切换按钮使用原生 `button`，带 `aria-expanded`、`aria-controls="app-sidebar"` 和动态 `aria-label`/`title`；`Tab` 可达，`Enter`/`Space` 触发，切换后焦点留在按钮。
- 收起态导航项保留可读名称：实现时用 antd `Tooltip` 从右侧弹出并设置 `aria-label`；原型用 `title` 表达同一信息。
- 当前页继续使用 `NavLink` 的 active 状态；收起态不隐藏当前页，也不因宽度变化触发路由重排。
- 记忆键为 `ep.sidebar.collapsed`，值为 `1`/`0`；读取放在 `useState` 初始化中，避免刷新后先展开再收起的闪烁；`localStorage` 不可用时静默回退到展开态。
- 不改变登录、权限、面包屑和 header 行为；admin-only 项仍按会话过滤。

### 代码落点（实现阶段）

- `web/src/components/AppShell.tsx`：持有 `collapsed` 状态，给外壳加 `sidebar-collapsed` 类，并向 `Sidebar` 传 `collapsed` 与 `onToggle`。
- `web/src/components/Sidebar.tsx`：底部切换按钮、收起态 `aria-label`/`title`、品牌与租户区的条件渲染。
- `web/src/components/Icon.tsx`：新增 `chevronsLeft`/`chevronsRight` 图标。
- `web/src/styles/shell.css`、`web/src/styles/tokens.css`：`--sidebar-collapsed`、宽度与边距过渡、收起态样式及 reduced-motion。
- 不改 `Header.tsx`、路由表和后端代码。

## 后端设计

- 折叠是纯浏览器渲染偏好，不进入账号、租户、评测或审计数据模型；后端不新增 API、字段、迁移或配置。
- 角色可见性继续由现有会话与 `adminOnly` 过滤决定；收起态不会绕过权限，也不会因为本地状态改变接口返回。
- 不做跨设备同步：当前只需要单浏览器偏好，本地存储即可；若后续要跨设备，再增加用户级 `ui_preferences` 接口与迁移，本期不预留半成品字段。

## 原型与截图

原型位于 `prototype/`，是本地可交互的单文件 HTML；设计与实现均在同一 PR #199，生产前端已按本原型落地。点击侧栏底部「收起导航 / 展开导航」可切换，原型也支持用 `?sidebar=collapsed` 直接打开收起态。

修改前（当前原型，236px 固定导航）：

![修改前：当前导航栏](https://github.com/Shadow-Azure/ai-eval-platform/releases/download/m8-09-sidebar-2026-11-12/01-before.png)

修改后 · 展开态（底部新增「收起导航」）：

![修改后：展开态](https://github.com/Shadow-Azure/ai-eval-platform/releases/download/m8-09-sidebar-2026-11-12/02-after-expanded.png)

修改后 · 收起态（72px 图标栏，主内容同步左移）：

![修改后：收起态](https://github.com/Shadow-Azure/ai-eval-platform/releases/download/m8-09-sidebar-2026-11-12/03-after-collapsed.png)

## 验收

- 设计评审确认：切换入口位置、72px 收起宽度、默认展开、仅浏览器本地记忆；跨设备同步或自动收起属于新范围。
- 实现后：展开与收起在 1440px 和 1280px 视口无横向溢出，主内容与页脚布局正常；当前页高亮和悬浮标签在收起态可用。
- 实现后：刷新与路由切换保持上次状态；键盘可切换且 `aria-expanded` 正确；`prefers-reduced-motion` 下无动画。
- 实现后：`npm run build`（tsc + vite）通过，其他路由布局不受影响；后端无变更、无迁移。
