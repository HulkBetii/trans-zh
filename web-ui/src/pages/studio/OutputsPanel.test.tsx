import { render, screen } from "@testing-library/react";
import type { JobDetail } from "../../api/types";
import { OutputsPanel } from "./OutputsPanel";

const job = {
  job_id: "job-output",
  title: "Episode",
  execution_status: "completed",
  quality_status: "needs_review",
  targets: ["vi"],
  progress: 1,
  message: "",
  request: { source: { kind: "local", value: "D:\\episode.mp4" }, targets: ["vi"], formats: ["srt"], bilingual: false, output_dir: "output/job-output" },
  stages: [],
  artifacts: [{ artifact_id: "subtitle-vi-srt", name: "episode.vi.srt", kind: "subtitle", language: "vi", format: "srt", size_bytes: 128, created_at: "2026-08-15T00:00:00Z", state: "current" }],
  allowed_actions: ["render"],
  glossary_stale: false,
} satisfies JobDetail;

test("renders outputs as an accessible table", () => {
  render(<OutputsPanel job={job} onRender={() => undefined} renderPending={false} />);

  const table = screen.getByRole("table", { name: "Danh sách output" });
  expect(table).toBeInTheDocument();
  expect(screen.getAllByRole("columnheader")).toHaveLength(6);
  expect(screen.getByRole("rowheader", { name: /episode.vi.srt/ })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Tải episode.vi.srt" })).toBeInTheDocument();
});

test("disables the empty-state render action while a request is pending", () => {
  render(<OutputsPanel job={{ ...job, artifacts: [] }} onRender={() => undefined} renderPending />);

  expect(screen.getByRole("button", { name: "Đang render…" })).toBeDisabled();
});
