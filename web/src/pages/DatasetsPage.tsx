import { useQuery } from "@tanstack/react-query";
import { Button, Table, Tag } from "antd";
import type { TableColumnsType } from "antd";
import { useNavigate } from "react-router-dom";
import { fetchDatasets, type DatasetSummary } from "../api/datasets";
import { PageHeading } from "../components/PageHeading";
import { QueryError } from "../components/QueryError";

const columns: TableColumnsType<DatasetSummary> = [
  { title: "数据集", dataIndex: "name", key: "name" },
  { title: "slug", dataIndex: "slug", key: "slug", className: "mono" },
  { title: "适配器", dataIndex: "adapter_key", key: "adapter_key", render: (value: string) => <Tag>{value}</Tag> },
  {
    title: "版本数",
    dataIndex: "version_count",
    key: "version_count",
    width: 96,
    render: (value: number | undefined) => value ?? "—",
  },
  {
    title: "创建时间",
    dataIndex: "created_at",
    key: "created_at",
    render: (value: string) => (value ? new Date(value).toLocaleString("zh-CN") : "—"),
  },
];

export function DatasetsPage() {
  const navigate = useNavigate();
  const query = useQuery({ queryKey: ["datasets"], queryFn: ({ signal }) => fetchDatasets(signal) });
  return (
    <section className="page">
      <PageHeading
        eyebrow="Configuration · Datasets"
        title="数据集"
        description="管理数据集来源、版本与用例，冻结每次评测使用的输入快照。"
        actions={
          <Button type="primary" onClick={() => navigate("/datasets/import")}>
            导入数据集
          </Button>
        }
      />
      {query.error ? <QueryError error={query.error} onRetry={() => query.refetch()} /> : null}
      <Table<DatasetSummary>
        rowKey="id"
        loading={query.isLoading}
        columns={columns}
        dataSource={query.data ?? []}
        pagination={false}
        onRow={(record) => ({ onClick: () => navigate(`/datasets/${record.id}`), style: { cursor: "pointer" } })}
      />
    </section>
  );
}
