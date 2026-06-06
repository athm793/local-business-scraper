#!/usr/bin/env python3
"""
Google Maps Business Scraper — GUI
Usage: py gui.py
"""

import asyncio
import csv
import json
import queue
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from csv_writer import CsvWriter
from db import Database
from pool import ScraperPool

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ── Palette ────────────────────────────────────────────────────────────────────
BG_APP    = "#0d1117"
BG_SIDE   = "#161b22"
BG_CARD   = "#21262d"
BG_INPUT  = "#0d1117"
BD        = "#30363d"
TX        = "#e6edf3"
TX_MUT    = "#8b949e"
AC_BLUE   = "#58a6ff"
AC_GREEN  = "#3fb950"
AC_AMBER  = "#d29922"
AC_PURPLE = "#bc8cff"
AC_RED    = "#f85149"
BTN_GRN   = "#238636"
BTN_GRN_H = "#2ea043"
BTN_RED   = "#b91c1c"
BTN_RED_H = "#dc2626"

STATE_COLORS = {
    "running":  AC_GREEN,
    "done":     AC_BLUE,
    "idle":     TX_MUT,
    "starting": AC_AMBER,
}

CONFIG_FILE   = Path(__file__).parent / "scraper_config.json"
LOG_MAX_LINES = 3000
MAX_LOC_SHOWN = 200   # cap displayed chips for performance


# ── Helpers ────────────────────────────────────────────────────────────────────

def section_label(parent, text: str):
    ctk.CTkLabel(
        parent, text=text,
        font=ctk.CTkFont(size=10, weight="bold"),
        text_color=TX_MUT,
    ).pack(anchor="w", padx=16, pady=(14, 4))


def _fmt_time(seconds: float) -> str:
    s = max(0, int(seconds))
    h, rem = divmod(s, 3600)
    m, s   = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


# ── Column Mapper Dialog ────────────────────────────────────────────────────────

class ColumnMapperDialog(ctk.CTkToplevel):
    """Modal dialog: shows CSV preview, lets user map columns, returns parsed locations."""

    NONE = "(none)"

    def __init__(self, parent, filepath: str, on_import):
        super().__init__(parent)
        self.title("Import Location CSV")
        self.geometry("720x560")
        self.resizable(False, False)
        self.configure(fg_color=BG_APP)
        self.grab_set()
        self.lift()
        self.focus_force()

        self._filepath  = filepath
        self._on_import = on_import
        self._columns:  list = []
        self._preview:  list = []   # first 5 rows (dicts)
        self._all_rows: list = []   # all rows
        self._city_var    = ctk.StringVar(value=self.NONE)
        self._state_var   = ctk.StringVar(value=self.NONE)
        self._country_var = ctk.StringVar(value=self.NONE)
        self._info_vars:  dict[str, ctk.StringVar] = {}

        self._read_csv()
        self._build_ui()
        self._auto_detect()
        self._refresh_info()

    # ── CSV reading ────────────────────────────────────────────────────────────

    def _read_csv(self):
        try:
            with open(self._filepath, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                self._columns = list(reader.fieldnames or [])
                for row in reader:
                    self._all_rows.append(dict(row))
            self._preview = self._all_rows[:5]
        except Exception as e:
            messagebox.showerror("CSV Error", str(e), parent=self)
            self.destroy()

    def _auto_detect(self):
        lower = {c.lower().strip(): c for c in self._columns}
        for k in ("city", "town", "municipality", "locality"):
            if k in lower:
                self._city_var.set(lower[k]); break
        for k in ("state", "province", "region", "state_code"):
            if k in lower:
                self._state_var.set(lower[k]); break
        for k in ("country", "nation", "country_code", "country_name"):
            if k in lower:
                self._country_var.set(lower[k]); break

    # ── UI ─────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Header
        hdr = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=0)
        hdr.pack(fill="x")
        ctk.CTkLabel(hdr, text="Import Location CSV",
                     font=ctk.CTkFont(size=15, weight="bold"), text_color=TX).pack(
            side="left", padx=20, pady=14)

        fname   = Path(self._filepath).name
        n_rows  = len(self._all_rows)
        n_cols  = len(self._columns)
        meta    = f"{fname}  ·  {n_rows:,} rows  ·  {n_cols} columns"
        ctk.CTkLabel(hdr, text=meta, font=ctk.CTkFont(size=11),
                     text_color=TX_MUT).pack(side="right", padx=20)

        body = ctk.CTkScrollableFrame(self, fg_color=BG_APP, scrollbar_button_color=BD)
        body.pack(fill="both", expand=True, padx=0, pady=0)
        body.grid_columnconfigure(0, weight=1)

        # Preview table
        ctk.CTkLabel(body, text="PREVIEW  (first 5 rows)",
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color=TX_MUT).grid(
            row=0, column=0, padx=20, pady=(16, 6), sticky="w")

        self._build_preview(body, row=1)

        # Column mapping
        ctk.CTkLabel(body, text="MAP COLUMNS",
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color=TX_MUT).grid(
            row=2, column=0, padx=20, pady=(20, 8), sticky="w")

        map_frame = ctk.CTkFrame(body, fg_color=BG_CARD,
                                  corner_radius=10, border_width=1, border_color=BD)
        map_frame.grid(row=3, column=0, padx=20, sticky="ew", pady=(0, 16))
        map_frame.grid_columnconfigure(1, weight=1)

        opts = [self.NONE] + self._columns
        rows_cfg = [
            ("City", "Required — used as primary search term", self._city_var, True),
            ("State / Province", "Narrows results geographically", self._state_var, False),
            ("Country", "Optional — omit for US locations", self._country_var, False),
        ]
        for i, (label, hint, var, required) in enumerate(rows_cfg):
            star = " *" if required else ""
            ctk.CTkLabel(map_frame, text=f"{label}{star}",
                         font=ctk.CTkFont(size=12), text_color=TX).grid(
                row=i, column=0, padx=(16, 8), pady=(14 if i == 0 else 8, 8), sticky="w")

            info_var = ctk.StringVar(value="")
            self._info_vars[label] = info_var
            menu = ctk.CTkOptionMenu(
                map_frame, values=opts, variable=var,
                fg_color=BG_INPUT, button_color=BD,
                dropdown_fg_color=BG_CARD,
                command=lambda _, lbl=label, v=var: self._on_change(lbl, v),
                width=230,
            )
            menu.grid(row=i, column=1, padx=8, pady=(14 if i == 0 else 8, 8), sticky="w")

            ctk.CTkLabel(map_frame, textvariable=info_var,
                         font=ctk.CTkFont(size=10), text_color=TX_MUT).grid(
                row=i, column=2, padx=(4, 16), sticky="w")

        # Buttons
        btn_row = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=0)
        btn_row.pack(fill="x", side="bottom")

        self._import_btn = ctk.CTkButton(
            btn_row,
            text=f"Import {len(self._all_rows):,} locations →",
            height=38, fg_color=BTN_GRN, hover_color=BTN_GRN_H,
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._do_import,
        )
        self._import_btn.pack(side="right", padx=16, pady=12)

        ctk.CTkButton(
            btn_row, text="Cancel", height=38,
            fg_color="transparent", border_width=1, border_color=BD,
            command=self.destroy,
        ).pack(side="right", padx=(0, 6), pady=12)

    def _build_preview(self, parent, row: int):
        cols_show = self._columns[:7]
        outer = ctk.CTkFrame(parent, fg_color=BG_CARD,
                              corner_radius=10, border_width=1, border_color=BD)
        outer.grid(row=row, column=0, padx=20, sticky="ew")

        for ci, col in enumerate(cols_show):
            ctk.CTkLabel(outer, text=col,
                         font=ctk.CTkFont(size=11, weight="bold"), text_color=AC_BLUE,
                         anchor="w").grid(row=0, column=ci, padx=(12 if ci == 0 else 4, 4),
                                          pady=(10, 4), sticky="w")

        divider = ctk.CTkFrame(outer, height=1, fg_color=BD)
        divider.grid(row=1, column=0, columnspan=len(cols_show), sticky="ew",
                     padx=8, pady=0)

        for ri, prow in enumerate(self._preview):
            for ci, col in enumerate(cols_show):
                val = str(prow.get(col, "") or "")[:28]
                ctk.CTkLabel(outer, text=val,
                             font=ctk.CTkFont(size=11), text_color=TX,
                             anchor="w").grid(row=ri + 2, column=ci,
                                              padx=(12 if ci == 0 else 4, 4),
                                              pady=3, sticky="w")

        if len(self._columns) > 7:
            ctk.CTkLabel(outer,
                         text=f"+ {len(self._columns) - 7} more columns not shown",
                         font=ctk.CTkFont(size=10), text_color=TX_MUT).grid(
                row=len(self._preview) + 2, column=0,
                columnspan=len(cols_show), padx=12, pady=(4, 10), sticky="w")
        else:
            ctk.CTkFrame(outer, height=8, fg_color="transparent").grid(
                row=len(self._preview) + 2, column=0)

    def _on_change(self, label: str, var: ctk.StringVar):
        self._refresh_info()

    def _refresh_info(self):
        mapping = {
            "City":             self._city_var.get(),
            "State / Province": self._state_var.get(),
            "Country":          self._country_var.get(),
        }
        for label, col in mapping.items():
            var = self._info_vars.get(label)
            if not var:
                continue
            if col == self.NONE:
                var.set("")
            else:
                unique = len({(r.get(col) or "").strip() for r in self._all_rows if r.get(col)})
                non_empty = sum(1 for r in self._all_rows if (r.get(col) or "").strip())
                var.set(f"{unique:,} unique  ·  {non_empty:,} non-empty")

    def _do_import(self):
        city_col    = self._city_var.get()
        state_col   = self._state_var.get()
        country_col = self._country_var.get()

        if city_col == self.NONE:
            messagebox.showwarning("City Required",
                                   "Please map the City column before importing.",
                                   parent=self)
            return

        locations = []
        seen = set()
        for row in self._all_rows:
            city    = (row.get(city_col,    "") or "").strip()
            state   = (row.get(state_col,   "") or "").strip() if state_col   != self.NONE else ""
            country = (row.get(country_col, "") or "").strip() if country_col != self.NONE else ""
            if not city:
                continue
            parts = [p for p in [city, state] if p]
            if country and country.upper() not in ("US", "USA", "UNITED STATES",
                                                    "UNITED STATES OF AMERICA"):
                parts.append(country)
            loc_str = ", ".join(parts)
            if loc_str in seen:
                continue
            seen.add(loc_str)
            locations.append({"location": loc_str, "city": city,
                               "state": state, "country": country})

        self._on_import(locations)
        self.destroy()


# ── Widgets ────────────────────────────────────────────────────────────────────

class StatCard(ctk.CTkFrame):
    def __init__(self, parent, title: str, accent: str, **kw):
        super().__init__(parent, fg_color=BG_CARD, corner_radius=10,
                         border_width=1, border_color=BD, **kw)
        ctk.CTkFrame(self, height=3, fg_color=accent, corner_radius=0).pack(
            fill="x", side="top")
        self._val = ctk.CTkLabel(self, text="—",
                                  font=ctk.CTkFont(size=22, weight="bold"), text_color=TX)
        self._val.pack(pady=(10, 2), padx=12)
        ctk.CTkLabel(self, text=title,
                     font=ctk.CTkFont(size=10), text_color=TX_MUT).pack(pady=(0, 10))

    def set(self, v: str):
        self._val.configure(text=v)


class WorkerRow(ctk.CTkFrame):
    def __init__(self, parent, wid: int):
        super().__init__(parent, fg_color=BG_CARD, corner_radius=8,
                         border_width=1, border_color=BD)
        self.pack(fill="x", padx=12, pady=3)

        # Dot
        self._dot = ctk.CTkLabel(self, text="●", font=ctk.CTkFont(size=10),
                                  text_color=TX_MUT, width=18)
        self._dot.grid(row=0, column=0, padx=(10, 4), pady=10)

        # Worker label
        ctk.CTkLabel(self, text=f"W{wid + 1}",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=TX_MUT, width=26).grid(
            row=0, column=1, padx=(0, 8), pady=10)

        # Location
        self._loc = ctk.CTkLabel(self, text="Idle",
                                  font=ctk.CTkFont(size=12), text_color=TX,
                                  anchor="w", width=230)
        self._loc.grid(row=0, column=2, padx=(0, 12), pady=10, sticky="w")

        # Progress bar
        self._bar = ctk.CTkProgressBar(self, width=160, height=6,
                                        corner_radius=3, progress_color=AC_GREEN)
        self._bar.grid(row=0, column=3, padx=(0, 10), pady=10)
        self._bar.set(0)

        # Count
        self._count = ctk.CTkLabel(self, text="",
                                    font=ctk.CTkFont(size=11), text_color=TX_MUT, width=72)
        self._count.grid(row=0, column=4, padx=(0, 8), pady=10)

        # Status badge
        self._badge = ctk.CTkLabel(self, text="Idle",
                                    font=ctk.CTkFont(size=10),
                                    text_color=TX_MUT, width=68)
        self._badge.grid(row=0, column=5, padx=(0, 10), pady=10)

    def update(self, s: dict):
        state   = s.get("state", "idle")
        loc     = s.get("location", "")
        current = s.get("current", 0)
        total   = s.get("total", 0)
        color   = STATE_COLORS.get(state, TX_MUT)

        self._dot.configure(text_color=color)
        self._loc.configure(text=loc or "Idle")
        self._bar.set(current / total if total else 0)
        self._bar.configure(progress_color=color)
        self._count.configure(text=f"{current:,} / {total:,}" if total else "")
        self._badge.configure(text=state.title(), text_color=color)


class LocationChip(ctk.CTkFrame):
    def __init__(self, parent, text: str, on_remove):
        super().__init__(parent, fg_color=BG_CARD, corner_radius=6,
                         border_width=1, border_color=BD)
        self.pack(fill="x", padx=0, pady=2)
        ctk.CTkLabel(self, text=text,
                     font=ctk.CTkFont(size=11), text_color=TX,
                     anchor="w").pack(side="left", padx=(10, 4), pady=6, expand=True, fill="x")
        ctk.CTkButton(self, text="×", width=22, height=22,
                      fg_color="transparent", hover_color=BD,
                      font=ctk.CTkFont(size=13), text_color=TX_MUT,
                      command=on_remove).pack(side="right", padx=6, pady=4)


# ── Main App ───────────────────────────────────────────────────────────────────

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Maps Scraper")
        self.geometry("1120x740")
        self.minsize(820, 560)
        self.configure(fg_color=BG_APP)

        self._q:            queue.Queue   = queue.Queue()
        self._stop_event =  threading.Event()
        self._locations:    list          = []
        self._worker_rows:  list[WorkerRow] = []
        self._csv_path:     str           = ""
        self._start_time:   float | None  = None
        self._total_records: int          = 0
        self._completed_loc: int          = 0
        self._total_loc:    int           = 0
        self._log_lines:    int           = 0
        self._is_running:   bool          = False

        self._build_ui()
        self._load_config()
        self._rebuild_worker_rows()
        self._poll_q()

    # ── Config ────────────────────────────────────────────────────────────────

    def _load_config(self):
        try:
            cfg = json.loads(CONFIG_FILE.read_text())
            self._kw_entry.insert(0, cfg.get("keyword", ""))
            self._depth_entry.delete(0, "end")
            self._depth_entry.insert(0, cfg.get("depth", "100"))
            self._workers_seg.set(cfg.get("workers", "2"))
            self._headless_var.set(cfg.get("headless", False))
            self._rebuild_worker_rows()
        except Exception:
            pass

    def _save_config(self):
        cfg = {
            "keyword":  self._kw_entry.get().strip(),
            "depth":    self._depth_entry.get().strip(),
            "workers":  self._workers_seg.get(),
            "headless": self._headless_var.get(),
        }
        try:
            CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
        except Exception:
            pass

    # ── UI ─────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_sidebar()
        self._build_main()

    # ── SIDEBAR ────────────────────────────────────────────────────────────────

    def _build_sidebar(self):
        sb = ctk.CTkFrame(self, width=300, fg_color=BG_SIDE, corner_radius=0)
        sb.grid(row=0, column=0, sticky="nsew")
        sb.grid_propagate(False)
        sb.grid_columnconfigure(0, weight=1)
        sb.grid_rowconfigure(5, weight=1)

        # Brand
        brand = ctk.CTkFrame(sb, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="ew", padx=16, pady=(18, 4))
        ctk.CTkLabel(brand, text="Maps Scraper",
                     font=ctk.CTkFont(size=16, weight="bold"), text_color=TX).pack(
            side="left")

        ctk.CTkLabel(sb, text="Google Maps → CSV",
                     font=ctk.CTkFont(size=11), text_color=TX_MUT).grid(
            row=1, column=0, padx=18, pady=(0, 4), sticky="w")

        ctk.CTkFrame(sb, height=1, fg_color=BD).grid(
            row=2, column=0, sticky="ew", padx=0, pady=(8, 0))

        # ── Search config ──────────────────────────────────────────────────────
        cfg_frame = ctk.CTkFrame(sb, fg_color="transparent")
        cfg_frame.grid(row=3, column=0, sticky="ew")
        cfg_frame.grid_columnconfigure(0, weight=1)

        section_label(cfg_frame, "SEARCH")

        ctk.CTkLabel(cfg_frame, text="Keyword",
                     font=ctk.CTkFont(size=11), text_color=TX_MUT).pack(
            anchor="w", padx=16, pady=(0, 3))
        self._kw_entry = ctk.CTkEntry(
            cfg_frame, placeholder_text='e.g. "dentist"',
            fg_color=BG_INPUT, border_color=BD, height=36,
            font=ctk.CTkFont(size=12), text_color=TX)
        self._kw_entry.pack(fill="x", padx=16, pady=(0, 10))

        ctk.CTkLabel(cfg_frame, text="Results per location",
                     font=ctk.CTkFont(size=11), text_color=TX_MUT).pack(
            anchor="w", padx=16, pady=(0, 3))
        self._depth_entry = ctk.CTkEntry(
            cfg_frame, placeholder_text="100",
            fg_color=BG_INPUT, border_color=BD, height=36,
            font=ctk.CTkFont(size=12), text_color=TX)
        self._depth_entry.insert(0, "100")
        self._depth_entry.pack(fill="x", padx=16, pady=(0, 10))

        ctk.CTkLabel(cfg_frame, text="Parallel workers",
                     font=ctk.CTkFont(size=11), text_color=TX_MUT).pack(
            anchor="w", padx=16, pady=(0, 4))
        self._workers_seg = ctk.CTkSegmentedButton(
            cfg_frame, values=["1", "2", "3", "4"],
            font=ctk.CTkFont(size=12),
            command=lambda _: (self._rebuild_worker_rows(), self._update_estimate()),
        )
        self._workers_seg.set("2")
        self._workers_seg.pack(fill="x", padx=16, pady=(0, 10))

        self._headless_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            cfg_frame, text="Headless mode",
            variable=self._headless_var,
            font=ctk.CTkFont(size=11), text_color=TX_MUT,
            checkbox_width=18, checkbox_height=18,
        ).pack(anchor="w", padx=16, pady=(0, 4))

        ctk.CTkFrame(sb, height=1, fg_color=BD).grid(
            row=4, column=0, sticky="ew", padx=0, pady=(4, 0))

        # ── Locations ──────────────────────────────────────────────────────────
        loc_frame = ctk.CTkFrame(sb, fg_color="transparent")
        loc_frame.grid(row=5, column=0, sticky="nsew")
        loc_frame.grid_columnconfigure(0, weight=1)
        loc_frame.grid_rowconfigure(4, weight=1)

        section_label(loc_frame, "LOCATIONS")

        # Buttons row
        btn_r = ctk.CTkFrame(loc_frame, fg_color="transparent")
        btn_r.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 8))
        btn_r.grid_columnconfigure(0, weight=1)

        ctk.CTkButton(
            btn_r, text="Upload CSV", height=34,
            fg_color=BG_CARD, hover_color=BD, border_width=1, border_color=BD,
            font=ctk.CTkFont(size=12), text_color=TX,
            command=self._upload_csv,
        ).grid(row=0, column=0, sticky="ew")

        ctk.CTkButton(
            btn_r, text="Clear", height=34, width=54,
            fg_color="transparent", hover_color=BD, border_width=1, border_color=BD,
            font=ctk.CTkFont(size=11), text_color=TX_MUT,
            command=self._clear_locations,
        ).grid(row=0, column=1, padx=(6, 0))

        # Manual add
        add_r = ctk.CTkFrame(loc_frame, fg_color="transparent")
        add_r.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 6))
        add_r.grid_columnconfigure(0, weight=1)

        self._manual_entry = ctk.CTkEntry(
            add_r, placeholder_text='Type a location + Enter',
            fg_color=BG_INPUT, border_color=BD, height=32,
            font=ctk.CTkFont(size=11), text_color=TX)
        self._manual_entry.grid(row=0, column=0, sticky="ew")
        self._manual_entry.bind("<Return>", lambda _: self._add_manual())

        ctk.CTkButton(
            add_r, text="Add", width=46, height=32,
            fg_color=BG_CARD, hover_color=BD, border_width=1, border_color=BD,
            font=ctk.CTkFont(size=11), text_color=TX_MUT,
            command=self._add_manual,
        ).grid(row=0, column=1, padx=(5, 0))

        # Count label
        self._loc_count = ctk.CTkLabel(loc_frame, text="No locations added",
                                        font=ctk.CTkFont(size=10), text_color=TX_MUT)
        self._loc_count.grid(row=3, column=0, padx=16, sticky="w", pady=(0, 4))

        # Scrollable chip list
        self._chip_frame = ctk.CTkScrollableFrame(
            loc_frame, fg_color="transparent",
            scrollbar_button_color=BD, scrollbar_button_hover_color=TX_MUT)
        self._chip_frame.grid(row=4, column=0, sticky="nsew", padx=16, pady=(0, 6))

        ctk.CTkFrame(sb, height=1, fg_color=BD).grid(
            row=6, column=0, sticky="ew", padx=0, pady=(0, 0))

        # ── Controls ───────────────────────────────────────────────────────────
        ctrl = ctk.CTkFrame(sb, fg_color="transparent")
        ctrl.grid(row=7, column=0, sticky="ew", padx=16, pady=(10, 16))
        ctrl.grid_columnconfigure(0, weight=1)

        # Estimate
        self._est_label = ctk.CTkLabel(ctrl, text="",
                                        font=ctk.CTkFont(size=10), text_color=TX_MUT)
        self._est_label.grid(row=0, column=0, pady=(0, 6), sticky="w")

        self._start_btn = ctk.CTkButton(
            ctrl, text="Start scraping", height=42,
            fg_color=BTN_GRN, hover_color=BTN_GRN_H,
            font=ctk.CTkFont(size=13, weight="bold"), text_color=TX,
            command=self._start,
        )
        self._start_btn.grid(row=1, column=0, sticky="ew", pady=(0, 6))

        self._stop_btn = ctk.CTkButton(
            ctrl, text="Stop", height=36,
            fg_color=BTN_RED, hover_color=BTN_RED_H,
            font=ctk.CTkFont(size=12), text_color=TX,
            state="disabled", command=self._stop,
        )
        self._stop_btn.grid(row=2, column=0, sticky="ew")

        # Output file label
        self._out_label = ctk.CTkLabel(ctrl, text="",
                                        font=ctk.CTkFont(size=10), text_color=AC_BLUE,
                                        cursor="hand2", wraplength=260, justify="left")
        self._out_label.grid(row=3, column=0, pady=(10, 0), sticky="w")
        self._out_label.bind("<Button-1>", lambda _: self._open_folder())

    # ── MAIN PANEL ─────────────────────────────────────────────────────────────

    def _build_main(self):
        main = ctk.CTkFrame(self, fg_color=BG_APP, corner_radius=0)
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(2, weight=1)

        # ── Stats row ──────────────────────────────────────────────────────────
        stats = ctk.CTkFrame(main, fg_color=BG_SIDE, corner_radius=0, height=94)
        stats.grid(row=0, column=0, sticky="ew")
        stats.grid_propagate(False)
        stats.grid_columnconfigure((0, 1, 2, 3), weight=1)

        self._c_records = StatCard(stats, "Records scraped", AC_BLUE)
        self._c_records.grid(row=0, column=0, padx=(14, 6), pady=12, sticky="nsew")

        self._c_rate = StatCard(stats, "Records / min", AC_GREEN)
        self._c_rate.grid(row=0, column=1, padx=6, pady=12, sticky="nsew")

        self._c_elapsed = StatCard(stats, "Elapsed", AC_AMBER)
        self._c_elapsed.grid(row=0, column=2, padx=6, pady=12, sticky="nsew")

        self._c_eta = StatCard(stats, "ETA", AC_PURPLE)
        self._c_eta.grid(row=0, column=3, padx=(6, 14), pady=12, sticky="nsew")

        # ── Workers section ────────────────────────────────────────────────────
        w_outer = ctk.CTkFrame(main, fg_color=BG_SIDE, corner_radius=0)
        w_outer.grid(row=1, column=0, sticky="ew")

        w_hdr = ctk.CTkFrame(w_outer, fg_color="transparent", height=34)
        w_hdr.pack(fill="x")
        w_hdr.pack_propagate(False)
        ctk.CTkLabel(w_hdr, text="WORKERS",
                     font=ctk.CTkFont(size=10, weight="bold"), text_color=TX_MUT).pack(
            side="left", padx=16, pady=8)
        self._loc_prog = ctk.CTkLabel(w_hdr, text="",
                                       font=ctk.CTkFont(size=10), text_color=TX_MUT)
        self._loc_prog.pack(side="right", padx=16)

        self._workers_wrap = ctk.CTkFrame(w_outer, fg_color="transparent")
        self._workers_wrap.pack(fill="x", padx=4, pady=(0, 10))

        ctk.CTkFrame(main, height=1, fg_color=BD, corner_radius=0).grid(
            row=1, column=0, sticky="sew")

        # ── Log panel ──────────────────────────────────────────────────────────
        log_wrap = ctk.CTkFrame(main, fg_color=BG_APP, corner_radius=0)
        log_wrap.grid(row=2, column=0, sticky="nsew")
        log_wrap.grid_rowconfigure(1, weight=1)
        log_wrap.grid_columnconfigure(0, weight=1)

        log_hdr = ctk.CTkFrame(log_wrap, fg_color=BG_SIDE, corner_radius=0, height=36)
        log_hdr.grid(row=0, column=0, sticky="ew")
        log_hdr.grid_propagate(False)
        log_hdr.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(log_hdr, text="LIVE OUTPUT",
                     font=ctk.CTkFont(size=10, weight="bold"), text_color=TX_MUT).grid(
            row=0, column=0, padx=16, sticky="w")

        btn_row2 = ctk.CTkFrame(log_hdr, fg_color="transparent")
        btn_row2.grid(row=0, column=1, padx=8)

        # CSV output strip (inside log header)
        self._csv_name_label = ctk.CTkLabel(log_hdr, text="",
                                             font=ctk.CTkFont(size=10), text_color=TX_MUT)
        self._csv_name_label.grid(row=0, column=2, padx=(0, 4))

        self._copy_btn = ctk.CTkButton(
            log_hdr, text="Copy path", width=76, height=26,
            fg_color="transparent", border_width=1, border_color=BD,
            font=ctk.CTkFont(size=10), text_color=TX_MUT,
            command=self._copy_csv_path, state="disabled")
        self._copy_btn.grid(row=0, column=3, padx=(0, 4))

        ctk.CTkButton(
            log_hdr, text="Open folder", width=80, height=26,
            fg_color="transparent", border_width=1, border_color=BD,
            font=ctk.CTkFont(size=10), text_color=TX_MUT,
            command=self._open_folder).grid(row=0, column=4, padx=(0, 4))

        ctk.CTkButton(
            log_hdr, text="Clear", width=54, height=26,
            fg_color="transparent", border_width=1, border_color=BD,
            font=ctk.CTkFont(size=10), text_color=TX_MUT,
            command=self._clear_log).grid(row=0, column=5, padx=(0, 10))

        self._log_box = ctk.CTkTextbox(
            log_wrap,
            font=ctk.CTkFont(family="Consolas", size=11),
            wrap="word", corner_radius=0,
            fg_color=BG_APP, text_color=TX,
            scrollbar_button_color=BD,
        )
        self._log_box.grid(row=1, column=0, sticky="nsew")
        self._log_box.configure(state="disabled")

        # Configure log colors
        self._log_box.tag_config("ts",      foreground="#3d4451")
        self._log_box.tag_config("worker",  foreground=AC_BLUE)
        self._log_box.tag_config("success", foreground=AC_GREEN)
        self._log_box.tag_config("error",   foreground=AC_RED)
        self._log_box.tag_config("warn",    foreground=AC_AMBER)
        self._log_box.tag_config("normal",  foreground=TX)
        self._log_box.tag_config("muted",   foreground=TX_MUT)

        # Status bar
        sb2 = ctk.CTkFrame(main, fg_color=BG_SIDE, corner_radius=0, height=24)
        sb2.grid(row=3, column=0, sticky="sew")
        sb2.grid_propagate(False)
        self._status = ctk.CTkLabel(sb2, text="Ready",
                                     font=ctk.CTkFont(size=10), text_color=TX_MUT)
        self._status.pack(side="left", padx=14)

    # ── Worker rows ────────────────────────────────────────────────────────────

    def _rebuild_worker_rows(self):
        for r in self._worker_rows:
            r.destroy()
        self._worker_rows.clear()
        n = int(self._workers_seg.get())
        for i in range(n):
            self._worker_rows.append(WorkerRow(self._workers_wrap, i))

    # ── Location management ───────────────────────────────────────────────────

    def _upload_csv(self):
        path = filedialog.askopenfilename(
            title="Select locations CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        ColumnMapperDialog(self, path, self._on_import)

    def _on_import(self, locations: list):
        self._locations = locations
        self._refresh_chips()
        self._update_estimate()
        self._log_line(f"Loaded {len(locations):,} locations from CSV", "success")

    def _add_manual(self):
        text = self._manual_entry.get().strip()
        if not text:
            return
        if any(l["location"] == text for l in self._locations):
            self._manual_entry.delete(0, "end")
            return
        self._locations.append({"location": text, "city": "", "state": "", "country": ""})
        self._manual_entry.delete(0, "end")
        self._refresh_chips()
        self._update_estimate()

    def _remove_location(self, idx: int):
        if 0 <= idx < len(self._locations):
            self._locations.pop(idx)
            self._refresh_chips()
            self._update_estimate()

    def _clear_locations(self):
        self._locations.clear()
        self._refresh_chips()
        self._update_estimate()

    def _refresh_chips(self):
        for w in self._chip_frame.winfo_children():
            w.destroy()
        n = len(self._locations)
        self._loc_count.configure(
            text=f"{n:,} location{'s' if n != 1 else ''} loaded" if n else "No locations added"
        )
        for i, loc in enumerate(self._locations[:MAX_LOC_SHOWN]):
            idx = i
            LocationChip(self._chip_frame, loc["location"],
                         on_remove=lambda i=idx: self._remove_location(i))
        if n > MAX_LOC_SHOWN:
            ctk.CTkLabel(self._chip_frame,
                         text=f"+ {n - MAX_LOC_SHOWN:,} more not shown",
                         font=ctk.CTkFont(size=10), text_color=TX_MUT).pack(
                anchor="w", padx=4, pady=4)

    def _update_estimate(self, *_):
        n   = len(self._locations) or (1 if self._manual_entry.get().strip() else 0)
        try:
            depth = int(self._depth_entry.get() or "100")
        except ValueError:
            depth = 100
        workers  = int(self._workers_seg.get())
        avg_sec  = 5
        total    = n * depth * avg_sec / max(workers, 1)
        if n == 0:
            self._est_label.configure(text="")
            return
        if total < 60:
            t = f"~{int(total)}s"
        elif total < 3600:
            t = f"~{int(total / 60)} min"
        else:
            t = f"~{total / 3600:.1f} h"
        self._est_label.configure(
            text=f"Est. {t}  ·  {n:,} location{'s' if n != 1 else ''}  ·  {workers} worker{'s' if workers != 1 else ''}"
        )

    # ── Actions ───────────────────────────────────────────────────────────────

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

        locations = self._locations if self._locations else []
        if not locations:
            single = self._manual_entry.get().strip()
            if not single:
                self._flash_error("Add at least one location or upload a CSV.")
                return
            locations = [{"location": single, "city": "", "state": "", "country": ""}]

        n_workers = int(self._workers_seg.get())
        headless  = self._headless_var.get()

        self._save_config()

        # Create output CSV
        self._csv_path = CsvWriter.make_path(keyword)
        csv_writer = CsvWriter(self._csv_path)
        csv_name   = Path(self._csv_path).name
        self._csv_name_label.configure(text=csv_name)
        self._copy_btn.configure(state="normal")
        self._out_label.configure(text=f"Output: {csv_name}")

        # Reset state
        self._stop_event.clear()
        self._total_records  = 0
        self._completed_loc  = 0
        self._total_loc      = len(locations)
        self._start_time     = time.time()
        self._is_running     = True

        self._start_btn.configure(state="disabled")
        self._stop_btn.configure(state="normal")
        self._workers_seg.configure(state="disabled")
        self._c_records.set("0")
        self._c_rate.set("—")
        self._c_elapsed.set("00:00:00")
        self._c_eta.set("—")
        self._loc_prog.configure(text=f"0 / {self._total_loc} locations")
        self._status.configure(text="Running")

        self._rebuild_worker_rows()
        ts = datetime.now().strftime("%H:%M:%S")
        self._log_line(f"── Starting: {keyword}  ·  {self._total_loc} locations  ·  {n_workers} workers ──", "success")
        self._log_line(f"Output: {self._csv_path}", "muted")

        db = Database("businesses.db")

        def log_fn(msg):       self._q.put(("log", msg))
        def worker_fn(wid, s): self._q.put(("worker", wid, s))
        def overall_fn(done, total, records):
            self._q.put(("overall", done, total, records))

        pool = ScraperPool(
            keyword=keyword, locations=locations, n_workers=n_workers,
            depth=depth, db=db, csv_writer=csv_writer,
            log_fn=log_fn, worker_status_fn=worker_fn,
            overall_progress_fn=overall_fn,
            stop_event=self._stop_event, headless=headless,
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
        self._tick()

    def _stop(self):
        self._stop_event.set()
        self._log_line("Stop requested — workers finishing current record...", "warn")
        self._stop_btn.configure(state="disabled")
        self._status.configure(text="Stopping...")

    def _copy_csv_path(self):
        if self._csv_path:
            self.clipboard_clear()
            self.clipboard_append(self._csv_path)
            self._copy_btn.configure(text="Copied!")
            self.after(2000, lambda: self._copy_btn.configure(text="Copy path"))

    def _open_folder(self):
        Path("outputs").mkdir(exist_ok=True)
        subprocess.Popen(f'explorer "{Path("outputs").resolve()}"')

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

        self.after(1000, self._tick)

    def _log_line(self, text: str, tag: str = ""):
        if not tag:
            if "[ERROR]" in text or "[SKIP]" in text:
                tag = "error"
            elif "[DONE]" in text or "finished" in text.lower() or "──" in text:
                tag = "success"
            elif text.startswith("[W") or "W1]" in text or "W2]" in text:
                tag = "worker"
            elif "[STOP]" in text or "[RESUME]" in text:
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
        self._log_lines += 1

    def _clear_log(self):
        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.configure(state="disabled")
        self._log_lines = 0

    def _on_done(self):
        self._is_running    = False
        self._start_time    = None
        self._start_btn.configure(state="normal")
        self._stop_btn.configure(state="disabled")
        self._workers_seg.configure(state="normal")
        self._status.configure(text="Done")
        self._log_line(
            f"── Complete: {self._total_records:,} records  ·  {self._completed_loc} locations ──",
            "success",
        )
        if self._csv_path:
            self._log_line(f"CSV: {self._csv_path}", "muted")


if __name__ == "__main__":
    app = App()
    app.mainloop()
