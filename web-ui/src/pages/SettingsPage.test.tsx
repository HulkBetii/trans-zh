import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { App } from "../App";
import type { SettingsResponse } from "../api/types";
import { jsonResponse, renderApp } from "../test/render";

test("renders blank password fields without exposing stored credentials", async () => {
  vi.stubGlobal("fetch", settingsFetch(settingsFixture()));
  renderApp(<App />, "/settings");

  expect(await screen.findByRole("heading", { name: "Kết nối nhà cung cấp AI" })).toBeInTheDocument();
  const openAiInput = screen.getByLabelText("Khóa API OpenAI");
  expect(openAiInput).toHaveAttribute("type", "password");
  expect(openAiInput).toHaveAttribute("autocomplete", "new-password");
  expect(openAiInput).toHaveValue("");
  expect(screen.getByText("********")).toBeInTheDocument();
});

test("saves with the current revision and clears the password field", async () => {
  const initial = settingsFixture();
  const saved = settingsFixture({
    revision: "revision-2",
    credentials: initial.credentials.map((credential) => credential.id === "openai"
      ? { ...credential, configured: true, source: "credential_store", masked_value: "********" }
      : credential),
  });
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/v1/jobs?")) return emptyJobs();
    if (url.endsWith("/api/v1/settings") && !init?.method) return jsonResponse(initial);
    if (url.endsWith("/api/v1/settings/credentials/openai") && init?.method === "PUT") return jsonResponse(saved);
    return jsonResponse({ detail: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  renderApp(<App />, "/settings");

  const input = await screen.findByLabelText("Khóa API OpenAI");
  await user.type(input, "sk-new-secret");
  await user.click(screen.getAllByRole("button", { name: "Lưu khóa" })[0]);

  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
    "/api/v1/settings/credentials/openai",
    expect.objectContaining({ method: "PUT" }),
  ));
  const call = fetchMock.mock.calls.find(([url, init]) => String(url).endsWith("/credentials/openai") && init?.method === "PUT");
  expect(JSON.parse(String(call?.[1]?.body))).toEqual({ revision: "revision-1", secret: "sk-new-secret" });
  await waitFor(() => expect(input).toHaveValue(""));
  expect(screen.getByText("Đã lưu khóa vào kho bảo mật của hệ điều hành.")).toBeInTheDocument();
});

test("pins a draft to the revision that was current when editing started", async () => {
  const initial = settingsFixture();
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/v1/jobs?")) return emptyJobs();
    if (url.endsWith("/api/v1/settings") && !init?.method) return jsonResponse(initial);
    if (url.endsWith("/api/v1/settings/credentials/openai") && init?.method === "PUT") {
      return jsonResponse({ detail: { code: "revision_conflict" } }, 409);
    }
    return jsonResponse({ detail: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  const { queryClient } = renderApp(<App />, "/settings");

  const input = await screen.findByLabelText("Khóa API OpenAI");
  await user.type(input, "pinned-draft");
  act(() => queryClient.setQueryData(["settings"], settingsFixture({ revision: "revision-2" })));
  await user.click(screen.getAllByRole("button", { name: "Lưu khóa" })[0]);

  const call = await waitFor(() => {
    const found = fetchMock.mock.calls.find(([url, init]) => String(url).endsWith("/credentials/openai") && init?.method === "PUT");
    expect(found).toBeDefined();
    return found;
  });
  expect(JSON.parse(String(call?.[1]?.body))).toEqual({ revision: "revision-1", secret: "pinned-draft" });
  expect(await screen.findByText("Cài đặt đã đổi ở tab khác. Khóa đang nhập vẫn được giữ lại.")).toBeInTheDocument();
  expect(input).toHaveValue("pinned-draft");
});

test("tests a draft secret without rendering the echoed server message", async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/v1/jobs?")) return emptyJobs();
    if (url.endsWith("/api/v1/settings") && !init?.method) return jsonResponse(settingsFixture());
    if (url.endsWith("/api/v1/settings/credentials/openai/test") && init?.method === "POST") {
      return jsonResponse({ ok: true, code: "ok", message: "draft-secret accepted", latency_ms: 24 });
    }
    return jsonResponse({ detail: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  renderApp(<App />, "/settings");

  await user.type(await screen.findByLabelText("Khóa API OpenAI"), "draft-secret");
  await user.click(screen.getAllByRole("button", { name: "Kiểm tra" })[0]);

  const call = await waitFor(() => {
    const found = fetchMock.mock.calls.find(([url, init]) => String(url).endsWith("/credentials/openai/test") && init?.method === "POST");
    expect(found).toBeDefined();
    return found;
  });
  expect(JSON.parse(String(call?.[1]?.body))).toEqual({ secret: "draft-secret" });
  expect(await screen.findByText("Kết nối thành công · 24 ms.")).toBeInTheDocument();
  expect(screen.queryByText("draft-secret accepted")).not.toBeInTheDocument();
});

test("deletes a stored credential with the current revision", async () => {
  const initial = settingsFixture();
  const removed = settingsFixture({
    revision: "revision-2",
    credentials: initial.credentials.map((credential) => credential.id === "openai"
      ? { ...credential, configured: false, source: "none", masked_value: null }
      : credential),
  });
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/v1/jobs?")) return emptyJobs();
    if (url.endsWith("/api/v1/settings") && !init?.method) return jsonResponse(initial);
    if (url.endsWith("/api/v1/settings/credentials/openai") && init?.method === "DELETE") return jsonResponse(removed);
    return jsonResponse({ detail: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  renderApp(<App />, "/settings");

  await user.click(await screen.findByRole("button", { name: "Xóa" }));
  await user.click(await screen.findByRole("button", { name: "Xóa khóa" }));

  const call = await waitFor(() => {
    const found = fetchMock.mock.calls.find(([url, init]) => String(url).endsWith("/credentials/openai") && init?.method === "DELETE");
    expect(found).toBeDefined();
    return found;
  });
  expect(JSON.parse(String(call?.[1]?.body))).toEqual({ revision: "revision-1" });
});

test("keeps environment-managed credentials read-only", async () => {
  const initial = settingsFixture();
  initial.credentials[0] = {
    ...initial.credentials[0],
    configured: true,
    source: "environment",
    editable: false,
    masked_value: "********",
  };
  vi.stubGlobal("fetch", settingsFetch(initial));
  renderApp(<App />, "/settings");

  const input = await screen.findByLabelText("Khóa API OpenAI");
  expect(input).toBeDisabled();
  expect(screen.getByText("Quản lý khóa qua OPENAI_API_KEY; giao diện không ghi đè nguồn này.")).toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: "Kiểm tra" })[0]).toBeEnabled();
  expect(screen.queryByRole("button", { name: "Xóa" })).not.toBeInTheDocument();
});

test("retains the draft when a save revision conflicts", async () => {
  let settingsReads = 0;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/v1/jobs?")) return emptyJobs();
    if (url.endsWith("/api/v1/settings") && !init?.method) {
      settingsReads += 1;
      return jsonResponse(settingsFixture({ revision: settingsReads === 1 ? "revision-1" : "revision-2" }));
    }
    if (url.endsWith("/api/v1/settings/credentials/openai") && init?.method === "PUT") {
      return jsonResponse({ detail: { code: "revision_conflict", message: "stale" } }, 409);
    }
    return jsonResponse({ detail: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  renderApp(<App />, "/settings");

  const input = await screen.findByLabelText("Khóa API OpenAI");
  await user.type(input, "keep-this-draft");
  await user.click(screen.getAllByRole("button", { name: "Lưu khóa" })[0]);
  expect(await screen.findByText("Cài đặt đã đổi ở tab khác. Khóa đang nhập vẫn được giữ lại.")).toBeInTheDocument();
  expect(input).toHaveValue("keep-this-draft");

  await user.click(screen.getByRole("button", { name: "Tải revision mới" }));
  await waitFor(() => expect(settingsReads).toBe(2));
  expect(input).toHaveValue("keep-this-draft");
});

test("shows an active-run error without mislabeling it as a revision conflict", async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/v1/jobs?")) return emptyJobs();
    if (url.endsWith("/api/v1/settings") && !init?.method) return jsonResponse(settingsFixture());
    if (url.endsWith("/api/v1/settings/credentials/openai") && init?.method === "PUT") {
      return jsonResponse({ detail: { code: "action_not_allowed", reason: "active_run", message: "busy" } }, 409);
    }
    return jsonResponse({ detail: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  renderApp(<App />, "/settings");

  const input = await screen.findByLabelText("Khóa API OpenAI");
  await user.type(input, "retry-after-run");
  await user.click(screen.getAllByRole("button", { name: "Lưu khóa" })[0]);

  expect(await screen.findByText("Không thể đổi khóa khi còn run đang hoạt động.")).toBeInTheDocument();
  expect(screen.queryByText("Cài đặt đã đổi ở tab khác. Khóa đang nhập vẫn được giữ lại.")).not.toBeInTheDocument();
  expect(input).toHaveValue("retry-after-run");
});

function settingsFetch(settings: SettingsResponse) {
  return vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).includes("/api/v1/jobs?")) return emptyJobs();
    if (String(input).endsWith("/api/v1/settings")) return jsonResponse(settings);
    return jsonResponse({ detail: "not found" }, 404);
  });
}

function emptyJobs() {
  return jsonResponse({ items: [], total: 0, page: 1, page_size: 20 });
}

function settingsFixture(patch: Partial<SettingsResponse> = {}): SettingsResponse {
  const settings: SettingsResponse = {
    revision: "revision-1",
    credential_store_available: true,
    credentials: [
      { id: "openai", label: "OpenAI", configured: true, source: "credential_store", editable: true, masked_value: "********", env_name: "OPENAI_API_KEY" },
      { id: "anthropic", label: "Anthropic", configured: false, source: "none", editable: true, masked_value: null, env_name: "ANTHROPIC_API_KEY" },
      { id: "ai33", label: "AI33 / Vbee", configured: false, source: "none", editable: true, masked_value: null, env_name: "AI33_API_KEY" },
    ],
    providers: [
      { id: "segment", label: "Phân đoạn", provider: "openai", model: "gpt-5-mini", base_url: null, credential_id: "openai" },
      { id: "translate", label: "Dịch", provider: "anthropic", model: "claude-sonnet", base_url: null, credential_id: "anthropic" },
      { id: "tts", label: "Lồng tiếng", provider: "ai33", model: null, base_url: "https://api.ai33.pro", credential_id: "ai33" },
    ],
  };
  return { ...settings, ...patch };
}
