"""Refuse a commit whose staged diff adds private identifiers.

Run by the pre-commit hook that scripts/install_hooks.ps1 installs. It reads
the private identity file (never committed) so the check knows the real
statement-holder name, own-account numbers and trusted counterparties, and
adds two shape checks that need no identity at all: an NRIC/FIN and a
10-digit-or-dashed account number. Only added lines are scanned. Bypass a
deliberate exception with ``git commit --no-verify`` after reading the
finding.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
IDENTITY_PATH = REPO_ROOT / "manual" / "identity.json"
# Shapes worth stopping on even when no identity file is present.
SHAPE_PATTERNS = [
    ("NRIC/FIN", re.compile(r"\b[STFGM]\d{7}[A-Z]\b")),
    ("10-digit account number", re.compile(r"(?<![\d.])\d{10}(?![\d.])")),
    ("dashed account number", re.compile(r"\b\d{3}-\d{3}-\d{3}-\d\b")),
]


def identity_terms(identity):
    """Every private string the diff must not contain, with a label each."""
    terms = []
    name = " ".join(str(identity.get("statementHolderName") or "").split())
    if name:
        terms.append(("statement-holder name", name))
    for number in identity.get("knownAccounts", {}) or {}:
        terms.append(("own account number", str(number)))
    for number in identity.get("fixedDepositAccounts", []) or []:
        terms.append(("fixed-deposit account number", str(number)))
    for counterparty in identity.get("trustedCounterparties", []) or []:
        text = str(counterparty).rstrip("*").strip()
        # Single words such as a bare first name are ordinary prose; only a
        # full name (two or more words) identifies a person.
        if len(text.split()) >= 2:
            terms.append(("trusted counterparty", text))
    return terms


def scan(diff_text, terms):
    """Return (file, line, label, match) for every private hit on an added line."""
    findings = []
    current = None
    for raw in diff_text.splitlines():
        if raw.startswith("+++ "):
            current = raw[4:].strip()
            if current.startswith("b/"):
                current = current[2:]
            continue
        if not raw.startswith("+") or raw.startswith("+++"):
            continue
        line = raw[1:]
        upper = line.upper()
        for label, term in terms:
            if term.upper() in upper:
                findings.append((current, line.strip(), label, term))
        for label, pattern in SHAPE_PATTERNS:
            for match in pattern.findall(line):
                findings.append((current, line.strip(), label, match))
    return findings


def staged_diff():
    result = subprocess.run(
        ["git", "diff", "--cached", "--unified=0", "--no-color", "--", ".", ":(exclude)*.pdf"],
        cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return result.stdout


def main():
    identity = {}
    if IDENTITY_PATH.exists():
        try:
            identity = json.loads(IDENTITY_PATH.read_text(encoding="utf-8"))
        except ValueError:
            print("check_staged_privacy: manual/identity.json is not valid JSON; shape checks only.")
    findings = scan(staged_diff(), identity_terms(identity))
    if not findings:
        return 0
    print("Commit refused: the staged diff adds private identifiers.")
    for path, line, label, match in findings:
        shown = line if len(line) <= 120 else line[:117] + "..."
        print("  %s: %s (%s)\n      %s" % (path or "?", label, match, shown))
    print("Move the value into a Git-ignored manual/ file, or if this is deliberate,"
          " commit again with --no-verify.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
