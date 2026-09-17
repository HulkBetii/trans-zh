import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, vi } from "vitest";
import { App } from "../App";
import { jsonResponse, renderApp } from "../test/render";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

test("shows and executes only quality actions allowed by the backend", async () => {
  const detail = {
    job_id: "job-1",
    title: "Episode 01",
    source: { kind: "local", value: "D:\\episode.mp4" },
    targets: ["vi"],
    execution_status: "completed",
    quality_status: "needs_review",
    request: { source: { kind: "local", value: "D:\\episode.mp4" }, targets: ["vi"], formats: ["srt"], bilingual: false, output_dir: "output/job-1" },
    stages: [{ id: "s0", status: "completed" }],
    artifacts: [],
    attention_reasons: [],
    allowed_actions: ["render", "approve"],
  };
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/meta")) return jsonResponse({ stages: [{ id: "s0", label: "Ingest" }], targets: ["vi"], formats: ["srt"], capabilities: [], readiness: [] });
    if (url.includes("/jobs?") && !init?.method) return jsonResponse({ items: [], total: 0, page: 1, page_size: 20 });
    if (url.endsWith("/approve") && init?.method === "POST") return jsonResponse({ ...detail, quality_status: "approved", allowed_actions: ["unapprove"] });
    if (url.endsWith("/jobs/job-1")) return jsonResponse(detail);
    return jsonResponse({ detail: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  renderApp(<App />, "/jobs/job-1/pipeline");

  const approve = await screen.findByRole("button", { name: "Duyệt phụ đề" });
  expect(screen.queryByRole("button", { name: "Bỏ duyệt phụ đề" })).not.toBeInTheDocument();
  await user.click(approve);
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/v1/jobs/job-1/approve", expect.objectContaining({ method: "POST" })));
});

test("shows the dubbing workspace only when the backend capability and Vietnamese target are present", async () => {
  const detail = {
    job_id: "job-tts",
    title: "Episode TTS",
    source: { kind: "local", value: "D:\\episode.mp4" },
    targets: ["vi"],
    execution_status: "completed",
    quality_status: "approved",
    request: { source: { kind: "local", value: "D:\\episode.mp4" }, targets: ["vi"], formats: ["srt"], bilingual: false, output_dir: "output/job-tts" },
    stages: [],
    artifacts: [],
    attention_reasons: [],
    allowed_actions: [],
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/meta")) return jsonResponse({ stages: [], targets: ["vi"], formats: ["srt"], capabilities: ["tts"], readiness: [] });
    if (url.includes("/jobs?")) return jsonResponse({ items: [], total: 0, page: 1, page_size: 20 });
    if (url.endsWith("/jobs/job-tts")) return jsonResponse(detail);
    return jsonResponse({ detail: "not found" }, 404);
  }));

  renderApp(<App />, "/jobs/job-tts/pipeline");

  const link = await screen.findByRole("link", { name: "Lồng tiếng" });
  expect(link).toHaveAttribute("href", "/jobs/job-tts/tts");
  expect(screen.getByText("Đã duyệt")).toBeInTheDocument();
});

test("shows the dubbing workspace when English target is present", async () => {
  const detail = {
    job_id: "job-tts-en",
    title: "Episode TTS EN",
    source: { kind: "local", value: "D:\\episode.mp4" },
    targets: ["en"],
    execution_status: "completed",
    quality_status: "approved",
    request: { source: { kind: "local", value: "D:\\episode.mp4" }, targets: ["en"], formats: ["srt"], bilingual: false, output_dir: "output/job-tts-en" },
    stages: [],
    artifacts: [],
    attention_reasons: [],
    allowed_actions: [],
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/meta")) return jsonResponse({ stages: [], targets: ["vi", "en"], formats: ["srt"], capabilities: ["tts"], readiness: [] });
    if (url.includes("/jobs?")) return jsonResponse({ items: [], total: 0, page: 1, page_size: 20 });
    if (url.endsWith("/jobs/job-tts-en")) return jsonResponse(detail);
    return jsonResponse({ detail: "not found" }, 404);
  }));

  renderApp(<App />, "/jobs/job-tts-en/pipeline");

  const link = await screen.findByRole("link", { name: "Lồng tiếng" });
  expect(link).toHaveAttribute("href", "/jobs/job-tts-en/tts");
});

test("queues a cache-only glossary update and shows it immediately", async () => {
  const detail = retranslateJobDetail();
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/meta")) return jsonResponse({ stages: [], targets: ["vi"], formats: ["srt"], capabilities: [], readiness: [] });
    if (url.includes("/jobs?") && !init?.method) return jsonResponse({ items: [], total: 0, page: 1, page_size: 20 });
    if (url.endsWith("/retranslate/estimate")) return jsonResponse(translationSummary("reuse", 2, 0, 2));
    if (url.endsWith("/retranslate") && init?.method === "POST") return jsonResponse(queuedRetranslateRun("reuse", 2, 0));
    if (url.endsWith("/jobs/job-retranslate")) return jsonResponse(detail);
    return jsonResponse({ detail: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  renderApp(<App />, "/jobs/job-retranslate/pipeline");

  await user.click(await screen.findByRole("button", { name: "Cập nhật theo Glossary" }));

  expect(await screen.findByText("Cập nhật theo Glossary", { selector: ".pipeline-run-heading strong" })).toBeInTheDocument();
  expect(screen.getByText("0 GPT")).toBeInTheDocument();
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  const retranslateCall = fetchMock.mock.calls.find(([input]) => String(input).endsWith("/retranslate"));
  expect(JSON.parse(String(retranslateCall?.[1]?.body))).toEqual({ cache_mode: "reuse", confirmed_gpt_units: 0 });
});

test("confirms fresh GPT units and keeps a terminal retranslation banner", async () => {
  const detail = retranslateJobDetail({
    latestRun: {
      ...queuedRetranslateRun("reuse", 2, 0),
      status: "completed",
      progress: 1,
      message: "Cập nhật theo Glossary hoàn tất: 2 từ cache, 0 bằng GPT.",
      finished_at: "2026-08-27T01:00:01Z",
      translation_summary: { ...translationSummary("reuse", 2, 0, 2), estimated: false },
    },
  });
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/meta")) return jsonResponse({ stages: [], targets: ["vi"], formats: ["srt"], capabilities: [], readiness: [] });
    if (url.includes("/jobs?") && !init?.method) return jsonResponse({ items: [], total: 0, page: 1, page_size: 20 });
    if (url.endsWith("/retranslate/estimate")) return jsonResponse(translationSummary("bypass", 0, 2, 2));
    if (url.endsWith("/retranslate") && init?.method === "POST") return jsonResponse(queuedRetranslateRun("bypass", 0, 2));
    if (url.endsWith("/jobs/job-retranslate")) return jsonResponse(detail);
    return jsonResponse({ detail: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  renderApp(<App />, "/jobs/job-retranslate/pipeline");

  expect(await screen.findByText("Cập nhật theo Glossary", { selector: ".pipeline-run-heading strong" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Dịch mới bằng GPT" }));
  expect(await screen.findByRole("alertdialog")).toHaveTextContent("2 lượt cue-ngôn ngữ cần GPT");
  expect(fetchMock.mock.calls.filter(([input]) => String(input).endsWith("/retranslate"))).toHaveLength(0);
  await user.click(screen.getByRole("button", { name: "Dịch mới" }));

  await waitFor(() => expect(fetchMock.mock.calls.filter(([input]) => String(input).endsWith("/retranslate"))).toHaveLength(1));
  expect(JSON.parse(String(fetchMock.mock.calls.find(([input]) => String(input).endsWith("/retranslate"))?.[1]?.body))).toEqual({ cache_mode: "bypass", confirmed_gpt_units: 2 });
});

test("reconfirms when cache misses increase between estimate and enqueue", async () => {
  const detail = retranslateJobDetail();
  let submitCount = 0;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/meta")) return jsonResponse({ stages: [], targets: ["vi"], formats: ["srt"], capabilities: [], readiness: [] });
    if (url.includes("/jobs?") && !init?.method) return jsonResponse({ items: [], total: 0, page: 1, page_size: 20 });
    if (url.endsWith("/retranslate/estimate")) return jsonResponse(translationSummary("reuse", 2, 0, 2));
    if (url.endsWith("/retranslate") && init?.method === "POST") {
      submitCount += 1;
      if (submitCount === 1) {
        return jsonResponse({ detail: { code: "gpt_confirmation_required", message: "confirm", estimate: translationSummary("reuse", 1, 1, 2) } }, 409);
      }
      return jsonResponse(queuedRetranslateRun("reuse", 1, 1));
    }
    if (url.endsWith("/jobs/job-retranslate")) return jsonResponse(detail);
    return jsonResponse({ detail: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  renderApp(<App />, "/jobs/job-retranslate/pipeline");

  await user.click(await screen.findByRole("button", { name: "Cập nhật theo Glossary" }));
  expect(await screen.findByRole("alertdialog")).toHaveTextContent("1 lượt cue-ngôn ngữ cần GPT");
  await user.click(screen.getByRole("button", { name: "Cập nhật bản dịch" }));

  await waitFor(() => expect(submitCount).toBe(2));
  const submissions = fetchMock.mock.calls.filter(([input]) => String(input).endsWith("/retranslate"));
  expect(JSON.parse(String(submissions[0][1]?.body)).confirmed_gpt_units).toBe(0);
  expect(JSON.parse(String(submissions[1][1]?.body)).confirmed_gpt_units).toBe(1);
});

function translationSummary(cacheMode: "reuse" | "bypass", cacheHits: number, gptUnits: number, totalUnits: number) {
  return {
    cache_mode: cacheMode,
    estimated: true,
    total_cues: totalUnits,
    total_units: totalUnits,
    cache_hits: cacheHits,
    gpt_units: gptUnits,
    by_language: [{ language: "vi", total_units: totalUnits, cache_hits: cacheHits, gpt_units: gptUnits }],
  };
}

function queuedRetranslateRun(cacheMode: "reuse" | "bypass", cacheHits: number, gptUnits: number) {
  return {
    run_id: `run-${cacheMode}`,
    job_id: "job-retranslate",
    lane: "pipeline",
    kind: "retranslate",
    from_stage: "s4",
    status: "queued",
    stage: "s4",
    progress: 0,
    message: "Đang chờ pipeline",
    error: null,
    event_seq: 1,
    created_at: "2026-08-27T01:00:00Z",
    started_at: null as string | null,
    finished_at: null as string | null,
    translation_summary: translationSummary(cacheMode, cacheHits, gptUnits, cacheHits + gptUnits),
  };
}

function retranslateJobDetail(options: { latestRun?: ReturnType<typeof queuedRetranslateRun> } = {}) {
  return {
    job_id: "job-retranslate",
    title: "Episode retranslate",
    source: { kind: "local", value: "D:\\episode.mp4" },
    targets: ["vi"],
    execution_status: "completed",
    quality_status: "needs_review",
    request: { source: { kind: "local", value: "D:\\episode.mp4" }, targets: ["vi"], formats: ["srt"], bilingual: false, output_dir: "output/job-retranslate" },
    stages: [],
    artifacts: [],
    attention_reasons: [],
    allowed_actions: ["retranslate", "render"],
    lanes: [{
      id: "pipeline",
      started: true,
      execution_status: "completed",
      quality_status: "needs_review",
      progress: 1,
      message: "",
      error: null,
      attention_reasons: [],
      active_run: null,
      latest_run: options.latestRun ?? null,
      allowed_actions: [],
    }],
  };
}
