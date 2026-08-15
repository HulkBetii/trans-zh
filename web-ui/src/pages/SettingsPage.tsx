import {
  AlertTriangle,
  CheckCircle2,
  KeyRound,
  LoaderCircle,
  RefreshCcw,
  Save,
  ServerCog,
  ShieldCheck,
  Trash2,
  XCircle,
} from "lucide-react";
import { useState } from "react";
import { ApiError } from "../api/client";
import {
  useDeleteCredential,
  useSaveCredential,
  useSettings,
  useTestCredential,
} from "../api/queries";
import type {
  CredentialId,
  SettingsCredential,
  SettingsProvider,
} from "../api/types";
import { EmptyState } from "../components/EmptyState";
import { useUnsavedChanges } from "../hooks/useUnsavedChanges";
import { errorMessage } from "../lib/format";

type Feedback = { kind: "success" | "error"; text: string };

const credentialOrder: CredentialId[] = ["openai", "anthropic", "ai33"];

export function SettingsPage() {
  const settingsQuery = useSettings();
  const saveCredential = useSaveCredential();
  const deleteCredential = useDeleteCredential();
  const testCredential = useTestCredential();
  const [drafts, setDrafts] = useState<Partial<Record<CredentialId, string>>>({});
  const [draftRevisions, setDraftRevisions] = useState<Partial<Record<CredentialId, string>>>({});
  const [feedback, setFeedback] = useState<Partial<Record<CredentialId, Feedback>>>({});
  const [conflicts, setConflicts] = useState<Partial<Record<CredentialId, boolean>>>({});
  const dirty = Object.values(drafts).some((secret) => Boolean(secret));
  useUnsavedChanges(dirty, "Bạn có khóa API chưa lưu. Rời trang và bỏ các giá trị vừa nhập?");

  if (settingsQuery.isLoading) {
    return <div className="editor-loading"><LoaderCircle className="spin" size={20} />Đang đọc cài đặt bảo mật…</div>;
  }
  if (settingsQuery.isError || !settingsQuery.data) {
    return (
      <div className="page state-page">
        <EmptyState
          icon={AlertTriangle}
          title="Không đọc được cài đặt"
          detail={errorMessage(settingsQuery.error)}
          action={<button className="secondary-button" onClick={() => void settingsQuery.refetch()}>Thử lại</button>}
        />
      </div>
    );
  }

  const settings = settingsQuery.data;
  const credentials = [...settings.credentials].sort(
    (left, right) => credentialOrder.indexOf(left.id) - credentialOrder.indexOf(right.id),
  );
  const busy = saveCredential.isPending || deleteCredential.isPending || testCredential.isPending;

  const setDraft = (credentialId: CredentialId, secret: string) => {
    const hadDraft = Boolean(drafts[credentialId]);
    setDrafts((current) => ({ ...current, [credentialId]: secret }));
    setDraftRevisions((current) => {
      if (!secret) {
        const next = { ...current };
        delete next[credentialId];
        return next;
      }
      if (hadDraft || current[credentialId]) return current;
      return { ...current, [credentialId]: settings.revision };
    });
    setFeedback((current) => ({ ...current, [credentialId]: undefined }));
  };

  const setRowFeedback = (credentialId: CredentialId, next: Feedback | undefined) => {
    setFeedback((current) => ({ ...current, [credentialId]: next }));
  };

  const save = async (credential: SettingsCredential) => {
    const secret = drafts[credential.id]?.trim() ?? "";
    if (!secret) return;
    setRowFeedback(credential.id, undefined);
    try {
      await saveCredential.mutateAsync({
        credentialId: credential.id,
        revision: draftRevisions[credential.id] ?? settings.revision,
        secret,
      });
      setDrafts((current) => ({ ...current, [credential.id]: "" }));
      setDraftRevisions((current) => {
        const next = { ...current };
        delete next[credential.id];
        return next;
      });
      setConflicts((current) => ({ ...current, [credential.id]: false }));
      setRowFeedback(credential.id, { kind: "success", text: "Đã lưu khóa vào kho bảo mật của hệ điều hành." });
    } catch (error) {
      if (isRevisionConflict(error)) {
        setConflicts((current) => ({ ...current, [credential.id]: true }));
        return;
      }
      setRowFeedback(credential.id, { kind: "error", text: settingsErrorMessage(error) });
    }
  };

  const test = async (credential: SettingsCredential) => {
    const secret = drafts[credential.id]?.trim();
    setRowFeedback(credential.id, undefined);
    try {
      const result = await testCredential.mutateAsync({
        credentialId: credential.id,
        ...(secret ? { secret } : {}),
      });
      const latency = result.latency_ms == null ? "" : ` · ${Math.round(result.latency_ms)} ms`;
      setRowFeedback(credential.id, result.ok
        ? { kind: "success", text: `Kết nối thành công${latency}.` }
        : { kind: "error", text: credentialTestError(result.code) });
    } catch (error) {
      setRowFeedback(credential.id, { kind: "error", text: settingsErrorMessage(error) });
    }
  };

  const remove = async (credential: SettingsCredential) => {
    if (!window.confirm(`Xóa khóa ${credential.label} đã lưu khỏi máy này?`)) return;
    setRowFeedback(credential.id, undefined);
    try {
      await deleteCredential.mutateAsync({ credentialId: credential.id, revision: settings.revision });
      setDrafts((current) => ({ ...current, [credential.id]: "" }));
      setDraftRevisions((current) => {
        const next = { ...current };
        delete next[credential.id];
        return next;
      });
      setConflicts((current) => ({ ...current, [credential.id]: false }));
      setRowFeedback(credential.id, { kind: "success", text: "Đã xóa khóa khỏi kho bảo mật." });
    } catch (error) {
      if (isRevisionConflict(error)) {
        setConflicts((current) => ({ ...current, [credential.id]: true }));
        return;
      }
      setRowFeedback(credential.id, { kind: "error", text: settingsErrorMessage(error) });
    }
  };

  const reloadConflict = async (credentialId: CredentialId) => {
    const result = await settingsQuery.refetch();
    if (!result.data) return;
    const revision = result.data.revision;
    setDraftRevisions((current) => ({ ...current, [credentialId]: revision }));
    setConflicts((current) => ({ ...current, [credentialId]: false }));
    setRowFeedback(credentialId, undefined);
  };

  return (
    <div className="page settings-page">
      <header className="page-intro settings-intro">
        <span className="section-number">Workspace / Security</span>
        <h1>Kết nối nhà cung cấp AI</h1>
        <p>Quản lý khóa API dùng cho phân đoạn, dịch và lồng tiếng. Giá trị bí mật chỉ được gửi khi lưu hoặc kiểm tra kết nối, không bao giờ được đọc ngược ra giao diện.</p>
      </header>

      {!settings.credential_store_available && (
        <div className="settings-store-alert" role="alert">
          <AlertTriangle size={18} />
          <div><strong>Kho bảo mật hệ điều hành chưa sẵn sàng</strong><p>Không thể lưu khóa từ giao diện. Bạn vẫn có thể cấu hình bằng biến môi trường tương ứng.</p></div>
        </div>
      )}

      <div className="settings-layout">
        <section className="settings-credentials" aria-labelledby="settings-credentials-title">
          <div className="settings-section-heading">
            <div><span className="eyebrow">Credentials</span><h2 id="settings-credentials-title">Khóa API</h2></div>
            <span>{credentials.filter((credential) => credential.configured).length}/{credentials.length} đã cấu hình</span>
          </div>

          <div className="credential-sheet">
            {credentials.map((credential) => {
              const draft = drafts[credential.id] ?? "";
              const rowFeedback = feedback[credential.id];
              const conflict = conflicts[credential.id];
              const canEdit = credential.editable && settings.credential_store_available;
              const canTest = Boolean(draft.trim()) || credential.configured;
              const savePending = saveCredential.isPending && saveCredential.variables?.credentialId === credential.id;
              const testPending = testCredential.isPending && testCredential.variables?.credentialId === credential.id;
              const deletePending = deleteCredential.isPending && deleteCredential.variables?.credentialId === credential.id;

              return (
                <section className="credential-row" key={credential.id} aria-labelledby={`credential-${credential.id}`}>
                  <div className="credential-identity">
                    <span className={`credential-mark ${credential.configured ? "configured" : ""}`}>
                      {credential.configured ? <ShieldCheck size={18} /> : <KeyRound size={18} />}
                    </span>
                    <div>
                      <h3 id={`credential-${credential.id}`}>{credential.label}</h3>
                      <p>{credential.env_name}</p>
                    </div>
                  </div>

                  <div className="credential-state">
                    <span className={`credential-source source-${credential.source}`}>{sourceLabel(credential.source)}</span>
                    <code>{credential.masked_value || "Chưa có khóa"}</code>
                  </div>

                  <div className="credential-editor">
                    <label htmlFor={`secret-${credential.id}`}>Khóa API mới</label>
                    <input
                      id={`secret-${credential.id}`}
                      className="text-input mono-input"
                      type="password"
                      autoComplete="new-password"
                      aria-label={`Khóa API ${credential.label}`}
                      value={draft}
                      placeholder={canEdit ? "Dán khóa mới để thay thế" : "Được quản lý ngoài ứng dụng"}
                      disabled={!canEdit || busy}
                      onChange={(event) => setDraft(credential.id, event.target.value)}
                    />
                    <small>{canEdit ? "Trường này luôn để trống sau khi tải hoặc lưu." : `Quản lý khóa qua ${credential.env_name}; giao diện không ghi đè nguồn này.`}</small>
                  </div>

                  {conflict && (
                    <div className="credential-conflict" role="alert">
                      <AlertTriangle size={16} />
                      <span>Cài đặt đã đổi ở tab khác. Khóa đang nhập vẫn được giữ lại.</span>
                      <button className="text-button" onClick={() => void reloadConflict(credential.id)}><RefreshCcw size={14} />Tải revision mới</button>
                    </div>
                  )}

                  {rowFeedback && (
                    <div className={`credential-feedback ${rowFeedback.kind}`} role="status">
                      {rowFeedback.kind === "success" ? <CheckCircle2 size={15} /> : <XCircle size={15} />}
                      <span>{rowFeedback.text}</span>
                    </div>
                  )}

                  <div className="credential-actions">
                    <button
                      className="secondary-button compact"
                      type="button"
                      disabled={!canTest || busy || Boolean(conflict)}
                      onClick={() => void test(credential)}
                    >
                      {testPending ? <LoaderCircle className="spin" size={15} /> : <ServerCog size={15} />}
                      Kiểm tra
                    </button>
                    {credential.configured && canEdit && (
                      <button
                        className="secondary-button compact danger"
                        type="button"
                        disabled={busy || Boolean(conflict)}
                        onClick={() => void remove(credential)}
                      >
                        {deletePending ? <LoaderCircle className="spin" size={15} /> : <Trash2 size={15} />}
                        Xóa
                      </button>
                    )}
                    <button
                      className="primary-button compact"
                      type="button"
                      disabled={!canEdit || !draft.trim() || busy || Boolean(conflict)}
                      onClick={() => void save(credential)}
                    >
                      {savePending ? <LoaderCircle className="spin" size={15} /> : <Save size={15} />}
                      Lưu khóa
                    </button>
                  </div>
                </section>
              );
            })}
          </div>
        </section>

        <aside className="settings-providers" aria-label="Cấu hình nhà cung cấp">
          <div className="settings-section-heading">
            <div><span className="eyebrow">Provider registry</span><h2>Cấu hình đang dùng</h2></div>
          </div>
          <p className="settings-aside-note">Model và endpoint được khóa trong cấu hình dự án. Trang này chỉ quản lý thông tin xác thực.</p>
          <div className="provider-list">
            {settings.providers.map((provider) => (
              <ProviderRow key={provider.id} provider={provider} credentials={credentials} />
            ))}
            {settings.providers.length === 0 && <p className="settings-empty">Chưa có provider nào được công bố.</p>}
          </div>
          <div className="settings-security-note">
            <ShieldCheck size={18} />
            <div><strong>Nguyên tắc bảo mật</strong><p>Frontend không nhận secret đã lưu. Kiểm tra kết nối trả về trạng thái và độ trễ, không trả lại khóa.</p></div>
          </div>
        </aside>
      </div>
    </div>
  );
}

function ProviderRow({ provider, credentials }: { provider: SettingsProvider; credentials: SettingsCredential[] }) {
  const credential = credentials.find((item) => item.id === provider.credential_id);
  return (
    <section className="provider-row">
      <div className="provider-row-heading"><strong>{provider.label}</strong><span>{provider.provider}</span></div>
      <dl>
        <div><dt>Model</dt><dd><code>{provider.model || "Chưa cấu hình"}</code></dd></div>
        <div><dt>Endpoint</dt><dd><code>{provider.base_url || "Mặc định nhà cung cấp"}</code></dd></div>
        <div><dt>Credential</dt><dd>{credential?.label ?? provider.credential_id ?? "Không yêu cầu"}</dd></div>
      </dl>
    </section>
  );
}

function sourceLabel(source: SettingsCredential["source"]): string {
  if (source === "environment") return "ENV";
  if (source === "credential_store") return "Vault";
  return "Chưa cấu hình";
}

function credentialTestError(code: string): string {
  if (code === "invalid_credentials") return "Khóa bị nhà cung cấp từ chối.";
  if (code === "provider_unavailable") return "Không thể kết nối tới nhà cung cấp lúc này.";
  return "Không thể xác minh khóa API.";
}

function isRevisionConflict(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409 && apiErrorCode(error) === "revision_conflict";
}

function settingsErrorMessage(error: unknown): string {
  const code = error instanceof ApiError ? apiErrorCode(error) : undefined;
  const reason = error instanceof ApiError ? apiErrorReason(error) : undefined;
  if (code === "active_run" || reason === "active_run") return "Không thể đổi khóa khi còn run đang hoạt động.";
  if (code === "managed_by_environment" || reason === "managed_by_environment") return "Khóa này đang được quản lý bằng biến môi trường.";
  if (code === "credential_store_unavailable") return "Kho bảo mật hệ điều hành hiện không khả dụng.";
  if (code === "credential_not_configured") return "Hãy nhập hoặc lưu khóa trước khi kiểm tra.";
  return errorMessage(error);
}

function apiErrorCode(error: ApiError): string | undefined {
  if (typeof error.body !== "object" || error.body === null || !("detail" in error.body)) return undefined;
  const detail = error.body.detail;
  if (typeof detail !== "object" || detail === null || !("code" in detail)) return undefined;
  return typeof detail.code === "string" ? detail.code : undefined;
}

function apiErrorReason(error: ApiError): string | undefined {
  if (typeof error.body !== "object" || error.body === null || !("detail" in error.body)) return undefined;
  const detail = error.body.detail;
  if (typeof detail !== "object" || detail === null || !("reason" in detail)) return undefined;
  return typeof detail.reason === "string" ? detail.reason : undefined;
}
