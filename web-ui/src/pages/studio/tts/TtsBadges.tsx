import type { TtsExecutionStatus, TtsWorkspace } from "../../../api/types";
import { ttsExecutionLabels } from "./labels";

export function TtsExecutionBadge({ status }: { status: TtsExecutionStatus }) {
  return <span className={`status-badge status-${status}`}><i aria-hidden="true" />{ttsExecutionLabels[status]}</span>;
}

export function TtsQualityBadge({ status }: { status: TtsWorkspace["quality_status"] }) {
  const labels = { needs_review: "Cần rà audio", degraded: "Audio có cảnh báo", approved: "Audio đã duyệt" };
  return <span className={`quality-badge quality-${status}`}>{labels[status]}</span>;
}

export function TtsOutputState({ state }: { state: "current" | "stale" | "missing" }) {
  return <span className={`tts-output-state tts-output-${state}`}><i />{{ current: "Mới nhất", stale: "Cần tạo lại", missing: "Không còn trên đĩa" }[state]}</span>;
}
