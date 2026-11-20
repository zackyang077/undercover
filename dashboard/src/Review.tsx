import { useEffect, useId, useRef, type ReactNode } from "react";

export function Diff({ lines }: Readonly<{ lines: string[] }>) {
  if (!lines.length) return <p className="muted">No text changes.</p>;
  return (
    <pre className="text-diff" aria-label="Text changes">
      {lines.map((line, index) => {
        let change = "context";
        if (line.startsWith("+") && !line.startsWith("+++")) change = "added";
        if (line.startsWith("-") && !line.startsWith("---")) change = "removed";
        return (
          <span key={`${String(index)}-${line}`} className={change}>
            {line}
            {"\n"}
          </span>
        );
      })}
    </pre>
  );
}

export function Confirm({
  title,
  children,
  confirmLabel,
  onConfirm,
  onCancel,
}: Readonly<{
  title: string;
  children: ReactNode;
  confirmLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
}>) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const dialog = ref.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, []);
  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      className="confirm-dialog"
      onCancel={onCancel}
    >
      <h2 id={titleId}>{title}</h2>
      {children}
      <div className="dialog-actions">
        <button className="secondary" onClick={onCancel}>
          Cancel
        </button>
        <button className="primary" onClick={onConfirm}>
          {confirmLabel}
        </button>
      </div>
    </dialog>
  );
}
