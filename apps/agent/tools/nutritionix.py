import os
import time
from typing import List, Dict, Any, Optional
import requests
import re

NUTRITIONIX_URL = "https://trackapi.nutritionix.com/v2/natural/nutrients"

class NutritionixError(RuntimeError):
    pass

def _get_env() -> Dict[str, str]:
    app_id = os.getenv("NUTRITIONIX_APP_ID")
    api_key = os.getenv("NUTRITIONIX_API_KEY")
    if not app_id or not api_key:
        raise NutritionixError("Missing NUTRITIONIX_APP_ID or NUTRITIONIX_API_KEY")
    # Optional but helpful metadata
    remote_user_id = os.getenv("NUTRITIONIX_REMOTE_USER_ID", "0")
    headers = {
        "x-app-id": app_id,
        "x-app-key": api_key,
        "x-remote-user-id": remote_user_id,
        "x-remote-user-app": "digital-mike",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    return headers

def _fmt_serving(item: Dict[str, Any]) -> str:
    qty = item.get("serving_qty")
    unit = item.get("serving_unit")
    grams = item.get("serving_weight_grams")
    parts = []
    if qty is not None and unit:
        parts.append(f"{qty} {unit}".strip())
    if grams:
        try:
            g = int(round(float(grams)))
            parts.append(f"({g} g)")
        except Exception:
            pass
    return " ".join(parts) if parts else "1 serving"

def _normalize(item: Dict[str, Any]) -> Dict[str, Any]:
    def f(key: str) -> float:
        try:
            return float(item.get(key, 0.0) or 0.0)
        except Exception:
            return 0.0
    return {
        "food_name": (item.get("food_name") or "").strip(),
        "serving": _fmt_serving(item),
        "calories": round(f("nf_calories"), 1),
        "protein": round(f("nf_protein"), 1),
        "carbs": round(f("nf_total_carbohydrate"), 1),
        "fat": round(f("nf_total_fat"), 1),
    }

def _post_with_retry(
    payload: Dict[str, Any],
    headers: Dict[str, str],
    timeout: float = 10.0,
    retries: int = 3,
    backoff: float = 0.8,
) -> requests.Response:
    last_exc = None
    for attempt in range(retries + 1):
        try:
            resp = requests.post(NUTRITIONIX_URL, json=payload, headers=headers, timeout=timeout)
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(backoff * (2 ** attempt))
                continue
            resp.raise_for_status()
            return resp
        except requests.HTTPError as e:
            last_exc = e
            if attempt < retries:
                time.sleep(backoff * (2 ** attempt))
                continue
            # surface concise server body if any
            body = ""
            try:
                body = resp.text[:200]
            except Exception:
                pass
            raise NutritionixError(f"Nutritionix HTTP {resp.status_code}: {body}") from e
        except requests.RequestException as e:
            last_exc = e
            if attempt < retries:
                time.sleep(backoff * (2 ** attempt))
                continue
            raise NutritionixError(f"Network error contacting Nutritionix: {e}") from e
    raise NutritionixError(f"Nutritionix request failed after retries: {last_exc}")

def lookup_macros(query: str, *, timeout: float = 10.0) -> List[Dict[str, Any]]:
    """
    Returns a list of dicts with keys: {food_name, serving, calories, protein, carbs, fat}
    """
    q = (query or "").strip()
    if not q:
        raise ValueError("lookup_macros: 'query' must be non-empty")
    headers = _get_env()

    def _try(payload_query: str) -> List[Dict[str, Any]]:
        payload = {
            "query": payload_query,
            "timezone": os.getenv("NUTRITIONIX_TZ", "US/Eastern"),
            "locale": os.getenv("NUTRITIONIX_LOCALE", "en_US"),
        }
        resp = _post_with_retry(payload, headers, timeout=timeout)
        data = resp.json() or {}
        foods = data.get("foods", []) or []
        out = []
        for item in foods:
            norm = _normalize(item)
            if norm["food_name"] or norm["calories"] > 0:
                out.append(norm)
        return out

    # Attempt 1: raw query
    try:
        items = _try(q)
        if items:
            return items
    except NutritionixError as e:
        err = str(e).lower()
        if "issue parsing your query" not in err:
            raise

    # Attempt 2: make quantity explicit
    try:
        items = _try(f"1 serving of {q}")
        if items:
            return items
    except NutritionixError:
        pass

    # Attempt 3: line-delimit components (e.g., "a with b and c" => separate lines)
    parts = [p.strip() for p in re.split(r"\b(?:with|and|\+|,)\b", q) if p.strip()]
    if parts:
        line_query = "\n".join(f"1 serving {p}" for p in parts)
        items = _try(line_query)
        if items:
            return items

    # Final fallback: raise last meaningful error
    # (Try one last time to surface a clear error for debugging)
    return _try(q)

def summarize_for_speech(items: List[Dict[str, Any]]) -> str:
    if not items:
        return "I couldn't find macros for that."
    total = {
        "cal": sum(x["calories"] for x in items),
        "p": sum(x["protein"] for x in items),
        "c": sum(x["carbs"] for x in items),
        "f": sum(x["fat"] for x in items),
    }
    parts = [f"{i['food_name']} — {i['calories']} kcal, P {i['protein']} g, C {i['carbs']} g, F {i['fat']} g"
             for i in items[:3]]
    s = "; ".join(parts)
    if len(items) > 1:
        s += f". Total: {round(total['cal'])} kcal — P {round(total['p'])} g, C {round(total['c'])} g, F {round(total['f'])} g."
    return s
