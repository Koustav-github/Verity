"use client";

import { useState } from "react";
import { ModelList } from "./model-list";
import { VersionList } from "./version-list";
import { VersionDetail } from "./version-detail";

/** Drill-down: models, then that model's versions, then one version's evidence.
 *
 * One thing on screen at a time. The detail pane is dense enough — manifest,
 * thresholds, deployment, telemetry, traces, alerts — that it earns the full width.
 */
export function ModelsBrowser({ userId }: { userId: string }) {
  const [selectedModelId, setSelectedModelId] = useState<string | null>(null);
  const [selectedVersionId, setSelectedVersionId] = useState<string | null>(null);

  if (selectedVersionId) {
    return (
      <VersionDetail
        modelVersionId={selectedVersionId}
        onBack={() => setSelectedVersionId(null)}
      />
    );
  }

  if (selectedModelId) {
    return (
      <VersionList
        modelId={selectedModelId}
        onSelect={setSelectedVersionId}
        onBack={() => setSelectedModelId(null)}
      />
    );
  }

  return <ModelList userId={userId} onSelect={setSelectedModelId} />;
}
