"""Bulk-assign every currently unassigned transaction.

Policy requested for the existing review queue:
  - descriptions containing Deliveroo or Grab -> Shared
  - every other unassigned transaction -> Nic

Run without ``--apply`` for a preview. The applied change is atomic, removes
stable tags whose source transactions no longer exist, keeps a timestamped
backup, rebuilds the dashboard, and runs structural validation.
"""

import argparse
import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from serve import (
    BUILD_SCRIPT,
    OWNER_PATH,
    TRANSACTIONS_PATH,
    VALIDATE_SCRIPT,
    atomic_write_bytes,
    run_script,
)


SHARED_TERMS = ("DELIVEROO", "GRAB")


def load(path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def plan_assignments(rows):
    assignments = {}
    for row in rows:
        if row.get("owner") != "Untagged":
            continue
        description = row.get("description", "").upper()
        assignments[row["id"]] = (
            "Shared" if any(term in description for term in SHARED_TERMS) else "Nic"
        )
    return assignments


def atomic_write_json(path, payload):
    descriptor, temporary = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=1)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    dashboard = load(TRANSACTIONS_PATH)
    rows = dashboard.get("transactions", [])
    assignments = plan_assignments(rows)
    owner_data = load(OWNER_PATH)
    tags_by_id = owner_data.setdefault("tagsById", {})
    current_ids = {row["id"] for row in rows}
    stale_ids = sorted(set(tags_by_id) - current_ids)
    shared = sum(owner == "Shared" for owner in assignments.values())
    nic = sum(owner == "Nic" for owner in assignments.values())
    print(
        "Plan: %d currently unassigned transaction(s): %d Shared, %d Nic; "
        "prune %d stale stable-ID tag(s)."
        % (len(assignments), shared, nic, len(stale_ids))
    )
    if not args.apply:
        print("Preview only. Re-run with --apply to save, rebuild, and validate.")
        return
    if not assignments and not stale_ids:
        print("Nothing to change.")
        return

    original = OWNER_PATH.read_bytes()
    for tx_id in stale_ids:
        tags_by_id.pop(tx_id, None)
    for tx_id, owner in assignments.items():
        tags_by_id[tx_id] = owner
    owner_data["tagsById"] = dict(sorted(tags_by_id.items()))
    backup = Path(
        str(OWNER_PATH) + ".pre_bulk_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".bak"
    )
    shutil.copy2(OWNER_PATH, backup)
    atomic_write_json(OWNER_PATH, owner_data)
    try:
        run_script(BUILD_SCRIPT)
        run_script(VALIDATE_SCRIPT)
        refreshed = load(TRANSACTIONS_PATH)
        remaining = [
            row for row in refreshed.get("transactions", [])
            if row.get("owner") == "Untagged"
        ]
        if remaining:
            raise RuntimeError(
                "%d transaction(s) remained unassigned after rebuild" % len(remaining)
            )
    except Exception:
        atomic_write_bytes(OWNER_PATH, original)
        run_script(BUILD_SCRIPT)
        raise

    print(
        "Applied %d stable owner tag(s), pruned %d stale tag(s), and validated "
        "the rebuilt dashboard." % (len(assignments), len(stale_ids))
    )
    print("Pre-bulk backup:", backup)


if __name__ == "__main__":
    main()
