"""Kleine Windows-Hilfsfunktionen (Admin, PowerShell, Pfade, Formatierung)."""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
from ctypes import wintypes

CREATE_NO_WINDOW = 0x08000000


def data_dir() -> str:
    """Ordner für Verlauf und Sicherungen (über TIDYKIT_DATA überschreibbar)."""
    base = os.environ.get("TIDYKIT_DATA") or os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "TidyKit")
    os.makedirs(base, exist_ok=True)
    return base


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin() -> bool:
    """Startet das Programm mit Adminrechten neu (UAC-Abfrage). True, wenn gestartet."""
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        pyw = exe[:-10] + "pythonw.exe"
        if os.path.exists(pyw):
            exe = pyw
    args = subprocess.list2cmdline(["-m", "tidykit"])
    cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, args, cwd, 1)
    return rc > 32


def run_powershell(script: str, timeout: float = 120) -> str:
    """Führt ein PowerShell-Skript aus und liefert stdout (UTF-8)."""
    full = "[Console]::OutputEncoding=[Text.Encoding]::UTF8;$ErrorActionPreference='Stop';" + script
    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-Command", full],
        capture_output=True, timeout=timeout, creationflags=CREATE_NO_WINDOW)
    out = proc.stdout.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(err or f"PowerShell-Fehler (Code {proc.returncode})")
    return out


def run_powershell_json(script: str, timeout: float = 120):
    out = run_powershell(script, timeout).strip()
    if not out:
        return []
    data = json.loads(out)
    if isinstance(data, dict):
        data = [data]
    return data


def ps_quote(s: str) -> str:
    """String als PowerShell-Literal in einfachen Anführungszeichen."""
    return "'" + s.replace("'", "''") + "'"


def current_user_sid() -> str:
    """SID des angemeldeten Benutzers (für den Papierkorb-Ordner)."""
    advapi = ctypes.windll.advapi32
    kernel = ctypes.windll.kernel32
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                           wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise ctypes.WinError()
    try:
        size = wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        buf = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(token, 1, buf, size, ctypes.byref(size)):
            raise ctypes.WinError()
        psid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
        sid_str = wintypes.LPWSTR()
        if not advapi.ConvertSidToStringSidW(psid, ctypes.byref(sid_str)):
            raise ctypes.WinError()
        value = sid_str.value
        kernel.LocalFree(sid_str)
        return value
    finally:
        kernel.CloseHandle(token)


def human_size(n: int | None) -> str:
    if n is None:
        return ""
    f = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if f < 1024 or unit == "GB":
            return f"{f:.0f} {unit}" if unit == "B" else f"{f:.1f} {unit}".replace(".", ",")
        f /= 1024
    return str(n)


def fmt_time(ts: float | None) -> str:
    if not ts:
        return ""
    return time.strftime("%d.%m.%Y %H:%M", time.localtime(ts))


def open_in_explorer(path: str) -> None:
    if os.path.exists(path):
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        parent = os.path.dirname(path)
        if os.path.isdir(parent):
            os.startfile(parent)


def same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def comparable_path(path: str) -> str:
    # Windows hands out 8.3 names (C:\\Users\\RUNNER~1) next to long ones; realpath expands them,
    # and for a path that no longer exists it resolves the part that still does.
    return os.path.normcase(os.path.realpath(path))


def is_within(child: str, parent: str) -> bool:
    c = comparable_path(child)
    p = comparable_path(parent).rstrip("\\")
    return c == p or c.startswith(p + "\\")
