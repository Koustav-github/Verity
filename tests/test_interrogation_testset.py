from verity.interrogation.testset import TestCase, TestSet


def _case(q="q?", ctx="ctx", ans="a"):
    return TestCase(question=q, reference_context=ctx, reference_answer=ans)


def test_testcase_exposes_core_fields():
    tc = TestCase(
        question="What year was X founded?",
        reference_context="X was founded in 1998.",
        reference_answer="1998",
    )
    assert tc.question == "What year was X founded?"
    assert tc.reference_context == "X was founded in 1998."
    assert tc.reference_answer == "1998"


def test_testset_reports_length():
    ts = TestSet(cases=[_case(), _case()])
    assert len(ts) == 2


def test_testset_is_iterable_over_cases():
    c1, c2 = _case(q="one"), _case(q="two")
    ts = TestSet(cases=[c1, c2])
    assert [c.question for c in ts] == ["one", "two"]


def test_testset_jsonl_roundtrip(tmp_path):
    ts = TestSet(cases=[_case(q="one", ctx="c1", ans="a1"),
                        _case(q="two", ctx="c2", ans="a2")])
    path = tmp_path / "eval.jsonl"

    ts.to_jsonl(path)
    loaded = TestSet.from_jsonl(path)

    assert len(loaded) == 2
    assert [c.question for c in loaded] == ["one", "two"]
    assert loaded.cases[0] == ts.cases[0]


def test_testset_jsonl_is_one_object_per_line(tmp_path):
    ts = TestSet(cases=[_case(), _case(), _case()])
    path = tmp_path / "eval.jsonl"
    ts.to_jsonl(path)
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
