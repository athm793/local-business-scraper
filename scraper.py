#!/usr/bin/env python3
"""
Google Maps Local Business Scraper
No proxies required. Persistent browser profile. Auto-resuming.

CLI usage:
    py scraper.py --keyword "dentist" --location "Miami, FL" --depth 100

Imported by pool.py — pass log_fn, progress_fn, record_fn, stop_event for full integration.
"""

import asyncio
import argparse
import random
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

from db import Database


async def delay(min_s: float = 2.0, max_s: float = 5.0):
    await asyncio.sleep(random.uniform(min_s, max_s))


class GoogleMapsScraper:
    FEED_SELECTOR = 'div[role="feed"]'
    RESULT_LINK_SELECTOR = 'div[role="feed"] a[href*="/maps/place/"]'
    END_OF_RESULTS_MARKERS = [
        "you've reached the end of the list",
        "end of results",
        "no more results",
    ]

    def __init__(
        self,
        keyword: str,
        location: str,
        depth: int,
        db: Database,
        headless: bool = False,
        log_fn: Optional[Callable[[str], None]] = None,
        progress_fn: Optional[Callable[[int, int], None]] = None,
        record_fn: Optional[Callable[[dict], None]] = None,
        stop_event: Optional[threading.Event] = None,
        extra_data: Optional[dict] = None,
    ):
        self.keyword = keyword
        self.location = location
        self.depth = depth
        self.db = db
        self.headless = headless
        self._log_fn = log_fn or print
        self._progress_fn = progress_fn
        self._record_fn = record_fn
        self._stop_event = stop_event
        self._extra_data = extra_data or {}

    def _log(self, msg: str):
        self._log_fn(msg)

    def _progress(self, current: int, total: int):
        if self._progress_fn:
            self._progress_fn(current, total)

    def _stopped(self) -> bool:
        return self._stop_event is not None and self._stop_event.is_set()

    async def run(self):
        """Standalone entry point — creates its own browser context."""
        profile_dir = Path("browser_profile").resolve()
        profile_dir.mkdir(exist_ok=True)
        async with async_playwright() as p:
            ctx = await p.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=self.headless,
                viewport={"width": 1280, "height": 900},
                args=["--disable-blink-features=AutomationControlled"],
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            )
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            try:
                await self._run(page)
            finally:
                await ctx.close()

    async def _run(self, page):
        """Core scrape logic — accepts an existing page from the pool."""
        search_query = f"{self.keyword} {self.location}"
        search_url = (
            "https://www.google.com/maps/search/"
            + search_query.replace(" ", "+")
        )

        self._log(f"Searching: {self.keyword} in {self.location}")

        await page.goto(search_url, wait_until="networkidle", timeout=30000)
        await delay(3, 6)
        await self._dismiss_dialogs(page)

        self._log("Collecting result URLs...")
        result_urls = await self._collect_urls(page)
        self._log(f"Found {len(result_urls)} results")

        if not result_urls:
            self._log("No results found.")
            return

        already_done = self.db.get_scraped_urls(self.keyword, self.location)
        pending = [u for u in result_urls if u not in already_done]
        scraped_count = len(already_done)

        if already_done:
            self._log(f"Resuming — skipping {len(already_done)} already scraped")

        self._progress(scraped_count, self.depth)

        for url in pending:
            if self._stopped() or scraped_count >= self.depth:
                break

            data = await self._scrape_business(page, url)
            if data:
                data.update(self._extra_data)
                data["keyword"] = self.keyword
                data["location"] = self.location
                self.db.upsert(data)
                if self._record_fn:
                    self._record_fn(data)
                scraped_count += 1
                self._log(f"[{scraped_count}/{self.depth}] {data.get('name', 'Unknown')}")
                self._progress(scraped_count, self.depth)

            await delay(3, 8)

        self._log(f"Done: {scraped_count} records for {self.location}")

    async def _dismiss_dialogs(self, page):
        for selector in [
            'button[aria-label*="Accept"]',
            'button[aria-label*="Agree"]',
            'button:has-text("Accept all")',
            'button:has-text("I agree")',
        ]:
            try:
                btn = await page.query_selector(selector)
                if btn and await btn.is_visible():
                    await btn.click()
                    await delay(1, 2)
                    break
            except Exception:
                pass

    async def _collect_urls(self, page) -> list:
        seen: set = set()
        urls: list = []

        while len(urls) < self.depth:
            if self._stopped():
                break
            links = await page.query_selector_all(self.RESULT_LINK_SELECTOR)
            for link in links:
                href = await link.get_attribute("href")
                if not href or href in seen:
                    continue
                seen.add(href)
                full = (
                    f"https://www.google.com{href}" if href.startswith("/") else href
                )
                urls.append(full.split("?")[0])

            if len(urls) >= self.depth:
                break

            scrolled, end_reached = await self._scroll_feed(page)
            if end_reached or not scrolled:
                break
            await delay(1.5, 3)

        return urls[: self.depth]

    async def _scroll_feed(self, page) -> tuple:
        """Returns (scrolled, end_reached)."""
        try:
            feed = await page.query_selector(self.FEED_SELECTOR)
            if not feed:
                return False, False
            before = await page.evaluate("el => el.scrollTop", feed)
            await page.evaluate("el => el.scrollBy(0, 3000)", feed)
            await delay(1.5, 2.5)
            after = await page.evaluate("el => el.scrollTop", feed)
            page_text = (await page.inner_text("body")).lower()
            for marker in self.END_OF_RESULTS_MARKERS:
                if marker in page_text:
                    return True, True
            return after > before, False
        except Exception:
            return False, False

    async def _scrape_business(self, page, url: str) -> dict | None:
        for attempt in range(2):
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                await delay(2, 4)
                try:
                    await page.wait_for_selector("h1", timeout=8000)
                except PlaywrightTimeout:
                    if attempt == 0:
                        await delay(3, 5)
                        continue
                    return None
                return await self._extract(page)
            except Exception as e:
                if attempt == 0:
                    await delay(3, 6)
                    continue
                self._log(f"[SKIP] {e}")
                return None
        return None

    async def _extract(self, page) -> dict | None:
        data: dict = {}

        try:
            data["name"] = (await page.inner_text("h1")).strip()
        except Exception:
            return None
        if not data["name"]:
            return None

        m = re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", page.url)
        if m:
            data["latitude"] = float(m.group(1))
            data["longitude"] = float(m.group(2))

        data["place_url"] = page.url
        data["scraped_at"] = datetime.now().isoformat()

        try:
            el = await page.query_selector('[aria-label*="star"]')
            if el:
                label = await el.get_attribute("aria-label") or ""
                m2 = re.search(r"(\d+\.?\d*)\s+star", label, re.IGNORECASE)
                if m2:
                    data["rating"] = float(m2.group(1))
        except Exception:
            pass

        try:
            el = await page.query_selector('[aria-label*="review"]')
            if el:
                label = await el.get_attribute("aria-label") or ""
                m3 = re.search(r"([\d,]+)\s+review", label, re.IGNORECASE)
                if m3:
                    data["review_count"] = int(m3.group(1).replace(",", ""))
        except Exception:
            pass

        try:
            el = await page.query_selector("button.DkEaL")
            if el:
                data["category"] = (await el.inner_text()).strip()
        except Exception:
            pass

        try:
            el = await page.query_selector('[data-item-id="address"]')
            if not el:
                el = await page.query_selector('[aria-label*="ddress"]')
            if el:
                data["address"] = (await el.inner_text()).strip()
        except Exception:
            pass

        try:
            el = await page.query_selector('[data-item-id^="phone"]')
            if not el:
                el = await page.query_selector('[aria-label*="hone"]')
            if el:
                data["phone"] = (await el.inner_text()).strip()
        except Exception:
            pass

        try:
            el = await page.query_selector(
                'a[data-item-id="authority"], [data-item-id="authority"] a'
            )
            if el:
                href = await el.get_attribute("href")
                data["website"] = href or (await el.inner_text()).strip()
        except Exception:
            pass

        return data


def main():
    parser = argparse.ArgumentParser(description="Google Maps Local Business Scraper")
    parser.add_argument("--keyword", required=True)
    parser.add_argument("--location", required=True)
    parser.add_argument("--depth", type=int, default=100)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--db", default="businesses.db")
    args = parser.parse_args()

    db = Database(args.db)
    scraper = GoogleMapsScraper(
        keyword=args.keyword,
        location=args.location,
        depth=args.depth,
        db=db,
        headless=args.headless,
    )
    asyncio.run(scraper.run())


if __name__ == "__main__":
    main()
