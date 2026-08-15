import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { jsonResponse, renderApp } from "../test/render";
import { JobNavigator } from "./JobNavigator";

test("requests the next server page from the navigator", async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const page = String(input).includes("page=2") ? 2 : 1;
    return jsonResponse({
      items: [{
        job_id: `job-${page}`,
        title: `Episode ${page}`,
        targets: ["vi"],
        execution_status: "completed",
        quality_status: "needs_review",
        progress: 1,
        message: "",
      }],
      total: 100,
      page,
      page_size: 50,
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  renderApp(<JobNavigator />, "/new");

  expect(await screen.findByText("Episode 1")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Sau" }));

  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("page=2"), expect.anything()));
  expect(await screen.findByText("Episode 2")).toBeInTheDocument();
});

test("routes to the active TTS lane and cancels it safely", async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/runs/run-tts/cancel") && init?.method === "POST") return jsonResponse({ ok: true, detail: "cancelled" });
    return jsonResponse({
      items: [{
        job_id: "job-audio",
        title: "Episode Audio",
        targets: ["vi"],
        execution_status: "completed",
        quality_status: "approved",
        progress: 1,
        message: "",
        lanes: [
          { id: "pipeline", started: true, execution_status: "completed", quality_status: "approved", progress: 1, message: "", attention_reasons: [], allowed_actions: [] },
          { id: "tts", started: true, execution_status: "running", quality_status: "needs_review", progress: 0.4, message: "Đang tạo MP3", attention_reasons: [], allowed_actions: ["cancel"], active_run: { run_id: "run-tts", job_id: "job-audio", lane: "tts", kind: "tts_render", from_stage: "tts_render", status: "running", progress: 0.4, message: "Đang tạo MP3", event_seq: 2 } },
        ],
      }],
      total: 1,
      page: 1,
      page_size: 50,
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  renderApp(<JobNavigator />, "/new");

  const row = await screen.findByRole("link", { name: /Episode Audio/ });
  expect(row).toHaveAttribute("href", "/jobs/job-audio/tts");
  expect(screen.getByText("Audio")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /Dừng an toàn audio/ }));

  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
    "/api/v1/runs/run-tts/cancel",
    expect.objectContaining({ method: "POST" }),
  ));
});
