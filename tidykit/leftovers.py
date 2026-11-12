"""Leere Ordner und Reste deinstallierter Programme finden.

Ein Ordner gilt als möglicher Programmrest, wenn er zu keinem installierten Programm
(Uninstall-Registry), keinem Dienst, keinem laufenden Prozess und keinem Autostart-
Eintrag passt. Alles wird nur vorgeschlagen – gelöscht wird erst nach Vorschau und
nur in den Papierkorb.
"""
from __future__ import annotations

import json
import os
import re
import stat
import time
import winreg
from dataclasses import dataclass, field

from .winutil import is_within, run_powershell

CONF_ORDER = {"hoch": 0, "mittel": 1, "niedrig": 2}

# Ordner, die nie als Rest vorgeschlagen werden (normalisierte Namen)
PROTECTED = {
    "microsoft", "windows", "windowsapps", "packages", "temp", "tmp", "commonfiles",
    "internetexplorer", "windowsdefender", "windowsdefenderadvancedthreatprotection",
    "windowsnt", "windowsmail", "windowsmediaplayer", "windowsphotoviewer",
    "windowspowershell", "windowssidebar", "windowsmultimediaplatform", "modifiablewindowsapps",
    "msbuild", "referenceassemblies", "dotnet", "crashdumps", "connecteddevicesplatform",
    "d3dscache", "comms", "packagecache", "usoshared", "usoprivate", "softwaredistribution",
    "applicationdata", "desktop", "documents", "startmenu", "templates", "favorites",
    "regidcommicrosoft", "ssh", "programs", "publishers", "history", "fontcache",
    "virtualstore", "placeholdertilelogofolder", "peernetworking", "identities",
    "microsoftnet", "uninstallinformation", "msecache", "recovery", "systemvolumeinformation",
    "windowsstore", "windowssecurity", "package", "pip", "python", "squirreltemp",
    "nuget", "npm", "npmcache", "deliveryoptimization", "ssl", "regid19912org",
}
PROTECTED_PREFIXES = ("microsoft", "windows", "msedge", "onedrive")
CONTAINERS = {"programs", "commonfiles"}   # in diese Ordner eine Ebene tiefer schauen
PUBLISHER_NOISE = r"\b(inc|llc|ltd|gmbh|co|corp|corporation|company|limited|ag|sa|srl|bv|the|software|technologies|technology|games|studio|studios|entertainment)\b"
GUID_RE = re.compile(r"^\{?[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\}?$", re.I)
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
# Von mehreren Programmen geteilte Komponenten: nie mit hoher Sicherheit vorschlagen
SHARED_RE = re.compile(r"vst|plugin|aax|clap|anticheat|battleye|crashreport|webview|dwrite|"
                       r"directx|vcredist|redist|runtime|codec|driver|treiber|license|lizenz|ilok", re.I)


@dataclass
class Candidate:
    path: str
    category: str      # "Programmrest" | "Leerer Ordner"
    confidence: str    # hoch | mittel | niedrig
    reason: str
    size: int = 0
    files: int = 0
    newest: float = 0.0
    capped: bool = False


@dataclass
class KnownData:
    names: set[str] = field(default_factory=set)       # normalisierte Namen
    texts: list[str] = field(default_factory=list)    # normalisierte Programm-/Herstellernamen
    paths: list[str] = field(default_factory=list)    # bekannte, benutzte Pfade
    programs: int = 0


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _norm_publisher(s: str) -> str:
    return norm(re.sub(PUBLISHER_NOISE, " ", s.lower()))


def default_roots() -> list[str]:
    env = os.environ
    home = os.path.expanduser("~")
    roots = [
        env.get("APPDATA"),
        env.get("LOCALAPPDATA"),
        os.path.join(home, "AppData", "LocalLow"),
        env.get("PROGRAMDATA"),
        env.get("ProgramFiles"),
        env.get("ProgramFiles(x86)"),
    ]
    seen, out = set(), []
    for r in roots:
        if r and os.path.isdir(r) and os.path.normcase(r) not in seen:
            seen.add(os.path.normcase(r))
            out.append(r)
    return out


# --- Was ist installiert / in Benutzung? -----------------------------------
_UNINSTALL = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"


def _iter_uninstall():
    hives = [
        (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY),
        (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
        (winreg.HKEY_CURRENT_USER, 0),
    ]
    for hive, view in hives:
        try:
            key = winreg.OpenKey(hive, _UNINSTALL, 0, winreg.KEY_READ | view)
        except OSError:
            continue
        with key:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(key, i)
                except OSError:
                    break
                i += 1
                vals = {"_key": sub}
                try:
                    with winreg.OpenKey(key, sub) as sk:
                        for name in ("DisplayName", "Publisher", "InstallLocation",
                                     "DisplayIcon", "UninstallString", "InstallSource"):
                            try:
                                vals[name] = str(winreg.QueryValueEx(sk, name)[0])
                            except OSError:
                                pass
                except OSError:
                    continue
                yield vals


def _path_from_command(cmd: str) -> str | None:
    if not cmd:
        return None
    cmd = os.path.expandvars(cmd.strip())
    if cmd.startswith('"'):
        p = cmd[1:].split('"', 1)[0]
    else:
        p = cmd.split(",")[0]
        m = re.match(r"^([a-zA-Z]:\\.*?\.(exe|dll|ico|bat|cmd|msi))", p, re.I)
        p = m.group(1) if m else p.split(" ")[0]
    if re.match(r"^[a-zA-Z]:\\", p):
        return p
    return None


def _services_paths() -> list[str]:
    paths = []
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services")
    except OSError:
        return paths
    with root:
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(root, i)
            except OSError:
                break
            i += 1
            try:
                with winreg.OpenKey(root, sub) as sk:
                    img = str(winreg.QueryValueEx(sk, "ImagePath")[0])
            except OSError:
                continue
            img = re.sub(r"^\\\?\?\\", "", img)
            p = _path_from_command(img)
            if p:
                paths.append(p)
    return paths


def _process_paths() -> list[str]:
    try:
        out = run_powershell("Get-Process | ForEach-Object { $_.Path } | Where-Object { $_ }", 60)
        return [l.strip() for l in out.splitlines() if l.strip()]
    except Exception:
        return []


def _game_installs() -> list[tuple[str, str]]:
    """(Name, Installationsordner) aller Steam- und Epic-Spiele."""
    games: list[tuple[str, str]] = []
    steam = None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            steam = str(winreg.QueryValueEx(k, "SteamPath")[0]).replace("/", "\\")
    except OSError:
        pass
    libraries = []
    if steam:
        libraries.append(steam)
        try:
            with open(os.path.join(steam, "steamapps", "libraryfolders.vdf"), encoding="utf-8") as fh:
                libraries += [m.replace("\\\\", "\\") for m in re.findall(r'"path"\s+"([^"]+)"', fh.read())]
        except OSError:
            pass
    seen = set()
    for lib in libraries:
        apps = os.path.join(lib, "steamapps")
        if os.path.normcase(apps) in seen:
            continue
        seen.add(os.path.normcase(apps))
        try:
            manifests = [f for f in os.listdir(apps) if f.startswith("appmanifest_")]
        except OSError:
            continue
        for f in manifests:
            try:
                with open(os.path.join(apps, f), encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            name = re.search(r'"name"\s+"([^"]*)"', text)
            inst = re.search(r'"installdir"\s+"([^"]*)"', text)
            if name:
                games.append((name.group(1), os.path.join(apps, "common", inst.group(1)) if inst else ""))
    epic = os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"),
                        r"Epic\EpicGamesLauncher\Data\Manifests")
    try:
        items = [f for f in os.listdir(epic) if f.endswith(".item")]
    except OSError:
        items = []
    for f in items:
        try:
            with open(os.path.join(epic, f), encoding="utf-8") as fh:
                d = json.load(fh)
            games.append((d.get("DisplayName", ""), d.get("InstallLocation", "")))
        except (OSError, ValueError):
            continue
    return games


def collect_known(extra_paths: list[str] | None = None, include_processes: bool = True) -> KnownData:
    kd = KnownData()
    for vals in _iter_uninstall():
        name = vals.get("DisplayName", "")
        if not name:
            continue
        kd.programs += 1
        n = norm(name)
        if n:
            kd.names.add(n)
            kd.texts.append(n)
        # Namensbestandteile ohne Versionsnummern
        stripped = norm(re.sub(r"[\d.]+|\(.*?\)|x64|x86|64-bit|32-bit", " ", name, flags=re.I))
        if len(stripped) >= 4:
            kd.texts.append(stripped)
        for word in re.findall(r"[A-Za-z][A-Za-z0-9]{3,}", name):
            kd.names.add(word.lower())
        pub = vals.get("Publisher", "")
        if pub:
            pn = _norm_publisher(pub)
            if len(pn) >= 3:
                kd.names.add(pn)
                kd.texts.append(pn)
        for field_name in ("InstallLocation", "DisplayIcon", "UninstallString"):
            p = _path_from_command(vals.get(field_name, ""))
            if not p:
                continue
            if field_name != "InstallLocation":
                p = os.path.dirname(p)
            kd.paths.append(p.rstrip("\\"))
            for comp in p.split("\\")[1:]:
                c = norm(comp)
                if len(c) >= 3:
                    kd.names.add(c)
    for name, location in _game_installs():
        kd.programs += 1
        n = norm(name)
        if n:
            kd.names.add(n)
            kd.texts.append(n)
        if location:
            kd.names.add(norm(os.path.basename(location)))
            # Unreal & Co. legen ihren internen Projektnamen als Unterordner an (z. B. TslGame)
            for e in _subdirs(location):
                c = norm(e.name)
                if len(c) >= 4 and c not in ("engine", "binaries", "content", "data", "bin"):
                    kd.names.add(c)
    kd.paths.extend(os.path.dirname(p) for p in _services_paths())
    if include_processes:
        kd.paths.extend(os.path.dirname(p) for p in _process_paths())
    if extra_paths:
        kd.paths.extend(extra_paths)
    return kd


# --- Scan ------------------------------------------------------------------
def _is_reparse(entry: os.DirEntry) -> bool:
    try:
        if entry.is_symlink() or entry.is_junction():
            return True
        return bool(entry.stat(follow_symlinks=False).st_file_attributes & FILE_ATTRIBUTE_REPARSE_POINT)
    except OSError:
        return True


SAVE_RE = re.compile(r"save|spielstand|savegame|profile", re.I)


def measure(path: str, max_files: int = 20000) -> tuple[int, int, float, bool, bool]:
    """(Größe, Dateien, neueste Dateiänderung, abgeschnitten, Spielstände?) – ohne Junctions.

    Gewertet werden nur Datei-Zeitstempel; bei leeren Ordnern der des Ordners selbst.
    """
    size = files = 0
    newest = 0.0
    saves = False
    stack = [path]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for e in it:
                    if _is_reparse(e):
                        continue
                    try:
                        st = e.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if not saves and SAVE_RE.search(e.name):
                        saves = True
                    if stat.S_ISDIR(st.st_mode):
                        stack.append(e.path)
                    else:
                        newest = max(newest, st.st_mtime)
                        files += 1
                        size += st.st_size
                        if files >= max_files:
                            return size, files, newest, True, saves
        except OSError:
            continue
    if files == 0:
        try:
            newest = os.stat(path).st_mtime
        except OSError:
            pass
    return size, files, newest, False, saves


def is_protected(name: str) -> bool:
    n = norm(name)
    return (not n) or n in PROTECTED or n.startswith(PROTECTED_PREFIXES) or name.startswith(".")


def matches_known(name: str, kd: KnownData) -> str | None:
    """Gibt den Grund zurück, warum der Ordner zu etwas Installiertem passt, sonst None."""
    n = norm(name)
    if len(n) < 3:
        return "Name zu kurz für eine Zuordnung"
    if n in kd.names:
        return "passt zu installiertem Programm"
    if len(n) >= 4:
        for t in kd.texts:
            if n in t or (len(t) >= 4 and t in n):
                return "passt zu installiertem Programm/Hersteller"
    return None


def scan_leftovers(roots: list[str] | None = None, known: KnownData | None = None,
                   progress=None, old_days: int = 180,
                   user_roots: list[str] | None = None) -> list[Candidate]:
    """old_days: ab so vielen Tagen ohne Änderung gilt ein Rest als „hoch“.

    „hoch“ gibt es nur in Benutzer-Ordnern (AppData). Unter Programme/ProgramData ist
    höchstens „mittel“ möglich, weil dort auch manuell/portabel installierte Software liegt.
    """
    roots = roots or default_roots()
    home = os.path.expanduser("~")
    user_roots = user_roots if user_roots is not None else [home]
    kd = known or collect_known()
    now = time.time()
    result: list[Candidate] = []

    def consider(folder: str, name: str, depth: int) -> None:
        if is_protected(name):
            if norm(name) in CONTAINERS and depth == 0:
                for e in _subdirs(folder):
                    consider(e.path, e.name, depth + 1)
            return
        if any(is_within(p, folder) for p in kd.paths):  # etwas Benutztes liegt darin
            return
        if progress:
            progress(folder)
        size, files, newest, capped, saves = measure(folder)
        if files == 0:
            result.append(Candidate(folder, "Leerer Ordner", "hoch",
                                    "enthält keine Dateien", 0, 0, newest))
            return
        if matches_known(name, kd):
            return
        age_days = (now - newest) / 86400
        if GUID_RE.match(name):
            conf, reason = "niedrig", "GUID-Ordner ohne zugeordnetes Programm"
        elif age_days < 30:
            conf, reason = "niedrig", f"kein Programm gefunden, aber vor {age_days:.0f} Tagen noch geändert"
        elif age_days < old_days:
            conf, reason = "mittel", f"kein Programm gefunden, zuletzt vor {age_days:.0f} Tagen geändert"
        else:
            conf, reason = "hoch", f"kein Programm gefunden, seit {age_days:.0f} Tagen unverändert"
        if SHARED_RE.search(name) or depth > 0 and SHARED_RE.search(os.path.dirname(folder)):
            conf = "niedrig"
            reason += "; geteilte Komponente (Plugins/Anti-Cheat/Laufzeit) – evtl. von anderen Programmen genutzt"
        elif conf == "hoch" and saves:
            conf = "mittel"
            reason += "; enthält vermutlich Spielstände/Profile"
        elif conf == "hoch" and not any(is_within(folder, r) for r in user_roots):
            conf = "mittel"
            reason += "; Programme/ProgramData – auch manuelle Installationen möglich"
        result.append(Candidate(folder, "Programmrest", conf, reason, size, files, newest, capped))

    for root in roots:
        for e in _subdirs(root):
            consider(e.path, e.name, 0)
    result.sort(key=lambda c: (CONF_ORDER[c.confidence], -c.size))
    return result


def _subdirs(folder: str) -> list[os.DirEntry]:
    try:
        with os.scandir(folder) as it:
            return [e for e in it if e.is_dir(follow_symlinks=False) and not _is_reparse(e)]
    except OSError:
        return []


def find_empty_dirs(root: str, progress=None) -> list[Candidate]:
    """Findet Ordner ohne Dateien (auch verschachtelt) – meldet jeweils den obersten."""
    empties: list[str] = []

    def visit(path: str) -> bool:
        """True, wenn der Ordner keine Dateien enthält."""
        if progress:
            progress(path)
        try:
            entries = list(os.scandir(path))
        except OSError:
            return False
        empty = True
        child_empty = []
        for e in entries:
            if _is_reparse(e):
                empty = False
                continue
            if e.is_dir(follow_symlinks=False):
                if visit(e.path):
                    child_empty.append(e.path)
                else:
                    empty = False
            else:
                empty = False
        if not empty:
            empties.extend(child_empty)
        return empty

    if visit(root):
        # Der Startordner selbst ist leer: seine Unterordner melden, nicht ihn selbst
        empties.extend(e.path for e in os.scandir(root) if e.is_dir(follow_symlinks=False))
    out = []
    for p in sorted(empties, key=str.lower):
        try:
            mt = os.stat(p).st_mtime
        except OSError:
            mt = 0
        out.append(Candidate(p, "Leerer Ordner", "hoch", "enthält keine Dateien", 0, 0, mt))
    return out
