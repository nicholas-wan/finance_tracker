import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import_trip_bookings = importlib.import_module("import_trip_bookings")


HEADER = (
    "Booking status",
    "Product Type",
    "Booking No. ",  # the real export ships a trailing space here
    "Booking Date",
    "Product name",
    "Travel time",
    "Traveller",
    "Currency",
    "Amount",
)

# Synthetic rows only: no real booking numbers, names or products appear here.
def row(booking_no, status="Completed", amount=10.0, product="Sample Stay", currency="sgd"):
    return [
        status,
        "Hotels",
        booking_no,
        "January 1, 2020",
        product,
        "Jan 1, 2020",
        "Test Traveller",
        currency,
        amount,
    ]


def make_workbook(path, rows, header=HEADER, sheet_name="My Bookings"):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_name
    sheet.append(list(header))
    for entry in rows:
        sheet.append(list(entry))
    workbook.save(path)
    return Path(path)


class ReadWorkbookTests(unittest.TestCase):
    def test_header_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            bad_header = list(HEADER)
            bad_header[3] = "Booked On"
            path = make_workbook(Path(directory) / "wrong.xlsx", [row("1")], header=bad_header)
            with self.assertRaises(SystemExit) as caught:
                import_trip_bookings.read_workbook(path)
            self.assertIn("wrong.xlsx", str(caught.exception))

    def test_numeric_booking_number_is_normalised(self):
        with tempfile.TemporaryDirectory() as directory:
            path = make_workbook(
                Path(directory) / "numeric.xlsx",
                [row(1234567890123), row(9876543210.0), row("  55  ")],
            )
            records = import_trip_bookings.read_workbook(path)
            self.assertEqual(
                [record["bookingNo"] for record in records],
                ["1234567890123", "9876543210", "55"],
            )

    def test_blank_rows_are_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            path = make_workbook(
                Path(directory) / "gaps.xlsx",
                [row("1"), [None] * 9, ["   "] + [None] * 8, row("2")],
            )
            records = import_trip_bookings.read_workbook(path)
            self.assertEqual([record["bookingNo"] for record in records], ["1", "2"])

    def test_amount_int_preserved_and_float_rounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = make_workbook(
                Path(directory) / "amounts.xlsx",
                [row("1", amount=250), row("2", amount=12.3456), row("3", amount=None)],
            )
            records = import_trip_bookings.read_workbook(path)
            self.assertIsInstance(records[0]["amount"], int)
            self.assertEqual(records[0]["amount"], 250)
            self.assertEqual(records[1]["amount"], 12.35)
            self.assertIsNone(records[2]["amount"])

    def test_non_numeric_amount_names_file_and_row(self):
        with tempfile.TemporaryDirectory() as directory:
            path = make_workbook(
                Path(directory) / "broken.xlsx", [row("1"), row("2", amount="free")]
            )
            with self.assertRaises(SystemExit) as caught:
                import_trip_bookings.read_workbook(path)
            message = str(caught.exception)
            self.assertIn("broken.xlsx", message)
            self.assertIn("row 3", message)

    def test_currency_is_upper_cased_and_strings_stripped(self):
        with tempfile.TemporaryDirectory() as directory:
            path = make_workbook(
                Path(directory) / "space.xlsx",
                [row("1", product="  Padded Stay\nSecond line  ", currency=" eur ")],
            )
            record = import_trip_bookings.read_workbook(path)[0]
            self.assertEqual(record["currency"], "EUR")
            self.assertEqual(record["productName"], "Padded Stay\nSecond line")


class MergeBookingsTests(unittest.TestCase):
    def test_later_file_wins_and_keeps_first_seen_position(self):
        first = [
            {"bookingNo": "1", "status": "Confirmed", "amount": 10, "sourceFile": "a.xlsx"},
            {"bookingNo": "2", "status": "Confirmed", "amount": 20, "sourceFile": "a.xlsx"},
        ]
        second = [
            {"bookingNo": "2", "status": "Cancelled", "amount": 25, "sourceFile": "b.xlsx"},
            {"bookingNo": "3", "status": "Confirmed", "amount": 30, "sourceFile": "b.xlsx"},
        ]
        merged = import_trip_bookings.merge_bookings(
            {"a.xlsx": first, "b.xlsx": second}
        )
        self.assertEqual([record["bookingNo"] for record in merged], ["1", "2", "3"])
        by_no = {record["bookingNo"]: record for record in merged}
        self.assertEqual(by_no["2"]["status"], "Cancelled")
        self.assertEqual(by_no["2"]["amount"], 25)
        self.assertEqual(by_no["2"]["sourceFile"], "b.xlsx")
        self.assertEqual(by_no["1"]["sourceFile"], "a.xlsx")

    def test_conflicting_fields_are_counted(self):
        import collections

        updates = collections.Counter()
        import_trip_bookings.merge_bookings(
            {
                "a.xlsx": [{"bookingNo": "1", "status": "Confirmed", "amount": 10}],
                "b.xlsx": [{"bookingNo": "1", "status": "Cancelled", "amount": 11}],
            },
            updates=updates,
        )
        self.assertEqual(updates["__bookings__"], 1)
        self.assertEqual(updates["status"], 1)
        self.assertEqual(updates["amount"], 1)


class CliTests(unittest.TestCase):
    def build_pair(self, directory):
        directory = Path(directory)
        first = make_workbook(
            directory / "Export.xlsx", [row("11"), row("22", status="Confirmed")]
        )
        second = make_workbook(
            directory / "Export (1).xlsx",
            [row("22", status="Cancelled", amount=99.999), row("33")],
        )
        return first, second

    def test_writes_merged_output_with_later_file_winning(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = self.build_pair(directory)
            output = Path(directory) / "nested" / "trip.json"
            code = import_trip_bookings.main(
                [str(first), str(second), "--output", str(output),
                 "--account-holder", "Synthetic Holder", "--imported-at", "2020-01-01"]
            )
            self.assertEqual(code, 0)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["sourceFiles"], ["Export.xlsx", "Export (1).xlsx"])
            self.assertEqual(payload["importedAt"], "2020-01-01")
            self.assertEqual(payload["accountHolder"], "Synthetic Holder")
            self.assertEqual(
                [record["bookingNo"] for record in payload["bookings"]], ["11", "22", "33"]
            )
            duplicate = payload["bookings"][1]
            self.assertEqual(duplicate["status"], "Cancelled")
            self.assertEqual(duplicate["amount"], 100.0)
            self.assertEqual(duplicate["sourceFile"], "Export (1).xlsx")
            self.assertTrue(output.read_text(encoding="utf-8").endswith("}\n"))

    def test_check_returns_zero_when_identical_and_one_when_different(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = self.build_pair(directory)
            output = Path(directory) / "trip.json"
            argv = [str(first), str(second), "--output", str(output),
                    "--account-holder", "Synthetic Holder", "--imported-at", "2020-01-01"]
            self.assertEqual(import_trip_bookings.main(argv), 0)

            # A different importedAt and accountHolder must not count as a change.
            self.assertEqual(
                import_trip_bookings.main(
                    [str(first), str(second), "--output", str(output),
                     "--account-holder", "Someone Else", "--imported-at", "2021-02-03", "--check"]
                ),
                0,
            )

            payload = json.loads(output.read_text(encoding="utf-8"))
            payload["bookings"][0]["status"] = "Used"
            output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            self.assertEqual(import_trip_bookings.main(argv + ["--check"]), 1)

    def test_check_does_not_write(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = self.build_pair(directory)
            output = Path(directory) / "absent.json"
            self.assertEqual(
                import_trip_bookings.main(
                    [str(first), str(second), "--output", str(output), "--check"]
                ),
                1,
            )
            self.assertFalse(output.exists())

    def test_directory_argument_expands_sorted_by_name(self):
        with tempfile.TemporaryDirectory() as directory:
            inbox = Path(directory) / "inbox"
            inbox.mkdir()
            make_workbook(inbox / "b.xlsx", [row("22", status="Cancelled")])
            make_workbook(inbox / "a.xlsx", [row("11"), row("22", status="Confirmed")])
            (inbox / "notes.txt").write_text("ignored", encoding="utf-8")

            paths = import_trip_bookings.expand_inputs([str(inbox)])
            self.assertEqual([path.name for path in paths], ["a.xlsx", "b.xlsx"])

            output = Path(directory) / "trip.json"
            self.assertEqual(
                import_trip_bookings.main(
                    [str(inbox), "--output", str(output), "--imported-at", "2020-01-01"]
                ),
                0,
            )
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["sourceFiles"], ["a.xlsx", "b.xlsx"])
            self.assertEqual(
                [record["bookingNo"] for record in payload["bookings"]], ["11", "22"]
            )
            # b.xlsx sorts last, so it wins the duplicate.
            self.assertEqual(payload["bookings"][1]["sourceFile"], "b.xlsx")
            self.assertEqual(payload["bookings"][1]["status"], "Cancelled")


if __name__ == "__main__":
    unittest.main()
