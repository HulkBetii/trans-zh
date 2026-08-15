import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, waitFor } from "@testing-library/react";
import { afterEach, vi } from "vitest";
import type { RunRecord } from "./types";
import { queryKeys, useRunEvents, useTtsRunEvents } from "./queries";

class EventSourceStub {
  static instance: EventSourceStub | null = null;

  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onerror: (() => void) | null = null;
  close = vi.fn();

  constructor(readonly url: string) {
    EventSourceStub.instance = this;
  }

  emit(payload: unknown) {
    this.onmessage?.(new MessageEvent("message", { data: JSON.stringify(payload) }));
  }
}

afterEach(() => {
  EventSourceStub.instance = null;
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

test("refreshes stage state during a run and subtitle state when the run finishes", async () => {
  vi.stubGlobal("EventSource", EventSourceStub);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(queryClient, "invalidateQueries").mockResolvedValue();
  render(
    <QueryClientProvider client={queryClient}>
      <RunEventsHarness />
    </QueryClientProvider>,
  );
  await waitFor(() => expect(EventSourceStub.instance).not.toBeNull());

  const running = runRecord({ status: "running", stage: "s1", event_seq: 1 });
  act(() => EventSourceStub.instance?.emit({ id: 1, type: "progress", run: running }));
  await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: queryKeys.job("job-1") }));

  invalidate.mockClear();
  const completed = runRecord({ status: "completed", stage: "s5", progress: 1, event_seq: 2 });
  act(() => EventSourceStub.instance?.emit({ id: 2, type: "completed", run: completed }));

  await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: queryKeys.subtitlesForJob("job-1") }));
  expect(invalidate).toHaveBeenCalledWith({ queryKey: queryKeys.job("job-1") });
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ["jobs"] });
});

test("keeps pipeline aliases isolated while refreshing TTS lane summaries", async () => {
  vi.stubGlobal("EventSource", EventSourceStub);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  queryClient.setQueryData(queryKeys.job("job-1"), { execution_status: "completed", active_run: null });
  queryClient.setQueryData(queryKeys.tts("job-1"), { execution_status: "not_started", active_run: null });
  const invalidate = vi.spyOn(queryClient, "invalidateQueries").mockResolvedValue();
  render(
    <QueryClientProvider client={queryClient}>
      <TtsRunEventsHarness />
    </QueryClientProvider>,
  );
  await waitFor(() => expect(EventSourceStub.instance).not.toBeNull());

  const running = runRecord({ lane: "tts", kind: "tts_render", from_stage: "tts_render", status: "running", stage: "tts_render", event_seq: 1 });
  act(() => EventSourceStub.instance?.emit({ id: 1, type: "progress", run: running }));

  await waitFor(() => expect(queryClient.getQueryData<{ active_run: RunRecord }>(queryKeys.tts("job-1"))?.active_run).toEqual(running));
  expect(queryClient.getQueryData(queryKeys.job("job-1"))).toEqual({ execution_status: "completed", active_run: null });

  invalidate.mockClear();
  const completed = { ...running, status: "completed" as const, progress: 1, event_seq: 2 };
  act(() => EventSourceStub.instance?.emit({ id: 2, type: "completed", run: completed }));

  await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: queryKeys.tts("job-1") }));
  expect(invalidate).toHaveBeenCalledWith({ queryKey: queryKeys.job("job-1") });
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ["jobs"] });
});

function RunEventsHarness() {
  useRunEvents("job-1", "run-1");
  return null;
}

function TtsRunEventsHarness() {
  useTtsRunEvents("job-1", "run-tts-1");
  return null;
}

function runRecord(patch: Partial<RunRecord>): RunRecord {
  return {
    run_id: "run-1",
    job_id: "job-1",
    lane: "pipeline",
    kind: "pipeline",
    from_stage: "s0",
    status: "queued",
    stage: null,
    progress: 0,
    message: "",
    event_seq: 0,
    ...patch,
  };
}
