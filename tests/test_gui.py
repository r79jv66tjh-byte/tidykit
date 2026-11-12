"""Steuert das echte Hauptfenster mit Testdaten durch alle Tabs."""
import os
import time
import unittest
import winreg

from helpers import TempDirCase, read, write

from tidykit import autostart, leftovers
from tidykit.gui.app import App
from tidykit.gui.common import CHECK_ON, PreviewDialog
from tidykit.renamer import Rule

TEST_BASE = r"Software\TidyKitGuiTest"


class GuiTests(TempDirCase):
    def setUp(self):
        super().setUp()
        PreviewDialog.auto_answer = True
        exe = self.p("tool.exe")
        write(exe, "MZ")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, TEST_BASE + r"\Run") as k:
            winreg.SetValueEx(k, "GuiTestTool", 0, winreg.REG_SZ, f'"{exe}"')
        sources = ([autostart.RegSource("Test", "HKCU", TEST_BASE + r"\Run", 64, "HKCU",
                                        TEST_BASE + r"\Approved")], [])
        self.app = App(journal=self.journal, autostart_sources=sources, autoload=False)
        self.app.autostart.check_sig.set(False)

    def tearDown(self):
        PreviewDialog.auto_answer = None
        self.app.destroy()
        for sub in (r"\Run", r"\Approved", ""):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, TEST_BASE + sub)
            except OSError:
                pass
        super().tearDown()

    def wait(self, cond, timeout=30):
        end = time.time() + timeout
        while time.time() < end:
            self.app.update()
            if cond():
                return
            time.sleep(0.05)
        self.fail("Zeitüberschreitung")

    def undo_latest(self):
        latest = self.journal.transactions[-1]["id"]
        self.app.history.undo(latest)
        self.assertEqual(self.journal.get(latest)["status"], "rückgängig gemacht")

    def test_renamer_tab(self):
        for n in ("b.txt", "a.txt", "c.txt"):
            write(self.p("ren", n), n)
        tab = self.app.renamer
        tab.folder.set(self.p("ren"))
        tab.load_files()
        tab.add_rule(Rule("pattern", {"template": "Datei_{n:2}", "start": 1, "step": 1}))
        self.assertEqual([tab.tree.set(i, "new") for i in tab.tree.get_children()],
                         ["Datei_01.txt", "Datei_02.txt", "Datei_03.txt"])
        tab.execute()
        self.assertEqual(sorted(os.listdir(self.p("ren"))), ["Datei_01.txt", "Datei_02.txt", "Datei_03.txt"])
        self.assertEqual(read(self.p("ren", "Datei_01.txt")), "a.txt")
        self.undo_latest()
        self.assertEqual(sorted(os.listdir(self.p("ren"))), ["a.txt", "b.txt", "c.txt"])

    def test_sync_tab(self):
        write(self.p("A", "x.txt"), "neu", time.time())
        write(self.p("B", "x.txt"), "alt", time.time() - 999)
        write(self.p("B", "extra.txt"), "e")
        tab = self.app.sync
        tab.a.set(self.p("A"))
        tab.b.set(self.p("B"))
        done = []
        tab.compare(on_done=done.append)
        self.wait(lambda: done)
        tab.delete_extra.set(True)
        tab.suggest("a2b")
        tab.execute()
        self.assertEqual(read(self.p("B", "x.txt")), "neu")
        self.assertFalse(os.path.exists(self.p("B", "extra.txt")))
        self.undo_latest()
        self.assertEqual(read(self.p("B", "x.txt")), "alt")
        self.assertTrue(os.path.exists(self.p("B", "extra.txt")))

    def test_leftovers_tab(self):
        write(self.p("root", "AltesProgramm", "d.dat"), "123", self.days_ago(300))
        os.makedirs(self.p("root", "LeererOrdner"))
        tab = self.app.leftovers
        done = []
        tab.scan_programs([self.p("root")], leftovers.KnownData(), on_done=done.append)
        self.wait(lambda: done)
        names = {os.path.basename(c.path): iid for iid, c in tab.candidates.items()}
        self.assertEqual(set(names), {"AltesProgramm", "LeererOrdner"})
        for iid in names.values():
            tab.tree.set(iid, "check", CHECK_ON)
        tab.trash_checked()
        self.assertEqual(os.listdir(self.p("root")), [])
        self.undo_latest()
        self.assertEqual(sorted(os.listdir(self.p("root"))), ["AltesProgramm", "LeererOrdner"])

    def test_autostart_tab(self):
        tab = self.app.autostart
        done = []
        tab.load(on_done=done.append)
        self.wait(lambda: done)
        entry = next(e for e in tab.entries.values() if e.name == "GuiTestTool")
        self.assertTrue(entry.enabled)
        done.clear()
        tab.switch(False, [entry])
        self.wait(lambda: tab.entries and any(e.name == "GuiTestTool" and not e.enabled
                                              for e in tab.entries.values()))
        backups = os.listdir(os.path.join(os.environ["TIDYKIT_DATA"], "sicherungen"))
        self.assertTrue(any("GuiTestTool" in b for b in backups))
        self.undo_latest()
        self.wait(lambda: any(e.name == "GuiTestTool" and e.enabled for e in tab.entries.values()))


if __name__ == "__main__":
    unittest.main()
