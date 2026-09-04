import importlib
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
build_data = importlib.import_module("build_data")
parse_one = importlib.import_module("parse_one")

# The account parser reads the holder's real name and own-account numbers from
# the git-ignored manual/identity.json. Tests hand classify() this stand-in so
# nothing private is needed and the flow rules are exercised as main() sees them.
TEST_IDENTITY = {
    "statementHolderName": "REDACTED HOLDER NAME",
    "knownAccounts": {"1111111111": "Redacted Savings A/c"},
    "fixedDepositAccounts": ["2222222222"],
    "trustedCounterparties": ["Redacted Person"],
}


class WordBoundaryRegressionTests(unittest.TestCase):
    """Substring rules used to fire in the middle of an unrelated word.

    categorize() and classify() now match against the description padded with a
    space at each end, and every short or generic token in the tables carries an
    explicit edge space, so it can only match at a word edge. Merchant names
    below are invented; none of them come from the real statements.
    """

    # (description, category that must NOT be returned, why)
    CATEGORY_FALSE_POSITIVES = [
        ("GYMBOREE PLAY ORCHARD", "Sports & fitness", "GYM inside GYMBOREE"),
        ("BLOOM1 SHOP SINGAPORE", "Bills & utilities", "M1 inside BLOOM1"),
        ("MALAIA TRADING CO", "Insurance", "AIA inside MALAIA"),
        ("SANDFWDS LOGISTICS", "Insurance", "FWD inside SANDFWDS"),
        ("GIGABYTE TECHNOLOGY SG", "Bills & utilities", "GIGA inside GIGABYTE"),
        ("GIGASPORTS OUTLET", "Bills & utilities", "GIGA inside GIGASPORTS"),
        ("MARSALONG SUPPLY CO", "Personal care", "SALON inside MARSALONG"),
        ("BODYGUARDIAN MEDICAL", "Personal care", "GUARDIAN inside BODYGUARDIAN"),
        ("RHUBARBERRY FARM", "Personal care", "BARBER inside RHUBARBERRY"),
        ("MOTELIER SUPPLY", "Travel", "HOTEL inside MOTELIER"),
        ("BIKEART STUDIO", "Shopping", "IKEA inside BIKEART"),
        ("REORDER 2024 CONSULTING", "Shopping", "ORDER 20 inside REORDER 2024"),
        ("HAMBURGERSTEIN LAW", "Food & dining", "BURGER inside HAMBURGERSTEIN"),
        ("PROTOASTER LABS", "Food & dining", "TOAST inside PROTOASTER"),
        ("BEEFCAFETERIA HOLDINGS", "Food & dining", "CAFE inside BEEFCAFETERIA"),
        ("PROCESSEDFOOD SUPPLY CO", "Food & dining", "FOOD inside PROCESSEDFOOD"),
        ("KKFCB CONSULTING", "Food & dining", "KFC inside KKFCB"),
        ("MYHSA ADMIN", "Food & dining", "YHS inside MYHSA"),
        ("TRANSMRT LOGISTICS", "Transport", "SMRT inside TRANSMRT"),
        ("SG2ADVISORY PTE", "Games", "G2A inside SG2ADVISORY"),
    ]

    # (description, category that must still be returned)
    CATEGORY_POSITIVES = [
        ("ANYTIME GYM PTE LTD", "Sports & fitness"),
        ("GIANT PIONEER MALL SINGAPORE", "Groceries"),
        ("GIANT-BISHAN SINGAPORE", "Groceries"),
        ("M1 LIMITED SINGAPORE", "Bills & utilities"),
        ("GIGA 68255000", "Bills & utilities"),
        ("AXS PTE LTD SINGAPORE", "Bills & utilities"),
        ("AIA SINGAPORE PTE LTD", "Insurance"),
        ("FWD SINGAPORE PTE LTD", "Insurance"),
        ("KCUTS SALON SINGAPORE", "Personal care"),
        ("GUARDIAN-WESTGATE SINGAPORE", "Personal care"),
        ("GRAND HYATT HOTEL SINGAPORE", "Travel"),
        ("IKEA - JEM SINGAPORE", "Shopping"),
        ("order 2004101320 Singapore", "Shopping"),
        ("SUBWAY - JUNCTION 8 SINGAPORE", "Food & dining"),
        ("MOS BURGER-JUNCTION 8 SINGAPORE", "Food & dining"),
        ("OPN Toast Bo Singapore", "Food & dining"),
        ("MELLBEN SEAFOOD DEPOT SINGAPORE", "Food & dining"),
        ("YHS(SINGAPORE) Singapore", "Food & dining"),
        ("TADA 019723857CD9 SINGAPORE", "Transport"),
        # Descriptors the statement glues to other text keep an unanchored
        # variant, so anchoring must not have cost them their category.
        ("Foodpanda Singapore SINGAPORE", "Food & dining"),
        ("FP*FOOD PANDA SINGAPORE", "Food & dining"),
        ("PAYPAL *WATAMIFOODS 63367686", "Food & dining"),
        ("LUCKINCOFFEE SINGAPORE", "Food & dining"),
        # Separator variants listed next to the anchored token.
        ("WTR*COLUMBUS-COFFEE-CO 6586988741", "Food & dining"),
        ("BP_MX_Penguin-Cafe Singapore", "Food & dining"),
        ("TBG-TP L.CAFE JOHOR BAHRU", "Food & dining"),
    ]

    # (description, flow that must NOT be returned, why)
    FLOW_FALSE_POSITIVES = [
        ("SAXOPHONE MUSIC SCHOOL", "Investment", "SAXO inside SAXOPHONE"),
        ("PAYNOW-FAST NETSUITE INC", "Cash & NETS", "NETS inside NETSUITE"),
        ("TRANSFER TO HDBANK VIETNAM", "Mortgage & home", "HDB inside HDBANK"),
        ("GIRO IBKRAFT WORKSHOP", "Investment", "IBKR inside IBKRAFT"),
        ("PAYNOW SYFELINE DESIGN", "Investment", "SYFE inside SYFELINE"),
    ]

    # (description, flow that must still be returned)
    FLOW_POSITIVES = [
        ("SAXO CAPITAL MARKETS", "Investment"),
        ("MOOMOO SG PTE LTD", "Investment"),
        ("IBKR SECURITIES", "Investment"),
        ("ENDOWUS SG PTE LTD", "Investment"),
        ("SYFE PTE LTD", "Investment"),
        ("NETS QR PAYMENT MERCHANT", "Cash & NETS"),
        ("Cash Withdrawal-ATM 1234567890", "Cash & NETS"),
        ("GIRO HDB LOAN PAYMENT", "Mortgage & home"),
        ("Bill Payment mBK-UOB Cards 1234567890", "Credit card bill"),
        ("PAYNOW-FAST PHILLIP SECURITIES", "Investment"),
    ]

    def test_category_false_positives_are_gone(self):
        for description, wrong, why in self.CATEGORY_FALSE_POSITIVES:
            with self.subTest(description=description, why=why):
                self.assertNotEqual(build_data.categorize(description), wrong, why)

    def test_category_positives_still_match(self):
        for description, expected in self.CATEGORY_POSITIVES:
            with self.subTest(description=description):
                self.assertEqual(build_data.categorize(description), expected)

    def test_flow_false_positives_are_gone(self):
        for description, wrong, why in self.FLOW_FALSE_POSITIVES:
            with self.subTest(description=description, why=why):
                self.assertNotEqual(
                    parse_one.classify(description, TEST_IDENTITY), wrong, why)

    def test_flow_positives_still_match(self):
        for description, expected in self.FLOW_POSITIVES:
            with self.subTest(description=description):
                self.assertEqual(
                    parse_one.classify(description, TEST_IDENTITY), expected)

    def test_investment_false_positive_would_hide_a_withdrawal(self):
        # The Investment flows are subtracted from spending as money you still
        # own, so a wrongly matched row disappears from the totals entirely.
        # This is the one direction that must never regress.
        for description in ("SAXOPHONE MUSIC SCHOOL", "PAYNOW MOOMOOSHI RAMEN",
                            "GIRO IBKRAFT WORKSHOP"):
            with self.subTest(description=description):
                self.assertNotEqual(
                    parse_one.classify(description, TEST_IDENTITY), "Investment")

    def test_game_rules_are_anchored_too(self):
        self.assertEqual(build_data.game_of("PSN NETWORK TOPUP"), "PlayStation")
        self.assertEqual(build_data.game_of("G2A.COM ORDER"), "Top-up sites")
        self.assertEqual(build_data.game_of("TOPSNAP MEDIA"), "Other games")
        self.assertEqual(build_data.game_of("SG2ADVISORY PTE"), "Other games")

    def test_matching_ignores_repeated_whitespace(self):
        self.assertEqual(
            build_data.categorize("NTUC   FAIRPRICE\tSINGAPORE"), "Groceries")
        self.assertEqual(build_data.categorize("  M1  LIMITED  "), "Bills & utilities")

    def test_identity_signatures_are_unchanged(self):
        rules = parse_one.flow_rules(TEST_IDENTITY)
        fixed = dict(rules)["Fixed deposit"]
        self.assertIn("TO 2222222222", fixed)
        self.assertEqual(parse_one.classify("TRANSFER TO 2222222222", TEST_IDENTITY),
                         "Fixed deposit")


class CategoryRuleTests(unittest.TestCase):
    def test_reviewed_food_merchants(self):
        descriptions = [
            "SEOUL GARDEN - BUGIS SINGAPORE",
            "Wunderfolks - J8 Singapore",
            "AWFULLY CHOCOLATE - RA SINGAPORE",
            "XW WESTERN GRILL- RC SINGAPORE",
            "JIA XIANG SARAWAK KUCHINGSingapore",
            "TROPICO KOPI-CENTURY JOHOR BAHRU",
            "HOONG YING ENTERPRISE PRISingapore",
            "KEIJO SDN BHD JOHOR",
            "TINO JC SINGAPORE",
            "Q'SON GROUP ST 4A Singapore",
            "AURESYS PL SINGAPORE",
            "ABBA OL2 PTE LTD* OD SINGAPORE",
            "FJ JN8-PEPPER GRILL SINGAPORE",
            "KOPIFELLAS SINGAPORE",
        ]
        for description in descriptions:
            with self.subTest(description=description):
                self.assertEqual(build_data.categorize(description), "Food & dining")

    def test_reviewed_grocery_and_travel_merchants(self):
        self.assertEqual(
            build_data.categorize("ACE DYNAMIC HOLDINGS SINGAPORE"),
            "Groceries",
        )
        self.assertEqual(
            build_data.categorize("AUSTRALIANETA NORTH SYDNEY"),
            "Travel",
        )

    def test_reviewed_iphone_purchase(self):
        self.assertEqual(
            build_data.categorize("TELECOM EQUIPMENT PL-WIRESINGAPORE"),
            "Shopping",
        )

    def test_reviewed_gift_and_aeon_food(self):
        self.assertEqual(
            build_data.categorize("MOMENTS Singapore"),
            "Shopping",
        )
        self.assertEqual(
            build_data.categorize("SB125-AEON BUKIT INDAH JOHOR BAHRU"),
            "Food & dining",
        )
        self.assertEqual(
            build_data.categorize("AIF 111-ORH006 Singapore"),
            "Food & dining",
        )

    def test_reviewed_work_expense(self):
        self.assertEqual(build_data.categorize("PAYPROGLOBA 6479777769"), "Work")

    def test_researched_final_merchants(self):
        self.assertEqual(build_data.categorize("ANDO.SG SINGAPORE"), "Food & dining")
        self.assertEqual(
            build_data.categorize("PAYFORGE SERVICES ABU DHABI"),
            "Subscriptions",
        )


if __name__ == "__main__":
    unittest.main()
