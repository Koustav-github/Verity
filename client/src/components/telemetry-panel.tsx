"use client";

import { useEffect, useState } from "react";
import { fetchTelemetry, type TelemetrySummary } from "@/lib/verity";

function ms(value: number | null) {
  return value == null ? "—" : `${value.toFixed(2)} ms`;
}

export function TelemetryPanel({ modelVersionId }: { modelVersionId: string }) {
  const [summary, setSummary] = useState<TelemetrySummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    try {
      setSummary(await fetchTelemetry(modelVersionId));
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
  if (!summary) {
    return <p className="mt-6 font-mono text-xs text-ink-soft">Reading telemetry…</p>;
  }

  return (
    <div className="mt-6 border-t border-rule pt-4 font-mono text-sm">
      <div className="mb-2 flex items-baseline justify-between">
        <h3 className="text-xs uppercase tracking-[0.2em] text-brass">
          Live traffic — last {summary.hours}h
        </h3>
        <button
          type="button"
          onClick={load}
          className="border border-ink px-2 py-1 text-[10px] uppercase tracking-[0.2em] hover:bg-ink hover:text-paper"
        >
          Refresh
        </button>
      </div>

      {summary.request_count === 0 ? (
        <p className="text-xs text-ink-soft">
          No requests recorded yet. Wrap the model with{" "}
          <code>verity.monitor(model, model_version_id=&quot;{modelVersionId}&quot;)</code> in
          your serving process and call predict.
        </p>
      ) : (
        <>
          <div className="flex items-baseline gap-2 py-1">
            <span className="shrink-0 text-ink-soft">requests</span>
            <span className="grow border-b border-dotted border-rule translate-y-[-3px]" />
            <span className="shrink-0 font-medium">{summary.request_count}</span>
          </div>
          <div className="flex items-baseline gap-2 py-1">
            <span className="shrink-0 text-ink-soft">error rate</span>
            <span className="grow border-b border-dotted border-rule translate-y-[-3px]" />
            <span className="shrink-0 font-medium">
              {(summary.error_rate * 100).toFixed(1)}%
            </span>
          </div>
          <div className="flex items-baseline gap-2 py-1">
            <span className="shrink-0 text-ink-soft">latency p50 / p95 / p99</span>
            <span className="grow border-b border-dotted border-rule translate-y-[-3px]" />
            <span className="shrink-0 font-medium">
              {ms(summary.latency_p50_ms)} / {ms(summary.latency_p95_ms)} /{" "}
              {ms(summary.latency_p99_ms)}
            </span>
          </div>
          {summary.truncated && (
            <p className="mt-2 text-xs text-ink-soft">
              Window truncated at the read limit — showing the most recent events only.
            </p>
          )}
        </>
      )}

      {summary.eval_reference != null && (
        <p className="mt-3 text-xs text-ink-soft">
          Eval-time reference (sandbox feasibility, not a production baseline — nothing is
          compared or alerted on):{" "}
          {ms((summary.eval_reference as Record<string, number>).latency_p95_ms ?? null)} p95.
        </p>
      )}
    </div>
  );
}
