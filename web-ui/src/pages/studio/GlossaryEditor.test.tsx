import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
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
