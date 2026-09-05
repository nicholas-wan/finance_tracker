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
    write(os.path.join(manual_dir, "owner_tags.json"),
          data.get("owner_tags", {"tags": {}, "tagsById": {}}))
    write(os.path.join(manual_dir, "transaction_overrides.json"),
          data.get("overrides", {"overridesById": {}}))
    write(os.path.join(manual_dir, "transaction_remarks.json"),
          data.get("remarks", {"remarksById": {}}))
    write(os.path.join(manual_dir, "risk_reviews.json"), {"recognizedSignals": []})
    write(os.path.join(manual_dir, "audit_history.json"), {"entries": []})
    write(os.path.join(manual_dir, "account_reviews.json"), {"recognizedSignals": []})
    # Hand-maintained inputs are optional on disk; a test opts in by putting
    # the payload in its dataset under these keys.
    for key, filename in (
        ("salary", "salary.json"),
        ("game_sales", "game_sales.json"),
        ("owner_rules", "owner_rules.json"),
        ("settlements_manual", "settlements.json"),
        ("identity_manual", "identity.json"),
    ):
        if key in data:
            write(os.path.join(manual_dir, filename), data[key])
    return data_dir, manual_dir


def run(mutate=None, omit=()):
    """Build the clean tree, apply one edit, and validate it. Returns (failed, text).

    `omit` names manual files to delete after the tree is written, which is how
    a fresh clone looks: manual/ is Git-ignored, so none of it exists yet.
    """
    data = dataset()
    if mutate:
        mutate(data)
    with tempfile.TemporaryDirectory() as tmp:
        data_dir, manual_dir = write_tree(tmp, data)
        for filename in omit:
            os.remove(os.path.join(manual_dir, filename))
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

    def test_mixed_import_generations_fail(self):
        def mutate(data):
            data["cards"]["generationId"] = "generation_a"
            data["account"]["generationId"] = "generation_b"
            data["output"]["generationId"] = "generation_a"

        failed, output = run(mutate)
        self.assertTrue(failed)
        self.assertIn("different import generations", output)

    def test_partial_import_generation_fails(self):
        def mutate(data):
            data["cards"]["generationId"] = "generation_a"

        failed, output = run(mutate)
        self.assertTrue(failed)
        self.assertIn("missing an import generation", output)

    def test_settlement_impacting_rule_rows_are_printed(self):
        def mutate(data):
            data["output"]["quality"]["review"]["splitByRule"] = {
                "count": 2,
                "amount": 32.0,
                "owedImpact": 16.0,
            }

        failed, output = run(mutate)
        self.assertFalse(failed, output)
        self.assertIn("2 settlement-impacting merchant-rule transaction(s)", output)


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


class FreshCloneTests(unittest.TestCase):
    """manual/ is Git-ignored, so a clone starts with none of those files.

    Every hand-maintained input is optional on disk and reads as empty when it
    is absent. owner_tags.json and legacy_transactions.json were the last two
    still loaded unconditionally, which aborted the whole run with a
    FileNotFoundError traceback instead of validating what was there.
    """

    @staticmethod
    def without_legacy(data):
        """Drop the legacy row: with no file there is no legacy source."""
        data["legacy"] = {"transactions": []}
        data["output"]["transactions"] = [
            row for row in data["output"]["transactions"]
            if row["provenance"]["sourceType"] != "legacy-manual"
        ]
        data["output"]["months"] = sorted(
            {row["month"] for row in data["output"]["transactions"]})
        remaining = len(data["output"]["transactions"])
        integrity = data["output"]["quality"]["integrity"]
        integrity["sourceTransactions"] = remaining
        integrity["outputTransactions"] = remaining

    def test_a_tree_without_owner_tags_or_legacy_files_validates(self):
        failed, output = run(
            self.without_legacy,
            omit=("owner_tags.json", "legacy_transactions.json"),
        )
        self.assertFalse(failed, output)
        self.assertNotIn("INTEGRITY ERRORS", output)

    def test_a_missing_owner_tags_file_still_checks_the_rest(self):
        # Absent is empty, not "skip the checks": a dangling remark is still
        # an error when owner_tags.json is not there.
        def mutate(data):
            self.without_legacy(data)
            data["remarks"] = {"remarksById": {"tx_gone000000000001": "note"}}

        failed, output = run(mutate, omit=("owner_tags.json",))
        self.assertTrue(failed)
        self.assertIn("no longer matches a transaction", output)

    def test_a_tree_with_no_manual_files_at_all_validates(self):
        failed, output = run(self.without_legacy, omit=(
            "owner_tags.json",
            "legacy_transactions.json",
            "transaction_overrides.json",
            "transaction_remarks.json",
            "risk_reviews.json",
            "audit_history.json",
            "account_reviews.json",
        ))
        self.assertFalse(failed, output)


class ManualInputTests(unittest.TestCase):
    """The hand-maintained files that feed the build are validated too."""

    def test_well_formed_manual_files_pass(self):
        def mutate(data):
            data["salary"] = {
                "steps": [{"from": CARD_MONTHS[0], "amount": 5000}],
                "years": [{"year": 2025, "income": 60000, "tax": 1200}],
            }
            data["game_sales"] = {
                "sales": [{"game": "Synth Quest", "month": CARD_MONTHS[0],
                           "amount": 120.0}],
            }
            data["owner_rules"] = {
                "rules": {"SYNTH GROCERY": "Shared"}, "confirmed": ["SYNTH GROCERY"],
            }
        failed, output = run(mutate)
        self.assertFalse(failed, output)

    def test_salary_step_with_malformed_month_fails(self):
        def mutate(data):
            data["salary"] = {
                "steps": [{"from": "2026/01", "amount": 5000}], "years": [],
            }
        failed, output = run(mutate)
        self.assertTrue(failed)
        self.assertIn("invalid from month", output)

    def test_duplicate_salary_year_fails(self):
        def mutate(data):
            data["salary"] = {
                "steps": [],
                "years": [{"year": 2025, "income": 1}, {"year": 2025, "income": 2}],
            }
        failed, output = run(mutate)
        self.assertTrue(failed)
        self.assertIn("lists 2025 twice", output)

    def test_salary_growth_must_match_incomes(self):
        def mutate(data):
            data["salary"] = {
                "steps": [],
                "years": [{"year": 2024, "income": 100000},
                          {"year": 2025, "income": 110000, "growth": 1.25}],
            }
        failed, output = run(mutate)
        self.assertTrue(failed)
        self.assertIn("growth 1.2500 disagrees", output)

    def test_salary_growth_matching_incomes_passes(self):
        def mutate(data):
            data["salary"] = {
                "steps": [],
                "years": [{"year": 2025, "income": 110000, "growth": 1.1,
                           "tax": 2000.5, "note": "YA2026"},
                          {"year": 2024, "income": 100000, "growth": None}],
            }
        failed, output = run(mutate)
        self.assertFalse(failed, output)

    def test_non_positive_salary_growth_fails(self):
        def mutate(data):
            data["salary"] = {
                "steps": [], "years": [{"year": 2025, "income": 1, "growth": 0}],
            }
        failed, output = run(mutate)
        self.assertTrue(failed)
        self.assertIn("growth", output)

    def test_non_positive_game_sale_fails(self):
        def mutate(data):
            data["game_sales"] = {
                "sales": [{"game": "Synth Quest", "month": CARD_MONTHS[0],
                           "amount": 0}],
            }
        failed, output = run(mutate)
        self.assertTrue(failed)
        self.assertIn("game sale 1 has invalid amount", output)

    def test_owner_rule_with_unknown_owner_fails(self):
        def mutate(data):
            data["owner_rules"] = {"rules": {"SYNTH GROCERY": "Everyone"},
                                   "confirmed": []}
        failed, output = run(mutate)
        self.assertTrue(failed)
        self.assertIn("unknown owner", output)

    def test_settlement_drift_from_manual_file_fails(self):
        # The dashboard's embedded copy stays empty while the manual file gains
        # an opening balance: the build is stale and the Split tab is wrong.
        def mutate(data):
            data["settlements_manual"] = {
                "openingBalances": [
                    {"from": CARD_MONTHS[0], "youOweYx": 250.0}],
                "payments": [],
            }
        failed, output = run(mutate)
        self.assertTrue(failed)
        self.assertIn("differs from manual/settlements.json", output)

    def test_matching_identity_copy_passes(self):
        def mutate(data):
            data["identity_manual"] = {
                "statementHolderName": "REDACTED HOLDER NAME",
                "knownAccounts": {"1111111111": "Redacted Savings A/c"},
                "fixedDepositAccounts": ["2222222222"],
                "trustedCounterparties": ["Redacted Person"],
            }
            data["output"]["identity"] = {
                "knownAccounts": {"1111111111": "Redacted Savings A/c"},
                "trustedCounterparties": ["Redacted Person"],
            }
        failed, output = run(mutate)
        self.assertFalse(failed, output)

    def test_identity_drift_from_manual_file_fails(self):
        # Own-account labels and trusted names are private, so the dashboard
        # only ever sees the copy embedded at build time. A stale copy labels a
        # transfer with the wrong account and trusts the wrong counterparty.
        def mutate(data):
            data["identity_manual"] = {
                "knownAccounts": {"1111111111": "Redacted Savings A/c"},
                "trustedCounterparties": ["Redacted Person"],
            }
            data["output"]["identity"] = {
                "knownAccounts": {"1111111111": "Stale Label"},
                "trustedCounterparties": ["Redacted Person"],
            }
        failed, output = run(mutate)
        self.assertTrue(failed)
        self.assertIn("identity.knownAccounts in the dashboard differs from "
                      "manual/identity.json", output)

    def test_identity_trusted_counterparty_drift_fails(self):
        def mutate(data):
            data["identity_manual"] = {
                "knownAccounts": {},
                "trustedCounterparties": ["Redacted Person"],
            }
        failed, output = run(mutate)
        self.assertTrue(failed)
        self.assertIn("identity.trustedCounterparties in the dashboard differs from "
                      "manual/identity.json", output)

    def test_identity_account_key_that_is_not_a_number_fails(self):
        def mutate(data):
            data["identity_manual"] = {
                "knownAccounts": {"Lady's account": "Redacted Savings A/c"},
                "trustedCounterparties": [],
            }
        failed, output = run(mutate)
        self.assertTrue(failed)
        self.assertIn("is not an account number", output)

    def test_identity_with_a_blank_trusted_counterparty_fails(self):
        # An empty name would match every counterparty and silence the
        # new-counterparty check outright.
        def mutate(data):
            data["identity_manual"] = {
                "knownAccounts": {},
                "trustedCounterparties": ["Redacted Person", "  "],
            }
        failed, output = run(mutate)
        self.assertTrue(failed)
        self.assertIn("trustedCounterparties in manual/identity.json must be a list",
                      output)

    def test_malformed_legacy_tag_key_fails(self):
        def mutate(data):
            data["owner_tags"] = {
                "tags": {"not-a-tag-key": ["Nic"]}, "tagsById": {},
            }
        failed, output = run(mutate)
        self.assertTrue(failed)
        self.assertIn("malformed key", output)

    def test_legacy_tag_with_unknown_owner_fails(self):
        def mutate(data):
            data["owner_tags"] = {
                "tags": {"2020-01|SOME MERCHANT|10.00|D": ["Everyone"]},
                "tagsById": {},
            }
        failed, output = run(mutate)
        self.assertTrue(failed)
        self.assertIn("invalid owner list", output)

    def test_orphaned_legacy_tags_note_without_failing(self):
        # A well-formed key that no longer matches any card row is reported as
        # a count, not an error: relabeled statement months make these normal.
        def mutate(data):
            data["owner_tags"] = {
                "tags": {"2020-01|LONG GONE MERCHANT|10.00|D": ["Nic"]},
                "tagsById": {},
            }
        failed, output = run(mutate)
        self.assertFalse(failed, output)
        self.assertIn("1 legacy owner-tag key(s) match no card row", output)


class ShopeeOrderLinkTests(unittest.TestCase):
    def test_reviewed_aggregate_passes_and_covers_each_order(self):
        source_orders = [
            {"orderId": "242058592217954", "merchant": "First Store",
             "status": "completed", "amount": 10.25, "items": ["First item"],
             "historyIndex": 0},
            {"orderId": "242058592217955", "merchant": "Second Store",
             "status": "completed", "amount": 8.65, "items": ["Second item"],
             "historyIndex": 1},
        ]
        note = "Two adjacent orders add exactly to the statement charge."
        manual = {
            "orders": source_orders,
            "statementAggregates": [{
                "transactionId": "tx_shopee000000000001",
                "orderIds": [order["orderId"] for order in source_orders],
                "note": note,
            }],
        }
        published = [dict(order, category="Shopping",
                          statementTransactionId="tx_shopee000000000001",
                          date="2026-08-24") for order in source_orders]
        details = [{key: order[key] for key in (
            "orderId", "merchant", "status", "amount", "items", "historyIndex", "category"
        )} for order in published]
        row = {
            "id": "tx_shopee000000000001", "date": "2026-08-24",
            "description": "SHOPEE SG MP SINGAPORE", "type": "debit", "amount": 18.90,
            "shopee": details[0], "shopeeOrders": details,
            "shopeeMatch": {"kind": "aggregate", "note": note},
        }
        output = {
            "shopeeOrders": published,
            "quality": {"shopee": {
                "orders": 2, "matched": 2, "unmatched": 0, "groceries": 0,
            }},
        }
        errors = []
        validate_data.validate_shopee(manual, output, {row["id"]: row}, errors)
        self.assertEqual(errors, [])


class TripBookingLinkTests(unittest.TestCase):
    """validate_trip re-derives the matcher's rule on the published rows."""

    def booking(self, **overrides):
        base = {
            "bookingNo": "1234567890123", "status": "Completed", "productType": "Hotels",
            "bookingDate": "June 12, 2026", "productName": "Example Hotel",
            "travelTime": "June 20, 2026", "traveller": "Example Traveller",
            "currency": "SGD", "amount": 321.45, "sourceFile": "synthetic.xlsx",
        }
        base.update(overrides)
        return base

    def linked_row(self, **overrides):
        detail = self.booking()
        row = {
            "id": "tx_trip0000000000001", "date": "2026-06-13", "type": "debit",
            "description": "TRIP.COM SINGAPORE", "amount": 321.45,
            "displayName": "Example Hotel", "displayNameSource": "trip-booking",
            "tripBooking": detail, "trip": {"status": "booking-matched"},
        }
        row.update(overrides)
        return row

    def check(self, row=None, manual=None, quality=None, output_extra=None,
              reconciliation=None):
        row = row or self.linked_row()
        manual = {"bookings": [self.booking()]} if manual is None else manual
        summary = {"bookings": 1, "matched": 1, "matchedCancelled": 0}
        if quality is not None:
            summary.update(quality)
        output = {"quality": {"trip": summary}}
        output.update(output_extra or {})
        errors = []
        validate_data.validate_trip(
            manual, output, {row["id"]: row}, errors, reconciliation
        )
        return errors

    def test_a_clean_link_passes(self):
        self.assertEqual(self.check(), [])

    def test_an_absent_import_and_summary_is_silent(self):
        errors = []
        validate_data.validate_trip({}, {"quality": {}}, {}, errors)
        self.assertEqual(errors, [])

    def test_each_broken_rule_is_named(self):
        cases = {
            "refund": (self.linked_row(type="refund"), "not a Trip.com charge"),
            "other merchant": (self.linked_row(description="SOME HOTEL"), "not a Trip.com charge"),
            "amount": (self.linked_row(amount=321.44), "disagrees on amount"),
            "window": (self.linked_row(date="2026-07-30"), "outside the match window"),
            "renamed": (self.linked_row(displayName="Other"), "not the product name"),
            "no source": (self.linked_row(displayNameSource=None), "no display-name source"),
            "unknown": (self.linked_row(tripBooking=self.booking(bookingNo="9999999999999")),
                        "unknown Trip.com booking"),
            "altered": (self.linked_row(tripBooking=self.booking(status="Used")),
                        "changed status during build"),
            "foreign": (self.linked_row(tripBooking=self.booking(currency="CNY")),
                        "non-SGD"),
            "name without booking": (
                {"id": "tx_x", "displayNameSource": "trip-booking", "displayName": "X"},
                "claims a Trip.com name without a booking"),
            "missing marker": (self.linked_row(trip=None), "inconsistent Trip.com marker"),
            "wrong marker": (self.linked_row(trip={"status": "unmatched"}),
                             "inconsistent Trip.com marker"),
            "unmatched row without marker": (
                {"id": "tx_y", "type": "debit", "description": "TRIP.COM SINGAPORE",
                 "amount": 5.0},
                "inconsistent Trip.com marker"),
            "marker on a non-trip row": (
                {"id": "tx_z", "type": "debit", "description": "SOME CAFE", "amount": 5.0,
                 "trip": {"status": "unmatched"}},
                "inconsistent Trip.com marker"),
        }
        for label, (row, message) in cases.items():
            with self.subTest(label):
                errors = self.check(row=row)
                self.assertTrue(any(message in error for error in errors), errors)

    def test_the_summary_and_the_export_are_checked(self):
        self.assertTrue(any("summary disagrees" in e for e in self.check(quality={"matched": 0})))
        self.assertTrue(any("miscounts cancelled" in e
                            for e in self.check(quality={"matchedCancelled": 1})))
        self.assertTrue(any("must not be published" in e
                            for e in self.check(output_extra={"tripBookings": []})))
        self.assertTrue(any("duplicate booking number" in e for e in self.check(
            manual={"bookings": [self.booking(), self.booking()]})))
        cancelled = self.check(
            row=self.linked_row(tripBooking=self.booking(status="Cancelled")),
            manual={"bookings": [self.booking(status="Cancelled")]},
            quality={"matchedCancelled": 1})
        self.assertEqual(cancelled, [])

    def test_a_reviewed_aggregate_refund_passes(self):
        first = self.booking(status="Cancelled")
        second = self.booking(
            bookingNo="1234567890124", status="Cancelled", amount=100.0
        )
        row = self.linked_row(
            type="refund",
            amount=421.45,
            tripBooking=first,
            tripBookings=[first, second],
            tripMatch={
                "kind": "aggregate",
                "note": "The refund combines both cancelled bookings.",
            },
        )
        errors = self.check(
            row=row,
            manual={"bookings": [first, second]},
            quality={
                "bookings": 2,
                "matched": 0,
                "matchedRefunds": 1,
                "matchedTransactions": 1,
                "matchedBookings": 2,
            },
            reconciliation={
                "links": [{
                    "transactionIds": [row["id"]],
                    "bookingNos": [first["bookingNo"], second["bookingNo"]],
                    "kind": "aggregate",
                    "note": "The refund combines both cancelled bookings.",
                }]
            },
        )
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
