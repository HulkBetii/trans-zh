import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, vi } from "vitest";
import { queryKeys } from "../../api/queries";
import type { JobDetail, SubtitleWorkspace } from "../../api/types";
import { jsonResponse, renderApp } from "../../test/render";
import { SubtitleStudio } from "./SubtitleStudio";

const virtualizerMocks = vi.hoisted(() => ({ scrollToIndex: vi.fn() }));

vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: () => ({
    getTotalSize: () => 0,
    getVirtualItems: () => [],
    measureElement: vi.fn(),
    scrollToIndex: virtualizerMocks.scrollToIndex,
  }),
}));

const job = {
  job_id: "job-1", title: "Episode", execution_status: "completed", quality_status: "needs_review",
  targets: ["vi"], progress: 1, message: "", glossary_stale: false,
  request: { source: { kind: "local", value: "D:\\episode.mp4" }, targets: ["vi"], formats: ["srt"], bilingual: false, output_dir: "output/job-1" },
  stages: [], artifacts: [], allowed_actions: ["render"],
} satisfies JobDetail;

const workspace: SubtitleWorkspace = {
  revision: "rev-1",
  language: "vi",
  editor_locked: false,
  output_stale: false,
  cues: [{
    segment_id: 1,
    start: 1.2,
    end: 3.8,
    source_text: "你好世界",
    model_text: "Xin chào thế giới",
    effective_text: "Xin chào thế giới",
    reviewed: false,
    cps: 6.5,
    line_count: 1,
    warnings: [],
    override_state: "none",
    overridden: false,
    stale: false,
    base_changed: false,
  }],
};

afterEach(() => {
  virtualizerMocks.scrollToIndex.mockReset();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

test("saves subtitle text as an override without changing timing", async () => {
  const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method === "PUT") return jsonResponse({ ...workspace, revision: "rev-2", cues: [{ ...workspace.cues[0], effective_text: "Chào thế giới", override_state: "manual" }] });
    return jsonResponse(workspace);
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  renderApp(<SubtitleStudio job={job} />);

  const editor = await screen.findByLabelText("Bản hiệu lực");
  await user.clear(editor);
  await user.type(editor, "Chào thế giới");
  await user.click(screen.getByRole("button", { name: "Lưu" }));

  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  const put = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT");
  expect(JSON.parse(String(put?.[1]?.body))).toEqual({ revision: "rev-1", changes: [{ segment_id: 1, text: "Chào thế giới" }] });
});

test("keeps dirty subtitle text when the server revision changes", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(workspace)));
  const { queryClient } = renderApp(<SubtitleStudio job={job} />);
  const user = userEvent.setup();
  const editor = await screen.findByLabelText("Bản hiệu lực");
  await user.clear(editor);
  await user.type(editor, "Bản local chưa lưu");

  act(() => {
    queryClient.setQueryData(queryKeys.subtitles(job.job_id, "vi"), {
      ...workspace,
      revision: "rev-2",
      cues: [{ ...workspace.cues[0], model_text: "Bản máy mới", effective_text: "Bản máy mới" }],
    });
  });

  expect(await screen.findByText("Phụ đề đã thay đổi ở tab khác")).toBeInTheDocument();
  expect(screen.getByLabelText("Bản hiệu lực")).toHaveValue("Bản local chưa lưu");
});

test("does not read a lock flip as someone else overwriting the subtitles", async () => {
  // `editor_locked` lật lên khi một run TTS bắt đầu, `output_stale` lật sau khi
  // render — cùng revision, không ai ghi đè gì. So định danh object thì cả hai
  // đều hiện banner xung đột, mà nút duy nhất trong banner là reload: đúng thao
  // tác xóa sạch bản nháp đang có trên màn hình.
  vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(workspace)));
  const { queryClient } = renderApp(<SubtitleStudio job={job} />);
  const user = userEvent.setup();
  const editor = await screen.findByLabelText("Bản hiệu lực");
  await user.clear(editor);
  await user.type(editor, "Bản local chưa lưu");

  act(() => {
    queryClient.setQueryData(queryKeys.subtitles(job.job_id, "vi"), {
      ...workspace,
      editor_locked: true,
      output_stale: true,
    });
  });

  expect(await screen.findByText(/Editor tạm khóa/)).toBeInTheDocument();
  expect(screen.queryByText("Phụ đề đã thay đổi ở tab khác")).not.toBeInTheDocument();
  expect(screen.getByLabelText("Bản hiệu lực")).toHaveValue("Bản local chưa lưu");
});

test("offers a reset for saved overrides even with nothing dirty", async () => {
  // Nút "Reset toàn bộ" từng nằm trong thanh chỉ hiện khi dirty, nên ở đúng
  // trạng thái cần nó — có override đã lưu, chưa sửa gì thêm — nó không tồn tại.
  const overridden = { ...workspace, cues: [{ ...workspace.cues[0], effective_text: "Bản chỉnh tay", override_state: "manual" as const, overridden: true }] };
  vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(overridden)));
  renderApp(<SubtitleStudio job={job} />);

  expect(await screen.findByRole("button", { name: "Reset toàn bộ" })).toBeInTheDocument();
  expect(screen.getByText("Mọi chỉnh sửa đã được lưu")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Lưu" })).not.toBeInTheDocument();
});

test("asks before changing language while subtitle edits are dirty", async () => {
  const multiLanguageJob = {
    ...job,
    targets: ["vi", "en"],
    request: { ...job.request, targets: ["vi", "en"] },
  } satisfies JobDetail;
  const fetchMock = vi.fn(async () => jsonResponse(workspace));
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  renderApp(<SubtitleStudio job={multiLanguageJob} />);

  const editor = await screen.findByLabelText("Bản hiệu lực");
  await user.clear(editor);
  await user.type(editor, "Bản local chưa lưu");
  await user.click(screen.getByRole("button", { name: "EN" }));
  expect(await screen.findByText(/Bỏ 1 thay đổi chưa lưu/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Hủy" }));

  expect(screen.getByLabelText("Bản hiệu lực")).toHaveValue("Bản local chưa lưu");
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

test("scrolls the active cue into view while following playback", async () => {
  const secondCue = {
    ...workspace.cues[0],
    segment_id: 2,
    start: 4,
    end: 7,
    source_text: "第二句",
    model_text: "Câu thứ hai",
    effective_text: "Câu thứ hai",
  };
  vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ ...workspace, cues: [...workspace.cues, secondCue] })));
  const { container } = renderApp(<SubtitleStudio job={job} />);
  await screen.findByLabelText("Bản hiệu lực");
  const player = container.querySelector("video");
  expect(player).not.toBeNull();
  Object.defineProperty(player, "currentTime", { configurable: true, value: 5 });

  fireEvent.timeUpdate(player!);

  expect(virtualizerMocks.scrollToIndex).toHaveBeenCalledWith(1, { align: "auto" });
});

test("opens and closes the cue inspector as a mobile sheet", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(workspace)));
  const user = userEvent.setup();
  renderApp(<SubtitleStudio job={job} />);
  await screen.findByLabelText("Bản hiệu lực");

  await user.click(screen.getByRole("button", { name: "Chi tiết" }));
  expect(screen.getByRole("complementary", { name: "Chi tiết cue" })).toHaveClass("mobile-open");

  await user.click(screen.getByRole("button", { name: "Đóng inspector" }));
  expect(screen.getByRole("complementary", { name: "Chi tiết cue" })).not.toHaveClass("mobile-open");
});

test("moves selection to a cue that remains visible after filtering", async () => {
  const warningCue = {
    ...workspace.cues[0],
    segment_id: 2,
    source_text: "第二句",
    model_text: "Câu có cảnh báo",
    effective_text: "Câu có cảnh báo",
    warnings: [{ kind: "cps_over", detail: "Nhịp nhanh", value: 24, limit: 20 }],
  };
  vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ ...workspace, cues: [workspace.cues[0], warningCue] })));
  const user = userEvent.setup();
  renderApp(<SubtitleStudio job={job} />);

  expect(await screen.findByLabelText("Bản hiệu lực")).toHaveValue("Xin chào thế giới");
  await user.click(screen.getByRole("button", { name: "Cảnh báo" }));

  expect(screen.getByLabelText("Bản hiệu lực")).toHaveValue("Câu có cảnh báo");
});
