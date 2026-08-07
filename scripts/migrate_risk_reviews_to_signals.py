"""One-shot migration: flat recognizedIds -> per-signal recognition entries.

The old file recorded which *transactions* had been acknowledged, so any future
check those rows tripped was suppressed without ever being shown. The new file
records which *signals* were acknowledged: a hash over the rows plus the checks
that fired.

The conversion re-runs the current detector over the rows in the built
dashboard data and marks a signal recognized when every row it covers was in
the old recognizedIds list. That reproduces exactly what the old semantics were
suppressing at the moment of migration, and nothing more.

Run once:

    python scripts/migrate_risk_reviews_to_signals.py           # dry run
    python scripts/migrate_risk_reviews_to_signals.py --write
"""

import argparse
import json
import os
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from build_data import merchant_key  # noqa: E402
from risk_checks import detect_risks  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANUAL_DIR = os.path.join(REPO_ROOT, "manual")
RISK_PATH = os.path.join(MANUAL_DIR, "risk_reviews.json")
TRANSACTIONS_PATH = os.path.join(REPO_ROOT, "app", "data", "transactions.json")
TAG = "risksignals"


def load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def backup_path(path, tag):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return "%s.pre_%s_%s.bak" % (path, tag, stamp)


def describe(row):
    return "%s %s" % (row.get("date") or row.get("month"), row.get("description"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true",
                        help="write the converted file (default is a dry run)")
    args = parser.parse_args()

    risk_data = load(RISK_PATH)
    old_ids = set(risk_data.get("recognizedIds") or [])
    if risk_data.get("recognizedSignals") is not None and not old_ids:
        print("risk_reviews.json is already in the per-signal format; nothing to do.")
        return 0

    built = load(TRANSACTIONS_PATH)
    rows = built.get("transactions", [])
    by_id = {row["id"]: row for row in rows}

    # Signals as the *previous* build recorded them, for the audit report.
    previous = {}
    for row in rows:
        risk = row.get("risk")
        if isinstance(risk, dict) and risk.get("groupIds"):
            previous.setdefault(tuple(sorted(risk["groupIds"])), risk)

    # detect_risks strips and rewrites the `risk` key on the rows it is given.
    signals = detect_risks(rows, merchant_key, [])["signals"]

    entries = []
    recognized_at = datetime.now().astimezone().isoformat(timespec="seconds")
    covered = set()
    for signal in signals:
        risk = signal["risk"]
        ids = list(risk["groupIds"])
        if not ids or not all(tx_id in old_ids for tx_id in ids):
            continue
        covered.update(ids)
        entries.append({
            "key": risk["key"],
            "ids": ids,
            "checks": list(risk["checks"]),
            "recognizedAt": recognized_at,
        })
    entries.sort(key=lambda entry: entry["key"])

    print("Old recognizedIds: %d" % len(old_ids))
    print("Signals in the rebuilt detector: %d" % len(signals))
    print("Converted to recognized signal entries: %d" % len(entries))
    print()
    print("MAPPING (recognized signal -> rows and checks)")
    for entry in sorted(entries, key=lambda e: describe(by_id[e["ids"][0]])):
        head = by_id[entry["ids"][0]]
        print("  %-52s %s" % (describe(head)[:52], ",".join(entry["checks"])))
        print("    key %s  rows %d" % (entry["key"][:16] + "...", len(entry["ids"])))

    orphans = sorted(old_ids - covered)
    if orphans:
        print()
        print("UNMAPPED old recognizedIds (%d) - these rows no longer belong to any"
              % len(orphans))
        print("recognized signal, so their acknowledgement is dropped:")
        for tx_id in orphans:
            row = by_id.get(tx_id)
            print("  %s  %s" % (tx_id, describe(row) if row else "(not in the build)"))

    previous_recognized = [
        ids for ids, risk in previous.items() if risk.get("recognized")
    ]
    new_id_sets = {tuple(sorted(entry["ids"])) for entry in entries}
    lost = [ids for ids in previous_recognized if ids not in new_id_sets]
    print()
    print("Previously recognized signal groups: %d" % len(previous_recognized))
    if lost:
        print("Groups whose row set no longer exists as a signal (%d):" % len(lost))
        for ids in lost:
            row = by_id.get(ids[0])
            print("  %s" % (describe(row) if row else ids[0]))
    else:
        print("Every previously recognized group maps to a signal entry.")

    if not args.write:
        print()
        print("Dry run. Re-run with --write to save.")
        return 0

    payload = dict(risk_data)
    payload.pop("recognizedIds", None)
    payload["recognizedSignals"] = entries

    destination = backup_path(RISK_PATH, TAG)
    shutil.copy2(RISK_PATH, destination)
    temporary = RISK_PATH + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1)
        handle.write("\n")
    os.replace(temporary, RISK_PATH)
    print()
    print("Backed up  -> %s" % os.path.relpath(destination, REPO_ROOT))
    print("Wrote      -> %s (%d recognized signals)"
          % (os.path.relpath(RISK_PATH, REPO_ROOT), len(entries)))
    print("Now run: python scripts/build_data.py && python scripts/validate_data.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
