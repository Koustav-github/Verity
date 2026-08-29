"""LLM provider defaults, shared by every agent.

Groq is OpenAI-compatible, so switching providers is these two values plus the key in
VERITY_LLM_API_KEY — no client code changes anywhere. Credentials are shared across
agents; only the model id is per-agent (HAWKEYE_LLM_MODEL / NAT_LLM_MODEL), so one
agent can be moved to a stronger model without touching the others.
"""

DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
# Was llama-3.3-70b-versatile until Groq retired the Llama line (404 model_not_found).
# Both agents need reliable JSON-mode output, which this handles well. If it disappears
# too, `GET /openai/v1/models` lists what the key can actually reach.
DEFAULT_MODEL = "openai/gpt-oss-120b"

# The openai SDK's own default is 10 minutes with internal retries on top. Every agent
# call happens synchronously inside an async route with no thread-pool offload, so a
# slow provider response blocks the entire single-threaded server, not just this
# request. A tight, explicit timeout turns "server wedged for minutes" into a fast,
# visible error instead.
DEFAULT_LLM_TIMEOUT_S = 30.0
