"""Server-side telemetry buffer for the inference proxy.

Mirrors the SDK reporter's contract deliberately: a request path must never wait on a
monitoring write. Recording synchronously would put a database round trip in front of
every single prediction — exactly the cost the SDK already refuses to pay, and there is
no reason the proxy should pay it instead.
"""

import queue
import threading


class TelemetrySink:
    def __init__(self, metadata_store, maxsize=10_000, flush_interval=5.0, detect_fn=None):
        self.metadata_store = metadata_store
        self.queue = queue.Queue(maxsize=maxsize)
        self.flush_interval = flush_interval
        # Bound *after* self.metadata_store exists, not a @staticmethod: the default
        # must reuse the store already injected into this sink, not construct a second
        # real one. A version of this that built its own SupabaseMetadataStore() would
        # both duplicate the connection and silently ignore a fake store injected for
        # tests, since detect_fn would call the real thing regardless.
        self.detect_fn = detect_fn or self._default_detect_fn
        self.dropped = 0
        self._dropped_lock = threading.Lock()
        self._stopping = threading.Event()
        self._thread = None

    def _default_detect_fn(self, model_version_id):
        from agents.brain4.falcon.monitor import check_systemic

        check_systemic(model_version_id=model_version_id, metadata_store=self.metadata_store)

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def record(self, event):
        try:
            self.queue.put_nowait(event)
        except queue.Full:
            # Drop and count. An overwhelmed monitoring path degrades itself, never the
            # request path it is watching.
            with self._dropped_lock:
                self.dropped += 1

    def flush(self):
        events = []
        while True:
            try:
                events.append(self.queue.get_nowait())
            except queue.Empty:
                break
        if not events:
            return 0
        try:
            written = self.metadata_store.save_telemetry_events(events=events)
        except Exception:  # noqa: BLE001 - a failed telemetry write is not an incident
            return 0

        # Detection runs after the write succeeds, once per distinct version in this
        # batch — never per event, and never ahead of the data it needs existing.
        for model_version_id in {e["model_version_id"] for e in events}:
            try:
                self.detect_fn(model_version_id)
            except Exception:  # noqa: BLE001 - detection failing must not undo a write
                pass
        return written

    def stop(self):
        self._stopping.set()
        if self._thread is not None:
            self._thread.join(timeout=self.flush_interval + 1)
            self._thread = None
        self.flush()

    def _loop(self):
        # wait() returns True only when stop() sets the event, so this both paces the
        # flush and exits promptly on shutdown instead of sleeping through it.
        while not self._stopping.wait(self.flush_interval):
            self.flush()
