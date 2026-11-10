import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Card, Descriptions, Drawer, InputNumber, Modal, Progress, Space, Statistic, Table, Tag, Timeline, message } from "antd";
import type { TableColumnsType } from "antd";
import { useNavigate, useParams } from "react-router-dom";
import {
  TERMINAL_EVAL_STATUSES,
  cancelEvaluation,
  fetchCaseTests,
  fetchEvaluationDetail,
  fetchEvaluationEvents,
  fetchEvaluationResults,
  resumeEvaluationBudget,
  streamEvaluationEvents,
  type CaseTestResult,
  type EvaluationCase,
  type EvaluationEvent,
  type FrozenAsset,
} from "../api/evaluations";
import { PageHeading } from "../components/PageHeading";
import { QueryError } from "../components/QueryError";

const statusLabels: Record<string, string> = {
  queued: "排队中",
  running: "运行中",
  completed: "已完成",
  partial: "部分成功",
  failed: "失败",
  cancelled: "已取消",
  pausing_budget: "预算暂停中",
  paused_budget: "预算已暂停",
};

function statusColor(status: string): string {
  if (status === "completed") return "success";
  if (status === "partial" || status === "paused_budget") return "warning";
  if (status === "failed") return "error";
  if (status === "running" || status === "pausing_budget") return "processing";
  return "default";
}

function formatUsd(value: string | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return value;
  if (parsed === 0) return "$0.00";
  return `$${parsed.toFixed(parsed < 1 ? 4 : 2)}`;
}

function formatMetric(value: unknown, kind: string): string {
  if (value === null || value === undefined) return "N/A";
  if (kind === "percentage" && typeof value === "number") return `${(value * 100).toFixed(1)}%`;
  if (typeof value === "number") return value.toFixed(2).replace(/\.00$/, "");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

const caseColumns: TableColumnsType<EvaluationCase> = [
  { title: "#", dataIndex: "ordinal", key: "ordinal", width: 56 },
  { title: "用例", dataIndex: "case_key", key: "case_key" },
  { title: "执行", dataIndex: "execution_status", key: "execution_status", render: (value: string) => <Tag>{value}</Tag> },
  { title: "结果", dataIndex: "result_status", key: "result_status", render: (value: string) => <Tag color={value === "resolved" ? "success" : value === "unresolved" ? "warning" : "default"}>{value}</Tag> },
  { title: "attempt", dataIndex: "latest_attempt_no", key: "latest_attempt_no", width: 80 },
  { title: "耗时", dataIndex: "duration_ms", key: "duration_ms", render: (value: number | null) => (value ? `${(value / 1000).toFixed(1)}s` : "—") },
];

const frozenAssetColumns: TableColumnsType<FrozenAsset> = [
  { title: "类型", dataIndex: "asset_type", key: "asset_type", width: 100, render: (value: string) => <Tag>{value}</Tag> },
  { title: "资产", dataIndex: "name", key: "name" },
  { title: "路径", dataIndex: "path", key: "path", render: (value: string) => <span className="mono small">{value}</span> },
  { title: "摘要", dataIndex: "content_digest", key: "content_digest", width: 150, render: (value: string | undefined) => <span className="mono small">{value ? value.replace(/^sha256:/, "").slice(0, 12) : "—"}</span> },
];

function shortDigest(value: string | null | undefined): string {
  if (!value) return "—";
  return value.replace(/^sha256:/, "").slice(0, 12);
}

interface FrozenFile {
  path?: unknown;
  sha256?: unknown;
  size?: unknown;
}

function frozenFiles(record: FrozenAsset): Array<{ path: string; sha256: string; size: number }> {
  const raw = record.metadata?.files;
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((item): item is FrozenFile => typeof item === "object" && item !== null)
    .map((item) => ({
      path: String(item.path ?? ""),
      sha256: String(item.sha256 ?? ""),
      size: typeof item.size === "number" ? item.size : 0,
    }))
    .filter((item) => Boolean(item.path));
}

const testColumns: TableColumnsType<CaseTestResult> = [
  { title: "组", dataIndex: "test_group", key: "test_group", width: 64 },
  { title: "测试", dataIndex: "test_name", key: "test_name" },
  { title: "结果", dataIndex: "outcome", key: "outcome", render: (value: string) => <Tag color={value === "passed" ? "success" : value === "failed" ? "error" : "default"}>{value}</Tag> },
  { title: "原生状态", dataIndex: "native_status", key: "native_status" },
];

export function EvalDetailPage() {
  const { runId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [events, setEvents] = useState<EvaluationEvent[]>([]);
  const [selectedCase, setSelectedCase] = useState<EvaluationCase | null>(null);
  const [resumeOpen, setResumeOpen] = useState(false);
  const [additionalBudget, setAdditionalBudget] = useState<number>(10);
  const cursor = useRef(0);

  const detail = useQuery({
    queryKey: ["evaluation", runId],
    queryFn: ({ signal }) => fetchEvaluationDetail(runId, signal),
    enabled: Boolean(runId),
  });
  const results = useQuery({
    queryKey: ["evaluation", runId, "results"],
    queryFn: ({ signal }) => fetchEvaluationResults(runId, signal),
    enabled: Boolean(runId),
  });
  const snapshot = useQuery({
    queryKey: ["evaluation", runId, "events"],
    queryFn: ({ signal }) => fetchEvaluationEvents(runId, signal),
    enabled: Boolean(runId),
  });
  const tests = useQuery({
    queryKey: ["case", selectedCase?.id, "tests"],
    queryFn: ({ signal }) => fetchCaseTests(selectedCase?.id ?? "", signal),
    enabled: Boolean(selectedCase),
  });
  const cancel = useMutation({
    mutationFn: () => cancelEvaluation(runId),
    onSuccess: () => {
      message.success("已请求取消");
      queryClient.invalidateQueries({ queryKey: ["evaluation", runId] });
    },
    onError: (error) => message.error(error instanceof Error ? error.message : "取消失败"),
  });
  const resumeBudget = useMutation({
    mutationFn: () =>
      resumeEvaluationBudget(runId, {
        additional_budget_usd: additionalBudget,
        budget_version: detail.data?.budget_version ?? 1,
      }),
    onSuccess: (result) => {
      setResumeOpen(false);
      message.success(`已增加预算，重新调度 ${result.attempts_created} 个未完成用例`);
      queryClient.invalidateQueries({ queryKey: ["evaluation", runId] });
    },
    onError: (error) => {
      message.error(error instanceof Error ? error.message : "恢复失败");
      // A stale budget version is the expected parallel/double-click race;
      // refresh the dialog so the next confirmation uses the current version.
      queryClient.invalidateQueries({ queryKey: ["evaluation", runId] });
    },
  });

  const terminal = terminalStatus(detail.data?.status);
  const budgetUsd = detail.data?.model_budget_usd;
  const hasBudget = budgetUsd !== null && budgetUsd !== undefined;
  const pausedBudget = detail.data?.status === "paused_budget";
  const pausingBudget = detail.data?.status === "pausing_budget";
  const nextBudgetUsd =
    budgetUsd !== null && budgetUsd !== undefined
      ? (Number(budgetUsd) + additionalBudget).toFixed(2)
      : "—";
  const unfinishedCount =
    detail.data?.unfinished_count ??
    (detail.data?.queued_count ?? 0) + (detail.data?.running_count ?? 0);

  useEffect(() => {
    if (!snapshot.data) return;
    setEvents(snapshot.data.items);
    cursor.current = snapshot.data.items.reduce((max, item) => Math.max(max, item.id), 0);
  }, [snapshot.data]);

  useEffect(() => {
    if (!runId || terminal) return;
    const controller = new AbortController();
    void streamEvaluationEvents(
      runId,
      cursor.current,
      (event) => {
        cursor.current = Math.max(cursor.current, event.id);
        setEvents((current) => (current.some((item) => item.id === event.id) ? current : [...current, event]));
        queryClient.invalidateQueries({ queryKey: ["evaluation", runId] });
      },
      controller.signal,
    ).catch((error: unknown) => {
      if (error instanceof DOMException && error.name === "AbortError") return;
    });
    return () => controller.abort();
  }, [runId, terminal, queryClient]);

  const progress = useMemo(() => {
    const total = detail.data?.total_cases ?? 0;
    if (!total) return 0;
    const finished = (detail.data?.succeeded_count ?? 0) + (detail.data?.failed_count ?? 0);
    return Math.round((finished / total) * 100);
  }, [detail.data]);

  if (detail.error) {
    return <section className="page"><QueryError error={detail.error} onRetry={() => detail.refetch()} /></section>;
  }

  return (
    <section className="page">
      <PageHeading
        eyebrow="Evaluation · Detail"
        title={detail.data?.name ?? "评测详情"}
        description={`run ${runId} · ${detail.data?.dataset_version_id ?? "合成评测"} · ${detail.data?.runtime_key ?? "synthetic"}`}
        actions={
          <Space>
            <Button onClick={() => navigate("/evals")}>返回列表</Button>
            {pausedBudget ? (
              <Button type="primary" onClick={() => setResumeOpen(true)}>
                增加预算并恢复
              </Button>
            ) : null}
            {!terminal ? (
              <Button danger loading={cancel.isPending} onClick={() => cancel.mutate()}>取消评测</Button>
            ) : null}
          </Space>
        }
      />
      <Card style={{ marginBottom: 16 }}>
        <Space size="large" align="center" wrap>
          <Tag color={statusColor(detail.data?.status ?? "queued")}>{statusLabels[detail.data?.status ?? ""] ?? detail.data?.status}</Tag>
          <Progress percent={progress} style={{ width: 260 }} />
          <Statistic title="总用例" value={detail.data?.total_cases ?? 0} />
          <Statistic title="成功" value={detail.data?.succeeded_count ?? 0} />
          <Statistic title="失败" value={detail.data?.failed_count ?? 0} />
          <Statistic title="运行中" value={detail.data?.running_count ?? 0} />
        </Space>
      </Card>

      {detail.data?.asset_snapshot ? (
        <Card
          title="Agent 资产快照"
          style={{ marginBottom: 16 }}
          extra={
            <span className="mono small muted">
              {detail.data.asset_snapshot.snapshot_id ?? ""}
            </span>
          }
        >
          <Descriptions size="small" column={4} style={{ marginBottom: 12 }}>
            <Descriptions.Item label="请求 Ref">{detail.data.asset_snapshot.requested_ref ?? "—"}</Descriptions.Item>
            <Descriptions.Item label="解析 commit">
              <span className="mono">{detail.data.asset_snapshot.resolved_commit ?? "—"}</span>
            </Descriptions.Item>
            <Descriptions.Item label="资产 / 文件">
              {detail.data.asset_snapshot.asset_count ?? 0} / {detail.data.asset_snapshot.file_count ?? 0}
            </Descriptions.Item>
            <Descriptions.Item label="树摘要">
              <span className="mono">{shortDigest(detail.data.asset_snapshot.tree_digest)}</span>
            </Descriptions.Item>
            <Descriptions.Item label="Runtime">{detail.data.asset_snapshot.runtime_key ?? "—"}</Descriptions.Item>
            <Descriptions.Item label="包大小">{detail.data.asset_snapshot.bundle_size ?? 0} B</Descriptions.Item>
            <Descriptions.Item label="包摘要">
              <span className="mono">{shortDigest(detail.data.asset_snapshot.bundle_digest)}</span>
            </Descriptions.Item>
          </Descriptions>
          <Table<FrozenAsset>
            rowKey={(record) => `${record.asset_type}:${record.path}`}
            size="small"
            columns={frozenAssetColumns}
            dataSource={detail.data.asset_snapshot.assets ?? []}
            pagination={false}
            expandable={{
              rowExpandable: (record) => frozenFiles(record).length > 0,
              expandedRowRender: (record) => (
                <div className="asset-file-list">
                  {frozenFiles(record).map((file) => (
                    <div className="asset-file-row" key={file.path}>
                      <span className="mono">{file.path}</span>
                      <span className="mono muted">
                        {shortDigest(file.sha256)} · {file.size} B
                      </span>
                    </div>
                  ))}
                </div>
              ),
            }}
          />
        </Card>
      ) : null}

      {hasBudget ? (
        <Card title="模型预算" style={{ marginBottom: 16 }}>
          {pausingBudget ? (
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 16 }}
              message="预算暂停中"
              description={`调度器已停止派发新分片，正在等待运行中的 ${detail.data?.running_count ?? 0} 个用例上传已有产物并安全退出；收口后即可增加预算恢复。`}
            />
          ) : null}
          {pausedBudget ? (
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 16 }}
              message="预算已暂停，评测尚未结束"
              description={`已花费 ${formatUsd(detail.data?.budget_spent_usd)} / 预算 ${formatUsd(budgetUsd)}，${unfinishedCount} 个用例未完成。增加预算后只会重新调度未完成用例；已完成用例的结果与成本保持不变。`}
              action={
                <Button type="primary" size="small" onClick={() => setResumeOpen(true)}>
                  增加预算并恢复
                </Button>
              }
            />
          ) : null}
          <Descriptions size="small" column={4}>
            <Descriptions.Item label="模型预算">{formatUsd(budgetUsd)}</Descriptions.Item>
            <Descriptions.Item label="已花费">{formatUsd(detail.data?.budget_spent_usd)}</Descriptions.Item>
            <Descriptions.Item label="剩余">{formatUsd(detail.data?.budget_remaining_usd)}</Descriptions.Item>
            <Descriptions.Item label="预算版本">v{detail.data?.budget_version ?? 1}</Descriptions.Item>
            <Descriptions.Item label="已完成">{detail.data?.finished_count ?? 0}</Descriptions.Item>
            <Descriptions.Item label="未完成">{unfinishedCount}</Descriptions.Item>
            {pausedBudget ? <Descriptions.Item label="暂停原因">模型预算用尽</Descriptions.Item> : null}
          </Descriptions>
        </Card>
      ) : null}

      <Card title="官方口径结果" style={{ marginBottom: 16 }}>
        {results.error ? <QueryError error={results.error} onRetry={() => results.refetch()} /> : null}
        {results.data?.items.length ? (
          <Space size="large" wrap>
            {results.data.items.map((item) => (
              <Statistic
                key={item.metric_key}
                title={item.display_name}
                value={formatMetric(item.value, item.display_kind)}
                suffix={item.is_final ? undefined : "（运行中）"}
              />
            ))}
          </Space>
        ) : (
          <Alert type="info" showIcon message="尚无结果快照；用例完成后自动生成。" />
        )}
      </Card>

      <Card title="用例" style={{ marginBottom: 16 }}>
        <Table<EvaluationCase>
          rowKey="id"
          size="small"
          loading={detail.isLoading}
          columns={caseColumns}
          dataSource={detail.data?.cases ?? []}
          pagination={{ pageSize: 20 }}
          onRow={(record) => ({ onClick: () => setSelectedCase(record), style: { cursor: "pointer" } })}
        />
      </Card>

      <Card title="事件时间线">
        <Timeline
          items={events.map((event) => ({
            key: event.id,
            color: event.event_type.includes("failed") ? "red" : event.event_type.includes("completed") ? "green" : "blue",
            children: (
              <div>
                <div><strong>{event.event_type}</strong></div>
                <div className="muted small">{event.occurred_at ?? ""}</div>
              </div>
            ),
          }))}
        />
      </Card>

      <Modal
        open={resumeOpen}
        title="增加模型预算并恢复"
        okText="确认增加并恢复"
        cancelText="取消"
        confirmLoading={resumeBudget.isPending}
        okButtonProps={{ disabled: !(additionalBudget > 0) }}
        onOk={() => resumeBudget.mutate()}
        onCancel={() => setResumeOpen(false)}
      >
        <Alert
          type="info"
          showIcon
          style={{ marginTop: 8, marginBottom: 16 }}
          message="只重新调度未完成用例"
          description="已完成用例不会重跑，被预算打断的用例会创建新 attempt；暂停期间不会切换模型或资源池。"
        />
        <Descriptions size="small" column={2} style={{ marginBottom: 16 }}>
          <Descriptions.Item label="当前预算">{formatUsd(budgetUsd)}</Descriptions.Item>
          <Descriptions.Item label="已花费">{formatUsd(detail.data?.budget_spent_usd)}</Descriptions.Item>
          <Descriptions.Item label="未完成用例">{unfinishedCount}</Descriptions.Item>
          <Descriptions.Item label="预算版本">v{detail.data?.budget_version ?? 1}</Descriptions.Item>
        </Descriptions>
        <div style={{ marginBottom: 6 }}>新增预算（USD）</div>
        <InputNumber
          min={0.01}
          step={1}
          precision={2}
          value={additionalBudget}
          onChange={(value) => setAdditionalBudget(typeof value === "number" ? value : 0)}
          style={{ width: "100%" }}
        />
        <div className="muted small" style={{ marginTop: 8 }}>
          恢复后总预算约 {nextBudgetUsd} USD；实际费用仍以模型网关记录为准。
        </div>
      </Modal>

      <Drawer
        open={Boolean(selectedCase)}
        onClose={() => setSelectedCase(null)}
        title={selectedCase ? `${selectedCase.case_key} · attempt ${tests.data?.attempt ?? selectedCase.latest_attempt_no}` : ""}
        width={880}
      >
        {selectedCase ? (
          <>
            <Descriptions size="small" column={2} style={{ marginBottom: 16 }}>
              <Descriptions.Item label="执行">{selectedCase.execution_status}</Descriptions.Item>
              <Descriptions.Item label="结果">{selectedCase.result_status}</Descriptions.Item>
              <Descriptions.Item label="错误">{selectedCase.error_summary ?? "—"}</Descriptions.Item>
            </Descriptions>
            {tests.data?.summary ? (
              <Space wrap style={{ marginBottom: 12 }}>
                {Object.entries(tests.data.summary).map(([key, value]) => (
                  <Tag key={key}>{key}: {value}</Tag>
                ))}
              </Space>
            ) : null}
            <Table<CaseTestResult>
              rowKey="id"
              size="small"
              loading={tests.isLoading}
              columns={testColumns}
              dataSource={tests.data?.items ?? []}
              pagination={{ pageSize: 20 }}
            />
          </>
        ) : null}
      </Drawer>
    </section>
  );
}

function terminalStatus(status: string | undefined): boolean {
  return status ? TERMINAL_EVAL_STATUSES.includes(status) : false;
}
