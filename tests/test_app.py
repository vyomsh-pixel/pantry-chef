"""
Tests for PantryChef. Run with:  pytest

None of these hit the real OpenRouter API — network calls are monkeypatched
so the suite runs offline and instantly.
"""

import json
import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ai_client
import db
import app


def test_normalize_env_value_strips_wrapping_quotes_and_parentheses():
    assert app.normalize_env_value('("abc")') == "abc"
    assert app.normalize_env_value('"xyz"') == "xyz"
    assert app.normalize_env_value('  value  ') == "value"
    assert app.normalize_env_value(None) is None


def test_load_environment_file_sets_values_from_dotenv(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text('OPENROUTER_API_KEY=("abc")\nPREFERRED_MODEL="xyz"\n', encoding="utf-8")

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("PREFERRED_MODEL", raising=False)

    app.load_environment_file(str(env_file))

    assert os.environ["OPENROUTER_API_KEY"] == "abc"
    assert os.environ["PREFERRED_MODEL"] == "xyz"


# ------------------------------------------------------------------
# ai_client.build_prompt
# ------------------------------------------------------------------

def test_build_prompt_includes_all_inputs():
    prompt = ai_client.build_prompt("rice, eggs", "Vegan", "Indian", 3)
    assert "rice, eggs" in prompt
    assert "Vegan" in prompt
    assert "Indian" in prompt
    assert "3" in prompt


# ------------------------------------------------------------------
# ai_client.parse_recipes
# ------------------------------------------------------------------

VALID_PAYLOAD = json.dumps({
    "recipes": [
        {
            "name": "Egg Fried Rice",
            "cook_time": "15 minutes",
            "ingredients": [{"item": "rice", "amount": "2 cups"}],
            "steps": ["Cook rice", "Add egg"],
            "nutrition": {"calories": "350", "protein": "12g"},
        },
        {"name": "Rice Pudding", "cook_time": "20 minutes", "ingredients": [], "steps": [], "nutrition": {}},
        {"name": "Rice Soup", "cook_time": "25 minutes", "ingredients": [], "steps": [], "nutrition": {}},
    ]
})


def test_parse_recipes_valid_json():
    recipes = ai_client.parse_recipes(VALID_PAYLOAD)
    assert len(recipes) == 3
    assert recipes[0]["name"] == "Egg Fried Rice"
    assert recipes[0]["ingredients"][0]["item"] == "rice"


def test_parse_recipes_strips_markdown_fences():
    fenced = f"Here you go:\n```json\n{VALID_PAYLOAD}\n```"
    recipes = ai_client.parse_recipes(fenced)
    assert len(recipes) == 3


def test_parse_recipes_raises_on_garbage():
    with pytest.raises(ai_client.GenerationError):
        ai_client.parse_recipes("Sorry, I can't help with that today!")


def test_parse_recipes_raises_on_empty_recipes_array():
    with pytest.raises(ai_client.GenerationError):
        ai_client.parse_recipes(json.dumps({"recipes": []}))


# ------------------------------------------------------------------
# ai_client.generate_recipes — model fallback behaviour
# ------------------------------------------------------------------

class FakeResponse:
    def __init__(self, status_code, json_data):
        self.status_code = status_code
        self._json = json_data
        self.text = json.dumps(json_data)

    def json(self):
        return self._json


def test_generate_recipes_falls_back_to_next_model(monkeypatch):
    """First model is rate limited (429), second model succeeds."""
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(json["model"])
        if len(calls) == 1:
            return FakeResponse(429, {"error": "rate limited"})
        return FakeResponse(200, {
            "choices": [{"message": {"content": VALID_PAYLOAD}}],
        })

    monkeypatch.setattr(ai_client.requests, "post", fake_post)
    monkeypatch.setattr(ai_client, "get_free_models", lambda: ["model-a", "model-b"])
    ai_client._response_cache.clear()

    result = ai_client.generate_recipes(
        api_key="dummy", ingredients="rice", diet="Any", cuisine="Any", servings=2
    )
    assert result["model_used"] == "model-b"
    assert len(calls) == 2


def test_generate_recipes_raises_when_all_models_fail(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):
        return FakeResponse(429, {"error": "rate limited"})

    monkeypatch.setattr(ai_client.requests, "post", fake_post)
    monkeypatch.setattr(ai_client, "get_free_models", lambda: ["model-a"])
    ai_client._response_cache.clear()

    with pytest.raises(ai_client.GenerationError):
        ai_client.generate_recipes(
            api_key="dummy", ingredients="rice", diet="Any", cuisine="Any", servings=2
        )


def test_generate_recipes_uses_cache_on_repeat_request(monkeypatch):
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(1)
        return FakeResponse(200, {"choices": [{"message": {"content": VALID_PAYLOAD}}]})

    monkeypatch.setattr(ai_client.requests, "post", fake_post)
    monkeypatch.setattr(ai_client, "get_free_models", lambda: ["model-a"])
    ai_client._response_cache.clear()

    args = dict(api_key="dummy", ingredients="eggs", diet="Any", cuisine="Any", servings=2)
    ai_client.generate_recipes(**args)
    ai_client.generate_recipes(**args)  # should hit cache, not call again

    assert len(calls) == 1


# ------------------------------------------------------------------
# db.py — favorites CRUD (isolated temp database per test)
# ------------------------------------------------------------------

@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(db, "DB_PATH", str(db_file))
    db.init_db()
    return db


def test_add_and_list_favorite(temp_db):
    recipe = {
        "name": "Test Recipe",
        "cook_time": "10 min",
        "ingredients": [{"item": "salt", "amount": "1 tsp"}],
        "steps": ["Mix it"],
        "nutrition": {"calories": "100"},
    }
    saved = temp_db.add_favorite(recipe, source_model="test-model")
    assert saved["name"] == "Test Recipe"
    assert saved["rating"] == 0

    all_favorites = temp_db.list_favorites()
    assert len(all_favorites) == 1
    assert all_favorites[0]["id"] == saved["id"]


def test_set_rating_clamps_range(temp_db):
    saved = temp_db.add_favorite({"name": "X", "ingredients": [], "steps": []})
    updated = temp_db.set_rating(saved["id"], 9)  # out of range, should clamp to 5
    assert updated["rating"] == 5

    updated = temp_db.set_rating(saved["id"], -3)
    assert updated["rating"] == 0


def test_delete_favorite(temp_db):
    saved = temp_db.add_favorite({"name": "Y", "ingredients": [], "steps": []})
    assert temp_db.delete_favorite(saved["id"]) is True
    assert temp_db.get_favorite(saved["id"]) is None
    assert temp_db.delete_favorite(saved["id"]) is False  # already gone


# ------------------------------------------------------------------
# Flask routes
# ------------------------------------------------------------------

@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_file = tmp_path / "routes_test.db"
    monkeypatch.setattr(db, "DB_PATH", str(db_file))
    db.init_db()

    import app as flask_app_module
    monkeypatch.setattr(flask_app_module, "API_KEY", "dummy-key")
    flask_app_module.app.config["TESTING"] = True
    with flask_app_module.app.test_client() as c:
        yield c


def test_generate_route_rejects_empty_ingredients(client):
    resp = client.post("/generate", json={"ingredients": ""})
    assert resp.status_code == 400


def test_generate_route_rejects_overlong_ingredients(client):
    resp = client.post("/generate", json={"ingredients": "x" * 501})
    assert resp.status_code == 400


def test_favorites_route_requires_recipe(client):
    resp = client.post("/favorites", json={})
    assert resp.status_code == 400


def test_favorites_full_flow(client):
    add_resp = client.post("/favorites", json={
        "recipe": {"name": "Flow Test", "ingredients": [], "steps": []},
        "model_used": "test-model",
    })
    assert add_resp.status_code == 201
    fav_id = add_resp.get_json()["id"]

    rate_resp = client.post(f"/favorites/{fav_id}/rating", json={"rating": 4})
    assert rate_resp.status_code == 200
    assert rate_resp.get_json()["rating"] == 4

    delete_resp = client.delete(f"/favorites/{fav_id}")
    assert delete_resp.status_code == 200

    missing_resp = client.delete(f"/favorites/{fav_id}")
    assert missing_resp.status_code == 404
