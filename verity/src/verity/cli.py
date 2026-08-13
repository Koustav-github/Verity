import argparse

import cloudpickle

from verity.client import assemble


def main(argv=None, assemble_fn=assemble):
    parser = argparse.ArgumentParser(prog="verity")
    parser.add_argument("model_path", nargs="?", help="path to a cloudpickled model file")
    parser.add_argument("--demo", action="store_true", help="use a tiny built-in demo model instead of a file")
    parser.add_argument("--test-set", help="path to a cloudpickled (X_test, y_test) tuple to evaluate against")
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--name", required=True, help="identifies this model across versions")
    parser.add_argument("--endpoint", default="http://127.0.0.1:8000")
    args = parser.parse_args(argv)

    X_test = y_test = None

    if args.demo:
        from sklearn.linear_model import LogisticRegression

        X, y = [[0], [1], [2], [3]], [0, 0, 1, 1]
        model = LogisticRegression().fit(X, y)
        # The demo carries its own holdout so `--demo` walks the full loop —
        # identify, evaluate, gate — rather than stopping at identification.
        X_test, y_test = X, y
    elif args.model_path:
        with open(args.model_path, "rb") as f:
            model = cloudpickle.load(f)
    else:
        parser.error("either a model_path or --demo is required")

    if args.test_set:
        with open(args.test_set, "rb") as f:
            X_test, y_test = cloudpickle.load(f)

    result = assemble_fn(
        model,
        user_id=args.user_id,
        name=args.name,
        endpoint=args.endpoint,
        X_test=X_test,
        y_test=y_test,
    )
    print(result)


if __name__ == "__main__":
    main()
