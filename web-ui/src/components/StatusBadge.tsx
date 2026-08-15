import type { ExecutionStatus, QualityStatus } from "../api/types";
import { executionLabels, qualityLabels } from "../lib/format";

export function ExecutionBadge({ status }: { status: ExecutionStatus | "not_started" }) {
  const label = status === "not_started" ? "Chưa bắt đầu" : executionLabels[status];
  return <span className={`status-badge status-${status}`}><i aria-hidden="true" />{label}</span>;
}

export function QualityBadge({ status }: { status: QualityStatus }) {
  return <span className={`quality-badge quality-${status}`}>{qualityLabels[status]}</span>;
}
