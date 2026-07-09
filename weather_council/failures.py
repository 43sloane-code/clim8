"""Soft-failure surfacing (Phase 6b): silent except-swallows stop being SILENT.

WHY. The pipeline deliberately swallows many fetch/parse failures to stay resilient — a dead
feed must not crash a run, and that resilience is intentional. But a SWALLOWED settlement-source
failure is invisible data corruption: the day just does not settle, and absence looks like
success. This module keeps a lightweight, additive ledger of soft failures so coverage is
MEASURABLE, not inferred from absence. It NEVER changes control flow — callers keep swallowing;
they merely also record.

READ-ONLY w.r.t. forecasting: it writes only its own additive `soft_failures` table, never a
verdict, vote, weight, score, or served probability. Leaf module — it imports nothing from
weather_council (so instrumenting sources/storage introduces no import cycle). Stdlib-only,
deterministic, self-tested.

Self-test:  python3 -m weather_council.failures
"""
from __future__ import annotations

__all__ = ["record_soft_failure", "recent_soft_failures", "soft_failure_counts", "DB_PATH"]

import datetime as dt
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "verdicts.db"


def _utc_now_iso() -> str:
    """UTC wall-clock, naive ISO, seconds — matches storage.utc_now_iso()'s convention (defined
    locally to keep this a leaf module with no weather_council imports, so sources/storage can
    instrument their swallows without a circular import)."""
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS soft_failures (
               at     TEXT NOT NULL,   -- utc_now_iso when the swallow happened
               tag    TEXT NOT NULL,   -- caller category, e.g. 'settle_wu_fetch', 'book_fetch'
               etype  TEXT NOT NULL,   -- exception class name
               detail TEXT)"""        # str(exc)[:200]
    )
    return conn


def record_soft_failure(tag: str, exc: BaseException, db_path: Path | None = None) -> None:
    """Append one swallowed failure to the ledger. BEST-EFFORT: this must NEVER raise back into
    the caller — a broken failures table cannot be allowed to break the resilience it observes.
    Call it INSIDE the existing `except` block; do not change the control flow around it."""
    try:
        conn = _connect(db_path)
        with conn:
            conn.execute(
                "INSERT INTO soft_failures (at, tag, etype, detail) VALUES (?, ?, ?, ?)",
                (_utc_now_iso(), str(tag), type(exc).__name__, str(exc)[:200]))
        conn.close()
    except Exception:
        pass


def recent_soft_failures(hours: int = 24, db_path: Path | None = None) -> list[dict]:
    """All soft failures in the last `hours` (by the naive-UTC `at` column), newest first."""
    cutoff = (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
              - dt.timedelta(hours=hours)).isoformat(timespec="seconds")
    try:
        conn = _connect(db_path)
        rows = conn.execute(
            "SELECT at, tag, etype, detail FROM soft_failures WHERE at >= ? ORDER BY at DESC",
            (cutoff,)).fetchall()
        conn.close()
    except Exception:
        return []
    return [{"at": r[0], "tag": r[1], "etype": r[2], "detail": r[3]} for r in rows]


def soft_failure_counts(hours: int = 24, db_path: Path | None = None) -> dict[str, int]:
    """{tag: count} over the last `hours` — the healthcheck's summary handle."""
    counts: dict[str, int] = {}
    for r in recent_soft_failures(hours, db_path):
        counts[r["tag"]] = counts.get(r["tag"], 0) + 1
    return counts


def _selftest() -> None:
    import tempfile
    tmp = Path(tempfile.mkdtemp()) / "sf.db"
    # never raises even on a bad exc / value
    record_soft_failure("t", ValueError("boom"), db_path=tmp)
    record_soft_failure("t", KeyError("k"), db_path=tmp)
    record_soft_failure("other", RuntimeError("x" * 500), db_path=tmp)   # detail truncates
    rows = recent_soft_failures(24, db_path=tmp)
    assert len(rows) == 3, rows
    assert rows[0]["detail"] is not None and len(rows[0]["detail"]) <= 200
    counts = soft_failure_counts(24, db_path=tmp)
    assert counts == {"t": 2, "other": 1}, counts
    # recording is best-effort: a bad db path must NOT raise
    record_soft_failure("t", ValueError("x"), db_path=Path("/nonexistent/dir/x.db"))
    # an old row is excluded by the window
    with _connect(tmp) as c:
        c.execute("INSERT INTO soft_failures (at, tag, etype, detail) VALUES "
                  "('2000-01-01T00:00:00','old','E',NULL)")
    assert "old" not in soft_failure_counts(24, db_path=tmp)
    print("failures selftest PASSED (record/query/counts; truncation; window; never-raises)")


if __name__ == "__main__":
    _selftest()
