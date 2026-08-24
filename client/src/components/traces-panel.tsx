"use client";

import { useEffect, useState } from "react";
import { fetchTelemetry, type TelemetryTrace } from "@/lib/verity";

function ms(value: number | null) {
  return value == null ? "—" : `${value.toFixed(2)} ms`;
}

const STATUS_CLASS: Record<TelemetryTrace["status"], string> = {
  ok: "text-pass",
  error: "text-fail",
  timeout: "text-fail",
};

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

  if (error) {
    return <p className="mt-6 font-mono text-xs text-fail">{error}</p>;
  }
  if (!traces) {
    return <p className="mt-6 font-mono text-xs text-ink-soft">Reading traces…</p>;
  }

  return (
    <div className="mt-6 border-t border-rule pt-4 font-mono text-sm">
      <div className="mb-2 flex items-baseline justify-between">
        <h3 className="text-xs uppercase tracking-[0.2em] text-brass">
          Recent calls ({traces.length})
        </h3>
        <button
          type="button"
          onClick={load}
          className="border border-ink px-2 py-1 text-[10px] uppercase tracking-[0.2em] hover:bg-ink hover:text-paper"
        >
          Refresh
        </button>
      </div>

      {traces.length === 0 ? (
        <p className="text-xs text-ink-soft">
          No calls recorded yet — this fills in as `/predict` is called, or as
          `verity.monitor()` reports from a customer-hosted model.
        </p>
      ) : (
        <ul className="space-y-1">
          {traces.map((trace) => (
            <li
              key={`${trace.prediction_id ?? "unknown"}_${trace.occurred_at}`}
              className="flex items-baseline gap-2 text-xs"
            >
              <span className="shrink-0 text-ink-soft">
                {new Date(trace.occurred_at).toLocaleString()}
              </span>
              <span className="grow border-b border-dotted border-rule translate-y-[-3px]" />
              <span className={`shrink-0 uppercase ${STATUS_CLASS[trace.status]}`}>
                {trace.status}
              </span>
              <span className="shrink-0 w-20 text-right font-medium">
                {ms(trace.latency_ms)}
              </span>
              {trace.error_type && (
                <span className="shrink-0 text-fail">{trace.error_type}</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
