import importlib
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
build_data = importlib.import_module("build_data")


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
