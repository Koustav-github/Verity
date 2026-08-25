"use client";

import { useEffect, useState } from "react";
import { EvidenceReport } from "./evidence-report";
import { fetchDownloadUrls, fetchVersionDetail, type DownloadUrls, type IngestResult } from "@/lib/verity";

function DownloadButtons({ modelVersionId }: { modelVersionId: string }) {
  const [urls, setUrls] = useState<DownloadUrls | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchDownloadUrls(modelVersionId)
      .then(setUrls)
      .catch((err) => setError((err as Error).message));
  }, [modelVersionId]);

  if (error) {
    return <p className="mt-6 font-mono text-xs text-fail">{error}</p>;
  }
  if (!urls) {
    return <p className="mt-6 font-mono text-xs text-ink-soft">Preparing download links…</p>;
  }

  return (
    <div className="mt-6 border-t border-rule pt-4 font-mono text-sm">
      <h3 className="mb-2 text-xs uppercase tracking-[0.2em] text-brass">Downloads</h3>
      <div className="flex gap-3">
        <a
          href={urls.artifact_url}
          target="_blank"
          rel="noreferrer"
          className="border border-ink px-3 py-1 text-xs uppercase tracking-[0.2em] hover:bg-ink hover:text-paper"
        >
          Download artifact
        </a>
        {urls.fixture_url && (
          <a
            href={urls.fixture_url}
            target="_blank"
            rel="noreferrer"
            className="border border-ink px-3 py-1 text-xs uppercase tracking-[0.2em] hover:bg-ink hover:text-paper"
          >
            Download fixture
          </a>
        )}
      </div>
      <p className="mt-2 text-xs text-ink-soft">
        Links expire in 15 minutes — re-open this page to get fresh ones.
      </p>
    </div>
  );
}

export function VersionDetail({
  modelVersionId,
  onBack,
}: {
  modelVersionId: string;
  onBack: () => void;
}) {
  const [detail, setDetail] = useState<IngestResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setDetail(null);
    setError(null);
    fetchVersionDetail(modelVersionId)
      .then(setDetail)
      .catch((err) => setError((err as Error).message));
  }, [modelVersionId]);

  return (
    <div className="font-mono text-sm">
      <button
        type="button"
        onClick={onBack}
        className="mb-4 text-xs uppercase tracking-[0.2em] text-ink-soft hover:text-brass"
      >
        ← Back to versions
      </button>

      {error && <p className="text-xs text-fail">{error}</p>}
      {!error && !detail && <p className="text-xs text-ink-soft">Reading version detail…</p>}

      {detail && (
        <>
          <EvidenceReport result={detail} />
          <DownloadButtons modelVersionId={modelVersionId} />
        </>
      )}
    </div>
  );
}
