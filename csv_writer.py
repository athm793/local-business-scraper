"""Thread-safe append-only CSV writer. One file per scrape job, appended record by record."""

import csv
import re
import threading
from datetime import datetime
from pathlib import Path

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
        self._init_file()

    def _init_file(self):
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(self.filepath, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=COLUMNS).writeheader()

    def append(self, data: dict):
        row = {col: data.get(col, "") for col in COLUMNS}
        with self._lock:
            with open(self.filepath, "a", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=COLUMNS).writerow(row)

    @staticmethod
    def make_path(keyword: str) -> str:
        slug = re.sub(r"[^\w]+", "_", keyword).strip("_").lower()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return str(Path("outputs") / f"{slug}_{ts}.csv")
