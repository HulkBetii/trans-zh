import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { useJobs } from "./api/queries";
import { JobStudioPage } from "./pages/JobStudioPage";
import { NewJobPage } from "./pages/NewJobPage";
import { SettingsPage } from "./pages/SettingsPage";

export function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<HomeRoute />} />
        <Route path="new" element={<NewJobPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="jobs/:jobId" element={<Navigate to="pipeline" replace />} />
        <Route path="jobs/:jobId/:tab" element={<JobStudioPage />} />
        <Route path="*" element={<Navigate to="/new" replace />} />
      </Route>
    </Routes>
  );
}

function HomeRoute() {
  const jobs = useJobs({ page: 1, lane: "any" });
  if (jobs.isLoading) return <div className="studio-loading">Đang mở workspace…</div>;
  const active = jobs.data?.items.find((job) => job.lanes?.some((lane) => lane.started && ["queued", "running", "cancelling"].includes(lane.execution_status)));
  const target = active ?? jobs.data?.items[0];
  const activeLane = target?.lanes?.find((lane) => lane.started && ["queued", "running", "cancelling"].includes(lane.execution_status));
  return <Navigate to={target ? `/jobs/${encodeURIComponent(target.job_id)}/${activeLane?.id === "tts" ? "tts" : "pipeline"}` : "/new"} replace />;
}
