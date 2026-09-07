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


class StaticRevalidationTests(unittest.TestCase):
    def test_browser_cache_revalidates_and_detects_same_size_rewrite(self):
        import http.client
        import threading
        from http.server import ThreadingHTTPServer
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'data.json'
            path.write_text(json.dumps({'rows': list(range(2000))}))
            class Handler(serve.FinanceHandler):
                def do_GET(self):
                    self.serve_static()
                def log_message(self, *args):
                    pass
            with patch.object(serve, 'APP_DIR', Path(folder)):
                server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    def request(headers):
                        conn = http.client.HTTPConnection('127.0.0.1', server.server_port)
                        conn.request('GET', '/data.json', headers=headers)
                        response = conn.getresponse()
                        result = response.status, dict(response.getheaders()), response.read()
                        conn.close()
                        return result
                    status, headers, body = request({'Accept-Encoding': 'gzip'})
                    self.assertEqual(status, 200)
                    self.assertEqual(headers['Cache-Control'], 'private, no-cache')
                    self.assertEqual(json.loads(gzip.decompress(body))['rows'][0], 0)
                    etag = headers['ETag']
                    status, _, body = request({'Accept-Encoding': 'gzip', 'If-None-Match': etag})
                    self.assertEqual((status, body), (304, b''))
                    old = path.stat()
                    path.write_text(path.read_text().replace('[0,', '[9,', 1))
                    os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns + 1_000_000))
                    status, headers, body = request({'Accept-Encoding': 'gzip', 'If-None-Match': etag})
                    self.assertEqual(status, 200)
                    self.assertNotEqual(headers['ETag'], etag)
                    self.assertEqual(json.loads(gzip.decompress(body))['rows'][0], 9)
                    status, raw_headers, body = request({'Accept-Encoding': 'identity', 'If-None-Match': headers['ETag']})
                    self.assertEqual(status, 200)
                    self.assertNotEqual(raw_headers['ETag'], headers['ETag'])
                    self.assertEqual(json.loads(body)['rows'][0], 9)
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join()

if __name__ == "__main__":
    unittest.main()
