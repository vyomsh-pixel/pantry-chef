import json
import re
import time
import requests

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"

_free_models_cache = {"models": [], "fetched_at": 0}
FREE_MODELS_TTL_SECONDS = 3600  # refresh the free-model list once an hour

_response_cache = {}  # key -> (expires_at, result_dict)
RESPONSE_CACHE_TTL_SECONDS = 600


class GenerationError(Exception):
    """Raised when every model attempt fails, or the response can't be parsed."""


def get_free_models() -> list[str]:
    """Return currently-free OpenRouter model ids, newest-cached-first.

    Falls back to just ["openrouter/free"] (OpenRouter's own auto-router)
    if the models endpoint can't be reached — that alone still works.
    """
    now = time.time()
    if _free_models_cache["models"] and now - _free_models_cache["fetched_at"] < FREE_MODELS_TTL_SECONDS:
        return _free_models_cache["models"]

    try:
        resp = requests.get(OPENROUTER_MODELS_URL, timeout=10)
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


def parse_recipes(raw_text: str) -> list[dict]:
    """Extract and validate the recipes array from a model response.

    Models occasionally wrap JSON in ```json fences or add a stray
    sentence despite instructions, so this tries a couple of extraction
    strategies before giving up.
    """
    candidates = [raw_text.strip()]

    fenced = re.search(r"```(?:json)?\s*(.*?)```", raw_text, re.DOTALL)
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

        recipes = parsed.get("recipes")
        if not isinstance(recipes, list) or not recipes:
            continue

        cleaned = []
        for r in recipes:
            if not isinstance(r, dict) or "name" not in r:
                continue
            cleaned.append({
                "name": str(r.get("name", "Untitled recipe")),
                "cook_time": str(r.get("cook_time", "")),
                "ingredients": r.get("ingredients", []) if isinstance(r.get("ingredients"), list) else [],
                "steps": r.get("steps", []) if isinstance(r.get("steps"), list) else [],
                "nutrition": r.get("nutrition", {}) if isinstance(r.get("nutrition"), dict) else {},
            })
        if cleaned:
            return cleaned

    raise GenerationError("Could not parse a valid recipe list from the model's response.")


def _cache_key(ingredients, diet, cuisine, servings, model_hint):
    return "|".join([ingredients.strip().lower(), diet, cuisine, str(servings), model_hint or ""])


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
            resp = requests.post(
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
                timeout=60,
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
        except (KeyError, IndexError, GenerationError) as e:
            last_error = f"{model} returned an unparseable response: {e}"
            continue

        result = {"recipes": recipes, "model_used": model}
        _response_cache[key] = (time.time() + RESPONSE_CACHE_TTL_SECONDS, result)
        return result

    raise GenerationError(last_error or "All model attempts failed.")
