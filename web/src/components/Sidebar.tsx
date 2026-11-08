import { NavLink } from "react-router-dom";
import { navGroups } from "../app/nav";
import type { EntityRef } from "../api/types";
import { Icon } from "./Icon";

function initials(name: string): string {
  const trimmed = name.trim();
  if (!trimmed) return "EP";
  const parts = trimmed.split(/\s+/);
  if (parts.length > 1) {
    return parts
      .slice(0, 2)
      .map((part) => part.charAt(0))
      .join("")
      .toUpperCase();
  }
  return trimmed.slice(0, 2).toUpperCase();
}

interface SidebarProps {
  tenant?: EntityRef;
}

export function Sidebar({ tenant }: SidebarProps) {
  const tenantName = tenant?.name ?? "默认组织";
  const tenantSlug = tenant?.slug ?? "default";

  return (
    <aside className="sidebar" aria-label="主导航">
      <div className="brand">
        <div className="brand-mark" aria-hidden="true">
          EP
        </div>
        <div>
          <div className="brand-name">Eval Platform</div>
          <div className="brand-caption">Agent Evaluation</div>
        </div>
      </div>

      <div className="sidebar-nav">
        {navGroups.map((group) => (
          <nav key={group.section} className="nav-group" aria-label={group.section}>
            <div className="nav-label">{group.section}</div>
            {group.items.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end
                className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}
              >
                <Icon name={item.icon} size={17} />
                <span>{item.label}</span>
              </NavLink>
            ))}
          </nav>
        ))}
      </div>

      <div className="sidebar-footer">
        <div className="tenant-chip" title={`租户 ${tenantName} · ${tenantSlug}`}>
          <div className="tenant-avatar" aria-hidden="true">
            {initials(tenantName)}
          </div>
          <div className="truncate">
            <div className="tenant-name">{tenantName}</div>
            <div className="tenant-meta">默认组织 · 单租户</div>
          </div>
          <Icon name="chevronDown" size={13} />
        </div>
      </div>
    </aside>
  );
}
