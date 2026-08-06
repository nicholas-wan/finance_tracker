import importlib
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
assign_unassigned = importlib.import_module("assign_unassigned")


class AssignmentPolicyTests(unittest.TestCase):
    def test_shared_merchants_and_nic_remainder(self):
        rows = [
            {"id": "tx_1", "owner": "Untagged", "description": "Deliveroo Singapore"},
            {"id": "tx_2", "owner": "Untagged", "description": "Grab* Ride"},
            {"id": "tx_3", "owner": "Untagged", "description": "Other merchant"},
            {"id": "tx_4", "owner": "Yx", "description": "Grab* Already assigned"},
        ]
        self.assertEqual(
            assign_unassigned.plan_assignments(rows),
            {"tx_1": "Shared", "tx_2": "Shared", "tx_3": "Nic"},
        )


if __name__ == "__main__":
    unittest.main()
