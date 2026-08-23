import type { Deployment } from "@/lib/verity";

export function DeploymentCard({ deployment }: { deployment: Deployment }) {
  return (
    <div className="mt-6 border-t border-rule pt-4 font-mono text-sm">
      <h3 className="mb-2 text-xs uppercase tracking-[0.2em] text-brass">
        Served by api-fication
      </h3>

      {deployment.status === "live" && deployment.endpoint_url && (
        <>
          <div className="flex items-baseline gap-2 py-1">
            <span className="shrink-0 text-ink-soft">status</span>
            <span className="grow border-b border-dotted border-rule translate-y-[-3px]" />
            <span className="shrink-0 font-medium text-pass">live</span>
          </div>
          <div className="flex items-baseline gap-2 py-1">
            <span className="shrink-0 text-ink-soft">endpoint</span>
            <span className="grow border-b border-dotted border-rule translate-y-[-3px]" />
            <code className="shrink-0 font-medium">{deployment.endpoint_url}</code>
          </div>
          <div className="flex items-baseline gap-2 py-1">
            <span className="shrink-0 text-ink-soft">image</span>
            <span className="grow border-b border-dotted border-rule translate-y-[-3px]" />
            <span className="shrink-0 font-medium">{deployment.image_tag}</span>
          </div>
          <p className="mt-2 text-xs text-ink-soft">
            Call it directly:{" "}
            <code>
              curl -X POST {deployment.endpoint_url}/predict -d {"'"}
              {"{"}&quot;instances&quot;: [...]{"}"}
              {"'"}
            </code>{" "}
            — or through Verity&apos;s own proxy, which is what records the telemetry below.
          </p>
        </>
      )}

      {deployment.status === "failed" && (
        <>
          <div className="flex items-baseline gap-2 py-1">
            <span className="shrink-0 text-ink-soft">status</span>
            <span className="grow border-b border-dotted border-rule translate-y-[-3px]" />
            <span className="shrink-0 font-medium text-fail">failed</span>
          </div>
          <p className="mt-2 text-xs text-fail">
            {deployment.error
              ? `${deployment.error.type}: ${deployment.error.message}`
              : "No error detail was recorded."}
          </p>
          <p className="mt-2 text-xs text-ink-soft">
            The promotion above still succeeded — a deploy failure never undoes it. Fix
            the cause and re-upload the same version to retry.
          </p>
        </>
      )}

      {deployment.status === "building" && (
        <p className="text-xs text-ink-soft">
          Still building — this response returned before the image finished. Refresh
          shortly.
        </p>
      )}
    </div>
  );
}
