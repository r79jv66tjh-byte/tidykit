"""Gemeinsame GUI-Bausteine."""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk

CHECK_ON = "☑"
CHECK_OFF = "☐"


class Background:
    """Führt Funktionen im Hintergrund aus und ruft das Ergebnis im GUI-Thread auf."""

    def __init__(self, root: tk.Misc):
        self.root = root
        self.q: queue.Queue = queue.Queue()
        self.busy = 0
        self._poll()

    def _poll(self):
        try:
            while True:
                cb, args = self.q.get_nowait()
                cb(*args)
        except queue.Empty:
            pass
        self._after = self.root.after(60, self._poll)

    def stop(self):
        try:
            self.root.after_cancel(self._after)
        except Exception:
            pass

    def call_in_gui(self, cb, *args):
        self.q.put((cb, args))

    def run(self, func, on_done, on_error=None):
        self.busy += 1

        def worker():
            try:
                result = func()
            except Exception as exc:  # an die GUI weiterreichen
                self.q.put((self._finish_err, (exc, on_error)))
            else:
                self.q.put((self._finish_ok, (result, on_done)))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_ok(self, result, cb):
        self.busy -= 1
        cb(result)

    def _finish_err(self, exc, cb):
        self.busy -= 1
        if cb:
            cb(exc)
        else:
            from tkinter import messagebox
            messagebox.showerror("Fehler", str(exc))


def make_tree(parent, columns: list[tuple[str, str, int]], height: int = 18) -> ttk.Treeview:
    """columns: (id, Überschrift, Breite). Liefert eine Treeview mit Scrollbalken."""
    frame = ttk.Frame(parent)
    frame.pack(fill="both", expand=True)
    tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show="headings",
                        height=height, selectmode="extended")
    for cid, text, width in columns:
        tree.heading(cid, text=text, command=lambda c=cid: sort_tree(tree, c, False))
        tree.column(cid, width=width, stretch=width > 150, anchor="w")
    ys = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    xs = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
    tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
    tree.grid(row=0, column=0, sticky="nsew")
    ys.grid(row=0, column=1, sticky="ns")
    xs.grid(row=1, column=0, sticky="ew")
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    return tree


def sort_tree(tree: ttk.Treeview, col: str, reverse: bool) -> None:
    def key(iid):
        sk = getattr(tree, "sort_keys", {}).get((iid, col))
        return (0, sk) if sk is not None else (1, str(tree.set(iid, col)).lower())
    items = list(tree.get_children(""))
    try:
        items.sort(key=key, reverse=reverse)
    except TypeError:
        items.sort(key=lambda i: str(tree.set(i, col)).lower(), reverse=reverse)
    for idx, iid in enumerate(items):
        tree.move(iid, "", idx)
    tree.heading(col, command=lambda: sort_tree(tree, col, not reverse))


def enable_checkboxes(tree: ttk.Treeview, col: str = "check", on_toggle=None) -> None:
    """Klick in die Spalte `col` schaltet ☐/☑; Leertaste schaltet die Auswahl."""

    def toggle(iids):
        for iid in iids:
            cur = tree.set(iid, col)
            tree.set(iid, col, CHECK_OFF if cur == CHECK_ON else CHECK_ON)
        if on_toggle:
            on_toggle()

    def click(event):
        if tree.identify_region(event.x, event.y) != "cell":
            return
        column = tree.identify_column(event.x)
        if tree["columns"][int(column[1:]) - 1] != col:
            return
        iid = tree.identify_row(event.y)
        if iid:
            toggle([iid])
            return "break"

    tree.bind("<Button-1>", click, add="+")
    tree.bind("<space>", lambda e: toggle(tree.selection()))


def checked(tree: ttk.Treeview, col: str = "check") -> list[str]:
    return [i for i in tree.get_children("") if tree.set(i, col) == CHECK_ON]


def set_all(tree: ttk.Treeview, value: bool, col: str = "check", only=None) -> None:
    for iid in tree.get_children(""):
        if only is None or only(iid):
            tree.set(iid, col, CHECK_ON if value else CHECK_OFF)


class PreviewDialog(tk.Toplevel):
    """Zeigt geplante Änderungen und fragt, ob sie ausgeführt werden sollen."""

    auto_answer: bool | None = None  # für automatisierte Tests

    def __init__(self, parent, title: str, intro: str, lines: list[str], ok_text: str = "Ausführen"):
        super().__init__(parent)
        self.title(title)
        self.transient(parent.winfo_toplevel())
        self.result = False
        ttk.Label(self, text=intro, wraplength=760, justify="left").pack(anchor="w", padx=12, pady=(12, 6))
        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True, padx=12)
        txt = tk.Text(frame, width=110, height=min(24, max(6, len(lines) + 1)), wrap="none")
        ys = ttk.Scrollbar(frame, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=ys.set)
        txt.pack(side="left", fill="both", expand=True)
        ys.pack(side="right", fill="y")
        txt.insert("1.0", "\n".join(lines) if lines else "(nichts zu tun)")
        txt.configure(state="disabled")
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=12, pady=12)
        ok = ttk.Button(bar, text=ok_text, command=self._ok)
        ok.pack(side="right")
        ttk.Button(bar, text="Abbrechen", command=self.destroy).pack(side="right", padx=6)
        if not lines:
            ok.state(["disabled"])
        self.bind("<Escape>", lambda e: self.destroy())

    def _ok(self):
        self.result = True
        self.destroy()

    @classmethod
    def ask(cls, parent, title, intro, lines, ok_text="Ausführen") -> bool:
        if cls.auto_answer is not None:
            return cls.auto_answer and bool(lines)
        dlg = cls(parent, title, intro, lines, ok_text)
        dlg.grab_set()
        parent.wait_window(dlg)
        return dlg.result


def show_errors(parent, title: str, errors: list[str]) -> None:
    if not errors:
        return
    if PreviewDialog.auto_answer is not None:
        print(f"[{title}]", *errors, sep="\n  ")
        return
    from tkinter import messagebox
    shown = errors[:15]
    more = f"\n… und {len(errors) - 15} weitere" if len(errors) > 15 else ""
    messagebox.showwarning(title, "\n".join(shown) + more, parent=parent)
