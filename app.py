
import os
import time
import sqlite3
from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv

import db
import ai_client

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOTENV_PATH = os.path.join(BASE_DIR, ".env")

app = Flask(__name__)


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
        if len(value) >= 2 and value[0] == '(' and value[-1] == ')':
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
                os.environ[key] = normalize_env_value(value)


load_environment_file(DOTENV_PATH)
load_dotenv(dotenv_path=DOTENV_PATH, override=True)

API_KEY = normalize_env_value(os.environ.get("OPENROUTER_API_KEY"))
PREFERRED_MODEL = normalize_env_value(os.environ.get("PREFERRED_MODEL"))  # optional, empty = auto

if API_KEY:
    os.environ["OPENROUTER_API_KEY"] = API_KEY
if PREFERRED_MODEL:
    os.environ["PREFERRED_MODEL"] = PREFERRED_MODEL

db.init_db()

if not API_KEY:
    print(
        "\n⚠️  OPENROUTER_API_KEY is not set. Copy .env.example to .env and "
        "add your key, or /generate will only return errors.\n"
    )

# --- very basic per-process, per-IP rate limiting -----------------------
# Since one shared server-side key serves every visitor, this stops a
# runaway tab or accidental double-click loop from burning the whole
# daily quota. Resets on restart; not meant as abuse protection for a
# publicly-hosted deployment.
_last_request_at = {}
MIN_SECONDS_BETWEEN_REQUESTS = 3


@app.route("/")
def index():
    return render_template("index.html", default_model=PREFERRED_MODEL or "auto (best available free model)")


@app.route("/favorites")
def favorites_page():
    return render_template("favorites.html", favorites=db.list_favorites())


@app.route("/generate", methods=["POST"])
def generate():
    if not API_KEY:
        return jsonify({
            "error": "The app owner hasn't configured an OpenRouter API key "
                     "yet (OPENROUTER_API_KEY is missing on the server)."
        }), 500

    data = request.get_json(force=True)
    ingredients = (data.get("ingredients") or "").strip()
    diet = data.get("diet", "No restriction")
    cuisine = data.get("cuisine", "Any")
    servings = data.get("servings", 2)
    model = data.get("model") or PREFERRED_MODEL

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
    _last_request_at[ip] = now

    try:
        servings = int(servings)
    except (TypeError, ValueError):
        servings = 2

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
    data = request.get_json(force=True)
    recipe = data.get("recipe")
    model_used = data.get("model_used")

    if not recipe or "name" not in recipe:
        return jsonify({"error": "Missing recipe data."}), 400

    saved = db.add_favorite(recipe, source_model=model_used)
    return jsonify(saved), 201


@app.route("/favorites/<int:favorite_id>/rating", methods=["POST"])
def rate_favorite(favorite_id):
    data = request.get_json(force=True)
    rating = data.get("rating", 0)
    try:
        rating = int(rating)
    except (TypeError, ValueError):
        return jsonify({"error": "Rating must be a number 0-5."}), 400

    updated = db.set_rating(favorite_id, rating)
    if not updated:
        return jsonify({"error": "Favorite not found."}), 404
    return jsonify(updated)


@app.route("/favorites/<int:favorite_id>", methods=["DELETE"])
def remove_favorite(favorite_id):
    deleted = db.delete_favorite(favorite_id)
    if not deleted:
        return jsonify({"error": "Favorite not found."}), 404
    return jsonify({"deleted": favorite_id})


if __name__ == "__main__":
    # use_reloader=False is intentional: the default reloader watches every
    # file in the project directory, including pantrychef.db — so every
    # favorite you save triggers a "code changed" reload and kills the
    # request mid-flight. Restart manually after editing source files.
    app.run(host="127.0.0.1", port=5000, debug=True, use_reloader=False)