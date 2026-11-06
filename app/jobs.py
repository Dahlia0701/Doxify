"""Small SQLite job queue. Each job has one owner; repeated requests reuse its id."""
import json
import sqlite3
import time
import uuid
from pathlib import Path

FINAL = {"completed", "completed_with_warnings", "failed"}
STAGES = {"queued", "understanding", "planning", "rendering", "validating", "repairing"}


class JobStore:
    def __init__(self, db_path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, idem_key TEXT UNIQUE NOT NULL,
                status TEXT NOT NULL, fmt TEXT, title TEXT, path TEXT,
                issues TEXT NOT NULL DEFAULT '[]', details TEXT NOT NULL DEFAULT '{}',
                error TEXT, created REAL NOT NULL, updated REAL NOT NULL)""")

    def _db(self):
        db = sqlite3.connect(self.db_path, timeout=15)
        db.row_factory = sqlite3.Row
        return db

    def create(self, idem_key):
        now = time.time()
        job_id = uuid.uuid4().hex
        with self._db() as db:
            db.execute("INSERT OR IGNORE INTO jobs(id,idem_key,status,created,updated) VALUES(?,?,?,?,?)",
                       (job_id, idem_key, "queued", now, now))
            row = db.execute("SELECT id FROM jobs WHERE idem_key=?", (idem_key,)).fetchone()
        return row["id"], row["id"] == job_id

    def get(self, job_id):
        with self._db() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["issues"] = json.loads(result["issues"])
        result["details"] = json.loads(result["details"])
        return result

    def claim(self, job_id):
        """Atomic queued -> understanding. Only one caller can claim."""
        with self._db() as db:
            cur = db.execute("UPDATE jobs SET status='understanding', updated=? WHERE id=? AND status='queued'",
                             (time.time(), job_id))
            return cur.rowcount == 1

    def stage(self, job_id, stage):
        if stage not in STAGES:
            raise ValueError("Unknown stage")
        with self._db() as db:
            db.execute("UPDATE jobs SET status=?, updated=? WHERE id=? AND status NOT IN ('completed','completed_with_warnings','failed')",
                       (stage, time.time(), job_id))

    def finish(self, job_id, result):
        if result.status not in FINAL:
            raise ValueError("Not a final status")
        details = {"assumptions": result.assumptions, "repairs": result.repairs,
                   "checks_skipped": result.checks_skipped, "intent": result.intent,
                   "render_attempts": result.render_attempts}
        with self._db() as db:
            db.execute("""UPDATE jobs SET status=?,fmt=?,title=?,path=?,issues=?,details=?,updated=?
                          WHERE id=? AND status NOT IN ('completed','completed_with_warnings','failed')""",
                       (result.status, result.fmt, result.title, str(result.path) if result.path else None,
                        json.dumps(result.issues), json.dumps(details), time.time(), job_id))

    def fail(self, job_id, message):
        with self._db() as db:
            db.execute("""UPDATE jobs SET status='failed',error=?,updated=?
                          WHERE id=? AND status NOT IN ('completed','completed_with_warnings','failed')""",
                       (message[:500], time.time(), job_id))
