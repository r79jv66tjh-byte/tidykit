"""Führt jeden Befehl aus toolhub.json so aus, wie ToolHub es tut – nur an Testdaten."""
import json
import os
import subprocess
import sys
import winreg

from helpers import TempDirCase, read, write

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(ROOT, "toolhub.json"), encoding="utf-8") as fh:
    MANIFEST = json.load(fh)
COMMANDS = {c["name"]: c for c in MANIFEST["commands"]}
TEST_BASE = r"Software\TidyKitCliTest"


class CliTests(TempDirCase):
    covered: set = set()

    def setUp(self):
        super().setUp()
        self.env = dict(os.environ, TIDYKIT_DATA=self.p("_data"), TIDYKIT_STARTUP_TEST_BASE=TEST_BASE)
        self.exe = self.p("tool.exe")
        write(self.exe, "MZ")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, TEST_BASE + r"\Run") as k:
            winreg.SetValueEx(k, "CliTestTool", 0, winreg.REG_SZ, f'"{self.exe}" --tray')

    def tearDown(self):
        for sub in (r"\Run", r"\Approved", ""):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, TEST_BASE + sub)
            except OSError:
                pass
        super().tearDown()

    def run_cmd(self, cmd, expect_ok=True, **values):
        """Baut den Aufruf aus toolhub.json (run + args) und liefert das JSON."""
        name = cmd
        spec = COMMANDS[cmd]
        argv = [sys.executable if a == "python" else a for a in spec["run"]]
        known = {a["name"]: a for a in spec["args"]}
        for key, value in values.items():
            arg = known[key]  # KeyError = Argument fehlt im Manifest
            if arg["type"] == "boolean":
                if value:
                    argv.append(arg["flag"])
            else:
                argv += [arg["flag"], str(value)]
        for arg in spec["args"]:
            if arg["required"]:
                self.assertIn(arg["name"], values, f"{name}: Pflichtargument {arg['name']} fehlt im Test")
        proc = subprocess.run(argv, cwd=ROOT, env=self.env, capture_output=True, timeout=180)
        out = proc.stdout.decode("utf-8")
        err = proc.stderr.decode("utf-8")
        if expect_ok:
            self.assertEqual(proc.returncode, 0, f"{name}: {err}")
            data = json.loads(out)
            self.assertTrue(data["ok"])
            self.assertEqual(data["command"], spec["run"][2])
            CliTests.covered.add(name)
            return data
        self.assertNotEqual(proc.returncode, 0)
        self.assertTrue(err.strip(), "Fehlermeldung auf stderr erwartet")
        return err

    # --- Manifest -----------------------------------------------------------
    def test_manifest_format(self):
        self.assertEqual(MANIFEST["name"], "TidyKit")
        self.assertTrue(MANIFEST["description"])
        for c in MANIFEST["commands"]:
            self.assertEqual(set(c), {"name", "description", "run", "args", "readonly"}, c["name"])
            self.assertEqual(c["run"][:2], ["python", "cli.py"])
            for a in c["args"]:
                self.assertEqual(set(a), {"name", "flag", "type", "required", "description"})
                self.assertIn(a["type"], ("string", "integer", "boolean"))
            if not c["readonly"]:
                self.assertIn("dry_run", {a["name"] for a in c["args"]}, f"{c['name']} braucht --dry-run")

    # --- Lesende Befehle ----------------------------------------------------
    def test_leftovers_and_empty_dirs(self):
        write(self.p("root", "AltesProgramm", "d.bin"), "123", self.days_ago(400))
        os.makedirs(self.p("root", "Leer", "auch_leer"))
        data = self.run_cmd("leftovers", root=self.p("root"), min_confidence="niedrig", limit=10)
        found = {os.path.basename(c["path"]): c for c in data["candidates"]}
        self.assertEqual(found["AltesProgramm"]["confidence"], "hoch")
        self.assertEqual(found["Leer"]["category"], "Leerer Ordner")
        data = self.run_cmd("empty-dirs", folder=self.p("root"))
        self.assertEqual(data["empty_dirs"], [self.p("root", "Leer")])

    def test_errors_go_to_stderr(self):
        err = self.run_cmd("empty-dirs", expect_ok=False, folder=self.p("gibtsnicht"))
        self.assertIn("nicht gefunden", err)
        err = self.run_cmd("trash", expect_ok=False, paths=os.environ["WINDIR"], dry_run=True)
        self.assertIn("Windows-Systemordner", err)
        err = self.run_cmd("rename", expect_ok=False, folder=self.tmp, dry_run=True)
        self.assertIn("Keine Regel", err)

    # --- Schreibende Befehle: dry-run, ausführen, rückgängig -----------------
    def test_trash(self):
        write(self.p("weg.txt"), "inhalt")
        data = self.run_cmd("trash", paths=self.p("weg.txt"), dry_run=True)
        self.assertTrue(os.path.exists(self.p("weg.txt")))
        self.assertEqual(data["to_trash"][0]["size_bytes"], 6)
        data = self.run_cmd("trash", paths=self.p("weg.txt"))
        self.assertFalse(os.path.exists(self.p("weg.txt")))
        self.undo(data["undo_id"])
        self.assertEqual(read(self.p("weg.txt")), "inhalt")

    def test_startup(self):
        data = self.run_cmd("startup-list", no_signatures=True)
        entry = next(e for e in data["entries"] if e["name"] == "CliTestTool")
        self.assertTrue(entry["enabled"])
        self.assertEqual(entry["target"], self.exe)
        self.run_cmd("startup-list", only_enabled=True, include_microsoft=True)
        data = self.run_cmd("startup-set", name="CliTestTool", state="off", dry_run=True)
        self.assertTrue(data["change_needed"])
        self.assertTrue(self.enabled())
        data = self.run_cmd("startup-set", name="CliTestTool", state="off", source="Test")
        self.assertFalse(self.enabled())
        self.undo(data["undo_id"])
        self.assertTrue(self.enabled())

    def enabled(self):
        data = self.run_cmd("startup-list", no_signatures=True)
        return next(e for e in data["entries"] if e["name"] == "CliTestTool")["enabled"]

    def test_rename(self):
        for n in ("b.jpg", "a.jpg", "notiz.txt"):
            write(self.p("ren", n), n)
        data = self.run_cmd("rename", folder=self.p("ren"), filter="*.jpg", template="Bild_{n:2}",
                            dry_run=True)
        self.assertEqual([i["new_name"] for i in data["items"]], ["Bild_01.jpg", "Bild_02.jpg"])
        self.assertEqual(sorted(os.listdir(self.p("ren"))), ["a.jpg", "b.jpg", "notiz.txt"])
        rules = json.dumps([{"kind": "replace", "find": ".", "repl": "_"}])
        data = self.run_cmd("rename", folder=self.p("ren"), rules=rules, include_ext=True, dry_run=True)
        self.assertEqual(data["changes"], 3)
        data = self.run_cmd("rename", folder=self.p("ren"), filter="*.jpg", find="a", replace="x",
                            case="upper", trim=True, sort="name", start=5, step=1,
                            regex="^b$", regex_replace="bee", case_sensitive=True, recursive=False)
        self.assertEqual(sorted(os.listdir(self.p("ren"))), ["BEE.jpg", "X.jpg", "notiz.txt"])
        self.undo(data["undo_id"])
        self.assertEqual(sorted(os.listdir(self.p("ren"))), ["a.jpg", "b.jpg", "notiz.txt"])

    def test_compare_and_sync(self):
        write(self.p("A", "neu.txt"), "neu", self.days_ago(1))
        write(self.p("B", "neu.txt"), "alt", self.days_ago(9))
        write(self.p("B", "extra.txt"), "e")
        write(self.p("A", "gleich.txt"), "g", self.days_ago(5))
        write(self.p("B", "gleich.txt"), "g", self.days_ago(5))
        data = self.run_cmd("compare", a=self.p("A"), b=self.p("B"), hash=True, show_equal=True)
        self.assertEqual(data["counts"], {"neuer_a": 1, "nur_b": 1, "gleich": 1})
        data = self.run_cmd("sync", a=self.p("A"), b=self.p("B"), mode="a2b", delete_extra=True, dry_run=True)
        self.assertEqual(data["planned"], 2)
        self.assertEqual(read(self.p("B", "neu.txt")), "alt")
        data = self.run_cmd("sync", a=self.p("A"), b=self.p("B"), mode="a2b", delete_extra=True, hash=True)
        self.assertEqual(read(self.p("B", "neu.txt")), "neu")
        self.assertFalse(os.path.exists(self.p("B", "extra.txt")))
        self.undo(data["undo_id"])
        self.assertEqual(read(self.p("B", "neu.txt")), "alt")
        self.assertTrue(os.path.exists(self.p("B", "extra.txt")))

    def undo(self, tx_id):
        hist = self.run_cmd("history", limit=5, details=True)
        self.assertIn(tx_id, [t["id"] for t in hist["transactions"]])
        data = self.run_cmd("undo", id=tx_id, dry_run=True)
        self.assertEqual(data["transaction"]["status"], "fertig")
        data = self.run_cmd("undo", id=tx_id)
        self.assertEqual(data["status"], "rückgängig gemacht")

    @classmethod
    def tearDownClass(cls):
        missing = set(COMMANDS) - cls.covered
        if missing and len(cls.covered) > 3:  # nur prüfen, wenn die ganze Klasse lief
            raise AssertionError(f"Befehle ohne Test: {sorted(missing)}")
