import type { IngestResult } from "@/lib/verity";
import { VerdictStamp } from "./verdict-stamp";
import { TelemetryPanel } from "./telemetry-panel";
import { TracesPanel } from "./traces-panel";
import { DeploymentCard } from "./deployment-card";
import { AlertsPanel } from "./alerts-panel";
import { Panel, LeaderRow } from "./panel";
import { StatusBadge } from "./status-badge";

const RESOURCE_PREFIX = "resource.";

function formatResourceValue(metric: string, value: number) {
  if (metric.endsWith("_ms")) return `${value.toFixed(2)} ms`;
  if (metric.endsWith("_mb")) return `${value.toFixed(1)} MB`;
  if (metric.endsWith("_s")) return `${value.toFixed(3)} s`;
  if (metric.endsWith("_rps")) return `${value.toFixed(1)} rps`;
  return value.toFixed(4);
}

function ThresholdRow({
  metric,
  op,
  value,
  actual,
  ok,
}: {
  metric: string;
  op: string;
  value: number;
  actual: number | null | undefined;
  ok: boolean;
}) {
  return (
    <div className="flex items-baseline gap-2 py-1">
      <span className={ok ? "text-pass" : "text-fail"}>{ok ? "✓" : "✗"}</span>
      <span className="shrink-0 text-ink-soft">{metric.replace(RESOURCE_PREFIX, "")}</span>
      <span className="grow translate-y-[-3px] border-b border-dotted border-rule" />
      <span className="shrink-0">
        {actual == null ? "—" : actual.toFixed(4)}
        <span className="text-ink-soft">
          {" "}
          {op} {value}
        </span>
      </span>
    </div>
  );
}

/** The three or four numbers someone actually wants first, lifted out of the threshold
 *  rows they were buried in. Only rendered for metrics that exist — a regression model
 *  has no accuracy, and an empty tile is worse than an absent one. */
function StatTiles({ result }: { result: IngestResult }) {
  const scores = result.eval_run?.scores ?? {};
  const tiles: { label: string; value: string }[] = [];

  const headlineLabel = scores["accuracy"] != null
    ? "accuracy"
    : scores["balanced_accuracy"] != null
      ? "bal. accuracy"
      : "r²";
  const headline = scores["accuracy"] ?? scores["balanced_accuracy"] ?? scores["r2"];
  if (headline != null) {
    tiles.push({ label: headlineLabel, value: headline.toFixed(3) });
  }

  const f1 = scores["f1"];
  if (f1 != null) tiles.push({ label: "f1", value: f1.toFixed(3) });

  const p95 = scores[`${RESOURCE_PREFIX}latency_p95_ms`];
  if (p95 != null) tiles.push({ label: "p95 latency", value: `${p95.toFixed(1)} ms` });

  const memory = scores[`${RESOURCE_PREFIX}peak_memory_mb`];
  if (memory != null) tiles.push({ label: "peak memory", value: `${memory.toFixed(0)} MB` });

  if (tiles.length === 0) return null;

  return (
    <div className="mt-4 grid grid-cols-2 gap-px border border-rule bg-rule sm:grid-cols-4">
      {tiles.map((tile) => (
        <div key={tile.label} className="bg-paper-raised px-3 py-2">
          <div className="text-[10px] uppercase tracking-[0.15em] text-ink-soft">
            {tile.label}
          </div>
          <div className="mt-0.5 font-mono text-lg font-medium tabular-nums">
            {tile.value}
          </div>
        </div>
      ))}
    </div>
  );
}

export function EvidenceReport({ result }: { result: IngestResult }) {
  const verdict = result.eval_run?.verdict ?? result.status;
  const failedMetrics = new Set((result.eval_run?.failed_on ?? []).map((f) => f.metric));

  const qualityThresholds =
    result.eval_run?.thresholds.filter((t) => !t.metric.startsWith(RESOURCE_PREFIX)) ?? [];
  const resourceThresholds =
    result.eval_run?.thresholds.filter((t) => t.metric.startsWith(RESOURCE_PREFIX)) ?? [];
  const resourceThresholdMetrics = new Set(resourceThresholds.map((t) => t.metric));
  // Some resource.* scores (e.g. gpu_memory_mb when no GPU ran) have no threshold at
  // all — still worth showing, just as plain values rather than a pass/fail row.
  const unthresholdedResourceScores = Object.entries(result.eval_run?.scores ?? {}).filter(
    ([metric, value]) =>
      metric.startsWith(RESOURCE_PREFIX) && value != null && !resourceThresholdMetrics.has(metric),
  );

  return (
    <section className="font-mono text-sm">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <VerdictStamp verdict={result.deduplicated ? result.status : verdict} />
        <StatusBadge status={result.status} />
      </div>

      <StatTiles result={result} />

      {result.deduplicated && (
        <p className="mt-4 border border-brass bg-paper-raised px-4 py-3 text-xs">
          Byte-identical to an existing record under this name — nothing re-ran. Showing
          the record already on file.
        </p>
      )}

      {result.archived_model_version_id && (
        <p className="mt-4 border border-brass bg-paper-raised px-4 py-3 text-xs">
          This promotion replaced <code>{result.archived_model_version_id}</code>, now
          archived.
        </p>
      )}

      <div className="mt-4 space-y-4">
        <Panel title="Identified by Hawkeye">
          {result.manifest ? (
            <>
              <LeaderRow label="framework" value={result.manifest.framework} />
              <LeaderRow label="class" value={result.manifest.model_class ?? "—"} />
              <LeaderRow label="task_type" value={result.manifest.task_type ?? "—"} />
            </>
          ) : (
            <p className="text-xs text-ink-soft">
              Not re-identified — this is a deduplicated record.
            </p>
          )}
        </Panel>

        {result.eval_run && (
          <Panel
            title={`Evaluated by Nat — ${result.eval_run.mechanism ?? "unknown mechanism"}`}
          >
            {result.eval_run.error && (
              <p className="mb-2 text-xs text-fail">
                {result.eval_run.error.type}: {result.eval_run.error.message}
              </p>
            )}

            {qualityThresholds.map((t) => (
              <ThresholdRow
                key={t.metric}
                metric={t.metric}
                op={t.op}
                value={t.value}
                actual={result.eval_run!.scores[t.metric]}
                ok={!failedMetrics.has(t.metric)}
              />
            ))}

            {result.eval_run.metric_set.skipped.length > 0 && (
              <p className="mt-2 text-xs text-ink-soft">
                Skipped:{" "}
                {result.eval_run.metric_set.skipped
                  .map((s) => `${s.metric} (${s.reason})`)
                  .join(", ")}
              </p>
            )}
          </Panel>
        )}

        {(resourceThresholds.length > 0 || unthresholdedResourceScores.length > 0) && (
          <Panel title="System metrics">
            <p className="mb-2 text-[11px] text-ink-soft">
              Sandbox feasibility — single-process, single-client, cold. Not production
              load.
            </p>

            {resourceThresholds.map((t) => (
              <ThresholdRow
                key={t.metric}
                metric={t.metric}
                op={t.op}
                value={t.value}
                actual={result.eval_run!.scores[t.metric]}
                ok={!failedMetrics.has(t.metric)}
              />
            ))}

            {unthresholdedResourceScores.map(([metric, value]) => (
              <LeaderRow
                key={metric}
                label={metric.replace(RESOURCE_PREFIX, "")}
                value={formatResourceValue(metric, value as number)}
              />
            ))}
          </Panel>
        )}

        {result.deployment && <DeploymentCard deployment={result.deployment} />}

        {result.monitoring_config && (
          <>
            <TelemetryPanel modelVersionId={result.model_version_id} />
            <TracesPanel modelVersionId={result.model_version_id} />
            <AlertsPanel modelVersionId={result.model_version_id} />
          </>
        )}

        <Panel title="Record">
          <LeaderRow label="model_id" value={result.model_id ?? "—"} />
          <LeaderRow
            label="model_version_id"
            value={<span className="break-all">{result.model_version_id}</span>}
          />
          <LeaderRow label="artifact" value={<span className="break-all text-xs">{result.artifact_uri}</span>} />
        </Panel>
      </div>
    </section>
  );
}
