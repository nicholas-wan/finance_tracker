# Extracts account transactions from the UOB ONE statement PDFs.
# Withdrawal vs deposit is decided by the direction the running balance moves,
# because the text layer does not preserve which column an amount sat in.
# Usage: python tracker/parse_one.py

import glob
import json
import os
import re

from pypdf import PdfReader
from data_ids import assign_provenance, source_name

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(REPO_ROOT, "app", "data", "account_transactions.json")

MONTHS = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
          "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}

DATE_RE = re.compile(r"^(\d{1,2})\s+(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\b", re.I)
AMOUNT_RE = re.compile(r"(?<![\w.,])(\d{1,3}(?:,\d{3})*\.\d{2}|\d+\.\d{2})(?![\d])")
PERIOD_RE = re.compile(r"Period:\s*\d{1,2}\s+(\w{3})\s+(\d{4})", re.I)

FLOW_RULES = [
    # "PHILLIP SECURITIES" is the full brokerage name; the bare surname is left out
    # on purpose because PayNow transfers to a person called Phillip would collide.
    ("Investment", ["INTERACTIVE BROKERS", "IBKR", "TIGER BROKERS", "MOOMOO", "SAXO", "ENDOWUS", "SYFE",
                    "PHILLIP SECURITIES", "PHILLIP SEC"]),
    ("Retirement (SRS)", ["SRS"]),
    ("Fixed deposit", ["FCFD", "FIXED DEPOSIT", "PRINCIPAL CREDIT", "TO 0000000000"]),
    ("Credit card bill", ["UOB CARD", "CARD PAYMENT", "PAYMENT TO CARD", "IB CARD PAYMENT", "CREDIT CARD",
                          "HSBC CC", "MBK-HSBC"]),
    ("Salary", ["SALARY", "PAYROLL", "GIRO SALARY"]),
    # IRAS prints as "INLAND REVENUE AUTHO..." over PayNow, which never says IRAS.
    ("Tax", ["IRAS", "INCOME TAX", "TAXS", "INLAND REVENUE"]),
    ("Interest", ["BONUS INTEREST", "INTEREST EARNED", "ONE BONUS INTEREST"]),
    ("Insurance", ["PRUDENTIAL", "TOKIO MARINE", "FWD", "GREAT EASTERN", "AIA", "AVIVA", "INCOME"]),
    ("Mortgage & home", ["HDB", "MORTGAGE", "HOME LOAN", "TOWN COUNCIL", "SP SERVICES", "SP DIGITAL"]),
    ("CPF", ["CPF"]),
    ("Transfer", ["PAYNOW", "FAST", "GIRO", "TRANSFER", "IBG"]),
    ("Cash & NETS", ["NETS", "ATM", "CASH WITHDRAWAL"]),
]


def classify(description):
    d = " ".join(description.split()).upper()
    for name, patterns in FLOW_RULES:
        for p in patterns:
            if p in d:
                return name
    return "Other"


def printed_sign(text, match):
    """Return -1/+1 when the statement itself printed a direction for this amount.

    A minus glued to the digits means money out; a trailing CR/DR is the bank's
    own credit/debit marker. The UOB ONE layout normally prints neither, which is
    why the balance chain decides direction - but when a sign IS printed it must
    agree with the balance movement or the row was not understood.
    """
    if match.start() and text[match.start() - 1] == "-":
        return -1
    tail = re.match(r"\s*(CR|DR)\b", text[match.end():], re.I)
    if tail:
        return 1 if tail.group(1).upper() == "CR" else -1
    return None


def statement_month(reader, path):
    text = reader.pages[0].extract_text() or ""
    m = PERIOD_RE.search(text)
    if m:
        mon = MONTHS.get(m.group(1).upper())
        if mon:
            return int(m.group(2)), mon
    # Fall back to the filename: UOB_ONE_2026_5.pdf or UOB_ONE_JUN_26.pdf
    base = os.path.basename(path).upper().replace(".PDF", "")
    parts = base.split("_")
    year = month = None
    for p in parts:
        if re.fullmatch(r"\d{4}", p):
            year = int(p)
        elif re.fullmatch(r"\d{1,2}", p):
            if year and month is None:
                month = int(p)
            elif not year:
                year = 2000 + int(p)
        elif p in MONTHS:
            month = MONTHS[p]
    if year and month and 1 <= month <= 12:
        return year, month
    return None


def parse_pdf(path):
    reader = PdfReader(path)
    ym = statement_month(reader, path)
    if not ym:
        return None, [], [], []
    year, month = ym
    month_key = "%04d-%02d" % (year, month)

    lines = []
    for page_no, page in enumerate(reader.pages, 1):
        lines.extend(
            (page_no, line_no, raw)
            for line_no, raw in enumerate((page.extract_text() or "").split("\n"), 1)
        )

    # Everything after this marker is legal boilerplate whose stray numbers would
    # otherwise be absorbed into the final transaction.
    for i, (_, _, raw) in enumerate(lines):
        if "End of Transaction Details" in raw:
            lines = lines[:i]
            break

    SKIP_RE = re.compile(
        r"^(Page \d|NICHOLAS|Date Description|One Account|Account Transaction|Total\b|-{5,})", re.I)

    entries = []
    current = None
    for page_no, line_no, raw in lines:
        line = raw.strip()
        if not line:
            continue
        if DATE_RE.match(line):
            if current:
                entries.append(current)
            current = {"lines": [line], "page": page_no, "line": line_no}
        elif current is not None:
            if SKIP_RE.match(line):
                continue
            current["lines"].append(line)
    if current:
        entries.append(current)

    txs = []
    overrides = []
    failures = []
    balance = None
    opened = False
    broken = False

    def fail(entry, reason, text):
        failures.append({
            "file": os.path.basename(path),
            "page": entry["page"],
            "line": entry["line"],
            "reason": reason,
            "text": " ".join(text.split())[:160],
        })

    for e in entries:
        text = " ".join(e["lines"])
        m = DATE_RE.match(e["lines"][0])
        day, mon = int(m.group(1)), MONTHS[m.group(2).upper()]
        # Statements can carry a few days of the neighbouring month.
        y, mo = year, month
        if mon != month:
            if mon == 12 and month == 1:
                y -= 1
            elif mon == 1 and month == 12:
                y += 1
            mo = mon
        date = "%04d-%02d-%02d" % (y, mo, day)

        matches = list(AMOUNT_RE.finditer(text))
        nums = [float(m.group(1).replace(",", "")) for m in matches]
        desc = re.sub(DATE_RE, "", e["lines"][0]).strip() + " " + " ".join(e["lines"][1:])
        desc = AMOUNT_RE.sub("", desc)
        desc = " ".join(desc.split())

        if "BALANCE B/F" in text.upper():
            # The opening marker is the one legitimate single-number row. A second
            # one, or one without a figure, means the layout moved under us.
            if nums and not opened:
                balance = nums[-1]
                opened = True
                continue
            fail(e, "BALANCE B/F marker is repeated or carries no figure", text)
            continue
        if balance is None:
            fail(e, "no trusted running balance: an earlier row on this statement "
                    "was unreadable" if broken else
                    "transaction appears before any BALANCE B/F opening figure", text)
            continue
        if len(nums) < 2:
            # Losing a row here used to be silent, and the next row's balance delta
            # would then span two rows - inventing an amount and flipping direction.
            fail(e, "transaction line yields %d number(s), need an amount and a balance"
                 % len(nums), text)
            # The running balance never advanced past this row, so no later delta
            # on this statement means anything either.
            balance = None
            broken = True
            continue

        new_balance = nums[-1]
        amount = nums[-2]
        delta = round(new_balance - balance, 2)
        delta_sign = 1 if delta > 0 else -1 if delta < 0 else 0
        sign = printed_sign(text, matches[-2])
        if sign is not None and delta_sign and sign != delta_sign:
            balance = new_balance
            fail(e, "printed amount sign says %s but the balance moved %s"
                 % ("deposit" if sign > 0 else "withdrawal",
                    "up" if delta_sign > 0 else "down"), text)
            continue
        overridden = abs(abs(delta) - amount) > 0.02
        if overridden:
            # The printed amount disagrees with the balance movement, usually a
            # wrapped line. The balance chain is the safer source, but record the
            # override so it is never silent.
            overrides.append({"date": date, "description": desc[:60],
                              "printed": round(amount, 2), "used": abs(delta)})
            amount = abs(delta)
        direction = "deposit" if delta > 0 else "withdrawal"
        balance = new_balance

        if amount == 0:
            continue
        txs.append({
            "date": date,
            "month": month_key,
            "description": desc[:120],
            "amount": round(amount, 2),
            "direction": direction,
            "flow": classify(desc),
            "balance": round(new_balance, 2),
            "amountSource": "balance" if overridden else "printed",
            "_sourcePage": e["page"],
            "_sourceLine": e["line"],
        })
    assign_provenance(txs, "account-pdf", source_name(path), verified=True)
    return month_key, txs, overrides, failures


def write_output(payload, ok):
    """Replace the published file, but only once every row parsed.

    A statement with an unreadable row cannot be trusted row-by-row either, so
    nothing is published until the whole run is clean.
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
        os.path.join(REPO_ROOT, "statements", "**", "UOB_ONE*.pdf"),
        os.path.join(REPO_ROOT, "UOB_ONE*.pdf"),
        os.path.join(REPO_ROOT, "Archive", "**", "UOB_ONE*.pdf"),
    ]
    files = sorted({f for p in patterns for f in glob.glob(p, recursive=True)})
    all_txs = []
    months = {}
    failed = []
    all_overrides = []
    row_failures = []
    for path in files:
        try:
            month_key, txs, overrides, failures = parse_pdf(path)
        except Exception as exc:
            failed.append((os.path.basename(path), str(exc)[:60]))
            continue
        row_failures.extend(failures)
        if not month_key or not txs:
            failed.append((os.path.basename(path), "no transactions parsed"))
            continue
        if month_key in months:
            failed.append((os.path.basename(path),
                           "month %s already read from %s" % (month_key, months[month_key])))
            continue
        months[month_key] = os.path.basename(path)
        all_txs.extend(txs)
        for o in overrides:
            all_overrides.append((month_key, o))

    all_txs.sort(key=lambda t: (t["date"], t["description"], t["id"]))

    expected = set()
    if months:
        y0, m0 = [int(x) for x in min(months).split("-")]
        y1, m1 = [int(x) for x in max(months).split("-")]
        while (y0, m0) <= (y1, m1):
            expected.add("%04d-%02d" % (y0, m0))
            m0 += 1
            if m0 > 12:
                m0, y0 = 1, y0 + 1
    gaps = sorted(expected - set(months))

    payload = {
        "months": sorted(months.keys()),
        "sourceFiles": months,
        "quality": {
            "statementFiles": len(months),
            "missingMonths": gaps,
            "amountOverrides": len(all_overrides),
            "unparsedRows": len(row_failures),
        },
        "transactions": all_txs,
    }
    written = write_output(
        payload, not row_failures and not failed and not all_overrides)

    wealth = ["Investment", "Retirement (SRS)", "Fixed deposit"]
    invested = sum(t["amount"] for t in all_txs
                   if t["flow"] in wealth and t["direction"] == "withdrawal")
    print("Parsed %d statements, %d account transactions -> %s"
          % (len(months), len(all_txs), OUT_PATH if written else "(not written)"))
    print("Moved to investments and savings: S$%s" % format(invested, ",.2f"))
    if gaps:
        print("No statement found for: %s" % ", ".join(gaps))
    if all_overrides:
        print("\nFAIL: %d row(s) where the printed amount disagrees with the balance "
              "movement; nothing was written:" % len(all_overrides))
        for month_key, o in all_overrides[:15]:
            print("   %s %s printed %.2f, balance movement %.2f  | %s"
                  % (month_key, o["date"], o["printed"], o["used"], o["description"]))
    else:
        print("Every row's printed amount matches the balance movement.")
    if row_failures:
        print("\nFAIL: %d statement row(s) could not be parsed; nothing was written:"
              % len(row_failures))
        for f in row_failures[:20]:
            print("   %s p%s l%s - %s\n      %s"
                  % (f["file"], f["page"], f["line"], f["reason"], f["text"]))
    if failed:
        print("Could not parse %d file(s):" % len(failed))
        for name, why in failed[:12]:
            print("  ", name, "-", why)
    if failed or row_failures or all_overrides:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
