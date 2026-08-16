import { createContext, useContext } from "react";

export interface ConfirmRequest {
  title: string;
  detail?: string;
  /**
   * Chi phí của thao tác, hiện thành dòng riêng.
   *
   * `window.confirm` chỉ nhận một chuỗi, nên "Hiệu chuẩn dùng 8 mẫu giọng và có
   * thể tiêu tốn 8 lượt TTS. Tiếp tục?" phải nhét cả việc lẫn giá vào một câu —
   * đúng thứ người ta đọc lướt.
   */
  cost?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: "default" | "danger";
}

export type Confirm = (request: ConfirmRequest) => Promise<boolean>;

export const ConfirmContext = createContext<Confirm | null>(null);

export function useConfirm(): Confirm {
  const confirm = useContext(ConfirmContext);
  if (!confirm) throw new Error("useConfirm cần được bọc trong ConfirmProvider");
  return confirm;
}
