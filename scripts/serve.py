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
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object.")
    tx_id = payload.get("id")
    owner = payload.get("owner")
    if not isinstance(tx_id, str) or not tx_id.startswith("tx_"):
        raise ValueError("A valid transaction ID is required.")
    if owner not in ALLOWED_OWNERS:
        raise ValueError("Owner must be Nic, Shared, Yx, or Unassigned.")
    if tx_id not in transactions:
        raise ValueError("That transaction is not present in the current build.")
    return tx_id, owner


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


def save_owner(tx_id, owner):
    with WRITE_LOCK:
        transaction_data = load_json(TRANSACTIONS_PATH)
        transactions = {
            row.get("id"): row for row in transaction_data.get("transactions", [])
        }
        validate_owner_request({"id": tx_id, "owner": owner}, transactions)
        current = transactions[tx_id]
        paths = (OWNER_PATH, AUDIT_PATH)
        originals = {path: path.read_bytes() for path in paths}
        owner_data = json.loads(originals[OWNER_PATH].decode("utf-8"))
        owner_data.setdefault("tagsById", {})[tx_id] = owner
        owner_data["tagsById"] = dict(sorted(owner_data["tagsById"].items()))
        changes = []
        if current.get("owner") != owner:
            changes.append({
                "field": "Owner",
                "before": current.get("owner", "Unassigned"),
                "after": owner,
            })
        if current.get("ownerSource") != "exact-id":
            changes.append({
                "field": "Owner source",
                "before": current.get("ownerSource", "unassigned"),
                "after": "confirmed manually",
            })
        audit_data = json.loads(originals[AUDIT_PATH].decode("utf-8"))
        append_audit_entry(
            audit_data,
            make_audit_entry(current, "Updated owner", changes),
        )
        payloads = {OWNER_PATH: owner_data, AUDIT_PATH: audit_data}

        try:
            for path in paths:
                backup_path = path.with_suffix(path.suffix + ".bak")
                shutil.copy2(path, backup_path)
                atomic_write_json(path, payloads[path])
            build_output = run_script(BUILD_SCRIPT)
            validation_output = run_script(VALIDATE_SCRIPT)
            refreshed = load_json(TRANSACTIONS_PATH)
            updated = next(
                (row for row in refreshed.get("transactions", []) if row.get("id") == tx_id),
                None,
            )
            if not updated or updated.get("owner") != owner:
                raise RuntimeError("The rebuilt dashboard did not apply the saved owner.")
        except Exception:
            for path in paths:
                atomic_write_bytes(path, originals[path])
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
            "transaction": {
                "id": updated["id"],
                "owner": updated["owner"],
                "ownerSource": updated.get("ownerSource"),
            },
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
            row for row in transactions.values()
            if row.get("risk", {}).get("key") == key
            and row.get("risk", {}).get("primary", True)
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
            for path in paths:
                backup_path = path.with_suffix(path.suffix + ".bak")
                shutil.copy2(path, backup_path)
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
        except Exception:
            for path in paths:
                atomic_write_bytes(path, originals[path])
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
            for path in paths:
                backup_path = path.with_suffix(path.suffix + ".bak")
                shutil.copy2(path, backup_path)
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
        except Exception:
            for path in paths:
                atomic_write_bytes(path, originals[path])
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
        owner_data.setdefault("tagsById", {})[tx_id] = owner
        owner_data["tagsById"] = dict(sorted(owner_data["tagsById"].items()))

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
        if current.get("ownerSource") != "exact-id":
            changes.append({
                "field": "Owner source",
                "before": current.get("ownerSource", "unassigned"),
                "after": "confirmed manually",
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
            for path in paths:
                backup_path = path.with_suffix(path.suffix + ".bak")
                shutil.copy2(path, backup_path)
                atomic_write_json(path, payloads[path])
            build_output = run_script(BUILD_SCRIPT)
            validation_output = run_script(VALIDATE_SCRIPT)
            refreshed = load_json(TRANSACTIONS_PATH)
            updated = next(
                (row for row in refreshed.get("transactions", []) if row.get("id") == tx_id),
                None,
            )
            if (
                not updated
                or updated.get("owner") != owner
                or updated.get("category") != category
                or updated.get("displayName", "") != display_name
                or updated.get("remark", "") != remark
            ):
                raise RuntimeError(
                    "The rebuilt dashboard did not apply all transaction details."
                )
        except Exception:
            for path in paths:
                atomic_write_bytes(path, originals[path])
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
            for path in paths:
                shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
                atomic_write_json(path, payloads[path])
            validation_output = run_script(VALIDATE_SCRIPT)
        except Exception:
            for path in paths:
                atomic_write_bytes(path, originals[path])
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

    @staticmethod
    def _hostname(value):
        # Accepts "localhost", "127.0.0.1:3402", "[::1]:3402" and full origins.
        try:
            return (urlparse("//" + value.strip()).hostname or "").lower()
        except ValueError:
            return ""

    def local_request(self):
        if self._hostname(self.headers.get("Host") or "") not in LOCAL_HOSTS:
            return False
        origin = self.headers.get("Origin")
        if origin:
            try:
                origin_host = (urlparse(origin).hostname or "").lower()
            except ValueError:
                return False
            if origin_host not in LOCAL_HOSTS:
                return False
        return True

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
        endpoint = self.path.split("?", 1)[0]
        if endpoint not in {
            "/api/owner",
            "/api/risk-review",
            "/api/remark",
            "/api/transaction-detail",
            "/api/account-review",
        }:
            self.send_json(404, {"ok": False, "error": "Unknown endpoint."})
            return
        if not self.local_request():
            self.send_json(403, {"ok": False, "error": "Local requests only."})
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
            if endpoint == "/api/account-review":
                account_data = load_json(ACCOUNT_TRANSACTIONS_PATH)
                account_transactions = {
                    row.get("id"): row for row in account_data.get("transactions", [])
                }
                tx_id, reviewed = validate_account_review_request(
                    payload, account_transactions)
                result = save_account_review(tx_id, reviewed)
            else:
                transaction_data = load_json(TRANSACTIONS_PATH)
                transactions = {
                    row.get("id"): row for row in transaction_data.get("transactions", [])
                }
            if endpoint == "/api/owner":
                tx_id, owner = validate_owner_request(payload, transactions)
                result = save_owner(tx_id, owner)
            elif endpoint == "/api/risk-review":
                ids, recognized, key, _ = validate_risk_review_request(
                    payload, transactions)
                result = save_risk_review(ids, recognized, key)
            elif endpoint == "/api/remark":
                tx_id, remark = validate_remark_request(payload, transactions)
                result = save_remark(tx_id, remark)
            elif endpoint == "/api/transaction-detail":
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
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=3402)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), FinanceHandler)
    print("Editable finance dashboard: http://localhost:%d" % args.port)
    print("Owner, remark, and transaction-review changes are rebuilt and validated.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
