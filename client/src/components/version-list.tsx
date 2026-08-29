"use client";

import { useEffect, useState } from "react";
import { fetchVersions, type VersionSummary } from "@/lib/verity";
import { Panel, RefreshButton } from "./panel";
import { StatusBadge } from "./status-badge";

/** Version ids are long and share a `mv_` prefix, so the head of the hash is the only
 *  part that distinguishes them at a glance. The full id stays available on hover and
 *  in the detail pane. */
function shortId(id: string) {
  return id.length > 14 ? `${id.slice(0, 11)}…${id.slice(-4)}` : id;
}

export function VersionList({
  modelId,
  selectedVersionId,
  onSelect,
  onBack,
  className = "",
}: {
  modelId: string;
  selectedVersionId?: string | null;
  onSelect: (modelVersionId: string) => void;
  onBack: () => void;
  className?: string;
}) {
  const [versions, setVersions] = useState<VersionSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    try {
      setVersions(await fetchVersions(modelId));
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modelId]);

  return (
    <Panel
      title={`Versions${versions ? ` (${versions.length})` : ""}`}
      action={<RefreshButton onClick={load} />}
      className={className}
    >
      <button
        type="button"
        onClick={onBack}
        className="mb-2 text-xs uppercase tracking-[0.2em] text-ink-soft hover:text-brass lg:hidden"
      >
        ← Back to models
      </button>

      {error && <p className="text-xs text-fail">{error}</p>}
      {!error && !versions && <p className="text-xs text-ink-soft">Reading versions…</p>}

      {versions && versions.length === 0 && (
        <p className="text-xs text-ink-soft">No versions found.</p>
      )}

      {versions && versions.length > 0 && (
        <ul className="-mx-2">
          {versions.map((version) => {
            const selected = version.id === selectedVersionId;
            return (
              <li key={version.id}>
                <button
                  type="button"
                  onClick={() => onSelect(version.id)}
                  title={version.id}
                  aria-current={selected ? "true" : undefined}
                  className={`block w-full px-2 py-2 text-left ${
                    selected ? "bg-ink text-paper" : "hover:bg-paper hover:text-brass"
                  }`}
                >
                  <span className="flex items-baseline justify-between gap-2">
                    <span className="truncate text-xs font-medium">
                      {shortId(version.id)}
                    </span>
                    <StatusBadge status={version.status} />
                  </span>
                  <span
                    className={`mt-1 block text-[11px] ${
                      selected ? "text-paper/70" : "text-ink-soft"
                    }`}
                  >
                    {new Date(version.created_at).toLocaleString()}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}
