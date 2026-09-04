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


# The real holder name and account numbers live in the git-ignored
# manual/identity.json. Tests hand the parser this fabricated stand-in, whose
# first word is deliberately the first word of the fixture's recipient line so
# the full-name-versus-first-name regression stays reproducible.
TEST_IDENTITY = {
    "statementHolderName": "REDACTED HOLDER NAME",
    "knownAccounts": {"1111111111": "Redacted Savings A/c"},
    "fixedDepositAccounts": ["2222222222"],
    "trustedCounterparties": ["Redacted Person"],
}


class ParserTestCase(unittest.TestCase):
    def fixture(self, name):
        return (FIXTURES / name).read_text(encoding="utf-8")

    def write_identity(self, root, identity=None):
        """Seed a throwaway tree with the identity file main() insists on."""
        manual_dir = os.path.join(root, "manual")
        os.makedirs(manual_dir, exist_ok=True)
        with open(os.path.join(manual_dir, "identity.json"), "w",
                  encoding="utf-8") as handle:
            json.dump(TEST_IDENTITY if identity is None else identity, handle)

    def parse_card(self, name):
        with patch.object(parse_cc, "PdfReader", return_value=FakeReader(self.fixture(name))):
            return parse_cc.parse_pdf("UOB_CC_REDACTED.pdf")

    def parse_account(self, name, identity=None):
        with patch.object(parse_one, "PdfReader", return_value=FakeReader(self.fixture(name))):
            return parse_one.parse_pdf(
                "UOB_ONE_REDACTED.pdf",
                TEST_IDENTITY if identity is None else identity)

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
    def test_statement_month_never_falls_back_to_the_filename(self):
        reader = FakeReader("No readable statement date")
        self.assertIsNone(
            parse_cc.statement_month(reader, "UOB_CC_2026_06.pdf")
        )

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

    def test_wrapped_continuation_line_is_not_dropped_as_a_heading(self):
        # "MR BEAN INTERNATIONAL PTE" matches the cardholder-name alternative
        # (MR + two or more capitalised words). Dropping it mid-row truncated
        # the description to "REDACTED SOME LONG PREFIX" while the amount, read
        # from the following line, still reconciled - so nothing complained.
        _, rows, checks, failures = self.parse_card(
            "card_wrapped_name_continuation_redacted.txt")
        self.assertEqual(failures, [])
        self.assertEqual(
            rows[0]["description"], "REDACTED SOME LONG PREFIX MR BEAN INTERNATIONAL PTE")
        self.assertEqual(rows[0]["amount"], 25.0)
        self.assertEqual(checks["UOB ONE CARD"]["gap"], 0.0)

    def test_true_furniture_is_still_dropped_mid_row(self):
        # The second row of the same fixture wraps over a reference tail, a page
        # number, the bank's own name, its legal note and its postcode. None of
        # those may reach the description.
        _, rows, _, failures = self.parse_card(
            "card_wrapped_name_continuation_redacted.txt")
        self.assertEqual(failures, [])
        self.assertEqual(rows[1]["description"], "REDACTED MERCHANT B")
        self.assertEqual(rows[1]["amount"], 30.0)

    def test_midrow_skip_is_the_safe_subset_of_the_furniture_catalogue(self):
        for line in ("Page 1 of 4", "Ref No. : 11111111111111111111111",
                     "1234-5678-9012-3456 100.00 25.00", "Contact Us",
                     "Call 1800 222 2121", "Email card.centre@uobgroup.com",
                     "SINGAPORE 048624", "United Overseas Bank Limited",
                     "Please note that this is a computer generated statement",
                     "Postage will be paid by licensee", "omissions or unauthorised",
                     "claim against the Bank"):
            self.assertIsNotNone(parse_cc.MIDROW_SKIP_RE.match(line), line)
        # Headings that a wrapped merchant name can imitate stay in the full
        # catalogue but must never be dropped while a row is open.
        for line in ("MR BEAN INTERNATIONAL PTE", "MR TEO GARDEN SUPPLIES",
                     "Trans", "Date", "Amount", "GRAND TOTAL"):
            self.assertIsNone(parse_cc.MIDROW_SKIP_RE.match(line), line)
            self.assertIsNotNone(parse_cc.SKIP_RE.match(line), line)

    def test_wrapped_card_line_does_not_open_a_phantom_section(self):
        # A continuation line of letters ending in CARD used to switch sections,
        # so every later row was filed under a card the statement never had and
        # the run died with an "unchecked section" message instead.
        _, rows, checks, failures = self.parse_card("card_phantom_section_redacted.txt")
        self.assertEqual(failures, [])
        self.assertEqual({row["card"] for row in rows}, {"UOB ONE CARD"})
        self.assertEqual(rows[0]["description"], "REDACTED DEPARTMENT STORE GIFT CARD")
        self.assertEqual(sorted(checks), ["UOB ONE CARD"])
        self.assertEqual(checks["UOB ONE CARD"]["gap"], 0.0)

    def test_card_header_matches_only_real_section_names(self):
        for line in ("UOB ONE CARD", "LADY'S SOLITAIRE CARD",
                     "UOB ONE CARD (CONTINUED)", "LADY'S SOLITAIRE CARD(CONTINUED)",
                     "UOB PRVI MILES CARD"):
            self.assertIsNotNone(parse_cc.CARD_RE.match(line), line)
        # Prose and footer headings that the old catch-all alternative accepted.
        for line in ("GIFT CARD", "UOB Credit Card", "One Credit Card",
                     "Mondays with One Card", "CARD",
                     "finance charges and cash advance charges applicable to your card"):
            self.assertIsNone(parse_cc.CARD_RE.match(line), line)

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

    def test_header_drift_fails_closed_instead_of_emptying_the_month(self):
        # A re-saved export with renamed columns used to make every row look
        # like furniture: zero rows, zero failures, and a silently missing
        # month in the published data.
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "UOB_CC_2026_06.csv")
            with open(path, "w", encoding="utf-8", newline="") as handle:
                handle.write("Post Date,Trans Date,Description,Amount\n")
                handle.write('"10 JUN","10 JUN","REDACTED MERCHANT A","25.00"\n')
            _, rows, failures = parse_cc.parse_csv(path)
        self.assertEqual(rows, [])
        self.assertEqual(len(failures), 1)
        self.assertIn("missing expected columns", failures[0]["reason"])

    def test_all_rows_read_as_furniture_is_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_csv(tmp, [
                ("", "", "PREVIOUS BALANCE", "100.00"),
                ("", "", "SUB TOTAL", "100.00"),
            ])
            _, rows, failures = parse_cc.parse_csv(path)
        self.assertEqual(rows, [])
        self.assertEqual(len(failures), 1)
        self.assertIn("no transactions parsed", failures[0]["reason"])

    def test_dated_row_with_empty_description_is_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_csv(tmp, [
                ("10 JUN", "10 JUN", "", "25.00"),
            ])
            _, rows, failures = parse_cc.parse_csv(path)
        self.assertEqual(rows, [])
        self.assertEqual(len(failures), 1)
        self.assertIn("empty description", failures[0]["reason"])

    def test_malformed_comma_grouping_is_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_csv(tmp, [
                ("10 JUN", "10 JUN", "REDACTED MERCHANT A", "1,2,3.45"),
            ])
            _, rows, failures = parse_cc.parse_csv(path)
        self.assertEqual(rows, [])
        self.assertEqual(len(failures), 1)
        self.assertIn("no usable amount", failures[0]["reason"])


class StatementCycleDateTests(ParserTestCase):
    def test_same_month_and_adjacent_months(self):
        cycle = parse_cc.date_in_statement_cycle
        self.assertEqual(cycle(2026, 7, 15, 7), "2026-07-15")
        self.assertEqual(cycle(2026, 7, 28, 6), "2026-06-28")
        self.assertEqual(cycle(2026, 1, 30, 12), "2025-12-30")
        self.assertEqual(cycle(2025, 12, 2, 1), "2026-01-02")

    def test_lagged_row_months_stay_in_the_past(self):
        # A late December reversal on a February statement was dated ten
        # months into the future by the old adjacent-Dec/Jan special case.
        cycle = parse_cc.date_in_statement_cycle
        self.assertEqual(cycle(2026, 2, 30, 12), "2025-12-30")
        self.assertEqual(cycle(2026, 3, 15, 11), "2025-11-15")
        self.assertEqual(cycle(2026, 8, 1, 10), "2025-10-01")


class AccountParserTests(ParserTestCase):
    def test_statement_month_never_falls_back_to_the_filename(self):
        reader = FakeReader("No readable statement period")
        self.assertIsNone(
            parse_one.statement_month(reader, "UOB_ONE_2026_06.pdf")
        )

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

    def test_self_transfer_recipient_survives_the_header_name_filter(self):
        # The page-header name is boilerplate, but "Redacted DBS" is the
        # recipient of a transfer to an own account: a filter on the first word
        # of the holder name used to erase it and leave "Unknown counterparty".
        month, rows, overrides, failures, _ = self.parse_account(
            "account_self_transfer_redacted.txt")
        self.assertEqual(failures, [])
        self.assertEqual(len(rows), 1)
        self.assertIn("Redacted DBS", rows[0]["description"])
        self.assertNotIn("HOLDER NAME", rows[0]["description"])

    def test_header_name_is_only_filtered_when_the_identity_supplies_it(self):
        # The name comes from manual/identity.json, so a parse without one must
        # not invent a filter (and must not match every line with an empty
        # alternative in the skip pattern).
        _, rows, _, failures, _ = self.parse_account(
            "account_self_transfer_redacted.txt", identity={})
        self.assertEqual(failures, [])
        self.assertEqual(len(rows), 1)
        self.assertIn("REDACTED HOLDER NAME", rows[0]["description"])
        self.assertIn("Redacted DBS", rows[0]["description"])

    def test_wrapped_payee_that_starts_with_total_is_preserved(self):
        text = (
            "Period: 01 JUN 2026\n"
            "01 JUN BALANCE B/F 1,000.00\n"
            "02 JUN CARD PURCHASE\n"
            "TOTAL WINE MERCHANT\n"
            "10.00 990.00\n"
            "End of Transaction Details\n"
        )
        with patch.object(parse_one, "PdfReader", return_value=FakeReader(text)):
            _, rows, _, failures, _ = parse_one.parse_pdf(
                "UOB_ONE_REDACTED.pdf", TEST_IDENTITY
            )
        self.assertEqual(failures, [])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["description"], "CARD PURCHASE TOTAL WINE MERCHANT")

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
            _, rows, _, failures, _ = parse_one.parse_pdf(
                "UOB_ONE_REDACTED.pdf", TEST_IDENTITY)
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
            _, rows, _, failures, _ = parse_one.parse_pdf(
                "UOB_ONE_REDACTED.pdf", TEST_IDENTITY)
        self.assertEqual(failures, [])
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[2]["description"], "REDACTED TRANSFER")
        self.assertTrue(all("claim against" not in row["description"] for row in rows))

    def test_failed_statement_is_not_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "statements"))
            open(os.path.join(tmp, "statements", "UOB_ONE_2026_06.pdf"), "w").close()
            self.write_identity(tmp)
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
            self.write_identity(tmp)
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

    def test_main_fails_closed_without_an_identity_file(self):
        # Without the holder name nothing crashes: the header line simply stops
        # being filtered and every description that follows one changes. That is
        # worse than an abort, so the run refuses to start.
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "statements"))
            open(os.path.join(tmp, "statements", "UOB_ONE_2026_06.pdf"), "w").close()
            out_path = os.path.join(tmp, "app", "data", "account_transactions.json")
            stdout = io.StringIO()
            with patch.object(parse_one, "REPO_ROOT", tmp), \
                    patch.object(parse_one, "OUT_PATH", out_path), \
                    patch.object(parse_one, "PdfReader",
                                 return_value=FakeReader(
                                     self.fixture("account_statement_redacted.txt"))), \
                    contextlib.redirect_stdout(stdout):
                with self.assertRaises(SystemExit) as raised:
                    parse_one.main()
            self.assertEqual(raised.exception.code, 1)
            self.assertIn("identity.json", stdout.getvalue())
            self.assertIn("statementHolderName", stdout.getvalue())
            self.assertFalse(os.path.exists(out_path))

    def test_main_fails_closed_when_the_identity_has_no_holder_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "statements"))
            open(os.path.join(tmp, "statements", "UOB_ONE_2026_06.pdf"), "w").close()
            self.write_identity(tmp, {"knownAccounts": {}, "statementHolderName": "  "})
            out_path = os.path.join(tmp, "app", "data", "account_transactions.json")
            with patch.object(parse_one, "REPO_ROOT", tmp), \
                    patch.object(parse_one, "OUT_PATH", out_path), \
                    patch.object(parse_one, "PdfReader",
                                 return_value=FakeReader(
                                     self.fixture("account_statement_redacted.txt"))), \
                    contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    parse_one.main()
            self.assertEqual(raised.exception.code, 1)
            self.assertFalse(os.path.exists(out_path))

    def test_load_identity_reads_the_manual_file_and_tolerates_its_absence(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(parse_one, "REPO_ROOT", tmp):
                self.assertEqual(parse_one.load_identity(), {})
                self.write_identity(tmp)
                self.assertEqual(
                    parse_one.load_identity()["statementHolderName"],
                    "REDACTED HOLDER NAME")

    def test_fixed_deposit_accounts_come_from_the_identity(self):
        # The account number is private, so the rule table cannot hold it. With
        # no identity the transfer is just a transfer; with one it is savings.
        description = "Funds Trf - FAST TO 2222222222 OTHR"
        self.assertEqual(parse_one.classify(description), "Transfer")
        self.assertEqual(
            parse_one.classify(description, TEST_IDENTITY), "Fixed deposit")
        # The published rule table itself is never mutated by a parse.
        self.assertNotIn(
            "TO 2222222222",
            dict(parse_one.FLOW_RULES)["Fixed deposit"])
        # The name-independent rules keep working either way.
        self.assertEqual(
            parse_one.classify("UOB FCFD PLACEMENT", TEST_IDENTITY), "Fixed deposit")

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
        self.assertEqual(
            parse_one.classify("Inward DR - GIRO INSU 80445639 SINGAPORE LIFE LTD"),
            "Insurance")
        self.assertEqual(parse_one.classify("PAYNOW-FAST SOMEONE ELSE"), "Transfer")


class WriteOutputTempFileTests(ParserTestCase):
    """The published file is swapped in from a per-process temp file.

    A fixed OUT_PATH + ".tmp" let two concurrent runs interleave writes into one
    name; whatever the outcome, no temp file may survive the call.
    """

    def leftovers(self, directory):
        return [name for name in os.listdir(directory) if name.endswith(".tmp")]

    def test_successful_write_leaves_no_temp_file(self):
        for module in (parse_cc, parse_one):
            with self.subTest(module=module.__name__):
                with tempfile.TemporaryDirectory() as tmp:
                    out_dir = os.path.join(tmp, "app", "data")
                    out_path = os.path.join(out_dir, "out.json")
                    with patch.object(module, "OUT_PATH", out_path):
                        self.assertTrue(module.write_output({"rows": [1, 2]}, True))
                    with open(out_path, encoding="utf-8") as handle:
                        self.assertEqual(json.load(handle), {"rows": [1, 2]})
                    self.assertEqual(self.leftovers(out_dir), [])

    def test_failed_replace_leaves_no_temp_file_and_no_output(self):
        for module in (parse_cc, parse_one):
            with self.subTest(module=module.__name__):
                with tempfile.TemporaryDirectory() as tmp:
                    out_dir = os.path.join(tmp, "app", "data")
                    out_path = os.path.join(out_dir, "out.json")
                    with patch.object(module, "OUT_PATH", out_path), \
                            patch.object(module.os, "replace",
                                         side_effect=PermissionError("locked")), \
                            patch.object(module.time, "sleep"):
                        with self.assertRaises(PermissionError):
                            module.write_output({"rows": [1]}, True)
                    self.assertFalse(os.path.exists(out_path))
                    self.assertEqual(self.leftovers(out_dir), [])

    def test_temp_file_name_is_unique_per_call(self):
        # Two runs writing at once must not share one temp name.
        for module in (parse_cc, parse_one):
            with self.subTest(module=module.__name__):
                with tempfile.TemporaryDirectory() as tmp:
                    out_dir = os.path.join(tmp, "app", "data")
                    out_path = os.path.join(out_dir, "out.json")
                    seen = []
                    real_replace = os.replace

                    def capture(src, dst, _seen=seen):
                        _seen.append(os.path.basename(src))
                        return real_replace(src, dst)

                    with patch.object(module, "OUT_PATH", out_path), \
                            patch.object(module.os, "replace", side_effect=capture):
                        module.write_output({"rows": [1]}, True)
                        module.write_output({"rows": [2]}, True)
                    self.assertEqual(len(set(seen)), 2)
                    for name in seen:
                        self.assertTrue(name.startswith("out.json."))
                        self.assertTrue(name.endswith(".tmp"))

    def test_failed_run_still_writes_nothing(self):
        for module in (parse_cc, parse_one):
            with self.subTest(module=module.__name__):
                with tempfile.TemporaryDirectory() as tmp:
                    out_path = os.path.join(tmp, "app", "data", "out.json")
                    with patch.object(module, "OUT_PATH", out_path):
                        self.assertFalse(module.write_output({"rows": [1]}, False))
                    self.assertFalse(os.path.exists(os.path.dirname(out_path)))


if __name__ == "__main__":
    unittest.main()
