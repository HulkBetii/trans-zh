import { AlertTriangle, Check, ChevronRight, Clock3, Info, LoaderCircle, Play, RefreshCcw, X } from "lucide-react";
import type { TtsCue } from "../../../api/types";
import { formatTime } from "../../../lib/format";
import { overrideLabels } from "./labels";

export function TtsCueRow({ cue, effective, selected, dirty, onSelect }: { cue: TtsCue; effective: string; selected: boolean; dirty: boolean; onSelect: () => void }) {
  const tight = (cue.overflow_seconds ?? 0) > 0;
  return (
    <button className={`cue-row tts-cue-row ${selected ? "selected" : ""}`} aria-pressed={selected} onClick={onSelect}>
      <span className="cue-row-head"><code>{String(cue.segment_id).padStart(4, "0")}</code><time>{formatTime(cue.start).slice(3, -1)}</time>{tight && <AlertTriangle size={13} />}{(dirty || cue.override_state !== "none") && <i title="Đã chỉnh sửa" />}{cue.preview_state === "current" && <Check size={12} aria-label="Có bản nghe thử" />}</span>
      <span className="cue-source">{cue.subtitle_text}</span>
      <span className="cue-target">{effective}</span>
    </button>
  );
}

export function TtsCueEditor({ cue, effective, dirty, locked, voiceDirty, inspectorOpen, onChange, onReset, onOpenInspector, onPreview, previewPending, canPreview }: { cue: TtsCue; effective: string; dirty: boolean; locked: boolean; voiceDirty: boolean; inspectorOpen: boolean; onChange: (value: string) => void; onReset: () => void; onOpenInspector: () => void; onPreview: () => void; previewPending: boolean; canPreview: boolean }) {
  const hasPreview = cue.preview_state === "current" && Boolean(cue.preview_url) && !dirty && !voiceDirty;
  return (
    <div className="tts-translation-editor">
      <div className="editor-cue-head"><code>#{String(cue.segment_id).padStart(4, "0")}</code><span><Clock3 size={14} />{formatTime(cue.start)} <ChevronRight size={12} /> {formatTime(cue.end)}</span><button className="text-button inspector-trigger" aria-expanded={inspectorOpen} onClick={onOpenInspector}><Info size={14} />Timing</button></div>
      <div className="tts-subtitle-copy"><span>Phụ đề (chỉ đọc)</span><p>{cue.subtitle_text}</p></div>
      <div className="tts-auto-copy"><span>Tự động chuẩn hóa để đọc</span><p>{cue.default_spoken_text}</p></div>
      <label className="effective-editor tts-spoken-editor"><span>Văn bản sẽ đọc</span><textarea aria-label="Văn bản sẽ đọc" value={effective} disabled={locked} onChange={(event) => onChange(event.target.value)} rows={4} /></label>
      {effective.trim().length === 0 && <p className="field-error"><AlertTriangle size={14} />Văn bản đọc không được để trống.</p>}
      <div className="tts-editor-actions"><span>{dirty ? "Bản thay đổi chưa lưu" : "Override chỉ ảnh hưởng audio, không sửa phụ đề."}</span><button className="text-button" disabled={locked || (!dirty && cue.override_state === "none")} onClick={onReset}><RefreshCcw size={14} />Về tự động</button></div>
      <div className="tts-preview-row">
        {hasPreview ? <audio src={cue.preview_url ?? undefined} controls preload="metadata" aria-label={`Nghe thử cue ${cue.segment_id}`} /> : <button className="secondary-button" disabled={!canPreview || previewPending || locked} title={voiceDirty ? "Lưu giọng mới trước khi nghe thử" : undefined} onClick={onPreview}>{previewPending ? <LoaderCircle className="spin" size={16} /> : <Play size={16} />} {dirty ? "Lưu cue này & nghe thử · tốn 1 lượt TTS" : "Nghe thử · tốn 1 lượt TTS"}</button>}
        {cue.preview_state === "stale" && <span className="tts-warning"><AlertTriangle size={14} />Bản nghe thử cũ</span>}
        {voiceDirty && <span className="tts-warning"><AlertTriangle size={14} />Lưu giọng mới để tạo đúng bản nghe thử</span>}
      </div>
    </div>
  );
}

export function TtsInspector({ cue, dirty, onClose }: { cue: TtsCue; dirty: boolean; onClose: () => void }) {
  const overflow = cue.overflow_seconds ?? 0;
  return (
    <div className="inspector-inner">
      <div className="pane-title"><span>Timing inspector</span><button className="icon-button inspector-close" onClick={onClose} aria-label="Đóng inspector"><X size={17} /></button></div>
      <dl className="cue-metrics tts-metrics"><div><dt>Khoảng đọc</dt><dd>{cue.room_seconds.toFixed(2)}s</dd></div><div><dt>Ước tính</dt><dd>{cue.predicted_duration == null ? "—" : `${cue.predicted_duration.toFixed(2)}s`}</dd></div><div><dt>Tốc độ</dt><dd>{cue.speed.toFixed(2)}x</dd></div></dl>
      <div className="provenance"><span>Văn bản đọc</span><strong className={`override-${cue.override_state}`}>{dirty ? "Chưa lưu" : overrideLabels[cue.override_state]}</strong></div>
      <div className="warning-stack"><span>Nhịp timeline</span>{overflow > 0 ? <div className="cue-warning"><AlertTriangle size={14} /><div><strong>Có thể lấn cue sau</strong><p>Dự kiến dư {overflow.toFixed(2)} giây. Đây là cảnh báo căn timeline, không phải đánh giá bản dịch.</p></div></div> : <p className="no-warning"><Check size={14} />Có đủ khoảng đọc dự kiến</p>}</div>
      <div className="provenance"><span>Bản nghe thử</span><strong>{cue.preview_state === "current" ? "Đã có" : cue.preview_state === "stale" ? "Cần tạo lại" : "Chưa có"}</strong></div>
    </div>
  );
}
