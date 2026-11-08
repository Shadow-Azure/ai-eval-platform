import type { ReactNode } from "react";
import { Icon, type IconName } from "./Icon";

interface StatusCardProps {
  label: string;
  value: ReactNode;
  meta?: ReactNode;
  icon: IconName;
  tone?: "primary" | "success" | "warning" | "danger" | "violet";
  loading?: boolean;
}

export function StatusCard({
  label,
  value,
  meta,
  icon,
  tone = "primary",
  loading = false,
}: StatusCardProps) {
  if (loading) {
    return (
      <div className="card metric-card" aria-busy="true">
        <span className="sr-only">加载中</span>
        <div className="skeleton skeleton-label" />
        <div className="skeleton skeleton-value" />
        <div className="skeleton skeleton-meta" />
        <div className={`metric-icon ${tone}`} aria-hidden="true">
          <Icon name={icon} size={16} />
        </div>
      </div>
    );
  }

  return (
    <div className="card metric-card">
      <div className="metric-label">{label}</div>
      <div className="metric-value">{value}</div>
      {meta ? <div className="metric-meta">{meta}</div> : null}
      <div className={`metric-icon ${tone}`} aria-hidden="true">
        <Icon name={icon} size={16} />
      </div>
    </div>
  );
}
