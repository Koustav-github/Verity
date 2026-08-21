import atexit
import queue
import threading
import time
from datetime import datetime, timezone


class MonitoredModel:
    """A transparent proxy that times predict() and reports it, changing nothing else.

    The governing rule: telemetry must never be why the customer's inference fails or
    slows. Recording is best-effort and swallows its own exceptions; the model's own
    exception always propagates unchanged.
    """

    def __init__(self, model, *, reporter):
        # Set via __dict__ so __getattr__ delegation never sees these two names.
        object.__setattr__(self, "_model", model)
        object.__setattr__(self, "_reporter", reporter)

    def __getattr__(self, item):
        # Only reached when normal lookup fails, so predict/predict_proba below win.
        return getattr(self._model, item)

    def predict(self, X, *args, **kwargs):
        return self._call("predict", X, *args, **kwargs)

    def predict_proba(self, X, *args, **kwargs):
        if not hasattr(self._model, "predict_proba"):
            raise AttributeError(
                f"{type(self._model).__name__!r} object has no attribute 'predict_proba'"
            )
        return self._call("predict_proba", X, *args, **kwargs)

    def _call(self, method_name, X, *args, **kwargs):
        started = time.perf_counter()
        try:
            result = getattr(self._model, method_name)(X, *args, **kwargs)
        except BaseException as exc:
            self._record(
                latency_ms=(time.perf_counter() - started) * 1000,
                status="error",
                error_type=type(exc).__name__,
            )
            raise
        self._record(
            latency_ms=(time.perf_counter() - started) * 1000,
            status="ok",
            error_type=None,
        )
        return result

    def _record(self, **kwargs):
        try:
            self._reporter.record(**kwargs)
        except Exception:
            # A telemetry failure is never allowed to surface into the caller's
            # inference path — that is the whole contract of this wrapper.
            pass

    def flush(self):
        """Drain buffered telemetry now. Called automatically at process exit."""
        self._reporter.flush()


class _HttpTransport:
    def __init__(self, endpoint, client=None):
        self._endpoint = endpoint
        self._client = client

    def send(self, events):
        if self._client is None:
            import httpx

            self._client = httpx.Client(timeout=10.0)
        self._client.post(f"{self._endpoint}/telemetry", json={"events": events})


class TelemetryReporter:
    """Buffers telemetry and ships it in batches from a background thread.

    Nothing here is allowed to block or break the caller: enqueue is non-blocking and
    drops on overflow, sending happens off the predict path, and transport failures are
    swallowed. Losing telemetry is always preferable to degrading the customer's serving.
    """

    def __init__(
        self,
        *,
        model_version_id,
        endpoint,
        transport=None,
        maxsize=10_000,
        batch_size=100,
        flush_interval=5.0,
    ):
        self._model_version_id = model_version_id
        self._transport = transport or _HttpTransport(endpoint)
        self._queue = queue.Queue(maxsize=maxsize)
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self.dropped = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        atexit.register(self.flush)

    def record(self, *, latency_ms, status, error_type):
        try:
            self._queue.put_nowait(
                {
                    "model_version_id": self._model_version_id,
                    "occurred_at": datetime.now(timezone.utc).isoformat(),
                    "latency_ms": latency_ms,
                    "status": status,
                    "error_type": error_type,
                }
            )
        except queue.Full:
            # Dropping is the correct failure mode: blocking here would add the
            # telemetry backlog to the customer's inference latency.
            self.dropped += 1

    def flush(self):
        while True:
            batch = self._next_batch()
            if not batch:
                return
            self._send(batch)

    def _next_batch(self):
        batch = []
        while len(batch) < self._batch_size:
            try:
                batch.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return batch

    def _send(self, batch):
        try:
            self._transport.send(batch)
        except Exception:
            pass

    def _loop(self):
        while not self._stop.wait(self._flush_interval):
            self.flush()


def monitor(
    model,
    *,
    model_version_id,
    endpoint="http://localhost:8000",
    transport=None,
    flush_interval=5.0,
):
    """Wrap a model so its predictions are reported to Verity.

    `model_version_id` is the id returned by assemble(). Upload and serving usually happen
    in different processes, so it is passed explicitly rather than remembered.
    """
    reporter = TelemetryReporter(
        model_version_id=model_version_id,
        endpoint=endpoint,
        transport=transport,
        flush_interval=flush_interval,
    )
    return MonitoredModel(model, reporter=reporter)
