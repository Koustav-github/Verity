def test_constructible_without_heavy_deps():
    """Importing/constructing EmbeddingDetector must not require torch/sklearn
    (models load lazily on first use)."""
    from verity.detectors.embeddings import EmbeddingDetector

    d = EmbeddingDetector()
    assert d.get_name() == "embedding"
    assert d._model is None  # not loaded until score() is called
