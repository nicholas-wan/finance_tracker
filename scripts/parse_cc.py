# Parses UOB credit card statement PDFs into card transactions.
# Replaces the monthly "* CC *" sheets that used to live in Finances.xlsx.
#
# Each statement holds one section per card (UOB ONE, Lady's Solitaire). A row is
# a date pair, a description, an optional "Ref No." tail, then the amount, with
# "CR" marking a credit.

import csv
import glob
import json
import os
import re

from pypdf import PdfReader
from data_ids import assign_provenance, source_name

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(REPO_ROOT, "app", "data", "card_transactions.json")

MONTHS = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
          "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}
MON_RE = "JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC"

# "12 JUN 10 JUN SHOPEE SINGAPORE ..." - post date, transaction date, description.
ROW_RE = re.compile(r"^(\d{1,2})\s+(%s)\s+(\d{1,2})\s+(%s)\s+(.*)$" % (MON_RE, MON_RE), re.I)
# The lookbehind also rejects a comma so "JPY1,220.00" cannot be read as 220.00,
# and the second alternative accepts thousands printed without separators.
AMOUNT_RE = re.compile(
    r"(?<![\w.,])(\d{1,3}(?:,\d{3})+\.\d{2}|\d+\.\d{2})\s*(CR)?\s*$", re.I)
STATEMENT_DATE_RE = re.compile(r"Statement Date\s+(\d{1,2})\s+(%s)\s+(\d{4})" % MON_RE, re.I)
# Foreign charges print the original amount on its own line above the SGD one.
# The lookahead rejects wrapped description tails such as "PTE 12.00" or
# "GST 5.00", which are not currency codes but match the shape.
FX_RE = re.compile(r"^(?!PTE\b|LTD\b|GST\b)[A-Z]{3}\s+[\d,]+\.\d{2}$")
# A card section header is one of the names UOB prints, alone on its line and in
# the table's upper case. The old catch-all "[A-Z' ]+CARD" alternative made any
# line of letters ending in CARD a section header, so a wrapped continuation line
# such as "GIFT CARD" opened a phantom section: every later row was attributed to
# it and the run failed closed with a confusing "unchecked section". Case matters
# because the statement's prose and footers are full of mixed-case headings
# ("UOB Credit Card", "One Credit Card", "Mondays with One Card") that are not
# sections. Matching is still open-ended after "UOB " so a new card product is
# read as its own section rather than folded silently into the previous one.
CARD_RE = re.compile(
    r"^(UOB [A-Z' ]*CARD|LADY'S SOLITAIRE\s*CARD)"
    r"(?:\s*\(\s*CONTINUED\s*\))?\s*$")
# Page furniture that can never be the tail of a wrapped merchant name, so it is
# safe to drop even in the middle of an open row.
MIDROW_FURNITURE = (
    r"Page \d+ of \d+\s*$",
    r"Ref\s*No\b",
    r"\d{4}-\d{4}-\d{4}-\d{4}\b",
    r"Contact Us\s*$",
    r"Call \d",
    r"Email \S+@",
    r"SINGAPORE \d{6}\s*$",
    r"United Overseas\b",
    r"Please note\b",
    r"Postage\b",
    r"omissions\b",
    r"claim against\b",
)
# Furniture that is only furniture when no row is open. Each of these can also be
# the tail of a wrapped description, and the cardholder-name alternative is the
# demonstrated case: "MR BEAN INTERNATIONAL PTE" is a merchant, not the name line,
# and dropping it truncated the description while the section still reconciled.
HEADER_ONLY_FURNITURE = (
    r"PREVIOUS BALANCE\b",
    r"SUB ?TOTAL\b",
    r"NEW BALANCE\b",
    r"GRAND TOTAL\b",
    r"TOTAL\s+[\d,]+\.\d{2}",
    r"TOTAL BALANCE FOR\b",
    r"TOTAL AMOUNT DUE\b",
    r"TOTAL CREDIT LIMIT\b",
    r"Amount to Pay\b",
    r"Minimum Payment\b",
    r"Description of Transaction\b",
    r"Statement (?:Date|Summary)\b",
    r"Due Date\b",
    r"(?:Post|Trans|Date|Amount|Description|Minimum|Statement)\s*$",
    r"MR(?:\s+[A-Z]+){2,}\s*$",
)
# The full furniture catalogue. Every alternative is either a bare column header
# (anchored to end of line) or a statement-furniture phrase that cannot begin a
# merchant name. The old version used loose prefixes - "Trans", "Total ", "Date",
# "Amount", "SINGAPORE \d" - which swallowed real rows such as "TRANSIT LINK PTE
# 25.00" and "TOTAL WINE MORE 25.00".
SKIP_RE = re.compile(
    r"^(?:" + "|".join(MIDROW_FURNITURE + HEADER_ONLY_FURNITURE) + r")", re.I)
# What parse_pdf actually consults while a row is open. Anything outside it is
# appended to the description instead of being dropped without a trace.
MIDROW_SKIP_RE = re.compile(r"^(?:" + "|".join(MIDROW_FURNITURE) + r")", re.I)


def statement_month(reader, path):
    text = reader.pages[0].extract_text() or ""
    m = STATEMENT_DATE_RE.search(text)
    if m:
        return int(m.group(3)), MONTHS[m.group(2).upper()]
    base = os.path.basename(path).upper().replace(".PDF", "")
    year = month = None
    for part in base.split("_"):
        if re.fullmatch(r"\d{4}", part):
            year = int(part)
        elif part in MONTHS:
            month = MONTHS[part]
        elif re.fullmatch(r"\d{1,2}", part):
            if year is None:
                year = 2000 + int(part)
            elif month is None:
                month = int(part)
    if year and month and 1 <= month <= 12:
        return year, month
    return None


def clean(desc):
    desc = re.sub(r"\s*Ref\s*No\b.*$", "", desc, flags=re.I | re.S)
    return " ".join(desc.split())


def date_in_statement_cycle(year, month, day, row_month):
    # A row's month names the most recent occurrence of that month at or before
    # the statement month, except one month ahead (a Dec statement carrying an
    # early-Jan posting) which wraps forward. The old adjacent-Dec/Jan special
    # case dated a lagged December refund on a Feb statement ten months into the
    # future.
    delta = (month - row_month) % 12
    if delta == 11:
        y = year + (1 if month == 12 else 0)
    else:
        y = year - (1 if row_month > month else 0)
    return "%04d-%02d-%02d" % (y, row_month, day)


def parse_pdf(path):
    reader = PdfReader(path)
    ym = statement_month(reader, path)
    if not ym:
        return None, [], {}, []
    year, month = ym
    month_key = "%04d-%02d" % (year, month)

    lines = []
    for page_no, page in enumerate(reader.pages, 1):
        lines.extend(
            (page_no, line_no, raw)
            for line_no, raw in enumerate((page.extract_text() or "").split("\n"), 1)
        )

    txs = []
    card = "UOB ONE CARD"
    pending = None
    # Each card section states its opening and closing balance; the rows between
    # them must bridge the two or something was missed.
    checks = {}
    failures = []
    prev_re = re.compile(r"^PREVIOUS BALANCE\s+([\d,]+\.\d{2})\s*(CR)?$", re.I)
    sub_re = re.compile(r"^SUB ?TOTAL\s+([\d,]+\.\d{2})\s*(CR)?$", re.I)

    def fail(reason, row):
        failures.append({
            "file": os.path.basename(path),
            "page": row["page"],
            "line": row["line"],
            "reason": reason,
            "text": " ".join(row["raw"].split())[:160],
        })

    def flush(amount, credit):
        if not pending:
            return
        if amount == 0:
            # Rows the bank prints at S$0.00 (UNI$ point deductions) carry a fully
            # resolved amount; there is simply no money to record.
            return
        desc = clean(pending["desc"])
        if not desc:
            fail("row has no description once the reference tail is removed", pending)
            return
        # Seeing an emitted transaction is enough to establish that this card
        # section exists. If extraction loses both balance markers, main() can
        # now report the section as unchecked instead of overlooking it.
        checks.setdefault(card, {})
        txs.append({
            "date": date_in_statement_cycle(
                year, month, pending["day"], pending["mon"]
            ),
            "postedDate": date_in_statement_cycle(
                year, month, pending["postDay"], pending["postMon"]
            ),
            "month": month_key,
            "card": card,
            "description": desc,
            "amount": round(amount, 2),
            "credit": bool(credit),
            "foreign": pending.get("fx"),
            "_sourcePage": pending["page"],
            "_sourceLine": pending["line"],
        })

    for page_no, line_no, raw in lines:
        line = raw.strip()
        if not line:
            continue

        # A section header never interrupts a row: while one is open the same
        # text is a continuation line, not a new card.
        header = CARD_RE.match(line) if pending is None else None
        if header and "PREVIOUS" not in line.upper():
            card = re.sub(r"\s+", " ", header.group(1).upper()).strip()
            continue

        m = prev_re.match(line)
        if m:
            value = float(m.group(1).replace(",", "")) * (-1 if m.group(2) else 1)
            checks.setdefault(card, {})["previous"] = value
            if pending:
                fail("row never resolved an amount before PREVIOUS BALANCE", pending)
            pending = None
            continue
        m = sub_re.match(line)
        if m:
            value = float(m.group(1).replace(",", "")) * (-1 if m.group(2) else 1)
            checks.setdefault(card, {})["subtotal"] = value
            if pending:
                fail("row never resolved an amount before SUB TOTAL", pending)
            pending = None
            continue

        row = ROW_RE.match(line)
        if row:
            if pending:
                # Dropping this used to hide a whole charge, and a dropped debit
                # plus a dropped credit of equal size still reconcile.
                fail("row never resolved an amount before the next dated row", pending)
            rest = row.group(5)
            amt = AMOUNT_RE.search(rest)
            pending = {
                "day": int(row.group(3)),
                "mon": MONTHS[row.group(4).upper()],
                "postDay": int(row.group(1)),
                "postMon": MONTHS[row.group(2).upper()],
                "desc": AMOUNT_RE.sub("", rest).strip(),
                "page": page_no,
                "line": line_no,
                "raw": line,
            }
            if amt:
                flush(float(amt.group(1).replace(",", "")), amt.group(2))
                pending = None
            continue

        if pending is None:
            continue

        if FX_RE.match(line):
            pending["fx"] = line
            continue

        amt = AMOUNT_RE.search(line)
        if amt:
            # A line ending in a currency amount while a row is open is that row's
            # amount, whatever else it looks like. SKIP_RE never gets a vote here.
            leading = AMOUNT_RE.sub("", line).strip()
            if leading:
                pending["desc"] += " " + leading
            flush(float(amt.group(1).replace(",", "")), amt.group(2))
            pending = None
        elif not MIDROW_SKIP_RE.match(line):
            # Only the mid-row-safe furniture above is dropped here. Anything
            # else is part of this row's description: a continuation line that
            # merely resembles a heading used to vanish, and because the amount
            # was still read from a later line the section reconciled anyway -
            # the row simply lost the tail of its description in silence.
            pending["desc"] += " " + line

    if pending:
        fail("row never resolved an amount before the end of the statement", pending)

    for c, info in checks.items():
        if "previous" not in info or "subtotal" not in info:
            continue
        moved = 0.0
        for t in txs:
            if t["card"] != c:
                continue
            moved += -t["amount"] if t["credit"] else t["amount"]
        info["expected"] = round(info["previous"] + moved, 2)
        info["gap"] = round(info["subtotal"] - info["expected"], 2)

    assign_provenance(txs, "card-pdf", source_name(path))
    for tx in txs:
        info = checks.get(tx["card"], {})
        tx["provenance"]["verified"] = (
            "previous" in info and "subtotal" in info and abs(info.get("gap", 1)) < 0.01
        )
    return month_key, txs, checks, failures


CSV_DATE_RE = re.compile(r"^(\d{1,2})\s+(%s)$" % MON_RE, re.I)
# Rows the old exporter emits that are genuinely not transactions: the repeated
# column header, the foreign original on its own line, and the balance markers.
CSV_MARKER_RE = re.compile(
    r"^(?:PREVIOUS BALANCE|SUB ?TOTAL|GRAND TOTAL|NEW BALANCE|TOTAL|"
    r"Description of Transaction)\s*$"
    r"|^(?:TOTAL BALANCE FOR|TOTAL AMOUNT DUE|MINIMUM PAYMENT)\b", re.I)
CSV_FX_RE = re.compile(r"^(?!PTE\b|LTD\b|GST\b)[A-Z]{3}\s+[\d,]+\.\d{2}$")
# Grouping is strict so a mangled export like "1,2,3.45" fails instead of
# silently parsing as 123.45; CSV months have no subtotal reconciliation to
# catch a bad amount later.
CSV_AMOUNT_RE = re.compile(
    r"^(-)?\s*(\d{1,3}(?:,\d{3})*\.\d{2}|\d+\.\d{2})\s*(CR)?$", re.I)
# parse_csv trusts these column names; if the exporter or a spreadsheet re-save
# renames them, every row would look like furniture and vanish. Checked
# explicitly so header drift fails the run instead of emptying a month.
CSV_REQUIRED_COLUMNS = ("Trans", "Description of Transaction", "Transaction Amount")


def csv_month(path):
    base = os.path.basename(path).upper().replace(".CSV", "")
    year = month = None
    for part in base.split("_"):
        if re.fullmatch(r"\d{4}", part):
            year = int(part)
        elif part in MONTHS:
            month = MONTHS[part]
        elif re.fullmatch(r"\d{1,2}", part):
            if year is None:
                year = 2000 + int(part)
            elif month is None:
                month = int(part)
    return (year, month) if (year and month and 1 <= month <= 12) else None


def parse_csv(path):
    # Fallback for months whose PDF is missing; these were produced by the old
    # pdf_to_csv.py and cover the UOB ONE card only.
    ym = csv_month(path)
    if not ym:
        return None, [], []
    year, month = ym
    month_key = "%04d-%02d" % (year, month)
    txs = []
    failures = []

    def fail(row_no, reason, row):
        failures.append({
            "file": os.path.basename(path),
            "page": None,
            "line": row_no,
            "reason": reason,
            "text": " | ".join("%s=%s" % (k, v) for k, v in row.items() if v)[:160],
        })

    data_rows = 0
    with open(path, encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        missing = [c for c in CSV_REQUIRED_COLUMNS if c not in header]
        if missing:
            fail(1, "missing expected columns %s in header %r"
                 % (", ".join(missing), header[:6]), {})
            return month_key, [], failures
        for row_no, row in enumerate(reader, 2):
            data_rows += 1
            desc = (row.get("Description of Transaction") or "").strip()
            raw = (row.get("Transaction Amount") or "").strip()
            trans = (row.get("Trans") or "").strip()
            post = (row.get("Post") or "").strip()
            dated = CSV_DATE_RE.match(trans)
            if not dated:
                # Only the known furniture may be dateless; anything else is a row
                # this parser did not understand and must not quietly discard.
                if not desc or CSV_MARKER_RE.match(desc) or CSV_FX_RE.match(desc):
                    continue
                fail(row_no, "row has no usable transaction date %r" % trans, row)
                continue
            if not desc:
                # The PDF path fails closed on a dated row with no description;
                # the CSV path must not be laxer.
                fail(row_no, "dated row has empty description", row)
                continue
            m = CSV_AMOUNT_RE.match(raw)
            if not m:
                fail(row_no, "row has no usable amount %r" % raw, row)
                continue
            # The exporter writes refunds either as a "CR" suffix or a leading
            # minus; both mean money coming back.
            credit = bool(m.group(3)) or bool(m.group(1))
            amount = round(float(m.group(2).replace(",", "")), 2)
            if amount == 0:
                continue
            day, mon = int(dated.group(1)), MONTHS[dated.group(2).upper()]
            post_match = CSV_DATE_RE.match(post)
            txs.append({
                "date": date_in_statement_cycle(year, month, day, mon),
                "postedDate": date_in_statement_cycle(
                    year,
                    month,
                    int(post_match.group(1)),
                    MONTHS[post_match.group(2).upper()],
                ) if post_match else None,
                "month": month_key,
                "card": "UOB ONE CARD",
                "description": clean(desc),
                "amount": amount,
                "credit": credit,
                "foreign": None,
                "_sourceLine": row_no,
            })
    if data_rows and not txs and not failures:
        # Every row was classified as furniture. A real export always holds at
        # least one transaction, so this is a parser/format mismatch, not an
        # empty month - refuse to succeed silently.
        fail(1, "no transactions parsed from %d rows" % data_rows, {})
    assign_provenance(txs, "card-csv", source_name(path), verified=False)
    return month_key, txs, failures


def write_output(payload, ok):
    """Replace the published file, but only once the whole run succeeded.

    Writing first and exiting 1 afterwards - which is what this used to do - left
    unreconciled rows on the dashboard for anything that ignored the exit code.
    """
    if not ok:
        return False
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    tmp_path = OUT_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1)
    os.replace(tmp_path, OUT_PATH)
    return True


def main():
    patterns = [
        os.path.join(REPO_ROOT, "statements", "**", "UOB_CC*.pdf"),
        os.path.join(REPO_ROOT, "UOB_CC*.pdf"),
        os.path.join(REPO_ROOT, "Archive", "**", "UOB_CC*.pdf"),
        os.path.join(REPO_ROOT, "Archive", "UOB_CC*.pdf"),
    ]
    files = sorted({f for p in patterns for f in glob.glob(p, recursive=True)})
    # Statement PDFs that no parser will ever open deserve a loud notice:
    # UOB_LADY_2024_12.pdf sat unread for years because only UOB_CC*/UOB_ONE*
    # are globbed. Non-fatal, because the combined CC statement may already
    # cover the same rows.
    all_statement_pdfs = glob.glob(
        os.path.join(REPO_ROOT, "statements", "**", "UOB_*.pdf"), recursive=True)
    for path in sorted(all_statement_pdfs):
        base = os.path.basename(path).upper()
        if not (base.startswith("UOB_CC") or base.startswith("UOB_ONE")):
            print("WARNING: %s is not ingested by any parser "
                  "(only UOB_CC*/UOB_ONE* are read)" % os.path.basename(path))
    all_txs, months, failed, balances = [], {}, [], {}
    row_failures = []
    for path in files:
        try:
            month_key, txs, checks, failures = parse_pdf(path)
        except Exception as exc:
            failed.append((os.path.basename(path), str(exc)[:60]))
            continue
        row_failures.extend(failures)
        if not month_key or not txs:
            failed.append((os.path.basename(path), "no transactions parsed"))
            continue
        if month_key in months:
            # Two files claiming one month would double every row.
            failed.append((os.path.basename(path),
                           "month %s already read from %s" % (month_key, months[month_key])))
            continue
        months[month_key] = os.path.basename(path)
        all_txs.extend(txs)
        for card, info in checks.items():
            balances[(month_key, card)] = info

    csv_patterns = [
        os.path.join(REPO_ROOT, "statements", "**", "UOB_CC*.csv"),
        os.path.join(REPO_ROOT, "UOB_CC*.csv"),
        os.path.join(REPO_ROOT, "Archive", "**", "UOB_CC*.csv"),
        os.path.join(REPO_ROOT, "Archive", "UOB_CC*.csv"),
    ]
    from_csv = []
    pdf_months = set(months)
    for path in sorted({f for p in csv_patterns for f in glob.glob(p, recursive=True)}):
        ym = csv_month(path)
        if not ym:
            continue
        month_key = "%04d-%02d" % ym
        if month_key in pdf_months:
            continue          # a real statement beats the old CSV export
        if month_key in months:
            # Two CSVs claiming one month would double every row; unlike the
            # PDF-beats-CSV rule this is a conflict, not a fallback.
            failed.append((os.path.basename(path),
                           "month %s already read from %s" % (month_key, months[month_key])))
            continue
        try:
            month_key, txs, failures = parse_csv(path)
        except Exception as exc:
            failed.append((os.path.basename(path), str(exc)[:60]))
            continue
        row_failures.extend(failures)
        if txs:
            months[month_key] = os.path.basename(path)
            from_csv.append(month_key)
            all_txs.extend(txs)
        elif not failures:
            failed.append((os.path.basename(path), "no transactions parsed"))

    # Identical charges on one day are real (three S$6.00 ActiveSG bookings, four
    # kuro games top-ups), so rows are never collapsed. Double-ingest is prevented
    # by the one-file-per-month guard above instead.
    rows = sorted(all_txs, key=lambda t: (t["month"], t["date"], t["description"], t["amount"]))

    # Reconcile the rows that would be written, before anything is written. A
    # gapped section used to land in neither reconciledSections nor
    # uncheckedSections, so nothing downstream could see it.
    gaps, unchecked = [], []
    for (month_key, card), info in sorted(balances.items()):
        if "previous" not in info or "subtotal" not in info:
            unchecked.append((month_key, card))
            continue
        moved = 0.0
        for t in rows:
            if t["month"] == month_key and t["card"] == card:
                moved += -t["amount"] if t["credit"] else t["amount"]
        expected = round(info["previous"] + moved, 2)
        gap = round(info["subtotal"] - expected, 2)
        if abs(gap) >= 0.01:
            gaps.append((month_key, card, gap, info["subtotal"], expected))

    payload = {
        "months": sorted(months),
        "sourceFiles": months,
        "quality": {
            "statementFiles": len(months),
            "pdfMonths": sorted(set(months) - set(from_csv)),
            "csvMonths": sorted(from_csv),
            "reconciledSections": sum(
                1 for info in balances.values()
                if "previous" in info and "subtotal" in info and abs(info.get("gap", 1)) < 0.01
            ),
            "uncheckedSections": len(unchecked),
            "unreconciledSections": [
                {"month": month_key, "card": card, "gap": gap,
                 "statementSubtotal": subtotal, "rowsGive": expected}
                for month_key, card, gap, subtotal, expected in gaps
            ],
            "unparsedRows": len(row_failures),
        },
        "transactions": rows,
    }
    written = write_output(
        payload, not gaps and not failed and not row_failures and not unchecked)

    debits = sum(t["amount"] for t in rows if not t["credit"])
    credits = sum(t["amount"] for t in rows if t["credit"])
    print("Parsed %d statements, %d card transactions -> %s"
          % (len(months), len(rows),
             os.path.relpath(OUT_PATH, REPO_ROOT) if written else "(not written)"))
    print("Debits S$%s, credits S$%s" % (format(debits, ",.2f"), format(credits, ",.2f")))
    cards = {}
    for t in rows:
        cards[t["card"]] = cards.get(t["card"], 0) + 1
    print("By card:", cards)
    if from_csv:
        print("No PDF for %s - fell back to the old CSV export (UOB ONE card only)."
              % ", ".join(sorted(from_csv)))
    checked = len(balances) - len(unchecked)
    if gaps:
        print("\nFAIL: %d of %d card section(s) do not reconcile to their SUB TOTAL:"
              % (len(gaps), len(balances)))
        for month_key, card, gap, subtotal, expected in gaps[:20]:
            print("   %s %-22s gap %8.2f (statement %.2f, rows give %.2f)"
                  % (month_key, card, gap, subtotal, expected))
    else:
        print("All %d checkable card section(s) reconcile to their statement SUB TOTAL."
              % checked)
    if unchecked:
        print("\nFAIL: %d section(s) could NOT be checked - no PREVIOUS BALANCE or "
              "SUB TOTAL found:" % len(unchecked))
        for month_key, card in unchecked[:10]:
            print("   %s %s" % (month_key, card))
    if from_csv:
        print("CSV-sourced months carry no balance markers, so they are unverified.")
    if row_failures:
        print("\nFAIL: %d statement row(s) could not be parsed:" % len(row_failures))
        for f in row_failures[:20]:
            where = "p%s l%s" % (f["page"], f["line"]) if f["page"] else "row %s" % f["line"]
            print("   %s %s - %s\n      %s" % (f["file"], where, f["reason"], f["text"]))
    if failed:
        print("Could not parse %d file(s):" % len(failed))
        for name, why in failed[:12]:
            print("  ", name, "-", why)

    if not written:
        print("\n%s was left untouched." % os.path.relpath(OUT_PATH, REPO_ROOT))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
