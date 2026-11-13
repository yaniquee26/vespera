"""SQLite storage: one file holds every run, its cases and its attempts."""

import json
import sqlite3
from pathlib import Path

from vespera.contracts import Attempt, Case, Category, CheckResult, CheckStatus, Language, Snapshot
from vespera.snapshot import now_iso

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS cases (
    run_id INTEGER NOT NULL REFERENCES runs(id),
    case_id TEXT NOT NULL,
    category TEXT NOT NULL,
    language TEXT NOT NULL,
    prompt TEXT NOT NULL,
    checks TEXT NOT NULL,
    params TEXT NOT NULL,
    PRIMARY KEY (run_id, case_id)
);
CREATE TABLE IF NOT EXISTS attempts (
    run_id INTEGER NOT NULL REFERENCES runs(id),
    case_id TEXT NOT NULL,
    repeat INTEGER NOT NULL,
    reply TEXT NOT NULL,
    error TEXT,
    latency_ms INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, case_id, repeat)
);
CREATE TABLE IF NOT EXISTS check_results (
    run_id INTEGER NOT NULL REFERENCES runs(id),
    case_id TEXT NOT NULL,
    repeat INTEGER NOT NULL,
    check_name TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT NOT NULL,
    excerpt TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (run_id, case_id, repeat, check_name)
);
"""


class Storage:
    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path))
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    def create_run(self, snapshot: Snapshot) -> int:
        cursor = self._db.execute(
            "INSERT INTO runs (snapshot, status, started_at) VALUES (?, 'running', ?)",
            (snapshot.model_dump_json(), now_iso()),
        )
        self._db.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def run_exists(self, run_id: int) -> bool:
        row = self._db.execute("SELECT 1 FROM runs WHERE id = ?", (run_id,)).fetchone()
        return row is not None

    def latest_run_id(self) -> int | None:
        row = self._db.execute("SELECT MAX(id) FROM runs").fetchone()
        return None if row[0] is None else int(row[0])

    def get_snapshot(self, run_id: int) -> Snapshot:
        row = self._db.execute("SELECT snapshot FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(f"no run with id {run_id}")
        return Snapshot.model_validate_json(row[0])

    def get_status(self, run_id: int) -> str:
        row = self._db.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(f"no run with id {run_id}")
        return str(row[0])

    def finish_run(self, run_id: int) -> None:
        self._db.execute(
            "UPDATE runs SET status = 'finished', finished_at = ? WHERE id = ?",
            (now_iso(), run_id),
        )
        self._db.commit()

    def save_cases(self, run_id: int, cases: list[Case]) -> None:
        self._db.executemany(
            "INSERT OR REPLACE INTO cases VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    run_id,
                    case.id,
                    case.category.value,
                    case.language.value,
                    case.prompt,
                    json.dumps(case.checks),
                    json.dumps(case.params, ensure_ascii=False),
                )
                for case in cases
            ],
        )
        self._db.commit()

    def add_attempt(self, run_id: int, attempt: Attempt) -> None:
        """Saved immediately, so an interrupted run loses nothing."""
        self._db.execute(
            "INSERT OR REPLACE INTO attempts VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                attempt.case_id,
                attempt.repeat,
                attempt.reply,
                attempt.error,
                attempt.latency_ms,
                attempt.created_at,
            ),
        )
        self._db.commit()

    def done_keys(self, run_id: int) -> set[tuple[str, int]]:
        """Attempts that finished without error. Failed ones are retried on resume."""
        rows = self._db.execute(
            "SELECT case_id, repeat FROM attempts WHERE run_id = ? AND error IS NULL",
            (run_id,),
        ).fetchall()
        return {(str(case_id), int(repeat)) for case_id, repeat in rows}

    def get_attempts(self, run_id: int) -> list[Attempt]:
        rows = self._db.execute(
            "SELECT case_id, repeat, reply, error, latency_ms, created_at "
            "FROM attempts WHERE run_id = ? ORDER BY repeat, case_id",
            (run_id,),
        ).fetchall()
        return [
            Attempt(
                case_id=r[0],
                repeat=r[1],
                reply=r[2],
                error=r[3],
                latency_ms=r[4],
                created_at=r[5],
            )
            for r in rows
        ]

    def get_cases(self, run_id: int) -> list[Case]:
        rows = self._db.execute(
            "SELECT case_id, category, language, prompt, checks, params "
            "FROM cases WHERE run_id = ? ORDER BY rowid",
            (run_id,),
        ).fetchall()
        return [
            Case(
                id=r[0],
                scenario_id=str(r[0]).split(":")[0],
                category=Category(r[1]),
                language=Language(r[2]),
                prompt=r[3],
                checks=json.loads(r[4]),
                params=json.loads(r[5]),
            )
            for r in rows
        ]

    def save_check_results(self, run_id: int, results: list[CheckResult]) -> None:
        """Replaces all earlier verdicts of this run, so checking twice is safe."""
        self._db.execute("DELETE FROM check_results WHERE run_id = ?", (run_id,))
        self._db.executemany(
            "INSERT INTO check_results VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    run_id,
                    r.case_id,
                    r.repeat,
                    r.check,
                    r.status.value,
                    r.detail,
                    r.excerpt,
                    r.value,
                )
                for r in results
            ],
        )
        self._db.commit()

    def get_check_results(self, run_id: int) -> list[CheckResult]:
        rows = self._db.execute(
            "SELECT case_id, repeat, check_name, status, detail, excerpt, value "
            "FROM check_results WHERE run_id = ? ORDER BY rowid",
            (run_id,),
        ).fetchall()
        return [
            CheckResult(
                case_id=r[0],
                repeat=r[1],
                check=r[2],
                status=CheckStatus(r[3]),
                detail=r[4],
                excerpt=r[5],
                value=r[6],
            )
            for r in rows
        ]