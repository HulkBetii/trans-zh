import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, LoaderCircle, Plus, RotateCcw, Save, Sparkles, Trash2 } from "lucide-react";
import { useCallback, useMemo, useRef, useState } from "react";
import { ApiError, api } from "../../api/client";
import { queryKeys } from "../../api/queries";
import type { AddressTerm, GlossaryDocument, GlossaryTerm, JobDetail, RevisionedGlossary } from "../../api/types";
import { EmptyState } from "../../components/EmptyState";
import { useRevisionedDraft } from "../../hooks/useRevisionedDraft";
import { useUnsavedChanges } from "../../hooks/useUnsavedChanges";
import { errorMessage } from "../../lib/format";

const emptyTerm: GlossaryTerm = { zh: "", pinyin: "", vi: "", en: "", type: "other", keep_source: false, note: "" };
const emptyAddress: AddressTerm = { speaker: "", addressee: "", vi_self: "", vi_other: "", basis: "" };

export function GlossaryEditor({ job }: { job: JobDetail }) {
  const query = useQuery({ queryKey: queryKeys.glossary(job.job_id), queryFn: () => api.glossary(job.job_id) });
  if (query.isLoading) return <div className="editor-loading"><LoaderCircle className="spin" size={20} />Đang tải glossary…</div>;
  if (query.isError || !query.data) return <EmptyState icon={AlertTriangle} title="Không đọc được glossary" detail={errorMessage(query.error)} action={<button className="secondary-button" onClick={() => void query.refetch()}>Thử lại</button>} />;
  // Khóa theo job, không theo revision: khóa theo revision thì mỗi lần server
  // đổi bản là form remount và bản nháp chưa lưu biến mất không một lời nào.
  return <GlossaryForm key={job.job_id} job={job} data={query.data} />;
}

function GlossaryForm({ job, data }: { job: JobDetail; data: RevisionedGlossary }) {
  const queryClient = useQueryClient();
  const [doc, setDoc] = useState(() => structuredClone(data.document));
  const [baseline, setBaseline] = useState(() => JSON.stringify(data.document));
  const [currentRevision, setCurrentRevision] = useState(data.revision);
  const [followUpError, setFollowUpError] = useState<string | null>(null);
  const nextTermId = useRef(data.document.terms.length);
  const nextAddressId = useRef(data.document.address_terms.length);
  const [termIds, setTermIds] = useState(() => data.document.terms.map((_, index) => `term-${index}`));
  const [addressIds, setAddressIds] = useState(() => data.document.address_terms.map((_, index) => `address-${index}`));
  const errors = useMemo(() => validateGlossary(doc), [doc]);
  const dirty = useMemo(() => JSON.stringify(doc) !== baseline, [baseline, doc]);
  // Khóa editor đọc thẳng từ props nên luôn tươi, không cần đồng bộ vào state.
  const editorLocked = data.editor_locked;
  const lockReason = data.lock_reason;
  useUnsavedChanges(dirty);

  // Nhận một bản từ server làm bản nền mới. Phải dựng lại termIds/addressIds:
  // trước đây việc đó do remount lo, giờ không còn remount nữa.
  const adopt = useCallback((next: RevisionedGlossary) => {
    setDoc(structuredClone(next.document));
    setBaseline(JSON.stringify(next.document));
    setCurrentRevision(next.revision);
    setTermIds(next.document.terms.map((_, index) => `term-${index}`));
    setAddressIds(next.document.address_terms.map((_, index) => `address-${index}`));
    nextTermId.current = next.document.terms.length;
    nextAddressId.current = next.document.address_terms.length;
  }, []);

  const { conflict, discardDraft, markSaved, markConflict } = useRevisionedDraft({ data, dirty, onAdopt: adopt });

  const saveMutation = useMutation({
    mutationFn: async (retranslate: boolean) => {
      const saved = await api.saveGlossary(job.job_id, currentRevision, doc);
      if (!retranslate) return { saved, followUpError: null };
      try {
        await api.retranslate(job.job_id);
        return { saved, followUpError: null };
      } catch (error) {
        return { saved, followUpError: errorMessage(error) };
      }
    },
    onSuccess: ({ saved, followUpError: retranslateError }) => {
      markSaved(saved);
      adopt(saved);
      setFollowUpError(retranslateError);
      queryClient.setQueryData(queryKeys.glossary(job.job_id), saved);
      void queryClient.invalidateQueries({ queryKey: queryKeys.job(job.job_id) });
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
    onError: (error) => {
      if (error instanceof ApiError && error.code === "revision_conflict") markConflict();
    },
  });

  const updateTerm = (index: number, patch: Partial<GlossaryTerm>) => setDoc((current) => ({
    ...current,
    terms: current.terms.map((term, termIndex) => termIndex === index ? { ...term, ...patch } : term),
  }));
  const updateAddress = (index: number, patch: Partial<AddressTerm>) => setDoc((current) => ({
    ...current,
    address_terms: current.address_terms.map((term, termIndex) => termIndex === index ? { ...term, ...patch } : term),
  }));
  const addTerm = () => {
    const id = `term-${nextTermId.current++}`;
    setTermIds((current) => [...current, id]);
    setDoc((current) => ({ ...current, terms: [...current.terms, { ...emptyTerm }] }));
  };
  const removeTerm = (index: number) => {
    setTermIds((current) => current.filter((_, termIndex) => termIndex !== index));
    setDoc((current) => ({ ...current, terms: current.terms.filter((_, termIndex) => termIndex !== index) }));
  };
  const addAddress = () => {
    const id = `address-${nextAddressId.current++}`;
    setAddressIds((current) => [...current, id]);
    setDoc((current) => ({ ...current, address_terms: [...current.address_terms, { ...emptyAddress }] }));
  };
  const removeAddress = (index: number) => {
    setAddressIds((current) => current.filter((_, termIndex) => termIndex !== index));
    setDoc((current) => ({ ...current, address_terms: current.address_terms.filter((_, termIndex) => termIndex !== index) }));
  };
  const canSave = dirty && errors.length === 0 && !saveMutation.isPending && !editorLocked && !conflict;

  return (
    <div className="glossary-editor">
      {editorLocked && <div className="inline-alert"><LoaderCircle className="spin" size={16} /><span>{lockReason === "tts_run_active" ? "Tác vụ audio đang dùng glossary. Editor tạm khóa để giữ artifact nhất quán." : "Pipeline đang tạo lại glossary hoặc bản dịch. Editor tạm khóa để tránh xung đột."}</span></div>}
      {conflict && <div className="conflict-banner"><AlertTriangle size={19} /><div><strong>Glossary đã thay đổi ở nơi khác</strong><p>Chỉnh sửa của bạn vẫn còn trên màn hình và chưa bị ghi đè. Lưu tạm khóa để không đè lên bản mới.</p></div><button className="secondary-button danger" onClick={discardDraft}><RotateCcw size={16} />Bỏ chỉnh sửa & lấy bản mới</button></div>}

      <fieldset className="panel-sheet glossary-section" disabled={editorLocked}>
        <div className="section-heading"><div><span className="eyebrow">Translation voice</span><h2>Văn phong</h2><p>Ghim cách kể và đại từ để các batch dịch không tự chọn lại.</p></div></div>
        <div className="style-grid">
          <TextField label="Sắc thái lời thoại" value={doc.style.speech_register} placeholder="Tự nhiên, gần gũi…" onChange={(value) => setDoc((current) => ({ ...current, style: { ...current.style, speech_register: value } }))} />
          <TextField label="Người kể tự xưng" value={doc.style.narrator_self_vi} placeholder="tôi / mình" onChange={(value) => setDoc((current) => ({ ...current, style: { ...current.style, narrator_self_vi: value } }))} />
          <TextField label="Gọi khán giả" value={doc.style.audience_vi} placeholder="các bạn / quý vị" onChange={(value) => setDoc((current) => ({ ...current, style: { ...current.style, audience_vi: value } }))} />
          <TextField label="Đại từ chủ thể" value={doc.style.subject_third_person_vi} placeholder="anh ấy / cô ấy" onChange={(value) => setDoc((current) => ({ ...current, style: { ...current.style, subject_third_person_vi: value } }))} />
        </div>
      </fieldset>

      <fieldset className="panel-sheet glossary-section" disabled={editorLocked}>
        <div className="section-heading"><div><span className="eyebrow">Terminology</span><h2>Thuật ngữ</h2><p>Tên riêng và thuật ngữ cần nhất quán trong toàn bộ video.</p></div><button className="secondary-button compact" onClick={addTerm} type="button"><Plus size={16} />Thêm</button></div>
        <div className="glossary-table-wrap">
          <div className="glossary-table term-table">
            <div className="glossary-head"><span>中文 *</span><span>Pinyin</span><span>Tiếng Việt</span><span>English</span><span>Loại</span><span>Giữ gốc</span><span /></div>
            {doc.terms.length === 0 && <div className="table-empty">Chưa có thuật ngữ. Đây là trạng thái hợp lệ; chỉ thêm những mục thực sự cần ghim.</div>}
            {doc.terms.map((term, index) => (
              <div className={`glossary-row ${errors.some((error) => error.index === index && error.scope === "term") ? "invalid" : ""}`} key={termIds[index]}>
                <label className="glossary-cell"><span>中文 *</span><input aria-label={`Thuật ngữ Trung ${index + 1}`} value={term.zh} onChange={(event) => updateTerm(index, { zh: event.target.value })} /></label>
                <label className="glossary-cell"><span>Pinyin</span><input aria-label={`Pinyin ${index + 1}`} value={term.pinyin} onChange={(event) => updateTerm(index, { pinyin: event.target.value })} /></label>
                <label className="glossary-cell"><span>Tiếng Việt</span><input aria-label={`Tiếng Việt ${index + 1}`} value={term.vi} disabled={term.keep_source} onChange={(event) => updateTerm(index, { vi: event.target.value })} /></label>
                <label className="glossary-cell"><span>English</span><input aria-label={`Tiếng Anh ${index + 1}`} value={term.en} disabled={term.keep_source} onChange={(event) => updateTerm(index, { en: event.target.value })} /></label>
                <label className="glossary-cell"><span>Loại</span><select aria-label={`Loại ${index + 1}`} value={term.type} onChange={(event) => updateTerm(index, { type: event.target.value as GlossaryTerm["type"] })}><option value="person">Nhân vật</option><option value="place">Địa danh</option><option value="org">Tổ chức</option><option value="term">Thuật ngữ</option><option value="dish">Món ăn</option><option value="product">Sản phẩm</option><option value="other">Khác</option></select></label>
                <label className="table-checkbox glossary-cell"><span>Giữ gốc</span><input aria-label={`Giữ nguyên nguồn cho thuật ngữ ${index + 1}`} type="checkbox" checked={term.keep_source} onChange={(event) => updateTerm(index, { keep_source: event.target.checked, ...(event.target.checked ? { vi: "", en: "" } : {}) })} /></label>
                <button className="icon-button" type="button" onClick={() => removeTerm(index)} aria-label={`Xóa thuật ngữ ${term.zh || index + 1}`}><Trash2 size={15} /></button>
              </div>
            ))}
          </div>
        </div>
        {errors.filter((error) => error.scope === "term").map((error) => <p className="validation-line" key={`${error.index}-${error.message}`}><AlertTriangle size={14} />Dòng {(error.index ?? 0) + 1}: {error.message}</p>)}
      </fieldset>

      <fieldset className="panel-sheet glossary-section" disabled={editorLocked}>
        <div className="section-heading"><div><span className="eyebrow">Relationships</span><h2>Cách xưng hô</h2><p>Quan hệ theo từng cặp người nói — người nghe. Danh sách rỗng hoàn toàn hợp lệ.</p></div><button className="secondary-button compact" onClick={addAddress} type="button"><Plus size={16} />Thêm</button></div>
        <div className="address-list">
          {doc.address_terms.map((term, index) => (
            <div className={`address-row ${errors.some((error) => error.scope === "address" && error.index === index) ? "invalid" : ""}`} key={addressIds[index]}>
              <TextField label="Người nói" value={term.speaker} onChange={(value) => updateAddress(index, { speaker: value })} />
              <TextField label="Người nghe" value={term.addressee} onChange={(value) => updateAddress(index, { addressee: value })} />
              <TextField label="Tự xưng" value={term.vi_self} onChange={(value) => updateAddress(index, { vi_self: value })} />
              <TextField label="Gọi đối phương" value={term.vi_other} onChange={(value) => updateAddress(index, { vi_other: value })} />
              <button className="icon-button" type="button" onClick={() => removeAddress(index)} aria-label={`Xóa cách xưng hô ${index + 1}`}><Trash2 size={16} /></button>
            </div>
          ))}
          {doc.address_terms.length === 0 && <div className="table-empty">Chưa có quan hệ xưng hô cần ghim.</div>}
        </div>
        {errors.filter((error) => error.scope === "address").map((error) => <p className="validation-line" key={`address-${error.index}-${error.message}`}><AlertTriangle size={14} />Dòng {(error.index ?? 0) + 1}: {error.message}</p>)}
      </fieldset>

      {saveMutation.isError && !conflict && <div className="inline-alert error"><AlertTriangle size={16} /><span>{errorMessage(saveMutation.error)}</span></div>}
      {followUpError && <div className="inline-alert error"><AlertTriangle size={16} /><span>Glossary đã lưu, nhưng chưa thể bắt đầu dịch lại: {followUpError}</span></div>}
      {dirty && <div className="sticky-savebar dirty">
        <span>{dirty ? "Có thay đổi chưa lưu" : "Mọi thay đổi đã được lưu"}</span>
        <div><button className="secondary-button" disabled={!canSave} onClick={() => saveMutation.mutate(false)}>{saveMutation.isPending ? <LoaderCircle className="spin" size={16} /> : <Save size={16} />}Lưu</button><button className="primary-button" disabled={!canSave} onClick={() => saveMutation.mutate(true)}><Sparkles size={16} />Lưu & dịch lại</button></div>
      </div>}
    </div>
  );
}

function TextField({ label, value, placeholder, onChange }: { label: string; value: string; placeholder?: string; onChange: (value: string) => void }) {
  return <label className="stacked-field"><span>{label}</span><input value={value} placeholder={placeholder} onChange={(event) => onChange(event.target.value)} /></label>;
}

interface ValidationError { scope: "term" | "address"; index?: number; message: string }

function validateGlossary(doc: GlossaryDocument): ValidationError[] {
  const errors: ValidationError[] = [];
  const seen = new Map<string, number>();
  doc.terms.forEach((term, index) => {
    const zh = term.zh.trim();
    if (!zh) errors.push({ scope: "term", index, message: "中文 không được để trống." });
    if (zh && seen.has(zh)) errors.push({ scope: "term", index, message: `Trùng chính xác với dòng ${(seen.get(zh) ?? 0) + 1}.` });
    if (zh) seen.set(zh, index);
    if (term.keep_source && (term.vi.trim() || term.en.trim())) errors.push({ scope: "term", index, message: "Mục giữ nguyên nguồn không được có bản dịch." });
  });
  // Nhánh "address" của ValidationError tồn tại từ đầu nhưng chưa từng có ai
  // ghi vào: một cặp xưng hô thiếu người nói hoặc người nghe thì không ghim được
  // gì, và hai dòng cùng một cặp thì dòng sau lặng lẽ vô hiệu.
  const pairs = new Map<string, number>();
  doc.address_terms.forEach((term, index) => {
    const speaker = term.speaker.trim();
    const addressee = term.addressee.trim();
    if (!speaker || !addressee) {
      errors.push({ scope: "address", index, message: "Cần cả người nói và người nghe." });
      return;
    }
    const key = `${speaker} ${addressee}`;
    if (pairs.has(key)) {
      errors.push({
        scope: "address",
        index,
        message: `Trùng cặp xưng hô với dòng ${(pairs.get(key) ?? 0) + 1}.`,
      });
    } else {
      pairs.set(key, index);
    }
  });
  return errors;
}
