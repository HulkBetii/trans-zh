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
