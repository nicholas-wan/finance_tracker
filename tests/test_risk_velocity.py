import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_risk_checks import checks_of, signals, transaction  # noqa: E402


def warm(*rows):
    """Prepend a charge months earlier so the 'never seen before' checks are
    past their warm-up; every merchant is new on the first day of history."""
    return [transaction("warm", 12.0, date="2025-06-01", description="ESTABLISHED SHOP SG")] + list(rows)


class CardTestingTests(unittest.TestCase):
    """Several tiny same-day charges from never-seen merchants look like a
    stolen card being probed; one new stall for lunch does not."""

    def probes(self):
        return [
            transaction("a", 1.00, description="PROBE ALPHA SG"),
            transaction("b", 2.50, description="PROBE BRAVO SG"),
            transaction("c", 0.99, description="PROBE CHARLIE SG"),
        ]

    def test_three_small_probes_from_new_merchants_flag_together(self):
        result = signals(warm(*self.probes()))
        self.assertEqual(checks_of(result), ["card-testing"])
        self.assertEqual(len(result["signals"]), 1)
        self.assertEqual(result["signals"][0]["risk"]["groupIds"], ["a", "b", "c"])
        self.assertEqual(result["signals"][0]["risk"]["reviewAmount"], 4.49)
        self.assertEqual(result["signals"][0]["risk"]["severity"], "medium")

    def test_five_probes_are_high_severity(self):
        names = ["ALPHA", "BRAVO", "CHARLIE", "DELTA", "ECHO"]
        rows = [transaction(str(i), 1.5, description="PROBE %s SG" % name) for i, name in enumerate(names)]
        self.assertEqual(signals(warm(*rows))["signals"][0]["risk"]["severity"], "high")

    def test_two_probes_or_a_known_merchant_stay_quiet(self):
        self.assertEqual(checks_of(signals(warm(*self.probes()[:2]))), [])
        # Two of the three merchants were charged on an earlier day.
        earlier = [
            transaction("x", 5.00, date="2025-12-20", description="PROBE ALPHA SG"),
            transaction("y", 5.00, date="2025-12-20", description="PROBE BRAVO SG"),
        ]
        self.assertEqual(checks_of(signals(warm(*earlier, *self.probes()))), [])

    def test_a_large_charge_among_the_probes_excludes_that_merchant(self):
        rows = self.probes() + [transaction("d", 45.00, description="PROBE DELTA SG")]
        result = signals(warm(*rows))
        self.assertEqual(result["signals"][0]["risk"]["groupIds"], ["a", "b", "c"])

    def test_silent_during_the_first_month_of_history(self):
        # No warm-up row: these probes are the very first day of statements.
        self.assertEqual(checks_of(signals(self.probes())), [])


class NewMerchantVelocityTests(unittest.TestCase):
    """A merchant first seen days ago that is already on its third charge day."""

    def run_of_days(self, amount, dates):
        return [transaction(str(i), amount, date=d, description="FRESH SHOP SG") for i, d in enumerate(dates)]

    def test_three_days_in_a_row_from_a_new_merchant_flags(self):
        rows = self.run_of_days(40.0, ["2026-01-01", "2026-01-02", "2026-01-03"])
        result = signals(warm(*rows))
        self.assertEqual(checks_of(result), ["new-merchant-velocity"])
        self.assertEqual(result["signals"][0]["transaction"]["id"], "2")
        self.assertEqual(result["signals"][0]["risk"]["reviewAmount"], 40.0)

    def test_small_totals_and_established_merchants_stay_quiet(self):
        rows = self.run_of_days(5.0, ["2026-01-01", "2026-01-02", "2026-01-03"])
        self.assertEqual(checks_of(signals(warm(*rows))), [])
        old = transaction("old", 40.0, date="2025-06-01", description="FRESH SHOP SG")
        rows = self.run_of_days(40.0, ["2026-01-01", "2026-01-02", "2026-01-03"])
        self.assertEqual(checks_of(signals(warm(old, *rows))), [])

    def test_charges_spread_over_a_week_stay_quiet(self):
        rows = self.run_of_days(40.0, ["2026-01-01", "2026-01-04", "2026-01-08"])
        self.assertEqual(checks_of(signals(warm(*rows))), [])

    def test_silent_during_the_first_month_of_history(self):
        rows = self.run_of_days(40.0, ["2026-01-01", "2026-01-02", "2026-01-03"])
        self.assertEqual(checks_of(signals(rows)), [])


if __name__ == "__main__":
    unittest.main()
