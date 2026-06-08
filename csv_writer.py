"""Thread-safe append-only CSV writer. One file per scrape job, persistent file handle."""

import csv
import re
import threading
from datetime import datetime
from pathlib import Path

_BASE_DIR = Path(__file__).parent

COLUMNS = [
    "keyword", "location", "city", "state", "country",
    "name", "category", "address", "phone", "website", "website_type",
    "rating", "review_count", "hours", "schedule",
    "latitude", "longitude", "place_url", "scraped_at", "reviews",
]


class CsvWriter:
    def __init__(self, filepath: str):
        self.filepath = Path(filepath)
        self._lock = threading.Lock()
        self._fh = None
        self._writer = None
        self._init_file()

    def _init_file(self):
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        # utf-8-sig writes the UTF-8 BOM so Excel opens the file with the
        # correct encoding and doesn't mangle Unicode characters in hours/text.
        self._fh = open(self.filepath, "w", newline="", encoding="utf-8-sig")
        self._writer = csv.DictWriter(self._fh, fieldnames=COLUMNS)
        self._writer.writeheader()
        self._fh.flush()

    def append(self, data: dict):
        row = {col: data.get(col, "") for col in COLUMNS}
        with self._lock:
            self._writer.writerow(row)
            self._fh.flush()

    def close(self):
        with self._lock:
            if self._fh and not self._fh.closed:
                self._fh.close()

    @staticmethod
    def make_path(keyword: str) -> str:
        slug = re.sub(r"[^\w]+", "_", keyword).strip("_").lower()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return str(_BASE_DIR / "outputs" / f"{slug}_{ts}.csv")

    @staticmethod
    def make_db_path(keyword: str) -> str:
        """Return a keyword-scoped DB path so each keyword's data is isolated."""
        slug = re.sub(r"[^\w]+", "_", keyword).strip("_").lower()
        return str(_BASE_DIR / f"{slug}.db")
