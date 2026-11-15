import os
import unittest
import winreg

from helpers import TempDirCase, read, write

from tidykit import autostart, foldersync, leftovers, renamer, trash
from tidykit.renamer import Rule


class TrashTests(TempDirCase):
    def test_trash_and_restore_file_and_folder(self):
        f = self.p("a.txt")
        write(f, "hallo")
        write(self.p("ordner", "b.txt"), "b")
        t1 = trash.send_to_trash(f)
        t2 = trash.send_to_trash(self.p("ordner"))
        self.assertFalse(os.path.exists(f))
        self.assertFalse(os.path.exists(self.p("ordner")))
        trash.restore_from_trash(f, t1)
        trash.restore_from_trash(self.p("ordner"), t2)
        self.assertEqual(read(f), "hallo")
        self.assertTrue(os.path.isfile(self.p("ordner", "b.txt")))

    def test_restore_with_short_path_name(self):
        # Windows can hand out 8.3 names (C:\\Users\\RUNNER~1); the Recycle Bin stores the long name.
        import ctypes
        long_dir = self.p("Langer Ordnername")
        write(os.path.join(long_dir, "c.txt"), "kurz")
        buf = ctypes.create_unicode_buffer(260)
        ctypes.windll.kernel32.GetShortPathNameW(long_dir, buf, 260)
        if os.path.normcase(buf.value) == os.path.normcase(long_dir):
            self.skipTest("Laufwerk erzeugt keine 8.3-Namen")
        short_file = os.path.join(buf.value, "c.txt")
        t = trash.send_to_trash(short_file)
        trash.restore_from_trash(short_file, t)
        self.assertEqual(read(os.path.join(long_dir, "c.txt")), "kurz")

    def test_restore_refuses_to_overwrite(self):
        f = self.p("a.txt")
        write(f, "alt")
        t = trash.send_to_trash(f)
        write(f, "neu")
        with self.assertRaises(trash.TrashError):
            trash.restore_from_trash(f, t)
        os.remove(f)
        trash.restore_from_trash(f, t)  # aufräumen
        self.assertEqual(read(f), "alt")


class RenamerTests(TempDirCase):
    def make_files(self, names):
        for n in names:
            write(self.p("bilder", n), n)
        return renamer.sort_files(renamer.list_files(self.p("bilder"), "*.jpg"))

    def test_rules_and_preview(self):
        files = self.make_files(["IMG 2.jpg", "IMG 10.jpg", "IMG 1.jpg", "notiz.txt"])
        self.assertEqual([os.path.basename(f) for f in files], ["IMG 1.jpg", "IMG 2.jpg", "IMG 10.jpg"])
        rules = [Rule("replace", {"find": "img ", "repl": "Urlaub_"}),
                 Rule("regex", {"pattern": r"_(\d+)$", "repl": "-$1"}),
                 Rule("case", {"mode": "lower"}),
                 Rule("pattern", {"template": "{n:3}_{name}", "start": 1, "step": 1})]
        plan = renamer.build_plan(files, rules)
        self.assertEqual([n for _, n in plan], ["001_urlaub-1.jpg", "002_urlaub-2.jpg", "003_urlaub-10.jpg"])
        self.assertEqual(renamer.check_plan(plan), ["ok"] * 3)

    def test_conflicts_detected(self):
        files = self.make_files(["a.jpg", "b.jpg"])
        write(self.p("bilder", "fremd.jpg"))
        plan = renamer.build_plan(files, [Rule("pattern", {"template": "gleich"})])
        self.assertTrue(all(s == "⚠ Name doppelt" for s in renamer.check_plan(plan)))
        plan = renamer.build_plan(files[:1], [Rule("pattern", {"template": "fremd"})])
        self.assertEqual(renamer.check_plan(plan), ["⚠ existiert bereits"])
        plan = renamer.build_plan(files[:1], [Rule("pattern", {"template": "a:b"})])
        self.assertEqual(renamer.check_plan(plan), ["⚠ ungültige Zeichen"])

    def test_swap_and_undo(self):
        files = self.make_files(["a.jpg", "b.jpg"])
        plan = [(files[0], "b.jpg"), (files[1], "a.jpg")]  # Tausch
        tx = self.journal.begin("Umbenenner", "Tausch")
        done = renamer.execute_renames(renamer.executable_pairs(plan))
        tx.add({"op": "rename_batch", "pairs": [list(p) for p in done]})
        tx.finish()
        self.assertEqual(read(self.p("bilder", "a.jpg")), "b.jpg")
        self.assertEqual(self.journal.undo(tx.id), [])
        self.assertEqual(read(self.p("bilder", "a.jpg")), "a.jpg")

    def test_case_only_rename(self):
        files = self.make_files(["foto.jpg"])
        plan = renamer.build_plan(files, [Rule("case", {"mode": "upper"})], include_ext=True)
        self.assertEqual(renamer.check_plan(plan), ["ok"])
        renamer.execute_renames(renamer.executable_pairs(plan))
        self.assertIn("FOTO.JPG", os.listdir(self.p("bilder")))


class SyncTests(TempDirCase):
    def setUp(self):
        super().setUp()
        old, new = self.days_ago(10), self.days_ago(1)
        write(self.p("A", "gleich.txt"), "same", old)
        write(self.p("B", "gleich.txt"), "same", old)
        write(self.p("A", "neu_in_a.txt"), "a", old)
        write(self.p("B", "nur_b.txt"), "b", old)
        write(self.p("A", "doc.txt"), "neue Version", new)
        write(self.p("B", "doc.txt"), "alt", old)
        write(self.p("A", "sub", "tief.txt"), "t", old)
        write(self.p("B", "sub", "tief.txt"), "t", old)
        write(self.p("A", "nurordner", "x.txt"), "x", old)

    def status(self, items):
        return {i.rel: i.status for i in items}

    def test_compare(self):
        st = self.status(foldersync.compare(self.p("A"), self.p("B")))
        self.assertEqual(st, {"doc.txt": "neuer_a", "gleich.txt": "gleich", "neu_in_a.txt": "nur_a",
                              "nur_b.txt": "nur_b", "nurordner": "nur_a",
                              os.path.join("sub", "tief.txt"): "gleich"})

    def test_hash_detects_same_size_change(self):
        write(self.p("A", "gleich.txt"), "SAME", self.days_ago(10))
        st = self.status(foldersync.compare(self.p("A"), self.p("B"), use_hash=False))
        self.assertEqual(st["gleich.txt"], "gleich")
        st = self.status(foldersync.compare(self.p("A"), self.p("B"), use_hash=True))
        self.assertEqual(st["gleich.txt"], "verschieden")

    def test_mirror_and_undo(self):
        items = foldersync.compare(self.p("A"), self.p("B"))
        foldersync.suggest(items, "a2b", delete_extra=True)
        tx = self.journal.begin("Ordner-Sync", "Test")
        errors = foldersync.execute(items, self.p("A"), self.p("B"), tx)
        tx.finish()
        self.assertEqual(errors, [])
        after = foldersync.compare(self.p("A"), self.p("B"))
        self.assertTrue(all(i.status == "gleich" for i in after), self.status(after))
        self.assertEqual(read(self.p("B", "doc.txt")), "neue Version")
        # Rückgängig: alter Zustand von B ist wieder da
        self.assertEqual(self.journal.undo(tx.id), [])
        self.assertEqual(read(self.p("B", "doc.txt")), "alt")
        self.assertTrue(os.path.exists(self.p("B", "nur_b.txt")))
        self.assertFalse(os.path.exists(self.p("B", "neu_in_a.txt")))
        self.assertFalse(os.path.exists(self.p("B", "nurordner")))


class LeftoverTests(TempDirCase):
    def test_is_within_matches_short_and_long_names(self):
        import ctypes
        from tidykit.winutil import is_within
        long_dir = self.p("Langer Ordnername")
        write(os.path.join(long_dir, "x.txt"))
        buf = ctypes.create_unicode_buffer(260)
        ctypes.windll.kernel32.GetShortPathNameW(long_dir, buf, 260)
        if os.path.normcase(buf.value) == os.path.normcase(long_dir):
            self.skipTest("Laufwerk erzeugt keine 8.3-Namen")
        self.assertTrue(is_within(os.path.join(buf.value, "x.txt"), long_dir))
        self.assertTrue(is_within(os.path.join(long_dir, "x.txt"), buf.value))

    def setUp(self):
        super().setUp()
        r = self.p("AppData")
        write(self.p("AppData", "Contoso Editor", "settings.ini"), "x", self.days_ago(400))
        write(self.p("AppData", "AltesSpiel", "daten.bin"), "x" * 1000, self.days_ago(400))
        write(self.p("AppData", "SpielMitStand", "Saved", "slot1.sav"), "x", self.days_ago(400))
        write(self.p("AppData", "VstPluginReste", "x.dll"), "x", self.days_ago(400))
        write(self.p("AppData", "FrischesTool", "cfg.json"), "x", self.days_ago(2))
        os.makedirs(self.p("AppData", "LeererRest", "unter", "noch"))
        write(self.p("AppData", "Microsoft", "x.txt"), "x", self.days_ago(400))
        write(self.p("AppData", "InUse", "cache.bin"), "x", self.days_ago(400))
        write(self.p("AppData", "Programs", "VergessenesProgramm", "a.exe"), "x", self.days_ago(400))
        self.root = r
        self.known = leftovers.KnownData(names={"contosoeditor"}, texts=["contosoeditor"],
                                         paths=[self.p("AppData", "InUse", "bin")])

    def test_scan(self):
        res = {os.path.basename(c.path): c for c in leftovers.scan_leftovers([self.root], self.known)}
        self.assertNotIn("Contoso Editor", res)     # installiert
        self.assertNotIn("Microsoft", res)          # geschützt
        self.assertNotIn("InUse", res)              # Pfad wird benutzt
        self.assertEqual(res["AltesSpiel"].confidence, "hoch")
        self.assertEqual(res["AltesSpiel"].size, 1000)
        self.assertEqual(res["FrischesTool"].confidence, "niedrig")
        self.assertEqual(res["SpielMitStand"].confidence, "mittel")    # Spielstände
        self.assertEqual(res["VstPluginReste"].confidence, "niedrig")  # geteilte Komponente
        outside = leftovers.scan_leftovers([self.root], self.known, user_roots=[])
        self.assertEqual({os.path.basename(c.path): c for c in outside}["AltesSpiel"].confidence,
                         "mittel")  # außerhalb von AppData nie "hoch"
        self.assertEqual(res["LeererRest"].category, "Leerer Ordner")
        self.assertIn("VergessenesProgramm", res)   # Container "Programs" wird durchsucht

    def test_empty_dirs(self):
        os.makedirs(self.p("leer", "a", "b"))
        os.makedirs(self.p("leer", "c"))
        write(self.p("leer", "d", "datei.txt"))
        os.makedirs(self.p("leer", "d", "e"))
        found = sorted(os.path.relpath(c.path, self.p("leer")) for c in leftovers.find_empty_dirs(self.p("leer")))
        self.assertEqual(found, ["a", "c", os.path.join("d", "e")])


TEST_BASE = r"Software\TidyKitTest"


class AutostartTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.exe = self.p("tool.exe")
        write(self.exe, "MZ")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, TEST_BASE + r"\Run") as k:
            winreg.SetValueEx(k, "TestTool", 0, winreg.REG_SZ, f'"{self.exe}" --minimized')
            winreg.SetValueEx(k, "Kaputt", 0, winreg.REG_SZ, r"C:\gibtsnicht\weg.exe")
        self.sources = ([autostart.RegSource("Test", "HKCU", TEST_BASE + r"\Run", 64, "HKCU",
                                             TEST_BASE + r"\Approved")], [])

    def tearDown(self):
        for sub in (r"\Run", r"\Approved", ""):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, TEST_BASE + sub)
            except OSError:
                pass
        super().tearDown()

    def test_list_rate_toggle_undo(self):
        entries = {e.name: e for e in autostart.list_all(False, False, False, self.sources)}
        self.assertEqual(entries["TestTool"].target, self.exe)
        self.assertTrue(entries["TestTool"].enabled)
        self.assertEqual(entries["Kaputt"].rating, "Verwaist")
        tx = self.journal.begin("Autostart", "Test")
        autostart.set_enabled(entries["TestTool"], False, tx)
        tx.finish()
        again = {e.name: e for e in autostart.list_all(False, False, False, self.sources)}
        self.assertFalse(again["TestTool"].enabled)
        # Run-Eintrag selbst ist unverändert erhalten
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, TEST_BASE + r"\Run") as k:
            self.assertIn("--minimized", winreg.QueryValueEx(k, "TestTool")[0])
        self.assertEqual(self.journal.undo(tx.id), [])
        self.assertIsNone(autostart.read_reg_value("HKCU", TEST_BASE + r"\Approved", "TestTool"))
        again = {e.name: e for e in autostart.list_all(False, False, False, self.sources)}
        self.assertTrue(again["TestTool"].enabled)

    def test_extract_target(self):
        self.assertEqual(autostart.extract_target(f'"{self.exe}" -x'), self.exe)
        self.assertEqual(autostart.extract_target(r"rundll32.exe C:\x\y.dll,Start"), r"C:\x\y.dll")
        self.assertTrue(autostart.extract_target("notepad.exe").lower().endswith("notepad.exe"))


if __name__ == "__main__":
    unittest.main()
