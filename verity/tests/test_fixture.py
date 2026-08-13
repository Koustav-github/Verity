import cloudpickle

from verity.fixture import labeled_holdout


def test_labeled_holdout_bundles_features_and_labels_into_one_addressable_blob():
    payload, descriptor = labeled_holdout([[0.0], [1.0], [2.0]], [0, 0, 1])

    assert cloudpickle.loads(payload) == {"X": [[0.0], [1.0], [2.0]], "y": [0, 0, 1]}
    assert descriptor["kind"] == "labeled_holdout"
    assert descriptor["sha256"] == __import__("hashlib").sha256(payload).hexdigest()


def test_the_descriptor_carries_the_shape_the_server_profiles_without_opening_the_blob():
    _, descriptor = labeled_holdout([[0.0, 1.0], [1.0, 2.0], [2.0, 3.0]], [0, 1, 1])

    assert descriptor["spec"] == {"n_samples": 3, "n_features": 2}


def test_the_same_holdout_always_addresses_to_the_same_digest():
    first, first_descriptor = labeled_holdout([[0.0]], [1])
    second, second_descriptor = labeled_holdout([[0.0]], [1])

    assert first == second
    assert first_descriptor["sha256"] == second_descriptor["sha256"]


def test_a_holdout_whose_rows_do_not_line_up_with_its_labels_is_rejected():
    try:
        labeled_holdout([[0.0], [1.0]], [0, 1, 1])
    except ValueError as exc:
        assert "2" in str(exc) and "3" in str(exc)
    else:
        raise AssertionError("expected a ValueError for mismatched X and y lengths")
