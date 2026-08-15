import { AlertTriangle, CheckCircle2, Clock3, Gauge, ListChecks, MessageSquareQuote, ServerCog } from "lucide-react";
import { Link } from "react-router-dom";
import type { JobDetail, StageDescriptor } from "../../api/types";
import { formatDate } from "../../lib/format";

const attentionLabels: Record<string, { title: string; detail: string }> = {
  segmentation_fallback: { title: "Ngắt câu dùng rule fallback", detail: "Nên rà lại các điểm ngắt trước khi xuất bản." },
  style_unpinned: { title: "Đại từ chủ thể chưa được ghim", detail: "Bổ sung trong Glossary để các batch dịch nhất quán." },
  render_warnings: { title: "Có cảnh báo hiển thị", detail: "CPS hoặc độ dài dòng vượt ngưỡng ở một số cue." },
  stale_overrides: { title: "Có chỉnh sửa đã lỗi thời", detail: "Nguồn cue đã thay đổi; override cũ không được áp dụng." },
  override_base_changed: { title: "Bản máy dưới override đã đổi", detail: "Rà lại các cue chỉnh tay trước khi xuất bản." },
  translations_stale: { title: "Bản dịch chưa cập nhật", detail: "Glossary đã đổi; lưu và chạy dịch lại để cập nhật nội dung." },
  stale_outputs: { title: "Output cần render lại", detail: "Nội dung hiệu lực đã thay đổi sau lần render gần nhất." },
};

const attentionTargets: Record<string, string> = {
  style_unpinned: "glossary",
  translations_stale: "glossary",
  segmentation_fallback: "subtitles?filter=review",
  render_warnings: "subtitles?filter=warnings",
  stale_overrides: "subtitles?filter=review",
  override_base_changed: "subtitles?filter=review",
  stale_outputs: "outputs",
};

export function PipelinePanel({ job, descriptors }: { job: JobDetail; descriptors: StageDescriptor[] }) {
  const activeRun = job.active_run;
  const isAsrIndeterminate = activeRun?.stage === "s1" && activeRun.status === "running";
  return (
    <div className="pipeline-layout">
      <section className="panel-sheet pipeline-sheet">
        <div className="section-heading"><div><span className="eyebrow">Execution log</span><h2>Pipeline</h2></div>{activeRun?.queue_position ? <span className="queue-position">Hàng chờ #{activeRun.queue_position}</span> : null}</div>
        {activeRun && ["queued", "running", "cancelling"].includes(activeRun.status) && (
          <div className="live-run">
            <div className="live-run-head"><div><span className="pulse-dot" /><strong>{activeRun.message || (activeRun.status === "queued" ? "Đang chờ tài nguyên" : "Đang xử lý")}</strong></div><code>{isAsrIndeterminate ? "—" : `${Math.round(activeRun.progress * 100)}%`}</code></div>
            <div className={`progress-track ${isAsrIndeterminate ? "indeterminate" : ""}`} role="progressbar" aria-label={isAsrIndeterminate ? "Đang nhận dạng giọng nói" : "Tiến độ pipeline"} aria-valuemin={0} aria-valuemax={100} aria-valuenow={isAsrIndeterminate ? undefined : Math.round((activeRun.progress ?? 0) * 100)} aria-valuetext={isAsrIndeterminate ? "Đang nhận dạng giọng nói" : undefined}><i style={isAsrIndeterminate ? undefined : { width: `${Math.round((activeRun.progress ?? 0) * 100)}%` }} /></div>
            {isAsrIndeterminate && <p>Đang nhận dạng giọng nói — tiến độ phụ thuộc độ dài và phần cứng.</p>}
          </div>
        )}
        <div className="stage-list">
          {job.stages.map((stage, index) => {
            const descriptor = descriptors.find((item) => item.id === stage.id);
            return (
              <article className={`stage-row row-${stage.status}`} key={stage.id}>
                <span className="stage-index">{String(index).padStart(2, "0")}</span>
                <span className="stage-state-icon">{stage.status === "completed" ? <CheckCircle2 size={18} /> : stage.status === "failed" ? <AlertTriangle size={18} /> : <Clock3 size={18} />}</span>
                <div className="stage-details"><div><strong>{descriptor?.label ?? stage.id}</strong>{stage.cache_hit && <span className="cache-mark">cache</span>}{stage.status === "degraded" && <span className="degraded-mark">degraded</span>}</div><p>{stage.error || stage.message || descriptor?.description || "Chưa chạy"}</p></div>
                <div className="stage-time">{stage.duration_seconds != null ? `${stage.duration_seconds.toFixed(1)}s` : formatDate(stage.finished_at)}</div>
              </article>
            );
          })}
        </div>
      </section>

      <aside className="pipeline-inspector">
        <section className="panel-sheet health-sheet">
          <div className="section-heading"><div><span className="eyebrow">Quality health</span><h2>Sức khỏe</h2></div></div>
          <dl className="health-grid">
            <HealthMetric icon={ListChecks} value={job.health?.segments ?? "—"} label="Cue" />
            <HealthMetric icon={Gauge} value={job.health?.cps_warnings ?? "—"} label="Cảnh báo CPS" />
            <HealthMetric icon={ServerCog} value={job.health?.method ?? "—"} label="Ngắt câu" />
            <HealthMetric icon={MessageSquareQuote} value={job.health?.subject_pronoun || "Chưa ghim"} label="Đại từ chủ thể" />
          </dl>
        </section>
        <section className="panel-sheet attention-sheet">
          <div className="section-heading"><div><span className="eyebrow">Attention</span><h2>Điểm cần rà</h2></div></div>
          {(job.attention_reasons ?? []).length === 0 ? <div className="all-clear"><CheckCircle2 size={18} /><span>Chưa phát hiện vấn đề cần chú ý.</span></div> : (
            <div className="attention-list">{job.attention_reasons?.map((reason) => {
              const copy = attentionLabels[reason] ?? { title: reason, detail: "Kiểm tra thông tin chi tiết của công việc." };
              const target = attentionTargets[reason] ?? "pipeline";
              return <Link className="attention-item" key={reason} to={`/jobs/${encodeURIComponent(job.job_id)}/${target}`}><AlertTriangle size={17} /><div><strong>{copy.title}</strong><p>{copy.detail}</p></div><span className="attention-cta">Mở</span></Link>;
            })}</div>
          )}
        </section>
      </aside>
    </div>
  );
}

function HealthMetric({ icon: Icon, value, label }: { icon: typeof Gauge; value: string | number; label: string }) {
  return <div><dt><Icon size={15} />{label}</dt><dd>{value}</dd></div>;
}
