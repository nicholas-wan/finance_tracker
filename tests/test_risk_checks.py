import importlib
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
risk_checks = importlib.import_module("risk_checks")
build_data = importlib.import_module("build_data")

# The checks must see merchants exactly as the rest of the build sees them, so
# the tests use the real key rather than a stand-in that hides descriptor noise.
merchant_key = build_data.merchant_key


def transaction(tx_id, amount, tx_type="debit", date="2026-01-01",
                description="MERCHANT", category="Shopping", foreign=None,
                rule_category=None, card="TEST CARD"):
    row = {
        "id": tx_id,
        "date": date,
        "month": date[:7],
        "card": card,
        "description": description,
        "amount": amount,
        "type": tx_type,
        "category": category,
        "ruleCategory": rule_category or category,
    }
    if foreign:
        row["foreign"] = foreign
    return row


def signals(rows, recognized=None):
    return risk_checks.detect_risks(rows, merchant_key, recognized)


def checks_of(result):
    return sorted(
        check for signal in result["signals"] for check in signal["risk"]["checks"]
    )


class RiskCheckTests(unittest.TestCase):
    def test_flags_unreversed_same_day_duplicate(self):
        rows = [
            transaction("tx_a", 70.40, description="CAFE"),
            transaction("tx_b", 70.40, description="CAFE"),
        ]
        result = signals(rows)
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
        self.assertEqual(signals(rows)["count"], 0)

    def test_earlier_credit_cannot_net_a_later_charge(self):
        # A refund reverses an earlier charge. The old abs() window let this
        # credit, four days BEFORE the charges, erase their duplicate signal -
        # a temporally impossible reversal.
        rows = [
            transaction("tx_r", 960, "refund", date="2026-01-01", description="SHOP"),
            transaction("tx_a", 960, date="2026-01-05", description="SHOP"),
            transaction("tx_b", 960, date="2026-01-05", description="SHOP"),
        ]
        self.assertEqual(signals(rows)["count"], 1)

    def test_later_refund_within_a_week_still_nets(self):
        rows = [
            transaction("tx_a", 960, date="2026-01-05", description="SHOP"),
            transaction("tx_b", 960, date="2026-01-05", description="SHOP"),
            transaction("tx_r", 960, "refund", date="2026-01-10", description="SHOP"),
        ]
        self.assertEqual(signals(rows)["count"], 0)

    def test_ignores_small_repeats(self):
        rows = [
            transaction("tx_a", 6),
            transaction("tx_b", 6),
            transaction("tx_c", 6),
        ]
        self.assertEqual(signals(rows)["count"], 0)

    def test_flags_first_observed_high_value_merchant(self):
        rows = [transaction("tx_new", 1800, description="NEW MERCHANT")]
        result = signals(rows)
        self.assertEqual(result["count"], 1)
        self.assertIn("first-observed-high-value", rows[0]["risk"]["checks"])

    def test_flags_concentrated_same_day_burst(self):
        rows = [
            transaction("tx_%d" % index, 70, description="BURST MERCHANT")
            for index in range(5)
        ]
        result = signals(rows)
        self.assertEqual(result["count"], 1)
        self.assertIn("same-day-burst", rows[0]["risk"]["checks"])

    def test_flags_stable_subscription_price_jump(self):
        rows = [
            transaction("tx_%d" % index, 20, date="2025-%02d-01" % index,
                        description="VIDEO PLAN", category="Subscriptions")
            for index in range(1, 5)
        ]
        rows.append(transaction("tx_jump", 28, date="2025-05-01",
                                description="VIDEO PLAN", category="Subscriptions"))
        result = signals(rows)
        self.assertEqual(result["count"], 1)
        self.assertIn("subscription-price-jump", rows[-1]["risk"]["checks"])

    def test_ignores_expected_travel_foreign_currency_use(self):
        rows = [
            transaction("tx_foreign", 240, description="FERRY COMPANY",
                        category="Travel", foreign="AUD 260.00")
        ]
        self.assertEqual(signals(rows)["count"], 0)


class RecognitionIsPerSignalTests(unittest.TestCase):
    """Fix 1: acknowledging one check must not blanket-suppress future ones."""

    def duplicate_pair(self, amount):
        return [
            transaction("tx_a", amount, date="2026-02-02", description="CAFE"),
            transaction("tx_b", amount, date="2026-02-02", description="CAFE"),
        ]

    def test_recognizing_a_signal_suppresses_that_signal(self):
        rows = self.duplicate_pair(70.40)
        key = signals(rows)["signals"][0]["risk"]["key"]
        again = signals(self.duplicate_pair(70.40), [key])
        self.assertEqual(again["count"], 0)
        self.assertEqual(again["recognized"], 1)

    def test_stored_entry_dicts_are_accepted(self):
        rows = self.duplicate_pair(70.40)
        first = signals(rows)["signals"][0]["risk"]
        entry = {"key": first["key"], "ids": first["groupIds"],
                 "checks": first["checks"], "recognizedAt": "2026-02-02T00:00:00+08:00"}
        self.assertEqual(signals(self.duplicate_pair(70.40), [entry])["count"], 0)

    def test_new_reason_on_recognized_rows_resurfaces(self):
        # The same two transaction IDs, acknowledged while they only tripped the
        # duplicate check. A corrected amount makes them trip a second check.
        acknowledged = signals(self.duplicate_pair(70.40))["signals"][0]["risk"]
        self.assertEqual(acknowledged["checks"], ["same-day-duplicate"])

        larger = self.duplicate_pair(1600)
        result = signals(larger, [acknowledged["key"]])
        risk = result["signals"][0]["risk"]

        self.assertEqual(risk["groupIds"], acknowledged["groupIds"])
        self.assertIn("first-observed-high-value", risk["checks"])
        self.assertFalse(risk["recognized"])
        self.assertEqual(result["count"], 1)

    def test_signal_key_ignores_row_and_check_ordering(self):
        self.assertEqual(
            risk_checks.signal_key(["tx_b", "tx_a"], ["b-check", "a-check"]),
            risk_checks.signal_key(["tx_a", "tx_b"], ["a-check", "b-check"]),
        )

    def test_signal_key_changes_when_checks_change(self):
        self.assertNotEqual(
            risk_checks.signal_key(["tx_a"], ["same-day-duplicate"]),
            risk_checks.signal_key(
                ["tx_a"], ["same-day-duplicate", "first-observed-high-value"]),
        )


class RefundNettingTests(unittest.TestCase):
    """Fixes 2 and 3."""

    def test_delayed_refund_nets_against_the_earlier_duplicate(self):
        rows = [
            transaction("tx_a", 960, date="2026-01-01", description="SHOP"),
            transaction("tx_b", 960, date="2026-01-01", description="SHOP"),
            transaction("tx_r", 960, "refund", date="2026-01-04", description="SHOP"),
        ]
        result = signals(rows)
        self.assertNotIn("same-day-duplicate", checks_of(result))

    def test_refund_beyond_the_window_does_not_net(self):
        rows = [
            transaction("tx_a", 960, date="2026-01-01", description="SHOP"),
            transaction("tx_b", 960, date="2026-01-01", description="SHOP"),
            transaction("tx_r", 960, "refund", date="2026-01-20", description="SHOP"),
        ]
        self.assertIn("same-day-duplicate", checks_of(signals(rows)))

    def test_delayed_refund_matches_on_merchant_not_descriptor(self):
        rows = [
            transaction("tx_a", 960, date="2026-01-01",
                        description="GRAB* RIDE GPC-A1"),
            transaction("tx_b", 960, date="2026-01-01",
                        description="GRAB* RIDE GPC-B2"),
            transaction("tx_r", 960, "refund", date="2026-01-03",
                        description="GRAB* RIDE GPC-C3"),
        ]
        self.assertNotIn("same-day-duplicate", checks_of(signals(rows)))

    def test_one_refund_does_not_cancel_unrelated_amount_buckets(self):
        rows = [
            transaction("tx_a", 100, description="SHOP"),
            transaction("tx_b", 100, description="SHOP"),
            transaction("tx_c", 300, description="SHOP"),
            transaction("tx_d", 300, description="SHOP"),
            transaction("tx_r", 300, "refund", description="SHOP"),
        ]
        result = signals(rows)
        reasons = result["signals"][0]["risk"]["reasons"]
        self.assertEqual(len(reasons), 1)
        self.assertIn("2 identical S$100.00", reasons[0])

    def test_same_day_reversal_outranks_a_neighbouring_day(self):
        # The S$500 credit belongs to the S$500 charge it lands beside, not to
        # the earlier day that merely came first.
        rows = [
            transaction("tx_early", 480, date="2026-01-01", description="SHOP"),
            transaction("tx_late", 500, date="2026-01-03", description="SHOP"),
            transaction("tx_r", 500, "refund", date="2026-01-03", description="SHOP"),
        ]
        signals(rows)
        self.assertIsNone(rows[1].get("risk"))
        self.assertEqual(rows[0]["amount"], 480)

    def test_small_leftover_credits_do_not_drift_across_days(self):
        rows = [
            transaction("tx_a", 70.40, date="2026-01-01", description="CAFE"),
            transaction("tx_b", 70.40, date="2026-01-01", description="CAFE"),
            transaction("tx_c", 5, date="2026-01-03", description="CAFE"),
            transaction("tx_r", 10, "refund", date="2026-01-03", description="CAFE"),
        ]
        self.assertIn("same-day-duplicate", checks_of(signals(rows)))


class DuplicateGroupingTests(unittest.TestCase):
    """Fix 4: group on merchant, not on the raw descriptor and card."""

    def test_gpc_suffix_variants_group_together(self):
        rows = [
            transaction("tx_a", 70.40, description="GRAB* RIDE GPC-A1"),
            transaction("tx_b", 70.40, description="GRAB* RIDE GPC-B2"),
        ]
        result = signals(rows)
        self.assertEqual(result["count"], 1)
        self.assertIn("same-day-duplicate", result["signals"][0]["risk"]["checks"])
        self.assertEqual(len(result["signals"][0]["risk"]["groupIds"]), 2)

    def test_same_merchant_on_two_cards_groups_together(self):
        rows = [
            transaction("tx_a", 70.40, description="CAFE", card="UOB ONE CARD"),
            transaction("tx_b", 70.40, description="CAFE", card="UOB LADY CARD"),
        ]
        result = signals(rows)
        self.assertEqual(result["count"], 1)
        self.assertEqual(len(result["signals"][0]["risk"]["groupIds"]), 2)

    def test_different_merchants_stay_apart(self):
        rows = [
            transaction("tx_a", 70.40, description="CAFE ONE"),
            transaction("tx_b", 70.40, description="BAKERY TWO"),
        ]
        self.assertEqual(signals(rows)["count"], 0)


class CategoryOverrideTests(unittest.TestCase):
    """Fix 5: exclusions read the rule category, not the user's relabelling."""

    def test_override_to_an_excluded_category_keeps_the_signal(self):
        rows = [
            transaction("tx_a", 70.40, description="CAFE",
                        category="Insurance", rule_category="Food & dining"),
            transaction("tx_b", 70.40, description="CAFE",
                        category="Insurance", rule_category="Food & dining"),
        ]
        self.assertEqual(signals(rows)["count"], 1)

    def test_genuinely_excluded_rule_category_is_still_skipped(self):
        rows = [
            transaction("tx_a", 70.40, description="PREMIUM",
                        category="Shopping", rule_category="Insurance"),
            transaction("tx_b", 70.40, description="PREMIUM",
                        category="Shopping", rule_category="Insurance"),
        ]
        self.assertEqual(signals(rows)["count"], 0)

    def test_travel_exemption_follows_the_rule_category(self):
        rows = [
            transaction("tx_foreign", 240, description="FERRY COMPANY",
                        category="Shopping", rule_category="Travel",
                        foreign="AUD 260.00")
        ]
        self.assertEqual(signals(rows)["count"], 0)


class ShortHistoryTests(unittest.TestCase):
    """Fix 6a: the 1-9 prior row blind spot between the two amount checks."""

    def history(self, count, amount, description="SNACK BAR"):
        return [
            transaction("tx_%02d" % index, amount, date="2025-%02d-01" % index,
                        description=description)
            for index in range(1, count + 1)
        ]

    def test_spike_after_a_short_history_flags(self):
        rows = self.history(9, 12)
        rows.append(transaction("tx_spike", 5000, date="2025-11-01",
                                description="SNACK BAR"))
        result = signals(rows)
        self.assertEqual(result["count"], 1)
        self.assertIn("short-history-spike", rows[-1]["risk"]["checks"])
        self.assertEqual(rows[-1]["risk"]["severity"], "high")

    def test_ordinary_ramp_up_stays_quiet(self):
        rows = self.history(5, 100)
        rows.append(transaction("tx_bigger", 380, date="2025-11-01",
                                description="SNACK BAR"))
        self.assertEqual(signals(rows)["count"], 0)

    def test_large_multiple_below_the_absolute_floor_stays_quiet(self):
        rows = self.history(3, 2)
        rows.append(transaction("tx_bigger", 400, date="2025-11-01",
                                description="SNACK BAR"))
        self.assertEqual(signals(rows)["count"], 0)

    def test_long_history_still_uses_the_median_check(self):
        rows = [
            transaction("tx_%02d" % index, 10, date="2025-%02d-01" % index,
                        description="GRAB RIDE GPC-%02d" % index)
            for index in range(1, 11)
        ]
        rows.append(transaction("tx_big", 1000, date="2025-11-01",
                                description="GRAB RIDE GPC-BIG"))
        result = signals(rows)
        self.assertEqual(result["high"], 1)
        self.assertIn("merchant-amount-outlier", rows[-1]["risk"]["checks"])
        self.assertNotIn("short-history-spike", rows[-1]["risk"]["checks"])


class ForeignCurrencyTests(unittest.TestCase):
    """Fix 6b: keyed on merchant and currency, and earned rather than assumed."""

    def test_tiny_first_charge_does_not_immunise_the_merchant(self):
        rows = [
            transaction("tx_small", 5, date="2025-01-01",
                        description="OVERSEAS SHOP", foreign="MYR 16.00"),
            transaction("tx_big", 3000, date="2025-02-01",
                        description="OVERSEAS SHOP", foreign="MYR 9600.00"),
        ]
        signals(rows)
        self.assertIsNone(rows[0].get("risk"))
        self.assertIn("first-foreign-currency-use", rows[1]["risk"]["checks"])

    def test_a_new_currency_at_a_known_merchant_flags(self):
        rows = [
            transaction("tx_myr", 300, date="2025-01-01",
                        description="OVERSEAS SHOP", foreign="MYR 960.00"),
            transaction("tx_usd", 3000, date="2025-02-01",
                        description="OVERSEAS SHOP", foreign="USD 2200.00"),
        ]
        signals(rows)
        self.assertIn("first-foreign-currency-use", rows[0]["risk"]["checks"])
        self.assertIn("first-foreign-currency-use", rows[1]["risk"]["checks"])
        foreign_reason = next(
            reason for reason in rows[1]["risk"]["reasons"]
            if "foreign-currency" in reason or "foreign currency" in reason
        )
        self.assertIn("USD", foreign_reason)
        self.assertNotIn("MYR", foreign_reason)

    def test_a_repeat_in_a_known_currency_stays_quiet(self):
        rows = [
            transaction("tx_first", 300, date="2025-01-01",
                        description="OVERSEAS SHOP", foreign="MYR 960.00"),
            transaction("tx_again", 400, date="2025-02-01",
                        description="OVERSEAS SHOP", foreign="MYR 1280.00"),
        ]
        signals(rows)
        self.assertIsNone(rows[1].get("risk"))


class AnnotationTests(unittest.TestCase):
    """Every member of a flagged group must be openable and reviewable."""

    def test_risk_is_attached_to_every_member_with_one_primary(self):
        rows = [
            transaction("tx_a", 70.40, description="CAFE"),
            transaction("tx_b", 70.40, description="CAFE"),
        ]
        signals(rows)
        self.assertTrue(all(row.get("risk") for row in rows))
        self.assertEqual(rows[0]["risk"]["key"], rows[1]["risk"]["key"])
        self.assertEqual(sum(1 for row in rows if row["risk"]["primary"]), 1)

    def test_a_second_run_does_not_leave_a_stale_verdict(self):
        rows = [
            transaction("tx_a", 70.40, description="CAFE"),
            transaction("tx_b", 70.40, description="CAFE"),
        ]
        signals(rows)
        self.assertTrue(rows[0].get("risk"))
        rows[1]["amount"] = 5.00
        signals(rows)
        self.assertIsNone(rows[0].get("risk"))
        self.assertIsNone(rows[1].get("risk"))


if __name__ == "__main__":
    unittest.main()
