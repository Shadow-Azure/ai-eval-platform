import type { ReactNode } from "react";
import { Icon, type IconName } from "./Icon";

interface EmptyStateProps {
  icon?: IconName;
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  compact?: boolean;
  inline?: boolean;
}

export function EmptyState({
  icon = "list",
  title,
  description,
  action,
  compact = false,
  inline = false,
}: EmptyStateProps) {
  return (
    <div
      className={`empty-state${compact ? " compact" : ""}${inline ? " inline" : ""}`}
    >
      <div className="empty-mark" aria-hidden="true">
        <Icon name={icon} size={20} />
      </div>
      <div className="empty-copy">
        <div className="empty-title">{title}</div>
        {description ? <div className="empty-description">{description}</div> : null}
      </div>
      {action ? <div className="empty-action">{action}</div> : null}
    </div>
  );
}
