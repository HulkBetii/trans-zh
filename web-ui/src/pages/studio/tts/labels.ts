import type { TtsCue, TtsVoice, TtsWorkspace } from "../../../api/types";

export type DraftChanges = Record<number, string | null>;

export const ttsExecutionLabels: Record<TtsWorkspace["execution_status"], string> = {
  not_started: "Chưa chạy",
  queued: "Đang chờ",
  running: "Đang chạy",
  cancelling: "Đang dừng an toàn",
  cancelled: "Đã hủy",
  failed: "Thất bại",
  interrupted: "Bị gián đoạn",
  completed: "Hoàn tất",
};

export const overrideLabels = {
  none: "Tự động",
  manual: "Đã chỉnh tay",
  base_changed: "Nền đã đổi",
  stale: "Override lỗi thời",
} as const;

export function effectiveSpoken(cue: TtsCue, changes: DraftChanges): string {
  return Object.hasOwn(changes, cue.segment_id) ? changes[cue.segment_id] ?? cue.default_spoken_text : cue.effective_spoken_text;
}

export function voiceMeta(voice: TtsVoice): string {
  return [voice.locale, voice.gender, voice.age].filter(Boolean).join(" · ") || "Tiếng Việt";
}

export function ttsRenderHint(workspace: TtsWorkspace, state: { dirty: boolean; hasInvalid: boolean; editorLocked: boolean; voiceDirty: boolean }): string {
  if (!workspace.provider_ready) return "AI33 / Vbee chưa sẵn sàng.";
  if (!workspace.voice_id || state.voiceDirty) return "Chọn và lưu một giọng trước khi tạo MP3.";
  if (!workspace.calibration || workspace.calibration.voice_id !== workspace.voice_id || workspace.calibration.sample_count < 8) return "Cần hiệu chuẩn đủ 8 mẫu cho giọng đang chọn.";
  if (!workspace.subtitle_approved) return "Cần duyệt phụ đề trước khi tạo MP3 toàn timeline.";
  if (workspace.attention_reasons.includes("stale_spoken_overrides")) return "Có override văn bản đọc lỗi thời cần xử lý.";
  if (workspace.attention_reasons.includes("spoken_overrides_base_changed")) return "Có override cần rà lại vì nền phụ đề đã đổi.";
  if (state.hasInvalid) return "Văn bản đọc không được để trống.";
  if (state.dirty) return "Lưu các thay đổi TTS trước khi tạo MP3.";
  if (state.editorLocked) return "Đang có tác vụ TTS sử dụng artifact này.";
  return workspace.allowed_actions.includes("render")
    ? "Sẵn sàng tạo MP3 theo timeline ASR."
    : "Backend chưa cho phép tạo MP3 ở trạng thái hiện tại.";
}
