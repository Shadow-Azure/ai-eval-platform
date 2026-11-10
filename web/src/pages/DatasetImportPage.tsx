import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Form, Input, Result, Select, Space, Steps } from "antd";
import { useNavigate } from "react-router-dom";
import { createDatasetImport, type ImportResult } from "../api/datasets";
import { fetchRuntimes } from "../api/runtimes";
import { PageHeading } from "../components/PageHeading";

interface FormValues {
  dataset_slug: string;
  dataset_name: string;
  adapter_key: string;
  version: string;
  source_revision: string;
  source_uri?: string;
  execution_image_digest?: string;
  scoring_image_digest?: string;
}

export function DatasetImportPage() {
  const navigate = useNavigate();
  const [step, setStep] = useState(0);
  const [form] = Form.useForm<FormValues>();
  const [recordsText, setRecordsText] = useState("");
  const [importing, setImporting] = useState(false);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const runtimes = useQuery({ queryKey: ["runtimes"], queryFn: ({ signal }) => fetchRuntimes(signal) });

  useEffect(() => {
    const runtime = runtimes.data?.[0];
    if (!runtime) return;
    const current = form.getFieldsValue();
    form.setFieldsValue({
      execution_image_digest:
        current.execution_image_digest || runtime.execution_image_digest || "",
      scoring_image_digest:
        current.scoring_image_digest || runtime.scoring_image_digest || "",
    });
  }, [form, runtimes.data]);

  const parsed = (() => {
    const lines = recordsText.split(/\r?\n/).filter((line) => line.trim());
    const records: Record<string, unknown>[] = [];
    const errors: string[] = [];
    lines.forEach((line, index) => {
      try {
        const value = JSON.parse(line) as unknown;
        if (typeof value !== "object" || value === null || Array.isArray(value)) {
          errors.push(`第 ${index + 1} 行不是 JSON 对象`);
          return;
        }
        records.push(value as Record<string, unknown>);
      } catch {
        errors.push(`第 ${index + 1} 行 JSON 解析失败`);
      }
    });
    return { records, errors };
  })();

  async function doImport() {
    const values = await form.validateFields();
    setImporting(true);
    setError(null);
    try {
      const imported = await createDatasetImport({
        ...values,
        execution_image_digest: values.execution_image_digest || undefined,
        scoring_image_digest: values.scoring_image_digest || undefined,
        records: parsed.records,
      });
      setResult(imported);
      setStep(2);
      if (imported.status === "failed") {
        setError("导入校验失败，请检查记录字段后重试。");
      }
    } catch (importError) {
      setError(importError instanceof Error ? importError.message : "导入失败");
    } finally {
      setImporting(false);
    }
  }

  return (
    <section className="page">
      <PageHeading
        eyebrow="Configuration · Datasets"
        title="导入数据集"
        description="选择来源与版本，粘贴 SWE-bench 记录并完成校验；成功后生成不可变版本。"
      />
      <Steps
        current={step}
        items={[{ title: "来源与版本" }, { title: "记录校验" }, { title: "完成" }]}
        style={{ marginBottom: 24 }}
      />
      {error ? <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} /> : null}
      {step === 0 ? (
        <Form form={form} layout="vertical" initialValues={{ adapter_key: "swebench" }}>
          <Space size="large" wrap>
            <Form.Item name="dataset_slug" label="数据集 slug" rules={[{ required: true }]}>
              <Input placeholder="swebench-verified" style={{ width: 240 }} />
            </Form.Item>
            <Form.Item name="dataset_name" label="数据集名称" rules={[{ required: true }]}>
              <Input placeholder="SWE-bench Verified" style={{ width: 260 }} />
            </Form.Item>
            <Form.Item name="adapter_key" label="适配器" rules={[{ required: true }]}>
              <Select style={{ width: 180 }} options={[{ value: "swebench", label: "SWE-bench" }]} />
            </Form.Item>
          </Space>
          <Space size="large" wrap>
            <Form.Item name="version" label="版本" rules={[{ required: true }]}>
              <Input placeholder="v2026.11" style={{ width: 200 }} />
            </Form.Item>
            <Form.Item name="source_revision" label="来源修订（commit/标签）" rules={[{ required: true }]}>
              <Input placeholder="40 位 commit 或标签" style={{ width: 300 }} />
            </Form.Item>
            <Form.Item name="source_uri" label="来源 URI（可选）">
              <Input placeholder="https://…" style={{ width: 300 }} />
            </Form.Item>
          </Space>
          <Space size="large" wrap>
            <Form.Item
              name="execution_image_digest"
              label="执行镜像 digest"
              extra="默认取平台配置，可覆盖"
            >
              <Input placeholder="sha256:…" style={{ width: 420 }} />
            </Form.Item>
            <Form.Item
              name="scoring_image_digest"
              label="评分镜像 digest"
              extra="默认取平台配置，可覆盖"
            >
              <Input placeholder="sha256:…" style={{ width: 420 }} />
            </Form.Item>
          </Space>
          <Button type="primary" onClick={async () => { await form.validateFields(); setStep(1); }}>
            下一步
          </Button>
        </Form>
      ) : null}
      {step === 1 ? (
        <>
          <Alert
            type={parsed.errors.length ? "error" : "info"}
            showIcon
            message={`已解析 ${parsed.records.length} 条记录${parsed.errors.length ? `，${parsed.errors.length} 条错误` : ""}`}
            description={parsed.errors.slice(0, 5).join("；")}
            style={{ marginBottom: 16 }}
          />
          <Input.TextArea
            rows={14}
            value={recordsText}
            onChange={(event) => setRecordsText(event.target.value)}
            placeholder='每行一条 SWE-bench JSON，例如 {"instance_id":"astropy__astropy-12907",...}'
            style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", marginBottom: 16 }}
          />
          <Space>
            <Button onClick={() => setStep(0)}>上一步</Button>
            <Button type="primary" loading={importing} disabled={!parsed.records.length || parsed.errors.length > 0} onClick={doImport}>
              校验并导入
            </Button>
          </Space>
        </>
      ) : null}
      {step === 2 && result ? (
        <Result
          status={result.status === "succeeded" ? "success" : "warning"}
          title={result.status === "succeeded" ? "数据集版本已创建" : "导入未完成"}
          subTitle={`有效 ${result.import.valid_count} 条，无效 ${result.import.invalid_count} 条`}
          extra={[
            <Button key="detail" type="primary" onClick={() => result.dataset && navigate(`/datasets/${result.dataset.id}`)}>
              查看数据集
            </Button>,
            <Button key="again" onClick={() => { setStep(0); setResult(null); setRecordsText(""); }}>
              继续导入
            </Button>,
          ]}
        />
      ) : null}
    </section>
  );
}
