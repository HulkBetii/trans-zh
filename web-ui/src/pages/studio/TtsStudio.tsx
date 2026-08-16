import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import {
  AlertTriangle,
  AudioLines,
  Check,
  CheckCircle2,
  ChevronRight,
  CircleDollarSign,
  Clock3,
  Info,
  LoaderCircle,
  Play,
  RefreshCcw,
  Search,
  Settings2,
  Square,
  Volume2,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, api, apiUrl } from "../../api/client";
import { queryKeys, useTts, useTtsRunEvents, useTtsVoices } from "../../api/queries";
import type {
  JobDetail,
  RunRecord,
  TtsCue,
  TtsExecutionStatus,
  TtsVoice,
  TtsWorkspace,
  VoiceOwnership,
} from "../../api/types";
import { EmptyState } from "../../components/EmptyState";
import { useDebouncedValue } from "../../hooks/useDebouncedValue";
import { useDialogFocus } from "../../hooks/useDialogFocus";
import { useRevisionedDraft } from "../../hooks/useRevisionedDraft";
import { useMediaQuery } from "../../hooks/useMediaQuery";
import { useUnsavedChanges } from "../../hooks/useUnsavedChanges";
import { errorMessage, formatBytes, formatDate, formatTime } from "../../lib/format";

type CueFilter = "all" | "edited" | "tight" | "preview";
type DraftChanges = Record<number, string | null>;
type SaveRequest = { scope: "voice" | "all" } | { scope: "cue"; segmentId: number };

const terminalRunStatuses = new Set(["completed", "cancelled", "failed", "interrupted"]);

class PartialTtsSaveError extends Error {
  constructor(
    readonly originalError: unknown,
    readonly saved: TtsWorkspace,
    readonly clearVoice: boolean,
    readonly clearSpokenIds: number[],
  ) {
    super(errorMessage(originalError));
    this.name = "PartialTtsSaveError";
  }
}

const ttsExecutionLabels: Record<TtsExecutionStatus, string> = {
  not_started: "Chưa chạy",
  queued: "Đang chờ",
  running: "Đang chạy",
  cancelling: "Đang dừng an toàn",
  cancelled: "Đã hủy",
  failed: "Thất bại",
  interrupted: "Bị gián đoạn",
  completed: "Hoàn tất",
};

const overrideLabels = {
  none: "Tự động",
  manual: "Đã chỉnh tay",
  base_changed: "Nền đã đổi",
  stale: "Override lỗi thời",
} as const;

export function TtsStudio({ job }: { job: JobDetail }) {
  const query = useTts(job.job_id);

  if (query.isLoading) {
    return <div className="editor-loading"><LoaderCircle className="spin" size={20} />Đang mở không gian lồng tiếng…</div>;
  }
  if (query.isError || !query.data) {
    return (
      <EmptyState
        icon={AlertTriangle}
        title="Chưa mở được không gian lồng tiếng"
        detail={errorMessage(query.error)}
        action={<button className="secondary-button" onClick={() => void query.refetch()}>Thử lại</button>}
      />
    );
  }
  return <TtsWorkspaceView key={job.job_id} job={job} data={query.data} />;
}

function TtsWorkspaceView({ job, data }: { job: JobDetail; data: TtsWorkspace }) {
  const queryClient = useQueryClient();
  const [workspace, setWorkspace] = useState(data);
  const [revision, setRevision] = useState(data.revision);
  const [draftVoiceId, setDraftVoiceId] = useState(data.voice_id ?? "");
  const [draftVoice, setDraftVoice] = useState<TtsVoice | null>(data.selected_voice ?? null);
  const [changes, setChanges] = useState<DraftChanges>({});
  const [selectedId, setSelectedId] = useState<number | null>(data.cues[0]?.segment_id ?? null);
  const [filter, setFilter] = useState<CueFilter>("all");
  const [search, setSearch] = useState("");
  const [followPlayback, setFollowPlayback] = useState(true);
  const [voiceDialogOpen, setVoiceDialogOpen] = useState(false);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [lastQueuedRun, setLastQueuedRun] = useState<RunRecord | null>(null);
  const playerRef = useRef<HTMLVideoElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const dirtyCount = Object.keys(changes).length;
  const spokenDirty = dirtyCount > 0;
  const voiceDirty = draftVoiceId !== (workspace.voice_id ?? "");
  const dirty = spokenDirty || voiceDirty;
  const editorLocked = workspace.editor_locked;
  const inspectorClose = useCallback(() => setInspectorOpen(false), []);
  const mobileInspector = useMediaQuery("(max-width: 820px)");
  const inspectorRef = useDialogFocus<HTMLElement>(mobileInspector && inspectorOpen, inspectorClose);
  useUnsavedChanges(dirty);

  const adopt = useCallback((next: TtsWorkspace) => {
    setWorkspace(next);
    setRevision(next.revision);
    setDraftVoiceId(next.voice_id ?? "");
    setDraftVoice((current) => next.selected_voice ?? (current?.voice_id === next.voice_id ? current : null));
    setChanges({});
  }, []);
  // Cùng revision: hấp thụ trạng thái chạy và khóa editor mà không đụng bản nháp.
  const refresh = useCallback((next: TtsWorkspace) => setWorkspace(next), []);
  const { conflict, discardDraft, markSaved, markConflict } = useRevisionedDraft({ data, dirty, onAdopt: adopt, onRefresh: refresh });

  useEffect(() => {
    if (!lastQueuedRun) return;
    if (data.active_run?.run_id === lastQueuedRun.run_id) {
      setLastQueuedRun(null);
      return;
    }
    if (data.latest_run?.run_id === lastQueuedRun.run_id && terminalRunStatuses.has(data.latest_run.status)) {
      setLastQueuedRun(null);
    }
  }, [data.active_run, data.latest_run, lastQueuedRun]);

  useEffect(() => {
    if (editorLocked) setVoiceDialogOpen(false);
  }, [editorLocked]);

  const filtered = useMemo(() => {
    const query = search.trim().toLocaleLowerCase();
    return workspace.cues.filter((cue) => {
      const effective = effectiveSpoken(cue, changes);
      const matchesSearch = !query
        || cue.subtitle_text.toLocaleLowerCase().includes(query)
        || effective.toLocaleLowerCase().includes(query)
        || String(cue.segment_id).includes(query);
      const matchesFilter = filter === "all"
        || (filter === "edited" && (cue.override_state !== "none" || Object.hasOwn(changes, cue.segment_id)))
        || (filter === "tight" && (cue.overflow_seconds ?? 0) > 0)
        || (filter === "preview" && cue.preview_state === "current");
      return matchesSearch && matchesFilter;
    });
  }, [changes, filter, search, workspace.cues]);
  const selected = filtered.find((cue) => cue.segment_id === selectedId) ?? filtered[0] ?? null;
  const virtualizer = useVirtualizer({
    count: filtered.length,
    getScrollElement: () => listRef.current,
    estimateSize: () => 82,
    overscan: 8,
  });
  const hasInvalid = Object.values(changes).some((value) => value !== null && !value.trim());
  const activeRun = workspace.active_run ?? lastQueuedRun;
  const connectionStatus = useTtsRunEvents(job.job_id, activeRun?.run_id);
  const selectedInvalid = Boolean(selected && Object.hasOwn(changes, selected.segment_id) && changes[selected.segment_id] !== null && !changes[selected.segment_id]?.trim());
  const canPreview = Boolean(selected && workspace.allowed_actions.includes("preview") && workspace.provider_ready && !editorLocked && !voiceDirty && !selectedInvalid);
  const calibrationReady = Boolean(workspace.calibration && workspace.calibration.voice_id === workspace.voice_id && workspace.calibration.sample_count >= 8);
  const canRender = workspace.provider_ready && workspace.allowed_actions.includes("render") && calibrationReady && !dirty && !editorLocked && workspace.subtitle_approved;
  const selectedDraft = selected ? Object.hasOwn(changes, selected.segment_id) : false;
  const terminalRun = workspace.latest_run && ["failed", "interrupted", "cancelled"].includes(workspace.latest_run.status)
    ? workspace.latest_run
    : null;
  const runPanel = activeRun ?? terminalRun;
  const displayedVoice = voiceDirty
    ? draftVoice
    : workspace.selected_voice ?? (draftVoice?.voice_id === workspace.voice_id ? draftVoice : null);

  useEffect(() => {
    if (selectedId !== null && filtered.some((cue) => cue.segment_id === selectedId)) return;
    setSelectedId(filtered[0]?.segment_id ?? null);
  }, [filtered, selectedId]);

  const applySaved = useCallback((saved: TtsWorkspace, options: { clearVoice: boolean; clearSpokenIds: number[] }) => {
    markSaved(saved);
    setWorkspace(saved);
    setRevision(saved.revision);
    if (options.clearVoice) {
      setDraftVoiceId(saved.voice_id ?? "");
      setDraftVoice((current) => saved.selected_voice ?? (current?.voice_id === saved.voice_id ? current : null));
    }
    if (options.clearSpokenIds.length > 0) {
      setChanges((current) => {
        const next = { ...current };
        options.clearSpokenIds.forEach((segmentId) => delete next[segmentId]);
        return next;
      });
    }
    setActionError(null);
    queryClient.setQueryData(queryKeys.tts(job.job_id), saved);
  }, [job.job_id, markSaved, queryClient]);

  const saveDrafts = useMutation({
    mutationFn: async (request: SaveRequest) => {
      let saved: TtsWorkspace | null = null;
      let currentRevision = revision;
      const clearVoice = (request.scope === "voice" || request.scope === "all") && voiceDirty;
      const spokenEntries = request.scope === "all"
        ? Object.entries(changes)
        : request.scope === "cue" && Object.hasOwn(changes, request.segmentId)
          ? [[String(request.segmentId), changes[request.segmentId]] as [string, string | null]]
          : [];
      const clearSpokenIds = spokenEntries.map(([segmentId]) => Number(segmentId));
      let voiceSaved = false;
      try {
        if (clearVoice) {
          if (!draftVoiceId.trim()) throw new Error("Hãy chọn một giọng trước khi lưu.");
          saved = await api.saveTtsSettings(job.job_id, { revision: currentRevision, voice_id: draftVoiceId.trim() });
          currentRevision = saved.revision;
          voiceSaved = true;
        }
        if (spokenEntries.length > 0) {
          saved = await api.saveSpokenOverrides(job.job_id, {
            revision: currentRevision,
            changes: spokenEntries.map(([segmentId, text]) => ({ segment_id: Number(segmentId), text })),
          });
        }
      } catch (error) {
        if (saved) throw new PartialTtsSaveError(error, saved, voiceSaved, []);
        throw error;
      }
      if (!saved) throw new Error("Không có thay đổi cần lưu.");
      return { saved, clearVoice, clearSpokenIds };
    },
    onSuccess: ({ saved, clearVoice, clearSpokenIds }) => applySaved(saved, { clearVoice, clearSpokenIds }),
    onError: (error) => {
      if (error instanceof PartialTtsSaveError) {
        applySaved(error.saved, { clearVoice: error.clearVoice, clearSpokenIds: error.clearSpokenIds });
        setActionError(error.message);
      } else {
        setActionError(errorMessage(error));
      }
      const originalError = error instanceof PartialTtsSaveError ? error.originalError : error;
      if (originalError instanceof ApiError && originalError.code === "revision_conflict") markConflict();
    },
  });

  const preview = useMutation({
    mutationFn: (segmentId: number) => api.previewTts(job.job_id, { segment_id: segmentId }),
    onSuccess: (run) => {
      setLastQueuedRun(run);
      setActionError(null);
      void queryClient.invalidateQueries({ queryKey: queryKeys.tts(job.job_id) });
    },
    onError: (error) => {
      setActionError(errorMessage(error));
      if (error instanceof ApiError && error.code === "revision_conflict") markConflict();
    },
  });

  const runAction = useMutation({
    mutationFn: async (kind: "calibrate" | "render") => {
      if (kind === "calibrate") return api.calibrateTts(job.job_id);
      return api.renderTts(job.job_id);
    },
    onSuccess: (run) => {
      setLastQueuedRun(run);
      setActionError(null);
      void queryClient.invalidateQueries({ queryKey: queryKeys.tts(job.job_id) });
    },
    onError: (error) => setActionError(errorMessage(error)),
  });

  const cancelRun = useMutation({
    mutationFn: () => {
      if (!activeRun?.run_id) throw new Error("Không tìm thấy run TTS đang hoạt động.");
      return api.cancelRun(activeRun.run_id);
    },
    onSuccess: () => {
      setLastQueuedRun(null);
      void queryClient.invalidateQueries({ queryKey: queryKeys.tts(job.job_id) });
    },
    onError: (error) => setActionError(errorMessage(error)),
  });

  const approval = useMutation({
    mutationFn: (kind: "approve" | "unapprove") => kind === "approve"
      ? api.approveTts(job.job_id)
      : api.unapproveTts(job.job_id),
    onSuccess: (saved) => applySaved(saved, { clearVoice: true, clearSpokenIds: Object.keys(changes).map(Number) }),
    onError: (error) => setActionError(errorMessage(error)),
  });

  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        if (dirty && !hasInvalid && !editorLocked && !saveDrafts.isPending && !conflict) {
          saveDrafts.mutate({ scope: "all" });
        }
      }
    };
    window.addEventListener("keydown", shortcut);
    return () => window.removeEventListener("keydown", shortcut);
  }, [conflict, dirty, editorLocked, hasInvalid, saveDrafts]);

  const selectCue = (cue: TtsCue, seek = true) => {
    setSelectedId(cue.segment_id);
    if (seek && playerRef.current) playerRef.current.currentTime = cue.start;
  };
  const updateSpoken = (cue: TtsCue, value: string) => {
    setChanges((current) => ({ ...current, [cue.segment_id]: value }));
  };
  const resetCue = (cue: TtsCue) => {
    setChanges((current) => {
      const next = { ...current };
      if (cue.override_state === "none") delete next[cue.segment_id];
      else next[cue.segment_id] = null;
      return next;
    });
  };
  const resetAll = () => {
    if (!window.confirm("Đặt lại toàn bộ văn bản đọc thủ công về bản tự động?")) return;
    const next: DraftChanges = {};
    workspace.cues.forEach((cue) => {
      if (cue.override_state !== "none") next[cue.segment_id] = null;
    });
    setChanges(next);
  };
  const syncPlayback = () => {
    if (!followPlayback || !playerRef.current) return;
    const time = playerRef.current.currentTime;
    const cue = workspace.cues.find((item) => time >= item.start && time < item.end);
    if (cue && cue.segment_id !== selectedId) {
      const visibleIndex = filtered.findIndex((item) => item.segment_id === cue.segment_id);
      if (visibleIndex >= 0) {
        setSelectedId(cue.segment_id);
        virtualizer.scrollToIndex(visibleIndex, { align: "auto" });
      }
    }
  };

  const askPreview = async () => {
    if (!selected || !canPreview || preview.isPending || saveDrafts.isPending) return;
    if (selected.preview_state === "current" && !selectedDraft && selected.preview_url) return;
    const cost = "Tác vụ này có thể tiêu tốn 1 lượt TTS của nhà cung cấp. Tiếp tục?";
    if (!window.confirm(cost)) return;
    if (selectedDraft) {
      try {
        await saveDrafts.mutateAsync({ scope: "cue", segmentId: selected.segment_id });
      } catch {
        return;
      }
    }
    preview.mutate(selected.segment_id);
  };
  const askCalibrate = () => {
    if (!workspace.allowed_actions.includes("calibrate") || voiceDirty || runAction.isPending) return;
    if (!window.confirm("Hiệu chuẩn dùng 8 mẫu giọng và có thể tiêu tốn 8 lượt TTS. Tiếp tục?")) return;
    runAction.mutate("calibrate");
  };
  const askRender = () => {
    if (!canRender || runAction.isPending) return;
    const uncached = workspace.uncached_cues;
    const detail = uncached > 0
      ? `Cần tạo ${uncached} cue mới, có thể phát sinh ${uncached} lượt TTS. Tiếp tục?`
      : "Các cue đã có cache; chỉ ráp lại timeline MP3. Tiếp tục?";
    if (!window.confirm(detail)) return;
    runAction.mutate("render");
  };
  const runBusy = saveDrafts.isPending || preview.isPending || runAction.isPending || cancelRun.isPending || approval.isPending;
  const outputStale = workspace.output_stale || dirty;
  const renderHint = ttsRenderHint(workspace, { dirty, hasInvalid, editorLocked, voiceDirty });

  return (
    <div className="tts-studio">
      {conflict && (
        <div className="conflict-banner" role="alert">
          <AlertTriangle size={19} />
          <div><strong>TTS đã thay đổi ở tab khác</strong><p>Chỉnh sửa của bạn vẫn còn trên màn hình và chưa bị ghi đè. Lưu tạm khóa để không đè lên bản mới.</p></div>
          <button className="secondary-button danger" onClick={discardDraft}><RefreshCcw size={16} />Bỏ chỉnh sửa & lấy bản mới</button>
        </div>
      )}
      {actionError && <div className="inline-alert error" role="alert"><AlertTriangle size={16} /><span>{actionError}</span></div>}
      {connectionStatus !== "live" && <div className={`connection-banner connection-${connectionStatus}`} role="status"><LoaderCircle className={connectionStatus === "offline" ? "" : "spin"} size={15} /><span>{{ connecting: "Đang kết nối tiến độ audio…", reconnecting: "Đang kết nối lại và đồng bộ run audio…", offline: "Đang offline. Trạng thái audio sẽ được tải lại khi có mạng.", live: "" }[connectionStatus]}</span></div>}

      <section className="tts-setup panel-sheet" aria-labelledby="tts-setup-title">
        <div className="tts-setup-heading">
          <div><span className="eyebrow">Voice & calibration</span><h2 id="tts-setup-title">Chuẩn bị giọng đọc</h2><p>Tiếng Việt · một giọng cho toàn bộ job · MP3 theo timeline ASR.</p></div>
          <div className="tts-status-line"><TtsExecutionBadge status={(activeRun?.status as TtsExecutionStatus | undefined) ?? workspace.execution_status} />{workspace.quality_status !== "approved" && <TtsQualityBadge status={workspace.quality_status} />}{workspace.quality_status === "approved" && <span className="tts-approved"><CheckCircle2 size={14} />Audio đã duyệt</span>}</div>
        </div>
        <div className="tts-setup-grid">
          <div className="tts-voice-current">
            <span className="tts-label">Giọng đang chọn</span>
            {(displayedVoice || workspace.voice_id || draftVoiceId) ? (
              <div className="tts-selected-voice">
                <div><strong>{displayedVoice?.name ?? (voiceDirty ? draftVoiceId : workspace.voice_id)}</strong><small>{displayedVoice ? voiceMeta(displayedVoice) : "Vbee · mã giọng đã lưu"}</small></div>
                <button className="secondary-button compact" disabled={runBusy || editorLocked} onClick={() => setVoiceDialogOpen(true)}><Settings2 size={15} />Đổi giọng</button>
              </div>
            ) : (
              <div className="tts-no-voice"><strong>Chưa chọn giọng</strong><button className="secondary-button compact" disabled={runBusy || editorLocked} onClick={() => setVoiceDialogOpen(true)}><Settings2 size={15} />Mở thư viện giọng</button></div>
            )}
            {voiceDirty && <div className="tts-dirty-row"><span>Giọng mới chưa lưu; nghe thử và hiệu chuẩn đang khóa.</span><button className="primary-button compact" disabled={runBusy || editorLocked || !draftVoiceId || conflict} onClick={() => saveDrafts.mutate({ scope: "voice" })}>{saveDrafts.isPending ? <LoaderCircle className="spin" size={14} /> : <Check size={14} />}Lưu giọng</button></div>}
          </div>
          <div className="tts-calibration">
            <span className="tts-label">Hiệu chuẩn dùng chung</span>
            {calibrationReady && !voiceDirty ? (
              <div className="tts-calibration-copy"><strong className="tts-ready"><Check size={15} />Đã đo 8/8 mẫu</strong><small>{workspace.calibration?.sec_per_syllable.toFixed(3)}s / âm tiết · {formatDate(workspace.calibration?.calibrated_at)}</small></div>
            ) : (
              <div className="tts-calibration-copy"><strong className="tts-warning"><AlertTriangle size={15} />{voiceDirty ? "Lưu giọng mới để kiểm tra hiệu chuẩn" : workspace.calibration?.voice_id === workspace.voice_id ? `Mới có ${workspace.calibration?.sample_count ?? 0}/8 mẫu` : "Chưa có số đo cho giọng này"}</strong><small>Hiệu chuẩn một lần, dùng lại cho các job sau.</small></div>
            )}
            {workspace.allowed_actions.includes("calibrate") && <button className="secondary-button compact" disabled={runBusy || !draftVoiceId || voiceDirty} title={voiceDirty ? "Lưu giọng mới trước khi hiệu chuẩn" : undefined} onClick={askCalibrate}><CircleDollarSign size={15} />Hiệu chuẩn 8 mẫu</button>}
          </div>
          <div className="tts-provider">
            <span className="tts-label">Nhà cung cấp</span>
            <strong className={workspace.provider_ready ? "tts-ready" : "tts-danger"}>{workspace.provider_ready ? "AI33 / Vbee sẵn sàng" : "AI33 / Vbee chưa sẵn sàng"}</strong>
            {workspace.provider_ready && <small>{displayedVoice?.tier ? `Gói ${displayedVoice.tier}` : "Không lộ API key trong trình duyệt"}</small>}
          </div>
        </div>
      </section>

      {runPanel && <TtsRunPanel run={runPanel} onCancel={() => cancelRun.mutate()} pending={cancelRun.isPending} />}
      {!workspace.subtitle_approved && (
        <div className="tts-gate" role="status"><Info size={17} /><div><strong>Phụ đề chưa được duyệt</strong><p>Nghe thử từng cue vẫn được. Tạo MP3 toàn timeline chỉ mở sau khi phụ đề đã duyệt.</p></div><Link className="secondary-button compact" to={`/jobs/${encodeURIComponent(job.job_id)}/subtitles`}>Rà phụ đề</Link></div>
      )}
      {workspace.output && <TtsOutput output={workspace.output} stale={outputStale} />}

      <div className="tts-toolbar">
        <div className="language-switch"><span className="language-label">VI</span></div>
        <div className="cue-filters" aria-label="Lọc cue TTS">
          {(["all", "edited", "tight", "preview"] as const).map((item) => <button key={item} className={filter === item ? "active" : ""} aria-pressed={filter === item} onClick={() => setFilter(item)}>{{ all: "Tất cả", edited: "Đã sửa", tight: "Chật giờ", preview: "Có nghe thử" }[item]}</button>)}
        </div>
        <label className="subtitle-search"><Search size={15} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Tìm cue hoặc văn bản đọc" /></label>
      </div>

      <div className="tts-workspace-grid">
        <section className="cue-pane tts-cue-pane" aria-label="Danh sách cue lồng tiếng">
          <div className="pane-title"><span>{filtered.length} cue</span><small>{workspace.total_cues} tổng</small></div>
          <div className="cue-scroll" ref={listRef}>
            {filtered.length === 0 && <div className="tts-filter-empty">Không có cue khớp bộ lọc hiện tại.</div>}
            <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
              {virtualizer.getVirtualItems().map((virtualRow) => {
                const cue = filtered[virtualRow.index];
                return <div key={cue.segment_id} ref={virtualizer.measureElement} data-index={virtualRow.index} style={{ position: "absolute", width: "100%", transform: `translateY(${virtualRow.start}px)` }}><TtsCueRow cue={cue} effective={effectiveSpoken(cue, changes)} selected={selected?.segment_id === cue.segment_id} dirty={Object.hasOwn(changes, cue.segment_id)} onSelect={() => selectCue(cue)} /></div>;
              })}
            </div>
          </div>
        </section>

        <section className="cue-editor-pane tts-editor-pane">
          <div className="media-frame">
            <video ref={playerRef} src={apiUrl.media(job.job_id)} muted controls preload="metadata" onTimeUpdate={syncPlayback} />
            <button className={`follow-toggle ${followPlayback ? "active" : ""}`} onClick={() => setFollowPlayback((current) => !current)}><Volume2 size={14} />{followPlayback ? "Đang theo cue" : "Theo cue khi phát"}</button>
          </div>
          {selected ? <TtsCueEditor cue={selected} effective={effectiveSpoken(selected, changes)} dirty={Object.hasOwn(changes, selected.segment_id)} locked={editorLocked} voiceDirty={voiceDirty} inspectorOpen={inspectorOpen} onChange={(value) => updateSpoken(selected, value)} onReset={() => resetCue(selected)} onOpenInspector={() => setInspectorOpen(true)} onPreview={() => void askPreview()} previewPending={preview.isPending || saveDrafts.isPending} canPreview={canPreview} /> : <div className="tts-editor-empty">Chọn bộ lọc khác để tiếp tục biên tập.</div>}
        </section>

        {mobileInspector && inspectorOpen && <button className="inspector-scrim" aria-label="Đóng bảng chi tiết TTS" onClick={inspectorClose} />}
        <aside ref={inspectorRef} className={`cue-inspector tts-inspector ${selected ? "visible" : ""} ${inspectorOpen ? "mobile-open" : ""}`} role={mobileInspector ? "dialog" : undefined} aria-modal={mobileInspector ? true : undefined} aria-label="Chi tiết timing TTS">
          {selected && <TtsInspector cue={selected} dirty={Object.hasOwn(changes, selected.segment_id)} onClose={inspectorClose} />}
        </aside>
      </div>

      <div className={`sticky-savebar subtitle-savebar tts-savebar ${dirty ? "dirty" : "clean"}`}>
        <div><strong>{dirty ? `${dirtyCount + (voiceDirty ? 1 : 0)} thay đổi TTS chưa lưu` : "Thiết lập TTS đã được lưu"}</strong><small className={canRender ? "tts-ready" : "tts-render-hint"}>{renderHint}</small>{workspace.cues.some((cue) => cue.override_state !== "none") && <button className="text-button" onClick={resetAll} disabled={editorLocked}>Reset toàn bộ</button>}</div>
        <div>{dirty && <button className="secondary-button" disabled={hasInvalid || editorLocked || saveDrafts.isPending || conflict} onClick={() => saveDrafts.mutate({ scope: "all" })}>{saveDrafts.isPending ? <LoaderCircle className="spin" size={16} /> : <Check size={16} />}Lưu</button>}<button className="primary-button" disabled={!canRender || runBusy || conflict} title={!canRender ? renderHint : undefined} onClick={askRender}><AudioLines size={16} />Tạo MP3 lồng tiếng</button>{workspace.allowed_actions.includes("approve") && <button className="secondary-button" disabled={runBusy || outputStale || dirty || conflict} onClick={() => approval.mutate("approve")}><CheckCircle2 size={16} />Duyệt audio</button>}{workspace.allowed_actions.includes("unapprove") && <button className="secondary-button" disabled={runBusy || dirty || conflict} onClick={() => approval.mutate("unapprove")}><Square size={15} />Bỏ duyệt audio</button>}</div>
      </div>

      <VoiceLibraryDialog open={voiceDialogOpen} selectedVoiceId={draftVoiceId} onClose={() => setVoiceDialogOpen(false)} onSelect={(voice) => { setDraftVoiceId(voice.voice_id); setDraftVoice(voice); setVoiceDialogOpen(false); }} />
    </div>
  );
}

function TtsRunPanel({ run, onCancel, pending }: { run: RunRecord; onCancel: () => void; pending: boolean }) {
  const terminal = ["completed", "cancelled", "failed", "interrupted"].includes(run.status);
  const kindLabel = run.kind === "tts_preview" ? "nghe thử" : run.kind === "tts_calibrate" ? "hiệu chuẩn giọng" : "MP3 timeline";
  const title = terminal ? `${ttsExecutionLabels[run.status as TtsExecutionStatus]} · ${kindLabel}` : `Đang tạo ${kindLabel}`;
  return (
    <section className="tts-run-panel" aria-live="polite">
      <div className="tts-run-head"><div><span className={terminal ? "run-state-dot" : "pulse-dot"} /><strong>{title}</strong><code>{run.run_id}</code></div>{!terminal && <button className="secondary-button compact danger" disabled={pending} onClick={onCancel}>{pending ? <LoaderCircle className="spin" size={14} /> : <Square size={14} />}Dừng an toàn</button>}</div>
      <p>{run.message || ttsExecutionLabels[run.status as TtsExecutionStatus]}</p>
      <div className="progress-track" role="progressbar" aria-label={`Tiến độ ${kindLabel}`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round((run.progress ?? 0) * 100)}><i style={{ width: `${Math.round((run.progress ?? 0) * 100)}%` }} /></div>
      <div className="tts-run-meta"><span>{Math.round((run.progress ?? 0) * 100)}%</span>{run.queue_position ? <span>Vị trí hàng đợi {run.queue_position}</span> : null}<span>{formatDate(run.started_at ?? run.created_at)}</span>{terminal && <span>Kết thúc {formatDate(run.finished_at)}</span>}{run.error && <span className="tts-danger">{run.error}</span>}</div>
    </section>
  );
}

function TtsOutput({ output, stale }: { output: NonNullable<TtsWorkspace["output"]>; stale: boolean }) {
  const href = output.download_url ?? undefined;
  const available = output.state !== "missing" && Boolean(href);
  const visibleState = output.state === "missing" ? "missing" : stale ? "stale" : output.state;
  return (
    <section className={`tts-output panel-sheet ${stale || output.state !== "current" ? "stale" : ""}`}>
      <div className="tts-output-heading"><div><span className="eyebrow">Timeline deliverable</span><h2>Audio lồng tiếng tiếng Việt</h2><p>{stale || output.state !== "current" ? "Output không còn khớp với văn bản hiện tại; cần tạo lại." : "MP3 đã ráp theo mốc thời gian ASR."}</p></div>{available && <a className="secondary-button compact" href={href} download><AudioLines size={15} />Tải MP3</a>}</div>
      {!available ? <p className="tts-danger">{output.state === "missing" ? "File không còn trên đĩa." : "Không có đường dẫn tải an toàn cho file này."}</p> : <audio className="tts-timeline-audio" src={href} controls preload="metadata" aria-label="Nghe audio lồng tiếng tiếng Việt" />}
      <div className="tts-output-meta"><span>{output.name}</span><span>{formatBytes(output.size_bytes)}</span><span>{formatDate(output.created_at)}</span><TtsOutputState state={visibleState} /></div>
    </section>
  );
}

function TtsCueRow({ cue, effective, selected, dirty, onSelect }: { cue: TtsCue; effective: string; selected: boolean; dirty: boolean; onSelect: () => void }) {
  const tight = (cue.overflow_seconds ?? 0) > 0;
  return (
    <button className={`cue-row tts-cue-row ${selected ? "selected" : ""}`} aria-pressed={selected} onClick={onSelect}>
      <span className="cue-row-head"><code>{String(cue.segment_id).padStart(4, "0")}</code><time>{formatTime(cue.start).slice(3, -1)}</time>{tight && <AlertTriangle size={13} />}{(dirty || cue.override_state !== "none") && <i title="Đã chỉnh sửa" />}{cue.preview_state === "current" && <Check size={12} aria-label="Có bản nghe thử" />}</span>
      <span className="cue-source">{cue.subtitle_text}</span>
      <span className="cue-target">{effective}</span>
    </button>
  );
}

function TtsCueEditor({ cue, effective, dirty, locked, voiceDirty, inspectorOpen, onChange, onReset, onOpenInspector, onPreview, previewPending, canPreview }: { cue: TtsCue; effective: string; dirty: boolean; locked: boolean; voiceDirty: boolean; inspectorOpen: boolean; onChange: (value: string) => void; onReset: () => void; onOpenInspector: () => void; onPreview: () => void; previewPending: boolean; canPreview: boolean }) {
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

function TtsInspector({ cue, dirty, onClose }: { cue: TtsCue; dirty: boolean; onClose: () => void }) {
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

function VoiceLibraryDialog({ open, selectedVoiceId, onClose, onSelect }: { open: boolean; selectedVoiceId: string; onClose: () => void; onSelect: (voice: TtsVoice) => void }) {
  const [search, setSearch] = useState("");
  const [ownership, setOwnership] = useState<VoiceOwnership>("all");
  const [page, setPage] = useState(1);
  const [voices, setVoices] = useState<TtsVoice[]>([]);
  const listRef = useRef<HTMLDivElement>(null);
  // Tìm kiếm do nhà cung cấp thực hiện, nên mỗi phím gõ là một request thật ra
  // ai33.pro. Chờ người dùng ngừng gõ rồi mới hỏi.
  const debouncedSearch = useDebouncedValue(search, 300);
  const filterKey = `${ownership} ${debouncedSearch}`;
  const [activeFilterKey, setActiveFilterKey] = useState(filterKey);

  // Đổi bộ lọc thì trang tích lũy phải về 1 ngay trong render này. Để cho effect
  // dọn một nhịp sau là kịp hỏi trang 3 của bộ lọc mới — một request vô nghĩa.
  let requestedPage = page;
  if (activeFilterKey !== filterKey) {
    setActiveFilterKey(filterKey);
    setPage(1);
    setVoices([]);
    requestedPage = 1;
  }

  const query = useTtsVoices({ search: debouncedSearch, page: requestedPage, page_size: 30, ownership }, open);
  // Ô tìm là việc đầu tiên người dùng muốn làm trong thư viện 1268 giọng.
  // Không chỉ định thì hook focus nút đóng ở header và autoFocus vô nghĩa.
  const dialogRef = useDialogFocus<HTMLElement>(open, onClose, {
    initialFocus: ".tts-voice-search input",
  });
  // Cả thư viện là 1268 giọng và mỗi dòng mang một thẻ <audio>; không ảo hóa thì
  // bấm "Tải thêm" đủ nhiều là trình duyệt ôm hơn một nghìn player cùng lúc.
  const virtualizer = useVirtualizer({
    count: voices.length,
    getScrollElement: () => listRef.current,
    estimateSize: () => 64,
    overscan: 6,
  });
  useEffect(() => {
    if (!open) {
      setSearch("");
      setOwnership("all");
      setPage(1);
      setVoices([]);
    }
  }, [open]);
  useEffect(() => {
    if (!query.data) return;
    setVoices((current) => deduplicateVoices(requestedPage === 1 ? query.data.items : [...current, ...query.data.items]));
  }, [requestedPage, query.data]);
  if (!open) return null;
  return (
    <div className="modal-layer" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section ref={dialogRef} className="tts-voice-dialog" role="dialog" aria-modal="true" aria-labelledby="tts-voice-title">
        <header><div><span className="eyebrow">Voice library · Vbee</span><h2 id="tts-voice-title">Chọn giọng tiếng Việt</h2></div><button className="icon-button" onClick={onClose} aria-label="Đóng thư viện giọng"><X size={19} /></button></header>
        <div className="tts-voice-filters">
          <label className="tts-voice-search"><Search size={15} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Tìm tên, vùng, giới tính…" /></label>
          <div className="tts-voice-ownership" role="group" aria-label="Nguồn giọng">
            {voiceOwnershipOptions.map((option) => (
              <button
                key={option.id}
                type="button"
                className={ownership === option.id ? "active" : ""}
                aria-pressed={ownership === option.id}
                onClick={() => setOwnership(option.id)}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>
        <div className="tts-voice-list" ref={listRef} aria-live="polite">
          {query.isLoading && voices.length === 0 && <div className="tts-dialog-message"><LoaderCircle className="spin" size={18} />Đang lấy thư viện giọng…</div>}
          {query.isError && <div className="tts-dialog-message tts-danger"><AlertTriangle size={18} />{errorMessage(query.error)}</div>}
          {!query.isLoading && !query.isError && voices.length === 0 && <div className="tts-dialog-message">Không tìm thấy giọng phù hợp.</div>}
          <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
            {virtualizer.getVirtualItems().map((virtualRow) => {
              const voice = voices[virtualRow.index];
              return <div key={voice.voice_id} ref={virtualizer.measureElement} data-index={virtualRow.index} style={{ position: "absolute", width: "100%", transform: `translateY(${virtualRow.start}px)` }}><VoiceRow voice={voice} selected={voice.voice_id === selectedVoiceId} onSelect={() => onSelect(voice)} /></div>;
            })}
          </div>
          {query.data?.has_more && <button className="secondary-button tts-load-more" disabled={query.isFetching} onClick={() => setPage((current) => current + 1)}>{query.isFetching ? <LoaderCircle className="spin" size={15} /> : null}Tải thêm giọng</button>}
        </div>
        <footer><span>{query.data?.credits == null ? "Chi phí chỉ phát sinh khi nghe thử, hiệu chuẩn hoặc tạo MP3." : `Còn khoảng ${query.data.credits} lượt TTS.`}</span></footer>
      </section>
    </div>
  );
}

// Giọng chính hãng chỉ có 25; cả thư viện là 1268. Mặc định "Tất cả" vì giọng dự
// án đang dùng (Duy Onyx) nằm ở nhóm cộng đồng.
const voiceOwnershipOptions: { id: VoiceOwnership; label: string }[] = [
  { id: "all", label: "Tất cả" },
  { id: "vbee", label: "Vbee" },
  { id: "community", label: "Cộng đồng" },
];

function deduplicateVoices(voices: TtsVoice[]): TtsVoice[] {
  return [...new Map(voices.map((voice) => [voice.voice_id, voice])).values()];
}

function VoiceRow({ voice, selected, onSelect }: { voice: TtsVoice; selected: boolean; onSelect: () => void }) {
  return (
    <div className={`tts-voice-row ${selected ? "selected" : ""}`}>
      <button className="tts-voice-select" aria-pressed={selected} onClick={onSelect}><span className="tts-voice-mark">{selected ? <Check size={14} /> : <AudioLines size={14} />}</span><span><strong>{voice.name}</strong><small>{voiceMeta(voice)}{voice.calibrated ? " · Đã hiệu chuẩn" : ""}</small>{voice.description && <em>{voice.description}</em>}</span></button>
      {voice.preview_url && <audio src={voice.preview_url} controls preload="none" aria-label={`Nghe mẫu giọng ${voice.name}`} />}
    </div>
  );
}

function TtsExecutionBadge({ status }: { status: TtsExecutionStatus }) {
  return <span className={`status-badge status-${status}`}><i aria-hidden="true" />{ttsExecutionLabels[status]}</span>;
}

function TtsQualityBadge({ status }: { status: TtsWorkspace["quality_status"] }) {
  const labels = { needs_review: "Cần rà audio", degraded: "Audio có cảnh báo", approved: "Audio đã duyệt" };
  return <span className={`quality-badge quality-${status}`}>{labels[status]}</span>;
}

function TtsOutputState({ state }: { state: "current" | "stale" | "missing" }) {
  return <span className={`tts-output-state tts-output-${state}`}><i />{{ current: "Mới nhất", stale: "Cần tạo lại", missing: "Không còn trên đĩa" }[state]}</span>;
}

function effectiveSpoken(cue: TtsCue, changes: DraftChanges): string {
  return Object.hasOwn(changes, cue.segment_id) ? changes[cue.segment_id] ?? cue.default_spoken_text : cue.effective_spoken_text;
}

function voiceMeta(voice: TtsVoice): string {
  return [voice.locale, voice.gender, voice.age].filter(Boolean).join(" · ") || "Tiếng Việt";
}

function ttsRenderHint(workspace: TtsWorkspace, state: { dirty: boolean; hasInvalid: boolean; editorLocked: boolean; voiceDirty: boolean }): string {
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
