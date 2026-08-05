import sqlite3
import json
import os
from datetime import datetime, timezone

DB_PATH = os.environ.get("PANTRYCHEF_DB", "pantrychef.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS favorites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    cook_time TEXT,
    ingredients_json TEXT NOT NULL,
    steps_json TEXT NOT NULL,
    nutrition_json TEXT,
    rating INTEGER NOT NULL DEFAULT 0,
    source_model TEXT,
    created_at TEXT NOT NULL
);
"""


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with get_connection() as conn:
        conn.execute(SCHEMA)


def add_favorite(recipe: dict, source_model: str | None = None) -> dict:
    """recipe must have: name, cook_time, ingredients, steps, nutrition."""
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO favorites
               (name, cook_time, ingredients_json, steps_json, nutrition_json,
                rating, source_model, created_at)
               VALUES (?, ?, ?, ?, ?, 0, ?, ?)""",
            (
                recipe["name"],
                recipe.get("cook_time", ""),
                json.dumps(recipe.get("ingredients", [])),
                json.dumps(recipe.get("steps", [])),
                json.dumps(recipe.get("nutrition", {})),
                source_model,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        return get_favorite(cur.lastrowid)


def get_favorite(favorite_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM favorites WHERE id = ?", (favorite_id,)
        ).fetchone()
        return _row_to_dict(row) if row else None


def list_favorites() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM favorites ORDER BY created_at DESC"
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def set_rating(favorite_id: int, rating: int) -> dict | None:
    rating = max(0, min(5, int(rating)))
    with get_connection() as conn:
        conn.execute(
            "UPDATE favorites SET rating = ? WHERE id = ?", (rating, favorite_id)
        )
        conn.commit()
    return get_favorite(favorite_id)


def delete_favorite(favorite_id: int) -> bool:
    with get_connection() as conn:
        cur = conn.execute("DELETE FROM favorites WHERE id = ?", (favorite_id,))
        conn.commit()
        return cur.rowcount > 0


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
        "created_at": row["created_at"],
    }
