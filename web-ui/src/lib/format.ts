import type { ExecutionStatus, QualityStatus } from "../api/types";

export const executionLabels: Record<ExecutionStatus, string> = {
  queued: "Đang chờ",
  running: "Đang chạy",
  cancelling: "Đang dừng an toàn",
  cancelled: "Đã hủy",
  failed: "Thất bại",
  interrupted: "Bị gián đoạn",
  completed: "Hoàn tất",
};

export const qualityLabels: Record<QualityStatus, string> = {
  needs_review: "Cần rà",
  degraded: "Cần chú ý",
  approved: "Đã duyệt",
};

export function formatTime(seconds: number): string {
  const totalMs = Math.max(0, Math.round(seconds * 1000));
  const hours = Math.floor(totalMs / 3_600_000);
  const minutes = Math.floor((totalMs % 3_600_000) / 60_000);
  const secs = Math.floor((totalMs % 60_000) / 1000);
  const millis = totalMs % 1000;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}.${String(millis).padStart(3, "0")}`;
}

export function formatBytes(value?: number | null): string {
  if (value == null) return "—";
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 ** 2).toFixed(1)} MB`;
}

export function formatDate(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("vi-VN", { dateStyle: "short", timeStyle: "short" }).format(date);
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Có lỗi không xác định";
}
