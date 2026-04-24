"""
SQLite database layer for LinkedIn Job Collector.

Single `jobs` table stores both job info and application status.
"""

import os
import sqlite3
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, "jobs.db")

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id           TEXT PRIMARY KEY,
    title            TEXT,
    company          TEXT,
    location         TEXT,
    url              TEXT,
    easy_apply       INTEGER DEFAULT 0,
    description      TEXT,
    score            REAL    DEFAULT 0,
    score_reason     TEXT,
    match_tags       TEXT,
    search_term      TEXT,
    date_posted      TEXT,
    first_seen       TEXT,
    last_seen        TEXT,
    -- application tracking
    status           TEXT,
    applied_date     TEXT,
    notes            TEXT    DEFAULT '',
    skip_reason      TEXT,
    status_updated   TEXT
);
CREATE INDEX IF NOT EXISTS idx_score     ON jobs(score);
CREATE INDEX IF NOT EXISTS idx_last_seen ON jobs(last_seen);
CREATE INDEX IF NOT EXISTS idx_status    ON jobs(status);
"""


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript(_CREATE_SQL)


def upsert_jobs(jobs: list[dict]):
    """
    Insert new jobs; on conflict update score + last_seen only.
    Application status columns are never overwritten here.
    """
    now = datetime.now().isoformat()
    with get_conn() as conn:
        for j in jobs:
            conn.execute(
                """
                INSERT INTO jobs
                    (job_id, title, company, location, url, easy_apply,
                     description, score, score_reason, match_tags,
                     search_term, date_posted, first_seen, last_seen)
                VALUES
                    (:job_id, :title, :company, :location, :url, :easy_apply,
                     :description, :score, :score_reason, :match_tags,
                     :search_term, :date_posted, :first_seen, :last_seen)
                ON CONFLICT(job_id) DO UPDATE SET
                    score        = excluded.score,
                    score_reason = excluded.score_reason,
                    match_tags   = excluded.match_tags,
                    last_seen    = excluded.last_seen
                """,
                {
                    "job_id":       j.get("job_id", ""),
                    "title":        j.get("title", ""),
                    "company":      j.get("company", ""),
                    "location":     j.get("location", ""),
                    "url":          j.get("url", ""),
                    "easy_apply":   int(bool(j.get("easy_apply", False))),
                    "description":  j.get("description", ""),
                    "score":        float(j.get("relevance_score", j.get("score", 0))),
                    "score_reason": j.get("relevance_reason", j.get("score_reason", "")),
                    "match_tags":   j.get("match_tags", ""),
                    "search_term":  j.get("search_term", ""),
                    "date_posted":  j.get("date_text", ""),
                    "first_seen":   now,
                    "last_seen":    now,
                },
            )
        conn.commit()


def update_status(job_id: str, status):
    """Set or clear application status for a job."""
    now = datetime.now().isoformat()
    with get_conn() as conn:
        if status is None:
            conn.execute(
                "UPDATE jobs SET status=NULL, status_updated=? WHERE job_id=?",
                (now, job_id),
            )
        else:
            conn.execute(
                """UPDATE jobs
                   SET status=?, status_updated=?,
                       applied_date=COALESCE(applied_date, ?)
                   WHERE job_id=?""",
                (status, now, datetime.now().strftime("%Y-%m-%d"), job_id),
            )
        conn.commit()


def update_notes(job_id: str, notes: str):
    with get_conn() as conn:
        conn.execute("UPDATE jobs SET notes=? WHERE job_id=?", (notes, job_id))
        conn.commit()


def get_jobs(
    min_score: float = 0,
    days: int = 0,
    status_filter=None,
    only_easy: bool = False,
    order_by: str = "score DESC, last_seen DESC",
) -> list:
    """
    Fetch jobs from the DB as a list of dicts.
    days=0 means no date filter.
    status_filter=None means all jobs; [] means only untracked jobs.
    """
    conditions = ["score >= ?"]
    params: list = [min_score]

    if days > 0:
        conditions.append("last_seen >= datetime('now', ?)")
        params.append(f"-{days} days")

    if only_easy:
        conditions.append("easy_apply = 1")

    if status_filter is not None:
        if len(status_filter) == 0:
            conditions.append("status IS NULL")
        else:
            placeholders = ",".join("?" * len(status_filter))
            conditions.append(f"status IN ({placeholders})")
            params.extend(status_filter)

    where = " AND ".join(conditions)
    sql = f"SELECT * FROM jobs WHERE {where} ORDER BY {order_by}"

    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_stats() -> dict:
    with get_conn() as conn:
        total      = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        tracked    = conn.execute("SELECT COUNT(*) FROM jobs WHERE status IS NOT NULL").fetchone()[0]
        by_status  = conn.execute(
            "SELECT status, COUNT(*) as n FROM jobs WHERE status IS NOT NULL GROUP BY status"
        ).fetchall()
        today      = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE last_seen >= date('now')"
        ).fetchone()[0]
    return {
        "total": total,
        "tracked": tracked,
        "today": today,
        "by_status": {r["status"]: r["n"] for r in by_status},
    }


def get_negative_examples(limit: int = 15) -> list:
    """
    Return recent 'not interested' jobs for prompt augmentation.
    Used by the scoring system to inject negative few-shot examples.
    """
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT title, company, score_reason
               FROM jobs
               WHERE status = '不感兴趣'
               ORDER BY status_updated DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# ─── Migration helpers ────────────────────────────────────────────────────────

def migrate_from_excel(list_dir: str):
    """Import all existing job_results_*.xlsx files into the DB."""
    import glob
    import pandas as pd

    files = sorted(glob.glob(os.path.join(list_dir, "job_results_*.xlsx")))
    if not files:
        return 0

    col_map = {
        "相关性评分": "relevance_score",
        "评分理由":   "relevance_reason",
        "匹配关键词": "match_tags",
        "职位名称":   "title",
        "公司":       "company",
        "地点":       "location",
        "发布时间":   "date_text",
        "Easy Apply": "easy_apply",
        "搜索词":     "search_term",
        "链接":       "url",
        "职位描述":   "description",
    }

    import re
    def extract_id(url):
        m = re.search(r"/jobs/view/(\d+)", str(url))
        return m.group(1) if m else None

    total = 0
    for fpath in files:
        df = pd.read_excel(fpath)
        df.rename(columns=col_map, inplace=True)
        jobs = []
        for _, row in df.iterrows():
            url = str(row.get("url", ""))
            jid = extract_id(url)
            if not jid:
                continue
            jobs.append({
                "job_id":        jid,
                "title":         str(row.get("title", "")),
                "company":       str(row.get("company", "")),
                "location":      str(row.get("location", "")),
                "url":           url,
                "easy_apply":    bool(row.get("easy_apply", False)),
                "description":   str(row.get("description", "")),
                "relevance_score":  float(row.get("relevance_score", 0) or 0),
                "relevance_reason": str(row.get("relevance_reason", "")),
                "match_tags":    str(row.get("match_tags", "")),
                "search_term":   str(row.get("search_term", "")),
                "date_text":     str(row.get("date_text", "")),
            })
        upsert_jobs(jobs)
        total += len(jobs)
    return total


def migrate_from_applications_json(json_path: str):
    """Apply status from applications.json to existing DB rows."""
    import json

    if not os.path.exists(json_path):
        return 0

    with open(json_path, encoding="utf-8") as f:
        apps = json.load(f)

    with get_conn() as conn:
        for a in apps:
            jid    = a.get("job_id", "")
            status = a.get("status")
            if not jid or not status:
                continue
            conn.execute(
                """UPDATE jobs SET
                     status=?, applied_date=?, notes=?, skip_reason=?, status_updated=?
                   WHERE job_id=?""",
                (
                    status,
                    a.get("applied_date", ""),
                    a.get("notes", ""),
                    a.get("skip_reason"),
                    a.get("updated_at", datetime.now().isoformat()),
                    jid,
                ),
            )
        conn.commit()
    return len(apps)


# Initialise on import
init_db()
