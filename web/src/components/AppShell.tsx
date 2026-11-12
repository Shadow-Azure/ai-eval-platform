import { useCallback, useState } from "react";
import { Outlet } from "react-router-dom";
import { useOverviewQuery } from "../api/overview";
import { Header } from "./Header";
import { Sidebar } from "./Sidebar";

const SIDEBAR_STORAGE_KEY = "ep.sidebar.collapsed";

function readSidebarCollapsed(): boolean {
  try {
    return window.localStorage.getItem(SIDEBAR_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

export function AppShell() {
  const overviewQuery = useOverviewQuery();
  const [sidebarCollapsed, setSidebarCollapsed] = useState(readSidebarCollapsed);
  const connectionState = overviewQuery.data
    ? "connected"
    : overviewQuery.isError
      ? "error"
      : "loading";

  const toggleSidebar = useCallback(() => {
    const next = !sidebarCollapsed;
    setSidebarCollapsed(next);
    try {
      window.localStorage.setItem(SIDEBAR_STORAGE_KEY, next ? "1" : "0");
    } catch {
      // The preference is cosmetic; private or sandboxed contexts may deny storage.
    }
  }, [sidebarCollapsed]);

  return (
    <div className={`app-shell${sidebarCollapsed ? " sidebar-collapsed" : ""}`}>
      <Sidebar
        tenant={overviewQuery.data?.tenant}
        collapsed={sidebarCollapsed}
        onToggle={toggleSidebar}
      />
      <div className="main-shell">
        <Header
          tenant={overviewQuery.data?.tenant}
          project={overviewQuery.data?.project}
          connectionState={connectionState}
        />
        <Outlet />
      </div>
    </div>
  );
}
