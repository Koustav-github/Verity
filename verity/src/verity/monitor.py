import time


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
