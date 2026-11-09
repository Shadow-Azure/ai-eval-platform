import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button, Card, Descriptions, Space, Table, Tag } from "antd";
import type { TableColumnsType } from "antd";
import { useNavigate, useParams } from "react-router-dom";
import {
  fetchDataset,
  fetchDatasetCases,
  fetchDatasetVersion,
  fetchDatasetVersions,
  type DatasetCaseSummary,
  type DatasetVersion,
  type MetricDefinition,
} from "../api/datasets";
import { PageHeading } from "../components/PageHeading";
import { QueryError } from "../components/QueryError";

const versionColumns: TableColumnsType<DatasetVersion> = [
  { title: "版本", dataIndex: "version", key: "version" },
  { title: "用例数", dataIndex: "case_count", key: "case_count", width: 96 },
  { title: "来源修订", dataIndex: "source_revision", key: "source_revision", ellipsis: true },
  { title: "状态", dataIndex: "status", key: "status", render: (value: string) => <Tag>{value}</Tag> },
  { title: "创建时间", dataIndex: "created_at", key: "created_at", render: (value: string) => new Date(value).toLocaleString("zh-CN") },
];

const caseColumns: TableColumnsType<DatasetCaseSummary> = [
  { title: "#", dataIndex: "ordinal", key: "ordinal", width: 56 },
  { title: "case_key", dataIndex: "case_key", key: "case_key" },
  { title: "repo", dataIndex: "repo", key: "repo" },
  { title: "base_commit", dataIndex: "base_commit", key: "base_commit", className: "mono" },
];

const metricColumns: TableColumnsType<MetricDefinition> = [
  { title: "指标", dataIndex: "display_name", key: "display_name" },
  { title: "key", dataIndex: "metric_key", key: "metric_key", className: "mono" },
  { title: "口径", dataIndex: "formula_text", key: "formula_text", ellipsis: true },
  { title: "版本", dataIndex: "version", key: "version", width: 72 },
];

export function DatasetDetailPage() {
  const { datasetId = "" } = useParams();
  const navigate = useNavigate();
  const [selectedVersion, setSelectedVersion] = useState<string | null>(null);
  const dataset = useQuery({ queryKey: ["dataset", datasetId], queryFn: ({ signal }) => fetchDataset(datasetId, signal) });
  const versions = useQuery({
    queryKey: ["dataset", datasetId, "versions"],
    queryFn: ({ signal }) => fetchDatasetVersions(datasetId, signal),
  });
  const versionId = selectedVersion ?? versions.data?.[0]?.id ?? "";
  const version = useQuery({
    queryKey: ["dataset-version", versionId],
    queryFn: ({ signal }) => fetchDatasetVersion(versionId, signal),
    enabled: Boolean(versionId),
  });
  const cases = useQuery({
    queryKey: ["dataset-version", versionId, "cases"],
    queryFn: ({ signal }) => fetchDatasetCases(versionId, signal),
    enabled: Boolean(versionId),
  });

  return (
    <section className="page">
      <PageHeading
        eyebrow="Configuration · Datasets"
        title={dataset.data?.name ?? "数据集详情"}
        description={`适配器 ${dataset.data?.adapter_key ?? "—"} · slug ${dataset.data?.slug ?? "—"}`}
        actions={
          <Button type="primary" disabled={!versionId} onClick={() => navigate(`/evals/new?datasetVersionId=${versionId}`)}>
            用此版本发起评测
          </Button>
        }
      />
      {dataset.error ? <QueryError error={dataset.error} onRetry={() => dataset.refetch()} /> : null}
      <Card title="版本" style={{ marginBottom: 16 }}>
        <Table<DatasetVersion>
          rowKey="id"
          size="small"
          loading={versions.isLoading}
          columns={versionColumns}
          dataSource={versions.data ?? []}
          pagination={false}
          rowClassName={(record) => (record.id === versionId ? "ant-table-row-selected" : "")}
          onRow={(record) => ({ onClick: () => setSelectedVersion(record.id), style: { cursor: "pointer" } })}
        />
      </Card>
      {version.data ? (
        <Card title="版本摘要" style={{ marginBottom: 16 }}>
          <Descriptions size="small" column={3}>
            <Descriptions.Item label="来源">{version.data.source_uri || "—"}</Descriptions.Item>
            <Descriptions.Item label="修订">{version.data.source_revision || "—"}</Descriptions.Item>
            <Descriptions.Item label="适配器版本">{version.data.adapter_version}</Descriptions.Item>
          </Descriptions>
          <Table<MetricDefinition>
            rowKey="id"
            size="small"
            columns={metricColumns}
            dataSource={version.data.metrics}
            pagination={false}
            style={{ marginTop: 12 }}
          />
        </Card>
      ) : null}
      <Card title={`用例（${cases.data?.length ?? 0}）`}>
        {cases.error ? <QueryError error={cases.error} onRetry={() => cases.refetch()} /> : null}
        <Table<DatasetCaseSummary>
          rowKey="id"
          size="small"
          loading={cases.isLoading}
          columns={caseColumns}
          dataSource={cases.data ?? []}
          pagination={{ pageSize: 20 }}
        />
      </Card>
      <Space style={{ marginTop: 16 }}>
        <Button onClick={() => navigate("/datasets")}>返回数据集</Button>
      </Space>
    </section>
  );
}
