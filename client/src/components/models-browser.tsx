"use client";

import { useState } from "react";
import { ModelList } from "./model-list";
import { VersionList } from "./version-list";
import { VersionDetail } from "./version-detail";

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
