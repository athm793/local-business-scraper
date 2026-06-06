"""SQLite store with WAL mode for concurrent multi-worker access + schema migration."""

import sqlite3
from pathlib import Path


class Database:
    COLUMNS = [
        "place_url", "name", "category", "address", "phone",
        "website", "rating", "review_count", "latitude", "longitude",
        "keyword", "location", "city", "state", "country", "scraped_at",
        "reviews",
    ]

    def __init__(self, db_path: str = "businesses.db"):
        self.path = db_path
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS businesses (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    place_url    TEXT    UNIQUE,
                    name         TEXT,
                    category     TEXT,
                    address      TEXT,
                    phone        TEXT,
                    website      TEXT,
                    rating       REAL,
                    review_count INTEGER,
                    latitude     REAL,
                    longitude    REAL,
                    keyword      TEXT,
                    location     TEXT,
                    city         TEXT,
                    state        TEXT,
                    country      TEXT,
                    scraped_at   TEXT,
                    reviews      TEXT
                )
            """)
            # Migrate older schemas that are missing columns
            existing = {row[1] for row in conn.execute("PRAGMA table_info(businesses)")}
            for col in ("city", "state", "country", "reviews"):
                if col not in existing:
                    conn.execute(f"ALTER TABLE businesses ADD COLUMN {col} TEXT")

    def upsert(self, data: dict):
        values = [data.get(c) for c in self.COLUMNS]
        placeholders = ", ".join("?" * len(self.COLUMNS))
        updates = ", ".join(
            f"{c} = excluded.{c}" for c in self.COLUMNS if c != "place_url"
        )
        with self._conn() as conn:
            conn.execute(
                f"""
                INSERT INTO businesses ({", ".join(self.COLUMNS)})
                VALUES ({placeholders})
                ON CONFLICT(place_url) DO UPDATE SET {updates}
                """,
                values,
            )

    def get_scraped_urls(self, keyword: str, location: str) -> set:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT place_url FROM businesses
                   WHERE keyword = ? AND location = ? AND place_url IS NOT NULL""",
                (keyword, location),
            ).fetchall()
            return {row[0] for row in rows}

    def count(self, keyword: str = None, location: str = None) -> int:
        with self._conn() as conn:
            if keyword and location:
                row = conn.execute(
                    "SELECT COUNT(*) FROM businesses WHERE keyword = ? AND location = ?",
                    (keyword, location),
                ).fetchone()
            elif keyword:
                row = conn.execute(
                    "SELECT COUNT(*) FROM businesses WHERE keyword = ?",
                    (keyword,),
                ).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) FROM businesses").fetchone()
            return row[0] if row else 0
