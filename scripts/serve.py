"""Serve the dashboard and safely persist owner, remark, and review decisions.

The server binds to 127.0.0.1 only. Changes are written atomically, then the
dashboard data is rebuilt and validated. A failed build restores the prior file
and rebuilds the previous state.
"""

import argparse
import atexit
import hmac
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from file_lock import FinanceWriteLock
from home_records import validate_record
from net_worth import EMPTY as NET_WORTH_EMPTY, apply_change as apply_net_worth_change


REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = REPO_ROOT / "app"
HOME_PATH = REPO_ROOT / "manual" / "home.json"
HOME_PUBLIC_PATH = APP_DIR / "data" / "home.json"
NET_WORTH_PATH = REPO_ROOT / "manual" / "net_worth.json"
NET_WORTH_PUBLIC_PATH = APP_DIR / "data" / "net_worth.json"
# Per-clone identity: monogram, colours, title and favicon live in the
# private folder so the same code serves every household member.
BRANDING_PATH = REPO_ROOT / "manual" / "branding.json"
BRANDING_PUBLIC_PATH = APP_DIR / "data" / "branding.json"
BRANDING_DIR = REPO_ROOT / "manual" / "branding"


def read_branding():
    if not BRANDING_PATH.exists():
        return {}
    try:
        value = load_json(BRANDING_PATH)
    except ValueError:
        return {}
    if not isinstance(value, dict):
        return {}
    allowed = {"monogram", "title", "brand", "brandInk", "brandDark", "brandInkDark"}
    result = {k: str(v)[:80] for k, v in value.items() if k in allowed and isinstance(v, str)}
    # Which tabs this clone shows, in order. Anything not listed stays out of
    # the header, so each person keeps the features they actually use.
    tabs = value.get("tabs")
    if isinstance(tabs, list):
        result["tabs"] = [t for t in tabs if isinstance(t, str) and t in TAB_IDS][:len(TAB_IDS)]
    return result


TAB_IDS = ("overview", "income", "insurance", "home", "networth", "travel", "games", "split", "transactions")


DEFAULT_PORT = 3402


def branding_port():
    """The clone's own port from manual/branding.json, else the default, so
    two household clones on one machine do not fight over 3402."""
    if BRANDING_PATH.exists():
        try:
            port = load_json(BRANDING_PATH).get("port")
            if isinstance(port, int) and not isinstance(port, bool) and 1 <= port <= 65535:
                return port
        except (ValueError, AttributeError):
            pass
    return DEFAULT_PORT
OWNER_PATH = REPO_ROOT / "manual" / "owner_tags.json"
RISK_REVIEW_PATH = REPO_ROOT / "manual" / "risk_reviews.json"
ACCOUNT_REVIEW_PATH = REPO_ROOT / "manual" / "account_reviews.json"
CARD_FEE_REVIEW_PATH = REPO_ROOT / "manual" / "card_fee_reviews.json"
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
# The account page's "Review all N" control reviews a whole filtered list in
# one request. The cap matches the owner batch and keeps a request inside the
# 4KB body limit do_POST enforces.
MAX_ACCOUNT_REVIEW_BATCH = 100
ACCOUNT_REVIEW_CHECKS = {
    "unclassified",
    "large-transfer",
    "large-withdrawal",
    "new-counterparty",
    "possible-duplicate",
    "derived-amount",
    "unverified-source",
}
WRITE_LOCK = FinanceWriteLock(REPO_ROOT / "tmp" / ".finance-data.lock")
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
REPLACE_ATTEMPTS = 5
REPLACE_RETRY_DELAY = 0.05
# Backups live in a subfolder of the file they copy, so manual/owner_tags.json
# is backed up into manual/backups/. Deriving the folder from the file keeps
# the two together when the paths are repointed at a test tree.
BACKUP_DIR_NAME = "backups"
# How many generations of each file to keep. Roughly a working day of saves;
# past that the disk cost stops buying anything a restore would want.
BACKUP_GENERATIONS = 30


def _code_snapshot():
    """Modification times of the files a running server was started from."""
    files = list((REPO_ROOT / "scripts").glob("*.py")) + list((APP_DIR / "js").glob("*.js")) \
        + list((APP_DIR / "css").glob("*.css")) + [APP_DIR / "index.html"]
    snapshot = {}
    for path in files:
        try:
            snapshot[str(path)] = path.stat().st_mtime_ns
        except OSError:
            pass
    return snapshot


CODE_AT_START = _code_snapshot()


def code_changed():
    """True once any code file differs from what this process started with,
    so the page can say the server needs a restart instead of failing on a
    route it does not have."""
    return _code_snapshot() != CODE_AT_START


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


def read_home():
    return load_json(HOME_PATH) if HOME_PATH.exists() else {"revision": 0, "records": []}


def read_net_worth():
    return load_json(NET_WORTH_PATH) if NET_WORTH_PATH.exists() else dict(NET_WORTH_EMPTY)


def save_net_worth(payload):
    """Apply one net-worth change under the same guarantees as a Home save:
    revision check, backup first, private file and public snapshot written
    together and both restored if either write fails."""
    if not isinstance(payload, dict) or type(payload.get("revision")) is not int:
        raise ValueError("A net-worth revision is required.")
    with WRITE_LOCK:
        current = read_net_worth()
        if payload["revision"] != current.get("revision", 0):
            raise ValueError("Net worth changed in another tab. Close this form and reload before saving.")
        changed = apply_net_worth_change(current, payload)
        updated = {"revision": current.get("revision", 0) + 1,
                   "updatedAt": datetime.now().isoformat(timespec="seconds")}
        updated.update(changed)
        paths = [NET_WORTH_PATH, NET_WORTH_PUBLIC_PATH]
        originals = {path: path.read_bytes() if path.exists() else None for path in paths}
        snapshot_backups([NET_WORTH_PATH] if NET_WORTH_PATH.exists() else [])
        try:
            for path in paths:
                path.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_json(path, updated)
        except Exception:
            for path, original in originals.items():
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_write_bytes(path, original)
            raise
        return dict(updated, ok=True)


def save_home(payload):
    if not isinstance(payload, dict) or type(payload.get("revision")) is not int:
        raise ValueError("A Home revision is required.")
    with WRITE_LOCK:
        current = read_home()
        if payload["revision"] != current["revision"]:
            raise ValueError("Home changed in another tab. Close this form and reload before saving.")
        transactions = set()
        for source, path in [("card", TRANSACTIONS_PATH), ("bank", ACCOUNT_TRANSACTIONS_PATH)]:
            if path.exists():
                transactions.update((source, row.get("id")) for row in load_json(path).get("transactions", []))
        record = validate_record(payload.get("record"), transactions)
        records = list(current["records"])
        index = next((i for i, row in enumerate(records) if row["id"] == record["id"]), None)
        if index is None:
            records.append(record)
        else:
            records[index] = record
        updated = {"revision": current["revision"] + 1,
                   "updatedAt": datetime.now().isoformat(timespec="seconds"), "records": records}
        if "costReference" in current:
            updated["costReference"] = current["costReference"]
        paths = [HOME_PATH, HOME_PUBLIC_PATH]
        originals = {path: path.read_bytes() if path.exists() else None for path in paths}
        snapshot_backups([HOME_PATH] if HOME_PATH.exists() else [])
        try:
            for path in paths:
                path.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_json(path, updated)
        except Exception:
            for path, original in originals.items():
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_write_bytes(path, original)
            raise
        return dict(updated, ok=True)


def manual_file_defaults():
    """Every manual file this server writes, with its empty payload.

    Only the files the server itself creates and maintains are listed. The
    hand-authored ones - identity.json, salary.json, settlements.json,
    game_sales.json, owner_rules.json, legacy_transactions.json - are user
    content: the parsers and the builder already treat their absence as
    "nothing recorded yet", or deliberately refuse to run without them.
    Seeding an empty stand-in would only make a missing file look answered.

    Resolved on each call so tests that repoint the path constants at a
    temporary tree get that tree.
    """
    return {
        OWNER_PATH: {"tags": {}, "tagsById": {}},
        RISK_REVIEW_PATH: {"recognizedSignals": []},
        ACCOUNT_REVIEW_PATH: {"recognizedSignals": []},
        CARD_FEE_REVIEW_PATH: {"resolvedIds": []},
        REMARK_PATH: {"remarksById": {}},
        OVERRIDE_PATH: {"overridesById": {}},
        AUDIT_PATH: {"entries": []},
        NET_WORTH_PATH: dict(NET_WORTH_EMPTY),
    }


def ensure_manual_files():
    """Create any missing manual file the server writes. Returns what it made.

    manual/ is Git-ignored, so a fresh clone has none of these. Every save
    reads the file it is about to update before touching it, and the review
    and history endpoints read theirs on GET, so an absent file surfaced as a
    500 with a traceback rather than as an empty dashboard. Idempotent: an
    existing file is never rewritten, whatever it holds.
    """
    created = []
    for path, default in manual_file_defaults().items():
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, default)
        created.append(path)
    return created


def backup_dir(path):
    return path.parent / BACKUP_DIR_NAME


def backup_generations(path):
    """Every kept backup of `path`, oldest first.

    The timestamp is fixed-width, so name order is time order.
    """
    directory = backup_dir(path)
    if not directory.is_dir():
        return []
    return sorted(directory.glob(path.name + ".*.bak"))


def prune_backups(path, keep=BACKUP_GENERATIONS):
    """Drop all but the newest `keep` generations of one file."""
    generations = backup_generations(path)
    for stale in generations[:max(len(generations) - keep, 0)]:
        try:
            stale.unlink()
        except OSError:
            # Pruning is housekeeping. A locked or already-deleted old copy
            # must not fail the save whose backup we just took.
            pass


def snapshot_backups(paths, keep=BACKUP_GENERATIONS):
    """Copy every file to manual/backups/<name>.<timestamp>.bak.

    Every backup is taken before any live file changes. Interleaving backup and
    write per file left mixed-generation backups when a save died midway, so
    restoring them could reconstruct a state that never existed. One timestamp
    is shared by the whole save for the same reason: the files that belong to
    one generation are identifiable by name alone.

    Backups rotate rather than overwrite. A single <file>.bak meant the second
    save after a bad one destroyed the only copy of the good state.
    """
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    created = []
    for path in paths:
        directory = backup_dir(path)
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / ("%s.%s.bak" % (path.name, stamp))
        shutil.copy2(path, destination)
        created.append(destination)
    for path in paths:
        prune_backups(path, keep)
    return created


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
    """Resolve a posted bank-review decision to rows in the current statements.

    ``id`` reviews one row; ``ids`` reviews a batch under a single validation
    run and a single audit entry, which is what the account page's "Review all
    N" control posts. Both forms return a list so the save path never has to
    branch on shape.
    """
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object.")
    reviewed = payload.get("reviewed")
    checks_by_id = payload.get("checksById", {})
    if "ids" in payload:
        ids = payload.get("ids")
        if (not isinstance(ids, list) or not ids
                or len(ids) > MAX_ACCOUNT_REVIEW_BATCH):
            raise ValueError(
                "Between 1 and %d account transaction IDs are required."
                % MAX_ACCOUNT_REVIEW_BATCH)
    else:
        ids = [payload.get("id")]
    if any(not isinstance(tx_id, str) or not tx_id.startswith("tx_")
           for tx_id in ids):
        raise ValueError("A valid account transaction ID is required.")
    if reviewed not in (True, False):
        raise ValueError("Reviewed must be true or false.")
    # Deduplicated, so a repeated ID cannot double-count in the audit row list.
    ids = sorted(set(ids))
    if any(tx_id not in transactions for tx_id in ids):
        raise ValueError("That account transaction is not present in the current statements.")
    if not isinstance(checks_by_id, dict):
        raise ValueError("Bank review checks must be an object keyed by transaction ID.")
    normalized_checks = {}
    for tx_id in ids:
        checks = checks_by_id.get(tx_id, [])
        if (not isinstance(checks, list) or
                any(not isinstance(check, str) or not check for check in checks)):
            raise ValueError("Each bank review must list its current checks.")
        unknown = sorted(set(checks) - ACCOUNT_REVIEW_CHECKS)
        if unknown:
            raise ValueError("Unknown bank review check: %s." % ", ".join(unknown))
        if reviewed and not checks:
            raise ValueError("A recognized bank review must list at least one current check.")
        normalized_checks[tx_id] = sorted(set(checks))
    return ids, reviewed, normalized_checks


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
    destination = payload.get("destination", "")
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
    # The destination is the country or region a travel charge belongs to,
    # for rows whose descriptor names only a platform's billing entity.
    if not isinstance(destination, str):
        raise ValueError("Destination must be text.")
    destination = " ".join(destination.split())
    if len(destination) > 40:
        raise ValueError("Destination must be 40 characters or fewer.")
    return tx_id, owner, category, display_name, remark, destination


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
                    "Copy their newest copies in manual/backups/ back "
                    "before saving again."
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
                    "Copy their newest copies in manual/backups/ back "
                    "before saving again."
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
                    "Copy their newest copies in manual/backups/ back "
                    "before saving again."
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


def save_transaction_detail(tx_id, owner, category, display_name, remark, destination=""):
    with WRITE_LOCK:
        transaction_data = load_json(TRANSACTIONS_PATH)
        transactions = {
            row.get("id"): row for row in transaction_data.get("transactions", [])
        }
        tx_id, owner, category, display_name, remark, destination = (
            validate_transaction_detail_request(
                {
                    "id": tx_id,
                    "owner": owner,
                    "category": category,
                    "displayName": display_name,
                    "remark": remark,
                    "destination": destination,
                },
                transactions,
            )
        )
        current = transactions[tx_id]
        # A Trip.com booking name is derived on every build, not a saved edit.
        # The drawer shows it as the placeholder, so an untouched field posts
        # "" and must not clear anything; a user re-typing the same name has
        # nothing to pin either. Only a different name becomes an override.
        derived_display_name = (
            current.get("displayName", "")
            if current.get("displayNameSource") == "trip-booking" else ""
        )
        if display_name == derived_display_name:
            display_name = ""
        saved_display_name = "" if derived_display_name else current.get("displayName", "")
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
        category_overlap = len(current.get("ruleCategories") or []) > 1
        # Saving an overlapping row is an explicit choice even when the user
        # keeps the first rule's category. Retaining that same-value override
        # is what lets the review queue distinguish "first rule happened to
        # win" from "the user confirmed the first rule".
        if category_overlap or category != current.get("ruleCategory", current.get("category")):
            override["category"] = category
        if display_name:
            override["displayName"] = display_name
        if destination:
            override["destination"] = destination
        if override:
            overrides[tx_id] = override
        else:
            overrides.pop(tx_id, None)
        override_data["overridesById"] = dict(sorted(overrides.items()))

        changes = []
        comparisons = (
            ("Display name", saved_display_name, display_name),
            ("Category", current.get("category", ""), category),
            ("Owner", current.get("owner", "Unassigned"), owner),
            ("Remarks", current.get("remark", ""), remark),
            ("Destination", current.get("destination", ""), destination),
        )
        for field, before, after in comparisons:
            if before != after:
                changes.append({"field": field, "before": before, "after": after})
        if category_overlap and category == current.get("category"):
            changes.append({
                "field": "Category review",
                "before": ", ".join(current.get("ruleCategories") or []),
                "after": category,
            })
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
            # With no override the rebuilt row shows its derived booking name
            # again; what must match is the override, not the label.
            display_applied = updated is not None and (
                updated.get("displayName", "") == display_name
                or (not display_name and updated.get("displayNameSource") == "trip-booking")
            )
            if (
                not updated
                or not owner_applied
                or updated.get("category") != category
                or not display_applied
                or updated.get("remark", "") != remark
                or updated.get("destination", "") != destination
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
                    "Copy their newest copies in manual/backups/ back "
                    "before saving again."
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


def save_card_fee_review(tx_id, resolved):
    if not isinstance(tx_id, str) or not tx_id:
        raise ValueError("Card fee alert requires a transaction ID.")
    if resolved not in (True, False):
        raise ValueError("Card fee alert resolved must be true or false.")
    with WRITE_LOCK:
        transaction_data = load_json(TRANSACTIONS_PATH)
        transaction = next((row for row in transaction_data.get("transactions", [])
                            if row.get("id") == tx_id), None)
        if transaction is None or transaction.get("type") != "debit" or \
                "CARD MEMBERSHIP FEE" not in str(transaction.get("description", "")).upper():
            raise ValueError("That transaction is not a current card membership fee.")
        review_data = load_json(CARD_FEE_REVIEW_PATH) if CARD_FEE_REVIEW_PATH.exists() \
            else {"resolvedIds": []}
        ids = set(review_data.get("resolvedIds", []))
        if resolved:
            ids.add(tx_id)
        else:
            ids.discard(tx_id)
        payload = {"resolvedIds": sorted(ids)}
        snapshot_backups((CARD_FEE_REVIEW_PATH,)) if CARD_FEE_REVIEW_PATH.exists() else None
        CARD_FEE_REVIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(CARD_FEE_REVIEW_PATH, payload)
        return {"ok": True, "resolvedIds": payload["resolvedIds"]}


def save_account_review(tx_ids, reviewed, checks_by_id=None):
    """Mark one bank row or a batch reviewed, under one validation run.

    ``tx_ids`` is a single ID string or a list of them; "Review all N" posts
    the whole filtered list, which used to arrive as N requests, each running
    the validator and writing its own audit entry.
    """
    if isinstance(tx_ids, str) or tx_ids is None:
        tx_ids = [tx_ids]
    with WRITE_LOCK:
        account_data = load_json(ACCOUNT_TRANSACTIONS_PATH)
        transactions = {
            row.get("id"): row for row in account_data.get("transactions", [])
        }
        request = {
            "ids": tx_ids,
            "reviewed": reviewed,
            "checksById": checks_by_id or {},
        }
        tx_ids, reviewed, checks_by_id = validate_account_review_request(
            request, transactions)
        current = transactions[tx_ids[0]]
        paths = (ACCOUNT_REVIEW_PATH, AUDIT_PATH)
        originals = {path: path.read_bytes() for path in paths}
        review_data = json.loads(originals[ACCOUNT_REVIEW_PATH].decode("utf-8"))
        stored_signals = review_data.get("recognizedSignals", [])
        signals_by_id = {
            entry.get("id"): entry
            for entry in stored_signals if isinstance(entry, dict) and entry.get("id")
        }
        # A batch spans rows that may not agree, so the audit records the set
        # it moved from rather than one row's state.
        previous = [
            label for label, present in (
                ("Reviewed", any(tx_id in signals_by_id for tx_id in tx_ids)),
                ("Needs review", any(tx_id not in signals_by_id for tx_id in tx_ids)),
            ) if present
        ]
        for tx_id in tx_ids:
            if reviewed:
                signals_by_id[tx_id] = {
                    "id": tx_id,
                    "checks": checks_by_id[tx_id],
                    "recognizedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
                }
            else:
                signals_by_id.pop(tx_id, None)
        review_data.pop("reviewedIds", None)
        review_data["recognizedSignals"] = [
            signals_by_id[key] for key in sorted(signals_by_id)
        ]
        after = "Reviewed" if reviewed else "Needs review"
        changes = []
        if previous != [after]:
            changes.append({
                "field": "Bank review",
                "before": ", ".join(previous),
                "after": after,
            })
        audit_data = json.loads(originals[AUDIT_PATH].decode("utf-8"))
        if len(tx_ids) == 1:
            action = ("Reviewed bank transaction" if reviewed
                      else "Reopened bank transaction")
        else:
            action = ("Reviewed %d bank transactions" if reviewed
                      else "Reopened %d bank transactions") % len(tx_ids)
        append_audit_entry(
            audit_data,
            make_audit_entry(current, action, changes, transaction_ids=tx_ids),
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
                    "Copy their newest copies in manual/backups/ back "
                    "before saving again."
                    % (save_error, ", ".join(failed_restores))
                )
            raise
        return {
            "ok": True,
            # "id" stays for single-row callers; "ids" carries the whole batch
            # so the page can repaint every row it just reviewed.
            "id": tx_ids[0],
            "ids": tx_ids,
            "reviewed": reviewed,
            "validation": validation_output.splitlines()[0]
            if validation_output else "Validation completed.",
        }


COMPRESSIBLE = {".json", ".js", ".css", ".html", ".svg"}
GZIP_MIN_BYTES = 2048
# (path, mtime, size) -> gzipped bytes. The data files are a few megabytes
# and change only on import or save, so compressing once per version is cheap.
_GZIP_CACHE = {}
_GZIP_LOCK = threading.Lock()


def gzipped_static(path):
    """Return (gzipped bytes, mtime) for a compressible file, or None."""
    try:
        stat = path.stat()
    except OSError:
        return None
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    with _GZIP_LOCK:
        hit = _GZIP_CACHE.get(key)
    if hit is None:
        import gzip
        hit = gzip.compress(path.read_bytes(), compresslevel=6)
        with _GZIP_LOCK:
            # Drop older versions of the same file so the cache cannot grow with
            # every save; the dashboard only ever asks for the current one.
            for stale in [k for k in _GZIP_CACHE if k[0] == key[0]]:
                del _GZIP_CACHE[stale]
            _GZIP_CACHE[key] = hit
    return hit, stat.st_mtime


class StaticMixin:
    """Shared by the editable and the read-only share handlers."""

    def serve_static(self):
        """Static files, gzipped when the browser accepts it.

        transactions.json alone is over 3 MB uncompressed and every page load
        reads it; gzip brings the data files to roughly a tenth of that,
        which matters most on the Wi-Fi share. Anything not compressible, or
        too small to be worth it, falls through to the stock handler.
        """
        request_path = self.path.split("?", 1)[0]
        path = Path(self.translate_path(request_path))
        # A clone's own favicon lives in the private folder; the tracked one
        # is the default for a fresh clone.
        if request_path in ("/favicon.svg", "/favicon.ico"):
            private = BRANDING_DIR / request_path.lstrip("/")
            if private.is_file():
                path = private
                body = private.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/svg+xml" if private.suffix == ".svg" else "image/x-icon")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)
                return
        accepts = "gzip" in (self.headers.get("Accept-Encoding") or "").lower()
        if (accepts and path.is_file() and path.suffix.lower() in COMPRESSIBLE
                and path.stat().st_size >= GZIP_MIN_BYTES):
            packed = gzipped_static(path)
            if packed:
                body, mtime = packed
                self.send_response(200)
                self.send_header("Content-Type", self.guess_type(str(path)))
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Vary", "Accept-Encoding")
                self.send_header("Last-Modified", self.date_time_string(int(mtime)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)
                return
        super().do_GET()


class FinanceHandler(StaticMixin, SimpleHTTPRequestHandler):
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
        if endpoint == "/api/home":
            try:
                with WRITE_LOCK:
                    self.send_json(200, dict(read_home(), ok=True))
            except Exception as error:
                self.send_json(500, {"ok": False, "error": str(error)})
            return
        if endpoint == "/api/net-worth":
            try:
                with WRITE_LOCK:
                    self.send_json(200, dict(read_net_worth(), ok=True))
            except Exception as error:
                self.send_json(500, {"ok": False, "error": str(error)})
            return
        if endpoint == "/api/status":
            self.send_json(200, {
                "ok": True,
                "editable": True,
                "autoStop": bool(getattr(self.server, "auto_stop", False)),
                "riskReviews": True,
                "remarks": True,
                "transactionDetails": True,
                "homeRecords": True,
                "netWorth": True,
                "codeChanged": code_changed(),
                "auditHistory": True,
                "accountReviews": True,
                # How many bank rows one /api/account-review may carry.
                "accountReviewBatch": MAX_ACCOUNT_REVIEW_BATCH,
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
            recognized_signals = review_data.get("recognizedSignals", []) \
                if isinstance(review_data, dict) else []
            self.send_json(200, {
                "ok": True,
                "recognizedSignals": recognized_signals,
            })
            return
        if endpoint == "/api/share":
            self.send_json(200, dict(share_status(), ok=True))
            return
        if endpoint == "/api/card-fee-reviews":
            try:
                with WRITE_LOCK:
                    review_data = load_json(CARD_FEE_REVIEW_PATH) if CARD_FEE_REVIEW_PATH.exists() \
                        else {"resolvedIds": []}
                self.send_json(200, {"ok": True, "resolvedIds": review_data.get("resolvedIds", [])})
            except Exception as error:
                self.send_json(500, {"ok": False, "error": "Could not read card fee alerts: %s" % error})
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
        self.serve_static()

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
            "/api/home",
            "/api/net-worth",
            "/api/client-heartbeat",
            "/api/client-disconnect",
            "/api/owner",
            "/api/risk-review",
            "/api/remark",
            "/api/transaction-detail",
            "/api/account-review",
            "/api/card-fee-review",
            "/api/share",
        }:
            self.send_json(404, {"ok": False, "error": "Unknown endpoint."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > (16384 if endpoint in {"/api/home", "/api/net-worth"} else 4096):
            self.send_json(400, {"ok": False, "error": "Invalid request size."})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if endpoint == "/api/home":
                result = save_home(payload)
            elif endpoint == "/api/net-worth":
                result = save_net_worth(payload)
            elif endpoint in {"/api/client-heartbeat", "/api/client-disconnect"}:
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
                    payload["ids"] if "ids" in payload else payload.get("id"),
                    payload.get("reviewed"),
                    payload.get("checksById"),
                )
            elif endpoint == "/api/card-fee-review":
                if not isinstance(payload, dict):
                    raise ValueError("Request body must be a JSON object.")
                result = save_card_fee_review(payload.get("id"), payload.get("resolved"))
            elif endpoint == "/api/share":
                if not isinstance(payload, dict):
                    raise ValueError("Request body must be a JSON object.")
                action = payload.get("action")
                if action == "start":
                    result = dict(start_share(), ok=True)
                elif action == "stop":
                    result = dict(stop_share(), ok=True)
                else:
                    raise ValueError("Share action must be start or stop.")
            else:
                # Read under the write lock: on Windows a concurrent rebuild's
                # os.replace onto this file raises PermissionError, which would
                # surface as a 500 on a request that is merely fast-rejecting.
                # The save functions re-read under the lock themselves.
                with WRITE_LOCK:
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

    # Endpoints the open dashboard polls on a timer rather than a user acting.
    # At one request every five seconds per tab, their access lines were about
    # 17,000 a day per tab in the log the launcher redirects stderr to, which
    # buried every line worth reading. Only a plain 200 is skipped: anything
    # else is a fault worth seeing.
    QUIET_ENDPOINTS = {"/api/client-heartbeat", "/api/client-disconnect"}

    def log_request(self, code="-", size="-"):
        status = code.value if isinstance(code, HTTPStatus) else code
        endpoint = getattr(self, "path", "").split("?", 1)[0]
        if status == 200 and endpoint in self.QUIET_ENDPOINTS:
            return
        super().log_request(code, size)

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

    # The grace period has to outlast a page reload: between the old tab's
    # unload and the new page's first heartbeat the server looks abandoned,
    # and on a busy machine three seconds was not enough - a hard reload could
    # stop the server the user was reloading.
    def __init__(self, server, stale_after=120, empty_grace=15, startup_grace=120):
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
        # A running Wi-Fi share keeps the server up for its guests even after the
        # owner closes their own tab; the share's own timer bounds that.
        if share_running():
            return False
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


# ---------- Wi-Fi sharing ----------
#
# "Share on Wi-Fi" opens a second, read-only view of the dashboard on every
# network interface so a phone on the same network can read it. It runs as a
# thread inside this process, so it can never outlive the dashboard, it never
# lists directories, it answers no write, and every request must carry the
# random code from the link (as a query parameter on the first visit, then as
# a cookie). It stops after SHARE_MAX_MINUTES, on Stop, or with this server.
# Only the local dashboard can start or stop it, through the same origin
# checks as every other write.
SHARE_PORT_OFFSET = 1000
SHARE_MAX_MINUTES = 120
SHARE_PORT = 3402 + SHARE_PORT_OFFSET
SHARE_COOKIE = "finance_share"
SHARE_LOCK = threading.Lock()
share_state = {"server": None, "thread": None, "port": None, "url": None, "token": None,
               "startedAt": None, "stopsAt": None, "timer": None, "generation": 0}

# What the shared copy answers for the dashboard's start-up API calls, so a
# guest's page settles into read-only mode without a single failed request.
SHARE_READ_ONLY_API = {
    "/api/status": {"ok": True, "editable": False, "shared": True},
    "/api/account-reviews": {"ok": True, "recognizedSignals": []},
    "/api/card-fee-reviews": {"ok": True, "resolvedIds": []},
    "/api/insurance-verifications": {"ok": True, "verifiedById": {}},
    "/api/audit-history": {"ok": True, "entries": []},
}


def lan_address():
    """The IPv4 address other devices on this network reach this machine by.

    Connecting a UDP socket sends nothing; it only makes the OS pick the
    outbound interface. Falls back to the hostname's address.
    """
    address = None
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))
        address = probe.getsockname()[0]
    except OSError:
        address = None
    finally:
        probe.close()
    if not address or address.startswith("127."):
        try:
            address = socket.gethostbyname(socket.gethostname())
        except OSError:
            address = None
    if not address or address.startswith("127."):
        return None
    return address


class ShareHandler(StaticMixin, SimpleHTTPRequestHandler):
    """Read-only, code-gated copy of app/ for other devices on the network."""

    _set_cookie = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(APP_DIR), **kwargs)

    def log_message(self, *args):
        # Guests' paths and addresses do not belong in the dashboard console.
        pass

    def send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        if self._set_cookie:
            self.send_header("Set-Cookie", "%s=%s; Path=/; HttpOnly; SameSite=Lax"
                             % (SHARE_COOKIE, self.server.share_token))
        super().end_headers()

    def authorised(self):
        token = self.server.share_token
        query = parse_qs(urlparse(self.path).query)
        offered = query.get("k", [""])[0]
        if offered and hmac.compare_digest(offered, token):
            self._set_cookie = True
            return True
        jar = SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie", ""))
        except Exception:
            return False
        morsel = jar.get(SHARE_COOKIE)
        return morsel is not None and hmac.compare_digest(morsel.value, token)

    def refuse(self):
        self.send_json(403, {"ok": False, "error": "This link only works with the code from the dashboard."})

    def list_directory(self, path):
        self.send_json(404, {"ok": False, "error": "Not found."})
        return None

    def do_GET(self):
        if not self.authorised():
            self.refuse()
            return
        path = urlparse(self.path).path
        if path == "/api/home":
            try:
                with WRITE_LOCK:
                    self.send_json(200, dict(read_home(), ok=True))
            except Exception:
                self.send_json(500, {"ok": False, "error": "Could not read Home records."})
            return
        if path == "/api/net-worth":
            try:
                with WRITE_LOCK:
                    self.send_json(200, dict(read_net_worth(), ok=True))
            except Exception:
                self.send_json(500, {"ok": False, "error": "Could not read net-worth records."})
            return
        if path in SHARE_READ_ONLY_API:
            self.send_json(200, SHARE_READ_ONLY_API[path])
            return
        if path.startswith("/api/"):
            self.send_json(404, {"ok": False, "error": "Not available on the shared copy."})
            return
        self.serve_static()

    def do_HEAD(self):
        if not self.authorised():
            self.refuse()
            return
        super().do_HEAD()

    def do_POST(self):
        path = urlparse(self.path).path
        if path in ("/api/client-heartbeat", "/api/client-disconnect"):
            # The page pings these every few seconds; answer quietly so a
            # guest's console stays clean. Nothing is recorded.
            self.send_response(204)
            self.end_headers()
            return
        if not self.authorised():
            self.refuse()
            return
        self.send_json(405, {"ok": False, "error": "The shared copy is read-only."})


class ShareServer(ThreadingHTTPServer):
    daemon_threads = True
    # A clash must fail loudly rather than quietly share a port with a
    # leftover server.
    allow_reuse_address = False


def share_running():
    with SHARE_LOCK:
        return share_state["server"] is not None


def share_status_locked():
    running = share_state["server"] is not None
    return {
        "running": running,
        "url": share_state["url"] if running else None,
        "port": share_state["port"] if running else None,
        "startedAt": share_state["startedAt"] if running else None,
        "stopsAt": share_state["stopsAt"] if running else None,
        "maxMinutes": SHARE_MAX_MINUTES,
    }


def share_status():
    with SHARE_LOCK:
        return share_status_locked()


def _stop_share_locked():
    server = share_state["server"]
    thread = share_state["thread"]
    timer = share_state["timer"]
    if timer is not None:
        timer.cancel()
    if server is not None:
        server.shutdown()
        server.server_close()
    if thread is not None:
        thread.join(timeout=3)
    share_state.update({"server": None, "thread": None, "port": None, "url": None,
                        "token": None, "startedAt": None, "stopsAt": None, "timer": None})


def _expire_share(generation):
    # A timer from an earlier share must not stop a later one.
    with SHARE_LOCK:
        if share_state["generation"] != generation or share_state["server"] is None:
            return
        _stop_share_locked()


def start_share(port=None):
    port = SHARE_PORT if port is None else port
    with SHARE_LOCK:
        if share_state["server"] is not None:
            return share_status_locked()
        address = lan_address()
        if not address:
            raise ValueError("Could not work out this computer's Wi-Fi address.")
        token = secrets.token_urlsafe(9)
        try:
            server = ShareServer(("0.0.0.0", port), ShareHandler)
        except OSError as error:
            raise RuntimeError(
                "Port %d is already in use, so the share could not start (%s). "
                "Close whatever is using it or start this dashboard with --share-port."
                % (port, error.strerror or error))
        server.share_token = token
        bound_port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, name="wifi-share", daemon=True)
        thread.start()
        share_state["generation"] += 1
        timer = threading.Timer(SHARE_MAX_MINUTES * 60, _expire_share,
                                args=(share_state["generation"],))
        timer.daemon = True
        timer.start()
        started = time.time()
        share_state.update({
            "server": server, "thread": thread, "port": bound_port, "token": token,
            "url": "http://%s:%d/?k=%s" % (address, bound_port, token),
            "startedAt": datetime.fromtimestamp(started).strftime("%Y-%m-%d %H:%M"),
            "stopsAt": datetime.fromtimestamp(
                started + SHARE_MAX_MINUTES * 60).strftime("%Y-%m-%d %H:%M"),
            "timer": timer,
        })
        return share_status_locked()


def stop_share():
    with SHARE_LOCK:
        _stop_share_locked()
        return share_status_locked()


atexit.register(stop_share)


def main():
    global SHARE_PORT
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=None,
                        help="listen port (default: manual/branding.json port, else %d)" % DEFAULT_PORT)
    parser.add_argument("--auto-stop", action="store_true",
                        help="stop after the last dashboard tab closes")
    parser.add_argument("--share-port", type=int, default=None,
                        help="port for the read-only Wi-Fi share (default: port + %d)"
                        % SHARE_PORT_OFFSET)
    args = parser.parse_args()
    if args.port is None:
        args.port = branding_port()
    SHARE_PORT = args.share_port if args.share_port is not None else args.port + SHARE_PORT_OFFSET
    # A fresh clone has no manual/ at all; seed the files this server writes
    # before any request can read one that is not there.
    for created in ensure_manual_files():
        print("Created empty %s" % created.relative_to(REPO_ROOT))
    # Recreate the static/read-only Home snapshot from its private source.
    with WRITE_LOCK:
        HOME_PUBLIC_PATH.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(HOME_PUBLIC_PATH, read_home())
        atomic_write_json(NET_WORTH_PUBLIC_PATH, read_net_worth())
        atomic_write_json(BRANDING_PUBLIC_PATH, read_branding())
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
