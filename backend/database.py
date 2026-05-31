import aiosqlite
import json
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "nuwa.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id             TEXT PRIMARY KEY,
    status         TEXT NOT NULL,
    config         TEXT NOT NULL,          -- JSON: user input
    domain_profile TEXT,                   -- JSON: agent-derived
    progress       TEXT,                   -- JSON
    error          TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidates (
    id                  TEXT PRIMARY KEY,
    run_id              TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    status              TEXT NOT NULL,
    phase               TEXT NOT NULL,
    defect_type         TEXT,
    parent_candidate_id TEXT REFERENCES candidates(id),
    mask                TEXT,              -- JSON
    prompt              TEXT,
    artifacts           TEXT,              -- JSON
    evaluation          TEXT,              -- JSON
    adaptation          TEXT,              -- JSON
    human_decision      TEXT,              -- JSON
    label               TEXT,              -- JSON
    created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decisions (
    id           TEXT PRIMARY KEY,
    run_id       TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    candidate_id TEXT REFERENCES candidates(id) ON DELETE CASCADE,
    actor        TEXT NOT NULL,            -- 'human' | 'auto'
    action       TEXT NOT NULL,
    note         TEXT,
    at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dataset_entries (
    id           TEXT PRIMARY KEY,
    run_id       TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    candidate_id TEXT NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    category     TEXT NOT NULL,
    bbox         TEXT,                     -- JSON
    split        TEXT,
    image_path   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_images (
    id          TEXT PRIMARY KEY,
    path        TEXT NOT NULL,
    width       INTEGER,
    height      INTEGER,
    uploaded_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_candidates_run    ON candidates(run_id);
CREATE INDEX IF NOT EXISTS idx_candidates_run_st ON candidates(run_id, status);
CREATE INDEX IF NOT EXISTS idx_runs_status       ON runs(status);
CREATE INDEX IF NOT EXISTS idx_dataset_run       ON dataset_entries(run_id);
"""


async def connect() -> aiosqlite.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row          # rows behave like dicts
    await db.execute("PRAGMA foreign_keys = ON")
    return db


async def init_db(db: aiosqlite.Connection) -> None:
    await db.executescript(SCHEMA)
    await db.commit()


async def insert_run(db: aiosqlite.Connection, run: dict) -> None:
    await db.execute(
        """INSERT INTO runs
           (id, status, config, domain_profile, progress, error, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            run["id"], run["status"],
            json.dumps(run["config"]),
            json.dumps(run["domain_profile"]),
            json.dumps(run["progress"]),
            run["error"], run["created_at"], run["updated_at"],
        ),
    )
    await db.commit()


async def get_run(db: aiosqlite.Connection, run_id: str) -> dict | None:
    cur = await db.execute("SELECT * FROM runs WHERE id = ?", (run_id,))
    row = await cur.fetchone()
    return _row_to_run(row) if row else None


def _row_to_run(row: aiosqlite.Row) -> dict:
    return {
        "id": row["id"],
        "status": row["status"],
        "config": json.loads(row["config"]),
        "domain_profile": json.loads(row["domain_profile"]) if row["domain_profile"] else None,
        "progress": json.loads(row["progress"]) if row["progress"] else None,
        "error": row["error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


async def list_runs(db: aiosqlite.Connection) -> list[dict]:
    cur = await db.execute("SELECT * FROM runs ORDER BY created_at DESC")
    rows = await cur.fetchall()
    return [_row_to_run(r) for r in rows]