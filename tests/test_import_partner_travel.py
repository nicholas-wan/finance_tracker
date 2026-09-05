import importlib
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import_partner_travel = importlib.import_module("import_partner_travel")


# Synthetic rows only: no real merchants, ids or amounts.
def row(tx_id, **overrides):
    base = {
        "id": tx_id,
        "date": "2026-03-18",
        "postedDate": "2026-03-19",
        "month": "2026-03",
        "description": "SAMPLE MERCHANT",
        "amount": 12.5,
        "type": "debit",
        "category": "Travel",
        "owner": "Shared",
        "card": "SAMPLE CARD",
        "provenance": {"sourceType": "card-pdf"},
    }
    base.update(overrides)
    return base


class SelectionTests(unittest.TestCase):
    def test_travel_rows_and_foreign_everyday_rows_qualify(self):
        rows = [
            row("tx_travel"),
            row("tx_dinner", category="Food & dining", foreign="CNY 69.00"),
            row("tx_game", category="Games", foreign="JPY 610.00"),
            row("tx_topup", category="Wallet funding", foreign="MYR 5.00"),
            row("tx_local", category="Food & dining"),
            row("tx_bill", category="Travel", type="payment"),
        ]
        chosen = import_partner_travel.select_charges(rows)
        self.assertEqual([(r["id"], reason) for r, reason in chosen],
                         [("tx_travel", "travel"), ("tx_dinner", "foreign-currency")])

    def test_copied_charge_is_prefixed_and_keeps_only_dashboard_fields(self):
        record = import_partner_travel.partner_charge(
            row("tx_travel", displayName="Nice hotel"), "travel", "Nic")
        self.assertEqual(record["id"], "nic_tx_travel")
        self.assertEqual(record["sourceId"], "tx_travel")
        self.assertEqual(record["displayName"], "Nice hotel")
        self.assertEqual(record["ownerTag"], "Shared")
        self.assertEqual(record["reason"], "travel")
        self.assertNotIn("provenance", record)
        self.assertNotIn("owner", record)
        self.assertNotIn("foreign", record)

    def test_rows_missing_essentials_fail_closed(self):
        with self.assertRaisesRegex(SystemExit, "no id"):
            import_partner_travel.partner_charge(row(""), "travel", "Nic")
        with self.assertRaisesRegex(SystemExit, "invalid amount"):
            import_partner_travel.partner_charge(row("tx_x", amount="12"), "travel", "Nic")
        with self.assertRaisesRegex(SystemExit, "no date"):
            import_partner_travel.partner_charge(row("tx_x", date=""), "travel", "Nic")


class ManifestTests(unittest.TestCase):
    def test_manifest_sorts_by_date_and_records_the_source(self):
        source = {
            "generationId": "generation_abc",
            "generatedAt": "2026-09-05 00:42",
            "transactions": [
                row("tx_b", date="2026-03-20"),
                row("tx_a", date="2026-03-18"),
            ],
        }
        manifest = import_partner_travel.build_manifest(
            source, Path("other/app/data/transactions.json"), "Nic", today=date(2026, 9, 5))
        self.assertEqual([c["id"] for c in manifest["charges"]], ["nic_tx_a", "nic_tx_b"])
        self.assertEqual(manifest["paidBy"], "Nic")
        self.assertEqual(manifest["source"]["generationId"], "generation_abc")
        self.assertEqual(manifest["source"]["importedAt"], "2026-09-05")

    def test_source_without_transactions_fails_closed(self):
        with self.assertRaisesRegex(SystemExit, "no transactions list"):
            import_partner_travel.build_manifest({"months": []}, Path("x.json"), "Nic")

    def test_main_writes_the_manifest_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            source_path = Path(folder) / "transactions.json"
            output_path = Path(folder) / "manual" / "partner_travel.json"
            source_path.write_text(json.dumps({
                "generationId": "generation_abc",
                "transactions": [row("tx_a"), row("tx_g", category="Games", foreign="JPY 1.00")],
            }), encoding="utf-8")
            import_partner_travel.main([str(source_path), "--output", str(output_path)])
            first = output_path.read_text(encoding="utf-8")
            data = json.loads(first)
            self.assertEqual([c["id"] for c in data["charges"]], ["nic_tx_a"])
            import_partner_travel.main([str(source_path), "--output", str(output_path)])
            self.assertEqual(output_path.read_text(encoding="utf-8"), first)


if __name__ == "__main__":
    unittest.main()
