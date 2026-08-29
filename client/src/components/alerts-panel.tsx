"use client";

import { useEffect, useState } from "react";
import {
  fetchAlerts,
  type AlertEvent,
  type QualityAlertDetail,
  type SystemicAlertDetail,
} from "@/lib/verity";
import { Panel, RefreshButton } from "./panel";

function describe(alert: AlertEvent): string {
  if (alert.kind === "systemic") {
    const d = alert.detail as SystemicAlertDetail;
    const pct = (d.relative_increase * 100).toFixed(0);
    return `${d.metric}: ${d.recent.toFixed(3)} vs. a baseline of ${d.baseline.toFixed(3)} (+${pct}%)`;
  }
  const d = alert.detail as QualityAlertDetail;
  const actual = d.actual == null ? "—" : d.actual.toFixed(4);
  return `${d.metric}: ${actual} ${d.op} ${d.value} — the same threshold that gated promotion`;
}

export function AlertsPanel({ modelVersionId }: { modelVersionId: string }) {
  const [alerts, setAlerts] = useState<AlertEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    try {
      setAlerts(await fetchAlerts(modelVersionId));
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
      title={`Falcon alerts${alerts ? ` (${alerts.length})` : ""}`}
      action={<RefreshButton onClick={load} />}
    >
      {error && <p className="text-xs text-fail">{error}</p>}
      {!error && !alerts && <p className="text-xs text-ink-soft">Reading alerts…</p>}

      {alerts && alerts.length === 0 && (
        <p className="text-xs text-ink-soft">
          None fired. Systemic checks run on live traffic automatically; quality checks
          need 30+ reported outcomes via{" "}
          <code>POST /predictions/{"{"}id{"}"}/outcomes</code>.
        </p>
      )}

      {alerts && alerts.length > 0 && (
        <ul className="space-y-2">
          {alerts.map((alert) => (
            <li key={alert.id} className="border-l-2 border-fail pl-3 text-xs">
              <div className="flex items-baseline gap-2">
                <span className="font-medium uppercase tracking-wide text-fail">
                  {alert.kind}
                </span>
                <span className="text-ink-soft">
                  {new Date(alert.created_at).toLocaleString()}
                </span>
              </div>
              <p className="mt-1">{describe(alert)}</p>
              <p className="mt-1 text-ink-soft">
                {alert.emailed_at
                  ? `Emailed ${new Date(alert.emailed_at).toLocaleString()}`
                  : "No email sent (no address configured, or delivery failed) — recorded here regardless."}
              </p>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
