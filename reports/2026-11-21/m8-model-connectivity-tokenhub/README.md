# 模型连通性测试 TokenHub 场景修复证据

- 基线（before）：生产部署 `22273ee`，`http://47.98.144.243:8080`。
- 变更（after）：本 PR 分支，本地 Vite 开发服务器渲染同一页面，API 代理到同一生产后端。
- 场景：模型凭据页 →「添加模型凭据」抽屉，1440×900，浅色主题，同一管理员账号。
- 差异：Base URL 辅助说明由「追加 /models」改为按协议发送最小模型请求。

| 文件 | 说明 |
|---|---|
| `before-models-drawer.png` | 基线：旧文案 |
| `after-models-drawer.png` | 变更后：新文案 |

后端行为验证（不消耗生产额度）：在 API 容器内用生产 DeepSeek 凭据直连
`https://tokenhub.tencentmaas.com/plan/anthropic`：

- `GET /v1/models` → 404（腾讯云 TokenHub 未提供模型列表）
- `POST /v1/messages` → 200（同一把 Key）

因此旧连通性测试必然失败，而真实模型调用可用。
