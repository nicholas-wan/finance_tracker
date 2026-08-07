"""The validator is the only thing guarding data that is already on disk.

The parsers refuse bad statements at import time, but nothing re-reads a PDF
afterwards: once account_transactions.json, card_transactions.json and
transactions.json are written, validate_data.py is the whole defence. An
adversarial pass over copies of the live data found six ways to edit those files
so that every check still passed - a whole statement's balances shifted, the
first row of a statement multiplied, a refund flipped to a charge, a month
re-ingested under a second file name, a real hole in the statement run with an
empty missingMonths, and month/date strings that either crashed the run or sorted
wrongly. Each test below seeds exactly one of those edits into a synthetic tree
and pins the message that now catches it.

Every fixture here is fabricated. No real statement data is used.
"""

import contextlib
import importlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

validate_data = importlib.import_module("validate_data")


def month_key(offset):
    """A month key `offset` months before the current one."""
    today = date.today()
    index = today.year * 12 + (today.month - 1) - offset
    return "%04d-%02d" % (index // 12, index % 12 + 1)


LEGACY_MONTH = month_key(3)
CARD_MONTHS = [month_key(2), month_key(1), month_key(0)]
ACCOUNT_MONTHS = list(CARD_MONTHS)


def account_statement(month, source_file, opening, movements):
    """One statement: an anchor plus rows whose balances chain off `opening`."""
    rows = []
    balance = opening
    for index, (description, amount, direction) in enumerate(movements, 1):
        balance = round(balance + (amount if direction == "deposit" else -amount), 2)
        rows.append({
            "date": "%s-1%d" % (month, index),
            "month": month,
            "description": description,
            "amount": amount,
            "direction": direction,
            "flow": "Transfer",
            "balance": balance,
            "amountSource": "printed",
            "id": "tx_acct%s%d" % (month.replace("-", ""), index),
            "provenance": {
                "sourceType": "account-pdf",
                "sourceFile": source_file,
                "statementMonth": month,
                "section": "UOB ONE",
                "occurrence": 1,
                "verified": True,
                "page": 1,
                "line": 10 + index,
            },
        })
    anchor = {
        "file": source_file,
        "openingBalance": opening,
        "closingBalance": balance,
        "rows": len(rows),
    }
    return rows, anchor, balance


def card_row(month, index, description, amount, credit=False):
    return {
        "date": "%s-1%d" % (month, index),
        "postedDate": "%s-1%d" % (month, index + 1),
        "month": month,
        "card": "UOB ONE CARD",
        "description": description,
        "amount": amount,
        "credit": credit,
        "foreign": None,
        "id": "tx_card%s%d" % (month.replace("-", ""), index),
        "provenance": {
            "sourceType": "card-pdf",
            "sourceFile": "SYNTH_CC_%s.pdf" % month.replace("-", "_"),
            "statementMonth": month,
            "section": "UOB ONE CARD",
            "occurrence": 1,
            "verified": True,
            "page": 1,
            "line": 20 + index,
        },
    }


def built_from(row, source_type="card-pdf"):
    """The dashboard row build_data.py would emit for this source row."""
    built = {
        "id": row["id"],
        "date": row["date"],
        "postedDate": row.get("postedDate"),
        "month": row["month"],
        "card": row.get("card", "UOB ONE CARD"),
        "description": row["description"],
        "amount": row["amount"],
        "type": "refund" if row.get("credit") else "debit",
        "owner": "Nic",
        "ownerSource": "exact-id",
        "category": "Groceries",
        "ruleCategory": "Groceries",
        "provenance": dict(row["provenance"]),
    }
    built["provenance"]["sourceType"] = source_type
    return built


def dataset():
    """A clean three-month tree: account statements, card statements, dashboard."""
    account_rows = []
    anchors = {}
    balance = 1000.00
    movements = [
        [("SYNTH SALARY", 100.0, "deposit"), ("SYNTH RENT", 50.0, "withdrawal")],
        [("SYNTH FEE", 50.0, "withdrawal"), ("SYNTH REFUND", 200.0, "deposit")],
        [("SYNTH BONUS", 300.0, "deposit"), ("SYNTH BILL", 100.0, "withdrawal")],
    ]
    for month, moves in zip(ACCOUNT_MONTHS, movements):
        source_file = "SYNTH_ONE_%s.pdf" % month.replace("-", "_")
        rows, anchor, balance = account_statement(month, source_file, balance, moves)
        account_rows.extend(rows)
        anchors[month] = anchor

    account = {
        "months": list(ACCOUNT_MONTHS),
        "sourceFiles": {month: anchors[month]["file"] for month in ACCOUNT_MONTHS},
        "statementAnchors": anchors,
        "quality": {
            "statementFiles": len(ACCOUNT_MONTHS),
            "missingMonths": [],
            "amountOverrides": 0,
            "unparsedRows": 0,
        },
        "transactions": account_rows,
    }

    card_rows = []
    for month in CARD_MONTHS:
        card_rows.append(card_row(month, 1, "SYNTH GROCER %s" % month, 40.0))
        card_rows.append(card_row(month, 2, "SYNTH RETURN %s" % month, 15.0, credit=True))
    cards = {
        "months": list(CARD_MONTHS),
        "sourceFiles": {
            month: "SYNTH_CC_%s.pdf" % month.replace("-", "_") for month in CARD_MONTHS
        },
        "quality": {
            "statementFiles": len(CARD_MONTHS),
            "pdfMonths": list(CARD_MONTHS),
            "csvMonths": [],
            "reconciledSections": len(CARD_MONTHS),
            "uncheckedSections": 0,
            "unreconciledSections": [],
            "unparsedRows": 0,
        },
        "transactions": card_rows,
    }

    # One month older than any statement, so it is filled from the spreadsheet
    # and reaches the dashboard as a legacy-sourced row.
    legacy_row = {
        "date": "%s-05" % LEGACY_MONTH,
        "month": LEGACY_MONTH,
        "card": "UOB ONE CARD",
        "description": "SYNTH LEGACY MARKET",
        "amount": 22.5,
        "credit": False,
        "foreign": None,
    }
    legacy_built = {
        "id": "tx_legacy000000000001",
        "date": legacy_row["date"],
        "postedDate": None,
        "month": LEGACY_MONTH,
        "card": "UOB ONE CARD",
        "description": legacy_row["description"],
        "amount": legacy_row["amount"],
        "type": "debit",
        "owner": "Nic",
        "ownerSource": "exact-id",
        "category": "Groceries",
        "ruleCategory": "Groceries",
        "provenance": {
            "sourceType": "legacy-manual",
            "sourceFile": "legacy_transactions.json",
            "statementMonth": LEGACY_MONTH,
            "section": "UOB ONE CARD",
            "occurrence": 1,
            "verified": False,
        },
    }

    built = [built_from(row) for row in card_rows] + [legacy_built]
    built.sort(key=lambda row: (row["month"], row["date"], row["description"]))
    latest = CARD_MONTHS[-1]
    output = {
        "currency": "SGD",
        "generatedAt": "2026-01-01 00:00",
        "months": sorted({row["month"] for row in built}),
        "settlements": {"openingBalances": [], "payments": []},
        "freshness": {
            "latestStatementMonth": latest,
            "latestStatementFile": cards["sourceFiles"][latest],
            "sourceThrough": "%s-12" % latest,
            "expectedNextStatementMonth": month_key(-1),
            "expectedNextStatementDate": "%s-12" % month_key(-1),
            "missingStatementMonths": [],
            "statementCount": len(CARD_MONTHS),
        },
        "quality": {
            "integrity": {
                "uniqueIds": True,
                "duplicateIds": 0,
                "missingProvenance": 0,
                "sourceTransactions": len(built),
                "outputTransactions": len(built),
            },
            "provenance": {"unverifiedTransactions": 0},
            "review": {},
        },
        "transactions": built,
    }
    return {"account": account, "cards": cards, "output": output,
            "legacy": {"transactions": [legacy_row]}}


def write_tree(root, data):
    data_dir = os.path.join(root, "app", "data")
    manual_dir = os.path.join(root, "manual")
    os.makedirs(data_dir)
    os.makedirs(manual_dir)

    def write(path, payload):
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=1)

    write(os.path.join(data_dir, "account_transactions.json"), data["account"])
    write(os.path.join(data_dir, "card_transactions.json"), data["cards"])
    write(os.path.join(data_dir, "transactions.json"), data["output"])
    write(os.path.join(manual_dir, "legacy_transactions.json"), data["legacy"])
    write(os.path.join(manual_dir, "owner_tags.json"), {"tags": {}, "tagsById": {}})
    write(os.path.join(manual_dir, "transaction_overrides.json"), {"overridesById": {}})
    write(os.path.join(manual_dir, "transaction_remarks.json"), {"remarksById": {}})
    write(os.path.join(manual_dir, "risk_reviews.json"), {"recognizedSignals": []})
    write(os.path.join(manual_dir, "audit_history.json"), {"entries": []})
    write(os.path.join(manual_dir, "account_reviews.json"), {"reviewedIds": []})
    return data_dir, manual_dir


def run(mutate=None):
    """Build the clean tree, apply one edit, and validate it. Returns (failed, text)."""
    data = dataset()
    if mutate:
        mutate(data)
    with tempfile.TemporaryDirectory() as tmp:
        data_dir, manual_dir = write_tree(tmp, data)
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


def account_rows_for(data, month):
    return [row for row in data["account"]["transactions"] if row["month"] == month]


class BaselineTests(unittest.TestCase):
    def test_the_clean_tree_passes(self):
        failed, output = run()
        self.assertFalse(failed, output)
        self.assertNotIn("INTEGRITY ERRORS", output)


class CrossStatementContinuityTests(unittest.TestCase):
    """Blind spot 1: the chain was only checked inside one (month, file) group."""

    def test_shifting_one_statements_balances_breaks_continuity(self):
        # Every balance in the middle statement moves by the same amount, so the
        # statement still reconciles row by row. Only the join to its neighbours
        # gives it away. The anchor moves with it, as an editor's would.
        month = ACCOUNT_MONTHS[1]

        def mutate(data):
            for row in account_rows_for(data, month):
                row["balance"] = round(row["balance"] + 5000.0, 2)
            anchor = data["account"]["statementAnchors"][month]
            anchor["openingBalance"] = round(anchor["openingBalance"] + 5000.0, 2)
            anchor["closingBalance"] = round(anchor["closingBalance"] + 5000.0, 2)

        failed, output = run(mutate)
        self.assertTrue(failed)
        self.assertIn("account balance does not carry from %s into %s"
                      % (ACCOUNT_MONTHS[0], month), output)

    def test_shifted_balances_without_moving_the_anchor_hit_the_anchor(self):
        month = ACCOUNT_MONTHS[1]

        def mutate(data):
            for row in account_rows_for(data, month):
                row["balance"] = round(row["balance"] + 5000.0, 2)

        failed, output = run(mutate)
        self.assertTrue(failed)
        self.assertIn("account statement %s opens at a printed" % month, output)

    def test_a_genuine_month_gap_warns_instead_of_being_skipped_silently(self):
        # Continuity cannot be checked across a month with no statement. That is
        # legitimate, but it must be said out loud rather than skipped.
        def mutate(data):
            dropped = ACCOUNT_MONTHS[1]
            data["account"]["transactions"] = [
                row for row in data["account"]["transactions"] if row["month"] != dropped
            ]
            data["account"]["months"].remove(dropped)
            data["account"]["sourceFiles"].pop(dropped)
            data["account"]["statementAnchors"].pop(dropped)
            data["account"]["quality"]["missingMonths"] = [dropped]
            data["account"]["quality"]["statementFiles"] = 2

        failed, output = run(mutate)
        self.assertFalse(failed, output)
        self.assertIn("no account statement between %s and %s"
                      % (ACCOUNT_MONTHS[0], ACCOUNT_MONTHS[2]), output)


class OpeningBalanceAnchorTests(unittest.TestCase):
    """Blind spot 2: the chain seeded from rows[0], so rows[0] was unfalsifiable."""

    def test_first_row_of_the_earliest_statement_is_checked(self):
        # This is the case nothing could catch before: the earliest statement has
        # no predecessor, so continuity says nothing about its first row.
        month = ACCOUNT_MONTHS[0]

        def mutate(data):
            rows = account_rows_for(data, month)
            rows[0]["amount"] = round(rows[0]["amount"] * 7, 2)

        failed, output = run(mutate)
        self.assertTrue(failed)
        self.assertIn("account statement %s opens at a printed" % month, output)

    def test_first_row_of_a_later_statement_is_checked(self):
        month = ACCOUNT_MONTHS[2]

        def mutate(data):
            rows = account_rows_for(data, month)
            rows[0]["amount"] = round(rows[0]["amount"] * 7, 2)

        failed, output = run(mutate)
        self.assertTrue(failed)
        self.assertIn("account statement %s opens at a printed" % month, output)

    def test_missing_anchors_fall_back_to_continuity_and_say_so(self):
        # Data written before parse_one.py exported the anchor must still
        # validate, but the weaker guarantee has to be visible.
        def mutate(data):
            data["account"].pop("statementAnchors")

        failed, output = run(mutate)
        self.assertFalse(failed, output)
        self.assertIn("carry no printed BALANCE B/F anchor", output)

    def test_a_recorded_closing_balance_that_disagrees_is_an_error(self):
        month = ACCOUNT_MONTHS[1]

        def mutate(data):
            anchor = data["account"]["statementAnchors"][month]
            anchor["closingBalance"] = round(anchor["closingBalance"] + 75.0, 2)

        failed, output = run(mutate)
        self.assertTrue(failed)
        self.assertIn("account statement %s ends at" % month, output)


class SourceToOutputTotalsTests(unittest.TestCase):
    """Blind spot 3: only row counts were compared, never the money."""

    def test_a_sign_flipped_refund_in_the_dashboard_is_caught(self):
        def mutate(data):
            for row in data["output"]["transactions"]:
                if row["type"] == "refund":
                    row["type"] = "debit"
                    return
            raise AssertionError("the fixture must contain a refund")

        failed, output = run(mutate)
        self.assertTrue(failed)
        self.assertIn("is typed 'debit' but its source says 'refund'", output)
        self.assertIn("totals disagree", output)

    def test_a_refund_relabelled_as_a_settled_payment_is_caught(self):
        def mutate(data):
            for row in data["output"]["transactions"]:
                if row["type"] == "refund":
                    row["type"] = "payment"
                    row["category"] = "Payment"
                    return

        failed, output = run(mutate)
        self.assertTrue(failed)
        self.assertIn("but its rule says 'Groceries' and nothing overrides it", output)

    def test_an_edited_legacy_amount_in_the_dashboard_is_caught(self):
        # Legacy rows carry no ID of their own, so before this they were only
        # ever counted. Their contents are now matched against the file.
        def mutate(data):
            for row in data["output"]["transactions"]:
                if row["provenance"]["sourceType"] == "legacy-manual":
                    row["amount"] = round(row["amount"] + 100.0, 2)
                    return

        failed, output = run(mutate)
        self.assertTrue(failed)
        self.assertIn("legacy rows in the dashboard do not echo", output)
        self.assertIn("totals disagree", output)

    def test_an_account_row_altered_on_its_way_into_the_dashboard_is_caught(self):
        # Account statements do not feed the dashboard today. If they ever do,
        # those rows must echo the statement as card rows already had to.
        def mutate(data):
            source = data["account"]["transactions"][0]
            built = built_from(source, source_type="account-pdf")
            built["amount"] = round(source["amount"] + 5.0, 2)
            built.pop("postedDate")
            data["output"]["transactions"].append(built)

        failed, output = run(mutate)
        self.assertTrue(failed)
        self.assertIn("changed amount during build", output)


class DuplicateIngestTests(unittest.TestCase):
    """Blind spot 4: a month re-read under a new file name formed its own chain."""

    def test_an_account_month_ingested_twice_is_caught(self):
        month = ACCOUNT_MONTHS[1]

        def mutate(data):
            clones = []
            for index, row in enumerate(account_rows_for(data, month), 1):
                clone = json.loads(json.dumps(row))
                clone["id"] = "tx_rescan%s%d" % (month.replace("-", ""), index)
                clone["provenance"]["sourceFile"] = "SYNTH_ONE_%s_rescan.pdf" \
                    % month.replace("-", "_")
                clones.append(clone)
            data["account"]["transactions"].extend(clones)

        failed, output = run(mutate)
        self.assertTrue(failed)
        self.assertIn("is ingested from 2 source files", output)
        self.assertIn("hold identical rows", output)

    def test_a_card_month_ingested_twice_is_caught(self):
        month = CARD_MONTHS[1]

        def mutate(data):
            clones = []
            for row in list(data["cards"]["transactions"]):
                if row["month"] != month:
                    continue
                clone = json.loads(json.dumps(row))
                clone["id"] = row["id"].replace("tx_card", "tx_resc")
                clone["provenance"]["sourceFile"] = "SYNTH_CC_%s_rescan.pdf" \
                    % month.replace("-", "_")
                clones.append(clone)
                built = built_from(clone)
                data["output"]["transactions"].append(built)
            data["cards"]["transactions"].extend(clones)
            counts = data["output"]["quality"]["integrity"]
            counts["sourceTransactions"] += len(clones)
            counts["outputTransactions"] += len(clones)

        failed, output = run(mutate)
        self.assertTrue(failed)
        self.assertIn("card data %s UOB ONE CARD is ingested from 2 source files" % month,
                      output)
        self.assertIn("hold identical rows", output)


class GapAndFreshnessTests(unittest.TestCase):
    """Blind spot 5: gaps were only checked in the direction that cannot fail."""

    def test_a_real_hole_with_an_empty_missing_list_is_caught(self):
        # Everything is removed consistently, exactly as a rebuild after losing a
        # statement would leave it. Only recomputing the range finds the hole.
        dropped = CARD_MONTHS[1]

        def mutate(data):
            data["cards"]["transactions"] = [
                row for row in data["cards"]["transactions"] if row["month"] != dropped
            ]
            data["cards"]["months"].remove(dropped)
            data["cards"]["sourceFiles"].pop(dropped)
            data["cards"]["quality"]["pdfMonths"].remove(dropped)
            data["output"]["transactions"] = [
                row for row in data["output"]["transactions"] if row["month"] != dropped
            ]
            data["output"]["months"] = sorted(
                {row["month"] for row in data["output"]["transactions"]})
            counts = data["output"]["quality"]["integrity"]
            counts["sourceTransactions"] = len(data["output"]["transactions"])
            counts["outputTransactions"] = len(data["output"]["transactions"])
            data["output"]["freshness"]["missingStatementMonths"] = []
            data["output"]["freshness"]["statementCount"] = 2

        failed, output = run(mutate)
        self.assertTrue(failed)
        self.assertIn("the card statements themselves are missing ['%s']" % dropped, output)

    def test_an_account_gap_left_out_of_missing_months_is_caught(self):
        dropped = ACCOUNT_MONTHS[1]

        def mutate(data):
            data["account"]["transactions"] = [
                row for row in data["account"]["transactions"] if row["month"] != dropped
            ]
            data["account"]["months"].remove(dropped)
            data["account"]["sourceFiles"].pop(dropped)
            data["account"]["statementAnchors"].pop(dropped)
            data["account"]["quality"]["statementFiles"] = 2
            # missingMonths deliberately left as []

        failed, output = run(mutate)
        self.assertTrue(failed)
        self.assertIn("account data reports [] as missing months", output)

    def test_a_month_index_that_does_not_match_its_rows_is_caught(self):
        def mutate(data):
            data["cards"]["months"].append(month_key(-2))

        failed, output = run(mutate)
        self.assertTrue(failed)
        self.assertIn("card data month index does not match its transactions", output)

    def test_a_stale_newest_statement_warns_without_failing(self):
        def mutate(data):
            data["output"]["freshness"]["sourceThrough"] = "2020-01-15"

        failed, output = run(mutate)
        self.assertFalse(failed, output)
        self.assertIn("days ago - a monthly statement has probably not been imported",
                      output)


class MalformedKeyTests(unittest.TestCase):
    """Blind spot 6: one crashed the run, the other sorted wrongly in silence."""

    def test_a_slash_separated_month_is_reported_not_raised(self):
        def mutate(data):
            row = data["cards"]["transactions"][0]
            broken = row["month"].replace("-", "/")
            row["month"] = broken
            row["provenance"]["statementMonth"] = broken
            for built in data["output"]["transactions"]:
                if built["id"] == row["id"]:
                    built["month"] = broken
                    built["provenance"]["statementMonth"] = broken

        failed, output = run(mutate)
        self.assertTrue(failed)
        self.assertIn("has invalid statement month", output)
        self.assertNotIn("Traceback", output)

    def test_a_non_zero_padded_date_is_reported(self):
        # "2026-3-4" survives strptime but sorts before "2026-12-31", so the
        # balance chain and every month index it touches quietly reorder.
        def mutate(data):
            row = data["cards"]["transactions"][0]
            year, month = row["month"].split("-")
            broken = "%s-%d-4" % (year, int(month))
            row["date"] = broken
            for built in data["output"]["transactions"]:
                if built["id"] == row["id"]:
                    built["date"] = broken

        failed, output = run(mutate)
        self.assertTrue(failed)
        self.assertIn("has invalid date", output)

    def test_a_calendar_impossible_date_is_reported(self):
        def mutate(data):
            row = data["cards"]["transactions"][0]
            broken = "%s-31" % row["month"] if row["month"].endswith("02") \
                else "%s-32" % row["month"]
            row["date"] = broken
            for built in data["output"]["transactions"]:
                if built["id"] == row["id"]:
                    built["date"] = broken

        failed, output = run(mutate)
        self.assertTrue(failed)
        self.assertIn("has invalid date", output)


if __name__ == "__main__":
    unittest.main()
