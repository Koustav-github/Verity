import type { Deployment } from "@/lib/verity";
import { Panel, LeaderRow } from "./panel";
import { StatusBadge } from "./status-badge";

export function DeploymentCard({ deployment }: { deployment: Deployment }) {
  return (
    <Panel
      title="Served by api-fication"
      action={<StatusBadge status={deployment.status} />}
    >
      {deployment.status === "live" && deployment.endpoint_url && (
        <>
          <LeaderRow
            label="container"
            value={<span className="break-all text-xs">{deployment.endpoint_url}</span>}
          />
          <LeaderRow label="image" value={<span className="break-all text-xs">{deployment.image_tag}</span>} />

          <div className="mt-3 border-t border-dotted border-rule pt-2">
            <p className="mb-1 text-[10px] uppercase tracking-[0.15em] text-ink-soft">
              Call it
            </p>
            {/* The proxy first: it is the address that survives a promotion, and it is
                what records the telemetry shown below. The container's own URL changes
                every deploy and bypasses all of it. */}
            <pre className="overflow-x-auto border border-rule bg-paper px-2 py-1.5 text-[11px] leading-relaxed">
{`POST /users/<user_id>/models/<name>/predict
{"instances": [...]}`}
            </pre>
            <p className="mt-1 text-[11px] text-ink-soft">
              Stable across promotions, and the only path that records telemetry. Hitting{" "}
              <code>{deployment.endpoint_url}/predict</code> directly works too, but that
              address changes on every deploy and bypasses monitoring.
            </p>
          </div>
        </>
      )}

      {deployment.status === "failed" && (
        <>
          <p className="text-xs text-fail">
            {deployment.error
              ? `${deployment.error.type}: ${deployment.error.message}`
              : "No error detail was recorded."}
          </p>
          <p className="mt-2 text-xs text-ink-soft">
            The promotion above still succeeded — a deploy failure never undoes it. Fix the
            cause and re-upload to retry.
          </p>
        </>
      )}

      {deployment.status === "building" && (
        <p className="text-xs text-ink-soft">
          Still building — this response returned before the image finished. Refresh
          shortly. If it never leaves this state, the server was interrupted mid-deploy;
          re-upload to retry.
        </p>
      )}

      {deployment.status === "stopped" && (
        <>
          <LeaderRow label="image" value={<span className="break-all text-xs">{deployment.image_tag}</span>} />
          <p className="mt-2 text-xs text-ink-soft">
            Replaced by a newer production version — this version no longer serves traffic.
          </p>
        </>
      )}
    </Panel>
  );
}
