"use client";

import { useState } from "react";
import { ModelList } from "./model-list";
import { VersionList } from "./version-list";
import { VersionDetail } from "./version-detail";

/** Master-detail on wide screens, drill-down on narrow ones.
 *
 * The drill-down replaced the whole view at every level, so while reading a version you
 * could not see which model it belonged to or what its siblings were. On lg+ the lists
 * stay on screen as context; below that the original one-thing-at-a-time flow is still
 * the right call, so the same components render in a stack instead.
 */
export function ModelsBrowser({ userId }: { userId: string }) {
  const [selectedModelId, setSelectedModelId] = useState<string | null>(null);
  const [selectedVersionId, setSelectedVersionId] = useState<string | null>(null);

  function selectModel(modelId: string) {
    setSelectedModelId(modelId);
    // A version from the previously selected model would otherwise stay open beside a
    // list it no longer belongs to.
    setSelectedVersionId(null);
  }

  return (
    <div className="lg:grid lg:grid-cols-[minmax(0,20rem)_minmax(0,1fr)] lg:gap-6">
      {/* Sidebar: on lg+ it is a sticky rail; below that it is just the top of the page. */}
      <div className="lg:sticky lg:top-8 lg:max-h-[calc(100vh-4rem)] lg:self-start lg:overflow-y-auto lg:pr-1">
        <div className={selectedModelId ? "hidden lg:block" : ""}>
          <ModelList
            userId={userId}
            selectedModelId={selectedModelId}
            onSelect={selectModel}
          />
        </div>

        {selectedModelId && (
          <div className={selectedVersionId ? "hidden lg:block" : ""}>
            <VersionList
              modelId={selectedModelId}
              selectedVersionId={selectedVersionId}
              onSelect={setSelectedVersionId}
              onBack={() => setSelectedModelId(null)}
              className="mt-6 lg:mt-8"
            />
          </div>
        )}
      </div>

      {/* Detail pane. */}
      <div className={selectedVersionId ? "mt-8 lg:mt-0" : "hidden lg:block"}>
        {selectedVersionId ? (
          <VersionDetail
            modelVersionId={selectedVersionId}
            onBack={() => setSelectedVersionId(null)}
          />
        ) : (
          <p className="border border-dashed border-rule px-4 py-10 text-center font-mono text-xs text-ink-soft">
            {selectedModelId
              ? "Pick a version to read its evidence."
              : "Pick a model to see its versions."}
          </p>
        )}
      </div>
    </div>
  );
}
