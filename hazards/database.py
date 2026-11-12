"""SQLite persistence for reports attached to schematic demo segments."""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_DATABASE = Path(__file__).resolve().parents[1] / "data" / "safenisha.sqlite3"


def connect(database_path=None):
    path = Path(database_path) if database_path is not None else DEFAULT_DATABASE
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


@contextmanager
def _session(database_path=None):
    connection = connect(database_path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize_database(database_path=None):
    with _session(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS hazards (
                id INTEGER PRIMARY KEY,
                edge_id TEXT,
                latitude REAL,
                longitude REAL,
                hazard_type TEXT NOT NULL CHECK (
                    hazard_type IN (
                        'broken_streetlight', 'dark_path',
                        'blocked_footpath', 'other_infrastructure'
                    )
                ),
                description TEXT NOT NULL DEFAULT '',
                reported_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'new'
                    CHECK (status IN ('new', 'confirmed', 'resolved')),
                expires_at TEXT
            )
            """
        )
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(hazards)")
        }
        if "latitude" not in columns:
            connection.execute("ALTER TABLE hazards ADD COLUMN latitude REAL")
        if "longitude" not in columns:
            connection.execute("ALTER TABLE hazards ADD COLUMN longitude REAL")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS hazards_edge_status_idx "
            "ON hazards (edge_id, status, expires_at)"
        )


def insert_hazard(edge_id, hazard_type, description, reported_at, expires_at=None,
                  latitude=None, longitude=None, database_path=None):
    with _session(database_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO hazards (
                edge_id, latitude, longitude, hazard_type, description, reported_at, expires_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (edge_id, latitude, longitude, hazard_type, description, reported_at, expires_at),
        )
        return cursor.lastrowid


def get_hazards(database_path=None, include_expired=True):
    query = "SELECT * FROM hazards"
    parameters = ()
    if not include_expired:
        query += " WHERE expires_at IS NULL OR expires_at > ?"
        parameters = (datetime.now(timezone.utc).isoformat(),)
    query += " ORDER BY reported_at DESC, id DESC"
    with _session(database_path) as connection:
        return [dict(row) for row in connection.execute(query, parameters).fetchall()]


def get_confirmed_hazards(database_path=None):
    now = datetime.now(timezone.utc).isoformat()
    with _session(database_path) as connection:
        rows = connection.execute(
            """
            SELECT * FROM hazards
            WHERE status = 'confirmed' AND (expires_at IS NULL OR expires_at > ?)
            ORDER BY reported_at DESC, id DESC
            """,
            (now,),
        ).fetchall()
        return [dict(row) for row in rows]


def update_hazard_status(hazard_id, status, database_path=None):
    with _session(database_path) as connection:
        cursor = connection.execute(
            "UPDATE hazards SET status = ? WHERE id = ?",
            (status, hazard_id),
        )
        if cursor.rowcount == 0:
            raise ValueError("Hazard report {} was not found.".format(hazard_id))
