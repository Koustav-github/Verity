import hashlib

from verity.serialize import serialize


def test_serialize_returns_bytes_and_sha256_of_those_bytes():
    model = {"kind": "fake-model", "weights": [1, 2, 3]}

    payload, digest = serialize(model)

    assert isinstance(payload, bytes)
    assert digest == hashlib.sha256(payload).hexdigest()


def test_serialize_round_trips_a_fitted_sklearn_model():
    import cloudpickle
    from sklearn.linear_model import LogisticRegression

    model = LogisticRegression().fit([[0], [1], [2], [3]], [0, 0, 1, 1])

    payload, digest = serialize(model)
    # Safe here: round-tripping our own just-created bytes, not untrusted
    # input. Loading a customer-uploaded artifact must happen in the sandbox
    # executor instead — see the transport/orchestrator design notes.
    restored = cloudpickle.loads(payload)

    assert digest == hashlib.sha256(payload).hexdigest()
    assert restored.predict([[3]]) == model.predict([[3]])
