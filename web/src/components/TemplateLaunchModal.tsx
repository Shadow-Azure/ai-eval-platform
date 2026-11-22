import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";
import { Alert, Button, Descriptions, Modal, message } from "antd";
import { useNavigate } from "react-router-dom";
import type { EvalTemplate } from "../api/evalTemplates";
import { launchEvalTemplate } from "../api/evalTemplates";
import { useAuth } from "../app/auth";
import { Icon } from "./Icon";
import {
  caseSelectionLabel,
  costEstimateLabel,
  executionSummary,
  retryLabel,
} from "./TemplateBits";

interface TemplateLaunchModalProps {
  template: EvalTemplate | null;
  open: boolean;
  onClose: () => void;
}

function newIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `launch-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function TemplateLaunchModal({ template, open, onClose }: TemplateLaunchModalProps) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { session } = useAuth();
  const canLaunch = session?.role === "admin";
  // One key per template intent: a retried request reuses it, so a lost
  // response returns the original run instead of creating a second one.
  const launchKeyRef = useRef<{ templateId: string; key: string } | null>(null);

  const launch = useMutation({
    mutationFn: async (target: EvalTemplate) => {
      if (!launchKeyRef.current || launchKeyRef.current.templateId !== target.id) {
        launchKeyRef.current = { templateId: target.id, key: newIdempotencyKey() };
      }
      return launchEvalTemplate(target.id, {}, launchKeyRef.current.key);
    },
    onSuccess: (response) => {
      launchKeyRef.current = null;
      void queryClient.invalidateQueries({ queryKey: ["eval-templates"] });
      void queryClient.invalidateQueries({ queryKey: ["evaluations"] });
      message.success("评测已创建，正在打开详情");
      onClose();
      navigate(`/evals/${response.run_id}`);
    },
    onError: (error) => {
      message.error(error instanceof Error ? error.message : "启动失败");
    },
  });

  if (!template) return null;

  return (
    <Modal
      open={open}
      title={`从「${template.name}」启动？`}
      width={680}
      onCancel={onClose}
      footer={[
        <Button key="cancel" onClick={onClose}>
          取消
        </Button>,
        <Button
          key="launch"
          type="primary"
          icon={<Icon name="play" size={13} />}
          loading={launch.isPending}
          disabled={!template.enabled || !canLaunch}
          onClick={() => launch.mutate(template)}
        >
          确认启动
        </Button>,
      ]}
    >
      <p className="small muted" style={{ marginTop: 0 }}>
        配置已固化，启动时不再询问；只确认本次费用与容量。
      </p>
      <Descriptions size="small" column={1} bordered>
        <Descriptions.Item label="数据集">
          {template.dataset_name ?? "—"}
          <span className="small muted">
            {" "}
            · {template.dataset_version ?? "—"} · {template.case_count} 用例
          </span>
        </Descriptions.Item>
        <Descriptions.Item label="Agent 资产">
          {template.runtime_key} {template.runtime_version ?? ""}
          <div className="small muted">
            {template.asset_source_name ?? "仅使用 Runtime 内置能力"}
            {template.asset_source_name
              ? ` · ${template.asset_ref ?? "默认 Ref"} · ${template.asset_path_count} 项`
              : ""}
          </div>
        </Descriptions.Item>
        <Descriptions.Item label="模型">
          {template.model_display_name ?? "—"}
          <div className="small muted">
            {template.model_provider ?? "—"} · {template.model_name ?? "—"} · 启动时换发短期凭据
          </div>
        </Descriptions.Item>
        <Descriptions.Item label="资源池">
          {template.resource_pool_name ?? "—"}
          <div className="small muted">
            {template.resource_pool_max_capacity != null
              ? `最大容量 ${template.resource_pool_max_capacity}`
              : "容量以当前在线 Runner 为准"}
          </div>
        </Descriptions.Item>
        <Descriptions.Item label="用例与策略">
          {caseSelectionLabel(template)} · {executionSummary(template)}
          <div className="small muted">
            失败重试：{retryLabel(template)} · 最大轮次 {template.max_turns ?? "默认"} · Agent 超时{" "}
            {template.agent_timeout_seconds
              ? `${Math.round(template.agent_timeout_seconds / 60)} 分钟`
              : "数据集默认"}
          </div>
        </Descriptions.Item>
        <Descriptions.Item label="预计费用">
          {costEstimateLabel(template.estimated_cost)}
          <div className="small muted">
            {template.estimated_cost?.basis ?? "未填写定价时费用以模型网关实际记账为准"}
          </div>
        </Descriptions.Item>
      </Descriptions>
      {!template.enabled ? (
        <Alert
          type="error"
          showIcon
          style={{ marginTop: 14 }}
          message="模板已停用"
          description="停用的模板不能启动。先启用模板，或编辑后再启动。"
        />
      ) : !canLaunch ? (
        <Alert
          type="warning"
          showIcon
          style={{ marginTop: 14 }}
          message="当前账号没有启动权限"
          description="启动评测需要管理员权限；可以查看模板详情与运行结果。"
        />
      ) : (
        <Alert
          type="warning"
          showIcon
          style={{ marginTop: 14 }}
          message={`这会占用资源池并产生模型费用。${template.prompt_suffix ? "固定附加指令也会随本次启动生效。" : ""}`}
          description="启动时平台会重新解析资产 Ref、检查镜像与容量，再按既有 prepare/create 流程创建评测；失败不会留下半创建的任务。"
        />
      )}
    </Modal>
  );
}
