import type { IngestResult } from "@/lib/verity";
import { VerdictStamp } from "./verdict-stamp";

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-baseline gap-2 py-1">
      <span className="shrink-0 text-ink-soft">{label}</span>
      <span className="grow border-b border-dotted border-rule translate-y-[-3px]" />
      <span className="shrink-0 font-medium">{value}</span>
    </div>
  );
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
      <span className="shrink-0 text-ink-soft">{metric}</span>
      <span className="grow border-b border-dotted border-rule translate-y-[-3px]" />
      <span className="shrink-0">
        {actual == null ? "—" : actual.toFixed(4)}
        <span className="text-ink-soft"> {op} {value}</span>
      </span>
    </div>
  );
}

export function EvidenceReport({ result }: { result: IngestResult }) {
  const verdict = result.eval_run?.verdict ?? result.status;
  const failedMetrics = new Set((result.eval_run?.failed_on ?? []).map((f) => f.metric));

  return (
    <section className="font-mono text-sm">
      <VerdictStamp verdict={result.deduplicated ? result.status : verdict} />

      {result.deduplicated && (
        <p className="mt-4 rounded-none border border-brass bg-paper-raised px-4 py-3 text-xs">
          Byte-identical to an existing record under this name — nothing re-ran. Showing
          the record already on file.
        </p>
      )}

      {result.archived_model_version_id && (
        <p className="mt-4 rounded-none border border-brass bg-paper-raised px-4 py-3 text-xs">
          This promotion replaced <code>{result.archived_model_version_id}</code>, now
          archived.
        </p>
      )}

      <div className="mt-6 border-t-2 border-ink pt-4">
        <h3 className="mb-2 text-xs uppercase tracking-[0.2em] text-brass">Identified by Hawkeye</h3>
        {result.manifest ? (
          <>
            <Row label="framework" value={result.manifest.framework} />
            <Row label="class" value={result.manifest.model_class ?? "—"} />
            <Row label="task_type" value={result.manifest.task_type ?? "—"} />
          </>
        ) : (
          <p className="text-ink-soft text-xs">Not re-identified — this is a deduplicated record.</p>
        )}
      </div>

      {result.eval_run && (
        <div className="mt-6 border-t border-rule pt-4">
          <h3 className="mb-2 text-xs uppercase tracking-[0.2em] text-brass">
            Evaluated by Nat — {result.eval_run.mechanism ?? "unknown mechanism"}
          </h3>

          {result.eval_run.error && (
            <p className="mb-2 text-fail text-xs">
              {result.eval_run.error.type}: {result.eval_run.error.message}
            </p>
          )}

          {result.eval_run.thresholds.map((t) => (
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
              Skipped: {result.eval_run.metric_set.skipped.map((s) => `${s.metric} (${s.reason})`).join(", ")}
            </p>
          )}
        </div>
      )}

      <div className="mt-6 border-t border-rule pt-4 text-xs text-ink-soft">
        <Row label="model_id" value={result.model_id ?? "—"} />
        <Row label="model_version_id" value={result.model_version_id} />
        <Row label="status" value={result.status} />
      </div>
    </section>
  );
}
