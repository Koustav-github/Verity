"use client";

import { useEffect, useState } from "react";
import { fetchTelemetry, type TelemetrySummary } from "@/lib/verity";
import { Panel, LeaderRow, RefreshButton } from "./panel";

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

  return (
    <Panel
      title={summary ? `Live traffic — last ${summary.hours}h` : "Live traffic"}
      action={<RefreshButton onClick={load} />}
    >
      {error && <p className="text-xs text-fail">{error}</p>}
      {!error && !summary && <p className="text-xs text-ink-soft">Reading telemetry…</p>}

      {summary && summary.request_count === 0 && (
        <p className="text-xs text-ink-soft">
          No requests recorded yet. Call the model through{" "}
          <code>POST /users/&lt;user&gt;/models/&lt;name&gt;/predict</code>, or wrap it with{" "}
          <code>verity.monitor()</code> if you serve it yourself.
        </p>
      )}

      {summary && summary.request_count > 0 && (
        <>
          <LeaderRow label="requests" value={summary.request_count} />
          <LeaderRow
            label="error rate"
            value={`${(summary.error_rate * 100).toFixed(1)}%`}
            valueClassName={
              summary.error_rate > 0 ? "font-medium text-fail" : "font-medium text-pass"
            }
          />
          <LeaderRow
            label="latency p50 / p95 / p99"
            value={
              <span className="tabular-nums">
                {ms(summary.latency_p50_ms)} / {ms(summary.latency_p95_ms)} /{" "}
                {ms(summary.latency_p99_ms)}
              </span>
            }
          />
          {summary.truncated && (
            <p className="mt-2 text-xs text-ink-soft">
              Window truncated at the read limit — showing the most recent events only.
            </p>
          )}
        </>
      )}

      {summary?.eval_reference != null && (
        <p className="mt-3 border-t border-dotted border-rule pt-2 text-[11px] text-ink-soft">
          Eval-time reference:{" "}
          {ms((summary.eval_reference as Record<string, number>).latency_p95_ms ?? null)} p95
          — sandbox feasibility, not a production baseline. Nothing is compared against it.
        </p>
      )}
    </Panel>
  );
}
