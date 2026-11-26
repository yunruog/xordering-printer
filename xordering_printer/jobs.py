"""The local ticket queue (SQLite, jobs.db in the data folder): no ticket is lost or printed twice.

A job (one order on one printer, by the server's job id) is stored the moment it is received.
States:
    queued   received, not printed yet (also after failed attempts: retried with a back-off)
    printed  printed here; `reported` says whether the server knows yet

A job id is printed at most once: once it is `printed`, receiving it again (the server didn't get
the report, say the internet dropped) only repeats the report. A reprint from the dashboard is a
new job id, so it prints. Rows are kept 14 days, then pruned.
"""
import json
import random
import sqlite3
import time
from typing import Any, Dict, List, Optional

SCHEMA = """
create table if not exists jobs (
  id text primary key,
  printer_id text not null,
  order_number integer,
  payload text not null,
  state text not null default 'queued',
  attempts integer not null default 0,
  last_error text,
  next_attempt real not null default 0,
  reported integer not null default 0,
  failure_reported_at real,
  received_at real not null,
  printed_at real
);
create index if not exists jobs_state on jobs (state, next_attempt);
"""

# Seconds before the next try after 1, 2, 3, … failures (plus up to 25 % at random, so two programs
# sharing a printer with the POS don't keep colliding). The ticket stays queued meanwhile.
BACKOFF = [2, 5, 10, 20, 30]


class JobStore:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path, timeout=10, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("pragma journal_mode=wal")
        self.db.execute("pragma synchronous=full")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    def add(self, job: Dict[str, Any]) -> bool:
        """Stores a job received from the server; False when this id is already known."""
        cur = self.db.execute(
            "insert or ignore into jobs (id, printer_id, order_number, payload, received_at) values (?, ?, ?, ?, ?)",
            (job["id"], job["printerId"], (job.get("order") or {}).get("number"), json.dumps(job, ensure_ascii=False), time.time()),
        )
        return cur.rowcount == 1

    def get(self, job_id: str) -> Optional[sqlite3.Row]:
        return self.db.execute("select * from jobs where id = ?", (job_id,)).fetchone()

    def due(self, now: Optional[float] = None) -> List[sqlite3.Row]:
        return self.db.execute("select * from jobs where state = 'queued' and next_attempt <= ? order by received_at, id", (now or time.time(),)).fetchall()

    def mark_printed(self, job_id: str) -> None:
        self.db.execute("update jobs set state = 'printed', printed_at = ?, attempts = attempts + 1, last_error = null, reported = 0 where id = ? and state = 'queued'", (time.time(), job_id))

    def mark_failed(self, job_id: str, error: str) -> int:
        row = self.get(job_id)
        attempts = (row["attempts"] if row else 0) + 1
        delay = BACKOFF[min(attempts - 1, len(BACKOFF) - 1)] * (1 + random.random() * 0.25)
        self.db.execute("update jobs set attempts = ?, last_error = ?, next_attempt = ? where id = ? and state = 'queued'", (attempts, error[:300], time.time() + delay, job_id))
        return attempts

    def retry_printer_now(self, printer_id: str) -> None:
        """A printer came back: its waiting jobs go now instead of after their back-off."""
        self.db.execute("update jobs set next_attempt = 0 where printer_id = ? and state = 'queued'", (printer_id,))

    def unreported_printed(self) -> List[sqlite3.Row]:
        return self.db.execute("select * from jobs where state = 'printed' and reported = 0 order by printed_at").fetchall()

    def mark_reported(self, job_id: str) -> None:
        self.db.execute("update jobs set reported = 1 where id = ?", (job_id,))

    def failures_to_report(self, every: float = 60.0) -> List[sqlite3.Row]:
        """Queued jobs that failed and whose failure the server hasn't heard about lately."""
        return self.db.execute(
            "select * from jobs where state = 'queued' and attempts > 0 and (failure_reported_at is null or failure_reported_at < ?)",
            (time.time() - every,),
        ).fetchall()

    def mark_failure_reported(self, job_id: str) -> None:
        self.db.execute("update jobs set failure_reported_at = ? where id = ?", (time.time(), job_id))

    def drop(self, job_id: str) -> None:
        """The server no longer has this job (its printer was deleted): forget it."""
        self.db.execute("delete from jobs where id = ? and state = 'queued'", (job_id,))

    def counts(self) -> Dict[str, int]:
        rows = self.db.execute("select state, reported, count(*) n from jobs group by state, reported").fetchall()
        out = {"queued": 0, "printed": 0, "unreported": 0}
        for r in rows:
            out[r["state"]] = out.get(r["state"], 0) + r["n"]
            if r["state"] == "printed" and not r["reported"]:
                out["unreported"] += r["n"]
        return out

    def recent(self, limit: int = 20) -> List[sqlite3.Row]:
        return self.db.execute("select id, printer_id, order_number, state, attempts, last_error, received_at, printed_at from jobs order by received_at desc limit ?", (limit,)).fetchall()

    def prune(self, days: int = 14) -> None:
        self.db.execute("delete from jobs where state = 'printed' and reported = 1 and printed_at < ?", (time.time() - days * 86400,))
