"""Validate generated finance data and surface records that still need review.

Default mode fails on structural or arithmetic integrity errors. ``--strict``
also fails while any classification/provenance review queue is non-empty.
"""

import argparse
import json
import math
import os
import re
import sys
from collections import Counter
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from build_data import tag_key  # noqa: E402
from risk_checks import signal_key  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO_ROOT, "app", "data")
MANUAL_DIR = os.path.join(REPO_ROOT, "manual")

# Everything downstream - month arithmetic, the balance chain's page/line
# ordering, the dashboard's month index - assumes these exact shapes. A month
# like "2025/03" used to reach int() and abort the whole run with a traceback,
# and "2025-3-4" parses happily with strptime while sorting before "2025-12-31".
MONTH_KEY_RE = re.compile(r"\d{4}-(0[1-9]|1[0-2])")
DATE_KEY_RE = re.compile(r"\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])")

# How stale the newest statement may get before it is worth mentioning. A UOB
# cycle is monthly, so a month and a half means one has almost certainly been
# missed rather than merely not issued yet.
STALE_STATEMENT_DAYS = 45


def load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def load_optional(path, default):
    """Hand-entered files are created on first use, so absent means empty."""
    if not os.path.exists(path):
        return default
    return load(path)


def is_signal_key(value):
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def is_month_key(value):
    return isinstance(value, str) and MONTH_KEY_RE.fullmatch(value) is not None


def is_date_key(value):
    """A zero-padded ISO date that also exists in the calendar."""
    if not isinstance(value, str) or DATE_KEY_RE.fullmatch(value) is None:
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def month_distance(left, right):
    """Months between two keys, or None when either is not a month key.

    Returning None rather than raising is deliberate: a malformed month is a
    finding to report, not a reason to abandon every remaining check.
    """
    if not (is_month_key(left) and is_month_key(right)):
        return None
    ly, lm = [int(part) for part in left.split("-")]
    ry, rm = [int(part) for part in right.split("-")]
    return (ly * 12 + lm) - (ry * 12 + rm)


def next_month(month):
    year, number = [int(part) for part in month.split("-")]
    return "%04d-01" % (year + 1) if number == 12 else "%04d-%02d" % (year, number + 1)


def month_gaps(months):
    """Months absent from the span the data itself covers.

    Recomputed from the months actually present, so a claimed "nothing is
    missing" can be contradicted rather than merely believed.
    """
    known = sorted({month for month in months if is_month_key(month)})
    if len(known) < 2:
        return []
    gaps = []
    current = known[0]
    present = set(known)
    while current != known[-1]:
        current = next_month(current)
        if current not in present:
            gaps.append(current)
    return gaps


def month_end(month):
    year, number = [int(part) for part in month.split("-")]
    first_of_next = date(year + 1, 1, 1) if number == 12 else date(year, number + 1, 1)
    return date.fromordinal(first_of_next.toordinal() - 1)


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
        if not is_date_key(row.get("date")):
            errors.append("%s has invalid date %r" % (label, row.get("date")))
        month = row.get("month")
        if not is_month_key(month):
            errors.append("%s has invalid statement month %r" % (label, month))
        cycle_date = row.get("postedDate") or row.get("date")
        if row.get("postedDate") is not None and not is_date_key(row.get("postedDate")):
            errors.append("%s has invalid posted date %r" % (label, row.get("postedDate")))
        distance = month_distance(cycle_date[:7], month) if is_date_key(cycle_date) else None
        if distance is not None and abs(distance) > 1:
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
        os.path.join(MANUAL_DIR, "risk_reviews.json"), {"recognizedSignals": []})
    audit_data = load_optional(
        os.path.join(MANUAL_DIR, "audit_history.json"), {"entries": []})
    account_review_data = load_optional(
        os.path.join(MANUAL_DIR, "account_reviews.json"), {"reviewedIds": []})
    salary_data = load_optional(
        os.path.join(MANUAL_DIR, "salary.json"), {"steps": [], "years": []})
    game_sales_data = load_optional(
        os.path.join(MANUAL_DIR, "game_sales.json"), {"sales": []})
    owner_rules_data = load_optional(
        os.path.join(MANUAL_DIR, "owner_rules.json"), {"rules": {}, "confirmed": []})
    manual_settlements = load_optional(
        os.path.join(MANUAL_DIR, "settlements.json"),
        {"openingBalances": [], "payments": []})

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
        if not is_month_key(opening_date):
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
        if not is_date_key(payment.get("date")):
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
        freshness = {}
    else:
        latest = freshness.get("latestStatementMonth")
        expected = freshness.get("expectedNextStatementMonth")
        if latest not in cards.get("months", []):
            errors.append("freshness latest statement month is not in card data")
        if latest and expected and month_distance(expected, latest) != 1:
            errors.append("freshness expected statement month is not the next month")
        if not is_date_key(freshness.get("sourceThrough")) or \
                not is_date_key(freshness.get("expectedNextStatementDate")):
            errors.append("freshness has an invalid source or expected date")
        missing_statement_months = freshness.get("missingStatementMonths")
        if not isinstance(missing_statement_months, list):
            errors.append("freshness missingStatementMonths must be a list")
        elif any(month in cards.get("months", []) for month in missing_statement_months):
            errors.append("freshness reports a present statement month as missing")
        else:
            # The old check could only be failed by over-claiming. A real hole in
            # the statement run with missingStatementMonths left empty sailed
            # through, so recompute the gaps from the months that are actually
            # covered and insist the claim matches.
            covered_months = cards.get("quality", {}).get("pdfMonths") \
                or cards.get("months", [])
            computed = month_gaps(covered_months)
            if sorted(missing_statement_months) != computed:
                errors.append(
                    "freshness claims %s is missing, but the card statements "
                    "themselves are missing %s"
                    % (sorted(missing_statement_months) or "nothing",
                       computed or "nothing")
                )

    final_by_id = {row["id"]: row for row in output.get("transactions", []) if row.get("id")}
    account_by_id = {
        row["id"]: row for row in account.get("transactions", []) if row.get("id")
    }
    card_by_id = {
        row["id"]: row for row in cards.get("transactions", []) if row.get("id")
    }
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

    # The dashboard is built out of the same rows the source files hold, so its
    # rows must echo them field for field - not merely in number. Counting was
    # the only thing checked here before, which let a sign-flipped refund or an
    # edited amount on an account/legacy row through untouched.
    def numeric(value):
        try:
            amount = float(value)
        except (TypeError, ValueError):
            return None
        return amount if math.isfinite(amount) else None

    def source_bucket(row):
        """Which side of the ledger the source file says this row sits on."""
        direction = row.get("direction")
        if direction:
            return "credit" if direction == "deposit" else "debit"
        amount = numeric(row.get("amount")) or 0.0
        return "credit" if row.get("credit") or amount < 0 else "debit"

    def output_bucket(row):
        return "credit" if row.get("type") in ("refund", "payment") else "debit"

    def content_key(row, fields):
        return tuple(row.get(field) for field in fields)

    output_by_source = {}
    for row in output.get("transactions", []):
        source_type = (row.get("provenance") or {}).get("sourceType")
        output_by_source.setdefault(source_type, []).append(row)

    account_rows = account.get("transactions", [])
    # Account statements deliberately do not feed the dashboard today. If any of
    # their rows ever do appear there, all of them must - a half-loaded account
    # would otherwise read as a smaller month rather than as a fault.
    account_in_output = bool(output_by_source.get("account-pdf"))
    for built in output_by_source.get("account-pdf", []):
        source = account_by_id.get(built.get("id"))
        if not source:
            errors.append(
                "dashboard account transaction %s matches no statement row" % built.get("id"))
            continue
        for field in ("date", "month", "description", "amount"):
            if built.get(field) != source.get(field):
                errors.append(
                    "account transaction %s changed %s during build" % (built.get("id"), field))
    if account_in_output:
        for source in account_rows:
            if source.get("id") not in final_by_id:
                errors.append(
                    "account transaction %s is missing from dashboard data" % source.get("id"))

    # Legacy rows are hand-entered and carry no ID of their own, so they are
    # matched on their content instead. An edited amount or a dropped field
    # changes the multiset and shows up here.
    legacy_fields = ("date", "month", "card", "description", "amount")
    expected_legacy_counts = Counter(
        content_key(row, legacy_fields) for row in expected_legacy)
    actual_legacy_counts = Counter(
        content_key(row, legacy_fields) for row in output_by_source.get("legacy-manual", []))
    if expected_legacy_counts != actual_legacy_counts:
        missing = expected_legacy_counts - actual_legacy_counts
        extra = actual_legacy_counts - expected_legacy_counts
        errors.append(
            "legacy rows in the dashboard do not echo legacy_transactions.json "
            "(%d absent, %d unaccounted for; first difference %r)"
            % (sum(missing.values()), sum(extra.values()),
               next(iter(list(missing) + list(extra)), None))
        )

    # Totals, not counts. Every (source, month, side) bucket has to carry the
    # same money in the dashboard as in the file it came from.
    expected_totals = {}
    actual_totals = {}
    contributing = [("card-pdf", cards.get("transactions", [])),
                    ("legacy-manual", expected_legacy)]
    if account_in_output:
        contributing.append(("account-pdf", account_rows))
    for source_type, rows in contributing:
        for row in rows:
            amount = numeric(row.get("amount"))
            if amount is None:
                continue
            key = (source_type, row.get("month"), source_bucket(row))
            count, total = expected_totals.get(key, (0, 0.0))
            expected_totals[key] = (count + 1, total + amount)
    contributing_types = {name for name, _ in contributing}
    for source_type, rows in output_by_source.items():
        if source_type not in contributing_types:
            continue
        for row in rows:
            amount = numeric(row.get("amount"))
            if amount is None:
                continue
            key = (source_type, row.get("month"), output_bucket(row))
            count, total = actual_totals.get(key, (0, 0.0))
            actual_totals[key] = (count + 1, total + amount)
    for key in sorted(set(expected_totals) | set(actual_totals), key=repr):
        want_count, want_total = expected_totals.get(key, (0, 0.0))
        got_count, got_total = actual_totals.get(key, (0, 0.0))
        if want_count != got_count or abs(want_total - got_total) > 0.005:
            errors.append(
                "%s %s %s totals disagree: source has %d row(s) worth %.2f, "
                "dashboard has %d worth %.2f"
                % (key[0], key[1], key[2], want_count, want_total, got_count, got_total)
            )

    # A row's category may only differ from the rule that produced it when the
    # user overrode it by ID, and the transaction type follows from the category
    # and the source's credit flag. Without this a refund silently becomes a
    # debit, or a plain charge silently becomes a settled payment.
    overrides_by_id = override_data.get("overridesById", {})
    for row in output.get("transactions", []):
        tx_id = row.get("id")
        category = row.get("category")
        rule_category = row.get("ruleCategory")
        override = overrides_by_id.get(tx_id, {})
        if category != rule_category and "category" not in override:
            errors.append(
                "transaction %s is filed as %r but its rule says %r and nothing overrides it"
                % (tx_id, category, rule_category)
            )
        source = None
        if tx_id in account_by_id and (row.get("provenance") or {}).get(
                "sourceType") == "account-pdf":
            source = account_by_id[tx_id]
        else:
            source = card_by_id.get(tx_id)
        if source is None:
            continue
        if category == "Payment":
            expected_type = "payment"
        elif source_bucket(source) == "credit":
            expected_type = "refund"
        else:
            expected_type = "debit"
        if row.get("type") != expected_type:
            errors.append(
                "transaction %s is typed %r but its source says %r"
                % (tx_id, row.get("type"), expected_type)
            )

    # A month re-read under a second file name used to be invisible: the clone
    # brought its own IDs and its own internally consistent balance chain, so
    # every existing check was satisfied while the month was counted twice.
    def check_single_ingest(label, rows):
        files_by_section = {}
        rows_by_file = {}
        for row in rows:
            provenance = row.get("provenance") or {}
            source_file = provenance.get("sourceFile")
            files_by_section.setdefault(
                (row.get("month"), provenance.get("section")), set()).add(source_file)
            amount = numeric(row.get("amount"))
            rows_by_file.setdefault(source_file, Counter())[
                (row.get("date"), row.get("description"),
                 None if amount is None else round(amount, 2))
            ] += 1
        for (month, section), files in sorted(files_by_section.items(), key=repr):
            if len(files) > 1:
                errors.append(
                    "%s %s %s is ingested from %d source files: %s"
                    % (label, month, section, len(files),
                       ", ".join(sorted(str(name) for name in files)))
                )
        fingerprints = {}
        for source_file, counter in sorted(rows_by_file.items(), key=repr):
            if not counter:
                continue
            fingerprint = tuple(sorted(counter.items(), key=repr))
            if fingerprint in fingerprints:
                errors.append(
                    "%s %s and %s hold identical rows - one statement has been "
                    "ingested twice" % (label, fingerprints[fingerprint], source_file)
                )
            else:
                fingerprints[fingerprint] = source_file

    check_single_ingest("card data", cards.get("transactions", []))
    check_single_ingest("account data", account_rows)
    check_single_ingest("dashboard data", output.get("transactions", []))

    # Month indexes and claimed gaps, recomputed rather than believed.
    for label, payload, rows, claimed in (
        ("card data", cards, cards.get("transactions", []),
         cards.get("quality", {}).get("missingMonths")),
        ("account data", account, account_rows,
         account.get("quality", {}).get("missingMonths")),
    ):
        row_months = sorted({row.get("month") for row in rows if is_month_key(row.get("month"))})
        if payload.get("months") != row_months:
            errors.append("%s month index does not match its transactions" % label)
        if claimed is not None:
            computed = month_gaps(row_months)
            if not isinstance(claimed, list) or sorted(claimed) != computed:
                errors.append(
                    "%s reports %s as missing months, but its own months are missing %s"
                    % (label, sorted(claimed) if isinstance(claimed, list) else claimed,
                       computed or "nothing")
                )

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

    # --- Hand-maintained inputs that previously had no validation at all. ---
    # salary.json, game_sales.json, owner_rules.json, and settlements.json feed
    # the build directly; a typo in any of them silently skews the dashboard.
    owner_domain = {"Nic", "Shared", "Yx", "Untagged"}

    def checked_amount(value, label, minimum_exclusive=None):
        try:
            amount = float(value)
            if not math.isfinite(amount):
                raise ValueError
            if minimum_exclusive is not None and amount <= minimum_exclusive:
                raise ValueError
        except (TypeError, ValueError):
            errors.append("%s has invalid amount %r" % (label, value))

    salary_steps = salary_data.get("steps")
    if not isinstance(salary_steps, list):
        errors.append("salary.steps must be a list")
        salary_steps = []
    step_months = []
    for index, step in enumerate(salary_steps, 1):
        label = "salary step %d" % index
        if not isinstance(step, dict):
            errors.append("%s must be an object" % label)
            continue
        if not is_month_key(step.get("from")):
            errors.append("%s has invalid from month %r" % (label, step.get("from")))
        else:
            step_months.append(step["from"])
        checked_amount(step.get("amount"), label, minimum_exclusive=0)
    if len(step_months) != len(set(step_months)):
        errors.append("salary.steps contains duplicate effective months")

    salary_years = salary_data.get("years")
    if not isinstance(salary_years, list):
        errors.append("salary.years must be a list")
        salary_years = []
    seen_years = set()
    for index, year_row in enumerate(salary_years, 1):
        label = "salary year %d" % index
        if not isinstance(year_row, dict):
            errors.append("%s must be an object" % label)
            continue
        year = year_row.get("year")
        if not isinstance(year, int) or not 2000 <= year <= 2100:
            errors.append("%s has invalid year %r" % (label, year))
        elif year in seen_years:
            errors.append("salary.years lists %d twice" % year)
        else:
            seen_years.add(year)
        checked_amount(year_row.get("income"), label + " income", minimum_exclusive=0)
        if year_row.get("tax") is not None:
            checked_amount(year_row.get("tax"), label + " tax", minimum_exclusive=-1)

    sales = game_sales_data.get("sales")
    if not isinstance(sales, list):
        errors.append("game_sales.sales must be a list")
        sales = []
    for index, sale in enumerate(sales, 1):
        label = "game sale %d" % index
        if not isinstance(sale, dict):
            errors.append("%s must be an object" % label)
            continue
        if not isinstance(sale.get("game"), str) or not sale["game"].strip():
            errors.append("%s has no game name" % label)
        if not is_month_key(sale.get("month")):
            errors.append("%s has invalid month %r" % (label, sale.get("month")))
        checked_amount(sale.get("amount"), label, minimum_exclusive=0)

    merchant_rules = owner_rules_data.get("rules")
    if not isinstance(merchant_rules, dict):
        errors.append("owner_rules.rules must be an object")
        merchant_rules = {}
    for merchant, rule_owner in merchant_rules.items():
        if rule_owner not in owner_domain:
            errors.append(
                "owner rule %r assigns unknown owner %r" % (merchant, rule_owner))
    confirmed = owner_rules_data.get("confirmed", [])
    if not isinstance(confirmed, list) or any(
            not isinstance(pattern, str) or not pattern.strip()
            for pattern in confirmed):
        errors.append("owner_rules.confirmed must be a list of non-empty patterns")

    # The dashboard reads the settlements copy embedded at build time, not the
    # manual file, so a drift between them means the build is stale.
    for field in ("openingBalances", "payments"):
        embedded = settlements.get(field) if isinstance(settlements, dict) else None
        if json.dumps(manual_settlements.get(field, []), sort_keys=True) != \
                json.dumps(embedded, sort_keys=True):
            errors.append(
                "settlements.%s in the dashboard differs from manual/settlements.json - "
                "rebuild before trusting the Split tab" % field)

    # The legacy month|description|amount|direction tags are structurally
    # validated (a malformed key or unknown owner is an error), but keys that
    # no longer match a card row are only reported. They accumulate whenever a
    # statement month is relabeled and are harmless as long as tagsById covers
    # the affected rows, so they fail no mode; the count keeps the drift visible.
    legacy_tags = owner_data.get("tags", {})
    if not isinstance(legacy_tags, dict):
        errors.append("owner_tags.tags must be an object")
        legacy_tags = {}
    card_key_counts = Counter(
        tag_key(row["month"], row["description"], row["amount"], bool(row.get("credit")))
        for row in cards.get("transactions", [])
        if is_month_key(row.get("month")) and isinstance(row.get("description"), str)
        and isinstance(row.get("amount"), (int, float))
    )
    orphaned_tag_keys = 0
    overfilled_tag_keys = 0
    for key, owners in legacy_tags.items():
        parts = key.split("|") if isinstance(key, str) else []
        if len(parts) != 4 or not is_month_key(parts[0]) or parts[3] not in ("C", "D"):
            errors.append("legacy owner tag has malformed key %r" % key)
            continue
        if not isinstance(owners, list) or not owners or any(
                owner not in owner_domain for owner in owners):
            errors.append("legacy owner tag %r has invalid owner list %r" % (key, owners))
            continue
        matching_rows = card_key_counts.get(key, 0)
        if matching_rows == 0:
            orphaned_tag_keys += 1
        elif len(owners) > matching_rows:
            overfilled_tag_keys += 1
    if orphaned_tag_keys or overfilled_tag_keys:
        print(
            "Note: %d legacy owner-tag key(s) match no card row and %d carry more "
            "owners than matching rows (informational; tagsById is authoritative)"
            % (orphaned_tag_keys, overfilled_tag_keys)
        )

    # Risk group IDs are recomputed on every build, so they must resolve as well;
    # a group naming a row that is not in the output means the check was raised
    # against something the dashboard cannot show.
    current_signals = {}
    for row in output.get("transactions", []):
        risk = row.get("risk")
        if not isinstance(risk, dict):
            continue
        group = risk.get("groupIds")
        if not isinstance(group, list):
            errors.append("transaction check on %s has no group id list" % row.get("id"))
            continue
        if not is_signal_key(risk.get("key")):
            errors.append("transaction check on %s has no signal key" % row.get("id"))
        else:
            current_signals[risk["key"]] = risk
        for tx_id in group:
            if tx_id not in final_by_id:
                errors.append(
                    "transaction check on %s names unknown transaction %s"
                    % (row.get("id"), tx_id)
                )

    if risk_data.get("recognizedIds"):
        warnings.append(
            "risk_reviews.json still holds the pre-signal recognizedIds list - "
            "run scripts/migrate_risk_reviews_to_signals.py"
        )
    entries = risk_data.get("recognizedSignals", [])
    if not isinstance(entries, list):
        errors.append("risk_reviews.recognizedSignals must be a list")
        entries = []
    seen_keys = set()
    for index, entry in enumerate(entries, 1):
        label = "recognized transaction check %d" % index
        if not isinstance(entry, dict):
            errors.append("%s must be an object" % label)
            continue
        key = entry.get("key")
        ids = entry.get("ids")
        checks = entry.get("checks")
        if not is_signal_key(key):
            errors.append("%s has no valid signal key" % label)
            continue
        if key in seen_keys:
            errors.append("%s repeats signal key %s" % (label, key[:12]))
            continue
        seen_keys.add(key)
        if (not isinstance(ids, list) or not ids or
                any(not isinstance(tx_id, str) for tx_id in ids)):
            errors.append("%s must name the transactions it covers" % label)
            continue
        if (not isinstance(checks, list) or
                any(not isinstance(check, str) for check in checks)):
            errors.append("%s must list the checks it acknowledges" % label)
            continue
        # Every stored ID still has to resolve: an acknowledgement pointing at a
        # row the dashboard cannot show is a dangling reference, same as before.
        for tx_id in ids:
            if tx_id not in final_by_id:
                errors.append(
                    "%s names unknown transaction %s" % (label, tx_id)
                )
        if signal_key(ids, checks) != key:
            errors.append(
                "%s key does not match its own rows and checks" % label
            )
        elif key not in current_signals:
            # Not an error. Tuning a check legitimately retires a signal, and
            # the entry is harmless once that happens - but a silently growing
            # pile of dead acknowledgements is worth seeing.
            warnings.append(
                "%s no longer matches a check in the current build (%s on %s)"
                % (label, ",".join(checks) or "no checks", ids[0])
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

    account_review_ids = account_review_data.get("reviewedIds", [])
    if not isinstance(account_review_ids, list):
        errors.append("account_reviews.reviewedIds must be a list")
        account_review_ids = []
    if len(account_review_ids) != len(set(account_review_ids)):
        errors.append("account_reviews.reviewedIds contains duplicates")
    for tx_id in account_review_ids:
        if tx_id not in account_by_id:
            errors.append(
                "reviewed account transaction %s no longer matches a statement row" % tx_id
            )

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

    # The chain above seeds from the first row's own balance, so that row's
    # amount is unfalsifiable from inside the statement: multiply it by seven and
    # every later delta still reconciles. parse_one.py now exports the statement's
    # printed BALANCE B/F, which is the one figure the first row can be measured
    # against.
    anchors = account.get("statementAnchors")
    if not isinstance(anchors, dict):
        anchors = {}
    statement_balances = []
    unanchored = []
    for (month, source_file), rows in sorted(account_groups.items(), key=repr):
        ordered = sorted(rows, key=lambda row: (
            row.get("provenance", {}).get("page", 0),
            row.get("provenance", {}).get("line", 0),
        ))
        if not ordered:
            continue
        first = ordered[0]
        first_delta = first["amount"] if first.get("direction") == "deposit" \
            else -first["amount"]
        derived_opening = round(first["balance"] - first_delta, 2)
        closing = round(ordered[-1]["balance"], 2)
        anchor = anchors.get(month)
        printed_opening = None
        if isinstance(anchor, dict) and anchor.get("file") == source_file:
            printed_opening = numeric(anchor.get("openingBalance"))
        if printed_opening is None:
            unanchored.append("%s (%s)" % (month, source_file))
        else:
            if abs(derived_opening - printed_opening) > 0.02:
                errors.append(
                    "account statement %s opens at a printed %.2f but its first row "
                    "%s implies %.2f" % (month, printed_opening, first.get("id"),
                                         derived_opening)
                )
            printed_closing = numeric(anchor.get("closingBalance"))
            if printed_closing is not None and abs(printed_closing - closing) > 0.02:
                errors.append(
                    "account statement %s ends at %.2f but the parser recorded %.2f"
                    % (month, closing, printed_closing)
                )
        statement_balances.append(
            (month, source_file, printed_opening if printed_opening is not None
             else derived_opening, closing, printed_opening is not None))
    if unanchored:
        # Old data on disk predates the anchor. Say so instead of quietly
        # reverting to the weaker check.
        warnings.append(
            "%d account statement(s) carry no printed BALANCE B/F anchor "
            "(%s%s) - re-run scripts/parse_one.py; until then their first row is "
            "only covered by month-to-month continuity"
            % (len(unanchored), ", ".join(unanchored[:4]),
               ", ..." if len(unanchored) > 4 else "")
        )
    for previous, current in zip(statement_balances, statement_balances[1:]):
        distance = month_distance(current[0], previous[0])
        if distance is None:
            continue
        if distance != 1:
            # A genuine hole in the statement run is a warning, not an error, but
            # it must be visible: continuity cannot be checked across it, so the
            # months on either side are unconstrained.
            warnings.append(
                "no account statement between %s and %s; the balance carried "
                "across that gap is unchecked" % (previous[0], current[0])
            )
            continue
        if abs(current[2] - previous[3]) > 0.02:
            errors.append(
                "account balance does not carry from %s into %s (gap %.2f)"
                % (previous[0], current[0], current[2] - previous[3])
            )

    # Nothing on disk goes stale loudly. A statement that simply never got
    # imported leaves every check above satisfied, so age is reported here.
    latest_month = freshness.get("latestStatementMonth")
    source_through = freshness.get("sourceThrough")
    newest = None
    if is_date_key(source_through):
        newest = datetime.strptime(source_through, "%Y-%m-%d").date()
    elif is_month_key(latest_month):
        newest = month_end(latest_month)
    if newest is not None:
        age = (date.today() - newest).days
        if age > STALE_STATEMENT_DAYS:
            warnings.append(
                "the newest statement covers only up to %s, %d days ago - "
                "a monthly statement has probably not been imported"
                % (newest.isoformat(), age)
            )

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
