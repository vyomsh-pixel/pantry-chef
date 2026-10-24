import os
import secrets
import sqlite3
import time
from flask import Flask, jsonify, render_template, request, session

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOTENV_PATH = os.path.join(BASE_DIR, ".env")


def normalize_env_value(value):
    if value is None:
        return None
    if not isinstance(value, str):
        return str(value)

    value = value.strip()
    while True:
        changed = False
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1].strip()
            changed = True
        if len(value) >= 2 and value[0] == "(" and value[-1] == ")":
            value = value[1:-1].strip()
            changed = True
        if not changed:
            break

    return value or None


def load_environment_file(env_path: str | None = None):
    env_path = env_path or DOTENV_PATH
    if not env_path or not os.path.exists(env_path):
        return

    with open(env_path, encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if key:
                normalized = normalize_env_value(value)
                if normalized is not None:
                    os.environ[key] = normalized
                else:
                    os.environ.pop(key, None)


load_environment_file(DOTENV_PATH)

import ai_client
import db

db.DB_PATH = os.environ.get("PANTRYCHEF_DB", db.DB_PATH)


def _get_or_create_secret_key() -> str:
    env_key = normalize_env_value(os.environ.get("SECRET_KEY"))
    if env_key:
        return env_key
    try:
        key_dir = os.path.dirname(os.path.abspath(db.DB_PATH))
        os.makedirs(key_dir, exist_ok=True)
        key_path = os.path.join(key_dir, ".session_secret")
        if os.path.exists(key_path):
            with open(key_path, "r", encoding="utf-8") as f:
                saved = f.read().strip()
                if saved:
                    return saved
        new_key = secrets.token_hex(32)
        with open(key_path, "w", encoding="utf-8") as f:
            f.write(new_key)
        return new_key
    except Exception:
        return secrets.token_hex(32)


app = Flask(__name__)
app.secret_key = _get_or_create_secret_key()
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

API_KEY = normalize_env_value(os.environ.get("OPENROUTER_API_KEY"))
PREFERRED_MODEL = normalize_env_value(os.environ.get("PREFERRED_MODEL"))  # optional, empty = auto

if API_KEY:
    os.environ["OPENROUTER_API_KEY"] = API_KEY
if PREFERRED_MODEL:
    os.environ["PREFERRED_MODEL"] = PREFERRED_MODEL

db.init_db()

if not API_KEY:
    print(
        "\n[WARNING] OPENROUTER_API_KEY is not set. Copy .env.example to .env and "
        "add your key, or /generate will only return errors.\n"
    )

# --- very basic per-process, per-IP rate limiting -----------------------
# Since one shared server-side key serves every visitor, this stops a
# runaway tab or accidental double-click loop from burning the whole
# daily quota. Resets on restart; not meant as abuse protection for a
# publicly-hosted deployment.
_last_request_at = {}
MIN_SECONDS_BETWEEN_REQUESTS = 3
MAX_RATE_LIMIT_ENTRIES = 256


def _record_rate_limit(ip: str, now: float) -> None:
    if len(_last_request_at) >= MAX_RATE_LIMIT_ENTRIES and ip not in _last_request_at:
        cutoff = now - MIN_SECONDS_BETWEEN_REQUESTS
        expired = [k for k, ts in _last_request_at.items() if ts < cutoff]
        for k in expired:
            _last_request_at.pop(k, None)
        if len(_last_request_at) >= MAX_RATE_LIMIT_ENTRIES:
            oldest_ip = min(_last_request_at, key=_last_request_at.get)
            _last_request_at.pop(oldest_ip, None)
    _last_request_at[ip] = now


def _sanitize_recipe(recipe: dict) -> dict | None:
    if not isinstance(recipe, dict):
        return None
    raw_name = recipe.get("name")
    if not isinstance(raw_name, str) or not raw_name.strip():
        return None

    name = raw_name.strip()[:200]
    cook_time = str(recipe.get("cook_time") or "").strip()[:100]

    raw_ings = recipe.get("ingredients", [])
    ingredients = []
    if isinstance(raw_ings, list):
        for ing in raw_ings[:50]:
            if isinstance(ing, dict):
                item = str(ing.get("item") or "").strip()[:200]
                amount = str(ing.get("amount") or "").strip()[:100]
                if item or amount:
                    ingredients.append({"item": item, "amount": amount})
            elif isinstance(ing, str) and ing.strip():
                ingredients.append({"item": ing.strip()[:200], "amount": ""})

    raw_steps = recipe.get("steps", [])
    steps = []
    if isinstance(raw_steps, list):
        for step in raw_steps[:50]:
            if isinstance(step, (str, int, float)) and str(step).strip():
                steps.append(str(step).strip()[:500])

    raw_nut = recipe.get("nutrition", {})
    nutrition = {}
    if isinstance(raw_nut, dict):
        for k, v in list(raw_nut.items())[:20]:
            if v is not None:
                nutrition[str(k).strip()[:50]] = str(v).strip()[:100]

    return {
        "name": name,
        "cook_time": cook_time,
        "ingredients": ingredients,
        "steps": steps,
        "nutrition": nutrition,
    }


@app.route("/")
def index():
    return render_template(
        "index.html",
        active="generator",
        default_model=PREFERRED_MODEL or "auto (best available free model)",
    )


@app.route("/favorites")
def favorites_page():
    user_id = session.get("user_id")
    return render_template(
        "favorites.html",
        active="favorites",
        favorites=db.list_favorites(user_id=user_id),
    )


@app.route("/generate", methods=["POST"])
def generate():
    if not API_KEY:
        return jsonify({
            "error": "The app owner hasn't configured an OpenRouter API key "
                     "yet (OPENROUTER_API_KEY is missing on the server)."
        }), 500

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid JSON payload."}), 400

    raw_ingredients = data.get("ingredients")
    ingredients = (raw_ingredients if isinstance(raw_ingredients, str) else "").strip()
    diet = str(data.get("diet") or "No restriction").strip()[:50] or "No restriction"
    cuisine = str(data.get("cuisine") or "Any").strip()[:50] or "Any"
    servings_raw = data.get("servings", 2)
    raw_model = str(data.get("model") or "").strip()[:100]
    model = raw_model or PREFERRED_MODEL

    # Validate the request body *before* spending a slot in the rate
    # limiter, so an invalid request never blocks a valid one that
    # follows it within the cooldown window.
    if not ingredients:
        return jsonify({"error": "Please enter at least one ingredient."}), 400
    if len(ingredients) > 500:
        return jsonify({"error": "That ingredient list is too long — keep it under 500 characters."}), 400

    ip = request.remote_addr or "unknown"
    now = time.time()
    if now - _last_request_at.get(ip, 0) < MIN_SECONDS_BETWEEN_REQUESTS:
        return jsonify({"error": "Please wait a few seconds before trying again."}), 429
    _record_rate_limit(ip, now)

    try:
        servings = int(servings_raw)
    except (TypeError, ValueError):
        servings = 2
    servings = max(1, min(12, servings))

    try:
        result = ai_client.generate_recipes(
            api_key=API_KEY,
            ingredients=ingredients,
            diet=diet,
            cuisine=cuisine,
            servings=servings,
            preferred_model=model,
        )
    except ai_client.GenerationError as e:
        return jsonify({"error": str(e)}), 502

    return jsonify(result)


@app.route("/favorites", methods=["POST"])
def add_favorite():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid JSON payload."}), 400

    cleaned_recipe = _sanitize_recipe(data.get("recipe"))
    if not cleaned_recipe:
        return jsonify({"error": "Missing recipe data."}), 400

    raw_model = data.get("model_used")
    model_used = str(raw_model).strip()[:100] if raw_model else None
    user_id = session.get("user_id")

    saved = db.add_favorite(cleaned_recipe, source_model=model_used, user_id=user_id)
    return jsonify(saved), 201


@app.route("/favorites/<int:favorite_id>/rating", methods=["POST"])
def rate_favorite(favorite_id):
    user_id = session.get("user_id")
    fav = db.get_favorite(favorite_id)
    if not fav:
        return jsonify({"error": "Favorite not found."}), 404
    if fav.get("user_id") is not None and fav.get("user_id") != user_id:
        return jsonify({"error": "Forbidden: You cannot rate another chef's saved recipe."}), 403

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid JSON payload."}), 400

    rating = data.get("rating", 0)
    try:
        rating = int(rating)
    except (TypeError, ValueError):
        return jsonify({"error": "Rating must be a number 0-5."}), 400

    updated = db.set_rating(favorite_id, rating, user_id=user_id)
    if not updated:
        return jsonify({"error": "Favorite not found."}), 404
    return jsonify(updated)


@app.route("/favorites/<int:favorite_id>", methods=["DELETE"])
def remove_favorite(favorite_id):
    user_id = session.get("user_id")
    fav = db.get_favorite(favorite_id)
    if not fav:
        return jsonify({"error": "Favorite not found."}), 404
    if fav.get("user_id") is not None and fav.get("user_id") != user_id:
        return jsonify({"error": "Forbidden: You cannot remove another chef's saved recipe."}), 403

    deleted = db.delete_favorite(favorite_id, user_id=user_id)
    if not deleted:
        return jsonify({"error": "Favorite not found."}), 404
    return jsonify({"deleted": favorite_id})


# --- Authentication & Account Data Rights -------------------------------

@app.route("/api/auth/register", methods=["POST"])
def auth_register():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid JSON payload."}), 400

    username = str(data.get("username") or "").strip().lower()
    password = str(data.get("password") or "")
    display_name = str(data.get("display_name") or "").strip()[:80] or username
    consent_accepted = data.get("consent_accepted", True)

    if not username or len(username) < 2 or len(username) > 50:
        return jsonify({"error": "Username must be between 2 and 50 characters."}), 400
    if not password or len(password) < 4 or len(password) > 128:
        return jsonify({"error": "Password must be at least 4 characters."}), 400
    if not consent_accepted:
        return jsonify({"error": "Consent is required to create an account."}), 400

    try:
        user = db.create_user(
            username=username,
            password=password,
            display_name=display_name,
            consent_accepted=True,
        )
    except sqlite3.IntegrityError:
        return jsonify({"error": "Username is already taken."}), 409

    session["user_id"] = user["id"]
    return jsonify({"user": user}), 201


@app.route("/api/auth/login", methods=["POST"])
def auth_login():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid JSON payload."}), 400

    username = str(data.get("username") or "").strip().lower()
    password = str(data.get("password") or "")
    if not username or not password:
        return jsonify({"error": "Username and password are required."}), 400

    user = db.authenticate_user(username, password)
    if not user:
        return jsonify({"error": "Invalid username or password."}), 401

    session["user_id"] = user["id"]
    return jsonify({"user": user})


@app.route("/api/auth/logout", methods=["POST"])
def auth_logout():
    session.clear()
    return jsonify({"logged_out": True, "user": None})


@app.route("/api/auth/me", methods=["GET"])
def auth_me():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"user": None})
    user = db.get_user_by_id(user_id)
    if not user:
        session.clear()
        return jsonify({"user": None})
    return jsonify({"user": user})


@app.route("/api/account/export", methods=["GET"])
def account_export():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Authentication required."}), 401
    payload = db.export_user_data(user_id)
    if not payload:
        session.clear()
        return jsonify({"error": "Account not found."}), 404
    return jsonify(payload)


@app.route("/api/account", methods=["DELETE"])
def account_delete():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Authentication required."}), 401
    db.delete_user_data(user_id)
    session.clear()
    return jsonify({"deleted": True})


if __name__ == "__main__":
    # use_reloader=False is intentional: the default reloader watches every
    # file in the project directory, including pantrychef.db — so every
    # favorite you save triggers a "code changed" reload and kills the
    # request mid-flight. Restart manually after editing source files.
    port = int(os.environ.get("PORT", 5000))
    app.run(host="127.0.0.1", port=port, debug=True, use_reloader=False)