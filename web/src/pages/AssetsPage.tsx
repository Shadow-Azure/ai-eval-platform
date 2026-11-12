import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Empty,
  Input,
  Modal,
  Segmented,
  Select,
  Space,
  Table,
  Tag,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useNavigate } from "react-router-dom";
import {
  createAssetSource,
  fetchAssetSource,
  fetchAssetSnapshot,
  fetchAssetSources,
  hasRuntimePrerequisites,
  isGeneratedAsset,
  isLoadTestPassed,
  loadTestAssetSource,
  previewAssetLoadTest,
  previewAssetScan,
  resolveAgentAssets,
  scanAssetSource,
  type AssetLoadTest,
  type AssetLoadTestPreview,
  type AssetSource,
  type AssetType,
  type ScannedAsset,
} from "../api/agentAssets";
import { Icon } from "../components/Icon";
import { PageHeading } from "../components/PageHeading";
import { QueryError } from "../components/QueryError";

const TYPE_LABELS: Record<AssetType, string> = {
  skill: "Skill",
  command: "Command",
  subagent: "Subagent",
};

const TYPE_TAG_CLASS: Record<AssetType, string> = {
  skill: "tag tag-primary",
  command: "tag tag-violet",
  subagent: "tag tag-success",
};

function statusTag(status: string) {
  if (status === "ready") return <Tag color="success">可用</Tag>;
  if (status === "requires_setup") return <Tag color="warning">需运行时准备</Tag>;
  if (status === "error") return <Tag color="error">扫描失败</Tag>;
  return <Tag>未扫描</Tag>;
}

function countAssets(assets: ScannedAsset[]): Record<AssetType, number> {
  const counts: Record<AssetType, number> = { skill: 0, command: 0, subagent: 0 };
  for (const asset of assets) counts[asset.asset_type] += 1;
  return counts;
}

function loadTestStatusTag(loadTest: AssetLoadTest | null | undefined) {
  if (loadTest?.status === "passed") return <Tag color="success">加载测试通过</Tag>;
  if (loadTest?.status === "testing") return <Tag color="processing">测试中…</Tag>;
  if (loadTest?.status === "failed") return <Tag color="error">加载测试失败</Tag>;
  return <Tag color="warning">未测试</Tag>;
}

/** "4 个 Skill / 4 个 Command / 5 个 Agent" (Agent omitted when zero). */
function generatedCountsLabel(loadTest: AssetLoadTest): string {
  const { skill, command, subagent } = loadTest.generated;
  const parts = [`${skill} 个 Skill`, `${command} 个 Command`];
  if (subagent) parts.push(`${subagent} 个 Agent`);
  return parts.join(" / ");
}

function generatedSummary(loadTest: AssetLoadTest): string {
  const duration =
    loadTest.duration_seconds !== null && Number.isFinite(loadTest.duration_seconds)
      ? ` · ${loadTest.duration_seconds}s`
      : "";
  return `已生成 ${generatedCountsLabel(loadTest)}${duration}；结果随快照冻结`;
}

function tailLog(value: string | null): string {
  if (!value) return "";
  return value.length > 1600 ? `…${value.slice(-1600)}` : value;
}

function shortDigest(value: string | null | undefined): string {
  if (!value) return "—";
  return value.replace(/^sha256:/, "").slice(0, 12);
}

function repoName(repoUrl: string): string {
  const cleaned = repoUrl.replace(/\.git$/, "").replace(/\/$/, "");
  return cleaned.split("/").filter(Boolean).at(-1) ?? "Agent Assets";
}

function scanPathsOf(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function runtimeRequirements(assets: ScannedAsset[] | undefined): string[] {
  const seen = new Set<string>();
  for (const asset of assets ?? []) {
    for (const item of asset.metadata.prerequisites ?? []) seen.add(item);
  }
  return [...seen].sort();
}

function generatedCommands(assets: ScannedAsset[] | undefined): ScannedAsset[] {
  return (assets ?? []).filter(
    (asset) => asset.asset_type === "command" && (asset.metadata.prerequisites?.length ?? 0) > 0,
  );
}

function RuntimeRequirementNote({
  assets,
  detail = false,
}: {
  assets: ScannedAsset[] | undefined;
  detail?: boolean;
}) {
  const requirements = runtimeRequirements(assets);
  const generated = generatedCommands(assets);
  if (!requirements.length && !generated.length) return null;
  return (
    <div
      className={`banner warning asset-requirement${detail ? " asset-requirement-detail" : ""}`}
      role="status"
    >
      <Icon name="alert" size={15} />
      <div>
        <strong>运行时前置缺失</strong>
        {requirements.length ? <> · 执行前需要 {requirements.join(" · ")}。</> : null}
        {generated.length
          ? ` 其中 ${generated.length} 个 Command 是生成型资产，启动前需由目标 Runtime adapter 生成并完成项目初始化。`
          : " 依赖该 CLI 的生成型 Command 需在启动前由目标 Runtime adapter 生成并完成项目初始化。"}
      </div>
    </div>
  );
}

function RuntimePrerequisiteBlock({
  source,
  onTest,
  loading,
  detail = false,
}: {
  source: AssetSource;
  onTest: () => void;
  loading: boolean;
  detail?: boolean;
}) {
  const loadTest = source.load_test;
  const status = loadTest?.status ?? "untested";
  return (
    <div className={`asset-runtime${detail ? " asset-runtime-detail" : ""}`}>
      <div className="asset-runtime-head">
        <strong>运行时前置</strong>
        {loadTestStatusTag(loadTest)}
      </div>
      {source.runtime_prerequisites.install_command ? (
        <div className="mono small muted">{source.runtime_prerequisites.install_command}</div>
      ) : null}
      {source.runtime_prerequisites.init_command ? (
        <div className="mono small muted">{source.runtime_prerequisites.init_command}</div>
      ) : null}
      {status === "passed" ? (
        <div className="asset-runtime-result small muted">{generatedSummary(loadTest)}</div>
      ) : status === "testing" ? (
        <div className="asset-runtime-result small muted">
          在隔离环境安装 CLI 并执行初始化，再扫描生成资产…
        </div>
      ) : status === "failed" ? (
        <div className="asset-runtime-error">
          <strong>加载测试失败</strong>
          {loadTest.log_tail ? (
            <pre className="asset-runtime-log">{tailLog(loadTest.log_tail)}</pre>
          ) : (
            <span className="small muted">未返回日志；请检查安装/初始化命令后重试。</span>
          )}
        </div>
      ) : (
        <div className="asset-runtime-result small muted">
          未测试：加载测试通过后才能用于评测。
        </div>
      )}
      <div>
        <Button
          size="small"
          icon={<Icon name="terminal" size={13} />}
          loading={loading}
          onClick={onTest}
        >
          {status === "passed" ? "重新测试" : "加载测试"}
        </Button>
      </div>
    </div>
  );
}

function useLoadTestMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (sourceId: string) => loadTestAssetSource(sourceId),
    onSuccess: ({ source, loadTest }, sourceId) => {
      if (source) queryClient.setQueryData(["asset-source", sourceId], source);
      queryClient.invalidateQueries({ queryKey: ["asset-sources"] });
      queryClient.invalidateQueries({ queryKey: ["asset-source", sourceId] });
      if (loadTest.status === "passed") {
        message.success(`加载测试通过：生成 ${generatedCountsLabel(loadTest)}`);
      } else if (loadTest.status === "failed") {
        message.error("加载测试失败：查看运行时前置日志");
      } else {
        message.info("加载测试完成，但未返回结果状态");
      }
    },
    onError: (error) => message.error(error instanceof Error ? error.message : "加载测试失败"),
  });
}

function metric(label: string, value: number | string, hint: string, tone?: string) {
  return (
    <Card className="asset-metric" key={label}>
      <div className={`asset-metric-value ${tone ?? ""}`}>{value}</div>
      <div className="asset-metric-label">{label}</div>
      <div className="asset-metric-hint">{hint}</div>
    </Card>
  );
}

function assetColumns(showDigest = true): ColumnsType<ScannedAsset> {
  const columns: ColumnsType<ScannedAsset> = [
    {
      title: "类型",
      dataIndex: "asset_type",
      width: 108,
      render: (value: AssetType) => <span className={TYPE_TAG_CLASS[value]}>{TYPE_LABELS[value]}</span>,
    },
    {
      title: "资产",
      dataIndex: "name",
      render: (value: string, record) => (
        <div>
          <div className="asset-name">{value}</div>
          <div className="mono small muted">{record.path}</div>
        </div>
      ),
    },
    {
      title: "来源",
      key: "origin",
      width: 96,
      render: (_value: unknown, record: ScannedAsset) =>
        isGeneratedAsset(record) ? <Tag color="orange">生成</Tag> : <Tag>源文件</Tag>,
    },
    {
      title: "运行时映射",
      dataIndex: ["metadata", "runtime_path"],
      width: 190,
      render: (value: string | undefined) => <span className="mono small">{value ?? "—"}</span>,
    },
    {
      title: "前置依赖",
      dataIndex: ["metadata", "prerequisites"],
      width: 150,
      render: (value: string[] | undefined) =>
        value && value.length ? <Tag color="warning">{value.join(" · ")}</Tag> : <span className="muted">—</span>,
    },
  ];
  if (showDigest) {
    columns.push({
      title: "内容摘要",
      dataIndex: "content_digest",
      width: 150,
      render: (value: string) => <span className="mono small">{shortDigest(value)}</span>,
    });
  }
  return columns;
}

interface AddModalProps {
  open: boolean;
  onClose: () => void;
  onCreated: (sourceId: string) => void;
}

function AddAssetSourceModal({ open, onClose, onCreated }: AddModalProps) {
  const [repoUrl, setRepoUrl] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [defaultRef, setDefaultRef] = useState("main");
  const [scanPath, setScanPath] = useState("skills, .claude");
  const [selectionMode, setSelectionMode] = useState<"directory" | "explicit">("directory");
  const [description, setDescription] = useState("");
  const [accessToken, setAccessToken] = useState("");
  const [runtimeEnabled, setRuntimeEnabled] = useState(false);
  const [installCommand, setInstallCommand] = useState("");
  const [initCommand, setInitCommand] = useState("");
  const [loadTesting, setLoadTesting] = useState(false);
  const [loadTestError, setLoadTestError] = useState<string | null>(null);
  const [loadTest, setLoadTest] = useState<{
    result: AssetLoadTestPreview;
    signature: string;
  } | null>(null);
  const [scan, setScan] = useState<Awaited<ReturnType<typeof previewAssetScan>> | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [manualTypes, setManualTypes] = useState<Record<string, AssetType>>({});
  const [filter, setFilter] = useState<"all" | AssetType>("all");
  const [scanning, setScanning] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    setScan(null);
    setSelected([]);
    setManualTypes({});
    setFilter("all");
    setScanning(false);
    setSaving(false);
    setRuntimeEnabled(false);
    setInstallCommand("");
    setInitCommand("");
    setLoadTesting(false);
    setLoadTestError(null);
    setLoadTest(null);
  }, [open]);

  const runtimeSignature = useMemo(
    () =>
      JSON.stringify({
        repo: repoUrl.trim(),
        ref: defaultRef.trim() || "main",
        paths: scanPathsOf(scanPath),
        install: installCommand.trim(),
        init: initCommand.trim(),
        token: accessToken.trim(),
      }),
    [accessToken, defaultRef, initCommand, installCommand, repoUrl, scanPath],
  );
  const activeLoadTest = loadTest && loadTest.signature === runtimeSignature ? loadTest.result : null;
  const loadTestPassed = runtimeEnabled && isLoadTestPassed(activeLoadTest);

  const filtered = useMemo(() => {
    const assets = scan?.assets ?? [];
    return filter === "all" ? assets : assets.filter((asset) => asset.asset_type === filter);
  }, [filter, scan]);

  const groups = useMemo(() => {
    const map = new Map<string, ScannedAsset[]>();
    for (const asset of filtered) {
      const parts = asset.path.split("/");
      const directory = parts.slice(0, -1).join("/") || ".";
      const bucket = map.get(directory);
      if (bucket) bucket.push(asset);
      else map.set(directory, [asset]);
    }
    return [...map.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [filtered]);

  function togglePaths(paths: string[], checked: boolean) {
    setSelected((current) =>
      checked ? [...new Set([...current, ...paths])] : current.filter((item) => !paths.includes(item)),
    );
  }

  async function handleScan() {
    const paths = scanPathsOf(scanPath);
    if (!repoUrl.trim()) {
      message.warning("请填写 GitHub 仓库");
      return;
    }
    setScanning(true);
    try {
      const result = await previewAssetScan({
        repo_url: repoUrl.trim(),
        requested_ref: defaultRef.trim() || "main",
        scan_paths: paths,
        access_token: accessToken.trim() || undefined,
      });
      setScan(result);
      // "directory" preselects the whole scan; "explicit" leaves the choice
      // empty so the operator must pick individual paths.
      setSelected(
        selectionMode === "directory" ? result.assets.map((asset) => asset.path) : [],
      );
      setManualTypes({});
      message.success(`扫描完成：发现 ${result.assets.length} 个资产`);
    } catch (error) {
      message.error(error instanceof Error ? error.message : "扫描失败");
    } finally {
      setScanning(false);
    }
  }

  async function handleLoadTest() {
    if (!runtimeEnabled) {
      message.warning("请先启用运行时前置");
      return;
    }
    if (!repoUrl.trim()) {
      message.warning("请填写 GitHub 仓库");
      return;
    }
    if (!installCommand.trim() || !initCommand.trim()) {
      message.warning("请填写安装与初始化命令");
      return;
    }
    const signature = runtimeSignature;
    setLoadTesting(true);
    setLoadTest(null);
    setLoadTestError(null);
    try {
      const result = await previewAssetLoadTest({
        repo_url: repoUrl.trim(),
        requested_ref: defaultRef.trim() || "main",
        scan_paths: scanPathsOf(scanPath),
        runtime_prerequisites: {
          install_command: installCommand.trim(),
          init_command: initCommand.trim(),
        },
        access_token: accessToken.trim() || undefined,
      });
      setLoadTest({ result, signature });
      if (result.status === "passed") {
        // The CLI may generate assets that a static scan cannot see. Seed the
        // result list and selection so "保存资产源" can freeze them.
        const generated = result.assets;
        if (generated.length) {
          setScan((current) => {
            if (!current) return current;
            const seen = new Set(current.assets.map((asset) => `${asset.asset_type}:${asset.path}`));
            const additions = generated.filter(
              (asset) => asset.path && !seen.has(`${asset.asset_type}:${asset.path}`),
            );
            if (!additions.length) return current;
            const assets = [...current.assets, ...additions];
            return { ...current, assets, counts: countAssets(assets) };
          });
          setSelected((current) => [
            ...new Set([...current, ...generated.map((asset) => asset.path).filter(Boolean)]),
          ]);
        }
        message.success(`加载测试通过：生成 ${generatedCountsLabel(result)}`);
      } else if (result.status === "failed") {
        message.error("加载测试失败：请查看日志并修正命令");
      } else {
        message.info("加载测试完成，但未返回结果状态");
      }
    } catch (error) {
      setLoadTest(null);
      setLoadTestError(error instanceof Error ? error.message : "加载测试失败");
      message.error(error instanceof Error ? error.message : "加载测试失败");
    } finally {
      setLoadTesting(false);
    }
  }

  async function handleSave() {
    if (!scan) {
      message.warning("请先扫描仓库");
      return;
    }
    if (!selected.length) {
      message.warning("请至少选择一个资产");
      return;
    }
    if (runtimeEnabled && !loadTestPassed) {
      message.warning("请先通过加载测试");
      return;
    }
    setSaving(true);
    try {
      const source = await createAssetSource({
        name: displayName.trim() || repoName(repoUrl),
        repo_url: repoUrl.trim(),
        default_ref: defaultRef.trim() || "main",
        scan_paths: scanPathsOf(scanPath),
        selection_mode: selectionMode,
        description: description.trim() || undefined,
        access_token: accessToken.trim() || undefined,
        runtime_prerequisites: runtimeEnabled
          ? {
              install_command: installCommand.trim(),
              init_command: initCommand.trim(),
            }
          : undefined,
        load_test_token: runtimeEnabled ? activeLoadTest?.load_test_token ?? undefined : undefined,
      });
      try {
        await resolveAgentAssets({
          source_id: source.id,
          requested_ref: defaultRef.trim() || "main",
          paths: selected,
          types: manualTypes,
        });
        message.success(`已保存 ${selected.length} 个 Agent 资产并冻结首个快照`);
      } catch (error) {
        message.warning(
          `资产源已创建，但初始快照未生成：${error instanceof Error ? error.message : "未知错误"}`,
        );
      }
      onCreated(source.id);
      onClose();
    } catch (error) {
      message.error(error instanceof Error ? error.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  const counts = scan?.counts ?? { skill: 0, command: 0, subagent: 0 };
  const runtimeBlocked = runtimeEnabled && !loadTestPassed;
  const saveDisabled = !scan || !selected.length || runtimeBlocked || loadTesting;

  return (
    <Modal
      open={open}
      onCancel={onClose}
      width={1100}
      title="添加 Git 资产源"
      destroyOnClose
      footer={
        <Space>
          <Button onClick={onClose}>取消</Button>
          <Button icon={<Icon name="refresh" size={14} />} loading={scanning} onClick={handleScan}>
            {scan ? "重新扫描" : "自动扫描"}
          </Button>
          <Button
            type="primary"
            loading={saving}
            disabled={saveDisabled}
            title={runtimeBlocked ? "启用运行时前置后，需先通过加载测试" : undefined}
            onClick={handleSave}
          >
            保存资产源
          </Button>
        </Space>
      }
    >
      <div className="asset-wizard-grid">
        <section>
          <div className="asset-step-title">
            <span>01</span>仓库配置
          </div>
          <div className="asset-field">
            <label>GitHub 仓库</label>
            <Input
              value={repoUrl}
              onChange={(event) => setRepoUrl(event.target.value)}
              placeholder="https://github.com/owner/repo"
            />
            <div className="asset-hint">私有仓库可填写租户级访问令牌；平台不会回显明文。</div>
          </div>
          <div className="asset-field">
            <label>显示名称</label>
            <Input value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder={repoName(repoUrl)} />
          </div>
          <div className="asset-field-row">
            <div className="asset-field">
              <label>默认 Ref</label>
              <Input value={defaultRef} onChange={(event) => setDefaultRef(event.target.value)} />
            </div>
            <div className="asset-field">
              <label>选择范围</label>
              <Select
                value={selectionMode}
                onChange={(value) => setSelectionMode(value)}
                options={[
                  { value: "directory", label: "目录下全部资产" },
                  { value: "explicit", label: "仅指定资产" },
                ]}
              />
            </div>
          </div>
          <div className="asset-field">
            <label>扫描路径</label>
            <Input
              value={scanPath}
              onChange={(event) => setScanPath(event.target.value)}
              placeholder="skills, .claude（逗号分隔；留空扫描整库）"
            />
          </div>
          <div className="asset-field asset-runtime-field">
            <label className="asset-runtime-toggle">
              <Checkbox
                checked={runtimeEnabled}
                onChange={(event) => setRuntimeEnabled(event.target.checked)}
              />
              <span>运行时前置（可选）：需要 CLI 生成命令</span>
            </label>
            <div className="asset-hint">适用于 OpenSpec/CodeSpec 等由 CLI 生成 Command 的仓库。</div>
          </div>
          {runtimeEnabled ? (
            <>
              <div className="asset-field">
                <label>CLI 安装命令</label>
                <Input
                  className="mono"
                  value={installCommand}
                  onChange={(event) => setInstallCommand(event.target.value)}
                  placeholder='npm install --prefix "$HOME" github:owner/repo#ref'
                />
              </div>
              <div className="asset-field">
                <label>初始化 / 生成命令</label>
                <Input
                  className="mono"
                  value={initCommand}
                  onChange={(event) => setInitCommand(event.target.value)}
                  placeholder='"$HOME/node_modules/.bin/openspec" init --tools claude'
                />
              </div>
              <div className="asset-field">
                <Space>
                  <Button
                    size="small"
                    icon={<Icon name="terminal" size={13} />}
                    loading={loadTesting}
                    onClick={handleLoadTest}
                  >
                    {loadTesting ? "测试中…" : loadTestPassed ? "重新测试" : "加载测试"}
                  </Button>
                </Space>
                {loadTesting ? (
                  <div className="asset-scan-hint">
                    <Icon name="refresh" size={14} />
                    <span>在隔离环境安装 CLI 并执行 init，再扫描生成资产…</span>
                  </div>
                ) : activeLoadTest?.status === "passed" ? (
                  <Alert
                    type="success"
                    showIcon
                    style={{ marginTop: 8 }}
                    message="加载测试通过"
                    description={`${generatedSummary(activeLoadTest)}。`}
                  />
                ) : activeLoadTest?.status === "failed" ? (
                  <Alert
                    type="error"
                    showIcon
                    style={{ marginTop: 8 }}
                    message="加载测试失败"
                    description={
                      activeLoadTest.log_tail ? (
                        <pre className="asset-runtime-log">{tailLog(activeLoadTest.log_tail)}</pre>
                      ) : (
                        "未返回日志；请检查安装/初始化命令后重试。"
                      )
                    }
                  />
                ) : loadTestError ? (
                  <Alert
                    type="error"
                    showIcon
                    style={{ marginTop: 8 }}
                    message="加载测试请求失败"
                    description={loadTestError}
                  />
                ) : (
                  <div className="asset-scan-hint">
                    <Icon name="shield" size={14} />
                    <span>在隔离环境执行上面两条命令并扫描生成结果，不写入仓库。</span>
                  </div>
                )}
              </div>
            </>
          ) : null}
          <div className="asset-field">
            <label>说明</label>
            <Input value={description} onChange={(event) => setDescription(event.target.value)} />
          </div>
          <div className="asset-field">
            <label>访问令牌（可选）</label>
            <Input.Password
              value={accessToken}
              onChange={(event) => setAccessToken(event.target.value)}
              placeholder="ghp_…，仅用于读取私有仓库"
            />
          </div>
          <div className="asset-scan-hint">
            <Icon name="lock" size={14} />
            <span>扫描只读取仓库内容，不执行 Skill 脚本；保存后固定解析 commit。</span>
          </div>
        </section>
        <section>
          <div className="asset-step-title">
            <span>02</span>扫描结果
            <em>{scan ? `${selected.length} / ${scan.assets.length} 已选择` : "等待扫描"}</em>
          </div>
          {scanning ? (
            <div className="asset-scan-placeholder">
              <Icon name="refresh" size={22} />
              <strong>正在读取 Git tree 并识别资产…</strong>
              <span>Skill 以 SKILL.md 目录为单位，Command 与 Subagent 按 Runtime 原生格式识别。</span>
            </div>
          ) : scan && scan.assets.length === 0 ? (
            <div className="asset-scan-placeholder">
              <Icon name="alert" size={22} />
              <strong>未发现可识别的 Skill、Command 或 Subagent</strong>
              <span>
                该仓库是 CLI 生成型资产源：填写左侧运行时前置并完成「加载测试」后，生成的 Skill 与
                Command 会出现在这里。
              </span>
              {runtimeEnabled ? (
                <Button type="primary" size="small" loading={loadTesting} onClick={handleLoadTest}>
                  加载测试
                </Button>
              ) : (
                <span className="small muted">先勾选「运行时前置（可选）」并填写安装/初始化命令。</span>
              )}
            </div>
          ) : scan ? (
            <>
              <div className="asset-scan-toolbar">
                <Space size={4}>
                  <span className={TYPE_TAG_CLASS.skill}>Skill {counts.skill}</span>
                  <span className={TYPE_TAG_CLASS.command}>Command {counts.command}</span>
                  <span className={TYPE_TAG_CLASS.subagent}>Subagent {counts.subagent}</span>
                </Space>
                <span className="mono small muted">@{shortDigest(scan.resolved_commit)}</span>
              </div>
              <Segmented
                value={filter}
                onChange={(value) => setFilter(value as "all" | AssetType)}
                options={[
                  { value: "all", label: `全部 ${scan.assets.length}` },
                  { value: "skill", label: `Skill ${counts.skill}` },
                  { value: "command", label: `Command ${counts.command}` },
                  { value: "subagent", label: `Subagent ${counts.subagent}` },
                ]}
                style={{ marginBottom: 10 }}
              />
              <div className="asset-pick-toolbar">
                <span className="small muted">已选择 {selected.length} / {scan.assets.length} 项</span>
                <Space size={4}>
                  <Button size="small" onClick={() => togglePaths(filtered.map((asset) => asset.path), true)}>
                    选择当前筛选
                  </Button>
                  <Button size="small" onClick={() => togglePaths(filtered.map((asset) => asset.path), false)}>
                    取消当前筛选
                  </Button>
                </Space>
              </div>
              <div className="asset-pick-list">
                {filtered.length ? (
                  groups.map(([directory, assets]) => {
                    const paths = assets.map((asset) => asset.path);
                    const allSelected = paths.every((path) => selected.includes(path));
                    const someSelected = paths.some((path) => selected.includes(path));
                    return (
                      <div className="asset-pick-group" key={directory}>
                        <div className="asset-pick-folder">
                          <Checkbox
                            checked={allSelected}
                            indeterminate={!allSelected && someSelected}
                            onChange={(event) => togglePaths(paths, event.target.checked)}
                          />
                          <Icon name="boxes" size={14} />
                          <strong>{directory}/</strong>
                          <span>{assets.length}</span>
                          <Button
                            type="link"
                            size="small"
                            onClick={() => togglePaths(paths, !allSelected)}
                          >
                            {allSelected ? "取消目录" : "选择目录"}
                          </Button>
                        </div>
                        {assets.map((asset) => (
                          <label key={`${asset.asset_type}:${asset.path}`} className="asset-pick-row">
                            <Checkbox
                              checked={selected.includes(asset.path)}
                              onChange={(event) =>
                                setSelected((current) =>
                                  event.target.checked
                                    ? [...current, asset.path]
                                    : current.filter((item) => item !== asset.path),
                                )
                              }
                            />
                            <span className={TYPE_TAG_CLASS[asset.asset_type]}>{TYPE_LABELS[asset.asset_type]}</span>
                            <span className="asset-pick-name">
                              <strong>{asset.name}</strong>
                              <span className="mono">{asset.path}</span>
                            </span>
                            {asset.metadata.prerequisites?.length ? (
                              <Tag color="warning">{asset.metadata.prerequisites.join(" · ")}</Tag>
                            ) : null}
                          </label>
                        ))}
                      </div>
                    );
                  })
                ) : (
                  <Empty description="当前类型下没有资产" />
                )}
              </div>
              {(scan.unclassified ?? []).length ? (
                <div className="asset-unclassified">
                  <div className="asset-unclassified-title">
                    <strong>未识别文件</strong>
                    <span>无法判断类型时必须显式指定后才能进入快照</span>
                  </div>
                  {(scan.unclassified ?? []).slice(0, 12).map((item) => (
                    <div className="asset-unclassified-row" key={item.path}>
                      <span className="mono truncate">{item.path}</span>
                      <Select
                        size="small"
                        value={manualTypes[item.path] ?? "command"}
                        onChange={(value) =>
                          setManualTypes((current) => ({ ...current, [item.path]: value as AssetType }))
                        }
                        options={[
                          { value: "skill", label: "Skill" },
                          { value: "command", label: "Command" },
                          { value: "subagent", label: "Subagent" },
                        ]}
                        style={{ width: 116 }}
                      />
                      <Button
                        size="small"
                        onClick={() => {
                          if (selected.includes(item.path)) return;
                          setManualTypes((current) => ({
                            ...current,
                            [item.path]: current[item.path] ?? "command",
                          }));
                          setSelected((current) => [...current, item.path]);
                        }}
                      >
                        加入
                      </Button>
                    </div>
                  ))}
                </div>
              ) : null}
              {scan.warnings.length ? (
                <Alert type="warning" showIcon message={scan.warnings[0]} style={{ marginTop: 10 }} />
              ) : null}
            </>
          ) : (
            <div className="asset-scan-placeholder">
              <Icon name="search" size={24} />
              <strong>尚未扫描仓库</strong>
              <span>填写仓库、默认 Ref 和扫描路径后点击“自动扫描”。</span>
              <Button type="primary" size="small" loading={scanning} onClick={handleScan}>
                开始扫描
              </Button>
            </div>
          )}
        </section>
      </div>
    </Modal>
  );
}

function AssetSourceDetail({
  sourceId,
  onClose,
  onUse,
}: {
  sourceId: string | null;
  onClose: () => void;
  onUse: (sourceId: string) => void;
}) {
  const queryClient = useQueryClient();
  const sourceQuery = useQuery({
    queryKey: ["asset-source", sourceId],
    queryFn: ({ signal }) => fetchAssetSource(sourceId ?? "", signal),
    enabled: Boolean(sourceId),
  });
  const snapshotQuery = useQuery({
    queryKey: ["asset-snapshot", sourceQuery.data?.latest_snapshot_id],
    queryFn: ({ signal }) => fetchAssetSnapshot(sourceQuery.data?.latest_snapshot_id ?? "", signal),
    enabled: Boolean(sourceQuery.data?.latest_snapshot_id),
  });
  const scan = useMutation({
    mutationFn: () => scanAssetSource(sourceId ?? ""),
    onSuccess: () => {
      message.success("已重新扫描资产源");
      queryClient.invalidateQueries({ queryKey: ["asset-sources"] });
      queryClient.invalidateQueries({ queryKey: ["asset-source", sourceId] });
    },
    onError: (error) => message.error(error instanceof Error ? error.message : "扫描失败"),
  });
  const loadTest = useLoadTestMutation();

  const source = sourceQuery.data;
  const snapshot = snapshotQuery.data;
  const assets: ScannedAsset[] =
    snapshot?.assets ?? source?.last_scan.assets ?? [];
  const counts = snapshot
    ? {
        skill: snapshot.assets.filter((item) => item.asset_type === "skill").length,
        command: snapshot.assets.filter((item) => item.asset_type === "command").length,
        subagent: snapshot.assets.filter((item) => item.asset_type === "subagent").length,
      }
    : source?.counts ?? { skill: 0, command: 0, subagent: 0 };

  return (
    <Modal
      open={Boolean(sourceId)}
      onCancel={onClose}
      width={980}
      destroyOnClose
      title={source?.name ?? "资产源详情"}
      footer={
        <Space>
          <Button onClick={onClose}>关闭</Button>
          <Button icon={<Icon name="refresh" size={14} />} loading={scan.isPending} onClick={() => scan.mutate()}>
            重新扫描
          </Button>
          <Button type="primary" disabled={!source} onClick={() => source && onUse(source.id)}>
            用于发起评测
          </Button>
        </Space>
      }
    >
      {sourceQuery.error ? <QueryError error={sourceQuery.error} onRetry={() => sourceQuery.refetch()} /> : null}
      {source ? (
        <>
          <div className="asset-metric-grid">
            {metric("Git Ref", source.default_ref, source.last_scanned_commit ? `已解析 @${source.last_scanned_commit.slice(0, 8)}` : "尚未解析", "mono")}
            {metric("Skill", counts.skill, "SKILL.md 目录")}
            {metric("Command", counts.command, "Runtime 命令资产")}
            {metric("Subagent", counts.subagent, "Runtime 子 Agent")}
          </div>
          <div className="asset-detail-banner">
            <Icon name="git" size={15} />
            <span className="asset-detail-banner-text">
              {source.repo_url} · 扫描路径 {source.scan_paths.length ? source.scan_paths.join(", ") : "整库"} ·{" "}
              {source.last_scanned_commit ? `已解析 @${source.last_scanned_commit.slice(0, 8)}` : "尚未解析"}
            </span>
            {statusTag(source.status)}
          </div>
          {source && hasRuntimePrerequisites(source.runtime_prerequisites) ? (
            <RuntimePrerequisiteBlock
              source={source}
              detail
              loading={loadTest.isPending && loadTest.variables === source.id}
              onTest={() => loadTest.mutate(source.id)}
            />
          ) : (
            <RuntimeRequirementNote assets={assets} detail />
          )}
          <Card className="asset-detail-card" title="已发现资产" extra={<span className="muted small">{assets.length} 个</span>}>
            <Table<ScannedAsset>
              rowKey={(record) => `${record.asset_type}:${record.path}`}
              columns={assetColumns()}
              dataSource={assets}
              pagination={false}
              size="small"
              locale={{ emptyText: <Empty description="还没有扫描结果" /> }}
            />
          </Card>
          <div className="asset-summary-grid">
            <div>
              <span className="muted small">选择方式</span>
              <div>{source.selection_mode === "directory" ? "扫描路径下全部资产" : "指定资产"}</div>
            </div>
            <div>
              <span className="muted small">Runtime 映射</span>
              <div className="mono small">.claude/skills · .claude/commands · .claude/agents</div>
            </div>
            <div>
              <span className="muted small">最近快照</span>
              <div className="mono small">{snapshot ? shortDigest(snapshot.tree_digest) : "尚未冻结"}</div>
            </div>
            <div>
              <span className="muted small">凭据</span>
              <div className="mono small">{source.credential_mask ?? (source.uses_platform_token ? "平台 GitHub Token" : "匿名读取")}</div>
            </div>
          </div>
          {source.last_scan.warnings?.length ? (
            <Alert type="warning" showIcon message={source.last_scan.warnings[0]} style={{ marginTop: 12 }} />
          ) : null}
        </>
      ) : null}
    </Modal>
  );
}

export function AssetsPage() {
  const navigate = useNavigate();
  const [addOpen, setAddOpen] = useState(false);
  const [detailId, setDetailId] = useState<string | null>(null);
  const [rulesOpen, setRulesOpen] = useState(false);
  const queryClient = useQueryClient();
  const sourcesQuery = useQuery({
    queryKey: ["asset-sources"],
    queryFn: ({ signal }) => fetchAssetSources(signal),
  });
  const sources = sourcesQuery.data ?? [];
  const totals = useMemo(
    () =>
      sources.reduce(
        (sum, source) => ({
          skill: sum.skill + source.counts.skill,
          command: sum.command + source.counts.command,
          subagent: sum.subagent + source.counts.subagent,
        }),
        { skill: 0, command: 0, subagent: 0 },
      ),
    [sources],
  );
  const scan = useMutation({
    mutationFn: (sourceId: string) => scanAssetSource(sourceId),
    onSuccess: () => {
      message.success("已重新扫描资产源");
      queryClient.invalidateQueries({ queryKey: ["asset-sources"] });
    },
    onError: (error) => message.error(error instanceof Error ? error.message : "扫描失败"),
  });
  const loadTest = useLoadTestMutation();

  function useForEvaluation(sourceId: string) {
    setDetailId(null);
    navigate(`/evals/new?assetSourceId=${encodeURIComponent(sourceId)}`);
  }

  return (
    <section className="page">
      <PageHeading
        eyebrow="Configuration · Agent assets"
        title="Agent 资产"
        description="将 Git 仓库中的 Skill、Command 与 Subagent 作为可复用资产源。评测启动时可覆盖默认分支，最终固定在解析后的 commit。"
        actions={
          <Space>
            <Button icon={<Icon name="help" size={14} />} onClick={() => setRulesOpen(true)}>
              发现规则
            </Button>
            <Button type="primary" icon={<Icon name="plus" size={14} />} onClick={() => setAddOpen(true)}>
              添加 Git 资产源
            </Button>
          </Space>
        }
      />
      <Alert
        className="asset-banner"
        type="info"
        showIcon
        icon={<Icon name="git" size={16} />}
        message="Git 是唯一来源，快照才是执行输入。"
        description="平台只读取仓库树并生成不可变资产包；Runner 不需要获得 GitHub 凭据，也不会在执行时重新拉取分支。"
      />
      {sourcesQuery.error ? <QueryError error={sourcesQuery.error} onRetry={() => sourcesQuery.refetch()} /> : null}
      <div className="asset-metric-grid">
        {metric("Git 资产源", sources.length, "默认 Ref 可在启动时覆盖")}
        {metric("Skill", totals.skill, "目录中包含 SKILL.md")}
        {metric("Command", totals.command, "由 Runtime adapter 识别")}
        {metric("Subagent", totals.subagent, "运行时特定格式")}
      </div>
      <div className="asset-source-grid section-gap">
        {sources.map((source) => (
          <Card key={source.id} className="asset-source-card">
            <div className="asset-source-head">
              <div className="asset-source-icon">
                <Icon name="git" size={18} />
              </div>
              <div className="asset-source-title">
                <div className="asset-source-name">{source.name}</div>
                <div className="mono small muted truncate">{source.repo_url.replace("https://github.com/", "")}</div>
              </div>
              {statusTag(source.status)}
            </div>
            <p className="asset-source-description">
              {source.description || "通过自动扫描与手动调整生成的 Git 资产源。"}
            </p>
            <div className="asset-source-meta">
              <div>
                <span>默认 Ref</span>
                <strong className="mono">{source.default_ref}</strong>
              </div>
              <div>
                <span>扫描路径</span>
                <strong className="mono">{source.scan_paths.length ? source.scan_paths.join(", ") : "整库"}</strong>
              </div>
              <div>
                <span>选择范围</span>
                <strong>{source.selection_mode === "directory" ? "目录下全部" : "指定资产"}</strong>
              </div>
            </div>
            <div className="asset-source-tags">
              <span className={TYPE_TAG_CLASS.skill}>Skill {source.counts.skill}</span>
              <span className={TYPE_TAG_CLASS.command}>Command {source.counts.command}</span>
              <span className={TYPE_TAG_CLASS.subagent}>Subagent {source.counts.subagent}</span>
            </div>
            {hasRuntimePrerequisites(source.runtime_prerequisites) ? (
              <RuntimePrerequisiteBlock
                source={source}
                loading={loadTest.isPending && loadTest.variables === source.id}
                onTest={() => loadTest.mutate(source.id)}
              />
            ) : (
              <RuntimeRequirementNote assets={source.last_scan.assets} />
            )}
            <div className="asset-source-note">
              Command 与 Subagent 由所选 Runtime adapter 映射，不保证跨 Agent 格式一致。
            </div>
            <div className="asset-source-footer">
              <span className="mono small muted">
                {source.last_scanned_commit ? `@${source.last_scanned_commit.slice(0, 8)}` : "未解析"}
              </span>
              <Space size={4}>
                <Button size="small" type="text" loading={scan.isPending} onClick={() => scan.mutate(source.id)}>
                  扫描
                </Button>
                <Button size="small" onClick={() => setDetailId(source.id)}>
                  查看资产
                </Button>
              </Space>
            </div>
          </Card>
        ))}
        <button className="asset-source-add" onClick={() => setAddOpen(true)}>
          <Icon name="plus" size={20} />
          <strong>添加 Git 资产源</strong>
          <span>填写仓库、默认分支和扫描路径，再选择全部或单个资产。</span>
        </button>
      </div>
      <Card className="section-gap" title="资产发现规则" extra={<Tag color="blue">Adapter 驱动</Tag>}>
        <div className="asset-rule-grid">
          <div>
            <span className="asset-rule-icon">
              <Icon name="sparkles" size={16} />
            </span>
            <div>
              <strong>Skill</strong>
              <p>递归发现包含 <span className="inline-code">SKILL.md</span> 的目录，解析名称、描述、依赖和安全扫描结果。</p>
            </div>
          </div>
          <div>
            <span className="asset-rule-icon">
              <Icon name="terminal" size={16} />
            </span>
            <div>
              <strong>Command</strong>
              <p>由 Claude Code、Codex、OpenCode、pi 的 adapter 识别；无法统一识别的格式要求用户明确配置。</p>
            </div>
          </div>
          <div>
            <span className="asset-rule-icon">
              <Icon name="users" size={16} />
            </span>
            <div>
              <strong>Subagent</strong>
              <p>读取 Runtime 原生 Agent 定义，并在注入前检查模型、工具权限和名称冲突。</p>
            </div>
          </div>
        </div>
      </Card>
      <AddAssetSourceModal
        open={addOpen}
        onClose={() => setAddOpen(false)}
        onCreated={() => queryClient.invalidateQueries({ queryKey: ["asset-sources"] })}
      />
      <AssetSourceDetail sourceId={detailId} onClose={() => setDetailId(null)} onUse={useForEvaluation} />
      <Modal
        open={rulesOpen}
        onCancel={() => setRulesOpen(false)}
        footer={<Button type="primary" onClick={() => setRulesOpen(false)}>关闭</Button>}
        title="资产发现与解析规则"
        width={760}
      >
        <div className="asset-rule-grid">
          <div>
            <span className="asset-rule-icon"><Icon name="sparkles" size={16} /></span>
            <div>
              <strong>Skill</strong>
              <p>目录必须包含 SKILL.md；以目录为单位映射到 <span className="inline-code">.claude/skills</span>，并校验路径和文件预算。</p>
            </div>
          </div>
          <div>
            <span className="asset-rule-icon"><Icon name="terminal" size={16} /></span>
            <div>
              <strong>Command</strong>
              <p>识别 <span className="inline-code">commands/**/*.md</span> 与 <span className="inline-code">.claude/commands</span>；OpenSpec 生成型命令会标记运行时前置依赖。</p>
            </div>
          </div>
          <div>
            <span className="asset-rule-icon"><Icon name="users" size={16} /></span>
            <div>
              <strong>Subagent</strong>
              <p>识别 <span className="inline-code">agents/**/*.md</span> 与 <span className="inline-code">.claude/agents</span>；不支持的类型会显式降级而不是静默忽略。</p>
            </div>
          </div>
        </div>
        <Alert
          type="warning"
          showIcon
          style={{ marginTop: 16 }}
          message="无法判断类型时必须显式选择"
          description="平台不会仅凭文件扩展名猜测 Command 或 Subagent；未识别文件要在选择时手动指定类型。"
        />
      </Modal>
    </section>
  );
}
