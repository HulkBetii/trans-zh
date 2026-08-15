import { AlertTriangle, Ban, Check, LoaderCircle, PanelLeftClose, Plus, Search } from "lucide-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useDeferredValue, useEffect, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { api } from "../api/client";
import { useJobs } from "../api/queries";
import type { JobLaneSummary, JobListItem } from "../api/types";
import { errorMessage } from "../lib/format";
import { ExecutionBadge } from "./StatusBadge";

type Filter = "all" | "active" | "attention" | "finished";

export function JobNavigator({ onNavigate, onCollapse }: { onNavigate?: () => void; onCollapse?: () => void }) {
  const location = useLocation();
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [page, setPage] = useState(1);
  const deferredSearch = useDeferredValue(search);
  const query = useJobs({
    search: deferredSearch || undefined,
    execution: filter === "active" ? "queued,running,cancelling" : filter === "finished" ? "completed,failed,cancelled,interrupted" : undefined,
    quality: filter === "attention" ? "needs_review,degraded" : undefined,
    lane: filter === "active" || filter === "attention" ? "any" : undefined,
    page,
  });
  const cancelRun = useMutation({
    mutationFn: ({ runId }: { runId: string; jobId: string }) => api.cancelRun(runId),
    onSuccess: (_result, variables) => {
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
      void queryClient.invalidateQueries({ queryKey: ["job", variables.jobId] });
    },
  });

  useEffect(() => setPage(1), [deferredSearch, filter]);

  const jobs = query.data?.items ?? [];

  return (
    <div className="job-navigator">
      <div className="navigator-heading">
        <div><span className="eyebrow">Production queue</span><h1>Công việc</h1></div>
        <div className="navigator-heading-actions">
          {onCollapse && <button className="icon-button" onClick={onCollapse} aria-label="Thu gọn cột công việc"><PanelLeftClose size={18} /></button>}
          <NavLink className="square-action" to="/new" aria-label="Tạo job mới" onClick={onNavigate}><Plus size={18} /></NavLink>
        </div>
      </div>
      <label className="search-box">
        <Search size={16} aria-hidden="true" />
        <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Tìm nguồn hoặc job ID" />
      </label>
      <div className="navigator-filters" aria-label="Lọc công việc">
        {(["all", "active", "attention", "finished"] as const).map((value) => (
          <button key={value} className={filter === value ? "active" : ""} aria-pressed={filter === value} onClick={() => setFilter(value)}>
            {{ all: "Tất cả", active: "Đang chạy", attention: "Cần rà", finished: "Đã xong" }[value]}
          </button>
        ))}
      </div>
      <div className="job-list" aria-live="polite">
        {query.isLoading && <NavigatorMessage icon={<LoaderCircle className="spin" size={18} />} text="Đang lấy danh sách…" />}
        {query.isError && <NavigatorMessage icon={<AlertTriangle size={18} />} text={errorMessage(query.error)} action={() => void query.refetch()} />}
        {!query.isLoading && !query.isError && jobs.length === 0 && (
          <NavigatorMessage icon={<ClapperIcon />} text={search ? "Không tìm thấy công việc phù hợp." : "Chưa có công việc nào."} />
        )}
        {jobs.map((job) => <JobRow key={job.job_id} job={job} active={location.pathname.startsWith(`/jobs/${encodeURIComponent(job.job_id)}/`)} filter={filter} onNavigate={onNavigate} onCancel={(lane) => lane.active_run && cancelRun.mutate({ runId: lane.active_run.run_id, jobId: job.job_id })} cancelPending={cancelRun.isPending && cancelRun.variables?.jobId === job.job_id} />)}
      </div>
      {query.data && query.data.total > query.data.page_size && (
        <div className="navigator-pagination">
          <button disabled={query.data.page <= 1} onClick={() => setPage((current) => Math.max(1, current - 1))}>Trước</button>
          <span>{query.data.page} / {Math.ceil(query.data.total / query.data.page_size)}</span>
          <button disabled={query.data.page * query.data.page_size >= query.data.total} onClick={() => setPage((current) => current + 1)}>Sau</button>
        </div>
      )}
    </div>
  );
}

function JobRow({ job, active, filter, onNavigate, onCancel, cancelPending }: { job: JobListItem; active: boolean; filter: Filter; onNavigate?: () => void; onCancel: (lane: JobLaneSummary) => void; cancelPending: boolean }) {
  const pipeline = job.lanes?.find((lane) => lane.id === "pipeline");
  const tts = job.lanes?.find((lane) => lane.id === "tts" && lane.started);
  const activeLane = [tts, pipeline].find((lane) => lane && ["queued", "running", "cancelling"].includes(lane.execution_status));
  const attentionLane = tts && tts.quality_status !== "approved" && pipeline?.quality_status === "approved" ? tts : pipeline;
  const targetLane = activeLane ?? (filter === "attention" ? attentionLane : pipeline);
  const detail = targetLane?.message || targetLane?.error || (targetLane?.quality_status === "degraded" ? "Có cảnh báo cần xem" : targetLane?.quality_status === "needs_review" ? "Chờ rà soát" : targetLane?.stage || "Sẵn sàng");
  const href = `/jobs/${encodeURIComponent(job.job_id)}/${targetLane?.id === "tts" ? "tts" : "pipeline"}`;
  return (
    <div className={`job-row-shell ${active ? "active" : ""}`}>
      <NavLink className="job-row" to={href} onClick={onNavigate}>
        <div className="job-row-top">
          <strong>{job.title}</strong>
          {pipeline?.quality_status === "approved" && (!tts || tts.quality_status === "approved") ? <Check size={15} aria-label="Đã duyệt" /> : null}
        </div>
        <div className="job-lanes">
          {pipeline && <LaneBadge label="Phụ đề" lane={pipeline} />}
          {tts && <LaneBadge label="Audio" lane={tts} />}
        </div>
        <div className="job-row-detail"><span>{detail}</span></div>
        {activeLane && <div className="row-progress" role="progressbar" aria-label={`Tiến độ ${activeLane.id === "tts" ? "audio" : "phụ đề"}`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round((activeLane.progress ?? 0) * 100)}><i style={{ width: `${Math.round((activeLane.progress ?? 0) * 100)}%` }} /></div>}
        <code>{job.job_id}</code>
      </NavLink>
      {activeLane?.allowed_actions?.includes("cancel") && <button className="job-row-cancel" disabled={cancelPending || activeLane.execution_status === "cancelling"} onClick={() => onCancel(activeLane)} aria-label={`Dừng an toàn ${activeLane.id === "tts" ? "audio" : "pipeline"} của ${job.title}`}>{cancelPending ? <LoaderCircle className="spin" size={14} /> : <Ban size={14} />}</button>}
    </div>
  );
}

function LaneBadge({ label, lane }: { label: string; lane: JobLaneSummary }) {
  return <span className="job-lane-badge"><b>{label}</b><ExecutionBadge status={lane.execution_status} /></span>;
}

function NavigatorMessage({ icon, text, action }: { icon: React.ReactNode; text: string; action?: () => void }) {
  return <div className="navigator-message">{icon}<span>{text}</span>{action && <button onClick={action}>Thử lại</button>}</div>;
}

function ClapperIcon() {
  return <span className="empty-glyph" aria-hidden="true">字</span>;
}
