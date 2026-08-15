import { Download, FileQuestion, FileText, LoaderCircle, RefreshCw, Volume2 } from "lucide-react";
import { apiUrl } from "../../api/client";
import type { Artifact, JobDetail } from "../../api/types";
import { EmptyState } from "../../components/EmptyState";
import { formatBytes, formatDate } from "../../lib/format";

export function OutputsPanel({ job, onRender, renderPending }: { job: JobDetail; onRender: () => void; renderPending: boolean }) {
  if (job.artifacts.length === 0) {
    return <EmptyState icon={FileQuestion} title="Chưa có output" detail="Pipeline cần hoàn tất stage Render trước khi file xuất hiện tại đây." action={job.allowed_actions.includes("render") ? <button className="primary-button" onClick={onRender} disabled={renderPending}>{renderPending ? <LoaderCircle className="spin" size={16} /> : <RefreshCw size={16} />}{renderPending ? "Đang render…" : "Render output"}</button> : undefined} />;
  }
  return (
    <section className="panel-sheet outputs-sheet">
      <div className="section-heading outputs-heading"><div><span className="eyebrow">Deliverables</span><h2>Output</h2><p>File được quản lý theo job để không ghi đè nguồn trùng tên.</p></div>{job.allowed_actions.includes("render") && <button className="secondary-button" onClick={onRender} disabled={renderPending}>{renderPending ? <LoaderCircle className="spin" size={16} /> : <RefreshCw size={16} />}Render lại</button>}</div>
      <div className="artifact-table-wrap"><table className="artifact-table" aria-label="Danh sách output">
        <thead><tr><th scope="col">File</th><th scope="col">Loại</th><th scope="col">Kích thước</th><th scope="col">Cập nhật</th><th scope="col">Trạng thái</th><th scope="col"><span className="sr-only">Tải file</span></th></tr></thead>
        <tbody>{job.artifacts.map((artifact) => <ArtifactRow key={artifact.artifact_id} artifact={artifact} jobId={job.job_id} />)}</tbody>
      </table></div>
    </section>
  );
}

function ArtifactRow({ artifact, jobId }: { artifact: Artifact; jobId: string }) {
  const Icon = artifact.kind === "audio" ? Volume2 : FileText;
  const href = artifact.download_url ?? apiUrl.artifact(jobId, artifact.artifact_id);
  const stateLabel = { current: "Mới nhất", stale: "Cần render lại", missing: "Không còn trên đĩa" }[artifact.state];
  return (
    <tr className={`artifact-row artifact-${artifact.state}`}>
      <th scope="row"><span className="artifact-name"><Icon size={17} /><span><strong>{artifact.name}</strong><code>{artifact.language?.toUpperCase() ?? "—"}</code></span></span></th>
      <td data-label="Loại">{artifact.format?.toUpperCase() ?? artifact.kind}</td>
      <td data-label="Kích thước">{formatBytes(artifact.size_bytes)}</td>
      <td data-label="Cập nhật">{formatDate(artifact.created_at)}</td>
      <td data-label="Trạng thái" className="artifact-state"><i />{stateLabel}</td>
      <td className="artifact-download">{artifact.state !== "missing" && <a className="icon-button" href={href} download aria-label={`Tải ${artifact.name}`}><Download size={17} /></a>}</td>
    </tr>
  );
}
