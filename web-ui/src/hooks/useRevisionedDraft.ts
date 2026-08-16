import { useCallback, useEffect, useRef, useState } from "react";

interface RevisionedDraftOptions<T extends { revision: string }> {
  /** Bản mới nhất từ server. */
  data: T;
  /** Đang có chỉnh sửa chưa lưu hay không. */
  dirty: boolean;
  /** Lấy `next` làm bản nền mới, bỏ bản nháp. Phải memo hóa (useCallback). */
  onAdopt: (next: T) => void;
  /**
   * Cùng revision, nhưng các trường phụ đã đổi (khóa editor, cờ output cũ).
   * Làm tươi bản sao server mà không đụng bản nháp. Phải memo hóa.
   */
  onRefresh?: (next: T) => void;
}

export interface RevisionedDraft<T> {
  /** Server đã có bản mới trong lúc đang có bản nháp. */
  conflict: boolean;
  /** Bỏ bản nháp, lấy bản mới nhất của server. */
  discardDraft: () => void;
  /** Gọi sau khi tự lưu thành công: bản vừa lưu trở thành bản nền. */
  markSaved: (saved: T) => void;
  /** Gọi khi server trả 409 revision_conflict. */
  markConflict: () => void;
}

/**
 * Một quy tắc chung cho mọi editor có bản nháp: chỉ `revision` mới nói lên nội
 * dung đã bị ghi đè ở nơi khác, và bản nháp không bao giờ bị vứt trừ khi người
 * dùng bảo thế.
 *
 * Ba editor từng tự trả lời câu này ba kiểu, và hai trong ba kiểu là lỗi: phụ đề
 * so định danh object nên một run TTS lật `editor_locked` cũng thành "đã thay
 * đổi ở tab khác"; glossary khóa form theo `key={revision}` nên server đổi bản
 * là remount và nuốt sạch bản nháp.
 */
export function useRevisionedDraft<T extends { revision: string }>({
  data,
  dirty,
  onAdopt,
  onRefresh,
}: RevisionedDraftOptions<T>): RevisionedDraft<T> {
  const [conflict, setConflict] = useState(false);
  const revisionRef = useRef(data.revision);

  useEffect(() => {
    if (data.revision === revisionRef.current) {
      onRefresh?.(data);
      return;
    }
    revisionRef.current = data.revision;
    if (dirty) {
      setConflict(true);
      return;
    }
    onAdopt(data);
  }, [data, dirty, onAdopt, onRefresh]);

  const discardDraft = useCallback(() => {
    revisionRef.current = data.revision;
    onAdopt(data);
    setConflict(false);
  }, [data, onAdopt]);

  const markSaved = useCallback((saved: T) => {
    revisionRef.current = saved.revision;
    setConflict(false);
  }, []);

  const markConflict = useCallback(() => setConflict(true), []);

  return { conflict, discardDraft, markSaved, markConflict };
}
