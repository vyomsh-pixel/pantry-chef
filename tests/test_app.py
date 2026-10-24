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


def test_load_environment_file_handles_empty_values_without_crash(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text('OPENROUTER_API_KEY="abc"\nPREFERRED_MODEL=\nSECRET_KEY=""\n', encoding="utf-8")

    monkeypatch.setenv("PREFERRED_MODEL", "old-model")
    app.load_environment_file(str(env_file))

    assert os.environ["OPENROUTER_API_KEY"] == "abc"
    assert "PREFERRED_MODEL" not in os.environ
    assert "SECRET_KEY" not in os.environ


def test_parse_recipes_handles_non_dict_json_and_uppercase_fences():
    payload = json.dumps({
        "recipes": [
            {
                "name": "Simple Rice",
                "cook_time": "20 minutes",
                "ingredients": ["2 cups rice", {"item": "water", "amount": "4 cups"}],
                "steps": ["Boil water", "Add rice"],
                "nutrition": {"calories": "350 kcal", "protein": "7g"},
            }
        ]
    })
    fenced = f"```JSON\n{payload}\n```"
    recipes = ai_client.parse_recipes(fenced)
    assert len(recipes) == 1
    assert recipes[0]["ingredients"] == [
        {"item": "2 cups rice", "amount": ""},
        {"item": "water", "amount": "4 cups"},
    ]
    assert recipes[0]["nutrition"]["calories"] == "350"

    with pytest.raises(ai_client.GenerationError):
        ai_client.parse_recipes("[1, 2, 3]")


def test_generate_recipes_canonicalizes_ingredient_cache_key(monkeypatch):
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(1)
        return FakeResponse(200, {"choices": [{"message": {"content": VALID_PAYLOAD}}]})

    monkeypatch.setattr(ai_client.requests, "post", fake_post)
    monkeypatch.setattr(ai_client, "get_free_models", lambda: ["model-a"])
    ai_client._response_cache.clear()

    ai_client.generate_recipes(api_key="dummy", ingredients="Rice, Eggs", diet="Any", cuisine="Any", servings=2)
    ai_client.generate_recipes(api_key="dummy", ingredients="eggs,  rice", diet="Any", cuisine="Any", servings=2)
    assert len(calls) == 1


def test_routes_reject_non_dict_json_and_text_plain_csrf(client):
    resp_list = client.post("/generate", json=["not", "a", "dict"])
    assert resp_list.status_code == 400

    resp_plain = client.post(
        "/generate",
        data='{"ingredients": "rice"}',
        content_type="text/plain",
    )
    assert resp_plain.status_code == 400

    fav_non_dict = client.post("/favorites", json={"recipe": "not-a-dict"})
    assert fav_non_dict.status_code == 400


def test_auth_register_login_export_and_delete_account(client):
    me_initial = client.get("/api/auth/me")
    assert me_initial.status_code == 200
    assert me_initial.get_json()["user"] is None

    reg_resp = client.post("/api/auth/register", json={
        "username": "Chef_Alice",
        "password": "secretpassword",
        "display_name": "Alice",
        "consent_accepted": True,
    })
    assert reg_resp.status_code == 201
    user_data = reg_resp.get_json()["user"]
    assert user_data["username"] == "chef_alice"
    assert user_data["display_name"] == "Alice"
    assert "password_hash" not in user_data

    # Duplicate registration fails with 409
    dup_resp = client.post("/api/auth/register", json={
        "username": "chef_alice",
        "password": "secretpassword",
    })
    assert dup_resp.status_code == 409

    # Save a favorite under the authenticated account
    fav_resp = client.post("/favorites", json={
        "recipe": {"name": "Alice's Risotto", "ingredients": ["1 cup arborio"], "steps": ["Stir"]},
        "model_used": "test-model",
    })
    assert fav_resp.status_code == 201

    # Logout and log back in
    logout_resp = client.post("/api/auth/logout")
    assert logout_resp.status_code == 200
    assert client.get("/api/auth/me").get_json()["user"] is None

    bad_login = client.post("/api/auth/login", json={
        "username": "chef_alice",
        "password": "wrongpassword",
    })
    assert bad_login.status_code == 401

    login_resp = client.post("/api/auth/login", json={
        "username": "Chef_Alice",
        "password": "secretpassword",
    })
    assert login_resp.status_code == 200
    assert login_resp.get_json()["user"]["username"] == "chef_alice"

    # Export user data
    export_resp = client.get("/api/account/export")
    assert export_resp.status_code == 200
    exported = export_resp.get_json()
    assert exported["user"]["username"] == "chef_alice"
    assert len(exported["favorites"]) == 1
    assert exported["favorites"][0]["name"] == "Alice's Risotto"
    assert "exported_at" in exported

    # Delete user account & data
    del_resp = client.delete("/api/account")
    assert del_resp.status_code == 200
    assert del_resp.get_json()["deleted"] is True

    # Session cleared and user + favorites deleted
    assert client.get("/api/auth/me").get_json()["user"] is None
    assert client.get("/api/account/export").status_code == 401
    assert db.list_favorites() == []


def test_multi_user_isolation_and_account_delete_blast_radius(client, tmp_path, monkeypatch):
    # 1. Guest saves a favorite
    guest_fav = db.add_favorite({"name": "Guest Soup", "ingredients": ["water", "beans"], "steps": ["Boil"]})
    assert guest_fav["id"] is not None

    # 2. Register Alice
    client.post("/api/auth/register", json={
        "username": "chef_alice_2",
        "password": "secretpassword",
        "display_name": "Alice Two",
        "consent_accepted": True,
    })
    alice_fav_resp = client.post("/favorites", json={
        "recipe": {"name": "Alice Tart", "ingredients": ["flour", "berries"], "steps": ["Bake"]},
    })
    alice_fav_id = alice_fav_resp.get_json()["id"]

    # 3. Register Bob
    client.post("/api/auth/logout")
    client.post("/api/auth/register", json={
        "username": "chef_bob",
        "password": "secretpassword",
        "display_name": "Bob",
        "consent_accepted": True,
    })
    bob_fav_resp = client.post("/favorites", json={
        "recipe": {"name": "Bob Stew", "ingredients": ["beef", "carrots"], "steps": ["Simmer"]},
    })
    bob_fav_id = bob_fav_resp.get_json()["id"]

    # 4. Bob tries to rate Alice's favorite -> 403
    rate_resp = client.post(f"/favorites/{alice_fav_id}/rating", json={"rating": 5})
    assert rate_resp.status_code == 403

    # 5. Bob tries to delete Alice's favorite -> 403
    del_forbidden = client.delete(f"/favorites/{alice_fav_id}")
    assert del_forbidden.status_code == 403

    # 6. Switch back to Alice and delete Alice's account
    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"username": "chef_alice_2", "password": "secretpassword"})
    del_alice = client.delete("/api/account")
    assert del_alice.status_code == 200

    # 7. CRITICAL: Guest favorite and Bob's favorite MUST NOT be deleted!
    remaining = db.list_favorites()
    remaining_names = {r["name"] for r in remaining}
    assert "Guest Soup" in remaining_names
    assert "Bob Stew" in remaining_names
    assert "Alice Tart" not in remaining_names

    # 8. Secret key persistence check
    test_db_dir = tmp_path / "secret_test"
    test_db_dir.mkdir()
    monkeypatch.setattr(db, "DB_PATH", str(test_db_dir / "pantry.db"))
    monkeypatch.delenv("SECRET_KEY", raising=False)
    k1 = app._get_or_create_secret_key()
    k2 = app._get_or_create_secret_key()
    assert k1 == k2
    assert len(k1) == 64


