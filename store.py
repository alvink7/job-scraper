"""SQLite dedup store.

Table: seen(id TEXT PRIMARY KEY, first_seen REAL)
The runner commits the DB file back to the repo so dedup survives ephemeral CI.
"""

import sqlite3
import time


class Store:
    def __init__(self, path="seen.db"):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS seen ("
            "  id TEXT PRIMARY KEY,"
            "  first_seen REAL"
            ")"
        )
        self.conn.commit()

    def is_new(self, job_id):
        cur = self.conn.execute(
            "SELECT 1 FROM seen WHERE id = ? LIMIT 1", (job_id,)
        )
        return cur.fetchone() is None

    def mark(self, job_id):
        self.conn.execute(
            "INSERT OR IGNORE INTO seen (id, first_seen) VALUES (?, ?)",
            (job_id, time.time()),
        )

    def commit(self):
        self.conn.commit()

    def count(self):
        cur = self.conn.execute("SELECT COUNT(*) FROM seen")
        return cur.fetchone()[0]

    def close(self):
        self.conn.close()
