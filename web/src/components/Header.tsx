import { Dropdown, type MenuProps } from "antd";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../app/auth";
import { breadcrumbFor } from "../app/nav";
import type { EntityRef } from "../api/types";
import { Icon } from "./Icon";

interface HeaderProps {
  tenant?: EntityRef;
  project?: EntityRef;
  connectionState: "loading" | "connected" | "error";
}

const helpItems: MenuProps["items"] = [
  { key: "help", label: "帮助中心将在后续里程碑接入", disabled: true },
];

const notificationItems: MenuProps["items"] = [
  { key: "empty", label: "暂无新通知", disabled: true },
];

export function Header({ tenant, project, connectionState }: HeaderProps) {
  const location = useLocation();
  const navigate = useNavigate();
  const { session, logout } = useAuth();
  const [group, page] = breadcrumbFor(location.pathname);

  const tenantName = tenant?.name ?? "默认组织";
  const projectName = project?.name ?? "默认项目";
  const contextTitle = `租户 ${tenant?.slug ?? "default"} · 项目 ${project?.slug ?? "default"}`;

  const userItems: MenuProps["items"] = [
    { key: "profile", label: "个人信息（建设中）", disabled: true },
    { type: "divider" },
    { key: "logout", label: "退出登录" },
  ];
  const userEmail = session?.user.email ?? "";
  const avatar = (userEmail.split("@")[0]?.slice(0, 2) || "EP").toUpperCase();

  const connectionCopy = {
    loading: "控制面连接中",
    connected: "控制面已连接",
    error: "控制面未连接",
  }[connectionState];

  return (
    <header className="topbar">
      <nav className="breadcrumbs" aria-label="面包屑">
        <span>{group}</span>
        <span aria-hidden="true">/</span>
        <strong>{page}</strong>
      </nav>

      <div className="topbar-spacer" />

      <div className="context-chip" title={contextTitle}>
        <Icon name="users" size={13} />
        <span className="context-name">{tenantName}</span>
        <span className="context-divider" aria-hidden="true">
          /
        </span>
        <span className="context-name">{projectName}</span>
      </div>

      <div
        className={`env-chip${connectionState === "error" ? " is-error" : ""}`}
        role="status"
        aria-live="polite"
      >
        <span className="env-dot" aria-hidden="true" />
        {connectionCopy}
      </div>

      <Dropdown menu={{ items: notificationItems }} trigger={["click"]} placement="bottomRight">
        <button type="button" className="icon-button" aria-label="通知" title="通知">
          <Icon name="bell" size={16} />
          <span className="notification-dot" aria-hidden="true" />
        </button>
      </Dropdown>

      <Dropdown menu={{ items: helpItems }} trigger={["click"]} placement="bottomRight">
        <button type="button" className="icon-button" aria-label="帮助" title="帮助">
          <Icon name="help" size={16} />
        </button>
      </Dropdown>

      <Dropdown
        menu={{
          items: userItems,
          onClick: ({ key }) => {
            if (key === "logout") {
              void (async () => {
                await logout();
                navigate("/login", { replace: true });
              })();
            }
          },
        }}
        trigger={["click"]}
        placement="bottomRight"
      >
        <button type="button" className="user-button" aria-label="用户菜单" aria-haspopup="menu">
          <span className="avatar" aria-hidden="true">
            {avatar}
          </span>
          <span className="user-copy">
            <span className="user-name" title={userEmail}>
              {userEmail || "未登录"}
            </span>
            <span className="user-role">{session?.roleLabel ?? "未登录"}</span>
          </span>
          <Icon name="chevronDown" size={13} />
        </button>
      </Dropdown>
    </header>
  );
}
