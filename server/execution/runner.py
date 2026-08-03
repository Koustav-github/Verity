"""Child process for sandboxed model execution.

Reads a cloudpickled job from stdin, runs the model, writes cloudpickled outputs to
stdout. Deliberately standalone: it imports nothing from the server or the agents, and
the parent starts it with a scrubbed environment, so a hostile artifact that reaches
`predict()` still has no credentials to steal.

Instrumentation lives here rather than in any one mechanism, so every eval mechanism
that ever runs through this sandbox gets the same systemic sample for free.
"""

import sys
import time

import cloudpickle

LATENCY_SAMPLE_ROWS = 200


def _n_rows(X):
    shape = getattr(X, "shape", None)
    return int(shape[0]) if shape else len(X)


def _percentiles(timings_ms):
    import numpy as np

    values = np.asarray(timings_ms)
    return {
        "latency_p50_ms": float(np.percentile(values, 50)),
        "latency_p95_ms": float(np.percentile(values, 95)),
        "latency_p99_ms": float(np.percentile(values, 99)),
    }


def _gpu_memory_mb():
    # Real GPU-aware dispatch is V3; until then this is recorded when it happens to be
    # observable and reported as null otherwise, rather than guessed.
    try:
        import torch

        if not torch.cuda.is_available():
            return None
        return torch.cuda.max_memory_allocated() / (1024 * 1024)
    except Exception:
        return None


def _memory_and_cpu():
    import psutil

    process = psutil.Process()
    mem = process.memory_info()
    cpu = process.cpu_times()
    return {
        # peak_wset is the Windows high-water mark; elsewhere current rss is the
        # best cross-platform reading available without sampling in a thread.
        "peak_memory_mb": getattr(mem, "peak_wset", None) or mem.rss,
        "cpu_time_s": cpu.user + cpu.system,
    }


def run_job(job):
    # Unpickling customer bytes IS the job here, and it is deliberate: this process is
    # the containment boundary. It runs with a scrubbed environment (no store, blob, or
    # LLM credentials), holds no network clients, and is killed at a timeout by the
    # parent. Never call run_job() in-process — that would remove the only protection.
    model = cloudpickle.loads(job["model_bytes"])
    X = job["X"]

    batch_started = time.perf_counter()
    y_pred = model.predict(X)
    batch_elapsed = time.perf_counter() - batch_started

    y_proba = model.predict_proba(X) if hasattr(model, "predict_proba") else None

    rows = _n_rows(X)
    timings_ms = []
    for i in range(min(rows, LATENCY_SAMPLE_ROWS)):
        started = time.perf_counter()
        model.predict(X[i : i + 1])
        timings_ms.append((time.perf_counter() - started) * 1000)

    resource = _percentiles(timings_ms)
    resource["throughput_rps"] = rows / batch_elapsed if batch_elapsed > 0 else 0.0
    usage = _memory_and_cpu()
    resource["peak_memory_mb"] = usage["peak_memory_mb"] / (1024 * 1024)
    resource["cpu_time_s"] = usage["cpu_time_s"]
    resource["gpu_memory_mb"] = _gpu_memory_mb()

    return {
        "y_pred": y_pred,
        "y_proba": y_proba.tolist() if hasattr(y_proba, "tolist") else y_proba,
        "resource": resource,
    }


def main():
    try:
        job = cloudpickle.loads(sys.stdin.buffer.read())
        result = run_job(job)
    except BaseException as exc:  # noqa: BLE001 - the parent decides what a failure means
        import traceback

        result = {
            "error": {
                "type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            }
        }
    sys.stdout.buffer.write(cloudpickle.dumps(result))
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
