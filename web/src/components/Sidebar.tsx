import { Tooltip } from "antd";
import { NavLink } from "react-router-dom";
import { navGroups } from "../app/nav";
import { useAuth } from "../app/auth";
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
  collapsed: boolean;
  onToggle: () => void;
}

export function Sidebar({ tenant, collapsed, onToggle }: SidebarProps) {
  const { session } = useAuth();
  const isAdmin = session?.role === "admin";
  const tenantName = tenant?.name ?? "默认组织";
  const tenantSlug = tenant?.slug ?? "default";
  const toggleLabel = collapsed ? "展开导航" : "收起导航";

  return (
    <aside className="sidebar" id="app-sidebar" aria-label="主导航">
      <div className="brand">
        <div className="brand-mark" aria-hidden="true">
          EP
        </div>
        <div className="brand-copy">
          <div className="brand-name">Eval Platform</div>
          <div className="brand-caption">Agent Evaluation</div>
        </div>
      </div>

      <div className="sidebar-nav">
        {navGroups.map((group) => {
          const items = group.items.filter((item) => !item.adminOnly || isAdmin);
          if (items.length === 0) {
            return null;
          }
          return (
            <nav key={group.section} className="nav-group" aria-label={group.section}>
              <div className="nav-label">{group.section}</div>
              {items.map((item) => {
                const link = (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    end
                    aria-label={collapsed ? item.label : undefined}
                    className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}
                  >
                    <Icon name={item.icon} size={17} />
                    <span>{item.label}</span>
                  </NavLink>
                );
                return collapsed ? (
                  <Tooltip key={item.to} title={item.label} placement="right">
                    {link}
                  </Tooltip>
                ) : (
                  link
                );
              })}
            </nav>
          );
        })}
      </div>

      <div className="sidebar-footer">
        <button
          type="button"
          className="sidebar-toggle"
          onClick={onToggle}
          aria-expanded={!collapsed}
          aria-controls="app-sidebar"
          aria-label={toggleLabel}
          title={toggleLabel}
        >
          <Icon name={collapsed ? "chevronsRight" : "chevronsLeft"} size={15} />
          <span>{toggleLabel}</span>
        </button>
        <div className="tenant-chip" title={`租户 ${tenantName} · ${tenantSlug}`}>
          <div className="tenant-avatar" aria-hidden="true">
            {initials(tenantName)}
          </div>
          <div className="tenant-copy truncate">
            <div className="tenant-name">{tenantName}</div>
            <div className="tenant-meta">默认组织 · 单租户</div>
          </div>
          <Icon name="chevronDown" size={13} />
        </div>
      </div>
    </aside>
  );
}
