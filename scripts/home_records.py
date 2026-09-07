"""Validation for the standalone household register (never a spending ledger)."""
import math
import re
from datetime import date
from urllib.parse import urlparse

KINDS = {"appliance", "insurance", "mortgage", "maintenance"}
STATUSES = {"Needs checking", "Verified", "Document missing", "Archived"}
DATES = {"repaymentStarts", "purchased", "installed", "warrantyStart", "expires", "starts", "balanceDate",
         "lockInEnd", "reviewDate", "lastService", "nextService", "secondaryExpiry"}
NUMBERS = {"termYears", "additionalCost", "cost", "premium", "balance", "instalment", "rate", "noticeDays", "frequencyMonths"}
TEXT = {"additionalCostNote", "id", "kind", "name", "status", "room", "brand", "model", "serial", "provider",
        "coverage", "cadence", "rateSchedule", "action", "sourceUrl", "sourceName",
        "notes", "transactionId", "transactionSource", "category", "funding", "costBasis",
        "warrantyTerms", "secondaryWarranty", "installationType", "installationSource",
        "linkedRecord"}
SCHEDULE_KEYS = {"label", "due", "done", "completed"}


def _iso_date(value):
    try:
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def validate_schedule(value):
    """A fixed list of dated steps (e.g. a prepaid multi-year filter package) on a maintenance record."""
    if not isinstance(value, list) or len(value) > 24:
        raise ValueError("schedule must be a list of up to 24 entries.")
    result = []
    for item in value:
        if (not isinstance(item, dict) or set(item) - SCHEDULE_KEYS
                or not isinstance(item.get("label"), str) or not item["label"].strip() or len(item["label"]) > 80
                or not isinstance(item.get("done", False), bool) or not _iso_date(item.get("due"))
                or (item.get("completed") and not _iso_date(item["completed"]))):
            raise ValueError("Each schedule entry needs a label, a valid due date and a true/false done flag.")
        entry = {"label": item["label"].strip(), "due": item["due"], "done": item.get("done", False)}
        if item.get("completed"):
            entry["completed"] = item["completed"]
        result.append(entry)
    return result


def validate_record(record, transactions):
    if not isinstance(record, dict) or set(record) - (DATES | NUMBERS | TEXT | {"schedule", "paymentLinks"}):
        raise ValueError("Invalid Home record fields.")
    result = {}
    for key, value in record.items():
        if key == "paymentLinks":
            if not isinstance(value, list) or len(value) > 1000:
                raise ValueError("Payment links must be a list of up to 1000 transactions.")
            links, seen = [], set()
            for link in value:
                if (not isinstance(link, dict) or set(link) != {"source", "id"}
                        or not isinstance(link.get("id"), str)
                        or link.get("source") not in ("card", "bank")):
                    raise ValueError("Each payment link needs a source and transaction ID.")
                pair = (link["source"], link["id"])
                if pair not in transactions or pair in seen:
                    raise ValueError("Payment links must refer to distinct existing transactions.")
                seen.add(pair)
                links.append(dict(link))
            result[key] = links
        elif key == "schedule":
            result[key] = validate_schedule(value)
        elif key in NUMBERS:
            if value is None or value == "":
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError("%s must be a non-negative number." % key)
            if key in {"noticeDays", "frequencyMonths"} and value != int(value):
                raise ValueError("%s must be a whole number." % key)
            result[key] = value
        else:
            if not isinstance(value, str) or len(value) > (1500 if key == "notes" else 500):
                raise ValueError("%s is too long or is not text." % key)
            value = value.strip()
            if key in DATES and value:
                try:
                    if date.fromisoformat(value).isoformat() != value:
                        raise ValueError()
                except ValueError:
                    raise ValueError("%s must be a valid date." % key)
            result[key] = value
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", result.get("id", "")):
        raise ValueError("Invalid record ID.")
    if result.get("linkedRecord") and not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", result["linkedRecord"]):
        raise ValueError("Invalid linked record ID.")
    if result.get("kind") not in KINDS or result.get("status") not in STATUSES:
        raise ValueError("Choose a valid record type and status.")
    if not result.get("name"):
        raise ValueError("A name is required.")
    if result.get("category", "") not in {"", "Appliances", "Fixtures", "Furniture", "Pet", "Renovation"}:
        raise ValueError("Choose Appliances, Fixtures, Furniture, Pet or Renovation.")
    if result.get("sourceUrl"):
        url = urlparse(result["sourceUrl"])
        if url.scheme != "https" or not url.netloc or url.username or url.password:
            raise ValueError("Document links must be HTTPS URLs without credentials.")
    if result.get("cadence", "") not in {"", "Monthly", "Yearly", "One-off"}:
        raise ValueError("Choose a valid premium cadence.")
    for start, end in [("starts", "expires"), ("warrantyStart", "expires")]:
        if result.get(start) and result.get(end) and result[start] > result[end]:
            raise ValueError("The end date must follow the start date.")
    if result.get("transactionId"):
        if (result.get("transactionSource"), result["transactionId"]) not in transactions:
            raise ValueError("The linked transaction is no longer available. Choose it again.")
    return result
