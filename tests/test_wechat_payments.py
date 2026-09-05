import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import_wechat_statement = importlib.import_module("import_wechat_statement")
build_data = importlib.import_module("build_data")


HEADER = ("交易时间", "交易类型", "交易对方", "商品", "收/支", "金额(元)", "支付方式",
          "当前状态", "交易单号", "商户单号", "备注")


# Synthetic rows only: no real merchants, cards or transaction numbers.
def wechat_row(txn, time="2026-05-10 13:22:22", counterparty="Sample Restaurant", product="Dinner",
               direction="支出", amount=100.0, method="MASTERCARD(0000)", status="支付成功", remark="/"):
    return [time, "商户消费", counterparty, product, direction, amount, method, status, txn, "M-" + txn, remark]


def make_workbook(path, rows):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["微信支付账单明细"])
    sheet.append(["微信昵称：[Someone]"])
    sheet.append([])
    sheet.append(list(HEADER))
    for row in rows:
        sheet.append(list(row))
    workbook.save(path)


class ImportTests(unittest.TestCase):
    def test_parses_payments_and_keeps_the_nickname_out(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "export.xlsx"
            make_workbook(path, [wechat_row("1001"), wechat_row("1000", time="2026-05-09 08:00:00", amount="18")])
            payments = import_wechat_statement.parse_workbook(path)
            self.assertEqual([p["id"] for p in payments], ["wx_1000", "wx_1001"])
            self.assertEqual(payments[1]["amountCny"], 100.0)
            self.assertEqual(payments[0]["amountCny"], 18.0)
            self.assertEqual(payments[1]["direction"], "expense")
            self.assertEqual(payments[1]["date"], "2026-05-10")
            self.assertEqual(payments[1]["remark"], "")
            self.assertNotIn("Someone", json.dumps(payments, ensure_ascii=False))

    def test_main_merges_overlapping_exports_by_transaction_number(self):
        with tempfile.TemporaryDirectory() as folder:
            first = Path(folder) / "a.xlsx"
            second = Path(folder) / "b.xlsx"
            output = Path(folder) / "manual" / "wechat_payments.json"
            make_workbook(first, [wechat_row("1001"), wechat_row("1002", time="2026-05-11 10:00:00")])
            make_workbook(second, [wechat_row("1002", time="2026-05-11 10:00:00", amount=101.0),
                                   wechat_row("1003", time="2026-05-12 10:00:00")])
            import_wechat_statement.main([str(first), "--output", str(output),
                                          "--instrument", "MASTERCARD(0000)=YouTrip"])
            import_wechat_statement.main([str(second), "--output", str(output)])
            data = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual([p["id"] for p in data["payments"]], ["wx_1001", "wx_1002", "wx_1003"])
            self.assertEqual(next(p for p in data["payments"] if p["id"] == "wx_1002")["amountCny"], 101.0)
            self.assertEqual(data["instruments"], {"MASTERCARD(0000)": "YouTrip"})
            self.assertEqual(data["source"]["files"], ["a.xlsx", "b.xlsx"])

    def test_missing_header_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "export.xlsx"
            workbook = Workbook()
            workbook.active.append(["nothing here"])
            workbook.save(path)
            with self.assertRaisesRegex(SystemExit, "header"):
                import_wechat_statement.parse_workbook(path)


def payment(txn, **overrides):
    base = {
        "id": "wx_" + txn, "time": "2026-05-10 13:22:22", "date": "2026-05-10", "type": "商户消费",
        "counterparty": "Sample Restaurant", "product": "Dinner", "direction": "expense",
        "amountCny": 100.0, "method": "MASTERCARD(0000)", "status": "支付成功",
        "transactionNo": txn, "merchantNo": "M-" + txn, "remark": "",
    }
    base.update(overrides)
    return base


def card_row(tx_id, date, amount, foreign, card="UOB ONE CARD"):
    return {"id": tx_id, "date": date, "amount": amount, "foreign": foreign, "card": card,
            "description": "SAMPLE MERCHANT"}


class BuildTests(unittest.TestCase):
    def test_wallet_payment_is_estimated_at_the_nearest_cny_rate(self):
        rows = [card_row("tx_a", "2026-04-01", 18.5, "CNY 100.00"), card_row("tx_b", "2025-01-01", 19.2, "CNY 100.00")]
        charges, evidence, stats = build_data.prepare_wechat_payments(
            {"instruments": {"MASTERCARD(0000)": "YouTrip"}, "payments": [payment("1001")]}, rows)
        self.assertEqual(len(charges), 1)
        self.assertEqual(charges[0]["paidBy"], "YouTrip")
        self.assertEqual(charges[0]["amount"], 18.5)
        self.assertEqual(charges[0]["rate"], 0.185)
        self.assertTrue(charges[0]["estimated"])
        self.assertEqual(charges[0]["foreign"], "CNY 100.00")
        self.assertEqual(charges[0]["category"], "Travel")
        self.assertEqual(charges[0]["displayName"], "Dinner")
        self.assertEqual(stats["wallet"], 1)
        self.assertEqual(evidence, {})

    def test_order_number_products_fall_back_to_the_merchant_name(self):
        charges, _, _ = build_data.prepare_wechat_payments(
            {"instruments": {"MASTERCARD(0000)": "YouTrip"}, "sgdPerCny": 0.19,
             "payments": [payment("1001", product="订单：864482001260513124607951127")]}, [])
        self.assertEqual(charges[0]["displayName"], "Sample Restaurant")
        self.assertEqual(charges[0]["amount"], 19.0)
        self.assertEqual(charges[0]["rateSource"], "manual sgdPerCny")

    def test_tracked_card_payment_becomes_evidence_for_the_statement_row(self):
        rows = [card_row("tx_a", "2026-05-11", 18.6, "CNY 100.00")]
        charges, evidence, stats = build_data.prepare_wechat_payments(
            {"instruments": {"VISA(1111)": "UOB ONE CARD"}, "payments": [payment("1001", method="VISA(1111)")]}, rows)
        self.assertEqual(charges, [])
        self.assertEqual(evidence["tx_a"]["counterparty"], "Sample Restaurant")
        self.assertEqual(stats["evidence"], 1)

    def test_unmapped_neutral_and_failed_payments_are_skipped(self):
        charges, evidence, stats = build_data.prepare_wechat_payments(
            {"instruments": {"MASTERCARD(0000)": "YouTrip"}, "sgdPerCny": 0.19, "payments": [
                payment("1001", method="VISA(9999)"),
                payment("1002", direction="neutral"),
                payment("1003", status="支付失败"),
                payment("1004"),
            ]}, [])
        self.assertEqual([c["id"] for c in charges], ["wx_1004"])
        self.assertEqual(stats["unmappedMethods"], ["VISA(9999)"])

    def test_no_rate_at_all_fails_closed(self):
        with self.assertRaisesRegex(SystemExit, "cannot be estimated"):
            build_data.prepare_wechat_payments(
                {"instruments": {"MASTERCARD(0000)": "YouTrip"}, "payments": [payment("1001")]}, [])

    def test_malformed_payments_fail_closed(self):
        with self.assertRaisesRegex(SystemExit, "wx_"):
            build_data.prepare_wechat_payments({"payments": [payment("1", id="bad")]}, [])
        with self.assertRaisesRegex(SystemExit, "date"):
            build_data.prepare_wechat_payments({"payments": [payment("1", date="10/05/2026")]}, [])
        with self.assertRaisesRegex(SystemExit, "amountCny"):
            build_data.prepare_wechat_payments({"payments": [payment("1", amountCny="100")]}, [])


if __name__ == "__main__":
    unittest.main()
