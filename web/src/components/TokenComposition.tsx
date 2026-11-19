import type { ReactNode } from "react";

/**
 * Four-way token composition panel shared by the evaluation results and
 * dataset pages (#339). The panel is presentational: callers pass the
 * authoritative values from the API so the page never re-derives totals.
 */
export interface TokenCompositionItem {
  label: string;
  value: ReactNode;
  meta?: ReactNode;
}

export interface TokenCompositionProps {
  title?: string;
  subtitle?: ReactNode;
  badge?: ReactNode;
  items: TokenCompositionItem[];
  className?: string;
}

export function TokenComposition({
  title = "Token 构成（含缓存）",
  subtitle,
  badge,
  items,
  className,
}: TokenCompositionProps) {
  return (
    <div className={className ? `token-composition ${className}` : "token-composition"}>
      <div className="row-between">
        <div>
          <h3 className="card-title">{title}</h3>
          {subtitle ? <p className="card-subtitle">{subtitle}</p> : null}
        </div>
        {badge}
      </div>
      <div className="token-composition-grid">
        {items.map((item) => (
          <div className="token-composition-item" key={item.label}>
            <span>{item.label}</span>
            <strong>{item.value}</strong>
            {item.meta ? <small>{item.meta}</small> : null}
          </div>
        ))}
      </div>
    </div>
  );
}
