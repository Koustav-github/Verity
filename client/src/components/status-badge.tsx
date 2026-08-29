/** One vocabulary for every status the pipeline produces.
 *
 * Version statuses and deployment statuses are different enumerations that a reader
 * scans the same way, so they share one component rather than each growing their own
 * colour logic. Square, not rounded: the theme sets --radius-none deliberately.
 */

const TONE = {
  pass: "border-pass text-pass",
  fail: "border-fail text-fail",
  working: "border-brass text-brass",
  muted: "border-rule text-ink-soft",
} as const;

// Anything unlisted falls through to `muted` rather than throwing — a status added
// server-side should render plainly, not crash the page that lists it.
const TONE_BY_STATUS: Record<string, keyof typeof TONE> = {
  production: "pass",
  live: "pass",
  staging_failed: "fail",
  failed: "fail",
  timeout: "fail",
  error: "fail",
  pending: "working",
  staging: "working",
  building: "working",
  archived: "muted",
  stopped: "muted",
  ok: "pass",
};

export function StatusBadge({
  status,
  className = "",
}: {
  status: string;
  className?: string;
}) {
  const tone = TONE[TONE_BY_STATUS[status] ?? "muted"];
  return (
    <span
      className={`shrink-0 border px-1.5 py-0.5 text-[10px] uppercase tracking-[0.15em] ${tone} ${className}`}
    >
      {status.replace(/_/g, " ")}
    </span>
  );
}
