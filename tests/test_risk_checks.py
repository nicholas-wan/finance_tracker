import importlib
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
risk_checks = importlib.import_module("risk_checks")


def merchant_key(description):
    return description.upper().split(" GPC-", 1)[0]


def transaction(tx_id, amount, tx_type="debit", date="2026-01-01",
                description="MERCHANT", category="Shopping", foreign=None):
    row = {
        "id": tx_id,
        "date": date,
        "month": date[:7],
        "card": "TEST CARD",
        "description": description,
        "amount": amount,
        "type": tx_type,
        "category": category,
    }
    if foreign:
        row["foreign"] = foreign
    return row


class RiskCheckTests(unittest.TestCase):
    def test_flags_unreversed_same_day_duplicate(self):
        rows = [
            transaction("tx_a", 70.40, description="CAFE"),
            transaction("tx_b", 70.40, description="CAFE"),
        ]
        result = risk_checks.detect_risks(rows, merchant_key)
        self.assertEqual(result["count"], 1)
        self.assertIn("identical", rows[0]["risk"]["reasons"][0])

    def test_refunds_clear_duplicate_before_checking(self):
        rows = [
            transaction("tx_a", 960, description="SHOP"),
            transaction("tx_b", 960, description="SHOP"),
            transaction("tx_c", 960, description="SHOP"),
            transaction("tx_d", 813.58, "refund", description="SHOP"),
            transaction("tx_e", 813.58, "refund", description="SHOP"),
            transaction("tx_f", 146.42, "refund", description="SHOP"),
            transaction("tx_g", 146.42, "refund", description="SHOP"),
        ]
        result = risk_checks.detect_risks(rows, merchant_key)
        self.assertEqual(result["count"], 0)

    def test_flags_large_merchant_outlier_and_can_recognize_group(self):
        rows = [
            transaction("tx_%02d" % index, 10, date="2025-%02d-01" % index,
                        description="GRAB GPC-%02d" % index)
            for index in range(1, 11)
        ]
        rows.append(transaction("tx_big", 1000, description="GRAB GPC-BIG"))
        result = risk_checks.detect_risks(rows, merchant_key)
        self.assertEqual(result["high"], 1)
        group_ids = result["signals"][0]["risk"]["groupIds"]
        recognized = risk_checks.detect_risks(rows, merchant_key, group_ids)
        self.assertEqual(recognized["count"], 0)
        self.assertEqual(recognized["recognized"], 1)

    def test_ignores_small_repeats(self):
        rows = [
            transaction("tx_a", 6),
            transaction("tx_b", 6),
            transaction("tx_c", 6),
        ]
        self.assertEqual(
            risk_checks.detect_risks(rows, merchant_key)["count"],
            0,
        )

    def test_flags_first_observed_high_value_merchant(self):
        rows = [transaction("tx_new", 1800, description="NEW MERCHANT")]
        result = risk_checks.detect_risks(rows, merchant_key)
        self.assertEqual(result["count"], 1)
        self.assertIn(
            "first-observed-high-value",
            rows[0]["risk"]["checks"],
        )

    def test_flags_concentrated_same_day_burst(self):
        rows = [
            transaction("tx_%d" % index, 70, description="BURST MERCHANT")
            for index in range(5)
        ]
        result = risk_checks.detect_risks(rows, merchant_key)
        self.assertEqual(result["count"], 1)
        self.assertIn("same-day-burst", rows[0]["risk"]["checks"])

    def test_flags_stable_subscription_price_jump(self):
        rows = [
            transaction(
                "tx_%d" % index,
                20,
                date="2025-%02d-01" % index,
                description="VIDEO PLAN",
                category="Subscriptions",
            )
            for index in range(1, 5)
        ]
        rows.append(transaction(
            "tx_jump",
            28,
            date="2025-05-01",
            description="VIDEO PLAN",
            category="Subscriptions",
        ))
        result = risk_checks.detect_risks(rows, merchant_key)
        self.assertEqual(result["count"], 1)
        self.assertIn(
            "subscription-price-jump",
            rows[-1]["risk"]["checks"],
        )

    def test_flags_first_material_foreign_currency_use(self):
        rows = [
            transaction(
                "tx_foreign",
                240,
                description="SPORTS SHOP",
                foreign="MYR 750.00",
            )
        ]
        result = risk_checks.detect_risks(rows, merchant_key)
        self.assertEqual(result["count"], 1)
        self.assertIn(
            "first-foreign-currency-use",
            rows[0]["risk"]["checks"],
        )

    def test_ignores_expected_travel_foreign_currency_use(self):
        rows = [
            transaction(
                "tx_foreign",
                240,
                description="FERRY COMPANY",
                category="Travel",
                foreign="AUD 260.00",
            )
        ]
        self.assertEqual(
            risk_checks.detect_risks(rows, merchant_key)["count"],
            0,
        )


if __name__ == "__main__":
    unittest.main()
