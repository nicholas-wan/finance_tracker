"""The dashboard reads app/data/transactions.json, never manual/.

Private identity - the own-account labels and the counterparties that are known
not to be strangers - is hand-maintained in the git-ignored manual/identity.json
and reaches the browser only through the copy build_data.py embeds. These tests
pin what is copied, and just as importantly what is not: the holder's legal name
and the fixed-deposit account numbers must never leave manual/.
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
sys.path.insert(0, str(ROOT / "scripts"))

build_data = importlib.import_module("build_data")

IDENTITY = {
    "_comment": "fabricated",
    "statementHolderName": "REDACTED HOLDER NAME",
    "knownAccounts": {"1111111111": "Redacted Savings A/c"},
    "fixedDepositAccounts": ["2222222222"],
    "trustedCounterparties": ["Redacted Person"],
}


def card_payload():
    return {
        "months": ["2026-06"],
        "sourceFiles": {"2026-06": "SYNTH_CC_2026_06.pdf"},
        "quality": {"pdfMonths": ["2026-06"]},
        "transactions": [{
            "id": "tx_synth0000000000001",
            "date": "2026-06-10",
            "postedDate": "2026-06-11",
            "month": "2026-06",
            "card": "UOB ONE CARD",
            "description": "SYNTH GROCER",
            "amount": 40.0,
            "credit": False,
            "foreign": None,
            "provenance": {
                "sourceType": "card-pdf",
                "sourceFile": "SYNTH_CC_2026_06.pdf",
                "statementMonth": "2026-06",
                "section": "UOB ONE CARD",
                "occurrence": 1,
                "verified": True,
                "page": 1,
                "line": 21,
            },
        }],
    }


class IdentityEmbeddingTests(unittest.TestCase):
    def build(self, identity=None):
        """Run build_data.main() in a throwaway tree and return its output."""
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = os.path.join(tmp, "app", "data")
            manual_dir = os.path.join(tmp, "manual")
            os.makedirs(data_dir)
            os.makedirs(manual_dir)
            cards_path = os.path.join(data_dir, "card_transactions.json")
            out_path = os.path.join(data_dir, "transactions.json")
            with open(cards_path, "w", encoding="utf-8") as handle:
                json.dump(card_payload(), handle)
            if identity is not None:
                with open(os.path.join(manual_dir, "identity.json"), "w",
                          encoding="utf-8") as handle:
                    json.dump(identity, handle)
            with patch.object(build_data, "DATA_DIR", data_dir), \
                    patch.object(build_data, "MANUAL_DIR", manual_dir), \
                    patch.object(build_data, "CARDS_PATH", cards_path), \
                    patch.object(build_data, "ACCOUNT_PATH",
                                 os.path.join(data_dir, "account_transactions.json")), \
                    patch.object(build_data, "OUT_PATH", out_path), \
                    contextlib.redirect_stdout(io.StringIO()):
                build_data.main()
            with open(out_path, encoding="utf-8") as handle:
                return json.load(handle)

    def test_known_accounts_and_trusted_counterparties_are_published(self):
        output = self.build(IDENTITY)
        self.assertEqual(output["identity"], {
            "knownAccounts": {"1111111111": "Redacted Savings A/c"},
            "trustedCounterparties": ["Redacted Person"],
        })
        self.assertEqual(output["transactions"][0]["merchantKey"], "SYNTH GROCER")

    def test_the_holder_name_and_deposit_accounts_stay_out_of_the_dashboard(self):
        # app/data/ is regenerated, copied and shared far more casually than
        # manual/, so it carries only what the dashboard actually renders.
        output = self.build(IDENTITY)
        self.assertNotIn("statementHolderName", output["identity"])
        self.assertNotIn("fixedDepositAccounts", output["identity"])
        serialized = json.dumps(output)
        self.assertNotIn("REDACTED HOLDER NAME", serialized)
        self.assertNotIn("2222222222", serialized)

    def test_a_missing_identity_file_builds_an_empty_identity(self):
        # A fresh clone has no manual/identity.json. The build still runs; only
        # parse_one.py, which cannot be correct without it, refuses.
        output = self.build(None)
        self.assertEqual(output["identity"],
                         {"knownAccounts": {}, "trustedCounterparties": []})

    def test_python_merchant_keys_match_the_browser_contract(self):
        samples = {
            "Grab* GPC-71541451439149cSINGAPORE": "GRAB",
            "Grab* ab2d5d37108809ef Singapore": "GRAB",
            "GRAB RIDES-EC PETALING JAYA": "GRAB",
            "SUBSCRIPTIONGRAB* GPC-A1B2C3": "GRAB SUBSCRIPTION",
            "NTUC FP-YISHUN MRT SINGAPORE": "NTUC FAIRPRICE",
            "NORTHFIELD BAKERY SINGAPO": "NORTHFIELD BAKERY",
            "HARBOUR DELI J J": "HARBOUR DELI",
            "SINGAPO": "SINGAPO",
        }
        for description, expected in samples.items():
            with self.subTest(description=description):
                self.assertEqual(build_data.merchant_key(description), expected)

    def test_category_rule_overlaps_are_enumerated_in_precedence_order(self):
        self.assertEqual(
            build_data.category_matches("GIANT HOTEL"),
            ["Groceries", "Travel"],
        )
        self.assertEqual(build_data.categorize("GIANT HOTEL"), "Groceries")
        self.assertFalse(build_data.has_category_override({}, "tx_one"))
        self.assertFalse(build_data.has_category_override(
            {"tx_one": {"displayName": "Giant"}}, "tx_one"))
        self.assertTrue(build_data.has_category_override(
            {"tx_one": {"category": "Groceries"}}, "tx_one"))


if __name__ == "__main__":
    unittest.main()
