import contextlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from werkzeug.security import check_password_hash, generate_password_hash

DB_PATH = os.environ.get("PANTRYCHEF_DB", "pantrychef.db")

USERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    display_name TEXT NOT NULL,
    consent_accepted_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

FAVORITES_SCHEMA = """
CREATE TABLE IF NOT EXISTS favorites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    name TEXT NOT NULL,
    cook_time TEXT,
    ingredients_json TEXT NOT NULL,
    steps_json TEXT NOT NULL,
    nutrition_json TEXT,
    rating INTEGER NOT NULL DEFAULT 0,
    source_model TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
"""


@contextlib.contextmanager
def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_connection() as conn:
        conn.execute(USERS_SCHEMA)
        conn.execute(FAVORITES_SCHEMA)
        cols = [
            r["name"]
            for r in conn.execute("PRAGMA table_info(favorites)").fetchall()
        ]
        if "user_id" not in cols:
            conn.execute(
                "ALTER TABLE favorites ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE"
            )


def create_user(
    username: str,
    password: str,
    display_name: str | None = None,
    consent_accepted: bool = True,
) -> dict:
    clean_username = username.strip().lower()
    clean_display = (display_name or clean_username).strip() or clean_username
    now_iso = datetime.now(timezone.utc).isoformat()
    consent_ts = now_iso if consent_accepted else ""
    pw_hash = generate_password_hash(password)

    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO users
               (username, password_hash, display_name, consent_accepted_at, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (clean_username, pw_hash, clean_display, consent_ts, now_iso),
        )
        user_id = cur.lastrowid

    return {
        "id": user_id,
        "username": clean_username,
        "display_name": clean_display,
        "consent_accepted_at": consent_ts,
        "created_at": now_iso,
    }


def authenticate_user(username: str, password: str) -> dict | None:
    clean_username = username.strip().lower()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (clean_username,)
        ).fetchone()
    if not row or not check_password_hash(row["password_hash"], password):
        return None
    return _user_row_to_dict(row)


def get_user_by_id(user_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    return _user_row_to_dict(row) if row else None


def export_user_data(user_id: int) -> dict | None:
    user = get_user_by_id(user_id)
    if not user:
        return None
    favorites = list_favorites(user_id=user_id)
    return {
        "user": user,
        "favorites": favorites,
        "exported_at": datetime.now(timezone.utc).isoformat(),
    }


def delete_user_data(user_id: int) -> bool:
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM favorites WHERE user_id = ?",
            (user_id,),
        )
        cur = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        return cur.rowcount > 0


def add_favorite(
    recipe: dict, source_model: str | None = None, user_id: int | None = None
) -> dict:
    """recipe must have: name, cook_time, ingredients, steps, nutrition."""
    name = str(recipe["name"])
    cook_time = str(recipe.get("cook_time", ""))
    ingredients = (
        recipe.get("ingredients", [])
        if isinstance(recipe.get("ingredients"), list)
        else []
    )
    steps = (
        recipe.get("steps", [])
        if isinstance(recipe.get("steps"), list)
        else []
    )
    nutrition = (
        recipe.get("nutrition", {})
        if isinstance(recipe.get("nutrition"), dict)
        else {}
    )
    created_at = datetime.now(timezone.utc).isoformat()

    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO favorites
               (user_id, name, cook_time, ingredients_json, steps_json, nutrition_json,
                rating, source_model, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)""",
            (
                user_id,
                name,
                cook_time,
                json.dumps(ingredients),
                json.dumps(steps),
                json.dumps(nutrition),
                source_model,
                created_at,
            ),
        )
        fav_id = cur.lastrowid

    return {
        "id": fav_id,
        "name": name,
        "cook_time": cook_time,
        "ingredients": ingredients,
        "steps": steps,
        "nutrition": nutrition,
        "rating": 0,
        "source_model": source_model,
        "created_at": created_at,
    }


def get_favorite(favorite_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM favorites WHERE id = ?", (favorite_id,)
        ).fetchone()
        return _row_to_dict(row) if row else None


def list_favorites(user_id: int | None = None) -> list[dict]:
    with get_connection() as conn:
        if user_id is not None:
            rows = conn.execute(
                "SELECT * FROM favorites WHERE user_id = ? OR user_id IS NULL ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM favorites ORDER BY created_at DESC"
            ).fetchall()
        return [_row_to_dict(r) for r in rows]


def set_rating(favorite_id: int, rating: int, user_id: int | None = None) -> dict | None:
    rating = max(0, min(5, int(rating)))
    with get_connection() as conn:
        if user_id is not None:
            cur = conn.execute(
                "UPDATE favorites SET rating = ? WHERE id = ? AND (user_id = ? OR user_id IS NULL)",
                (rating, favorite_id, user_id),
            )
        else:
            cur = conn.execute(
                "UPDATE favorites SET rating = ? WHERE id = ?", (rating, favorite_id)
            )
        if cur.rowcount == 0:
            return None
        row = conn.execute(
            "SELECT * FROM favorites WHERE id = ?", (favorite_id,)
        ).fetchone()
        return _row_to_dict(row) if row else None


def delete_favorite(favorite_id: int, user_id: int | None = None) -> bool:
    with get_connection() as conn:
        if user_id is not None:
            cur = conn.execute(
                "DELETE FROM favorites WHERE id = ? AND (user_id = ? OR user_id IS NULL)",
                (favorite_id, user_id),
            )
        else:
            cur = conn.execute("DELETE FROM favorites WHERE id = ?", (favorite_id,))
        return cur.rowcount > 0


def _user_row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "consent_accepted_at": row["consent_accepted_at"],
        "created_at": row["created_at"],
    }


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "cook_time": row["cook_time"],
        "ingredients": json.loads(row["ingredients_json"]),
        "steps": json.loads(row["steps_json"]),
        "nutrition": json.loads(row["nutrition_json"] or "{}"),
        "rating": row["rating"],
        "source_model": row["source_model"],
        "user_id": row["user_id"] if "user_id" in row.keys() else None,
        "created_at": row["created_at"],
    }

