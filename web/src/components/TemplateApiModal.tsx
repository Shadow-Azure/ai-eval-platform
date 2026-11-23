import { Button, Modal } from "antd";
import type { EvalTemplate } from "../api/evalTemplates";
import { Icon } from "./Icon";

function MethodBadge({ method }: { method: "GET" | "POST" }) {
  return <span className={`template-api-method ${method.toLowerCase()}`}>{method}</span>;
}

export function TemplateApiModal({
  template,
  open,
  onClose,
  onLaunch,
}: {
  template: EvalTemplate | null;
  open: boolean;
  onClose: () => void;
  onLaunch: () => void;
}) {
  if (!template) return null;
  const base = typeof window === "undefined" ? "" : window.location.origin;
  const runId = "eval-007";
  const listResponse = `GET /api/v1/eval-templates?q=&status=&limit=20&offset=0
${JSON.stringify({
  status: "ok",
  items: [
    {
      id: template.id,
      name: template.name,
      enabled: true,
      version: 3,
      launch_count: 18,
      preflight: { status: "ready" },
    },
  ],
  total: 3,
  limit: 20,
  offset: 0,
})}`;
  const launchResponse = `HTTP/1.1 202 Accepted
${JSON.stringify({
  run_id: runId,
  template_id: template.id,
  status: "queued",
  status_url: `/api/v1/eval-runs/${runId}/status`,
  result_url: `/api/v1/eval-runs/${runId}/results`,
  created: true,
})}`;
  return (
    <Modal
      open={open}
      title={
        <div className="template-api-heading">
          <div className="template-api-heading-title">模板接口</div>
          <div className="template-api-heading-subtitle">
            {template.name} · 列表查询与触发、状态、结果三个执行接口
          </div>
        </div>
      }
      width={760}
      centered
      rootClassName="template-api-modal"
      onCancel={onClose}
      footer={[
        <Button key="close" autoInsertSpace={false} onClick={onClose}>
          关闭
        </Button>,
        <Button
          key="launch"
          type="primary"
          autoInsertSpace={false}
          icon={<Icon name="play" size={13} />}
          onClick={onLaunch}
        >
          一键启动
        </Button>,
      ]}
    >
      <div className="template-api-summary">
        <div>
          <span>Base URL</span>
          <code title={base}>{base}</code>
        </div>
        <div>
          <span>模板 ID</span>
          <code title={template.id}>{template.id}</code>
        </div>
        <div>
          <span>鉴权</span>
          <span className="template-api-summary-value">会话 Cookie；启动需管理员权限</span>
        </div>
      </div>
      <div className="template-api-list">
        <div className="template-api-item">
          <div className="template-api-head">
            <MethodBadge method="GET" />
            <code>/api/v1/eval-templates?q=&amp;status=&amp;limit=&amp;offset=</code>
          </div>
          <p>
            控制台管理列表：按名称、数据集、Agent、模型搜索，按启用状态筛选，服务端分页返回
            total；不属于三个执行接口。
          </p>
          <pre className="template-api-code">{listResponse}</pre>
        </div>
        <div className="template-api-item">
          <div className="template-api-head">
            <MethodBadge method="POST" />
            <code>/api/v1/eval-templates/{template.id}/launch</code>
          </div>
          <p>
            按模板触发一次评测，返回 run_id 与状态/结果地址；固定字段不可覆盖，幂等键重放返回同一 run。
          </p>
          <pre className="template-api-code">{`curl -sS -X POST "${base}/api/v1/eval-templates/${template.id}/launch" \\
  -H 'Content-Type: application/json' \\
  -b 'ep_session=<session>' \\
  -d '{"idempotency_key":"nightly-20261122"}'`}</pre>
          <pre className="template-api-code">{launchResponse}</pre>
        </div>
        <div className="template-api-item">
          <div className="template-api-head">
            <MethodBadge method="GET" />
            <code>/api/v1/eval-runs/{"{run_id}"}/status</code>
          </div>
          <p>轮询执行状态、进度与是否终态；terminal=true 后停止轮询，再读取结果。</p>
          <pre className="template-api-code">{`GET /api/v1/eval-runs/<run_id>/status
{"status":"running","terminal":false,"progress":{"finished":231,"total":500}}`}</pre>
        </div>
        <div className="template-api-item">
          <div className="template-api-head">
            <MethodBadge method="GET" />
            <code>/api/v1/eval-runs/{"{run_id}"}/results</code>
          </div>
          <p>读取指标快照；运行中 is_final=false，终态后返回完整指标与 token 摘要。</p>
          <pre className="template-api-code">{`GET /api/v1/eval-runs/<run_id>/results
{"status":"completed","is_final":true,"items":[{"metric_key":"resolve_rate","value":0.412}]}`}</pre>
        </div>
      </div>
      <div className="banner" style={{ marginBottom: 12 }}>
        <div>
          <strong>自进化上报随模板保存。</strong> 创建/更新模板时传{" "}
          <code>self_evolution</code>（<code>base_url</code>、<code>workspace_id</code>、
          <code>token</code>）；令牌加密存储、任何响应不回显，更新时省略令牌会保留已存值。
          一键启动沿用模板配置，不在启动请求里覆盖。
        </div>
      </div>
      <div className="banner">
        <div>
          <strong>模板只固化选择，不固化执行结果。</strong>{" "}
          每次启动都会重新解析资产 Ref 并冻结新的快照；模板后续编辑不会改变已启动的评测。
        </div>
      </div>
    </Modal>
  );
}
