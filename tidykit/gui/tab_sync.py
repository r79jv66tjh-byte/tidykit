"""Tab: Ordner-Vergleich & Abgleich."""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, ttk

from .. import foldersync as fs
from ..winutil import fmt_time, human_size
from .common import PreviewDialog, make_tree, show_errors

SYMBOL = {"nur_a": "◀ nur A", "nur_b": "nur B ▶", "gleich": "=", "neuer_a": "A neuer",
          "neuer_b": "B neuer", "verschieden": "≠", "typ": "Typ ≠"}


class SyncTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=8)
        self.app = app
        self.items: dict[str, fs.DiffItem] = {}
        self.all_items: list[fs.DiffItem] = []

        paths = ttk.Frame(self)
        paths.pack(fill="x")
        self.a = tk.StringVar()
        self.b = tk.StringVar()
        for row, (label, var) in enumerate((("Ordner A:", self.a), ("Ordner B:", self.b))):
            ttk.Label(paths, text=label).grid(row=row, column=0, sticky="w")
            ttk.Entry(paths, textvariable=var, width=90).grid(row=row, column=1, sticky="ew", padx=4, pady=1)
            ttk.Button(paths, text="…", width=3, command=lambda v=var: self.pick(v)).grid(row=row, column=2)
        paths.columnconfigure(1, weight=1)

        opts = ttk.Frame(self)
        opts.pack(fill="x", pady=6)
        self.use_hash = tk.BooleanVar(value=False)
        ttk.Button(opts, text="Vergleichen", command=self.compare).pack(side="left")
        ttk.Checkbutton(opts, text="Inhalt prüfen (langsamer, SHA-256)", variable=self.use_hash).pack(side="left", padx=8)
        ttk.Label(opts, text="Zeigen:").pack(side="left", padx=(16, 4))
        self.show_equal = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="gleiche", variable=self.show_equal, command=self.refill).pack(side="left")

        holder = ttk.Frame(self)
        holder.pack(fill="both", expand=True)
        self.tree = make_tree(holder, [
            ("rel", "Pfad (relativ)", 380), ("status", "Vergleich", 80), ("action", "Aktion", 130),
            ("size_a", "Größe A", 80), ("date_a", "Datum A", 120),
            ("size_b", "Größe B", 80), ("date_b", "Datum B", 120)])
        self.tree.sort_keys = {}
        for tag, color in (("nur_a", "#0b5cad"), ("nur_b", "#8250df"), ("neuer_a", "#9a6700"),
                           ("neuer_b", "#9a6700"), ("verschieden", "#b42318"), ("typ", "#b42318"),
                           ("gleich", "#999999")):
            self.tree.tag_configure(tag, foreground=color)

        act = ttk.Frame(self)
        act.pack(fill="x", pady=(8, 0))
        ttk.Label(act, text="Vorschlag:").pack(side="left")
        ttk.Button(act, text="A → B spiegeln", command=lambda: self.suggest("a2b")).pack(side="left", padx=2)
        ttk.Button(act, text="B → A spiegeln", command=lambda: self.suggest("b2a")).pack(side="left", padx=2)
        ttk.Button(act, text="Beidseitig (Neueres gewinnt)", command=lambda: self.suggest("both")).pack(side="left", padx=2)
        self.delete_extra = tk.BooleanVar(value=False)
        ttk.Checkbutton(act, text="Überzählige im Ziel löschen", variable=self.delete_extra).pack(side="left", padx=6)

        act2 = ttk.Frame(self)
        act2.pack(fill="x", pady=(4, 0))
        ttk.Label(act2, text="Auswahl:").pack(side="left")
        for action in ("a2b", "b2a", "del_a", "del_b", ""):
            ttk.Button(act2, text=fs.ACTION_TEXT[action] or "keine Aktion",
                       command=lambda a=action: self.set_action(a)).pack(side="left", padx=2)
        self.summary = ttk.Label(act2, text="")
        self.summary.pack(side="left", padx=12)
        ttk.Button(act2, text="Ausführen …", command=self.execute).pack(side="right")

    def pick(self, var):
        f = filedialog.askdirectory()
        if f:
            var.set(os.path.normpath(f))

    def compare(self, on_done=None):
        a, b = self.a.get().strip(), self.b.get().strip()
        if not (os.path.isdir(a) and os.path.isdir(b)):
            self.app.set_status("Bitte zwei vorhandene Ordner angeben.")
            return
        if os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b)):
            self.app.set_status("A und B sind derselbe Ordner.")
            return
        self.app.set_status("Vergleiche …")
        use_hash = self.use_hash.get()
        self.app.bg.run(lambda: fs.compare(a, b, use_hash, lambda r: self.app.bg.call_in_gui(
            self.app.set_status, f"Vergleiche {r}")), lambda res: self._show(res, on_done))

    def _show(self, items, on_done=None):
        self.all_items = items
        self.refill()
        diff = sum(1 for i in items if i.status != "gleich")
        self.app.set_status(f"{len(items)} Einträge verglichen, {diff} Unterschiede.")
        if on_done:
            on_done(items)

    def refill(self):
        self.tree.delete(*self.tree.get_children(""))
        self.items = {}
        self.tree.sort_keys = {}
        for i, it in enumerate(self.all_items):
            if it.status == "gleich" and not self.show_equal.get():
                continue
            iid = f"d{i}"
            self.items[iid] = it
            rel = it.rel + ("\\" if it.is_dir else "")
            self.tree.insert("", "end", iid=iid, tags=(it.status,), values=(
                rel, SYMBOL[it.status], fs.ACTION_TEXT[it.action],
                human_size(it.size_a) if it.size_a is not None else ("Ordner" if it.is_dir and it.mtime_a else ""),
                fmt_time(it.mtime_a),
                human_size(it.size_b) if it.size_b is not None else ("Ordner" if it.is_dir and it.mtime_b else ""),
                fmt_time(it.mtime_b)))
            self.tree.sort_keys[(iid, "size_a")] = it.size_a or 0
            self.tree.sort_keys[(iid, "size_b")] = it.size_b or 0
            self.tree.sort_keys[(iid, "date_a")] = it.mtime_a or 0
            self.tree.sort_keys[(iid, "date_b")] = it.mtime_b or 0
        self.update_summary()

    def update_summary(self):
        n = sum(1 for it in self.all_items if it.action)
        self.summary.configure(text=f"{n} Aktionen geplant")

    def suggest(self, mode):
        fs.suggest(self.all_items, mode, self.delete_extra.get())
        self.refill()

    def set_action(self, action):
        skipped = 0
        for iid in self.tree.selection():
            it = self.items[iid]
            if action in fs.allowed_actions(it):
                it.action = action
                self.tree.set(iid, "action", fs.ACTION_TEXT[action])
            else:
                skipped += 1
        self.update_summary()
        if skipped:
            self.app.set_status(f"{skipped} Einträge übersprungen (Aktion passt nicht).")

    def execute(self):
        a, b = self.a.get().strip(), self.b.get().strip()
        lines = fs.plan_lines(self.all_items, a, b)
        if not lines:
            self.app.set_status("Keine Aktionen geplant.")
            return
        if not PreviewDialog.ask(self, "Vorschau: Abgleich",
                                 f"{len(lines)} Aktionen ausführen? Überschriebene und gelöschte Dateien "
                                 "landen im Papierkorb; alles ist über den Verlauf-Tab rückgängig zu machen.",
                                 lines):
            return
        tx = self.app.journal.begin("Ordner-Sync", f"{len(lines)} Aktionen: {a}  ⇄  {b}")
        errors = fs.execute(self.all_items, a, b, tx)
        tx.finish("fertig" if not errors else "fertig (mit Fehlern)")
        show_errors(self, "Nicht alles abgeglichen", errors)
        self.compare()
