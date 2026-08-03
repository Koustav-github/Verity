import pytest

from agents.brain2.nat.mechanisms import labeled_holdout
from agents.brain2.nat.registry import UnsupportedFixture, for_fixture


def test_a_labeled_holdout_fixture_dispatches_to_the_labeled_holdout_mechanism():
    mechanism = for_fixture({"kind": "labeled_holdout", "uri": "s3://b/abc"})

    assert mechanism is labeled_holdout


def test_a_fixture_kind_no_mechanism_handles_is_rejected_by_name():
    with pytest.raises(UnsupportedFixture) as excinfo:
        for_fixture({"kind": "corpus_index", "uri": "s3://b/abc"})

    assert "corpus_index" in str(excinfo.value)
