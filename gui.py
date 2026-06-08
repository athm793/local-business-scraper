#!/usr/bin/env python3
"""
Google Maps Business Scraper — GUI
Usage: py gui.py
"""

import queue
import threading

import customtkinter as ctk

from gui_widgets import (
    BG_APP, BG_SIDE, BG_CARD, BG_INPUT, BD, TX, TX_MUT,
    AC_BLUE, AC_GREEN, AC_AMBER, AC_PURPLE, AC_RED,
    BTN_GRN, BTN_GRN_H, BTN_RED, BTN_RED_H,
    section_label, StatCard, WorkerRow,
)
from gui_config import ConfigMixin
from gui_locations import LocationMixin
from gui_runner import RunMixin

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


# ── Main App ───────────────────────────────────────────────────────────────────

class App(ConfigMixin, LocationMixin, RunMixin, ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Maps Scraper")
        self.geometry("1120x680")
        self.minsize(820, 480)
        self.configure(fg_color=BG_APP)
        self.after(150, lambda: (self.wm_attributes('-topmost', True), self.focus_force()))

        self._q:             queue.Queue    = queue.Queue()
        self._stop_event   = threading.Event()
        self._locations:     list           = []
        self._worker_rows:   list[WorkerRow] = []
        self._csv_path:      str            = ""
        self._start_time:    float | None   = None
        self._total_records: int            = 0
        self._completed_loc: int            = 0
        self._total_loc:     int            = 0
        self._log_lines:     int            = 0
        self._is_running:    bool           = False
        self._tick_id                       = None
        self._csv_writer                    = None

        self._build_ui()
        self._load_config()
        self._rebuild_worker_rows()
        self._poll_q()

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

        workers_hdr = ctk.CTkFrame(cfg_frame, fg_color="transparent")
        workers_hdr.pack(fill="x", padx=16, pady=(0, 2))
        ctk.CTkLabel(workers_hdr, text="Parallel workers",
                     font=ctk.CTkFont(size=11), text_color=TX_MUT).pack(side="left")
        self._workers_val_label = ctk.CTkLabel(
            workers_hdr, text="2",
            font=ctk.CTkFont(size=11, weight="bold"), text_color=AC_BLUE)
        self._workers_val_label.pack(side="right")

        self._workers_slider = ctk.CTkSlider(
            cfg_frame, from_=1, to=20, number_of_steps=19,
            button_color=AC_BLUE, button_hover_color=AC_BLUE,
            progress_color=AC_BLUE,
            command=self._on_workers_change,
        )
        self._workers_slider.set(2)
        self._workers_slider.pack(fill="x", padx=16, pady=(0, 10))

        # Reviews row: checkbox + depth entry inline
        rev_row = ctk.CTkFrame(cfg_frame, fg_color="transparent")
        rev_row.pack(fill="x", padx=16, pady=(0, 8))

        self._reviews_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            rev_row, text="Scrape reviews",
            variable=self._reviews_var,
            font=ctk.CTkFont(size=11), text_color=TX_MUT,
            checkbox_width=18, checkbox_height=18,
            command=self._on_reviews_toggle,
        ).pack(side="left")

        self._review_depth_entry = ctk.CTkEntry(
            rev_row, width=46, height=28,
            fg_color=BG_INPUT, border_color=BD,
            font=ctk.CTkFont(size=11), text_color=TX,
            state="disabled",
        )
        self._review_depth_entry.insert(0, "10")
        self._review_depth_entry.pack(side="right", padx=(6, 0))

        ctk.CTkLabel(rev_row, text="per biz",
                     font=ctk.CTkFont(size=10), text_color=TX_MUT).pack(
            side="right", padx=(8, 2))

        # Hours row: two checkboxes side by side
        hrs_row = ctk.CTkFrame(cfg_frame, fg_color="transparent")
        hrs_row.pack(fill="x", padx=16, pady=(0, 8))
        self._hours_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(hrs_row, text="Scrape hours",
                        variable=self._hours_var,
                        font=ctk.CTkFont(size=11), text_color=TX_MUT,
                        checkbox_width=18, checkbox_height=18).pack(side="left")
        self._schedule_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(hrs_row, text="Full schedule",
                        variable=self._schedule_var,
                        font=ctk.CTkFont(size=11), text_color=TX_MUT,
                        checkbox_width=18, checkbox_height=18).pack(side="right")

        # ── Filters ───────────────────────────────────────────────────────────
        section_label(cfg_frame, "FILTERS  (skip non-matching businesses)")

        rev_f = ctk.CTkFrame(cfg_frame, fg_color="transparent")
        rev_f.pack(fill="x", padx=16, pady=(0, 5))
        ctk.CTkLabel(rev_f, text="Reviews", width=52,
                     font=ctk.CTkFont(size=11), text_color=TX_MUT).pack(side="left")
        self._filter_min_rev = ctk.CTkEntry(rev_f, width=62, height=28,
            placeholder_text="min", fg_color=BG_INPUT, border_color=BD,
            font=ctk.CTkFont(size=11), text_color=TX)
        self._filter_min_rev.pack(side="left", padx=(4, 2))
        ctk.CTkLabel(rev_f, text="–",
                     font=ctk.CTkFont(size=11), text_color=TX_MUT).pack(side="left")
        self._filter_max_rev = ctk.CTkEntry(rev_f, width=62, height=28,
            placeholder_text="max", fg_color=BG_INPUT, border_color=BD,
            font=ctk.CTkFont(size=11), text_color=TX)
        self._filter_max_rev.pack(side="left", padx=(2, 0))

        rat_f = ctk.CTkFrame(cfg_frame, fg_color="transparent")
        rat_f.pack(fill="x", padx=16, pady=(0, 5))
        ctk.CTkLabel(rat_f, text="Rating", width=52,
                     font=ctk.CTkFont(size=11), text_color=TX_MUT).pack(side="left")
        self._filter_min_rat = ctk.CTkEntry(rat_f, width=62, height=28,
            placeholder_text="min", fg_color=BG_INPUT, border_color=BD,
            font=ctk.CTkFont(size=11), text_color=TX)
        self._filter_min_rat.pack(side="left", padx=(4, 2))
        ctk.CTkLabel(rat_f, text="–",
                     font=ctk.CTkFont(size=11), text_color=TX_MUT).pack(side="left")
        self._filter_max_rat = ctk.CTkEntry(rat_f, width=62, height=28,
            placeholder_text="max", fg_color=BG_INPUT, border_color=BD,
            font=ctk.CTkFont(size=11), text_color=TX)
        self._filter_max_rat.pack(side="left", padx=(2, 0))

        wp_f = ctk.CTkFrame(cfg_frame, fg_color="transparent")
        wp_f.pack(fill="x", padx=16, pady=(0, 10))
        ctk.CTkLabel(wp_f, text="Website",
                     font=ctk.CTkFont(size=10), text_color=TX_MUT).pack(side="left")
        self._filter_website = ctk.CTkOptionMenu(
            wp_f, values=["Any", "Must have", "Must not have"],
            width=108, height=26, fg_color=BG_CARD, button_color=BD,
            dropdown_fg_color=BG_CARD, font=ctk.CTkFont(size=10))
        self._filter_website.pack(side="left", padx=(4, 10))
        ctk.CTkLabel(wp_f, text="Phone",
                     font=ctk.CTkFont(size=10), text_color=TX_MUT).pack(side="left")
        self._filter_phone = ctk.CTkOptionMenu(
            wp_f, values=["Any", "Must have", "Must not have"],
            width=108, height=26, fg_color=BG_CARD, button_color=BD,
            dropdown_fg_color=BG_CARD, font=ctk.CTkFont(size=10))
        self._filter_phone.pack(side="left", padx=(4, 0))

        def _clamp_entry(entry, lo, hi, is_float=True):
            def _cb(_):
                v = entry.get().strip()
                if not v:
                    return
                try:
                    val = float(v) if is_float else int(v)
                    val = max(lo, min(hi, val))
                    entry.delete(0, "end")
                    entry.insert(0, str(val) if is_float else str(int(val)))
                except ValueError:
                    entry.delete(0, "end")
            return _cb

        self._filter_min_rat.bind("<FocusOut>", _clamp_entry(self._filter_min_rat, 0.0, 5.0))
        self._filter_max_rat.bind("<FocusOut>", _clamp_entry(self._filter_max_rat, 0.0, 5.0))
        self._filter_min_rev.bind("<FocusOut>", _clamp_entry(self._filter_min_rev, 0, 10_000_000, is_float=False))
        self._filter_max_rev.bind("<FocusOut>", _clamp_entry(self._filter_max_rev, 0, 10_000_000, is_float=False))

        ctk.CTkFrame(sb, height=1, fg_color=BD).grid(
            row=4, column=0, sticky="ew", padx=0, pady=(4, 0))

        # ── Locations ──────────────────────────────────────────────────────────
        loc_frame = ctk.CTkFrame(sb, fg_color="transparent")
        loc_frame.grid(row=5, column=0, sticky="nsew")

        section_label(loc_frame, "LOCATIONS")

        btn_r = ctk.CTkFrame(loc_frame, fg_color="transparent")
        btn_r.pack(fill="x", padx=16, pady=(0, 8))
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

        add_r = ctk.CTkFrame(loc_frame, fg_color="transparent")
        add_r.pack(fill="x", padx=16, pady=(0, 6))
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

        self._loc_count = ctk.CTkLabel(loc_frame, text="No locations added",
                                        font=ctk.CTkFont(size=10), text_color=TX_MUT)
        self._loc_count.pack(anchor="w", padx=16, pady=(0, 4))

        self._chip_frame = ctk.CTkScrollableFrame(
            loc_frame, fg_color="transparent",
            scrollbar_button_color=BD, scrollbar_button_hover_color=TX_MUT)
        self._chip_frame.pack(fill="both", expand=True, padx=16, pady=(0, 6))

        ctk.CTkFrame(sb, height=1, fg_color=BD).grid(
            row=6, column=0, sticky="ew", padx=0, pady=(0, 0))

        # ── Controls ───────────────────────────────────────────────────────────
        ctrl = ctk.CTkFrame(sb, fg_color="transparent")
        ctrl.grid(row=7, column=0, sticky="ew", padx=16, pady=(10, 16))
        ctrl.grid_columnconfigure(0, weight=1)

        self._start_btn = ctk.CTkButton(
            ctrl, text="Start scraping", height=42,
            fg_color=BTN_GRN, hover_color=BTN_GRN_H,
            font=ctk.CTkFont(size=13, weight="bold"), text_color=TX,
            command=self._start,
        )
        self._start_btn.grid(row=0, column=0, sticky="ew", pady=(0, 6))

        self._stop_btn = ctk.CTkButton(
            ctrl, text="Stop", height=36,
            fg_color=BTN_RED, hover_color=BTN_RED_H,
            font=ctk.CTkFont(size=12), text_color=TX,
            state="disabled", command=self._stop,
        )
        self._stop_btn.grid(row=1, column=0, sticky="ew")

        self._out_label = ctk.CTkLabel(ctrl, text="",
                                        font=ctk.CTkFont(size=10), text_color=AC_BLUE,
                                        cursor="hand2", wraplength=260, justify="left")
        self._out_label.grid(row=2, column=0, pady=(10, 0), sticky="w")
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
        self._workers_wrap.pack(fill="x", padx=4, pady=(0, 8))

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

        ctk.CTkFrame(log_hdr, fg_color="transparent").grid(row=0, column=1, padx=8)

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

        self._log_box.tag_config("ts",      foreground="#3d4451")
        self._log_box.tag_config("worker",  foreground=AC_BLUE)
        self._log_box.tag_config("success", foreground=AC_GREEN)
        self._log_box.tag_config("error",   foreground=AC_RED)
        self._log_box.tag_config("warn",    foreground=AC_AMBER)
        self._log_box.tag_config("normal",  foreground=TX)
        self._log_box.tag_config("muted",   foreground=TX_MUT)

        sb2 = ctk.CTkFrame(main, fg_color=BG_SIDE, corner_radius=0, height=24)
        sb2.grid(row=3, column=0, sticky="sew")
        sb2.grid_propagate(False)
        self._status = ctk.CTkLabel(sb2, text="Ready",
                                     font=ctk.CTkFont(size=10), text_color=TX_MUT)
        self._status.pack(side="left", padx=14)

    # ── Worker rows ────────────────────────────────────────────────────────────

    def _get_workers(self) -> int:
        return int(round(self._workers_slider.get()))

    def _on_workers_change(self, value):
        n = int(round(value))
        self._workers_val_label.configure(text=str(n))
        self._rebuild_worker_rows()

    def _on_reviews_toggle(self):
        state = "normal" if self._reviews_var.get() else "disabled"
        self._review_depth_entry.configure(state=state)

    def _rebuild_worker_rows(self):
        for r in self._worker_rows:
            r.destroy()
        self._worker_rows.clear()
        n     = self._get_workers()
        ncols = 5
        for c in range(ncols):
            self._workers_wrap.grid_columnconfigure(c, weight=1)
        for i in range(n):
            card = WorkerRow(self._workers_wrap, i)
            card.grid(row=i // ncols, column=i % ncols, padx=4, pady=4, sticky="nsew")
            self._worker_rows.append(card)


if __name__ == "__main__":
    app = App()
    app.mainloop()
