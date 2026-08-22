// Talks directly to the Verity FastAPI server (see server/main.py's /ingest route).
// No SDK/proxy in between — this mirrors exactly what verity.client.assemble() sends,
// just from the browser: multipart form data, a client-computed sha256 the server
// re-verifies against the actual bytes, and an optional fixture for evaluation.

const API_BASE =
  process.env.NEXT_PUBLIC_VERITY_API ?? "http://127.0.0.1:8000";

export type FixtureDescriptor = {
  kind: string;
  sha256: string;
  spec?: Record<string, unknown>;
};

export type Manifest = {
  framework: string;
  detected_via?: string | null;
  model_class?: string | null;
  hyperparameters?: Record<string, unknown> | null;
  task_type?: string | null;
};

export type ThresholdCheck = {
  metric: string;
  op: string;
  value: number;
  actual?: number | null;
};

export type EvalRun = {
  id: string;
  mechanism: string | null;
  metric_set: { resolved: string[]; skipped: { metric: string; reason: string }[] };
  scores: Record<string, number | null>;
  thresholds: ThresholdCheck[];
  verdict: "pass" | "fail" | "error";
  failed_on: ThresholdCheck[];
  test_set_ref: string | null;
  error: { type: string; message: string } | null;
};

export type IngestResult = {
  model_version_id: string;
  artifact_uri: string;
  status: string;
  manifest: Manifest | null;
  eval_run: EvalRun | null;
  model_id?: string;
  deduplicated?: boolean;
  archived_model_version_id?: string | null;
  monitoring_config?: { id: string; metrics: string[] } | null;
};

export class IngestError extends Error {
  constructor(message: string, readonly detail?: unknown) {
    super(message);
    this.name = "IngestError";
  }
}

export async function sha256Hex(bytes: ArrayBuffer): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

export async function ingest(options: {
  modelBytes: ArrayBuffer;
  name: string;
  userId: string;
  fixture?: { bytes: ArrayBuffer; descriptor: FixtureDescriptor };
}): Promise<IngestResult> {
  const { modelBytes, name, userId, fixture } = options;
  const modelSha = await sha256Hex(modelBytes);

  const form = new FormData();
  form.append("artifact", new Blob([modelBytes]), "artifact");
  form.append("sha256", modelSha);
  form.append("user_id", userId);
  form.append("name", name);
  form.append("args", "{}");

  if (fixture) {
    form.append("fixture", new Blob([fixture.bytes]), "fixture");
    form.append("fixture_descriptor", JSON.stringify(fixture.descriptor));
  }

  let response: Response;
  try {
    response = await fetch(`${API_BASE}/ingest`, { method: "POST", body: form });
  } catch {
    throw new IngestError(
      `Couldn't reach the Verity server at ${API_BASE}. Is it running (uvicorn main:app --port 8000)?`,
    );
  }

  if (!response.ok) {
    let detail: unknown;
    try {
      detail = await response.json();
    } catch {
      detail = await response.text();
    }
    throw new IngestError(`Server rejected the upload (${response.status}).`, detail);
  }

  return response.json();
}

export type TelemetrySummary = {
  model_version_id: string;
  hours: number;
  request_count: number;
  error_rate: number;
  latency_p50_ms: number | null;
  latency_p95_ms: number | null;
  latency_p99_ms: number | null;
  truncated: boolean;
  eval_reference: Record<string, unknown> | null;
};

export async function fetchTelemetry(
  modelVersionId: string,
): Promise<TelemetrySummary> {
  const response = await fetch(
    `${API_BASE}/models/${encodeURIComponent(modelVersionId)}/telemetry`,
  );
  if (!response.ok) {
    throw new IngestError(`Couldn't read telemetry (${response.status}).`);
  }
  return response.json();
}
