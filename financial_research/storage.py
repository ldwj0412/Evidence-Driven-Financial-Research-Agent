import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(UTC)


def json_text(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    title TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS turns (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    run_id TEXT NOT NULL UNIQUE,
    parent_id TEXT REFERENCES turns(id),
    question TEXT NOT NULL,
    mode TEXT NOT NULL,
    cutoff TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS turns_by_session ON turns(session_id, created_at);
CREATE TABLE IF NOT EXISTS selection (
    slot INTEGER PRIMARY KEY CHECK(slot = 1),
    session_id TEXT NOT NULL REFERENCES sessions(id),
    turn_id TEXT REFERENCES turns(id)
);
CREATE TABLE IF NOT EXISTS events (
    run_id TEXT NOT NULL REFERENCES turns(run_id),
    sequence INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    stage TEXT NOT NULL,
    pass_no INTEGER NOT NULL,
    kind TEXT NOT NULL,
    outcome TEXT NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY(run_id, sequence)
);
CREATE TABLE IF NOT EXISTS lookups (
    session_id TEXT NOT NULL REFERENCES sessions(id),
    mode TEXT NOT NULL,
    cache_key TEXT NOT NULL,
    cutoff TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    records TEXT NOT NULL,
    PRIMARY KEY(session_id, mode, cache_key, cutoff)
);
"""


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(_SCHEMA)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=1)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def new_session(self) -> str:
        session = uuid4().hex
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO sessions VALUES (?, ?, ?)", (session, utc_now().isoformat(), "New session")
            )
            self._select(conn, session, None)
        return session

    def _select(self, conn, session_id, turn_id):
        conn.execute(
            "INSERT INTO selection VALUES (1, ?, ?) ON CONFLICT(slot) DO UPDATE SET "
            "session_id = excluded.session_id, turn_id = excluded.turn_id",
            (session_id, turn_id),
        )

    def select(self, session_id, turn_id=None):
        with self.connect() as conn:
            if (
                turn_id is not None
                and not conn.execute(
                    "SELECT 1 FROM turns WHERE id = ? AND session_id = ?",
                    (turn_id, session_id),
                ).fetchone()
            ):
                raise ValueError("Select a question from the current session")
            self._select(conn, session_id, turn_id)

    def selection(self):
        with self.connect() as conn:
            row = conn.execute("SELECT session_id, turn_id FROM selection").fetchone()
        return dict(row) if row else None

    def sessions(self):
        with self.connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM sessions ORDER BY created_at DESC")]

    def turns(self, session_id):
        with self.connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT id, question, mode, status FROM turns WHERE session_id = ? ORDER BY created_at",
                    (session_id,),
                )
            ]

    def create_turn(self, session, question, mode, parent, cutoff):
        turn, run = uuid4().hex, uuid4().hex
        with self.connect() as conn:
            if (
                parent
                and not conn.execute(
                    "SELECT 1 FROM turns WHERE id = ? AND session_id = ? AND status = 'Complete' AND mode = ?",
                    (parent, session, mode),
                ).fetchone()
            ):
                raise ValueError("Follow-ups require a finished question in this session and mode")
            conn.execute(
                "INSERT INTO turns (id, session_id, run_id, parent_id, question, mode, cutoff, created_at, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Queued')",
                (turn, session, run, parent, question, mode, cutoff.isoformat(), utc_now().isoformat()),
            )
            conn.execute(
                "UPDATE sessions SET title = ? WHERE id = ? AND title = 'New session'",
                (question if len(question) <= 42 else question[:41] + "…", session),
            )
            self._select(conn, session, turn)
        return turn

    def turn(self, turn):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM turns WHERE id = ?", (turn,)).fetchone()
        if row is None:
            raise ValueError("The selected question does not exist")
        result = dict(row)
        result.update(json.loads(result.pop("data")))
        return result

    def transition(self, turn, previous, status, **data):
        with self.connect() as conn:
            result = conn.execute(
                "UPDATE turns SET status = ?, data = json_patch(data, ?) WHERE id = ? AND status = ?",
                (status, json_text(data), turn, previous),
            )
            return result.rowcount == 1

    def patch(self, turn, **data):
        with self.connect() as conn:
            conn.execute("UPDATE turns SET data = json_patch(data, ?) WHERE id = ?", (json_text(data), turn))

    def set_cutoff(self, turn, cutoff):
        with self.connect() as conn:
            conn.execute("UPDATE turns SET cutoff = ? WHERE id = ?", (cutoff.isoformat(), turn))

    def _insert_event(self, conn, run, stage, pass_no, kind, outcome, data):
        conn.execute(
            "INSERT INTO events SELECT ?, COALESCE(MAX(sequence), 0) + 1, ?, ?, ?, ?, ?, ? "
            "FROM events WHERE run_id = ?",
            (run, utc_now().isoformat(), stage, pass_no, kind, outcome, json_text(data), run),
        )

    def event(self, turn, stage, pass_no, kind, outcome, data):
        with self.connect() as conn:
            run = conn.execute("SELECT run_id FROM turns WHERE id = ?", (turn,)).fetchone()[0]
            self._insert_event(conn, run, stage, pass_no, kind, outcome, data)

    def events(self, turn):
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT events.* FROM events JOIN turns USING (run_id) WHERE turns.id = ? ORDER BY sequence",
                (turn,),
            )
            return [{**dict(row), "data": json.loads(row["data"])} for row in rows]

    def finish(self, turn, status, data, stage, pass_no, outcome, event_data):
        with self.connect() as conn:
            conn.execute(
                "UPDATE turns SET status = ?, data = json_patch(data, ?) WHERE id = ?",
                (status, json_text(data), turn),
            )
            run = conn.execute("SELECT run_id FROM turns WHERE id = ?", (turn,)).fetchone()[0]
            self._insert_event(conn, run, stage, pass_no, "status", outcome, event_data)

    def recover(self):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute("SELECT run_id FROM turns WHERE status IN ('Running', 'Queued')").fetchall()
            for row in rows:
                latest = conn.execute(
                    "SELECT stage, pass_no FROM events WHERE run_id = ? ORDER BY sequence DESC LIMIT 1",
                    (row["run_id"],),
                ).fetchone()
                stage, pass_no = tuple(latest) if latest else ("Parse", 0)
                conn.execute("UPDATE turns SET status = 'Interrupted' WHERE run_id = ?", (row["run_id"],))
                self._insert_event(
                    conn,
                    row["run_id"],
                    stage,
                    pass_no,
                    "status",
                    "interrupted",
                    {"message": "The app stopped before the run finished."},
                )

    def save_lookup(self, session, mode, key, cutoff, records):
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO lookups VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(session_id, mode, cache_key, cutoff) DO UPDATE SET "
                "observed_at = excluded.observed_at, records = excluded.records",
                (session, mode, key, cutoff.isoformat(), utc_now().isoformat(), json_text(records)),
            )

    def lookups(self, session, mode, key=None):
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM lookups WHERE session_id = ? AND mode = ? AND (? IS NULL OR cache_key = ?) "
                "ORDER BY observed_at DESC",
                (session, mode, key, key),
            )
            return [{**dict(row), "records": json.loads(row["records"])} for row in rows]
