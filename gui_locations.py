"""Location management mixin — CSV import, manual add, chip list."""

import customtkinter as ctk
from tkinter import filedialog

from gui_widgets import ColumnMapperDialog, LocationChip, MAX_LOC_SHOWN, TX_MUT


class LocationMixin:

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

    def _clear_locations(self):
        self._locations.clear()
        self._refresh_chips()

    def _refresh_chips(self):
        for w in self._chip_frame.winfo_children():
            w.destroy()
        n = len(self._locations)
        self._loc_count.configure(
            text=f"{n:,} location{'s' if n != 1 else ''} loaded" if n else "No locations added"
        )
        for loc in self._locations[:MAX_LOC_SHOWN]:
            LocationChip(self._chip_frame, loc["location"])
        if n > MAX_LOC_SHOWN:
            ctk.CTkLabel(self._chip_frame,
                         text=f"+ {n - MAX_LOC_SHOWN:,} more not shown",
                         font=ctk.CTkFont(size=10), text_color=TX_MUT).pack(
                anchor="w", padx=4, pady=4)
