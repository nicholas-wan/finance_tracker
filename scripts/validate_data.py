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

from build_data import (  # noqa: E402
    TRIP_MATCH_WINDOW_DAYS,
    is_grab_description,
    is_shopee_description,
    is_trip_description,
    merge_grab_web_history,
    normalize_trip_booking_no,
    parse_trip_date,
    prepare_insurance,
    tag_key,
)
from risk_checks import signal_key  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get(
    "FINANCE_DATA_DIR", os.path.join(REPO_ROOT, "app", "data")
)
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
ACCOUNT_REVIEW_CHECKS = {
    "unclassified",
    "large-transfer",
    "large-withdrawal",
    "new-counterparty",
    "possible-duplicate",
    "derived-amount",
    "unverified-source",
}


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


def validate_foodpanda(manual_data, output, final_by_id, errors):
    source = manual_data.get("orders", []) if isinstance(manual_data, dict) else []
    has_published_surface = (
        "foodpandaOrders" in output
        or "foodpanda" in output.get("quality", {})
    )
    # Generated fixtures and pre-feature datasets legitimately have neither
    # source orders nor a Foodpanda surface. Once either exists, require the
    # complete reciprocal structure below.
    if not source and not has_published_surface:
        return
    published = output.get("foodpandaOrders", [])
    if not isinstance(source, list) or not isinstance(published, list):
        errors.append("Foodpanda source and published orders must be lists")
        return
    source_by_id = {
        order.get("orderId"): order for order in source if isinstance(order, dict)
    }
    published_by_id = {
        order.get("orderId"): order for order in published if isinstance(order, dict)
    }
    if len(source_by_id) != len(source):
        errors.append("manual Foodpanda orders contain a missing or duplicate orderId")
    if set(source_by_id) != set(published_by_id):
        errors.append("published Foodpanda orders do not exactly match the manual import")
    attached = {}
    for tx_id, transaction in final_by_id.items():
        detail = transaction.get("foodpanda")
        if not isinstance(detail, dict):
            continue
        order_id = detail.get("orderId")
        if order_id in attached:
            errors.append("Foodpanda order %s is attached to more than one transaction" % order_id)
        attached[order_id] = tx_id
        if order_id not in published_by_id:
            errors.append("transaction %s names unknown Foodpanda order %r" % (tx_id, order_id))
    for order_id, order in published_by_id.items():
        original = source_by_id.get(order_id)
        if not original:
            continue
        for field in ("date", "time", "fulfillment", "merchant", "amount", "category"):
            if order.get(field) != original.get(field):
                errors.append("Foodpanda order %s changed %s during build" % (order_id, field))
        tx_id = order.get("statementTransactionId")
        if tx_id:
            transaction = final_by_id.get(tx_id)
            if not transaction:
                errors.append("Foodpanda order %s names unknown transaction %s" % (order_id, tx_id))
            elif attached.get(order_id) != tx_id:
                errors.append("Foodpanda order %s link is not reciprocal" % order_id)
            elif (transaction.get("date") != order.get("date") or
                  transaction.get("amount") != order.get("amount")):
                errors.append("Foodpanda order %s link disagrees on date or amount" % order_id)
        elif order_id in attached:
            errors.append("unmatched Foodpanda order %s is attached to a transaction" % order_id)

    summary = output.get("quality", {}).get("foodpanda", {})
    matched = sum(1 for order in published if order.get("statementTransactionId"))
    groceries = sum(1 for order in published if order.get("category") == "Groceries")
    expected = {
        "orders": len(published),
        "matched": matched,
        "unmatched": len(published) - matched,
        "groceries": groceries,
    }
    if summary != expected:
        errors.append("Foodpanda quality summary disagrees with published orders")


def validate_shopee(manual_data, output, final_by_id, errors):
    source = manual_data.get("orders", []) if isinstance(manual_data, dict) else []
    has_surface = "shopeeOrders" in output or "shopee" in output.get("quality", {})
    if not source and not has_surface:
        return
    published = output.get("shopeeOrders", [])
    if not isinstance(source, list) or not isinstance(published, list):
        errors.append("Shopee source and published orders must be lists")
        return
    source_by_id = {o.get("orderId"): o for o in source if isinstance(o, dict)}
    published_by_id = {o.get("orderId"): o for o in published if isinstance(o, dict)}
    if len(source_by_id) != len(source) or set(source_by_id) != set(published_by_id):
        errors.append("published Shopee orders do not exactly match the manual import")
    expected_aggregates = {}
    aggregates = manual_data.get("statementAggregates", []) if isinstance(manual_data, dict) else []
    if not isinstance(aggregates, list):
        errors.append("manual Shopee statementAggregates must be a list")
        aggregates = []
    for index, aggregate in enumerate(aggregates, 1):
        if not isinstance(aggregate, dict):
            errors.append("Shopee statement aggregate %d must be an object" % index)
            continue
        transaction_id = aggregate.get("transactionId")
        order_ids = aggregate.get("orderIds")
        note = aggregate.get("note")
        if (not isinstance(transaction_id, str) or not transaction_id
                or not isinstance(order_ids, list) or len(order_ids) < 2
                or any(not isinstance(value, str) or not value for value in order_ids)
                or len(set(order_ids)) != len(order_ids)
                or not isinstance(note, str) or not note.strip()):
            errors.append("Shopee statement aggregate %d is incomplete" % index)
            continue
        if transaction_id in expected_aggregates:
            errors.append("Shopee statement aggregates repeat transaction %s" % transaction_id)
            continue
        expected_aggregates[transaction_id] = {
            "orderIds": order_ids,
            "note": note.strip(),
        }

    attached = {}
    surfaced_aggregates = set()
    for tx_id, transaction in final_by_id.items():
        primary = transaction.get("shopee")
        plural = transaction.get("shopeeOrders")
        if plural is not None:
            if not isinstance(plural, list) or not plural:
                errors.append("transaction %s has invalid Shopee order details" % tx_id)
                details = []
            else:
                details = plural
                if primary != details[0]:
                    errors.append("transaction %s has inconsistent primary Shopee order" % tx_id)
        else:
            details = [primary] if isinstance(primary, dict) else []
        if not details:
            if transaction.get("shopeeMatch") is not None:
                errors.append("transaction %s has Shopee match evidence without an order" % tx_id)
            continue
        if (not is_shopee_description(transaction.get("description", ""))
                or transaction.get("type") != "debit"):
            errors.append("transaction %s is not a Shopee charge but carries an order" % tx_id)
        published_ids = []
        total_cents = 0
        for detail in details:
            if not isinstance(detail, dict):
                errors.append("transaction %s has invalid Shopee order details" % tx_id)
                continue
            order_id = detail.get("orderId")
            published_ids.append(order_id)
            if order_id in attached:
                errors.append("Shopee order %s is attached to more than one transaction" % order_id)
            attached[order_id] = tx_id
            if order_id not in published_by_id:
                errors.append("transaction %s names unknown Shopee order %r" % (tx_id, order_id))
                continue
            published_order = published_by_id[order_id]
            for field in (
                    "merchant", "status", "amount", "items", "historyIndex", "category"):
                if detail.get(field) != published_order.get(field):
                    errors.append(
                        "transaction %s Shopee detail disagrees on %s" % (tx_id, field))
            total_cents += int(round(float(detail.get("amount", 0)) * 100))
        if total_cents != int(round(float(transaction.get("amount", 0)) * 100)):
            errors.append("transaction %s Shopee order totals disagree with its amount" % tx_id)
        match = transaction.get("shopeeMatch")
        expected = expected_aggregates.get(tx_id)
        if expected:
            surfaced_aggregates.add(tx_id)
            if published_ids != expected["orderIds"]:
                errors.append("transaction %s disagrees with its Shopee aggregate" % tx_id)
            if match != {"kind": "aggregate", "note": expected["note"]}:
                errors.append("transaction %s has inconsistent Shopee aggregate evidence" % tx_id)
        else:
            if len(details) != 1:
                errors.append("transaction %s has an unreviewed Shopee aggregate" % tx_id)
            expected_match = {
                "kind": "exact",
                "note": "The order total uniquely matches this Shopee statement charge.",
            }
            if match != expected_match:
                errors.append("transaction %s has inconsistent Shopee exact-match evidence" % tx_id)
    for transaction_id in set(expected_aggregates) - surfaced_aggregates:
        errors.append("Shopee statement aggregate %s is not published" % transaction_id)
    for order_id, order in published_by_id.items():
        original = source_by_id.get(order_id)
        if not original:
            continue
        for field in ("merchant", "status", "amount", "items", "historyIndex"):
            if order.get(field) != original.get(field):
                errors.append("Shopee order %s changed %s during build" % (order_id, field))
        tx_id = order.get("statementTransactionId")
        if tx_id:
            transaction = final_by_id.get(tx_id)
            if not transaction or attached.get(order_id) != tx_id:
                errors.append("Shopee order %s link is not reciprocal" % order_id)
        elif order_id in attached:
            errors.append("unmatched Shopee order %s is attached to a transaction" % order_id)
    summary = output.get("quality", {}).get("shopee", {})
    matched = sum(1 for order in published if order.get("statementTransactionId"))
    expected = {
        "orders": len(published), "matched": matched,
        "unmatched": len(published) - matched,
        "groceries": sum(1 for order in published if order.get("category") == "Groceries"),
    }
    if summary != expected:
        errors.append("Shopee quality summary disagrees with published orders")


TRIP_DETAIL_FIELDS = (
    "bookingNo", "status", "productType", "bookingDate", "productName", "travelTime",
    "traveller", "currency", "amount", "sourceFile",
)


def validate_partner_travel(manual_data, output, final_by_id, errors):
    """Copied partner charges must match their manual source and stay outside
    the transactions list, so nothing of the other person's leaks into totals."""
    published = output.get("partnerTravel")
    if not manual_data and published is None:
        return
    if not isinstance(published, dict) or not isinstance(published.get("charges"), list):
        errors.append("partnerTravel must be an object with a charges list")
        return
    source_charges = manual_data.get("charges") if isinstance(manual_data, dict) else None
    if not isinstance(source_charges, list):
        if published["charges"]:
            errors.append("partnerTravel is published without a manual source")
        return
    if published.get("paidBy") != str(manual_data.get("paidBy") or "").strip():
        errors.append("partnerTravel paidBy does not match the manual file")
    by_source = {}
    for row in source_charges:
        if isinstance(row, dict) and row.get("id"):
            by_source[row["id"]] = row
    seen = set()
    for index, row in enumerate(published["charges"], 1):
        label = "partner charge %d" % index
        if not isinstance(row, dict) or not row.get("id"):
            errors.append("%s is malformed" % label)
            continue
        tx_id = row["id"]
        if tx_id in seen:
            errors.append("%s repeats id %s" % (label, tx_id))
        seen.add(tx_id)
        if tx_id in final_by_id:
            errors.append("%s collides with a transaction id" % label)
        source = by_source.get(tx_id)
        if source is None:
            errors.append("%s (%s) is not in manual/partner_travel.json" % (label, tx_id))
            continue
        for field in ("date", "month", "type", "category", "description"):
            if row.get(field) != source.get(field):
                errors.append("%s %s was changed in publishing" % (label, field))
        try:
            if round(float(source.get("amount")), 2) != row.get("amount"):
                errors.append("%s amount was changed in publishing" % label)
        except (TypeError, ValueError):
            errors.append("%s has an invalid source amount" % label)
        if not is_date_key(row.get("date")) or not is_month_key(row.get("month")):
            errors.append("%s has an invalid date or month" % label)
    if len(seen) != len(by_source):
        errors.append("partnerTravel publishes %d charge(s) but the manual file holds %d"
                      % (len(seen), len(by_source)))


def validate_trip(manual_data, output, final_by_id, errors, reconciliation_data=None):
    """Re-derive exact and reviewed Trip.com links from their private sources."""
    source = manual_data.get("bookings", []) if isinstance(manual_data, dict) else None
    summary = output.get("quality", {}).get("trip")
    if not source and summary is None:
        # No export on disk and a dashboard built before the surface existed.
        return
    if not isinstance(source, list):
        errors.append("manual Trip.com bookings must be a list")
        return
    source_by_no = {}
    for booking in source:
        if not isinstance(booking, dict):
            errors.append("manual Trip.com bookings must be objects")
            return
        booking_no = normalize_trip_booking_no(booking.get("bookingNo"))
        if not booking_no or booking_no in source_by_no:
            errors.append("manual Trip.com bookings contain a missing or duplicate booking number")
            return
        source_by_no[booking_no] = booking
    if "tripBookings" in output:
        errors.append("the full Trip.com export must not be published to the dashboard")

    reconciliation_data = reconciliation_data or {"links": []}
    expected_manual = {}
    links = reconciliation_data.get("links", []) if isinstance(reconciliation_data, dict) else None
    if not isinstance(links, list):
        errors.append("manual Trip.com reconciliation must contain a links list")
        links = []
    for index, link in enumerate(links, 1):
        if not isinstance(link, dict):
            errors.append("Trip.com reconciliation %d must be an object" % index)
            continue
        transaction_ids = link.get("transactionIds")
        booking_nos = link.get("bookingNos")
        kind = link.get("kind")
        note = link.get("note")
        if (not isinstance(transaction_ids, list) or not transaction_ids
                or not isinstance(booking_nos, list) or not booking_nos
                or not isinstance(kind, str) or not kind
                or not isinstance(note, str) or not note):
            errors.append("Trip.com reconciliation %d is incomplete" % index)
            continue
        normalized_nos = [normalize_trip_booking_no(value) for value in booking_nos]
        for transaction_id in transaction_ids:
            if transaction_id in expected_manual:
                errors.append("Trip.com reconciliation repeats transaction %s" % transaction_id)
                continue
            expected_manual[transaction_id] = {
                "bookingNos": normalized_nos,
                "kind": kind,
                "note": note,
            }

    attached_auto = {}
    attached_all = set()
    matched_charge_ids = set()
    matched_refund_ids = set()
    matched_cancelled_charge_ids = set()
    for tx_id, transaction in final_by_id.items():
        detail = transaction.get("tripBooking")
        plural = transaction.get("tripBookings")
        if plural is not None:
            if not isinstance(plural, list) or not plural:
                errors.append("transaction %s has invalid Trip.com booking details" % tx_id)
                details = []
            else:
                details = plural
                if detail != details[0]:
                    errors.append("transaction %s has inconsistent primary Trip.com booking" % tx_id)
        else:
            details = [detail] if detail is not None else []
        marker = transaction.get("trip")
        is_trip_row = is_trip_description(transaction.get("description", ""))
        expected_marker = (
            {"status": "booking-matched" if details else "unmatched"}
            if is_trip_row else None
        )
        if marker != expected_marker:
            errors.append("transaction %s has an inconsistent Trip.com marker" % tx_id)
        if not details:
            if transaction.get("displayNameSource") == "trip-booking":
                errors.append("transaction %s claims a Trip.com name without a booking" % tx_id)
            continue
        if not is_trip_row or transaction.get("type") not in ("debit", "refund"):
            errors.append("transaction %s is not a Trip.com charge but carries a booking" % tx_id)

        manual_link = expected_manual.get(tx_id)
        published_nos = []
        originals = []
        for detail in details:
            if not isinstance(detail, dict):
                errors.append("transaction %s has invalid Trip.com booking details" % tx_id)
                continue
            booking_no = normalize_trip_booking_no(detail.get("bookingNo"))
            published_nos.append(booking_no)
            original = source_by_no.get(booking_no)
            if not original:
                errors.append("transaction %s names unknown Trip.com booking %r" % (tx_id, booking_no))
                continue
            originals.append(original)
            attached_all.add(booking_no)
            if detail.get("currency") != "SGD":
                errors.append("transaction %s is linked to a non-SGD Trip.com booking" % tx_id)
            for field in TRIP_DETAIL_FIELDS:
                published = detail.get(field)
                expected = original.get(field)
                if field == "bookingNo":
                    expected = normalize_trip_booking_no(expected)
                elif field == "currency":
                    expected = str(expected or "").strip().upper()
                elif field == "amount":
                    try:
                        expected = None if expected is None else round(float(expected), 2)
                    except (TypeError, ValueError):
                        expected = object()
                else:
                    expected = str(expected or "").strip()
                if published != expected:
                    errors.append(
                        "Trip.com booking %s changed %s during build" % (booking_no, field)
                    )

        if manual_link:
            if published_nos != manual_link["bookingNos"]:
                errors.append("transaction %s disagrees with the reviewed Trip.com link" % tx_id)
            expected_match = {"kind": manual_link["kind"], "note": manual_link["note"]}
            if transaction.get("tripMatch") != expected_match:
                errors.append("transaction %s changed its Trip.com reconciliation evidence" % tx_id)
        else:
            if len(details) != 1 or transaction.get("type") != "debit":
                errors.append(
                    "transaction %s is not a Trip.com charge with an automatic exact match "
                    "and has no reviewed reconciliation" % tx_id
                )
            if published_nos:
                booking_no = published_nos[0]
                if booking_no in attached_auto:
                    errors.append(
                        "Trip.com booking %s is attached to more than one automatic charge"
                        % booking_no
                    )
                attached_auto[booking_no] = tx_id
            exact_detail = details[0] if len(details) == 1 and isinstance(details[0], dict) else {}
            try:
                same_amount = (
                    int(round(float(exact_detail.get("amount")) * 100))
                    == int(round(float(transaction.get("amount")) * 100))
                )
            except (TypeError, ValueError):
                same_amount = False
            if not same_amount:
                errors.append("transaction %s Trip.com link disagrees on amount" % tx_id)
            booking_day = parse_trip_date(exact_detail.get("bookingDate"))
            row_day = parse_trip_date(transaction.get("date"))
            if (booking_day is None or row_day is None
                    or abs((row_day - booking_day).days) > TRIP_MATCH_WINDOW_DAYS):
                errors.append("transaction %s Trip.com link is outside the match window" % tx_id)
            if (transaction.get("tripMatch") is not None
                    and transaction.get("tripMatch", {}).get("kind") != "exact"):
                errors.append("transaction %s changed its automatic Trip.com evidence" % tx_id)

        product_names = list(dict.fromkeys(
            str(original.get("productName") or "").strip() for original in originals
        ))
        expected_name = (
            product_names[0] if len(product_names) == 1
            else "%d Trip.com bookings" % len(details)
        )
        if (transaction.get("displayNameSource") == "trip-booking"
                and transaction.get("displayName") != expected_name):
            errors.append(
                "transaction %s Trip.com display name is not the product name derived from its bookings"
                % tx_id
            )
        if transaction.get("displayNameSource") not in ("trip-booking", "override"):
            errors.append("transaction %s Trip.com link has no display-name source" % tx_id)

        if transaction.get("type") == "refund":
            matched_refund_ids.add(tx_id)
        else:
            matched_charge_ids.add(tx_id)
            if any(
                    str(original.get("status") or "").strip().lower() == "cancelled"
                    for original in originals):
                matched_cancelled_charge_ids.add(tx_id)

    missing_reviewed = set(expected_manual) - set(final_by_id)
    if missing_reviewed:
        errors.append("reviewed Trip.com links name transactions missing from the dashboard")
    if not isinstance(summary, dict):
        errors.append("Trip.com quality summary is missing")
        return
    if (summary.get("bookings") != len(source_by_no)
            or summary.get("matched") != len(matched_charge_ids)):
        errors.append("Trip.com quality summary disagrees with published links")
    optional_counts = {
        "matchedRefunds": len(matched_refund_ids),
        "matchedTransactions": len(matched_charge_ids | matched_refund_ids),
        "matchedBookings": len(attached_all),
    }
    for key, expected in optional_counts.items():
        if key in summary and summary.get(key) != expected:
            errors.append("Trip.com quality summary miscounts %s" % key)
    if summary.get("matchedCancelled") != len(matched_cancelled_charge_ids):
        errors.append("Trip.com quality summary miscounts cancelled links")


def validate_grab(manual_data, history_stats, output, final_by_id, errors):
    source = manual_data.get("receipts", []) if isinstance(manual_data, dict) else []
    has_surface = "grabReceipts" in output or "grab" in output.get("quality", {})
    if not source and not has_surface:
        return
    published = output.get("grabReceipts", [])
    if not isinstance(source, list) or not isinstance(published, list):
        errors.append("Grab source and published receipts must be lists")
        return
    source_by_id = {r.get("receiptId"): r for r in source if isinstance(r, dict)}
    published_by_id = {r.get("receiptId"): r for r in published if isinstance(r, dict)}
    if len(source_by_id) != len(source) or set(source_by_id) != set(published_by_id):
        errors.append("published Grab receipts do not exactly match the manual import")

    detail_fields = (
        "receiptId", "date", "time", "service", "category", "amount", "currency",
        "profile", "corporate", "merchant", "items", "pickup", "dropoff",
        "paymentMethod", "pickupLabel", "dropoffLabel", "webHistoryAmount",
        "webHistoryAmountDiffers",
        "evidenceSources",
    )
    attached = {}
    for tx_id, transaction in final_by_id.items():
        detail = transaction.get("grab")
        if not isinstance(detail, dict):
            if is_grab_description(transaction.get("description")):
                errors.append("Grab statement transaction %s has no Grab status" % tx_id)
            continue
        receipts = detail.get("receipts", [])
        status = detail.get("status")
        if status == "unreconciled":
            if receipts != [] or detail.get("kind") != "wallet-funding":
                errors.append("unreconciled Grab transaction %s has receipt details" % tx_id)
            if detail.get("corporate"):
                errors.append("unreconciled Grab transaction %s is marked corporate" % tx_id)
            if (transaction.get("categorySource") == "grab-unreconciled"
                    and transaction.get("category") != "Wallet funding"):
                errors.append("unreconciled Grab transaction %s has invalid category" % tx_id)
            continue
        if status != "receipt-matched" or detail.get("kind") != "receipt":
            errors.append("transaction %s has invalid Grab status" % tx_id)
        if not isinstance(receipts, list) or not receipts:
            errors.append("transaction %s has invalid Grab receipt details" % tx_id)
            continue
        for receipt in receipts:
            receipt_id = receipt.get("receiptId") if isinstance(receipt, dict) else None
            if receipt_id not in published_by_id:
                errors.append("transaction %s names unknown Grab receipt %r" % (tx_id, receipt_id))
                continue
            if tx_id in attached.setdefault(receipt_id, set()):
                errors.append("transaction %s repeats Grab receipt %s" % (tx_id, receipt_id))
            attached[receipt_id].add(tx_id)
            published_receipt = published_by_id[receipt_id]
            for field in detail_fields:
                if receipt.get(field) != published_receipt.get(field):
                    errors.append("transaction %s Grab detail disagrees on %s" % (tx_id, field))
        category_source = transaction.get("categorySource")
        if category_source == "grab-corporate":
            if not detail.get("corporate") or transaction.get("category") != "Payment":
                errors.append("corporate Grab transaction %s is not excluded" % tx_id)
        elif category_source == "grab-receipt":
            receipt_categories = {
                receipt.get("category") for receipt in receipts
                if isinstance(receipt, dict)
            }
            if (detail.get("corporate") or len(receipt_categories) != 1
                    or transaction.get("category") not in receipt_categories):
                errors.append("personal Grab transaction %s has invalid classification" % tx_id)

    source_fields = tuple(
        field for field in detail_fields
        if field not in ("pickupLabel", "dropoffLabel")
    ) + ("eligibleForPersonalFinance",)
    for receipt_id, receipt in published_by_id.items():
        original = source_by_id.get(receipt_id)
        if not original:
            continue
        for field in source_fields:
            if receipt.get(field) != original.get(field):
                errors.append("Grab receipt %s changed %s during build" % (receipt_id, field))
        expected_ids = set(receipt.get("statementTransactionIds", []))
        if expected_ids != attached.get(receipt_id, set()):
            errors.append("Grab receipt %s links are not reciprocal" % receipt_id)
        if receipt.get("profile") == "unknown" and expected_ids:
            errors.append("unknown-profile Grab receipt %s was matched" % receipt_id)

    summary = output.get("quality", {}).get("grab", {})
    expected = {
        "receipts": len(published),
        "webHistoryRecords": history_stats["records"],
        "webHistoryAdded": history_stats["added"],
        "webHistoryEnriched": history_stats["enriched"],
        "personal": sum(1 for receipt in published if receipt.get("profile") == "personal"),
        "corporate": sum(1 for receipt in published if receipt.get("corporate")),
        "unknownProfile": sum(
            1 for receipt in published if receipt.get("profile") == "unknown"
        ),
        "matched": sum(1 for receipt in published if receipt.get("statementTransactionIds")),
        "matchedTransactions": sum(
            1 for transaction in final_by_id.values()
            if transaction.get("grab", {}).get("status") == "receipt-matched"
        ),
        "statementTransactions": sum(
            1 for transaction in final_by_id.values()
            if (transaction.get("type") != "refund"
                and is_grab_description(transaction.get("description")))
        ),
        "unreconciledTransactions": sum(
            1 for transaction in final_by_id.values()
            if (transaction.get("type") != "refund"
                and transaction.get("grab", {}).get("status") == "unreconciled")
        ),
        "corporateExcluded": sum(
            1 for receipt in published
            if receipt.get("corporate") and receipt.get("statementTransactionIds")
        ),
    }
    if summary != expected:
        errors.append("Grab quality summary disagrees with published receipts")


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
    legacy = load_optional(
        os.path.join(MANUAL_DIR, "legacy_transactions.json"), {"transactions": []})
    owner_data = load_optional(
        os.path.join(MANUAL_DIR, "owner_tags.json"), {"tags": {}, "tagsById": {}})
    override_data = load_optional(
        os.path.join(MANUAL_DIR, "transaction_overrides.json"), {"overridesById": {}})
    remark_data = load_optional(
        os.path.join(MANUAL_DIR, "transaction_remarks.json"), {"remarksById": {}})
    risk_data = load_optional(
        os.path.join(MANUAL_DIR, "risk_reviews.json"), {"recognizedSignals": []})
    audit_data = load_optional(
        os.path.join(MANUAL_DIR, "audit_history.json"), {"entries": []})
    account_review_data = load_optional(
        os.path.join(MANUAL_DIR, "account_reviews.json"), {"recognizedSignals": []})
    salary_data = load_optional(
        os.path.join(MANUAL_DIR, "salary.json"), {"steps": [], "years": []})
    game_sales_data = load_optional(
        os.path.join(MANUAL_DIR, "game_sales.json"), {"sales": []})
    partner_travel_data = load_optional(
        os.path.join(MANUAL_DIR, "partner_travel.json"), {})
    owner_rules_data = load_optional(
        os.path.join(MANUAL_DIR, "owner_rules.json"), {"rules": {}, "confirmed": []})
    manual_settlements = load_optional(
        os.path.join(MANUAL_DIR, "settlements.json"),
        {"openingBalances": [], "payments": []})
    manual_identity = load_optional(
        os.path.join(MANUAL_DIR, "identity.json"),
        {"knownAccounts": {}, "trustedCounterparties": []})
    foodpanda_data = load_optional(
        os.path.join(MANUAL_DIR, "foodpanda_orders.json"), {"orders": []})
    shopee_data = load_optional(
        os.path.join(MANUAL_DIR, "shopee_orders.json"), {"orders": []})
    trip_data = load_optional(
        os.path.join(MANUAL_DIR, "trip_bookings.json"), {"bookings": []})
    trip_reconciliation_data = load_optional(
        os.path.join(MANUAL_DIR, "trip_booking_reconciliation.json"), {"links": []})
    grab_data = load_optional(
        os.path.join(MANUAL_DIR, "grab_receipts.json"), {"receipts": []})
    grab_web_data = load_optional(
        os.path.join(MANUAL_DIR, "grab_web_history.json"),
        {"fields": [], "records": []})
    grab_data, grab_history_stats = merge_grab_web_history(grab_data, grab_web_data)
    insurance_path = os.path.join(MANUAL_DIR, "insurance.json")
    insurance_data = load_optional(insurance_path, {"people": []})

    errors = []
    warnings = []
    generation_values = [
        payload.get("generationId") for payload in (cards, account, output)
    ]
    generations = {value for value in generation_values if value}
    if generations and any(not value for value in generation_values):
        errors.append("generated data files are missing an import generation")
    elif len(generations) > 1:
        errors.append("generated data files belong to different import generations")
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
    validate_foodpanda(foodpanda_data, output, final_by_id, errors)
    validate_shopee(shopee_data, output, final_by_id, errors)
    validate_trip(
        trip_data, output, final_by_id, errors, trip_reconciliation_data
    )
    validate_grab(grab_data, grab_history_stats, output, final_by_id, errors)
    validate_partner_travel(partner_travel_data, output, final_by_id, errors)
    if (os.path.exists(insurance_path) or "insurance" in output) and \
            output.get("insurance") != prepare_insurance(insurance_data):
        errors.append("published insurance data does not match the manual source")
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
    shopee_categories_by_id = {
        order.get("orderId"): order.get("category")
        for order in output.get("shopeeOrders", [])
        if isinstance(order, dict)
    }
    for row in output.get("transactions", []):
        tx_id = row.get("id")
        category = row.get("category")
        rule_category = row.get("ruleCategory")
        override = overrides_by_id.get(tx_id, {})
        foodpanda_category = (
            row.get("categorySource") == "foodpanda-order"
            and isinstance(row.get("foodpanda"), dict)
            and category == "Groceries"
            and re.match(r"^pandamart\b", row["foodpanda"].get("merchant", ""), re.I)
        )
        shopee_category = (
            row.get("categorySource") == "shopee-order"
            and isinstance(row.get("shopee"), dict)
            and category == shopee_categories_by_id.get(row["shopee"].get("orderId"))
        )
        grab_category = (
            row.get("categorySource") in (
                "grab-receipt", "grab-corporate", "grab-unreconciled"
            )
            and isinstance(row.get("grab"), dict)
        )
        if (category != rule_category and "category" not in override
                and not foodpanda_category and not shopee_category
                and not grab_category):
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
        if "destination" in override and built.get("destination") != override["destination"]:
            errors.append("destination override %s was not applied" % tx_id)

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
        if year_row.get("growth") is not None:
            checked_amount(year_row.get("growth"), label + " growth", minimum_exclusive=0)

    # growth is a hand-entered ratio against the previous year's income. A
    # stale one after an income correction would misreport the headline KPI,
    # so it must agree with the two income figures it summarises.
    income_by_year = {}
    for year_row in salary_years:
        if isinstance(year_row, dict) and isinstance(year_row.get("year"), int):
            income_by_year[year_row["year"]] = year_row.get("income")
    for year_row in salary_years:
        if not isinstance(year_row, dict) or not isinstance(year_row.get("year"), int):
            continue
        year, growth = year_row["year"], year_row.get("growth")
        income, previous = year_row.get("income"), income_by_year.get(year - 1)
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool)
                   for value in (growth, income, previous)):
            continue
        if previous > 0 and abs(growth - income / previous) > 0.001:
            errors.append(
                "salary year %d growth %.4f disagrees with income %.2f / %.2f = %.4f"
                % (year, growth, income, previous, income / previous))

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

    # Own-account labels and trusted counterparties are private, so they live
    # in manual/identity.json and reach the dashboard only through the copy
    # embedded at build time. Same drift risk as settlements: a stale copy
    # silently labels a transfer with the wrong account or trusts the wrong
    # name, so the two must agree.
    manual_known = manual_identity.get("knownAccounts", {})
    if not isinstance(manual_known, dict):
        errors.append("identity.knownAccounts in manual/identity.json must be an object")
        manual_known = {}
    for known_account, label in manual_known.items():
        if not (isinstance(known_account, str) and known_account.isdigit()):
            errors.append(
                "identity.knownAccounts key %r is not an account number" % known_account)
        if not isinstance(label, str) or not label.strip():
            errors.append("identity.knownAccounts[%r] has no label" % known_account)
    manual_trusted = manual_identity.get("trustedCounterparties", [])
    if not isinstance(manual_trusted, list) or any(
            not isinstance(name, str) or not name.strip() for name in manual_trusted):
        errors.append(
            "identity.trustedCounterparties in manual/identity.json must be a list "
            "of non-empty names")
        manual_trusted = []

    embedded_identity = output.get("identity", {})
    if not isinstance(embedded_identity, dict):
        errors.append("identity must be an object")
        embedded_identity = {}
    # An absent key reads as empty, so a tree with no identity at all is only
    # flagged once manual/identity.json actually holds something.
    for field, expected, empty in (("knownAccounts", manual_known, {}),
                                   ("trustedCounterparties", manual_trusted, [])):
        if json.dumps(expected, sort_keys=True) != \
                json.dumps(embedded_identity.get(field, empty), sort_keys=True):
            errors.append(
                "identity.%s in the dashboard differs from manual/identity.json - "
                "re-run scripts/build_data.py" % field)

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

    legacy_account_review_ids = account_review_data.get("reviewedIds", [])
    if legacy_account_review_ids:
        warnings.append(
            "account_reviews.json still holds row-only reviewedIds; run "
            "scripts/migrate_account_reviews_to_signals.js"
        )
    account_signals = account_review_data.get("recognizedSignals", [])
    if not isinstance(account_signals, list):
        errors.append("account_reviews.recognizedSignals must be a list")
        account_signals = []
    seen_account_ids = set()
    for index, entry in enumerate(account_signals, 1):
        label = "recognized bank check %d" % index
        if not isinstance(entry, dict):
            errors.append("%s must be an object" % label)
            continue
        tx_id = entry.get("id")
        checks = entry.get("checks")
        if tx_id in seen_account_ids:
            errors.append("account reviews repeat transaction %s" % tx_id)
        seen_account_ids.add(tx_id)
        if tx_id not in account_by_id:
            errors.append(
                "%s no longer matches a statement row (%s)" % (label, tx_id)
            )
        if (not isinstance(checks, list) or not checks or
                any(not isinstance(check, str) or not check for check in checks)):
            errors.append("%s must list at least one check" % label)
        elif len(checks) != len(set(checks)):
            errors.append("%s repeats a check" % label)
        elif set(checks) - ACCOUNT_REVIEW_CHECKS:
            errors.append("%s contains an unknown check" % label)

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
        ("overlapping category-rule", review.get("categoryRuleOverlap", {})),
        ("Lady card rule/unassigned", review.get("ladyRuleOrUnassigned", {})),
        ("settlement-impacting merchant-rule", review.get("splitByRule", {})),
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
