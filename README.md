# Maps Scraper

A multi-worker Google Maps business scraper with a desktop GUI. No proxies, no paid APIs — drives real Chromium browser profiles with human-like behavior to collect business data at scale into CSV and SQLite.

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey)

## Features

- **Multi-worker scraping** — run up to 20 parallel browser workers, each with its own persistent profile and isolated location queue
- **No proxies required** — relies entirely on stealth/anti-detection techniques rather than IP rotation
- **Anti-detection layer**
  - Removes `navigator.webdriver` and fakes plugins / Chrome runtime / WebGL fingerprints
  - Human-like mouse movement (bezier curves, easing, micro-jitter)
  - Human-like scrolling with overshoot and easing
  - Gaussian-distributed delays instead of uniform random waits
  - Random idle/"reading" pauses
  - CAPTCHA / block detection with automatic cooldown and retry
  - Rotating user-agent and viewport per worker (Windows + macOS Chrome/Edge)
- **Resumable** — already-scraped places (by URL) are skipped on re-runs, per keyword + location
- **Live desktop GUI** (CustomTkinter, dark theme)
  - Per-worker status panel (idle / starting / running / done) with progress
  - Live scrollable log with color-coded message types
  - Running totals: records collected, scrape rate, elapsed time, ETA
  - Start/stop controls that finish the in-flight record cleanly
- **Bulk locations** — import a CSV of City/State/Country (with column-mapping dialog), or add locations manually as chips
- **Filters** — skip businesses that don't match your criteria before they're written:
  - Min/max review count
  - Min/max rating
  - Must / must not have a website
  - Must / must not have a phone number
- **Website classification** — each result is tagged as `Legit Website`, `Social Media Page`, `Yellow Pages Link`, or `No Website`
- **Optional extras** — opening hours, weekly schedule, and recent reviews (configurable depth)
- **Dual output**
  - Append-only CSV per run (UTF-8 with BOM, opens cleanly in Excel)
  - SQLite database (WAL mode) per keyword, for dedup and querying
- **Config persistence** — last-used keyword, depth, worker count, and review settings are saved to `scraper_config.json`

## Requirements

- Windows 10/11
- Python 3.10+
- [Playwright](https://playwright.dev/python/) (Chromium)
- [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter)

## Installation

```bash
git clone https://github.com/athm793/local-business-scraper.git
cd local-business-scraper

pip install -r requirements.txt
playwright install chromium
```

### Optional: desktop shortcut

```bash
py create_shortcut.py
```

Creates a "Maps Scraper" shortcut on your Desktop that launches the GUI with `pythonw` (no console window).

## Usage

### GUI (recommended)

```bash
py gui.py
```

1. Enter a search **keyword** (e.g. `"dentist"`).
2. Set **results per location** and **parallel workers**.
3. Optionally configure **filters** (review count, rating, website, phone) and **reviews/hours/schedule** options.
4. Add **locations** — type one manually, or upload a CSV (columns: `City`, `State`/`Province`, `Country`).
5. Click **Start**. Watch live per-worker status, totals, and the log panel.
6. Click **Copy path** or **Open folder** to grab the resulting CSV from `outputs/`.

### CLI (single location)

```bash
py scraper.py --keyword "dentist" --location "Miami, FL" --depth 100
```

## Output

### CSV (`outputs/<keyword>_<timestamp>.csv`)

One row per business with columns:

```
keyword, location, city, state, country,
name, category, address, phone, website, website_type,
rating, review_count, hours, schedule,
latitude, longitude, place_url, scraped_at, reviews
```

### SQLite (`<keyword>.db`)

A `businesses` table (WAL mode, `place_url` unique) used for dedup across runs — re-running the same keyword/location skips places already scraped.

## Project structure

```
gui.py            # App entry point / window assembly
gui_widgets.py     # Theme constants + reusable widgets (cards, chips, dialogs)
gui_config.py      # Load/save scraper_config.json
gui_locations.py   # Location CSV import + manual entry
gui_runner.py       # Start/stop, queue polling, live stats
pool.py            # Async worker pool — N browser contexts, shared queue
scraper.py         # Core Playwright scraping logic + CLI entry point
stealth.py         # Anti-detection: fingerprinting, mouse/scroll, block handling
db.py              # SQLite schema + upsert/dedup
csv_writer.py      # Thread-safe append-only CSV writer
create_shortcut.py # Windows desktop shortcut generator
```

## Notes

- Each worker launches a real, visible (non-headless) Chromium window with its own persistent profile under `browser_profile_<n>/`. Headless mode is intentionally disabled — it's far more detectable.
- Worker launches are staggered (8s apart) to avoid simultaneous requests from the same machine.
- This tool automates a public website. Use it responsibly and in accordance with Google's Terms of Service and applicable laws in your jurisdiction.
