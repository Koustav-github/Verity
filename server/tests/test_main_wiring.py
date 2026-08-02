import main


def test_get_build_artifact_wires_real_seaweedfs_and_supabase_stores(monkeypatch):
    monkeypatch.setenv("SEAWEEDFS_ENDPOINT_URL", "http://seaweedfs.test:8333")
    monkeypatch.setenv("SEAWEEDFS_BUCKET", "verity-artifacts")
    monkeypatch.setenv("SUPABASE_URL", "http://supabase.test")
    monkeypatch.setenv("SUPABASE_KEY", "test-key")
    main.get_build_artifact.cache_clear()

    build_artifact_fn = main.get_build_artifact()

    assert build_artifact_fn.func is main.build_artifact
    assert isinstance(build_artifact_fn.keywords["blob_store"], main.SeaweedFSStore)
    assert isinstance(build_artifact_fn.keywords["metadata_store"], main.SupabaseMetadataStore)
