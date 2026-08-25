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

export type Deployment = {
  id: string;
  status: "building" | "live" | "failed" | "stopped";
  host_port?: number | null;
  endpoint_url?: string | null;
  image_tag: string;
  error?: { type: string; message: string } | null;
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
  // Null for a version that wasn't promoted, or whose deploy failed non-fatally —
  // a null here is never itself an error; check `status` for that.
  deployment?: Deployment | null;
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

export type TelemetryTrace = {
  occurred_at: string;
  latency_ms: number | null;
  status: "ok" | "error" | "timeout";
  error_type: string | null;
  prediction_id: string | null;
};

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
  recent_events: TelemetryTrace[];
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

// Shape varies by `kind`: a systemic detail compares a version against its own
// trailing window; a quality detail is the failed threshold itself (Nat's own
// apply_thresholds() shape, `{metric, op, value, actual}`).
export type SystemicAlertDetail = {
  metric: string;
  recent: number;
  baseline: number;
  relative_increase: number;
};

export type QualityAlertDetail = {
  metric: string;
  op: string;
  value: number;
  actual: number | null;
};

export type AlertEvent = {
  id: string;
  model_version_id: string;
  kind: "systemic" | "quality";
  metric: string;
  detail: SystemicAlertDetail | QualityAlertDetail;
  created_at: string;
  emailed_at: string | null;
};

export async function fetchAlerts(
  modelVersionId: string,
): Promise<AlertEvent[]> {
  const response = await fetch(
    `${API_BASE}/models/${encodeURIComponent(modelVersionId)}/alerts`,
  );
  if (!response.ok) {
    throw new IngestError(`Couldn't read alerts (${response.status}).`);
  }
  const body: { model_version_id: string; alerts: AlertEvent[] } = await response.json();
  return body.alerts;
}

export type ModelSummary = {
  id: string;
  name: string;
  model_class: string | null;
  task_type: string | null;
  created_at: string;
  production_version_id: string | null;
};

export async function fetchModels(userId: string): Promise<ModelSummary[]> {
  const response = await fetch(`${API_BASE}/users/${encodeURIComponent(userId)}/models`);
  if (!response.ok) {
    throw new IngestError(`Couldn't list models (${response.status}).`);
  }
  const body: { user_id: string; models: ModelSummary[] } = await response.json();
  return body.models;
}

export type VersionSummary = {
  id: string;
  status: string;
  created_at: string;
};

export async function fetchVersions(modelId: string): Promise<VersionSummary[]> {
  const response = await fetch(`${API_BASE}/models/${encodeURIComponent(modelId)}/versions`);
  if (!response.ok) {
    throw new IngestError(`Couldn't list versions (${response.status}).`);
  }
  const body: { model_id: string; versions: VersionSummary[] } = await response.json();
  return body.versions;
}

export async function fetchVersionDetail(modelVersionId: string): Promise<IngestResult> {
  const response = await fetch(
    `${API_BASE}/model_versions/${encodeURIComponent(modelVersionId)}`,
  );
  if (!response.ok) {
    throw new IngestError(`Couldn't read version detail (${response.status}).`);
  }
  return response.json();
}

export type DownloadUrls = {
  model_version_id: string;
  artifact_url: string;
  fixture_url: string | null;
};

export async function fetchDownloadUrls(modelVersionId: string): Promise<DownloadUrls> {
  const response = await fetch(
    `${API_BASE}/model_versions/${encodeURIComponent(modelVersionId)}/download-urls`,
  );
  if (!response.ok) {
    throw new IngestError(`Couldn't get download links (${response.status}).`);
  }
  return response.json();
}
