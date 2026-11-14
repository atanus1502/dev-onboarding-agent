"""SQLite persistence: setup progress per user, and every Q&A (mined for eval questions)."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS setup_progress (
    slack_user TEXT NOT NULL,
    repo TEXT NOT NULL,
    step_id TEXT NOT NULL,
    passed INTEGER NOT NULL,
    output TEXT NOT NULL,
    ts REAL NOT NULL,
    PRIMARY KEY (slack_user, repo, step_id)
);
CREATE TABLE IF NOT EXISTS interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    slack_user TEXT NOT NULL,
    channel TEXT NOT NULL,
    message_ts TEXT,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    sources TEXT NOT NULL,
    feedback INTEGER
);
CREATE INDEX IF NOT EXISTS interactions_msg ON interactions (channel, message_ts);
"""


@dataclass(frozen=True)
class Interaction:
    id: int
    ts: float
    slack_user: str
    question: str
    answer: str
    sources: list[str]
    feedback: int | None


class Store:
    def __init__(self, path: Path | str) -> None:
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.executescript(_SCHEMA)

    def record_step(
        self, slack_user: str, repo: str, step_id: str, passed: bool, output: str
    ) -> None:
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO setup_progress VALUES (?, ?, ?, ?, ?, ?)",
                (slack_user, repo, step_id, int(passed), output[:4000], time.time()),
            )

    def passed_steps(self, slack_user: str, repo: str) -> set[str]:
        rows = self._db.execute(
            "SELECT step_id FROM setup_progress WHERE slack_user=? AND repo=? AND passed=1",
            (slack_user, repo),
        )
        return {r[0] for r in rows}

    def progress(self, slack_user: str) -> dict[str, set[str]]:
        """Passed step ids per repo, for repos where the user has passed at least one step."""
        rows = self._db.execute(
            "SELECT repo, step_id FROM setup_progress"
            " WHERE slack_user=? AND passed=1 ORDER BY repo",
            (slack_user,),
        )
        result: dict[str, set[str]] = {}
        for repo, step in rows:
            result.setdefault(repo, set()).add(step)
        return result

    def log_interaction(
        self,
        slack_user: str,
        channel: str,
        message_ts: str | None,
        question: str,
        answer: str,
        sources: list[str],
    ) -> int:
        with self._db:
            cur = self._db.execute(
                "INSERT INTO interactions (ts, slack_user, channel, message_ts, question, answer,"
                " sources) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    time.time(),
                    slack_user,
                    channel,
                    message_ts,
                    question,
                    answer,
                    json.dumps(sources),
                ),
            )
        return int(cur.lastrowid or 0)

    def record_feedback(self, channel: str, message_ts: str, score: int) -> None:
        with self._db:
            self._db.execute(
                "UPDATE interactions SET feedback=? WHERE channel=? AND message_ts=?",
                (score, channel, message_ts),
            )

    def interactions_since(self, since_ts: float) -> list[Interaction]:
        rows = self._db.execute(
            "SELECT id, ts, slack_user, question, answer, sources, feedback FROM interactions"
            " WHERE ts >= ? ORDER BY ts",
            (since_ts,),
        )
        return [Interaction(r[0], r[1], r[2], r[3], r[4], json.loads(r[5]), r[6]) for r in rows]
