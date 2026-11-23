import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Alert, Button, Descriptions, Modal, message } from "antd";
import { useNavigate } from "react-router-dom";
import type { EvalTemplate } from "../api/evalTemplates";
import { launchEvalTemplate } from "../api/evalTemplates";
import type { PreflightIssue } from "../api/evaluations";
import { ApiError } from "../api/types";
import { useAuth } from "../app/auth";
import { Icon } from "./Icon";
import {
  TemplateBlockerList,
  caseSelectionLabel,
  costEstimateLabel,
  executionSummary,
  imageDependencyDetail,
  imageDependencyLabel,
  retryLabel,
  selfEvolutionLabel,
  selfEvolutionTokenLabel,
  templatePreflightBlockers,
} from "./TemplateBits";

interface TemplateLaunchModalProps {
  template: EvalTemplate | null;
  open: boolean;
  onClose: () => void;
}

function issuesFromDetails(
  details: Record<string, unknown> | null | undefined,
  key: "blockers" | "warnings",
): PreflightIssue[] {
  const raw = details && Array.isArray(details[key]) ? details[key] : [];
  return raw.map((entry) => {
    const item = (entry ?? {}) as Record<string, unknown>;
    return {
      code: typeof item.code === "string" ? item.code : "",
      message: typeof item.message === "string" ? item.message : "",
      field: typeof item.field === "string" ? item.field : undefined,
      details:
        typeof item.details === "object" && item.details !== null
          ? (item.details as Record<string, unknown>)
          : undefined,
    };
  });
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
  const [blockers, setBlockers] = useState<PreflightIssue[]>([]);
  const [warnings, setWarnings] = useState<PreflightIssue[]>([]);
  const [launchFailed, setLaunchFailed] = useState(false);

  useEffect(() => {
    if (!open) return;
    setBlockers(template ? templatePreflightBlockers(template) : []);
    setWarnings([]);
    setLaunchFailed(false);
  }, [open, template?.id, template?.version, template?.preflight?.status]);

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
      const api = error instanceof ApiError ? error : null;
      const nextBlockers = api ? issuesFromDetails(api.details, "blockers") : [];
      if (nextBlockers.length) {
        setBlockers(nextBlockers);
        setWarnings(api ? issuesFromDetails(api.details, "warnings") : []);
        setLaunchFailed(true);
        return;
      }
      message.error(error instanceof Error ? error.message : "启动失败");
    },
  });

  if (!template) return null;

  const blocked = blockers.length > 0;

  return (
    <Modal
      open={open}
      title={blocked ? "启动被阻断" : `从「${template.name}」启动？`}
      width={680}
      onCancel={onClose}
      footer={
        blocked
          ? [
              <Button key="close" onClick={onClose}>
                关闭
              </Button>,
            ]
          : [
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
            ]
      }
    >
      {blocked ? (
        <>
          <TemplateBlockerList
            blockers={blockers}
            title={`发现 ${blockers.length} 个阻断项`}
            description={
              launchFailed
                ? "启动接口返回了以下阻断项；本次没有创建评测，也没有占用资源。"
                : "这些依赖在保存时已能判定缺失，确认启动已禁用；修正后才能启动。"
            }
          />
          <Alert
            type="warning"
            showIcon
            style={{ marginTop: 14 }}
            message="本次没有创建评测，也没有占用资源。"
            description="资产加载、容量与模型凭据仍会在下次启动时重新检查；补齐上面的依赖后重试即可。"
          />
          {warnings.length ? (
            <Alert
              type="info"
              showIcon
              style={{ marginTop: 14 }}
              message="其他提示"
              description={
                <ul style={{ margin: 0, paddingLeft: 18 }}>
                  {warnings.map((warning, index) => (
                    <li key={`${warning.code}-${index}`}>{warning.message}</li>
                  ))}
                </ul>
              }
            />
          ) : null}
        </>
      ) : (
        <>
          <p className="small muted" style={{ marginTop: 0 }}>
            配置已固化，启动时不再询问；只确认本次费用与容量。
          </p>
          <Descriptions size="small" column={1} bordered labelStyle={{ width: 120 }}>
            <Descriptions.Item label="数据集">
              {template.dataset_name ?? "—"}
              <span className="small muted">
                {" "}
                · {template.dataset_version ?? "—"} · {template.case_count} 用例
              </span>
            </Descriptions.Item>
            <Descriptions.Item label="镜像依赖">
              {imageDependencyLabel(template)}
              <div className="small muted">{imageDependencyDetail(template)}</div>
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
            <Descriptions.Item label="自进化上报">
              {selfEvolutionLabel(template)}
              <div className="small muted">
                {selfEvolutionTokenLabel(template)} ·{" "}
                {template.self_evolution.enabled
                  ? "启动沿用模板保存的上报目标，不在启动请求里覆盖"
                  : "未启用：本次启动不上报 bad case"}
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
        </>
      )}
    </Modal>
  );
}
