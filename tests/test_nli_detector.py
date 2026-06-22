def test_constructible_without_heavy_deps():
    """NLIDetector must import/construct without torch (model loads lazily)."""
    from verity.detectors.nli import NLIDetector

    d = NLIDetector()
    assert d.get_name() == "nli"
    assert d._model is None  # not loaded until entailment() is called
