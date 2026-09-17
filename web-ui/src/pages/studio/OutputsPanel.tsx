import {
  Check,
  Copy,
  Download,
  Eye,
  FileQuestion,
  FileText,
  LoaderCircle,
  RefreshCw,
  Video,
  Volume2,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";
import { apiUrl } from "../../api/client";
import type { Artifact, JobDetail } from "../../api/types";
import { EmptyState } from "../../components/EmptyState";
import { formatBytes, formatDate } from "../../lib/format";

export function OutputsPanel({
  job,
  onRender,
  renderPending,
}: {
  job: JobDetail;
  onRender: () => void;
  renderPending: boolean;
}) {
  const [kitModal, setKitModal] = useState<{ open: boolean; url: string; filename: string }>({
    open: false,
    url: "",
    filename: "",
  });

  if (job.artifacts.length === 0) {
    return (
      <EmptyState
        icon={FileQuestion}
        title="Chưa có output"
        detail="Pipeline cần hoàn tất stage Render trước khi file xuất hiện tại đây."
        action={
          job.allowed_actions.includes("render") ? (
            <button className="primary-button" onClick={onRender} disabled={renderPending}>
              {renderPending ? <LoaderCircle className="spin" size={16} /> : <RefreshCw size={16} />}
              {renderPending ? "Đang render…" : "Render output"}
            </button>
          ) : undefined
        }
      />
    );
  }

  const handleOpenKit = (url: string, filename: string) => {
    setKitModal({ open: true, url, filename });
  };

  return (
    <section className="panel-sheet outputs-sheet">
      <div className="section-heading outputs-heading">
        <div>
          <span className="eyebrow">Deliverables</span>
          <h2>Output</h2>
          <p>File được quản lý theo job để không ghi đè nguồn trùng tên.</p>
        </div>
        {job.allowed_actions.includes("render") && (
          <button className="secondary-button" onClick={onRender} disabled={renderPending}>
            {renderPending ? <LoaderCircle className="spin" size={16} /> : <RefreshCw size={16} />}
            Render lại
          </button>
        )}
      </div>
      <div className="artifact-table-wrap">
        <table className="artifact-table" aria-label="Danh sách output">
          <thead>
            <tr>
              <th scope="col">File</th>
              <th scope="col">Loại</th>
              <th scope="col">Kích thước</th>
              <th scope="col">Cập nhật</th>
              <th scope="col">Trạng thái</th>
              <th scope="col">
                <span className="sr-only">Tải file</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {job.artifacts.map((artifact) => (
              <ArtifactRow
                key={artifact.artifact_id}
                artifact={artifact}
                jobId={job.job_id}
                onOpenKit={handleOpenKit}
              />
            ))}
          </tbody>
        </table>
      </div>

      <YouTubeKitDialog
        open={kitModal.open}
        downloadUrl={kitModal.url}
        filename={kitModal.filename}
        onClose={() => setKitModal({ open: false, url: "", filename: "" })}
      />
    </section>
  );
}

function ArtifactRow({
  artifact,
  jobId,
  onOpenKit,
}: {
  artifact: Artifact;
  jobId: string;
  onOpenKit?: (url: string, name: string) => void;
}) {
  const isYoutubeKit = artifact.name.includes("youtube_upload_kit");
  const Icon = isYoutubeKit ? Video : artifact.kind === "audio" ? Volume2 : FileText;
  const href = artifact.download_url ?? apiUrl.artifact(jobId, artifact.artifact_id);
  const stateLabel = {
    current: "Mới nhất",
    stale: "Cần render lại",
    missing: "Không còn trên đĩa",
  }[artifact.state];

  return (
    <tr className={`artifact-row artifact-${artifact.state}`}>
      <th scope="row">
        <span className="artifact-name">
          <Icon size={17} style={isYoutubeKit ? { color: "#e11d48" } : undefined} />
          <span>
            <strong>{artifact.name}</strong>
            {isYoutubeKit ? (
              <span
                style={{
                  background: "rgba(225,29,72,0.12)",
                  color: "#e11d48",
                  fontWeight: 600,
                  fontSize: "10px",
                  padding: "2px 6px",
                  borderRadius: "3px",
                  marginLeft: "6px",
                  letterSpacing: "0.04em",
                }}
              >
                {artifact.language?.toLowerCase() === "en" || artifact.name.includes(".en.")
                  ? "YOUTUBE KIT (EN)"
                  : "YOUTUBE KIT (VI)"}
              </span>
            ) : (
              <code>{artifact.language?.toUpperCase() ?? "—"}</code>
            )}
          </span>
        </span>
      </th>
      <td data-label="Loại">{isYoutubeKit ? "YOUTUBE KIT" : artifact.format?.toUpperCase() ?? artifact.kind}</td>
      <td data-label="Kích thước">{formatBytes(artifact.size_bytes)}</td>
      <td data-label="Cập nhật">{formatDate(artifact.created_at)}</td>
      <td data-label="Trạng thái" className="artifact-state">
        <i />
        {stateLabel}
      </td>
      <td className="artifact-download">
        {artifact.state !== "missing" && (
          <div style={{ display: "inline-flex", gap: "6px", alignItems: "center" }}>
            {isYoutubeKit && onOpenKit && (
              <button
                className="secondary-button compact"
                style={{ height: "28px", fontSize: "11px", padding: "0 8px" }}
                onClick={() => onOpenKit(href, artifact.name)}
                title="Xem nhanh nội dung Kit"
              >
                <Eye size={13} /> Xem nhanh
              </button>
            )}
            <a
              className="icon-button"
              href={href}
              download
              aria-label={`Tải ${artifact.name}`}
              title="Tải file"
            >
              <Download size={17} />
            </a>
          </div>
        )}
      </td>
    </tr>
  );
}

interface KitSection {
  id: string;
  title: string;
  content: string;
}

function parseKitSections(raw: string): KitSection[] {
  const sections: KitSection[] = [];
  const lines = raw.split("\n");
  let currentId = "intro";
  let currentTitle = "Giới thiệu bộ Kit";
  let currentLines: string[] = [];

  for (const line of lines) {
    if (line.includes("[1. TIÊU ĐỀ")) {
      if (currentLines.length > 0) sections.push({ id: currentId, title: currentTitle, content: currentLines.join("\n").trim() });
      currentId = "titles";
      currentTitle = "1. Tiêu đề Video (Gợi ý)";
      currentLines = [];
    } else if (line.includes("[2. NỘI DUNG MÔ TẢ")) {
      if (currentLines.length > 0) sections.push({ id: currentId, title: currentTitle, content: currentLines.join("\n").trim() });
      currentId = "desc";
      currentTitle = "2. Mô tả & Dòng thời gian (Timestamps)";
      currentLines = [];
    } else if (line.includes("[3. BỘ THẺ TAGS")) {
      if (currentLines.length > 0) sections.push({ id: currentId, title: currentTitle, content: currentLines.join("\n").trim() });
      currentId = "tags";
      currentTitle = "3. Thẻ Tags YouTube";
      currentLines = [];
    } else if (line.includes("[4. PROMPT TẠO ẢNH THUMBNAIL")) {
      if (currentLines.length > 0) sections.push({ id: currentId, title: currentTitle, content: currentLines.join("\n").trim() });
      currentId = "thumbs";
      currentTitle = "4. Prompt Tạo Thumbnail (@image GPT)";
      currentLines = [];
    } else {
      currentLines.push(line);
    }
  }
  if (currentLines.length > 0) {
    sections.push({ id: currentId, title: currentTitle, content: currentLines.join("\n").trim() });
  }
  return sections;
}

function YouTubeKitDialog({
  open,
  downloadUrl,
  filename,
  onClose,
}: {
  open: boolean;
  downloadUrl: string;
  filename: string;
  onClose: () => void;
}) {
  const [content, setContent] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    fetch(downloadUrl)
      .then((res) => res.text())
      .then((text) => {
        setContent(text);
        setLoading(false);
      })
      .catch(() => {
        setContent("Không thể đọc nội dung file YouTube Upload Kit.");
        setLoading(false);
      });
  }, [open, downloadUrl]);

  const copySection = (id: string, text: string) => {
    void navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  if (!open) return null;

  const sections = content ? parseKitSections(content) : [];

  return (
    <div
      className="modal-layer"
      role="presentation"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <section
        className="file-browser youtube-kit-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="youtube-kit-title"
        style={{ width: "min(840px, 96%)", maxHeight: "90vh" }}
      >
        <header>
          <div>
            <span className="eyebrow" style={{ color: "#e11d48" }}>YouTube Upload Kit</span>
            <h2 id="youtube-kit-title">{filename}</h2>
          </div>
          <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
            <a className="secondary-button compact" href={downloadUrl} download>
              <Download size={14} /> Tải .txt
            </a>
            <button className="icon-button" onClick={onClose} aria-label="Đóng">
              <X size={18} />
            </button>
          </div>
        </header>

        <div
          className="browser-list youtube-kit-body"
          style={{
            padding: "16px",
            overflowY: "auto",
            display: "flex",
            flexDirection: "column",
            gap: "16px",
          }}
        >
          {loading && (
            <div className="browser-message">
              <LoaderCircle className="spin" size={20} /> Đang tải nội dung YouTube Kit…
            </div>
          )}
          {!loading && content && (
            <>
              <div style={{ display: "flex", justifyContent: "flex-end" }}>
                <button
                  className="secondary-button compact"
                  onClick={() => copySection("all", content)}
                >
                  {copiedId === "all" ? <Check size={14} /> : <Copy size={14} />}
                  {copiedId === "all" ? "Đã sao chép toàn bộ!" : "Sao chép toàn bộ Kit"}
                </button>
              </div>
              {sections.map((section) => (
                <div
                  key={section.id}
                  className="panel-sheet"
                  style={{
                    padding: "14px",
                    border: "1px solid var(--line)",
                    borderRadius: "4px",
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      marginBottom: "8px",
                      paddingBottom: "6px",
                      borderBottom: "1px solid var(--line)",
                    }}
                  >
                    <strong style={{ fontSize: "13px" }}>{section.title}</strong>
                    <button
                      className="secondary-button compact"
                      onClick={() => copySection(section.id, section.content)}
                    >
                      {copiedId === section.id ? <Check size={13} /> : <Copy size={13} />}
                      {copiedId === section.id ? "Đã chép" : "Sao chép"}
                    </button>
                  </div>
                  <pre
                    style={{
                      fontSize: "11px",
                      lineHeight: "1.5",
                      whiteSpace: "pre-wrap",
                      wordBreak: "break-word",
                      fontFamily: "var(--font-mono)",
                      margin: 0,
                      color: "var(--ink-soft)",
                    }}
                  >
                    {section.content}
                  </pre>
                </div>
              ))}
            </>
          )}
        </div>
      </section>
    </div>
  );
}
