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
  asset: {
    repo_url: "https://github.com/acme/agent-assets",
    requested_ref: "candidate",
    requested_commit: "a82a590ade533b9ce8cc46f9be250e1a726da2d3",
    resolved_commit: "a82a590ade533b9ce8cc46f9be250e1a726da2d3",
    asset_snapshot_id: "snap-042",
    bundle_digest: "sha256:5d87e384133a42874f3b09458c3efef6c1b05bc46036364cbd7a05b2cae59920",
  },
})}`;
  const serviceTokenResponse = JSON.stringify({
    status: "ok",
    service_token: {
      id: "tok-01",
      name: "harness-evolution",
      scopes: ["eval:launch", "eval:read"],
      template_allowlist: [template.id],
      active: true,
    },
    token: "hev_x8…（仅此一次返回，撤销后名称不复用）",
  });
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
          <span className="template-api-summary-value">
            控制台会话 Cookie 或服务令牌 Bearer（hev_…）；启动需管理员权限或 eval:launch 令牌
          </span>
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
            按模板触发一次评测，返回 run_id 与状态/结果地址；幂等键重放返回同一 run。控制台一键启动不携带覆盖；机器触发（持{" "}
            <code>eval:launch</code> 的服务令牌）可在 body 传可选 <code>asset_override</code>
            （repo_url、ref、commit、asset_paths、asset_types，缺省继承模板）与 <code>lineage</code>
            ，把评测钉在指定候选 commit 上，202 回显 <code>asset.resolved_commit</code> 供比对。
          </p>
          <pre className="template-api-code">{`curl -sS -X POST "${base}/api/v1/eval-templates/${template.id}/launch" \\
  -H 'Content-Type: application/json' \\
  -b 'ep_session=<session>' \\
  -d '{"idempotency_key":"nightly-20261122"}'`}</pre>
          <pre className="template-api-code">{`curl -sS -X POST "${base}/api/v1/eval-templates/${template.id}/launch" \\
  -H 'Content-Type: application/json' \\
  -H 'Authorization: Bearer hev_<service-token>' \\
  -d '{"idempotency_key":"evo-round-12","asset_override":{"repo_url":"https://github.com/acme/agent-assets","ref":"candidate","commit":"a82a590ade533b9ce8cc46f9be250e1a726da2d3","asset_paths":["skills/reviewer"]},"lineage":{"evolutionRunId":"evo-12","evolutionRoundId":"round-3","roundSeq":3,"parentCommit":"bff0c1e29a44e0a1c3d5e6f708192a3b4c5d6e7f"}}'`}</pre>
          <pre className="template-api-code">{launchResponse}</pre>
        </div>
        <div className="template-api-item">
          <div className="template-api-head">
            <MethodBadge method="GET" />
            <code>/api/v1/eval-runs/{"{run_id}"}/status</code>
          </div>
          <p>
            轮询执行状态、进度与是否终态；terminal=true 后停止轮询，再读取结果。控制台会话或持
            eval:read 的服务令牌均可读取；冻结过资产快照的 run 回显 asset（resolved_commit、
            asset_snapshot_id、requested_ref）。
          </p>
          <pre className="template-api-code">{`GET /api/v1/eval-runs/<run_id>/status
{"status":"running","terminal":false,"progress":{"finished":231,"total":500},"asset":{"resolved_commit":"a82a590ade533b9ce8cc46f9be250e1a726da2d3","asset_snapshot_id":"snap-042","requested_ref":"candidate"}}`}</pre>
        </div>
        <div className="template-api-item">
          <div className="template-api-head">
            <MethodBadge method="GET" />
            <code>/api/v1/eval-runs/{"{run_id}"}/results</code>
          </div>
          <p>
            读取指标快照；运行中 is_final=false，终态后返回完整指标与 token
            摘要。鉴权同 status；launch 提供 lineage 时原样回显。
          </p>
          <pre className="template-api-code">{`GET /api/v1/eval-runs/<run_id>/results
{"status":"completed","is_final":true,"items":[{"metric_key":"resolve_rate","value":0.412}],"asset":{"resolved_commit":"a82a590ade533b9ce8cc46f9be250e1a726da2d3","asset_snapshot_id":"snap-042","requested_ref":"candidate"},"lineage":{"evolutionRunId":"evo-12","evolutionRoundId":"round-3","roundSeq":3}}`}</pre>
        </div>
        <div className="template-api-item">
          <div className="template-api-head">
            <MethodBadge method="POST" />
            <code>/api/v1/service-tokens</code>
          </div>
          <p>
            机器身份（仅管理员会话）：创建/列表/撤销服务令牌，scope 取 <code>eval:launch</code> /{" "}
            <code>eval:read</code>，可配模板白名单与并发/预算上限；明文仅在创建响应返回一次，库内只存哈希，机器启动的{" "}
            <code>created_by</code> 落 <code>svc:&lt;name&gt;</code>。无凭据 401、scope
            或白名单不足 403、达到上限 429 service_token_limit_exceeded；撤销用{" "}
            <code>DELETE /api/v1/service-tokens/{"{token_id}"}</code>。
          </p>
          <pre className="template-api-code">{`curl -sS -X POST "${base}/api/v1/service-tokens" \\
  -H 'Content-Type: application/json' \\
  -b 'ep_session=<session>' \\
  -d '{"name":"harness-evolution","scopes":["eval:launch","eval:read"],"template_allowlist":["${template.id}"],"max_concurrent_runs":2}'
${serviceTokenResponse}`}</pre>
        </div>
      </div>
      <div className="banner" style={{ marginBottom: 12 }}>
        <div>
          <strong>自进化上报随模板保存。</strong> 创建/更新模板时传{" "}
          <code>self_evolution</code>（<code>base_url</code>、<code>workspace_id</code>、
          <code>token</code>）；令牌加密存储、任何响应不回显，更新时省略令牌会保留已存值。
          一键启动沿用模板配置，不在启动请求里覆盖；机器触发的 <code>asset_override</code>{" "}
          只注入资产来源，不改上报开关，模板启用上报时随响应 <code>warnings</code> 提示一次。
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
