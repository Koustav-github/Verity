"use client";

import { useEffect, useState } from "react";
import { fetchVersions, type VersionSummary } from "@/lib/verity";

export function VersionList({
  modelId,
  onSelect,
  onBack,
}: {
  modelId: string;
  onSelect: (modelVersionId: string) => void;
  onBack: () => void;
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
    <div className="font-mono text-sm">
      <button
        type="button"
        onClick={onBack}
        className="mb-4 text-xs uppercase tracking-[0.2em] text-ink-soft hover:text-brass"
      >
        ← Back to models
      </button>

      {error && <p className="text-xs text-fail">{error}</p>}
      {!error && !versions && <p className="text-xs text-ink-soft">Reading versions…</p>}

      {versions && (
        <>
          <h3 className="mb-2 text-xs uppercase tracking-[0.2em] text-brass">
            Versions ({versions.length})
          </h3>
          {versions.length === 0 ? (
            <p className="text-xs text-ink-soft">No versions found.</p>
          ) : (
            <ul className="space-y-1">
              {versions.map((version) => (
                <li key={version.id}>
                  <button
                    type="button"
                    onClick={() => onSelect(version.id)}
                    className="flex w-full items-baseline gap-2 py-1 text-left hover:text-brass"
                  >
                    <span className="shrink-0 font-medium">{version.id}</span>
                    <span className="grow border-b border-dotted border-rule translate-y-[-3px]" />
                    <span className="shrink-0 text-xs text-ink-soft">{version.status}</span>
                    <span className="shrink-0 text-xs text-ink-soft">
                      {new Date(version.created_at).toLocaleString()}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
