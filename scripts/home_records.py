"""Validation for the standalone household register (never a spending ledger)."""
import math
import re
from datetime import date
from urllib.parse import urlparse

KINDS = {"appliance", "insurance", "mortgage", "maintenance"}
STATUSES = {"Needs checking", "Verified", "Document missing", "Archived"}
DATES = {"purchased", "installed", "warrantyStart", "expires", "starts", "balanceDate",
         "lockInEnd", "reviewDate", "lastService", "nextService", "secondaryExpiry"}
NUMBERS = {"cost", "premium", "balance", "instalment", "rate", "noticeDays", "frequencyMonths"}
TEXT = {"id", "kind", "name", "status", "room", "brand", "model", "serial", "provider",
        "coverage", "cadence", "rateSchedule", "action", "sourceUrl", "sourceName",
        "notes", "transactionId", "transactionSource", "category", "funding", "costBasis",
        "warrantyTerms", "secondaryWarranty"}


def validate_record(record, transactions):
    if not isinstance(record, dict) or set(record) - (DATES | NUMBERS | TEXT):
        raise ValueError("Invalid Home record fields.")
    result = {}
    for key, value in record.items():
        if key in NUMBERS:
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
    if result.get("kind") not in KINDS or result.get("status") not in STATUSES:
        raise ValueError("Choose a valid record type and status.")
    if not result.get("name"):
        raise ValueError("A name is required.")
    if result.get("category", "") not in {"", "Appliances", "Fixtures", "Furniture"}:
        raise ValueError("Choose Appliances, Fixtures or Furniture.")
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
