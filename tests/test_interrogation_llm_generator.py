from verity.interrogation.llm_generator import LLMQuestionGenerator


class _FakeLLM:
    """Records the prompt it was given and returns a canned response."""

    def __init__(self, response):
        self.response = response
        self.last_prompt = None

    def complete(self, prompt):
        self.last_prompt = prompt
        return self.response


def test_parses_qa_pairs_into_testcases():
    resp = '[{"question": "What tax on 7.5L?", "answer": "5%"}]'
    gen = LLMQuestionGenerator(client=_FakeLLM(resp))

    cases = gen.generate("5% tax applies to the 5L-10L bracket")

    assert len(cases) == 1
    assert cases[0].question == "What tax on 7.5L?"
    assert cases[0].reference_answer == "5%"
    assert cases[0].reference_context == "5% tax applies to the 5L-10L bracket"


def test_prompt_includes_the_chunk():
    client = _FakeLLM("[]")
    LLMQuestionGenerator(client=client).generate("UNIQUE_CHUNK_TEXT")
    assert "UNIQUE_CHUNK_TEXT" in client.last_prompt


def test_malformed_response_returns_empty_list():
    gen = LLMQuestionGenerator(client=_FakeLLM("not valid json"))
    assert gen.generate("chunk") == []


def test_skips_items_missing_fields():
    resp = '[{"question": "ok?", "answer": "a"}, {"question": "no answer"}]'
    gen = LLMQuestionGenerator(client=_FakeLLM(resp))
    cases = gen.generate("chunk")
    assert len(cases) == 1
    assert cases[0].question == "ok?"


def test_non_list_json_returns_empty():
    gen = LLMQuestionGenerator(client=_FakeLLM('{"question": "x", "answer": "y"}'))
    assert gen.generate("chunk") == []


def test_parses_json_wrapped_in_code_fences():
    resp = '```json\n[{"question": "q?", "answer": "a"}]\n```'
    gen = LLMQuestionGenerator(client=_FakeLLM(resp))
    cases = gen.generate("chunk")
    assert len(cases) == 1
    assert cases[0].question == "q?"


def test_prompt_enforces_passage_only_grounding():
    prompt = LLMQuestionGenerator(client=_FakeLLM("[]")).build_prompt("X").lower()
    # must forbid outside/external knowledge
    assert any(
        p in prompt
        for p in ["outside knowledge", "external knowledge", "prior knowledge"]
    )
    # answers must be answerable from the passage itself
    assert any(
        p in prompt
        for p in ["answerable from the passage", "stated in the passage", "from the passage alone"]
    )
