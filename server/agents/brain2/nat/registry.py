from agents.brain2.nat.mechanisms import labeled_holdout


class UnsupportedFixture(Exception):
    """No registered mechanism consumes this fixture kind."""


# fixture kind -> mechanism module. Widening Nat to a new model class is a module
# here plus a line here, not a change to evaluate.py / orchestrator.py / main.py:
#   image_folder      -> V3 (DL)
#   corpus_index      -> V4 (RAG, interrogation)
#   environment_ref   -> V5 (RL, rollout)
MECHANISMS = {
    labeled_holdout.KIND: labeled_holdout,
}


def for_fixture(fixture):
    kind = fixture.get("kind")
    if kind not in MECHANISMS:
        raise UnsupportedFixture(
            f"no eval mechanism handles fixture kind {kind!r}; "
            f"registered kinds are {sorted(MECHANISMS)}"
        )
    return MECHANISMS[kind]
