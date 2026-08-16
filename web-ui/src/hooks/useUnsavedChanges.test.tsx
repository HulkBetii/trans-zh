import { act, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { Link, Route, Routes } from "react-router-dom";
import { renderApp } from "../test/render";
import { useUnsavedChanges } from "./useUnsavedChanges";

test("blocks in-app navigation while edits are dirty", async () => {
  const user = userEvent.setup();
  renderApp(
    <Routes>
      <Route path="/edit" element={<DirtyForm />} />
      <Route path="/other" element={<h1>Trang khác</h1>} />
    </Routes>,
    "/edit",
  );

  await user.type(screen.getByLabelText("Nội dung"), "đã sửa");
  await user.click(screen.getByRole("link", { name: "Rời trang" }));
  await user.click(await screen.findByRole("button", { name: "Hủy" }));

  expect(screen.getByLabelText("Nội dung")).toHaveValue("đã sửa");
  expect(screen.queryByText("Trang khác")).not.toBeInTheDocument();
});

test("blocks browser history navigation while edits are dirty", async () => {
  const user = userEvent.setup();
  const { router } = renderApp(
    <Routes>
      <Route path="/edit" element={<DirtyForm />} />
      <Route path="/other" element={<h1>Trang khác</h1>} />
    </Routes>,
    ["/other", "/edit"],
  );

  await user.type(screen.getByLabelText("Nội dung"), "đã sửa");
  await act(async () => router.navigate(-1));
  await user.click(await screen.findByRole("button", { name: "Hủy" }));

  expect(router.state.location.pathname).toBe("/edit");
  expect(screen.getByLabelText("Nội dung")).toHaveValue("đã sửa");
});

function DirtyForm() {
  const [value, setValue] = useState("");
  useUnsavedChanges(Boolean(value));
  return <><label>Nội dung<input value={value} onChange={(event) => setValue(event.target.value)} /></label><Link to="/other">Rời trang</Link></>;
}
