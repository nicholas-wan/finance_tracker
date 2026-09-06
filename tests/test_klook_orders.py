import importlib
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
build_data = importlib.import_module("build_data")


# Synthetic orders and charges only.
def order(**overrides):
    base = {
        "name": "Sample City Tour",
        "package": "Small group",
        "activityDate": "2026-10-05",
        "quantity": "Adult * 2",
        "amount": 100.0,
        "currency": "SGD",
        "status": "confirmed",
    }
    base.update(overrides)
    return base


def charge(tx_id, amount, date="2026-08-08", credit=False, description="Klook Travel Singapore"):
    return {"id": tx_id, "date": date, "description": description, "amount": amount, "credit": credit}


def prepare(orders, rows, partner=()):
    return build_data.prepare_klook_orders({"orders": orders}, rows, partner)


class KlookOrderTests(unittest.TestCase):
    def test_exact_amount_inside_the_window_links_once(self):
        orders, matches, stats = prepare([order()], [charge("tx_a", 100.0)])
        self.assertEqual(matches["tx_a"]["kind"], "exact")
        self.assertEqual(matches["tx_a"]["orders"][0]["name"], "Sample City Tour")
        self.assertEqual(stats["matchedCharges"], 1)
        self.assertEqual(stats["awaiting"], [])

    def test_a_charge_after_the_activity_does_not_link(self):
        orders, matches, stats = prepare([order()], [charge("tx_a", 100.0, date="2026-10-20")])
        self.assertEqual(matches, {})
        self.assertEqual([item["name"] for item in stats["awaiting"]], ["Sample City Tour"])

    def test_two_orders_with_one_amount_stay_unlinked(self):
        orders, matches, stats = prepare(
            [order(), order(name="Other Tour")], [charge("tx_a", 100.0)])
        self.assertEqual(matches, {})

    def test_two_charges_for_one_order_stay_unlinked(self):
        orders, matches, stats = prepare(
            [order()], [charge("tx_a", 100.0), charge("tx_b", 100.0, date="2026-08-09")])
        self.assertEqual(matches, {})

    def test_expired_orders_never_link_and_are_not_awaited(self):
        orders, matches, stats = prepare(
            [order(status="expired")], [charge("tx_a", 100.0)])
        self.assertEqual(matches, {})
        self.assertEqual(stats["expired"], 1)
        self.assertEqual(stats["awaiting"], [])

    def test_a_refund_links_to_the_cancelled_order(self):
        orders, matches, stats = prepare(
            [order(status="canceled")],
            [charge("tx_a", 100.0, date="2026-06-26"), charge("tx_r", 100.0, date="2026-06-26", credit=True)])
        self.assertEqual(matches["tx_a"]["kind"], "exact")
        self.assertEqual(matches["tx_r"]["kind"], "refund")
        self.assertEqual(stats["matchedRefunds"], 1)
        self.assertEqual(stats["awaiting"], [])

    def test_a_charge_equal_to_two_same_day_orders_links_to_both(self):
        orders, matches, stats = prepare(
            [order(name="Circus seat", amount=197.77), order(name="Circus box", amount=104.58)],
            [charge("tx_a", 302.35, date="2026-05-01")])
        self.assertEqual(matches["tx_a"]["kind"], "aggregate")
        self.assertEqual(sorted(item["name"] for item in matches["tx_a"]["orders"]), ["Circus box", "Circus seat"])
        self.assertEqual(stats["matchedOrders"], 2)

    def test_orders_on_different_days_do_not_aggregate(self):
        orders, matches, stats = prepare(
            [order(name="A", amount=60.0), order(name="B", amount=40.0, activityDate="2026-10-09")],
            [charge("tx_a", 100.0)])
        self.assertEqual(matches, {})

    def test_the_other_persons_charge_links_too(self):
        partner = [{"id": "nic_tx_1", "date": "2026-08-08", "description": "Klook Travel Singapore",
                    "amount": 100.0, "type": "debit"}]
        orders, matches, stats = prepare([order()], [], partner)
        self.assertEqual(matches["nic_tx_1"]["kind"], "exact")

    def test_non_klook_rows_are_ignored(self):
        orders, matches, stats = prepare([order()], [charge("tx_a", 100.0, description="TRIP.COM SINGAPORE")])
        self.assertEqual(matches, {})

    def test_malformed_orders_fail_closed(self):
        with self.assertRaisesRegex(SystemExit, "no name"):
            prepare([order(name="")], [])
        with self.assertRaisesRegex(SystemExit, "activityDate"):
            prepare([order(activityDate="5 Oct 2026")], [])
        with self.assertRaisesRegex(SystemExit, "status"):
            prepare([order(status="pending")], [])
        with self.assertRaisesRegex(SystemExit, "amount"):
            prepare([order(amount="100")], [])

    def test_orders_before_the_first_statement_are_not_awaited(self):
        orders, matches, stats = build_data.prepare_klook_orders(
            {"orders": [order(activityDate="2019-06-21"), order(name="Later", activityDate="2026-10-05")]},
            [], (), "2023-07")
        self.assertEqual([item["name"] for item in stats["awaiting"]], ["Later"])
        self.assertEqual(stats["beforeStatements"], 1)

    def test_attach_klook_names_the_row_unless_told_not_to(self):
        match = {"kind": "exact", "orders": [order()], "note": "n"}
        record = {}
        build_data.attach_klook(record, match, True)
        self.assertEqual(record["displayName"], "Sample City Tour")
        self.assertEqual(record["displayNameSource"], "klook-order")
        self.assertEqual(record["klookOrder"]["activityDate"], "2026-10-05")
        self.assertNotIn("klookOrders", record)
        quiet = {}
        build_data.attach_klook(quiet, match, False)
        self.assertNotIn("displayName", quiet)


if __name__ == "__main__":
    unittest.main()
