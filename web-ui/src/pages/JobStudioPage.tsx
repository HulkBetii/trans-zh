import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Ban, CheckCircle2, ChevronRight, LoaderCircle, RotateCcw, Sparkles, Undo2 } from "lucide-react";
import { Navigate, NavLink, useParams } from "react-router-dom";
import { api } from "../api/client";
import { queryKeys, useJob, useMeta, useRunEvents } from "../api/queries";
import type { AllowedAction, JobDetail, RunRecord, StageDescriptor } from "../api/types";
import { ExecutionBadge, QualityBadge } from "../components/StatusBadge";
import { errorMessage } from "../lib/format";
import { GlossaryEditor } from "./studio/GlossaryEditor";
import { OutputsPanel } from "./studio/OutputsPanel";
import { PipelinePanel } from "./studio/PipelinePanel";
import { SubtitleStudio } from "./studio/SubtitleStudio";
import { TtsStudio } from "./studio/TtsStudio";

const coreTabs = [
  { id: "pipeline", label: "Pipeline" },
  { id: "glossary", label: "Glossary" },
  { id: "subtitles", label: "Phụ đề" },
  { id: "outputs", label: "Output" },
] as const;

export function JobStudioPage() {
  const { jobId = "", tab = "pipeline" } = useParams();
  const queryClient = useQueryClient();
  const jobQuery = useJob(jobId);
  const meta = useMeta();
  const job = jobQuery.data;
  const connectionStatus = useRunEvents(jobId, job?.active_run?.run_id);

  const action = useMutation({
    mutationFn: async (type: AllowedAction): Promise<RunRecord | JobDetail | { ok: boolean }> => {
      if (type === "cancel") {
        if (!job?.active_run?.run_id) throw new Error("Không tìm thấy run đang hoạt động.");
        return api.cancelRun(job.active_run.run_id);
      }
      return api[type](jobId);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.job(jobId) });
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });

  if (jobQuery.isLoading) return <StudioLoading />;
  if (jobQuery.isError || !job) return (
    <div className="page state-page"><AlertTriangle size={28} /><h1>Không mở được công việc</h1><p>{errorMessage(jobQuery.error)}</p><button className="secondary-button" onClick={() => void jobQuery.refetch()}>Thử lại</button></div>
  );

  const ttsAvailable = Boolean(
    meta.data?.capabilities.includes("tts") && job.request.targets.includes("vi"),
  );
  if (tab === "tts" && meta.isSuccess && !ttsAvailable) {
    return <Navigate to={`/jobs/${encodeURIComponent(jobId)}/pipeline`} replace />;
  }
  const tabs = ttsAvailable
    ? [coreTabs[0], coreTabs[1], coreTabs[2], { id: "tts", label: "Lồng tiếng" }, coreTabs[3]]
    : coreTabs;
  const activeTab = tabs.some((item) => item.id === tab) ? tab : "pipeline";
  const pipelineLane = job.lanes?.find((lane) => lane.id === "pipeline");
  const ttsLane = job.lanes?.find((lane) => lane.id === "tts" && lane.started);

  return (
    <div className="job-studio">
      <header className="studio-header">
        <div className="studio-title">
          <div className="job-kicker"><code>{job.job_id}</code><ChevronRight size={13} /><span>{job.source?.kind === "url" ? "URL source" : "Local source"}</span></div>
          <h1 title={job.title}>{job.title}</h1>
          <div className="studio-status">
            <span className="studio-lane-status"><b>Phụ đề</b><ExecutionBadge status={pipelineLane?.execution_status ?? job.execution_status} /><QualityBadge status={pipelineLane?.quality_status ?? job.quality_status} /></span>
            {ttsLane && <span className="studio-lane-status"><b>Audio</b><ExecutionBadge status={ttsLane.execution_status} /><QualityBadge status={ttsLane.quality_status} /></span>}
            {job.glossary_stale && <span className="stale-note">Bản dịch chưa cập nhật</span>}
          </div>
        </div>
        <div className="studio-actions">
          {job.allowed_actions.includes("cancel") && <ActionButton label={job.execution_status === "cancelling" ? "Đang dừng…" : "Dừng an toàn"} icon={Ban} pending={action.isPending} onClick={() => action.mutate("cancel")} tone="danger" />}
          {job.allowed_actions.includes("retry") && <ActionButton label="Thử lại" icon={RotateCcw} pending={action.isPending} onClick={() => action.mutate("retry")} />}
          {job.allowed_actions.includes("retranslate") && <ActionButton label="Dịch lại" icon={Sparkles} pending={action.isPending} onClick={() => action.mutate("retranslate")} />}
          {job.allowed_actions.includes("approve") && <ActionButton label="Duyệt phụ đề" icon={CheckCircle2} pending={action.isPending} onClick={() => action.mutate("approve")} />}
          {job.allowed_actions.includes("unapprove") && <ActionButton label="Bỏ duyệt phụ đề" icon={Undo2} pending={action.isPending} onClick={() => action.mutate("unapprove")} />}
        </div>
      </header>

      <StageRail job={job} descriptors={meta.data?.stages ?? []} />

      {connectionStatus !== "live" && <div className={`connection-banner connection-${connectionStatus}`} role="status"><LoaderCircle className={connectionStatus === "offline" ? "" : "spin"} size={15} /><span>{{ connecting: "Đang kết nối tiến độ trực tiếp…", reconnecting: "Mất kết nối tạm thời; đang đồng bộ lại trạng thái…", offline: "Đang offline. Snapshot sẽ được tải lại khi có mạng.", live: "" }[connectionStatus]}</span></div>}
      {action.isError && <div className="inline-alert error"><AlertTriangle size={16} /><span>{errorMessage(action.error)}</span></div>}
      <nav className="studio-tabs" aria-label="Không gian công việc">
        {tabs.map((item) => <NavLink key={item.id} className={activeTab === item.id ? "active" : ""} to={`/jobs/${encodeURIComponent(jobId)}/${item.id}`}>{item.label}</NavLink>)}
      </nav>
      <div className="studio-content">
        {activeTab === "pipeline" && <PipelinePanel job={job} descriptors={meta.data?.stages ?? []} />}
        {activeTab === "glossary" && <GlossaryEditor job={job} />}
        {activeTab === "subtitles" && <SubtitleStudio job={job} />}
        {activeTab === "tts" && ttsAvailable && <TtsStudio job={job} />}
        {activeTab === "outputs" && <OutputsPanel job={job} onRender={() => action.mutate("render")} renderPending={action.isPending} />}
      </div>
    </div>
  );
}

function StageRail({ job, descriptors }: { job: JobDetail; descriptors: StageDescriptor[] }) {
  const stages: StageDescriptor[] = descriptors.length > 0
    ? descriptors
    : job.stages.map((stage) => ({ id: stage.id, label: stage.id }));
  return (
    <div className="stage-rail" aria-label="Tiến độ pipeline">
      {stages.map((descriptor, index) => {
        const state = job.stages.find((stage) => stage.id === descriptor.id);
        const status = state?.status ?? "pending";
        return (
          <div className={`stage-node stage-${status}`} key={descriptor.id} title={state?.message ?? descriptor.label}>
            <div className="stage-track">{index > 0 && <i />}</div>
            <span className="stage-dot">{status === "running" ? <LoaderCircle className="spin" size={13} /> : index}</span>
            <span className="stage-copy"><strong>{descriptor.short_label ?? descriptor.id.toUpperCase()}</strong><small>{descriptor.label}</small></span>
          </div>
        );
      })}
    </div>
  );
}

function ActionButton({ label, icon: Icon, pending, onClick, tone = "default" }: { label: string; icon: typeof Ban; pending: boolean; onClick: () => void; tone?: "default" | "danger" }) {
  return <button className={`secondary-button ${tone === "danger" ? "danger" : ""}`} disabled={pending} onClick={onClick}>{pending ? <LoaderCircle className="spin" size={16} /> : <Icon size={16} />}{label}</button>;
}

function StudioLoading() {
  return <div className="studio-loading"><LoaderCircle className="spin" size={24} /><span>Đang mở Studio…</span></div>;
}
