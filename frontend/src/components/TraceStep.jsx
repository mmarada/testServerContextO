export function TraceStep({
  status,
  title,
  detail,
  children,
  isLast,
  stepNumber,
}) {
  const icon =
    status === "done" ? (
      <span
        className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-ctx-ok text-[10px] text-ctx-ok"
        aria-hidden
      >
        ✓
      </span>
    ) : status === "active" ? (
      <span
        className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-ctx-accent"
        aria-hidden
      >
        <span className="h-3 w-3 animate-spin rounded-full border-2 border-ctx-accent border-t-transparent" />
      </span>
    ) : (
      <span
        className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-ctx-muted text-[10px] text-ctx-muted"
        aria-hidden
      >
        {stepNumber}
      </span>
    );

  return (
    <div className="flex gap-3">
      <div className="flex w-8 flex-col items-center">
        {icon}
        {!isLast && (
          <div
            className="mt-1 w-px flex-1 min-h-[24px] bg-ctx-border"
            aria-hidden
          />
        )}
      </div>
      <div className="min-w-0 flex-1 pb-6">
        <div className="text-[12px] font-semibold text-ctx-text">{title}</div>
        {detail && (
          <div className="mt-1 text-[11px] leading-relaxed text-ctx-muted">
            {detail}
          </div>
        )}
        {children && <div className="mt-3 space-y-2">{children}</div>}
      </div>
    </div>
  );
}
