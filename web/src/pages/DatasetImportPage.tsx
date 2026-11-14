import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Form, Input, Modal, Progress, Select, Space, Tag } from "antd";
import { useNavigate } from "react-router-dom";
import {
  cancelDatasetImport,
  commitDatasetImport,
  fetchDatasetImport,
  uploadDatasetImport,
  type DatasetImportRecord,
  type ImportIssue,
} from "../api/datasets";
import { fetchRuntimes } from "../api/runtimes";
import { Icon } from "../components/Icon";
import { PageHeading } from "../components/PageHeading";

const MAX_BYTES = 1024 * 1024 * 1024;
const ACCEPTED_SUFFIXES = [".json", ".jsonl", ".ndjson"];
const ACTIVE_STATUSES = new Set(["queued", "validating", "committing"]);
const STEP_DESCRIPTIONS: Array<[string, string]> = [
  ["选择类型", "适配器与数据源"],
  ["校验数据", "字段、环境与指标"],
  ["生成版本", "任务包与入库"],
];

interface FormValues {
  dataset_slug: string;
  dataset_name: string;
  adapter_key: string;
  version: string;
  source_revision: string;
  execution_image_digest?: string;
  scoring_image_digest?: string;
}

function formatBytes(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 * 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MB`;
  return `${(value / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function errorMessage(error: unknown): string {
  return error instanceof Error && error.message ? error.message : "操作失败，请稍后再试";
}

function importIssues(record: DatasetImportRecord): ImportIssue[] {
  const reportIssues = record.report?.errors;
  if (Array.isArray(reportIssues) && reportIssues.length) return reportIssues;
  const errorIssues = record.error_report?.errors;
  return Array.isArray(errorIssues) ? errorIssues : [];
}

export function DatasetImportPage() {
  const navigate = useNavigate();
  const [form] = Form.useForm<FormValues>();
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [step, setStep] = useState(1);
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [fileError, setFileError] = useState<string | null>(null);
  const [importRecord, setImportRecord] = useState<DatasetImportRecord | null>(null);
  const [uploadRatio, setUploadRatio] = useState<number | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [committing, setCommitting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [pollTick, setPollTick] = useState(0);
  const [imageSourcesOpen, setImageSourcesOpen] = useState(false);
  const runtimes = useQuery({ queryKey: ["runtimes"], queryFn: ({ signal }) => fetchRuntimes(signal) });

  useEffect(() => {
    const runtime = runtimes.data?.[0];
    if (!runtime) return;
    const current = form.getFieldsValue();
    form.setFieldsValue({
      execution_image_digest: current.execution_image_digest || runtime.execution_image_digest || "",
      scoring_image_digest: current.scoring_image_digest || runtime.scoring_image_digest || "",
    });
  }, [form, runtimes.data]);

  // Poll while the isolated worker is active. A terminal status stops the
  // timer; the response is the only source of truth for counts and reports.
  useEffect(() => {
    if (!importRecord || !ACTIVE_STATUSES.has(importRecord.status)) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      fetchDatasetImport(importRecord.id)
        .then((next) => {
          if (!cancelled) setImportRecord(next);
        })
        .catch((error) => {
          if (cancelled) return;
          setActionError(errorMessage(error));
          // Keep polling after a transient network error instead of leaving
          // the wizard frozen on its last status.
          setPollTick((tick) => tick + 1);
        });
    }, 900);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [importRecord, pollTick]);

  useEffect(() => {
    if (!importRecord) return;
    if (importRecord.status === "succeeded" || importRecord.status === "duplicate") {
      setStep(3);
    }
  }, [importRecord]);

  function acceptFile(candidate: File | null | undefined) {
    setFileError(null);
    if (!candidate) return;
    const suffix = candidate.name.slice(candidate.name.lastIndexOf(".")).toLowerCase();
    if (!ACCEPTED_SUFFIXES.includes(suffix)) {
      setFile(null);
      setFileError("仅支持 .json、.jsonl 或 .ndjson 数据文件");
      return;
    }
    if (candidate.size > MAX_BYTES) {
      setFile(null);
      setFileError("数据文件超过 1 GB 上限，请拆分后再上传");
      return;
    }
    if (candidate.size === 0) {
      setFile(null);
      setFileError("数据文件为空");
      return;
    }
    setFile(candidate);
  }

  async function startUpload() {
    let values: FormValues;
    try {
      values = await form.validateFields();
    } catch {
      return; // Ant Design has already marked the missing fields.
    }
    if (!file) {
      setFileError("请先选择一份 JSON / JSONL 数据文件");
      return;
    }
    setSubmitting(true);
    setActionError(null);
    setUploadRatio(0);
    try {
      const record = await uploadDatasetImport(
        {
          dataset_slug: values.dataset_slug,
          dataset_name: values.dataset_name,
          adapter_key: values.adapter_key,
          version: values.version,
          source_revision: values.source_revision,
          execution_image_digest: values.execution_image_digest || undefined,
          scoring_image_digest: values.scoring_image_digest || undefined,
        },
        file,
        { onProgress: setUploadRatio },
      );
      setImportRecord(record);
      setStep(2);
    } catch (error) {
      setActionError(errorMessage(error));
    } finally {
      setSubmitting(false);
      setUploadRatio(null);
    }
  }

  async function startCommit() {
    if (!importRecord) return;
    setCommitting(true);
    setActionError(null);
    try {
      const record = await commitDatasetImport(importRecord.id);
      setImportRecord(record);
      setStep(3);
    } catch (error) {
      setActionError(errorMessage(error));
    } finally {
      setCommitting(false);
    }
  }

  async function cancelAndLeave() {
    if (
      importRecord &&
      (importRecord.status === "queued" ||
        importRecord.status === "validating" ||
        importRecord.status === "validated")
    ) {
      try {
        await cancelDatasetImport(importRecord.id);
      } catch {
        // Leaving the wizard is still valid; an in-flight worker keeps going.
      }
    }
    navigate("/datasets");
  }

  function resetWizard() {
    if (
      importRecord &&
      (importRecord.status === "queued" ||
        importRecord.status === "validating" ||
        importRecord.status === "validated")
    ) {
      // Best effort: release the staged file before starting a new import.
      cancelDatasetImport(importRecord.id).catch(() => {});
    }
    setStep(1);
    setImportRecord(null);
    setActionError(null);
    setFileError(null);
    setFile(null);
    setUploadRatio(null);
  }

  const status = importRecord?.status ?? "";
  const report = importRecord?.report ?? {};
  const errors = importRecord ? importIssues(importRecord) : [];
  const imageCheck = report.image_check;
  const imageBlocked = imageCheck?.status === "blocked";
  const imageCases = imageCheck?.unavailable?.length
    ? imageCheck.unavailable
    : imageCheck?.cases ?? [];
  const completeness = Object.entries(report.field_completeness ?? {});
  const activeSide = step;
  const validating = status === "queued" || status === "validating";
  const commitRunning = status === "committing";
  const completed = status === "succeeded" || status === "duplicate";
  const failed = status === "failed";

  return (
    <section className="page">
      <PageHeading
        eyebrow="Dataset import"
        title="导入数据集"
        description="配置只在本次导入使用；生成的版本可以被所有评测任务引用。"
        actions={
          <Button onClick={cancelAndLeave} disabled={submitting}>
            取消
          </Button>
        }
      />
      <section className="ds-import-card">
        <aside className="ds-import-side">
          <div className="ds-import-side-label">导入流程</div>
          {STEP_DESCRIPTIONS.map(([title, meta], index) => {
            const order = index + 1;
            const className =
              activeSide === order
                ? "ds-import-step active"
                : activeSide > order
                  ? "ds-import-step done"
                  : "ds-import-step";
            return (
              <div className={className} key={title}>
                <div className="ds-import-step-index">{activeSide > order ? "✓" : order}</div>
                <div>
                  <div className="ds-import-step-title">{title}</div>
                  <div className="ds-import-step-meta">{meta}</div>
                </div>
              </div>
            );
          })}
          <div className="ds-import-security">
            <Icon name="lock" size={15} />
            <span>导入由数据集适配器在隔离 worker 中执行，不接触执行机凭据。</span>
          </div>
        </aside>

        <div className="ds-import-main">
          {actionError ? (
            <Alert type="error" showIcon message={actionError} style={{ marginBottom: 16 }} />
          ) : null}

          {step === 1 ? (
            <>
              <div className="ds-import-eyebrow">Step 1 · Source</div>
              <h2 className="ds-import-title">选择评测集类型与数据来源</h2>
              <p className="ds-import-subtitle">平台会用对应适配器解析字段并生成统一任务包；类型列表由注册表提供。</p>
              <Form form={form} layout="vertical" initialValues={{ adapter_key: "swebench" }}>
                <Form.Item name="adapter_key" label="评测集类型" rules={[{ required: true }]}>
                  <Select
                    onChange={() => {
                      // The adapter drives the accepted source schema, so a
                      // previously selected file must be chosen again.
                      setFile(null);
                      setFileError(null);
                    }}
                    options={[
                      { value: "swebench", label: "SWE-bench Adapter · v1" },
                      { value: "terminal-bench", label: "Terminal-Bench（尚未注册）", disabled: true },
                      { value: "gaia", label: "GAIA（尚未注册）", disabled: true },
                    ]}
                  />
                </Form.Item>

                <div
                  className={dragging ? "ds-upload-zone dragging" : "ds-upload-zone"}
                  onDragOver={(event) => {
                    event.preventDefault();
                    setDragging(true);
                  }}
                  onDragLeave={() => setDragging(false)}
                  onDrop={(event) => {
                    event.preventDefault();
                    setDragging(false);
                    acceptFile(event.dataTransfer.files?.[0]);
                  }}
                >
                  <div className="ds-upload-icon">
                    <Icon name="database" size={19} />
                  </div>
                  <strong>选择一份 SWE-bench 数据文件</strong>
                  <div className="ds-upload-hint">支持 JSON / JSONL，单文件不超过 1 GB；可拖放到此处</div>
                  <Button size="small" onClick={() => inputRef.current?.click()} disabled={submitting}>
                    <Icon name="plus" size={13} /> 选择文件
                  </Button>
                  <input
                    ref={inputRef}
                    type="file"
                    accept=".json,.jsonl,.ndjson,application/json"
                    hidden
                    onChange={(event) => {
                      acceptFile(event.target.files?.[0]);
                      event.target.value = "";
                    }}
                  />
                  {file ? (
                    <div className="ds-upload-file">
                      <Icon name="check" size={14} /> {file.name} · {formatBytes(file.size)}
                      <Button type="link" size="small" onClick={() => setFile(null)} disabled={submitting}>
                        移除
                      </Button>
                    </div>
                  ) : null}
                  {fileError ? <div className="ds-upload-error">{fileError}</div> : null}
                </div>

                <div className="ds-field-row">
                  <Form.Item name="version" label="版本标签" rules={[{ required: true, whitespace: true }]}>
                    <Input placeholder="例如 v2026.11" />
                  </Form.Item>
                  <Form.Item
                    name="source_revision"
                    label="数据提交号 / Tag"
                    rules={[{ required: true, whitespace: true }]}
                  >
                    <Input className="mono" placeholder="例如 3f2c9a1" />
                  </Form.Item>
                </div>

                <div className="ds-field-row">
                  <Form.Item name="dataset_slug" label="数据集 slug" rules={[{ required: true, whitespace: true }]}>
                    <Input placeholder="swebench-verified" />
                  </Form.Item>
                  <Form.Item name="dataset_name" label="数据集名称" rules={[{ required: true, whitespace: true }]}>
                    <Input placeholder="SWE-bench Verified" />
                  </Form.Item>
                </div>
                <details className="ds-import-advanced">
                  <summary>平台元信息（保留线上字段）</summary>
                  <div className="ds-field-row">
                    <Form.Item
                      name="execution_image_digest"
                      label="执行镜像 digest"
                      extra="默认取平台配置，可覆盖"
                    >
                      <Input className="mono" placeholder="sha256:…" />
                    </Form.Item>
                    <Form.Item
                      name="scoring_image_digest"
                      label="评分镜像 digest"
                      extra="默认取平台配置，可覆盖"
                    >
                      <Input className="mono" placeholder="sha256:…" />
                    </Form.Item>
                  </div>
                </details>
              </Form>
              <div className="ds-import-footer">
                <Button onClick={cancelAndLeave} disabled={submitting}>
                  取消
                </Button>
                <Button type="primary" loading={submitting} onClick={startUpload}>
                  {uploadRatio !== null && submitting
                    ? `上传中 ${Math.round(uploadRatio * 100)}%`
                    : "校验数据"}{" "}
                  <Icon name="arrowRight" size={13} />
                </Button>
              </div>
            </>
          ) : null}

          {step === 2 && importRecord ? (
            <>
              <div className="ds-import-eyebrow">Step 2 · Validate</div>
              <h2 className="ds-import-title">
                {validating ? "正在隔离 worker 中校验数据" : failed ? "字段校验未通过" : "字段校验与环境检查"}
              </h2>
              <p className="ds-import-subtitle">
                校验在后台完成，错误会阻止生成版本；修正文件后可重新上传。
              </p>

              {validating ? (
                <div className="ds-import-progress">
                  <Progress percent={100} status="active" showInfo={false} />
                  <div className="ds-progress-meta">
                    <span>{importRecord.status === "queued" ? "已入队，等待 worker…" : "解析标准字段并校验…"}</span>
                    <strong>{importRecord.processed_count} 条</strong>
                  </div>
                </div>
              ) : null}

              {!validating ? (
                <>
                  {status === "cancelled" ? (
                    <Alert
                      type="warning"
                      showIcon
                      message="导入已取消"
                      description="暂存文件已释放；如需导入请重新选择文件。"
                      style={{ marginBottom: 12 }}
                    />
                  ) : null}
                  <div className="ds-stat-grid">
                    <div className="ds-stat">
                      <div className="ds-stat-label">识别用例</div>
                      <div className="ds-stat-value">{report.total ?? importRecord.total_count}</div>
                    </div>
                    <div className="ds-stat">
                      <div className="ds-stat-label">字段错误</div>
                      <div className={failed ? "ds-stat-value danger" : "ds-stat-value success"}>
                        {failed ? report.invalid ?? importRecord.invalid_count : 0}
                      </div>
                    </div>
                    <div className="ds-stat">
                      <div className="ds-stat-label">警告</div>
                      <div className="ds-stat-value warning">{report.warnings?.length ?? 0}</div>
                    </div>
                  </div>

                  {failed || status === "cancelled" ? (
                    <Alert
                      type="error"
                      showIcon
                      message={report.fatal_message ?? "校验发现不可导入的错误，未生成任何版本"}
                      description={
                        report.fatal_code
                          ? `错误码：${report.fatal_code}`
                          : `共 ${report.invalid ?? importRecord.invalid_count} 条错误记录；修正后可重新上传。`
                      }
                      style={{ marginBottom: 12 }}
                    />
                  ) : (
                    <Alert
                      type="success"
                      showIcon
                      message="标准字段完整"
                      description="实例 ID、仓库、base_commit、problem_statement、patch 均可解析。"
                      style={{ marginBottom: 12 }}
                    />
                  )}

                  {completeness.length ? (
                    <div className="ds-completeness">
                      <div className="ds-review-heading">字段完整率</div>
                      <div className="ds-completeness-grid">
                        {completeness.map(([field, info]) => (
                          <div className="ds-completeness-row" key={field}>
                            <span className="mono">{field}</span>
                            <span className="ds-completeness-bar">
                              <Progress percent={info.percent} showInfo={false} size="small" />
                            </span>
                            <span className="ds-completeness-value">
                              {info.present} / {report.total ?? importRecord.total_count} ·{" "}
                              {info.percent}%
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : null}

                  {errors.length ? (
                    <div className="ds-error-list">
                      <div className="ds-review-heading">
                        错误明细（前 {Math.min(errors.length, 20)} 条）
                      </div>
                      {errors.slice(0, 20).map((issue, index) => (
                        <div className="ds-error-item" key={`${issue.code}-${index}`}>
                          <Tag color="error">{issue.code}</Tag>
                          <span>
                            {issue.field ? `字段 ${issue.field} · ` : ""}
                            {issue.message}
                            {issue.case_index ? `（第 ${issue.case_index} 条）` : ""}
                          </span>
                        </div>
                      ))}
                      {(report.errors_truncated ?? 0) > 0 ? (
                        <div className="ds-error-more">另有 {report.errors_truncated} 条错误未展示</div>
                      ) : null}
                    </div>
                  ) : null}

                  {!failed ? (
                    <div className="ds-import-review">
                      <div className="ds-summary-row">
                        <div>
                          <strong>环境与评分任务包</strong>
                          <div className="ds-summary-hint">environment/Dockerfile · tests/test.sh · task.toml</div>
                        </div>
                        <Tag color={imageBlocked ? "warning" : "success"}>{imageBlocked ? "待镜像检查" : "可生成"}</Tag>
                      </div>
                      <div className="ds-summary-row">
                        <div>
                          <strong>指标映射</strong>
                          <div className="ds-summary-hint">swebench.resolved / swebench.submitted</div>
                        </div>
                        <Tag color="processing">解决率</Tag>
                      </div>
                      <div className="ds-summary-row">
                        <div>
                          <strong>来源文件</strong>
                          <div className="ds-summary-hint">
                            {importRecord.file_name} · {formatBytes(importRecord.file_size)}
                          </div>
                        </div>
                        <span className="mono ds-digest">
                          {(importRecord.file_digest ?? "").slice(0, 18)}…
                        </span>
                      </div>
                      {imageCheck ? (
                        <div className="ds-image-check">
                          <div className="ds-image-check-head">
                            <div>
                              <strong>运行时镜像检查</strong>
                              <div className="ds-summary-hint">
                                按每个用例的 instance/架构解析镜像，并按配置顺序探测公开源；全部不可用则阻止生成版本。
                              </div>
                            </div>
                            <Tag color={imageBlocked ? "error" : imageCheck.status === "passed" ? "success" : "warning"}>
                              {imageBlocked ? "未找到" : imageCheck.status === "passed" ? "通过" : "未检查"}
                            </Tag>
                          </div>
                          <div className="ds-image-sources">
                            {(imageCheck.sources ?? []).map((source, index) => (
                              <span className="ds-image-source" key={`${source.registry}-${source.prefix ?? ""}`}>
                                <b>{index + 1}</b>
                                {source.name}
                                <Tag color={source.status === "found" ? "success" : source.status === "not_found" ? "error" : "default"}>
                                  {source.status === "found" ? "200" : source.status === "not_found" ? "404" : source.status}
                                </Tag>
                              </span>
                            ))}
                            {!imageCheck.sources?.length ? <span className="ds-summary-hint">未配置公开镜像源</span> : null}
                          </div>
                          {imageCases.length ? (
                            <div className="ds-image-cases">
                              {imageCases.slice(0, 3).map((item) => (
                                <div className="ds-image-case" key={item.case_key}>
                                  <span className="mono">{item.case_key}</span>
                                  <span className="mono ds-summary-hint">{item.image}</span>
                                  <Tag color={item.status === "found" ? "success" : "error"}>
                                    {item.status === "found" ? item.source ?? "可用" : "未找到"}
                                  </Tag>
                                </div>
                              ))}
                            </div>
                          ) : null}
                          <div className="ds-image-summary">
                            <span>
                              {imageCheck.checked} 个用例 · {imageCheck.available} 个可用
                              {imageCheck.cases_truncated ? ` · 另有 ${imageCheck.cases_truncated} 个未展示` : ""}
                            </span>
                            <Button size="small" type="link" onClick={() => setImageSourcesOpen(true)}>
                              查看镜像源配置
                            </Button>
                          </div>
                          {imageBlocked ? (
                            <Alert
                              type="error"
                              showIcon
                              message="生成版本已阻断"
                              description={`有 ${imageCheck.checked - imageCheck.available} 个用例镜像未在已配置公开源中找到。请先让管理员补充/同步镜像源，或稍后重新上传校验；不会写入半成品版本。`}
                            />
                          ) : imageCheck.status === "skipped" ? (
                            <Alert
                              type="warning"
                              showIcon
                              message="未执行公开镜像源检查"
                              description={imageCheck.reason ?? "请配置 DATASET_IMAGE_REGISTRIES 后重新上传校验。"}
                            />
                          ) : (
                            <Alert type="success" showIcon message="所有用例镜像在已配置公开源中可用" />
                          )}
                        </div>
                      ) : null}
                    </div>
                  ) : null}
                </>
              ) : null}

              <div className="ds-import-footer">
                <Button onClick={() => setStep(1)} disabled={committing}>
                  上一步
                </Button>
                <Space>
                  <Button onClick={resetWizard} disabled={committing}>
                    重新上传
                  </Button>
                  <Button
                    type="primary"
                    disabled={status !== "validated" || committing || imageBlocked}
                    loading={committing}
                    onClick={startCommit}
                  >
                    生成版本 <Icon name="arrowRight" size={13} />
                  </Button>
                </Space>
              </div>
            </>
          ) : null}

          {step === 3 && importRecord ? (
            <>
              <div className="ds-import-eyebrow">Step 3 · Materialize</div>
              <h2 className="ds-import-title">
                {completed ? "数据集版本已就绪" : commitRunning ? "正在生成版本并写入用例" : "版本生成未完成"}
              </h2>
              <p className="ds-import-subtitle">
                {completed
                  ? "版本内容不可变，可以立即用于发起评测。"
                  : commitRunning
                    ? "可以离开此页面，导入会在后台继续；完成后可在数据集目录查看。"
                    : "本轮没有生成版本，可修正后重新上传。"}
              </p>

              {commitRunning ? (
                <>
                  <div className="ds-import-progress">
                    <Progress percent={100} status="active" showInfo={false} />
                    <div className="ds-progress-meta">
                      <span>批量写入不可变用例…</span>
                      <strong>共 {importRecord.total_count} 条</strong>
                    </div>
                  </div>
                  <div className="ds-stage-list">
                    <div className="ds-stage done">
                      <span><Icon name="check" size={15} /> 字段校验</span>
                      <Tag color="success">完成</Tag>
                    </div>
                    <div className="ds-stage">
                      <span><Icon name="refresh" size={15} /> 冻结版本</span>
                      <Tag color="processing">进行中</Tag>
                    </div>
                    <div className="ds-stage">
                      <span><Icon name="clock" size={15} /> 用例入库</span>
                      <Tag>等待</Tag>
                    </div>
                  </div>
                </>
              ) : null}

              {completed ? (
                <div className="ds-success">
                  <div className="ds-success-icon">
                    <Icon name="check" size={26} />
                  </div>
                  <h3>
                    {importRecord.dataset?.name ?? importRecord.dataset_slug} · {importRecord.version}
                  </h3>
                  <p className="ds-summary-hint">
                    {importRecord.version_detail?.case_count ?? importRecord.total_count} 个用例 ·{" "}
                    {report.warnings?.length ?? 0} 个警告 · 版本已冻结
                  </p>
                  <div className="ds-import-review">
                    <div className="ds-summary-row">
                      <span className="ds-summary-label">任务包</span>
                      <span>{importRecord.total_count} × 平台任务视图</span>
                    </div>
                    <div className="ds-summary-row">
                      <span className="ds-summary-label">来源指纹</span>
                      <span className="mono ds-digest">{importRecord.file_digest}</span>
                    </div>
                    <div className="ds-summary-row">
                      <span className="ds-summary-label">指标</span>
                      <span>解决率（% Resolved）</span>
                    </div>
                  </div>
                </div>
              ) : null}

              {failed ? (
                <Alert
                  type="error"
                  showIcon
                  message={report.fatal_message ?? "版本生成失败"}
                  description={report.fatal_code ? `错误码：${report.fatal_code}` : undefined}
                  style={{ marginBottom: 16 }}
                />
              ) : null}

              <div className="ds-import-footer">
                <Button onClick={cancelAndLeave}>取消</Button>
                <Space>
                  {completed ? (
                    <>
                      <Button onClick={resetWizard}>继续导入</Button>
                      <Button
                        type="primary"
                        disabled={!importRecord.dataset}
                        onClick={() =>
                          importRecord.dataset && navigate(`/datasets/${importRecord.dataset.id}`)
                        }
                      >
                        查看数据集 <Icon name="arrowRight" size={13} />
                      </Button>
                    </>
                  ) : (
                    <Button onClick={resetWizard}>重新上传</Button>
                  )}
                </Space>
              </div>
            </>
          ) : null}
        </div>
      </section>
      <Modal
        open={imageSourcesOpen}
        title="公开镜像源配置"
        onCancel={() => setImageSourcesOpen(false)}
        footer={<Button onClick={() => setImageSourcesOpen(false)}>关闭</Button>}
      >
        <p className="ds-summary-hint">按顺序探测；某个源 404/超时会继续下一个，全部不可用时阻断数据集版本。</p>
        <div className="ds-image-sources ds-image-sources-modal">
          {(imageCheck?.sources ?? []).map((source, index) => (
            <span className="ds-image-source" key={`${source.registry}-${source.prefix ?? ""}`}>
              <b>{index + 1}</b>
              {source.name}
              <span className="mono ds-summary-hint">{source.registry}{source.prefix ? `/${source.prefix}` : ""}</span>
            </span>
          ))}
          {!imageCheck?.sources?.length ? <span className="ds-summary-hint">尚未配置公开镜像源。</span> : null}
        </div>
        <Alert
          style={{ marginTop: 12 }}
          type="info"
          showIcon
          message="由管理员通过 DATASET_IMAGE_REGISTRIES 配置"
          description="当前页面只展示本次探测使用的源；镜像构建、推送和私有源认证不在本次导入中处理。"
        />
      </Modal>
    </section>
  );
}
