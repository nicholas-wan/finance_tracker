import importlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
build_data = importlib.import_module("build_data")


class NegativePatternTests(unittest.TestCase):
    """A rule token inside an unrelated business name is vetoed by the rule's
    exclusion phrases, without changing the ordinary matches."""

    def test_vetoed_phrases_do_not_take_the_rule(self):
        self.assertNotEqual(build_data.categorize("GIANT LEAP CLIMBING SG"), "Groceries")
        self.assertNotEqual(build_data.categorize("THE COFFEE TABLE CO SG"), "Food & dining")
        self.assertNotEqual(build_data.categorize("KITCHENAID FOOD PROCESSOR"), "Food & dining")

    def test_ordinary_matches_survive(self):
        self.assertEqual(build_data.categorize("GIANT-TAMPINES SG"), "Groceries")
        self.assertEqual(build_data.categorize("COMMON MAN COFFEE ROASTERS"), "Food & dining")

    def test_every_rule_has_a_valid_shape(self):
        for rule in build_data.CATEGORY_RULES:
            self.assertIn(len(rule), (2, 3))
            self.assertTrue(all(isinstance(p, str) and p for p in rule[1]))
            if len(rule) == 3:
                self.assertTrue(all(isinstance(p, str) and p for p in rule[2]))


if __name__ == "__main__":
    unittest.main()
