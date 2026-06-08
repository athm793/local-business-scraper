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
import json
import random
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

from db import Database
from stealth import apply_stealth, human_click, human_scroll, maybe_idle, is_blocked, handle_block


_SOCIAL_DOMAINS = frozenset({
    "facebook.com", "fb.com", "instagram.com", "twitter.com", "x.com",
    "linkedin.com", "tiktok.com", "youtube.com", "pinterest.com",
    "snapchat.com", "reddit.com", "whatsapp.com", "telegram.org", "vk.com",
})
_DIRECTORY_DOMAINS = frozenset({
    "yelp.com", "yellowpages.com", "tripadvisor.com", "angi.com",
    "angieslist.com", "bbb.org", "houzz.com", "thumbtack.com",
    "homeadvisor.com", "manta.com", "mapquest.com", "whitepages.com",
    "superpages.com", "foursquare.com", "merchantcircle.com",
    "expertise.com", "porch.com", "bark.com", "citysearch.com",
    "local.com", "chamberofcommerce.com",
})


async def delay(min_s: float = 2.0, max_s: float = 5.0):
    mean = (min_s + max_s) / 2
    std  = (max_s - min_s) / 6
    t = random.gauss(mean, std)
    await asyncio.sleep(max(min_s, min(max_s, t)))


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
        review_depth: int = 0,
        filters: Optional[dict] = None,
        scrape_hours: bool = False,
        scrape_schedule: bool = False,
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
        self.review_depth = review_depth
        self._filters = filters or {}
        self.scrape_hours = scrape_hours
        self.scrape_schedule = scrape_schedule

    def _log(self, msg: str):
        self._log_fn(msg)

    def _progress(self, current: int, total: int):
        if self._progress_fn:
            self._progress_fn(current, total)

    def _stopped(self) -> bool:
        return self._stop_event is not None and self._stop_event.is_set()

    async def _stoppable_delay(self, min_s: float, max_s: float):
        """Like delay() but checks stop_event every 0.5 s so Stop responds quickly."""
        mean = (min_s + max_s) / 2
        std  = (max_s - min_s) / 6
        t = max(min_s, min(max_s, random.gauss(mean, std)))
        elapsed = 0.0
        while elapsed < t:
            if self._stopped():
                return
            chunk = min(0.5, t - elapsed)
            await asyncio.sleep(chunk)
            elapsed += chunk

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
            await apply_stealth(page)
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

        await page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
        # Google Maps never reaches networkidle — wait for the results feed instead
        try:
            await page.wait_for_selector('div[role="feed"]', timeout=20000)
        except Exception:
            pass

        if await is_blocked(page):
            if not await handle_block(page, self._log, self.location, self._stop_event):
                return
            await page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
            try:
                await page.wait_for_selector('div[role="feed"]', timeout=20000)
            except Exception:
                pass

        await delay(2, 4)
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

            if not self._stopped():
                await maybe_idle()
                await self._stoppable_delay(3, 8)

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
                if not href:
                    continue
                full = (
                    f"https://www.google.com{href}" if href.startswith("/") else href
                )
                clean = full.split("?")[0]
                if clean in seen:
                    continue
                seen.add(clean)
                urls.append(clean)

            if len(urls) >= self.depth:
                break

            scrolled, end_reached = await self._scroll_feed(page)
            if end_reached or not scrolled:
                break
            await self._stoppable_delay(1.5, 3)

        return urls[: self.depth]

    async def _scroll_feed(self, page) -> tuple:
        """Returns (scrolled, end_reached)."""
        try:
            feed = await page.query_selector(self.FEED_SELECTOR)
            if not feed:
                return False, False
            before = await page.evaluate("el => el.scrollTop", feed)
            await human_scroll(page, feed, 3000)
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
                if await is_blocked(page):
                    if not await handle_block(page, self._log, url, self._stop_event):
                        return None
                    await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                    await delay(2, 4)
                try:
                    await page.wait_for_selector("h1", timeout=8000)
                except PlaywrightTimeout:
                    if attempt == 0:
                        await delay(3, 5)
                        continue
                    return None
                data = await self._extract(page)
                if data:
                    if not self._passes_filters(data):
                        self._log(f"[FILTER] Skipped: {data.get('name', '')}")
                        return None
                    if self.scrape_schedule:
                        data["schedule"] = await self._scrape_schedule(page)
                    if self.review_depth > 0:
                        self._log(f"Scraping up to {self.review_depth} reviews...")
                        reviews = await self._scrape_reviews(page, self.review_depth)
                        data["reviews"] = json.dumps(reviews, ensure_ascii=False)
                return data
            except Exception as e:
                if attempt == 0:
                    await delay(3, 6)
                    continue
                self._log(f"[SKIP] {e}")
                return None
        return None

    async def _scrape_reviews(self, page, max_reviews: int) -> list:
        """Return up to max_reviews recent reviews from the current business page."""
        reviews = []
        try:
            # ── Open Reviews tab ──────────────────────────────────────────────
            # Try every element type that could be a tab — Google uses button,
            # a, and div variants depending on the business page layout.
            tab_clicked = False
            for tab_sel in ('button[role="tab"]', 'a[role="tab"]', '[role="tab"]'):
                tabs = await page.query_selector_all(tab_sel)
                for tab in tabs:
                    try:
                        text = (await tab.inner_text()).strip().lower()
                        if "review" in text and len(text) < 40:
                            await human_click(page, tab)
                            tab_clicked = True
                            break
                    except Exception:
                        pass
                if tab_clicked:
                    break

            # Wait for review cards to appear in the DOM, then scroll past any
            # "Browse and Book" or promotional widget that sits above the reviews
            # feed. These widgets live inside the same scrollable container and
            # cause lazy-loading of review items to never trigger when we scroll
            # the wrong container or don't scroll at all.
            if tab_clicked:
                try:
                    await page.wait_for_selector(
                        '[data-review-id], .jftiEf', timeout=7000
                    )
                except Exception:
                    await asyncio.sleep(2.5)

                # Initial scroll to push past any promotional widget above reviews
                try:
                    for ps in ('div.m6QErb[aria-label]', 'div.m6QErb', '[role="feed"]'):
                        init_pane = await page.query_selector(ps)
                        if init_pane:
                            await page.evaluate("el => el.scrollBy(0, 350)", init_pane)
                            await asyncio.sleep(0.9)
                            break
                    else:
                        await page.evaluate("window.scrollBy(0, 350)")
                        await asyncio.sleep(0.9)
                except Exception:
                    pass
            else:
                await asyncio.sleep(1.0)

            # ── Sort by Newest (best-effort) ──────────────────────────────────
            sort_btn = await page.query_selector('button[aria-label*="Sort"]')
            if sort_btn and await sort_btn.is_visible():
                await human_click(page, sort_btn)
                await asyncio.sleep(0.7)
                for item_sel in ('[role="menuitemradio"]', '[role="option"]', 'li'):
                    menu_items = await page.query_selector_all(item_sel)
                    for mi in menu_items:
                        try:
                            text = (await mi.inner_text()).strip().lower()
                            if text in ("newest", "most recent"):
                                await human_click(page, mi)
                                await asyncio.sleep(1.5)
                                break
                        except Exception:
                            pass
                    else:
                        continue
                    break

            # ── Locate the scrollable review pane ─────────────────────────────
            # Prefer a pane that already contains review cards — the booking widget
            # can have its own div.m6QErb that matches the selector but holds booking
            # slots, not reviews. Selecting it means we scroll the wrong container.
            pane = None
            for ps in ('div.m6QErb[aria-label]', 'div[role="feed"]', 'div.m6QErb'):
                candidates = await page.query_selector_all(ps)
                for c in candidates:
                    # Prefer a container that already has review items in it
                    if await c.query_selector('[data-review-id], .jftiEf'):
                        pane = c
                        break
                if pane:
                    break
            if not pane:
                # Fallback: take first matching container even without confirmed items
                for ps in ('div.m6QErb[aria-label]', 'div[role="feed"]', 'div.m6QErb'):
                    pane = await page.query_selector(ps)
                    if pane:
                        break

            # ── Scroll and collect ────────────────────────────────────────────
            seen: set = set()
            stalls = 0

            while len(reviews) < max_reviews and stalls < 6:
                # Expand truncated review text
                for more_btn in await page.query_selector_all('button[aria-label="See more"]'):
                    try:
                        if await more_btn.is_visible():
                            await more_btn.click()
                    except Exception:
                        pass

                # Try multiple card selectors — Google occasionally drops data-review-id
                items = []
                for rs in ('[data-review-id]', '.jftiEf', '[jslog*="review"]'):
                    items = await page.query_selector_all(rs)
                    if items:
                        break

                added = 0
                for item in items:
                    if len(reviews) >= max_reviews:
                        break

                    # Build a stable unique key for deduplication using whatever
                    # attribute is available — the previous bug was that data-review-id
                    # returns None on some pages, and None in seen == False, so every
                    # item looked new but then None was added to seen, blocking all
                    # subsequent items on the next scroll pass.
                    rid = await item.get_attribute("data-review-id")
                    if not rid:
                        rid = await item.get_attribute("data-key")
                    if not rid:
                        rid = await item.get_attribute("jslog")
                    if not rid:
                        try:
                            rid = await page.evaluate(
                                "el => el.innerHTML.slice(0,120)", item
                            )
                        except Exception:
                            rid = None
                    if rid is None:
                        continue   # truly can't identify this item — skip
                    if rid in seen:
                        continue
                    seen.add(rid)
                    added += 1

                    r: dict = {}

                    # Reviewer name
                    for ns in ('[class*="d4r55"]', 'button[jsaction*="reviewAuthor"]',
                               'a[href*="maps/contrib"]', '[class*="X43Kjb"]'):
                        try:
                            el = await item.query_selector(ns)
                            if el:
                                name = (await el.inner_text()).strip()
                                if name and len(name) < 80:
                                    r["reviewer"] = name
                                    break
                        except Exception:
                            pass

                    # Stars
                    for star_sel in ('[aria-label*="star"]', '[aria-label*="Star"]',
                                     '[class*="kvMYJc"]'):
                        try:
                            el = await item.query_selector(star_sel)
                            if el:
                                lbl = await el.get_attribute("aria-label") or ""
                                m = re.search(r"(\d+)\s+star", lbl, re.IGNORECASE)
                                if m:
                                    r["stars"] = int(m.group(1))
                                    break
                        except Exception:
                            pass

                    # Date — short span with relative time words
                    try:
                        for span in await item.query_selector_all("span"):
                            t = (await span.inner_text()).strip()
                            if 1 < len(t) < 40 and any(
                                w in t.lower() for w in
                                ("ago", "week", "month", "year", "day", "hour")
                            ):
                                r["date"] = t
                                break
                    except Exception:
                        pass

                    # Review text
                    for ts in ('[class*="wiI7pd"]', 'span[jscontroller]',
                               '[class*="MyEned"]'):
                        try:
                            el = await item.query_selector(ts)
                            if el:
                                t = (await el.inner_text()).strip()
                                if t:
                                    r["text"] = t
                                    break
                        except Exception:
                            pass
                    if "text" not in r:
                        best = ""
                        try:
                            for span in await item.query_selector_all("span"):
                                t = (await span.inner_text()).strip()
                                if len(t) > len(best) and len(t) > 20:
                                    best = t
                            if best:
                                r["text"] = best
                        except Exception:
                            pass

                    if r:
                        reviews.append(r)

                stalls = 0 if added else stalls + 1
                if len(reviews) < max_reviews:
                    if pane:
                        await human_scroll(page, pane, 2000)
                    else:
                        try:
                            await page.evaluate("window.scrollBy(0, 2000)")
                        except Exception:
                            pass
                    await asyncio.sleep(random.gauss(1.3, 0.3))

        except Exception as e:
            self._log(f"[REVIEWS] Error: {e}")

        if not reviews:
            self._log("[REVIEWS] None found — tab click or selectors may need updating")

        return reviews[:max_reviews]

    def _passes_filters(self, data: dict) -> bool:
        """Return True if data satisfies all active filters."""
        f = self._filters
        if not f:
            return True
        rc  = data.get("review_count") or 0
        rat = data.get("rating") or 0.0
        if (v := f.get("min_reviews")) is not None and rc < v:
            return False
        if (v := f.get("max_reviews")) is not None and rc > v:
            return False
        if (v := f.get("min_rating")) is not None and rat < v:
            return False
        if (v := f.get("max_rating")) is not None and rat > v:
            return False
        if (v := f.get("require_website")) is not None:
            has = bool((data.get("website") or "").strip())
            if v and not has:
                return False
            if not v and has:
                return False
        if (v := f.get("require_phone")) is not None:
            has = bool((data.get("phone") or "").strip())
            if v and not has:
                return False
            if not v and has:
                return False
        return True

    @staticmethod
    def _classify_website(url: str) -> str:
        """Return Legit Website / Social Media Page / Yellow Pages Link / No Website."""
        if not url or not url.strip():
            return "No Website"
        try:
            domain = urlparse(url).netloc.lower()
            if domain.startswith("www."):
                domain = domain[4:]
            if any(s in domain for s in _SOCIAL_DOMAINS):
                return "Social Media Page"
            if any(d in domain for d in _DIRECTORY_DOMAINS):
                return "Yellow Pages Link"
            return "Legit Website"
        except Exception:
            return "Legit Website"

    async def _scrape_schedule(self, page) -> str:
        """Return the full weekly schedule for the current business page.

        Two layout types exist on Google Maps:
        - Inline dropdown: the schedule is a collapsible div inside the business
          card. Clicking [data-item-id="oh"] expands it in-place. After expanding,
          inner_text() of that same element contains the full schedule.
        - Separate section: clicking opens a dedicated Hours page with a table
          (table.eK4R0e or similar selectors).
        We handle both.
        """
        _DAYS = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday',
                 'Saturday', 'Sunday')

        def _has_days(t: str) -> bool:
            return sum(1 for d in _DAYS if d in t) >= 2

        try:
            # ── Check if already expanded / aria-label has full schedule ──────
            for sel in ('[data-item-id="oh"]', '[aria-label*="hour" i]'):
                el = await page.query_selector(sel)
                if not el:
                    continue
                label = await el.get_attribute('aria-label') or ''
                if _has_days(label):
                    return self._clean_text(label)
                # inner_text covers the "inline dropdown already open" case
                text = self._clean_text(await el.inner_text())
                if _has_days(text):
                    return text

            # ── Click to expand ───────────────────────────────────────────────
            for sel in ('[data-item-id="oh"] button', '[data-item-id="oh"]',
                        'button[jsaction*="openhours"]', '[jsaction*="openhours"]'):
                btn = await page.query_selector(sel)
                if btn and await btn.is_visible():
                    await human_click(page, btn)
                    await asyncio.sleep(1.5)
                    break

            # ── Re-read [data-item-id="oh"] — covers the inline dropdown case ─
            # After clicking, the element expands in-place and its inner_text()
            # now contains the full day-by-day schedule (e.g. Saraland Smiles).
            el = await page.query_selector('[data-item-id="oh"]')
            if el:
                text = self._clean_text(await el.inner_text())
                if _has_days(text):
                    return text

            # ── Known table/div selectors — covers the separate-section case ──
            for sel in ('table.eK4R0e', 'div.eK4R0e', 'div.o0Svhf',
                        'div.t39EBf', '[data-day-of-week]'):
                el = await page.query_selector(sel)
                if el:
                    text = self._clean_text(await el.inner_text())
                    if _has_days(text):
                        return text

            # ── aria-expanded container fallback ──────────────────────────────
            exp = await page.query_selector('[aria-expanded="true"]')
            if exp:
                text = self._clean_text(await exp.inner_text())
                if _has_days(text) and len(text) > 20:
                    return text

            # ── Broad DOM scan ────────────────────────────────────────────────
            for div in await page.query_selector_all('div, section, table'):
                try:
                    text = (await div.inner_text()).strip()
                    if 30 < len(text) < 600 and _has_days(text):
                        return self._clean_text(text)
                except Exception:
                    pass

        except Exception:
            pass
        return ""

    @staticmethod
    def _clean_text(text: str) -> str:
        """Strip icon glyphs and normalise Unicode spaces from Google Maps inner_text().

        Google Maps prepends a non-ASCII icon glyph + newline before address/phone
        text, and uses U+202F (NARROW NO-BREAK SPACE) inside time strings.
        Discard any line that has no ASCII letter or digit; normalise Unicode
        spaces to regular ASCII spaces so CSVs open correctly in Excel.
        """
        # Normalise Unicode spaces → regular space
        text = text.replace(' ', ' ').replace(' ', ' ').replace(' ', ' ')
        # Strip trailing "See more hours" boilerplate
        text = re.sub(r'\s*See more hours\s*$', '', text, flags=re.IGNORECASE)
        lines = text.strip().splitlines()
        clean = [
            l.strip() for l in lines
            if l.strip() and any(c.isascii() and (c.isalpha() or c.isdigit()) for c in l)
        ]
        return "\n".join(clean) if clean else text.strip()

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

        # ── Rating + review count ─────────────────────────────────────────────
        # Strategy 1: specific rating-number spans (inner text = "4.5")
        try:
            for sel in ("span.MW4etd", "span.ceNzKf", "span.Aq14fc"):
                el = await page.query_selector(sel)
                if el:
                    t = (await el.inner_text()).strip()
                    if re.match(r"^\d+\.\d$", t):
                        data["rating"] = float(t)
                        break
        except Exception:
            pass

        # Strategy 2: aria-label on star/review elements
        try:
            for sel in ('[aria-label*="star"]', '[aria-label*="Star"]',
                        '[aria-label*="review"]'):
                el = await page.query_selector(sel)
                if not el:
                    continue
                label = await el.get_attribute("aria-label") or ""
                if "rating" not in data:
                    m_r = re.search(r"(\d+\.?\d*)\s+stars?", label, re.IGNORECASE)
                    if not m_r:
                        m_r = re.search(r"rated\s+(\d+\.?\d*)", label, re.IGNORECASE)
                    if m_r:
                        data["rating"] = float(m_r.group(1))
                if "review_count" not in data:
                    m_c = re.search(r"([\d,]+)\s+reviews?", label, re.IGNORECASE)
                    if m_c:
                        data["review_count"] = int(m_c.group(1).replace(",", ""))
                if "rating" in data and "review_count" in data:
                    break
        except Exception:
            pass

        # Strategy 3: body-text fallback — "4.5(1,607 reviews)" is always visible
        try:
            if "rating" not in data or "review_count" not in data:
                body = (await page.inner_text("body"))[:6000]
                if "rating" not in data:
                    m_r = re.search(r"\b(\d\.\d)\s*[\(\n]([\d,]+)\s*review", body,
                                    re.IGNORECASE)
                    if m_r:
                        data["rating"] = float(m_r.group(1))
                        if "review_count" not in data:
                            data["review_count"] = int(m_r.group(2).replace(",", ""))
                if "review_count" not in data:
                    m_c = re.search(r"([\d,]+)\s+reviews?", body, re.IGNORECASE)
                    if m_c:
                        data["review_count"] = int(m_c.group(1).replace(",", ""))
        except Exception:
            pass

        # ── Category ─────────────────────────────────────────────────────────
        try:
            el = await page.query_selector("button.DkEaL")
            if el:
                data["category"] = (await el.inner_text()).strip()
        except Exception:
            pass

        # ── Address ───────────────────────────────────────────────────────────
        try:
            el = await page.query_selector('[data-item-id="address"]')
            if not el:
                el = await page.query_selector('[aria-label*="ddress"]')
            if el:
                data["address"] = self._clean_text(await el.inner_text())
        except Exception:
            pass

        # ── Phone ─────────────────────────────────────────────────────────────
        try:
            el = await page.query_selector('[data-item-id^="phone"]')
            if not el:
                el = await page.query_selector('[aria-label*="hone"]')
            if el:
                data["phone"] = self._clean_text(await el.inner_text())
        except Exception:
            pass

        # ── Website + classification ──────────────────────────────────────────
        try:
            el = await page.query_selector(
                'a[data-item-id="authority"], [data-item-id="authority"] a'
            )
            if el:
                href = await el.get_attribute("href")
                data["website"] = href or (await el.inner_text()).strip()
        except Exception:
            pass
        data["website_type"] = self._classify_website(data.get("website", ""))

        # ── Hours (current status) ────────────────────────────────────────────
        if self.scrape_hours or self.scrape_schedule:
            try:
                for sel in ('[data-item-id="oh"]', 'button[aria-label*="hour" i]',
                            '[jsaction*="openhours"]'):
                    el = await page.query_selector(sel)
                    if el:
                        text = self._clean_text(await el.inner_text())
                        if text:
                            data["hours"] = text
                            break
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
