"""Kommandozeile für TidyKit (für Claude/ToolHub).

Alle Befehle geben JSON auf stdout aus. Fehler: Meldung auf stderr, Exit-Code 1.
Schreibende Befehle unterstützen --dry-run (nur Vorschau) und landen im selben
Verlauf wie die GUI – sie lassen sich also mit `undo` oder im Verlauf-Tab zurücknehmen.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from . import autostart, foldersync, leftovers, renamer
from .journal import Journal, describe_op
from .renamer import Rule
from .trash import has_recycle_bin, send_to_trash
from .winutil import is_admin, is_within


class CliError(Exception):
    pass


# --- Hilfen ------------------------------------------------------------------
def _iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts)) if ts else None


def _split_paths(value: str) -> list[str]:
    return [os.path.abspath(p.strip().strip('"')) for p in value.replace("\n", ";").split(";") if p.strip()]


def _protected_paths() -> list[str]:
    env = os.environ
    return [p for p in (env.get("WINDIR"), env.get("SystemRoot")) if p]


def _forbidden_roots() -> list[str]:
    env = os.environ
    home = os.path.expanduser("~")
    roots = [home, os.path.join(home, "Desktop"), os.path.join(home, "Documents"),
             os.path.join(home, "Downloads"), os.path.join(home, "AppData"),
             env.get("APPDATA"), env.get("LOCALAPPDATA"), env.get("PROGRAMDATA"),
             env.get("ProgramFiles"), env.get("ProgramFiles(x86)"), env.get("USERPROFILE")]
    return [r for r in roots if r]


def check_trash_target(path: str) -> str | None:
    """Gibt einen Grund zurück, warum der Pfad nicht gelöscht werden darf, sonst None."""
    if not os.path.lexists(path):
        return "existiert nicht"
    if os.path.splitdrive(path)[1] in ("", "\\", "/"):
        return "Laufwerkswurzel"
    if any(is_within(path, p) for p in _protected_paths()):
        return "Windows-Systemordner"
    if any(os.path.normcase(os.path.normpath(path)) == os.path.normcase(os.path.normpath(r))
           for r in _forbidden_roots()):
        return "geschützter Hauptordner"
    if not has_recycle_bin(path):
        return "Laufwerk ohne Papierkorb"
    return None


def _startup_sources():
    """Test-Hook: TIDYKIT_STARTUP_TEST_BASE ersetzt die echten Autostart-Quellen."""
    base = os.environ.get("TIDYKIT_STARTUP_TEST_BASE")
    if not base:
        return None, True
    return ([autostart.RegSource("Test", "HKCU", base + r"\Run", 64, "HKCU", base + r"\Approved")], []), False


def _entry_json(e: autostart.StartupEntry) -> dict:
    return {"name": e.name, "source": e.source, "kind": e.kind, "enabled": e.enabled,
            "rating": e.rating, "note": e.note, "target": e.target, "command": e.command,
            "signer": e.signer, "task_path": e.task_path or None, "needs_admin": e.needs_admin}


def _candidate_json(c: leftovers.Candidate) -> dict:
    return {"path": c.path, "category": c.category, "confidence": c.confidence,
            "reason": c.reason, "size_bytes": c.size, "files": c.files,
            "size_capped": c.capped, "last_modified": _iso(c.newest)}


def _rules_from_args(a) -> list[Rule]:
    if a.rules:
        try:
            raw = json.loads(a.rules)
        except ValueError as exc:
            raise CliError(f"--rules ist kein gültiges JSON: {exc}")
        if not isinstance(raw, list):
            raise CliError("--rules muss eine JSON-Liste sein")
        rules = []
        for r in raw:
            kind = r.get("kind")
            if kind not in renamer.KINDS:
                raise CliError(f"Unbekannte Regel '{kind}'. Erlaubt: {', '.join(renamer.KINDS)}")
            rules.append(Rule(kind, {k: v for k, v in r.items() if k != "kind"}))
        return rules
    rules = []
    if a.trim:
        rules.append(Rule("trim", {"underscores": a.underscores}))
    if a.find is not None:
        rules.append(Rule("replace", {"find": a.find, "repl": a.replace or "",
                                      "case_sensitive": a.case_sensitive}))
    if a.regex is not None:
        rules.append(Rule("regex", {"pattern": a.regex, "repl": a.regex_replace or "",
                                    "case_sensitive": a.case_sensitive}))
    if a.case:
        if a.case not in renamer.CASE_MODES:
            raise CliError(f"--case muss eines von {', '.join(renamer.CASE_MODES)} sein")
        rules.append(Rule("case", {"mode": a.case}))
    if a.template:
        rules.append(Rule("pattern", {"template": a.template, "start": a.start, "step": a.step}))
    if not rules:
        raise CliError("Keine Regel angegeben (--find, --regex, --case, --template, --trim oder --rules).")
    return rules


def _rename_plan(a):
    if not os.path.isdir(a.folder):
        raise CliError(f"Ordner nicht gefunden: {a.folder}")
    files = renamer.sort_files(renamer.list_files(a.folder, a.filter, a.recursive), a.sort)
    rules = _rules_from_args(a)
    try:
        plan = renamer.build_plan(files, rules, a.include_ext)
    except Exception as exc:
        raise CliError(f"Regel-Fehler: {exc}")
    status = renamer.check_plan(plan)
    items = [{"old": src, "new_name": new, "status": st} for (src, new), st in zip(plan, status)]
    conflicts = [i for i in items if i["status"].startswith("⚠")]
    return plan, items, conflicts, rules


# --- Befehle -------------------------------------------------------------------
def cmd_leftovers(a, journal):
    roots = _split_paths(a.root) if a.root else None
    if roots:
        missing = [r for r in roots if not os.path.isdir(r)]
        if missing:
            raise CliError(f"Ordner nicht gefunden: {', '.join(missing)}")
    sources, real = _startup_sources()
    extra = []
    if real:
        try:
            extra = [os.path.dirname(e.target) for e in autostart.list_all(True, False, False) if e.target]
        except Exception:
            pass
    kd = leftovers.collect_known(extra_paths=extra)
    res = leftovers.scan_leftovers(roots, kd, old_days=a.old_days)
    limit = leftovers.CONF_ORDER[a.min_confidence]
    res = [c for c in res if leftovers.CONF_ORDER[c.confidence] <= limit]
    total = len(res)
    if a.limit:
        res = res[:a.limit]
    return {"roots": roots or leftovers.default_roots(), "known_programs": kd.programs,
            "count": total, "shown": len(res), "candidates": [_candidate_json(c) for c in res]}


def cmd_empty_dirs(a, journal):
    if not os.path.isdir(a.folder):
        raise CliError(f"Ordner nicht gefunden: {a.folder}")
    res = leftovers.find_empty_dirs(os.path.abspath(a.folder))
    return {"folder": os.path.abspath(a.folder), "count": len(res), "empty_dirs": [c.path for c in res]}


def cmd_trash(a, journal):
    paths = _split_paths(a.paths)
    if not paths:
        raise CliError("Keine Pfade angegeben.")
    refused = [{"path": p, "reason": r} for p in paths if (r := check_trash_target(p))]
    ok = [p for p in paths if not check_trash_target(p)]
    preview = [{"path": p, "is_dir": os.path.isdir(p),
                "size_bytes": foldersync.dir_size(p) if os.path.isdir(p) else os.path.getsize(p)} for p in ok]
    out = {"dry_run": a.dry_run, "to_trash": preview, "refused": refused}
    if a.dry_run or not ok:
        if refused and not ok:
            raise CliError("Nichts löschbar: " + "; ".join(f"{r['path']} ({r['reason']})" for r in refused))
        return out
    tx = journal.begin("CLI: Papierkorb", f"{len(ok)} Einträge in den Papierkorb")
    errors = []
    for p in ok:
        try:
            tx.add({"op": "trash", "path": p, "time": send_to_trash(p)})
        except Exception as exc:
            errors.append(f"{p}: {exc}")
    tx.finish("fertig" if not errors else "fertig (mit Fehlern)")
    out.update({"undo_id": tx.id, "trashed": [op["path"] for op in tx.data["ops"]], "errors": errors})
    return out


def cmd_startup_list(a, journal):
    sources, real = _startup_sources()
    entries = autostart.list_all(real, a.include_microsoft, not a.no_signatures, sources)
    entries = [e for e in entries if e.kind != "info"]
    if a.only_enabled:
        entries = [e for e in entries if e.enabled]
    return {"is_admin": is_admin(), "count": len(entries), "entries": [_entry_json(e) for e in entries]}


def cmd_startup_set(a, journal):
    if a.state not in ("on", "off"):
        raise CliError("--state muss 'on' oder 'off' sein")
    enable = a.state == "on"
    sources, real = _startup_sources()
    entries = [e for e in autostart.list_all(real, True, False, sources) if e.kind != "info"]
    matches = [e for e in entries if e.name.lower() == a.name.lower()
               and (not a.source or a.source.lower() in e.source.lower())]
    if not matches:
        raise CliError(f"Kein Autostart-Eintrag namens '{a.name}' gefunden (siehe startup-list).")
    if len(matches) > 1:
        raise CliError("Name ist nicht eindeutig, bitte --source angeben: "
                       + ", ".join(e.source for e in matches))
    e = matches[0]
    out = {"dry_run": a.dry_run, "entry": _entry_json(e), "new_state": a.state,
           "change_needed": e.enabled != enable}
    if e.enabled == enable or a.dry_run:
        return out
    tx = journal.begin("Autostart", f"CLI: {e.name} {'einschalten' if enable else 'ausschalten'}")
    try:
        autostart.set_enabled(e, enable, tx)
    except PermissionError:
        tx.finish("fehlgeschlagen")
        raise CliError(f"Zugriff verweigert – für '{e.name}' sind Adminrechte nötig.")
    tx.finish()
    out["undo_id"] = tx.id
    return out


def cmd_rename(a, journal):
    plan, items, conflicts, rules = _rename_plan(a)
    changes = [i for i in items if i["status"] == "ok"]
    out = {"dry_run": a.dry_run, "folder": os.path.abspath(a.folder),
           "rules": [r.describe() for r in rules], "files": len(items),
           "changes": len(changes), "conflicts": conflicts,
           "items": items if a.dry_run or conflicts else changes}
    if a.dry_run:
        return out
    if conflicts:
        raise CliError(f"{len(conflicts)} Konflikte – nichts umbenannt. Vorschau mit --dry-run ansehen.")
    pairs = renamer.executable_pairs(plan)
    if not pairs:
        return out
    tx = journal.begin("Umbenenner", f"CLI: {len(pairs)} Dateien umbenannt in {a.folder}")
    try:
        done = renamer.execute_renames(pairs)
    except Exception as exc:
        tx.finish("fehlgeschlagen")
        raise CliError(f"Umbenennen fehlgeschlagen, nichts geändert: {exc}")
    tx.add({"op": "rename_batch", "pairs": [list(p) for p in done]})
    tx.finish()
    out["undo_id"] = tx.id
    return out


def _compare(a):
    for p in (a.a, a.b):
        if not os.path.isdir(p):
            raise CliError(f"Ordner nicht gefunden: {p}")
    if os.path.normcase(os.path.abspath(a.a)) == os.path.normcase(os.path.abspath(a.b)):
        raise CliError("A und B sind derselbe Ordner.")
    return foldersync.compare(a.a, a.b, a.hash)


def _diff_json(it):
    return {"path": it.rel, "is_dir": it.is_dir, "status": it.status,
            "status_text": foldersync.STATUS_TEXT[it.status], "size_a": it.size_a,
            "size_b": it.size_b, "modified_a": _iso(it.mtime_a), "modified_b": _iso(it.mtime_b)}


def cmd_compare(a, journal):
    items = _compare(a)
    counts = {}
    for it in items:
        counts[it.status] = counts.get(it.status, 0) + 1
    shown = items if a.show_equal else [i for i in items if i.status != "gleich"]
    return {"a": os.path.abspath(a.a), "b": os.path.abspath(a.b), "counts": counts,
            "differences": [_diff_json(i) for i in shown]}


def cmd_sync(a, journal):
    if a.mode not in ("a2b", "b2a", "both"):
        raise CliError("--mode muss a2b, b2a oder both sein")
    items = _compare(a)
    foldersync.suggest(items, a.mode, a.delete_extra)
    actions = [{"path": it.rel, "action": it.action, "action_text": foldersync.ACTION_TEXT[it.action],
                "status": it.status} for it in items if it.action]
    out = {"dry_run": a.dry_run, "mode": a.mode, "planned": len(actions), "actions": actions,
           "preview": foldersync.plan_lines(items, a.a, a.b)}
    if a.dry_run or not actions:
        return out
    tx = journal.begin("Ordner-Sync", f"CLI: {len(actions)} Aktionen: {a.a} ⇄ {a.b}")
    errors = foldersync.execute(items, a.a, a.b, tx)
    tx.finish("fertig" if not errors else "fertig (mit Fehlern)")
    out.update({"undo_id": tx.id, "errors": errors})
    return out


def _tx_json(tx, details=False):
    d = {"id": tx["id"], "time": _iso(tx["time"]), "area": tx["area"],
         "description": tx["description"], "status": tx["status"], "steps": len(tx["ops"])}
    if details:
        d["step_details"] = [describe_op(op) for op in tx["ops"]]
    return d


def cmd_history(a, journal):
    txs = [t for t in reversed(journal.transactions) if t["ops"]]
    return {"count": len(txs), "transactions": [_tx_json(t, a.details) for t in txs[:a.limit]]}


def cmd_undo(a, journal):
    tx = journal.get(a.id)
    if not tx:
        raise CliError(f"Verlaufseintrag '{a.id}' nicht gefunden (siehe history).")
    if tx["status"] == "rückgängig gemacht":
        raise CliError("Dieser Eintrag wurde bereits rückgängig gemacht.")
    out = {"dry_run": a.dry_run, "transaction": _tx_json(tx, True)}
    if a.dry_run:
        return out
    errors = journal.undo(a.id)
    out.update({"status": journal.get(a.id)["status"], "errors": errors})
    if errors:
        raise CliError("Teilweise rückgängig gemacht: " + "; ".join(errors))
    return out


# --- Parser --------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cli.py", description="TidyKit – Kommandozeile mit JSON-Ausgabe")
    sub = p.add_subparsers(dest="command", required=True)

    def add(name, func, help_text):
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("--json", action="store_true", help="JSON ausgeben (Standard)")
        sp.set_defaults(func=func)
        return sp

    def dry(sp):
        sp.add_argument("--dry-run", action="store_true", help="nur Vorschau, nichts ändern")

    sp = add("leftovers", cmd_leftovers, "Programmreste und leere Ordner finden")
    sp.add_argument("--root", help="statt der Standardordner: Ordner (mehrere mit ;)")
    sp.add_argument("--min-confidence", default="mittel", choices=["hoch", "mittel", "niedrig"])
    sp.add_argument("--old-days", type=int, default=180)
    sp.add_argument("--limit", type=int, default=0)

    sp = add("empty-dirs", cmd_empty_dirs, "leere Ordner in einem Ordner finden")
    sp.add_argument("--folder", required=True)

    sp = add("trash", cmd_trash, "Dateien/Ordner in den Papierkorb legen")
    sp.add_argument("--paths", required=True, help="Pfade, mehrere mit ;")
    dry(sp)

    sp = add("startup-list", cmd_startup_list, "Autostart-Einträge mit Bewertung")
    sp.add_argument("--include-microsoft", action="store_true")
    sp.add_argument("--no-signatures", action="store_true")
    sp.add_argument("--only-enabled", action="store_true")

    sp = add("startup-set", cmd_startup_set, "Autostart-Eintrag ein-/ausschalten")
    sp.add_argument("--name", required=True)
    sp.add_argument("--state", required=True, help="on oder off")
    sp.add_argument("--source", help="Teil der Quelle zur Unterscheidung, z. B. 'Benutzer'")
    dry(sp)

    sp = add("rename", cmd_rename, "Dateien nach Regeln umbenennen")
    sp.add_argument("--folder", required=True)
    sp.add_argument("--filter", default="*", help="z. B. *.jpg;*.png")
    sp.add_argument("--recursive", action="store_true")
    sp.add_argument("--sort", default="name", choices=["name", "date", "size"])
    sp.add_argument("--include-ext", action="store_true")
    sp.add_argument("--find")
    sp.add_argument("--replace")
    sp.add_argument("--regex")
    sp.add_argument("--regex-replace")
    sp.add_argument("--case-sensitive", action="store_true")
    sp.add_argument("--case")
    sp.add_argument("--template")
    sp.add_argument("--start", type=int, default=1)
    sp.add_argument("--step", type=int, default=1)
    sp.add_argument("--trim", action="store_true")
    sp.add_argument("--underscores", action="store_true")
    sp.add_argument("--rules", help="JSON-Liste von Regeln, z. B. [{\"kind\":\"replace\",\"find\":\"a\",\"repl\":\"b\"}]")
    dry(sp)

    for name, func, text in (("compare", cmd_compare, "zwei Ordner vergleichen"),
                             ("sync", cmd_sync, "zwei Ordner abgleichen")):
        sp = add(name, func, text)
        sp.add_argument("--a", required=True)
        sp.add_argument("--b", required=True)
        sp.add_argument("--hash", action="store_true")
        if name == "compare":
            sp.add_argument("--show-equal", action="store_true")
        else:
            sp.add_argument("--mode", default="a2b")
            sp.add_argument("--delete-extra", action="store_true")
            dry(sp)

    sp = add("history", cmd_history, "Verlauf der Änderungen")
    sp.add_argument("--limit", type=int, default=20)
    sp.add_argument("--details", action="store_true")

    sp = add("undo", cmd_undo, "Änderung rückgängig machen")
    sp.add_argument("--id", required=True)
    dry(sp)
    return p


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    try:
        result = args.func(args, Journal())
    except CliError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Fehler: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    payload = {"ok": not result.get("errors"), "command": args.command}
    payload.update(result)
    print(json.dumps(payload, ensure_ascii=False, indent=1))
    if payload.get("errors"):
        print("Fehler bei einzelnen Schritten: " + "; ".join(payload["errors"]), file=sys.stderr)
        return 1
    return 0
