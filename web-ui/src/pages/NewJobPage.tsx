import { AlertCircle, ArrowRight, Check, FileSearch, Film, Globe2, ShieldCheck } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useCreateJob, useJobs, useMeta } from "../api/queries";
import type { OutputFormat, TargetLanguage } from "../api/types";
import { FileBrowserDialog } from "../components/FileBrowserDialog";
import { errorMessage } from "../lib/format";

export function NewJobPage() {
  const navigate = useNavigate();
  const meta = useMeta();
  const recentJobs = useJobs({}, { poll: false });
  const createJob = useCreateJob();
  const [sourceKind, setSourceKind] = useState<"local" | "url">("local");
  const [source, setSource] = useState("");
  const [targets, setTargets] = useState<TargetLanguage[]>(["vi"]);
  const [formats, setFormats] = useState<OutputFormat[]>(["srt"]);
  const [bilingual, setBilingual] = useState(false);
  const [browserOpen, setBrowserOpen] = useState(false);
  const availableTargets = useMemo(
    () => (meta.data?.targets ?? []) as TargetLanguage[],
    [meta.data?.targets],
  );
  const availableFormats = useMemo(
    () => (meta.data?.formats ?? []) as OutputFormat[],
    [meta.data?.formats],
  );

  useEffect(() => {
    if (!meta.data) return;
    setTargets((current) => {
      const supported = current.filter((item) => availableTargets.includes(item));
      if (supported.length > 0) return supported;
      return availableTargets.includes("vi") ? ["vi"] : availableTargets.slice(0, 1);
    });
    setFormats((current) => {
      const supported = current.filter((item) => availableFormats.includes(item));
      if (supported.length > 0) return supported;
      return availableFormats.includes("srt") ? ["srt"] : availableFormats.slice(0, 1);
    });
  }, [availableFormats, availableTargets, meta.data]);

  const blocked = meta.data?.readiness.filter((item) => item.status === "blocked") ?? [];
  const recentSources = useMemo(() => {
    const values = recentJobs.data?.items
      .filter((job) => job.source?.kind === sourceKind)
      .map((job) => job.source!.value) ?? [];
    return [...new Set(values)].slice(0, 4);
  }, [recentJobs.data, sourceKind]);
  const sourceValid = sourceKind === "local"
    ? /^[a-zA-Z]:[\\/]/.test(source.trim()) || source.trim().startsWith("/")
    : /^https?:\/\//i.test(source.trim());
  const canSubmit = meta.isSuccess && sourceValid && targets.length > 0 && formats.length > 0 && blocked.length === 0 && !createJob.isPending;

  const toggleTarget = (target: TargetLanguage) => setTargets((current) => current.includes(target) ? current.filter((item) => item !== target) : [...current, target]);
  const toggleFormat = (format: OutputFormat) => setFormats((current) => current.includes(format) ? current.filter((item) => item !== format) : [...current, format]);

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!canSubmit) return;
    createJob.mutate({
      source: { kind: sourceKind, value: source.trim() },
      targets,
      formats,
      bilingual,
    }, {
      onSuccess: (result) => navigate(`/jobs/${encodeURIComponent(result.job_id)}/pipeline`),
    });
  };

  return (
    <div className="page new-job-page">
      <header className="page-intro">
        <span className="section-number">00 / Bắt đầu</span>
        <h1>Biến một nguồn tiếng Trung<br />thành bản phụ đề có thể tin cậy.</h1>
        <p>Media không được tải lên. Studio đọc trực tiếp nguồn local hoặc URL, giữ nguyên timestamp ASR và lưu từng stage để có thể tiếp tục.</p>
      </header>

      <div className="new-job-grid">
        <form className="production-form" onSubmit={submit}>
          <section className="form-section">
            <div className="form-section-label"><span>01</span><div><h2>Nguồn media</h2><p>Chọn file trên máy hoặc dán URL công khai.</p></div></div>
            <fieldset className="source-switch"><legend className="sr-only">Loại nguồn</legend>
              <label className={sourceKind === "local" ? "active" : ""}><input type="radio" name="source-kind" value="local" checked={sourceKind === "local"} onChange={() => { setSourceKind("local"); setSource(""); }} /><Film size={17} />File local</label>
              <label className={sourceKind === "url" ? "active" : ""}><input type="radio" name="source-kind" value="url" checked={sourceKind === "url"} onChange={() => { setSourceKind("url"); setSource(""); }} /><Globe2 size={17} />URL</label>
            </fieldset>
            <label className="field-label" htmlFor="source-input">{sourceKind === "local" ? "Đường dẫn tuyệt đối" : "URL video"}</label>
            <div className="source-input-row">
              <input id="source-input" className="text-input mono-input" value={source} onChange={(event) => setSource(event.target.value)} placeholder={sourceKind === "local" ? "D:\\Media\\episode-01.mp4" : "https://www.bilibili.com/video/..."} spellCheck={false} />
              {sourceKind === "local" && <button className="secondary-button browse-button" type="button" onClick={() => setBrowserOpen(true)}><FileSearch size={17} />Duyệt</button>}
            </div>
            {source && !sourceValid && <p className="field-error"><AlertCircle size={14} />{sourceKind === "local" ? "Cần một đường dẫn tuyệt đối." : "URL phải bắt đầu bằng http:// hoặc https://."}</p>}
            {recentSources.length > 0 && <div className="recent-sources"><span>Gần đây</span>{recentSources.map((value) => <button type="button" key={value} onClick={() => setSource(value)} title={value}>{value}</button>)}</div>}
          </section>

          <section className="form-section option-section">
            <div className="form-section-label"><span>02</span><div><h2>Bản dịch</h2><p>Chọn ngôn ngữ và định dạng cần xuất.</p></div></div>
            <fieldset><legend>Ngôn ngữ đích</legend><div className="choice-grid">
              {availableTargets.map((target) => <Choice key={target} checked={targets.includes(target)} onChange={() => toggleTarget(target)} label={targetLabel(target)} detail={`Bản dịch ${target.toUpperCase()}`} />)}
            </div></fieldset>
            <fieldset><legend>Định dạng output</legend><div className="choice-grid">
              {availableFormats.map((format) => <Choice key={format} checked={formats.includes(format)} onChange={() => toggleFormat(format)} label={format.toUpperCase()} detail={formatDetail(format)} />)}
            </div></fieldset>
            <label className="bilingual-toggle"><input type="checkbox" checked={bilingual} onChange={(event) => setBilingual(event.target.checked)} /><span><strong>Phụ đề song ngữ</strong><small>Đặt tiếng Trung cùng bản dịch trong một cue.</small></span></label>
          </section>

          {createJob.isError && <div className="form-alert error"><AlertCircle size={18} /><div><strong>Không thể tạo công việc</strong><p>{errorMessage(createJob.error)}</p></div></div>}
          <button className="primary-button submit-job" type="submit" disabled={!canSubmit}>
            <span>{createJob.isPending ? "Đang đưa vào hàng chờ…" : "Tạo và bắt đầu"}</span><ArrowRight size={18} />
          </button>
        </form>

        <aside className="readiness-panel">
          <div className="readiness-title"><ShieldCheck size={20} /><div><span className="eyebrow">System readiness</span><h2>Sẵn sàng vận hành</h2></div></div>
          {meta.isLoading && <p className="muted">Đang kiểm tra môi trường…</p>}
          {meta.isError && <div className="readiness-item blocked"><AlertCircle size={16} /><div><strong>Không đọc được trạng thái</strong><span>{errorMessage(meta.error)}</span><button className="text-button" type="button" disabled={meta.isFetching} onClick={() => void meta.refetch()}>Thử lại</button></div></div>}
          {meta.data?.readiness.map((item) => (
            <div className={`readiness-item ${item.status}`} key={item.id}>
              {item.status === "ready" ? <Check size={16} /> : <AlertCircle size={16} />}
              <div><strong>{item.label}</strong>{item.detail && <span>{item.detail}</span>}</div>
            </div>
          ))}
          <div className="readiness-note"><span>Quy tắc nền</span><p>Timestamp luôn đi từ ASR đến output. Mọi chỉnh sửa trong Studio chỉ thay nội dung cue.</p></div>
        </aside>
      </div>

      <FileBrowserDialog open={browserOpen} onClose={() => setBrowserOpen(false)} onSelect={(value) => { setSource(value); setBrowserOpen(false); }} />
    </div>
  );
}

function Choice({ checked, onChange, label, detail }: { checked: boolean; onChange: () => void; label: string; detail: string }) {
  return <label className={`choice-card ${checked ? "selected" : ""}`}><input type="checkbox" checked={checked} onChange={onChange} /><span className="choice-check">{checked && <Check size={13} />}</span><span><strong>{label}</strong><small>{detail}</small></span></label>;
}

function targetLabel(target: string): string {
  return { vi: "Tiếng Việt", en: "English" }[target] ?? target.toUpperCase();
}

function formatDetail(format: string): string {
  return { srt: "Tương thích rộng", ass: "Giữ styling" }[format] ?? `Định dạng ${format.toUpperCase()}`;
}
