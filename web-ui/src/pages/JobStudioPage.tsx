import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Ban, BookOpenCheck, CheckCircle2, ChevronRight, LoaderCircle, RotateCcw, Sparkles, Undo2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Navigate, NavLink, useParams } from "react-router-dom";
import { ApiError, api } from "../api/client";
import { queryKeys, useJob, useMeta, useRunEvents } from "../api/queries";
import type { AllowedAction, JobDetail, RunRecord, StageDescriptor, TranslationCacheMode, TranslationSummary } from "../api/types";
import { ExecutionBadge, QualityBadge } from "../components/StatusBadge";
import { useConfirm, type Confirm } from "../hooks/useConfirm";
import { errorMessage, executionLabels, formatDate } from "../lib/format";
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
const activeRunStatuses = new Set(["queued", "running", "cancelling"]);
const terminalRunStatuses = new Set(["completed", "cancelled", "failed", "interrupted"]);

export function JobStudioPage() {
  const { jobId = "", tab = "pipeline" } = useParams();
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const jobQuery = useJob(jobId);
  const meta = useMeta();
  const job = jobQuery.data;
  const pipelineLane = job?.lanes?.find((lane) => lane.id === "pipeline");
  const [lastQueuedRun, setLastQueuedRun] = useState<RunRecord | null>(null);
  const streamRun = lastQueuedRun && activeRunStatuses.has(lastQueuedRun.status)
    ? lastQueuedRun
    : pipelineLane?.active_run ?? job?.active_run;
  const connectionStatus = useRunEvents(jobId, streamRun?.run_id);

  useEffect(() => {
    if (!lastQueuedRun) return;
    const serverRun = pipelineLane?.active_run?.run_id === lastQueuedRun.run_id
      ? pipelineLane.active_run
      : pipelineLane?.latest_run?.run_id === lastQueuedRun.run_id
        ? pipelineLane.latest_run
        : null;
    if (
      serverRun
      && (
        serverRun.status !== lastQueuedRun.status
        || serverRun.event_seq > lastQueuedRun.event_seq
        || terminalRunStatuses.has(serverRun.status)
      )
    ) setLastQueuedRun(null);
  }, [lastQueuedRun, pipelineLane?.active_run, pipelineLane?.latest_run]);

  const queuePipelineRun = useCallback((run: RunRecord) => {
    setLastQueuedRun(run);
    queryClient.setQueryData(queryKeys.job(jobId), (current: JobDetail | undefined) => {
      if (!current) return current;
      const lanes = current.lanes?.map((lane) => lane.id === "pipeline" ? {
        ...lane,
        execution_status: run.status,
        stage: run.stage,
        progress: run.progress,
        message: run.message,
        error: run.error,
        active_run: run,
        latest_run: run,
        allowed_actions: ["cancel"] as const,
      } : lane);
      return {
        ...current,
        ...(lanes ? { lanes } : {}),
        active_run: run,
        execution_status: run.status,
        stage: run.stage,
        progress: run.progress,
        message: run.message,
        error: run.error,
        allowed_actions: ["cancel"],
      };
    });
    void queryClient.invalidateQueries({ queryKey: ["jobs"] });
  }, [jobId, queryClient]);

  const action = useMutation({
    mutationFn: async (type: Exclude<AllowedAction, "retranslate">): Promise<RunRecord | JobDetail | { ok: boolean }> => {
      if (type === "cancel") {
        const activeRun = lastQueuedRun && activeRunStatuses.has(lastQueuedRun.status)
          ? lastQueuedRun
          : job?.active_run;
        if (!activeRun?.run_id) throw new Error("Không tìm thấy run đang hoạt động.");
        return api.cancelRun(activeRun.run_id);
      }
      if (type === "retry") return api.retry(jobId);
      if (type === "render") return api.render(jobId);
      if (type === "approve") return api.approve(jobId);
      return api.unapprove(jobId);
    },
    onSuccess: (result) => {
      if (isRunRecord(result)) queuePipelineRun(result);
      void queryClient.invalidateQueries({ queryKey: queryKeys.job(jobId) });
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });

  const retranslate = useMutation({
    mutationFn: async (cacheMode: TranslationCacheMode): Promise<RunRecord | null> => {
      let estimate = await api.estimateRetranslate(jobId, cacheMode);
      const confirmed = await confirmRetranslation(confirm, estimate, cacheMode === "bypass");
      if (!confirmed) return null;
      try {
        return await api.retranslate(jobId, {
          cache_mode: cacheMode,
          confirmed_gpt_units: estimate.gpt_units,
        });
      } catch (error) {
        const refreshed = confirmationEstimate(error);
        if (!refreshed) throw error;
        estimate = refreshed;
        const reconfirmed = await confirmRetranslation(confirm, estimate, true);
        if (!reconfirmed) return null;
        return api.retranslate(jobId, {
          cache_mode: cacheMode,
          confirmed_gpt_units: estimate.gpt_units,
        });
      }
    },
    onSuccess: (run) => {
      if (run) queuePipelineRun(run);
      void queryClient.invalidateQueries({ queryKey: queryKeys.job(jobId) });
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });

  if (jobQuery.isLoading) return <StudioLoading />;
  if (jobQuery.isError || !job) return (
    <div className="page state-page"><AlertTriangle size={28} /><h1>Không mở được công việc</h1><p>{errorMessage(jobQuery.error)}</p><button className="secondary-button" onClick={() => void jobQuery.refetch()}>Thử lại</button></div>
  );

  const ttsAvailable = Boolean(
    meta.data?.capabilities.includes("tts") && (job.request.targets.includes("vi") || job.request.targets.includes("en")),
  );
  if (tab === "tts" && meta.isSuccess && !ttsAvailable) {
    return <Navigate to={`/jobs/${encodeURIComponent(jobId)}/pipeline`} replace />;
  }
  const tabs = ttsAvailable
    ? [coreTabs[0], coreTabs[1], coreTabs[2], { id: "tts", label: "Lồng tiếng" }, coreTabs[3]]
    : coreTabs;
  const activeTab = tabs.some((item) => item.id === tab) ? tab : "pipeline";
  const ttsLane = job.lanes?.find((lane) => lane.id === "tts" && lane.started);
  const matchingServerRun = lastQueuedRun
    ? [pipelineLane?.active_run, pipelineLane?.latest_run].find((run) => run?.run_id === lastQueuedRun.run_id)
    : null;
  const displayRun = matchingServerRun ?? lastQueuedRun ?? pipelineLane?.active_run ?? pipelineLane?.latest_run ?? null;
  const pipelineBusy = Boolean(displayRun && activeRunStatuses.has(displayRun.status));
  const actionsPending = action.isPending || retranslate.isPending;

  return (
    <div className="job-studio">
      <header className="studio-header">
        <div className="studio-title">
          <div className="job-kicker"><code>{job.job_id}</code><ChevronRight size={13} /><span>{job.source?.kind === "url" ? "URL source" : "Local source"}</span></div>
          <h1 title={job.title}>{job.title}</h1>
          <div className="studio-status">
            <span className="studio-lane-status"><b>Phụ đề</b><ExecutionBadge status={pipelineBusy && displayRun ? displayRun.status : pipelineLane?.execution_status ?? job.execution_status} /><QualityBadge status={pipelineLane?.quality_status ?? job.quality_status} /></span>
            {ttsLane && <span className="studio-lane-status"><b>Audio</b><ExecutionBadge status={ttsLane.execution_status} /><QualityBadge status={ttsLane.quality_status} /></span>}
            {job.glossary_stale && <span className="stale-note">Bản dịch chưa cập nhật</span>}
          </div>
        </div>
        <div className="studio-actions">
          {pipelineBusy && <ActionButton label={displayRun?.status === "cancelling" ? "Đang dừng…" : "Dừng an toàn"} icon={Ban} pending={actionsPending} onClick={() => action.mutate("cancel")} tone="danger" />}
          {!pipelineBusy && job.allowed_actions.includes("retry") && <ActionButton label="Thử lại" icon={RotateCcw} pending={actionsPending} onClick={() => action.mutate("retry")} />}
          {!pipelineBusy && job.allowed_actions.includes("retranslate") && <ActionButton label="Cập nhật theo Glossary" icon={BookOpenCheck} pending={actionsPending} onClick={() => retranslate.mutate("reuse")} />}
          {!pipelineBusy && job.allowed_actions.includes("retranslate") && <ActionButton label="Dịch mới bằng GPT" icon={Sparkles} pending={actionsPending} onClick={() => retranslate.mutate("bypass")} />}
          {!pipelineBusy && job.allowed_actions.includes("approve") && <ActionButton label="Duyệt phụ đề" icon={CheckCircle2} pending={actionsPending} onClick={() => action.mutate("approve")} />}
          {!pipelineBusy && job.allowed_actions.includes("unapprove") && <ActionButton label="Bỏ duyệt phụ đề" icon={Undo2} pending={actionsPending} onClick={() => action.mutate("unapprove")} />}
        </div>
      </header>

      <StageRail job={job} descriptors={meta.data?.stages ?? []} />

      <PipelineRunBanner run={displayRun} />

      {connectionStatus !== "live" && <div className={`connection-banner connection-${connectionStatus}`} role="status"><LoaderCircle className={connectionStatus === "offline" ? "" : "spin"} size={15} /><span>{{ connecting: "Đang kết nối tiến độ trực tiếp…", reconnecting: "Mất kết nối tạm thời; đang đồng bộ lại trạng thái…", offline: "Đang offline. Snapshot sẽ được tải lại khi có mạng.", live: "" }[connectionStatus]}</span></div>}
      {action.isError && <div className="inline-alert error"><AlertTriangle size={16} /><span>{errorMessage(action.error)}</span></div>}
      {retranslate.isError && <div className="inline-alert error"><AlertTriangle size={16} /><span>{errorMessage(retranslate.error)}</span></div>}
      <nav className="studio-tabs" aria-label="Không gian công việc">
        {tabs.map((item) => <NavLink key={item.id} className={activeTab === item.id ? "active" : ""} to={`/jobs/${encodeURIComponent(jobId)}/${item.id}`}>{item.label}</NavLink>)}
      </nav>
      <div className="studio-content">
        {activeTab === "pipeline" && <PipelinePanel job={job} descriptors={meta.data?.stages ?? []} />}
        {activeTab === "glossary" && <GlossaryEditor job={job} onUpdateTranslations={() => retranslate.mutateAsync("reuse")} />}
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

async function confirmRetranslation(
  confirm: Confirm,
  estimate: TranslationSummary,
  alwaysConfirm: boolean,
): Promise<boolean> {
  if (!alwaysConfirm && estimate.gpt_units === 0) return true;
  const languageBreakdown = estimate.by_language
    .map((item) => `${item.language.toUpperCase()}: ${item.gpt_units}/${item.total_units}`)
    .join(" · ");
  const fresh = estimate.cache_mode === "bypass";
  return confirm({
    title: fresh ? "Dịch mới toàn bộ bằng GPT?" : "Glossary mới cần gọi GPT",
    detail: fresh
      ? "Bản dịch máy hiện tại sẽ được tạo lại từ đầu. Subtitle chỉnh tay vẫn được giữ dưới dạng override."
      : "Studio sẽ dùng cache cho phần còn hợp lệ và chỉ gửi những lượt còn thiếu tới GPT.",
    cost: `${estimate.gpt_units} lượt cue-ngôn ngữ cần GPT${languageBreakdown ? ` · ${languageBreakdown}` : ""}. Số request thực tế phụ thuộc batching và retry.`,
    confirmLabel: fresh ? "Dịch mới" : "Cập nhật bản dịch",
  });
}

function confirmationEstimate(error: unknown): TranslationSummary | null {
  if (!(error instanceof ApiError) || error.code !== "gpt_confirmation_required") return null;
  if (typeof error.body !== "object" || error.body === null || !("detail" in error.body)) return null;
  const detail = (error.body as { detail?: unknown }).detail;
  if (typeof detail !== "object" || detail === null || !("estimate" in detail)) return null;
  const estimate = (detail as { estimate?: unknown }).estimate;
  if (typeof estimate !== "object" || estimate === null) return null;
  const candidate = estimate as Partial<TranslationSummary>;
  return typeof candidate.cache_mode === "string"
    && typeof candidate.gpt_units === "number"
    && Array.isArray(candidate.by_language)
    ? candidate as TranslationSummary
    : null;
}

function PipelineRunBanner({ run }: { run: RunRecord | null }) {
  if (!run || (run.kind === "pipeline" && run.status === "completed")) return null;
  const active = activeRunStatuses.has(run.status);
  const terminal = terminalRunStatuses.has(run.status);
  const summary = run.translation_summary;
  const title = runTitle(run);
  const duration = run.started_at && run.finished_at
    ? Math.max(0, new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()) / 1000
    : null;

  return (
    <section className={`pipeline-run-banner run-${run.status}`} role={run.status === "failed" ? "alert" : "status"} aria-live="polite">
      <div className="pipeline-run-main">
        <span className="pipeline-run-icon">{active ? <LoaderCircle className="spin" size={18} /> : run.status === "completed" ? <CheckCircle2 size={18} /> : <AlertTriangle size={18} />}</span>
        <div className="pipeline-run-copy">
          <div className="pipeline-run-heading"><strong>{title}</strong><span>{executionLabels[run.status]}</span></div>
          <p>{run.error || run.message || (active ? "Đang chuẩn bị tác vụ…" : "Tác vụ đã kết thúc.")}</p>
          {summary && <div className="translation-run-summary">
            <span>{summary.estimated && active ? "Dự kiến" : "Kết quả"}</span>
            <b>{summary.cache_hits} cache</b>
            <b>{summary.gpt_units} GPT</b>
            <small>{summary.total_units} lượt dịch · {summary.total_cues} cue</small>
          </div>}
        </div>
      </div>
      {active && <div className="pipeline-run-progress" role="progressbar" aria-label={`Tiến độ ${title}`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(run.progress * 100)}><i style={{ width: `${Math.max(2, run.progress * 100)}%` }} /></div>}
      {terminal && <div className="pipeline-run-meta"><span>{run.finished_at ? `Kết thúc ${formatDate(run.finished_at)}` : "Đã kết thúc"}</span>{duration != null && <span>{duration < 10 ? `${duration.toFixed(1)} giây` : `${Math.round(duration)} giây`}</span>}<code>{run.run_id}</code></div>}
    </section>
  );
}

function runTitle(run: RunRecord): string {
  if (run.kind === "retranslate") {
    return run.translation_summary?.cache_mode === "bypass"
      ? "Dịch mới bằng GPT"
      : "Cập nhật theo Glossary";
  }
  return {
    retry: "Thử lại pipeline",
    render: "Render lại output",
  }[run.kind] ?? "Pipeline";
}

function isRunRecord(value: RunRecord | JobDetail | { ok: boolean }): value is RunRecord {
  return "run_id" in value;
}

function ActionButton({ label, icon: Icon, pending, onClick, tone = "default" }: { label: string; icon: typeof Ban; pending: boolean; onClick: () => void; tone?: "default" | "danger" }) {
  return <button className={`secondary-button ${tone === "danger" ? "danger" : ""}`} disabled={pending} onClick={onClick}>{pending ? <LoaderCircle className="spin" size={16} /> : <Icon size={16} />}{label}</button>;
}

function StudioLoading() {
  return <div className="studio-loading"><LoaderCircle className="spin" size={24} /><span>Đang mở Studio…</span></div>;
}
