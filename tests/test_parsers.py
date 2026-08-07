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

parse_cc = importlib.import_module("parse_cc")
parse_one = importlib.import_module("parse_one")


class FakePage:
    def __init__(self, text):
        self.text = text

    def extract_text(self):
        return self.text


class FakeReader:
    def __init__(self, text):
        pages = text if isinstance(text, list) else [text]
        self.pages = [FakePage(page) for page in pages]


class ParserTestCase(unittest.TestCase):
    def fixture(self, name):
        return (FIXTURES / name).read_text(encoding="utf-8")

    def parse_card(self, name):
        with patch.object(parse_cc, "PdfReader", return_value=FakeReader(self.fixture(name))):
            return parse_cc.parse_pdf("UOB_CC_REDACTED.pdf")

    def parse_account(self, name):
        with patch.object(parse_one, "PdfReader", return_value=FakeReader(self.fixture(name))):
            return parse_one.parse_pdf("UOB_ONE_REDACTED.pdf")

    def run_card_main(self, fixture_name):
        """Run parse_cc.main() over one synthetic statement in a throwaway tree.

        Returns (raised_systemexit, payload_handed_to_write_output, output_path).
        """
        captured = {}
        real_write = parse_cc.write_output

        def recorder(payload, ok):
            captured["payload"] = payload
            return real_write(payload, ok)

        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "statements"))
            open(os.path.join(tmp, "statements", "UOB_CC_2026_06.pdf"), "w").close()
            out_path = os.path.join(tmp, "app", "data", "card_transactions.json")
            with patch.object(parse_cc, "REPO_ROOT", tmp), \
                    patch.object(parse_cc, "OUT_PATH", out_path), \
                    patch.object(parse_cc, "PdfReader",
                                 return_value=FakeReader(self.fixture(fixture_name))), \
                    patch.object(parse_cc, "write_output", recorder), \
                    contextlib.redirect_stdout(io.StringIO()):
                exited = False
                try:
                    parse_cc.main()
                except SystemExit:
                    exited = True
                exists = os.path.exists(out_path)
                if exists:
                    with open(out_path, encoding="utf-8") as handle:
                        written = json.load(handle)
                else:
                    written = None
            return exited, captured.get("payload"), out_path, exists, written

    def write_csv(self, tmp, rows):
        path = os.path.join(tmp, "UOB_CC_2026_06.csv")
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write("Post,Trans,Description of Transaction,Transaction Amount\n")
            for row in rows:
                handle.write(",".join('"%s"' % cell for cell in row) + "\n")
        return path


class CardParserTests(ParserTestCase):
    def test_duplicates_foreign_amount_credit_and_reconciliation(self):
        month, rows, checks, failures = self.parse_card("card_statement_redacted.txt")
        self.assertEqual(month, "2026-06")
        self.assertEqual(failures, [])
        self.assertEqual(len(rows), 4)
        self.assertEqual([row["amount"] for row in rows], [6.0, 6.0, 18.0, 5.0])
        self.assertEqual(rows[0]["postedDate"], "2026-06-12")
        self.assertEqual(rows[2]["foreign"], "JPY 1,220.00")
        self.assertTrue(rows[3]["credit"])
        self.assertEqual(checks["UOB ONE CARD"]["gap"], 0.0)
        self.assertEqual(len({row["id"] for row in rows}), 4)
        self.assertTrue(all(row["provenance"]["verified"] for row in rows))

    def test_ids_repeat_exactly_for_same_source(self):
        _, first, _, _ = self.parse_card("card_statement_redacted.txt")
        _, second, _, _ = self.parse_card("card_statement_redacted.txt")
        self.assertEqual([row["id"] for row in first], [row["id"] for row in second])

    def test_skip_rules_no_longer_eat_wrapped_amount_lines(self):
        # "Trans", "Total " and "SINGAPORE \d" used to match these continuation
        # lines as statement furniture, so all three rows vanished silently.
        _, rows, checks, failures = self.parse_card("card_wrapped_amount_redacted.txt")
        self.assertEqual(failures, [])
        self.assertEqual(len(rows), 3)
        self.assertEqual([row["amount"] for row in rows], [25.0, 25.0, 25.0])
        self.assertEqual(
            [row["description"] for row in rows],
            ["REDACTED TRANSPORT TRANSIT LINK PTE",
             "REDACTED DRINKS TOTAL WINE MORE",
             "REDACTED SHOP SINGAPORE 408600"],
        )
        self.assertEqual(checks["UOB ONE CARD"]["gap"], 0.0)

    def test_skip_patterns_are_anchored(self):
        for line in ("TRANSIT LINK PTE 25.00", "TOTAL WINE MORE 25.00",
                     "SINGAPORE 408600 25.00", "DATE NIGHT BISTRO 25.00",
                     "POSTAL CAFE 25.00", "AMOUNT BAR 25.00"):
            self.assertIsNone(parse_cc.SKIP_RE.match(line), line)
        for line in ("Ref No. : 74556223020302001533816", "SUB TOTAL 125.00",
                     "PREVIOUS BALANCE 100.00", "TOTAL BALANCE FOR UOB ONE CARD 125.00",
                     "SINGAPORE 408600", "Page 1 of 4", "Date", "Trans",
                     "1234-5678-9012-3456 100.00 25.00"):
            self.assertIsNotNone(parse_cc.SKIP_RE.match(line), line)

    def test_amount_regex_edges(self):
        # A comma in front of the digits used to be invisible to the lookbehind,
        # so "JPY1,220.00" was read as a S$220.00 charge.
        self.assertIsNone(parse_cc.AMOUNT_RE.search("JPY1,220.00"))
        self.assertIsNone(parse_cc.AMOUNT_RE.search("REF12345.67ABC"))
        self.assertEqual(parse_cc.AMOUNT_RE.search("12345.67").group(1), "12345.67")
        self.assertEqual(parse_cc.AMOUNT_RE.search("SHOP 1,220.00").group(1), "1,220.00")
        self.assertEqual(parse_cc.AMOUNT_RE.search("SHOP 25.00 CR").group(1), "25.00")
        self.assertEqual(parse_cc.AMOUNT_RE.search("SHOP 25.00 CR").group(2), "CR")

    def test_row_that_never_finds_an_amount_is_fatal(self):
        # The section still reconciles - a lost row is exactly what net
        # arithmetic cannot see - so the failure has to come from the parser.
        _, rows, checks, failures = self.parse_card("card_lost_row_redacted.txt")
        self.assertEqual(len(rows), 1)
        self.assertEqual(checks["UOB ONE CARD"]["gap"], 0.0)
        self.assertEqual(len(failures), 1)
        self.assertIn("MERCHANT A", failures[0]["text"])
        self.assertIn("never resolved an amount", failures[0]["reason"])

    def test_unreconciled_section_is_exported_and_nothing_is_written(self):
        exited, payload, _, exists, _ = self.run_card_main("card_unreconciled_redacted.txt")
        self.assertTrue(exited)
        self.assertFalse(exists, "card_transactions.json must not be written on failure")
        self.assertEqual(
            payload["quality"]["unreconciledSections"],
            [{"month": "2026-06", "card": "UOB ONE CARD", "gap": 25.0,
              "statementSubtotal": 150.0, "rowsGive": 125.0}],
        )

    def test_lost_row_stops_the_file_being_written(self):
        exited, payload, _, exists, _ = self.run_card_main("card_lost_row_redacted.txt")
        self.assertTrue(exited)
        self.assertFalse(exists)
        # Reconciliation was clean; only the row-level failure caught this.
        self.assertEqual(payload["quality"]["unreconciledSections"], [])
        self.assertEqual(payload["quality"]["unparsedRows"], 1)

    def test_unchecked_section_is_fatal_and_nothing_is_written(self):
        # A section with no SUB TOTAL cannot be reconciled at all; publishing
        # it would smuggle unverifiable rows past the balance check.
        exited, payload, out_path, exists, _ = self.run_card_main(
            "card_missing_subtotal_redacted.txt")
        self.assertTrue(exited)
        self.assertFalse(exists)
        self.assertFalse(os.path.exists(out_path + ".tmp"))
        self.assertEqual(payload["quality"]["uncheckedSections"], 1)

    def test_clean_run_writes_and_exports_an_empty_gap_list(self):
        exited, payload, _, exists, written = self.run_card_main("card_statement_redacted.txt")
        self.assertFalse(exited)
        self.assertTrue(exists)
        self.assertEqual(payload["quality"]["unreconciledSections"], [])
        self.assertEqual(payload["quality"]["unparsedRows"], 0)
        self.assertEqual(len(written["transactions"]), 4)


class CardCsvFallbackTests(ParserTestCase):
    def test_unparseable_row_is_reported_not_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_csv(tmp, [
                ("Date", "Date", "", "SGD"),
                ("", "", "PREVIOUS BALANCE", "100.00"),
                ("10 JUN", "10 JUN", "REDACTED MERCHANT A", "25.00"),
                ("11 JUN", "11 JUN", "REDACTED MERCHANT B", "not a number"),
                ("", "", "SUB TOTAL", "125.00"),
            ])
            _, rows, failures = parse_cc.parse_csv(path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(failures), 1)
        self.assertIn("no usable amount", failures[0]["reason"])
        self.assertIn("MERCHANT B", failures[0]["text"])

    def test_known_furniture_rows_are_not_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_csv(tmp, [
                ("Date", "Date", "", "SGD"),
                ("", "", "PREVIOUS BALANCE", "100.00"),
                ("", "", "JPY 1,220.00", ""),
                ("10 JUN", "10 JUN", "REDACTED MERCHANT A", "25.00"),
                ("", "", "SUB TOTAL", "125.00"),
                ("", "", "TOTAL BALANCE FOR UOB ONE CARD", "125.00"),
            ])
            _, rows, failures = parse_cc.parse_csv(path)
        self.assertEqual(failures, [])
        self.assertEqual(len(rows), 1)

    def test_leading_minus_is_a_credit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_csv(tmp, [
                ("10 JUN", "10 JUN", "REDACTED REFUND", "-25.00"),
                ("11 JUN", "11 JUN", "REDACTED REFUND CR SUFFIX", "25.00 CR"),
                ("12 JUN", "12 JUN", "REDACTED CHARGE", "25.00"),
            ])
            _, rows, failures = parse_cc.parse_csv(path)
        self.assertEqual(failures, [])
        self.assertEqual([row["amount"] for row in rows], [25.0, 25.0, 25.0])
        self.assertEqual([row["credit"] for row in rows], [True, True, False])


class AccountParserTests(ParserTestCase):
    def test_direction_classification_and_visible_override(self):
        month, rows, overrides, failures, _ = self.parse_account(
            "account_statement_redacted.txt")
        self.assertEqual(month, "2026-06")
        self.assertEqual(failures, [])
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["direction"], "deposit")
        self.assertEqual(rows[0]["flow"], "Salary")
        self.assertEqual(rows[1]["direction"], "withdrawal")
        self.assertEqual(rows[1]["flow"], "Investment")
        self.assertEqual(rows[2]["amount"], 60.0)
        self.assertEqual(rows[2]["amountSource"], "balance")
        self.assertEqual(len(overrides), 1)
        self.assertTrue(all(row["provenance"]["verified"] for row in rows))

    def test_printed_opening_balance_is_exported_as_an_anchor(self):
        # The first movement on a statement seeds the balance chain from its own
        # balance, so nothing inside the statement can contradict it. The printed
        # BALANCE B/F is the only independent witness, so it has to leave the
        # parser.
        _, rows, _, _, anchor = self.parse_account("account_statement_redacted.txt")
        self.assertEqual(anchor["file"], "UOB_ONE_REDACTED.pdf")
        self.assertEqual(anchor["openingBalance"], 1000.00)
        self.assertEqual(anchor["closingBalance"], rows[-1]["balance"])
        self.assertEqual(anchor["rows"], len(rows))
        # The anchor must be the printed figure, not the first row read back.
        first = rows[0]
        derived = round(first["balance"] - first["amount"], 2)
        self.assertEqual(anchor["openingBalance"], derived)

    def test_row_with_too_few_numbers_is_fatal_not_absorbed(self):
        # The lost salary line used to be skipped without advancing the balance,
        # which turned the next row's S$80 withdrawal into a S$4,920 deposit.
        _, rows, overrides, failures, _ = self.parse_account("account_lost_row_redacted.txt")
        self.assertEqual(failures[0]["file"], "UOB_ONE_REDACTED.pdf")
        self.assertIn("REDACTED SALARY", failures[0]["text"])
        self.assertIn("number(s)", failures[0]["reason"])
        # The grocery row's delta now spans two rows, so it is refused as well
        # instead of being published as a S$4,920 deposit.
        self.assertEqual(len(failures), 2)
        self.assertIn("REDACTED GROCERY", failures[1]["text"])
        self.assertIn("no trusted running balance", failures[1]["reason"])
        self.assertEqual(rows, [])
        self.assertEqual(overrides, [])

    def test_printed_sign_contradicting_the_balance_move_is_fatal(self):
        _, rows, _, failures, _ = self.parse_account("account_sign_conflict_redacted.txt")
        self.assertEqual(rows, [])
        self.assertEqual(len(failures), 2)
        self.assertIn("REDACTED REFUND", failures[0]["text"])
        self.assertIn("deposit", failures[0]["reason"])
        self.assertIn("REDACTED FEE", failures[1]["text"])
        self.assertIn("withdrawal", failures[1]["reason"])

    def test_legal_footer_is_not_appended_to_transaction_description(self):
        text = self.fixture("account_statement_redacted.txt").replace(
            "End of Transaction Details",
            "Please note that you are bound by a duty under the rules governing this account\n"
            "omissions or unauthorised debits within fourteen (14) days of this statement\n"
            "End of Transaction Details",
        )
        with patch.object(parse_one, "PdfReader", return_value=FakeReader(text)):
            _, rows, _, failures, _ = parse_one.parse_pdf("UOB_ONE_REDACTED.pdf")
        self.assertEqual(failures, [])
        self.assertTrue(rows)
        self.assertTrue(all("Please note" not in row["description"] for row in rows))
        self.assertTrue(all("omissions or unauthorised" not in row["description"]
                            for row in rows))

    def test_new_page_footer_cannot_pollute_prior_page_transaction(self):
        first_page = self.fixture("account_statement_redacted.txt").replace(
            "End of Transaction Details\n", ""
        )
        second_page = (
            "claim against the bank in relation thereto. BROKEN PDF GLYPHS\n"
            "Page 2 of 2\n"
            "05 JUN REDACTED FEE 10.00 830.00\n"
            "End of Transaction Details\n"
        )
        with patch.object(parse_one, "PdfReader",
                          return_value=FakeReader([first_page, second_page])):
            _, rows, _, failures, _ = parse_one.parse_pdf("UOB_ONE_REDACTED.pdf")
        self.assertEqual(failures, [])
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[2]["description"], "REDACTED TRANSFER")
        self.assertTrue(all("claim against" not in row["description"] for row in rows))

    def test_failed_statement_is_not_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "statements"))
            open(os.path.join(tmp, "statements", "UOB_ONE_2026_06.pdf"), "w").close()
            out_path = os.path.join(tmp, "app", "data", "account_transactions.json")
            with patch.object(parse_one, "REPO_ROOT", tmp), \
                    patch.object(parse_one, "OUT_PATH", out_path), \
                    patch.object(parse_one, "PdfReader",
                                 return_value=FakeReader(
                                     self.fixture("account_lost_row_redacted.txt"))), \
                    contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit):
                    parse_one.main()
            self.assertFalse(os.path.exists(out_path))
            self.assertFalse(os.path.exists(out_path + ".tmp"))

    def test_amount_override_is_fatal_and_nothing_is_written(self):
        # account_statement_redacted.txt carries one row whose printed amount
        # disagrees with the balance movement. That contradiction must abort
        # the run before the published JSON is replaced.
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "statements"))
            open(os.path.join(tmp, "statements", "UOB_ONE_2026_06.pdf"), "w").close()
            out_path = os.path.join(tmp, "app", "data", "account_transactions.json")
            with patch.object(parse_one, "REPO_ROOT", tmp), \
                    patch.object(parse_one, "OUT_PATH", out_path), \
                    patch.object(parse_one, "PdfReader",
                                 return_value=FakeReader(
                                     self.fixture("account_statement_redacted.txt"))), \
                    contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit):
                    parse_one.main()
            self.assertFalse(os.path.exists(out_path))
            self.assertFalse(os.path.exists(out_path + ".tmp"))

    def test_flow_rules_for_broker_and_tax_authority(self):
        self.assertEqual(
            parse_one.classify("PAYNOW-FAST PHILLIP SECURITIES MBK2601088841599485"),
            "Investment",
        )
        self.assertEqual(
            parse_one.classify("PAYNOW-FAST PAYNOW OTHR PHILLIP SECURITIES PPPC2702261"),
            "Investment",
        )
        self.assertEqual(
            parse_one.classify("PAYNOW-FAST PIB2405262496091452 INLAND REVENUE AUTHO OTHR"),
            "Tax",
        )
        # The existing tax and transfer rules keep working.
        self.assertEqual(parse_one.classify("GIRO IRAS INCOME TAX"), "Tax")
        self.assertEqual(parse_one.classify("Interest Credit"), "Interest")
        self.assertEqual(parse_one.classify("PAYNOW-FAST SOMEONE ELSE"), "Transfer")


if __name__ == "__main__":
    unittest.main()
