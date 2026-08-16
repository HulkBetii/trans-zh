import { AlertTriangle, CircleDollarSign } from "lucide-react";
import { useCallback, useRef, useState, type ReactNode } from "react";
import { useDialogFocus } from "../hooks/useDialogFocus";
import { ConfirmContext, type Confirm, type ConfirmRequest } from "../hooks/useConfirm";

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [request, setRequest] = useState<ConfirmRequest | null>(null);
  const resolverRef = useRef<((ok: boolean) => void) | null>(null);

  const confirm = useCallback<Confirm>(
    (next) =>
      new Promise<boolean>((resolve) => {
        // Hỏi chồng lên một câu hỏi chưa trả lời thì câu cũ coi như bị hủy —
        // không bao giờ để một Promise treo vĩnh viễn.
        resolverRef.current?.(false);
        resolverRef.current = resolve;
        setRequest(next);
      }),
    [],
  );

  const settle = useCallback((ok: boolean) => {
    resolverRef.current?.(ok);
    resolverRef.current = null;
    setRequest(null);
  }, []);

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {request && <ConfirmSheet request={request} onSettle={settle} />}
    </ConfirmContext.Provider>
  );
}

function ConfirmSheet({ request, onSettle }: { request: ConfirmRequest; onSettle: (ok: boolean) => void }) {
  const cancel = useCallback(() => onSettle(false), [onSettle]);
  // Focus nút hủy, không phải nút xác nhận: mọi câu hỏi ở đây đều hỏi trước một
  // việc phá hủy hoặc tốn tiền, nên Enter theo phản xạ phải là không làm gì.
  const dialogRef = useDialogFocus<HTMLElement>(true, cancel, {
    initialFocus: ".confirm-cancel",
  });
  const danger = request.tone === "danger";

  return (
    <div
      className="modal-layer"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) cancel();
      }}
    >
      <section
        ref={dialogRef}
        className="confirm-dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
      >
        <div className="confirm-head">
          <AlertTriangle size={19} className={danger ? "confirm-danger" : undefined} />
          <h2 id="confirm-title">{request.title}</h2>
        </div>
        {request.detail && <p className="confirm-detail">{request.detail}</p>}
        {request.cost && (
          <p className="confirm-cost">
            <CircleDollarSign size={15} />
            {request.cost}
          </p>
        )}
        <div className="confirm-actions">
          <button className="secondary-button confirm-cancel" onClick={cancel}>
            {request.cancelLabel ?? "Hủy"}
          </button>
          <button
            className={danger ? "secondary-button danger" : "primary-button"}
            onClick={() => onSettle(true)}
          >
            {request.confirmLabel ?? "Tiếp tục"}
          </button>
        </div>
      </section>
    </div>
  );
}
