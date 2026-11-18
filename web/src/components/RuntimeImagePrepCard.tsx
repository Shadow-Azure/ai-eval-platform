import { useState } from "react";
import { Button, Drawer, Tag } from "antd";
import type {
  ImageCleanupStatus,
  ImageReadiness,
  ImageReadinessImage,
  ImageReadinessStatus,
} from "../api/evaluations";
import { Icon } from "./Icon";

/** Poll every 3s while the preparation still has images that are not ready. */
export const IMAGE_PREP_POLL_MS = 3000;

/** How many per-image chips fit before the card collapses the rest into "+N". */
const IMAGE_CHIP_LIMIT = 8;

export type ImagePrepAction = "ensure" | "retry" | "recheck";

interface RuntimeImagePrepCardProps {
  readiness: ImageReadiness;
  error?: string | null;
  busy?: ImagePrepAction | null;
  onEnsure: () => void;
  onRetry: () => void;
  onRecheck: () => void;
}

/**
 * SWE-bench image refs embed the case key after the last underscore
 * (`…_1776_astropy-12907`); chips use that suffix like the prototype while the
 * drawer still shows the full ref.
 */
function imageShortLabel(ref: string): string {
  const repoName = ref.split("@")[0].split("/").pop() ?? ref;
  const withoutTag = repoName.replace(/:[^:]*$/, "");
  const suffix = withoutTag.split("_").pop();
  return suffix && suffix.length >= 6 ? suffix : withoutTag || ref;
}

function formatBytes(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 * 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MB`;
  return `${(value / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function shortObjectKey(key: string): string {
  const segments = key.split("/");
  const file = segments.pop() ?? key;
  const parent = segments.pop();
  const shortFile =
    file.length > 28 ? `${file.slice(0, 12)}…${file.slice(-10)}` : file;
  return parent ? `${parent}/${shortFile}` : shortFile;
}

function formatCheckedAt(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function statusTag(status: ImageReadinessStatus) {
  if (status === "ready") return <Tag color="success">已就绪</Tag>;
  if (status === "preparing") {
    return (
      <Tag color="processing">
        <span className="status-dot image-prep-status-dot" aria-hidden="true" />
        准备中
      </Tag>
    );
  }
  if (status === "failed") return <Tag color="error">失败</Tag>;
  return <Tag color="warning">缺失</Tag>;
}

/**
 * The prototype distinguishes a cleaned-up failure ("已清理（等待重试）", a
 * neutral tag) from a clean ready row, and a pending cleanup on a ready image
 * means the tar is already in OSS ("待清理") rather than "上传后清理".
 */
function cleanupTag(
  cleanup: ImageCleanupStatus | null | undefined,
  status: ImageReadinessStatus,
) {
  if (cleanup === "failed") return <Tag color="error">清理失败</Tag>;
  if (cleanup === "done") {
    if (status === "failed") return <Tag>已清理（等待重试）</Tag>;
    if (status === "ready") return <Tag color="success">已清理</Tag>;
    return <Tag>已清理</Tag>;
  }
  if (cleanup === "pending") {
    if (status === "ready") return <Tag color="warning">待清理</Tag>;
    return <Tag color="warning">上传后清理</Tag>;
  }
  return <Tag>—</Tag>;
}

function cleanupSummary(readiness: ImageReadiness): string {
  if (readiness.cleanup_failed_count > 0) {
    return `后台本地：${readiness.cleanup_failed_count} 个镜像清理失败`;
  }
  if (readiness.preparing_count > 0) {
    return `后台本地：${readiness.preparing_count} 个镜像处理中（上传后自动清理）`;
  }
  if (readiness.ready) {
    return "后台本地：0 个镜像 / 0 个临时 tar（上传后已清理）";
  }
  if (readiness.failed_count > 0) {
    return "后台本地：0 个镜像 / 0 个临时 tar（失败后已清理）";
  }
  return "后台本地：0 个镜像 / 0 个临时 tar（等待准备）";
}

/** Chips follow the prototype order (ready → preparing → failed), while the
 * backend deliberately returns actionable rows first for the detail drawer. */
const CHIP_STATUS_ORDER: Record<ImageReadinessStatus, number> = {
  ready: 0,
  preparing: 1,
  missing: 2,
  failed: 3,
};

export function RuntimeImagePrepCard({
  readiness,
  error,
  busy,
  onEnsure,
  onRetry,
  onRecheck,
}: RuntimeImagePrepCardProps) {
  const [detailOpen, setDetailOpen] = useState(false);
  const total = readiness.total || readiness.images.length;
  const readyCount = readiness.ready_count;
  const percent = total > 0 ? Math.round((readyCount / total) * 100) : readiness.ready ? 100 : 0;
  const cleanupFailed = readiness.cleanup_failed_count > 0;
  const tone =
    readiness.ready && !cleanupFailed
      ? "success"
      : readiness.failed_count || cleanupFailed
        ? "danger"
        : "warning";
  const progressTone =
    readiness.ready && !cleanupFailed ? "success" : cleanupFailed ? "danger" : "warning";
  const failedImages = readiness.images.filter((image) => image.status === "failed");
  const failedNames = failedImages.map((image) => imageShortLabel(image.image)).join("、");
  const chipImages = [...readiness.images].sort(
    (left, right) => CHIP_STATUS_ORDER[left.status] - CHIP_STATUS_ORDER[right.status],
  );
  const visibleImages = chipImages.slice(0, IMAGE_CHIP_LIMIT);
  const hiddenImages = Math.max(0, total - visibleImages.length);
  const retryable = readiness.failed_count > 0;
  // Like the prototype, "继续准备" is the primary action when nothing failed;
  // it also covers a preparation whose first ensure POST never landed and left
  // images in `missing` next to a failed row.
  const ensureable =
    !readiness.ready && (readiness.failed_count === 0 || readiness.missing_count > 0);
  const actionBusy = busy !== null && busy !== undefined;
  const bannerIcon = readiness.ready && !cleanupFailed ? "check" : "alert";

  const bannerText = readiness.ready ? (
    cleanupFailed ? (
      <>
        <strong>
          {readyCount}/{total} 个镜像 tar 已就绪，但 {readiness.cleanup_failed_count} 个镜像的本地清理失败。
        </strong>{" "}
        请检查后台部署服务的磁盘与 worker 日志，避免本地镜像和临时 tar 残留占满部署机。
      </>
    ) : (
      <>
        <strong>
          {total}/{total} 个镜像 tar 已就绪。
        </strong>{" "}
        Runner 会优先从内网 OSS 拉取；每个镜像上传并 HEAD 校验通过后，后台部署服务已删除本地镜像与临时
        tar。
      </>
    )
  ) : (
    <>
      <strong>
        {readyCount}/{total} 就绪，启动已暂停。
      </strong>{" "}
      后台部署服务按公开源顺序执行 pull → save → upload → HEAD；每完成一个镜像就删除本地镜像与临时
      tar，避免占满部署机磁盘。
      {retryable ? ` ${readiness.failed_count} 个镜像失败：${failedNames || "—"}。` : ""}
      {readiness.missing_count ? ` ${readiness.missing_count} 个镜像尚未开始准备。` : ""}
    </>
  );

  const cleanupPolicy = (
    <div className="image-prep-policy">
      <strong>清理策略</strong>
      <p>
        每个镜像完成上传并通过 HEAD 校验后，后台立即删除本地 docker 镜像与临时 tar；失败或重试前也先清理。
        准备任务串行执行，部署机同时只保留一个正在处理的镜像，启动时还会扫描并清理上次遗留的镜像与临时文件。
      </p>
    </div>
  );
  const checkedAtText = formatCheckedAt(readiness.checked_at);

  function detailRow(image: ImageReadinessImage) {
    // The ledger always derives a destination key; only ready/preparing rows
    // have (or are about to have) an object, while failed/missing rows are
    // still "not written" like the prototype.
    const hasObject =
      Boolean(image.object_key) && image.status !== "failed" && image.status !== "missing";
    return (
      <div className="image-prep-detail-row" key={image.image}>
        <div className="row-between">
          <span className="mono small">{imageShortLabel(image.image)}</span>
          {statusTag(image.status)}
        </div>
        <div className="mono small muted image-prep-detail-image" title={image.image}>
          {image.image}
        </div>
        <div className="image-prep-detail-meta">
          <span>
            OSS tar{" "}
            <b className="mono" title={image.object_key ?? undefined}>
              {hasObject && image.object_key ? shortObjectKey(image.object_key) : "未写入"}
            </b>
          </span>
          <span>
            大小 <b>{formatBytes(image.size_bytes)}</b>
          </span>
          <span>
            来源 <b>{image.source || "—"}</b>
          </span>
        </div>
        <div className="row-between small image-prep-detail-cleanup">
          <span className="muted">后台本地清理</span>
          {cleanupTag(image.cleanup_status, image.status)}
        </div>
        {image.error ? <div className="small image-prep-detail-error">{image.error}</div> : null}
        {image.cleanup_error ? (
          <div className="small image-prep-detail-error">清理失败：{image.cleanup_error}</div>
        ) : null}
      </div>
    );
  }

  return (
    <>
      <div className="review-box image-prep-card">
        <div className="image-prep-head">
          <div>
            <div className="image-prep-title-row">
              <strong>运行时镜像准备</strong>
              <Tag
                color={
                  readiness.ready && !cleanupFailed
                    ? "success"
                    : tone === "danger"
                      ? "error"
                      : "warning"
                }
              >
                {readyCount}/{total} 就绪
              </Tag>
              {retryable ? <Tag color="error">{readiness.failed_count} 失败</Tag> : null}
              {cleanupFailed ? <Tag color="error">{readiness.cleanup_failed_count} 清理失败</Tag> : null}
            </div>
            <p className="image-prep-subtitle">
              开评前自动检查用例镜像的 OSS tar；缺失时由后台部署服务补齐，全部就绪后才允许启动。
            </p>
          </div>
          <Button size="small" onClick={() => setDetailOpen(true)}>
            查看准备详情
          </Button>
        </div>
        <div
          className={`image-prep-progress ${progressTone}`}
          role="progressbar"
          aria-label="运行时镜像就绪进度"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={percent}
        >
          <span style={{ width: `${percent}%` }} />
        </div>
        <div className="image-prep-stats">
          <span>
            <span className="status-dot ready" aria-hidden="true" />
            就绪 {readyCount}
          </span>
          <span>
            <span className="status-dot preparing" aria-hidden="true" />
            准备中 {readiness.preparing_count}
          </span>
          <span>
            <span className="status-dot failed" aria-hidden="true" />
            失败 {readiness.failed_count}
          </span>
          {busy === "ensure" ? (
            <span className="image-prep-enqueue-hint" role="status">
              <span className="status-dot preparing image-prep-status-dot" aria-hidden="true" />
              正在入队…
            </span>
          ) : null}
          {readiness.missing_count ? (
            <span>
              <span className="status-dot missing" aria-hidden="true" />
              缺失 {readiness.missing_count}
            </span>
          ) : null}
          <span className="muted image-prep-cleanup">{cleanupSummary(readiness)}</span>
        </div>
        {readiness.images.length ? (
          <div className="image-prep-chips">
            {visibleImages.map((image) => (
              <span
                className={`image-prep-chip ${image.status}`}
                title={image.image}
                key={image.image}
              >
                <span className="status-dot" aria-hidden="true" />
                <span className="mono">{imageShortLabel(image.image)}</span>
              </span>
            ))}
            {hiddenImages > 0 ? (
              <span className="image-prep-chip more" title={`还有 ${hiddenImages} 个镜像`}>
                +{hiddenImages}
              </span>
            ) : null}
          </div>
        ) : null}
        <div className={`banner ${tone}`}>
          <Icon name={bannerIcon} size={15} />
          <div>{bannerText}</div>
        </div>
        <div className="image-prep-actions">
          {retryable ? (
            <Button
              size="small"
              type="primary"
              loading={busy === "retry"}
              disabled={actionBusy && busy !== "retry"}
              onClick={onRetry}
            >
              <Icon name="refresh" size={13} /> 重试失败镜像
            </Button>
          ) : null}
          {ensureable ? (
            <Button
              size="small"
              type={retryable ? "default" : "primary"}
              loading={busy === "ensure"}
              disabled={actionBusy && busy !== "ensure"}
              onClick={onEnsure}
            >
              <Icon name="refresh" size={13} /> 继续准备
            </Button>
          ) : null}
          <Button
            size="small"
            loading={busy === "recheck"}
            disabled={actionBusy && busy !== "recheck"}
            onClick={onRecheck}
          >
            <Icon name="search" size={13} /> 重新检查
          </Button>
          <span className="small muted image-prep-action-hint">镜像串行准备 · 上传后自动清理</span>
        </div>
        {error ? (
          <div className="banner danger image-prep-error-banner" role="alert">
            <Icon name="alert" size={15} />
            <div>{error}</div>
          </div>
        ) : null}
      </div>

      <Drawer
        title="运行时镜像准备"
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
        width={640}
        rootClassName="image-prep-drawer"
        footer={
          <div className="image-prep-drawer-footer">
            {retryable ? (
              <Button
                type="primary"
                loading={busy === "retry"}
                disabled={actionBusy && busy !== "retry"}
                onClick={onRetry}
              >
                <Icon name="refresh" size={13} /> 重试失败镜像
              </Button>
            ) : null}
            <Button onClick={() => setDetailOpen(false)}>关闭</Button>
          </div>
        }
      >
        <p className="small muted image-prep-drawer-subtitle">
          {readyCount}/{total} 就绪 · 由后台部署服务串行处理
        </p>
        <div className={`banner ${tone}`}>
          <Icon name={bannerIcon} size={15} />
          <div>{bannerText}</div>
        </div>
        <div className="image-prep-detail-list">
          {readiness.images.map((image) => detailRow(image))}
        </div>
        {readiness.images.length ? null : (
          <div className="small muted image-prep-drawer-empty">暂无可展示的镜像条目。</div>
        )}
        {readiness.truncated ? (
          <div className="small muted image-prep-drawer-empty">
            后端仅返回前 {readiness.images.length} 个镜像，共 {total} 个。
          </div>
        ) : null}
        {cleanupPolicy}
        <p className="small muted image-prep-drawer-footnote">
          最近检查：{checkedAtText} · 全部通过后才允许启动评测。
        </p>
      </Drawer>
    </>
  );
}
