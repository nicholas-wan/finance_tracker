"""Bulk-assign every currently unassigned transaction.

Policy requested for the existing review queue:
  - descriptions containing Deliveroo or Grab -> Shared
  - every other unassigned transaction -> Nic

Run without ``--apply`` for a preview. The applied change is atomic, removes
stable tags whose source transactions no longer exist, records one audit entry
covering the whole batch, keeps a timestamped backup of both manual files,
rebuilds the dashboard, and runs structural validation.

The script refuses to run while the dashboard server is up: serve.py holds a
write lock this process knows nothing about, so a bulk write here and a save
from the dashboard could clobber each other.
"""

import argparse
import json
import shutil
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from serve import (
    AUDIT_PATH,
    BUILD_SCRIPT,
    OWNER_PATH,
    TRANSACTIONS_PATH,
    VALIDATE_SCRIPT,
    append_audit_entry,
    atomic_write_bytes,
    atomic_write_json,
    make_audit_entry,
    run_script,
)


SHARED_TERMS = ("DELIVEROO", "GRAB")
DEFAULT_PORT = 3402
STATUS_TIMEOUT = 1.0
SERVER_RUNNING_MESSAGE = (
    "The dashboard server is running; stop it first or tag from the dashboard."
)


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


def server_is_running(port=DEFAULT_PORT, timeout=STATUS_TIMEOUT):
    """True when something answers /api/status on the loopback port.

    Any HTTP answer counts, including an error status: a server that replies at
    all owns the manual/ files, whatever it thinks of the request.
    """
    url = "http://127.0.0.1:%d/api/status" % port
    try:
        with urllib.request.urlopen(url, timeout=timeout):
            return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def build_audit_entry(rows, assignments, stale_ids):
    """One entry for the whole batch, in the shape /api/audit-history renders."""
    changes = []
    if assignments:
        owners = sorted(set(assignments.values()))
        changes.append({
            "field": "Owner",
            "before": "Untagged",
            "after": ", ".join(owners),
        })
    if stale_ids:
        changes.append({
            "field": "Pruned stale tags",
            "before": str(len(stale_ids)),
            "after": "0",
        })
    if not changes:
        return None
    by_id = {row.get("id"): row for row in rows}
    tx_ids = sorted(assignments)
    # A batch has no single subject row, so the entry hangs off the first
    # transaction it touched and carries every id it changed.
    current = by_id.get(tx_ids[0], {}) if tx_ids else {}
    return make_audit_entry(
        current,
        "Bulk-assigned owners for %d transactions" % len(assignments),
        changes,
        transaction_ids=tx_ids,
    )


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help="port the dashboard server would be listening on")
    parser.add_argument("--force", action="store_true",
                        help="run even when the dashboard server answers")
    args = parser.parse_args(argv)

    if not args.force and server_is_running(args.port):
        print(SERVER_RUNNING_MESSAGE, file=sys.stderr)
        raise SystemExit(1)

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

    paths = (OWNER_PATH, AUDIT_PATH)
    originals = {path: path.read_bytes() for path in paths}
    for tx_id in stale_ids:
        tags_by_id.pop(tx_id, None)
    for tx_id, owner in assignments.items():
        tags_by_id[tx_id] = owner
    owner_data["tagsById"] = dict(sorted(tags_by_id.items()))
    audit_data = json.loads(originals[AUDIT_PATH].decode("utf-8"))
    append_audit_entry(audit_data, build_audit_entry(rows, assignments, stale_ids))
    payloads = {OWNER_PATH: owner_data, AUDIT_PATH: audit_data}

    # One timestamp for both backups: a mixed-generation pair cannot be restored
    # by hand into a state the repo was ever actually in.
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backups = {
        path: Path("%s.pre_bulk_%s.bak" % (path, stamp)) for path in paths
    }
    try:
        for path in paths:
            shutil.copy2(path, backups[path])
        for path in paths:
            atomic_write_json(path, payloads[path])
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
        # Roll back the audit trail too: an entry describing owner tags that
        # were just reverted is worse than no entry at all.
        for path in paths:
            atomic_write_bytes(path, originals[path])
        run_script(BUILD_SCRIPT)
        raise

    print(
        "Applied %d stable owner tag(s), pruned %d stale tag(s), and validated "
        "the rebuilt dashboard." % (len(assignments), len(stale_ids))
    )
    for path in paths:
        print("Pre-bulk backup:", backups[path])


if __name__ == "__main__":
    main()
