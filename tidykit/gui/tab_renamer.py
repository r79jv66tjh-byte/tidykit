"""Tab: Massen-Umbenenner."""
from __future__ import annotations

import os
import re
import tkinter as tk
from tkinter import filedialog, ttk

from .. import renamer
from ..renamer import Rule
from .common import PreviewDialog, make_tree, show_errors

HELP = {
    "replace": "Ersetzt Text im Namen.",
    "regex": "Regulärer Ausdruck; Gruppen im Ersatz mit \\1 oder $1.",
    "pattern": "Platzhalter: {name} {n} {n:3} (3-stellig) {datum} {datum:%Y%m%d} {ordner} {ext}",
    "case": "Ändert die Groß-/Kleinschreibung.",
    "trim": "Entfernt doppelte Leerzeichen und Leerzeichen am Anfang/Ende.",
}


class RenamerTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=8)
        self.app = app
        self.rules: list[Rule] = []
        self.files: list[str] = []
        self.plan: list[tuple[str, str]] = []

        # Dateiauswahl
        src = ttk.LabelFrame(self, text="Dateien", padding=6)
        src.pack(fill="x")
        self.folder = tk.StringVar()
        self.pattern = tk.StringVar(value="*")
        self.recursive = tk.BooleanVar(value=False)
        self.sort = tk.StringVar(value="Name")
        self.include_ext = tk.BooleanVar(value=False)
        ttk.Label(src, text="Ordner:").grid(row=0, column=0, sticky="w")
        ttk.Entry(src, textvariable=self.folder, width=70).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(src, text="…", width=3, command=self.pick_folder).grid(row=0, column=2)
        ttk.Label(src, text="Filter:").grid(row=0, column=3, padx=(12, 2))
        ttk.Entry(src, textvariable=self.pattern, width=14).grid(row=0, column=4)
        ttk.Checkbutton(src, text="Unterordner", variable=self.recursive,
                        command=self.load_files).grid(row=0, column=5, padx=6)
        ttk.Label(src, text="Sortierung:").grid(row=0, column=6)
        sort_cb = ttk.Combobox(src, textvariable=self.sort, values=["Name", "Datum", "Größe"],
                               width=7, state="readonly")
        sort_cb.grid(row=0, column=7, padx=2)
        sort_cb.bind("<<ComboboxSelected>>", lambda e: self.load_files())
        ttk.Checkbutton(src, text="Endung mit umbenennen", variable=self.include_ext,
                        command=self.update_preview).grid(row=0, column=8, padx=6)
        ttk.Button(src, text="Laden", command=self.load_files).grid(row=0, column=9)
        src.columnconfigure(1, weight=1)

        # Regeln
        mid = ttk.Frame(self)
        mid.pack(fill="x", pady=6)
        rules_box = ttk.LabelFrame(mid, text="Regeln (werden von oben nach unten angewendet)", padding=6)
        rules_box.pack(side="left", fill="both", expand=True)
        self.rule_list = tk.Listbox(rules_box, height=6, activestyle="none")
        self.rule_list.pack(side="left", fill="both", expand=True)
        rb = ttk.Frame(rules_box)
        rb.pack(side="left", fill="y", padx=4)
        for text, cmd in (("▲", lambda: self.move_rule(-1)), ("▼", lambda: self.move_rule(1)),
                          ("An/Aus", self.toggle_rule), ("Entfernen", self.remove_rule)):
            ttk.Button(rb, text=text, command=cmd).pack(fill="x", pady=1)

        editor = ttk.LabelFrame(mid, text="Neue Regel", padding=6)
        editor.pack(side="left", fill="y", padx=(8, 0))
        self.kind = tk.StringVar(value=renamer.KINDS["replace"])
        kind_cb = ttk.Combobox(editor, textvariable=self.kind, values=list(renamer.KINDS.values()),
                               state="readonly", width=24)
        kind_cb.grid(row=0, column=0, columnspan=2, sticky="w")
        kind_cb.bind("<<ComboboxSelected>>", lambda e: self.build_editor())
        self.fields_frame = ttk.Frame(editor)
        self.fields_frame.grid(row=1, column=0, columnspan=2, sticky="w", pady=4)
        self.help = ttk.Label(editor, foreground="#555", wraplength=330, justify="left")
        self.help.grid(row=2, column=0, columnspan=2, sticky="w")
        ttk.Button(editor, text="Regel hinzufügen", command=self.add_rule).grid(row=3, column=0, sticky="w", pady=4)
        self.vars: dict[str, tk.Variable] = {}
        self.build_editor()

        # Vorschau
        holder = ttk.Frame(self)
        holder.pack(fill="both", expand=True)
        self.tree = make_tree(holder, [("old", "Alter Name", 360), ("new", "Neuer Name", 360),
                                       ("status", "Status", 160), ("dir", "Ordner", 300)])
        self.tree.tag_configure("err", foreground="#b42318")
        self.tree.tag_configure("same", foreground="#999999")
        self.tree.tag_configure("ok", foreground="#1a7f37")

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", pady=(8, 0))
        self.summary = ttk.Label(bottom, text="")
        self.summary.pack(side="left")
        ttk.Button(bottom, text="Umbenennen …", command=self.execute).pack(side="right")

    # --- Regel-Editor ---------------------------------------------------
    def current_kind(self) -> str:
        return next(k for k, v in renamer.KINDS.items() if v == self.kind.get())

    def build_editor(self):
        for w in self.fields_frame.winfo_children():
            w.destroy()
        kind = self.current_kind()
        self.vars = {}

        def entry(row, label, key, default="", width=24):
            ttk.Label(self.fields_frame, text=label).grid(row=row, column=0, sticky="w")
            v = tk.StringVar(value=default)
            ttk.Entry(self.fields_frame, textvariable=v, width=width).grid(row=row, column=1, sticky="w", padx=4)
            self.vars[key] = v

        def check(row, label, key, default=False):
            v = tk.BooleanVar(value=default)
            ttk.Checkbutton(self.fields_frame, text=label, variable=v).grid(row=row, column=0, columnspan=2, sticky="w")
            self.vars[key] = v

        if kind == "replace":
            entry(0, "Suchen:", "find")
            entry(1, "Ersetzen durch:", "repl")
            check(2, "Groß/Klein beachten", "case_sensitive")
        elif kind == "regex":
            entry(0, "Muster:", "pattern")
            entry(1, "Ersatz:", "repl")
            check(2, "Groß/Klein beachten", "case_sensitive", True)
        elif kind == "pattern":
            entry(0, "Muster:", "template", "{name}_{n:3}")
            entry(1, "Start:", "start", "1", 6)
            entry(2, "Schritt:", "step", "1", 6)
        elif kind == "case":
            ttk.Label(self.fields_frame, text="Modus:").grid(row=0, column=0, sticky="w")
            v = tk.StringVar(value=renamer.CASE_MODES["lower"])
            ttk.Combobox(self.fields_frame, textvariable=v, values=list(renamer.CASE_MODES.values()),
                         state="readonly", width=20).grid(row=0, column=1, padx=4)
            self.vars["mode"] = v
        elif kind == "trim":
            check(0, "Unterstriche zu Leerzeichen", "underscores")
        self.help.configure(text=HELP[kind])

    def add_rule(self, rule: Rule | None = None):
        if rule is None:
            kind = self.current_kind()
            params = {k: v.get() for k, v in self.vars.items()}
            if kind == "case":
                params["mode"] = next(k for k, v in renamer.CASE_MODES.items() if v == params["mode"])
            if kind == "pattern":
                try:
                    params["start"], params["step"] = int(params["start"]), int(params["step"])
                except ValueError:
                    self.app.set_status("Start und Schritt müssen Zahlen sein.")
                    return
            if kind == "regex":
                try:
                    re.compile(params["pattern"])
                except re.error as exc:
                    self.app.set_status(f"Ungültiger regulärer Ausdruck: {exc}")
                    return
            rule = Rule(kind, params)
        self.rules.append(rule)
        self.refresh_rules()

    def refresh_rules(self):
        self.rule_list.delete(0, "end")
        for r in self.rules:
            self.rule_list.insert("end", ("" if r.enabled else "(aus) ") + r.describe())
        self.update_preview()

    def _sel_rule(self):
        s = self.rule_list.curselection()
        return s[0] if s else None

    def move_rule(self, d):
        i = self._sel_rule()
        if i is None or not 0 <= i + d < len(self.rules):
            return
        self.rules[i], self.rules[i + d] = self.rules[i + d], self.rules[i]
        self.refresh_rules()
        self.rule_list.selection_set(i + d)

    def toggle_rule(self):
        i = self._sel_rule()
        if i is not None:
            self.rules[i].enabled = not self.rules[i].enabled
            self.refresh_rules()
            self.rule_list.selection_set(i)

    def remove_rule(self):
        i = self._sel_rule()
        if i is not None:
            del self.rules[i]
            self.refresh_rules()

    # --- Dateien & Vorschau ---------------------------------------------
    def pick_folder(self):
        f = filedialog.askdirectory(title="Ordner mit den Dateien")
        if f:
            self.folder.set(os.path.normpath(f))
            self.load_files()

    def load_files(self):
        folder = self.folder.get().strip()
        if not os.path.isdir(folder):
            self.app.set_status("Bitte einen vorhandenen Ordner wählen.")
            return
        files = renamer.list_files(folder, self.pattern.get(), self.recursive.get())
        key = {"Name": "name", "Datum": "date", "Größe": "size"}[self.sort.get()]
        self.files = renamer.sort_files(files, key)
        self.update_preview()

    def update_preview(self):
        try:
            self.plan = renamer.build_plan(self.files, self.rules, self.include_ext.get())
        except (re.error, ValueError, IndexError) as exc:
            self.app.set_status(f"Regel-Fehler: {exc}")
            self.plan = [(f, os.path.basename(f)) for f in self.files]
        status = renamer.check_plan(self.plan)
        self.tree.delete(*self.tree.get_children(""))
        for i, ((src, new), st) in enumerate(zip(self.plan, status)):
            tag = "err" if st.startswith("⚠") else "same" if st == "unverändert" else "ok"
            self.tree.insert("", "end", iid=f"f{i}", tags=(tag,),
                             values=(os.path.basename(src), new, st, os.path.dirname(src)))
        n_ok = status.count("ok")
        n_err = sum(1 for s in status if s.startswith("⚠"))
        self.summary.configure(text=f"{len(self.plan)} Dateien · {n_ok} werden umbenannt · {n_err} Konflikte")
        self.status_list = status

    def execute(self):
        self.update_preview()
        if any(s.startswith("⚠") for s in self.status_list):
            show_errors(self, "Konflikte", ["Bitte erst die rot markierten Konflikte beheben."])
            self.app.set_status("Konflikte vorhanden – nichts umbenannt.")
            return
        pairs = renamer.executable_pairs(self.plan)
        lines = [f"{os.path.basename(s)}  →  {os.path.basename(d)}" for s, d in pairs]
        if not PreviewDialog.ask(self, "Vorschau: Umbenennen",
                                 f"{len(pairs)} Dateien umbenennen? (rückgängig über den Verlauf-Tab)",
                                 lines, "Umbenennen"):
            return
        tx = self.app.journal.begin("Umbenenner", f"{len(pairs)} Dateien umbenannt in {self.folder.get()}")
        try:
            done = renamer.execute_renames(pairs)
        except Exception as exc:
            tx.finish("fehlgeschlagen")
            show_errors(self, "Umbenennen fehlgeschlagen", [f"{exc} – nichts wurde geändert."])
            return
        tx.add({"op": "rename_batch", "pairs": [list(p) for p in done]})
        tx.finish()
        self.app.set_status(f"{len(done)} Dateien umbenannt.")
        self.load_files()
