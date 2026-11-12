"""Autostart-Manager: Run-Keys, Autostart-Ordner und Aufgabenplanung.

Ein-/Ausschalten von Registry- und Ordner-Einträgen geschieht wie im Task-Manager über
...\\Explorer\\StartupApproved (der Original-Eintrag bleibt unverändert erhalten).
Aufgaben werden über Enable-/Disable-ScheduledTask geschaltet. Vor jeder Änderung
wird der alte Zustand im Verlauf und als JSON-Sicherung abgelegt.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import struct
import time
import winreg
from dataclasses import dataclass, field, asdict

from .winutil import data_dir, ps_quote, run_powershell, run_powershell_json

APPROVED = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"
RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
ROOTS = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}
VIEWS = {64: winreg.KEY_WOW64_64KEY, 32: winreg.KEY_WOW64_32KEY}


@dataclass
class RegSource:
    label: str
    root: str          # HKCU / HKLM
    key: str
    view: int          # 64 / 32
    approved_root: str
    approved_key: str


@dataclass
class FolderSource:
    label: str
    folder: str
    approved_root: str
    approved_key: str


def default_sources() -> tuple[list[RegSource], list[FolderSource]]:
    regs = [
        RegSource("Registry (Benutzer)", "HKCU", RUN, 64, "HKCU", APPROVED + r"\Run"),
        RegSource("Registry (System)", "HKLM", RUN, 64, "HKLM", APPROVED + r"\Run"),
        RegSource("Registry (System, 32-Bit)", "HKLM", RUN, 32, "HKLM", APPROVED + r"\Run32"),
    ]
    folders = [
        FolderSource("Autostart-Ordner (Benutzer)",
                     os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup"),
                     "HKCU", APPROVED + r"\StartupFolder"),
        FolderSource("Autostart-Ordner (Alle)",
                     os.path.join(os.environ.get("PROGRAMDATA", ""), r"Microsoft\Windows\Start Menu\Programs\StartUp"),
                     "HKLM", APPROVED + r"\StartupFolder"),
    ]
    return regs, folders


@dataclass
class StartupEntry:
    kind: str                  # reg | folder | task
    source: str
    name: str
    command: str
    target: str = ""
    enabled: bool = True
    rating: str = ""
    note: str = ""
    approved_root: str = ""
    approved_key: str = ""
    task_path: str = ""
    file_path: str = ""
    signer: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def needs_admin(self) -> bool:
        return self.approved_root == "HKLM" or (self.kind == "task" and not self.extra.get("user_task"))


# --- Registry-Helfer --------------------------------------------------------
def _read_values(root: str, key: str, view: int = 64) -> dict[str, tuple]:
    out = {}
    try:
        k = winreg.OpenKey(ROOTS[root], key, 0, winreg.KEY_READ | VIEWS[view])
    except OSError:
        return out
    with k:
        i = 0
        while True:
            try:
                name, data, typ = winreg.EnumValue(k, i)
            except OSError:
                break
            out[name] = (data, typ)
            i += 1
    return out


def _approved_state(values: dict, name: str) -> bool:
    v = values.get(name)
    if not v or not isinstance(v[0], (bytes, bytearray)) or not v[0]:
        return True
    return v[0][0] % 2 == 0   # 02/06 = an, 03/07 = aus (wie Task-Manager)


def _encode(data, typ):
    if isinstance(data, (bytes, bytearray)):
        return {"type": typ, "hex": bytes(data).hex()}
    return {"type": typ, "value": data}


def _decode(d):
    return (bytes.fromhex(d["hex"]) if "hex" in d else d["value"]), d["type"]


def read_reg_value(root: str, key: str, name: str, view: int = 64):
    try:
        with winreg.OpenKey(ROOTS[root], key, 0, winreg.KEY_READ | VIEWS[view]) as k:
            data, typ = winreg.QueryValueEx(k, name)
            return _encode(data, typ)
    except OSError:
        return None


def write_reg_value(root: str, key: str, name: str, encoded, view: int = 64) -> None:
    """encoded=None löscht den Wert."""
    if encoded is None:
        try:
            with winreg.OpenKey(ROOTS[root], key, 0, winreg.KEY_SET_VALUE | VIEWS[view]) as k:
                winreg.DeleteValue(k, name)
        except FileNotFoundError:
            pass
        return
    data, typ = _decode(encoded)
    with winreg.CreateKeyEx(ROOTS[root], key, 0, winreg.KEY_SET_VALUE | VIEWS[view]) as k:
        winreg.SetValueEx(k, name, 0, typ, data)


def restore_reg_value(op: dict) -> None:
    write_reg_value(op["root"], op["key"], op["name"], op["old"], op.get("view", 64))


# --- Einträge sammeln ------------------------------------------------------
def extract_target(cmd: str) -> str:
    """Ausführbare Datei aus einer Befehlszeile ermitteln."""
    if not cmd:
        return ""
    c = os.path.expandvars(cmd.strip())
    if c.startswith('"'):
        exe = c[1:].split('"', 1)[0]
        rest = c[len(exe) + 2:].strip()
    else:
        parts = c.split(" ")
        exe, rest = parts[0], " ".join(parts[1:])
        for i in range(len(parts), 0, -1):
            cand = " ".join(parts[:i])
            if os.path.isfile(cand) or os.path.isfile(cand + ".exe"):
                exe, rest = cand, " ".join(parts[i:])
                break
    if not os.path.dirname(exe):
        found = shutil.which(exe)
        if found:
            exe = found
    base = os.path.basename(exe).lower()
    if base in ("rundll32.exe", "rundll32") and rest:
        dll = rest.strip().strip('"').split(",")[0].strip('"')
        if dll:
            return dll
    return exe


def _resolve_shortcuts(paths: list[str]) -> dict[str, tuple[str, str]]:
    lnks = [p for p in paths if p.lower().endswith(".lnk")]
    if not lnks:
        return {}
    arr = ",".join(ps_quote(p) for p in lnks)
    script = ("$s=New-Object -ComObject WScript.Shell;"
              f"@({arr}) | ForEach-Object {{ $l=$s.CreateShortcut($_); "
              "[pscustomobject]@{P=$_;T=$l.TargetPath;A=$l.Arguments} } | ConvertTo-Json -Compress")
    try:
        return {d["P"]: (d.get("T") or "", d.get("A") or "") for d in run_powershell_json(script)}
    except Exception:
        return {}


def list_registry(sources: list[RegSource]) -> list[StartupEntry]:
    entries = []
    for src in sources:
        approved = _read_values(src.approved_root, src.approved_key)
        for name, (data, _typ) in _read_values(src.root, src.key, src.view).items():
            cmd = str(data)
            entries.append(StartupEntry(
                "reg", src.label, name, cmd, extract_target(cmd),
                _approved_state(approved, name), approved_root=src.approved_root,
                approved_key=src.approved_key, extra={"run_root": src.root, "run_key": src.key,
                                                      "view": src.view}))
    return entries


def list_folders(sources: list[FolderSource]) -> list[StartupEntry]:
    entries = []
    files = []
    for src in sources:
        approved = _read_values(src.approved_root, src.approved_key)
        try:
            names = os.listdir(src.folder)
        except OSError:
            continue
        for name in names:
            if name.lower() == "desktop.ini":
                continue
            full = os.path.join(src.folder, name)
            if os.path.isdir(full):
                continue
            files.append(full)
            entries.append(StartupEntry(
                "folder", src.label, name, full, full, _approved_state(approved, name),
                approved_root=src.approved_root, approved_key=src.approved_key, file_path=full))
    resolved = _resolve_shortcuts(files)
    for e in entries:
        if e.file_path in resolved:
            target, args = resolved[e.file_path]
            if target:
                e.target = target
                e.command = f'"{target}" {args}'.strip()
    return entries


def list_tasks(include_microsoft: bool = False) -> list[StartupEntry]:
    script = r"""
$me = "$env:USERDOMAIN\$env:USERNAME"
Get-ScheduledTask | Where-Object {
  $_.Triggers | Where-Object { $_.CimClass.CimClassName -in 'MSFT_TaskLogonTrigger','MSFT_TaskBootTrigger' }
} | ForEach-Object {
  $acts = @($_.Actions | ForEach-Object { if ($_.Execute) { ('"' + $_.Execute + '" ' + $_.Arguments).Trim() } })
  [pscustomobject]@{ Path=$_.TaskPath; Name=$_.TaskName; State=[string]$_.State; Author=$_.Author;
    User=$_.Principal.UserId; Exec=($acts -join ' | ') }
} | ConvertTo-Json -Compress
"""
    entries = []
    for d in run_powershell_json(script):
        path = d.get("Path") or "\\"
        if not include_microsoft and path.lower().startswith("\\microsoft\\"):
            continue
        cmd = d.get("Exec") or "(COM-Aktion)"
        first = cmd.split(" | ")[0]
        user = (d.get("User") or "")
        entries.append(StartupEntry(
            "task", "Aufgabenplanung", d.get("Name", ""), cmd,
            extract_target(first) if d.get("Exec") else "",
            (d.get("State") or "").lower() != "disabled", task_path=path,
            extra={"author": d.get("Author") or "",
                   "user_task": bool(user) and user.lower() in (os.environ.get("USERNAME", "").lower(),)}))
    return entries


def _signatures(paths: list[str]) -> dict[str, tuple[str, str]]:
    existing = sorted({p for p in paths if p and os.path.isfile(p)})
    if not existing:
        return {}
    arr = ",".join(ps_quote(p) for p in existing)
    script = (f"@({arr}) | ForEach-Object {{ $s=Get-AuthenticodeSignature -LiteralPath $_; "
              "[pscustomobject]@{P=$_;S=[string]$s.Status;N=$(if($s.SignerCertificate){$s.SignerCertificate.Subject}else{''})} }"
              " | ConvertTo-Json -Compress")
    try:
        return {d["P"].lower(): (d.get("S") or "", d.get("N") or "") for d in run_powershell_json(script, 180)}
    except Exception:
        return {}


def _signer_name(subject: str) -> str:
    m = re.search(r"CN=(\"[^\"]+\"|[^,]+)", subject or "")
    return m.group(1).strip('"') if m else ""


def rate(entries: list[StartupEntry], check_signatures: bool = True) -> None:
    sigs = _signatures([e.target for e in entries]) if check_signatures else {}
    temp = os.path.normcase(os.environ.get("TEMP", "~~"))
    downloads = os.path.normcase(os.path.join(os.path.expanduser("~"), "Downloads"))
    for e in entries:
        t = e.target
        status, subject = sigs.get(t.lower(), ("", "")) if t else ("", "")
        e.signer = _signer_name(subject)
        low = os.path.normcase(t)
        name = (e.name + " " + os.path.basename(t)).lower()
        if not t:
            e.rating, e.note = "Unklar", "Ziel nicht ermittelbar"
        elif not os.path.lexists(t):   # lexists: App-Aliase in WindowsApps sind Reparse-Points
            e.rating, e.note = "Verwaist", "Zieldatei fehlt – Eintrag ist nutzlos"
        elif low.startswith(temp) or low.startswith(downloads):
            e.rating, e.note = "Verdächtig", "startet aus Temp/Downloads"
        elif check_signatures and status and status != "Valid":
            e.rating, e.note = "Prüfen", f"Signatur: {status}"
        elif "microsoft" in e.signer.lower() or low.startswith(os.path.normcase(os.environ.get("WINDIR", r"C:\Windows"))):
            e.rating, e.note = "System", "Windows/Microsoft"
        elif re.search(r"updat|helper|launcher|tray|agent|notifier|crashpad", name):
            e.rating, e.note = "Optional", "Updater/Hilfsprogramm – meist verzichtbar"
        else:
            e.rating, e.note = "Normal", e.signer and f"signiert: {e.signer}" or ""


def list_all(include_tasks: bool = True, include_microsoft: bool = False,
             check_signatures: bool = True, sources=None) -> list[StartupEntry]:
    regs, folders = sources or default_sources()
    entries = list_registry(regs) + list_folders(folders)
    if include_tasks:
        try:
            entries += list_tasks(include_microsoft)
        except Exception as exc:
            entries.append(StartupEntry("info", "Aufgabenplanung", "(Fehler beim Lesen)", str(exc)))
    rate([e for e in entries if e.kind != "info"], check_signatures)
    return entries


# --- Schalten -------------------------------------------------------------
def set_task_enabled(path: str, name: str, enabled: bool) -> None:
    verb = "Enable-ScheduledTask" if enabled else "Disable-ScheduledTask"
    run_powershell(f"{verb} -TaskPath {ps_quote(path)} -TaskName {ps_quote(name)} | Out-Null", 60)


def _approved_bytes(enabled: bool) -> bytes:
    if enabled:
        return bytes([2, 0, 0, 0]) + bytes(8)
    ft = int((time.time() + 11644473600) * 10_000_000)
    return bytes([3, 0, 0, 0]) + struct.pack("<q", ft)


def backup(entry: StartupEntry, old_value) -> str:
    folder = os.path.join(data_dir(), "sicherungen")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, time.strftime("autostart-%Y%m%d-%H%M%S-") + re.sub(r"[^\w.-]", "_", entry.name)[:40] + ".json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"zeit": time.strftime("%Y-%m-%d %H:%M:%S"), "eintrag": asdict(entry),
                   "alter_wert": old_value}, fh, ensure_ascii=False, indent=1)
    return path


def set_enabled(entry: StartupEntry, enabled: bool, tx) -> None:
    """Schaltet einen Eintrag und protokolliert den alten Zustand in der Transaktion."""
    if entry.kind in ("reg", "folder"):
        old = read_reg_value(entry.approved_root, entry.approved_key, entry.name)
        backup(entry, old)
        write_reg_value(entry.approved_root, entry.approved_key, entry.name,
                        {"type": winreg.REG_BINARY, "hex": _approved_bytes(enabled).hex()})
        tx.add({"op": "reg_value", "root": entry.approved_root, "key": entry.approved_key,
                "name": entry.name, "view": 64, "old": old})
    elif entry.kind == "task":
        backup(entry, {"enabled": entry.enabled})
        set_task_enabled(entry.task_path, entry.name, enabled)
        tx.add({"op": "task_state", "path": entry.task_path, "name": entry.name,
                "enabled": entry.enabled})
    else:
        raise ValueError("Dieser Eintrag kann nicht geschaltet werden.")
    entry.enabled = enabled
