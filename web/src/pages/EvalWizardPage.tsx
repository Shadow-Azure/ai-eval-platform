import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Alert, Button, Card, Descriptions, Form, Input, Select, Space, Steps, Tag, message } from "antd";
import { useNavigate, useSearchParams } from "react-router-dom";
import { fetchDatasetCases, fetchDatasetVersion, fetchDatasetVersions, fetchDatasets } from "../api/datasets";
import {
  createEvaluation,
  prepareEvaluation,
  type PrepareResponse,
} from "../api/evaluations";
import { fetchModelConfigs } from "../api/models";
import { fetchRunners, fetchRuntimes } from "../api/runtimes";
import { PageHeading } from "../components/PageHeading";

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

  const runtime = useMemo(
    () => runtimes.data?.find((item) => item.key === runtimeKey) ?? runtimes.data?.[0],
    [runtimeKey, runtimes.data],
  );
  const poolIds = useMemo(() => {
    const ids = new Set<string>();
    (runners.data ?? []).filter((runner) => runner.status === "online" || runner.status === "busy").forEach((runner) => ids.add(runner.resource_pool_id));
    return Array.from(ids);
  }, [runners.data]);

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

  async function runPreflight() {
    if (!versionId || !modelConfigId || !poolId) {
      message.warning("先选择数据集版本、模型配置和资源池");
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
      });
      setPreflight(result);
      setStep(2);
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
        idempotency_key: crypto.randomUUID(),
      }),
    onSuccess: (run) => {
      message.success("评测已创建");
      navigate(`/evals/${run.id}`);
    },
    onError: (error) => message.error(error instanceof Error ? error.message : "创建失败"),
  });

  const selectedModel = models.data?.find((item) => item.id === modelConfigId);

  return (
    <section className="page">
      <PageHeading
        eyebrow="Evaluation · Launch"
        title="发起真实评测"
        description="冻结数据集版本、模型、Runtime 与资源池；一致性检查阻塞项会在创建前展示。"
      />
      <Steps
        current={step}
        items={[{ title: "数据集" }, { title: "模型与资源" }, { title: "一致性检查" }, { title: "创建" }]}
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
                  onChange={setRuntimeKey}
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
              <Button onClick={() => setStep(0)}>上一步</Button>
              <Button type="primary" loading={checking} onClick={runPreflight}>运行一致性检查</Button>
            </Space>
            {preflightError ? <Alert type="error" showIcon message={preflightError} /> : null}
          </Space>
        ) : null}
        {step === 2 && preflight ? (
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
            </Descriptions>
            <Space>
              <Button onClick={() => setStep(1)}>上一步</Button>
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
