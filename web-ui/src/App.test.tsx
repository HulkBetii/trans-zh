import { screen } from "@testing-library/react";
import { vi } from "vitest";
import { App } from "./App";
import { renderApp } from "./test/render";

afterEach(() => vi.unstubAllGlobals());

test("does not read a dead server as an empty workspace", async () => {
  // Trước đây HomeRoute chỉ xử lý isLoading, nên server chết là chuyển thẳng sang
  // /new — người dùng đọc thành "chưa có công việc nào" rồi tạo lại thứ đã có.
  vi.stubGlobal("fetch", vi.fn(async () => new Response("boom", { status: 500 })));

  renderApp(<App />, "/");

  expect(await screen.findByText("Không đọc được danh sách công việc")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Thử lại/ })).toBeInTheDocument();
});
