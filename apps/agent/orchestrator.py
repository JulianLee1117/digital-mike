import os
import re
import time
from typing import List, Dict, Tuple, Any, Optional
import requests

from persona import SYSTEM_PROMPT
from rag.store import RAGStore
from tools.nutritionix import lookup_macros, summarize_for_speech, NutritionixError

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

# ---- Intent hints ----
_NUTRITION_HINTS = {
    "macro", "macros", "calorie", "calories", "protein", "carb", "carbs", "fat", "fats",
    "nutrition", "kcal", "grams", "serving", "meal", "bowl",
}
_THEORY_HINTS = {
    "mrv", "mev", "mv", "mav", "sra", "periodization", "fatigue", "overload", "specificity", "variation",
    "meso", "mesocycle", "microcycle", "macrocycle", "volume landmarks", "autoregul", "rpe",
    "technique", "bench", "squat", "deadlift", "chapter", "page",
}

class Orchestrator:
    """
    Step 7 orchestrator:
    - intent routing: Nutritionix vs RAG vs direct LLM
    - RAG: inject 'RAG CONTEXT' as assistant message; ensure (p.X) citations
    - replies concise for TTS
    """
    def __init__(self, rag_store: Optional[RAGStore] = None):
        self.rag_store = rag_store  # lazy init

    # ---- Intent detection ----
    def _detect_intent(self, text: str) -> str:
        tl = (text or "").lower()
        if any(k in tl for k in _NUTRITION_HINTS):
            return "nutrition"
        if re.search(r"\b\d+\s*(g|grams|kg|kcal|calories?)\b", tl):
            return "nutrition"
        if any(k in tl for k in _THEORY_HINTS) or re.search(r"\b(MRV|MEV|MV)\b", text):
            return "theory"
        return "general"

    def _ensure_store(self) -> RAGStore:
        if self.rag_store is None:
            self.rag_store = RAGStore()
        return self.rag_store

    # ---- RAG helpers ----
    def _build_rag_context(
        self, query: str, *, k: int = 4
    ) -> Tuple[str, List[int]]:
        store = self._ensure_store()
        chunks = store.search(query, k=k)
        if not chunks:
            return "", []
        lines, pages = [], []
        for c in chunks:
            pg = int(c.get("page", -1))
            txt = (c.get("text") or "").strip()
            if not txt:
                continue
            snippet = txt if len(txt) <= 400 else (txt[:380].rstrip() + " …")
            lines.append(f"- (p.{pg}) {snippet}")
            pages.append(pg)
        pages = sorted({p for p in pages if p >= 0})
        ctx = "RAG CONTEXT:\n" + "\n".join(lines)
        return ctx, pages

    def _append_citations_if_missing(self, reply: str, pages: List[int]) -> str:
        if not pages:
            return reply
        if re.search(r"\(p\.\s*\d+", reply):
            return reply
        cites = ", ".join(str(p) for p in pages[:6])
        suffix = f" (p.{cites})"
        return reply + ("" if reply.endswith(('.', '!', '?')) else ".") + suffix

    # ---- Main entrypoint ----
    def answer(self, user_text: str) -> Tuple[str, Dict[str, Any]]:
        route = self._detect_intent(user_text)

        if route == "nutrition":
            try:
                items = lookup_macros(user_text)
                reply = summarize_for_speech(items)
                print("[orchestrator] route=nutrition items", len(items))
                return reply, {"route": "nutrition", "items_count": len(items), "nutrition_items": items}
            except NutritionixError as e:
                print("[orchestrator] nutrition error, fallback to LLM:", str(e))
                msgs = [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "system", "content": "Keep replies to 1–3 short sentences for voice."},
                    {"role": "user", "content": f"User asked for macros but the tool failed. Be concise and helpful: {user_text}"},
                ]
                reply, meta = call_llm(msgs, temperature=0.5, max_output_tokens=120)
                meta["route"] = "nutrition_fallback"
                return reply, meta

        if route == "theory":
            rag_ctx, pages = self._build_rag_context(user_text, k=4)
            print("[orchestrator] route=rag pages", pages)
            policy = (
                "When context is supplied below, answer concisely (1–3 sentences) "
                "and include page citations like (p.X). Do not invent citations."
            )
            msgs = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "system", "content": policy},
            ]
            if rag_ctx:
                msgs.append({"role": "assistant", "content": rag_ctx})
            msgs.append({"role": "user", "content": user_text.strip()})
            reply, meta = call_llm(msgs, temperature=0.5, max_output_tokens=160)
            reply = self._append_citations_if_missing(reply, pages)
            meta["route"] = "rag"
            meta["pages"] = pages
            return reply, meta

        # default general chat
        msgs = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "system", "content": "Keep replies to 1–3 short sentences for voice."},
            {"role": "user", "content": user_text.strip()},
        ]
        reply, meta = call_llm(msgs, temperature=0.6, max_output_tokens=160)
        meta["route"] = "llm"
        print("[orchestrator] route=llm")
        return reply, meta
