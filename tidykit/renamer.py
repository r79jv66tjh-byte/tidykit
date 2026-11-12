"""Massen-Umbenenner: Regeln anwenden, Plan prüfen, sicher umbenennen."""
from __future__ import annotations

import fnmatch
import os
import re
import time
import uuid
from dataclasses import dataclass, field

KINDS = {
    "replace": "Suchen/Ersetzen",
    "regex": "Regex",
    "pattern": "Muster/Nummerierung",
    "case": "Groß-/Kleinschreibung",
    "trim": "Leerzeichen bereinigen",
}
CASE_MODES = {"lower": "kleinschreibung", "upper": "GROSSSCHREIBUNG",
              "title": "Jedes Wort Groß", "sentence": "Satzanfang groß"}
INVALID_CHARS = set('<>:"/\\|?*')
RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
            *(f"lpt{i}" for i in range(1, 10))}


@dataclass
class Rule:
    kind: str
    params: dict = field(default_factory=dict)
    enabled: bool = True

    def describe(self) -> str:
        p = self.params
        if self.kind == "replace":
            cs = "" if p.get("case_sensitive") else " (ohne Groß/Klein)"
            return f"Ersetze „{p.get('find', '')}“ → „{p.get('repl', '')}“{cs}"
        if self.kind == "regex":
            return f"Regex /{p.get('pattern', '')}/ → „{p.get('repl', '')}“"
        if self.kind == "pattern":
            return (f"Muster „{p.get('template', '')}“ (Start {p.get('start', 1)}, "
                    f"Schritt {p.get('step', 1)})")
        if self.kind == "case":
            return f"Schreibweise: {CASE_MODES.get(p.get('mode', 'lower'))}"
        if self.kind == "trim":
            extra = ", _ → Leerzeichen" if p.get("underscores") else ""
            return f"Leerzeichen bereinigen (doppelte, Anfang/Ende{extra})"
        return self.kind


def list_files(folder: str, pattern: str = "*", recursive: bool = False) -> list[str]:
    pats = [p.strip() for p in (pattern or "*").split(";") if p.strip()] or ["*"]
    result = []
    if recursive:
        walker = os.walk(folder)
    else:
        try:
            walker = [(folder, [], os.listdir(folder))]
        except OSError:
            return []
    for root, _dirs, files in walker:
        for f in files:
            full = os.path.join(root, f)
            if not os.path.isfile(full):
                continue
            if any(fnmatch.fnmatch(f.lower(), p.lower()) for p in pats):
                result.append(full)
    return result


def sort_files(files: list[str], key: str = "name") -> list[str]:
    if key == "date":
        return sorted(files, key=lambda f: (os.path.getmtime(f), f.lower()))
    if key == "size":
        return sorted(files, key=lambda f: (os.path.getsize(f), f.lower()))
    return sorted(files, key=lambda f: _natural_key(f))


def _natural_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


_N_TOKEN = re.compile(r"\{n(?::(\d+))?\}")
_DATE_TOKEN = re.compile(r"\{datum(?::([^}]*))?\}")


def _apply_rule(rule: Rule, name: str, ext: str, path: str, index: int) -> str:
    p = rule.params
    if rule.kind == "replace":
        find = p.get("find", "")
        if not find:
            return name
        if p.get("case_sensitive"):
            return name.replace(find, p.get("repl", ""))
        return re.sub(re.escape(find), lambda _m: p.get("repl", ""), name, flags=re.IGNORECASE)
    if rule.kind == "regex":
        pattern = p.get("pattern", "")
        if not pattern:
            return name
        repl = re.sub(r"\$(\d+)", r"\\g<\1>", p.get("repl", ""))  # $1 wie \1 erlauben
        flags = 0 if p.get("case_sensitive", True) else re.IGNORECASE
        return re.sub(pattern, repl, name, flags=flags)
    if rule.kind == "pattern":
        template = p.get("template", "{name}")
        number = int(p.get("start", 1)) + index * int(p.get("step", 1))
        out = _N_TOKEN.sub(lambda m: str(number).zfill(int(m.group(1) or 0)), template)
        mtime = os.path.getmtime(path) if os.path.exists(path) else time.time()
        out = _DATE_TOKEN.sub(lambda m: time.strftime(m.group(1) or "%Y-%m-%d",
                                                      time.localtime(mtime)), out)
        out = out.replace("{ordner}", os.path.basename(os.path.dirname(path)))
        out = out.replace("{ext}", ext.lstrip("."))
        return out.replace("{name}", name)
    if rule.kind == "case":
        mode = p.get("mode", "lower")
        if mode == "upper":
            return name.upper()
        if mode == "title":
            return re.sub(r"[^\W_]+", lambda m: m.group(0)[:1].upper() + m.group(0)[1:].lower(), name)
        if mode == "sentence":
            low = name.lower()
            return low[:1].upper() + low[1:]
        return name.lower()
    if rule.kind == "trim":
        if p.get("underscores"):
            name = name.replace("_", " ")
        return re.sub(r"\s+", " ", name).strip()
    return name


def build_plan(files: list[str], rules: list[Rule], include_ext: bool = False) -> list[tuple[str, str]]:
    """Liefert [(alter_pfad, neuer_name)] in der übergebenen Reihenfolge."""
    plan = []
    for index, path in enumerate(files):
        base = os.path.basename(path)
        if include_ext:
            name, ext = base, ""
        else:
            name, ext = os.path.splitext(base)
        for rule in rules:
            if rule.enabled:
                name = _apply_rule(rule, name, ext or os.path.splitext(base)[1], path, index)
        plan.append((path, name + ext))
    return plan


def check_plan(plan: list[tuple[str, str]]) -> list[str]:
    """Status je Eintrag: 'ok', 'unverändert' oder eine Fehlermeldung (beginnt mit '⚠')."""
    sources = {os.path.normcase(src) for src, _ in plan}
    targets: dict[str, int] = {}
    for src, new in plan:
        key = os.path.normcase(os.path.join(os.path.dirname(src), new))
        targets[key] = targets.get(key, 0) + 1
    status = []
    for src, new in plan:
        dst = os.path.join(os.path.dirname(src), new)
        key = os.path.normcase(dst)
        stem = os.path.splitext(new)[0].lower()
        if not new.strip():
            status.append("⚠ leerer Name")
        elif any(c in INVALID_CHARS or ord(c) < 32 for c in new):
            status.append("⚠ ungültige Zeichen")
        elif new.endswith((" ", ".")):
            status.append("⚠ endet mit Punkt/Leerzeichen")
        elif stem in RESERVED:
            status.append("⚠ reservierter Name")
        elif new == os.path.basename(src):
            status.append("unverändert")
        elif targets[key] > 1:
            status.append("⚠ Name doppelt")
        elif os.path.exists(dst) and key not in sources:
            status.append("⚠ existiert bereits")
        else:
            status.append("ok")
    return status


def executable_pairs(plan: list[tuple[str, str]]) -> list[tuple[str, str]]:
    status = check_plan(plan)
    if any(s.startswith("⚠") for s in status):
        raise ValueError("Der Plan enthält Konflikte – bitte zuerst beheben.")
    return [(src, os.path.join(os.path.dirname(src), new))
            for (src, new), s in zip(plan, status) if s == "ok"]


def execute_renames(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Benennt in zwei Phasen um (über Zwischennamen), damit Tausch/Ketten klappen.

    Bei einem Fehler wird alles bereits Erledigte zurückgerollt und der Fehler geworfen.
    """
    tag = uuid.uuid4().hex[:8]
    staged: list[tuple[str, str, str]] = []  # (src, tmp, dst)
    done: list[tuple[str, str, str]] = []
    try:
        for i, (src, dst) in enumerate(pairs):
            if not os.path.exists(src):
                raise FileNotFoundError(f"Datei fehlt: {src}")
            tmp = os.path.join(os.path.dirname(src), f".~oh_{tag}_{i}")
            os.rename(src, tmp)
            staged.append((src, tmp, dst))
        for src, tmp, dst in staged:
            if os.path.exists(dst):
                raise FileExistsError(f"Ziel existiert bereits: {dst}")
            os.rename(tmp, dst)
            done.append((src, tmp, dst))
    except Exception:
        for src, tmp, dst in reversed(done):
            try:
                os.rename(dst, tmp)
            except OSError:
                pass
        for src, tmp, _dst in reversed(staged):
            try:
                if os.path.exists(tmp):
                    os.rename(tmp, src)
            except OSError:
                pass
        raise
    return [(src, dst) for src, _tmp, dst in done]
