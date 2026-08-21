from verity.monitor import TelemetryReporter, monitor


class FakeTransport:
    def __init__(self):
        self.batches = []

    def send(self, events):
        self.batches.append(events)


class ExplodingTransport:
    def send(self, events):
        raise ConnectionError("server unreachable")


def a_reporter(transport, **kwargs):
    # A long flush interval keeps the background thread out of the way so tests drive
    # flushing explicitly and stay deterministic — never sleep in a test.
    return TelemetryReporter(
        model_version_id="mv_1",
        endpoint="http://verity.test",
        transport=transport,
        flush_interval=3600,
        **kwargs,
    )


def test_recorded_events_are_sent_on_flush_with_the_version_and_a_timestamp():
    transport = FakeTransport()
    reporter = a_reporter(transport)

    reporter.record(latency_ms=1.5, status="ok", error_type=None)
    reporter.flush()

    assert len(transport.batches) == 1
    event = transport.batches[0][0]
    assert event["model_version_id"] == "mv_1"
    assert event["latency_ms"] == 1.5
    assert event["status"] == "ok"
    assert event["occurred_at"].endswith("+00:00")


def test_nothing_is_sent_when_there_is_nothing_recorded():
    transport = FakeTransport()
    reporter = a_reporter(transport)

    reporter.flush()

    assert transport.batches == []


def test_events_are_sent_in_batches_rather_than_one_request_each():
    transport = FakeTransport()
    reporter = a_reporter(transport, batch_size=2)

    for _ in range(5):
        reporter.record(latency_ms=1.0, status="ok", error_type=None)
    reporter.flush()

    assert [len(batch) for batch in transport.batches] == [2, 2, 1]


def test_a_full_queue_drops_events_instead_of_blocking_the_caller():
    transport = FakeTransport()
    reporter = a_reporter(transport, maxsize=2)

    for _ in range(5):
        reporter.record(latency_ms=1.0, status="ok", error_type=None)

    assert reporter.dropped == 3
    reporter.flush()
    assert sum(len(batch) for batch in transport.batches) == 2


def test_a_transport_that_is_down_never_raises_into_the_caller():
    reporter = a_reporter(ExplodingTransport())

    reporter.record(latency_ms=1.0, status="ok", error_type=None)
    reporter.flush()  # must not raise


def test_the_background_thread_delivers_without_an_explicit_flush():
    import threading

    delivered = threading.Event()

    class SignallingTransport:
        def __init__(self):
            self.batches = []

        def send(self, events):
            self.batches.append(events)
            delivered.set()

    transport = SignallingTransport()
    reporter = TelemetryReporter(
        model_version_id="mv_1",
        endpoint="http://verity.test",
        transport=transport,
        flush_interval=0.01,
    )
    try:
        reporter.record(latency_ms=1.0, status="ok", error_type=None)
        # Waits on a real event the transport sets, with a generous ceiling — this is a
        # bounded wait on an actual signal, not a sleep guessing at timing.
        assert delivered.wait(timeout=5.0), "background thread never delivered the batch"
    finally:
        reporter.stop()


def test_stopping_the_reporter_ends_its_background_thread():
    class FakeTransport:
        def send(self, events):
            pass

    reporter = TelemetryReporter(
        model_version_id="mv_1",
        endpoint="http://verity.test",
        transport=FakeTransport(),
        flush_interval=3600,
    )

    reporter.stop()

    assert not reporter._thread.is_alive()


def test_stopping_twice_is_safe():
    class FakeTransport:
        def send(self, events):
            pass

    reporter = TelemetryReporter(
        model_version_id="mv_1", endpoint="http://verity.test",
        transport=FakeTransport(), flush_interval=3600,
    )

    reporter.stop()
    reporter.stop()  # must not raise


def test_stopping_drains_what_was_already_queued():
    class FakeTransport:
        def __init__(self):
            self.batches = []

        def send(self, events):
            self.batches.append(events)

    transport = FakeTransport()
    reporter = TelemetryReporter(
        model_version_id="mv_1", endpoint="http://verity.test",
        transport=transport, flush_interval=3600,
    )

    reporter.record(latency_ms=1.0, status="ok", error_type=None)
    reporter.stop()

    assert sum(len(batch) for batch in transport.batches) == 1


def test_monitor_returns_a_proxy_whose_predictions_reach_the_transport():
    class FakeModel:
        def predict(self, X):
            return [1]

    transport = FakeTransport()
    monitored = monitor(
        FakeModel(),
        model_version_id="mv_9",
        transport=transport,
        flush_interval=3600,
    )

    assert monitored.predict([[0.0]]) == [1]
    monitored.flush()

    assert transport.batches[0][0]["model_version_id"] == "mv_9"
