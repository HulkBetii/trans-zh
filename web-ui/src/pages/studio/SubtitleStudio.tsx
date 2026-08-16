import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import { AlertTriangle, Check, ChevronRight, Clock3, Eye, Film, Info, LoaderCircle, RefreshCcw, Save, Search, Sparkles, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api, apiUrl } from "../../api/client";
import { queryKeys } from "../../api/queries";
import type { JobDetail, OverrideChange, SubtitleCue, SubtitleWorkspace, TargetLanguage } from "../../api/types";
import { EmptyState } from "../../components/EmptyState";
import { useDialogFocus } from "../../hooks/useDialogFocus";
import { useMediaQuery } from "../../hooks/useMediaQuery";
import { useUnsavedChanges } from "../../hooks/useUnsavedChanges";
import { errorMessage, formatTime } from "../../lib/format";
import { useSearchParams } from "react-router-dom";

type CueFilter = "all" | "warnings" | "edited" | "review";

export function SubtitleStudio({ job }: { job: JobDetail }) {
  const languages = job.request.targets;
  const [language, setLanguage] = useState<TargetLanguage>(languages[0] ?? "vi");
  const query = useQuery({
    queryKey: queryKeys.subtitles(job.job_id, language),
    queryFn: () => api.subtitles(job.job_id, language),
  });

  if (query.isLoading) return <div className="editor-loading"><LoaderCircle className="spin" size={20} />Đang mở phụ đề…</div>;
  if (query.isError || !query.data) return <EmptyState icon={AlertTriangle} title="Chưa mở được phụ đề" detail={errorMessage(query.error)} action={<button className="secondary-button" onClick={() => void query.refetch()}>Thử lại</button>} />;
  return <SubtitleWorkspaceView key={language} job={job} data={query.data} language={language} languages={languages} onLanguage={setLanguage} />;
}

function SubtitleWorkspaceView({ job, data, language, languages, onLanguage }: { job: JobDetail; data: SubtitleWorkspace; language: TargetLanguage; languages: TargetLanguage[]; onLanguage: (language: TargetLanguage) => void }) {
  const queryClient = useQueryClient();
  const [searchParams] = useSearchParams();
  const requestedFilter = searchParams.get("filter");
  const [filter, setFilter] = useState<CueFilter>(() => isCueFilter(requestedFilter) ? requestedFilter : "all");
  const [search, setSearch] = useState("");
  const [workspace, setWorkspace] = useState(data);
  const [selectedId, setSelectedId] = useState<number | null>(workspace.cues[0]?.segment_id ?? null);
  const [changes, setChanges] = useState<Record<number, string | null>>({});
  const [revision, setRevision] = useState(data.revision);
  const [followPlayback, setFollowPlayback] = useState(true);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [followUpError, setFollowUpError] = useState<string | null>(null);
  const dataRevisionRef = useRef(data.revision);
  const playerRef = useRef<HTMLVideoElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const dirtyCount = Object.keys(changes).length;
  const dirty = dirtyCount > 0;
  const editorLocked = Boolean(workspace.editor_locked) || (job.execution_status === "running" && ["s2", "s4"].includes(job.active_run?.stage ?? ""));
  const closeInspector = useCallback(() => setInspectorOpen(false), []);
  const mobileInspector = useMediaQuery("(max-width: 820px)");
  const inspectorRef = useDialogFocus<HTMLElement>(mobileInspector && inspectorOpen, closeInspector);
  useUnsavedChanges(dirty);

  useEffect(() => {
    // Chỉ `revision` mới nói lên nội dung đã bị ghi đè ở nơi khác. So định danh
    // object thì `editor_locked` lật lên vì một run TTS, hay `output_stale` lật
    // sau khi render, cũng bị đọc thành xung đột — rồi banner khuyên reload,
    // đúng thao tác xóa sạch bản nháp đang có.
    if (data.revision !== dataRevisionRef.current) {
      dataRevisionRef.current = data.revision;
      if (dirty) {
        setConflict(true);
        return;
      }
      setWorkspace(data);
      setRevision(data.revision);
      setConflict(false);
      return;
    }
    // Cùng revision: hấp thụ các trường phụ (khóa editor, cờ output cũ) mà không
    // đụng tới `changes`.
    setWorkspace(data);
  }, [data, dirty]);

  const filtered = useMemo(() => workspace.cues.filter((cue) => {
    const query = search.trim().toLocaleLowerCase();
    const effective = effectiveText(cue, changes);
    const matchesSearch = !query || cue.source_text.toLocaleLowerCase().includes(query) || effective.toLocaleLowerCase().includes(query) || String(cue.segment_id).includes(query);
    const matchesFilter = filter === "all"
      || (filter === "warnings" && cue.warnings.length > 0)
      || (filter === "edited" && (cue.override_state !== "none" || Object.hasOwn(changes, cue.segment_id)))
      || (filter === "review" && (!cue.reviewed || cue.override_state === "base_changed" || cue.override_state === "stale"));
    return matchesSearch && matchesFilter;
  }), [changes, filter, search, workspace.cues]);
  const selected = filtered.find((cue) => cue.segment_id === selectedId) ?? filtered[0] ?? null;
  const virtualizer = useVirtualizer({ count: filtered.length, getScrollElement: () => listRef.current, estimateSize: () => 92, overscan: 8 });
  const hasInvalid = Object.values(changes).some((value) => value !== null && !value.trim());
  const hasSavedOverrides = workspace.cues.some((cue) => cue.override_state !== "none");

  useEffect(() => {
    if (selectedId !== null && filtered.some((cue) => cue.segment_id === selectedId)) return;
    setSelectedId(filtered[0]?.segment_id ?? null);
  }, [filtered, selectedId]);

  const saveMutation = useMutation({
    mutationFn: async (render: boolean) => {
      const payload: OverrideChange[] = Object.entries(changes).map(([segmentId, text]) => ({ segment_id: Number(segmentId), text }));
      const saved = await api.saveOverrides(job.job_id, language, revision, payload);
      if (!render) return { saved, followUpError: null };
      try {
        await api.render(job.job_id);
        return { saved, followUpError: null };
      } catch (error) {
        return { saved, followUpError: errorMessage(error) };
      }
    },
    onSuccess: ({ saved, followUpError: renderError }) => {
      dataRevisionRef.current = saved.revision;
      setChanges({});
      setRevision(saved.revision);
      setWorkspace(saved);
      setConflict(false);
      setFollowUpError(renderError);
      queryClient.setQueryData(queryKeys.subtitles(job.job_id, language), saved);
      void queryClient.invalidateQueries({ queryKey: queryKeys.job(job.job_id) });
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
    onError: (error) => {
      if (error instanceof ApiError && error.code === "revision_conflict") setConflict(true);
    },
  });

  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        if (dirty && !hasInvalid && !editorLocked && !saveMutation.isPending) saveMutation.mutate(false);
      }
    };
    window.addEventListener("keydown", shortcut);
    return () => window.removeEventListener("keydown", shortcut);
  }, [dirty, editorLocked, hasInvalid, saveMutation]);

  const selectCue = (cue: SubtitleCue, seek = true) => {
    setSelectedId(cue.segment_id);
    if (seek && playerRef.current) playerRef.current.currentTime = cue.start;
  };
  const updateText = (cue: SubtitleCue, value: string) => setChanges((current) => ({ ...current, [cue.segment_id]: value }));
  const resetCue = (cue: SubtitleCue) => setChanges((current) => {
    const next = { ...current };
    if (cue.override_state === "none") delete next[cue.segment_id];
    else next[cue.segment_id] = null;
    return next;
  });
  const resetAll = () => {
    if (!window.confirm("Đặt lại toàn bộ chỉnh sửa thủ công của ngôn ngữ này về bản máy?")) return;
    const next: Record<number, null> = {};
    workspace.cues.forEach((cue) => { if (cue.override_state !== "none") next[cue.segment_id] = null; });
    setChanges(next);
  };
  const syncPlayback = () => {
    if (!followPlayback || !playerRef.current) return;
    const time = playerRef.current.currentTime;
    const cue = workspace.cues.find((item) => time >= item.start && time < item.end);
    if (cue && cue.segment_id !== selectedId) {
      setSelectedId(cue.segment_id);
      const visibleIndex = filtered.findIndex((item) => item.segment_id === cue.segment_id);
      if (visibleIndex >= 0) virtualizer.scrollToIndex(visibleIndex, { align: "auto" });
    }
  };

  const changeLanguage = (nextLanguage: TargetLanguage) => {
    if (nextLanguage === language) return;
    if (dirty && !window.confirm("Bạn có thay đổi phụ đề chưa lưu. Đổi ngôn ngữ và bỏ các thay đổi này?")) return;
    onLanguage(nextLanguage);
  };

  if (workspace.cues.length === 0) return <EmptyState icon={Film} title="Chưa có cue" detail="Stage Segment và Translate cần hoàn tất trước khi có thể chỉnh phụ đề." />;

  return (
    <div className="subtitle-studio">
      {editorLocked && <div className="inline-alert"><LoaderCircle className="spin" size={16} /><span>Pipeline đang có thể ghi lại dữ liệu phụ đề. Editor tạm khóa.</span></div>}
      {conflict && <div className="conflict-banner"><AlertTriangle size={19} /><div><strong>Phụ đề đã thay đổi ở tab khác</strong><p>Các chỉnh sửa local vẫn còn trên màn hình nhưng chưa được lưu.</p></div><button className="secondary-button" onClick={() => window.location.reload()}><RefreshCcw size={16} />Tải bản mới</button></div>}
      {workspace.output_stale && <div className="stale-output-note"><AlertTriangle size={15} />Output hiện tại cũ hơn nội dung đang chỉnh. Render lại sau khi lưu.</div>}

      <div className="subtitle-toolbar">
        <div className="language-switch" aria-label="Ngôn ngữ phụ đề">{languages.map((item) => <button className={language === item ? "active" : ""} aria-pressed={language === item} key={item} onClick={() => changeLanguage(item)}>{item.toUpperCase()}</button>)}</div>
        <div className="cue-filters" aria-label="Lọc cue">{(["all", "warnings", "edited", "review"] as const).map((item) => <button className={filter === item ? "active" : ""} aria-pressed={filter === item} key={item} onClick={() => setFilter(item)}>{{ all: "Tất cả", warnings: "Cảnh báo", edited: "Đã sửa", review: "Cần rà" }[item]}</button>)}</div>
        <label className="subtitle-search"><Search size={15} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Tìm nội dung hoặc ID" /></label>
      </div>

      <div className="subtitle-workspace-grid">
        <section className="cue-pane" aria-label="Danh sách cue">
          <div className="pane-title"><span>{filtered.length} cue</span><small>{workspace.cues.length} tổng</small></div>
          <div className="cue-scroll" ref={listRef}>
            <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
              {virtualizer.getVirtualItems().map((virtualRow) => {
                const cue = filtered[virtualRow.index];
                return <div key={cue.segment_id} ref={virtualizer.measureElement} data-index={virtualRow.index} style={{ position: "absolute", width: "100%", transform: `translateY(${virtualRow.start}px)` }}><CueRow cue={cue} selected={selected?.segment_id === cue.segment_id} effective={effectiveText(cue, changes)} dirty={Object.hasOwn(changes, cue.segment_id)} onSelect={() => selectCue(cue)} /></div>;
              })}
            </div>
          </div>
        </section>

        <section className="cue-editor-pane">
          <div className="media-frame">
            <video ref={playerRef} src={apiUrl.media(job.job_id)} controls preload="metadata" onTimeUpdate={syncPlayback} />
            <button className={`follow-toggle ${followPlayback ? "active" : ""}`} onClick={() => setFollowPlayback((current) => !current)}><Eye size={14} />{followPlayback ? "Đang theo cue" : "Theo cue khi phát"}</button>
          </div>
          {selected && (
            <div className="translation-editor">
              <div className="editor-cue-head"><code>#{String(selected.segment_id).padStart(4, "0")}</code><span><Clock3 size={14} />{formatTime(selected.start)} <ChevronRight size={12} /> {formatTime(selected.end)}</span><button className="text-button inspector-trigger" aria-expanded={inspectorOpen} aria-controls="cue-inspector" onClick={() => setInspectorOpen(true)}><Info size={14} />Chi tiết</button></div>
              <div className="source-copy"><span>Nguyên văn</span><p lang="zh">{selected.source_text}</p></div>
              <div className="machine-copy"><span>Bản máy · chỉ đọc</span><p>{selected.model_text}</p></div>
              <label className="effective-editor"><span>Bản hiệu lực</span><textarea aria-label="Bản hiệu lực" value={effectiveText(selected, changes)} disabled={editorLocked} onChange={(event) => updateText(selected, event.target.value)} rows={5} /></label>
              {Object.hasOwn(changes, selected.segment_id) && changes[selected.segment_id] !== null && !changes[selected.segment_id]?.trim() && <p className="field-error"><AlertTriangle size={14} />Bản hiệu lực không được để trống.</p>}
              <div className="editor-foot"><span>Chỉnh sửa chỉ thay nội dung, không đổi timecode.</span><button className="text-button" disabled={editorLocked || (selected.override_state === "none" && !Object.hasOwn(changes, selected.segment_id))} onClick={() => resetCue(selected)}><RefreshCcw size={14} />Về bản máy</button></div>
            </div>
          )}
        </section>

        {mobileInspector && inspectorOpen && <button className="inspector-scrim" aria-label="Đóng bảng chi tiết" onClick={closeInspector} />}
        <aside ref={inspectorRef} id="cue-inspector" className={`cue-inspector ${selected ? "visible" : ""} ${inspectorOpen ? "mobile-open" : ""}`} role={mobileInspector ? "dialog" : undefined} aria-modal={mobileInspector ? true : undefined} aria-label="Chi tiết cue">
          {selected && <Inspector cue={selected} dirty={Object.hasOwn(changes, selected.segment_id)} onClose={closeInspector} />}
        </aside>
      </div>

      {saveMutation.isError && !conflict && <div className="inline-alert error"><AlertTriangle size={16} /><span>{errorMessage(saveMutation.error)}</span></div>}
      {followUpError && <div className="inline-alert error"><AlertTriangle size={16} /><span>Chỉnh sửa đã lưu, nhưng chưa thể bắt đầu render: {followUpError}</span></div>}
      {/* Thanh này phải hiện cả khi sạch: "Reset toàn bộ" nằm trong đó, mà lúc
          cần nó nhất — có override đã lưu, chưa sửa gì thêm — thì lại không dirty. */}
      {(dirty || hasSavedOverrides) && <div className={`sticky-savebar subtitle-savebar ${dirty ? "dirty" : "clean"}`}>
        <div><strong>{dirty ? `${dirtyCount} thay đổi chưa lưu` : "Mọi chỉnh sửa đã được lưu"}</strong>{hasSavedOverrides && <button className="text-button" onClick={resetAll} disabled={editorLocked}>Reset toàn bộ</button>}</div>
        {dirty && <div><button className="secondary-button" disabled={hasInvalid || editorLocked || saveMutation.isPending || conflict} onClick={() => saveMutation.mutate(false)}>{saveMutation.isPending ? <LoaderCircle className="spin" size={16} /> : <Save size={16} />}Lưu</button><button className="primary-button" disabled={hasInvalid || editorLocked || saveMutation.isPending || conflict || !job.allowed_actions.includes("render")} onClick={() => saveMutation.mutate(true)}><Sparkles size={16} />Lưu & render lại</button></div>}
      </div>}
    </div>
  );
}

function effectiveText(cue: SubtitleCue, changes: Record<number, string | null>): string {
  if (!Object.hasOwn(changes, cue.segment_id)) return cue.effective_text;
  return changes[cue.segment_id] ?? cue.model_text;
}

function CueRow({ cue, selected, effective, dirty, onSelect }: { cue: SubtitleCue; selected: boolean; effective: string; dirty: boolean; onSelect: () => void }) {
  return (
    <button className={`cue-row ${selected ? "selected" : ""}`} aria-pressed={selected} onClick={onSelect}>
      <span className="cue-row-head"><code>{String(cue.segment_id).padStart(4, "0")}</code><time>{formatTime(cue.start).slice(3, -1)}</time>{cue.warnings.length > 0 && <AlertTriangle size={13} />}{(dirty || cue.override_state !== "none") && <i title="Đã chỉnh sửa" />}</span>
      <span className="cue-source" lang="zh">{cue.source_text}</span>
      <span className="cue-target">{effective}</span>
    </button>
  );
}

function isCueFilter(value: string | null): value is CueFilter {
  return value === "all" || value === "warnings" || value === "edited" || value === "review";
}

function Inspector({ cue, dirty, onClose }: { cue: SubtitleCue; dirty: boolean; onClose: () => void }) {
  const stateLabel = dirty ? "Chưa lưu" : { none: "Bản máy", manual: "Đã chỉnh tay", base_changed: "Bản máy đã đổi", stale: "Override lỗi thời" }[cue.override_state];
  return (
    <div className="inspector-inner">
      <div className="pane-title"><span>Inspector</span><button className="icon-button inspector-close" onClick={onClose} aria-label="Đóng inspector"><X size={17} /></button></div>
      <dl className="cue-metrics">
        <div><dt>CPS</dt><dd>{cue.cps?.toFixed(1) ?? "—"}</dd></div>
        <div><dt>Số dòng</dt><dd>{cue.line_count ?? "—"}</dd></div>
        <div><dt>Thời lượng</dt><dd>{(cue.end - cue.start).toFixed(2)}s</dd></div>
      </dl>
      <div className="provenance"><span>Trạng thái nội dung</span><strong className={`override-${cue.override_state}`}>{stateLabel}</strong></div>
      <div className="warning-stack"><span>Cảnh báo hiển thị</span>{cue.warnings.length === 0 ? <p className="no-warning"><Check size={14} />Không có cảnh báo</p> : cue.warnings.map((warning, index) => <div className="cue-warning" key={`${warning.kind}-${index}`}><AlertTriangle size={14} /><div><strong>{{ cps_over: "Nhịp đọc nhanh", line_over: "Dòng dài", too_many_lines: "Nhiều dòng" }[warning.kind] ?? warning.kind}</strong><p>{warning.detail}</p></div></div>)}</div>
      <p className="inspector-note">CPS là tín hiệu về nhịp hiển thị, không phải đánh giá chất lượng bản dịch.</p>
    </div>
  );
}
