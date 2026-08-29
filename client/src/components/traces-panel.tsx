"use client";

import { useEffect, useState } from "react";
import { fetchTelemetry, type TelemetryTrace } from "@/lib/verity";
import { Panel, LeaderRow, RefreshButton } from "./panel";
import { StatusBadge } from "./status-badge";

function ms(value: number | null) {
  return value == null ? "—" : `${value.toFixed(2)} ms`;
}

/** One call, expandable.
 *
 * The row itself carries what you scan for — when, outcome, how slow. The expansion
 * carries `prediction_id`, which is the only way to report a delayed outcome back
 * against this call via POST /predictions/{id}/outcomes, and is far too long to sit in
 * the row without crowding out everything else.
 */
function TraceRow({ trace }: { trace: TelemetryTrace }) {
  const [open, setOpen] = useState(false);

  return (
    <li className="border-b border-dotted border-rule last:border-b-0">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-baseline gap-2 py-1.5 text-left text-xs hover:text-brass"
      >
        <span aria-hidden className="shrink-0 text-ink-soft">
          {open ? "▾" : "▸"}
        </span>
        <span className="shrink-0 text-ink-soft">
          {new Date(trace.occurred_at).toLocaleTimeString()}
        </span>
        <span className="grow translate-y-[-3px] border-b border-dotted border-rule" />
        {trace.error_type && (
          <span className="shrink-0 truncate text-fail">{trace.error_type}</span>
        )}
        <StatusBadge status={trace.status} />
        <span className="w-20 shrink-0 text-right font-medium tabular-nums">
          {ms(trace.latency_ms)}
        </span>
      </button>

      {open && (
        <div className="mb-2 ml-4 border-l-2 border-rule pl-3 text-xs">
          <LeaderRow
            label="prediction_id"
            value={
              <span className="break-all">{trace.prediction_id ?? "— (not recorded)"}</span>
            }
          />
          <LeaderRow
            label="occurred_at"
            value={new Date(trace.occurred_at).toLocaleString()}
          />
          <LeaderRow label="status" value={trace.status} />
          <LeaderRow label="error_type" value={trace.error_type ?? "—"} />
          <p className="mt-1 text-[11px] text-ink-soft">
            Inputs and predictions are recorded server-side but deliberately not served
            here — every route is unauthenticated until V1.5.
          </p>
        </div>
      )}
    </li>
  );
}

export function TracesPanel({ modelVersionId }: { modelVersionId: string }) {
  const [traces, setTraces] = useState<TelemetryTrace[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    try {
      const summary = await fetchTelemetry(modelVersionId);
      setTraces(summary.recent_events);
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modelVersionId]);

  return (
    <Panel
      title={`Recent calls${traces ? ` (${traces.length})` : ""}`}
      action={<RefreshButton onClick={load} />}
    >
      {error && <p className="text-xs text-fail">{error}</p>}
      {!error && !traces && <p className="text-xs text-ink-soft">Reading traces…</p>}

      {traces && traces.length === 0 && (
        <p className="text-xs text-ink-soft">
          No calls recorded yet — this fills in as <code>/predict</code> is called, or as{" "}
          <code>verity.monitor()</code> reports from a customer-hosted model.
        </p>
      )}

      {traces && traces.length > 0 && (
        <ul>
          {traces.map((trace) => (
            <TraceRow
              key={`${trace.prediction_id ?? "unknown"}_${trace.occurred_at}`}
              trace={trace}
            />
          ))}
        </ul>
      )}
    </Panel>
  );
}
