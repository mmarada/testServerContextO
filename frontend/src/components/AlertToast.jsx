import { useEffect } from "react";

function toneClass(tone) {
  if (tone === "error") return "border-ctx-err text-ctx-err";
  if (tone === "success") return "border-ctx-ok text-ctx-ok";
  if (tone === "warn") return "border-ctx-warn text-ctx-warn";
  return "border-ctx-border text-ctx-muted";
}

export function AlertToast({ toasts, onDismiss }) {
  return (
    <div className="pointer-events-none fixed right-3 top-3 z-50 flex w-[min(360px,calc(100vw-24px))] flex-col gap-2">
      {toasts.map((t) => (
        <ToastItem key={t.id} toast={t} onDismiss={onDismiss} />
      ))}
    </div>
  );
}

function ToastItem({ toast, onDismiss }) {
  useEffect(() => {
    const id = setTimeout(() => onDismiss(toast.id), 5000);
    return () => clearTimeout(id);
  }, [toast.id, onDismiss]);

  return (
    <div
      className={`pointer-events-auto border bg-ctx-card px-3 py-2 text-[11px] ${toneClass(
        toast.tone
      )}`}
    >
      <div className="flex justify-between gap-2">
        <span className="text-ctx-text">{toast.message}</span>
        <button
          type="button"
          className="shrink-0 text-ctx-muted hover:text-ctx-text"
          onClick={() => onDismiss(toast.id)}
        >
          ×
        </button>
      </div>
    </div>
  );
}
