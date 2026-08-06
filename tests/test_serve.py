import http.client
import importlib
import json
import shutil
import sys
import tempfile
import threading
import unittest
from email.message import Message
from http.server import ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
serve = importlib.import_module("serve")


class OwnerApiValidationTests(unittest.TestCase):
    def setUp(self):
        self.transactions = {"tx_abc123": {"id": "tx_abc123"}}

    def test_accepts_supported_owner_and_current_transaction(self):
        result = serve.validate_owner_request(
            {"id": "tx_abc123", "owner": "Shared"}, self.transactions
        )
        self.assertEqual(result, ("tx_abc123", "Shared"))

    def test_rejects_unknown_transaction(self):
        with self.assertRaisesRegex(ValueError, "not present"):
            serve.validate_owner_request(
                {"id": "tx_missing", "owner": "Nic"}, self.transactions
            )

    def test_rejects_unknown_owner(self):
        with self.assertRaisesRegex(ValueError, "Owner must"):
            serve.validate_owner_request(
                {"id": "tx_abc123", "owner": "Everyone"}, self.transactions
            )

    def test_accepts_current_risk_group(self):
        transactions = {
            "tx_a": {
                "id": "tx_a",
                "risk": {"groupIds": ["tx_a", "tx_b"]},
            },
            "tx_b": {"id": "tx_b"},
        }
        result = serve.validate_risk_review_request(
            {"ids": ["tx_b", "tx_a"], "recognized": True},
            transactions,
        )
        self.assertEqual(result, (["tx_a", "tx_b"], True))

    def test_rejects_non_risk_group(self):
        with self.assertRaisesRegex(ValueError, "not present"):
            serve.validate_risk_review_request(
                {"ids": ["tx_abc123"], "recognized": True},
                self.transactions,
            )

    def test_accepts_trimmed_remark(self):
        result = serve.validate_remark_request(
            {"id": "tx_abc123", "remark": "  Vacuum   cleaner  "},
            self.transactions,
        )
        self.assertEqual(result, ("tx_abc123", "Vacuum cleaner"))

    def test_rejects_overlong_remark(self):
        with self.assertRaisesRegex(ValueError, "240"):
            serve.validate_remark_request(
                {"id": "tx_abc123", "remark": "x" * 241},
                self.transactions,
            )

    def test_accepts_transaction_detail_edits(self):
        result = serve.validate_transaction_detail_request(
            {
                "id": "tx_abc123",
                "owner": "Nic",
                "category": "Shopping",
                "displayName": "  Aftershock   laptop ",
                "remark": "  Personal   computer ",
            },
            self.transactions,
        )
        self.assertEqual(
            result,
            (
                "tx_abc123",
                "Nic",
                "Shopping",
                "Aftershock laptop",
                "Personal computer",
            ),
        )

    def test_rejects_unknown_transaction_detail_category(self):
        with self.assertRaisesRegex(ValueError, "supported"):
            serve.validate_transaction_detail_request(
                {
                    "id": "tx_abc123",
                    "owner": "Nic",
                    "category": "Mystery",
                    "displayName": "",
                    "remark": "",
                },
                self.transactions,
            )

    def test_audit_entry_records_before_and_after(self):
        entry = serve.make_audit_entry(
            {
                "id": "tx_abc123",
                "date": "2025-01-02",
                "description": "STATEMENT MERCHANT",
                "displayName": "Friendly merchant",
                "amount": 12.34,
            },
            "Updated transaction details",
            [{"field": "Category", "before": "Other", "after": "Shopping"}],
        )
        self.assertEqual(entry["transactionId"], "tx_abc123")
        self.assertEqual(entry["description"], "Friendly merchant")
        self.assertEqual(entry["changes"][0]["before"], "Other")
        self.assertTrue(entry["id"].startswith("audit_"))

    def test_audit_entry_skips_noop(self):
        self.assertIsNone(
            serve.make_audit_entry(
                {"id": "tx_abc123"},
                "Updated remarks",
                [],
            )
        )


def make_headers(**fields):
    headers = Message()
    for name, value in fields.items():
        if value is not None:
            headers[name.replace("_", "-")] = value
    return headers


class LocalRequestGuardTests(unittest.TestCase):
    """The guard decides, per request, whether a caller is the local dashboard."""

    def guard(self, **fields):
        handler = serve.FinanceHandler.__new__(serve.FinanceHandler)
        handler.headers = make_headers(**fields)
        return handler.local_request()

    def test_accepts_both_dashboard_host_forms(self):
        for host in ("localhost:3402", "127.0.0.1:3402", "localhost", "127.0.0.1"):
            with self.subTest(host=host):
                self.assertTrue(self.guard(Host=host))

    def test_accepts_navigation_without_origin(self):
        self.assertTrue(self.guard(Host="localhost:3402"))

    def test_accepts_matching_local_origin(self):
        self.assertTrue(
            self.guard(Host="127.0.0.1:3402", Origin="http://localhost:3402")
        )

    def test_rejects_rebound_host(self):
        for host in ("evil.com", "evil.com:3402", "192.168.1.9:3402"):
            with self.subTest(host=host):
                self.assertFalse(self.guard(Host=host))

    def test_rejects_missing_host(self):
        self.assertFalse(self.guard())

    def test_rejects_cross_site_origin(self):
        self.assertFalse(
            self.guard(Host="localhost:3402", Origin="https://evil.com")
        )


class StaticFileGuardTests(unittest.TestCase):
    """A real server on an ephemeral port, serving a throwaway directory."""

    @classmethod
    def setUpClass(cls):
        cls.directory = Path(tempfile.mkdtemp(prefix="serve-test-"))
        (cls.directory / "data").mkdir()
        (cls.directory / "index.html").write_text("<h1>dashboard</h1>", encoding="utf-8")
        (cls.directory / "data" / "transactions.json").write_text(
            json.dumps({"transactions": [{"id": "tx_secret"}]}), encoding="utf-8"
        )

        directory = str(cls.directory)

        class TestHandler(serve.FinanceHandler):
            def __init__(self, request, client_address, server):
                # Skip FinanceHandler.__init__, which pins the real app directory.
                super(serve.FinanceHandler, self).__init__(
                    request, client_address, server, directory=directory
                )

            def log_message(self, *args):
                pass

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), TestHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(timeout=5)
        cls.server.server_close()
        shutil.rmtree(cls.directory, ignore_errors=True)

    def get(self, path, **headers):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            sent = {
                name.replace("_", "-"): value
                for name, value in headers.items()
                if value is not None
            }
            connection.request("GET", path, headers=sent)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_rejects_static_data_for_rebound_host(self):
        status, body = self.get("/data/transactions.json", Host="evil.com")
        self.assertEqual(status, 403)
        self.assertNotIn(b"tx_secret", body)

    def test_rejects_static_data_for_cross_site_origin(self):
        status, body = self.get(
            "/data/transactions.json",
            Host="127.0.0.1:%d" % self.port,
            Origin="https://evil.com",
        )
        self.assertEqual(status, 403)
        self.assertNotIn(b"tx_secret", body)

    def test_serves_static_data_for_local_host(self):
        for host in ("127.0.0.1:%d" % self.port, "localhost:%d" % self.port):
            with self.subTest(host=host):
                status, body = self.get("/data/transactions.json", Host=host)
                self.assertEqual(status, 200)
                self.assertIn(b"tx_secret", body)

    def test_serves_dashboard_index(self):
        status, body = self.get("/", Host="localhost:%d" % self.port)
        self.assertEqual(status, 200)
        self.assertIn(b"dashboard", body)

    def test_refuses_directory_listing(self):
        status, body = self.get("/data/", Host="127.0.0.1:%d" % self.port)
        self.assertEqual(status, 404)
        self.assertNotIn(b"transactions.json", body)


if __name__ == "__main__":
    unittest.main()
