import json
import os

from pydantic import BaseModel


class Manifest(BaseModel):
    framework: str
    detected_via: str | None = None
    model_class: str | None = None
    hyperparameters: dict | None = None


_SYSTEM_PROMPT = """You are Hawkeye, a model-identification agent. Given a Python repr() of a
machine learning model object, respond with a JSON object with these fields:
- framework: the ML framework/library the model belongs to (e.g. "sklearn", "pytorch", "xgboost"), or "unknown" if you cannot tell
- detected_via: the specific signal you used to identify it (e.g. class name, module path), or null if unknown
- model_class: the model's class name
- hyperparameters: an object of hyperparameter name -> value pairs visible in the repr, or null if none are visible

Only report what you can actually see in the input. Do not guess or invent values."""


def identify(model, client=None) -> dict:
    client = client or _real_client()
    response = client.chat.completions.create(
        model=os.environ.get("HAWKEYE_LLM_MODEL", "glm-5.2"),
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": repr(model)},
        ],
        response_format={"type": "json_object"},
    )
    raw = json.loads(response.choices[0].message.content)
    return Manifest.model_validate(raw).model_dump()


def _real_client():
    from openai import OpenAI

    return OpenAI(
        api_key=os.environ["HAWKEYE_LLM_API_KEY"],
        base_url=os.environ.get("HAWKEYE_LLM_BASE_URL", "https://api.z.ai/api/paas/v4/"),
    )
