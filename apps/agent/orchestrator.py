import os
import time
from typing import List, Dict, Tuple, Any
import requests

from persona import SYSTEM_PROMPT

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"

class LLMError(RuntimeError):
    pass

def _openai_headers() -> Dict[str, str]:
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        raise LLMError("LLM_API_KEY not set")
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

def call_llm(
    messages: List[Dict[str, str]],
    *,
    model: str = None,
    temperature: float = 0.7,
    max_output_tokens: int = 180,
    timeout: float = 30.0,
    retries: int = 3,
    backoff: float = 0.8,
) -> Tuple[str, Dict[str, Any]]:
    """
    Simple non-streaming chat completion. Returns (text, meta).
    meta includes usage and finish_reason when available.
    """
    provider = os.getenv("MODEL_PROVIDER", "openai").lower()
    model = model or os.getenv("MODEL_NAME", "gpt-4o-mini")

    if provider != "openai":
        raise LLMError(f"Only provider 'openai' implemented in Step 6. Got: {provider}")

    payload = {
        "model": model,
        "messages": messages,
        "temperature": float(temperature),
        "max_tokens": int(max_output_tokens),
    }
    headers = _openai_headers()

    last_exc = None
    for attempt in range(retries + 1):
        try:
            r = requests.post(OPENAI_CHAT_URL, json=payload, headers=headers, timeout=timeout)
            # retry on rate-limit/transient
            if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(backoff * (2 ** attempt))
                continue
            r.raise_for_status()
            data = r.json()
            msg = (data["choices"][0]["message"]["content"] or "").strip()
            meta = {
                "finish_reason": data["choices"][0].get("finish_reason"),
                "usage": data.get("usage", {}),
                "model": data.get("model", model),
            }
            return msg, meta
        except requests.HTTPError as e:
            last_exc = e
            if attempt < retries:
                time.sleep(backoff * (2 ** attempt))
                continue
            # surface server message to help debugging
            body = ""
            try:
                body = r.text[:200]
            except Exception:
                pass
            raise LLMError(f"OpenAI HTTP {r.status_code}: {body}") from e
        except requests.RequestException as e:
            last_exc = e
            if attempt < retries:
                time.sleep(backoff * (2 ** attempt))
                continue
            raise LLMError(f"Network error calling OpenAI: {e}") from e
    raise LLMError(f"LLM call failed after retries: {last_exc}")

class Orchestrator:
    """
    Minimal orchestrator for Step 6:
    - wraps call_llm
    - enforces persona
    - will be extended in Step 7 to route Nutritionix & RAG
    """
    def __init__(self):
        pass

    def answer(self, user_text: str) -> Tuple[str, Dict[str, Any]]:
        msgs = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_text.strip()},
        ]
        # lower temperature for tighter, quicker replies in voice
        return call_llm(msgs, temperature=0.6, max_output_tokens=160)
