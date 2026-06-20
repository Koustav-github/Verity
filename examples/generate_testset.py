"""Generate an evaluation test set from a Pinecone index.

Requires (in .env or environment):
  OPENAI_API_KEY      - your OpenAI (or compatible) key
  PINECONE_API_KEY    - your Pinecone key
  PINECONE_INDEX      - index name           (or use PINECONE_HOST)
  PINECONE_HOST       - index host URL       (the "database link")
Optional:
  VERITY_GEN_MODEL    - generation model     (default: gpt-4o-mini)
  PINECONE_TEXT_KEY   - metadata field holding chunk text (default: text)
"""

import os

from dotenv import load_dotenv

from verity.interrogation import TestsetGenerator

load_dotenv()

gen = TestsetGenerator.from_pinecone(
    openai_api_key=os.environ["OPENAI_API_KEY"],
    pinecone_api_key=os.environ["PINECONE_API_KEY"],
    index_name=os.environ.get("PINECONE_INDEX"),
    host=os.environ.get("PINECONE_HOST"),
    model=os.environ.get("VERITY_GEN_MODEL", "gpt-4o-mini"),
    text_key=os.environ.get("PINECONE_TEXT_KEY", "text"),
)

testset = gen.generate(n=20, seed=42, fetch_limit=200)
testset.to_jsonl("eval_set.jsonl")
print(f"Generated {len(testset)} questions -> eval_set.jsonl")
