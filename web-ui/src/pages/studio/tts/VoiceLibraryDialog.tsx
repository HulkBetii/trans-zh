import { useVirtualizer } from "@tanstack/react-virtual";
import { AlertTriangle, AudioLines, Check, LoaderCircle, Search, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTtsVoices } from "../../../api/queries";
import type { TtsVoice, VoiceOwnership } from "../../../api/types";
import { useDebouncedValue } from "../../../hooks/useDebouncedValue";
import { useDialogFocus } from "../../../hooks/useDialogFocus";
import { errorMessage } from "../../../lib/format";
import { voiceMeta } from "./labels";

export function VoiceLibraryDialog({ open, selectedVoiceId, onClose, onSelect }: { open: boolean; selectedVoiceId: string; onClose: () => void; onSelect: (voice: TtsVoice) => void }) {
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
