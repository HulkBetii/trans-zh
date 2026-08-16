import type { components } from "./schema";

type Schemas = components["schemas"];

export type ExecutionStatus = Schemas["ExecutionStatus"];
export type QualityStatus = Schemas["QualityStatus"];
export type TargetLanguage = NonNullable<Schemas["CreateJobRequest"]["targets"]>[number];
export type OutputFormat = NonNullable<Schemas["CreateJobRequest"]["formats"]>[number];
export type AllowedAction = Schemas["JobDetail"]["allowed_actions"][number];
export type StageStatus = "pending" | "queued" | "running" | "completed" | "failed" | "skipped" | "degraded";

export type StageDescriptor = Schemas["StageDescriptor"];
export type ReadinessItem = Schemas["ReadinessItem"];
export type StudioMeta = Schemas["MetaResponse"];
export type JobSource = Schemas["SourceRequest"];
export type JobRequestSnapshot = Schemas["JobRequestSnapshot"];
export type JobHealth = Schemas["HealthResponse"];
export type StageState = Schemas["StageState"];
export type RunRecord = Schemas["RunResponse"] & { lane?: "pipeline" | "tts" };
export type Artifact = Schemas["ArtifactResponse"];
export type JobLaneSummary = Omit<Schemas["JobLaneSummary"], "active_run" | "latest_run"> & {
  active_run?: RunRecord | null;
  latest_run?: RunRecord | null;
};
export type JobListItem = Omit<Schemas["JobSummary"], "active_run" | "lanes"> & {
  active_run?: RunRecord | null;
  lanes?: JobLaneSummary[];
};
export type JobsPage = Omit<Schemas["JobPage"], "items"> & { items: JobListItem[] };
export type JobDetail = Omit<Schemas["JobDetail"], "active_run" | "lanes"> & {
  active_run?: RunRecord | null;
  lanes?: JobLaneSummary[];
};
export type CreateJobRequest = Schemas["CreateJobRequest"];
export type CreateJobResponse = Schemas["CreateJobResponse"];

export type GlossaryTerm = Schemas["GlossaryTerm"];
export type AddressTerm = Schemas["AddressTerm"];
export type StyleDecision = Schemas["StyleDecision"];
export type GlossaryDocumentDto = Schemas["GlossaryDoc"];
export type GlossaryResponseDto = Schemas["GlossaryResponse"];
export type GlossaryUpdateRequest = Schemas["GlossaryUpdateRequest"];

export type GlossaryDocument = Omit<GlossaryDocumentDto, "terms" | "address_terms" | "style"> & {
  terms: GlossaryTerm[];
  address_terms: AddressTerm[];
  style: StyleDecision;
};

export interface RevisionedGlossary {
  revision: string;
  document: GlossaryDocument;
  editor_locked: boolean;
  lock_reason?: string | null;
}

export type SubtitleWarning = Schemas["SubtitleWarningResponse"];
export type SubtitleCueDto = Schemas["SubtitleCue"];
export type SubtitleResponseDto = Schemas["SubtitleResponse"];
export type OverrideChange = Schemas["OverrideChange"];
export type OverrideUpdateRequest = Schemas["OverrideUpdateRequest"];

export type SubtitleCue = Omit<SubtitleCueDto, "warnings"> & {
  warnings: SubtitleWarning[];
};

export type SubtitleWorkspace = Omit<SubtitleResponseDto, "cues"> & {
  cues: SubtitleCue[];
};

export type TtsWorkspaceDto = Schemas["TtsWorkspaceResponse"];
export type TtsExecutionStatus = TtsWorkspaceDto["execution_status"];
export type TtsAllowedAction = NonNullable<TtsWorkspaceDto["allowed_actions"]>[number];
export type TtsOverrideState = Schemas["TtsCueResponse"]["override_state"];
export type TtsPreviewState = Schemas["TtsCueResponse"]["preview_state"];
export type TtsVoice = Schemas["TtsVoiceResponse"];
export type TtsVoicePage = Schemas["TtsVoicePage"];
/** Nguồn giọng vbee. Chính hãng chỉ có 25 giọng; cả thư viện là 1268. */
export type VoiceOwnership = "all" | "vbee" | "community";
export type VoiceCalibration = Schemas["VoiceCalibrationResponse"];
export type TtsCue = Schemas["TtsCueResponse"];
export type TtsWorkspace = Omit<TtsWorkspaceDto, "latest_run" | "active_run" | "attention_reasons" | "allowed_actions" | "cues"> & {
  latest_run?: RunRecord | null;
  active_run?: RunRecord | null;
  attention_reasons: string[];
  allowed_actions: TtsAllowedAction[];
  cues: TtsCue[];
};
export type TtsSettingsUpdateRequest = Schemas["TtsSettingsUpdateRequest"];
export type SpokenOverrideUpdateRequest = Schemas["SpokenOverrideUpdateRequest"];
export type TtsPreviewRequest = Schemas["TtsPreviewRequest"];

export type CredentialId = Schemas["CredentialId"];
export type SettingsCredential = Schemas["SettingsCredentialResponse"];
export type SettingsProvider = Schemas["SettingsProviderResponse"];
export type SettingsResponse = Schemas["SettingsResponse"];
export type CredentialWriteRequest = Schemas["CredentialUpdateRequest"];
export type CredentialDeleteRequest = Schemas["SettingsRevisionRequest"];
export type CredentialTestRequest = Schemas["CredentialTestRequest"];
export type CredentialTestResponse = Schemas["CredentialTestResponse"];

export type FileEntry = Schemas["FileEntryResponse"];
export type FileRoot = Schemas["FileRootResponse"];
export type FileRootsResponse = Schemas["FileRootsResponse"];
export type FileListing = Schemas["FileListResponse"];

export interface RunEvent {
  id?: number;
  type?: "snapshot" | "progress" | "completed" | "error";
  run?: RunRecord;
  stage?: string;
  progress?: number;
  message?: string;
}

export type ConnectionStatus = "connecting" | "live" | "reconnecting" | "offline";
