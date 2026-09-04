"""Serve the dashboard and safely persist owner, remark, and review decisions.

The server binds to 127.0.0.1 only. Changes are written atomically, then the
dashboard data is rebuilt and validated. A failed build restores the prior file
and rebuilds the previous state.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = REPO_ROOT / "app"
OWNER_PATH = REPO_ROOT / "manual" / "owner_tags.json"
RISK_REVIEW_PATH = REPO_ROOT / "manual" / "risk_reviews.json"
ACCOUNT_REVIEW_PATH = REPO_ROOT / "manual" / "account_reviews.json"
REMARK_PATH = REPO_ROOT / "manual" / "transaction_remarks.json"
OVERRIDE_PATH = REPO_ROOT / "manual" / "transaction_overrides.json"
AUDIT_PATH = REPO_ROOT / "manual" / "audit_history.json"
TRANSACTIONS_PATH = APP_DIR / "data" / "transactions.json"
ACCOUNT_TRANSACTIONS_PATH = APP_DIR / "data" / "account_transactions.json"
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build_data.py"
VALIDATE_SCRIPT = REPO_ROOT / "scripts" / "validate_data.py"
ALLOWED_OWNERS = {"Nic", "Shared", "Yx", "Untagged"}
# One-click tagging posts whole merchant groups and whole filtered pages.
# The cap keeps a batch inside the 4KB request-body limit do_POST enforces.
MAX_OWNER_BATCH = 100
ALLOWED_CATEGORIES = {
    "Bills & utilities",
    "Entertainment",
    "Fees & charges",
    "Fixed deposit",
    "Food & dining",
    "Games",
    "Groceries",
    "Healthcare",
    "Home & furnishings",
    "Insurance",
    "Investment",
    "Other",
    "Payment",
    "Personal care",
    "Pet care",
    "Rebates",
    "Retirement (SRS)",
    "Shopping",
    "Sports & fitness",
    "Subscriptions",
    "Transport",
    "Travel",
    "Work",
}
WRITE_LOCK = threading.Lock()
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
REPLACE_ATTEMPTS = 5
REPLACE_RETRY_DELAY = 0.05


def load_json(path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def atomic_write_bytes(path, content):
    descriptor, temporary = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        # On Windows os.replace fails while another thread still holds the
        # destination open. That window is short, so retry before giving up.
        for attempt in range(REPLACE_ATTEMPTS):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(REPLACE_RETRY_DELAY)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_write_json(path, payload):
    content = (json.dumps(payload, indent=1) + "\n").encode("utf-8")
    atomic_write_bytes(path, content)


def snapshot_backups(paths):
    # Every .bak is taken before any live file changes. Interleaving backup and
    # write per file left mixed-generation backups when a save died midway, so
    # "copy the .bak files back" could reconstruct a state that never existed.
    for path in paths:
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))


def restore_originals(paths, originals):
    """Restore every path, even when one restore fails.

    A single unguarded loop used to abandon the remaining files on the first
    PermissionError, leaving manual/ half old and half rejected-new with no
    record of which was which. Returns the names that could not be restored.
    """
    failed = []
    for path in paths:
        try:
            atomic_write_bytes(path, originals[path])
        except Exception:
            failed.append(path.name)
    return failed


def make_audit_entry(transaction, action, changes, transaction_ids=None):
    if not changes:
        return None
    return {
        "id": "audit_" + uuid.uuid4().hex[:16],
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "action": action,
        "transactionId": transaction.get("id"),
        "transactionIds": sorted(transaction_ids or [transaction.get("id")]),
        "transactionDate": transaction.get("date"),
        "description": transaction.get("displayName") or transaction.get("description"),
        "statementDescription": transaction.get("description"),
        "amount": transaction.get("amount"),
        "changes": changes,
    }


def append_audit_entry(audit_data, entry):
    entries = audit_data.setdefault("entries", [])
    if entry:
        entries.append(entry)
    return audit_data


def validate_owner_request(payload, transactions):
    """Resolve a posted owner decision to rows in the current build.

    ``id`` tags one row; ``ids`` tags a batch under a single rebuild, which is
    what the ledger's merchant-group and "tag everything shown" controls post.
    Both forms return a list so the save path never has to branch on shape.
    """
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object.")
    owner = payload.get("owner")
    if "ids" in payload:
        ids = payload.get("ids")
        if not isinstance(ids, list) or not ids or len(ids) > MAX_OWNER_BATCH:
            raise ValueError(
                "Between 1 and %d transaction IDs are required." % MAX_OWNER_BATCH)
    else:
        ids = [payload.get("id")]
    if any(not isinstance(tx_id, str) or not tx_id.startswith("tx_")
           for tx_id in ids):
        raise ValueError("A valid transaction ID is required.")
    if owner not in ALLOWED_OWNERS:
        raise ValueError("Owner must be Nic, Shared, Yx, or Unassigned.")
    # Deduplicated, so a repeated ID cannot double-count in the audit row list.
    ids = sorted(set(ids))
    if any(tx_id not in transactions for tx_id in ids):
        raise ValueError("That transaction is not present in the current build.")
    return ids, owner


def is_signal_key(value):
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def validate_risk_review_request(payload, transactions):
    """Resolve a posted review decision to one signal in the current build.

    Decisions are recorded against a signal - the rows plus the checks that
    fired - not against the rows alone, so the caller's key must still match
    what the current build computes. A stale page whose signal has since gained
    a new reason is rejected rather than silently acknowledging the new reason.
    """
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object.")
    ids = payload.get("ids")
    recognized = payload.get("recognized")
    key = payload.get("key")
    if (not isinstance(ids, list) or not ids or len(ids) > 50 or
            any(not isinstance(tx_id, str) or not tx_id.startswith("tx_")
                for tx_id in ids)):
        raise ValueError("One current transaction-check group is required.")
    if recognized not in (True, False):
        raise ValueError("Recognized must be true or false.")
    if key is not None and not is_signal_key(key):
        raise ValueError("The transaction-check signal key is malformed.")
    if any(tx_id not in transactions for tx_id in ids):
        raise ValueError("A transaction is not present in the current build.")

    requested = frozenset(ids)
    signal = None
    for row in transactions.values():
        risk = row.get("risk")
        if not isinstance(risk, dict) or not is_signal_key(risk.get("key")):
            continue
        if frozenset(risk.get("groupIds") or []) == requested:
            signal = risk
            break
    if signal is None:
        raise ValueError("That transaction-check group is not present in the current build.")
    if key is not None and key != signal["key"]:
        raise ValueError(
            "That transaction check changed since this page loaded. "
            "Reload the dashboard and review it again."
        )
    return sorted(requested), recognized, signal["key"], list(signal.get("checks") or [])


def validate_account_review_request(payload, transactions):
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object.")
    tx_id = payload.get("id")
    reviewed = payload.get("reviewed")
    if not isinstance(tx_id, str) or not tx_id.startswith("tx_"):
        raise ValueError("A valid account transaction ID is required.")
    if tx_id not in transactions:
        raise ValueError("That account transaction is not present in the current statements.")
    if reviewed not in (True, False):
        raise ValueError("Reviewed must be true or false.")
    return tx_id, reviewed


def validate_remark_request(payload, transactions):
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object.")
    tx_id = payload.get("id")
    remark = payload.get("remark")
    if not isinstance(tx_id, str) or not tx_id.startswith("tx_"):
        raise ValueError("A valid transaction ID is required.")
    if tx_id not in transactions:
        raise ValueError("That transaction is not present in the current build.")
    if not isinstance(remark, str):
        raise ValueError("Remark must be text.")
    remark = " ".join(remark.split())
    if len(remark) > 240:
        raise ValueError("Remark must be 240 characters or fewer.")
    return tx_id, remark


def validate_transaction_detail_request(payload, transactions):
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object.")
    tx_id = payload.get("id")
    owner = payload.get("owner")
    category = payload.get("category")
    display_name = payload.get("displayName")
    remark = payload.get("remark")
    if not isinstance(tx_id, str) or not tx_id.startswith("tx_"):
        raise ValueError("A valid transaction ID is required.")
    if tx_id not in transactions:
        raise ValueError("That transaction is not present in the current build.")
    if owner not in ALLOWED_OWNERS:
        raise ValueError("Owner must be Nic, Shared, Yx, or Unassigned.")
    if category not in ALLOWED_CATEGORIES:
        raise ValueError("Choose a supported transaction category.")
    if not isinstance(display_name, str):
        raise ValueError("Display name must be text.")
    display_name = " ".join(display_name.split())
    if len(display_name) > 100:
        raise ValueError("Display name must be 100 characters or fewer.")
    if not isinstance(remark, str):
        raise ValueError("Remark must be text.")
    remark = " ".join(remark.split())
    if len(remark) > 240:
        raise ValueError("Remark must be 240 characters or fewer.")
    return tx_id, owner, category, display_name, remark


def run_script(script):
    completed = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    output = "\n".join(
        part.strip() for part in (completed.stdout, completed.stderr) if part.strip()
    )
    if completed.returncode:
        raise RuntimeError(output or "%s exited with %d" % (script.name, completed.returncode))
    return output


def save_owner(tx_ids, owner):
    """Tag one row or a batch of rows, under a single rebuild and audit entry."""
    if isinstance(tx_ids, str):
        tx_ids = [tx_ids]
    with WRITE_LOCK:
        transaction_data = load_json(TRANSACTIONS_PATH)
        transactions = {
            row.get("id"): row for row in transaction_data.get("transactions", [])
        }
        tx_ids, owner = validate_owner_request(
            {"ids": list(tx_ids), "owner": owner}, transactions)
        rows = [transactions[tx_id] for tx_id in tx_ids]
        current = rows[0]
        paths = (OWNER_PATH, AUDIT_PATH)
        originals = {path: path.read_bytes() for path in paths}
        owner_data = json.loads(originals[OWNER_PATH].decode("utf-8"))
        tags = owner_data.setdefault("tagsById", {})
        # "Untagged" means "drop my tag and let the merchant fallbacks decide
        # again", not "pin this row as unassigned". Writing the literal value
        # would shadow manual/owner_rules.json for the life of the transaction.
        for tx_id in tx_ids:
            if owner == "Untagged":
                tags.pop(tx_id, None)
            else:
                tags[tx_id] = owner
        owner_data["tagsById"] = dict(sorted(tags.items()))
        changes = []
        # A batch spans rows that may have started from different owners, so the
        # audit records the set it moved from rather than one row's value.
        previous = sorted({row.get("owner") or "Unassigned" for row in rows})
        if previous != [owner]:
            changes.append({
                "field": "Owner",
                "before": ", ".join(previous),
                "after": owner,
            })
        unconfirmed = sorted({
            row.get("ownerSource") or "unassigned" for row in rows
            if row.get("ownerSource") != "exact-id"
        })
        if unconfirmed:
            changes.append({
                "field": "Owner source",
                "before": ", ".join(unconfirmed),
                "after": "confirmed manually",
            })
        audit_data = json.loads(originals[AUDIT_PATH].decode("utf-8"))
        append_audit_entry(
            audit_data,
            make_audit_entry(
                current,
                "Updated owner" if len(tx_ids) == 1
                else "Updated owner for %d transactions" % len(tx_ids),
                changes,
                transaction_ids=tx_ids,
            ),
        )
        payloads = {OWNER_PATH: owner_data, AUDIT_PATH: audit_data}

        try:
            snapshot_backups(paths)
            for path in paths:
                atomic_write_json(path, payloads[path])
            build_output = run_script(BUILD_SCRIPT)
            validation_output = run_script(VALIDATE_SCRIPT)
            refreshed = load_json(TRANSACTIONS_PATH)
            rebuilt = {
                row.get("id"): row for row in refreshed.get("transactions", [])
            }
            # Every row in the batch must have landed. A partially applied batch
            # is rolled back whole, so the page never shows some rows tagged.
            updated_rows = [rebuilt.get(tx_id) for tx_id in tx_ids]
            # Clearing a tag hands the row back to the merchant fallbacks, which
            # may legitimately name an owner; what must be gone is the stable-ID
            # tag itself. Setting an owner must land as that exact owner.
            def applied(row):
                if row is None:
                    return False
                if owner == "Untagged":
                    return row.get("ownerSource") != "exact-id"
                return row.get("owner") == owner
            if not all(applied(row) for row in updated_rows):
                raise RuntimeError("The rebuilt dashboard did not apply the saved owner.")
            updated = updated_rows[0]
        except Exception as save_error:
            failed_restores = restore_originals(paths, originals)
            if failed_restores:
                # Keep the root cause visible: hiding it behind the restore
                # message left the user with no idea why the save failed.
                raise RuntimeError(
                    "Save failed (%s) AND these manual files could not be "
                    "restored, so they may still hold the rejected edit: %s. "
                    "Copy their .bak files back before saving again."
                    % (save_error, ", ".join(failed_restores))
                )
            try:
                run_script(BUILD_SCRIPT)
            except Exception as rollback_error:
                raise RuntimeError(
                    "Save failed and the prior owner file was restored, but rebuilding "
                    "that prior state also failed: %s" % rollback_error
                )
            raise

        return {
            "ok": True,
            # "transaction" stays for single-row callers; "transactions" carries
            # the whole batch so the page can repaint every row it just tagged.
            "transaction": {
                "id": updated["id"],
                "owner": updated["owner"],
                "ownerSource": updated.get("ownerSource"),
            },
            "transactions": [
                {
                    "id": row["id"],
                    "owner": row["owner"],
                    "ownerSource": row.get("ownerSource"),
                }
                for row in updated_rows
            ],
            "quality": refreshed.get("quality", {}),
            "build": build_output.splitlines()[0] if build_output else "Build completed.",
            "validation": validation_output.splitlines()[0]
            if validation_output else "Validation completed.",
        }


def save_risk_review(ids, recognized, key=None):
    with WRITE_LOCK:
        transaction_data = load_json(TRANSACTIONS_PATH)
        transactions = {
            row.get("id"): row for row in transaction_data.get("transactions", [])
        }
        ids, recognized, key, checks = validate_risk_review_request(
            {"ids": ids, "recognized": recognized, "key": key}, transactions
        )
        current = next(
            (row for row in transactions.values()
             if row.get("risk", {}).get("key") == key
             and row.get("risk", {}).get("primary", True)),
            None,
        )
        if current is None:
            # Bare next() here let StopIteration escape as an empty 500.
            raise ValueError(
                "That transaction check has no primary row in the current build. "
                "Reload the dashboard and review it again."
            )
        paths = (RISK_REVIEW_PATH, AUDIT_PATH)
        originals = {path: path.read_bytes() for path in paths}
        risk_data = json.loads(originals[RISK_REVIEW_PATH].decode("utf-8"))
        stored = risk_data.get("recognizedSignals")
        entries = [
            entry for entry in (stored if isinstance(stored, list) else [])
            if isinstance(entry, dict) and entry.get("key") != key
        ]
        if recognized:
            entries.append({
                "key": key,
                "ids": ids,
                "checks": checks,
                "recognizedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
            })
        entries.sort(key=lambda entry: entry.get("key") or "")
        risk_data["recognizedSignals"] = entries
        # The flat pre-signal list is not consulted any more; leaving it behind
        # would look like a live setting.
        risk_data.pop("recognizedIds", None)
        audit_data = json.loads(originals[AUDIT_PATH].decode("utf-8"))
        before = bool(current.get("risk", {}).get("recognized"))
        changes = []
        if before != recognized:
            changes.append({
                "field": "Transaction check",
                "before": "Recognized" if before else "Needs review",
                "after": "Recognized" if recognized else "Needs review",
            })
        append_audit_entry(
            audit_data,
            make_audit_entry(
                current,
                "Recognized transaction check" if recognized
                else "Reopened transaction check",
                changes,
                ids,
            ),
        )
        payloads = {RISK_REVIEW_PATH: risk_data, AUDIT_PATH: audit_data}

        try:
            snapshot_backups(paths)
            for path in paths:
                atomic_write_json(path, payloads[path])
            build_output = run_script(BUILD_SCRIPT)
            validation_output = run_script(VALIDATE_SCRIPT)
            refreshed = load_json(TRANSACTIONS_PATH)
            updated = next(
                (
                    row for row in refreshed.get("transactions", [])
                    if row.get("risk", {}).get("key") == key
                ),
                None,
            )
            if (not updated or
                    updated.get("risk", {}).get("recognized") is not recognized):
                raise RuntimeError("The rebuilt dashboard did not apply the review decision.")
        except Exception as save_error:
            failed_restores = restore_originals(paths, originals)
            if failed_restores:
                # Keep the root cause visible: hiding it behind the restore
                # message left the user with no idea why the save failed.
                raise RuntimeError(
                    "Save failed (%s) AND these manual files could not be "
                    "restored, so they may still hold the rejected edit: %s. "
                    "Copy their .bak files back before saving again."
                    % (save_error, ", ".join(failed_restores))
                )
            try:
                run_script(BUILD_SCRIPT)
            except Exception as rollback_error:
                raise RuntimeError(
                    "Save failed and the prior review file was restored, but rebuilding "
                    "that prior state also failed: %s" % rollback_error
                )
            raise

        return {
            "ok": True,
            "risk": updated.get("risk", {}),
            "quality": refreshed.get("quality", {}),
            "build": build_output.splitlines()[0] if build_output else "Build completed.",
            "validation": validation_output.splitlines()[0]
            if validation_output else "Validation completed.",
        }


def save_remark(tx_id, remark):
    with WRITE_LOCK:
        transaction_data = load_json(TRANSACTIONS_PATH)
        transactions = {
            row.get("id"): row for row in transaction_data.get("transactions", [])
        }
        tx_id, remark = validate_remark_request(
            {"id": tx_id, "remark": remark}, transactions
        )
        current = transactions[tx_id]
        paths = (REMARK_PATH, AUDIT_PATH)
        originals = {path: path.read_bytes() for path in paths}
        remark_data = json.loads(originals[REMARK_PATH].decode("utf-8"))
        remarks = remark_data.setdefault("remarksById", {})
        if remark:
            remarks[tx_id] = remark
        else:
            remarks.pop(tx_id, None)
        remark_data["remarksById"] = dict(sorted(remarks.items()))
        changes = []
        if current.get("remark", "") != remark:
            changes.append({
                "field": "Remarks",
                "before": current.get("remark", ""),
                "after": remark,
            })
        audit_data = json.loads(originals[AUDIT_PATH].decode("utf-8"))
        append_audit_entry(
            audit_data,
            make_audit_entry(current, "Updated remarks", changes),
        )
        payloads = {REMARK_PATH: remark_data, AUDIT_PATH: audit_data}

        try:
            snapshot_backups(paths)
            for path in paths:
                atomic_write_json(path, payloads[path])
            build_output = run_script(BUILD_SCRIPT)
            validation_output = run_script(VALIDATE_SCRIPT)
            refreshed = load_json(TRANSACTIONS_PATH)
            updated = next(
                (row for row in refreshed.get("transactions", []) if row.get("id") == tx_id),
                None,
            )
            if not updated or updated.get("remark", "") != remark:
                raise RuntimeError("The rebuilt dashboard did not apply the saved remark.")
        except Exception as save_error:
            failed_restores = restore_originals(paths, originals)
            if failed_restores:
                # Keep the root cause visible: hiding it behind the restore
                # message left the user with no idea why the save failed.
                raise RuntimeError(
                    "Save failed (%s) AND these manual files could not be "
                    "restored, so they may still hold the rejected edit: %s. "
                    "Copy their .bak files back before saving again."
                    % (save_error, ", ".join(failed_restores))
                )
            try:
                run_script(BUILD_SCRIPT)
            except Exception as rollback_error:
                raise RuntimeError(
                    "Save failed and the prior remark file was restored, but rebuilding "
                    "that prior state also failed: %s" % rollback_error
                )
            raise

        return {
            "ok": True,
            "transaction": {
                "id": updated["id"],
                "remark": updated.get("remark", ""),
            },
            "quality": refreshed.get("quality", {}),
            "build": build_output.splitlines()[0] if build_output else "Build completed.",
            "validation": validation_output.splitlines()[0]
            if validation_output else "Validation completed.",
        }


def save_transaction_detail(tx_id, owner, category, display_name, remark):
    with WRITE_LOCK:
        transaction_data = load_json(TRANSACTIONS_PATH)
        transactions = {
            row.get("id"): row for row in transaction_data.get("transactions", [])
        }
        tx_id, owner, category, display_name, remark = (
            validate_transaction_detail_request(
                {
                    "id": tx_id,
                    "owner": owner,
                    "category": category,
                    "displayName": display_name,
                    "remark": remark,
                },
                transactions,
            )
        )
        current = transactions[tx_id]
        paths = (OWNER_PATH, REMARK_PATH, OVERRIDE_PATH, AUDIT_PATH)
        originals = {path: path.read_bytes() for path in paths}

        owner_data = json.loads(originals[OWNER_PATH].decode("utf-8"))
        tags = owner_data.setdefault("tagsById", {})
        # Only a real owner change may write a tag. Stamping the value the form
        # happened to be showing turned a category-only edit into an explicit
        # tag, and an explicit "Untagged" then permanently shadows the merchant
        # fallbacks in manual/owner_rules.json.
        owner_changed = current.get("owner", "Untagged") != owner
        if owner_changed:
            # "Untagged" in the drawer means exactly what the owner chip means:
            # drop my stable-ID tag and let manual/owner_rules.json decide
            # again. Writing the literal value pinned the row as unassigned.
            if owner == "Untagged":
                tags.pop(tx_id, None)
            else:
                tags[tx_id] = owner
        owner_data["tagsById"] = dict(sorted(tags.items()))

        remark_data = json.loads(originals[REMARK_PATH].decode("utf-8"))
        remarks = remark_data.setdefault("remarksById", {})
        if remark:
            remarks[tx_id] = remark
        else:
            remarks.pop(tx_id, None)
        remark_data["remarksById"] = dict(sorted(remarks.items()))

        override_data = json.loads(originals[OVERRIDE_PATH].decode("utf-8"))
        overrides = override_data.setdefault("overridesById", {})
        override = {}
        if category != current.get("ruleCategory", current.get("category")):
            override["category"] = category
        if display_name:
            override["displayName"] = display_name
        if override:
            overrides[tx_id] = override
        else:
            overrides.pop(tx_id, None)
        override_data["overridesById"] = dict(sorted(overrides.items()))

        changes = []
        comparisons = (
            ("Display name", current.get("displayName", ""), display_name),
            ("Category", current.get("category", ""), category),
            ("Owner", current.get("owner", "Unassigned"), owner),
            ("Remarks", current.get("remark", ""), remark),
        )
        for field, before, after in comparisons:
            if before != after:
                changes.append({"field": field, "before": before, "after": after})
        if owner_changed and current.get("ownerSource") != "exact-id":
            changes.append({
                "field": "Owner source",
                "before": current.get("ownerSource", "unassigned"),
                # Dropping the tag confirms nothing; it hands the row back.
                "after": "merchant fallback" if owner == "Untagged"
                else "confirmed manually",
            })
        audit_data = json.loads(originals[AUDIT_PATH].decode("utf-8"))
        append_audit_entry(
            audit_data,
            make_audit_entry(current, "Updated transaction details", changes),
        )
        payloads = {
            OWNER_PATH: owner_data,
            REMARK_PATH: remark_data,
            OVERRIDE_PATH: override_data,
            AUDIT_PATH: audit_data,
        }
        try:
            snapshot_backups(paths)
            for path in paths:
                atomic_write_json(path, payloads[path])
            build_output = run_script(BUILD_SCRIPT)
            validation_output = run_script(VALIDATE_SCRIPT)
            refreshed = load_json(TRANSACTIONS_PATH)
            updated = next(
                (row for row in refreshed.get("transactions", []) if row.get("id") == tx_id),
                None,
            )
            # Clearing a tag hands the row back to the merchant fallbacks,
            # which may legitimately name an owner; what must be gone is the
            # stable-ID tag. Any other owner must land as that exact owner.
            owner_applied = updated is not None and (
                updated.get("ownerSource") != "exact-id"
                if owner == "Untagged"
                else updated.get("owner") == owner
            )
            if (
                not updated
                or not owner_applied
                or updated.get("category") != category
                or updated.get("displayName", "") != display_name
                or updated.get("remark", "") != remark
            ):
                raise RuntimeError(
                    "The rebuilt dashboard did not apply all transaction details."
                )
        except Exception as save_error:
            failed_restores = restore_originals(paths, originals)
            if failed_restores:
                # Keep the root cause visible: hiding it behind the restore
                # message left the user with no idea why the save failed.
                raise RuntimeError(
                    "Save failed (%s) AND these manual files could not be "
                    "restored, so they may still hold the rejected edit: %s. "
                    "Copy their .bak files back before saving again."
                    % (save_error, ", ".join(failed_restores))
                )
            try:
                run_script(BUILD_SCRIPT)
            except Exception as rollback_error:
                raise RuntimeError(
                    "Save failed and the prior transaction details were restored, but "
                    "rebuilding that prior state also failed: %s" % rollback_error
                )
            raise

        return {
            "ok": True,
            "transaction": updated,
            "quality": refreshed.get("quality", {}),
            "build": build_output.splitlines()[0] if build_output else "Build completed.",
            "validation": validation_output.splitlines()[0]
            if validation_output else "Validation completed.",
        }


def save_account_review(tx_id, reviewed):
    with WRITE_LOCK:
        account_data = load_json(ACCOUNT_TRANSACTIONS_PATH)
        transactions = {
            row.get("id"): row for row in account_data.get("transactions", [])
        }
        validate_account_review_request(
            {"id": tx_id, "reviewed": reviewed}, transactions)
        current = transactions[tx_id]
        paths = (ACCOUNT_REVIEW_PATH, AUDIT_PATH)
        originals = {path: path.read_bytes() for path in paths}
        review_data = json.loads(originals[ACCOUNT_REVIEW_PATH].decode("utf-8"))
        reviewed_ids = set(review_data.get("reviewedIds", []))
        before = tx_id in reviewed_ids
        if reviewed:
            reviewed_ids.add(tx_id)
        else:
            reviewed_ids.discard(tx_id)
        review_data["reviewedIds"] = sorted(reviewed_ids)
        changes = []
        if before != reviewed:
            changes.append({
                "field": "Bank review",
                "before": "Reviewed" if before else "Needs review",
                "after": "Reviewed" if reviewed else "Needs review",
            })
        audit_data = json.loads(originals[AUDIT_PATH].decode("utf-8"))
        append_audit_entry(
            audit_data,
            make_audit_entry(
                current,
                "Reviewed bank transaction" if reviewed else "Reopened bank transaction",
                changes,
            ),
        )
        payloads = {ACCOUNT_REVIEW_PATH: review_data, AUDIT_PATH: audit_data}
        try:
            snapshot_backups(paths)
            for path in paths:
                atomic_write_json(path, payloads[path])
            validation_output = run_script(VALIDATE_SCRIPT)
        except Exception as save_error:
            failed_restores = restore_originals(paths, originals)
            if failed_restores:
                # Keep the root cause visible: hiding it behind the restore
                # message left the user with no idea why the save failed.
                raise RuntimeError(
                    "Save failed (%s) AND these manual files could not be "
                    "restored, so they may still hold the rejected edit: %s. "
                    "Copy their .bak files back before saving again."
                    % (save_error, ", ".join(failed_restores))
                )
            raise
        return {
            "ok": True,
            "id": tx_id,
            "reviewed": reviewed,
            "validation": validation_output.splitlines()[0]
            if validation_output else "Validation completed.",
        }


class FinanceHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(APP_DIR), **kwargs)

    def send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def bound_port(self, port=None):
        # The guards compare against the port we actually bound. Unit tests
        # that build a bare handler pass it explicitly instead.
        if port is not None:
            return port
        return self.server.server_address[1]

    @staticmethod
    def _split_authority(value):
        """Split a Host header into (hostname, port); port is None if absent.

        Accepts "localhost", "127.0.0.1:3402" and the bracketed "[::1]:3402".
        Returns ("", None) for anything unparseable, which no caller accepts.
        """
        try:
            parsed = urlparse("//" + (value or "").strip())
            return (parsed.hostname or "").lower(), parsed.port
        except ValueError:
            return "", None

    @staticmethod
    def local_origin(origin, port):
        """Is this Origin exactly the dashboard's own origin?

        Hostname alone is not enough. Every other dev server on this machine
        (Jupyter, Vite, a scratch python -m http.server) also gets a
        "http://localhost" origin, so the port has to match the one we bound.
        "null" - sandboxed iframes, file:// pages, some redirects - is never us.
        """
        origin = (origin or "").strip()
        if not origin or origin.lower() == "null":
            return False
        try:
            parsed = urlparse(origin)
            origin_port = parsed.port
        except ValueError:
            return False
        if parsed.scheme != "http":
            return False
        if (parsed.hostname or "").lower() not in LOCAL_HOSTS:
            return False
        # An origin without a port means the scheme default, i.e. 80 for http.
        return (origin_port if origin_port is not None else 80) == port

    def local_request(self, port=None):
        """Is this request addressed to the dashboard on its own port?"""
        port = self.bound_port(port)
        host_name, host_port = self._split_authority(self.headers.get("Host"))
        if host_name not in LOCAL_HOSTS:
            return False
        # A Host without a port names port 80, so it is only ours if we bound
        # 80. Otherwise "Host: localhost" is some other server's address.
        if (host_port if host_port is not None else 80) != port:
            return False
        origin = self.headers.get("Origin")
        if origin is not None and not self.local_origin(origin, port):
            # Per the Fetch spec a same-origin POST can still carry
            # "Origin: null" under some referrer policies. Sec-Fetch-Site is a
            # forbidden header - only the browser can set it - so its
            # same-origin attestation is trusted over a nulled Origin.
            return self.browser_same_origin()
        return True

    def browser_same_origin(self):
        site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
        return site == "same-origin"

    def same_origin_post(self, port=None):
        """A write must prove it came from the dashboard page itself.

        A local Host header is not proof: a page on another localhost port can
        send a "simple request" POST that the browser delivers with no
        preflight at all. Demanding either our exact Origin or a browser's
        Sec-Fetch-Site attestation - together with the JSON content type
        do_POST also requires - forces such a request into a CORS preflight,
        which fails because this server answers no CORS headers.
        """
        # The browser's own attestation wins outright: it cannot be set by
        # page script, and it survives a referrer policy that nulls Origin.
        site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
        if site in {"same-origin", "none"}:
            return True
        origin = self.headers.get("Origin")
        if origin is not None:
            return self.local_origin(origin, self.bound_port(port))
        return False

    @staticmethod
    def json_content_type(value):
        # "application/json; charset=utf-8" is the same media type.
        return (value or "").split(";", 1)[0].strip().lower() == "application/json"

    def list_directory(self, path):
        # Directory indexes expose the shape of the private data folder.
        self.send_json(404, {"ok": False, "error": "Not found."})
        return None

    def do_HEAD(self):
        if not self.local_request():
            self.send_json(403, {"ok": False, "error": "Local requests only."})
            return
        super().do_HEAD()

    def do_GET(self):
        # Every GET, static files included, must come from the local dashboard.
        # Without this a DNS-rebinding page could read app/data/transactions.json.
        if not self.local_request():
            self.send_json(403, {"ok": False, "error": "Local requests only."})
            return
        endpoint = self.path.split("?", 1)[0]
        if endpoint == "/api/status":
            self.send_json(200, {
                "ok": True,
                "editable": True,
                "autoStop": bool(getattr(self.server, "auto_stop", False)),
                "riskReviews": True,
                "remarks": True,
                "transactionDetails": True,
                "auditHistory": True,
                "accountReviews": True,
                "owners": ["Nic", "Shared", "Yx", "Untagged"],
            })
            return
        if endpoint == "/api/account-reviews":
            try:
                with WRITE_LOCK:
                    review_data = load_json(ACCOUNT_REVIEW_PATH)
            except Exception as error:
                self.send_json(500, {
                    "ok": False,
                    "error": "Could not read account reviews: %s" % error,
                })
                return
            reviewed_ids = review_data.get("reviewedIds", []) \
                if isinstance(review_data, dict) else []
            self.send_json(200, {
                "ok": True,
                "reviewedIds": reviewed_ids,
            })
            return
        if endpoint == "/api/audit-history":
            # Read under the write lock: on Windows a concurrent save's
            # os.replace onto an open audit file raises PermissionError and
            # would roll back an otherwise valid save.
            try:
                with WRITE_LOCK:
                    audit_data = load_json(AUDIT_PATH)
            except Exception as error:
                self.send_json(500, {
                    "ok": False,
                    "error": "Could not read the audit history: %s" % error,
                })
                return
            entries = audit_data.get("entries", []) if isinstance(audit_data, dict) else []
            self.send_json(200, {
                "ok": True,
                "total": len(entries),
                "entries": list(reversed(entries[-250:])),
            })
            return
        super().do_GET()

    def do_POST(self):
        # The origin gate comes first, as in do_GET/do_HEAD: answering 404 vs
        # 403 before the check let a cross-origin page probe which endpoints
        # exist.
        if not self.local_request() or not self.same_origin_post():
            self.send_json(403, {"ok": False, "error": "Local requests only."})
            return
        # Checked before the endpoint is routed and before the body is read, so
        # it is not a probe either. A non-JSON type is what a cross-origin
        # "simple request" would have to use to skip the preflight.
        if not self.json_content_type(self.headers.get("Content-Type")):
            self.send_json(400, {
                "ok": False,
                "error": "Content-Type must be application/json.",
            })
            return
        endpoint = self.path.split("?", 1)[0]
        if endpoint not in {
            "/api/client-heartbeat",
            "/api/client-disconnect",
            "/api/owner",
            "/api/risk-review",
            "/api/remark",
            "/api/transaction-detail",
            "/api/account-review",
        }:
            self.send_json(404, {"ok": False, "error": "Unknown endpoint."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > 4096:
            self.send_json(400, {"ok": False, "error": "Invalid request size."})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if endpoint in {"/api/client-heartbeat", "/api/client-disconnect"}:
                if not isinstance(payload, dict):
                    raise ValueError("Request body must be a JSON object.")
                try:
                    client_id = str(uuid.UUID(payload.get("clientId", "")))
                except (ValueError, TypeError, AttributeError):
                    raise ValueError("clientId must be a UUID.")
                lifecycle = getattr(self.server, "dashboard_lifecycle", None)
                if lifecycle:
                    active = (lifecycle.touch(client_id)
                              if endpoint == "/api/client-heartbeat"
                              else lifecycle.disconnect(client_id))
                else:
                    active = 0
                result = {"ok": True, "activeClients": active}
            elif endpoint == "/api/account-review":
                # save_account_review re-validates under WRITE_LOCK; the early
                # validation the card endpoints do here is only a fast reject,
                # so this branch defers entirely to the save function.
                if not isinstance(payload, dict):
                    raise ValueError("Request body must be a JSON object.")
                result = save_account_review(
                    payload.get("id"), payload.get("reviewed"))
            else:
                transaction_data = load_json(TRANSACTIONS_PATH)
                transactions = {
                    row.get("id"): row for row in transaction_data.get("transactions", [])
                }
                if endpoint == "/api/owner":
                    tx_ids, owner = validate_owner_request(payload, transactions)
                    result = save_owner(tx_ids, owner)
                elif endpoint == "/api/risk-review":
                    ids, recognized, key, _ = validate_risk_review_request(
                        payload, transactions)
                    result = save_risk_review(ids, recognized, key)
                elif endpoint == "/api/remark":
                    tx_id, remark = validate_remark_request(payload, transactions)
                    result = save_remark(tx_id, remark)
                else:
                    details = validate_transaction_detail_request(payload, transactions)
                    result = save_transaction_detail(*details)
        except ValueError as error:
            self.send_json(400, {"ok": False, "error": str(error)})
            return
        except Exception as error:
            self.send_json(500, {"ok": False, "error": str(error)})
            return
        self.send_json(200, result)

    def end_headers(self):
        # This is a local development dashboard whose HTML, CSS, JavaScript and
        # generated data change together. Caching any one of them can leave the
        # page running an incompatible mixture after a refresh.
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("X-Content-Type-Options", "nosniff")
        # "same-origin" rather than "no-referrer": both keep the dashboard URL
        # out of third-party requests, but under "no-referrer" the Fetch spec
        # sends "Origin: null" on same-origin POSTs, which the write gate above
        # would then have to special-case.
        self.send_header("Referrer-Policy", "same-origin")
        # Framing is forbidden: an embedded dashboard sends no Origin header on
        # the iframe navigation, so a hostile page could clickjack review
        # buttons whose fetches are then genuinely same-origin.
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        super().end_headers()


class DashboardLifecycle:
    """Stop a launcher-owned server after its last browser tab disappears."""

    def __init__(self, server, stale_after=120, empty_grace=3, startup_grace=120):
        self.server = server
        self.stale_after = stale_after
        self.empty_grace = empty_grace
        self.startup_grace = startup_grace
        self.started_at = time.monotonic()
        self.empty_since = self.started_at
        self.clients = {}
        self.ever_connected = False
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._monitor, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread.is_alive() and threading.current_thread() is not self.thread:
            self.thread.join(timeout=2)

    def touch(self, client_id):
        with self.lock:
            self.clients[client_id] = time.monotonic()
            self.ever_connected = True
            return len(self.clients)

    def disconnect(self, client_id):
        with self.lock:
            self.clients.pop(client_id, None)
            if not self.clients:
                self.empty_since = time.monotonic()
            return len(self.clients)

    def _should_shutdown(self):
        now = time.monotonic()
        with self.lock:
            stale = [client_id for client_id, seen in self.clients.items()
                     if now - seen >= self.stale_after]
            for client_id in stale:
                self.clients.pop(client_id, None)
            if stale and not self.clients:
                self.empty_since = now
            if self.clients:
                return False
            if self.ever_connected:
                return now - self.empty_since >= self.empty_grace
            return now - self.started_at >= self.startup_grace

    def _monitor(self):
        while not self.stop_event.wait(1):
            if self._should_shutdown():
                self.server.shutdown()
                return


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=3402)
    parser.add_argument("--auto-stop", action="store_true",
                        help="stop after the last dashboard tab closes")
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), FinanceHandler)
    server.auto_stop = args.auto_stop
    lifecycle = DashboardLifecycle(server) if args.auto_stop else None
    server.dashboard_lifecycle = lifecycle
    print("Editable finance dashboard: http://localhost:%d" % args.port)
    print("Owner, remark, and transaction-review changes are rebuilt and validated.")
    if lifecycle:
        print("Auto-stop is enabled; closing the last dashboard tab stops the server.")
        lifecycle.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        if lifecycle:
            lifecycle.stop()
        server.server_close()


if __name__ == "__main__":
    main()
