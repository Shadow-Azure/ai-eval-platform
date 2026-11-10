import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Empty,
  Form,
  Input,
  Segmented,
  Select,
  Space,
  Steps,
  Table,
  Tag,
  message,
} from "antd";
import type { TableColumnsType } from "antd";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  fetchAssetSources,
  fetchAssetTree,
  resolveAgentAssets,
  type AssetSnapshot,
  type AssetSource,
  type AssetType,
  type ScannedAsset,
} from "../api/agentAssets";
import { fetchDatasetCases, fetchDatasetVersion, fetchDatasetVersions, fetchDatasets } from "../api/datasets";
import {
  createEvaluation,
  prepareEvaluation,
  type PrepareResponse,
} from "../api/evaluations";
import { fetchModelConfigs } from "../api/models";
import { fetchRunners, fetchRuntimes } from "../api/runtimes";
import { Icon } from "../components/Icon";
import { PageHeading } from "../components/PageHeading";

function assetTypeTag(type: AssetType) {
  if (type === "command") return <Tag color="purple">Command</Tag>;
  if (type === "subagent") return <Tag color="green">Subagent</Tag>;
  return <Tag color="blue">Skill</Tag>;
}

function sourceOption(source: AssetSource): string {
  return `${source.name} · ${source.repo_url.replace("https://github.com/", "")}`;
}

export function EvalWizardPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [step, setStep] = useState(0);
  const [datasetId, setDatasetId] = useState<string | null>(null);
  const [versionId, setVersionId] = useState<string | null>(searchParams.get("datasetVersionId"));
  const [modelConfigId, setModelConfigId] = useState<string | null>(null);
  const [runtimeKey, setRuntimeKey] = useState("claude-code");
  const [poolId, setPoolId] = useState<string | null>(null);
  const [executionDigest, setExecutionDigest] = useState("");
  const [scoringDigest, setScoringDigest] = useState("");
  const [preflight, setPreflight] = useState<PrepareResponse | null>(null);
  const [preflightError, setPreflightError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  const [assetSourceId, setAssetSourceId] = useState<string | null>(searchParams.get("assetSourceId"));
  const [assetRef, setAssetRef] = useState("");
  const [selectedPaths, setSelectedPaths] = useState<string[]>([]);
  const [assetFilter, setAssetFilter] = useState<"all" | AssetType>("all");
  const [snapshot, setSnapshot] = useState<AssetSnapshot | null>(null);

  const datasets = useQuery({ queryKey: ["datasets"], queryFn: ({ signal }) => fetchDatasets(signal) });
  const versions = useQuery({
    queryKey: ["dataset", datasetId, "versions"],
    queryFn: ({ signal }) => fetchDatasetVersions(datasetId ?? "", signal),
    enabled: Boolean(datasetId),
  });
  const versionDetail = useQuery({
    queryKey: ["dataset-version", versionId],
    queryFn: ({ signal }) => fetchDatasetVersion(versionId ?? "", signal),
    enabled: Boolean(versionId),
  });
  const cases = useQuery({
    queryKey: ["dataset-version", versionId, "cases"],
    queryFn: ({ signal }) => fetchDatasetCases(versionId ?? "", signal),
    enabled: Boolean(versionId),
  });
  const models = useQuery({ queryKey: ["model-configs"], queryFn: ({ signal }) => fetchModelConfigs(signal) });
  const runtimes = useQuery({ queryKey: ["runtimes"], queryFn: ({ signal }) => fetchRuntimes(signal) });
  const runners = useQuery({ queryKey: ["runners"], queryFn: ({ signal }) => fetchRunners(signal) });
  const sources = useQuery({
    queryKey: ["asset-sources"],
    queryFn: ({ signal }) => fetchAssetSources(signal),
  });
  const assetTree = useQuery({
    queryKey: ["asset-tree", assetSourceId],
    queryFn: ({ signal }) => fetchAssetTree(assetSourceId ?? "", signal),
    enabled: Boolean(assetSourceId),
  });

  const runtime = useMemo(
    () => runtimes.data?.find((item) => item.key === runtimeKey) ?? runtimes.data?.[0],
    [runtimeKey, runtimes.data],
  );
  const poolIds = useMemo(() => {
    const ids = new Set<string>();
    (runners.data ?? []).filter((runner) => runner.status === "online" || runner.status === "busy").forEach((runner) => ids.add(runner.resource_pool_id));
    return Array.from(ids);
  }, [runners.data]);
  const selectedSource = sources.data?.find((source) => source.id === assetSourceId) ?? null;

  useEffect(() => {
    if (!poolId && poolIds.length === 1) setPoolId(poolIds[0]);
  }, [poolId, poolIds]);
  useEffect(() => {
    if (runtime) setRuntimeKey(runtime.key);
  }, [runtime]);
  useEffect(() => {
    if (!versionDetail.data) return;
    setExecutionDigest(
      versionDetail.data.execution_image_digest || runtime?.execution_image_digest || "",
    );
    setScoringDigest(
      versionDetail.data.scoring_image_digest || runtime?.scoring_image_digest || "",
    );
  }, [versionDetail.data, runtime]);
  useEffect(() => {
    if (versionDetail.data) setDatasetId(versionDetail.data.dataset_id);
  }, [versionDetail.data]);
  useEffect(() => {
    if (!modelConfigId && models.data?.length) setModelConfigId(models.data[0].id);
  }, [modelConfigId, models.data]);
  useEffect(() => {
    if (!datasetId && datasets.data?.length) setDatasetId(datasets.data[0].id);
  }, [datasetId, datasets.data]);
  useEffect(() => {
    if (!versionId && versions.data?.length) setVersionId(versions.data[0].id);
  }, [versionId, versions.data]);

  // Pick a default asset source once the catalog loads (deep link wins).
  const defaultSourceApplied = useRef(false);
  useEffect(() => {
    if (defaultSourceApplied.current || !sources.data?.length) return;
    const preferred =
      sources.data.find((source) => source.id === assetSourceId) ?? sources.data[0];
    defaultSourceApplied.current = true;
    setAssetSourceId(preferred.id);
    setAssetRef(preferred.default_ref);
  }, [assetSourceId, sources.data]);

  // Auto-select every classified asset once per source/tree load. Clearing all
  // afterwards is a deliberate user action and must not snap back.
  useEffect(() => {
    if (!assetTree.data) return;
    setSelectedPaths((current) => {
      if (current.length) return current;
      if (selectedSource?.selection_mode !== "directory") return [];
      return assetTree.data!.assets.map((asset) => asset.path);
    });
    // A background refetch with the same commit must not invalidate a valid
    // resolution; a moved Ref clears it so the operator resolves again.
    setSnapshot((current) => {
      if (!current) return current;
      const latest = assetTree.data?.resolved_commit;
      return latest && current.resolved_commit !== latest ? null : current;
    });
  }, [assetTree.data, selectedSource?.selection_mode]);

  function selectAssetSource(sourceId: string) {
    const source = sources.data?.find((item) => item.id === sourceId);
    setAssetSourceId(sourceId);
    setAssetRef(source?.default_ref ?? "");
    setSelectedPaths([]);
    setSnapshot(null);
    setAssetFilter("all");
  }

  function toggleAsset(path: string, checked: boolean) {
    setSelectedPaths((current) =>
      checked ? [...current, path] : current.filter((item) => item !== path),
    );
    setSnapshot(null);
  }

  const filteredAssets = useMemo(() => {
    const assets = assetTree.data?.assets ?? [];
    return assetFilter === "all" ? assets : assets.filter((asset) => asset.asset_type === assetFilter);
  }, [assetFilter, assetTree.data]);

  const resolve = useMutation({
    mutationFn: () =>
      resolveAgentAssets({
        source_id: assetSourceId ?? "",
        requested_ref: assetRef || selectedSource?.default_ref,
        paths: selectedPaths,
        runtime_key: runtimeKey,
      }),
    onSuccess: (result) => {
      setSnapshot(result.snapshot);
      message.success(`已冻结 ${result.snapshot.asset_count} 个资产到 @${result.snapshot.resolved_commit.slice(0, 8)}`);
    },
    onError: (error) => message.error(error instanceof Error ? error.message : "资产解析失败"),
  });

  async function runPreflight() {
    if (!versionId || !modelConfigId || !poolId) {
      message.warning("先选择数据集版本、模型配置和资源池");
      return;
    }
    if (!snapshot) {
      message.warning("先解析 Agent 资产快照");
      return;
    }
    setChecking(true);
    setPreflightError(null);
    try {
      const result = await prepareEvaluation({
        dataset_version_id: versionId,
        model_config_id: modelConfigId,
        runtime_key: runtimeKey,
        resource_pool_id: poolId,
        requested_concurrency: 1,
        scoring_image_digest: scoringDigest || undefined,
        execution_image_digest: executionDigest || undefined,
        asset_snapshot_id: snapshot.id,
      });
      setPreflight(result);
      setStep(3);
    } catch (error) {
      setPreflightError(error instanceof Error ? error.message : "预检失败");
    } finally {
      setChecking(false);
    }
  }

  const create = useMutation({
    mutationFn: () =>
      createEvaluation({
        name: `真实评测 · ${versionDetail.data?.version ?? ""}`,
        dataset_version_id: versionId ?? undefined,
        model_config_id: modelConfigId ?? undefined,
        runtime_key: runtimeKey,
        resource_pool_id: poolId ?? undefined,
        scoring_image_digest: scoringDigest || undefined,
        execution_image_digest: executionDigest || undefined,
        asset_snapshot_id: snapshot?.id,
        idempotency_key: crypto.randomUUID(),
      }),
    onSuccess: (run) => {
      message.success("评测已创建");
      navigate(`/evals/${run.id}`);
    },
    onError: (error) => message.error(error instanceof Error ? error.message : "创建失败"),
  });

  const selectedModel = models.data?.find((item) => item.id === modelConfigId);
  const counts = assetTree.data?.counts ?? { skill: 0, command: 0, subagent: 0 };

  const assetColumns: TableColumnsType<ScannedAsset> = [
    {
      title: "类型",
      dataIndex: "asset_type",
      width: 110,
      render: (value: AssetType) => assetTypeTag(value),
    },
    {
      title: "资产",
      dataIndex: "name",
      render: (value: string, record) => (
        <div>
          <div style={{ fontWeight: 600 }}>{value}</div>
          <div className="mono small muted">{record.path}</div>
        </div>
      ),
    },
    {
      title: "前置依赖",
      dataIndex: ["metadata", "prerequisites"],
      width: 170,
      render: (value: string[] | undefined) =>
        value && value.length ? <Tag color="warning">{value.join(" · ")}</Tag> : <span className="muted">—</span>,
    },
  ];

  return (
    <section className="page">
      <PageHeading
        eyebrow="Evaluation · Launch"
        title="发起真实评测"
        description="冻结数据集版本、Agent 资产、模型、Runtime 与资源池；一致性检查阻塞项会在创建前展示。"
      />
      <Steps
        current={step}
        items={[
          { title: "数据集" },
          { title: "Agent 资产" },
          { title: "模型与资源" },
          { title: "一致性检查" },
        ]}
        style={{ marginBottom: 24 }}
      />
      <Card>
        {step === 0 ? (
          <Space direction="vertical" size="large" style={{ width: "100%" }}>
            <Form layout="vertical">
              <Form.Item label="数据集">
                <Select
                  value={datasetId ?? undefined}
                  onChange={(value) => { setDatasetId(value); setVersionId(null); }}
                  options={(datasets.data ?? []).map((item) => ({ value: item.id, label: item.name }))}
                  placeholder="选择数据集"
                  style={{ maxWidth: 420 }}
                />
              </Form.Item>
              <Form.Item label="版本">
                <Select
                  value={versionId ?? undefined}
                  onChange={setVersionId}
                  options={(versions.data ?? []).map((item) => ({
                    value: item.id,
                    label: `${item.version} · ${item.case_count} cases · ${item.source_revision.slice(0, 8)}`,
                  }))}
                  placeholder="选择版本"
                  style={{ maxWidth: 560 }}
                />
              </Form.Item>
            </Form>
            {versionDetail.data ? (
              <Descriptions size="small" column={3}>
                <Descriptions.Item label="来源">{versionDetail.data.source_uri || "—"}</Descriptions.Item>
                <Descriptions.Item label="用例">{versionDetail.data.case_count}</Descriptions.Item>
                <Descriptions.Item label="状态"><Tag>{versionDetail.data.status}</Tag></Descriptions.Item>
              </Descriptions>
            ) : null}
            {cases.data?.length ? <Alert type="info" showIcon message={`将选取 ${cases.data.length} 个用例（当前支持全量）`} /> : null}
            <Button type="primary" disabled={!versionId} onClick={() => setStep(1)}>下一步</Button>
          </Space>
        ) : null}
        {step === 1 ? (
          <Space direction="vertical" size="large" style={{ width: "100%" }}>
            {!sources.isLoading && !(sources.data ?? []).length ? (
              <Alert
                type="warning"
                showIcon
                message="还没有 Agent 资产源"
                description="先到 Agent 资产页添加 Git 资产源并扫描，再回到这里选择本次评测使用的资产。"
                action={<Button size="small" onClick={() => navigate("/assets")}>去添加</Button>}
              />
            ) : null}
            <div className="asset-runtime-panel">
              <div className="asset-runtime-meta">
                <span className="mono small">
                  {selectedSource ? selectedSource.repo_url.replace("https://github.com/", "") : "选择 Git 资产源"}
                </span>
                <span className="tag tag-primary">{counts.skill} Skill</span>
                <span className="tag tag-violet">{counts.command} Command</span>
                <span className="tag tag-success">{counts.subagent} Subagent</span>
              </div>
              <Form layout="vertical">
                <Form.Item label="资产源">
                  <Select
                    value={assetSourceId ?? undefined}
                    onChange={selectAssetSource}
                    options={(sources.data ?? []).map((source) => ({ value: source.id, label: sourceOption(source) }))}
                    placeholder="选择 Git 资产源"
                    style={{ maxWidth: 620 }}
                  />
                </Form.Item>
              </Form>
              <div className="asset-ref-row">
                <Form layout="vertical" style={{ flex: 1 }}>
                  <Form.Item label="评测 Ref" extra={selectedSource ? `默认 ${selectedSource.default_ref}；修改后必须重新解析` : undefined}>
                    <Input
                      className="mono"
                      value={assetRef}
                      onChange={(event) => { setAssetRef(event.target.value); setSnapshot(null); }}
                      placeholder="分支、tag 或 commit"
                    />
                  </Form.Item>
                </Form>
                <div className="asset-commit-box">
                  <span className="mono">
                    {snapshot ? `@${snapshot.resolved_commit}` : "尚未解析当前 Ref"}
                  </span>
                  <Button
                    onClick={() => resolve.mutate()}
                    loading={resolve.isPending}
                    disabled={!assetSourceId || !selectedPaths.length}
                  >
                    <Icon name="refresh" size={13} /> 读取资产
                  </Button>
                </div>
              </div>
              <Alert
                type={snapshot ? "success" : "warning"}
                showIcon
                message={
                  snapshot
                    ? `已从 ${snapshot.requested_ref} 冻结 ${snapshot.asset_count} 项资产；评测将固定到 commit ${snapshot.resolved_commit.slice(0, 12)}。`
                    : "Ref 尚未解析；未解析或选择为空不能进入下一步。"
                }
              />
            </div>
            <div>
              <div className="row-between" style={{ marginBottom: 10 }}>
                <div>
                  <strong>选择资产</strong>
                  <div className="small muted">已选择 {selectedPaths.length} / {assetTree.data?.assets.length ?? 0} 项</div>
                </div>
                <Segmented
                  value={assetFilter}
                  onChange={(value) => setAssetFilter(value as "all" | AssetType)}
                  options={[
                    { value: "all", label: `全部 ${assetTree.data?.assets.length ?? 0}` },
                    { value: "skill", label: `Skill ${counts.skill}` },
                    { value: "command", label: `Command ${counts.command}` },
                    { value: "subagent", label: `Subagent ${counts.subagent}` },
                  ]}
                />
              </div>
              {assetTree.isLoading ? <Card loading /> : filteredAssets.length ? (
                <Table<ScannedAsset>
                  rowKey={(record) => record.path}
                  size="small"
                  columns={assetColumns}
                  dataSource={filteredAssets}
                  pagination={false}
                  rowSelection={{
                    selectedRowKeys: selectedPaths,
                    onChange: (keys) => {
                      setSelectedPaths(keys.map(String));
                      setSnapshot(null);
                    },
                  }}
                  onRow={(record) => ({
                    onClick: (event) => {
                      const target = event.target as HTMLElement;
                      if (target.closest(".ant-checkbox-wrapper")) return;
                      toggleAsset(record.path, !selectedPaths.includes(record.path));
                    },
                    style: { cursor: "pointer" },
                  })}
                />
              ) : (
                <Empty
                  description={selectedSource ? "该资产源还没有扫描结果" : "先选择资产源"}
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                >
                  <Button onClick={() => navigate("/assets")}>去扫描资产源</Button>
                </Empty>
              )}
            </div>
            {selectedPaths.some((path) => assetTree.data?.assets.find((asset) => asset.path === path)?.metadata.prerequisites?.length) ? (
              <Alert
                type="warning"
                showIcon
                message="所选资产包含运行时前置依赖"
                description="OpenSpec 生成型 Command 需要目标沙箱提供 openspec CLI；快照会记录前置依赖，但不会静默授予宿主机执行权限。"
              />
            ) : null}
            <Space>
              <Button onClick={() => setStep(0)}>上一步</Button>
              <Button type="primary" disabled={!snapshot} onClick={() => setStep(2)}>下一步</Button>
            </Space>
          </Space>
        ) : null}
        {step === 2 ? (
          <Space direction="vertical" size="large" style={{ width: "100%" }}>
            <Form layout="vertical">
              <Form.Item label="模型配置">
                <Select
                  value={modelConfigId ?? undefined}
                  onChange={setModelConfigId}
                  options={(models.data ?? []).map((item) => ({
                    value: item.id,
                    label: `${item.display_name} · ${item.model_name} · ${item.api_style}${
                      item.credential?.revoked_at ? " · 凭据已撤销" : ""
                    }`,
                  }))}
                  placeholder="选择模型配置"
                  style={{ maxWidth: 560 }}
                />
              </Form.Item>
              {selectedModel?.credential?.revoked_at ? (
                <Alert
                  type="warning"
                  showIcon
                  message="所选模型配置的凭据已撤销"
                  description="一致性检查会把 credential_unavailable 列为阻塞项；请先在「模型凭据」页替换 Key 后再发起评测。"
                />
              ) : null}
              <Form.Item label="Runtime">
                <Select
                  value={runtimeKey}
                  onChange={(value) => { setRuntimeKey(value); setSnapshot(null); }}
                  options={(runtimes.data ?? []).map((item) => ({ value: item.key, label: `${item.key} ${item.version}` }))}
                  style={{ maxWidth: 360 }}
                />
              </Form.Item>
              <Form.Item label="资源池">
                <Select
                  value={poolId ?? undefined}
                  onChange={setPoolId}
                  options={poolIds.map((id) => ({ value: id, label: id }))}
                  placeholder="从在线 Runner 推导"
                  style={{ maxWidth: 420 }}
                />
              </Form.Item>
              <Form.Item label="执行镜像 digest" extra="默认来自数据集版本或平台配置；可覆盖，仅本次评测使用">
                <Input value={executionDigest} onChange={(event) => setExecutionDigest(event.target.value)} placeholder="sha256:…" style={{ maxWidth: 560 }} />
              </Form.Item>
              <Form.Item label="评分镜像 digest" extra="默认来自数据集版本或平台配置；可覆盖，仅本次评测使用">
                <Input value={scoringDigest} onChange={(event) => setScoringDigest(event.target.value)} placeholder="sha256:…" style={{ maxWidth: 560 }} />
              </Form.Item>
            </Form>
            <Space>
              <Button onClick={() => setStep(1)}>上一步</Button>
              <Button type="primary" loading={checking} onClick={runPreflight}>运行一致性检查</Button>
            </Space>
            {preflightError ? <Alert type="error" showIcon message={preflightError} /> : null}
          </Space>
        ) : null}
        {step === 3 && preflight ? (
          <Space direction="vertical" size="large" style={{ width: "100%" }}>
            <Alert
              type={preflight.ready ? "success" : "error"}
              showIcon
              message={preflight.ready ? "一致性检查通过" : `存在 ${preflight.blockers.length} 个阻塞项`}
            />
            {preflight.blockers.map((issue) => (
              <Alert key={issue.code} type="error" showIcon message={issue.code} description={issue.message} />
            ))}
            {preflight.warnings.map((issue) => (
              <Alert key={issue.code} type="warning" showIcon message={issue.code} description={issue.message} />
            ))}
            <Descriptions size="small" column={2}>
              <Descriptions.Item label="版本">{versionDetail.data?.version ?? versionId}</Descriptions.Item>
              <Descriptions.Item label="模型">{selectedModel?.display_name ?? modelConfigId}</Descriptions.Item>
              <Descriptions.Item label="Runtime">{runtime?.key} {runtime?.version}</Descriptions.Item>
              <Descriptions.Item label="资源池">{poolId}</Descriptions.Item>
              <Descriptions.Item label="资产 commit">
                <span className="mono">{snapshot?.resolved_commit ?? "—"}</span>
              </Descriptions.Item>
              <Descriptions.Item label="资产数量">{snapshot?.asset_count ?? 0}</Descriptions.Item>
            </Descriptions>
            <Space>
              <Button onClick={() => setStep(2)}>上一步</Button>
              <Button type="primary" disabled={!preflight.ready} loading={create.isPending} onClick={() => create.mutate()}>
                创建评测
              </Button>
            </Space>
          </Space>
        ) : null}
      </Card>
    </section>
  );
}
