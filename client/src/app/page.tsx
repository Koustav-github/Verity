"use client";

import { useState } from "react";
import { EvidenceReport } from "@/components/evidence-report";
import { ModelsBrowser } from "@/components/models-browser";
import { ingest, sha256Hex, type FixtureDescriptor, type IngestResult, IngestError } from "@/lib/verity";

type Source = "demo" | "custom";

export default function Home() {
  const [source, setSource] = useState<Source>("demo");
  const [name, setName] = useState("demo-classifier");
  const [userId, setUserId] = useState("demo-user");
  const [modelFile, setModelFile] = useState<File | null>(null);
  const [fixtureFile, setFixtureFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<IngestResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<"upload" | "models">("upload");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    setResult(null);

    try {
      let modelBytes: ArrayBuffer;
      let fixture: { bytes: ArrayBuffer; descriptor: FixtureDescriptor } | undefined;

      if (source === "demo") {
        modelBytes = await (await fetch("/demo/model.pkl")).arrayBuffer();
        const fixtureBytes = await (await fetch("/demo/fixture.pkl")).arrayBuffer();
        const manifest = await (await fetch("/demo/manifest.json")).json();
        fixture = { bytes: fixtureBytes, descriptor: manifest.fixture_descriptor };
      } else {
        if (!modelFile) throw new Error("Choose a model file first.");
        modelBytes = await modelFile.arrayBuffer();
        if (fixtureFile) {
          const fixtureBytes = await fixtureFile.arrayBuffer();
          const fixtureSha = await sha256Hex(fixtureBytes);
          fixture = {
            bytes: fixtureBytes,
            descriptor: { kind: "labeled_holdout", sha256: fixtureSha },
          };
        }
      }

      const outcome = await ingest({ modelBytes, name, userId, fixture });
      setResult(outcome);
    } catch (err) {
      setError(err instanceof IngestError ? err.message : (err as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex flex-1 justify-center px-4 py-6 sm:py-8">
      {/* The intake form is a single column of fields and reads best narrow. The registry
          is a list beside a detail pane and was being strangled by the same width. */}
      <main className={`w-full ${view === "models" ? "max-w-6xl" : "max-w-xl"}`}>
        <div className="mb-6 flex gap-2 font-mono text-xs uppercase tracking-[0.2em]">
          <button
            type="button"
            onClick={() => setView("upload")}
            className={`border-2 px-3 py-1 ${view === "upload" ? "border-ink bg-ink text-paper" : "border-ink text-ink-soft hover:text-ink"}`}
          >
            Upload
          </button>
          <button
            type="button"
            onClick={() => setView("models")}
            className={`border-2 px-3 py-1 ${view === "models" ? "border-ink bg-ink text-paper" : "border-ink text-ink-soft hover:text-ink"}`}
          >
            Models
          </button>
        </div>

        {view === "models" && (
          <>
            <header className="mb-6 border-b-2 border-ink pb-4">
              <p className="text-xs uppercase tracking-[0.3em] text-brass">Registry</p>
              <h1 className="mt-1 font-mono text-2xl font-bold tracking-tight sm:text-3xl">
                Every version, and the evidence behind it.
              </h1>
              <p className="mt-2 max-w-xl text-sm text-ink-soft">
                Each version keeps the eval run that justified it, how it was served, and
                what its live traffic has looked like since.
              </p>
            </header>
            <ModelsBrowser userId={userId} />
          </>
        )}

        {view === "upload" && (
          <>
            <header className="mb-6 border-b-2 border-ink pb-3">
              <p className="text-xs uppercase tracking-[0.3em] text-brass">Intake</p>
              <h1 className="mt-1 font-mono text-2xl font-bold tracking-tight sm:text-3xl">
                Upload a model. Watch it get gated.
              </h1>
              <p className="mt-2 max-w-md text-sm text-ink-soft">
                Hawkeye identifies it, Nat evaluates it against accuracy and systemic
                thresholds, Fury registers the verdict. Nothing here is staged for the demo —
                this calls the real pipeline.
              </p>
            </header>

            <form onSubmit={handleSubmit} className="space-y-6 font-mono text-sm">
              <fieldset className="space-y-2">
                <legend className="mb-2 text-xs uppercase tracking-[0.2em] text-brass">Source</legend>
                <div className="flex gap-4">
                  <label className="flex items-center gap-2">
                    <input
                      type="radio"
                      name="source"
                      checked={source === "demo"}
                      onChange={() => setSource("demo")}
                    />
                    Bundled demo model
                  </label>
                  <label className="flex items-center gap-2">
                    <input
                      type="radio"
                      name="source"
                      checked={source === "custom"}
                      onChange={() => setSource("custom")}
                    />
                    Upload your own
                  </label>
                </div>
                <p className="text-xs text-ink-soft">
                  {source === "demo"
                    ? "A tiny scikit-learn LogisticRegression with a matching holdout — trains and evaluates cleanly, so you see the whole pipeline run."
                    : "A cloudpickled model file (same as verity.serialize()). Optionally attach a fixture — cloudpickle.dump({\"X\": X_test, \"y\": y_test}, open('fixture.pkl','wb')) — to also trigger evaluation."}
                </p>
              </fieldset>

              {source === "custom" && (
                <div className="space-y-3 border-l-2 border-rule pl-4">
                  <label className="block">
                    <span className="mb-1 block text-xs uppercase tracking-[0.2em] text-brass">Model file (.pkl)</span>
                    <input
                      type="file"
                      required
                      onChange={(e) => setModelFile(e.target.files?.[0] ?? null)}
                      className="block w-full text-xs file:mr-3 file:border file:border-ink file:bg-transparent file:px-3 file:py-1 file:text-xs"
                    />
                  </label>
                  <label className="block">
                    <span className="mb-1 block text-xs uppercase tracking-[0.2em] text-brass">Fixture file (optional)</span>
                    <input
                      type="file"
                      onChange={(e) => setFixtureFile(e.target.files?.[0] ?? null)}
                      className="block w-full text-xs file:mr-3 file:border file:border-ink file:bg-transparent file:px-3 file:py-1 file:text-xs"
                    />
                  </label>
                </div>
              )}

              <label className="block">
                <span className="mb-1 block text-xs uppercase tracking-[0.2em] text-brass">Model name</span>
                <input
                  type="text"
                  required
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="w-full border-b border-ink bg-transparent py-1 outline-none focus-visible:border-brass"
                  placeholder="fraud-classifier"
                />
                <span className="mt-1 block text-xs text-ink-soft">
                  Groups versions — re-upload under this name to register a new version of the same model.
                </span>
              </label>

              <label className="block">
                <span className="mb-1 block text-xs uppercase tracking-[0.2em] text-brass">Your ID</span>
                <input
                  type="text"
                  required
                  value={userId}
                  onChange={(e) => setUserId(e.target.value)}
                  className="w-full border-b border-ink bg-transparent py-1 outline-none focus-visible:border-brass"
                />
              </label>

              <button
                type="submit"
                disabled={submitting}
                className="w-full border-2 border-ink py-3 text-xs uppercase tracking-[0.3em] transition-colors hover:bg-ink hover:text-paper disabled:opacity-50"
              >
                {submitting ? "Running the pipeline…" : "Submit model"}
              </button>
            </form>

            {error && (
              <p className="mt-6 border border-fail px-4 py-3 font-mono text-xs text-fail">
                {error}
              </p>
            )}

            {result && (
              <div className="mt-10">
                <EvidenceReport result={result} />
              </div>
            )}
          </>
        )}
      </main>
    </div>
  );
}
