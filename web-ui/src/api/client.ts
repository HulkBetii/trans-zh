import type {
  CreateJobRequest,
  CreateJobResponse,
  CredentialDeleteRequest,
  CredentialId,
  CredentialTestRequest,
  CredentialTestResponse,
  CredentialWriteRequest,
  FileListing,
  FileRoot,
  FileRootsResponse,
  GlossaryDocument,
  GlossaryResponseDto,
  JobDetail,
  JobListItem,
  JobsPage,
  JobLaneSummary,
  OverrideChange,
  RevisionedGlossary,
  RunRecord,
  SettingsResponse,
  StudioMeta,
  SubtitleResponseDto,
  SubtitleWorkspace,
  TargetLanguage,
  TtsPreviewRequest,
  TtsSettingsUpdateRequest,
  TtsVoicePage,
  TtsWorkspace,
  TtsWorkspaceDto,
  SpokenOverrideUpdateRequest,
} from "./types";

const API_BASE = "/api/v1";

export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;
  readonly code?: string;

  constructor(status: number, message: string, body: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
    this.code = apiErrorCode(body);
  }
}

function apiErrorCode(body: unknown): string | undefined {
  if (typeof body !== "object" || body === null || !("detail" in body)) return undefined;
  const detail = (body as { detail: unknown }).detail;
  if (typeof detail !== "object" || detail === null || !("code" in detail)) return undefined;
  return typeof (detail as { code: unknown }).code === "string"
    ? (detail as { code: string }).code
    : undefined;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });

  const contentType = response.headers.get("content-type") ?? "";
  const body = contentType.includes("application/json")
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    const detail = apiErrorMessage(body, response.status);
    throw new ApiError(response.status, detail, body);
  }
  return body as T;
}

function apiErrorMessage(body: unknown, status: number): string {
  if (typeof body !== "object" || body === null || !("detail" in body)) {
    return `Yêu cầu thất bại (${status})`;
  }
  const detail = (body as { detail: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (typeof detail === "object" && detail !== null) {
    const conflict = detail as { code?: unknown; message?: unknown };
    if (conflict.code === "revision_conflict") {
      return "Dữ liệu đã thay đổi ở một tab khác. Hãy tải bản mới trước khi lưu lại.";
    }
    if (typeof conflict.message === "string") return conflict.message;
  }
  return `Yêu cầu thất bại (${status})`;
}

function normalizeJob(item: JobListItem & { status?: JobListItem["execution_status"] }): JobListItem {
  const executionStatus = item.execution_status ?? item.status ?? "interrupted";
  const qualityStatus = item.quality_status ?? "needs_review";
  const pipelineLane: JobLaneSummary = {
    id: "pipeline",
    started: true,
    execution_status: executionStatus,
    quality_status: qualityStatus,
    stage: item.stage,
    progress: item.progress ?? 0,
    message: item.message ?? "",
    error: item.error,
    attention_reasons: item.attention_reasons ?? [],
    active_run: item.active_run,
    latest_run: item.active_run,
    allowed_actions: item.active_run ? ["cancel"] : [],
  };
  return {
    ...item,
    title: item.title || item.job_id,
    execution_status: executionStatus,
    quality_status: qualityStatus,
    lanes: item.lanes?.length ? item.lanes : [
      pipelineLane,
      {
        id: "tts",
        started: false,
        execution_status: "not_started",
        quality_status: "needs_review",
        progress: 0,
        message: "",
        attention_reasons: [],
        allowed_actions: [],
      },
    ],
  };
}

function normalizeGlossary(payload: GlossaryResponseDto): RevisionedGlossary {
  return {
    revision: payload.revision,
    editor_locked: Boolean(payload.editor_locked),
    lock_reason: payload.lock_reason,
    document: {
      ...payload.document,
      terms: payload.document.terms ?? [],
      address_terms: payload.document.address_terms ?? [],
      style: payload.document.style ?? {
        speech_register: "",
        narrator_self_vi: "",
        audience_vi: "",
        subject_third_person_vi: "",
      },
    },
  };
}

function normalizeSubtitles(payload: SubtitleResponseDto): SubtitleWorkspace {
  return {
    ...payload,
    cues: payload.cues.map((cue) => ({ ...cue, warnings: cue.warnings ?? [] })),
  };
}

function normalizeTts(payload: TtsWorkspaceDto): TtsWorkspace {
  return {
    ...payload,
    attention_reasons: payload.attention_reasons ?? [],
    allowed_actions: payload.allowed_actions ?? [],
    cues: (payload.cues ?? []).map((cue) => ({
      ...cue,
      override_state: cue.override_state ?? "none",
      preview_state: cue.preview_state ?? "missing",
      speed: cue.speed ?? 1,
      room_seconds: cue.room_seconds ?? 0,
    })),
    total_cues: payload.total_cues ?? payload.cues?.length ?? 0,
    uncached_cues: payload.uncached_cues ?? 0,
    output_stale: Boolean(payload.output_stale),
    editor_locked: Boolean(payload.editor_locked),
  };
}

export const api = {
  meta: () => request<StudioMeta>("/meta"),
  settings: () => request<SettingsResponse>("/settings"),
  saveCredential: (credentialId: CredentialId, payload: CredentialWriteRequest) =>
    request<SettingsResponse>(`/settings/credentials/${credentialId}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  deleteCredential: (credentialId: CredentialId, payload: CredentialDeleteRequest) =>
    request<SettingsResponse>(`/settings/credentials/${credentialId}`, {
      method: "DELETE",
      body: JSON.stringify(payload),
    }),
  testCredential: (credentialId: CredentialId, payload: CredentialTestRequest) =>
    request<CredentialTestResponse>(`/settings/credentials/${credentialId}/test`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  async jobs(params: { search?: string; execution?: string; quality?: string; lane?: "pipeline" | "tts" | "any"; page?: number } = {}): Promise<JobsPage> {
    const query = new URLSearchParams();
    if (params.search) query.set("search", params.search);
    if (params.execution) query.set("execution", params.execution);
    if (params.quality) query.set("quality", params.quality);
    if (params.lane) query.set("lane", params.lane);
    if (params.page) query.set("page", String(params.page));
    const result = await request<JobsPage | JobListItem[]>(`/jobs?${query}`);
    if (Array.isArray(result)) {
      return { items: result.map(normalizeJob), total: result.length, page: 1, page_size: result.length };
    }
    return { ...result, items: result.items.map(normalizeJob) };
  },

  createJob: (payload: CreateJobRequest) =>
    request<CreateJobResponse>("/jobs", { method: "POST", body: JSON.stringify(payload) }),

  async job(jobId: string): Promise<JobDetail> {
    const result = await request<JobDetail & { status?: JobDetail["execution_status"] }>(`/jobs/${encodeURIComponent(jobId)}`);
    return normalizeJob(result) as JobDetail;
  },

  retry: (jobId: string) => request<RunRecord>(`/jobs/${encodeURIComponent(jobId)}/retry`, { method: "POST" }),
  retranslate: (jobId: string) => request<RunRecord>(`/jobs/${encodeURIComponent(jobId)}/retranslate`, { method: "POST" }),
  render: (jobId: string) => request<RunRecord>(`/jobs/${encodeURIComponent(jobId)}/render`, { method: "POST" }),
  approve: (jobId: string) => request<JobDetail>(`/jobs/${encodeURIComponent(jobId)}/approve`, { method: "POST" }),
  unapprove: (jobId: string) => request<JobDetail>(`/jobs/${encodeURIComponent(jobId)}/unapprove`, { method: "POST" }),
  run: (runId: string) => request<RunRecord>(`/runs/${encodeURIComponent(runId)}`),
  cancelRun: (runId: string) => request<{ ok: boolean }>(`/runs/${encodeURIComponent(runId)}/cancel`, { method: "POST" }),

  glossary: (jobId: string) => request<GlossaryResponseDto>(`/jobs/${encodeURIComponent(jobId)}/glossary`).then(normalizeGlossary),
  saveGlossary: (jobId: string, revision: string, document: GlossaryDocument) =>
    request<GlossaryResponseDto>(`/jobs/${encodeURIComponent(jobId)}/glossary`, {
      method: "PUT",
      body: JSON.stringify({ revision, document }),
    }).then(normalizeGlossary),

  subtitles: (jobId: string, language: TargetLanguage) =>
    request<SubtitleResponseDto>(`/jobs/${encodeURIComponent(jobId)}/subtitles/${language}`).then(normalizeSubtitles),
  saveOverrides: (jobId: string, language: TargetLanguage, revision: string, changes: OverrideChange[]) =>
    request<SubtitleResponseDto>(`/jobs/${encodeURIComponent(jobId)}/subtitle-overrides/${language}`, {
      method: "PUT",
      body: JSON.stringify({ revision, changes }),
    }).then(normalizeSubtitles),

  ttsVoices: (params: { search?: string; page?: number; page_size?: number } = {}) => {
    const query = new URLSearchParams();
    if (params.search) query.set("search", params.search);
    if (params.page) query.set("page", String(params.page));
    if (params.page_size) query.set("page_size", String(params.page_size));
    return request<TtsVoicePage>(`/tts/voices?${query}`);
  },
  tts: (jobId: string) =>
    request<TtsWorkspaceDto>(`/jobs/${encodeURIComponent(jobId)}/tts/vi`).then(normalizeTts),
  saveTtsSettings: (jobId: string, payload: TtsSettingsUpdateRequest) =>
    request<TtsWorkspaceDto>(`/jobs/${encodeURIComponent(jobId)}/tts/vi/settings`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }).then(normalizeTts),
  saveSpokenOverrides: (jobId: string, payload: SpokenOverrideUpdateRequest) =>
    request<TtsWorkspaceDto>(`/jobs/${encodeURIComponent(jobId)}/tts/vi/spoken-overrides`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }).then(normalizeTts),
  previewTts: (jobId: string, payload: TtsPreviewRequest) =>
    request<RunRecord>(`/jobs/${encodeURIComponent(jobId)}/tts/vi/preview`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  calibrateTts: (jobId: string) =>
    request<RunRecord>(`/jobs/${encodeURIComponent(jobId)}/tts/vi/calibrate`, { method: "POST" }),
  renderTts: (jobId: string) =>
    request<RunRecord>(`/jobs/${encodeURIComponent(jobId)}/tts/vi/render`, { method: "POST" }),
  approveTts: (jobId: string) =>
    request<TtsWorkspaceDto>(`/jobs/${encodeURIComponent(jobId)}/tts/vi/approve`, { method: "POST" }).then(normalizeTts),
  unapproveTts: (jobId: string) =>
    request<TtsWorkspaceDto>(`/jobs/${encodeURIComponent(jobId)}/tts/vi/unapprove`, { method: "POST" }).then(normalizeTts),

  fileRoots: () => request<FileRootsResponse | FileRoot[]>("/files/roots").then((data) => Array.isArray(data) ? data : data.roots),
  files: (path: string) => request<FileListing>(`/files?path=${encodeURIComponent(path)}`),
};

export const apiUrl = {
  media: (jobId: string) => `${API_BASE}/jobs/${encodeURIComponent(jobId)}/media`,
  artifact: (jobId: string, artifactId: string) =>
    `${API_BASE}/jobs/${encodeURIComponent(jobId)}/artifacts/${encodeURIComponent(artifactId)}`,
  events: (runId: string) => `${API_BASE}/runs/${encodeURIComponent(runId)}/events`,
};
