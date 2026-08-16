import { AudioLines, LoaderCircle, Square } from "lucide-react";
import type { RunRecord, TtsExecutionStatus, TtsWorkspace } from "../../../api/types";
import { formatBytes, formatDate } from "../../../lib/format";
import { TtsOutputState } from "./TtsBadges";
import { ttsExecutionLabels } from "./labels";

export function TtsRunPanel({ run, onCancel, pending }: { run: RunRecord; onCancel: () => void; pending: boolean }) {
  const terminal = ["completed", "cancelled", "failed", "interrupted"].includes(run.status);
  const kindLabel = run.kind === "tts_preview" ? "nghe thử" : run.kind === "tts_calibrate" ? "hiệu chuẩn giọng" : "MP3 timeline";
  const title = terminal ? `${ttsExecutionLabels[run.status as TtsExecutionStatus]} · ${kindLabel}` : `Đang tạo ${kindLabel}`;
  return (
    <section className="tts-run-panel" aria-live="polite">
      <div className="tts-run-head"><div><span className={terminal ? "run-state-dot" : "pulse-dot"} /><strong>{title}</strong><code>{run.run_id}</code></div>{!terminal && <button className="secondary-button compact danger" disabled={pending} onClick={onCancel}>{pending ? <LoaderCircle className="spin" size={14} /> : <Square size={14} />}Dừng an toàn</button>}</div>
      <p>{run.message || ttsExecutionLabels[run.status as TtsExecutionStatus]}</p>
      <div className="progress-track" role="progressbar" aria-label={`Tiến độ ${kindLabel}`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round((run.progress ?? 0) * 100)}><i style={{ width: `${Math.round((run.progress ?? 0) * 100)}%` }} /></div>
      <div className="tts-run-meta"><span>{Math.round((run.progress ?? 0) * 100)}%</span>{run.queue_position ? <span>Vị trí hàng đợi {run.queue_position}</span> : null}<span>{formatDate(run.started_at ?? run.created_at)}</span>{terminal && <span>Kết thúc {formatDate(run.finished_at)}</span>}{run.error && <span className="tts-danger">{run.error}</span>}</div>
    </section>
  );
}

export function TtsOutput({ output, stale }: { output: NonNullable<TtsWorkspace["output"]>; stale: boolean }) {
  const href = output.download_url ?? undefined;
  const available = output.state !== "missing" && Boolean(href);
  const visibleState = output.state === "missing" ? "missing" : stale ? "stale" : output.state;
  return (
    <section className={`tts-output panel-sheet ${stale || output.state !== "current" ? "stale" : ""}`}>
      <div className="tts-output-heading"><div><span className="eyebrow">Timeline deliverable</span><h2>Audio lồng tiếng tiếng Việt</h2><p>{stale || output.state !== "current" ? "Output không còn khớp với văn bản hiện tại; cần tạo lại." : "MP3 đã ráp theo mốc thời gian ASR."}</p></div>{available && <a className="secondary-button compact" href={href} download><AudioLines size={15} />Tải MP3</a>}</div>
      {!available ? <p className="tts-danger">{output.state === "missing" ? "File không còn trên đĩa." : "Không có đường dẫn tải an toàn cho file này."}</p> : <audio className="tts-timeline-audio" src={href} controls preload="metadata" aria-label="Nghe audio lồng tiếng tiếng Việt" />}
      <div className="tts-output-meta"><span>{output.name}</span><span>{formatBytes(output.size_bytes)}</span><span>{formatDate(output.created_at)}</span><TtsOutputState state={visibleState} /></div>
    </section>
  );
}
