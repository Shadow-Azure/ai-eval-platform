# 评测模板列表长名称布局塌陷：对比截图与根因证据

来源：2026-11-23 飞书请求（附生产「评测模板」截图）。关联 issue：#386。生产截图为用户提供；其余截图在 `main@094721f` 上以同一数据、同一视口（1500×837，deviceScaleFactor=2）生成。

## 文件

| 文件 | 说明 |
|---|---|
| `01-production.jpg` | 生产截图，裁掉顶部账号栏；唯一模板名称较长 |
| `02-prototype.png` | `prototype/index.html#/evals/templates`，把同一模板名称/描述/固定组合注入原型内存数据后截图；原型源码未改 |
| `03-fixed-proposal.png` | 在本地 web 页面临时注入推荐样式后的效果（仅验证，未提交代码） |
| `04-local-reproduction.png` | `main@094721f` 本地 web + mock 控制面，与生产同数据/同视口复现 |

截图均为 3000×1500（裁掉顶部账号栏后的 1500×750 逻辑视口 @2x）。

## 关键测量（`main@094721f`，本地复现）

| 项目 | 生产实测 | 原型 |
|---|---|---|
| `table-layout` | `auto` | `fixed` |
| 表格宽度 | 1198px | 1198px |
| 模板列 | 658px；名称按钮 640px，`white-space: nowrap` | 24%（约 291px），单行省略 |
| 固定组合列 | 71px；内容宽 55px、高 315px | 27%（约 327px），三行 nowrap + ellipsis |
| 行高 | 332px | 54px |
| 状态 / 最近启动 / 累计启动 / 费用估算 / 更新 / 操作 | 85 / 69 / 50 / 62 / 70 / 132px（声明的宽度被压缩） | 固定百分比 |

## 复现方式

```sh
# 1. 启动 mock 控制面（返回与生产相同的单个长名称模板）
python3 artifacts/mock_api.py

# 2. 在 web/ 下用临时配置启动 Vite（代理到 mock）
cd web && ./node_modules/.bin/vite --config vite.repro.config.ts

# 3. 打开并截图
# http://127.0.0.1:5199/evals/templates
```

根因是 `web/src/pages/EvalTemplatesPage.tsx` 的 antd `Table` 没有固定表格布局/列宽，模板名称又沿用 antd `Button` 的 `white-space: nowrap`，长名称把模板列撑到 640px，固定组合列只剩 71px，逐词换行把行高撑到 332px。原型用 `table-layout: fixed`、固定列宽和省略号规避了同一问题。
