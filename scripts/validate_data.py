"""Validate generated finance data and surface records that still need review.

Default mode fails on structural or arithmetic integrity errors. ``--strict``
also fails while any classification/provenance review queue is non-empty.
"""

import argparse
import json
import math
import os
import re
from datetime import datetime


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO_ROOT, "app", "data")
MANUAL_DIR = os.path.join(REPO_ROOT, "manual")


def load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def load_optional(path, default):
    """Hand-entered files are created on first use, so absent means empty."""
    if not os.path.exists(path):
        return default
    return load(path)


def month_distance(left, right):
    ly, lm = [int(part) for part in left.split("-")]
    ry, rm = [int(part) for part in right.split("-")]
    return (ly * 12 + lm) - (ry * 12 + rm)


def validate_rows(name, rows, errors):
    ids = []
    for index, row in enumerate(rows, 1):
        label = "%s row %d" % (name, index)
        tx_id = row.get("id")
        if not isinstance(tx_id, str) or not tx_id.startswith("tx_"):
            errors.append("%s has no stable transaction ID" % label)
        else:
            ids.append(tx_id)
        try:
            amount = float(row.get("amount"))
            if not math.isfinite(amount) or amount <= 0:
                errors.append("%s has invalid amount %r" % (label, row.get("amount")))
        except (TypeError, ValueError):
            errors.append("%s has invalid amount %r" % (label, row.get("amount")))
        try:
            datetime.strptime(row.get("date", ""), "%Y-%m-%d")
        except (TypeError, ValueError):
            errors.append("%s has invalid date %r" % (label, row.get("date")))
        month = row.get("month")
        if not isinstance(month, str) or len(month) != 7:
            errors.append("%s has invalid statement month %r" % (label, month))
        cycle_date = row.get("postedDate") or row.get("date")
        if row.get("postedDate"):
            try:
                datetime.strptime(row["postedDate"], "%Y-%m-%d")
            except (TypeError, ValueError):
                errors.append("%s has invalid posted date %r" % (label, row.get("postedDate")))
        if isinstance(month, str) and len(month) == 7 and cycle_date and \
                abs(month_distance(cycle_date[:7], month)) > 1:
            errors.append(
                "%s posting date %s is more than one month from statement %s"
                % (label, cycle_date, month)
            )
        provenance = row.get("provenance")
        if not isinstance(provenance, dict):
            errors.append("%s has no provenance" % label)
        else:
            for field in ("sourceType", "sourceFile", "statementMonth", "section", "occurrence"):
                if provenance.get(field) in (None, ""):
                    errors.append("%s provenance is missing %s" % (label, field))
            if provenance.get("statementMonth") != month:
                errors.append("%s provenance statement month disagrees with row" % label)
    duplicates = len(ids) - len(set(ids))
    if duplicates:
        errors.append("%s contains %d duplicate transaction ID(s)" % (name, duplicates))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--strict",
        action="store_true",
        help="also fail while any classification, provenance, or transaction check remains",
    )
    args = parser.parse_args()

    cards = load(os.path.join(DATA_DIR, "card_transactions.json"))
    account = load(os.path.join(DATA_DIR, "account_transactions.json"))
    output = load(os.path.join(DATA_DIR, "transactions.json"))
    legacy = load(os.path.join(MANUAL_DIR, "legacy_transactions.json"))
    owner_data = load(os.path.join(MANUAL_DIR, "owner_tags.json"))
    override_data = load_optional(
        os.path.join(MANUAL_DIR, "transaction_overrides.json"), {"overridesById": {}})
    remark_data = load_optional(
        os.path.join(MANUAL_DIR, "transaction_remarks.json"), {"remarksById": {}})
    risk_data = load_optional(
        os.path.join(MANUAL_DIR, "risk_reviews.json"), {"recognizedIds": []})
    audit_data = load_optional(
        os.path.join(MANUAL_DIR, "audit_history.json"), {"entries": []})

    errors = []
    warnings = []
    validate_rows("card data", cards.get("transactions", []), errors)
    validate_rows("account data", account.get("transactions", []), errors)
    validate_rows("dashboard data", output.get("transactions", []), errors)

    settlements = output.get("settlements", {})
    opening_balances = settlements.get("openingBalances") if isinstance(settlements, dict) else None
    if not isinstance(opening_balances, list):
        errors.append("settlements.openingBalances must be a list")
        opening_balances = []
    opening_dates = []
    for index, balance in enumerate(opening_balances, 1):
        label = "settlement opening balance %d" % index
        if not isinstance(balance, dict):
            errors.append("%s must be an object" % label)
            continue
        opening_date = balance.get("from")
        if not isinstance(opening_date, str) or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", opening_date):
            errors.append("%s has invalid from month %r" % (label, opening_date))
        else:
            opening_dates.append(opening_date)
        try:
            amount = float(balance.get("youOweYx"))
            if not math.isfinite(amount) or amount < 0:
                errors.append("%s has invalid youOweYx %r" % (label, balance.get("youOweYx")))
        except (TypeError, ValueError):
            errors.append("%s has invalid youOweYx %r" % (label, balance.get("youOweYx")))
        if balance.get("note") is not None and not isinstance(balance["note"], str):
            errors.append("%s note must be text" % label)
    if len(opening_dates) != len(set(opening_dates)):
        errors.append("settlements contains duplicate opening months")

    payments = settlements.get("payments") if isinstance(settlements, dict) else None
    if not isinstance(payments, list):
        errors.append("settlements.payments must be a list")
        payments = []
    for index, payment in enumerate(payments, 1):
        label = "settlement payment %d" % index
        if not isinstance(payment, dict):
            errors.append("%s must be an object" % label)
            continue
        try:
            datetime.strptime(payment.get("date", ""), "%Y-%m-%d")
        except (TypeError, ValueError):
            errors.append("%s has invalid date %r" % (label, payment.get("date")))
        try:
            amount = float(payment.get("amount"))
            if not math.isfinite(amount) or amount <= 0:
                errors.append("%s has invalid amount %r" % (label, payment.get("amount")))
        except (TypeError, ValueError):
            errors.append("%s has invalid amount %r" % (label, payment.get("amount")))
        if payment.get("direction") not in ("toYx", "fromYx"):
            errors.append("%s has invalid direction %r" % (label, payment.get("direction")))
        if payment.get("note") is not None and not isinstance(payment["note"], str):
            errors.append("%s note must be text" % label)

    freshness = output.get("freshness", {})
    if not isinstance(freshness, dict):
        errors.append("freshness must be an object")
    else:
        latest = freshness.get("latestStatementMonth")
        expected = freshness.get("expectedNextStatementMonth")
        if latest not in cards.get("months", []):
            errors.append("freshness latest statement month is not in card data")
        if latest and expected and month_distance(expected, latest) != 1:
            errors.append("freshness expected statement month is not the next month")
        try:
            datetime.strptime(freshness.get("sourceThrough", ""), "%Y-%m-%d")
            datetime.strptime(freshness.get("expectedNextStatementDate", ""), "%Y-%m-%d")
        except (TypeError, ValueError):
            errors.append("freshness has an invalid source or expected date")
        missing_statement_months = freshness.get("missingStatementMonths")
        if not isinstance(missing_statement_months, list):
            errors.append("freshness missingStatementMonths must be a list")
        elif any(month in cards.get("months", []) for month in missing_statement_months):
            errors.append("freshness reports a present statement month as missing")

    final_by_id = {row["id"]: row for row in output.get("transactions", []) if row.get("id")}
    card_ids = set()
    for source in cards.get("transactions", []):
        card_ids.add(source.get("id"))
        built = final_by_id.get(source.get("id"))
        if not built:
            errors.append("card transaction %s is missing from dashboard data" % source.get("id"))
            continue
        for field in ("date", "postedDate", "month", "card", "description", "amount"):
            if built.get(field) != source.get(field):
                errors.append(
                    "card transaction %s changed %s during build"
                    % (source.get("id"), field)
                )

    covered = set(cards.get("months", []))
    expected_legacy = [
        row for row in legacy.get("transactions", []) if row.get("month") not in covered
    ]
    output_source_count = len(cards.get("transactions", [])) + len(expected_legacy)
    if len(output.get("transactions", [])) != output_source_count:
        errors.append(
            "dashboard has %d rows; card plus uncovered legacy sources have %d"
            % (len(output.get("transactions", [])), output_source_count)
        )

    final_months = sorted({row["month"] for row in output.get("transactions", [])})
    if output.get("months") != final_months:
        errors.append("dashboard month index does not match its transactions")

    quality = output.get("quality", {})
    integrity = quality.get("integrity", {})
    if integrity.get("duplicateIds") or integrity.get("missingProvenance"):
        errors.append("build-time integrity summary reports an error")
    if integrity.get("sourceTransactions") != integrity.get("outputTransactions"):
        errors.append("build-time source/output transaction counts disagree")

    for tx_id, owner in owner_data.get("tagsById", {}).items():
        built = final_by_id.get(tx_id)
        if not built:
            errors.append("stable owner tag %s no longer matches a transaction" % tx_id)
        elif built.get("owner") != owner or built.get("ownerSource") != "exact-id":
            errors.append("stable owner tag %s was not applied exactly" % tx_id)

    # Every other hand-entered record is keyed by transaction ID too, and a
    # dangling key there is just as silent as a dangling owner tag: the category
    # override, the remark or the "I recognize this" simply stops applying and
    # nothing says so. Treat them all as integrity errors.
    for tx_id, override in override_data.get("overridesById", {}).items():
        built = final_by_id.get(tx_id)
        if not built:
            errors.append("category override %s no longer matches a transaction" % tx_id)
            continue
        if "category" in override and built.get("category") != override["category"]:
            errors.append("category override %s was not applied" % tx_id)
        if "displayName" in override and built.get("displayName") != override["displayName"]:
            errors.append("display name override %s was not applied" % tx_id)

    for tx_id, remark in remark_data.get("remarksById", {}).items():
        built = final_by_id.get(tx_id)
        if not built:
            errors.append("remark %s no longer matches a transaction" % tx_id)
        elif built.get("remark") != remark:
            errors.append("remark %s was not applied" % tx_id)

    recognized_ids = risk_data.get("recognizedIds", [])
    if not isinstance(recognized_ids, list):
        errors.append("risk_reviews.recognizedIds must be a list")
        recognized_ids = []
    for tx_id in recognized_ids:
        if tx_id not in final_by_id:
            errors.append("recognized transaction check %s no longer matches a transaction" % tx_id)
    # Risk group IDs are recomputed on every build, so they must resolve as well;
    # a group naming a row that is not in the output means the check was raised
    # against something the dashboard cannot show.
    for row in output.get("transactions", []):
        risk = row.get("risk")
        if not isinstance(risk, dict):
            continue
        group = risk.get("groupIds")
        if not isinstance(group, list):
            errors.append("transaction check on %s has no group id list" % row.get("id"))
            continue
        for tx_id in group:
            if tx_id not in final_by_id:
                errors.append(
                    "transaction check on %s names unknown transaction %s"
                    % (row.get("id"), tx_id)
                )
    for key in ("groupIds", "recognizedGroups", "groups"):
        stored = risk_data.get(key)
        if not isinstance(stored, list):
            continue
        for entry in stored:
            candidates = entry if isinstance(entry, list) else [entry]
            for tx_id in candidates:
                if isinstance(tx_id, str) and tx_id not in final_by_id:
                    errors.append(
                        "risk_reviews.%s names unknown transaction %s" % (key, tx_id)
                    )

    # Audit history is a log, not a live reference. It legitimately describes
    # transactions that a later statement correction removed, so its IDs are
    # checked for shape only - requiring them to resolve would make the file
    # un-appendable and would push the user towards deleting their own history.
    for index, entry in enumerate(audit_data.get("entries", []), 1):
        label = "audit history entry %d" % index
        if not isinstance(entry, dict):
            errors.append("%s must be an object" % label)
            continue
        candidates = [entry.get("transactionId")] + list(entry.get("transactionIds") or [])
        for tx_id in candidates:
            if tx_id is None:
                continue
            if not isinstance(tx_id, str) or not tx_id.startswith("tx_"):
                errors.append("%s has a malformed transaction id %r" % (label, tx_id))

    account_groups = {}
    for row in account.get("transactions", []):
        provenance = row.get("provenance", {})
        key = (row.get("month"), provenance.get("sourceFile"))
        account_groups.setdefault(key, []).append(row)
    for key, rows in account_groups.items():
        rows.sort(key=lambda row: (
            row.get("provenance", {}).get("page", 0),
            row.get("provenance", {}).get("line", 0),
        ))
        previous_balance = rows[0].get("balance") if rows else None
        for row in rows[1:]:
            if previous_balance is None or row.get("balance") is None:
                previous_balance = row.get("balance")
                continue
            delta = round(row["balance"] - previous_balance, 2)
            expected_delta = row["amount"] if row.get("direction") == "deposit" else -row["amount"]
            if abs(delta - expected_delta) > 0.02:
                errors.append(
                    "account balance chain breaks at %s in %s"
                    % (row.get("id"), key[1])
                )
            previous_balance = row["balance"]

    review = quality.get("review", {})
    queues = [
        ("unassigned owner", review.get("untagged", {})),
        ("Other category", review.get("otherCategory", {})),
        ("Lady card rule/unassigned", review.get("ladyRuleOrUnassigned", {})),
        ("suspicious transaction check", review.get("suspicious", {})),
    ]
    unverified = quality.get("provenance", {}).get("unverifiedTransactions", 0)
    if unverified:
        warnings.append("%d transaction(s) come from unverified CSV/manual sources" % unverified)
    unchecked = cards.get("quality", {}).get("uncheckedSections", 0)
    if unchecked:
        errors.append("%d card section(s) could not be reconciled" % unchecked)
    unreconciled = cards.get("quality", {}).get("unreconciledSections") or []
    for section in unreconciled:
        if isinstance(section, dict):
            errors.append(
                "card section %s %s misses its SUB TOTAL by %s"
                % (section.get("month"), section.get("card"), section.get("gap"))
            )
        else:
            errors.append("card data reports an unreconciled section: %r" % (section,))
    # An override means the printed amount lost to the balance chain. That is a
    # guess, not a reading, so it fails the build rather than joining a queue.
    amount_overrides = account.get("quality", {}).get("amountOverrides", 0)
    if amount_overrides:
        errors.append("%d account amount(s) were derived from balance movement" % amount_overrides)
    for label, queue in queues:
        if queue.get("count"):
            warnings.append(
                "%d %s transaction(s), S$%0.2f, need review"
                % (queue["count"], label, queue.get("amount", 0))
            )

    print(
        "Validated %d card, %d account and %d dashboard transactions."
        % (
            len(cards.get("transactions", [])),
            len(account.get("transactions", [])),
            len(output.get("transactions", [])),
        )
    )
    if errors:
        print("\nINTEGRITY ERRORS:")
        for message in errors:
            print("  -", message)
    else:
        print("Structural integrity, stable IDs, provenance, hand-entered references "
              "and source-to-output totals pass.")
    if warnings:
        print("\nREVIEW QUEUE:")
        for message in warnings:
            print("  -", message)
    else:
        print("No classification, provenance, or transaction review items remain.")

    if errors or (args.strict and warnings):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
