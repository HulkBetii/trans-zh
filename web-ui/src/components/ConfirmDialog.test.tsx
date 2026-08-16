import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { useConfirm } from "../hooks/useConfirm";
import { renderApp } from "../test/render";

function Harness({ request }: { request: Parameters<ReturnType<typeof useConfirm>>[0] }) {
  const confirm = useConfirm();
  const [answer, setAnswer] = useState<string>("chưa hỏi");
  return (
    <>
      <button onClick={() => void confirm(request).then((ok) => setAnswer(ok ? "đồng ý" : "từ chối"))}>
        Hỏi
      </button>
      <output>{answer}</output>
    </>
  );
}

test("resolves true only when the confirm button is pressed", async () => {
  const user = userEvent.setup();
  renderApp(<Harness request={{ title: "Xóa hết?", confirmLabel: "Xóa" }} />);

  await user.click(screen.getByRole("button", { name: "Hỏi" }));
  await user.click(await screen.findByRole("button", { name: "Xóa" }));

  expect(screen.getByRole("status")).toHaveTextContent("đồng ý");
});

test("resolves false on cancel and leaves nothing on screen", async () => {
  const user = userEvent.setup();
  renderApp(<Harness request={{ title: "Xóa hết?" }} />);

  await user.click(screen.getByRole("button", { name: "Hỏi" }));
  await user.click(await screen.findByRole("button", { name: "Hủy" }));

  expect(screen.getByRole("status")).toHaveTextContent("từ chối");
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
});

test("resolves false on Escape", async () => {
  const user = userEvent.setup();
  renderApp(<Harness request={{ title: "Xóa hết?" }} />);

  await user.click(screen.getByRole("button", { name: "Hỏi" }));
  await screen.findByRole("alertdialog");
  await user.keyboard("{Escape}");

  expect(screen.getByRole("status")).toHaveTextContent("từ chối");
});

test("shows the cost on its own line instead of inside the sentence", async () => {
  // window.confirm chỉ nhận một chuỗi, nên giá bị nhét chung câu với việc làm.
  const user = userEvent.setup();
  renderApp(
    <Harness request={{ title: "Tạo MP3 lồng tiếng?", cost: "Cần tổng hợp 12 cue mới: tốn 12 lượt TTS." }} />,
  );

  await user.click(screen.getByRole("button", { name: "Hỏi" }));

  expect(await screen.findByText(/tốn 12 lượt TTS/)).toBeInTheDocument();
});

test("puts the cursor on cancel, so a reflex Enter does nothing", async () => {
  const user = userEvent.setup();
  renderApp(<Harness request={{ title: "Xóa hết?", tone: "danger" }} />);

  await user.click(screen.getByRole("button", { name: "Hỏi" }));

  expect(await screen.findByRole("button", { name: "Hủy" })).toHaveFocus();
});

test("never leaves an earlier question unanswered", async () => {
  // Promise treo vĩnh viễn là một cách rất im lặng để treo cả một luồng thao tác.
  const user = userEvent.setup();
  function TwoQuestions() {
    const confirm = useConfirm();
    const [log, setLog] = useState<string[]>([]);
    return (
      <>
        <button
          onClick={() => {
            void confirm({ title: "Câu một" }).then((ok) => setLog((l) => [...l, `một:${ok}`]));
            void confirm({ title: "Câu hai" }).then((ok) => setLog((l) => [...l, `hai:${ok}`]));
          }}
        >
          Hỏi hai lần
        </button>
        <output>{log.join(" ")}</output>
      </>
    );
  }
  renderApp(<TwoQuestions />);

  await user.click(screen.getByRole("button", { name: "Hỏi hai lần" }));
  expect(await screen.findByText("Câu hai")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Tiếp tục" }));

  expect(screen.getByRole("status")).toHaveTextContent("một:false hai:true");
});
