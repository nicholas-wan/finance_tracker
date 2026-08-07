"""Transaction IDs must survive re-extraction, and orphaned manual data must shout.

IDs used to hash the statement's file name and the page/line each row was pulled
from. Renaming a PDF, or a pypdf release that shifted the text layer by a line,
therefore re-minted every ID in that file - detaching (or worse, transplanting)
the owner tags, category overrides, remarks and recognized risk reviews that are
keyed by them. These tests pin both halves of the fix: the ID no longer depends
on where the row was found, and validate_data.py now fails on a manual record
that points at nothing.
"""

import contextlib
import importlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(SCRIPTS))

risk_checks = importlib.import_module("risk_checks")
parse_cc = importlib.import_module("parse_cc")
parse_one = importlib.import_module("parse_one")
validate_data = importlib.import_module("validate_data")


class FakePage:
    def __init__(self, text):
        self.text = text

    def extract_text(self):
        return self.text


class FakeReader:
    def __init__(self, text):
        self.pages = [FakePage(text)]


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def parse_card(text, filename):
    with patch.object(parse_cc, "PdfReader", return_value=FakeReader(text)):
        return parse_cc.parse_pdf(filename)


def parse_account(text, filename):
    with patch.object(parse_one, "PdfReader", return_value=FakeReader(text)):
        # The trailing element is the printed BALANCE B/F anchor; these tests
        # are about IDs, so they keep the original four-part shape.
        return parse_one.parse_pdf(filename)[:4]


class SourceIndependentIdTests(unittest.TestCase):
    def test_card_ids_survive_a_renamed_statement(self):
        _, original, _, failures = parse_card(
            fixture("card_statement_redacted.txt"), "UOB_CC_2026_06.pdf")
        _, renamed, _, _ = parse_card(
            fixture("card_statement_redacted.txt"), "UOB_CC_JUN_26 (copy).pdf")
        self.assertEqual(failures, [])
        self.assertEqual([row["id"] for row in original], [row["id"] for row in renamed])
        # Only the provenance changed: it still records where the row was read.
        self.assertEqual(original[0]["provenance"]["sourceFile"], "UOB_CC_2026_06.pdf")
        self.assertEqual(renamed[0]["provenance"]["sourceFile"], "UOB_CC_JUN_26 (copy).pdf")

    def test_account_ids_survive_a_renamed_statement(self):
        _, original, _, failures = parse_account(
            fixture("account_statement_redacted.txt"), "UOB_ONE_2026_06.pdf")
        _, renamed, _, _ = parse_account(
            fixture("account_statement_redacted.txt"), "UOB_ONE_JUN_26.pdf")
        self.assertEqual(failures, [])
        self.assertEqual([row["id"] for row in original], [row["id"] for row in renamed])

    def test_card_ids_survive_every_line_moving_down_one(self):
        # A pypdf upgrade that emits one extra leading line is enough; the old
        # scheme hashed the line number, so all four IDs changed.
        _, original, _, _ = parse_card(
            fixture("card_statement_redacted.txt"), "UOB_CC_2026_06.pdf")
        _, shifted, _, failures = parse_card(
            "\n" + fixture("card_statement_redacted.txt"), "UOB_CC_2026_06.pdf")
        self.assertEqual(failures, [])
        self.assertEqual([row["id"] for row in original], [row["id"] for row in shifted])
        self.assertEqual(
            [row["provenance"]["line"] + 1 for row in original],
            [row["provenance"]["line"] for row in shifted],
            "the fixture must actually shift every line, or this proves nothing",
        )

    def test_account_ids_survive_every_line_moving_down_one(self):
        _, original, _, _ = parse_account(
            fixture("account_statement_redacted.txt"), "UOB_ONE_2026_06.pdf")
        _, shifted, _, failures = parse_account(
            "\n" + fixture("account_statement_redacted.txt"), "UOB_ONE_2026_06.pdf")
        self.assertEqual(failures, [])
        self.assertEqual([row["id"] for row in original], [row["id"] for row in shifted])
        self.assertEqual(
            [row["provenance"]["line"] + 1 for row in original],
            [row["provenance"]["line"] for row in shifted],
        )

    def test_identical_same_day_rows_stay_distinct_and_deterministic(self):
        # The fixture books the same S$6.00 charge twice on 10 JUN. Both are real
        # (three same-day ActiveSG bookings happen), so they must not collapse.
        _, rows, _, _ = parse_card(
            fixture("card_statement_redacted.txt"), "UOB_CC_2026_06.pdf")
        first, second = rows[0], rows[1]
        self.assertEqual(first["description"], second["description"])
        self.assertEqual(first["date"], second["date"])
        self.assertEqual(first["amount"], second["amount"])
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(
            [first["provenance"]["occurrence"], second["provenance"]["occurrence"]], [1, 2])
        self.assertEqual(len({row["id"] for row in rows}), len(rows))
        _, again, _, _ = parse_card(
            fixture("card_statement_redacted.txt"), "UOB_CC_2026_06.pdf")
        self.assertEqual([row["id"] for row in rows], [row["id"] for row in again])

    def test_id_carries_no_trace_of_file_page_or_line(self):
        _, rows, _, _ = parse_card(
            fixture("card_statement_redacted.txt"), "UOB_CC_2026_06.pdf")
        data_ids = importlib.import_module("data_ids")
        identity = data_ids.content_identity(rows[0], "card-pdf", rows[0]["card"])
        self.assertNotIn("UOB_CC_2026_06.pdf", identity)
        self.assertNotIn(rows[0]["provenance"]["page"], identity)
        self.assertNotIn(rows[0]["provenance"]["line"], identity)


REAL_ID = "tx_synthetic0000000001"


def synthetic_tree(root, overrides=None, remarks=None, recognized=None, audit=None):
    """A one-row dataset that validates cleanly, so only seeded faults show up."""
    data_dir = os.path.join(root, "app", "data")
    manual_dir = os.path.join(root, "manual")
    os.makedirs(data_dir)
    os.makedirs(manual_dir)

    provenance = {
        "sourceType": "card-pdf",
        "sourceFile": "SYNTHETIC.pdf",
        "statementMonth": "2026-06",
        "section": "UOB ONE CARD",
        "occurrence": 1,
        "verified": True,
        "page": 1,
        "line": 4,
    }
    source_row = {
        "date": "2026-06-10",
        "postedDate": "2026-06-12",
        "month": "2026-06",
        "card": "UOB ONE CARD",
        "description": "SYNTHETIC MERCHANT",
        "amount": 10.0,
        "credit": False,
        "id": REAL_ID,
        "provenance": provenance,
    }
    built_row = dict(source_row)
    built_row.update({
        "type": "debit",
        "owner": "Nic",
        "ownerSource": "exact-id",
        "category": "Other",
        "ruleCategory": "Other",
    })
    built_row.pop("credit")

    def write(path, payload):
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=1)

    write(os.path.join(data_dir, "card_transactions.json"), {
        "months": ["2026-06"],
        "sourceFiles": {"2026-06": "SYNTHETIC.pdf"},
        "quality": {
            "statementFiles": 1,
            "pdfMonths": ["2026-06"],
            "csvMonths": [],
            "reconciledSections": 1,
            "uncheckedSections": 0,
            "unreconciledSections": [],
            "unparsedRows": 0,
        },
        "transactions": [source_row],
    })
    write(os.path.join(data_dir, "account_transactions.json"), {
        "months": [],
        "sourceFiles": {},
        "quality": {"amountOverrides": 0},
        "transactions": [],
    })
    write(os.path.join(data_dir, "transactions.json"), {
        "months": ["2026-06"],
        "settlements": {"openingBalances": [], "payments": []},
        "freshness": {
            "latestStatementMonth": "2026-06",
            "latestStatementFile": "SYNTHETIC.pdf",
            "sourceThrough": "2026-06-12",
            "expectedNextStatementMonth": "2026-07",
            "expectedNextStatementDate": "2026-07-12",
            "missingStatementMonths": [],
            "statementCount": 1,
        },
        "quality": {
            "integrity": {
                "duplicateIds": 0,
                "missingProvenance": 0,
                "sourceTransactions": 1,
                "outputTransactions": 1,
            },
            "provenance": {"unverifiedTransactions": 0},
            "review": {},
        },
        "transactions": [built_row],
    })
    write(os.path.join(manual_dir, "legacy_transactions.json"), {"transactions": []})
    write(os.path.join(manual_dir, "owner_tags.json"), {"tags": {}, "tagsById": {REAL_ID: "Nic"}})
    write(os.path.join(manual_dir, "transaction_overrides.json"),
          {"overridesById": overrides or {}})
    write(os.path.join(manual_dir, "transaction_remarks.json"), {"remarksById": remarks or {}})
    write(os.path.join(manual_dir, "risk_reviews.json"),
          {"recognizedSignals": recognized or []})
    write(os.path.join(manual_dir, "audit_history.json"), {"entries": audit or []})
    return data_dir, manual_dir


def run_validate(root):
    data_dir = os.path.join(root, "app", "data")
    manual_dir = os.path.join(root, "manual")
    stdout = io.StringIO()
    with patch.object(validate_data, "DATA_DIR", data_dir), \
            patch.object(validate_data, "MANUAL_DIR", manual_dir), \
            patch.object(sys, "argv", ["validate_data.py"]), \
            contextlib.redirect_stdout(stdout):
        failed = False
        try:
            validate_data.main()
        except SystemExit:
            failed = True
    return failed, stdout.getvalue()


def recognized_signal(ids, checks=("same-day-duplicate",)):
    """A stored acknowledgement, keyed the way the detector keys signals."""
    ids = sorted(ids)
    checks = list(checks)
    return {
        "key": risk_checks.signal_key(ids, checks),
        "ids": ids,
        "checks": checks,
        "recognizedAt": "2026-06-12T00:00:00+08:00",
    }


class OrphanDetectionTests(unittest.TestCase):
    def test_clean_synthetic_tree_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            synthetic_tree(tmp)
            failed, output = run_validate(tmp)
        self.assertFalse(failed, output)
        self.assertNotIn("INTEGRITY ERRORS", output)

    def test_dangling_override_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            synthetic_tree(tmp, overrides={"tx_goneaway000000000001": {"category": "Games"}})
            failed, output = run_validate(tmp)
        self.assertTrue(failed)
        self.assertIn("category override tx_goneaway000000000001 no longer matches", output)

    def test_dangling_remark_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            synthetic_tree(tmp, remarks={"tx_goneaway000000000002": "PC purchase"})
            failed, output = run_validate(tmp)
        self.assertTrue(failed)
        self.assertIn("remark tx_goneaway000000000002 no longer matches", output)

    def test_dangling_recognized_risk_id_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            synthetic_tree(tmp, recognized=[
                recognized_signal(["tx_goneaway000000000003"])])
            failed, output = run_validate(tmp)
        self.assertTrue(failed)
        self.assertIn(
            "names unknown transaction tx_goneaway000000000003", output)

    def test_a_retired_signal_warns_rather_than_failing(self):
        # Tuning a check legitimately stops it firing. The acknowledgement is
        # then dead weight, worth reporting but not worth failing the build -
        # failing would push the user towards deleting their own decisions.
        with tempfile.TemporaryDirectory() as tmp:
            synthetic_tree(tmp, recognized=[recognized_signal([REAL_ID])])
            failed, output = run_validate(tmp)
        self.assertFalse(failed, output)
        self.assertNotIn("INTEGRITY ERRORS", output)
        self.assertIn("no longer matches a check in the current build", output)

    def test_a_tampered_signal_key_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            entry = recognized_signal([REAL_ID])
            entry["checks"] = ["first-observed-high-value"]
            synthetic_tree(tmp, recognized=[entry])
            failed, output = run_validate(tmp)
        self.assertTrue(failed)
        self.assertIn("does not match its own rows and checks", output)

    def test_a_leftover_pre_signal_list_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            synthetic_tree(tmp)
            path = os.path.join(tmp, "manual", "risk_reviews.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"recognizedIds": [REAL_ID]}, handle)
            failed, output = run_validate(tmp)
        self.assertFalse(failed, output)
        self.assertIn("migrate_risk_reviews_to_signals", output)

    def test_all_three_orphan_kinds_are_reported_together(self):
        with tempfile.TemporaryDirectory() as tmp:
            synthetic_tree(
                tmp,
                overrides={"tx_goneaway000000000001": {"category": "Games"}},
                remarks={"tx_goneaway000000000002": "PC purchase"},
                recognized=[recognized_signal(["tx_goneaway000000000003"])],
            )
            failed, output = run_validate(tmp)
        self.assertTrue(failed)
        for fragment in ("category override tx_goneaway000000000001",
                         "remark tx_goneaway000000000002",
                         "names unknown transaction tx_goneaway000000000003"):
            self.assertIn(fragment, output)

    def test_an_override_that_resolves_but_was_not_applied_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            synthetic_tree(tmp, overrides={REAL_ID: {"category": "Games"}})
            failed, output = run_validate(tmp)
        self.assertTrue(failed)
        self.assertIn("category override %s was not applied" % REAL_ID, output)

    def test_audit_history_may_reference_a_transaction_that_no_longer_exists(self):
        # History outlives the rows it describes on purpose; only the shape of
        # the id is checked, never whether it still resolves.
        with tempfile.TemporaryDirectory() as tmp:
            synthetic_tree(tmp, audit=[{
                "id": "audit_synthetic",
                "transactionId": "tx_deleted00000000001",
                "transactionIds": ["tx_deleted00000000001"],
            }])
            failed, output = run_validate(tmp)
        self.assertFalse(failed, output)
        self.assertNotIn("tx_deleted00000000001", output)

    def test_malformed_audit_transaction_id_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            synthetic_tree(tmp, audit=[{"id": "audit_synthetic", "transactionId": 17}])
            failed, output = run_validate(tmp)
        self.assertTrue(failed)
        self.assertIn("malformed transaction id", output)


if __name__ == "__main__":
    unittest.main()
