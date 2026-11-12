import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  Alert,
  Button,
  Empty,
  Input,
  InputNumber,
  Segmented,
  Select,
  Table,
  Tag,
  message,
} from "antd";
import type { TableColumnsType } from "antd";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  fetchAssetSources,
  fetchAssetTree,
  hasRuntimePrerequisites,
  isLoadTestPassed,
  resolveAgentAssets,
  type AssetSource,
  type AssetType,
  type ScannedAsset,
} from "../api/agentAssets";
import {
  fetchDatasetCasesPage,
  fetchDatasetVersion,
  fetchDatasetVersions,
  fetchDatasets,
  type DatasetCaseSummary,
  type DatasetVersion,
} from "../api/datasets";
import {
  createEvaluation,
  prepareEvaluation,
  type CaseSelection,
  type CaseSelectionMode,
  type ExecutionPolicy,
  type PrepareResponse,
  type PreflightIssue,
} from "../api/evaluations";
import { fetchModelConfigs } from "../api/models";
import { fetchResourcePools } from "../api/resources";
import { fetchRuntimes } from "../api/runtimes";
import { ApiError } from "../api/types";
import { Icon } from "../components/Icon";
import { formatMoney, formatMoneyRange } from "../utils/money";
import { PageHeading } from "../components/PageHeading";

const DRAFT_KEY = "ai-eval-platform:eval-wizard-draft:v1";
const CASE_PAGE_SIZE = 20;
const MAX_PROMPT_SUFFIX = 4000;
const PROMPT_SUFFIX_PLACEHOLDER =
  "例如：这是非交互评测。请使用 superpowers 技能完成任务；不要向用户提问，自主决策并继续。";

const STEP_ITEMS: { title: string; meta: string }[] = [
  { title: "数据集与用例", meta: "版本与子集" },
  { title: "Agent / 资产", meta: "Runtime 必选 · 资产可选" },
  { title: "模型 / 资源", meta: "凭据、容量与执行策略" },
  { title: "确认启动", meta: "冻结快照与费用" },
];

const TIMEOUT_OPTIONS = [15, 30, 60, 120].map((minutes) => ({
  value: minutes * 60,
  label: `${minutes} 分钟`,
}));

interface WizardDraft {
  datasetId: string | null;
  versionId: string | null;
  selectionMode: CaseSelectionMode;
  selectionCount: number;
  selectionKeys: string[];
  assetSourceId: string | null;
  assetRef: string;
  selectedPaths: string[];
  assetSelectionTouched: boolean;
  assetSnapshotId: string | null;
  assetSnapshotCommit: string | null;
  assetSnapshotCount: number;
  modelConfigId: string | null;
  runtimeKey: string;
  poolId: string | null;
  concurrency: number;
  caseTimeoutSeconds: number;
  maxAttempts: number;
  budgetCny: number | null;
  executionDigest: string;
  scoringDigest: string;
  promptSuffix: string;
}

function defaultDraft(): WizardDraft {
  return {
    datasetId: null,
    versionId: null,
    selectionMode: "all",
    selectionCount: 50,
    selectionKeys: [],
    assetSourceId: null,
    assetRef: "",
    selectedPaths: [],
    assetSelectionTouched: false,
    assetSnapshotId: null,
    assetSnapshotCommit: null,
    assetSnapshotCount: 0,
    modelConfigId: null,
    runtimeKey: "claude-code",
    poolId: null,
    concurrency: 8,
    caseTimeoutSeconds: 3600,
    maxAttempts: 2,
    budgetCny: null,
    executionDigest: "",
    scoringDigest: "",
    promptSuffix: "",
  };
}

function stringOrNull(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function numberOr(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function readDraft(): WizardDraft | null {
  try {
    const raw = window.localStorage.getItem(DRAFT_KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null) return null;
    const it = parsed as Record<string, unknown>;
    const base = defaultDraft();
    const mode = it.selectionMode;
    return {
      ...base,
      datasetId: stringOrNull(it.datasetId),
      versionId: stringOrNull(it.versionId),
      selectionMode: mode === "count" || mode === "cases" ? mode : "all",
      selectionCount: numberOr(it.selectionCount, base.selectionCount),
      selectionKeys: Array.isArray(it.selectionKeys)
        ? it.selectionKeys.filter((key): key is string => typeof key === "string")
        : [],
      assetSourceId: stringOrNull(it.assetSourceId),
      assetRef: typeof it.assetRef === "string" ? it.assetRef : "",
      selectedPaths: Array.isArray(it.selectedPaths)
        ? it.selectedPaths.filter((path): path is string => typeof path === "string")
        : [],
      assetSelectionTouched: it.assetSelectionTouched === true,
      assetSnapshotId: stringOrNull(it.assetSnapshotId),
      assetSnapshotCommit: stringOrNull(it.assetSnapshotCommit),
      assetSnapshotCount: numberOr(it.assetSnapshotCount, 0),
      modelConfigId: stringOrNull(it.modelConfigId),
      runtimeKey: typeof it.runtimeKey === "string" && it.runtimeKey ? it.runtimeKey : base.runtimeKey,
      poolId: stringOrNull(it.poolId),
      concurrency: Math.max(1, numberOr(it.concurrency, base.concurrency)),
      caseTimeoutSeconds: Math.max(60, numberOr(it.caseTimeoutSeconds, base.caseTimeoutSeconds)),
      maxAttempts: Math.max(1, numberOr(it.maxAttempts, base.maxAttempts)),
      budgetCny:
        typeof it.budgetCny === "number" && Number.isFinite(it.budgetCny) ? it.budgetCny : null,
      executionDigest: typeof it.executionDigest === "string" ? it.executionDigest : "",
      scoringDigest: typeof it.scoringDigest === "string" ? it.scoringDigest : "",
      promptSuffix:
        typeof it.promptSuffix === "string" ? it.promptSuffix.slice(0, MAX_PROMPT_SUFFIX) : "",
    };
  } catch {
    return null;
  }
}

function saveDraft(draft: WizardDraft): void {
  try {
    window.localStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
  } catch {
    // Storage can be unavailable (private mode); the wizard still works.
  }
}

function clearDraft(): void {
  try {
    window.localStorage.removeItem(DRAFT_KEY);
  } catch {
    // ignore
  }
}

function newIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `idem-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function formatMinutes(seconds: number): string {
  const minutes = Math.max(0, Math.round(seconds / 60));
  if (minutes < 60) return `${minutes} 分钟`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} 小时 ${rest} 分` : `${hours} 小时`;
}

function formatDuration(range: { low: number; high: number } | null): string {
  if (!range || range.high <= 0) return "—";
  return `${formatMinutes(range.low)}–${formatMinutes(range.high)}`;
}

function formatCountdown(seconds: number): string {
  const safe = Math.max(0, seconds);
  const minutes = Math.floor(safe / 60);
  const rest = safe % 60;
  return `${minutes}:${String(rest).padStart(2, "0")}`;
}

function blockersFromDetails(details: Record<string, unknown> | null | undefined): PreflightIssue[] {
  const raw = details && Array.isArray(details.blockers) ? details.blockers : [];
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
  const initialDraft = useRef<WizardDraft | null>(null);
  if (initialDraft.current === null) {
    initialDraft.current = readDraft() ?? defaultDraft();
  }
  const [draft, setDraft] = useState<WizardDraft>(initialDraft.current);
  const [step, setStep] = useState(0);
  const [preparation, setPreparation] = useState<PrepareResponse | null>(null);
  const [blockers, setBlockers] = useState<PreflightIssue[]>([]);
  const [createNotice, setCreateNotice] = useState<{
    title: string;
    description?: string;
  } | null>(null);
  const [checking, setChecking] = useState(false);
  const [name, setName] = useState("");
  const [caseOffset, setCaseOffset] = useState(0);
  const [caseLimit, setCaseLimit] = useState(CASE_PAGE_SIZE);
  const [assetPage, setAssetPage] = useState(1);
  const [assetPageSize, setAssetPageSize] = useState(8);
  const [assetFilter, setAssetFilter] = useState<"all" | AssetType>("all");
  const [nowTick, setNowTick] = useState(() => Date.now());
  const idempotencyRef = useRef<{ preparationId: string; key: string } | null>(null);

  const update = useCallback(
    (patch: Partial<WizardDraft>, options?: { invalidate?: boolean }) => {
      setDraft((current) => ({ ...current, ...patch }));
      if (options?.invalidate !== false) {
        setPreparation(null);
        setBlockers([]);
        setCreateNotice(null);
      }
    },
    [],
  );

  useEffect(() => {
    saveDraft(draft);
  }, [draft]);

  const datasets = useQuery({ queryKey: ["datasets"], queryFn: ({ signal }) => fetchDatasets(signal) });
  const versions = useQuery({
    queryKey: ["dataset", draft.datasetId, "versions"],
    queryFn: ({ signal }) => fetchDatasetVersions(draft.datasetId ?? "", signal),
    enabled: Boolean(draft.datasetId),
  });
  const versionDetail = useQuery({
    queryKey: ["dataset-version", draft.versionId],
    queryFn: ({ signal }) => fetchDatasetVersion(draft.versionId ?? "", signal),
    enabled: Boolean(draft.versionId),
  });
  const casesPage = useQuery({
    queryKey: ["dataset-version", draft.versionId, "cases", caseLimit, caseOffset],
    queryFn: ({ signal }) =>
      fetchDatasetCasesPage(draft.versionId ?? "", { limit: caseLimit, offset: caseOffset }, signal),
    enabled: Boolean(draft.versionId) && draft.selectionMode === "cases",
  });
  const models = useQuery({ queryKey: ["model-configs"], queryFn: ({ signal }) => fetchModelConfigs(signal) });
  const runtimes = useQuery({ queryKey: ["runtimes"], queryFn: ({ signal }) => fetchRuntimes(signal) });
  const pools = useQuery({
    queryKey: ["resource-pools"],
    queryFn: ({ signal }) => fetchResourcePools(signal),
  });
  const sources = useQuery({
    queryKey: ["asset-sources"],
    queryFn: ({ signal }) => fetchAssetSources(signal),
  });
  const assetTree = useQuery({
    queryKey: ["asset-tree", draft.assetSourceId],
    queryFn: ({ signal }) => fetchAssetTree(draft.assetSourceId ?? "", signal),
    enabled: Boolean(draft.assetSourceId),
  });

  // Deep links from the dataset/asset pages override a fresh wizard only.
  const deepLinkApplied = useRef(false);
  useEffect(() => {
    if (deepLinkApplied.current) return;
    deepLinkApplied.current = true;
    const versionId = searchParams.get("datasetVersionId");
    const assetSourceId = searchParams.get("assetSourceId");
    if (!versionId && !assetSourceId) return;
    update(
      {
        ...(versionId ? { versionId } : {}),
        ...(assetSourceId ? { assetSourceId } : {}),
      },
      { invalidate: false },
    );
  }, [searchParams, update]);

  const runtime = useMemo(
    () => runtimes.data?.find((item) => item.key === draft.runtimeKey) ?? runtimes.data?.[0],
    [draft.runtimeKey, runtimes.data],
  );
  const selectedModel = models.data?.find((item) => item.id === draft.modelConfigId) ?? null;
  const selectedPool = pools.data?.find((item) => item.id === draft.poolId) ?? null;
  const selectedSource = sources.data?.find((source) => source.id === draft.assetSourceId) ?? null;
  const runtimeConfigured = hasRuntimePrerequisites(selectedSource?.runtime_prerequisites);
  const runtimeLoadTestPassed = isLoadTestPassed(selectedSource?.load_test);
  // Only sources that declare commands are hard-blocked until the isolated
  // load test passes; sources without explicit prerequisites keep the prior
  // warning-only behavior.
  const runtimeSetupBlocked = Boolean(runtimeConfigured && !runtimeLoadTestPassed);
  const runtimeSetupWarning = Boolean(
    selectedSource && (runtimeSetupBlocked || selectedSource.status === "requires_setup"),
  );
  const totalCases = versionDetail.data?.case_count ?? 0;
  const selectedCaseCount =
    draft.selectionMode === "all"
      ? totalCases
      : draft.selectionMode === "count"
        ? Math.min(Math.max(1, draft.selectionCount), Math.max(1, totalCases))
        : draft.selectionKeys.length;

  useEffect(() => {
    if (!draft.datasetId && datasets.data?.length) {
      update({ datasetId: datasets.data[0].id }, { invalidate: false });
    }
  }, [datasets.data, draft.datasetId, update]);

  useEffect(() => {
    if (draft.versionId || !versions.data?.length) return;
    const first = versions.data[0];
    update(
      {
        versionId: first.id,
        selectionCount: Math.min(50, Math.max(1, first.case_count)),
        executionDigest: "",
        scoringDigest: "",
      },
      { invalidate: false },
    );
  }, [draft.versionId, versions.data, update]);

  useEffect(() => {
    if (versionDetail.data && versionDetail.data.dataset_id !== draft.datasetId) {
      update({ datasetId: versionDetail.data.dataset_id }, { invalidate: false });
    }
  }, [draft.datasetId, versionDetail.data, update]);

  useEffect(() => {
    if (!draft.modelConfigId && models.data?.length) {
      update({ modelConfigId: models.data[0].id }, { invalidate: false });
    }
  }, [draft.modelConfigId, models.data, update]);

  useEffect(() => {
    const activePools = (pools.data ?? []).filter((pool) => pool.status === "active");
    if (!draft.poolId && activePools.length) {
      update({ poolId: activePools[0].id }, { invalidate: false });
    }
  }, [draft.poolId, pools.data, update]);

  useEffect(() => {
    if (!versionDetail.data) return;
    const fallbackRuntime = runtimes.data?.find((item) => item.key === draft.runtimeKey);
    const patch: Partial<WizardDraft> = {};
    if (!draft.executionDigest) {
      patch.executionDigest =
        versionDetail.data.execution_image_digest ||
        fallbackRuntime?.execution_image_digest ||
        "";
    }
    if (!draft.scoringDigest) {
      patch.scoringDigest =
        versionDetail.data.scoring_image_digest || fallbackRuntime?.scoring_image_digest || "";
    }
    if (Object.keys(patch).length) update(patch, { invalidate: false });
  }, [
    draft.executionDigest,
    draft.runtimeKey,
    draft.scoringDigest,
    runtimes.data,
    update,
    versionDetail.data,
  ]);

  // Assets stay unselected until the operator opts in: the asset step is
  // optional, while the Agent Runtime remains required. Deep links from the
  // dataset/asset pages still preselect a source through the effect above.

  // Directory-mode sources auto-select every asset until the operator edits
  // the selection themselves.
  useEffect(() => {
    if (!assetTree.data || draft.assetSelectionTouched) return;
    if (selectedSource?.selection_mode !== "directory") return;
    update(
      { selectedPaths: assetTree.data.assets.map((asset) => asset.path) },
      { invalidate: false },
    );
  }, [assetTree.data, draft.assetSelectionTouched, selectedSource?.selection_mode, update]);

  useEffect(() => {
    setCaseOffset(0);
  }, [draft.versionId, draft.selectionMode]);

  useEffect(() => {
    if (!preparation) return;
    const timer = window.setInterval(() => setNowTick(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [preparation]);

  const resolve = useMutation({
    mutationFn: () =>
      resolveAgentAssets({
        source_id: draft.assetSourceId ?? "",
        requested_ref: draft.assetRef || selectedSource?.default_ref,
        paths: draft.selectedPaths,
        runtime_key: draft.runtimeKey,
      }),
    onSuccess: (result) => {
      update({
        assetSnapshotId: result.snapshot.id,
        assetSnapshotCommit: result.snapshot.resolved_commit,
        assetSnapshotCount: result.snapshot.asset_count,
      });
      message.success(
        `已冻结 ${result.snapshot.asset_count} 个资产到 @${result.snapshot.resolved_commit.slice(0, 8)}`,
      );
    },
    onError: (error) => message.error(error instanceof Error ? error.message : "资产解析失败"),
  });

  const create = useMutation({
    mutationFn: async () => {
      if (!preparation?.preparation_id) throw new Error("准备快照缺失");
      if (!idempotencyRef.current || idempotencyRef.current.preparationId !== preparation.preparation_id) {
        idempotencyRef.current = {
          preparationId: preparation.preparation_id,
          key: newIdempotencyKey(),
        };
      }
      return createEvaluation(
        { preparation_id: preparation.preparation_id, name: name.trim() || undefined },
        idempotencyRef.current.key,
      );
    },
    onSuccess: (run) => {
      clearDraft();
      message.success("评测已创建");
      navigate(`/evals/${run.id}`);
    },
    onError: (error) => {
      const api = error instanceof ApiError ? error : null;
      if (api && api.code === "preparation_expired") {
        setPreparation(null);
        setStep(2);
        setCreateNotice({
          title: "准备快照已过期",
          description: "请返回执行策略重新准备；已冻结的输入不会自动沿用。",
        });
        return;
      }
      if (api && api.code === "configuration_changed") {
        setPreparation(null);
        setStep(2);
        setCreateNotice({
          title: "配置在确认后发生变化",
          description:
            "模型凭据、资源池、资产 commit 或镜像默认值已变化，请重新准备后再确认。",
        });
        return;
      }
      if (api && api.code === "preparation_consumed") {
        setPreparation(null);
        setStep(2);
        setCreateNotice({
          title: "准备快照已用于创建评测",
          description: "创建是单次操作；需要再次发起评测时请重新准备。",
        });
        return;
      }
      if (api && (api.code === "capacity_invalid" || api.code === "preparation_blocked")) {
        setPreparation(null);
        setStep(2);
        setBlockers(blockersFromDetails(api.details));
        setCreateNotice({
          title: "一致性命中阻塞项",
          description: api.message,
        });
        return;
      }
      message.error(error instanceof Error ? error.message : "创建失败");
    },
  });

  function selectDataset(datasetId: string) {
    update({
      datasetId,
      versionId: null,
      selectionKeys: [],
      executionDigest: "",
      scoringDigest: "",
    });
  }

  function selectVersion(versionId: string, version?: DatasetVersion) {
    update({
      versionId,
      selectionKeys: [],
      selectionCount: Math.min(50, Math.max(1, version?.case_count ?? 50)),
      executionDigest: "",
      scoringDigest: "",
    });
  }

  function selectAssetSource(sourceId: string) {
    const source = sources.data?.find((item) => item.id === sourceId);
    update({
      assetSourceId: sourceId,
      assetRef: source?.default_ref ?? "",
      selectedPaths: [],
      assetSelectionTouched: false,
      assetSnapshotId: null,
      assetSnapshotCommit: null,
      assetSnapshotCount: 0,
    });
    setAssetFilter("all");
    setAssetPage(1);
  }

  function toggleAsset(path: string, checked: boolean) {
    update({
      selectedPaths: checked
        ? [...draft.selectedPaths, path]
        : draft.selectedPaths.filter((item) => item !== path),
      assetSelectionTouched: true,
      assetSnapshotId: null,
      assetSnapshotCommit: null,
      assetSnapshotCount: 0,
    });
  }

  const filteredAssets = useMemo(() => {
    const assets = assetTree.data?.assets ?? [];
    return assetFilter === "all" ? assets : assets.filter((asset) => asset.asset_type === assetFilter);
  }, [assetFilter, assetTree.data]);
  const counts = assetTree.data?.counts ?? { skill: 0, command: 0, subagent: 0 };
  const expiresAt = preparation?.expires_at ? Date.parse(preparation.expires_at) : null;
  const remainingSeconds = expiresAt ? Math.max(0, Math.floor((expiresAt - nowTick) / 1000)) : 0;
  const expired = Boolean(preparation) && remainingSeconds <= 0;
  const summary = preparation?.summary ?? null;
  const costEstimate = preparation?.cost_estimate ?? null;

  const assetTableColumns: TableColumnsType<ScannedAsset> = [
    {
      title: "类型",
      dataIndex: "asset_type",
      width: 100,
      render: (value: AssetType) => assetTypeTag(value),
    },
    {
      title: "资产",
      dataIndex: "name",
      render: (value: string) => <span style={{ fontWeight: 600 }}>{value}</span>,
    },
    {
      title: "路径",
      dataIndex: "path",
      render: (value: string) => <span className="mono small muted">{value}</span>,
    },
    {
      title: "来源",
      dataIndex: "path",
      width: 100,
      render: (value: string) =>
        value.startsWith("generated:") ? <Tag color="orange">生成</Tag> : <Tag>源文件</Tag>,
    },
  ];

  const caseColumns: TableColumnsType<DatasetCaseSummary> = [
    {
      title: "用例",
      dataIndex: "case_key",
      render: (value: string, record) => (
        <div>
          <div className="mono">{value}</div>
          <div className="small muted">{record.repo ?? "—"}</div>
        </div>
      ),
    },
    {
      title: "Base commit",
      dataIndex: "base_commit",
      width: 150,
      render: (value: string | null) => (
        <span className="mono small muted">{value ? value.slice(0, 10) : "—"}</span>
      ),
    },
  ];

  function blockerFor(field: string): PreflightIssue | undefined {
    return blockers.find((item) => item.field === field);
  }

  function caseSelectionPayload(): CaseSelection {
    if (draft.selectionMode === "count") {
      return {
        mode: "count",
        count: Math.max(1, Math.min(draft.selectionCount, Math.max(1, totalCases))),
      };
    }
    if (draft.selectionMode === "cases") {
      return { mode: "cases", case_keys: draft.selectionKeys };
    }
    return { mode: "all" };
  }

  function executionPayload(): ExecutionPolicy {
    return {
      concurrency: draft.concurrency,
      case_timeout_seconds: draft.caseTimeoutSeconds,
      max_attempts: draft.maxAttempts,
      model_budget_cny: draft.budgetCny ?? undefined,
    };
  }

  async function runPrepare() {
    if (!draft.versionId || !draft.modelConfigId || !draft.poolId) {
      message.warning("先选择数据集版本、模型配置和资源池");
      return;
    }
    if (!draft.runtimeKey) {
      message.warning("先选择 Agent Runtime");
      return;
    }
    if (draft.assetSourceId && !draft.assetSnapshotId && draft.selectedPaths.length > 0) {
      message.warning("已选择资产但尚未读取冻结：点击「读取资产」，或清空选择后跳过资产");
      return;
    }
    if (runtimeSetupBlocked) {
      message.warning("所选资产源的运行时前置未通过加载测试：请先在 Agent 资产页完成加载测试");
      return;
    }
    setChecking(true);
    setBlockers([]);
    setCreateNotice(null);
    try {
      const result = await prepareEvaluation({
        dataset_version_id: draft.versionId,
        model_config_id: draft.modelConfigId,
        runtime_key: draft.runtimeKey,
        resource_pool_id: draft.poolId,
        ...(draft.assetSnapshotId ? { asset_snapshot_id: draft.assetSnapshotId } : {}),
        case_selection: caseSelectionPayload(),
        execution: executionPayload(),
        scoring_image_digest: draft.scoringDigest || undefined,
        execution_image_digest: draft.executionDigest || undefined,
        prompt_suffix: draft.promptSuffix.trim() || undefined,
      });
      setPreparation(result);
      if (result.ready) {
        setStep(3);
      } else {
        setBlockers(result.blockers);
        setStep(2);
      }
    } catch (error) {
      message.error(error instanceof Error ? error.message : "准备失败");
    } finally {
      setChecking(false);
    }
  }

  function goNext() {
    if (step === 0) {
      if (!draft.versionId) {
        message.warning("先选择数据集版本");
        return;
      }
      if (draft.selectionMode === "cases" && draft.selectionKeys.length === 0) {
        message.warning("指定用例模式下至少选择 1 个用例");
        return;
      }
      setStep(1);
      return;
    }
    if (step === 1) {
      if (!draft.runtimeKey) {
        message.warning("先选择 Agent Runtime");
        return;
      }
      if (draft.assetSourceId && !draft.assetSnapshotId && draft.selectedPaths.length > 0) {
        message.warning("已选择资产但尚未读取冻结：点击「读取资产」，或清空选择后跳过资产");
        return;
      }
      if (runtimeSetupBlocked) {
        message.warning("所选资产源的运行时前置未通过加载测试：请先在 Agent 资产页完成加载测试");
        return;
      }
      setStep(2);
    }
  }

  function goBack() {
    if (step === 0) {
      navigate("/evals");
      return;
    }
    setStep(step - 1);
  }

  const canPrepare = Boolean(draft.versionId && draft.modelConfigId && draft.poolId && draft.runtimeKey);

  const datasetName = versionDetail.data?.dataset_id
    ? datasets.data?.find((item) => item.id === versionDetail.data?.dataset_id)?.name
    : null;
  const selectedDatasetName =
    datasetName ?? datasets.data?.find((item) => item.id === draft.datasetId)?.name ?? "未选择";
  const versionLabel = versionDetail.data?.version ?? "—";
  const assetSummary = draft.assetSnapshotId
    ? `${draft.assetSnapshotCount} 项 @${(draft.assetSnapshotCommit ?? "").slice(0, 8)}`
    : draft.assetSourceId
      ? "尚未读取冻结"
      : "仅使用内置能力";
  const peakConcurrency =
    summary?.resolved.effective_concurrency ??
    (selectedPool
      ? Math.min(
          draft.concurrency,
          selectedPool.max_capacity > 0 ? selectedPool.max_capacity : draft.concurrency,
        )
      : draft.concurrency);
  const selectedMetrics = versionDetail.data?.metrics?.map((metric) => metric.display_name) ?? [];
  const promptSuffix = draft.promptSuffix.trim();

  function renderStepBar() {
    return (
      <div className="step-bar">
        {STEP_ITEMS.map((item, index) => (
          <div
            key={item.title}
            className={`step ${step === index ? "active" : step > index ? "done" : ""}`}
          >
            <div className="step-index">{step > index ? "✓" : index + 1}</div>
            <div>
              <div>{item.title}</div>
              <div className="small muted">{item.meta}</div>
            </div>
          </div>
        ))}
      </div>
    );
  }

  function renderAside() {
    return (
      <aside className="card wizard-aside">
        <div className="card-header">
          <div>
            <h2 className="card-title">评测摘要</h2>
            <p className="card-subtitle">提交后仍可取消排队任务</p>
          </div>
          <Icon name="lock" size={15} className="muted" />
        </div>
        <div className="card-body summary-list">
          <div className="summary-item">
            <span className="summary-label">数据集</span>
            <span className="summary-value">
              {selectedDatasetName}
              <br />
              <span className="muted">
                {versionLabel} · {selectedCaseCount} 用例
              </span>
            </span>
          </div>
          <div className="summary-item">
            <span className="summary-label">Agent</span>
            <span className="summary-value">
              {runtime ? `${runtime.key} ${runtime.version}` : draft.runtimeKey}
            </span>
          </div>
          <div className="summary-item">
            <span className="summary-label">Agent 资产</span>
            <span className="summary-value">
              {selectedSource?.name ?? "未选择（可选）"}
              <br />
              <span className="muted mono">{assetSummary}</span>
            </span>
          </div>
          <div className="summary-item">
            <span className="summary-label">模型</span>
            <span className="summary-value">
              {selectedModel ? `${selectedModel.display_name} · ${selectedModel.model_name}` : "未选择"}
            </span>
          </div>
          <div className="summary-item">
            <span className="summary-label">资源池</span>
            <span className="summary-value">
              {selectedPool
                ? `${selectedPool.name} · ${selectedPool.runners.online} 在线`
                : "未选择"}
            </span>
          </div>
          <div className="summary-item">
            <span className="summary-label">附加指令</span>
            <span className="summary-value">
              {promptSuffix ? `已配置 · ${promptSuffix.length} 字符` : "未设置"}
            </span>
          </div>
        </div>
        <div className="summary-cost">
          <div className="review-box">
            <div className="row-between">
              <span className="small muted">预计模型费</span>
              <strong>{costEstimate ? formatMoneyRange(costEstimate.model_cny.low, costEstimate.model_cny.high) : "确认后计算"}</strong>
            </div>
            <div className="row-between" style={{ marginTop: 7 }}>
              <span className="small muted">预计峰值并发</span>
              <strong>{peakConcurrency} Runner</strong>
            </div>
            <div className="row-between" style={{ marginTop: 7 }}>
              <span className="small muted">预计总耗时</span>
              <strong>{formatDuration(costEstimate?.estimated_duration_seconds ?? null)}</strong>
            </div>
          </div>
        </div>
      </aside>
    );
  }

  function renderDatasetStep() {
    const versionOptions = versions.data ?? [];
    return (
      <div>
        <div className="eyebrow">Step 1 · Dataset</div>
        <h2 className="wizard-title">选择数据集版本</h2>
        <p className="small muted wizard-subtitle">
          版本固定用例、环境、评分命令与指标口径，确保结果可复现；本次可再选择用例子集。
        </p>
        <div className="field">
          <label className="required">数据集</label>
          <Select
            value={draft.datasetId ?? undefined}
            onChange={selectDataset}
            options={(datasets.data ?? []).map((item) => ({ value: item.id, label: item.name }))}
            placeholder="选择数据集"
            loading={datasets.isLoading}
            showSearch
            optionFilterProp="label"
          />
        </div>
        {versionOptions.length ? (
          <div className="choice-grid">
            {versionOptions.map((item) => {
              const selected = draft.versionId === item.id;
              return (
                <button
                  key={item.id}
                  type="button"
                  className={`choice-card ${selected ? "selected" : ""}`}
                  onClick={() => selectVersion(item.id, item)}
                >
                  <div className="choice-icon">
                    <Icon name="database" size={16} />
                  </div>
                  <div className="choice-title">
                    {datasets.data?.find((dataset) => dataset.id === item.dataset_id)?.name ?? "数据集"}
                  </div>
                  <div className="choice-meta">
                    {item.version} · {item.case_count} 个用例
                  </div>
                  <div className="choice-tags">
                    <span className={`tag ${item.status === "active" ? "tag-success" : "tag-neutral"}`}>
                      {item.status}
                    </span>
                    <span className="tag tag-neutral mono">{item.source_revision.slice(0, 8)}</span>
                  </div>
                </button>
              );
            })}
          </div>
        ) : (
          <Empty
            description={draft.datasetId ? "该数据集还没有可用版本" : "先选择数据集"}
            image={Empty.PRESENTED_IMAGE_SIMPLE}
          >
            <Button onClick={() => navigate("/datasets")}>去导入数据集</Button>
          </Empty>
        )}
        {versionDetail.data ? (
          <div className="small muted wizard-version-meta">
            来源 {versionDetail.data.source_uri || "—"} · 用例 {versionDetail.data.case_count} · 指标口径{" "}
            {selectedMetrics.length ? selectedMetrics.slice(0, 3).join(" · ") : "—"}
          </div>
        ) : null}
        <div className="field-row">
          <div className="field">
            <label>用例范围</label>
            <Select
              id="wizard-selection-mode"
              value={draft.selectionMode}
              onChange={(value) => {
                update({ selectionMode: value as CaseSelectionMode }, { invalidate: false });
                setCaseOffset(0);
              }}
              options={[
                { value: "all", label: `全部 ${totalCases} 个用例` },
                { value: "count", label: "按数量选择" },
                { value: "cases", label: "指定用例" },
              ]}
            />
          </div>
          {draft.selectionMode === "count" ? (
            <div className="field">
              <label className="required">用例数量</label>
              <InputNumber
                id="wizard-case-count"
                min={1}
                max={Math.max(1, totalCases)}
                value={draft.selectionCount}
                onChange={(value) =>
                  update(
                    { selectionCount: Math.max(1, Math.min(value ?? 1, Math.max(1, totalCases))) },
                    { invalidate: false },
                  )
                }
                style={{ width: "100%" }}
              />
              <span className="hint">从版本第一个用例起取前 N 个（按 ordinal 排序）。</span>
            </div>
          ) : draft.selectionMode === "cases" ? (
            <div className="field">
              <label>已选用例</label>
              <div className="asset-commit-box">
                <span className="mono small">{draft.selectionKeys.length} 个</span>
              </div>
            </div>
          ) : null}
        </div>
        {draft.selectionMode === "cases" ? (
          <div className="wizard-case-picker">
            <div className="row-between wizard-case-toolbar">
              <span className="small muted">
                已选择 {draft.selectionKeys.length} / {casesPage.data?.total ?? totalCases} 个用例
              </span>
              <Button size="small" onClick={() => update({ selectionKeys: [] }, { invalidate: false })}>
                全部取消
              </Button>
            </div>
            <Table<DatasetCaseSummary>
              rowKey="case_key"
              size="small"
              columns={caseColumns}
              dataSource={casesPage.data?.items ?? []}
              loading={casesPage.isLoading}
              rowSelection={{
                selectedRowKeys: draft.selectionKeys,
                preserveSelectedRowKeys: true,
                onChange: (keys) => update({ selectionKeys: keys.map(String) }, { invalidate: false }),
              }}
              pagination={{
                current: Math.floor(caseOffset / caseLimit) + 1,
                pageSize: caseLimit,
                total: casesPage.data?.total ?? 0,
                showSizeChanger: true,
                pageSizeOptions: [20, 50, 100],
                onChange: (page, pageSize) => {
                  setCaseLimit(pageSize);
                  setCaseOffset((page - 1) * pageSize);
                },
              }}
            />
          </div>
        ) : null}
        <div className="banner">
          <Icon name="list" size={15} />
          <div>
            <strong>结果将包含</strong> · 解决率、Token / 用例、成本 / 用例、难度分布与逐用例评分明细。
          </div>
        </div>
        {!datasets.isLoading && !(datasets.data ?? []).length ? (
          <Alert
            type="warning"
            showIcon
            message="还没有数据集"
            description="先到数据集页导入一个数据集版本，再回到这里发起评测。"
            action={
              <Button size="small" onClick={() => navigate("/datasets")}>
                去导入
              </Button>
            }
          />
        ) : null}
      </div>
    );
  }

  function renderAssetsStep() {
    const pageCount = Math.max(1, Math.ceil(filteredAssets.length / assetPageSize));
    const currentPage = Math.min(assetPage, pageCount);
    const refreshSnapshot = {
      assetSnapshotId: null,
      assetSnapshotCommit: null,
      assetSnapshotCount: 0,
    };
    return (
      <div>
        <div className="eyebrow">Step 2 · Runtime</div>
        <h2 className="wizard-title">选择 Agent 与资产源</h2>
        <p className="small muted wizard-subtitle">
          Agent Runtime 为必选；Git 资产源可选，不选择时只使用 Runtime 内置能力。
        </p>
        <div className="wizard-section-head">
          <div>
            <h3>Agent Runtime（必选）</h3>
            <p className="small muted">决定会话适配器与 Runtime 内置能力</p>
          </div>
        </div>
        <div className="choice-grid">
          {(runtimes.data ?? []).map((item) => {
            const selected = draft.runtimeKey === item.key;
            return (
              <button
                key={item.key}
                type="button"
                className={`choice-card ${selected ? "selected" : ""}`}
                onClick={() => update({ runtimeKey: item.key, ...refreshSnapshot })}
              >
                <div className="choice-icon">
                  <Icon name="terminal" size={16} />
                </div>
                <div className="choice-title">{item.key}</div>
                <div className="choice-meta">
                  {item.version} · {item.adapter_version}
                </div>
                <div className="choice-tags">
                  <span className="tag tag-neutral">会话适配器已就绪</span>
                </div>
              </button>
            );
          })}
        </div>
        <div className="wizard-section-head">
          <div>
            <h3>Git 资产源（可选）</h3>
            <p className="small muted">不选择时只使用 Runtime 内置能力；选择后可读取并冻结快照</p>
          </div>
          <Button type="link" onClick={() => navigate("/assets")}>
            管理资产源 →
          </Button>
        </div>
        {!sources.isLoading && !(sources.data ?? []).length ? (
          <Alert
            type="info"
            showIcon
            message="还没有 Agent 资产源（可选）"
            description="资产可选；如需加载 Skill、Command 或 Subagent，先到 Agent 资产页添加 Git 资产源并扫描。"
            action={
              <Button size="small" onClick={() => navigate("/assets")}>
                去添加
              </Button>
            }
          />
        ) : null}
        <div className="asset-runtime-panel">
          <div className="field">
            <label>资产源（可选）</label>
            <Select
              value={draft.assetSourceId ?? undefined}
              onChange={selectAssetSource}
              options={(sources.data ?? []).map((source) => ({
                value: source.id,
                label: sourceOption(source),
              }))}
              placeholder="选择 Git 资产源"
              loading={sources.isLoading}
              showSearch
              optionFilterProp="label"
            />
          </div>
          <div className="asset-runtime-meta">
            <span className="tag tag-neutral mono">
              {selectedSource
                ? selectedSource.repo_url.replace("https://github.com/", "")
                : "未选择仓库"}
            </span>
            <span className="tag tag-neutral">
              路径 {(selectedSource?.scan_paths ?? []).join(" · ") || "—"}
            </span>
            <span className="tag tag-primary">{counts.skill} Skill</span>
            <span className="tag tag-violet">{counts.command} Command</span>
            <span className="tag tag-success">{counts.subagent} Subagent</span>
            {runtimeConfigured ? (
              <span className={runtimeLoadTestPassed ? "tag tag-success" : "tag tag-warning"}>
                {runtimeLoadTestPassed
                  ? "加载测试通过"
                  : selectedSource?.load_test.status === "failed"
                    ? "加载测试失败"
                    : "加载测试未通过"}
              </span>
            ) : null}
          </div>
          <div className="field-row asset-ref-row">
            <div className="field">
              <label>评测 Ref（可选）</label>
              <Input
                className="mono"
                value={draft.assetRef}
                onChange={(event) =>
                  update({
                    assetRef: event.target.value,
                    ...refreshSnapshot,
                  })
                }
                placeholder="分支、tag 或 commit"
              />
              <span className="hint">
                {selectedSource
                  ? `默认填入 ${selectedSource.default_ref}，发起本次评测前可以修改。`
                  : "选择资产源后填入默认 Ref"}
              </span>
            </div>
            <div className="field">
              <label>已解析版本</label>
              <div className="asset-commit-box">
                <span className="mono small">
                  {draft.assetSnapshotCommit
                    ? `@${draft.assetSnapshotCommit}`
                    : "尚未解析当前 Ref"}
                </span>
                <Button
                  size="small"
                  onClick={() => resolve.mutate()}
                  loading={resolve.isPending}
                  disabled={!draft.assetSourceId || !draft.selectedPaths.length}
                >
                  <Icon name="refresh" size={13} /> 读取资产
                </Button>
              </div>
            </div>
          </div>
          <div
            className={`banner ${
              draft.assetSnapshotId ? "success" : draft.assetSourceId ? "warning" : ""
            }`}
          >
            <Icon
              name={draft.assetSnapshotId ? "check" : draft.assetSourceId ? "alert" : "help"}
              size={15}
            />
            <div>
              {draft.assetSnapshotId ? (
                <>
                  已从 <span className="mono">{draft.assetRef || selectedSource?.default_ref}</span> 冻结{" "}
                  {draft.assetSnapshotCount} 项资产；启动时会固定到 commit{" "}
                  <span className="mono">{draft.assetSnapshotCommit}</span>。
                </>
              ) : draft.assetSourceId ? (
                <>
                  <strong>资产尚未读取冻结。</strong>{" "}
                  {draft.selectedPaths.length
                    ? `已选择 ${draft.selectedPaths.length} 项资产；点击「读取资产」冻结快照，或清空选择后跳过资产。`
                    : assetTree.isLoading
                      ? "正在读取资产树；读取完成后可选择并冻结，或清空选择后跳过资产。"
                      : "未选择任何资产；可直接进入下一步，只使用 Runtime 内置能力。"}
                </>
              ) : (
                <>
                  <strong>未选择 Agent 资产（可选）。</strong> 可直接进入下一步，只使用 Runtime
                  内置能力；如需 Skill、Command 或 Subagent，请选择资产源与 Ref 后读取资产。
                </>
              )}
            </div>
          </div>
        </div>
        {runtimeSetupWarning ? (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 12 }}
            message={
              runtimeSetupBlocked
                ? "运行时前置尚未通过加载测试"
                : "该资产源需要运行时准备"
            }
            description={
              runtimeConfigured
                ? `该资产源声明了安装/初始化命令，加载测试${
                    selectedSource?.load_test.status === "failed" ? "失败" : "尚未通过"
                  }；通过后才能发起评测。`
                : "扫描结果中的 Command 依赖运行时前置（如 openspec CLI）；平台不会静默安装，请确认目标沙箱已准备。"
            }
            action={
              <Button size="small" onClick={() => navigate("/assets")}>
                {runtimeSetupBlocked ? "去完成加载测试" : "去资产页查看"}
              </Button>
            }
          />
        ) : null}
        <div className="row-between wizard-asset-toolbar">
          <div>
            <strong>选择资产</strong>
            <div className="small muted">
              已选择 {draft.selectedPaths.length} / {assetTree.data?.assets.length ?? 0} 项 · 全选覆盖当前筛选的所有页
            </div>
          </div>
          <Button type="link" onClick={() => navigate("/assets")}>
            查看资产详情
          </Button>
        </div>
        <div className="wizard-asset-filters">
          <Segmented
            value={assetFilter}
            onChange={(value) => {
              setAssetFilter(value as "all" | AssetType);
              setAssetPage(1);
            }}
            options={[
              { value: "all", label: `全部 ${assetTree.data?.assets.length ?? 0}` },
              { value: "skill", label: `Skill ${counts.skill}` },
              { value: "command", label: `Command ${counts.command}` },
              { value: "subagent", label: `Subagent ${counts.subagent}` },
            ]}
          />
          <div className="wizard-asset-tools">
            <Button
              size="small"
              onClick={() =>
                update(
                  {
                    selectedPaths: filteredAssets.map((asset) => asset.path),
                    assetSelectionTouched: true,
                    ...refreshSnapshot,
                  },
                  { invalidate: true },
                )
              }
            >
              <Icon name="check" size={13} /> 全选全部
            </Button>
            <Button
              size="small"
              onClick={() =>
                update({ selectedPaths: [], assetSelectionTouched: true, ...refreshSnapshot }, { invalidate: true })
              }
            >
              全部取消
            </Button>
          </div>
        </div>
        {assetTree.isLoading ? (
          <div className="card wizard-asset-placeholder">正在读取资产树…</div>
        ) : filteredAssets.length ? (
          <Table<ScannedAsset>
            rowKey={(record) => record.path}
            size="small"
            columns={assetTableColumns}
            dataSource={filteredAssets}
            rowSelection={{
              selectedRowKeys: draft.selectedPaths,
              preserveSelectedRowKeys: true,
              onChange: (keys) =>
                update(
                  {
                    selectedPaths: keys.map(String),
                    assetSelectionTouched: true,
                    ...refreshSnapshot,
                  },
                  { invalidate: true },
                ),
            }}
            pagination={{
              current: currentPage,
              pageSize: assetPageSize,
              total: filteredAssets.length,
              showSizeChanger: true,
              pageSizeOptions: [8, 16, 32],
              showTotal: (total, range) => `显示 ${range[0]}–${range[1]} / ${total}`,
              onChange: (page, pageSize) => {
                setAssetPageSize(pageSize);
                setAssetPage(page);
              },
            }}
            onRow={(record) => ({
              onClick: (event) => {
                const target = event.target as HTMLElement;
                if (target.closest(".ant-checkbox-wrapper")) return;
                toggleAsset(record.path, !draft.selectedPaths.includes(record.path));
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
        {draft.selectedPaths.some(
          (path) =>
            assetTree.data?.assets.find((asset) => asset.path === path)?.metadata.prerequisites?.length,
        ) ? (
          <Alert
            type="warning"
            showIcon
            style={{ marginTop: 14 }}
            message="所选资产包含运行时前置依赖"
            description="OpenSpec 生成型 Command 需要目标沙箱提供 openspec CLI；快照会记录前置依赖，但不会静默授予宿主机执行权限。"
          />
        ) : null}
        {blockerFor("asset_snapshot_id") ? (
          <span className="field-error">{blockerFor("asset_snapshot_id")?.message}</span>
        ) : null}
      </div>
    );
  }

  function renderPolicyStep() {
    const online = selectedPool?.runners.online ?? 0;
    const capacityWarning = selectedPool ? online < draft.concurrency : false;
    const modelOptions = models.data ?? [];
    const poolOptions = pools.data ?? [];
    return (
      <div>
        <div className="eyebrow">Step 3 · Model &amp; capacity</div>
        <h2 className="wizard-title">选择模型凭据与资源池</h2>
        <p className="small muted wizard-subtitle">
          可引用已保存凭据；临时 Key 不在本向导中输入，也不会写入浏览器存储。
        </p>
        {createNotice ? (
          <Alert
            type="error"
            showIcon
            closable
            message={createNotice.title}
            description={createNotice.description}
            onClose={() => setCreateNotice(null)}
            style={{ marginBottom: 14 }}
          />
        ) : null}
        <h3 className="wizard-section-title">模型凭据</h3>
        {modelOptions.length ? (
          <div className="radio-list">
            {modelOptions.map((item) => {
              const selected = draft.modelConfigId === item.id;
              const revoked = Boolean(item.credential?.revoked_at);
              return (
                <button
                  key={item.id}
                  type="button"
                  className={`radio-row ${selected ? "selected" : ""}`}
                  onClick={() => update({ modelConfigId: item.id })}
                >
                  <span className={`radio ${selected ? "selected" : ""}`} />
                  <span className="radio-copy">
                    <strong>
                      {item.display_name} · {item.model_name}
                    </strong>
                    <span className="small muted">
                      {item.provider} · {item.api_style} ·{" "}
                      {revoked ? "凭据已撤销" : "凭据已加密，Runner 只接收短期凭据"}
                    </span>
                  </span>
                  <span className={`tag ${revoked ? "tag-danger" : "tag-success"}`}>
                    {revoked ? "已撤销" : "可用"}
                  </span>
                </button>
              );
            })}
          </div>
        ) : (
          <Alert
            type="warning"
            showIcon
            message="还没有模型配置"
            description="先到模型凭据页添加模型配置与加密凭据，再回到这里发起评测。"
            action={
              <Button size="small" onClick={() => navigate("/models")}>
                去配置
              </Button>
            }
          />
        )}
        {selectedModel?.credential?.revoked_at ? (
          <span className="field-error">
            所选模型配置的凭据已撤销，一致性检查会阻塞；请先在「模型凭据」页替换 Key。
          </span>
        ) : null}
        <h3 className="wizard-section-title">执行资源</h3>
        <p className="small muted wizard-section-hint">按标签路由到匹配 Runner</p>
        {poolOptions.length ? (
          <div className="radio-list">
            {poolOptions.map((pool) => {
              const selected = draft.poolId === pool.id;
              const poolOnline = pool.runners.online;
              const unavailable = pool.status !== "active";
              return (
                <button
                  key={pool.id}
                  type="button"
                  disabled={unavailable}
                  className={`radio-row ${selected ? "selected" : ""}`}
                  onClick={() => update({ poolId: pool.id })}
                >
                  <span className={`radio ${selected ? "selected" : ""}`} />
                  <span className="radio-copy">
                    <strong>{pool.name}</strong>
                    <span className="small muted">
                      {poolOnline} 在线 / {pool.max_capacity > 0 ? pool.max_capacity : "—"} 上限 ·{" "}
                      {pool.provider_key}
                      {unavailable ? " · 不可用" : ""}
                    </span>
                  </span>
                  <span
                    className={`tag ${
                      unavailable ? "tag-neutral" : poolOnline > 0 ? "tag-primary" : "tag-warning"
                    }`}
                  >
                    {unavailable ? "不可用" : poolOnline > 0 ? `${poolOnline} 路可用` : "无空闲 Runner"}
                  </span>
                </button>
              );
            })}
          </div>
        ) : (
          <Alert
            type="warning"
            showIcon
            message="还没有资源池"
            description="先到资源池页创建池并等待 Runner 上线。"
            action={
              <Button size="small" onClick={() => navigate("/resources")}>
                去配置
              </Button>
            }
          />
        )}
        <div className="banner">
          <Icon name="cpu" size={15} />
          <div>按当前在线容量与执行策略估算；确认页会给出费用与耗时区间。队列等待时间不计入用例超时。</div>
        </div>
        {blockerFor("resource_pool_id") ? (
          <span className="field-error">{blockerFor("resource_pool_id")?.message}</span>
        ) : null}
        <div
          className="row-between"
          style={{ marginTop: 18, paddingTop: 14, borderTop: "1px solid var(--line)" }}
        >
          <div>
            <h3 style={{ margin: 0, fontSize: 13, fontWeight: 700, color: "var(--ink)" }}>
              附加指令（可选）
            </h3>
            <p className="small muted" style={{ margin: "2px 0 0" }}>
              追加到每个用例的题面之后，启动时冻结进评测快照
            </p>
          </div>
          <Tag style={{ marginInlineEnd: 0 }}>可选</Tag>
        </div>
        <div className="field" style={{ marginTop: 12 }}>
          <Input.TextArea
            value={draft.promptSuffix}
            onChange={(event) =>
              update({ promptSuffix: event.target.value.slice(0, MAX_PROMPT_SUFFIX) })
            }
            placeholder={PROMPT_SUFFIX_PLACEHOLDER}
            rows={4}
            maxLength={MAX_PROMPT_SUFFIX}
            showCount
            className="mono"
          />
          <span className="hint">
            原样追加到数据集的 problem_statement 之后；上限 4000 字符。评测配置与轨迹会保留该文本。
          </span>
        </div>
        <div className="banner" style={{ marginTop: 2 }}>
          <Icon name="sparkles" size={15} />
          <div>
            <strong>示例</strong> · “非交互评测，请勿向用户提问；按需使用 superpowers 技能（using-superpowers、test-driven-development 等）完成任务。”
          </div>
        </div>
        <h3 className="wizard-section-title">高级执行策略</h3>
        <div className="policy-grid">
          <div className="field">
            <label className="required">并发上限</label>
            <InputNumber
              id="wizard-concurrency"
              min={1}
              max={256}
              value={draft.concurrency}
              onChange={(value) => update({ concurrency: Math.max(1, Math.min(value ?? 1, 256)) })}
              style={{ width: "100%" }}
            />
            <span className="hint">超出在线容量时排队，不承诺秒级启动。</span>
            {blockerFor("requested_concurrency") ? (
              <span className="field-error">{blockerFor("requested_concurrency")?.message}</span>
            ) : null}
          </div>
          <div className="field">
            <label className="required">单用例超时</label>
            <Select
              id="wizard-timeout"
              value={draft.caseTimeoutSeconds}
              onChange={(value) => update({ caseTimeoutSeconds: value })}
              options={TIMEOUT_OPTIONS}
            />
            <span className="hint">超时按失败处理并受重试策略约束。</span>
            {blockerFor("case_timeout_seconds") ? (
              <span className="field-error">{blockerFor("case_timeout_seconds")?.message}</span>
            ) : null}
          </div>
          <div className="field">
            <label className="required">失败重试次数</label>
            <Select
              id="wizard-retries"
              value={draft.maxAttempts - 1}
              onChange={(value) => update({ maxAttempts: value + 1 })}
              options={[0, 1, 2, 3, 4].map((value) => ({
                value,
                label: value === 0 ? "不重试" : `重试 ${value} 次`,
              }))}
            />
            <span className="hint">总尝试次数 = 1 + 重试次数，旧 attempt 轨迹保留。</span>
            {blockerFor("max_attempts") ? (
              <span className="field-error">{blockerFor("max_attempts")?.message}</span>
            ) : null}
          </div>
          <div className="field">
            <label>模型预算（CNY，可选）</label>
            <InputNumber
              id="wizard-budget"
              min={0}
              step={1}
              value={draft.budgetCny}
              onChange={(value) => update({ budgetCny: value === null ? null : Math.max(0, value) })}
              placeholder="不设置则不暂停"
              style={{ width: "100%" }}
            />
            <span className="hint">达到预算时评测暂停；确认追加预算后从未完成用例继续。</span>
            {blockerFor("model_budget_cny") ? (
              <span className="field-error">{blockerFor("model_budget_cny")?.message}</span>
            ) : null}
          </div>
        </div>
        <div className="field-row wizard-digest-row">
          <div className="field">
            <label>执行镜像 digest</label>
            <Input
              value={draft.executionDigest}
              onChange={(event) => update({ executionDigest: event.target.value })}
              placeholder="sha256:…"
            />
          </div>
          <div className="field">
            <label>评分镜像 digest</label>
            <Input
              value={draft.scoringDigest}
              onChange={(event) => update({ scoringDigest: event.target.value })}
              placeholder="sha256:…"
            />
          </div>
        </div>
        {capacityWarning ? (
          <Alert
            type="warning"
            showIcon
            message={`当前在线 ${online} 个 Runner，请求并发 ${draft.concurrency}，评测将排队等待`}
            description="准备快照会记录容量警告；不会因为排队而判定失败。"
          />
        ) : null}
        {blockerFor("dataset_version_id") ? (
          <span className="field-error">{blockerFor("dataset_version_id")?.message}</span>
        ) : null}
        {blockerFor("case_selection") ? (
          <span className="field-error">{blockerFor("case_selection")?.message}</span>
        ) : null}
        {blockers.length && !checking ? (
          <div className="wizard-blockers">
            <Alert
              type="error"
              showIcon
              message={`准备快照存在 ${blockers.length} 个阻塞项`}
              description="修正以下配置后重新准备；阻塞项不会被静默忽略。"
            />
            {blockers.map((issue) => (
              <Alert
                key={`${issue.code}-${issue.field ?? ""}`}
                type="error"
                showIcon
                message={issue.code}
                description={issue.message}
              />
            ))}
          </div>
        ) : null}
      </div>
    );
  }

  function renderConfirmStep() {
    if (!preparation || !summary) {
      return (
        <Empty description="还没有准备快照，请返回上一步重新准备">
          <Button onClick={() => setStep(2)}>返回执行策略</Button>
        </Empty>
      );
    }
    return (
      <div>
        <div className="eyebrow">Step 4 · Review</div>
        <h2 className="wizard-title">确认并启动评测</h2>
        <p className="small muted wizard-subtitle">
          启动后平台会创建评测记录并拆分为多个 GitHub Actions 分片；创建按钮是幂等操作。
        </p>
        <div className="review-box">
          <div className="summary-item">
            <span className="summary-label">评测名称</span>
            <span className="summary-value wizard-name-input">
              <Input
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder={`留空自动命名：${summary.dataset.name} · ${summary.dataset.version}`}
              />
            </span>
          </div>
          <div className="summary-item">
            <span className="summary-label">数据集</span>
            <span className="summary-value">
              {summary.dataset.name} · {summary.dataset.version}
            </span>
          </div>
          <div className="summary-item">
            <span className="summary-label">用例与 Job</span>
            <span className="summary-value">
              {summary.case_count} 个用例 / {summary.estimated_job_count} 个 Job
            </span>
          </div>
          <div className="summary-item">
            <span className="summary-label">Agent</span>
            <span className="summary-value">
              {summary.runtime.key} {summary.runtime.version ?? ""}
            </span>
          </div>
          <div className="summary-item">
            <span className="summary-label">Agent 资产</span>
            <span className="summary-value">
              {summary.asset_snapshot
                ? `${summary.asset_snapshot.asset_count ?? 0} 项 · @${(summary.asset_snapshot.resolved_commit ?? "").slice(0, 12)}`
                : "未选择（只使用 Runtime 内置能力）"}
            </span>
          </div>
          <div className="summary-item">
            <span className="summary-label">模型与凭据</span>
            <span className="summary-value">
              {summary.model.display_name ?? "—"} · {summary.model.model_name ?? "—"}
            </span>
          </div>
          <div className="summary-item">
            <span className="summary-label">资源池与容量</span>
            <span className="summary-value">
              {summary.resource_pool.name ?? "—"} · {summary.resource_pool.online_runners} 在线 /{" "}
              {summary.resource_pool.max_capacity ?? "—"} 上限
            </span>
          </div>
          <div className="summary-item">
            <span className="summary-label">执行策略</span>
            <span className="summary-value">
              并发 {summary.execution.concurrency} · 超时{" "}
              {formatMinutes(summary.execution.case_timeout_seconds)} · 重试{" "}
              {summary.execution.max_attempts - 1} 次
              {summary.execution.model_budget_cny !== null
                ? ` · 预算 ${formatMoney(summary.execution.model_budget_cny)}`
                : ""}
            </span>
          </div>
          <div className="summary-item">
            <span className="summary-label">附加指令</span>
            <span className="summary-value" style={{ whiteSpace: "pre-wrap" }}>
              {promptSuffix || "未设置"}
            </span>
          </div>
          <div className="summary-item">
            <span className="summary-label">评分镜像 digest</span>
            <span className="summary-value mono small">
              {summary.resolved.scoring_image_digest ?? "—"}
            </span>
          </div>
          <div className="summary-item">
            <span className="summary-label">准备快照</span>
            <span className="summary-value mono small">
              {preparation.snapshot_digest?.slice(0, 23) ?? "—"}
            </span>
          </div>
        </div>
        <div className={`banner ${expired ? "danger" : "warning"}`} style={{ marginTop: 14 }}>
          <Icon name={expired ? "alert" : "clock"} size={15} />
          <div>
            {expired ? (
              <strong>准备快照已过期。</strong>
            ) : (
              <>
                准备快照将在 <strong>{formatCountdown(remainingSeconds)}</strong> 后过期；修改任何配置都需要重新准备。
              </>
            )}
          </div>
        </div>
        {preparation.warnings.length ? (
          <div className="wizard-warnings">
            {preparation.warnings.map((issue) => (
              <Alert
                key={`${issue.code}-${issue.field ?? ""}`}
                type="warning"
                showIcon
                message={issue.code}
                description={issue.message}
              />
            ))}
          </div>
        ) : null}
        <div className="banner warning" style={{ marginTop: 14 }}>
          <Icon name="alert" size={15} />
          <div>
            <strong>这会占用资源池并产生模型费用。</strong>{" "}
            {costEstimate ? (
              <>
                预计 {formatMoneyRange(costEstimate.total_cny.low, costEstimate.total_cny.high)}（{costEstimate.basis}）；预计总耗时{" "}
                {formatDuration(costEstimate.estimated_duration_seconds)}。
              </>
            ) : (
              <>该模型配置未填写定价，费用以模型网关实际记账为准。</>
            )}{" "}
            启动后仍可取消排队和执行中的用例。
          </div>
        </div>
      </div>
    );
  }

  return (
    <section className="page eval-wizard">
      <PageHeading
        eyebrow="New evaluation"
        title="发起评测"
        description="组合数据集、Agent 资产、模型、资源池与执行策略；准备快照会冻结全部输入，创建是幂等操作。"
        actions={<Button onClick={() => navigate("/evals")}>取消</Button>}
      />
      {renderStepBar()}
      <div className="wizard-layout">
        <section className="card wizard-main">
          <div className="card-body">
            {step === 0 ? renderDatasetStep() : null}
            {step === 1 ? renderAssetsStep() : null}
            {step === 2 ? renderPolicyStep() : null}
            {step === 3 ? renderConfirmStep() : null}
            <div className="wizard-footer">
              <Button onClick={goBack}>{step === 0 ? "取消" : "上一步"}</Button>
              {step < 2 ? (
                <Button type="primary" onClick={goNext}>
                  下一步 <Icon name="arrowRight" size={13} />
                </Button>
              ) : null}
              {step === 2 ? (
                <Button type="primary" loading={checking} onClick={runPrepare} disabled={!canPrepare}>
                  准备并检查
                </Button>
              ) : null}
              {step === 3 ? (
                <Button
                  type="primary"
                  size="large"
                  loading={create.isPending}
                  disabled={!preparation || expired}
                  onClick={() => create.mutate()}
                >
                  <Icon name="play" size={14} /> 创建评测
                </Button>
              ) : null}
            </div>
          </div>
        </section>
        {renderAside()}
      </div>
    </section>
  );
}
