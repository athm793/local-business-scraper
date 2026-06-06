#!/usr/bin/env python3
"""
Google Maps Business Scraper — GUI
Usage: py gui.py
"""

import asyncio
import queue
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from csv_writer import CsvWriter
from db import Database
from pool import ScraperPool, parse_locations_csv

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ── colours ──────────────────────────────────────────────────────────────────
C_GREEN       = "#2b9348"
C_GREEN_H     = "#208040"
C_RED         = "#9b2226"
C_RED_H       = "#7d1e22"
C_CARD_BG     = ("gray88", "gray18")
C_DIVIDER     = ("gray72", "gray30")
C_MUTED       = ("gray50", "gray55")
C_ACCENT      = "#4a9eca"
STATE_COLORS  = {"running": C_GREEN, "done": C_ACCENT, "idle": "gray55", "starting": "#e07b20"}

MAX_WORKERS   = 4
LOG_MAX_LINES = 2000


# ── helper widgets ─────────────────────────────────────────────────────────────

class StatCard(ctk.CTkFrame):
    def __init__(self, parent, title: str, init: str = "—", **kwargs):
        super().__init__(parent, corner_radius=10, fg_color=C_CARD_BG, **kwargs)
        self._val = ctk.CTkLabel(self, text=init, font=ctk.CTkFont(size=24, weight="bold"))
        self._val.pack(pady=(10, 0), padx=12)
        ctk.CTkLabel(self, text=title, font=ctk.CTkFont(size=11), text_color=C_MUTED).pack(pady=(2, 10))

    def set(self, value: str):
        self._val.configure(text=value)


class WorkerRow(ctk.CTkFrame):
    def __init__(self, parent, worker_id: int):
        super().__init__(parent, corner_radius=8, fg_color=C_CARD_BG)
        self.pack(fill="x", padx=8, pady=3)

        label = ctk.CTkLabel(self, text=f"W{worker_id + 1}",
                              font=ctk.CTkFont(size=12, weight="bold"), width=28)
        label.grid(row=0, column=0, padx=(10, 4), pady=8)

        self._loc = ctk.CTkLabel(self, text="Idle", font=ctk.CTkFont(size=12),
                                  anchor="w", width=220)
        self._loc.grid(row=0, column=1, padx=(0, 8), pady=8, sticky="w")

        self._bar = ctk.CTkProgressBar(self, width=170)
        self._bar.grid(row=0, column=2, padx=(0, 8), pady=8)
        self._bar.set(0)

        self._count = ctk.CTkLabel(self, text="", font=ctk.CTkFont(size=11),
                                    text_color=C_MUTED, width=70)
        self._count.grid(row=0, column=3, padx=(0, 8), pady=8)

        self._status = ctk.CTkLabel(self, text="Idle", font=ctk.CTkFont(size=11),
                                     text_color="gray55", width=68)
        self._status.grid(row=0, column=4, padx=(0, 10), pady=8)

    def update(self, s: dict):
        state   = s.get("state", "idle")
        loc     = s.get("location", "")
        current = s.get("current", 0)
        total   = s.get("total", 0)

        self._loc.configure(text=loc if loc else "Idle")
        pct = current / total if total else 0
        self._bar.set(pct)
        self._count.configure(text=f"{current}/{total}" if total else "")
        self._status.configure(text=state.title(), text_color=STATE_COLORS.get(state, "gray55"))


# ── main app ──────────────────────────────────────────────────────────────────

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Google Maps Business Scraper")
        self.geometry("1080x720")
        self.minsize(780, 540)

        self._q:            queue.Queue = queue.Queue()
        self._pool_thread:  threading.Thread | None = None
        self._stop_event =  threading.Event()
        self._locations:    list = []          # [{location, city, state, country}, ...]
        self._worker_rows:  list[WorkerRow] = []
        self._csv_path:     str = ""
        self._start_time:   float | None = None
        self._total_records:int = 0
        self._completed_loc:int = 0
        self._total_loc:    int = 0
        self._log_lines:    int = 0

        self._build_ui()
        self._poll_q()
        self._rebuild_worker_rows()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_sidebar()
        self._build_main()

    # ── SIDEBAR ───────────────────────────────────────────────────────────────

    def _build_sidebar(self):
        sb = ctk.CTkFrame(self, width=310, corner_radius=0)
        sb.grid(row=0, column=0, sticky="nsew")
        sb.grid_propagate(False)
        sb.grid_columnconfigure(0, weight=1)
        sb.grid_rowconfigure(20, weight=1)

        # Title
        ctk.CTkLabel(sb, text="Maps Scraper",
                     font=ctk.CTkFont(size=16, weight="bold")).grid(
            row=0, column=0, padx=20, pady=(20, 2), sticky="w")
        ctk.CTkLabel(sb, text="Google Maps → CSV  |  Multi-worker",
                     font=ctk.CTkFont(size=11), text_color=C_MUTED).grid(
            row=1, column=0, padx=20, pady=(0, 18), sticky="w")

        # Keyword
        self._keyword = self._labeled_entry(sb, "Keyword", 2, 'e.g. "dentist"')

        # Depth
        self._depth = self._labeled_entry(sb, "Max results per location (depth)", 4, "100")

        # Workers
        ctk.CTkLabel(sb, text="Parallel workers",
                     font=ctk.CTkFont(size=12)).grid(
            row=6, column=0, padx=20, pady=(0, 2), sticky="w")
        self._workers_var = ctk.StringVar(value="2")
        self._workers_menu = ctk.CTkOptionMenu(
            sb, values=["1", "2", "3", "4"],
            variable=self._workers_var,
            command=lambda _: self._rebuild_worker_rows(),
        )
        self._workers_menu.grid(row=7, column=0, padx=20, pady=(0, 14), sticky="ew")

        # Headless
        self._headless_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(sb, text="Headless (less safe, faster)",
                        variable=self._headless_var,
                        font=ctk.CTkFont(size=12)).grid(
            row=8, column=0, padx=20, pady=(0, 18), sticky="w")

        # Divider
        ctk.CTkFrame(sb, height=1, fg_color=C_DIVIDER).grid(
            row=9, column=0, padx=20, sticky="ew", pady=(0, 14))

        # Locations section
        ctk.CTkLabel(sb, text="Locations",
                     font=ctk.CTkFont(size=13, weight="bold")).grid(
            row=10, column=0, padx=20, pady=(0, 6), sticky="w")

        btn_row = ctk.CTkFrame(sb, fg_color="transparent")
        btn_row.grid(row=11, column=0, padx=20, sticky="ew", pady=(0, 6))
        btn_row.grid_columnconfigure(0, weight=1)

        ctk.CTkButton(btn_row, text="Upload CSV (City, State, Country)",
                      height=34, command=self._upload_csv).grid(
            row=0, column=0, sticky="ew")

        ctk.CTkButton(btn_row, text="Clear", width=60, height=34,
                      fg_color="transparent", border_width=1,
                      command=self._clear_locations).grid(
            row=0, column=1, padx=(6, 0))

        # Single location fallback
        self._single_loc = self._labeled_entry(sb, "or type a single location", 12,
                                                'e.g. "Miami, FL"')

        # Location count label
        self._loc_count_label = ctk.CTkLabel(sb, text="", font=ctk.CTkFont(size=11),
                                              text_color=C_ACCENT)
        self._loc_count_label.grid(row=14, column=0, padx=20, sticky="w")

        # Scrollable location list
        self._loc_list_frame = ctk.CTkScrollableFrame(sb, height=100, label_text="")
        self._loc_list_frame.grid(row=15, column=0, padx=20, sticky="ew", pady=(4, 8))
        self._loc_list_frame.grid_remove()  # hidden until CSV loaded

        # Spacer
        ctk.CTkFrame(sb, fg_color="transparent").grid(row=20, column=0, sticky="nsew")

        # CSV output label
        self._csv_label = ctk.CTkLabel(sb, text="", font=ctk.CTkFont(size=10),
                                        text_color=C_ACCENT, wraplength=270, justify="left")
        self._csv_label.grid(row=21, column=0, padx=20, pady=(0, 4), sticky="w")

        # Open folder
        self._open_btn = ctk.CTkButton(
            sb, text="Open outputs folder", height=28,
            fg_color="transparent", border_width=1,
            font=ctk.CTkFont(size=11), command=self._open_folder)
        self._open_btn.grid(row=22, column=0, padx=20, pady=(0, 10), sticky="ew")
        self._open_btn.grid_remove()

        # Start / Stop
        btn_frame = ctk.CTkFrame(sb, fg_color="transparent")
        btn_frame.grid(row=23, column=0, padx=20, pady=(0, 20), sticky="ew")
        btn_frame.grid_columnconfigure((0, 1), weight=1)

        self._start_btn = ctk.CTkButton(
            btn_frame, text="Start", height=42,
            fg_color=C_GREEN, hover_color=C_GREEN_H,
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._start)
        self._start_btn.grid(row=0, column=0, padx=(0, 5), sticky="ew")

        self._stop_btn = ctk.CTkButton(
            btn_frame, text="Stop", height=42,
            fg_color=C_RED, hover_color=C_RED_H,
            font=ctk.CTkFont(size=13, weight="bold"),
            state="disabled", command=self._stop)
        self._stop_btn.grid(row=0, column=1, padx=(5, 0), sticky="ew")

    def _labeled_entry(self, parent, label: str, row: int, placeholder: str = "") -> ctk.CTkEntry:
        ctk.CTkLabel(parent, text=label, font=ctk.CTkFont(size=12)).grid(
            row=row, column=0, padx=20, pady=(0, 2), sticky="w")
        e = ctk.CTkEntry(parent, placeholder_text=placeholder, height=34)
        e.grid(row=row + 1, column=0, padx=20, pady=(0, 12), sticky="ew")
        return e

    # ── MAIN PANEL ────────────────────────────────────────────────────────────

    def _build_main(self):
        main = ctk.CTkFrame(self, corner_radius=0, fg_color=("gray90", "gray14"))
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(2, weight=1)

        # ── Stats bar ─────────────────────────────────────────────────────────
        stats = ctk.CTkFrame(main, corner_radius=0, fg_color=("gray82", "gray18"), height=82)
        stats.grid(row=0, column=0, sticky="ew", padx=0, pady=0)
        stats.grid_propagate(False)
        stats.grid_columnconfigure((0, 1, 2, 3), weight=1)

        self._card_records  = StatCard(stats, "Records scraped")
        self._card_records.grid(row=0, column=0, padx=(12, 6), pady=10, sticky="nsew")

        self._card_rate     = StatCard(stats, "Records / min")
        self._card_rate.grid(row=0, column=1, padx=6, pady=10, sticky="nsew")

        self._card_elapsed  = StatCard(stats, "Elapsed")
        self._card_elapsed.configure(fg_color=C_CARD_BG)
        self._card_elapsed.grid(row=0, column=2, padx=6, pady=10, sticky="nsew")

        self._card_eta      = StatCard(stats, "ETA")
        self._card_eta.grid(row=0, column=3, padx=(6, 12), pady=10, sticky="nsew")

        # ── Workers section ───────────────────────────────────────────────────
        workers_wrap = ctk.CTkFrame(main, corner_radius=0, fg_color=("gray85", "gray16"))
        workers_wrap.grid(row=1, column=0, sticky="ew")

        hdr = ctk.CTkFrame(workers_wrap, fg_color="transparent", height=32)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        ctk.CTkLabel(hdr, text="Workers", font=ctk.CTkFont(size=12, weight="bold")).pack(
            side="left", padx=14, pady=4)
        self._loc_progress_label = ctk.CTkLabel(
            hdr, text="", font=ctk.CTkFont(size=11), text_color=C_MUTED)
        self._loc_progress_label.pack(side="right", padx=14)

        self._workers_frame = ctk.CTkFrame(workers_wrap, fg_color="transparent")
        self._workers_frame.pack(fill="x", padx=4, pady=(0, 8))

        # ── Log panel ─────────────────────────────────────────────────────────
        log_wrap = ctk.CTkFrame(main, corner_radius=0, fg_color="transparent")
        log_wrap.grid(row=2, column=0, sticky="nsew")
        log_wrap.grid_rowconfigure(1, weight=1)
        log_wrap.grid_columnconfigure(0, weight=1)

        log_hdr = ctk.CTkFrame(log_wrap, height=36, fg_color=("gray82", "gray18"), corner_radius=0)
        log_hdr.grid(row=0, column=0, sticky="ew")
        log_hdr.grid_propagate(False)
        log_hdr.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(log_hdr, text="Live Output",
                     font=ctk.CTkFont(size=12, weight="bold")).grid(
            row=0, column=0, padx=14, sticky="w")
        ctk.CTkButton(log_hdr, text="Clear", width=70, height=26,
                      fg_color="transparent", border_width=1,
                      font=ctk.CTkFont(size=11), command=self._clear_log).grid(
            row=0, column=1, padx=8, pady=4)

        self._log_box = ctk.CTkTextbox(
            log_wrap, font=ctk.CTkFont(family="Consolas", size=11),
            wrap="word", corner_radius=0,
            fg_color=("gray90", "gray14"))
        self._log_box.grid(row=1, column=0, sticky="nsew")
        self._log_box.configure(state="disabled")

        # Status bar
        sb2 = ctk.CTkFrame(main, height=24, corner_radius=0, fg_color=("gray78", "gray20"))
        sb2.grid(row=3, column=0, sticky="ew")
        sb2.grid_propagate(False)
        self._status_label = ctk.CTkLabel(sb2, text="Ready",
                                           font=ctk.CTkFont(size=10), text_color=C_MUTED)
        self._status_label.pack(side="left", padx=12)

    # ── Worker rows ───────────────────────────────────────────────────────────

    def _rebuild_worker_rows(self):
        for row in self._worker_rows:
            row.destroy()
        self._worker_rows.clear()

        n = int(self._workers_var.get())
        for i in range(n):
            row = WorkerRow(self._workers_frame, i)
            self._worker_rows.append(row)

    # ── Location management ───────────────────────────────────────────────────

    def _upload_csv(self):
        path = filedialog.askopenfilename(
            title="Select locations CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            locs = parse_locations_csv(path)
            if not locs:
                self._log(f"[ERROR] No valid rows in {path}. Need City column.")
                return
            self._locations = locs
            self._refresh_loc_display()
            self._log(f"[LOCATIONS] Loaded {len(locs)} locations from {Path(path).name}")
        except Exception as e:
            self._log(f"[ERROR] Could not parse CSV: {e}")

    def _clear_locations(self):
        self._locations = []
        self._refresh_loc_display()

    def _refresh_loc_display(self):
        n = len(self._locations)
        if n == 0:
            self._loc_count_label.configure(text="")
            self._loc_list_frame.grid_remove()
            return

        self._loc_count_label.configure(text=f"{n} locations loaded")
        # Rebuild list
        for widget in self._loc_list_frame.winfo_children():
            widget.destroy()
        for loc in self._locations:
            ctk.CTkLabel(self._loc_list_frame,
                          text=loc["location"],
                          font=ctk.CTkFont(size=11),
                          anchor="w").pack(fill="x", padx=4, pady=1)
        self._loc_list_frame.grid()

    # ── Actions ───────────────────────────────────────────────────────────────

    def _start(self):
        keyword = self._keyword.get().strip()
        if not keyword:
            self._log("[ERROR] Keyword is required.")
            return

        try:
            depth = int(self._depth.get().strip() or "100")
        except ValueError:
            self._log("[ERROR] Depth must be a whole number.")
            return

        # Resolve locations
        if self._locations:
            locations = self._locations
        else:
            single = self._single_loc.get().strip()
            if not single:
                self._log("[ERROR] Enter a location or upload a CSV.")
                return
            locations = [{"location": single, "city": "", "state": "", "country": ""}]

        n_workers = int(self._workers_var.get())
        headless  = self._headless_var.get()

        # Create CSV output for this job
        self._csv_path = CsvWriter.make_path(keyword)
        csv_writer = CsvWriter(self._csv_path)
        self._csv_label.configure(text=f"Output: {Path(self._csv_path).name}")
        self._open_btn.grid()

        # Reset state
        self._stop_event.clear()
        self._total_records  = 0
        self._completed_loc  = 0
        self._total_loc      = len(locations)
        self._start_time     = time.time()
        self._start_btn.configure(state="disabled")
        self._stop_btn.configure(state="normal")
        self._workers_menu.configure(state="disabled")
        self._card_records.set("0")
        self._card_rate.set("—")
        self._card_elapsed.set("00:00:00")
        self._card_eta.set("—")
        self._loc_progress_label.configure(text=f"0 / {self._total_loc} locations")
        self._set_status("Running...")

        # Make sure worker rows match selection
        self._rebuild_worker_rows()

        ts = datetime.now().strftime("%H:%M:%S")
        self._log(f"[{ts}] ── Starting: {keyword}  |  {self._total_loc} locations  |  {n_workers} workers")

        db = Database("businesses.db")

        def log_fn(msg):       self._q.put(("log", msg))
        def worker_fn(wid, s): self._q.put(("worker", wid, s))
        def overall_fn(done, total, records):
            self._q.put(("overall", done, total, records))

        pool = ScraperPool(
            keyword=keyword,
            locations=locations,
            n_workers=n_workers,
            depth=depth,
            db=db,
            csv_writer=csv_writer,
            log_fn=log_fn,
            worker_status_fn=worker_fn,
            overall_progress_fn=overall_fn,
            stop_event=self._stop_event,
            headless=headless,
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

        self._pool_thread = threading.Thread(target=run, daemon=True)
        self._pool_thread.start()
        self._tick()

    def _stop(self):
        self._stop_event.set()
        self._log("[STOP] Stop requested — workers will finish their current record...")
        self._stop_btn.configure(state="disabled")
        self._set_status("Stopping...")

    def _clear_log(self):
        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.configure(state="disabled")
        self._log_lines = 0

    def _open_folder(self):
        Path("outputs").mkdir(exist_ok=True)
        subprocess.Popen(f'explorer "{Path("outputs").resolve()}"')

    # ── Queue polling ─────────────────────────────────────────────────────────

    def _poll_q(self):
        try:
            while True:
                item = self._q.get_nowait()
                kind = item[0]
                if kind == "log":
                    self._append_log(item[1])
                elif kind == "worker":
                    _, wid, status = item
                    if wid < len(self._worker_rows):
                        self._worker_rows[wid].update(status)
                elif kind == "overall":
                    _, done, total, records = item
                    self._completed_loc  = done
                    self._total_records  = records
                    self._card_records.set(f"{records:,}")
                    self._loc_progress_label.configure(text=f"{done} / {total} locations")
                elif kind == "done":
                    self._on_done()
        except queue.Empty:
            pass
        self.after(100, self._poll_q)

    def _tick(self):
        """Update elapsed / rate / ETA every second while running."""
        if self._start_time is None:
            return
        elapsed = time.time() - self._start_time
        self._card_elapsed.set(self._fmt_time(elapsed))

        if elapsed > 5 and self._total_records > 0:
            rate = self._total_records / elapsed * 60
            self._card_rate.set(f"{rate:.1f}")

        if self._completed_loc > 0:
            time_per_loc = elapsed / self._completed_loc
            remaining    = self._total_loc - self._completed_loc
            eta          = remaining * time_per_loc
            self._card_eta.set(self._fmt_time(eta))

        if not self._stop_event.is_set() and self._start_time is not None:
            self.after(1000, self._tick)

    def _append_log(self, text: str):
        self._log_box.configure(state="normal")
        if self._log_lines >= LOG_MAX_LINES:
            self._log_box.delete("1.0", "500.0")
            self._log_lines -= 500
        self._log_box.insert("end", text + "\n")
        self._log_box.see("end")
        self._log_box.configure(state="disabled")
        self._log_lines += 1

    def _log(self, text: str):
        self._append_log(text)

    def _set_status(self, text: str):
        self._status_label.configure(text=text)

    def _on_done(self):
        self._start_time = None
        self._start_btn.configure(state="normal")
        self._stop_btn.configure(state="disabled")
        self._workers_menu.configure(state="normal")
        self._set_status("Done")
        ts = datetime.now().strftime("%H:%M:%S")
        self._append_log(f"[{ts}] ── All locations complete ───────────────────────")
        if self._csv_path:
            self._append_log(f"Output CSV: {self._csv_path}")

    @staticmethod
    def _fmt_time(seconds: float) -> str:
        s = max(0, int(seconds))
        h, rem = divmod(s, 3600)
        m, s   = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"


if __name__ == "__main__":
    app = App()
    app.mainloop()
