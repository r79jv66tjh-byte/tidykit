"""Tab: Leere Ordner & Programmreste."""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, ttk

from .. import leftovers
from ..trash import send_to_trash
from ..winutil import fmt_time, human_size, open_in_explorer
from .common import (CHECK_OFF, CHECK_ON, PreviewDialog, checked, enable_checkboxes,
                     make_tree, set_all, show_errors)


class LeftoversTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=8)
        self.app = app
        self.candidates: dict[str, leftovers.Candidate] = {}

        top = ttk.Frame(self)
        top.pack(fill="x")
        ttk.Button(top, text="Programmreste suchen", command=self.scan_programs).pack(side="left")
        ttk.Button(top, text="Leere Ordner suchen in …", command=self.scan_empty_ask).pack(side="left", padx=6)
        ttk.Label(top, text="Mindest-Sicherheit:").pack(side="left", padx=(16, 4))
        self.min_conf = tk.StringVar(value="niedrig")
        cb = ttk.Combobox(top, textvariable=self.min_conf, values=["hoch", "mittel", "niedrig"],
                          width=8, state="readonly")
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda e: self.refill())

        ttk.Label(self, wraplength=1000, justify="left", foreground="#555", text=(
            "Durchsucht AppData (Roaming/Local/LocalLow), ProgramData und Programme nach Ordnern, die keinem "
            "installierten Programm, Dienst, laufenden Prozess oder Autostart-Eintrag zugeordnet werden können. "
            "Es sind Vorschläge – prüfe sie vor dem Löschen. Gelöscht wird nur in den Papierkorb.")
        ).pack(fill="x", pady=6)

        holder = ttk.Frame(self)
        holder.pack(fill="both", expand=True)
        self.tree = make_tree(holder, [
            ("check", "✓", 34), ("path", "Ordner", 420), ("cat", "Art", 100),
            ("conf", "Sicherheit", 80), ("size", "Größe", 80), ("files", "Dateien", 60),
            ("date", "Zuletzt geändert", 120), ("reason", "Begründung", 340)])
        self.tree.sort_keys = {}
        self.tree.tag_configure("hoch", foreground="#1a7f37")
        self.tree.tag_configure("mittel", foreground="#9a6700")
        self.tree.tag_configure("niedrig", foreground="#888888")
        enable_checkboxes(self.tree, on_toggle=self.update_summary)
        self.tree.bind("<Double-1>", lambda e: self.show_selected())

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", pady=(8, 0))
        ttk.Button(bottom, text="Alle „hoch“ ankreuzen",
                   command=lambda: (set_all(self.tree, True, only=lambda i: self.candidates[i].confidence == "hoch"),
                                    self.update_summary())).pack(side="left")
        ttk.Button(bottom, text="Keine", command=lambda: (set_all(self.tree, False), self.update_summary())
                   ).pack(side="left", padx=6)
        ttk.Button(bottom, text="Im Explorer zeigen", command=self.show_selected).pack(side="left")
        self.summary = ttk.Label(bottom, text="")
        self.summary.pack(side="left", padx=12)
        ttk.Button(bottom, text="Angekreuzte in den Papierkorb …", command=self.trash_checked).pack(side="right")

    # --- Suche ---------------------------------------------------------
    def scan_programs(self, roots=None, known=None, on_done=None):
        self.app.set_status("Sammle installierte Programme und durchsuche Ordner …")

        def work():
            kd = known or leftovers.collect_known(extra_paths=self.app.autostart_targets())
            return leftovers.scan_leftovers(roots, kd, progress=self._progress)

        self.app.bg.run(work, lambda res: self._show(res, "Programmreste", on_done))

    def scan_empty_ask(self):
        folder = filedialog.askdirectory(title="Ordner nach leeren Unterordnern durchsuchen")
        if folder:
            self.scan_empty(os.path.normpath(folder))

    def scan_empty(self, folder, on_done=None):
        self.app.set_status(f"Suche leere Ordner in {folder} …")
        self.app.bg.run(lambda: leftovers.find_empty_dirs(folder, progress=self._progress),
                        lambda res: self._show(res, "Leere Ordner", on_done))

    def _progress(self, path):
        self.app.bg.call_in_gui(self.app.set_status, f"Prüfe {path}")

    def _show(self, result, what, on_done=None):
        self.candidates = {}
        self._all = result
        self.refill()
        self.app.set_status(f"{what}: {len(result)} Vorschläge gefunden.")
        if on_done:
            on_done(result)

    def refill(self):
        self.tree.delete(*self.tree.get_children(""))
        self.tree.sort_keys = {}
        limit = leftovers.CONF_ORDER[self.min_conf.get()]
        self.candidates = {}
        for i, c in enumerate(getattr(self, "_all", [])):
            if leftovers.CONF_ORDER[c.confidence] > limit:
                continue
            iid = f"c{i}"
            self.candidates[iid] = c
            size = human_size(c.size) + ("+" if c.capped else "")
            self.tree.insert("", "end", iid=iid, tags=(c.confidence,), values=(
                CHECK_OFF, c.path, c.category, c.confidence, size, c.files,
                fmt_time(c.newest), c.reason))
            self.tree.sort_keys[(iid, "size")] = c.size
            self.tree.sort_keys[(iid, "files")] = c.files
            self.tree.sort_keys[(iid, "date")] = c.newest
            self.tree.sort_keys[(iid, "conf")] = leftovers.CONF_ORDER[c.confidence]
        self.update_summary()

    def update_summary(self):
        sel = [self.candidates[i] for i in checked(self.tree)]
        self.summary.configure(text=f"{len(sel)} angekreuzt, {human_size(sum(c.size for c in sel))}")

    def show_selected(self):
        for iid in self.tree.selection()[:3]:
            open_in_explorer(self.candidates[iid].path)

    # --- Löschen -------------------------------------------------------
    def trash_checked(self):
        sel = [self.candidates[i] for i in checked(self.tree)]
        if not sel:
            self.app.set_status("Nichts angekreuzt.")
            return
        lines = [f"{c.path}    ({human_size(c.size)}, {c.files} Dateien, {c.confidence})" for c in sel]
        total = human_size(sum(c.size for c in sel))
        if not PreviewDialog.ask(self, "Vorschau: In den Papierkorb",
                                 f"Diese {len(sel)} Ordner ({total}) werden in den Papierkorb verschoben. "
                                 "Über den Verlauf-Tab lässt sich das jederzeit rückgängig machen.",
                                 lines, "In den Papierkorb"):
            return
        tx = self.app.journal.begin("Reste-Finder", f"{len(sel)} Ordner in den Papierkorb ({total})")
        errors = []
        for c in sel:
            try:
                tx.add({"op": "trash", "path": c.path, "time": send_to_trash(c.path)})
            except Exception as exc:
                errors.append(f"{c.path}: {exc}")
        tx.finish("fertig" if not errors else "fertig (mit Fehlern)")
        done = {os.path.normcase(op["path"]) for op in tx.data["ops"]}
        self._all = [c for c in self._all if os.path.normcase(c.path) not in done]
        self.refill()
        self.app.set_status(f"{len(done)} Ordner in den Papierkorb verschoben.")
        show_errors(self, "Nicht alles gelöscht", errors + (
            ["Hinweis: Für Programme/ProgramData sind oft Adminrechte nötig."] if errors else []))
