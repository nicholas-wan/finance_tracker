#!/usr/bin/env python3
"""Turn the local Gmail Grab-receipt capture into a compact manual source file."""

import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "manual" / "grab_receipt_search_raw.json"
DEFAULT_SUPPLEMENTAL = ROOT / "manual" / "grab_receipt_search_supplemental.json"
DEFAULT_OUTPUT = ROOT / "manual" / "grab_receipts.json"
HEADER = re.compile(
    r"(?im)^(?:Grab <no-reply@grab\.com>|no-reply@grab\.com)\s*$"
)
MONEY = r"(?:S\$|SGD|RM)\s*([0-9]+(?:\.[0-9]{1,2})?)"


def clean(value):
    return re.sub(r"\s+", " ", value or "").strip(" \t\r\n.:")


def next_value(lines, labels):
    labels = tuple(label.lower() for label in labels)
    for index, line in enumerate(lines):
        normalized = clean(line).lower()
        if normalized.rstrip(":") not in labels:
            continue
        for candidate in lines[index + 1:]:
            candidate = clean(candidate)
            if candidate:
                return candidate
    return ""


def parse_datetime(text):
    patterns = [
        r"Picked up on\s+(\d{1,2}\s+[A-Za-z]+\s+\d{4})",
        r"(?:Pickup|Pick-up) time:\s*(\d{1,2}\s+[A-Za-z]+\s+\d{2,4})\s+(\d{1,2}:\d{2})",
        r"DATE\s*\|\s*TIME\s+(\d{1,2}\s+[A-Za-z]+\s+\d{2,4})\s+(\d{1,2}:\d{2})",
    ]
    for pattern in patterns:
        match = re.search(pattern, clean(text), re.I)
        if not match:
            continue
        raw_date = match.group(1)
        for fmt in ("%d %B %Y", "%d %b %Y", "%d %b %y", "%d %B %y"):
            try:
                date = datetime.strptime(raw_date, fmt).date().isoformat()
                return date, match.group(2) if match.lastindex and match.lastindex > 1 else ""
            except ValueError:
                pass
    return "", ""


def parse_amount(text):
    normalized = clean(text)
    patterns = [
        r"\bTOTAL\s+" + MONEY + r"\s+(?:DATE|Pickup|Pick-up)",
        r"\bTotal Paid\s+" + MONEY,
        r"\bTOTAL\s+" + MONEY,
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized, re.I)
        if match:
            currency_match = re.search(r"(?:S\$|SGD|RM)", match.group(0), re.I)
            currency = "MYR" if currency_match and currency_match.group(0).upper() == "RM" else "SGD"
            return round(float(match.group(1)), 2), currency
    return None, ""


def parse_profile(lines):
    for index, line in enumerate(lines):
        if clean(line).lower().rstrip(":") != "profile":
            continue
        for candidate in lines[index + 1:]:
            candidate = clean(candidate)
            if candidate:
                return candidate.lower()
    normalized = clean(" ".join(lines))
    match = re.search(r"\bProfile[.:]?\s+(PERSONAL|BUSINESS|CORPORATE)\b", normalized, re.I)
    return match.group(1).lower() if match else "unknown"


def parse_items(lines):
    items = []
    for line in lines:
        line = clean(line.replace("\xa0", " "))
        match = re.match(r"\d+x\s+(.+?)(?:\s+(?:S\$|SGD|RM)\s*[0-9]|$)", line, re.I)
        if not match:
            continue
        item = clean(match.group(1))
        if item and item not in items:
            items.append(item)
    return items


def parse_route(lines):
    start = next((i for i, line in enumerate(lines) if clean(line).lower() == "your trip"), None)
    if start is None:
        return "", ""
    route = []
    for line in lines[start + 1:]:
        value = clean(line)
        if not value or value == "⋮" or re.match(r"^\d+(?:\.\d+)?\s+km\b", value, re.I):
            continue
        if value.lower() in ("pick-up", "pickup", "drop-off", "dropoff"):
            route.append(value.lower())
            continue
        if re.match(r"^\d{1,2}:\d{2}(?:AM|PM)$", value, re.I):
            continue
        if value.startswith("Grab Singapore") or value.startswith("Follow us"):
            break
        route.append(value)
        if len(route) >= 4 and "drop-off" in route:
            break
    try:
        pickup_index = next(i for i, value in enumerate(route) if value in ("pick-up", "pickup"))
        dropoff_index = next(i for i, value in enumerate(route) if value in ("drop-off", "dropoff"))
        pickup = route[pickup_index + 1] if pickup_index + 1 < dropoff_index else ""
        dropoff = route[dropoff_index + 1] if dropoff_index + 1 < len(route) else ""
        return pickup, dropoff
    except StopIteration:
        addresses = []
        compact = [clean(line) for line in lines[start + 1:] if clean(line)]
        for index, value in enumerate(compact[:-1]):
            if value == "⋮" or re.match(r"^\d+(?:\.\d+)?\s+km\b", value, re.I):
                continue
            if re.match(r"^\d{1,2}:\d{2}(?:AM|PM)$", compact[index + 1], re.I):
                addresses.append(value)
                if len(addresses) == 2:
                    return addresses[0], addresses[1]
        return "", ""


def ride_service(lines):
    for index, line in enumerate(lines):
        if not re.search(r"Hope you (?:enjoyed your|had an enjoyable) ride", line, re.I):
            continue
        for candidate in reversed(lines[:index]):
            candidate = clean(candidate)
            if not candidate or candidate.lower() in (
                "download", "to me", "more", "reply", "add reaction",
            ):
                continue
            if re.match(r"^(?:Grab <|no-reply@|\w{3},\s)", candidate, re.I):
                continue
            return candidate
    return "Grab ride"


def parse_segment(segment):
    lines = segment.replace("\r", "").split("\n")
    normalized = clean(segment)
    date, time = parse_datetime(segment)
    amount, currency = parse_amount(segment)
    profile = parse_profile(lines)
    ride = bool(re.search(r"\bBooking ID:\s*[A-Z0-9-]+", segment, re.I)) or bool(
        re.search(r"Hope you (?:enjoyed your|had an enjoyable) ride", segment, re.I)
    )
    dine_in = bool(re.search(r"Type of order:\s*Dine-in", normalized, re.I))
    food = dine_in or bool(re.search(r"Hope you enjoyed your food", segment, re.I))
    if not (ride or food):
        return None

    if ride:
        service = ride_service(lines)
        category = "Transport"
        merchant = "Grab"
        pickup, dropoff = parse_route(lines)
        items = []
    else:
        service = "Dine-in Discount" if dine_in else next_value(lines, ("vehicle type",)) or "GrabFood"
        category = "Food & dining"
        merchant = next_value(lines, ("order from", "restaurant")) or "Grab"
        pickup = dropoff = ""
        items = parse_items(lines)

    id_match = re.search(r"\b(?:Booking ID|Booking code|Short order ID):?\s*([A-Z0-9-]+)", normalized, re.I)
    receipt_id = id_match.group(1).upper() if id_match else "GRAB-" + hashlib.sha256(
        segment.encode("utf-8")
    ).hexdigest()[:16].upper()
    payment = next_value(lines, ("paid by", "payment method"))
    corporate = profile in ("business", "corporate")
    return {
        "receiptId": receipt_id,
        "date": date,
        "time": time,
        "service": service,
        "category": category,
        "amount": amount,
        "currency": currency,
        "profile": profile,
        "corporate": corporate,
        "eligibleForPersonalFinance": profile == "personal",
        "merchant": merchant,
        "items": items,
        "pickup": pickup,
        "dropoff": dropoff,
        "paymentMethod": payment,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--supplemental", type=Path, default=DEFAULT_SUPPLEMENTAL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    source = json.loads(args.input.read_text(encoding="utf-8"))
    sources = [source]
    if args.supplemental.exists() and args.supplemental != args.input:
        sources.append(json.loads(args.supplemental.read_text(encoding="utf-8")))
    receipts = []
    seen = set()
    skipped = 0
    for capture in sources:
        for conversation in capture.get("openedBodies", []):
            text = conversation.get("text", "")
            starts = [match.start() for match in HEADER.finditer(text)]
            for index, start in enumerate(starts):
                segment = text[start:starts[index + 1] if index + 1 < len(starts) else len(text)]
                receipt = parse_segment(segment)
                if not receipt:
                    skipped += 1
                    continue
                if receipt["receiptId"] in seen:
                    continue
                seen.add(receipt["receiptId"])
                receipts.append(receipt)
    receipts.sort(key=lambda receipt: (receipt["date"], receipt["time"], receipt["receiptId"]), reverse=True)
    output = {
        "source": source.get("source", "gmail-grab-receipts"),
        "extractedAt": max(
            (capture.get("extractedAt", "") for capture in sources), default=""
        ),
        "profilePolicy": "Only receipts explicitly marked personal are eligible for finance matching.",
        "receipts": receipts,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("Parsed %d Grab receipts (%d skipped message segment(s))." % (len(receipts), skipped))


if __name__ == "__main__":
    main()
