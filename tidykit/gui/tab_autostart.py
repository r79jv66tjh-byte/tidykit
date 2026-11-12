"""Tab: Autostart-Manager."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .. import autostart
from ..winutil import is_admin, open_in_explorer
from .common import PreviewDialog, make_tree, show_errors

RATING_ORDER = {"Verwaist": 0, "Verdächtig": 1, "Prüfen": 2, "Optional": 3, "Unklar": 4,
                "Normal": 5, "System": 6}


class AutostartTab(ttk.Frame):
    def __init__(self, parent, app, sources=None):
        super().__init__(parent, padding=8)
        self.app = app
        self.sources = sources
        self.entries: dict[str, autostart.StartupEntry] = {}

        top = ttk.Frame(self)
        top.pack(fill="x")
        ttk.Button(top, text="Neu laden", command=self.load).pack(side="left")
        self.show_ms = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="Windows-Aufgaben (\\Microsoft\\) zeigen", variable=self.show_ms,
                        command=self.load).pack(side="left", padx=12)
        self.check_sig = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="Signaturen prüfen", variable=self.check_sig,
                        command=self.load).pack(side="left")

        ttk.Label(self, wraplength=1000, justify="left", foreground="#555", text=(
            "Ausschalten funktioniert wie im Task-Manager: Der Eintrag bleibt erhalten und wird nur deaktiviert. "
            "Vor jeder Änderung wird der alte Zustand gesichert (Verlauf + JSON in den Sicherungen). "
            "Einträge für „System“/„Alle“ brauchen Adminrechte.")).pack(fill="x", pady=6)

        holder = ttk.Frame(self)
        holder.pack(fill="both", expand=True)
        self.tree = make_tree(holder, [
            ("state", "Status", 60), ("rating", "Bewertung", 90), ("name", "Name", 220),
            ("source", "Quelle", 170), ("note", "Hinweis", 230), ("target", "Ziel", 380),
            ("cmd", "Befehl", 300)])
        self.tree.sort_keys = {}
        for tag, color in (("Verwaist", "#b42318"), ("Verdächtig", "#b42318"), ("Prüfen", "#9a6700"),
                           ("Optional", "#0b5cad"), ("System", "#888888"), ("aus", "#999999")):
            self.tree.tag_configure(tag, foreground=color)
        self.tree.bind("<Double-1>", lambda e: self.open_target())

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", pady=(8, 0))
        ttk.Button(bottom, text="Ausschalten …", command=lambda: self.switch(False)).pack(side="left")
        ttk.Button(bottom, text="Einschalten …", command=lambda: self.switch(True)).pack(side="left", padx=6)
        ttk.Button(bottom, text="Speicherort zeigen", command=self.open_target).pack(side="left")
        if not is_admin():
            ttk.Label(bottom, text="(ohne Adminrechte: nur Benutzer-Einträge schaltbar)",
                      foreground="#9a6700").pack(side="left", padx=12)

    def load(self, on_done=None):
        self.app.set_status("Lese Autostart-Einträge …")
        inc_ms, sig = self.show_ms.get(), self.check_sig.get()
        self._generation = getattr(self, "_generation", 0) + 1
        gen = self._generation
        self.app.bg.run(lambda: autostart.list_all(True, inc_ms, sig, self.sources),
                        lambda res: self._show(res, on_done, gen))

    def _show(self, entries, on_done=None, gen=None):
        if gen is not None and gen != self._generation:
            return  # ein neuerer Ladevorgang läuft – veraltetes Ergebnis verwerfen
        self.tree.delete(*self.tree.get_children(""))
        self.entries = {}
        self.tree.sort_keys = {}
        entries.sort(key=lambda e: (RATING_ORDER.get(e.rating, 9), e.source, e.name.lower()))
        for i, e in enumerate(entries):
            iid = f"e{i}"
            self.entries[iid] = e
            state = "an" if e.enabled else "aus"
            tags = (e.rating,) if e.enabled else ("aus",)
            self.tree.insert("", "end", iid=iid, tags=tags, values=(
                state, e.rating, e.name, e.source, e.note, e.target, e.command))
            self.tree.sort_keys[(iid, "rating")] = RATING_ORDER.get(e.rating, 9)
        on = sum(1 for e in entries if e.enabled)
        self.app.set_status(f"{len(entries)} Autostart-Einträge, davon {on} aktiv.")
        if on_done:
            on_done(entries)

    def selected(self):
        return [self.entries[i] for i in self.tree.selection() if i in self.entries]

    def open_target(self):
        for e in self.selected()[:3]:
            open_in_explorer(e.file_path or e.target)

    def switch(self, enable: bool, entries=None):
        sel = [e for e in (entries or self.selected()) if e.kind != "info" and e.enabled != enable]
        if not sel:
            self.app.set_status("Keine passenden Einträge ausgewählt (schon im gewünschten Zustand?).")
            return
        verb = "einschalten" if enable else "ausschalten"
        lines = [f"{'EIN' if enable else 'AUS'}  {e.name}   [{e.source}]   {e.target}"
                 + ("   (braucht Adminrechte)" if e.needs_admin and not is_admin() else "") for e in sel]
        if not PreviewDialog.ask(self, f"Autostart {verb}",
                                 f"Diese {len(sel)} Einträge {verb}? Der alte Zustand wird gesichert und "
                                 "lässt sich im Verlauf-Tab wiederherstellen.", lines, verb.capitalize()):
            return
        tx = self.app.journal.begin("Autostart", f"{len(sel)} Einträge {verb}: "
                                    + ", ".join(e.name for e in sel[:4]) + (" …" if len(sel) > 4 else ""))
        errors = []
        for e in sel:
            try:
                autostart.set_enabled(e, enable, tx)
            except PermissionError:
                errors.append(f"{e.name}: Zugriff verweigert – Adminrechte nötig")
            except Exception as exc:
                errors.append(f"{e.name}: {exc}")
        tx.finish("fertig" if not errors else "fertig (mit Fehlern)")
        show_errors(self, "Nicht alles geschaltet", errors)
        self.load()
