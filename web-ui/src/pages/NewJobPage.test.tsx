import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { App } from "../App";
import { jsonResponse, renderApp } from "../test/render";

test("creates a local job with selected targets and formats", async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/v1/meta")) return jsonResponse({ stages: [], targets: ["vi", "en"], formats: ["srt", "ass"], capabilities: [], readiness: [{ id: "ffmpeg", label: "FFmpeg", status: "ready" }] });
    if (url.includes("/api/v1/jobs?") && !init?.method) return jsonResponse({ items: [], total: 0, page: 1, page_size: 20 });
    if (url.endsWith("/api/v1/jobs") && init?.method === "POST") return jsonResponse({ job_id: "episode-01_abcd1234" }, 201);
    return jsonResponse({ detail: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  renderApp(<App />, "/new");

  const source = await screen.findByLabelText("Đường dẫn tuyệt đối");
  await user.type(source, "D:\\Media\\episode-01.mp4");
  await user.click(screen.getByText("English"));
  await user.click(screen.getByText("ASS"));
  await user.click(screen.getByRole("button", { name: "Tạo và bắt đầu" }));

  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/v1/jobs", expect.objectContaining({ method: "POST" })));
  const call = fetchMock.mock.calls.find(([url, init]) => String(url).endsWith("/api/v1/jobs") && init?.method === "POST");
  expect(JSON.parse(String(call?.[1]?.body))).toEqual({
    source: { kind: "local", value: "D:\\Media\\episode-01.mp4" },
    targets: ["vi", "en"],
    formats: ["srt", "ass"],
    bilingual: false,
  });
});

test("blocks URL submission when readiness has a hard dependency failure", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).includes("/meta")) return jsonResponse({ stages: [], targets: ["vi"], formats: ["srt"], capabilities: [], readiness: [{ id: "ffmpeg", label: "FFmpeg", status: "blocked", detail: "Không tìm thấy" }] });
    return jsonResponse({ items: [], total: 0, page: 1, page_size: 20 });
  }));
  const user = userEvent.setup();
  renderApp(<App />, "/new");
  await user.click(await screen.findByRole("radio", { name: "URL" }));
  await user.type(screen.getByLabelText("URL video"), "https://example.com/video");
  expect(screen.getByRole("button", { name: "Tạo và bắt đầu" })).toBeDisabled();
  expect(screen.getByText("Không tìm thấy")).toBeInTheDocument();
});

test("keeps submission disabled until readiness can be loaded successfully", async () => {
  let metaRequests = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/meta")) {
      metaRequests += 1;
      if (metaRequests === 1) return jsonResponse({ detail: "readiness unavailable" }, 503);
      return jsonResponse({ stages: [], targets: ["vi"], formats: ["srt"], capabilities: [], readiness: [{ id: "ffmpeg", label: "FFmpeg", status: "ready" }] });
    }
    return jsonResponse({ items: [], total: 0, page: 1, page_size: 20 });
  }));
  const user = userEvent.setup();
  renderApp(<App />, "/new");

  await screen.findByText("readiness unavailable");
  await user.type(screen.getByLabelText("Đường dẫn tuyệt đối"), "D:\\Media\\episode-01.mp4");
  const submit = screen.getByRole("button", { name: "Tạo và bắt đầu" });
  expect(submit).toBeDisabled();

  await user.click(screen.getByRole("button", { name: "Thử lại" }));
  await waitFor(() => expect(submit).toBeEnabled());
  expect(metaRequests).toBe(2);
});
