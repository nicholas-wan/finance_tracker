# Extracts account transactions from the UOB ONE statement PDFs.
# Withdrawal vs deposit is decided by the direction the running balance moves,
# because the text layer does not preserve which column an amount sat in.
# The holder name and own-account numbers are private, so they are read from
# the git-ignored manual/identity.json rather than written into this file.
# Usage: python tracker/parse_one.py

import glob
import json
import os
import re
import tempfile
import time

from pypdf import PdfReader
from data_ids import assign_provenance, source_name

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get(
    "FINANCE_DATA_DIR", os.path.join(REPO_ROOT, "app", "data")
)
OUT_PATH = os.path.join(DATA_DIR, "account_transactions.json")

MONTHS = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
          "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}

DATE_RE = re.compile(r"^(\d{1,2})\s+(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\b", re.I)
AMOUNT_RE = re.compile(r"(?<![\w.,])(\d{1,3}(?:,\d{3})*\.\d{2}|\d+\.\d{2})(?![\d])")
PERIOD_RE = re.compile(r"Period:\s*\d{1,2}\s+(\w{3})\s+(\d{4})", re.I)

# classify() matches these against the description padded with one leading and
# one trailing space, so a pattern written with an edge space is anchored to a
# word edge. Short tokens must use that: bare "FAST" classified THE BREAKFAST
# CLUB as a transfer, bare "FWD"/"AIA"/"INCOME" turned unrelated PayNow rows
# into insurance. A space at both ends is for tokens that are also the prefix of
# an unrelated word, which a leading space alone would not exclude: " SAXO "
# (SAXOPHONE), " MOOMOO " (THE MOOMOO DAIRY), " NETS " (NETSUITE), " HDB "
# (HDBANK), " IBKR " (IBKRAFT), " SYFE " (SYFELINE). Longer patterns keep plain
# substring semantics ("UOB CARD" must still match "UOB CARDS", and the
# statement prints it as "iBK-UOB Cards").
FLOW_RULES = [
    # "PHILLIP SECURITIES" is the full brokerage name; the bare surname is left out
    # on purpose because PayNow transfers to a person called Phillip would collide.
    # A misfiled Investment row is the worst case here: it is dropped from spending
    # totals as if it were still your money, so every short token is anchored.
    ("Investment", ["INTERACTIVE BROKERS", "INTERACTIVE BR", " IBKR ", "TIGER BROKERS", " MOOMOO ", " SAXO ",
                    " ENDOWUS ", " SYFE ", "PHILLIP SECURITIES", "PHILLIP SEC"]),
    ("Retirement (SRS)", [" SRS", "-SRS"]),
    # The own fixed-deposit account numbers are private and are folded in at
    # runtime by flow_rules(); see manual/identity.json.
    ("Fixed deposit", ["FCFD", "FIXED DEPOSIT", "PRINCIPAL CREDIT"]),
    ("Credit card bill", ["UOB CARD", "CARD PAYMENT", "PAYMENT TO CARD", "IB CARD PAYMENT", "CREDIT CARD",
                          "HSBC CC", "MBK-HSBC"]),
    # UOB account statements label Yx's main salary as "Inward CR - GIRO
    # PAYNOW SALA ..."; keep this specific marker ahead of the generic GIRO
    # transfer rule so ordinary GIRO credits are not reclassified.
    ("Salary", ["SALARY", "PAYROLL", "GIRO SALARY", "INWARD CR - GIRO PAYNOW SALA"]),
    # IRAS prints as "INLAND REVENUE AUTHO..." over PayNow, which never says IRAS.
    ("Tax", ["IRAS", "INCOME TAX", "TAXS", "INLAND REVENUE"]),
    ("Interest", ["BONUS INTEREST", "INTEREST EARNED", "ONE BONUS INTEREST", "INTEREST CREDIT"]),
    ("Insurance", ["PRUDENTIAL", "TOKIO MARINE", " FWD ", "GREAT EASTERN", " AIA ", "AVIVA", " INCOME "]),
    # " HDB " keeps HDBANK, a Vietnamese bank, out of the mortgage bucket.
    ("Mortgage & home", [" HDB ", "MORTGAGE", "HOME LOAN", "TOWN COUNCIL", "SP SERVICES",
                         "SP DIGITAL"]),
    ("CPF", [" CPF"]),
    ("Transfer", ["PAYNOW", " FAST ", "-FAST", "GIRO", "TRANSFER", " IBG", "-IBG"]),
    # " NETS " keeps NETSUITE out; the statement always prints NETS as its own word.
    ("Cash & NETS", [" NETS ", "-NETS ", " ATM ", "-ATM ", "CASH WITHDRAWAL"]),
]


def identity_path():
    """Where the private identity file lives, resolved against REPO_ROOT."""
    return os.path.join(REPO_ROOT, "manual", "identity.json")


def load_identity(path=None):
    """Read manual/identity.json, the git-ignored home of the private literals.

    The statement holder's legal name and the own-account numbers must not sit
    in tracked source, so the parser is handed them at runtime. An absent file
    returns {}; main() decides that this is fatal, because a missing name
    filter changes every description instead of failing loudly.
    """
    path = path or identity_path()
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


def flow_rules(identity=None):
    """FLOW_RULES with the identity's fixed-deposit account numbers folded in.

    A transfer into an own fixed deposit prints as "TO <account>" and carries
    no other marker, so the number itself is the rule.
    """
    accounts = (identity or {}).get("fixedDepositAccounts") or []
    extra = ["TO %s" % str(account).strip().upper()
             for account in accounts if str(account).strip()]
    if not extra:
        return FLOW_RULES
    return [(name, list(patterns) + extra if name == "Fixed deposit" else patterns)
            for name, patterns in FLOW_RULES]


def classify(description, identity=None):
    d = " " + " ".join(description.split()).upper() + " "
    for name, patterns in flow_rules(identity):
        for p in patterns:
            if p in d:
                return name
    return "Other"


def header_skip_re(identity=None):
    """Lines that are page furniture rather than part of a transaction.

    The name filter must match the whole page-header name, not the bare first
    name: self-transfer recipient lines ("<first name> DBS", "<first name>
    CIMB") start the same way, and a first-name-only prefix silently erased
    the recipient from every transfer to an own account. So the pattern uses
    statementHolderName from manual/identity.json verbatim, escaped; without
    an identity it simply omits that alternative rather than guessing.
    """
    name = " ".join(str((identity or {}).get("statementHolderName") or "").split())
    alternatives = [r"Page \d+(?:\s+of\s+\d+)?\s*$"]
    if name:
        alternatives.append(re.escape(name) + r"\s*$")
    alternatives.extend([
        r"Date Description(?:\s+.*)?$", r"One Account\s*$",
        r"Account Transaction(?:s)?\s*$",
        # A printed total contains only money columns. Do not use a loose
        # ``Total\b`` prefix: a wrapped payee such as TOTAL WINE is row data.
        r"Total(?:\s+[\d,]+\.\d{2})+\s*$",
        r"Please note that you are bound\b", r"omissions or unauthorised debits\b",
        r"-{5,}\s*$",
    ])
    return re.compile(r"^(" + "|".join(alternatives) + r")", re.I)


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
    # Never infer accounting state from a mutable filename. If the printed
    # period cannot be extracted, the statement needs a parser update.
    return None


def parse_pdf(path, identity=None):
    reader = PdfReader(path)
    ym = statement_month(reader, path)
    if not ym:
        return None, [], [], [], None
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

    SKIP_RE = header_skip_re(identity)

    entries = []
    current = None
    current_page = None
    for page_no, line_no, raw in lines:
        # A transaction row never continues onto the next statement page. Text
        # extraction can place the next page's legal footer before its table,
        # so close the prior row before considering any text from a new page.
        if current_page is not None and page_no != current_page:
            if current:
                entries.append(current)
                current = None
        current_page = page_no
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
    opening = None
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
        # UOB repeats this legal footer near the end of many transaction pages.
        # Older text layers occasionally append it to the final transaction on
        # the page, so also trim it defensively after the entry is assembled.
        desc = re.sub(
            r"\s*(?:Please note that you are bound|omissions or unauthorised debits)\b.*$",
            "", desc,
            flags=re.I,
        ).strip()

        if "BALANCE B/F" in text.upper():
            # The opening marker is the one legitimate single-number row. A second
            # one, or one without a figure, means the layout moved under us.
            if nums and not opened:
                balance = nums[-1]
                # Keep the printed figure. Without it the first movement on a
                # statement is unfalsifiable: the chain seeds from that row's own
                # balance, so its amount can be anything and still reconcile.
                opening = round(balance, 2)
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
            "flow": classify(desc, identity),
            "balance": round(new_balance, 2),
            "amountSource": "balance" if overridden else "printed",
            "_sourcePage": e["page"],
            "_sourceLine": e["line"],
        })
    assign_provenance(txs, "account-pdf", source_name(path), verified=True)
    anchor = None
    if opening is not None:
        anchor = {
            "file": source_name(path),
            "openingBalance": opening,
            "closingBalance": round(txs[-1]["balance"], 2) if txs else opening,
            "rows": len(txs),
        }
    return month_key, txs, overrides, failures, anchor


def write_output(payload, ok):
    """Replace the published file, but only once every row parsed.

    A statement with an unreadable row cannot be trusted row-by-row either, so
    nothing is published until the whole run is clean.
    """
    if not ok:
        return False
    out_dir = os.path.dirname(OUT_PATH)
    os.makedirs(out_dir, exist_ok=True)
    # Write through a per-process temp file rather than a fixed OUT_PATH +
    # ".tmp": two concurrent parser runs would otherwise interleave writes into
    # one shared name. os.replace is retried because on Windows it fails while
    # a reader still holds the destination open; build_data.py has the same
    # guard.
    descriptor, tmp_path = tempfile.mkstemp(
        prefix=os.path.basename(OUT_PATH) + ".", suffix=".tmp", dir=out_dir)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=1)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(5):
            try:
                os.replace(tmp_path, OUT_PATH)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
    return True


def main():
    # Fail closed rather than parse without the name filter. A missing holder
    # name does not break the run, it quietly leaves the page-header name in
    # every description that follows one - a silent change to published data.
    identity = load_identity()
    if not str(identity.get("statementHolderName") or "").strip():
        print("FAIL: %s is missing or has no statementHolderName; nothing was written."
              % os.path.relpath(identity_path(), REPO_ROOT))
        print('      Create it with {"statementHolderName": "<name exactly as the '
              'statement page header prints it>"}. Without it the header name is '
              "not filtered and every description silently changes.")
        raise SystemExit(1)

    patterns = [
        os.path.join(REPO_ROOT, "statements", "**", "UOB_ONE*.pdf"),
        os.path.join(REPO_ROOT, "UOB_ONE*.pdf"),
        os.path.join(REPO_ROOT, "Archive", "**", "UOB_ONE*.pdf"),
    ]
    files = sorted({f for p in patterns for f in glob.glob(p, recursive=True)})
    all_txs = []
    months = {}
    anchors = {}
    failed = []
    all_overrides = []
    row_failures = []
    for path in files:
        try:
            month_key, txs, overrides, failures, anchor = parse_pdf(path, identity)
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
        if anchor:
            anchors[month_key] = anchor
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
        # The statement's own printed BALANCE B/F, kept per month so the
        # validator can check the first row against something the bank wrote
        # rather than against the row itself.
        "statementAnchors": {key: anchors[key] for key in sorted(anchors)},
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
