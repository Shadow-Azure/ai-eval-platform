import { Modal, Tag } from "antd";
import type { EvalTemplate } from "../api/evalTemplates";

function MethodTag({ method }: { method: "GET" | "POST" }) {
  return <Tag color={method === "POST" ? "blue" : "default"}>{method}</Tag>;
}

export function TemplateApiModal({
  template,
  open,
  onClose,
}: {
  template: EvalTemplate | null;
  open: boolean;
  onClose: () => void;
}) {
  if (!template) return null;
  const base = typeof window === "undefined" ? "" : window.location.origin;
  return (
    <Modal
      open={open}
      title="模板接口"
      width={760}
      footer={null}
      onCancel={onClose}
    >
      <p className="small muted">
        {template.name} · 列表查询与触发、状态、结果三个执行接口；鉴权复用控制台会话，启动需要管理员权限。
      </p>
      <div className="template-api-summary">
        <div>
          <span>Base URL</span>
          <code>{base}</code>
        </div>
        <div>
          <span>模板 ID</span>
          <code>{template.id}</code>
        </div>
        <div>
          <span>鉴权</span>
          <span>会话 Cookie；启动需管理员权限</span>
        </div>
      </div>
      <div className="template-api-list">
        <div className="template-api-item">
          <div className="template-api-head">
            <MethodTag method="GET" />
            <code>/api/v1/eval-templates?q=&amp;status=&amp;limit=&amp;offset=</code>
          </div>
          <p>
            控制台管理列表：按名称、数据集、Agent、模型搜索，按启用状态筛选，服务端分页返回
            total；不属于三个执行接口。
          </p>
        </div>
        <div className="template-api-item">
          <div className="template-api-head">
            <MethodTag method="POST" />
            <code>/api/v1/eval-templates/{template.id}/launch</code>
          </div>
          <p>
            按模板触发一次评测，返回 run_id 与状态/结果地址；固定字段不可覆盖，幂等键重放返回同一 run。
          </p>
          <pre className="template-api-code">{`curl -sS -X POST "${base}/api/v1/eval-templates/${template.id}/launch" \\
  -H 'Content-Type: application/json' \\
  -b 'ep_session=<session>' \\
  -d '{"idempotency_key":"nightly-20261122"}'`}</pre>
        </div>
        <div className="template-api-item">
          <div className="template-api-head">
            <MethodTag method="GET" />
            <code>/api/v1/eval-runs/{"{run_id}"}/status</code>
          </div>
          <p>轮询执行状态、进度与是否终态；terminal=true 后停止轮询，再读取结果。</p>
          <pre className="template-api-code">{`GET /api/v1/eval-runs/<run_id>/status
{"status":"running","terminal":false,"progress":{"finished":231,"total":500}}`}</pre>
        </div>
        <div className="template-api-item">
          <div className="template-api-head">
            <MethodTag method="GET" />
            <code>/api/v1/eval-runs/{"{run_id}"}/results</code>
          </div>
          <p>读取指标快照；运行中 is_final=false，终态后返回完整指标与 token 摘要。</p>
          <pre className="template-api-code">{`GET /api/v1/eval-runs/<run_id>/results
{"status":"completed","is_final":true,"items":[{"metric_key":"resolve_rate","value":0.412}]}`}</pre>
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
