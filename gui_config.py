"""Config persistence mixin — load/save scraper_config.json."""

import json

from gui_widgets import CONFIG_FILE


class ConfigMixin:

    def _load_config(self):
        try:
            cfg = json.loads(CONFIG_FILE.read_text())
            self._kw_entry.insert(0, cfg.get("keyword", ""))
            self._depth_entry.delete(0, "end")
            self._depth_entry.insert(0, cfg.get("depth", "100"))
            w = int(cfg.get("workers", 2))
            self._workers_slider.set(w)
            self._workers_val_label.configure(text=str(w))
            self._reviews_var.set(cfg.get("reviews_enabled", False))
            self._review_depth_entry.configure(state="normal")
            self._review_depth_entry.delete(0, "end")
            self._review_depth_entry.insert(0, str(cfg.get("reviews_depth", "10")))
            self._review_depth_entry.configure(
                state="normal" if self._reviews_var.get() else "disabled"
            )
            self._rebuild_worker_rows()
        except Exception:
            pass

    def _save_config(self):
        cfg = {
            "keyword":         self._kw_entry.get().strip(),
            "depth":           self._depth_entry.get().strip(),
            "workers":         self._get_workers(),
            "reviews_enabled": self._reviews_var.get(),
            "reviews_depth":   self._review_depth_entry.get().strip(),
        }
        try:
            CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
        except Exception:
            pass
