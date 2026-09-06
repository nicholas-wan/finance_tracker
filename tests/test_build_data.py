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


class FoodpandaOrderTests(unittest.TestCase):
    def order(self, **changes):
        value = {
            "orderId": "abcd-2635-efgh",
            "date": "2026-08-29",
            "time": "13:09",
            "fulfillment": "delivery",
            "merchant": "pandamart (Example)",
            "amount": 42.50,
            "category": "Groceries",
        }
        value.update(changes)
        return value

    def card(self, **changes):
        value = {
            "id": "tx_foodpanda000000001",
            "date": "2026-08-29",
            "description": "fp*Food Panda Singapore",
            "amount": 42.50,
            "credit": False,
        }
        value.update(changes)
        return value

    def test_exact_date_and_amount_links_order_to_statement(self):
        orders, by_transaction = build_data.prepare_foodpanda_orders(
            {"orders": [self.order()]}, [self.card()])
        self.assertEqual(orders[0]["statementTransactionId"],
                         "tx_foodpanda000000001")
        self.assertEqual(by_transaction["tx_foodpanda000000001"]["category"],
                         "Groceries")

    def test_amount_mismatch_is_visible_but_not_forced(self):
        orders, by_transaction = build_data.prepare_foodpanda_orders(
            {"orders": [self.order()]}, [self.card(amount=42.49)])
        self.assertNotIn("statementTransactionId", orders[0])
        self.assertEqual(by_transaction, {})

    def test_ambiguous_duplicate_key_is_not_partially_matched(self):
        second = self.order(orderId="wxyz-2635-1234")
        orders, by_transaction = build_data.prepare_foodpanda_orders(
            {"orders": [self.order(), second]}, [self.card()])
        self.assertTrue(all("statementTransactionId" not in order for order in orders))
        self.assertEqual(by_transaction, {})

    def test_category_is_derived_and_cannot_disagree_with_merchant(self):
        with self.assertRaisesRegex(SystemExit, "category disagrees"):
            build_data.prepare_foodpanda_orders(
                {"orders": [self.order(category="Food & dining")]}, [])


class ShopeeOrderTests(unittest.TestCase):
    def source(self, orders, **overrides):
        source = {
            "statementFrom": "2026-02-01",
            "statementThrough": "2026-09-30",
            "orders": orders,
        }
        source.update(overrides)
        return source

    def order(self, order_id="242058592217954", amount=18.90):
        return {"orderId": order_id, "merchant": "Example Store",
                "status": "completed", "amount": amount,
                "items": ["Example item"], "historyIndex": 0}

    def card(self, tx_id="tx_shopee000000000001", amount=18.90, date="2026-08-24"):
        return {"id": tx_id, "date": date, "description": "SHOPEE SG MP SINGAPORE",
                "amount": amount, "credit": False}

    def test_unique_amount_links_to_statement(self):
        orders, matches = build_data.prepare_shopee_orders(
            self.source([self.order()]), [self.card()])
        self.assertEqual(orders[0]["statementTransactionId"],
                         "tx_shopee000000000001")
        self.assertEqual(matches["tx_shopee000000000001"]["merchant"],
                         "Example Store")
        self.assertEqual(matches["tx_shopee000000000001"]["items"],
                         ["Example item"])

    def test_amount_collision_is_not_forced(self):
        rows = [self.card(), self.card("tx_shopee000000000002", date="2026-04-10")]
        orders, matches = build_data.prepare_shopee_orders(
            self.source([self.order()]), rows)
        self.assertNotIn("statementTransactionId", orders[0])
        self.assertEqual(matches, {})

    def test_orders_beyond_the_history_cutoff_never_link(self):
        # An older order priced exactly like a statement charge must not be
        # linked by coincidence once it sits outside the statement window.
        older = self.order("242058592217955", amount=18.90)
        older["historyIndex"] = 7
        source = self.source([older], statementOrderMaxHistoryIndex=5)
        orders, matches = build_data.prepare_shopee_orders(source, [self.card()])
        self.assertNotIn("statementTransactionId", orders[0])
        self.assertEqual(matches, {})

    def test_reviewed_aggregate_links_multiple_orders_to_one_charge(self):
        first = self.order(amount=10.25)
        second = self.order("242058592217955", amount=8.65)
        second["historyIndex"] = 1
        source = self.source(
            [first, second],
            statementAggregates=[{
                "transactionId": "tx_shopee000000000001",
                "orderIds": [first["orderId"], second["orderId"]],
                "note": "Two adjacent orders add exactly to the statement charge.",
            }],
        )
        orders, matches = build_data.prepare_shopee_orders(source, [self.card()])
        self.assertEqual(
            [order["statementTransactionId"] for order in orders],
            ["tx_shopee000000000001", "tx_shopee000000000001"],
        )
        self.assertEqual([order["date"] for order in orders], ["2026-08-24"] * 2)
        self.assertEqual(matches["tx_shopee000000000001"]["kind"], "aggregate")
        self.assertEqual(
            [order["orderId"] for order in matches["tx_shopee000000000001"]["orders"]],
            [first["orderId"], second["orderId"]],
        )

    def test_reviewed_aggregate_must_equal_the_statement_charge(self):
        first = self.order(amount=10.00)
        second = self.order("242058592217955", amount=8.00)
        second["historyIndex"] = 1
        source = self.source(
            [first, second],
            statementAggregates=[{
                "transactionId": "tx_shopee000000000001",
                "orderIds": [first["orderId"], second["orderId"]],
                "note": "Synthetic mismatch.",
            }],
        )
        with self.assertRaisesRegex(SystemExit, "do not equal"):
            build_data.prepare_shopee_orders(source, [self.card()])

    def test_reviewed_aggregate_rejects_orders_beyond_the_history_cutoff(self):
        first = self.order(amount=10.25)
        second = self.order("242058592217955", amount=8.65)
        second["historyIndex"] = 9
        source = self.source(
            [first, second],
            statementOrderMaxHistoryIndex=5,
            statementAggregates=[{
                "transactionId": "tx_shopee000000000001",
                "orderIds": [first["orderId"], second["orderId"]],
                "note": "Synthetic cutoff breach.",
            }],
        )
        with self.assertRaisesRegex(SystemExit, "history cutoff"):
            build_data.prepare_shopee_orders(source, [self.card()])


class GrabReceiptTests(unittest.TestCase):
    def test_known_grab_locations_use_home_labels(self):
        self.assertEqual(
            build_data.grab_location_label(
                "Lobby near 123 Example Road",
                {"123 Example Road": "Home"}),
            "Home",
        )
        self.assertEqual(
            build_data.grab_location_label(
                "Near 456 Sample Street", {"456 Sample Street": "Yx's Home"}),
            "Yx's Home",
        )

    def receipt(self, **changes):
        value = {
            "receiptId": "A-12345678",
            "date": "2026-08-08",
            "time": "13:09",
            "service": "GrabCar",
            "category": "Transport",
            "amount": 22.10,
            "currency": "SGD",
            "profile": "personal",
            "corporate": False,
            "eligibleForPersonalFinance": True,
            "merchant": "GrabCar",
            "items": [],
            "pickup": "Home address",
            "dropoff": "Destination address",
            "paymentMethod": "GrabPay Wallet",
        }
        value.update(changes)
        return value

    def card(self, tx_id, amount, description="Grab* GPC-EXAMPLE SINGAPORE"):
        return {
            "id": tx_id,
            "date": "2026-08-08",
            "description": description,
            "amount": amount,
            "credit": False,
        }

    def test_web_history_adds_missing_booking_and_excludes_business(self):
        source, stats = build_data.merge_grab_web_history(
            {"receipts": []},
            {
                "fields": ["amount", "bookingCode", "currency", "dateTime",
                           "dropoff", "fleetType", "pickup", "profile"],
                "records": [[12.4, "A-WEB-1", "SGD", "16 Jul 2026, 01:07PM",
                             "Office", "Standard | Car or taxi", "Hotel", "Business"]],
            },
        )
        self.assertEqual(stats, {"records": 1, "added": 1, "enriched": 0})
        receipt = source["receipts"][0]
        self.assertEqual(receipt["pickup"], "Hotel")
        self.assertEqual(receipt["profile"], "business")
        self.assertTrue(receipt["corporate"])
        self.assertFalse(receipt["eligibleForPersonalFinance"])

    def test_web_history_enriches_email_without_replacing_receipt_total(self):
        email = self.receipt(amount=18.0, pickup="", dropoff="")
        source, stats = build_data.merge_grab_web_history(
            {"receipts": [email]},
            {
                "fields": ["amount", "bookingCode", "currency", "dateTime",
                           "dropoff", "fleetType", "pickup", "profile"],
                "records": [[25.6, "A-12345678", "SGD", "08 Aug 2026, 01:09PM",
                             "Home", "GrabFood", "Restaurant", "Personal"]],
            },
        )
        receipt = source["receipts"][0]
        self.assertEqual(stats, {"records": 1, "added": 0, "enriched": 1})
        self.assertEqual(receipt["amount"], 18.0)
        self.assertEqual(receipt["webHistoryAmount"], 25.6)
        self.assertTrue(receipt["webHistoryAmountDiffers"])
        self.assertEqual(receipt["pickup"], "Restaurant")

    def test_one_receipt_can_reconcile_to_split_wallet_funding(self):
        rows = [self.card("tx_grab_12", 12), self.card("tx_grab_10", 10)]
        receipts, matches = build_data.prepare_grab_receipts(
            {"receipts": [self.receipt()]}, rows)
        self.assertEqual(
            set(receipts[0]["statementTransactionIds"]),
            {"tx_grab_12", "tx_grab_10"},
        )
        self.assertEqual(set(matches), {"tx_grab_12", "tx_grab_10"})

    def test_exact_receipt_id_survives_unrelated_same_day_charge(self):
        rows = [
            self.card("tx_direct", 22.10, "Grab* A-12345678 Singapore"),
            self.card("tx_unrelated", 2.71, "Grab* A-87654321 Singapore"),
        ]
        receipts, matches = build_data.prepare_grab_receipts(
            {"receipts": [self.receipt(paymentMethod="Visa 7389")]}, rows)
        self.assertEqual(receipts[0]["statementTransactionIds"], ["tx_direct"])
        self.assertEqual(set(matches), {"tx_direct"})

    def test_unique_direct_card_amount_matches_before_wallet_grouping(self):
        rows = [
            self.card("tx_direct", 22.10, "Grab* DIRECT SINGAPORE"),
            self.card("tx_unrelated", 2.71, "Grab* OTHER SINGAPORE"),
        ]
        receipts, matches = build_data.prepare_grab_receipts(
            {"receipts": [self.receipt(paymentMethod="7389")]}, rows)
        self.assertEqual(receipts[0]["statementTransactionIds"], ["tx_direct"])
        self.assertEqual(set(matches), {"tx_direct"})

    def test_unknown_profile_is_not_guessed_and_flags_must_agree(self):
        unknown = self.receipt(
            profile="unknown", eligibleForPersonalFinance=False)
        receipts, matches = build_data.prepare_grab_receipts(
            {"receipts": [unknown]}, [self.card("tx_grab_22", 22)])
        self.assertNotIn("statementTransactionIds", receipts[0])
        self.assertEqual(matches, {})
        with self.assertRaisesRegex(SystemExit, "eligibility disagrees"):
            build_data.prepare_grab_receipts(
                {"receipts": [self.receipt(eligibleForPersonalFinance=False)]},
                [],
            )


class InsuranceTests(unittest.TestCase):
    def test_premiums_are_annualized_and_coverage_is_totalled(self):
        insurance = build_data.prepare_insurance({"people": [{
            "id": "person", "name": "Person", "policies": [
                {
                    "id": "monthly", "company": "A", "plan": "Monthly plan",
                    "verification": {
                        "source": "Insurer portal", "checkedAt": "2026-09-04",
                    },
                    "summary": "A plain-language policy summary.",
                    "paymentMethod": "DBS account",
                    "reconcileWithImportedStatements": False,
                    "portalPremiumTotal": 99.75,
                    "accountDebitAmount": 100,
                    "benefits": {"death": 100000},
                    "premiums": {"cashWithValue": 100, "frequency": "Monthly"},
                    "components": [{
                        "name": "Family cover", "insuredPerson": "Person",
                        "relationship": "Main insured", "benefitLabel": "Death",
                        "sumAssured": 100000, "premiumAmount": 99.75,
                        "premiumFrequency": "Monthly",
                        "coverageEffectiveDate": "2026-01-01",
                        "nextDueDate": "2026-10-01", "paymentMethod": "GIRO",
                    }],
                },
                {
                    "id": "annual", "company": "B", "plan": "Annual plan",
                    "benefits": {"death": 50000, "criticalIllness": 25000},
                    "premiums": {"cashWithoutValue": 300, "frequency": "Annual"},
                },
                {
                    "id": "lapsed", "company": "C", "plan": "Old plan",
                    "status": "Lapsed", "benefits": {"death": 999999},
                    "premiums": {"cashWithValue": 20, "frequency": "Monthly"},
                },
                {
                    "id": "coverage-mirror", "company": "A", "plan": "Mirror",
                    "coverageOnly": True, "hiddenInRegister": True,
                    "premiumPaidBy": "Partner",
                    "benefits": {"death": 25000},
                    "premiums": {},
                },
            ],
        }]})
        person = insurance["people"][0]
        self.assertEqual(person["coverage"]["death"], 175000)
        self.assertEqual(person["coverage"]["criticalIllness"], 25000)
        self.assertEqual(person["totals"]["annualCashPremium"], 1500)
        self.assertEqual(person["totals"]["monthlyEquivalent"], 125)
        self.assertEqual(person["totals"]["activePolicies"], 2)
        self.assertEqual(person["totals"]["lapsedPolicies"], 1)
        self.assertEqual(person["policies"][0]["verification"], {
            "source": "Insurer portal", "checkedAt": "2026-09-04",
        })
        self.assertEqual(
            person["policies"][0]["summary"], "A plain-language policy summary.")
        self.assertFalse(person["policies"][0]["reconcileWithImportedStatements"])
        self.assertEqual(person["policies"][0]["paymentMethod"], "DBS account")
        self.assertEqual(person["policies"][0]["portalPremiumTotal"], 99.75)
        self.assertEqual(person["policies"][0]["accountDebitAmount"], 100)
        self.assertEqual(person["policies"][0]["components"][0]["insuredPerson"], "Person")
        self.assertEqual(person["policies"][0]["components"][0]["nextDueDate"], "2026-10-01")
        self.assertTrue(person["policies"][3]["coverageOnly"])
        self.assertTrue(person["policies"][3]["hiddenInRegister"])
        self.assertEqual(person["policies"][3]["premiumPaidBy"], "Partner")


if __name__ == "__main__":
    unittest.main()



class TripBookingTests(unittest.TestCase):
    """Only an exact, dated, one-to-one SGD amount names a Trip.com charge."""

    def booking(self, **overrides):
        base = {
            "bookingNo": "1234567890123",
            "status": "Completed",
            "productType": "Hotels",
            "bookingDate": "June 12, 2026",
            "productName": "Example Hotel",
            "travelTime": "June 20, 2026",
            "traveller": "Example Traveller",
            "currency": "SGD",
            "amount": 321.45,
            "sourceFile": "synthetic.xlsx",
        }
        base.update(overrides)
        return base

    def card(self, tx_id="tx_trip0000000000001", amount=321.45, date="2026-06-12",
             description="TRIP.COM SINGAPORE", credit=False):
        return {"id": tx_id, "date": date, "description": description,
                "amount": amount, "credit": credit}

    def prepare(self, bookings, rows):
        return build_data.prepare_trip_bookings({"bookings": bookings}, rows)

    def test_unique_dated_amount_links_and_keeps_the_export_fields(self):
        bookings, matches, stats = self.prepare([self.booking()], [self.card(date="2026-06-13")])
        self.assertEqual(matches["tx_trip0000000000001"]["productName"], "Example Hotel")
        self.assertEqual(matches["tx_trip0000000000001"]["status"], "Completed")
        self.assertEqual(bookings[0]["bookingNo"], "1234567890123")
        self.assertEqual(stats["matched"], 1)
        self.assertEqual(stats["unmatchedCharges"], 0)
        self.assertEqual(stats["ambiguousCharges"], 0)

    def test_two_bookings_sharing_an_amount_are_never_zipped_onto_two_charges(self):
        bookings = [self.booking(), self.booking(bookingNo="1234567890124", productName="Other Hotel",
                                                 bookingDate="June 13, 2026")]
        rows = [self.card(), self.card("tx_trip0000000000002", date="2026-06-13")]
        _, matches, stats = self.prepare(bookings, rows)
        self.assertEqual(matches, {})
        self.assertEqual(stats["ambiguousCharges"], 2)
        self.assertEqual(stats["unmatchedBookings"], 2)

    def test_one_booking_with_two_candidate_charges_is_ambiguous(self):
        rows = [self.card(), self.card("tx_trip0000000000002", date="2026-06-15")]
        _, matches, stats = self.prepare([self.booking()], rows)
        self.assertEqual(matches, {})
        self.assertEqual(stats["ambiguousCharges"], 2)

    def test_dates_separate_same_priced_bookings(self):
        bookings = [self.booking(), self.booking(bookingNo="1234567890124", productName="Later Hotel",
                                                 bookingDate="September 1, 2026")]
        rows = [self.card(), self.card("tx_trip0000000000002", date="2026-09-02")]
        _, matches, _ = self.prepare(bookings, rows)
        self.assertEqual(matches["tx_trip0000000000001"]["productName"], "Example Hotel")
        self.assertEqual(matches["tx_trip0000000000002"]["productName"], "Later Hotel")

    def test_a_charge_outside_the_window_is_not_named(self):
        _, matches, stats = self.prepare([self.booking()], [self.card(date="2026-06-25")])
        self.assertEqual(matches, {})
        self.assertEqual(stats["unmatchedCharges"], 1)

    def test_refunds_foreign_currency_and_zero_amounts_are_skipped(self):
        bookings = [
            self.booking(currency="CNY"),
            self.booking(bookingNo="1234567890125", amount=0),
            self.booking(bookingNo="1234567890126", amount=None),
        ]
        rows = [self.card(), self.card("tx_trip0000000000002", credit=True)]
        _, matches, stats = self.prepare(bookings, rows)
        self.assertEqual(matches, {})
        self.assertEqual(stats["foreignCurrency"], 1)
        self.assertEqual(stats["zeroAmount"], 2)
        self.assertEqual(stats["statementRefunds"], 1)

    def test_a_cancelled_booking_still_links_and_is_counted(self):
        _, matches, stats = self.prepare([self.booking(status="Cancelled")], [self.card()])
        self.assertEqual(matches["tx_trip0000000000001"]["status"], "Cancelled")
        self.assertEqual(stats["matchedCancelled"], 1)

    def test_an_undated_booking_cannot_match(self):
        _, matches, stats = self.prepare([self.booking(bookingDate="sometime")], [self.card()])
        self.assertEqual(matches, {})
        self.assertEqual(stats["undated"], 1)

    def test_non_trip_descriptions_are_ignored(self):
        _, matches, stats = self.prepare(
            [self.booking()], [self.card(description="SOME HOTEL DIRECT")])
        self.assertEqual(matches, {})
        self.assertEqual(stats["statementCharges"], 0)

    def test_numeric_booking_numbers_are_normalised(self):
        bookings, matches, _ = self.prepare([self.booking(bookingNo=1234567890123)], [self.card()])
        self.assertEqual(bookings[0]["bookingNo"], "1234567890123")
        self.assertIn("tx_trip0000000000001", matches)

    def test_invalid_exports_abort_the_build(self):
        cases = [
            ("missing booking number", [self.booking(bookingNo="")]),
            ("float booking number", [self.booking(bookingNo=1.5e15 + 0.5)]),
            ("duplicate", [self.booking(), self.booking()]),
            ("no product name", [self.booking(productName=" ")]),
            ("bad currency", [self.booking(currency="S$")]),
            ("negative", [self.booking(amount=-1)]),
            ("nan", [self.booking(amount=float("nan"))]),
            ("inf", [self.booking(amount=float("inf"))]),
            ("text amount", [self.booking(amount="12.3.4")]),
            ("bool amount", [self.booking(amount=True)]),
            ("non-text status", [self.booking(status=3)]),
            ("not an object", ["booking"]),
        ]
        for label, bookings in cases:
            with self.subTest(label):
                with self.assertRaises(SystemExit):
                    self.prepare(bookings, [self.card()])
        with self.assertRaises(SystemExit):
            build_data.prepare_trip_bookings(["not", "a", "dict"], [self.card()])
        with self.assertRaises(SystemExit):
            build_data.prepare_trip_bookings({"bookings": "nope"}, [self.card()])

    def test_a_missing_file_builds_nothing(self):
        bookings, matches, stats = build_data.prepare_trip_bookings(None, [self.card()])
        self.assertEqual((bookings, matches), ([], {}))
        self.assertEqual(stats["statementCharges"], 1)

    def test_reviewed_links_support_aggregate_charges_and_refunds(self):
        second = self.booking(
            bookingNo="1234567890124", productName="Example Hotel", amount=100.0
        )
        charge = self.card(amount=421.45)
        refund = self.card(
            "tx_trip0000000000002", amount=100.0, date="2026-06-20", credit=True
        )
        reconciliation = {
            "links": [
                {
                    "transactionIds": [charge["id"]],
                    "bookingNos": [self.booking()["bookingNo"], second["bookingNo"]],
                    "kind": "aggregate",
                    "note": "One statement charge covers both same-day bookings.",
                },
                {
                    "transactionIds": [refund["id"]],
                    "bookingNos": [second["bookingNo"]],
                    "kind": "refund",
                    "note": "The later credit refunds the second booking.",
                },
            ]
        }
        _, matches, stats = build_data.prepare_trip_bookings(
            {"bookings": [self.booking(), second]}, [charge, refund], reconciliation
        )
        self.assertEqual(
            [booking["bookingNo"] for booking in matches[charge["id"]]["bookings"]],
            ["1234567890123", "1234567890124"],
        )
        self.assertEqual(stats["matched"], 1)
        self.assertEqual(stats["matchedRefunds"], 1)
        self.assertEqual(stats["matchedTransactions"], 2)
        self.assertEqual(stats["matchedBookings"], 2)
        self.assertEqual(stats["unmatchedCharges"], 0)
        self.assertEqual(stats["unmatchedRefunds"], 0)

    def test_reviewed_links_reject_unknown_and_repeated_rows(self):
        cases = [
            {
                "transactionIds": ["tx_missing"],
                "bookingNos": [self.booking()["bookingNo"]],
                "kind": "discounted",
                "note": "Reviewed.",
            },
            {
                "transactionIds": [self.card()["id"]],
                "bookingNos": 123,
                "kind": "discounted",
                "note": "Reviewed.",
            },
        ]
        for link in cases:
            with self.subTest(link=link):
                with self.assertRaises(SystemExit):
                    build_data.prepare_trip_bookings(
                        {"bookings": [self.booking()]},
                        [self.card()],
                        {"links": [link]},
                    )
        repeated = {
            "transactionIds": [self.card()["id"]],
            "bookingNos": [self.booking()["bookingNo"]],
            "kind": "discounted",
            "note": "Reviewed.",
        }
        with self.assertRaises(SystemExit):
            build_data.prepare_trip_bookings(
                {"bookings": [self.booking()]},
                [self.card()],
                {"links": [repeated, repeated]},
            )
