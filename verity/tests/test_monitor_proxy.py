import pytest

from verity.monitor import MonitoredModel


class FakeReporter:
    def __init__(self):
        self.records = []

    def record(self, *, latency_ms, status, error_type):
        self.records.append(
            {"latency_ms": latency_ms, "status": status, "error_type": error_type}
        )


class FakeModel:
    coef_ = [1, 2, 3]

    def predict(self, X):
        return [0 for _ in X]

    def predict_proba(self, X):
        return [[0.5, 0.5] for _ in X]


def test_predict_returns_the_models_real_result_unchanged():
    monitored = MonitoredModel(FakeModel(), reporter=FakeReporter())

    assert monitored.predict([[1.0], [2.0]]) == [0, 0]


def test_a_successful_predict_is_recorded_as_ok_with_a_real_latency():
    reporter = FakeReporter()
    monitored = MonitoredModel(FakeModel(), reporter=reporter)

    monitored.predict([[1.0]])

    assert len(reporter.records) == 1
    assert reporter.records[0]["status"] == "ok"
    assert reporter.records[0]["error_type"] is None
    assert reporter.records[0]["latency_ms"] >= 0


def test_a_model_that_raises_still_raises_the_same_exception_to_the_caller():
    class Exploding:
        def predict(self, X):
            raise ValueError("feature order mismatch")

    reporter = FakeReporter()
    monitored = MonitoredModel(Exploding(), reporter=reporter)

    with pytest.raises(ValueError) as excinfo:
        monitored.predict([[1.0]])

    assert "feature order mismatch" in str(excinfo.value)
    assert reporter.records[0]["status"] == "error"
    assert reporter.records[0]["error_type"] == "ValueError"


def test_a_broken_reporter_never_breaks_the_customers_inference():
    class BrokenReporter:
        def record(self, **_):
            raise RuntimeError("telemetry backend exploded")

    monitored = MonitoredModel(FakeModel(), reporter=BrokenReporter())

    assert monitored.predict([[1.0]]) == [0]


def test_every_other_attribute_delegates_to_the_wrapped_model():
    monitored = MonitoredModel(FakeModel(), reporter=FakeReporter())

    assert monitored.coef_ == [1, 2, 3]


def test_predict_proba_is_monitored_when_the_model_has_it():
    reporter = FakeReporter()
    monitored = MonitoredModel(FakeModel(), reporter=reporter)

    assert monitored.predict_proba([[1.0]]) == [[0.5, 0.5]]
    assert reporter.records[0]["status"] == "ok"


def test_predict_proba_raises_attribute_error_when_the_model_lacks_it():
    class LabelsOnly:
        def predict(self, X):
            return [0]

    monitored = MonitoredModel(LabelsOnly(), reporter=FakeReporter())

    with pytest.raises(AttributeError):
        monitored.predict_proba([[1.0]])
