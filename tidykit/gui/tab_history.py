"""Tab: Verlauf & Rückgängig."""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk

from ..journal import describe_op
from ..winutil import data_dir, fmt_time
from .common import PreviewDialog, make_tree, show_errors


class HistoryTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=8)
        self.app = app
        ttk.Label(self, wraplength=1000, justify="left", foreground="#555", text=(
            "Jede Änderung, die TidyKit macht, wird hier protokolliert und kann rückgängig gemacht werden: "
            "Gelöschtes kommt aus dem Papierkorb zurück, Umbenennungen werden zurückbenannt, "
            "Autostart-Einträge erhalten ihren alten Zustand.")).pack(fill="x", pady=(0, 6))
        holder = ttk.Frame(self)
        holder.pack(fill="both", expand=True)
        self.tree = make_tree(holder, [("time", "Zeit", 130), ("area", "Bereich", 110),
                                       ("desc", "Beschreibung", 560), ("ops", "Schritte", 70),
                                       ("status", "Status", 160)], height=12)
        self.tree.tag_configure("undone", foreground="#999999")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.show_details())
        self.details = tk.Text(self, height=8, wrap="none")
        self.details.pack(fill="x", pady=6)
        bar = ttk.Frame(self)
        bar.pack(fill="x")
        ttk.Button(bar, text="Rückgängig machen …", command=self.undo).pack(side="left")
        ttk.Button(bar, text="Sicherungsordner öffnen", command=lambda: os.startfile(data_dir())).pack(side="left", padx=6)
        app.journal.on_change(lambda: app.bg.call_in_gui(self.refresh))
        self.refresh()

    def refresh(self):
        self.tree.delete(*self.tree.get_children(""))
        for tx in reversed(self.app.journal.transactions):
            if not tx["ops"] and tx["status"] != "läuft":
                continue
            tags = ("undone",) if tx["status"].startswith("rückgängig") else ()
            self.tree.insert("", "end", iid=tx["id"], tags=tags, values=(
                fmt_time(tx["time"]), tx["area"], tx["description"], len(tx["ops"]), tx["status"]))

    def show_details(self):
        self.details.delete("1.0", "end")
        sel = self.tree.selection()
        if sel:
            tx = self.app.journal.get(sel[0])
            if tx:
                self.details.insert("1.0", "\n".join(describe_op(op) for op in tx["ops"]))

    def undo(self, tx_id=None):
        tx_id = tx_id or (self.tree.selection() or [None])[0]
        tx = self.app.journal.get(tx_id) if tx_id else None
        if not tx:
            self.app.set_status("Bitte einen Eintrag auswählen.")
            return
        if tx["status"] == "rückgängig gemacht":
            self.app.set_status("Dieser Eintrag wurde bereits rückgängig gemacht.")
            return
        later = [t for t in self.app.journal.transactions
                 if t["time"] > tx["time"] and not t["status"].startswith("rückgängig") and t["ops"]]
        warn = (f"\n\nAchtung: Danach wurden noch {len(later)} weitere Änderungen gemacht. "
                "Am sichersten macht man von neu nach alt rückgängig.") if later else ""
        lines = ["RÜCKGÄNGIG  " + describe_op(op) for op in reversed(tx["ops"])]
        if not PreviewDialog.ask(self, "Rückgängig machen", f"„{tx['description']}“ rückgängig machen?{warn}",
                                 lines, "Rückgängig machen"):
            return
        errors = self.app.journal.undo(tx_id)
        show_errors(self, "Nicht alles rückgängig gemacht", errors)
        self.app.set_status("Rückgängig gemacht." if not errors else "Teilweise rückgängig gemacht.")
        self.app.after_undo(tx)
