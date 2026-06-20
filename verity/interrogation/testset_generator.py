from typing import Optional

from .connector import VectorStoreConnector
from .generator import QuestionGenerator
from .pipeline import InterrogationPipeline
from .testset import TestSet


class TestsetGenerator:
    """Top-level entry: pull chunks from a vector store and generate a TestSet."""

    __test__ = False  # not a pytest test class

    def __init__(self, connector: VectorStoreConnector, generator: QuestionGenerator):
        self.connector = connector
        self.pipeline = InterrogationPipeline(generator)

    def generate(
        self,
        n: Optional[int] = None,
        seed: Optional[int] = None,
        fetch_limit: Optional[int] = None,
    ) -> TestSet:
        chunks = self.connector.fetch_chunks(limit=fetch_limit)
        return self.pipeline.run(chunks, n=n, seed=seed)

    @classmethod
    def from_pinecone(
        cls,
        *,
        openai_api_key: str,
        pinecone_api_key: str,
        index_name: Optional[str] = None,
        host: Optional[str] = None,
        model: str = "gpt-4o-mini",
        base_url: Optional[str] = None,
        namespace: str = "",
        text_key: str = "text",
    ) -> "TestsetGenerator":
        """Build a generator wired to a Pinecone index + an OpenAI-compatible LLM."""
        from .clients import OpenAICompatibleClient
        from .llm_generator import LLMQuestionGenerator
        from .pinecone_connector import PineconeConnector

        client = OpenAICompatibleClient(
            api_key=openai_api_key, model=model, base_url=base_url
        )
        generator = LLMQuestionGenerator(client)
        connector = PineconeConnector(
            api_key=pinecone_api_key,
            index_name=index_name,
            host=host,
            namespace=namespace,
            text_key=text_key,
        )
        return cls(connector, generator)
