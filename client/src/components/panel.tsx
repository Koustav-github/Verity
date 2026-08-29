/** The repeating section shell used across the evidence report.
 *
 * Every panel previously re-declared the same border/heading/spacing classes inline,
 * which is why six different concerns ended up rendering at identical visual weight.
 * Centralising it is what lets the report read as distinct blocks instead of one ribbon.
 */

export function Panel({
  title,
  action,
  children,
  className = "",
}: {
  title: string;
  action?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`border border-rule bg-paper-raised px-4 py-3 font-mono text-sm ${className}`}
    >
      <div className="mb-2 flex items-baseline justify-between gap-3">
        <h3 className="text-xs uppercase tracking-[0.2em] text-brass">{title}</h3>
        {action}
      </div>
      {children}
    </section>
  );
}

/** A label/value line with the dotted leader the ledger look is built on. */
export function LeaderRow({
  label,
  value,
  valueClassName = "font-medium",
}: {
  label: React.ReactNode;
  value: React.ReactNode;
  valueClassName?: string;
}) {
  return (
    <div className="flex items-baseline gap-2 py-1">
      <span className="shrink-0 text-ink-soft">{label}</span>
      <span className="grow translate-y-[-3px] border-b border-dotted border-rule" />
      <span className={`shrink-0 ${valueClassName}`}>{value}</span>
    </div>
  );
}

export function RefreshButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="shrink-0 border border-ink px-2 py-1 text-[10px] uppercase tracking-[0.2em] hover:bg-ink hover:text-paper"
    >
      Refresh
    </button>
  );
}
