"""Validation for the net-worth register: dated balance snapshots per account.

The register is a private, hand-maintained list of accounts (CPF, brokers,
policies, property, loans) and the balances read from their portals on given
dates. It never holds transactions; the dashboard combines these snapshots
with the bank closing balances already derived from the statements.
"""
import math
import re
from datetime import date

GROUPS = ("cash", "cpf", "investments", "insurance", "property", "liabilities")
ACCOUNT_TEXT = {"id", "name", "group", "note", "source", "linkedRecord"}
SNAPSHOT_TEXT = {"accountId", "date", "source", "note"}
ID_PATTERN = r"[a-zA-Z0-9_-]{1,80}"
EMPTY = {"revision": 0, "accounts": [], "snapshots": []}


def _text(record, allowed, limits=None):
    result = {}
    for key, value in record.items():
        if key not in allowed:
            raise ValueError("Unknown field %s." % key)
        if not isinstance(value, str) or len(value) > (limits or {}).get(key, 300):
            raise ValueError("%s is too long or is not text." % key)
        result[key] = value.strip()
    return result


def _iso(value, label):
    try:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError()
    except (TypeError, ValueError):
        raise ValueError("%s must be a valid YYYY-MM-DD date." % label)
    return value


def _top_up(value):
    """A fixed amount added in the same month every year from a given year;
    the dashboard carries it as scheduled balances (see networth.js)."""
    if not isinstance(value, dict):
        raise ValueError("topUp must be an object.")
    extra = set(value) - {"month", "amount", "since", "flow"}
    if extra:
        raise ValueError("Unknown topUp field %s." % sorted(extra)[0])
    month, amount, since = value.get("month"), value.get("amount"), value.get("since")
    if isinstance(month, bool) or not isinstance(month, int) or not 1 <= month <= 12:
        raise ValueError("topUp.month must be 1-12.")
    if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not math.isfinite(amount) or amount <= 0:
        raise ValueError("topUp.amount must be a positive number.")
    if isinstance(since, bool) or not isinstance(since, int) or not 2000 <= since <= 2100:
        raise ValueError("topUp.since must be a year.")
    result = {"month": month, "amount": round(float(amount), 2), "since": since}
    flow = value.get("flow")
    if flow is not None:
        if not isinstance(flow, str) or not flow.strip() or len(flow) > 60:
            raise ValueError("topUp.flow must be a short statement flow name.")
        result["flow"] = flow.strip()
    return result


def validate_reminder(record):
    """A yearly transfer that is not an asset of the owner's (a family
    member's CPF top-up): reminded on the Overview, never counted."""
    if not isinstance(record, dict):
        raise ValueError("Invalid reminder.")
    schedule = _top_up({k: v for k, v in record.items() if k in ("month", "amount", "since", "flow")})
    text = _text({k: v for k, v in record.items() if k not in ("month", "amount", "since", "flow")},
                 {"id", "name", "match", "note"}, {"note": 1000})
    if not re.fullmatch(ID_PATTERN, text.get("id", "")):
        raise ValueError("Invalid reminder ID.")
    if not text.get("name"):
        raise ValueError("A reminder name is required.")
    text.update(schedule)
    return text


def validate_account(record):
    if not isinstance(record, dict):
        raise ValueError("Invalid account.")
    archived = record.pop("archived", False)
    if not isinstance(archived, bool):
        raise ValueError("archived must be true or false.")
    top_up = record.pop("topUp", None)
    result = _text(record, ACCOUNT_TEXT, {"note": 1000})
    if top_up is not None:
        result["topUp"] = _top_up(top_up)
    if not re.fullmatch(ID_PATTERN, result.get("id", "")):
        raise ValueError("Invalid account ID.")
    if not result.get("name"):
        raise ValueError("An account name is required.")
    if result.get("group") not in GROUPS:
        raise ValueError("Choose a valid group.")
    if result.get("linkedRecord") and not re.fullmatch(ID_PATTERN, result["linkedRecord"]):
        raise ValueError("Invalid linked record ID.")
    if archived:
        result["archived"] = True
    return result


def validate_snapshot(record, account_ids):
    if not isinstance(record, dict):
        raise ValueError("Invalid snapshot.")
    value = record.pop("value", None)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError("value must be a non-negative number.")
    result = _text(record, SNAPSHOT_TEXT, {"note": 1000})
    if result.get("accountId") not in account_ids:
        raise ValueError("Choose an existing account.")
    result["date"] = _iso(result.get("date"), "date")
    result["value"] = round(float(value), 2)
    return result


def apply_change(current, payload):
    """Return the register after one save. `payload` carries exactly one of
    `account` (upsert), `snapshot` (upsert by account and date) or
    `deleteSnapshot` ({accountId, date})."""
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object.")
    accounts = [dict(a) for a in current.get("accounts", [])]
    snapshots = [dict(s) for s in current.get("snapshots", [])]
    keys = [k for k in ("account", "snapshot", "deleteSnapshot") if k in payload]
    if len(keys) != 1:
        raise ValueError("Send one account, snapshot or deletion per save.")
    kind = keys[0]
    if kind == "account":
        account = validate_account(dict(payload["account"]))
        index = next((i for i, a in enumerate(accounts) if a["id"] == account["id"]), None)
        if index is None:
            accounts.append(account)
        else:
            accounts[index] = account
    else:
        ids = {a["id"] for a in accounts}
        if kind == "snapshot":
            snapshot = validate_snapshot(dict(payload["snapshot"]), ids)
            snapshots = [s for s in snapshots
                         if not (s["accountId"] == snapshot["accountId"] and s["date"] == snapshot["date"])]
            snapshots.append(snapshot)
        else:
            target = payload["deleteSnapshot"]
            if not isinstance(target, dict) or target.get("accountId") not in ids:
                raise ValueError("Choose an existing account.")
            when = _iso(target.get("date"), "date")
            before = len(snapshots)
            snapshots = [s for s in snapshots
                         if not (s["accountId"] == target["accountId"] and s["date"] == when)]
            if len(snapshots) == before:
                raise ValueError("That balance is no longer recorded.")
    snapshots.sort(key=lambda s: (s["accountId"], s["date"]))
    result = {"accounts": accounts, "snapshots": snapshots}
    if current.get("reminders"):
        result["reminders"] = [validate_reminder(dict(r)) for r in current["reminders"]]
    return result
