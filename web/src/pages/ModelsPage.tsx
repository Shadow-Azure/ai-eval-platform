import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  AutoComplete,
  Button,
  Checkbox,
  Descriptions,
  Drawer,
  Dropdown,
  Form,
  Input,
  InputNumber,
  Modal,
  Radio,
  Select,
  Skeleton,
  Tag,
  Tooltip,
  message,
} from "antd";
import type { FormInstance, MenuProps } from "antd";
import {
  createModelConfig,
  fetchCredentials,
  fetchModelConfigs,
  revokeCredential,
  rotateCredential,
  testModelConfig,
  updateModelConfig,
  type CredentialSummary,
  type ModelConfig,
  type ModelConfigPatch,
} from "../api/models";
import { ApiError } from "../api/types";
import { useAuth } from "../app/auth";
import { EmptyState } from "../components/EmptyState";
import { Icon } from "../components/Icon";
import { PageHeading } from "../components/PageHeading";
import { QueryError } from "../components/QueryError";
import { formatMoney, formatUnitPrice } from "../utils/money";

const PROVIDER_OPTIONS = [
  { value: "openai", label: "OpenAI" },
  { value: "deepseek", label: "DeepSeek" },
  { value: "minimax", label: "MiniMax" },
  { value: "mimo", label: "小米 MiMo" },
  { value: "glm", label: "智谱 GLM" },
  { value: "kimi", label: "Kimi" },
];

const PROVIDER_LABELS: Record<string, string> = Object.fromEntries(
  PROVIDER_OPTIONS.map((item) => [item.value, item.label]),
);

const PROVIDER_INITIALS: Record<string, string> = {
  openai: "OA",
  deepseek: "DS",
  minimax: "MM",
  mimo: "MI",
  glm: "GL",
  kimi: "KM",
};

const API_STYLE_OPTIONS = [
  { value: "openai", label: "OpenAI 兼容（chat/completions）" },
  { value: "anthropic", label: "Anthropic 兼容（messages）" },
];

interface TestResult {
  ok: boolean;
  at: number;
  detail: string;
  code: string | null;
  fields: Record<string, string>;
}

interface ModelFormValues {
  provider: string;
  display_name: string;
  base_url: string;
  model_name: string;
  api_style: string;
  input_price?: number;
  output_price?: number;
  credential_mode?: "new" | "existing";
  credential_id?: string;
  secret?: string;
  test_after_save?: boolean;
}

interface RotateFormValues {
  secret: string;
}

function providerLabel(provider: string): string {
  return PROVIDER_LABELS[provider] ?? provider;
}

function providerInitials(provider: string): string {
  return PROVIDER_INITIALS[provider] ?? provider.slice(0, 2).toUpperCase();
}

function providerClass(provider: string): string {
  return PROVIDER_INITIALS[provider] ? `provider-${provider}` : "provider-custom";
}

function apiStyleLabel(style: string): string {
  if (style === "openai") return "OpenAI";
  if (style === "anthropic") return "Anthropic";
  return style;
}

const FIELD_LABELS: Record<string, string> = {
  base_url: "Base URL",
  secret: "API Key",
  model_name: "模型名",
  display_name: "显示名称",
  api_style: "API 协议",
  credential_id: "凭据",
  provider: "服务商",
};

function fieldLabel(field: string): string {
  return FIELD_LABELS[field] ?? field;
}

function errorMessage(error: unknown): string {
  return error instanceof Error && error.message
    ? error.message
    : "请求控制面时发生未知错误。";
}

function readPrice(pricing: Record<string, unknown>, key: string): number | undefined {
  const value = pricing[key];
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function pricingText(pricing: Record<string, unknown>): string | null {
  const input = readPrice(pricing, "input_per_1m");
  const output = readPrice(pricing, "output_per_1m");
  if (input === undefined && output === undefined) return null;
  const parts: string[] = [];
  if (input !== undefined) parts.push(`入 ${formatUnitPrice(input)}/M`);
  if (output !== undefined) parts.push(`出 ${formatUnitPrice(output)}/M`);
  return parts.join(" · ");
}

function formatDateTime(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString("zh-CN");
}

function formatRelative(value: string | null): string {
  if (!value) return "尚未使用";
  const time = new Date(value).getTime();
  if (Number.isNaN(time)) return "尚未使用";
  const diff = Date.now() - time;
  if (diff < 60_000) return "刚刚使用";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前使用`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前使用`;
  return `${Math.floor(diff / 86_400_000)} 天前使用`;
}

function credentialState(config: ModelConfig): "revoked" | "temporary" | "active" {
  if (config.credential?.revoked_at) return "revoked";
  if (config.is_temporary) return "temporary";
  return "active";
}

function stateTag(config: ModelConfig) {
  if (!config.credential) return <span className="tag tag-warning">缺少凭据</span>;
  const state = credentialState(config);
  if (state === "revoked") return <span className="tag tag-danger">已撤销</span>;
  if (state === "temporary") return <span className="tag tag-primary">临时配置</span>;
  return <span className="tag tag-success">正常</span>;
}

function applyServerError(
  error: unknown,
  form: FormInstance<ModelFormValues>,
  setError: (value: { message: string; code: string | null } | null) => void,
): void {
  if (error instanceof ApiError) {
    const entries = Object.entries(error.fields);
    if (entries.length) {
      form.setFields(
        entries.map(([name, text]) => ({
          name: name as keyof ModelFormValues,
          errors: [text],
        })),
      );
    }
    setError({ message: error.message, code: error.code });
    return;
  }
  setError({ message: errorMessage(error), code: null });
}

function ModelConfigDrawer({
  open,
  mode,
  config,
  credentials,
  onClose,
  onSaved,
}: {
  open: boolean;
  mode: "create" | "edit";
  config: ModelConfig | null;
  credentials: CredentialSummary[];
  onClose: () => void;
  onSaved: (saved: ModelConfig, testAfterSave: boolean) => Promise<void>;
}) {
  const [form] = Form.useForm<ModelFormValues>();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<{ message: string; code: string | null } | null>(null);
  const credentialMode = Form.useWatch("credential_mode", form) ?? "new";

  useEffect(() => {
    if (!open) return;
    setError(null);
    form.resetFields();
    if (mode === "edit" && config) {
      form.setFieldsValue({
        provider: config.provider,
        display_name: config.display_name,
        base_url: config.base_url,
        model_name: config.model_name,
        api_style: config.api_style,
        input_price: readPrice(config.pricing, "input_per_1m"),
        output_price: readPrice(config.pricing, "output_per_1m"),
        credential_id: config.credential_id ?? undefined,
        test_after_save: true,
      });
    } else {
      form.setFieldsValue({
        provider: "openai",
        api_style: "openai",
        credential_mode: "new",
        test_after_save: true,
      });
    }
  }, [config, form, mode, open]);

  function close(): void {
    form.resetFields();
    onClose();
  }

  async function submit(): Promise<void> {
    let values: ModelFormValues;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const pricing: Record<string, number> = {};
      if (typeof values.input_price === "number") {
        pricing.input_per_1m = values.input_price;
      }
      if (typeof values.output_price === "number") {
        pricing.output_per_1m = values.output_price;
      }
      let saved: ModelConfig;
      if (mode === "create") {
        const base = {
          provider: values.provider.trim(),
          display_name: values.display_name.trim(),
          base_url: values.base_url.trim(),
          model_name: values.model_name.trim(),
          api_style: values.api_style,
          pricing,
        };
        saved =
          values.credential_mode === "existing"
            ? await createModelConfig({ ...base, credential_id: values.credential_id })
            : await createModelConfig({ ...base, secret: values.secret });
      } else {
        if (!config) return;
        const patch: ModelConfigPatch = {
          provider: values.provider.trim(),
          display_name: values.display_name.trim(),
          base_url: values.base_url.trim(),
          model_name: values.model_name.trim(),
          api_style: values.api_style,
          pricing,
        };
        if (values.credential_id && values.credential_id !== config.credential_id) {
          patch.credential_id = values.credential_id;
        }
        saved = await updateModelConfig(config.id, patch);
      }
      form.resetFields();
      await onSaved(saved, values.test_after_save !== false);
    } catch (caught) {
      applyServerError(caught, form, setError);
    } finally {
      setSaving(false);
    }
  }

  const credentialOptions = credentials.map((item) => ({
    value: item.id,
    disabled: Boolean(item.revoked_at) && item.id !== config?.credential_id,
    label: `${providerLabel(item.provider)} · ${item.secret_mask}${
      item.revoked_at ? " · 已撤销" : ""
    }`,
  }));

  return (
    <Drawer
      title={mode === "create" ? "添加模型凭据" : "编辑模型配置"}
      width={540}
      open={open}
      onClose={close}
      maskClosable={!saving}
      footer={
        <div className="drawer-footer">
          <Button onClick={close} disabled={saving}>
            取消
          </Button>
          <Button type="primary" loading={saving} onClick={() => void submit()}>
            {mode === "create" ? "验证并保存" : "保存配置"}
          </Button>
        </div>
      }
    >
      {error ? (
        <Alert
          type="error"
          showIcon
          message={error.message}
          description={error.code ? <span className="mono">{error.code}</span> : null}
          style={{ marginBottom: 16 }}
        />
      ) : null}
      <Form form={form} layout="vertical">
        <Form.Item
          name="provider"
          label="服务商"
          rules={[{ required: true, message: "请选择或输入服务商" }]}
        >
          <AutoComplete
            options={PROVIDER_OPTIONS}
            placeholder="deepseek / openai / minimax"
            filterOption={(input, option) =>
              String(option?.label ?? "").toLowerCase().includes(input.toLowerCase()) ||
              String(option?.value ?? "").toLowerCase().includes(input.toLowerCase())
            }
          >
            <Input />
          </AutoComplete>
        </Form.Item>
        <Form.Item
          name="display_name"
          label="显示名称"
          rules={[{ required: true, message: "请输入显示名称" }]}
        >
          <Input placeholder="DeepSeek Production" maxLength={200} />
        </Form.Item>
        <Form.Item
          name="base_url"
          label="Base URL"
          rules={[
            { required: true, message: "请输入 Base URL" },
            { pattern: /^https?:\/\//, message: "必须以 http:// 或 https:// 开头" },
          ]}
          extra="平台会在该地址后追加 /models 做连通性测试，并按所选协议拼接模型请求。"
        >
          <Input className="mono" placeholder="https://api.deepseek.com/v1" maxLength={500} />
        </Form.Item>
        <Form.Item
          name="model_name"
          label="模型名"
          rules={[{ required: true, message: "请输入模型名" }]}
        >
          <Input placeholder="deepseek-chat" maxLength={200} />
        </Form.Item>
        <Form.Item
          name="api_style"
          label="API 协议"
          rules={[{ required: true, message: "请选择 API 协议" }]}
        >
          <Select options={API_STYLE_OPTIONS} />
        </Form.Item>
        <div className="field-row">
          <Form.Item name="input_price" label="输入价格（CNY / 1M tokens）">
            <InputNumber min={0} step={0.1} style={{ width: "100%" }} placeholder="0" />
          </Form.Item>
          <Form.Item name="output_price" label="输出价格（CNY / 1M tokens）">
            <InputNumber min={0} step={0.1} style={{ width: "100%" }} placeholder="0" />
          </Form.Item>
        </div>
        {mode === "create" ? (
          <>
            <Form.Item name="credential_mode" label="API Key 来源">
              <Radio.Group>
                <Radio.Button value="new">本次新建凭据</Radio.Button>
                <Radio.Button value="existing">使用已有凭据</Radio.Button>
              </Radio.Group>
            </Form.Item>
            {credentialMode === "existing" ? (
              <Form.Item
                name="credential_id"
                label="已有凭据"
                rules={[{ required: true, message: "请选择已有凭据" }]}
                extra="仅可选择未撤销的模型凭据。"
              >
                <Select
                  options={credentialOptions.filter((item) => !item.disabled)}
                  placeholder="选择凭据"
                />
              </Form.Item>
            ) : (
              <Form.Item
                name="secret"
                label="API Key"
                rules={[{ required: true, message: "请输入 API Key" }]}
                extra="只写不读：不会进入 URL、本地存储或查询缓存。"
              >
                <Input.Password
                  className="mono"
                  autoComplete="new-password"
                  placeholder="sk-…"
                  maxLength={4096}
                />
              </Form.Item>
            )}
          </>
        ) : (
          <Form.Item
            name="credential_id"
            label="绑定凭据"
            extra="默认保持当前凭据；选择其他凭据会改变后续评测使用的 Key。"
          >
            <Select allowClear options={credentialOptions} placeholder="保持当前凭据" />
          </Form.Item>
        )}
        {mode === "edit" && config?.credential?.revoked_at ? (
          <Alert
            type="warning"
            showIcon
            message="当前凭据已撤销"
            description="先在列表中执行「替换 Key」恢复凭据，或在此改绑其他凭据。"
            style={{ marginBottom: 16 }}
          />
        ) : null}
        <Form.Item name="test_after_save" valuePropName="checked">
          <Checkbox>保存后测试连通性</Checkbox>
        </Form.Item>
        <div className="banner" style={{ marginTop: 4 }}>
          <Icon name="lock" size={16} />
          <div>
            <strong>凭据边界</strong> · Key 加密存储，页面与 API 只回显掩码；评测由平台换发短期凭据，Runner
            不保存长期 Key。
          </div>
        </div>
      </Form>
    </Drawer>
  );
}

export function ModelsPage() {
  const { session } = useAuth();
  const isAdmin = session?.role === "admin";
  const queryClient = useQueryClient();
  const configsQuery = useQuery({
    queryKey: ["model-configs"],
    queryFn: ({ signal }) => fetchModelConfigs(signal),
  });
  const credentialsQuery = useQuery({
    queryKey: ["model-credentials", "model"],
    queryFn: ({ signal }) => fetchCredentials(signal),
    enabled: isAdmin,
  });

  const [search, setSearch] = useState("");
  const [drawer, setDrawer] = useState<{ mode: "create" | "edit"; config: ModelConfig | null } | null>(
    null,
  );
  const [detailId, setDetailId] = useState<string | null>(null);
  const [rotateId, setRotateId] = useState<string | null>(null);
  const [revokeId, setRevokeId] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, TestResult>>({});
  const [diagnostic, setDiagnostic] = useState<{ config: ModelConfig; result: TestResult } | null>(
    null,
  );
  const [testingId, setTestingId] = useState<string | null>(null);
  const [testingAll, setTestingAll] = useState(false);
  const [rotating, setRotating] = useState(false);
  const [revoking, setRevoking] = useState(false);
  const [rotateError, setRotateError] = useState<{ message: string; code: string | null } | null>(
    null,
  );
  const [rotateForm] = Form.useForm<RotateFormValues>();

  const configs = useMemo(
    () => (configsQuery.data ?? []).filter((item) => !item.is_temporary),
    [configsQuery.data],
  );
  const credentials = credentialsQuery.data ?? [];
  const available = configs.filter(
    (item) => item.credential && !item.credential.revoked_at,
  ).length;
  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) return configs;
    return configs.filter((item) =>
      `${item.display_name} ${providerLabel(item.provider)} ${item.provider} ${item.model_name}`
        .toLowerCase()
        .includes(needle),
    );
  }, [configs, search]);

  const detailConfig = configs.find((item) => item.id === detailId) ?? null;
  const rotateConfig = configs.find((item) => item.id === rotateId) ?? null;
  const revokeConfig = configs.find((item) => item.id === revokeId) ?? null;

  async function refresh(): Promise<void> {
    await queryClient.invalidateQueries({ queryKey: ["model-configs"] });
    await queryClient.invalidateQueries({ queryKey: ["model-credentials", "model"] });
  }

  async function performTest(config: ModelConfig): Promise<TestResult> {
    let result: TestResult;
    try {
      const response = await testModelConfig(config.id);
      result = {
        ok: true,
        at: Date.now(),
        detail: response.model_count !== null ? `${response.model_count} 个模型可用` : "端点可达",
        code: null,
        fields: {},
      };
    } catch (caught) {
      result = {
        ok: false,
        at: Date.now(),
        detail: errorMessage(caught),
        code: caught instanceof ApiError ? caught.code : null,
        fields: caught instanceof ApiError ? caught.fields : {},
      };
    }
    setTestResults((previous) => ({ ...previous, [config.id]: result }));
    return result;
  }

  async function testOne(config: ModelConfig): Promise<void> {
    setTestingId(config.id);
    try {
      const result = await performTest(config);
      if (result.ok) {
        message.success(`「${config.display_name}」连通性验证通过`);
      } else {
        setDiagnostic({ config, result });
        message.error(`「${config.display_name}」连通性验证失败，请检查端点或 Key`);
      }
    } finally {
      setTestingId(null);
    }
  }

  async function testAll(): Promise<void> {
    const targets = configs.filter((item) => item.credential && !item.credential.revoked_at);
    if (!targets.length) {
      message.info("没有可验证的模型配置");
      return;
    }
    setTestingAll(true);
    let passed = 0;
    try {
      for (const config of targets) {
        const result = await performTest(config);
        if (result.ok) passed += 1;
      }
    } finally {
      setTestingAll(false);
    }
    const failed = targets.length - passed;
    if (failed) {
      message.warning(`${passed} 个通过，${failed} 个失败`);
    } else {
      message.success(`${passed} 个模型端点全部验证通过`);
    }
  }

  async function handleSaved(saved: ModelConfig, testAfterSave: boolean): Promise<void> {
    const wasCreate = drawer?.mode === "create";
    setDrawer(null);
    await refresh();
    message.success(wasCreate ? "模型配置已创建" : "模型配置已更新");
    if (!testAfterSave) return;
    const result = await performTest(saved);
    if (result.ok) {
      message.success(`「${saved.display_name}」连通性验证通过`);
    } else {
      setDiagnostic({ config: saved, result });
      message.warning(`「${saved.display_name}」已保存，但连通性测试失败，请检查端点或 Key`);
    }
  }

  async function rotateSecret(): Promise<void> {
    if (!rotateConfig?.credential) return;
    let values: RotateFormValues;
    try {
      values = await rotateForm.validateFields();
    } catch {
      return;
    }
    setRotating(true);
    setRotateError(null);
    try {
      await rotateCredential(rotateConfig.credential.id, values.secret);
      rotateForm.resetFields();
      setRotateId(null);
      await refresh();
      message.success("Key 已轮换，旧 Key 立即失效");
      const result = await performTest(rotateConfig);
      if (result.ok) message.success("新 Key 连通性验证通过");
      else {
        setDiagnostic({ config: rotateConfig, result });
        message.warning("新 Key 已保存，但连通性测试失败，请检查端点");
      }
    } catch (caught) {
      if (caught instanceof ApiError) {
        const entries = Object.entries(caught.fields);
        if (entries.length) {
          rotateForm.setFields(
            entries.map(([name, text]) => ({
              name: name as keyof RotateFormValues,
              errors: [text],
            })),
          );
        }
        setRotateError({ message: caught.message, code: caught.code });
      } else {
        setRotateError({ message: errorMessage(caught), code: null });
      }
    } finally {
      setRotating(false);
    }
  }

  async function revoke(): Promise<void> {
    if (!revokeConfig?.credential) return;
    setRevoking(true);
    try {
      await revokeCredential(revokeConfig.credential.id);
      setRevokeId(null);
      await refresh();
      message.success("凭据已撤销，后续预检将阻塞该模型配置");
    } catch (caught) {
      message.error(errorMessage(caught));
    } finally {
      setRevoking(false);
    }
  }

  function menuFor(config: ModelConfig): MenuProps {
    const items: MenuProps["items"] = [{ key: "detail", label: "查看详情" }];
    if (isAdmin) {
      items.push(
        { key: "edit", label: "编辑配置" },
        { key: "test", label: "测试连通性" },
        { type: "divider" },
        {
          key: "revoke",
          label: "撤销凭据",
          danger: true,
          disabled: !config.credential || Boolean(config.credential.revoked_at),
        },
      );
    }
    return {
      items,
      onClick: ({ key }) => {
        if (key === "detail") setDetailId(config.id);
        else if (key === "edit") setDrawer({ mode: "edit", config });
        else if (key === "test") void testOne(config);
        else if (key === "revoke") {
          setRevokeId(config.id);
        }
      },
    };
  }

  function footerStatus(config: ModelConfig) {
    const result = testResults[config.id];
    if (result) {
      return (
        <Tooltip title={`${result.detail}${result.code ? ` · ${result.code}` : ""}`}>
          <span className="status-inline">
            <span className={`status-dot ${result.ok ? "is-ok" : "is-failed"}`} />
            <span className="small muted">{result.ok ? "刚刚验证通过" : "验证失败"}</span>
          </span>
        </Tooltip>
      );
    }
    if (!config.credential) {
      return (
        <>
          <span className="status-dot is-failed" />
          <span className="small muted">未绑定凭据</span>
        </>
      );
    }
    if (config.credential.revoked_at) {
      return (
        <>
          <span className="status-dot is-failed" />
          <span className="small muted">凭据已撤销</span>
        </>
      );
    }
    return (
      <>
        <span className="status-dot is-ok" />
        <span className="small muted">{formatRelative(config.last_used_at)}</span>
      </>
    );
  }

  return (
    <section className="page">
      <PageHeading
        eyebrow="Configuration · Model credentials"
        title="模型凭据"
        description="统一管理服务商端点、模型配置与加密凭据；Key 只写不读，页面仅显示掩码。"
        actions={
          isAdmin ? (
            <Button
              type="primary"
              icon={<Icon name="plus" size={14} />}
              onClick={() => setDrawer({ mode: "create", config: null })}
            >
              添加模型凭据
            </Button>
          ) : (
            <span className="tag tag-neutral">
              <Icon name="lock" size={11} /> 只读
            </span>
          )
        }
      />

      <div className="banner models-banner">
        <Icon name="lock" size={16} />
        <div>
          <strong>凭据边界</strong> · Runner 不保存长期 API Key。平台只在派发时解密并换发短期凭据，结束后失效。
          {!isAdmin ? " 只读角色可以查看配置与掩码，但不能新增、替换、撤销凭据或执行连通性测试。" : null}
        </div>
      </div>

      <section className="card">
        <div className="card-header models-toolbar">
          <Input
            className="models-search"
            allowClear
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="搜索服务商或模型"
            prefix={<Icon name="search" size={14} />}
            aria-label="搜索服务商或模型"
          />
          <div className="card-actions">
            {configs.length ? (
              <span className={`tag ${available === configs.length ? "tag-success" : "tag-warning"}`}>
                {available} / {configs.length} 可用
              </span>
            ) : null}
            {isAdmin && configs.length ? (
              <Button
                size="small"
                icon={<Icon name="refresh" size={13} />}
                loading={testingAll}
                onClick={() => void testAll()}
              >
                全部验证
              </Button>
            ) : null}
          </div>
        </div>
        <div className="card-body">
          {configsQuery.isError ? (
            <QueryError
              error={configsQuery.error}
              onRetry={() => void configsQuery.refetch()}
              isRetrying={configsQuery.isFetching}
              title="无法加载模型凭据"
              endpoint="GET /api/v1/model-configs"
            />
          ) : configsQuery.isLoading ? (
            <div className="credential-grid" aria-busy="true">
              {[0, 1, 2].map((item) => (
                <div className="card credential-card" key={item}>
                  <Skeleton active paragraph={{ rows: 3 }} />
                </div>
              ))}
            </div>
          ) : !configs.length ? (
            <EmptyState
              icon="key"
              title="还没有模型配置"
              description="添加服务商端点与加密凭据后，发起评测即可直接选择，不再依赖 seed 或手工接口。"
              action={
                isAdmin ? (
                  <Button
                    type="primary"
                    onClick={() => setDrawer({ mode: "create", config: null })}
                  >
                    添加模型凭据
                  </Button>
                ) : undefined
              }
            />
          ) : !filtered.length ? (
            <EmptyState
              icon="search"
              title="没有匹配的凭据"
              description="换一个服务商名称或模型名试试。"
              action={<Button onClick={() => setSearch("")}>清除搜索</Button>}
            />
          ) : (
            <div className="credential-grid">
              {filtered.map((config) => {
                const price = pricingText(config.pricing);
                return (
                  <article className="card credential-card" key={config.id}>
                    <div className="row-between">
                      <div className="credential-identity">
                        <div className={`provider-logo ${providerClass(config.provider)}`}>
                          {providerInitials(config.provider)}
                        </div>
                        <div>
                          <div className="credential-name">{config.display_name}</div>
                          <div className="small muted truncate">{config.model_name}</div>
                        </div>
                      </div>
                      {stateTag(config)}
                    </div>
                    <div className="credential-meta">
                      <span className="tag tag-neutral">{providerLabel(config.provider)}</span>
                      <span className="tag tag-primary">{apiStyleLabel(config.api_style)}</span>
                      {price ? <span className="tag tag-neutral">{price}</span> : null}
                    </div>
                    <div className="credential-endpoint">
                      <div className="small muted">API 端点</div>
                      <div className="mono small truncate" title={config.base_url}>
                        {config.base_url}
                      </div>
                    </div>
                    <div className={`masked-key${config.credential?.revoked_at ? " is-revoked" : ""}`}>
                      {config.credential?.secret_mask ?? "未绑定凭据"}
                    </div>
                    <div className="credential-footer">
                      {footerStatus(config)}
                      {testingId === config.id ? (
                        <span className="small muted">验证中…</span>
                      ) : null}
                      <div className="credential-actions">
                        {isAdmin ? (
                          <Button
                            type="link"
                            size="small"
                            disabled={!config.credential}
                            onClick={() => setRotateId(config.id)}
                          >
                            替换 Key
                          </Button>
                        ) : null}
                        <Dropdown menu={menuFor(config)} trigger={["click"]}>
                          <Button
                            type="text"
                            size="small"
                            aria-label={`更多操作：${config.display_name}`}
                            icon={<Icon name="more" size={14} />}
                          />
                        </Dropdown>
                      </div>
                    </div>
                  </article>
                );
              })}
            </div>
          )}
        </div>
      </section>

      <ModelConfigDrawer
        open={drawer !== null}
        mode={drawer?.mode ?? "create"}
        config={drawer?.config ?? null}
        credentials={credentials}
        onClose={() => setDrawer(null)}
        onSaved={handleSaved}
      />

      <Drawer
        title={detailConfig?.display_name ?? "模型配置详情"}
        width={520}
        open={detailConfig !== null}
        onClose={() => setDetailId(null)}
        extra={detailConfig ? stateTag(detailConfig) : null}
      >
        {detailConfig ? (
          <Descriptions column={1} size="small" bordered>
            <Descriptions.Item label="服务商">
              {providerLabel(detailConfig.provider)}
            </Descriptions.Item>
            <Descriptions.Item label="模型名">{detailConfig.model_name}</Descriptions.Item>
            <Descriptions.Item label="API 协议">{apiStyleLabel(detailConfig.api_style)}</Descriptions.Item>
            <Descriptions.Item label="端点">
              <span className="mono">{detailConfig.base_url}</span>
            </Descriptions.Item>
            <Descriptions.Item label="凭据">
              <span className="mono">
                {detailConfig.credential?.secret_mask ?? "未绑定凭据"}
              </span>
            </Descriptions.Item>
            <Descriptions.Item label="凭据状态">
              {!detailConfig.credential ? (
                <Tag>未绑定</Tag>
              ) : detailConfig.credential.revoked_at ? (
                <Tag color="red">已撤销</Tag>
              ) : (
                <Tag color="green">可用</Tag>
              )}
            </Descriptions.Item>
            <Descriptions.Item label="价格">
              {pricingText(detailConfig.pricing) ?? "未配置"}
            </Descriptions.Item>
            <Descriptions.Item label="最近轮换">
              {formatDateTime(detailConfig.credential?.last_rotated_at ?? null)}
            </Descriptions.Item>
            <Descriptions.Item label="最近使用">
              {formatDateTime(detailConfig.last_used_at)}
            </Descriptions.Item>
            <Descriptions.Item label="累计请求">
              {detailConfig.usage.requests}
            </Descriptions.Item>
            <Descriptions.Item label="累计 Token">
              {detailConfig.usage.total_tokens}
            </Descriptions.Item>
            <Descriptions.Item label="累计成本">
              <span className="mono">{formatMoney(detailConfig.usage.cost_cny)}</span>
            </Descriptions.Item>
            <Descriptions.Item label="限制">
              <span className="mono small">
                {Object.keys(detailConfig.limits).length
                  ? JSON.stringify(detailConfig.limits)
                  : "未配置"}
              </span>
            </Descriptions.Item>
            <Descriptions.Item label="创建时间">
              {formatDateTime(detailConfig.created_at)}
            </Descriptions.Item>
          </Descriptions>
        ) : null}
      </Drawer>

      <Modal
        title={rotateConfig ? `替换「${rotateConfig.display_name}」的 API Key` : "替换 API Key"}
        open={rotateConfig !== null}
        okText="验证并替换"
        cancelText="取消"
        confirmLoading={rotating}
        onOk={() => void rotateSecret()}
        onCancel={() => {
          if (rotating) return;
          rotateForm.resetFields();
          setRotateError(null);
          setRotateId(null);
        }}
        afterClose={() => {
          rotateForm.resetFields();
          setRotateError(null);
        }}
      >
        <p className="small">
          当前掩码 <span className="mono">{rotateConfig?.credential?.secret_mask ?? "—"}</span>
          。旧 Key 会在新 Key 验证后失效；正在运行的评测使用短期凭据，不受影响。
        </p>
        {rotateError ? (
          <Alert
            type="error"
            showIcon
            message={rotateError.message}
            description={rotateError.code ? <span className="mono">{rotateError.code}</span> : null}
            style={{ marginBottom: 12 }}
          />
        ) : null}
        <Form form={rotateForm} layout="vertical" onFinish={() => void rotateSecret()}>
          <Form.Item
            name="secret"
            label="新 API Key"
            rules={[{ required: true, message: "请输入新的 API Key" }]}
          >
            <Input.Password
              className="mono"
              autoComplete="new-password"
              placeholder="sk-…"
              maxLength={4096}
            />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={revokeConfig ? `撤销「${revokeConfig.display_name}」的凭据？` : "撤销凭据"}
        open={revokeConfig !== null}
        okText="确认撤销"
        okButtonProps={{ danger: true }}
        cancelText="取消"
        confirmLoading={revoking}
        onOk={() => void revoke()}
        onCancel={() => {
          if (!revoking) setRevokeId(null);
        }}
      >
        <Alert
          type="warning"
          showIcon
          message="撤销后不能再用此凭据发起评测"
          description="历史评测仍保留模型名与用量快照；正在运行的任务使用短期凭据，不受影响。撤销后可在列表中替换 Key 恢复。"
        />
        <p className="small muted" style={{ marginTop: 12 }}>
          当前掩码 <span className="mono">{revokeConfig?.credential?.secret_mask ?? "—"}</span>
        </p>
      </Modal>

      <Modal
        title={
          diagnostic
            ? `连通性测试失败 · ${diagnostic.config.display_name}`
            : "连通性测试失败"
        }
        open={diagnostic !== null}
        onCancel={() => setDiagnostic(null)}
        footer={<Button onClick={() => setDiagnostic(null)}>关闭</Button>}
        width={560}
      >
        {diagnostic ? (
          <>
            <Alert
              type="error"
              showIcon
              message={diagnostic.result.detail}
              description={
                diagnostic.result.code ? (
                  <span className="mono">{diagnostic.result.code}</span>
                ) : null
              }
            />
            {Object.keys(diagnostic.result.fields).length ? (
              <div className="diagnostic-fields">
                {Object.entries(diagnostic.result.fields).map(([field, text]) => (
                  <div className="diagnostic-field" key={field}>
                    <span className="diagnostic-field-label">{fieldLabel(field)}</span>
                    <span>{text}</span>
                  </div>
                ))}
              </div>
            ) : null}
            <p className="small muted" style={{ marginTop: 12 }}>
              检查 Base URL 是否可访问、API Key 是否有效，以及该模型名是否对当前 Key 开放；
              修改后可在卡片菜单中选择「测试连通性」重新验证。
            </p>
          </>
        ) : null}
      </Modal>
    </section>
  );
}
