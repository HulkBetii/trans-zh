import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, apiUrl } from "./client";
import type {
  CreateJobRequest,
  CredentialDeleteRequest,
  CredentialId,
  CredentialTestRequest,
  CredentialWriteRequest,
  ConnectionStatus,
  JobDetail,
  JobsPage,
  RunEvent,
  RunRecord,
  VoiceOwnership,
} from "./types";

export const queryKeys = {
  meta: ["meta"] as const,
  settings: ["settings"] as const,
  jobs: (filters: object = {}) => ["jobs", filters] as const,
  job: (jobId: string) => ["job", jobId] as const,
  glossary: (jobId: string) => ["glossary", jobId] as const,
  subtitlesForJob: (jobId: string) => ["subtitles", jobId] as const,
  subtitles: (jobId: string, language: string) => ["subtitles", jobId, language] as const,
  tts: (jobId: string) => ["tts", jobId, "vi"] as const,
  ttsVoices: (params: object = {}) => ["tts-voices", params] as const,
};

export function useMeta() {
  return useQuery({ queryKey: queryKeys.meta, queryFn: api.meta, staleTime: 60_000 });
}

export function useSettings() {
  return useQuery({ queryKey: queryKeys.settings, queryFn: api.settings, staleTime: 30_000 });
}

export function useSaveCredential() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ credentialId, ...payload }: CredentialWriteRequest & { credentialId: CredentialId }) =>
      api.saveCredential(credentialId, payload),
    onSuccess: (settings) => {
      queryClient.setQueryData(queryKeys.settings, settings);
      void queryClient.invalidateQueries({ queryKey: queryKeys.meta });
    },
  });
}

export function useDeleteCredential() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ credentialId, ...payload }: CredentialDeleteRequest & { credentialId: CredentialId }) =>
      api.deleteCredential(credentialId, payload),
    onSuccess: (settings) => {
      queryClient.setQueryData(queryKeys.settings, settings);
      void queryClient.invalidateQueries({ queryKey: queryKeys.meta });
    },
  });
}

export function useTestCredential() {
  return useMutation({
    mutationFn: ({ credentialId, ...payload }: CredentialTestRequest & { credentialId: CredentialId }) =>
      api.testCredential(credentialId, payload),
  });
}

export function useJobs(filters: { search?: string; execution?: string; quality?: string; lane?: "pipeline" | "tts" | "any"; page?: number }) {
  return useQuery({
    queryKey: queryKeys.jobs(filters),
    queryFn: () => api.jobs(filters),
    refetchInterval: 12_000,
  });
}

export function useJob(jobId: string) {
  return useQuery({ queryKey: queryKeys.job(jobId), queryFn: () => api.job(jobId), enabled: Boolean(jobId) });
}

export function useCreateJob() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: CreateJobRequest) => api.createJob(payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

export function useTts(jobId: string) {
  return useQuery({
    queryKey: queryKeys.tts(jobId),
    queryFn: () => api.tts(jobId),
    enabled: Boolean(jobId),
    refetchInterval: (query) => query.state.data?.active_run ? 4_000 : false,
  });
}

export function useTtsVoices(
  params: { search?: string; page?: number; page_size?: number; ownership?: VoiceOwnership },
  enabled = true,
) {
  return useQuery({
    queryKey: queryKeys.ttsVoices(params),
    queryFn: () => api.ttsVoices(params),
    enabled,
    staleTime: 5 * 60_000,
  });
}

export function useRunEvents(jobId: string, runId?: string | null) {
  return useRunStream(jobId, runId, "pipeline");
}

export function useTtsRunEvents(jobId: string, runId?: string | null) {
  return useRunStream(jobId, runId, "tts");
}

function useRunStream(jobId: string, runId: string | null | undefined, lane: "pipeline" | "tts") {
  const queryClient = useQueryClient();
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>("live");

  useEffect(() => {
    if (!runId || typeof EventSource === "undefined") {
      setConnectionStatus("live");
      return;
    }

    setConnectionStatus(navigator.onLine ? "connecting" : "offline");
    const source = new EventSource(apiUrl.events(runId));
    let lastStage: string | null | undefined;
    let lastStatus: string | undefined;
    const refreshJob = () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.job(jobId) });
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
    };
    const refreshAfterTerminalRun = () => {
      if (lane === "pipeline") {
        refreshJob();
        void queryClient.invalidateQueries({ queryKey: queryKeys.subtitlesForJob(jobId) });
      } else {
        void queryClient.invalidateQueries({ queryKey: queryKeys.tts(jobId) });
        refreshJob();
      }
    };
    const updateRunSnapshot = (run: RunRecord) => {
      queryClient.setQueryData(queryKeys.job(jobId), (current: JobDetail | undefined) => {
        if (!current) return current;
        const lanes = current.lanes?.map((summary) => summary.id === lane ? {
          ...summary,
          execution_status: run.status,
          stage: run.stage,
          progress: run.progress,
          message: run.message,
          error: run.error,
          active_run: run,
          latest_run: run,
        } : summary);
        return lane === "pipeline"
          ? { ...current, ...(lanes ? { lanes } : {}), active_run: run, execution_status: run.status, stage: run.stage, progress: run.progress, message: run.message, error: run.error }
          : { ...current, ...(lanes ? { lanes } : {}) };
      });
      queryClient.setQueriesData({ queryKey: ["jobs"] }, (current: JobsPage | undefined) => {
        if (!current?.items) return current;
        return {
          ...current,
          items: current.items.map((item) => {
            if (item.job_id !== jobId) return item;
            const lanes = item.lanes?.map((summary) => summary.id === lane ? {
              ...summary,
              execution_status: run.status,
              stage: run.stage,
              progress: run.progress,
              message: run.message,
              error: run.error,
              active_run: run,
              latest_run: run,
            } : summary);
            return lane === "pipeline"
              ? { ...item, ...(lanes ? { lanes } : {}), active_run: run, execution_status: run.status, stage: run.stage, progress: run.progress, message: run.message, error: run.error }
              : { ...item, ...(lanes ? { lanes } : {}) };
          }),
        };
      });
    };
    source.onopen = () => {
      setConnectionStatus("live");
      refreshJob();
      if (lane === "tts") {
        void queryClient.invalidateQueries({ queryKey: queryKeys.tts(jobId) });
      }
    };
    source.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data) as RunEvent;
        const run = payload.run;
        if (run) updateRunSnapshot(run);
        if (lane === "pipeline") {
          queryClient.setQueryData(queryKeys.job(jobId), (current: unknown) => {
            if (!current || !run) return current;
            return { ...(current as object), active_run: run, execution_status: run.status };
          });
        } else if (run) {
          queryClient.setQueryData(queryKeys.tts(jobId), (current: unknown) => {
            if (!current) return current;
            return { ...(current as object), active_run: run, execution_status: run.status };
          });
        }
        const stageChanged = run
          && (run.stage !== lastStage || run.status !== lastStatus);
        if (run) {
          lastStage = run.stage;
          lastStatus = run.status;
        }
        const terminal = payload.type === "completed"
          || payload.type === "error"
          || (run && ["completed", "cancelled", "failed", "interrupted"].includes(run.status));
        if (terminal) refreshAfterTerminalRun();
        else if (stageChanged) {
          if (lane === "pipeline") refreshJob();
          else void queryClient.invalidateQueries({ queryKey: queryKeys.tts(jobId) });
        }
      } catch {
        if (lane === "pipeline") refreshJob();
        else void queryClient.invalidateQueries({ queryKey: queryKeys.tts(jobId) });
      }
    };
    source.onerror = () => {
      setConnectionStatus(navigator.onLine ? "reconnecting" : "offline");
      if (lane === "pipeline") refreshJob();
      else void queryClient.invalidateQueries({ queryKey: queryKeys.tts(jobId) });
    };
    const handleOnline = () => {
      setConnectionStatus("reconnecting");
      refreshJob();
      if (lane === "tts") void queryClient.invalidateQueries({ queryKey: queryKeys.tts(jobId) });
    };
    const handleOffline = () => setConnectionStatus("offline");
    window.addEventListener("online", handleOnline);
    window.addEventListener("offline", handleOffline);
    return () => {
      source.close();
      window.removeEventListener("online", handleOnline);
      window.removeEventListener("offline", handleOffline);
    };
  }, [jobId, lane, queryClient, runId]);

  return connectionStatus;
}
