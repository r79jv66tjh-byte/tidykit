"""Papierkorb: Dateien/Ordner hineinlegen und gezielt wiederherstellen.

Löschen läuft über SHFileOperationW mit FOF_ALLOWUNDO (echter Windows-Papierkorb).
Wiederherstellen liest die $I-Metadaten in <Laufwerk>:\\$Recycle.Bin\\<SID> und
verschiebt die passende $R-Datei an ihren Originalort zurück – genau wie Explorer.
"""
from __future__ import annotations

import ctypes
import os
import struct
import time
from ctypes import wintypes
from dataclasses import dataclass

from .winutil import comparable_path, current_user_sid

FO_DELETE = 3
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040
FOF_NOERRORUI = 0x0400
FOF_WANTNUKEWARNING = 0x4000


class SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", ctypes.c_void_p),
        ("pTo", ctypes.c_void_p),
        ("fFlags", ctypes.c_ushort),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


class TrashError(Exception):
    pass


@dataclass
class TrashEntry:
    original: str
    deleted_at: float
    size: int
    info_file: str   # $I...
    data_file: str   # $R...


_SID: str | None = None


def _sid() -> str:
    global _SID
    if _SID is None:
        _SID = current_user_sid()
    return _SID


def recycle_dir_for(path: str) -> str:
    drive = os.path.splitdrive(os.path.abspath(path))[0]
    return os.path.join(drive + "\\", "$Recycle.Bin", _sid())


def has_recycle_bin(path: str) -> bool:
    drive = os.path.splitdrive(os.path.abspath(path))[0]
    if not drive or drive.startswith("\\\\"):
        return False
    return os.path.isdir(os.path.join(drive + "\\", "$Recycle.Bin"))


def send_to_trash(path: str) -> float:
    """Legt eine Datei/einen Ordner in den Papierkorb. Gibt den Zeitpunkt zurück.

    Fehler, wenn das Laufwerk keinen Papierkorb hat (dann würde Windows endgültig löschen).
    """
    path = os.path.abspath(path)
    if not os.path.lexists(path):
        raise TrashError(f"Nicht gefunden: {path}")
    if not has_recycle_bin(path):
        raise TrashError(f"Laufwerk ohne Papierkorb – nicht gelöscht: {path}")
    started = time.time()
    buf = ctypes.create_unicode_buffer(path + "\0")  # doppelt nullterminiert
    op = SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = FO_DELETE
    op.pFrom = ctypes.cast(buf, ctypes.c_void_p)
    op.pTo = None
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI | FOF_WANTNUKEWARNING
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if rc != 0 or op.fAnyOperationsAborted:
        raise TrashError(f"Papierkorb fehlgeschlagen (Code {rc:#x}): {path}")
    if os.path.lexists(path):
        raise TrashError(f"Datei existiert nach dem Löschen noch (gesperrt?): {path}")
    return started


def _parse_info(data: bytes) -> tuple[str, float, int]:
    version, size, filetime = struct.unpack_from("<qqq", data, 0)
    if version >= 2:
        n = struct.unpack_from("<i", data, 24)[0]
        original = data[28:28 + 2 * n].decode("utf-16-le", errors="replace").rstrip("\0")
    else:
        original = data[24:24 + 520].decode("utf-16-le", errors="replace").split("\0")[0]
    deleted = filetime / 10_000_000 - 11644473600
    return original, deleted, size


def list_trash(drive_path: str = "C:\\") -> list[TrashEntry]:
    folder = recycle_dir_for(drive_path)
    entries: list[TrashEntry] = []
    try:
        names = os.listdir(folder)
    except OSError:
        return entries
    for name in names:
        if not name.upper().startswith("$I"):
            continue
        info = os.path.join(folder, name)
        data_file = os.path.join(folder, "$R" + name[2:])
        try:
            with open(info, "rb") as fh:
                original, deleted, size = _parse_info(fh.read())
        except (OSError, struct.error):
            continue
        if os.path.lexists(data_file):
            entries.append(TrashEntry(original, deleted, size, info, data_file))
    return entries


def find_in_trash(original: str, since: float | None = None) -> TrashEntry | None:
    """Papierkorb-Eintrag für den Originalpfad.

    Mit `since` (Startzeit der protokollierten Löschung) wird der erste Eintrag ab diesem
    Zeitpunkt gewählt – also genau der, den diese Aktion erzeugt hat, auch wenn derselbe
    Pfad später noch einmal gelöscht wurde. Ohne `since` der neueste.
    """
    key = comparable_path(original)
    matches = [e for e in list_trash(original) if comparable_path(e.original) == key]
    if since is not None:
        matches = [e for e in matches if e.deleted_at >= since - 1]
        return min(matches, key=lambda e: e.deleted_at, default=None)
    return max(matches, key=lambda e: e.deleted_at, default=None)


def restore_from_trash(original: str, since: float | None = None) -> str:
    """Stellt den Eintrag an seinem Originalort wieder her. Gibt den Pfad zurück."""
    entry = find_in_trash(original, since)
    if entry is None:
        raise TrashError(f"Nicht mehr im Papierkorb: {original}")
    if os.path.lexists(entry.original):
        raise TrashError(f"Am Originalort existiert bereits etwas: {entry.original}")
    os.makedirs(os.path.dirname(entry.original), exist_ok=True)
    os.rename(entry.data_file, entry.original)
    try:
        os.remove(entry.info_file)
    except OSError:
        pass
    return entry.original
