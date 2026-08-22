# Verity client

A minimal intake form for exercising the pipeline by hand. Upload a model, watch all four
agents' real output render.

**This is a test harness, not a dashboard.** It shows the result of the upload you just made;
it cannot browse models you uploaded before, because no read endpoint exists server-side yet.

## Running

The server must be running on `http://localhost:8000` first (see [`../server/README.md`](../server/README.md)) —
the browser calls it directly, with no Next.js API route in between. CORS on the server is
scoped to `localhost:3000`, so use that port.

```bash
npm install
npm run dev
```

Then open http://localhost:3000. The **Bundled demo model** option (selected by default) loads
a pre-baked model + fixture from `public/demo/`, so the whole Hawkeye → Nat → Fury → Falcon
loop runs without Python involved at all.

## Layout

| Path | What it is |
|---|---|
| `src/app/page.tsx` | the intake form — the only stateful component |
| `src/lib/verity.ts` | `sha256Hex()` (Web Crypto) + `ingest()` — the same wire format `verity.assemble()` uses |
| `src/components/` | `verdict-stamp.tsx`, `evidence-report.tsx`, `telemetry-panel.tsx` — pure render, no logic |
| `public/demo/` | a model and fixture generated once through the real SDK, so their bytes match what a genuine upload produces |

The client computes SHA-256 in the browser because the server re-verifies the digest against
the actual bytes it received. The frontend gets no more latitude to send a stale or wrong hash
than the CLI does.

## Next.js

This is Next.js 16 with the App Router, and it has breaking changes relative to most training
data and tutorials — see [`AGENTS.md`](AGENTS.md). Read `node_modules/next/dist/docs/` before
reaching for a remembered API.
