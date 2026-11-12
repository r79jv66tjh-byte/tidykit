"""Ordner-Vergleich & Abgleich."""
from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass

from .trash import send_to_trash

MTIME_TOLERANCE = 2.0  # FAT/Netzlaufwerke runden auf 2 Sekunden

STATUS_TEXT = {
    "nur_a": "nur in A",
    "nur_b": "nur in B",
    "gleich": "gleich",
    "neuer_a": "A ist neuer",
    "neuer_b": "B ist neuer",
    "verschieden": "unterschiedlich",
    "typ": "Datei ↔ Ordner",
}

ACTION_TEXT = {
    "": "",
    "a2b": "A → B kopieren",
    "b2a": "B → A kopieren",
    "del_a": "in A löschen",
    "del_b": "in B löschen",
}


@dataclass
class DiffItem:
    rel: str
    is_dir: bool
    status: str
    size_a: int | None = None
    size_b: int | None = None
    mtime_a: float | None = None
    mtime_b: float | None = None
    action: str = ""


def _hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _scan(folder: str) -> dict[str, os.DirEntry]:
    out = {}
    try:
        with os.scandir(folder) as it:
            for e in it:
                out[e.name.lower()] = e
    except OSError:
        pass
    return out


def dir_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def compare(a: str, b: str, use_hash: bool = False, progress=None) -> list[DiffItem]:
    items: list[DiffItem] = []

    def walk(rel: str) -> None:
        ea = _scan(os.path.join(a, rel))
        eb = _scan(os.path.join(b, rel))
        if progress:
            progress(rel or ".")
        for key in sorted(set(ea) | set(eb)):
            xa, xb = ea.get(key), eb.get(key)
            name = (xa or xb).name
            r = os.path.join(rel, name) if rel else name
            if xa and not xb:
                st = xa.stat()
                isdir = xa.is_dir()
                items.append(DiffItem(r, isdir, "nur_a", None if isdir else st.st_size, None, st.st_mtime))
                continue
            if xb and not xa:
                st = xb.stat()
                isdir = xb.is_dir()
                items.append(DiffItem(r, isdir, "nur_b", None, None if isdir else st.st_size, None, st.st_mtime))
                continue
            da, db = xa.is_dir(), xb.is_dir()
            if da and db:
                walk(r)
                continue
            sa, sb = xa.stat(), xb.stat()
            if da != db:
                items.append(DiffItem(r, False, "typ", None, None, sa.st_mtime, sb.st_mtime))
                continue
            item = DiffItem(r, False, "gleich", sa.st_size, sb.st_size, sa.st_mtime, sb.st_mtime)
            same_time = abs(sa.st_mtime - sb.st_mtime) <= MTIME_TOLERANCE
            if sa.st_size == sb.st_size and (same_time or use_hash):
                if use_hash and _hash(xa.path) != _hash(xb.path):
                    item.status = _newer(sa.st_mtime, sb.st_mtime)
                elif not same_time and use_hash:
                    item.status = "gleich"   # Inhalt identisch, nur Zeitstempel anders
            elif same_time:
                item.status = "verschieden"
            else:
                item.status = _newer(sa.st_mtime, sb.st_mtime)
            items.append(item)

    walk("")
    return items


def _newer(ma: float, mb: float) -> str:
    if abs(ma - mb) <= MTIME_TOLERANCE:
        return "verschieden"
    return "neuer_a" if ma > mb else "neuer_b"


def suggest(items: list[DiffItem], mode: str, delete_extra: bool = False) -> None:
    """Setzt Aktionen: mode 'a2b' (A nach B spiegeln), 'b2a' oder 'both' (neuere gewinnt)."""
    for it in items:
        it.action = ""
        s = it.status
        if mode == "a2b":
            if s in ("nur_a", "neuer_a", "neuer_b", "verschieden"):
                it.action = "a2b"
            elif s == "nur_b" and delete_extra:
                it.action = "del_b"
        elif mode == "b2a":
            if s in ("nur_b", "neuer_a", "neuer_b", "verschieden"):
                it.action = "b2a"
            elif s == "nur_a" and delete_extra:
                it.action = "del_a"
        elif mode == "both":
            if s in ("nur_a", "neuer_a"):
                it.action = "a2b"
            elif s in ("nur_b", "neuer_b"):
                it.action = "b2a"


def allowed_actions(item: DiffItem) -> set[str]:
    s = item.status
    allowed = {""}
    if s != "nur_b":
        allowed |= {"a2b", "del_a"}
    if s != "nur_a":
        allowed |= {"b2a", "del_b"}
    if s == "gleich":
        allowed -= {"a2b", "b2a"}
    if s == "typ":
        allowed -= {"a2b", "b2a"}
    return allowed


def plan_lines(items: list[DiffItem], a: str, b: str) -> list[str]:
    lines = []
    for it in items:
        if not it.action:
            continue
        pa, pb = os.path.join(a, it.rel), os.path.join(b, it.rel)
        kind = "Ordner" if it.is_dir else "Datei"
        if it.action == "a2b":
            extra = " (altes Ziel → Papierkorb)" if os.path.lexists(pb) else ""
            lines.append(f"KOPIEREN  {kind} {pa}  →  {pb}{extra}")
        elif it.action == "b2a":
            extra = " (altes Ziel → Papierkorb)" if os.path.lexists(pa) else ""
            lines.append(f"KOPIEREN  {kind} {pb}  →  {pa}{extra}")
        elif it.action == "del_a":
            lines.append(f"PAPIERKORB  {pa}")
        elif it.action == "del_b":
            lines.append(f"PAPIERKORB  {pb}")
    return lines


def execute(items: list[DiffItem], a: str, b: str, tx, progress=None) -> list[str]:
    """Führt die Aktionen aus und protokolliert jeden Schritt in der Transaktion."""
    errors = []
    for it in items:
        if not it.action:
            continue
        pa, pb = os.path.join(a, it.rel), os.path.join(b, it.rel)
        try:
            if progress:
                progress(it.rel)
            if it.action in ("a2b", "b2a"):
                src, dst = (pa, pb) if it.action == "a2b" else (pb, pa)
                _copy(src, dst, tx)
            elif it.action == "del_a":
                tx.add({"op": "trash", "path": pa, "time": send_to_trash(pa)})
            elif it.action == "del_b":
                tx.add({"op": "trash", "path": pb, "time": send_to_trash(pb)})
        except Exception as exc:
            errors.append(f"{it.rel}: {exc}")
    return errors


def _copy(src: str, dst: str, tx) -> None:
    if not os.path.lexists(src):
        raise FileNotFoundError(src)
    parent = os.path.dirname(dst)
    if not os.path.isdir(parent):
        # oberster neu anzulegender Ordner wird als 'created' protokolliert
        top = parent
        while not os.path.isdir(os.path.dirname(top)):
            top = os.path.dirname(top)
        os.makedirs(parent)
        tx.add({"op": "created", "path": top})
    if os.path.lexists(dst):
        tx.add({"op": "trash", "path": dst, "time": send_to_trash(dst)})
    if os.path.isdir(src):
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)
    tx.add({"op": "created", "path": dst})
