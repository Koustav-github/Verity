"use client";

import { useEffect, useState } from "react";
import { fetchModels, type ModelSummary } from "@/lib/verity";

export function ModelList({
  userId,
  onSelect,
}: {
  userId: string;
  onSelect: (modelId: string) => void;
}) {
  const [models, setModels] = useState<ModelSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    try {
      setModels(await fetchModels(userId));
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId]);

  if (error) {
    return <p className="mt-6 font-mono text-xs text-fail">{error}</p>;
  }
  if (!models) {
    return <p className="mt-6 font-mono text-xs text-ink-soft">Reading models…</p>;
  }

  return (
    <div className="font-mono text-sm">
      <div className="mb-2 flex items-baseline justify-between">
        <h3 className="text-xs uppercase tracking-[0.2em] text-brass">
          Models for {userId} ({models.length})
        </h3>
        <button
          type="button"
          onClick={load}
          className="border border-ink px-2 py-1 text-[10px] uppercase tracking-[0.2em] hover:bg-ink hover:text-paper"
        >
          Refresh
        </button>
      </div>

      {models.length === 0 ? (
        <p className="text-xs text-ink-soft">
          No models uploaded yet under this user id.
        </p>
      ) : (
        <ul className="space-y-1">
          {models.map((model) => (
            <li key={model.id}>
              <button
                type="button"
                onClick={() => onSelect(model.id)}
                className="flex w-full items-baseline gap-2 py-1 text-left hover:text-brass"
              >
                <span className="shrink-0 font-medium">{model.name}</span>
                <span className="grow border-b border-dotted border-rule translate-y-[-3px]" />
                <span className="shrink-0 text-xs text-ink-soft">
                  {model.production_version_id ? "has a production version" : "no production version"}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
