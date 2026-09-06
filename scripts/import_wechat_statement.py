"""Copy a WeChat Pay statement export (微信支付账单) into manual/wechat_payments.json.

WeChat Pay exports a workbook whose first sheet holds a preamble, then a
header row starting 交易时间, then one payment per row: time, type,
counterparty, product, direction (收/支), amount in yuan, payment method
(the card WeChat charged, e.g. "MASTERCARD(7975)"), status, transaction
number, merchant order number and a remark. Only the columns the dashboard
reads are copied; the account nickname in the preamble is not.

Each payment method is a card. Map the ones you know with --instrument, for
example --instrument "MASTERCARD(7975)=YouTrip". The build treats a payment
whose instrument is a tracked card as evidence for the matching statement
row, and a payment on any other instrument as spending the statements never
show, published beside them as paid via that instrument. Re-running with
the same or a later export refreshes the copy: payments are keyed by their
WeChat transaction number, so an overlapping export adds nothing twice.
"""

import argparse
import json
import os
import tempfile
from datetime import date
from pathlib import Path

from openpyxl import load_workbook


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "manual" / "wechat_payments.json"

HEADER_FIRST_CELL = "交易时间"
COLUMNS = (
    ("time", "交易时间"),
    ("type", "交易类型"),
    ("counterparty", "交易对方"),
    ("product", "商品"),
    ("direction", "收/支"),
    ("amountCny", "金额(元)"),
    ("method", "支付方式"),
    ("status", "当前状态"),
    ("transactionNo", "交易单号"),
    ("merchantNo", "商户单号"),
    ("remark", "备注"),
)
DIRECTIONS = {"支出": "expense", "收入": "income", "/": "neutral", "": "neutral"}
JSON_INDENT = 1


def clean(value):
    if value is None:
        return ""
    return " ".join(str(value).replace("\t", " ").split())


def parse_workbook(path):
    """The payments in the export, oldest first, as plain records."""
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        rows = list(workbook.worksheets[0].iter_rows(values_only=True))
    finally:
        # A read-only workbook holds the file open until closed, which on
        # Windows keeps the export from being moved or deleted afterwards.
        workbook.close()
    header_index = None
    payments = []
    for row in rows:
        cells = [clean(value) for value in row]
        if header_index is None:
            if cells and cells[0] == HEADER_FIRST_CELL:
                header_index = {}
                for position, cell in enumerate(cells):
                    for key, title in COLUMNS:
                        if cell == title:
                            header_index[key] = position
                missing = [title for key, title in COLUMNS if key not in header_index]
                if missing:
                    raise SystemExit("%s is missing the columns %s" % (path, ", ".join(missing)))
            continue
        if not any(cells):
            continue
        record = {key: cells[header_index[key]] if header_index[key] < len(cells) else ""
                  for key, _ in COLUMNS}
        if not record["transactionNo"] or not record["time"]:
            continue
        record["date"] = record["time"][:10]
        try:
            date.fromisoformat(record["date"])
        except ValueError:
            raise SystemExit("payment %s has an unreadable time %r" % (record["transactionNo"], record["time"]))
        try:
            record["amountCny"] = round(float(str(record["amountCny"]).replace(",", "")), 2)
        except ValueError:
            raise SystemExit("payment %s has an unreadable amount %r" % (record["transactionNo"], record["amountCny"]))
        if record["direction"] not in DIRECTIONS:
            raise SystemExit("payment %s has an unexpected direction %r" % (record["transactionNo"], record["direction"]))
        record["direction"] = DIRECTIONS[record["direction"]]
        record["remark"] = "" if record["remark"] == "/" else record["remark"]
        record["id"] = "wx_" + record["transactionNo"]
        payments.append(record)
    if header_index is None:
        raise SystemExit("%s has no %s header row" % (path, HEADER_FIRST_CELL))
    payments.sort(key=lambda record: (record["time"], record["id"]))
    return payments


def parse_instruments(values):
    instruments = {}
    for value in values or []:
        if "=" not in value:
            raise SystemExit("--instrument expects METHOD=NAME, got %r" % value)
        method, name = value.split("=", 1)
        if not method.strip() or not name.strip():
            raise SystemExit("--instrument expects METHOD=NAME, got %r" % value)
        instruments[method.strip()] = name.strip()
    return instruments


def merge(existing, payments):
    """Existing payments keep their place; a repeated transaction number is
    replaced by the newer export's row; new ones follow in time order."""
    by_id = {}
    order = []
    for record in existing or []:
        if isinstance(record, dict) and record.get("id"):
            by_id[record["id"]] = record
            order.append(record["id"])
    for record in payments:
        if record["id"] not in by_id:
            order.append(record["id"])
        by_id[record["id"]] = record
    merged = [by_id[key] for key in order]
    merged.sort(key=lambda record: (record.get("time", ""), record["id"]))
    return merged


def write_atomic(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, tmp_path = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("workbook", nargs="+", help="WeChat Pay statement .xlsx export(s)")
    parser.add_argument("--instrument", action="append", default=[],
                        help='map a payment method to a card, e.g. "MASTERCARD(7975)=YouTrip"')
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args(argv)

    output_path = Path(args.output)
    existing = {}
    if output_path.exists():
        with open(output_path, encoding="utf-8") as handle:
            existing = json.load(handle)
    payments = list(existing.get("payments", [])) if isinstance(existing, dict) else []
    added = 0
    for workbook in args.workbook:
        fresh = parse_workbook(workbook)
        before = len(payments)
        payments = merge(payments, fresh)
        added += len(payments) - before
    instruments = dict((existing.get("instruments") or {}) if isinstance(existing, dict) else {})
    instruments.update(parse_instruments(args.instrument))
    manifest = {
        "source": {
            "kind": "WeChat Pay statement export",
            "files": sorted(set(
                [os.path.basename(path) for path in args.workbook]
                + list(((existing.get("source") or {}).get("files") or []) if isinstance(existing, dict) else [])
            )),
            "importedAt": date.today().isoformat(),
        },
        "instruments": instruments,
        "payments": payments,
    }
    write_atomic(output_path, json.dumps(manifest, indent=JSON_INDENT, ensure_ascii=False) + "\n")
    methods = sorted(set(record.get("method", "") for record in payments))
    unmapped = [method for method in methods if method and method not in instruments]
    print("WeChat payments %d (%d new) -> %s" % (len(payments), added, output_path))
    if unmapped:
        print("Unmapped payment methods, treated as cards the statements do not show: %s"
              % ", ".join(unmapped))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
