import gzip
import importlib
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
serve = importlib.import_module("serve")


class GzipCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "transactions.json"
        self.path.write_text(json.dumps({"rows": list(range(2000))}), encoding="utf-8")

    def test_gzipped_bytes_round_trip_and_are_cached_per_version(self):
        body, mtime = serve.gzipped_static(self.path)
        self.assertEqual(json.loads(gzip.decompress(body)), {"rows": list(range(2000))})
        self.assertLess(len(body), self.path.stat().st_size)
        again, _ = serve.gzipped_static(self.path)
        self.assertIs(again, body)
        # A rewritten file gets fresh bytes and the old version is dropped.
        time.sleep(0.01)
        self.path.write_text(json.dumps({"rows": []}), encoding="utf-8")
        os.utime(self.path, None)
        fresh, _ = serve.gzipped_static(self.path)
        self.assertEqual(json.loads(gzip.decompress(fresh)), {"rows": []})
        self.assertEqual([k for k in serve._GZIP_CACHE if k[0] == str(self.path)].__len__(), 1)

    def test_missing_file_is_none(self):
        self.assertIsNone(serve.gzipped_static(Path(self.temp.name) / "absent.json"))


class CodeChangedTests(unittest.TestCase):
    def test_flag_follows_the_startup_snapshot(self):
        with patch.object(serve, "CODE_AT_START", serve._code_snapshot()):
            self.assertFalse(serve.code_changed())
        with patch.object(serve, "CODE_AT_START", dict(serve._code_snapshot(), extra=1)):
            self.assertTrue(serve.code_changed())


if __name__ == "__main__":
    unittest.main()
