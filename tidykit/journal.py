"""Verlauf aller Änderungen mit Rückgängig-Funktion.

Jede Aktion des Tools ist eine Transaktion aus Einzelschritten (ops). Jeder Schritt
wird sofort auf die Platte geschrieben, damit auch ein Absturz rückgängig gemacht
werden kann. Rückgängig läuft die Schritte in umgekehrter Reihenfolge ab.

Schritt-Typen:
  trash        {path, time}                 -> aus dem Papierkorb wiederherstellen
  created      {path}                       -> neu angelegte Datei/Ordner in den Papierkorb
  rename_batch {pairs: [[alt, neu], ...]}   -> zurückbenennen
  reg_value    {root, key, name, view, old} -> alten Registry-Wert zurückschreiben
  task_state   {path, name, enabled}        -> Aufgabe wieder (de)aktivieren
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid

from .winutil import data_dir


class Transaction:
    def __init__(self, journal: "Journal", data: dict):
        self._journal = journal
        self.data = data

    @property
    def id(self) -> str:
        return self.data["id"]

    def add(self, op: dict) -> None:
        self.data["ops"].append(op)
        self._journal.save()

    def finish(self, status: str = "fertig") -> None:
        self.data["status"] = status
        self._journal.save()
        self._journal.notify()


class Journal:
    def __init__(self, path: str | None = None):
        self.path = path or os.path.join(data_dir(), "verlauf.json")
        self._lock = threading.RLock()
        self.transactions: list[dict] = []
        self._listeners = []
        self.load()

    # --- Persistenz -------------------------------------------------------
    def load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as fh:
                self.transactions = json.load(fh)
        except (OSError, ValueError):
            self.transactions = []

    def save(self) -> None:
        with self._lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.transactions, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)

    def on_change(self, callback) -> None:
        self._listeners.append(callback)

    def notify(self) -> None:
        for cb in list(self._listeners):
            try:
                cb()
            except Exception:
                pass

    # --- Transaktionen ----------------------------------------------------
    def begin(self, area: str, description: str) -> Transaction:
        data = {"id": uuid.uuid4().hex[:12], "time": time.time(), "area": area,
                "description": description, "status": "läuft", "ops": []}
        with self._lock:
            self.transactions.append(data)
            self.save()
        return Transaction(self, data)

    def get(self, tx_id: str) -> dict | None:
        for t in self.transactions:
            if t["id"] == tx_id:
                return t
        return None

    def undo(self, tx_id: str) -> list[str]:
        """Macht eine Transaktion rückgängig. Gibt eine Liste von Fehlermeldungen zurück."""
        tx = self.get(tx_id)
        if tx is None:
            return ["Eintrag nicht gefunden."]
        if tx["status"] == "rückgängig gemacht":
            return ["Wurde bereits rückgängig gemacht."]
        errors: list[str] = []
        remaining = []
        for op in reversed(tx["ops"]):
            try:
                undo_op(op)
            except Exception as exc:  # weiter mit den anderen Schritten
                errors.append(f"{describe_op(op)}: {exc}")
                remaining.append(op)
        if errors:
            tx["status"] = "teilweise rückgängig"
            tx["ops"] = list(reversed(remaining))
        else:
            tx["status"] = "rückgängig gemacht"
        tx["undone_at"] = time.time()
        self.save()
        self.notify()
        return errors


def describe_op(op: dict) -> str:
    kind = op.get("op")
    if kind == "trash":
        return f"Papierkorb: {op['path']}"
    if kind == "created":
        return f"Angelegt: {op['path']}"
    if kind == "rename_batch":
        return f"{len(op['pairs'])} Umbenennungen"
    if kind == "reg_value":
        return f"Registry: {op['root']}\\{op['key']} [{op['name']}]"
    if kind == "task_state":
        return f"Aufgabe: {op['path']}{op['name']}"
    return str(op)


def undo_op(op: dict) -> None:
    kind = op.get("op")
    if kind == "trash":
        from .trash import restore_from_trash
        restore_from_trash(op["path"], op.get("time"))
    elif kind == "created":
        from .trash import send_to_trash
        if os.path.lexists(op["path"]):
            send_to_trash(op["path"])
    elif kind == "rename_batch":
        from .renamer import execute_renames
        execute_renames([(new, old) for old, new in op["pairs"]])
    elif kind == "reg_value":
        from .autostart import restore_reg_value
        restore_reg_value(op)
    elif kind == "task_state":
        from .autostart import set_task_enabled
        set_task_enabled(op["path"], op["name"], op["enabled"])
    else:
        raise ValueError(f"Unbekannter Schritt: {kind}")
