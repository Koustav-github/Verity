"""Generate an evaluation test set from a Pinecone index.

Set these in .env (or the environment):

  # LLM (any OpenAI-compatible endpoint)
  VERITY_LLM_API_KEY    - API key for the LLM       (falls back to GROQ_API_KEY / OPENAI_API_KEY)
  VERITY_LLM_BASE_URL   - base URL                  (e.g. https://api.groq.com/openai/v1; omit for OpenAI)
  VERITY_GEN_MODEL      - model id                  (e.g. llama-3.3-70b-versatile)

  # Pinecone
  PINECONE_API_KEY      - your Pinecone key
  PINECONE_HOST         - index host URL            (the "database link")  (or PINECONE_INDEX)
  PINECONE_TEXT_KEY     - metadata field holding chunk text   (e.g. summary; default: text)

  # Optional knobs
  VERITY_N              - number of chunks to sample (default: 5)

Run:  python examples/generate_testset.py
"""

import os

from dotenv import load_dotenv

from verity.interrogation import TestsetGenerator

load_dotenv()
load_dotenv(".env.test")  # Pinecone key kept separately in .env.test

api_key = (
    os.environ.get("VERITY_LLM_API_KEY")
    or os.environ.get("GROQ_API_KEY")
    or os.environ["OPENAI_API_KEY"]
)

gen = TestsetGenerator.from_pinecone(
    openai_api_key=api_key,
    base_url=os.environ.get("VERITY_LLM_BASE_URL"),
    model=os.environ.get("VERITY_GEN_MODEL", "gpt-4o-mini"),
    pinecone_api_key=os.environ["PINECONE_API_KEY"],
    index_name=os.environ.get("PINECONE_INDEX"),
    host=os.environ.get("PINECONE_HOST"),
    text_key=os.environ.get("PINECONE_TEXT_KEY", "text"),
)

testset = gen.generate(n=int(os.environ.get("VERITY_N", "5")), seed=42, fetch_limit=50)
testset.to_jsonl("eval_set.jsonl")

print(f"Generated {len(testset)} questions -> eval_set.jsonl\n")
for c in testset.cases:
    print(f"Q: {c.question}")
    print(f"A: {c.reference_answer}\n")
