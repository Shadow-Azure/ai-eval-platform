import { Outlet } from "react-router-dom";
import { useOverviewQuery } from "../api/overview";
import { Header } from "./Header";
import { Sidebar } from "./Sidebar";

export function AppShell() {
  const overviewQuery = useOverviewQuery();
  const connectionState = overviewQuery.data
    ? "connected"
    : overviewQuery.isError
      ? "error"
      : "loading";

  return (
    <div className="app-shell">
      <Sidebar tenant={overviewQuery.data?.tenant} />
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
