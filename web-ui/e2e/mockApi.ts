import type { Page, Route } from "@playwright/test";

type SourceKind = "local" | "url";

interface SourcePayload {
  kind: SourceKind;
  value: string;
}

interface CreateJobPayload {
  source: SourcePayload;
  targets: string[];
  formats: string[];
  bilingual: boolean;
}

interface GlossaryDocument {
  schema: number;
  version: number;
  terms: Array<Record<string, unknown>>;
  address_terms: Array<Record<string, unknown>>;
  style: {
    speech_register: string;
    narrator_self_vi: string;
    audience_vi: string;
    subject_third_person_vi: string;
  };
}

interface GlossarySavePayload {
  revision: string;
  document: GlossaryDocument;
}

interface OverrideChangePayload {
  segment_id: number;
  text: string | null;
}

interface OverrideSavePayload {
  revision: string;
  changes: OverrideChangePayload[];
}

interface TtsSettingsSavePayload {
  revision: string;
  voice_id: string;
}

type CredentialId = "openai" | "anthropic" | "ai33";

interface CredentialWritePayload {
  revision: string;
  secret: string;
}

interface CredentialDeletePayload {
  revision: string;
}

interface CredentialTestPayload {
  secret?: string;
}

interface CredentialRequest<T> {
  credential_id: CredentialId;
  payload: T;
}

interface SubtitleCueFixture {
  segment_id: number;
  start: number;
  end: number;
  source_text: string;
  model_text: string;
  effective_text: string;
  overridden: boolean;
  stale: boolean;
  base_changed: boolean;
  override_state: "none" | "manual" | "base_changed" | "stale";
  warnings: Array<Record<string, unknown>>;
  reviewed: boolean;
  cps: number;
  line_count: number;
}

export interface MockApiRequests {
  createJob: CreateJobPayload | null;
  glossarySaves: GlossarySavePayload[];
  retranslateCalls: number;
  overrideSaves: OverrideSavePayload[];
  renderCalls: number;
  artifactDownloads: number;
  ttsSettingsSaves: TtsSettingsSavePayload[];
  credentialSaves: Array<CredentialRequest<CredentialWritePayload>>;
  credentialDeletes: Array<CredentialRequest<CredentialDeletePayload>>;
  credentialTests: Array<CredentialRequest<CredentialTestPayload>>;
}

export interface MockApiController {
  requests: MockApiRequests;
}

interface MockApiOptions {
  initialJob?: boolean;
  ttsEnabled?: boolean;
}

interface MockApiState {
  jobCreated: boolean;
  source: SourcePayload;
  targets: string[];
  formats: string[];
  bilingual: boolean;
  title: string;
  executionStatus: "queued" | "completed";
  activeRun: ReturnType<typeof queuedRun> | null;
  glossaryRevision: string;
  glossaryStale: boolean;
  glossary: GlossaryDocument;
  subtitleRevision: string;
  outputStale: boolean;
  cues: SubtitleCueFixture[];
  artifactReady: boolean;
  ttsRevision: string;
  ttsVoiceId: string | null;
  ttsCuesReady: boolean;
  settingsRevision: string;
  configuredCredentials: Record<CredentialId, boolean>;
}

const jobId = "job-001";
const runId = "run-001";
const createdAt = "2026-08-15T00:00:00Z";

const stageDescriptors = [
  { id: "s0", label: "Ingest", short_label: "S0", description: "Chuẩn bị nguồn media" },
  { id: "s1", label: "ASR", short_label: "S1", description: "Nhận dạng giọng nói" },
  { id: "s2", label: "Segment", short_label: "S2", description: "Ngắt cue" },
  { id: "s3", label: "Glossary", short_label: "S3", description: "Khóa thuật ngữ" },
  { id: "s4", label: "Translate", short_label: "S4", description: "Dịch nội dung" },
  { id: "s5", label: "Render", short_label: "S5", description: "Xuất phụ đề" },
];

export async function installMockApi(page: Page, options: MockApiOptions = {}): Promise<MockApiController> {
  const requests: MockApiRequests = {
    createJob: null,
    glossarySaves: [],
    retranslateCalls: 0,
    overrideSaves: [],
    renderCalls: 0,
    artifactDownloads: 0,
    ttsSettingsSaves: [],
    credentialSaves: [],
    credentialDeletes: [],
    credentialTests: [],
  };
  const state: MockApiState = {
    jobCreated: options.initialJob ?? true,
    source: { kind: "local", value: "D:\\Media\\episode-01.mp4" },
    targets: ["vi"],
    formats: ["srt"],
    bilingual: false,
    title: "episode-01.mp4",
    executionStatus: (options.initialJob ?? true) ? "completed" : "queued",
    activeRun: (options.initialJob ?? true) ? null : queuedRun(),
    glossaryRevision: "glossary-rev-1",
    glossaryStale: false,
    glossary: defaultGlossary(),
    subtitleRevision: "subtitle-rev-1",
    outputStale: false,
    cues: defaultCues(),
    artifactReady: false,
    ttsRevision: "tts-rev-1",
    ttsVoiceId: null,
    ttsCuesReady: false,
    settingsRevision: "settings-rev-1",
    configuredCredentials: { openai: false, anthropic: false, ai33: false },
  };

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const method = request.method();
    const path = new URL(request.url()).pathname;

    if (method === "GET" && path === "/api/v1/meta") {
      const ttsReady = state.configuredCredentials.ai33;
      await fulfillJson(route, {
        stages: stageDescriptors,
        targets: ["vi", "en"],
        formats: ["srt", "ass"],
        capabilities: options.ttsEnabled
          ? ["core_studio", "settings", "tts"]
          : ["core_studio", "settings"],
        readiness: [
          { id: "ffmpeg", label: "FFmpeg", status: "ready", detail: "Sẵn sàng" },
          { id: "asr", label: "ASR runtime", status: "ready", detail: "Sẵn sàng" },
          {
            id: "tts_ai33",
            label: "AI33 / Vbee TTS",
            status: ttsReady ? "ready" : "warning",
            detail: ttsReady ? "Đã cấu hình qua kho bảo mật." : "Chưa có khóa AI33.",
          },
        ],
      });
      return;
    }

    if (method === "GET" && path === "/api/v1/settings") {
      await fulfillJson(route, settingsResponse(state));
      return;
    }

    const credentialMatch = path.match(/^\/api\/v1\/settings\/credentials\/(openai|anthropic|ai33)$/);
    const credentialTestMatch = path.match(/^\/api\/v1\/settings\/credentials\/(openai|anthropic|ai33)\/test$/);

    if (method === "POST" && credentialTestMatch) {
      const credentialId = credentialTestMatch[1] as CredentialId;
      const payload = request.postDataJSON() as CredentialTestPayload;
      requests.credentialTests.push({ credential_id: credentialId, payload });
      await fulfillJson(route, {
        ok: true,
        code: "ok",
        message: "Kết nối thành công.",
        latency_ms: 37,
      });
      return;
    }

    if (method === "PUT" && credentialMatch) {
      const credentialId = credentialMatch[1] as CredentialId;
      const payload = request.postDataJSON() as CredentialWritePayload;
      requests.credentialSaves.push({ credential_id: credentialId, payload });
      state.configuredCredentials[credentialId] = true;
      state.settingsRevision = `settings-rev-${requests.credentialSaves.length + requests.credentialDeletes.length + 1}`;
      await fulfillJson(route, settingsResponse(state));
      return;
    }

    if (method === "DELETE" && credentialMatch) {
      const credentialId = credentialMatch[1] as CredentialId;
      const payload = request.postDataJSON() as CredentialDeletePayload;
      requests.credentialDeletes.push({ credential_id: credentialId, payload });
      state.configuredCredentials[credentialId] = false;
      state.settingsRevision = `settings-rev-${requests.credentialSaves.length + requests.credentialDeletes.length + 1}`;
      await fulfillJson(route, settingsResponse(state));
      return;
    }

    if (method === "GET" && path === "/api/v1/jobs") {
      const items = state.jobCreated ? [jobSummary(state)] : [];
      await fulfillJson(route, { items, page: 1, page_size: 25, total: items.length });
      return;
    }

    if (method === "POST" && path === "/api/v1/jobs") {
      const payload = request.postDataJSON() as CreateJobPayload;
      requests.createJob = payload;
      state.jobCreated = true;
      state.source = payload.source;
      state.targets = payload.targets;
      state.formats = payload.formats;
      state.bilingual = payload.bilingual;
      state.title = payload.source.value.split(/[\\/]/).at(-1) || payload.source.value;
      state.executionStatus = "queued";
      state.activeRun = queuedRun();
      await fulfillJson(route, { job_id: jobId, run_id: runId }, 201);
      return;
    }

    if (method === "GET" && path === `/api/v1/jobs/${jobId}`) {
      await fulfillJson(route, jobDetail(state));
      return;
    }

    if (method === "GET" && path === `/api/v1/jobs/${jobId}/glossary`) {
      await fulfillJson(route, { revision: state.glossaryRevision, document: state.glossary });
      return;
    }

    if (method === "PUT" && path === `/api/v1/jobs/${jobId}/glossary`) {
      const payload = request.postDataJSON() as GlossarySavePayload;
      requests.glossarySaves.push(payload);
      state.glossary = structuredClone(payload.document);
      state.glossaryRevision = `glossary-rev-${requests.glossarySaves.length + 1}`;
      state.glossaryStale = true;
      await fulfillJson(route, { revision: state.glossaryRevision, document: state.glossary });
      return;
    }

    if (method === "POST" && path === `/api/v1/jobs/${jobId}/retranslate`) {
      requests.retranslateCalls += 1;
      state.glossaryStale = false;
      await fulfillJson(route, actionRun("retranslate", "s4"), 202);
      return;
    }

    if (method === "GET" && path === `/api/v1/jobs/${jobId}/subtitles/vi`) {
      await fulfillJson(route, subtitleResponse(state));
      return;
    }

    if (method === "GET" && path === "/api/v1/tts/voices") {
      await fulfillJson(route, {
        items: [ttsVoice()],
        page: 1,
        page_size: 50,
        total: 1,
        has_more: false,
        credits: 24,
      });
      return;
    }

    if (method === "GET" && path === `/api/v1/jobs/${jobId}/tts/vi`) {
      await fulfillJson(route, ttsWorkspace(state));
      return;
    }

    if (method === "PUT" && path === `/api/v1/jobs/${jobId}/tts/vi/settings`) {
      const payload = request.postDataJSON() as TtsSettingsSavePayload;
      requests.ttsSettingsSaves.push(payload);
      state.ttsRevision = `tts-rev-${requests.ttsSettingsSaves.length + 1}`;
      state.ttsVoiceId = payload.voice_id;
      state.ttsCuesReady = true;
      await fulfillJson(route, ttsWorkspace(state));
      return;
    }

    if (method === "PUT" && path === `/api/v1/jobs/${jobId}/subtitle-overrides/vi`) {
      const payload = request.postDataJSON() as OverrideSavePayload;
      requests.overrideSaves.push(payload);
      state.cues = state.cues.map((cue) => {
        const change = payload.changes.find((item) => item.segment_id === cue.segment_id);
        if (!change) return cue;
        if (change.text === null) {
          return { ...cue, effective_text: cue.model_text, overridden: false, override_state: "none" as const };
        }
        return { ...cue, effective_text: change.text, overridden: true, override_state: "manual" as const };
      });
      state.subtitleRevision = `subtitle-rev-${requests.overrideSaves.length + 1}`;
      state.outputStale = true;
      await fulfillJson(route, subtitleResponse(state));
      return;
    }

    if (method === "POST" && path === `/api/v1/jobs/${jobId}/render`) {
      requests.renderCalls += 1;
      state.artifactReady = true;
      state.outputStale = false;
      await fulfillJson(route, actionRun("render", "s5"), 202);
      return;
    }

    if (method === "GET" && path === `/api/v1/jobs/${jobId}/media`) {
      await route.fulfill({ status: 204 });
      return;
    }

    if (method === "GET" && path === `/api/v1/jobs/${jobId}/artifacts/subtitle-vi-srt`) {
      requests.artifactDownloads += 1;
      await route.fulfill({
        status: 200,
        contentType: "application/x-subrip",
        headers: { "Content-Disposition": 'attachment; filename="episode-01.vi.srt"' },
        body: "1\n00:00:00,000 --> 00:00:02,400\nBan phu de da bien tap.\n",
      });
      return;
    }

    if (method === "GET" && path === `/api/v1/runs/${runId}/events`) {
      await route.fulfill({ status: 204 });
      return;
    }

    await fulfillJson(route, { detail: `Unhandled mock route: ${method} ${path}` }, 404);
  });

  return { requests };
}

function defaultGlossary(): GlossaryDocument {
  return {
    schema: 1,
    version: 1,
    terms: [],
    address_terms: [],
    style: {
      speech_register: "Tự nhiên",
      narrator_self_vi: "tôi",
      audience_vi: "các bạn",
      subject_third_person_vi: "anh ấy",
    },
  };
}

function defaultCues(): SubtitleCueFixture[] {
  return [
    {
      segment_id: 1,
      start: 0,
      end: 2.4,
      source_text: "欢迎来到今天的节目。",
      model_text: "Chào mừng đến với chương trình hôm nay.",
      effective_text: "Chào mừng đến với chương trình hôm nay.",
      overridden: false,
      stale: false,
      base_changed: false,
      override_state: "none",
      warnings: [],
      reviewed: false,
      cps: 17.1,
      line_count: 1,
    },
    {
      segment_id: 2,
      start: 2.4,
      end: 5.8,
      source_text: "我们会逐句检查字幕。",
      model_text: "Chúng ta sẽ kiểm tra từng câu phụ đề.",
      effective_text: "Chúng ta sẽ kiểm tra từng câu phụ đề.",
      overridden: false,
      stale: false,
      base_changed: false,
      override_state: "none",
      warnings: [{ kind: "cps_over", detail: "Nhịp đọc hơi nhanh.", value: 23, limit: 20 }],
      reviewed: false,
      cps: 23,
      line_count: 1,
    },
  ];
}

function queuedRun() {
  return {
    run_id: runId,
    job_id: jobId,
    kind: "pipeline",
    from_stage: "s0",
    status: "queued",
    stage: "s0",
    progress: 0,
    message: "Đang chờ GPU",
    error: null,
    queue_position: 1,
    created_at: createdAt,
    started_at: null,
    finished_at: null,
    event_seq: 1,
  };
}

function actionRun(kind: string, fromStage: string) {
  return {
    run_id: `${runId}-${kind}`,
    job_id: jobId,
    kind,
    from_stage: fromStage,
    status: "queued",
    stage: fromStage,
    progress: 0,
    message: "Đã đưa vào hàng chờ",
    error: null,
    queue_position: 1,
    created_at: createdAt,
    started_at: null,
    finished_at: null,
    event_seq: 1,
  };
}

function jobSummary(state: MockApiState) {
  return {
    job_id: jobId,
    title: state.title,
    source: state.source,
    targets: state.targets,
    execution_status: state.executionStatus,
    quality_status: "needs_review",
    stage: state.executionStatus === "completed" ? "s5" : "s0",
    progress: state.executionStatus === "completed" ? 1 : 0,
    message: state.activeRun?.message ?? "Hoàn tất — cần rà",
    error: null,
    attention_reasons: state.executionStatus === "completed" ? ["render_warnings"] : [],
    active_run: state.activeRun,
    created_at: createdAt,
    updated_at: createdAt,
  };
}

function jobDetail(state: MockApiState) {
  const summary = jobSummary(state);
  return {
    ...summary,
    request: {
      source: state.source,
      targets: state.targets,
      formats: state.formats,
      bilingual: state.bilingual,
      output_dir: `D:\\Output\\${jobId}`,
    },
    stages: stageDescriptors.map((stage, index) => ({
      id: stage.id,
      status: state.executionStatus === "completed" ? "completed" : index === 0 ? "queued" : "pending",
      started_at: state.executionStatus === "completed" ? createdAt : null,
      finished_at: state.executionStatus === "completed" ? createdAt : null,
      duration_seconds: state.executionStatus === "completed" ? index + 0.5 : null,
      message: state.executionStatus === "completed" ? "Đã hoàn tất" : index === 0 ? "Đang chờ tài nguyên" : "Chưa chạy",
      error: null,
      cache_hit: index < 2 && state.executionStatus === "completed",
    })),
    health: {
      method: "semantic",
      subject_pronoun: "anh ấy",
      cps_warnings: 1,
      address_terms: 0,
      segments: 2,
      terms: 0,
    },
    attention_reasons: state.outputStale ? ["stale_outputs"] : summary.attention_reasons,
    artifacts: state.artifactReady ? [artifact(state.outputStale)] : [],
    allowed_actions: state.executionStatus === "completed"
      ? ["retranslate", "render", "approve"]
      : ["cancel"],
    glossary_stale: state.glossaryStale,
  };
}

function subtitleResponse(state: MockApiState) {
  return {
    revision: state.subtitleRevision,
    language: "vi",
    cues: state.cues,
    editor_locked: false,
    output_stale: state.outputStale,
  };
}

function settingsResponse(state: MockApiState) {
  const credential = (id: CredentialId, label: string, envName: string) => {
    const configured = state.configuredCredentials[id];
    return {
      id,
      label,
      configured,
      source: configured ? "credential_store" : "none",
      editable: true,
      masked_value: configured ? "********" : null,
      env_name: envName,
    };
  };
  return {
    revision: state.settingsRevision,
    credential_store_available: true,
    credentials: [
      credential("openai", "OpenAI", "OPENAI_API_KEY"),
      credential("anthropic", "Anthropic", "ANTHROPIC_API_KEY"),
      credential("ai33", "AI33 / Vbee", "AI33_API_KEY"),
    ],
    providers: [
      {
        id: "segment",
        label: "Phân đoạn (S2)",
        provider: "openai",
        model: "gpt-segment",
        base_url: "https://api.openai.com/v1",
        credential_id: "openai",
      },
      {
        id: "translate",
        label: "Dịch phụ đề (S4)",
        provider: "anthropic",
        model: "claude-translate",
        base_url: "https://api.anthropic.com",
        credential_id: "anthropic",
      },
      {
        id: "tts",
        label: "Lồng tiếng (TTS)",
        provider: "ai33_vbee",
        model: null,
        base_url: "https://api.ai33.pro",
        credential_id: "ai33",
      },
    ],
  };
}

function ttsVoice() {
  return {
    voice_id: "vbee-voice-minh-quan",
    name: "Minh Quân",
    description: "Giọng nam miền Bắc, rõ và điềm tĩnh.",
    locale: "vi-VN",
    gender: "male",
    age: "adult",
    category: "narration",
    tier: "standard",
    preview_url: null,
    avatar_url: null,
    calibrated: false,
  };
}

function ttsWorkspace(state: MockApiState) {
  const voice = state.ttsVoiceId === ttsVoice().voice_id ? ttsVoice() : null;
  const cues = state.ttsCuesReady
    ? [
        {
          segment_id: 1,
          start: 0,
          end: 2.4,
          subtitle_text: "Chào mừng đến với chương trình hôm nay.",
          default_spoken_text: "Chào mừng đến với chương trình hôm nay.",
          effective_spoken_text: "Chào mừng đến với chương trình hôm nay.",
          override_state: "none",
          room_seconds: 2.65,
          predicted_duration: 2.1,
          overflow_seconds: 0,
          speed: 1,
          preview_state: "missing",
          preview_url: null,
        },
        {
          segment_id: 2,
          start: 2.4,
          end: 5.8,
          subtitle_text: "Chúng ta sẽ kiểm tra từng câu phụ đề.",
          default_spoken_text: "Chúng ta sẽ kiểm tra từng câu phụ đề.",
          effective_spoken_text: "Chúng ta sẽ kiểm tra từng câu phụ đề.",
          override_state: "none",
          room_seconds: 3.4,
          predicted_duration: 3.1,
          overflow_seconds: 0,
          speed: 1,
          preview_state: "missing",
          preview_url: null,
        },
      ]
    : [];
  return {
    revision: state.ttsRevision,
    language: "vi",
    provider: "vbee",
    provider_ready: true,
    voice_id: state.ttsVoiceId,
    selected_voice: voice,
    calibration: null,
    subtitle_approved: false,
    execution_status: "not_started",
    quality_status: "needs_review",
    latest_run: null,
    active_run: null,
    attention_reasons: [],
    allowed_actions: state.ttsVoiceId ? ["preview", "calibrate"] : [],
    cues,
    total_cues: cues.length,
    uncached_cues: cues.length,
    output: null,
    output_stale: false,
    editor_locked: false,
  };
}

function artifact(stale: boolean) {
  return {
    artifact_id: "subtitle-vi-srt",
    name: "episode-01.vi.srt",
    kind: "subtitle",
    language: "vi",
    format: "srt",
    size_bytes: 72,
    created_at: createdAt,
    state: stale ? "stale" : "current",
    download_url: "/e2e/fixtures/episode-01.vi.srt",
  };
}

async function fulfillJson(route: Route, body: unknown, status = 200): Promise<void> {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}
