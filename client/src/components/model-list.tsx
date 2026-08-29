"use client";

import { useEffect, useState } from "react";
import { fetchModels, type ModelSummary } from "@/lib/verity";
import { Panel, RefreshButton } from "./panel";

export function ModelList({
  userId,
  selectedModelId,
  onSelect,
}: {
  userId: string;
  selectedModelId?: string | null;
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

  return (
    <Panel title={`Models — ${userId}`} action={<RefreshButton onClick={load} />}>
      {error && <p className="text-xs text-fail">{error}</p>}
      {!error && !models && <p className="text-xs text-ink-soft">Reading models…</p>}

      {models && models.length === 0 && (
        <p className="text-xs text-ink-soft">
          No models under this user id yet. Upload one and it appears here.
        </p>
      )}

      {models && models.length > 0 && (
        <ul className="-mx-2">
          {models.map((model) => {
            const selected = model.id === selectedModelId;
            return (
              <li key={model.id}>
                <button
                  type="button"
                  onClick={() => onSelect(model.id)}
                  aria-current={selected ? "true" : undefined}
                  className={`block w-full px-2 py-2 text-left ${
                    selected ? "bg-ink text-paper" : "hover:bg-paper hover:text-brass"
                  }`}
                >
                  <span className="block truncate font-medium">{model.name}</span>
                  <span
                    className={`mt-0.5 block truncate text-[11px] ${
                      selected ? "text-paper/70" : "text-ink-soft"
                    }`}
                  >
                    {model.model_class ?? "—"}
                    {model.production_version_id ? " · serving" : " · not serving"}
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
