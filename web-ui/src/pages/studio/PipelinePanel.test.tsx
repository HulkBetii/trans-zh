import { render, screen } from "@testing-library/react";
import type { JobDetail } from "../../api/types";
import { PipelinePanel } from "./PipelinePanel";

test("shows ASR as indeterminate even when the run has overall progress", () => {
  const job = {
    job_id: "job-1",
    title: "Episode",
    targets: ["vi"],
    execution_status: "running",
    quality_status: "needs_review",
    progress: 0.18,
    message: "",
    request: { source: { kind: "local", value: "D:\\episode.mp4" }, targets: ["vi"], formats: ["srt"], bilingual: false, output_dir: "output/job-1" },
    stages: [{ id: "s1", status: "running", cache_hit: false }],
    artifacts: [],
    allowed_actions: ["cancel"],
    glossary_stale: false,
    active_run: {
      run_id: "run-1",
      job_id: "job-1",
      lane: "pipeline",
      kind: "pipeline",
      from_stage: "s0",
      status: "running",
      stage: "s1",
      progress: 0.18,
      message: "Đang nhận dạng giọng nói",
      event_seq: 2,
    },
  } satisfies JobDetail;

  const { container } = render(<PipelinePanel job={job} descriptors={[{ id: "s1", label: "Nhận dạng giọng nói" }]} />);

  expect(container.querySelector(".progress-track.indeterminate")).toBeInTheDocument();
  expect(container.querySelector(".live-run-head code")).toHaveTextContent("—");
  expect(screen.queryByText("18%")).not.toBeInTheDocument();
  expect(screen.getByText(/tiến độ phụ thuộc độ dài và phần cứng/i)).toBeInTheDocument();
});
