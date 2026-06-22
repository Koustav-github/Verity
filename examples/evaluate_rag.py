"""Test a RAG pipeline end-to-end with Verity.

  Phase 1  generate questions from the RAG's vector DB   (interrogation)
  Phase 2  run the RAG: retrieve top-k + generate answer (the system under test)
  Phase 3  score with Verity's offline triad             (answer + context relevance)

Keys are read from TestRAGPipeline/DummyRAG/.env (and the repo .env). Config via env:

  RAG_INDEX_HOST   Pinecone index host of the RAG    (default: the Avengers demo index)
  RAG_TEXT_KEY     metadata field holding chunk text (default: text)
  GEN_MODEL        generation/answer model           (default: llama-3.3-70b-versatile)
  N_QUESTIONS      how many questions to generate     (default: 5)
  TOP_K            retrieval depth                    (default: 4)

Run:  python examples/evaluate_rag.py
"""

import os

from dotenv import load_dotenv

load_dotenv("TestRAGPipeline/DummyRAG/.env")
load_dotenv()  # repo .env, if present

from openai import OpenAI
from pinecone import Pinecone
from sentence_transformers import SentenceTransformer

from verity.detectors.embeddings import EmbeddingDetector
from verity.detectors.nli import NLIDetector
from verity.interrogation import TestsetGenerator
from verity.metrics.answerrelevance import AnswerRelevance
from verity.metrics.contextrelevance import ContextRelevance
from verity.metrics.faithfulness import Faithfulness
from verity.runner import EvalRunner

HOST = os.environ.get(
    "RAG_INDEX_HOST",
    "https://avengers-rag-j0djz9z.svc.aped-4627-b74a.pinecone.io",
)
TEXT_KEY = os.environ.get("RAG_TEXT_KEY", "text")
MODEL = os.environ.get("GEN_MODEL", "llama-3.3-70b-versatile")
N = int(os.environ.get("N_QUESTIONS", "5"))
TOP_K = int(os.environ.get("TOP_K", "4"))
GROQ_KEY = os.environ["GROQ_API_KEY"]
GROQ_BASE = "https://api.groq.com/openai/v1"
PINECONE_KEY = os.environ["PINECONE_API_KEY"]

# --- Phase 1: generate questions from the DB ---
print(f"[1/3] Generating {N} questions from the index...")
gen = TestsetGenerator.from_pinecone(
    openai_api_key=GROQ_KEY, base_url=GROQ_BASE, model=MODEL,
    pinecone_api_key=PINECONE_KEY, host=HOST, text_key=TEXT_KEY,
)
testset = gen.generate(n=N, seed=42, fetch_limit=50)

# --- Phase 2: run the RAG (retrieve + generate) ---
print(f"[2/3] Running the RAG over {len(testset)} questions (top_k={TOP_K})...")
embed = SentenceTransformer("all-MiniLM-L6-v2")
index = Pinecone(api_key=PINECONE_KEY).Index(host=HOST)
llm = OpenAI(api_key=GROQ_KEY, base_url=GROQ_BASE)


def run_rag(question: str):
    res = index.query(
        vector=embed.encode(question).tolist(), top_k=TOP_K, include_metadata=True
    )
    chunks = [m["metadata"][TEXT_KEY] for m in res["matches"]]
    context = "\n\n".join(chunks)
    answer = llm.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content":
            f"Answer using ONLY the context.\n\nContext:\n{context}\n\nQuestion: {question}"}],
    ).choices[0].message.content
    return chunks, answer


items = []
for case in testset:
    chunks, answer = run_rag(case.question)
    items.append({"question": case.question, "context": chunks, "answer": answer})

# --- Phase 3: score with Verity (offline, no LLM judge) ---
print("[3/3] Scoring with Verity (offline triad)...\n")
runner = EvalRunner(
    AnswerRelevance(EmbeddingDetector()),
    ContextRelevance(EmbeddingDetector()),
    Faithfulness(NLIDetector()),
)
report = runner.evaluate_batch(items)

for it in report.items:
    lo = min(it.answer_relevance, it.context_relevance, it.faithfulness)
    flag = "  <-- low" if lo < 0.4 else ""
    print(f"AR={it.answer_relevance:.3f}  CR={it.context_relevance:.3f}  "
          f"FA={it.faithfulness:.3f}{flag}")
    print(f"   Q: {it.question}")
    print(f"   A: {it.answer[:100]}...\n")

print(f"MEAN  answer_relevance={report.mean_answer_relevance:.3f}  "
      f"context_relevance={report.mean_context_relevance:.3f}  "
      f"faithfulness={report.mean_faithfulness:.3f}")
