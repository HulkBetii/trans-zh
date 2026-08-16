import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import {
  AlertTriangle,
  AudioLines,
  Check,
  CheckCircle2,
  CircleDollarSign,
  Info,
  LoaderCircle,
  RefreshCcw,
  Search,
  Settings2,
  Square,
  Volume2,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, api, apiUrl } from "../../api/client";
import { queryKeys, useTts, useTtsRunEvents } from "../../api/queries";
import type {
  JobDetail,
  RunRecord,
  TtsCue,
  TtsExecutionStatus,
  TtsVoice,
  TtsWorkspace,
} from "../../api/types";
import { useConfirm } from "../../hooks/useConfirm";
import { EmptyState } from "../../components/EmptyState";
import { useDialogFocus } from "../../hooks/useDialogFocus";
import { useMediaQuery } from "../../hooks/useMediaQuery";
import { useRevisionedDraft } from "../../hooks/useRevisionedDraft";
import { useUnsavedChanges } from "../../hooks/useUnsavedChanges";
import { errorMessage, formatDate } from "../../lib/format";
import { TtsExecutionBadge, TtsQualityBadge } from "./tts/TtsBadges";
import { TtsCueEditor, TtsCueRow, TtsInspector } from "./tts/TtsCuePanes";
import { TtsOutput, TtsRunPanel } from "./tts/TtsRunPanel";
import { VoiceLibraryDialog } from "./tts/VoiceLibraryDialog";
import { effectiveSpoken, ttsRenderHint, voiceMeta, type DraftChanges } from "./tts/labels";

type CueFilter = "all" | "edited" | "tight" | "preview";

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
  const confirm = useConfirm();
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
  const hasSavedOverrides = workspace.cues.some((cue) => cue.override_state !== "none");
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
  const resetAll = async () => {
    const ok = await confirm({
      title: "Đặt lại toàn bộ văn bản đọc?",
      detail: "Mọi chỉnh sửa thủ công cho lời đọc sẽ về bản tự động. Phụ đề không đổi.",
      confirmLabel: "Đặt lại toàn bộ",
      tone: "danger",
    });
    if (!ok) return;
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
    const ok = await confirm({
      title: "Nghe thử cue này?",
      detail: "Tổng hợp một cue để nghe trước khi tạo cả timeline.",
      cost: "Tốn 1 lượt TTS của nhà cung cấp.",
      confirmLabel: "Nghe thử",
    });
    if (!ok) return;
    if (selectedDraft) {
      try {
        await saveDrafts.mutateAsync({ scope: "cue", segmentId: selected.segment_id });
      } catch {
        return;
      }
    }
    preview.mutate(selected.segment_id);
  };
  const askCalibrate = async () => {
    if (!workspace.allowed_actions.includes("calibrate") || voiceDirty || runAction.isPending) return;
    const measured = Boolean(workspace.calibration);
    const ok = await confirm({
      title: measured ? "Đo lại giọng này?" : "Hiệu chuẩn giọng này?",
      detail: measured
        ? "Đã có số đo cho giọng này. Đo lại sẽ tổng hợp 8 mẫu mới và ghi đè số cũ."
        : "Đo tốc độ đọc trên 8 câu dài ngắn khác nhau. Một lần cho mỗi giọng, dùng lại cho các job sau.",
      cost: measured
        ? "Tốn 8 lượt TTS."
        : "Tốn tối đa 8 lượt TTS; mẫu đã có trên đĩa được dùng lại.",
      confirmLabel: measured ? "Đo lại" : "Hiệu chuẩn",
    });
    if (!ok) return;
    runAction.mutate("calibrate");
  };
  const askRender = async () => {
    if (!canRender || runAction.isPending) return;
    const uncached = workspace.uncached_cues;
    const ok = await confirm({
      title: "Tạo MP3 lồng tiếng?",
      detail: "Ráp toàn bộ cue theo mốc thời gian của ASR.",
      cost: uncached > 0
        ? `Cần tổng hợp ${uncached} cue mới: tốn ${uncached} lượt TTS.`
        : "Mọi cue đã có sẵn — chỉ ráp lại timeline, không tốn lượt nào.",
      confirmLabel: "Tạo MP3",
    });
    if (!ok) return;
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
            {workspace.allowed_actions.includes("calibrate") && <button className="secondary-button compact" disabled={runBusy || !draftVoiceId || voiceDirty} title={voiceDirty ? "Lưu giọng mới trước khi hiệu chuẩn" : undefined} onClick={() => void askCalibrate()}><CircleDollarSign size={15} />Hiệu chuẩn 8 mẫu</button>}
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
        <div>
          <strong>
            {dirty
              ? `${dirtyCount + (voiceDirty ? 1 : 0)} thay đổi TTS chưa lưu`
              : "Thiết lập TTS đã được lưu"}
          </strong>
          <small className={canRender ? "tts-ready" : "tts-render-hint"}>{renderHint}</small>
          {hasSavedOverrides && (
            <button className="text-button" onClick={() => void resetAll()} disabled={editorLocked}>
              Reset toàn bộ
            </button>
          )}
        </div>
        <div>
          {dirty && (
            <button
              className="secondary-button"
              disabled={hasInvalid || editorLocked || saveDrafts.isPending || conflict}
              onClick={() => saveDrafts.mutate({ scope: "all" })}
            >
              {saveDrafts.isPending ? <LoaderCircle className="spin" size={16} /> : <Check size={16} />}
              Lưu
            </button>
          )}
          <button
            className="primary-button"
            disabled={!canRender || runBusy || conflict}
            title={!canRender ? renderHint : undefined}
            onClick={() => void askRender()}
          >
            <AudioLines size={16} />Tạo MP3 lồng tiếng
          </button>
          {workspace.allowed_actions.includes("approve") && (
            <button
              className="secondary-button"
              disabled={runBusy || outputStale || dirty || conflict}
              onClick={() => approval.mutate("approve")}
            >
              <CheckCircle2 size={16} />Duyệt audio
            </button>
          )}
          {workspace.allowed_actions.includes("unapprove") && (
            <button
              className="secondary-button"
              disabled={runBusy || dirty || conflict}
              onClick={() => approval.mutate("unapprove")}
            >
              <Square size={15} />Bỏ duyệt audio
            </button>
          )}
        </div>
      </div>

      <VoiceLibraryDialog open={voiceDialogOpen} selectedVoiceId={draftVoiceId} onClose={() => setVoiceDialogOpen(false)} onSelect={(voice) => { setDraftVoiceId(voice.voice_id); setDraftVoice(voice); setVoiceDialogOpen(false); }} />
    </div>
  );
}
