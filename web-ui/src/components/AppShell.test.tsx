import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { vi } from "vitest";
import { jsonResponse, renderApp } from "../test/render";
import { AppShell } from "./AppShell";

test("marks Settings active without marking Studio active", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ items: [], total: 0, page: 1, page_size: 20 })));
  renderApp(
    <Routes>
      <Route element={<AppShell />}>
        <Route path="settings" element={<div>Cài đặt ứng dụng</div>} />
      </Route>
    </Routes>,
    "/settings",
  );

  const rail = screen.getByRole("complementary", { name: "Điều hướng chính" });
  expect(within(rail).getByRole("link", { name: "Cài đặt" })).toHaveClass("active");
  expect(within(rail).getByRole("link", { name: "Studio" })).not.toHaveClass("active");
  expect(screen.getByRole("link", { name: "Mở cài đặt" })).toHaveAttribute("href", "/settings");
});

test("traps focus in the job drawer and restores it after Escape", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ items: [], total: 0, page: 1, page_size: 20 })));
  const user = userEvent.setup();
  renderApp(
    <Routes>
      <Route element={<AppShell />}>
        <Route path="new" element={<div>Job mới</div>} />
      </Route>
    </Routes>,
    "/new",
  );

  const trigger = screen.getByRole("button", { name: "Mở danh sách công việc" });
  trigger.focus();
  await user.click(trigger);

  const drawer = screen.getByRole("dialog", { name: "Danh sách công việc" });
  const close = within(drawer).getByRole("button", { name: "Đóng danh sách" });
  expect(close).toHaveFocus();
  expect(document.body.style.overflow).toBe("hidden");

  await user.tab({ shift: true });
  expect(within(drawer).getByRole("button", { name: "Đã xong" })).toHaveFocus();
  await user.tab();
  expect(close).toHaveFocus();

  await user.keyboard("{Escape}");
  expect(screen.queryByRole("dialog", { name: "Danh sách công việc" })).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
  expect(document.body.style.overflow).toBe("");
});
