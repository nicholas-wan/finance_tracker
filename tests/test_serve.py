import http.client
import importlib
import io
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
        self.transactions = {
            "tx_abc123": {"id": "tx_abc123"},
            "tx_def456": {"id": "tx_def456"},
        }

    def test_accepts_supported_owner_and_current_transaction(self):
        result = serve.validate_owner_request(
            {"id": "tx_abc123", "owner": "Shared"}, self.transactions
        )
        # A single "id" still resolves, as a one-element batch.
        self.assertEqual(result, (["tx_abc123"], "Shared"))

    def test_accepts_a_batch_of_ids(self):
        result = serve.validate_owner_request(
            {"ids": ["tx_def456", "tx_abc123"], "owner": "Nic"}, self.transactions
        )
        self.assertEqual(result, (["tx_abc123", "tx_def456"], "Nic"))

    def test_collapses_a_repeated_id(self):
        # A duplicate would otherwise be counted twice in the audit row list.
        result = serve.validate_owner_request(
            {"ids": ["tx_abc123", "tx_abc123"], "owner": "Nic"}, self.transactions
        )
        self.assertEqual(result, (["tx_abc123"], "Nic"))

    def test_rejects_an_empty_batch(self):
        with self.assertRaisesRegex(ValueError, "Between 1 and"):
            serve.validate_owner_request(
                {"ids": [], "owner": "Nic"}, self.transactions
            )

    def test_rejects_a_batch_over_the_limit(self):
        oversized = ["tx_%06d" % n for n in range(serve.MAX_OWNER_BATCH + 1)]
        with self.assertRaisesRegex(ValueError, "Between 1 and"):
            serve.validate_owner_request(
                {"ids": oversized, "owner": "Nic"}, self.transactions
            )

    def test_rejects_a_batch_holding_an_unknown_transaction(self):
        with self.assertRaisesRegex(ValueError, "not present"):
            serve.validate_owner_request(
                {"ids": ["tx_abc123", "tx_missing"], "owner": "Nic"},
                self.transactions,
            )

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

    def risk_transactions(self, key=None, checks=("same-day-duplicate",)):
        key = key or ("a" * 64)
        risk = {
            "key": key,
            "groupIds": ["tx_a", "tx_b"],
            "checks": list(checks),
        }
        return {
            "tx_a": {"id": "tx_a", "risk": dict(risk, primary=True)},
            "tx_b": {"id": "tx_b", "risk": dict(risk, primary=False)},
        }

    def test_accepts_current_risk_group(self):
        result = serve.validate_risk_review_request(
            {"ids": ["tx_b", "tx_a"], "recognized": True},
            self.risk_transactions(),
        )
        self.assertEqual(
            result, (["tx_a", "tx_b"], True, "a" * 64, ["same-day-duplicate"])
        )

    def test_accepts_a_matching_signal_key(self):
        result = serve.validate_risk_review_request(
            {"ids": ["tx_a", "tx_b"], "recognized": False, "key": "a" * 64},
            self.risk_transactions(),
        )
        self.assertEqual(result[1], False)
        self.assertEqual(result[2], "a" * 64)

    def test_rejects_a_stale_signal_key(self):
        # The rows still form a group, but the checks that fired have changed,
        # so acknowledging the decision the page was showing would acknowledge
        # a reason the user never saw.
        with self.assertRaisesRegex(ValueError, "changed since this page loaded"):
            serve.validate_risk_review_request(
                {"ids": ["tx_a", "tx_b"], "recognized": True, "key": "b" * 64},
                self.risk_transactions(),
            )

    def test_rejects_a_malformed_signal_key(self):
        with self.assertRaisesRegex(ValueError, "malformed"):
            serve.validate_risk_review_request(
                {"ids": ["tx_a", "tx_b"], "recognized": True, "key": "nope"},
                self.risk_transactions(),
            )

    def test_rejects_a_partial_group(self):
        with self.assertRaisesRegex(ValueError, "not present"):
            serve.validate_risk_review_request(
                {"ids": ["tx_a"], "recognized": True},
                self.risk_transactions(),
            )

    def test_rejects_non_risk_group(self):
        with self.assertRaisesRegex(ValueError, "not present"):
            serve.validate_risk_review_request(
                {"ids": ["tx_abc123"], "recognized": True},
                self.transactions,
            )

    def test_rejects_a_group_without_a_signal_key(self):
        transactions = {
            "tx_a": {"id": "tx_a", "risk": {"groupIds": ["tx_a"]}},
        }
        with self.assertRaisesRegex(ValueError, "not present"):
            serve.validate_risk_review_request(
                {"ids": ["tx_a"], "recognized": True}, transactions
            )

    def test_accepts_current_account_review(self):
        result = serve.validate_account_review_request(
            {"id": "tx_abc123", "reviewed": True,
             "checksById": {"tx_abc123": ["large-transfer"]}}, self.transactions
        )
        # A single "id" still resolves, as a one-element batch.
        self.assertEqual(
            result,
            (["tx_abc123"], True, {"tx_abc123": ["large-transfer"]}),
        )

    def test_rejects_missing_account_review_transaction(self):
        with self.assertRaisesRegex(ValueError, "not present"):
            serve.validate_account_review_request(
                {"id": "tx_missing", "reviewed": True}, self.transactions
            )

    def test_accepts_a_batch_of_account_reviews(self):
        result = serve.validate_account_review_request(
            {"ids": ["tx_def456", "tx_abc123"], "reviewed": True,
             "checksById": {
                 "tx_abc123": ["large-transfer"],
                 "tx_def456": ["possible-duplicate"],
             }},
            self.transactions,
        )
        self.assertEqual(result, (
            ["tx_abc123", "tx_def456"], True,
            {"tx_abc123": ["large-transfer"],
             "tx_def456": ["possible-duplicate"]},
        ))

    def test_rejects_recognition_without_current_checks(self):
        with self.assertRaisesRegex(ValueError, "at least one current check"):
            serve.validate_account_review_request(
                {"id": "tx_abc123", "reviewed": True}, self.transactions
            )

    def test_rejects_an_unknown_account_review_check(self):
        with self.assertRaisesRegex(ValueError, "Unknown bank review check"):
            serve.validate_account_review_request(
                {"id": "tx_abc123", "reviewed": True,
                 "checksById": {"tx_abc123": ["invented-check"]}},
                self.transactions,
            )

    def test_collapses_a_repeated_account_review_id(self):
        result = serve.validate_account_review_request(
            {"ids": ["tx_abc123", "tx_abc123"], "reviewed": False},
            self.transactions,
        )
        self.assertEqual(result, (["tx_abc123"], False, {"tx_abc123": []}))

    def test_rejects_an_empty_account_review_batch(self):
        with self.assertRaisesRegex(ValueError, "Between 1 and"):
            serve.validate_account_review_request(
                {"ids": [], "reviewed": True}, self.transactions
            )

    def test_rejects_an_account_review_batch_over_the_limit(self):
        oversized = [
            "tx_%06d" % n for n in range(serve.MAX_ACCOUNT_REVIEW_BATCH + 1)
        ]
        with self.assertRaisesRegex(ValueError, "Between 1 and"):
            serve.validate_account_review_request(
                {"ids": oversized, "reviewed": True}, self.transactions
            )

    def test_rejects_a_whole_account_review_batch_for_one_unknown_id(self):
        # Half a review is worse than none: the page would show rows cleared
        # that were never written.
        with self.assertRaisesRegex(ValueError, "not present"):
            serve.validate_account_review_request(
                {"ids": ["tx_abc123", "tx_missing"], "reviewed": True},
                self.transactions,
            )

    def test_rejects_a_non_boolean_batch_decision(self):
        with self.assertRaisesRegex(ValueError, "true or false"):
            serve.validate_account_review_request(
                {"ids": ["tx_abc123"], "reviewed": "yes"}, self.transactions
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


# Lets a test say "send no Origin at all" (None) apart from "send the default".
_DEFAULT = object()


def make_headers(**fields):
    headers = Message()
    for name, value in fields.items():
        if value is not None:
            headers[name.replace("_", "-")] = value
    return headers


class LocalRequestGuardTests(unittest.TestCase):
    """The guard decides, per request, whether a caller is the local dashboard."""

    PORT = 3402

    def guard(self, port=None, **fields):
        handler = serve.FinanceHandler.__new__(serve.FinanceHandler)
        handler.headers = make_headers(**fields)
        return handler.local_request(port=port or self.PORT)

    def post_guard(self, port=None, **fields):
        handler = serve.FinanceHandler.__new__(serve.FinanceHandler)
        handler.headers = make_headers(**fields)
        return handler.same_origin_post(port=port or self.PORT)

    def test_accepts_every_dashboard_host_form(self):
        for host in ("localhost:3402", "127.0.0.1:3402", "[::1]:3402"):
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

    def test_rejects_host_on_another_port(self):
        # Another dev server on this machine is still "localhost"; only the
        # port tells the dashboard's own address apart from its neighbours.
        for host in ("localhost:8888", "127.0.0.1:5173", "[::1]:80"):
            with self.subTest(host=host):
                self.assertFalse(self.guard(Host=host))

    def test_portless_host_means_port_80(self):
        self.assertFalse(self.guard(Host="localhost"))
        self.assertTrue(self.guard(Host="localhost", port=80))

    def test_rejects_cross_site_origin(self):
        self.assertFalse(
            self.guard(Host="localhost:3402", Origin="https://evil.com")
        )

    def test_rejects_cross_port_local_origin(self):
        # The hole this closes: a Jupyter/Vite page on another localhost port.
        for origin in ("http://localhost:8888", "http://127.0.0.1", "http://[::1]:5173"):
            with self.subTest(origin=origin):
                self.assertFalse(self.guard(Host="localhost:3402", Origin=origin))

    def test_rejects_https_origin_on_our_port(self):
        self.assertFalse(
            self.guard(Host="localhost:3402", Origin="https://localhost:3402")
        )

    def test_null_origin_is_accepted_only_with_browser_same_origin_attestation(self):
        # Under some referrer policies the Fetch spec nulls Origin on a
        # same-origin POST. Sec-Fetch-Site cannot be set by page script, so
        # its same-origin value outranks a nulled Origin - and nothing else does.
        self.assertTrue(self.guard(
            Host="localhost:3402", Origin="null", Sec_Fetch_Site="same-origin"))
        self.assertTrue(self.post_guard(Origin="null", Sec_Fetch_Site="same-origin"))
        self.assertFalse(self.guard(
            Host="localhost:3402", Origin="null", Sec_Fetch_Site="cross-site"))
        self.assertFalse(self.post_guard(Origin="null", Sec_Fetch_Site="cross-site"))
        self.assertFalse(self.guard(
            Host="localhost:3402", Origin="http://localhost:8000",
            Sec_Fetch_Site="same-site"))

    def test_rejects_null_origin(self):
        for origin in ("null", "NULL"):
            with self.subTest(origin=origin):
                self.assertFalse(self.guard(Host="localhost:3402", Origin=origin))

    def test_post_accepts_our_own_origin(self):
        self.assertTrue(self.post_guard(Origin="http://localhost:3402"))

    def test_post_rejects_cross_port_origin(self):
        self.assertFalse(self.post_guard(Origin="http://localhost:8888"))

    def test_post_accepts_sec_fetch_site_without_origin(self):
        for site in ("same-origin", "none", "None"):
            with self.subTest(site=site):
                self.assertTrue(self.post_guard(Sec_Fetch_Site=site))

    def test_post_rejects_bare_request_without_origin_or_sec_fetch_site(self):
        self.assertFalse(self.post_guard())

    def test_post_rejects_cross_site_sec_fetch_site(self):
        for site in ("cross-site", "same-site"):
            with self.subTest(site=site):
                self.assertFalse(self.post_guard(Sec_Fetch_Site=site))

    def test_json_content_type_allows_parameters_only(self):
        self.assertTrue(serve.FinanceHandler.json_content_type("application/json"))
        self.assertTrue(serve.FinanceHandler.json_content_type(
            " Application/JSON ; charset=utf-8"))
        self.assertFalse(serve.FinanceHandler.json_content_type("text/plain"))
        self.assertFalse(serve.FinanceHandler.json_content_type(None))


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

    def test_rejects_static_data_for_another_local_port(self):
        # A page served by some other localhost dev server is not the dashboard.
        status, body = self.get(
            "/data/transactions.json",
            Host="127.0.0.1:%d" % self.port,
            Origin="http://localhost:%d" % (self.port + 1),
        )
        self.assertEqual(status, 403)
        self.assertNotIn(b"tx_secret", body)

    def test_rejects_static_data_for_host_naming_another_port(self):
        status, body = self.get(
            "/data/transactions.json", Host="127.0.0.1:%d" % (self.port + 1))
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


class SaveWritePathTests(unittest.TestCase):
    """The save/build/validate/rollback machinery against a throwaway manual/.

    Stub build and validate scripts stand in for the real pipeline: the build
    stub copies a pre-staged "rebuilt" transactions file into place and logs
    each run, and the validate stub either passes or fails on demand. Every
    test asserts the on-disk state of all files a save touches, because the
    rollback contract is exactly "all files or none".
    """

    TX_ID = "tx_write0001"
    SECOND_ID = "tx_write0002"
    BANK_ID = "tx_bank000001"
    SECOND_BANK_ID = "tx_bank000002"

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="serve-write-"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        manual = self.dir / "manual"
        manual.mkdir()
        data = self.dir / "data"
        data.mkdir()

        self.paths = {
            "OWNER_PATH": manual / "owner_tags.json",
            "REMARK_PATH": manual / "transaction_remarks.json",
            "OVERRIDE_PATH": manual / "transaction_overrides.json",
            "AUDIT_PATH": manual / "audit_history.json",
            "ACCOUNT_REVIEW_PATH": manual / "account_reviews.json",
            "TRANSACTIONS_PATH": data / "transactions.json",
            "ACCOUNT_TRANSACTIONS_PATH": data / "account_transactions.json",
        }
        seeds = {
            "OWNER_PATH": {"tagsById": {}},
            "REMARK_PATH": {"remarksById": {}},
            "OVERRIDE_PATH": {"overridesById": {}},
            "AUDIT_PATH": {"entries": []},
            "ACCOUNT_REVIEW_PATH": {"recognizedSignals": []},
            "TRANSACTIONS_PATH": {
                "transactions": [self.row()],
                "quality": {"seed": True},
            },
            "ACCOUNT_TRANSACTIONS_PATH": {
                "transactions": [
                    self.bank_row(),
                    self.bank_row(id=self.SECOND_BANK_ID, amount=-9.0),
                ],
            },
        }
        for name, payload in seeds.items():
            self.paths[name].write_text(
                json.dumps(payload, indent=1) + "\n", encoding="utf-8"
            )
        self.originals = {
            name: path.read_bytes() for name, path in self.paths.items()
        }

        self.log = self.dir / "script_log.txt"
        self.applied = self.dir / "applied.json"
        build = self.dir / "stub_build.py"
        build.write_text(
            "import pathlib, shutil\n"
            "here = pathlib.Path(__file__).parent\n"
            "with (here / 'script_log.txt').open('a') as log:\n"
            "    log.write('build\\n')\n"
            "if (here / 'applied.json').exists():\n"
            "    shutil.copy(here / 'applied.json', here / 'data' / 'transactions.json')\n",
            encoding="utf-8",
        )
        validate_ok = self.dir / "stub_validate_ok.py"
        validate_ok.write_text(
            "import pathlib\n"
            "here = pathlib.Path(__file__).parent\n"
            "with (here / 'script_log.txt').open('a') as log:\n"
            "    log.write('validate\\n')\n"
            "print('All checks passed')\n",
            encoding="utf-8",
        )
        validate_fail = self.dir / "stub_validate_fail.py"
        validate_fail.write_text(
            "import sys\n"
            "print('validation exploded on purpose')\n"
            "sys.exit(1)\n",
            encoding="utf-8",
        )
        self.scripts = {
            "build": build, "ok": validate_ok, "fail": validate_fail,
        }

        self.patched = []
        for name, path in self.paths.items():
            self.patch_module(name, path)
        self.patch_module("BUILD_SCRIPT", build)
        self.patch_module("VALIDATE_SCRIPT", validate_ok)

    def patch_module(self, name, value):
        self.patched.append((name, getattr(serve, name)))
        setattr(serve, name, value)
        self.addCleanup(lambda n=name: setattr(
            serve, n, dict(self.patched)[n]))

    def row(self, **overrides):
        base = {
            "id": self.TX_ID,
            "date": "2026-01-02",
            "description": "REDACTED MERCHANT",
            "amount": -12.5,
            "owner": "Nic",
            "ownerSource": "merchant-rule",
            "category": "Shopping",
            "displayName": "",
            "remark": "",
        }
        base.update(overrides)
        return base

    def bank_row(self, **overrides):
        base = {
            "id": self.BANK_ID,
            "date": "2026-01-03",
            "description": "REDACTED TRANSFER",
            "amount": -25.0,
        }
        base.update(overrides)
        return base

    def stage_rebuild(self, **overrides):
        """What the stub build will publish as the rebuilt dashboard."""
        self.stage_rebuild_rows([self.row(**overrides)])

    def stage_rebuild_rows(self, rows):
        self.applied.write_text(json.dumps({
            "transactions": rows,
            "quality": {"rebuilt": True},
        }), encoding="utf-8")

    def write_transactions(self, rows):
        """Replace the pre-save dashboard the save path reads current state from."""
        path = self.paths["TRANSACTIONS_PATH"]
        path.write_text(
            json.dumps({"transactions": rows, "quality": {"seed": True}}, indent=1),
            encoding="utf-8",
        )
        self.originals["TRANSACTIONS_PATH"] = path.read_bytes()

    def script_runs(self):
        if not self.log.exists():
            return []
        return self.log.read_text(encoding="utf-8").split()

    def read(self, name):
        return json.loads(self.paths[name].read_text(encoding="utf-8"))

    def assert_untouched(self, *names):
        for name in names:
            self.assertEqual(
                self.paths[name].read_bytes(), self.originals[name],
                "%s should have been rolled back to its original bytes" % name,
            )

    def backups(self, name):
        """Every rotating backup of one file, oldest first."""
        return serve.backup_generations(self.paths[name])

    def assert_backup_matches_original(self, name):
        generations = self.backups(name)
        self.assertTrue(generations, "no backup written for %s" % name)
        self.assertEqual(generations[-1].read_bytes(), self.originals[name])

    def test_save_owner_persists_tag_audit_and_reports_quality(self):
        self.stage_rebuild(owner="Shared", ownerSource="exact-id")
        result = serve.save_owner(self.TX_ID, "Shared")
        self.assertTrue(result["ok"])
        self.assertEqual(result["transaction"]["owner"], "Shared")
        self.assertEqual(result["quality"], {"rebuilt": True})
        self.assertEqual(self.read("OWNER_PATH")["tagsById"], {self.TX_ID: "Shared"})
        entries = self.read("AUDIT_PATH")["entries"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["action"], "Updated owner")
        self.assertEqual(self.script_runs(), ["build", "validate"])
        for name in ("OWNER_PATH", "AUDIT_PATH"):
            self.assert_backup_matches_original(name)

    def test_save_owner_tags_a_batch_under_one_rebuild_and_one_audit_entry(self):
        self.write_transactions([
            self.row(),
            self.row(id=self.SECOND_ID, owner="Untagged", ownerSource="unassigned"),
        ])
        self.stage_rebuild_rows([
            self.row(owner="Shared", ownerSource="exact-id"),
            self.row(id=self.SECOND_ID, owner="Shared", ownerSource="exact-id"),
        ])
        result = serve.save_owner([self.TX_ID, self.SECOND_ID], "Shared")
        self.assertTrue(result["ok"])
        self.assertEqual(
            [row["id"] for row in result["transactions"]],
            [self.TX_ID, self.SECOND_ID],
        )
        self.assertEqual(
            self.read("OWNER_PATH")["tagsById"],
            {self.TX_ID: "Shared", self.SECOND_ID: "Shared"},
        )
        entries = self.read("AUDIT_PATH")["entries"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(
            entries[0]["transactionIds"], [self.TX_ID, self.SECOND_ID])
        owner_change = next(
            c for c in entries[0]["changes"] if c["field"] == "Owner")
        # The batch spanned two starting owners; the audit records both.
        self.assertEqual(owner_change["before"], "Nic, Untagged")
        self.assertEqual(owner_change["after"], "Shared")
        # One rebuild for the whole batch, not one per row.
        self.assertEqual(self.script_runs(), ["build", "validate"])

    def test_unassigning_removes_the_tag_instead_of_pinning_untagged(self):
        # A literal "Untagged" tag outranks manual/owner_rules.json forever, so
        # clearing a tag must delete it and let the fallbacks speak again - even
        # when a fallback then names an owner.
        self.paths["OWNER_PATH"].write_text(
            json.dumps({"tagsById": {self.TX_ID: "Yx"}}, indent=1), encoding="utf-8")
        self.write_transactions([self.row(owner="Yx", ownerSource="exact-id")])
        self.stage_rebuild(owner="Nic", ownerSource="merchant-rule")
        result = serve.save_owner(self.TX_ID, "Untagged")
        self.assertTrue(result["ok"])
        self.assertEqual(self.read("OWNER_PATH")["tagsById"], {})

    def test_unassign_rolls_back_when_the_tag_survives_the_rebuild(self):
        self.paths["OWNER_PATH"].write_text(
            json.dumps({"tagsById": {self.TX_ID: "Yx"}}, indent=1), encoding="utf-8")
        self.originals["OWNER_PATH"] = self.paths["OWNER_PATH"].read_bytes()
        self.write_transactions([self.row(owner="Yx", ownerSource="exact-id")])
        self.stage_rebuild(owner="Yx", ownerSource="exact-id")
        with self.assertRaisesRegex(RuntimeError, "did not apply"):
            serve.save_owner(self.TX_ID, "Untagged")
        self.assert_untouched("OWNER_PATH", "AUDIT_PATH")

    def test_batch_rolls_back_when_any_row_comes_back_untagged(self):
        self.write_transactions([
            self.row(),
            self.row(id=self.SECOND_ID, owner="Untagged", ownerSource="unassigned"),
        ])
        # The rebuild applies the tag to only one of the two rows.
        self.stage_rebuild_rows([
            self.row(owner="Shared", ownerSource="exact-id"),
            self.row(id=self.SECOND_ID, owner="Untagged", ownerSource="unassigned"),
        ])
        with self.assertRaisesRegex(RuntimeError, "did not apply"):
            serve.save_owner([self.TX_ID, self.SECOND_ID], "Shared")
        self.assert_untouched("OWNER_PATH", "AUDIT_PATH")

    def test_category_only_detail_save_does_not_pin_an_owner_tag(self):
        # An explicit "Untagged" tag outranks manual/owner_rules.json forever,
        # so a save that never touched the owner select must not write one.
        self.write_transactions(
            [self.row(owner="Untagged", ownerSource="unassigned")])
        self.stage_rebuild(
            owner="Untagged", ownerSource="unassigned", category="Games")
        serve.save_transaction_detail(self.TX_ID, "Untagged", "Games", "", "")
        self.assertEqual(self.read("OWNER_PATH")["tagsById"], {})
        self.assertEqual(
            self.read("OVERRIDE_PATH")["overridesById"],
            {self.TX_ID: {"category": "Games"}},
        )

    def test_saving_the_winning_category_confirms_an_overlap(self):
        overlap = self.row(
            ruleCategory="Shopping",
            ruleCategories=["Shopping", "Travel"],
        )
        self.write_transactions([overlap])
        self.stage_rebuild(
            ruleCategory="Shopping",
            ruleCategories=["Shopping", "Travel"],
        )
        serve.save_transaction_detail(
            self.TX_ID, "Nic", "Shopping", "", ""
        )
        self.assertEqual(
            self.read("OVERRIDE_PATH")["overridesById"],
            {self.TX_ID: {"category": "Shopping"}},
        )
        changes = self.read("AUDIT_PATH")["entries"][0]["changes"]
        confirmation = next(
            change for change in changes if change["field"] == "Category review"
        )
        self.assertEqual(confirmation["after"], "Shopping")

    def test_detail_save_still_writes_the_tag_when_the_owner_changes(self):
        self.stage_rebuild(owner="Yx", ownerSource="exact-id")
        serve.save_transaction_detail(self.TX_ID, "Yx", "Shopping", "", "")
        self.assertEqual(self.read("OWNER_PATH")["tagsById"], {self.TX_ID: "Yx"})

    def test_detail_save_unassigning_drops_the_tag_like_the_chip(self):
        # "Unassigned" means one thing everywhere: drop my stable-ID tag. The
        # merchant rule then names Nic again, and that counts as applied.
        self.paths["OWNER_PATH"].write_text(
            json.dumps({"tagsById": {self.TX_ID: "Yx"}}, indent=1), encoding="utf-8")
        self.write_transactions([self.row(owner="Yx", ownerSource="exact-id")])
        self.stage_rebuild(owner="Nic", ownerSource="merchant-rule")
        result = serve.save_transaction_detail(
            self.TX_ID, "Untagged", "Shopping", "", "")
        self.assertTrue(result["ok"])
        self.assertEqual(result["transaction"]["owner"], "Nic")
        self.assertEqual(result["transaction"]["ownerSource"], "merchant-rule")
        self.assertEqual(self.read("OWNER_PATH")["tagsById"], {})

    def test_detail_save_unassigning_is_not_audited_as_confirmed(self):
        self.write_transactions([self.row(owner="Nic", ownerSource="merchant-rule")])
        self.stage_rebuild(owner="Nic", ownerSource="merchant-rule")
        serve.save_transaction_detail(self.TX_ID, "Untagged", "Shopping", "", "")
        changes = self.read("AUDIT_PATH")["entries"][0]["changes"]
        source = next(c for c in changes if c["field"] == "Owner source")
        self.assertEqual(source["after"], "merchant fallback")

    def test_detail_save_unassign_rolls_back_when_the_tag_survives(self):
        self.paths["OWNER_PATH"].write_text(
            json.dumps({"tagsById": {self.TX_ID: "Yx"}}, indent=1), encoding="utf-8")
        self.originals["OWNER_PATH"] = self.paths["OWNER_PATH"].read_bytes()
        self.write_transactions([self.row(owner="Yx", ownerSource="exact-id")])
        self.stage_rebuild(owner="Yx", ownerSource="exact-id")
        with self.assertRaisesRegex(RuntimeError, "did not apply"):
            serve.save_transaction_detail(self.TX_ID, "Untagged", "Shopping", "", "")
        self.assert_untouched("OWNER_PATH", "AUDIT_PATH")

    def test_failed_validation_rolls_back_every_file_and_rebuilds_prior_state(self):
        self.stage_rebuild(owner="Shared", ownerSource="exact-id")
        serve.VALIDATE_SCRIPT = self.scripts["fail"]
        with self.assertRaisesRegex(RuntimeError, "validation exploded"):
            serve.save_owner(self.TX_ID, "Shared")
        self.assert_untouched("OWNER_PATH", "AUDIT_PATH")
        # First build applied the edit, second build republished the restored
        # state - without it app/data keeps serving the rejected edit.
        self.assertEqual(self.script_runs(), ["build", "build"])

    def test_unapplied_rebuild_rolls_back(self):
        # The build succeeds but does not carry the new owner into the
        # dashboard (e.g. a build bug): the save must not stand.
        self.stage_rebuild(owner="Nic")
        with self.assertRaisesRegex(RuntimeError, "did not apply"):
            serve.save_owner(self.TX_ID, "Shared")
        self.assert_untouched("OWNER_PATH", "AUDIT_PATH")

    def test_transaction_detail_failure_restores_all_four_files(self):
        self.stage_rebuild()
        serve.VALIDATE_SCRIPT = self.scripts["fail"]
        with self.assertRaises(RuntimeError):
            serve.save_transaction_detail(
                self.TX_ID, "Yx", "Games", "Renamed", "A remark")
        self.assert_untouched(
            "OWNER_PATH", "REMARK_PATH", "OVERRIDE_PATH", "AUDIT_PATH")

    def test_backups_land_in_the_backups_folder_under_one_timestamp(self):
        self.stage_rebuild(owner="Shared", ownerSource="exact-id")
        serve.save_owner(self.TX_ID, "Shared")
        stamps = set()
        for name in ("OWNER_PATH", "AUDIT_PATH"):
            generations = self.backups(name)
            self.assertEqual(len(generations), 1)
            backup = generations[0]
            self.assertEqual(backup.parent, self.paths[name].parent / "backups")
            # <name>.<YYYYmmdd_HHMMSS_ffffff>.bak
            self.assertTrue(backup.name.startswith(self.paths[name].name + "."))
            stamps.add(backup.name.split(".json.", 1)[1][:-len(".bak")])
            # The single overwritten <file>.bak is not written any more.
            self.assertFalse(
                self.paths[name].with_name(
                    self.paths[name].name + ".bak").exists())
        # One save is one identifiable generation across every file it touched.
        self.assertEqual(len(stamps), 1)
        stamp = stamps.pop()
        self.assertRegex(stamp, r"^\d{8}_\d{6}_\d{6}$")

    def test_two_consecutive_saves_leave_two_generations(self):
        # The old single .bak meant a save after a bad save destroyed the only
        # copy of the good state.
        self.stage_rebuild(owner="Shared", ownerSource="exact-id")
        serve.save_owner(self.TX_ID, "Shared")
        after_first = self.paths["OWNER_PATH"].read_bytes()
        self.write_transactions([self.row(owner="Shared", ownerSource="exact-id")])
        self.stage_rebuild(owner="Yx", ownerSource="exact-id")
        serve.save_owner(self.TX_ID, "Yx")

        generations = self.backups("OWNER_PATH")
        self.assertEqual(len(generations), 2)
        # Oldest first: the pre-first-save file, then the state the first save
        # left behind - the one a bad second save has to be undone to.
        self.assertEqual(generations[0].read_bytes(), self.originals["OWNER_PATH"])
        self.assertEqual(generations[1].read_bytes(), after_first)
        self.assertEqual(len(self.backups("AUDIT_PATH")), 2)

    def test_backup_cap_prunes_the_oldest_generations(self):
        path = self.paths["OWNER_PATH"]
        self.assertEqual(serve.BACKUP_GENERATIONS, 30)
        for index in range(5):
            path.write_text(
                json.dumps({"tagsById": {}, "generation": index}),
                encoding="utf-8")
            serve.snapshot_backups((path,), keep=3)
        generations = serve.backup_generations(path)
        self.assertEqual(len(generations), 3)
        self.assertEqual(
            [json.loads(copy.read_text(encoding="utf-8"))["generation"]
             for copy in generations],
            [2, 3, 4],
        )

    def test_all_backups_snapshot_before_any_write(self):
        # A write that dies on the LAST file must still find every backup at
        # the original generation; the old interleaved order left the audit
        # backup unwritten in this scenario.
        self.stage_rebuild(owner="Shared", ownerSource="exact-id")
        real_write = serve.atomic_write_json

        def failing_write(path, payload):
            if path == serve.AUDIT_PATH:
                raise RuntimeError("disk full")
            return real_write(path, payload)

        serve.atomic_write_json = failing_write
        self.addCleanup(setattr, serve, "atomic_write_json", real_write)
        with self.assertRaisesRegex(RuntimeError, "disk full"):
            serve.save_owner(self.TX_ID, "Shared")
        for name in ("OWNER_PATH", "AUDIT_PATH"):
            self.assert_backup_matches_original(name)
        self.assert_untouched("OWNER_PATH", "AUDIT_PATH")

    def test_account_review_batch_writes_once_and_audits_once(self):
        # "Review all N" used to post N requests, each running the validator
        # and writing its own history row.
        result = serve.save_account_review(
            [self.SECOND_BANK_ID, self.BANK_ID], True,
            {self.BANK_ID: ["large-transfer"],
             self.SECOND_BANK_ID: ["possible-duplicate"]})
        self.assertTrue(result["ok"])
        self.assertEqual(result["ids"], [self.BANK_ID, self.SECOND_BANK_ID])
        self.assertEqual(result["id"], self.BANK_ID)
        self.assertTrue(result["reviewed"])
        self.assertEqual(
            [entry["id"] for entry in
             self.read("ACCOUNT_REVIEW_PATH")["recognizedSignals"]],
            [self.BANK_ID, self.SECOND_BANK_ID],
        )
        entries = self.read("AUDIT_PATH")["entries"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["action"], "Reviewed 2 bank transactions")
        self.assertEqual(
            entries[0]["transactionIds"], [self.BANK_ID, self.SECOND_BANK_ID])
        self.assertEqual(entries[0]["changes"], [{
            "field": "Bank review",
            "before": "Needs review",
            "after": "Reviewed",
        }])
        # One validation run for the whole batch, not one per row.
        self.assertEqual(self.script_runs(), ["validate"])

    def test_account_review_batch_records_a_mixed_before_state(self):
        self.paths["ACCOUNT_REVIEW_PATH"].write_text(
            json.dumps({"recognizedSignals": [{
                "id": self.BANK_ID, "checks": ["large-transfer"]
            }]}), encoding="utf-8")
        serve.save_account_review(
            [self.BANK_ID, self.SECOND_BANK_ID], True,
            {self.BANK_ID: ["large-transfer"],
             self.SECOND_BANK_ID: ["possible-duplicate"]})
        change = self.read("AUDIT_PATH")["entries"][0]["changes"][0]
        self.assertEqual(change["before"], "Reviewed, Needs review")
        self.assertEqual(change["after"], "Reviewed")

    def test_account_review_batch_reopening_clears_every_row(self):
        self.paths["ACCOUNT_REVIEW_PATH"].write_text(
            json.dumps({"recognizedSignals": [
                {"id": self.BANK_ID, "checks": ["large-transfer"]},
                {"id": self.SECOND_BANK_ID, "checks": ["possible-duplicate"]},
            ]}),
            encoding="utf-8")
        serve.save_account_review([self.BANK_ID, self.SECOND_BANK_ID], False)
        self.assertEqual(
            self.read("ACCOUNT_REVIEW_PATH")["recognizedSignals"], [])
        entries = self.read("AUDIT_PATH")["entries"]
        self.assertEqual(entries[0]["action"], "Reopened 2 bank transactions")

    def test_single_account_review_is_unchanged(self):
        result = serve.save_account_review(
            self.BANK_ID, True, {self.BANK_ID: ["large-transfer"]})
        self.assertEqual(result["id"], self.BANK_ID)
        self.assertTrue(result["reviewed"])
        # The single form gained "ids" and nothing else.
        self.assertEqual(result["ids"], [self.BANK_ID])
        self.assertEqual(
            [entry["id"] for entry in
             self.read("ACCOUNT_REVIEW_PATH")["recognizedSignals"]],
            [self.BANK_ID])
        entries = self.read("AUDIT_PATH")["entries"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["action"], "Reviewed bank transaction")
        self.assertEqual(entries[0]["transactionId"], self.BANK_ID)

    def test_account_review_batch_with_an_unknown_id_writes_nothing(self):
        with self.assertRaisesRegex(ValueError, "not present"):
            serve.save_account_review([self.BANK_ID, "tx_bank_missing"], True)
        self.assert_untouched("ACCOUNT_REVIEW_PATH", "AUDIT_PATH")

    def test_account_review_batch_over_the_limit_writes_nothing(self):
        oversized = ["tx_%06d" % n
                     for n in range(serve.MAX_ACCOUNT_REVIEW_BATCH + 1)]
        with self.assertRaisesRegex(ValueError, "Between 1 and"):
            serve.save_account_review(oversized, True)
        self.assert_untouched("ACCOUNT_REVIEW_PATH", "AUDIT_PATH")

    def test_account_review_backs_up_before_writing(self):
        serve.save_account_review(
            self.BANK_ID, True, {self.BANK_ID: ["large-transfer"]})
        for name in ("ACCOUNT_REVIEW_PATH", "AUDIT_PATH"):
            self.assert_backup_matches_original(name)

    def test_restore_reports_unrestorable_files_and_restores_the_rest(self):
        good = self.dir / "restorable.json"
        good.write_text("{}", encoding="utf-8")
        bad = self.dir / "gone" / "unrestorable.json"  # parent never exists
        originals = {good: b'{"was": "here"}\n', bad: b"{}"}
        good.write_text('{"clobbered": true}', encoding="utf-8")
        failed = serve.restore_originals((good, bad), originals)
        self.assertEqual(failed, ["unrestorable.json"])
        self.assertEqual(good.read_bytes(), b'{"was": "here"}\n')

    def test_save_reports_unrestored_files_with_root_cause(self):
        self.stage_rebuild(owner="Shared", ownerSource="exact-id")
        serve.VALIDATE_SCRIPT = self.scripts["fail"]
        real_restore = serve.restore_originals

        def failing_restore(paths, originals):
            return ["owner_tags.json"]

        serve.restore_originals = failing_restore
        self.addCleanup(setattr, serve, "restore_originals", real_restore)
        with self.assertRaisesRegex(
                RuntimeError, "validation exploded.*owner_tags.json"):
            serve.save_owner(self.TX_ID, "Shared")


class PostEndpointTests(unittest.TestCase):
    """POST routing through a real server: origin gate, size cap, save wiring."""

    @classmethod
    def setUpClass(cls):
        cls.directory = Path(tempfile.mkdtemp(prefix="serve-post-"))
        directory = str(cls.directory)

        class TestHandler(serve.FinanceHandler):
            def __init__(self, request, client_address, server):
                super(serve.FinanceHandler, self).__init__(
                    request, client_address, server, directory=directory
                )

            def log_message(self, *args):
                pass

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), TestHandler)
        cls.lifecycle_calls = []

        class LifecycleRecorder:
            def touch(self, client_id):
                cls.lifecycle_calls.append(("touch", client_id))
                return 1

            def disconnect(self, client_id):
                cls.lifecycle_calls.append(("disconnect", client_id))
                return 0

        cls.server.auto_stop = True
        cls.server.dashboard_lifecycle = LifecycleRecorder()
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(timeout=5)
        cls.server.server_close()
        shutil.rmtree(cls.directory, ignore_errors=True)

    def post(self, path, body, host=None, origin=_DEFAULT,
             content_type="application/json", sec_fetch_site=None):
        """POST as the dashboard page does unless a test says otherwise."""
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            headers = {"Host": host or "127.0.0.1:%d" % self.port}
            if origin is _DEFAULT:
                origin = "http://127.0.0.1:%d" % self.port
            if origin:
                headers["Origin"] = origin
            if content_type:
                headers["Content-Type"] = content_type
            if sec_fetch_site:
                headers["Sec-Fetch-Site"] = sec_fetch_site
            connection.request("POST", path, body=body, headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read() or b"{}")
        finally:
            connection.close()

    def test_cross_origin_post_is_rejected_before_routing(self):
        # Both a real endpoint and an unknown one must answer identically to a
        # foreign origin, or the 403/404 split maps the API surface.
        for path in ("/api/owner", "/api/definitely-not-real"):
            with self.subTest(path=path):
                status, body = self.post(
                    path, '{"id": "tx_x"}', origin="https://evil.com")
                self.assertEqual(status, 403)
                self.assertIn("Local requests only", body["error"])

    def test_post_from_another_local_port_is_rejected(self):
        # The hole: a page on some other localhost dev server used to get in.
        for path in ("/api/owner", "/api/definitely-not-real"):
            with self.subTest(path=path):
                status, body = self.post(
                    path, '{"id": "tx_x"}',
                    origin="http://localhost:%d" % (self.port + 1))
                self.assertEqual(status, 403)
                self.assertIn("Local requests only", body["error"])

    def test_post_with_no_origin_and_no_sec_fetch_site_is_rejected(self):
        status, body = self.post("/api/owner", '{"id": "tx_x"}', origin=None)
        self.assertEqual(status, 403)
        self.assertIn("Local requests only", body["error"])

    def test_post_with_same_origin_sec_fetch_site_is_accepted(self):
        # A browser that sends no Origin still attests the request is ours.
        status, body = self.post(
            "/api/owner", json.dumps({"id": "tx_nope", "owner": "Nic"}),
            origin=None, sec_fetch_site="same-origin")
        self.assertEqual(status, 400)
        self.assertIn("not present", body["error"])

    def test_simple_request_content_type_is_rejected(self):
        # text/plain is the type a cross-origin "simple request" would use to
        # dodge the preflight, so it never reaches a handler.
        for content_type in ("text/plain", "text/plain;charset=UTF-8",
                             "application/x-www-form-urlencoded",
                             "multipart/form-data"):
            with self.subTest(content_type=content_type):
                status, body = self.post(
                    "/api/owner", json.dumps({"id": "tx_nope", "owner": "Nic"}),
                    content_type=content_type)
                self.assertEqual(status, 400)
                self.assertIn("Content-Type", body["error"])

    def test_missing_content_type_is_rejected(self):
        status, body = self.post(
            "/api/owner", '{"a": 1}', content_type=None)
        self.assertEqual(status, 400)
        self.assertIn("Content-Type", body["error"])

    def test_json_content_type_parameters_are_accepted(self):
        status, body = self.post(
            "/api/owner", json.dumps({"id": "tx_nope", "owner": "Nic"}),
            content_type="application/json; charset=utf-8")
        self.assertEqual(status, 400)
        self.assertIn("not present", body["error"])

    def test_post_to_host_naming_another_port_is_rejected(self):
        status, body = self.post(
            "/api/owner", '{"a": 1}',
            host="127.0.0.1:%d" % (self.port + 1), origin=None,
            sec_fetch_site="same-origin")
        self.assertEqual(status, 403)
        self.assertIn("Local requests only", body["error"])

    def test_null_origin_post_with_same_origin_attestation_reaches_the_handler(self):
        status, body = self.post("/api/owner", '{"id": "tx_x"}', origin="null",
                                 sec_fetch_site="same-origin")
        # Past the gate: the handler itself rejects the malformed payload.
        self.assertEqual(status, 400)
        self.assertNotIn("Local requests only", body.get("error", ""))

    def test_referrer_policy_is_same_origin(self):
        # "no-referrer" would make browsers send "Origin: null" on the
        # dashboard's own POSTs; "same-origin" keeps the real Origin while
        # still withholding the URL from third parties.
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request("GET", "/api/status",
                               headers={"Host": "127.0.0.1:%d" % self.port})
            response = connection.getresponse()
            response.read()
            self.assertEqual(response.getheader("Referrer-Policy"), "same-origin")
        finally:
            connection.close()

    def test_null_origin_post_is_rejected(self):
        status, body = self.post("/api/owner", '{"a": 1}', origin="null")
        self.assertEqual(status, 403)
        self.assertIn("Local requests only", body["error"])

    def test_unknown_endpoint_is_404_for_local_callers(self):
        status, body = self.post("/api/definitely-not-real", '{"a": 1}')
        self.assertEqual(status, 404)

    def test_oversized_body_is_rejected(self):
        status, body = self.post("/api/owner", '{"pad": "%s"}' % ("x" * 4200))
        self.assertEqual(status, 400)
        self.assertIn("size", body["error"].lower())

    def test_invalid_owner_payload_is_a_clean_400(self):
        # Routes through the real TRANSACTIONS_PATH read; the tx id cannot
        # exist, so this must come back as a validation error, not a 500.
        status, body = self.post(
            "/api/owner", json.dumps({"id": "tx_nope", "owner": "Nic"}))
        self.assertEqual(status, 400)
        self.assertIn("not present", body["error"])

    def test_account_review_batch_reaches_the_save_path(self):
        # Routes through the real account_transactions.json read; the IDs
        # cannot exist, so an "ids" body must come back as a validation error
        # rather than as the single form's "a valid ID is required".
        status, body = self.post("/api/account-review", json.dumps(
            {"ids": ["tx_nope0001", "tx_nope0002"], "reviewed": True}))
        self.assertEqual(status, 400)
        self.assertIn("not present", body["error"])

    def test_oversized_account_review_batch_is_rejected(self):
        oversized = ["tx_%06d" % n
                     for n in range(serve.MAX_ACCOUNT_REVIEW_BATCH + 1)]
        status, body = self.post("/api/account-review", json.dumps(
            {"ids": oversized, "reviewed": True}))
        self.assertEqual(status, 400)
        self.assertIn("Between 1 and 100", body["error"])

    def test_status_advertises_the_account_review_batch_cap(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request("GET", "/api/status",
                               headers={"Host": "127.0.0.1:%d" % self.port})
            response = connection.getresponse()
            status = json.loads(response.read())
            self.assertEqual(status["accountReviewBatch"], 100)
            self.assertTrue(status["accountReviews"])
        finally:
            connection.close()

    def test_browser_lifecycle_heartbeat_and_disconnect(self):
        client_id = "3ecadfb6-1d6b-4f7a-8b5c-2a4373ec2e13"
        status, body = self.post(
            "/api/client-heartbeat", json.dumps({"clientId": client_id}))
        self.assertEqual(status, 200)
        self.assertEqual(body["activeClients"], 1)
        status, body = self.post(
            "/api/client-disconnect", json.dumps({"clientId": client_id}))
        self.assertEqual(status, 200)
        self.assertEqual(body["activeClients"], 0)
        self.assertEqual(self.lifecycle_calls[-2:], [
            ("touch", client_id), ("disconnect", client_id)
        ])

    def test_browser_lifecycle_rejects_invalid_client_id(self):
        status, body = self.post(
            "/api/client-heartbeat", json.dumps({"clientId": "not-a-uuid"}))
        self.assertEqual(status, 400)
        self.assertIn("UUID", body["error"])


class ManualFileBootstrapTests(unittest.TestCase):
    """A fresh clone has no manual/ at all: Git ignores the whole folder.

    Every save reads the file it is about to update, and two GET endpoints read
    theirs, so a missing file used to surface as a 500 with a traceback rather
    than as an empty dashboard.
    """

    EXPECTED = {
        "OWNER_PATH": ("owner_tags.json", {"tags": {}, "tagsById": {}}),
        "RISK_REVIEW_PATH": ("risk_reviews.json", {"recognizedSignals": []}),
        "ACCOUNT_REVIEW_PATH": ("account_reviews.json", {"recognizedSignals": []}),
        "REMARK_PATH": ("transaction_remarks.json", {"remarksById": {}}),
        "OVERRIDE_PATH": ("transaction_overrides.json", {"overridesById": {}}),
        "AUDIT_PATH": ("audit_history.json", {"entries": []}),
    }

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="serve-bootstrap-"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        # The tree is empty: not even manual/ exists yet.
        self.manual = self.dir / "manual"
        for attribute, (filename, _) in self.EXPECTED.items():
            original = getattr(serve, attribute)
            setattr(serve, attribute, self.manual / filename)
            self.addCleanup(setattr, serve, attribute, original)

    def contents(self, filename):
        return json.loads((self.manual / filename).read_text(encoding="utf-8"))

    def test_creates_exactly_the_six_server_owned_files(self):
        created = serve.ensure_manual_files()
        expected = sorted(name for name, _ in self.EXPECTED.values())
        self.assertEqual(sorted(path.name for path in created), expected)
        # Nothing else: identity, salary, settlements, game sales, owner rules
        # and the legacy ledger are user content, and an empty stand-in would
        # make a file the user has yet to write look answered.
        self.assertEqual(
            sorted(path.name for path in self.manual.iterdir()), expected)
        for filename, default in self.EXPECTED.values():
            self.assertEqual(self.contents(filename), default)

    def test_is_idempotent_and_leaves_existing_content_alone(self):
        serve.ensure_manual_files()
        self.manual.joinpath("audit_history.json").write_text(
            json.dumps({"entries": [{"id": "audit_kept"}]}), encoding="utf-8")
        self.assertEqual(serve.ensure_manual_files(), [])
        self.assertEqual(
            self.contents("audit_history.json"),
            {"entries": [{"id": "audit_kept"}]},
        )

    def test_recreates_only_the_file_that_went_missing(self):
        serve.ensure_manual_files()
        self.manual.joinpath("owner_tags.json").unlink()
        created = serve.ensure_manual_files()
        self.assertEqual([path.name for path in created], ["owner_tags.json"])

    def test_the_seeded_files_satisfy_the_reads_the_save_paths_do(self):
        serve.ensure_manual_files()
        # What each save function pulls out of its file before writing.
        self.assertEqual(self.contents("owner_tags.json")["tagsById"], {})
        self.assertEqual(self.contents("transaction_remarks.json")["remarksById"], {})
        self.assertEqual(
            self.contents("transaction_overrides.json")["overridesById"], {})
        self.assertEqual(self.contents("risk_reviews.json")["recognizedSignals"], [])
        self.assertEqual(
            self.contents("account_reviews.json")["recognizedSignals"], [])
        self.assertEqual(self.contents("audit_history.json")["entries"], [])


class RequestLogTests(unittest.TestCase):
    """The launcher redirects this server's stderr into a log file.

    An open tab polls two endpoints every five seconds, which was roughly
    17,000 access lines a day per tab and buried everything worth reading.
    """

    @classmethod
    def setUpClass(cls):
        cls.directory = Path(tempfile.mkdtemp(prefix="serve-log-"))
        directory = str(cls.directory)

        # Deliberately no log_message override: this is the real logging path.
        class LoggingHandler(serve.FinanceHandler):
            def __init__(self, request, client_address, server):
                super(serve.FinanceHandler, self).__init__(
                    request, client_address, server, directory=directory
                )

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), LoggingHandler)
        cls.server.auto_stop = False
        cls.server.dashboard_lifecycle = None
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(timeout=5)
        cls.server.server_close()
        shutil.rmtree(cls.directory, ignore_errors=True)

    def request(self, method, path, body=None):
        """Send one request and return (status, captured stderr).

        log_request runs inside send_response, before the response reaches the
        client, so the log line is already written when this returns.
        """
        captured = io.StringIO()
        real_stderr = sys.stderr
        sys.stderr = captured
        try:
            connection = http.client.HTTPConnection(
                "127.0.0.1", self.port, timeout=5)
            try:
                headers = {
                    "Host": "127.0.0.1:%d" % self.port,
                    "Origin": "http://127.0.0.1:%d" % self.port,
                }
                if body is not None:
                    headers["Content-Type"] = "application/json"
                connection.request(method, path, body=body, headers=headers)
                response = connection.getresponse()
                response.read()
                return response.status, captured.getvalue()
            finally:
                connection.close()
        finally:
            sys.stderr = real_stderr

    def heartbeat_body(self):
        return json.dumps({"clientId": "3ecadfb6-1d6b-4f7a-8b5c-2a4373ec2e13"})

    def test_successful_poll_endpoints_are_not_logged(self):
        for path in ("/api/client-heartbeat", "/api/client-disconnect"):
            with self.subTest(path=path):
                status, logged = self.request("POST", path, self.heartbeat_body())
                self.assertEqual(status, 200)
                self.assertEqual(logged, "")

    def test_a_failing_poll_request_is_still_logged(self):
        # Only the quiet 200 is dropped; a rejected heartbeat is a fault.
        status, logged = self.request(
            "POST", "/api/client-heartbeat", json.dumps({"clientId": "nope"}))
        self.assertEqual(status, 400)
        self.assertIn("/api/client-heartbeat", logged)
        self.assertIn("400", logged)

    def test_other_endpoints_are_logged_as_before(self):
        status, logged = self.request("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertIn("/api/status", logged)
        self.assertIn("200", logged)

    def test_a_heartbeat_with_a_query_string_is_still_quiet(self):
        status, logged = self.request(
            "POST", "/api/client-heartbeat?tab=2", self.heartbeat_body())
        self.assertEqual(status, 200)
        self.assertEqual(logged, "")


class DashboardLifecycleTests(unittest.TestCase):
    def test_disconnect_makes_launcher_server_eligible_to_stop(self):
        lifecycle = serve.DashboardLifecycle(
            object(), stale_after=60, empty_grace=0, startup_grace=60)
        client_id = "3ecadfb6-1d6b-4f7a-8b5c-2a4373ec2e13"
        self.assertEqual(lifecycle.touch(client_id), 1)
        self.assertEqual(lifecycle.disconnect(client_id), 0)
        self.assertTrue(lifecycle._should_shutdown())

    def test_the_empty_grace_outlasts_a_page_reload(self):
        # A reload leaves the server with no clients between the old tab's
        # unload and the new page's first heartbeat. Three seconds of grace
        # could stop the server the user was reloading.
        lifecycle = serve.DashboardLifecycle(object())
        self.assertEqual(lifecycle.empty_grace, 15)
        client_id = "3ecadfb6-1d6b-4f7a-8b5c-2a4373ec2e13"
        lifecycle.touch(client_id)
        lifecycle.disconnect(client_id)
        self.assertFalse(lifecycle._should_shutdown())


if __name__ == "__main__":
    unittest.main()
