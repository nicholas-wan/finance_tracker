import importlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
privacy = importlib.import_module("check_staged_privacy")

# Built at runtime so this file never contains an identifier-shaped literal
# itself; the pre-commit hook scans test sources like everything else.
ACCOUNT_A = "1" * 10
ACCOUNT_B = "2" * 10
NRIC = "S" + "1234567" + "A"
FIN = "T" + "7654321" + "Z"
DASHED = "-".join(["252", "000", "500"]) + "-1"
IDENTITY = {
    "statementHolderName": "Redacted Holder Name",
    "knownAccounts": {ACCOUNT_A: "Savings"},
    "fixedDepositAccounts": [ACCOUNT_B],
    "trustedCounterparties": ["Redacted Person", "Mei", "Long Stem*"],
}


def diff(*added, path="README.md"):
    head = "diff --git a/%s b/%s\n--- a/%s\n+++ b/%s\n@@ -1 +1 @@\n" % (path, path, path, path)
    return head + "".join("+%s\n" % line for line in added)


class StagedPrivacyTests(unittest.TestCase):
    def test_identity_terms_skip_bare_first_names(self):
        terms = {term for _, term in privacy.identity_terms(IDENTITY)}
        self.assertEqual(terms, {"Redacted Holder Name", ACCOUNT_A, ACCOUNT_B,
                                 "Redacted Person", "Long Stem"})

    def test_added_lines_with_private_values_are_reported(self):
        text = diff("Paid by REDACTED HOLDER NAME", "acct %s closed" % ACCOUNT_A,
                    "id " + NRIC, "ref " + DASHED)
        found = privacy.scan(text, privacy.identity_terms(IDENTITY))
        self.assertEqual([f[2] for f in found], [
            "statement-holder name", "own account number", "10-digit account number",
            "NRIC/FIN", "dashed account number"])
        self.assertTrue(all(f[0] == "README.md" for f in found))

    def test_removed_context_lines_and_decimals_are_ignored(self):
        text = ("--- a/x\n+++ b/x\n-old %s\n context %s\n"
                "+amount 1234567890.12\n+Mei paid\n") % (NRIC, ACCOUNT_A)
        self.assertEqual(privacy.scan(text, privacy.identity_terms(IDENTITY)), [])

    def test_shape_checks_work_without_an_identity(self):
        found = privacy.scan(diff("nric " + FIN), privacy.identity_terms({}))
        self.assertEqual([f[2] for f in found], ["NRIC/FIN"])


if __name__ == "__main__":
    unittest.main()
