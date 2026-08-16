import { act, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { queryKeys } from "../../api/queries";
import type { JobDetail } from "../../api/types";
import { jsonResponse, renderApp } from "../../test/render";
import { GlossaryEditor } from "./GlossaryEditor";

const job = {
  job_id: "job-1", title: "Episode", execution_status: "completed", quality_status: "needs_review",
  targets: ["vi"], progress: 1, message: "", glossary_stale: false,
  request: { source: { kind: "local", value: "D:\\episode.mp4" }, targets: ["vi"], formats: ["srt"], bilingual: false, output_dir: "output/job-1" },
  stages: [], artifacts: [], allowed_actions: ["retranslate"],
} satisfies JobDetail;

test("preserves local glossary edits when optimistic revision conflicts", async () => {
  const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method === "PUT") return jsonResponse({ detail: { code: "revision_conflict", current_revision: "rev-2" } }, 409);
    return jsonResponse({
      revision: "rev-1",
      document: {
        version: 1,
        terms: [{ zh: "小明", pinyin: "Xiao Ming", vi: "Tiểu Minh", en: "Xiao Ming", type: "person", keep_source: false, note: "" }],
        address_terms: [],
        style: { speech_register: "tự nhiên", narrator_self_vi: "tôi", audience_vi: "các bạn", subject_third_person_vi: "anh ấy" },
      },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  renderApp(<GlossaryEditor job={job} />);

  const input = await screen.findByLabelText("Tiếng Việt 1");
  await user.clear(input);
  await user.type(input, "Minh");
  await user.click(screen.getByRole("button", { name: "Lưu" }));

  expect(await screen.findByText("Glossary đã thay đổi ở nơi khác")).toBeInTheDocument();
  expect(screen.getByDisplayValue("Minh")).toBeInTheDocument();
});

const glossaryDoc = (vi_: string) => ({
  version: 1,
  terms: [{ zh: "小明", pinyin: "", vi: vi_, en: "", type: "person", keep_source: false, note: "" }],
  address_terms: [],
  style: { speech_register: "", narrator_self_vi: "", audience_vi: "", subject_third_person_vi: "" },
});

test("keeps unsaved glossary edits when the server revision changes", async () => {
  // Trước đây form khóa theo `key={revision}`, nên server đổi bản là remount và
  // bản nháp biến mất, không banner, không hỏi han.
  vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ revision: "rev-1", document: glossaryDoc("Tiểu Minh") })));
  const { queryClient } = renderApp(<GlossaryEditor job={job} />);
  const user = userEvent.setup();
  const input = await screen.findByLabelText("Tiếng Việt 1");
  await user.clear(input);
  await user.type(input, "Bản nháp chưa lưu");

  act(() => {
    queryClient.setQueryData(queryKeys.glossary(job.job_id), {
      revision: "rev-9", editor_locked: false, document: glossaryDoc("Bản máy mới"),
    });
  });

  expect(await screen.findByText("Glossary đã thay đổi ở nơi khác")).toBeInTheDocument();
  expect(screen.getByLabelText("Tiếng Việt 1")).toHaveValue("Bản nháp chưa lưu");
});

test("takes the server version only when the discard is asked for", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ revision: "rev-1", document: glossaryDoc("Tiểu Minh") })));
  const { queryClient } = renderApp(<GlossaryEditor job={job} />);
  const user = userEvent.setup();
  const input = await screen.findByLabelText("Tiếng Việt 1");
  await user.clear(input);
  await user.type(input, "Bản nháp chưa lưu");

  act(() => {
    queryClient.setQueryData(queryKeys.glossary(job.job_id), {
      revision: "rev-9", editor_locked: false, document: glossaryDoc("Bản máy mới"),
    });
  });
  await user.click(await screen.findByRole("button", { name: /Bỏ chỉnh sửa/ }));

  expect(screen.getByLabelText("Tiếng Việt 1")).toHaveValue("Bản máy mới");
  expect(screen.queryByText("Glossary đã thay đổi ở nơi khác")).not.toBeInTheDocument();
});

test("keeps the editor lock in sync without a remount", async () => {
  // Khóa editor đọc thẳng từ props; nếu ai đó đưa nó vào state thì test này đổ.
  vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ revision: "rev-1", editor_locked: false, document: glossaryDoc("Tiểu Minh") })));
  const { queryClient } = renderApp(<GlossaryEditor job={job} />);
  await screen.findByLabelText("Tiếng Việt 1");

  act(() => {
    queryClient.setQueryData(queryKeys.glossary(job.job_id), {
      revision: "rev-1", editor_locked: true, lock_reason: "tts_run_active", document: glossaryDoc("Tiểu Minh"),
    });
  });

  expect(await screen.findByText(/Tác vụ audio đang dùng glossary/)).toBeInTheDocument();
});

test("keeps focus while editing the Chinese term used to identify a row", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({
    revision: "rev-1",
    editor_locked: false,
    document: {
      version: 1,
      terms: [{ zh: "小", pinyin: "", vi: "", en: "", type: "person", keep_source: false, note: "" }],
      address_terms: [],
      style: { speech_register: "", narrator_self_vi: "", audience_vi: "", subject_third_person_vi: "" },
    },
  })));
  const user = userEvent.setup();
  renderApp(<GlossaryEditor job={job} />);

  const input = await screen.findByLabelText("Thuật ngữ Trung 1");
  await user.type(input, "明");

  expect(input).toHaveValue("小明");
  expect(input).toHaveFocus();
});

test("flags an address pair that is missing one of its two sides", async () => {
  // Nhánh "address" của ValidationError có từ đầu nhưng chưa bao giờ được ghi vào.
  vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({
    revision: "rev-1",
    document: {
      ...glossaryDoc("Tiểu Minh"),
      address_terms: [{ speaker: "A", addressee: "", vi_self: "tôi", vi_other: "anh", basis: "" }],
    },
  })));
  renderApp(<GlossaryEditor job={job} />);

  expect(await screen.findByText(/Cần cả người nói và người nghe/)).toBeInTheDocument();
});

test("flags two rows pinning the same speaker and addressee", async () => {
  const pair = { speaker: "A", addressee: "B", vi_self: "tôi", vi_other: "anh", basis: "" };
  vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({
    revision: "rev-1",
    document: { ...glossaryDoc("Tiểu Minh"), address_terms: [pair, { ...pair, vi_self: "em" }] },
  })));
  renderApp(<GlossaryEditor job={job} />);

  expect(await screen.findByText(/Trùng cặp xưng hô/)).toBeInTheDocument();
});
