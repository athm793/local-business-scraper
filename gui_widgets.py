"""Palette constants, shared widget classes, and helpers for the Maps Scraper GUI."""

import csv
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

# ── Palette (Terminal console UI · warm amber) ──────────────────────────────────
BG_APP    = "#0e0c0a"
BG_SIDE   = "#171310"
BG_CARD   = "#211b16"
BG_INPUT  = "#0e0c0a"
BD        = "#332a22"
TX        = "#f3ede6"
TX_MUT    = "#a29384"
AC_BLUE   = "#5aa2f0"   # "done" / info
AC_GREEN  = "#4ac47e"   # running / success / rate
AC_AMBER  = "#f59e0b"   # brand accent — starting / elapsed
AC_PURPLE = "#f59e0b"   # legacy name; ETA → brand amber
AC_RED    = "#f26a5a"   # errors / skips
BRAND     = "#f59e0b"
BRAND_INK = "#241503"   # dark ink for text on amber fills
BTN_GRN   = "#f59e0b"   # primary CTA (Start / Import) — amber
BTN_GRN_H = "#fbbf24"   # hover
BTN_RED   = "#f26a5a"   # stop (outline red)
BTN_RED_H = "#3a2420"   # stop hover bg (subtle warm)

FONT_UI   = "Segoe UI"
FONT_MONO = "Consolas"

# pill text inks (dark ink on solid state fills)
INK_RUN   = "#04150c"
INK_DONE  = "#04121f"
INK_START = "#231502"

STATE_COLORS = {
    "running":  AC_GREEN,
    "done":     AC_BLUE,
    "idle":     TX_MUT,
    "starting": AC_AMBER,
}
STATE_INK = {
    "running":  INK_RUN,
    "done":     INK_DONE,
    "starting": INK_START,
    "idle":     TX_MUT,
}

CONFIG_FILE   = Path(__file__).parent / "scraper_config.json"
LOG_MAX_LINES = 3000
MAX_LOC_SHOWN = 200


# ── Helpers ────────────────────────────────────────────────────────────────────

def section_label(parent, text: str):
    ctk.CTkLabel(
        parent, text=f"// {text}",
        font=ctk.CTkFont(size=10, weight="bold"),
        text_color=AC_AMBER,
    ).pack(anchor="w", padx=16, pady=(11, 3))


def _fmt_time(seconds: float) -> str:
    s = max(0, int(seconds))
    h, rem = divmod(s, 3600)
    m, s   = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


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
        self.wm_attributes('-topmost', True)

        self._filepath  = filepath
        self._on_import = on_import
        self._columns:  list = []
        self._preview:  list = []
        self._all_rows: list = []
        self._city_var    = ctk.StringVar(value=self.NONE)
        self._state_var   = ctk.StringVar(value=self.NONE)
        self._country_var = ctk.StringVar(value=self.NONE)
        self._info_vars:  dict[str, ctk.StringVar] = {}

        self._read_csv()
        self._build_ui()
        self._auto_detect()
        self._refresh_info()

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

    def _build_ui(self):
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

        ctk.CTkLabel(body, text="PREVIEW  (first 5 rows)",
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color=TX_MUT).grid(
            row=0, column=0, padx=20, pady=(16, 6), sticky="w")

        self._build_preview(body, row=1)

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
                         font=ctk.CTkFont(size=11, weight="bold"), text_color=AC_AMBER,
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
                unique    = len({(r.get(col) or "").strip() for r in self._all_rows if r.get(col)})
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
    """Flat KPI segment — hairline-grid strip (no border/shadow); label over value."""

    def __init__(self, parent, title: str, accent: str, **kw):
        super().__init__(parent, fg_color=BG_SIDE, corner_radius=0, **kw)
        ctk.CTkLabel(self, text=title.upper(),
                     font=ctk.CTkFont(size=10), text_color=TX_MUT,
                     anchor="w").pack(fill="x", padx=14, pady=(11, 1))
        self._val = ctk.CTkLabel(self, text="—",
                                  font=ctk.CTkFont(size=22, weight="bold"),
                                  text_color=accent, anchor="w")
        self._val.pack(fill="x", padx=14, pady=(0, 11))

    def set(self, v: str):
        self._val.configure(text=v)


class WorkerRow(ctk.CTkFrame):
    """Compact card for the 5-column worker grid — solid status pill."""

    def __init__(self, parent, wid: int):
        super().__init__(parent, fg_color=BG_CARD, corner_radius=6,
                         border_width=1, border_color=BD)

        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.pack(fill="x", padx=8, pady=(7, 0))

        ctk.CTkLabel(hdr, text=f"W{wid + 1}",
                     font=ctk.CTkFont(size=9, weight="bold"),
                     text_color=TX_MUT).pack(side="left")

        self._badge = ctk.CTkLabel(hdr, text="IDLE",
                                    font=ctk.CTkFont(size=9, weight="bold"),
                                    fg_color=BD, text_color=TX_MUT,
                                    corner_radius=4, height=16)
        self._badge.pack(side="right", ipadx=5)

        self._loc = ctk.CTkLabel(self, text="Idle",
                                  font=ctk.CTkFont(size=9), text_color=TX_MUT,
                                  anchor="w")
        self._loc.pack(fill="x", padx=8, pady=(4, 0))

        self._bar = ctk.CTkProgressBar(self, height=4, corner_radius=2,
                                        fg_color=BD, progress_color=AC_GREEN)
        self._bar.pack(fill="x", padx=8, pady=(4, 0))
        self._bar.set(0)

        self._count = ctk.CTkLabel(self, text="",
                                    font=ctk.CTkFont(size=9), text_color=TX_MUT)
        self._count.pack(pady=(3, 7))

    def update(self, s: dict):
        state   = s.get("state", "idle")
        loc     = s.get("location", "")
        current = s.get("current", 0)
        total   = s.get("total", 0)
        color   = STATE_COLORS.get(state, TX_MUT)

        loc_text = (loc[:21] + "…") if len(loc) > 22 else loc
        self._loc.configure(text=loc_text or "Idle",
                            text_color=TX if state == "running" else TX_MUT)
        self._bar.set(current / total if total else 0)
        self._bar.configure(progress_color=color)
        self._count.configure(
            text=f"{current:,} / {total:,}" if total else "",
            text_color=TX_MUT,
        )
        if state == "idle":
            self._badge.configure(text="IDLE", fg_color=BD, text_color=TX_MUT)
        else:
            self._badge.configure(text=state.upper(), fg_color=color,
                                  text_color=STATE_INK.get(state, BG_APP))


class LocationChip(ctk.CTkFrame):
    def __init__(self, parent, text: str):
        super().__init__(parent, fg_color=BG_CARD, corner_radius=5,
                         border_width=1, border_color=BD)
        self.pack(fill="x", padx=0, pady=2)
        ctk.CTkLabel(self, text=text,
                     font=ctk.CTkFont(size=11), text_color=TX,
                     anchor="w").pack(padx=(10, 10), pady=5, fill="x")
