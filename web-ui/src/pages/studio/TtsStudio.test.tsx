import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, vi } from "vitest";
import { queryKeys } from "../../api/queries";
import type { JobDetail, TtsWorkspace } from "../../api/types";
import { jsonResponse, renderApp } from "../../test/render";
import { TtsStudio } from "./TtsStudio";

const virtualizerMocks = vi.hoisted(() => ({ counts: [] as number[] }));

vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: ({ count }: { count: number }) => {
    virtualizerMocks.counts.push(count);
    return {
      getTotalSize: () => count * 82,
      getVirtualItems: () => Array.from({ length: count }, (_, index) => ({ index, start: index * 82, key: index })),
      measureElement: vi.fn(),
      scrollToIndex: vi.fn(),
    };
  },
}));

const job = {
  job_id: "job-tts",
  title: "Episode",
  execution_status: "completed",
  quality_status: "approved",
  targets: ["vi"],
  progress: 1,
  message: "",
  glossary_stale: false,
  request: { source: { kind: "local", value: "D:\\episode.mp4" }, targets: ["vi"], formats: ["srt"], bilingual: false, output_dir: "output/job-tts" },
  stages: [],
  artifacts: [],
  allowed_actions: [],
} satisfies JobDetail;

const cue = {
  segment_id: 1,
  start: 1.2,
  end: 3.8,
  subtitle_text: "Xin chào thế giới",
  default_spoken_text: "Xin chào thế giới",
  effective_spoken_text: "Xin chào thế giới",
  override_state: "none" as const,
  room_seconds: 3.4,
  predicted_duration: 2.1,
  overflow_seconds: 0,
  speed: 1,
  preview_state: "missing" as const,
  preview_url: null,
};

const workspace: TtsWorkspace = {
  revision: "tts-rev-1",
  language: "vi",
  provider: "vbee",
  provider_ready: true,
  voice_id: "voice-1",
  selected_voice: { voice_id: "voice-1", name: "Giọng nam 1", locale: "vi-VN", gender: "male", calibrated: true },
  calibration: { provider: "vbee", voice_id: "voice-1", overhead_sec: 0.15, sec_per_syllable: 0.217, sample_count: 8, revision: "cal-1" },
  subtitle_approved: false,
  execution_status: "not_started",
  quality_status: "needs_review",
  latest_run: null,
  active_run: null,
  attention_reasons: [],
  allowed_actions: ["preview"],
  cues: [cue],
  total_cues: 1,
  uncached_cues: 1,
  output: null,
  output_stale: false,
  editor_locked: false,
};

afterEach(() => {
  virtualizerMocks.counts.length = 0;
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

test("saves spoken text separately from subtitle content", async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/tts/vi/spoken-overrides") && init?.method === "PUT") {
      return jsonResponse({ ...workspace, revision: "tts-rev-2", cues: [{ ...cue, effective_spoken_text: "Chào thế giới", override_state: "manual" }] });
    }
    return jsonResponse(workspace);
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  renderApp(<TtsStudio job={job} />);

  const editor = await screen.findByLabelText("Văn bản sẽ đọc");
  await user.clear(editor);
  await user.type(editor, "Chào thế giới");
  await user.click(screen.getByRole("button", { name: /^Lưu$/ }));

  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
    "/api/v1/jobs/job-tts/tts/vi/spoken-overrides",
    expect.objectContaining({ method: "PUT" }),
  ));
  const put = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT");
  expect(JSON.parse(String(put?.[1]?.body))).toEqual({ revision: "tts-rev-1", changes: [{ segment_id: 1, text: "Chào thế giới" }] });
});

test("allows a paid cue preview before subtitle approval but gates full render", async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/tts/vi/preview") && init?.method === "POST") {
      return jsonResponse({ run_id: "run-preview", job_id: "job-tts", lane: "tts", kind: "tts_preview", from_stage: "tts_preview", status: "queued", progress: 0, message: "Đã đưa vào hàng chờ", event_seq: 1 });
    }
    return jsonResponse(workspace);
  });
  vi.stubGlobal("fetch", fetchMock);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  const user = userEvent.setup();
  renderApp(<TtsStudio job={job} />);

  expect(await screen.findByText("Phụ đề chưa được duyệt")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Tạo MP3 lồng tiếng" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Nghe thử · tốn 1 lượt TTS" }));

  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
    "/api/v1/jobs/job-tts/tts/vi/preview",
    expect.objectContaining({ method: "POST" }),
  ));
});

test("keeps paid TTS actions disabled when the provider is unavailable", async () => {
  const unavailableWorkspace = {
    ...workspace,
    provider_ready: false,
    subtitle_approved: true,
    allowed_actions: ["preview", "render"] as TtsWorkspace["allowed_actions"],
  };
  vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(unavailableWorkspace)));

  renderApp(<TtsStudio job={job} />);

  expect(await screen.findByText("AI33 / Vbee chưa sẵn sàng")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Nghe thử · tốn 1 lượt TTS" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Tạo MP3 lồng tiếng" })).toBeDisabled();
});

test("selects a voice in the library and saves the revisioned setting", async () => {
  const noVoiceWorkspace = { ...workspace, voice_id: null, selected_voice: null, calibration: null, allowed_actions: ["calibrate"] as const };
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/tts/voices")) return jsonResponse({ items: [{ voice_id: "voice-2", name: "Giọng nữ 2", locale: "vi-VN", gender: "female" }], page: 1, page_size: 50, total: 1, has_more: false, credits: 12 });
    if (url.endsWith("/tts/vi/settings") && init?.method === "PUT") return jsonResponse({ ...noVoiceWorkspace, revision: "tts-rev-2", voice_id: "voice-2", selected_voice: { voice_id: "voice-2", name: "Giọng nữ 2", locale: "vi-VN", gender: "female" } });
    return jsonResponse(noVoiceWorkspace);
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  renderApp(<TtsStudio job={job} />);

  await user.click(await screen.findByRole("button", { name: "Mở thư viện giọng" }));
  await user.click(await screen.findByRole("button", { name: /Giọng nữ 2/ }));
  await user.click(screen.getByRole("button", { name: "Lưu giọng" }));

  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
    "/api/v1/jobs/job-tts/tts/vi/settings",
    expect.objectContaining({ method: "PUT" }),
  ));
  const put = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT");
  expect(JSON.parse(String(put?.[1]?.body))).toEqual({ revision: "tts-rev-1", voice_id: "voice-2" });
});

test("browses community voices, which the provider hides unless asked for", async () => {
  // Nhà cung cấp mặc định chỉ trả 25 giọng chính hãng; cả thư viện là 1268. Giọng
  // dự án đang dùng (Duy Onyx) nằm ở nhóm cộng đồng nên trước đây không hề hiện ra.
  const noVoiceWorkspace = { ...workspace, voice_id: null, selected_voice: null, calibration: null, allowed_actions: ["calibrate"] as const };
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/tts/voices")) {
      const community = new URL(url, "http://localhost").searchParams.get("ownership") === "community";
      return jsonResponse({
        items: [community
          ? { voice_id: "vbee_n_hn_male_duyonyx_oaistable_vc", name: "Duy Onyx", locale: "vi-VN", gender: "male" }
          : { voice_id: "vbee_hn_female_ngochuyen", name: "HN - Ngọc Huyền", locale: "vi-VN", gender: "female" }],
        page: 1, page_size: 30, total: 1, has_more: false, credits: null,
      });
    }
    return jsonResponse(noVoiceWorkspace);
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  renderApp(<TtsStudio job={job} />);

  await user.click(await screen.findByRole("button", { name: "Mở thư viện giọng" }));
  expect(await screen.findByRole("button", { name: /Ngọc Huyền/ })).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "Cộng đồng" }));

  expect(await screen.findByRole("button", { name: /Duy Onyx/ })).toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([input]) => String(input).includes("ownership=community"))).toBe(true);
});

test("asks for every voice source by default", async () => {
  const noVoiceWorkspace = { ...workspace, voice_id: null, selected_voice: null, calibration: null, allowed_actions: ["calibrate"] as const };
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).includes("/tts/voices")) {
      return jsonResponse({ items: [], page: 1, page_size: 30, total: 0, has_more: false, credits: null });
    }
    return jsonResponse(noVoiceWorkspace);
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  renderApp(<TtsStudio job={job} />);

  await user.click(await screen.findByRole("button", { name: "Mở thư viện giọng" }));

  await waitFor(() => expect(
    fetchMock.mock.calls.some(([input]) => String(input).includes("ownership=all")),
  ).toBe(true));
});

test("waits for typing to stop before asking the provider", async () => {
  // Tìm kiếm chạy phía nhà cung cấp, nên mỗi ký tự không debounce là một request
  // thật ra ai33.pro — gõ "duyonyx" thành bảy lần gọi.
  const noVoiceWorkspace = { ...workspace, voice_id: null, selected_voice: null, calibration: null, allowed_actions: ["calibrate"] as const };
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).includes("/tts/voices")) {
      return jsonResponse({
        items: [{ voice_id: "vbee_n_hn_male_duyonyx_oaistable_vc", name: "Duy Onyx", locale: "vi-VN", gender: "male" }],
        page: 1, page_size: 30, total: 1, has_more: false, credits: null,
      });
    }
    return jsonResponse(noVoiceWorkspace);
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  renderApp(<TtsStudio job={job} />);

  await user.click(await screen.findByRole("button", { name: "Mở thư viện giọng" }));
  await screen.findByRole("button", { name: /Duy Onyx/ });
  await user.type(screen.getByPlaceholderText(/Tìm tên/), "duyonyx");

  const searched = () => fetchMock.mock.calls
    .map(([input]) => new URL(String(input), "http://localhost").searchParams.get("search"))
    .filter((value): value is string => value !== null);
  await waitFor(() => expect(searched()).toEqual(["duyonyx"]));
});

test("virtualises the voice library instead of mounting every row", async () => {
  // 1268 giọng, mỗi dòng một thẻ <audio>: dựng hết là trình duyệt ôm hơn một
  // nghìn player cùng lúc.
  const noVoiceWorkspace = { ...workspace, voice_id: null, selected_voice: null, calibration: null, allowed_actions: ["calibrate"] as const };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).includes("/tts/voices")) {
      return jsonResponse({
        items: [
          { voice_id: "voice-a", name: "Giọng A", locale: "vi-VN", gender: "female" },
          { voice_id: "voice-b", name: "Giọng B", locale: "vi-VN", gender: "male" },
          { voice_id: "voice-c", name: "Giọng C", locale: "vi-VN", gender: "male" },
        ],
        page: 1, page_size: 30, total: 3, has_more: false, credits: null,
      });
    }
    return jsonResponse(noVoiceWorkspace);
  }));
  const user = userEvent.setup();
  renderApp(<TtsStudio job={job} />);

  await user.click(await screen.findByRole("button", { name: "Mở thư viện giọng" }));
  await screen.findByRole("button", { name: /Giọng A/ });

  expect(virtualizerMocks.counts).toContain(3);
});

test("keeps voice setup available before a voice has generated the cue plan", async () => {
  const noVoiceWorkspace = { ...workspace, voice_id: null, selected_voice: null, calibration: null, cues: [], total_cues: 0, allowed_actions: [] as const };
  vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(noVoiceWorkspace)));

  renderApp(<TtsStudio job={job} />);

  expect(await screen.findByRole("button", { name: "Mở thư viện giọng" })).toBeEnabled();
  expect(screen.getByText("Không có cue khớp bộ lọc hiện tại.")).toBeInTheDocument();
});

test("keeps unsaved spoken text when saving a voice separately", async () => {
  const noVoiceWorkspace = { ...workspace, voice_id: null, selected_voice: null, calibration: null, allowed_actions: ["preview", "calibrate"] as const };
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/tts/voices")) return jsonResponse({ items: [{ voice_id: "voice-2", name: "Giọng nữ 2", locale: "vi-VN", gender: "female" }], page: 1, page_size: 50, total: 1, has_more: false, credits: null });
    if (url.endsWith("/tts/vi/settings") && init?.method === "PUT") return jsonResponse({ ...noVoiceWorkspace, revision: "tts-rev-2", voice_id: "voice-2", selected_voice: null });
    return jsonResponse(noVoiceWorkspace);
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  renderApp(<TtsStudio job={job} />);

  const editor = await screen.findByLabelText("Văn bản sẽ đọc");
  await user.clear(editor);
  await user.type(editor, "Cách đọc đang sửa");
  await user.click(screen.getByRole("button", { name: "Mở thư viện giọng" }));
  await user.click(await screen.findByRole("button", { name: /Giọng nữ 2/ }));
  await user.click(screen.getByRole("button", { name: "Lưu giọng" }));

  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
    "/api/v1/jobs/job-tts/tts/vi/settings",
    expect.objectContaining({ method: "PUT" }),
  ));
  expect(screen.getByLabelText("Văn bản sẽ đọc")).toHaveValue("Cách đọc đang sửa");
  expect(screen.getByText("1 thay đổi TTS chưa lưu")).toBeInTheDocument();
});

test("keeps the local spoken draft when the TTS revision conflicts", async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/tts/vi/spoken-overrides") && init?.method === "PUT") {
      return jsonResponse({ detail: { code: "revision_conflict", current_revision: "tts-rev-2" } }, 409);
    }
    return jsonResponse(workspace);
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  renderApp(<TtsStudio job={job} />);

  const editor = await screen.findByLabelText("Văn bản sẽ đọc");
  await user.clear(editor);
  await user.type(editor, "Bản đọc ở tab hiện tại");
  await user.click(screen.getByRole("button", { name: /^Lưu$/ }));

  expect(await screen.findByText("TTS đã thay đổi ở tab khác")).toBeInTheDocument();
  expect(screen.getByLabelText("Văn bản sẽ đọc")).toHaveValue("Bản đọc ở tab hiện tại");
  expect(screen.getByText(/Dữ liệu đã thay đổi ở một tab khác/)).toBeInTheDocument();
});

test("discards the TTS draft only when the banner button is pressed", async () => {
  // Nút này từng là window.location.reload(): nạp lại cả trang để lấy một bản
  // TTS, kéo theo mọi state chưa lưu ở nơi khác. Giờ nó chỉ bỏ bản nháp TTS.
  vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(workspace)));
  const { queryClient } = renderApp(<TtsStudio job={job} />);
  const user = userEvent.setup();
  const editor = await screen.findByLabelText("Văn bản sẽ đọc");
  await user.clear(editor);
  await user.type(editor, "Bản nháp chưa lưu");

  act(() => {
    queryClient.setQueryData(queryKeys.tts(job.job_id), {
      ...workspace,
      revision: "tts-rev-9",
      cues: [{ ...cue, effective_spoken_text: "Bản máy mới" }],
    });
  });

  expect(await screen.findByText("TTS đã thay đổi ở tab khác")).toBeInTheDocument();
  expect(screen.getByLabelText("Văn bản sẽ đọc")).toHaveValue("Bản nháp chưa lưu");

  await user.click(screen.getByRole("button", { name: /Bỏ chỉnh sửa/ }));

  expect(screen.getByLabelText("Văn bản sẽ đọc")).toHaveValue("Bản máy mới");
  expect(screen.queryByText("TTS đã thay đổi ở tab khác")).not.toBeInTheDocument();
});

test("saves only the selected cue before preview and keeps other invalid drafts", async () => {
  const secondCue = { ...cue, segment_id: 2, subtitle_text: "Câu thứ hai", default_spoken_text: "Câu thứ hai", effective_spoken_text: "Câu thứ hai" };
  const twoCueWorkspace = { ...workspace, cues: [cue, secondCue], total_cues: 2 };
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/tts/vi/spoken-overrides") && init?.method === "PUT") {
      return jsonResponse({ ...twoCueWorkspace, revision: "tts-rev-2", cues: [{ ...cue, effective_spoken_text: "Cách đọc cue một", override_state: "manual" }, secondCue] });
    }
    if (url.endsWith("/tts/vi/preview") && init?.method === "POST") {
      return jsonResponse({ run_id: "run-preview", job_id: "job-tts", lane: "tts", kind: "tts_preview", from_stage: "tts_preview", status: "queued", progress: 0, message: "Đang chờ", event_seq: 1 });
    }
    return jsonResponse(twoCueWorkspace);
  });
  vi.stubGlobal("fetch", fetchMock);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  const user = userEvent.setup();
  renderApp(<TtsStudio job={job} />);

  await user.click((await screen.findAllByText("Câu thứ hai"))[0].closest("button")!);
  await user.clear(screen.getByLabelText("Văn bản sẽ đọc"));
  await user.click(screen.getAllByText("Xin chào thế giới")[0].closest("button")!);
  const editor = screen.getByLabelText("Văn bản sẽ đọc");
  await user.clear(editor);
  await user.type(editor, "Cách đọc cue một");
  await user.click(screen.getByRole("button", { name: "Lưu cue này & nghe thử · tốn 1 lượt TTS" }));

  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
    "/api/v1/jobs/job-tts/tts/vi/preview",
    expect.objectContaining({ method: "POST" }),
  ));
  const put = fetchMock.mock.calls.find(([url, init]) => String(url).endsWith("spoken-overrides") && init?.method === "PUT");
  expect(JSON.parse(String(put?.[1]?.body))).toEqual({ revision: "tts-rev-1", changes: [{ segment_id: 1, text: "Cách đọc cue một" }] });
  expect(screen.getByText("1 thay đổi TTS chưa lưu")).toBeInTheDocument();
});

test("keeps the latest terminal TTS error visible", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({
    ...workspace,
    execution_status: "failed",
    latest_run: {
      run_id: "run-failed",
      job_id: "job-tts",
      lane: "tts",
      kind: "tts_render",
      from_stage: "tts_render",
      status: "failed",
      stage: "tts_render",
      progress: 0.4,
      message: "Provider từ chối request",
      error: "quota_exhausted",
      event_seq: 3,
      created_at: "2026-01-01T00:00:00Z",
      finished_at: "2026-01-01T00:00:05Z",
    },
  })));

  renderApp(<TtsStudio job={job} />);

  expect(await screen.findByText("Thất bại · MP3 timeline")).toBeInTheDocument();
  expect(screen.getByText("quota_exhausted")).toBeInTheDocument();
});

test("loads more voices and removes duplicate voice IDs", async () => {
  const noVoiceWorkspace = { ...workspace, voice_id: null, selected_voice: null, calibration: null, cues: [], total_cues: 0, allowed_actions: [] as const };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/tts/voices")) {
      const page = url.includes("page=2") ? 2 : 1;
      return jsonResponse({
        items: page === 1
          ? [{ voice_id: "voice-a", name: "Giọng A" }]
          : [{ voice_id: "voice-a", name: "Giọng A" }, { voice_id: "voice-b", name: "Giọng B" }],
        page,
        page_size: 30,
        total: 3,
        has_more: page === 1,
        credits: null,
      });
    }
    return jsonResponse(noVoiceWorkspace);
  }));
  const user = userEvent.setup();
  renderApp(<TtsStudio job={job} />);

  await user.click(await screen.findByRole("button", { name: "Mở thư viện giọng" }));
  await user.click(await screen.findByRole("button", { name: "Tải thêm giọng" }));

  expect(await screen.findByRole("button", { name: /Giọng B/ })).toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: /Giọng A/ })).toHaveLength(1);
});
