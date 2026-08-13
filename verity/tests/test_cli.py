from verity.cli import main


def test_demo_flag_trains_a_model_and_calls_assemble_with_it():
    calls = []

    def fake_assemble(model, user_id, name, endpoint, **kwargs):
        calls.append({"model": model, "user_id": user_id, "name": name, "endpoint": endpoint, **kwargs})
        return {"model_version_id": "mv_test", "status": "pending"}

    main(
        ["--demo", "--user-id", "cli-test-user", "--name", "demo-model", "--endpoint", "http://example.test"],
        assemble_fn=fake_assemble,
    )

    assert len(calls) == 1
    assert calls[0]["user_id"] == "cli-test-user"
    assert calls[0]["name"] == "demo-model"
    assert calls[0]["endpoint"] == "http://example.test"
    assert hasattr(calls[0]["model"], "predict")


def test_the_demo_ships_a_holdout_so_it_exercises_the_whole_loop():
    calls = []

    def fake_assemble(model, user_id, name, endpoint, **kwargs):
        calls.append(kwargs)
        return {"status": "staging"}

    main(["--demo", "--user-id", "cli-test-user", "--name", "demo-model"], assemble_fn=fake_assemble)

    assert len(calls[0]["X_test"]) == len(calls[0]["y_test"])
    assert len(calls[0]["y_test"]) > 0


def test_model_path_loads_a_cloudpickled_file_and_calls_assemble_with_it(tmp_path):
    import cloudpickle

    model_path = tmp_path / "model.pkl"
    model_path.write_bytes(cloudpickle.dumps({"kind": "fake-model"}))

    calls = []

    def fake_assemble(model, user_id, name, endpoint, **kwargs):
        calls.append(model)
        return {"model_version_id": "mv_test", "status": "pending"}

    main(
        [str(model_path), "--user-id", "cli-test-user", "--name", "my-model"],
        assemble_fn=fake_assemble,
    )

    assert calls == [{"kind": "fake-model"}]


def test_a_test_set_file_is_loaded_and_passed_as_the_holdout(tmp_path):
    import cloudpickle

    model_path = tmp_path / "model.pkl"
    model_path.write_bytes(cloudpickle.dumps({"kind": "fake-model"}))
    test_set_path = tmp_path / "holdout.pkl"
    test_set_path.write_bytes(cloudpickle.dumps(([[0.0], [1.0]], [0, 1])))

    calls = []

    def fake_assemble(model, user_id, name, endpoint, **kwargs):
        calls.append(kwargs)
        return {"status": "staging"}

    main(
        [str(model_path), "--user-id", "u_1", "--name", "my-model", "--test-set", str(test_set_path)],
        assemble_fn=fake_assemble,
    )

    assert calls[0]["X_test"] == [[0.0], [1.0]]
    assert calls[0]["y_test"] == [0, 1]


def test_a_model_without_a_test_set_is_uploaded_with_no_holdout(tmp_path):
    import cloudpickle

    model_path = tmp_path / "model.pkl"
    model_path.write_bytes(cloudpickle.dumps({"kind": "fake-model"}))

    calls = []

    def fake_assemble(model, user_id, name, endpoint, **kwargs):
        calls.append(kwargs)
        return {"status": "pending"}

    main([str(model_path), "--user-id", "u_1", "--name", "my-model"], assemble_fn=fake_assemble)

    assert calls[0]["X_test"] is None
    assert calls[0]["y_test"] is None


def test_name_is_required():
    import pytest

    with pytest.raises(SystemExit):
        main(["--demo", "--user-id", "cli-test-user"], assemble_fn=lambda **_: {})
