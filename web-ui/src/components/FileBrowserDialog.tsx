import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, FileVideo, Folder, HardDrive, LoaderCircle, X } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { FileEntry, FileRoot } from "../api/types";
import { useDialogFocus } from "../hooks/useDialogFocus";
import { errorMessage, formatBytes } from "../lib/format";

interface FileBrowserDialogProps {
  open: boolean;
  onClose: () => void;
  onSelect: (path: string) => void;
}

export function FileBrowserDialog({ open, onClose, onSelect }: FileBrowserDialogProps) {
  const [path, setPath] = useState<string | null>(null);
  const dialogRef = useDialogFocus<HTMLElement>(open, onClose);
  const roots = useQuery({ queryKey: ["file-roots"], queryFn: api.fileRoots, enabled: open });
  const listing = useQuery({ queryKey: ["files", path], queryFn: () => api.files(path!), enabled: open && Boolean(path) });

  useEffect(() => {
    if (!open) setPath(null);
  }, [open]);

  if (!open) return null;

  return (
    <div className="modal-layer" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section ref={dialogRef} className="file-browser" role="dialog" aria-modal="true" aria-labelledby="file-browser-title">
        <header>
          <div>
            <span className="eyebrow">Local filesystem</span>
            <h2 id="file-browser-title">Chọn media</h2>
          </div>
          <button className="icon-button" onClick={onClose} aria-label="Đóng"><X size={19} /></button>
        </header>
        {path && (
          <div className="browser-pathbar">
            <button className="icon-button" onClick={() => setPath(listing.data?.parent ?? null)} aria-label="Quay lại"><ChevronLeft size={19} /></button>
            <code title={path}>{path}</code>
          </div>
        )}
        <div className="browser-list">
          {!path && roots.isLoading && <BrowserMessage text="Đang tìm thư mục…" loading />}
          {!path && roots.isError && <BrowserMessage text={errorMessage(roots.error)} />}
          {!path && roots.data?.map((root) => <RootRow key={root.path} root={root} onOpen={setPath} />)}
          {path && listing.isLoading && <BrowserMessage text="Đang đọc thư mục…" loading />}
          {path && listing.isError && <BrowserMessage text={errorMessage(listing.error)} />}
          {path && listing.data?.entries.length === 0 && <BrowserMessage text="Không có media được hỗ trợ trong thư mục này." />}
          {path && listing.data?.entries.map((entry) => <EntryRow key={entry.path} entry={entry} onOpen={setPath} onSelect={onSelect} />)}
        </div>
        <footer><span>Chỉ hiển thị thư mục và định dạng media được hỗ trợ.</span></footer>
      </section>
    </div>
  );
}

function RootRow({ root, onOpen }: { root: FileRoot; onOpen: (path: string) => void }) {
  return <button className="browser-row" onClick={() => onOpen(root.path)}><HardDrive size={18} /><span><strong>{root.label}</strong><code>{root.path}</code></span></button>;
}

function EntryRow({ entry, onOpen, onSelect }: { entry: FileEntry; onOpen: (path: string) => void; onSelect: (path: string) => void }) {
  const directory = entry.kind === "directory";
  return (
    <button className="browser-row" onClick={() => directory ? onOpen(entry.path) : onSelect(entry.path)}>
      {directory ? <Folder size={18} /> : <FileVideo size={18} />}
      <span><strong>{entry.name}</strong><code>{directory ? entry.path : formatBytes(entry.size_bytes)}</code></span>
    </button>
  );
}

function BrowserMessage({ text, loading = false }: { text: string; loading?: boolean }) {
  return <div className="browser-message">{loading && <LoaderCircle className="spin" size={18} />}<span>{text}</span></div>;
}
