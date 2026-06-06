"""
Async worker pool: N browser contexts, shared asyncio work queue, one location per worker slot.
All workers write to the same SQLite DB and CSV file concurrently.
"""

import asyncio
import csv
import threading
from pathlib import Path
from typing import Callable, List, Optional

from playwright.async_api import async_playwright

from csv_writer import CsvWriter
from db import Database
from scraper import GoogleMapsScraper
from stealth import random_user_agent, random_viewport, apply_stealth


def parse_locations_csv(filepath: str) -> list:
    """Parse a CSV with City, State, Country columns into location dicts."""
    locations = []
    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            data = {k.lower().strip(): (v or "").strip() for k, v in row.items()}
            city = data.get("city", "")
            state = data.get("state", data.get("province", ""))
            country = data.get("country", "")
            if not city:
                continue
            parts = [p for p in [city, state] if p]
            if country and country.upper() not in ("US", "USA", "UNITED STATES", "UNITED STATES OF AMERICA"):
                parts.append(country)
            locations.append({
                "location": ", ".join(parts),
                "city": city,
                "state": state,
                "country": country,
            })
    return locations


class ScraperPool:
    def __init__(
        self,
        keyword: str,
        locations: List[dict],
        n_workers: int,
        depth: int,
        db: Database,
        csv_writer: CsvWriter,
        log_fn: Callable[[str], None],
        worker_status_fn: Callable[[int, dict], None],
        overall_progress_fn: Callable[[int, int, int], None],
        stop_event: threading.Event,
        headless: bool = False,
        record_tick_fn: Optional[Callable[[], None]] = None,
    ):
        self.keyword = keyword
        self.locations = locations
        self.n_workers = min(n_workers, len(locations))
        self.depth = depth
        self.db = db
        self.csv_writer = csv_writer
        self._log = log_fn
        self._worker_status = worker_status_fn
        self._overall_progress = overall_progress_fn
        self._record_tick = record_tick_fn
        self._stop_event = stop_event
        self.headless = headless
        self._completed_locations = 0

    async def run(self):
        self._lock = asyncio.Lock()
        work_queue: asyncio.Queue = asyncio.Queue()
        for loc in self.locations:
            await work_queue.put(loc)

        async with async_playwright() as p:
            tasks = [
                asyncio.create_task(self._worker(p, wid, work_queue))
                for wid in range(self.n_workers)
            ]
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _worker(self, p, worker_id: int, work_queue: asyncio.Queue):
        profile_dir = Path(f"browser_profile_{worker_id}").resolve()
        profile_dir.mkdir(exist_ok=True)

        # Stagger launches so all workers don't hit Google simultaneously
        if worker_id > 0:
            await asyncio.sleep(worker_id * 8)

        self._worker_status(worker_id, {"state": "starting", "location": "", "current": 0, "total": 0})

        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=self.headless,
            viewport=random_viewport(),
            args=["--disable-blink-features=AutomationControlled"],
            user_agent=random_user_agent(),
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await apply_stealth(page)

        try:
            while not self._stop_event.is_set():
                try:
                    loc_data = work_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

                location = loc_data["location"]
                self._log(f"[W{worker_id + 1}] Starting: {self.keyword} in {location}")
                self._worker_status(worker_id, {
                    "state": "running", "location": location,
                    "current": 0, "total": self.depth,
                })

                def make_log(wid):
                    def fn(msg):
                        self._log(f"[W{wid + 1}] {msg}")
                    return fn

                def make_progress(wid, loc):
                    def fn(current, total):
                        self._worker_status(wid, {
                            "state": "running", "location": loc,
                            "current": current, "total": total,
                        })
                    return fn

                def make_record(loc_ref):
                    def fn(data):
                        data.update({
                            "city": loc_ref.get("city", ""),
                            "state": loc_ref.get("state", ""),
                            "country": loc_ref.get("country", ""),
                        })
                        self.csv_writer.append(data)
                        if self._record_tick:
                            self._record_tick()
                    return fn

                scraper = GoogleMapsScraper(
                    keyword=self.keyword,
                    location=location,
                    depth=self.depth,
                    db=self.db,
                    log_fn=make_log(worker_id),
                    progress_fn=make_progress(worker_id, location),
                    record_fn=make_record(loc_data),
                    stop_event=self._stop_event,
                )
                await scraper._run(page)

                async with self._lock:
                    self._completed_locations += 1
                    total_records = self.db.count(self.keyword)
                    self._overall_progress(
                        self._completed_locations,
                        len(self.locations),
                        total_records,
                    )

                self._log(f"[W{worker_id + 1}] Finished: {location}")
                self._worker_status(worker_id, {
                    "state": "done", "location": location,
                    "current": self.depth, "total": self.depth,
                })

        except Exception as e:
            self._log(f"[W{worker_id + 1}] Error: {e}")
        finally:
            self._worker_status(worker_id, {"state": "idle", "location": "", "current": 0, "total": 0})
            await ctx.close()
