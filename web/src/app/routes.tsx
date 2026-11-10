import { Navigate, Route, Routes } from "react-router-dom";
import { RequireAdmin, RequireAuth } from "./auth";
import { AppShell } from "../components/AppShell";
import { PlaceholderPage } from "../components/PlaceholderPage";
import { DatasetDetailPage } from "../pages/DatasetDetailPage";
import { DatasetImportPage } from "../pages/DatasetImportPage";
import { DatasetsPage } from "../pages/DatasetsPage";
import { EvalDetailPage } from "../pages/EvalDetailPage";
import { EvalWizardPage } from "../pages/EvalWizardPage";
import { EvalsPage } from "../pages/EvalsPage";
import { LoginPage } from "../pages/LoginPage";
import { ModelsPage } from "../pages/ModelsPage";
import { NotFoundPage } from "../pages/NotFoundPage";
import { OperationsPage } from "../pages/OperationsPage";
import { OverviewPage } from "../pages/OverviewPage";

const placeholders = [
  {
    path: "assets",
    eyebrow: "Configuration · Agent assets",
    title: "Agent 资产",
    description: "维护 Git 资产源、扫描路径与 Skill / Command / Subagent 清单。",
  },
  {
    path: "resources",
    eyebrow: "Configuration · Resource pools",
    title: "资源池",
    description: "管理资源连接、供给模式与 Runner 在线容量。",
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
      <Route element={<RequireAuth />}>
        <Route element={<AppShell />}>
          <Route index element={<Navigate to="/overview" replace />} />
          <Route path="/overview" element={<OverviewPage />} />
          <Route path="/evals" element={<EvalsPage />} />
          <Route path="/evals/new" element={<EvalWizardPage />} />
          <Route path="/evals/:runId" element={<EvalDetailPage />} />
          <Route path="/datasets" element={<DatasetsPage />} />
          <Route path="/datasets/import" element={<DatasetImportPage />} />
          <Route path="/datasets/:datasetId" element={<DatasetDetailPage />} />
          <Route path="/models" element={<ModelsPage />} />
          <Route element={<RequireAdmin />}>
            <Route path="/operations" element={<OperationsPage />} />
          </Route>
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
      </Route>
    </Routes>
  );
}
