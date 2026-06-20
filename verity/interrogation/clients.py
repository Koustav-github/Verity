from typing import Optional


class OpenAICompatibleClient:
    """LLM client adapter for any OpenAI-compatible Chat Completions endpoint.

    Works with OpenAI, Together, Groq, OpenRouter, and local servers (vLLM/Ollama)
    by configuring ``base_url`` + ``model``. Implements ``complete(prompt) -> str``,
    the contract expected by LLMQuestionGenerator.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gpt-4o-mini",
        base_url: Optional[str] = None,
        _client=None,
    ):
        self.model = model
        self._api_key = api_key
        self._base_url = base_url
        self._client = _client  # injected for testing; real SDK built lazily otherwise

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI  # optional dependency (the `llm` extra)

            self._client = OpenAI(api_key=self._api_key, base_url=self._base_url)
        return self._client

    def complete(self, prompt: str) -> str:
        resp = self._get_client().chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.choices[0].message.content
