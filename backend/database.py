import aiosqlite
import json
from pathlib import Path
import util

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "nuwa.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id             TEXT PRIMARY KEY,
    status         TEXT NOT NULL,
    config         TEXT NOT NULL,          -- JSON: user input
    domain_profile TEXT,                   -- JSON: agent-derived
    progress       TEXT,                   -- JSON
    regions        TEXT,                   -- JSON: reviewed via-mask pool
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
    region_ids          TEXT,              -- JSON: ids into runs.regions
    prompt              TEXT,
    artifacts           TEXT,              -- JSON
    evaluation          TEXT,              -- JSON
    adaptation          TEXT,              -- JSON
    human_decision      TEXT,              -- JSON
    labels              TEXT,              -- JSON
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
           (id, status, config, domain_profile, progress, regions, error, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            run["id"], run["status"],
            json.dumps(run["config"]),
            json.dumps(run["domain_profile"]),
            json.dumps(run["progress"]),
            _dump(run["regions"]),
            run["error"], run["created_at"], run["updated_at"],
        ),
    )
    await db.commit()


async def update_run(db: aiosqlite.Connection, run: dict) -> None:
    run["updated_at"] = util.utcnow_iso()
    await db.execute(
        """UPDATE runs SET status=?, domain_profile=?, progress=?, regions=?,
                           error=?, updated_at=? WHERE id=?""",
        (run["status"], json.dumps(run["domain_profile"]), json.dumps(run["progress"]),
         _dump(run["regions"]), run["error"], run["updated_at"], run["id"]),
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
        "regions": _load(row["regions"]),
        "error": row["error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


async def list_runs(db: aiosqlite.Connection) -> list[dict]:
    cur = await db.execute("SELECT * FROM runs ORDER BY created_at DESC")
    rows = await cur.fetchall()
    return [_row_to_run(r) for r in rows]


def _dump(v):
    return json.dumps(v) if v is not None else None


def _load(v):
    return json.loads(v) if v is not None else None


async def insert_candidate(db: aiosqlite.Connection, c: dict) -> None:
    await db.execute(
        """INSERT INTO candidates
           (id, run_id, status, phase, defect_type, parent_candidate_id,
            region_ids, prompt, artifacts, evaluation, adaptation,
            human_decision, labels, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (c["id"], c["run_id"], c["status"], c["phase"], c["defect_type"],
         c["parent_candidate_id"], _dump(c["region_ids"]), c["prompt"],
         _dump(c["artifacts"]), _dump(c["evaluation"]), _dump(c["adaptation"]),
         _dump(c["human_decision"]), _dump(c["labels"]), c["created_at"]),
    )
    await db.commit()


async def update_candidate(db: aiosqlite.Connection, c: dict) -> None:
    await db.execute(
        """UPDATE candidates SET
             status=?, phase=?, defect_type=?, parent_candidate_id=?,
             region_ids=?, prompt=?, artifacts=?, evaluation=?, adaptation=?,
             human_decision=?, labels=?
           WHERE id=?""",
        (c["status"], c["phase"], c["defect_type"], c["parent_candidate_id"],
         _dump(c["region_ids"]), c["prompt"], _dump(c["artifacts"]), _dump(c["evaluation"]),
         _dump(c["adaptation"]), _dump(c["human_decision"]), _dump(c["labels"]), c["id"]),
    )
    await db.commit()


async def get_candidate(db: aiosqlite.Connection, cid: str) -> dict | None:
    cur = await db.execute("SELECT * FROM candidates WHERE id = ?", (cid,))
    row = await cur.fetchone()
    return _row_to_candidate(row) if row else None


async def list_candidates(db: aiosqlite.Connection, run_id: str) -> list[dict]:
    cur = await db.execute(
        "SELECT * FROM candidates WHERE run_id = ? ORDER BY created_at", (run_id,)
    )
    rows = await cur.fetchall()
    return [_row_to_candidate(r) for r in rows]


def _row_to_candidate(row: aiosqlite.Row) -> dict:
    return {
        "id": row["id"], "run_id": row["run_id"], "status": row["status"],
        "phase": row["phase"], "defect_type": row["defect_type"],
        "parent_candidate_id": row["parent_candidate_id"],
        "region_ids": _load(row["region_ids"]), "prompt": row["prompt"],
        "artifacts": _load(row["artifacts"]), "evaluation": _load(row["evaluation"]),
        "adaptation": _load(row["adaptation"]), "human_decision": _load(row["human_decision"]),
        "labels": _load(row["labels"]), "created_at": row["created_at"],
    }


async def insert_dataset_entry(db: aiosqlite.Connection, e: dict) -> None:
    await db.execute(
        """INSERT INTO dataset_entries
           (id, run_id, candidate_id, category, bbox, split, image_path)
           VALUES (?,?,?,?,?,?,?)""",
        (e["id"], e["run_id"], e["candidate_id"], e["category"],
         _dump(e["bbox"]), e["split"], e["image_path"]),
    )
    await db.commit()


    # ── Source image helpers ──────────────────────────────────────────────


async def insert_source_image(db, img: dict) -> None:
    await db.execute(
        """INSERT INTO source_images (id, path, width, height, uploaded_at)
           VALUES (?, ?, ?, ?, ?)""",
        (img["id"], img["path"], img["width"], img["height"], img["uploaded_at"]),
    )
    await db.commit()


async def get_source_image(db, image_id: str) -> dict | None:
    cur = await db.execute("SELECT * FROM source_images WHERE id = ?", (image_id,))
    row = await cur.fetchone()
    return dict(row) if row else None


async def list_source_images(db) -> list[dict]:
    cur = await db.execute("SELECT * FROM source_images ORDER BY uploaded_at DESC")
    return [dict(r) for r in await cur.fetchall()]


async def resolve_source_image(db, run: dict) -> str:
    """On-disk path of a run's primary source image.
    Raises ValueError if unset/missing — callers translate (HTTP vs loop)."""
    ids = run["config"].get("source_image_ids") or []
    if not ids:
        raise ValueError("run has no source_image_ids")
    img = await get_source_image(db, ids[0])
    if img is None:
        raise ValueError(f"source image {ids[0]} not found")
    return img["path"]