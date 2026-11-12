import os
import shutil
import tempfile
import time
import unittest

# Verlauf/Sicherungen der Tests nicht in den echten Datenordner schreiben
_DATA = tempfile.mkdtemp(prefix="oh_testdata_")
os.environ["TIDYKIT_DATA"] = _DATA


def write(path, text="x", mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


class TempDirCase(unittest.TestCase):
    """Testordner unter %TEMP% (Laufwerk C: hat einen Papierkorb)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="oh_test_")
        from tidykit.journal import Journal
        self.journal = Journal(os.path.join(self.tmp, "_verlauf.json"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def p(self, *parts):
        return os.path.join(self.tmp, *parts)

    @staticmethod
    def days_ago(n):
        return time.time() - n * 86400


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()
