"""SQLite storage for the stitch-notebooklm plugin.

The plugin owns its own SQLite database at ``db_path`` (received in the
``plugin.init`` handshake).  Tables are created on ``_migrate_db``.
"""

# _generated_by: stitch_plugin_tools scaffold v3

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Any


def _connect(db_path: str) -> sqlite3.Connection:
    """Open a SQLite connection with WAL mode for concurrent reads."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def migrate(db_path: str) -> None:
    """Create the notebooks table if it does not exist (raw_sql migration).

    Owner scoping: the ``owner_id`` column is added to new tables via
    CREATE TABLE and to existing tables via a guarded ALTER TABLE (PRAGMA
    table_info check).  Existing rows get ``owner_id = NULL`` (shared /
    instance-wide), matching the ``_visible_where`` visibility pattern.
    """
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notebooks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                owner_id INTEGER,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        # Guarded migration: CREATE TABLE IF NOT EXISTS does not add columns
        # to an existing table.  Check PRAGMA table_info and ALTER TABLE
        # only when the owner_id column is missing (pre-existing DBs).
        cols = {row[1] for row in conn.execute("PRAGMA table_info(notebooks)")}
        if "owner_id" not in cols:
            conn.execute("ALTER TABLE notebooks ADD COLUMN owner_id INTEGER")
        conn.commit()
    finally:
        conn.close()


# ── Visibility helpers (mirror stitch-totp / stitch-mail pattern) ───────────


def _visible_where(uid: int | None) -> tuple[str, list[Any]]:
    """WHERE clause: own OR instance-shared (NULL owner)."""
    if uid is None:
        return "owner_id IS NULL", []
    return "owner_id IS NULL OR owner_id = ?", [uid]


def list_notebooks(db_path: str, uid: int | None = None) -> list[dict[str, Any]]:
    """Return notebooks visible to *uid* (shared + owned)."""
    conn = _connect(db_path)
    try:
        where, args = _visible_where(uid)
        rows = conn.execute(
            f"SELECT id, title FROM notebooks WHERE {where} ORDER BY created_at DESC",
            args,
        ).fetchall()
        return [{"id": r["id"], "title": r["title"]} for r in rows]
    finally:
        conn.close()


def create_notebook(
    db_path: str, title: str, uid: int | None = None
) -> dict[str, Any]:
    """Insert a notebook record (stamping owner_id = caller) and return it."""
    nb_id = uuid.uuid4().hex[:12]
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO notebooks (id, title, owner_id) VALUES (?, ?, ?)",
            (nb_id, title, uid),
        )
        conn.commit()
    finally:
        conn.close()
    return {"id": nb_id, "title": title}
