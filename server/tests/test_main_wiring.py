import orchestrator

import main


def test_get_build_artifact_wires_real_s3_and_supabase_stores(monkeypatch):
    monkeypatch.setenv("S3_BUCKET", "verity-artifacts")
    monkeypatch.setenv("S3_REGION", "us-east-1")
    monkeypatch.setenv("SUPABASE_URL", "http://supabase.test")
    monkeypatch.setenv("SUPABASE_KEY", "test-key")
    main.get_blob_store.cache_clear()
    main.get_build_artifact.cache_clear()

    build_artifact_fn = main.get_build_artifact()

    assert build_artifact_fn.func is main.build_artifact
    assert isinstance(build_artifact_fn.keywords["blob_store"], main.S3BlobStore)
    assert build_artifact_fn.keywords["blob_store"].bucket == "verity-artifacts"
    assert isinstance(build_artifact_fn.keywords["metadata_store"], main.SupabaseMetadataStore)


def test_the_default_evaluator_runs_nat_against_the_real_sandbox(monkeypatch):
    import agents.brain2.nat.evaluate as nat_evaluate
    from execution.sandbox import execute

    seen = {}
    monkeypatch.setattr(nat_evaluate, "evaluate", lambda **kwargs: seen.update(kwargs))

    orchestrator._default_evaluate(manifest={}, fixture={}, data={}, model_payload=b"")

    assert seen["execute_fn"] is execute
