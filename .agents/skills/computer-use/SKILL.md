---
name: computer-use
description: 通过 node_repl 调用 @oai/sky 查看、操作和验证 macOS 应用界面；当用户要求点击、输入、滚动、截屏或读取桌面 UI 时使用。
argument-hint: "[应用名或完整 .app 路径] [要完成的操作]"
disable-model-invocation: true
---

# Computer Use

Computer Use 没有独立的 `click` / `type` / `screenshot` 工具。Codex 通过 `node_repl` 动态导入 `@oai/sky`，再用同一个对象执行读取、截屏和交互。全程中文汇报。

## 核心入口

```js
const { sky } = await import("@oai/sky");

const apps = await sky.list_apps();
const state = await sky.get_app_state({ app: "/Applications/Example.app" });

await nodeRepl.emitImage(state.screenshot.url);
nodeRepl.write(state.text);
```

`list_apps()` 返回应用名、bundle id 和运行状态。`get_app_state()` 是每次操作前后的主读取面，返回：

- `app`：Computer Use 实际定位到的应用；
- `screenshot.url`：本地 `file://` 截图；
- `text`：accessibility 树或相对上一次的树 diff。

## 寻址应用

- 所有交互都传同一个 `app` 值；优先使用完整 `.app` 路径，例如 `/Applications/Example.app`。
- 显示名或 bundle id 出现多份安装副本时可能 ambiguous；此时不要猜，改用完整路径。
- `get_app_state()` 的参数必须是对象，且应用字段是普通数据属性：`{ app }`。
- 目标应用未运行时，`sky` 不负责启动；先用普通 shell 获得用户授权后启动，再回来调用 Computer Use。

## 读取界面

1. 先调用 `get_app_state({ app })` 建立基线。需要完整树而不是相对 diff 时，用 `get_app_state({ app, disableDiff: true })`。
2. 用 `nodeRepl.emitImage(state.screenshot.url)` 给用户展示截图。
3. 用 `state.text` 找控件。首次通常是完整树；操作后可能返回带 `+`、`~` 和删除范围摘要的 diff。
4. 从包含角色和标题的行里解析 `element_index`，例如 `button 保存`、`text field 名称`、`pop up button 厂商`。
5. 每次界面可能变化后重新取态。不要跨多个操作复用旧编号；旧索引在重建、滚动、弹层或窗口切换后会失效。

## 执行操作

常用调用形态：

```js
await sky.click({ app, element_index: 12 });
await sky.click({ app, element_index: 12, click_count: 2 });
await sky.type_text({ app, text: "示例文本" });
await sky.press_key({ app, key: "Return" });
await sky.set_value({ app, element_index: 12, value: "示例值" });
await sky.scroll({ app, element_index: 12, direction: "down", pages: 1 });
await sky.perform_secondary_action({ app, element_index: 12, action: "Show Menu" });
```

不同平台和控件可能只支持其中一部分。调用失败时，把错误当作契约线索：

- `Cannot set a value...not settable`：该控件不能程序化赋值，改用点击展开、键盘或菜单项；
- `requires an object input`：不要传字符串，改传 `{ app }`；
- `Ambiguous app identifier`：改传完整 `.app` 路径；
- 操作后树无变化：等待一拍再取态，确认窗口是否聚焦、弹层是否异步渲染。

## 安全与授权

- 只执行用户明确要求的操作。保存、删除、支付、发送、提交、关闭未保存内容等动作必须先确认。
- 启动 GUI、安装软件、改变系统设置需要对应授权；Computer Use 不绕过登录、系统保护、验证码或安全提示。
- 处理密钥、令牌、个人数据时，不要无差别输出 UI 树或截图。可在内存中比对并只报告布尔结果。
- 自己启动的进程要在任务结束时停止；如果修改了临时文件，也要清理。

## 推荐节奏

```js
const { sky } = await import("@oai/sky");
const app = "/Applications/Example.app";

let state = await sky.get_app_state({ app });
// 解析 state.text，找到目标 index。

await sky.click({ app, element_index: 8 });
await new Promise((resolve) => setTimeout(resolve, 800));

state = await sky.get_app_state({ app });
await nodeRepl.emitImage(state.screenshot.url);
nodeRepl.write(state.text);
```

把复杂任务拆成小步：读取 → 判断 → 一个动作 → 重新读取 → 汇报。每一步用最新 accessibility 树验证，而不是凭截图坐标盲点。
