import { useEffect, useRef } from "react";

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

/** Dialog đang mở, theo thứ tự mở. Chỉ cái trên cùng được nhận Escape. */
const openDialogs: symbol[] = [];

interface DialogFocusOptions {
  /**
   * Selector của phần tử nhận focus đầu tiên. Không đặt thì lấy phần tử focus
   * được đầu tiên — thường là nút đóng ở header, và điều đó vô hiệu hóa mọi
   * `autoFocus` trong dialog.
   */
  initialFocus?: string;
}

export function useDialogFocus<T extends HTMLElement>(
  open: boolean,
  onClose: () => void,
  options: DialogFocusOptions = {},
) {
  const dialogRef = useRef<T>(null);
  const onCloseRef = useRef(onClose);
  const { initialFocus } = options;

  // Trong effect, không phải giữa render: gán ref lúc render là tác dụng phụ
  // trong pha lẽ ra phải thuần.
  useEffect(() => {
    onCloseRef.current = onClose;
  });

  useEffect(() => {
    if (!open) return;

    const token = Symbol("dialog");
    openDialogs.push(token);
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const dialog = dialogRef.current;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const focusableElements = () => Array.from(dialog?.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR) ?? []);
    const preferred = initialFocus
      ? dialog?.querySelector<HTMLElement>(initialFocus) ?? null
      : null;
    (preferred ?? focusableElements()[0])?.focus();

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        // Chỉ dialog trên cùng đóng. Nghe ở cấp document mà không xét thứ tự thì
        // một phím Escape đóng luôn cả dialog cha bên dưới.
        if (openDialogs[openDialogs.length - 1] !== token) return;
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab") return;

      const elements = focusableElements();
      if (elements.length === 0) {
        event.preventDefault();
        return;
      }

      const first = elements[0];
      const last = elements[elements.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      const index = openDialogs.indexOf(token);
      if (index >= 0) openDialogs.splice(index, 1);
      document.body.style.overflow = previousOverflow;
      previousFocus?.focus();
    };
  }, [initialFocus, open]);

  return dialogRef;
}
