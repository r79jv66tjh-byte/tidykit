"""Hauptfenster von TidyKit."""
from __future__ import annotations

import ctypes
import tkinter as tk
from tkinter import messagebox, ttk

from ..journal import Journal
from ..winutil import is_admin, relaunch_as_admin
from .common import Background
from .tab_autostart import AutostartTab
from .tab_history import HistoryTab
from .tab_leftovers import LeftoversTab
from .tab_renamer import RenamerTab
from .tab_sync import SyncTab


class App(tk.Tk):
    def __init__(self, journal: Journal | None = None, autostart_sources=None, autoload: bool = True):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
        super().__init__()
        self.title("TidyKit" + ("  [Administrator]" if is_admin() else ""))
        self.geometry("1280x800")
        self.minsize(900, 600)
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Treeview", rowheight=22)

        self.bg = Background(self)
        self.journal = journal or Journal()

        header = ttk.Frame(self, padding=(8, 6))
        header.pack(fill="x")
        ttk.Label(header, text="TidyKit", font=("Segoe UI", 14, "bold")).pack(side="left")
        ttk.Label(header, text="  Nichts ohne Vorschau · Löschen nur in den Papierkorb · alles rückgängig machbar",
                  foreground="#555").pack(side="left")
        if not is_admin():
            ttk.Button(header, text="Als Administrator neu starten", command=self.elevate).pack(side="right")

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=6)
        self.leftovers = LeftoversTab(self.nb, self)
        self.autostart = AutostartTab(self.nb, self, autostart_sources)
        self.renamer = RenamerTab(self.nb, self)
        self.sync = SyncTab(self.nb, self)
        self.history = HistoryTab(self.nb, self)
        self.nb.add(self.leftovers, text="  Reste-Finder  ")
        self.nb.add(self.autostart, text="  Autostart  ")
        self.nb.add(self.renamer, text="  Umbenennen  ")
        self.nb.add(self.sync, text="  Ordner-Vergleich  ")
        self.nb.add(self.history, text="  Verlauf / Rückgängig  ")

        self.status = tk.StringVar(value="Bereit.")
        ttk.Label(self, textvariable=self.status, relief="sunken", anchor="w", padding=(6, 2)).pack(fill="x", side="bottom")
        self._autostart_entries = []
        if autoload:
            self.after(200, lambda: self.autostart.load(on_done=self._remember_autostart))

    def _remember_autostart(self, entries):
        self._autostart_entries = entries

    def autostart_targets(self) -> list[str]:
        import os
        return [os.path.dirname(e.target) for e in self._autostart_entries if e.target]

    def destroy(self):
        self.bg.stop()
        super().destroy()

    def set_status(self, text: str) -> None:
        self.status.set(text)

    def elevate(self):
        if relaunch_as_admin():
            self.destroy()
        else:
            messagebox.showinfo("TidyKit", "Neustart mit Adminrechten wurde abgebrochen.")

    def after_undo(self, tx: dict) -> None:
        """Ansichten nach einem Rückgängig aktualisieren."""
        area = tx.get("area")
        if area == "Autostart":
            self.autostart.load()
        elif area == "Umbenenner" and self.renamer.files:
            self.renamer.load_files()
        elif area == "Ordner-Sync" and self.sync.all_items:
            self.sync.compare()


def main():
    App().mainloop()
