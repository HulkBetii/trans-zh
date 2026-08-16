import { AlertTriangle, RotateCcw } from "lucide-react";
import { Link, Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { useJobs } from "./api/queries";
import { errorMessage } from "./lib/format";
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
  const jobs = useJobs({ page: 1, lane: "any" }, { poll: false });
  if (jobs.isLoading) return <div className="studio-loading">Đang mở workspace…</div>;
  // Không nuốt lỗi: server chết mà vẫn chuyển sang /new thì người dùng đọc thành
  // "chưa có công việc nào", rồi tạo lại thứ họ đã có.
  if (jobs.isError) {
    return (
      <div className="page state-page" role="alert">
        <AlertTriangle size={28} />
        <h1>Không đọc được danh sách công việc</h1>
        <p>{errorMessage(jobs.error)}</p>
        <div className="state-page-actions">
          <button className="secondary-button" onClick={() => void jobs.refetch()}>
            <RotateCcw size={16} />Thử lại
          </button>
          <Link className="secondary-button" to="/new">Tạo job mới</Link>
        </div>
      </div>
    );
  }
  const active = jobs.data?.items.find((job) => job.lanes?.some((lane) => lane.started && ["queued", "running", "cancelling"].includes(lane.execution_status)));
  const target = active ?? jobs.data?.items[0];
  const activeLane = target?.lanes?.find((lane) => lane.started && ["queued", "running", "cancelling"].includes(lane.execution_status));
  return <Navigate to={target ? `/jobs/${encodeURIComponent(target.job_id)}/${activeLane?.id === "tts" ? "tts" : "pipeline"}` : "/new"} replace />;
}
