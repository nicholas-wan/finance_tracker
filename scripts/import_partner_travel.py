"""Copy the travel charges another tracker paid into manual/partner_travel.json.

Some of a shared trip lands on the other person's card: flights, insurance,
a visa, an evening out. This reads the *published* dashboard data of that
tracker (its app/data/transactions.json, never its statements or manual
files) and copies the rows that can belong to a trip:

* every Travel-category row, and
* foreign-currency rows in everyday categories (food, transport, groceries,
  shopping, entertainment, personal care, healthcare), so spending on the
  ground during a shared trip can attach to it. Games, subscriptions and
  wallet top-ups priced in a foreign currency are not travel and are left out.

Nothing here decides which trip a charge belongs to. The dashboard clusters
the copied rows together with this tracker's own, by date and destination,
and shows every copied row as paid by the other person. Re-running the
script after the other tracker rebuilds refreshes the copy in place.
"""

import argparse
import json
import math
import os
import tempfile
from datetime import date
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = (
    REPO_ROOT.parent / "UOB_bank_statement_OCR" / "app" / "data" / "transactions.json"
)
DEFAULT_OUTPUT = REPO_ROOT / "manual" / "partner_travel.json"
DEFAULT_PAID_BY = "Nic"

ON_THE_GROUND_CATEGORIES = (
    "Food & dining",
    "Transport",
    "Groceries",
    "Shopping",
    "Entertainment",
    "Personal care",
    "Healthcare",
)
COPIED_FIELDS = (
    "date", "postedDate", "month", "description", "displayName", "amount", "type",
    "category", "foreign", "card",
)
JSON_INDENT = 1


def select_charges(transactions):
    """The rows worth copying, each with the reason it qualified."""
    chosen = []
    for row in transactions:
        if not isinstance(row, dict) or row.get("type") == "payment":
            continue
        category = row.get("category")
        if category == "Travel":
            reason = "travel"
        elif row.get("foreign") and category in ON_THE_GROUND_CATEGORIES:
            reason = "foreign-currency"
        else:
            continue
        chosen.append((row, reason))
    return chosen


def partner_charge(row, reason, paid_by):
    """One copied row: the other tracker's stable id, prefixed so it can never
    collide with this tracker's own ids, plus the fields the dashboard reads."""
    source_id = str(row.get("id") or "").strip()
    if not source_id:
        raise SystemExit("a %s row dated %r has no id" % (reason, row.get("date")))
    for field in ("date", "month", "description"):
        if not str(row.get(field) or "").strip():
            raise SystemExit("row %s has no %s" % (source_id, field))
    amount = row.get("amount")
    if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not math.isfinite(amount):
        raise SystemExit("row %s has an invalid amount %r" % (source_id, amount))
    if row.get("type") not in ("debit", "refund"):
        raise SystemExit("row %s has an unexpected type %r" % (source_id, row.get("type")))
    record = {"id": "%s_%s" % (paid_by.lower(), source_id), "sourceId": source_id}
    for field in COPIED_FIELDS:
        value = row.get(field)
        if value not in (None, ""):
            record[field] = value
    record["ownerTag"] = str(row.get("owner") or "")
    record["reason"] = reason
    return record


def build_manifest(source_data, source_path, paid_by, today=None):
    rows = source_data.get("transactions") if isinstance(source_data, dict) else None
    if not isinstance(rows, list):
        raise SystemExit("%s has no transactions list" % source_path)
    charges = [partner_charge(row, reason, paid_by) for row, reason in select_charges(rows)]
    charges.sort(key=lambda record: (record["date"], record["id"]))
    return {
        "source": {
            "tracker": paid_by,
            "path": str(source_path),
            "generationId": source_data.get("generationId"),
            "generatedAt": source_data.get("generatedAt"),
            "importedAt": (today or date.today()).isoformat(),
        },
        "paidBy": paid_by,
        "charges": charges,
    }


def write_atomic(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, tmp_path = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", nargs="?", default=str(DEFAULT_SOURCE),
                        help="the other tracker's app/data/transactions.json")
    parser.add_argument("--paid-by", default=DEFAULT_PAID_BY,
                        help="who paid these charges, as shown on the dashboard")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args(argv)

    source_path = Path(args.source)
    if not source_path.exists():
        raise SystemExit("%s does not exist; build the other tracker first" % source_path)
    with open(source_path, encoding="utf-8") as handle:
        source_data = json.load(handle)
    manifest = build_manifest(source_data, source_path, args.paid_by.strip() or DEFAULT_PAID_BY)
    write_atomic(args.output, json.dumps(manifest, indent=JSON_INDENT, ensure_ascii=False) + "\n")

    travel = sum(1 for record in manifest["charges"] if record["reason"] == "travel")
    foreign = len(manifest["charges"]) - travel
    print("Copied %d charge(s) paid by %s: %d travel, %d foreign-currency -> %s"
          % (len(manifest["charges"]), manifest["paidBy"], travel, foreign, args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
