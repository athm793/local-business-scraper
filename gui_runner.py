"""Run-loop mixin — start/stop, queue polling, tick timer, log, done."""

import asyncio
import queue
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from csv_writer import CsvWriter
from db import Database
from pool import ScraperPool
from gui_widgets import LOG_MAX_LINES, _fmt_time


class RunMixin:

    # ── Start / Stop ──────────────────────────────────────────────────────────

    def _start(self):
        keyword = self._kw_entry.get().strip()
        if not keyword:
            self._flash_error("Keyword is required.")
            return

        try:
            depth = int(self._depth_entry.get().strip() or "100")
        except ValueError:
            self._flash_error("Depth must be a whole number.")
            return
        if depth < 1:
            self._flash_error("Results per location must be at least 1.")
            return

        locations = self._locations if self._locations else []
        if not locations:
            single = self._manual_entry.get().strip()
            if not single:
                self._flash_error("Add at least one location or upload a CSV.")
                return
            locations = [{"location": single, "city": "", "state": "", "country": ""}]

        n_workers    = self._get_workers()
        review_depth = 0
        if self._reviews_var.get():
            try:
                review_depth = max(1, int(self._review_depth_entry.get().strip() or "10"))
            except ValueError:
                review_depth = 10

        filters: dict = {}

        def _int(entry):
            v = entry.get().strip()
            return int(v) if v else None

        def _flt(entry):
            v = entry.get().strip()
            return float(v) if v else None

        if (v := _int(self._filter_min_rev)) is not None:
            filters["min_reviews"] = max(0, v)
        if (v := _int(self._filter_max_rev)) is not None:
            filters["max_reviews"] = max(0, v)
        if (v := _flt(self._filter_min_rat)) is not None:
            filters["min_rating"] = max(0.0, min(5.0, v))
        if (v := _flt(self._filter_max_rat)) is not None:
            filters["max_rating"] = max(0.0, min(5.0, v))
        web_val = self._filter_website.get()
        if web_val == "Must have":
            filters["require_website"] = True
        elif web_val == "Must not have":
            filters["require_website"] = False
        ph_val = self._filter_phone.get()
        if ph_val == "Must have":
            filters["require_phone"] = True
        elif ph_val == "Must not have":
            filters["require_phone"] = False

        scrape_hours    = self._hours_var.get()
        scrape_schedule = self._schedule_var.get()

        self._save_config()

        self._csv_path   = CsvWriter.make_path(keyword)
        self._csv_writer = CsvWriter(self._csv_path)
        csv_name         = Path(self._csv_path).name
        self._csv_name_label.configure(text=csv_name)
        self._copy_btn.configure(state="normal")
        self._out_label.configure(text=f"Output: {csv_name}")

        self._stop_event.clear()
        self._total_records  = 0
        self._completed_loc  = 0
        self._total_loc      = len(locations)
        self._start_time     = time.time()
        self._is_running     = True

        self._start_btn.configure(state="disabled")
        self._stop_btn.configure(state="normal")
        self._workers_slider.configure(state="disabled")
        self._c_records.set("0")
        self._c_rate.set("—")
        self._c_elapsed.set("00:00:00")
        self._c_eta.set("—")
        self._loc_prog.configure(text=f"0 / {self._total_loc} locations")
        self._status.configure(text="Running")

        self._rebuild_worker_rows()
        self._log_line(
            f"── Starting: {keyword}  ·  {self._total_loc} locations  ·  {n_workers} workers ──",
            "success",
        )
        self._log_line(f"Output: {self._csv_path}", "muted")

        db = Database(CsvWriter.make_db_path(keyword))

        def log_fn(msg):       self._q.put(("log", msg))
        def worker_fn(wid, s): self._q.put(("worker", wid, s))
        def overall_fn(done, total, records):
            self._q.put(("overall", done, total, records))

        pool = ScraperPool(
            keyword=keyword, locations=locations, n_workers=n_workers,
            depth=depth, db=db, csv_writer=self._csv_writer,
            log_fn=log_fn, worker_status_fn=worker_fn,
            overall_progress_fn=overall_fn,
            record_tick_fn=lambda: self._q.put(("record_tick",)),
            stop_event=self._stop_event,
            review_depth=review_depth,
            filters=filters,
            scrape_hours=scrape_hours,
            scrape_schedule=scrape_schedule,
        )

        def run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(pool.run())
            except Exception as e:
                self._q.put(("log", f"[ERROR] {e}"))
            finally:
                loop.close()
                self._q.put(("done", None))

        threading.Thread(target=run, daemon=True).start()
        if self._tick_id is not None:
            self.after_cancel(self._tick_id)
            self._tick_id = None
        self._tick()

    def _stop(self):
        self._stop_event.set()
        self._log_line("Stop requested — workers finishing current record...", "warn")
        self._stop_btn.configure(state="disabled")
        self._status.configure(text="Stopping...")

    # ── Output ────────────────────────────────────────────────────────────────

    def _copy_csv_path(self):
        if self._csv_path:
            self.clipboard_clear()
            self.clipboard_append(self._csv_path)
            self._copy_btn.configure(text="Copied!")
            self.after(2000, lambda: self._copy_btn.configure(text="Copy path"))

    def _open_folder(self):
        folder = Path(__file__).parent / "outputs"
        folder.mkdir(exist_ok=True)
        subprocess.Popen(f'explorer "{folder.resolve()}"')

    def _flash_error(self, msg: str):
        self._log_line(f"[ERROR] {msg}", "error")
        self._status.configure(text=f"Error: {msg}")

    # ── Queue polling ─────────────────────────────────────────────────────────

    def _poll_q(self):
        try:
            while True:
                item = self._q.get_nowait()
                kind = item[0]
                if kind == "log":
                    self._log_line(item[1])
                elif kind == "record_tick":
                    self._total_records += 1
                    self._c_records.set(f"{self._total_records:,}")
                elif kind == "worker":
                    _, wid, status = item
                    if wid < len(self._worker_rows):
                        self._worker_rows[wid].update(status)
                elif kind == "overall":
                    _, done, total, records = item
                    self._completed_loc  = done
                    self._total_records  = records
                    self._c_records.set(f"{records:,}")
                    self._loc_prog.configure(text=f"{done} / {total} locations")
                elif kind == "done":
                    self._on_done()
        except queue.Empty:
            pass
        self.after(100, self._poll_q)

    def _tick(self):
        if not self._is_running or self._start_time is None:
            self._tick_id = None
            return
        elapsed = time.time() - self._start_time
        self._c_elapsed.set(_fmt_time(elapsed))

        if elapsed > 5 and self._total_records > 0:
            rate = self._total_records / elapsed * 60
            self._c_rate.set(f"{rate:.1f}")

        if self._completed_loc > 0 and self._total_loc > 0:
            time_per_loc = elapsed / self._completed_loc
            remaining    = self._total_loc - self._completed_loc
            self._c_eta.set(_fmt_time(remaining * time_per_loc))

        self._tick_id = self.after(1000, self._tick)

    # ── Log ───────────────────────────────────────────────────────────────────

    def _log_line(self, text: str, tag: str = ""):
        if not tag:
            if "[ERROR]" in text or "[SKIP]" in text:
                tag = "error"
            elif "[DONE]" in text or "finished" in text.lower() or "──" in text:
                tag = "success"
            elif text.startswith("[W") or "W1]" in text or "W2]" in text:
                tag = "worker"
            elif "[STOP]" in text or "[RESUME]" in text or "[BLOCK]" in text:
                tag = "warn"
            else:
                tag = "normal"

        ts = datetime.now().strftime("%H:%M:%S")
        self._log_box.configure(state="normal")
        if self._log_lines >= LOG_MAX_LINES:
            self._log_box.delete("1.0", "500.0")
            self._log_lines -= 500
        self._log_box.insert("end", f"{ts}  ", "ts")
        self._log_box.insert("end", text + "\n", tag)
        self._log_box.see("end")
        self._log_box.configure(state="disabled")
        self._log_lines += text.count('\n') + 1

    def _clear_log(self):
        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.configure(state="disabled")
        self._log_lines = 0

    # ── Done ──────────────────────────────────────────────────────────────────

    def _on_done(self):
        self._is_running  = False
        self._start_time  = None
        if self._csv_writer is not None:
            self._csv_writer.close()
            self._csv_writer = None
        self._start_btn.configure(state="normal")
        self._stop_btn.configure(state="disabled")
        self._workers_slider.configure(state="normal")
        self._status.configure(text="Done")
        self._log_line(
            f"── Complete: {self._total_records:,} records  ·  {self._completed_loc} locations ──",
            "success",
        )
        if self._csv_path:
            self._log_line(f"CSV: {self._csv_path}", "muted")
