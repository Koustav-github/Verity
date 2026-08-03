"""LLM provider defaults, shared by every agent.

Groq is OpenAI-compatible, so switching providers is these two values plus the key in
VERITY_LLM_API_KEY — no client code changes anywhere. Credentials are shared across
agents; only the model id is per-agent (HAWKEYE_LLM_MODEL / NAT_LLM_MODEL), so one
agent can be moved to a stronger model without touching the others.
"""

DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "llama-3.3-70b-versatile"
