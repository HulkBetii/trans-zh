import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { vi } from "vitest";
import { jsonResponse, renderApp } from "../test/render";
import { FileBrowserDialog } from "./FileBrowserDialog";

function DialogHarness() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)}>Mở trình duyệt</button>
      <FileBrowserDialog open={open} onClose={() => setOpen(false)} onSelect={() => setOpen(false)} />
    </>
  );
}

test("traps focus and restores it after closing the file browser", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => jsonResponse([{ label: "Media", path: "D:\\Media" }])));
  const user = userEvent.setup();
  renderApp(<DialogHarness />);

  const trigger = screen.getByRole("button", { name: "Mở trình duyệt" });
  await user.click(trigger);

  const close = screen.getByRole("button", { name: "Đóng" });
  expect(close).toHaveFocus();
  const root = await screen.findByRole("button", { name: /Media/ });
  await user.tab({ shift: true });
  expect(root).toHaveFocus();

  await user.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
});
