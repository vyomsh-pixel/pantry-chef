import json
import re
import time
import requests

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"

_DEFAULT_REQUESTS_GET = requests.get
_DEFAULT_REQUESTS_POST = requests.post
_session = requests.Session()
session = _session

_free_models_cache = {"models": [], "fetched_at": 0}
FREE_MODELS_TTL_SECONDS = 3600  # refresh the free-model list once an hour

_response_cache = {}  # key -> (expires_at, result_dict)
RESPONSE_CACHE_TTL_SECONDS = 600
MAX_CACHE_ENTRIES = 256


class GenerationError(Exception):
    """Raised when every model attempt fails, or the response can't be parsed."""


def _http_get(*args, **kwargs):
    if requests.get is not _DEFAULT_REQUESTS_GET:
        return requests.get(*args, **kwargs)
    return _session.get(*args, **kwargs)


def _http_post(*args, **kwargs):
    if requests.post is not _DEFAULT_REQUESTS_POST:
        return requests.post(*args, **kwargs)
    return _session.post(*args, **kwargs)


def get_free_models() -> list[str]:
    """Return currently-free OpenRouter model ids, newest-cached-first.

    Falls back to just ["openrouter/free"] (OpenRouter's own auto-router)
    if the models endpoint can't be reached — that alone still works.
    """
    now = time.time()
    if _free_models_cache["models"] and now - _free_models_cache["fetched_at"] < FREE_MODELS_TTL_SECONDS:
        return _free_models_cache["models"]

    try:
        resp = _http_get(OPENROUTER_MODELS_URL, timeout=(5, 10))
        resp.raise_for_status()
        data = resp.json().get("data", [])
        free_ids = [
            m["id"] for m in data
            if m.get("pricing", {}).get("prompt") == "0"
            and m.get("pricing", {}).get("completion") == "0"
        ]
        # Put the general-purpose auto-router first since it's the most
        # reliable single entry point, then a handful of specific free
        # models as genuine fallbacks behind it.
        ordered = ["openrouter/free"] + [m for m in free_ids if m != "openrouter/free"]
        _free_models_cache["models"] = ordered[:6]
        _free_models_cache["fetched_at"] = now
    except requests.exceptions.RequestException:
        if not _free_models_cache["models"]:
            _free_models_cache["models"] = ["openrouter/free"]

    return _free_models_cache["models"]


def build_prompt(ingredients: str, diet: str, cuisine: str, servings: int) -> str:
    return f"""You are a practical home-cooking assistant. A user has these
ingredients available: {ingredients}.

Dietary preference: {diet}
Preferred cuisine: {cuisine}
Servings needed: {servings}

Suggest exactly 3 different recipes that primarily use the ingredients
listed above (a few common pantry staples like salt, oil, or water can be
assumed available). If the ingredients are insufficient for a full meal,
work in one small reasonable addition rather than inventing an unrelated
dish.

Respond with ONLY raw JSON — no markdown code fences, no commentary
before or after — matching exactly this schema:

{{
  "recipes": [
    {{
      "name": "string",
      "cook_time": "string, e.g. '20 minutes'",
      "ingredients": [
        {{"item": "string", "amount": "string, e.g. '2 cups'"}}
      ],
      "steps": ["string", "string"],
      "nutrition": {{"calories": "string", "protein": "string"}}
    }}
  ]
}}

Return exactly 3 objects in the recipes array."""


def _normalize_ingredients(raw_ingredients) -> list[dict]:
    if not isinstance(raw_ingredients, list):
        return []
    normalized = []
    for ing in raw_ingredients:
        if isinstance(ing, dict):
            item = str(ing.get("item", "")).strip()
            amount = str(ing.get("amount", "")).strip()
            if item or amount:
                normalized.append({"item": item, "amount": amount})
        elif isinstance(ing, str) and ing.strip():
            normalized.append({"item": ing.strip(), "amount": ""})
    return normalized


def _normalize_nutrition(raw_nutrition) -> dict:
    if not isinstance(raw_nutrition, dict):
        return {}
    normalized = {}
    for k, v in raw_nutrition.items():
        if v is not None:
            normalized[str(k)] = str(v).strip()
    if "calories" in normalized:
        normalized["calories"] = re.sub(
            r"\s*(?:kcal|calories|cal)\s*$",
            "",
            normalized["calories"],
            flags=re.IGNORECASE,
        ).strip()
    return normalized


def parse_recipes(raw_text: str) -> list[dict]:
    """Extract and validate the recipes array from a model response.

    Models occasionally wrap JSON in ```json fences or add a stray
    sentence despite instructions, so this tries a couple of extraction
    strategies before giving up.
    """
    candidates = [raw_text.strip()]

    fenced = re.search(
        r"```(?:json)?\s*(.*?)```", raw_text, re.DOTALL | re.IGNORECASE
    )
    if fenced:
        candidates.insert(0, fenced.group(1).strip())

    brace_match = re.search(r"\{.*\}", raw_text, re.DOTALL)
    if brace_match:
        candidates.append(brace_match.group(0))

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue

        if not isinstance(parsed, dict):
            continue

        recipes = parsed.get("recipes")
        if not isinstance(recipes, list) or not recipes:
            continue

        cleaned = []
        for r in recipes:
            if not isinstance(r, dict) or "name" not in r:
                continue
            raw_steps = r.get("steps", [])
            steps = (
                [str(s).strip() for s in raw_steps if str(s).strip()]
                if isinstance(raw_steps, list)
                else []
            )
            cleaned.append({
                "name": str(r.get("name", "Untitled recipe")).strip() or "Untitled recipe",
                "cook_time": str(r.get("cook_time", "")).strip(),
                "ingredients": _normalize_ingredients(r.get("ingredients", [])),
                "steps": steps,
                "nutrition": _normalize_nutrition(r.get("nutrition", {})),
            })
        if cleaned:
            return cleaned

    raise GenerationError("Could not parse a valid recipe list from the model's response.")


def _cache_key(ingredients, diet, cuisine, servings, model_hint):
    canonical_ingredients = ",".join(
        sorted({i.strip().lower() for i in (ingredients or "").split(",") if i.strip()})
    )
    return "|".join([
        canonical_ingredients,
        (diet or "").strip().lower(),
        (cuisine or "").strip().lower(),
        str(servings),
        (model_hint or "").strip(),
    ])


def _store_cache(key: str, result: dict) -> None:
    now = time.time()
    if len(_response_cache) >= MAX_CACHE_ENTRIES and key not in _response_cache:
        expired = [k for k, (exp, _) in _response_cache.items() if exp <= now]
        for k in expired:
            _response_cache.pop(k, None)
        if len(_response_cache) >= MAX_CACHE_ENTRIES:
            oldest_key = min(_response_cache, key=lambda k: _response_cache[k][0])
            _response_cache.pop(oldest_key, None)
    _response_cache[key] = (now + RESPONSE_CACHE_TTL_SECONDS, result)


def generate_recipes(api_key: str, ingredients: str, diet: str, cuisine: str,
                      servings: int, preferred_model: str | None = None) -> dict:
    """Try models in order until one returns parseable recipes.

    Returns {"recipes": [...], "model_used": "..."}. Raises GenerationError
    if every attempt fails (network errors, rate limits, and unparseable
    responses are all retried against the next model in the list).
    """
    key = _cache_key(ingredients, diet, cuisine, servings, preferred_model)
    cached = _response_cache.get(key)
    if cached and cached[0] > time.time():
        return cached[1]

    models_to_try = []
    if preferred_model:
        models_to_try.append(preferred_model)
    for m in get_free_models():
        if m not in models_to_try:
            models_to_try.append(m)

    prompt = build_prompt(ingredients, diet, cuisine, servings)
    last_error = None

    for model in models_to_try[:4]:  # cap attempts so a bad run fails fast
        try:
            resp = _http_post(
                OPENROUTER_CHAT_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "http://localhost:5000",
                    "X-Title": "PantryChef",
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "response_format": {"type": "json_object"},
                },
                timeout=(5, 25),
            )
        except requests.exceptions.RequestException as e:
            last_error = f"Could not reach OpenRouter ({model}): {e}"
            continue

        if resp.status_code == 429:
            last_error = f"Rate limited on {model}, trying next model…"
            continue
        if resp.status_code != 200:
            try:
                detail = resp.json().get("error", {}).get("message", resp.text)
            except Exception:
                detail = resp.text
            last_error = f"{model} returned {resp.status_code}: {detail}"
            continue

        try:
            text = resp.json()["choices"][0]["message"]["content"]
            recipes = parse_recipes(text)
        except (KeyError, IndexError, TypeError, ValueError, GenerationError) as e:
            last_error = f"{model} returned an unparseable response: {e}"
            continue

        result = {"recipes": recipes, "model_used": model}
        _store_cache(key, result)
        return result

    raise GenerationError(last_error or "All model attempts failed.")

