import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Descriptions, Modal, Popconfirm, Space, message } from "antd";
import { useState } from "react";
import {
  deleteEvalTemplate,
  updateEvalTemplate,
  type EvalTemplate,
} from "../api/evalTemplates";
import { Icon } from "./Icon";

interface TemplateActionsProps {
  template: EvalTemplate;
  canManage: boolean;
  canLaunch: boolean;
  allowDisable?: boolean;
  onLaunch: () => void;
}

/**
 * M10 #416: disabled templates cannot launch, so their action area offers
 * enable and delete instead. The template list and the quick-launch rows share
 * this renderer so both entries keep the same behavior.
 */
export function TemplateActions({
  template,
  canManage,
  canLaunch,
  allowDisable = false,
  onLaunch,
}: TemplateActionsProps) {
  const queryClient = useQueryClient();
  const [deleteOpen, setDeleteOpen] = useState(false);

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["eval-templates"] });
  };

  const toggle = useMutation({
    mutationFn: (enabled: boolean) =>
      updateEvalTemplate(template.id, { enabled }, template.version),
    onSuccess: (updated) => {
      message.success(`模板「${updated.name}」已${updated.enabled ? "启用" : "停用"}`);
      refresh();
    },
    onError: (error) => message.error(error instanceof Error ? error.message : "更新失败"),
  });

  const remove = useMutation({
    mutationFn: () => deleteEvalTemplate(template.id),
    onSuccess: () => {
      setDeleteOpen(false);
      message.success(`模板「${template.name}」已删除`);
      refresh();
    },
    onError: (error) => message.error(error instanceof Error ? error.message : "删除失败"),
  });

  const launchButton = (disabled: boolean, title?: string) => (
    <Button
      size="small"
      type="primary"
      icon={<Icon name="play" size={12} />}
      disabled={disabled}
      title={title}
      onClick={onLaunch}
    >
      启动
    </Button>
  );

  if (!template.enabled) {
    if (!canManage) return launchButton(true, "模板已停用");
    return (
      <Space size={4}>
        <Button size="small" loading={toggle.isPending} onClick={() => toggle.mutate(true)}>
          启用
        </Button>
        <Button size="small" danger onClick={() => setDeleteOpen(true)}>
          删除
        </Button>
        <Modal
          open={deleteOpen}
          title={`删除模板「${template.name}」？`}
          width={560}
          okText="确认删除"
          cancelText="取消"
          okButtonProps={{ danger: true }}
          confirmLoading={remove.isPending}
          onOk={() => remove.mutate()}
          onCancel={() => {
            if (!remove.isPending) setDeleteOpen(false);
          }}
        >
          <p className="small muted" style={{ marginTop: 0 }}>
            删除后不可恢复，模板不会再出现在列表与快速启动中。
          </p>
          <Alert
            type="warning"
            showIcon
            message="删除只影响模板本身"
            description="该模板已停用，删除不会影响已启动的评测记录与轨迹；之后也不能再用它一键启动。"
          />
          <Descriptions
            size="small"
            column={1}
            bordered
            style={{ marginTop: 14 }}
            labelStyle={{ width: 96 }}
          >
            <Descriptions.Item label="固定组合">
              {template.dataset_name ?? "—"} {template.dataset_version ?? ""} ·{" "}
              {template.runtime_key} ·{" "}
              {template.model_display_name ?? template.model_name ?? "—"}
            </Descriptions.Item>
            <Descriptions.Item label="累计启动">
              {template.launch_count} 次 · 历史评测保留
            </Descriptions.Item>
          </Descriptions>
        </Modal>
      </Space>
    );
  }

  return (
    <Space size={4}>
      {canManage && allowDisable ? (
        <Popconfirm
          title="停用这个模板？"
          description="停用后不能一键启动，已启动的评测不受影响。"
          okText="停用"
          cancelText="取消"
          onConfirm={() => toggle.mutate(false)}
        >
          <Button size="small" loading={toggle.isPending}>
            停用
          </Button>
        </Popconfirm>
      ) : null}
      {launchButton(!canLaunch)}
    </Space>
  );
}
