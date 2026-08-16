import { useEffect, useRef } from "react";
import { useBlocker } from "react-router-dom";
import { useConfirm } from "./useConfirm";

export function useUnsavedChanges(isDirty: boolean, message = "Rời trang và bỏ các thay đổi chưa lưu?") {
  const blocker = useBlocker(isDirty);
  const confirm = useConfirm();
  // `blocker` là object mới mỗi lần render, nên effect chạy lại liên tục trong
  // lúc câu hỏi còn treo. Không có cờ này thì mỗi render là một dialog nữa.
  const askingRef = useRef(false);

  useEffect(() => {
    if (blocker.state !== "blocked") {
      askingRef.current = false;
      return;
    }
    if (askingRef.current) return;
    askingRef.current = true;

    let abandoned = false;
    void confirm({
      title: message,
      detail: "Các chỉnh sửa chưa lưu trên màn hình này sẽ mất.",
      confirmLabel: "Rời trang",
      tone: "danger",
    }).then((ok) => {
      if (abandoned) return;
      askingRef.current = false;
      if (ok) blocker.proceed?.();
      else blocker.reset?.();
    });
    return () => {
      abandoned = true;
    };
  }, [blocker, confirm, message]);

  useEffect(() => {
    if (!isDirty) return;

    // Đóng tab thì chỉ trình duyệt hỏi được; dialog trong app không kịp hiện.
    const beforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", beforeUnload);
    return () => {
      window.removeEventListener("beforeunload", beforeUnload);
    };
  }, [isDirty]);
}
