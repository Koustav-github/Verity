# Model registry dashboard — browse, detail, download

## Context

Verity's frontend (`client/`) is a single upload form: submit a model, watch the pipeline
run, see one result (`EvidenceReport`, fed by whatever `POST /ingest` just returned), and
that's it. Navigate away and the result is gone — there is no way to come back and see a
model you uploaded five minutes ago, no way to see a model's version history, and no way
to get the artifact bytes back out short of going into the AWS S3 console directly and
reading a URI by hand.

This is the gap the request names directly: "just like mlflow dashboard... details and
downloadable links and buttons." MLflow's UI is fundamentally a browsable list you click
into for detail; Verity's frontend has no list at all today, and the backend has no route
to produce one either — every existing `metadata_store` query method takes a specific,
already-known id (`find_model`, `find_production_version`, `find_live_deployment`, …).
Nothing returns a collection to browse.

### Settled decisions

| Decision | Choice |
|---|---|
| Scope | Full browse view: models → versions → detail → download. Not a small addition to the existing result page. |
| Audience scoping | By `user_id`, matching the existing tenancy model (`model.user_id`, `UNIQUE(user_id, name)`). No auth exists yet — everything is uploaded under `demo-user` today, and that's fine; the route still takes `user_id` as a real parameter so nothing needs reworking when V1.5 auth lands. |
| Navigation | A new "Models" tab alongside the existing upload form on the same page. Upload stays the default view; browsing is one click away. The existing upload flow (`client/src/app/page.tsx`) is untouched. |
| Downloads | Presigned S3 URLs, generated on request, short-lived (15 minutes). The browser downloads directly from S3 — no file bytes pass through the Verity server. This is exactly how MLflow itself does it for an S3-backed artifact store. |
| Explicitly out of scope | Run comparison, metric-over-time charts, search/filtering, tagging, promotion/staging buttons. Browse, detail, download — not a full MLflow reimplementation. |

## Architecture

```
client/ "Models" tab
   ├─ ModelList        GET /users/{user_id}/models        → pick a model
   ├─ VersionList       GET /models/{model_id}/versions     → pick a version
   └─ VersionDetail      GET /model_versions/{id}            → EvidenceReport, reused as-is
                          GET /model_versions/{id}/download-urls → download buttons
```

Three new read-only GET routes plus one download-url route, all in `server/main.py`,
backed by a new module `server/registry.py` that assembles each response from
`metadata_store` queries — mirroring `server/telemetry.py`'s existing shape (a small,
focused module for one read-side concern, separate from `orchestrator.py`'s write-side
pipeline). `orchestrator.py` itself is untouched: its `build_artifact()` builds its
response from values it just computed in memory, mid-pipeline; `registry.py`'s functions
build the identical *shape* by re-querying already-stored rows, for a version that may
have been created any time in the past.

## The four new routes

### `GET /users/{user_id}/models`

Lists a user's models. One query, plus one `find_production_version` lookup per model to
report its current production version — accepted N+1 at V1 scale (this codebase's
existing pattern: relational storage now, an analytics/read-optimized store later if
volume ever demands it, same reasoning `Schemas.md` already applies to `telemetry_event`).

```json
{
  "user_id": "demo-user",
  "models": [
    {
      "id": "mdl_...",
      "name": "fraud-classifier",
      "model_class": "LogisticRegression",
      "task_type": "classification",
      "created_at": "2026-08-20T10:00:00+00:00",
      "production_version_id": "mv_..." | null
    }
  ]
}
```

### `GET /models/{model_id}/versions`

Lists every version of one model, newest first, **including archived ones** — that
history is the actual point of a registry view; MLflow shows every run, not just the
latest. Deliberately lean: no join to `eval_run` for a verdict here (that would be a
second N+1, one join per version, just to render a list) — `status` already tells you
whether a version passed (`production`/`archived`, both mean it passed at some point),
failed (`staging_failed`), or never got that far (`pending`). Full verdict detail lives
one click further in, at the detail route.

```json
{
  "model_id": "mdl_...",
  "versions": [
    {
      "id": "mv_...",
      "status": "production" | "archived" | "staging_failed" | "pending",
      "created_at": "2026-08-24T09:00:00+00:00"
    }
  ]
}
```

### `GET /model_versions/{model_version_id}`

The full detail bundle — same shape `POST /ingest` already returns for a freshly-created
version, except this route can fetch it for *any* version, at any time, by re-querying
stored rows instead of reading in-memory pipeline state. `registry.get_version_detail()`
does the assembly:

```python
def get_version_detail(*, model_version_id, metadata_store):
    version = metadata_store.find_model_version(model_version_id=model_version_id)
    if version is None:
        return None
    manifest = metadata_store.find_manifest(model_version_id=model_version_id)
    eval_run = metadata_store.find_eval_run(model_version_id=model_version_id)
    monitoring_config = metadata_store.find_monitoring_config(model_version_id=model_version_id)
    deployment = metadata_store.find_deployment(model_version_id=model_version_id)
    return {
        "model_version_id": model_version_id,
        "artifact_uri": version["artifact_uri"],
        "status": version["status"],
        "manifest": manifest,
        "eval_run": eval_run,
        "model_id": version["model_id"],
        "monitoring_config": monitoring_config,
        "deployment": deployment,
    }
```

The route returns this dict directly, or 404 when `model_version_id` doesn't exist. This
is deliberately the *exact* shape `IngestResult` already has on the frontend (minus
`deduplicated`/`archived_model_version_id`, which are ingest-call-specific, not properties
of a version) — so the frontend's existing `EvidenceReport` component renders it with zero
changes to that component. One shape, two ways to arrive at it: freshly, from `/ingest`,
or later, from this route.

### `GET /model_versions/{model_version_id}/download-urls`

```json
{
  "model_version_id": "mv_...",
  "artifact_url": "https://verity-artifacts.s3.amazonaws.com/...(presigned, 15 min)",
  "fixture_url": "https://...(presigned, 15 min)" | null
}
```

`artifact_url` comes from `model_version.artifact_sha256` (already its own column — no
URI parsing needed). `fixture_url` comes from `eval_run.fixture.sha256` when an eval_run
exists and has a fixture; `null` otherwise (a version with no fixture was never
evaluated, so there's nothing to download). 404 when the version itself doesn't exist.

## Storage layer additions

`server/storage/models/supabase.py` — six new methods, matching the file's existing
`select().eq().execute()` style throughout (see `find_model`, `find_model_by_version` for
the pattern being followed):

```python
def find_models_by_user(self, *, user_id):
    result = self.client.table("model").select("*").eq("user_id", user_id).execute()
    return result.data or []

def find_model_versions(self, *, model_id):
    result = (
        self.client.table("model_version")
        .select("*")
        .eq("model_id", model_id)
        .order("created_at", desc=True)
        .execute()
    )
    return result.data or []

def find_model_version(self, *, model_version_id):
    result = (
        self.client.table("model_version").select("*").eq("id", model_version_id).execute()
    )
    return result.data[0] if result.data else None

def find_manifest(self, *, model_version_id):
    result = (
        self.client.table("manifest")
        .select("*")
        .eq("model_version_id", model_version_id)
        .execute()
    )
    return result.data[0] if result.data else None

def find_eval_run(self, *, model_version_id):
    # One eval_run per version in every path that exists today (build_artifact() calls
    # evaluate/save_eval_run at most once per model_version_id) — ordering by started_at
    # is defensive, not because multiple rows are expected.
    result = (
        self.client.table("eval_run")
        .select("*")
        .eq("model_version_id", model_version_id)
        .order("started_at", desc=True)
        .execute()
    )
    return result.data[0] if result.data else None

def find_deployment(self, *, model_version_id):
    result = (
        self.client.table("deployment")
        .select("*")
        .eq("model_version_id", model_version_id)
        .order("created_at", desc=True)
        .execute()
    )
    return result.data[0] if result.data else None
```

`server/storage/models/s3.py` — one new method on `S3BlobStore`:

```python
def presigned_url(self, sha256: str, expires_in: int = 900) -> str:
    return self.client.generate_presigned_url(
        "get_object",
        Params={"Bucket": self.bucket, "Key": sha256},
        ExpiresIn=expires_in,
    )
```

15 minutes: long enough to actually complete a download of a model artifact (these are
small — tabular/classical-ML pickles, not gigabyte weights), short enough that a copied
URL isn't a standing credential leak sitting in someone's shell history.

## Routes wiring (`server/main.py`)

A new `get_blob_store()` dependency, extracted so `get_build_artifact()` and the new
download-urls route share one `S3BlobStore` instance instead of constructing two:

```python
@lru_cache
def get_blob_store():
    return S3BlobStore(bucket=os.environ["S3_BUCKET"], region=os.environ["S3_REGION"])

@lru_cache
def get_build_artifact():
    metadata_store = SupabaseMetadataStore()
    return partial(build_artifact, blob_store=get_blob_store(), metadata_store=metadata_store)
```

Four new routes, all thin — parse path params, call `registry.py`, 404 on `None`:

```python
@app.get("/users/{user_id}/models")
async def list_models(user_id: str, metadata_store=Depends(get_metadata_store)):
    return {"user_id": user_id, "models": registry.list_models(user_id=user_id, metadata_store=metadata_store)}

@app.get("/models/{model_id}/versions")
async def list_versions(model_id: str, metadata_store=Depends(get_metadata_store)):
    return {"model_id": model_id, "versions": registry.list_versions(model_id=model_id, metadata_store=metadata_store)}

@app.get("/model_versions/{model_version_id}")
async def read_version_detail(model_version_id: str, metadata_store=Depends(get_metadata_store)):
    detail = registry.get_version_detail(model_version_id=model_version_id, metadata_store=metadata_store)
    if detail is None:
        raise HTTPException(404, f"no model version {model_version_id!r}")
    return detail

@app.get("/model_versions/{model_version_id}/download-urls")
async def read_download_urls(
    model_version_id: str,
    metadata_store=Depends(get_metadata_store),
    blob_store=Depends(get_blob_store),
):
    urls = registry.get_download_urls(
        model_version_id=model_version_id, metadata_store=metadata_store, blob_store=blob_store
    )
    if urls is None:
        raise HTTPException(404, f"no model version {model_version_id!r}")
    return urls
```

`registry.py` itself holds `list_models`, `list_versions`, `get_version_detail` (shown
above), and `get_download_urls` — each a thin, pure function over its injected
`metadata_store`/`blob_store`, following the same injected-collaborator convention as
every other module in this codebase (no lazy-default wrapper needed here, since these are
read-only queries with no heavy/optional dependency to defer-import).

## Frontend

**`client/src/lib/verity.ts`** — four fetch functions and their types, following the
existing `fetchTelemetry`/`fetchAlerts` pattern exactly:

```typescript
export type ModelSummary = {
  id: string;
  name: string;
  model_class: string | null;
  task_type: string | null;
  created_at: string;
  production_version_id: string | null;
};

export type VersionSummary = {
  id: string;
  status: string;
  created_at: string;
};

export type DownloadUrls = {
  model_version_id: string;
  artifact_url: string;
  fixture_url: string | null;
};

export async function fetchModels(userId: string): Promise<ModelSummary[]> { ... }
export async function fetchVersions(modelId: string): Promise<VersionSummary[]> { ... }
export async function fetchVersionDetail(modelVersionId: string): Promise<IngestResult> { ... }
export async function fetchDownloadUrls(modelVersionId: string): Promise<DownloadUrls> { ... }
```

`fetchVersionDetail` returns `IngestResult` — the type already exists; the new route's
response shape was designed to match it exactly (see above), so no new type is needed
there.

**New components**, one responsibility each:

- `client/src/components/model-list.tsx` — fetches and renders `ModelSummary[]`; a click
  selects a model.
- `client/src/components/version-list.tsx` — takes a `modelId`, fetches and renders
  `VersionSummary[]`; a click selects a version. A "back to models" affordance.
- `client/src/components/version-detail.tsx` — takes a `modelVersionId`, fetches
  `IngestResult` via `fetchVersionDetail`, renders it with the **existing**
  `EvidenceReport` component unchanged, and adds a `DownloadButtons` sub-component that
  fetches `DownloadUrls` and renders `<a href={url} target="_blank">` links for artifact
  and (when present) fixture. A "back to versions" affordance.
- `client/src/components/models-browser.tsx` — the container: holds
  `selectedModelId`/`selectedVersionId` state and renders whichever of the three above is
  current. This is the component the new "Models" tab mounts.

**`client/src/app/page.tsx`** — one new piece of state, `view: "upload" | "models"`, two
tab buttons above the existing `<header>`, and a conditional render: `view === "upload"`
renders exactly what's there today (untouched), `view === "models"` renders
`<ModelsBrowser />`. No existing code in this file changes shape, only gets wrapped.

Download buttons are plain anchor tags to the presigned URL — no client-side fetch, no
blob handling, no download-progress state. The browser's native download behavior on a
direct S3 URL is the entire mechanism; this is deliberately as little frontend code as
downloading can take.

## Error handling

- Unknown `user_id` on `/users/{user_id}/models`: not an error — an empty `models: []`
  list. There's no "does this user exist" concept in Verity (no user table), so an empty
  list is the honest answer, not a 404.
- Unknown `model_id` on `/models/{model_id}/versions`: same reasoning, empty
  `versions: []` rather than a 404 — cheaper than a existence-check query for a
  distinction the frontend doesn't need to render differently anyway.
- Unknown `model_version_id` on the detail and download-urls routes: 404, since these
  *are* keyed to one specific real thing the frontend just clicked on — the frontend can
  and should distinguish "this version doesn't exist" from "this version has no data yet."
- A version whose `eval_run` is null (never evaluated) or whose `manifest` is null
  (a deduplicated dead-end that never got its own manifest row — see
  `orchestrator.py`'s dedup return path) is not an error; `get_version_detail` already
  returns `None` in those slots naturally, and `EvidenceReport` already handles both being
  null (it does today, for the equivalent case from `/ingest`).
- Download-urls when `fixture_url` would be null (no eval_run, or an eval_run with no
  fixture): not an error, `fixture_url: null`, and the frontend simply doesn't render that
  button.

## Testing

Matching this codebase's standing convention: hand-written fakes, no `unittest.mock`,
full-sentence test names.

| File | Covers |
|---|---|
| `server/tests/test_registry.py` (new) | `list_models`, `list_versions`, `get_version_detail` (present, and each null-slot case), `get_download_urls` (with and without a fixture) — each against a hand-written fake `metadata_store`/`blob_store` |
| `server/tests/test_main.py` (extended) | All four new routes: happy path, the empty-list cases, the two 404 cases, via `app.dependency_overrides` matching the existing route-test pattern |
| `server/tests/test_supabase.py` (extended) | The six new `SupabaseMetadataStore` methods' query-building, matching how existing `find_*` methods are already tested there |

Frontend: no test framework exists in `client/` today (matching the rest of this
project's frontend, which relies on `next build`'s type-check plus manual browser
verification) — this stays consistent with that. Manual verification: upload two versions
of the same model (one passing, one deliberately failing the eval gate), confirm the
Models tab shows both under one model entry with the right statuses, confirm the detail
view renders identically to what `/ingest` showed at upload time, and confirm both
download buttons produce a working direct-S3 download.

## Out of scope

Everything in the "explicitly out of scope" table above (comparison, charts, search,
tags, staging buttons), plus: pagination (a V1 user has, at most, a handful of models —
add it if that stops being true), any change to `/ingest`'s own response shape or to
`orchestrator.py`, and auth/privacy scoping beyond passing `user_id` through as a plain
parameter (explicitly deferred to V1.5, per the settled decisions table).
