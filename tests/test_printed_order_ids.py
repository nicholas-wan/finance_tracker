import importlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
data_ids = importlib.import_module("data_ids")


def rows():
    base = {"month": "2026-06", "date": "2026-06-10", "postedDate": "2026-06-11",
            "card": "TEST CARD", "description": "ACTIVESG BOOKING", "amount": 6.0}
    return [dict(base, _sourcePage=2, _sourceLine=40), dict(base, _sourcePage=1, _sourceLine=12)]


class PrintedOrderOccurrenceTests(unittest.TestCase):
    """Identical rows are numbered by where they are printed, not by the order
    a caller hands them over."""

    def test_occurrence_follows_page_and_line(self):
        reordered = rows()
        data_ids.assign_provenance(reordered, "card-pdf", "x.pdf")
        self.assertEqual([r["provenance"]["occurrence"] for r in reordered], [2, 1])
        ordered = list(reversed(rows()))
        data_ids.assign_provenance(ordered, "card-pdf", "x.pdf")
        self.assertEqual([r["provenance"]["occurrence"] for r in ordered], [1, 2])
        self.assertEqual(sorted(r["id"] for r in reordered), sorted(r["id"] for r in ordered))
        self.assertEqual(reordered[1]["id"], ordered[0]["id"])
        self.assertEqual(reordered[1]["provenance"]["page"], 1)

    def test_rows_without_coordinates_keep_arrival_order(self):
        plain = rows()
        for r in plain:
            r.pop("_sourcePage")
            r.pop("_sourceLine")
        data_ids.assign_provenance(plain, "card-pdf", "x.pdf")
        self.assertEqual([r["provenance"]["occurrence"] for r in plain], [1, 2])
        self.assertNotIn("page", plain[0]["provenance"])


if __name__ == "__main__":
    unittest.main()
