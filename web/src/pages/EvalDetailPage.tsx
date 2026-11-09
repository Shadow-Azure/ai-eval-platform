import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Card, Descriptions, Drawer, Progress, Space, Statistic, Table, Tag, Timeline, message } from "antd";
import type { TableColumnsType } from "antd";
import { useNavigate, useParams } from "react-router-dom";
import {
  TERMINAL_EVAL_STATUSES,
  cancelEvaluation,
  fetchCaseTests,
  fetchEvaluationDetail,
  fetchEvaluationEvents,
  fetchEvaluationResults,
  streamEvaluationEvents,
  type CaseTestResult,
  type EvaluationCase,
  type EvaluationEvent,
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
  if (status === "running") return "processing";
  return "default";
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

  const terminal = terminalStatus(detail.data?.status);

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
