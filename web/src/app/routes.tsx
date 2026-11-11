import { Navigate, Route, Routes } from "react-router-dom";
import { RequireAdmin, RequireAuth } from "./auth";
import { AppShell } from "../components/AppShell";
import { AssetsPage } from "../pages/AssetsPage";
import { DatasetDetailPage } from "../pages/DatasetDetailPage";
import { DeploymentsPage } from "../pages/DeploymentsPage";
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
import { ResourcesPage } from "../pages/ResourcesPage";
import { TracePage } from "../pages/TracePage";

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
          <Route path="/evals/:runId/cases/:caseRunId/trace" element={<TracePage />} />
          <Route path="/evals/:runId" element={<EvalDetailPage />} />
          <Route path="/datasets" element={<DatasetsPage />} />
          <Route path="/datasets/import" element={<DatasetImportPage />} />
          <Route path="/datasets/:datasetId" element={<DatasetDetailPage />} />
          <Route path="/models" element={<ModelsPage />} />
          <Route path="/assets" element={<AssetsPage />} />
          <Route path="/resources" element={<ResourcesPage />} />
          <Route path="/deployments" element={<DeploymentsPage />} />
          <Route element={<RequireAdmin />}>
            <Route path="/operations" element={<OperationsPage />} />
          </Route>
          <Route path="/deployment" element={<Navigate to="/deployments" replace />} />
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Route>
    </Routes>
  );
}
