import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "../components/AppShell";
import { PlaceholderPage } from "../components/PlaceholderPage";
import { EvalsPage } from "../pages/EvalsPage";
import { LoginPage } from "../pages/LoginPage";
import { NotFoundPage } from "../pages/NotFoundPage";
import { OverviewPage } from "../pages/OverviewPage";

const placeholders = [
  {
    path: "datasets",
    eyebrow: "Configuration · Datasets",
    title: "数据集",
    description: "管理数据集来源、版本与用例，冻结每次评测使用的输入快照。",
  },
  {
    path: "assets",
    eyebrow: "Configuration · Agent assets",
    title: "Agent 资产",
    description: "维护 Git 资产源、扫描路径与 Skill / Command / Subagent 清单。",
  },
  {
    path: "models",
    eyebrow: "Configuration · Model credentials",
    title: "模型凭据",
    description: "配置模型供应商与集中式凭据，并验证端点连通性。",
  },
  {
    path: "resources",
    eyebrow: "Configuration · Resource pools",
    title: "资源池",
    description: "管理资源连接、供给模式与 Runner 在线容量。",
  },
  {
    path: "evals/new",
    eyebrow: "Evaluation · Launch",
    title: "发起评测",
    description: "组合数据集、Agent 资产、模型与资源池，冻结输入后创建评测。",
  },
  {
    path: "deployments",
    eyebrow: "Delivery · Deployments",
    title: "交付流水线",
    description: "查看构建、迁移、部署与受控回滚记录。",
  },
];

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<AppShell />}>
        <Route index element={<Navigate to="/overview" replace />} />
        <Route path="/overview" element={<OverviewPage />} />
        <Route path="/evals" element={<EvalsPage />} />
        {placeholders.map((page) => (
          <Route
            key={page.path}
            path={`/${page.path}`}
            element={
              <PlaceholderPage
                eyebrow={page.eyebrow}
                title={page.title}
                description={page.description}
              />
            }
          />
        ))}
        <Route path="/deployment" element={<Navigate to="/deployments" replace />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
