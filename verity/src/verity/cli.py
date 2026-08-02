import argparse

import cloudpickle

from verity.client import assemble


def main(argv=None, assemble_fn=assemble):
    parser = argparse.ArgumentParser(prog="verity")
    parser.add_argument("model_path", nargs="?", help="path to a cloudpickled model file")
    parser.add_argument("--demo", action="store_true", help="use a tiny built-in demo model instead of a file")
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8000")
    args = parser.parse_args(argv)

    if args.demo:
        from sklearn.linear_model import LogisticRegression

        model = LogisticRegression().fit([[0], [1], [2], [3]], [0, 0, 1, 1])
    elif args.model_path:
        with open(args.model_path, "rb") as f:
            model = cloudpickle.load(f)
    else:
        parser.error("either a model_path or --demo is required")

    result = assemble_fn(model, user_id=args.user_id, endpoint=args.endpoint)
    print(result)


if __name__ == "__main__":
    main()
